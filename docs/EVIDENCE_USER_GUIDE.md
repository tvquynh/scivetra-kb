# rkb evidence: scientific evidence extraction (S08, 0.8.0.dev3) — beginner guide

`rkb evidence` turns the text of full-text PDFs that humans already screened (S05) and that the KB parsed (S06) into **typed
evidence drafts**: for example "TPR = 93.1 % at 1 % FPR on the EMBER 2018 test split", each with the exact page, character
offsets and SHA-256 of the sentence it came from. In S08 the "extractor" is a deterministic **mock** for practice and testing
with **synthetic** documents only. A draft is never accepted evidence until a human reviewer accepts it, and even accepted
evidence is not an authoritative corpus or a manuscript claim.

Use a practice folder, for example `E:\ResearchPlatformDev\SciVetra\S08_practice\kb`. Never use `E:\ResearchKB`.

## 0. Shell syntax

PowerShell (recommended):

```powershell
$env:SCIVETRA_PYPDF_SITE = 'E:\ResearchPlatformDev\SciVetra\S08_review\SCIVETRA_S08_SCIENTIFIC_EVIDENCE_EXTRACTION_v01\reference\dependencies\pypdf-6.12.1-py3-none-any.whl'
$kb = 'E:\ResearchPlatformDev\SciVetra\S08_practice\kb'
```

cmd.exe: `set SCIVETRA_PYPDF_SITE=...` (no quotes). The parser is needed to produce the S06 artifacts.

## 1. What must exist first

1. An S05 full-text eligibility run (`FTE_USER_GUIDE.md`) whose candidates have a HUMAN_CONFIRMED main-text PDF attached and two
   concordant human decisions `FT_INCLUDE` (or an adjudication).
2. An S06 `RESEARCH_PARSE` artifact of exactly that PDF (`PARSE_USER_GUIDE.md`).
3. Reviewers in the owner-registered human roster (shared with S07):

```powershell
rkb screen reviewer-register --data-root $kb --label rev.anna --operator owner --reason "owner roster"
```

## 2. Profile and scope

```powershell
rkb evidence profile-register --data-root $kb --builtin rkb-malware-detection --operator you
rkb evidence scope-create     --data-root $kb --scope-file scope.json --operator you
```

Built-in profiles: `rkb-general-empirical` (any empirical study) and `rkb-malware-detection` (a sample cybersecurity profile).
For another research field write your own `rkb-evidence-profile/1` file and use `--profile-file`.

`scope.json`:

```json
{"fte_run_id": "ftr_...", "profile_id": "evp_...", "label": "my extraction v1",
 "members": [{"candidate_id": "ftc_...", "parse_artifact_id": "pza_..."}]}
```

Optional keys: `"review_policy": "SINGLE_HUMAN"` with `"review_policy_reason"` (default is two independent reviewers),
`"eligibility_mode": "PROVISIONAL_NON_AUTHORITATIVE"` with `"provisional_reason"` (allows candidates whose S05 outcome is not
final; such evidence can never be accepted). A member is refused if it is not FT_INCLUDE, if the artifact belongs to another
paper or another version, if the same PDF bytes are linked to another paper/version, or if rights do not allow local processing.

## 3. Packs, mock extraction, verification

```powershell
rkb evidence build-packs --data-root $kb --scope-id evs_... --operator you
rkb evidence run-mock    --data-root $kb --scope-id evs_... --operator runner
rkb evidence verify      --data-root $kb --draft-id evd_... --operator you
rkb evidence state       --data-root $kb --draft-id evd_...
rkb evidence show        --data-root $kb --draft-id evd_...
rkb evidence summary     --data-root $kb --scope-id evs_...
```

`verify` checks the page, offsets and SHA-256 of every quote against the stored text and that every number, unit, dataset,
year, split and metric in the draft literally appears in the quote (exit 3 when not `STRUCTURALLY_VERIFIED` /
`ABSENCE_RECORDED`). States you will see:

| State | Meaning |
|---|---|
| `STRUCTURALLY_VERIFIED` | quotes and values match the text. It does **not** prove the interpretation is right |
| `ABSENCE_RECORDED` | the draft says "not reported"; a human must confirm |
| `PARTIAL` | the quote exists but at other offsets; a suggested anchor is shown, nothing is changed |
| `UNSUPPORTED` | a quote, page, number, unit or condition is not in the text |
| `AMBIGUOUS` | the value could come only from an ambiguous number such as `1,5` |
| `STALE` / `BLOCKED` | the source, version, rights, identity, S05 outcome or profile changed; history is kept |

`show` (and `audit`) never shows the quote or a verbatim text field value (it lists the text field names; use `reveal`). An operator may import a response file produced elsewhere
(`rkb evidence import --pack-id ... --response-file ... --model-file ...`); the model identity is recorded as unverified, and
importing is not permission to send any data to any service.

## 4. Human review

```powershell
rkb evidence reveal     --data-root $kb --draft-id evd_... --reviewer rev.anna
rkb evidence review     --data-root $kb --draft-id evd_... --reviewer rev.anna --decision ACCEPT
rkb evidence review     --data-root $kb --draft-id evd_... --reviewer rev.ben --decision REJECT --reason-code CONDITION_MISREAD --rationale "..."
rkb evidence review     --data-root $kb --draft-id evd_... --reviewer rev.ben --decision CORRECT --reason-code WRONG_VALUE --rationale "..." --correction-file fix.json
rkb evidence adjudicate --data-root $kb --draft-id evd_... --adjudicator rev.cara --outcome ACCEPT --rationale "..."
rkb evidence audit      --data-root $kb --draft-id evd_...
```

When the document's licence does not allow storing excerpts, do not paste sentences from the paper into `--rationale`
(or spell them as a reason code): the command is refused with `HUMAN_TEXT_SOURCE_EXCERPT_REFUSED`. Write in your own words
instead. If the licence is later withdrawn, `audit` hides earlier rationales that quote the paper (`WITHHELD`); your decision,
reason codes and the time stay visible.

`reveal` shows you the claim, the anchors and the quote (only while rights allow; every reveal is logged). Reveal again after
a new verification. You cannot review a draft you extracted, imported or corrected. A correction becomes a new draft that
another reviewer must check. When two reviewers disagree, an independent adjudicator decides.

## 5. Export

```powershell
rkb evidence export --data-root $kb --scope-id evs_... --out-dir E:\ResearchPlatformDev\SciVetra\S08_practice\exports --operator you
rkb evidence export --data-root $kb --scope-id evs_... --out-dir ... --operator you --content-level ACCEPTED_TYPED_VALUES
```

The export never contains quotes or document text. The second form adds typed values of accepted drafts only.

Every export is logged before the file appears (`RESERVED`), then closed as `PUBLISHED` or `ABANDONED`. If anything fails, no
unlogged report is left behind; run the same export again. To see what happened:

```
rkb evidence exports --data-root $kb --scope-id evs_...
```

`UNCONFIRMED` means the program stopped between writing the report and logging it; the next identical export confirms it.

## What remains human work

- Deciding whether a quote really supports the claim (the verifier checks structure, not meaning).
- Accepting, rejecting or correcting every draft; resolving disagreements.
- S05 eligibility decisions and Reprovane decisions (S08 never changes them).
- Reading tables, figures and scanned pages (S08 cannot extract them; such records are refused).
- Deciding whether any evidence may be shared or used for synthesis (later stages, owner-gated).
