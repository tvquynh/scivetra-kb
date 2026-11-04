-- SciVetra KB migration 0011: S11 Scientific Synthesis and Citation Audit (ADR_S11). Purely additive: no 0001-0010 object is
-- altered and no existing row is read-modified. Every table is append-only. S11 reads S05 / S06 / S08 state through explicit
-- read-only interfaces and never writes an S05 ballot or lock, an S06 artifact, an S07 proposal, an S08 draft / review /
-- adjudication, an S09 index, an S10 owner record, a rights assessment or a Reprovane decision. Its outputs are research DRAFTS:
-- authority NON_AUTHORITATIVE, status DRAFT_NON_AUTHORITATIVE, publication PUBLICATION_NOT_AUTHORIZED (CHECKs below). External
-- sharing (gate A7 + owner gate OAG-7) is not representable: exports are LOCAL_REVIEW_ONLY and every share request is DENIED.

-- SynthesisScope: one review, pinned to ONE S08 evidence scope (one S05 FTE run, its Reprovane release import and protocol) and an
-- explicit, digest-bound subset of its members (paper, document version, asset, S06 artifact, link). Never a DOI inventory or a
-- metadata-only list. The S11 rubric hash binds the comparison and support rules used.
CREATE TABLE syn_scope (
  scope_id TEXT PRIMARY KEY NOT NULL CHECK (scope_id GLOB 'sys_*'),
  ev_scope_id TEXT NOT NULL REFERENCES ev_scope(scope_id),
  ev_scope_sha256 TEXT NOT NULL CHECK (length(ev_scope_sha256) = 64),
  fte_run_id TEXT NOT NULL REFERENCES fte_run(run_id),
  release_import_id TEXT NOT NULL REFERENCES reprovane_release_import(release_import_id),
  protocol_sha256 TEXT NOT NULL CHECK (length(protocol_sha256) = 64),
  profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
  rubric_sha256 TEXT NOT NULL CHECK (length(rubric_sha256) = 64),
  members_json TEXT NOT NULL CHECK (json_valid(members_json) AND json_type(members_json) = 'array'
                                    AND json_array_length(members_json) BETWEEN 1 AND 2000),
  membership_sha256 TEXT NOT NULL CHECK (length(membership_sha256) = 64),
  review_policy TEXT NOT NULL CHECK (review_policy IN ('DUAL_INDEPENDENT','SINGLE_HUMAN')),
  review_policy_reason TEXT,
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  output_status TEXT NOT NULL CHECK (output_status = 'DRAFT_NON_AUTHORITATIVE'),
  publication TEXT NOT NULL CHECK (publication = 'PUBLICATION_NOT_AUTHORIZED'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (review_policy = 'DUAL_INDEPENDENT' OR length(coalesce(review_policy_reason, '')) > 0)
);

-- ClaimUnit: one explicitly identified, versioned claim (claim_key + version). The statement is author-supplied (human text, gate
-- A4) or rendered from the typed spec by a deterministic template. The typed spec is what is checked; the statement may not say
-- more than the spec (checked at creation). A new version supersedes the previous one; nothing is overwritten.
CREATE TABLE syn_claim (
  claim_id TEXT PRIMARY KEY NOT NULL CHECK (claim_id GLOB 'syc_*'),
  scope_id TEXT NOT NULL REFERENCES syn_scope(scope_id),
  claim_key TEXT NOT NULL CHECK (length(claim_key) BETWEEN 1 AND 40 AND claim_key NOT GLOB '*[^A-Za-z0-9._-]*'),
  version INTEGER NOT NULL CHECK (version >= 1),
  supersedes_claim_id TEXT REFERENCES syn_claim(claim_id),
  claim_kind TEXT NOT NULL CHECK (claim_kind IN ('REPORTED_VALUE','COMPARISON','ABSENCE','THEMATIC','COUNT')),
  statement TEXT NOT NULL CHECK (length(statement) BETWEEN 1 AND 600),
  statement_origin TEXT NOT NULL CHECK (statement_origin IN ('AUTHOR_SUPPLIED','DETERMINISTIC_TEMPLATE')),
  spec_json TEXT NOT NULL CHECK (json_valid(spec_json) AND json_type(spec_json) = 'object'),
  spec_sha256 TEXT NOT NULL CHECK (length(spec_sha256) = 64),
  cited_draft_ids_json TEXT NOT NULL CHECK (json_valid(cited_draft_ids_json) AND json_type(cited_draft_ids_json) = 'array'
                                            AND json_array_length(cited_draft_ids_json) <= 20),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (scope_id, claim_key, version),
  CHECK ((version = 1) = (supersedes_claim_id IS NULL))
);

-- SynthesisEvidenceSnapshot: the evidence state of every pinned member's S08 drafts at one audit, re-derived NOW (S08 current
-- acceptance, latest verification and reviews, S05 / S06 currency, rights, CAS re-read and anchor re-check). References and hashes
-- only: no quote, no document text, no verbatim text field value (text dimensions are kept as SHA-256 of their normalized form).
CREATE TABLE syn_snapshot (
  snapshot_id TEXT PRIMARY KEY NOT NULL CHECK (snapshot_id GLOB 'syq_*'),
  scope_id TEXT NOT NULL REFERENCES syn_scope(scope_id),
  entries_json TEXT NOT NULL CHECK (json_valid(entries_json) AND json_type(entries_json) = 'array'),
  snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64),
  eligible_count INTEGER NOT NULL CHECK (eligible_count >= 0),
  excluded_json TEXT NOT NULL CHECK (json_valid(excluded_json) AND json_type(excluded_json) = 'object'),
  contains_quotes INTEGER NOT NULL CHECK (contains_quotes = 0),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- CitationAudit run: the claims (exact versions) audited against one snapshot with one rubric and engine version. The report is
-- deterministic (no timestamps or row ids); its SHA-256 is what human reviews and exports bind to.
CREATE TABLE syn_audit_run (
  audit_id TEXT PRIMARY KEY NOT NULL CHECK (audit_id GLOB 'sya_*'),
  scope_id TEXT NOT NULL REFERENCES syn_scope(scope_id),
  snapshot_id TEXT NOT NULL REFERENCES syn_snapshot(snapshot_id),
  claim_ids_json TEXT NOT NULL CHECK (json_valid(claim_ids_json) AND json_type(claim_ids_json) = 'array'),
  claim_set_sha256 TEXT NOT NULL CHECK (length(claim_set_sha256) = 64),
  rubric_sha256 TEXT NOT NULL CHECK (length(rubric_sha256) = 64),
  engine TEXT NOT NULL CHECK (engine = 'scivetra-synthesis-engine/1'),
  report_sha256 TEXT NOT NULL CHECK (length(report_sha256) = 64),
  counts_json TEXT NOT NULL CHECK (json_valid(counts_json) AND json_type(counts_json) = 'object'),
  traceability TEXT NOT NULL CHECK (traceability IN ('COMPLETE')),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- ClaimEvidenceLink set and verdict of one claim in one audit run. A claim that is not SUPPORTED always carries a visible marker.
CREATE TABLE syn_claim_audit (
  row_id TEXT PRIMARY KEY NOT NULL CHECK (row_id GLOB 'syl_*'),
  audit_id TEXT NOT NULL REFERENCES syn_audit_run(audit_id),
  claim_id TEXT NOT NULL REFERENCES syn_claim(claim_id),
  verdict TEXT NOT NULL CHECK (verdict IN ('SUPPORTED','PARTIAL','UNSUPPORTED','MISSING_EVIDENCE','STALE_SOURCE','BLOCKED_RIGHTS','CONFLICTING')),
  marker TEXT NOT NULL CHECK (marker IN ('CITED','[UNVERIFIED]','[CONFLICTED]')),
  reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json) AND json_type(reasons_json) = 'array'),
  links_json TEXT NOT NULL CHECK (json_valid(links_json) AND json_type(links_json) = 'array'),
  comparability_json TEXT NOT NULL CHECK (json_valid(comparability_json)),
  derivation_json TEXT NOT NULL CHECK (json_valid(derivation_json)),
  link_graph_sha256 TEXT NOT NULL CHECK (length(link_graph_sha256) = 64),
  UNIQUE (audit_id, claim_id),
  CHECK ((verdict = 'SUPPORTED') = (marker = 'CITED')),
  CHECK ((verdict = 'CONFLICTING') = (marker = '[CONFLICTED]'))
);

-- ContradictionRecord: conflicts found in one audit run. Both sides are kept; nothing is resolved by vote. A numerical difference
-- between independent studies is NOT a contradiction (it is a comparison-group annotation, not stored here).
CREATE TABLE syn_contradiction (
  contradiction_id TEXT PRIMARY KEY NOT NULL CHECK (contradiction_id GLOB 'syx_*'),
  audit_id TEXT NOT NULL REFERENCES syn_audit_run(audit_id),
  kind TEXT NOT NULL CHECK (kind IN ('SAME_PAPER_SAME_KEY_DIFFERENT_VALUE','DOCUMENT_VERSION_VALUE_MISMATCH','S08_VALUE_STATUS_CONFLICTED',
                                     'CLAIM_CONTRADICTED_BY_EVIDENCE','PAPER_IDENTITY_CONFLICT')),
  key_sha256 TEXT NOT NULL CHECK (length(key_sha256) = 64),
  draft_ids_json TEXT NOT NULL CHECK (json_valid(draft_ids_json) AND json_type(draft_ids_json) = 'array'),
  claim_ids_json TEXT NOT NULL CHECK (json_valid(claim_ids_json) AND json_type(claim_ids_json) = 'array'),
  detail_json TEXT NOT NULL CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object')
);

-- HumanSynthesisReview: append-only votes of owner-registered HUMAN reviewers (S07 roster), separate from S05 / S07 / S08 votes.
-- DRAFT reviews bind the audit report SHA-256; CLAIM reviews confirm or dispute the support of one claim for its exact link graph.
-- The current review state is derived at read time; a review of an audit that no longer reproduces is STALE.
CREATE TABLE syn_review (
  review_id TEXT PRIMARY KEY NOT NULL CHECK (review_id GLOB 'syr_*'),
  scope_id TEXT NOT NULL REFERENCES syn_scope(scope_id),
  audit_id TEXT NOT NULL REFERENCES syn_audit_run(audit_id),
  report_sha256 TEXT NOT NULL CHECK (length(report_sha256) = 64),
  target TEXT NOT NULL CHECK (target IN ('DRAFT','CLAIM')),
  claim_id TEXT REFERENCES syn_claim(claim_id),
  link_graph_sha256 TEXT CHECK (link_graph_sha256 IS NULL OR length(link_graph_sha256) = 64),
  reviewer_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  decision TEXT NOT NULL CHECK (decision IN ('ATTEST_DRAFT_ACCURATE','REQUEST_CHANGES','REJECT_DRAFT','CONFIRM_SUPPORT','DISPUTE_SUPPORT')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  rationale TEXT CHECK (rationale IS NULL OR length(rationale) BETWEEN 1 AND 2000),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK ((target = 'CLAIM') = (claim_id IS NOT NULL AND link_graph_sha256 IS NOT NULL)),
  CHECK ((target = 'DRAFT') = (decision IN ('ATTEST_DRAFT_ACCURATE','REQUEST_CHANGES','REJECT_DRAFT'))),
  CHECK (decision NOT IN ('REQUEST_CHANGES','REJECT_DRAFT','DISPUTE_SUPPORT') OR json_array_length(reason_codes_json) >= 1)
);

-- SynthesisExport lifecycle (as S08 ev_export_event): RESERVED is committed before any file can become visible; exactly one
-- terminal event (PUBLISHED / ABANDONED) cites it. Destination is LOCAL_REVIEW_ONLY; the files never contain quotes.
CREATE TABLE syn_export_event (
  export_id TEXT PRIMARY KEY NOT NULL CHECK (export_id GLOB 'sye_*'),
  scope_id TEXT NOT NULL REFERENCES syn_scope(scope_id),
  audit_id TEXT NOT NULL REFERENCES syn_audit_run(audit_id),
  phase TEXT NOT NULL CHECK (phase IN ('RESERVED','PUBLISHED','ABANDONED')),
  reservation_id TEXT REFERENCES syn_export_event(export_id),
  outcome_code TEXT CHECK (outcome_code IS NULL OR length(outcome_code) BETWEEN 1 AND 80),
  destination TEXT NOT NULL CHECK (destination = 'LOCAL_REVIEW_ONLY'),
  bundle_sha256 TEXT NOT NULL CHECK (length(bundle_sha256) = 64),
  files_json TEXT NOT NULL CHECK (json_valid(files_json) AND json_type(files_json) = 'object'),
  contains_quotes INTEGER NOT NULL CHECK (contains_quotes = 0),
  target_path_hash TEXT NOT NULL CHECK (length(target_path_hash) = 64),
  folder_name TEXT NOT NULL CHECK (length(folder_name) BETWEEN 1 AND 120),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK ((phase = 'RESERVED') = (reservation_id IS NULL)),
  CHECK (phase <> 'RESERVED' OR outcome_code IS NULL)
);

CREATE TRIGGER syn_export_event_terminal_matches_reservation
BEFORE INSERT ON syn_export_event
WHEN NEW.phase <> 'RESERVED' AND NOT EXISTS (
  SELECT 1 FROM syn_export_event r WHERE r.export_id = NEW.reservation_id AND r.phase = 'RESERVED' AND r.scope_id = NEW.scope_id
    AND r.audit_id = NEW.audit_id AND r.bundle_sha256 = NEW.bundle_sha256 AND r.files_json = NEW.files_json
    AND r.target_path_hash = NEW.target_path_hash AND r.folder_name = NEW.folder_name)
BEGIN
  SELECT RAISE(ABORT, 'syn_export_event: terminal event must match its reservation');
END;

CREATE TRIGGER syn_export_event_single_terminal
BEFORE INSERT ON syn_export_event
WHEN NEW.phase <> 'RESERVED' AND EXISTS (SELECT 1 FROM syn_export_event t WHERE t.reservation_id = NEW.reservation_id)
BEGIN
  SELECT RAISE(ABORT, 'syn_export_event: a reservation has exactly one terminal event');
END;

-- External sharing request (A7 + OAG-7): recorded and always DENIED in S11 (no owner sharing policy exists).
CREATE TABLE syn_share_request (
  request_id TEXT PRIMARY KEY NOT NULL CHECK (request_id GLOB 'syh_*'),
  scope_id TEXT NOT NULL REFERENCES syn_scope(scope_id),
  destination_class TEXT NOT NULL CHECK (length(destination_class) BETWEEN 1 AND 60),
  decision TEXT NOT NULL CHECK (decision = 'DENIED'),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'
                                         AND json_array_length(reason_codes_json) >= 1),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- A claim review must cite a claim of the audited claim set; a draft review must not name a claim.
CREATE TRIGGER syn_review_claim_in_audit
BEFORE INSERT ON syn_review
WHEN NEW.target = 'CLAIM' AND NOT EXISTS (
  SELECT 1 FROM syn_claim_audit c WHERE c.audit_id = NEW.audit_id AND c.claim_id = NEW.claim_id AND c.link_graph_sha256 = NEW.link_graph_sha256)
BEGIN
  SELECT RAISE(ABORT, 'syn_review: the claim and link graph must belong to the audit run');
END;

CREATE TRIGGER syn_review_report_matches_audit
BEFORE INSERT ON syn_review
WHEN NOT EXISTS (SELECT 1 FROM syn_audit_run a WHERE a.audit_id = NEW.audit_id AND a.scope_id = NEW.scope_id AND a.report_sha256 = NEW.report_sha256)
BEGIN
  SELECT RAISE(ABORT, 'syn_review: report SHA-256 must be the audit run''s');
END;

-- Immutability: every S11 table is append-only.
CREATE TRIGGER syn_scope_no_update BEFORE UPDATE ON syn_scope BEGIN SELECT RAISE(ABORT, 'immutable: syn_scope'); END;
CREATE TRIGGER syn_scope_no_delete BEFORE DELETE ON syn_scope BEGIN SELECT RAISE(ABORT, 'immutable: syn_scope'); END;
CREATE TRIGGER syn_claim_no_update BEFORE UPDATE ON syn_claim BEGIN SELECT RAISE(ABORT, 'immutable: syn_claim'); END;
CREATE TRIGGER syn_claim_no_delete BEFORE DELETE ON syn_claim BEGIN SELECT RAISE(ABORT, 'immutable: syn_claim'); END;
CREATE TRIGGER syn_snapshot_no_update BEFORE UPDATE ON syn_snapshot BEGIN SELECT RAISE(ABORT, 'immutable: syn_snapshot'); END;
CREATE TRIGGER syn_snapshot_no_delete BEFORE DELETE ON syn_snapshot BEGIN SELECT RAISE(ABORT, 'immutable: syn_snapshot'); END;
CREATE TRIGGER syn_audit_run_no_update BEFORE UPDATE ON syn_audit_run BEGIN SELECT RAISE(ABORT, 'immutable: syn_audit_run'); END;
CREATE TRIGGER syn_audit_run_no_delete BEFORE DELETE ON syn_audit_run BEGIN SELECT RAISE(ABORT, 'immutable: syn_audit_run'); END;
CREATE TRIGGER syn_claim_audit_no_update BEFORE UPDATE ON syn_claim_audit BEGIN SELECT RAISE(ABORT, 'immutable: syn_claim_audit'); END;
CREATE TRIGGER syn_claim_audit_no_delete BEFORE DELETE ON syn_claim_audit BEGIN SELECT RAISE(ABORT, 'immutable: syn_claim_audit'); END;
CREATE TRIGGER syn_contradiction_no_update BEFORE UPDATE ON syn_contradiction BEGIN SELECT RAISE(ABORT, 'immutable: syn_contradiction'); END;
CREATE TRIGGER syn_contradiction_no_delete BEFORE DELETE ON syn_contradiction BEGIN SELECT RAISE(ABORT, 'immutable: syn_contradiction'); END;
CREATE TRIGGER syn_review_no_update BEFORE UPDATE ON syn_review BEGIN SELECT RAISE(ABORT, 'immutable: syn_review'); END;
CREATE TRIGGER syn_review_no_delete BEFORE DELETE ON syn_review BEGIN SELECT RAISE(ABORT, 'immutable: syn_review'); END;
CREATE TRIGGER syn_export_event_no_update BEFORE UPDATE ON syn_export_event BEGIN SELECT RAISE(ABORT, 'immutable: syn_export_event'); END;
CREATE TRIGGER syn_export_event_no_delete BEFORE DELETE ON syn_export_event BEGIN SELECT RAISE(ABORT, 'immutable: syn_export_event'); END;
CREATE TRIGGER syn_share_request_no_update BEFORE UPDATE ON syn_share_request BEGIN SELECT RAISE(ABORT, 'immutable: syn_share_request'); END;
CREATE TRIGGER syn_share_request_no_delete BEFORE DELETE ON syn_share_request BEGIN SELECT RAISE(ABORT, 'immutable: syn_share_request'); END;
