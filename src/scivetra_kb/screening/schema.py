"""Defensive validation of a model response (``rkb-ai-response/1``) before it can become an ``rkb-ai-proposal/1``.

The same validator handles the deterministic mock adapter's output and operator-imported JSON. Fail closed: size and depth
limits (strict JSON decoder: no NaN/Infinity, no duplicate-free ambiguity, bounded nesting), exact key sets (unknown keys
refused), the response must name exactly the pack's task, paper and pack hash (no scope drift), exactly the criteria of the
frozen criteria set, known judgements / outcomes / reason codes, bounded strings, and no secrets, local paths or tokenised
URLs (LEAK_IN_RESPONSE). Nothing in a response can change the scope, the model location or the source identity.
"""
from __future__ import annotations

import re

from ..metadata.strict_json import UnsafeJson, decode

RESPONSE_SCHEMA = "rkb-ai-response/1"
OUTCOMES = ("INCLUDE", "EXCLUDE", "UNSURE")
JUDGEMENTS = ("MET", "NOT_MET", "UNCLEAR")
UNCERTAINTY = ("LOW", "MEDIUM", "HIGH")
MAX_RESPONSE_BYTES = 256 * 1024
MAX_TEXT = 2000
MAX_ANCHORS = 20
TOP_KEYS = {"schema", "task_type", "paper_id", "input_pack_sha256", "suggested_outcome", "criteria", "suggested_reason_codes", "uncertainty", "notes"}
CRITERION_KEYS = {"criterion_id", "judgement", "rationale", "anchors"}
ANCHOR_KEYS = {"kind", "ref", "page", "char_start", "char_end", "quote", "quote_sha256"}
_LEAKS = re.compile(r"(?i)([a-z]:[\\/]+(users|windows|program)|/home/|/root/|/etc/|\\\\[a-z0-9]|api[_-]?key|secret[_-]?key|password\s*[:=]|bearer\s+[a-z0-9]|"
                    r"sk-[a-z0-9]{16}|ghp_[a-z0-9]{20}|x-amz-signature|[?&](sig|token|signature)=|-----begin [a-z ]*private key)")


class InvalidResponse(Exception):
    def __init__(self, codes):
        super().__init__(",".join(codes))
        self.codes = sorted(set(codes))


def _string(value, codes, code, limit=MAX_TEXT):
    if not isinstance(value, str) or len(value) > limit:
        codes.append(code)
        return ""
    if _LEAKS.search(value):
        codes.append("LEAK_IN_RESPONSE")
    return value


def validate_response(raw: bytes, *, pack: dict, criteria: dict) -> dict:
    """Return the normalized payload or raise InvalidResponse(codes)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESPONSE_BYTES:
        raise InvalidResponse(["RESPONSE_TOO_LARGE"])
    try:
        doc = decode(bytes(raw))
    except UnsafeJson as exc:
        raise InvalidResponse(["MALFORMED_JSON:" + str(exc)[:40]]) from None
    if not isinstance(doc, dict):
        raise InvalidResponse(["SCHEMA_INVALID:not_object"])
    codes = []
    if set(doc) != TOP_KEYS:
        codes.append("SCHEMA_INVALID:keys")
    if doc.get("schema") != RESPONSE_SCHEMA:
        codes.append("SCHEMA_INVALID:schema")
    if doc.get("task_type") != pack["task_type"] or doc.get("paper_id") != pack["paper_id"] or doc.get("input_pack_sha256") != pack["pack_sha256"]:
        codes.append("SCOPE_DRIFT")
    if doc.get("suggested_outcome") not in OUTCOMES:
        codes.append("SCHEMA_INVALID:outcome")
    if doc.get("uncertainty") not in UNCERTAINTY:
        codes.append("SCHEMA_INVALID:uncertainty")
    _string(doc.get("notes", ""), codes, "SCHEMA_INVALID:notes")
    known = [c["id"] for c in criteria["criteria"]]
    vocabulary = set(criteria["exclude_reason_codes"])
    reasons = doc.get("suggested_reason_codes")
    if not isinstance(reasons, list) or len(reasons) > 20 or any(not isinstance(r, str) or r not in vocabulary for r in reasons):
        codes.append("UNKNOWN_REASON_CODE")
    items = doc.get("criteria")
    seen = []
    if not isinstance(items, list) or len(items) > len(known):
        codes.append("SCHEMA_INVALID:criteria")
        items = []
    for item in items:
        if not isinstance(item, dict) or set(item) != CRITERION_KEYS:
            codes.append("SCHEMA_INVALID:criterion")
            continue
        if item["criterion_id"] not in known:
            codes.append("UNKNOWN_CRITERION")
        seen.append(item["criterion_id"])
        if item["judgement"] not in JUDGEMENTS:
            codes.append("SCHEMA_INVALID:judgement")
        _string(item["rationale"], codes, "SCHEMA_INVALID:rationale")
        anchors = item["anchors"]
        if not isinstance(anchors, list) or len(anchors) > MAX_ANCHORS:
            codes.append("SCHEMA_INVALID:anchors")
            continue
        for a in anchors:
            if not isinstance(a, dict) or set(a) != ANCHOR_KEYS or a.get("kind") not in ("METADATA_CLAIM", "PARSE_SEGMENT"):
                codes.append("SCHEMA_INVALID:anchor")
                continue
            for key in ("page", "char_start", "char_end"):
                if not (a[key] is None or (isinstance(a[key], int) and not isinstance(a[key], bool) and 0 <= a[key] <= 10_000_000)):
                    codes.append("SCHEMA_INVALID:anchor_offsets")
            _string(a["ref"], codes, "SCHEMA_INVALID:anchor_ref", 200)
            _string(a["quote"], codes, "SCHEMA_INVALID:anchor_quote", 1000)
            if not (isinstance(a["quote_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", a["quote_sha256"])):
                codes.append("SCHEMA_INVALID:anchor_hash")
    if sorted(seen) != sorted(known) or len(set(seen)) != len(seen):
        codes.append("CRITERIA_MISMATCH")
    if codes:
        raise InvalidResponse(codes)
    return {k: doc[k] for k in sorted(TOP_KEYS - {"schema"})}


# -- S07 v02 (S07-F01): outcome / criteria / reason-code consistency ------------------------------------------------------
AGGREGATION = "rkb-screening-aggregation/1"
AGGREGATIONS = ("ALL_CRITERIA_REQUIRED",)


def aggregation_failures(payload: dict, criteria: dict) -> list:
    """Structural consistency of a suggestion with the FROZEN criteria set (``criteria["aggregation"]``, default
    ``ALL_CRITERIA_REQUIRED``, the only rule of rkb-screening-criteria/1):

    * INCLUDE  <=> every criterion is MET, and no reason code;
    * EXCLUDE  <=> at least one criterion is NOT_MET, and the reason codes are exactly the exclude_reason_codes of the NOT_MET
      criteria (an unsupported or missing reason fails);
    * UNSURE   <=> no NOT_MET and not all MET, and no reason code.

    This checks coherence of the labels only; it does not verify the scientific reasoning (quote checks do not either)."""
    rule = criteria.get("aggregation", "ALL_CRITERIA_REQUIRED")
    if rule not in AGGREGATIONS:
        return ["AGGREGATION_RULE_UNKNOWN"]
    codes = {c["id"]: c["exclude_reason_code"] for c in criteria["criteria"]}
    judgements = {c["criterion_id"]: c["judgement"] for c in payload.get("criteria", [])}
    not_met = {cid for cid, j in judgements.items() if j == "NOT_MET"}
    all_met = bool(judgements) and all(j == "MET" for j in judgements.values()) and set(judgements) == set(codes)
    reasons = set(payload.get("suggested_reason_codes", []))
    outcome = payload.get("suggested_outcome")
    failures = []
    if outcome == "INCLUDE" and not all_met:
        failures.append("OUTCOME_CRITERIA_INCONSISTENT:INCLUDE_WITHOUT_ALL_MET")
    if outcome == "EXCLUDE" and not not_met:
        failures.append("OUTCOME_CRITERIA_INCONSISTENT:EXCLUDE_WITHOUT_NOT_MET")
    if outcome == "UNSURE" and (not_met or all_met):
        failures.append("OUTCOME_CRITERIA_INCONSISTENT:UNSURE_WITH_DECISIVE_CRITERIA")
    expected = {codes[cid] for cid in not_met} if outcome == "EXCLUDE" else set()
    if reasons - expected:
        failures.append("REASON_CODE_UNSUPPORTED:" + ",".join(sorted(reasons - expected)))
    if outcome == "EXCLUDE" and expected - reasons:
        failures.append("REASON_CODE_MISSING:" + ",".join(sorted(expected - reasons)))
    return failures


# -- S07 v02 (S07-F02): numeric token policy rkb-number-match/1 -----------------------------------------------------------
NUMBER_POLICY = "rkb-number-match/1"
# A numeric token: optional sign, digits with optional thousands groups (comma, NBSP, thin/narrow space, apostrophe), optional
# decimal part with '.' or ','. Boundaries: not preceded by a letter/digit/'.'/','; not followed by a digit or by a separator
# that continues the number. Adjacent units are allowed ("12mg", "12%").
_SEP = ",   '"
_TOKEN = re.compile(r"(?<![0-9A-Za-z.,   '])([+\-−–]?)(\d+(?:[" + _SEP + r"]\d+)*(?:[.,]\d+)?)(?![0-9])(?![.,]\d)")


def numeric_tokens(text: str) -> tuple[set, set]:
    """``(canonical, ambiguous)``. Canonical: sign normalised (U+2212/U+2013 -> '-', '+' dropped), group separators removed,
    decimal point '.'. A token whose separators can be read two ways ("1,5", "1,234" is grouped, "1,2345" is not) is AMBIGUOUS
    and never matches. No rounding: 12 and 12.0 are different tokens."""
    canonical, ambiguous = set(), set()
    text = text or ""
    covered = set()
    for m in _TOKEN.finditer(text):
        covered.update(range(m.start(), m.end()))
    for m in re.finditer(r"\d+", text):             # digits outside any well-formed token ("2.0.1", "3.2.") are ambiguous
        if not set(range(m.start(), m.end())) <= covered:
            ambiguous.add(text[max(0, m.start() - 3):m.end() + 3].strip())
    for sign, body in _TOKEN.findall(text):
        sign = "-" if sign in ("-", "−", "–") else ""
        parts = re.split(r"[" + _SEP + r"]", body)
        # thousands grouping: every group after the first has exactly 3 digits (and the first has 1-3)
        if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            canonical.add(sign + "".join(parts))
            continue
        if body.count(".") == 1 and not re.search("[" + _SEP + "]", body):
            canonical.add(sign + body)
            continue
        if re.fullmatch(r"\d+", body):
            canonical.add(sign + body)
            continue
        if len(parts) == 2 and "," in body and "." not in body:
            ambiguous.add(sign + body)       # "1,5": a decimal comma or a malformed group - refuse to guess
            continue
        ambiguous.add(sign + body)
    return canonical, ambiguous
