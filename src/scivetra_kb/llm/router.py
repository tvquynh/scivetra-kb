"""Rights-aware rotation across LLM providers (ADR_LLM LL-D05..D09). Stage L1: the transport is injected; only the scripted
``FakeTransport`` exists, so nothing here opens a network connection.

For one request the router:
1. keeps the providers whose ``data_policy`` the data class allows (``config.ALLOWED_POLICIES``) and that are enabled;
2. skips providers without their key in the environment (KEY_NOT_SET), rejected keys (KEY_REJECTED), providers cooling down after
   a rate limit (RATE_LIMITED, honouring Retry-After), open circuit breakers (after ``breaker_threshold`` consecutive failures)
   and exhausted per-minute / per-day / token budgets;
3. tries the rest in priority order and moves on after a rate limit, server error, timeout, network error or a response that
   fails validation; ``EXHAUSTED`` (with the earliest time a provider frees up) when none answers.

Every attempt is logged as one JSON line (task, provider, model, data class, outcome, prompt / response SHA-256, latency): never
the prompt, the response or a key. Responses are untrusted data: the caller validates them (schema, anchors) and a human decides.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.canonical import canonical_json
from ..core.errors import ValidationError
from .config import ALLOWED_POLICIES

OUTCOMES = ("OK", "RATE_LIMITED", "SERVER_ERROR", "TIMEOUT", "NETWORK_ERROR", "AUTH_ERROR", "INVALID_RESPONSE")
RETRYABLE = ("RATE_LIMITED", "SERVER_ERROR", "TIMEOUT", "NETWORK_ERROR", "INVALID_RESPONSE")


@dataclass
class TransportResult:
    outcome: str                     # one of OUTCOMES (INVALID_RESPONSE is set by the router after validation)
    text: str | None = None
    retry_after: float | None = None
    detail: str | None = None


class FakeTransport:
    """Scripted, offline transport for tests and ``rkb llm simulate``: ``script[name]`` is a list of TransportResult consumed in
    order (the last one repeats). Records every call (provider name, message count) but never inspects keys."""

    def __init__(self, script: dict):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def complete(self, provider: dict, messages: list, *, api_key: str | None, timeout: int, json_mode: bool) -> TransportResult:
        self.calls.append({"provider": provider["name"], "messages": len(messages), "key_given": api_key is not None})
        queue = self.script.get(provider["name"]) or [TransportResult("NETWORK_ERROR", detail="no script")]
        return queue.pop(0) if len(queue) > 1 else queue[0]


@dataclass
class _State:
    minute: list = field(default_factory=list)       # monotonic times of calls in the last 60 s
    day: str = ""
    day_calls: int = 0
    day_tokens: int = 0
    cool_until: float = 0.0
    failures: int = 0
    breaker_until: float = 0.0
    key_rejected: bool = False


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_tokens(messages: list) -> int:
    return max(1, sum(len(m.get("content", "")) for m in messages) // 4)


class Router:
    def __init__(self, config: dict, transport, *, env=None, monotonic=time.monotonic, wall=time.time, log_path: str | None = None,
                 breaker_threshold: int = 3, breaker_seconds: float = 300.0, default_cooldown: float = 60.0):
        self.providers = [p for p in config["providers"] if p["enabled"]]
        self.transport = transport
        self.env = os.environ if env is None else env
        self.monotonic, self.wall = monotonic, wall
        self.log_path = Path(log_path) if log_path else None
        self.breaker_threshold, self.breaker_seconds, self.default_cooldown = breaker_threshold, breaker_seconds, default_cooldown
        self.state = {p["name"]: _State() for p in self.providers}
        self.log = []

    # -- selection ------------------------------------------------------------------------------------------------------------
    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self.wall()))

    def _skip_reason(self, p: dict, tokens: int) -> str | None:
        s, now = self.state[p["name"]], self.monotonic()
        if p["key_env"] and not self.env.get(p["key_env"]):
            return "KEY_NOT_SET"
        if s.key_rejected:
            return "KEY_REJECTED"
        if now < s.breaker_until:
            return "BREAKER_OPEN"
        if now < s.cool_until:
            return "RATE_LIMITED"
        if s.day != self._today():
            s.day, s.day_calls, s.day_tokens = self._today(), 0, 0
        s.minute = [t for t in s.minute if now - t < 60]
        lim = p["limits"]
        if "rpm" in lim and len(s.minute) >= lim["rpm"]:
            return "MINUTE_LIMIT_REACHED"
        if "rpd" in lim and s.day_calls >= lim["rpd"]:
            return "DAY_LIMIT_REACHED"
        if "tpm" in lim and tokens > lim["tpm"]:
            return "PROMPT_TOO_LARGE_FOR_TPM"
        return None

    def plan(self, data_class: str, *, tokens: int = 1) -> list:
        """Every provider in priority order with ELIGIBLE or the reason it would be skipped for this data class now."""
        if data_class not in ALLOWED_POLICIES:
            raise ValidationError(f"data class must be one of {tuple(ALLOWED_POLICIES)}", code="LLM_DATA_CLASS_INVALID")
        out = []
        for p in self.providers:
            if p["data_policy"] not in ALLOWED_POLICIES[data_class]:
                reason = "DATA_POLICY_NOT_ALLOWED_FOR_" + data_class
            else:
                reason = self._skip_reason(p, tokens)
            out.append({"provider": p["name"], "model": p["model"], "data_policy": p["data_policy"], "priority": p["priority"],
                        "status": "ELIGIBLE" if reason is None else "SKIP", "reason": reason})
        return out

    # -- calls ----------------------------------------------------------------------------------------------------------------
    def _record(self, entry: dict) -> None:
        self.log.append(entry)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _attempt(self, p: dict, task: str, messages: list, data_class: str, validate, tokens: int):
        s = self.state[p["name"]]
        started = self.monotonic()
        s.minute.append(started)
        s.day_calls += 1
        s.day_tokens += tokens
        key = self.env.get(p["key_env"]) if p["key_env"] else None
        try:
            res = self.transport.complete(p, messages, api_key=key, timeout=p["timeout_seconds"], json_mode=p["json_mode"])
        except Exception as exc:                                   # a transport bug or an unexpected error is a failed attempt
            res = TransportResult("NETWORK_ERROR", detail=type(exc).__name__)
        parsed = None
        if res.outcome == "OK":
            try:
                parsed = validate(res.text) if validate else res.text
            except (ValueError, TypeError, KeyError) as exc:
                res = TransportResult("INVALID_RESPONSE", text=res.text, detail=type(exc).__name__)
        if res.outcome == "OK":
            s.failures = 0
        elif res.outcome == "RATE_LIMITED":
            s.cool_until = self.monotonic() + (res.retry_after if res.retry_after is not None else self.default_cooldown)
        elif res.outcome == "AUTH_ERROR":
            s.key_rejected = True
        else:
            s.failures += 1
            if s.failures >= self.breaker_threshold:
                s.breaker_until = self.monotonic() + self.breaker_seconds
        entry = {"ts": round(self.wall(), 3), "task": task, "provider": p["name"], "model": p["model"], "data_class": data_class,
                 "outcome": res.outcome, "prompt_sha256": _sha(canonical_json(messages)),
                 "response_sha256": _sha(res.text) if res.text is not None else None, "latency_ms": int((self.monotonic() - started) * 1000)}
        self._record(entry)
        return res, parsed, entry

    def complete(self, task: str, messages: list, data_class: str, *, validate=None, exclude_families=()) -> dict:
        """One answer from the first eligible provider that succeeds; EXHAUSTED when none does. ``validate(text)`` returns the parsed
        value or raises ValueError (the answer then counts as INVALID_RESPONSE and the next provider is tried)."""
        if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) and m.get("role") in ("system", "user", "assistant")
                                                                     and isinstance(m.get("content"), str) for m in messages):
            raise ValidationError("messages: a list of {role, content}", code="LLM_MESSAGES_INVALID")
        tokens = estimate_tokens(messages)
        attempts = []
        for row in self.plan(data_class, tokens=tokens):
            p = next(x for x in self.providers if x["name"] == row["provider"])
            if p["family"] in exclude_families:
                continue
            if row["status"] != "ELIGIBLE":
                attempts.append({"provider": p["name"], "outcome": "SKIPPED", "reason": row["reason"]})
                continue
            res, parsed, entry = self._attempt(p, task, messages, data_class, validate, tokens)
            attempts.append({"provider": p["name"], "outcome": res.outcome})
            if res.outcome == "OK":
                return {"status": "OK", "provider": p["name"], "model": p["model"], "family": p["family"], "data_policy": p["data_policy"],
                        "text": res.text, "parsed": parsed, "response_sha256": entry["response_sha256"], "attempts": attempts,
                        "authority": "NON_AUTHORITATIVE_PROPOSAL"}
        waits = [max(0.0, s.cool_until - self.monotonic()) for s in self.state.values() if s.cool_until > self.monotonic()]
        return {"status": "EXHAUSTED", "attempts": attempts, "retry_after_seconds": round(min(waits), 1) if waits else None}

    def consensus(self, task: str, messages: list, data_class: str, *, validate=None, compare=None) -> dict:
        """Ask two providers of DIFFERENT families. AGREED when ``compare(a, b)`` (default: equal canonical JSON of the parsed
        values) holds; DISAGREED keeps both answers, without a vote; INSUFFICIENT_PROVIDERS when no second family answers."""
        compare = compare or (lambda a, b: canonical_json(a) == canonical_json(b))
        first = self.complete(task, messages, data_class, validate=validate)
        if first["status"] != "OK":
            return {"status": "INSUFFICIENT_PROVIDERS", "answers": [], "first": first}
        second = self.complete(task, messages, data_class, validate=validate, exclude_families=(first["family"],))
        if second["status"] != "OK":
            return {"status": "INSUFFICIENT_PROVIDERS", "answers": [first], "second": second}
        agreed = bool(compare(first["parsed"], second["parsed"]))
        return {"status": "AGREED" if agreed else "DISAGREED", "answers": [first, second], "authority": "NON_AUTHORITATIVE_PROPOSAL"}


def json_object(text: str) -> dict:
    """Default validator: the whole answer must be one JSON object (no prose around it, no repair)."""
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("answer is not a JSON object")
    return value
