# S11 adaptations of accepted S10 v02 tests

## Why

Migration `0011_scientific_synthesis.sql` (ADR_S11) and version `1.0.0` change literals that accepted tests assert:
- the schema version after `init` / `upgrade` (10 → 11);
- the migration lists (0011 appended);
- the "future" migration number (0011 → 0012);
- the position of 0010, which is no longer the last migration;
- the version string.

Only these literals changed. Every logical assertion is kept, including all byte-identity and manifest checks of the upgrade tests. No test was deleted, skipped or weakened.

**Markers.** Each changed line carries `S11: docs/S11_TEST_ADAPTATIONS.md #n (...)`. Items 1–28 are exactly the lines that already carried the S10 adaptation marker, so their numbers follow `S10_TEST_ADAPTATIONS.md`. The earlier S05–S10 baseline guards therefore stay satisfied.

**Guard.** `tests/test_s11_operational.py::test_accepted_s10_tests_changed_only_on_marked_lines` (S11-TEST-ADAPTATIONS) compares every S10 v02 test and tool file with `tools/s10v02_test_baseline.json`, which was generated from the architect-accepted ZIP. It checks three things:
- files outside the adapted set are byte-identical;
- every changed block carries `S11`;
- no accepted line is deleted.

`tools/required_ids.json` only gains S11 ids and its source note. `tools/s10v02_migration_hashes.json` pins 0001–0010.

## The 31 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema 10 → 11 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0011 → 0012 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1..10]` → `[1..11]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 10)` → `(0, 11)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 10 → 11 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0011 → 0012 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 10)` → `(1, 11)`, schema 10 → 11, `to_version` 10 → 11 |
| 8 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 10 → 11 |
| 9 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 10)` → `(2, 11)` |
| 10 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 10)` → `(1, 11)` |
| 11 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(11, "0011_scientific_synthesis.sql")` |
| 12 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 10, […0009, 0010])` → `(3, 11, […0009, 0010, 0011])` |
| 13 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.10.0.dev2` → `1.0.0` |
| 14 | tests/test_s05_static.py | `test_version_and_syntax` | `0.10.0.dev2` → `1.0.0` |
| 15 | tests/test_fte_migration.py | `test_schema4_with_journal_data_upgrades_to_5` | `(4, 10, […, 0010])` → `(4, 11, […, 0010, 0011])`; event `to_version` 10 → 11 |
| 16 | tests/test_fte_migration.py | `test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean` | `(3, 10, […, 0010])` → `(3, 11, […, 0010, 0011])` |
| 17 | tests/test_s06_static.py | `test_version_and_syntax` | `0.10.0.dev2` → `1.0.0` |
| 18 | tests/test_s06_artifacts.py | `test_0006_is_additive_and_prior_migrations_unchanged` | fresh schema 10 → 11 |
| 19 | tests/test_s06_artifacts.py | `test_schema5_upgrade_to_6_with_backup_and_rollback` | `(5, 10, […0009, 0010])` → `(5, 11, […0009, 0010, 0011])` |
| 20 | tests/test_s07_operational.py | `test_0007_additive_and_0001_0006_unchanged` | fresh schema 10 → 11 |
| 21 | tests/test_s07_operational.py | `test_upgrade_6_to_7_backup_first_and_rollback` | `(6, 10, […0009, 0010])` → `(6, 11, […0009, 0010, 0011])` |
| 22 | tests/test_s07_operational.py | `test_docs_and_version` | `0.10.0.dev2` → `1.0.0` |
| 23 | tests/test_s08_operational.py | `test_0008_additive_and_0001_0007_unchanged` | fresh schema 10 → 11 |
| 24 | tests/test_s08_operational.py | `test_upgrade_7_to_8_backup_first_and_rollback` | `(7, 10, […0009, 0010])` → `(7, 11, […0009, 0010, 0011])` |
| 25 | tests/test_s08_operational.py | `test_docs_and_version` | `0.10.0.dev2` → `1.0.0` |
| 26 | tests/test_s09_operational.py | `test_0009_additive_and_0001_0008_unchanged` | fresh schema 10 → 11 (the `[8]` index adapted in S10 is unchanged) |
| 27 | tests/test_s09_operational.py | `test_upgrade_8_to_9_backup_first_and_rollback` | `(8, 10, [0009, 0010])` → `(8, 11, [0009, 0010, 0011])` |
| 28 | tests/test_s09_operational.py | `test_docs_and_version` | `0.10.0.dev2` → `1.0.0` |
| 29 | tests/test_s10_operational.py | `test_0010_additive_and_0001_0009_unchanged` | 0010 is the 10th migration (`[9]`) instead of the last (`[-1]`); fresh schema 10 → 11 |
| 30 | tests/test_s10_operational.py | `test_upgrade_9_to_10_backup_first_and_rollback` | `(9, 10, [0010])` → `(9, 11, [0010, 0011])`; the new tables after the upgrade are the S10 `aig_*` tables plus the S11 `syn_*` tables (the `aig_*` assertion is unchanged) |
| 31 | tests/test_s10_operational.py | `test_docs_and_version` | `0.10.0.dev2` → `1.0.0` (assertion and docstring) |

## Evidence that nothing else changed

- `evidence/s10v02_unchanged_tests_with_s11.json` records a run of the **unmodified** S10 v02 `tests/` tree against the S11 code. Only the tests above fail, each on a literal.
- The architect's unmodified S10-F01 reproducer (`INDEPENDENT_S10_A8_METADATA_ANCHOR_REPRO.py`, 5/5) and the earlier independent reproducers pass.
