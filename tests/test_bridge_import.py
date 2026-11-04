"""S04-C/D: PBD result import, status mapping, reconciliation, idempotency, crash resume, files, late files, rights, closure."""
import json
import os
import shutil
import sqlite3
import unittest
from pathlib import Path

from tests.bridge_helpers import BridgeCase, pbd_write_results, result_row
from tests.pdf_fixtures import html_challenge, truncated
from scivetra_kb.assets.service import AssetService
from scivetra_kb.bridge import pbd
from scivetra_kb.bridge.service import BridgeService, sanitize_text
from scivetra_kb.core.db import DB_RELATIVE
from scivetra_kb.core.errors import IntegrityError, KBError, ValidationError
from scivetra_kb.rights import policy as rights_policy

GENUINE_WORK_ID = "https://openalex.org/W3"   # a real-looking OpenAlex Work ID where PBD should echo the bridge token
BRIDGE_TABLES = ("bridge_attempt", "bridge_file_event", "bridge_row_event", "bridge_review_task", "bridge_result_import", "bridge_batch_event",
                 "pdf_asset", "paper_asset_link", "import_event", "rights_assessment")


class ImportCase(BridgeCase):
    def setUp(self):
        super().setUp()
        self.last = self.export()
        self.rows = self.manifest()["rows"]

    def table_counts(self):
        with self.reader() as kb:
            return {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in BRIDGE_TABLES}

    def assert_refused(self, rows_or_bytes, code, exc=ValidationError, **kw):
        path = os.path.join(self.tmp, "refused.csv")
        Path(path).write_bytes(rows_or_bytes if isinstance(rows_or_bytes, bytes) else pbd_write_results(rows_or_bytes))
        before = self.table_counts()
        with self.assertRaises(exc) as ctx:
            self.run_import(path, **kw)
        self.assertEqual(ctx.exception.code, code)
        after = self.table_counts()
        self.assertEqual({k: v for k, v in after.items() if k != "bridge_result_import"}, {k: v for k, v in before.items() if k != "bridge_result_import"})
        return ctx.exception


class StatusMappingTests(ImportCase):
    def test_exact_status_mapping(self):
        """IDs: S04-IMP-STATUS-MAP, S04-D03, S04-D04, F06. Every PBD V5.2.11 JobStatus maps exactly as 06 section 6.2; Failed carries PBD_REPORTED_PAGE_NOT_FOUND only for PBD's own permanent page-not-found text, else UNSPECIFIED (never 'paper not found'); an unknown status is UNKNOWN_STATUS, quarantined with a review task."""
        self.assertEqual(set(pbd.STATUS_MAP), set(pbd.PBD_JOB_STATUSES))
        expected = {"Completed": "RECEIVED_PENDING_VERIFY", "Failed": "FAILED", "Deferred": "DEFERRED", "LoginRequired": "NEEDS_HUMAN_ACCESS",
                    "InstitutionalAccessRequired": "NEEDS_HUMAN_ACCESS", "BotCheck": "NEEDS_HUMAN_ACTION", "WaitingForUser": "NEEDS_HUMAN_ACTION",
                    "ManualAttention": "NEEDS_HUMAN_ACTION", "Skipped": "SKIPPED", "NoUrl": "NO_URL", "Pending": "IN_PROGRESS_AT_EXPORT",
                    "Navigating": "IN_PROGRESS_AT_EXPORT", "LookingForPdf": "IN_PROGRESS_AT_EXPORT", "Downloading": "IN_PROGRESS_AT_EXPORT"}
        self.assertEqual({k: v[0] for k, v in pbd.STATUS_MAP.items()}, expected)
        self.assertEqual({k for k, v in pbd.STATUS_MAP.items() if v[1]}, {"Failed", "Skipped", "NoUrl"})
        self.assertEqual(pbd.map_status("completed"), ("UNKNOWN_STATUS", None))
        r = self.rows
        code, out = self.run_import(self.write_result([
            result_row(r[0], "Failed", deferred_reason="Permanent page-not-found response"), result_row(r[1], "Failed", deferred_reason="Timeout"),
            result_row(r[2], "Exploded"), result_row(r[3], "BotCheck"), result_row(r[4], "Navigating")]))
        self.assertEqual(code, 4)
        with self.reader() as kb:
            attempts = {a["bridge_row_id"]: a for a in kb.all("SELECT * FROM bridge_attempt")}
            tasks = sorted(t["trigger"] for t in kb.all("SELECT trigger FROM bridge_review_task"))
        self.assertEqual((attempts[r[0]["bridge_row_id"]]["sub_reason"], attempts[r[1]["bridge_row_id"]]["sub_reason"]),
                         ("PBD_REPORTED_PAGE_NOT_FOUND", "UNSPECIFIED"))
        self.assertEqual(attempts[r[2]["bridge_row_id"]]["bridge_status"], "UNKNOWN_STATUS")
        self.assertEqual(tasks, ["NEEDS_HUMAN_ACTION", "UNKNOWN_STATUS"])
        status = {x["bridge_row_id"]: x["status"] for x in self.status()["rows"]}
        self.assertEqual([status[x["bridge_row_id"]] for x in r], ["FAILED", "FAILED", "UNKNOWN_STATUS", "NEEDS_HUMAN_ACTION", "IN_PROGRESS_AT_EXPORT"])

    def test_completed_is_only_verification_and_identity_stays_unverified(self):
        """IDs: S04-IMP-COMPLETED, S04-D06, B-INV-5. Completed + a valid synthetic PDF whose title page matches the paper is VERIFIED_PDF (bytes/structure) through S03 G0-G3, but the link stays UNVERIFIED (human gate closed); LoginRequired is an access barrier recorded as provenance, never a rights class; rights stay UNKNOWN and A7/A8 DENY."""
        r = self.rows
        login = r[1]
        self.run_import(self.write_result([result_row(login, "LoginRequired")], name="first.csv"))
        for row in (r[0], login):
            self.put_pdf(row, self.pdf_for(row, title="Synthetic Work " + row["export_doi"][-1]))
        code, out = self.run_import(self.write_result([result_row(r[0], "Completed"), result_row(login, "Completed")], name="second.csv"))
        self.assertEqual(out["counts"]["row_outcomes"], {"VERIFIED_PDF": 2})
        with self.reader() as kb:
            links = kb.all("SELECT l.identity_state s, l.reason_codes_json c, e.asset_sha256 a FROM bridge_file_event e JOIN paper_asset_link l USING (link_id)")
            self.assertEqual({x["s"] for x in links}, {"UNVERIFIED"})
            # The title-page DOI and title match, so S03 G3 alone would have auto-matched: the bridge keeps the human gate closed.
            self.assertTrue(all(json.loads(x["c"]) == ["HUMAN_CONFIRMATION_REQUIRED"] for x in links))
            classes = {x["rights_class"] for x in kb.all("SELECT rights_class FROM rights_assessment")}
            svc = AssetService(kb)
            dv = {d["paper_id"]: d["document_version_id"] for d in kb.all("SELECT * FROM document_version WHERE version_class='UNKNOWN'")}
            gates = [svc.gate(asset_sha256=x["a"], document_version_id=dv[p["paper_id"]], action=a)["decision"]
                     for x, p in zip(sorted(links, key=lambda y: y["a"]), sorted([r[0], login], key=lambda y: y["paper_id"])) for a in ("A7", "A8")]
            barrier = json.loads(kb.one("SELECT access_provenance_json j FROM rights_assessment WHERE access_provenance_json LIKE '%LoginRequired%'")["j"])
        self.assertEqual(classes, {"UNKNOWN"})
        self.assertNotIn("PUBLIC_OA_LICENSED", classes)
        self.assertEqual(set(gates), {"DENY"})
        self.assertEqual(barrier["access_barrier_observed"], ["LoginRequired"])
        self.assertNotIn("PUBLIC_OA_LICENSED", rights_policy.classify({"access_provenance": {"access_route": "INSTITUTIONAL_LOGIN",
                                                                                              "access_barrier_observed": ["LoginRequired"]}},
                                                                       version_class="UNKNOWN", as_of=__import__("datetime").date(2026, 11, 1))["rights_class"])


class HeaderAndMatchingTests(ImportCase):
    def test_header_encoding_and_line_endings(self):
        """IDs: S04-IMP-HEADER, S04-D10. A changed, reordered, semicolon or extra-column header is HEADER_MISMATCH; non-UTF-8 and NUL are ENCODING_REJECTED; broken quoting is CSV_MALFORMED; each refusal is journaled with no attempt, asset or link. The BOM is optional; LF-only files and quoted newlines inside fields parse."""
        good = pbd_write_results([result_row(self.rows[0], "Failed")])
        bad_headers = [good.replace(b"openalex_id,doi", b"doi,openalex_id"), good.replace(b",save_as", b",save_as,extra"),
                       good.replace(pbd.OUTPUT_HEADER.encode(), pbd.OUTPUT_HEADER.replace(",", ";").encode()),
                       good.replace(b"id,openalex_id", b"Id,openalex_id")]
        for data in bad_headers:
            self.assert_refused(data, "HEADER_MISMATCH")
        self.assert_refused(good.replace(b"Synthetic", b"Synth\xe9tic"), "ENCODING_REJECTED")
        self.assert_refused(good.replace(b"Synthetic", b"Synth\x00tic"), "ENCODING_REJECTED")
        self.assert_refused(good.replace(b'"Synthetic"', b'"Synth"etic"'), "CSV_MALFORMED")
        with self.reader() as kb:
            refused = [r["refusal_code"] for r in kb.all("SELECT refusal_code FROM bridge_result_import ORDER BY rowid")]
        self.assertEqual(len(refused), 7)
        for name, data in (("nobom.csv", pbd_write_results([result_row(self.rows[0], "Failed", title='Multi\r\nline, "quoted"')], bom=False)),
                           ("lf.csv", pbd_write_results([result_row(self.rows[1], "Skipped")], newline="\n"))):
            Path(self.tmp, name).write_bytes(data)
            code, out = self.run_import(os.path.join(self.tmp, name))
            self.assertEqual(out["data_rows"], 1)

    def test_row_matching_anomalies(self):
        """IDs: S04-IMP-MATCHING, B-INV-4. Rows are matched by save_as, never by row number: reordered rows get ROW_ORDINAL_MISMATCH (warning only); a token that differs from bridge_row_id, and a duplicated save_as, are QUARANTINED with review tasks; unknown and case-variant save_as are ROW_UNEXPECTED / CASE_VARIANT_NAME; absent rows are ROW_MISSING; malformed rows are ROW_MALFORMED; exit code 4."""
        r = self.rows
        rows = [result_row(r[1], "Failed", ordinal=1), result_row(r[0], "Failed", ordinal=2), result_row(r[2], "Failed", record_token=GENUINE_WORK_ID),
                result_row(r[3], "Skipped"), result_row(r[3], "Skipped"), result_row(r[4], "Failed", save_as="b" + "0" * 23 + ".pdf"),
                result_row(r[4], "Failed", save_as=r[4]["save_as"].upper()), result_row(r[1], "Skipped", save_as=r[1]["save_as"] + ":ads")]
        path = self.write_result(rows)
        data = Path(path).read_bytes().replace(b"\r\n", b"\r\n1,\"x\"\r\n", 1) if False else Path(path).read_bytes() + b'9,"short"\r\n'
        Path(path).write_bytes(data)
        code, out = self.run_import(path)
        self.assertEqual(code, 4)
        anomalies = sorted(a["code"] for a in out["anomalies"])
        self.assertEqual(anomalies, ["CASE_VARIANT_NAME", "ROW_MALFORMED", "ROW_MISSING", "ROW_UNEXPECTED", "ROW_UNEXPECTED"])
        with self.reader() as kb:
            attempts = {(a["bridge_row_id"], a["output_row_number"]): a for a in kb.all("SELECT * FROM bridge_attempt")}
            tasks = sorted(t["trigger"] for t in kb.all("SELECT trigger FROM bridge_review_task"))
        self.assertIn("ROW_ORDINAL_MISMATCH", json.loads(attempts[(r[1]["bridge_row_id"], 1)]["codes_json"]))
        self.assertEqual(attempts[(r[1]["bridge_row_id"], 1)]["bridge_status"], "FAILED")
        self.assertEqual(attempts[(r[2]["bridge_row_id"], 3)]["bridge_status"], "QUARANTINED")
        self.assertEqual({attempts[(r[3]["bridge_row_id"], n)]["bridge_status"] for n in (4, 5)}, {"QUARANTINED"})
        self.assertEqual(tasks, ["ROW_DUPLICATE", "ROW_DUPLICATE", "ROW_IDENTITY_MISMATCH"])
        self.assertNotIn(r[4]["bridge_row_id"], {k[0] for k in attempts})
        self.assertEqual({x["bridge_row_id"]: x["status"] for x in self.status()["rows"]}[r[4]["bridge_row_id"]], "EXPORTED")

    def test_wrong_batch_refused(self):
        """IDs: S04-IMP-WRONG-BATCH. A result file of another batch (tokens/save_as unknown here) is WRONG_BATCH: refused, nothing recorded but the refusal."""
        other = self.export(out_dir=os.path.join(self.tmp, "batch2"))
        other_rows = self.manifest(os.path.join(self.tmp, "batch2"))["rows"]
        self.assert_refused([result_row(x, "Failed") for x in other_rows], "WRONG_BATCH")
        self.assertNotEqual(other["batch_id"], self.last["batch_id"])


class FileTests(ImportCase):
    def test_completed_file_problems(self):
        """IDs: S04-IMP-FILES, B-FAIL-6, B-FAIL-8, B-FAIL-11. Completed with a missing file, an HTML challenge, a truncated PDF, an empty file or a case-variant file name is REJECTED_FILE with the S03 G0/G1 code; no asset, CAS object or link is created; the PBD file_path (pointing elsewhere) is never opened."""
        r = self.rows
        folder = Path(self.folder())
        outside = Path(self.tmp, "elsewhere")
        outside.mkdir()
        (outside / r[0]["save_as"]).write_bytes(self.pdf_for(r[0]))
        (folder / r[1]["save_as"]).write_bytes(html_challenge())
        (folder / r[2]["save_as"]).write_bytes(truncated(self.pdf_for(r[2])))
        (folder / r[3]["save_as"]).write_bytes(b"")
        (folder / r[4]["save_as"].upper()).write_bytes(self.pdf_for(r[4]))
        rows = [result_row(r[0], "Completed", file_path=str(outside / r[0]["save_as"]))] + [result_row(x, "Completed") for x in r[1:]]
        code, out = self.run_import(self.write_result(rows))
        self.assertEqual(out["counts"]["row_outcomes"], {"REJECTED_FILE": 5})
        with self.reader() as kb:
            codes = {e["bridge_row_id"]: json.loads(e["reason_codes_json"]) for e in kb.all("SELECT * FROM bridge_file_event")}
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM paper_asset_link")["n"], 0)
        self.assertEqual([codes[x["bridge_row_id"]] for x in r], [["FILE_MISSING"], ["HTML_OR_CHALLENGE"], ["TRUNCATED_NO_EOF"], ["EMPTY"],
                                                                 ["CASE_VARIANT_NAME"]])
        self.assertIn("CASE_VARIANT_NAME", [a["code"] for a in out["anomalies"]])
        self.assertEqual([p for p in Path(self.root, "cas", "sha256").rglob("*") if p.is_file() and p.read_bytes().startswith(b"%PDF")], [])
        self.assertEqual((outside / r[0]["save_as"]).read_bytes(), self.pdf_for(r[0]))

    def test_late_files_need_new_result_or_operator_flag(self):
        """IDs: S04-IMP-LATE, B-FAIL-3. A Deferred row whose file is already present is LATE_FILE_PRESENT (not imported, review task); the same result file with --accept-late-files imports it through G0-G3 (ACCEPTED_LATE_FILE_BY_OPERATOR); a later result showing Completed also verifies it, without a second asset."""
        row = self.rows[0]
        self.put_pdf(row)
        late = self.write_result([result_row(row, "Deferred", deferred_reason="Native download idle; continuing in background",
                                             file_path="C:\\pbd\\" + row["save_as"])], name="late.csv")
        code, out = self.run_import(late)
        self.assertEqual(out["counts"]["row_outcomes"], {"LATE_FILE_PRESENT": 1})
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
            self.assertEqual([t["trigger"] for t in kb.all("SELECT trigger FROM bridge_review_task")], ["LATE_FILE_PRESENT"])
        late2 = self.write_result([result_row(row, "Deferred", attempts=2)], name="late2.csv")
        code, out = self.run_import(late2, accept_late_files=True)
        self.assertEqual(out["counts"]["row_outcomes"], {"VERIFIED_PDF": 1})
        with self.reader() as kb:
            event = kb.one("SELECT * FROM bridge_file_event WHERE outcome='VERIFIED_PDF'")
        self.assertIn("ACCEPTED_LATE_FILE_BY_OPERATOR", json.loads(event["reason_codes_json"]))
        code, out = self.run_import(self.write_result([result_row(row, "Completed")], name="done.csv"))
        self.assertEqual(out["counts"]["row_outcomes"], {"DUPLICATE_NOOP": 1})
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 1)


class IdempotencyTests(ImportCase):
    def test_repeat_update_and_changed_bytes(self):
        """IDs: S04-IMP-IDEMPOTENT, S04-D05, B-FAIL-7. An unchanged result file is a whole-file DUPLICATE_NOOP (no new rows anywhere); a later corrected result adds attempts only; the same bytes again are DUPLICATE_NOOP; different bytes for the same row keep BOTH CAS objects, open NEW_BYTES_SAME_ROW review and auto-accept nothing."""
        r = self.rows
        first = self.write_result([result_row(r[0], "Completed"), result_row(r[1], "Deferred", deferred_reason="Paywall")], name="r1.csv")
        self.put_pdf(r[0])
        code, out = self.run_import(first)
        snapshot = self.table_counts()
        code, again = self.run_import(first)
        self.assertEqual((code, again["outcome"]), (0, "DUPLICATE_NOOP"))
        self.assertEqual(self.table_counts(), snapshot)
        self.put_pdf(r[1])
        code, upd = self.run_import(self.write_result([result_row(r[0], "Completed"), result_row(r[1], "Completed")], name="r2.csv"))
        self.assertEqual(upd["counts"]["row_outcomes"], {"DUPLICATE_NOOP": 1, "VERIFIED_PDF": 1})
        new_bytes = self.pdf_for(r[0], title="Synthetic Work changed")
        os.remove(os.path.join(self.folder(), r[0]["save_as"]))
        self.put_pdf(r[0], new_bytes)
        code, chg = self.run_import(self.write_result([result_row(r[0], "Completed", attempts=3)], name="r3.csv"))
        self.assertEqual(chg["counts"]["row_outcomes"], {"NEW_BYTES_SAME_ROW": 1})
        with self.reader() as kb:
            assets = sorted({e["asset_sha256"] for e in kb.all("SELECT asset_sha256 FROM bridge_file_event WHERE bridge_row_id=? AND asset_sha256 IS NOT NULL",
                                                               (r[0]["bridge_row_id"],))})
            self.assertEqual(len(assets), 2)
            for digest in assets:
                kb.cas.verify_object(digest)
            states = {x["s"] for x in kb.all("SELECT effective_state s FROM paper_asset_link_effective WHERE asset_sha256 IN (?,?)", assets)}
            tasks = [t["trigger"] for t in kb.all("SELECT trigger FROM bridge_review_task WHERE bridge_row_id=?", (r[0]["bridge_row_id"],))]
            attempts = kb.one("SELECT count(*) n FROM bridge_attempt")["n"]
        self.assertEqual(states, {"UNVERIFIED"})
        self.assertEqual(tasks, ["NEW_BYTES_SAME_ROW"])
        self.assertEqual(attempts, 5)

    def test_crash_during_reconciliation_resumes(self):
        """IDs: S04-IMP-CRASH. An import interrupted after its first row leaves only keyed rows; re-running the same command completes it; the resulting facts (row statuses, assets, file events, attempts) equal an uninterrupted run on a copy of the KB."""
        r = self.rows
        for row in r[:3]:
            self.put_pdf(row)
        rows = [result_row(x, "Completed") for x in r[:3]] + [result_row(r[3], "LoginRequired"), result_row(r[4], "NoUrl")]
        path = self.write_result(rows)
        copy_root = os.path.join(self.tmp, "kb-copy")
        shutil.copytree(self.root, copy_root)

        def boom(index):
            if index == 0:
                raise KeyboardInterrupt("simulated crash")
        with self.assertRaises(KeyboardInterrupt):
            self.run_import(path, faults={"after_row": boom})
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM bridge_result_import")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM bridge_attempt")["n"], 1)
        code, out = self.run_import(path)
        self.assertEqual(out["outcome"], "RECONCILED_PARTIAL")

        def facts(root):
            con = sqlite3.connect(Path(root, DB_RELATIVE).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                status = {}
                for row_id, s in con.execute("SELECT bridge_row_id, new_status FROM bridge_row_event ORDER BY rowid"):
                    status[row_id] = s
                return {"status": status,
                        "files": sorted(con.execute("SELECT bridge_row_id, outcome, asset_sha256 FROM bridge_file_event").fetchall()),
                        "attempts": sorted(con.execute("SELECT bridge_row_id, output_row_number, bridge_status FROM bridge_attempt").fetchall()),
                        "assets": sorted(con.execute("SELECT asset_sha256 FROM pdf_asset").fetchall())}
            finally:
                con.close()
        from scivetra_kb.core.db import open_writer
        with open_writer(copy_root, clock=self.clock, ids=self.ids) as kb:
            BridgeService(kb).import_results(batch_id=self.last["batch_id"], result_csv=path, pdf_folder=self.folder(), operator="op.c",
                                             allow_different_folder=True)
        self.assertEqual(facts(self.root), facts(copy_root))


class ManifestFolderCloseTests(ImportCase):
    def test_manifest_and_folder_checks(self):
        """IDs: S04-IMP-MANIFEST, S04-IMP-FOLDER, B-FAIL-10. An edited bridge_manifest.json is MANIFEST_HASH_MISMATCH (exit 3, integrity comparison only); the genuine one passes; a different PDF folder is DIFFERENT_OUTPUT_FOLDER unless --allow-different-folder, which records the code on every attempt."""
        row = self.rows[0]
        good = os.path.join(self.out, "bridge_manifest.json")
        edited = os.path.join(self.tmp, "edited_manifest.json")
        Path(edited).write_bytes(Path(good).read_bytes().replace(b'"flags":[]', b'"flags":["X"]', 1))
        res = [result_row(row, "Failed")]
        self.assert_refused(res, "MANIFEST_HASH_MISMATCH", IntegrityError, manifest_file=edited)
        other = os.path.join(self.tmp, "other-folder")
        os.makedirs(other)
        self.assert_refused(res, "DIFFERENT_OUTPUT_FOLDER", folder=other, manifest_file=good)
        code, out = self.run_import(self.write_result(res), folder=other, manifest_file=good, allow_different_folder=True)
        self.assertTrue(out["different_output_folder"])
        with self.reader() as kb:
            self.assertIn("DIFFERENT_OUTPUT_FOLDER_ACCEPTED_BY_OPERATOR", json.loads(kb.one("SELECT codes_json c FROM bridge_attempt")["c"]))

    def test_close_rules(self):
        """IDs: S04-CLOSE, B-CLOSE-1. A batch with open rows cannot close without --reason OPERATOR_CLOSE_WITH_OPEN_ROWS; then it is CLOSED_WITH_OPEN_ROWS listing every open row (still visible in status); later imports are BATCH_CLOSED; a batch whose rows are all final closes as CLOSED."""
        r = self.rows
        self.run_import(self.write_result([result_row(r[0], "Failed"), result_row(r[1], "LoginRequired")]))
        with self.writer() as kb:
            with self.assertRaises(ValidationError) as ctx:
                BridgeService(kb).close(batch_id=self.last["batch_id"], operator="op.c")
            self.assertEqual(ctx.exception.code, "BATCH_HAS_OPEN_ROWS")
            out = BridgeService(kb).close(batch_id=self.last["batch_id"], operator="op.c", reason="OPERATOR_CLOSE_WITH_OPEN_ROWS")
        self.assertEqual((out["state"], len(out["open_rows"])), ("CLOSED_WITH_OPEN_ROWS", 4))
        status = self.status()
        self.assertEqual((status["state"], len(status["open_rows"]), len(status["rows"])), ("CLOSED_WITH_OPEN_ROWS", 4, 5))
        self.assert_refused([result_row(r[2], "Failed")], "BATCH_CLOSED")
        second = self.export(out_dir=os.path.join(self.tmp, "b2"))
        rows2 = self.manifest(os.path.join(self.tmp, "b2"))["rows"]
        with self.writer() as kb:
            BridgeService(kb).import_results(batch_id=second["batch_id"], result_csv=self.write_result([result_row(x, "NoUrl") for x in rows2], name="n.csv"),
                                             pdf_folder=os.path.join(self.tmp, "b2", second["output_folder"]), operator="op.c")
            self.assertEqual(BridgeService(kb).close(batch_id=second["batch_id"], operator="op.c")["state"], "CLOSED")


class ProvenanceTests(ImportCase):
    def test_no_secrets_paths_or_result_bytes_persisted(self):
        """IDs: S04-PROVENANCE, B-SEC-1. Raw status and bounded, sanitized reasons are kept; local paths, query strings, cookies/tokens from PBD error/url fields and the result CSV bytes are never stored in the KB DB, CAS or portable files."""
        row = self.rows[0]
        planted = "S3CR3TVALUE" + "0123456789abcdefABCDEF0123456789"
        res = [result_row(row, "Failed", error=f"Cookie: session={planted} at C:\\Users\\operator\\AppData\\cookies.txt",
                          selected_url=f"https://papers.example.org/a.pdf?token={planted}", pdf_url=f"https://cdn.example.org/x?sig={planted}",
                          attention_reason="see /home/operator/private", file_path="C:\\Users\\operator\\Downloads\\x.pdf")]
        path = self.write_result(res)
        self.run_import(path)
        with self.reader() as kb:
            observed = json.loads(kb.one("SELECT observed_json o FROM bridge_attempt")["o"])
            self.assertEqual(kb.one("SELECT count(*) n FROM bridge_result_import")["n"], 1)
        self.assertEqual(observed["selected_url_host"], "https://papers.example.org")
        self.assertTrue(observed["file_path_reported"])
        dump = b"".join(p.read_bytes() for p in Path(self.root).rglob("*") if p.is_file())
        for needle in (planted.encode(), b"C:\\Users\\operator", b"/home/operator", b"cookies.txt"):
            self.assertNotIn(needle, dump)
        for needle in (b"C:\\Users", self.tmp.encode()):
            self.assertNotIn(needle, b"".join(p.read_bytes() for p in Path(self.out).iterdir() if p.is_file()))
        self.assertEqual(sanitize_text("x https://h.example.org/p?q=1 C:\\a\\b"), "x https://h.example.org <path>")


if __name__ == "__main__":
    unittest.main()
