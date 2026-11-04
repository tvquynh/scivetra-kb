# Journal DOI Harvesting: user guide (S04J, offline candidate)

This guide explains, step by step, how to build a journal inventory with `rkb`. **In S04J, everything runs offline against a
simulated provider.** No real Crossref or OpenAlex request is ever made: `--live` is refused with `LIVE_ACCESS_NOT_AUTHORIZED`.
A real pilot (for example JISA) needs a separate, written owner authorization.

## What you get, and what you do NOT get

You get a **versioned, provenance-preserving list of the works** that two metadata providers associate with one journal:

- Crossref: every record returned by `/journals/{ISSN}/works`.
- OpenAlex: every work whose *venue of publication* (`primary_location.source.id`) is the journal's OpenAlex Source.

Each work is keyed by its DOI, using the same DOI rules as the rest of SciVetra. Each value in the inventory says which
provider, run, page and JSON field it came from. Disagreements between providers are kept side by side and turned into
review tasks.

You do **not** get any of the following:

- a screened SLR set, an inclusion or exclusion decision, or a PRISMA number;
- a Reprovane corpus member;
- an identity-confirmed PDF, or permission to download anything.

Every exported row says `screening_status = NOT_SCREENED` and `inventory_scope = DISCOVERY_EVIDENCE_ONLY`.

"COMPLETE" always means *complete relative to the recorded provider queries*: each crawl reached its documented end.
It never means "every paper the journal ever published".

## Words used below

| Word | Meaning |
|---|---|
| run | one crawl of one provider for one journal (all pages of one query) |
| page | one provider response; its raw bytes are kept in the KB content store |
| observation | one work record exactly as one provider returned it in one run |
| collection version | the reconciliation of the latest runs: the list of works plus coverage numbers |
| subject | one row of the collection: a DOI, or one provider record that has no usable DOI |

## The beginner path (copy-paste, about 1 minute)

Use a new empty folder for the KB. The simulated scenario folder is `tests/fixtures/journal_sim/jisa_shaped` in the source
tree. Its data is synthetic and shaped like JISA (print ISSN 2214-2126, electronic ISSN 2214-2134).

```bash
rkb init --data-root ./kb
```

```bash
rkb journal register --data-root ./kb --label "JISA-shaped synthetic" --operator novice --issn 2214-2126 --issn 2214-2134 --owner-count 2151 --owner-count-observed-on 2026-11-01 --owner-count-note "Owner-observed Crossref rows=0 total-results; historical only"
```

The command prints a `journal_id` (for example `jj_...`). Use it below.

The owner count `2151` is stored as **history only**. It is never compared with the inventory. Provider counts change
over time.

```bash
rkb journal resolve --data-root ./kb --journal-id <journal_id> --operator novice --transport-sim tests/fixtures/journal_sim/jisa_shaped
```

`resolve` looks both ISSNs up at Crossref (journal record, print/electronic types) and at OpenAlex (Source IDs).

**Several ISSNs (v02).** The Crossref harvest builds a query plan with every requested ISSN that Crossref resolved, plus
every print/electronic sibling ISSN listed by a resolved Crossref journal record, and queries each ISSN separately in one
run. A DOI returned by both queries is one work, listed with both ISSNs (`crossref_query_issns`). The Crossref inventory
is COMPLETE only if every planned ISSN query finished; if one fails, the run is PARTIAL and names the ISSN and the reason
(for example `ISSN_QUERY_BLOCKED:2214-2134:HTTP_404_NOT_FOUND`). `journal status` and the coverage report show a
per-ISSN table. A source
counts only if it lists the requested ISSN; titles and DOI prefixes are never used to decide. If two OpenAlex sources
share the ISSN, nothing is selected. A review task is created, and you choose one with
`--openalex-source-id S...` (it must be one of the candidates).

Crossref crawl, with a simulated crash after 5 pages (the `interrupt_once` profile):

```bash
rkb journal harvest --data-root ./kb --journal-id <journal_id> --operator novice --provider crossref --page-size 200 --transport-sim tests/fixtures/journal_sim/jisa_shaped --sim-profile interrupt_once
```

The command exits with code 4 and `"error": "SIMULATED_INTERRUPTION"`, plus a `run_id`. The pages committed before the
crash are kept. Check the run:

```bash
rkb journal status --data-root ./kb --run-id <run_id>
```

It shows `"state": "UNFINISHED"`. An unfinished run is never treated as complete. With several ISSNs, `resume` continues only
the ISSN queries that had not finished; queries that already reached their end are not sent again. Resume it after the "restart" (the
`after_restart` profile makes every saved cursor invalid, as a real provider may):

```bash
rkb journal resume --data-root ./kb --run-id <run_id> --operator novice --transport-sim tests/fixtures/journal_sim/jisa_shaped --sim-profile after_restart
```

The provider rejects the old cursor (HTTP 400). The run records `CURSOR_INVALID` and replays the same query from the
start, skipping records it already stored. It ends `COMPLETE` with no duplicates.

OpenAlex crawls. OpenAlex requires a (free) API key to follow a cursor past the first page. In the simulation the test
key below is accepted. Set it in the environment; it is never written to the KB or to any file:

```bash
export SCIVETRA_OPENALEX_API_KEY=SYNTHETIC-OA-TEST-KEY-not-a-real-credential
```

```bash
rkb journal harvest --data-root ./kb --journal-id <journal_id> --operator novice --provider openalex --transport-sim tests/fixtures/journal_sim/jisa_shaped
```

```bash
rkb journal harvest --data-root ./kb --journal-id <journal_id> --operator novice --provider openalex-alternate --transport-sim tests/fixtures/journal_sim/jisa_shaped
```

Without the key, the first page is kept, the deep-paging request is **not sent**, and the run is
`BLOCKED / API_KEY_REQUIRED_FOR_DEEP_PAGING`. A missing key is never reported as "no more works".

`openalex-alternate` finds works that list the journal only as an *alternate location* (for example a repository copy).
They are evidence of a copy, **not** of journal membership.

Reconcile, export and read the coverage report:

```bash
rkb journal reconcile --data-root ./kb --journal-id <journal_id> --operator novice
```

```bash
rkb journal export --data-root ./kb --version-id <version_id> --out-dir ./journal_exports --operator novice
```

```bash
rkb journal coverage --data-root ./kb --version-id <version_id>
```

Back up, verify, restore and check:

```bash
rkb backup --data-root ./kb --target ./backups
```

```bash
rkb verify-backup --backup ./backups/<backup folder>
```

```bash
rkb restore --backup ./backups/<backup folder> --data-root ./kb_restored
```

```bash
rkb doctor --data-root ./kb_restored --full
```

The same sequence is automated in `tools/s04j_cli_smoke.py`, which writes a full transcript, and in the test
`tests/test_journal_batch_cli.py`.

## Several journals at once

`rkb journal harvest-batch --batch-file batch.json ...` takes a JSON list of
`{"label": ..., "issns": [...], "openalex_source_id": optional}`. Each journal is handled on its own. An invalid ISSN,
an unresolved source or a failed provider affects only that journal's result (`REFUSED`, `BLOCKED` or `PARTIAL`).

## Exported files (`journal_export_v<no>_<hash>/`)

| File | Content |
|---|---|
| `journal_works.csv` | one row per subject: DOI, category, projected fields, flags, OA dry-run status, run IDs (UTF-8, no BOM) |
| `journal_works.jsonl` | the same subjects with full projection, integrity signals, history and evidence observation IDs |
| `field_provenance.jsonl` | for each subject and field: provider, observation, run, page, JSON pointer, time |
| `conflicts.jsonl` | every disagreement, with all values and their sources |
| `records_without_usable_doi.csv` | works without DOI, or with an ambiguous/invalid DOI (kept, never dropped) |
| `review_tasks.csv` | what a person should look at |
| `oa_candidate_queue_dryrun.csv` | open-access PDF candidates that pass evidence + rights + URL checks; `pdf_identity=UNVERIFIED`, `download_authorized=NO` |
| `coverage.json` | provider counts (as snapshots), fetched rows, unique DOIs, set differences, conflicts, states, limitations |
| `export_manifest.json`, `SHA256SUMS.txt` | integrity of the export |

Exporting the same version again produces identical bytes. Spreadsheet formulas in provider text are neutralised with a
leading `'`.

## Categories you will see

| Category | Meaning |
|---|---|
| `BOTH` | Crossref lists it for the journal and OpenAlex gives the journal as venue |
| `CROSSREF_ONLY` / `OPENALEX_ONLY` | only one provider lists it (both kept, for review) |
| `OPENALEX_ALTERNATE_LOCATION_ONLY` | the journal appears only as an alternate location: **not** a member |
| `NOT_SEEN_IN_LATEST_RUNS` | seen before, missing now: kept and flagged, never deleted |
| `NO_DOI`, `DOI_AMBIGUOUS`, `DOI_INVALID` | provider records without a usable DOI: kept, never merged |

## Incremental refresh

`--mode incremental --since YYYY-MM-DD` asks Crossref for `from-update-date` (and OpenAlex for `from_updated_date`).
Incremental runs only *update* values. They never imply that a work disappeared. Disappearance is detected only by a
later **full** run, and then the work is flagged, not deleted.

## Credentials and politeness (for the future live pilot)

- **Credentials:** only from the environment (`SCIVETRA_OPENALEX_API_KEY`; Crossref contact `SCIVETRA_CROSSREF_MAILTO`).
  They are held in a redacting `Secret` object and recorded only as "configured: yes/no". The KB refuses to store any
  request identity containing `@`, `mailto` or `api_key`.
- **Retries:** bounded at 4 attempts with exponential backoff, jitter and `Retry-After` (capped at 60 s). Three
  consecutive 429 responses pause the provider. Request and byte budgets, page caps and cancellation are supported.
