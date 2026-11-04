"""DOI parsing per S01 v03 04 section 2 (DOI Handbook 2025 sections 4.2-4.7).

Implementation order (architect clarification 2 on S01 v03): preserve the raw
value, strip leading formatting (I1a), recognise the form (I2/I3), then apply
form-specific whitespace handling (I1b for literal/label forms, I4/I4b for
proxy/URN forms), decode (I5/I6), validate (I7-I10) and build outputs (I11).
Nothing here ever trims a suffix space, case-folds beyond ASCII A-Z, or applies
Unicode normalization. I12/I13 are KB-level checks (see ``store.py``).
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict

DOI_IDENTIFIER_FIELD = "DOI_IDENTIFIER_FIELD"
DOI_LIST_FIELD = "DOI_LIST_FIELD"
FREE_TEXT_EXTRACT = "FREE_TEXT_EXTRACT"
CONTEXTS = (DOI_IDENTIFIER_FIELD, DOI_LIST_FIELD, FREE_TEXT_EXTRACT)

STRUCTURED_PROVIDER_FIELD = "STRUCTURED_PROVIDER_FIELD"
OPERATOR_OR_TABULAR_INPUT = "OPERATOR_OR_TABULAR_INPUT"
TRUST_LEVELS = (STRUCTURED_PROVIDER_FIELD, OPERATOR_OR_TABULAR_INPUT)

VALID, INVALID, AMBIGUOUS = "VALID", "INVALID", "AMBIGUOUS"

PROXY_LABELS = (("https://doi.org/", False), ("http://doi.org/", False),
                ("https://dx.doi.org/", True), ("http://dx.doi.org/", True))
URN_LABEL = "urn:doi:"
DOI_LABEL = "doi:"
UNRESERVED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+;=:@")
TRAILING_PUNCT = set(".,;:)]}'\"")
CONTROL_EDGE = set("\t\n\r")
PREFIX_RE = re.compile(r"^10\.[0-9]+(\.[0-9]+)*$")
NUMERIC_PREFIX_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
PREFIX_OCCURRENCE_RE = re.compile(r"(?<![0-9A-Za-z.])10\.[0-9]+(?:\.[0-9]+)*/")
FREE_TEXT_START_RE = re.compile(r"(?<![0-9A-Za-z])10\.[0-9]+(?:\.[0-9]+)*/")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
PERCENT_HH_RE = re.compile(r"%[0-9A-Fa-f]{2}")


@dataclass
class DoiParse:
    status: str
    value_original: str
    context: str
    source_trust: str
    doi: str | None = None
    comparison_key: str | None = None
    url_form: str | None = None
    candidates: list = field(default_factory=list)
    reason: str | None = None
    flags: list = field(default_factory=list)

    @property
    def automatic_action_allowed(self) -> bool:
        """Lookups, links and assertions are allowed only for VALID identifier-field parses."""
        return self.status == VALID and self.context != FREE_TEXT_EXTRACT and "AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION" not in self.flags

    def as_dict(self) -> dict:
        data = asdict(self)
        data["candidates"] = [c if isinstance(c, dict) else c.as_dict() for c in self.candidates]
        data["automatic_action_allowed"] = self.automatic_action_allowed
        return data


def _is_zs(ch: str) -> bool:
    return unicodedata.category(ch) == "Zs"


def _is_graphic(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category[0] in "LMNPS" or category == "Zs"


def _ascii_lower(text: str) -> str:
    """ASCII-only lowercasing used for label/scheme recognition (never for Unicode case folding)."""
    return text.translate({c: c + 32 for c in range(0x41, 0x5B)})


def ascii_key(doi: str) -> str:
    """Comparison key: map only U+0041..U+005A to lowercase (Handbook 4.3.4)."""
    return doi.translate({c: c + 32 for c in range(0x41, 0x5B)})


def percent_encode_part(part: str) -> str:
    out = []
    for byte in part.encode("utf-8"):
        out.append(chr(byte) if byte in UNRESERVED else "%{:02X}".format(byte))
    return "".join(out)


def url_form(doi: str) -> str:
    prefix, suffix = doi.split("/", 1)
    return "https://doi.org/" + percent_encode_part(prefix) + "/" + percent_encode_part(suffix)


def percent_encoded_name(doi: str) -> str:
    prefix, suffix = doi.split("/", 1)
    return percent_encode_part(prefix) + "/" + percent_encode_part(suffix)


def _percent_decode_once(text: str) -> tuple[str | None, str | None]:
    buf = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "%":
            hh = text[i + 1:i + 3]
            if len(hh) != 2 or not all(c in "0123456789abcdefABCDEF" for c in hh):
                return None, "MALFORMED_PERCENT_ESCAPE"
            buf.append(int(hh, 16))
            i += 3
        else:
            buf.extend(ch.encode("utf-8"))
            i += 1
    try:
        return bytes(buf).decode("utf-8", errors="strict"), None
    except UnicodeDecodeError:
        return None, "INVALID_UTF8_AFTER_DECODE"


def _validate_name(name: str) -> tuple[str, str | None, list]:
    """Steps I7-I10 on a DOI name. Returns (status, reason, flags)."""
    flags: list = []
    if "/" not in name:
        return INVALID, "BAD_PREFIX", flags
    prefix, suffix = name.split("/", 1)
    if not PREFIX_RE.match(prefix):
        if NUMERIC_PREFIX_RE.match(prefix) and prefix.split(".")[0] != "10":
            return INVALID, "NON_10_DIRECTORY", flags
        return INVALID, "BAD_PREFIX", flags
    if suffix == "":
        return INVALID, "EMPTY_SUFFIX", flags
    if any(not _is_graphic(ch) for ch in name):
        return INVALID, "NON_GRAPHIC_CODE_POINT", flags
    stripped = suffix.rstrip("".join(ch for ch in set(suffix) if _is_zs(ch)))
    if any(_is_zs(ch) for ch in stripped):
        flags.append("SUFFIX_CONTAINS_SPACE")
    if "," in suffix or ";" in suffix:
        flags.append("SUFFIX_CONTAINS_LIST_PUNCTUATION")
    if suffix and _is_zs(suffix[-1]):
        flags.append("TRAILING_SPACE_IN_SUFFIX")
    if name.endswith("/"):
        flags.append("TRAILING_SLASH")
    elif name[-1] in TRAILING_PUNCT:
        flags.append("TRAILING_PUNCTUATION_REVIEW")
    if any(ord(ch) > 127 for ch in suffix):
        flags.append("NON_ASCII_SUFFIX")
    return VALID, None, flags


def _candidate(name: str, derivation: str) -> dict:
    status, reason, flags = _validate_name(name)
    entry = dict(doi=None, comparison_key=None, url_form=None, derivation=derivation, status=status, reason=reason, flags=flags)
    if status == VALID:
        entry.update(doi=name, comparison_key=ascii_key(name), url_form=url_form(name))
    else:
        entry["raw"] = name
    return entry


def _multiple_prefix_split(name: str) -> list[str] | None:
    matches = list(PREFIX_OCCURRENCE_RE.finditer(name))
    if len(matches) < 2:
        return None
    boundaries = [0]
    for match in matches[1:]:
        before = name[:match.start()]
        trimmed = before.rstrip(" ")
        spaced = len(trimmed) != len(before)
        if spaced or (trimmed and (trimmed[-1] in ",;|" or trimmed[-1].isspace())):
            boundaries.append(match.start())
    if len(boundaries) < 2:
        return None
    pieces = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(name)
        piece = name[start:end]
        piece = piece.rstrip(" ")
        while piece and piece[-1] in ",;|":
            piece = piece[:-1].rstrip(" ")
        pieces.append(piece)
    return pieces


def _finish(result: DoiParse, name: str) -> DoiParse:
    status, reason, flags = _validate_name(name)
    result.flags.extend(flags)
    if status != VALID:
        result.status, result.reason = INVALID, reason
        return result
    pieces = _multiple_prefix_split(name)
    if pieces:
        result.status = AMBIGUOUS
        result.reason = "MULTIPLE_PREFIX_OCCURRENCES"
        result.flags.append("AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION")
        result.candidates = [_candidate(name, "WHOLE_STRING")] + [_candidate(p, f"SPLIT_PIECE_{i + 1}") for i, p in enumerate(pieces)]
        return result
    result.status = VALID
    result.doi = name
    result.comparison_key = ascii_key(name)
    result.url_form = url_form(name)
    return result


def _parse_identifier(raw: str, context: str, trust: str) -> DoiParse:
    result = DoiParse(status=INVALID, value_original=raw, context=context, source_trust=trust)
    text = raw
    # I1a: leading formatting whitespace can never belong to a DOI (names start with "10.", labels with a letter).
    lead = 0
    while lead < len(text) and (_is_zs(text[lead]) or text[lead] in CONTROL_EDGE):
        lead += 1
    if lead:
        text = text[lead:]
        result.flags.append("LEADING_WHITESPACE_REMOVED")
    if not text:
        result.reason = "EMPTY_INPUT"
        return result
    # I2/I3: recognise exactly one label.
    lower = _ascii_lower(text[:20])
    form, remainder = "LITERAL", text
    for label, deprecated in PROXY_LABELS:
        if lower.startswith(label):
            form, remainder = "PROXY", text[len(label):]
            if deprecated:
                result.flags.append("DEPRECATED_DX_PROXY")
            break
    else:
        if lower.startswith(URN_LABEL):
            form, remainder = "URN", text[len(URN_LABEL):]
        elif lower.startswith(DOI_LABEL):
            form, remainder = "LABEL", text[len(DOI_LABEL):]
        elif SCHEME_RE.match(text) or lower.startswith("www."):
            result.reason = "UNSUPPORTED_URL_FORM"
            return result
    if form in ("PROXY", "URN"):
        # I4b: a URL/URN cannot contain raw whitespace; trailing raw whitespace/control is formatting.
        end = len(remainder)
        while end and (remainder[end - 1] in CONTROL_EDGE or _is_zs(remainder[end - 1])):
            end -= 1
        if end != len(remainder):
            ctrl = any(c in CONTROL_EDGE for c in remainder[end:])
            space = any(_is_zs(c) for c in remainder[end:])
            if ctrl:
                result.flags.append("TRAILING_CONTROL_REMOVED")
            if space:
                result.flags.append("WHITESPACE_AFTER_URL_REMOVED")
            remainder = remainder[:end]
        if any(_is_zs(c) or c in CONTROL_EDGE for c in remainder):
            result.reason = "RAW_WHITESPACE_IN_URL_FORM"
            return result
        # I4: raw '?' or '#' cannot be part of a percent-encoded name.
        if "?" in remainder or "#" in remainder:
            result.reason = "URL_QUERY_OR_FRAGMENT"
            return result
        decoded, error = _percent_decode_once(remainder)  # I5
        if error:
            result.reason = error
            return result
        return _finish(result, decoded)
    # LITERAL or LABEL form. I1b: trailing TAB/LF/CR removed; trailing Zs kept as suffix code points.
    end = len(remainder)
    while end and remainder[end - 1] in CONTROL_EDGE:
        end -= 1
    if end != len(remainder):
        result.flags.append("TRAILING_CONTROL_REMOVED")
        remainder = remainder[:end]
    trailing_spaces = 0
    while trailing_spaces < len(remainder) and _is_zs(remainder[len(remainder) - 1 - trailing_spaces]):
        trailing_spaces += 1
    if form == "LABEL" and PERCENT_HH_RE.search(remainder):
        # I6: visual form vs URI form cannot be decided.
        result.status, result.reason = AMBIGUOUS, "DOI_LABEL_PERCENT_AMBIGUOUS"
        result.flags.append("AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION")
        result.candidates = [_candidate(remainder, "LITERAL_VISUAL_FORM")]
        decoded, error = _percent_decode_once(remainder)
        if not error:
            result.candidates.append(_candidate(decoded, "PERCENT_DECODED_URI_FORM"))
        return result
    if trailing_spaces and trust == OPERATOR_OR_TABULAR_INPUT:
        trimmed = remainder[:len(remainder) - trailing_spaces]
        result.status, result.reason = AMBIGUOUS, "TRAILING_SPACE_UNCERTAIN"
        result.flags.append("AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION")
        result.candidates = [_candidate(remainder, "EXACT_TRAILING_SPACES_KEPT"), _candidate(trimmed, "TRAILING_SPACES_TRIMMED")]
        return result
    return _finish(result, remainder)


def _parse_free_text(raw: str, trust: str) -> DoiParse:
    result = DoiParse(status=INVALID, value_original=raw, context=FREE_TEXT_EXTRACT, source_trust=trust)
    result.flags.append("FREE_TEXT_CANDIDATE_ONLY")
    candidates = []
    for match in FREE_TEXT_START_RE.finditer(raw):
        start = match.start()
        for label in ("https://dx.doi.org/", "http://dx.doi.org/", "https://doi.org/", "http://doi.org/", "urn:doi:", "doi:"):
            if _ascii_lower(raw[max(0, start - len(label)):start]) == label:
                start -= len(label)
                break
        end = match.end()
        while end < len(raw) and not (_is_zs(raw[end]) or raw[end] in CONTROL_EDGE):
            end += 1
        token = raw[start:end]
        trailing = 0
        while trailing < len(token) and token[len(token) - 1 - trailing] in TRAILING_PUNCT:
            trailing += 1
        if trailing:
            variants = []
            for cut in range(0, trailing + 1):
                piece = token[:len(token) - cut] if cut else token
                parsed = _parse_identifier(piece, FREE_TEXT_EXTRACT, STRUCTURED_PROVIDER_FIELD)
                variants.append(dict(parsed.as_dict(), derivation="AS_IS" if not cut else f"TRIMMED_{cut}"))
            candidates.append(dict(status=AMBIGUOUS, reason="TRAILING_PUNCTUATION", token=token, variants=variants,
                                   flags=["AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION"]))
        else:
            parsed = _parse_identifier(token, FREE_TEXT_EXTRACT, STRUCTURED_PROVIDER_FIELD)
            entry = parsed.as_dict()
            entry["token"] = token
            candidates.append(entry)
    result.candidates = candidates
    distinct = {c.get("token") for c in candidates}
    if not candidates:
        result.reason = "NO_DOI_FOUND"
        return result
    if len(distinct) >= 2:
        result.status, result.reason = AMBIGUOUS, "MULTIPLE_CANDIDATES"
        result.flags += ["MULTIPLE_CANDIDATES", "AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION"]
        return result
    only = candidates[0]
    if only["status"] == VALID:
        result.status = VALID
        result.doi, result.comparison_key, result.url_form = only["doi"], only["comparison_key"], only["url_form"]
        result.flags += [f for f in only["flags"] if f not in result.flags]
    else:
        result.status, result.reason = only["status"], only.get("reason")
    return result


def parse_doi(raw, context: str = DOI_IDENTIFIER_FIELD, source_trust: str = STRUCTURED_PROVIDER_FIELD) -> DoiParse:
    if context not in CONTEXTS:
        raise ValueError("unknown DOI context")
    if source_trust not in TRUST_LEVELS:
        raise ValueError("unknown source_trust")
    if context == DOI_LIST_FIELD:
        values = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            return DoiParse(status=INVALID, value_original=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                            context=context, source_trust=source_trust, reason="NOT_A_STRING_LIST")
        elements = [parse_doi(v, DOI_IDENTIFIER_FIELD, source_trust) for v in values]
        statuses = {e.status for e in elements}
        status = VALID if statuses <= {VALID} else (AMBIGUOUS if AMBIGUOUS in statuses else INVALID)
        return DoiParse(status=status, value_original=raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                        context=context, source_trust=source_trust, candidates=[e.as_dict() for e in elements],
                        flags=["LIST_ELEMENTS_PARSED_INDIVIDUALLY"])
    if not isinstance(raw, str):
        raise ValueError("raw DOI must be a string")
    if context == FREE_TEXT_EXTRACT:
        return _parse_free_text(raw, source_trust)
    return _parse_identifier(raw, context, source_trust)
