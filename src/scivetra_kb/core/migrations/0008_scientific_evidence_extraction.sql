-- SciVetra KB migration 0008: S08 scientific evidence extraction (S01 v03 08 section 6; ADR_S08). Purely additive: no 0001-0007
-- object is altered. Every table is append-only (no UPDATE / DELETE); current states are derived at read time.
-- Authority boundary: an evidence draft is never a screening decision and never accepted evidence by itself. Nothing here
-- writes, or can be joined into, an S05 ballot / adjudication, an S07 proposal or approval, a Reprovane decision or a corpus
-- membership. Human review uses the owner-registered HUMAN roster of S07 (ai_reviewer); self-review is refused by trigger.
-- Model location is LOCAL_PROCESS only. Only S06 text-layer evidence (FULLTEXT_TEXT) exists: no table cells, figure geometry or OCR.

-- A versioned, research-domain extraction profile (evidence classes, typed fields, vocabularies). Immutable.
CREATE TABLE ev_profile (
  profile_id TEXT PRIMARY KEY NOT NULL CHECK (profile_id GLOB 'evp_*'),
  profile_key TEXT NOT NULL CHECK (length(profile_key) BETWEEN 3 AND 80),
  domain TEXT NOT NULL CHECK (length(domain) BETWEEN 1 AND 80),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  profile_json TEXT NOT NULL CHECK (json_valid(profile_json) AND json_type(profile_json) = 'object'),
  profile_sha256 TEXT NOT NULL UNIQUE CHECK (length(profile_sha256) = 64),
  supersedes_profile_id TEXT REFERENCES ev_profile(profile_id),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- EvidenceExtractionScope: explicit, digest-bound members of ONE S05 FTE run (itself bound to a Reprovane release import). Each
-- member pins paper, document version, asset, S06 RESEARCH_PARSE artifact and the S05 eligibility observed at creation.
CREATE TABLE ev_scope (
  scope_id TEXT PRIMARY KEY NOT NULL CHECK (scope_id GLOB 'evs_*'),
  fte_run_id TEXT NOT NULL REFERENCES fte_run(run_id),
  release_import_id TEXT NOT NULL REFERENCES reprovane_release_import(release_import_id),
  protocol_sha256 TEXT NOT NULL CHECK (length(protocol_sha256) = 64),
  profile_id TEXT NOT NULL REFERENCES ev_profile(profile_id),
  profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
  members_json TEXT NOT NULL CHECK (json_valid(members_json) AND json_type(members_json) = 'array' AND json_array_length(members_json) BETWEEN 1 AND 5000),
  membership_sha256 TEXT NOT NULL CHECK (length(membership_sha256) = 64),
  eligibility_mode TEXT NOT NULL CHECK (eligibility_mode IN ('FINAL_FT_INCLUDE_ONLY','PROVISIONAL_NON_AUTHORITATIVE')),
  provisional_reason TEXT,
  review_policy TEXT NOT NULL CHECK (review_policy IN ('DUAL_INDEPENDENT','SINGLE_HUMAN')),
  review_policy_reason TEXT,
  model_location_allowed TEXT NOT NULL CHECK (model_location_allowed = 'LOCAL_PROCESS'),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (eligibility_mode = 'FINAL_FT_INCLUDE_ONLY' OR length(coalesce(provisional_reason, '')) > 0),
  CHECK (review_policy = 'DUAL_INDEPENDENT' OR length(coalesce(review_policy_reason, '')) > 0)
);

-- An immutable, deterministic extraction input pack for one member (canonical JSON in CAS; this row holds references only).
CREATE TABLE ev_input_pack (
  pack_id TEXT PRIMARY KEY NOT NULL CHECK (pack_id GLOB 'evk_*'),
  scope_id TEXT NOT NULL REFERENCES ev_scope(scope_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  candidate_id TEXT NOT NULL REFERENCES fte_candidate(candidate_id),
  link_id TEXT NOT NULL REFERENCES paper_asset_link(link_id),
  document_version_id TEXT NOT NULL REFERENCES document_version(document_version_id),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  parse_artifact_id TEXT NOT NULL REFERENCES parse_artifact(artifact_id),
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
  profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
  pack_sha256 TEXT NOT NULL CHECK (length(pack_sha256) = 64 AND pack_sha256 NOT GLOB '*[^0-9a-f]*'),
  pack_bytes INTEGER NOT NULL CHECK (pack_bytes > 0),
  segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
  rights_snapshot_json TEXT NOT NULL CHECK (json_valid(rights_snapshot_json) AND json_type(rights_snapshot_json) = 'object'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  UNIQUE (scope_id, paper_id, pack_sha256)
);

CREATE TRIGGER ev_input_pack_matches_scope
BEFORE INSERT ON ev_input_pack
WHEN NOT EXISTS (SELECT 1 FROM ev_scope s, json_each(s.members_json) m
                 WHERE s.scope_id = NEW.scope_id AND s.profile_sha256 = NEW.profile_sha256
                   AND json_extract(m.value, '$.paper_id') = NEW.paper_id AND json_extract(m.value, '$.candidate_id') = NEW.candidate_id
                   AND json_extract(m.value, '$.link_id') = NEW.link_id AND json_extract(m.value, '$.document_version_id') = NEW.document_version_id
                   AND json_extract(m.value, '$.asset_sha256') = NEW.asset_sha256 AND json_extract(m.value, '$.parse_artifact_id') = NEW.parse_artifact_id
                   AND json_extract(m.value, '$.artifact_sha256') = NEW.artifact_sha256)
BEGIN SELECT RAISE(ABORT, 'input pack does not match a pinned member of the scope'); END;

-- One extractor call (mock adapter or validated operator import) on one pack. Invalid responses are kept with their codes.
CREATE TABLE ev_extraction_run (
  run_id TEXT PRIMARY KEY NOT NULL CHECK (run_id GLOB 'evr_*'),
  scope_id TEXT NOT NULL REFERENCES ev_scope(scope_id),
  pack_id TEXT NOT NULL REFERENCES ev_input_pack(pack_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  origin TEXT NOT NULL CHECK (origin IN ('MOCK_LOCAL_ADAPTER','OPERATOR_IMPORT')),
  model_json TEXT NOT NULL CHECK (json_valid(model_json) AND json_type(model_json) = 'object'),
  model_location TEXT NOT NULL CHECK (model_location = 'LOCAL_PROCESS'),
  model_identity TEXT NOT NULL CHECK (model_identity IN ('MOCK_DETERMINISTIC','OPERATOR_DECLARED_UNVERIFIED')),
  template_id TEXT NOT NULL,
  template_sha256 TEXT NOT NULL CHECK (length(template_sha256) = 64),
  rendered_sha256 TEXT NOT NULL CHECK (length(rendered_sha256) = 64),
  input_pack_sha256 TEXT NOT NULL CHECK (length(input_pack_sha256) = 64),
  profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
  extraction_policy_version TEXT NOT NULL CHECK (extraction_policy_version GLOB 'rkb-evidence-extract/?*'),
  output_status TEXT NOT NULL CHECK (output_status IN ('OK','PARTIAL','SCHEMA_INVALID')),
  error_codes_json TEXT NOT NULL CHECK (json_valid(error_codes_json) AND json_type(error_codes_json) = 'array'),
  record_errors_json TEXT NOT NULL CHECK (json_valid(record_errors_json) AND json_type(record_errors_json) = 'array'),
  draft_count INTEGER NOT NULL CHECK (draft_count >= 0),
  raw_response_sha256 TEXT NOT NULL CHECK (length(raw_response_sha256) = 64),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  run_semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(run_semantic_sha256) = 64),
  CHECK ((output_status = 'SCHEMA_INVALID') = (json_array_length(error_codes_json) > 0)),
  CHECK ((output_status = 'PARTIAL') = (json_array_length(record_errors_json) > 0 AND output_status <> 'SCHEMA_INVALID')),
  CHECK (output_status <> 'SCHEMA_INVALID' OR draft_count = 0)
);

CREATE TRIGGER ev_extraction_run_matches_pack
BEFORE INSERT ON ev_extraction_run
WHEN NOT EXISTS (SELECT 1 FROM ev_input_pack p WHERE p.pack_id = NEW.pack_id AND p.scope_id = NEW.scope_id AND p.paper_id = NEW.paper_id
                 AND p.pack_sha256 = NEW.input_pack_sha256 AND p.profile_sha256 = NEW.profile_sha256)
BEGIN SELECT RAISE(ABORT, 'extraction run scope, paper, pack hash or profile differs from its input pack'); END;

-- An immutable EvidenceDraft (one typed claim about exactly one paper and document version). Quote text AND verbatim `text`-typed
-- field values are stored only when rights gate A3 allowed the quote at creation; otherwise each text value is kept only as
-- SHA-256 and length (withheld_text_json) and the draft can never be accepted. Anchors and SHA-256 are always kept.
-- Corrections are new drafts (supersedes_draft_id).
CREATE TABLE ev_draft (
  draft_id TEXT PRIMARY KEY NOT NULL CHECK (draft_id GLOB 'evd_*'),
  scope_id TEXT NOT NULL REFERENCES ev_scope(scope_id),
  pack_id TEXT NOT NULL REFERENCES ev_input_pack(pack_id),
  run_id TEXT REFERENCES ev_extraction_run(run_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  document_version_id TEXT NOT NULL REFERENCES document_version(document_version_id),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  parse_artifact_id TEXT NOT NULL REFERENCES parse_artifact(artifact_id),
  artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
  profile_sha256 TEXT NOT NULL CHECK (length(profile_sha256) = 64),
  member_class TEXT NOT NULL CHECK (member_class IN ('FT_INCLUDE_KB_STAGING','PROVISIONAL_NON_AUTHORITATIVE')),
  record_index INTEGER NOT NULL CHECK (record_index >= 0),
  evidence_class TEXT NOT NULL CHECK (length(evidence_class) BETWEEN 2 AND 60 AND evidence_class NOT GLOB '*[^A-Z0-9_]*'),
  value_status TEXT NOT NULL CHECK (value_status IN ('REPORTED','NOT_REPORTED','NOT_EXTRACTABLE','UNCERTAIN','CONFLICTED','NOT_APPLICABLE')),
  typed_fields_json TEXT NOT NULL CHECK (json_valid(typed_fields_json) AND json_type(typed_fields_json) = 'object'),
  source_kind TEXT NOT NULL CHECK (source_kind = 'FULLTEXT_TEXT'),
  anchors_json TEXT NOT NULL CHECK (json_valid(anchors_json) AND json_type(anchors_json) = 'array'),
  quote_text_json TEXT CHECK (quote_text_json IS NULL OR (json_valid(quote_text_json) AND json_type(quote_text_json) = 'array')),
  quote_storage TEXT NOT NULL CHECK (quote_storage IN ('STORED_GATE_A3','HASH_ONLY_GATE_A3_DENIED','NO_QUOTE')),
  text_storage TEXT NOT NULL CHECK (text_storage IN ('NO_TEXT_FIELD','STORED_GATE_A3','WITHHELD_GATE_A3_DENIED')),
  withheld_text_json TEXT NOT NULL CHECK (json_valid(withheld_text_json) AND json_type(withheld_text_json) = 'object'),
  confidence TEXT CHECK (confidence IS NULL OR confidence IN ('LOW','MEDIUM','HIGH')),
  origin TEXT NOT NULL CHECK (origin IN ('AI_PROPOSAL','HUMAN_CORRECTION')),
  model_identity TEXT NOT NULL CHECK (model_identity IN ('MOCK_DETERMINISTIC','OPERATOR_DECLARED_UNVERIFIED','HUMAN')),
  supersedes_draft_id TEXT REFERENCES ev_draft(draft_id),
  review_gate TEXT NOT NULL CHECK (review_gate = 'HUMAN_REQUIRED'),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  draft_semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(draft_semantic_sha256) = 64),
  CHECK ((origin = 'AI_PROPOSAL') = (run_id IS NOT NULL)),
  CHECK ((origin = 'HUMAN_CORRECTION') = (supersedes_draft_id IS NOT NULL AND model_identity = 'HUMAN')),
  CHECK (value_status <> 'REPORTED' OR json_array_length(anchors_json) >= 1),
  CHECK (value_status <> 'UNCERTAIN' OR json_array_length(anchors_json) >= 1),
  CHECK (value_status <> 'CONFLICTED' OR json_array_length(anchors_json) >= 2),
  CHECK (value_status NOT IN ('NOT_REPORTED','NOT_EXTRACTABLE','NOT_APPLICABLE','CONFLICTED') OR typed_fields_json = '{}'),
  CHECK ((quote_storage = 'STORED_GATE_A3') = (quote_text_json IS NOT NULL)),
  CHECK (quote_storage <> 'NO_QUOTE' OR json_array_length(anchors_json) = 0),
  CHECK (text_storage <> 'STORED_GATE_A3' OR quote_storage = 'STORED_GATE_A3'),
  CHECK ((text_storage = 'WITHHELD_GATE_A3_DENIED') = (withheld_text_json <> '{}'))
);

CREATE TRIGGER ev_draft_matches_pack
BEFORE INSERT ON ev_draft
WHEN NOT EXISTS (SELECT 1 FROM ev_input_pack p WHERE p.pack_id = NEW.pack_id AND p.scope_id = NEW.scope_id AND p.paper_id = NEW.paper_id
                 AND p.document_version_id = NEW.document_version_id AND p.asset_sha256 = NEW.asset_sha256
                 AND p.parse_artifact_id = NEW.parse_artifact_id AND p.artifact_sha256 = NEW.artifact_sha256 AND p.profile_sha256 = NEW.profile_sha256)
  OR (NEW.run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ev_extraction_run r WHERE r.run_id = NEW.run_id AND r.pack_id = NEW.pack_id
                                             AND r.output_status IN ('OK','PARTIAL')))
  OR (NEW.supersedes_draft_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ev_draft d WHERE d.draft_id = NEW.supersedes_draft_id
                                                          AND d.paper_id = NEW.paper_id AND d.document_version_id = NEW.document_version_id
                                                          AND d.scope_id = NEW.scope_id AND d.evidence_class = NEW.evidence_class))
BEGIN SELECT RAISE(ABORT, 'draft paper, version, asset, artifact, profile, run or corrected draft differs from its pack'); END;

-- Independent, append-only structural verification (rkb-evidence-verify/1). Structural verification is NOT semantic entailment.
CREATE TABLE ev_verification (
  verification_id TEXT PRIMARY KEY NOT NULL CHECK (verification_id GLOB 'evv_*'),
  draft_id TEXT NOT NULL REFERENCES ev_draft(draft_id),
  state TEXT NOT NULL CHECK (state IN ('STRUCTURALLY_VERIFIED','ABSENCE_RECORDED','PARTIAL','UNSUPPORTED','AMBIGUOUS','STALE','NOT_ASSESSABLE')),
  checks_json TEXT NOT NULL CHECK (json_valid(checks_json) AND json_type(checks_json) = 'array'),
  failure_codes_json TEXT NOT NULL CHECK (json_valid(failure_codes_json) AND json_type(failure_codes_json) = 'array'),
  suggested_anchors_json TEXT NOT NULL CHECK (json_valid(suggested_anchors_json) AND json_type(suggested_anchors_json) = 'array'),
  access_snapshot_json TEXT NOT NULL CHECK (json_valid(access_snapshot_json) AND json_type(access_snapshot_json) = 'object'),
  verifier_version TEXT NOT NULL CHECK (verifier_version GLOB 'rkb-evidence-verify/?*'),
  number_policy TEXT NOT NULL CHECK (number_policy GLOB 'rkb-number-match/?*'),
  semantic_entailment TEXT NOT NULL CHECK (semantic_entailment = 'NOT_ASSESSED'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK ((state IN ('STRUCTURALLY_VERIFIED','ABSENCE_RECORDED')) = (json_array_length(failure_codes_json) = 0))
);

-- Every disclosure of a draft (typed claim, anchors and - only while gate A3/A5 allows - the quote and verbatim text fields)
-- to a roster reviewer.
CREATE TABLE ev_reveal (
  reveal_id TEXT PRIMARY KEY NOT NULL CHECK (reveal_id GLOB 'evrl_*'),
  draft_id TEXT NOT NULL REFERENCES ev_draft(draft_id),
  reviewer_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  verification_id TEXT REFERENCES ev_verification(verification_id),
  quote_disclosure TEXT NOT NULL CHECK (quote_disclosure IN ('SHOWN','WITHHELD','NO_QUOTE')),
  withheld_reasons_json TEXT NOT NULL CHECK (json_valid(withheld_reasons_json) AND json_type(withheld_reasons_json) = 'array'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK ((quote_disclosure = 'WITHHELD') = (json_array_length(withheld_reasons_json) > 0))
);

-- SHOWN only for a draft whose quote and text values were stored under gate A3; NO_QUOTE only for a draft without evidence text.
CREATE TRIGGER ev_reveal_matches_storage
BEFORE INSERT ON ev_reveal
WHEN EXISTS (SELECT 1 FROM ev_draft d WHERE d.draft_id = NEW.draft_id AND (
               (NEW.quote_disclosure = 'SHOWN' AND (d.quote_storage <> 'STORED_GATE_A3' OR d.text_storage = 'WITHHELD_GATE_A3_DENIED'))
            OR (NEW.quote_disclosure = 'NO_QUOTE' AND (d.quote_storage <> 'NO_QUOTE' OR d.text_storage <> 'NO_TEXT_FIELD'))))
BEGIN SELECT RAISE(ABORT, 'a reveal may show only evidence stored under gate A3 (NO_QUOTE only for drafts without evidence text)'); END;

-- Human review of one draft (append-only; the latest decision of each reviewer counts). CORRECT = this version is wrong, the
-- corrected claim is a new HUMAN_CORRECTION draft that needs its own verification and an independent review.
CREATE TABLE ev_review (
  review_id TEXT PRIMARY KEY NOT NULL CHECK (review_id GLOB 'evrv_*'),
  draft_id TEXT NOT NULL REFERENCES ev_draft(draft_id),
  reviewer_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  decision TEXT NOT NULL CHECK (decision IN ('ACCEPT','REJECT','CORRECT','DEFER')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  rationale TEXT CHECK (rationale IS NULL OR length(rationale) <= 2000),
  corrected_draft_id TEXT REFERENCES ev_draft(draft_id),
  verification_id TEXT REFERENCES ev_verification(verification_id),
  reveal_id TEXT NOT NULL REFERENCES ev_reveal(reveal_id),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK (decision NOT IN ('REJECT','CORRECT') OR (json_array_length(reason_codes_json) > 0 AND length(coalesce(rationale, '')) > 0)),
  CHECK ((decision = 'CORRECT') = (corrected_draft_id IS NOT NULL)),
  CHECK (decision <> 'ACCEPT' OR verification_id IS NOT NULL)
);

CREATE TRIGGER ev_review_requires_reveal
BEFORE INSERT ON ev_review
WHEN NOT EXISTS (SELECT 1 FROM ev_reveal r WHERE r.reveal_id = NEW.reveal_id AND r.draft_id = NEW.draft_id AND r.reviewer_id = NEW.reviewer_id
                 AND (NEW.decision <> 'ACCEPT' OR r.quote_disclosure IN ('SHOWN','NO_QUOTE')))
BEGIN SELECT RAISE(ABORT, 'review without a recorded reveal of this draft to this reviewer (ACCEPT needs the source evidence shown)'); END;

CREATE TRIGGER ev_review_no_self_review
BEFORE INSERT ON ev_review
WHEN EXISTS (SELECT 1 FROM ev_draft d JOIN ai_reviewer v ON v.reviewer_id = NEW.reviewer_id
             LEFT JOIN ev_extraction_run r ON r.run_id = d.run_id
             WHERE d.draft_id = NEW.draft_id AND (d.created_by = 'OPERATOR:' || v.label OR r.created_by = 'OPERATOR:' || v.label))
BEGIN SELECT RAISE(ABORT, 'self-review: the reviewer created, imported or corrected this draft'); END;

CREATE TRIGGER ev_review_accept_needs_current_verification
BEFORE INSERT ON ev_review
WHEN NEW.decision = 'ACCEPT' AND NOT EXISTS (
  SELECT 1 FROM ev_verification v WHERE v.verification_id = NEW.verification_id AND v.draft_id = NEW.draft_id
    AND v.state IN ('STRUCTURALLY_VERIFIED','ABSENCE_RECORDED')
    AND v.rowid = (SELECT max(x.rowid) FROM ev_verification x WHERE x.draft_id = NEW.draft_id))
BEGIN SELECT RAISE(ABORT, 'ACCEPT needs the current verification of this draft to be STRUCTURALLY_VERIFIED or ABSENCE_RECORDED'); END;

CREATE TRIGGER ev_review_no_provisional_accept
BEFORE INSERT ON ev_review
WHEN NEW.decision = 'ACCEPT' AND EXISTS (SELECT 1 FROM ev_draft d WHERE d.draft_id = NEW.draft_id AND d.member_class = 'PROVISIONAL_NON_AUTHORITATIVE')
BEGIN SELECT RAISE(ABORT, 'provisional (eligibility not final) evidence cannot be accepted'); END;

CREATE TRIGGER ev_review_correction_matches
BEFORE INSERT ON ev_review
WHEN NEW.corrected_draft_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM ev_draft c JOIN ai_reviewer v ON v.reviewer_id = NEW.reviewer_id
  WHERE c.draft_id = NEW.corrected_draft_id AND c.supersedes_draft_id = NEW.draft_id AND c.origin = 'HUMAN_CORRECTION'
    AND c.created_by = 'OPERATOR:' || v.label)
BEGIN SELECT RAISE(ABORT, 'a correction must be a HUMAN_CORRECTION draft by this reviewer that supersedes the reviewed draft'); END;

-- Resolution of a disagreement between reviewers (append-only; must cite exactly the current latest reviews).
CREATE TABLE ev_adjudication (
  adjudication_id TEXT PRIMARY KEY NOT NULL CHECK (adjudication_id GLOB 'evad_*'),
  draft_id TEXT NOT NULL REFERENCES ev_draft(draft_id),
  adjudicator_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  outcome TEXT NOT NULL CHECK (outcome IN ('ACCEPT','REJECT')),
  resolved_review_ids_json TEXT NOT NULL CHECK (json_valid(resolved_review_ids_json) AND json_type(resolved_review_ids_json) = 'array'
                                                AND json_array_length(resolved_review_ids_json) >= 2),
  rationale TEXT NOT NULL CHECK (length(rationale) BETWEEN 1 AND 2000),
  verification_id TEXT REFERENCES ev_verification(verification_id),
  reveal_id TEXT NOT NULL REFERENCES ev_reveal(reveal_id),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK (outcome <> 'ACCEPT' OR verification_id IS NOT NULL)
);

CREATE TRIGGER ev_adjudication_rules
BEFORE INSERT ON ev_adjudication
WHEN NOT EXISTS (SELECT 1 FROM ev_reveal r WHERE r.reveal_id = NEW.reveal_id AND r.draft_id = NEW.draft_id AND r.reviewer_id = NEW.adjudicator_id
                 AND (NEW.outcome <> 'ACCEPT' OR r.quote_disclosure IN ('SHOWN','NO_QUOTE')))
  OR EXISTS (SELECT 1 FROM ev_draft d JOIN ai_reviewer v ON v.reviewer_id = NEW.adjudicator_id LEFT JOIN ev_extraction_run r ON r.run_id = d.run_id
             WHERE d.draft_id = NEW.draft_id AND (d.created_by = 'OPERATOR:' || v.label OR r.created_by = 'OPERATOR:' || v.label))
  OR EXISTS (SELECT 1 FROM json_each(NEW.resolved_review_ids_json) j JOIN ev_review w ON w.review_id = j.value
             WHERE w.reviewer_id = NEW.adjudicator_id OR w.draft_id <> NEW.draft_id)
  OR (NEW.outcome = 'ACCEPT' AND (NOT EXISTS (
        SELECT 1 FROM ev_verification v WHERE v.verification_id = NEW.verification_id AND v.draft_id = NEW.draft_id
          AND v.state IN ('STRUCTURALLY_VERIFIED','ABSENCE_RECORDED')
          AND v.rowid = (SELECT max(x.rowid) FROM ev_verification x WHERE x.draft_id = NEW.draft_id))
      OR EXISTS (SELECT 1 FROM ev_draft d WHERE d.draft_id = NEW.draft_id AND d.member_class = 'PROVISIONAL_NON_AUTHORITATIVE')))
BEGIN SELECT RAISE(ABORT, 'adjudication needs a reveal, an independent adjudicator, the draft''s own reviews and (ACCEPT) a current verification'); END;

-- Metadata-only export audit (never quotes or text) as an append-only lifecycle. A RESERVED row is committed BEFORE the report
-- becomes visible; exactly one terminal row (PUBLISHED after the atomic rename, or ABANDONED when nothing was published) may
-- follow it. A RESERVED row without a terminal row is an unconfirmed attempt, never a completed export.
CREATE TABLE ev_export_event (
  export_id TEXT PRIMARY KEY NOT NULL CHECK (export_id GLOB 'eve_*'),
  scope_id TEXT NOT NULL REFERENCES ev_scope(scope_id),
  content_level TEXT NOT NULL CHECK (content_level IN ('METADATA_ONLY','ACCEPTED_TYPED_VALUES')),
  report_sha256 TEXT NOT NULL CHECK (length(report_sha256) = 64),
  item_count INTEGER NOT NULL CHECK (item_count >= 0),
  excluded_by_reason_json TEXT NOT NULL CHECK (json_valid(excluded_by_reason_json) AND json_type(excluded_by_reason_json) = 'object'),
  contains_quotes INTEGER NOT NULL CHECK (contains_quotes = 0),
  phase TEXT NOT NULL CHECK (phase IN ('RESERVED','PUBLISHED','ABANDONED')),
  reservation_id TEXT REFERENCES ev_export_event(export_id),
  target_path_hash TEXT NOT NULL CHECK (length(target_path_hash) = 64),
  folder_name TEXT NOT NULL CHECK (folder_name GLOB 'evidence_?*' AND length(folder_name) <= 80 AND folder_name NOT GLOB '*[/\:.]*'),
  outcome_code TEXT CHECK (outcome_code IS NULL OR (length(outcome_code) BETWEEN 2 AND 60 AND outcome_code NOT GLOB '*[^A-Z0-9_]*')),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK ((phase = 'RESERVED') = (reservation_id IS NULL)),
  CHECK (phase <> 'ABANDONED' OR outcome_code IS NOT NULL)
);

CREATE TRIGGER ev_export_event_terminal_matches_reservation
BEFORE INSERT ON ev_export_event
WHEN NEW.phase <> 'RESERVED' AND (
  NOT EXISTS (SELECT 1 FROM ev_export_event r WHERE r.export_id = NEW.reservation_id AND r.phase = 'RESERVED' AND r.scope_id = NEW.scope_id
              AND r.content_level = NEW.content_level AND r.report_sha256 = NEW.report_sha256 AND r.folder_name = NEW.folder_name
              AND r.target_path_hash = NEW.target_path_hash
              AND r.item_count = NEW.item_count AND r.excluded_by_reason_json = NEW.excluded_by_reason_json)
  OR EXISTS (SELECT 1 FROM ev_export_event t WHERE t.reservation_id = NEW.reservation_id))
BEGIN SELECT RAISE(ABORT, 'an export outcome must close exactly one matching RESERVED export event'); END;

-- Immutability: every S08 table is append-only.
CREATE TRIGGER ev_profile_no_update BEFORE UPDATE ON ev_profile BEGIN SELECT RAISE(ABORT, 'immutable: ev_profile'); END;
CREATE TRIGGER ev_profile_no_delete BEFORE DELETE ON ev_profile BEGIN SELECT RAISE(ABORT, 'immutable: ev_profile'); END;
CREATE TRIGGER ev_scope_no_update BEFORE UPDATE ON ev_scope BEGIN SELECT RAISE(ABORT, 'immutable: ev_scope'); END;
CREATE TRIGGER ev_scope_no_delete BEFORE DELETE ON ev_scope BEGIN SELECT RAISE(ABORT, 'immutable: ev_scope'); END;
CREATE TRIGGER ev_input_pack_no_update BEFORE UPDATE ON ev_input_pack BEGIN SELECT RAISE(ABORT, 'immutable: ev_input_pack'); END;
CREATE TRIGGER ev_input_pack_no_delete BEFORE DELETE ON ev_input_pack BEGIN SELECT RAISE(ABORT, 'immutable: ev_input_pack'); END;
CREATE TRIGGER ev_extraction_run_no_update BEFORE UPDATE ON ev_extraction_run BEGIN SELECT RAISE(ABORT, 'immutable: ev_extraction_run'); END;
CREATE TRIGGER ev_extraction_run_no_delete BEFORE DELETE ON ev_extraction_run BEGIN SELECT RAISE(ABORT, 'immutable: ev_extraction_run'); END;
CREATE TRIGGER ev_draft_no_update BEFORE UPDATE ON ev_draft BEGIN SELECT RAISE(ABORT, 'immutable: ev_draft'); END;
CREATE TRIGGER ev_draft_no_delete BEFORE DELETE ON ev_draft BEGIN SELECT RAISE(ABORT, 'immutable: ev_draft'); END;
CREATE TRIGGER ev_verification_no_update BEFORE UPDATE ON ev_verification BEGIN SELECT RAISE(ABORT, 'immutable: ev_verification'); END;
CREATE TRIGGER ev_verification_no_delete BEFORE DELETE ON ev_verification BEGIN SELECT RAISE(ABORT, 'immutable: ev_verification'); END;
CREATE TRIGGER ev_reveal_no_update BEFORE UPDATE ON ev_reveal BEGIN SELECT RAISE(ABORT, 'immutable: ev_reveal'); END;
CREATE TRIGGER ev_reveal_no_delete BEFORE DELETE ON ev_reveal BEGIN SELECT RAISE(ABORT, 'immutable: ev_reveal'); END;
CREATE TRIGGER ev_review_no_update BEFORE UPDATE ON ev_review BEGIN SELECT RAISE(ABORT, 'immutable: ev_review'); END;
CREATE TRIGGER ev_review_no_delete BEFORE DELETE ON ev_review BEGIN SELECT RAISE(ABORT, 'immutable: ev_review'); END;
CREATE TRIGGER ev_adjudication_no_update BEFORE UPDATE ON ev_adjudication BEGIN SELECT RAISE(ABORT, 'immutable: ev_adjudication'); END;
CREATE TRIGGER ev_adjudication_no_delete BEFORE DELETE ON ev_adjudication BEGIN SELECT RAISE(ABORT, 'immutable: ev_adjudication'); END;
CREATE TRIGGER ev_export_event_no_update BEFORE UPDATE ON ev_export_event BEGIN SELECT RAISE(ABORT, 'immutable: ev_export_event'); END;
CREATE TRIGGER ev_export_event_no_delete BEFORE DELETE ON ev_export_event BEGIN SELECT RAISE(ABORT, 'immutable: ev_export_event'); END;
