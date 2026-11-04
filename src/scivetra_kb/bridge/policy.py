"""Export-cell policy for the PBD input CSV (S01 v03 06 section 4): DOI cell, URL cells, display fields.

Everything here is a pure function. Values are only ever *omitted* (with a row flag), never rewritten into a different
identifier: the original claims stay in the manifest and the KB.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlsplit

# 04 section 4.7 unreserved bytes (kept unencoded in DOI URL form). PBD builds "https://doi.org/" + doi WITHOUT
# encoding, so only DOIs whose prefix and suffix consist of these ASCII characters are written.
_DOI_SAFE = re.compile(r"[A-Za-z0-9\-._~!$&'()*+;=:@]+")
# 05 section 2 secret-like query keys (same family as the RV-Stage11B redaction), plus RV's own REDACTED marker.
_SECRET_KEY = re.compile(r"key|token|sig|signature|expires|policy|auth|session|email|password|x-amz-|key-pair-id", re.I)
_URL_CHARS = re.compile(r"[\x21-\x7e]+")
# Host characters accepted for export: lower-case letters, digits, '-', '_' and '.' (urlsplit lower-cases the host).
# Anything else ('%' escapes and the like) may be decoded or re-interpreted by a browser, so it is refused.
_HOST_CHARS = re.compile(r"[a-z0-9._-]+")
# A label that a WHATWG URL parser (Chromium / WebView2) or an inet_aton-style parser reads as a number: decimal or
# octal digits, or 0x plus hex digits (an empty "0x" counts as zero).
_NUMERIC_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*")
# Single labels or suffixes naming local, internal or reserved namespaces (RFC 2606/6761/6762/8375 and common
# private-network conventions). Suppressing a candidate on these is cheap; exporting one to a browser is not.
_LOCAL_SUFFIXES = ("localhost", "local", "localdomain", "internal", "intranet", "lan", "home", "corp", "private",
                   "home.arpa", "arpa", "invalid", "test")
MAX_URL_CHARS = 2000
MAX_DISPLAY_CHARS = 500
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def doi_url_safe(doi: str) -> bool:
    if not isinstance(doi, str) or "/" not in doi:
        return False
    prefix, suffix = doi.split("/", 1)
    return bool(prefix.startswith("10.") and _DOI_SAFE.fullmatch(prefix) and _DOI_SAFE.fullmatch(suffix))


def select_doi(source_dois: list, kb_active: list[str], kb_conflicting: list[str]) -> tuple[str | None, list[str]]:
    """Return (doi cell or None, flags). ``source_dois``: the RV item's DOI list as exported; ``kb_active`` /
    ``kb_conflicting``: the KB paper's effective DOI assertions (comparison keys). Exactly one consistent, ACTIVE,
    URL-safe DOI is written; anything else leaves the cell empty."""
    flags = []
    if len(source_dois) == 0:
        return None, ["NO_DOI"]
    if len(source_dois) > 1 or len(set(kb_active)) > 1:
        return None, ["MULTIPLE_DOI_SUPPRESSED"]
    if kb_conflicting:
        return None, ["DOI_CONFLICTING_SUPPRESSED"]
    if not kb_active:
        return None, ["DOI_NOT_VALID_IN_KB"]
    source, active = source_dois[0], kb_active[0]
    # The RV value must be the same DOI as the KB assertion without any trimming (a trailing space is a different value).
    if not isinstance(source, str) or source.translate(_ASCII_LOWER) != active:
        return None, ["DOI_SOURCE_KB_MISMATCH"]
    if not doi_url_safe(active):
        return None, ["DOI_NOT_URL_SAFE_FOR_PBD"]
    return active, flags


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def numeric_host(host: str) -> bool:
    """True if a client could read ``host`` as an IPv4 address in any notation. Purely lexical: no DNS, no network.

    Rule (a conservative superset of the WHATWG "ends in a number" check and of inet_aton): after dropping one trailing
    dot, the host is numeric if its LAST label is a numeric label, or if ANY label is a hex label (``0x...``). This
    covers ``127.1``, ``0177.0.0.1``, ``0x7f.0.0.1``, ``0x7f000001``, ``2130706433``, ``192.168.1``, ``10.1``,
    ``127.0.0.01`` and mixed forms. No real DNS name ends in an all-digit label (there is no numeric TLD), so the only
    cost is suppressing a rare ``0x``-labelled DNS name, which then is simply not exported."""
    labels = (host[:-1] if host.endswith(".") else host).split(".")
    if _NUMERIC_LABEL.fullmatch(labels[-1]):
        return True
    return any(label.startswith("0x") and _NUMERIC_LABEL.fullmatch(label) for label in labels)


def url_refusal(url) -> str | None:
    """None if ``url`` may be written to the PBD CSV, else a stable refusal code."""
    if not isinstance(url, str) or not url:
        return "URL_EMPTY"
    if len(url) > MAX_URL_CHARS or not _URL_CHARS.fullmatch(url) or "\\" in url:
        return "URL_UNSAFE_CHARACTERS"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "URL_MALFORMED"
    if parts.scheme not in ("http", "https"):
        return "URL_SCHEME_REFUSED"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "URL_USERINFO_REFUSED"
    host = parts.hostname or ""
    if "[" in parts.netloc or "]" in parts.netloc:
        return "URL_IP_LITERAL_REFUSED"
    if not host or port is not None or parts.netloc.lower() != host:
        return "URL_AUTHORITY_REFUSED"
    try:
        ipaddress.ip_address(host)
        return "URL_IP_LITERAL_REFUSED"
    except ValueError:
        pass
    if not _HOST_CHARS.fullmatch(host):
        return "URL_HOST_CHARACTERS_REFUSED"
    if numeric_host(host):
        return "URL_NUMERIC_HOST_REFUSED"
    if "." not in host or host.endswith(".") or any(host == s or host.endswith("." + s) for s in _LOCAL_SUFFIXES):
        return "URL_LOCAL_HOST_REFUSED"
    if parts.fragment:
        return "URL_FRAGMENT_REFUSED"
    if "REDACTED" in url:
        return "SIGNED_URL_OMITTED"
    for key, _ in parse_qsl(parts.query, keep_blank_values=True):
        if _SECRET_KEY.search(key):
            return "SIGNED_URL_OMITTED"
    return None


def display(value, *, flags: list[str], name: str) -> str:
    """Display-only cell: newlines/controls -> space, formula-leading characters escaped, bounded length."""
    if value is None:
        return ""
    text = str(value)
    cleaned = "".join(" " if (ord(c) < 32 or ord(c) == 127 or c in "  \u0085") else c for c in text)
    if cleaned != text:
        flags.append(f"{name.upper()}_CONTROL_CHARS_REPLACED")
    if len(cleaned) > MAX_DISPLAY_CHARS:
        cleaned = cleaned[:MAX_DISPLAY_CHARS]
        flags.append(f"{name.upper()}_TRUNCATED")
    if cleaned.lstrip(" ").startswith(_FORMULA_START):
        cleaned = "'" + cleaned
        flags.append(f"{name.upper()}_FORMULA_ESCAPED")
    return cleaned


__all__ = ["select_doi", "doi_url_safe", "numeric_host", "url_refusal", "display"]
