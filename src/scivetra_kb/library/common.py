"""Shared helpers for library writes: operator labels, semantic references, append-only state projections."""
from __future__ import annotations

import re

from ..core.canonical import canonical_json, semantic_sha256
from ..core.errors import ValidationError

_OPERATOR = re.compile(r"[A-Za-z0-9._-]{1,64}")


def operator_label(operator: str) -> str:
    """``reviewer.a`` -> ``OPERATOR:reviewer.a``. A free label, never an email address or a credential."""
    text = operator[len("OPERATOR:"):] if isinstance(operator, str) and operator.startswith("OPERATOR:") else operator
    if not isinstance(text, str) or not _OPERATOR.fullmatch(text):
        raise ValidationError("Operator label must match [A-Za-z0-9._-]{1,64} (no email addresses)", code="OPERATOR_LABEL_INVALID")
    return "OPERATOR:" + text


def require_reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValidationError("A non-empty reason (<= 2000 characters) is required", code="REASON_REQUIRED")
    return reason


def sem_of(kb, table: str, key_column: str, key) -> str:
    row = kb.one(f"SELECT semantic_sha256 FROM {table} WHERE {key_column}=?", (key,))
    if not row:
        raise ValidationError(f"Unknown {table} reference", code="UNKNOWN_REFERENCE")
    return row["semantic_sha256"]


def latest(kb, table: str, column: str, value, *, order="rowid"):
    return kb.one(f"SELECT * FROM {table} WHERE {column}=? ORDER BY {order} DESC LIMIT 1", (value,))


def insert_chained(kb, table: str, id_column: str, prefix: str, row: dict, *, chain_column: str, chain_value, content: dict) -> tuple[dict, bool]:
    """Append an S-column event whose semantic hash includes the previous event of the same chain.

    Repeating the same transition after an intervening different one therefore creates a new row, while an
    identical retry of the latest transition is an idempotent no-op.
    """
    prior = latest(kb, table, chain_column, chain_value)
    if prior is not None and all(prior.get(k) == v for k, v in row.items() if k not in ("recorded_at_utc", "decided_at_utc")):
        return prior, False
    full = dict(row)
    full[id_column] = kb.ids.new(prefix)
    prior_column = next(c for c in ("prior_event_id", "prior_decision_id") if c in _columns(kb, table))
    full[prior_column] = prior[id_column] if prior else None
    full["semantic_sha256"] = semantic_sha256({"content": content, "prior": prior["semantic_sha256"] if prior else None})
    kb.insert(table, full)
    return full, True


_COLUMN_CACHE: dict = {}


def _columns(kb, table: str) -> set:
    if table not in _COLUMN_CACHE:
        _COLUMN_CACHE[table] = {r["name"] for r in kb.all(f"PRAGMA table_info({table})")}
    return _COLUMN_CACHE[table]


def json_text(value) -> str:
    return canonical_json(value)
