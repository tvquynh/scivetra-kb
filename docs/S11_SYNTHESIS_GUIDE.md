# S11 synthesis and citation audit guide (beginner, Windows, synthetic practice data only)

This guide shows how to turn **human-accepted S08 evidence** into a **non-authoritative synthesis draft** in which every claim is either cited to current evidence or visibly flagged.

You do not need an AI account, an API key or a model. You do not need to edit any code.

**Data.** Use only a practice data root under `E:\ResearchPlatformDev\...` built from synthetic data. Never use `E:\ResearchKB` or real research PDFs.

**What the output is not:**
- It is not a publication.
- It cannot be shared outside your computer: sharing needs owner policy OAG-7, which does not exist yet.

## 0. What you need first

- A KB with an **S08 evidence scope** whose drafts were verified and accepted by a human reviewer. See `EVIDENCE_USER_GUIDE.md`.
- A registered human reviewer roster (`rkb screen reviewer-register`). The person who writes the claims cannot review them.

In PowerShell, set a shortcut once:

```powershell
function rkb { py -3.11 -I -m scivetra_kb @args }
$root = 'E:\ResearchPlatformDev\SciVetra\S11_practice\kb'
```

## 1. Create a synthesis scope

```powershell
rkb synth scope-create --data-root $root --evidence-scope-id evs_... --label "practice review" --operator alice --review-policy SINGLE_HUMAN --review-policy-reason "practice: one reviewer"
```

The output shows `status: DRAFT_NON_AUTHORITATIVE` and `publication: PUBLICATION_NOT_AUTHORIZED`.

The command fails if a member is not currently usable (`SYNTHESIS_SCOPE_MEMBER_REFUSED`) or the evidence scope is provisional. Use `--paper-id` (repeatable) to pin only some papers of the evidence scope.

## 2. Write claims as typed specs

A claim is a small JSON file (`scivetra-claim-spec/1`) plus the S08 draft ids it cites. Example, `c1.json`:

```json
{"schema": "scivetra-claim-spec/1", "kind": "REPORTED_VALUE",
 "measurement": {"metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%",
                 "dataset": "EMBER", "dataset_year": "2018", "split": "TEST", "pe_type": "WIN32"}}
```

```powershell
rkb synth claim-add --data-root $root --scope-id sys_... --claim-key C1 --spec-file c1.json --cite evd_... --operator alice
```

Rules to remember:
- **Numbers are text.** Write `"93.1"`, never `93.1` (no quotes) and never `"93,1"`.
- **Units are never guessed.** `0.931` (ratio) is not `93.1%`.
- **Operating point.** Always state it and its unit for TPR: `1%` FPR is not `0.1%` FPR.
- **Statement.** Without `--statement`, a sentence is generated from the spec. Prefer this: only a generated sentence can be SUPPORTED by the machine alone.
- **Your own sentence (`--statement`).** It is stored exactly as you wrote it and checked at every audit. It may not contain numbers, signs, units, datasets, metrics, splits, PE types, time words, "not" or comparison words that the spec does not support (`-93.1%` is not `93.1%`; "training split" is not TEST; PR-AUC is not TPR). Writing "significantly", "causes", "on average" or "outperforms" needs the matching flag or a COMPARISON claim. `claim-add` shows `statement_check_codes` at once; any code means the audit will say UNSUPPORTED: add a new version. A consistent own sentence is PARTIAL until a different registered person confirms that exact sentence (section 4).
- **Your own words.** Do not paste text from the paper: a copied passage is refused where the rights do not allow storing it.

Other kinds:
- `COMPARISON`: two cites, `"relation": "HIGHER"|"LOWER"|"EQUAL"`, and an optional `"difference": {"value": "1.7", "unit": "PERCENTAGE_POINTS"}`.
- `ABSENCE`: `"evidence_class": "UNCERTAINTY_INTERVAL", "status": "NOT_REPORTED"`.
- `THEMATIC`: `"evidence_class": "LIMITATION"`.
- `COUNT`: `"count": "2", "denominator": "<number of pinned papers>", "criterion": {...}`. Every pinned paper is checked: it counts as "no" only with an accepted NOT_REPORTED record. A paper whose evidence is missing, not accepted, stale or blocked is UNKNOWN, and the count is then at most PARTIAL. For a count of 0, cite the accepted NOT_REPORTED records.

To correct a claim, add it again with the same `--claim-key`. This creates version 2; version 1 is kept.

## 3. Audit

```powershell
rkb synth audit --data-root $root --scope-id sys_... --operator bob
```

Every claim gets a verdict:

| Verdict | Marker in the draft | Meaning |
|---|---|---|
| SUPPORTED | `[1]`, `[2]`… | Matches current, human-accepted, anchor-verified evidence in every stated dimension |
| PARTIAL | `[UNVERIFIED]` | Something cannot be confirmed: precision differs, context missing, unknown papers in a count, needs a human judgement (thematic claims, your own sentence) |
| UNSUPPORTED | `[UNVERIFIED]` | The evidence says something else, or the comparison is not valid |
| MISSING_EVIDENCE | `[UNVERIFIED]` | No current, accepted evidence |
| STALE_SOURCE | `[UNVERIFIED]` | The PDF, artifact, anchor or version changed |
| BLOCKED_RIGHTS | `[UNVERIFIED]` | Rights no longer allow using the source |
| CONFLICTING | `[CONFLICTED]` | The same paper reports different values for the same measurement |

The audit re-checks everything NOW. Run it again whenever evidence, rights or claims change.

## 4. Human review

A different, registered person reviews the latest audit:

```powershell
rkb synth review --data-root $root --scope-id sys_... --audit-id sya_... --reviewer carol --decision ATTEST_DRAFT_ACCURATE --reason-code UNVERIFIED_CLAIMS_ACKNOWLEDGED
```

**Draft decisions:**
- `ATTEST_DRAFT_ACCURATE`. If any claim is not SUPPORTED, add `--reason-code UNVERIFIED_CLAIMS_ACKNOWLEDGED`.
- `REQUEST_CHANGES` (needs a reason code).
- `REJECT_DRAFT` (needs a reason code).

**Claim decisions** (thematic claims and your own sentences): `CONFIRM_SUPPORT` and `DISPUTE_SUPPORT`, with `--claim-key`. They count from the next audit. You confirm the exact sentence shown: a new wording needs a new confirmation, and a sentence that fails a check, or is WITHHELD, cannot be confirmed.

**Rights.** If the owner withdraws the right to show stored excerpts (A5), sentences and notes that copy the paper are shown as `WITHHELD_A5` in audits, exports and review history until the right is given back.

`rkb synth status ...` shows the current state. If evidence or rights changed after the review, the state becomes `REVIEW_STALE_AUDIT_NO_LONGER_REPRODUCES`: audit again and review again.

## 5. Export for local review

```powershell
rkb synth export --data-root $root --scope-id sys_... --out-dir E:\ResearchPlatformDev\SciVetra\S11_practice\exports --operator bob
```

The command writes a new folder with four files:
- `draft.md`: readable;
- `evidence_matrix.csv`: one row per claim, cited record and anchor;
- `synthesis.json`: the full audit;
- `MANIFEST.json`: the SHA-256 of each file.

**Rules:**
- The folder must be outside the KB and given as an absolute path.
- The export refuses an outdated audit.
- The files never contain document text or quotes.
- They are **LOCAL_REVIEW_ONLY**.

`rkb synth share ...` always answers `SHARING_NOT_AUTHORIZED`, and the request is recorded.

**Export refused at release.** If rights, evidence or a source file change while an export is being written, the export stops with `EXPORT_RELEASE_REFUSED` and nothing appears in the folder. Run `rkb synth audit …` again, then export again. `EXPORT_RELEASE_FAILED` means the KB was busy or a file error happened: nothing was published, try again.

## 6. If two papers look like the same paper

The audit reports `PAPER_IDENTITY_CONFLICT`, and the affected claims are `PARTIAL`. Resolve the conflict in the library, then audit again (see ADR_S11 SY-D09):

```powershell
rkb library search --data-root $root --doi 10.99999/...
```

```powershell
rkb library resolve-conflict --data-root $root --conflict-id ... --action ... --evidence-ref ... --withdraw-assertion-id ... --operator alice --reason "checked title page"
```
