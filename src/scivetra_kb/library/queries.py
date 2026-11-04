"""Read-only library views: operator review queue, paper/DOI search, reconcile against a portable export copy.

Every function here takes a read-only KB handle (SQLite mode=ro) and writes nothing.
"""
from __future__ import annotations

import json

from ..core.canonical import sha256_bytes
from ..metadata.doi import DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT, VALID, parse_doi
from ..metadata.rv_import import FILES, _read_export
from .common import latest
from .service import conflict_state, paper_identity_state


def review_queue(kb) -> dict:
    """Everything waiting for a human: link candidates, identity conflicts, asset links, family proposals, rights tasks."""
    links = []
    for c in kb.all("SELECT * FROM link_candidate ORDER BY rowid"):
        decision = latest(kb, "link_decision", "link_candidate_id", c["link_candidate_id"])
        if decision is None or decision["decision"] == "DEFER":
            links.append({"link_candidate_id": c["link_candidate_id"], "subject_paper_id": c["subject_paper_id"],
                          "candidate_paper_id": c["candidate_paper_id"], "signals": json.loads(c["signals_json"]),
                          "conflict_codes": json.loads(c["conflict_codes_json"]), "deferred": decision is not None})
    conflicts = [{"conflict_id": c["conflict_id"], "kind": c["kind"], "key": [c["key_namespace"], c["key_value"]],
                  "paper_ids": json.loads(c["paper_ids_json"]), "assertion_ids": json.loads(c["assertion_ids_json"]),
                  "detail": json.loads(c["detail_json"])}
                 for c in kb.all("SELECT * FROM identity_conflict ORDER BY rowid") if conflict_state(kb, c["conflict_id"]) == "OPEN"]
    asset_links = [{"link_id": r["link_id"], "asset_sha256": r["asset_sha256"], "paper_id": r["paper_id"], "role": r["effective_role"],
                    "state": r["effective_state"], "reason_codes": json.loads(r["reason_codes_json"])}
                   for r in kb.all("SELECT * FROM paper_asset_link_effective WHERE effective_state IN ('UNVERIFIED','AMBIGUOUS') ORDER BY row_seq")]
    members = []
    for m in kb.all("SELECT * FROM work_family_member ORDER BY rowid"):
        event = latest(kb, "work_family_member_event", "member_id", m["member_id"])
        if event is None or event["confirmation"] == "PROPOSED":
            members.append({"member_id": m["member_id"], "work_family_id": m["work_family_id"], "paper_id": m["paper_id"], "role": m["role"]})
    rights = []
    for t in kb.all("SELECT * FROM rights_review_task ORDER BY rowid"):
        current = kb.one("SELECT assessment_id FROM rights_assessment WHERE asset_sha256=? AND document_version_id=? ORDER BY rowid DESC LIMIT 1",
                         (t["asset_sha256"], t["document_version_id"]))
        if current and current["assessment_id"] == t["assessment_id"]:
            rights.append({"task_id": t["task_id"], "asset_sha256": t["asset_sha256"], "document_version_id": t["document_version_id"],
                           "trigger": t["trigger"], "context": json.loads(t["context_json"])})
    integrity_tasks = kb.one("SELECT count(*) n FROM review_task")["n"]
    return {"link_candidates": links, "identity_conflicts": conflicts, "asset_links": asset_links, "work_family_proposals": members,
            "rights_tasks": rights, "integrity_review_tasks_total": integrity_tasks,
            "counts": {"link_candidates": len(links), "identity_conflicts": len(conflicts), "asset_links": len(asset_links),
                       "work_family_proposals": len(members), "rights_tasks": len(rights)}}


def paper_view(kb, paper_id: str) -> dict:
    paper = kb.one("SELECT paper_id, creation_basis, manifestation_kind, identity_state FROM global_paper WHERE paper_id=?", (paper_id,))
    if paper is None:
        return {"paper_id": paper_id, "found": False}
    kind = latest(kb, "paper_kind_event", "paper_id", paper_id)
    paper.update(found=True, identity_state_effective=paper_identity_state(kb, paper_id),
                 manifestation_kind_effective=kind["manifestation_kind"] if kind else paper["manifestation_kind"])
    paper["assertions"] = kb.all("SELECT assertion_id, namespace, value_original, value_normalized, scope_ref, asserted_by, status, effective_status "
                                 "FROM identity_assertion_effective WHERE paper_id=? ORDER BY row_seq", (paper_id,))
    paper["references"] = [dict(r, item_claims=json.loads(r.pop("item_claims_json"))) for r in kb.all(
        "SELECT r.reference_id, w.operator_label workspace_label, r.release_id, r.corpus_item_id, r.link_basis, r.link_key_json, r.item_claims_json "
        "FROM project_paper_reference r JOIN workspace_registration w ON w.workspace_ref=r.workspace_ref WHERE r.paper_id=? ORDER BY r.rowid",
        (paper_id,))]
    paper["document_versions"] = kb.all("SELECT document_version_id, version_class, version_label FROM document_version WHERE paper_id=? ORDER BY rowid",
                                        (paper_id,))
    paper["asset_links"] = kb.all("SELECT link_id, asset_sha256, document_version_id, effective_role role, effective_state state "
                                  "FROM paper_asset_link_effective WHERE paper_id=? ORDER BY row_seq", (paper_id,))
    return paper


def search(kb, *, doi: str | None = None, title: str | None = None, paper_id: str | None = None) -> dict:
    """DOI search uses the exact S02 comparison key (trailing spaces significant); non-VALID input triggers no lookup."""
    out = {}
    if doi is not None:
        parse = parse_doi(doi, DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT)
        out["doi_parse"] = parse.as_dict()
        if parse.status != VALID:
            out["note"] = "No lookup for non-VALID input; candidates are shown for human review only"
            out["papers"] = []
        else:
            ids = [r["paper_id"] for r in kb.all("SELECT DISTINCT paper_id FROM identity_assertion WHERE namespace='doi' AND value_normalized=? "
                                                 "ORDER BY paper_id", (parse.comparison_key,))]
            out["papers"] = [paper_view(kb, p) for p in ids]
    if title is not None:
        needle = title.casefold()
        ids = sorted({r["paper_id"] for r in kb.all("SELECT paper_id, item_claims_json FROM project_paper_reference")
                      if any(needle in t.casefold() for t in json.loads(r["item_claims_json"])["titles"])})
        out["title_matches"] = [paper_view(kb, p) for p in ids]
    if paper_id is not None:
        out["paper"] = paper_view(kb, paper_id)
    return out


def reconcile(kb, *, workspace_label: str, export_dir: str) -> dict:
    """Read-only comparison of a registered workspace with a supplied portable-export copy (10 section 7)."""
    workspace = kb.one("SELECT * FROM workspace_registration WHERE operator_label=?", (workspace_label,))
    if workspace is None:
        return {"workspace_label": workspace_label, "status": "WORKSPACE_UNKNOWN"}
    availability = latest(kb, "workspace_availability_event", "workspace_ref", workspace["workspace_ref"])
    export = _read_export(export_dir, unique_entities=True)
    release_sem = export["manifest"]["release_semantic_manifest_sha256"]
    hashes = {name: sha256_bytes(export["data"][name]) for name in FILES}
    known = kb.all("SELECT * FROM reprovane_release_import WHERE workspace_ref=? ORDER BY rowid", (workspace["workspace_ref"],))
    match = next((r for r in known if r["release_manifest_sha256"] == release_sem), None)
    out = {"workspace_label": workspace_label, "workspace_ref": workspace["workspace_ref"],
           "availability": availability["availability"] if availability else None, "release_id": export["manifest"]["release_id"],
           "release_manifest_sha256": release_sem, "known_releases": [r["release_id"] for r in known]}
    if match is None:
        out["status"] = "NEW_RELEASE_NOT_IMPORTED"
        return out
    same_bytes = (match["manifest_bytes_sha256"], match["corpus_jsonl_bytes_sha256"], match["corpus_csv_bytes_sha256"], match["sums_bytes_sha256"]) == \
        (hashes["manifest.json"], hashes["corpus.jsonl"], hashes["corpus.csv"], hashes["SHA256SUMS.txt"])
    refs = {r["corpus_item_id"]: r["item_sha256"] for r in kb.all(
        "SELECT corpus_item_id, item_sha256 FROM project_paper_reference WHERE release_import_id=?", (match["release_import_id"],))}
    drift = sorted(i["corpus_item_id"] for i in export["items"] if refs.get(i["corpus_item_id"]) != i["item_sha256"])
    missing = sorted(set(refs) - {i["corpus_item_id"] for i in export["items"]})
    out.update(status="CONFIRMED" if same_bytes and not drift and not missing else "DRIFT", file_bytes_identical=same_bytes,
               item_hash_mismatches=drift, references_missing_from_export=missing, references=len(refs))
    return out
