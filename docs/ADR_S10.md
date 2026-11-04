# ADR S10: Desktop and AI integration gateway (v01, corrected in v02)

Status: IMPLEMENTED offline and synthetic, submitted for architect review. Not self-accepted.

Baseline: the architect-accepted S09 v02 (`SCIVETRA_S09_SEMANTIC_SEARCH_RAG_v02.zip`, SHA-256 `3cb8565b…6fc`).

Inputs:
- the S10 handoff v01 (execution prompt, acceptance checklist, architecture, offline validation note);
- S01 v03 contracts 05 §4.2–4.3 and 09 §2–§5.

Scope: a read-only, provider-independent AI Integration Gateway in front of the S09 retrieval engine.

## AI-D01 One policy contract, versioned and hash-bound

`scivetra-ai-consumer-policy/2` (`src/scivetra_kb/ai/policy.py`; v01 shipped `/1`, see AI-D11) defines, for each consumer:
- transport and destination;
- model location;
- the rights gates for document content;
- the disabled features.

The tool protocol is `scivetra-ai-tools/1`: seven read-only tools with strict input schemas and stable aliases. Every result uses the schema `scivetra-ai-tool-result/1`.

The SHA-256 of the policy and of the protocol is bound into every consumer profile and every OAG-6 approval. Changing either one voids them (`PROFILE_POLICY_CHANGED`, `APPROVAL_PROTOCOL_CHANGED`).

| Consumer | Transport | Destination | Model location | Gates for EVERY content class (v02) | Additional gates for document text / values |
|---|---|---|---|---|---|
| `CLAUDE_DESKTOP_LOCAL_MCP` | `MCP_STDIO` | `CLAUDE_DESKTOP_APP` | `EXTERNAL_UNVERIFIED` | A8 + OAG-6 | A2, A5 |
| `CHATGPT_FILE_EXCHANGE` | `FILE_EXCHANGE` | `CHATGPT_MANUAL_UPLOAD` | `EXTERNAL_UNVERIFIED` | A7, A8 + OAG-6 | A2, A5 |

The following consumers are not representable in v01:

| Consumer | State |
|---|---|
| `CHATGPT_REMOTE_MCP` | `REMOTE_NOT_APPROVED` |
| `SECURE_MCP_TUNNEL` | `REMOTE_NOT_APPROVED` |
| `EXTERNAL_AI_API` | `EXTERNAL_API_DISABLED` |
| `CHATGPT_DESKTOP_LOCAL_PLUGIN` | `PARTNER_GATED_NOT_AVAILABLE` |

These are not representable because:
- the migration 0010 CHECK constraints reject them;
- the CLI refuses them.

**Transport is not authorization.** Claude Desktop runs the MCP server locally, but it may send tool output to a vendor-hosted model. So the model location is always `EXTERNAL_UNVERIFIED`, and A8 (`external_ai_process`) plus owner gate OAG-6 apply. A5, which authorizes local display, never authorizes AI chat use. No local-inference path is verified (`local_verified_inference: NOT_AVAILABLE`).

## AI-D02 S09 is adapted through an explicit interface: `rkb-retrieval-consumer/1`

S09's `check_consumer` still accepts only the string `LOCAL_CLI`. The strings `CLAUDE_DESKTOP_LOCAL_MCP`, `mcp` and `claude-desktop` are all refused (`CONSUMER_NOT_PERMITTED`). An external consumer reaches `RetrievalService` only as a server-side `ExternalConsumer` object (`retrieval/consumers.py`). The S10 gateway builds that object from a registered profile, so a client can never name itself `LOCAL_CLI`.

Every S09 check runs unchanged:
- scope membership;
- member currency;
- S05 eligibility;
- the A2 rights checks;
- the CAS artifact re-read;
- the PDF re-hash;
- anchor verification;
- gate A5.

For each item S09 would show, it also asks `consumer.release(content_class, member, assessment)`. S10 can therefore add gates but never relax an S09 decision.

S09 audit details for an external consumer are handed to `consumer.record(...)` and stored durably by the gateway. `rt_access_event` remains LOCAL_CLI only.

LOCAL_CLI outputs, reasons and audit are byte-identical to S09 v02, as the 70-query benchmark shows (the hashes are equal).

## AI-D03 One endpoint for every transport, fresh per request

`IntegrationGateway.handle(tool, arguments)` (`ai/gateway.py`) is the same code behind:
- the MCP stdio adapter;
- the ChatGPT file exporter;
- the offline tests (`IN_PROCESS_TEST`, transport state `OFFLINE_MOCK`).

Per request, the gateway:

1. **Opens a fresh KB connection and a fresh `RetrievalService`.** Nothing is shared between requests or threads. The S09 `_per_call` cache now belongs to the thread that opened the call. A second thread that enters the same instance is refused with `CONCURRENT_SERVICE_USE` instead of sharing the cache.
2. **Runs `BEGIN IMMEDIATE`.** The whole request reads one consistent KB state, and its audit row commits in the same transaction. A revocation, a rights change or an S05 change by another process therefore commits either before the request (the request denies) or after it (the next request denies). This closes TOCTOU inside the KB. CAS files are outside SQLite, but each one is re-hashed in the request that returns its text. A file changed after that point affects the next request.
3. **Validates the tool and its arguments strictly.** Inputs:
   - must be the seven tool names (or aliases);
   - may have no extra keys;
   - are bounded in size (8 KiB arguments, 500-character query);
   - may contain no control, format, bidi, private-use or surrogate characters;
   - must match patterns for scope ids and chunk ids.

   No argument names an operator, consumer, profile, path, URL or SQL.
4. **Binds the caller from the session**, which is the operator's installation profile. The client's `clientInfo` is recorded only as `DECLARED_BY_CLIENT_UNVERIFIED`. Assurance is `INSTALLATION_PROFILE_UNATTESTED`, and `attested_identity` is null.
5. **Requires an active OAG-6 approval.** The approval must be:
   - unexpired and unrevoked, with an active owner;
   - for the same consumer, destination and model location;
   - for the same scope membership SHA-256;
   - for the named operation;
   - for the same policy and protocol SHA-256;
   - untampered: its semantic SHA-256 is re-checked.
6. **Releases items per item and per content class**, through S09 plus `GatewayConsumer.release` (AI-D04).
7. **Writes one `aig_access_event` and commits it before returning** (AI-D08).

Results are masked:
- a content request that releases nothing reports `NO_AUTHORIZED_EVIDENCE` (status and `evidence_status`);
- error messages are never echoed;
- an unknown scope and an unconfigured scope give the same `SCOPE_NOT_APPROVED_FOR_SESSION`;
- an unknown chunk and an out-of-scope chunk give the same `PASSAGE_NOT_AVAILABLE`;
- results above 512 KiB are refused whole (`RESULT_TOO_LARGE`), never truncated.

## AI-D04 Gates per content class

Content classes are approved one by one by the owner:

| Class | Released when |
|---|---|
| `METADATA` | approval names the class and the member, **and** A8 ALLOW (+ A7 for file exchange) for the member's current rights assessment, **and** the member is current (v02, S10-F01) |
| `ANCHOR` | approval names the class and the member, **and** A8 ALLOW (+ A7) for the member's current rights assessment; no text (v02, S10-F01) |
| `PASSAGE_EXCERPT` | S09 A2 + A5 + current CAS / anchor, and approval, and A8 ALLOW (+ A7 ALLOW for file exchange) |
| `EVIDENCE_TYPED_VALUES` | S09 currently-accepted S08 evidence of a current member, and approval, and A8 (+ A7) |
| `EVIDENCE_TEXT_FIELDS` | the row above, and A5, and stored under A3, and its own class approval |

The following are never released, whatever is approved:
- quotes;
- reviewer and adjudicator rationales;
- scope labels and reasons;
- query text;
- paths and secrets.

A8 / A7 ALLOW comes from one of two sources:
- the current rights assessment (`external_ai_process` / `redistribute` = ALLOW, which migration 0002 allows only with an `owner_approval_ref`);
- a valid owner determination (`aig_rights_determination`).

A determination resolves `UNDETERMINED` to `ALLOW` only for `PUBLIC_OA_LICENSED`, which is the "owner rule per licence family" of S01 05 §4.2. A DENY is never widened. The determination is bound to the exact current rights assessment (id and semantic SHA-256), the asset and the DocumentVersion. A reassessment, a new version or a new PDF voids it, so rights never transfer to an updated PDF. `UNDETERMINED`, `DENY`, a missing, stale, corrupt or mismatched record, or an unavailable lookup all deny.

## AI-D05 Owner records (migration `0010_ai_integration_gateway.sql`)

Migration 0010 uses the table prefix `aig_` (S07 already owns `ai_*` for screening proposals). It is purely additive and append-only (no-update / no-delete triggers on every table). Migrations 0001–0009 are byte-identical to S09 v02.

| Table | Purpose |
|---|---|
| `aig_owner` | Registered human owners. Assurance is `LOCAL_OPERATOR_ASSERTED`: there is no cryptographic signature in v1. |
| `aig_consumer_profile` | The trusted installation context. Holds the consumer, transport, destination, model location, scope list and policy SHA-256. |
| `aig_rights_determination` | A8 / A7 owner determinations (AI-D04). |
| `aig_release_approval` | OAG-6 approvals. Each holds the profile, consumer, destination, model location, scope and membership SHA-256, pinned member identities (paper, version, asset, artifact, link), operations, content classes, policy and protocol SHA-256, reason, owner and expiry. Only the SHA-256 of the one-time revocation token is stored. |
| `aig_revocation` | Revokes an owner, profile, determination or approval. It is effective for every request that starts after it commits. |
| `aig_access_event` | One row per request. |
| `aig_exchange_bundle` | One row per published exchange bundle. |

No record is created automatically. Nothing in the product grants a real-world approval: the positive path exists only in synthetic tests where a test owner records it explicitly. Upgrade 9 → 10 uses the S03 backup-first path. It is tested with rollback and with backup / restore of S10 records.

## AI-D06 Claude Desktop: local stdio MCP, standard library only

`ai/mcp_stdio.py` implements the MCP base protocol subset that a read-only tool server needs:
- newline-delimited JSON-RPC 2.0;
- `initialize` with version negotiation (2025-06-18, 2025-03-26, 2024-11-05);
- the `tools` capability only;
- `notifications/initialized`, `ping`, `tools/list`, `tools/call` (`content` text block + `structuredContent` + `isError`) and `notifications/cancelled`.

Input handling:
- input is parsed strictly: no NaN, lone surrogates, duplicate keys or batches;
- lines are limited to 1 MiB;
- requests before `initialize` get -32002.

Process behaviour:
- **Stdout** carries protocol messages only. `print` is redirected to the null device, and there is no logging.
- **Network:** the server opens no network listener and makes no network call.
- **Startup** is refused (exit 3, fixed code, no path) unless the KB opens and the profile is an active `CLAUDE_DESKTOP_LOCAL_MCP` profile.
- **Requests** are processed one at a time by a worker. A cancellation suppresses a call that has not produced its response.

The official MCP Python SDK is not installed, and installing it from the network was not authorized. The implementation is therefore tested with an independent stdio client (`tests/s10_mcp_client.py`) over a real child process, and labelled:
- `MCP_STDIO_STDLIB_IMPLEMENTATION_OFFLINE_MOCK_CLIENT_TESTED`;
- `official_mcp_sdk_interop: BLOCKED_SDK_NOT_INSTALLED_NO_NETWORK_INSTALL`;
- `live_client: LOCAL_MCP_NOT_CONNECTED`;
- `claude_desktop_app: NOT_TESTED`.

No `.mcpb` Desktop Extension is built (`NOT_BUILT`). It is optional, and it would need a verified SDK-level protocol and reproducible packaging.

## AI-D07 ChatGPT: offline file exchange (the guaranteed path)

`rkb ai exchange-export` calls the same gateway (`build_rag_context`) as the `CHATGPT_FILE_EXCHANGE` profile (A7 + A8 + OAG-6). It writes three files into a new directory:
- `chatgpt_context_bundle.json`, the full envelope plus bundle metadata;
- `chatgpt_context_bundle.md`, where each passage is fenced as untrusted data with its anchor;
- `bundle_manifest.json`, the SHA-256 of the files and of the bundle.

The order of operations is:
1. the gateway audit event commits;
2. the files are written to a private temporary directory (0600 / 0700 on POSIX);
3. a privacy scan runs (e-mail addresses, drive / home paths, Authorization / Cookie headers, PDF bytes);
4. the `aig_exchange_bundle` row commits;
5. the directory is atomically renamed into place.

If any step fails, the temporary files are overwritten and deleted (best effort) and nothing is published. A denied request writes nothing at all.

Bundles expire after 1 hour by default (at most 24). A file on disk is not a permission to upload it. `rkb ai exchange-verify` must say `RELEASE_STILL_AUTHORIZED` immediately before any manual upload. It re-checks every passage through the same S09 + S10 release logic, and on a failure `--purge-if-invalid` deletes the files. The re-checks are:
- file hashes;
- the KB record;
- expiry;
- the approval;
- A7 / A8;
- member currency;
- the CAS source;
- the exact excerpt hashes.

SciVetra never uploads anything. Uploading is external AI processing (A8) and redistribution (A7).

There is no claim that ChatGPT can use a local stdio MCP server:
- ChatGPT custom MCP is remote (HTTPS, or Secure MCP Tunnel);
- desktop-only local plugins are partner-gated.

Both are `REMOTE_NOT_APPROVED` or `PARTNER_GATED_NOT_AVAILABLE`.

## AI-D08 Audit before release, minimal persistence

An `aig_access_event` row is written for every request, including invalid and denied ones. It holds:
- request id;
- profile, consumer, transport and destination;
- the SHA-256 of the declared client;
- the tool;
- the arguments SHA-256 and the query SHA-256;
- scope and approval;
- status, decision and reason codes;
- `content_revealed`;
- returned ids and excerpt hashes;
- rights ids;
- excluded reasons;
- index build;
- the SHA-256 of the result.

It never holds query text, document text, paths, tokens or error messages.

The row commits in the request transaction before the response is returned. Three kinds of failure give ERROR with empty results and no scope, and leave no row:
- the database is locked (busy timeout), read-only or cannot be opened;
- the insert fails (I/O error, disk full);
- the commit fails.

The schema enforces that `content_revealed = 1` requires status OK, an approval and a profile. A response is sent only after its event has committed. A failure after the commit, while writing to stdout, is disclosed as an over-reporting audit (it records a release that the client may not have received), never under-reporting.

## AI-D09 Prompt injection and untrusted data

Document text and metadata appear only inside `results`. Every envelope carries `content_trust: UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS`, `authority: NON_AUTHORITATIVE` and a notice. The Markdown fences passages between `BEGIN/END UNTRUSTED DOCUMENT DATA`.

A model has no way to act on injected text:
- no tool executes a query or path built from model text: query words are reduced to quoted FTS5 terms by S09;
- no tool reads files, opens URLs, runs commands, writes data, approves anything or changes scope;
- unknown tool names are `UNKNOWN_TOOL`, and the attempt is audited.

The gateway never generates answers, never merges numbers and never asserts semantic entailment (`semantic_entailment: NOT_ASSESSED`). S11 synthesis is out of scope.

## AI-D10 Activation is a separate, future, owner decision

Nothing in S10 v01 connects to any of the following:
- the Claude Desktop app;
- ChatGPT;
- a tunnel;
- a remote MCP server;
- an AI API;
- the network.

Real use needs the checklist in handoff 04: a real machine and account, a rights-proven corpus, OAG-6 per client and provider, a verified sandbox for real PDFs, and an owner-accepted deployment record. Remote consumers additionally need a later stage and a new migration.

## AI-D11 v02 correction S10-F01: no content class bypasses A8 / A7

**Finding (architect review of v01, HIGH).** v01 released `METADATA` (titles, DOIs, venues, authors) and `ANCHOR` (paper / version /
asset ids, artifact and excerpt SHA-256, page, character span) to an external consumer under an OAG-6 approval alone: the gateway
evaluated A8 / A7 only for `PASSAGE_EXCERPT` and the evidence classes, and S09 passed no rights assessment for metadata and anchors.

**Decision.** Every content class an external consumer can receive is research-derived. `GatewayConsumer.release` now applies the
consumer's `release_gates` (A8; A7 too for the file exchange) to EVERY class, on the member's CURRENT rights assessment, which must
belong to the member's own asset and DocumentVersion (`RIGHTS_ASSESSMENT_NOT_FOR_MEMBER` otherwise). A missing assessment is a DENY
(`NO_RIGHTS_ASSESSMENT`), never an implicit permission. S09 passes the current assessment for every class:
- `search_papers`: `_release_metadata` requires a current full-text member (`member_problems` empty, else
  `MEMBER_NOT_CURRENT:<problem>`) and its current assessment; a metadata-only member (Reprovane release scope) has none and is denied;
- `_authorize_chunk`: an `ANCHOR_ONLY` hit for an external consumer needs its own `ANCHOR` release (else dropped and counted
  `ANCHOR_NOT_RELEASED:<reason>`); `get_verified_passage`, `build_rag_context` (`anchor_only_hits`) and the exchange inherit this;
- the gateway's own anchor check also passes the current assessment (defence in depth).

**Faithful reporting.** The release reason names the gates actually checked (`RELEASED_UNDER_OAG6_APPROVAL_AND_CURRENT_A8`, `..._A8_A7`);
`authorization.gates_for_every_content_class` lists them; each `rights` entry records A2 / A5 / A8 / A7 decisions and a per-class
outcome. For a member with nothing released, the response keeps the gate decisions but withholds the paper id, rights assessment id
and rights class (`member: WITHHELD_NOTHING_RELEASED`); the audit row keeps the full entry. An owner-determination failure reports the
newest determination's reason.

**No metadata exemption.** The policy document states `metadata_exemption: NONE` and `member_without_current_rights_assessment: DENY`.
A bibliographic-sharing exception (e.g. Crossref-licensed facts of a metadata-only record) would need a separately designed,
provenance-based policy approved by the architect; it is proposed for review only (`OPEN_ISSUES.md` R-S10-13), not implemented.

**Versioning.** The policy meaning changed, so the policy id is `scivetra-ai-consumer-policy/2` and its SHA-256 changed (the tool
descriptions changed, so the protocol SHA-256 changed too). Every v01 profile and approval is void (`PROFILE_POLICY_CHANGED`,
`APPROVAL_POLICY_CHANGED`, `APPROVAL_PROTOCOL_CHANGED`); the operator must re-create them, so nothing approved under v01 silently
carries over. No schema change: migration 0010 is unchanged.

**Tests.** The architect's unmodified `INDEPENDENT_S10_A8_METADATA_ANCHOR_REPRO.py` (5/5) and `tests/test_s10_v02_rights_coverage.py`
(S10-SC-63..76, S10-MCP-13, S10-XCH-15). The class-by-class matrix is `S10_RIGHTS_DECISION_MATRIX.md`.

## Known limits (see `OPEN_ISSUES.md` R-S10-*)

- **Owner identity is a local operator assertion.** Approvals carry no cryptographic signature.
- **Caller identity is not attested.** The client is whatever launched the stdio process with that profile.
- **Secure deletion of bundles is best effort.** It cannot be guaranteed on SSDs or journaling file systems, and Windows ACLs are not tightened (POSIX modes only).
- **One Windows behaviour is untested.** It is not tested whether Claude Desktop shows tool output verbatim, and where it processes it.
- **The embedding is a mock.** Hybrid mode uses the mock embedding (`HYBRID_MOCK`, `semantic_model: NOT_CONFIGURED`). It is not semantic search.
