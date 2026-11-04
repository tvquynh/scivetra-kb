"""Trust-boundary shape validation for provider JSON (S02 v02, review finding S02-F01).

Provider responses are untrusted. Before any claim decomposition, every field a
parser reads is checked against the type it expects. A mismatch raises
``MalformedResponse`` with a JSON-pointer ``field_path``; parsers catch exactly
this exception (never a broad ``except``) and return a controlled
``REFUSED`` result with reason ``MALFORMED_FIELD_TYPE``. ``None``/JSON null is
treated as "absent" for optional fields. Unknown extra fields are ignored.
"""
from __future__ import annotations

MALFORMED_FIELD_TYPE = "MALFORMED_FIELD_TYPE"


class MalformedResponse(Exception):
    def __init__(self, field_path: str, expected: str):
        super().__init__(f"{field_path}: expected {expected}")
        self.field_path = field_path or "/"
        self.expected = expected


def _name(kind) -> str:
    if isinstance(kind, tuple):
        return "|".join(_name(k) for k in kind)
    return {dict: "object", list: "array", str: "string", int: "integer", bool: "boolean", float: "number"}.get(kind, str(kind))


def check(value, kind, path: str, *, required: bool = False):
    """Return ``value`` if it has type ``kind`` (or is None and not required); raise otherwise.

    ``bool`` is never accepted where ``int`` is expected (JSON true/false are not numbers).
    """
    if value is None:
        if required:
            raise MalformedResponse(path, _name(kind))
        return None
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if isinstance(value, bool) and bool not in kinds:
        raise MalformedResponse(path, _name(kind))
    if not isinstance(value, kinds):
        raise MalformedResponse(path, _name(kind))
    return value


def obj(container: dict, key: str, path: str, *, required: bool = False) -> dict | None:
    return check(container.get(key), dict, f"{path}/{key}", required=required)


def arr(container: dict, key: str, path: str) -> list | None:
    return check(container.get(key), list, f"{path}/{key}")


def scalar(container: dict, key: str, path: str, kind=str, *, required: bool = False):
    return check(container.get(key), kind, f"{path}/{key}", required=required)


def each(items: list | None, kind, path: str) -> list:
    """Check every element of a list; returns [] for None."""
    for index, item in enumerate(items or []):
        check(item, kind, f"{path}/{index}", required=True)
    return list(items or [])


def date_object(container: dict, key: str, path: str) -> None:
    """Crossref date object: {"date-parts": [[y, m?, d?]], "date-time": str, "timestamp": int}."""
    value = obj(container, key, path)
    if value is None:
        return
    here = f"{path}/{key}"
    parts = arr(value, "date-parts", here)
    for i, row in enumerate(each(parts, list, f"{here}/date-parts")):
        for j, part in enumerate(row):
            check(part, int, f"{here}/date-parts/{i}/{j}")  # null allowed: Crossref uses [[null]] for unknown dates
    scalar(value, "date-time", here)
    scalar(value, "timestamp", here, (int, float))
