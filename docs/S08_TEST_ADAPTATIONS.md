# S08 adaptations of accepted S07 v02 tests

## Why

Migration `0008_scientific_evidence_extraction.sql` (ADR_S08), the version `0.8.0.dev3` (S08 v03; `0.8.0.dev2` in v02, `0.8.0.dev1` in v01) and the schema-8 CAS reference
(`ev_input_pack.pack_sha256`) change literals that accepted tests assert: the migration list, the schema version after
`init` / `upgrade`, the "future" migration number (0008 → 0009), the CAS reference list and the version string. Only these
literals changed; every logical assertion, including all byte-identity and manifest checks of the upgrade tests, is kept. No
test was deleted, skipped or weakened. Each changed line carries `S08: docs/S08_TEST_ADAPTATIONS.md #n (after S07, S06)`; the
words "S07" and "S06" keep the earlier S04J, S05, S06 and S07 baseline guards satisfied (they require their marker on changed
lines).

`tests/test_s08_operational.py::test_accepted_s07_tests_changed_only_on_marked_lines` (S08-TEST-ADAPTATIONS) compares every
S07 v02 test and tool file with `tools/s07v02_test_baseline.json` (generated from the accepted ZIP): files outside the adapted
set are byte-identical, every changed block carries `S08`, no accepted line is deleted. `tools/required_ids.json` only gains
S08 IDs (and its source note). `tools/s07v02_migration_hashes.json` pins 0001–0007.

## The 23 adaptations

| # | File | Test | Old → new |
|---|---|---|---|
| 1 | tests/test_core.py | `test_init_is_explicit_and_refuses_reuse` | schema 7 → 8 |
| 2 | tests/test_core.py | `test_refuses_future_schema` | fake future migration 0008 → 0009 |
| 3 | tests/test_core.py | `test_packaged_migration_is_contiguous` | `[1..7]` → `[1..8]` |
| 4 | tests/test_cli.py | `test_cli_full_offline_path` | `(0, 7)` → `(0, 8)` |
| 5 | tests/test_s03_cli.py | `test_cli_full_s03_path` | 7 → 8 |
| 6 | tests/test_upgrade_backup.py | `test_future_schema_refused_everywhere` | fake future migration 0008 → 0009 |
| 7 | tests/test_upgrade_backup.py | `test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent` | `(1, 7)` → `(1, 8)`, schema 7 → 8, `to_version` 7 → 8 |
| 8 | tests/test_s03_v02_fixes.py | `test_reference_inventory_is_schema_aware_and_shared` | adds `("ev_input_pack", "pack_sha256")` |
| 9 | tests/test_bridge_paths_backup.py | `test_bridge_cas_references_in_backup_doctor_restore` | `kb_schema_version` 7 → 8 |
| 10 | tests/test_bridge_paths_backup.py | `test_schema2_to_3_backup_first_and_rollback` | `(2, 7)` → `(2, 8)` |
| 11 | tests/test_bridge_paths_backup.py | `test_schema1_to_3_and_missing_reference_blocks_upgrade` | `(1, 7)` → `(1, 8)` |
| 12 | tests/test_journal_migration.py | `test_accepted_migrations_are_byte_identical` | adds `(8, "0008_scientific_evidence_extraction.sql")` |
| 13 | tests/test_journal_migration.py | `test_schema3_kb_upgrades_backup_first_and_rolls_back` | `(3, 7, […0007])` → `(3, 8, […0007, 0008])` |
| 14 | tests/test_s04j_static.py | `test_all_python_compiles_and_version` | `0.7.0.dev2` → `0.8.0.dev2` |
| 15 | tests/test_s05_static.py | `test_version_and_syntax` | `0.7.0.dev2` → `0.8.0.dev2` |
| 16 | tests/test_fte_migration.py | `test_schema4_with_journal_data_upgrades_to_5` | `(4, 7, [0005, 0006, 0007])` → `(4, 8, […, 0008])`; event `to_version` 7 → 8 |
| 17 | tests/test_fte_migration.py | `test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean` | `(3, 7, […])` → `(3, 8, […, 0008])` |
| 18 | tests/test_s06_static.py | `test_version_and_syntax` | `0.7.0.dev2` → `0.8.0.dev2` |
| 19 | tests/test_s06_artifacts.py | `test_0006_is_additive_and_prior_migrations_unchanged` | fresh schema 7 → 8 |
| 20 | tests/test_s06_artifacts.py | `test_schema5_upgrade_to_6_with_backup_and_rollback` | `(5, 7, [0006, 0007])` → `(5, 8, [0006, 0007, 0008])` |
| 21 | tests/test_s07_operational.py | `test_0007_additive_and_0001_0006_unchanged` | 0007 is the 7th migration (`[6]`) instead of the last; fresh schema 7 → 8 |
| 22 | tests/test_s07_operational.py | `test_upgrade_6_to_7_backup_first_and_rollback` | `(6, 7, [0007])` → `(6, 8, [0007, 0008])` |
| 23 | tests/test_s07_operational.py | `test_docs_and_version` | `0.7.0.dev2` → `0.8.0.dev2` |

## Evidence that nothing else changed

- `evidence/s07v02_unchanged_tests_with_s08.json`: the **unmodified** S07 v02 `tests/` tree against the S08 code; exactly the
  tests above fail, each on a literal.
- The architect's S07 reproducer (6/6), S06 reproducer (4/4), S05 reproducer (7/7) and S04J multi-ISSN (3/3), the S05 and S06
  pilots and the S07 evaluation run unmodified on the S08 code.

## S08 v02: changes to S08 v01 tests (not accepted tests)

The S08 v02 corrective revision (S08-F01, S08-F02; ADR_S08 ES-D14) changes two assertions of S08 v01's own tests; the
adaptations #14, #15, #18 and #23 above read `0.8.0.dev2` in v02 and `0.8.0.dev3` in v03. No accepted S00-S07 assertion changed.

| Test | Old | New | Why |
|---|---|---|---|
| `test_s08_evidence.ExtractionFlow.test_quote_storage_follows_gate_a3` | withheld reasons `[QUOTE_NOT_STORED_GATE_A3_DENIED]` | `[GATE_A5_DENIED:STORE_LOCALLY_DENY, QUOTE_NOT_STORED_GATE_A3_DENIED, TEXT_NOT_STORED_GATE_A3_DENIED]` | reveal also reports current gate A5 and withheld text fields (stricter) |
| `test_s08_evidence.ProfilesAndExport.test_export_is_metadata_only_deterministic_and_rights_safe` | 3 export events | 4 events, phases `RESERVED, PUBLISHED, RESERVED, PUBLISHED` | each publication is a reservation + outcome pair; repeating identical bytes is a no-op |

## S08 v03

The S08 v03 corrective revision (S08-F03; ADR_S08 ES-D15) changes no assertion of an earlier test; only the version literal
(`0.8.0.dev3`) on the marked lines above and in `test_s08_operational` changes.
