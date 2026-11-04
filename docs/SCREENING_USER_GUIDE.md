# rkb screen: AI-assisted screening proposals (S07, 0.7.0.dev2) — beginner guide

`rkb screen` prepares **suggestions** for title/abstract (TA_SCREEN) or full-text (FT_SCREEN) screening. In S07 the
"model" is a deterministic **mock** for practice and testing with **synthetic** data only. A suggestion is never a decision:
you still screen in Reprovane / the S05 workflow.

Use a practice folder, for example `E:\ResearchPlatformDev\SciVetra\S07_practice\kb`. Never use `E:\ResearchKB`.

## 0. Shell syntax

PowerShell (recommended):

```powershell
$env:SCIVETRA_PYPDF_SITE = 'E:\ResearchPlatformDev\SciVetra\S07_review\SCIVETRA_S07_AI_ASSISTED_SCREENING_v01\reference\dependencies\pypdf-6.12.1-py3-none-any.whl'
$kb = 'E:\ResearchPlatformDev\SciVetra\S07_practice\kb'
```

cmd.exe: `set SCIVETRA_PYPDF_SITE=...` (no quotes). The parser is only needed for FT_SCREEN.

## 1. Prepare a practice KB

```powershell
rkb init --data-root $kb
rkb library import-reprovane --data-root $kb --export-dir <synthetic release folder> --workspace-label ws --operator you
```

For FT_SCREEN you also need an S05 run and S06 parse artifacts of HUMAN_CONFIRMED main-text PDFs (see `FTE_USER_GUIDE.md`,
`PARSE_USER_GUIDE.md`).

## 2. Criteria and scope

```powershell
rkb screen criteria-create --data-root $kb --task-type TA_SCREEN --criteria-file criteria.json --label "my criteria v1" --operator you
rkb screen scope-create --data-root $kb --scope-file scope.json --operator you
```

`criteria.json` follows `rkb-screening-criteria/1` (ids `C1`.., text, an exclude reason code per criterion; `mock_hint` is used
only by the mock). `scope.json` names `task_type`, `criteria_set_id`, `label`, `content_classes`
(`METADATA_ONLY` / `METADATA_AND_ABSTRACT` for TA; `ANCHOR_ONLY` / `FULLTEXT_EXCERPT` for FT), and either `release_import_id`
+ `paper_ids` (TA) or `fte_run_id` + `ft_members` `[{candidate_id, parse_artifact_id}]` (FT). Only `LOCAL_PROCESS` is allowed.

## 3. Packs, mock suggestions, verification

```powershell
rkb screen build-packs --data-root $kb --scope-id psc_... --operator you
rkb screen run-mock    --data-root $kb --scope-id psc_... --operator runner
rkb screen verify      --data-root $kb --proposal-id aip_... --operator you
rkb screen state       --data-root $kb --proposal-id aip_...
rkb screen show        --data-root $kb --proposal-id aip_...
```

Members that fail rights, identity or scope checks are `SCOPE_DENIED` and get no suggestion. `verify` re-checks every quote
against the stored text (exit 3 when FAILED). `state` says `PASSED`, `PARTIAL` (some judgements without evidence), `FAILED`
or `STALE` (rights, identity, criteria, metadata or parse artifact changed). `show` never shows the suggestion.

An operator may import a response file produced elsewhere (`rkb screen import-proposal --pack-id ... --response-file ...
--model-file ...`). The model identity is recorded as unverified; importing is not permission to send data to any service.

## 4. Human-only actions (blind-first)

```powershell
rkb screen reviewer-register --data-root $kb --label rev.anna --operator owner --reason "owner roster"
rkb screen initial-judgement --data-root $kb --scope-id psc_... --paper-id gp_... --reviewer rev.anna --outcome INCLUDE
rkb screen reveal            --data-root $kb --proposal-id aip_... --reviewer rev.anna
rkb screen approve           --data-root $kb --proposal-id aip_... --reviewer rev.anna --decision ACCEPT
```

You must record your own judgement first; only then can the suggestion be revealed to you. You cannot approve a suggestion you
produced yourself. `ACCEPT` only means "I agree"; it changes nothing in Reprovane or S05.

## 5. Export, evaluation, rollback

```powershell
rkb screen export --data-root $kb --scope-id psc_... --out-dir E:\ResearchPlatformDev\SciVetra\S07_practice\exports --operator you
py -3.11 -I -B tools\s07_evaluation.py --out-dir E:\ResearchPlatformDev\SciVetra\S07_practice\eval
```

The export holds states and hashes only (no suggestions, quotes or text). The evaluation scores the mock on 100 synthetic papers;
the numbers describe the fixtures, not any real model. Rollback of the schema upgrade: replace `db\kb.sqlite` with the
`kb.sqlite.pre-6-…` file, or restore the backup written by `rkb upgrade`.

## What remains human work

Deciding inclusion and exclusion; recording authoritative decisions in Reprovane / S05; confirming paper identity and PDF
version; judging rights; reading the full text; any use of a real model (not approved).
