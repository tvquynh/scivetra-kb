"""S04-A/B: verified bridge sources, frozen identifiers, PBD input CSV v1, DOI/URL cell policy, output-folder rules."""
import hashlib
import json
import os
import shutil
import unittest
from pathlib import Path

from tests.bridge_helpers import (NATIVE_FT, BridgeCase, candidate, pbd_read_jobs, synthetic_corpus, write_fulltext_export)
from tests.helpers import KBTest
from tests.s03_helpers import s03_item, write_export
from scivetra_kb.bridge import pbd, policy
from scivetra_kb.bridge.service import BridgeService, batch_short, bridge_row_id
from scivetra_kb.bridge.sources import _render, read_fulltext_export, rv_digest
from scivetra_kb.core.errors import IntegrityError, KBError, ValidationError
from scivetra_kb.library.service import LibraryService


def counts(kb):
    return {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("bridge_batch", "bridge_row", "bridge_row_event", "bridge_batch_event")}


class SourceTests(BridgeCase):
    def test_native_stage11b_verified_and_acquired_not_exported(self):
        """IDs: S04-SRC-11B-NATIVE, B-INV-1. The native Reprovane 2.0.1 Stage-11B export verifies with the independent checker (same manifest digest as Reprovane's own verify-export); only the 5 MANUAL_REQUIRED items are exported, never the ACQUIRED_AUTO one; expected_hash_supplied is recorded."""
        report = json.loads((NATIVE_FT / "generation_report.json").read_text(encoding="utf-8"))
        native = next(e for e in report["exports"] if e["label"] == "fulltext")
        self.assertEqual(native["native_cli_exit_code"], 0)
        independent = read_fulltext_export(self.fulltext)
        self.assertEqual(independent["rv_digest"], native["verify_export"]["manifest_sha256"])
        out = self.export()
        acquired = [s["corpus_item_id"] for s in independent["manifest"]["items"] if s["status"] == "ACQUIRED_AUTO"]
        self.assertEqual((out["row_count"], len(acquired)), (5, 1))
        rows = self.manifest()["rows"]
        self.assertNotIn(acquired[0], [r["corpus_item_id"] for r in rows])
        self.assertFalse(out["expected_hash_supplied"])
        self.assertIn("not a signature", out["verify_export"]["scope"])
        out2 = self.export(out_dir=os.path.join(self.tmp, "batch2"), expected_manifest_sha256=independent["rv_digest"])
        self.assertTrue(out2["expected_hash_supplied"])
        with self.reader() as kb:
            self.assertEqual([r["expected_hash_supplied"] for r in kb.all("SELECT expected_hash_supplied FROM bridge_batch ORDER BY rowid")], [0, 1])

    def test_expected_hash_mismatch_and_native_cross_check(self):
        """IDs: S04-SRC-EXPECTED-HASH, S04-SRC-NATIVE-XCHECK. A wrong expected manifest digest is EXPECTED_MANIFEST_MISMATCH with no batch, rows or files; Reprovane's own verify-export JSON is accepted when it matches and NATIVE_VERIFY_MISMATCH when it does not."""
        with self.assertRaises(IntegrityError) as ctx:
            self.export(expected_manifest_sha256="0" * 64)
        self.assertEqual(ctx.exception.code, "EXPECTED_MANIFEST_MISMATCH")
        self.assertFalse(os.path.exists(self.out))
        with self.reader() as kb:
            self.assertEqual(counts(kb)["bridge_batch"], 0)
        report = json.loads((NATIVE_FT / "generation_report.json").read_text(encoding="utf-8"))
        native = next(e for e in report["exports"] if e["label"] == "fulltext")["native_cli_output"]
        good, bad = Path(self.tmp, "verify.json"), Path(self.tmp, "verify-bad.json")
        good.write_text(json.dumps(native), encoding="utf-8")
        bad.write_text(json.dumps(dict(native, manifest_sha256="1" * 64)), encoding="utf-8")
        out = self.export(rv_verify_json=str(good))
        self.assertEqual(out["verify_export"]["native_verify_export"]["manifest_sha256"], native["manifest_sha256"])
        with self.assertRaises(IntegrityError) as ctx:
            self.export(out_dir=os.path.join(self.tmp, "b3"), rv_verify_json=str(bad))
        self.assertEqual(ctx.exception.code, "NATIVE_VERIFY_MISMATCH")

    def test_tampered_and_self_consistent_untrusted_exports(self):
        """IDs: S04-SRC-TAMPER, S04-SRC-SELF-CONSISTENT. An edited manual_queue.jsonl (even with recomputed SHA256SUMS), an extra file and a missing file are refused; a regenerated self-consistent export is accepted only as 'self-consistency, not authenticity' and is bound to a release only by an operator-supplied expected digest."""
        def resum(d):
            files = {n: Path(d, n).read_bytes() for n in ("fulltext_items.jsonl", "fulltext_manifest.json", "manual_queue.csv", "manual_queue.jsonl")}
            Path(d, "SHA256SUMS.txt").write_bytes("".join(hashlib.sha256(b).hexdigest() + "  " + n + "\n" for n, b in sorted(files.items())).encode())
        cases = []
        d1 = os.path.join(self.tmp, "t1")
        shutil.copytree(self.fulltext, d1)
        Path(d1, "manual_queue.jsonl").write_bytes(Path(d1, "manual_queue.jsonl").read_bytes().replace(b"Synthetic Work 4", b"Synthetic Work X"))
        resum(d1)
        cases.append((d1, "FULLTEXT_EXPORT_INVALID"))
        d2 = os.path.join(self.tmp, "t2")
        shutil.copytree(self.fulltext, d2)
        Path(d2, "notes.txt").write_text("x")
        cases.append((d2, "EXPORT_INVENTORY_MISMATCH"))
        d3 = os.path.join(self.tmp, "t3")
        shutil.copytree(self.fulltext, d3)
        Path(d3, "manual_queue.csv").unlink()
        cases.append((d3, "EXPORT_INVENTORY_MISMATCH"))
        d4 = os.path.join(self.tmp, "t4")
        shutil.copytree(self.fulltext, d4)
        Path(d4, "manual_queue.csv").write_bytes(b"x")
        cases.append((d4, "EXPORT_HASH_MISMATCH"))
        for directory, code in cases:
            with self.subTest(directory=os.path.basename(directory)):
                with self.assertRaises(KBError) as ctx:
                    read_fulltext_export(directory)
                self.assertEqual(ctx.exception.code, code)
        items = [json.loads(line) for line in Path(self.corpus, "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
        release_sem = json.loads(Path(self.corpus, "manifest.json").read_text(encoding="utf-8"))["release_semantic_manifest_sha256"]
        forged = write_fulltext_export(os.path.join(self.tmp, "forged"), items, release_id="crl_66da990ffa62424aaeace0bc4d85c0ee",
                                       release_sem=release_sem)
        result = read_fulltext_export(forged)
        self.assertTrue(result["verify"]["verified"])
        self.assertFalse(result["verify"]["expected_hash_supplied"])
        self.assertIn("not a signature or authenticity claim", result["verify"]["scope"])
        native = read_fulltext_export(self.fulltext)["rv_digest"]
        with self.assertRaises(IntegrityError) as ctx:
            read_fulltext_export(forged, expected_manifest_sha256=native)
        self.assertEqual(ctx.exception.code, "EXPECTED_MANIFEST_MISMATCH")

    def test_stage11_corpus_source_and_acquired_exclusion(self):
        """IDs: S04-SRC-11-CORPUS. The Stage-11 corpus fallback exports registered references; with the matching Stage-11B export the ACQUIRED_AUTO item is excluded; without it the acquisition state is recorded as unknown; no RV eligibility exists, so no direct URL is exported."""
        out = self.export(source_dir=self.corpus, kind="rv-corpus", fulltext_dir=self.fulltext)
        self.assertEqual((out["row_count"], out["excluded_already_acquired"]), (5, 1))
        manifest = self.manifest()
        self.assertTrue(manifest["source"]["acquisition_state_known"])
        self.assertTrue(all("NO_RV_ELIGIBILITY_DECISION" in r["flags"] and not r["exported_urls"]["best_oa_pdf_url"] for r in manifest["rows"]))
        out2 = self.export(source_dir=self.corpus, kind="rv-corpus", out_dir=os.path.join(self.tmp, "b2"))
        self.assertEqual(out2["row_count"], 6)
        self.assertFalse(self.manifest(os.path.join(self.tmp, "b2"))["source"]["acquisition_state_known"])

    def test_source_must_be_registered_in_kb(self):
        """IDs: S04-SRC-REGISTERED, B-INV-2. A Stage-11B export whose release was never imported with the S03 library import is refused (SOURCE_NOT_REGISTERED / WORKSPACE_UNKNOWN); nothing is created; the source export bytes are unchanged."""
        before = {p.name: p.read_bytes() for p in Path(self.fulltext).iterdir()}
        with self.assertRaises(ValidationError) as ctx:
            self.export(workspace_label="other")
        self.assertEqual(ctx.exception.code, "WORKSPACE_UNKNOWN")
        with self.writer() as kb:
            LibraryService(kb).ensure_workspace("empty", "OPERATOR:op.a")
        with self.assertRaises(ValidationError) as ctx:
            self.export(workspace_label="empty")
        self.assertEqual(ctx.exception.code, "SOURCE_NOT_REGISTERED")
        self.assertFalse(os.path.exists(self.out))
        with self.reader() as kb:
            self.assertEqual(counts(kb), {"bridge_batch": 0, "bridge_row": 0, "bridge_row_event": 0, "bridge_batch_event": 0})
        self.assertEqual({p.name: p.read_bytes() for p in Path(self.fulltext).iterdir()}, before)


class IdentifierAndCsvTests(BridgeCase):
    def test_frozen_identifiers_and_independent_persistence(self):
        """IDs: S04-ID-FROZEN, S04-D02, B-ID-1. batch_id ab_*, batch_short = sha256(batch_id)[:8], bridge_row_id = 'b'+sha256(batch_id\\n+source_item_key)[:23], save_as = <row>.pdf; manifest rows, KB rows and the KB/CAS copies of manifest and CSV agree; OpenAlex Work IDs appear only in the manifest, never in the PBD record_id."""
        out = self.export()
        manifest = self.manifest()
        self.assertTrue(out["batch_id"].startswith("ab_"))
        self.assertEqual(out["batch_short"], hashlib.sha256(out["batch_id"].encode()).hexdigest()[:8])
        self.assertEqual(batch_short(out["batch_id"]), out["batch_short"])
        csv_bytes = Path(self.out, out["input_csv"]).read_bytes()
        manifest_bytes = Path(self.out, "bridge_manifest.json").read_bytes()
        with self.reader() as kb:
            batch = kb.one("SELECT * FROM bridge_batch")
            db_rows = kb.all("SELECT * FROM bridge_row ORDER BY row_ordinal")
            self.assertEqual(kb.cas.read(batch["manifest_bytes_sha256"]), manifest_bytes)
            self.assertEqual(kb.cas.read(batch["input_csv_bytes_sha256"]), csv_bytes)
            refs = {r["reference_id"]: r for r in kb.all("SELECT * FROM project_paper_reference")}
        self.assertEqual(batch["manifest_bytes_sha256"], hashlib.sha256(manifest_bytes).hexdigest())
        for mrow, drow in zip(manifest["rows"], db_rows):
            self.assertEqual(mrow["bridge_row_id"], bridge_row_id(out["batch_id"], mrow["source_item_key"]))
            self.assertEqual(len(mrow["bridge_row_id"]), 24)
            self.assertEqual((drow["bridge_row_id"], drow["save_as"], drow["paper_id"]), (mrow["bridge_row_id"], mrow["bridge_row_id"] + ".pdf", mrow["paper_id"]))
            self.assertEqual(refs[drow["reference_id"]]["paper_id"], drow["paper_id"])
            self.assertTrue(mrow["source_item_key"].startswith("rv:" + manifest["source"]["release_manifest_sha256"] + ":"))
            self.assertTrue(all(w.startswith("https://openalex.org/W") for w in mrow["openalex_work_ids"]))
        self.assertNotIn(b"openalex.org", csv_bytes)
        self.assertNotIn(b"openalex_id", csv_bytes)
        self.assertNotIn(self.tmp.encode(), manifest_bytes + csv_bytes)
        with self.writer() as kb:
            with self.assertRaises(KBError):
                kb.execute("UPDATE bridge_row SET export_doi='x'")
            with self.assertRaises(KBError):
                kb.execute("DELETE FROM bridge_batch")

    def test_pbd_input_csv_v1_format(self):
        """IDs: S04-CSV-INPUT, B-CSV-1. UTF-8 BOM, exact 12-column header, every field quoted, CRLF, no blank lines; the Python port of PBD ReadJobs (V5.2.11) maps record_id to the bridge token, keeps save_as and applies best_oa_pdf_url > pdf_url > landing_page > doi."""
        out = self.export()
        data = Path(self.out, out["input_csv"]).read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf" + pbd.INPUT_HEADER.encode() + b"\r\n"))
        lines = data[3:].split(b"\r\n")
        self.assertEqual(lines[-1], b"")
        self.assertNotIn(b"\n", b"".join(lines))
        self.assertEqual(len(lines) - 2, out["row_count"])
        self.assertTrue(all(line.startswith(b'"') and line.endswith(b'"') and line.count(b'","') == 11 for line in lines[1:-1]))
        manifest = {r["bridge_row_id"]: r for r in self.manifest()["rows"]}
        jobs = pbd_read_jobs(data)
        self.assertEqual([j["id"] for j in jobs], list(range(1, 6)))
        for job in jobs:
            row = manifest[job["openalex_id"]]
            self.assertEqual(job["save_as"], row["save_as"])
            urls = row["exported_urls"]
            expected = urls["best_oa_pdf_url"] or urls["pdf_url"] or urls["landing_page"] or ("https://doi.org/" + row["export_doi"])
            self.assertEqual(job["url"], expected)
            self.assertEqual(job["doi"], row["export_doi"])
        self.assertEqual({j["url_source"] for j in jobs}, {"best_oa_pdf_url", "best_oa_landing_page", "doi"})
        contract = json.loads(Path(__file__).parent.joinpath("fixtures", "pbd_csv_contract.json").read_text(encoding="utf-8"))
        self.assertEqual((list(pbd.INPUT_COLUMNS), list(pbd.OUTPUT_COLUMNS)), (contract["input_columns"], contract["output_columns"]))


class CellPolicyTests(KBTest):
    def setUp(self):
        super().setUp()
        self.corpus_dir = os.path.join(self.tmp, "corpus")
        self.items = [
            s03_item("cri_valid", dois=[("10.99999/ok.1", "10.99999/ok.1")], title='Quote " comma, line\r\nbreak \u00e9\u4e2d =cmd|calc'),
            s03_item("cri_none", dois=[], title="No DOI study"),
            s03_item("cri_multi", dois=[("10.99999/m.1", "10.99999/m.1"), ("10.99999/m.2", "10.99999/m.2")], title="Two DOI study"),
            s03_item("cri_space", dois=[("10.99999/sp.1 ", None)], title="Trailing space DOI"),
            s03_item("cri_unsafe", dois=[("10.99999/a#b", "10.99999/a#b")], title="Unsafe DOI"),
            s03_item("cri_slash", dois=[("10.99999/a/b", "10.99999/a/b")], title="Slash in suffix"),
            s03_item("cri_dup1", dois=[("10.99999/m.1", "10.99999/m.1")], title="Shares a DOI with the multi-DOI item", entity="pub_" + "1" * 32),
            s03_item("cri_dup2", dois=[("10.99999/OK.1", "10.99999/ok.1")], title="Same DOI as cri_valid", entity="pub_" + "2" * 32),
            s03_item("cri_long", dois=[("10.99999/long", "10.99999/long")], title="L" * 900),
        ]
        write_export(self.corpus_dir, self.items, release_id="crl_cells")
        self.release_sem = json.loads(Path(self.corpus_dir, "manifest.json").read_text(encoding="utf-8"))["release_semantic_manifest_sha256"]
        with self.writer() as kb:
            LibraryService(kb).import_release(self.corpus_dir, workspace_label="ws", operator="op.a")
        manual = {
            "cri_valid": {"candidates": [candidate("https://papers.example.org/v.pdf"), candidate("https://repo.example.org/v2.pdf"),
                                         candidate("https://papers.example.org/landing/v"),
                                         candidate("https://preprints.example.org/v.pdf", eligible=False, reason="ONLY_SUBMITTED_VERSION")],
                          "landing": ["https://papers.example.org/landing/v"]},
            "cri_none": {"candidates": [candidate("https://user:pw" + chr(64) + "papers.example.org/a.pdf"), candidate("https://10.0.0.5/a.pdf"),
                                        candidate("file:///C:/papers/a.pdf"), candidate("https://localhost/a.pdf"),
                                        candidate("https://papers.example.org:8443/a.pdf"), candidate("https://papers.example.org/a.pdf#page=2"),
                                        candidate("https://cdn.example.org/a.pdf?X-Amz-Signature=abc&X-Amz-Expires=60"),
                                        candidate("https://cdn.example.org/b.pdf?token=REDACTED")]},
        }
        self.fulltext_dir = write_fulltext_export(os.path.join(self.tmp, "fulltext"), self.items, release_id="crl_cells", release_sem=self.release_sem,
                                                  manual=manual)

    def export(self, out="b1", **kw):
        with self.writer() as kb:
            return BridgeService(kb).export_batch(source_dir=self.fulltext_dir, kind="rv-manual-queue", workspace_label="ws",
                                                  out_dir=os.path.join(self.tmp, out), operator="op.b", **kw)

    def rows(self, out="b1"):
        return {r["corpus_item_id"]: r for r in json.loads(Path(self.tmp, out, "bridge_manifest.json").read_text(encoding="utf-8"))["rows"]}

    def test_doi_cell_rules(self):
        """IDs: S04-DOI-RULES, B-DOI-1. Only one VALID, ACTIVE, URL-safe DOI is written: no DOI, multiple DOIs, a trailing-space DOI (never trimmed into another DOI), '#' or '/' in the suffix, and a DOI CONFLICTING after an S03 collision leave the cell empty with a flag; the original DOIs stay in the manifest."""
        self.export()
        rows = self.rows()
        expect = {"cri_valid": ("10.99999/ok.1", []), "cri_long": ("10.99999/long", []), "cri_none": (None, ["NO_DOI"]),
                  "cri_multi": (None, ["MULTIPLE_DOI_SUPPRESSED"]), "cri_space": (None, ["NO_DOI"]),
                  "cri_unsafe": (None, ["DOI_NOT_URL_SAFE_FOR_PBD"]), "cri_slash": (None, ["DOI_NOT_URL_SAFE_FOR_PBD"]),
                  "cri_dup1": (None, ["DOI_CONFLICTING_SUPPRESSED"]), "cri_dup2": ("10.99999/ok.1", [])}
        for cid, (doi, flags) in expect.items():
            with self.subTest(cid=cid):
                self.assertEqual(rows[cid]["export_doi"], doi)
                for flag in flags:
                    self.assertIn(flag, rows[cid]["flags"])
        self.assertEqual(rows["cri_multi"]["all_dois"], ["10.99999/m.1", "10.99999/m.2"])
        # Two RV items auto-linked (exact DOI) to one KB paper: both exported, the second flagged; never merged by the bridge.
        self.assertEqual(rows["cri_dup2"]["paper_id"], rows["cri_valid"]["paper_id"])
        self.assertIn("PAPER_ALREADY_IN_BATCH", rows["cri_valid"]["flags"] + rows["cri_dup2"]["flags"])
        self.assertEqual(rows["cri_space"]["all_dois"], [])
        csv_text = Path(self.tmp, "b1", next(p.name for p in Path(self.tmp, "b1").glob("pbd_input_*.csv"))).read_text(encoding="utf-8-sig")
        self.assertNotIn("10.99999/sp.1", csv_text)
        self.assertNotIn("10.99999/m.", csv_text)
        self.assertEqual(policy.select_doi(["10.99999/sp.1 "], ["10.99999/sp.1"], []), (None, ["DOI_SOURCE_KB_MISMATCH"]))
        self.assertEqual(policy.select_doi(["10.99999/A"], ["10.99999/a"], []), ("10.99999/a", []))

    def test_url_rules_and_ineligible_flag(self):
        """IDs: S04-URL-RULES, B-URL-1. Eligible direct URLs become best_oa_pdf_url/pdf_url, a landing page only landing_page; ineligible RV candidates are not exported unless --include-ineligible-candidates (then flagged); userinfo, IP literal, file:, localhost, port, fragment and signed/REDACTED URLs are never exported."""
        self.export()
        rows = self.rows()
        self.assertEqual(rows["cri_valid"]["exported_urls"], {"best_oa_pdf_url": "https://papers.example.org/v.pdf",
                                                              "pdf_url": "https://repo.example.org/v2.pdf", "landing_page": "https://papers.example.org/landing/v"})
        self.assertIn("RV_INELIGIBLE_URL_NOT_EXPORTED", rows["cri_valid"]["flags"])
        self.assertEqual(rows["cri_none"]["exported_urls"], {"best_oa_pdf_url": "", "pdf_url": "", "landing_page": ""})
        for code in ("URL_USERINFO_REFUSED", "URL_IP_LITERAL_REFUSED", "URL_SCHEME_REFUSED", "URL_LOCAL_HOST_REFUSED", "URL_AUTHORITY_REFUSED",
                     "URL_FRAGMENT_REFUSED", "SIGNED_URL_OMITTED", "NO_URL_EXPECTED"):
            self.assertIn(code, rows["cri_none"]["flags"])
        self.export(out="b2", include_ineligible=True)
        loose = self.rows("b2")["cri_valid"]
        self.assertIn("RV_INELIGIBLE_URL_EXPORTED", loose["flags"])
        self.assertEqual(loose["ineligible_urls_exported"][0]["rv_reason"], "ONLY_SUBMITTED_VERSION")
        for url in ("https://Papers.Example.org/x.pdf", "http://papers.example.org/x?id=1&download=1"):
            self.assertIsNone(policy.url_refusal(url))
        for url in ("https://[::1]/x", "https://papers.example.org/x?Signature=1", "https://papers.example.org/x?sessionid=1", "https://a/x",
                    "https://papers.example.org/\u00e9", "https://papers.example.org/a b", "javascript:alert(1)", "\\\\server\\share\\x.pdf"):
            self.assertIsNotNone(policy.url_refusal(url), url)

    def test_display_field_escaping_round_trip(self):
        """IDs: S04-CSV-ESCAPING. Quotes, commas, CR/LF, non-ASCII, a formula-leading title and an over-long title survive the PBD ReadJobs port as one sanitized display value (newlines -> spaces, formula escaped, bounded); the manifest keeps the original title claim."""
        self.export()
        rows = self.rows()
        csv_bytes = next(Path(self.tmp, "b1").glob("pbd_input_*.csv")).read_bytes()
        jobs = {j["openalex_id"]: j for j in pbd_read_jobs(csv_bytes)}
        valid = jobs[rows["cri_valid"]["bridge_row_id"]]
        self.assertEqual(valid["title"], 'Quote " comma, line  break \u00e9\u4e2d =cmd|calc')
        self.assertIn("TITLE_CONTROL_CHARS_REPLACED", rows["cri_valid"]["flags"])
        self.assertEqual(rows["cri_valid"]["titles"], ['Quote " comma, line\r\nbreak \u00e9\u4e2d =cmd|calc'])
        self.assertEqual(len(jobs[rows["cri_long"]["bridge_row_id"]]["title"]), policy.MAX_DISPLAY_CHARS)
        self.assertIn("TITLE_TRUNCATED", rows["cri_long"]["flags"])
        flags = []
        self.assertEqual(policy.display("=HYPERLINK(1)", flags=flags, name="title"), "'=HYPERLINK(1)")
        self.assertEqual(flags, ["TITLE_FORMULA_ESCAPED"])
        with self.assertRaises(ValueError):
            pbd.write_input_csv([{c: ("\x00" if c == "title" else "") for c in pbd.INPUT_COLUMNS}])

    def test_out_dir_rules(self):
        """IDs: S04-OUTDIR. --out-dir must be new or empty and outside the KB and the source export; refusals create no files and no DB rows."""
        os.makedirs(os.path.join(self.tmp, "busy"))
        Path(self.tmp, "busy", "x").write_text("x")
        for out, code in (("busy", "OUT_DIR_NOT_EMPTY"), (os.path.join("kb", "bridge"), "OUT_DIR_INVALID"),
                          (os.path.join("fulltext", "inside"), "OUT_DIR_INVALID")):
            with self.subTest(out=out):
                with self.assertRaises(ValidationError) as ctx:
                    self.export(out=out)
                self.assertEqual(ctx.exception.code, code)
        self.assertFalse(os.path.exists(os.path.join(self.root, "bridge")))
        self.assertFalse(os.path.exists(os.path.join(self.fulltext_dir, "inside")))
        with self.reader() as kb:
            self.assertEqual(counts(kb)["bridge_batch"], 0)
        with self.assertRaises(ValidationError) as ctx:
            self.export(out="b5", limit=0)
        self.assertEqual(ctx.exception.code, "LIMIT_INVALID")
        self.assertEqual(self.export(out="b6", limit=2)["row_count"], 2)


class NumericHostTests(BridgeCase):
    """S04-F01 (v02): numeric IPv4 aliases and other internal hosts never reach the PBD input CSV."""

    UNSAFE = {
        # canonical literals
        "127.0.0.1": "URL_IP_LITERAL_REFUSED", "10.0.0.5": "URL_IP_LITERAL_REFUSED", "169.254.169.254": "URL_IP_LITERAL_REFUSED",
        "8.8.8.8": "URL_IP_LITERAL_REFUSED", "[::1]": "URL_IP_LITERAL_REFUSED", "[::ffff:127.0.0.1]": "URL_IP_LITERAL_REFUSED",
        "[fe80::1%25eth0]": "URL_IP_LITERAL_REFUSED",
        # abbreviated, octal, hex, single-number and mixed IPv4 notations
        "127.1": "URL_NUMERIC_HOST_REFUSED", "0177.0.0.1": "URL_NUMERIC_HOST_REFUSED", "0x7f.0.0.1": "URL_NUMERIC_HOST_REFUSED",
        "0X7F.0.0.1": "URL_NUMERIC_HOST_REFUSED", "0x7F.1": "URL_NUMERIC_HOST_REFUSED", "192.168.1": "URL_NUMERIC_HOST_REFUSED",
        "10.1": "URL_NUMERIC_HOST_REFUSED", "0x7f000001": "URL_NUMERIC_HOST_REFUSED", "2130706433": "URL_NUMERIC_HOST_REFUSED",
        "017700000001": "URL_NUMERIC_HOST_REFUSED", "127.0.0.01": "URL_NUMERIC_HOST_REFUSED", "0177.0x0.1": "URL_NUMERIC_HOST_REFUSED",
        "127.0.0.1.": "URL_NUMERIC_HOST_REFUSED", "1.2.3.4.5": "URL_NUMERIC_HOST_REFUSED", "0x.0x.0x.0x": "URL_NUMERIC_HOST_REFUSED",
        "0x7f.example.org": "URL_NUMERIC_HOST_REFUSED", "papers.example.123": "URL_NUMERIC_HOST_REFUSED",
        # encodings a browser may decode into one of the above
        "127%2e0%2e0%2e1": "URL_HOST_CHARACTERS_REFUSED", "127.0.0.1%00.example.org": "URL_HOST_CHARACTERS_REFUSED",
        # local / internal / reserved names
        "localhost": "URL_LOCAL_HOST_REFUSED", "LOCALHOST": "URL_LOCAL_HOST_REFUSED", "printer": "URL_LOCAL_HOST_REFUSED",
        "api.localhost": "URL_LOCAL_HOST_REFUSED", "nas.local": "URL_LOCAL_HOST_REFUSED", "db.internal": "URL_LOCAL_HOST_REFUSED",
        "router.home.arpa": "URL_LOCAL_HOST_REFUSED", "1.0.0.127.in-addr.arpa": "URL_LOCAL_HOST_REFUSED",
        "files.corp": "URL_LOCAL_HOST_REFUSED", "box.lan": "URL_LOCAL_HOST_REFUSED", "app.test": "URL_LOCAL_HOST_REFUSED",
        "papers.example.org.": "URL_LOCAL_HOST_REFUSED",
        # authority decorations around a numeric host
        "user" + chr(64) + "127.1": "URL_USERINFO_REFUSED", "127.1:443": "URL_AUTHORITY_REFUSED", "papers.example.org:8443": "URL_AUTHORITY_REFUSED",
    }
    SAFE = ("trusted.example.org", "journals.example.org", "www.example.com", "1e100.example.net", "123.example.org", "10.example.org",
            "x0.example.org", "xn--bcher-kva.example.org", "repo_mirror.example.org", "a-b.example.co.uk")

    def test_numeric_host_url_policy(self):
        """IDs: S04-F01-POLICY, S04-URL-NUMERIC. Pure lexical policy (no DNS): canonical IPv4/IPv6 literals, abbreviated/octal/hex/
        single-number/mixed IPv4 aliases, a trailing dot, %-encoded hosts, localhost and reserved internal suffixes, userinfo and ports
        are refused with a stable code (upper-case hosts are lower-cased by urlsplit first, so 0X7F.0.0.1 is numeric too); ordinary DNS
        names, including digit-leading labels and an upper-case DNS name, stay exportable. A fragment keeps
        URL_FRAGMENT_REFUSED."""
        for host, code in self.UNSAFE.items():
            with self.subTest(host=host):
                self.assertEqual(policy.url_refusal("https://" + host + "/paper.pdf"), code)
                self.assertEqual(policy.url_refusal("http://" + host + "/paper.pdf"), code)
        for host in ("0X7F.0.0.1", "0x7F.1", "0X7F000001"):
            self.assertTrue(policy.numeric_host(host.lower()), host)
        for host in self.SAFE + ("Papers.Example.org",):
            with self.subTest(host=host):
                self.assertIsNone(policy.url_refusal("https://" + host + "/paper.pdf"))
                self.assertFalse(policy.numeric_host(host.lower()))
        self.assertEqual(policy.url_refusal("https://trusted.example.org/paper.pdf#page=2"), "URL_FRAGMENT_REFUSED")
        self.assertEqual(policy.url_refusal("https://127.1/paper.pdf#page=2"), "URL_NUMERIC_HOST_REFUSED")

    def test_full_export_refuses_numeric_hosts(self):
        """IDs: S04-F01-EXPORT. Through BridgeService.export_batch on a self-consistent SYNTHETIC Stage-11B manual queue: numeric-alias
        candidates and landing pages are absent from the PBD input CSV bytes, exported_urls and the whole manifest; each refusal is a row
        flag; a later safe candidate of the same item is still exported as itself (never a rewritten form of a refused URL), and an item
        with only unsafe URLs exports no URL and is flagged."""
        path = Path(self.fulltext, "fulltext_manifest.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        unsafe = ["https://127.1/candidate.pdf", "https://0177.0.0.1/a.pdf", "https://0x7f.0.0.1/b.pdf", "https://192.168.1/c.pdf",
                  "https://10.1/d.pdf", "https://0X7F000001/e.pdf"]
        manifest["manual_queue"][0]["candidates"] = [candidate(u, eligible=True) for u in unsafe[:3]] + [
            candidate("https://trusted.example.org/paper.pdf", eligible=True)]
        manifest["manual_queue"][0]["landing_pages"] = ["https://10.1/landing", "https://journals.example.org/article"]
        manifest["manual_queue"][1]["candidates"] = [candidate(u, eligible=True) for u in unsafe[3:]]
        manifest["manual_queue"][1]["landing_pages"] = ["https://127.0.0.01/landing"]
        manifest["manual_queue_sha256"] = rv_digest(manifest["manual_queue"])
        for filename, payload in _render(manifest).items():
            Path(self.fulltext, filename).write_bytes(payload)
        batch = self.export()
        csv_bytes = Path(self.out, batch["input_csv"]).read_bytes()
        manifest_bytes = Path(self.out, "bridge_manifest.json").read_bytes()
        rows = {r["corpus_item_id"]: r for r in self.manifest()["rows"]}
        first, second = rows[manifest["manual_queue"][0]["corpus_item_id"]], rows[manifest["manual_queue"][1]["corpus_item_id"]]
        for url in unsafe + ["https://10.1/landing", "https://127.0.0.01/landing"]:
            with self.subTest(url=url):
                self.assertNotIn(url.encode(), csv_bytes)
                self.assertNotIn(url.encode(), manifest_bytes)
                self.assertNotIn(("//" + url.split("/")[2].lower() + "/").encode(), csv_bytes.lower())
        self.assertEqual(first["exported_urls"], {"best_oa_pdf_url": "https://trusted.example.org/paper.pdf", "pdf_url": "",
                                                  "landing_page": "https://journals.example.org/article"})
        self.assertIn("URL_NUMERIC_HOST_REFUSED", first["flags"])  # row flags are a de-duplicated set
        self.assertEqual(second["exported_urls"], {"best_oa_pdf_url": "", "pdf_url": "", "landing_page": ""})
        self.assertIn("URL_NUMERIC_HOST_REFUSED", second["flags"])
        self.assertNotIn("URL_AUTHORITY_REFUSED", second["flags"])  # upper-case 0X7F000001 is lower-cased by urlsplit, then numeric
        self.assertEqual(batch["flag_counts"]["URL_NUMERIC_HOST_REFUSED"], 2)  # rows carrying the flag
        jobs = {j["openalex_id"]: j for j in pbd_read_jobs(csv_bytes)}
        self.assertEqual(jobs[first["bridge_row_id"]]["best_oa_pdf_url"], "https://trusted.example.org/paper.pdf")
        self.assertEqual(jobs[second["bridge_row_id"]]["best_oa_pdf_url"], "")


if __name__ == "__main__":
    unittest.main()
