"""Deterministic MOCK screening model (LOCAL_PROCESS, tool-less). It is a test fixture, NOT a language model and NOT evidence
of any real model's quality.

``screen(rendered_prompt, pack, criteria, mode)`` sees only two strings/dicts: the rendered prompt and the frozen criteria. It
has no filesystem, network, shell or KB access. Rule: for each criterion, a ``not_met_any`` phrase in the untrusted text gives
NOT_MET, else a ``met_any`` phrase gives MET, else UNCLEAR; the anchor is the exact matched span. Any NOT_MET -> EXCLUDE with
that criterion's reason code; all MET -> INCLUDE; otherwise UNSURE. Instructions inside the document text are ignored
(they are matched as data like any other text). ``mode`` injects deliberate faults for tests (hallucinated quote, wrong page,
wrong paper, unsupported number, scope drift, schema violation).
"""
from __future__ import annotations

import hashlib
import json

MODEL = {"provider": "local", "model_id": "rkb-mock-screener", "model_version": "1", "location": "LOCAL_PROCESS",
         "settings": {"deterministic": True}, "client": "scivetra_kb.screening.mock"}
MODES = ("normal", "hallucinate_quote", "wrong_page", "wrong_paper", "unsupported_number", "scope_drift", "schema_violation", "unanchored")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find(items, phrases):
    for item in items:
        text = item.get("text") or item.get("value") or ""
        low = text.lower()
        for phrase in phrases:
            i = low.find(phrase.lower())
            if i >= 0:
                return item, i, i + len(phrase), text[i:i + len(phrase)]
    return None


def _anchor(item, i, j, quote):
    if item["kind"] == "PARSE_SEGMENT":
        return {"kind": "PARSE_SEGMENT", "ref": item["ref"], "page": item["page"], "char_start": item["start"] + i, "char_end": item["start"] + j,
                "quote": quote, "quote_sha256": _sha(quote)}
    return {"kind": "METADATA_CLAIM", "ref": item["ref"], "page": None, "char_start": i, "char_end": j, "quote": quote, "quote_sha256": _sha(quote)}


def screen(rendered_prompt: str, pack: dict, criteria: dict, mode: str = "normal") -> bytes:
    if mode not in MODES:
        raise ValueError("unknown mock mode")
    items = [i for i in pack["items"] if i.get("text") or i.get("value")]
    judged, reasons = [], []
    for c in criteria["criteria"]:
        hint = c.get("mock_hint") or {}
        hit = _find(items, hint.get("not_met_any", []))
        judgement = "NOT_MET" if hit else None
        if not hit:
            hit = _find(items, hint.get("met_any", []))
            judgement = "MET" if hit else "UNCLEAR"
        anchors = [_anchor(*hit)] if hit else []
        rationale = f"{judgement}: matched phrase in the untrusted input" if hit else "UNCLEAR: no supporting phrase found"
        if judgement == "NOT_MET" and c.get("exclude_reason_code"):
            reasons.append(c["exclude_reason_code"])
        judged.append({"criterion_id": c["id"], "judgement": judgement, "rationale": rationale, "anchors": anchors})
    outcome = "EXCLUDE" if any(j["judgement"] == "NOT_MET" for j in judged) else (
        "INCLUDE" if all(j["judgement"] == "MET" for j in judged) else "UNSURE")
    response = {"schema": "rkb-ai-response/1", "task_type": pack["task_type"], "paper_id": pack["paper_id"], "input_pack_sha256": pack["pack_sha256"],
                "suggested_outcome": outcome, "criteria": judged, "suggested_reason_codes": sorted(set(reasons)),
                "uncertainty": "LOW" if outcome != "UNSURE" else "HIGH", "notes": "mock rule-based output; not a language model"}
    first = next((j for j in judged if j["anchors"]), None)
    if mode == "hallucinate_quote" and first:
        a = first["anchors"][0]
        a["quote"] = a["quote"] + " (fabricated)"
        a["quote_sha256"] = _sha(a["quote"])
    elif mode == "wrong_page" and first and first["anchors"][0]["kind"] == "PARSE_SEGMENT":
        first["anchors"][0]["page"] += 1
    elif mode == "wrong_paper" and first:
        first["anchors"][0]["ref"] = first["anchors"][0]["ref"].replace(":", ":OTHER", 1)
    elif mode == "unsupported_number" and first:
        first["rationale"] = "Reported sample size was 1234 participants."
    elif mode == "scope_drift":
        response["paper_id"] = "gp_" + "0" * 26
    elif mode == "schema_violation":
        response["model_location"] = "REMOTE_API"
    elif mode == "unanchored":
        for j in judged:
            j["anchors"] = []
    return json.dumps(response, sort_keys=True, ensure_ascii=False).encode("utf-8")
