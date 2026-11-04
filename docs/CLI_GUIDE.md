# `rkb` command guide (S03 local development candidate)

Every command prints exactly one JSON document on stdout. Errors are `{"ok": false, "error": "<CODE>", "message": "..."}` with exit code 2 (validation/policy) or 3 (schema/integrity). Use synthetic data and disposable roots only; `E:\ResearchKB` is refused (`PROTECTED_DATA_ROOT`, OAG-2).

`--operator` takes a free label matching `[A-Za-z0-9._-]{1,64}` (no email addresses); it is stored as `OPERATOR:<label>`.

## KB lifecycle

| Command | Effect | Writes? |
|---|---|---|
| `rkb init --data-root R` | create a new schema-2 KB in an absent/empty folder | yes (new root only) |
| `rkb doctor --data-root R [--full]` | integrity, foreign keys, triggers, CAS references; `--full` also re-hashes every CAS object, lists unreferenced objects and re-checks library invariants (IA-1, PAL-1, PPR-2, rights default-deny) | no (mode=ro) |
| `rkb backup --data-root R --target T` | new `T/rkb-backup-<UTC>/` with DB (SQLite backup API), verified CAS copy, `backup_manifest.json`, `SHA256SUMS.txt`. No default target (OAG-1). T must be outside R. Refused before any folder is created if a DB-referenced CAS object is missing, a link or corrupt (`BACKUP_SOURCE_INCOMPLETE`, exit 3) | target only |
| `rkb verify-backup --backup DIR` | offline check of inventory, hashes, ledger, integrity, domain manifests, CAS list; incomplete backups fail `BACKUP_INCOMPLETE`; references recomputed from the backup DB must all be present (`BACKUP_CAS_REFERENCE_MISSING`, independent of manifest/SHA256SUMS) | no |
| `rkb restore --backup DIR --data-root NEW` | verify, copy into an absent/empty NEW root, re-compare DB hash, manifests, CAS list and DB-referenced objects; on any failure the files it wrote are removed (`RESTORE_MISMATCH`) | NEW root only |
| `rkb upgrade --data-root R --backup-target T` | explicit S02 schema-1 → schema-2 upgrade (backup, copy, migrate, verify, swap; keeps `db/kb.sqlite.pre-1-<UTC>`). On a current KB: `UPGRADE_NOT_REQUIRED` | yes |

No other command migrates. A schema-1 KB is refused by every other command with `KB_UPGRADE_REQUIRED`; a newer schema with `KB_CLIENT_TOO_OLD`.

## Library (Reprovane portable exports, link-policy/1)

| Command | Effect | Writes? |
|---|---|---|
| `rkb library import-reprovane --data-root R --export-dir D --workspace-label L --operator O [--expected-manifest-sha256 H]` | import one `corpus-portable/1` export (never written to); repeat = `DUPLICATE_NOOP`; refusals are journaled `REJECTED` with no identity writes | yes |
| `rkb library review-queue --data-root R` | open link candidates, identity conflicts, asset links awaiting review, WorkFamily proposals, rights tasks | no |
| `rkb library search --data-root R [--doi X] [--title T] [--paper-id P]` | exact DOI comparison-key lookup (non-VALID input: no lookup), title substring over RV reference titles, paper view | no |
| `rkb library decide-link --data-root R --candidate-id LC --decision SAME_MANIFESTATION\|DIFFERENT_MANIFESTATION_SAME_WORK\|UNRELATED\|DEFER --operator O --reason TXT` | append a LinkDecision; DIFFERENT_… confirms the WorkFamily pair; UNRELATED rejects it; SAME_… is recorded only (no merge in S03) | yes |
| `rkb library resolve-conflict --data-root R --conflict-id IC --action ASSERTIONS_WITHDRAWN\|DOI_SELECTED_FOR_ITEM\|CONFIRMED_DISTINCT_MANIFESTATIONS\|DEFER --operator O --reason TXT --evidence-ref REF [--activate-assertion-id A] [--withdraw-assertion-id A]` | human-anchored identity resolution (IA-1 enforced) | yes |
| `rkb library set-kind --data-root R --paper-id P --kind KIND --operator O --reason TXT --claim-ref REF` | operator manifestation-kind event citing claims | yes |
| `rkb library confirm-family-member --data-root R --member-id M --confirmation HUMAN_CONFIRMED\|REJECTED\|PROPOSED --operator O --reason TXT` | WorkFamily member decision | yes |
| `rkb library workspace-status --data-root R --workspace-label L --availability AVAILABLE\|SOURCE_UNAVAILABLE\|RETIRED --operator O --reason TXT` | availability event; references are never deleted (N26) | yes |
| `rkb reconcile --data-root R --workspace-label L --export-dir D` | compare a supplied export copy: `CONFIRMED`, `NEW_RELEASE_NOT_IMPORTED`, `DRIFT`, `WORKSPACE_UNKNOWN` | no |

## Assets and rights (synthetic files only)

| Command | Effect | Writes? |
|---|---|---|
| `rkb asset add-version --data-root R --paper-id P --version-class SUBMITTED\|ACCEPTED\|PUBLISHED\|UPDATED_PUBLISHED\|UNKNOWN [--evidence-file F] --operator O` | DocumentVersion; known classes need allowlisted evidence items `{source, claim_ref, value}` (never filename/URL/host) | yes |
| `rkb asset register --data-root R --fixture-root ROOT --file REL --document-version-id DV --operator O [--role MAIN_TEXT] [--access-route ROUTE] [--access-barrier X]... [--max-bytes N] [--parser-timeout S] [--require-human-identity] [--pypdf-site DIR]` | explicit one-file intake: G0 path/file checks, streamed staging with TOCTOU check, G1 sandboxed validator, G2 CAS, G3 identity, PAL-1, G4 initial UNKNOWN rights | yes |
| `rkb asset review-link --data-root R --link-id PAL --decision CONFIRM\|REJECT\|MARK_SUPPLEMENT --operator O --reason TXT` | human link decision | yes |
| `rkb rights assess --data-root R --asset SHA --document-version-id DV --evidence-file F --operator O` | rights-policy/1 classification from licence/availability/terms evidence; access provenance never changes the class | yes |
| `rkb rights gate --data-root R --asset SHA --document-version-id DV --action A1..A10 [--quote-chars N]` | pure permission gate decision (UNDETERMINED = DENY; A9 always DENY) | no |

Rights evidence file shape: `{"license_evidence":[{"source","license_uri","content_version":"vor|am|submitted","start_date"?,"delay_in_days"?,"claim_ref"}], "availability_evidence":[{"source","is_oa":true,"version":"publishedVersion|acceptedVersion|submittedVersion","claim_ref"}], "terms_declarations":[{"terms","evidence_ref","restrictive":true|false}], "access_provenance":{"access_route","access_barrier_observed":[...]}}`.

## S04 Download Bridge (file exchange with PaperBatchDownloader; see `BRIDGE_USER_GUIDE.md`)

| Command | Does | Writes |
|---|---|---|
| `rkb bridge export --data-root R --source-dir D --kind rv-manual-queue|rv-corpus --workspace-label W --out-dir NEW --operator O [--expected-manifest-sha256 X] [--rv-verify-json F] [--fulltext-dir D11B] [--limit N] [--include-ineligible-candidates]` | verified RV source -> `pbd_input_<short>.csv` + `bridge_manifest.json` + empty `pbd_output_<short>/` | KB + NEW folder |
| `rkb bridge import --data-root R --batch-id ab_… --result-csv F --pdf-folder DIR --operator O [--manifest M] [--accept-late-files] [--allow-different-folder] [--pypdf-site S]` | reconciles a PBD result CSV and the files named `<save_as>` through S03 G0-G3; exit 0 / 2 / 3 / 4 (partial) | KB only (never the folder or CSV) |
| `rkb bridge status --data-root R --batch-id ab_…` | per-row status, assets, identity states, review tasks, imports | no |
| `rkb bridge close --data-root R --batch-id ab_… --operator O [--reason OPERATOR_CLOSE_WITH_OPEN_ROWS]` | CLOSED (all rows final) or CLOSED_WITH_OPEN_ROWS | KB |

There is no watcher, scan, download, browser automation, GUI or synchronisation command.
