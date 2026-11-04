-- SciVetra KB migration 0010: S10 AI Integration Gateway (ADR_S10). Purely additive: no 0001-0009 object is altered and no
-- existing row is read-modified. Every table is append-only. The gateway is read-only towards research data: it never writes an
-- S05 ballot / lock, an S07 proposal, an S08 draft / review, a rights assessment, a retrieval scope or a Reprovane decision. It
-- only appends the owner records below (written by the local operator CLI) and one access event per tool request.
-- External AI release (gate A8 external_ai_process, owner gate OAG-6) is DEFAULT DENY: with no rows in these tables nothing that
-- comes from a research document is returned to any AI client. Remote MCP, Secure MCP Tunnel and external API consumers are not
-- representable (CHECK constraints below); enabling them needs a later, separately authorized migration.

-- Human owners who may sign S10 owner records (OAG-6 releases, A7/A8 determinations, revocations). Registered by the local
-- operator; assurance is LOCAL_OPERATOR_ASSERTED (no cryptographic signature in v1).
CREATE TABLE aig_owner (
  owner_id TEXT PRIMARY KEY NOT NULL CHECK (owner_id GLOB 'aio_*'),
  owner_label TEXT NOT NULL UNIQUE CHECK (owner_label GLOB 'OWNER:?*' AND length(owner_label) <= 70),
  assurance TEXT NOT NULL CHECK (assurance = 'LOCAL_OPERATOR_ASSERTED'),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- A consumer profile = the trusted installation context of one AI client. The MCP server / exchange exporter is started with a
-- profile id by the operator; the client never names its own consumer, operator or scope set. Model location is always
-- EXTERNAL_UNVERIFIED in v1: content shown in a desktop chat app is treated as sent to the vendor's hosted model.
CREATE TABLE aig_consumer_profile (
  profile_id TEXT PRIMARY KEY NOT NULL CHECK (profile_id GLOB 'aip_*'),
  consumer TEXT NOT NULL CHECK (consumer IN ('CLAUDE_DESKTOP_LOCAL_MCP','CHATGPT_FILE_EXCHANGE')),
  transport TEXT NOT NULL CHECK (transport IN ('MCP_STDIO','FILE_EXCHANGE')),
  destination_class TEXT NOT NULL CHECK (destination_class IN ('CLAUDE_DESKTOP_APP','CHATGPT_MANUAL_UPLOAD')),
  model_location TEXT NOT NULL CHECK (model_location = 'EXTERNAL_UNVERIFIED'),
  identity_assurance TEXT NOT NULL CHECK (identity_assurance = 'INSTALLATION_PROFILE_UNATTESTED'),
  scope_ids_json TEXT NOT NULL CHECK (json_valid(scope_ids_json) AND json_type(scope_ids_json) = 'array'
                                      AND json_array_length(scope_ids_json) BETWEEN 1 AND 20),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
  policy_sha256 TEXT NOT NULL CHECK (length(policy_sha256) = 64),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64),
  CHECK ((consumer = 'CLAUDE_DESKTOP_LOCAL_MCP') = (transport = 'MCP_STDIO' AND destination_class = 'CLAUDE_DESKTOP_APP')),
  CHECK ((consumer = 'CHATGPT_FILE_EXCHANGE') = (transport = 'FILE_EXCHANGE' AND destination_class = 'CHATGPT_MANUAL_UPLOAD'))
);

-- Owner determination that resolves an UNDETERMINED external_ai_process (A8) or redistribute (A7) permission to ALLOW for ONE
-- asset + DocumentVersion, bound to the exact current rights assessment (id and semantic SHA-256). A newer assessment, another
-- version or asset, expiry or revocation voids it. Only PUBLIC_OA_LICENSED can be determined (a DENY is never widened).
CREATE TABLE aig_rights_determination (
  determination_id TEXT PRIMARY KEY NOT NULL CHECK (determination_id GLOB 'aid_*'),
  permission TEXT NOT NULL CHECK (permission IN ('external_ai_process','redistribute')),
  decision TEXT NOT NULL CHECK (decision = 'ALLOW'),
  asset_sha256 TEXT NOT NULL REFERENCES pdf_asset(asset_sha256),
  document_version_id TEXT NOT NULL REFERENCES document_version(document_version_id),
  rights_assessment_id TEXT NOT NULL REFERENCES rights_assessment(assessment_id),
  rights_assessment_sha256 TEXT NOT NULL CHECK (length(rights_assessment_sha256) = 64),
  rights_class TEXT NOT NULL CHECK (rights_class = 'PUBLIC_OA_LICENSED'),
  basis_ref TEXT NOT NULL CHECK (length(basis_ref) BETWEEN 1 AND 300),
  owner_id TEXT NOT NULL REFERENCES aig_owner(owner_id),
  expires_at_utc TEXT NOT NULL CHECK (expires_at_utc GLOB '*Z'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

-- OAG-6 external AI release approval for one profile (consumer, destination, model location), one scope with its exact
-- membership digest and member identities, named operations and content classes, the policy and tool-protocol hashes, and an
-- expiry. Only the SHA-256 of the revocation token is stored.
CREATE TABLE aig_release_approval (
  approval_id TEXT PRIMARY KEY NOT NULL CHECK (approval_id GLOB 'aia_*'),
  profile_id TEXT NOT NULL REFERENCES aig_consumer_profile(profile_id),
  consumer TEXT NOT NULL CHECK (consumer IN ('CLAUDE_DESKTOP_LOCAL_MCP','CHATGPT_FILE_EXCHANGE')),
  destination_class TEXT NOT NULL CHECK (destination_class IN ('CLAUDE_DESKTOP_APP','CHATGPT_MANUAL_UPLOAD')),
  model_location TEXT NOT NULL CHECK (model_location = 'EXTERNAL_UNVERIFIED'),
  scope_id TEXT NOT NULL REFERENCES rt_scope(scope_id),
  membership_sha256 TEXT NOT NULL CHECK (length(membership_sha256) = 64),
  members_json TEXT NOT NULL CHECK (json_valid(members_json) AND json_type(members_json) = 'array' AND json_array_length(members_json) BETWEEN 1 AND 5000),
  operations_json TEXT NOT NULL CHECK (json_valid(operations_json) AND json_type(operations_json) = 'array' AND json_array_length(operations_json) >= 1),
  content_classes_json TEXT NOT NULL CHECK (json_valid(content_classes_json) AND json_type(content_classes_json) = 'array'
                                            AND json_array_length(content_classes_json) >= 1),
  policy_sha256 TEXT NOT NULL CHECK (length(policy_sha256) = 64),
  protocol_sha256 TEXT NOT NULL CHECK (length(protocol_sha256) = 64),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
  owner_id TEXT NOT NULL REFERENCES aig_owner(owner_id),
  expires_at_utc TEXT NOT NULL CHECK (expires_at_utc GLOB '*Z'),
  revocation_token_sha256 TEXT NOT NULL UNIQUE CHECK (length(revocation_token_sha256) = 64),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  semantic_sha256 TEXT NOT NULL UNIQUE CHECK (length(semantic_sha256) = 64)
);

-- Revocation of an owner, profile, determination or approval. Effective for every request that starts after it commits.
CREATE TABLE aig_revocation (
  revocation_id TEXT PRIMARY KEY NOT NULL CHECK (revocation_id GLOB 'air_*'),
  target_kind TEXT NOT NULL CHECK (target_kind IN ('OWNER','PROFILE','DETERMINATION','APPROVAL')),
  target_id TEXT NOT NULL CHECK (length(target_id) BETWEEN 5 AND 80),
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
  revoked_by TEXT NOT NULL CHECK (revoked_by GLOB 'OPERATOR:?*' OR revoked_by = 'REVOCATION_TOKEN'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);
CREATE INDEX aig_revocation_target ON aig_revocation(target_kind, target_id);

-- One durable event per gateway request, committed BEFORE any response or exchange file is released. Never query text, document
-- text, paths, tokens or error messages: hashes, row ids, decisions and reason codes only.
CREATE TABLE aig_access_event (
  event_id TEXT PRIMARY KEY NOT NULL CHECK (event_id GLOB 'aie_*'),
  request_id TEXT NOT NULL UNIQUE CHECK (request_id GLOB 'aiq_*'),
  profile_id TEXT,
  consumer TEXT NOT NULL CHECK (consumer IN ('CLAUDE_DESKTOP_LOCAL_MCP','CHATGPT_FILE_EXCHANGE','UNBOUND')),
  transport TEXT NOT NULL CHECK (transport IN ('MCP_STDIO','FILE_EXCHANGE','IN_PROCESS_TEST')),
  destination_class TEXT NOT NULL CHECK (destination_class IN ('CLAUDE_DESKTOP_APP','CHATGPT_MANUAL_UPLOAD','UNBOUND')),
  model_location TEXT NOT NULL CHECK (model_location = 'EXTERNAL_UNVERIFIED'),
  declared_client_sha256 TEXT CHECK (declared_client_sha256 IS NULL OR length(declared_client_sha256) = 64),
  tool TEXT NOT NULL CHECK (length(tool) BETWEEN 1 AND 40 AND tool NOT GLOB '*[^a-z_]*'),
  arguments_sha256 TEXT NOT NULL CHECK (length(arguments_sha256) = 64),
  scope_id TEXT,
  approval_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('OK','NO_MATCH','NO_AUTHORIZED_EVIDENCE','DENIED','INVALID_REQUEST','ERROR')),
  decision TEXT NOT NULL CHECK (decision IN ('ALLOW','PARTIAL','DENY','NOT_APPLICABLE')),
  reason_codes_json TEXT NOT NULL CHECK (json_valid(reason_codes_json) AND json_type(reason_codes_json) = 'array'),
  content_revealed INTEGER NOT NULL CHECK (content_revealed IN (0, 1)),
  returned_json TEXT NOT NULL CHECK (json_valid(returned_json) AND json_type(returned_json) = 'array'),
  rights_json TEXT NOT NULL CHECK (json_valid(rights_json) AND json_type(rights_json) = 'array'),
  excluded_by_reason_json TEXT NOT NULL CHECK (json_valid(excluded_by_reason_json) AND json_type(excluded_by_reason_json) = 'object'),
  index_build_id TEXT,
  query_sha256 TEXT CHECK (query_sha256 IS NULL OR length(query_sha256) = 64),
  result_sha256 TEXT NOT NULL CHECK (length(result_sha256) = 64),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z'),
  CHECK (content_revealed = 0 OR (status = 'OK' AND approval_id IS NOT NULL AND profile_id IS NOT NULL))
);

-- A ChatGPT exchange bundle written by the operator's export command. Recorded (with its audit event) before the files are
-- published; re-validated (expiry, approval, rights, CAS) whenever it is checked before a manual upload.
CREATE TABLE aig_exchange_bundle (
  bundle_id TEXT PRIMARY KEY NOT NULL CHECK (bundle_id GLOB 'aib_*'),
  event_id TEXT NOT NULL UNIQUE REFERENCES aig_access_event(event_id),
  profile_id TEXT NOT NULL REFERENCES aig_consumer_profile(profile_id),
  approval_id TEXT REFERENCES aig_release_approval(approval_id),
  scope_id TEXT NOT NULL REFERENCES rt_scope(scope_id),
  bundle_sha256 TEXT NOT NULL CHECK (length(bundle_sha256) = 64),
  files_json TEXT NOT NULL CHECK (json_valid(files_json) AND json_type(files_json) = 'object'),
  passages_json TEXT NOT NULL CHECK (json_valid(passages_json) AND json_type(passages_json) = 'array'),
  content_revealed INTEGER NOT NULL CHECK (content_revealed IN (0, 1)),
  expires_at_utc TEXT NOT NULL CHECK (expires_at_utc GLOB '*Z'),
  created_by TEXT NOT NULL CHECK (created_by GLOB 'OPERATOR:?*'),
  created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '*Z')
);

-- Immutability: every S10 table is append-only.
CREATE TRIGGER aig_owner_no_update BEFORE UPDATE ON aig_owner BEGIN SELECT RAISE(ABORT, 'immutable: aig_owner'); END;
CREATE TRIGGER aig_owner_no_delete BEFORE DELETE ON aig_owner BEGIN SELECT RAISE(ABORT, 'immutable: aig_owner'); END;
CREATE TRIGGER aig_consumer_profile_no_update BEFORE UPDATE ON aig_consumer_profile BEGIN SELECT RAISE(ABORT, 'immutable: aig_consumer_profile'); END;
CREATE TRIGGER aig_consumer_profile_no_delete BEFORE DELETE ON aig_consumer_profile BEGIN SELECT RAISE(ABORT, 'immutable: aig_consumer_profile'); END;
CREATE TRIGGER aig_rights_determination_no_update BEFORE UPDATE ON aig_rights_determination BEGIN SELECT RAISE(ABORT, 'immutable: aig_rights_determination'); END;
CREATE TRIGGER aig_rights_determination_no_delete BEFORE DELETE ON aig_rights_determination BEGIN SELECT RAISE(ABORT, 'immutable: aig_rights_determination'); END;
CREATE TRIGGER aig_release_approval_no_update BEFORE UPDATE ON aig_release_approval BEGIN SELECT RAISE(ABORT, 'immutable: aig_release_approval'); END;
CREATE TRIGGER aig_release_approval_no_delete BEFORE DELETE ON aig_release_approval BEGIN SELECT RAISE(ABORT, 'immutable: aig_release_approval'); END;
CREATE TRIGGER aig_revocation_no_update BEFORE UPDATE ON aig_revocation BEGIN SELECT RAISE(ABORT, 'immutable: aig_revocation'); END;
CREATE TRIGGER aig_revocation_no_delete BEFORE DELETE ON aig_revocation BEGIN SELECT RAISE(ABORT, 'immutable: aig_revocation'); END;
CREATE TRIGGER aig_access_event_no_update BEFORE UPDATE ON aig_access_event BEGIN SELECT RAISE(ABORT, 'immutable: aig_access_event'); END;
CREATE TRIGGER aig_access_event_no_delete BEFORE DELETE ON aig_access_event BEGIN SELECT RAISE(ABORT, 'immutable: aig_access_event'); END;
CREATE TRIGGER aig_exchange_bundle_no_update BEFORE UPDATE ON aig_exchange_bundle BEGIN SELECT RAISE(ABORT, 'immutable: aig_exchange_bundle'); END;
CREATE TRIGGER aig_exchange_bundle_no_delete BEFORE DELETE ON aig_exchange_bundle BEGIN SELECT RAISE(ABORT, 'immutable: aig_exchange_bundle'); END;
