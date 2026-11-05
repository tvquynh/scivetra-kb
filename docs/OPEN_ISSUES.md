# Open issues, interpretations and deviations (S02 + S03 + S04 + S04J + S05)

Each item states what was decided in code, why, and whether the architect needs to act. None of them changes accepted S01 v03 semantics. Where the contract was silent, the more conservative option was chosen.

## A. Contract interpretations (please confirm)

| # | Item | Decision in S02 | Architect action |
|---|---|---|---|
| A1 | `N01` meaning | S01 v03 `11` §2 defines N01 as "paper without DOI"; the S02 prompt says "N01 (unsafe input)". Both are implemented. MF-21 / `fetch_paper` covers no-DOI; the path, CAS, export and URL tests cover unsafe input. Both are tagged N01 | confirm |
| A2 | `RESPONSE_SUBJECT_MISMATCH` | The ProviderSnapshot `outcome` enum (03 §5.1) has no such value, so it is stored as `outcome=REFUSED_UNSAFE_RESPONSE`, `outcome_reason=RESPONSE_SUBJECT_MISMATCH`. Raw bytes are kept, no claims are created, and coverage is `CHECK_FAILED` | confirm |
| A3 | SQLite journal mode | S01 v03 `10` §1 says WAL. S02 uses `journal_mode=DELETE` so that read-only commands (`mode=ro`) never need to create `-wal`/`-shm` files. That honours "read commands never auto-create" (single-writer KB) | **ACCEPTED (S02 v01 review)** as an explicit S02-only trade-off; revisit under future concurrency/performance testing |
| A4 | Crossref update entry without `source` | Treated as `CROSSREF_PUBLISHER_UPDATE` with payload flag `source_missing=true` (INFERRED: pre-Retraction-Watch Crossmark deposits) | confirm |
| A5 | Coverage granularity | Crossref: one coverage row per retrieval covering all 7 event types. DataCite: two rows per retrieval (notice types `NOT_SUPPORTED_BY_PROVIDER`; versioning `NO_SIGNAL`/`SIGNAL`). If any earlier-sighted event is missing, the single row is `REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT` with all missing event IDs | confirm |
| A6 | Identical re-check | The same bytes and outcome reuse the snapshot and coverage row (semantic de-duplication). The re-check is logged in the operational `provider_check` journal and is excluded from the semantic manifest | confirm |
| A7 | mEDRA routing | Routed to doi.org content negotiation without `--allow-cn-probe`, per the 04 §3.2 table ("OTHER (mEDRA) → content negotiation"). Other OTHER/UNKNOWN RAs need the flag | confirm |
| A8 | Free-text DOI result | A single VALID free-text candidate fills `doi`/`comparison_key`, but `automatic_action_allowed` is always false for free text, so it is never used for lookup, link or assertion | confirm |
| A9 | `NEW_REPORT` review tasks | Not created automatically; the `OPEN_ALERT` set is itself the queue. Tasks are created automatically only for omission, possible opposing notices and DOI review cases | confirm |
| A10 | Omission flag scope | `REPORTING_SOURCE_LATEST_SNAPSHOT_OMITS_REPORT` is raised when the reporting provider's latest coverage for that family is omission, 404 or failure, per 03 §5.3.5 | none |
| A11 | DataCite version events | `IsPreviousVersionOf`/`IsObsoletedBy` → NEW_VERSION on this DOI. `IsNewVersionOf`/`Obsoletes` → NEW_VERSION on the related DOI. `IsVersionOf` → relation claim only | confirm |
| A12 | Operator trailing-space DOIs | AMBIGUOUS + review task, no automatic lookup (S01 v03 default). Formalised by MF-18.24/18.28, CLI-05 and N28 | **ACCEPTED (S02 v01 review)**: keep AMBIGUOUS; never trim silently |
| A13 | Malformed provider JSON (v02) | Every field a parser reads is type-checked first (`metadata/shape.py`). A mismatch gives snapshot `REFUSED_UNSAFE_RESPONSE` / `MALFORMED_FIELD_TYPE` with the raw bytes kept, and `CHECK_FAILED` coverage whose `scope_note.field_path` is a JSON pointer. No claims or events are written. Content negotiation reports user status `NO_METADATA` for the same snapshot outcome. JSON `null` counts as an absent optional field, and `bool` is never accepted as an integer | confirm the reason code and null handling |
| A14 | Unsafe provider scalars (v03) | All four provider parsers (Crossref, DataCite, doiRA, content negotiation) decode through `metadata/strict_json.py`. Stable reason codes: `NONSTANDARD_JSON_CONSTANT` (NaN/Infinity/-Infinity token), `NON_FINITE_NUMBER` (number overflowing to an infinite float, e.g. 1e9999), `INTEGER_OUT_OF_RANGE` (integer outside signed 64-bit, the SQLite INTEGER range; literals longer than 20 digits are refused unconverted), `UNPAIRED_SURROGATE` (string or object key with a lone surrogate; a bad key is reported at its containing object and never echoed), `NESTING_TOO_DEEP` (deeper than 64 levels, or the decoder's own recursion limit, then without a path), and the existing `UNPARSEABLE_RESPONSE`. Crossref/DataCite: snapshot `REFUSED_UNSAFE_RESPONSE`, raw bytes in CAS, `CHECK_FAILED` coverage with reason and path. doiRA: refused snapshot, no RA claim, no routing. Content negotiation: refused snapshot, user status `NO_METADATA`; an unparseable CSL body is now also a refused snapshot (v02: `NOT_FOUND` snapshot) | confirm the reason codes, the 64-bit integer bound and the CN change |

## B. Known limitations (not defects)

| # | Limitation | Impact / follow-up |
|---|---|---|
| B1 | No live transport is implemented (not authorized). The URL policy validates scheme/host/port/user-info/literal IPs and redirect targets. DNS-time private-address checks need a real transport | Implement with live access (OAG-3) as a separately reviewed change |
| B2 | Provider field shapes are taken from the S01 documentation notes and remain **UNVERIFIED** where marked there: exact doiRA RA strings beyond DataCite/Public/EIDR, Crossref `Retry-After` on 429, `x-rate-limit-type`, DataCite 404 body, DataCite withdrawal/tombstone, Crossref timestamp units. Offline fixtures encode expected behaviour only; they are **not** evidence of live API behaviour | Live smoke after OAG-3 |
| B3 | (S02) RV import was tested only with synthetic exports | **Closed in S03**: native Reprovane 2.0.1 exports generated from Reprovane's own synthetic fixtures (`tests/fixtures/rv_native_2_0_1/`, see C3) |
| B4 | An OpenAlex event's subject is the DOI only when the RV item has exactly one VALID DOI; otherwise it is the OpenAlex Work ID. Cross-provider corroboration (Crossref vs OpenAlex) therefore works only for single-DOI items | S03 identity linking |
| B5 | Alert ordering uses the monotonic ID sequence (ULID time + counter). It is correct for a single writer; clock skew between machines is out of scope | none (single-writer design) |
| B6 | Operator documents are limited to UTF-8 text (pages split by form feed). PDF notices need S03/S06 assets and parsing | S03/S06 |
| B7 | Integrity entries are not metadata claims, so their removal appears as coverage (`REPORT_NO_LONGER_PRESENT…`), not as a MetadataChangeEvent | by design |
| B8 | Editable install offline needs setuptools ≥ 70.1 (built-in `bdist_wheel`) or the `wheel` package; a fresh Python 3.11 venv ships setuptools 65.5. The smoke test built a wheel with the host's setuptools 81 used read-only. See the install guide | none |
| B9 | Coverage measurement (line/branch) was not run, because no coverage tool is installed and nothing was downloaded | optional |
| B10 | PaperBatchDownloader tests are still NOT RUN (no .NET SDK); not in S02 scope | S04 |
| B11 | **Linux was not run by Claude** (v02 and v03). The architect ran v02 on Linux/Python 3.13.5: 149/149 PASS. The implementation host has no WSL, Docker or Linux machine. The POSIX path branch is tested on Windows by patching `paths.host_is_windows` (S02-PATH-POSIX). The child-guard fix no longer depends on platform `site` behaviour (explicit `-I -B` bootstrap that prints `GUARD_ACTIVE` before any network attempt). v03 Linux results therefore remain **NOT VERIFIED** until the architect reruns the suite on Linux | architect rerun of v03 on Linux (F05 real-POSIX tests and the repro script's POSIX branch) |
| B12 | F03 root cause: the architect's v02 Linux run reported `sitecustomize_child_guard_diagnostic=False` with all child-guard tests passing, which confirms the symptom (`sitecustomize` does not reach a plain child there) and that the explicit bootstrap is what protects children. Why the site layer does not reach it on that host is still INFERRED. Original analysis: on Windows (Python 3.11.9 and 3.14.4) the runner's `sitecustomize` diagnostic reports `True`. A likely cause on Linux is that the child's site initialisation did not import the runner's `sitecustomize` (for example a distribution-provided `sitecustomize`, or interpreter flags/environment in the reviewer's harness). The enforced guard no longer relies on it. The diagnostic field `sitecustomize_child_guard_diagnostic` in `evidence/test_results.json` shows the answer on any platform | closed for S02 (symptom confirmed, guard independent of it) |
| B13 | The Reprovane portable importer (`rv_import.py`) still trusts the *shape* of `corpus.jsonl` items after the hash/inventory checks. A self-consistent but malformed export would raise an error instead of a controlled refusal. It was left unchanged because v02 is restricted to F01's provider parsers | **Closed in S03**: hardened export reader (strict JSON, duplicate keys, caps, shape, CSV consistency; S03-RV-UNSAFE-*) |
| B14 | On POSIX, any data-root or input path containing a backslash is refused (`WINDOWS_PATH_ON_POSIX`), including legal POSIX filenames that contain `\` | deliberately conservative; revisit if needed |
| B15 | On POSIX every path starting with a drive prefix (`^[A-Za-z]:`) is refused, including a legal relative POSIX name such as `C:notes`. Use `./C:notes` if such a name is really wanted (S02-PATH-COLON-OK) | deliberately conservative (S02-F05) |
| B16 | The F05 tests that call real `init_kb` with drive-relative text, and the colon-name positive case, run only on a real POSIX host. On Windows these cases are lexical-only, under a guard that fails the test if any filesystem call is reached (never `init_kb('E:ResearchKB')` on Windows) | architect Linux rerun |
| B17 | `strict_json` covers provider response bodies. Fixture-manifest headers (`transport.json`, operator-supplied) and Reprovane exports still use the standard decoder; live HTTP headers are Latin-1 by construction and cannot hold surrogates | Reprovane exports: **closed in S03** (strict reader). `transport.json` fixture manifests unchanged (development transport only) |

## C. Things deliberately not done

- No live calls to Crossref, DataCite, doi.org, OpenAlex, PyPI or GitHub.
- No changes to Reprovane, PaperBatchDownloader or `tvquynh/scivetra`.
- No access to `E:\ResearchKB` (refused by code: `PROTECTED_DATA_ROOT`).
- (S02) No PDFs, AI, Download Bridge or S03 work.

## S03 (0.3.0.dev1, revised in 0.3.0.dev2): contract interpretations, limitations and pending decisions

Decisions and field-level deviations are in `docs/ADR_S03.md` (ADR-S03-01…09, D1…D16). Items needing the architect or owner:

| # | Item | State | Action |
|---|---|---|---|
| C1 | Reversible merge/split (GP-1) not implemented; `SAME_MANIFESTATION` decisions are recorded only (ADR-S03-04) | NOT_IMPLEMENTED_PENDING | specify re-pointing semantics |
| C2 | IdentityResolutionEvent action vocabulary (ADR-S03-05) and the C1 kind source (D8) are interpretations | PROPOSED | confirm |
| C3 | Native fixture coverage: Reprovane 2.0.1 keeps two records of one OpenAlex work with different DOIs as *unresolved* discovery evidence (they never reach a release without a human SAME decision). The native fixture therefore holds single-DOI, no-DOI and OpenAlex-retraction items and a later release, but no multi-DOI or shared-OpenAlex item. Those cases (N02, N27, N27b, N27d, N27e) use KB-built synthetic exports in the native item shape, kept separate (`tests/s03_helpers.write_export`) | documented | optional: a native fixture via a Reprovane human SAME resolution decision |
| C4 | The G1 stdlib validator refuses xref/object streams and non-Flate filters (ADR-S03-07). Fine for synthetic fixtures; real PDFs need the pypdf sandbox as the primary path. A pypdf 6.21.4 cross-check was EXECUTED from an offline, RECORD-verified copy (`evidence/pypdf_crosscheck.json`); it is not part of the required suite | limitation | decide before S04/S06 real intake |
| C5 | Memory cap: Windows job object EXECUTED (self-test `LIMITED`); POSIX `RLIMIT_AS` branch **NOT RUN** (no Linux host) | NOT_RUN | architect Linux rerun |
| C6 | **Linux NOT RUN** for S03 (no WSL/Docker/Linux host). POSIX-only branches: symlinks in tests (Windows uses junctions), `RLIMIT_AS`, `fcntl` lock | NOT_RUN | architect Linux rerun |
| C7 | S02 `metadata import-reprovane` is kept (ADR-S03-06). Using it and the library import on one workspace creates duplicate papers, which the S03 linker and doctor flag | risk | decide retirement |
| C8 | Title-similarity candidate search is linear in the number of papers with references, per item | scale limit | index later if needed |
| C9 | identity-policy/1 uses only text the stdlib validator extracts (unencoded/Flate content streams, Tj/TJ strings). Scanned or font-encoded PDFs give `SCANNED_NO_TEXT` (AMBIGUOUS) | conservative | S06 parsing |
| C10 | `rkb restore` restores a schema-1 backup as schema 1; it must then be upgraded explicitly | by design | none |
| C12 | (v02) CC jurisdiction-ported licence URIs (e.g. `/licenses/by/3.0/us/`) are **not** recognized as open licences (ADR-S03-10): they classify as unrecognized evidence, i.e. at most PUBLIC_OA_UNVERIFIED | conservative | architect: supply a reviewed port list if wanted |
| C13 | (v02) The stored policy id remains `rights-policy/1` (CHECK in migration 0002); the URI-rule revision `cc-uri-allowlist/2` is reported in each evaluation and the change is documented here and in ADR-S03-10. v01-era LICENSED assessments are not rewritten; `doctor --full` flags them (`RIGHTS_REASSESSMENT_DUE`) | documented | a future migration may admit `rights-policy/2` |
| C14 | (v02) Backup completeness checks need the writer lock (taken by `rkb backup`); a process writing without the lock is outside the model. Restore re-verifies after copying, closing the verify-then-copy window | by design | none |
| C15 | (S04) PBD compatibility is pinned to V5.2.11 @ 2901b046 (CsvUtils blob 3d4e607f); the architect's orientation names blob dfda3180 on a newer main. Any newer PBD must be audited and given a new profile; until then its files are HEADER_MISMATCH / UNKNOWN_STATUS | pinned | architect/owner |
| C16 | (S04) No .NET SDK on the host: PBD tests NOT_RUN; replaced by a static source audit (22 checks) and Python ports of ReadJobs/WriteResults. The real PBD application was not run end to end | NOT_RUN | run the PBD harness where .NET exists |
| C17 | (S04) G1 production blocker: the stdlib validator fails closed on xref/object-stream PDFs, so most real downloads would be REJECTED_FILE (PARSER_REJECTED). The isolated pypdf stage stays optional (second opinion) and was not made primary | blocker for real intake | decide the primary validator before real data (OAG-2) |
| C18 | (S04) Bridge files never auto-match identity (require_human_identity). Review load = every verified file (41 links in the 100-item pilot) | by design | consider a reviewed auto-match policy later |
| C19 | (S04 v02) **Residual URL risk, not solved by the bridge.** The export filter is static and lexical (ADR-S04-11). A DNS name that is safe at export time can later resolve to a private or loopback address (DNS rebinding), and an exported public URL can HTTP-redirect, or its page can script WebView2, to an internal target. PBD/WebView2 makes those requests, not SciVetra; the bridge cannot see or block them. Exporting only safe candidates is therefore **not** equivalent to controlling PBD's later requests | residual risk | any enforcement in PBD or the browser needs a separate, owner-approved change |
| C20 | (S04 v02, architect advisory) A PBD DOI echo that differs from the frozen exported DOI is recorded (`bridge_attempt.observed_json.doi_echo_matches_export=false`) and identity stays `UNVERIFIED`, but no separate bridge review task is opened for that discrepancy | advisory, not changed in v02 | surface it to the reviewer before S05 eligibility work |
| C21 | (S04 v02) The bridge path guard now refuses the protected root lexically for every bridge argument. The S02/S03 core `safe_input_file` still stats an input path before its own checks (unchanged accepted behaviour; bridge callers never reach it with the protected root) | documented | consider the same lexical check in core in a later reviewed stage |
| C11 | S02 integrity behaviours (N06/N06b/N06c) are unchanged and re-verified by the S02 tests; S03 adds no integrity logic | none | none |

## S04J (0.4.1.dev1): Journal DOI Harvesting — limitations and pending decisions

| # | Item | Status / follow-up |
|---|---|---|
| D1 | **No live provider was called.** Every provider behaviour is exercised against `journal/simulator.py`, which encodes the documented endpoints, termination rules and the handoff's description of OpenAlex key requirements. Live response shapes (Crossref expired-cursor error, OpenAlex 401 body, `from_updated_date` access level, rate-limit headers) are **UNVERIFIED** | owner-authorized controlled live pilot (JISA) with budgets and key handling |
| D2 | A live transport is not implemented (`LiveTransportNotAuthorized`). The future transport must reveal `Secret` values only at send time (`mailto` for the Crossref polite pool, `api_key` for OpenAlex) and must never log them; the KB already refuses identities containing them | with D1 |
| D3 | Cursor recovery replays the whole recorded query from `cursor=*` (bounded by `max_replays`). Time-window partitioning of a replay (e.g. by `from-index-date`) is not implemented; a very large journal with cursors expiring often could exhaust replays and end PARTIAL (never silently incomplete) | add partitioned replay before crawling journals far above ~10k works |
| D4 | Reconciliation is in memory. Measured on the 2,226 + 2,210 + 2,212-record JISA-shaped pilot: ~290 MB peak Python memory (tracemalloc), ~12 s without tracing (Windows, Python 3.11) | stream per DOI before journals of 20k+ works |
| D5 | Title conflict detection uses whitespace-collapsed, casefolded text (`_norm_title`). It is used only to *flag* a conflict, never for identity. Encoded titles (MathML, HTML entities) may give extra conflict tasks | tune after the live pilot |
| D6 | The OA dry-run queue is not connected to the S04 bridge: `0003` pins `bridge_batch.source_kind` to Reprovane sources. A journal source kind needs a later migration and owner approval | decision for a later stage (with OAG-4) |
| D7 | Journal subjects are not minted as GlobalPapers. Existing papers with the same DOI are listed read-only (`paper_candidates`). Promoting a journal subject into the library or a project is left to S05's explicit import policy | S05 handoff (`docs/S04J_S05_IMPACT.md`) |
| D8 | Linux was not run; Python 3.11.9 and 3.14.4 on Windows 11 only (as in S04 v02) | architect Linux rerun |
| D9 | OpenAlex `type_crossref` is not stored as a separate claim; the OpenAlex `type` is compared with the Crossref type via `jh-type-compat/1` | revisit with live data |
| D10 | (v02) Sibling ISSNs listed by a resolved Crossref journal record are crawled even if not requested (ADR JH-I7). A provider record that lists an unrelated ISSN would therefore add a query; its works still enter only through that ISSN's Crossref journal endpoint and stay traceable via `crossref_query_issns` | confirm interpretation |
| D11 | (v02) A query branch that failed earlier in a run that later crashed stays failed on resume (not retried); start a new run to retry it. Budget exhaustion is recorded as a branch failure | confirm |
| D12 | (v02) Two ISSN queries double the Crossref requests and observations for journals whose ISSNs share one inventory (JISA-shaped pilot: 4,456 rows / 4,454 observations for 2,228 DOIs). Measured impact in `02_TEST_REPORT.md` | acceptable; revisit for very large journals with D4 |
| D13 | (v02) A malformed Crossref record returned by two ISSN queries gives one MALFORMED_PROVIDER_RECORD review task per observation (2 tasks for one record), because the task context names the observation | cosmetic; group per record if reviewers prefer |

## S05 (0.5.0.dev1, revised in 0.5.0.dev2): Full-text Eligibility candidate workflow — limitations and pending decisions

| # | Item | Status / follow-up |
|---|---|---|
| E1 | **Authority.** S05 is NON_AUTHORITATIVE staging. No RV-FTE API, no Reprovane change, no sealed corpus, no official PRISMA counts (OAG-5 CLOSED). `docs/RV_FTE_FUTURE_HANDOFF_CONTRACT.md` is a proposal only | owner/architect decision before any Reprovane-side adapter |
| E2 | **Reviewer identity is self-declared.** Roster labels are audit labels, not authentication or e-signatures; the human-only rule is enforced by `actor_type = HUMAN` and a refusal of AI/system/bot-looking labels, which cannot prove that a human typed a decision | organisational control; real authentication would need a separate design |
| E3 | **SEALED status is inferred, not read.** The S03-imported portable export carries no explicit `sealed` flag. S05 relies on (a) Reprovane 2.0.1 writing a portable export only for a SEALED release (`corpus_release/store.py:153`), (b) the operator-supplied output of Reprovane's own `verify-export`, (c) byte equality with the S03 import and (d) the Stage-10 binding fields in the manifest. A forged export plus forged verify JSON from the operator is not detectable offline | confirm interpretation; a signed Reprovane attestation would close it (OAG-5) |
| E4 | **D7 (S04J) superseded.** S04J D7 and `docs/S04J_S05_IMPACT.md` mention a future "explicit import policy" for journal subjects. ADR-05-01 prohibits journal → FTE; S05 refuses journal ids (`JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE`) and implements no journal → TA importer | separate, owner-approved journal → TA screening interface |
| E5 | **C20 surfaced, not resolved.** A PBD DOI echo that differs from the exported DOI now appears as queue flag `DOI_ECHO_MISMATCH` (and the asset stays unusable as evidence until a human confirms identity). Nothing resolves it automatically | closes the C20 advisory's "surface it before S05" request; resolution remains human |
| E6 | **External Stage-11B references are never evidence-ready (v02, S05-F01 fixed).** v01 accepted a caller-asserted `export_verified=true` with arbitrary digests. v02 verifies the actual read-only export against a trusted digest and binds release, item and selected artifact (ADR FTE-D18), but keeps the reference non-ready: no human identity confirmation, no KB bytes, no rights determination. Trust assumptions: the expected manifest digest comes from a trusted channel; the policy digest is checked only through the optional native `verify-export` output; no publisher signature | the ready path is a human-confirmed KB asset with the same bytes; a future authenticated Reprovane attestation plus human identity review would be needed to change this |
| E7 | **Rights at export.** Exports never contain PDF bytes, notes or excerpts, so no rights grant is needed for them; evidence rights are checked at attach and re-checked at queue/lock time (a later reassessment makes evidence CHANGED). There is no "view PDF" command in S05 | add a rights-gated viewer only if requested |
| E8 | **Studies vs reports.** A report counts as a separate study unless a human links it with `DUPLICATE_REPORT_OF_INCLUDED_STUDY` + `related_corpus_item_id`. No automatic study clustering | by design |
| E9 | **Assignment** is deterministic round-robin of 2 reviewers per candidate, skipping declared conflicts; it is frozen with the run. Re-assignment (e.g. a reviewer leaves) needs a new run | add an audited re-assignment event if needed |
| E10 | **Manual Download Inbox** is a requirements page only (`docs/MANUAL_INBOX_CONTRACT.md`); no watcher, downloader or inbox command exists. Manual downloads still enter through S03 intake + human link review | S04 acquisition addendum (owner-gated, before the first real full-text pilot) |
| E11 | **Linux**: v01 suite verified 379/379 by the architect on Linux (Python 3.13.5). v02 was run on Windows 11 (Python 3.11.9 and 3.14) only; Linux NOT_RUN for v02 | architect Linux rerun |
| E13 | **Evidence currency (v02, S05-F02 fixed).** Identity, role, paper and rights changes now invalidate votes immediately on unlocked runs (ADR FTE-D17), database rows only. Changed CAS bytes are still detected only at attach and at the lock gate (full re-hash), by design, so a routine summary never reads multi-GB PDFs | by design; `doctor --full` also re-hashes CAS |
| E14 | **Interface change (v02).** `attach_external` / `rkb fte attach-evidence` take the export folder and trusted digest (`--rv11b-export-dir`, `--rv11b-expected-manifest-sha256`, optional `--rv11b-verify-json`, `--rv11b-expected-artifact-sha256`) instead of `--rv11b-artifact-file`. KB-asset evidence fingerprints now include the identity review event and the paper, so fingerprints differ from v01 (v01 is not accepted; no stored v01 data is expected) | documented input change |
| E15 | **Locked runs and later drift.** A locked staging snapshot is historical and immutable; identity/rights changes after the lock are reported as `post_lock_drift` / `current_evidence_drift`, never folded into the locked manifest. Further work needs a new run | by design |
| E12 | Carried forward unchanged: C17 (stdlib PDF validator rejects xref/object-stream PDFs — blocker for real intake), C19 (PBD redirect/DNS residual risk), S04J D1/D2 (no live provider, no live transport) and D10–D13. OAG-4 (bridge/PBD live) and OAG-5 (Reprovane authority) stay CLOSED | unchanged |

## S06 (0.6.0.dev1, revised in 0.6.0.dev2): Local PDF parsing — limitations and pending decisions

- **P1 (R22, HIGH for production).** Windows: no OS-level network isolation; only the language-level audit hook. Parsing refuses
  `OS_NETWORK_ISOLATION_UNAVAILABLE` unless the operator accepts the guard. Real untrusted PDFs on Windows stay gated (AppContainer,
  firewall rule or VM needed).
- **P2.** Linux `unshare(CLONE_NEWUSER|CLONE_NEWNET)` is implemented but NOT_TESTED here; the whole S06 suite is NOT_RUN on Linux.
- **P3 (resolved in 0.6.0.dev2, S06-F01).** Parser identity, integrity and provenance are separate checks; the trust anchor
  is the architect-recorded PyPI SHA-256 of the official wheel and the fingerprint of its files (`parse/approved_parsers.py`).
- **P9 (0.6.0.dev2, S06-F02).** Text exports are an append-only AUTHORIZED → COMPLETED/FAILED state machine; an authorization
  without a terminal event is RECOVERY_REQUIRED until the next identical export reconciles it.
- **P10 (0.6.0.dev2).** POSIX root: RLIMIT_NPROC does not confine root; parsing and parser-backed intake refuse
  `PARSER_PRIVILEGED_ACCOUNT_REFUSED` unless explicitly accepted for synthetic input. Not exercised on Linux here.
- **P4.** Reading order is content-stream order; tables, columns, formulas, footnotes and font problems are heuristic flags only.
  No bounding boxes. No OCR (`docs/OCR_ADAPTER_CONTRACT.md`).
- **P5.** C17 is resolved for files that use xref streams / object streams; any other legacy structural rejection still stands
  (deliberately conservative). Encrypted PDFs are always refused.
- **P6.** Rights revocation withholds text on read/export; derived artifacts stay in CAS for audit (not deleted).
- **P7.** Locked S05 snapshots hold no parse text; S07+ must call the same current-access check.
- **P8.** Manual full-text inbox: not implemented (owner-gated addendum); it will reuse `rkb-pdf-structure/2` and the parser child.

## S07 (0.7.0.dev1): AI-assisted screening proposals — limitations and pending decisions

- **Q1.** The only model is a deterministic mock; its evaluation numbers describe synthetic fixtures only. A real local model needs
  a separate approval, pinning (weights, runtime, licence) and evaluation; REMOTE_API / DESKTOP_APP_MANUAL need OAG-6.
- **Q2.** Reviewer labels in `ai_reviewer` are owner-registered, self-declared labels, not authenticated identities.
- **Q3.** Blind-first is enforced inside the KB (reveal audit); a human can still see a suggestion elsewhere (exported files are
  metadata-only to limit this).
- **Q4.** Abstracts are copyrighted metadata (`COPYRIGHTED_ABSTRACT`); they enter packs only with `METADATA_AND_ABSTRACT` and stay local.
- **Q5.** Staleness is derived at read time by rebuilding the pack; very large scopes make `state` slower (bounded by pack limits).
- **Q6.** No authoritative link to Reprovane decisions: `linked_authoritative_decision` is an operator-supplied reference only.
- **Q7 (0.7.0.dev2).** Verification proves quotes, numbers, anchor types and label coherence; it does NOT prove logical or
  semantic entailment (a correctly quoted passage can still be misread). Every proposal stays HUMAN_REQUIRED.

## S08 (0.8.0.dev3): Scientific evidence extraction — limitations and pending decisions

- **E1.** The only extractor is a deterministic mock that recognises the fixed sentence forms of the synthetic fixtures; its pilot
  numbers are not evidence about any real model. Real local models (S10) need separate approval, pinning and evaluation.
- **E2.** Structural verification is not semantic entailment: a value literally present in the quote can still be attached to the
  wrong condition (pilot `pair_swap`, superseded erratum values). Every draft stays HUMAN_REQUIRED.
- **E3.** Only S06 text-layer evidence exists. Table cells, figure geometry, OCR and HUMAN_READING evidence are refused/DEFERRED.
- **E4.** Reviewer labels are self-declared, owner-registered labels (S07 roster), not authenticated identities.
- **E5.** Accepted evidence of FT_INCLUDE members is KB staging evidence: S05 itself is NON_AUTHORITATIVE and no Reprovane
  RV-FTE release exists yet, so no evidence is an authoritative corpus. PROVISIONAL members can never be accepted.
- **E6.** Current state is derived by re-rendering the input pack; large scopes make `state`, `summary` and export slower.
- **E7.** Exports never carry quotes or verbatim text fields (A7 is never ALLOW in rights-policy/1).
- **E8 (v02).** Under gate A3 denial the S06 artifact and the evidence input pack (CAS) still contain the document text: they are
  governed by A2 (local parsing, accepted in S06), not by A3. A3 governs excerpts recorded as evidence (quotes, text fields).
- **E9 (v02).** A crash between the atomic rename and the PUBLISHED event leaves a visible report whose committed reservation is
  UNCONFIRMED until the next identical export; there is no background repair. `rkb evidence exports` shows it.
- **E10 (v02).** A withheld text value is re-checked by hash only at its exact position; values that match only after
  normalisation are NOT_ASSESSABLE under A3 denial (they cannot be accepted anyway, because nothing can be shown).
- **E11 (v03).** Copied-excerpt detection in human text (`rkb-excerpt-detect/1`) is word-window matching against the pinned S06
  text: paraphrases, translations, reordered or heavily edited copies and fragments under four words are not detected;
  six-word phrases that also occur in the source are refused even when written independently (fail closed).
- **E12 (v03).** Human text stored while A4 allowed it stays in the append-only rows; after revocation it is withheld on every
  read path but remains in the database file and its backups (like machine quotes stored under A3).

## S09 (0.9.0.dev2; v01 0.9.0.dev1): Local search and RAG context — limitations and pending decisions

- **R1.** No real embedding model was exercised: the semantic model is `NOT_CONFIGURED`. `HYBRID_MOCK` results come from a
  deterministic word / trigram hashing mock and say nothing about semantic retrieval quality. A real local model needs owner-
  supplied weights with SHA-256, licence review and its own evaluation.
- **R2.** The benchmark corpus and questions are synthetic and generated; recall@10 / MRR describe this fixture, not real papers.
- **R3.** Contentless FTS5 rows cannot be re-hashed; a forged FTS row can only change ranking, never content or rights (every hit is
  re-verified against CAS and current rights), and `index verify` re-checks every chunk anchor.
- **R4.** An index built while text was allowed still holds its tokens until it is rebuilt; query-time checks withhold them at
  once, `index status` counts stale entries, and a rebuild removes them. Deleted index files may remain on disk sectors.
- **R5.** On Windows, publishing a new index fails with `INDEX_PUBLISH_FAILED` while another process holds the old file open; the
  old index stays published.
- **R6.** S06 has no section structure, so `section_path` is null; chunks are S06 text blocks (≤ 1000 characters).
- **R7.** Retrieval of authoritative evidence for synthesis remains gated (S11); S09 evidence search returns non-authoritative
  staging evidence only.
- **R8.** (v02) Every public call re-hashes the source PDF of each member it touches. This is the price of failing closed on a
  removed or corrupted source (S09-F03); on large real PDFs it adds I/O per call. A cheaper size / mtime pre-check was not adopted
  because it cannot detect same-size corruption.
- **R9.** (v02) Authorization is a snapshot of ONE public call: a change committed by another process while a call is running is
  seen by the next call, not halfway through the current one. Within a call the result set is consistent.

## S10 (0.10.0.dev2; v01 0.10.0.dev1): Desktop and AI integration — limitations and pending decisions

- **R-S10-01.** No Claude Desktop app, ChatGPT app, account, tunnel, remote MCP or AI API was used. Every client capability is
  `NOT_TESTED`, `BLOCKED` or disabled exactly as listed in `S10_CAPABILITY_MATRIX.md`.
- **R-S10-02.** The MCP server is a standard-library implementation of the base protocol and the tools subset. It was tested against
  an independent offline client only; interop with the official MCP SDK is BLOCKED (not installed, no network install authorized).
- **R-S10-03.** Owner records carry no cryptographic signature (`LOCAL_OPERATOR_ASSERTED`); anyone with write access to the KB
  file and the CLI can act as a registered owner. Tampering by direct SQL is detected (semantic SHA-256 re-check) but not prevented.
- **R-S10-04.** The caller is not attested (`INSTALLATION_PROFILE_UNATTESTED`): whoever launches `rkb ai mcp-serve` with a profile id
  is that profile. The client's self-declared name is recorded, never trusted.
- **R-S10-05.** Content shown to a desktop chat app is assumed to reach a vendor-hosted model (`EXTERNAL_UNVERIFIED`); a verified
  local-inference path does not exist.
- **R-S10-06.** Audit before release: if writing the response to stdout fails after the audit commit, the audit over-reports a
  release the client did not receive (never under-reports).
- **R-S10-07.** CAS files are outside the SQLite transaction: a file changed after its re-hash in a request affects the next
  request, not the current one.
- **R-S10-08.** Exchange bundles: POSIX file modes only (Windows ACLs are not tightened); secure deletion is best effort (SSD /
  journaling file systems may keep copies); the privacy scan is pattern-based.
- **R-S10-09.** Requests are serialized by `BEGIN IMMEDIATE`; a long CLI write delays gateway requests up to the busy timeout (5 s),
  after which they fail closed (ERROR, nothing released).
- **R-S10-10.** Per-request source re-hashing (inherited from S09-F03) costs I/O on every content request; real large-corpus latency
  is unmeasured.
- **R-S10-11.** `submit_proposal` (S01 09 §4) is not implemented: the S10 handoff requires a read-only tool surface.
- **R-S10-13 (v02, proposal for architect review, NOT implemented).** S10-F01 is fixed by denying every class without current A8
  (A7). Metadata-only records (Reprovane / Crossref, no PDF assessment) are therefore never shared with an AI client. If bibliographic
  facts should be shareable, a separate policy would be needed: a provenance-based bibliographic-rights record per source (e.g. the
  provider's metadata licence), its own owner determination and its own content class (e.g. `BIBLIOGRAPHIC_FACTS`), versioned into the
  consumer policy hash. Until the architect approves such a design, there is no exemption.
- **R-S10-14 (v02).** A current member with A5 DENY cannot be produced by the synthetic pipeline (a store_locally DENY asset is not
  indexed; a later reassessment makes the member's FTE evidence stale). The anchor-only path is therefore tested with A5 DENY injected
  at the rights gate (S10-SC-69).
- **R-S10-15 (v02).** Exclusion counts (`excluded_by_reason`) still tell an approved consumer how many matching items were withheld
  and why, without any identifier; this was accepted S09 behaviour and is kept.
- **R-S10-12.** Vendor client features (Claude Desktop local MCP, ChatGPT custom MCP and plugins) change over time; v01 records dated
  references (`S10_VENDOR_REFERENCES.md`) and exact NOT_TESTED / BLOCKED / disabled states. Re-check before any activation.

## LLM integration (ADR_LLM) — open decisions

- **R-LLM-01.** Free tiers change models, limits and data terms often; `data_policy` is the owner's reading and must be re-checked.
- **R-LLM-02.** No remote provider has been called (stage L3); remote model quality is unmeasured.
- **R-LLM-03.** The router's attempt log is a file outside the KB; per-answer provenance is in S08's `ev_extraction_run` (ADR_LLM LL-D14), so no migration 0012 is planned.
- **R-LLM-05.** Real local models were run on SYNTHETIC papers only (`tools/llm_local_model_check.py`, ADR_LLM LL-D16): qwen2.5:7b-instruct on CPU reached precision 0.8 / recall 0.5 on the malware paper and proposed nothing usable on the other two; the injected value was never proposed. Quality on real papers is unmeasured (needs OAG-1/2).
- **R-LLM-04.** Several accounts at one provider are not supported by design (terms of service).

## 1.0.1 — review follow-ups (2026-11-05)

- **R-101-01.** 1.0.0 on GitHub is broken (S08 package missing). Its release text must point users to 1.0.1; the tag is not rewritten.
- **R-101-02.** CI (`.github/workflows/ci.yml`) has not run yet; Linux / macOS remain NOT_RUN until it does.
- **R-101-03.** AI evaluation is 3 synthetic papers; a versioned benchmark with independent gold labels is not built (P1-04). REAL_CORPUS_NOT_EVALUATED.
- **R-101-04.** `--consensus` requires whole-answer agreement; record-level agreement with escalation to a human is a proposal, not implemented (P1-04).
- **R-101-06.** macOS: a data root below a symlink (e.g. `/tmp`, `/var/...`) is refused with `REPARSE_POINT` by design; use a folder in your home directory (`~/SciVetra`).
- **R-101-07.** macOS: PDF intake (S03 G1) and parsing (S06) are NOT_SUPPORTED. In the first CI run every PDF child failed on macOS (most likely the POSIX `RLIMIT_AS` memory cap the sandbox requires), so every PDF is REJECTED (`PARSER_REJECTED`) — safe, but unusable. The sandbox is not weakened to make it pass; a macOS implementation needs its own verified memory cap. Linux and Windows are tested in CI.
- **R-101-08.** Windows CI, Python 3.14: `test_parse_time_resource_limits_fail_safely` [80 MiB] failed once and passed in the other run (runner memory behaviour); watched, not changed.
- **R-101-05.** Retrieval quality (recall@k, nDCG) on a truth set is not measured; the default embedder is a hashing stand-in (P1-05).

## S12 (1.0.0rc1 → 1.0.0): Release — open decisions

- **R-S12-01.** No encryption at rest; protect the data root and backups with OS permissions or disk encryption.
- **R-S12-02.** Owner and reviewer identities are self-declared labels (signing / two-person rule: owner decision).
- **R-S12-03.** The security review and scan are developer-run and pattern-based; an independent review and an online dependency
  advisory check are required before any public release.
- **R-S12-04.** The owner published 1.0.0 on GitHub (Apache-2.0). The package name stays provisional (OAG-10) and the private classifier still blocks index uploads (no PyPI).
- **R-S12-05.** S11 v03 is awaiting independent acceptance; findings would be fixed in 1.0.0rc2.

## S11 (0.11.0.dev3, v03): Scientific synthesis and citation audit — limitations and pending decisions

- **R-S11-01.** S11 is deterministic and rule-based. It checks structural support (typed dimensions, anchors, current acceptance), not
  semantic entailment: a correct value can still be misread by the human who accepted it in S08, and THEMATIC claims need a human
  support judgement. No language model, local or remote, is used; a model adapter is not implemented.
- **R-S11-02.** The comparison rubric (`scivetra-synthesis-rubric/2`; comparability unchanged from /1) is designed for NUMERICAL_RESULT records of the sample
  malware-detection profile (metric, operating point and unit, dataset, year, split, PE type, ...). Other domains need their own
  profile dimensions; the general profile works with metric-as-text and subset-as-split.
- **R-S11-03.** Text dimensions (dataset name, subset, model version, temporal holdout, comparator) are compared by SHA-256 of their
  NFC / whitespace / case-normalized form: spelling variants ("EMBER-2018" vs "EMBER") are DIFFERENT, never silently equal.
- **R-S11-04.** No PRISMA counts: there is no authoritative sealed corpus or selection log in the KB (S05 is staging); output is
  `NOT_AVAILABLE` with the missing source. No meta-analysis, confidence intervals, p-values or rankings are produced.
- **R-S11-05.** Human labels (reviewers, operators) are self-declared audit labels (as in S07 / S08), not authentication; self-review
  is refused by label only.
- **R-S11-06.** Exports omit titles and all source text (identifiers, hashes, anchors and author-written statements only); a reader
  needs the KB (S08 reveal under A5) to see the passages. External sharing needs OAG-7 + A7: always DENIED.
- **R-S11-07.** Excerpt detection in author statements / rationales (`rkb-excerpt-detect/1`) does not catch paraphrases or copies
  shorter than four words (inherited S08 limitation).
- **R-S11-08.** A paper identity conflict is detected only through one DOI ACTIVE on two pinned papers; other duplicates (same work,
  different DOIs) need the S03 library's human identity review.
- **R-S11-09.** The synthetic evaluation's oracle is written by the generator from the S11 rubric itself: 100 % agreement shows that
  the rubric is implemented as specified on these fixtures, not that the rubric is scientifically complete or that any real-world
  accuracy is achieved.
- **R-S11-10.** Audits re-derive every snapshot entry through S08 services and re-read CAS: cost grows with the number of drafts;
  unmeasured on large corpora.
- **R-S11-13 (v02).** COUNT negatives need an accepted NOT_REPORTED / NOT_APPLICABLE record for the criterion. The S08 mock
  extraction records these for absence-type classes only, so an exact count over a NUMERICAL_RESULT criterion is SUPPORTED only when
  every member is positive. A human member-level ascertainment workflow (a reviewed "does not report" per paper) is not implemented:
  policy decision for the architect.
- **R-S11-14 (v02).** The statement check (`rkb-statement-lexical/1`) is lexical: closed vocabularies of datasets, metrics, splits,
  PE types, temporal and comparison words, plus negation, signs and units. Paraphrases outside them are not understood, so author
  statements are never machine-SUPPORTED (human exact-statement confirmation). False flags are possible and only ever lower a verdict.
  The vocabularies fit the malware-detection sample profile; other domains need their own.
- **R-S11-15 (v02).** Gate A5 projection uses `rkb-excerpt-detect/1` (six-word runs): template sentences that restate a value in the
  source's own word order are withheld too while that source's A5 is denied (conservative over-withholding).
- **R-S11-16 (v02).** v01 practice scopes refuse further use with `RUBRIC_CHANGED` (rubric /2); their rows are kept. Create a new scope.
- **R-S11-18 (v03).** Export release is serialized against every KB writer (one SQLite write transaction, ADR SY-D20). CAS files on
  disk are outside that lock: a deletion or change by direct filesystem access after the final full hash and before the rename
  (milliseconds) is not detected for that export, only by the next status / audit / export. A published LOCAL_REVIEW_ONLY copy is not
  recalled when rights change later. Lock contention is bounded by busy_timeout (5 s) and fails closed.
- **R-S11-17 (v02).** Statement consistency is no longer refused at creation; an inconsistent statement is stored (append-only) and
  audited UNSUPPORTED with its codes, and `claim-add` warns at once. Policy alternative for the architect: refuse at creation as well.
