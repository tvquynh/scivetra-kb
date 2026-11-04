"""Deterministic export of one journal collection version (``jh-export/1``).

Writes one new folder ``journal_export_v<no>_<8 hex>`` under ``--out-dir`` (outside the KB):

* ``journal_works.csv``          UTF-8 (no BOM), LF, one row per subject; spreadsheet formula prefixes neutralized with ``'``
* ``journal_works.jsonl``        canonical JSON per subject: projection, category, flags, evidence observation IDs
* ``field_provenance.jsonl``     sidecar: per subject, the provider/observation/run/page/JSON pointer of every projected field
* ``conflicts.jsonl``            every stored conflict with all values and sources
* ``records_without_usable_doi.csv``  NO_DOI / DOI_AMBIGUOUS / DOI_INVALID subjects (quarantine view; never dropped)
* ``review_tasks.csv``           the journal's review queue
* ``oa_candidate_queue_dryrun.csv``  jh-oa-dryrun/1 candidates; PDF identity UNVERIFIED; download_authorized NO; never sent anywhere
* ``coverage.json``              the version's machine-readable coverage report
* ``export_manifest.json``, ``SHA256SUMS.txt``

Bytes depend only on the stored version (no wall-clock value is written), so exporting the same version twice yields
identical files; re-exporting into the same folder is a verified no-op. Files are first written to a temporary sibling
folder and renamed into place, so a crash never leaves a half-written export under the final name.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil
from pathlib import Path

from ..bridge.service import guard_path_text, path_hash
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import ValidationError
from ..core.paths import safe_data_root
from ..library.common import operator_label
from .reconcile import STATEMENT

PROFILE = "jh-export/1"
COLUMNS = ("subject_id", "subject_kind", "category", "membership_asserted", "doi_key", "doi_url", "doi_original_crossref", "doi_original_openalex",
           "openalex_work_ids", "title", "work_type", "openalex_type", "container_title", "issns", "publisher", "publication_year",
           "publication_year_basis", "published_online", "published_print", "issued", "openalex_publication_date", "volume", "issue", "pages",
           "article_number", "authors", "license_urls", "is_oa", "oa_status", "best_oa_landing_url", "best_oa_pdf_url", "oa_dry_run_status",
           "retraction_reported_ever", "integrity_event_types", "openalex_is_retracted_latest", "crossref_updated_at", "openalex_updated_at",
           "first_seen_at", "last_seen_at", "metadata_sources", "crossref_query_issns", "title_source", "publication_year_source", "flags", "existing_library_papers",
           "collection_version_id", "journal_id", "crossref_run_id", "openalex_run_id", "screening_status", "inventory_scope")
OA_COLUMNS = ("subject_id", "doi_url", "title", "pdf_url", "location_kind", "version", "rights_class", "licence_families", "pdf_identity",
              "download_authorized", "evidence_observation_id", "evidence_path", "rule")
TASK_COLUMNS = ("task_id", "subject", "trigger", "first_version_id", "created_at_utc", "context_json")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value)
    text = str(value)
    return "'" + text if text.startswith(_FORMULA) else text


def _csv(columns, rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return buffer.getvalue().encode("utf-8")


def _jsonl(rows) -> bytes:
    return "".join(canonical_json(r) + "\n" for r in rows).encode("utf-8")


def build_files(kb, version_id: str, *, work_types: list[str] | None = None) -> tuple[dict[str, bytes], dict]:
    version = kb.one("SELECT * FROM jh_collection_version WHERE version_id=?", (version_id,))
    if not version:
        raise ValidationError("Unknown collection version", code="UNKNOWN_VERSION")
    basis = json.loads(version["basis_json"])
    members = kb.all("SELECT * FROM jh_member WHERE version_id=?", (version_id,))
    members.sort(key=lambda m: (m["subject_kind"], m["doi_key"] or "", m["record_ref"] or ""))
    wanted = set(work_types) if work_types else None
    rows, works, provenance, quarantine, oa_rows = [], [], [], [], []
    for m in members:
        proj = json.loads(m["projection_json"])
        if wanted is not None and proj.get("work_type") not in wanted and proj.get("openalex_type") not in wanted:
            continue
        prov = json.loads(m["provenance_json"])
        flags = json.loads(m["flags_json"])
        subject = m["doi_key"] or m["record_ref"]
        row = {c: proj.get(c) for c in COLUMNS if c in proj}
        row.update(subject_id=subject, subject_kind=m["subject_kind"], category=m["category"], membership_asserted=m["membership_asserted"],
                   doi_key=m["doi_key"], oa_dry_run_status=proj["oa_dry_run"]["status"],
                   metadata_sources=json.loads(m["providers_json"]), flags=flags,
                   title_source=(prov.get("title") or {}).get("provider"), publication_year_source=(prov.get("publication_year") or {}).get("provider"),
                   existing_library_papers=json.loads(m["paper_candidates_json"]), collection_version_id=version_id,
                   journal_id=version["journal_id"], crossref_run_id=basis["CROSSREF"]["full_run_id"], openalex_run_id=basis["OPENALEX"]["full_run_id"],
                   screening_status="NOT_SCREENED", inventory_scope="DISCOVERY_EVIDENCE_ONLY")
        rows.append(row)
        works.append({"subject_id": subject, "subject_kind": m["subject_kind"], "category": m["category"],
                      "membership_asserted": bool(m["membership_asserted"]), "providers": json.loads(m["providers_json"]), "flags": flags,
                      "projection": proj, "evidence": json.loads(m["evidence_json"]), "existing_library_papers": json.loads(m["paper_candidates_json"]),
                      "screening_status": "NOT_SCREENED", "inventory_scope": "DISCOVERY_EVIDENCE_ONLY"})
        provenance.append({"subject_id": subject, "fields": prov})
        if m["subject_kind"] != "DOI":
            quarantine.append(row)
        dry = proj["oa_dry_run"]
        if dry["status"] == "DRY_RUN_CANDIDATE":
            oa_rows.append({"subject_id": subject, "doi_url": proj.get("doi_url"), "title": proj.get("title"), "pdf_url": dry["pdf_url"],
                            "location_kind": dry["location_kind"], "version": dry["version"], "rights_class": dry["rights_class"],
                            "licence_families": dry["licence_families"], "pdf_identity": "UNVERIFIED", "download_authorized": "NO",
                            "evidence_observation_id": dry["evidence"]["location"]["observation_id"], "evidence_path": dry["evidence"]["location"]["path"],
                            "rule": dry["rule"]})
    conflicts = [{"subject": c["subject"], "field": c["field"], "kind": c["kind"], "values": json.loads(c["values_json"])}
                 for c in kb.all("SELECT * FROM jh_conflict WHERE version_id=?", (version_id,))]
    conflicts.sort(key=lambda c: (c["subject"], c["field"], c["kind"], canonical_json(c["values"])))
    # The queue as of this version: tasks first raised by this or an earlier version, plus resolution tasks raised before it.
    tasks = kb.all("SELECT t.task_id, t.subject, t.trigger, t.first_version_id, t.created_at_utc, t.context_json FROM jh_review_task t "
                   "LEFT JOIN jh_collection_version v ON v.version_id = t.first_version_id WHERE t.journal_id=? AND "
                   "((v.version_no IS NOT NULL AND v.version_no <= ?) OR (t.first_version_id IS NULL AND t.created_at_utc <= ?)) "
                   "ORDER BY t.subject, t.trigger, t.semantic_sha256", (version["journal_id"], version["version_no"], version["created_at_utc"]))
    files = {
        "journal_works.csv": _csv(COLUMNS, rows),
        "journal_works.jsonl": _jsonl(works),
        "field_provenance.jsonl": _jsonl(provenance),
        "conflicts.jsonl": _jsonl(conflicts),
        "records_without_usable_doi.csv": _csv(COLUMNS, quarantine),
        "review_tasks.csv": _csv(TASK_COLUMNS, tasks),
        "oa_candidate_queue_dryrun.csv": _csv(OA_COLUMNS, oa_rows),
        "coverage.json": (canonical_json(json.loads(version["coverage_json"])) + "\n").encode("utf-8"),
    }
    manifest = {"schema": PROFILE, "journal_id": version["journal_id"], "version_id": version_id, "version_no": version["version_no"],
                "collection_state": version["collection_state"], "members_sha256": version["members_sha256"],
                "filter": {"work_types": sorted(wanted) if wanted else None}, "row_counts": {"subjects": len(rows), "oa_dry_run_candidates": len(oa_rows),
                                                                                         "conflicts": len(conflicts), "review_tasks": len(tasks),
                                                                                         "records_without_usable_doi": len(quarantine)},
                "files": {name: {"sha256": sha256_bytes(data), "bytes": len(data)} for name, data in sorted(files.items())},
                "scope_statement": STATEMENT, "downloads_performed": 0, "pdf_identity_policy": "UNVERIFIED_UNTIL_HUMAN_CONFIRMATION"}
    files["export_manifest.json"] = (canonical_json(manifest) + "\n").encode("utf-8")
    files["SHA256SUMS.txt"] = "".join(f"{sha256_bytes(d)}  {n}\n" for n, d in sorted(files.items())).encode("utf-8")
    return files, manifest


def export_version(kb, version_id: str, out_dir: str, *, operator: str, work_types: list[str] | None = None) -> dict:
    label = operator_label(operator)
    guard_path_text(out_dir, "--out-dir")
    target = safe_data_root(out_dir, must_exist=False)
    if target == kb.root or target.is_relative_to(kb.root) or kb.root.is_relative_to(target):
        raise ValidationError("--out-dir must be outside the KB data root", code="OUT_DIR_INVALID")
    files, manifest = build_files(kb, version_id, work_types=work_types)
    manifest_sha = sha256_bytes(files["export_manifest.json"])
    folder = target / f"journal_export_v{manifest['version_no']}_{manifest_sha[:8]}"
    result = {"version_id": version_id, "folder_name": folder.name, "manifest_sha256": manifest_sha, "files": manifest["files"],
              "row_counts": manifest["row_counts"]}
    if folder.exists():
        present = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        if present != files or any(p.is_dir() for p in folder.iterdir()):
            raise ValidationError("Export folder exists with different content; choose another --out-dir", code="EXPORT_FOLDER_EXISTS_DIFFERENT")
        return dict(result, written=False, code="EXPORT_ALREADY_PRESENT_IDENTICAL")
    target.mkdir(parents=True, exist_ok=True)
    staging = target / (folder.name + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for name, data in sorted(files.items()):
        with open(staging / name, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(staging, folder)
    row = dict(export_id=kb.ids.new("jx"), version_id=version_id, profile=PROFILE, filter_json=canonical_json(manifest["filter"]),
               files_json=canonical_json(manifest["files"]), manifest_sha256=manifest_sha, out_dir_path_sha256=path_hash(target),
               created_by=label, created_at_utc=kb.now())
    row["semantic_sha256"] = semantic_sha256({"version": version_id, "manifest": manifest_sha, "out": row["out_dir_path_sha256"]})
    with kb.transaction():
        kb.insert_unique("jh_export", row)
    return dict(result, written=True)


__all__ = ["export_version", "build_files", "COLUMNS", "PROFILE"]
