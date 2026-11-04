# ADR S05 — Full-text Eligibility CANDIDATE workflow (0.5.0.dev2)

Status: v02 corrective revision implemented for architect review (findings S05-F01, S05-F02 of
`SCIVETRA_S05_V02_REVISION_HANDOFF_v01`; FTE-D6 and FTE-D7 amended, FTE-D17 and FTE-D18 added). Binding inputs: handoff `SCIVETRA_S04J_ACCEPT_S05_HANDOFF_v01`
(`01_CLAUDE_S05_EXECUTION_PROMPT.md`, `03_S05_ARCHITECTURE_DECISIONS.md` ADR-05-01..05), S01 v03 architecture contracts.
Baseline: accepted `SCIVETRA_S04J_JOURNAL_HARVESTING_v02.zip` (sha256 `252c2926…0294941`).

## Authority statement

Everything S05 writes is **NON_AUTHORITATIVE**. An S05 run is a KB-side *staging* of human full-text eligibility review.
It is never:

- a Reprovane RV-FTE run (OAG-5 stays CLOSED; no Reprovane code, schema or release is touched);
- a SEALED corpus release;
- an official PRISMA count.

`fte_run.authority` has a `CHECK (authority = 'NON_AUTHORITATIVE')`. Every service return value, summary, queue, CSV row,
JSONL record, export manifest and handoff manifest carries `authority: NON_AUTHORITATIVE` and a disclaimer.

## Decisions

| ID | Decision |
|---|---|
| FTE-D1 | One additive migration `0005_fulltext_eligibility_staging.sql` (schema 5). `0001`–`0004` are byte-identical (hashes asserted by tests). Upgrade is explicit, backup-first, copy-migrate-verify-swap (S03 mechanism, unchanged). Rollback = restore of the verified pre-upgrade backup; there is no down migration. |
| FTE-D2 | The only source of a run is exactly ONE S03-imported Reprovane Stage-11 title/abstract release (`reprovane_release_import`). At creation the operator must supply the export folder, the trusted expected manifest digest and the output of Reprovane's own read-only `verify-export`. The export bytes must equal the bytes recorded at S03 import; the manifest must carry the Stage-10 screening binding (`screening_run_id`, `screening_manifest_sha256`, `project_revision_id`) and `screen_include_count == item count`; the KB project references must equal the export items exactly, in the same workspace. Reprovane 2.0.1 writes a portable export only for a SEALED release (`corpus_release/store.py`: "Portable export requires sealed release"), so an export that verifies natively is SEALED. |
| FTE-D3 | **Journal inventories are never FTE sources** (ADR-05-01). Any `jcv_`/`jj_`/`jcm_` id, or an id present in a `jh_*` table, is refused with `JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE`. There is no journal → TA or journal → FTE importer. The "future journal screening importer" sketched in `S04J_S05_IMPACT.md` is NOT implemented and is NOT authorized by S05. |
| FTE-D4 | Candidates are frozen at creation, in one transaction: one `fte_candidate` per release item (zero dropped, with or without PDF), with `item_sha256`, ordinal, paper and reference. The state goes `OPEN → INPUT_FROZEN` in that transaction; triggers refuse later candidates, roster entries and assignments. |
| FTE-D5 | Human-only roster. `actor_type` must be `HUMAN` (CHECK); labels that look like AI/system/bot actors are refused (`NON_HUMAN_ACTOR_REFUSED`); ≥2 REVIEWERs and ≥1 ADJUDICATOR; one label cannot hold two roles. Labels are self-declared audit labels, not authentication or e-signatures. Deterministic round-robin assigns 2 independent reviewers per candidate, skipping declared conflicts of interest. |
| FTE-D6 | Evidence kinds: `KB_ASSET` (a `paper_asset_link` to the candidate's own paper, `HUMAN_CONFIRMED`, role `MAIN_TEXT`, CAS bytes re-hashed at attach), `RV_STAGE11B_ARTIFACT` (a reference verified against the actual read-only Stage-11B export, `EXTERNAL_HASH_ONLY_NOT_REVERIFIED`, **never evidence-ready**, see FTE-D18), `NONE_NOT_RETRIEVED`. Protocol version and rights rules produce explicit blockers (`VERSION_NOT_ALLOWED_BY_PROTOCOL:<class>`, `RIGHTS_LOCAL_PROCESS_<value>`, `RIGHTS_NOT_ASSESSED`). S03 `VALID_PDF` and PBD `Completed` are structural/operational facts, never identity. |
| FTE-D7 | Every decision and adjudication binds the candidate `item_sha256`, the frozen `protocol_sha256` and the current evidence fingerprint. A new evidence row makes earlier votes stale (`STALE_DECISION_EVIDENCE_CHANGED`); a later change of the attached evidence's identity, role, paper or rights makes the candidate PENDING immediately (FTE-D17); both show as queue `CHANGED_EVIDENCE` and block resolve and lock. A stored protocol whose hash no longer matches is refused (`PROTOCOL_HASH_CHANGED`). |
| FTE-D8 | Outcomes `FT_INCLUDE`, `FT_EXCLUDE`, `FT_UNSURE`, `FT_NOT_RETRIEVED` (votes) and `FT_PENDING` (projection only) are distinct. INCLUDE/EXCLUDE need evidence-ready full text. EXCLUDE and NOT_RETRIEVED reasons come from two disjoint protocol vocabularies. NOT_RETRIEVED needs ≥ `not_retrieved_min_documented_attempts` recorded acquisition attempts and is refused while ready evidence exists. UNSURE needs a note. `DUPLICATE_REPORT_OF_INCLUDED_STUDY` needs a human-entered `related_corpus_item_id`. |
| FTE-D9 | Decisions are append-only. A changed vote needs an explicit `--supersedes`; an exact repeat is `DUPLICATE_NOOP`. Superseded or stale votes are never counted. |
| FTE-D10 | Adjudication only by the roster ADJUDICATOR, only when both current, non-stale reviewer decisions exist and genuinely disagree (or one is UNSURE). No fabricated consensus. |
| FTE-D11 | Acquisition attempts (`LOGIN_REQUIRED`, `BOT_CHECK`, `INSTITUTIONAL_RESTRICTED`, `NO_ACCESS`, `RETRY_QUEUED`, `WRONG_PDF`, …) are operational metadata, never votes. Free text passes `safe_text` (no secrets, query-string URLs, absolute paths or e-mail addresses). |
| FTE-D12 | Queue = projection of flags: MISSING_FULLTEXT, RESTRICTED_RIGHTS, VERSION_MISMATCH, CHANGED_EVIDENCE, EXTERNAL_EVIDENCE_HASH_ONLY, IDENTITY_UNCONFIRMED_ASSET, IDENTITY_MISMATCH, DOI_ECHO_MISMATCH (C20, from `bridge_attempt.observed_json.doi_echo_matches_export = false`), ACQUISITION_BRIDGE_STATUS, RETRYABLE_ACQUISITION, MANUAL_ACQUISITION_NEEDED, ACQUISITION_ATTEMPTED, INTEGRITY_ALERT (S02 integrity events / S04J retraction flags; warning only), JOURNAL_VS_PREPRINT_MISMATCH, UNRESOLVED_REVIEW, NEEDS_ADJUDICATION. Flags are never resolved automatically. |
| FTE-D13 | S04J context is read-only and shown only after a HUMAN_CONFIRMED `KB_ASSET` whose paper has an exact DOI key match; otherwise `NOT_LINKED`. No fuzzy title/DOI joins; no S04J row is written. |
| FTE-D14 | Accounting: `ta_candidate_count = ft_include + ft_exclude + ft_not_retrieved + ft_pending`; per-reason breakdowns; studies vs reports of included studies counted separately (a report counts as a separate study only without a human duplicate link). Status `PROVISIONAL` until locked. |
| FTE-D15 | Lifecycle `OPEN → INPUT_FROZEN → REVIEW_IN_PROGRESS → REVIEW_RESOLVED → LOCKED_FOR_HANDOFF`, each transition an append-only `fte_run_event`. Lock is refused while anything is pending, stale, conflicting or changed. The handoff manifest (`fte-handoff/1`) is stored in CAS and registered as a schema-5 CAS reference `(fte_lock, handoff_manifest_sha256)`, so `doctor --full`, backup verification and restore cover it. After lock, triggers refuse every insert for that run. |
| FTE-D17 | **Evidence currency (v02, S05-F02).** For an UNLOCKED run, `FTEService.evidence_currency` re-derives the attach-time facts of the current `KB_ASSET` evidence from database rows only (no CAS read): effective link state, latest identity review event, role, paper and current rights assessment. Any difference (`IDENTITY_NOW_<state>`, `ROLE_NOW_<role>`, `PAPER_CHANGED`, `RIGHTS_REASSESSED…`, `IDENTITY_REVIEWED_AGAIN_OR_FACTS_CHANGED`) makes the candidate PENDING `STALE_EVIDENCE_NOT_CURRENT` in `candidate_state`, and therefore in `summary`, `queue`, provisional export, `resolve` (refused) and `lock` (refused). New decisions on such evidence are refused (`EVIDENCE_NOT_CURRENT`). Original votes stay unchanged in the journal; FINAL is restored only by re-attaching evidence (new fingerprint, earlier votes stale) and two fresh, explicitly superseding human decisions. The full CAS re-hash stays at attach and at the lock gate. A re-confirmation after a rejection is a new identity event, so it never silently revives old votes. **Locked runs** keep their historical projection: the locked CAS manifest is never rewritten; later drift is reported beside it (`summary.post_lock_drift`, `verify_lock.current_evidence_drift`). |
| FTE-D18 | **External Stage-11B references (v02, S05-F01).** `attach_external` accepts only a reference to an actual read-only RV-Stage11B portable export (`fulltext_export_dir`, trusted `expected_manifest_sha256`, optional native `rv_verify_json` and `expected_artifact_sha256`); any self-asserted key (`export_verified`, typed digests, version or rights classes) is refused `EXTERNAL_SELF_ASSERTION_REFUSED`. The export is verified with the accepted S04 reader (`bridge.sources.read_fulltext_export`: exact five files, safe paths, no links, SHA256SUMS, strict JSON, canonical re-rendering, trusted digest) and then bound to the run: release id and release manifest digest, the candidate's `corpus_item_id` and `publication_entity_id`, status `ACQUIRED_AUTO`, the selected artifact as the item's own ACQUIRED attempt and not claimed by another item, and the expected artifact digest if given. The reference is stored (no paths) with its verification level, what was and was not verified, and provider version/licence claims as claims. It is **never evidence-ready**: Stage-11B selection is automatic (no human identity confirmation, unlike FTE-D6's KB rule), SciVetra holds no bytes, and no local-processing rights are determined. The supported ready path is the same bytes imported as a KB asset, human-confirmed and rights-assessed; the reference lists such links (`kb_asset_links_with_same_bytes`). An evidence-ready external row (possible only from v01 self-assertion) is treated as not current. |
| FTE-D16 | Export `fte-export/1` is deterministic (identical bytes for identical frozen content), contains no notes (only `note_sha256`), no paths, no PDF bytes, no excerpts. `verify_export` checks SHA256SUMS, the exact file set, the manifest, replays every decision/adjudication hash, the outcomes digest and the accounting. `verify_lock` rebuilds the handoff manifest from the KB and compares it with the CAS bytes. |

## Contract mapping (S01 v03 / handoff names → implementation)

| Contract entity | Table / code | Notes |
|---|---|---|
| FTECandidacyRun | `fte_run` (`ftr_`), `fte_run_event` (`fre_`), `FTEService.create_run/state/resolve/lock` | authority CHECK; release/screening digests; item-set, protocol, roster and assignment hashes |
| Protocol | `fte_protocol` (`ftp_`), `eligibility/protocol.py` | schema `fte-protocol/1`, immutable, hashed |
| FTECandidate | `fte_candidate` (`ftc_`) | one per frozen `corpus_item_id` |
| FTEEvidenceRef | `fte_evidence` (`fev_`) | KB_ASSET / RV_STAGE11B_ARTIFACT / NONE_NOT_RETRIEVED |
| FTEReviewerRoster + assignments | `fte_reviewer` (`ftv_`), `fte_assignment` (`fta_`) | frozen at INPUT_FROZEN |
| FTEReviewDecision | `fte_decision` (`ftd_`) | append-only, supersession explicit |
| FTEAdjudication | `fte_adjudication` (`ftj_`) | resolves exactly two decisions |
| Acquisition attempt | `fte_acquisition_attempt` (`ftq_`) | operational only |
| FTEQueueItem | `FTEService.queue` (projection) | not stored |
| FTESummary | `FTEService.summary` (projection) | invariant reported |
| FTEHandoffManifest | `fte_lock` (`ftl_`) + CAS object | `eligibility/handoff.py` |

## Migration

`src/scivetra_kb/core/migrations/0005_fulltext_eligibility_staging.sql` creates only new `fte_*` tables and triggers. It
references existing tables (`workspace_registration`, `reprovane_release_import`, `project_paper_reference`,
`global_paper`, `pdf_asset`, `paper_asset_link`, `document_version`, `rights_assessment`) by foreign key and modifies none.
