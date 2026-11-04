"""S05: the FTE input universe is exactly one verified SEALED Stage-11 TA release; journal inventories never qualify."""
import json
import os
import shutil
from pathlib import Path

from tests.bridge_helpers import NATIVE_FT, copy_native_fulltext
from tests.fte_helpers import FTECase, PROTOCOL, ROSTER, write_ta_release
from tests.s03_helpers import s03_item, write_export
from scivetra_kb.core.errors import KBError
from scivetra_kb.eligibility.service import FTEService
from scivetra_kb.library.service import LibraryService


class SourceTests(FTECase):
    def _create(self, **over):
        r = self.make_run(3)
        args = dict(source_id=r["source"], export_dir=r["export"], expected_manifest_sha256=r["sem"], rv_verify_json=r["verify"],
                    protocol_id=r["protocol_id"], roster=ROSTER, label="again", operator="op.fte")
        args.update(over)
        with self.writer() as kb:
            return r, FTEService(kb).create_run(**args)

    def refused(self, code, **over):
        with self.assertRaises(KBError) as ctx:
            self._create(**over)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_exact_frozen_candidate_set_and_idempotent_create(self):
        """IDs: S05-SRC-EXACT, S05-SRC-IDEMPOTENT, S05-AUTHORITY. One candidate per frozen corpus_item_id (with item hash, publication entity, S03 reference and paper), whatever PDFs exist; the run records the release digest, Stage-10 screening binding, item-set digest, protocol/roster/assignment digests and authority NON_AUTHORITATIVE; re-creating the same run is DUPLICATE_NOOP."""
        r = self.make_run(7)
        with self.reader() as kb:
            run = kb.one("SELECT * FROM fte_run WHERE run_id=?", (r["run_id"],))
            refs = kb.all("SELECT corpus_item_id, item_sha256, paper_id, reference_id FROM project_paper_reference WHERE release_import_id=? "
                          "ORDER BY corpus_item_id", (r["source"],))
            cands = kb.all("SELECT corpus_item_id, item_sha256, paper_id, reference_id FROM fte_candidate WHERE run_id=? ORDER BY corpus_item_id",
                           (r["run_id"],))
        self.assertEqual(cands, refs)
        self.assertEqual((run["authority"], run["item_count"], run["screening_run_id"]), ("NON_AUTHORITATIVE", 7, "scr_synthetic_s05"))
        self.assertTrue(json.loads(run["source_verification_json"])["native_verify_export"]["valid"])
        with self.writer() as kb:
            again = FTEService(kb).create_run(source_id=r["source"], export_dir=r["export"], expected_manifest_sha256=r["sem"], rv_verify_json=r["verify"],
                                              protocol_id=r["protocol_id"], roster=ROSTER, label="synthetic FTE run", operator="op.fte")
            self.assertEqual((again["created"], again["code"], again["run_id"]), (False, "DUPLICATE_NOOP", r["run_id"]))
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_candidate")["n"], 7)

    def test_journal_inventory_is_never_an_fte_source(self):
        """IDs: S05-JOURNAL-REFUSED, ADR-05-01. A journal collection version, journal or journal member (S04J discovery evidence, NOT_SCREENED) passed as the FTE source is refused with JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE, also when the id exists in the KB; an unknown id is SOURCE_NOT_A_SEALED_TA_RELEASE; nothing is written."""
        from tests.journal_helpers import SimulatedProviders, pipeline, small_scenario
        r = self.make_run(2)
        with self.writer() as kb:
            out = pipeline(kb, SimulatedProviders(small_scenario(6)), sizes=(3, 3, 3))
            version_id = out["version"]["version_id"]
            member = kb.one("SELECT member_id FROM jh_member WHERE version_id=? LIMIT 1", (version_id,))["member_id"]
            before = kb.one("SELECT count(*) n FROM fte_run")["n"]
            for source in (version_id, out["journal_id"], member, "jcv_fake_collection_version"):
                with self.assertRaises(KBError) as ctx:
                    FTEService(kb).create_run(source_id=source, export_dir=r["export"], expected_manifest_sha256=r["sem"], rv_verify_json=r["verify"],
                                              protocol_id=r["protocol_id"], roster=ROSTER, label="x", operator="op.fte")
                self.assertEqual(ctx.exception.code, "JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE")
            with self.assertRaises(KBError) as ctx:
                FTEService(kb).create_run(source_id="rri_does_not_exist", export_dir=r["export"], expected_manifest_sha256=r["sem"],
                                          rv_verify_json=r["verify"], protocol_id=r["protocol_id"], roster=ROSTER, label="x", operator="op.fte")
            self.assertEqual(ctx.exception.code, "SOURCE_NOT_A_SEALED_TA_RELEASE")
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_run")["n"], before)

    def test_unsealed_unverified_or_unscreened_inputs_are_refused(self):
        """IDs: S05-SRC-UNSEALED, S05-SRC-UNSCREENED, S05-SRC-TAMPER. Refused: no trusted expected digest; a wrong digest; no native verify-export result; a native result that is invalid or for another item count; export bytes that changed after the S03 import; a release without the Stage-10 screening binding (unscreened)."""
        r = self.make_run(3)
        bad_verify = os.path.join(self.tmp, "bad_verify.json")
        Path(bad_verify).write_text(json.dumps({"valid": False, "release_manifest_sha256": r["sem"], "corpus_item_count": 3}), encoding="utf-8")
        wrong_count = os.path.join(self.tmp, "wrong_count.json")
        Path(wrong_count).write_text(json.dumps({"valid": True, "release_manifest_sha256": r["sem"], "corpus_item_count": 4}), encoding="utf-8")
        base = dict(source_id=r["source"], export_dir=r["export"], expected_manifest_sha256=r["sem"], rv_verify_json=r["verify"],
                    protocol_id=r["protocol_id"], roster=ROSTER, label="x", operator="op.fte")
        cases = [({"expected_manifest_sha256": ""}, "EXPECTED_MANIFEST_REQUIRED"), ({"expected_manifest_sha256": "a" * 64}, "EXPECTED_MANIFEST_MISMATCH"),
                 ({"rv_verify_json": ""}, "NATIVE_VERIFY_REQUIRED"), ({"rv_verify_json": bad_verify}, "NATIVE_VERIFY_MISMATCH"),
                 ({"rv_verify_json": wrong_count}, "NATIVE_VERIFY_MISMATCH")]
        for over, code in cases:
            with self.subTest(code=code), self.writer() as kb:
                with self.assertRaises(KBError) as ctx:
                    FTEService(kb).create_run(**dict(base, **over))
                self.assertEqual(ctx.exception.code, code)
        tampered = os.path.join(self.tmp, "tampered")
        shutil.copytree(r["export"], tampered)
        other_items, other_sem, _ = write_ta_release(os.path.join(self.tmp, "other"), 3, release_id="crl_other")
        shutil.rmtree(tampered)
        shutil.copytree(os.path.join(self.tmp, "other"), tampered)
        with self.writer() as kb:
            with self.assertRaises(KBError) as ctx:
                FTEService(kb).create_run(**dict(base, export_dir=tampered))
            self.assertIn(ctx.exception.code, ("EXPECTED_MANIFEST_MISMATCH", "SOURCE_EXPORT_CHANGED"))
        unscreened = os.path.join(self.tmp, "unscreened")
        items = [s03_item(f"cri_u_{i}", dois=[(f"10.99999/u.{i}", f"10.99999/u.{i}")]) for i in range(2)]
        write_export(unscreened, items, release_id="crl_unscreened", release_semantic="b" * 64)
        verify = os.path.join(self.tmp, "u_verify.json")
        Path(verify).write_text(json.dumps({"valid": True, "release_manifest_sha256": "b" * 64, "corpus_item_count": 2}), encoding="utf-8")
        with self.writer() as kb:
            LibraryService(kb).import_release(unscreened, workspace_label="ws2", operator="op.lib")
            src = kb.one("SELECT release_import_id FROM reprovane_release_import WHERE release_manifest_sha256=?", ("b" * 64,))["release_import_id"]
            with self.assertRaises(KBError) as ctx:
                FTEService(kb).create_run(**dict(base, source_id=src, export_dir=unscreened, expected_manifest_sha256="b" * 64, rv_verify_json=verify))
            self.assertEqual(ctx.exception.code, "UNSCREENED_OR_NOT_A_TA_RELEASE")

    def test_frozen_set_cannot_be_extended_or_mixed(self):
        """IDs: S05-SRC-FROZEN, S05-SRC-CROSS-PROJECT, S05-SRC-DUPLICATE. After INPUT_FROZEN no candidate, reviewer or assignment can be added (database triggers), a candidate from another workspace/release is refused, and a duplicate corpus_item_id is impossible (UNIQUE)."""
        a = self.make_run(2, release_id="crl_a", workspace="ws_a")
        b = self.make_run(2, release_id="crl_b", workspace="ws_b")
        with self.writer() as kb:
            ref_b = kb.one("SELECT * FROM project_paper_reference WHERE release_import_id=? LIMIT 1", (b["source"],))
            first = kb.one("SELECT * FROM fte_candidate WHERE run_id=? ORDER BY ordinal LIMIT 1", (a["run_id"],))
            for row in ({"candidate_id": "ftc_x1", "run_id": a["run_id"], "ordinal": 9, "corpus_item_id": ref_b["corpus_item_id"],
                         "reference_id": ref_b["reference_id"], "paper_id": ref_b["paper_id"], "publication_entity_id": "pub_x",
                         "item_sha256": ref_b["item_sha256"]},
                        dict(first, candidate_id="ftc_x2", ordinal=10)):
                with self.assertRaises(KBError):
                    with kb.transaction():
                        kb.insert("fte_candidate", row)
            with self.assertRaises(KBError):
                with kb.transaction():
                    kb.insert("fte_reviewer", {"reviewer_id": "ftv_x", "run_id": a["run_id"], "label": "OPERATOR:late.reviewer", "role": "REVIEWER",
                                               "actor_type": "HUMAN", "conflicts_json": "[]"})
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_candidate WHERE run_id=?", (a["run_id"],))["n"], 2)


class NativeReleaseTests(FTECase):
    def test_native_reprovane_release_with_bridge_evidence_and_doi_echo(self):
        """IDs: S05-NATIVE-RELEASE, S05-C20-DOI-ECHO, S05-PBD-NOT-IDENTITY. On the NATIVE Reprovane 2.0.1 SEALED release (6 SCREEN_INCLUDE items, its own verify-export output): a run is created; a PBD 'Completed' row verified by the S04 bridge but echoing a different DOI leaves the link UNVERIFIED, so attaching it is refused, and the queue shows DOI_ECHO_MISMATCH (C20) and IDENTITY_UNCONFIRMED_ASSET; once a human confirms the link, the same bytes become evidence."""
        from tests.bridge_helpers import pbd_write_results, result_row
        from tests.pdf_fixtures import synthetic_pdf, title_page
        from scivetra_kb.assets.service import AssetService
        from scivetra_kb.bridge.service import BridgeService
        corpus, fulltext = copy_native_fulltext(self.tmp)
        report = json.loads((NATIVE_FT / "generation_report.json").read_text(encoding="utf-8"))
        native = [e for e in report["exports"] if e["label"] == "corpus"][0]["native_cli_output"]
        verify = os.path.join(self.tmp, "native_verify.json")
        Path(verify).write_text(json.dumps(native), encoding="utf-8")
        with self.writer() as kb:
            LibraryService(kb).import_release(corpus, workspace_label="ws", operator="op.a")
            exported = BridgeService(kb).export_batch(source_dir=fulltext, kind="rv-manual-queue", workspace_label="ws",
                                                      out_dir=os.path.join(self.tmp, "batch"), operator="op.b")
        manifest = json.loads(Path(self.tmp, "batch", "bridge_manifest.json").read_text(encoding="utf-8"))
        row = [x for x in manifest["rows"] if x["export_doi"]][0]
        Path(self.tmp, "batch", exported["output_folder"], row["save_as"]).write_bytes(
            synthetic_pdf([title_page(title="Synthetic Work", authors=["First Synthetic"], doi=row["export_doi"])]))
        result = os.path.join(self.tmp, "result.csv")
        Path(result).write_bytes(pbd_write_results([result_row(row, "Completed", doi="10.99999/a-different-doi")]))
        with self.writer() as kb:
            BridgeService(kb).import_results(batch_id=exported["batch_id"], result_csv=result, pdf_folder=os.path.join(self.tmp, "batch", exported["output_folder"]),
                                             operator="op.c")
            source = kb.one("SELECT release_import_id FROM reprovane_release_import")["release_import_id"]
            svc = FTEService(kb)
            pid = svc.create_protocol(PROTOCOL, operator="op.fte")["protocol_id"]
            run = svc.create_run(source_id=source, export_dir=corpus, expected_manifest_sha256=native["release_manifest_sha256"], rv_verify_json=verify,
                                 protocol_id=pid, roster=ROSTER, label="native", operator="op.fte")
            self.assertEqual(run["item_count"], 6)
            cand = kb.one("SELECT * FROM fte_candidate WHERE run_id=? AND paper_id=?", (run["run_id"], row["paper_id"]))
            link = kb.one("SELECT link_id FROM bridge_file_event WHERE bridge_row_id=? AND link_id IS NOT NULL", (row["bridge_row_id"],))["link_id"]
            with self.assertRaises(KBError) as ctx:
                svc.attach_kb_asset(candidate_id=cand["candidate_id"], link_id=link, operator="op.fte")
            self.assertEqual(ctx.exception.code, "EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED")
            q = {e["candidate_id"]: {f["category"] for f in e["flags"]} for e in svc.queue(run["run_id"])}
            self.assertTrue({"DOI_ECHO_MISMATCH", "IDENTITY_UNCONFIRMED_ASSET", "MISSING_FULLTEXT"} <= q[cand["candidate_id"]])
            AssetService(kb).review_link(link_id=link, decision="CONFIRM", operator="op.human", reason="checked title page")
            out = svc.attach_kb_asset(candidate_id=cand["candidate_id"], link_id=link, operator="op.fte")
            self.assertTrue(out["created"])
            self.assertIn("RIGHTS_LOCAL_PROCESS_UNDETERMINED", out["blockers"])
