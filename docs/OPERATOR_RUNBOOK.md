# SciVetra KB operator runbook (1.0.0)

For the person who keeps a KB healthy: health checks, backups, restores, upgrades, rollback and incidents. All commands print JSON. The examples use `R` for the data root, `T` for a backup target and `B` for one backup folder.

**Never use `E:\ResearchKB`** until the owner has approved it (OAG-2), together with a backup destination (OAG-1). `rkb` refuses that root (`PROTECTED_DATA_ROOT`).

## 1. Routine

| When | Do | Expect |
|---|---|---|
| Daily, before work | `rkb doctor --data-root R` | `"ok": true` |
| Weekly, or before any upgrade, bulk import or rights change | `rkb backup --data-root R --target T`, then `rkb verify-backup --backup B` | `"valid": true` |
| Monthly | `rkb doctor --data-root R --full` (re-hashes every CAS object) | no problems |
| Monthly | A restore drill into a new folder: `rkb restore --backup B --data-root NEW`, then `rkb doctor --data-root NEW --full` | ok |

**Backup rules:**
- A backup folder is `T/rkb-backup-<UTC>/`. It holds the database (SQLite backup API), a verified CAS copy, `backup_manifest.json` and `SHA256SUMS.txt`.
- `T` must be outside `R`.
- A backup is refused (`BACKUP_SOURCE_INCOMPLETE`) if a referenced CAS object is missing or corrupt. Fix the KB first; see section 4.

## 2. Upgrade (new `rkb` version with a newer schema)

1. Stop every `rkb` process (one writer at a time; a writer lock enforces this).
2. Run `rkb upgrade --data-root R --backup-target T`. The upgrade:
   - writes and verifies a backup **first**;
   - migrates a copy;
   - verifies that every earlier table is byte-identical, plus integrity and foreign keys;
   - only then swaps the copy in.

   The old database is kept as `db/kb.sqlite.pre-<old>-<UTC>`.
3. Run `rkb doctor --data-root R --full`.

Any other command on an older KB answers `KB_UPGRADE_REQUIRED`; nothing migrates silently. A KB from a newer client answers `KB_CLIENT_TOO_OLD`.

The release readiness tool rehearses every path from schema 1 … 10 to 11 (gate G3, `tools/s12_release_readiness.py`).

## 3. Rollback

There are two options; both are rehearsed in G3.

- **Fast.** Stop `rkb`, then replace `db/kb.sqlite` with the kept `db/kb.sqlite.pre-<old>-<UTC>` file. Its SHA-256 equals the pre-upgrade database. Reinstall the previous `rkb` version.
- **Clean.** Restore the upgrade backup into a new folder (`rkb restore --backup B --data-root NEW`) and point the previous version at it.

Files exported after the upgrade (review copies, PBD lists) are outside the KB. They are not removed by a rollback.

## 4. Incidents

| Symptom | Meaning | Action |
|---|---|---|
| `doctor` reports a CAS hash mismatch or a missing object | A stored file was changed or deleted outside `rkb` | Do not write. Restore the object from the latest verified backup (restore into a new root and compare), or restore the whole KB. Records citing it show STALE until it is restored. |
| `WRITER_ACTIVE` ("Another rkb writer is active") | Another writer is running | Wait or stop it. Never delete the lock while a process runs. |
| `EXPORT_RELEASE_REFUSED` (S11) | Rights, evidence or a source changed while an export was written | Nothing was published. Run `rkb synth audit` again, then export again. |
| `EXPORT_RELEASE_FAILED` / `EXPORT_AUDIT_FAILED` | Lock timeout or file error during export | Nothing is visible; the audit trail says ABANDONED. Retry. |
| `BLOCKED_RIGHTS`, `WITHHELD`, `WITHHELD_A5` | The current rights do not allow this use | Expected behaviour. The owner may record a new rights assessment; never edit the database. |
| A rights assessment was entered wrongly | — | Record a superseding assessment (`rkb rights assess`). Every later use re-checks; earlier review copies outside the KB are not recalled. |
| Suspected leak of source text in an export | — | Delete the export folder, keep the KB, report it with the `syn_export_event` / `ev_export_event` ids. Exports are LOCAL_REVIEW_ONLY by design. |

## 5. What `rkb` will never do on its own

- **Network.** No network access, no AI API call, and no upload to GitHub, PyPI or a cloud service.
- **Other tools.** No change to Reprovane or PaperBatchDownloader, and no reading of browser or Downloads folders.
- **History.** No deleting or rewriting of history: the record tables are append-only, enforced by no-update / no-delete database triggers in every migration.
