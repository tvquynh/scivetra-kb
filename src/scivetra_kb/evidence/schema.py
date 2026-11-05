"""Defensive validation of an extractor response (``rkb-evidence-response/1``) before any EvidenceDraft exists, and the
structural claim checks used by the verifier.

Fail closed. Top level (any failure -> SCHEMA_INVALID, no draft): size, strict JSON (no NaN/Infinity, bounded nesting), duplicate
object keys, exact key sets, and the response must echo exactly the pack's hash, paper, document version, parse artifact and
profile (WRONG_PAPER, WRONG_DOCUMENT_VERSION, ...). Record level (the record is refused and listed, the others proceed): exact
keys, a known evidence class and typed fields of that class, decimal STRINGS only (no JSON numbers, no locale forms), explicit
units (never inferred), value-status rules (absence is explicit and carries no values or fabricated zeros), anchor shape and
quote hash, and source kinds the KB can verify (S06 text only: table cells, figure geometry and OCR do not exist and are refused).
Free text is scanned for secrets, local paths and tokenised URLs.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from ..metadata.strict_json import UnsafeJson, decode
from ..screening.schema import _LEAKS, _TOKEN, NUMBER_POLICY, numeric_tokens

RESPONSE_SCHEMA = "rkb-evidence-response/1"
VALUE_STATUSES = ("REPORTED", "NOT_REPORTED", "NOT_EXTRACTABLE", "UNCERTAIN", "CONFLICTED", "NOT_APPLICABLE")
ABSENCE = ("NOT_REPORTED", "NOT_EXTRACTABLE", "NOT_APPLICABLE")
CONFIDENCE = ("LOW", "MEDIUM", "HIGH")
UNVERIFIABLE_SOURCES = {"FULLTEXT_TABLE": "TABLE_GEOMETRY_NOT_AVAILABLE", "FULLTEXT_FIGURE_CAPTION": "FIGURE_GEOMETRY_NOT_AVAILABLE",
                        "OCR_TEXT": "OCR_NOT_AVAILABLE", "METADATA_CLAIM": "SOURCE_KIND_NOT_FOR_EVIDENCE_DRAFTS",
                        "HUMAN_READING": "SOURCE_KIND_NOT_FOR_EVIDENCE_DRAFTS"}
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RECORDS, MAX_ANCHORS, MAX_QUOTE, MAX_TEXT_FIELD, MAX_NOTE = 200, 10, 1000, 300, 500
TOP_KEYS = {"schema", "input_pack_sha256", "paper_id", "document_version_id", "artifact_sha256", "profile_sha256", "records", "notes"}
RECORD_KEYS = {"evidence_class", "value_status", "fields", "source_kind", "confidence", "anchors", "note"}
ANCHOR_KEYS = {"page", "char_start", "char_end", "quote", "quote_sha256"}
DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")
__all__ = ["NUMBER_POLICY", "InvalidResponse", "validate_response", "validate_record", "claim_checks", "normalize_text"]


class InvalidResponse(Exception):
    def __init__(self, codes):
        super().__init__(",".join(codes))
        self.codes = sorted(set(codes))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise InvalidResponse(["DUPLICATE_JSON_KEY"])
    return dict(pairs)


def _free_text(value, codes, code, limit):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        codes.append(code)
        return ""
    if _LEAKS.search(value):
        codes.append("LEAK_IN_RESPONSE")
    return value


def validate_record(rec, *, profile: dict) -> tuple[dict | None, list]:
    """``(normalized_record, [])`` or ``(None, codes)``."""
    codes = []
    if not isinstance(rec, dict) or set(rec) != RECORD_KEYS:
        return None, ["RECORD_KEYS"]
    cls = profile["classes"].get(rec["evidence_class"]) if isinstance(rec["evidence_class"], str) else None
    if cls is None:
        return None, ["UNKNOWN_EVIDENCE_CLASS"]
    if rec["source_kind"] != "FULLTEXT_TEXT":
        return None, [UNVERIFIABLE_SOURCES.get(rec["source_kind"], "SOURCE_KIND_INVALID") if isinstance(rec["source_kind"], str) else "SOURCE_KIND_INVALID"]
    status = rec["value_status"]
    if status not in VALUE_STATUSES:
        codes.append("VALUE_STATUS_INVALID")
    if rec["confidence"] is not None and rec["confidence"] not in CONFIDENCE:
        codes.append("CONFIDENCE_INVALID")
    note = _free_text(rec["note"], codes, "NOTE_INVALID", MAX_NOTE)
    fields = rec["fields"]
    if not isinstance(fields, dict) or len(fields) > len(cls["fields"]):
        return None, sorted(set(codes + ["FIELDS_INVALID"]))
    for name, value in fields.items():
        spec = cls["fields"].get(name)
        if spec is None:
            codes.append("UNKNOWN_FIELD:" + str(name)[:40])
            continue
        kind = spec["type"]
        if value is None:
            codes.append("NULL_FIELD_USE_VALUE_STATUS:" + name)          # absence is a value_status, never a null or a zero
        elif kind in ("decimal", "integer"):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                codes.append("NUMBER_MUST_BE_DECIMAL_STRING:" + name)
            elif not isinstance(value, str) or not (DECIMAL if kind == "decimal" else INTEGER).fullmatch(value):
                codes.append("NUMBER_FORMAT_INVALID:" + name)
            elif spec.get("unit_field") and spec["unit_field"] not in fields:
                codes.append("UNIT_MISSING:" + name)                     # a unit is never inferred
        elif kind == "unit":
            if value not in profile["units"]:
                codes.append("UNIT_INVALID:" + name)
        elif kind == "enum":
            if value not in spec["values"]:
                codes.append("ENUM_VALUE_INVALID:" + name)
        else:
            _free_text(value, codes, "TEXT_FIELD_INVALID:" + name, MAX_TEXT_FIELD)
            if isinstance(value, str) and not value.strip():
                codes.append("TEXT_FIELD_INVALID:" + name)
    anchors = rec["anchors"]
    if not isinstance(anchors, list) or len(anchors) > MAX_ANCHORS:
        return None, sorted(set(codes + ["ANCHORS_INVALID"]))
    clean = []
    for a in anchors:
        if not isinstance(a, dict) or set(a) != ANCHOR_KEYS:
            codes.append("ANCHOR_KEYS")
            continue
        if not all(isinstance(a[k], int) and not isinstance(a[k], bool) for k in ("page", "char_start", "char_end")) \
                or not (1 <= a["page"] <= 100_000 and 0 <= a["char_start"] < a["char_end"] <= 10_000_000):
            codes.append("ANCHOR_OFFSETS_INVALID")
            continue
        if not isinstance(a["quote"], str) or not 1 <= len(a["quote"]) <= MAX_QUOTE or not isinstance(a["quote_sha256"], str):
            codes.append("ANCHOR_QUOTE_INVALID")
            continue
        if _sha(a["quote"]) != a["quote_sha256"]:
            codes.append("QUOTE_HASH_INCONSISTENT")
            continue
        clean.append(dict(a))
    if status == "REPORTED":
        missing = [n for n, s in cls["fields"].items() if s["required"] and n not in fields]
        if missing:
            codes.append("REQUIRED_FIELD_MISSING:" + ",".join(missing))
        if not anchors:
            codes.append("REPORTED_WITHOUT_ANCHOR")
    if status == "UNCERTAIN" and not anchors:
        codes.append("UNCERTAIN_WITHOUT_ANCHOR")
    if status == "CONFLICTED" and (len(anchors) < 2 or fields):
        codes.append("CONFLICTED_NEEDS_TWO_ANCHORS_AND_NO_VALUES")
    if status in ABSENCE and fields:
        codes.append("ABSENCE_WITH_VALUES")
    if status in ("NOT_REPORTED", "NOT_APPLICABLE") and anchors:
        codes.append("ABSENCE_WITH_ANCHORS")
    if codes:
        return None, sorted(set(codes))
    return {"evidence_class": rec["evidence_class"], "value_status": status, "fields": dict(sorted(fields.items())), "source_kind": "FULLTEXT_TEXT",
            "confidence": rec["confidence"], "anchors": clean, "note": note}, []


def validate_response(raw: bytes, *, pack: dict, profile: dict) -> tuple[list, list]:
    """``(records, record_errors)``; raises InvalidResponse for top-level failures (no draft may be created)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESPONSE_BYTES:
        raise InvalidResponse(["RESPONSE_TOO_LARGE"])
    try:
        doc = decode(bytes(raw))
        json.loads(bytes(raw).decode("utf-8"), object_pairs_hook=_no_duplicates)
    except UnsafeJson as exc:
        raise InvalidResponse(["MALFORMED_JSON:" + str(exc)[:40]]) from None
    if not isinstance(doc, dict):
        raise InvalidResponse(["SCHEMA_INVALID:not_object"])
    codes = []
    if set(doc) != TOP_KEYS:
        raise InvalidResponse(["SCHEMA_INVALID:keys"])
    if doc["schema"] != RESPONSE_SCHEMA:
        codes.append("SCHEMA_INVALID:schema")
    for key, expected, code in (("input_pack_sha256", pack["pack_sha256"], "WRONG_PACK"), ("paper_id", pack["paper_id"], "WRONG_PAPER"),
                                ("document_version_id", pack["document_version_id"], "WRONG_DOCUMENT_VERSION"),
                                ("artifact_sha256", pack["artifact"]["artifact_sha256"], "WRONG_PARSE_ARTIFACT"),
                                ("profile_sha256", pack["profile"]["profile_sha256"], "WRONG_PROFILE")):
        if doc[key] != expected:
            codes.append(code)
    _free_text(doc["notes"], codes, "SCHEMA_INVALID:notes", 2000)
    records = doc["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        codes.append("SCHEMA_INVALID:records")
    if codes:
        raise InvalidResponse(codes)
    good, errors = [], []
    for i, rec in enumerate(records):
        norm, rcodes = validate_record(rec, profile=profile)
        if norm is None:
            errors.append({"record_index": i, "codes": rcodes})
        else:
            good.append((i, norm))
    return good, errors


# -- structural claim checks (verifier) ------------------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().casefold()


def _has_phrase(text_norm: str, phrase: str) -> bool:
    p = normalize_text(phrase)
    return bool(p) and re.search(r"(?<![0-9a-z])" + re.escape(p) + r"(?![0-9a-z])", text_norm) is not None


def numeric_mentions(text: str) -> list[dict]:
    """Every numeric token of ``text`` (``rkb-number-match/1``) with its canonical form (None if ambiguous) and whether a percent
    sign / word follows it."""
    out = []
    for m in _TOKEN.finditer(text or ""):
        canonical, ambiguous = numeric_tokens(m.group(0))
        after = text[m.end():m.end() + 12].lower()
        percent = bool(re.match(r"\s?(%|percent\b|per cent\b)", after))
        out.append({"canonical": next(iter(canonical)) if canonical and not ambiguous else None, "raw": m.group(0), "percent": percent})
    return out


def _readings(ambiguous: set) -> set:
    """Every way an ambiguous numeric form could be read (decimal comma, grouping separator, digits only). A claimed value that
    equals one of these is AMBIGUOUS; a value no reading could produce is simply not in the quote."""
    out = set()
    for text in ambiguous:
        for chunk in re.findall(r"[0-9][0-9.,'   ]*[0-9]|[0-9]", text):
            plain = re.sub(r"[   ']", "", chunk)
            out |= {plain, plain.replace(",", "."), plain.replace(",", ""), plain.replace(".", ""), plain.replace(".", "").replace(",", ".")}
    return out


def claim_checks(record: dict, quotes: list[str], *, profile: dict) -> tuple[list, list, list]:
    """``(checks, failures, ambiguities)`` for the typed fields of one record against its verified quotes. Never a semantic
    judgement: it establishes that every quote-bound value literally occurs in the cited text with the stated unit."""
    cls = profile["classes"][record["evidence_class"]]
    fields = record["fields"]
    joined = "\n".join(quotes)
    norm = normalize_text(joined)
    mentions = [m for q in quotes for m in numeric_mentions(q)]
    _, unclear = numeric_tokens(joined)
    checks, failures, ambiguous = [], [], []
    for name in sorted(fields):
        spec, value = cls["fields"][name], fields[name]
        kind = spec["type"]
        if kind == "text":
            ok = normalize_text(value) in norm
            checks.append({"field": name, "check": "TEXT_IN_QUOTE", "result": "PASS" if ok else "FAIL"})
            if not ok:
                failures.append("TEXT_NOT_IN_QUOTE:" + name)
        elif kind == "enum":
            terms = spec["values"][value]
            if not terms:
                checks.append({"field": name, "check": "INTERPRETIVE_HUMAN_JUDGEMENT", "result": "NOT_STRUCTURALLY_CHECKABLE"})
                continue
            ok = any(_has_phrase(norm, t) for t in terms)
            checks.append({"field": name, "check": "ENUM_TERM_IN_QUOTE", "result": "PASS" if ok else "FAIL"})
            if not ok:
                failures.append("ENUM_TERM_NOT_IN_QUOTE:" + name)
        elif kind in ("decimal", "integer"):
            unit = fields.get(spec.get("unit_field")) if spec.get("unit_field") else None
            hits = [m for m in mentions if m["canonical"] == value]
            if not hits:
                if value.lstrip("-") in _readings(unclear):
                    ambiguous.append("NUMBER_AMBIGUOUS:" + name)
                    checks.append({"field": name, "check": "NUMBER_IN_QUOTE", "result": "AMBIGUOUS"})
                else:
                    failures.append("NUMBER_NOT_IN_QUOTE:" + name)
                    checks.append({"field": name, "check": "NUMBER_IN_QUOTE", "result": "FAIL"})
                continue
            if unit == "%":
                ok = any(m["percent"] for m in hits)
            elif unit is not None and spec.get("unit_field"):
                ok = any(not m["percent"] for m in hits) and (not profile["units"].get(unit) or any(_has_phrase(norm, t) for t in profile["units"][unit]))
            else:
                ok = True
            checks.append({"field": name, "check": "NUMBER_AND_UNIT_IN_QUOTE", "unit": unit, "result": "PASS" if ok else "FAIL"})
            if not ok:
                failures.append("UNIT_NOT_SUPPORTED:" + name)
    return checks, failures, ambiguous
