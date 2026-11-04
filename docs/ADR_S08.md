# ADR S08: Scientific evidence extraction (0.8.0.dev3; S08 v02 corrective revision in ES-D14, S08 v03 in ES-D15)

Status: candidate, submitted for independent architect review. Not self-accepted. Baseline: accepted S07 v02
(`SCIVETRA_S07_AI_ASSISTED_SCREENING_v02.zip`, sha256 `d2ad98fd…464f`). Normative: S01 v03 `08_AI_PROPOSAL_AND_EVIDENCE_CONTRACT.md`
§6 (EvidenceRecord), §7, `05` (rights gates A2–A8); handoff `SciVetra_S08_Handoff` (`01`, `02`, `03`).

## ES-D1. Authority boundary

An EvidenceDraft is **NON_AUTHORITATIVE** and always `HUMAN_REQUIRED`. It is not a screening decision and does not change S05
ballots, adjudications or locks, S07 proposals or approvals, a Reprovane decision or corpus membership: the evidence package
inserts only into `ev_*` tables (static test) and a complete S08 cycle leaves every other table byte-identical (test). A human
acceptance makes a draft **accepted KB evidence**; it is still not an authoritative corpus, not RAG index text and not a
manuscript claim (`authoritative_corpus`, `rag_index_eligible` and `synthesis_eligible` are always false in S08; S09–S11 have
their own gates).

## ES-D2. Objects (one-to-one with the handoff)

| Handoff object | Table | Notes |
|---|---|---|
| extraction schema / vocabulary (versioned) | `ev_profile` | `rkb-evidence-profile/1`, SHA-256 bound; may supersede a previous profile |
| `EvidenceExtractionScope` | `ev_scope` | explicit, digest-bound members of ONE S05 FTE run (bound to its Reprovane release import and protocol SHA-256) |
| immutable `ExtractionInputPack` | `ev_input_pack` + CAS | `rkb-evidence-input-pack/1`, canonical JSON, schema-8 CAS reference |
| extractor call | `ev_extraction_run` | mock or validated operator import; invalid responses are kept with their codes |
| immutable `EvidenceDraft` | `ev_draft` | S01 `EvidenceRecord` (`ev_`) fields; corrections are new drafts (`supersedes_draft_id`) |
| `EvidenceVerification` events | `ev_verification` | `rkb-evidence-verify/1`, append-only, `semantic_entailment = NOT_ASSESSED` |
| reveal audit | `ev_reveal` | every disclosure of a draft to a reviewer; quote `SHOWN` / `WITHHELD` / `NO_QUOTE` |
| `EvidenceReview` | `ev_review` | ACCEPT / REJECT / CORRECT / DEFER, latest per reviewer counts |
| adjudication | `ev_adjudication` | resolves exactly the current conflicting reviews |
| export audit | `ev_export_event` | metadata only (`contains_quotes = 0` by CHECK) |

Scoped adaptations of S01 08 §6 (not redefinitions): `evidence_type` is the profile's `evidence_class` (versioned vocabulary);
`verification_state` is split into the append-only structural state (`STRUCTURALLY_VERIFIED`, `ABSENCE_RECORDED`, `PARTIAL`,
`UNSUPPORTED`, `AMBIGUOUS`, `STALE`) and the derived human state (`HUMAN_ACCEPTED`, `HUMAN_REJECTED`, `HUMAN_CORRECTED`,
`REVIEW_CONFLICT`, `HUMAN_ACCEPTED_BY_ADJUDICATION`, …); S01 `VERIFIED` (automatic quote match + human accept) corresponds to
`STRUCTURALLY_VERIFIED` + `HUMAN_ACCEPTED`. `origin` is `AI_PROPOSAL` (with `run_id`) or `HUMAN_CORRECTION`. Only
`source_kind = FULLTEXT_TEXT` exists (ES-D6). `HUMAN_READING` evidence is DEFERRED.

## ES-D3. Scope and eligibility

A scope member is `{candidate_id, parse_artifact_id}` of one S05 run. Creation binds and records the paper, the
document version and asset of the S06 `RESEARCH_PARSE` link, the artifact SHA-256, the parser identity and the S05 state, and
refuses (`SCOPE_MEMBER_REFUSED`): another paper's artifact, a link that is not the candidate's current S05 evidence (a preprint,
another version or a supplement is never substituted), the same bytes linked to another paper or document version
(`ASSET_IDENTITY_COLLISION`, quarantined until a human rejects that link), no text layer, a superseded artifact, rights/identity
problems from `ParseService.current_access()`, gate A6 (local model) not ALLOW, FT_EXCLUDE / NOT_RETRIEVED members, and PENDING
members. A journal inventory, a bare DOI and an S07 proposal are never a basis.

`eligibility_mode = PROVISIONAL_NON_AUTHORITATIVE` (reason required) admits PENDING candidates as
`member_class = PROVISIONAL_NON_AUTHORITATIVE`: their drafts can be extracted, verified and reviewed, but **never accepted**
(service, trigger `ev_review_no_provisional_accept`, doctor invariant). FINAL FT_INCLUDE members are `FT_INCLUDE_KB_STAGING`:
S05 is itself NON_AUTHORITATIVE staging, so even accepted evidence is not an authoritative corpus.

## ES-D4. Current validity is derived (STALE / BLOCKED)

`member_problems()` re-evaluates at pack build, extraction, verification, state, reveal, review and export:
`BLOCKED:*` = rights now deny (`RIGHTS_*`, A6), identity no longer HUMAN_CONFIRMED, role no longer MAIN_TEXT, asset collision;
`STALE:*` = artifact hash changed or superseded, link moved, document version changed or superseded, the S05 evidence link
changed, the S05 outcome is no longer FINAL FT_INCLUDE (or a provisional member became excluded), profile superseded, the pack
no longer reproduces. Rows are never rewritten; a BLOCKED/STALE draft does not count as accepted, cannot be accepted, and its
quote is withheld.

## ES-D5. Extraction and strict validation

The mock (`evidence/mock.py`) is deterministic and tool-less (imports `hashlib`, `json`, `re`; no KB, file or network access)
and recognises the fixed sentence forms of the synthetic fixtures only. Operator import accepts `rkb-evidence-response/1` and
records the model as `OPERATOR_DECLARED_UNVERIFIED` (`attested: false`); importing never authorizes data transmission.

One validator handles both. Whole-response refusal (`SCHEMA_INVALID`, no draft): > 512 KiB, non-strict JSON (NaN, Infinity,
deep nesting), duplicate keys, wrong key sets, wrong pack / paper / document version / artifact / profile, leaks (local paths,
secrets, tokenised URLs). Record-level refusal (run `PARTIAL`, other records proceed): unknown class or field, JSON numbers
(`NUMBER_MUST_BE_DECIMAL_STRING`), non-canonical decimals (`93,1`), missing unit (`UNIT_MISSING`, never inferred), invalid
enum/unit, null values, absence statuses with values or zero (`ABSENCE_WITH_VALUES`), REPORTED without anchor or required
field, CONFLICTED with < 2 anchors or with values, inconsistent quote hash, and unverifiable source kinds (ES-D6).

## ES-D6. Tables, figures, OCR

S06 provides text with code-point offsets but no table cells, figure geometry or OCR. `FULLTEXT_TABLE`
(`TABLE_GEOMETRY_NOT_AVAILABLE`), `FULLTEXT_FIGURE_CAPTION` and `OCR_TEXT` records are refused and listed; no cell, coordinate
or content is invented. Text that the parser emitted from a table is an ordinary text quote and is checked like any other.

## ES-D7. Typed evidence and absence

Profiles are data (`evidence/profiles.py`): classes, typed fields (`text`, `decimal`, `integer`, `unit`, `enum`), units with
terms, enum values with evidence terms (values without terms are INTERPRETIVE and reported as not structurally checkable), and
`key_fields` that identify the same measurement. Built-in: `rkb-general-empirical` (16 classes: objective, design,
population/sample, dataset, period, labelling/leakage controls, method, baseline, implementation constraint, outcome metric,
numerical result, uncertainty interval, statistical comparison, shift condition, limitation, ethics/licensing) and the sample
`rkb-malware-detection` profile (PE type, train/test/challenge split, dataset and year, temporal holdout, FPR operating point
with its own unit, TPR, ROC-AUC vs PR-AUC vs AP, seeds, model version). Operators register other domains through the same
validator. Absence is explicit: `NOT_REPORTED`, `NOT_EXTRACTABLE`, `UNCERTAIN`, `CONFLICTED`, `NOT_APPLICABLE` (never zero,
false or null values).

## ES-D8. Structural verification (`rkb-evidence-verify/1`) is not entailment

Per draft: pack and member still current (else `STALE`); artifact readable; parser version unchanged; every anchor on its own
page (`PAGE_OUT_OF_RANGE`), exact code-point offsets and quote SHA-256 against the S06 artifact (`check_anchor`). A stored quote
found elsewhere on the same page gives `PARTIAL` with a `SUGGESTED_NOT_APPLIED` anchor (nothing is repaired). Each typed value is
then checked against the verified quotes only: text fields occur literally (NFC, case/whitespace-insensitive); enum values need
one of their evidence terms (ROC-AUC is not PR-AUC); numbers use `rkb-number-match/1` (S07 v02) as complete tokens — `12` is not
`312`, `1,234` is `1234`, `12` is not `12.0`; a value that only an ambiguous form could produce (`1,5` for `1.5`) is `AMBIGUOUS`;
unit `%` needs a percent sign at that token and a bare decimal is never a percentage (`0.931` is not `93.1%`); the FPR operating
point is its own number with its own unit (TPR@1%FPR ≠ TPR@0.1%FPR); denominators, years, datasets and splits must occur. A
draft passes as `STRUCTURALLY_VERIFIED` (or `ABSENCE_RECORDED`) only with no failure. **This is not semantic entailment**
(`semantic_entailment = NOT_ASSESSED`, CHECK): a correct quote can be misinterpreted (the pilot's `pair_swap` case swaps two
values within one sentence and still passes). Only a human can accept.

## ES-D9. Human review, independence and audit

Reviewers are the owner-registered HUMAN roster of S07 (`ai_reviewer`; AI/system/model labels refused). Labels are
self-declared audit labels, not authentication (R-S08-05). Rules (service + triggers + doctor):
- a review needs a reveal of the current verification (`REVEAL_OUTDATED` otherwise); ACCEPT needs the source quote to have been
  SHOWN (or NO_QUOTE for absence) — `EVIDENCE_NOT_SEEN` when gate A3 kept only a hash or the source is blocked;
- no self-review: the operator who ran the mock, imported the response or wrote the correction cannot review it;
- ACCEPT needs the latest verification STRUCTURALLY_VERIFIED / ABSENCE_RECORDED, a current (not STALE/BLOCKED) source, a
  non-provisional member, and `CONFLICT_ACKNOWLEDGED` when another draft gives a different value for the same measurement key;
- REJECT and CORRECT need reason codes and a rationale; CORRECT stores the corrected claim as a new `HUMAN_CORRECTION` draft
  (validated like any record) that needs its own verification and an independent review;
- `DUAL_INDEPENDENT` (default) needs two concordant reviewers; disagreement is `REVIEW_CONFLICT` and only an independent
  adjudicator citing exactly the current latest reviews resolves it; `SINGLE_HUMAN` needs a protocol reason.
`audit` returns the run (model, template, pack, profile, policy, raw-response hashes), all verifications, reveals, reviews and
adjudications.

## ES-D10. Rights

Gate A2 / evidence eligibility (S06 `current_access`) and A6 at scope and pack; quotes stored only when A3 allows (else
`HASH_ONLY_GATE_A3_DENIED`: anchors + SHA-256 only). Verbatim `text`-typed field values are excerpts too: they follow the quote
(stored only with an A3-stored quote, else `WITHHELD_GATE_A3_DENIED`, see ES-D14). Reveal re-checks current validity and the
current gate A5 for quotes and text values; `show` and `audit` never carry either; exports never contain quotes because
A7 (`redistribute`) is never ALLOW in `rights-policy/1` (counted in `excluded_by_reason`); A8 (external AI) is not used: only
`LOCAL_PROCESS` exists (CHECKs). Rights withdrawal → BLOCKED, quote withheld, no acceptance, no export of content.

## ES-D11. Export

`rkb-evidence-export/1` is deterministic (no timestamps or row ids; drafts identified by semantic SHA-256). `METADATA_ONLY`
(default): identifiers, hashes, anchors, classes, statuses and states. `ACCEPTED_TYPED_VALUES`: adds typed values only for
currently accepted, non-provisional drafts. Never quotes or document text: the bytes are scanned for pack segment text, stored
quotes and local paths before writing (`EXPORT_LEAK_REFUSED`). Publication follows the audited lifecycle of ES-D14.

## ES-D12. Storage and migration

`0008_scientific_evidence_extraction.sql`: 10 `ev_*` tables, CHECKs, foreign keys, consistency triggers (pack ↔ scope member,
run ↔ pack, draft ↔ pack/run/corrected draft, review/adjudication rules) and no-update/no-delete triggers on every table.
0001–0007 are byte-identical to S07 v02. Upgrade 7 → 8 is backup-first (existing `upgrade_kb`), rollback restores the exact
schema-7 file; `doctor --full` checks eight S08 invariants (v02) and reports an evidence manifest. Schema-8 CAS reference:
`ev_input_pack.pack_sha256`.

## ES-D14. S08 v02 corrective revision (architect findings S08-F01, S08-F02)

**S08-F01: verbatim typed text under gates A3 / A5.** Profile fields of type `text` hold verbatim phrases of the quote (e.g.
`LIMITATION.limitation`, `METHOD.method`, `NUMERICAL_RESULT.dataset`). v01 stored and returned them unconditionally.
- *Creation (A3).* `ev_draft.text_storage` is `NO_TEXT_FIELD`, `STORED_GATE_A3` (only together with an A3-stored quote, CHECK) or
  `WITHHELD_GATE_A3_DENIED`. When withheld, every text value is removed from `typed_fields_json`; `withheld_text_json` keeps
  only SHA-256, length and the exact position (anchor index, offset) in the cited quote. No other column keeps it: the raw
  response is stored as a hash only and run records as codes. An unknown field counts as text (fail closed). The draft keeps the
  extractor's value status (provenance) and can never be accepted (reveal WITHHELD, so `EVIDENCE_NOT_SEEN`).
- *Verification.* A withheld value is re-checked by hash at its recorded position of the artifact-verified quote
  (`WITHHELD_TEXT_HASH_IN_QUOTE`), so `STRUCTURALLY_VERIFIED` keeps its meaning. A value without an exact position (it matched only
  after case / whitespace normalisation) cannot be re-checked: new verification state `NOT_ASSESSABLE`
  (`TEXT_NOT_ASSESSABLE_GATE_A3_DENIED:<field>`), never VERIFIED and never acceptable.
- *Display (A5).* `show` and `audit` (SDK and CLI) return non-text fields plus `withheld_text_fields` names and
  `text: WITHHELD_USE_REVEAL`; never a value. `reveal`, the audited path, shows quote and text values only when nothing blocks:
  quote and text stored under A3, current gate A5 `ALLOW` (else `GATE_A5_DENIED:<reason>` / `GATE_A5_UNDETERMINED:<code>`), and
  a current source (STALE / BLOCKED reasons). Otherwise both are WITHHELD with every reason code.
- *Database.* Trigger `ev_reveal_matches_storage`: a SHOWN reveal is refused for a draft whose quote or text was not stored, and
  NO_QUOTE is allowed only for drafts without evidence text, so the existing ACCEPT-needs-SHOWN triggers also cover text. Doctor
  invariant `EV_TEXT_STORED_WITHOUT_GATE_A3`.

**S08-F02: durable export audit before visibility.** `ev_export_event` is an append-only lifecycle: `RESERVED` (committed
before anything is written; if it cannot be committed the export fails with `EXPORT_AUDIT_FAILED` and nothing is written),
then exactly one terminal event citing it (trigger `ev_export_event_terminal_matches_reservation`: same scope, level, report
SHA-256, item count, exclusions, folder and destination hash): `PUBLISHED` after the fsync'd write and atomic rename, or
`ABANDONED` with an outcome code. Failure semantics:
- write / rename failure: staging removed, nothing visible, `ABANDONED PUBLICATION_FAILED`, `EXPORT_WRITE_FAILED`;
- PUBLISHED cannot be committed: the report is withdrawn (renamed away and deleted), `ABANDONED AUDIT_COMPLETION_FAILED_WITHDRAWN`,
  `EXPORT_AUDIT_FAILED`; if even the withdrawal fails, the report is covered by its committed reservation and stays UNCONFIRMED;
- crash after the rename: the visible report has its committed RESERVED event; `rkb evidence exports` reports it `UNCONFIRMED`
  (never completed); the next export of the same bytes to the same destination records `PUBLISHED RECOVERED_AFTER_INTERRUPTION`
  without rewriting the file; stale reservations whose report is absent are closed `ABANDONED NOT_PUBLISHED_FOUND_ON_RETRY`;
- repeat of a completed export: no-op (`EXPORT_ALREADY_PUBLISHED`, no new event); identical bytes in a destination that no
  reservation records (e.g. a copied folder): `EXPORT_FOLDER_UNAUDITED`.
The destination is recorded only as `target_path_hash` (no path). Report bytes, determinism, the metadata-only default, A7 and
the leak scan are unchanged. Doctor invariant `EV_EXPORT_OUTCOME_WITHOUT_RESERVATION`; the evidence manifest lists `phase:sha256`.

**Migration.** 0008 was not yet accepted and is changed in place (new SHA-256 in `01_IMPLEMENTATION_MANIFEST`); 0001-0007 are
unchanged. A practice KB upgraded by S08 v01 code carries the v01 0008 hash and is refused by v02 with `MIGRATION_DRIFT` (fail
closed, nothing written). Recovery: restore the `kb.sqlite.pre-7-...` file (or the verified backup) kept by the v01 upgrade and
run `rkb upgrade` with v02 (rehearsed by `tools/s08_upgrade_rehearsal.py --s08v01-source`). No accepted KB is affected.

## ES-D15. S08 v03 corrective revision (architect finding S08-F03): human-typed text under gates A4 / A5

Reviewers and adjudicators type free text that can contain a passage copied from the document. Gate A4 ("store a human-typed
quote locally") governs storing it and gate A5 ("show stored quotes") governs showing it, exactly as A3 / A5 govern machine
quotes (S01 v03 05).

**Human free-text surfaces in S08** (complete list): `ev_review.rationale`, `ev_review.reason_codes_json` (UPPER_SNAKE codes can
spell words), `ev_adjudication.rationale`, `ev_scope.label`, `ev_scope.provisional_reason`, `ev_scope.review_policy_reason`.
A correction's `note` and an imported response's `notes` / record `note` are validated but never stored. Profile labels are
written before any document exists and are not tied to a source. Exports, `show`, `state`, `summary`, `reveal` and
`export_history` never contain human free text; `audit` (SDK and CLI) is the only read path.

**Detection** (`evidence/excerpt.py`, `rkb-excerpt-detect/1`). The test is deterministic and grounded in the pinned S06 artifact
text of the draft's document (local processing under A2).
- Both texts are NFKC-normalised, case-folded and split into words (letters and digits; punctuation, underscores and whitespace
  separate).
- A text contains an excerpt when any 6 consecutive words occur in the source, or when the whole text has at least 4 words and
  occurs in the source.
- Exact and near-verbatim copies are caught: case, spacing, punctuation, full-width or compatibility forms, and a copy embedded
  in a longer note.
- Not caught (documented): paraphrases, reordering, translations, copies with an edit at least every five words, and fragments
  under four words.
- Common six-word phrases that also occur in the source are refused (fail closed).

**Write time (A4).** `review`, `adjudicate` and `create_scope` evaluate A4 with the CURRENT assessment of each source document
(an undetermined assessment denies).
- Where A4 does not allow, text containing an excerpt is refused with `HUMAN_TEXT_SOURCE_EXCERPT_REFUSED`. Nothing is truncated
  or silently changed, and no row is written. The message names the policy, not the text.
- If the source text cannot be read, the check cannot be made and any text is refused (`HUMAN_TEXT_SOURCE_CHECK_UNAVAILABLE`).
- Own-words notes and reason codes still work, so REJECT and CORRECT remain possible with reviewer, decision, codes and
  timestamp.

**Read time (A5).** `audit` projects every rationale and reason code under the CURRENT A5 of the draft's source.
- While A5 allows, the text is shown.
- Otherwise it is shown only if it contains no excerpt. If it does, or the source cannot be read, it becomes
  `rationale: null`, `rationale_disclosure: WITHHELD`, `rationale_withheld_reasons` (`GATE_A5_DENIED:<reason>` /
  `GATE_A5_UNDETERMINED:<code>` plus `SOURCE_EXCERPT_DETECTED` / `SOURCE_CHECK_UNAVAILABLE`), `rationale_sha256` and
  `rationale_chars`. A reason code that spells an excerpt becomes `WITHHELD_SOURCE_EXCERPT`.
- Decisions, outcomes, reviewers, timestamps and every stored row stay unchanged (append-only). Text stored while A4 allowed is
  not rewritten; it is only withheld.

**Migration.** None: 0008 is unchanged from v02 (detection happens in the service, which a trigger could not do). Schema-8 KBs of
v02 open unchanged; their stored rationales are projected by the same read rule.

## ES-D13. Out of scope (DEFERRED / owner-gated)

Real models (S10), remote/desktop AI (OAG-6), RAG/search (S09), synthesis/manuscripts (S11), HUMAN_READING evidence, OCR,
table/figure structure, real PDFs, the manual full-text inbox (`04_MANUAL_FULLTEXT_INTAKE_FOLLOWUP`), Windows OS-isolated
parsing of real PDFs (R22).
