"""S05 v02 corrective regressions: S05-F01 (external Stage-11B references) and S05-F02 (current evidence validity).

Synthetic data only; Stage-11B exports are either the NATIVE Reprovane 2.0.1 fixture or KB-built synthetic exports
(tests/fte_helpers.write_stage11b_export). No network, no real PDFs.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path
from unittest import mock

from tests.bridge_helpers import NATIVE_FT, copy_native_fulltext
from tests.fte_helpers import FTECase, PROTOCOL, RESTRICTED, RIGHTS, ROSTER, write_stage11b_export
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.errors import KBError
from scivetra_kb.eligibility.handoff import export_run, verify_export, verify_lock
from scivetra_kb.eligibility.service import FTEService
from scivetra_kb.library.common import operator_label
from scivetra_kb.library.service import LibraryService


def art(n):
    return hashlib.sha256(f"synthetic rv artifact {n}".encode()).hexdigest()


class Base(FTECase):
    def code_of(self, fn, **kw):
        with self.assertRaises(KBError) as ctx:
            self.svc_call(fn, **kw)
        return ctx.exception.code

    def state(self, candidate):
        with self.reader() as kb:
            return FTEService(kb).candidate_state(candidate)

    def summary(self, run_id):
        with self.reader() as kb:
            return FTEService(kb).summary(run_id)

    def evidence_rows(self):
        with self.reader() as kb:
            return kb.one("SELECT count(*) n FROM fte_evidence")["n"]


class ExternalReferenceTests(Base):
    def setUp(self):
        super().setUp()
        self.r = self.make_run(6)
        self.c = self.r["candidates"]
        self.items = self.r["items"]

    def export(self, name="s11b", **kw):
        directory = os.path.join(self.tmp, name)
        kw.setdefault("release_id", "crl_fte_synthetic")
        kw.setdefault("release_sem", self.r["sem"])
        kw.setdefault("acquired", {self.c[0]["corpus_item_id"]: art(0)})
        return directory, write_stage11b_export(directory, kw.pop("items", self.items), **kw)

    def attach(self, candidate, **ref):
        return self.svc_call("attach_external", candidate_id=candidate["candidate_id"], artifact=ref, operator="op.f")

    def refused(self, candidate, **ref):
        return self.code_of("attach_external", candidate_id=candidate["candidate_id"], artifact=ref, operator="op.f")

    def test_fabricated_or_self_asserted_references_are_refused(self):
        """IDs: S05-F01-SELF-ASSERTED, S05-F01-BOGUS-RUN, S05-F01-ARBITRARY-DIGEST, S05-F01-FALSE-RIGHTS. The architect's fabricated reference (non-existent run id, a*64 / b*64 digests, PUBLIC_OA_LICENSED claim, export_verified=true) and any reference carrying self-asserted keys (export_verified, digests, version or rights classes) are refused EXTERNAL_SELF_ASSERTION_REFUSED; a reference naming a missing export folder, an arbitrary expected digest or malformed keys is refused; nothing is written."""
        before = self.evidence_rows()
        fabricated = {"fulltext_run_id": "ftr_never_existed", "fulltext_manifest_sha256": "a" * 64, "artifact_sha256": "b" * 64,
                      "corpus_item_id": self.c[0]["corpus_item_id"], "version_class": "PUBLISHED", "rights_class": "PUBLIC_OA_LICENSED", "export_verified": True}
        self.assertEqual(self.refused(self.c[0], **fabricated), "EXTERNAL_SELF_ASSERTION_REFUSED")
        directory, digest = self.export()
        for key, value in (("export_verified", True), ("rights_class", "PUBLIC_OA_LICENSED"), ("version_class", "PUBLISHED"), ("artifact_sha256", art(0))):
            self.assertEqual(self.refused(self.c[0], fulltext_export_dir=directory, expected_manifest_sha256=digest, **{key: value}),
                             "EXTERNAL_SELF_ASSERTION_REFUSED", key)
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=os.path.join(self.tmp, "ftr_never_existed"), expected_manifest_sha256=digest),
                         "EXPORT_NOT_FOUND")
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=directory, expected_manifest_sha256="a" * 64), "EXPECTED_MANIFEST_MISMATCH")
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=directory), "EXTERNAL_REFERENCE_INVALID")
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=directory, expected_manifest_sha256=digest.upper()), "EXTERNAL_REFERENCE_INVALID")
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=directory, expected_manifest_sha256=digest, note="trust me"),
                         "EXTERNAL_REFERENCE_INVALID")
        self.assertEqual(self.evidence_rows(), before)

    def test_wrong_release_item_or_artifact_is_refused(self):
        """IDs: S05-F01-WRONG-RELEASE, S05-F01-WRONG-ITEM, S05-F01-CROSS-ITEM, S05-F01-ARTIFACT-MISMATCH, S05-F01-NOT-ACQUIRED. A verified export of another release (other release id, or same id with another manifest digest), an export without this candidate, an item naming another publication entity, a MANUAL_REQUIRED item, a selected artifact that is not the item's own ACQUIRED attempt, an artifact claimed by two items, or an expected artifact digest the export did not select is refused with a stable code; nothing is written."""
        before = self.evidence_rows()
        cid0, cid1 = self.c[0]["corpus_item_id"], self.c[1]["corpus_item_id"]
        d, g = self.export("other_release", release_id="crl_other", release_sem=hashlib.sha256(b"other").hexdigest())
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_RELEASE_MISMATCH")
        d, g = self.export("same_id_other_digest", release_sem=hashlib.sha256(b"other").hexdigest())
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_RELEASE_MISMATCH")
        d, g = self.export("subset", items=self.items[1:], acquired={cid1: art(1)})
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_ITEM_NOT_IN_EXPORT")

        def other_entity(statuses):
            statuses[0]["publication_entity_id"] = statuses[1]["publication_entity_id"]
        d, g = self.export("entity", mutate=other_entity)
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_ITEM_MISMATCH")
        d, g = self.export("manual")
        self.assertEqual(self.refused(self.c[1], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_ITEM_NOT_ACQUIRED")

        def foreign_attempt(statuses):
            statuses[0]["attempts"][0]["outcome"]["artifact_sha256"] = art(99)
        d, g = self.export("foreign", mutate=foreign_attempt)
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_ARTIFACT_NOT_IN_EXPORT")
        d, g = self.export("shared", acquired={cid0: art(0), cid1: art(0)})
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g), "EXTERNAL_ARTIFACT_SHARED_ACROSS_ITEMS")
        d, g = self.export("good")
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g, expected_artifact_sha256=art(1)),
                         "EXTERNAL_ARTIFACT_MISMATCH")
        self.assertEqual(self.evidence_rows(), before)

    def test_altered_export_files_are_refused(self):
        """IDs: S05-F01-ALTERED-MANIFEST, S05-F01-NATIVE-VERIFY. An export whose bytes differ from SHA256SUMS, whose files were edited and re-summed (no longer the canonical rendering of the manifest), that carries an extra file, or whose native verify-export output names another digest is refused before anything is recorded."""
        before = self.evidence_rows()
        d, g = self.export()
        for name, fix in (("bytes", None), ("resummed", "sums"), ("extra", "extra")):
            target = os.path.join(self.tmp, "alt_" + name)
            shutil.copytree(d, target)
            if fix == "extra":
                Path(target, "notes.txt").write_text("synthetic", encoding="utf-8")
            else:
                items = Path(target, "fulltext_items.jsonl")
                items.write_bytes(items.read_bytes().replace(b'"ACQUIRED_AUTO"', b'"ACQUIRED_AUTO" ', 1))
                if fix == "sums":
                    sums = Path(target, "SHA256SUMS.txt")
                    lines = [line.split("  ") for line in sums.read_text(encoding="utf-8").splitlines()]
                    sums.write_text("".join(f"{hashlib.sha256(Path(target, n).read_bytes()).hexdigest()}  {n}\n" for _, n in lines), encoding="utf-8")
            code = self.refused(self.c[0], fulltext_export_dir=target, expected_manifest_sha256=g)
            self.assertIn(code, {"bytes": {"EXPORT_HASH_MISMATCH"}, "resummed": {"FULLTEXT_EXPORT_INVALID", "EXPORT_MALFORMED_JSON"},
                                 "extra": {"EXPORT_INVENTORY_MISMATCH"}}[name], name)
        verify = os.path.join(self.tmp, "wrong_native_verify.json")
        Path(verify).write_text(json.dumps({"verified": True, "manifest_sha256": "c" * 64, "item_count": 6,
                                            "mode": "PORTABLE_METADATA_ONLY_NO_DB_NO_PDF_BYTES"}), encoding="utf-8")
        self.assertEqual(self.refused(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g, rv_verify_json=verify), "NATIVE_VERIFY_MISMATCH")
        self.assertEqual(self.evidence_rows(), before)

    def test_licence_and_version_claims_never_make_external_evidence_ready(self):
        """IDs: S05-F01-NEVER-READY, S05-F01-FALSE-RIGHTS, S05-F01-VERSION. A verified reference whose provider claims say cc-by / OA stays blocked (RIGHTS_NOT_DETERMINED_EXTERNAL; claims recorded, not adopted); a submitted-version selection adds the protocol version blocker; FT_INCLUDE and FT_EXCLUDE are refused; the reference points to the human-confirmed KB asset with the same bytes, and only that KB asset makes the candidate reviewable."""
        ev = self.add_pdf(self.c[0])
        d, g = self.export(acquired={self.c[0]["corpus_item_id"]: ev["asset_sha256"], self.c[1]["corpus_item_id"]: art(1)}, licence="cc-by")
        out = self.attach(self.c[0], fulltext_export_dir=d, expected_manifest_sha256=g, expected_artifact_sha256=ev["asset_sha256"])
        self.assertFalse(out["evidence_ready"])
        self.assertIn("RIGHTS_NOT_DETERMINED_EXTERNAL", out["blockers"])
        self.assertEqual(out["kb_asset_links_with_same_bytes"], [ev["link_id"]])
        for outcome, reasons in (("FT_INCLUDE", []), ("FT_EXCLUDE", ["WRONG_POPULATION"])):
            self.assertEqual(self.code_of("decide", candidate_id=self.c[0]["candidate_id"], reviewer="rev.a", outcome=outcome, reasons=reasons),
                             "EVIDENCE_NOT_READY")
        d2, g2 = self.export("submitted", acquired={self.c[1]["corpus_item_id"]: art(1)}, version="submittedVersion")
        out2 = self.attach(self.c[1], fulltext_export_dir=d2, expected_manifest_sha256=g2)
        self.assertIn("VERSION_NOT_ALLOWED_BY_PROTOCOL:SUBMITTED", out2["blockers"])
        kb_out = self.svc_call("attach_kb_asset", candidate_id=self.c[0]["candidate_id"], link_id=ev["link_id"], operator="op.f")
        self.assertTrue(kb_out["evidence_ready"])
        self.both(self.c[0], "FT_INCLUDE")
        self.assertEqual(self.state(self.c[0])["outcome"], "FT_INCLUDE")

    def test_v01_self_asserted_ready_row_is_not_counted(self):
        """IDs: S05-F01-LEGACY-ROW. An RV_STAGE11B_ARTIFACT evidence row stored as evidence-ready (only possible from v01 self-assertion) is treated as not current: the candidate is PENDING STALE_EVIDENCE_NOT_CURRENT even with two FT_INCLUDE votes, and new decisions are refused EVIDENCE_NOT_CURRENT."""
        with self.writer() as kb:
            svc = FTEService(kb)
            row = dict(evidence_id=kb.ids.new("fev"), candidate_id=self.c[0]["candidate_id"], kind="RV_STAGE11B_ARTIFACT", asset_sha256=None, link_id=None,
                       document_version_id=None, version_class="PUBLISHED", link_state_at_attach=None, rights_assessment_id=None,
                       rights_class="PUBLIC_OA_LICENSED", external_ref_json=json.dumps({"export_verified": True}), evidence_ready=1, blockers_json="[]",
                       byte_verification="EXTERNAL_HASH_ONLY_NOT_REVERIFIED", evidence_fingerprint="e" * 64, supersedes_evidence_id=None,
                       attached_by="OPERATOR:op.v01", attached_at_utc=kb.now())
            with kb.transaction():
                kb.insert("fte_evidence", row)
                for label in ("rev.a", "rev.b"):
                    rev = kb.one("SELECT reviewer_id FROM fte_reviewer WHERE run_id=? AND label=?", (self.r["run_id"], operator_label(label)))
                    kb.insert("fte_decision", dict(decision_id=kb.ids.new("ftd"), candidate_id=self.c[0]["candidate_id"], reviewer_id=rev["reviewer_id"],
                                                   outcome="FT_INCLUDE", reason_codes_json="[]", note=None, locator_json=None, related_corpus_item_id=None,
                                                   evidence_id=row["evidence_id"], evidence_fingerprint="e" * 64, protocol_sha256=svc.run(self.r["run_id"])["protocol_sha256"],
                                                   item_sha256=self.c[0]["item_sha256"], supersedes_decision_id=None, decided_at_utc=kb.now(),
                                                   decision_sha256=hashlib.sha256(label.encode()).hexdigest()))
        st = self.state(self.c[0])
        self.assertEqual((st["status"], st["pending_reason"]), ("PENDING", "STALE_EVIDENCE_NOT_CURRENT"))
        self.assertEqual(st["evidence_problems"], ["EXTERNAL_READY_WITHOUT_HUMAN_CONFIRMED_KB_BYTES"])
        self.assertEqual(self.summary(self.r["run_id"])["ft_include"], 0)
        self.assertEqual(self.code_of("decide", candidate_id=self.c[0]["candidate_id"], reviewer="rev.a", outcome="FT_UNSURE", note="re-check"),
                         "EVIDENCE_NOT_CURRENT")


class NativeStage11BTests(Base):
    def test_genuine_native_stage11b_export_is_verified_but_not_ready(self):
        """IDs: S05-F01-NATIVE-FIXTURE. On the NATIVE Reprovane 2.0.1 release and its NATIVE Stage-11B export (with Reprovane's own verify-export outputs), the one ACQUIRED_AUTO item's reference verifies at level RV11B_PORTABLE_EXPORT_VERIFIED_WITH_TRUSTED_DIGEST_AND_NATIVE_VERIFY, keeps the provider claims (acceptedVersion, cc-by) as claims, and is not evidence-ready; a MANUAL_REQUIRED item is refused; a byte-edited copy of the native export is refused."""
        corpus, fulltext = copy_native_fulltext(self.tmp)
        report = json.loads((NATIVE_FT / "generation_report.json").read_text(encoding="utf-8"))
        outputs = {e["label"]: e["native_cli_output"] for e in report["exports"]}
        verify_corpus, verify_ft = os.path.join(self.tmp, "native_corpus_verify.json"), os.path.join(self.tmp, "native_ft_verify.json")
        Path(verify_corpus).write_text(json.dumps(outputs["corpus"]), encoding="utf-8")
        Path(verify_ft).write_text(json.dumps(outputs["fulltext"]), encoding="utf-8")
        manifest = json.loads(Path(fulltext, "fulltext_manifest.json").read_text(encoding="utf-8"))
        acquired = [s for s in manifest["items"] if s["status"] == "ACQUIRED_AUTO"]
        self.assertEqual(len(acquired), 1)
        with self.writer() as kb:
            LibraryService(kb).import_release(corpus, workspace_label="ws", operator="op.a")
            source = kb.one("SELECT release_import_id FROM reprovane_release_import")["release_import_id"]
            svc = FTEService(kb)
            pid = svc.create_protocol(PROTOCOL, operator="op.fte")["protocol_id"]
            run = svc.create_run(source_id=source, export_dir=corpus, expected_manifest_sha256=outputs["corpus"]["release_manifest_sha256"],
                                 rv_verify_json=verify_corpus, protocol_id=pid, roster=ROSTER, label="native", operator="op.fte")
            cands = {c["corpus_item_id"]: c for c in svc.candidates(run["run_id"])}
        target = cands[acquired[0]["corpus_item_id"]]
        out = self.svc_call("attach_external", candidate_id=target["candidate_id"], operator="op.f", artifact={
            "fulltext_export_dir": fulltext, "expected_manifest_sha256": outputs["fulltext"]["manifest_sha256"], "rv_verify_json": verify_ft,
            "expected_artifact_sha256": acquired[0]["selected_artifact_sha256"]})
        self.assertTrue(out["created"])
        self.assertFalse(out["evidence_ready"])
        self.assertEqual(out["verification_level"], "RV11B_PORTABLE_EXPORT_VERIFIED_WITH_TRUSTED_DIGEST_AND_NATIVE_VERIFY")
        with self.reader() as kb:
            row = kb.one("SELECT * FROM fte_evidence WHERE evidence_id=?", (out["evidence_id"],))
        ref = json.loads(row["external_ref_json"])
        self.assertEqual((row["version_class"], ref["provider_version_claims"], ref["provider_licence_claims"]), ("ACCEPTED", ["acceptedVersion"], ["cc-by"]))
        self.assertEqual(ref["artifact_sha256"], acquired[0]["selected_artifact_sha256"])
        self.assertIn("PDF bytes (SciVetra holds none and re-hashed nothing)", ref["verification"]["not_verified"])
        manual = next(c for cid, c in cands.items() if cid != target["corpus_item_id"])
        self.assertEqual(self.code_of("attach_external", candidate_id=manual["candidate_id"], operator="op.f", artifact={
            "fulltext_export_dir": fulltext, "expected_manifest_sha256": outputs["fulltext"]["manifest_sha256"]}), "EXTERNAL_ITEM_NOT_ACQUIRED")
        edited = os.path.join(self.tmp, "native-edited")
        shutil.copytree(fulltext, edited)
        p = Path(edited, "fulltext_manifest.json")
        p.write_bytes(p.read_bytes().replace(b"cc-by", b"cc-0x", 1))
        self.assertEqual(self.code_of("attach_external", candidate_id=target["candidate_id"], operator="op.f", artifact={
            "fulltext_export_dir": edited, "expected_manifest_sha256": outputs["fulltext"]["manifest_sha256"]}), "EXPORT_HASH_MISMATCH")


class CurrentEvidenceTests(Base):
    def setUp(self):
        super().setUp()
        self.r = self.make_run(1)
        self.c = self.r["candidates"][0]

    def decide(self, who, outcome="FT_INCLUDE", **kw):
        return self.svc_call("decide", candidate_id=self.c["candidate_id"], reviewer=who, outcome=outcome, **kw)

    def assess(self, ev, evidence):
        with self.writer() as kb:
            AssetService(kb).assess_rights(asset_sha256=ev["asset_sha256"], document_version_id=ev["document_version_id"], operator="op.safety",
                                           evidence=evidence)

    def test_rights_downgrade_invalidates_until_reattach_and_fresh_votes(self):
        """IDs: S05-F02-RIGHTS, S05-F02-RESOLVE, S05-F02-EXPORT, S05-F02-REATTACH, S05-F02-APPEND-ONLY. After two FT_INCLUDE votes and resolve, a rights reassessment to RESTRICTED makes the candidate PENDING STALE_EVIDENCE_NOT_CURRENT at once (summary 0 include / 1 pending, provisional export the same, queue CHANGED_EVIDENCE); resolve and lock refuse; new votes on the stale evidence are refused; the original votes stay unchanged in the journal; re-attaching gives blocked evidence; after rights are cleared again, re-attachment plus two fresh superseding human votes restore FINAL, and the run resolves, locks and verifies."""
        _, ev = self.attach(self.c)
        a, b = self.both(self.c, "FT_INCLUDE")
        self.assertEqual(self.svc_call("resolve", run_id=self.r["run_id"], operator="op.r")["state"], "REVIEW_RESOLVED")
        self.assess(ev, RESTRICTED)
        st = self.state(self.c)
        self.assertEqual((st["status"], st["outcome"], st["pending_reason"]), ("PENDING", None, "STALE_EVIDENCE_NOT_CURRENT"))
        self.assertTrue(any(p.startswith("RIGHTS_REASSESSED") for p in st["evidence_problems"]))
        s = self.summary(self.r["run_id"])
        self.assertEqual((s["ft_include"], s["ft_pending"], s["ft_pending_by_reason"]), (0, 1, {"STALE_EVIDENCE_NOT_CURRENT": 1}))
        with self.reader() as kb:
            flags = {f["category"] for f in FTEService(kb).queue(self.r["run_id"])[0]["flags"]}
        self.assertIn("CHANGED_EVIDENCE", flags)
        self.assertEqual(self.code_of("resolve", run_id=self.r["run_id"], operator="op.r"), "RUN_HAS_PENDING_CANDIDATES")
        self.assertEqual(self.code_of("lock", run_id=self.r["run_id"], operator="op.r"), "LOCK_REFUSED_STALE_OR_UNRESOLVED")
        self.assertEqual(self.code_of("decide", candidate_id=self.c["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE", supersedes=a["decision_id"]),
                         "EVIDENCE_NOT_CURRENT")
        with self.writer() as kb:
            out = export_run(kb, self.r["run_id"], os.path.join(self.tmp, "exp"), operator="op.x")
        folder = os.path.join(self.tmp, "exp", out["folder_name"])
        self.assertTrue(verify_export(folder)["valid"])
        row = json.loads(Path(folder, "candidates.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((row["status"], row["pending_reason"]), ("PENDING", "STALE_EVIDENCE_NOT_CURRENT"))
        self.assertEqual(json.loads(Path(folder, "summary.json").read_text(encoding="utf-8"))["ft_include"], 0)
        with self.reader() as kb:
            journal = [(d["decision_id"], d["decision_sha256"]) for d in kb.all("SELECT * FROM fte_decision ORDER BY rowid")]
        self.assertEqual(journal, [(a["decision_id"], a["decision_sha256"]), (b["decision_id"], b["decision_sha256"])])
        blocked = self.svc_call("attach_kb_asset", candidate_id=self.c["candidate_id"], link_id=ev["link_id"], operator="op.f")
        self.assertTrue(blocked["created"] and blocked["earlier_votes_now_stale"] and not blocked["evidence_ready"])
        self.assertEqual(self.state(self.c)["pending_reason"], "STALE_DECISION_EVIDENCE_CHANGED")
        self.assertEqual(self.code_of("decide", candidate_id=self.c["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE", supersedes=a["decision_id"]),
                         "EVIDENCE_NOT_READY")
        self.assess(ev, RIGHTS["PUBLISHED"])
        self.assertEqual(self.state(self.c)["pending_reason"], "STALE_EVIDENCE_NOT_CURRENT")
        ready = self.svc_call("attach_kb_asset", candidate_id=self.c["candidate_id"], link_id=ev["link_id"], operator="op.f")
        self.assertTrue(ready["evidence_ready"])
        self.assertEqual(self.state(self.c)["pending_reason"], "STALE_DECISION_EVIDENCE_CHANGED")
        self.decide("rev.a", supersedes=a["decision_id"])
        self.assertEqual(self.state(self.c)["status"], "PENDING")
        self.decide("rev.b", supersedes=b["decision_id"])
        st = self.state(self.c)
        self.assertEqual((st["status"], st["outcome"]), ("FINAL", "FT_INCLUDE"))
        self.svc_call("resolve", run_id=self.r["run_id"], operator="op.r")
        lock = self.svc_call("lock", run_id=self.r["run_id"], operator="op.r")
        self.assertEqual(lock["summary"]["ft_include"], 1)
        with self.reader() as kb:
            check = verify_lock(kb, self.r["run_id"])
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_decision")["n"], 4)
        self.assertTrue(check["valid"])
        self.assertEqual(check["current_evidence_drift"]["count"], 0)

    def test_identity_reversal_and_role_change_invalidate_until_reattach(self):
        """IDs: S05-F02-IDENTITY, S05-F02-ROLE, S05-F02-RECONFIRM. After two FT_INCLUDE votes, rejecting the identity link (MISMATCH) makes the candidate PENDING STALE_EVIDENCE_NOT_CURRENT (IDENTITY_NOW_MISMATCH) and re-attaching is refused; a later human re-confirmation does not silently revive the old votes (still PENDING until re-attached); re-attachment plus fresh votes restore FINAL. Re-marking the link as supplementary (role change) also invalidates."""
        _, ev = self.attach(self.c)
        a, b = self.both(self.c, "FT_INCLUDE")
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=ev["link_id"], decision="REJECT", operator="op.safety", reason="identity corrected")
        st = self.state(self.c)
        self.assertEqual((st["status"], st["evidence_problems"]), ("PENDING", ["IDENTITY_NOW_MISMATCH"]))
        self.assertEqual(self.summary(self.r["run_id"])["ft_include"], 0)
        self.assertEqual(self.code_of("attach_kb_asset", candidate_id=self.c["candidate_id"], link_id=ev["link_id"], operator="op.f"),
                         "EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED")
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=ev["link_id"], decision="CONFIRM", operator="op.safety", reason="re-checked: same paper after all")
        st = self.state(self.c)
        self.assertEqual((st["status"], st["evidence_problems"]), ("PENDING", ["IDENTITY_REVIEWED_AGAIN_OR_FACTS_CHANGED"]))
        out = self.svc_call("attach_kb_asset", candidate_id=self.c["candidate_id"], link_id=ev["link_id"], operator="op.f")
        self.assertTrue(out["created"] and out["earlier_votes_now_stale"])
        self.decide("rev.a", supersedes=a["decision_id"])
        self.decide("rev.b", supersedes=b["decision_id"])
        self.assertEqual(self.state(self.c)["status"], "FINAL")
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=ev["link_id"], decision="MARK_SUPPLEMENT", operator="op.safety", reason="this is the supplement")
        self.assertEqual(self.state(self.c)["evidence_problems"], ["ROLE_NOW_SUPPLEMENTARY"])

    def test_locked_snapshot_stays_historical_and_drift_is_reported_separately(self):
        """IDs: S05-F02-LOCKED-HISTORICAL, S05-F02-DRIFT. A rights reassessment after LOCKED_FOR_HANDOFF does not rewrite the locked snapshot: the locked projection, the CAS manifest bytes and verify_lock stay valid, while summary.post_lock_drift and verify_lock.current_evidence_drift report the candidate separately; the locked export still verifies offline."""
        _, ev = self.attach(self.c)
        self.both(self.c, "FT_INCLUDE")
        self.svc_call("resolve", run_id=self.r["run_id"], operator="op.r")
        lock = self.svc_call("lock", run_id=self.r["run_id"], operator="op.r")
        with self.reader() as kb:
            before = kb.cas.read(lock["handoff_manifest_sha256"])
        self.assess(ev, RESTRICTED)
        self.assertEqual(self.state(self.c)["status"], "FINAL")
        s = self.summary(self.r["run_id"])
        self.assertEqual((s["status"], s["ft_include"], s["post_lock_drift"]["count"]), ("LOCKED_FOR_HANDOFF_STAGING_SNAPSHOT", 1, 1))
        with self.reader() as kb:
            check = verify_lock(kb, self.r["run_id"])
            self.assertEqual(kb.cas.read(lock["handoff_manifest_sha256"]), before)
        self.assertTrue(check["valid"])
        self.assertEqual(check["current_evidence_drift"]["items"][0]["corpus_item_id"], self.c["corpus_item_id"])
        with self.writer() as kb:
            out = export_run(kb, self.r["run_id"], os.path.join(self.tmp, "exp"), operator="op.x")
        folder = os.path.join(self.tmp, "exp", out["folder_name"])
        self.assertTrue(verify_export(folder)["valid"])
        self.assertEqual(Path(folder, "handoff_manifest.json").read_bytes(), before)
        self.assertEqual(json.loads(Path(folder, "summary.json").read_text(encoding="utf-8"))["post_lock_drift"]["count"], 1)
        self.assertEqual(self.code_of("decide", candidate_id=self.c["candidate_id"], reviewer="rev.a", outcome="FT_UNSURE", note="x"), "RUN_LOCKED")

    def test_projections_do_not_read_cas_bytes_but_lock_does(self):
        """IDs: S05-F02-NO-CAS-SCAN, S05-F02-LOCK-CAS. candidate_state, summary and queue run with CAS reads disabled (database-only currency check); with the evidence bytes removed from CAS the projection stays FINAL, while the lock gate's full re-hash still refuses."""
        _, ev = self.attach(self.c)
        self.both(self.c, "FT_INCLUDE")
        with self.reader() as kb:
            cas_type = type(kb.cas)
            with mock.patch.object(cas_type, "verify_object", side_effect=AssertionError("CAS read")), \
                    mock.patch.object(cas_type, "read", side_effect=AssertionError("CAS read")):
                svc = FTEService(kb)
                self.assertEqual(svc.candidate_state(self.c)["status"], "FINAL")
                self.assertEqual(svc.summary(self.r["run_id"])["ft_include"], 1)
                svc.queue(self.r["run_id"])
        Path(self.root, "cas", "sha256", ev["asset_sha256"][:2], ev["asset_sha256"]).unlink()
        self.assertEqual(self.state(self.c)["status"], "FINAL")
        self.svc_call("resolve", run_id=self.r["run_id"], operator="op.r")
        self.assertEqual(self.code_of("lock", run_id=self.r["run_id"], operator="op.r"), "LOCK_REFUSED_STALE_OR_UNRESOLVED")
