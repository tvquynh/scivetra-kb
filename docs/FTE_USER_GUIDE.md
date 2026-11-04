# Full-text eligibility (S05) — beginner guide

> **NON_AUTHORITATIVE.** What you do here is a local *staging* of human full-text eligibility review. It is not a
> Reprovane RV-FTE run, not a SEALED corpus release and not an official PRISMA count. Reprovane stays the authority.

Every command prints one JSON object. Exit code 0 = success, 2 = refused (input not acceptable, see `error`),
3 = integrity problem (e.g. a tampered export). All examples use synthetic data. Never point `--data-root` at a
production KB while you are trying things out; make a copy or a fresh `rkb init` folder.

## What you need first

1. A KB at schema 5 (`rkb init --data-root KB`, or `rkb upgrade` on an older KB — it backs up first).
2. ONE Reprovane Stage-11 title/abstract release that Reprovane sealed, exported and that you imported with
   `rkb library import-reprovane`. A journal inventory from S04J (`rkb journal …`) is **not** a source: journal DOIs have
   never been title/abstract screened, so `fte create` refuses them (`JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE`).
3. The release's trusted manifest digest (from your Reprovane records) and the JSON printed by Reprovane's own
   read-only `verify-export` for that export folder, saved to a file.
4. A protocol file and a roster file (below).

## 1. Protocol file (`protocol.json`)

```json
{
  "schema": "fte-protocol/1",
  "label": "My review FTE protocol",
  "criteria": [{"id": "C1", "text": "Population: adults"}, {"id": "C2", "text": "Design: randomized trial"}],
  "reason_vocabulary_version": "my-reasons/1",
  "exclude_reasons": ["WRONG_POPULATION", "WRONG_STUDY_DESIGN", "WRONG_OUTCOME", "DUPLICATE_REPORT_OF_INCLUDED_STUDY", "RETRACTED"],
  "not_retrieved_reasons": ["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS", "NOT_AVAILABLE_FROM_AUTHORS"],
  "consensus_rule": "TWO_INDEPENDENT_REVIEWERS_PLUS_ADJUDICATOR",
  "evidence_policy": {"allowed_kinds": ["KB_ASSET", "RV_STAGE11B_ARTIFACT"],
                      "allowed_version_classes": ["PUBLISHED", "UPDATED_PUBLISHED", "ACCEPTED"],
                      "require_local_process_allow": true},
  "not_retrieved_min_documented_attempts": 2
}
```

Exclusion reasons and not-retrieved reasons are two separate lists: "we could not get the paper" is never an exclusion.

## 2. Roster file (`roster.json`)

```json
[{"label": "rev.a", "role": "REVIEWER", "actor_type": "HUMAN"},
 {"label": "rev.b", "role": "REVIEWER", "actor_type": "HUMAN", "conflicts": ["<corpus_item_id rev.b must not review>"]},
 {"label": "adj.c", "role": "ADJUDICATOR", "actor_type": "HUMAN"}]
```

Only humans. Labels such as `gpt-reviewer`, `ai.helper` or `system` are refused. Labels are self-declared and are not
a login or an e-signature.

## 3. Create the run (PowerShell)

```powershell
rkb fte sources --data-root KB
rkb fte protocol-create --data-root KB --protocol-file protocol.json --operator me
rkb fte create --data-root KB --source <release_import_id> --export-dir RV_EXPORT `
  --expected-manifest-sha256 <trusted digest> --rv-verify-json rv_verify.json `
  --protocol-id <protocol_id> --roster-file roster.json --label "first FTE run" --operator me
rkb fte candidates --data-root KB --run-id <run_id>
```

POSIX shells: the same commands with `\` instead of the backtick for line continuation.

`fte create` freezes the exact item set (every release item becomes a candidate, also those without a PDF), the
protocol hash, the roster and the reviewer assignments. Running the identical command again is a no-op.

## 4. Attach evidence

```powershell
rkb fte attach-evidence --data-root KB --candidate-id <ftc_…> --link-id <S03 paper_asset_link id> --operator me
rkb fte attach-evidence --data-root KB --candidate-id <ftc_…> --rv11b-export-dir RV_FULLTEXT_EXPORT `
  --rv11b-expected-manifest-sha256 <trusted digest> [--rv11b-verify-json rv_ft_verify.json] [--rv11b-expected-artifact-sha256 <sha>] --operator me
rkb fte attach-evidence --data-root KB --candidate-id <ftc_…> --none --operator me
```

A KB PDF counts as evidence only if a human CONFIRMED its link to this paper in S03 (`rkb asset review-link …`), it is
the main text, the protocol allows its version (a preprint is not a published article) and its rights allow local
processing. Otherwise the output lists `blockers`. A "Completed" download or a valid PDF structure is not identity.
An external Reprovane Stage-11B artifact is checked against the actual read-only Stage-11B export folder (its files,
checksums, the trusted manifest digest you supply, this run's release, this candidate's item and its own selected
artifact). It is recorded as `EXTERNAL_HASH_ONLY_NOT_REVERIFIED` and is **never** evidence-ready: Reprovane selected the
file automatically, no human confirmed that it is this paper, SciVetra holds no bytes and no rights were assessed. To
review it, import the same PDF into the KB, confirm its identity (`rkb asset review-link`), assess rights and attach it
with `--link-id`; the output field `kb_asset_links_with_same_bytes` lists such links. Writing your own JSON with
`export_verified: true` or typed digests is not accepted.

If an attached PDF's identity, role or rights change later (for example a rights reassessment to restricted terms),
the candidate becomes PENDING `STALE_EVIDENCE_NOT_CURRENT` at once and its earlier votes no longer count. Attach the
evidence again and both reviewers decide again (with `--supersedes`). After a run is locked, such changes are reported
separately as `post_lock_drift`; the locked snapshot itself never changes.

## 5. Record acquisition attempts (operational, not votes)

```powershell
rkb fte attempt --data-root KB --candidate-id <ftc_…> --status BOT_CHECK --source "publisher site" --attempted-on 2026-10-01 --operator me
```

Statuses: LOGIN_REQUIRED, BOT_CHECK, INSTITUTIONAL_RESTRICTED, NO_ACCESS, RETRY_QUEUED (with `--retry-after`), WRONG_PDF,
NOT_FOUND_ONLINE, OA_LOCATION_FAILED, OTHER. Never type passwords, cookies, tokens, full local paths or URLs with query
strings into `--source` or `--note`; they are refused.

## 6. Human decisions and adjudication

```powershell
rkb fte decide --data-root KB --candidate-id <ftc_…> --reviewer rev.a --outcome FT_INCLUDE --page 3
rkb fte decide --data-root KB --candidate-id <ftc_…> --reviewer rev.b --outcome FT_EXCLUDE --reason WRONG_POPULATION
rkb fte adjudicate --data-root KB --candidate-id <ftc_…> --adjudicator adj.c --outcome FT_INCLUDE --note "population matches C1"
rkb fte decide --data-root KB --candidate-id <ftc_…> --reviewer rev.a --outcome FT_NOT_RETRIEVED --reason NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS
```

- FT_INCLUDE / FT_EXCLUDE need evidence-ready full text. FT_UNSURE needs `--note`.
- FT_NOT_RETRIEVED needs the documented attempts required by the protocol.
- To change your vote use `--supersedes <your earlier decision id>`; repeating the same vote is a no-op.
- If the evidence changes (new bytes, new rights assessment) earlier votes become stale and must be re-done.
- Adjudication is only possible when the two reviewers really disagree (or one is UNSURE).

## 7. Queue, status and summary

```powershell
rkb fte queue --data-root KB --run-id <run_id>
rkb fte status --data-root KB --run-id <run_id>
rkb fte summary --data-root KB --run-id <run_id>
```

The summary always satisfies `ta_candidate_count = ft_include + ft_exclude + ft_not_retrieved + ft_pending`. Retraction or
DOI-echo flags appear in the queue as warnings; nothing is excluded automatically.

## 8. Resolve, lock, export, verify

```powershell
rkb fte resolve --data-root KB --run-id <run_id> --operator me
rkb fte lock --data-root KB --run-id <run_id> --operator me
rkb fte export --data-root KB --run-id <run_id> --out-dir OUT --operator me
rkb fte verify --export-dir OUT/<folder_name>
rkb fte verify --data-root KB --run-id <run_id>
```

The lock is refused while anything is pending or stale. A locked run cannot change; start a new run for more work.
The export contains no PDFs, no notes (only their hashes) and no paths. `verify` exits 3 if any file was edited.
