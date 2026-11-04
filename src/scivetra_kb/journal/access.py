"""Polite, credential-safe provider access for the journal harvester.

* Credentials come only from the environment (``SCIVETRA_OPENALEX_API_KEY``) and are wrapped in ``Secret``, whose
  ``repr``/``str`` never show the value. A request carries the Secret object, never the key text in its URL, identity,
  headers or any persisted row; only a future, separately authorized live transport may reveal it at send time.
* The Crossref polite-pool contact (``SCIVETRA_CROSSREF_MAILTO``) is handled the same way: only whether one is configured
  is recorded. Request identities are refused by the KB if they contain ``@``, ``mailto`` or ``api_key``.
* Hosts are limited to ``api.crossref.org`` and ``api.openalex.org`` through the accepted S02 ``GuardedTransport``.
* There is no live transport in S04J: ``LiveTransportNotAuthorized`` (S02) still refuses construction.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .. import __version__
from ..metadata.transport import GuardedTransport, Request

JOURNAL_HOSTS = frozenset({"api.crossref.org", "api.openalex.org"})
OPENALEX_KEY_ENV = "SCIVETRA_OPENALEX_API_KEY"
CROSSREF_CONTACT_ENV = "SCIVETRA_CROSSREF_MAILTO"
USER_AGENT = f"SciVetra-KB/{__version__} (journal-harvest; S04J offline candidate)"
MAX_PAGE_BYTES = 32 * 1024 * 1024


class Secret:
    """Opaque holder: the value is reachable only through ``reveal()``."""
    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str) or not value:
            raise ValueError("empty secret")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("Secret values are not serializable")


@dataclass
class Credentials:
    openalex_api_key: Secret | None = None
    crossref_contact: Secret | None = None

    @classmethod
    def from_env(cls, env=None) -> "Credentials":
        env = os.environ if env is None else env
        key, contact = env.get(OPENALEX_KEY_ENV) or None, env.get(CROSSREF_CONTACT_ENV) or None
        return cls(Secret(key) if key else None, Secret(contact) if contact else None)

    def summary(self) -> dict:
        """What may be recorded: presence only."""
        return {"openalex_api_key_configured": self.openalex_api_key is not None,
                "crossref_contact_configured": self.crossref_contact is not None}


@dataclass
class JournalRequest(Request):
    credential: Secret | None = None


def make_request(url: str, provider: str, purpose: str, *, credential: Secret | None = None) -> JournalRequest:
    return JournalRequest("GET", url, provider, purpose, {"User-Agent": USER_AGENT, "Accept": "application/json"}, credential)


def guarded(inner) -> GuardedTransport:
    return GuardedTransport(inner, max_response_bytes=MAX_PAGE_BYTES, allowed_hosts=JOURNAL_HOSTS)


__all__ = ["Secret", "Credentials", "JournalRequest", "make_request", "guarded", "JOURNAL_HOSTS", "OPENALEX_KEY_ENV",
           "CROSSREF_CONTACT_ENV", "USER_AGENT"]
