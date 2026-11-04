# S02 tests adapted in S03 (schema-version fixtures only)

All 163 S02 v03 tests are still present and pass. Five of them assert schema-version facts that necessarily change when migration 0002 is packaged. They were adapted, each with an in-code comment pointing here, and each has an equivalent or stronger regression test. No other S02 test body changed. All other S02 behaviour (including the S02 semantic manifest `2bb3c832…63ed0548a` of the idempotency fixture) is unchanged.

| # | Test | v03 assertion | S03 assertion | Justification | Equivalent regression |
|---|---|---|---|---|---|
| 1 | `test_core.InitAndOpenTests.test_init_is_explicit_and_refuses_reuse` (CORE-INIT-01) | fresh init has schema 1 | fresh init has schema 2 | `init` applies all packaged migrations | same test; `test_s03_cli` (schema_version 2) |
| 2 | `test_core.InitAndOpenTests.test_refuses_future_schema` (N15, CORE-SCHEMA-01) | injected ledger row 2 = future | injected ledger row 3 = future | version 2 is now a packaged migration, so row 2 is drift, not "future" | `test_upgrade_backup.RefusalTests.test_future_schema_refused_everywhere` |
| 3 | `test_core.InitAndOpenTests.test_packaged_migration_is_contiguous` (CORE-SCHEMA-04) | versions `[1]` | versions `[1, 2]`, and 0001 SHA-256 equals the accepted `2c07dd34…71f38b` | 0002 added | `test_library_schema.MigrationShapeTests.test_0002_is_additive` |
| 4 | `test_core.ImmutabilityTests.test_every_table_rejects_update_and_delete` (CORE-IMMUTABLE-01) | all 22 tables | the 22 tables of 0001 + ledger (explicit list) | the S02 populate routine cannot fill S03 tables | `test_library_schema.ImmutabilityTests.test_every_0002_table_rejects_update_and_delete` (CORE-IMMUTABLE-03) covers all 22 new tables |
| 5 | `test_cli.CliTests.test_cli_full_offline_path` (CLI-01) | `init` reports schema 1 | schema 2 | as #1 | `test_s03_cli.S03CliTests.test_cli_full_s03_path` |

Non-test S02 code touched by S03 (behaviour for accepted inputs unchanged):

- `core/migrations.py`: `ledger_status`, `apply_pending`, `compare_ledger(allow_older=…)`; the `KB_UPGRADE_REQUIRED` message now names `rkb upgrade`.
- `core/db.py`: private `_allow_older_schema` flag (backup/upgrade only, STATIC-09).
- `core/cas.py`: streaming `put_file` / `verify_object`.
- `core/doctor.py`: library CAS references, `--full` invariants and manifests.
- `core/manifest.py`: `library_manifest`, `full_manifest`, `table_content_sha256`; `semantic_manifest` (rkb-semantic-manifest/1) is unchanged.
- `metadata/rv_import.py`: the shared export reader is hardened (strict JSON, duplicate keys, caps, shape, CSV consistency; closes S02 B13/B17). `import_corpus_export` keeps its S02 minting behaviour.
- `metadata/store.py`: optional `scope_semantic` parameter of `add_assertion` (default keeps the S02 hash).
