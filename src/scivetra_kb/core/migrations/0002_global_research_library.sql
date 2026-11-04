-- SciVetra KB (rkb) migration 0002: S03 Global Research Library (additive only).
-- Nothing in 0001 is altered: no table, column, index or trigger of migration 0001 is changed or dropped.
-- Every new table is append-only (UPDATE/DELETE refused by triggers at the end). State columns of the S01
-- model (identity state, availability, link state, member confirmation) are projections over append-only
-- *_event tables, so history is never rewritten.
-- Contract: S01 v03 03 sections 3.3, 4, 6; 05 sections 1-4; 10 sections 3, 5-7. Deviations: docs/ADR_S03.md.

CREATE TABLE workspace_registration (
  workspace_ref TEXT PRIMARY KEY NOT NULL CHECK (workspace_ref GLOB 'wsr_*'),
  operator_label TEXT NOT NULL UNIQUE CHECK (length(operator_label) BETWEEN 1 AND 200),
  registered_by TEXT NOT NULL CHECK (registered_by GLOB 'OPERATOR:?*'),
  path_hint_sha256 TEXT CHECK (path_hint_sha256 IS NULL OR length(path_hint_sha256) = 64),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE workspace_availability_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'wae_*'),
  workspace_ref TEXT NOT NULL REFERENCES workspace_registration(workspace_ref),
  availability TEXT NOT NULL CHECK (availability IN ('AVAILABLE','SOURCE_UNAVAILABLE','RETIRED')),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  prior_event_id TEXT REFERENCES workspace_availability_event(event_id),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE import_event (
  import_event_id TEXT PRIMARY KEY NOT NULL CHECK (import_event_id GLOB 'imp_*'),
  kind TEXT NOT NULL CHECK (kind IN ('REPROVANE_EXPORT','MANUAL_FILE')),
  idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) = 64),
  inputs_sha256_json TEXT NOT NULL CHECK (json_valid(inputs_sha256_json) AND json_type(inputs_sha256_json) = 'array'),
  outcome TEXT NOT NULL CHECK (outcome IN ('REGISTERED','DUPLICATE_NOOP','REJECTED','QUARANTINED')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  asset_sha256 TEXT CHECK (asset_sha256 IS NULL OR length(asset_sha256) = 64),
  context_json TEXT NOT NULL CHECK (json_valid(context_json) AND json_type(context_json) = 'object'),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*' OR actor = 'SYSTEM'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK (outcome IN ('REGISTERED','DUPLICATE_NOOP') OR json_array_length(reason_codes_json) > 0)
);
CREATE UNIQUE INDEX import_event_registered_once ON import_event(kind, idempotency_key) WHERE outcome = 'REGISTERED';

CREATE TABLE reprovane_release_import (
  release_import_id TEXT PRIMARY KEY NOT NULL CHECK (release_import_id GLOB 'rri_*'),
  workspace_ref TEXT NOT NULL REFERENCES workspace_registration(workspace_ref),
  release_id TEXT NOT NULL CHECK (length(release_id) > 0),
  release_label TEXT,
  project_revision_id TEXT,
  release_manifest_sha256 TEXT NOT NULL CHECK (length(release_manifest_sha256) = 64),
  manifest_bytes_sha256 TEXT NOT NULL CHECK (length(manifest_bytes_sha256) = 64),
  corpus_jsonl_bytes_sha256 TEXT NOT NULL CHECK (length(corpus_jsonl_bytes_sha256) = 64),
  corpus_csv_bytes_sha256 TEXT NOT NULL CHECK (length(corpus_csv_bytes_sha256) = 64),
  sums_bytes_sha256 TEXT NOT NULL CHECK (length(sums_bytes_sha256) = 64),
  item_count INTEGER NOT NULL CHECK (item_count >= 0),
  import_event_id TEXT NOT NULL REFERENCES import_event(import_event_id),
  imported_at_utc TEXT NOT NULL CHECK (imported_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (workspace_ref, release_manifest_sha256)
);

CREATE TABLE project_paper_reference (
  reference_id TEXT PRIMARY KEY NOT NULL CHECK (reference_id GLOB 'ppr_*'),
  workspace_ref TEXT NOT NULL REFERENCES workspace_registration(workspace_ref),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  link_basis TEXT NOT NULL CHECK (link_basis IN ('EXACT_MANIFESTATION_ID','OPERATOR_CONFIRMED','NEW_PAPER_MINTED')),
  link_key_json TEXT CHECK (link_key_json IS NULL OR (json_valid(link_key_json) AND json_type(link_key_json) = 'object')),
  link_policy_version TEXT NOT NULL,
  release_import_id TEXT NOT NULL REFERENCES reprovane_release_import(release_import_id),
  release_id TEXT NOT NULL,
  release_manifest_sha256 TEXT NOT NULL CHECK (length(release_manifest_sha256) = 64),
  corpus_item_id TEXT NOT NULL CHECK (length(corpus_item_id) > 0),
  item_sha256 TEXT NOT NULL CHECK (length(item_sha256) = 64),
  publication_entity_id TEXT NOT NULL CHECK (length(publication_entity_id) > 0),
  item_claims_json TEXT NOT NULL CHECK (json_valid(item_claims_json) AND json_type(item_claims_json) = 'object'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (workspace_ref, release_manifest_sha256, corpus_item_id),
  -- PPR-2 / IA-4: an automatic link is keyed only by a manifestation-level identifier, never by openalex_work.
  CHECK (link_basis <> 'EXACT_MANIFESTATION_ID' OR (link_key_json IS NOT NULL AND coalesce(json_extract(link_key_json, '$.namespace'), '') IN ('doi','pmid','pmcid','arxiv'))),
  CHECK (link_basis = 'EXACT_MANIFESTATION_ID' OR link_key_json IS NULL)
);
CREATE INDEX project_paper_reference_paper ON project_paper_reference(paper_id);

CREATE TABLE paper_kind_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'pke_*'),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  manifestation_kind TEXT NOT NULL CHECK (manifestation_kind IN ('JOURNAL_ARTICLE','PROCEEDINGS_PAPER','PREPRINT','BOOK_CHAPTER','BOOK','THESIS','DATASET','SOFTWARE','REPORT','OTHER','UNKNOWN')),
  claim_refs_json TEXT NOT NULL CHECK (json_valid(claim_refs_json) AND json_type(claim_refs_json) = 'array' AND json_array_length(claim_refs_json) > 0),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  prior_event_id TEXT REFERENCES paper_kind_event(event_id),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE identity_conflict (
  conflict_id TEXT PRIMARY KEY NOT NULL CHECK (conflict_id GLOB 'ic_*'),
  kind TEXT NOT NULL CHECK (kind IN ('DOI_COLLISION','MULTI_DOI_ITEM','SHARED_MLI_WITH_CONFLICT','SAME_BYTES_MULTIPLE_PAPERS')),
  key_namespace TEXT NOT NULL,
  key_value TEXT,
  paper_ids_json TEXT NOT NULL CHECK (json_valid(paper_ids_json) AND json_type(paper_ids_json) = 'array' AND json_array_length(paper_ids_json) > 0),
  assertion_ids_json TEXT NOT NULL CHECK (json_valid(assertion_ids_json) AND json_type(assertion_ids_json) = 'array'),
  detail_json TEXT NOT NULL CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object'),
  raised_by_ref TEXT NOT NULL CHECK (length(raised_by_ref) > 0),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE identity_resolution_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'ire_*'),
  conflict_id TEXT NOT NULL REFERENCES identity_conflict(conflict_id),
  action TEXT NOT NULL CHECK (action IN ('ASSERTIONS_WITHDRAWN','DOI_SELECTED_FOR_ITEM','CONFIRMED_DISTINCT_MANIFESTATIONS','DEFER')),
  assertion_updates_json TEXT NOT NULL CHECK (json_valid(assertion_updates_json) AND json_type(assertion_updates_json) = 'array'),
  evidence_refs_json TEXT NOT NULL CHECK (json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array' AND json_array_length(evidence_refs_json) > 0),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  prior_event_id TEXT REFERENCES identity_resolution_event(event_id),
  decided_at_utc TEXT NOT NULL CHECK (decided_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (action = 'DEFER' OR action = 'CONFIRMED_DISTINCT_MANIFESTATIONS' OR json_array_length(assertion_updates_json) > 0)
);

CREATE TABLE assertion_status_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'ase_*'),
  assertion_id TEXT NOT NULL REFERENCES identity_assertion(assertion_id),
  new_status TEXT NOT NULL CHECK (new_status IN ('ACTIVE','RETRACTED_ASSERTION','CONFLICTING')),
  cause_kind TEXT NOT NULL CHECK (cause_kind IN ('IDENTITY_CONFLICT_RAISED','HUMAN_RESOLUTION')),
  cause_ref TEXT NOT NULL,
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  -- A system-raised conflict can only mark assertions CONFLICTING; nothing but a human resolution re-activates or withdraws one.
  CHECK (cause_kind <> 'IDENTITY_CONFLICT_RAISED' OR new_status = 'CONFLICTING')
);

CREATE TABLE link_candidate (
  link_candidate_id TEXT PRIMARY KEY NOT NULL CHECK (link_candidate_id GLOB 'lc_*'),
  reference_id TEXT NOT NULL REFERENCES project_paper_reference(reference_id),
  subject_paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  candidate_paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  signals_json TEXT NOT NULL CHECK (json_valid(signals_json) AND json_type(signals_json) = 'array' AND json_array_length(signals_json) > 0),
  conflict_codes_json TEXT NOT NULL CHECK (json_valid(conflict_codes_json) AND json_type(conflict_codes_json) = 'array'),
  link_policy_version TEXT NOT NULL,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (subject_paper_id <> candidate_paper_id)
);

CREATE TABLE link_decision (
  decision_id TEXT PRIMARY KEY NOT NULL CHECK (decision_id GLOB 'ld_*'),
  link_candidate_id TEXT NOT NULL REFERENCES link_candidate(link_candidate_id),
  decision TEXT NOT NULL CHECK (decision IN ('SAME_MANIFESTATION','DIFFERENT_MANIFESTATION_SAME_WORK','UNRELATED','DEFER')),
  reviewer_label TEXT NOT NULL CHECK (reviewer_label GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  prior_decision_id TEXT REFERENCES link_decision(decision_id),
  decided_at_utc TEXT NOT NULL CHECK (decided_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE work_family (
  work_family_id TEXT PRIMARY KEY NOT NULL CHECK (work_family_id GLOB 'wf_*'),
  created_by TEXT NOT NULL CHECK (created_by IN ('OPERATOR','PROVIDER_RELATION_PROPOSAL')),
  origin_ref TEXT NOT NULL CHECK (length(origin_ref) > 0),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE work_family_member (
  member_id TEXT PRIMARY KEY NOT NULL CHECK (member_id GLOB 'wfm_*'),
  work_family_id TEXT NOT NULL REFERENCES work_family(work_family_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  role TEXT NOT NULL CHECK (role IN ('PREPRINT','ACCEPTED_MANUSCRIPT_HOST','VERSION_OF_RECORD','EXTENDED_VERSION','CONFERENCE_VERSION','ERRATUM_OR_CORRECTION','RELATED_DATASET','RELATED_SOFTWARE','OTHER')),
  -- WF-1: every membership starts as a proposal; confirmation is a human event (work_family_member_event).
  confirmation TEXT NOT NULL CHECK (confirmation = 'PROPOSED'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (work_family_id, paper_id)
);

CREATE TABLE work_family_member_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'wfme_*'),
  member_id TEXT NOT NULL REFERENCES work_family_member(member_id),
  confirmation TEXT NOT NULL CHECK (confirmation IN ('PROPOSED','HUMAN_CONFIRMED','REJECTED')),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  prior_event_id TEXT REFERENCES work_family_member_event(event_id),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE document_version (
  document_version_id TEXT PRIMARY KEY NOT NULL CHECK (document_version_id GLOB 'dv_*'),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  version_class TEXT NOT NULL CHECK (version_class IN ('SUBMITTED','ACCEPTED','PUBLISHED','UPDATED_PUBLISHED','UNKNOWN')),
  version_evidence_json TEXT NOT NULL CHECK (json_valid(version_evidence_json) AND json_type(version_evidence_json) = 'array'),
  version_label TEXT,
  supersedes_version_id TEXT REFERENCES document_version(document_version_id),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  -- DV-1: a known version class needs at least one evidence item; otherwise UNKNOWN.
  CHECK (version_class = 'UNKNOWN' OR json_array_length(version_evidence_json) > 0)
);

-- DV-1 / RV-Stage11B rule: version evidence comes only from version-specific claims or human observation,
-- never from a filename, URL, host or login state.
CREATE TRIGGER document_version_evidence_sources
BEFORE INSERT ON document_version
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.version_evidence_json) e
  WHERE json_type(e.value) <> 'object'
     OR json_extract(e.value, '$.source') NOT IN ('CROSSREF_LICENSE_CONTENT_VERSION','OPENALEX_LOCATION_VERSION','UNPAYWALL_LOCATION_VERSION',
                                                 'DATACITE_VERSION_CLAIM','ARXIV_VERSION_STAMP','PUBLISHER_PAGE_HUMAN','PDF_WATERMARK_HUMAN')
     OR json_extract(e.value, '$.source') IS NULL
     OR coalesce(length(json_extract(e.value, '$.claim_ref')), 0) = 0
     OR json_extract(e.value, '$.value') IS NULL)
BEGIN
  SELECT RAISE(ABORT, 'version evidence source not allowed');
END;

CREATE TABLE pdf_asset (
  asset_sha256 TEXT PRIMARY KEY NOT NULL CHECK (length(asset_sha256) = 64 AND asset_sha256 NOT GLOB '*[^0-9a-f]*'),
  byte_length INTEGER NOT NULL CHECK (byte_length > 0),
  storage_key TEXT NOT NULL CHECK (storage_key = 'cas/sha256/' || substr(asset_sha256, 1, 2) || '/' || asset_sha256),
  media_format TEXT NOT NULL CHECK (media_format = 'PDF'),
  first_import_event_id TEXT NOT NULL REFERENCES import_event(import_event_id),
  first_registered_at_utc TEXT NOT NULL CHECK (first_registered_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE asset_validation_event (
  validation_id TEXT PRIMARY KEY NOT NULL CHECK (validation_id GLOB 'ave_*'),
  input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
  asset_sha256 TEXT REFERENCES pdf_asset(asset_sha256),
  validator TEXT NOT NULL,
  validator_version TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('STRUCTURALLY_VALID','REJECTED')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  result_json TEXT NOT NULL CHECK (json_valid(result_json) AND json_type(result_json) = 'object'),
  import_event_id TEXT NOT NULL REFERENCES import_event(import_event_id),
  validated_at_utc TEXT NOT NULL CHECK (validated_at_utc GLOB '*Z'),
  CHECK ((outcome = 'STRUCTURALLY_VALID') = (json_array_length(reason_codes_json) = 0)),
  CHECK (asset_sha256 IS NULL OR asset_sha256 = input_sha256)
);

CREATE TABLE paper_asset_link (
  link_id TEXT PRIMARY KEY NOT NULL CHECK (link_id GLOB 'pal_*'),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  document_version_id TEXT NOT NULL REFERENCES document_version(document_version_id),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  link_role TEXT NOT NULL CHECK (link_role IN ('MAIN_TEXT','SUPPLEMENTARY','ERRATUM_TEXT','COVER_OR_ABSTRACT_ONLY','UNKNOWN_ROLE')),
  -- HUMAN_CONFIRMED and MISMATCH only arise from a human event, never at creation.
  identity_state TEXT NOT NULL CHECK (identity_state IN ('UNVERIFIED','AUTO_EVIDENCE_MATCH','AMBIGUOUS')),
  identity_evidence_json TEXT NOT NULL CHECK (json_valid(identity_evidence_json) AND json_type(identity_evidence_json) = 'array'),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  identity_policy_version TEXT NOT NULL,
  created_from TEXT NOT NULL CHECK (length(created_from) > 0),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (asset_sha256, document_version_id, link_role)
);

CREATE TABLE paper_asset_link_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'pale_*'),
  link_id TEXT NOT NULL REFERENCES paper_asset_link(link_id),
  new_state TEXT NOT NULL CHECK (new_state IN ('UNVERIFIED','AUTO_EVIDENCE_MATCH','HUMAN_CONFIRMED','MISMATCH','AMBIGUOUS')),
  new_role TEXT CHECK (new_role IS NULL OR new_role IN ('MAIN_TEXT','SUPPLEMENTARY','ERRATUM_TEXT','COVER_OR_ABSTRACT_ONLY','UNKNOWN_ROLE')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json) AND json_type(evidence_json) = 'array'),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*' OR actor = 'SYSTEM'),
  cause_ref TEXT,
  prior_event_id TEXT REFERENCES paper_asset_link_event(event_id),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  -- The system may only raise ambiguity (PAL-1); confirmations, rejections and relinks are human events.
  CHECK (actor <> 'SYSTEM' OR (new_state = 'AMBIGUOUS' AND coalesce(cause_ref, '') GLOB 'ic_*')),
  CHECK (new_state <> 'AUTO_EVIDENCE_MATCH')
);

CREATE VIEW paper_asset_link_effective AS
SELECT l.*, l.rowid AS row_seq,
       coalesce((SELECT e.new_state FROM paper_asset_link_event e WHERE e.link_id = l.link_id ORDER BY e.rowid DESC LIMIT 1), l.identity_state) AS effective_state,
       coalesce((SELECT e.new_role FROM paper_asset_link_event e WHERE e.link_id = l.link_id AND e.new_role IS NOT NULL ORDER BY e.rowid DESC LIMIT 1), l.link_role) AS effective_role
FROM paper_asset_link l;

-- N04 / PAL-1: bytes already linked as MAIN_TEXT to another paper (and not MISMATCH there) can never gain an
-- independent automatic match.
CREATE TRIGGER paper_asset_link_no_double_auto_match
BEFORE INSERT ON paper_asset_link
WHEN NEW.identity_state = 'AUTO_EVIDENCE_MATCH' AND EXISTS (
  SELECT 1 FROM paper_asset_link_effective o
  WHERE o.asset_sha256 = NEW.asset_sha256 AND o.paper_id <> NEW.paper_id
    AND o.effective_role = 'MAIN_TEXT' AND o.effective_state <> 'MISMATCH')
BEGIN
  SELECT RAISE(ABORT, 'same bytes already linked to another paper: AUTO_EVIDENCE_MATCH refused');
END;

CREATE TRIGGER paper_asset_link_paper_matches_version
BEFORE INSERT ON paper_asset_link
WHEN NOT EXISTS (SELECT 1 FROM document_version v WHERE v.document_version_id = NEW.document_version_id AND v.paper_id = NEW.paper_id)
BEGIN
  SELECT RAISE(ABORT, 'link paper differs from document version paper');
END;

CREATE TRIGGER paper_asset_link_event_conflict_exists
BEFORE INSERT ON paper_asset_link_event
WHEN NEW.actor = 'SYSTEM' AND NOT EXISTS (SELECT 1 FROM identity_conflict c WHERE c.conflict_id = NEW.cause_ref)
BEGIN
  SELECT RAISE(ABORT, 'system link event must cite an identity conflict');
END;

CREATE TABLE rights_assessment (
  assessment_id TEXT PRIMARY KEY NOT NULL CHECK (assessment_id GLOB 'rga_*'),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  document_version_id TEXT NOT NULL REFERENCES document_version(document_version_id),
  rights_class TEXT NOT NULL CHECK (rights_class IN ('PUBLIC_OA_LICENSED','PUBLIC_OA_UNVERIFIED','INSTITUTIONAL_RESTRICTED','UNKNOWN')),
  store_locally TEXT NOT NULL CHECK (store_locally IN ('ALLOW','DENY','UNDETERMINED')),
  redistribute TEXT NOT NULL CHECK (redistribute IN ('ALLOW','DENY','UNDETERMINED')),
  local_process TEXT NOT NULL CHECK (local_process IN ('ALLOW','DENY','UNDETERMINED')),
  external_ai_process TEXT NOT NULL CHECK (external_ai_process IN ('ALLOW','DENY','UNDETERMINED')),
  license_evidence_json TEXT NOT NULL CHECK (json_valid(license_evidence_json) AND json_type(license_evidence_json) = 'array'),
  terms_declarations_json TEXT NOT NULL CHECK (json_valid(terms_declarations_json) AND json_type(terms_declarations_json) = 'array'),
  access_provenance_json TEXT NOT NULL CHECK (json_valid(access_provenance_json) AND json_type(access_provenance_json) = 'object'),
  flags_json TEXT NOT NULL CHECK (json_valid(flags_json) AND json_type(flags_json) = 'array'),
  policy_version TEXT NOT NULL CHECK (policy_version = 'rights-policy/1'),
  assessed_by TEXT NOT NULL CHECK (assessed_by = 'POLICY_ENGINE' OR assessed_by GLOB 'OPERATOR:?*'),
  recorded_by TEXT NOT NULL CHECK (recorded_by GLOB 'OPERATOR:?*'),
  owner_approval_ref TEXT,
  supersedes_assessment_id TEXT REFERENCES rights_assessment(assessment_id),
  assessed_at_utc TEXT NOT NULL CHECK (assessed_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  -- Default deny (05 4.2-4.3): redistribution and remote AI need an owner approval reference (OAG-6) in addition to the class.
  CHECK (redistribute <> 'ALLOW' OR (rights_class = 'PUBLIC_OA_LICENSED' AND coalesce(length(owner_approval_ref), 0) > 0)),
  CHECK (external_ai_process <> 'ALLOW' OR coalesce(length(owner_approval_ref), 0) > 0),
  CHECK (rights_class <> 'UNKNOWN' OR (redistribute = 'DENY' AND external_ai_process = 'DENY' AND local_process <> 'ALLOW')),
  CHECK (rights_class <> 'PUBLIC_OA_UNVERIFIED' OR (redistribute = 'DENY' AND external_ai_process = 'DENY')),
  CHECK (rights_class <> 'INSTITUTIONAL_RESTRICTED' OR (redistribute = 'DENY' AND external_ai_process = 'DENY'))
);

CREATE TRIGGER rights_assessment_version_matches_asset
BEFORE INSERT ON rights_assessment
WHEN NOT EXISTS (SELECT 1 FROM paper_asset_link l WHERE l.asset_sha256 = NEW.asset_sha256 AND l.document_version_id = NEW.document_version_id)
BEGIN
  SELECT RAISE(ABORT, 'rights assessment needs a link between this asset and this document version');
END;

CREATE TABLE rights_review_task (
  task_id TEXT PRIMARY KEY NOT NULL CHECK (task_id GLOB 'rrt_*'),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  document_version_id TEXT NOT NULL REFERENCES document_version(document_version_id),
  assessment_id TEXT NOT NULL REFERENCES rights_assessment(assessment_id),
  trigger TEXT NOT NULL CHECK (trigger IN ('RIGHTS_EVIDENCE_CONFLICT','RIGHTS_UNKNOWN','ACCESS_BARRIER_OBSERVED','LICENCE_VERSION_MISMATCH')),
  context_json TEXT NOT NULL CHECK (json_valid(context_json) AND json_type(context_json) = 'object'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE kb_upgrade_event (
  upgrade_id TEXT PRIMARY KEY NOT NULL CHECK (upgrade_id GLOB 'kue_*'),
  from_version INTEGER NOT NULL CHECK (from_version >= 1),
  to_version INTEGER NOT NULL CHECK (to_version > from_version),
  backup_manifest_sha256 TEXT NOT NULL CHECK (length(backup_manifest_sha256) = 64),
  s02_semantic_manifest_sha256 TEXT NOT NULL CHECK (length(s02_semantic_manifest_sha256) = 64),
  client_version TEXT NOT NULL,
  upgraded_at_utc TEXT NOT NULL CHECK (upgraded_at_utc GLOB '*Z')
);

CREATE VIEW identity_assertion_effective AS
SELECT a.*, a.rowid AS row_seq,
       coalesce((SELECT e.new_status FROM assertion_status_event e WHERE e.assertion_id = a.assertion_id ORDER BY e.rowid DESC LIMIT 1), a.status) AS effective_status
FROM identity_assertion a;

-- Only a human-anchored resolution can re-activate or withdraw an assertion (S03-B item 3; IA-1).
CREATE TRIGGER assertion_status_event_cause_valid
BEFORE INSERT ON assertion_status_event
WHEN NOT (
  (NEW.cause_kind = 'IDENTITY_CONFLICT_RAISED' AND EXISTS (SELECT 1 FROM identity_conflict c WHERE c.conflict_id = NEW.cause_ref))
  OR
  (NEW.cause_kind = 'HUMAN_RESOLUTION' AND EXISTS (
     SELECT 1 FROM identity_resolution_event r, json_each(r.assertion_updates_json) u
     WHERE r.event_id = NEW.cause_ref AND r.actor GLOB 'OPERATOR:?*'
       AND json_extract(u.value, '$.assertion_id') = NEW.assertion_id AND json_extract(u.value, '$.new_status') = NEW.new_status)))
BEGIN
  SELECT RAISE(ABORT, 'assertion status change needs a recorded conflict or a matching human resolution');
END;

CREATE TRIGGER identity_resolution_event_assertions_in_conflict
BEFORE INSERT ON identity_resolution_event
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.assertion_updates_json) u
  WHERE json_extract(u.value, '$.new_status') NOT IN ('ACTIVE','RETRACTED_ASSERTION')
     OR NOT EXISTS (SELECT 1 FROM identity_conflict c, json_each(c.assertion_ids_json) a
                    WHERE c.conflict_id = NEW.conflict_id AND a.value = json_extract(u.value, '$.assertion_id')))
BEGIN
  SELECT RAISE(ABORT, 'resolution may only update assertions named by its conflict');
END;

-- Immutability: every 0002 table is append-only.
CREATE TRIGGER workspace_registration_no_update BEFORE UPDATE ON workspace_registration BEGIN SELECT RAISE(ABORT, 'immutable: workspace_registration'); END;
CREATE TRIGGER workspace_registration_no_delete BEFORE DELETE ON workspace_registration BEGIN SELECT RAISE(ABORT, 'immutable: workspace_registration'); END;
CREATE TRIGGER workspace_availability_event_no_update BEFORE UPDATE ON workspace_availability_event BEGIN SELECT RAISE(ABORT, 'immutable: workspace_availability_event'); END;
CREATE TRIGGER workspace_availability_event_no_delete BEFORE DELETE ON workspace_availability_event BEGIN SELECT RAISE(ABORT, 'immutable: workspace_availability_event'); END;
CREATE TRIGGER import_event_no_update BEFORE UPDATE ON import_event BEGIN SELECT RAISE(ABORT, 'immutable: import_event'); END;
CREATE TRIGGER import_event_no_delete BEFORE DELETE ON import_event BEGIN SELECT RAISE(ABORT, 'immutable: import_event'); END;
CREATE TRIGGER reprovane_release_import_no_update BEFORE UPDATE ON reprovane_release_import BEGIN SELECT RAISE(ABORT, 'immutable: reprovane_release_import'); END;
CREATE TRIGGER reprovane_release_import_no_delete BEFORE DELETE ON reprovane_release_import BEGIN SELECT RAISE(ABORT, 'immutable: reprovane_release_import'); END;
CREATE TRIGGER project_paper_reference_no_update BEFORE UPDATE ON project_paper_reference BEGIN SELECT RAISE(ABORT, 'immutable: project_paper_reference'); END;
CREATE TRIGGER project_paper_reference_no_delete BEFORE DELETE ON project_paper_reference BEGIN SELECT RAISE(ABORT, 'immutable: project_paper_reference'); END;
CREATE TRIGGER paper_kind_event_no_update BEFORE UPDATE ON paper_kind_event BEGIN SELECT RAISE(ABORT, 'immutable: paper_kind_event'); END;
CREATE TRIGGER paper_kind_event_no_delete BEFORE DELETE ON paper_kind_event BEGIN SELECT RAISE(ABORT, 'immutable: paper_kind_event'); END;
CREATE TRIGGER identity_conflict_no_update BEFORE UPDATE ON identity_conflict BEGIN SELECT RAISE(ABORT, 'immutable: identity_conflict'); END;
CREATE TRIGGER identity_conflict_no_delete BEFORE DELETE ON identity_conflict BEGIN SELECT RAISE(ABORT, 'immutable: identity_conflict'); END;
CREATE TRIGGER identity_resolution_event_no_update BEFORE UPDATE ON identity_resolution_event BEGIN SELECT RAISE(ABORT, 'immutable: identity_resolution_event'); END;
CREATE TRIGGER identity_resolution_event_no_delete BEFORE DELETE ON identity_resolution_event BEGIN SELECT RAISE(ABORT, 'immutable: identity_resolution_event'); END;
CREATE TRIGGER assertion_status_event_no_update BEFORE UPDATE ON assertion_status_event BEGIN SELECT RAISE(ABORT, 'immutable: assertion_status_event'); END;
CREATE TRIGGER assertion_status_event_no_delete BEFORE DELETE ON assertion_status_event BEGIN SELECT RAISE(ABORT, 'immutable: assertion_status_event'); END;
CREATE TRIGGER link_candidate_no_update BEFORE UPDATE ON link_candidate BEGIN SELECT RAISE(ABORT, 'immutable: link_candidate'); END;
CREATE TRIGGER link_candidate_no_delete BEFORE DELETE ON link_candidate BEGIN SELECT RAISE(ABORT, 'immutable: link_candidate'); END;
CREATE TRIGGER link_decision_no_update BEFORE UPDATE ON link_decision BEGIN SELECT RAISE(ABORT, 'immutable: link_decision'); END;
CREATE TRIGGER link_decision_no_delete BEFORE DELETE ON link_decision BEGIN SELECT RAISE(ABORT, 'immutable: link_decision'); END;
CREATE TRIGGER work_family_no_update BEFORE UPDATE ON work_family BEGIN SELECT RAISE(ABORT, 'immutable: work_family'); END;
CREATE TRIGGER work_family_no_delete BEFORE DELETE ON work_family BEGIN SELECT RAISE(ABORT, 'immutable: work_family'); END;
CREATE TRIGGER work_family_member_no_update BEFORE UPDATE ON work_family_member BEGIN SELECT RAISE(ABORT, 'immutable: work_family_member'); END;
CREATE TRIGGER work_family_member_no_delete BEFORE DELETE ON work_family_member BEGIN SELECT RAISE(ABORT, 'immutable: work_family_member'); END;
CREATE TRIGGER work_family_member_event_no_update BEFORE UPDATE ON work_family_member_event BEGIN SELECT RAISE(ABORT, 'immutable: work_family_member_event'); END;
CREATE TRIGGER work_family_member_event_no_delete BEFORE DELETE ON work_family_member_event BEGIN SELECT RAISE(ABORT, 'immutable: work_family_member_event'); END;
CREATE TRIGGER document_version_no_update BEFORE UPDATE ON document_version BEGIN SELECT RAISE(ABORT, 'immutable: document_version'); END;
CREATE TRIGGER document_version_no_delete BEFORE DELETE ON document_version BEGIN SELECT RAISE(ABORT, 'immutable: document_version'); END;
CREATE TRIGGER pdf_asset_no_update BEFORE UPDATE ON pdf_asset BEGIN SELECT RAISE(ABORT, 'immutable: pdf_asset'); END;
CREATE TRIGGER pdf_asset_no_delete BEFORE DELETE ON pdf_asset BEGIN SELECT RAISE(ABORT, 'immutable: pdf_asset'); END;
CREATE TRIGGER asset_validation_event_no_update BEFORE UPDATE ON asset_validation_event BEGIN SELECT RAISE(ABORT, 'immutable: asset_validation_event'); END;
CREATE TRIGGER asset_validation_event_no_delete BEFORE DELETE ON asset_validation_event BEGIN SELECT RAISE(ABORT, 'immutable: asset_validation_event'); END;
CREATE TRIGGER paper_asset_link_no_update BEFORE UPDATE ON paper_asset_link BEGIN SELECT RAISE(ABORT, 'immutable: paper_asset_link'); END;
CREATE TRIGGER paper_asset_link_no_delete BEFORE DELETE ON paper_asset_link BEGIN SELECT RAISE(ABORT, 'immutable: paper_asset_link'); END;
CREATE TRIGGER paper_asset_link_event_no_update BEFORE UPDATE ON paper_asset_link_event BEGIN SELECT RAISE(ABORT, 'immutable: paper_asset_link_event'); END;
CREATE TRIGGER paper_asset_link_event_no_delete BEFORE DELETE ON paper_asset_link_event BEGIN SELECT RAISE(ABORT, 'immutable: paper_asset_link_event'); END;
CREATE TRIGGER rights_assessment_no_update BEFORE UPDATE ON rights_assessment BEGIN SELECT RAISE(ABORT, 'immutable: rights_assessment'); END;
CREATE TRIGGER rights_assessment_no_delete BEFORE DELETE ON rights_assessment BEGIN SELECT RAISE(ABORT, 'immutable: rights_assessment'); END;
CREATE TRIGGER rights_review_task_no_update BEFORE UPDATE ON rights_review_task BEGIN SELECT RAISE(ABORT, 'immutable: rights_review_task'); END;
CREATE TRIGGER rights_review_task_no_delete BEFORE DELETE ON rights_review_task BEGIN SELECT RAISE(ABORT, 'immutable: rights_review_task'); END;
CREATE TRIGGER kb_upgrade_event_no_update BEFORE UPDATE ON kb_upgrade_event BEGIN SELECT RAISE(ABORT, 'immutable: kb_upgrade_event'); END;
CREATE TRIGGER kb_upgrade_event_no_delete BEFORE DELETE ON kb_upgrade_event BEGIN SELECT RAISE(ABORT, 'immutable: kb_upgrade_event'); END;
