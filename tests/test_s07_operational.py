"""S07 operational checks: migration 0007, upgrade 6 -> 7, backup/restore, interrupted writes, concurrency, CLI, static
boundaries, documentation and the S06 v02 baseline guard."""
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.helpers import ROOT
from tests.journal_helpers import make_older_kb
from tests.s07_helpers import CRITERIA, EXCLUDE_TEXT, INCLUDE_TEXT, S07Case
from scivetra_kb import cli
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError, LockError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.screening.service import ScreeningService

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"
SCREENING = ROOT / "src" / "scivetra_kb" / "screening"
ADAPTED = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_s03_v02_fixes.py",
           "tests/test_bridge_paths_backup.py", "tests/test_journal_migration.py", "tests/test_s04j_static.py", "tests/test_s05_static.py",
           "tests/test_fte_migration.py", "tests/test_s06_static.py", "tests/test_s06_artifacts.py", "tools/required_ids.json"}


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MigrationAndRecovery(S07Case):
    def test_0007_additive_and_0001_0006_unchanged(self):
        """IDs: S07-MIG-HASH, S07-MIG-ADDITIVE, S07-MIG-FRESH-INIT. 0001-0006 are byte-identical to the accepted S06 v02; 0007_ai_screening_proposals.sql only CREATEs ai_* tables and triggers; every ai_* table has no-update/no-delete triggers; a fresh KB is schema 7."""
        expected = json.loads((ROOT / "tools" / "s06v02_migration_hashes.json").read_text())
        actual = {m[1]: m[2] for m in packaged_migrations()}
        self.assertEqual({k: actual[k] for k in expected}, expected)
        self.assertEqual([m[1] for m in packaged_migrations()][6], "0007_ai_screening_proposals.sql")  # S08: docs/S08_TEST_ADAPTATIONS.md #21 (after S07, S06)
        for statement in migrations._statements((MIG / "0007_ai_screening_proposals.sql").read_text(encoding="utf-8")):
            self.assertRegex(re.sub(r"--[^\n]*", "", statement).strip(), r"^CREATE (TABLE|TRIGGER) ai_")
        with self.reader() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S08: docs/S08_TEST_ADAPTATIONS.md #21 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #20 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #20 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #20 (after S10, S09, S08, S07, S06)
            tables = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'ai_*'")]
            triggers = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertEqual(len(tables), 9)
        for t in tables:
            self.assertTrue({f"{t}_no_update", f"{t}_no_delete"} <= triggers, t)

    def test_upgrade_6_to_7_backup_first_and_rollback(self):
        """IDs: S07-UPGRADE-6-7, S07-ROLLBACK. A schema-6 KB is refused by normal opens; upgrade backs up first, applies only 0007, keeps every prior table byte-identical (except the ledger and upgrade audit rows); the kept pre-upgrade file restores the exact schema-6 bytes."""
        v6 = os.path.join(self.tmp, "kb_v6")
        make_older_kb(v6, 6, clock=self.clock, ids=self.ids, client="0.6.0.dev2")
        with umock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:6]):
            with open_reader(v6) as kb:
                before = {t: table_content_sha256(kb.connection, t) for t in
                          [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]}
        original = sha(Path(v6, DB_RELATIVE))
        out = upgrade_kb(v6, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (6, 11, ["0007_ai_screening_proposals.sql", "0008_scientific_evidence_extraction.sql", "0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S08: docs/S08_TEST_ADAPTATIONS.md #22 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #21 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #21 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #21 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        with open_reader(v6) as kb:
            after = {t: table_content_sha256(kb.connection, t) for t in before}
        audit = ("kb_upgrade_event", "schema_migration")
        self.assertEqual({k: v for k, v in after.items() if k not in audit}, {k: v for k, v in before.items() if k not in audit})
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v6, rollback)
        shutil.copyfile(Path(v6, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        self.assertEqual(doctor(v6, full=True)["problems"], [])

    def test_backup_restore_interrupted_write_and_concurrency(self):
        """IDs: S07-BACKUP-RESTORE, S07-INTERRUPTED-WRITE, S07-CONCURRENCY, S07-DOCTOR-INVARIANTS. A failure after the pack was placed in CAS but before its row commits leaves no row (the orphan object is only a doctor warning) and a rerun completes; a second concurrent writer is refused (LockError); a backup carries every input pack, verifies and restores, and doctor --full on the restore is clean with zero S07 invariant violations."""
        run = self.ta_world([INCLUDE_TEXT, EXCLUDE_TEXT])
        scope = self.ta_scope(run)
        with self.writer() as kb:
            real = kb.insert

            def failing(table, row):
                if table == "ai_input_pack":
                    raise OSError("injected crash before commit")
                return real(table, row)
            kb.insert = failing
            with self.assertRaises(OSError):
                ScreeningService(kb).build_packs(scope_id=scope, operator="op.owner")
            self.assertEqual(kb.one("SELECT count(*) n FROM ai_input_pack")["n"], 0)
            with self.assertRaises(LockError):
                open_writer(self.root).__enter__()
        self.assertTrue(any(w.startswith("UNREFERENCED_OBJECTS") for w in doctor(self.root)["warnings"]))
        self.assertEqual(self.call("build_packs", scope_id=scope, operator="op.owner")["counts"]["PACKED"], 2)
        for r in self.call("run_mock", scope_id=scope, operator="op.runner")["results"]:
            self.call("verify", proposal_id=r["proposal_id"], operator="op.v")
        backup = create_backup(self.root, os.path.join(self.tmp, "backups"), clock=self.clock)
        self.assertTrue(verify_backup(os.path.join(self.tmp, "backups", backup["backup_name"]))["valid"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(os.path.join(self.tmp, "backups", backup["backup_name"]), restored)
        report = doctor(restored, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["screening_invariants"].values()), {0})
        with open_reader(restored) as kb:
            for row in kb.all("SELECT pack_sha256 FROM ai_input_pack"):
                kb.cas.verify_object(row["pack_sha256"])


class CliAndStatic(S07Case):
    def cli(self, *argv):
        return cli.run(list(argv), clock=self.clock, ids=self.ids)

    def test_screen_cli_path(self):
        """IDs: S07-CLI-FLOW, S07-CLI-BLIND-FIRST, S07-CLI-EXIT-CODES. The rkb screen commands cover criteria, scope, packs, mock, verify, state/show, roster, initial judgement, reveal, approval and export; show never contains the recommendation; reveal before judgement is refused with a stable code; a FAILED verification exits 3."""
        run = self.ta_world([INCLUDE_TEXT])
        crit = Path(self.tmp, "criteria.json")
        crit.write_text(json.dumps(CRITERIA), encoding="utf-8")
        code, cs = self.cli("screen", "criteria-create", "--data-root", self.root, "--task-type", "TA_SCREEN", "--criteria-file", str(crit), "--label", "c",
                            "--operator", "op.owner")
        spec = Path(self.tmp, "scope.json")
        spec.write_text(json.dumps({"task_type": "TA_SCREEN", "criteria_set_id": cs["criteria_set_id"], "label": "cli scope",
                                    "content_classes": ["METADATA_AND_ABSTRACT"], "paper_ids": [run["candidates"][0]["paper_id"]],
                                    "release_import_id": run["source"]}), encoding="utf-8")
        code, scope = self.cli("screen", "scope-create", "--data-root", self.root, "--scope-file", str(spec), "--operator", "op.owner")
        sid = scope["scope_id"]
        self.assertEqual(self.cli("screen", "build-packs", "--data-root", self.root, "--scope-id", sid, "--operator", "op.owner")[1]["counts"]["PACKED"], 1)
        pid = self.cli("screen", "run-mock", "--data-root", self.root, "--scope-id", sid, "--operator", "op.runner")[1]["results"][0]["proposal_id"]
        code, ver = self.cli("screen", "verify", "--data-root", self.root, "--proposal-id", pid, "--operator", "op.v")
        self.assertEqual((code, ver["state"]), (0, "PASSED"))
        with self.reader() as kb:
            pack_row = kb.one("SELECT * FROM ai_input_pack WHERE scope_id=?", (sid,))
            pack = json.loads(kb.cas.read(pack_row["pack_sha256"]))
        title = next(i for i in pack["items"] if i["field"] == "title")
        fake = "a sentence the paper never contains"
        anchors = [{"kind": "METADATA_CLAIM", "ref": title["ref"], "page": None, "char_start": 0, "char_end": len(fake), "quote": fake,
                    "quote_sha256": hashlib.sha256(fake.encode()).hexdigest()}]
        items = [{"criterion_id": c["id"], "judgement": "MET" if c["id"] == "C1" else "UNCLEAR", "rationale": "r", "anchors": anchors if c["id"] == "C1" else []}
                 for c in CRITERIA["criteria"]]
        resp = Path(self.tmp, "response.json")
        resp.write_text(json.dumps({"schema": "rkb-ai-response/1", "task_type": "TA_SCREEN", "paper_id": pack["paper_id"],
                                    "input_pack_sha256": pack_row["pack_sha256"], "suggested_outcome": "UNSURE", "criteria": items,
                                    "suggested_reason_codes": [], "uncertainty": "HIGH", "notes": ""}), encoding="utf-8")
        model = Path(self.tmp, "model.json")
        model.write_text(json.dumps({"provider": "local", "model_id": "operator-model"}), encoding="utf-8")
        code, imported = self.cli("screen", "import-proposal", "--data-root", self.root, "--pack-id", pack_row["pack_id"], "--response-file", str(resp),
                                  "--model-file", str(model), "--operator", "op.importer")
        self.assertEqual((code, imported["status"]), (0, "OK"))
        code, failed = self.cli("screen", "verify", "--data-root", self.root, "--proposal-id", imported["proposal_id"], "--operator", "op.v")
        self.assertEqual((code, failed["state"]), (3, "FAILED"))
        code, shown = self.cli("screen", "show", "--data-root", self.root, "--proposal-id", pid)
        self.assertEqual((code, shown["recommendation"]), (0, "WITHHELD_UNTIL_REVEAL"))
        self.cli("screen", "reviewer-register", "--data-root", self.root, "--label", "rev.cli", "--operator", "op.owner", "--reason", "owner roster")
        self.assertEqual(cli.main(["screen", "reveal", "--data-root", self.root, "--proposal-id", pid, "--reviewer", "rev.cli"]), 2)
        self.cli("screen", "initial-judgement", "--data-root", self.root, "--scope-id", sid, "--paper-id", run["candidates"][0]["paper_id"],
                 "--reviewer", "rev.cli", "--outcome", "INCLUDE")
        self.assertEqual(self.cli("screen", "reveal", "--data-root", self.root, "--proposal-id", pid, "--reviewer", "rev.cli")[1]["suggested_outcome"], "INCLUDE")
        self.assertEqual(self.cli("screen", "approve", "--data-root", self.root, "--proposal-id", pid, "--reviewer", "rev.cli", "--decision", "ACCEPT")[0], 0)
        self.assertEqual(self.cli("screen", "state", "--data-root", self.root, "--proposal-id", pid)[1]["review_state"], "REVIEWED")
        self.assertEqual(self.cli("screen", "export", "--data-root", self.root, "--scope-id", sid, "--out-dir", os.path.join(self.tmp, "e"),
                                  "--operator", "op.owner")[0], 0)

    def test_screening_package_boundaries(self):
        """IDs: S07-STATIC-BOUNDARY, S07-NO-NETWORK, S07-NO-AI-SDK, S07-MOCK-TOOLLESS, S07-NO-S05-WRITES. The screening package imports no network/HTTP client, AI SDK, subprocess, browser or file watcher; the mock imports only hashlib and json; no screening module inserts into an S05, S06 or Reprovane-import table."""
        forbidden = re.compile(r"^(socket|ssl|urllib|http|requests|httpx|aiohttp|openai|anthropic|subprocess|webbrowser|selenium|playwright|watchdog|transformers|torch|llama)")
        for path in sorted(SCREENING.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    self.assertIsNone(forbidden.match(name), (path.name, name))
            for table in re.findall(r'insert\(\s*"([a-z_]+)"', text):
                self.assertTrue(table.startswith("ai_"), (path.name, table))
        mock_imports = {a.name for n in ast.walk(ast.parse((SCREENING / "mock.py").read_text(encoding="utf-8"))) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(mock_imports, {"hashlib", "json"})
        self.assertNotRegex((SCREENING / "mock.py").read_text(encoding="utf-8"), r"(?m)^\s*from\s+(?!__future__)")

    def test_docs_and_version(self):
        """IDs: S07-DOCS, S07-STATIC-VERSION, S07-ERRATA-S06-GUIDE. Version 0.7.0.dev2; the ADR, beginner guide and test adaptations exist and state the authority boundary, blind-first, LOCAL_PROCESS-only and synthetic-only limits; the S06 install-guide errata is recorded."""
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.2")  # S08: docs/S08_TEST_ADAPTATIONS.md #23 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #22 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #22 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #22 (after S10, S09, S08, S07, S06)
        adr = (ROOT / "docs" / "ADR_S07.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "SCREENING_USER_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("NON_AUTHORITATIVE", "blind-first", "LOCAL_PROCESS", "rkb-ai-proposal/1", "0007_ai_screening_proposals.sql", "mock"):
            self.assertIn(needle, adr)
        for needle in ("rkb screen", "initial-judgement", "synthetic", "What remains human work", "$env:"):
            self.assertIn(needle, guide)
        self.assertIn("errata", (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8").lower())
        for n in range(1, 21):
            self.assertIn(f"| {n} |", (ROOT / "docs" / "S07_TEST_ADAPTATIONS.md").read_text(encoding="utf-8"))
        for path in sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_accepted_s06_tests_changed_only_on_marked_lines(self):
        """IDs: S07-TEST-ADAPTATIONS, S07-BASELINE-PRESERVED. Every S06 v02 test and tool file is still present; files outside the adapted set are byte-identical to the accepted S06 v02 ZIP; in adapted files every changed or added block carries an 'S07' marker; no accepted line is deleted."""
        baseline = json.loads((ROOT / "tools" / "s06v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
        changed = 0
        for rel, info in sorted(baseline.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            data = path.read_bytes()
            if rel not in ADAPTED:
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"], rel)
                continue
            lines = data.splitlines()
            now = [hashlib.sha256(line).hexdigest()[:16] for line in lines]
            for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=info["line_sha256"], b=now, autojunk=False).get_opcodes():
                if tag == "equal":
                    continue
                self.assertNotEqual(tag, "delete", (rel, a0, a1))
                block = [lines[i].decode("utf-8") for i in range(b0, b1)]
                self.assertTrue(any("S07" in line for line in block), (rel, block))
                if rel.startswith("tests/"):
                    changed += b1 - b0
        self.assertLess(changed, 40)


if __name__ == "__main__":
    unittest.main()
