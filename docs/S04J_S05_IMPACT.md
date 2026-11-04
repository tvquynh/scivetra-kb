# S04J -> S05 hand-off impact

## Migration numbering (binding for the re-issued S05 handoff)

- S04J adds `0004_journal_harvesting.sql` (SHA-256 in `01_IMPLEMENTATION_MANIFEST.md`). Accepted `0001`-`0003` are unchanged.
- The postponed S05 must therefore add **`0005_fulltext_eligibility_staging.sql`**, not the `0004` name used in the old S05
  prompt. An S05 KB starts at schema 5. The existing backup-first `rkb upgrade` path applies `0004` then `0005` in one
  verified copy-migrate-swap, and the tests that pin the schema literal move from 4 to 5 (documented as before).

## Boundary between a journal collection and S05 eligibility

- A journal collection version is **never a screened candidate set**. Each subject is discovery evidence with
  `screening_status = NOT_SCREENED`. S04J has no column, value or command that expresses inclusion, exclusion,
  eligibility, screening, PRISMA or corpus membership (tested: S04J-NO-SLR).
- Reprovane stays the authority for sealed title/abstract corpora. A journal collection is not a substitute for a frozen
  Stage-11 release, and S04J never writes to Reprovane, its schema 13 or any release (tested: S04J-BOUNDARY-01).
- S05 may consume a journal collection only through an **explicit, auditable import policy**. Recommended contract:
  1. The input is a named `jh_collection_version` (`version_id` + `members_sha256`), never "the latest".
  2. The operator selects a subset by a recorded rule (for example work types, a year range, membership categories);
     the rule and the resulting subject list are hashed and stored.
  3. Each imported subject becomes an S05 *candidate for human screening*. It is never pre-included. PRISMA counts
     report the journal route separately from the Reprovane route.
  4. `OPENALEX_ALTERNATE_LOCATION_ONLY`, `NOT_SEEN_IN_LATEST_RUNS` and records without a usable DOI are excluded by
     default and listed in the import report.
  5. Any subject with `retraction_reported_ever` or an open S04J review task is imported with that flag visible.
- The OA dry-run queue (`oa_candidate_queue_dryrun.csv`) is not a download authorization. Feeding it to the S04 bridge
  needs a new bridge source kind (a later migration) and owner approval. PDF identity stays UNVERIFIED until a person
  confirms it.

## Items S05 should pick up

- **C20 (from S04):** a DOI-echo mismatch should appear in S05's pending review queue.
- **S04J review tasks** (`jh_review_task`): S05 should show them next to the candidate, not resolve them silently.
- **Identity:** existing GlobalPapers with the same DOI are listed in `paper_candidates`. S05 must decide (with a human)
  whether a journal subject is the same manifestation; S04J never decides.
