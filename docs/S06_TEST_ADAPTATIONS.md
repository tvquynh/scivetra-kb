# S06 adaptations of accepted S05 v02 tests

## Why

Migration `0006_pdf_parse_artifacts.sql` (ADR_S06 PZ-D7), the version bump to `0.6.0.dev2`, the new schema-6 CAS reference
(`parse_artifact.artifact_sha256`) and the new sandbox modules change facts that accepted tests assert literally:

- the packaged migration list and the schema version after `init` / `upgrade`;
- the version number used to fake a "future" migration (0006 → 0007);
- the schema-aware list of CAS reference columns;
- the package version string;
- two static allowlists: the modules that may start a process (`parse/sandbox.py`, `parse/parse_child.py`) and the one
  module that imports `socket` (`parse/parse_child.py`, inside its sandbox self-test, to prove the audit hook refuses it);
- the earlier baseline guards (S04J and S05 static tests), which must now accept S06-marked lines in two more files.

Only these literals changed. No test was deleted, skipped or weakened; every logical assertion is kept, and the upgrade
adaptations keep all byte-identity and manifest checks. Each changed line carries an `S06` marker
(`S06: docs/S06_TEST_ADAPTATIONS.md #n`).

`tests/test_s06_static.py::test_accepted_s05_tests_changed_only_on_marked_lines` (S06-TEST-ADAPTATIONS) compares every S05 v02
test and tool file with `tools/s05v02_test_baseline.json` (line hashes from the accepted ZIP): files outside the adapted set are
byte-identical; every changed or added block carries `S06`; no accepted line is deleted. `tools/required_ids.json` and
`tools/documented_gaps.json` only gain S06 entries.

## The 22 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema version 5 → 6 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0006 → 0007 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1..5]` → `[1..6]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 5)` → `(0, 6)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 5 → 6 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0006 → 0007 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 5)` → `(1, 6)`, schema 5 → 6, `to_version` 5 → 6 |
| 8 | tests/test_s03_v02_fixes.py | `test_reference_inventory_is_schema_aware_and_shared` | adds `("parse_artifact", "artifact_sha256")` |
| 9 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 5 → 6 |
| 10 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 5)` → `(2, 6)` |
| 11 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 5)` → `(1, 6)` |
| 12 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(6, "0006_pdf_parse_artifacts.sql")` |
| 13 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 5, [0004, 0005])` → `(3, 6, [0004, 0005, 0006])` |
| 14 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.5.0.dev2` → `0.6.0.dev2` |
| 15 | tests/test_s05_static.py | `test_version_and_syntax` | `0.5.0.dev2` → `0.6.0.dev2` |
| 16 | tests/test_fte_migration.py | `test_accepted_migrations_unchanged_and_0005_additive` | 0005 is the 5th migration (`[4]`) instead of the last (`[-1]`) |
| 17 | tests/test_fte_migration.py | `test_schema4_with_journal_data_upgrades_to_5` | `(4, 5, [0005])` → `(4, 6, [0005, 0006])`; upgrade event `to_version` 5 → 6 |
| 18 | tests/test_fte_migration.py | `test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean` | `(3, 5, [0004, 0005])` → `(3, 6, [0004, 0005, 0006])` |
| 19 | tests/test_static.py | `test_no_network_client_code` | `parse/parse_child.py` exempted; a new assertion pins exactly one `import socket` there (self-test) |
| 20 | tests/test_s03_static.py | `test_subprocess_only_in_sandbox` | allowlist + `parse/parse_child.py`, `parse/sandbox.py` |
| 21 | tests/test_s05_static.py | `test_accepted_s04j_tests_changed_only_on_marked_lines` | adapted set + `test_static.py`, `test_s03_static.py`; marker `S05` or `S06`; limit 30 → 50 lines |
| 22 | tests/test_s04j_static.py | `test_accepted_s04_tests_changed_only_on_marked_lines` | adapted set + `test_static.py`, `test_s03_static.py`; marker `S04J` or `S06`; limit 30 → 50 lines |

## Evidence that nothing else changed

- `evidence/s05v02_unchanged_tests_with_complete_s06.json`: the **unmodified** S05 v02 `tests/` tree run against the complete
  S06 code; exactly the tests above fail, each on a literal.
- The architect's original S05 reproducer (7/7), the S04J multi-ISSN reproducer (3/3) and the S05 100-candidate pilot run
  unmodified on the S06 code (`evidence/`).
