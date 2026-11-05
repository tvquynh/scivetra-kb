"""S10 operational checks: migration 0010 (additive, 0001-0009 byte-identical to the accepted S09 v02), upgrade 9 -> 10 with backup and
rollback, backup / restore of S10 records, static boundaries of the ai package, configuration templates, documentation and version."""
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
from tests.s10_helpers import QUERY, S10Case
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"
AI = ROOT / "src" / "scivetra_kb" / "ai"
AI_TABLES = ("aig_owner", "aig_consumer_profile", "aig_rights_determination", "aig_release_approval", "aig_revocation", "aig_access_event",
             "aig_exchange_bundle")


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contents(root):
    with open_reader(root) as kb:
        names = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: table_content_sha256(kb.connection, t) for t in names}


class Migration(S10Case):
    def populated(self):
        self.ai_world()
        approval = self.full_release()
        self.tool("build_rag_context", scope_id=self.scope, query=QUERY)
        self.records("revoke", target_id=approval["approval_id"], reason="synthetic revocation", operator="op.owner")
        from scivetra_kb.ai import exchange
        prof = self.make_profile("CHATGPT_FILE_EXCHANGE")
        self.determine(self.items[0], "redistribute")
        self.approve(profile=prof, paper_ids=[self.items[0]["candidate"]["paper_id"]])
        out = Path(self.tmp, "x")
        out.mkdir()
        exchange.export_bundle(self.open_kb, profile_id=prof, scope_id=self.scope, query=QUERY, out_dir=str(out / "b"), operator="op.e")

    def test_0010_additive_and_0001_0009_unchanged(self):
        """IDs: S10-MIG-HASH, S10-MIG-ADDITIVE, S10-MIG-FRESH-INIT, S10-APPEND-ONLY-TRIGGERS. 0001-0009 are byte-identical to the accepted
        S09 v02 (tools/s09v02_migration_hashes.json, equal to the architect's independent hashes); 0010_ai_integration_gateway.sql only
        CREATEs aig_* tables, indexes and triggers; every aig_* table refuses UPDATE and DELETE; a fresh KB is schema 10."""
        expected = json.loads((ROOT / "tools" / "s09v02_migration_hashes.json").read_text())["migrations"]
        actual = {m[1]: m[2] for m in packaged_migrations()}
        self.assertEqual(len(expected), 9)
        self.assertEqual({k: actual[k] for k in expected}, expected)
        self.assertEqual([m[1] for m in packaged_migrations()][9], "0010_ai_integration_gateway.sql")  # S11: docs/S11_TEST_ADAPTATIONS.md #29 (after S10)
        for statement in migrations._statements((MIG / "0010_ai_integration_gateway.sql").read_text(encoding="utf-8")):
            body = re.sub(r"--[^\n]*", "", statement).strip()
            self.assertRegex(body, r"^CREATE (TABLE|TRIGGER|INDEX) aig_")
            self.assertTrue(any(re.search(rf"\b{t}\b", body) for t in AI_TABLES), body[:60])
        self.populated()
        with self.writer() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S11: docs/S11_TEST_ADAPTATIONS.md #29 (after S10)
            names = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(set(AI_TABLES) <= names)          # S07 owns the ai_ prefix (ai_proposal, ...); S10 uses aig_
            tables = sorted(AI_TABLES)
            triggers = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
            for t in tables:
                self.assertTrue({f"{t}_no_update", f"{t}_no_delete"} <= triggers, t)
                self.assertGreater(kb.one(f"SELECT count(*) n FROM {t}")["n"], 0, t)
                col = kb.all(f"PRAGMA table_info({t})")[1]["name"]
                for sql in (f"UPDATE {t} SET {col}={col}", f"DELETE FROM {t}"):
                    with self.assertRaises(KBError, msg=sql):
                        kb.execute(sql)

    def test_check_constraints_fail_closed(self):
        """IDs: S10-MIG-CHECKS. The schema itself refuses a remote / API consumer, a model location other than EXTERNAL_UNVERIFIED, an
        access event that reveals content without an approval and a determination for a non-licensed class."""
        self.ai_world()
        self.full_release()
        self.tool("build_rag_context", scope_id=self.scope, query=QUERY)
        with self.writer() as kb:
            ev = kb.one("SELECT * FROM aig_access_event")
            det = kb.one("SELECT * FROM aig_rights_determination")
            prof = kb.one("SELECT * FROM aig_consumer_profile")
            for table, row in (("aig_access_event", dict(ev, event_id="aie_x", request_id="aiq_x", approval_id=None)),
                               ("aig_access_event", dict(ev, event_id="aie_y", request_id="aiq_y", transport="REMOTE_MCP")),
                               ("aig_consumer_profile", dict(prof, profile_id="aip_x", model_location="LOCAL_VERIFIED", semantic_sha256="3" * 64)),
                               ("aig_rights_determination", dict(det, determination_id="aid_x", rights_class="UNKNOWN", semantic_sha256="4" * 64))):
                with self.assertRaises(KBError, msg=table):
                    with kb.transaction():
                        kb.insert(table, row)

    def test_upgrade_9_to_10_backup_first_and_rollback(self):
        """IDs: S10-UPGRADE-9-10, S10-ROLLBACK. A schema-9 KB is upgraded only after a verified backup; only 0010 is applied; every prior
        table is byte-identical (except the ledger and upgrade audit rows); the kept pre-upgrade file restores the exact schema-9 bytes;
        doctor reports no problem."""
        v9 = os.path.join(self.tmp, "kb_v9")
        make_older_kb(v9, 9, clock=self.clock, ids=self.ids, client="0.9.0.dev2")
        with umock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:9]):
            before = contents(v9)
        original = sha(Path(v9, DB_RELATIVE))
        out = upgrade_kb(v9, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (9, 11, ["0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S11: docs/S11_TEST_ADAPTATIONS.md #30 (after S10)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        after = contents(v9)
        audit = ("kb_upgrade_event", "schema_migration")
        self.assertEqual({k: v for k, v in after.items() if k in before and k not in audit}, {k: v for k, v in before.items() if k not in audit})
        self.assertEqual(sorted(set(after) - set(before) - {t for t in after if t.startswith("syn_")}), sorted(AI_TABLES))  # S11: docs/S11_TEST_ADAPTATIONS.md #30 (after S10)
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v9, rollback)
        shutil.copyfile(Path(v9, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        self.assertEqual(doctor(v9, full=True)["problems"], [])

    def test_backup_and_restore_carry_s10_records(self):
        """IDs: S10-BACKUP-RESTORE. A KB with owners, profiles, determinations, approvals, revocations, access events and an exchange bundle
        backs up, verifies and restores into a new root with every table byte-identical; the restored KB still denies the revoked
        approval."""
        self.populated()
        backup = create_backup(self.root, os.path.join(self.tmp, "bk"), clock=self.clock)
        self.assertTrue(verify_backup(backup["backup_dir"])["valid"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(backup["backup_dir"], restored)
        self.assertEqual(contents(restored), contents(self.root))
        from scivetra_kb.ai.gateway import GatewaySession, IntegrationGateway
        from scivetra_kb.core.db import KB
        g = IntegrationGateway(lambda: KB(restored, readonly=False, clock=self.clock, ids=self.ids), GatewaySession(self.profile, "IN_PROCESS_TEST"))
        self.assertIn("APPROVAL_REVOKED", g.handle("build_rag_context", {"scope_id": self.scope, "query": QUERY})["authorization"]["reason_codes"])


class StaticAndDocs(S10Case):
    def test_ai_package_writes_only_ai_tables_and_s09_audit_stays_local(self):
        """IDs: S10-STATIC-WRITES, S10-STATIC-NO-LOCAL-CLI. The ai package inserts only into aig_* tables; it never passes the string
        LOCAL_CLI as a consumer; S09 still lists LOCAL_CLI as its only string consumer."""
        for path in sorted(AI.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for table in re.findall(r'insert\(\s*"([a-z_]+)"', text):
                self.assertIn(table, AI_TABLES, path.name)
            self.assertNotIn('consumer="LOCAL_CLI"', text)
            self.assertNotIn("consumer='LOCAL_CLI'", text)
        from scivetra_kb.retrieval import service
        self.assertEqual(service.CONSUMERS, ("LOCAL_CLI",))

    def test_configuration_templates_hold_no_secrets(self):
        """IDs: S10-CONFIG-TEMPLATES. The shipped templates exist, parse as JSON, contain placeholders instead of paths, no key / token /
        password field, no URL and no enabled remote transport."""
        templates = sorted((ROOT / "config" / "s10").glob("*.json"))
        self.assertGreaterEqual(len(templates), 3)
        for path in templates:
            text = path.read_text(encoding="utf-8")
            json.loads(text)
            self.assertNotRegex(text, r"(?i)api[_-]?key|secret|password|bearer|token\"\s*:")
            self.assertNotRegex(text, r"[A-Za-z]:\\\\Users|/home/|https?://")
            self.assertNotRegex(text, r'"enabled"\s*:\s*true')

    def test_docs_and_version(self):
        """IDs: S10-DOCS, S10-STATIC-VERSION. Version 1.0.1 (S11: docs/S11_TEST_ADAPTATIONS.md #31); the ADR, beginner guides, capability matrix, scenario catalogue,
        vendor references and test adaptations exist and state the gates, the default deny, the transport states and the limits."""
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.1")  # S11: docs/S11_TEST_ADAPTATIONS.md #31 (after S10)
        docs = ROOT / "docs"
        adr = (docs / "ADR_S10.md").read_text(encoding="utf-8")
        for needle in ("A2", "A3", "A5", "A7", "A8", "OAG-6", "EXTERNAL_UNVERIFIED", "NON_AUTHORITATIVE", "UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS",
                       "NO_AUTHORIZED_EVIDENCE", "rkb-retrieval-consumer/1", "scivetra-ai-tool-result/1", "0010_ai_integration_gateway.sql",
                       "BEGIN IMMEDIATE", "CONCURRENT_SERVICE_USE", "LOCAL_MCP_NOT_CONNECTED", "REMOTE_NOT_APPROVED", "EXTERNAL_API_DISABLED", "OFFLINE_MOCK",
                       "S10-F01", "scivetra-ai-consumer-policy/2", "AI-D11"):
            self.assertIn(needle, adr)
        for name, needles in (("S10_CLAUDE_DESKTOP_GUIDE.md", ("claude_desktop_config.json", "rkb ai mcp-serve", "NOT_TESTED", "external AI processing")),
                              ("S10_CHATGPT_FILE_EXCHANGE_GUIDE.md", ("rkb ai exchange-export", "rkb ai exchange-verify", "external AI processing",
                                                                      "RELEASE_STILL_AUTHORIZED", "never")),
                              ("S10_OWNER_APPROVAL_GUIDE.md", ("rkb ai owner-register", "rkb ai approve", "rkb ai revoke", "rkb ai determine-rights")),
                              ("S10_CAPABILITY_MATRIX.md", ("NOT_TESTED", "BLOCKED", "LOCAL_MCP_NOT_CONNECTED", "REMOTE_NOT_APPROVED")),
                              ("S10_VENDOR_REFERENCES.md", ("2026-11-03", "support.claude.com", "help.openai.com")),
                              ("S10_SCENARIO_CATALOGUE.md", ("S10-SC-01", "S10-SC-62", "S10-SC-76", "S10-MCP-13", "S10-XCH-15")),
                              ("S10_RIGHTS_DECISION_MATRIX.md", ("METADATA", "ANCHOR", "A8", "A7", "NO_RIGHTS_ASSESSMENT", "WITHHELD_NOTHING_RELEASED"))):
            text = (docs / name).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, name)
        adaptations = (docs / "S10_TEST_ADAPTATIONS.md").read_text(encoding="utf-8")
        for n in range(1, 29):
            self.assertIn(f"| {n} |", adaptations)

    def test_accepted_s09_tests_changed_only_on_marked_lines(self):
        """IDs: S10-TEST-ADAPTATIONS, S10-BASELINE-PRESERVED. Every S09 v02 test and tool file is still present; files outside the adapted
        set are byte-identical to the accepted S09 v02 ZIP (tools/s09v02_test_baseline.json); in adapted files every changed or added
        block carries an 'S10' marker; no accepted line is deleted."""
        adapted = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_bridge_paths_backup.py",
                   "tests/test_journal_migration.py", "tests/test_s04j_static.py", "tests/test_s05_static.py", "tests/test_fte_migration.py",
                   "tests/test_s06_static.py", "tests/test_s06_artifacts.py", "tests/test_s07_operational.py", "tests/test_s08_operational.py",
                   "tests/test_s09_operational.py", "tools/required_ids.json"}
        baseline = json.loads((ROOT / "tools" / "s09v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
        changed = 0
        for rel, info in sorted(baseline.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            data = path.read_bytes()
            if rel not in adapted:
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"], rel)
                continue
            lines = data.splitlines()
            now = [hashlib.sha256(line).hexdigest()[:16] for line in lines]
            for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=info["line_sha256"], b=now, autojunk=False).get_opcodes():
                if tag == "equal":
                    continue
                self.assertNotEqual(tag, "delete", (rel, a0, a1))
                block = [lines[i].decode("utf-8") for i in range(b0, b1)]
                if rel.startswith("tests/"):
                    self.assertTrue(any("S10" in line for line in block), (rel, block))
                    changed += b1 - b0
        self.assertLess(changed, 40)


if __name__ == "__main__":
    unittest.main()
