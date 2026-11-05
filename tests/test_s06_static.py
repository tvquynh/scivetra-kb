"""S06 static checks: S05 v02 baseline preserved, parse package boundaries, documentation."""
import ast
import difflib
import hashlib
import json
import re
import unittest

from tests.helpers import ROOT

ADAPTED = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_s03_v02_fixes.py",
           "tests/test_bridge_paths_backup.py", "tests/test_journal_migration.py", "tests/test_s04j_static.py", "tests/test_s05_static.py",
           "tests/test_fte_migration.py", "tests/test_static.py", "tests/test_s03_static.py", "tools/required_ids.json",
           "tools/documented_gaps.json"}
PARSE = ROOT / "src" / "scivetra_kb" / "parse"


class StaticTests(unittest.TestCase):
    def test_accepted_s05_tests_changed_only_on_marked_lines(self):
        """IDs: S06-TEST-ADAPTATIONS, S06-BASELINE-PRESERVED. Every S05 v02 test and tool file is still present; files outside the documented
        adaptation set are byte-identical to the accepted S05 v02 ZIP; in the adapted files every changed or added block carries an 'S06' marker
        (or an S06 contract ID), no accepted line is deleted, and fewer than 40 lines of accepted test code change."""
        baseline = json.loads((ROOT / "tools" / "s05v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
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
                self.assertTrue(any("S06" in line for line in block), (rel, block))
                if rel.startswith("tests/"):
                    changed += b1 - b0
        self.assertLess(changed, 40)

    def test_version_and_syntax(self):
        """IDs: S06-STATIC-SYNTAX. Every Python file parses; the package version is 0.6.0.dev2; pypdf is an exactly pinned optional extra."""
        for path in sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.0")  # S07: docs/S07_TEST_ADAPTATIONS.md #18 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #18 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #17 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #17 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #17 (after S10, S09, S08, S07, S06)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('pdf = ["pypdf==6.12.1"]', pyproject)
        self.assertIn("dependencies = []", pyproject)

    def test_parse_package_boundaries(self):
        """IDs: S06-STATIC-BOUNDARY, S06-NO-AI, S06-NO-NETWORK-CLIENT, S06-CHILD-STANDALONE. The parse package imports no HTTP/network client, AI SDK,
        OCR engine, browser automation or file watcher; the child imports nothing from scivetra_kb and imports socket/subprocess only inside its
        self-tests; no module names the production root or Reprovane/PaperBatchDownloader code; nothing calls PDF actions or opens URLs."""
        forbidden = re.compile(r"^(ssl|urllib\.request|http\.client|requests|httpx|aiohttp|openai|anthropic|pytesseract|ocrmypdf|watchdog|selenium|"
                               r"playwright|webbrowser|reprovane|paperbatchdownloader|fitz|pymupdf)")
        for path in sorted(PARSE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    self.assertIsNone(forbidden.match(name.lower()), (path.name, name))
            self.assertNotIn("ResearchKB", text, path.name)
        child = (PARSE / "parse_child.py").read_text(encoding="utf-8")
        self.assertNotRegex(child, r"(?m)^\s*(from|import)\s+(scivetra_kb|\.)")
        self_tests = child.split("def _self_test", 1)[1].split("\ndef ", 1)[0]
        outside = child.replace(self_tests, "")
        self.assertNotRegex(outside, r"(?m)^\s*import (socket|subprocess)\b")

    def test_docs_present_and_state_the_boundaries(self):
        """IDs: S06-DOCS, S06-OCR-CONTRACT, S06-OUT-OF-SCOPE. The ADR, user guide, adaptation list and OCR adapter contract exist and state the
        parser pin, the C17 rule, the Windows network limitation, metadata-only exports and the out-of-scope items."""
        docs = ROOT / "docs"
        adr = (docs / "ADR_S06.md").read_text(encoding="utf-8")
        guide = (docs / "PARSE_USER_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("pypdf==6.12.1", "BSD-3-Clause", "rkb-pdf-structure/2", "OS_NETWORK_ISOLATION_UNAVAILABLE", "NOT_ELIGIBLE_FOR_EVIDENCE",
                       "parse-artifact/1", "AGPL"):
            self.assertIn(needle, adr)
        for needle in ("rkb parse run", "rkb parse show", "rkb parse verify", "rkb parse export", "--accept-language-level-network-guard",
                       "E:\\ResearchPlatformDev", "metadata-only"):
            self.assertIn(needle, guide)
        self.assertIn("ocr-adapter/1", (docs / "OCR_ADAPTER_CONTRACT.md").read_text(encoding="utf-8"))
        adaptations = (docs / "S06_TEST_ADAPTATIONS.md").read_text(encoding="utf-8")
        for n in range(1, 23):
            self.assertIn(f"| {n} |", adaptations)


if __name__ == "__main__":
    unittest.main()
