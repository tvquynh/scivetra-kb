"""S03-E: operator CLI surface; read-only commands never create, initialize or upgrade anything."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers import KBTest
from tests.pdf_fixtures import synthetic_pdf, title_page
from tests.s03_helpers import copy_native, make_schema1_kb, tree_hashes
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider


class S03CliTests(KBTest):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rkb-s03cli-")
        self.root = os.path.join(self.tmp, "kb")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *argv):
        code, out = self.run_cli(*argv)
        return code, out

    def test_cli_full_s03_path(self):
        """IDs: S03-CLI-01, S03-CLI-SURFACE. init, library import/review-queue/search, asset add-version/register/review-link, rights assess/gate, doctor --full, backup, verify-backup, restore, reconcile, upgrade (not required) with JSON output."""
        self.assertEqual(self.cli("init", "--data-root", self.root)[1]["schema_version"], 11)  # S04 adaptation (docs/S04_TEST_ADAPTATIONS.md #5); S04J #5; S05: docs/S05_TEST_ADAPTATIONS.md #5; S06: docs/S06_TEST_ADAPTATIONS.md #5; S07: docs/S07_TEST_ADAPTATIONS.md #5 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #5 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #5 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #5 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #5 (after S10, S09, S08, S07, S06)
        export = copy_native(self.tmp, "s03-r1")
        code, out = self.cli("library", "import-reprovane", "--data-root", self.root, "--export-dir", export, "--workspace-label", "ws",
                             "--operator", "op.a")
        self.assertEqual((code, out["references_created"]), (0, 3))
        code, out = self.cli("library", "search", "--data-root", self.root, "--doi", "10.99999/s03.alpha")
        paper = out["papers"][0]["paper_id"]
        self.assertEqual(code, 0)
        code, out = self.cli("library", "review-queue", "--data-root", self.root)
        self.assertEqual((code, out["counts"]["identity_conflicts"]), (0, 0))
        evidence = Path(self.tmp, "version.json")
        evidence.write_text(json.dumps([{"source": "CROSSREF_LICENSE_CONTENT_VERSION", "claim_ref": "synthetic:vor", "value": "vor"}]))
        code, out = self.cli("asset", "add-version", "--data-root", self.root, "--paper-id", paper, "--version-class", "PUBLISHED",
                             "--evidence-file", str(evidence), "--operator", "op.a")
        dv = out["document_version_id"]
        fixtures = Path(self.tmp, "fixtures")
        fixtures.mkdir()
        Path(fixtures, "alpha.pdf").write_bytes(synthetic_pdf([title_page(title="Synthetic alpha study", authors=["First Synthetic"],
                                                                          doi="10.99999/s03.alpha")]))
        code, out = self.cli("asset", "register", "--data-root", self.root, "--fixture-root", str(fixtures), "--file", "alpha.pdf",
                             "--document-version-id", dv, "--operator", "op.a", "--access-route", "INSTITUTIONAL_LOGIN", "--access-barrier", "LoginRequired")
        self.assertEqual((code, out["outcome"], out["identity_state"], out["rights"]["rights_class"]), (0, "REGISTERED", "AUTO_EVIDENCE_MATCH", "UNKNOWN"))
        asset, link = out["asset_sha256"], out["link_id"]
        code, out = self.cli("asset", "review-link", "--data-root", self.root, "--link-id", link, "--decision", "CONFIRM", "--operator", "op.b",
                             "--reason", "title page checked")
        self.assertEqual(out["new_state"], "HUMAN_CONFIRMED")
        rights = Path(self.tmp, "rights.json")
        rights.write_text(json.dumps({"license_evidence": [{"source": "CROSSREF_LICENSE", "license_uri": "https://creativecommons.org/licenses/by/4.0/",
                                                            "content_version": "vor", "claim_ref": "synthetic:license"}],
                                      "availability_evidence": [{"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion",
                                                                 "claim_ref": "synthetic:oa"}]}))
        code, out = self.cli("rights", "assess", "--data-root", self.root, "--asset", asset, "--document-version-id", dv, "--evidence-file", str(rights),
                             "--operator", "op.r")
        self.assertEqual(out["rights_class"], "PUBLIC_OA_LICENSED")
        for action, decision in (("A2", "ALLOW"), ("A7", "DENY"), ("A8", "DENY"), ("A9", "DENY")):
            code, out = self.cli("rights", "gate", "--data-root", self.root, "--asset", asset, "--document-version-id", dv, "--action", action)
            self.assertEqual(out["decision"], decision, action)
        code, out = self.cli("doctor", "--data-root", self.root, "--full")
        self.assertEqual((code, out["problems"]), (0, []))
        code, out = self.cli("backup", "--data-root", self.root, "--target", os.path.join(self.tmp, "bk"))
        backup = out["backup_dir"]
        self.assertEqual(self.cli("verify-backup", "--backup", backup)[1]["valid"], True)
        code, out = self.cli("restore", "--backup", backup, "--data-root", os.path.join(self.tmp, "restored"))
        self.assertTrue(all(out["comparison"].values()))
        self.assertEqual(self.cli("reconcile", "--data-root", self.root, "--workspace-label", "ws", "--export-dir", export)[1]["status"], "CONFIRMED")
        self.assertEqual(self.cli("upgrade", "--data-root", self.root, "--backup-target", os.path.join(self.tmp, "bk"))[1]["code"], "UPGRADE_NOT_REQUIRED")
        code, out = self.cli("library", "workspace-status", "--data-root", self.root, "--workspace-label", "ws", "--availability", "SOURCE_UNAVAILABLE",
                             "--operator", "op.a", "--reason", "synthetic workspace removed")
        self.assertEqual((code, out["references_kept"]), (0, 3))

    def test_read_only_commands_never_create_or_upgrade(self):
        """IDs: S03-CLI-READONLY, N15, CORE-INIT-02. review-queue, search, reconcile, rights gate and doctor on a missing root create nothing; on a schema-1 KB they refuse (KB_UPGRADE_REQUIRED) and leave the bytes unchanged."""
        missing = os.path.join(self.tmp, "absent")
        export = copy_native(self.tmp)
        commands = [("library", "review-queue", "--data-root"), ("library", "search", "--doi", "10.99999/x", "--data-root"),
                    ("reconcile", "--workspace-label", "ws", "--export-dir", export, "--data-root"),
                    ("rights", "gate", "--asset", "a" * 64, "--document-version-id", "dv_x", "--action", "A1", "--data-root"), ("doctor", "--data-root")]
        for argv in commands:
            with self.subTest(argv=argv[:2]):
                code, out = self.cli(*argv, missing)
                self.assertNotEqual(code, 0)
                self.assertFalse(os.path.exists(missing))
        v1 = os.path.join(self.tmp, "v1")
        clock = FixedClock()
        make_schema1_kb(v1, clock=clock, ids=SequentialIdProvider(clock))
        before = tree_hashes(v1)
        for argv in commands[:4]:
            with self.subTest(argv=argv[:2], schema=1):
                code, out = self.cli(*argv, v1)
                self.assertEqual((code, out["error"]), (3, "KB_UPGRADE_REQUIRED"))
        code, out = self.cli("library", "import-reprovane", "--data-root", v1, "--export-dir", export, "--workspace-label", "ws", "--operator", "op.a")
        self.assertEqual(out["error"], "KB_UPGRADE_REQUIRED")
        after = tree_hashes(v1)
        after.pop(".rkb-writer.lock", None)     # a writer command takes the OS lock first (S02 behaviour); DB/CAS bytes are unchanged
        self.assertEqual(after, before)

    def test_cli_refusals_are_structured(self):
        """IDs: S03-CLI-02. Operator labels that look like emails, an unknown decision and a backup without target give structured JSON errors."""
        self.cli("init", "--data-root", self.root)
        code, out = self.cli("library", "import-reprovane", "--data-root", self.root, "--export-dir", copy_native(self.tmp), "--workspace-label", "ws",
                             "--operator", "someone@example.invalid")
        self.assertEqual((code, out["error"]), (2, "OPERATOR_LABEL_INVALID"))
        code, out = self.cli("backup", "--data-root", self.root, "--target", "")
        self.assertEqual(out["error"], "BACKUP_TARGET_REQUIRED")
        code, out = self.cli("library", "decide-link", "--data-root", self.root, "--candidate-id", "lc_x", "--decision", "MERGE_NOW",
                             "--operator", "op.a", "--reason", "x")
        self.assertEqual(out["error"], "LINK_DECISION_INVALID")


if __name__ == "__main__":
    unittest.main()
