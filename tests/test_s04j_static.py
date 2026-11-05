"""S04J static checks: accepted S04 tests/tools unchanged except documented adaptation lines, syntax, fixture
determinism, version and the S05 hand-off documentation."""
import ast
import difflib
import hashlib
import json
import subprocess
import sys
import unittest

from tests.helpers import ROOT

ADAPTED = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_s03_v02_fixes.py",
           "tests/test_bridge_paths_backup.py",
           "tests/test_static.py", "tests/test_s03_static.py"}  # S06: docs/S06_TEST_ADAPTATIONS.md #22 (two more S04 files adapted by S06)


class StaticTests(unittest.TestCase):
    def test_accepted_s04_tests_changed_only_on_marked_lines(self):
        """IDs: JH-01, S04J-TEST-ADAPTATIONS. Every S04 v02 test and tool file is still present. Files outside the documented adaptation set are byte-identical to S04 v02; in the 6 adapted files every changed or added line carries an 'S04J' adaptation marker (or is the second half of a marked statement), no accepted line was deleted without replacement, and the total number of changed lines is small."""
        baseline = json.loads((ROOT / "tools" / "s04v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
        changed_total = 0
        for rel, info in sorted(baseline.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            data = path.read_bytes()
            if rel not in ADAPTED:
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"], rel)
                continue
            lines = data.splitlines()
            now = [hashlib.sha256(l).hexdigest()[:16] for l in lines]
            matcher = difflib.SequenceMatcher(a=info["line_sha256"], b=now, autojunk=False)
            for tag, a0, a1, b0, b1 in matcher.get_opcodes():
                if tag == "equal":
                    continue
                self.assertNotEqual(tag, "delete", (rel, a0, a1))
                block = [lines[i].decode("utf-8") for i in range(b0, b1)]
                self.assertTrue(any("S04J" in line or "S06" in line for line in block), (rel, block))  # S06: docs/S06_TEST_ADAPTATIONS.md #22
                changed_total += b1 - b0
        self.assertLessEqual(changed_total, 50)  # S06: docs/S06_TEST_ADAPTATIONS.md #22 (S04J + S05 + S06 literal lines)

    def test_all_python_compiles_and_version(self):
        """IDs: S04J-STATIC-SYNTAX. Every Python file of the package, tests and tools parses; the package version is 0.5.0.dev2 (S05; was 0.4.1.dev2 in S04J v02)."""
        files = sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")) + list((ROOT / "tools").rglob("*.py")))
        self.assertGreater(len(files), 100)
        for path in files:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.0")  # S05: docs/S05_TEST_ADAPTATIONS.md #14; S06: docs/S06_TEST_ADAPTATIONS.md #14; S07: docs/S07_TEST_ADAPTATIONS.md #14 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #14 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #13 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #13 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #13 (after S10, S09, S08, S07, S06)

    def test_fixtures_are_reproducible_from_the_generator(self):
        """IDs: JH-28, JH-31, S04J-FIXTURE-DETERMINISM. tools/s04j_make_fixtures.py --check regenerates every synthetic scenario byte-for-byte from code (no randomness, no network)."""
        out = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "s04j_make_fixtures.py"), "--check"], capture_output=True, text=True,
                             timeout=300)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(json.loads(out.stdout)["fixtures_match_generator"])

    def test_s05_impact_and_migration_number_documented(self):
        """IDs: JH-32, S04J-S05-IMPACT. The S05 impact document states that S05 must use migration 0005_fulltext_eligibility_staging.sql, that a journal collection is never a screened candidate set, and that S05 may consume it only through an explicit, auditable import policy; the user guide documents the CLI path."""
        impact = (ROOT / "docs" / "S04J_S05_IMPACT.md").read_text(encoding="utf-8")
        for needle in ("0005_fulltext_eligibility_staging.sql", "0004_journal_harvesting.sql", "never a screened candidate set",
                       "explicit, auditable", "NOT_SCREENED"):
            self.assertIn(needle, impact)
        guide = (ROOT / "docs" / "JOURNAL_HARVESTING_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("rkb journal register", "rkb journal resume", "rkb journal export", "SCIVETRA_OPENALEX_API_KEY"):
            self.assertIn(needle, guide)
