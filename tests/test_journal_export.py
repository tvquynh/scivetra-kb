"""S04J: deterministic CSV/JSONL export with provenance; OA dry-run queue (evidence + rights + URL guard, no downloads)."""
import csv
import io
import json
import os

from tests.journal_helpers import (JISA, KBTest, Reconciler, SimulatedProviders, export_version, harvester, members, shared_jisa, small_scenario)
from scivetra_kb.core.db import open_reader
from scivetra_kb.core.errors import KBError
from scivetra_kb.journal.export import COLUMNS


def _folder(shared, i=1):
    return os.path.join(shared["tmp"], f"out{i}", shared["exports"][i - 1]["folder_name"])


class ExportTests(KBTest):
    def test_export_is_byte_deterministic_with_provenance(self):
        """IDs: JH-23, S04J-EXPORT-01. Two exports of the same version into different folders are byte-identical (same folder name, manifest and SHA256SUMS); files are strict UTF-8 without BOM; the CSV header is the documented column list and has one row per subject; every JSONL line is canonical JSON; the provenance sidecar names, for each projected field, an existing observation and a JSON pointer; SHA256SUMS verifies every file."""
        import hashlib
        shared = shared_jisa()
        a, b = _folder(shared, 1), _folder(shared, 2)
        self.assertEqual(os.path.basename(a), os.path.basename(b))
        names = sorted(os.listdir(a))
        self.assertEqual(names, sorted(os.listdir(b)))
        for name in names:
            with open(os.path.join(a, name), "rb") as x, open(os.path.join(b, name), "rb") as y:
                data = x.read()
                self.assertEqual(data, y.read(), name)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                data.decode("utf-8")
        sums = open(os.path.join(a, "SHA256SUMS.txt"), encoding="utf-8").read().splitlines()
        for line in sums:
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256(open(os.path.join(a, name), "rb").read()).hexdigest(), digest)
        rows = list(csv.DictReader(io.StringIO(open(os.path.join(a, "journal_works.csv"), encoding="utf-8", newline="").read())))
        self.assertEqual(list(rows[0].keys()), list(COLUMNS))
        self.assertEqual(len(rows), shared["version"]["member_count"])
        self.assertEqual({r["screening_status"] for r in rows}, {"NOT_SCREENED"})
        self.assertEqual({r["inventory_scope"] for r in rows}, {"DISCOVERY_EVIDENCE_ONLY"})
        from scivetra_kb.core.canonical import canonical_json
        with open(os.path.join(a, "journal_works.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                self.assertEqual(canonical_json(json.loads(line)), line.rstrip("\n"))
        with open_reader(shared["root"]) as kb, open(os.path.join(a, "field_provenance.jsonl"), encoding="utf-8") as fh:
            for n, line in enumerate(fh):
                if n % 97:
                    continue
                for field, ref in json.loads(line)["fields"].items():
                    self.assertTrue(ref["path"].startswith("/"), field)
                    self.assertIsNotNone(kb.one("SELECT 1 x FROM jh_observation WHERE observation_id=? AND run_id=?", (ref["observation_id"], ref["run_id"])))
        manifest = json.load(open(os.path.join(a, "export_manifest.json"), encoding="utf-8"))
        self.assertEqual((manifest["downloads_performed"], manifest["pdf_identity_policy"]), (0, "UNVERIFIED_UNTIL_HUMAN_CONFIRMATION"))

    def test_reexport_is_noop_and_type_filter_is_explicit(self):
        """IDs: JH-23, JH-21, JH-09. Re-exporting into a folder that already holds identical bytes is a verified no-op; a different content under the same name is refused; an explicit --work-type journal-article filter is recorded in the manifest and drops the other types, while the default export keeps all types."""
        shared = shared_jisa()
        from scivetra_kb.core.db import open_writer
        import shutil
        root = os.path.join(self.tmp, "kb2")
        shutil.copytree(shared["root"], root)
        out = os.path.join(self.tmp, "exports")
        with open_writer(root) as kb:
            exports_before = kb.one("SELECT count(*) n FROM jh_export")["n"]
            first = export_version(kb, shared["version"]["version_id"], out, operator="tester")
            again = export_version(kb, shared["version"]["version_id"], out, operator="tester")
            self.assertEqual((first["written"], again["written"], again["code"]), (True, False, "EXPORT_ALREADY_PRESENT_IDENTICAL"))
            with open(os.path.join(out, first["folder_name"], "coverage.json"), "ab") as fh:
                fh.write(b" ")
            with self.assertRaises(KBError) as ctx:
                export_version(kb, shared["version"]["version_id"], out, operator="tester")
            self.assertEqual(ctx.exception.code, "EXPORT_FOLDER_EXISTS_DIFFERENT")
            filtered = export_version(kb, shared["version"]["version_id"], os.path.join(self.tmp, "filtered"), operator="tester",
                                      work_types=["journal-article"])
            manifest = json.load(open(os.path.join(self.tmp, "filtered", filtered["folder_name"], "export_manifest.json"), encoding="utf-8"))
            self.assertEqual(manifest["filter"], {"work_types": ["journal-article"]})
            rows = list(csv.DictReader(open(os.path.join(self.tmp, "filtered", filtered["folder_name"], "journal_works.csv"), encoding="utf-8", newline="")))
            self.assertTrue(all(r["work_type"] == "journal-article" or r["openalex_type"] == "journal-article" for r in rows))
            self.assertLess(len(rows), shared["version"]["member_count"])
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_export")["n"], exports_before + 2)   # the no-op and the refusal add nothing

    def test_csv_neutralizes_formula_cells_and_refuses_unsafe_out_dirs(self):
        """IDs: JH-23, S04J-EXPORT-SAFE. A provider title starting with '=' is written as text prefixed with an apostrophe; --out-dir inside the KB, the protected production root and a drive-relative path are refused before anything is written."""
        item = small_scenario(1)["crossref_works"]["JISA"][0]
        item = dict(item, DOI="10.5555/jisa-synthetic.formula", title=["=HYPERLINK(\"https://x.example\",\"y\")"])
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(small_scenario(4, rows_extra=[item])))
            jid = h.register_journal("formula", JISA)["journal_id"]
            h.resolve(jid)
            h.harvest(jid, "CROSSREF", page_size=10)
            v = Reconciler(kb, operator="tester").reconcile(jid)
            out = export_version(kb, v["version_id"], os.path.join(self.tmp, "x"), operator="tester")
            text = open(os.path.join(self.tmp, "x", out["folder_name"], "journal_works.csv"), encoding="utf-8").read()
            self.assertIn("'=HYPERLINK", text)
            for bad, code in ((os.path.join(self.root, "inside"), "OUT_DIR_INVALID"), ("E:\\ResearchKB\\exports", "PROTECTED_DATA_ROOT"),
                              ("C:relative", "DRIVE_RELATIVE_PATH_REFUSED")):
                with self.assertRaises(KBError) as ctx:
                    export_version(kb, v["version_id"], bad, operator="tester")
                self.assertEqual(ctx.exception.code, code, bad)
            self.assertFalse(os.path.exists(os.path.join(self.root, "inside")))


class OaDryRunTests(KBTest):
    def test_oa_candidates_need_evidence_rights_and_a_safe_url(self):
        """IDs: JH-24, S04J-OA-DRYRUN. A DRY_RUN_CANDIDATE needs an OpenAlex OA location with a PDF URL, a Crossref licence that the accepted rights-policy/1 classifies PUBLIC_OA_LICENSED for that version, and a URL the S04 guard accepts. Negative cases stay NOT_A_CANDIDATE with their reason: landing page only (paywall-style), closed, publisher-specific licence (PUBLIC_OA_UNVERIFIED), malformed CC URI, embargo not elapsed, numeric-host PDF URL, retraction reported. An accepted-manuscript repository copy qualifies only with an 'am' licence."""
        shared = shared_jisa()
        with open_reader(shared["root"]) as kb:
            m = members(kb, shared["version"]["version_id"])
        dry = lambda i: m[f"10.5555/jisa-synthetic.{i:06d}"]["projection"]["oa_dry_run"]  # noqa: E731
        self.assertEqual((dry(10)["status"], dry(10)["version"], dry(10)["rights_class"]), ("DRY_RUN_CANDIDATE", "publishedVersion", "PUBLIC_OA_LICENSED"))
        self.assertEqual((dry(60)["status"], dry(60)["version"], dry(60)["pdf_url"]),
                         ("DRY_RUN_CANDIDATE", "acceptedVersion", "https://repository.example/60/accepted.pdf"))
        for i, reason in ((5, "NO_PDF_URL_LANDING_PAGE_ONLY"), (3, "NO_OA_LOCATION_REPORTED"), (11, "RIGHTS_PUBLIC_OA_UNVERIFIED"),
                          (13, "LICENCE_URI_NOT_RECOGNISED"), (17, "EMBARGO_NOT_ELAPSED"), (70, "URL_NUMERIC_HOST_REFUSED"),
                          (40, "RETRACTION_REPORTED_REVIEW_REQUIRED")):
            self.assertEqual(dry(i)["status"], "NOT_A_CANDIDATE", i)
            self.assertIn(reason, dry(i)["reasons"], i)
        rows = list(csv.DictReader(open(os.path.join(_folder(shared), "oa_candidate_queue_dryrun.csv"), encoding="utf-8", newline="")))
        self.assertEqual(len(rows), shared["version"]["coverage"]["reconciliation"]["oa_dry_run_candidates"])
        self.assertTrue(rows)
        self.assertEqual({(r["pdf_identity"], r["download_authorized"], r["rights_class"]) for r in rows}, {("UNVERIFIED", "NO", "PUBLIC_OA_LICENSED")})
        from scivetra_kb.bridge.policy import url_refusal
        self.assertTrue(all(url_refusal(r["pdf_url"]) is None for r in rows))
        with open_reader(shared["root"]) as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM bridge_batch")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM rights_assessment")["n"], 0)
        self.assertEqual(shared["version"]["coverage"]["reconciliation"]["pdf_download_success"], "NOT_APPLICABLE_NO_DOWNLOADS_IN_S04J")
        self.assertFalse(any(name.lower().endswith(".pdf") for name in os.listdir(_folder(shared))))
