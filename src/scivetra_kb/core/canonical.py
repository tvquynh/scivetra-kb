"""Deterministic canonical JSON and hashing.

Canonical form: UTF-8, sorted keys, no insignificant whitespace, non-ASCII
kept as-is (``ensure_ascii=False``). NaN/Infinity are refused. Semantic hashes
are computed over canonical JSON; byte hashes over exact bytes. The two kinds
are never mixed (S01 03 section 1).
"""
import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semantic_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
