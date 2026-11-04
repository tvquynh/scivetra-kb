"""S11 test helpers: synthetic S08 worlds (generated PDFs parsed by the official S06 parser, deterministic mock extraction,
structural verification, human S08 acceptance) and the S11 SynthesisService. Everything is synthetic: DOIs 10.99999/fte.*,
generated sentences, no network, no model. Quote canaries mark document text that must never reach S11 outputs."""
import json

from tests.s08_helpers import S08Case
from scivetra_kb.evidence.service import EvidenceService
from scivetra_kb.synthesis import rules as R
from scivetra_kb.synthesis.service import SynthesisService

QUOTE_CANARY = "pelicanquote"
STUDY_A = ["Objective: evaluate a static Windows PE malware detector pelicanquote.",
           "TPR of 93.1% at 1% FPR on the EMBER 2018 test split of Win32 files.",
           "TPR of 80.2% at 0.1% FPR on the EMBER 2018 test split of Win32 files.",
           "ROC-AUC of 0.981 on the EMBER 2018 test split.",
           "Limitation: packed samples were excluded from the evaluation pelicanquote."]
STUDY_B = ["Objective: evaluate a gradient boosted PE detector pelicanquote.",
           "TPR of 91.4% at 1% FPR on the EMBER 2018 test split of Win32 files.",
           "PR-AUC of 0.912 on the EMBER 2018 test split.",
           "Limitation: only Win32 executables were evaluated pelicanquote."]
STUDY_C = ["TPR of 96.8% at 1% FPR on the SOREL 2020 challenge split of Win64 files.",
           "Accuracy was reported with a 95% confidence interval 0.91 to 0.95 for the detector."]


class S11Case(S08Case):
    def scall(self, method, faults=(), **kw):
        with self.writer() as kb:
            return getattr(SynthesisService(kb, faults=faults), method)(**kw)

    def sread(self, method, **kw):
        with self.writer() as kb:          # status / audit re-derive evidence through S08 services (read-only towards S08)
            return getattr(SynthesisService(kb), method)(**kw)

    def accept_all(self, scope, *, skip=lambda d: False):
        for d in self.drafts(scope):
            self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
        for d in self.drafts(scope):
            if skip(d):
                continue
            st = self.eread("state", draft_id=d["draft_id"])
            if st["verification_state"] not in ("STRUCTURALLY_VERIFIED", "ABSENCE_RECORDED"):
                continue
            self.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
            codes = ["CONFLICT_ACKNOWLEDGED"] if st["conflicts_with"] else []
            self.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT", reason_codes=codes)

    def syn_world(self, docs, *, accept=True, skip=lambda d: False, review_policy="SINGLE_HUMAN", **kw):
        run, items, ev_scope, _ = self.mocked(docs, **kw)
        self.roster("rev.one", "rev.two", "rev.three")
        if accept:
            self.accept_all(ev_scope, skip=skip)
        self.run_, self.items, self.ev_scope = run, items, ev_scope
        reason = "synthetic test protocol: one reviewer" if review_policy == "SINGLE_HUMAN" else None
        self.syn = self.scall("create_scope", ev_scope_id=ev_scope, operator="op.synth", label="synthetic synthesis scope", review_policy=review_policy,
                              review_policy_reason=reason)["scope_id"]
        return self.syn

    def draft(self, item_index, cls="NUMERICAL_RESULT", **fields):
        paper = self.items[item_index]["candidate"]["paper_id"]
        for d in self.drafts(self.ev_scope):
            f = json.loads(d["typed_fields_json"])
            if d["paper_id"] == paper and d["evidence_class"] == cls and all(f.get(k) == v for k, v in fields.items()):
                return d["draft_id"]
        raise AssertionError(f"no draft {item_index} {cls} {fields}")

    def claim(self, key, kind, cites, statement=None, **spec):
        return self.scall("add_claim", scope_id=self.syn, claim_key=key, spec={"schema": R.SPEC_SCHEMA, "kind": kind, **spec}, cites=cites,
                          operator="op.author", statement=statement)

    def audit(self):
        return self.scall("audit", scope_id=self.syn, operator="op.auditor")

    def verdicts(self, report):
        return {c["claim_key"]: c["verdict"] for c in report["claims"]}

    def tpr(self, value, op="1", **extra):
        m = {"metric": "TPR", "value": value, "unit": "%", "fpr_operating_point": op, "fpr_operating_point_unit": "%", "dataset": "EMBER",
             "dataset_year": "2018", "split": "TEST", "pe_type": "WIN32"}
        m.update(extra)
        return {k: v for k, v in m.items() if v is not None}

    def ev_text(self) -> str:
        with self.reader() as kb:
            return json.dumps({t: kb.all(f"SELECT * FROM {t} ORDER BY rowid") for t in ("syn_scope", "syn_claim", "syn_snapshot", "syn_audit_run",
                                                                                         "syn_claim_audit", "syn_contradiction", "syn_review",
                                                                                         "syn_export_event", "syn_share_request")})


def ids_of(report):
    return {c["claim_key"]: c for c in report["claims"]}


__all__ = ["S11Case", "STUDY_A", "STUDY_B", "STUDY_C", "QUOTE_CANARY", "EvidenceService", "ids_of"]
