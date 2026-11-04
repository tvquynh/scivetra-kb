"""S11 scientific rules (``scivetra-synthesis-rubric/1``): pure data and pure functions, no I/O, no KB, no network, no model.

Typed claim specs (``scivetra-claim-spec/1``), the statement-vs-spec check, ComparisonKey / ComparabilityAssessment, the
deterministic claim-to-evidence support test, contradiction detection and comparison groups. Inputs are snapshot entries built by
``SynthesisService`` (references, typed non-text values and SHA-256 of normalized text dimensions; never quotes or document text).

Scientific stance (ADR_S11 SY-D04..SY-D07):
- a current, human-accepted S08 record is necessary, never sufficient: the claim must match it dimension by dimension;
- a missing dimension is never a wildcard: it is MISSING, and a claim that omits a context the evidence has is PARTIAL;
- numbers are compared as reported tokens (``12`` is not ``312``; ``12`` vs ``12.0`` is a precision difference, PARTIAL); units are
  never converted (``0.96`` ratio is not ``96%``); TPR@1%FPR is not TPR@0.1%FPR; ROC-AUC is not PR-AUC / AP;
- different studies with different values are NOT a contradiction; a contradiction is the same paper reporting different values
  for the same measurement key, an S08 CONFLICTED record, a claim the evidence contradicts, or a paper identity conflict;
- no pooling, no significance test, no causal inference, no majority vote; the only derived arithmetic is a logged difference
  (percentage points or relative percent) between two COMPARABLE reported values;
- thematic statements need a human support judgement (structural checks are not semantic entailment).

Rubric /2 (S11 v02, ADR_S11 SY-D16..SY-D18):
- the VISIBLE statement is checked, not only the hidden spec: signed numbers exactly (``-93.1`` is not ``93.1``), units next to
  each number, and a closed lexical vocabulary of datasets, metrics, splits, PE types, temporal and comparison words, and negation.
  Any inconsistency makes the claim UNSUPPORTED. This is a conservative lexical check, never semantic entailment;
- only a deterministic template statement can be machine-SUPPORTED. An author-supplied statement is at most PARTIAL until humans
  confirm that exact statement for the exact link graph (the statement SHA-256 is part of the graph);
- a COUNT is ascertained member by member: POSITIVE (current accepted matching evidence), NEGATIVE (current accepted NOT_REPORTED /
  NOT_APPLICABLE record for the criterion) or UNKNOWN (anything else: a missing extract is not NOT_REPORTED; unaccepted, stale or
  blocked evidence is unknown, never a negative). An exact count is SUPPORTED only when no member is UNKNOWN.
"""
from __future__ import annotations

import hashlib
import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from ..core.canonical import canonical_json, semantic_sha256
from ..evidence.schema import DECIMAL, INTEGER, normalize_text, numeric_mentions
from ..screening.schema import _LEAKS

RUBRIC_ID = "scivetra-synthesis-rubric/2"
SPEC_SCHEMA = "scivetra-claim-spec/1"
ENGINE = "scivetra-synthesis-engine/1"          # pinned by a CHECK in migration 0011; behaviour is versioned by the rubric hash
VERDICTS = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "MISSING_EVIDENCE", "STALE_SOURCE", "BLOCKED_RIGHTS", "CONFLICTING")
KINDS = ("REPORTED_VALUE", "COMPARISON", "ABSENCE", "THEMATIC", "COUNT")
ELIGIBLE = "ELIGIBLE_NON_AUTHORITATIVE_DRAFT"

# Measurement dimensions of a NUMERICAL_RESULT (those present in the profile are used). Text dimensions are compared through the
# SHA-256 of their normalized form; numeric ones as reported decimal strings; enums exactly.
DIMENSIONS = ("metric", "value", "unit", "fpr_operating_point", "fpr_operating_point_unit", "dataset", "dataset_year", "split", "pe_type",
              "subset", "condition", "temporal_holdout", "denominator", "seeds", "model_version", "comparator")
NUMERIC = ("value", "fpr_operating_point", "dataset_year", "denominator", "seeds")
TEXT = ("dataset", "subset", "condition", "temporal_holdout", "model_version", "comparator", "metric_text")
CORE = ("metric", "unit", "dataset", "dataset_year", "split")          # must be EQUAL (both present) for any comparison
OPERATING_POINT_METRICS = ("TPR", "FPR", "RECALL", "PRECISION")       # these need the FPR operating point and its unit too
COMPARED_NOT_CONTEXT = ("value", "model_version", "comparator")         # what a comparison compares; not part of its context
ABSENCE_STATUSES = ("NOT_REPORTED", "NOT_APPLICABLE", "NOT_EXTRACTABLE")
DIFFERENCE_UNITS = ("PERCENTAGE_POINTS", "RELATIVE_PERCENT")
RELATIONS = ("HIGHER", "LOWER", "EQUAL")
FLAGS = ("asserts_significance", "asserts_causality", "asserts_pooled_estimate")
MAX_STATEMENT = 600

SIGNIFICANCE = re.compile(r"(?i)\b(significant(ly)?|statistically|p\s*[<=>≤]|p-value|confidence level)\b")
CAUSAL = re.compile(r"(?i)\b(causes?|caused|causing|leads? to|led to|results? in|due to|because of|drives?|attributable)\b")
POOLED = re.compile(r"(?i)\b(pooled|on average|overall (mean|average)|meta-analy\w*|weighted (mean|average)|across all studies|average of)\b")
COMPARATIVE = re.compile(r"(?i)\b(improv\w*|outperform\w*|better|worse|higher|lower|exceed\w*|superior|inferior|beats?|gain|drop|than|versus|vs|"
                         r"compared (to|with)|underperform\w*)\b")
NEGATION = re.compile(r"(?i)\b(not|no|never|neither|nor|none|without|fails? to|cannot|can't|doesn't|didn't|isn't|wasn't|absent)\b")
TEMPORAL = re.compile(r"(?i)\b(earlier|later|older|newer|future|chronolog\w*|temporal\w*|time[- ](split|aware|based|ordered)|concept drift|drift)\b"
                      r"|\b(before|after|prior to|since|until)\s+(19|20)\d\d\b")
# Closed vocabularies for the lexical statement check (rkb-statement-lexical/1). Order matters: longer names are matched (and
# masked) first, so "average precision" is not also read as "precision" and "ROC-AUC" not as a bare "AUC".
METRIC_TERMS = (("PR_AUC", r"\bpr[- _]?auc\b|\bauprc\b|\bauc[- _]?pr\b|\baverage precision\b|\barea under the (pr|precision[- ]recall) curve\b"),
                ("ROC_AUC", r"\broc[- _]?auc\b|\bauroc\b|\bauc[- _]?roc\b|\barea under the (roc|receiver operating\w*) curve\b"),
                ("TPR", r"\btpr\b|\btrue[- ]positive rate\b|\bdetection rate\b|\bsensitivity\b|\brecall\b"),
                ("FPR", r"\bfpr\b|\bfalse[- ]positive rate\b"),
                ("FNR", r"\bfnr\b|\bfalse[- ]negative rate\b|\bmiss rate\b"),
                ("TNR", r"\btnr\b|\btrue[- ]negative rate\b|\bspecificity\b"),
                ("PRECISION", r"\bprecision\b|\bppv\b"),
                ("ACCURACY", r"\baccuracy\b"),
                ("F1", r"\bf1\b|\bf[- ]?score\b|\bf[- ]?measure\b"),
                ("MCC", r"\bmcc\b|\bmatthews\b"),
                ("AUC_UNQUALIFIED", r"\bauc\b"))
METRIC_SYNONYMS = {"RECALL": "TPR", "SENSITIVITY": "TPR", "DETECTION_RATE": "TPR", "TRUE_POSITIVE_RATE": "TPR", "AUROC": "ROC_AUC", "AUPRC": "PR_AUC",
                   "AP": "PR_AUC", "AVERAGE_PRECISION": "PR_AUC", "F1_SCORE": "F1", "SPECIFICITY": "TNR"}
DATASET_TERMS = (("SOREL", r"\bsorel\b"), ("EMBER", r"\bember\b"), ("BODMAS", r"\bbodmas\b"), ("DREBIN", r"\bdrebin\b"), ("ANDROZOO", r"\bandrozoo\b"),
                 ("VIRUSSHARE", r"\bvirusshare\b"), ("VIRUSTOTAL", r"\bvirustotal\b"), ("MALIMG", r"\bmalimg\b"), ("BIG2015", r"\bbig[- ]?2015\b"),
                 ("CICMALMEM", r"\bcic[- ]?malmem\w*\b"), ("MOTIF", r"\bmotif\b"))
SPLIT_TERMS = (("TRAIN", r"\btrain(ing)?\b"), ("TEST", r"\btest(ing)?\b|\bheld[- ]out\b|\bholdout\b"), ("VALIDATION", r"\bvalidation\b|\bval\b|\bdev(elopment)?\b"),
               ("CHALLENGE", r"\bchallenge\b"))
SPLIT_CONTEXT = r"\s+(split|set|data|partition|subset|fold|corpus|portion)\b"
PE_TERMS = (("WIN64", r"\bwin(dows)?[- ]?64\b|\bpe32\+|\bpe64\b|\bx64\b|\b64[- ]bit\b"), ("WIN32", r"\bwin(dows)?[- ]?32\b|\bpe32\b|\bx86\b|\b32[- ]bit\b"),
            ("DOTNET", r"\.net\b|\bdotnet\b|\bclr\b"), ("ELF", r"\belf\b"), ("APK", r"\bapk\b|\bandroid\b"))
RELATION_TERMS = (("HIGHER", r"\b(higher|better|outperform\w*|exceed\w*|superior|greater|more than|improv\w*|beats?|gain)\b"),
                  ("LOWER", r"\b(lower|worse|underperform\w*|inferior|less than|smaller|drop|decrease\w*|reduc\w*)\b"),
                  ("EQUAL", r"\b(equal|same as|identical|on par|tied?)\b"))
MINUS_VARIANTS = str.maketrans({c: "-" for c in "‐‑‒—➖﹣－"})   # U+2212 / U+2013 are read by the tokenizer
HARD_STATEMENT_CODES = ("STATEMENT_INVALID", "STATEMENT_LEAK_OR_URL")   # refused at creation; everything else is judged at audit time

RUBRIC = {"rubric": RUBRIC_ID, "spec_schema": SPEC_SCHEMA, "engine": ENGINE, "verdicts": list(VERDICTS), "kinds": list(KINDS),
          "dimensions": list(DIMENSIONS), "numeric": list(NUMERIC), "core": list(CORE), "operating_point_metrics": list(OPERATING_POINT_METRICS),
          "compared_not_context": list(COMPARED_NOT_CONTEXT), "number_policy": "reported decimal tokens; 12 != 312; 12 vs 12.0 = PRECISION_DIFFERS",
          "units": "never converted", "missing_dimension": "MISSING, never a wildcard", "pooling": "NONE", "significance": "requires accepted test evidence",
          "causality": "never established by S11", "derived": "difference of two comparable reported values only (pp or relative %), logged",
          "contradiction": ["SAME_PAPER_SAME_KEY_DIFFERENT_VALUE", "DOCUMENT_VERSION_VALUE_MISMATCH", "S08_VALUE_STATUS_CONFLICTED",
                            "CLAIM_CONTRADICTED_BY_EVIDENCE", "PAPER_IDENTITY_CONFLICT"],
          "thematic": "PARTIAL until a human confirms support for the exact link graph", "markers": {"SUPPORTED": "[n]", "CONFLICTING": "[CONFLICTED]",
                                                                                                         "other": "[UNVERIFIED]"},
          "statement": {"checked": "visible statement vs spec at every audit: signed numbers, units, lexical vocabularies, negation, temporal and "
                                   "comparison words (rkb-statement-lexical/1; not semantic entailment); any inconsistency = UNSUPPORTED",
                        "machine_supported_origin": "DETERMINISTIC_TEMPLATE only",
                        "author_supplied": "at most PARTIAL until the review policy's humans confirm the exact statement for the exact link graph",
                        "vocabularies": {"metric": [k for k, _ in METRIC_TERMS], "dataset": [k for k, _ in DATASET_TERMS], "split": [k for k, _ in SPLIT_TERMS],
                                         "pe_type": [k for k, _ in PE_TERMS], "relation": [k for k, _ in RELATION_TERMS]},
                        "sign": "exact; - U+2212 U+2013 U+2010 U+2011 U+2012 U+2014 U+FE63 U+FF0D read as minus"},
          "count": "member-level ascertainment POSITIVE / NEGATIVE (accepted NOT_REPORTED or NOT_APPLICABLE for the criterion) / UNKNOWN; "
                   "UNKNOWN is never a negative; exact count SUPPORTED only with zero UNKNOWN members",
          "disclosure": "statements and spec text shown only while gate A5 allows every pinned source or they contain no excerpt of a denied source"}
RUBRIC_SHA256 = semantic_sha256(RUBRIC)


class SpecInvalid(ValueError):
    def __init__(self, codes):
        super().__init__(",".join(codes))
        self.codes = sorted(set(codes))


def text_hash(value: str) -> str:
    return "h:" + hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


# -- claim specs ------------------------------------------------------------------------------------------------------------
def _measurement(m, codes, *, require_value=True, where="measurement"):
    if not isinstance(m, dict) or not m or len(m) > len(DIMENSIONS):
        codes.append(f"{where.upper()}_INVALID")
        return {}
    out = {}
    for k, v in m.items():
        if k not in DIMENSIONS:
            codes.append(f"UNKNOWN_DIMENSION:{str(k)[:30]}")
            continue
        if not isinstance(v, str) or not v.strip() or len(v) > 120 or _LEAKS.search(v):
            codes.append(f"DIMENSION_VALUE_INVALID:{k}")
            continue
        if k in NUMERIC and not (DECIMAL if k in ("value", "fpr_operating_point") else INTEGER).fullmatch(v):
            codes.append(f"NUMBER_FORMAT_INVALID:{k}")          # "93,1", "1e3", " 12" are refused; never re-interpreted
            continue
        out[k] = v
    if require_value and not {"metric", "value", "unit"} <= set(out):
        codes.append(f"{where.upper()}_NEEDS_METRIC_VALUE_UNIT")
    if "fpr_operating_point" in out and "fpr_operating_point_unit" not in out:
        codes.append("OPERATING_POINT_UNIT_MISSING")           # a unit is never inferred
    return dict(sorted(out.items()))


def validate_spec(spec, n_cites: int) -> dict:
    """Normalized spec or SpecInvalid. Exact keys per kind; strict decimal strings; known dimensions only."""
    codes = []
    if not isinstance(spec, dict) or spec.get("schema") != SPEC_SCHEMA or spec.get("kind") not in KINDS:
        raise SpecInvalid(["SPEC_SCHEMA_OR_KIND_INVALID"])
    kind = spec["kind"]
    allowed = {"schema", "kind", *FLAGS} | {"REPORTED_VALUE": {"measurement"}, "COMPARISON": {"relation", "difference"},
                                            "ABSENCE": {"evidence_class", "status"}, "THEMATIC": {"evidence_class", "topic"},
                                            "COUNT": {"count", "denominator", "criterion"}}[kind]
    extra = set(spec) - allowed
    if extra:
        codes.append("SPEC_KEYS_INVALID:" + ",".join(sorted(str(e)[:30] for e in extra)))
    out = {"schema": SPEC_SCHEMA, "kind": kind}
    for f in FLAGS:
        if f in spec:
            if not isinstance(spec[f], bool):
                codes.append("FLAG_INVALID:" + f)
            elif spec[f]:
                out[f] = True
    if kind == "REPORTED_VALUE":
        out["measurement"] = _measurement(spec.get("measurement"), codes)
        if n_cites < 1:
            codes.append("CITES_REQUIRED")
    elif kind == "COMPARISON":
        if spec.get("relation") not in RELATIONS:
            codes.append("RELATION_INVALID")
        out["relation"] = spec.get("relation")
        if "difference" in spec:
            d = spec["difference"]
            if not isinstance(d, dict) or set(d) != {"value", "unit"} or d.get("unit") not in DIFFERENCE_UNITS or not isinstance(d.get("value"), str) \
                    or not DECIMAL.fullmatch(d["value"]) or d["value"].startswith("-"):
                codes.append("DIFFERENCE_INVALID")
            else:
                out["difference"] = dict(d)
        if n_cites != 2:
            codes.append("COMPARISON_NEEDS_EXACTLY_TWO_CITES")
    elif kind in ("ABSENCE", "THEMATIC"):
        cls = spec.get("evidence_class")
        if not isinstance(cls, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,59}", cls):
            codes.append("EVIDENCE_CLASS_INVALID")
        out["evidence_class"] = cls
        if kind == "ABSENCE":
            if spec.get("status") not in ABSENCE_STATUSES:
                codes.append("ABSENCE_STATUS_INVALID")
            out["status"] = spec.get("status")
        elif "topic" in spec:
            if not isinstance(spec["topic"], str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,59}", spec["topic"]):
                codes.append("TOPIC_INVALID")
            else:
                out["topic"] = spec["topic"]
        if n_cites < 1:
            codes.append("CITES_REQUIRED")
    else:
        for k in ("count", "denominator"):
            v = spec.get(k)
            if not isinstance(v, str) or not INTEGER.fullmatch(v) or v.startswith("-"):
                codes.append(k.upper() + "_INVALID")
            out[k] = v
        crit = spec.get("criterion")
        if not isinstance(crit, dict) or set(crit) - {"evidence_class", "measurement"} or not isinstance(crit.get("evidence_class"), str):
            codes.append("CRITERION_INVALID")
        else:
            out["criterion"] = {"evidence_class": crit["evidence_class"]}
            if "measurement" in crit:
                out["criterion"]["measurement"] = _measurement(crit["measurement"], codes, require_value=False, where="criterion")
    if codes:
        raise SpecInvalid(codes)
    return out


def spec_numbers(spec) -> set:
    nums = set()
    for m in (spec.get("measurement"), (spec.get("criterion") or {}).get("measurement")):
        for k, v in (m or {}).items():
            if k in NUMERIC:
                nums.add(v)
    for v in ((spec.get("difference") or {}).get("value"), spec.get("count"), spec.get("denominator")):
        if v:
            nums.add(v)
    return nums


def _number_units(spec: dict, compared=(), context=None) -> dict:
    """Signed number token -> the unit kinds it may carry in a statement ('%', 'PP' or 'NONE'). ``compared``: (value, unit) of the
    two reported results of a COMPARISON; ``context``: the dimensions they share."""
    out = {}

    def put(v, kind):
        if v:
            out.setdefault(v, set()).add(kind)
    for m in (spec.get("measurement"), (spec.get("criterion") or {}).get("measurement"), context if spec["kind"] == "COMPARISON" else None):
        m = m or {}
        for k, v in m.items():                       # numbers inside a stated text value (e.g. a dataset named "EMBER2018-v2")
            if k not in NUMERIC and isinstance(v, str) and not v.startswith("h:"):
                for x in numeric_mentions(v.translate(MINUS_VARIANTS)):
                    put(x["canonical"], "NONE")
        put(m.get("value"), "%" if m.get("unit") == "%" else "NONE")
        put(m.get("fpr_operating_point"), "%" if m.get("fpr_operating_point_unit") == "%" else "NONE")
        for k in ("dataset_year", "denominator", "seeds"):
            put(m.get(k), "NONE")
    for v, unit in compared:
        put(v, "%" if unit == "%" else "NONE")
    d = spec.get("difference")
    if d:
        put(d["value"], "PP" if d["unit"] == "PERCENTAGE_POINTS" else "%")
    put(spec.get("count"), "NONE")
    put(spec.get("denominator"), "NONE")
    return out


def _terms(text: str, table) -> list:
    """Canonical keys of vocabulary terms mentioned in ``text`` (matched spans are masked, longest-first table order)."""
    found, low = [], text.casefold()
    for key, rx in table:
        if re.search(rx, low):
            found.append(key)
            low = re.sub(rx, " ", low)
    return found


def _canon_metric(value: str) -> str:
    k = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return METRIC_SYNONYMS.get(k, k)


def _canon_with(value: str, table) -> set:
    return set(_terms(value, table)) or {re.sub(r"[^A-Z0-9]+", "", value.upper())}


def statement_checks(statement: str, spec: dict, *, compared=(), context: dict | None = None) -> list:
    """Codes why the VISIBLE statement says more than, or other than, its typed spec; empty = no inconsistency found. Checked:
    signed numbers (exactly), the unit beside each number, and closed lexical vocabularies (dataset, metric, split, PE type,
    temporal and comparison words, negation) against the spec measurement (or, for a COMPARISON, ``context``: the dimensions both
    compared results share). ``compared``: (value, unit) of the compared results. A conservative lexical check, never semantic
    entailment: an empty result does not make an author statement machine-verified. The statement is untrusted text: nothing in it
    is executed or followed."""
    codes = []
    if not isinstance(statement, str) or not statement.strip() or len(statement) > MAX_STATEMENT or "\x00" in statement:
        return ["STATEMENT_INVALID"]
    if _LEAKS.search(statement) or re.search(r"https?://", statement):
        codes.append("STATEMENT_LEAK_OR_URL")
    text = statement.translate(MINUS_VARIANTS)
    units = _number_units(spec, compared, context)
    for m in numeric_mentions(text):
        c = m["canonical"]
        if c is None:
            codes.append("STATEMENT_NUMBER_AMBIGUOUS:" + m["raw"][:20])
            continue
        if c not in units:
            other = c[1:] if c.startswith("-") else "-" + c
            codes.append(("STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:" if other in units else "STATEMENT_NUMBER_NOT_IN_SPEC:") + c[:20])
            continue
    for m in re.finditer(r"(?<![0-9A-Za-z.,])[+\-−–]?\d+(?:\.\d+)?(?![0-9])(?![.,]\d)", text):
        tok = m.group(0).replace("−", "-").replace("–", "-").lstrip("+")
        if tok not in units:
            continue
        after = text[m.end():m.end() + 24].lower()
        kind = "PP" if re.match(r"\s?(pp\b|p\.p\.|percentage[- ]points?\b|points?\b)", after) else (
            "%" if re.match(r"\s?(%|percent\b|per cent\b)", after) else "NONE")
        if kind not in units[tok]:
            codes.append("STATEMENT_UNIT_CONTRADICTS_SPEC:" + tok[:20])
    for rx, flag in ((SIGNIFICANCE, "asserts_significance"), (CAUSAL, "asserts_causality"), (POOLED, "asserts_pooled_estimate")):
        if rx.search(text) and not spec.get(flag):
            codes.append("STATEMENT_NEEDS_FLAG:" + flag)
    kind = spec["kind"]
    if kind != "COMPARISON" and COMPARATIVE.search(text):
        codes.append("STATEMENT_COMPARATIVE_WITHOUT_COMPARISON_SPEC")
    if kind in ("REPORTED_VALUE", "COMPARISON", "COUNT") and NEGATION.search(text):
        codes.append("STATEMENT_NEGATION_NOT_IN_SPEC")
    meas = spec.get("measurement") or (spec.get("criterion") or {}).get("measurement") or (context if kind == "COMPARISON" else None)
    if meas is not None and kind != "THEMATIC" and kind != "ABSENCE":
        codes += _categorical_checks(text, meas)
    if kind == "COMPARISON":
        said = _terms(text, RELATION_TERMS)
        if any(r != spec["relation"] for r in said):
            codes.append("STATEMENT_RELATION_CONTRADICTS_SPEC:" + ",".join(sorted(set(said))))
    return sorted(set(codes))


def _categorical_checks(text: str, meas: dict) -> list:
    codes = []
    said = [m for m in _terms(text, METRIC_TERMS) if m != "AUC_UNQUALIFIED"]
    want = {_canon_metric(meas["metric"])} if meas.get("metric") else set()
    if meas.get("fpr_operating_point"):
        want.add("FPR")
    for m in said:
        if not want:
            codes.append("STATEMENT_CONTEXT_NOT_IN_SPEC:metric")
        elif m not in want:
            codes.append("STATEMENT_METRIC_CONTRADICTS_SPEC:" + m)
    if "AUC_UNQUALIFIED" in _terms(text, METRIC_TERMS) and meas.get("metric") and _canon_metric(meas["metric"]) not in ("ROC_AUC", "PR_AUC"):
        codes.append("STATEMENT_METRIC_CONTRADICTS_SPEC:AUC")
    for dim, table in (("dataset", DATASET_TERMS), ("pe_type", PE_TERMS)):
        said = _terms(text, table)
        if said and dim not in meas:
            codes.append("STATEMENT_CONTEXT_NOT_IN_SPEC:" + dim)
        elif said and meas[dim].startswith("h:"):            # a text dimension known only by its hash (COMPARISON context)
            rx = dict(table)
            codes += [f"STATEMENT_{dim.upper()}_CONTRADICTS_SPEC:{s}" for s in said
                      if meas[dim] not in {text_hash(s)} | {text_hash(x.group(0)) for x in re.finditer(rx[s], text.casefold())}]
        elif said:
            want = _canon_with(meas[dim], table) if meas[dim] != "WITHHELD" else set()
            codes += [f"STATEMENT_{dim.upper()}_CONTRADICTS_SPEC:{s}" for s in said if s not in want]
    said = [k for k, rx in SPLIT_TERMS if re.search("(" + rx + ")" + SPLIT_CONTEXT, text.casefold())]
    if said and "split" not in meas:
        codes.append("STATEMENT_CONTEXT_NOT_IN_SPEC:split")
    elif said:
        codes += [f"STATEMENT_SPLIT_CONTRADICTS_SPEC:{s}" for s in said if s != re.sub(r"[^A-Z]+", "", meas["split"].upper())]
    if TEMPORAL.search(text) and "temporal_holdout" not in meas:
        codes.append("STATEMENT_CONTEXT_NOT_IN_SPEC:temporal_holdout")
    return codes


def template_statement(spec: dict) -> str:
    """A deterministic statement from the typed spec (no text taken from any source)."""
    k = spec["kind"]
    if k == "REPORTED_VALUE":
        m = spec["measurement"]
        s = f"{m['metric']} of {m['value']}{'%' if m['unit'] == '%' else ''}"
        if "fpr_operating_point" in m:
            s += f" at {m['fpr_operating_point']}{'%' if m['fpr_operating_point_unit'] == '%' else ''} FPR"
        ctx = " ".join(m[d] for d in ("dataset", "dataset_year") if d in m)
        if ctx:
            s += " on " + ctx
        if "split" in m:
            s += f" ({m['split'].lower()} split"
            s += f", {m['pe_type']})" if "pe_type" in m else ")"
        return "The study reports " + s + "."
    if k == "ABSENCE":
        return f"The study does not report a {spec['evidence_class'].lower().replace('_', ' ')} ({spec['status']})."
    if k == "THEMATIC":
        return f"The cited studies report {spec['evidence_class'].lower().replace('_', ' ')} evidence" + (
            f" on {spec['topic'].lower().replace('_', ' ')}." if spec.get("topic") else ".")
    if k == "COUNT":
        return f"{spec['count']} of {spec['denominator']} pinned studies report {spec['criterion']['evidence_class'].lower().replace('_', ' ')} evidence matching the stated criterion."
    rel = {"HIGHER": "higher than", "LOWER": "lower than", "EQUAL": "equal to"}[spec["relation"]]
    s = f"Under the same reported evaluation conditions, the first cited result is {rel} the second"
    if spec.get("difference"):
        s += f" (difference {spec['difference']['value']} {'percentage points' if spec['difference']['unit'] == 'PERCENTAGE_POINTS' else 'percent relative'})"
    return s + "."


# -- dimensions and comparability -------------------------------------------------------------------------------------------
def entry_dims(entry: dict) -> dict:
    """The measurement dimensions of a snapshot entry: typed values, text dimensions as hashes ('WITHHELD' if not stored)."""
    out = {k: v for k, v in entry["fields"].items() if k in DIMENSIONS}
    out.update({k: v for k, v in entry["text_dims"].items() if k in DIMENSIONS})
    return out


def _dim_compare(k, claimed, have, *, metric_is_text=False):
    if have is None:
        return "MISSING"
    if have == "WITHHELD":
        return "WITHHELD"
    c = text_hash(claimed) if (k in TEXT or (k == "metric" and metric_is_text)) else claimed
    if c == have:
        return "EQUAL"
    if k in NUMERIC:
        try:
            if Decimal(claimed) == Decimal(have):
                return "PRECISION_DIFFERS"
        except InvalidOperation:
            pass
    return "DIFFERENT"


def key_dims(dims: dict, profile_dims) -> list:
    """Context dimensions that matter for this measurement (profile dimensions minus what a comparison compares)."""
    return [d for d in DIMENSIONS if d in profile_dims and d not in COMPARED_NOT_CONTEXT]


def required_core(dims: dict, profile_dims) -> list:
    core = [d for d in CORE if d in profile_dims]
    if "split" not in profile_dims and "subset" in profile_dims:
        core.append("subset")
    if dims.get("metric") in OPERATING_POINT_METRICS:
        core += ["fpr_operating_point", "fpr_operating_point_unit"]
    return core


def comparability(a: dict, b: dict, profile_dims) -> dict:
    """ComparabilityAssessment of two measurements (entry dims). COMPARABLE only when every core dimension is present and EQUAL
    in both and no other context dimension differs or is stated on one side only."""
    per, notes = {}, []
    core = sorted(set(required_core(a, profile_dims)) | set(required_core(b, profile_dims)))
    for d in key_dims(a, profile_dims):
        x, y = a.get(d), b.get(d)
        if "WITHHELD" in (x, y):
            per[d] = "WITHHELD"
        elif x is None and y is None:
            per[d] = "MISSING_BOTH"
        elif x is None or y is None:
            per[d] = "MISSING_ONE_SIDE"
        else:
            per[d] = "EQUAL" if x == y else "DIFFERENT"
    blocking = [f"{'NOT_COMPARABLE' if per[d] == 'DIFFERENT' else 'NOT_ESTABLISHED'}:{d}" for d in core if per.get(d) != "EQUAL"]
    blocking += [f"NOT_COMPARABLE:{d}" for d, s in per.items() if s == "DIFFERENT" and d not in core]
    partial = [f"CONTEXT_INCOMPLETE:{d}" for d, s in per.items() if s in ("MISSING_ONE_SIDE", "WITHHELD") and d not in core]
    for d in ("seeds", "denominator"):
        if per.get(d) == "MISSING_BOTH":
            notes.append(f"{d.upper()}_NOT_REPORTED")
    if per.get("pe_type") == "MISSING_BOTH":
        notes.append("PE_TYPE_NOT_REPORTED")
    state = "NOT_COMPARABLE" if blocking else ("PARTIALLY_COMPARABLE" if partial else "COMPARABLE")
    return {"state": state, "dimensions": per, "blocking": sorted(set(blocking)), "incomplete": sorted(set(partial)), "notes": sorted(notes),
            "significance": "NOT_ASSESSED_NO_TEST", "pooling": "NOT_PERFORMED"}


def context_key(dims: dict, profile_dims) -> str:
    return semantic_sha256({d: dims.get(d) for d in key_dims(dims, profile_dims)})


def full_key(dims: dict, profile_dims) -> str:
    return semantic_sha256({d: dims.get(d) for d in DIMENSIONS if d in profile_dims and d != "value"})


# -- support test -----------------------------------------------------------------------------------------------------------
def _measurement_link(spec_m, entry, profile_dims, metric_is_text):
    dims = entry_dims(entry)
    per = {k: _dim_compare(k, v, dims.get(k), metric_is_text=metric_is_text) for k, v in spec_m.items()}
    reasons = [f"MISMATCH:{k}" for k, s in per.items() if s == "DIFFERENT"]
    partial = [f"EVIDENCE_LACKS:{k}" for k, s in per.items() if s == "MISSING"]
    partial += [f"TEXT_DIMENSION_WITHHELD:{k}" for k, s in per.items() if s == "WITHHELD"]
    partial += [f"REPORTED_PRECISION_DIFFERS:{k}" for k, s in per.items() if s == "PRECISION_DIFFERS"]
    # A claim must not drop a context the evidence states (e.g. TPR without its 1% FPR operating point).
    omitted = [d for d in required_core(dims, profile_dims) + ["pe_type", "temporal_holdout", "subset"] if d in dims and d not in spec_m
               and d in profile_dims]
    partial += [f"CLAIM_OMITS_CONTEXT:{d}" for d in sorted(set(omitted))]
    result = "MISMATCH" if reasons else ("PARTIAL" if partial else "MATCH")
    return {"result": result, "dimensions": per, "reasons": sorted(set(reasons + partial))}


def _same_paper_conflicts(entry, eligible, profile_dims):
    """Eligible entries of the same paper with the same full measurement key and a different value."""
    if entry["evidence_class"] != "NUMERICAL_RESULT" or entry["value_status"] != "REPORTED":
        return []
    mine = entry_dims(entry)
    key = full_key(mine, profile_dims)
    out = []
    for e in eligible:
        if e["draft_id"] == entry["draft_id"] or e["paper_id"] != entry["paper_id"] or e["evidence_class"] != "NUMERICAL_RESULT":
            continue
        d = entry_dims(e)
        if full_key(d, profile_dims) == key and d.get("value") != mine.get("value"):
            out.append(e["draft_id"])
    return sorted(out)


def _crit_dims(crit, e, pdims, metric_is_text) -> dict:
    if "measurement" not in crit:
        return {}
    return _measurement_link(crit["measurement"], e, pdims, metric_is_text)["dimensions"]


def _count_match(crit, e, pdims, metric_is_text) -> bool:
    """Current, accepted, REPORTED evidence of the criterion class whose every criterion dimension is EQUAL."""
    return (e["category"] == "ELIGIBLE" and e["evidence_class"] == crit["evidence_class"] and e["value_status"] == "REPORTED"
            and all(s == "EQUAL" for s in _crit_dims(crit, e, pdims, metric_is_text).values()))


def _count_negative(crit, e, pdims, metric_is_text) -> bool:
    """Current, accepted NOT_REPORTED / NOT_APPLICABLE record of the criterion class whose criterion context (all criterion
    dimensions except value and unit) is EQUAL: an explicit, human-accepted negative for this member. NOT_EXTRACTABLE is not one."""
    if e["category"] != "ELIGIBLE" or e["evidence_class"] != crit["evidence_class"] or e["value_status"] not in ("NOT_REPORTED", "NOT_APPLICABLE"):
        return False
    return all(s == "EQUAL" for k, s in _crit_dims(crit, e, pdims, metric_is_text).items() if k not in ("value", "unit"))


def count_ascertainment(crit, entries, members, pdims, metric_is_text) -> dict:
    """Member-level ascertainment of a COUNT criterion: {paper_id: {state: POSITIVE|NEGATIVE|UNKNOWN, reasons}}. UNKNOWN is never a
    negative: a missing extract is not NOT_REPORTED, and unaccepted, stale or blocked evidence that could match is unknown."""
    out = {}
    for p in members:
        mine = [e for e in entries.values() if e["paper_id"] == p and e["evidence_class"] == crit["evidence_class"]]
        could = [e for e in mine if e["category"] != "ELIGIBLE" and all(s != "DIFFERENT" for s in _crit_dims(crit, e, pdims, metric_is_text).values())]
        if any(_count_match(crit, e, pdims, metric_is_text) for e in mine):
            out[p] = {"state": "POSITIVE", "reasons": []}
        elif could:
            out[p] = {"state": "UNKNOWN", "reasons": sorted({"POSSIBLY_MATCHING_EVIDENCE_NOT_USABLE:" + e["category"] for e in could})}
        elif any(_count_negative(crit, e, pdims, metric_is_text) for e in mine):
            out[p] = {"state": "NEGATIVE", "reasons": []}
        elif any(e["category"] == "ELIGIBLE" and all(s != "DIFFERENT" for s in _crit_dims(crit, e, pdims, metric_is_text).values()) for e in mine):
            out[p] = {"state": "UNKNOWN", "reasons": ["MATCH_UNDETERMINED_MISSING_WITHHELD_OR_INDETERMINATE"]}
        else:
            out[p] = {"state": "UNKNOWN", "reasons": ["NO_ASCERTAINMENT_RECORD_MISSING_EXTRACT_IS_NOT_NOT_REPORTED"]}
    return out


AUTHOR_CAP = "AUTHOR_STATEMENT_NEEDS_HUMAN_EXACT_STATEMENT_CONFIRMATION"
THEMATIC_CAP = "HUMAN_SUPPORT_JUDGEMENT_REQUIRED_STRUCTURAL_MATCH_IS_NOT_ENTAILMENT"


def evaluate_claim(claim: dict, entries: dict, ctx: dict) -> dict:
    """Verdict of one claim (see ``_evaluate``), then the statement-origin rule: an AUTHOR_SUPPLIED statement whose evidence test
    is SUPPORTED stays PARTIAL until the review policy's humans confirm that exact statement for the exact link graph; a human
    dispute makes a SUPPORTED or PARTIAL claim UNSUPPORTED. ``human_judgement``: REQUIRED (confirmation possible now), CONFIRMED,
    DISPUTED or NOT_APPLICABLE."""
    res = _evaluate(claim, entries, ctx)
    if claim["kind"] == "THEMATIC" or claim.get("statement_origin") != "AUTHOR_SUPPLIED":
        return res
    human = ctx.get("human", {}).get(claim.get("claim_id"))
    if human == "DISPUTED" and res["verdict"] in ("SUPPORTED", "PARTIAL"):
        return _reverdict(res, "UNSUPPORTED", ["HUMAN_DISPUTED_EXACT_AUTHOR_STATEMENT"], "DISPUTED")
    if res["verdict"] != "SUPPORTED":
        return res
    if human == "CONFIRMED":
        return _reverdict(res, "SUPPORTED", ["HUMAN_CONFIRMED_EXACT_AUTHOR_STATEMENT"], "CONFIRMED")
    return _reverdict(res, "PARTIAL", [AUTHOR_CAP], "REQUIRED")


def _reverdict(res, verdict, extra, judgement):
    out = dict(res, verdict=verdict, reasons=sorted(set(res["reasons"]) | set(extra)), human_judgement=judgement)
    out["marker"] = "CITED" if verdict == "SUPPORTED" else ("[CONFLICTED]" if verdict == "CONFLICTING" else "[UNVERIFIED]")
    return out


def _evaluate(claim: dict, entries: dict, ctx: dict) -> dict:
    """Evidence verdict of one claim. ``claim``: kind, spec, cites (draft ids), statement_sha256, statement_codes (from
    ``statement_checks`` of the visible statement). ``entries``: snapshot entries by draft id (all of the scope). ``ctx``:
    profile_dims, metric_is_text, members (paper ids pinned), human ({claim_id: CONFIRMED|DISPUTED|None}), identity conflicts
    (paper ids). Precedence: no evidence -> MISSING_EVIDENCE; any cited source blocked -> BLOCKED_RIGHTS; stale -> STALE_SOURCE;
    not currently accepted -> MISSING_EVIDENCE; statement inconsistent with the spec or unsupported qualifiers -> UNSUPPORTED; then
    the kind-specific test."""
    spec, cites = claim["spec"], claim["cites"]
    links, reasons, comp, deriv = [], [], None, None
    extra = {"human_judgement": "NOT_APPLICABLE", "count_ascertainment": None}
    for did in cites:
        e = entries.get(did)
        if e is None:
            links.append({"draft_id": did, "eligibility": "NOT_IN_PINNED_SCOPE", "result": "NOT_USABLE"})
            continue
        links.append({"draft_id": did, "draft_semantic_sha256": e["draft_semantic_sha256"], "paper_id": e["paper_id"],
                      "document_version_id": e["document_version_id"], "asset_sha256": e["asset_sha256"], "artifact_sha256": e["artifact_sha256"],
                      "anchors": e["anchors"], "evidence_class": e["evidence_class"], "value_status": e["value_status"],
                      "eligibility": e["eligibility"], "eligibility_reasons": e["eligibility_reasons"], "result": "PENDING"})

    def done(verdict, why):
        for l in links:
            if l["result"] == "PENDING":
                l["result"] = "NOT_EVALUATED"
        marker = "CITED" if verdict == "SUPPORTED" else ("[CONFLICTED]" if verdict == "CONFLICTING" else "[UNVERIFIED]")
        out = {"verdict": verdict, "marker": marker, "reasons": sorted(set(why)), "links": links, "comparability": comp, "derivation": deriv, **extra}
        out["link_graph_sha256"] = semantic_sha256({"spec": spec, "statement_sha256": claim.get("statement_sha256"),
                                                    "links": [{k: l.get(k) for k in ("draft_id", "draft_semantic_sha256", "anchors", "eligibility")} for l in links]})
        return out

    if not cites:
        return done("MISSING_EVIDENCE", ["NO_CITED_EVIDENCE"])
    if any(l["eligibility"] == "NOT_IN_PINNED_SCOPE" for l in links):
        return done("MISSING_EVIDENCE", ["CITED_EVIDENCE_NOT_IN_PINNED_SCOPE"])
    usable = [entries[l["draft_id"]] for l in links]
    for cat, verdict in (("BLOCKED", "BLOCKED_RIGHTS"), ("STALE", "STALE_SOURCE"), ("NOT_ACCEPTED", "MISSING_EVIDENCE")):
        bad = [e for e in usable if e["category"] == cat]
        if bad:
            for l in links:
                if entries[l["draft_id"]]["category"] == cat:
                    l["result"] = "NOT_USABLE"
            return done(verdict, [f"{cat}:{r}" for e in bad for r in e["eligibility_reasons"]] or [cat])
    eligible = [e for e in entries.values() if e["category"] == "ELIGIBLE"]
    if claim.get("statement_codes"):
        reasons += ["STATEMENT_NOT_CONSISTENT_WITH_SPEC"] + list(claim["statement_codes"])
    if spec.get("asserts_causality"):
        reasons.append("CAUSAL_CLAIM_NOT_ESTABLISHED_BY_SYNTHESIS")
    if spec.get("asserts_pooled_estimate"):
        reasons.append("POOLING_NOT_PERFORMED")
    if spec.get("asserts_significance"):
        tests = [e for e in usable if e["evidence_class"] == "STATISTICAL_COMPARISON" and e["value_status"] == "REPORTED"
                 and {"test", "p_value"} <= set(e["fields"]) | set(e["text_dims"])]
        if not tests:
            reasons.append("SIGNIFICANCE_NOT_TESTED_IN_CITED_EVIDENCE")
    if reasons:
        return done("UNSUPPORTED", reasons)
    kind, pdims = claim["kind"], ctx["profile_dims"]

    if kind == "REPORTED_VALUE":
        keys, results = set(), []
        for l, e in zip(links, usable):
            if e["evidence_class"] != "NUMERICAL_RESULT" or e["value_status"] != "REPORTED":
                l.update(result="MISMATCH", reasons=["NOT_A_REPORTED_NUMERICAL_RESULT"])
                results.append("MISMATCH")
                continue
            r = _measurement_link(spec["measurement"], e, pdims, ctx.get("metric_is_text", False))
            l.update(result=r["result"], dimensions=r["dimensions"], reasons=r["reasons"])
            results.append(r["result"])
            keys.add(context_key(entry_dims(e), pdims))
            reasons += r["reasons"]
        if len(keys) > 1:
            return done("UNSUPPORTED", ["MIXED_SETTINGS_IN_ONE_CLAIM"])
        if "MISMATCH" in results:
            conflicting = [e for e in usable if e["evidence_class"] == "NUMERICAL_RESULT" and
                           all(s != "DIFFERENT" for k, s in _measurement_link(spec["measurement"], e, pdims, ctx.get("metric_is_text", False))["dimensions"].items() if k != "value")
                           and _dim_compare("value", spec["measurement"]["value"], entry_dims(e).get("value")) == "DIFFERENT"]
            if conflicting and len(conflicting) == len(usable):
                reasons.append("CLAIMED_VALUE_CONTRADICTED_BY_CITED_EVIDENCE")
            return done("UNSUPPORTED", reasons)
        conflicts = sorted({c for e in usable for c in _same_paper_conflicts(e, eligible, pdims)} |
                           {c for e in usable for c in e.get("s08_conflicts_with", []) if entries.get(c, {}).get("category") == "ELIGIBLE"})
        if conflicts:
            return done("CONFLICTING", reasons + ["SAME_MEASUREMENT_DIFFERENT_VALUE_IN_PAPER:" + ",".join(conflicts)])
        if any(e["paper_id"] in ctx.get("identity_conflicts", ()) for e in usable):
            return done("PARTIAL", reasons + ["PAPER_IDENTITY_CONFLICT_UNRESOLVED"])
        return done("PARTIAL" if "PARTIAL" in results else "SUPPORTED", reasons)

    if kind == "ABSENCE":
        for l, e in zip(links, usable):
            if e["evidence_class"] != spec["evidence_class"]:
                l.update(result="MISMATCH", reasons=["EVIDENCE_CLASS_DIFFERS"])
            elif e["value_status"] != spec["status"]:
                l.update(result="MISMATCH", reasons=["ABSENCE_STATUS_DIFFERS:" + e["value_status"]])
            else:
                l.update(result="MATCH")
        if any(l["result"] == "MISMATCH" for l in links):
            return done("UNSUPPORTED", [r for l in links for r in l.get("reasons", [])])
        papers = {e["paper_id"] for e in usable}
        reported = [e["draft_id"] for e in eligible if e["paper_id"] in papers and e["evidence_class"] == spec["evidence_class"]
                    and e["value_status"] == "REPORTED"]
        if reported:
            return done("CONFLICTING", ["SAME_PAPER_REPORTS_VALUES_FOR_CLASS:" + ",".join(sorted(reported))])
        return done("SUPPORTED", [])

    if kind == "THEMATIC":
        for l, e in zip(links, usable):
            ok = e["evidence_class"] == spec["evidence_class"] and e["value_status"] == "REPORTED"
            l.update(result="STRUCTURAL_MATCH" if ok else "MISMATCH", reasons=[] if ok else ["EVIDENCE_CLASS_OR_STATUS_DIFFERS"])
        if any(l["result"] == "MISMATCH" for l in links):
            return done("UNSUPPORTED", ["THEMATIC_EVIDENCE_CLASS_MISMATCH"])
        human = ctx.get("human", {}).get(claim.get("claim_id"))
        if human == "DISPUTED":
            extra["human_judgement"] = "DISPUTED"
            return done("UNSUPPORTED", ["HUMAN_DISPUTED_SUPPORT"])
        if human == "CONFIRMED":
            extra["human_judgement"] = "CONFIRMED"
            return done("SUPPORTED", ["HUMAN_CONFIRMED_SUPPORT_FOR_EXACT_LINK_GRAPH"])
        extra["human_judgement"] = "REQUIRED"
        return done("PARTIAL", [THEMATIC_CAP])

    if kind == "COUNT":
        crit = spec["criterion"]
        members = ctx["members"]
        if spec["denominator"] != str(len(members)):
            return done("UNSUPPORTED", [f"DENOMINATOR_NOT_VALIDATED:pinned_members={len(members)}"])
        table = count_ascertainment(crit, entries, members, pdims, ctx.get("metric_is_text", False))
        extra["count_ascertainment"] = table
        positive = sorted(p for p, s in table.items() if s["state"] == "POSITIVE")
        unknown = sorted(p for p, s in table.items() if s["state"] == "UNKNOWN")
        cited_positive = sorted({e["paper_id"] for e in usable if _count_match(crit, e, pdims, ctx.get("metric_is_text", False))})
        for l, e in zip(links, usable):
            if _count_match(crit, e, pdims, ctx.get("metric_is_text", False)):
                l.update(result="MATCH")
            elif _count_negative(crit, e, pdims, ctx.get("metric_is_text", False)):
                l.update(result="ASCERTAINED_NEGATIVE")
            else:
                l.update(result="MISMATCH")
        # Independence: one paper counts once; papers in an identity conflict are not independent.
        dup = len(usable) - len({e["paper_id"] for e in usable})
        why = [f"DUPLICATE_SOURCE_COUNTED_ONCE:{dup}"] if dup else []
        tally = f"positive={len(positive)};negative={len(members) - len(positive) - len(unknown)};unknown={len(unknown)}"
        why += sorted({f"COUNT_MEMBER_UNKNOWN:{r}" for p in unknown for r in table[p]["reasons"]})
        if any(p in ctx.get("identity_conflicts", ()) for p in positive):
            return done("PARTIAL", why + ["PAPER_IDENTITY_CONFLICT_INDEPENDENCE_UNKNOWN", "COUNT_ASCERTAINMENT:" + tally])
        n = int(spec["count"])
        if n < len(positive) or n > len(positive) + len(unknown):
            return done("UNSUPPORTED", why + ["COUNT_CONTRADICTED_BY_ASCERTAINED_MEMBERS:" + tally])
        if n != len(positive):
            return done("UNSUPPORTED", why + ["COUNT_NOT_ESTABLISHED_UNKNOWN_MEMBERS_ARE_NOT_POSITIVES:" + tally])
        if any(l["result"] == "MISMATCH" for l in links):
            return done("UNSUPPORTED", why + ["COUNT_CITES_EVIDENCE_THAT_IS_NEITHER_POSITIVE_NOR_ASCERTAINED_NEGATIVE:" + tally])
        if cited_positive != positive:
            return done("UNSUPPORTED", why + [f"COUNT_CITES_DO_NOT_COVER_POSITIVES:cited={len(cited_positive)};" + tally])
        if unknown:
            return done("PARTIAL", why + ["COUNT_LOWER_BOUND_ONLY_UNKNOWN_MEMBERS_NOT_COUNTED_AS_NEGATIVE:" + tally])
        return done("SUPPORTED", why + ["COUNT_ASCERTAINMENT_COMPLETE:" + tally])

    # COMPARISON
    left, right = usable
    for l, e in zip(links, usable):
        if e["evidence_class"] != "NUMERICAL_RESULT" or e["value_status"] != "REPORTED":
            l.update(result="MISMATCH", reasons=["NOT_A_REPORTED_NUMERICAL_RESULT"])
    if any(l["result"] == "MISMATCH" for l in links):
        return done("UNSUPPORTED", ["COMPARISON_NEEDS_TWO_REPORTED_NUMERICAL_RESULTS"])
    a, b = entry_dims(left), entry_dims(right)
    comp = comparability(a, b, pdims)
    for l in links:
        l["result"] = comp["state"]
    if left["paper_id"] == right["paper_id"] and full_key(a, pdims) == full_key(b, pdims):
        return done("CONFLICTING", ["SAME_PAPER_SAME_MEASUREMENT_TWO_VALUES"] if a.get("value") != b.get("value") else ["SAME_MEASUREMENT_CITED_TWICE"])
    internal = sorted({c for e in usable for c in _same_paper_conflicts(e, eligible, pdims)})
    if internal:
        return done("CONFLICTING", ["A_COMPARED_PAPER_REPORTS_ANOTHER_VALUE_FOR_THE_SAME_MEASUREMENT:" + ",".join(internal)])
    if comp["state"] == "NOT_COMPARABLE":
        return done("UNSUPPORTED", comp["blocking"] + ["DIFFERENCE_NOT_INTERPRETABLE_WITHOUT_COMPARABLE_CONDITIONS"])
    x, y = Decimal(a["value"]), Decimal(b["value"])
    actual = "HIGHER" if x > y else ("LOWER" if x < y else "EQUAL")
    if actual != spec["relation"]:
        return done("UNSUPPORTED", [f"RELATION_CONTRADICTED_BY_EVIDENCE:actual={actual}"])
    why = list(comp["incomplete"])
    if spec.get("difference"):
        d = spec["difference"]
        if d["unit"] == "PERCENTAGE_POINTS":
            if a.get("unit") != "%" or b.get("unit") != "%":
                return done("UNSUPPORTED", why + ["PERCENTAGE_POINTS_NEED_PERCENT_VALUES"])
            raw, formula = abs(x - y), "|left_value - right_value| (percentage points)"
        else:
            if y == 0:
                return done("UNSUPPORTED", why + ["RELATIVE_CHANGE_UNDEFINED_ZERO_BASE"])
            raw, formula = abs(x - y) / y * 100, "|left_value - right_value| / right_value * 100 (relative percent)"
        exp = Decimal(d["value"]).as_tuple().exponent
        rounded = raw.quantize(Decimal(1).scaleb(exp), rounding=ROUND_HALF_EVEN)
        deriv = {"kind": "DERIVED", "formula": formula, "operands": {"left": {"draft_id": left["draft_id"], "value": a["value"], "unit": a.get("unit")},
                                                                    "right": {"draft_id": right["draft_id"], "value": b["value"], "unit": b.get("unit")}},
                 "exact": format(raw.normalize(), "f"), "rounding": f"ROUND_HALF_EVEN to {-exp} decimals", "rounded": format(rounded, "f"),
                 "claimed": d["value"], "reported_vs_derived": "DERIVED_BY_S11_NOT_REPORTED_BY_AUTHORS"}
        if format(rounded, "f") != d["value"]:
            return done("UNSUPPORTED", why + [f"DERIVED_DIFFERENCE_MISMATCH:computed={deriv['rounded']}"])
        why.append("DERIVED_VALUE_LOGGED")
    if comp["state"] == "PARTIALLY_COMPARABLE":
        return done("PARTIAL", why)
    if any(e["paper_id"] in ctx.get("identity_conflicts", ()) for e in usable):
        return done("PARTIAL", why + ["PAPER_IDENTITY_CONFLICT_UNRESOLVED"])
    return done("SUPPORTED", why + comp["notes"])


# -- contradictions and comparison groups ---------------------------------------------------------------------------------------
def contradictions(entries: list, ctx: dict) -> list:
    """ContradictionRecords among ELIGIBLE entries (never resolved by vote) plus paper identity conflicts."""
    out, seen = [], set()
    eligible = [e for e in entries if e["category"] == "ELIGIBLE"]
    pdims = ctx["profile_dims"]
    for e in eligible:
        if e["value_status"] == "CONFLICTED":
            out.append({"kind": "S08_VALUE_STATUS_CONFLICTED", "key_sha256": semantic_sha256({"draft": e["draft_id"]}), "draft_ids": [e["draft_id"]],
                        "claim_ids": [], "detail": {"paper_id": e["paper_id"], "evidence_class": e["evidence_class"]}})
        others = _same_paper_conflicts(e, eligible, pdims)
        if others:
            group = tuple(sorted([e["draft_id"], *others]))
            if group in seen:
                continue
            seen.add(group)
            versions = {x["document_version_id"] for x in eligible if x["draft_id"] in group}
            out.append({"kind": "DOCUMENT_VERSION_VALUE_MISMATCH" if len(versions) > 1 else "SAME_PAPER_SAME_KEY_DIFFERENT_VALUE",
                        "key_sha256": full_key(entry_dims(e), pdims), "draft_ids": list(group), "claim_ids": [],
                        "detail": {"paper_id": e["paper_id"], "values": sorted({entry_dims(x).get("value") for x in eligible if x["draft_id"] in group}),
                                   "resolution": "NOT_RESOLVED_BY_S11_HUMAN_JUDGEMENT_REQUIRED"}})
    for doi, papers in sorted(ctx.get("doi_collisions", {}).items()):
        out.append({"kind": "PAPER_IDENTITY_CONFLICT", "key_sha256": semantic_sha256({"doi": doi}), "draft_ids": [], "claim_ids": [],
                    "detail": {"papers": sorted(papers), "basis": "SAME_ACTIVE_DOI_ON_DIFFERENT_PAPERS",
                               "resolution": "MANUAL: rkb library conflicts / resolve (ADR_S11 SY-D09); not independent until resolved"}})
    return sorted(out, key=lambda c: (c["kind"], c["key_sha256"], c["draft_ids"]))


def comparison_groups(entries: list, ctx: dict) -> list:
    """Eligible reported NUMERICAL_RESULT records grouped by their full context key. Values of different studies are listed side by
    side, never pooled, averaged or ranked; spread across independent studies is annotated, not called a contradiction."""
    pdims, groups = ctx["profile_dims"], {}
    for e in entries:
        if e["category"] != "ELIGIBLE" or e["evidence_class"] != "NUMERICAL_RESULT" or e["value_status"] != "REPORTED":
            continue
        d = entry_dims(e)
        groups.setdefault(context_key(d, pdims), []).append(e)
    out = []
    for key, members in sorted(groups.items()):
        d0 = entry_dims(members[0])
        papers = sorted({m["paper_id"] for m in members})
        notes = []
        if len(papers) > 1 and len({entry_dims(m).get("value") for m in members}) > 1:
            notes.append("VALUES_DIFFER_ACROSS_INDEPENDENT_STUDIES_NOT_A_CONTRADICTION")
        missing = [x for x in required_core(d0, pdims) if d0.get(x) in (None, "WITHHELD")]
        if missing:
            notes.append("CORE_DIMENSION_NOT_ESTABLISHED:" + ",".join(missing))
        for x in ("seeds", "denominator"):
            if x in pdims and all(entry_dims(m).get(x) is None for m in members):
                notes.append(x.upper() + "_NOT_REPORTED")
        if not any(entry_dims(m).get("comparator") for m in members):
            notes.append("BASELINE_NOT_IDENTIFIED")
        out.append({"group_key_sha256": key, "context": {k: d0.get(k) for k in key_dims(d0, pdims)},
                    "members": [{"draft_id": m["draft_id"], "paper_id": m["paper_id"], "value": entry_dims(m).get("value"), "unit": entry_dims(m).get("unit"),
                                 "model_version": entry_dims(m).get("model_version")} for m in sorted(members, key=lambda m: (m["paper_id"], m["draft_id"]))],
                    "independent_papers": len(papers), "notes": sorted(notes), "pooled_estimate": None, "ranking": None})
    return out


def report_sha256(report: dict) -> str:
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
