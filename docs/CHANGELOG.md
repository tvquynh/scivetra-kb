# Change log — SciVetra KB candidate

## 1.0.1 — packaging fix: the S08 evidence package was missing (2026-11-05)

Independent review (2026-11-05) of tag `v1.0.0` (`7a19cc7`), reproduced on a clean clone and the published wheel / sdist: `src/scivetra_kb/evidence/` was not in Git, the wheel or the sdist, so S08, S09, S11 and `rkb llm extract-evidence` failed with `No module named 'scivetra_kb.evidence'`. The developer tree had the files, so the 654 tests and gates G1–G8 passed there.

| File | Change | Proof |
|---|---|---|
| `src/scivetra_kb/evidence/` (now tracked) | Restored, byte-identical to the accepted S12 release-candidate ZIP | REL-SOURCE-TRACKED |
| `.gitignore` | `/evidence/`, `/build/`, `/dist/` anchored at the root | REL-GITIGNORE-ROOT-ONLY |
| `.gitattributes` | `* -text`: no line-ending conversion except the `.cmd` / `.ps1` / `.sh` launchers. Found while verifying 1.0.1: a Windows clone with `core.autocrlf=true` turned SHA-256-pinned LF files (migrations, fixtures, baselines) into CRLF and 62 tests failed | REL-EXACT-BYTES |
| `tools/s12_release_readiness.py` | `copy_tree` (root-only excludes) for G5 / G7; G7 requires every source package file in wheel and sdist, every internal import resolved, a wheel rebuilt from the sdist with the same files, all modules importable from both installs, and the S08, S09, S10, S11 and LLM-L2 behavioural tests (108) passing against the installed wheel from outside the checkout | REL-*, G7 |
| `tests/test_release_integrity.py` (new) | 7 ids incl. negative cases (package removed → detected) | REL-* |
| `.github/workflows/ci.yml` (new) | CI on Windows / Linux / macOS, Python 3.11 and 3.14: clean checkout, suite, build, clean install | not yet run |
| `docs/CAPABILITY_MATRIX.md` and status docs, `src/scivetra_kb/cli.py` help text | One capability vocabulary; stale "release candidate" / "simulator only" statements corrected | — |

## 1.0.0 — first release on GitHub (2026-11-05) — **broken: S08 package missing, use 1.0.1**

Owner decision (ADR_S12 RL-D06). Contains 1.0.0rc1 plus ADR_LLM stages L1 and L2 below. Schema 11 and migrations 0001–0011 unchanged.

| File | Change | Proof |
|---|---|---|
| `src/scivetra_kb/llm/evidence_extract.py` | Compact answer format `scivetra-llm-evidence/1`, prompt `/3` with class catalogue and worked example; quotes located verbatim by SciVetra (LL-D15) | LLM-L2-* |
| `tools/llm_local_model_check.py` (new) | Real local model check on synthetic papers (LL-D16) | real run, see `RELEASE_NOTES_1.0.0.md` |
| `config/llm-providers.example.toml` | Ollama `qwen2.5:7b-instruct` first, `llama3.2:3b` as a second family for consensus | LLM-L1-EXAMPLE-CONFIG |
| `src/scivetra_kb/__init__.py`, marked test version lines, docs | Version `1.0.0`; `RELEASE_NOTES_1.0.0.md` | G1 |

### ADR_LLM stage L2: local models (loopback only) and S08 extraction

| File | Change | Proof |
|---|---|---|
| `src/scivetra_llm_transport/` (new, separate component; `scivetra_kb` stays network-free) | Loopback-only OpenAI-compatible transport (no DNS, no proxy, no redirect, 2 MiB cap; remote refused) | LLM-L2-LOOPBACK-ONLY, -HTTP-MAPPING, -NO-PROXY-NO-REDIRECT, -REQUEST-SHAPE |
| `src/scivetra_kb/llm/evidence_extract.py` (new) | S08 extraction through the router (LOCAL_ONLY; S08 import, validation and verification unchanged; optional consensus) | LLM-L2-S08-*, -FABRICATED-QUOTE, -SCHEMA-VIOLATION, -INJECTION-FOLLOWED, -CONSENSUS, -RIGHTS-RECHECK, -NO-ANSWER |
| `src/scivetra_kb/cli.py` | `rkb llm ping`, `rkb llm extract-evidence` | LLM-L2-PING |
| `tools/s12_release_readiness.py`, `pyproject.toml` | G5 allowlist entry and wheel inclusion for `scivetra_llm_transport` | S12-SECURITY-SCAN, G7 |
| `docs/ADR_LLM.md` | LL-D13, LL-D14; migration 0012 not needed | — |

### ADR_LLM stage L1: free-provider LLM routing (offline)

| File | Change | Proof |
|---|---|---|
| `src/scivetra_kb/llm/` (new: `config.py`, `router.py`, `simulate.py`) | Provider config `scivetra-llm-providers/1` (keys only by environment variable name), data classes from rights, rotation with Retry-After, rpm / rpd / tpm limits, circuit breaker, two-family consensus, content-free call log; scripted FakeTransport only (no network code) | `tests/test_llm_router.py` (LLM-L1-*) |
| `src/scivetra_kb/cli.py` | `rkb llm check-config / plan / simulate` | LLM-L1-CLI |
| `config/llm-providers.example.toml` (new) | Example provider list (local Ollama + free remote tiers; policies to be verified by the owner) | LLM-L1-EXAMPLE-CONFIG |
| `docs/ADR_LLM.md`, `guide/12-free-llm-providers.md`, `huong-dan/12-llm-mien-phi.md` (new) | Design LL-D01..D12; user guides | — |
| `tools/required_ids.json`, `tools/s12_command_reference.py`, `docs/COMMAND_REFERENCE.md` | 21 LLM-L1 ids; 3 new commands | G6 |

## 1.0.0rc1 — S12: local release candidate (2026-11-05)

Baseline: S11 v03 (`67e84e3b…1eab`). No schema or behaviour change: migrations 0001–0011 byte-identical; `src/` changes only the version.

| File | Change | Proof |
|---|---|---|
| `tools/s12_release_readiness.py` (new) | Release gates G1–G8: version / docs, migrations, upgrade chain 1..10 → 11 with rollback and backup, populated backup / restore, security scan, CLI documentation, offline wheel / sdist / venv install, dependency inventory | `evidence/release_readiness.json`, S12-* tests |
| `tools/s12_command_reference.py` (new), `docs/COMMAND_REFERENCE.md` (generated) | Every `rkb` command with its options | G6 |
| `tools/s12_migration_hashes.json` (new) | Pinned 0001–0011 hashes | G2 |
| `docs/ADR_S12.md`, `SCIVETRA_USER_MANUAL.md`, `OPERATOR_RUNBOOK.md`, `SECURITY_REVIEW_S12.md`, `CAPABILITY_MATRIX.md`, `RELEASE_NOTES_1.0.0rc1.md` (new) | Release documentation | G1 |
| `tests/test_s12_release.py` (new) | Release gates in the suite | S12-* |
| `src/scivetra_kb/__init__.py`, `pyproject.toml`, marked test version lines | `1.0.0rc1`; description covers S02–S11 | S11-STATIC-VERSION |

## 0.11.0.dev3 — S11 v03: release-time export currency (2026-11-04)

Architect review of S11 v02: REVISE (finding S11-F05). Baseline: S11 v02 (`f1f22c2e…73d9`). Migrations 0001–0011 byte-identical to v02.

| Finding | File | Change | Proof tests |
|---|---|---|---|
| S11-F05 | `synthesis/service.py` | Export release (final re-derivation of the report incl. rights and CAS, bundle and staged-file identity, leak scan, atomic rename, PUBLISHED event) inside one SQLite write transaction; refusals `EXPORT_RELEASE_REFUSED` / `EXPORT_RELEASE_FAILED` with ABANDONED outcomes; withdrawal on commit failure | S11-V3-* |
| — | `tests/test_s11_v03_release.py` (new) | revocation and PDF deletion before release, serialization of writers, lock timeout, release / commit faults, staged-file tampering, newer audit, positive control | — |
| — | version | `0.11.0.dev3` (marked lines) | S11-STATIC-VERSION |

## 0.11.0.dev2 — S11 v02: corrective revision (2026-11-04)

Architect review of S11 v01: REVISE (findings S11-F01..F04). Baseline: S11 v01 (`d45e0d11…456f`). Migrations 0001–0011 byte-identical to
v01 (0001–0010 to S10 v02). Rubric `scivetra-synthesis-rubric/2`. Offline and synthetic only.

| Finding | File | Change | Proof tests |
|---|---|---|---|
| S11-F01 | `synthesis/service.py` | Gate A5 applied NOW to every claim statement and spec text projection (audit report, `claim-add`, CLI, export files) and to review reason codes: `WITHHELD_A5` with SHA-256 / length, fail closed when a denied source cannot be read; export leak scan checks every line for short excerpts of denied sources; stored rows unchanged | S11-V2-F01-* |
| S11-F02 | `synthesis/rules.py` | COUNT member-level ascertainment POSITIVE / NEGATIVE / UNKNOWN (`count_ascertainment`); unknown is never a negative; a zero count cites accepted NOT_REPORTED records | S11-V2-F02-* |
| S11-F03 | `synthesis/rules.py`, `service.py` | Visible statement re-checked at every audit (lexical vocabularies for dataset, metric, split, PE type, temporal context, comparison direction; negation); only template statements are machine-SUPPORTED; author statements PARTIAL until humans confirm the exact statement (statement SHA-256 in the link graph); consistency no longer refused at creation (`STATEMENT_INPUT_REFUSED` only for URLs / secrets / invalid text) | S11-V2-F03-* |
| S11-F04 | `synthesis/rules.py` | Signed numbers compared exactly (ASCII and Unicode minus); unit beside each number checked | S11-V2-F04-*, S11-V2-RULE-* |
| — | `tools/s11_evaluation.py`, `tools/s11_eval_compare.py` (new) | 284 cases (40 new: author statements, comparison statements, COUNT ascertainment on four scopes with an independent count oracle); row comparison with v01 | evaluation evidence |
| — | `tests/test_s11_v02_corrections.py` (new); `test_s11_rules.py`, `test_s11_synthesis.py` | new tests; two v01 S11 tests updated to rubric /2 (statements recorded and audited instead of refused; a COUNT with an unknown member is PARTIAL) | — |
| — | version | `0.11.0.dev2` (literal on the marked S11 adaptation lines) | S11-STATIC-VERSION |

## 0.11.0.dev1 — S11 v01: scientific synthesis and citation audit (2026-11-03)

Baseline: architect-accepted S10 v02 (`SCIVETRA_S10_DESKTOP_AI_INTEGRATION_v02.zip`, `e6bbc0f4…b9aa`). Migrations 0001–0010
byte-identical. Offline and synthetic only; no model, AI account or network.

| File | Change | Proof tests |
|---|---|---|
| `core/migrations/0011_scientific_synthesis.sql` (new) | 9 append-only `syn_*` tables: scope, claim, snapshot, audit run, claim audit, contradiction, review, export event, share request; CHECKs for authority / publication / markers / LOCAL_REVIEW_ONLY / DENIED | S11-MIG-*, S11-UPGRADE-10-11 |
| `synthesis/rules.py` (new) | rubric `scivetra-synthesis-rubric/1`, claim spec `scivetra-claim-spec/1`, statement discipline, ComparisonKey / comparability, support test, derived differences, contradictions, comparison groups (pure) | `tests/test_s11_rules.py` |
| `synthesis/service.py` (new) | scope pinning, snapshot re-derivation (S08 state, CAS, anchors), audit with numbered references and 100 % traceability invariant, human review with derived / stale state, audited export, denied share | `tests/test_s11_synthesis.py`, `test_s11_adversarial.py` |
| `cli.py` | `rkb synth scope-create / claim-add / audit / review / status / reviews / export / share` | S11-CLI |
| tests, tools, docs | 5 S11 test modules, `tools/s11_evaluation.py` (32 studies, 244 claims), `tools/s11_upgrade_rehearsal.py`, `tools/s11_scenario_catalogue.py`, `tools/s10v02_*` baselines, ADR_S11, beginner guide, 31 marked literal adaptations (`S11_TEST_ADAPTATIONS.md`) | S11-TEST-ADAPTATIONS |

## 0.10.0.dev2 — S10 v02 corrective revision: S10-F01 (2026-11-03)

Baseline: own S10 v01 (`SCIVETRA_S10_DESKTOP_AI_INTEGRATION_v01.zip`, `d61abc39…fdc8c`; identical to the copy in the revision
handoff). No migration change (0001–0010 identical to v01).

| File | Change | Proof tests |
|---|---|---|
| `ai/policy.py` | policy `/2`: `RESEARCH_DERIVED` = every class; per-consumer `release_gates` (A8; A7 + A8); `metadata_exemption: NONE`; tool descriptions say METADATA / ANCHOR need A8. Policy and protocol SHA-256 change, voiding v01 profiles and approvals | S10-SC-74 |
| `ai/gateway.py` | `release` applies A8 / A7 to every class on the member's current assessment (must match the member's asset and version); no assessment = DENY; per-class outcome in `rights`; unreleased members' ids withheld from the response (kept in audit); release reason names the gates; anchor check passes the current assessment | S10-SC-63..69, architect reproducer 5/5 |
| `retrieval/service.py` | `search_papers` (external): `_release_metadata` on a current member's current assessment; `_authorize_chunk` (external): ANCHOR_ONLY hits need an ANCHOR release. LOCAL_CLI unchanged | S10-SC-63..73, S09 benchmark identical |
| `ai/records.py` | the reported determination failure is the newest determination's reason | S10-SC-71 |
| `retrieval/consumers.py` | interface doc: the current assessment is passed for every class; `None` must deny | — |
| tests, docs, tools | `tests/test_s10_v02_rights_coverage.py` (12 tests); SC-58 now expects a metadata-only scope to be denied; ADR AI-D11; `S10_RIGHTS_DECISION_MATRIX.md`; guides; 16 new required ids; version literals → `0.10.0.dev2` | S10-DOCS |

## 0.10.0.dev1 — S10 v01: desktop and AI integration gateway (2026-11-03)

Baseline: architect-accepted S09 v02 (`SCIVETRA_S09_SEMANTIC_SEARCH_RAG_v02.zip`, `3cb8565b…86fc`). Migrations 0001–0009
byte-identical (equal to the architect's independent hashes). Offline and synthetic only; no AI client, account, model or network.

| File | Change | Proof tests |
|---|---|---|
| `core/migrations/0010_ai_integration_gateway.sql` (new) | `aig_owner`, `aig_consumer_profile`, `aig_rights_determination`, `aig_release_approval`, `aig_revocation`, `aig_access_event`, `aig_exchange_bundle` (append-only; remote / API consumers not representable) | S10-MIG-*, S10-UPGRADE-9-10, S10-BACKUP-RESTORE |
| `retrieval/consumers.py` (new), `retrieval/service.py` | explicit `rkb-retrieval-consumer/1` interface: S10 consumers only as server-side objects, per-item `release` on top of every S09 check, S09 audit handed to the gateway; per-call cache owned by one thread (`CONCURRENT_SERVICE_USE`). LOCAL_CLI unchanged (benchmark hashes equal) | S10-SC-29, S10-SC-52, S09 suite |
| `ai/` (new: `policy`, `records`, `gateway`, `mcp_stdio`, `exchange`) | consumer policy and tool protocol (hash-bound); owner records; one transport-independent gateway (fresh per request, `BEGIN IMMEDIATE`, audit before release); stdlib MCP stdio server; ChatGPT file exchange with re-validation and purge | `tests/test_s10_*.py` |
| `cli.py` | `rkb ai owner-register / profile-create / determine-rights / approve / revoke / status / access-log / mcp-serve / claude-desktop-config / exchange-export / exchange-verify` | S10-XCH-14, S10-MCP-* |
| tests, tools, config, docs | 5 S10 test modules, independent stdio client, `tools/s10_demo.py`, `tools/s10_scenario_catalogue.py`, `tools/s10_mcp_sdk_interop.py`, `tools/s10_upgrade_rehearsal.py`, `tools/s09v02_*` baselines, `config/s10/` templates, ADR_S10 and beginner guides, 28 marked literal adaptations (`S10_TEST_ADAPTATIONS.md`) | S10-TEST-ADAPTATIONS |

## 0.9.0.dev2 — S09 v02 corrective revision (2026-11-03)

Baseline: S09 v01 (`SCIVETRA_S09_SEMANTIC_SEARCH_RAG_v01.zip`, `cdfa10eb…2a5b`). Scope: architect findings S09-F01, S09-F02 and
S09-F03 only. Migrations 0001–0009 unchanged (0001–0008 byte-identical to S08 v03; 0009 byte-identical to v01). Index schema,
chunker, RRF policy and context schema unchanged.

| File | Change | Proof tests |
|---|---|---|
| `retrieval/service.py` | F01: per-call caches only (`_per_call`); F02: scope inside the candidate SQL via one `json_each` parameter, exact member bindings, window growth ×4 when authorization drops hits, `candidate_window` reported; F03: per-call CAS re-read, `_source_problems` (S06 artifact + source PDF re-hash) → `BLOCKED:SOURCE_INTEGRITY:*`, `index verify` re-hashes source PDFs | `tests/test_s09_v02_regressions.py` (8 tests, 12 IDs) |
| tests, docs | 8 regression tests on one long-lived service instance; ADR RS-D11; OPEN_ISSUES R8–R9; version `0.9.0.dev2` (5 marked version literals and the S09 docs test) | — |

## 0.9.0.dev1 — S09 v01: local search and rights-gated RAG context (2026-11-02)

Baseline: accepted S08 v03 (`SCIVETRA_S08_SCIENTIFIC_EVIDENCE_EXTRACTION_v03.zip`, `6bbac0c1…22d5`). Migrations 0001–0008 byte-identical.

| File | Change | Proof tests |
|---|---|---|
| `core/migrations/0009_local_retrieval.sql` (new) | `rt_scope`, `rt_index_build`, `rt_access_event` (append-only; LOCAL_CLI only; NON_AUTHORITATIVE) | S09-MIG-* |
| `retrieval/` (new: `chunker`, `embedding`, `vectors`, `query`, `index`, `service`) | explicit scopes; derived FTS5 / vector index with atomic publish; per-hit rights and currency checks; lexical BM25, mock / optional local embeddings, NumPy or pure-Python cosine, RRF hybrid; paper, passage and evidence search; deterministic tool-less RAG context; audit | `tests/test_s09_*.py` |
| `core/doctor.py`, `core/manifest.py` | retrieval invariants and manifest (schema ≥ 9) | S09-MIG-*, S09-NO-AUTHORITY-WRITES |
| `cli.py` | `rkb retrieval`, `rkb index`, `rkb search`, `rkb passage`, `rkb rag` | S09-CLI-* |
| tests, tools, docs | 3 S09 test modules, `tools/s09_benchmark.py`, `tools/s09_upgrade_rehearsal.py`, `tools/s08v03_*` baselines, ADR_S09, RETRIEVAL_USER_GUIDE, 25 marked literal adaptations | S09-TEST-ADAPTATIONS |

## 0.8.0.dev3 — S08 v03 corrective revision (2026-11-02)

Baseline: S08 v02 (`SCIVETRA_S08_SCIENTIFIC_EVIDENCE_EXTRACTION_v02.zip`, `86e977a0…13de`). Scope: architect finding S08-F03 only.
Migrations 0001–0008 unchanged (0008 byte-identical to v02).

| File | Change | Proof tests |
|---|---|---|
| `evidence/excerpt.py` (new) | `rkb-excerpt-detect/1`: NFKC / case-fold word windows (6 words; whole text ≥ 4 words) against the pinned S06 text | S08-V03-EXCERPT-POLICY |
| `evidence/service.py` | gate A4 on review / adjudication rationales, reason codes, scope label and reasons (`HUMAN_TEXT_SOURCE_EXCERPT_REFUSED`, `HUMAN_TEXT_SOURCE_CHECK_UNAVAILABLE`); `audit` projects rationales and reason codes under current A5 (`WITHHELD`, reasons, SHA-256, length) | `tests/test_s08_v03_human_text.py` |
| tests, docs | 6 new tests (14 contract IDs); ADR ES-D15, guide, OPEN_ISSUES E11–E12; version `0.8.0.dev3` | — |

## 0.8.0.dev2 — S08 v02 corrective revision (2026-11-02)

Baseline: S08 v01 (`SCIVETRA_S08_SCIENTIFIC_EVIDENCE_EXTRACTION_v01.zip`, `e0616a65…8464`). Scope: architect findings S08-F01 and
S08-F02 only. Migrations 0001–0007 unchanged; the not-yet-accepted 0008 changed in place (ADR_S08 ES-D14).

| File | Change | Finding | Proof tests |
|---|---|---|---|
| `core/migrations/0008_scientific_evidence_extraction.sql` | `ev_draft.text_storage` / `withheld_text_json` + CHECKs; verification state `NOT_ASSESSABLE`; trigger `ev_reveal_matches_storage`; `ev_export_event` lifecycle (`phase`, `reservation_id`, `target_path_hash`, `folder_name`, `outcome_code`) + trigger | F01, F02 | S08-V02-* |
| `evidence/service.py` | text fields withheld under A3 (hash + position), hash re-check in verify, `show` / `audit` without text, `reveal` checks current A5; export: durable reservation before publication, fsync + atomic rename, withdrawal on completion failure, recovery, `export_history` | F01, F02 | `tests/test_s08_v02_rights_export.py` |
| `cli.py` | `rkb evidence exports` | F02 | S08-V02-R6-CRASH-RECOVERY |
| `core/doctor.py`, `core/manifest.py` | invariants `EV_EXPORT_OUTCOME_WITHOUT_RESERVATION`, `EV_TEXT_STORED_WITHOUT_GATE_A3`; manifest lists export `phase:sha256` | F01, F02 | S08-V02-R6-SCHEMA-LIFECYCLE |
| `tools/s08_upgrade_rehearsal.py` | optional phase E: S08 v01 schema-8 KB is refused (`MIGRATION_DRIFT`), recovery from the kept pre-upgrade file | — | rehearsal report |
| tests, docs | 8 new tests (20 contract IDs); 2 S08 v01 assertions updated (`S08_TEST_ADAPTATIONS.md`); ADR ES-D14, guide, OPEN_ISSUES E8–E10; version `0.8.0.dev2` | — | — |

## 0.8.0.dev1 — S08 v01: scientific evidence extraction (2026-11-02)

Baseline: accepted S07 v02 (`SCIVETRA_S07_AI_ASSISTED_SCREENING_v02.zip`, `d2ad98fd…464f`). Migrations 0001–0007 byte-identical.

| File | Change | Proof tests |
|---|---|---|
| `core/migrations/0008_scientific_evidence_extraction.sql` (new) | 10 append-only `ev_*` tables: profiles, scopes, input packs, extraction runs, drafts, verifications, reveals, reviews, adjudications, export events; LOCAL_PROCESS-only and NOT_ASSESSED-entailment CHECKs; consistency, self-review, provisional, ACCEPT-needs-verification and evidence-seen triggers | S08-MIG-*, S08-APPEND-ONLY, S08-TRIGGER-ENFORCED |
| `evidence/profiles.py`, `schema.py`, `mock.py`, `service.py` (new) | EvidenceService (profile → scope → packs → runs → drafts → structural verification → audited reveal → review / correction / adjudication → metadata-only export), strict response validator, numeric/unit/condition checks (`rkb-number-match/1`), general and malware-detection profiles, deterministic tool-less mock | S08-* |
| `core/cas.py`, `core/manifest.py`, `core/doctor.py` | schema-8 CAS reference for evidence packs; evidence manifest and six invariants | S08-BACKUP-RESTORE, S08-DOCTOR-INVARIANTS |
| `cli.py` | `rkb evidence …` (15 commands) | S08-CLI-* |
| tests, tools, docs | `test_s08_evidence.py`, `test_s08_faults.py`, `test_s08_operational.py`, `tools/s08_evaluation.py`, `tools/s08_upgrade_rehearsal.py`, `tools/s07v02_*` baselines, ADR_S08, EVIDENCE_USER_GUIDE, 23 marked literal adaptations | S08-TEST-ADAPTATIONS |

## 0.7.0.dev2 — S07 v02 corrective revision (2026-11-02)

Baseline: S07 v01 (`02055f58…f93a`). Scope: architect findings S07-F01 and S07-F02 only. Migrations 0001–0007 unchanged
(0007 byte-identical to v01); proposals are unchanged, only verification became stricter (`rkb-ai-verify/2`).

| File | Change | Finding | Proof tests |
|---|---|---|---|
| `screening/schema.py` | `aggregation_failures` (rkb-screening-aggregation/1), `numeric_tokens` (rkb-number-match/1) | F01, F02 | S07-F01-*, S07-F02-* |
| `screening/service.py` | verify: OUTCOME_CONSISTENCY check, exact numeric tokens with ambiguity failure, METADATA_CLAIM page must be null; criteria may name the aggregation rule | F01, F02 | `tests/test_s07_v02.py` |
| `tests/test_s07_v02.py` (new) | 6 regression tests (22 contract IDs) incl. positive controls | F01, F02 | themselves |
| docs | ADR_S07 AS-D5, OPEN_ISSUES Q7 | — | — |

## 0.7.0.dev1 — S07 v01: AI-assisted screening PROPOSALS (2026-11-02)

Baseline: accepted S06 v02 (`SCIVETRA_S06_PDF_PARSING_v02.zip`, `fc5ee14b…9a8c`). Migrations 0001–0006 byte-identical.

| File | Change | Proof tests |
|---|---|---|
| `core/migrations/0007_ai_screening_proposals.sql` (new) | 9 append-only `ai_*` tables: criteria sets, processing scopes, input packs, proposals, verifications, HUMAN roster, initial judgements, reveals, approvals; LOCAL_PROCESS-only CHECKs; blind-first, reveal, self-approval and ACCEPT-needs-verification triggers | S07-MIG-*, S07-SQL-GUARDS |
| `screening/service.py`, `schema.py`, `mock.py` (new) | ScreeningService (scope → packs → proposals → verification → blind-first reveal → advisory approval), strict response validator, deterministic tool-less mock | S07-* |
| `core/cas.py`, `core/manifest.py`, `core/doctor.py` | schema-7 CAS reference for input packs; screening manifest and invariants | S07-BACKUP-RESTORE, S07-DOCTOR-INVARIANTS |
| `cli.py` | `rkb screen …` (13 commands) | S07-CLI-* |
| tests, tools, docs | `test_s07_screening.py`, `test_s07_operational.py`, `tools/s07_evaluation.py`, ADR_S07, SCREENING_USER_GUIDE, 20 marked literal adaptations | S07-TEST-ADAPTATIONS |

**S06 v02 documentation errata (architect, non-blocking), fixed in this cumulative delivery:** the S06 top-level beginner guide
named the v01 ZIP and source folder, contained three literal carriage returns in `reference\dependencies` paths and used only
cmd.exe `set`. The S07 `05_LOCAL_INSTALL_AND_SMOKE_GUIDE.md` and `docs/SCREENING_USER_GUIDE.md` use the current names, contain no
control characters (checked by the package builder) and give PowerShell `$env:` syntax next to cmd.exe. The accepted S06 ZIP is
unmodified.

## 0.6.0.dev2 — S06 v02 corrective revision (2026-11-02)

Baseline: S06 v01 (`SCIVETRA_S06_PDF_PARSING_v01.zip`, `59167de4…2e10`). Scope: architect findings S06-F01 and S06-F02 plus the
Linux root caveat. Migrations 0001–0005 byte-identical; 0006 (unaccepted S06 migration) adjusted for the export state machine.

| File | Change | Finding | Proof tests |
|---|---|---|---|
| `parse/approved_parsers.py` (new) | Reviewed trust anchor: official wheel SHA-256 (architect-recorded) + official package-file fingerprint | F01 | S06-F01-* |
| `parse/runtime.py` | Identity (folder / METADATA / `_version.py` / pin), complete RECORD integrity, provenance against the anchor; wheel or site directory | F01 | S06-F01-* |
| `parse/parse_child.py`, `parse/sandbox.py` | Child reports imported version + fingerprint; parent rejects mismatch; root labelled and refused | F01, root | S06-F01-CHILD-IDENTITY, S06-ROOT-* |
| `parse/service.py` | Staged, re-verified parser copy per run; provenance in artifacts and runs; export state machine, reconciliation, `export_status` | F01, F02 | S06-F01-*, S06-F02-* |
| `assets/sandbox.py`, `assets/service.py`, `cli.py` | Identity + privilege checks in G1 v2 intake; `--allow-privileged-account-for-synthetic-input`; `rkb parse export-status` | F01, F02, root | S06-ROOT-*, S06-CLI-* |
| `core/migrations/0006_pdf_parse_artifacts.sql` | `parse_export_event`: `state`, `authorization_event_id` (UNIQUE), `export_folder_name`, `detail_json`, terminal-matches-authorization trigger | F02 | S06-F02-SQL-* |
| `core/doctor.py`, `core/manifest.py` | `PARSE_EXPORT_RECOVERY_REQUIRED` warning; export state in the parse manifest | F02 | S06-F02-DB-DOWN-RECOVERY |
| `tests/test_s06_v02.py` (new), `tests/s06_helpers.py`, two S06 v01 tests | 13 regressions; helpers use the official wheel; v01 expectations updated to the stricter behaviour | F01, F02 | themselves |
| `tools/s06_parser_provenance.py` (new), `tools/s06_install_test.py` | Provenance derivation; install with `--require-hashes` from the official wheel | F01 | install test |

## 0.6.0.dev1 — S06 v01: local PDF parsing and structured extraction (2026-11-02)

Baseline: accepted S05 v02 (`SCIVETRA_S05_FULLTEXT_ELIGIBILITY_v02.zip`, `95cff052…e69a`). Migrations 0001–0005 byte-identical.

| File | Change | Rationale | Proof tests |
|---|---|---|---|
| `core/migrations/0006_pdf_parse_artifacts.sql` (new) | `parse_artifact`, `parse_run`, `parse_export_event`; append-only; SQL gates for rights / link / identity | PZ-D7, PZ-D4 | S06-MIG-*, S06-SQL-GATES |
| `parse/parse_child.py` (new) | Sandboxed pypdf child: OS memory/CPU/process limits, audit hook, bounded strict validation and extraction, quality flags | PZ-D5, PZ-D6, C17 | S06-SANDBOX-*, S06-FMT-*, S06-LIMIT-* |
| `parse/sandbox.py`, `parse/runtime.py` (new) | Parent runner (timeout, output cap); pinned parser verification against RECORD | PZ-D2, PZ-D5 | S06-PARSER-*, S06-LIMIT-* |
| `parse/service.py` (new) | Gates, private staged copy, canonical `parse-artifact/1`, reuse/recheck, current-rights reads, verify, anchors, exports | PZ-D4..D9 | S06-* |
| `assets/sandbox.py`, `assets/service.py` | `rkb-pdf-structure/2` (`pdf_validator="2"`); default unchanged (v1) | C17, PZ-D3 | S06-C17-* |
| `core/cas.py`, `core/manifest.py`, `core/doctor.py` | Schema-6 CAS reference for derived artifacts; parse manifest and invariants in `doctor --full` | PZ-D7 | S06-DOCTOR-*, S06-BACKUP-* |
| `cli.py` | `rkb parse run/status/show/verify/verify-anchor/export/runtime-check`; `asset register --pdf-validator/--parser-site` | PZ-D9 | S06-CLI-* |
| `pyproject.toml` | optional extra `pdf = ["pypdf==6.12.1"]` | PZ-D2 | S06-STATIC-SYNTAX, install test |
| tests, tools, docs | 5 new test modules; pilot, oracle, upgrade rehearsal, CLI smoke, install test; ADR_S06, user guide, OCR contract; 22 marked literal adaptations | — | S06-TEST-ADAPTATIONS |

## 0.5.0.dev2 — S05 v02 corrective revision (2026-11-02)

Architect findings of `SCIVETRA_S05_V02_REVISION_HANDOFF_v01` (`docs/ADR_S05.md` FTE-D17/FTE-D18, `REVIEW_FIXES.md`):

- **S05-F01 (HIGH)** — a self-asserted external Stage-11B reference could become evidence-ready. `attach_external` now
  refuses self-asserted references (`EXTERNAL_SELF_ASSERTION_REFUSED`), verifies the actual read-only Stage-11B export
  with a trusted digest (accepted S04 reader), binds release, item and selected artifact, records the verification level,
  and never marks external references evidence-ready. CLI: `--rv11b-export-dir` + `--rv11b-expected-manifest-sha256`
  (+ optional `--rv11b-verify-json`, `--rv11b-expected-artifact-sha256`) replace `--rv11b-artifact-file`.
- **S05-F02 (MEDIUM-HIGH)** — rights or identity changes did not invalidate votes in projections. A shared, CAS-free
  currency check now makes such candidates PENDING `STALE_EVIDENCE_NOT_CURRENT` in state, summary, queue, provisional
  export, resolve and lock; new decisions on stale evidence are refused (`EVIDENCE_NOT_CURRENT`). Locked snapshots stay
  historical; later drift is reported as `post_lock_drift` / `current_evidence_drift`.
- Pilot: candidates 95–99 now use human-confirmed KB assets whose bytes equal a verified Stage-11B selection (no
  fabricated external verification); 85–89 carry verified, non-ready Stage-11B references. Accounting unchanged
  (39 / 12 / 18 / 31).
- Migrations `0001`–`0005` byte-identical to v01. New tests `tests/test_fte_v02.py` (10); 27 new contract IDs.

## 0.5.0.dev1 — S05 Full-text Eligibility CANDIDATE workflow (2026-11-01)

Baseline: accepted S04J v02 (0.4.1.dev2). KB-side, **NON_AUTHORITATIVE** staging of human full-text eligibility review
(`docs/ADR_S05.md`, `docs/FTE_USER_GUIDE.md`).

- Migration `0005_fulltext_eligibility_staging.sql` (schema 5), additive; `0001`–`0004` byte-identical. Explicit
  backup-first upgrade 4 → 5 and 3 → 4 → 5; rollback by verified pre-upgrade backup.
- New package `scivetra_kb.eligibility` (`protocol.py`, `service.py`, `handoff.py`) and `rkb fte` commands: `sources`,
  `protocol-create`, `create`, `status`, `candidates`, `queue`, `summary`, `attach-evidence`, `attempt`, `decide`,
  `adjudicate`, `resolve`, `lock`, `export`, `verify`.
- Source = exactly one verified, SEALED Reprovane Stage-11 TA release imported by S03; journal inventories refused.
- Human-only roster, independent assignments with conflicts of interest, append-only decisions with explicit
  supersession, adjudication only on real disagreement, evidence fingerprints and stale-vote detection.
- Queue flags incl. C20 DOI-echo mismatch and integrity (retraction) warnings; acquisition attempts are operational only.
- Lock stores the `fte-handoff/1` manifest in CAS (new schema-5 CAS reference `fte_lock.handoff_manifest_sha256`,
  covered by `doctor --full`, backup verification and restore). Deterministic `fte-export/1` with offline verification.
- `doctor --full` adds eligibility invariants; backup `domain_summary` adds the eligibility manifest.
- 14 accepted test literals adapted (`docs/S05_TEST_ADAPTATIONS.md`). New docs: RV-FTE future handoff contract (proposal
  only, OAG-5 CLOSED) and Manual Download Inbox requirements (not implemented).

## 0.4.1.dev2 — S04J v02 corrective revision (2026-11-01)

Architect finding **S04J-F01 (HIGH)**: Crossref harvesting crawled only the first selected ISSN of a multi-ISSN journal and
could report COMPLETE. Fixed in `journal/harvest.py`, `journal/reconcile.py` and `journal/export.py`
(`REVIEW_FIXES.md`, `docs/ADR_S04J.md` JH-D16..D18). Migration `0004` is unchanged; `0001`-`0003` are unchanged.

- **Explicit Crossref ISSN query plan** (`JournalHarvester.crossref_issn_plan`), stored in the run's `query_json.issn_plan`:
  - every requested ISSN that the Crossref journal lookup resolved;
  - every sibling ISSN listed by a resolved Crossref journal record;
  - sorted, each entry with its evidence (basis, journal record, lookup page).
- Requested ISSNs that are neither resolved nor listed are recorded in `issns_requested_not_resolved`; they keep the
  inventory from being COMPLETE.
- **One checkpointed query branch per planned ISSN in the same run.** Each branch has its own:
  - count probe and count drift;
  - cursor chain, pass number and bounded replay;
  - failure record (`PAGE_FAILED` with `issn` and `branch_state`).
- **Resume** continues only unfinished ISSN queries. A query that already terminated is never re-requested; one that
  already failed stays failed.
- **Aggregate state:**
  - COMPLETE (`ALL_PLANNED_ISSN_QUERIES_TERMINATED`, or the single query's own stop reason) only if every planned query
    terminated and no requested ISSN is unresolved;
  - otherwise PARTIAL, or BLOCKED when no query produced data, with
    `ISSN_QUERY_<state>:<issn>:<reason>;REQUESTED_ISSN_NOT_RESOLVED:<issn>`;
  - a 403 stops the run, and the remaining ISSNs are recorded as `NOT_ATTEMPTED_...`.
- **Per-ISSN provenance:**
  - observations are de-duplicated per (run, ISSN query, record, content), so a DOI returned by two ISSN queries keeps
    both sightings;
  - the projection `crossref_query_issns`, evidence `crossref_observations_by_issn`, provenance `query_issn` and the
    CSV column `crossref_query_issns` record which queries returned each DOI;
  - one DOI is still one subject;
  - differing content between ISSN queries is flagged `CROSSREF_ISSN_QUERIES_DISAGREE` with a FIELD_CONFLICT task.
- **Coverage:**
  - `crossref_issn_queries` table: plan, per-ISSN state, stop reason, count snapshots, pages, rows, observations,
    unique DOIs, replays;
  - rows-versus-union math (`crossref_rows_fetched_all_issn_queries`, `crossref_unique_dois`,
    `crossref_dois_by_issn_query_set`) and the semantics statement.
- **Tests:** new `tests/test_journal_multi_issn.py` (11 tests). The single-query mechanics tests now run on a one-ISSN
  journal with unchanged assertions; 3 assertions on the two-ISSN JISA-shaped crawl updated to per-ISSN counts
  (`REVIEW_FIXES.md`).

## 0.4.1.dev1 — S04J Journal DOI Harvesting (2026-11-01)

Stage inserted after accepted S04 v02 and before S05 (architect ADR-JH-001). Offline candidate; no live provider access.

- New package `scivetra_kb/journal/` (`docs/ADR_S04J.md`, `docs/JOURNAL_HARVESTING_GUIDE.md`):
  - ISSN validation;
  - evidence-only journal identity: Crossref journal records and OpenAlex Source IDs, matched by exact ISSN;
  - checkpointed Crossref `/journals/{ISSN}/works` cursor crawl;
  - independent OpenAlex `primary_location.source.id` crawl, plus an optional `locations.source.id` alternate-location
    crawl;
  - resume with cursor replay;
  - bounded retries through the accepted S02 `Executor`;
  - DOI-union reconciliation with S02 DOI semantics;
  - field-level provenance and conflicts, review tasks and non-destructive history;
  - integrity signals that are never erased;
  - versioned collections with explicit COMPLETE/PARTIAL/BLOCKED coverage;
  - deterministic CSV/JSONL export;
  - OA dry-run queue (rights-policy/1 + S04 URL guard; no downloads);
  - batch isolation.
- Migration `0004_journal_harvesting.sql` (additive, 14 append-only `jh_*` tables). `0001`-`0003` are byte-identical.
  S05 must use `0005`.
- `core/cas.py`: schema-4 CAS reference `jh_page.response_bytes_sha256`. `core/manifest.py`: `journal_manifest`.
  `core/backup.py`: journal domain summary. `core/doctor.py`: journal counts and `JOURNAL_INVARIANTS`.
- `metadata/transport.py`: additive keyword `allowed_hosts` for `validate_url` / `GuardedTransport`; the S02 default is
  unchanged.
- CLI: `rkb journal register | resolve | harvest | resume | status | reconcile | coverage | export | harvest-batch`
  (simulated transport only; `--live` refused).
- Synthetic scenarios: `tests/fixtures/journal_sim/`, generated by `tools/s04j_make_fixtures.py`. Tools:
  `s04j_cli_smoke.py`, `s04j_pilot.py`, `s04j_upgrade_rehearsal.py`, `s04j_line_coverage.py`.
- Tests: 8 new modules. 11 accepted tests adapted on schema literals only (`docs/S04J_TEST_ADAPTATIONS.md`).

## 0.4.0.dev2 — S04 v02 corrective revision (2026-11-01)

Baseline: S04 v01 (`SCIVETRA_S04_DOWNLOAD_BRIDGE_v01.zip`, `56eaf684…99b265ab`). Scope: architect findings S04-F01 and S04-F02 only. Migrations 0001, 0002 and 0003 byte-identical; no schema change, no new dependency, no network capability, no core (S02/S03) source change.

| File | Change | Finding | Proof tests |
|---|---|---|---|
| `bridge/policy.py` | New `numeric_host()`: a purely lexical rule (no DNS) that treats a host as an IPv4 address if, after one trailing dot, its last label is all digits or `0x`+hex, or any label is `0x`+hex (superset of the WHATWG "ends in a number" rule and of inet_aton). `url_refusal()` now returns `URL_NUMERIC_HOST_REFUSED` for these (`127.1`, `0177.0.0.1`, `0x7f.0.0.1`, `192.168.1`, `10.1`, `0x7f000001`, `2130706433`, `127.0.0.01`, …), `URL_HOST_CHARACTERS_REFUSED` for any host character outside `[a-z0-9._-]` (e.g. `%`-encoded hosts), `URL_IP_LITERAL_REFUSED` for every bracketed host (was `URL_AUTHORITY_REFUSED`), and extends `URL_LOCAL_HOST_REFUSED` to the reserved suffixes `localdomain, intranet, home, corp, private, home.arpa, arpa, invalid, test` (ADR-S04-11) | S04-F01 | S04-F01-POLICY, S04-URL-NUMERIC, S04-F01-EXPORT |
| `bridge/service.py` | `guard_path_text()` recognises drive-relative `C:x` **before** the host-specific core check, so the code is `DRIVE_RELATIVE_PATH_REFUSED` on every OS; it also refuses the protected production root (`E:\ResearchKB` and descendants, raw text and cwd-absolute form) lexically with `PROTECTED_DATA_ROOT` before any stat, for every bridge argument (previously `--result-csv E:\ResearchKB` was refused only after a `stat`) (ADR-S04-12) | S04-F02 | S04-PATH-ARGS, S04-F02-NOCREATE |
| `tests/test_bridge_export.py` | new `NumericHostTests` (2 tests) | S04-F01 | 282 tests |
| `tests/test_bridge_paths_backup.py` | `test_bridge_path_arguments_refused`: simulated-POSIX block extended (drive-relative → `DRIVE_RELATIVE_PATH_REFUSED`, absolute Windows → `WINDOWS_PATH_ON_POSIX`, protected root → `PROTECTED_DATA_ROOT`); new `test_refused_paths_create_nothing` | S04-F02 | 282 tests |
| `tools/required_ids.json` | +4 contract IDs (324) | both | conformance matrix |
| `docs/` | ADR-S04-11/12, open issues C19–C21, bridge guide URL codes, S04 test adaptation note | both | — |
| `__init__.py` | version `0.4.0.dev2` | — | — |

## 0.4.0.dev1 — S04 v01 Download Bridge (2026-11-01)

Baseline: accepted S03 v02 (`SCIVETRA_S03_GLOBAL_RESEARCH_LIBRARY_v02.zip`, `b52b4cc3…14993d817`). Migrations 0001 and 0002 byte-identical; new additive 0003. No new dependency, no network capability.

| Area | Files | Change |
|---|---|---|
| Schema | `core/migrations/0003_download_bridge.sql` (new) | 8 append-only bridge tables, immutability and enforcing triggers |
| Bridge | `bridge/` (new): `pbd.py`, `policy.py`, `sources.py`, `service.py` | profile pbd-csv-v1, export / import / status / close, reconciliation, path guard |
| Core | `core/cas.py`, `core/manifest.py`, `core/backup.py`, `core/doctor.py`, `core/upgrade.py` | bridge CAS references (schema 3), bridge domain manifest, doctor bridge invariants, upgrade checks every prior table and the S03 library manifest |
| CLI | `cli.py` | `rkb bridge export|import|status|close` |
| Tests | 6 new test modules, `tests/bridge_helpers.py`, `tests/bridge_pilot.py`, native Stage-11/11B fixture; 8 schema-literal adaptations (`docs/S04_TEST_ADAPTATIONS.md`) | 279 tests |
| Tools | `tools/rv_native/generate_native_fulltext_export.py`, `tools/pbd_contract_audit.py`, `tools/s04_pilot.py`, `tools/s04_upgrade_rehearsal.py`, `tools/s04_cli_smoke.py`; `required_ids.json` (+66), `documented_gaps.json` | evidence |
| Docs | `ADR_S04.md`, `BRIDGE_USER_GUIDE.md`, `S04_TEST_ADAPTATIONS.md`; CLI guide, open issues | |

## 0.3.0.dev2 — S03 v02 corrective revision (2026-11-01)

Baseline: S03 v01 (`SCIVETRA_S03_GLOBAL_RESEARCH_LIBRARY_v01.zip`, `477d0bbe…cd4f88`). Scope: architect findings S03-F01 and S03-F02 only. Migrations 0001 and 0002 are byte-identical; no schema change, no new dependency, no network capability.

| File | Change | Finding | Proof tests |
|---|---|---|---|
| `rights/policy.py` | `licence_family()` parses the complete URI (`urllib.parse.urlsplit`) and accepts only canonical unported CC licence URIs with a published family/version pair, CC0 1.0 and PDM 1.0 (allowlist `cc-uri-allowlist/2`, ADR-S03-10). Everything else is unrecognized licence evidence (L3). New flag `LICENCE_URI_NOT_RECOGNISED` for creativecommons.org-like URIs that fail; `evaluation.licence_uri_rules` names the rule revision | S03-F01 | S03-F01-CANONICAL, -NEAR-MISS, -SEMANTICS, -E2E, -REASSESS |
| `core/doctor.py` | `doctor --full` adds `rights_reassessment_due` / warning `RIGHTS_REASSESSMENT_DUE:n` for current PUBLIC_OA_LICENSED assessments the current URI rules no longer support (read-only; re-running `rkb rights assess` appends a superseding row) | S03-F01 | S03-F01-REASSESS |
| `core/cas.py` | Shared, schema-aware, read-only reference inventory `CAS_REFERENCE_COLUMNS` / `referenced_digests()` and `check_object_at()` / `reference_problems()` (missing, link/reparse object or shard, hash mismatch) | S03-F02 | S03-F02-INVENTORY |
| `core/doctor.py` | uses the shared inventory (same codes as before) | S03-F02 | S03-DOCTOR-01, S03-F02-INVENTORY |
| `core/backup.py` | `create_backup` refuses an unhealthy source before creating any folder (`BACKUP_SOURCE_INCOMPLETE`), re-derives references from the backup-API snapshot (`BACKUP_SNAPSHOT_DRIFT`) and checks every reference was copied; manifest records `cas.referenced_object_count`. `verify_backup` recomputes references from the backup's own DB and refuses missing/link/corrupt referenced objects (`BACKUP_CAS_REFERENCE_MISSING`) regardless of manifest/SHA256SUMS; reparse points inside a backup are refused. `restore_backup` re-checks references in the restored root (`RESTORE_MISMATCH`) and removes everything it wrote on any failure | S03-F02 | S03-F02-HEALTHY, -CREATE, -VERIFY, -RESTORE, -NON-PDF, -ORPHAN, -LINK, -SCHEMA1, -CLI |
| `tools/s03_cli_smoke.py` | adds fake-CC-URI assessment, broken-KB doctor and refused `rkb backup` steps | both | `evidence/cli_smoke.log` |
| `tests/test_s03_v02_fixes.py` (new), `tools/required_ids.json` | 15 tests, 15 new contract IDs | both | 247 tests |
| `__init__.py` | version `0.3.0.dev2` | — | — |

## 0.3.0.dev1 — S03 v01 Global Research Library (2026-11-01)

Baseline: accepted S02 v03 (`SCIVETRA_S02_METADATA_ENRICHMENT_v03.zip`, `eb78181a…7744d6`). Migration 0001 byte-identical; new additive migration 0002. No new dependencies; no network capability.

| Area | Files | Change |
|---|---|---|
| Schema | `core/migrations/0002_global_research_library.sql` (new) | 22 append-only tables, 2 projection views, triggers for human-anchored identity changes, PAL-1, DV-1 evidence sources, rights default-deny |
| Upgrade/backup | `core/upgrade.py`, `core/backup.py` (new); `core/migrations.py`, `core/db.py`, `core/cas.py`, `core/doctor.py`, `core/manifest.py` | explicit backup-first upgrade, backup/verify/restore, `doctor --full`, library + v2 semantic manifests |
| Library | `library/` (new): `linking.py`, `service.py`, `queries.py`, `common.py` | link-policy/1 L1-L6 + C1-C4, workspaces, PPRs, LinkCandidates/Decisions, IdentityConflicts/resolutions, WorkFamily proposals, review queue, search, reconcile, SOURCE_UNAVAILABLE |
| Untrusted export boundary | `metadata/rv_import.py`, `metadata/store.py` | strict JSON + duplicate keys + caps + shape + CSV consistency for every export read; optional `scope_semantic` |
| Assets | `assets/` (new): `pdf_child.py`, `sandbox.py`, `identity.py`, `service.py` | DocumentVersion, explicit synthetic intake G0-G4, sandboxed G1, G2 CAS, G3 identity-policy/1, PAL-1 |
| Rights | `rights/policy.py` (new) | rights-policy/1 classifier, A1-A10 gates, A9 handoff scan |
| CLI | `cli.py` | backup, verify-backup, restore, upgrade, reconcile, library *, asset *, rights *, doctor --full |
| Tests | 8 new test modules; 5 S02 tests adapted (`docs/S02_TEST_ADAPTATIONS.md`) | 232 tests |
| Tools | `tools/rv_native/generate_native_export.py`, `tools/s02_upgrade_rehearsal.py`, `tools/pypdf_crosscheck.py`, `tools/documented_gaps.json`; `build_conformance.py`, `required_ids.json` | fixture generation and evidence |

## 0.2.0.dev3 — S02 v03 (narrow corrective revision, 2026-11-01)

Baseline: S02 v02 (`SCIVETRA_S02_METADATA_ENRICHMENT_v02.zip`, `74fd1b04…4c565e3b`). Scope: architect findings S02-F04 and S02-F05 only. Migration 0001 is byte-identical. No new dependencies, no live network capability, and no change to DOI-space, retraction-omission or alert semantics.

| File | Change | Rationale | Proof tests | Risk |
|---|---|---|---|---|
| `src/scivetra_kb/metadata/strict_json.py` (new) | Strict decoder: decoder hooks flag NaN/Infinity tokens, non-finite floats and out-of-range integers; an iterative, depth-bounded walk refuses them and unpaired surrogates (values and keys) with a JSON-pointer path; RecursionError from the C decoder becomes `NESTING_TOO_DEEP`. Only named decoder exceptions are caught | F04: canonical JSON raised inside the persistence transaction, losing snapshot and coverage | `tests/test_unsafe_json.py`, STATIC-08 | A valid response with an integer above 2^63-1 or nesting deeper than 64 is refused (visible as CHECK_FAILED with reason) |
| `metadata/crossref.py`, `metadata/datacite.py` | Decode through `strict_decode`; refusal returns `REFUSED / <reason> / field_path` | F04 | S02-UNSAFE-* | none |
| `metadata/ra.py` | Same strict boundary; refusal adds `field_path` | F04 (doiRA) | S02-UNSAFE-RA | none |
| `metadata/cn.py` | Same strict boundary; an unsafe or unparseable CSL body is now a refused snapshot (v02: unparseable gave a `NOT_FOUND` snapshot). User status stays `NO_METADATA` | F04 (CN), prompt item A5 | S02-UNSAFE-CN | snapshot outcome for unparseable CSL changes (documented, A14) |
| `core/paths.py` | `WINDOWS_DRIVE_RE` is now `^[A-Za-z]:` (drive-relative forms included); public `refuse_unsafe_path_text()`; raw docstring (removes the `SyntaxWarning`) | F05 | S02-PATH-DRIVE-RELATIVE, S02-PATH-ENTRYPOINTS, S02-PATH-COLON-OK | `C:notes` refused on POSIX (B15) |
| `metadata/transport.py`, `metadata/rv_import.py` | Directory entrypoints call `refuse_unsafe_path_text()` before building any path | F05 (uniform entrypoints) | S02-PATH-ENTRYPOINTS | none |
| `tests/test_unsafe_json.py` (new), `tests/test_paths_portable.py`, `tests/test_static.py` (STATIC-08) | Regression tests | F04/F05 | themselves | none |
| `tools/required_ids.json` | +19 required IDs | conformance | `03_CONTRACT_CONFORMANCE_MATRIX.csv` | none |
| `src/scivetra_kb/__init__.py` | version 0.2.0.dev2 → 0.2.0.dev3 | provenance | — | none |

## 0.2.0.dev2 — S02 v02 (narrow corrective revision, 2026-11-01)

Baseline: S02 v01 (`SCIVETRA_S02_METADATA_ENRICHMENT_v01.zip`, `59554b54…05f2cf`). Scope is limited to the architect's review findings S02-F01/F02/F03. Migration 0001 is byte-identical (`2c07dd34…71f38b`). No new dependencies, no live network capability, and no change to DOI-space, retraction-omission or alert semantics.

| File | Change | Rationale | Proof tests | Risk |
|---|---|---|---|---|
| `src/scivetra_kb/metadata/shape.py` (new) | Trust-boundary type checks (`check`, `obj`, `arr`, `scalar`, `each`, `date_object`) raising `MalformedResponse(field_path)`; `bool` is never accepted as `int` | F01: untrusted nested JSON reached `.get`/iteration | `tests/test_malformed.py` (all) | Over-rejection of exotic but valid provider shapes; mitigated by positive/null fixtures (S02-MALFORMED-POSITIVE) |
| `metadata/crossref.py` | `validate_message()` covers every field read by `decompose`/`_update_events`/`parse_response`; `parse_response` returns `REFUSED / MALFORMED_FIELD_TYPE / field_path` | F01 | S02-MALFORMED-CR-*, -SERVICE, -NESTED | Same as above (e.g. `member` accepted as string or integer) |
| `metadata/datacite.py` | `validate_document()` covers root, `data`, `attributes` and every attribute read; same controlled result | F01 | S02-MALFORMED-DC-*, -ENVELOPE | Same as above |
| `metadata/cn.py` | CSL root must be an object; title/publisher/type must be strings; controlled `REFUSED / MALFORMED_FIELD_TYPE`. Service status remains `NO_METADATA` (documented) | F01 | S02-MALFORMED-CN-ROOT | none |
| `metadata/service.py` | Propagates `field_path` into the result and the `CHECK_FAILED` coverage `scope_note`; the CN result adds `snapshot_outcome` | F01 provenance | S02-MALFORMED-SERVICE | none (additive fields) |
| `core/paths.py` | Lexical protected-root check before host resolution (all OS); on non-Windows hosts drive-letter/backslash paths are refused (`WINDOWS_PATH_ON_POSIX`); patchable `host_is_windows()` | F02: on POSIX `E:\ResearchKB` resolved to a relative directory and was created | `tests/test_paths_portable.py`, CORE-PATH-02/03 | A POSIX filename that really contains a backslash is now refused (deliberately conservative) |
| `tests/netguard.py` | `child_command()`: guarded child via explicit bootstrap (`-I -B`), prints `GUARD_ACTIVE` before user code, exits 3 if the guard is missing | F03: child guard depended on platform `sitecustomize` behaviour | NET-GUARD-05, NET-GUARD-CHILD | none |
| `tests/test_00_network_guard.py` | NET-GUARD-05 rewritten: child must print `GUARD_ACTIVE`, `BLOCKED_DNS`, `BLOCKED_CONNECT`; new structural test of the bootstrap | F03 | itself | none |
| `tools/run_offline_tests.py` | Records a non-blocking diagnostic: does the `sitecustomize` layer reach a plain child on this platform | F03 diagnosis | evidence header/summary | none |
| `tests/test_malformed.py`, `tests/test_paths_portable.py` (new); `tests/test_static.py` (STATIC-07) | Regression tests | F01/F02 + narrow exception handling | themselves | none |
| `tools/required_ids.json` | +21 required IDs (S02-MALFORMED-*, S02-PATH-POSIX, NET-GUARD-05, NET-GUARD-CHILD) | conformance | `03_CONTRACT_CONFORMANCE_MATRIX.csv` | none |
| `src/scivetra_kb/__init__.py` | version 0.2.0.dev1 → 0.2.0.dev2 | provenance | — | none |

## 0.2.0.dev1 — S02 v01

Initial S02 candidate (see the S02 v01 package).
