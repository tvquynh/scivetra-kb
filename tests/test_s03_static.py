"""Static properties of the S03 additions (complements tests/test_static.py)."""
import ast
import re
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "scivetra_kb"
SOURCES = {p.relative_to(SRC).as_posix(): p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}
S03_MODULES = [n for n in SOURCES if n.startswith(("library/", "assets/", "rights/")) or n in ("core/backup.py", "core/upgrade.py")]


class S03StaticTests(unittest.TestCase):
    def test_cli_never_bypasses_schema_check(self):
        """IDs: STATIC-09, N15. Only backup/upgrade (and test fixtures) pass _allow_older_schema; the CLI and library/asset code never do."""
        users = sorted(n for n, t in SOURCES.items() if "_allow_older_schema=True" in t)
        self.assertEqual(users, ["core/backup.py", "core/upgrade.py"])

    def test_pdf_child_is_stdlib_only(self):
        """IDs: STATIC-10, S03-G1-SANDBOX. The sandboxed validator imports only stdlib modules (pypdf only from an explicit --pypdf-site) and nothing from the KB package."""
        tree = ast.parse(SOURCES["assets/pdf_child.py"])
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        self.assertTrue(all(n.level == 0 for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)))   # no relative imports
        self.assertEqual(names - set(sys.stdlib_module_names), {"pypdf"})
        self.assertNotIn("scivetra_kb", names)

    def test_subprocess_only_in_sandbox(self):
        """IDs: STATIC-11. The only module that starts a process is assets/sandbox.py (the G1 validator child)."""
        users = sorted(n for n, t in SOURCES.items() if re.search(r"^\s*(import subprocess|from subprocess)", t, re.M))
        self.assertEqual(users, ["assets/sandbox.py", "parse/parse_child.py", "parse/sandbox.py"])  # S06: docs/S06_TEST_ADAPTATIONS.md #20

    def test_no_permissive_rights_defaults(self):
        """IDs: STATIC-12, S03-RIGHTS-DB-DENY. No S03 module assigns ALLOW to redistribute or external_ai_process."""
        pattern = re.compile(r"[\"'](redistribute|external_ai_process)[\"']\s*:\s*[\"']ALLOW[\"']")
        self.assertEqual([n for n in S03_MODULES if pattern.search(SOURCES[n])], [])

    def test_no_broad_exception_handlers_in_s03(self):
        """IDs: STATIC-13. S03 modules catch only named exceptions (no bare except / except Exception / except BaseException without re-raise)."""
        for name in S03_MODULES:
            text = SOURCES[name]
            self.assertIsNone(re.search(r"except\s*:", text), name)
            self.assertIsNone(re.search(r"except\s+Exception\b", text), name)
            for match in re.finditer(r"except BaseException:\n((?:\s{8,}.*\n)+)", text):
                self.assertIn("raise", match.group(1), name)

    def test_openalex_never_an_auto_link_key(self):
        """IDs: STATIC-14, IA-4, PPR-2. link-policy/1 declares only doi/pmid/pmcid/arxiv as MLI and the migration CHECK restricts EXACT_MANIFESTATION_ID keys to them."""
        self.assertIn('MLI_NAMESPACES = ("doi", "pmid", "pmcid", "arxiv")', SOURCES["library/linking.py"])
        sql = (SRC / "core" / "migrations" / "0002_global_research_library.sql").read_text(encoding="utf-8")
        self.assertIn("IN ('doi','pmid','pmcid','arxiv')", sql)

    def test_no_reprovane_database_or_write_paths(self):
        """IDs: STATIC-15, N16. S03 code never opens a Reprovane DB, never imports reprovane and never writes into an export directory (only reads)."""
        for name in S03_MODULES:
            text = SOURCES[name]
            self.assertNotIn("litintel.sqlite", text, name)
            self.assertIsNone(re.search(r"^\s*(import|from)\s+reprovane\b", text, re.M), name)
        reader = SOURCES["metadata/rv_import.py"].split("def _read_export", 1)[1].split("\ndef import_corpus_export", 1)[0]
        self.assertNotRegex(reader, r"write_bytes|write_text|open\([^)]*[\"'][wax]")


if __name__ == "__main__":
    unittest.main()
