-- SciVetra KB migration 0009: S09 local retrieval (S01 v03 09 section 1; ADR_S09). Purely additive: no 0001-0008 object is
-- altered. The search index itself is NOT in the KB: it is a derived, disposable file (<data_root>/index/fts.sqlite) rebuilt
-- from KB data. The KB only records the explicit retrieval scopes, the index builds it has published and an audit of every
-- retrieval call (query hashes, never query or document text). Every table is append-only.
-- Authority boundary: retrieval never writes an S05 ballot / lock, an S07 proposal, an S08 draft / review, a Reprovane decision
-- or a corpus membership. A retrieval scope is NON_AUTHORITATIVE and serves only the local CLI consumer (S10 owns desktop / MCP /
-- remote consumers behind gate A8 and OAG-6).

-- An explicit retrieval scope. FTE_RUN: members of ONE S05 full-text-eligibility run, each pinned to candidate, link, document
-- version, asset and S06 RESEARCH_PARSE artifact (full-text passages, S08 evidence and metadata). RELEASE_IMPORT: papers of ONE
-- imported Reprovane release (metadata only, never full text). Journal-harvest (S04J) inventories are never a basis.
CREATE TABLE rt_scope (
  scope_id TEXT PRIMARY KEY NOT NULL CHECK (scope_id GLOB 'rts_*'),
  basis_kind TEXT NOT NULL CHECK (basis_kind IN ('FTE_RUN','RELEASE_IMPORT')),
  basis_id TEXT NOT NULL CHECK (length(basis_id) BETWEEN 1 AND 80),
  members_json TEXT NOT NULL CHECK (json_valid(members_json) AND json_type(members_json) = 'array' AND json_array_length(members_json) BETWEEN 1 AND 5000),
  membership_sha256 TEXT NOT NULL CHECK (length(membership_sha256) = 64),
  eligibility_mode TEXT NOT NULL CHECK (eligibility_mode IN ('FINAL_FT_INCLUDE_ONLY','PROVISIONAL_NON_AUTHORITATIVE','METADATA_ONLY')),
  provisional_reason TEXT CHECK (provisional_reason IS NULL OR length(provisional_reason) BETWEEN 1 AND 500),
  consumer_allowed TEXT NOT NULL CHECK (consumer_allowed = 'LOCAL_CLI'),
  authority TEXT NOT NULL CHECK (authority = 'NON_AUTHORITATIVE'),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((basis_kind = 'RELEASE_IMPORT') = (eligibility_mode = 'METADATA_ONLY')),
  CHECK ((eligibility_mode = 'PROVISIONAL_NON_AUTHORITATIVE') = (provisional_reason IS NOT NULL))
);

CREATE TRIGGER rt_scope_basis_exists
BEFORE INSERT ON rt_scope
WHEN (NEW.basis_kind = 'FTE_RUN' AND NOT EXISTS (SELECT 1 FROM fte_run WHERE run_id = NEW.basis_id))
  OR (NEW.basis_kind = 'RELEASE_IMPORT' AND NOT EXISTS (SELECT 1 FROM reprovane_release_import WHERE release_import_id = NEW.basis_id))
BEGIN SELECT RAISE(ABORT, 'retrieval scope basis must be an existing S05 run or imported Reprovane release'); END;

-- A published (atomically replaced, complete) derived index. The index file carries the same build id and manifest SHA-256;
-- a file whose build id is not recorded here is foreign or stale and is refused.
CREATE TABLE rt_index_build (
  build_id TEXT PRIMARY KEY NOT NULL CHECK (build_id GLOB 'rtb_*'),
  index_schema TEXT NOT NULL CHECK (index_schema GLOB 'rkb-retrieval-index/?*'),
  chunker TEXT NOT NULL CHECK (chunker GLOB 'rkb-chunker/?*'),
  embedder_json TEXT NOT NULL CHECK (json_valid(embedder_json) AND json_type(embedder_json) = 'object'),
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  counts_json TEXT NOT NULL CHECK (json_valid(counts_json) AND json_type(counts_json) = 'object'),
  excluded_by_reason_json TEXT NOT NULL CHECK (json_valid(excluded_by_reason_json) AND json_type(excluded_by_reason_json) = 'object'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- Audit of every retrieval call: what was returned (chunk / draft ids and excerpt hashes), which rights views, what was excluded
-- and why. Never the query text or document text.
CREATE TABLE rt_access_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'rta_*'),
  scope_id TEXT NOT NULL REFERENCES rt_scope(scope_id),
  operation TEXT NOT NULL CHECK (operation IN ('SEARCH_PAPERS','SEARCH_PASSAGES','SEARCH_EVIDENCE','GET_PASSAGE','RAG_CONTEXT')),
  consumer TEXT NOT NULL CHECK (consumer = 'LOCAL_CLI'),
  retrieval_mode TEXT NOT NULL CHECK (retrieval_mode IN ('LEXICAL_ONLY','HYBRID_MOCK','HYBRID_LOCAL_MODEL','METADATA','DIRECT')),
  index_build_id TEXT REFERENCES rt_index_build(build_id),
  query_sha256 TEXT CHECK (query_sha256 IS NULL OR length(query_sha256) = 64),
  content_revealed INTEGER NOT NULL CHECK (content_revealed IN (0, 1)),
  returned_json TEXT NOT NULL CHECK (json_valid(returned_json) AND json_type(returned_json) = 'array'),
  view_counts_json TEXT NOT NULL CHECK (json_valid(view_counts_json) AND json_type(view_counts_json) = 'object'),
  excluded_by_reason_json TEXT NOT NULL CHECK (json_valid(excluded_by_reason_json) AND json_type(excluded_by_reason_json) = 'object'),
  outcome TEXT NOT NULL CHECK (length(outcome) BETWEEN 2 AND 60 AND outcome NOT GLOB '*[^A-Z0-9_]*'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- Immutability: every S09 table is append-only.
CREATE TRIGGER rt_scope_no_update BEFORE UPDATE ON rt_scope BEGIN SELECT RAISE(ABORT, 'immutable: rt_scope'); END;
CREATE TRIGGER rt_scope_no_delete BEFORE DELETE ON rt_scope BEGIN SELECT RAISE(ABORT, 'immutable: rt_scope'); END;
CREATE TRIGGER rt_index_build_no_update BEFORE UPDATE ON rt_index_build BEGIN SELECT RAISE(ABORT, 'immutable: rt_index_build'); END;
CREATE TRIGGER rt_index_build_no_delete BEFORE DELETE ON rt_index_build BEGIN SELECT RAISE(ABORT, 'immutable: rt_index_build'); END;
CREATE TRIGGER rt_access_event_no_update BEFORE UPDATE ON rt_access_event BEGIN SELECT RAISE(ABORT, 'immutable: rt_access_event'); END;
CREATE TRIGGER rt_access_event_no_delete BEFORE DELETE ON rt_access_event BEGIN SELECT RAISE(ABORT, 'immutable: rt_access_event'); END;
