"""S09 test helpers: synthetic S05 runs with parsed synthetic PDFs (S08 helpers), explicit retrieval scopes and a derived index.
Everything is synthetic: generated sentences, no network, no model."""
from tests.s08_helpers import S08Case
from scivetra_kb.retrieval.service import RetrievalService

RETRIEVAL_DOC = [
    "Objective: evaluate a static Windows PE malware detector on a public benchmark.",
    "The corpus contains 200,000 samples collected in 2018 from a commercial feed.",
    "TPR of 93.1% at 1% FPR on the EMBER 2018 test split of Win32 files.",
    "TPR of 80.2% at 0.1% FPR on the EMBER 2018 test split of Win32 files.",
    "ROC-AUC of 0.981 on the EMBER 2018 test split.",
    "Temporal holdout: trained on samples up to 2017 and tested on samples from 2018.",
    "Limitation: packed samples were excluded from the evaluation.",
]
OTHER_DOC = [
    "Objective: compare gradient boosted trees for Android permission analysis.",
    "Accuracy of 0.912 on the AndroZoo 2020 hold-out set with 5,000 applications.",
    "Limitation: obfuscated applications were not studied.",
]


class S09Case(S08Case):
    def rcall(self, method, **kw):
        with self.writer() as kb:
            return getattr(RetrievalService(kb), method)(**kw)

    def rread(self, method, **kw):
        with self.reader() as kb:
            return getattr(RetrievalService(kb), method)(**kw)

    def rscope(self, run, items, **kw):
        kw.setdefault("label", "synthetic retrieval scope")
        return self.rcall("create_scope", basis_kind="FTE_RUN", basis_id=run["run_id"], operator="op.owner",
                          members=[{"candidate_id": it["candidate"]["candidate_id"], "parse_artifact_id": it["artifact_id"]} for it in items],
                          **kw)["scope_id"]

    def rworld(self, docs=None, *, embedder="mock", before_attach=None, build=True):
        run, items = self.ev_world(docs or [[RETRIEVAL_DOC], [OTHER_DOC]], before_attach=before_attach)
        scope = self.rscope(run, items)
        built = self.rcall("build_index", operator="op.index", embedder=embedder) if build else None
        return run, items, scope, built

    def passages(self, scope, query, **kw):
        kw.setdefault("consumer", "LOCAL_CLI")
        return self.rcall("search_passages", scope_id=scope, query=query, operator="op.reader", **kw)

    def supersede_rights(self, asset_sha256, **changes):
        with self.writer() as kb:
            old = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (asset_sha256,))
            row = dict(old, assessment_id=kb.ids.new("rga"), supersedes_assessment_id=old["assessment_id"], assessed_by="OPERATOR:owner",
                       recorded_by="OPERATOR:owner", semantic_sha256=kb.ids.new("x").ljust(64, "0")[:64], **changes)
            with kb.transaction():
                kb.insert("rights_assessment", row)
