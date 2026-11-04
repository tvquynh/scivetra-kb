"""Content-addressed store: ``<data_root>/cas/sha256/<aa>/<64hex>``.

Bytes are written temp -> fsync -> atomic replace, then re-hashed from disk
(same pattern as Reprovane core/artifacts.py). Existing objects are verified,
never overwritten. CAS placement precedes DB registration, so a crash can only
leave an unreferenced object, which ``rkb doctor`` reports.
"""
import hashlib
import os
import tempfile
from pathlib import Path

from .canonical import is_sha256_hex
from .errors import IntegrityError, ValidationError
from .paths import is_reparse_or_link

# Every DB column that names a CAS object, with the schema version that introduced it (shared by doctor and backup,
# so the two cannot drift). Rows are (min_schema_version, table, column); NULL values are not references.
CAS_REFERENCE_COLUMNS = (
    (1, "provider_snapshot", "response_bytes_sha256"),
    (1, "operator_document", "document_sha256"),
    (2, "pdf_asset", "asset_sha256"),
    (2, "reprovane_release_import", "manifest_bytes_sha256"),
    (2, "reprovane_release_import", "corpus_jsonl_bytes_sha256"),
    (2, "reprovane_release_import", "corpus_csv_bytes_sha256"),
    (2, "reprovane_release_import", "sums_bytes_sha256"),
    (3, "bridge_batch", "manifest_bytes_sha256"),
    (3, "bridge_batch", "input_csv_bytes_sha256"),
    (4, "jh_page", "response_bytes_sha256"),
    (5, "fte_lock", "handoff_manifest_sha256"),
    (6, "parse_artifact", "artifact_sha256"),
    (7, "ai_input_pack", "pack_sha256"),
    (8, "ev_input_pack", "pack_sha256"),
)


def referenced_digests(connection, schema_version: int) -> dict[str, list[str]]:
    """Read-only, schema-aware inventory ``{digest: ["table.column", ...]}`` of all CAS references in one DB snapshot.

    A referencing table that the schema version requires but the DB lacks, or a value that is not lowercase 64-hex,
    raises IntegrityError(CAS_REFERENCE_INVALID): an inventory that cannot be completed is never reported as complete.
    """
    present = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    refs: dict[str, list[str]] = {}
    for since, table, column in CAS_REFERENCE_COLUMNS:
        if schema_version < since:
            continue
        if table not in present:
            raise IntegrityError(f"Schema {schema_version} DB lacks CAS-referencing table {table}", code="CAS_REFERENCE_INVALID")
        for (value,) in connection.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"):
            if not isinstance(value, str) or not is_sha256_hex(value):
                raise IntegrityError(f"{table}.{column} holds a value that is not a SHA-256 digest", code="CAS_REFERENCE_INVALID")
            refs.setdefault(value, []).append(f"{table}.{column}")
    return dict(sorted(refs.items()))


def check_object_at(cas_root: Path, digest: str, *, chunk: int = 1 << 20) -> str | None:
    """Check one referenced object under ``cas_root`` (``.../cas/sha256``). Returns None if healthy, else a stable code:
    CAS_MISSING, CAS_LINK_REFUSED (object or shard is a symlink/junction/reparse point) or CAS_HASH_MISMATCH."""
    shard, path = cas_root / digest[:2], cas_root / digest[:2] / digest
    if is_reparse_or_link(shard) or is_reparse_or_link(path):
        return "CAS_LINK_REFUSED"
    if not path.is_file():
        return "CAS_MISSING"
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            h.update(block)
    return None if h.hexdigest() == digest else "CAS_HASH_MISMATCH"


def reference_problems(cas_root: Path, digests) -> list[tuple[str, str]]:
    """``[(code, digest)]`` for every referenced digest that is not a healthy regular file under ``cas_root``."""
    out = []
    for digest in sorted(digests):
        code = check_object_at(cas_root, digest)
        if code:
            out.append((code, digest))
    return out


class ContentStore:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.root = self.data_root / "cas" / "sha256"

    def path_for(self, digest: str) -> Path:
        if not is_sha256_hex(digest):
            raise ValidationError("Invalid SHA-256", code="INVALID_HASH")
        path = self.root / digest[:2] / digest
        if not Path(os.path.realpath(path.parent if path.parent.exists() else self.data_root)).is_relative_to(Path(os.path.realpath(self.data_root))):
            raise ValidationError("CAS path escapes data root", code="PATH_OUTSIDE_ROOT")
        return path

    def exists(self, digest: str) -> bool:
        return self.path_for(digest).is_file()

    def read(self, digest: str) -> bytes:
        path = self.path_for(digest)
        if is_reparse_or_link(path):
            raise IntegrityError("CAS object is a link", code="CAS_LINK_REFUSED")
        try:
            data = path.read_bytes()
        except OSError:
            raise IntegrityError("CAS object missing", code="CAS_MISSING") from None
        if hashlib.sha256(data).hexdigest() != digest:
            raise IntegrityError("CAS object hash mismatch", code="CAS_HASH_MISMATCH")
        return data

    def put(self, data: bytes) -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise ValidationError("CAS input must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        target = self.path_for(digest)
        if target.exists():
            self.read(digest)  # verify existing bytes; never overwrite
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".put-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self.read(digest)
        return digest

    def put_file(self, source: Path, expected_sha256: str, *, chunk: int = 1 << 20) -> str:
        """Stream a staged file into CAS (temp -> fsync -> replace), then re-hash the CAS copy (G2).

        Raises IntegrityError(CAS_MISMATCH) if the bytes read differ from ``expected_sha256`` or the placed object
        does not re-hash to it. Existing objects are verified, never overwritten.
        """
        target = self.path_for(expected_sha256)
        if target.exists():
            self.verify_object(expected_sha256)
            return expected_sha256
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".put-", dir=str(target.parent))
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
                while True:
                    block = src.read(chunk)
                    if not block:
                        break
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if digest.hexdigest() != expected_sha256:
                raise IntegrityError("Staged bytes differ from the intake hash", code="CAS_MISMATCH")
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        try:
            self.verify_object(expected_sha256)
        except IntegrityError:
            raise IntegrityError("CAS copy does not re-hash to the intake hash", code="CAS_MISMATCH") from None
        return expected_sha256

    def verify_object(self, digest: str, *, chunk: int = 1 << 20) -> None:
        """Re-hash one object by streaming (large assets are never loaded whole)."""
        path = self.path_for(digest)
        if is_reparse_or_link(path):
            raise IntegrityError("CAS object is a link", code="CAS_LINK_REFUSED")
        h = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                while True:
                    block = handle.read(chunk)
                    if not block:
                        break
                    h.update(block)
        except OSError:
            raise IntegrityError("CAS object missing", code="CAS_MISSING") from None
        if h.hexdigest() != digest:
            raise IntegrityError("CAS object hash mismatch", code="CAS_HASH_MISMATCH")

    def iter_objects(self):
        """Yield ``(path, state)`` for every CAS entry; ``state`` is OK, TEMP_FILE, INVALID_NAME, LINK or UNEXPECTED_ENTRY."""
        if not self.root.exists():
            return
        for shard in sorted(self.root.iterdir()):
            if not shard.is_dir():
                yield shard, "UNEXPECTED_ENTRY"
                continue
            for obj in sorted(shard.iterdir()):
                if obj.name.startswith(".put-"):
                    yield obj, "TEMP_FILE"
                elif not is_sha256_hex(obj.name) or obj.name[:2] != shard.name:
                    yield obj, "INVALID_NAME"
                elif is_reparse_or_link(obj):
                    yield obj, "LINK"
                else:
                    yield obj, "OK"
