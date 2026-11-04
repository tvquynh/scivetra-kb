"""DataCite REST ``/dois/{doi}`` (JSON:API) request building and claim decomposition (S01 04 section 6).

``publisher`` and ``creators[].affiliation`` are parsed in both string and
object forms. DataCite has no Crossmark equivalent: retraction-type coverage is
``NOT_SUPPORTED_BY_PROVIDER``; versioning relations produce NEW_VERSION events.
Withdrawal/tombstone representation and 404 body shape are UNVERIFIED.
"""
from __future__ import annotations


from .doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi, percent_encoded_name
from .strict_json import UnsafeJson, decode as strict_decode
from .shape import MALFORMED_FIELD_TYPE, MalformedResponse, arr, check, each, obj, scalar

PARSER_NAME, PARSER_VERSION = "datacite-doi", "1"
BASE = "https://api.datacite.org/dois/"
NOTICE_TYPES = ["CORRECTION", "EXPRESSION_OF_CONCERN", "OTHER", "REMOVAL", "RETRACTION", "WITHDRAWAL"]
VERSION_TYPES = ["NEW_VERSION"]


def validate_document(document) -> tuple[dict, dict]:
    """Shape-check the JSON:API document and every attribute this parser reads. Returns (data, attributes)."""
    check(document, dict, "/", required=True)
    data = obj(document, "data", "", required=True)
    scalar(data, "type", "/data")
    scalar(data, "id", "/data")
    attributes = obj(data, "attributes", "/data", required=True)
    base = "/data/attributes"
    scalar(attributes, "doi", base)
    if attributes.get("doi") is None and data.get("id") is None:
        raise MalformedResponse(f"{base}/doi", "string")
    for i, title in enumerate(each(arr(attributes, "titles", base), dict, f"{base}/titles")):
        scalar(title, "title", f"{base}/titles/{i}")
    for i, creator in enumerate(each(arr(attributes, "creators", base), dict, f"{base}/creators")):
        here = f"{base}/creators/{i}"
        for key in ("name", "nameType", "givenName", "familyName"):
            scalar(creator, key, here)
        each(arr(creator, "nameIdentifiers", here), dict, f"{here}/nameIdentifiers")
        for j, aff in enumerate(each(arr(creator, "affiliation", here), (dict, str), f"{here}/affiliation")):
            if isinstance(aff, dict):
                scalar(aff, "name", f"{here}/affiliation/{j}")
    publisher = scalar(attributes, "publisher", base, (str, dict))
    if isinstance(publisher, dict):
        scalar(publisher, "name", f"{base}/publisher")
        scalar(publisher, "publisherIdentifier", f"{base}/publisher")
    scalar(attributes, "publicationYear", base, (int, str))
    for i, date in enumerate(each(arr(attributes, "dates", base), dict, f"{base}/dates")):
        scalar(date, "date", f"{base}/dates/{i}")
        scalar(date, "dateType", f"{base}/dates/{i}")
    types = obj(attributes, "types", base)
    if types is not None:
        scalar(types, "resourceTypeGeneral", f"{base}/types")
        scalar(types, "resourceType", f"{base}/types")
    for i, rel in enumerate(each(arr(attributes, "relatedIdentifiers", base), dict, f"{base}/relatedIdentifiers")):
        for key in ("relationType", "relatedIdentifier", "relatedIdentifierType"):
            scalar(rel, key, f"{base}/relatedIdentifiers/{i}")
    scalar(attributes, "version", base, (str, int, float))
    scalar(attributes, "state", base)
    scalar(attributes, "metadataVersion", base, int)
    for i, right in enumerate(each(arr(attributes, "rightsList", base), dict, f"{base}/rightsList")):
        for key in ("rights", "rightsIdentifier", "rightsIdentifierScheme"):
            scalar(right, key, f"{base}/rightsList/{i}")
    for key in ("created", "registered", "updated"):
        scalar(attributes, key, base)
    return data, attributes


def request_identity(doi: str, key: str) -> dict:
    return {"provider": "DATACITE", "endpoint": "/dois/{doi}", "doi": doi, "doi_key": key,
            "params": {"affiliation": "true", "publisher": "true"}}


def build_url(doi: str) -> str:
    return BASE + percent_encoded_name(doi) + "?affiliation=true&publisher=true"


def _claim(field, value, path):
    return {"field": field, "value": value, "path": path, "licence_class": "FACT"}


def decompose(attributes: dict) -> list[dict]:
    claims = []
    for i, title in enumerate(attributes.get("titles") or []):
        if isinstance(title, dict):
            claims.append(_claim("title", title.get("title"), f"/data/attributes/titles/{i}"))
    for i, creator in enumerate(attributes.get("creators") or []):
        if isinstance(creator, dict):
            affiliations = [a.get("name") if isinstance(a, dict) else a for a in creator.get("affiliation") or []]
            claims.append(_claim("author", {"position": i + 1, "name_type": creator.get("nameType"), "literal": creator.get("name"),
                                            "given": creator.get("givenName"), "family": creator.get("familyName"),
                                            "name_identifiers": creator.get("nameIdentifiers") or [],
                                            "affiliations": affiliations}, f"/data/attributes/creators/{i}"))
    publisher = attributes.get("publisher")
    if isinstance(publisher, str):
        claims.append(_claim("publisher", {"name": publisher, "form": "string"}, "/data/attributes/publisher"))
    elif isinstance(publisher, dict):
        claims.append(_claim("publisher", {"name": publisher.get("name"), "form": "object",
                                           "identifier": publisher.get("publisherIdentifier")}, "/data/attributes/publisher"))
    if attributes.get("publicationYear") is not None:
        claims.append(_claim("date_issued", {"date_parts": [[attributes["publicationYear"]]], "source_field": "publicationYear"},
                             "/data/attributes/publicationYear"))
    for i, date in enumerate(attributes.get("dates") or []):
        if isinstance(date, dict):
            claims.append(_claim("date_issued" if date.get("dateType") == "Issued" else "date_updated" if date.get("dateType") == "Updated" else "date_posted",
                                 {"date": date.get("date"), "date_type": date.get("dateType")}, f"/data/attributes/dates/{i}"))
    types = attributes.get("types") or {}
    if types.get("resourceTypeGeneral"):
        claims.append(_claim("resource_type_general", types["resourceTypeGeneral"], "/data/attributes/types/resourceTypeGeneral"))
    if types.get("resourceType"):
        claims.append(_claim("work_type", types["resourceType"], "/data/attributes/types/resourceType"))
    for i, rel in enumerate(attributes.get("relatedIdentifiers") or []):
        if isinstance(rel, dict):
            claims.append(_claim("relation", {"name": rel.get("relationType"), "id": rel.get("relatedIdentifier"),
                                              "id_type": rel.get("relatedIdentifierType")}, f"/data/attributes/relatedIdentifiers/{i}"))
    for key, field in (("version", "version"), ("state", "state"), ("metadataVersion", "metadata_version")):
        if attributes.get(key) is not None:
            claims.append(_claim(field, attributes[key], f"/data/attributes/{key}"))
    for i, right in enumerate(attributes.get("rightsList") or []):
        if isinstance(right, dict):
            claims.append(_claim("rights", {"rights": right.get("rights"), "identifier": right.get("rightsIdentifier"),
                                            "scheme": right.get("rightsIdentifierScheme"), "applies_to": "resource"},
                                 f"/data/attributes/rightsList/{i}"))
    for key, field in (("created", "date_created"), ("registered", "date_registered"), ("updated", "date_updated")):
        if attributes.get(key):
            claims.append(_claim(field, {"date_time": attributes[key], "source_field": key}, f"/data/attributes/{key}"))
    return claims


def version_events(attributes: dict, requested_key: str) -> list[dict]:
    events = []
    for i, rel in enumerate(attributes.get("relatedIdentifiers") or []):
        if not isinstance(rel, dict) or rel.get("relatedIdentifierType") != "DOI":
            continue
        other = parse_doi(rel.get("relatedIdentifier") or "", DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
        if other.status != VALID:
            continue
        name = rel.get("relationType")
        if name in ("IsPreviousVersionOf", "IsObsoletedBy"):      # this work has a newer version
            subject, notice = requested_key, other.comparison_key
        elif name in ("IsNewVersionOf", "Obsoletes"):             # the related work has a newer version (this one)
            subject, notice = other.comparison_key, requested_key
        else:
            continue
        events.append({"subject_namespace": "doi", "subject_value": subject, "event_type": "NEW_VERSION",
                       "event_subtype": name, "notice_doi": notice, "reported_by": "DATACITE_RELATION", "record_id": None,
                       "event_date": None, "source_field_path": f"/data/attributes/relatedIdentifiers/{i}",
                       "payload": {"relation_type": name}})
    return events


def parse_response(body: bytes, requested_doi: str, requested_key: str) -> dict:
    try:
        document = strict_decode(body)
    except UnsafeJson as exc:
        return {"status": "REFUSED", "reason": exc.reason, "field_path": exc.field_path, "claims": [], "events": []}
    try:
        data, attributes = validate_document(document)
    except MalformedResponse as exc:
        return {"status": "REFUSED", "reason": MALFORMED_FIELD_TYPE, "field_path": exc.field_path, "claims": [], "events": []}
    if data.get("type") != "dois":
        return {"status": "REFUSED", "reason": "UNEXPECTED_ENVELOPE", "claims": [], "events": []}
    returned = parse_doi(attributes.get("doi") or data.get("id"), DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
    if returned.status != VALID or returned.comparison_key != requested_key:
        return {"status": "REFUSED", "reason": "RESPONSE_SUBJECT_MISMATCH", "claims": [], "events": []}
    return {"status": "FOUND", "reason": None, "claims": decompose(attributes),
            "events": version_events(attributes, requested_key), "updated": attributes.get("updated"),
            "metadata_version": attributes.get("metadataVersion")}
