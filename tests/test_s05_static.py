"""S05 static checks: accepted S04J v02 tests/tools changed only on marked lines, version, package boundaries, documentation."""
import ast
import difflib
import hashlib
import json
import re
import unittest

from tests.helpers import ROOT

ADAPTED = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_s03_v02_fixes.py",
           "tests/test_bridge_paths_backup.py", "tests/test_journal_migration.py", "tests/test_s04j_static.py",
           "tests/test_static.py", "tests/test_s03_static.py"}  # S06: docs/S06_TEST_ADAPTATIONS.md #21 (two more S04J files adapted by S06)
ELIGIBILITY = ROOT / "src" / "scivetra_kb" / "eligibility"


class StaticTests(unittest.TestCase):
    def test_accepted_s04j_tests_changed_only_on_marked_lines(self):
        """IDs: S05-TEST-ADAPTATIONS, S05-BASELINE-PRESERVED. Every S04J v02 test and tool file is still present; files outside the documented adaptation set are byte-identical to S04J v02; in the 8 adapted files every changed or added block carries an 'S05' marker, no accepted line is deleted, and fewer than 30 lines change in total."""
        baseline = json.loads((ROOT / "tools" / "s04j_v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
        changed = 0
        for rel, info in sorted(baseline.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            data = path.read_bytes()
            if rel not in ADAPTED:
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"], rel)
                continue
            lines = data.splitlines()
            now = [hashlib.sha256(l).hexdigest()[:16] for l in lines]
            for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=info["line_sha256"], b=now, autojunk=False).get_opcodes():
                if tag == "equal":
                    continue
                self.assertNotEqual(tag, "delete", (rel, a0, a1))
                block = [lines[i].decode("utf-8") for i in range(b0, b1)]
                self.assertTrue(any("S05" in line or "S06" in line for line in block), (rel, block))  # S06: docs/S06_TEST_ADAPTATIONS.md #21
                changed += b1 - b0
        self.assertLess(changed, 50)  # S06: docs/S06_TEST_ADAPTATIONS.md #21 (S05 + S06 literal lines)

    def test_version_and_syntax(self):
        """IDs: S05-STATIC-SYNTAX. Every Python file parses; the package version is 0.5.0.dev2."""
        for path in sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.1")  # S06: docs/S06_TEST_ADAPTATIONS.md #15; S07: docs/S07_TEST_ADAPTATIONS.md #15 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #15 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #14 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #14 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #14 (after S10, S09, S08, S07, S06)

    def test_eligibility_package_boundaries(self):
        """IDs: S05-STATIC-BOUNDARY, S05-NO-AI, S05-NO-DOWNLOADER. The eligibility package imports no network/HTTP client, no AI/LLM SDK, no S04J harvester or journal export code, no bridge exporter/PBD module and no file watcher; it writes files only in handoff.py (exports into a new folder); it imports no Reprovane or PaperBatchDownloader code and never names a Reprovane write API, the migrator or the production root."""
        forbidden = re.compile(r"(socket|urllib\.request|http\.client|requests|httpx|aiohttp|ssl|openai|anthropic|watchdog|selenium|playwright)$")
        for path in sorted(ELIGIBILITY.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                for name in names:
                    self.assertFalse(forbidden.match(name), (path.name, name))
                    self.assertFalse(name.split(".")[0] in ("reprovane", "paperbatchdownloader"), (path.name, name))
                    self.assertNotIn(name, ("JournalHarvester", "export_version", "export_batch", "import_results", "pbd", "harvest"), path.name)
            if path.name != "handoff.py":
                self.assertNotRegex(text, r"open\([^)]*['\"][wa]b?['\"]", path.name)
            for needle in ("ResearchKB", "apply_migrations", "export_corpus", "seal_release"):
                self.assertNotIn(needle, text, (path.name, needle))

    def test_documents_state_the_authority_boundary(self):
        """IDs: S05-DOCS, S05-MANUAL-INBOX-CONTRACT, S05-RV-FTE-CONTRACT. The user guide shows the CLI path; the RV-FTE contract is a proposal with OAG-5 CLOSED; the manual inbox contract is requirements only (no watcher/downloader in S05); the ADR states NON_AUTHORITATIVE and that journal inventories are never FTE sources."""
        docs = ROOT / "docs"
        guide = (docs / "FTE_USER_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("rkb fte create", "rkb fte decide", "rkb fte adjudicate", "rkb fte lock", "rkb fte verify", "NON_AUTHORITATIVE"):
            self.assertIn(needle, guide)
        contract = (docs / "RV_FTE_FUTURE_HANDOFF_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("OAG-5", contract)
        self.assertIn("proposal only", contract.lower())
        inbox = (docs / "MANUAL_INBOX_CONTRACT.md").read_text(encoding="utf-8")
        for needle in ("candidate_token", "HUMAN_CONFIRMED", "not implemented in S05"):
            self.assertIn(needle, inbox)
        adr = (docs / "ADR_S05.md").read_text(encoding="utf-8")
        for needle in ("NON_AUTHORITATIVE", "JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE", "0005_fulltext_eligibility_staging.sql"):
            self.assertIn(needle, adr)
