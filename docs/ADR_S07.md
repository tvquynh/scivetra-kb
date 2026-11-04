# ADR S07: AI-assisted screening PROPOSALS (0.7.0.dev2)

Status: candidate, submitted for independent architect review. Baseline: accepted S06 v02
(`SCIVETRA_S06_PDF_PARSING_v02.zip`, sha256 `fc5ee14b…9a8c`). Normative: S01 v03 `08_AI_PROPOSAL_AND_EVIDENCE_CONTRACT.md`
(AIP-1..5), `05`, `09`; handoff `SCIVETRA_S06_ACCEPT_S07_HANDOFF_v01`.

## AS-D1. Authority boundary

S07 produces **NON_AUTHORITATIVE** proposals for `TA_SCREEN` and `FT_SCREEN` only. A proposal is never a decision (AIP-1). No
code path writes `fte_decision`, `fte_adjudication`, `fte_lock`, any Reprovane table or corpus membership: the screening package
inserts only into `ai_*` tables (static test), and a full cycle leaves every pre-S07 table byte-identical (test). A human approval
is an `ADVISORY_KB_ANNOTATION`; the authoritative decision is entered by the human in Reprovane/S05 and may be referenced by an
operator-supplied `linked_authoritative_decision` (never validated against or written to Reprovane).

## AS-D2. Integration points (inspected)

| Need | Accepted source |
|---|---|
| TA metadata | `project_paper_reference.item_claims_json` (release titles) + S02 `metadata_claim` of the paper's ACTIVE DOIs (latest snapshot: title, subtitle, container, work type, dates, language; `abstract` = `COPYRIGHTED_ABSTRACT` only with content class `METADATA_AND_ABSTRACT`) |
| Ambiguous DOI | `identity_conflict` without a non-DEFER `identity_resolution_event` → `IDENTITY_CONFLICT_OPEN` |
| Retractions | `integrity_event` of the paper / DOIs → `integrity_signals` in the pack (a change makes proposals STALE) |
| FT candidate | S05 `fte_candidate` of the scope's `fte_run` |
| FT text | S06 `parse_artifact` with a RESEARCH_PARSE run on a link of the candidate's paper; `ParseService.current_access()`; `check_anchor()` |
| Human roster | new `ai_reviewer` (S05 `fte_reviewer` rosters are per run and are not reused for AI approvals) |

Scoped adaptations of S01 08 (not redefinitions): `context.project_ref` is the scope's basis (`RV_RELEASE` release import or
`FTE_RUN`); a `KB_COLLECTION` basis is not offered (a journal inventory must never become a screening basis); the payload is
`rkb-ai-response/1` (task fields of S01 08 §3) and its anchors carry `quote` and `quote_sha256` in addition to the S01 anchor
fields; `verification` and `review` are append-only tables and projections (S01 note); `uncertainty.self_reported` is the
`uncertainty` column. bbox/section paths are not used (S06 has none).

## AS-D3. ProcessingScope and input packs (AIP-5)

`ai_processing_scope` binds task, basis, an explicit member list (`members_json`, digest `membership_sha256`), the frozen
criteria set (`criteria_sha256`), content classes, `model_location_allowed = 'LOCAL_PROCESS'` (CHECK: REMOTE_API and
DESKTOP_APP_MANUAL are refused until OAG-6) and `blind_first` (default 1; 0 only with a protocol reason). Packs are built per
member BEFORE any model call: refused members are `SCOPE_DENIED` with reasons and get no pack. A pack is canonical JSON
(`rkb-ai-input-pack/1`, no timestamps) stored in CAS (schema-7 CAS reference), with `content_trust =
UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS`. FT packs contain only S06 segments, each re-verified with `check_anchor`
against the artifact (exact page, Unicode offsets, text SHA-256); `ANCHOR_ONLY` packs carry no text; `FULLTEXT_EXCERPT` needs
`text_readable` now. Refusals: rights not allowed, link not HUMAN_CONFIRMED MAIN_TEXT, IDENTITY_INSPECTION-only artifacts,
another paper's artifact, superseded parse artifact, no text layer, pack size limits.

## AS-D4. Proposals (`rkb-ai-proposal/1`) and the mock adapter

Proposals are immutable (AIP-2) and record origin, model (declared vs attested: `MOCK_DETERMINISTIC` or
`OPERATOR_DECLARED_UNVERIFIED`), template id/SHA-256, rendered-prompt SHA-256, input-pack SHA-256, status, error codes, the
normalized payload, uncertainty and the raw-response SHA-256 (raw bytes are not stored). Every response, from the mock or an
operator import, passes one strict validator: size ≤ 256 KiB, strict JSON (no NaN, bounded nesting), exact key sets, exact
task/paper/pack (no scope drift), exactly the frozen criteria, known judgements, outcomes and reason codes, bounded strings,
and a leak scan (local paths, secrets, tokenised URLs). Invalid responses are stored as `SCHEMA_INVALID` with codes and no
payload. Identical responses are `DUPLICATE_PROPOSAL_NOOP` (semantic hash). The **mock** is a deterministic, tool-less rule
matcher (imports only `hashlib`, `json`); it is a test fixture, not a language model, and its scores say nothing about any real
model. No model download, API key, network or remote AI exists in S07.

## AS-D5. Verification (AIP-3)

Append-only `ai_proposal_verification` runs (`rkb-ai-verify/1`) recheck, at verification time: current pack validity (scope,
rights, identity, artifact), FT text readability (`current_access`), every anchor (in the pack, same kind, quote hash consistent,
page equal to the segment page, offsets inside the segment, exact quote via `check_anchor` / exact metadata substring), and
that every number in a rationale appears in a verified quote of the same criterion. MET/NOT_MET without anchors is
`UNANCHORED` (state PARTIAL, never PASSED). Failures are never repaired (no anchor moving).

**S07 v02 (`rkb-ai-verify/2`, findings S07-F01 / S07-F02):**

- *Outcome consistency* (`rkb-screening-aggregation/1`; the frozen criteria set may name it as `"aggregation":
  "ALL_CRITERIA_REQUIRED"`, the default and the only accepted value): INCLUDE requires every criterion MET and no reason
  code; EXCLUDE requires at least one NOT_MET criterion and exactly the `exclude_reason_code`s of the NOT_MET criteria; UNSURE
  requires no NOT_MET, not all MET and no reason code. Violations fail `OUTCOME_CRITERIA_INCONSISTENT:*`,
  `REASON_CODE_UNSUPPORTED:*` or `REASON_CODE_MISSING:*`. The rule checks label coherence only, not scientific reasoning.
- *Numbers* (`rkb-number-match/1`): numbers are compared as complete numeric tokens, never substrings (`12` does not match
  `312`). Signs are normalized (U+2212 and U+2013 become `-`; `+` is dropped). Thousands groups of exactly three digits
  (comma, NBSP, thin or narrow space, apostrophe) are removed. The decimal point is kept, with no rounding (`12` differs from
  `12.0`). Adjacent units are allowed (`12mg`, `12%`). A decimal comma, a malformed group, a dotted version or any digit
  outside a well-formed token is AMBIGUOUS and fails `NUMBER_AMBIGUOUS`. Each number in a rationale must equal a token of a
  verified quote of the same criterion (`NUMBER_NOT_IN_QUOTE` otherwise).
- *Typed anchors*: a `METADATA_CLAIM` anchor must have `page = null` (`METADATA_ANCHOR_HAS_PAGE`); `PARSE_SEGMENT` anchors keep
  the S06 page, offset and hash checks. Nothing is repaired; the proposal row is never changed.

## AS-D6. Staleness

Derived at read time, never written: `CRITERIA_SUPERSEDED`, `INPUT_CHANGED` (the pack would now differ: metadata,
retraction signals, rights snapshot), `NOW_DENIED:<reason>` (rights, identity, superseded parse artifact, CAS). A STALE proposal
does not count as verified and cannot be accepted; reveals withhold quotes when the text is no longer readable.

## AS-D7. Human review and blind-first

`ai_reviewer` is an owner-registered HUMAN roster; labels in AI/system/model/mock/bot/llm/auto namespaces are refused by service
and CHECK. Blind-first (default): the recommendation is withheld (`show` → `WITHHELD_UNTIL_REVEAL`) until the reviewer records
an `ai_human_judgement`; every reveal is audited (`ai_proposal_reveal`, trigger-enforced); an initial judgement after a reveal is
refused. Approvals (`ai_approval`) need a recorded reveal, are refused for the operator who produced or imported the proposal
(self-approval), need reasons for REJECT / ACCEPT_WITH_EDIT, and ACCEPT needs the current verification PASSED/PARTIAL and a
non-stale proposal. All S07 tables are append-only.

## AS-D8. Export

`rkb screen export` is metadata-only (states, hashes, approval decisions; no recommendation, rationale, quote, abstract or
document text) and is scanned for document text and local paths before it is written.

## AS-D9. Out of scope / gates kept

Real PDFs, real models, cloud or desktop AI, Reprovane changes, the manual full-text inbox, S08 extraction, S09 RAG, S10 desktop,
S11 synthesis, `E:\ResearchKB`, publication. Windows R22 and the Linux root refusal from S06 are unchanged. Migration
`0007_ai_screening_proposals.sql` is additive; 0001–0006 byte-identical.
