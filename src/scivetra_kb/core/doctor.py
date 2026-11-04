"""``rkb doctor``: read-only inspection. Reports problems; never repairs, migrates or creates files."""
from __future__ import annotations

import json
import os
import sqlite3
import sys

from .. import __version__
from .canonical import sha256_bytes
from .cas import reference_problems, referenced_digests
from .db import DB_RELATIVE, open_reader
from .errors import KBError
from .manifest import (BRIDGE_TABLES, ELIGIBILITY_TABLES, EVIDENCE_TABLES, RETRIEVAL_TABLES, evidence_manifest, retrieval_manifest, JOURNAL_TABLES, PARSE_TABLES, SCREENING_TABLES, LIBRARY_TABLES, bridge_manifest, eligibility_manifest, full_manifest, parse_manifest, screening_manifest,
                       journal_manifest, library_manifest, semantic_manifest)
from .paths import safe_data_root
from ..rights import policy as rights_policy


# Library invariants re-checked by ``doctor --full`` (each query returns offending rows; expected empty).
LIBRARY_INVARIANTS = {
    # IA-1: one normalized DOI may map to several papers only while every mapping is CONFLICTING.
    "IA1_DOI_MAPPED_TO_SEVERAL_PAPERS_ACTIVE":
        "SELECT value_normalized FROM identity_assertion_effective WHERE namespace='doi' AND value_normalized IS NOT NULL "
        "AND effective_status='ACTIVE' GROUP BY value_normalized HAVING count(DISTINCT paper_id) > 1",
    # PAL-1 / N04: the same bytes never carry two independent automatic main-text matches.
    "PAL1_SAME_BYTES_TWO_AUTO_MATCHES":
        "SELECT asset_sha256 FROM paper_asset_link_effective WHERE effective_role='MAIN_TEXT' AND effective_state='AUTO_EVIDENCE_MATCH' "
        "GROUP BY asset_sha256 HAVING count(DISTINCT paper_id) > 1",
    # PPR-2: automatic links are keyed only by manifestation-level identifiers.
    "PPR2_AUTO_LINK_NOT_MLI":
        "SELECT reference_id FROM project_paper_reference WHERE link_basis='EXACT_MANIFESTATION_ID' "
        "AND json_extract(link_key_json,'$.namespace') NOT IN ('doi','pmid','pmcid','arxiv')",
    # Rights default deny (05 4.2/4.3).
    "RIGHTS_EXTERNAL_AI_WITHOUT_OWNER_APPROVAL":
        "SELECT assessment_id FROM rights_assessment WHERE external_ai_process='ALLOW' AND coalesce(owner_approval_ref,'')=''",
}


# S04 bridge invariants (schema >= 3), re-checked by ``doctor --full``; each query returns offending rows.
BRIDGE_INVARIANTS = {
    # A verified bridge file is linked to the exported paper only (also enforced by trigger).
    "BRIDGE_LINK_NOT_EXPORTED_PAPER":
        "SELECT e.event_id FROM bridge_file_event e JOIN bridge_row r USING (bridge_row_id) JOIN paper_asset_link l ON l.link_id = e.link_id "
        "WHERE l.paper_id <> r.paper_id",
    # A bridge-created link never starts as an automatic identity match (require_human_identity, S04-D06).
    "BRIDGE_LINK_AUTO_MATCHED":
        "SELECT e.event_id FROM bridge_file_event e JOIN paper_asset_link l ON l.link_id = e.link_id WHERE l.identity_state = 'AUTO_EVIDENCE_MATCH'",
    # Every row has a status history starting with EXPORTED.
    "BRIDGE_ROW_WITHOUT_EXPORT_EVENT":
        "SELECT r.bridge_row_id FROM bridge_row r WHERE NOT EXISTS (SELECT 1 FROM bridge_row_event e WHERE e.bridge_row_id = r.bridge_row_id "
        "AND e.new_status = 'EXPORTED')",
    # A row counted VERIFIED_PDF has a verified file event.
    "BRIDGE_VERIFIED_WITHOUT_FILE_EVENT":
        "SELECT e.bridge_row_id FROM bridge_row_event e WHERE e.new_status = 'VERIFIED_PDF' AND NOT EXISTS (SELECT 1 FROM bridge_file_event f "
        "WHERE f.bridge_row_id = e.bridge_row_id AND f.outcome IN ('VERIFIED_PDF','DUPLICATE_NOOP','NEW_BYTES_SAME_ROW'))",
}


# S04J journal invariants (schema >= 4), re-checked by ``doctor --full``; each query returns offending rows.
JOURNAL_INVARIANTS = {
    # A run is COMPLETE only if no page of it failed after its last replay start and it has at least one OK works page.
    "JH_COMPLETE_RUN_WITHOUT_OK_PAGE":
        "SELECT e.run_id FROM jh_run_event e JOIN jh_run r ON r.run_id = e.run_id WHERE e.kind='FINISHED' AND e.state='COMPLETE' "
        "AND r.mode <> 'RESOLVE' AND NOT EXISTS (SELECT 1 FROM jh_page p WHERE p.run_id = e.run_id AND p.kind='WORKS_PAGE' AND p.outcome='OK')",
    # Membership is asserted only on venue evidence (alternate-location-only subjects are never members).
    "JH_ALTERNATE_ONLY_MEMBER_ASSERTED":
        "SELECT member_id FROM jh_member WHERE category IN ('OPENALEX_ALTERNATE_LOCATION_ONLY','NOT_SEEN_IN_LATEST_RUNS') AND membership_asserted = 1",
    # A collection is never stated complete while a basis provider run is not COMPLETE.
    "JH_COMPLETE_VERSION_WITH_INCOMPLETE_BASIS":
        "SELECT version_id FROM jh_collection_version WHERE collection_state='COMPLETE_RELATIVE_TO_RECORDED_QUERIES' AND "
        "(json_extract(basis_json,'$.CROSSREF.state') <> 'COMPLETE' OR json_extract(basis_json,'$.OPENALEX.state') <> 'COMPLETE')",
    # No stored request identity or query carries a credential or contact address.
    "JH_SECRET_SHAPED_QUERY":
        "SELECT page_id FROM jh_page WHERE instr(request_identity_json,'@') OR instr(lower(request_identity_json),'api_key') "
        "OR instr(lower(request_identity_json),'mailto')",
}


# S05 eligibility invariants (schema >= 5), re-checked by ``doctor --full``; each query returns offending rows.
ELIGIBILITY_INVARIANTS = {
    # Exactly one candidate per frozen release item.
    "FTE_CANDIDATE_COUNT_DIFFERS_FROM_RELEASE":
        "SELECT r.run_id FROM fte_run r WHERE r.item_count <> (SELECT count(*) FROM fte_candidate c WHERE c.run_id = r.run_id)",
    # Every decision is bound to its run's frozen protocol and its candidate's frozen item hash.
    "FTE_DECISION_NOT_BOUND_TO_FROZEN_INPUTS":
        "SELECT d.decision_id FROM fte_decision d JOIN fte_candidate c USING (candidate_id) JOIN fte_run r ON r.run_id = c.run_id "
        "WHERE d.protocol_sha256 <> r.protocol_sha256 OR d.item_sha256 <> c.item_sha256",
    # KB evidence always points to the candidate's own paper.
    "FTE_EVIDENCE_OTHER_PAPER":
        "SELECT e.evidence_id FROM fte_evidence e JOIN fte_candidate c USING (candidate_id) JOIN paper_asset_link l ON l.link_id = e.link_id "
        "WHERE l.paper_id <> c.paper_id",
    # An adjudication only resolves decisions of its own candidate.
    "FTE_ADJUDICATION_FOREIGN_DECISION":
        "SELECT a.adjudication_id FROM fte_adjudication a, json_each(a.resolved_decision_ids_json) j "
        "WHERE NOT EXISTS (SELECT 1 FROM fte_decision d WHERE d.decision_id = j.value AND d.candidate_id = a.candidate_id)",
    # A lock exists only with a LOCKED_FOR_HANDOFF event as the last event.
    "FTE_LOCK_WITHOUT_LOCK_EVENT":
        "SELECT l.lock_id FROM fte_lock l WHERE (SELECT new_state FROM fte_run_event e WHERE e.run_id = l.run_id ORDER BY seq DESC LIMIT 1) "
        "<> 'LOCKED_FOR_HANDOFF'",
    # Authority is pinned.
    "FTE_AUTHORITY_NOT_NON_AUTHORITATIVE":
        "SELECT run_id FROM fte_run WHERE authority <> 'NON_AUTHORITATIVE' UNION SELECT lock_id FROM fte_lock WHERE authority <> 'NON_AUTHORITATIVE'",
}


# S06 parse invariants (schema >= 6), re-checked by ``doctor --full``; each query returns offending rows.
PARSE_INVARIANTS = {
    # A producing run cites an artifact of its own asset and a rights assessment for that asset.
    "PARSE_RUN_ARTIFACT_OTHER_ASSET":
        "SELECT r.run_id FROM parse_run r JOIN parse_artifact a ON a.artifact_id = r.artifact_id WHERE a.asset_sha256 <> r.requested_asset_sha256",
    "PARSE_RUN_RIGHTS_OTHER_ASSET":
        "SELECT r.run_id FROM parse_run r JOIN rights_assessment x ON x.assessment_id = r.rights_assessment_id "
        "WHERE r.outcome IN ('PRODUCED','REUSED') AND (x.asset_sha256 <> r.requested_asset_sha256 OR x.local_process <> 'ALLOW')",
    # Every artifact was produced by exactly one PRODUCED run.
    "PARSE_ARTIFACT_WITHOUT_PRODUCING_RUN":
        "SELECT a.artifact_id FROM parse_artifact a WHERE (SELECT count(*) FROM parse_run r WHERE r.artifact_id = a.artifact_id AND r.outcome = 'PRODUCED') <> 1",
    # Identity-inspection output is never evidence-eligible.
    "PARSE_INSPECTION_MARKED_ELIGIBLE":
        "SELECT run_id FROM parse_run WHERE purpose = 'IDENTITY_INSPECTION' AND evidence_eligibility <> 'NOT_ELIGIBLE_FOR_EVIDENCE'",
}


# S07 screening invariants (schema >= 7), re-checked by ``doctor --full``; each query returns offending rows.
SCREENING_INVARIANTS = {
    # An approval references a proposal of a scope whose authority is pinned and a HUMAN roster reviewer.
    "AI_APPROVAL_NON_HUMAN": "SELECT a.approval_id FROM ai_approval a JOIN ai_reviewer v USING (reviewer_id) WHERE v.actor_type <> 'HUMAN'",
    # Proposals are never authoritative and always gated for humans.
    "AI_PROPOSAL_AUTHORITY": "SELECT proposal_id FROM ai_proposal WHERE authority <> 'NON_AUTHORITATIVE' OR review_gate <> 'HUMAN_REQUIRED'",
    # Blind-first: every reveal in a blind-first scope follows an initial judgement by the same reviewer.
    "AI_REVEAL_BEFORE_JUDGEMENT": "SELECT r.reveal_id FROM ai_proposal_reveal r JOIN ai_processing_scope s USING (scope_id) WHERE s.blind_first = 1 AND NOT EXISTS "
                                  "(SELECT 1 FROM ai_human_judgement j WHERE j.scope_id = r.scope_id AND j.paper_id = r.paper_id AND j.reviewer_id = r.reviewer_id "
                                  "AND j.rowid < (SELECT rowid FROM ai_proposal_reveal WHERE reveal_id = r.reveal_id))",
    # Remote / desktop model locations never appear.
    "AI_NON_LOCAL_MODEL": "SELECT proposal_id FROM ai_proposal WHERE model_location <> 'LOCAL_PROCESS' UNION SELECT scope_id FROM ai_processing_scope "
                          "WHERE model_location_allowed <> 'LOCAL_PROCESS'",
}


# S08 evidence invariants (schema >= 8), re-checked by ``doctor --full``; each query returns offending rows.
EVIDENCE_INVARIANTS = {
    # Drafts are never authoritative and always gated for humans; only S06 text-layer evidence exists.
    "EV_DRAFT_AUTHORITY": "SELECT draft_id FROM ev_draft WHERE authority <> 'NON_AUTHORITATIVE' OR review_gate <> 'HUMAN_REQUIRED' OR source_kind <> 'FULLTEXT_TEXT'",
    # Reviews and adjudications come from the HUMAN roster.
    "EV_REVIEW_NON_HUMAN": "SELECT w.review_id FROM ev_review w JOIN ai_reviewer v USING (reviewer_id) WHERE v.actor_type <> 'HUMAN' UNION "
                           "SELECT a.adjudication_id FROM ev_adjudication a JOIN ai_reviewer v ON v.reviewer_id = a.adjudicator_id WHERE v.actor_type <> 'HUMAN'",
    # No reviewer reviewed a draft they created or imported.
    "EV_SELF_REVIEW": "SELECT w.review_id FROM ev_review w JOIN ai_reviewer v USING (reviewer_id) JOIN ev_draft d USING (draft_id) "
                      "LEFT JOIN ev_extraction_run r ON r.run_id = d.run_id WHERE d.created_by = 'OPERATOR:' || v.label OR r.created_by = 'OPERATOR:' || v.label",
    # Provisional evidence is never accepted.
    "EV_PROVISIONAL_ACCEPTED": "SELECT w.review_id FROM ev_review w JOIN ev_draft d USING (draft_id) WHERE w.decision = 'ACCEPT' "
                               "AND d.member_class = 'PROVISIONAL_NON_AUTHORITATIVE' UNION SELECT a.adjudication_id FROM ev_adjudication a "
                               "JOIN ev_draft d USING (draft_id) WHERE a.outcome = 'ACCEPT' AND d.member_class = 'PROVISIONAL_NON_AUTHORITATIVE'",
    # Verification never claims semantic entailment; remote / desktop model locations never appear.
    "EV_ENTAILMENT_OR_REMOTE": "SELECT verification_id FROM ev_verification WHERE semantic_entailment <> 'NOT_ASSESSED' UNION SELECT run_id FROM "
                               "ev_extraction_run WHERE model_location <> 'LOCAL_PROCESS' UNION SELECT scope_id FROM ev_scope WHERE model_location_allowed <> 'LOCAL_PROCESS'",
    # Exports never carry quotes.
    "EV_EXPORT_WITH_QUOTES": "SELECT export_id FROM ev_export_event WHERE contains_quotes <> 0",
    # Every export outcome closes exactly one matching reservation (the reservation is recorded before publication).
    "EV_EXPORT_OUTCOME_WITHOUT_RESERVATION": "SELECT t.export_id FROM ev_export_event t WHERE t.phase <> 'RESERVED' AND NOT EXISTS (SELECT 1 FROM "
                                             "ev_export_event r WHERE r.export_id = t.reservation_id AND r.phase = 'RESERVED' AND "
                                             "r.report_sha256 = t.report_sha256 AND r.folder_name = t.folder_name AND r.target_path_hash = t.target_path_hash)",
    # Verbatim text values are stored only together with quotes stored under gate A3; a withheld text keeps no value.
    "EV_TEXT_STORED_WITHOUT_GATE_A3": "SELECT draft_id FROM ev_draft WHERE (text_storage = 'STORED_GATE_A3' AND quote_storage <> 'STORED_GATE_A3') "
                                      "OR (text_storage = 'WITHHELD_GATE_A3_DENIED' AND quote_text_json IS NOT NULL)",
}


# S09 retrieval invariants (schema >= 9), re-checked by ``doctor --full``; each query returns offending rows.
RETRIEVAL_INVARIANTS = {
    # Only the local CLI consumer exists in S09 (desktop / MCP / remote need A8 and OAG-6 in S10).
    "RT_NON_LOCAL_CONSUMER": "SELECT event_id FROM rt_access_event WHERE consumer <> 'LOCAL_CLI' UNION SELECT scope_id FROM rt_scope "
                             "WHERE consumer_allowed <> 'LOCAL_CLI'",
    # Retrieval scopes are never authoritative.
    "RT_SCOPE_AUTHORITY": "SELECT scope_id FROM rt_scope WHERE authority <> 'NON_AUTHORITATIVE'",
    # An access event cites a recorded index build (or none for direct reads that failed before opening an index).
    "RT_EVENT_UNKNOWN_BUILD": "SELECT event_id FROM rt_access_event WHERE index_build_id IS NOT NULL AND index_build_id NOT IN "
                              "(SELECT build_id FROM rt_index_build)",
}


def rights_reassessment_due(kb) -> list[str]:
    """Current PUBLIC_OA_LICENSED assessments whose stored licence evidence the current licence-URI rules
    (``rights.policy.LICENCE_URI_RULES``) no longer recognize for that version (e.g. written by the S03 v01 prefix rule).
    Read-only: history is never changed; the operator re-runs ``rkb rights assess``, which appends a superseding row."""
    current = {}
    for row in kb.connection.execute("SELECT r.assessment_id, r.asset_sha256, r.document_version_id, r.rights_class, r.license_evidence_json, "
                                     "v.version_class FROM rights_assessment r JOIN document_version v USING (document_version_id) ORDER BY r.rowid"):
        current[(row[1], row[2])] = row
    due = []
    for assessment_id, _, _, rights_class, evidence_json, version_class in current.values():
        if rights_class != "PUBLIC_OA_LICENSED":
            continue
        applicable = [i for i in json.loads(evidence_json) if version_class in rights_policy.CONTENT_VERSION_CLASS.get(i.get("content_version"), ())]
        if not applicable or any(rights_policy.licence_family(i.get("license_uri")) is None for i in applicable):
            due.append(assessment_id)
    return sorted(due)


def doctor(data_root_text: str, *, full: bool = False) -> dict:
    """Read-only inspection; ``full`` re-hashes every CAS object and re-checks the library invariants."""
    report = {"schema": "rkb-doctor/1", "client_version": __version__, "python": sys.version.split()[0],
              "sqlite": sqlite3.sqlite_version, "problems": [], "warnings": [], "full": full}
    root = safe_data_root(data_root_text, must_exist=True)
    report["data_root_exists"] = True
    report["database_present"] = (root / DB_RELATIVE).is_file()
    if not report["database_present"]:
        report["problems"].append("KB_NOT_INITIALIZED")
        report["ok"] = False
        return report
    try:
        kb_ctx = open_reader(data_root_text)
        kb = kb_ctx.__enter__()
    except KBError as exc:
        report["problems"].append(exc.code)
        report["ok"] = False
        return report
    try:
        report["schema_status"] = kb.schema
        report["integrity_check"] = kb.connection.execute("PRAGMA integrity_check").fetchone()[0]
        if report["integrity_check"] != "ok":
            report["problems"].append("SQLITE_INTEGRITY_CHECK_FAILED")
        fk = kb.connection.execute("PRAGMA foreign_key_check").fetchall()
        report["foreign_key_violations"] = len(fk)
        if fk:
            report["problems"].append("FOREIGN_KEY_VIOLATIONS")
        tables = [r[0] for r in kb.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        triggers = {r[0] for r in kb.connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        missing = [t for t in tables if f"{t}_no_update" not in triggers or f"{t}_no_delete" not in triggers]
        report["immutable_tables"] = len(tables) - len(missing)
        if missing:
            report["problems"].append("IMMUTABILITY_TRIGGERS_MISSING:" + ",".join(sorted(missing)))
        try:
            referenced = set(referenced_digests(kb.connection, kb.schema["schema_version"]))
        except KBError as exc:
            referenced = set()
            report["problems"].append(exc.code)
        for code, digest in reference_problems(kb.cas.root, referenced):
            report["problems"].append(f"{code}:{digest}")
        on_disk, odd = set(), []
        for path, state in kb.cas.iter_objects() or []:
            if state == "OK":
                on_disk.add(path.name)
            else:
                odd.append(f"{state}:{path.name}")
        report["cas_objects"] = len(on_disk)
        report["cas_referenced"] = len(referenced)
        unreferenced = sorted(on_disk - referenced)
        if unreferenced:
            report["warnings"].append(f"UNREFERENCED_OBJECTS:{len(unreferenced)}")
        report["warnings"].extend(odd)
        if full:
            for digest in unreferenced:
                try:
                    kb.cas.verify_object(digest)
                except KBError as exc:
                    report["problems"].append(f"{exc.code}:{digest}")
            report["unreferenced_objects"] = unreferenced
            violations = {}
            for code, query in LIBRARY_INVARIANTS.items():
                rows = [r[0] for r in kb.connection.execute(query)]
                violations[code] = len(rows)
                if rows:
                    report["problems"].append(f"{code}:{len(rows)}")
            report["library_invariants"] = violations
            due = rights_reassessment_due(kb)
            report["rights_reassessment_due"] = due
            if due:
                report["warnings"].append(f"RIGHTS_REASSESSMENT_DUE:{len(due)}")
            report["library_manifest"] = library_manifest(kb)
            report["semantic_manifest_v2"] = full_manifest(kb)
        counts = {}
        for table in ("provider_snapshot", "metadata_claim", "integrity_event", "integrity_coverage", "integrity_counter_evidence",
                      "integrity_review", "review_task", "global_paper", "identity_assertion", "normalization_divergence"):
            counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in LIBRARY_TABLES + ("import_event", "asset_validation_event", "kb_upgrade_event"):
            counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if kb.schema["schema_version"] >= 3:
            for table in BRIDGE_TABLES + ("bridge_result_import",):
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                bridge_violations = {}
                for code, query in BRIDGE_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    bridge_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["bridge_invariants"] = bridge_violations
                report["bridge_manifest"] = bridge_manifest(kb)
        if kb.schema["schema_version"] >= 4:
            for table in JOURNAL_TABLES + ("jh_journal", "jh_run", "jh_run_event", "jh_page", "jh_field_claim", "jh_member", "jh_conflict", "jh_export"):
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                journal_violations = {}
                for code, query in JOURNAL_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    journal_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["journal_invariants"] = journal_violations
                report["journal_manifest"] = journal_manifest(kb)
        if kb.schema["schema_version"] >= 5:
            for table in ELIGIBILITY_TABLES:
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                eligibility_violations = {}
                for code, query in ELIGIBILITY_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    eligibility_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["eligibility_invariants"] = eligibility_violations
                report["eligibility_manifest"] = eligibility_manifest(kb)
        if kb.schema["schema_version"] >= 6:
            for table in PARSE_TABLES:
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                parse_violations = {}
                for code, query in PARSE_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    parse_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["parse_invariants"] = parse_violations
                open_exports = kb.connection.execute(
                    "SELECT count(*) FROM parse_export_event a WHERE a.state = 'AUTHORIZED' AND NOT EXISTS "
                    "(SELECT 1 FROM parse_export_event t WHERE t.authorization_event_id = a.event_id)").fetchone()[0]
                report["parse_exports_recovery_required"] = open_exports
                if open_exports:
                    report["warnings"].append(f"PARSE_EXPORT_RECOVERY_REQUIRED:{open_exports}")
                report["parse_manifest"] = parse_manifest(kb)
        if kb.schema["schema_version"] >= 7:
            for table in SCREENING_TABLES:
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                screening_violations = {}
                for code, query in SCREENING_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    screening_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["screening_invariants"] = screening_violations
                report["screening_manifest"] = screening_manifest(kb)
        if kb.schema["schema_version"] >= 8:
            for table in EVIDENCE_TABLES:
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                evidence_violations = {}
                for code, query in EVIDENCE_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    evidence_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["evidence_invariants"] = evidence_violations
                report["evidence_manifest"] = evidence_manifest(kb)
        if kb.schema["schema_version"] >= 9:
            for table in RETRIEVAL_TABLES:
                counts[table] = kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if full:
                retrieval_violations = {}
                for code, query in RETRIEVAL_INVARIANTS.items():
                    found = [r[0] for r in kb.connection.execute(query)]
                    retrieval_violations[code] = len(found)
                    if found:
                        report["problems"].append(f"{code}:{len(found)}")
                report["retrieval_invariants"] = retrieval_violations
                report["retrieval_manifest"] = retrieval_manifest(kb)
        report["counts"] = counts
        report["semantic_manifest"] = semantic_manifest(kb)
        report["writable"] = os.access(root, os.W_OK)
    finally:
        kb_ctx.__exit__(None, None, None)
    report["ok"] = not report["problems"]
    return report
