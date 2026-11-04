-- SciVetra KB (rkb) migration 0001: S02 metadata and publication-integrity foundation.
-- All tables are append-only; UPDATE and DELETE are refused by triggers (section at end).
-- Contract: S01 v03 03 sections 3, 5, 6 (subset) and 04. Deferred S03 fields are listed in docs/DEFERRED_FIELDS.md.

CREATE TABLE kb_instance (
  instance_id TEXT PRIMARY KEY NOT NULL CHECK (length(instance_id) > 0),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  client_version TEXT NOT NULL
);

CREATE TABLE global_paper (
  paper_id TEXT PRIMARY KEY NOT NULL CHECK (paper_id GLOB 'gp_*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  creation_basis TEXT NOT NULL CHECK (creation_basis IN ('IMPORTED_REPROVANE_ITEM','IMPORTED_BRIDGE_ROW','MANUAL_OPERATOR','METADATA_LOOKUP')),
  creation_evidence_ref TEXT NOT NULL CHECK (length(creation_evidence_ref) > 0),
  manifestation_kind TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK (manifestation_kind IN ('JOURNAL_ARTICLE','PROCEEDINGS_PAPER','PREPRINT','BOOK_CHAPTER','BOOK','THESIS','DATASET','SOFTWARE','REPORT','OTHER','UNKNOWN')),
  identity_state TEXT NOT NULL DEFAULT 'PROVISIONAL' CHECK (identity_state IN ('PROVISIONAL','CONFIRMED','DISPUTED','MERGED_INTO','SPLIT')),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE identity_assertion (
  assertion_id TEXT PRIMARY KEY NOT NULL CHECK (assertion_id GLOB 'ia_*'),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  namespace TEXT NOT NULL CHECK (namespace IN ('doi','openalex_work','pmid','pmcid','arxiv','reprovane_publication_entity','reprovane_corpus_item','other')),
  value_original TEXT NOT NULL,
  value_normalized TEXT,
  parse_result_json TEXT CHECK (parse_result_json IS NULL OR json_valid(parse_result_json)),
  scope_ref TEXT,
  asserted_by TEXT NOT NULL CHECK (asserted_by IN ('PROVIDER_CLAIM','REPROVANE_EXPORT','BRIDGE_MANIFEST','OPERATOR','DOC_TITLE_PAGE')),
  evidence_ref TEXT NOT NULL CHECK (length(evidence_ref) > 0),
  registration_agency TEXT NOT NULL CHECK (registration_agency IN ('CROSSREF','DATACITE','OTHER','UNKNOWN','NOT_APPLICABLE')),
  status TEXT NOT NULL CHECK (status IN ('ACTIVE','RETRACTED_ASSERTION','CONFLICTING')),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (namespace NOT IN ('reprovane_publication_entity','reprovane_corpus_item') OR scope_ref IS NOT NULL),
  CHECK (namespace = 'doi' OR registration_agency = 'NOT_APPLICABLE')
);
CREATE INDEX identity_assertion_lookup ON identity_assertion(namespace, value_normalized);

CREATE TABLE provider_snapshot (
  snapshot_id TEXT PRIMARY KEY NOT NULL CHECK (snapshot_id GLOB 'ps_*'),
  provider TEXT NOT NULL CHECK (provider IN ('DOI_RA','CROSSREF','DATACITE','DOI_CONTENT_NEGOTIATION','OPENALEX_VIA_REPROVANE_EXPORT','OPERATOR_ENTRY')),
  request_identity_json TEXT NOT NULL CHECK (json_valid(request_identity_json) AND instr(request_identity_json, '@') = 0 AND instr(lower(request_identity_json), 'mailto') = 0),
  request_identity_sha256 TEXT NOT NULL CHECK (length(request_identity_sha256) = 64),
  http_status INTEGER CHECK (http_status IS NULL OR (http_status >= 100 AND http_status <= 599)),
  response_bytes_sha256 TEXT CHECK (response_bytes_sha256 IS NULL OR length(response_bytes_sha256) = 64),
  response_media_type TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('FOUND','NOT_FOUND','INVALID_DOI','RATE_LIMITED','TRANSIENT_ERROR','PERMANENT_ERROR','REFUSED_UNSAFE_RESPONSE')),
  outcome_reason TEXT,
  retrieved_at_utc TEXT NOT NULL CHECK (retrieved_at_utc GLOB '*Z'),
  client_version TEXT NOT NULL,
  parser_name TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  supersedes_snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);
CREATE INDEX provider_snapshot_request ON provider_snapshot(provider, request_identity_sha256);

CREATE TABLE fetch_run (
  run_id TEXT PRIMARY KEY NOT NULL CHECK (run_id GLOB 'run_*'),
  started_at_utc TEXT NOT NULL CHECK (started_at_utc GLOB '*Z'),
  mode TEXT NOT NULL CHECK (mode IN ('FIXTURE','LIVE')),
  command TEXT NOT NULL,
  max_requests INTEGER NOT NULL CHECK (max_requests >= 0),
  max_bytes INTEGER NOT NULL CHECK (max_bytes >= 0),
  max_response_bytes INTEGER NOT NULL CHECK (max_response_bytes > 0),
  client_version TEXT NOT NULL
);

CREATE TABLE provider_check (
  check_id TEXT PRIMARY KEY NOT NULL CHECK (check_id GLOB 'chk_*'),
  snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
  run_id TEXT REFERENCES fetch_run(run_id),
  checked_at_utc TEXT NOT NULL CHECK (checked_at_utc GLOB '*Z'),
  result TEXT NOT NULL CHECK (result IN ('NEW_SNAPSHOT','UNCHANGED'))
);

CREATE TABLE request_reservation (
  reservation_id TEXT PRIMARY KEY NOT NULL CHECK (reservation_id GLOB 'rq_*'),
  run_id TEXT NOT NULL REFERENCES fetch_run(run_id),
  provider TEXT NOT NULL,
  purpose TEXT NOT NULL,
  url_identity_sha256 TEXT NOT NULL CHECK (length(url_identity_sha256) = 64),
  attempt INTEGER NOT NULL CHECK (attempt >= 1),
  reserved_bytes INTEGER NOT NULL CHECK (reserved_bytes >= 0),
  reserved_at_utc TEXT NOT NULL CHECK (reserved_at_utc GLOB '*Z')
);

CREATE TABLE request_completion (
  reservation_id TEXT PRIMARY KEY NOT NULL REFERENCES request_reservation(reservation_id),
  http_status INTEGER,
  received_bytes INTEGER NOT NULL CHECK (received_bytes >= 0),
  outcome TEXT NOT NULL,
  completed_at_utc TEXT NOT NULL CHECK (completed_at_utc GLOB '*Z')
);

CREATE TABLE metadata_claim (
  claim_id TEXT PRIMARY KEY NOT NULL CHECK (claim_id GLOB 'mc_*'),
  snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
  subject_namespace TEXT NOT NULL,
  subject_value TEXT NOT NULL,
  field TEXT NOT NULL CHECK (field IN ('title','subtitle','original_title','container_title','issn','isbn','publisher','member','prefix','work_type','author','date_issued','date_published_print','date_published_online','date_published','date_posted','date_accepted','date_created','date_deposited','date_indexed','date_updated','date_registered','volume','issue','pages','language','license','abstract','relation','funder','version','resource_type_general','state','metadata_version','rights')),
  value_json TEXT NOT NULL CHECK (json_valid(value_json)),
  source_field_path TEXT NOT NULL CHECK (source_field_path GLOB '/*'),
  licence_class TEXT NOT NULL CHECK (licence_class IN ('FACT','COPYRIGHTED_ABSTRACT')),
  claim_semantic_sha256 TEXT NOT NULL CHECK (length(claim_semantic_sha256) = 64),
  UNIQUE (snapshot_id, claim_semantic_sha256)
);
CREATE INDEX metadata_claim_subject ON metadata_claim(subject_namespace, subject_value);

CREATE TABLE ra_claim (
  ra_claim_id TEXT PRIMARY KEY NOT NULL CHECK (ra_claim_id GLOB 'ra_*'),
  snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
  doi_key TEXT NOT NULL,
  ra TEXT NOT NULL CHECK (ra IN ('CROSSREF','DATACITE','OTHER','UNKNOWN','NOT_FOUND','INVALID_DOI')),
  ra_raw TEXT,
  status_raw TEXT,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);
CREATE INDEX ra_claim_key ON ra_claim(doi_key);

CREATE TABLE metadata_change_event (
  change_id TEXT PRIMARY KEY NOT NULL CHECK (change_id GLOB 'mce_*'),
  provider TEXT NOT NULL,
  request_identity_sha256 TEXT NOT NULL,
  prior_snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
  new_snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
  diff_json TEXT NOT NULL CHECK (json_valid(diff_json)),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (prior_snapshot_id <> new_snapshot_id)
);

CREATE TABLE integrity_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'ige_*'),
  subject_paper_id TEXT REFERENCES global_paper(paper_id),
  subject_namespace TEXT NOT NULL CHECK (subject_namespace IN ('doi','openalex_work')),
  subject_value TEXT NOT NULL CHECK (length(subject_value) > 0),
  event_type TEXT NOT NULL CHECK (event_type IN ('RETRACTION','CORRECTION','EXPRESSION_OF_CONCERN','WITHDRAWAL','REMOVAL','NEW_VERSION','OTHER')),
  event_subtype TEXT,
  notice_doi TEXT,
  reported_by TEXT NOT NULL CHECK (reported_by IN ('CROSSREF_PUBLISHER_UPDATE','CROSSREF_RETRACTION_WATCH','DATACITE_RELATION','OPENALEX_IS_RETRACTED_VIA_REPROVANE','OPERATOR')),
  record_id TEXT,
  first_snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id),
  source_field_path TEXT,
  operator_evidence_ref TEXT,
  event_date_json TEXT CHECK (event_date_json IS NULL OR json_valid(event_date_json)),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((reported_by = 'OPERATOR') = (first_snapshot_id IS NULL)),
  CHECK (reported_by <> 'OPERATOR' OR length(operator_evidence_ref) > 0),
  CHECK (reported_by = 'OPERATOR' OR source_field_path GLOB '/*')
);
CREATE INDEX integrity_event_subject ON integrity_event(subject_namespace, subject_value);

CREATE TABLE integrity_event_sighting (
  sighting_id TEXT PRIMARY KEY NOT NULL CHECK (sighting_id GLOB 'igs_*'),
  event_id TEXT NOT NULL REFERENCES integrity_event(event_id),
  snapshot_id TEXT NOT NULL REFERENCES provider_snapshot(snapshot_id),
  source_field_path TEXT NOT NULL CHECK (source_field_path GLOB '/*'),
  UNIQUE (event_id, snapshot_id)
);

CREATE TABLE integrity_coverage (
  coverage_id TEXT PRIMARY KEY NOT NULL CHECK (coverage_id GLOB 'igc_*'),
  subject_namespace TEXT NOT NULL CHECK (subject_namespace IN ('doi','openalex_work')),
  subject_value TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('CROSSREF','DATACITE','OPENALEX_VIA_REPROVANE_EXPORT','OPERATOR_CHECK')),
  snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id),
  document_ref TEXT,
  prior_snapshot_id TEXT REFERENCES provider_snapshot(snapshot_id),
  affected_event_ids_json TEXT NOT NULL CHECK (json_valid(affected_event_ids_json) AND json_type(affected_event_ids_json) = 'array'),
  checked_at_utc TEXT NOT NULL CHECK (checked_at_utc GLOB '*Z'),
  outcome TEXT NOT NULL CHECK (outcome IN ('SIGNAL_REPORTED','NO_SIGNAL_REPORTED','REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT','SUBJECT_NOT_FOUND','CHECK_FAILED','NOT_SUPPORTED_BY_PROVIDER')),
  scope_note_json TEXT NOT NULL CHECK (json_valid(scope_note_json) AND json_type(scope_note_json) = 'object'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((provider = 'OPERATOR_CHECK') = (snapshot_id IS NULL)),
  CHECK (provider <> 'OPERATOR_CHECK' OR length(document_ref) > 0),
  CHECK ((outcome = 'REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT') = (prior_snapshot_id IS NOT NULL)),
  CHECK (outcome <> 'REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT' OR json_array_length(affected_event_ids_json) > 0),
  CHECK (outcome = 'REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT' OR json_array_length(affected_event_ids_json) = 0)
);
CREATE INDEX integrity_coverage_subject ON integrity_coverage(subject_namespace, subject_value);

CREATE TABLE operator_document (
  document_sha256 TEXT PRIMARY KEY NOT NULL CHECK (length(document_sha256) = 64),
  media_type TEXT NOT NULL CHECK (media_type IN ('text/plain')),
  byte_length INTEGER NOT NULL CHECK (byte_length > 0),
  label TEXT NOT NULL CHECK (length(label) > 0),
  registered_by TEXT NOT NULL CHECK (registered_by GLOB 'OPERATOR:?*'),
  registered_at_utc TEXT NOT NULL CHECK (registered_at_utc GLOB '*Z')
);

CREATE TABLE integrity_counter_evidence (
  counter_evidence_id TEXT PRIMARY KEY NOT NULL CHECK (counter_evidence_id GLOB 'ice_*'),
  opposes_event_id TEXT NOT NULL REFERENCES integrity_event(event_id),
  kind TEXT NOT NULL CHECK (kind IN ('NOTICE_STATES_ERROR','REINSTATEMENT_NOTICE','DIFFERENT_SUBJECT_DETERMINATION','PROVIDER_AFFIRMATIVE_STATEMENT')),
  primary_document_ref TEXT NOT NULL CHECK (primary_document_ref GLOB 'doi:10.*' OR primary_document_ref GLOB 'sha256:*'),
  evidence_anchor_json TEXT NOT NULL CHECK (json_valid(evidence_anchor_json) AND json_type(evidence_anchor_json) = 'object'),
  statement_summary TEXT NOT NULL CHECK (length(trim(statement_summary)) > 0),
  recorded_by TEXT NOT NULL CHECK (recorded_by GLOB 'OPERATOR:?*'),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z')
);

CREATE TABLE review_task (
  task_id TEXT PRIMARY KEY NOT NULL CHECK (task_id GLOB 'irt_*'),
  subject_namespace TEXT NOT NULL,
  subject_value TEXT NOT NULL,
  trigger TEXT NOT NULL CHECK (trigger IN ('NEW_REPORT','REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT','POSSIBLE_OPPOSING_NOTICE','OPERATOR_REQUEST','DOI_TRAILING_SPACE_UNCERTAIN','DOI_NEAR_DUPLICATE_TRAILING_SPACE','DOI_NOT_FOUND_TRAILING_CHARACTERS')),
  event_ids_json TEXT NOT NULL CHECK (json_valid(event_ids_json) AND json_type(event_ids_json) = 'array'),
  coverage_id TEXT REFERENCES integrity_coverage(coverage_id),
  context_json TEXT NOT NULL CHECK (json_valid(context_json)),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE integrity_review (
  review_id TEXT PRIMARY KEY NOT NULL CHECK (review_id GLOB 'igr_*'),
  subject_namespace TEXT NOT NULL,
  subject_value TEXT NOT NULL,
  task_id TEXT REFERENCES review_task(task_id),
  event_ids_json TEXT NOT NULL CHECK (json_valid(event_ids_json) AND json_type(event_ids_json) = 'array' AND json_array_length(event_ids_json) > 0),
  trigger TEXT NOT NULL CHECK (trigger IN ('NEW_REPORT','REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT','POSSIBLE_OPPOSING_NOTICE','OPERATOR_REQUEST')),
  decision TEXT NOT NULL CHECK (decision IN ('CONFIRMED_REPORT','REPORT_NOT_APPLICABLE_WITH_EVIDENCE','INCONCLUSIVE')),
  counter_evidence_ids_json TEXT NOT NULL CHECK (json_valid(counter_evidence_ids_json) AND json_type(counter_evidence_ids_json) = 'array'),
  reviewer_label TEXT NOT NULL CHECK (reviewer_label GLOB 'OPERATOR:?*'),
  reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  decided_at_utc TEXT NOT NULL CHECK (decided_at_utc GLOB '*Z'),
  CHECK (decision <> 'REPORT_NOT_APPLICABLE_WITH_EVIDENCE' OR json_array_length(counter_evidence_ids_json) > 0)
);

CREATE TABLE normalization_divergence (
  divergence_id TEXT PRIMARY KEY NOT NULL CHECK (divergence_id GLOB 'nd_*'),
  source_ref TEXT NOT NULL,
  rv_value_original TEXT NOT NULL,
  rv_value_normalized TEXT,
  kb_status TEXT NOT NULL CHECK (kb_status IN ('VALID','INVALID','AMBIGUOUS')),
  kb_comparison_key TEXT,
  divergence_kind TEXT NOT NULL,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TABLE identifier_warning (
  warning_id TEXT PRIMARY KEY NOT NULL CHECK (warning_id GLOB 'iw_*'),
  kind TEXT NOT NULL CHECK (kind IN ('NEAR_DUPLICATE_TRAILING_SPACE','UNICODE_NORMALIZATION_VARIANT')),
  key_a TEXT NOT NULL,
  key_b TEXT NOT NULL,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (key_a < key_b)
);

CREATE TABLE reprovane_export_import (
  import_id TEXT PRIMARY KEY NOT NULL CHECK (import_id GLOB 'imp_*'),
  workspace_label TEXT NOT NULL,
  release_id TEXT NOT NULL,
  manifest_bytes_sha256 TEXT NOT NULL CHECK (length(manifest_bytes_sha256) = 64),
  corpus_jsonl_bytes_sha256 TEXT NOT NULL CHECK (length(corpus_jsonl_bytes_sha256) = 64),
  sums_bytes_sha256 TEXT NOT NULL CHECK (length(sums_bytes_sha256) = 64),
  item_count INTEGER NOT NULL CHECK (item_count >= 0),
  imported_at_utc TEXT NOT NULL CHECK (imported_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

-- Persistence-boundary rules for human-only, anchored counter-evidence (S01 v03 03 section 5.3.3, IC-1).
CREATE TRIGGER integrity_counter_evidence_anchor_required
BEFORE INSERT ON integrity_counter_evidence
WHEN NOT (
  (json_extract(NEW.evidence_anchor_json, '$.snapshot_id') IS NOT NULL
     AND json_extract(NEW.evidence_anchor_json, '$.source_field_path') GLOB '/*'
     AND EXISTS (SELECT 1 FROM provider_snapshot s WHERE s.snapshot_id = json_extract(NEW.evidence_anchor_json, '$.snapshot_id')))
  OR
  (json_extract(NEW.evidence_anchor_json, '$.document_sha256') IS NOT NULL
     AND json_type(NEW.evidence_anchor_json, '$.page') = 'integer'
     AND json_type(NEW.evidence_anchor_json, '$.char_start') = 'integer'
     AND json_type(NEW.evidence_anchor_json, '$.char_end') = 'integer'
     AND json_extract(NEW.evidence_anchor_json, '$.char_end') > json_extract(NEW.evidence_anchor_json, '$.char_start')
     AND EXISTS (SELECT 1 FROM operator_document d WHERE d.document_sha256 = json_extract(NEW.evidence_anchor_json, '$.document_sha256')))
)
BEGIN
  SELECT RAISE(ABORT, 'counter-evidence anchor unresolvable');
END;

CREATE TRIGGER integrity_counter_evidence_document_required
BEFORE INSERT ON integrity_counter_evidence
WHEN NEW.primary_document_ref GLOB 'sha256:*'
  AND NOT EXISTS (SELECT 1 FROM operator_document d WHERE 'sha256:' || d.document_sha256 = NEW.primary_document_ref)
BEGIN
  SELECT RAISE(ABORT, 'counter-evidence primary document not registered');
END;

CREATE TRIGGER integrity_review_events_exist
BEFORE INSERT ON integrity_review
WHEN EXISTS (SELECT 1 FROM json_each(NEW.event_ids_json) j WHERE NOT EXISTS (SELECT 1 FROM integrity_event e WHERE e.event_id = j.value))
BEGIN
  SELECT RAISE(ABORT, 'review references unknown integrity event');
END;

CREATE TRIGGER integrity_review_counter_evidence_valid
BEFORE INSERT ON integrity_review
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.counter_evidence_ids_json) c
  WHERE NOT EXISTS (
    SELECT 1 FROM integrity_counter_evidence ce, json_each(NEW.event_ids_json) e
    WHERE ce.counter_evidence_id = c.value AND ce.opposes_event_id = e.value))
BEGIN
  SELECT RAISE(ABORT, 'review counter-evidence does not oppose a reviewed event');
END;

-- Immutability: every table is append-only.
CREATE TRIGGER kb_instance_no_update BEFORE UPDATE ON kb_instance BEGIN SELECT RAISE(ABORT, 'immutable: kb_instance'); END;
CREATE TRIGGER kb_instance_no_delete BEFORE DELETE ON kb_instance BEGIN SELECT RAISE(ABORT, 'immutable: kb_instance'); END;
CREATE TRIGGER global_paper_no_update BEFORE UPDATE ON global_paper BEGIN SELECT RAISE(ABORT, 'immutable: global_paper'); END;
CREATE TRIGGER global_paper_no_delete BEFORE DELETE ON global_paper BEGIN SELECT RAISE(ABORT, 'immutable: global_paper'); END;
CREATE TRIGGER identity_assertion_no_update BEFORE UPDATE ON identity_assertion BEGIN SELECT RAISE(ABORT, 'immutable: identity_assertion'); END;
CREATE TRIGGER identity_assertion_no_delete BEFORE DELETE ON identity_assertion BEGIN SELECT RAISE(ABORT, 'immutable: identity_assertion'); END;
CREATE TRIGGER provider_snapshot_no_update BEFORE UPDATE ON provider_snapshot BEGIN SELECT RAISE(ABORT, 'immutable: provider_snapshot'); END;
CREATE TRIGGER provider_snapshot_no_delete BEFORE DELETE ON provider_snapshot BEGIN SELECT RAISE(ABORT, 'immutable: provider_snapshot'); END;
CREATE TRIGGER fetch_run_no_update BEFORE UPDATE ON fetch_run BEGIN SELECT RAISE(ABORT, 'immutable: fetch_run'); END;
CREATE TRIGGER fetch_run_no_delete BEFORE DELETE ON fetch_run BEGIN SELECT RAISE(ABORT, 'immutable: fetch_run'); END;
CREATE TRIGGER provider_check_no_update BEFORE UPDATE ON provider_check BEGIN SELECT RAISE(ABORT, 'immutable: provider_check'); END;
CREATE TRIGGER provider_check_no_delete BEFORE DELETE ON provider_check BEGIN SELECT RAISE(ABORT, 'immutable: provider_check'); END;
CREATE TRIGGER request_reservation_no_update BEFORE UPDATE ON request_reservation BEGIN SELECT RAISE(ABORT, 'immutable: request_reservation'); END;
CREATE TRIGGER request_reservation_no_delete BEFORE DELETE ON request_reservation BEGIN SELECT RAISE(ABORT, 'immutable: request_reservation'); END;
CREATE TRIGGER request_completion_no_update BEFORE UPDATE ON request_completion BEGIN SELECT RAISE(ABORT, 'immutable: request_completion'); END;
CREATE TRIGGER request_completion_no_delete BEFORE DELETE ON request_completion BEGIN SELECT RAISE(ABORT, 'immutable: request_completion'); END;
CREATE TRIGGER metadata_claim_no_update BEFORE UPDATE ON metadata_claim BEGIN SELECT RAISE(ABORT, 'immutable: metadata_claim'); END;
CREATE TRIGGER metadata_claim_no_delete BEFORE DELETE ON metadata_claim BEGIN SELECT RAISE(ABORT, 'immutable: metadata_claim'); END;
CREATE TRIGGER ra_claim_no_update BEFORE UPDATE ON ra_claim BEGIN SELECT RAISE(ABORT, 'immutable: ra_claim'); END;
CREATE TRIGGER ra_claim_no_delete BEFORE DELETE ON ra_claim BEGIN SELECT RAISE(ABORT, 'immutable: ra_claim'); END;
CREATE TRIGGER metadata_change_event_no_update BEFORE UPDATE ON metadata_change_event BEGIN SELECT RAISE(ABORT, 'immutable: metadata_change_event'); END;
CREATE TRIGGER metadata_change_event_no_delete BEFORE DELETE ON metadata_change_event BEGIN SELECT RAISE(ABORT, 'immutable: metadata_change_event'); END;
CREATE TRIGGER integrity_event_no_update BEFORE UPDATE ON integrity_event BEGIN SELECT RAISE(ABORT, 'immutable: integrity_event'); END;
CREATE TRIGGER integrity_event_no_delete BEFORE DELETE ON integrity_event BEGIN SELECT RAISE(ABORT, 'immutable: integrity_event'); END;
CREATE TRIGGER integrity_event_sighting_no_update BEFORE UPDATE ON integrity_event_sighting BEGIN SELECT RAISE(ABORT, 'immutable: integrity_event_sighting'); END;
CREATE TRIGGER integrity_event_sighting_no_delete BEFORE DELETE ON integrity_event_sighting BEGIN SELECT RAISE(ABORT, 'immutable: integrity_event_sighting'); END;
CREATE TRIGGER integrity_coverage_no_update BEFORE UPDATE ON integrity_coverage BEGIN SELECT RAISE(ABORT, 'immutable: integrity_coverage'); END;
CREATE TRIGGER integrity_coverage_no_delete BEFORE DELETE ON integrity_coverage BEGIN SELECT RAISE(ABORT, 'immutable: integrity_coverage'); END;
CREATE TRIGGER operator_document_no_update BEFORE UPDATE ON operator_document BEGIN SELECT RAISE(ABORT, 'immutable: operator_document'); END;
CREATE TRIGGER operator_document_no_delete BEFORE DELETE ON operator_document BEGIN SELECT RAISE(ABORT, 'immutable: operator_document'); END;
CREATE TRIGGER integrity_counter_evidence_no_update BEFORE UPDATE ON integrity_counter_evidence BEGIN SELECT RAISE(ABORT, 'immutable: integrity_counter_evidence'); END;
CREATE TRIGGER integrity_counter_evidence_no_delete BEFORE DELETE ON integrity_counter_evidence BEGIN SELECT RAISE(ABORT, 'immutable: integrity_counter_evidence'); END;
CREATE TRIGGER review_task_no_update BEFORE UPDATE ON review_task BEGIN SELECT RAISE(ABORT, 'immutable: review_task'); END;
CREATE TRIGGER review_task_no_delete BEFORE DELETE ON review_task BEGIN SELECT RAISE(ABORT, 'immutable: review_task'); END;
CREATE TRIGGER integrity_review_no_update BEFORE UPDATE ON integrity_review BEGIN SELECT RAISE(ABORT, 'immutable: integrity_review'); END;
CREATE TRIGGER integrity_review_no_delete BEFORE DELETE ON integrity_review BEGIN SELECT RAISE(ABORT, 'immutable: integrity_review'); END;
CREATE TRIGGER normalization_divergence_no_update BEFORE UPDATE ON normalization_divergence BEGIN SELECT RAISE(ABORT, 'immutable: normalization_divergence'); END;
CREATE TRIGGER normalization_divergence_no_delete BEFORE DELETE ON normalization_divergence BEGIN SELECT RAISE(ABORT, 'immutable: normalization_divergence'); END;
CREATE TRIGGER identifier_warning_no_update BEFORE UPDATE ON identifier_warning BEGIN SELECT RAISE(ABORT, 'immutable: identifier_warning'); END;
CREATE TRIGGER identifier_warning_no_delete BEFORE DELETE ON identifier_warning BEGIN SELECT RAISE(ABORT, 'immutable: identifier_warning'); END;
CREATE TRIGGER reprovane_export_import_no_update BEFORE UPDATE ON reprovane_export_import BEGIN SELECT RAISE(ABORT, 'immutable: reprovane_export_import'); END;
CREATE TRIGGER reprovane_export_import_no_delete BEFORE DELETE ON reprovane_export_import BEGIN SELECT RAISE(ABORT, 'immutable: reprovane_export_import'); END;
CREATE TRIGGER schema_migration_no_update BEFORE UPDATE ON schema_migration BEGIN SELECT RAISE(ABORT, 'immutable: schema_migration'); END;
CREATE TRIGGER schema_migration_no_delete BEFORE DELETE ON schema_migration BEGIN SELECT RAISE(ABORT, 'immutable: schema_migration'); END;
