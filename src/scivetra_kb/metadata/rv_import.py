"""Read-only import of a Reprovane RV-Stage11 portable corpus export (13 section 1 item 5).

The KB never opens a Reprovane database. It reads the four portable files
(``SHA256SUMS.txt``, ``corpus.csv``, ``corpus.jsonl``, ``manifest.json`` with
schema ``corpus-portable/1``), checks their self-consistency (SHA256SUMS and the
manifest file inventory; this is not authentication, S01 06 section 5.1), and
creates minimal GlobalPapers, DOI/OpenAlex/context assertions, divergence
records and OpenAlex integrity evidence. It writes nothing to the export folder.
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, ValidationError
from ..core.paths import check_no_links_on_path, refuse_unsafe_path_text, safe_input_file
from .divergence import classify
from .doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi
from .integrity import IntegrityService
from .store import MetadataStore
from .strict_json import UnsafeJson, decode as strict_decode

FILES = ("SHA256SUMS.txt", "corpus.csv", "corpus.jsonl", "manifest.json")
PARSER_NAME, PARSER_VERSION = "reprovane-corpus-portable", "1"

# Untrusted-input caps for portable exports (S03-B item 4). Exceeding any cap refuses the whole export.
MAX_FILE = 64 * 1024 * 1024        # per file
MAX_ITEMS = 100_000                # corpus.jsonl lines / corpus.csv rows
MAX_LINE_BYTES = 4 * 1024 * 1024   # one JSONL item
MAX_STRING_CHARS = 65_536          # any JSON string or CSV cell
_SUMS_LINE = re.compile(r"([0-9a-f]{64})  (corpus\.jsonl|corpus\.csv|manifest\.json)")
_ID_TEXT = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class _DuplicateKey(ValueError):
    pass


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _strict(raw: bytes, where: str):
    """strict_json (S02 v03 boundary) plus duplicate-key and string-length refusal; content is never echoed."""
    try:
        value = strict_decode(raw)
    except UnsafeJson as exc:
        raise IntegrityError(f"Unsafe JSON in {where}: {exc.reason}" + (f" at {exc.field_path}" if exc.field_path else ""),
                             code="EXPORT_UNSAFE_JSON") from None
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except _DuplicateKey:
        raise IntegrityError(f"Duplicate JSON object key in {where}", code="EXPORT_DUPLICATE_KEY") from None
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str) and len(node) > MAX_STRING_CHARS:
            raise IntegrityError(f"String longer than {MAX_STRING_CHARS} characters in {where}", code="EXPORT_FIELD_TOO_LONG")
        if isinstance(node, dict):
            stack.extend(node.values())
            stack.extend(node.keys())
        elif isinstance(node, list):
            stack.extend(node)
    return value


def _shape(ok: bool, where: str, what: str) -> None:
    if not ok:
        raise IntegrityError(f"Export shape invalid in {where}: {what}", code="EXPORT_SHAPE_INVALID")


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_item_shape(item, where: str) -> None:
    """Minimum shape every KB consumer relies on; anything else is refused instead of guessed."""
    _shape(isinstance(item, dict), where, "item is not an object")
    _shape(isinstance(item.get("corpus_item_id"), str) and bool(_ID_TEXT.fullmatch(item["corpus_item_id"])), where, "corpus_item_id")
    _shape(isinstance(item.get("publication_entity_id"), str) and bool(_ID_TEXT.fullmatch(item["publication_entity_id"])), where,
           "publication_entity_id")
    _shape(isinstance(item.get("item_sha256"), str) and bool(_HEX64.fullmatch(item["item_sha256"])), where, "item_sha256")
    identity = item.get("identity")
    _shape(isinstance(identity, dict), where, "identity")
    _shape(_str_list(identity.get("normalized_dois", [])) and _str_list(identity.get("openalex_work_ids", [])), where, "identity lists")
    identifiers = identity.get("provider_identifiers", [])
    _shape(isinstance(identifiers, list) and all(
        isinstance(c, dict) and isinstance(c.get("namespace"), str) and isinstance(c.get("original", ""), str)
        and (c.get("value") is None or isinstance(c.get("value"), str)) for c in identifiers), where, "provider_identifiers")
    metadata = item.get("metadata", {})
    _shape(isinstance(metadata, dict) and isinstance(metadata.get("records", []), list) and all(
        isinstance(r, dict) and isinstance(r.get("fields", {}), dict) for r in metadata.get("records", [])), where, "metadata.records")


def _read_export(directory: str, *, unique_entities: bool = False) -> dict:
    """Read and self-check a portable export. Every check runs before the caller writes anything.

    ``unique_entities`` (library import) also refuses a repeated publication_entity_id, as Reprovane's own
    verify-export does; the S02 metadata import keeps accepting the S02 synthetic fixtures that reuse one entity id.
    """
    refuse_unsafe_path_text(directory)
    root = Path(directory)
    check_no_links_on_path(root)
    if not root.is_dir():
        raise ValidationError("Export directory not found", code="EXPORT_NOT_FOUND")
    entries = list(root.iterdir())
    if {p.name for p in entries} != set(FILES) or len(entries) != len(FILES):
        raise ValidationError("Export must contain exactly the four portable files", code="EXPORT_INVENTORY_MISMATCH")
    data = {name: safe_input_file(str(root / name), max_bytes=MAX_FILE).read_bytes() for name in FILES}
    listed = {}
    try:
        sums_text = data["SHA256SUMS.txt"].decode("utf-8")
    except UnicodeDecodeError:
        raise IntegrityError("SHA256SUMS.txt is not UTF-8", code="EXPORT_MALFORMED_UTF8") from None
    for line in sums_text.splitlines():
        match = _SUMS_LINE.fullmatch(line)
        if not match or match[2] in listed:
            raise IntegrityError("SHA256SUMS.txt lists an unexpected entry", code="EXPORT_SUMS_INVALID")
        listed[match[2]] = match[1]
    if set(listed) != set(FILES) - {"SHA256SUMS.txt"}:
        raise IntegrityError("SHA256SUMS.txt does not cover the export files", code="EXPORT_SUMS_INVALID")
    for name, digest in listed.items():
        if sha256_bytes(data[name]) != digest:
            raise IntegrityError("Export file hash differs from SHA256SUMS.txt", code="EXPORT_HASH_MISMATCH")
    manifest = _strict(data["manifest.json"], "manifest.json")
    _shape(isinstance(manifest, dict), "manifest.json", "not an object")
    if manifest.get("schema") != "corpus-portable/1":
        raise ValidationError("Unsupported corpus export schema", code="EXPORT_SCHEMA_UNSUPPORTED")
    _shape(isinstance(manifest.get("release_id"), str) and bool(_ID_TEXT.fullmatch(manifest["release_id"])), "manifest.json", "release_id")
    _shape(isinstance(manifest.get("release_semantic_manifest_sha256"), str) and bool(_HEX64.fullmatch(manifest["release_semantic_manifest_sha256"])),
           "manifest.json", "release_semantic_manifest_sha256")
    _shape(type(manifest.get("item_count")) is int and 0 <= manifest["item_count"] <= MAX_ITEMS, "manifest.json", "item_count")
    inventory = manifest.get("file_inventory") or []
    _shape(isinstance(inventory, list) and all(isinstance(e, dict) for e in inventory), "manifest.json", "file_inventory")
    for entry in inventory:
        if entry.get("name") in data and (sha256_bytes(data[entry["name"]]) != entry.get("sha256") or len(data[entry["name"]]) != entry.get("bytes")):
            raise IntegrityError("Manifest file inventory mismatch", code="EXPORT_HASH_MISMATCH")
    lines = [line for line in data["corpus.jsonl"].split(b"\n") if line.strip()]
    if len(lines) > MAX_ITEMS:
        raise IntegrityError("Too many items", code="EXPORT_TOO_MANY_ITEMS")
    items, seen_items, seen_entities = [], set(), set()
    for number, line in enumerate(lines, 1):
        if len(line) > MAX_LINE_BYTES:
            raise IntegrityError(f"corpus.jsonl line {number} exceeds {MAX_LINE_BYTES} bytes", code="EXPORT_LINE_TOO_LONG")
        where = f"corpus.jsonl line {number}"
        item = _strict(line, where)
        validate_item_shape(item, where)
        if item["corpus_item_id"] in seen_items or (unique_entities and item["publication_entity_id"] in seen_entities):
            raise IntegrityError(f"Duplicate corpus_item_id or publication_entity_id at {where}", code="EXPORT_DUPLICATE_IDENTIFIER")
        seen_items.add(item["corpus_item_id"])
        seen_entities.add(item["publication_entity_id"])
        items.append(item)
    if manifest.get("item_count") != len(items):
        raise IntegrityError("Manifest item_count mismatch", code="EXPORT_ITEM_COUNT_MISMATCH")
    _check_csv(data["corpus.csv"], seen_items)
    return {"data": data, "manifest": manifest, "items": items}


def _check_csv(raw: bytes, item_ids: set) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise IntegrityError("corpus.csv is not UTF-8", code="EXPORT_MALFORMED_UTF8") from None
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise IntegrityError("corpus.csv is not well-formed CSV", code="EXPORT_SHAPE_INVALID") from None
    if not rows or not rows[0] or rows[0][0] != "corpus_item_id" or len(rows) - 1 > MAX_ITEMS:
        raise IntegrityError("corpus.csv header or row count invalid", code="EXPORT_SHAPE_INVALID")
    if any(len(cell) > MAX_STRING_CHARS for row in rows for cell in row) or any(len(row) != len(rows[0]) for row in rows):
        raise IntegrityError("corpus.csv cell too long or ragged row", code="EXPORT_SHAPE_INVALID")
    ids = [row[0] for row in rows[1:]]
    if len(ids) != len(set(ids)) or set(ids) != item_ids:
        raise IntegrityError("corpus.csv items differ from corpus.jsonl", code="EXPORT_CSV_MISMATCH")


def import_corpus_export(kb, directory: str, *, workspace_label: str, expected_manifest_sha256: str | None = None) -> dict:
    export = _read_export(directory)
    manifest, items, data = export["manifest"], export["items"], export["data"]
    release_sem = manifest.get("release_semantic_manifest_sha256")
    if expected_manifest_sha256 and expected_manifest_sha256 != release_sem:
        raise IntegrityError("Release manifest hash differs from the operator-supplied expected value", code="EXPECTED_MANIFEST_MISMATCH")
    store = MetadataStore(kb)
    integrity = IntegrityService(store)
    summary = {"release_id": manifest.get("release_id"), "items": len(items), "papers_created": 0, "assertions_created": 0,
               "divergences": 0, "integrity_events": 0, "coverage_rows": 0, "expected_hash_supplied": bool(expected_manifest_sha256)}
    with kb.transaction():
        record, created = kb.insert_unique("reprovane_export_import", dict(
            import_id=kb.ids.new("imp"), workspace_label=workspace_label, release_id=manifest["release_id"],
            manifest_bytes_sha256=sha256_bytes(data["manifest.json"]), corpus_jsonl_bytes_sha256=sha256_bytes(data["corpus.jsonl"]),
            sums_bytes_sha256=sha256_bytes(data["SHA256SUMS.txt"]), item_count=len(items), imported_at_utc=kb.now(),
            semantic_sha256=semantic_sha256({"manifest": sha256_bytes(data["manifest.json"]), "corpus": sha256_bytes(data["corpus.jsonl"]),
                                             "workspace": workspace_label})))
        summary["import_id"], summary["new_import"] = record["import_id"], created
        scope = f"rv:{workspace_label}:{manifest['release_id']}"
        for index, item in enumerate(items):
            _import_item(kb, store, integrity, item, index, manifest, scope, record["import_id"], summary)
    return summary


def _import_item(kb, store, integrity, item, index, manifest, scope, import_id, summary):
    corpus_item_id = item["corpus_item_id"]
    paper_basis = {"release_semantic_manifest_sha256": manifest.get("release_semantic_manifest_sha256"), "corpus_item_id": corpus_item_id}
    paper = store.ensure_paper(basis="IMPORTED_REPROVANE_ITEM", evidence_ref=f"{import_id}:{corpus_item_id}", semantic_basis=paper_basis)
    paper_sem = paper["semantic_sha256"]
    identity = item.get("identity") or {}
    doi_claims = [c for c in identity.get("provider_identifiers") or [] if c.get("namespace") == "doi"]
    parses = []
    for claim in doi_claims:
        parsed = parse_doi(claim.get("original") or "", DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
        parses.append((claim, parsed))
        kind = classify(claim.get("original") or "", claim.get("value"), parsed)
        if kind:
            store.add_divergence(source_ref=f"{scope}:{corpus_item_id}:{claim.get('observation_id')}", rv_original=claim.get("original") or "",
                                 rv_normalized=claim.get("value"), kb_parse=parsed, kind=kind)
            summary["divergences"] += 1
    valid_keys = sorted({p.comparison_key for _, p in parses if p.status == VALID})
    status = "CONFLICTING" if len(valid_keys) > 1 else "ACTIVE"
    for claim, parsed in parses:
        _, created = store.add_assertion(paper_id=paper["paper_id"], namespace="doi", value_original=claim.get("original") or "", parse=parsed,
                                         value_normalized=parsed.comparison_key if parsed.status == VALID else None, scope_ref=None,
                                         asserted_by="REPROVANE_EXPORT", evidence_ref=f"{import_id}:{corpus_item_id}",
                                         registration_agency="UNKNOWN", status=status if parsed.status == VALID else "CONFLICTING",
                                         paper_semantic=paper_sem)
        summary["assertions_created"] += int(created)
    for work in identity.get("openalex_work_ids") or []:
        _, created = store.add_assertion(paper_id=paper["paper_id"], namespace="openalex_work", value_original=work, parse=None,
                                         value_normalized=work, scope_ref=None, asserted_by="REPROVANE_EXPORT",
                                         evidence_ref=f"{import_id}:{corpus_item_id}", registration_agency="NOT_APPLICABLE",
                                         status="ACTIVE", paper_semantic=paper_sem)
        summary["assertions_created"] += int(created)
    for namespace, value in (("reprovane_corpus_item", corpus_item_id), ("reprovane_publication_entity", item.get("publication_entity_id"))):
        if value:
            _, created = store.add_assertion(paper_id=paper["paper_id"], namespace=namespace, value_original=value, parse=None,
                                             value_normalized=value, scope_ref=scope, asserted_by="REPROVANE_EXPORT",
                                             evidence_ref=f"{import_id}:{corpus_item_id}", registration_agency="NOT_APPLICABLE",
                                             status="ACTIVE", paper_semantic=paper_sem)
            summary["assertions_created"] += int(created)
    _import_openalex_integrity(kb, store, integrity, item, index, manifest, valid_keys, summary)


def _import_openalex_integrity(kb, store, integrity, item, index, manifest, valid_keys, summary):
    """OpenAlex ``is_retracted`` evidence as exported by Reprovane (RV integrity observations; RETRACTION only)."""
    records = (item.get("metadata") or {}).get("records") or []
    works = (item.get("identity") or {}).get("openalex_work_ids") or []
    if len(valid_keys) == 1:
        subject = ("doi", valid_keys[0])
    elif len(works) == 1:
        subject = ("openalex_work", works[0])
    else:
        return
    for r_index, record in enumerate(records):
        if (record.get("source") or {}).get("provider") != "openalex" or "integrity" not in (record.get("fields") or {}):
            continue  # integrity not projected by the release plan: no coverage can be claimed
        claims = record["fields"]["integrity"] or []
        identity = {"provider": "OPENALEX_VIA_REPROVANE_EXPORT", "release_semantic_manifest_sha256": manifest.get("release_semantic_manifest_sha256"),
                    "corpus_item_id": item["corpus_item_id"], "provider_record_id": record.get("provider_record_id")}
        body = canonical_json(record).encode("utf-8")
        snapshot, _ = store.record_snapshot(provider="OPENALEX_VIA_REPROVANE_EXPORT", request_identity=identity, http_status=None, body=body,
                                            media_type="application/json", outcome="FOUND", reason=None, parser_name=PARSER_NAME,
                                            parser_version=PARSER_VERSION, run_id=None)
        events = []
        for c_index, claim in enumerate(claims):
            if claim.get("event_type") == "RETRACTION":
                events.append({"subject_namespace": subject[0], "subject_value": subject[1], "event_type": "RETRACTION",
                               "event_subtype": claim.get("status_code"), "notice_doi": None,
                               "reported_by": "OPENALEX_IS_RETRACTED_VIA_REPROVANE", "record_id": claim.get("observation_id"),
                               "event_date": None, "source_field_path": f"/fields/integrity/{c_index}",
                               "payload": {"rv_source_field_path": claim.get("source_field_path"), "corpus_item_index": index,
                                           "record_index": r_index}})
        recorded = integrity.record_provider_events(snapshot, events)
        summary["integrity_events"] += sum(1 for _, created in recorded if created)
        scope = {"event_types": ["RETRACTION"], "crossref_rw_included": "NOT_APPLICABLE", "record": "openalex_work_via_reprovane"}
        _, created = integrity.add_coverage(subject_namespace=subject[0], subject_value=subject[1], provider="OPENALEX_VIA_REPROVANE_EXPORT",
                                            snapshot=snapshot, outcome="SIGNAL_REPORTED" if events else "NO_SIGNAL_REPORTED", scope_note=scope)
        summary["coverage_rows"] += int(created)
