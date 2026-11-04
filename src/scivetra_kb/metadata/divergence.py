"""Report-only comparison with Reprovane's DOI normalizer (S01 v03 04 section 2.4).

The KB never calls, patches or re-implements Reprovane for authority purposes.
It compares the value Reprovane *exported* (``identifier_value_normalized``)
with the KB parse of ``identifier_value_original`` and classifies differences.
"""
from __future__ import annotations

import re
import unicodedata

from .doi import AMBIGUOUS, VALID, DoiParse

RV_PATTERN = re.compile(r"10\.[0-9]{4,9}/\S+")  # documented RV acceptance rule (normalization.py:9-15)


def classify(rv_original: str, rv_normalized: str | None, kb: DoiParse) -> str | None:
    """Return a divergence kind, or None when RV and KB agree."""
    if kb.status == AMBIGUOUS:
        return {"MULTIPLE_PREFIX_OCCURRENCES": "MULTI_DOI",
                "DOI_LABEL_PERCENT_AMBIGUOUS": "DOI_LABEL_PERCENT",
                "TRAILING_SPACE_UNCERTAIN": "TRAILING_SPACE_STRIPPED"}.get(kb.reason, "KB_AMBIGUOUS")
    if kb.status != VALID:
        return "KB_REJECTS_RV_ACCEPTS" if rv_normalized else None
    key = kb.comparison_key
    if rv_normalized == key:
        return None
    if rv_normalized is None:
        lowered = rv_original.strip().lower()
        prefix, suffix = key.split("/", 1)
        registrant = prefix.split(".", 1)[1]
        if lowered.startswith("urn:doi:"):
            return "URN_LABEL"
        if "." in registrant:
            return "DOTTED_REGISTRANT"
        if not 4 <= len(registrant) <= 9:
            return "REGISTRANT_DIGITS"
        if any(unicodedata.category(c) == "Zs" for c in suffix):
            return "SUFFIX_WHITESPACE"
        return "RV_REJECTS_KB_ACCEPTS"
    if key and unicodedata.category(key[-1]) == "Zs" and rv_normalized == key.rstrip("               　"):
        return "TRAILING_SPACE_STRIPPED"
    if "%" in rv_normalized:
        return "PERCENT_ENCODING"
    if rv_normalized == key.lower():
        return "UNICODE_LOWERCASE"
    return "OTHER"
