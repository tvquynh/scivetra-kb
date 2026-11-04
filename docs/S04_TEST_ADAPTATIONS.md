# S04 adaptations of accepted S03 v02 tests

## Why tests had to change

Migration `0003_download_bridge.sql` is required for S04 (see ADR-S04-01). Adding it changes three schema-version facts that 8 accepted S03 v02 tests assert literally:
- the packaged migration list;
- the schema version after `init` or `upgrade`;
- the version number used to simulate a "future" migration.

A 0003 migration therefore cannot be added without touching these assertions. Only the pinned literal changed in each case, and nothing about the behaviour being tested.

## Evidence that nothing else changed

- **`evidence/s03_unchanged_tests_without_0003/`:** the UNMODIFIED S03 v02 `tests/` tree runs against the S04 code minus its two schema-3-coupled pieces: `0003_download_bridge.sql` and the two schema-3 entries of `CAS_REFERENCE_COLUMNS`. Result: **247/247 PASS**. Every other S04 change (bridge package, CLI, upgrade/doctor/backup/manifest extensions) therefore leaves the accepted behaviour intact.
- **`evidence/s03_unchanged_tests_with_0003.json`:** the same unmodified tests run against the complete S04 code. Exactly these 8 tests fail, each on a schema-version or schema-3 inventory literal.

## The 8 adaptations

| # | Test (file :: test) | Original assertion | S04 assertion | Behaviour still verified |
|---|---|---|---|---|
| 1 | `test_core.py :: InitAndOpenTests.test_init_is_explicit_and_refuses_reuse` | schema 2 after init | schema 3 | init is explicit, refuses reuse |
| 2 | `test_core.py :: InitAndOpenTests.test_refuses_future_schema` | fake future migration `(3, '0003_future.sql')` | `(4, '0004_future.sql')` | unknown future migration → `KB_CLIENT_TOO_OLD` |
| 3 | `test_core.py :: InitAndOpenTests.test_packaged_migration_is_contiguous` | `[1, 2]` | `[1, 2, 3]` | contiguous, checksummed package |
| 4 | `test_cli.py :: CliTests.test_cli_full_offline_path` | `rkb init` schema 2 | 3 | full S02 CLI path |
| 5 | `test_s03_cli.py :: S03CliTests.test_cli_full_s03_path` | `rkb init` schema 2 | 3 | full S03 CLI path |
| 6 | `test_upgrade_backup.py :: RefusalTests.test_future_schema_refused_everywhere` | fake `(3, '0003_future.sql')` | `(4, '0004_future.sql')` | `KB_CLIENT_TOO_OLD` for open and upgrade |
| 7 | `test_upgrade_backup.py :: UpgradeTests.test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | upgrade `1 → 2`, schema 2, `kb_upgrade_event.to_version = 2` | `1 → 3`, schema 3, `to_version = 3` | every S02 table and the S02 manifest are byte-identical; re-attempt is `UPGRADE_NOT_REQUIRED` |
| 8 | `test_s03_v02_fixes.py :: BackupReferenceTests.test_reference_inventory_is_schema_aware_and_shared` | 7 CAS-reference columns | 7 + `bridge_batch.manifest_bytes_sha256` and `bridge_batch.input_csv_bytes_sha256` | shared schema-aware inventory (S03-F02, S04-D08) |

Each edited line carries the comment `S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #n)`.

## What is unchanged

- No other S02/S03 test file or assertion was modified. The S03-F01/F02 regressions, and the architect reproducer (`evidence/s03_regressions/independent_repro_10_cases.json`), stay green.
- The `0002 → 0003` path is covered by new tests: S04-UPGRADE-2-3 and S04-UPGRADE-1-3.
- The rehearsal `evidence/s04_upgrade_rehearsal.json` uses the accepted S03 v02 code itself.

## v02 (0.4.0.dev2): change to one S04 v01 test

These are S04's own tests, not accepted S02/S03 tests. All 247 accepted S03 v02 tests and their 8 documented adaptations above are unchanged in v02.

| Test | v01 assertion | v02 assertion | Reason |
|---|---|---|---|
| `test_bridge_paths_backup.py :: PathTests.test_bridge_path_arguments_refused` | on a simulated POSIX host, `D:pdfs` → `WINDOWS_PATH_ON_POSIX` | on a simulated POSIX host, drive-relative forms (`D:pdfs`, `C:relative`, `E:ResearchKB`, `D:records/sub`) → `DRIVE_RELATIVE_PATH_REFUSED`; absolute Windows/backslash forms → `WINDOWS_PATH_ON_POSIX`; protected root (`E:\ResearchKB`, descendants, `e:/researchkb/...`) → `PROTECTED_DATA_ROOT`; UNC → `UNSUPPORTED_PATH_FORM` | architect finding S04-F02: one stable S04 code per form on every OS. The v01 assertion encoded the very inconsistency the architect reported (and the real-POSIX run failed on it) |
