"""S05: evidence gating, human-only review and adjudication, staleness, lifecycle/lock, accounting, export and privacy."""
import copy
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

from tests.fte_helpers import FTECase, PROTOCOL, ROSTER, write_stage11b_export
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.errors import ImmutableRecordError, KBError
from scivetra_kb.eligibility import protocol as P
from scivetra_kb.eligibility.handoff import export_run, verify_export, verify_lock
from scivetra_kb.eligibility.service import FTEService


class EvidenceTests(FTECase):
    def setUp(self):
        super().setUp()
        self.r = self.make_run(6)
        self.c = self.r["candidates"]

    def code_of(self, fn, **kw):
        with self.assertRaises(KBError) as ctx:
            self.svc_call(fn, **kw)
        return ctx.exception.code

    def test_only_human_confirmed_main_text_of_the_same_paper_is_evidence(self):
        """IDs: S05-EV-IDENTITY, S05-EV-WRONG-PAPER, S05-EV-ROLE, S05-PBD-NOT-IDENTITY. A structurally valid (G0-G3) PDF whose link is UNVERIFIED, REJECTED (MISMATCH), linked to another candidate's paper, or a supplementary item is refused as evidence with a stable code; the human-confirmed main text of the candidate's own paper is accepted and its CAS bytes are re-hashed at attach (KB_CAS_REHASHED_AT_ATTACH)."""
        unconfirmed = self.add_pdf(self.c[0], confirm=False)
        self.assertEqual(self.code_of("attach_kb_asset", candidate_id=self.c[0]["candidate_id"], link_id=unconfirmed["link_id"], operator="op.f"),
                         "EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED")
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=unconfirmed["link_id"], decision="REJECT", operator="op.h", reason="different paper")
        self.assertEqual(self.code_of("attach_kb_asset", candidate_id=self.c[0]["candidate_id"], link_id=unconfirmed["link_id"], operator="op.f"),
                         "EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED")
        other = self.add_pdf(self.c[1])
        self.assertEqual(self.code_of("attach_kb_asset", candidate_id=self.c[0]["candidate_id"], link_id=other["link_id"], operator="op.f"),
                         "EVIDENCE_PAPER_MISMATCH")
        supp = self.add_pdf(self.c[2], confirm=False, variant="supp")
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=supp["link_id"], decision="MARK_SUPPLEMENT", operator="op.h", reason="supplement")
        self.assertEqual(self.code_of("attach_kb_asset", candidate_id=self.c[2]["candidate_id"], link_id=supp["link_id"], operator="op.f"),
                         "EVIDENCE_NOT_MAIN_TEXT")
        out, ev = self.attach(self.c[3])
        self.assertTrue(out["created"] and out["evidence_ready"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT byte_verification FROM fte_evidence WHERE evidence_id=?", (out["evidence_id"],))["byte_verification"],
                             "KB_CAS_REHASHED_AT_ATTACH")
        again = self.svc_call("attach_kb_asset", candidate_id=self.c[3]["candidate_id"], link_id=ev["link_id"], operator="op.f")
        self.assertEqual((again["created"], again["code"]), (False, "DUPLICATE_NOOP"))

    def test_version_rights_and_missing_bytes_block_evidence(self):
        """IDs: S05-EV-VERSION, S05-EV-RIGHTS, S05-EV-CAS, S05-EV-PREPRINT. A submitted preprint (protocol allows published/accepted) and a restricted or default-deny (UNKNOWN) licence are recorded with explicit blockers and evidence_ready=false, so FT_INCLUDE/FT_EXCLUDE are refused (EVIDENCE_NOT_READY) and the queue shows VERSION_MISMATCH / RESTRICTED_RIGHTS; a confirmed PDF whose CAS bytes are missing cannot be attached (EVIDENCE_BYTES_UNVERIFIABLE)."""
        out, _ = self.attach(self.c[0], version="SUBMITTED", rights="NONE")
        # S03 intake always records a default policy-engine assessment (UNKNOWN -> local processing UNDETERMINED: default deny).
        self.assertEqual(sorted(out["blockers"]), ["RIGHTS_LOCAL_PROCESS_UNDETERMINED", "VERSION_NOT_ALLOWED_BY_PROTOCOL:SUBMITTED"])
        out, _ = self.attach(self.c[1], rights="RESTRICTED")
        self.assertFalse(out["evidence_ready"])
        self.assertTrue(any(b.startswith("RIGHTS_LOCAL_PROCESS_") for b in out["blockers"]))
        for c in self.c[:2]:
            self.assertEqual(self.code_of("decide", candidate_id=c["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE"), "EVIDENCE_NOT_READY")
            self.assertEqual(self.code_of("decide", candidate_id=c["candidate_id"], reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_POPULATION"]),
                             "EVIDENCE_NOT_READY")
        with self.reader() as kb:
            q = {e["corpus_item_id"]: {f["category"] for f in e["flags"]} for e in FTEService(kb).queue(self.r["run_id"])}
        self.assertIn("VERSION_MISMATCH", q[self.c[0]["corpus_item_id"]])
        self.assertIn("RESTRICTED_RIGHTS", q[self.c[1]["corpus_item_id"]])
        ev = self.add_pdf(self.c[2])
        Path(self.root, "cas", "sha256", ev["asset_sha256"][:2], ev["asset_sha256"]).unlink()
        self.assertEqual(self.code_of("attach_kb_asset", candidate_id=self.c[2]["candidate_id"], link_id=ev["link_id"], operator="op.f"),
                         "EVIDENCE_BYTES_UNVERIFIABLE")

    def test_external_stage11b_reference_is_verified_and_never_ready(self):
        """IDs: S05-EV-EXTERNAL, S05-F01-NEVER-READY. (S05 v02) An RV-Stage11B reference names the read-only portable export and its trusted digest; it is verified against the actual files and this run's release/item/artifact, recorded as EXTERNAL_HASH_ONLY_NOT_REVERIFIED with blockers (no human identity confirmation, no KB bytes, no rights determination) and is never evidence-ready, so FT_INCLUDE is refused; a self-asserted v01-style reference is refused; a protocol that does not allow external evidence refuses it."""
        export = os.path.join(self.tmp, "stage11b")
        art = hashlib.sha256(b"synthetic rv artifact 0").hexdigest()
        digest = write_stage11b_export(export, self.r["items"], release_id="crl_fte_synthetic", release_sem=self.r["sem"],
                                       acquired={self.c[0]["corpus_item_id"]: art})
        ref = {"fulltext_export_dir": export, "expected_manifest_sha256": digest}
        out = self.svc_call("attach_external", candidate_id=self.c[0]["candidate_id"], artifact=ref, operator="op.f")
        self.assertFalse(out["evidence_ready"])
        self.assertEqual(out["verification_level"], "RV11B_PORTABLE_EXPORT_VERIFIED_WITH_TRUSTED_DIGEST")
        self.assertEqual(set(out["blockers"]), {"IDENTITY_NOT_HUMAN_CONFIRMED_EXTERNAL", "BYTES_NOT_IN_KB_EXTERNAL", "RIGHTS_NOT_DETERMINED_EXTERNAL"})
        with self.reader() as kb:
            row = kb.one("SELECT * FROM fte_evidence WHERE evidence_id=?", (out["evidence_id"],))
            flags = [f["category"] for e in FTEService(kb).queue(self.r["run_id"]) if e["candidate_id"] == self.c[0]["candidate_id"] for f in e["flags"]]
        self.assertEqual((row["byte_verification"], row["evidence_ready"], row["version_class"], row["rights_class"]),
                         ("EXTERNAL_HASH_ONLY_NOT_REVERIFIED", 0, "PUBLISHED", None))
        stored = json.loads(row["external_ref_json"])
        self.assertEqual((stored["artifact_sha256"], stored["fulltext_manifest_sha256"], stored["release_manifest_sha256"]), (art, digest, self.r["sem"]))
        self.assertNotIn(export, row["external_ref_json"])
        self.assertIn("EXTERNAL_EVIDENCE_HASH_ONLY", flags)
        self.assertNotIn("RESTRICTED_RIGHTS", flags)
        self.assertEqual(self.code_of("decide", candidate_id=self.c[0]["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE"), "EVIDENCE_NOT_READY")
        legacy = {"fulltext_run_id": "ftr_rv_synthetic", "fulltext_manifest_sha256": digest, "artifact_sha256": art, "corpus_item_id": self.c[0]["corpus_item_id"],
                  "version_class": "PUBLISHED", "rights_class": "PUBLIC_OA_LICENSED", "export_verified": True}
        self.assertEqual(self.code_of("attach_external", candidate_id=self.c[0]["candidate_id"], artifact=legacy, operator="op.f"),
                         "EXTERNAL_SELF_ASSERTION_REFUSED")
        strict = copy.deepcopy(PROTOCOL)
        strict["evidence_policy"]["allowed_kinds"] = ["KB_ASSET"]
        strict["label"] = "KB only"
        other = self.make_run(2, protocol=strict, release_id="crl_kbonly", workspace="ws_k")
        self.assertEqual(self.code_of("attach_external", candidate_id=other["candidates"][0]["candidate_id"], artifact=ref, operator="op.f"),
                         "EVIDENCE_KIND_NOT_ALLOWED")

    def test_changed_evidence_makes_votes_stale(self):
        """IDs: S05-EV-CHANGED, S05-STALE. After two FT_INCLUDE votes, new bytes for the same candidate (a newer confirmed PDF) supersede the evidence: the earlier votes are stale (PENDING STALE_DECISION_EVIDENCE_CHANGED, queue CHANGED_EVIDENCE) until both reviewers re-decide (explicitly superseding); a later rights reassessment of the attached asset is detected as CHANGED_EVIDENCE and blocks the lock."""
        self.attach(self.c[0])
        a, b = self.both(self.c[0], "FT_INCLUDE")
        out, _ = self.attach(self.c[0], variant="v2")
        self.assertTrue(out["earlier_votes_now_stale"])
        with self.reader() as kb:
            state = FTEService(kb).candidate_state(self.c[0])
        self.assertEqual((state["status"], state["pending_reason"]), ("PENDING", "STALE_DECISION_EVIDENCE_CHANGED"))
        self.svc_call("decide", candidate_id=self.c[0]["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE", supersedes=a["decision_id"])
        self.svc_call("decide", candidate_id=self.c[0]["candidate_id"], reviewer="rev.b", outcome="FT_INCLUDE", supersedes=b["decision_id"])
        with self.reader() as kb:
            self.assertEqual(FTEService(kb).candidate_state(self.c[0])["outcome"], "FT_INCLUDE")
        out2, ev2 = self.attach(self.c[1])
        self.both(self.c[1], "FT_INCLUDE")
        with self.writer() as kb:
            AssetService(kb).assess_rights(asset_sha256=ev2["asset_sha256"], document_version_id=ev2["document_version_id"], operator="op.r",
                                           evidence={"license_evidence": [], "availability_evidence": []})
            flags = {f["category"] for e in FTEService(kb).queue(self.r["run_id"]) if e["candidate_id"] == self.c[1]["candidate_id"] for f in e["flags"]}
        self.assertIn("CHANGED_EVIDENCE", flags)


class ReviewTests(FTECase):
    def setUp(self):
        super().setUp()
        self.r = self.make_run(6)
        self.c = self.r["candidates"]

    def code_of(self, fn, **kw):
        with self.assertRaises(KBError) as ctx:
            self.svc_call(fn, **kw)
        return ctx.exception.code

    def test_roster_is_human_only(self):
        """IDs: S05-AI-NO-VOTE, S05-ROSTER. actor_type other than HUMAN, and labels naming an AI, model, bot or system, are refused at roster level (NON_HUMAN_ACTOR_REFUSED); the plan needs two reviewers and one adjudicator and one person cannot hold both roles; an unregistered label, an AI label or an adjudicator voting as reviewer cannot decide; the database CHECK refuses a non-HUMAN reviewer row."""
        for bad in ([dict(ROSTER[0], actor_type="AI")] + ROSTER[1:], [dict(ROSTER[0], label="gpt.reviewer")] + ROSTER[1:],
                    [dict(ROSTER[0], label="claude")] + ROSTER[1:], [dict(ROSTER[0], label="screen-bot")] + ROSTER[1:],
                    [dict(ROSTER[0], label="system")] + ROSTER[1:], [dict(ROSTER[0], label="rev.ai")] + ROSTER[1:]):
            with self.assertRaises(KBError) as ctx:
                P.validate_roster(bad)
            self.assertEqual(ctx.exception.code, "NON_HUMAN_ACTOR_REFUSED", bad[0])
        for bad in (ROSTER[:1] + ROSTER[2:], ROSTER[:2], ROSTER + [dict(ROSTER[0], role="ADJUDICATOR")]):
            with self.assertRaises(KBError) as ctx:
                P.validate_roster(bad)
            self.assertEqual(ctx.exception.code, "ROSTER_INVALID")
        self.attach(self.c[0])
        for reviewer, code in (("someone.else", "UNKNOWN_REVIEWER"), ("model.gpt", "UNKNOWN_REVIEWER"), ("adj.c", "WRONG_ROLE")):
            self.assertEqual(self.code_of("decide", candidate_id=self.c[0]["candidate_id"], reviewer=reviewer, outcome="FT_INCLUDE"), code)
        with self.writer() as kb:
            with self.assertRaises(KBError):
                with kb.transaction():
                    kb.execute("INSERT INTO fte_reviewer VALUES ('ftv_ai', ?, 'OPERATOR:ai', 'REVIEWER', 'AI', '[]')", (self.r["run_id"],))

    def test_assignment_independence_and_conflicts_of_interest(self):
        """IDs: S05-ASSIGN, S05-COI. With three reviewers the frozen assignment gives each candidate two distinct reviewers and skips declared conflicts of interest; a reviewer not assigned to a candidate cannot decide it (NOT_ASSIGNED_REVIEWER, also enforced by trigger); a roster that cannot cover a candidate without a conflict is refused."""
        roster = [{"label": "rev.a", "role": "REVIEWER", "actor_type": "HUMAN", "conflicts": ["cri_fte_0000"]},
                  {"label": "rev.b", "role": "REVIEWER", "actor_type": "HUMAN"}, {"label": "rev.d", "role": "REVIEWER", "actor_type": "HUMAN"},
                  {"label": "adj.c", "role": "ADJUDICATOR", "actor_type": "HUMAN"}]
        r = self.make_run(4, roster=roster, release_id="crl_three", workspace="ws3")
        with self.reader() as kb:
            assign = {}
            for row in kb.all("SELECT c.corpus_item_id, v.label FROM fte_assignment a JOIN fte_candidate c USING (candidate_id) JOIN fte_reviewer v "
                              "USING (reviewer_id) WHERE c.run_id=? ORDER BY c.corpus_item_id, a.slot", (r["run_id"],)):
                assign.setdefault(row["corpus_item_id"], []).append(row["label"])
        self.assertTrue(all(len(set(v)) == 2 for v in assign.values()))
        self.assertNotIn("OPERATOR:rev.a", assign["cri_fte_0000"])
        c0 = r["candidates"][0]
        self.attach(c0)
        self.assertEqual(self.code_of("decide", candidate_id=c0["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE"), "NOT_ASSIGNED_REVIEWER")
        blocked = [dict(e, conflicts=["cri_fte_0000"]) if e["role"] == "REVIEWER" and e["label"] != "rev.d" else e for e in roster]
        with self.assertRaises(KBError) as ctx:
            self.make_run(2, roster=blocked, release_id="crl_coi", workspace="ws_coi")
        self.assertEqual(ctx.exception.code, "ROSTER_CANNOT_COVER_CANDIDATE")

    def test_reason_vocabulary_and_retrieval_rules(self):
        """IDs: S05-REASONS, S05-NOT-RETRIEVED. FT_EXCLUDE needs reasons from the frozen exclusion vocabulary; FT_INCLUDE takes none; FT_UNSURE needs a note; FT_NOT_RETRIEVED needs a not-retrieved reason (an exclusion reason is refused) and the protocol's documented attempts, and is refused while evidence-ready full text is attached; a duplicate-report reason needs another candidate of the run."""
        self.attach(self.c[0])
        cid = self.c[0]["candidate_id"]
        checks = [(dict(outcome="FT_EXCLUDE"), "REASON_INVALID"), (dict(outcome="FT_EXCLUDE", reasons=["NOT_IN_VOCABULARY"]), "REASON_INVALID"),
                  (dict(outcome="FT_INCLUDE", reasons=["WRONG_POPULATION"]), "REASON_INVALID"), (dict(outcome="FT_UNSURE"), "TEXT_REQUIRED"),
                  (dict(outcome="FT_NOT_RETRIEVED", reasons=["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"]), "NOT_RETRIEVED_CONTRADICTS_READY_EVIDENCE"),
                  (dict(outcome="FT_EXCLUDE", reasons=["DUPLICATE_REPORT_OF_INCLUDED_STUDY"]), "DUPLICATE_LINK_INVALID"),
                  (dict(outcome="FT_MAYBE"), "OUTCOME_INVALID")]
        for kw, code in checks:
            self.assertEqual(self.code_of("decide", candidate_id=cid, reviewer="rev.a", **kw), code, kw)
        c1 = self.c[1]["candidate_id"]
        self.assertEqual(self.code_of("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_NOT_RETRIEVED", reasons=["WRONG_POPULATION"]),
                         "REASON_INVALID")
        self.assertEqual(self.code_of("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_NOT_RETRIEVED", reasons=["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"]),
                         "RETRIEVAL_EFFORT_NOT_DOCUMENTED")
        self.attempts(self.c[1], 2, status="BOT_CHECK")
        out = self.svc_call("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_NOT_RETRIEVED", reasons=["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"])
        self.assertTrue(out["created"])

    def test_acquisition_attempts_are_operational_and_secret_free(self):
        """IDs: S05-ACQ-OPERATIONAL, S05-PRIVACY-INPUT. LOGIN_REQUIRED, BOT_CHECK, INSTITUTIONAL_RESTRICTED, NO_ACCESS, RETRY_QUEUED and WRONG_PDF attempts are stored without creating any vote or outcome (the candidate stays PENDING AWAITING_REVIEWS) and appear in the queue; text holding a password/token/cookie, a URL with a query string, an absolute local path or an e-mail address is refused; an exact repeat is DUPLICATE_NOOP."""
        cid = self.c[0]["candidate_id"]
        for k, status in enumerate(("LOGIN_REQUIRED", "BOT_CHECK", "INSTITUTIONAL_RESTRICTED", "NO_ACCESS", "WRONG_PDF", "RETRY_QUEUED")):
            self.svc_call("record_attempt", candidate_id=cid, status=status, source_description="publisher site", attempted_on=f"2026-10-0{k + 1}",
                          retry_after="2026-11-15" if status == "RETRY_QUEUED" else None, operator="op.acq")
        with self.reader() as kb:
            svc = FTEService(kb)
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_decision")["n"], 0)
            self.assertEqual(svc.candidate_state(self.c[0])["pending_reason"], "AWAITING_REVIEWS")
            flags = {f["category"] for e in svc.queue(self.r["run_id"]) if e["candidate_id"] == cid for f in e["flags"]}
        self.assertIn("RETRYABLE_ACQUISITION", flags)
        for text, code in (("cookie=abc123", "TEXT_LOOKS_LIKE_SECRET"), ("https://x.example/a.pdf?token=1", "TEXT_LOOKS_LIKE_SECRET"),
                           ("https://x.example/a.pdf?id=1", "TEXT_URL_WITH_QUERY_REFUSED"), ("C:\\Users\\me\\Downloads\\a.pdf", "TEXT_LOCAL_PATH_REFUSED"),
                           ("/home/me/a.pdf", "TEXT_LOCAL_PATH_REFUSED"), ("ask owner@example.invalid", "TEXT_EMAIL_REFUSED")):
            self.assertEqual(self.code_of("record_attempt", candidate_id=cid, status="OTHER", source_description=text, attempted_on="2026-10-20",
                                          operator="op.acq"), code, text)
        again = self.svc_call("record_attempt", candidate_id=cid, status="BOT_CHECK", source_description="publisher site", attempted_on="2026-10-02",
                              operator="op.acq")
        self.assertEqual(again["code"], "DUPLICATE_NOOP")

    def test_consensus_disagreement_adjudication_and_supersession(self):
        """IDs: S05-CONSENSUS, S05-ADJUDICATION, S05-SUPERSEDE, S05-NO-DOUBLE-COUNT. Two matching votes are a CONSENSUS outcome; disagreement or FT_UNSURE stays PENDING NEEDS_ADJUDICATION; adjudication is refused before both votes, when they agree, or by a reviewer; a vote can only be replaced by an explicit superseding decision (SUPERSEDE_REQUIRED otherwise), an exact repeat is DUPLICATE_NOOP, and superseded votes are reported separately and never counted; a re-vote after adjudication makes the adjudication stale."""
        for c in self.c[:3]:
            self.attach(c)
        c0, c1, c2 = (c["candidate_id"] for c in self.c[:3])
        self.both(self.c[0], "FT_INCLUDE")
        self.assertEqual(self.code_of("adjudicate", candidate_id=c0, adjudicator="adj.c", outcome="FT_INCLUDE", note="n"),
                         "ADJUDICATION_WITHOUT_DISAGREEMENT")
        self.assertEqual(self.code_of("adjudicate", candidate_id=c1, adjudicator="adj.c", outcome="FT_INCLUDE", note="n"), "REVIEWS_INCOMPLETE")
        a = self.svc_call("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_INCLUDE")
        self.assertEqual(self.code_of("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_OUTCOME"]), "SUPERSEDE_REQUIRED")
        self.assertEqual(self.svc_call("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_INCLUDE")["code"], "DUPLICATE_NOOP")
        a2 = self.svc_call("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_EXCLUDE", reasons=["WRONG_OUTCOME"], supersedes=a["decision_id"])
        self.svc_call("decide", candidate_id=c1, reviewer="rev.b", outcome="FT_INCLUDE")
        with self.reader() as kb:
            self.assertEqual(FTEService(kb).candidate_state(self.c[1])["pending_reason"], "NEEDS_ADJUDICATION")
        self.assertEqual(self.code_of("adjudicate", candidate_id=c1, adjudicator="rev.a", outcome="FT_INCLUDE", note="n"), "WRONG_ROLE")
        adj = self.svc_call("adjudicate", candidate_id=c1, adjudicator="adj.c", outcome="FT_EXCLUDE", reasons=["WRONG_OUTCOME"], note="outcome differs")
        self.assertEqual((adj["candidate_state"]["basis"], adj["candidate_state"]["outcome"]), ("ADJUDICATION", "FT_EXCLUDE"))
        self.svc_call("decide", candidate_id=c2, reviewer="rev.a", outcome="FT_UNSURE", note="unclear population")
        self.svc_call("decide", candidate_id=c2, reviewer="rev.b", outcome="FT_UNSURE", note="unclear")
        with self.reader() as kb:
            self.assertEqual(FTEService(kb).candidate_state(self.c[2])["pending_reason"], "NEEDS_ADJUDICATION")
        self.svc_call("decide", candidate_id=c1, reviewer="rev.a", outcome="FT_INCLUDE", supersedes=a2["decision_id"])
        with self.reader() as kb:
            svc = FTEService(kb)
            self.assertEqual(svc.candidate_state(self.c[1])["outcome"], "FT_INCLUDE")
            summary = svc.summary(self.r["run_id"])
        self.assertEqual(summary["review_load"]["superseded_votes_not_counted"], 2)
        self.assertEqual((summary["ft_include"], summary["ft_exclude"], summary["ft_pending"]), (2, 0, 4))
        self.assertTrue(summary["invariant"]["holds"])

    def test_retraction_and_integrity_signals_are_warnings_only(self):
        """IDs: S05-RETRACTION-WARNING. An integrity event (retraction) reported for the candidate's DOI appears as INTEGRITY_ALERT in the queue while the candidate stays PENDING AWAITING_REVIEWS: no automatic FT_EXCLUDE; a human may exclude with the protocol reason RETRACTED."""
        with self.writer() as kb:
            key = kb.one("SELECT value_normalized v FROM identity_assertion WHERE paper_id=? AND namespace='doi'", (self.c[0]["paper_id"],))["v"]
            kb.insert("integrity_event", {"event_id": "ige_synthetic", "subject_paper_id": self.c[0]["paper_id"], "subject_namespace": "doi",
                                          "subject_value": key, "event_type": "RETRACTION", "event_subtype": "retraction", "notice_doi": None,
                                          "reported_by": "OPERATOR", "record_id": None, "first_snapshot_id": None, "source_field_path": None,
                                          "operator_evidence_ref": "synthetic notice", "event_date_json": None, "payload_json": "{}",
                                          "created_at_utc": kb.now(), "semantic_sha256": "e" * 64})
        with self.reader() as kb:
            svc = FTEService(kb)
            entry = [e for e in svc.queue(self.r["run_id"]) if e["candidate_id"] == self.c[0]["candidate_id"]][0]
            self.assertEqual((entry["status"], entry["outcome"]), ("PENDING", None))
        self.assertIn("INTEGRITY_ALERT", {f["category"] for f in entry["flags"]})
        self.attach(self.c[0])
        self.both(self.c[0], "FT_EXCLUDE", ["RETRACTED"])
        with self.reader() as kb:
            self.assertEqual(FTEService(kb).candidate_state(self.c[0])["outcome"], "FT_EXCLUDE")


class LifecycleTests(FTECase):
    def finish(self, r):
        c = r["candidates"]
        self.attach(c[0])
        self.both(c[0], "FT_INCLUDE")
        self.attach(c[1])
        self.both(c[1], "FT_EXCLUDE", ["DUPLICATE_REPORT_OF_INCLUDED_STUDY"], related_corpus_item_id=c[0]["corpus_item_id"])
        self.attach(c[2])
        self.svc_call("decide", candidate_id=c[2]["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE", note="meets C1 and C2")
        self.svc_call("decide", candidate_id=c[2]["candidate_id"], reviewer="rev.b", outcome="FT_EXCLUDE", reasons=["WRONG_STUDY_DESIGN"])
        self.svc_call("adjudicate", candidate_id=c[2]["candidate_id"], adjudicator="adj.c", outcome="FT_EXCLUDE", reasons=["WRONG_STUDY_DESIGN"],
                      note="non-randomized")
        self.attempts(c[3])
        self.svc_call("attach_none", candidate_id=c[3]["candidate_id"], operator="op.f")
        self.both(c[3], "FT_NOT_RETRIEVED", ["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"])

    def test_lock_requires_resolution_and_is_immutable(self):
        """IDs: S05-LOCK, S05-LOCK-REFUSED, S05-IMMUTABLE, S05-ACCOUNTING. resolve and lock are refused while a candidate is pending; after every candidate is final the run resolves and locks into a NON_AUTHORITATIVE staging snapshot whose handoff manifest is stored in CAS; accounting reconciles exactly (4 = 1 + 2 + 1 + 0; studies 1, reports of included studies 2); afterwards decisions, evidence, attempts and events are refused (service and triggers), lock is idempotent, and UPDATE/DELETE on S05 tables is refused."""
        r = self.make_run(5)
        self.finish(r)
        for fn in ("resolve", "lock"):
            with self.assertRaises(KBError) as ctx:
                self.svc_call(fn, run_id=r["run_id"], operator="op.f")
            self.assertIn(ctx.exception.code, ("RUN_HAS_PENDING_CANDIDATES", "RUN_NOT_RESOLVED"))
        c4 = r["candidates"][4]
        self.attempts(c4)
        self.both(c4, "FT_NOT_RETRIEVED", ["NOT_AVAILABLE_FROM_AUTHORS"])
        self.svc_call("resolve", run_id=r["run_id"], operator="op.f")
        lock = self.svc_call("lock", run_id=r["run_id"], operator="op.f")
        s = lock["summary"]
        self.assertEqual((s["ta_candidate_count"], s["ft_include"], s["ft_exclude"], s["ft_not_retrieved"], s["ft_pending"]), (5, 1, 2, 2, 0))
        self.assertEqual((s["reports_vs_studies"]["studies_included"], s["reports_vs_studies"]["reports_of_included_studies"]), (1, 2))
        self.assertEqual(self.svc_call("lock", run_id=r["run_id"], operator="op.f")["code"], "DUPLICATE_NOOP")
        with self.assertRaises(KBError) as ctx:
            self.svc_call("decide", candidate_id=c4["candidate_id"], reviewer="rev.a", outcome="FT_UNSURE", note="late")
        self.assertEqual(ctx.exception.code, "RUN_LOCKED")
        with self.writer() as kb:
            self.assertTrue(kb.cas.exists(lock["handoff_manifest_sha256"]))
            with self.assertRaises(KBError):
                with kb.transaction():
                    kb.insert("fte_acquisition_attempt", {"attempt_id": "ftq_late", "candidate_id": c4["candidate_id"], "status": "OTHER",
                                                          "source_description": "late", "attempted_on": "2026-10-30", "retry_after": None, "note": None,
                                                          "recorded_by": "OPERATOR:x", "recorded_at_utc": kb.now(), "semantic_sha256": "f" * 64})
            for sql in ("UPDATE fte_decision SET outcome='FT_INCLUDE'", "DELETE FROM fte_lock", "UPDATE fte_run SET authority='AUTHORITATIVE'",
                        "DELETE FROM fte_candidate"):
                with self.assertRaises(ImmutableRecordError):
                    kb.execute(sql)
            state = FTEService(kb).state(r["run_id"])
        self.assertEqual(state, "LOCKED_FOR_HANDOFF")

    def test_locked_snapshot_survives_metadata_refresh_and_detects_tampering(self):
        """IDs: S05-SNAPSHOT-VERIFIABLE, S05-EXPORT-DETERMINISTIC, S05-TAMPER. After the lock, a later rights reassessment and identity event in the library do not change the locked snapshot (verify_lock valid); two exports are byte-identical and re-export is a no-op; the exported folder verifies offline (checksums, replay of every decision/adjudication hash, outcomes digest, accounting) and any edit of a decision, a candidate row or a removed file is detected."""
        r = self.make_run(5)
        self.finish(r)
        c4 = r["candidates"][4]
        self.attempts(c4)
        self.both(c4, "FT_NOT_RETRIEVED", ["NOT_AVAILABLE_FROM_AUTHORS"])
        self.svc_call("resolve", run_id=r["run_id"], operator="op.f")
        self.svc_call("lock", run_id=r["run_id"], operator="op.f")
        with self.writer() as kb:
            ev = kb.one("SELECT * FROM fte_evidence WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1", (r["candidates"][0]["candidate_id"],))
            AssetService(kb).assess_rights(asset_sha256=ev["asset_sha256"], document_version_id=ev["document_version_id"], operator="op.r",
                                           evidence={"license_evidence": [], "availability_evidence": []})
            self.assertTrue(verify_lock(kb, r["run_id"])["valid"])
            a = export_run(kb, r["run_id"], os.path.join(self.tmp, "x1"), operator="op.f")
            b = export_run(kb, r["run_id"], os.path.join(self.tmp, "x2"), operator="op.f")
            again = export_run(kb, r["run_id"], os.path.join(self.tmp, "x1"), operator="op.f")
        self.assertEqual(again["code"], "EXPORT_ALREADY_PRESENT_IDENTICAL")
        fa, fb = os.path.join(self.tmp, "x1", a["folder_name"]), os.path.join(self.tmp, "x2", b["folder_name"])
        self.assertEqual(sorted(os.listdir(fa)), sorted(os.listdir(fb)))
        for name in os.listdir(fa):
            self.assertEqual(Path(fa, name).read_bytes(), Path(fb, name).read_bytes(), name)
        self.assertTrue(verify_export(fa)["valid"])
        import shutil
        for mutate, problem in (("decisions", "CHECKSUM_MISMATCH"), ("rehash_decision", "DECISION_HASH_REPLAY_FAILED"),
                                ("rehash_candidate", "OUTCOMES_DIGEST_MISMATCH"), ("delete", "FILE_SET_MISMATCH")):
            work = os.path.join(self.tmp, "t_" + mutate)
            shutil.copytree(fa, work)
            if mutate == "delete":
                os.remove(os.path.join(work, "adjudications.jsonl"))
            else:
                target = "candidates.jsonl" if mutate == "rehash_candidate" else "decisions.jsonl"
                text = Path(work, target).read_text(encoding="utf-8").replace("FT_INCLUDE", "FT_EXCLUDE", 1)
                Path(work, target).write_text(text, encoding="utf-8", newline="")
                if mutate.startswith("rehash"):
                    _resum(work)
            out = verify_export(work)
            self.assertFalse(out["valid"])
            self.assertTrue(any(p.startswith(problem) for p in out["problems"]), (mutate, out["problems"]))

    def test_exports_are_non_authoritative_and_carry_no_private_content(self):
        """IDs: S05-EXPORT-PRIVACY, S05-AUTHORITY. Every exported JSON/JSONL record and CSV row declares NON_AUTHORITATIVE; exports carry no PDF bytes, no free-text notes (only their SHA-256), no local paths and no fixture folder names; the handoff manifest lists what it is NOT (RV-FTE run, SEALED release, official PRISMA, authenticated identity)."""
        r = self.make_run(5)
        self.finish(r)
        with self.writer() as kb:
            out = export_run(kb, r["run_id"], os.path.join(self.tmp, "x"), operator="op.f")
        folder = Path(self.tmp, "x", out["folder_name"])
        for path in folder.iterdir():
            data = path.read_bytes()
            self.assertNotIn(b"%PDF-", data, path.name)
            for needle in (b"meets C1 and C2", b"non-randomized", self.tmp.encode(), b"fixture-root", b"Users\\"):
                self.assertNotIn(needle, data, (path.name, needle))
            if path.suffix == ".jsonl":
                for line in data.decode().splitlines():
                    self.assertEqual(json.loads(line).get("authority"), "NON_AUTHORITATIVE", path.name) if "seq" not in line else None
        rows = (folder / "candidates.csv").read_text(encoding="utf-8").splitlines()[1:]
        self.assertTrue(rows and all(r.startswith("NON_AUTHORITATIVE,") for r in rows))
        self.assertEqual(json.loads((folder / "summary.json").read_text(encoding="utf-8"))["authority"], "NON_AUTHORITATIVE")

    def test_out_of_band_protocol_change_is_refused(self):
        """IDs: S05-PROTOCOL-HASH. The frozen protocol is append-only (UPDATE refused by trigger); if its stored JSON is nevertheless altered out of band (trigger removed, criteria edited), every evidence attach, decision, adjudication and the lock refuse with PROTOCOL_HASH_CHANGED instead of silently applying the edited criteria."""
        import sqlite3
        r = self.make_run(2)
        c = r["candidates"]
        self.attach(c[0])
        with self.writer() as kb:
            with self.assertRaises(KBError):
                kb.execute("UPDATE fte_protocol SET label='edited' WHERE protocol_id=?", (r["protocol_id"],))
        con = sqlite3.connect(os.path.join(self.root, "db", "kb.sqlite"))
        edited = copy.deepcopy(PROTOCOL)
        edited["criteria"][0]["text"] = "edited after freezing"
        with con:
            con.execute("DROP TRIGGER fte_protocol_no_update")
            con.execute("UPDATE fte_protocol SET protocol_json=? WHERE protocol_id=?", (json.dumps(edited), r["protocol_id"]))
        con.close()
        for fn, kw in (("decide", dict(candidate_id=c[0]["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE")),
                       ("adjudicate", dict(candidate_id=c[0]["candidate_id"], adjudicator="adj.c", outcome="FT_INCLUDE", note="n")),
                       ("lock", dict(run_id=r["run_id"], operator="op.fte"))):
            with self.assertRaises(KBError) as ctx:
                self.svc_call(fn, **kw)
            self.assertEqual(ctx.exception.code, "PROTOCOL_HASH_CHANGED", fn)
        with self.assertRaises(KBError) as ctx:
            self.attach(c[1])
        self.assertEqual(ctx.exception.code, "PROTOCOL_HASH_CHANGED")

    def test_crash_during_create_or_decision_leaves_nothing_half_written(self):
        """IDs: S05-CRASH-ROLLBACK. A failure in the middle of run creation (after the run row, before INPUT_FROZEN) rolls the whole run back (no run, roster or candidate rows); a failure while recording a decision rolls that decision back; retrying afterwards works."""
        r = self.make_run(3)
        original = FTEService._event

        def boom(self, run_id, state, *a, **kw):
            if state in ("INPUT_FROZEN", "REVIEW_IN_PROGRESS"):
                raise RuntimeError("simulated crash")
            return original(self, run_id, state, *a, **kw)
        roster = [dict(e) for e in ROSTER] + [{"label": "rev.z", "role": "REVIEWER", "actor_type": "HUMAN"}]
        with mock.patch.object(FTEService, "_event", boom):
            with self.writer() as kb:
                before = {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("fte_run", "fte_reviewer", "fte_candidate", "fte_assignment")}
                with self.assertRaises(RuntimeError):
                    FTEService(kb).create_run(source_id=r["source"], export_dir=r["export"], expected_manifest_sha256=r["sem"], rv_verify_json=r["verify"],
                                              protocol_id=r["protocol_id"], roster=roster, label="crashy", operator="op.fte")
                self.assertEqual({t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in before}, before)
            self.attach(r["candidates"][0])
            with self.assertRaises(RuntimeError):
                self.svc_call("decide", candidate_id=r["candidates"][0]["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_decision")["n"], 0)
        self.assertTrue(self.svc_call("decide", candidate_id=r["candidates"][0]["candidate_id"], reviewer="rev.a", outcome="FT_INCLUDE")["created"])


def _resum(folder):
    """Re-write SHA256SUMS and the export manifest after an edit, so only the semantic checks can catch it."""
    from scivetra_kb.core.canonical import canonical_json, sha256_bytes
    root = Path(folder)
    manifest = json.loads((root / "export_manifest.json").read_text(encoding="utf-8"))
    for name in manifest["files"]:
        data = (root / name).read_bytes()
        manifest["files"][name] = {"sha256": sha256_bytes(data), "bytes": len(data)}
    (root / "export_manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="")
    files = sorted(p.name for p in root.iterdir() if p.name != "SHA256SUMS.txt")
    (root / "SHA256SUMS.txt").write_text("".join(f"{sha256_bytes((root / n).read_bytes())}  {n}\n" for n in files), encoding="utf-8", newline="")
