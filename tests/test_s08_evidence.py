"""S08 evidence extraction: scope binding, typed drafts, verification, staleness, human review, adjudication, export."""
import json
import os
import unittest

from tests.s08_helpers import CI_DOC, MALWARE_DOC, S08Case
from tests.test_s06_gates import insert_assessment
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.errors import KBError
from scivetra_kb.evidence.profiles import builtin
from scivetra_kb.parse.service import ParseService


def by_class(drafts, cls):
    return [d for d in drafts if d["evidence_class"] == cls]


class ExtractionFlow(S08Case):
    def test_end_to_end_typed_drafts_with_provenance(self):
        """IDs: S08-SCOPE-FTE-BASIS, S08-PACK-DETERMINISTIC, S08-PACK-UNTRUSTED, S08-DRAFT-TYPED, S08-DRAFT-PROVENANCE, S08-ABSENCE-EXPLICIT, S08-VERIFY-STRUCTURAL, S08-REVIEW-ACCEPT, S08-NOT-AUTHORITATIVE, S08-RETRY-IDEMPOTENT. One FINAL FT_INCLUDE candidate with its S05-attached, S06-parsed PDF: the pack is deterministic, untrusted-marked and bound to paper/version/asset/artifact/profile; the mock yields typed drafts (fixed-FPR TPR with unit and operating point, ROC-AUC as a ratio, sample size from a thousands-grouped number) with exact anchors and full model/template/pack provenance; an unreported interval is NOT_REPORTED with no values (ABSENCE_RECORDED, never zero); every value verifies structurally (semantic entailment NOT_ASSESSED); one human acceptance makes it accepted KB evidence that is still not authoritative corpus, RAG or synthesis input; repeating packs and the mock is a no-op."""
        run, items, scope, res = self.mocked([[MALWARE_DOC]])
        self.assertEqual(res["results"][0]["status"], "OK")
        again = self.ecall("build_packs", scope_id=scope, operator="op.owner")
        self.assertEqual(again["counts"]["PACK_EXISTS"], 1)
        self.assertEqual(self.ecall("run_mock", scope_id=scope, operator="op.runner")["results"][0]["code"], "DUPLICATE_RUN_NOOP")
        with self.reader() as kb:
            prow = kb.one("SELECT * FROM ev_input_pack WHERE scope_id=?", (scope,))
            pack = json.loads(kb.cas.read(prow["pack_sha256"]))
            erun = kb.one("SELECT * FROM ev_extraction_run WHERE scope_id=?", (scope,))
        self.assertEqual(pack["content_trust"], "UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS")
        self.assertEqual((prow["document_version_id"], prow["asset_sha256"], prow["parse_artifact_id"]),
                         (items[0]["document_version_id"], items[0]["asset_sha256"], items[0]["artifact_id"]))
        self.assertIn("NO_TABLE_CELLS", pack["limits"])
        self.assertEqual((erun["model_identity"], erun["model_location"], erun["input_pack_sha256"]), ("MOCK_DETERMINISTIC", "LOCAL_PROCESS", prow["pack_sha256"]))
        self.assertEqual(json.loads(erun["model_json"])["model_id"], "rkb-mock-evidence-extractor")
        drafts = self.drafts(scope)
        tpr = next(json.loads(d["typed_fields_json"]) for d in by_class(drafts, "NUMERICAL_RESULT") if "TPR" in d["typed_fields_json"])
        self.assertEqual(tpr, {"dataset": "EMBER", "dataset_year": "2018", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "metric": "TPR",
                               "pe_type": "WIN32", "split": "TEST", "unit": "%", "value": "93.1"})
        roc = next(json.loads(d["typed_fields_json"]) for d in by_class(drafts, "NUMERICAL_RESULT") if "ROC" in d["typed_fields_json"])
        self.assertEqual((roc["value"], roc["unit"]), ("0.981", "ratio"))
        self.assertEqual(json.loads(by_class(drafts, "POPULATION_SAMPLE")[0]["typed_fields_json"])["sample_size"], "200000")
        absent = by_class(drafts, "UNCERTAINTY_INTERVAL")[0]
        self.assertEqual((absent["value_status"], absent["typed_fields_json"], absent["quote_storage"]), ("NOT_REPORTED", "{}", "NO_QUOTE"))
        for d in drafts:
            self.assertEqual((d["document_version_id"], d["artifact_sha256"], d["authority"], d["review_gate"]),
                             (items[0]["document_version_id"], prow["artifact_sha256"], "NON_AUTHORITATIVE", "HUMAN_REQUIRED"))
            v = self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
            self.assertEqual(v["state"], "ABSENCE_RECORDED" if d is absent else "STRUCTURALLY_VERIFIED", (d["evidence_class"], v))
            self.assertEqual(v["semantic_entailment"], "NOT_ASSESSED")
        for a in json.loads(drafts[0]["anchors_json"]):
            with self.reader() as kb:
                self.assertTrue(ParseService(kb).verify_anchor(artifact_id=items[0]["artifact_id"], page=a["page"], start=a["char_start"], end=a["char_end"],
                                                               text_sha256=a["quote_sha256"])["valid"])
        self.roster()
        target = by_class(drafts, "NUMERICAL_RESULT")[0]["draft_id"]
        shown = self.ecall("reveal", draft_id=target, reviewer="rev.one")
        self.assertEqual(shown["quote_disclosure"], "SHOWN")
        out = self.ecall("review", draft_id=target, reviewer="rev.one", decision="ACCEPT")
        st = out["state"]
        self.assertEqual((st["current_state"], st["counts_as_accepted_evidence"]), ("HUMAN_ACCEPTED", True))
        self.assertEqual((st["authoritative_corpus"], st["synthesis_eligible"], st["rag_index_eligible"]), (False, False, False))
        accepted_absence = absent["draft_id"]
        self.ecall("reveal", draft_id=accepted_absence, reviewer="rev.one")
        self.assertEqual(self.ecall("review", draft_id=accepted_absence, reviewer="rev.one", decision="ACCEPT")["state"]["value_status"], "NOT_REPORTED")
        self.assertEqual(self.eread("scope_summary", scope_id=scope)["accepted_evidence"], 2)

    def test_scope_basis_eligibility_and_identity_binding(self):
        """IDs: S08-SCOPE-NOT-INVENTORY, S08-SCOPE-ELIGIBILITY-FINAL, S08-SCOPE-EXCLUDED-REFUSED, S08-PROVISIONAL, S08-SUPPLEMENT-NOT-SUBSTITUTED, S08-ASSET-COLLISION, S08-WRONG-PAPER-ARTIFACT, S08-MODEL-LOCATION. Only candidates of one S05 run with their own S06 RESEARCH_PARSE artifact (the S05-attached MAIN_TEXT link) qualify; a PENDING candidate is refused unless the scope is PROVISIONAL_NON_AUTHORITATIVE (reason required), whose drafts can never be accepted; an FT_EXCLUDE candidate, another paper's artifact, a supplementary PDF and an asset whose bytes are also linked to another document version are refused (quarantined for human action until that link is rejected); a non-local model location is refused."""
        docs = [[MALWARE_DOC]] * 4
        run = self.make_run(4)
        c = run["candidates"]
        items = []
        for i, cand in enumerate(c):
            pdf = self.ev_pdf(cand, [MALWARE_DOC + [f"Synthetic paper {i}."]], name=f"s{i}.pdf")
            self.svc_call("attach_kb_asset", candidate_id=cand["candidate_id"], link_id=pdf["link_id"], operator="op.fte")
            items.append(dict(pdf, candidate=cand))
        self.both(c[0], "FT_INCLUDE")
        self.both(c[1], "FT_EXCLUDE", reasons=["WRONG_POPULATION"])
        self.both(c[3], "FT_INCLUDE")
        profile = self.profile()
        member = lambda cand, art: {"candidate_id": cand["candidate_id"], "parse_artifact_id": art}

        def refused(members, code, **kw):
            with self.assertRaises(KBError) as ctx:
                self.ecall("create_scope", fte_run_id=run["run_id"], profile_id=profile, operator="op.owner", label="x", members=members, **kw)
            self.assertIn(code, str(ctx.exception) + ctx.exception.code)
        refused([member(c[2], items[2]["artifact_id"])], "ELIGIBILITY_NOT_FINAL")
        refused([member(c[1], items[1]["artifact_id"])], "MEMBER_NOT_FT_INCLUDE:FT_EXCLUDE")
        refused([member(c[0], items[3]["artifact_id"])], "ARTIFACT_NOT_FOR_CANDIDATE")
        refused([member(c[0], items[0]["artifact_id"])], "MODEL_LOCATION_NOT_ALLOWED", model_location="REMOTE_API")
        refused([member(c[2], items[2]["artifact_id"])], "REASON_REQUIRED", eligibility_mode="PROVISIONAL_NON_AUTHORITATIVE")
        supp = self.ev_pdf(c[3], [["Supplementary tables for the synthetic study."]], name="supp.pdf", role="SUPPLEMENTARY")
        self.assertEqual((supp["outcome"], supp["artifact_id"]), ("REFUSED", None))       # S06: a supplement is never research text
        other = self.ev_pdf(c[3], [MALWARE_DOC + ["An accepted manuscript version."]], name="am.pdf", version="ACCEPTED")
        refused([member(c[3], other["artifact_id"])], "MEMBER_LINK_NOT_FTE_EVIDENCE")       # not the version humans screened
        # the same bytes registered under a second document version of paper 0: quarantined until a human rejects that link
        with self.writer() as kb:
            svc = AssetService(kb)
            from tests.fte_helpers import VERSION_EVIDENCE
            dv2 = svc.add_document_version(paper_id=c[0]["paper_id"], version_class="ACCEPTED", evidence=VERSION_EVIDENCE["ACCEPTED"], operator="op.lib")
            dup = svc.register_asset(fixture_root=self.fixtures, relative_path="s0.pdf", document_version_id=dv2["document_version_id"], operator="op.lib")
        refused([member(c[0], items[0]["artifact_id"])], "ASSET_IDENTITY_COLLISION")
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=dup["link_id"], decision="REJECT", operator="op.lib", reason="same bytes, not the accepted version")
        ok = self.ecall("create_scope", fte_run_id=run["run_id"], profile_id=profile, operator="op.owner", label="final only",
                        members=[member(c[0], items[0]["artifact_id"])], review_policy="SINGLE_HUMAN", review_policy_reason="test")
        self.assertEqual(ok["member_classes"], ["FT_INCLUDE_KB_STAGING"])
        prov = self.ecall("create_scope", fte_run_id=run["run_id"], profile_id=profile, operator="op.owner", label="provisional",
                          members=[member(c[2], items[2]["artifact_id"])], eligibility_mode="PROVISIONAL_NON_AUTHORITATIVE",
                          provisional_reason="preliminary extraction before the FT decision", review_policy="SINGLE_HUMAN", review_policy_reason="test")
        self.assertEqual(prov["member_classes"], ["PROVISIONAL_NON_AUTHORITATIVE"])
        self.ecall("build_packs", scope_id=prov["scope_id"], operator="op.owner")
        self.ecall("run_mock", scope_id=prov["scope_id"], operator="op.runner")
        d = self.drafts(prov["scope_id"])[0]
        self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
        self.roster("rev.one")
        self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "PROVISIONAL_NOT_PROMOTABLE")
        st = self.eread("state", draft_id=d["draft_id"])
        self.assertTrue(st["provisional"])
        self.assertFalse(st["counts_as_accepted_evidence"])
        self.both(c[2], "FT_EXCLUDE", reasons=["WRONG_POPULATION"])
        self.assertIn("STALE:ELIGIBILITY_NOW_FT_EXCLUDE", self.eread("state", draft_id=d["draft_id"])["stale_reasons"])
        del docs

    def test_rights_identity_parse_and_eligibility_changes_make_drafts_stale(self):
        """IDs: S08-RIGHTS-REVOKED-BLOCKED, S08-IDENTITY-CHANGE-BLOCKED, S08-STALE-ARTIFACT-SUPERSEDED, S08-STALE-ELIGIBILITY, S08-HISTORY-KEPT, S08-QUOTE-WITHHELD, S08-NO-EXPOSURE-AFTER-REVOCATION. After acceptance, an owner DENY of local processing makes the draft BLOCKED (not counted as accepted), re-verification STALE, a reveal withholds the quote, ACCEPT is refused and the mock is SCOPE_DENIED; a newer parse artifact of the same asset, a rejected link and a changed S05 outcome each make other drafts STALE/BLOCKED. Every stored row remains."""
        run, items = self.ev_world([[MALWARE_DOC]] * 3)
        scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        self.ecall("run_mock", scope_id=scope, operator="op.runner")
        self.roster("rev.one")
        firsts = {}
        for d in self.drafts(scope):
            if d["paper_id"] not in firsts and d["evidence_class"] == "NUMERICAL_RESULT":
                firsts[d["paper_id"]] = d["draft_id"]
                self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
                self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
                self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT")
        by_paper = {it["candidate"]["paper_id"]: it for it in items}
        p0, p1, p2 = sorted(firsts)
        with self.reader() as kb:
            before = kb.one("SELECT count(*) n FROM ev_draft")["n"], kb.one("SELECT count(*) n FROM ev_review")["n"]
        with self.writer() as kb:
            insert_assessment(kb, by_paper[p0]["asset_sha256"], by_paper[p0]["document_version_id"], "DENY")
        st = self.eread("state", draft_id=firsts[p0])
        self.assertEqual((st["current_state"], st["counts_as_accepted_evidence"]), ("BLOCKED", False))
        self.assertTrue(any(r.startswith("BLOCKED:RIGHTS_") for r in st["stale_reasons"]))
        self.assertEqual(self.ecall("verify", draft_id=firsts[p0], operator="op.v")["state"], "STALE")
        shown = self.ecall("reveal", draft_id=firsts[p0], reviewer="rev.one")
        self.assertEqual((shown["quote_disclosure"], shown["quotes"]), ("WITHHELD", []))
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=firsts[p0], reviewer="rev.one", decision="ACCEPT")
        self.assertIn(ctx.exception.code, ("DRAFT_STALE_OR_BLOCKED", "DRAFT_NOT_VERIFIED"))
        denied = [r for r in self.ecall("run_mock", scope_id=scope, operator="op.runner2")["results"] if r["paper_id"] == p0][0]
        self.assertEqual(denied["status"], "SCOPE_DENIED")
        with self.writer() as kb:
            ParseService(kb, parser_site=self.site, allow_language_level_network_guard=True, limits={"max_chars_per_page": 300_000}).parse(
                asset_sha256=by_paper[p1]["asset_sha256"], link_id=by_paper[p1]["link_id"], purpose="RESEARCH_PARSE", operator="op.parse", recheck=True)
        self.assertIn("STALE:PARSE_ARTIFACT_SUPERSEDED", self.eread("state", draft_id=firsts[p1])["stale_reasons"])
        self.assertEqual(self.eread("state", draft_id=firsts[p1])["current_state"], "STALE")
        cand2 = by_paper[p2]["candidate"]
        with self.reader() as kb:
            prior = kb.one("SELECT d.decision_id FROM fte_decision d JOIN fte_reviewer v USING (reviewer_id) WHERE d.candidate_id=? AND v.label='OPERATOR:rev.a'",
                           (cand2["candidate_id"],))["decision_id"]
        self.svc_call("decide", candidate_id=cand2["candidate_id"], reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_STUDY_DESIGN"], supersedes=prior)
        self.assertTrue(any(r.startswith("STALE:ELIGIBILITY_CHANGED") for r in self.eread("state", draft_id=firsts[p2])["stale_reasons"]))
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=by_paper[p2]["link_id"], decision="REJECT", operator="op.lib", reason="wrong paper after all")
        st2 = self.eread("state", draft_id=firsts[p2])
        self.assertIn("BLOCKED:IDENTITY_NOW_MISMATCH", st2["stale_reasons"])
        self.assertEqual(st2["review_decisions"], ["ACCEPT"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM ev_draft")["n"], before[0])
            self.assertEqual(kb.one("SELECT count(*) n FROM ev_review")["n"], before[1])

    def test_quote_storage_follows_gate_a3(self):
        """IDs: S08-QUOTE-GATE-A3, S08-EVIDENCE-SEEN-BEFORE-ACCEPT. When the current assessment allows local processing but not local
        storage of excerpts, drafts keep anchors and quote SHA-256 only (HASH_ONLY_GATE_A3_DENIED); the reveal withholds the quote and
        ACCEPT is refused because the reviewer could not see the source evidence (service and trigger)."""
        def no_excerpt_storage(pdf):
            with self.writer() as kb:
                row = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (pdf["asset_sha256"],))
                row.update(assessment_id=kb.ids.new("rga"), store_locally="DENY", supersedes_assessment_id=row["assessment_id"],
                           semantic_sha256=kb.ids.new("x").ljust(64, "0")[:64], recorded_by="OPERATOR:owner", assessed_by="OPERATOR:owner")
                kb.insert("rights_assessment", row)
        run, items = self.ev_world([[MALWARE_DOC]], before_attach=no_excerpt_storage)
        scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        self.ecall("run_mock", scope_id=scope, operator="op.runner")
        d = by_class(self.drafts(scope), "NUMERICAL_RESULT")[0]
        self.assertEqual((d["quote_storage"], d["quote_text_json"]), ("HASH_ONLY_GATE_A3_DENIED", None))
        self.assertEqual(self.ecall("verify", draft_id=d["draft_id"], operator="op.v")["state"], "STRUCTURALLY_VERIFIED")
        self.roster("rev.one")
        shown = self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
        # S08 v02 (S08-F01): the reveal also reports the withheld verbatim text fields and the current gate A5 decision.
        self.assertEqual(shown["withheld_reasons"], ["GATE_A5_DENIED:STORE_LOCALLY_DENY", "QUOTE_NOT_STORED_GATE_A3_DENIED", "TEXT_NOT_STORED_GATE_A3_DENIED"])
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "EVIDENCE_NOT_SEEN")
        with self.writer() as kb, self.assertRaises(KBError):
            ver = kb.one("SELECT verification_id FROM ev_verification WHERE draft_id=?", (d["draft_id"],))
            kb.insert("ev_review", dict(review_id=kb.ids.new("evrv"), draft_id=d["draft_id"], reviewer_id=kb.one("SELECT reviewer_id FROM ai_reviewer")["reviewer_id"],
                                        decision="ACCEPT", reason_codes_json="[]", rationale=None, corrected_draft_id=None,
                                        verification_id=ver["verification_id"], reveal_id=shown["reveal_id"], created_at_utc=kb.now()))


class HumanReview(S08Case):
    def test_dual_review_conflict_adjudication_correction_and_audit(self):
        """IDs: S08-ROSTER-HUMAN-ONLY, S08-REVIEW-REQUIRES-REVEAL, S08-SELF-REVIEW-REFUSED, S08-DUAL-REVIEW, S08-REVIEWER-DISAGREEMENT, S08-ADJUDICATION, S08-CORRECTION-NEW-DRAFT, S08-REVEAL-CURRENT, S08-AUDIT-HISTORY. AI/system labels are not reviewers; a review needs a reveal of the current verification; the operator who ran or imported the extraction cannot review it (service and trigger); under DUAL_INDEPENDENT one ACCEPT is PARTIALLY_REVIEWED, ACCEPT + REJECT is REVIEW_CONFLICT, the adjudicator must be independent, and adjudication resolves it; CORRECT stores a new HUMAN_CORRECTION draft (never an overwrite) that its author cannot review; the audit lists every run, verification, reveal, review and adjudication."""
        run, items = self.ev_world([[MALWARE_DOC]])
        scope = self.ev_scope(run, items, review_policy="DUAL_INDEPENDENT")
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        self.ecall("run_mock", scope_id=scope, operator="op.runner")
        with self.assertRaises(KBError) as ctx:
            self.call("register_reviewer", label="ai.helper", operator="op.owner", reason="x")
        self.assertEqual(ctx.exception.code, "REVIEWER_LABEL_INVALID")
        self.roster("rev.one", "rev.two", "rev.adj", "op.runner")
        tpr, roc = [d["draft_id"] for d in by_class(self.drafts(scope), "NUMERICAL_RESULT")]
        self.ecall("verify", draft_id=tpr, operator="op.v")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=tpr, reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "REVIEW_WITHOUT_REVEAL")
        self.ecall("reveal", draft_id=tpr, reviewer="op.runner")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=tpr, reviewer="op.runner", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "SELF_REVIEW_REFUSED")
        with self.writer() as kb, self.assertRaises(KBError):
            rev = kb.one("SELECT reviewer_id FROM ai_reviewer WHERE label='op.runner'")["reviewer_id"]
            reveal = kb.one("SELECT reveal_id FROM ev_reveal WHERE reviewer_id=?", (rev,))["reveal_id"]
            kb.insert("ev_review", dict(review_id=kb.ids.new("evrv"), draft_id=tpr, reviewer_id=rev, decision="DEFER", reason_codes_json="[]",
                                        rationale=None, corrected_draft_id=None, verification_id=None, reveal_id=reveal, created_at_utc=kb.now()))
        self.ecall("reveal", draft_id=tpr, reviewer="rev.one")
        self.ecall("verify", draft_id=tpr, operator="op.v")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=tpr, reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "REVEAL_OUTDATED")
        self.ecall("reveal", draft_id=tpr, reviewer="rev.one")
        self.assertEqual(self.ecall("review", draft_id=tpr, reviewer="rev.one", decision="ACCEPT")["state"]["review_state"], "PARTIALLY_REVIEWED")
        self.ecall("reveal", draft_id=tpr, reviewer="rev.two")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=tpr, reviewer="rev.two", decision="REJECT")
        self.assertEqual(ctx.exception.code, "REVIEW_REASON_REQUIRED")
        st = self.ecall("review", draft_id=tpr, reviewer="rev.two", decision="REJECT", reason_codes=["CONDITION_MISREAD"],
                        rationale="the operating point belongs to another model")["state"]
        self.assertEqual((st["current_state"], st["counts_as_accepted_evidence"]), ("REVIEW_CONFLICT", False))
        self.ecall("reveal", draft_id=tpr, reviewer="rev.one")
        with self.assertRaises(KBError) as ctx:
            self.ecall("adjudicate", draft_id=tpr, adjudicator="rev.one", outcome="ACCEPT", rationale="mine")
        self.assertEqual(ctx.exception.code, "ADJUDICATOR_NOT_INDEPENDENT")
        self.ecall("reveal", draft_id=tpr, reviewer="rev.adj")
        adj = self.ecall("adjudicate", draft_id=tpr, adjudicator="rev.adj", outcome="ACCEPT", rationale="the quote states the 1% operating point")
        self.assertEqual((adj["state"]["current_state"], adj["state"]["counts_as_accepted_evidence"]), ("HUMAN_ACCEPTED_BY_ADJUDICATION", True))
        # correction of the ROC draft: a new HUMAN_CORRECTION draft; the corrector cannot review it, another human can
        self.ecall("verify", draft_id=roc, operator="op.v")
        self.ecall("reveal", draft_id=roc, reviewer="rev.two")
        original = self.eread("show", draft_id=roc)
        page_anchor = original["anchors"][0]
        with self.reader() as kb:
            quote = json.loads(kb.one("SELECT quote_text_json FROM ev_draft WHERE draft_id=?", (roc,))["quote_text_json"])[0]
        fixed = {"value_status": "REPORTED", "fields": {"metric": "ROC_AUC", "value": "0.981", "unit": "ratio", "dataset": "EMBER", "dataset_year": "2018",
                                                         "split": "TEST", "subset": "test split"},
                 "anchors": [dict(page_anchor, quote=quote)]}
        out = self.ecall("review", draft_id=roc, reviewer="rev.two", decision="CORRECT", reason_codes=["MISSING_CONDITION"], rationale="subset added",
                         correction=fixed)
        new = out["corrected_draft_id"]
        self.assertEqual(self.eread("state", draft_id=roc)["corrected_by"], [new])
        nd = self.eread("show", draft_id=new)
        self.assertEqual((nd["origin"], nd["model_identity"], nd["created_by"], nd["supersedes_draft_id"]), ("HUMAN_CORRECTION", "HUMAN", "OPERATOR:rev.two", roc))
        self.assertEqual(self.ecall("verify", draft_id=new, operator="op.v")["state"], "STRUCTURALLY_VERIFIED")
        self.ecall("reveal", draft_id=new, reviewer="rev.two")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=new, reviewer="rev.two", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "SELF_REVIEW_REFUSED")
        for r in ("rev.one", "rev.adj"):
            self.ecall("reveal", draft_id=new, reviewer=r)
            self.ecall("review", draft_id=new, reviewer=r, decision="ACCEPT")
        self.assertTrue(self.eread("state", draft_id=new)["counts_as_accepted_evidence"])
        audit = self.eread("audit", draft_id=tpr)
        self.assertEqual([r["decision"] for r in audit["reviews"]], ["ACCEPT", "REJECT"])
        self.assertEqual(len(audit["verifications"]), 2)
        self.assertEqual(audit["adjudications"][0]["adjudicator"], "rev.adj")
        self.assertEqual(audit["run"]["model"]["model_id"], "rkb-mock-evidence-extractor")
        self.assertNotIn("93.1% at 1% FPR on the EMBER", json.dumps(audit))

    def test_conflicting_results_need_explicit_acknowledgement(self):
        """IDs: S08-CONFLICT-EXPLICIT, S08-ACCEPT-ONLY-VERIFIED. Two drafts for the same measurement key (metric, operating point,
        dataset, year, split) with different values are reported as conflicting; ACCEPT requires the reason code CONFLICT_ACKNOWLEDGED;
        an UNSUPPORTED draft cannot be accepted."""
        doc = ["TPR of 93.1% at 1% FPR on the EMBER 2018 test split.", "TPR of 91.0% at 1% FPR on the EMBER 2018 test split."]
        run, items, scope, _ = self.mocked([[doc]])
        a, b = [d["draft_id"] for d in by_class(self.drafts(scope), "NUMERICAL_RESULT")]
        self.assertEqual(self.eread("state", draft_id=a)["conflicts_with"], [b])
        self.roster("rev.one")
        self.ecall("verify", draft_id=a, operator="op.v")
        self.ecall("reveal", draft_id=a, reviewer="rev.one")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=a, reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "CONFLICT_NOT_ACKNOWLEDGED")
        self.assertTrue(self.ecall("review", draft_id=a, reviewer="rev.one", decision="ACCEPT", reason_codes=["CONFLICT_ACKNOWLEDGED"])["state"]
                        ["counts_as_accepted_evidence"])
        run2, items2, scope2, _ = self.mocked([[CI_DOC]], mode="inflate_number")
        bad = by_class(self.drafts(scope2), "NUMERICAL_RESULT")[0]["draft_id"]
        self.assertEqual(self.ecall("verify", draft_id=bad, operator="op.v")["state"], "UNSUPPORTED")
        self.ecall("reveal", draft_id=bad, reviewer="rev.one")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=bad, reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "DRAFT_NOT_VERIFIED")


class ProfilesAndExport(S08Case):
    def test_domain_profiles_are_configurable_and_versioned(self):
        """IDs: S08-PROFILE-CONFIGURABLE, S08-PROFILE-VALIDATION, S08-STALE-PROFILE, S08-GENERAL-PROFILE. A custom profile for another
        domain registers through the strict validator (malformed profiles are refused); the general profile extracts with text metrics;
        a superseding profile makes drafts of the old profile STALE without rewriting them."""
        custom = builtin("rkb-general-empirical")
        custom.update(profile_key="clinical-trial-sample", domain="clinical", label="Synthetic clinical profile")
        custom["classes"] = {"SAMPLE": {"fields": {"arm": {"type": "text", "required": True}, "n": {"type": "integer", "required": True}}}}
        self.assertTrue(self.ecall("register_profile", operator="op.owner", profile=custom)["created"])
        for broken in ({**custom, "schema": "other"}, {**custom, "classes": {"bad name": {"fields": {}}}},
                       {**custom, "classes": {"X1": {"fields": {"v": {"type": "decimal", "required": False, "unit_field": "nope"}}}}}):
            with self.assertRaises(KBError) as ctx:
                self.ecall("register_profile", operator="op.owner", profile=broken)
            self.assertEqual(ctx.exception.code, "PROFILE_INVALID")
        general = self.profile("rkb-general-empirical")
        run, items, scope, _ = self.mocked([[CI_DOC]], profile=general)
        drafts = self.drafts(scope)
        ci = by_class(drafts, "UNCERTAINTY_INTERVAL")[0]
        self.assertEqual(json.loads(ci["typed_fields_json"])["lower"], "0.91")
        self.assertEqual(self.ecall("verify", draft_id=ci["draft_id"], operator="op.v")["state"], "STRUCTURALLY_VERIFIED")
        result = by_class(drafts, "NUMERICAL_RESULT")[0]
        self.assertEqual(json.loads(result["typed_fields_json"])["metric"], "accuracy")
        self.ecall("register_profile", operator="op.owner", profile=dict(builtin("rkb-general-empirical"), version="2"), supersedes=general)
        self.assertIn("STALE:PROFILE_SUPERSEDED", self.eread("state", draft_id=ci["draft_id"])["stale_reasons"])

    def test_export_is_metadata_only_deterministic_and_rights_safe(self):
        """IDs: S08-EXPORT-METADATA-ONLY, S08-EXPORT-NO-QUOTES, S08-EXPORT-DETERMINISTIC, S08-EXPORT-TYPED-ACCEPTED-ONLY, S08-EXPORT-NO-PATHS. The
        default export holds identifiers, hashes, anchors and states but no quote, typed value or document text; every stored quote is
        counted as excluded (A7); ACCEPTED_TYPED_VALUES adds typed values only for currently accepted drafts; repeating an export gives
        identical bytes; an out-dir inside the KB is refused."""
        run, items, scope, _ = self.mocked([[MALWARE_DOC]])
        self.roster("rev.one")
        d = by_class(self.drafts(scope), "NUMERICAL_RESULT")[0]["draft_id"]
        self.ecall("verify", draft_id=d, operator="op.v")
        self.ecall("reveal", draft_id=d, reviewer="rev.one")
        self.ecall("review", draft_id=d, reviewer="rev.one", decision="ACCEPT")
        out = os.path.join(self.tmp, "exports")
        first = self.ecall("export", scope_id=scope, out_dir=out, operator="op.owner")
        second = self.ecall("export", scope_id=scope, out_dir=out, operator="op.owner")
        self.assertEqual((first["report_sha256"], second["written"]), (second["report_sha256"], False))
        data = open(os.path.join(out, first["folder_name"], "evidence_report.json"), "rb").read()
        report = json.loads(data)
        self.assertEqual((report["contains_quotes"], report["contains_document_text"], report["authoritative_corpus"]), (False, False, False))
        self.assertEqual(report["excluded_by_reason"]["QUOTE_A7_REDISTRIBUTE_NOT_ALLOWED"], 7)
        self.assertNotIn(b"93.1", data)
        self.assertNotIn(b"EMBER", data)
        typed = self.ecall("export", scope_id=scope, out_dir=out, operator="op.owner", content_level="ACCEPTED_TYPED_VALUES")
        rep = json.loads(open(os.path.join(out, typed["folder_name"], "evidence_report.json"), "rb").read())
        self.assertEqual(sum(1 for r in rep["drafts"] if "typed_fields" in r), 1)
        self.assertEqual(rep["excluded_by_reason"]["TYPED_VALUES_NOT_ACCEPTED_EVIDENCE"], 7)
        with self.assertRaises(KBError) as ctx:
            self.ecall("export", scope_id=scope, out_dir=os.path.join(self.root, "x"), operator="op.owner")
        self.assertEqual(ctx.exception.code, "OUT_DIR_INVALID")
        with self.reader() as kb:
            # S08 v02 (S08-F02): each publication is a RESERVED + PUBLISHED pair; repeating identical bytes records nothing new.
            self.assertEqual(kb.one("SELECT count(*) n FROM ev_export_event WHERE contains_quotes=0")["n"], 4)
            self.assertEqual([r["phase"] for r in kb.all("SELECT phase FROM ev_export_event ORDER BY rowid")], ["RESERVED", "PUBLISHED"] * 2)


if __name__ == "__main__":
    unittest.main()
