"""S08 operational checks: migration 0008, upgrade 7 -> 8, backup/restore, interrupted writes, concurrency, protected S05/S07
state, CLI, static boundaries, documentation and the S07 v02 baseline guard."""
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
from tests.s08_helpers import MALWARE_DOC, S08Case
from scivetra_kb import cli
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError, LockError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.evidence.service import EvidenceService

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"
EVIDENCE = ROOT / "src" / "scivetra_kb" / "evidence"
ADAPTED = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_s03_v02_fixes.py",
           "tests/test_bridge_paths_backup.py", "tests/test_journal_migration.py", "tests/test_s04j_static.py", "tests/test_s05_static.py",
           "tests/test_fte_migration.py", "tests/test_s06_static.py", "tests/test_s06_artifacts.py", "tests/test_s07_operational.py",
           "tools/required_ids.json"}
EV_TABLES = 10


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class MigrationAndRecovery(S08Case):
    def test_0008_additive_and_0001_0007_unchanged(self):
        """IDs: S08-MIG-HASH, S08-MIG-ADDITIVE, S08-MIG-FRESH-INIT, S08-APPEND-ONLY-TRIGGERS. 0001-0007 are byte-identical to the accepted S07 v02; 0008_scientific_evidence_extraction.sql only CREATEs ev_* tables and triggers; every ev_* table has no-update/no-delete triggers; a fresh KB is schema 8."""
        expected = json.loads((ROOT / "tools" / "s07v02_migration_hashes.json").read_text())
        actual = {m[1]: m[2] for m in packaged_migrations()}
        self.assertEqual({k: actual[k] for k in expected}, expected)
        self.assertEqual(len(expected), 7)
        self.assertEqual([m[1] for m in packaged_migrations()][7], "0008_scientific_evidence_extraction.sql")  # S09: docs/S09_TEST_ADAPTATIONS.md #23 (after S08, S07, S06)
        for statement in migrations._statements((MIG / "0008_scientific_evidence_extraction.sql").read_text(encoding="utf-8")):
            self.assertRegex(re.sub(r"--[^\n]*", "", statement).strip(), r"^CREATE (TABLE|TRIGGER) ev_")
        with self.reader() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S09: docs/S09_TEST_ADAPTATIONS.md #23 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #23 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #23 (after S10, S09, S08, S07, S06)
            tables = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'ev_*'")]
            triggers = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertEqual(len(tables), EV_TABLES)
        for t in tables:
            self.assertTrue({f"{t}_no_update", f"{t}_no_delete"} <= triggers, t)

    def test_direct_sql_cannot_rewrite_evidence_history(self):
        """IDs: S08-APPEND-ONLY, S08-TRIGGER-ENFORCED. With rows in every S08 table, a direct SQL UPDATE or DELETE on any of them is
        aborted by the database; history cannot be silently rewritten."""
        run, items, scope, _ = self.mocked([[MALWARE_DOC]])
        self.roster("rev.one", "rev.two", "rev.adj")
        d = [x for x in self.drafts(scope) if x["evidence_class"] == "NUMERICAL_RESULT"][0]["draft_id"]
        self.ecall("verify", draft_id=d, operator="op.v")
        for r in ("rev.one", "rev.two"):
            self.ecall("reveal", draft_id=d, reviewer=r)
        self.ecall("review", draft_id=d, reviewer="rev.one", decision="ACCEPT")
        self.ecall("export", scope_id=scope, out_dir=os.path.join(self.tmp, "x"), operator="op.owner")
        self.ecall("review", draft_id=d, reviewer="rev.two", decision="REJECT", reason_codes=["X_Y"], rationale="disagree")
        self.ecall("reveal", draft_id=d, reviewer="rev.adj")
        self.ecall("adjudicate", draft_id=d, adjudicator="rev.adj", outcome="REJECT", rationale="operating point unclear")
        with self.writer() as kb:
            for t in [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'ev_*'")]:
                self.assertGreater(kb.one(f"SELECT count(*) n FROM {t}")["n"], 0, t)
                col = kb.all(f"PRAGMA table_info({t})")[1]["name"]
                for sql in (f"UPDATE {t} SET {col}={col}", f"DELETE FROM {t}"):
                    with self.assertRaises(KBError, msg=sql):
                        kb.execute(sql)

    def test_upgrade_7_to_8_backup_first_and_rollback(self):
        """IDs: S08-UPGRADE-7-8, S08-ROLLBACK. A schema-7 KB is upgraded only after a verified backup; only 0008 is applied; every prior table is byte-identical (except the ledger and upgrade audit rows); the kept pre-upgrade file restores the exact schema-7 bytes."""
        v7 = os.path.join(self.tmp, "kb_v7")
        make_older_kb(v7, 7, clock=self.clock, ids=self.ids, client="0.7.0.dev2")
        with umock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:7]):
            with open_reader(v7) as kb:
                before = {t: table_content_sha256(kb.connection, t) for t in
                          [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]}
        original = sha(Path(v7, DB_RELATIVE))
        out = upgrade_kb(v7, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (7, 11, ["0008_scientific_evidence_extraction.sql", "0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S09: docs/S09_TEST_ADAPTATIONS.md #24 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #24 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #24 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        with open_reader(v7) as kb:
            after = {t: table_content_sha256(kb.connection, t) for t in before}
        audit = ("kb_upgrade_event", "schema_migration")
        self.assertEqual({k: v for k, v in after.items() if k not in audit}, {k: v for k, v in before.items() if k not in audit})
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v7, rollback)
        shutil.copyfile(Path(v7, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        self.assertEqual(doctor(v7, full=True)["problems"], [])

    def test_backup_restore_interrupted_write_and_concurrency(self):
        """IDs: S08-BACKUP-RESTORE, S08-INTERRUPTED-WRITE, S08-CONCURRENCY, S08-DOCTOR-INVARIANTS, S08-CRASH-RETRY. A crash while drafts are being inserted rolls back the whole extraction run (no run row, no draft) and a retry completes; a second concurrent writer is refused (LockError); a backup of the non-empty KB carries every input pack, verifies and restores; doctor --full on the restore is clean with zero S08 invariant violations and the same evidence manifest."""
        run, items = self.ev_world([[MALWARE_DOC]])
        scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        with self.writer() as kb:
            real = kb.insert

            def failing(table, row):
                if table == "ev_draft":
                    raise OSError("injected crash before commit")
                return real(table, row)
            kb.insert = failing
            with self.assertRaises(OSError):
                EvidenceService(kb).run_mock(scope_id=scope, operator="op.runner")
            self.assertEqual((kb.one("SELECT count(*) n FROM ev_extraction_run")["n"], kb.one("SELECT count(*) n FROM ev_draft")["n"]), (0, 0))
            with self.assertRaises(LockError):
                open_writer(self.root).__enter__()
        self.assertEqual(self.ecall("run_mock", scope_id=scope, operator="op.runner")["results"][0]["status"], "OK")
        self.roster("rev.one")
        for d in self.drafts(scope):
            self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
            self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
            self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT")
        backup = create_backup(self.root, os.path.join(self.tmp, "backups"), clock=self.clock)
        self.assertTrue(verify_backup(os.path.join(self.tmp, "backups", backup["backup_name"]))["valid"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(os.path.join(self.tmp, "backups", backup["backup_name"]), restored)
        report, original = doctor(restored, full=True), doctor(self.root, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["evidence_invariants"].values()), {0})
        self.assertEqual(report["evidence_manifest"], original["evidence_manifest"])
        self.assertEqual(report["evidence_manifest"]["counts"]["ev_review"], 8)
        with open_reader(restored) as kb:
            for row in kb.all("SELECT pack_sha256 FROM ev_input_pack"):
                kb.cas.verify_object(row["pack_sha256"])
            self.assertEqual(EvidenceService(kb).scope_summary(scope_id=scope)["accepted_evidence"], 8)


class ProtectedState(S08Case):
    def test_s05_s07_and_release_tables_unchanged_by_s08(self):
        """IDs: S08-NO-S05-WRITES, S08-NO-S07-WRITES, S08-NO-REPROVANE-WRITES, S08-CAPABILITY-GATES. With S05 ballots, an S07 proposal and approval and a Reprovane release import present, a complete S08 cycle (scope, packs, mock, import, verify, reveal, review, correction, adjudication, export) leaves every S05 (fte_*), S07 (ai_* except the shared roster registered beforehand), release-import and S06 table byte-identical; the evidence package only inserts into ev_* tables."""
        run, items = self.ev_world([[MALWARE_DOC]])
        # an S07 FT proposal + approval on the same paper, before S08 runs
        scope7 = self.ft_scope(run, [{"candidate_id": items[0]["candidate"]["candidate_id"], "parse_artifact_id": items[0]["artifact_id"]}])
        self.call("build_packs", scope_id=scope7, operator="op.owner")
        pid = self.call("run_mock", scope_id=scope7, operator="op.runner")["results"][0]["proposal_id"]
        self.call("verify", proposal_id=pid, operator="op.v")
        self.roster("rev.one", "rev.two", "rev.adj")
        self.call("record_initial_judgement", scope_id=scope7, paper_id=items[0]["candidate"]["paper_id"], reviewer="rev.one", outcome="INCLUDE")
        self.call("reveal", proposal_id=pid, reviewer="rev.one")
        self.call("approve", proposal_id=pid, reviewer="rev.one", decision="DEFER")
        with self.reader() as kb:
            names = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT GLOB 'ev_*'")]
            before = {t: table_content_sha256(kb.connection, t) for t in names}
        scope = self.ev_scope(run, items, review_policy="DUAL_INDEPENDENT")
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        self.ecall("run_mock", scope_id=scope, operator="op.runner")
        drafts = self.drafts(scope)
        for d in drafts:
            self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
        d = [x for x in drafts if x["evidence_class"] == "NUMERICAL_RESULT"][0]["draft_id"]
        for r, dec in (("rev.one", "ACCEPT"), ("rev.two", "REJECT")):
            self.ecall("reveal", draft_id=d, reviewer=r)
            self.ecall("review", draft_id=d, reviewer=r, decision=dec, **({"reason_codes": ["X_Y"], "rationale": "r"} if dec == "REJECT" else {}))
        self.ecall("reveal", draft_id=d, reviewer="rev.adj")
        self.ecall("adjudicate", draft_id=d, adjudicator="rev.adj", outcome="ACCEPT", rationale="quote supports it")
        self.ecall("export", scope_id=scope, out_dir=os.path.join(self.tmp, "exp"), operator="op.owner", content_level="ACCEPTED_TYPED_VALUES")
        with self.reader() as kb:
            after = {t: table_content_sha256(kb.connection, t) for t in names}
        changed = sorted(t for t in names if before[t] != after[t])
        self.assertEqual(changed, [])
        for path in sorted(EVIDENCE.glob("*.py")):
            for table in re.findall(r'insert\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")):
                self.assertTrue(table.startswith("ev_"), (path.name, table))


class CliAndStatic(S08Case):
    def cli(self, *argv):
        return cli.run(list(argv), clock=self.clock, ids=self.ids)

    def test_evidence_cli_path(self):
        """IDs: S08-CLI-FLOW, S08-CLI-EXIT-CODES, S08-CLI-NO-QUOTE-IN-SHOW. The rkb evidence commands cover profile, scope, packs, mock, import, verify, state/show/audit/summary, reveal, review, adjudicate and export; show never contains the quote; an UNSUPPORTED verification and a SCHEMA_INVALID import exit 3; refusals exit 2 with a stable code."""
        run, items = self.ev_world([[MALWARE_DOC]])
        code, prof = self.cli("evidence", "profile-register", "--data-root", self.root, "--builtin", "rkb-malware-detection", "--operator", "op.owner")
        self.assertEqual(code, 0)
        spec = Path(self.tmp, "scope.json")
        spec.write_text(json.dumps({"fte_run_id": run["run_id"], "profile_id": prof["profile_id"], "label": "cli scope", "review_policy": "SINGLE_HUMAN",
                                    "review_policy_reason": "cli test",
                                    "members": [{"candidate_id": items[0]["candidate"]["candidate_id"], "parse_artifact_id": items[0]["artifact_id"]}]}),
                        encoding="utf-8")
        sid = self.cli("evidence", "scope-create", "--data-root", self.root, "--scope-file", str(spec), "--operator", "op.owner")[1]["scope_id"]
        self.assertEqual(self.cli("evidence", "build-packs", "--data-root", self.root, "--scope-id", sid, "--operator", "op.owner")[1]["counts"]["PACKED"], 1)
        drafts = self.cli("evidence", "run-mock", "--data-root", self.root, "--scope-id", sid, "--operator", "op.runner")[1]["results"][0]["draft_ids"]
        code, ver = self.cli("evidence", "verify", "--data-root", self.root, "--draft-id", drafts[0], "--operator", "op.v")
        self.assertEqual((code, ver["state"]), (0, "STRUCTURALLY_VERIFIED"))
        code, shown = self.cli("evidence", "show", "--data-root", self.root, "--draft-id", drafts[0])
        self.assertEqual((code, shown["quote"]), (0, "WITHHELD_USE_REVEAL"))
        with self.reader() as kb:
            prow = kb.one("SELECT * FROM ev_input_pack WHERE scope_id=?", (sid,))
            pack = json.loads(kb.cas.read(prow["pack_sha256"]))
        seg = next(s for s in pack["segments"] if s["text"].startswith("TPR"))
        fake = "TPR of 99.9% at 1% FPR"
        record = {"evidence_class": "NUMERICAL_RESULT", "value_status": "REPORTED", "source_kind": "FULLTEXT_TEXT", "confidence": None, "note": "",
                  "fields": {"metric": "TPR", "value": "99.9", "unit": "%"},
                  "anchors": [{"page": seg["page"], "char_start": seg["start"], "char_end": seg["start"] + len(fake), "quote": fake,
                               "quote_sha256": hashlib.sha256(fake.encode()).hexdigest()}]}
        resp = Path(self.tmp, "response.json")
        body = {"schema": "rkb-evidence-response/1", "input_pack_sha256": prow["pack_sha256"], "paper_id": pack["paper_id"],
                "document_version_id": pack["document_version_id"], "artifact_sha256": pack["artifact"]["artifact_sha256"],
                "profile_sha256": pack["profile"]["profile_sha256"], "records": [record], "notes": ""}
        resp.write_text(json.dumps(body), encoding="utf-8")
        model = Path(self.tmp, "model.json")
        model.write_text(json.dumps({"provider": "local", "model_id": "operator-model"}), encoding="utf-8")
        code, imported = self.cli("evidence", "import", "--data-root", self.root, "--pack-id", prow["pack_id"], "--response-file", str(resp),
                                  "--model-file", str(model), "--operator", "op.importer")
        self.assertEqual((code, imported["status"]), (0, "OK"))
        code, failed = self.cli("evidence", "verify", "--data-root", self.root, "--draft-id", imported["draft_ids"][0], "--operator", "op.v")
        self.assertEqual((code, failed["state"]), (3, "UNSUPPORTED"))
        resp.write_text(json.dumps(dict(body, paper_id="gp_x")), encoding="utf-8")
        self.assertEqual(self.cli("evidence", "import", "--data-root", self.root, "--pack-id", prow["pack_id"], "--response-file", str(resp),
                                  "--model-file", str(model), "--operator", "op.importer")[0], 3)
        self.cli("screen", "reviewer-register", "--data-root", self.root, "--label", "rev.cli", "--operator", "op.owner", "--reason", "owner roster")
        self.assertEqual(cli.main(["evidence", "review", "--data-root", self.root, "--draft-id", drafts[0], "--reviewer", "rev.cli", "--decision", "ACCEPT"]), 2)
        self.assertEqual(self.cli("evidence", "reveal", "--data-root", self.root, "--draft-id", drafts[0], "--reviewer", "rev.cli")[1]["quote_disclosure"], "SHOWN")
        self.assertEqual(self.cli("evidence", "review", "--data-root", self.root, "--draft-id", drafts[0], "--reviewer", "rev.cli", "--decision", "ACCEPT")[0], 0)
        self.assertEqual(self.cli("evidence", "state", "--data-root", self.root, "--draft-id", drafts[0])[1]["current_state"], "HUMAN_ACCEPTED")
        self.assertEqual(len(self.cli("evidence", "audit", "--data-root", self.root, "--draft-id", drafts[0])[1]["reviews"]), 1)
        self.assertEqual(self.cli("evidence", "summary", "--data-root", self.root, "--scope-id", sid)[1]["accepted_evidence"], 1)
        self.assertEqual(self.cli("evidence", "export", "--data-root", self.root, "--scope-id", sid, "--out-dir", os.path.join(self.tmp, "e"),
                                  "--operator", "op.owner")[0], 0)

    def test_evidence_package_boundaries(self):
        """IDs: S08-STATIC-BOUNDARY, S08-NO-NETWORK, S08-NO-AI-SDK, S08-MOCK-TOOLLESS, S08-NO-URL-FOLLOW. The evidence package imports no network/HTTP client, AI SDK, subprocess, browser or file watcher; the mock imports only hashlib, json and re and nothing from the KB; no module opens URLs."""
        forbidden = re.compile(r"^(socket|ssl|urllib|http|requests|httpx|aiohttp|openai|anthropic|subprocess|webbrowser|selenium|playwright|watchdog|transformers|torch|llama)")
        for path in sorted(EVIDENCE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    self.assertIsNone(forbidden.match(name), (path.name, name))
            self.assertNotRegex(text, r"urlopen|requests\.get|webbrowser\.open")
        mock_imports = {a.name for n in ast.walk(ast.parse((EVIDENCE / "mock.py").read_text(encoding="utf-8"))) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(mock_imports, {"hashlib", "json", "re"})
        self.assertNotRegex((EVIDENCE / "mock.py").read_text(encoding="utf-8"), r"(?m)^\s*from\s+(?!__future__)")

    def test_docs_and_version(self):
        """IDs: S08-DOCS, S08-STATIC-VERSION. Version 0.8.0.dev3; the ADR, beginner guide and test adaptations exist and state the authority boundary, structural-not-semantic verification, provisional evidence, LOCAL_PROCESS-only and synthetic-only limits."""
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.2")  # S09: docs/S09_TEST_ADAPTATIONS.md #25 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #25 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #25 (after S10, S09, S08, S07, S06)
        adr = (ROOT / "docs" / "ADR_S08.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "EVIDENCE_USER_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("NON_AUTHORITATIVE", "STRUCTURALLY_VERIFIED", "PROVISIONAL_NON_AUTHORITATIVE", "LOCAL_PROCESS", "0008_scientific_evidence_extraction.sql",
                       "mock", "NOT_REPORTED", "entailment"):
            self.assertIn(needle, adr)
        for needle in ("rkb evidence", "reveal", "synthetic", "What remains human work", "$env:"):
            self.assertIn(needle, guide)
        adaptations = (ROOT / "docs" / "S08_TEST_ADAPTATIONS.md").read_text(encoding="utf-8")
        for n in range(1, 21):
            self.assertIn(f"| {n} |", adaptations)
        for path in sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def test_accepted_s07_tests_changed_only_on_marked_lines(self):
        """IDs: S08-TEST-ADAPTATIONS, S08-BASELINE-PRESERVED. Every S07 v02 test and tool file is still present; files outside the adapted set are byte-identical to the accepted S07 v02 ZIP; in adapted files every changed or added block carries an 'S08' marker; no accepted line is deleted."""
        baseline = json.loads((ROOT / "tools" / "s07v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
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
                self.assertTrue(any("S08" in line for line in block), (rel, block))
                if rel.startswith("tests/"):
                    changed += b1 - b0
        self.assertLess(changed, 40)


if __name__ == "__main__":
    unittest.main()
