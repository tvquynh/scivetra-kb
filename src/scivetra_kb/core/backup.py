"""Operator-chosen backups, offline backup verification and restore into a new root (S01 v03 10 sections 5-6).

``rkb backup`` never invents a destination (OAG-1): the operator passes ``--target``. Each backup is a new
``rkb-backup-<UTC>`` folder holding a SQLite backup-API copy of the DB, a re-hashed copy of every CAS object,
``backup_manifest.json`` (schema ``rkb-backup/1``) and ``SHA256SUMS.txt``. The manifest is written last: a
folder without it is incomplete and refused by ``verify-backup``. Verification is an integrity comparison
against recorded hashes; it does not authenticate who wrote the backup (no signatures, S01 06 section 5.1).
Manifests hold hashes and counts only, never local paths or secrets.

Referential completeness (S03-F02): every CAS digest named by the database (``cas.referenced_digests``, the same
inventory ``doctor`` uses) must be a healthy regular file. ``create_backup`` refuses an unhealthy source before
creating any folder; ``verify_backup`` recomputes the references from the backup's own DB and never trusts the
manifest or SHA256SUMS for completeness; ``restore_backup`` re-checks the restored root and removes what it wrote
on any failure. Unreferenced but hash-valid objects are copied (doctor reports them); temp files, invalid names and
links in the source CAS are skipped and counted, and are never acceptable inside a backup.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import nullcontext
from pathlib import Path

from .. import __version__
from .canonical import canonical_json, is_sha256_hex, sha256_bytes
from .cas import ContentStore, reference_problems, referenced_digests
from .db import DB_RELATIVE, KB
from .errors import IntegrityError, NotInitializedError, ValidationError
from .ids import Clock
from .lock import writer_lock
from .manifest import bridge_manifest, full_manifest, library_manifest, semantic_manifest, journal_manifest, eligibility_manifest
from .migrations import ledger_status
from .paths import check_no_links_on_path, is_reparse_or_link, refuse_unsafe_path_text, safe_data_root

MANIFEST_NAME, SUMS_NAME = "backup_manifest.json", "SHA256SUMS.txt"
_SUMS_LINE = re.compile(r"([0-9a-f]{64})  (db/kb\.sqlite|backup_manifest\.json)")


def _fault(faults, name, **ctx):
    if faults and name in faults:
        faults[name](**ctx)


def file_sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                return h.hexdigest()
            h.update(block)


def copy_verified(source: Path, target: Path, expected_sha256: str) -> None:
    """Copy by streaming, fsync, then re-hash the copy; a mismatch removes the copy and raises."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(source, "rb") as src, open(target, "xb") as out:
        shutil.copyfileobj(src, out, 1 << 20)
        out.flush()
        os.fsync(out.fileno())
    if file_sha256(target) != expected_sha256:
        target.unlink()
        raise IntegrityError("Copied object does not re-hash to its recorded hash", code="BACKUP_COPY_MISMATCH")


class _Rows:
    """Minimal read adapter so manifest functions can run on a raw read-only connection."""

    def __init__(self, connection):
        self.connection = connection

    def all(self, sql, params=()):
        return [dict(r) for r in self.connection.execute(sql, params).fetchall()]


def _rights_counts(rows) -> dict:
    current = {}
    for r in rows.all("SELECT asset_sha256, document_version_id, rights_class FROM rights_assessment ORDER BY rowid"):
        current[(r["asset_sha256"], r["document_version_id"])] = r["rights_class"]
    counts = {}
    for value in current.values():
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def domain_summary(rows, schema_version: int) -> dict:
    s02 = semantic_manifest(rows)
    out = {"s02_semantic_manifest_sha256": s02["manifest_sha256"], "s02_counts": s02["counts"]}
    if schema_version >= 2:
        lib, full = library_manifest(rows), full_manifest(rows)
        out.update(library_manifest_sha256=lib["manifest_sha256"], library_counts=lib["counts"],
                   semantic_manifest_v2_sha256=full["manifest_sha256"], rights_class_counts=_rights_counts(rows))
    if schema_version >= 3:
        bridge = bridge_manifest(rows)
        out.update(bridge_manifest_sha256=bridge["manifest_sha256"], bridge_counts=bridge["counts"])
    if schema_version >= 4:
        journal = journal_manifest(rows)
        out.update(journal_manifest_sha256=journal["manifest_sha256"], journal_counts=journal["counts"])
    if schema_version >= 5:
        eligibility = eligibility_manifest(rows)
        out.update(eligibility_manifest_sha256=eligibility["manifest_sha256"], eligibility_counts=eligibility["counts"])
    return out


def _reference_failure(problems, code: str, where: str) -> IntegrityError:
    kinds = {}
    for kind, _ in problems:
        kinds[kind] = kinds.get(kind, 0) + 1
    detail = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
    return IntegrityError(f"{where}: {len(problems)} database-referenced CAS object(s) not healthy ({detail}; first {problems[0][1]}); "
                          "run 'rkb doctor --full' and recover the bytes from an earlier verified backup", code=code)


def _check_target(root: Path, target_text: str) -> Path:
    target = safe_data_root(target_text, must_exist=False)
    if target == root or target.is_relative_to(root) or root.is_relative_to(target):
        raise ValidationError("Backup target must be outside the KB data root (and not contain it)", code="BACKUP_TARGET_INSIDE_KB")
    if target.exists() and not target.is_dir():
        raise ValidationError("Backup target is not a directory", code="BACKUP_TARGET_INVALID")
    return target


def create_backup(data_root_text: str, target_text: str, *, clock: Clock | None = None, faults=None, _locked: bool = False) -> dict:
    """Back up a quiescent KB to ``<target>/rkb-backup-<UTC>/`` (operator-selected target only)."""
    clock = clock or Clock()
    if not target_text:
        raise ValidationError("An explicit --target is required (OAG-1: no default backup path)", code="BACKUP_TARGET_REQUIRED")
    root = safe_data_root(data_root_text, must_exist=True)
    if not (root / DB_RELATIVE).is_file():
        raise NotInitializedError("No KB database under this data root")
    target = _check_target(root, target_text)
    stamp = clock.now_utc_text().replace("-", "").replace(":", "").replace(".", "")
    with (nullcontext() if _locked else writer_lock(root)):
        kb = KB(str(root), readonly=True, clock=clock, _allow_older_schema=True)
        try:
            schema = kb.schema["schema_version"]
            # The writer lock is held, so this inventory and the backup-API snapshot below describe the same DB state.
            references = referenced_digests(kb.connection, schema)
            problems = reference_problems(kb.cas.root, references)
            if problems:
                raise _reference_failure(problems, "BACKUP_SOURCE_INCOMPLETE", "Source KB was not backed up")
            target.mkdir(parents=True, exist_ok=True)
            folder = target / f"rkb-backup-{stamp}"
            try:
                folder.mkdir()
            except FileExistsError:
                raise ValidationError("A backup folder with this timestamp already exists", code="BACKUP_TARGET_EXISTS") from None
            (folder / "db").mkdir()
            dest = sqlite3.connect(folder / DB_RELATIVE)
            try:
                kb.connection.backup(dest)
            finally:
                dest.close()
            _fault(faults, "after_db", folder=folder)
            snapshot = sqlite3.connect((folder / DB_RELATIVE).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if referenced_digests(snapshot, schema) != references:
                    raise IntegrityError("Backup DB snapshot references differ from the locked source", code="BACKUP_SNAPSHOT_DRIFT")
            finally:
                snapshot.close()
            db_sha = file_sha256(folder / DB_RELATIVE)
            store = ContentStore(root)
            objects, skipped, total = [], {}, 0
            for path, state in store.iter_objects() or []:
                if state != "OK":
                    skipped[state] = skipped.get(state, 0) + 1
                    continue
                store.verify_object(path.name)
                copy_verified(path, folder / "cas" / "sha256" / path.name[:2] / path.name, path.name)
                objects.append(path.name)
                total += path.stat().st_size
                _fault(faults, "after_object", folder=folder, digest=path.name)
            objects.sort()
            missing = sorted(set(references) - set(objects))
            if missing:
                raise _reference_failure([("CAS_MISSING", d) for d in missing], "BACKUP_SOURCE_INCOMPLETE", "Backup copy incomplete")
            instance = kb.one("SELECT instance_id FROM kb_instance")
            manifest = {
                "schema": "rkb-backup/1", "client_version": __version__, "created_at_utc": clock.now_utc_text(),
                "kb_schema_version": schema, "migration_ledger": kb.schema["migrations"], "kb_instance_id": instance["instance_id"] if instance else None,
                "source_data_root_sha256": sha256_bytes(os.path.normcase(str(root)).encode("utf-8")),
                "db": {"path": DB_RELATIVE.as_posix(), "sha256": db_sha, "bytes": (folder / DB_RELATIVE).stat().st_size},
                "cas": {"object_count": len(objects), "object_list_sha256": sha256_bytes("".join(o + "\n" for o in objects).encode("ascii")),
                        "total_bytes": total, "skipped_entries": skipped, "referenced_object_count": len(references)},
                "domain": domain_summary(kb, schema),
                "not_included": ["Reprovane workspaces", "PaperBatchDownloader state", "secrets", "rebuildable indexes"],
            }
        finally:
            kb.close()
        manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")
        (folder / MANIFEST_NAME).write_bytes(manifest_bytes)
        sums = f"{db_sha}  db/kb.sqlite\n{sha256_bytes(manifest_bytes)}  {MANIFEST_NAME}\n"
        (folder / SUMS_NAME).write_bytes(sums.encode("ascii"))
    return {"backup_dir": str(folder), "backup_name": folder.name, "manifest_sha256": sha256_bytes(manifest_bytes),
            "kb_schema_version": schema, "cas_object_count": len(objects), "cas_referenced_count": len(references), "domain": manifest["domain"]}


def _open_immutable(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def verify_backup(backup_text: str) -> dict:
    """Offline, read-only verification of one backup folder. Raises on any mismatch; writes nothing."""
    from ..metadata.strict_json import UnsafeJson, decode

    refuse_unsafe_path_text(backup_text)
    folder = Path(backup_text)
    check_no_links_on_path(folder)
    if not folder.is_dir():
        raise ValidationError("Backup folder not found", code="BACKUP_NOT_FOUND")
    if not (folder / MANIFEST_NAME).is_file() or not (folder / SUMS_NAME).is_file():
        raise IntegrityError("Backup has no manifest/SHA256SUMS: incomplete or interrupted", code="BACKUP_INCOMPLETE")
    expected_files = {MANIFEST_NAME, SUMS_NAME, "db/kb.sqlite"}
    found, cas_found = set(), []
    for path in folder.rglob("*"):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink() or is_reparse_or_link(path):
            raise IntegrityError("Link inside backup", code="BACKUP_INVENTORY_MISMATCH")
        if path.is_dir():
            continue
        if rel.startswith("cas/sha256/"):
            parts = rel.split("/")
            if len(parts) != 4 or not is_sha256_hex(parts[3]) or parts[2] != parts[3][:2]:
                raise IntegrityError("Unexpected CAS entry in backup", code="BACKUP_INVENTORY_MISMATCH")
            cas_found.append(parts[3])
        else:
            found.add(rel)
    if found != expected_files:
        raise IntegrityError("Backup file inventory differs from rkb-backup/1", code="BACKUP_INVENTORY_MISMATCH")
    listed = {}
    for line in (folder / SUMS_NAME).read_bytes().decode("ascii", errors="strict").splitlines():
        match = _SUMS_LINE.fullmatch(line)
        if not match or match[2] in listed:
            raise IntegrityError("Invalid SHA256SUMS.txt", code="BACKUP_SUMS_INVALID")
        listed[match[2]] = match[1]
    if set(listed) != {"db/kb.sqlite", MANIFEST_NAME}:
        raise IntegrityError("SHA256SUMS.txt does not cover the backup", code="BACKUP_SUMS_INVALID")
    for name, digest in listed.items():
        if file_sha256(folder / name) != digest:
            raise IntegrityError("Backup file hash differs from SHA256SUMS.txt", code="BACKUP_HASH_MISMATCH")
    try:
        manifest = decode((folder / MANIFEST_NAME).read_bytes())
    except UnsafeJson:
        raise IntegrityError("Backup manifest is not strict JSON", code="BACKUP_MANIFEST_INVALID") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != "rkb-backup/1":
        raise IntegrityError("Unsupported backup manifest", code="BACKUP_MANIFEST_INVALID")
    if manifest["db"]["sha256"] != listed["db/kb.sqlite"]:
        raise IntegrityError("Manifest DB hash differs", code="BACKUP_HASH_MISMATCH")
    cas_found.sort()
    for digest in cas_found:
        if file_sha256(folder / "cas" / "sha256" / digest[:2] / digest) != digest:
            raise IntegrityError("Backup CAS object does not re-hash to its name", code="BACKUP_CAS_MISMATCH")
    if len(cas_found) != manifest["cas"]["object_count"] or \
            sha256_bytes("".join(o + "\n" for o in cas_found).encode("ascii")) != manifest["cas"]["object_list_sha256"]:
        raise IntegrityError("Backup CAS object list differs from the manifest", code="BACKUP_CAS_MISMATCH")
    connection = _open_immutable(folder / DB_RELATIVE)
    try:
        status = ledger_status(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        fk = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if integrity != "ok" or fk:
            raise IntegrityError("Backup database fails integrity/foreign-key checks", code="BACKUP_DB_INVALID")
        if status["schema_version"] != manifest["kb_schema_version"] or status["migrations"] != manifest["migration_ledger"]:
            raise IntegrityError("Backup ledger differs from the manifest", code="BACKUP_LEDGER_MISMATCH")
        # Completeness is derived from the backup's own DB, never from the (rewritable) manifest or SHA256SUMS.
        try:
            references = referenced_digests(connection, status["schema_version"])
        except IntegrityError as exc:
            raise IntegrityError(str(exc), code="BACKUP_CAS_REFERENCE_MISSING") from None
        problems = reference_problems(folder / "cas" / "sha256", references)
        if problems:
            raise _reference_failure(problems, "BACKUP_CAS_REFERENCE_MISSING", "Backup is incomplete")
        recorded = manifest["cas"].get("referenced_object_count")
        if recorded is not None and recorded != len(references):
            raise IntegrityError("Backup referenced-object count differs from the manifest", code="BACKUP_CAS_MISMATCH")
        domain = domain_summary(_Rows(connection), status["schema_version"])
    finally:
        connection.close()
    if domain != manifest["domain"]:
        raise IntegrityError("Backup domain manifests differ from the recorded values", code="BACKUP_DOMAIN_MISMATCH")
    return {"valid": True, "backup_name": folder.name, "kb_schema_version": status["schema_version"], "cas_object_count": len(cas_found),
            "cas_referenced_count": len(references), "manifest_sha256": listed[MANIFEST_NAME], "domain": domain,
            "scope": "Integrity comparison against recorded hashes; no authenticity or authorship is established."}


def restore_backup(backup_text: str, new_root_text: str) -> dict:
    """Restore a verified backup into an absent or empty NEW data root, then re-verify it independently."""
    verified = verify_backup(backup_text)
    folder = Path(backup_text)
    root = safe_data_root(new_root_text, must_exist=False)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValidationError("Restore target must be absent or empty", code="RESTORE_TARGET_NOT_EMPTY")
    manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    created_root = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    restored = False
    try:
        (root / "cas" / "sha256").mkdir(parents=True)
        copy_verified(folder / DB_RELATIVE, root / DB_RELATIVE, manifest["db"]["sha256"])
        for path in sorted((folder / "cas" / "sha256").rglob("*")):
            if path.is_file():
                copy_verified(path, root / "cas" / "sha256" / path.name[:2] / path.name, path.name)
        kb = KB(str(root), readonly=True, _allow_older_schema=True)
        try:
            domain = domain_summary(kb, kb.schema["schema_version"])
            objects = sorted(p.name for p, state in kb.cas.iter_objects() or [] if state == "OK")
            problems = reference_problems(kb.cas.root, referenced_digests(kb.connection, kb.schema["schema_version"]))
        finally:
            kb.close()
        if problems:
            raise _reference_failure(problems, "RESTORE_MISMATCH", "Restored KB is incomplete")
        comparison = {"domain_identical": domain == manifest["domain"],
                      "cas_list_identical": sha256_bytes("".join(o + "\n" for o in objects).encode("ascii")) == manifest["cas"]["object_list_sha256"],
                      "db_sha256_identical": file_sha256(root / DB_RELATIVE) == manifest["db"]["sha256"],
                      "cas_references_complete": True}
        if not all(comparison.values()):
            raise IntegrityError("Restored KB differs from the backup manifest", code="RESTORE_MISMATCH")
        restored = True
    finally:
        if not restored:
            # Remove only what this restore wrote (the target was verified absent or empty): no broken KB is left behind.
            shutil.rmtree(root / "cas", ignore_errors=True)
            shutil.rmtree(root / "db", ignore_errors=True)
            if created_root:
                shutil.rmtree(root, ignore_errors=True)
    return {"restored": True, "data_root": str(root), "backup_manifest_sha256": verified["manifest_sha256"],
            "kb_schema_version": verified["kb_schema_version"], "comparison": comparison, "domain": domain}
