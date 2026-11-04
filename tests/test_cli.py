import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers import FIXTURES, KBTest, openalex_retraction_claim, rv_item, write_rv_export

TRANSPORT = str(FIXTURES / "cli_transport")


def db_hash(root):
    return hashlib.sha256(Path(root, "db", "kb.sqlite").read_bytes()).hexdigest()


class CliTests(KBTest):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rkb-cli-")
        self.root = os.path.join(self.tmp, "kb")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cli_full_offline_path(self):
        """IDs: CLI-01, S02-CLI-SURFACE. init, doctor, route, fetch, show, refresh with the fixture transport; strict JSON output."""
        code, out = self.run_cli("init", "--data-root", self.root)
        self.assertEqual((code, out["schema_version"]), (0, 11))  # S03 adaptation (docs/S02_TEST_ADAPTATIONS.md #5); S04: docs/S04_TEST_ADAPTATIONS.md #4; S04J: docs/S04J_TEST_ADAPTATIONS.md #4; S05: docs/S05_TEST_ADAPTATIONS.md #4; S06: docs/S06_TEST_ADAPTATIONS.md #4; S07: docs/S07_TEST_ADAPTATIONS.md #4 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #4 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #4 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #4 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #4 (after S10, S09, S08, S07, S06)
        code, out = self.run_cli("doctor", "--data-root", self.root)
        self.assertEqual((code, out["ok"], out["problems"]), (0, True, []))
        code, out = self.run_cli("metadata", "route", "--data-root", self.root, "--doi", "10.99999/smoke.1", "--transport-fixtures", TRANSPORT)
        self.assertEqual((code, out["results"][0]["ra"]), (0, "CROSSREF"))
        code, out = self.run_cli("metadata", "fetch", "--data-root", self.root, "--doi", "10.99999/smoke.1", "--doi", "10.99999/smoke.2",
                                 "--transport-fixtures", TRANSPORT)
        self.assertEqual([r["status"] for r in out["results"]], ["FOUND", "FOUND"])
        code, out = self.run_cli("metadata", "show", "--data-root", self.root, "--doi", "10.99999/smoke.1")
        self.assertEqual(code, 0)
        self.assertEqual(out["integrity"]["families"]["RETRACTION"]["state"], "REPORTED")
        self.assertEqual(out["integrity"]["alert"], "OPEN_ALERT")
        self.assertIn("CROSSREF", out["claims"])
        code, out = self.run_cli("metadata", "refresh", "--data-root", self.root, "--older-than", "0", "--transport-fixtures", TRANSPORT)
        self.assertEqual((code, out["refresh"]["unchanged"], out["refresh"]["changed"]), (0, 2, 0))
        code, out = self.run_cli("metadata", "refresh", "--data-root", self.root, "--integrity-only", "--older-than", "0", "--transport-fixtures", TRANSPORT)
        self.assertEqual(out["refresh"]["checked"], 1)
        code, out = self.run_cli("doctor", "--data-root", self.root)
        self.assertTrue(out["ok"])
        self.assertGreater(out["counts"]["metadata_claim"], 0)

    def test_live_refused_without_side_effects(self):
        """IDs: CLI-02, OPS-LIVE-01. --live fails with LIVE_ACCESS_NOT_AUTHORIZED before the database is opened."""
        self.run_cli("init", "--data-root", self.root)
        before = db_hash(self.root)
        code, out = self.run_cli("metadata", "fetch", "--data-root", self.root, "--doi", "10.99999/smoke.1", "--live", "--allow-public-pool")
        self.assertEqual((code, out["error"]), (2, "LIVE_ACCESS_NOT_AUTHORIZED"))
        code, out = self.run_cli("metadata", "refresh", "--data-root", self.root, "--live")
        self.assertEqual(out["error"], "LIVE_ACCESS_NOT_AUTHORIZED")
        self.assertEqual(db_hash(self.root), before)

    def test_no_transport_no_network(self):
        """IDs: CLI-03. Without a configured transport, network-shaped commands refuse (no implicit network)."""
        self.run_cli("init", "--data-root", self.root)
        code, out = self.run_cli("metadata", "fetch", "--data-root", self.root, "--doi", "10.99999/smoke.1")
        self.assertEqual(out["error"], "NO_TRANSPORT_CONFIGURED")

    def test_read_commands_on_missing_root_create_nothing(self):
        """IDs: CLI-04, N15. show/doctor on an uninitialised root fail and create nothing."""
        missing = os.path.join(self.tmp, "absent")
        for argv in (("metadata", "show", "--data-root", missing, "--doi", "10.99999/smoke.1"), ("doctor", "--data-root", missing)):
            code, out = self.run_cli(*argv)
            self.assertNotEqual(code, 0)
            self.assertFalse(out["ok"])
        self.assertFalse(os.path.exists(missing))

    def test_operator_cli_input_trailing_space(self):
        """IDs: CLI-05, MF-18.28, N28. CLI --doi values are operator input: a trailing space is refused with no request."""
        self.run_cli("init", "--data-root", self.root)
        code, out = self.run_cli("metadata", "fetch", "--data-root", self.root, "--doi", "10.99999/smoke.1 ", "--transport-fixtures", TRANSPORT)
        self.assertEqual(out["results"][0]["status"], "REFUSED")
        self.assertEqual(out["results"][0]["reason"], "AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION")
        code, out = self.run_cli("metadata", "show", "--data-root", self.root, "--doi", "10.99999/smoke.1 ")
        self.assertEqual(out["parse"]["status"], "AMBIGUOUS")

    def test_file_input_with_bom(self):
        """IDs: CLI-06. --file accepts UTF-8 with BOM, one DOI per line."""
        self.run_cli("init", "--data-root", self.root)
        path = os.path.join(self.tmp, "dois.txt")
        Path(path).write_bytes("﻿10.99999/smoke.1\n10.99999/smoke.2\n".encode("utf-8"))
        code, out = self.run_cli("metadata", "route", "--data-root", self.root, "--file", path, "--transport-fixtures", TRANSPORT)
        self.assertEqual([r["ra"] for r in out["results"]], ["CROSSREF", "DATACITE"])

    def test_doctor_reports_cas_tampering_without_repair(self):
        """IDs: CLI-07, CORE-CAS-01. doctor detects a modified CAS object, exits 3, and changes nothing."""
        self.run_cli("init", "--data-root", self.root)
        self.run_cli("metadata", "fetch", "--data-root", self.root, "--doi", "10.99999/smoke.1", "--transport-fixtures", TRANSPORT)
        objects = [p for p in Path(self.root, "cas", "sha256").rglob("*") if p.is_file()]
        objects[0].write_bytes(b"tampered")
        before = db_hash(self.root)
        code, out = self.run_cli("doctor", "--data-root", self.root)
        self.assertEqual(code, 3)
        self.assertTrue(any(p.startswith("CAS_HASH_MISMATCH") for p in out["problems"]))
        self.assertEqual(db_hash(self.root), before)
        self.assertEqual(objects[0].read_bytes(), b"tampered")

    def test_integrity_operator_commands(self):
        """IDs: CLI-08, MF-19f. register-document, counter-evidence and review through the CLI, with an anchored synthetic notice."""
        self.run_cli("init", "--data-root", self.root)
        self.run_cli("metadata", "fetch", "--data-root", self.root, "--doi", "10.99999/smoke.1", "--transport-fixtures", TRANSPORT)
        notice = FIXTURES / "notice_synthetic.txt"
        code, doc = self.run_cli("integrity", "register-document", "--data-root", self.root, "--operator", "reviewer.a",
                                 "--file", str(notice), "--label", "synthetic notice")
        self.assertEqual(code, 0)
        code, show = self.run_cli("metadata", "show", "--data-root", self.root, "--doi", "10.99999/smoke.1")
        event_id = show["integrity_events"][0]["event_id"]
        text = notice.read_text(encoding="utf-8")
        start = text.index("was issued in error")
        anchor = '{"document_sha256":"%s","page":1,"char_start":%d,"char_end":%d}' % (doc["document_sha256"], start, start + 19)
        code, ce = self.run_cli("integrity", "counter-evidence", "--data-root", self.root, "--operator", "reviewer.a", "--event-id", event_id,
                                "--kind", "NOTICE_STATES_ERROR", "--primary-document-ref", "sha256:" + doc["document_sha256"],
                                "--anchor-json", anchor, "--statement", "Notice says the retraction was issued in error")
        self.assertEqual(code, 0, ce)
        code, out = self.run_cli("integrity", "counter-evidence", "--data-root", self.root, "--operator", "reviewer.a", "--event-id", event_id,
                                 "--kind", "NOTICE_STATES_ERROR", "--primary-document-ref", "just believe me",
                                 "--anchor-json", anchor, "--statement", "x")
        self.assertEqual(out["error"], "PRIMARY_DOCUMENT_REQUIRED")
        code, review = self.run_cli("integrity", "review", "--data-root", self.root, "--operator", "reviewer.b", "--subject-doi", "10.99999/smoke.1",
                                    "--event-id", event_id, "--trigger", "OPERATOR_REQUEST", "--decision", "REPORT_NOT_APPLICABLE_WITH_EVIDENCE",
                                    "--reason", "Anchored notice", "--counter-evidence-id", ce["counter_evidence_id"])
        self.assertEqual(code, 0, review)
        code, show = self.run_cli("metadata", "show", "--data-root", self.root, "--doi", "10.99999/smoke.1")
        self.assertEqual(show["integrity"]["alert"], "CLOSED_NOT_APPLICABLE")
        self.assertEqual(show["integrity"]["families"]["RETRACTION"]["state"], "EXPLICIT_CONTRADICTION")

    def test_import_reprovane_cli(self):
        """IDs: CLI-09, RV-IMPORT-01. import-reprovane reads only the portable export."""
        self.run_cli("init", "--data-root", self.root)
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_1", dois=[("10.99999/smoke.1", "10.99999/smoke.1")],
                                                                        integrity=openalex_retraction_claim())])
        code, out = self.run_cli("metadata", "import-reprovane", "--data-root", self.root, "--export-dir", export, "--workspace-label", "ws1")
        self.assertEqual((code, out["items"], out["integrity_events"]), (0, 1, 1))


if __name__ == "__main__":
    unittest.main()
