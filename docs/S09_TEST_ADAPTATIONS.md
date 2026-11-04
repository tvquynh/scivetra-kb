# S09 adaptations of accepted S08 v03 tests

## Why

Migration `0009_local_retrieval.sql` (ADR_S09) and the version `0.9.0.dev2` (S09 v02; `0.9.0.dev1` in v01) change literals that accepted tests assert:
- the schema version after `init` / `upgrade` (8 → 9);
- the migration lists (0009 appended);
- the "future" migration number (0009 → 0010);
- the position of 0008, which is no longer the last migration;
- the version string.

Only these literals changed. Every logical assertion, including all byte-identity and manifest checks of the upgrade tests, is
kept. No test was deleted, skipped or weakened.

Each changed line carries `S09: docs/S09_TEST_ADAPTATIONS.md #n (after S08, S07, S06)`. The words "S08", "S07" and "S06" keep the
earlier S05–S08 baseline guards satisfied, because they require their own marker on changed lines.

`tests/test_s09_operational.py::test_accepted_s08_tests_changed_only_on_marked_lines` (S09-TEST-ADAPTATIONS) compares every
S08 v03 test and tool file with `tools/s08v03_test_baseline.json`, generated from the accepted ZIP. It checks three things:
- files outside the adapted set are byte-identical;
- every changed block carries `S09`;
- no accepted line is deleted.

`tools/required_ids.json` only gains S09 IDs and its source note. `tools/s08v03_migration_hashes.json` pins 0001–0008.

## The 25 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema 8 → 9 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0009 → 0010 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1..8]` → `[1..9]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 8)` → `(0, 9)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 8 → 9 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0009 → 0010 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 8)` → `(1, 9)`, schema 8 → 9, `to_version` 8 → 9 |
| 8 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 8 → 9 |
| 9 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 8)` → `(2, 9)` |
| 10 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 8)` → `(1, 9)` |
| 11 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(9, "0009_local_retrieval.sql")` |
| 12 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 8, […0008])` → `(3, 9, […0008, 0009])` |
| 13 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.8.0.dev3` → `0.9.0.dev2` (v01: `0.9.0.dev1`) |
| 14 | tests/test_s05_static.py | `test_version_and_syntax` | `0.8.0.dev3` → `0.9.0.dev2` (v01: `0.9.0.dev1`) |
| 15 | tests/test_fte_migration.py | `test_schema4_with_journal_data_upgrades_to_5` | `(4, 8, […0008])` → `(4, 9, […, 0009])`; event `to_version` 8 → 9 |
| 16 | tests/test_fte_migration.py | `test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean` | `(3, 8, […])` → `(3, 9, […, 0009])` |
| 17 | tests/test_s06_static.py | `test_version_and_syntax` | `0.8.0.dev3` → `0.9.0.dev2` (v01: `0.9.0.dev1`) |
| 18 | tests/test_s06_artifacts.py | `test_0006_is_additive_and_prior_migrations_unchanged` | fresh schema 8 → 9 |
| 19 | tests/test_s06_artifacts.py | `test_schema5_upgrade_to_6_with_backup_and_rollback` | `(5, 8, […0008])` → `(5, 9, […0008, 0009])` |
| 20 | tests/test_s07_operational.py | `test_0007_additive_and_0001_0006_unchanged` | fresh schema 8 → 9 |
| 21 | tests/test_s07_operational.py | `test_upgrade_6_to_7_backup_first_and_rollback` | `(6, 8, […0008])` → `(6, 9, […0008, 0009])` |
| 22 | tests/test_s07_operational.py | `test_docs_and_version` | `0.8.0.dev3` → `0.9.0.dev2` (v01: `0.9.0.dev1`) |
| 23 | tests/test_s08_operational.py | `test_0008_additive_and_0001_0007_unchanged` | 0008 is the 8th migration (`[7]`) instead of the last; fresh schema 8 → 9 |
| 24 | tests/test_s08_operational.py | `test_upgrade_7_to_8_backup_first_and_rollback` | `(7, 8, [0008])` → `(7, 9, [0008, 0009])` |
| 25 | tests/test_s08_operational.py | `test_docs_and_version` | `0.8.0.dev3` → `0.9.0.dev2` (v01: `0.9.0.dev1`) |

## Evidence that nothing else changed

- `evidence/s08v03_unchanged_tests_with_s09.json`: the **unmodified** S08 v03 `tests/` tree run against the S09 code. Exactly the
  tests above fail, each on a literal.
- The architect's S08 reproducers (5/5 and 4/4), S07 (6/6), S06 (4/4), S05 (7/7) and S04J (3/3) reproducers run unchanged.
