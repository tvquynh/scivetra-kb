"""DOI Registration Agency lookup via ``https://doi.org/doiRA/<doi>[,<doi>...]`` (S01 04 section 3).

Only VALID identifier-field DOIs are routed; AMBIGUOUS/INVALID values are refused
before any request. Exact RA strings other than DataCite/Public/EIDR are
UNVERIFIED; matching is case-insensitive and unknown strings become OTHER.
Unknown/unsupported RAs are never sent to Crossref.
"""
from __future__ import annotations

from .doi import percent_encoded_name
from .strict_json import UnsafeJson, decode as strict_decode

PARSER_NAME, PARSER_VERSION = "doi-ra", "1"
BASE = "https://doi.org/doiRA/"
MAX_BATCH = 20
TTL_FOUND_DAYS = 365
TTL_NEGATIVE_DAYS = 30
STATUS_MAP = {"doi does not exist": "NOT_FOUND", "invalid doi": "INVALID_DOI", "unknown": "UNKNOWN"}


def build_url(dois: list[str]) -> str:
    if not 1 <= len(dois) <= MAX_BATCH:
        raise ValueError("doiRA batch must contain 1..20 DOIs")
    # percent_encoded_name encodes ',' as %2C, so commas inside a DOI cannot split the list.
    return BASE + ",".join(percent_encoded_name(d) for d in dois)


def request_identity(keys: list[str]) -> dict:
    return {"provider": "DOI_RA", "endpoint": "/doiRA/{dois}", "doi_keys": keys, "params": {}}


def map_ra(raw: str) -> str:
    value = raw.strip().lower()
    if value == "crossref":
        return "CROSSREF"
    if value == "datacite":
        return "DATACITE"
    return "OTHER"


def parse_response(body: bytes, requested: list[tuple[str, str]]) -> dict:
    """``requested`` = [(doi, key)]. Returns {"status", "reason", "items": [{"key","ra","ra_raw","status_raw"}]}."""
    try:
        data = strict_decode(body)
    except UnsafeJson as exc:
        return {"status": "REFUSED", "reason": exc.reason, "field_path": exc.field_path, "items": []}
    if not isinstance(data, list) or len(data) != len(requested):
        return {"status": "REFUSED", "reason": "RESPONSE_SHAPE_MISMATCH", "items": []}
    items = []
    for (doi, key), entry in zip(requested, data):
        if not isinstance(entry, dict) or not isinstance(entry.get("DOI"), str):
            return {"status": "REFUSED", "reason": "RESPONSE_SHAPE_MISMATCH", "items": []}
        echoed = entry["DOI"].translate({c: c + 32 for c in range(0x41, 0x5B)})
        if echoed != key:
            return {"status": "REFUSED", "reason": "RESPONSE_SUBJECT_MISMATCH", "items": []}
        if isinstance(entry.get("RA"), str):
            items.append({"key": key, "ra": map_ra(entry["RA"]), "ra_raw": entry["RA"], "status_raw": None})
        else:
            status_raw = entry.get("status") if isinstance(entry.get("status"), str) else None
            items.append({"key": key, "ra": STATUS_MAP.get((status_raw or "").strip().lower(), "UNKNOWN"),
                          "ra_raw": None, "status_raw": status_raw})
    return {"status": "FOUND", "reason": None, "items": items}


def route_decision(ra: str, ra_raw: str | None, *, allow_cn_probe: bool) -> str:
    """Return CROSSREF | DATACITE | CONTENT_NEGOTIATION | UNROUTED | STOP (S01 04 section 3.2)."""
    if ra == "CROSSREF":
        return "CROSSREF"
    if ra == "DATACITE":
        return "DATACITE"
    if ra in ("NOT_FOUND", "INVALID_DOI"):
        return "STOP"
    if ra == "OTHER" and (ra_raw or "").strip().lower() == "medra":
        return "CONTENT_NEGOTIATION"
    if ra in ("OTHER", "UNKNOWN") and allow_cn_probe:
        return "CONTENT_NEGOTIATION"
    return "UNROUTED"
