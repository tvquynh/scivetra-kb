-- SciVetra KB migration 0007: S07 AI-assisted screening PROPOSALS (S01 v03 08, AIP-1..5). Purely additive: no 0001-0006
-- object is altered. Every table is append-only (no UPDATE / DELETE).
-- Authority boundary (ADR-07): a proposal is never a decision. Nothing here writes, or can be joined into, an S05 ballot
-- (fte_decision / fte_adjudication), a Reprovane decision or a corpus membership. Human approvals are advisory KB
-- annotations by an owner-registered HUMAN roster; reviewer labels in an AI/system namespace are refused by constraint.
-- Model location is LOCAL_PROCESS only (REMOTE_API / DESKTOP_APP_MANUAL need OAG-6 and are refused by CHECK).

-- Versioned screening criteria / rubric (immutable). A newer set may supersede an older one (proposals become STALE).
CREATE TABLE ai_criteria_set (
  criteria_set_id TEXT PRIMARY KEY NOT NULL CHECK (criteria_set_id GLOB 'acs_*'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  task_type TEXT NOT NULL CHECK (task_type IN ('TA_SCREEN','FT_SCREEN')),
  criteria_json TEXT NOT NULL CHECK (json_valid(criteria_json) AND json_type(criteria_json) = 'object'),
  criteria_sha256 TEXT NOT NULL UNIQUE CHECK (length(criteria_sha256) = 64),
  rubric_version TEXT NOT NULL CHECK (length(rubric_version) BETWEEN 1 AND 40),
  supersedes_criteria_set_id TEXT REFERENCES ai_criteria_set(criteria_set_id),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- What may be processed: an explicit, digest-bound member list from ONE auditable basis (never a journal inventory).
CREATE TABLE ai_processing_scope (
  scope_id TEXT PRIMARY KEY NOT NULL CHECK (scope_id GLOB 'psc_*'),
  task_type TEXT NOT NULL CHECK (task_type IN ('TA_SCREEN','FT_SCREEN')),
  corpus_basis TEXT NOT NULL CHECK (corpus_basis IN ('RV_RELEASE','FTE_RUN')),
  release_import_id TEXT REFERENCES reprovane_release_import(release_import_id),
  fte_run_id TEXT REFERENCES fte_run(run_id),
  members_json TEXT NOT NULL CHECK (json_valid(members_json) AND json_type(members_json) = 'array' AND json_array_length(members_json) BETWEEN 1 AND 5000),
  membership_sha256 TEXT NOT NULL CHECK (length(membership_sha256) = 64),
  criteria_set_id TEXT NOT NULL REFERENCES ai_criteria_set(criteria_set_id),
  criteria_sha256 TEXT NOT NULL CHECK (length(criteria_sha256) = 64),
  model_location_allowed TEXT NOT NULL CHECK (model_location_allowed = 'LOCAL_PROCESS'),
  content_classes_json TEXT NOT NULL CHECK (json_valid(content_classes_json) AND json_type(content_classes_json) = 'array'),
  blind_first INTEGER NOT NULL CHECK (blind_first IN (0, 1)),
  blind_first_override_reason TEXT,
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((corpus_basis = 'RV_RELEASE') = (release_import_id IS NOT NULL AND fte_run_id IS NULL)),
  CHECK ((corpus_basis = 'FTE_RUN') = (fte_run_id IS NOT NULL)),
  CHECK ((task_type = 'FT_SCREEN') = (corpus_basis = 'FTE_RUN')),
  CHECK (blind_first = 1 OR length(coalesce(blind_first_override_reason, '')) > 0)
);

-- A deterministic input pack for one scope member (canonical JSON in CAS; this row holds references and hashes only).
CREATE TABLE ai_input_pack (
  pack_id TEXT PRIMARY KEY NOT NULL CHECK (pack_id GLOB 'aipk_*'),
  scope_id TEXT NOT NULL REFERENCES ai_processing_scope(scope_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  task_type TEXT NOT NULL CHECK (task_type IN ('TA_SCREEN','FT_SCREEN')),
  content_class TEXT NOT NULL CHECK (content_class IN ('METADATA_ONLY','METADATA_AND_ABSTRACT','ANCHOR_ONLY','FULLTEXT_EXCERPT')),
  pack_sha256 TEXT NOT NULL CHECK (length(pack_sha256) = 64 AND pack_sha256 NOT GLOB '*[^0-9a-f]*'),
  pack_bytes INTEGER NOT NULL CHECK (pack_bytes > 0),
  item_refs_json TEXT NOT NULL CHECK (json_valid(item_refs_json) AND json_type(item_refs_json) = 'array'),
  candidate_id TEXT REFERENCES fte_candidate(candidate_id),
  parse_artifact_id TEXT REFERENCES parse_artifact(artifact_id),
  artifact_sha256 TEXT,
  link_id TEXT REFERENCES paper_asset_link(link_id),
  rights_snapshot_json TEXT NOT NULL CHECK (json_valid(rights_snapshot_json) AND json_type(rights_snapshot_json) = 'object'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  UNIQUE (scope_id, paper_id, pack_sha256),
  CHECK ((task_type = 'FT_SCREEN') = (parse_artifact_id IS NOT NULL AND candidate_id IS NOT NULL AND link_id IS NOT NULL AND artifact_sha256 IS NOT NULL))
);

CREATE TRIGGER ai_input_pack_matches_scope
BEFORE INSERT ON ai_input_pack
WHEN NOT EXISTS (SELECT 1 FROM ai_processing_scope s, json_each(s.members_json) m
                 WHERE s.scope_id = NEW.scope_id AND s.task_type = NEW.task_type AND json_extract(m.value, '$.paper_id') = NEW.paper_id)
BEGIN SELECT RAISE(ABORT, 'input pack paper is not a member of the scope'); END;

-- An immutable AI proposal (rkb-ai-proposal/1). Always HUMAN_REQUIRED; never a decision.
CREATE TABLE ai_proposal (
  proposal_id TEXT PRIMARY KEY NOT NULL CHECK (proposal_id GLOB 'aip_*'),
  scope_id TEXT NOT NULL REFERENCES ai_processing_scope(scope_id),
  pack_id TEXT NOT NULL REFERENCES ai_input_pack(pack_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  task_type TEXT NOT NULL CHECK (task_type IN ('TA_SCREEN','FT_SCREEN')),
  origin TEXT NOT NULL CHECK (origin IN ('MOCK_LOCAL_ADAPTER','OPERATOR_IMPORT')),
  model_json TEXT NOT NULL CHECK (json_valid(model_json) AND json_type(model_json) = 'object'),
  model_location TEXT NOT NULL CHECK (model_location = 'LOCAL_PROCESS'),
  model_identity TEXT NOT NULL CHECK (model_identity IN ('MOCK_DETERMINISTIC','OPERATOR_DECLARED_UNVERIFIED')),
  template_id TEXT NOT NULL,
  template_sha256 TEXT NOT NULL CHECK (length(template_sha256) = 64),
  rendered_sha256 TEXT NOT NULL CHECK (length(rendered_sha256) = 64),
  input_pack_sha256 TEXT NOT NULL CHECK (length(input_pack_sha256) = 64),
  output_status TEXT NOT NULL CHECK (output_status IN ('OK','PARTIAL','MODEL_ERROR','REFUSED_BY_MODEL','SCHEMA_INVALID','SCOPE_DENIED')),
  error_codes_json TEXT NOT NULL CHECK (json_valid(error_codes_json) AND json_type(error_codes_json) = 'array'),
  suggested_outcome TEXT CHECK (suggested_outcome IS NULL OR suggested_outcome IN ('INCLUDE','EXCLUDE','UNSURE')),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
  uncertainty TEXT CHECK (uncertainty IS NULL OR uncertainty IN ('LOW','MEDIUM','HIGH')),
  raw_response_sha256 TEXT NOT NULL CHECK (length(raw_response_sha256) = 64),
  review_gate TEXT NOT NULL CHECK (review_gate = 'HUMAN_REQUIRED'),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  proposal_semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(proposal_semantic_sha256) = 64),
  CHECK ((output_status IN ('OK','PARTIAL')) = (suggested_outcome IS NOT NULL)),
  CHECK ((output_status IN ('OK','PARTIAL')) = (json_array_length(error_codes_json) = 0))
);

CREATE TRIGGER ai_proposal_matches_pack
BEFORE INSERT ON ai_proposal
WHEN NOT EXISTS (SELECT 1 FROM ai_input_pack p WHERE p.pack_id = NEW.pack_id AND p.scope_id = NEW.scope_id AND p.paper_id = NEW.paper_id
                 AND p.task_type = NEW.task_type AND p.pack_sha256 = NEW.input_pack_sha256)
BEGIN SELECT RAISE(ABORT, 'proposal scope, paper, task or pack hash differs from its input pack'); END;

-- Independent, append-only verification runs (the latest one is current; history is kept).
CREATE TABLE ai_proposal_verification (
  verification_id TEXT PRIMARY KEY NOT NULL CHECK (verification_id GLOB 'apvr_*'),
  proposal_id TEXT NOT NULL REFERENCES ai_proposal(proposal_id),
  state TEXT NOT NULL CHECK (state IN ('PASSED','PARTIAL','FAILED')),
  checks_json TEXT NOT NULL CHECK (json_valid(checks_json) AND json_type(checks_json) = 'array'),
  failure_codes_json TEXT NOT NULL CHECK (json_valid(failure_codes_json) AND json_type(failure_codes_json) = 'array'),
  access_snapshot_json TEXT NOT NULL CHECK (json_valid(access_snapshot_json) AND json_type(access_snapshot_json) = 'object'),
  verifier_version TEXT NOT NULL CHECK (verifier_version GLOB 'rkb-ai-verify/?*'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK ((state = 'FAILED') = (json_array_length(failure_codes_json) > 0))
);

-- Owner-defined HUMAN reviewer roster for advisory approvals (independent of S05 run rosters). Labels are self-declared
-- audit labels, never generated; AI / system / model namespaces are refused.
CREATE TABLE ai_reviewer (
  reviewer_id TEXT PRIMARY KEY NOT NULL CHECK (reviewer_id GLOB 'arv_*'),
  label TEXT NOT NULL UNIQUE CHECK (length(label) BETWEEN 2 AND 64 AND label GLOB '[a-z]*' AND label NOT GLOB '*[^a-z0-9._-]*'
    AND lower(label) NOT GLOB 'ai*' AND lower(label) NOT GLOB 'system*' AND lower(label) NOT GLOB 'model*' AND lower(label) NOT GLOB 'mock*'
    AND lower(label) NOT GLOB 'bot*' AND lower(label) NOT GLOB 'llm*' AND lower(label) NOT GLOB 'auto*'),
  actor_type TEXT NOT NULL CHECK (actor_type = 'HUMAN'),
  registered_by TEXT NOT NULL CHECK (registered_by GLOB 'OPERATOR:?*'),
  registration_reason TEXT NOT NULL CHECK (length(registration_reason) BETWEEN 1 AND 500),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- Blind-first: an initial human judgement recorded BEFORE the AI suggestion is shown to that reviewer.
CREATE TABLE ai_human_judgement (
  judgement_id TEXT PRIMARY KEY NOT NULL CHECK (judgement_id GLOB 'ahj_*'),
  scope_id TEXT NOT NULL REFERENCES ai_processing_scope(scope_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  reviewer_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  initial_outcome TEXT NOT NULL CHECK (initial_outcome IN ('INCLUDE','EXCLUDE','UNSURE')),
  note TEXT CHECK (note IS NULL OR length(note) <= 1000),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  UNIQUE (scope_id, paper_id, reviewer_id)
);

CREATE TRIGGER ai_human_judgement_before_reveal
BEFORE INSERT ON ai_human_judgement
WHEN EXISTS (SELECT 1 FROM ai_proposal_reveal r WHERE r.scope_id = NEW.scope_id AND r.paper_id = NEW.paper_id AND r.reviewer_id = NEW.reviewer_id)
BEGIN SELECT RAISE(ABORT, 'initial judgement after the AI suggestion was revealed is not an initial judgement'); END;

-- Every time an AI suggestion is revealed to a reviewer (audit of the blind-first gate).
CREATE TABLE ai_proposal_reveal (
  reveal_id TEXT PRIMARY KEY NOT NULL CHECK (reveal_id GLOB 'arvl_*'),
  proposal_id TEXT NOT NULL REFERENCES ai_proposal(proposal_id),
  scope_id TEXT NOT NULL REFERENCES ai_processing_scope(scope_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  reviewer_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  basis TEXT NOT NULL CHECK (basis IN ('AFTER_INITIAL_JUDGEMENT','BLIND_FIRST_DISABLED_BY_PROTOCOL')),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

CREATE TRIGGER ai_proposal_reveal_respects_blind_first
BEFORE INSERT ON ai_proposal_reveal
WHEN (SELECT blind_first FROM ai_processing_scope WHERE scope_id = NEW.scope_id) = 1 AND NOT EXISTS (
  SELECT 1 FROM ai_human_judgement j WHERE j.scope_id = NEW.scope_id AND j.paper_id = NEW.paper_id AND j.reviewer_id = NEW.reviewer_id)
BEGIN SELECT RAISE(ABORT, 'blind-first: no initial human judgement by this reviewer'); END;

-- Advisory human approval of a proposal (append-only; a later approval supersedes an earlier one, both remain).
CREATE TABLE ai_approval (
  approval_id TEXT PRIMARY KEY NOT NULL CHECK (approval_id GLOB 'apv_*'),
  proposal_id TEXT NOT NULL REFERENCES ai_proposal(proposal_id),
  reviewer_id TEXT NOT NULL REFERENCES ai_reviewer(reviewer_id),
  decision TEXT NOT NULL CHECK (decision IN ('ACCEPT','ACCEPT_WITH_EDIT','REJECT','DEFER')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  edited_payload_json TEXT CHECK (edited_payload_json IS NULL OR (json_valid(edited_payload_json) AND json_type(edited_payload_json) = 'object')),
  edited_payload_sha256 TEXT,
  verification_id TEXT REFERENCES ai_proposal_verification(verification_id),
  linked_authoritative_decision_json TEXT CHECK (linked_authoritative_decision_json IS NULL OR json_valid(linked_authoritative_decision_json)),
  authority TEXT NOT NULL CHECK (authority = 'ADVISORY_KB_ANNOTATION'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK (decision NOT IN ('REJECT','ACCEPT_WITH_EDIT') OR json_array_length(reason_codes_json) > 0),
  CHECK ((decision = 'ACCEPT_WITH_EDIT') = (edited_payload_json IS NOT NULL AND edited_payload_sha256 IS NOT NULL)),
  CHECK (decision NOT IN ('ACCEPT','ACCEPT_WITH_EDIT') OR verification_id IS NOT NULL)
);

-- The approving human must have seen the suggestion legitimately (blind-first) and must not be the operator who produced it.
CREATE TRIGGER ai_approval_requires_reveal
BEFORE INSERT ON ai_approval
WHEN NOT EXISTS (SELECT 1 FROM ai_proposal_reveal r WHERE r.proposal_id = NEW.proposal_id AND r.reviewer_id = NEW.reviewer_id)
BEGIN SELECT RAISE(ABORT, 'approval without a recorded, legitimate reveal of the suggestion to this reviewer'); END;

CREATE TRIGGER ai_approval_no_self_approval
BEFORE INSERT ON ai_approval
WHEN EXISTS (SELECT 1 FROM ai_proposal p JOIN ai_reviewer v ON v.reviewer_id = NEW.reviewer_id
             WHERE p.proposal_id = NEW.proposal_id AND p.created_by = 'OPERATOR:' || v.label)
BEGIN SELECT RAISE(ABORT, 'self-approval: the reviewer produced or imported this proposal'); END;

CREATE TRIGGER ai_approval_accept_needs_passing_verification
BEFORE INSERT ON ai_approval
WHEN NEW.decision IN ('ACCEPT','ACCEPT_WITH_EDIT') AND NOT EXISTS (
  SELECT 1 FROM ai_proposal_verification v WHERE v.verification_id = NEW.verification_id AND v.proposal_id = NEW.proposal_id
    AND v.state IN ('PASSED','PARTIAL')
    AND v.rowid = (SELECT max(x.rowid) FROM ai_proposal_verification x WHERE x.proposal_id = NEW.proposal_id))
BEGIN SELECT RAISE(ABORT, 'ACCEPT needs the current verification of this proposal to be PASSED or PARTIAL'); END;

-- Immutability: every S07 table is append-only.
CREATE TRIGGER ai_criteria_set_no_update BEFORE UPDATE ON ai_criteria_set BEGIN SELECT RAISE(ABORT, 'immutable: ai_criteria_set'); END;
CREATE TRIGGER ai_criteria_set_no_delete BEFORE DELETE ON ai_criteria_set BEGIN SELECT RAISE(ABORT, 'immutable: ai_criteria_set'); END;
CREATE TRIGGER ai_processing_scope_no_update BEFORE UPDATE ON ai_processing_scope BEGIN SELECT RAISE(ABORT, 'immutable: ai_processing_scope'); END;
CREATE TRIGGER ai_processing_scope_no_delete BEFORE DELETE ON ai_processing_scope BEGIN SELECT RAISE(ABORT, 'immutable: ai_processing_scope'); END;
CREATE TRIGGER ai_input_pack_no_update BEFORE UPDATE ON ai_input_pack BEGIN SELECT RAISE(ABORT, 'immutable: ai_input_pack'); END;
CREATE TRIGGER ai_input_pack_no_delete BEFORE DELETE ON ai_input_pack BEGIN SELECT RAISE(ABORT, 'immutable: ai_input_pack'); END;
CREATE TRIGGER ai_proposal_no_update BEFORE UPDATE ON ai_proposal BEGIN SELECT RAISE(ABORT, 'immutable: ai_proposal'); END;
CREATE TRIGGER ai_proposal_no_delete BEFORE DELETE ON ai_proposal BEGIN SELECT RAISE(ABORT, 'immutable: ai_proposal'); END;
CREATE TRIGGER ai_proposal_verification_no_update BEFORE UPDATE ON ai_proposal_verification BEGIN SELECT RAISE(ABORT, 'immutable: ai_proposal_verification'); END;
CREATE TRIGGER ai_proposal_verification_no_delete BEFORE DELETE ON ai_proposal_verification BEGIN SELECT RAISE(ABORT, 'immutable: ai_proposal_verification'); END;
CREATE TRIGGER ai_reviewer_no_update BEFORE UPDATE ON ai_reviewer BEGIN SELECT RAISE(ABORT, 'immutable: ai_reviewer'); END;
CREATE TRIGGER ai_reviewer_no_delete BEFORE DELETE ON ai_reviewer BEGIN SELECT RAISE(ABORT, 'immutable: ai_reviewer'); END;
CREATE TRIGGER ai_human_judgement_no_update BEFORE UPDATE ON ai_human_judgement BEGIN SELECT RAISE(ABORT, 'immutable: ai_human_judgement'); END;
CREATE TRIGGER ai_human_judgement_no_delete BEFORE DELETE ON ai_human_judgement BEGIN SELECT RAISE(ABORT, 'immutable: ai_human_judgement'); END;
CREATE TRIGGER ai_proposal_reveal_no_update BEFORE UPDATE ON ai_proposal_reveal BEGIN SELECT RAISE(ABORT, 'immutable: ai_proposal_reveal'); END;
CREATE TRIGGER ai_proposal_reveal_no_delete BEFORE DELETE ON ai_proposal_reveal BEGIN SELECT RAISE(ABORT, 'immutable: ai_proposal_reveal'); END;
CREATE TRIGGER ai_approval_no_update BEFORE UPDATE ON ai_approval BEGIN SELECT RAISE(ABORT, 'immutable: ai_approval'); END;
CREATE TRIGGER ai_approval_no_delete BEFORE DELETE ON ai_approval BEGIN SELECT RAISE(ABORT, 'immutable: ai_approval'); END;
