"""S12 release candidate: the release gates that run inside the offline suite (G1, G2, G3, G5, G6, G8; packaging G7 and the
populated backup G4 run in tools/s12_release_readiness.py). Synthetic data only; no network."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

spec = importlib.util.spec_from_file_location("s12_release_readiness", ROOT / "tools" / "s12_release_readiness.py")
RR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RR)


class ReleaseGates(unittest.TestCase):
    def test_version_docs_and_dependencies(self):
        """IDs: S12-VERSION, S12-DOCS, S12-DEPENDENCIES. The version is 1.0.0 with a change-log entry and release notes; the user manual, operator runbook, command reference, capability matrix, security review and ADR_S12 exist; the runtime has no dependency and the pdf extra pins pypdf 6.12.1 with the official wheel SHA-256."""
        g1, g8 = RR.g1_version(), RR.g8_dependencies()
        self.assertEqual((g1["status"], g1["version"], g1["missing_docs"]), ("PASS", "1.0.0", []))
        self.assertEqual(g8["status"], "PASS")
        self.assertTrue(g8["inventory"]["stdlib_only_runtime"])

    def test_migrations_pinned_and_upgrade_chain(self):
        """IDs: S12-MIGRATIONS-PINNED, S12-UPGRADE-CHAIN-1-TO-11, S12-ROLLBACK-FILE, S12-UPGRADE-BACKUP-RESTORE. Migrations 0001-0011 are contiguous and equal the pinned hashes; a KB at every earlier schema 1..10 upgrades to 11 (backup first, prior tables byte-identical, doctor --full clean), its kept pre-upgrade file equals the original bytes and its upgrade backup verifies and restores."""
        self.assertEqual(RR.g2_migrations()["status"], "PASS")
        with tempfile.TemporaryDirectory(prefix="rkb-s12-chain-") as tmp:
            g3 = RR.g3_upgrade_chain(Path(tmp))
        self.assertEqual(g3["status"], "PASS", g3)
        self.assertEqual([r["from"] for r in g3["chain"]], list(range(1, 11)))

    def test_security_scan_clean_and_detects_planted_constructs(self):
        """IDs: S12-SECURITY-SCAN, S12-SECURITY-SCAN-NEGATIVE. The AST scan of src/ has no finding (only the four reviewed, allowlisted sandbox constructs); a planted eval, shell=True, pickle import and urllib.request import in a scratch tree are all reported."""
        scan = RR.scan_source(ROOT / "src")
        self.assertEqual(scan["findings"], [])
        self.assertEqual({a["file"] for a in scan["allowlisted"]}, set(RR.ALLOWLIST))
        with tempfile.TemporaryDirectory(prefix="rkb-s12-scan-") as tmp:
            bad = Path(tmp) / "pkg" / "bad.py"
            bad.parent.mkdir()
            bad.write_text("import pickle\nimport subprocess\nimport urllib.request\nx = eval('1')\nsubprocess.run('dir', shell=True)\n", encoding="utf-8")
            kinds = sorted({(f["kind"], f["name"]) for f in RR.scan_source(Path(tmp))["findings"]})
        self.assertEqual(kinds, [("FORBIDDEN_CALL", "eval"), ("FORBIDDEN_IMPORT", "pickle"), ("RESTRICTED_IMPORT", "subprocess"),
                                 ("RESTRICTED_IMPORT", "urllib.request"), ("SHELL_TRUE", "subprocess.run")])

    def test_every_command_documented(self):
        """IDs: S12-CLI-DOCS, S12-COMMAND-REFERENCE-CURRENT. Every rkb command (walked from the parser) appears in the documentation, and docs/COMMAND_REFERENCE.md equals the generated reference."""
        g6 = RR.g6_cli_docs()
        self.assertEqual((g6["status"], g6["undocumented"]), ("PASS", []))
        self.assertGreater(g6["commands"], 100)


if __name__ == "__main__":
    unittest.main()
