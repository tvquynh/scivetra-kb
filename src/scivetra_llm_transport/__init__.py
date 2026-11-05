"""scivetra_llm_transport: the loopback-only HTTP transport for OpenAI-compatible local model servers (ADR_LLM stage L2, LL-D13):
Ollama, LM Studio, llama.cpp server or vLLM running on THIS computer.

A separate, optional component: the ``scivetra_kb`` package itself stays free of HTTP client code (S02 invariant STATIC-01); this
module is the only network code shipped with it (allowlisted in release gate G5) and is imported only by ``rkb llm ping`` and
``rkb llm extract-evidence``. It refuses every host that is not a loopback literal
(``localhost`` is rewritten to 127.0.0.1, so no DNS lookup happens), ignores proxy settings, refuses redirects, caps the answer
size and maps HTTP results to router outcomes. Remote providers (https) are stage L3 and are refused here.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from scivetra_kb.llm.router import TransportResult

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirects are refused", headers, fp)


def loopback_url(base_url: str) -> str | None:
    """The base URL with a loopback IP literal as host, or None when the host is not loopback (or not plain http)."""
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "http" or parts.username or parts.password:
        return None
    if host == "localhost":
        host = "127.0.0.1"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not ip.is_loopback:
        return None
    netloc = (f"[{host}]" if ip.version == 6 else host) + (f":{parts.port}" if parts.port else "")
    return urlunsplit(("http", netloc, parts.path.rstrip("/"), "", ""))


class LoopbackTransport:
    """``complete()`` for providers whose ``base_url`` is a loopback http URL; anything else is NETWORK_ERROR (REMOTE_NOT_ENABLED)."""

    def __init__(self, *, temperature: float = 0.0, seed: int = 0, max_tokens: int = 4096):
        self.temperature, self.seed, self.max_tokens = temperature, seed, max_tokens
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def complete(self, provider: dict, messages: list, *, api_key: str | None, timeout: int, json_mode: bool) -> TransportResult:
        base = loopback_url(provider["base_url"])
        if base is None:
            return TransportResult("NETWORK_ERROR", detail="REMOTE_NOT_ENABLED_STAGE_L2")
        body = {"model": provider["model"], "messages": messages, "temperature": self.temperature, "seed": self.seed, "max_tokens": self.max_tokens,
                "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(retry) if retry is not None else None
                except ValueError:
                    wait = None
                return TransportResult("RATE_LIMITED", retry_after=wait, detail="HTTP_429")
            if exc.code in (401, 403):
                return TransportResult("AUTH_ERROR", detail=f"HTTP_{exc.code}")
            if exc.code >= 500:
                return TransportResult("SERVER_ERROR", detail=f"HTTP_{exc.code}")
            return TransportResult("INVALID_RESPONSE", detail=f"HTTP_{exc.code}")
        except TimeoutError:
            return TransportResult("TIMEOUT")
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            return TransportResult("NETWORK_ERROR", detail=type(getattr(exc, "reason", exc)).__name__)
        if len(raw) > MAX_RESPONSE_BYTES:
            return TransportResult("INVALID_RESPONSE", detail="RESPONSE_TOO_LARGE")
        try:
            data = json.loads(raw.decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            return TransportResult("INVALID_RESPONSE", detail="NOT_AN_OPENAI_CHAT_COMPLETION")
        if not isinstance(text, str):
            return TransportResult("INVALID_RESPONSE", detail="CONTENT_NOT_TEXT")
        return TransportResult("OK", text=text)
