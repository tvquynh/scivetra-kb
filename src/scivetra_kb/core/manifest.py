"""Semantic manifest of all semantic (non-operational) KB rows.

Built from content hashes only, so the manifest is identical across re-imports
and across fresh databases fed the same evidence. Excluded (operational):
kb_instance, fetch_run, provider_check, request_reservation, request_completion,
schema_migration, timestamps and generated IDs.
"""
from __future__ import annotations

import json

from .canonical import canonical_json, semantic_sha256

SEMANTIC_TABLES = ("global_paper", "identity_assertion", "provider_snapshot", "ra_claim", "metadata_change_event", "integrity_event",
                   "integrity_coverage", "review_task", "normalization_divergence", "identifier_warning", "reprovane_export_import")


def semantic_manifest(kb) -> dict:
    sections: dict[str, list] = {}
    for table in SEMANTIC_TABLES:
        sections[table] = sorted(r["semantic_sha256"] for r in kb.all(f"SELECT semantic_sha256 FROM {table}"))
    snap = {r["snapshot_id"]: r["semantic_sha256"] for r in kb.all("SELECT snapshot_id, semantic_sha256 FROM provider_snapshot")}
    event = {r["event_id"]: r["semantic_sha256"] for r in kb.all("SELECT event_id, semantic_sha256 FROM integrity_event")}
    sections["metadata_claim"] = sorted(semantic_sha256([snap[r["snapshot_id"]], r["claim_semantic_sha256"]])
                                        for r in kb.all("SELECT snapshot_id, claim_semantic_sha256 FROM metadata_claim"))
    sections["integrity_event_sighting"] = sorted(semantic_sha256([event[r["event_id"]], snap[r["snapshot_id"]], r["source_field_path"]])
                                                  for r in kb.all("SELECT * FROM integrity_event_sighting"))
    sections["operator_document"] = sorted(r["document_sha256"] for r in kb.all("SELECT document_sha256 FROM operator_document"))
    sections["integrity_counter_evidence"] = sorted(semantic_sha256([event[r["opposes_event_id"]], r["kind"], r["primary_document_ref"],
                                                                     json.loads(r["evidence_anchor_json"]) | ({"snapshot_id": snap.get(json.loads(r["evidence_anchor_json"]).get("snapshot_id"))}
                                                                     if json.loads(r["evidence_anchor_json"]).get("snapshot_id") else {}),
                                                                     r["statement_summary"], r["recorded_by"]])
                                                    for r in kb.all("SELECT * FROM integrity_counter_evidence"))
    sections["integrity_review"] = sorted(semantic_sha256([r["subject_namespace"], r["subject_value"], sorted(event[e] for e in json.loads(r["event_ids_json"])),
                                                           r["trigger"], r["decision"], r["reviewer_label"], r["reason"]])
                                          for r in kb.all("SELECT * FROM integrity_review"))
    counts = {k: len(v) for k, v in sections.items()}
    return {"schema": "rkb-semantic-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": counts}


# S03 (migration 0002) semantic tables. Their semantic_sha256 values reference other rows only through those rows'
# semantic hashes, never through IDs or timestamps. Operational journals (import_event, kb_upgrade_event and
# rejected validation attempts) are excluded.
LIBRARY_TABLES = ("workspace_registration", "workspace_availability_event", "reprovane_release_import", "project_paper_reference",
                  "paper_kind_event", "identity_conflict", "identity_resolution_event", "assertion_status_event", "link_candidate",
                  "link_decision", "work_family", "work_family_member", "work_family_member_event", "document_version", "pdf_asset",
                  "paper_asset_link", "paper_asset_link_event", "rights_assessment", "rights_review_task")


def library_manifest(kb) -> dict:
    sections: dict[str, list] = {}
    for table in LIBRARY_TABLES:
        sections[table] = sorted(r["semantic_sha256"] for r in kb.all(f"SELECT semantic_sha256 FROM {table}"))
    sections["asset_validation_event"] = sorted(
        {semantic_sha256([r["input_sha256"], r["validator"], r["validator_version"], r["outcome"], json.loads(r["reason_codes_json"])])
         for r in kb.all("SELECT * FROM asset_validation_event WHERE outcome='STRUCTURALLY_VALID'")})
    counts = {k: len(v) for k, v in sections.items()}
    return {"schema": "rkb-library-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": counts}


def full_manifest(kb) -> dict:
    """``rkb-semantic-manifest/2``: the unchanged S02 manifest plus the S03 library manifest."""
    s02, library = semantic_manifest(kb), library_manifest(kb)
    return {"schema": "rkb-semantic-manifest/2", "s02_manifest_sha256": s02["manifest_sha256"],
            "library_manifest_sha256": library["manifest_sha256"],
            "manifest_sha256": semantic_sha256([s02["manifest_sha256"], library["manifest_sha256"]]),
            "counts": {"s02": s02["counts"], "library": library["counts"]}}


BRIDGE_TABLES = ("bridge_batch", "bridge_row", "bridge_attempt", "bridge_file_event", "bridge_row_event", "bridge_batch_event", "bridge_review_task")


def bridge_manifest(kb) -> dict:
    """``rkb-bridge-domain-manifest/1`` (schema >= 3): semantic hashes of the append-only bridge tables. Import journal rows
    (``bridge_result_import``) carry IDs and times only and are counted, not hashed."""
    sections = {t: sorted(r["semantic_sha256"] for r in kb.all(f"SELECT semantic_sha256 FROM {t}")) for t in BRIDGE_TABLES if t != "bridge_row"}
    sections["bridge_row"] = sorted(r["row_semantic_sha256"] for r in kb.all("SELECT row_semantic_sha256 FROM bridge_row"))
    counts = {k: len(v) for k, v in sections.items()}
    counts["bridge_result_import"] = kb.all("SELECT count(*) n FROM bridge_result_import")[0]["n"]
    return {"schema": "rkb-bridge-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": counts}


JOURNAL_TABLES = ("jh_count_observation", "jh_issn_evidence", "jh_source_resolution", "jh_observation", "jh_collection_version",
                  "jh_review_task")


def journal_manifest(kb) -> dict:
    """``rkb-journal-domain-manifest/1`` (schema >= 4): semantic hashes of the append-only S04J tables that carry one, plus
    ID-independent digests of runs, pages, claims, members, conflicts and exports (IDs and wall-clock times excluded)."""
    sections = {t: sorted(r["semantic_sha256"] for r in kb.all(f"SELECT semantic_sha256 FROM {t}")) for t in JOURNAL_TABLES}
    sections["jh_journal"] = sorted(r["collection_key"] for r in kb.all("SELECT collection_key FROM jh_journal"))
    sections["jh_run"] = sorted(r["q"] for r in kb.all("SELECT provider || ':' || mode || ':' || query_sha256 AS q FROM jh_run"))
    sections["jh_page"] = sorted(r["h"] for r in kb.all("SELECT kind || ':' || outcome || ':' || coalesce(response_bytes_sha256, '-') AS h FROM jh_page"))
    sections["jh_field_claim"] = sorted(r["claim_semantic_sha256"] for r in kb.all("SELECT claim_semantic_sha256 FROM jh_field_claim"))
    sections["jh_member"] = sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM jh_member"))
    sections["jh_conflict"] = sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM jh_conflict"))
    sections["jh_export"] = sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM jh_export"))
    counts = {k: len(v) for k, v in sections.items()}
    counts["jh_run_event"] = kb.all("SELECT count(*) n FROM jh_run_event")[0]["n"]
    return {"schema": "rkb-journal-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": counts}


ELIGIBILITY_TABLES = ("fte_protocol", "fte_run", "fte_run_event", "fte_reviewer", "fte_candidate", "fte_assignment", "fte_evidence",
                      "fte_acquisition_attempt", "fte_decision", "fte_adjudication", "fte_lock")


PARSE_TABLES = ("parse_artifact", "parse_run", "parse_export_event")


def parse_manifest(kb) -> dict:
    """``rkb-parse-domain-manifest/1`` (schema >= 6): ID-independent digests of the S06 append-only tables."""
    sections = {
        "parse_artifact": sorted(r["h"] for r in kb.all("SELECT semantic_sha256 || ':' || artifact_sha256 AS h FROM parse_artifact")),
        "parse_run": sorted(r["h"] for r in kb.all("SELECT purpose || ':' || outcome || ':' || reason_codes_json || ':' || requested_asset_sha256 AS h "
                                                   "FROM parse_run")),
        "parse_export_event": sorted(r["h"] for r in kb.all("SELECT state || ':' || decision || ':' || reason_codes_json AS h FROM parse_export_event")),
    }
    return {"schema": "rkb-parse-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": {k: len(v) for k, v in sections.items()}}


SCREENING_TABLES = ("ai_criteria_set", "ai_processing_scope", "ai_input_pack", "ai_proposal", "ai_proposal_verification", "ai_reviewer",
                    "ai_human_judgement", "ai_proposal_reveal", "ai_approval")


def screening_manifest(kb) -> dict:
    """``rkb-screening-domain-manifest/1`` (schema >= 7): ID-independent digests of the S07 append-only tables."""
    sections = {
        "ai_criteria_set": sorted(r["criteria_sha256"] for r in kb.all("SELECT criteria_sha256 FROM ai_criteria_set")),
        "ai_processing_scope": sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM ai_processing_scope")),
        "ai_input_pack": sorted(r["pack_sha256"] for r in kb.all("SELECT pack_sha256 FROM ai_input_pack")),
        "ai_proposal": sorted(r["proposal_semantic_sha256"] for r in kb.all("SELECT proposal_semantic_sha256 FROM ai_proposal")),
        "ai_proposal_verification": sorted(r["h"] for r in kb.all("SELECT state || ':' || failure_codes_json AS h FROM ai_proposal_verification")),
        "ai_reviewer": sorted(r["label"] for r in kb.all("SELECT label FROM ai_reviewer")),
        "ai_human_judgement": sorted(r["initial_outcome"] for r in kb.all("SELECT initial_outcome FROM ai_human_judgement")),
        "ai_approval": sorted(r["decision"] for r in kb.all("SELECT decision FROM ai_approval")),
    }
    return {"schema": "rkb-screening-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": {k: len(v) for k, v in sections.items()}}


EVIDENCE_TABLES = ("ev_profile", "ev_scope", "ev_input_pack", "ev_extraction_run", "ev_draft", "ev_verification", "ev_reveal", "ev_review",
                   "ev_adjudication", "ev_export_event")


def evidence_manifest(kb) -> dict:
    """``rkb-evidence-domain-manifest/1`` (schema >= 8): ID-independent digests of the S08 append-only tables."""
    sections = {
        "ev_profile": sorted(r["profile_sha256"] for r in kb.all("SELECT profile_sha256 FROM ev_profile")),
        "ev_scope": sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM ev_scope")),
        "ev_input_pack": sorted(r["pack_sha256"] for r in kb.all("SELECT pack_sha256 FROM ev_input_pack")),
        "ev_extraction_run": sorted(r["run_semantic_sha256"] for r in kb.all("SELECT run_semantic_sha256 FROM ev_extraction_run")),
        "ev_draft": sorted(r["draft_semantic_sha256"] for r in kb.all("SELECT draft_semantic_sha256 FROM ev_draft")),
        "ev_verification": sorted(r["h"] for r in kb.all("SELECT state || ':' || failure_codes_json AS h FROM ev_verification")),
        "ev_reveal": sorted(r["quote_disclosure"] for r in kb.all("SELECT quote_disclosure FROM ev_reveal")),
        "ev_review": sorted(r["h"] for r in kb.all("SELECT decision || ':' || reason_codes_json AS h FROM ev_review")),
        "ev_adjudication": sorted(r["outcome"] for r in kb.all("SELECT outcome FROM ev_adjudication")),
        "ev_export_event": sorted(r["h"] for r in kb.all("SELECT phase || ':' || report_sha256 AS h FROM ev_export_event")),
    }
    return {"schema": "rkb-evidence-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": {k: len(v) for k, v in sections.items()}}


RETRIEVAL_TABLES = ("rt_scope", "rt_index_build", "rt_access_event")


def retrieval_manifest(kb) -> dict:
    """``rkb-retrieval-domain-manifest/1`` (schema >= 9): ID-independent digests of the S09 append-only tables."""
    sections = {
        "rt_scope": sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM rt_scope")),
        "rt_index_build": sorted(r["manifest_sha256"] for r in kb.all("SELECT manifest_sha256 FROM rt_index_build")),
        "rt_access_event": sorted(r["h"] for r in kb.all("SELECT operation || ':' || outcome || ':' || returned_json AS h FROM rt_access_event")),
    }
    return {"schema": "rkb-retrieval-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": {k: len(v) for k, v in sections.items()}}


def eligibility_manifest(kb) -> dict:
    """``rkb-eligibility-domain-manifest/1`` (schema >= 5): ID-independent digests of the S05 append-only tables."""
    sections = {
        "fte_protocol": sorted(r["protocol_sha256"] for r in kb.all("SELECT protocol_sha256 FROM fte_protocol")),
        "fte_run": sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM fte_run")),
        "fte_run_event": sorted(r["h"] for r in kb.all("SELECT new_state || ':' || reason AS h FROM fte_run_event")),
        "fte_candidate": sorted(r["h"] for r in kb.all("SELECT corpus_item_id || ':' || item_sha256 AS h FROM fte_candidate")),
        "fte_evidence": sorted(r["evidence_fingerprint"] for r in kb.all("SELECT evidence_fingerprint FROM fte_evidence")),
        "fte_acquisition_attempt": sorted(r["semantic_sha256"] for r in kb.all("SELECT semantic_sha256 FROM fte_acquisition_attempt")),
        "fte_decision": sorted(r["decision_sha256"] for r in kb.all("SELECT decision_sha256 FROM fte_decision")),
        "fte_adjudication": sorted(r["adjudication_sha256"] for r in kb.all("SELECT adjudication_sha256 FROM fte_adjudication")),
        "fte_lock": sorted(r["handoff_manifest_sha256"] for r in kb.all("SELECT handoff_manifest_sha256 FROM fte_lock")),
    }
    counts = {k: len(v) for k, v in sections.items()}
    counts["fte_reviewer"] = kb.all("SELECT count(*) n FROM fte_reviewer")[0]["n"]
    counts["fte_assignment"] = kb.all("SELECT count(*) n FROM fte_assignment")[0]["n"]
    return {"schema": "rkb-eligibility-domain-manifest/1", "manifest_sha256": semantic_sha256(sections), "counts": counts}


def table_content_sha256(connection, table: str) -> str:
    """Byte-level content digest of one table (every column, rowid order); used to prove upgrades leave 0001 rows identical."""
    rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    return semantic_sha256([[None if v is None else (v.hex() if isinstance(v, bytes) else v) for v in tuple(r)] for r in rows])


__all__ = ["semantic_manifest", "library_manifest", "full_manifest", "bridge_manifest", "BRIDGE_TABLES", "journal_manifest", "JOURNAL_TABLES",
           "eligibility_manifest", "ELIGIBILITY_TABLES", "parse_manifest", "PARSE_TABLES", "screening_manifest", "SCREENING_TABLES",
           "evidence_manifest", "EVIDENCE_TABLES", "retrieval_manifest", "RETRIEVAL_TABLES",
           "table_content_sha256", "canonical_json"]
