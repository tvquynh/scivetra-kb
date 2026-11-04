-- SciVetra KB migration 0006: S06 local PDF parsing -> versioned, page-anchored ParseArtifacts.
-- Purely additive: no 0001-0005 object is altered. Every table is append-only (no UPDATE / DELETE).
-- Authority boundary (ADR-06): a ParseArtifact is DATA extracted from a local, rights-gated PDF asset. It is never a paper
-- identity decision, a review vote, an eligibility outcome or a synthesis result, and it changes no S03/S05 row.
-- Parsed text is untrusted content (it may contain instructions); nothing in S06 sends it to any AI or network service.

-- One immutable extraction of one PDF asset by one parser build and one extraction configuration.
-- The canonical parse-artifact/1 JSON (pages, text, blocks, anchors, flags) lives in CAS under artifact_sha256.
CREATE TABLE parse_artifact (
  artifact_id TEXT PRIMARY KEY NOT NULL CHECK (artifact_id GLOB 'pza_*'),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  artifact_schema TEXT NOT NULL CHECK (artifact_schema = 'parse-artifact/1'),
  parser_name TEXT NOT NULL CHECK (parser_name = 'pypdf'),
  parser_version TEXT NOT NULL CHECK (length(parser_version) BETWEEN 1 AND 40),
  parser_build_sha256 TEXT NOT NULL CHECK (length(parser_build_sha256) = 64 AND parser_build_sha256 NOT GLOB '*[^0-9a-f]*'),
  extractor_version TEXT NOT NULL CHECK (extractor_version GLOB 'rkb-parse/?*'),
  config_json TEXT NOT NULL CHECK (json_valid(config_json) AND json_type(config_json) = 'object'),
  config_sha256 TEXT NOT NULL CHECK (length(config_sha256) = 64 AND config_sha256 NOT GLOB '*[^0-9a-f]*'),
  status TEXT NOT NULL CHECK (status IN ('PARSED_TEXT','PARSED_PARTIAL_TEXT','SCANNED_NO_TEXT')),
  page_count INTEGER NOT NULL CHECK (page_count >= 1),
  text_page_count INTEGER NOT NULL CHECK (text_page_count >= 0 AND text_page_count <= page_count),
  flags_json TEXT NOT NULL CHECK (json_valid(flags_json) AND json_type(flags_json) = 'array'),
  artifact_sha256 TEXT NOT NULL UNIQUE CHECK (length(artifact_sha256) = 64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'),
  artifact_bytes INTEGER NOT NULL CHECK (artifact_bytes > 0),
  document_text_sha256 TEXT NOT NULL CHECK (length(document_text_sha256) = 64),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  UNIQUE (asset_sha256, parser_name, parser_version, parser_build_sha256, extractor_version, config_sha256),
  CHECK ((status = 'SCANNED_NO_TEXT') = (text_page_count = 0)),
  CHECK ((status = 'PARSED_TEXT') = (text_page_count = page_count))
);

-- Every parse request (produced, reused, refused before any byte was opened, or failed in the sandbox) is one audit row.
-- A refused or failed run never references an artifact and never stores page content.
CREATE TABLE parse_run (
  run_id TEXT PRIMARY KEY NOT NULL CHECK (run_id GLOB 'pzr_*'),
  requested_asset_sha256 TEXT NOT NULL CHECK (length(requested_asset_sha256) = 64 AND requested_asset_sha256 NOT GLOB '*[^0-9a-f]*'),
  link_id TEXT REFERENCES paper_asset_link(link_id),
  document_version_id TEXT REFERENCES document_version(document_version_id),
  purpose TEXT NOT NULL CHECK (purpose IN ('RESEARCH_PARSE','IDENTITY_INSPECTION')),
  outcome TEXT NOT NULL CHECK (outcome IN ('PRODUCED','REUSED','REFUSED','FAILED')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  artifact_id TEXT REFERENCES parse_artifact(artifact_id),
  evidence_eligibility TEXT NOT NULL CHECK (evidence_eligibility IN ('ELIGIBLE_WHILE_CURRENT','NOT_ELIGIBLE_FOR_EVIDENCE')),
  identity_state_at_run TEXT,
  link_role_at_run TEXT,
  rights_assessment_id TEXT REFERENCES rights_assessment(assessment_id),
  rights_gate_json TEXT NOT NULL CHECK (json_valid(rights_gate_json) AND json_type(rights_gate_json) = 'object'),
  parser_version TEXT,
  parser_build_sha256 TEXT,
  config_sha256 TEXT,
  sandbox_json TEXT NOT NULL CHECK (json_valid(sandbox_json) AND json_type(sandbox_json) = 'object'),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  client_version TEXT NOT NULL CHECK (length(client_version) > 0),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((outcome IN ('PRODUCED','REUSED')) = (artifact_id IS NOT NULL)),
  CHECK ((outcome IN ('PRODUCED','REUSED')) = (json_array_length(reason_codes_json) = 0)),
  CHECK (outcome NOT IN ('PRODUCED','REUSED') OR (rights_assessment_id IS NOT NULL AND link_id IS NOT NULL)),
  CHECK (purpose = 'RESEARCH_PARSE' OR evidence_eligibility = 'NOT_ELIGIBLE_FOR_EVIDENCE')
);
CREATE INDEX parse_run_asset ON parse_run(requested_asset_sha256);
CREATE INDEX parse_run_artifact ON parse_run(artifact_id);

-- Defense in depth for the A2 (parse) gate: a producing run must cite the CURRENT rights assessment of its asset and
-- document version, and that assessment must say local_process = ALLOW.
CREATE TRIGGER parse_run_requires_current_local_process_allow
BEFORE INSERT ON parse_run
WHEN NEW.outcome IN ('PRODUCED','REUSED') AND NOT EXISTS (
  SELECT 1 FROM rights_assessment r
  WHERE r.assessment_id = NEW.rights_assessment_id AND r.asset_sha256 = NEW.requested_asset_sha256
    AND r.document_version_id = NEW.document_version_id AND r.local_process = 'ALLOW'
    AND r.rowid = (SELECT max(x.rowid) FROM rights_assessment x WHERE x.asset_sha256 = r.asset_sha256 AND x.document_version_id = r.document_version_id))
BEGIN SELECT RAISE(ABORT, 'parse refused: current rights assessment does not allow local processing'); END;

-- A producing run's link belongs to the parsed asset and document version.
CREATE TRIGGER parse_run_link_matches_asset
BEFORE INSERT ON parse_run
WHEN NEW.link_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM paper_asset_link l WHERE l.link_id = NEW.link_id AND l.asset_sha256 = NEW.requested_asset_sha256
    AND l.document_version_id = NEW.document_version_id)
BEGIN SELECT RAISE(ABORT, 'parse run link differs from asset or document version'); END;

-- Ordinary research parsing needs a HUMAN_CONFIRMED main-text link (the identity-inspection purpose does not, but its
-- output is pinned NOT_ELIGIBLE_FOR_EVIDENCE above). A MISMATCH link is never parsed.
CREATE TRIGGER parse_run_research_requires_confirmed_main_text
BEFORE INSERT ON parse_run
WHEN NEW.outcome IN ('PRODUCED','REUSED') AND NEW.purpose = 'RESEARCH_PARSE' AND NOT EXISTS (
  SELECT 1 FROM paper_asset_link_effective e WHERE e.link_id = NEW.link_id AND e.effective_state = 'HUMAN_CONFIRMED'
    AND e.effective_role = 'MAIN_TEXT')
BEGIN SELECT RAISE(ABORT, 'research parse requires a HUMAN_CONFIRMED MAIN_TEXT link'); END;

CREATE TRIGGER parse_run_never_mismatch
BEFORE INSERT ON parse_run
WHEN NEW.outcome IN ('PRODUCED','REUSED') AND EXISTS (
  SELECT 1 FROM paper_asset_link_effective e WHERE e.link_id = NEW.link_id AND e.effective_state = 'MISMATCH')
BEGIN SELECT RAISE(ABORT, 'a MISMATCH asset link is never parsed'); END;

CREATE TRIGGER parse_run_artifact_of_asset
BEFORE INSERT ON parse_run
WHEN NEW.artifact_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM parse_artifact a WHERE a.artifact_id = NEW.artifact_id AND a.asset_sha256 = NEW.requested_asset_sha256)
BEGIN SELECT RAISE(ABORT, 'parse run artifact belongs to another asset'); END;

-- Explicit local text exports (the only way derived text leaves the KB). Metadata-only exports are not recorded here.
-- The target path is never stored; only the SHA-256 of its resolved text.
-- State machine (S06-F02): REFUSED (no files) | AUTHORIZED (committed BEFORE any plaintext byte is written outside the KB)
-- -> exactly one terminal COMPLETED (folder visible) or FAILED (staging removed, visible folder withdrawn). An AUTHORIZED
-- row without a terminal row is RECOVERY_REQUIRED; the next identical export reconciles it. decision = the authorization
-- decision (ALLOWED / REFUSED) and is carried unchanged by the terminal rows.
CREATE TABLE parse_export_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'pze_*'),
  artifact_id TEXT NOT NULL REFERENCES parse_artifact(artifact_id),
  link_id TEXT NOT NULL REFERENCES paper_asset_link(link_id),
  kind TEXT NOT NULL CHECK (kind IN ('LOCAL_TEXT_EXPORT')),
  decision TEXT NOT NULL CHECK (decision IN ('ALLOWED','REFUSED')),
  state TEXT NOT NULL CHECK (state IN ('REFUSED','AUTHORIZED','COMPLETED','FAILED')),
  authorization_event_id TEXT UNIQUE REFERENCES parse_export_event(event_id),
  export_folder_name TEXT CHECK (export_folder_name IS NULL OR export_folder_name GLOB 'parse_export_*'),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  rights_assessment_id TEXT REFERENCES rights_assessment(assessment_id),
  target_path_sha256 TEXT NOT NULL CHECK (length(target_path_sha256) = 64),
  export_manifest_sha256 TEXT,
  operator_reason TEXT NOT NULL CHECK (length(operator_reason) BETWEEN 1 AND 500),
  detail_json TEXT NOT NULL CHECK (json_valid(detail_json) AND json_type(detail_json) = 'object'),
  actor TEXT NOT NULL CHECK (actor GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((decision = 'REFUSED') = (state = 'REFUSED')),
  CHECK ((state IN ('AUTHORIZED','COMPLETED')) = (json_array_length(reason_codes_json) = 0)),
  CHECK ((state IN ('COMPLETED','FAILED')) = (authorization_event_id IS NOT NULL)),
  CHECK (state = 'REFUSED' OR (rights_assessment_id IS NOT NULL AND export_manifest_sha256 IS NOT NULL AND export_folder_name IS NOT NULL))
);

-- A terminal row closes exactly one AUTHORIZED row of the same artifact, manifest, target and folder (UNIQUE above: at most one).
CREATE TRIGGER parse_export_event_terminal_matches_authorization
BEFORE INSERT ON parse_export_event
WHEN NEW.authorization_event_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM parse_export_event a WHERE a.event_id = NEW.authorization_event_id AND a.state = 'AUTHORIZED'
    AND a.artifact_id = NEW.artifact_id AND a.export_manifest_sha256 = NEW.export_manifest_sha256
    AND a.target_path_sha256 = NEW.target_path_sha256 AND a.export_folder_name = NEW.export_folder_name)
BEGIN SELECT RAISE(ABORT, 'export terminal event does not match an AUTHORIZED event'); END;

-- Immutability: every S06 table is append-only.
CREATE TRIGGER parse_artifact_no_update BEFORE UPDATE ON parse_artifact BEGIN SELECT RAISE(ABORT, 'immutable: parse_artifact'); END;
CREATE TRIGGER parse_artifact_no_delete BEFORE DELETE ON parse_artifact BEGIN SELECT RAISE(ABORT, 'immutable: parse_artifact'); END;
CREATE TRIGGER parse_run_no_update BEFORE UPDATE ON parse_run BEGIN SELECT RAISE(ABORT, 'immutable: parse_run'); END;
CREATE TRIGGER parse_run_no_delete BEFORE DELETE ON parse_run BEGIN SELECT RAISE(ABORT, 'immutable: parse_run'); END;
CREATE TRIGGER parse_export_event_no_update BEFORE UPDATE ON parse_export_event BEGIN SELECT RAISE(ABORT, 'immutable: parse_export_event'); END;
CREATE TRIGGER parse_export_event_no_delete BEFORE DELETE ON parse_export_event BEGIN SELECT RAISE(ABORT, 'immutable: parse_export_event'); END;
