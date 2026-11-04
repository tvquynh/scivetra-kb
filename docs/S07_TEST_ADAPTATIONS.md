# S07 adaptations of accepted S06 v02 tests

## Why

Migration `0007_ai_screening_proposals.sql` (ADR_S07), the version `0.7.0.dev2` and the schema-7 CAS reference
(`ai_input_pack.pack_sha256`) change literals that accepted tests assert: the migration list, the schema version after
`init` / `upgrade`, the "future" migration number (0007 → 0008), the CAS reference list and the version string. Only these
literals changed; every logical assertion, including all byte-identity and manifest checks of the upgrade tests, is kept. No
test was deleted, skipped or weakened. Each changed line carries `S07: docs/S07_TEST_ADAPTATIONS.md #n (after S06)`; the words
"after S06" keep the earlier S04J, S05 and S06 baseline guards satisfied (they require an S04J/S05/S06 marker on changed lines).

`tests/test_s07_operational.py::test_accepted_s06_tests_changed_only_on_marked_lines` (S07-TEST-ADAPTATIONS) compares every S06
v02 test and tool file with `tools/s06v02_test_baseline.json`: files outside the adapted set are byte-identical, every changed
block carries `S07`, no accepted line is deleted. `tools/required_ids.json` only gains S07 IDs.

## The 20 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema 6 → 7 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0007 → 0008 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1..6]` → `[1..7]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 6)` → `(0, 7)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 6 → 7 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0007 → 0008 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 6)` → `(1, 7)`, schema 6 → 7, `to_version` 6 → 7 |
| 8 | tests/test_s03_v02_fixes.py | `test_reference_inventory_is_schema_aware_and_shared` | adds `("ai_input_pack", "pack_sha256")` |
| 9 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 6 → 7 |
| 10 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 6)` → `(2, 7)` |
| 11 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 6)` → `(1, 7)` |
| 12 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(7, "0007_ai_screening_proposals.sql")` |
| 13 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 6, […0006])` → `(3, 7, […0006, 0007])` |
| 14 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.6.0.dev2` → `0.7.0.dev2` |
| 15 | tests/test_s05_static.py | `test_version_and_syntax` | `0.6.0.dev2` → `0.7.0.dev2` |
| 16 | tests/test_fte_migration.py | `test_schema4_with_journal_data_upgrades_to_5` | `(4, 6, [0005, 0006])` → `(4, 7, [0005, 0006, 0007])`; event `to_version` 6 → 7 |
| 17 | tests/test_fte_migration.py | `test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean` | `(3, 6, […])` → `(3, 7, […, 0007])` |
| 18 | tests/test_s06_static.py | `test_version_and_syntax` | `0.6.0.dev2` → `0.7.0.dev2` |
| 19 | tests/test_s06_artifacts.py | `test_0006_is_additive_and_prior_migrations_unchanged` | 0006 is the 6th migration (`[5]`) instead of the last; fresh schema 6 → 7 |
| 20 | tests/test_s06_artifacts.py | `test_schema5_upgrade_to_6_with_backup_and_rollback` | `(5, 6, [0006])` → `(5, 7, [0006, 0007])` |

## Evidence that nothing else changed

- `evidence/s06v02_unchanged_tests_with_complete_s07.json`: the **unmodified** S06 v02 `tests/` tree against the S07 code; exactly
  the tests above fail, each on a literal.
- The architect's S06 reproducer (4/4), S05 reproducer (7/7), S04J multi-ISSN (3/3), the S05 100-candidate and S06 100-PDF pilots
  run unmodified on the S07 code.
