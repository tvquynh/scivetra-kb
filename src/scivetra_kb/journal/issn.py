"""ISSN syntax and check digit (ISO 3297), conservative by design.

Accepted forms: ``NNNN-NNNC`` and the compact ``NNNNNNNC`` (C is a digit or ``X``; a lowercase ``x`` is accepted and
upper-cased, reported in ``flags``). Leading/trailing ASCII spaces are refused rather than trimmed, as are every other
separator, URN/label prefixes, full-width digits and any other character: an operator must supply a clean ISSN.
The check digit is mandatory. ``X`` is allowed only in the check position (value 10).

Validation says nothing about which journal an ISSN belongs to, or whether two ISSNs are print/electronic siblings:
those relationships come only from provider evidence (``journal/resolve.py``). ISSN-L is never derived locally.
"""
from __future__ import annotations

import re

VALID, INVALID = "VALID", "INVALID"
_FORM = re.compile(r"([0-9]{4})-?([0-9]{3})([0-9Xx])")


def check_digit(first7: str) -> str:
    total = sum(int(d) * w for d, w in zip(first7, range(8, 1, -1)))
    value = (11 - total % 11) % 11
    return "X" if value == 10 else str(value)


def parse_issn(raw) -> dict:
    """Return {"status", "issn" (NNNN-NNNC or None), "input", "reason", "flags"}."""
    out = {"status": INVALID, "issn": None, "input": raw, "reason": None, "flags": []}
    if not isinstance(raw, str):
        out["reason"] = "NOT_A_STRING"
        return out
    match = _FORM.fullmatch(raw)
    if not match:
        out["reason"] = "BAD_FORMAT"
        return out
    if "-" not in raw:
        out["flags"].append("COMPACT_FORM")
    if match.group(3) == "x":
        out["flags"].append("LOWERCASE_X_UPPERCASED")
    first7, check = match.group(1) + match.group(2), match.group(3).upper()
    if check_digit(first7) != check:
        out["reason"] = "CHECK_DIGIT_MISMATCH"
        return out
    out.update(status=VALID, issn=f"{first7[:4]}-{first7[4:]}{check}")
    return out


def provider_issn(value) -> str | None:
    """Normalize an ISSN string found in provider metadata; None if it is not a valid ISSN (kept raw elsewhere)."""
    parsed = parse_issn(value)
    return parsed["issn"] if parsed["status"] == VALID else None


__all__ = ["parse_issn", "provider_issn", "check_digit", "VALID", "INVALID"]
