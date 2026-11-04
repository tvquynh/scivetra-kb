-- SciVetra KB migration 0005: S05 Full-text Eligibility CANDIDATE workflow (KB-side, NON_AUTHORITATIVE staging).
-- Purely additive: no 0001/0002/0003/0004 object is altered. Every table is append-only (no UPDATE / DELETE).
-- Authority boundary (ADR-05-01..05): this is NOT a Reprovane RV-FTE run, NOT a SEALED corpus release and NOT an official
-- PRISMA count. Its only input is ONE verified SEALED Reprovane Stage-11 title/abstract release imported through S03.
-- An S04J journal inventory (jh_*) can never be a source: candidates reference project_paper_reference rows only.
-- Decisions and adjudications are human-only (actor_type pinned to HUMAN); no column can hold an AI vote.

-- Frozen review protocol: criteria, reason vocabularies, evidence/version policy, consensus rule, retrieval-effort rule.
CREATE TABLE fte_protocol (
  protocol_id TEXT PRIMARY KEY NOT NULL CHECK (protocol_id GLOB 'ftp_*'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  protocol_json TEXT NOT NULL CHECK (json_valid(protocol_json) AND json_type(protocol_json) = 'object'),
  protocol_sha256 TEXT NOT NULL UNIQUE CHECK (length(protocol_sha256) = 64),
  reason_vocabulary_version TEXT NOT NULL CHECK (length(reason_vocabulary_version) > 0),
  consensus_rule TEXT NOT NULL CHECK (consensus_rule = 'TWO_INDEPENDENT_REVIEWERS_PLUS_ADJUDICATOR'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- One staging run over exactly one verified SEALED TA release (frozen digests; authority pinned).
CREATE TABLE fte_run (
  run_id TEXT PRIMARY KEY NOT NULL CHECK (run_id GLOB 'ftr_*'),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  workspace_ref TEXT NOT NULL REFERENCES workspace_registration(workspace_ref),
  release_import_id TEXT NOT NULL REFERENCES reprovane_release_import(release_import_id),
  release_id TEXT NOT NULL CHECK (length(release_id) > 0),
  release_manifest_sha256 TEXT NOT NULL CHECK (length(release_manifest_sha256) = 64),
  project_revision_id TEXT NOT NULL CHECK (length(project_revision_id) > 0),
  screening_run_id TEXT NOT NULL CHECK (length(screening_run_id) > 0),
  screening_manifest_sha256 TEXT NOT NULL CHECK (length(screening_manifest_sha256) = 64),
  source_verification_json TEXT NOT NULL CHECK (json_valid(source_verification_json) AND json_type(source_verification_json) = 'object'),
  item_count INTEGER NOT NULL CHECK (item_count >= 1),
  item_set_sha256 TEXT NOT NULL CHECK (length(item_set_sha256) = 64),
  protocol_id TEXT NOT NULL REFERENCES fte_protocol(protocol_id),
  protocol_sha256 TEXT NOT NULL CHECK (length(protocol_sha256) = 64),
  roster_sha256 TEXT NOT NULL CHECK (length(roster_sha256) = 64),
  assignment_sha256 TEXT NOT NULL CHECK (length(assignment_sha256) = 64),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  client_version TEXT NOT NULL,
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

-- Lifecycle history: OPEN -> INPUT_FROZEN -> REVIEW_IN_PROGRESS <-> REVIEW_RESOLVED -> LOCKED_FOR_HANDOFF (terminal).
CREATE TABLE fte_run_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'fre_*'),
  run_id TEXT NOT NULL REFERENCES fte_run(run_id),
  seq INTEGER NOT NULL CHECK (seq >= 1),
  new_state TEXT NOT NULL CHECK (new_state IN ('OPEN','INPUT_FROZEN','REVIEW_IN_PROGRESS','REVIEW_RESOLVED','LOCKED_FOR_HANDOFF')),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(reason) > 0),
  detail_json TEXT NOT NULL CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  UNIQUE (run_id, seq)
);

-- Frozen human roster. actor_type is pinned to HUMAN; labels are self-declared audit labels, not authenticated identities.
CREATE TABLE fte_reviewer (
  reviewer_id TEXT PRIMARY KEY NOT NULL CHECK (reviewer_id GLOB 'ftv_*'),
  run_id TEXT NOT NULL REFERENCES fte_run(run_id),
  label TEXT NOT NULL CHECK (label GLOB 'OPERATOR:?*'),
  role TEXT NOT NULL CHECK (role IN ('REVIEWER','ADJUDICATOR')),
  actor_type TEXT NOT NULL CHECK (actor_type = 'HUMAN'),
  conflicts_json TEXT NOT NULL CHECK (json_valid(conflicts_json) AND json_type(conflicts_json) = 'array'),
  UNIQUE (run_id, label)
);

-- One candidate per frozen corpus_item_id of the source release (never from a DOI list or journal inventory).
CREATE TABLE fte_candidate (
  candidate_id TEXT PRIMARY KEY NOT NULL CHECK (candidate_id GLOB 'ftc_*'),
  run_id TEXT NOT NULL REFERENCES fte_run(run_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  corpus_item_id TEXT NOT NULL CHECK (length(corpus_item_id) > 0),
  reference_id TEXT NOT NULL REFERENCES project_paper_reference(reference_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  publication_entity_id TEXT NOT NULL CHECK (length(publication_entity_id) > 0),
  item_sha256 TEXT NOT NULL CHECK (length(item_sha256) = 64),
  UNIQUE (run_id, corpus_item_id),
  UNIQUE (run_id, ordinal)
);

-- Frozen independent-review assignments (two distinct REVIEWERs per candidate, conflicts of interest excluded).
CREATE TABLE fte_assignment (
  assignment_id TEXT PRIMARY KEY NOT NULL CHECK (assignment_id GLOB 'fta_*'),
  candidate_id TEXT NOT NULL REFERENCES fte_candidate(candidate_id),
  reviewer_id TEXT NOT NULL REFERENCES fte_reviewer(reviewer_id),
  slot INTEGER NOT NULL CHECK (slot IN (1, 2)),
  UNIQUE (candidate_id, reviewer_id),
  UNIQUE (candidate_id, slot)
);

-- Evidence references (append-only; the latest per candidate is current). KB_ASSET: a verified KB PDF whose link to the
-- candidate's paper is HUMAN_CONFIRMED at attach time. RV_STAGE11B_ARTIFACT: an external hash-only claim (never described
-- as byte-reverified). NONE_NOT_RETRIEVED: no full text available.
CREATE TABLE fte_evidence (
  evidence_id TEXT PRIMARY KEY NOT NULL CHECK (evidence_id GLOB 'fev_*'),
  candidate_id TEXT NOT NULL REFERENCES fte_candidate(candidate_id),
  kind TEXT NOT NULL CHECK (kind IN ('KB_ASSET','RV_STAGE11B_ARTIFACT','NONE_NOT_RETRIEVED')),
  asset_sha256 TEXT REFERENCES pdf_asset(asset_sha256),
  link_id TEXT REFERENCES paper_asset_link(link_id),
  document_version_id TEXT REFERENCES document_version(document_version_id),
  version_class TEXT,
  link_state_at_attach TEXT,
  rights_assessment_id TEXT REFERENCES rights_assessment(assessment_id),
  rights_class TEXT,
  external_ref_json TEXT CHECK (external_ref_json IS NULL OR (json_valid(external_ref_json) AND json_type(external_ref_json) = 'object')),
  evidence_ready INTEGER NOT NULL CHECK (evidence_ready IN (0, 1)),
  blockers_json TEXT NOT NULL CHECK (json_valid(blockers_json) AND json_type(blockers_json) = 'array'),
  byte_verification TEXT NOT NULL CHECK (byte_verification IN ('KB_CAS_REHASHED_AT_ATTACH','EXTERNAL_HASH_ONLY_NOT_REVERIFIED','NOT_APPLICABLE')),
  evidence_fingerprint TEXT NOT NULL CHECK (length(evidence_fingerprint) = 64),
  supersedes_evidence_id TEXT REFERENCES fte_evidence(evidence_id),
  attached_by TEXT NOT NULL CHECK (attached_by GLOB 'OPERATOR:?*'),
  attached_at_utc TEXT NOT NULL CHECK (attached_at_utc GLOB '*Z'),
  CHECK ((kind = 'KB_ASSET') = (asset_sha256 IS NOT NULL AND link_id IS NOT NULL AND document_version_id IS NOT NULL)),
  CHECK (kind <> 'KB_ASSET' OR link_state_at_attach = 'HUMAN_CONFIRMED'),
  CHECK ((kind = 'RV_STAGE11B_ARTIFACT') = (external_ref_json IS NOT NULL)),
  CHECK (kind <> 'RV_STAGE11B_ARTIFACT' OR byte_verification = 'EXTERNAL_HASH_ONLY_NOT_REVERIFIED'),
  CHECK (kind <> 'KB_ASSET' OR byte_verification = 'KB_CAS_REHASHED_AT_ATTACH'),
  CHECK (kind <> 'NONE_NOT_RETRIEVED' OR evidence_ready = 0),
  CHECK (evidence_ready = 0 OR json_array_length(blockers_json) = 0)
);
CREATE INDEX fte_evidence_candidate ON fte_evidence(candidate_id);

-- Operational acquisition attempts (metadata only; never a vote). No URL query strings, files, sessions or secrets.
CREATE TABLE fte_acquisition_attempt (
  attempt_id TEXT PRIMARY KEY NOT NULL CHECK (attempt_id GLOB 'ftq_*'),
  candidate_id TEXT NOT NULL REFERENCES fte_candidate(candidate_id),
  status TEXT NOT NULL CHECK (status IN ('LOGIN_REQUIRED','BOT_CHECK','INSTITUTIONAL_RESTRICTED','NO_ACCESS','RETRY_QUEUED','WRONG_PDF',
                                         'NOT_FOUND_ONLINE','OA_LOCATION_FAILED','OTHER')),
  source_description TEXT NOT NULL CHECK (length(source_description) BETWEEN 1 AND 300),
  attempted_on TEXT NOT NULL CHECK (attempted_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  retry_after TEXT CHECK (retry_after IS NULL OR retry_after GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  note TEXT CHECK (note IS NULL OR length(note) <= 1000),
  recorded_by TEXT NOT NULL CHECK (recorded_by GLOB 'OPERATOR:?*'),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);
CREATE INDEX fte_acquisition_attempt_candidate ON fte_acquisition_attempt(candidate_id);

-- Human reviewer decisions (append-only; a newer decision explicitly supersedes the reviewer's previous one).
CREATE TABLE fte_decision (
  decision_id TEXT PRIMARY KEY NOT NULL CHECK (decision_id GLOB 'ftd_*'),
  candidate_id TEXT NOT NULL REFERENCES fte_candidate(candidate_id),
  reviewer_id TEXT NOT NULL REFERENCES fte_reviewer(reviewer_id),
  outcome TEXT NOT NULL CHECK (outcome IN ('FT_INCLUDE','FT_EXCLUDE','FT_UNSURE','FT_NOT_RETRIEVED')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  note TEXT CHECK (note IS NULL OR length(note) <= 2000),
  locator_json TEXT CHECK (locator_json IS NULL OR (json_valid(locator_json) AND json_type(locator_json) = 'object')),
  related_corpus_item_id TEXT,
  evidence_id TEXT REFERENCES fte_evidence(evidence_id),
  evidence_fingerprint TEXT NOT NULL CHECK (length(evidence_fingerprint) = 64),
  protocol_sha256 TEXT NOT NULL CHECK (length(protocol_sha256) = 64),
  item_sha256 TEXT NOT NULL CHECK (length(item_sha256) = 64),
  supersedes_decision_id TEXT UNIQUE REFERENCES fte_decision(decision_id),
  decided_at_utc TEXT NOT NULL CHECK (decided_at_utc GLOB '*Z'),
  decision_sha256 TEXT NOT NULL UNIQUE CHECK (length(decision_sha256) = 64),
  CHECK (outcome NOT IN ('FT_EXCLUDE','FT_NOT_RETRIEVED') OR json_array_length(reason_codes_json) > 0),
  CHECK (outcome NOT IN ('FT_INCLUDE','FT_EXCLUDE') OR evidence_id IS NOT NULL)
);
CREATE INDEX fte_decision_candidate ON fte_decision(candidate_id);

-- Human adjudication of an actual disagreement (or an unsure vote) between the two assigned reviewers' current decisions.
CREATE TABLE fte_adjudication (
  adjudication_id TEXT PRIMARY KEY NOT NULL CHECK (adjudication_id GLOB 'ftj_*'),
  candidate_id TEXT NOT NULL REFERENCES fte_candidate(candidate_id),
  adjudicator_id TEXT NOT NULL REFERENCES fte_reviewer(reviewer_id),
  outcome TEXT NOT NULL CHECK (outcome IN ('FT_INCLUDE','FT_EXCLUDE','FT_NOT_RETRIEVED')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  note TEXT NOT NULL CHECK (length(trim(note)) > 0 AND length(note) <= 2000),
  related_corpus_item_id TEXT,
  resolved_decision_ids_json TEXT NOT NULL CHECK (json_valid(resolved_decision_ids_json) AND json_type(resolved_decision_ids_json) = 'array'
                                                  AND json_array_length(resolved_decision_ids_json) = 2),
  evidence_id TEXT REFERENCES fte_evidence(evidence_id),
  evidence_fingerprint TEXT NOT NULL CHECK (length(evidence_fingerprint) = 64),
  protocol_sha256 TEXT NOT NULL CHECK (length(protocol_sha256) = 64),
  supersedes_adjudication_id TEXT UNIQUE REFERENCES fte_adjudication(adjudication_id),
  decided_at_utc TEXT NOT NULL CHECK (decided_at_utc GLOB '*Z'),
  adjudication_sha256 TEXT NOT NULL UNIQUE CHECK (length(adjudication_sha256) = 64),
  CHECK (outcome NOT IN ('FT_EXCLUDE','FT_NOT_RETRIEVED') OR json_array_length(reason_codes_json) > 0),
  CHECK (outcome NOT IN ('FT_INCLUDE','FT_EXCLUDE') OR evidence_id IS NOT NULL)
);

-- The local, immutable staging snapshot. Its handoff manifest bytes live in CAS (schema-5 CAS reference).
CREATE TABLE fte_lock (
  lock_id TEXT PRIMARY KEY NOT NULL CHECK (lock_id GLOB 'ftl_*'),
  run_id TEXT NOT NULL UNIQUE REFERENCES fte_run(run_id),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  handoff_manifest_sha256 TEXT NOT NULL CHECK (length(handoff_manifest_sha256) = 64),
  outcomes_sha256 TEXT NOT NULL CHECK (length(outcomes_sha256) = 64),
  summary_json TEXT NOT NULL CHECK (json_valid(summary_json) AND json_type(summary_json) = 'object'),
  locked_by TEXT NOT NULL CHECK (locked_by GLOB 'OPERATOR:?*'),
  locked_at_utc TEXT NOT NULL CHECK (locked_at_utc GLOB '*Z')
);

-- Persistence-boundary rules.
CREATE TRIGGER fte_reviewer_frozen_after_create
BEFORE INSERT ON fte_reviewer
WHEN EXISTS (SELECT 1 FROM fte_run_event e WHERE e.run_id = NEW.run_id AND e.new_state = 'INPUT_FROZEN')
BEGIN
  SELECT RAISE(ABORT, 'FTE roster is frozen');
END;

CREATE TRIGGER fte_candidate_frozen_after_create
BEFORE INSERT ON fte_candidate
WHEN EXISTS (SELECT 1 FROM fte_run_event e WHERE e.run_id = NEW.run_id AND e.new_state = 'INPUT_FROZEN')
BEGIN
  SELECT RAISE(ABORT, 'FTE candidate set is frozen');
END;

CREATE TRIGGER fte_candidate_reference_of_release
BEFORE INSERT ON fte_candidate
WHEN NOT EXISTS (SELECT 1 FROM project_paper_reference r JOIN fte_run u ON u.run_id = NEW.run_id
                 WHERE r.reference_id = NEW.reference_id AND r.release_import_id = u.release_import_id AND r.workspace_ref = u.workspace_ref
                   AND r.corpus_item_id = NEW.corpus_item_id AND r.item_sha256 = NEW.item_sha256 AND r.paper_id = NEW.paper_id)
BEGIN
  SELECT RAISE(ABORT, 'FTE candidate must be an item of the run source release (same workspace)');
END;

CREATE TRIGGER fte_assignment_frozen_after_create
BEFORE INSERT ON fte_assignment
WHEN EXISTS (SELECT 1 FROM fte_candidate c JOIN fte_run_event e ON e.run_id = c.run_id
             WHERE c.candidate_id = NEW.candidate_id AND e.new_state = 'INPUT_FROZEN')
BEGIN
  SELECT RAISE(ABORT, 'FTE assignments are frozen');
END;

CREATE TRIGGER fte_assignment_reviewer_role
BEFORE INSERT ON fte_assignment
WHEN NOT EXISTS (SELECT 1 FROM fte_reviewer v JOIN fte_candidate c ON c.run_id = v.run_id
                 WHERE v.reviewer_id = NEW.reviewer_id AND c.candidate_id = NEW.candidate_id AND v.role = 'REVIEWER')
BEGIN
  SELECT RAISE(ABORT, 'assignment needs a REVIEWER of the same run');
END;

CREATE TRIGGER fte_decision_assigned_reviewer
BEFORE INSERT ON fte_decision
WHEN NOT EXISTS (SELECT 1 FROM fte_assignment a WHERE a.candidate_id = NEW.candidate_id AND a.reviewer_id = NEW.reviewer_id)
BEGIN
  SELECT RAISE(ABORT, 'decision by a reviewer not assigned to this candidate');
END;

CREATE TRIGGER fte_adjudication_adjudicator_role
BEFORE INSERT ON fte_adjudication
WHEN NOT EXISTS (SELECT 1 FROM fte_reviewer v JOIN fte_candidate c ON c.run_id = v.run_id
                 WHERE v.reviewer_id = NEW.adjudicator_id AND c.candidate_id = NEW.candidate_id AND v.role = 'ADJUDICATOR')
BEGIN
  SELECT RAISE(ABORT, 'adjudication needs an ADJUDICATOR of the same run');
END;

-- Nothing can be added to a run once it is LOCKED_FOR_HANDOFF.
CREATE TRIGGER fte_decision_not_after_lock BEFORE INSERT ON fte_decision
WHEN EXISTS (SELECT 1 FROM fte_candidate c JOIN fte_lock l ON l.run_id = c.run_id WHERE c.candidate_id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'FTE run is locked'); END;
CREATE TRIGGER fte_adjudication_not_after_lock BEFORE INSERT ON fte_adjudication
WHEN EXISTS (SELECT 1 FROM fte_candidate c JOIN fte_lock l ON l.run_id = c.run_id WHERE c.candidate_id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'FTE run is locked'); END;
CREATE TRIGGER fte_evidence_not_after_lock BEFORE INSERT ON fte_evidence
WHEN EXISTS (SELECT 1 FROM fte_candidate c JOIN fte_lock l ON l.run_id = c.run_id WHERE c.candidate_id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'FTE run is locked'); END;
CREATE TRIGGER fte_attempt_not_after_lock BEFORE INSERT ON fte_acquisition_attempt
WHEN EXISTS (SELECT 1 FROM fte_candidate c JOIN fte_lock l ON l.run_id = c.run_id WHERE c.candidate_id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'FTE run is locked'); END;
CREATE TRIGGER fte_run_event_not_after_lock BEFORE INSERT ON fte_run_event
WHEN EXISTS (SELECT 1 FROM fte_lock l WHERE l.run_id = NEW.run_id)
BEGIN SELECT RAISE(ABORT, 'FTE run is locked'); END;

-- Immutability: every S05 table is append-only.
CREATE TRIGGER fte_protocol_no_update BEFORE UPDATE ON fte_protocol BEGIN SELECT RAISE(ABORT, 'immutable: fte_protocol'); END;
CREATE TRIGGER fte_protocol_no_delete BEFORE DELETE ON fte_protocol BEGIN SELECT RAISE(ABORT, 'immutable: fte_protocol'); END;
CREATE TRIGGER fte_run_no_update BEFORE UPDATE ON fte_run BEGIN SELECT RAISE(ABORT, 'immutable: fte_run'); END;
CREATE TRIGGER fte_run_no_delete BEFORE DELETE ON fte_run BEGIN SELECT RAISE(ABORT, 'immutable: fte_run'); END;
CREATE TRIGGER fte_run_event_no_update BEFORE UPDATE ON fte_run_event BEGIN SELECT RAISE(ABORT, 'immutable: fte_run_event'); END;
CREATE TRIGGER fte_run_event_no_delete BEFORE DELETE ON fte_run_event BEGIN SELECT RAISE(ABORT, 'immutable: fte_run_event'); END;
CREATE TRIGGER fte_reviewer_no_update BEFORE UPDATE ON fte_reviewer BEGIN SELECT RAISE(ABORT, 'immutable: fte_reviewer'); END;
CREATE TRIGGER fte_reviewer_no_delete BEFORE DELETE ON fte_reviewer BEGIN SELECT RAISE(ABORT, 'immutable: fte_reviewer'); END;
CREATE TRIGGER fte_candidate_no_update BEFORE UPDATE ON fte_candidate BEGIN SELECT RAISE(ABORT, 'immutable: fte_candidate'); END;
CREATE TRIGGER fte_candidate_no_delete BEFORE DELETE ON fte_candidate BEGIN SELECT RAISE(ABORT, 'immutable: fte_candidate'); END;
CREATE TRIGGER fte_assignment_no_update BEFORE UPDATE ON fte_assignment BEGIN SELECT RAISE(ABORT, 'immutable: fte_assignment'); END;
CREATE TRIGGER fte_assignment_no_delete BEFORE DELETE ON fte_assignment BEGIN SELECT RAISE(ABORT, 'immutable: fte_assignment'); END;
CREATE TRIGGER fte_evidence_no_update BEFORE UPDATE ON fte_evidence BEGIN SELECT RAISE(ABORT, 'immutable: fte_evidence'); END;
CREATE TRIGGER fte_evidence_no_delete BEFORE DELETE ON fte_evidence BEGIN SELECT RAISE(ABORT, 'immutable: fte_evidence'); END;
CREATE TRIGGER fte_acquisition_attempt_no_update BEFORE UPDATE ON fte_acquisition_attempt BEGIN SELECT RAISE(ABORT, 'immutable: fte_acquisition_attempt'); END;
CREATE TRIGGER fte_acquisition_attempt_no_delete BEFORE DELETE ON fte_acquisition_attempt BEGIN SELECT RAISE(ABORT, 'immutable: fte_acquisition_attempt'); END;
CREATE TRIGGER fte_decision_no_update BEFORE UPDATE ON fte_decision BEGIN SELECT RAISE(ABORT, 'immutable: fte_decision'); END;
CREATE TRIGGER fte_decision_no_delete BEFORE DELETE ON fte_decision BEGIN SELECT RAISE(ABORT, 'immutable: fte_decision'); END;
CREATE TRIGGER fte_adjudication_no_update BEFORE UPDATE ON fte_adjudication BEGIN SELECT RAISE(ABORT, 'immutable: fte_adjudication'); END;
CREATE TRIGGER fte_adjudication_no_delete BEFORE DELETE ON fte_adjudication BEGIN SELECT RAISE(ABORT, 'immutable: fte_adjudication'); END;
CREATE TRIGGER fte_lock_no_update BEFORE UPDATE ON fte_lock BEGIN SELECT RAISE(ABORT, 'immutable: fte_lock'); END;
CREATE TRIGGER fte_lock_no_delete BEFORE DELETE ON fte_lock BEGIN SELECT RAISE(ABORT, 'immutable: fte_lock'); END;
