"""Versioned, research-domain extraction profiles (``rkb-evidence-profile/1``).

A profile is data, not code: evidence classes, their typed fields, the units and the enumerations a claim may use. Field types:

* ``text``    - verbatim phrase; it must occur in a verified quote (NFC, case and whitespace insensitive).
* ``decimal`` - canonical decimal STRING (``-?digits[.digits]``; no locale, no exponent, no JSON number). It must equal a complete
                numeric token of a verified quote (``rkb-number-match/1``); with ``unit_field`` the unit is checked too.
* ``integer`` - canonical integer string, checked like ``decimal``.
* ``unit``    - one of the profile's units; checked through its decimal field. A unit is never inferred or converted.
* ``enum``    - one of ``values``. A value with evidence ``terms`` must have one of its terms in a verified quote; a value without
                terms is INTERPRETIVE (a human judgement the structural verifier cannot check; it is reported as such).

``key_fields`` (optional) define when two REPORTED drafts of one paper describe the same measurement; different values for the
same key are reported as a conflict. ``required`` fields must be present for value_status REPORTED. Two built-in profiles are
shipped: a general empirical-research profile and a sample cybersecurity / malware-detection profile. Operators may register
their own profile for another research domain through the same validator.
"""
from __future__ import annotations

import copy
import re

PROFILE_SCHEMA = "rkb-evidence-profile/1"
FIELD_TYPES = ("text", "decimal", "integer", "unit", "enum")
MAX_CLASSES, MAX_FIELDS = 60, 40

UNITS = {
    "%": ["%", "percent", "per cent"],      # adjacency to the number is checked, never inferred from a bare decimal
    "ratio": [],                            # a bare number; the token must NOT carry a percent sign
    "count": [],
    "seconds": ["s", "sec", "seconds"],
    "milliseconds": ["ms", "milliseconds"],
    "hours": ["h", "hours"],
    "days": ["days"],
    "years": ["years"],
    "bytes": ["bytes", "B"],
    "samples": ["samples"],
}


def _t(required=False, **kw):
    return {"type": "text", "required": required, **kw}


def _d(unit_field=None, required=False):
    spec = {"type": "decimal", "required": required}
    if unit_field:
        spec["unit_field"] = unit_field
    return spec


def _i(required=False):
    return {"type": "integer", "required": required}


def _u():
    return {"type": "unit", "required": False}


def _e(values: dict, required=False):
    return {"type": "enum", "required": required, "values": values}


INTERPRETIVE = []          # an enum value without evidence terms: human judgement only

GENERAL_CLASSES = {
    "STUDY_OBJECTIVE": {"fields": {"objective": _t(True)}},
    "STUDY_DESIGN": {"fields": {"design_statement": _t(True),
                                "design_category": _e({"EXPERIMENTAL": INTERPRETIVE, "QUASI_EXPERIMENTAL": INTERPRETIVE, "OBSERVATIONAL": INTERPRETIVE,
                                                       "BENCHMARK_EVALUATION": INTERPRETIVE, "SIMULATION": INTERPRETIVE, "OTHER": INTERPRETIVE})}},
    "POPULATION_SAMPLE": {"fields": {"population": _t(), "sample_size": _i(), "sample_unit": _t(), "subgroup": _t()}},
    "DATASET": {"fields": {"dataset": _t(True), "dataset_version": _t(), "dataset_year": _i(), "split": _t(), "source_statement": _t()}},
    "EXPERIMENT_PERIOD": {"fields": {"period_statement": _t(), "start_year": _i(), "end_year": _i()}},
    "LABELING_LEAKAGE_CONTROL": {"fields": {"control_statement": _t(True)}},
    "METHOD": {"fields": {"method": _t(True), "configuration": _t()}},
    "BASELINE": {"fields": {"baseline": _t(True)}},
    "IMPLEMENTATION_CONSTRAINT": {"fields": {"constraint": _t(True)}},
    "OUTCOME_METRIC": {"fields": {"metric": _t(True), "definition": _t()}},
    "NUMERICAL_RESULT": {"fields": {"metric": _t(True), "value": _d("unit", True), "unit": _u(), "denominator": _i(), "dataset": _t(),
                                    "dataset_year": _i(), "subset": _t(), "condition": _t(), "comparator": _t(),
                                    "direction": _e({"HIGHER_IS_BETTER": INTERPRETIVE, "LOWER_IS_BETTER": INTERPRETIVE})},
                         "key_fields": ["metric", "dataset", "dataset_year", "subset", "condition"]},
    "UNCERTAINTY_INTERVAL": {"fields": {"metric": _t(True), "level": _d("level_unit"), "level_unit": _u(), "lower": _d("unit"), "upper": _d("unit"),
                                        "unit": _u(), "method": _t()}},
    "STATISTICAL_COMPARISON": {"fields": {"comparison": _t(True), "test": _t(), "statistic": _d(), "p_value": _d(), "comparator": _t()}},
    "SHIFT_CONDITION": {"fields": {"condition": _t(True),
                                   "shift_type": _e({"TEMPORAL": INTERPRETIVE, "ADVERSARIAL": INTERPRETIVE, "DISTRIBUTION": INTERPRETIVE,
                                                     "OTHER": INTERPRETIVE})}},
    "LIMITATION": {"fields": {"limitation": _t(True)}},
    "ETHICS_LICENSING": {"fields": {"statement": _t(True)}},
}

GENERAL = {"schema": PROFILE_SCHEMA, "profile_key": "rkb-general-empirical", "version": "1", "domain": "general",
           "label": "General empirical research evidence (typed, quote-bound)", "units": UNITS, "classes": GENERAL_CLASSES}

METRIC_TERMS = {
    "TPR": ["TPR", "true positive rate", "detection rate"],
    "FPR": ["FPR", "false positive rate"],
    "ROC_AUC": ["ROC-AUC", "ROC AUC", "AUROC", "AUC-ROC", "area under the ROC curve"],
    "PR_AUC": ["PR-AUC", "PR AUC", "AUPRC", "area under the precision-recall curve"],
    "AP": ["average precision"],
    "F1": ["F1", "F1-score", "F1 score"],
    "PRECISION": ["precision"],
    "RECALL": ["recall"],
    "ACCURACY": ["accuracy"],
}
MALWARE_CLASSES = copy.deepcopy(GENERAL_CLASSES)
MALWARE_CLASSES["DATASET"] = {"fields": {
    "dataset": _t(True), "dataset_version": _t(), "dataset_year": _i(), "sample_count": _i(),
    "split": _e({"TRAIN": ["train", "training"], "TEST": ["test", "testing"], "VALIDATION": ["validation"], "CHALLENGE": ["challenge"]}),
    "pe_type": _e({"WIN32": ["Win32", "32-bit"], "WIN64": ["Win64", "64-bit", "x64"], "DOTNET": [".NET"]}),
    "source_statement": _t()}}
MALWARE_CLASSES["NUMERICAL_RESULT"] = {"fields": {
    "metric": _e(METRIC_TERMS, True), "value": _d("unit", True), "unit": _u(),
    "fpr_operating_point": _d("fpr_operating_point_unit"), "fpr_operating_point_unit": _u(),
    "dataset": _t(), "dataset_year": _i(),
    "split": _e({"TRAIN": ["train", "training"], "TEST": ["test", "testing"], "VALIDATION": ["validation"], "CHALLENGE": ["challenge"]}),
    "pe_type": _e({"WIN32": ["Win32", "32-bit"], "WIN64": ["Win64", "64-bit", "x64"], "DOTNET": [".NET"]}),
    "denominator": _i(), "subset": _t(), "seeds": _i(), "model_version": _t(), "temporal_holdout": _t()},
    "key_fields": ["metric", "fpr_operating_point", "fpr_operating_point_unit", "dataset", "dataset_year", "split", "pe_type", "subset",
                   "model_version", "temporal_holdout"]}
MALWARE_CLASSES["SHIFT_CONDITION"] = {"fields": {
    "condition": _t(True), "train_end_year": _i(), "test_start_year": _i(),
    "shift_type": _e({"TEMPORAL": ["temporal", "time-split", "later"], "ADVERSARIAL": ["adversarial", "evasion"],
                      "DISTRIBUTION": INTERPRETIVE, "OTHER": INTERPRETIVE})}}

MALWARE = {"schema": PROFILE_SCHEMA, "profile_key": "rkb-malware-detection", "version": "1", "domain": "cybersecurity/malware-detection",
           "label": "Sample profile: Windows PE malware detection evaluations (fixed-FPR TPR, ROC-AUC vs PR-AUC/AP, splits, temporal holdout)",
           "units": UNITS, "classes": MALWARE_CLASSES}

BUILTIN = {"rkb-general-empirical": GENERAL, "rkb-malware-detection": MALWARE}


class ProfileInvalid(ValueError):
    pass


def builtin(key: str) -> dict:
    if key not in BUILTIN:
        raise ProfileInvalid("unknown built-in profile")
    return copy.deepcopy(BUILTIN[key])


def validate_profile(profile) -> dict:
    """Return the profile unchanged or raise ProfileInvalid. Exact keys, bounded sizes, known field types, enum values with term
    lists, units with term lists, unit_field pointing to a unit field of the same class, key_fields naming existing fields."""
    if not isinstance(profile, dict) or set(profile) != {"schema", "profile_key", "version", "domain", "label", "units", "classes"}:
        raise ProfileInvalid("profile keys")
    if profile["schema"] != PROFILE_SCHEMA:
        raise ProfileInvalid("profile schema")
    for key, limit in (("profile_key", 80), ("version", 20), ("domain", 80), ("label", 200)):
        if not isinstance(profile[key], str) or not 1 <= len(profile[key]) <= limit:
            raise ProfileInvalid(key)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,79}", profile["profile_key"]):
        raise ProfileInvalid("profile_key")
    units = profile["units"]
    if not isinstance(units, dict) or not 1 <= len(units) <= 40 or not all(
            isinstance(u, str) and 1 <= len(u) <= 20 and isinstance(t, list) and all(isinstance(x, str) and 1 <= len(x) <= 40 for x in t)
            for u, t in units.items()):
        raise ProfileInvalid("units")
    classes = profile["classes"]
    if not isinstance(classes, dict) or not 1 <= len(classes) <= MAX_CLASSES:
        raise ProfileInvalid("classes")
    for name, spec in classes.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,59}", name) or not isinstance(spec, dict) or set(spec) - {"fields", "key_fields"} or "fields" not in spec:
            raise ProfileInvalid("class " + str(name)[:60])
        fields = spec["fields"]
        if not isinstance(fields, dict) or not 1 <= len(fields) <= MAX_FIELDS:
            raise ProfileInvalid("fields of " + name)
        for fname, f in fields.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", fname) or not isinstance(f, dict) or f.get("type") not in FIELD_TYPES \
                    or not isinstance(f.get("required"), bool):
                raise ProfileInvalid(f"field {name}.{str(fname)[:40]}")
            allowed = {"type", "required"} | ({"unit_field"} if f["type"] == "decimal" else set()) | ({"values"} if f["type"] == "enum" else set())
            if set(f) - allowed:
                raise ProfileInvalid(f"field keys {name}.{fname}")
            if f["type"] == "enum":
                values = f.get("values")
                if not isinstance(values, dict) or not 1 <= len(values) <= 60 or not all(
                        re.fullmatch(r"[A-Z0-9_]{1,60}", v) and isinstance(t, list) and all(isinstance(x, str) and 1 <= len(x) <= 80 for x in t)
                        for v, t in values.items()):
                    raise ProfileInvalid(f"enum {name}.{fname}")
            if "unit_field" in f and fields.get(f["unit_field"], {}).get("type") != "unit":
                raise ProfileInvalid(f"unit_field {name}.{fname}")
        keys = spec.get("key_fields", [])
        if not isinstance(keys, list) or any(k not in fields for k in keys):
            raise ProfileInvalid("key_fields of " + name)
    return profile
