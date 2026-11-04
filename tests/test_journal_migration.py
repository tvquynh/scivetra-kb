"""S04J: migration 0004 is additive and reversible; accepted migrations unchanged; backup/verify/restore/doctor cover journal data."""
import hashlib
import os
import re
import shutil
from pathlib import Path
from unittest import mock

from tests.helpers import ROOT
from tests.journal_helpers import (ACCEPTED_MIGRATIONS, KBTest, SimulatedProviders, make_older_kb, pipeline, shared_jisa, small_scenario)
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.cas import referenced_digests
from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import IntegrityError, KBError, SchemaError
from scivetra_kb.core.manifest import bridge_manifest, journal_manifest, library_manifest
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb

MIGRATIONS = ROOT / "src" / "scivetra_kb" / "core" / "migrations"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MigrationTests(KBTest):
    def test_accepted_migrations_are_byte_identical(self):
        """IDs: JH-02, S04J-MIGRATION-HASH. 0001, 0002 and 0003 keep the SHA-256 accepted in S04 v02; 0004_journal_harvesting.sql is the only new migration and the package is contiguous 1..4."""
        for version, digest in ACCEPTED_MIGRATIONS.items():
            name = {1: "0001_metadata_foundation.sql", 2: "0002_global_research_library.sql", 3: "0003_download_bridge.sql"}[version]
            self.assertEqual(sha(MIGRATIONS / name), digest, name)
        self.assertEqual([(m[0], m[1]) for m in packaged_migrations()],
                         [(1, "0001_metadata_foundation.sql"), (2, "0002_global_research_library.sql"), (3, "0003_download_bridge.sql"),
                          (4, "0004_journal_harvesting.sql"), (5, "0005_fulltext_eligibility_staging.sql"), (6, "0006_pdf_parse_artifacts.sql"), (7, "0007_ai_screening_proposals.sql"), (8, "0008_scientific_evidence_extraction.sql"), (9, "0009_local_retrieval.sql"), (10, "0010_ai_integration_gateway.sql"), (11, "0011_scientific_synthesis.sql")])  # S05: docs/S05_TEST_ADAPTATIONS.md #12; S06: docs/S06_TEST_ADAPTATIONS.md #12; S07: docs/S07_TEST_ADAPTATIONS.md #12 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #12 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #11 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #11 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #11 (after S10, S09, S08, S07, S06)

    def test_0004_is_purely_additive(self):
        """IDs: JH-03, S04J-MIGRATION-ADDITIVE. 0004 only CREATEs jh_* tables, their indexes and triggers (no ALTER, DROP, UPDATE, DELETE, INSERT or reference to a prior table except read-only foreign keys to fetch_run); every jh_* table has no-update/no-delete triggers."""
        sql = (MIGRATIONS / "0004_journal_harvesting.sql").read_text(encoding="utf-8")
        code = "\n".join(line.split("--")[0] for line in sql.splitlines())
        for word in ("ALTER ", "DROP ", "REPLACE "):
            self.assertNotIn(word, code.upper(), word)
        # statements (not trigger events such as "BEFORE INSERT ON") must never start with INSERT/UPDATE/DELETE
        self.assertEqual(re.findall(r"^\s*(?:INSERT|UPDATE|DELETE)", code, re.M | re.I), [])
        created = re.findall(r"CREATE TABLE (\w+)", code)
        self.assertTrue(created and all(t.startswith("jh_") for t in created))
        self.assertTrue(all(re.search(rf"CREATE TRIGGER {t}_no_update", code) and re.search(rf"CREATE TRIGGER {t}_no_delete", code) for t in created))
        for name in re.findall(r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)", code) + re.findall(r"BEFORE INSERT ON (\w+)", code):
            self.assertTrue(name.startswith("jh_"), name)
        refs = set(re.findall(r"REFERENCES (\w+)", code))
        self.assertEqual({r for r in refs if not r.startswith("jh_")}, {"fetch_run"})

    def test_schema3_kb_upgrades_backup_first_and_rolls_back(self):
        """IDs: JH-03, S04J-UPGRADE-3-4, S01-10. An S04 v02 (schema-3) KB is refused by normal opens (KB_UPGRADE_REQUIRED); 'upgrade' writes a verified backup first, applies only 0004 to a copy, keeps every prior table byte-identical (and the S02/library/bridge manifests), and the journal pipeline then works; an S04-v02 client (3 migrations) refuses the schema-4 KB (KB_CLIENT_TOO_OLD); restoring the pre-upgrade file gives back the exact schema-3 bytes; a second upgrade is UPGRADE_NOT_REQUIRED."""
        v3 = os.path.join(self.tmp, "kb_v3")
        make_older_kb(v3, 3, clock=self.clock, ids=self.ids)
        original = sha(Path(v3, DB_RELATIVE))
        with self.assertRaises(SchemaError) as ctx:
            open_writer(v3).__enter__()
        self.assertEqual(ctx.exception.code, "KB_UPGRADE_REQUIRED")
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:3]):
            with open_reader(v3) as kb:
                before = (library_manifest(kb)["manifest_sha256"], bridge_manifest(kb)["manifest_sha256"])
        out = upgrade_kb(v3, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (3, 11, ["0004_journal_harvesting.sql", "0005_fulltext_eligibility_staging.sql", "0006_pdf_parse_artifacts.sql", "0007_ai_screening_proposals.sql", "0008_scientific_evidence_extraction.sql", "0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S05: docs/S05_TEST_ADAPTATIONS.md #13; S06: docs/S06_TEST_ADAPTATIONS.md #13; S07: docs/S07_TEST_ADAPTATIONS.md #13 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #13 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #12 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #12 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #12 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"] and out["checks"]["s02_tables_byte_identical"])
        self.assertTrue(out["checks"]["library_manifest_identical"])
        self.assertTrue(verify_backup(os.path.join(self.tmp, "backups", out["backup"]["backup_name"]))["valid"])
        with open_writer(v3, clock=self.clock, ids=self.ids) as kb:
            self.assertEqual((library_manifest(kb)["manifest_sha256"], bridge_manifest(kb)["manifest_sha256"]), before)
            run = pipeline(kb, SimulatedProviders(small_scenario(12)), sizes=(5, 5, 5))
            self.assertEqual(run["version"]["collection_state"], "COMPLETE_RELATIVE_TO_RECORDED_QUERIES")
        self.assertEqual(doctor(v3, full=True)["problems"], [])
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:3]):
            with self.assertRaises(SchemaError) as ctx:
                open_reader(v3).__enter__()
        self.assertEqual(ctx.exception.code, "KB_CLIENT_TOO_OLD")
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v3, rollback)
        shutil.copyfile(Path(v3, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:3]):
            with open_reader(rollback) as kb:
                self.assertEqual(kb.schema["schema_version"], 3)
        self.assertEqual(upgrade_kb(v3, os.path.join(self.tmp, "backups2"), clock=self.clock, ids=self.ids)["code"], "UPGRADE_NOT_REQUIRED")

    def test_failed_upgrade_leaves_schema3_untouched(self):
        """IDs: JH-03, S04J-UPGRADE-FAULT. A fault after 0004 is applied to the upgrade copy aborts the upgrade: the copy is removed, the original schema-3 database bytes are unchanged and no pre-upgrade file is left."""
        v3 = os.path.join(self.tmp, "kb_v3")
        make_older_kb(v3, 3, clock=self.clock, ids=self.ids)
        original = sha(Path(v3, DB_RELATIVE))

        def boom(**_):
            raise IntegrityError("injected", code="INJECTED_FAULT")
        with self.assertRaises(KBError):
            upgrade_kb(v3, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids, faults={"after_migrate": boom})
        self.assertEqual(sha(Path(v3, DB_RELATIVE)), original)
        self.assertEqual(sorted(os.listdir(Path(v3, "db"))), ["kb.sqlite"])


class BackupTests(KBTest):
    def test_backup_verify_restore_cover_journal_pages(self):
        """IDs: JH-03, JH-19, S04J-BACKUP. Raw journal pages are schema-4 CAS references: a backup of the JISA-shaped KB verifies, its domain summary carries the journal manifest, a restore into a new root reproduces the journal manifest and doctor --full is clean (journal invariants all zero); a missing page object blocks create_backup and is reported by doctor."""
        shared = shared_jisa()
        root = os.path.join(self.tmp, "kb_jisa")
        shutil.copytree(shared["root"], root)
        with open_reader(root) as kb:
            refs = referenced_digests(kb.connection, 4)
            pages = [r["response_bytes_sha256"] for r in kb.all("SELECT response_bytes_sha256 FROM jh_page WHERE response_bytes_sha256 IS NOT NULL")]
            manifest = journal_manifest(kb)
        self.assertTrue(pages and all("jh_page.response_bytes_sha256" in refs[d] for d in pages))
        report = doctor(root, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["journal_invariants"].values()), {0})
        backup = create_backup(root, os.path.join(self.tmp, "backups"), clock=self.clock)
        verified = verify_backup(backup["backup_dir"])
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["domain"]["journal_manifest_sha256"], manifest["manifest_sha256"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(backup["backup_dir"], restored)
        with open_reader(restored) as kb:
            self.assertEqual(journal_manifest(kb), manifest)
        self.assertEqual(doctor(restored, full=True)["problems"], [])
        victim = pages[0]
        Path(root, "cas", "sha256", victim[:2], victim).unlink()
        self.assertIn(f"CAS_MISSING:{victim}", doctor(root)["problems"])
        with self.assertRaises(KBError):
            create_backup(root, os.path.join(self.tmp, "backups_broken"), clock=self.clock)
