# S10 adaptations of accepted S09 v02 tests

## Why

Migration `0010_ai_integration_gateway.sql` (ADR_S10) and the version `0.10.0.dev1` change literals that accepted tests assert:
- the schema version after `init` / `upgrade` (9 → 10);
- the migration lists (0010 appended);
- the "future" migration number (0010 → 0011);
- the position of 0009, which is no longer the last migration;
- the version string.

Only these literals changed. Every logical assertion is kept, including all byte-identity and manifest checks of the upgrade tests. No test was deleted, skipped or weakened.

Each changed line carries a marker:
- in files older than S09: `S10: docs/S10_TEST_ADAPTATIONS.md #n (after S09, S08, S07, S06)`, so the earlier S06–S09 baseline guards stay satisfied;
- in `tests/test_s09_operational.py`: `(after S09)`.

`tests/test_s10_operational.py::test_accepted_s09_tests_changed_only_on_marked_lines` (S10-TEST-ADAPTATIONS) compares every S09 v02 test and tool file with `tools/s09v02_test_baseline.json`, which was generated from the architect-accepted ZIP. It checks three things:
- files outside the adapted set are byte-identical;
- every changed block carries `S10`;
- no accepted line is deleted.

`tools/required_ids.json` only gains S10 IDs and its source note. `tools/s09v02_migration_hashes.json` pins 0001–0009, and these hashes equal the architect's independent hashes.

## The 28 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema 9 → 10 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0010 → 0011 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1..9]` → `[1..10]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 9)` → `(0, 10)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 9 → 10 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0010 → 0011 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 9)` → `(1, 10)`, schema 9 → 10, `to_version` 9 → 10 |
| 8 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 9 → 10 |
| 9 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 9)` → `(2, 10)` |
| 10 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 9)` → `(1, 10)` |
| 11 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(10, "0010_ai_integration_gateway.sql")` |
| 12 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 9, […0009])` → `(3, 10, […0009, 0010])` |
| 13 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.9.0.dev2` → `0.10.0.dev1` (v02: `0.10.0.dev2`) |
| 14 | tests/test_s05_static.py | `test_version_and_syntax` | `0.9.0.dev2` → `0.10.0.dev1` (v02: `0.10.0.dev2`) |
| 15 | tests/test_fte_migration.py | `test_schema4_with_journal_data_upgrades_to_5` | `(4, 9, […])` → `(4, 10, […, 0010])`; event `to_version` 9 → 10 |
| 16 | tests/test_fte_migration.py | `test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean` | `(3, 9, […])` → `(3, 10, […, 0010])` |
| 17 | tests/test_s06_static.py | `test_version_and_syntax` | `0.9.0.dev2` → `0.10.0.dev1` (v02: `0.10.0.dev2`) |
| 18 | tests/test_s06_artifacts.py | `test_0006_is_additive_and_prior_migrations_unchanged` | fresh schema 9 → 10 |
| 19 | tests/test_s06_artifacts.py | `test_schema5_upgrade_to_6_with_backup_and_rollback` | `(5, 9, […0009])` → `(5, 10, […0009, 0010])` |
| 20 | tests/test_s07_operational.py | `test_0007_additive_and_0001_0006_unchanged` | fresh schema 9 → 10 |
| 21 | tests/test_s07_operational.py | `test_upgrade_6_to_7_backup_first_and_rollback` | `(6, 9, […0009])` → `(6, 10, […0009, 0010])` |
| 22 | tests/test_s07_operational.py | `test_docs_and_version` | `0.9.0.dev2` → `0.10.0.dev1` (v02: `0.10.0.dev2`) |
| 23 | tests/test_s08_operational.py | `test_0008_additive_and_0001_0007_unchanged` | fresh schema 9 → 10 |
| 24 | tests/test_s08_operational.py | `test_upgrade_7_to_8_backup_first_and_rollback` | `(7, 9, […0009])` → `(7, 10, […0009, 0010])` |
| 25 | tests/test_s08_operational.py | `test_docs_and_version` | `0.9.0.dev2` → `0.10.0.dev1` (v02: `0.10.0.dev2`) |
| 26 | tests/test_s09_operational.py | `test_0009_additive_and_0001_0008_unchanged` | 0009 is the 9th migration (`[8]`) instead of the last; fresh schema 9 → 10 |
| 27 | tests/test_s09_operational.py | `test_upgrade_8_to_9_backup_first_and_rollback` | `(8, 9, [0009])` → `(8, 10, [0009, 0010])` |
| 28 | tests/test_s09_operational.py | `test_docs_and_version` | `0.9.0.dev2` → `0.10.0.dev1` (v02: `0.10.0.dev2`) |

## Evidence that nothing else changed

- `evidence/s09v02_unchanged_tests_with_s10.json` records a run of the **unmodified** S09 v02 `tests/` tree against the S10 code. Exactly the 28 tests above fail, each on a literal.
- The architect's unmodified independent S09 reproducer (`INDEPENDENT_S09_REPRO.py`, 3/3) and the S09 v02 regression tests (8/8) pass.
