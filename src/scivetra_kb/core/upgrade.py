"""Explicit, backup-first schema upgrade: ``rkb upgrade --data-root R --backup-target T`` (S01 v03 10 section 3).

Order: verify ledger -> writer lock -> verified backup to the operator target -> SQLite backup-API copy to
``db/kb.sqlite.upgrade-<UTC>`` -> apply pending migrations to the COPY only -> integrity, foreign-key, ledger and
domain verification (every pre-existing 0001/0002 table byte-identical, S02 and S03 library manifests identical) -> keep the original as
``db/kb.sqlite.pre-<from>-<UTC>`` -> atomic replace. Any failure before the replace removes the copy and leaves the
original database untouched. There are no down-migrations; rollback = restore the ``pre`` file or the backup.
No other command ever migrates (reads, doctor, import and constructors refuse with KB_UPGRADE_REQUIRED).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .. import __version__
from .backup import _fault, copy_verified, create_backup, file_sha256
from .db import DB_RELATIVE, KB, _configure
from .errors import IntegrityError, KBError, NotInitializedError, SchemaError
from .ids import Clock, IdProvider
from .lock import writer_lock
from .manifest import library_manifest, semantic_manifest, table_content_sha256
from .migrations import apply_pending, ledger_status
from .paths import safe_data_root

S02_TABLES = ("kb_instance", "global_paper", "identity_assertion", "provider_snapshot", "fetch_run", "provider_check", "request_reservation",
              "request_completion", "metadata_claim", "ra_claim", "metadata_change_event", "integrity_event", "integrity_event_sighting",
              "integrity_coverage", "operator_document", "integrity_counter_evidence", "review_task", "integrity_review",
              "normalization_divergence", "identifier_warning", "reprovane_export_import")


def _content(connection) -> dict:
    return {t: table_content_sha256(connection, t) for t in S02_TABLES}


def _prior_tables(connection) -> list[str]:
    return sorted(r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                                   "AND name <> 'schema_migration'"))


def _all_content(connection, tables) -> dict:
    return {t: table_content_sha256(connection, t) for t in tables}


def upgrade_kb(data_root_text: str, backup_target_text: str, *, clock: Clock | None = None, ids: IdProvider | None = None,
               faults=None) -> dict:
    clock = clock or Clock()
    ids = ids or IdProvider(clock)
    root = safe_data_root(data_root_text, must_exist=True)
    db_path = root / DB_RELATIVE
    if not db_path.is_file():
        raise NotInitializedError("No KB database under this data root; run 'rkb init' explicitly")
    with writer_lock(root):
        probe = KB(str(root), readonly=True, clock=clock, _allow_older_schema=True)
        try:
            status = ledger_status(probe.connection)
            if status["state"] == "CURRENT":
                return {"upgraded": False, "code": "UPGRADE_NOT_REQUIRED", "schema_version": status["schema_version"]}
            before_manifest = semantic_manifest(probe)
            before_content = _content(probe.connection)
            prior_tables = _prior_tables(probe.connection)
            before_all = _all_content(probe.connection, prior_tables)
            before_library = library_manifest(probe)["manifest_sha256"] if status["schema_version"] >= 2 else None
        finally:
            probe.close()
        original_sha = file_sha256(db_path)
        backup = create_backup(str(root), backup_target_text, clock=clock, faults=faults, _locked=True)
        stamp = clock.now_utc_text().replace("-", "").replace(":", "").replace(".", "")
        copy_path = db_path.with_name(f"kb.sqlite.upgrade-{stamp}")
        pre_path = db_path.with_name(f"kb.sqlite.pre-{status['schema_version']}-{stamp}")
        if copy_path.exists() or pre_path.exists():
            raise KBError("Upgrade work file already exists", code="UPGRADE_WORKFILE_EXISTS")
        try:
            source = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
            dest = sqlite3.connect(copy_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
            _fault(faults, "after_copy", copy_path=copy_path)
            connection = sqlite3.connect(copy_path, isolation_level=None)
            try:
                _configure(connection)
                connection.execute("PRAGMA journal_mode = DELETE")
                if ledger_status(connection)["migrations"] != status["migrations"]:
                    raise IntegrityError("Upgrade copy ledger differs from the original", code="UPGRADE_COPY_INVALID")
                applied = apply_pending(connection, clock.now_utc_text())
                _fault(faults, "after_migrate", connection=connection)
                checks = {"integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
                          "foreign_key_violations": len(connection.execute("PRAGMA foreign_key_check").fetchall())}
                new_status = ledger_status(connection)
                after_manifest = semantic_manifest(_Reader(connection))
                after_content = _content(connection)
                checks.update(ledger_state=new_status["state"], s02_semantic_manifest_identical=after_manifest == before_manifest,
                              s02_tables_byte_identical=after_content == before_content,
                              prior_tables_byte_identical=_all_content(connection, prior_tables) == before_all)
                if before_library is not None:
                    checks["library_manifest_identical"] = library_manifest(_Reader(connection))["manifest_sha256"] == before_library
                if checks["integrity_check"] != "ok" or checks["foreign_key_violations"] or new_status["state"] != "CURRENT" \
                        or not checks["s02_semantic_manifest_identical"] or not checks["s02_tables_byte_identical"] \
                        or not checks["prior_tables_byte_identical"] or checks.get("library_manifest_identical") is False:
                    raise IntegrityError("Upgraded copy failed verification: " + canonical_failures(checks), code="UPGRADE_VERIFICATION_FAILED")
                connection.execute("INSERT INTO kb_upgrade_event VALUES (?,?,?,?,?,?,?)", (
                    ids.new("kue"), status["schema_version"], new_status["schema_version"], backup["manifest_sha256"],
                    before_manifest["manifest_sha256"], __version__, clock.now_utc_text()))
            finally:
                connection.close()
            _fault(faults, "before_swap", copy_path=copy_path)
            if file_sha256(db_path) != original_sha:
                raise IntegrityError("Original database changed during the upgrade", code="UPGRADE_SOURCE_CHANGED")
            copy_verified(db_path, pre_path, original_sha)
            os.replace(copy_path, db_path)
        except BaseException:
            if copy_path.exists():
                copy_path.unlink()
            if pre_path.exists() and file_sha256(db_path) == original_sha:
                pre_path.unlink()
            raise
    return {"upgraded": True, "from_version": status["schema_version"], "to_version": new_status["schema_version"], "applied": applied,
            "checks": checks, "backup": {k: backup[k] for k in ("backup_name", "manifest_sha256", "cas_object_count")},
            "pre_upgrade_file": pre_path.name, "original_db_sha256": original_sha,
            "s02_semantic_manifest_sha256": before_manifest["manifest_sha256"],
            "rollback": "stop rkb, replace db/kb.sqlite with the pre-upgrade file (or restore the backup into a new root)"}


class _Reader:
    def __init__(self, connection):
        self.connection = connection

    def all(self, sql, params=()):
        cursor = self.connection.execute(sql, params)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def canonical_failures(checks: dict) -> str:
    return ",".join(sorted(k for k, v in checks.items() if v not in ("ok", 0, True, "CURRENT")))


__all__ = ["upgrade_kb", "SchemaError"]
