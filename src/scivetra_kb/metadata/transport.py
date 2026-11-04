"""Injected HTTP transport, URL policy and bounded execution.

S02 ships **no live network transport**: live access is not authorized
(handoff section 4.2). ``LiveTransportNotAuthorized`` refuses construction, and
the CLI refuses ``--live`` with ``LIVE_ACCESS_NOT_AUTHORIZED``. Tests and
offline CLI runs use ``FixtureTransport``. ``GuardedTransport`` applies the
policy (S01 04 section 7, 09 section 6) to any inner transport.
"""
from __future__ import annotations

import ipaddress
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..core.canonical import sha256_bytes
from ..core.errors import PolicyRefusal, TransportError, ValidationError
from ..core.paths import refuse_unsafe_path_text, safe_child, safe_input_file

ALLOWED_HOSTS = frozenset({"doi.org", "api.crossref.org", "api.datacite.org", "data.crosscite.org"})
FORBIDDEN_REQUEST_HEADERS = frozenset({"cookie", "authorization", "proxy-authorization"})


@dataclass
class Request:
    method: str
    url: str
    provider: str
    purpose: str
    headers: dict = field(default_factory=dict)


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    final_url: str

    def header(self, name: str) -> str | None:
        return {k.lower(): v for k, v in self.headers.items()}.get(name.lower())


class Transport:
    def send(self, request: Request) -> Response:  # pragma: no cover - interface
        raise NotImplementedError


class LiveTransportNotAuthorized(Transport):
    def __init__(self, *args, **kwargs):
        raise PolicyRefusal("Live provider access is not authorized in S02 (OAG-3/owner approval required)",
                            code="LIVE_ACCESS_NOT_AUTHORIZED")


def validate_url(url: str, allowed_hosts=ALLOWED_HOSTS) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise PolicyRefusal("Only HTTPS URLs are allowed", code="URL_SCHEME_REFUSED")
    if parts.username or parts.password or "@" in parts.netloc:
        raise PolicyRefusal("User-info in URL refused", code="URL_USERINFO_REFUSED")
    if parts.port not in (None, 443):
        raise PolicyRefusal("Non-standard port refused", code="URL_PORT_REFUSED")
    host = (parts.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        raise PolicyRefusal("IP-literal hosts refused", code="URL_HOST_REFUSED")
    except ValueError:
        pass
    if host not in allowed_hosts:
        raise PolicyRefusal("Host not on the allowlist", code="URL_HOST_REFUSED")
    if parts.fragment:
        raise PolicyRefusal("Fragments refused", code="URL_FRAGMENT_REFUSED")


class GuardedTransport(Transport):
    """Policy wrapper: allowlisted HTTPS only, no credentials, bounded body, safe redirects."""

    def __init__(self, inner: Transport, *, max_response_bytes: int, allowed_hosts=ALLOWED_HOSTS):
        # ``allowed_hosts`` (S04J): the journal harvester passes its own allowlist; S02 callers keep the default.
        self.inner = inner
        self.max_response_bytes = max_response_bytes
        self.allowed_hosts = frozenset(allowed_hosts)

    def send(self, request: Request) -> Response:
        validate_url(request.url, self.allowed_hosts)
        if any(name.lower() in FORBIDDEN_REQUEST_HEADERS for name in request.headers):
            raise PolicyRefusal("Credential-bearing request headers refused", code="HEADER_REFUSED")
        response = self.inner.send(request)
        if response.final_url != request.url:
            validate_url(response.final_url, self.allowed_hosts)  # redirect target must also be allowlisted HTTPS
        if len(response.body) > self.max_response_bytes:
            raise TransportError("Response exceeds byte cap", code="RESPONSE_TOO_LARGE")
        encoding = (response.header("content-encoding") or "identity").lower()
        if encoding != "identity":
            raise TransportError("Encoded response refused", code="CONTENT_ENCODING_REFUSED")
        return response


class FixtureTransport(Transport):
    """Offline transport. ``routes`` maps URL -> list of responses served in order (last one repeats)."""

    def __init__(self, routes: dict[str, list[Response]] | None = None):
        self.routes = {url: list(responses) for url, responses in (routes or {}).items()}
        self.calls: list[Request] = []

    def add(self, url: str, *responses: Response) -> None:
        self.routes.setdefault(url, []).extend(responses)

    def send(self, request: Request) -> Response:
        self.calls.append(request)
        queue = self.routes.get(request.url)
        if not queue:
            raise TransportError("No fixture for URL", code="NO_FIXTURE")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    @classmethod
    def from_directory(cls, directory: str) -> "FixtureTransport":
        """Load ``transport.json``: [{"url": ..., "responses": [{"status":..,"headers":{},"body_file":..}]}]."""
        refuse_unsafe_path_text(directory)
        manifest = safe_input_file(str(Path(directory) / "transport.json"), max_bytes=4 * 1024 * 1024)
        root = manifest.parent
        routes: dict[str, list[Response]] = {}
        for entry in json.loads(manifest.read_text(encoding="utf-8")):
            responses = []
            for spec in entry["responses"]:
                body = b""
                if spec.get("body_file"):
                    body = safe_input_file(str(safe_child(root, spec["body_file"])), max_bytes=16 * 1024 * 1024).read_bytes()
                elif spec.get("body_text") is not None:
                    body = spec["body_text"].encode("utf-8")
                responses.append(Response(int(spec["status"]), dict(spec.get("headers", {})), body,
                                          spec.get("final_url", entry["url"])))
            routes[entry["url"]] = responses
        return cls(routes)


class BudgetExhausted(Exception):
    pass


class RunStopped(Exception):
    """A 403 stops the whole run (S01 04 section 7)."""


class RunBudget:
    """Append-only request journal with reservations committed before any send."""

    def __init__(self, kb, run_id: str, *, max_requests: int, max_bytes: int, max_response_bytes: int):
        self.kb, self.run_id = kb, run_id
        self.max_requests, self.max_bytes, self.max_response_bytes = max_requests, max_bytes, max_response_bytes
        self.requests = 0
        self.bytes_charged = 0

    def reserve(self, request: Request, attempt: int) -> str:
        if self.requests >= self.max_requests or self.bytes_charged + self.max_response_bytes > self.max_bytes:
            raise BudgetExhausted()
        rid = self.kb.ids.new("rq")
        with self.kb.transaction():
            self.kb.insert("request_reservation", dict(
                reservation_id=rid, run_id=self.run_id, provider=request.provider, purpose=request.purpose,
                url_identity_sha256=sha256_bytes(request.url.split("?")[0].encode("utf-8")), attempt=attempt,
                reserved_bytes=self.max_response_bytes, reserved_at_utc=self.kb.now()))
        self.requests += 1
        self.bytes_charged += self.max_response_bytes
        return rid

    def complete(self, reservation_id: str, *, http_status, received: int, outcome: str) -> None:
        with self.kb.transaction():
            self.kb.insert("request_completion", dict(reservation_id=reservation_id, http_status=http_status,
                                                      received_bytes=received, outcome=outcome, completed_at_utc=self.kb.now()))
        self.bytes_charged += received - self.max_response_bytes


@dataclass
class ExecutionResult:
    response: Response | None
    outcome: str            # HTTP_OK | HTTP_NOT_FOUND | HTTP_OTHER | RATE_LIMITED | TRANSIENT_ERROR | REFUSED | BUDGET_EXHAUSTED | PROVIDER_PAUSED
    reason: str | None
    attempts: int


class Executor:
    """Bounded retries: 429/5xx/transport errors, max 4 attempts, capped backoff, 3x429 pauses provider."""

    MAX_ATTEMPTS = 4

    def __init__(self, transport: Transport, budget: RunBudget, *, sleeper=time.sleep, jitter=lambda: random.uniform(0, 0.5)):
        self.transport, self.budget, self.sleeper, self.jitter = transport, budget, sleeper, jitter
        self.paused: set[str] = set()
        self.consecutive_429: dict[str, int] = {}
        self.sleeps: list[float] = []
        self.stopped = False

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        base = float(2 ** attempt)
        if retry_after:
            try:
                base = max(base, float(retry_after))
            except ValueError:
                pass
        return min(60.0, base + self.jitter())

    def execute(self, request: Request) -> ExecutionResult:
        if self.stopped:
            raise RunStopped()
        if request.provider in self.paused:
            return ExecutionResult(None, "PROVIDER_PAUSED", "PAUSED_AFTER_429", 0)
        last_reason = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                reservation = self.budget.reserve(request, attempt)
            except BudgetExhausted:
                return ExecutionResult(None, "BUDGET_EXHAUSTED", "BUDGET_EXHAUSTED", attempt - 1)
            try:
                response = self.transport.send(request)
            except PolicyRefusal as exc:
                self.budget.complete(reservation, http_status=None, received=0, outcome="REFUSED:" + exc.code)
                return ExecutionResult(None, "REFUSED", exc.code, attempt)
            except TransportError as exc:
                self.budget.complete(reservation, http_status=None, received=0, outcome="TRANSPORT:" + exc.code)
                if exc.code in ("RESPONSE_TOO_LARGE", "CONTENT_ENCODING_REFUSED", "NO_FIXTURE"):
                    return ExecutionResult(None, "REFUSED" if exc.code != "NO_FIXTURE" else "TRANSIENT_ERROR", exc.code, attempt)
                last_reason = exc.code
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep(self._delay(attempt, None))
                continue
            self.budget.complete(reservation, http_status=response.status, received=len(response.body), outcome="HTTP")
            if response.status == 429:
                count = self.consecutive_429.get(request.provider, 0) + 1
                self.consecutive_429[request.provider] = count
                if count >= 3:
                    self.paused.add(request.provider)
                    return ExecutionResult(response, "RATE_LIMITED", "PROVIDER_PAUSED_AFTER_3x429", attempt)
                last_reason = "HTTP_429"
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep(self._delay(attempt, response.header("retry-after")))
                continue
            self.consecutive_429[request.provider] = 0
            if response.status == 403:
                self.stopped = True
                return ExecutionResult(response, "HTTP_OTHER", "HTTP_403_RUN_STOPPED", attempt)
            if 500 <= response.status <= 599:
                last_reason = f"HTTP_{response.status}"
                if attempt < self.MAX_ATTEMPTS:
                    self._sleep(self._delay(attempt, None))
                continue
            if response.status == 404:
                return ExecutionResult(response, "HTTP_NOT_FOUND", None, attempt)
            if 200 <= response.status <= 299:
                return ExecutionResult(response, "HTTP_OK", None, attempt)
            return ExecutionResult(response, "HTTP_OTHER", f"HTTP_{response.status}", attempt)
        outcome = "RATE_LIMITED" if last_reason == "HTTP_429" else "TRANSIENT_ERROR"
        return ExecutionResult(None, outcome, last_reason, self.MAX_ATTEMPTS)

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.sleeper(seconds)
