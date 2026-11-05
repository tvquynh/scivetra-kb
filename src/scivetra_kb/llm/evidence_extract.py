"""S08 evidence extraction through the LLM router (ADR_LLM stage L2, LL-D14).

For every member's latest S08 input pack of an evidence scope:
1. the pack must still be current (S08 ``_pack_current``: identity, eligibility, rights A2 / A6, S06 artifact) - else SCOPE_DENIED;
2. the prompt is S08's own rendered template + UNTRUSTED pack (``EvidenceService.render``), unchanged;
3. S08 scopes allow LOCAL_PROCESS models only, so the data class is always LOCAL_ONLY: only ``data_policy = LOCAL`` providers on
   this machine are asked (remote providers need OAG-6 and S10, stage L3+);
4. the answer goes through ``EvidenceService.import_extraction``: strict schema validation, run and drafts recorded as
   OPERATOR_IMPORT / OPERATOR_DECLARED_UNVERIFIED with the router's provider and model as the declared model, NON_AUTHORITATIVE,
   HUMAN_REQUIRED. Structural verification (anchors, numbers, units) and human review are unchanged.
With ``consensus``, two local providers of different families must return the same records; otherwise nothing is imported and
the disagreement is reported.
"""
from __future__ import annotations

from ..core.canonical import canonical_json
from ..evidence.service import EvidenceService
from ..library.common import operator_label
from .router import json_object

DATA_CLASS = "LOCAL_ONLY"
MODEL_VERSION = "scivetra-llm-router/L2"


def _records_key(parsed: dict):
    """What two answers must share to AGREE: per record class, status, typed fields and anchor positions (not notes or confidence)."""
    recs = parsed.get("records") if isinstance(parsed, dict) else None
    if not isinstance(recs, list):
        return None
    out = []
    for r in recs:
        if not isinstance(r, dict):
            return None
        anchors = [(a.get("page"), a.get("char_start"), a.get("char_end")) for a in r.get("anchors", []) if isinstance(a, dict)]
        out.append(canonical_json([r.get("evidence_class"), r.get("value_status"), r.get("fields"), anchors]))
    return sorted(out)


def extract_scope(kb, *, scope_id: str, operator: str, router, consensus: bool = False) -> dict:
    operator_label(operator)
    ev = EvidenceService(kb)
    ev._scope(scope_id)
    results = []
    for prow in kb.all("SELECT p.* FROM ev_input_pack p WHERE p.scope_id=? AND p.rowid = (SELECT max(x.rowid) FROM ev_input_pack x "
                       "WHERE x.scope_id = p.scope_id AND x.paper_id = p.paper_id) ORDER BY p.paper_id", (scope_id,)):
        stale = ev._pack_current(prow)
        if stale:
            results.append({"paper_id": prow["paper_id"], "status": "SCOPE_DENIED", "reasons": stale})
            continue
        _, pack = ev._pack(prow["pack_id"])
        messages = [{"role": "user", "content": ev.render(pack)}]
        if consensus:
            out = router.consensus("s08.extract", messages, DATA_CLASS, validate=json_object,
                                   compare=lambda a, b: _records_key(a) is not None and _records_key(a) == _records_key(b))
            if out["status"] != "AGREED":
                results.append({"paper_id": prow["paper_id"], "status": "NOT_IMPORTED_" + out["status"],
                                "providers": [a["provider"] for a in out.get("answers", [])]})
                continue
            answer = out["answers"][0]
            agreed_by = [a["provider"] for a in out["answers"]]
        else:
            answer = router.complete("s08.extract", messages, DATA_CLASS, validate=json_object)
            if answer["status"] != "OK":
                results.append({"paper_id": prow["paper_id"], "status": "NO_MODEL_ANSWER", "attempts": answer["attempts"],
                                "retry_after_seconds": answer.get("retry_after_seconds")})
                continue
            agreed_by = None
        stored = ev.import_extraction(pack_id=prow["pack_id"], response=answer["text"].encode("utf-8"), operator=operator,
                                      declared_model={"provider": answer["provider"][:80], "model_id": answer["model"][:80], "model_version": MODEL_VERSION})
        results.append(dict(stored, paper_id=prow["paper_id"], provider=answer["provider"], model=answer["model"], agreed_by=agreed_by))
    return {"scope_id": scope_id, "data_class": DATA_CLASS, "consensus": consensus, "results": results,
            "authority": "NON_AUTHORITATIVE_PROPOSALS_HUMAN_REVIEW_REQUIRED"}
