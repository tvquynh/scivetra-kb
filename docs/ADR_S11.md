# ADR S11: Scientific synthesis and citation audit (0.11.0.dev3, S11 v03)

**Status:** candidate, submitted for independent architect review. Not self-accepted. S12 is not started.

**Baseline:** the architect-accepted S10 v02 (`SCIVETRA_S10_DESKTOP_AI_INTEGRATION_v02.zip`, `e6bbc0f4…b9aa`).

**Normative sources:**
- S01 v03 `08_AI_PROPOSAL_AND_EVIDENCE_CONTRACT.md` §6 (synthesis rule) and §7;
- `05_FULLTEXT_RIGHTS_AND_LIFECYCLE.md` (gates A2–A8);
- `09`, `11`;
- the S11 handoff `01`–`07`.

**Code:**
- `src/scivetra_kb/synthesis/rules.py` (pure rules, no I/O);
- `src/scivetra_kb/synthesis/service.py`;
- migration `0011_scientific_synthesis.sql`.

## SY-D01 Authority boundary

Every S11 output carries:
- `authority: NON_AUTHORITATIVE`;
- `status: DRAFT_NON_AUTHORITATIVE`;
- `publication: PUBLICATION_NOT_AUTHORIZED`;
- `authoritative_corpus: NOT_AVAILABLE_KB_STAGING_ONLY`;
- `semantic_entailment: NOT_ASSESSED_BY_MACHINE`.

Migration CHECKs make the scope's authority, status and publication values the only representable ones.

**Boundaries with earlier stages:**
- S08's `synthesis_eligible = false` is **not** reinterpreted or rewritten. S11 derives its own, separate eligibility per evidence record, `ELIGIBLE_NON_AUTHORITATIVE_DRAFT`. It means "usable for a non-authoritative research draft now", never "accepted for a manuscript or a published corpus".
- S05 FT_INCLUDE is KB staging, not a sealed Reprovane corpus. S11 reports this and sets PRISMA counts to `NOT_AVAILABLE` with the missing source.
- A human attestation of a draft (`HUMAN_REVIEWED_NON_AUTHORITATIVE_DRAFT`) says the draft is accurate to its evidence. It is not publication approval.

S11 reads S05–S10 state only through their services and tables, and inserts only into `syn_*` tables (static test S11-STATIC-WRITES). It never writes:
- an S05 ballot or lock;
- an S06 artifact;
- an S07 proposal;
- an S08 draft, review or adjudication;
- an S09 index;
- an S10 owner record;
- a rights assessment;
- a Reprovane decision.

No model is used. A local model adapter is **not implemented**; it would need a separate owner decision. The S10 MCP tool list is unchanged: no synthesis tool is exposed to AI clients.

## SY-D02 Objects

| Handoff contract | Table / structure | Notes |
|---|---|---|
| `SynthesisScope` | `syn_scope` | Pinned members of ONE S08 evidence scope. Binds the S08 scope SHA-256, the FTE run, the Reprovane release import, the protocol and profile SHA-256 and the rubric SHA-256. |
| `SynthesisEvidenceSnapshot` | `syn_snapshot` | Re-derived at each audit. References and hashes only (`contains_quotes = 0` CHECK). |
| `ComparisonKey` / `ComparabilityAssessment` | `rules.comparability`, `context_key`; stored per claim in `syn_claim_audit.comparability_json` | — |
| `ClaimUnit` | `syn_claim` | `claim_key` + `version`; a new version supersedes the old one, which is never overwritten. |
| `ClaimEvidenceLink` | `syn_claim_audit.links_json`, `link_graph_sha256` | One row per claim per audit run. |
| `ContradictionRecord` | `syn_contradiction` | — |
| `CitationAudit` | `syn_audit_run` + the deterministic report (`scivetra-synthesis/1`) | Report SHA-256 stored. |
| `HumanSynthesisReview` | `syn_review` | Append-only. Separate from S05, S07 and S08 votes. |
| `SynthesisExport` | `syn_export_event` | RESERVED → PUBLISHED / ABANDONED. `LOCAL_REVIEW_ONLY`. |
| External share request | `syn_share_request` | Always `DENIED` (CHECK). |

Every table is append-only (no-update and no-delete triggers).

## SY-D03 Scope pinning

`create_scope` takes an S08 evidence scope and, optionally, a subset of its member papers. It refuses:
- a paper that is not a member of that scope (no DOI inventory or metadata-only list): `SYNTHESIS_MEMBER_NOT_IN_EVIDENCE_SCOPE`;
- a `PROVISIONAL_NON_AUTHORITATIVE` scope: `SYNTHESIS_SCOPE_PROVISIONAL`;
- any member that is not current NOW: `SYNTHESIS_SCOPE_MEMBER_REFUSED`. Current means S08 `member_problems` is empty (S05 FT_INCLUDE, S06 artifact and link, identity, rights A2 / A6) and the S06 artifact and PDF re-read from CAS.

Each member is pinned as candidate, paper, document version, asset SHA-256, artifact id and SHA-256, and link. The membership is digest-bound. Changing the scope means creating a new scope.

## SY-D04 Evidence eligibility is re-derived at every use

At scope creation, audit, review, status and export, every S08 draft of a pinned member is re-evaluated through `EvidenceService.state()`, which covers:
- current human acceptance or adjudication;
- the latest structural verification;
- STALE / BLOCKED reasons.

S11 adds three checks of its own:
- the draft still matches the pinned identity;
- the S06 artifact and the PDF re-read from CAS;
- every anchor re-checks against the artifact (`check_anchor`).

There is no cache across calls: a revocation through another process is seen by the next call on a long-lived handle (S11-SAME-PROCESS-REVOCATION). Each record falls into one category, with this precedence:

| Category | Meaning | Verdict of a claim citing it |
|---|---|---|
| BLOCKED | rights / identity / role deny now | `BLOCKED_RIGHTS` |
| STALE | source, version, artifact, CAS or anchor changed | `STALE_SOURCE` |
| NOT_ACCEPTED | not currently human-accepted, verification not passing, review conflict, provisional | `MISSING_EVIDENCE` |
| ELIGIBLE | `ELIGIBLE_NON_AUTHORITATIVE_DRAFT` | goes to the support test |

The snapshot keeps typed non-text values and, for text dimensions (dataset, subset, model version, temporal holdout, comparator), only the SHA-256 of their normalized form. A value whose text was withheld under A3 is `WITHHELD`, never compared as equal.

## SY-D05 ComparisonKey and comparability (`scivetra-synthesis-rubric/1`, unchanged in `scivetra-synthesis-rubric/2`)

The dimensions are:
- metric;
- value and unit;
- FPR operating point **and its unit**;
- dataset, dataset year, split, PE type;
- subset, condition, temporal holdout;
- denominator, seeds;
- model version, comparator.

**Core dimensions** must be present and EQUAL on both sides: metric, unit, dataset, dataset year and split. TPR, FPR, recall and precision also need the operating point and its unit.

| Case | Result |
|---|---|
| A core dimension is missing on both sides | `NOT_ESTABLISHED`; a missing dimension is never a wildcard |
| Any context dimension is DIFFERENT | `NOT_COMPARABLE` |
| A non-core dimension is stated on one side only or withheld | `PARTIALLY_COMPARABLE` |

`model_version` and `comparator` are what a comparison compares, so they are not part of its context. Seeds and denominators missing on both sides are annotated.

**Significance and pooling.** S11 never performs a significance test or a pooled estimate (`significance: NOT_ASSESSED_NO_TEST`, `pooling: NOT_PERFORMED`).

## SY-D06 Claims, the support test and verdicts

Claims have a typed spec, `scivetra-claim-spec/1`. The kinds are:
- `REPORTED_VALUE`;
- `COMPARISON` (exactly two cited records);
- `ABSENCE` (`NOT_REPORTED` / `NOT_APPLICABLE` / `NOT_EXTRACTABLE`);
- `THEMATIC`;
- `COUNT` (count + denominator + criterion).

There are three optional flags: `asserts_significance`, `asserts_causality` and `asserts_pooled_estimate`. Numbers are canonical decimal strings: `93,1`, JSON numbers and exponents are refused. An operating point needs its unit.

**Statement discipline.** The statement is either author-supplied or produced by a deterministic template from the spec. It may not say more than the spec. **v02 (rubric /2) moved this check from creation to every audit and widened it; see SY-D16.** At creation only URLs, paths, secrets and invalid text are refused (`STATEMENT_INPUT_REFUSED`), and a copied excerpt is refused where A4 does not allow it. Instructions inside a statement are inert data.

**Verdict precedence:**
1. No cited evidence, or evidence outside the pinned scope: `MISSING_EVIDENCE`.
2. Any cited record BLOCKED: `BLOCKED_RIGHTS`; STALE: `STALE_SOURCE`; NOT_ACCEPTED: `MISSING_EVIDENCE`.
3. A visible statement inconsistent with its spec (SY-D16), or a causal, pooled or untested significance claim: `UNSUPPORTED`.
4. The kind-specific test.
5. An `AUTHOR_SUPPLIED` statement whose evidence test is SUPPORTED is `PARTIAL` until confirmed by humans (SY-D16).

**Kind-specific tests:**

| Kind | SUPPORTED when | Otherwise |
|---|---|---|
| REPORTED_VALUE | Every stated dimension equals the evidence, and the claim does not drop a context the evidence states | `MISMATCH` → `UNSUPPORTED`. Precision differences (`12` vs `12.0`), dimensions the evidence lacks, withheld text and an omitted context (TPR without its operating point) → `PARTIAL`. Results from two different settings in one claim → `UNSUPPORTED`. Another eligible value of the same paper and key → `CONFLICTING`. |
| ABSENCE | Accepted absence records of the class | A reported value of that class in the same paper → `CONFLICTING`. Zero is an observed value, never absence. |
| COMPARISON | COMPARABLE, and the relation holds | Not comparable → `UNSUPPORTED`. One-sided context → `PARTIAL`. Same paper and measurement, or a compared paper that internally reports another value → `CONFLICTING`. |
| THEMATIC | A human confirms support for the exact link graph | Structural match of class and status → `PARTIAL`. A human dispute → `UNSUPPORTED`. |
| COUNT | Member-level ascertainment is complete (SY-D17): the denominator equals the pinned member count, the count equals the POSITIVE members, no member is UNKNOWN, and the cites cover every positive (plus, optionally, the accepted NOT_REPORTED records of negatives) | Duplicates count once. A paper identity conflict → `PARTIAL`. A correct count with UNKNOWN members → `PARTIAL` (lower bound only). A count that needs an unknown member to be positive, or that the ascertained members contradict → `UNSUPPORTED`. |

**Markers.** `SUPPORTED` claims are cited `[n]`. `CONFLICTING` claims are marked `[CONFLICTED]`. Every other verdict (`PARTIAL`, `UNSUPPORTED`, `MISSING_EVIDENCE`, `STALE_SOURCE`, `BLOCKED_RIGHTS`) is marked `[UNVERIFIED]`. The pairing is enforced by CHECK. S08 structural verification is NOT_ASSESSED for semantics: it is necessary, never sufficient.

## SY-D07 Derived arithmetic

The only derived calculation is a difference between two COMPARABLE reported values. It is either:
- percentage points `|L − R|`, which needs both values in %;
- relative percent `|L − R| / R × 100`, which is undefined for a zero base.

Exact decimal arithmetic is rounded ROUND_HALF_EVEN to the claim's precision. Each derivation is logged with:
- the formula;
- the operands (draft, value, unit);
- the exact and rounded results;
- the label `DERIVED_BY_S11_NOT_REPORTED_BY_AUTHORS`.

A mismatch gives `DERIVED_DIFFERENCE_MISMATCH:computed=…`. There are no confidence intervals, p-values, means or rankings.

## SY-D08 Contradictions, comparison groups, duplicates

A `ContradictionRecord` is one of:
- `SAME_PAPER_SAME_KEY_DIFFERENT_VALUE`;
- `DOCUMENT_VERSION_VALUE_MISMATCH`;
- `S08_VALUE_STATUS_CONFLICTED`;
- `CLAIM_CONTRADICTED_BY_EVIDENCE`;
- `PAPER_IDENTITY_CONFLICT`.

Both sides are kept, with `resolution: NOT_RESOLVED_BY_S11_HUMAN_JUDGEMENT_REQUIRED`. There is no vote and no majority truth.

Different values in **independent** studies are not a contradiction. Comparison groups list eligible results side by side by context key, annotated `VALUES_DIFFER_ACROSS_INDEPENDENT_STUDIES_NOT_A_CONTRADICTION`, `BASELINE_NOT_IDENTIFIED`, `SEEDS_NOT_REPORTED`, `DENOMINATOR_NOT_REPORTED` or `CORE_DIMENSION_NOT_ESTABLISHED`. They always have `pooled_estimate: null` and `ranking: null`.

Independence is per paper: drafts of the same paper count once, and papers in an identity conflict are not independent.

## SY-D09 Paper identity conflicts: manual resolution path

**Detection.** A DOI ACTIVE (`identity_assertion_effective`) on two pinned papers is a `PAPER_IDENTITY_CONFLICT`. It makes claims depending on those papers `PARTIAL`.

**Resolution.** S11 never merges or picks a paper. The operator resolves the conflict in the S03 library:
1. `rkb library search --doi …` shows the papers and their assertions.
2. `rkb library resolve-conflict --conflict-id … --action … --evidence-ref … --withdraw-assertion-id …` (or `--activate-assertion-id …`) records a human resolution with evidence references.
3. A new audit then sees the resolved identity.

Each step is append-only and auditable.

## SY-D10 Citation audit, references and traceability

References are numbered `[1]`, `[2]`, … in order of first citation by a SUPPORTED claim (claims sorted by key). One reference is one source document version: paper, document version and class, PDF SHA-256, S06 artifact SHA-256.

**DOI.** The DOI is shown only when exactly one ACTIVE DOI assertion exists; otherwise `NO_ACTIVE_DOI_ASSERTION` / `MULTIPLE_DOIS_UNRESOLVED`. The title is not included (`NOT_INCLUDED_S11_CITES_IDENTIFIERS_ONLY`); no bibliographic fact is invented.

**What validates a claim.** A reference entry is never validation. Validation is the claim's link: the exact draft, its semantic SHA-256, its anchors (page, span, excerpt SHA-256), its eligibility and the link result.

**Traceability invariant.** Traceability = (SUPPORTED claims with references and only eligible links + visibly flagged claims) / all claims. It must be 100%, or the audit fails (`TRACEABILITY_INCOMPLETE`).

## SY-D11 Human review

Reviewers come from the owner-registered HUMAN roster (S07 `ai_reviewer`). Labels are self-declared audit labels, not authentication. The authors of the claims, the scope and the audit run cannot review (`SELF_REVIEW_REFUSED`).

**Draft decisions** (bound to the audit report SHA-256):
- `ATTEST_DRAFT_ACCURATE`, which needs `UNVERIFIED_CLAIMS_ACKNOWLEDGED` when any claim is not SUPPORTED;
- `REQUEST_CHANGES`;
- `REJECT_DRAFT`.

**Claim decisions** (THEMATIC claims and, since v02, AUTHOR_SUPPLIED statements; bound to the exact link graph SHA-256, which includes the statement SHA-256): `CONFIRM_SUPPORT` and `DISPUTE_SUPPORT`. `CONFIRM_SUPPORT` is accepted only while the claim awaits a human judgement (`human_judgement: REQUIRED`: every machine check passes) and its statement is shown (not WITHHELD under A5). A claim judgement takes effect at the next audit, so the audit it was recorded on stays reproducible.

**Rules:**
- Only the latest audit can be reviewed, and only while it still reproduces NOW (`REVIEW_TARGET_NOT_CURRENT`).
- `DUAL_INDEPENDENT` (default) needs two attestations; `SINGLE_HUMAN` needs a protocol reason.

**Derived state**, recomputed at read time:
- `NOT_REVIEWED`;
- `PARTIALLY_REVIEWED`;
- `HUMAN_REVIEWED_NON_AUTHORITATIVE_DRAFT`;
- `CHANGES_REQUESTED`;
- `REVIEW_CONFLICT`;
- `REJECTED`;
- `REVIEW_STALE_AUDIT_NO_LONGER_REPRODUCES` (evidence, rights or claims changed: the old approval goes stale);
- `REVIEW_STALE_EARLIER_AUDIT`.

A model output cannot become human-approved by itself: there is no model, and template statements are typed specs checked against evidence.

## SY-D12 Rights and privacy

| Gate | How S11 applies it |
|---|---|
| A2 / A6 | Current S06 access, through S08 `member_problems`, at every use |
| A3 | Machine-extracted text stored only by S08 when A3 allows. S11 never copies quotes or text field values: text dimensions are SHA-256 only; a withheld value is `WITHHELD`. Exports contain no quote, document text or text field value (leak scan of stored quotes and pack segments; `EXPORT_LEAK_REFUSED`). |
| A4 | Human-typed text in S11 is checked with `rkb-excerpt-detect/1` against every pinned source where A4 does not allow storing an excerpt. This covers statements, rationales, reason codes, scope labels and review policy reasons. A copy is refused (`HUMAN_TEXT_SOURCE_EXCERPT_REFUSED`); text whose source cannot be read is refused too. |
| A5 | Applied NOW at every projection (SY-D18): claim statements and spec text values in the audit report, `claim-add` output, CLI output and every export file, and rationales and reason codes in `reviews`. Shown while A5 allows every pinned source; otherwise shown only if they contain no excerpt of a denied source, else `WITHHELD_A5` (statements) / `WITHHELD` with SHA-256 and length. A denied source that cannot be read fails closed. Rows are unchanged. The export leak scan also refuses any line holding a short excerpt of a denied source (`EXPORT_LEAK_REFUSED`). |
| A7 + OAG-7 | External sharing or publication is always `DENIED`. The request is recorded (`syn_share_request`) with `OAG7_SHARING_POLICY_NOT_AUTHORIZED` and the per-member A7 reason. Exports are `LOCAL_REVIEW_ONLY` (CHECK). There is no bypass through file exchange. |
| A8 + OAG-6 | Not used. No external AI processing in S11. The S10 gateway is unchanged and exposes no synthesis tool. |

Logs and audit rows hold ids, hashes, codes and states only. No PDF text is logged.

## SY-D13 Export

`export` writes `synthesis.json` (the report), `evidence_matrix.csv` (claim × link × anchor), `draft.md` (review-only Markdown) and `MANIFEST.json` (file SHA-256). It runs only if the latest audit still reproduces NOW (`EXPORT_AUDIT_NOT_CURRENT`); rights, sources and acceptance are re-checked at publish time.

**Lifecycle**, as in S08 ES-D14 (v03: the final check and the publication are one critical section, SY-D20):
1. A RESERVED event is committed before anything is written. If that commit fails, nothing is written (`EXPORT_AUDIT_FAILED`).
2. The files are written fsync'd to a staging folder and renamed atomically.
3. A PUBLISHED event is appended.

**Failures:**
- A write failure leaves nothing visible (`ABANDONED PUBLICATION_FAILED`).
- A failure to record PUBLISHED withdraws the files (`ABANDONED AUDIT_COMPLETION_FAILED_WITHDRAWN`).

**Destination.** The output folder must be an absolute path outside the KB root, without `..`. Bytes are deterministic: the same audit gives the same bundle SHA-256.

## SY-D14 Migration

`0011_scientific_synthesis.sql` is purely additive: 9 `syn_*` tables, CHECKs and triggers. 0001–0010 are byte-identical to S10 v02 (`tools/s10v02_migration_hashes.json`). The upgrade from 10 to 11 is backup-first, and rollback restores the exact schema-10 file (S11-UPGRADE-10-11, S11-ROLLBACK).

## SY-D16 The visible statement is what is verified (v02, S11-F03 / S11-F04)

v01 checked the typed spec against the evidence, and the author's sentence only loosely against the spec: a sentence naming SOREL, PR-AUC, a training split or `-93.1%` over an EMBER / TPR / TEST / `93.1%` spec could be SUPPORTED. Rubric `scivetra-synthesis-rubric/2`:

- **Every audit re-checks the stored statement** (`rules.statement_checks`, `rkb-statement-lexical/1`) and any inconsistency makes the claim `UNSUPPORTED` with `STATEMENT_NOT_CONSISTENT_WITH_SPEC` and the specific code. The author's words are stored unchanged (append-only provenance); they are never repaired or normalized. `claim-add` reports the same codes at once as an early warning (`statement_check_codes`, `statement_status`).
- **Numbers:** compared with their sign, exactly (`-93.1` ≠ `93.1`; ASCII hyphen-minus, U+2212, U+2013, U+2010, U+2011, U+2012, U+2014, U+FE63 and U+FF0D are minus); a hyphen inside a word or a range is not a sign. The unit next to each number must match: `%` for a percentage value or operating point, `pp` / `percentage points` for a percentage-point difference, no `%` for a ratio, a year or a count.
- **Closed vocabularies** (in the rubric): metric (TPR = recall = sensitivity; ROC-AUC; PR-AUC = average precision; FPR, FNR, TNR, precision, accuracy, F1, MCC; a bare `AUC` cannot stand for another metric), dataset (EMBER, SOREL, BODMAS, DREBIN, AndroZoo, VirusShare, VirusTotal, Malimg, BIG 2015, CIC-MalMem, MOTIF), split (train / test / validation / challenge, only next to split, set, data, partition, subset, fold, corpus or portion), PE type (Win32, Win64, .NET, ELF, APK). A mention that differs from the spec contradicts it; a mention of a dimension the spec does not state says more than the spec. For a COMPARISON, the context is the dimensions both cited results share (text dimensions by hash).
- **Temporal words** (earlier, later, newer, older, future, temporal, time split, drift, before / after a year) need a `temporal_holdout` in the spec. **Negation** (not, no, never, without, …) is not allowed in REPORTED_VALUE, COMPARISON or COUNT statements (an ABSENCE claim is the typed way to say "not reported"). **Comparison direction** words (higher / better / outperforms; lower / worse; equal / on par) must match the spec relation, in the cited order.
- **Only deterministic templates are machine-SUPPORTED.** An `AUTHOR_SUPPLIED` statement whose evidence test is SUPPORTED becomes `PARTIAL` with `AUTHOR_STATEMENT_NEEDS_HUMAN_EXACT_STATEMENT_CONFIRMATION` and `human_judgement: REQUIRED`. The review policy's humans (two for DUAL_INDEPENDENT) can confirm that exact statement for the exact link graph (`CONFIRM_SUPPORT`); it is SUPPORTED from the next audit. Another wording is another statement SHA-256, another link graph, and needs a new confirmation. A dispute makes it `UNSUPPORTED`.

**Scientific limits.** This is a conservative lexical check, not semantic entailment. It finds the listed contradictions; it does not prove that an author sentence means what the spec means. Paraphrases outside the vocabularies, reordered comparisons ("B is lower than A" for HIGHER is flagged, conservatively), scope words ("all", "only"), hedges and implicature are not understood. That is why an author statement never becomes machine-SUPPORTED. False flags are possible (e.g. "after" next to a year); they make a claim UNSUPPORTED, never SUPPORTED.

## SY-D17 COUNT ascertainment (v02, S11-F02)

v01 treated a member without an *eligible record of the evidence class* as a negative, so an accepted PR-AUC hid an unaccepted matching TPR and `1 of 2` was SUPPORTED. Rubric /2 classifies every pinned member for the criterion (`rules.count_ascertainment`, reported per claim as `count_ascertainment`):

| State | When |
|---|---|
| POSITIVE | a current, accepted (ELIGIBLE), REPORTED record of the class whose every criterion dimension is EQUAL |
| UNKNOWN (`POSSIBLY_MATCHING_EVIDENCE_NOT_USABLE:<category>`) | a record that could match is NOT_ACCEPTED, STALE or BLOCKED; this wins over a negative record |
| NEGATIVE | a current, accepted NOT_REPORTED / NOT_APPLICABLE record of the class with an EQUAL criterion context (NOT_EXTRACTABLE is not a negative) |
| UNKNOWN (`MATCH_UNDETERMINED_…`) | an eligible record whose match cannot be decided (missing, withheld or indeterminate dimension) |
| UNKNOWN (`NO_ASCERTAINMENT_RECORD_MISSING_EXTRACT_IS_NOT_NOT_REPORTED`) | anything else: a missing extract is not NOT_REPORTED; a result in another context does not show the member lacks this one |

Verdicts: count = positives and no UNKNOWN → `SUPPORTED` (`COUNT_ASCERTAINMENT_COMPLETE`); count = positives with UNKNOWN members → `PARTIAL` (lower bound only); count above the positives but reachable only through unknowns → `UNSUPPORTED` (`COUNT_NOT_ESTABLISHED_UNKNOWN_MEMBERS_ARE_NOT_POSITIVES`); count below the positives or above positives + unknowns → `UNSUPPORTED` (`COUNT_CONTRADICTED_BY_ASCERTAINED_MEMBERS`); cites that are neither a positive nor an ascertained negative → `UNSUPPORTED`. A zero count must cite the accepted NOT_REPORTED records it rests on (no cites → `MISSING_EVIDENCE`), so zero and missing evidence stay distinct. No denominator is invented: it is the pinned member count.

**Consequence.** With the S08 mock extraction, explicit negatives exist for absence-type classes (an uncertainty interval NOT_REPORTED), not for "this paper has no TPR at 1% FPR on EMBER 2018". An exact count over a NUMERICAL_RESULT criterion is therefore SUPPORTED only when every member is positive; otherwise it is at most PARTIAL. A human ascertainment workflow (recording a reviewed negative per member) is not implemented (R-S11-13).

## SY-D18 Gate A5 on every statement projection (v02, S11-F01)

An author statement may legitimately contain a source excerpt while A4 allows storing it. v01 kept showing it after A5 was withdrawn, and its leak scan only looked for whole stored segments. In v02:

- the audit report, the `claim-add` output and so the CLI and every export file project each statement (and every spec text value) through gate A5 **at that moment**: `statement_disclosure: SHOWN`, or `WITHHELD_A5` with `statement_sha256` and `statement_chars` and `statement: null`;
- the test is `rkb-excerpt-detect/1` against every pinned source whose A5 is not ALLOW; if such a source cannot be read, any text of four or more words is withheld (fail closed);
- review history withholds reason codes that spell an excerpt, as it already did for rationales;
- the export leak scan also checks every file line against denied sources and refuses (`EXPORT_LEAK_REFUSED`); the RESERVED → PUBLISHED / ABANDONED lifecycle is unchanged;
- the stored `syn_claim` row is never rewritten; after the owner reinstates the right, the statement is shown again;
- the statement projection is part of the report SHA-256, so a rights change makes an earlier audit and its reviews stale.

**Conservative effect.** A template sentence such as "TPR of 93.1% at 1% FPR on …" shares six consecutive words with the source sentence it restates, so it is withheld too while that source's A5 is denied. Nothing is shown that A5 does not allow; some harmless restatements are hidden.

## SY-D19 Rubric /2, migration 0011 and v01 practice databases

`migration 0011` is unchanged from v01 (byte-identical); its CHECK pins the engine id `scivetra-synthesis-engine/1`, so the engine id is kept and the behaviour change is versioned by the rubric (`scivetra-synthesis-rubric/2`, its SHA-256 is bound into every scope and audit run). A v01 practice database keeps all its rows and opens normally; its v01 scopes refuse further use with `RUBRIC_CHANGED` ("create a new scope"), because their audits were made under rubric /1. Upgrade 10 → 11, rollback and backup / restore are unchanged.

## SY-D20 Release-time currency of exports (v03, S11-F05)

v02 checked the audit, rights and sources when an export started. It then staged the files and published them without a further check. A rights revocation committed by another connection, or a PDF deleted from CAS, during staging was therefore published (architect reproducer: 1/3).

**Design.** The release is one critical section inside a single SQLite write transaction (`BEGIN IMMEDIATE`, `busy_timeout` 5 s). The steps are:

1. `RESERVED` is committed (unchanged).
2. The files are written fsync'd to a staging folder (unchanged).
3. The transaction begins. Every KB writer is now blocked: rights assessments, S08 reviews and adjudications, S05 / S06 changes, other audits and exports.
4. `_release_check`:
   - this audit is still the latest;
   - the report re-derived **NOW** reproduces the audited report SHA-256. This covers current S08 acceptance and verification; rights A2 / A3 / A5 via S08 state and the A5 statement projection; S05 / S06 currency; a CAS re-read of every parse artifact and a full hash of every pinned PDF; and every anchor.
   - the bundle rebuilt from it is byte-identical to the staged files, and the staged files on disk still have their hashes;
   - the A5 leak scan passes.
5. `os.replace(staging, folder)`, then the `PUBLISHED` event is inserted and the transaction commits.

**Refusals and failures:**

| What happens | Error | Outcome recorded | What remains on disk |
|---|---|---|---|
| A check fails | `EXPORT_RELEASE_REFUSED` | `ABANDONED RELEASE_REFUSED:<reason>` | the staging folder is removed; nothing is visible |
| Failure before the rename: lock not obtained within `busy_timeout`, I/O | `EXPORT_RELEASE_FAILED` | `ABANDONED RELEASE_FAILED` | nothing |
| Failure after the rename, before commit | `EXPORT_AUDIT_FAILED` | `ABANDONED AUDIT_COMPLETION_FAILED_WITHDRAWN` | the folder is withdrawn |

The `RESERVED → PUBLISHED / ABANDONED` semantics and the single-terminal triggers are unchanged; migration 0011 is unchanged.

**Guarantees and residual race.**

- **KB state.** Any change committed before the release obtains the lock is seen and refuses. A writer that wants to commit during the release waits until the bundle is published. Its change is then later than the publication, and the next status, audit or export sees it (the published review copy is not recalled; it is LOCAL_REVIEW_ONLY).
- **CAS files on disk.** These are not protected by the SQLite lock. A deletion or alteration made with direct filesystem access *after* the final full hash and before the rename (milliseconds) is not detected for this export. It is detected by the next status, audit or export. S11 never deletes CAS objects; the only writers are the rkb services, which store new objects and do not remove them.
- **Lock contention.** It is bounded by `busy_timeout`. A release that cannot get the lock fails closed (`EXPORT_RELEASE_FAILED`) and never publishes.

## SY-D15 Not done (DEFERRED / owner-gated)

- **Not implemented:**
  - LLM narrative generation and a local model adapter;
  - semantic entailment;
  - meta-analysis;
  - PRISMA counts (no authoritative corpus);
  - table / figure evidence.
- **Not used:** real PDFs or corpora, external sharing (OAG-7), external AI (A8 / OAG-6), S12.
- **Domain of the comparison rubric.** It is tuned for NUMERICAL_RESULT in the malware-detection sample profile. The general profile works with its own dimensions (metric as text, subset as split).
