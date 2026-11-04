"""Crossref REST ``/works/{doi}`` request building and claim decomposition (S01 04 sections 4-5).

Field shapes follow the S01 documentation notes (checked 2026-11-01). Shapes not
confirmed there are UNVERIFIED and parsed defensively (e.g. ``issn-type`` type
vocabularies, ``record-id`` string/int, organisation authors with ``name``).
"""
from __future__ import annotations

from urllib.parse import quote

from .doi import STRUCTURED_PROVIDER_FIELD, DOI_IDENTIFIER_FIELD, VALID, parse_doi, percent_encoded_name
from .strict_json import UnsafeJson, decode as strict_decode
from .shape import MALFORMED_FIELD_TYPE, MalformedResponse, arr, check, date_object, each, obj, scalar

PARSER_NAME, PARSER_VERSION = "crossref-work", "1"
BASE = "https://api.crossref.org/works/"

UPDATE_TYPE_MAP = {
    "retraction": "RETRACTION", "partial_retraction": "RETRACTION",
    "withdrawal": "WITHDRAWAL", "removal": "REMOVAL",
    "expression_of_concern": "EXPRESSION_OF_CONCERN",
    "correction": "CORRECTION", "corrigendum": "CORRECTION", "erratum": "CORRECTION",
    "new_version": "NEW_VERSION", "new_edition": "NEW_VERSION",
    "addendum": "OTHER", "clarification": "OTHER",
}
EVENT_TYPES_COVERED = ["CORRECTION", "EXPRESSION_OF_CONCERN", "NEW_VERSION", "OTHER", "REMOVAL", "RETRACTION", "WITHDRAWAL"]
DATE_FIELDS = {"issued": "date_issued", "published-print": "date_published_print", "published-online": "date_published_online",
               "published": "date_published", "posted": "date_posted", "accepted": "date_accepted",
               "created": "date_created", "deposited": "date_deposited", "indexed": "date_indexed"}


STRING_LIST_FIELDS = ("title", "subtitle", "original-title", "container-title", "short-container-title", "ISSN")
STRING_FIELDS = ("publisher", "prefix", "type", "volume", "issue", "page", "language", "abstract")


def validate_message(message: dict) -> None:
    """Shape-check every field this parser reads (raises MalformedResponse with a field path)."""
    base = "/message"
    scalar(message, "DOI", base, required=True)
    for key in STRING_LIST_FIELDS:
        each(arr(message, key, base), str, f"{base}/{key}")
    for i, item in enumerate(each(arr(message, "issn-type", base), dict, f"{base}/issn-type")):
        scalar(item, "value", f"{base}/issn-type/{i}")
        scalar(item, "type", f"{base}/issn-type/{i}")
    for key in STRING_FIELDS:
        scalar(message, key, base)
    scalar(message, "member", base, (str, int))
    for key in DATE_FIELDS:
        date_object(message, key, base)
    for i, author in enumerate(each(arr(message, "author", base), dict, f"{base}/author")):
        here = f"{base}/author/{i}"
        for key in ("given", "family", "name", "ORCID", "sequence"):
            scalar(author, key, here)
        scalar(author, "authenticated-orcid", here, bool)
        for j, aff in enumerate(each(arr(author, "affiliation", here), (dict, str), f"{here}/affiliation")):
            if isinstance(aff, dict):
                scalar(aff, "name", f"{here}/affiliation/{j}")
    for i, lic in enumerate(each(arr(message, "license", base), dict, f"{base}/license")):
        here = f"{base}/license/{i}"
        scalar(lic, "URL", here)
        scalar(lic, "content-version", here)
        scalar(lic, "delay-in-days", here, int)
        date_object(lic, "start", here)
    relation = obj(message, "relation", base)
    for name in sorted(relation or {}):
        here = f"{base}/relation/{name}"
        for i, rel in enumerate(each(check(relation[name], list, here), dict, here)):
            for key in ("id-type", "id", "asserted-by"):
                scalar(rel, key, f"{here}/{i}")
    for key in ("updated-by", "update-to"):
        for i, entry in enumerate(each(arr(message, key, base), dict, f"{base}/{key}")):
            here = f"{base}/{key}/{i}"
            for field in ("DOI", "type", "source", "label"):
                scalar(entry, field, here)
            scalar(entry, "record-id", here, (str, int))
            date_object(entry, "updated", here)


def request_identity(doi: str, key: str) -> dict:
    # The runtime contact email is never part of the identity (S01 04 section 4/7).
    return {"provider": "CROSSREF", "endpoint": "/works/{doi}", "doi": doi, "doi_key": key, "params": {}}


def build_url(doi: str, contact_email: str | None = None) -> str:
    url = BASE + percent_encoded_name(doi)
    if contact_email:
        url += "?mailto=" + quote(contact_email, safe="")
    return url


def _claim(field, value, path, licence="FACT"):
    return {"field": field, "value": value, "path": path, "licence_class": licence}


def decompose(message: dict) -> list[dict]:
    claims = []
    for key, field in (("title", "title"), ("subtitle", "subtitle"), ("original-title", "original_title"),
                       ("container-title", "container_title"), ("short-container-title", "container_title")):
        for i, value in enumerate(message.get(key) or []):
            if isinstance(value, str):
                claims.append(_claim(field, value, f"/message/{key}/{i}"))
    for i, item in enumerate(message.get("issn-type") or []):
        if isinstance(item, dict):
            claims.append(_claim("issn", {"value": item.get("value"), "type": item.get("type")}, f"/message/issn-type/{i}"))
    if not message.get("issn-type"):
        for i, value in enumerate(message.get("ISSN") or []):
            claims.append(_claim("issn", {"value": value, "type": None}, f"/message/ISSN/{i}"))
    for key, field in (("publisher", "publisher"), ("member", "member"), ("prefix", "prefix"), ("type", "work_type"),
                       ("volume", "volume"), ("issue", "issue"), ("page", "pages"), ("language", "language")):
        if isinstance(message.get(key), str):
            claims.append(_claim(field, message[key], f"/message/{key}"))
    for key, field in DATE_FIELDS.items():
        value = message.get(key)
        if isinstance(value, dict):
            claims.append(_claim(field, {"date_parts": value.get("date-parts"), "date_time": value.get("date-time"),
                                         "source_field": key}, f"/message/{key}"))
    for i, author in enumerate(message.get("author") or []):
        if isinstance(author, dict):
            affiliations = [a.get("name") if isinstance(a, dict) else a for a in author.get("affiliation") or []]
            claims.append(_claim("author", {"position": i + 1, "given": author.get("given"), "family": author.get("family"),
                                            "literal": author.get("name"), "orcid": author.get("ORCID"),
                                            "authenticated_orcid": author.get("authenticated-orcid"),
                                            "sequence": author.get("sequence"), "affiliations": affiliations},
                                 f"/message/author/{i}"))
    for i, lic in enumerate(message.get("license") or []):
        if isinstance(lic, dict):
            claims.append(_claim("license", {"url": lic.get("URL"), "content_version": lic.get("content-version"),
                                             "start": (lic.get("start") or {}).get("date-parts"),
                                             "delay_in_days": lic.get("delay-in-days")}, f"/message/license/{i}"))
    relation = message.get("relation") or {}
    if isinstance(relation, dict):
        for name in sorted(relation):
            for i, rel in enumerate(relation[name] or []):
                if isinstance(rel, dict):
                    claims.append(_claim("relation", {"name": name, "id_type": rel.get("id-type"), "id": rel.get("id"),
                                                      "asserted_by": rel.get("asserted-by")}, f"/message/relation/{name}/{i}"))
    if isinstance(message.get("abstract"), str):
        claims.append(_claim("abstract", message["abstract"], "/message/abstract", "COPYRIGHTED_ABSTRACT"))
    return claims


def _update_events(entries, direction: str, requested_key: str, requested_doi: str) -> list[dict]:
    events = []
    for i, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            continue
        raw_type = entry.get("type")
        event_type = UPDATE_TYPE_MAP.get(raw_type, "OTHER")
        source = entry.get("source")
        reported_by = "CROSSREF_RETRACTION_WATCH" if source == "retraction-watch" else "CROSSREF_PUBLISHER_UPDATE"
        other = parse_doi(entry.get("DOI") or "", DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
        other_key = other.comparison_key if other.status == VALID else None
        if direction == "updated-by":
            subject, notice = requested_key, other_key
        else:  # update-to: this record is the notice; the subject is the referenced work
            if other_key is None:
                continue
            subject, notice = other_key, requested_key
        record_id = entry.get("record-id")
        events.append({
            "subject_namespace": "doi", "subject_value": subject, "event_type": event_type,
            "event_subtype": raw_type, "notice_doi": notice, "reported_by": reported_by,
            "record_id": None if record_id is None else str(record_id),
            "event_date": (entry.get("updated") or {}).get("date-parts"),
            "source_field_path": f"/message/{direction}/{i}",
            "payload": {"label": entry.get("label"), "source": source, "source_missing": source is None},
        })
    return events


def parse_response(body: bytes, requested_doi: str, requested_key: str) -> dict:
    """Return {"status": FOUND|REFUSED, "reason", "claims", "events", "rw_seen"}."""
    try:
        envelope = strict_decode(body)
    except UnsafeJson as exc:
        return {"status": "REFUSED", "reason": exc.reason, "field_path": exc.field_path, "claims": [], "events": []}
    if not isinstance(envelope, dict) or envelope.get("status") != "ok" or envelope.get("message-type") != "work" \
            or not isinstance(envelope.get("message"), dict):
        return {"status": "REFUSED", "reason": "UNEXPECTED_ENVELOPE", "claims": [], "events": []}
    message = envelope["message"]
    try:
        validate_message(message)
    except MalformedResponse as exc:
        return {"status": "REFUSED", "reason": MALFORMED_FIELD_TYPE, "field_path": exc.field_path, "claims": [], "events": []}
    returned = parse_doi(message["DOI"], DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
    if returned.status != VALID or returned.comparison_key != requested_key:
        return {"status": "REFUSED", "reason": "RESPONSE_SUBJECT_MISMATCH", "claims": [], "events": []}
    events = _update_events(message.get("updated-by"), "updated-by", requested_key, requested_doi) + \
        _update_events(message.get("update-to"), "update-to", requested_key, requested_doi)
    rw_seen = any(e["reported_by"] == "CROSSREF_RETRACTION_WATCH" and e["subject_value"] == requested_key for e in events)
    return {"status": "FOUND", "reason": None, "claims": decompose(message), "events": events, "rw_seen": rw_seen,
            "deposited": (message.get("deposited") or {}).get("date-time")}
