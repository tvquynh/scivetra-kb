# S04J adaptations of accepted S04 v02 tests

## Why

Migration `0004_journal_harvesting.sql` (ADR_S04J JH-D1) changes four schema facts that 11 accepted tests assert
literally:

- the packaged migration list;
- the schema version after `init` or `upgrade`;
- the version number used to fake a "future" migration;
- the schema-aware list of CAS reference columns.

Only these pinned literals changed. Each changed line carries an `S04J` marker. The test
`test_s04j_static.StaticTests.test_accepted_s04_tests_changed_only_on_marked_lines` (JH-01) compares every accepted S04 v02
test and tool file line by line with `tools/s04v02_test_baseline.json`:

- unmarked files must be byte-identical;
- in the 6 adapted files, every changed line block must carry an S04J marker;
- no accepted line may be deleted.

## Evidence that nothing else changed

- `evidence/s04_unchanged_tests_without_0004/`: the **unmodified** S04 v02 `tests/` tree, run against the S04J code with
  only its two schema-4-coupled pieces removed (the file `0004_journal_harvesting.sql` and the schema-4 entry of
  `CAS_REFERENCE_COLUMNS`). Result: 282/282 PASS.
- `evidence/s04_unchanged_tests_with_0004.json`: the same unmodified tests against the complete S04J code. Exactly the
  11 tests below fail, each on a schema literal.

## The 11 adaptations

| # | Test | Original assertion | S04J assertion |
|---|---|---|---|
| 1 | `test_core.py :: InitAndOpenTests.test_init_is_explicit_and_refuses_reuse` | schema 3 after init | 4 |
| 2 | `test_core.py :: InitAndOpenTests.test_refuses_future_schema` | fake future migration `(4, '0004_future.sql')` | `(5, '0005_future.sql')` |
| 3 | `test_core.py :: InitAndOpenTests.test_packaged_migration_is_contiguous` | `[1, 2, 3]` | `[1, 2, 3, 4]` |
| 4 | `test_cli.py :: CliTests.test_cli_full_offline_path` | `rkb init` schema 3 | 4 |
| 5 | `test_s03_cli.py :: S03CliTests.test_cli_full_s03_path` | `rkb init` schema 3 | 4 |
| 6 | `test_upgrade_backup.py :: RefusalTests.test_future_schema_refused_everywhere` | fake `(4, '0004_future.sql')` | `(5, '0005_future.sql')` |
| 7 | `test_upgrade_backup.py :: UpgradeTests.test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | upgrade `1 -> 3`, schema 3, `kb_upgrade_event.to_version = 3` | `1 -> 4`, schema 4, `to_version = 4` |
| 8 | `test_s03_v02_fixes.py :: BackupReferenceTests.test_reference_inventory_is_schema_aware_and_shared` | 9 CAS reference columns | 9 + `jh_page.response_bytes_sha256` |
| 9 | `test_bridge_paths_backup.py :: BridgeBackupTests.test_bridge_cas_references_in_backup_doctor_restore` | verified backup schema 3 | 4 |
| 10 | `test_bridge_paths_backup.py :: UpgradeChainTests.test_schema2_to_3_backup_first_and_rollback` | upgrade `2 -> 3` | `2 -> 4` |
| 11 | `test_bridge_paths_backup.py :: UpgradeChainTests.test_schema1_to_3_and_missing_reference_blocks_upgrade` | upgrade `1 -> 3` | `1 -> 4` |

Tests 1-8 were already adapted once for S04 (`docs/S04_TEST_ADAPTATIONS.md`); 9-11 are S04's own tests. The behaviour each
verifies is unchanged. The `3 -> 4` upgrade path itself is covered by new tests: `S04J-UPGRADE-3-4` and
`S04J-UPGRADE-FAULT`.

## Tools

No accepted S04 tool was changed. `tools/s04_upgrade_rehearsal.py` still asserts `to_version == 3`, because it runs the
accepted S03 code against the S04 state it was written for. The S04J rehearsal is `tools/s04j_upgrade_rehearsal.py`.
