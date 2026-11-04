"""S05: migration 0005 is additive; schema 4->5 and 3->5 upgrades keep every prior table and S04J digest; backup/restore and
CAS checks cover the locked handoff manifest; S04J context is read-only and only after a human-confirmed manifestation."""
import hashlib
import os
import re
import shutil
from pathlib import Path
from unittest import mock

from tests.fte_helpers import FTECase
from tests.helpers import ROOT
from tests.journal_helpers import ACCEPTED_MIGRATIONS, SimulatedProviders, make_older_kb, pipeline, small_scenario
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError, SchemaError
from scivetra_kb.core.manifest import eligibility_manifest, journal_manifest, library_manifest, table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.eligibility.service import FTEService

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"
S04J_0004 = "57b6cc3d936618c471585e45b93e0662667c6c82087f237b6a14ba81dbe81b68"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tables(connection):
    return sorted(r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'schema_migration'"))


class MigrationTests(FTECase):
    def test_accepted_migrations_unchanged_and_0005_additive(self):
        """IDs: S05-MIG-HASH, S05-MIG-ADDITIVE. 0001-0003 keep their accepted S04 v02 hashes and 0004 its accepted S04J v02 hash (57b6cc3d...); 0005_fulltext_eligibility_staging.sql is the only new file; it only CREATEs fte_* tables/indexes/triggers (no ALTER/DROP, no statement starting with INSERT/UPDATE/DELETE), references prior tables only by foreign key, and every fte_* table has no-update/no-delete triggers."""
        for version, digest in ACCEPTED_MIGRATIONS.items():
            self.assertEqual(sha(next(MIG.glob(f"000{version}_*.sql"))), digest)
        self.assertEqual(sha(MIG / "0004_journal_harvesting.sql"), S04J_0004)
        self.assertEqual([m[1] for m in packaged_migrations()][4], "0005_fulltext_eligibility_staging.sql")  # S06: docs/S06_TEST_ADAPTATIONS.md #16
        code = "\n".join(line.split("--")[0] for line in (MIG / "0005_fulltext_eligibility_staging.sql").read_text(encoding="utf-8").splitlines())
        for word in ("ALTER ", "DROP ", "REPLACE "):
            self.assertNotIn(word, code.upper())
        self.assertEqual(re.findall(r"^\s*(?:INSERT|UPDATE|DELETE)\b", code, re.M | re.I), [])
        created = re.findall(r"CREATE TABLE (\w+)", code)
        self.assertTrue(created and all(t.startswith("fte_") for t in created))
        self.assertTrue(all(f"CREATE TRIGGER {t}_no_update" in code and f"CREATE TRIGGER {t}_no_delete" in code for t in created))
        for name in re.findall(r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)", code) + re.findall(r"BEFORE INSERT ON (\w+)", code):
            self.assertTrue(name.startswith("fte_"), name)
        self.assertEqual({r for r in re.findall(r"REFERENCES (\w+)", code) if not r.startswith("fte_")},
                         {"workspace_registration", "reprovane_release_import", "project_paper_reference", "global_paper", "pdf_asset",
                          "paper_asset_link", "document_version", "rights_assessment"})
        self.assertNotRegex(code, r"(?i)jh_")

    def test_schema4_with_journal_data_upgrades_to_5(self):
        """IDs: S05-UPGRADE-4-5, S05-S04J-PRESERVED. A schema-4 (S04J v02) KB holding a full journal pipeline is refused by normal opens; 'upgrade' backs up first, applies only 0005, keeps every prior table byte-identical and the S04J journal manifest identical; a 4-migration client refuses the result (KB_CLIENT_TOO_OLD); the kept pre-upgrade file restores the exact schema-4 bytes; the FTE workflow then works."""
        v4 = os.path.join(self.tmp, "kb_v4")
        make_older_kb(v4, 4, clock=self.clock, ids=self.ids, client="0.4.1.dev2")
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:4]):
            with open_writer(v4, clock=self.clock, ids=self.ids) as kb:
                pipeline(kb, SimulatedProviders(small_scenario(12)), sizes=(5, 5, 5))
                before_journal = journal_manifest(kb)
                before_tables = {t: table_content_sha256(kb.connection, t) for t in _tables(kb.connection)}
        original = sha(Path(v4, DB_RELATIVE))
        with self.assertRaises(SchemaError) as ctx:
            open_writer(v4).__enter__()
        self.assertEqual(ctx.exception.code, "KB_UPGRADE_REQUIRED")
        out = upgrade_kb(v4, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (4, 11, ["0005_fulltext_eligibility_staging.sql", "0006_pdf_parse_artifacts.sql", "0007_ai_screening_proposals.sql", "0008_scientific_evidence_extraction.sql", "0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S06: docs/S06_TEST_ADAPTATIONS.md #17; S07: docs/S07_TEST_ADAPTATIONS.md #16 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #16 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #15 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #15 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #15 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        with open_reader(v4) as kb:
            self.assertEqual(journal_manifest(kb), before_journal)
            # The upgrade appends its own audit row to kb_upgrade_event; every other prior table is byte-identical.
            after = {t: table_content_sha256(kb.connection, t) for t in before_tables}
            self.assertEqual({t: v for t, v in after.items() if t != "kb_upgrade_event"}, {t: v for t, v in before_tables.items() if t != "kb_upgrade_event"})
            self.assertEqual(kb.one("SELECT count(*) n FROM kb_upgrade_event WHERE from_version=4 AND to_version=11")["n"], 1)  # S06: docs/S06_TEST_ADAPTATIONS.md #17; S07: docs/S07_TEST_ADAPTATIONS.md #16 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #16 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #15 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #15 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #15 (after S10, S09, S08, S07, S06)
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:4]):
            with self.assertRaises(SchemaError) as ctx:
                open_reader(v4).__enter__()
        self.assertEqual(ctx.exception.code, "KB_CLIENT_TOO_OLD")
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v4, rollback)
        shutil.copyfile(Path(v4, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:4]):
            with open_reader(rollback) as kb:
                self.assertEqual((kb.schema["schema_version"], journal_manifest(kb)), (4, before_journal))
        self.assertEqual(doctor(v4, full=True)["problems"], [])

    def test_schema3_upgrades_through_4_to_5_and_failed_upgrade_is_clean(self):
        """IDs: S05-UPGRADE-3-5, S05-UPGRADE-FAULT. A schema-3 (S04 v02) KB upgrades in one backup-first copy-migrate-swap applying 0004 and 0005, with every prior table byte-identical; an injected failure after migrating the copy leaves the original schema-3 bytes untouched and no work file."""
        v3 = os.path.join(self.tmp, "kb_v3")
        make_older_kb(v3, 3, clock=self.clock, ids=self.ids)
        broken = os.path.join(self.tmp, "kb_v3b")
        shutil.copytree(v3, broken)
        original = sha(Path(broken, DB_RELATIVE))
        out = upgrade_kb(v3, os.path.join(self.tmp, "b1"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]),
                         (3, 11, ["0004_journal_harvesting.sql", "0005_fulltext_eligibility_staging.sql", "0006_pdf_parse_artifacts.sql", "0007_ai_screening_proposals.sql", "0008_scientific_evidence_extraction.sql", "0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S06: docs/S06_TEST_ADAPTATIONS.md #18; S07: docs/S07_TEST_ADAPTATIONS.md #17 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #17 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #16 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #16 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #16 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"] and out["checks"]["library_manifest_identical"])

        def boom(**_):
            raise KBError("injected", code="INJECTED")
        with self.assertRaises(KBError):
            upgrade_kb(broken, os.path.join(self.tmp, "b2"), clock=self.clock, ids=self.ids, faults={"after_migrate": boom})
        self.assertEqual(sha(Path(broken, DB_RELATIVE)), original)
        self.assertEqual(sorted(os.listdir(Path(broken, "db"))), ["kb.sqlite"])

    def test_backup_restore_and_cas_cover_locked_handoff(self):
        """IDs: S05-BACKUP, S05-CAS. The locked handoff manifest is a schema-5 CAS reference: doctor --full is clean (eligibility invariants all zero), a backup verifies with the eligibility manifest, a restore reproduces it and verifies the lock; a missing handoff object is CAS_MISSING in doctor and blocks create_backup."""
        r = self.make_run(3)
        for c in r["candidates"]:
            self.attempts(c)
            self.both(c, "FT_NOT_RETRIEVED", ["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"])
        self.svc_call("resolve", run_id=r["run_id"], operator="op.f")
        lock = self.svc_call("lock", run_id=r["run_id"], operator="op.f")
        report = doctor(self.root, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["eligibility_invariants"].values()), {0})
        with open_reader(self.root) as kb:
            before = eligibility_manifest(kb)
        backup = create_backup(self.root, os.path.join(self.tmp, "bk"), clock=self.clock)
        verified = verify_backup(backup["backup_dir"])
        self.assertEqual((verified["valid"], verified["domain"]["eligibility_manifest_sha256"]), (True, before["manifest_sha256"]))
        restored = os.path.join(self.tmp, "restored")
        restore_backup(backup["backup_dir"], restored)
        from scivetra_kb.eligibility.handoff import verify_lock
        with open_reader(restored) as kb:
            self.assertEqual(eligibility_manifest(kb), before)
            self.assertTrue(verify_lock(kb, r["run_id"])["valid"])
        digest = lock["handoff_manifest_sha256"]
        Path(self.root, "cas", "sha256", digest[:2], digest).unlink()
        self.assertIn(f"CAS_MISSING:{digest}", doctor(self.root)["problems"])
        with self.assertRaises(KBError):
            create_backup(self.root, os.path.join(self.tmp, "bk2"), clock=self.clock)


class JournalContextTests(FTECase):
    def test_s04j_context_is_read_only_and_needs_a_confirmed_manifestation(self):
        """IDs: S05-S04J-READ-ONLY, S05-S04J-NO-FUZZY. A TA candidate whose paper DOI exactly equals an S04J subject DOI key gets read-only journal context (retraction ever reported, crossref_query_issns, journal review tasks, NOT_SCREENED) only after a HUMAN_CONFIRMED manifestation is attached; without it the association is NOT_LINKED (no fuzzy title/DOI joins); the S04J retraction appears as INTEGRITY_ALERT but creates no vote; every jh_* table is byte-identical before and after all FTE operations."""
        with self.writer() as kb:
            pipeline(kb, SimulatedProviders(small_scenario(45)), sizes=(10, 10, 10))
        dois = {0: "10.5555/jisa-synthetic.000040", 1: "10.5555/jisa-synthetic.000001"}
        r = self.make_run(3, doi_for=lambda i: [(dois.get(i, f"10.99999/fte.{i}"), dois.get(i, f"10.99999/fte.{i}"))])
        with self.reader() as kb:
            jh_before = {t: table_content_sha256(kb.connection, t) for t in _tables(kb.connection) if t.startswith("jh_")}
            svc = FTEService(kb)
            self.assertEqual(svc.journal_context(r["candidates"][0])["association"], "NOT_LINKED")
        self.attach(r["candidates"][0])
        with self.reader() as kb:
            svc = FTEService(kb)
            ctx = svc.journal_context(r["candidates"][0])
            self.assertEqual(ctx["association"], "EXACT_DOI_KEY_OF_HUMAN_CONFIRMED_MANIFESTATION")
            entry = ctx["entries"][0]
            self.assertEqual((entry["doi_key"], entry["retraction_reported_ever"], entry["screening_status"]),
                             ("10.5555/jisa-synthetic.000040", True, "NOT_SCREENED"))
            self.assertEqual(entry["crossref_query_issns"], ["2214-2126", "2214-2134"])
            self.assertIn("INTEGRITY_SIGNAL_REPORTED", entry["journal_review_task_triggers"])
            self.assertEqual(svc.journal_context(r["candidates"][1])["association"], "NOT_LINKED")
            q = {e["candidate_id"]: e for e in svc.queue(r["run_id"])}
            self.assertIn("INTEGRITY_ALERT", {f["category"] for f in q[r["candidates"][0]["candidate_id"]]["flags"]})
            self.assertEqual(q[r["candidates"][0]["candidate_id"]]["status"], "PENDING")
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_decision")["n"], 0)
        self.both(r["candidates"][0], "FT_INCLUDE")
        with self.reader() as kb:
            self.assertEqual({t: table_content_sha256(kb.connection, t) for t in jh_before}, jh_before)
