"""Static properties of the source tree (complements the runtime tests)."""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "scivetra_kb"
SOURCES = {p.relative_to(SRC).as_posix(): p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")}


class StaticTests(unittest.TestCase):
    def test_no_network_client_code(self):
        """IDs: STATIC-01, OPS-LIVE-01. The package contains no socket/HTTP client code (no live transport in S02)."""
        pattern = re.compile(r"^\s*(import|from)\s+(socket|ssl|http\.client|urllib\.request|urllib3|requests|httpx|aiohttp)\b", re.M)
        # S06: docs/S06_TEST_ADAPTATIONS.md #19: the parser child imports socket ONLY inside its sandbox self-tests, to prove the audit hook refuses it.
        offenders = [name for name, text in SOURCES.items() if pattern.search(text) and name != "parse/parse_child.py"]  # S06 #19
        self.assertEqual(pattern.findall(SOURCES["parse/parse_child.py"]), [("import", "socket")])  # S06 #19: exactly the one self-test probe import
        self.assertEqual(offenders, [])

    def test_no_reprovane_or_pbd_imports(self):
        """IDs: STATIC-02, N15, N16-SUBSET. No Reprovane store/Database import and no PaperBatchDownloader access."""
        pattern = re.compile(r"^\s*(import|from)\s+reprovane\b", re.M)
        self.assertEqual([n for n, t in SOURCES.items() if pattern.search(t)], [])
        self.assertEqual([n for n, t in SOURCES.items() if "litintel.sqlite" in t], [])

    def test_counter_evidence_single_writer(self):
        """IDs: STATIC-03, S02-PROP-8. Only IntegrityService.record_counter_evidence inserts counter-evidence; no other module names the table in an insert."""
        writers = [n for n, t in SOURCES.items() if re.search(r"insert\(\s*\"integrity_counter_evidence\"", t)]
        self.assertEqual(writers, ["metadata/integrity.py"])
        text = SOURCES["metadata/integrity.py"]
        body = text.split("def record_counter_evidence", 1)[1].split("\n    def ", 1)[0]
        self.assertIn('self.kb.insert("integrity_counter_evidence"', body)
        self.assertEqual(text.count('insert("integrity_counter_evidence"'), 1)
        for forbidden in ("SOURCE_REPORT_WITHDRAWN", "SYSTEM_DIFF", "NOT_CHECKED"):
            self.assertEqual([n for n, t in SOURCES.items() if forbidden in t], [], forbidden)

    def test_coverage_never_changes_alert(self):
        """IDs: STATIC-04, S02-PROP-8. The alert derivation reads only events, counter-evidence and reviews, never coverage."""
        text = SOURCES["metadata/integrity.py"]
        alert = text.split("def alert(", 1)[1]
        self.assertNotIn("coverage", alert)

    def test_doi_parser_has_no_generic_cleanup(self):
        """IDs: STATIC-05, S02-PROP-9. doi.py never applies argument-less strip(), casefold() or Unicode normalization to identifiers."""
        text = SOURCES["metadata/doi.py"]
        for forbidden in (".strip()", ".lstrip()", ".rstrip()", "casefold(", "unicodedata.normalize(", ".lower()"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_parsers_have_no_broad_exception_handlers(self):
        """IDs: STATIC-07, S02-MALFORMED-NARROW. Trust-boundary parsers catch only named exceptions (MalformedResponse, decode errors); no `except Exception` or bare `except:` that could hide programmer errors."""
        for name in ("metadata/crossref.py", "metadata/datacite.py", "metadata/cn.py", "metadata/ra.py", "metadata/shape.py", "metadata/service.py"):
            text = SOURCES[name]
            self.assertIsNone(re.search(r"except\s*:", text), name)
            self.assertIsNone(re.search(r"except\s+(Exception|BaseException)\b", text), name)
        for name in ("metadata/crossref.py", "metadata/datacite.py", "metadata/cn.py"):
            self.assertIn("except MalformedResponse as exc:", SOURCES[name], name)

    def test_provider_parsers_use_strict_decoder(self):
        """IDs: STATIC-08, S02-UNSAFE-NARROW. The four provider parsers decode only through strict_json (no direct json.loads); strict_json catches only named decoder failures."""
        for name in ("metadata/crossref.py", "metadata/datacite.py", "metadata/cn.py", "metadata/ra.py"):
            text = SOURCES[name]
            self.assertNotIn("json.loads", text, name)
            self.assertIn("strict_decode(body)", text, name)
            self.assertIn("except UnsafeJson as exc:", text, name)
        text = SOURCES["metadata/strict_json.py"]
        self.assertIsNone(re.search(r"except\s*:", text))
        self.assertIsNone(re.search(r"except\s+(Exception|BaseException)\b", text))
        handlers = sorted(set(re.findall(r"^\s*except\s+([^:\n]+):", text, re.M)))
        self.assertEqual(handlers, ["(UnicodeDecodeError, ValueError)", "RecursionError", "UnicodeEncodeError"])

    def test_no_reads_of_production_paths(self):
        """IDs: STATIC-06. E:\\ResearchKB appears only in the protected-roots refusal list; PBD state only in the deny list."""
        hits = [n for n, t in SOURCES.items() if "ResearchKB" in t]
        self.assertEqual(hits, ["core/paths.py"])


if __name__ == "__main__":
    unittest.main()
