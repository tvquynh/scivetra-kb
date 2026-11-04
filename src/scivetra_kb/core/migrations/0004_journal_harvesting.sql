-- SciVetra KB migration 0004: S04J Journal DOI Harvesting (ADR-JH-001). Purely additive: no 0001/0002/0003 object is altered.
-- Every table is append-only (no UPDATE / DELETE). Run state, membership and read views are projections over these rows.
-- A journal inventory is DISCOVERY EVIDENCE only: nothing here is an SLR inclusion, a screening decision, a sealed Reprovane
-- corpus member, an identity-confirmed PDF or a download entitlement. No column below can express any of those.

-- One journal collection, identified by its sorted, validated, operator-requested ISSN set.
CREATE TABLE jh_journal (
  journal_id TEXT PRIMARY KEY NOT NULL CHECK (journal_id GLOB 'jj_*'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  requested_issns_json TEXT NOT NULL CHECK (json_valid(requested_issns_json) AND json_type(requested_issns_json) = 'array'
                                            AND json_array_length(requested_issns_json) BETWEEN 1 AND 8),
  collection_key TEXT NOT NULL UNIQUE CHECK (length(collection_key) = 64),
  work_type_policy_json TEXT NOT NULL CHECK (json_valid(work_type_policy_json) AND json_type(work_type_policy_json) = 'object'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- A provider request sequence for one journal. query_json never holds a contact address or a credential.
CREATE TABLE jh_run (
  run_id TEXT PRIMARY KEY NOT NULL CHECK (run_id GLOB 'jhr_*'),
  journal_id TEXT NOT NULL REFERENCES jh_journal(journal_id),
  provider TEXT NOT NULL CHECK (provider IN ('CROSSREF_JOURNAL_LOOKUP','CROSSREF','OPENALEX_SOURCE_LOOKUP','OPENALEX','OPENALEX_ALTERNATE')),
  mode TEXT NOT NULL CHECK (mode IN ('RESOLVE','FULL','INCREMENTAL')),
  fetch_run_id TEXT NOT NULL REFERENCES fetch_run(run_id),
  query_json TEXT NOT NULL CHECK (json_valid(query_json) AND json_type(query_json) = 'object' AND instr(query_json, '@') = 0
                                  AND instr(lower(query_json), 'mailto') = 0 AND instr(lower(query_json), 'api_key') = 0),
  query_sha256 TEXT NOT NULL CHECK (length(query_sha256) = 64),
  adapter_name TEXT NOT NULL,
  adapter_version TEXT NOT NULL,
  transport_mode TEXT NOT NULL CHECK (transport_mode IN ('SIMULATED','LIVE')),
  caps_json TEXT NOT NULL CHECK (json_valid(caps_json) AND json_type(caps_json) = 'object'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  started_at_utc TEXT NOT NULL CHECK (started_at_utc GLOB '*Z'),
  client_version TEXT NOT NULL,
  CHECK ((provider IN ('CROSSREF_JOURNAL_LOOKUP','OPENALEX_SOURCE_LOOKUP')) = (mode = 'RESOLVE'))
);
CREATE INDEX jh_run_journal ON jh_run(journal_id, provider);

-- Run history. A run without a FINISHED event is unfinished (interrupted or running) and is never COMPLETE.
CREATE TABLE jh_run_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'jhe_*'),
  run_id TEXT NOT NULL REFERENCES jh_run(run_id),
  seq INTEGER NOT NULL CHECK (seq >= 1),
  kind TEXT NOT NULL CHECK (kind IN ('STARTED','RESUMED','CURSOR_INVALID','REPLAY_STARTED','PAGE_FAILED','COUNT_DRIFT','FINISHED')),
  state TEXT CHECK (state IS NULL OR state IN ('COMPLETE','PARTIAL','BLOCKED')),
  stop_reason TEXT,
  fetch_run_id TEXT REFERENCES fetch_run(run_id),
  detail_json TEXT NOT NULL CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  UNIQUE (run_id, seq),
  CHECK ((kind = 'FINISHED') = (state IS NOT NULL)),
  CHECK (kind <> 'FINISHED' OR length(stop_reason) > 0)
);

-- One provider response (or one refused/failed request). Raw response bytes live in CAS (response_bytes_sha256).
CREATE TABLE jh_page (
  page_id TEXT PRIMARY KEY NOT NULL CHECK (page_id GLOB 'jhp_*'),
  run_id TEXT NOT NULL REFERENCES jh_run(run_id),
  seq INTEGER NOT NULL CHECK (seq >= 1),
  pass_no INTEGER NOT NULL CHECK (pass_no >= 0),
  kind TEXT NOT NULL CHECK (kind IN ('COUNT_PROBE','JOURNAL_LOOKUP','SOURCE_LOOKUP','WORKS_PAGE')),
  request_identity_json TEXT NOT NULL CHECK (json_valid(request_identity_json) AND instr(request_identity_json, '@') = 0
                                             AND instr(lower(request_identity_json), 'mailto') = 0
                                             AND instr(lower(request_identity_json), 'api_key') = 0),
  request_identity_sha256 TEXT NOT NULL CHECK (length(request_identity_sha256) = 64),
  cursor_in TEXT,
  cursor_out TEXT,
  http_status INTEGER CHECK (http_status IS NULL OR (http_status >= 100 AND http_status <= 599)),
  attempts INTEGER NOT NULL CHECK (attempts >= 0),
  response_bytes_sha256 TEXT CHECK (response_bytes_sha256 IS NULL OR length(response_bytes_sha256) = 64),
  response_media_type TEXT,
  items_returned INTEGER CHECK (items_returned IS NULL OR items_returned >= 0),
  items_new INTEGER CHECK (items_new IS NULL OR items_new >= 0),
  outcome TEXT NOT NULL CHECK (outcome IN ('OK','NOT_FOUND','CURSOR_INVALID','HTTP_ERROR','UNPARSEABLE','REFUSED','TRANSPORT_FAILED',
                                           'RATE_LIMITED','BUDGET_EXHAUSTED','CREDENTIAL_REJECTED','NOT_SENT_CREDENTIAL_REQUIRED')),
  outcome_reason TEXT,
  retrieved_at_utc TEXT NOT NULL CHECK (retrieved_at_utc GLOB '*Z'),
  UNIQUE (run_id, seq),
  CHECK (outcome <> 'OK' OR (response_bytes_sha256 IS NOT NULL AND items_returned IS NOT NULL AND items_new IS NOT NULL))
);

-- Provider-reported counts are timestamped snapshots, never an inventory assertion (scope is pinned).
CREATE TABLE jh_count_observation (
  count_id TEXT PRIMARY KEY NOT NULL CHECK (count_id GLOB 'jhc_*'),
  journal_id TEXT NOT NULL REFERENCES jh_journal(journal_id),
  run_id TEXT REFERENCES jh_run(run_id),
  page_id TEXT REFERENCES jh_page(page_id),
  provider TEXT NOT NULL CHECK (provider IN ('CROSSREF','OPENALEX','OWNER_REPORTED')),
  basis TEXT NOT NULL CHECK (basis IN ('CROSSREF_TOTAL_RESULTS','OPENALEX_META_COUNT','OWNER_HISTORICAL_OBSERVATION')),
  reported_count INTEGER NOT NULL CHECK (reported_count >= 0),
  observed_at TEXT NOT NULL CHECK (length(observed_at) >= 10),
  scope TEXT NOT NULL CHECK (scope = 'SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION'),
  note TEXT,
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((provider = 'OWNER_REPORTED') = (page_id IS NULL)),
  CHECK ((provider = 'OWNER_REPORTED') = (basis = 'OWNER_HISTORICAL_OBSERVATION'))
);

-- ISSN facts with their evidence. A relationship (print/electronic/linking) exists only where a provider said so.
CREATE TABLE jh_issn_evidence (
  evidence_id TEXT PRIMARY KEY NOT NULL CHECK (evidence_id GLOB 'jie_*'),
  journal_id TEXT NOT NULL REFERENCES jh_journal(journal_id),
  issn TEXT NOT NULL CHECK (issn GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9X]'),
  issn_role TEXT NOT NULL CHECK (issn_role IN ('REQUESTED','PRINT','ELECTRONIC','LINKING','UNSPECIFIED')),
  provider TEXT NOT NULL CHECK (provider IN ('OPERATOR','CROSSREF','OPENALEX')),
  page_id TEXT REFERENCES jh_page(page_id),
  source_field_path TEXT,
  provider_source_id TEXT,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((provider = 'OPERATOR') = (page_id IS NULL)),
  CHECK (provider = 'OPERATOR' OR source_field_path GLOB '/*'),
  CHECK ((provider = 'OPERATOR') = (issn_role = 'REQUESTED'))
);

-- Provider source/journal records matched by exact ISSN evidence (never by title or DOI prefix).
CREATE TABLE jh_source_resolution (
  resolution_id TEXT PRIMARY KEY NOT NULL CHECK (resolution_id GLOB 'jsr_*'),
  journal_id TEXT NOT NULL REFERENCES jh_journal(journal_id),
  run_id TEXT NOT NULL REFERENCES jh_run(run_id),
  page_id TEXT REFERENCES jh_page(page_id),
  provider TEXT NOT NULL CHECK (provider IN ('CROSSREF','OPENALEX')),
  provider_source_id TEXT NOT NULL CHECK (length(provider_source_id) > 0),
  display_name TEXT,
  issn_l TEXT,
  issns_json TEXT NOT NULL CHECK (json_valid(issns_json) AND json_type(issns_json) = 'array'),
  matched_issns_json TEXT NOT NULL CHECK (json_valid(matched_issns_json) AND json_type(matched_issns_json) = 'array'),
  basis TEXT NOT NULL CHECK (basis IN ('ISSN_EXACT_PROVIDER_EVIDENCE','OPERATOR_SELECTED_AMONG_CANDIDATES')),
  status TEXT NOT NULL CHECK (status IN ('SELECTED','CANDIDATE_AMBIGUOUS')),
  source_field_path TEXT,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (basis <> 'ISSN_EXACT_PROVIDER_EVIDENCE' OR json_array_length(matched_issns_json) > 0)
);

-- One work record as one provider returned it in one run. De-duplicated within a run by content (replays, page overlap).
CREATE TABLE jh_observation (
  observation_id TEXT PRIMARY KEY NOT NULL CHECK (observation_id GLOB 'jho_*'),
  run_id TEXT NOT NULL REFERENCES jh_run(run_id),
  page_id TEXT NOT NULL REFERENCES jh_page(page_id),
  item_index INTEGER NOT NULL CHECK (item_index >= 0),
  item_pointer TEXT NOT NULL CHECK (item_pointer GLOB '/*'),
  provider TEXT NOT NULL CHECK (provider IN ('CROSSREF','OPENALEX')),
  venue_evidence TEXT NOT NULL CHECK (venue_evidence IN ('CROSSREF_JOURNAL_ENDPOINT','OPENALEX_PRIMARY_LOCATION','OPENALEX_ANY_LOCATION')),
  provider_record_id TEXT,
  doi_original TEXT,
  doi_status TEXT NOT NULL CHECK (doi_status IN ('VALID','INVALID','AMBIGUOUS','MISSING')),
  doi_key TEXT,
  doi_parse_json TEXT CHECK (doi_parse_json IS NULL OR json_valid(doi_parse_json)),
  shape_status TEXT NOT NULL CHECK (shape_status IN ('OK','MALFORMED')),
  shape_reason TEXT,
  work_type_raw TEXT,
  item_sha256 TEXT NOT NULL CHECK (length(item_sha256) = 64),
  observed_at_utc TEXT NOT NULL CHECK (observed_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((doi_status = 'VALID') = (doi_key IS NOT NULL)),
  CHECK ((doi_status = 'MISSING') = (doi_original IS NULL)),
  CHECK ((provider = 'CROSSREF') = (venue_evidence = 'CROSSREF_JOURNAL_ENDPOINT'))
);
CREATE INDEX jh_observation_key ON jh_observation(doi_key);
CREATE INDEX jh_observation_run ON jh_observation(run_id);

-- Field-level source assertions decomposed from one observation (value + JSON pointer into the raw page).
CREATE TABLE jh_field_claim (
  claim_id TEXT PRIMARY KEY NOT NULL CHECK (claim_id GLOB 'jfc_*'),
  observation_id TEXT NOT NULL REFERENCES jh_observation(observation_id),
  field TEXT NOT NULL CHECK (field IN ('title','subtitle','original_title','container_title','issn','isbn','publisher','member','prefix',
    'work_type','author','date_issued','date_published_print','date_published_online','date_published','date_posted','date_accepted',
    'date_created','date_deposited','date_indexed','publication_year','publication_date','volume','issue','pages','article_number','language','license',
    'relation','integrity_signal','is_retracted','primary_source','location','best_oa_location','open_access','landing_page',
    'provider_updated_at','provider_created_at')),
  value_json TEXT NOT NULL CHECK (json_valid(value_json)),
  source_field_path TEXT NOT NULL CHECK (source_field_path GLOB '/*'),
  claim_semantic_sha256 TEXT NOT NULL CHECK (length(claim_semantic_sha256) = 64),
  UNIQUE (observation_id, claim_semantic_sha256)
);
CREATE INDEX jh_field_claim_obs ON jh_field_claim(observation_id);

-- A versioned journal collection: the reconciliation of named runs under named policies at one time.
CREATE TABLE jh_collection_version (
  version_id TEXT PRIMARY KEY NOT NULL CHECK (version_id GLOB 'jcv_*'),
  journal_id TEXT NOT NULL REFERENCES jh_journal(journal_id),
  version_no INTEGER NOT NULL CHECK (version_no >= 1),
  basis_json TEXT NOT NULL CHECK (json_valid(basis_json) AND json_type(basis_json) = 'object'),
  policy_json TEXT NOT NULL CHECK (json_valid(policy_json) AND json_type(policy_json) = 'object'),
  coverage_json TEXT NOT NULL CHECK (json_valid(coverage_json) AND json_type(coverage_json) = 'object'
                                     AND instr(coverage_json, 'ALL_JOURNAL_ARTICLES_FOUND') = 0),
  collection_state TEXT NOT NULL CHECK (collection_state IN ('COMPLETE_RELATIVE_TO_RECORDED_QUERIES','PARTIAL','BLOCKED')),
  member_count INTEGER NOT NULL CHECK (member_count >= 0),
  members_sha256 TEXT NOT NULL CHECK (length(members_sha256) = 64),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (journal_id, version_no)
);

-- One subject (a DOI comparison key, or one DOI-less / unusable-DOI provider record) of a collection version.
-- membership_asserted = 1 only on provider venue evidence (Crossref journal endpoint or OpenAlex primary location).
CREATE TABLE jh_member (
  member_id TEXT PRIMARY KEY NOT NULL CHECK (member_id GLOB 'jcm_*'),
  version_id TEXT NOT NULL REFERENCES jh_collection_version(version_id),
  subject_kind TEXT NOT NULL CHECK (subject_kind IN ('DOI','RECORD_WITHOUT_USABLE_DOI')),
  doi_key TEXT,
  record_ref TEXT,
  category TEXT NOT NULL CHECK (category IN ('BOTH','CROSSREF_ONLY','OPENALEX_ONLY','OPENALEX_ALTERNATE_LOCATION_ONLY',
                                             'NO_DOI','DOI_AMBIGUOUS','DOI_INVALID','NOT_SEEN_IN_LATEST_RUNS')),
  membership_asserted INTEGER NOT NULL CHECK (membership_asserted IN (0, 1)),
  providers_json TEXT NOT NULL CHECK (json_valid(providers_json) AND json_type(providers_json) = 'array'),
  evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json) AND json_type(evidence_json) = 'object'),
  projection_json TEXT NOT NULL CHECK (json_valid(projection_json) AND json_type(projection_json) = 'object'),
  provenance_json TEXT NOT NULL CHECK (json_valid(provenance_json) AND json_type(provenance_json) = 'object'),
  flags_json TEXT NOT NULL CHECK (json_valid(flags_json) AND json_type(flags_json) = 'array'),
  paper_candidates_json TEXT NOT NULL CHECK (json_valid(paper_candidates_json) AND json_type(paper_candidates_json) = 'array'),
  semantic_sha256 TEXT NOT NULL CHECK (length(semantic_sha256) = 64),
  UNIQUE (version_id, semantic_sha256),
  CHECK ((subject_kind = 'DOI') = (doi_key IS NOT NULL)),
  CHECK ((subject_kind = 'DOI') = (record_ref IS NULL)),
  CHECK (category NOT IN ('OPENALEX_ALTERNATE_LOCATION_ONLY','NOT_SEEN_IN_LATEST_RUNS') OR membership_asserted = 0)
);
CREATE INDEX jh_member_version ON jh_member(version_id);

-- Disagreements between assertions; every value is kept with its source. Nothing is overwritten.
CREATE TABLE jh_conflict (
  conflict_id TEXT PRIMARY KEY NOT NULL CHECK (conflict_id GLOB 'jcf_*'),
  version_id TEXT NOT NULL REFERENCES jh_collection_version(version_id),
  subject TEXT NOT NULL CHECK (length(subject) > 0),
  field TEXT NOT NULL,
  kind TEXT NOT NULL,
  values_json TEXT NOT NULL CHECK (json_valid(values_json) AND json_type(values_json) = 'array' AND json_array_length(values_json) >= 2),
  semantic_sha256 TEXT NOT NULL CHECK (length(semantic_sha256) = 64),
  UNIQUE (version_id, semantic_sha256)
);

-- Human review queue for the journal inventory. Created once per (journal, subject, trigger, context).
CREATE TABLE jh_review_task (
  task_id TEXT PRIMARY KEY NOT NULL CHECK (task_id GLOB 'jrt_*'),
  journal_id TEXT NOT NULL REFERENCES jh_journal(journal_id),
  first_version_id TEXT REFERENCES jh_collection_version(version_id),
  subject TEXT NOT NULL CHECK (length(subject) > 0),
  trigger TEXT NOT NULL CHECK (trigger IN ('FIELD_CONFLICT','VENUE_MISMATCH','WORK_TYPE_VERSION_MISMATCH','DUPLICATE_PROVIDER_RECORDS_SAME_DOI',
    'DOI_AMBIGUOUS','DOI_INVALID','DOI_NEAR_DUPLICATE_TRAILING_SPACE','DOI_UNICODE_NORMALIZATION_VARIANT','INTEGRITY_SIGNAL_REPORTED',
    'INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT','VERSION_LINK_CANDIDATE','NOT_SEEN_IN_LATEST_COMPLETE_RUN','SOURCE_AMBIGUOUS',
    'ISSN_RELATIONSHIP_CONFLICT','MALFORMED_PROVIDER_RECORD','ALTERNATE_LOCATION_ONLY')),
  context_json TEXT NOT NULL CHECK (json_valid(context_json) AND json_type(context_json) = 'object'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

-- One export of one collection version (files are written outside the KB; their hashes are recorded here).
CREATE TABLE jh_export (
  export_id TEXT PRIMARY KEY NOT NULL CHECK (export_id GLOB 'jx_*'),
  version_id TEXT NOT NULL REFERENCES jh_collection_version(version_id),
  profile TEXT NOT NULL CHECK (profile = 'jh-export/1'),
  filter_json TEXT NOT NULL CHECK (json_valid(filter_json) AND json_type(filter_json) = 'object'),
  files_json TEXT NOT NULL CHECK (json_valid(files_json) AND json_type(files_json) = 'object'),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  out_dir_path_sha256 TEXT NOT NULL CHECK (length(out_dir_path_sha256) = 64),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

-- Persistence-boundary rules.
CREATE TRIGGER jh_run_event_after_finished
BEFORE INSERT ON jh_run_event
WHEN EXISTS (SELECT 1 FROM jh_run_event e WHERE e.run_id = NEW.run_id AND e.kind = 'FINISHED')
BEGIN
  SELECT RAISE(ABORT, 'journal run already finished');
END;

CREATE TRIGGER jh_page_after_finished
BEFORE INSERT ON jh_page
WHEN EXISTS (SELECT 1 FROM jh_run_event e WHERE e.run_id = NEW.run_id AND e.kind = 'FINISHED')
BEGIN
  SELECT RAISE(ABORT, 'journal run already finished');
END;

CREATE TRIGGER jh_observation_page_of_run
BEFORE INSERT ON jh_observation
WHEN NOT EXISTS (SELECT 1 FROM jh_page p WHERE p.page_id = NEW.page_id AND p.run_id = NEW.run_id AND p.outcome = 'OK')
BEGIN
  SELECT RAISE(ABORT, 'observation must come from an OK page of the same run');
END;

-- Immutability: every journal table is append-only.
CREATE TRIGGER jh_journal_no_update BEFORE UPDATE ON jh_journal BEGIN SELECT RAISE(ABORT, 'immutable: jh_journal'); END;
CREATE TRIGGER jh_journal_no_delete BEFORE DELETE ON jh_journal BEGIN SELECT RAISE(ABORT, 'immutable: jh_journal'); END;
CREATE TRIGGER jh_run_no_update BEFORE UPDATE ON jh_run BEGIN SELECT RAISE(ABORT, 'immutable: jh_run'); END;
CREATE TRIGGER jh_run_no_delete BEFORE DELETE ON jh_run BEGIN SELECT RAISE(ABORT, 'immutable: jh_run'); END;
CREATE TRIGGER jh_run_event_no_update BEFORE UPDATE ON jh_run_event BEGIN SELECT RAISE(ABORT, 'immutable: jh_run_event'); END;
CREATE TRIGGER jh_run_event_no_delete BEFORE DELETE ON jh_run_event BEGIN SELECT RAISE(ABORT, 'immutable: jh_run_event'); END;
CREATE TRIGGER jh_page_no_update BEFORE UPDATE ON jh_page BEGIN SELECT RAISE(ABORT, 'immutable: jh_page'); END;
CREATE TRIGGER jh_page_no_delete BEFORE DELETE ON jh_page BEGIN SELECT RAISE(ABORT, 'immutable: jh_page'); END;
CREATE TRIGGER jh_count_observation_no_update BEFORE UPDATE ON jh_count_observation BEGIN SELECT RAISE(ABORT, 'immutable: jh_count_observation'); END;
CREATE TRIGGER jh_count_observation_no_delete BEFORE DELETE ON jh_count_observation BEGIN SELECT RAISE(ABORT, 'immutable: jh_count_observation'); END;
CREATE TRIGGER jh_issn_evidence_no_update BEFORE UPDATE ON jh_issn_evidence BEGIN SELECT RAISE(ABORT, 'immutable: jh_issn_evidence'); END;
CREATE TRIGGER jh_issn_evidence_no_delete BEFORE DELETE ON jh_issn_evidence BEGIN SELECT RAISE(ABORT, 'immutable: jh_issn_evidence'); END;
CREATE TRIGGER jh_source_resolution_no_update BEFORE UPDATE ON jh_source_resolution BEGIN SELECT RAISE(ABORT, 'immutable: jh_source_resolution'); END;
CREATE TRIGGER jh_source_resolution_no_delete BEFORE DELETE ON jh_source_resolution BEGIN SELECT RAISE(ABORT, 'immutable: jh_source_resolution'); END;
CREATE TRIGGER jh_observation_no_update BEFORE UPDATE ON jh_observation BEGIN SELECT RAISE(ABORT, 'immutable: jh_observation'); END;
CREATE TRIGGER jh_observation_no_delete BEFORE DELETE ON jh_observation BEGIN SELECT RAISE(ABORT, 'immutable: jh_observation'); END;
CREATE TRIGGER jh_field_claim_no_update BEFORE UPDATE ON jh_field_claim BEGIN SELECT RAISE(ABORT, 'immutable: jh_field_claim'); END;
CREATE TRIGGER jh_field_claim_no_delete BEFORE DELETE ON jh_field_claim BEGIN SELECT RAISE(ABORT, 'immutable: jh_field_claim'); END;
CREATE TRIGGER jh_collection_version_no_update BEFORE UPDATE ON jh_collection_version BEGIN SELECT RAISE(ABORT, 'immutable: jh_collection_version'); END;
CREATE TRIGGER jh_collection_version_no_delete BEFORE DELETE ON jh_collection_version BEGIN SELECT RAISE(ABORT, 'immutable: jh_collection_version'); END;
CREATE TRIGGER jh_member_no_update BEFORE UPDATE ON jh_member BEGIN SELECT RAISE(ABORT, 'immutable: jh_member'); END;
CREATE TRIGGER jh_member_no_delete BEFORE DELETE ON jh_member BEGIN SELECT RAISE(ABORT, 'immutable: jh_member'); END;
CREATE TRIGGER jh_conflict_no_update BEFORE UPDATE ON jh_conflict BEGIN SELECT RAISE(ABORT, 'immutable: jh_conflict'); END;
CREATE TRIGGER jh_conflict_no_delete BEFORE DELETE ON jh_conflict BEGIN SELECT RAISE(ABORT, 'immutable: jh_conflict'); END;
CREATE TRIGGER jh_review_task_no_update BEFORE UPDATE ON jh_review_task BEGIN SELECT RAISE(ABORT, 'immutable: jh_review_task'); END;
CREATE TRIGGER jh_review_task_no_delete BEFORE DELETE ON jh_review_task BEGIN SELECT RAISE(ABORT, 'immutable: jh_review_task'); END;
CREATE TRIGGER jh_export_no_update BEFORE UPDATE ON jh_export BEGIN SELECT RAISE(ABORT, 'immutable: jh_export'); END;
CREATE TRIGGER jh_export_no_delete BEFORE DELETE ON jh_export BEGIN SELECT RAISE(ABORT, 'immutable: jh_export'); END;
