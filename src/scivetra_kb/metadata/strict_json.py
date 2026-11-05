"""Strict, bounded JSON decoding for untrusted provider bodies (S02 v03, review finding S02-F04).

Python's ``json.loads`` accepts input that the KB cannot safely persist:
``NaN``/``Infinity``/``-Infinity`` (not JSON at all), numbers that overflow
to an infinite float (``1e9999``, legal JSON), and escaped unpaired UTF-16
surrogates (``"\\ud800"``, legal JSON syntax) that later fail UTF-8 encoding
during canonical serialisation. ``decode`` refuses all of them before any
claim, event or note is built, so the caller can store a refused snapshot
with the exact raw bytes instead of failing mid-transaction.

Every refusal is an ``UnsafeJson`` with a stable ``reason`` and, where a
location exists, a JSON-pointer ``field_path``. Only specific, understood
decoder failures are caught; nothing else is masked.

Reason codes:

* ``UNPARSEABLE_RESPONSE`` - body is not UTF-8 or not JSON (unchanged since v01).
* ``NONSTANDARD_JSON_CONSTANT`` - ``NaN``, ``Infinity`` or ``-Infinity`` token.
* ``NON_FINITE_NUMBER`` - a JSON number that does not fit a finite float.
* ``INTEGER_OUT_OF_RANGE`` - a JSON integer outside signed 64-bit (SQLite INTEGER) range.
* ``UNPAIRED_SURROGATE`` - a string or object key holding a lone surrogate code point.
  For a bad key, ``field_path`` is the containing object; the key is never echoed.
* ``NESTING_TOO_DEEP`` - arrays/objects nested deeper than ``MAX_DEPTH``.
"""
from __future__ import annotations

import json
import math
import re

UNPARSEABLE_RESPONSE = "UNPARSEABLE_RESPONSE"
NONSTANDARD_JSON_CONSTANT = "NONSTANDARD_JSON_CONSTANT"
NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
INTEGER_OUT_OF_RANGE = "INTEGER_OUT_OF_RANGE"
UNPAIRED_SURROGATE = "UNPAIRED_SURROGATE"
NESTING_TOO_DEEP = "NESTING_TOO_DEEP"
REASONS = (UNPARSEABLE_RESPONSE, NONSTANDARD_JSON_CONSTANT, NON_FINITE_NUMBER, INTEGER_OUT_OF_RANGE,
           UNPAIRED_SURROGATE, NESTING_TOO_DEEP)

MAX_DEPTH = 64          # provider documents nest < 10 levels; anything deeper is refused
INT_MIN, INT_MAX = -(2 ** 63), 2 ** 63 - 1
MAX_INT_DIGITS = 20     # longer integer literals are refused without converting them


class UnsafeJson(Exception):
    def __init__(self, reason: str, field_path: str | None = None):
        super().__init__(reason if field_path is None else f"{reason} at {field_path}")
        self.reason = reason
        self.field_path = field_path


class _Rejected:
    """Placeholder left by a decoder hook; the tree walk reports it with its exact path."""
    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason


def _constant(_token: str) -> _Rejected:
    return _Rejected(NONSTANDARD_JSON_CONSTANT)


def _float(text: str):
    value = float(text)
    return value if math.isfinite(value) else _Rejected(NON_FINITE_NUMBER)


def _int(text: str):
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        return _Rejected(INTEGER_OUT_OF_RANGE)
    value = int(text)
    return value if INT_MIN <= value <= INT_MAX else _Rejected(INTEGER_OUT_OF_RANGE)


def _text(value: str, path: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:      # UTF-8 can encode every code point except surrogates
        raise UnsafeJson(UNPAIRED_SURROGATE, path or "/") from None


def _walk(root) -> None:
    """Iterative depth-first check in document order (no recursion, bounded depth)."""
    stack = [(root, "", 0)]
    while stack:
        value, path, depth = stack.pop()
        if isinstance(value, _Rejected):
            raise UnsafeJson(value.reason, path or "/")
        if isinstance(value, str):
            _text(value, path)
        elif isinstance(value, (dict, list)):
            if depth >= MAX_DEPTH:
                raise UnsafeJson(NESTING_TOO_DEEP, path or "/")
            if isinstance(value, dict):
                for key in value:
                    _text(key, path)
                children = [(item, f"{path}/{key}", depth + 1) for key, item in value.items()]
            else:
                children = [(item, f"{path}/{index}", depth + 1) for index, item in enumerate(value)]
            stack.extend(reversed(children))


DECODER_DEPTH = 512     # raw nesting refused BEFORE the C decoder runs (1.0.1): its own limit differs between Python builds
_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]|"', re.S)


def _too_deep_for_decoder(text: str) -> bool:
    """True when brackets outside strings nest deeper than DECODER_DEPTH. Python 3.14 on Linux / macOS decodes 50 000 levels
    without RecursionError where 3.11 and Windows raised it, so the refusal reason and path must not depend on the decoder."""
    depth = 0
    for m in _TOKENS.finditer(text):
        tok = m.group()
        if tok in "[{":
            depth += 1
            if depth > DECODER_DEPTH:
                return True
        elif tok in "]}":
            depth -= 1
        elif tok == '"':            # unterminated string: the decoder reports it as unparseable
            return False
    return False


def decode(body: bytes):
    """Decode an untrusted provider body or raise ``UnsafeJson``."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise UnsafeJson(UNPARSEABLE_RESPONSE) from None
    if _too_deep_for_decoder(text):
        raise UnsafeJson(NESTING_TOO_DEEP) from None
    try:
        value = json.loads(text, parse_constant=_constant, parse_float=_float, parse_int=_int)
    except ValueError:
        raise UnsafeJson(UNPARSEABLE_RESPONSE) from None
    except RecursionError:          # the C decoder's own nesting limit, reached before MAX_DEPTH can be checked
        raise UnsafeJson(NESTING_TOO_DEEP) from None
    _walk(value)
    return value
