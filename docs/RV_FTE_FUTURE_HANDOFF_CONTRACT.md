# RV-FTE future handoff contract — proposal only

**Status: proposal only. Documentation, no code.** OAG-5 (authoritative Reprovane changes) stays **CLOSED**. Nothing in
S05 calls, writes to, or changes Reprovane, its schema 13, its Stage-11 releases or its human screening decisions. This
page describes what a *future*, separately authorized Reprovane-side adapter could consume, so the S05 staging format can
be reviewed against it now.

## What S05 hands over

A locked S05 run produces `fte-handoff/1` (stored in the KB CAS and inside every locked export):

| Field | Meaning |
|---|---|
| `schema`, `authority`, `disclaimer`, `not` | `fte-handoff/1`; always `NON_AUTHORITATIVE`; the list of things this is NOT (RV-FTE run, SEALED corpus release, official PRISMA count, authenticated reviewer identity) |
| `run.release_id`, `run.release_manifest_sha256` | the exact Reprovane Stage-11 TA release the run froze |
| `run.screening_run_id`, `run.screening_manifest_sha256`, `run.project_revision_id` | the Stage-10 binding read from that release |
| `run.item_set_sha256`, `run.item_count` | digest and size of the frozen `corpus_item_id` set |
| `run.protocol_sha256`, `protocol` | the frozen protocol (criteria, reason vocabularies and version) |
| `run.roster_sha256`, `run.assignment_sha256`, `roster` | the human roster and the reviewer assignment digest |
| `source_verification` | what was checked at run creation (native verify, export bytes, Stage-10 binding) |
| `candidates[]` | per candidate: `corpus_item_id`, `item_sha256`, outcome, basis (CONSENSUS / ADJUDICATION), reasons, current decision ids, adjudication id, evidence summary and fingerprint |
| `decision_journal[]`, `adjudication_journal[]` | every record id with its hash and supersession link; a consumer replays the hashes from the JSONL files |
| `outcomes_sha256` | digest over every candidate's item hash, status, outcome, basis, reasons, evidence fingerprint and current decision/adjudication ids |
| `summary` | `ft_include`, `ft_exclude`, `ft_not_retrieved` (all final; a locked run has `ft_pending = 0`), reason breakdowns, studies vs reports |
| `future_reprovane_handoff` | the pointer to this page and "OAG-5 CLOSED" |

## What a future Reprovane adapter would have to do (owner/architect-gated)

1. Verify the export offline (`rkb fte verify --export-dir …` semantics: SHA256SUMS, file set, record-hash replay,
   outcomes digest, accounting).
2. Check that `run.release_id` / `run.release_manifest_sha256` name a SEALED release that Reprovane itself holds.
3. Treat every S05 record as a **proposal**: Reprovane would record its own authoritative full-text decisions under its
   own reviewer model and audit trail. Nothing would be copied as "already decided" without a Reprovane-side human step
   defined by Reprovane's owner.
4. Keep `FT_NOT_RETRIEVED` distinct from `FT_EXCLUDE` in any PRISMA view.
5. Never ingest PDF bytes, notes or local paths from the S05 export (none are present: notes are hashes only).

## Explicit non-goals of S05

- No RV-FTE API, no Reprovane import command, no final corpus release generation, no official PRISMA counts.
- No journal (S04J) records in the handoff: S05 sources are TA-screened releases only (ADR-05-01).

Opening OAG-5 needs a separate written decision by the owner and the architect.
