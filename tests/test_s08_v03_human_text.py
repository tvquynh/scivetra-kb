"""S08 v03 corrective tests (S08-F03): human-typed free text (review / adjudication rationales, reason codes, scope label and
reasons) under rights gates A4 (storage) and A5 (disclosure). Synthetic PDFs only, offline, no model."""
import json
import os
import unittest
from unittest import mock as umock

from tests.s08_helpers import MALWARE_DOC, S08Case
from scivetra_kb import cli
from scivetra_kb.core.errors import KBError
from scivetra_kb.evidence.excerpt import SourceIndex
from scivetra_kb.evidence.service import EvidenceService

PHRASE = "packed samples were excluded from the evaluation"
NOTE = "licence status unclear to me, I cannot judge this claim"
# Tables holding human-typed text or its by-products (machine quotes stored under gate A3 are governed by S08-F01, not here).
EV_TABLES = ("ev_reveal", "ev_review", "ev_adjudication", "ev_export_event", "ev_scope")


class HumanTextRights(S08Case):
    def deny_storage(self, asset_sha256):
        with self.writer() as kb:
            old = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (asset_sha256,))
            row = dict(old, assessment_id=kb.ids.new("rga"), supersedes_assessment_id=old["assessment_id"], store_locally="DENY",
                       local_process="ALLOW", assessed_by="OPERATOR:owner", recorded_by="OPERATOR:owner",
                       semantic_sha256=kb.ids.new("x").ljust(64, "0")[:64])
            with kb.transaction():
                kb.insert("rights_assessment", row)

    def world(self):
        run, items, scope, _ = self.mocked([[MALWARE_DOC]])
        draft = next(d for d in self.drafts(scope) if d["evidence_class"] == "LIMITATION")["draft_id"]
        self.roster("rev.one", "rev.two", "rev.adj")
        self.ecall("verify", draft_id=draft, operator="op.v")
        return run, items, scope, draft

    def cli(self, *argv):
        return cli.run(list(argv), clock=self.clock, ids=self.ids)

    def raw_rows(self):
        with self.reader() as kb:
            return json.dumps({t: kb.all(f"SELECT * FROM {t}") for t in EV_TABLES})

    def refused(self, fn):
        with self.assertRaises(KBError) as ctx:
            fn()
        return ctx.exception

    def test_excerpt_detection_policy(self):
        """IDs: S08-V03-EXCERPT-POLICY. rkb-excerpt-detect/1 matches exact and near-verbatim copies (case, spacing, punctuation,
        NFKC full-width forms, underscores of reason codes, a six-word window inside other text) and leaves own-words notes, short
        fragments (< 4 words) and paraphrases alone (documented limits)."""
        idx = SourceIndex(["Limitation: packed samples were excluded from the evaluation.", "ROC-AUC of 0.981 on the EMBER 2018 test split."])
        for text in (PHRASE, "PACKED  Samples, were excluded -- from the evaluation!", "ｐａｃｋｅｄ samples were excluded from the evaluation",
                     "PACKED_SAMPLES_WERE_EXCLUDED_FROM_THE", "I think that packed samples were excluded from the evaluation, so reject",
                     "roc auc of 0 981 on"):
            self.assertTrue(idx.contains_excerpt(text), text)
        for text in (NOTE, "independent judgement", "packed samples were", "the authors left out compressed binaries", "SOURCE_UNAVAILABLE"):
            self.assertFalse(idx.contains_excerpt(text), text)

    def test_a4_denied_refuses_copied_excerpts_and_keeps_own_words_review_usable(self):
        """IDs: S08-V03-A4-REVIEW-REFUSED, S08-V03-A4-ADJUDICATION-REFUSED, S08-V03-A4-REASON-CODE, S08-V03-OWN-WORDS-REVIEW, S08-V03-NO-LAUNDERING.
        Under A4 denial a review or adjudication whose rationale (or reason code) copies the source, exactly or near-verbatim, is
        refused with HUMAN_TEXT_SOURCE_EXCERPT_REFUSED (no row, message without the text); a REJECT with an own-words note and
        reason codes is stored with reviewer, decision, codes and timestamp and stays visible in audit; adjudication works with an
        own-words rationale. The phrase reaches no ev_* row, audit, show, state, summary, export, export history or CLI output."""
        run, items, scope, draft = self.world()
        self.deny_storage(items[0]["asset_sha256"])
        for r in ("rev.one", "rev.two", "rev.adj"):
            self.ecall("reveal", draft_id=draft, reviewer=r)
        for kw in (dict(rationale=PHRASE.upper() + "!"), dict(rationale="Reject: " + PHRASE + "."),
                   dict(rationale=NOTE, reason_codes=["SOURCE_UNAVAILABLE", "PACKED_SAMPLES_WERE_EXCLUDED_FROM_THE"])):
            kw.setdefault("reason_codes", ["SOURCE_UNAVAILABLE"])
            exc = self.refused(lambda: self.ecall("review", draft_id=draft, reviewer="rev.one", decision="REJECT", **kw))
            self.assertEqual(exc.code, "HUMAN_TEXT_SOURCE_EXCERPT_REFUSED")
            self.assertNotIn("packed", str(exc).lower())
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM ev_review")["n"], 0)
            s05_before = kb.one("SELECT count(*) n FROM fte_decision")["n"]
        out = self.ecall("review", draft_id=draft, reviewer="rev.one", decision="REJECT", reason_codes=["SOURCE_UNAVAILABLE"], rationale=NOTE)
        self.ecall("review", draft_id=draft, reviewer="rev.two", decision="REJECT", reason_codes=["WRONG_SCOPE"], rationale="not a limitation of the method")
        audit = self.eread("audit", draft_id=draft)
        first = audit["reviews"][0]
        self.assertEqual((first["reviewer"], first["decision"], json.loads(first["reason_codes_json"]), first["rationale"], first["rationale_disclosure"]),
                         ("rev.one", "REJECT", ["SOURCE_UNAVAILABLE"], NOTE, "SHOWN"))
        self.assertTrue(first["created_at_utc"].endswith("Z"))
        self.assertEqual(out["review_id"], first["review_id"])
        self.assertEqual(audit["human_text_policy"], "rkb-excerpt-detect/1")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM fte_decision")["n"], s05_before)
        out_dir = os.path.join(self.tmp, "exp")
        self.ecall("export", scope_id=scope, out_dir=out_dir, operator="op.owner")
        blobs = [self.raw_rows(), json.dumps(audit), json.dumps(self.eread("show", draft_id=draft)), json.dumps(self.eread("state", draft_id=draft)),
                 json.dumps(self.eread("scope_summary", scope_id=scope)), json.dumps(self.eread("export_history", scope_id=scope)),
                 json.dumps(self.cli("evidence", "audit", "--data-root", self.root, "--draft-id", draft)[1])]
        for root, _, files in os.walk(out_dir):
            blobs.extend(open(os.path.join(root, f), encoding="utf-8").read() for f in files)
        for blob in blobs:
            self.assertNotIn(PHRASE, blob.lower())

    def test_a4_denied_adjudication(self):
        """IDs: S08-V03-A4-ADJUDICATION-REFUSED, S08-V03-OWN-WORDS-ADJUDICATION. A disagreement after A4 denial: adjudication with a
        copied rationale is refused; with an own-words rationale it is recorded and visible."""
        run, items, scope, draft = self.world()
        self.ecall("reveal", draft_id=draft, reviewer="rev.one")
        self.ecall("review", draft_id=draft, reviewer="rev.one", decision="ACCEPT")
        self.ecall("reveal", draft_id=draft, reviewer="rev.two")
        self.ecall("review", draft_id=draft, reviewer="rev.two", decision="REJECT", reason_codes=["NOT_RELEVANT"], rationale="independent judgement")
        self.deny_storage(items[0]["asset_sha256"])
        self.ecall("reveal", draft_id=draft, reviewer="rev.adj")
        exc = self.refused(lambda: self.ecall("adjudicate", draft_id=draft, adjudicator="rev.adj", outcome="REJECT", rationale="Excluded: " + PHRASE))
        self.assertEqual(exc.code, "HUMAN_TEXT_SOURCE_EXCERPT_REFUSED")
        adj = self.ecall("adjudicate", draft_id=draft, adjudicator="rev.adj", outcome="REJECT", rationale="rev.two is right; the claim is not supported")
        self.assertFalse(adj["state"]["counts_as_accepted_evidence"])
        row = self.eread("audit", draft_id=draft)["adjudications"][0]
        self.assertEqual((row["adjudicator"], row["outcome"], row["rationale_disclosure"]), ("rev.adj", "REJECT", "SHOWN"))
        self.assertNotIn(PHRASE, self.raw_rows())

    def test_allowed_quote_is_audited_then_withheld_after_revocation(self):
        """IDs: S08-V03-A4-A5-ALLOWED, S08-V03-A5-REVIEW-WITHHELD, S08-V03-A5-ADJUDICATION-WITHHELD, S08-V03-A5-REASON-CODE, S08-V03-HISTORY-PRESERVED.
        While A4 / A5 allow, a reviewer may quote the source and the audit shows it with reviewer and draft identity. After the
        store-locally permission is revoked, SDK and CLI audit return those rationales and the excerpt reason code as WITHHELD
        (current A5 reason, SOURCE_EXCERPT_DETECTED, SHA-256, length) while own-words notes, decisions, reviewers, timestamps and
        the stored rows stay unchanged (append-only)."""
        run, items, scope, draft = self.world()
        self.ecall("reveal", draft_id=draft, reviewer="rev.one")
        self.ecall("review", draft_id=draft, reviewer="rev.one", decision="ACCEPT", rationale="The paper says: " + PHRASE)
        self.ecall("reveal", draft_id=draft, reviewer="rev.two")
        self.ecall("review", draft_id=draft, reviewer="rev.two", decision="REJECT", reason_codes=["NOT_RELEVANT", "PACKED_SAMPLES_WERE_EXCLUDED_FROM_THE"],
                   rationale="independent judgement")
        self.ecall("reveal", draft_id=draft, reviewer="rev.adj")
        self.ecall("adjudicate", draft_id=draft, adjudicator="rev.adj", outcome="ACCEPT", rationale=PHRASE)
        allowed = self.eread("audit", draft_id=draft)
        self.assertEqual(allowed["draft"]["draft_id"], draft)
        self.assertEqual([(r["reviewer"], r["rationale_disclosure"]) for r in allowed["reviews"]], [("rev.one", "SHOWN"), ("rev.two", "SHOWN")])
        self.assertIn(PHRASE, allowed["reviews"][0]["rationale"])
        self.assertEqual(allowed["adjudications"][0]["rationale"], PHRASE)
        before = self.raw_rows()
        self.deny_storage(items[0]["asset_sha256"])
        for audit in (self.eread("audit", draft_id=draft), self.cli("evidence", "audit", "--data-root", self.root, "--draft-id", draft)[1]):
            text = json.dumps(audit).lower()
            self.assertNotIn(PHRASE, text)
            self.assertNotIn("packed_samples", text)
            one, two = audit["reviews"]
            self.assertEqual((one["reviewer"], one["decision"], one["rationale"], one["rationale_disclosure"], one["rationale_chars"]),
                             ("rev.one", "ACCEPT", None, "WITHHELD", len("The paper says: " + PHRASE)))
            self.assertIn("GATE_A5_DENIED:STORE_LOCALLY_DENY", one["rationale_withheld_reasons"])
            self.assertIn("SOURCE_EXCERPT_DETECTED", one["rationale_withheld_reasons"])
            self.assertEqual(len(one["rationale_sha256"]), 64)
            self.assertEqual((two["rationale"], json.loads(two["reason_codes_json"])), ("independent judgement", ["NOT_RELEVANT", "WITHHELD_SOURCE_EXCERPT"]))
            adj = audit["adjudications"][0]
            self.assertEqual((adj["adjudicator"], adj["outcome"], adj["rationale"], adj["rationale_disclosure"]), ("rev.adj", "ACCEPT", None, "WITHHELD"))
        self.assertEqual(self.raw_rows().count(PHRASE), before.count(PHRASE))

    def test_unreadable_source_fails_closed(self):
        """IDs: S08-V03-FAIL-CLOSED. When the source text needed for the check cannot be read: under A4 denial any rationale is
        refused (HUMAN_TEXT_SOURCE_CHECK_UNAVAILABLE); under A5 denial every stored rationale is WITHHELD (SOURCE_CHECK_UNAVAILABLE)."""
        run, items, scope, draft = self.world()
        self.ecall("reveal", draft_id=draft, reviewer="rev.one")
        self.ecall("review", draft_id=draft, reviewer="rev.one", decision="REJECT", reason_codes=["WRONG_SCOPE"], rationale=NOTE)
        self.deny_storage(items[0]["asset_sha256"])
        with umock.patch.object(EvidenceService, "_source_index", return_value=None):
            audit = self.eread("audit", draft_id=draft)
            self.assertEqual(audit["reviews"][0]["rationale_disclosure"], "WITHHELD")
            self.assertIn("SOURCE_CHECK_UNAVAILABLE", audit["reviews"][0]["rationale_withheld_reasons"])
            self.ecall("reveal", draft_id=draft, reviewer="rev.two")
            exc = self.refused(lambda: self.ecall("review", draft_id=draft, reviewer="rev.two", decision="REJECT", reason_codes=["WRONG_SCOPE"], rationale=NOTE))
            self.assertEqual(exc.code, "HUMAN_TEXT_SOURCE_CHECK_UNAVAILABLE")
        self.assertEqual(self.eread("audit", draft_id=draft)["reviews"][0]["rationale"], NOTE)

    def test_scope_label_and_reasons_are_gated(self):
        """IDs: S08-V03-SCOPE-TEXT. A scope label or protocol reason that copies a member's source text is refused when A4 denies
        for that member; an own-words label is accepted."""
        def deny(pdf):
            self.deny_storage(pdf["asset_sha256"])
        run, items = self.ev_world([[MALWARE_DOC]], before_attach=deny)
        profile = self.profile()

        def scope(label, reason="synthetic test protocol: one reviewer"):
            return self.ecall("create_scope", fte_run_id=run["run_id"], profile_id=profile, operator="op.owner", label=label,
                              review_policy="SINGLE_HUMAN", review_policy_reason=reason,
                              members=[{"candidate_id": it["candidate"]["candidate_id"], "parse_artifact_id": it["artifact_id"]} for it in items])
        self.assertEqual(self.refused(lambda: scope(PHRASE)).code, "HUMAN_TEXT_SOURCE_EXCERPT_REFUSED")
        self.assertEqual(self.refused(lambda: scope("own label", "because " + PHRASE)).code, "HUMAN_TEXT_SOURCE_EXCERPT_REFUSED")
        self.assertTrue(scope("malware limitations scope")["created"])
        self.assertNotIn(PHRASE, self.raw_rows())


if __name__ == "__main__":
    unittest.main()
