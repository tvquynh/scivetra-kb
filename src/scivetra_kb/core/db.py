"""KB database: explicit init, read-only opening for read commands, no implicit migrations.

Layout under the data root::

    db/kb.sqlite          SQLite, journal_mode=DELETE (so read-only opens never need to create -wal/-shm)
    cas/sha256/aa/<hex>   content-addressed bytes
    .rkb-writer.lock      single-writer OS lock
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .. import __version__
from .canonical import canonical_json, semantic_sha256
from .cas import ContentStore
from .errors import ImmutableRecordError, KBError, NotInitializedError, ValidationError
from .ids import Clock, IdProvider
from .lock import writer_lock
from .migrations import apply_all_to_new, compare_ledger
from .paths import safe_data_root

DB_RELATIVE = Path("db") / "kb.sqlite"


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA recursive_triggers = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise KBError("SQLite foreign keys unavailable", code="SQLITE_FEATURE_MISSING")


def init_kb(data_root_text: str, *, clock: Clock | None = None, ids: IdProvider | None = None) -> dict:
    """Create a new KB in a non-existent or empty directory. Never reuses existing data."""
    clock = clock or Clock()
    ids = ids or IdProvider(clock)
    root = safe_data_root(data_root_text, must_exist=False)
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise ValidationError("Data root must be absent or empty for init", code="DATA_ROOT_NOT_EMPTY")
    root.mkdir(parents=True, exist_ok=True)
    (root / "db").mkdir()
    (root / "cas" / "sha256").mkdir(parents=True)
    connection = sqlite3.connect(root / DB_RELATIVE, isolation_level=None)
    try:
        _configure(connection)
        connection.execute("PRAGMA journal_mode = DELETE")
        migrations = apply_all_to_new(connection, clock.now_utc_text())
        instance = ids.new("kbi")
        connection.execute("INSERT INTO kb_instance VALUES (?,?,?)", (instance, clock.now_utc_text(), __version__))
    finally:
        connection.close()
    return dict(initialized=True, data_root=str(root), instance_id=instance, schema_version=len(migrations), migrations=migrations)


class KB:
    """An opened KB. ``readonly=True`` opens SQLite with ``mode=ro`` and never writes."""

    def __init__(self, data_root_text: str, *, readonly: bool, clock: Clock | None = None, ids: IdProvider | None = None,
                 _allow_older_schema: bool = False):
        # ``_allow_older_schema`` is reserved for the explicit backup/upgrade path (core/backup.py, core/upgrade.py) and
        # test fixtures that build an S02 schema-1 database; no CLI command passes it (STATIC-09).
        self.root = safe_data_root(data_root_text, must_exist=True)
        self.db_path = self.root / DB_RELATIVE
        if not self.db_path.is_file():
            raise NotInitializedError("No KB database under this data root; run 'rkb init' explicitly")
        self.readonly = readonly
        self.clock = clock or Clock()
        self.ids = ids or IdProvider(self.clock)
        self.cas = ContentStore(self.root)
        uri = self.db_path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        self.connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        try:
            _configure(self.connection)
            if not readonly:
                self.connection.execute("PRAGMA synchronous = FULL")
                self.connection.execute("PRAGMA busy_timeout = 5000")
            self.schema = compare_ledger(self.connection, allow_older=_allow_older_schema)
        except Exception:
            self.connection.close()
            raise

    # -- statements -------------------------------------------------------
    def execute(self, sql: str, params=()):
        try:
            return self.connection.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "immutable" in message:
                raise ImmutableRecordError(message) from None
            raise ValidationError("Database constraint rejected the operation: " + message, code="CONSTRAINT_REJECTED") from None

    def one(self, sql: str, params=()):
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params=()):
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self):
        if self.readonly:
            raise KBError("Read-only KB handle", code="READ_ONLY")
        if self.connection.in_transaction:
            raise KBError("Nested transaction", code="NESTED_TRANSACTION")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def insert(self, table: str, row: dict) -> dict:
        columns = list(row)
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
        self.execute(sql, [row[c] for c in columns])
        return row

    def insert_unique(self, table: str, row: dict, key_column: str = "semantic_sha256") -> tuple[dict, bool]:
        """Insert unless a row with the same semantic key exists. Returns (row, created)."""
        existing = self.one(f"SELECT * FROM {table} WHERE {key_column} = ?", (row[key_column],))
        if existing:
            return existing, False
        return self.insert(table, row), True

    def now(self) -> str:
        return self.clock.now_utc_text()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@contextmanager
def open_writer(data_root_text: str, **kwargs):
    root = safe_data_root(data_root_text, must_exist=True)
    if not (root / DB_RELATIVE).is_file():
        raise NotInitializedError("No KB database under this data root; run 'rkb init' explicitly")
    with writer_lock(root):
        kb = KB(data_root_text, readonly=False, **kwargs)
        try:
            yield kb
        finally:
            kb.close()


@contextmanager
def open_reader(data_root_text: str, **kwargs):
    kb = KB(data_root_text, readonly=True, **kwargs)
    try:
        yield kb
    finally:
        kb.close()


def json_text(value) -> str:
    return canonical_json(value)


__all__ = ["KB", "init_kb", "open_reader", "open_writer", "json_text", "semantic_sha256"]
