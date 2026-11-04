"""Forward-only migration ledger with exact-byte SHA-256 comparison.

Unlike Reprovane (constructor-time ``apply_migrations``), the KB never applies
migrations implicitly: ``rkb init`` applies them to a brand-new database, and
opening an existing database only *compares* its ledger with the packaged
migrations (S01 v03 10 section 3). The comparison detects drift or tampering
relative to the packaged bytes; it is not cryptographic authentication.
"""
import hashlib
import re
import sqlite3
from importlib import resources

from .errors import SchemaError

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def packaged_migrations():
    folder = resources.files("scivetra_kb.core").joinpath("migrations")
    items = []
    for entry in folder.iterdir():
        match = _NAME.fullmatch(entry.name)
        if not match:
            continue
        data = entry.read_bytes()
        items.append((int(match[1]), entry.name, hashlib.sha256(data).hexdigest(), data.decode("utf-8")))
    items.sort()
    if [v for v, *_ in items] != list(range(1, len(items) + 1)):
        raise SchemaError("Packaged migrations are not contiguous", code="MIGRATION_PACKAGE_INVALID")
    return items


def apply_all_to_new(connection: sqlite3.Connection, applied_at_utc: str) -> list[dict]:
    """Apply every packaged migration to an EMPTY database inside one transaction."""
    if connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
        raise SchemaError("Refusing to migrate a non-empty database", code="KB_NOT_EMPTY")
    migrations = packaged_migrations()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "sha256 TEXT NOT NULL, applied_at_utc TEXT NOT NULL)")
        for version, name, digest, script in migrations:
            # executescript() would COMMIT implicitly; run statements inside our transaction instead.
            for statement in _statements(script):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migration VALUES (?,?,?,?)", (version, name, digest, applied_at_utc))
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return [dict(version=v, name=n, sha256=d) for v, n, d, _ in migrations]


def _statements(script: str):
    pending = ""
    for char in script:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            if re.sub(r"--[^\n]*", "", pending).strip():
                yield pending
            pending = ""
    if re.sub(r"--[^\n]*", "", pending).strip():
        raise SchemaError("Incomplete migration SQL", code="MIGRATION_PACKAGE_INVALID")


def ledger_status(connection: sqlite3.Connection) -> dict:
    """Verify the ledger is an exact prefix of the packaged migrations; never raises for an older (prefix) schema.

    ``state`` is ``CURRENT`` or ``UPGRADE_REQUIRED``. Drift, gaps and unknown future versions raise SchemaError.
    """
    packaged = [dict(version=v, name=n, sha256=d) for v, n, d, _ in packaged_migrations()]
    try:
        rows = [dict(version=r[0], name=r[1], sha256=r[2]) for r in
                connection.execute("SELECT version, name, sha256 FROM schema_migration ORDER BY version")]
    except sqlite3.DatabaseError:
        raise SchemaError("No migration ledger", code="KB_SCHEMA_UNKNOWN") from None
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if [r["version"] for r in rows] != list(range(1, len(rows) + 1)) or user_version != len(rows):
        raise SchemaError("Migration ledger inconsistent with user_version", code="MIGRATION_DRIFT")
    packaged_by_version = {p["version"]: p for p in packaged}
    for row in rows:
        expected = packaged_by_version.get(row["version"])
        if expected is None:
            raise SchemaError("Database schema is newer than this client", code="KB_CLIENT_TOO_OLD")
        if expected != row:
            raise SchemaError("Applied migration differs from packaged bytes", code="MIGRATION_DRIFT")
    return dict(schema_version=len(rows), latest_version=len(packaged), migrations=rows,
                state="CURRENT" if len(rows) == len(packaged) else "UPGRADE_REQUIRED")


def compare_ledger(connection: sqlite3.Connection, *, allow_older: bool = False) -> dict:
    """Return the ledger status; raise SchemaError unless it matches exactly.

    ``allow_older`` is used only by the explicit upgrade/backup path (S03): it accepts an exact older prefix so the
    database can be backed up and copied, never migrated in place.
    """
    status = ledger_status(connection)
    if status["state"] == "UPGRADE_REQUIRED" and not allow_older:
        raise SchemaError("Database requires an explicit 'rkb upgrade --backup-target'", code="KB_UPGRADE_REQUIRED")
    return {k: status[k] for k in ("schema_version", "latest_version", "migrations")} | {"state": status["state"]}


def apply_pending(connection: sqlite3.Connection, applied_at_utc: str) -> list[dict]:
    """Apply the packaged migrations missing from an existing database, in one transaction (upgrade copies only)."""
    status = ledger_status(connection)
    pending = [m for m in packaged_migrations() if m[0] > status["schema_version"]]
    connection.execute("BEGIN IMMEDIATE")
    try:
        for version, name, digest, script in pending:
            for statement in _statements(script):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migration VALUES (?,?,?,?)", (version, name, digest, applied_at_utc))
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return [dict(version=v, name=n, sha256=d) for v, n, d, _ in pending]
