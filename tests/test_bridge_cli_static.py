"""S04-F: rkb bridge CLI contract (JSON stdout, sanitized stderr, exit codes 0/2/3/4) and static scope checks of the bridge."""
import io
import json
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.bridge_helpers import BridgeCase, pbd_write_results, result_row
from scivetra_kb import cli

BRIDGE_SRC = Path(__file__).resolve().parents[1] / "src" / "scivetra_kb" / "bridge"


class BridgeCliTests(BridgeCase):
    def rkb(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue()), err.getvalue()

    def test_cli_export_import_status_close(self):
        """IDs: S04-CLI, B-CLI-1. rkb bridge export/import/status/close emit JSON on stdout; exit 0 success or DUPLICATE_NOOP, 2 validation refusal (HEADER_MISMATCH, open rows), 3 integrity failure (MANIFEST_HASH_MISMATCH), 4 partial reconciliation; stderr carries only the code; no local path is echoed."""
        code, out, _ = self.rkb("bridge", "export", "--data-root", self.root, "--source-dir", self.fulltext, "--kind", "rv-manual-queue",
                                "--workspace-label", "ws", "--out-dir", self.out, "--operator", "op.b")
        self.assertEqual((code, out["row_count"]), (0, 5))
        self.assertNotIn(self.tmp, json.dumps(out))
        batch = out["batch_id"]
        rows = self.manifest()["rows"]
        folder = os.path.join(self.out, out["output_folder"])
        self.put_pdf(rows[0], folder=folder)
        result = self.write_result([result_row(rows[0], "Completed"), result_row(rows[1], "LoginRequired")])
        args = ["bridge", "import", "--data-root", self.root, "--batch-id", batch, "--result-csv", result, "--pdf-folder", folder, "--operator", "op.c"]
        code, out, err = self.rkb(*args, "--manifest", os.path.join(self.out, "bridge_manifest.json"))
        self.assertEqual((code, out["outcome"], out["counts"]["row_outcomes"]), (4, "RECONCILED_PARTIAL", {"NEEDS_HUMAN_ACCESS": 1, "VERIFIED_PDF": 1}))
        self.assertEqual(self.rkb(*args)[0:2][0], 0)
        bad = os.path.join(self.tmp, "bad.csv")
        Path(bad).write_bytes(pbd_write_results([result_row(rows[2], "Failed")]).replace(b"save_as", b"saveas"))
        code, out, err = self.rkb(*args[:7], bad, *args[8:])
        self.assertEqual((code, out["error"], err.strip()), (2, "HEADER_MISMATCH", "rkb: HEADER_MISMATCH"))
        edited = os.path.join(self.tmp, "m.json")
        Path(edited).write_text(Path(self.out, "bridge_manifest.json").read_text(encoding="utf-8").replace("rkb.bridge", "rkb.bridgX"), encoding="utf-8")
        code, out, err = self.rkb(*args, "--manifest", edited)
        self.assertEqual((code, out["error"]), (3, "MANIFEST_HASH_MISMATCH"))
        self.assertNotIn(self.tmp, err + json.dumps(out))
        code, out, _ = self.rkb("bridge", "status", "--data-root", self.root, "--batch-id", batch)
        self.assertEqual((code, out["state"], out["counts"]), (0, "RECONCILED_PARTIAL", {"EXPORTED": 3, "NEEDS_HUMAN_ACCESS": 1, "VERIFIED_PDF": 1}))
        code, out, _ = self.rkb("bridge", "close", "--data-root", self.root, "--batch-id", batch, "--operator", "op.c")
        self.assertEqual((code, out["error"]), (2, "BATCH_HAS_OPEN_ROWS"))
        code, out, _ = self.rkb("bridge", "close", "--data-root", self.root, "--batch-id", batch, "--operator", "op.c", "--reason", "OPERATOR_CLOSE_WITH_OPEN_ROWS")
        self.assertEqual((code, out["state"], len(out["open_rows"])), (0, "CLOSED_WITH_OPEN_ROWS", 4))
        code, out, _ = self.rkb("bridge", "status", "--data-root", self.root, "--batch-id", "ab_unknown")
        self.assertEqual((code, out["error"]), (2, "BATCH_UNKNOWN"))


class BridgeStaticTests(unittest.TestCase):
    def test_bridge_scope_static(self):
        """IDs: S04-STATIC, S04-D01, S04-D09, B-INV-3. The bridge code imports no network, subprocess, browser or HTTP module, never names the Reprovane database, never opens SQLite itself, has no bare/Exception handlers (a BaseException handler only cleans up and re-raises), and writes files only with exclusive-create mode."""
        text = {p.name: p.read_text(encoding="utf-8") for p in BRIDGE_SRC.glob("*.py")}
        self.assertEqual(sorted(text), ["__init__.py", "pbd.py", "policy.py", "service.py", "sources.py"])
        for name, source in text.items():
            with self.subTest(file=name):
                for forbidden in (r"^\s*import (socket|subprocess|webbrowser|http|urllib\.request|requests|ssl)\b",
                                  r"^\s*from (socket|subprocess|webbrowser|http|urllib\.request|requests|ssl)\b", r"litintel", r"sqlite3\.connect",
                                  r"except\s*:", r"except Exception\b", r"os\.system", r"\bopen\([^)]*['\"]w['\"]"):
                    self.assertIsNone(re.search(forbidden, source, re.M), forbidden)
                for match in re.finditer(r"except BaseException:\n((?:[ \t]+.*\n)+?)[ \t]+raise\n", source):
                    self.assertNotIn("return", match.group(1))
                self.assertEqual(source.count("except BaseException:"), len(re.findall(r"except BaseException:\n(?:[ \t]+.*\n)+?[ \t]+raise\n", source)))


if __name__ == "__main__":
    unittest.main()


class BridgeG1AndRightsPolicyTests(BridgeCase):
    def test_g1_fail_closed_and_rights_policy_unchanged(self):
        """IDs: S04-G1-FAIL-CLOSED, S04-D07, S04-RIGHTS. Through the bridge, a '%PDF-'-only file, a malformed xref and an unsupported structure are REJECTED_FILE by the unchanged S03 G1 (PBD's own check is '%PDF-' only); rights-policy/1 with cc-uri-allowlist/2 is unchanged (a jurisdiction-ported CC URI stays unrecognized); bridge imports never create PUBLIC_OA_LICENSED."""
        from datetime import date
        from tests.pdf_fixtures import malformed_xref
        from scivetra_kb.rights import policy as rights_policy
        self.last = self.export()
        rows = self.manifest()["rows"]
        self.put_pdf(rows[0], b"%PDF-1.7\n" + b"not really a pdf body\n" * 20)
        self.put_pdf(rows[1], malformed_xref(self.pdf_for(rows[1])))
        self.put_pdf(rows[2], b"%PDF-1.5\n1 0 obj\n<< /Type /XRef /W [1 2 1] >>\nstream\nxx\nendstream\nendobj\nstartxref\n9\n%%EOF\n")
        code, out = self.run_import(self.write_result([result_row(r, "Completed") for r in rows[:3]]))
        self.assertEqual(out["counts"]["row_outcomes"], {"REJECTED_FILE": 3})
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM rights_assessment WHERE rights_class='PUBLIC_OA_LICENSED'")["n"], 0)
        self.assertEqual((rights_policy.POLICY_VERSION, rights_policy.LICENCE_URI_RULES), ("rights-policy/1", "cc-uri-allowlist/2"))
        self.assertIsNone(rights_policy.licence_family("https://creativecommons.org/licenses/by/3.0/us/"))
        evidence = {"license_evidence": [{"source": "CROSSREF_LICENSE", "license_uri": "https://creativecommons.org/licenses/by/not-real",
                                          "content_version": "vor", "claim_ref": "synthetic"}],
                    "availability_evidence": [{"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion", "claim_ref": "synthetic"}],
                    "access_provenance": {"access_route": "INSTITUTIONAL_LOGIN", "access_barrier_observed": ["LoginRequired"]}}
        self.assertEqual(rights_policy.classify(evidence, version_class="PUBLISHED", as_of=date(2026, 11, 1))["rights_class"], "PUBLIC_OA_UNVERIFIED")
