# ADR S04J: Journal DOI Harvesting (implements architect ADR-JH-001)

Status: implemented as an **offline candidate** (0.4.1.dev1). Live harvesting, real data and publication are out of scope.

## Interface map and reuse plan (audited before implementation)

| Need | Accepted implementation reused (file :: symbol) | How S04J uses it |
|---|---|---|
| DOI parse / equality | `metadata/doi.py :: parse_doi`, `ascii_key`, `url_form` (S02 v03) | every provider DOI is parsed with `DOI_IDENTIFIER_FIELD` + `STRUCTURED_PROVIDER_FIELD`; the subject key is `comparison_key`. No `strip()`, no `casefold()` on identity |
| Near-duplicate keys | `metadata/store.py :: zs_rstrip` + NFC comparison (same rule as `check_near_duplicates`) | flags + review tasks, never merges |
| Crossref record shape + claims | `metadata/crossref.py :: validate_message`, `decompose`, `_update_events` | applied per list item; JSON pointers rebased to `/message/items/<i>/...` |
| Untrusted JSON | `metadata/strict_json.py :: decode`; `metadata/shape.py` | all envelopes and OpenAlex records |
| Retries, budgets, request journal | `metadata/transport.py :: Executor`, `RunBudget`, `GuardedTransport`; S02 `fetch_run`/`request_reservation`/`request_completion` tables | unchanged retry semantics (4 attempts, backoff, jitter, Retry-After <= 60 s, 3x429 pause, 403 stop); one additive change: `GuardedTransport(..., allowed_hosts=...)` / `validate_url(url, allowed_hosts)` with the S02 allowlist as default |
| Live access refusal | `metadata/transport.py :: LiveTransportNotAuthorized` | `rkb journal ... --live` |
| Raw bytes | `core/cas.py :: ContentStore`; `CAS_REFERENCE_COLUMNS` (+ schema-4 `jh_page.response_bytes_sha256`) | doctor / backup / verify / restore cover raw pages |
| Migrations, upgrade, backup | `core/migrations.py`, `core/upgrade.py`, `core/backup.py` | `0004` is applied by the existing backup-first upgrade path; `domain_summary` adds the journal manifest for schema >= 4 |
| Library identity (S03) | `identity_assertion_effective` view | read-only lookup: existing GlobalPapers with the same DOI are listed as `paper_candidates`; nothing is minted or linked |
| Rights (S03) | `rights/policy.py :: classify`, `licence_family`, `LOCATION_VERSION_CLASS` | OA dry-run classification from Crossref licence + OpenAlex location evidence |
| URL safety (S04) | `bridge/policy.py :: url_refusal`; `bridge/service.py :: guard_path_text`, `path_hash` | OA candidate URLs; `--out-dir` path guard |
| Operator labels | `library/common.py :: operator_label` | all `created_by` columns |

The S02 `provider_snapshot`, `metadata_claim`, `integrity_event` and `review_task` tables were **not** reused for journal data.
Their CHECK constraints (in accepted migration 0001) pin provider names, per-DOI request identities, reporter names and
review triggers that a list-oriented, two-provider journal crawl cannot satisfy (for example, no `OPENALEX` provider value
and no page-level identity). Changing them would mean editing 0001. Journal evidence therefore lives in additive `jh_*`
tables with the same append-only, hash-and-CAS conventions. The S02 parsing logic is reused, so there is still exactly
one DOI resolver and one Crossref decomposition.

## Decisions

| ID | Decision |
|---|---|
| JH-D1 | **Migration `0004_journal_harvesting.sql`** (14 `jh_*` tables, all append-only with no-update/no-delete triggers). It is warranted because no accepted table can hold page/run/cursor evidence, OpenAlex observations or collection versions. Applied only by `init` (new KB) or the existing backup-first `rkb upgrade`. Accepted `0001`-`0003` are byte-identical. S05 must start at **0005**. |
| JH-D2 | **Checkpoint = one page transaction.** The page row, its new observations and their field claims commit together. A crash loses at most the page in flight. |
| JH-D3 | **Resume never trusts an old cursor.** Resume first tries the last committed `next-cursor`. If the provider rejects it (HTTP 400 on a continuation request), the run replays the *same recorded query* from `cursor=*` in a new pass, de-duplicating by content (run, provider, record id, item hash, venue). Replays are bounded (`max_replays`, default 2), then the run is PARTIAL. Time-window partitioning is not implemented (OPEN_ISSUES D3). |
| JH-D4 | **Termination.** Crossref: stop when a page returns fewer than `rows` items (an empty page included). OpenAlex: stop when `meta.next_cursor` is null or a page is empty. A full page without a next cursor is PARTIAL (`NEXT_CURSOR_MISSING`). |
| JH-D5 | **States.** Run: COMPLETE only after the documented termination; any failed page, cap, budget, cancellation, replay limit or unparseable page is PARTIAL; HTTP 401/403/404, missing key for deep paging, or an unresolved or ambiguous source is BLOCKED; no FINISHED event means UNFINISHED. Collection: `COMPLETE_RELATIVE_TO_RECORDED_QUERIES` only if the Crossref and OpenAlex base runs, any incremental runs and the optional alternate run are all COMPLETE; `BLOCKED` if both base providers are BLOCKED or NOT_RUN; otherwise `PARTIAL`. A CHECK constraint forbids the text `ALL_JOURNAL_ARTICLES_FOUND`. |
| JH-D6 | **Counts are observations.** `total-results` / `meta.count` are stored per probe, per first page of each pass, and whenever they change (`COUNT_DRIFT`), with a pinned scope `SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION`. The owner's JISA 2151 is stored as `OWNER_REPORTED` history. No code compares any count with the inventory. |
| JH-D7 | **Membership = venue evidence.** Crossref journal endpoint, or OpenAlex `primary_location.source.id`. `locations.source.id` is a separate crawl (`OPENALEX_ALTERNATE`) whose matches are copy evidence (`membership_asserted = 0`). Title or DOI prefix is never membership evidence. |
| JH-D8 | **Source resolution** by exact ISSN (or ISSN-L) present in the provider record. Several candidates: none is selected, a SOURCE_AMBIGUOUS task is raised, and the operator may select one *among the candidates* (recorded as `OPERATOR_SELECTED_AMONG_CANDIDATES`). ISSN roles (print/electronic/linking) are stored only as provider evidence; nothing is inferred locally. |
| JH-D9 | **Projection `jh-projection/1`.** Crossref first for bibliographic fields; OpenAlex for OA/location fields and as fallback. Publication year: Crossref published-online > published-print > issued, else OpenAlex `publication_year`, with the basis recorded. Every projected field has a provenance reference. Conflicts (title, year, work type via `jh-type-compat/1`, venue, ISSN) keep all values and raise tasks. |
| JH-D10 | **History.** Every observation is kept. The projection uses the latest observation per provider. Value changes between successive observations of the same record are listed in `projection.history`. An integrity signal ever observed stays in `integrity_signals` (`retraction_reported_ever`). If it is missing from the latest snapshot, the record is flagged and a task is raised; it is never a counter-retraction. |
| JH-D11 | **No merge.** Two OpenAlex works with one DOI stay two work IDs. A Crossref `has-preprint` (or other version relation) gives only a VERSION_LINK_CANDIDATE task. A preprint DOI is its own subject. |
| JH-D12 | **No library writes.** S04J never creates GlobalPapers, identity assertions, project references, rights assessments, assets or bridge batches. Existing papers with the same DOI are listed read-only. |
| JH-D13 | **OA dry run `jh-oa-dryrun/1`.** A candidate needs an OpenAlex OA location with a PDF URL, Crossref licence evidence that `rights-policy/1` classifies PUBLIC_OA_LICENSED for that version, a URL accepted by the S04 guard, and no retraction ever reported. Candidates carry `pdf_identity = UNVERIFIED` and `download_authorized = NO`. They are never sent to PaperBatchDownloader: the S04 bridge accepts only Reprovane sources (0003 CHECK `source_kind`), and a journal source kind would need a later migration and owner approval. |
| JH-D14 | **Simulator as the only transport.** `journal/simulator.py` implements the S02 `Transport` interface for the two journal endpoints, with injectable faults. It is a development/testing transport, like S02's `FixtureTransport`. |
| JH-D15 | **Abstracts** in Crossref records are not decomposed into journal claims (they remain only inside the local raw page bytes, which are never exported). |

### v02 (S04J-F01)

| ID | Decision |
|---|---|
| JH-D16 | **Crossref ISSN query plan.** The plan is the sorted union of (a) requested ISSNs that the latest Crossref journal lookup resolved and (b) ISSNs listed by a resolved Crossref journal record (print/electronic siblings). Each entry carries its evidence (`REQUESTED_AND_RESOLVED` / `LISTED_BY_RESOLVED_JOURNAL_RECORD`, journal record, lookup page). Requested ISSNs in neither set are recorded and prevent COMPLETE. Titles and DOI prefixes are never used. |
| JH-D17 | **One run, one branch per ISSN.** Every planned ISSN gets its own count probe, cursor chain, pass number, replay budget and failure record within the same `jh_run`. Branch state is derived from existing append-only rows: page identities carry `issn`; `PAGE_FAILED` / `CURSOR_INVALID` / `REPLAY_STARTED` / `COUNT_DRIFT` events carry `issn` in their detail. **No schema change**: migration `0004` is byte-identical to v01. Resume skips terminated queries, keeps failed ones failed, and continues the others from their own checkpoint. A 403 stops the run; caps, budgets and cancellation stop the run and leave the remaining ISSNs unfinished (PARTIAL). |
| JH-D18 | **Per-ISSN provenance with one subject per DOI.** Observations are de-duplicated per (run, ISSN query, record, content). Rows are counted per query; unique DOI keys are the union. Reconciliation uses the latest Crossref observation for the projection, lists every ISSN query that returned the DOI, and flags differing content between ISSN queries (`CROSSREF_ISSN_QUERIES_DISAGREE`). |

## Deviations / interpretations (please confirm)

| # | Item | Interpretation |
|---|---|---|
| JH-I1 | "Store print/eISSN and ISSN-L only when evidenced" | Crossref `issn-type` gives PRINT/ELECTRONIC rows; OpenAlex `issn_l` gives LINKING rows; plain `ISSN` lists give UNSPECIFIED rows. All cite page + pointer. |
| JH-I2 | Crossref cursor invalidation signal | The exact live response for an expired Crossref/OpenAlex cursor is **UNVERIFIED**. S04J treats any HTTP 400 on a continuation request as "cursor invalid" (conservative: replay, never skip). |
| JH-I3 | OpenAlex key requirement | Per the handoff (OpenAlex docs 2026-10-14): first page without a key, deeper cursor pages require a key. S04J does not send deep-page requests without a key (BLOCKED); with a rejected key it records HTTP 401 (BLOCKED). The live 401 body shape is UNVERIFIED. |
| JH-I4 | OpenAlex incremental filter | `from_updated_date` is used for OpenAlex incremental runs; whether it needs a key or a premium tier live is **UNVERIFIED**. Incremental runs never drive deletion. |
| JH-I5 | Crossref `from-update-date` | Used for Crossref incremental runs (documented filter). The "update" semantics (metadata deposit vs indexing) are the provider's. |
| JH-I7 | Sibling ISSNs (v02) | An ISSN listed only by a resolved Crossref journal record is crawled even if the operator did not request it, because leaving it out could silently miss works (the S04J-F01 risk). Its evidence is the listing. |
| JH-I6 | Work-type policy | The inventory keeps all types. Filtering is explicit at export time (`--work-type`) and recorded in the export manifest. |
