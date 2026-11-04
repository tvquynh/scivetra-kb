"""S03-A: explicit backup-first upgrade from S02 schema 1, backup / verify-backup / restore, refusal of implicit migration."""
import hashlib
import json
import os
import shutil
import sqlite3
import unittest
from pathlib import Path

from tests.helpers import KBTest
from tests.s03_helpers import copy_native, make_schema1_kb, populate_s02, schema1_writer
from scivetra_kb.core.canonical import semantic_sha256
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import IntegrityError, KBError, SchemaError, ValidationError
from scivetra_kb.core.manifest import full_manifest, semantic_manifest, table_content_sha256
from scivetra_kb.core.upgrade import S02_TABLES, upgrade_kb
from scivetra_kb.library.service import LibraryService


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Schema1Case(KBTest):
    def setUp(self):
        super().setUp()
        self.v1 = os.path.join(self.tmp, "kb_v1")
        make_schema1_kb(self.v1, clock=self.clock, ids=self.ids)
        with schema1_writer(self.v1, clock=self.clock, ids=self.ids) as kb:
            populate_s02(kb, self.tmp, self)
            self.v1_manifest = semantic_manifest(kb)
            self.v1_content = {t: table_content_sha256(kb.connection, t) for t in S02_TABLES}
            self.v1_counts = {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in S02_TABLES}
            self.v1_prefix = {t: semantic_sha256([list(r) for r in kb.connection.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()])
                              for t in S02_TABLES}
        self.v1_db = os.path.join(self.v1, DB_RELATIVE)
        self.v1_sha = sha(self.v1_db)
        self.backups = os.path.join(self.tmp, "operator-backups")


class RefusalTests(Schema1Case):
    def test_schema1_refused_by_every_normal_open(self):
        """IDs: N15, S03-UPGRADE-REFUSE-01. A schema-1 KB is refused with KB_UPGRADE_REQUIRED by reader, writer and doctor; nothing is migrated or written."""
        for opener in (open_reader, open_writer):
            with self.subTest(opener=opener.__name__):
                with self.assertRaises(SchemaError) as ctx:
                    opener(self.v1).__enter__()
                self.assertEqual(ctx.exception.code, "KB_UPGRADE_REQUIRED")
        self.assertIn("KB_UPGRADE_REQUIRED", doctor(self.v1)["problems"])
        self.assertEqual(sha(self.v1_db), self.v1_sha)
        self.assertEqual(sorted(os.listdir(os.path.join(self.v1, "db"))), ["kb.sqlite"])

    def test_future_schema_refused_everywhere(self):
        """IDs: N15, S03-UPGRADE-REFUSE-02. A ledger entry beyond the packaged migrations is KB_CLIENT_TOO_OLD for open and for upgrade."""
        con = sqlite3.connect(Path(self.root) / DB_RELATIVE)
        con.executescript("PRAGMA recursive_triggers=OFF; DROP TRIGGER schema_migration_no_update; DROP TRIGGER schema_migration_no_delete;"
                          # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #6): 0003 is packaged, the "future" migration is 0004.
                          # S04J adaptation (docs/S04J_TEST_ADAPTATIONS.md #6): 0004 is packaged, the "future" migration is 0005.
                          # S04J/S05: docs/S05_TEST_ADAPTATIONS.md #6: 0005 is packaged, the "future" migration is 0006.
                          "INSERT INTO schema_migration VALUES (12,'0012_future.sql','" + "f" * 64 + "','2026-01-01T00:00:00.000000Z'); PRAGMA user_version=12;")  # S06: docs/S06_TEST_ADAPTATIONS.md #6; S07: docs/S07_TEST_ADAPTATIONS.md #6 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #6 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #6 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #6 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #6 (after S10, S09, S08, S07, S06)
        con.close()
        for call in (lambda: open_reader(self.root).__enter__(), lambda: upgrade_kb(self.root, self.backups)):
            with self.assertRaises(SchemaError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "KB_CLIENT_TOO_OLD")

    def test_library_import_refused_on_schema1(self):
        """IDs: N15, S03-UPGRADE-REFUSE-03. An import (writer) never upgrades implicitly; DB bytes unchanged."""
        with self.assertRaises(SchemaError):
            with open_writer(self.v1) as kb:
                LibraryService(kb).import_release(copy_native(self.tmp), workspace_label="ws", operator="op.a")
        self.assertEqual(sha(self.v1_db), self.v1_sha)


class UpgradeTests(Schema1Case):
    def test_upgrade_preserves_s02_rows_and_is_reattempt_idempotent(self):
        """IDs: S03-UPGRADE-01, S03-UPGRADE-REATTEMPT, S03-S02-SEMANTIC-COMPARE. Explicit upgrade: backup first, copy-migrate-verify-swap; every 0001 table byte-identical; S02 semantic manifest identical; pre-upgrade file kept; a second run is UPGRADE_NOT_REQUIRED."""
        out = upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids)
        self.assertTrue(out["upgraded"])
        self.assertEqual((out["from_version"], out["to_version"]), (1, 11))  # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #7); S04J #7; S05: docs/S05_TEST_ADAPTATIONS.md #7; S06: docs/S06_TEST_ADAPTATIONS.md #7; S07: docs/S07_TEST_ADAPTATIONS.md #7 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #7 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #7 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #7 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #7 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["s02_tables_byte_identical"] and out["checks"]["s02_semantic_manifest_identical"])
        pre = Path(self.v1, "db", out["pre_upgrade_file"])
        self.assertEqual(sha(pre), self.v1_sha)
        with open_reader(self.v1) as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S04 adaptation #7; S04J #7; S05: docs/S05_TEST_ADAPTATIONS.md #7; S06: docs/S06_TEST_ADAPTATIONS.md #7; S07: docs/S07_TEST_ADAPTATIONS.md #7 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #7 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #7 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #7 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #7 (after S10, S09, S08, S07, S06)
            self.assertEqual(semantic_manifest(kb), self.v1_manifest)
            self.assertEqual({t: table_content_sha256(kb.connection, t) for t in S02_TABLES}, self.v1_content)
            upgrade_row = kb.one("SELECT from_version, to_version, s02_semantic_manifest_sha256 FROM kb_upgrade_event")
        self.assertEqual(upgrade_row, {"from_version": 1, "to_version": 11, "s02_semantic_manifest_sha256": self.v1_manifest["manifest_sha256"]})  # S04 #7; S04J #7; S05: docs/S05_TEST_ADAPTATIONS.md #7; S06: docs/S06_TEST_ADAPTATIONS.md #7; S07: docs/S07_TEST_ADAPTATIONS.md #7 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #7 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #7 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #7 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #7 (after S10, S09, S08, S07, S06)
        self.assertNotEqual(sha(self.v1_db), self.v1_sha)
        again = upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids)
        self.assertEqual((again["upgraded"], again["code"]), (False, "UPGRADE_NOT_REQUIRED"))
        self.assertTrue(verify_backup(os.path.join(self.backups, out["backup"]["backup_name"]))["valid"])
        report = doctor(self.v1, full=True)
        self.assertEqual(report["problems"], [])

    def test_rollback_rehearsal(self):
        """IDs: S03-ROLLBACK-01. Rollback = put the pre-upgrade file back: the KB is schema 1 again with the original bytes and S02 manifest; the backup restores to a new root as schema 1 too."""
        out = upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids)
        shutil.copyfile(Path(self.v1, "db", out["pre_upgrade_file"]), self.v1_db)
        self.assertEqual(sha(self.v1_db), self.v1_sha)
        with self.assertRaises(SchemaError):
            open_reader(self.v1).__enter__()
        restored = restore_backup(os.path.join(self.backups, out["backup"]["backup_name"]), os.path.join(self.tmp, "restored_v1"))
        self.assertEqual((restored["kb_schema_version"], restored["domain"]["s02_semantic_manifest_sha256"]),
                         (1, self.v1_manifest["manifest_sha256"]))

    def failing(self, faults, code, exc_type=KBError):
        with self.assertRaises(exc_type) as ctx:
            upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids, faults=faults)
        if code:
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(sha(self.v1_db), self.v1_sha)
        self.assertEqual(sorted(os.listdir(os.path.join(self.v1, "db"))), ["kb.sqlite"])
        with self.assertRaises(SchemaError):
            open_reader(self.v1).__enter__()

    def test_failed_validation_and_corrupt_copy_leave_original(self):
        """IDs: S03-UPGRADE-FAIL-VALIDATION, S03-UPGRADE-FAIL-CORRUPT. A copy whose verification fails (S02 row changed) or whose bytes are corrupted is discarded; the original DB bytes are unchanged."""
        def mutate(connection):
            connection.execute("DROP TRIGGER kb_instance_no_update")
            connection.execute("UPDATE kb_instance SET client_version='tampered'")
        self.failing({"after_migrate": lambda connection: mutate(connection)}, "UPGRADE_VERIFICATION_FAILED")

        def corrupt(copy_path):
            data = bytearray(Path(copy_path).read_bytes())
            data[100:4096] = b"\x00" * (4096 - 100)
            Path(copy_path).write_bytes(bytes(data))
        with self.assertRaises((KBError, sqlite3.DatabaseError)):
            upgrade_kb(self.v1, os.path.join(self.tmp, "b2"), clock=self.clock, ids=self.ids, faults={"after_copy": corrupt})
        self.assertEqual(sha(self.v1_db), self.v1_sha)
        self.assertEqual(sorted(os.listdir(os.path.join(self.v1, "db"))), ["kb.sqlite"])

    def test_cancellation_before_swap(self):
        """IDs: S03-UPGRADE-CANCEL. An interruption (KeyboardInterrupt) just before the swap removes the copy and leaves the original."""
        def cancel(copy_path):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids, faults={"before_swap": cancel})
        self.assertEqual(sha(self.v1_db), self.v1_sha)
        self.assertEqual(sorted(os.listdir(os.path.join(self.v1, "db"))), ["kb.sqlite"])
        self.clock.advance(seconds=1)
        self.assertTrue(upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids)["upgraded"])     # recovery: re-run succeeds

    def test_backup_failure_and_tampered_ledger(self):
        """IDs: S03-UPGRADE-FAIL-BACKUP, S03-UPGRADE-FAIL-LEDGER. No backup target, a target inside the KB, or a ledger checksum that differs from packaged migration bytes stop the upgrade before any copy."""
        with self.assertRaises(ValidationError) as ctx:
            upgrade_kb(self.v1, "", clock=self.clock, ids=self.ids)
        self.assertEqual(ctx.exception.code, "BACKUP_TARGET_REQUIRED")
        with self.assertRaises(ValidationError) as ctx:
            upgrade_kb(self.v1, os.path.join(self.v1, "inside"), clock=self.clock, ids=self.ids)
        self.assertEqual(ctx.exception.code, "BACKUP_TARGET_INSIDE_KB")

        def interrupted(folder):
            raise OSError("disk full (simulated)")
        with self.assertRaises(OSError):
            upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids, faults={"after_db": interrupted})
        self.assertEqual(sha(self.v1_db), self.v1_sha)
        con = sqlite3.connect(self.v1_db)
        con.executescript("PRAGMA recursive_triggers=OFF; DROP TRIGGER schema_migration_no_update; UPDATE schema_migration SET sha256='" + "0" * 64 + "';")
        con.close()
        tampered = sha(self.v1_db)
        with self.assertRaises(SchemaError) as ctx:
            upgrade_kb(self.v1, os.path.join(self.tmp, "b3"), clock=self.clock, ids=self.ids)
        self.assertEqual(ctx.exception.code, "MIGRATION_DRIFT")
        self.assertEqual(sha(self.v1_db), tampered)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "b3")))

    def test_upgraded_kb_accepts_library_work(self):
        """IDs: S03-UPGRADE-02. After the upgrade the KB imports a native export; the S02 part of the manifest is still the pre-upgrade value."""
        upgrade_kb(self.v1, self.backups, clock=self.clock, ids=self.ids)
        with open_writer(self.v1, clock=self.clock, ids=self.ids) as kb:
            LibraryService(kb).import_release(copy_native(self.tmp), workspace_label="ws", operator="op.a")
            self.assertEqual(kb.one("SELECT count(*) n FROM project_paper_reference")["n"], 3)
            self.assertNotEqual(semantic_manifest(kb), self.v1_manifest)   # the import added S02-layer rows (assertions, papers)
        # Every pre-upgrade S02 row is still present and byte-identical (the import only appended rows).
        with open_reader(self.v1) as kb:
            for table, count in self.v1_counts.items():
                rows = kb.connection.execute(f"SELECT * FROM {table} ORDER BY rowid LIMIT ?", (count,)).fetchall()
                self.assertEqual(semantic_sha256([list(r) for r in rows]), self.v1_prefix[table], table)


class BackupRestoreTests(KBTest):
    def setUp(self):
        super().setUp()
        with self.writer() as kb:
            LibraryService(kb).import_release(copy_native(self.tmp), workspace_label="ws", operator="op.a")
            self.manifest = full_manifest(kb)
        self.target = os.path.join(self.tmp, "operator-backups")

    def test_n25_backup_verify_restore_identical(self):
        """IDs: N25, S03-BACKUP-01, S03-RESTORE-01. Backup to an operator target, verify offline, restore into a NEW root: DB hash, S02 + library semantic manifests and CAS list are identical; doctor --full is clean."""
        out = create_backup(self.root, self.target, clock=self.clock)
        folder = out["backup_dir"]
        manifest_text = Path(folder, "backup_manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(self.tmp, manifest_text)
        self.assertNotIn(os.path.basename(self.tmp), manifest_text)
        self.assertTrue(verify_backup(folder)["valid"])
        new_root = os.path.join(self.tmp, "restored")
        restored = restore_backup(folder, new_root)
        self.assertTrue(all(restored["comparison"].values()))
        with open_reader(new_root) as kb:
            self.assertEqual(full_manifest(kb), self.manifest)
        self.assertEqual(doctor(new_root, full=True)["problems"], [])
        self.assertEqual(doctor(new_root, full=True)["semantic_manifest_v2"], doctor(self.root, full=True)["semantic_manifest_v2"])

    def test_interrupted_and_tampered_backups_refused(self):
        """IDs: S03-BACKUP-INCOMPLETE, S03-BACKUP-TAMPER. A backup interrupted before its manifest is BACKUP_INCOMPLETE; a changed DB or CAS object, an extra file and a non-empty restore target are refused."""
        def stop(folder, digest):
            raise OSError("interrupted (simulated)")
        with self.assertRaises(OSError):
            create_backup(self.root, self.target, clock=self.clock, faults={"after_object": stop})
        partial = [os.path.join(self.target, n) for n in os.listdir(self.target)][0]
        with self.assertRaises(IntegrityError) as ctx:
            verify_backup(partial)
        self.assertEqual(ctx.exception.code, "BACKUP_INCOMPLETE")
        self.clock.advance(seconds=1)
        good = create_backup(self.root, self.target, clock=self.clock)["backup_dir"]
        for name, mutate, code in (
                ("db", lambda d: Path(d, "db", "kb.sqlite").write_bytes(Path(d, "db", "kb.sqlite").read_bytes() + b"\x00"), "BACKUP_HASH_MISMATCH"),
                ("cas", lambda d: next(p for p in Path(d, "cas").rglob("*") if p.is_file()).write_bytes(b"x"), "BACKUP_CAS_MISMATCH"),
                ("extra", lambda d: Path(d, "notes.txt").write_text("x"), "BACKUP_INVENTORY_MISMATCH")):
            with self.subTest(name=name):
                copy = os.path.join(self.tmp, "copy-" + name)
                shutil.copytree(good, copy)
                mutate(copy)
                with self.assertRaises(IntegrityError) as ctx:
                    verify_backup(copy)
                self.assertEqual(ctx.exception.code, code)
        busy = os.path.join(self.tmp, "busy")
        os.makedirs(busy)
        Path(busy, "x").write_text("x")
        with self.assertRaises(ValidationError) as ctx:
            restore_backup(good, busy)
        self.assertEqual(ctx.exception.code, "RESTORE_TARGET_NOT_EMPTY")

    def test_backup_requires_explicit_outside_target(self):
        """IDs: S03-BACKUP-TARGET, OAG-1. No default path: an empty target, a target inside the data root, and the protected production root are refused."""
        for target, code in (("", "BACKUP_TARGET_REQUIRED"), (os.path.join(self.root, "b"), "BACKUP_TARGET_INSIDE_KB"),
                             ("E:\\ResearchKB\\backups", "PROTECTED_DATA_ROOT")):
            with self.subTest(target=target):
                with self.assertRaises(KBError) as ctx:
                    create_backup(self.root, target, clock=self.clock)
                self.assertEqual(ctx.exception.code, code)

    def test_doctor_full_detects_orphan_and_corrupt_cas(self):
        """IDs: S03-DOCTOR-01. doctor --full reports an unreferenced CAS object (warning) and a corrupted referenced object (problem) without changing either."""
        from scivetra_kb.core.cas import ContentStore
        orphan = ContentStore(Path(self.root)).put(b"orphan synthetic bytes")
        report = doctor(self.root, full=True)
        self.assertIn(orphan, report["unreferenced_objects"])
        self.assertTrue(any(w.startswith("UNREFERENCED_OBJECTS") for w in report["warnings"]))
        with open_reader(self.root) as kb:
            digest = kb.one("SELECT manifest_bytes_sha256 d FROM reprovane_release_import")["d"]
        path = Path(self.root, "cas", "sha256", digest[:2], digest)
        path.write_bytes(b"corrupted")
        report = doctor(self.root, full=True)
        self.assertIn(f"CAS_HASH_MISMATCH:{digest}", report["problems"])
        self.assertEqual(path.read_bytes(), b"corrupted")


if __name__ == "__main__":
    unittest.main()
