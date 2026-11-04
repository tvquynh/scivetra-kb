"""doi.org content negotiation (CSL JSON) fallback, policy-gated (S01 04 section 3.2).

A result is accepted only if the final response is 200 with Content-Type
``application/vnd.citationstyles.csl+json``. A 204, an HTML landing page or any
other type means NO_METADATA. Lower fidelity: no update/licence/state fields.
"""
from __future__ import annotations


from .doi import url_form
from .strict_json import UnsafeJson, decode as strict_decode
from .shape import MALFORMED_FIELD_TYPE, MalformedResponse, check, scalar

PARSER_NAME, PARSER_VERSION = "doi-cn-csl", "1"
ACCEPT = "application/vnd.citationstyles.csl+json"


def request_identity(doi: str, key: str) -> dict:
    return {"provider": "DOI_CONTENT_NEGOTIATION", "endpoint": "https://doi.org/{doi}", "doi": doi, "doi_key": key,
            "params": {"accept": ACCEPT}}


def build_url(doi: str) -> str:
    return url_form(doi)


def parse_response(status: int, content_type: str | None, body: bytes) -> dict:
    if status == 204:
        return {"status": "NO_METADATA", "reason": "HTTP_204_NO_METADATA", "claims": []}
    if status != 200 or not (content_type or "").lower().startswith(ACCEPT):
        return {"status": "NO_METADATA", "reason": "NOT_CSL_JSON", "claims": []}
    try:
        data = strict_decode(body)
    except UnsafeJson as exc:
        # S02 v03: unparseable or unsafe CSL is a refused snapshot (REFUSED_UNSAFE_RESPONSE); user status stays NO_METADATA.
        return {"status": "REFUSED", "reason": exc.reason, "field_path": exc.field_path, "claims": []}
    try:
        check(data, dict, "/", required=True)
        for key in ("title", "publisher", "type"):
            scalar(data, key, "")
    except MalformedResponse as exc:
        # Documented mapping (S02 v02): snapshot outcome REFUSED_UNSAFE_RESPONSE / MALFORMED_FIELD_TYPE;
        # the user-facing content-negotiation status is NO_METADATA.
        return {"status": "REFUSED", "reason": MALFORMED_FIELD_TYPE, "field_path": exc.field_path, "claims": []}
    claims = []
    if isinstance(data.get("title"), str):
        claims.append({"field": "title", "value": data["title"], "path": "/title", "licence_class": "FACT"})
    if isinstance(data.get("publisher"), str):
        claims.append({"field": "publisher", "value": data["publisher"], "path": "/publisher", "licence_class": "FACT"})
    if isinstance(data.get("type"), str):
        claims.append({"field": "work_type", "value": data["type"], "path": "/type", "licence_class": "FACT"})
    return {"status": "FOUND", "reason": None, "claims": claims}
