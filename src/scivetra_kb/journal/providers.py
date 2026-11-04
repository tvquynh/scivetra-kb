"""Crossref and OpenAlex journal-scoped request building and response parsing (S04J, ADR-JH-001).

Endpoints (documentation checked 2026-11-01, see 04_API_AND_IDENTIFIER_RULES):

* Crossref ``GET /journals/{ISSN}`` (journal record, ISSN types), ``GET /journals/{ISSN}/works?rows=0`` (count probe) and
  ``GET /journals/{ISSN}/works?rows=N&cursor=C`` (deep paging; the traversal ends when a page returns fewer than ``rows``
  items). ``total-results`` is recorded only as a count observed at request time.
* OpenAlex ``GET /sources?filter=issn:{ISSN}`` (source resolution) and ``GET /works?filter=<venue filter>&per-page=N&cursor=C``
  where the venue filter is ``primary_location.source.id:S..`` (venue of publication) or ``locations.source.id:S..`` (any
  location, possibly an alternate copy). The traversal ends when ``meta.next_cursor`` is null or a page is empty.

Provider bodies are untrusted: they go through ``strict_json.decode`` and per-field shape checks before anything is used.
A malformed *item* never fails the page: it is kept as a MALFORMED observation (quarantined, counted, reviewable).
Item-level Crossref decomposition reuses the accepted S02 ``crossref.validate_message`` / ``decompose`` / update-event
parsing; JSON pointers are rebased onto the item's position in the page (``/message/items/<i>/...``).
Request identities never contain a contact address or a credential (the KB CHECK constraints refuse them).
"""
from __future__ import annotations

import re
from urllib.parse import quote

from ..metadata import crossref as s02_crossref
from ..metadata.doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi
from ..metadata.shape import MALFORMED_FIELD_TYPE, MalformedResponse, arr, check, each, obj, scalar
from ..metadata.strict_json import UnsafeJson, decode as strict_decode
from .issn import provider_issn

CROSSREF_BASE = "https://api.crossref.org"
OPENALEX_BASE = "https://api.openalex.org"
CROSSREF_ADAPTER = ("crossref-journal-works", "1")
OPENALEX_ADAPTER = ("openalex-source-works", "1")
CROSSREF_MAX_ROWS = 1000
OPENALEX_MAX_PER_PAGE = 100
_SOURCE_ID = re.compile(r"S[0-9]{1,20}")
_WORK_ID = re.compile(r"W[0-9]{1,20}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
VENUE_FILTERS = {"OPENALEX": "primary_location.source.id", "OPENALEX_ALTERNATE": "locations.source.id"}


class PageRefused(Exception):
    def __init__(self, reason: str, field_path: str | None = None):
        super().__init__(reason if field_path is None else f"{reason} at {field_path}")
        self.reason, self.field_path = reason, field_path


def _q(value: str) -> str:
    return quote(value, safe="")


def check_since(since: str | None) -> str | None:
    if since is not None and not _DATE.fullmatch(since):
        raise ValueError("since must be YYYY-MM-DD")
    return since


# -- request builders ---------------------------------------------------------------------------------------------------
def crossref_journal_request(issn: str) -> tuple[str, dict]:
    return (f"{CROSSREF_BASE}/journals/{issn}",
            {"provider": "CROSSREF", "endpoint": "/journals/{issn}", "issn": issn, "params": {}})


def crossref_count_request(issn: str, *, since: str | None = None) -> tuple[str, dict]:
    params = {"rows": "0"}
    if since:
        params["filter"] = f"from-update-date:{since}"
    url = f"{CROSSREF_BASE}/journals/{issn}/works?" + "&".join(f"{k}={_q(v)}" for k, v in params.items())
    return url, {"provider": "CROSSREF", "endpoint": "/journals/{issn}/works", "issn": issn, "params": params}


def crossref_works_request(issn: str, *, rows: int, cursor: str, since: str | None = None) -> tuple[str, dict]:
    if not 1 <= rows <= CROSSREF_MAX_ROWS:
        raise ValueError("Crossref rows must be 1..1000")
    params = {"rows": str(rows), "cursor": cursor}
    if since:
        params["filter"] = f"from-update-date:{since}"
    url = f"{CROSSREF_BASE}/journals/{issn}/works?" + "&".join(f"{k}={_q(v)}" for k, v in params.items())
    return url, {"provider": "CROSSREF", "endpoint": "/journals/{issn}/works", "issn": issn, "params": params}


def openalex_sources_request(issn: str) -> tuple[str, dict]:
    params = {"filter": f"issn:{issn}"}
    return (f"{OPENALEX_BASE}/sources?filter={_q(params['filter'])}",
            {"provider": "OPENALEX", "endpoint": "/sources", "params": params})


def openalex_works_request(source_id: str, *, provider: str, per_page: int, cursor: str, since: str | None = None) -> tuple[str, dict]:
    if not _SOURCE_ID.fullmatch(source_id):
        raise ValueError("OpenAlex source id must look like S<digits>")
    if not 1 <= per_page <= OPENALEX_MAX_PER_PAGE:
        raise ValueError("OpenAlex per-page must be 1..100")
    filters = [f"{VENUE_FILTERS[provider]}:{source_id}"]
    if since:
        filters.append(f"from_updated_date:{since}")
    params = {"filter": ",".join(filters), "per-page": str(per_page), "cursor": cursor}
    url = f"{OPENALEX_BASE}/works?" + "&".join(f"{k}={_q(v)}" for k, v in params.items())
    return url, {"provider": "OPENALEX", "endpoint": "/works", "params": params}


def short_openalex_id(value, pattern=_SOURCE_ID) -> str | None:
    if not isinstance(value, str):
        return None
    tail = value.rsplit("/", 1)[-1]
    return tail if pattern.fullmatch(tail) else None


# -- envelopes ----------------------------------------------------------------------------------------------------------
def _decode(body: bytes):
    try:
        return strict_decode(body)
    except UnsafeJson as exc:
        raise PageRefused(exc.reason, exc.field_path) from None


def parse_crossref_journal(body: bytes) -> dict:
    data = _decode(body)
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("message-type") != "journal" or not isinstance(data.get("message"), dict):
        raise PageRefused("UNEXPECTED_ENVELOPE")
    message = data["message"]
    try:
        scalar(message, "title", "/message")
        scalar(message, "publisher", "/message")
        each(arr(message, "ISSN", "/message"), str, "/message/ISSN")
        for i, item in enumerate(each(arr(message, "issn-type", "/message"), dict, "/message/issn-type")):
            scalar(item, "value", f"/message/issn-type/{i}")
            scalar(item, "type", f"/message/issn-type/{i}")
    except MalformedResponse as exc:
        raise PageRefused(MALFORMED_FIELD_TYPE, exc.field_path) from None
    typed = []
    for i, item in enumerate(message.get("issn-type") or []):
        issn = provider_issn(item.get("value"))
        if issn:
            typed.append({"issn": issn, "type": item.get("type"), "path": f"/message/issn-type/{i}"})
    plain = [{"issn": provider_issn(v), "path": f"/message/ISSN/{i}"} for i, v in enumerate(message.get("ISSN") or []) if provider_issn(v)]
    return {"title": message.get("title"), "publisher": message.get("publisher"), "typed_issns": typed, "issns": plain}


def parse_crossref_list(body: bytes) -> dict:
    """Work-list envelope. Returns total_results, next_cursor, items (raw dicts), items_per_page."""
    data = _decode(body)
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("message-type") != "work-list" or not isinstance(data.get("message"), dict):
        raise PageRefused("UNEXPECTED_ENVELOPE")
    message = data["message"]
    try:
        total = check(message.get("total-results"), int, "/message/total-results", required=True)
        items = check(message.get("items"), list, "/message/items", required=True)
        cursor = scalar(message, "next-cursor", "/message")
    except MalformedResponse as exc:
        raise PageRefused(MALFORMED_FIELD_TYPE, exc.field_path) from None
    if total < 0:
        raise PageRefused(MALFORMED_FIELD_TYPE, "/message/total-results")
    return {"total_results": total, "next_cursor": cursor, "items": items}


def parse_openalex_list(body: bytes) -> dict:
    data = _decode(body)
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict) or not isinstance(data.get("results"), list):
        raise PageRefused("UNEXPECTED_ENVELOPE")
    meta = data["meta"]
    try:
        count = check(meta.get("count"), int, "/meta/count")
        cursor = scalar(meta, "next_cursor", "/meta")
    except MalformedResponse as exc:
        raise PageRefused(MALFORMED_FIELD_TYPE, exc.field_path) from None
    return {"total_results": count, "next_cursor": cursor, "items": data["results"]}


def parse_openalex_sources(body: bytes) -> list[dict]:
    listing = parse_openalex_list(body)
    sources = []
    for i, item in enumerate(listing["items"]):
        here = f"/results/{i}"
        try:
            check(item, dict, here, required=True)
            scalar(item, "id", here, required=True)
            scalar(item, "display_name", here)
            scalar(item, "issn_l", here)
            scalar(item, "type", here)
            each(arr(item, "issn", here), str, f"{here}/issn")
        except MalformedResponse as exc:
            raise PageRefused(MALFORMED_FIELD_TYPE, exc.field_path) from None
        sources.append({"source_id": short_openalex_id(item["id"]), "id_raw": item["id"], "display_name": item.get("display_name"),
                        "issn_l": provider_issn(item.get("issn_l")), "issns": sorted({provider_issn(v) for v in item.get("issn") or [] if provider_issn(v)}),
                        "type": item.get("type"), "path": here})
    return sources


# -- items -> observation + claims ---------------------------------------------------------------------------------------
def _rebase(path: str, pointer: str) -> str:
    return pointer + path[len("/message"):] if path.startswith("/message") else pointer + path


def _doi(raw) -> tuple[str, str | None, dict | None]:
    """(status, key, parse_dict) for a provider DOI field; MISSING when absent/empty."""
    if raw is None or raw == "":
        return "MISSING", None, None
    if not isinstance(raw, str):
        return "INVALID", None, {"status": "INVALID", "reason": "NOT_A_STRING"}
    parsed = parse_doi(raw, DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
    key = parsed.comparison_key if parsed.status == VALID and parsed.automatic_action_allowed else None
    status = "VALID" if key else ("AMBIGUOUS" if parsed.status == "AMBIGUOUS" else ("INVALID" if parsed.status != VALID else "AMBIGUOUS"))
    return status, key, parsed.as_dict()


def crossref_item(item, pointer: str) -> dict:
    """One ``/message/items/<i>`` record -> observation fields + claims (S02 decomposition, pointers rebased)."""
    out = {"provider_record_id": None, "doi_original": None, "doi_status": "MISSING", "doi_key": None, "doi_parse": None,
           "shape_status": "OK", "shape_reason": None, "work_type_raw": None, "claims": []}
    if not isinstance(item, dict):
        out.update(shape_status="MALFORMED", shape_reason=f"{MALFORMED_FIELD_TYPE}@{pointer}")
        return out
    raw_doi = item.get("DOI")
    if isinstance(raw_doi, str):
        out["doi_original"] = raw_doi
        out["provider_record_id"] = raw_doi
    elif raw_doi is not None:
        out["doi_original"] = repr(type(raw_doi).__name__)
    try:
        s02_crossref.validate_message(item)
        scalar(item, "article-number", "/message")
        resource = obj(item, "resource", "/message")
        if resource is not None:
            primary = obj(resource, "primary", "/message/resource")
            if primary is not None:
                scalar(primary, "URL", "/message/resource/primary")
    except MalformedResponse as exc:
        out.update(shape_status="MALFORMED", shape_reason=f"{MALFORMED_FIELD_TYPE}@{_rebase(exc.field_path, pointer)}")
        if isinstance(raw_doi, str):
            out["doi_status"], out["doi_key"], out["doi_parse"] = _doi(raw_doi)
        return out
    out["doi_status"], out["doi_key"], out["doi_parse"] = _doi(raw_doi)
    out["work_type_raw"] = item.get("type")
    claims = []
    for claim in s02_crossref.decompose(item):
        if claim["field"] == "abstract":
            continue        # copyrighted text is not decomposed into journal claims (it stays only in the local raw page)
        claims.append({"field": claim["field"], "value": claim["value"], "path": _rebase(claim["path"], pointer)})
    if isinstance(item.get("article-number"), str):
        claims.append({"field": "article_number", "value": item["article-number"], "path": f"{pointer}/article-number"})
    url = ((item.get("resource") or {}).get("primary") or {}).get("URL")
    if isinstance(url, str):
        claims.append({"field": "landing_page", "value": url, "path": f"{pointer}/resource/primary/URL"})
    deposited = (item.get("deposited") or {}).get("date-time")
    if isinstance(deposited, str):
        claims.append({"field": "provider_updated_at", "value": deposited, "path": f"{pointer}/deposited/date-time"})
    key = out["doi_key"]
    events = s02_crossref._update_events(item.get("updated-by"), "updated-by", key, raw_doi) if key else []
    events += s02_crossref._update_events(item.get("update-to"), "update-to", key, raw_doi)
    for event in events:
        if event["subject_value"] is None:
            continue
        claims.append({"field": "integrity_signal", "path": _rebase(event["source_field_path"], pointer), "value": {
            "subject_doi_key": event["subject_value"], "event_type": event["event_type"], "event_subtype": event["event_subtype"],
            "notice_doi_key": event["notice_doi"], "reported_by": event["reported_by"], "record_id": event["record_id"],
            "event_date": event["event_date"], "label": event["payload"].get("label")}})
    out["claims"] = claims
    return out


def _validate_location(loc, here: str) -> None:
    check(loc, dict, here, required=True)
    source = obj(loc, "source", here)
    if source is not None:
        scalar(source, "id", f"{here}/source")
        scalar(source, "display_name", f"{here}/source")
        scalar(source, "issn_l", f"{here}/source")
        each(arr(source, "issn", f"{here}/source"), str, f"{here}/source/issn")
    for key in ("landing_page_url", "pdf_url", "license", "version"):
        scalar(loc, key, here)
    scalar(loc, "is_oa", here, bool)


def validate_openalex_work(item, here: str) -> None:
    check(item, dict, here, required=True)
    scalar(item, "id", here, required=True)
    for key in ("doi", "title", "display_name", "publication_date", "type", "updated_date", "created_date"):
        scalar(item, key, here)
    scalar(item, "publication_year", here, int)
    scalar(item, "is_retracted", here, bool)
    if obj(item, "primary_location", here) is not None:
        _validate_location(item["primary_location"], f"{here}/primary_location")
    if obj(item, "best_oa_location", here) is not None:
        _validate_location(item["best_oa_location"], f"{here}/best_oa_location")
    for i, loc in enumerate(each(arr(item, "locations", here), dict, f"{here}/locations")):
        _validate_location(loc, f"{here}/locations/{i}")
    oa = obj(item, "open_access", here)
    if oa is not None:
        scalar(oa, "is_oa", f"{here}/open_access", bool)
        scalar(oa, "oa_status", f"{here}/open_access")
        scalar(oa, "oa_url", f"{here}/open_access")
    for i, authorship in enumerate(each(arr(item, "authorships", here), dict, f"{here}/authorships")):
        author = obj(authorship, "author", f"{here}/authorships/{i}")
        if author is not None:
            scalar(author, "display_name", f"{here}/authorships/{i}/author")
            scalar(author, "orcid", f"{here}/authorships/{i}/author")
        scalar(authorship, "author_position", f"{here}/authorships/{i}")
    biblio = obj(item, "biblio", here)
    if biblio is not None:
        for key in ("volume", "issue", "first_page", "last_page"):
            scalar(biblio, key, f"{here}/biblio")


def _location_value(loc: dict, *, index, is_primary: bool) -> dict:
    source = loc.get("source") or {}
    return {"index": index, "is_primary": is_primary, "source_id": short_openalex_id(source.get("id")),
            "source_display_name": source.get("display_name"), "is_oa": loc.get("is_oa"),
            "landing_page_url": loc.get("landing_page_url"), "pdf_url": loc.get("pdf_url"),
            "license": loc.get("license"), "version": loc.get("version")}


def openalex_item(item, pointer: str) -> dict:
    out = {"provider_record_id": None, "doi_original": None, "doi_status": "MISSING", "doi_key": None, "doi_parse": None,
           "shape_status": "OK", "shape_reason": None, "work_type_raw": None, "claims": []}
    try:
        validate_openalex_work(item, pointer)
    except MalformedResponse as exc:
        out.update(shape_status="MALFORMED", shape_reason=f"{MALFORMED_FIELD_TYPE}@{exc.field_path}")
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            out["provider_record_id"] = short_openalex_id(item["id"], _WORK_ID) or item["id"][:200]
        if isinstance(item, dict) and isinstance(item.get("doi"), str):
            out["doi_original"] = item["doi"]
            out["doi_status"], out["doi_key"], out["doi_parse"] = _doi(item["doi"])
        return out
    out["provider_record_id"] = short_openalex_id(item["id"], _WORK_ID) or item["id"][:200]
    if isinstance(item.get("doi"), str) and item["doi"] != "":
        out["doi_original"] = item["doi"]
    out["doi_status"], out["doi_key"], out["doi_parse"] = _doi(item.get("doi"))
    out["work_type_raw"] = item.get("type")
    claims = []

    def add(field, value, path):
        if value is not None:
            claims.append({"field": field, "value": value, "path": path})

    title = item.get("title") if item.get("title") is not None else item.get("display_name")
    add("title", title, f"{pointer}/title" if item.get("title") is not None else f"{pointer}/display_name")
    add("work_type", item.get("type"), f"{pointer}/type")
    add("publication_year", item.get("publication_year"), f"{pointer}/publication_year")
    add("publication_date", item.get("publication_date"), f"{pointer}/publication_date")
    add("provider_updated_at", item.get("updated_date"), f"{pointer}/updated_date")
    add("provider_created_at", item.get("created_date"), f"{pointer}/created_date")
    primary = item.get("primary_location")
    if isinstance(primary, dict):
        value = _location_value(primary, index=None, is_primary=True)
        add("primary_source", {"source_id": value["source_id"], "display_name": value["source_display_name"],
                               "issn_l": provider_issn((primary.get("source") or {}).get("issn_l")),
                               "issns": sorted({provider_issn(v) for v in (primary.get("source") or {}).get("issn") or [] if provider_issn(v)})},
            f"{pointer}/primary_location/source")
        add("location", value, f"{pointer}/primary_location")
        add("landing_page", primary.get("landing_page_url"), f"{pointer}/primary_location/landing_page_url")
    for i, loc in enumerate(item.get("locations") or []):
        add("location", _location_value(loc, index=i, is_primary=False), f"{pointer}/locations/{i}")
    if isinstance(item.get("best_oa_location"), dict):
        add("best_oa_location", _location_value(item["best_oa_location"], index=None, is_primary=False), f"{pointer}/best_oa_location")
    if isinstance(item.get("open_access"), dict):
        oa = item["open_access"]
        add("open_access", {"is_oa": oa.get("is_oa"), "oa_status": oa.get("oa_status"), "oa_url": oa.get("oa_url")}, f"{pointer}/open_access")
    if item.get("is_retracted") is not None:
        add("is_retracted", item["is_retracted"], f"{pointer}/is_retracted")
        if item["is_retracted"] is True and out["doi_key"]:
            add("integrity_signal", {"subject_doi_key": out["doi_key"], "event_type": "RETRACTION", "event_subtype": "is_retracted",
                                     "notice_doi_key": None, "reported_by": "OPENALEX_IS_RETRACTED", "record_id": None,
                                     "event_date": None, "label": None}, f"{pointer}/is_retracted")
    for i, authorship in enumerate(item.get("authorships") or []):
        author = authorship.get("author") or {}
        add("author", {"position": i + 1, "literal": author.get("display_name"), "orcid": author.get("orcid"),
                       "author_position": authorship.get("author_position")}, f"{pointer}/authorships/{i}")
    biblio = item.get("biblio") or {}
    add("volume", biblio.get("volume"), f"{pointer}/biblio/volume")
    add("issue", biblio.get("issue"), f"{pointer}/biblio/issue")
    if biblio.get("first_page") is not None:
        pages = biblio["first_page"] + ("-" + biblio["last_page"] if biblio.get("last_page") not in (None, biblio["first_page"]) else "")
        add("pages", pages, f"{pointer}/biblio")
    out["claims"] = claims
    return out


__all__ = ["PageRefused", "crossref_journal_request", "crossref_count_request", "crossref_works_request", "openalex_sources_request",
           "openalex_works_request", "parse_crossref_journal", "parse_crossref_list", "parse_openalex_list", "parse_openalex_sources",
           "crossref_item", "openalex_item", "short_openalex_id", "check_since", "CROSSREF_ADAPTER", "OPENALEX_ADAPTER"]
