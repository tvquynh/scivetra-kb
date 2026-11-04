-- SciVetra KB migration 0003: S04 Download Bridge (S01 v03 06). Purely additive: no 0001/0002 object is altered.
-- Every table is append-only (no UPDATE / DELETE). Current states are projections over event tables.
-- Integrity comparisons only: hashes recorded here detect change, they do not authenticate (06 section 5.1).

-- One exported PBD batch. Hashes of the portable manifest and input CSV are recorded here (both byte streams are
-- also stored in CAS, so doctor/backup/verify/restore enumerate them: core/cas.py CAS_REFERENCE_COLUMNS, schema 3).
-- No local absolute path is stored: folders are identified only by sha256(normcase(realpath)).
CREATE TABLE bridge_batch (
  batch_id TEXT PRIMARY KEY NOT NULL CHECK (batch_id GLOB 'ab_*'),
  batch_short TEXT NOT NULL UNIQUE CHECK (length(batch_short) = 8 AND batch_short NOT GLOB '*[^0-9a-f]*'),
  source_kind TEXT NOT NULL CHECK (source_kind IN ('RV_MANUAL_QUEUE','RV_CORPUS')),
  workspace_ref TEXT NOT NULL REFERENCES workspace_registration(workspace_ref),
  release_id TEXT NOT NULL CHECK (length(release_id) > 0),
  release_manifest_sha256 TEXT NOT NULL CHECK (length(release_manifest_sha256) = 64),
  fulltext_run_id TEXT,
  fulltext_manifest_rv_digest TEXT CHECK (fulltext_manifest_rv_digest IS NULL OR length(fulltext_manifest_rv_digest) = 64),
  source_files_json TEXT NOT NULL CHECK (json_valid(source_files_json) AND json_type(source_files_json) = 'object'),
  source_sums_sha256 TEXT NOT NULL CHECK (length(source_sums_sha256) = 64),
  expected_hash_supplied INTEGER NOT NULL CHECK (expected_hash_supplied IN (0, 1)),
  verify_result_json TEXT NOT NULL CHECK (json_valid(verify_result_json) AND json_type(verify_result_json) = 'object'),
  pbd_profile TEXT NOT NULL CHECK (pbd_profile = 'pbd-csv-v1'),
  manifest_bytes_sha256 TEXT NOT NULL UNIQUE CHECK (length(manifest_bytes_sha256) = 64),
  rows_semantic_sha256 TEXT NOT NULL CHECK (length(rows_semantic_sha256) = 64),
  input_csv_bytes_sha256 TEXT NOT NULL CHECK (length(input_csv_bytes_sha256) = 64),
  input_csv_name TEXT NOT NULL CHECK (input_csv_name GLOB 'pbd_input_????????.csv'),
  row_count INTEGER NOT NULL CHECK (row_count >= 1),
  out_dir_path_sha256 TEXT NOT NULL CHECK (length(out_dir_path_sha256) = 64),
  output_folder_name TEXT NOT NULL CHECK (output_folder_name GLOB 'pbd_output_????????'),
  output_folder_path_sha256 TEXT NOT NULL CHECK (length(output_folder_path_sha256) = 64),
  output_folder_created_empty INTEGER NOT NULL CHECK (output_folder_created_empty = 1),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  client_version TEXT NOT NULL,
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (source_kind <> 'RV_MANUAL_QUEUE' OR (fulltext_run_id IS NOT NULL AND fulltext_manifest_rv_digest IS NOT NULL))
);

-- Frozen row mapping (06 sections 3-5). The PBD record_id / output openalex_id is bridge_row_id, never an OpenAlex ID.
CREATE TABLE bridge_row (
  bridge_row_id TEXT PRIMARY KEY NOT NULL CHECK (length(bridge_row_id) = 24 AND bridge_row_id GLOB 'b*' AND substr(bridge_row_id, 2) NOT GLOB '*[^0-9a-f]*'),
  batch_id TEXT NOT NULL REFERENCES bridge_batch(batch_id),
  row_ordinal INTEGER NOT NULL CHECK (row_ordinal >= 1),
  save_as TEXT NOT NULL UNIQUE CHECK (save_as = bridge_row_id || '.pdf'),
  source_item_key TEXT NOT NULL CHECK (source_item_key GLOB 'rv:*:*'),
  paper_id TEXT NOT NULL REFERENCES global_paper(paper_id),
  reference_id TEXT NOT NULL REFERENCES project_paper_reference(reference_id),
  corpus_item_id TEXT NOT NULL CHECK (length(corpus_item_id) > 0),
  publication_entity_id TEXT NOT NULL CHECK (length(publication_entity_id) > 0),
  export_doi TEXT,
  flags_json TEXT NOT NULL CHECK (json_valid(flags_json) AND json_type(flags_json) = 'array'),
  exported_urls_json TEXT NOT NULL CHECK (json_valid(exported_urls_json) AND json_type(exported_urls_json) = 'object'),
  row_semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(row_semantic_sha256) = 64),
  UNIQUE (batch_id, row_ordinal),
  UNIQUE (batch_id, source_item_key)
);
CREATE INDEX bridge_row_paper ON bridge_row(paper_id);

-- One operator-invoked import of one PBD result CSV. Only the SHA-256 of the result CSV is kept (its bytes may hold
-- local paths and URLs); anomalies hold row numbers, codes and hashes, never raw values.
CREATE TABLE bridge_result_import (
  import_id TEXT PRIMARY KEY NOT NULL CHECK (import_id GLOB 'bri_*'),
  batch_id TEXT NOT NULL REFERENCES bridge_batch(batch_id),
  result_csv_sha256 TEXT NOT NULL CHECK (length(result_csv_sha256) = 64),
  outcome TEXT NOT NULL CHECK (outcome IN ('RECONCILED_PARTIAL','RECONCILED_COMPLETE','REFUSED')),
  refusal_code TEXT,
  data_row_count INTEGER NOT NULL CHECK (data_row_count >= 0),
  counts_json TEXT NOT NULL CHECK (json_valid(counts_json) AND json_type(counts_json) = 'object'),
  anomalies_json TEXT NOT NULL CHECK (json_valid(anomalies_json) AND json_type(anomalies_json) = 'array'),
  pdf_folder_path_sha256 TEXT CHECK (pdf_folder_path_sha256 IS NULL OR length(pdf_folder_path_sha256) = 64),
  different_output_folder INTEGER NOT NULL CHECK (different_output_folder IN (0, 1)),
  accept_late_files INTEGER NOT NULL CHECK (accept_late_files IN (0, 1)),
  operator TEXT NOT NULL CHECK (operator GLOB 'OPERATOR:?*'),
  completed_at_utc TEXT NOT NULL CHECK (completed_at_utc GLOB '*Z'),
  CHECK ((outcome = 'REFUSED') = (refusal_code IS NOT NULL))
);
-- 06 section 8 step 3: an unchanged result file is imported (reconciled) at most once per batch.
CREATE UNIQUE INDEX bridge_result_import_once ON bridge_result_import(batch_id, result_csv_sha256) WHERE outcome <> 'REFUSED';

-- One PBD output row as observed in one result file (AcquisitionAttempt). Raw status kept, whitelisted sanitized fields.
CREATE TABLE bridge_attempt (
  attempt_id TEXT PRIMARY KEY NOT NULL CHECK (attempt_id GLOB 'bat_*'),
  batch_id TEXT NOT NULL REFERENCES bridge_batch(batch_id),
  bridge_row_id TEXT NOT NULL REFERENCES bridge_row(bridge_row_id),
  result_csv_sha256 TEXT NOT NULL CHECK (length(result_csv_sha256) = 64),
  output_row_number INTEGER NOT NULL CHECK (output_row_number >= 1),
  status_raw TEXT NOT NULL CHECK (length(status_raw) <= 64),
  bridge_status TEXT NOT NULL CHECK (bridge_status IN ('RECEIVED_PENDING_VERIFY','FAILED','DEFERRED','NEEDS_HUMAN_ACCESS','NEEDS_HUMAN_ACTION',
                                                       'SKIPPED','NO_URL','IN_PROGRESS_AT_EXPORT','UNKNOWN_STATUS','QUARANTINED')),
  sub_reason TEXT,
  codes_json TEXT NOT NULL CHECK (json_valid(codes_json) AND json_type(codes_json) = 'array'),
  observed_json TEXT NOT NULL CHECK (json_valid(observed_json) AND json_type(observed_json) = 'object'),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (result_csv_sha256, batch_id, output_row_number)
);
CREATE INDEX bridge_attempt_row ON bridge_attempt(bridge_row_id);

-- Outcome of the KB-side file verification for one attempt (S03 G0-G3 through AssetService.register_asset only).
-- VERIFIED_PDF = bytes + structure verified; it never means the paper identity or full-text eligibility is confirmed.
CREATE TABLE bridge_file_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'bfe_*'),
  attempt_id TEXT NOT NULL REFERENCES bridge_attempt(attempt_id),
  bridge_row_id TEXT NOT NULL REFERENCES bridge_row(bridge_row_id),
  outcome TEXT NOT NULL CHECK (outcome IN ('VERIFIED_PDF','REJECTED_FILE','DUPLICATE_NOOP','NEW_BYTES_SAME_ROW','LATE_FILE_PRESENT')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  input_sha256 TEXT CHECK (input_sha256 IS NULL OR length(input_sha256) = 64),
  asset_sha256 TEXT REFERENCES pdf_asset(asset_sha256),
  link_id TEXT REFERENCES paper_asset_link(link_id),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (outcome NOT IN ('VERIFIED_PDF','DUPLICATE_NOOP','NEW_BYTES_SAME_ROW') OR (asset_sha256 IS NOT NULL AND link_id IS NOT NULL)),
  CHECK (outcome <> 'REJECTED_FILE' OR (asset_sha256 IS NULL AND json_array_length(reason_codes_json) > 0))
);
-- Reconciliation key (06 section 8): (batch, bridge_row_id, asset_sha256) is verified at most once.
CREATE UNIQUE INDEX bridge_file_event_verified_once ON bridge_file_event(bridge_row_id, asset_sha256) WHERE outcome IN ('VERIFIED_PDF','NEW_BYTES_SAME_ROW');

-- Append-only row status history; the current status is the latest event per row.
CREATE TABLE bridge_row_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'bre_*'),
  bridge_row_id TEXT NOT NULL REFERENCES bridge_row(bridge_row_id),
  new_status TEXT NOT NULL CHECK (new_status IN ('EXPORTED','RECEIVED_PENDING_VERIFY','VERIFIED_PDF','REJECTED_FILE','FAILED','DEFERRED',
                                                 'NEEDS_HUMAN_ACCESS','NEEDS_HUMAN_ACTION','SKIPPED','NO_URL','IN_PROGRESS_AT_EXPORT',
                                                 'UNKNOWN_STATUS','QUARANTINED')),
  cause_kind TEXT NOT NULL CHECK (cause_kind IN ('BATCH_EXPORTED','ATTEMPT','FILE_EVENT')),
  cause_ref TEXT NOT NULL CHECK (length(cause_ref) > 0),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);
CREATE INDEX bridge_row_event_row ON bridge_row_event(bridge_row_id);

-- Batch lifecycle (06 section 6.2): CLOSED only with every row final, else CLOSED_WITH_OPEN_ROWS + operator reason.
CREATE TABLE bridge_batch_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'bbe_*'),
  batch_id TEXT NOT NULL REFERENCES bridge_batch(batch_id),
  new_state TEXT NOT NULL CHECK (new_state IN ('EXPORTED','RECONCILED_PARTIAL','RECONCILED_COMPLETE','CLOSED','CLOSED_WITH_OPEN_ROWS')),
  reason TEXT,
  open_rows_json TEXT NOT NULL CHECK (json_valid(open_rows_json) AND json_type(open_rows_json) = 'array'),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  recorded_at_utc TEXT NOT NULL CHECK (recorded_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK (new_state <> 'CLOSED_WITH_OPEN_ROWS' OR (reason = 'OPERATOR_CLOSE_WITH_OPEN_ROWS' AND json_array_length(open_rows_json) > 0)),
  CHECK (new_state <> 'CLOSED' OR json_array_length(open_rows_json) = 0)
);

-- Operator review prompts raised by the bridge (identity of new bytes, late files, quarantined rows, access barriers).
CREATE TABLE bridge_review_task (
  task_id TEXT PRIMARY KEY NOT NULL CHECK (task_id GLOB 'brt_*'),
  batch_id TEXT NOT NULL REFERENCES bridge_batch(batch_id),
  bridge_row_id TEXT REFERENCES bridge_row(bridge_row_id),
  trigger TEXT NOT NULL CHECK (trigger IN ('NEW_BYTES_SAME_ROW','LATE_FILE_PRESENT','UNKNOWN_STATUS','ROW_DUPLICATE','ROW_IDENTITY_MISMATCH',
                                           'NEEDS_HUMAN_ACCESS','NEEDS_HUMAN_ACTION','IDENTITY_REVIEW')),
  context_json TEXT NOT NULL CHECK (json_valid(context_json) AND json_type(context_json) = 'object'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

CREATE TRIGGER bridge_batch_no_update BEFORE UPDATE ON bridge_batch BEGIN SELECT RAISE(ABORT, 'immutable: bridge_batch'); END;
CREATE TRIGGER bridge_batch_no_delete BEFORE DELETE ON bridge_batch BEGIN SELECT RAISE(ABORT, 'immutable: bridge_batch'); END;
CREATE TRIGGER bridge_row_no_update BEFORE UPDATE ON bridge_row BEGIN SELECT RAISE(ABORT, 'immutable: bridge_row'); END;
CREATE TRIGGER bridge_row_no_delete BEFORE DELETE ON bridge_row BEGIN SELECT RAISE(ABORT, 'immutable: bridge_row'); END;
CREATE TRIGGER bridge_result_import_no_update BEFORE UPDATE ON bridge_result_import BEGIN SELECT RAISE(ABORT, 'immutable: bridge_result_import'); END;
CREATE TRIGGER bridge_result_import_no_delete BEFORE DELETE ON bridge_result_import BEGIN SELECT RAISE(ABORT, 'immutable: bridge_result_import'); END;
CREATE TRIGGER bridge_attempt_no_update BEFORE UPDATE ON bridge_attempt BEGIN SELECT RAISE(ABORT, 'immutable: bridge_attempt'); END;
CREATE TRIGGER bridge_attempt_no_delete BEFORE DELETE ON bridge_attempt BEGIN SELECT RAISE(ABORT, 'immutable: bridge_attempt'); END;
CREATE TRIGGER bridge_file_event_no_update BEFORE UPDATE ON bridge_file_event BEGIN SELECT RAISE(ABORT, 'immutable: bridge_file_event'); END;
CREATE TRIGGER bridge_file_event_no_delete BEFORE DELETE ON bridge_file_event BEGIN SELECT RAISE(ABORT, 'immutable: bridge_file_event'); END;
CREATE TRIGGER bridge_row_event_no_update BEFORE UPDATE ON bridge_row_event BEGIN SELECT RAISE(ABORT, 'immutable: bridge_row_event'); END;
CREATE TRIGGER bridge_row_event_no_delete BEFORE DELETE ON bridge_row_event BEGIN SELECT RAISE(ABORT, 'immutable: bridge_row_event'); END;
CREATE TRIGGER bridge_batch_event_no_update BEFORE UPDATE ON bridge_batch_event BEGIN SELECT RAISE(ABORT, 'immutable: bridge_batch_event'); END;
CREATE TRIGGER bridge_batch_event_no_delete BEFORE DELETE ON bridge_batch_event BEGIN SELECT RAISE(ABORT, 'immutable: bridge_batch_event'); END;
CREATE TRIGGER bridge_review_task_no_update BEFORE UPDATE ON bridge_review_task BEGIN SELECT RAISE(ABORT, 'immutable: bridge_review_task'); END;
CREATE TRIGGER bridge_review_task_no_delete BEFORE DELETE ON bridge_review_task BEGIN SELECT RAISE(ABORT, 'immutable: bridge_review_task'); END;

-- A file event belongs to the same row as its attempt.
CREATE TRIGGER bridge_file_event_row_matches_attempt
BEFORE INSERT ON bridge_file_event
WHEN NOT EXISTS (SELECT 1 FROM bridge_attempt a WHERE a.attempt_id = NEW.attempt_id AND a.bridge_row_id = NEW.bridge_row_id)
BEGIN
  SELECT RAISE(ABORT, 'file event row differs from attempt row');
END;

-- A verified link must point at the paper the row was exported for (never another paper).
CREATE TRIGGER bridge_file_event_link_paper
BEFORE INSERT ON bridge_file_event
WHEN NEW.link_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM paper_asset_link l JOIN bridge_row r ON r.bridge_row_id = NEW.bridge_row_id
  WHERE l.link_id = NEW.link_id AND l.paper_id = r.paper_id AND l.asset_sha256 = NEW.asset_sha256)
BEGIN
  SELECT RAISE(ABORT, 'bridge link must be the exported paper and the same asset');
END;

-- A batch cannot be reconciled after it was closed.
CREATE TRIGGER bridge_attempt_batch_open
BEFORE INSERT ON bridge_attempt
WHEN EXISTS (SELECT 1 FROM bridge_batch_event e WHERE e.batch_id = NEW.batch_id AND e.new_state IN ('CLOSED','CLOSED_WITH_OPEN_ROWS'))
BEGIN
  SELECT RAISE(ABORT, 'batch is closed');
END;
