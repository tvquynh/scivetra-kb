import os
import sqlite3
import tempfile
import shutil
import unittest
from pathlib import Path

from tests.helpers import KBTest
from scivetra_kb.core.cas import ContentStore
from scivetra_kb.core.db import DB_RELATIVE, init_kb, open_reader, open_writer
from scivetra_kb.core.errors import (ImmutableRecordError, IntegrityError, LockError, NotInitializedError, PolicyRefusal,
                                     SchemaError, ValidationError)
from scivetra_kb.core.lock import writer_lock
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.paths import safe_child, safe_data_root

S02_TABLES = {"kb_instance", "global_paper", "identity_assertion", "provider_snapshot", "fetch_run", "provider_check", "request_reservation",
              "request_completion", "metadata_claim", "ra_claim", "metadata_change_event", "integrity_event", "integrity_event_sighting",
              "integrity_coverage", "operator_document", "integrity_counter_evidence", "review_task", "integrity_review",
              "normalization_divergence", "identifier_warning", "reprovane_export_import", "schema_migration"}


class InitAndOpenTests(KBTest):
    def test_init_is_explicit_and_refuses_reuse(self):
        """IDs: CORE-INIT-01. ``init`` creates the packaged schema (2 since S03) once; a non-empty data root is refused."""
        # S03 adaptation (docs/S02_TEST_ADAPTATIONS.md #1): fresh init now applies 0001 + 0002.
        with self.reader() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #1); S04J: docs/S04J_TEST_ADAPTATIONS.md #1; S05: docs/S05_TEST_ADAPTATIONS.md #1; S06: docs/S06_TEST_ADAPTATIONS.md #1; S07: docs/S07_TEST_ADAPTATIONS.md #1 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #1 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #1 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #1 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #1 (after S10, S09, S08, S07, S06)
        with self.assertRaises(ValidationError) as ctx:
            init_kb(self.root)
        self.assertEqual(ctx.exception.code, "DATA_ROOT_NOT_EMPTY")

    def test_read_commands_never_create(self):
        """IDs: CORE-INIT-02, N15. Opening a missing KB fails and creates nothing (no implicit init/migration)."""
        missing = os.path.join(self.tmp, "absent")
        with self.assertRaises(ValidationError):
            open_reader(missing).__enter__()
        with self.assertRaises(ValidationError):
            open_writer(missing).__enter__()
        self.assertFalse(os.path.exists(missing))
        empty = os.path.join(self.tmp, "empty")
        os.mkdir(empty)
        with self.assertRaises(NotInitializedError):
            open_reader(empty).__enter__()
        self.assertEqual(os.listdir(empty), [])

    def test_reader_is_read_only(self):
        """IDs: CORE-INIT-03. A reader handle cannot write (SQLite mode=ro)."""
        with self.reader() as kb:
            with self.assertRaises(Exception):
                kb.connection.execute("INSERT INTO kb_instance VALUES ('x','2026-01-01T00:00:00.000000Z','x')")

    def _tamper(self, sql):
        con = sqlite3.connect(Path(self.root) / DB_RELATIVE)
        con.executescript("PRAGMA recursive_triggers=OFF; DROP TRIGGER schema_migration_no_update; DROP TRIGGER schema_migration_no_delete;" + sql)
        con.close()

    def test_refuses_future_schema(self):
        """IDs: N15, CORE-SCHEMA-01. A database with an unknown future migration is refused (KB_CLIENT_TOO_OLD)."""
        # S03 adaptation (docs/S02_TEST_ADAPTATIONS.md #2): 0002 is now packaged, so the "future" migration is 0003.
        # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #2): 0003 is now packaged, so the "future" migration is 0004.
        # S04J adaptation (docs/S04J_TEST_ADAPTATIONS.md #2): 0004 is now packaged, so the "future" migration is 0005.
        # S04J/S05: docs/S05_TEST_ADAPTATIONS.md #2: 0005 is now packaged, so the "future" migration is 0006.
        self._tamper("INSERT INTO schema_migration VALUES (12,'0012_future.sql','" + "f" * 64 + "','2026-01-01T00:00:00.000000Z'); PRAGMA user_version=12;")  # S06: docs/S06_TEST_ADAPTATIONS.md #2; S07: docs/S07_TEST_ADAPTATIONS.md #2 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #2 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #2 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #2 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #2 (after S10, S09, S08, S07, S06)
        with self.assertRaises(SchemaError) as ctx:
            open_reader(self.root).__enter__()
        self.assertEqual(ctx.exception.code, "KB_CLIENT_TOO_OLD")

    def test_refuses_checksum_drift(self):
        """IDs: N15, CORE-SCHEMA-02. A ledger checksum that differs from the packaged bytes is refused (tamper-evident, not authentication)."""
        self._tamper("UPDATE schema_migration SET sha256='" + "0" * 64 + "' WHERE version=1;")
        with self.assertRaises(SchemaError) as ctx:
            open_writer(self.root).__enter__()
        self.assertEqual(ctx.exception.code, "MIGRATION_DRIFT")

    def test_refuses_older_schema_without_upgrading(self):
        """IDs: N15, CORE-SCHEMA-03. A database below the packaged version is refused, never migrated implicitly."""
        self._tamper("DELETE FROM schema_migration; PRAGMA user_version=0;")
        before = (Path(self.root) / DB_RELATIVE).read_bytes()
        with self.assertRaises(SchemaError):
            open_writer(self.root).__enter__()
        self.assertEqual((Path(self.root) / DB_RELATIVE).read_bytes(), before)

    def test_packaged_migration_is_contiguous(self):
        """IDs: CORE-SCHEMA-04. Packaged migrations are contiguous and checksummed."""
        # S03 adaptation (docs/S02_TEST_ADAPTATIONS.md #3): 0002 added; 0001 must keep its accepted exact bytes.
        migrations = packaged_migrations()
        self.assertEqual([m[0] for m in migrations], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])  # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #3); S04J #3; S05: docs/S05_TEST_ADAPTATIONS.md #3; S06: docs/S06_TEST_ADAPTATIONS.md #3; S07: docs/S07_TEST_ADAPTATIONS.md #3 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #3 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #3 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #3 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #3 (after S10, S09, S08, S07, S06)
        self.assertEqual(migrations[0][2], "2c07dd3461458695465693a53faaebeded0b7ed850dd275c4e4b93698b71f38b")
        self.assertTrue(all(len(m[2]) == 64 for m in migrations))


class ImmutabilityTests(KBTest):
    def populate_every_table(self):
        """Drive the real code paths until every table holds at least one row (triggers only fire on existing rows)."""
        from tests.helpers import cr_work, openalex_retraction_claim, rv_item, structured, write_rv_export
        from scivetra_kb.metadata import crossref
        from scivetra_kb.metadata.integrity import IntegrityService
        from scivetra_kb.metadata.rv_import import import_corpus_export
        from scivetra_kb.metadata.store import MetadataStore
        from scivetra_kb.metadata.transport import FixtureTransport
        doi = "10.99999/imm.1"
        export = write_rv_export(os.path.join(self.tmp, "rv"), [
            rv_item("cri_1", dois=[(doi, doi)], integrity=openalex_retraction_claim()),
            rv_item("cri_2", dois=[("https://doi.org/10.99999/a%2Cb", "10.99999/a%2cb")]),
            rv_item("cri_3", dois=[("10.99999/imm.1 ", "10.99999/imm.1")])])
        ret = {"DOI": "10.99999/imm.n", "type": "retraction", "source": "publisher"}
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = crossref.build_url(doi)
        ft.routes[url] = [__import__("tests.helpers", fromlist=["resp"]).resp(200, cr_work(doi, title=["A"], **{"updated-by": [ret]}), url),
                          __import__("tests.helpers", fromlist=["resp"]).resp(200, cr_work(doi, title=["B"]), url)]
        with self.writer() as kb:
            import_corpus_export(kb, export, workspace_label="ws")
            service = self.service(kb, ft)
            service.fetch([structured(doi)])
            service.fetch_provider("CROSSREF", structured(doi))
            integrity = IntegrityService(MetadataStore(kb))
            event = kb.one("SELECT event_id FROM integrity_event WHERE reported_by='CROSSREF_PUBLISHER_UPDATE'")["event_id"]
            text = "Notice: the retraction was issued in error."
            with kb.transaction():
                doc = integrity.register_document("reviewer.a", text.encode(), "synthetic")
                start = text.index("issued in error")
                ce = integrity.record_counter_evidence(operator="reviewer.a", opposes_event_id=event, kind="NOTICE_STATES_ERROR",
                                                       primary_document_ref="sha256:" + doc["document_sha256"],
                                                       evidence_anchor={"document_sha256": doc["document_sha256"], "page": 1, "char_start": start, "char_end": start + 15},
                                                       statement_summary="error notice")
                integrity.record_review(operator="reviewer.b", subject_namespace="doi", subject_value=doi, event_ids=[event],
                                        trigger="OPERATOR_REQUEST", decision="REPORT_NOT_APPLICABLE_WITH_EVIDENCE", reason="evidenced",
                                        counter_evidence_ids=[ce["counter_evidence_id"]])

    def test_every_table_rejects_update_and_delete(self):
        """IDs: CORE-IMMUTABLE-01. With every table populated, UPDATE and DELETE are rejected on every table, including the migration ledger."""
        self.populate_every_table()
        with self.writer() as kb:
            # S03 adaptation (docs/S02_TEST_ADAPTATIONS.md #4): this S02 test covers exactly the 0001 tables + the ledger;
            # the 22 tables added by 0002 are populated and checked by tests/test_library_schema.py (CORE-IMMUTABLE-03).
            tables = [r[0] for r in kb.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            tables = [t for t in tables if t in S02_TABLES]
            self.assertEqual(len(tables), 22)  # 21 from migration 0001 + schema_migration
            for table in tables:
                with self.subTest(table=table):
                    self.assertGreater(kb.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0, f"{table} not populated")
                    with self.assertRaises(ImmutableRecordError):
                        kb.execute(f"UPDATE {table} SET rowid = rowid")
                    with self.assertRaises(ImmutableRecordError):
                        kb.execute(f"DELETE FROM {table}")

    def test_replace_cannot_bypass_triggers(self):
        """IDs: CORE-IMMUTABLE-02. INSERT OR REPLACE on an existing row fires the delete guard (recursive_triggers=ON)."""
        with self.writer() as kb:
            row = kb.one("SELECT * FROM kb_instance")
            with self.assertRaises(ImmutableRecordError):
                kb.execute("INSERT OR REPLACE INTO kb_instance VALUES (?,?,?)", (row["instance_id"], row["created_at_utc"], "tampered"))


class CasAndPathTests(KBTest):
    def test_cas_roundtrip_and_hash_mismatch(self):
        """IDs: CORE-CAS-01. CAS stores by SHA-256; a modified object is detected on read."""
        cas = ContentStore(Path(self.root))
        digest = cas.put(b"synthetic evidence")
        self.assertEqual(cas.read(digest), b"synthetic evidence")
        self.assertEqual(cas.put(b"synthetic evidence"), digest)
        cas.path_for(digest).write_bytes(b"tampered")
        with self.assertRaises(IntegrityError) as ctx:
            cas.read(digest)
        self.assertEqual(ctx.exception.code, "CAS_HASH_MISMATCH")
        with self.assertRaises(IntegrityError):
            cas.put(b"synthetic evidence")  # existing object verified, never overwritten

    def test_cas_rejects_invalid_hash(self):
        """IDs: CORE-CAS-02, N01. Non-hex or traversal-shaped digests are refused."""
        cas = ContentStore(Path(self.root))
        for bad in ("../x", "A" * 64, "z" * 64, ""):
            with self.assertRaises(ValidationError):
                cas.path_for(bad)

    def test_path_traversal_refused(self):
        """IDs: CORE-PATH-01, N01. Relative children may not escape their root."""
        for bad in ("../outside", "a/../../b", os.path.abspath(self.tmp)):
            with self.assertRaises(ValidationError):
                safe_child(Path(self.root), bad)

    def test_device_and_unc_paths_refused(self):
        """IDs: CORE-PATH-02, N01. Device and UNC data roots are refused."""
        for bad in (r"\\?\C:\x", r"\\.\pipe\x", r"\\server\share\kb"):
            with self.assertRaises(ValidationError):
                safe_data_root(bad, must_exist=False)

    def test_protected_production_root_refused(self):
        """IDs: CORE-PATH-03. The production data root E:\\ResearchKB is refused without OAG-2 (no write is attempted)."""
        with self.assertRaises(PolicyRefusal) as ctx:
            init_kb(r"E:\ResearchKB")
        self.assertEqual(ctx.exception.code, "PROTECTED_DATA_ROOT")
        with self.assertRaises(PolicyRefusal):
            safe_data_root(r"E:\ResearchKB\sub", must_exist=False)

    def test_symlink_or_junction_data_root_refused(self):
        """IDs: CORE-PATH-04, N01. A data root reached through a symlink/junction is refused."""
        target = os.path.join(self.tmp, "real")
        os.mkdir(target)
        link = os.path.join(self.tmp, "link")
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            import subprocess
            result = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
            if result.returncode != 0:
                self.fail("Could not create a symlink or junction for the test")
        with self.assertRaises(ValidationError) as ctx:
            safe_data_root(link, must_exist=True)
        self.assertEqual(ctx.exception.code, "REPARSE_POINT")

    def test_writer_lock_is_exclusive(self):
        """IDs: CORE-LOCK-01. A second concurrent writer is refused."""
        with writer_lock(Path(self.root)):
            with self.assertRaises(LockError):
                with writer_lock(Path(self.root)):
                    pass


if __name__ == "__main__":
    unittest.main()
