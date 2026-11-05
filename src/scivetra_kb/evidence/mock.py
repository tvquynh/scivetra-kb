"""Deterministic MOCK evidence extractor (LOCAL_PROCESS, tool-less). A test fixture, NOT a language model and NOT evidence of
any real model's quality.

``extract(rendered_prompt, pack, mode)`` sees only the rendered prompt and the pack (untrusted segment text + profile). It has no
filesystem, network, shell or KB access. Rules: fixed sentence patterns of the synthetic fixtures ("Study design: ...",
"TPR of 93.1% at 1% FPR on the EMBER 2018 test split", "95% confidence interval ...", ...) become typed records whose anchor is
the exact matched span; a profile class with no match for an uncertainty interval is reported NOT_REPORTED (never a zero).
Instructions inside the document text are data and are ignored. ``mode`` injects deliberate faults for tests and the pilot.
"""
from __future__ import annotations

import hashlib
import json
import re

MODEL = {"provider": "local", "model_id": "rkb-mock-evidence-extractor", "model_version": "1", "location": "LOCAL_PROCESS",
         "settings": {"deterministic": True}, "client": "scivetra_kb.evidence.mock"}
MODES = ("normal", "hallucinate_quote", "wrong_page", "shift_offset", "wrong_paper", "wrong_version", "inflate_number", "percent_from_decimal",
         "swap_operating_point", "pair_swap", "wrong_denominator", "wrong_dataset_year", "roc_for_pr", "fabricated_citation", "table_only",
         "schema_violation", "drop_unit", "json_number")
METRICS = {"tpr": "TPR", "roc-auc": "ROC_AUC", "pr-auc": "PR_AUC", "f1": "F1", "accuracy": "ACCURACY", "average precision": "AP"}
RESULT = re.compile(r"(?P<metric>TPR|ROC-AUC|PR-AUC|F1|accuracy|average precision) (?:of|was) (?P<value>[0-9][0-9.,]*?)(?P<pct>%)?"
                    r"(?: at (?P<op>[0-9][0-9.]*)% FPR)?(?: and (?P<value2>[0-9][0-9.,]*?)(?P<pct2>%)? at (?P<op2>[0-9][0-9.]*)% FPR)?"
                    r" on the (?P<dataset>[A-Z][A-Za-z0-9-]*) (?P<year>(?:19|20)[0-9]{2}) (?P<split>train|test|validation|challenge) split"
                    r"(?: of (?P<pe>Win32|Win64|\.NET) files)?")
SAMPLE = re.compile(r"(?P<n>[0-9][0-9,]*) (?P<what>samples|participants)")
CI = re.compile(r"95% confidence interval (?:of |from )?(?P<lo>[0-9][0-9.]*) to (?P<hi>[0-9][0-9.]*)")
LABELLED = {"Study design:": ("STUDY_DESIGN", "design_statement"), "Objective:": ("STUDY_OBJECTIVE", "objective"),
            "Limitation:": ("LIMITATION", "limitation"), "Licence:": ("ETHICS_LICENSING", "statement"), "Method:": ("METHOD", "method"),
            "Baseline:": ("BASELINE", "baseline"), "Leakage control:": ("LABELING_LEAKAGE_CONTROL", "control_statement"),
            "Temporal holdout:": ("SHIFT_CONDITION", "condition")}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _num(text: str) -> str:
    """The mock's naive reading of a number: thousands groups dropped, a comma otherwise read as a decimal point."""
    if re.fullmatch(r"[0-9]{1,3}(,[0-9]{3})+", text):
        return text.replace(",", "")
    return text.replace(",", ".").rstrip(".")


def _anchor(seg, i, j):
    quote = seg["text"][i:j]
    return {"page": seg["page"], "char_start": seg["start"] + i, "char_end": seg["start"] + j, "quote": quote, "quote_sha256": _sha(quote)}


def _record(cls, fields, anchors, status="REPORTED", confidence="MEDIUM", kind="FULLTEXT_TEXT"):
    return {"evidence_class": cls, "value_status": status, "fields": fields, "source_kind": kind, "confidence": confidence, "anchors": anchors, "note": ""}


def _results(seg, malware):
    out = []
    for m in RESULT.finditer(seg["text"]):
        anchor = _anchor(seg, m.start(), m.end())
        metric = METRICS[m.group("metric").lower()]
        pairs = [(m.group("value"), m.group("pct"), m.group("op"))]
        if m.group("value2"):
            pairs.append((m.group("value2"), m.group("pct2"), m.group("op2")))
        for value, pct, op in pairs:
            f = {"metric": metric if malware else m.group("metric"), "value": _num(value), "unit": "%" if pct else "ratio",
                 "dataset": m.group("dataset"), "dataset_year": m.group("year")}
            if malware:
                f["split"] = m.group("split").upper()
                if m.group("pe"):
                    f["pe_type"] = {"Win32": "WIN32", "Win64": "WIN64", ".NET": "DOTNET"}[m.group("pe")]
                if op:
                    f["fpr_operating_point"], f["fpr_operating_point_unit"] = op, "%"
            else:
                f["subset"] = m.group("split") + " split"
            out.append(_record("NUMERICAL_RESULT", f, [anchor], confidence="HIGH"))
    return out


def extract(rendered: str, pack: dict, mode: str = "normal") -> bytes:
    if mode not in MODES:
        raise ValueError("unknown mock mode")
    if "<<<UNTRUSTED_INPUT_PACK" not in rendered:
        raise ValueError("mock refuses an unrendered pack")
    classes = pack["profile"]["classes"]
    malware = isinstance(classes.get("NUMERICAL_RESULT", {}).get("fields", {}).get("metric", {}).get("values"), dict)
    records, ci_found = [], False
    for seg in pack["segments"]:
        text = seg["text"]
        for label, (cls, field) in LABELLED.items():
            if text.startswith(label) and cls in classes:
                body = text[len(label):].strip().rstrip(".")
                i = text.index(body)
                records.append(_record(cls, {field: body}, [_anchor(seg, i, i + len(body))]))
        if "NUMERICAL_RESULT" in classes:
            records.extend(_results(seg, malware))
        for m in SAMPLE.finditer(text):
            if "POPULATION_SAMPLE" in classes:
                records.append(_record("POPULATION_SAMPLE", {"sample_size": _num(m.group("n")), "sample_unit": m.group("what")},
                                       [_anchor(seg, m.start(), m.end())]))
        for m in CI.finditer(text):
            ci_found = True
            records.append(_record("UNCERTAINTY_INTERVAL", {"metric": "95% confidence interval", "level": "95", "level_unit": "%",
                                                            "lower": m.group("lo"), "upper": m.group("hi"), "unit": "ratio"},
                                   [_anchor(seg, m.start(), m.end())]))
    if "UNCERTAINTY_INTERVAL" in classes and not ci_found:
        records.append(_record("UNCERTAINTY_INTERVAL", {}, [], status="NOT_REPORTED", confidence="LOW"))
    _fault(mode, records, pack)
    doc = {"schema": "rkb-evidence-response/1", "input_pack_sha256": pack["pack_sha256"], "paper_id": pack["paper_id"],
           "document_version_id": pack["document_version_id"], "artifact_sha256": pack["artifact"]["artifact_sha256"],
           "profile_sha256": pack["profile"]["profile_sha256"], "records": records, "notes": "deterministic mock; not a language model"}
    if mode == "wrong_paper":
        doc["paper_id"] = "gp_" + "0" * 26
    if mode == "wrong_version":
        doc["document_version_id"] = "dv_" + "0" * 26
    if mode == "schema_violation":
        doc["unexpected"] = True
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    if mode == "json_number":
        text = re.sub(r'"value": "([0-9.]+)"', r'"value": \1', text, count=1)
    return text.encode("utf-8")


def _fault(mode, records, pack):
    results = [r for r in records if r["evidence_class"] == "NUMERICAL_RESULT"]
    anchored = [r for r in records if r["anchors"]]
    first = results[0] if results else (anchored[0] if anchored else None)
    if first is None:
        return
    a = first["anchors"][0]
    f = first["fields"]
    if mode == "hallucinate_quote":
        a["quote"] = "The detector achieved perfect results on every benchmark."
        a["quote_sha256"] = _sha(a["quote"])
    elif mode == "wrong_page":
        a["page"] += 1
    elif mode == "shift_offset":
        a["char_start"] += 1
        a["char_end"] += 1
    elif mode == "inflate_number" and "value" in f:
        f["value"] = str(round(float(f["value"]) + 1, 6)).rstrip("0").rstrip(".")
    elif mode == "percent_from_decimal" and "value" in f and f.get("unit") == "ratio":
        f["value"], f["unit"] = str(round(float(f["value"]) * 100, 6)).rstrip("0").rstrip("."), "%"
    elif mode == "swap_operating_point" and "fpr_operating_point" in f:
        f["fpr_operating_point"] = {"1": "0.1", "0.1": "1"}.get(f["fpr_operating_point"], "5")
    elif mode == "pair_swap" and len(results) >= 2 and "fpr_operating_point" in results[1]["fields"]:
        f["value"], results[1]["fields"]["value"] = results[1]["fields"]["value"], f["value"]
    elif mode == "wrong_denominator":
        f["denominator"] = "2000"
    elif mode == "wrong_dataset_year" and "dataset_year" in f:
        f["dataset_year"] = str(int(f["dataset_year"]) + 1)
    elif mode == "roc_for_pr" and f.get("metric") == "ROC_AUC":
        f["metric"] = "PR_AUC"
    elif mode == "fabricated_citation":
        quote = "as reported by Smith et al. (2031), Table 9"
        records.append(_record("LIMITATION", {"limitation": quote}, [{"page": 1, "char_start": 0, "char_end": len(quote), "quote": quote,
                                                                      "quote_sha256": _sha(quote)}]))
    elif mode == "table_only":
        records.append(_record(first["evidence_class"], dict(f), [dict(a)], kind="FULLTEXT_TABLE"))
    elif mode == "drop_unit" and "unit" in f:
        del f["unit"]
