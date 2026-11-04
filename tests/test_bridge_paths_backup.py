"""S04-D/E: filesystem safety of bridge paths and files; CAS completeness of bridge references; backup-first upgrade chain."""
import hashlib
import json
import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from tests.bridge_helpers import BridgeCase, copy_native_fulltext, result_row
from tests.helpers import KBTest
from tests.pdf_fixtures import synthetic_pdf, title_page
from tests.s03_helpers import make_schema1_kb, populate_s02, schema1_writer
from tests.test_s03_v02_fixes import cas_file, rebuild_metadata
from scivetra_kb.assets.service import AssetService
from scivetra_kb.bridge import service as bridge_service
from scivetra_kb.bridge.service import BridgeService, guard_path_text
from scivetra_kb.core import migrations, paths
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.cas import referenced_digests
from scivetra_kb.core.db import DB_RELATIVE, _configure, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError, SchemaError, ValidationError
from scivetra_kb.core.manifest import library_manifest
from scivetra_kb.core.migrations import _statements, packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.library.service import LibraryService


class PathTests(BridgeCase):
    def setUp(self):
        super().setUp()
        self.last = self.export()
        self.rows = self.manifest()["rows"]

    def test_bridge_path_arguments_refused(self):
        """IDs: S04-PATH-ARGS, B-PATH-1. UNC, device, drive-relative, '..' traversal, ADS ':' and reserved device names are refused for every bridge path argument before any file is opened; on a simulated POSIX host a Windows drive path is refused (no drive-prefix ambiguity)."""
        cases = [(r"\\server\share\pdfs", "UNSUPPORTED_PATH_FORM"), (r"\\?\C:\pdfs", "UNSUPPORTED_PATH_FORM"), ("C:relative", "DRIVE_RELATIVE_PATH_REFUSED"),
                 (os.path.join(self.tmp, "a", "..", "b"), "TRAVERSAL_REFUSED"), (os.path.join(self.tmp, "x.csv:stream"), "ADS_OR_COLON_IN_PATH"),
                 (os.path.join(self.tmp, "CON"), "RESERVED_NAME_REFUSED"), (os.path.join(self.tmp, "nul.csv"), "RESERVED_NAME_REFUSED"),
                 (os.path.join(self.tmp, "trail. "), "RESERVED_NAME_REFUSED"), ("", "PATH_INVALID")]
        for text, code in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as ctx:
                    guard_path_text(text, "--pdf-folder")
                self.assertEqual(ctx.exception.code, code)
                self.assertNotIn(text or "\x01", str(ctx.exception))
                with self.assertRaises(ValidationError):
                    self.run_import(os.path.join(self.tmp, "results.csv"), folder=text or "")
        # S04-F02: on a simulated POSIX host a drive-relative form keeps the S04 code; absolute Windows paths stay WINDOWS_PATH_ON_POSIX.
        with mock.patch.object(paths, "host_is_windows", return_value=False):
            for text, code in (("D:pdfs", "DRIVE_RELATIVE_PATH_REFUSED"), ("C:relative", "DRIVE_RELATIVE_PATH_REFUSED"),
                               ("E:ResearchKB", "DRIVE_RELATIVE_PATH_REFUSED"), ("D:records/sub", "DRIVE_RELATIVE_PATH_REFUSED"),
                               ("D:\\pdfs", "WINDOWS_PATH_ON_POSIX"), ("D:/pdfs", "WINDOWS_PATH_ON_POSIX"),
                               ("E:\\ResearchKB", "PROTECTED_DATA_ROOT"), ("E:\\ResearchKB\\papers", "PROTECTED_DATA_ROOT"),
                               ("e:/researchkb/papers", "PROTECTED_DATA_ROOT"),
                               ("rel\\pdfs", "WINDOWS_PATH_ON_POSIX"), ("\\\\server\\share\\pdfs", "UNSUPPORTED_PATH_FORM")):
                with self.subTest(posix=text):
                    with self.assertRaises(KBError) as ctx:
                        guard_path_text(text, "--pdf-folder")
                    self.assertEqual(ctx.exception.code, code)
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM bridge_attempt")["n"], 0)

    def test_refused_paths_create_nothing(self):
        """IDs: S04-F02-NOCREATE. Every refused bridge path form (drive-relative, protected root, UNC/device, traversal, ADS, reserved
        name, backslash form on POSIX), given to import as --pdf-folder or --result and to export as --out-dir, on the real host and on a
        simulated POSIX host, leaves the working directory and the test root byte-for-byte unchanged: no file or folder named after the
        refused text is created and the KB gains no attempt, batch or row."""
        sandbox = Path(self.tmp, "cwd")
        sandbox.mkdir()
        forms = ["C:relative", "D:records/sub", "E:ResearchKB", "E:\\ResearchKB", "E:\\ResearchKB\\pdfs", "\\\\server\\share\\pdfs",
                 "\\\\?\\C:\\pdfs", "a/../b", "x.csv:stream", "CON", "nul.csv", "rel\\pdfs"]

        def tree():
            return sorted((p.relative_to(self.tmp).as_posix(), p.is_dir(), p.stat().st_size if p.is_file() else 0, p.stat().st_mtime_ns)
                          for p in Path(self.tmp).rglob("*")
                          if p.name not in (DB_RELATIVE.name, ".rkb-writer.lock") and "-wal" not in p.name and "-shm" not in p.name)

        with self.reader() as kb:
            counts = [kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("bridge_batch", "bridge_row", "bridge_attempt", "bridge_result_import")]
        old_cwd = os.getcwd()
        os.chdir(sandbox)
        try:
            before = tree()
            for posix in (False, True):
                with mock.patch.object(paths, "host_is_windows", return_value=False if posix else paths.host_is_windows()):
                    for text in forms:
                        if text == "rel\\pdfs" and paths.host_is_windows():
                            continue  # an ordinary relative path on Windows, not a refused form there
                        with self.subTest(text=text, simulated_posix=posix):
                            with self.assertRaises((ValidationError, KBError)) as ctx:
                                guard_path_text(text, "--pdf-folder")
                            if text.startswith("E:\\ResearchKB"):
                                # refused lexically before any stat/open, with the same code on every host
                                self.assertEqual(ctx.exception.code, "PROTECTED_DATA_ROOT")
                            with self.assertRaises((ValidationError, KBError)):
                                self.run_import(os.path.join(self.tmp, "results.csv"), folder=text)
                            with self.assertRaises((ValidationError, KBError)):
                                self.run_import(text)
                            with self.assertRaises((ValidationError, KBError)):
                                self.export(out_dir=text)
            self.assertEqual(tree(), before)
            self.assertEqual(list(sandbox.iterdir()), [])
        finally:
            os.chdir(old_cwd)
        with self.reader() as kb:
            self.assertEqual([kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("bridge_batch", "bridge_row", "bridge_attempt", "bridge_result_import")],
                             counts)

    def test_links_toctou_size_and_traversal_names(self):
        """IDs: S04-PATH-FILES, N18, N19. A junction/symlink PDF folder is REPARSE_POINT; a symlink in place of save_as, a file changed during the streamed copy and an oversized file are REJECTED_FILE (REPARSE_POINT, FILE_CHANGED_DURING_IMPORT, TOO_LARGE); a traversal save_as in the result is ROW_UNEXPECTED and never opened; no asset, CAS object or staging file remains."""
        r = self.rows
        outside = Path(self.tmp, "outside")
        outside.mkdir()
        (outside / "real.pdf").write_bytes(self.pdf_for(r[0]))
        linked_folder = os.path.join(self.tmp, "linked-folder")
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), linked_folder)
        else:
            os.symlink(outside, linked_folder)
        res = self.write_result([result_row(r[0], "Completed")])
        with self.assertRaises(ValidationError) as ctx:
            self.run_import(res, folder=linked_folder, allow_different_folder=True)
        self.assertEqual(ctx.exception.code, "REPARSE_POINT")
        folder = Path(self.folder())
        symlinked = False
        try:
            os.symlink(outside / "real.pdf", folder / r[0]["save_as"])
            symlinked = True
        except OSError:
            (folder / r[0]["save_as"]).write_bytes(self.pdf_for(r[0]))
        self.put_pdf(r[1])
        big = synthetic_pdf([title_page(title="Synthetic Work 3", authors=["First Synthetic"], doi=r[2]["export_doi"], body=["filler " * 400])])
        self.put_pdf(r[2], big)
        done = []

        def grow(target):
            if Path(target).name == r[1]["save_as"] and not done:
                done.append(1)
                with open(target, "ab") as handle:
                    handle.write(b"% appended concurrently\n")
                os.utime(target, ns=(1, 1))
        rows = [result_row(x, "Completed") for x in r[:3]] + [result_row(r[3], "Completed", save_as="..\\..\\kb\\db\\kb.sqlite")]
        self.assertLess(len(self.pdf_for(r[1])) + 100, len(big))
        with mock.patch.object(bridge_service, "MAX_FILE_BYTES", len(big) - 1):
            code, out = self.run_import(self.write_result(rows, name="r2.csv"), asset_faults={"during_copy": grow})
        with self.reader() as kb:
            codes = {e["bridge_row_id"]: json.loads(e["reason_codes_json"]) for e in kb.all("SELECT * FROM bridge_file_event")}
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
        if symlinked:
            self.assertEqual(codes[r[0]["bridge_row_id"]], ["REPARSE_POINT"])
        self.assertEqual(codes[r[1]["bridge_row_id"]], ["FILE_CHANGED_DURING_IMPORT"])
        self.assertEqual(codes[r[2]["bridge_row_id"]], ["TOO_LARGE"])
        self.assertIn("ROW_UNEXPECTED", [a["code"] for a in out["anomalies"]])
        self.assertEqual(list(Path(self.root, "staging").iterdir()) if Path(self.root, "staging").exists() else [], [])
        self.assertTrue((outside / "real.pdf").exists())


class BridgeBackupTests(BridgeCase):
    def setUp(self):
        super().setUp()
        self.last = self.export()
        self.rows = self.manifest()["rows"]
        self.put_pdf(self.rows[0])
        self.run_import(self.write_result([result_row(self.rows[0], "Completed"), result_row(self.rows[1], "LoginRequired")]))
        with self.reader() as kb:
            self.batch = kb.one("SELECT * FROM bridge_batch")
            self.refs = referenced_digests(kb.connection, kb.schema["schema_version"])
        self.target = os.path.join(self.tmp, "operator-backups")

    def test_bridge_cas_references_in_backup_doctor_restore(self):
        """IDs: S04-BACKUP-REFS, S04-D08, S03-F02. The bridge manifest and input CSV objects are schema-3 CAS references: a healthy backup verifies and restores with identical bridge status and doctor --full clean; a missing manifest object is CAS_MISSING in doctor and blocks create_backup; a self-consistent rebuilt backup without the input CSV object is refused by verify and restore."""
        for column in ("manifest_bytes_sha256", "input_csv_bytes_sha256"):
            self.assertIn(f"bridge_batch.{column}", self.refs[self.batch[column]])
        report = doctor(self.root, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["bridge_invariants"].values()), {0})
        folder = create_backup(self.root, self.target, clock=self.clock)["backup_dir"]
        verified = verify_backup(folder)
        self.assertEqual(verified["kb_schema_version"], 11)  # S04J adaptation (docs/S04J_TEST_ADAPTATIONS.md #9); S05: docs/S05_TEST_ADAPTATIONS.md #9; S06: docs/S06_TEST_ADAPTATIONS.md #9; S07: docs/S07_TEST_ADAPTATIONS.md #9 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #9 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #8 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #8 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #8 (after S10, S09, S08, S07, S06)
        self.assertIn("bridge_manifest_sha256", verified["domain"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(folder, restored)
        with open_reader(restored) as kb:
            restored_status = BridgeService(kb).status(self.batch["batch_id"])
        self.assertEqual(restored_status, self.status())
        bad = os.path.join(self.tmp, "incomplete")
        shutil.copytree(folder, bad)
        cas_file(bad, self.batch["input_csv_bytes_sha256"]).unlink()
        rebuild_metadata(bad)
        for call in (lambda: verify_backup(bad), lambda: restore_backup(bad, os.path.join(self.tmp, "r2"))):
            with self.assertRaises(KBError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "BACKUP_CAS_REFERENCE_MISSING")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "r2")))
        cas_file(self.root, self.batch["manifest_bytes_sha256"]).unlink()
        self.assertIn(f"CAS_MISSING:{self.batch['manifest_bytes_sha256']}", doctor(self.root, full=True)["problems"])
        self.clock.advance(seconds=1)
        with self.assertRaises(KBError) as ctx:
            create_backup(self.root, os.path.join(self.tmp, "b2"), clock=self.clock)
        self.assertEqual(ctx.exception.code, "BACKUP_SOURCE_INCOMPLETE")
        with self.writer() as kb:
            with self.assertRaises(KBError):
                BridgeService(kb).import_results(batch_id=self.batch["batch_id"], result_csv=self.write_result([result_row(self.rows[2], "Failed")], name="x.csv"),
                                                 pdf_folder=self.folder(), operator="op.c")


def make_schema2_kb(root, *, clock, ids):
    """An S03 v02 schema-2 KB: migrations 0001 + 0002 only (exact packaged bytes)."""
    os.makedirs(os.path.join(root, "db"))
    os.makedirs(os.path.join(root, "cas", "sha256"))
    connection = sqlite3.connect(os.path.join(root, DB_RELATIVE), isolation_level=None)
    try:
        _configure(connection)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at_utc TEXT NOT NULL)")
        for version, name, digest, script in packaged_migrations()[:2]:
            for statement in _statements(script):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migration VALUES (?,?,?,?)", (version, name, digest, clock.now_utc_text()))
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute("INSERT INTO kb_instance VALUES (?,?,?)", (ids.new("kbi"), clock.now_utc_text(), "0.3.0.dev2"))
        connection.execute("COMMIT")
    finally:
        connection.close()


class UpgradeChainTests(KBTest):
    def test_schema2_to_3_backup_first_and_rollback(self):
        """IDs: S04-UPGRADE-2-3, S01-10. A schema-2 (S03 v02) KB with a library import and a registered asset is refused by every normal open (KB_UPGRADE_REQUIRED) and by the bridge; 'rkb upgrade' backs up first, keeps every 0001/0002 table byte-identical and the S03 library manifest identical, then the bridge works; an S03-only client refuses the schema-3 KB (KB_CLIENT_TOO_OLD); the pre-upgrade file rolls back to schema 2 with identical bytes."""
        v2 = os.path.join(self.tmp, "kb_v2")
        make_schema2_kb(v2, clock=self.clock, ids=self.ids)
        corpus, fulltext = copy_native_fulltext(self.tmp)
        fixtures = os.path.join(self.tmp, "fixtures")
        os.makedirs(fixtures)
        Path(fixtures, "a.pdf").write_bytes(synthetic_pdf([title_page(title="Synthetic Work 1", authors=["First Synthetic"], doi="10.1234/test1")]))
        with schema1_writer(v2, clock=self.clock, ids=self.ids) as kb:
            LibraryService(kb).import_release(corpus, workspace_label="ws", operator="op.a")
            paper = kb.one("SELECT paper_id FROM project_paper_reference ORDER BY rowid LIMIT 1")["paper_id"]
            svc = AssetService(kb)
            dv = svc.add_document_version(paper_id=paper, version_class="UNKNOWN", evidence=[], operator="op.a")["document_version_id"]
            svc.register_asset(fixture_root=fixtures, relative_path="a.pdf", document_version_id=dv, operator="op.a")
            before_library = library_manifest(kb)["manifest_sha256"]
        db = Path(v2, DB_RELATIVE)
        original = hashlib.sha256(db.read_bytes()).hexdigest()
        with self.assertRaises(SchemaError) as ctx:
            open_writer(v2).__enter__()
        self.assertEqual(ctx.exception.code, "KB_UPGRADE_REQUIRED")
        out = upgrade_kb(v2, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"]), (2, 11))  # S04J adaptation (docs/S04J_TEST_ADAPTATIONS.md #10); S05: docs/S05_TEST_ADAPTATIONS.md #10; S06: docs/S06_TEST_ADAPTATIONS.md #10; S07: docs/S07_TEST_ADAPTATIONS.md #10 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #10 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #9 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #9 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #9 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"] and out["checks"]["library_manifest_identical"])
        self.assertTrue(verify_backup(os.path.join(self.tmp, "backups", out["backup"]["backup_name"]))["valid"])
        with open_reader(v2) as kb:
            self.assertEqual(library_manifest(kb)["manifest_sha256"], before_library)
        with open_writer(v2, clock=self.clock, ids=self.ids) as kb:
            exported = BridgeService(kb).export_batch(source_dir=fulltext, kind="rv-manual-queue", workspace_label="ws",
                                                      out_dir=os.path.join(self.tmp, "batch"), operator="op.b")
        self.assertEqual(exported["row_count"], 5)
        self.assertEqual(doctor(v2, full=True)["problems"], [])
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:2]):
            with self.assertRaises(SchemaError) as ctx:
                open_reader(v2).__enter__()
        self.assertEqual(ctx.exception.code, "KB_CLIENT_TOO_OLD")
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v2, rollback)
        shutil.copyfile(Path(v2, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(hashlib.sha256(Path(rollback, DB_RELATIVE).read_bytes()).hexdigest(), original)
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:2]):
            with open_reader(rollback) as kb:
                self.assertEqual((kb.schema["schema_version"], library_manifest(kb)["manifest_sha256"]), (2, before_library))

    def test_schema1_to_3_and_missing_reference_blocks_upgrade(self):
        """IDs: S04-UPGRADE-1-3. An S02 schema-1 KB upgrades directly to schema 3 with all S02 tables byte-identical; a missing provider-snapshot CAS object blocks the backup-first upgrade and leaves the KB unchanged."""
        v1 = os.path.join(self.tmp, "kb_v1")
        make_schema1_kb(v1, clock=self.clock, ids=self.ids)
        with schema1_writer(v1, clock=self.clock, ids=self.ids) as kb:
            populate_s02(kb, self.tmp, self)
            snapshot = kb.one("SELECT response_bytes_sha256 d FROM provider_snapshot WHERE response_bytes_sha256 IS NOT NULL LIMIT 1")["d"]
        broken = os.path.join(self.tmp, "kb_v1_broken")
        shutil.copytree(v1, broken)
        out = upgrade_kb(v1, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"]), (1, 11))  # S04J adaptation (docs/S04J_TEST_ADAPTATIONS.md #11); S05: docs/S05_TEST_ADAPTATIONS.md #11; S06: docs/S06_TEST_ADAPTATIONS.md #11; S07: docs/S07_TEST_ADAPTATIONS.md #11 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #11 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #10 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #10 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #10 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["s02_tables_byte_identical"] and out["checks"]["prior_tables_byte_identical"])
        cas_file(broken, snapshot).unlink()
        before = hashlib.sha256(Path(broken, DB_RELATIVE).read_bytes()).hexdigest()
        self.clock.advance(seconds=1)
        with self.assertRaises(KBError) as ctx:
            upgrade_kb(broken, os.path.join(self.tmp, "backups2"), clock=self.clock, ids=self.ids)
        self.assertEqual(ctx.exception.code, "BACKUP_SOURCE_INCOMPLETE")
        self.assertEqual(hashlib.sha256(Path(broken, DB_RELATIVE).read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
