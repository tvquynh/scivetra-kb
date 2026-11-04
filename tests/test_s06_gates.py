"""S06: rights, identity and asset gates before any byte is opened; read/export re-check CURRENT rights; no S03/S05 mutation."""
import json
import os
import sqlite3
import unittest
from pathlib import Path

from tests import s06_pdf_fixtures as F
from tests.s06_helpers import OPEN_RIGHTS, S06Case
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.errors import PolicyRefusal, ValidationError
from scivetra_kb.core.manifest import table_content_sha256


def insert_assessment(kb, asset, dv, local_process, prior=None):
    """An operator-recorded assessment row with an explicit local_process value (e.g. an owner DENY decision)."""
    row = dict(assessment_id=kb.ids.new("rga"), asset_sha256=asset, document_version_id=dv, rights_class="UNKNOWN", store_locally="ALLOW",
               redistribute="DENY", local_process=local_process, external_ai_process="DENY", license_evidence_json="[]", terms_declarations_json="[]",
               access_provenance_json="{}", flags_json="[]", policy_version="rights-policy/1", assessed_by="OPERATOR:owner", recorded_by="OPERATOR:owner",
               owner_approval_ref=None, supersedes_assessment_id=prior, assessed_at_utc=kb.now(), semantic_sha256=kb.ids.new("x").ljust(64, "0")[:64])
    kb.insert("rights_assessment", row)
    return row["assessment_id"]


class Gates(S06Case):
    def doc(self, i=0, extra="Body."):
        return F.build_pdf([self.title_page(i, [extra])], syntax="object_stream")

    def assert_refused(self, out, code):
        self.assertEqual(out["outcome"], "REFUSED", out)
        self.assertIn(code, out["reasons"])
        self.assertFalse(out["bytes_opened"])
        self.assertIsNone(out["artifact_id"])

    def test_rights_gate_refuses_without_derived_content(self):
        """IDs: S06-RIGHTS-MISSING, S06-RIGHTS-UNDETERMINED, S06-RIGHTS-DENY, S06-NO-ARTIFACT-AFTER-REFUSAL. Intake-default UNKNOWN rights
        (local_process UNDETERMINED), an INSTITUTIONAL_RESTRICTED assessment and an explicit DENY all refuse before any byte is opened; the refusal is
        audited and no artifact, no CAS object and no page text exist."""
        unknown = self.register("u.pdf", self.doc(0), rights=None)
        restricted = self.register("r.pdf", self.doc(1), paper=1, rights="RESTRICTED")
        denied = self.register("d.pdf", self.doc(2), paper=2)
        with self.writer() as kb:
            with kb.transaction():
                insert_assessment(kb, denied["asset_sha256"], denied["document_version_id"], "DENY")
        cas_before = self.cas_names()
        self.assert_refused(self.parse(unknown["asset_sha256"], unknown["link_id"]), "RIGHTS_LOCAL_PROCESS_UNDETERMINED")
        self.assert_refused(self.parse(restricted["asset_sha256"], restricted["link_id"]), "RIGHTS_LOCAL_PROCESS_UNDETERMINED")
        self.assert_refused(self.parse(denied["asset_sha256"], denied["link_id"]), "RIGHTS_LOCAL_PROCESS_DENY")
        self.assert_refused(self.parse(denied["asset_sha256"], denied["link_id"], "IDENTITY_INSPECTION"), "RIGHTS_LOCAL_PROCESS_DENY")
        self.assertEqual((self.count("parse_artifact"), self.count("parse_run")), (0, 4))
        self.assertEqual(self.cas_names(), cas_before)
        with self.reader() as kb:
            runs = kb.all("SELECT * FROM parse_run")
        self.assertTrue(all(r["outcome"] == "REFUSED" and r["artifact_id"] is None for r in runs))

    def test_identity_gates_and_inspection_purpose(self):
        """IDs: S06-IDENTITY-UNVERIFIED, S06-IDENTITY-MISMATCH, S06-IDENTITY-INSPECTION, S06-ROLE-MAIN-TEXT. A link without human confirmation is
        refused for RESEARCH_PARSE but may be parsed for IDENTITY_INSPECTION, whose output is NOT_ELIGIBLE_FOR_EVIDENCE; a MISMATCH (rejected) link is
        refused for both purposes; a supplementary link is refused for research parsing. Identity is never auto-confirmed by parsing."""
        unconfirmed = self.register("a.pdf", self.doc(0), confirm=False)
        self.assertNotEqual(unconfirmed["identity_state"], "HUMAN_CONFIRMED")
        self.assert_refused(self.parse(unconfirmed["asset_sha256"], unconfirmed["link_id"]), "IDENTITY_NOT_HUMAN_CONFIRMED")
        inspected = self.parse(unconfirmed["asset_sha256"], unconfirmed["link_id"], "IDENTITY_INSPECTION")
        self.assertEqual((inspected["outcome"], inspected["evidence_eligibility"]), ("PRODUCED", "NOT_ELIGIBLE_FOR_EVIDENCE"))
        with self.reader() as kb:
            access = self.svc(kb).show(artifact_id=inspected["artifact_id"])["access"]
            state = kb.one("SELECT effective_state FROM paper_asset_link_effective WHERE link_id=?", (unconfirmed["link_id"],))["effective_state"]
        self.assertFalse(access["evidence_eligible_now"])
        self.assertEqual(state, unconfirmed["identity_state"])          # parsing changed no identity state
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=unconfirmed["link_id"], decision="REJECT", operator="op.lib", reason="different paper")
        for purpose in ("RESEARCH_PARSE", "IDENTITY_INSPECTION"):
            self.assert_refused(self.parse(unconfirmed["asset_sha256"], unconfirmed["link_id"], purpose), "IDENTITY_MISMATCH")
        supp = self.register("s.pdf", self.doc(1), paper=1, role="SUPPLEMENTARY")
        self.assert_refused(self.parse(supp["asset_sha256"], supp["link_id"]), "LINK_ROLE_NOT_MAIN_TEXT")

    def test_external_and_foreign_references_cannot_bypass(self):
        """IDs: S06-NO-BYPASS-EXTERNAL, S06-NO-BYPASS-PBD. A digest that is not a KB pdf_asset (e.g. a Reprovane Stage-11B hash-only reference or a
        PaperBatchDownloader 'Completed' file never imported) is refused ASSET_NOT_IN_KB; a link of another asset is refused LINK_NOT_FOR_ASSET."""
        a = self.register("a.pdf", self.doc(0))
        b = self.register("b.pdf", self.doc(1), paper=1)
        self.assert_refused(self.parse("ab" * 32, a["link_id"]), "ASSET_NOT_IN_KB")
        self.assert_refused(self.parse(a["asset_sha256"], b["link_id"]), "LINK_NOT_FOR_ASSET")
        self.assert_refused(self.parse(a["asset_sha256"], "pal_does_not_exist"), "LINK_NOT_FOR_ASSET")

    def test_sql_triggers_are_defense_in_depth(self):
        """IDs: S06-SQL-GATES. Direct inserts of a PRODUCED run without a current local_process=ALLOW assessment, or a research run on an
        unconfirmed link, are refused by migration-0006 triggers."""
        a = self.register("a.pdf", self.doc(0), rights=None)
        b = self.register("b.pdf", self.doc(1), paper=1, confirm=False)
        produced = self.parse(b["asset_sha256"], b["link_id"], "IDENTITY_INSPECTION")
        with self.writer() as kb:
            undetermined = kb.one("SELECT assessment_id FROM rights_assessment WHERE asset_sha256=?", (a["asset_sha256"],))["assessment_id"]
            allow_b = kb.one("SELECT assessment_id FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC", (b["asset_sha256"],))["assessment_id"]
            base = dict(purpose="RESEARCH_PARSE", outcome="PRODUCED", reason_codes_json="[]", evidence_eligibility="ELIGIBLE_WHILE_CURRENT",
                        rights_gate_json="{}", sandbox_json="{}", actor="OPERATOR:x", client_version="t", created_at_utc=kb.now())
            for i, (asset, link, dv, assessment, artifact) in enumerate([
                    (a["asset_sha256"], a["link_id"], a["document_version_id"], undetermined, produced["artifact_id"]),
                    (b["asset_sha256"], b["link_id"], b["document_version_id"], allow_b, produced["artifact_id"])]):
                row = dict(base, run_id=f"pzr_x{i}", requested_asset_sha256=asset, link_id=link, document_version_id=dv, rights_assessment_id=assessment,
                           artifact_id=artifact, semantic_sha256=str(i) * 64)
                with self.assertRaises(ValidationError):
                    with kb.transaction():
                        kb.insert("parse_run", row)

    def test_revoked_rights_withhold_text_on_read_and_export(self):
        """IDs: S06-REVOKE-READ, S06-REVOKE-EXPORT, S06-EXPORT-METADATA-DEFAULT, S06-EXPORT-AUDIT. After a research parse, a later DENY assessment
        makes show withhold the text (stale_derived_content) and refuses + audits a local text export, while the immutable artifact and its CAS bytes
        stay verifiable; the default export is metadata-only (no page text in any file). A fresh ALLOW assessment makes the text readable again."""
        a = self.register("a.pdf", self.doc(0, "Sentinel derived sentence XYZZY."))
        parsed = self.parse(a["asset_sha256"], a["link_id"])
        out_dir = os.path.join(self.tmp, "exports")
        with self.writer() as kb:
            svc = self.svc(kb)
            meta = svc.export(artifact_id=parsed["artifact_id"], out_dir=out_dir, operator="op.x")
            self.assertFalse(meta["contains_text"])
            with self.assertRaises(ValidationError):
                svc.export(artifact_id=parsed["artifact_id"], out_dir=out_dir, operator="op.x", include_text=True)       # reason required
            text = svc.export(artifact_id=parsed["artifact_id"], out_dir=out_dir, operator="op.x", include_text=True, reason="local reading")
            self.assertTrue(text["contains_text"])
        folder = os.path.join(out_dir, meta["folder_name"])
        for name in os.listdir(folder):
            self.assertNotIn(b"XYZZY", Path(folder, name).read_bytes())
        self.assertIn(b"XYZZY", Path(out_dir, text["folder_name"], "page-0001.txt").read_bytes())
        with self.writer() as kb:
            with kb.transaction():
                insert_assessment(kb, a["asset_sha256"], a["document_version_id"], "DENY")
        with self.writer() as kb:
            svc = self.svc(kb)
            shown = svc.show(artifact_id=parsed["artifact_id"], with_text=True)
            self.assertTrue(shown["access"]["stale_derived_content"])
            self.assertFalse(shown["access"]["evidence_eligible_now"])
            self.assertNotIn("text", shown["pages"][0])
            self.assertTrue(shown["text_withheld"].startswith("DERIVED_CONTENT_WITHHELD:RIGHTS_LOCAL_PROCESS_DENY"))
            with self.assertRaises(PolicyRefusal) as ctx:
                svc.export(artifact_id=parsed["artifact_id"], out_dir=os.path.join(self.tmp, "x2"), operator="op.x", include_text=True, reason="r")
            self.assertEqual(ctx.exception.code, "TEXT_EXPORT_REFUSED")
            self.assertTrue(svc.verify(artifact_id=parsed["artifact_id"])["valid"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x2")))
        with self.reader() as kb:
            events = [(r["state"], json.loads(r["reason_codes_json"])) for r in kb.all("SELECT * FROM parse_export_event ORDER BY rowid")]
        self.assertEqual(events[:2], [("AUTHORIZED", []), ("COMPLETED", [])])       # S06 v02 (F02): authorization precedes completion
        self.assertEqual(events[2][0], "REFUSED")
        with self.writer() as kb:
            AssetService(kb).assess_rights(asset_sha256=a["asset_sha256"], document_version_id=a["document_version_id"], operator="op.lib",
                                           evidence=dict(OPEN_RIGHTS, availability_evidence=[dict(OPEN_RIGHTS["availability_evidence"][0], claim_ref="synthetic:oa2")]))
        with self.reader() as kb:
            again = self.svc(kb).show(artifact_id=parsed["artifact_id"], with_text=True)
        self.assertIn("XYZZY", again["pages"][0]["text"])

    def test_identity_reversal_after_parse_is_reported_not_rewritten(self):
        """IDs: S06-IDENTITY-REVERSAL. Rejecting the link after a research parse makes evidence_eligible_now false (IDENTITY_NOW_MISMATCH) without
        changing the artifact or the historical run row."""
        a = self.register("a.pdf", self.doc(0))
        parsed = self.parse(a["asset_sha256"], a["link_id"])
        self.assertEqual(parsed["evidence_eligibility"], "ELIGIBLE_WHILE_CURRENT")
        with self.reader() as kb:
            self.assertTrue(self.svc(kb).show(artifact_id=parsed["artifact_id"])["access"]["evidence_eligible_now"])
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=a["link_id"], decision="REJECT", operator="op.lib", reason="wrong paper")
        with self.reader() as kb:
            access = self.svc(kb).show(artifact_id=parsed["artifact_id"])["access"]
            run = kb.one("SELECT evidence_eligibility FROM parse_run WHERE run_id=?", (parsed["run_id"],))
        self.assertFalse(access["evidence_eligible_now"])
        self.assertIn("IDENTITY_NOW_MISMATCH", access["problems"])
        self.assertEqual(run["evidence_eligibility"], "ELIGIBLE_WHILE_CURRENT")

    def test_parsing_mutates_no_prior_table(self):
        """IDs: S06-NO-S05-MUTATION, S06-DATAFLOW. Parsing (produce, reuse, refuse, fail, show, verify, metadata export) leaves every pre-S06 table
        (S02-S05: identity, links, link events, rights, FTE) byte-identical; parsed text never appears outside parse_artifact's CAS object."""
        a = self.register("a.pdf", self.doc(0, "Sentinel QWERTYQ text."))
        u = self.register("u.pdf", self.doc(1), paper=1, rights=None)
        con = sqlite3.connect(os.path.join(self.root, "db", "kb.sqlite"))
        prior = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'parse_%'")]
        before = {t: table_content_sha256(con, t) for t in prior}
        con.close()
        p = self.parse(a["asset_sha256"], a["link_id"])
        self.parse(a["asset_sha256"], a["link_id"])
        self.parse(u["asset_sha256"], u["link_id"])
        self.parse(a["asset_sha256"], a["link_id"], limits={"max_pages": 1, "max_chars_per_page": 5})
        with self.writer() as kb:
            svc = self.svc(kb)
            svc.show(artifact_id=p["artifact_id"], with_text=True)
            svc.verify(artifact_id=p["artifact_id"])
            svc.export(artifact_id=p["artifact_id"], out_dir=os.path.join(self.tmp, "e"), operator="op.x")
        con = sqlite3.connect(os.path.join(self.root, "db", "kb.sqlite"))
        try:
            self.assertEqual({t: table_content_sha256(con, t) for t in prior}, before)
            dump = "\n".join(con.iterdump())
        finally:
            con.close()
        self.assertNotIn("QWERTYQ", dump)


if __name__ == "__main__":
    unittest.main()
