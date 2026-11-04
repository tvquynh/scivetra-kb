# S05 adaptations of accepted S04J v02 tests

## Why

Migration `0005_fulltext_eligibility_staging.sql` (ADR_S05 FTE-D1) and the version bump to `0.5.0.dev2` change five
facts that 14 accepted tests assert literally:

- the packaged migration list;
- the schema version after `init` or `upgrade`;
- the version number used to fake a "future" migration;
- the schema-aware list of CAS reference columns (the locked FTE handoff manifest);
- the package version string.

Only these literals changed. No test was deleted, skipped or weakened; each logical assertion is kept. Each changed line
carries an `S05: docs/S05_TEST_ADAPTATIONS.md #n` marker.

`tests/test_s05_static.py::test_accepted_s04j_tests_changed_only_on_marked_lines` (S05-TEST-ADAPTATIONS) compares every
S04J v02 test and tool file with `tools/s04j_v02_test_baseline.json` (line hashes taken from the accepted ZIP):

- files outside the 8 adapted files must be byte-identical;
- every changed or added line block in an adapted file must carry an `S05` marker;
- no accepted line may be deleted.

## Evidence that nothing else changed

- `evidence/s04j_v02_baseline/`: the accepted S04J v02 source, unmodified, before any S05 change: 347/347 PASS, plus the
  original multi-ISSN reproduction script (3/3 scenarios, exit 0).
- `evidence/s04j_unchanged_tests_without_0005/`: the **unmodified** S04J v02 `tests/` tree, run against the S05 code with
  only its schema-5-coupled pieces removed (the file `0005_fulltext_eligibility_staging.sql`, the schema-5 CAS entry and
  the version string). Expected: 347/347 PASS.
- `evidence/s04j_unchanged_tests_with_0005.json`: the same unmodified tests against the complete S05 code. Exactly the
  tests below fail, each on a literal.

## The 14 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema version 4 → 5 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0005 → 0006 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1,2,3,4]` → `[1,2,3,4,5]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 4)` → `(0, 5)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 4 → 5 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0005 → 0006 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 4)` → `(1, 5)`, schema 4 → 5, `to_version` 4 → 5 |
| 8 | tests/test_s03_v02_fixes.py | `test_reference_inventory_is_schema_aware_and_shared` | adds `("fte_lock", "handoff_manifest_sha256")` |
| 9 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 4 → 5 |
| 10 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 4)` → `(2, 5)` |
| 11 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 4)` → `(1, 5)` |
| 12 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(5, "0005_fulltext_eligibility_staging.sql")` |
| 13 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 4, [0004])` → `(3, 5, [0004, 0005])` |
| 14 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.4.1.dev2` → `0.5.0.dev2` |

The upgrade adaptations (#7, #10, #11, #13) keep all byte-identity and semantic-manifest checks of the earlier tables; the
new S05 tests additionally assert that every S04J `jh_*` table is byte-identical across 4 → 5 and 3 → 4 → 5.
