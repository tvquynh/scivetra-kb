"""Read-only readers for verified Reprovane portable exports used as bridge sources (S01 v03 06 section 2).

* ``read_fulltext_export``: RV-Stage11B ``fulltext_manifest.json`` / ``fulltext_items.jsonl`` / ``manual_queue.jsonl`` /
  ``manual_queue.csv`` / ``SHA256SUMS.txt``. An independent re-implementation of the portable checks of Reprovane
  2.0.1 ``fulltext_acquisition.portable.verify_export`` (pinned commit 6c4793cd): exact inventory, checksums, strict
  JSON, per-item status digests, manual queue = every non-ACQUIRED_AUTO item, counts, release consistency, and
  byte-exact re-rendering of every file. NOT re-implemented: the policy digest (``FullTextPolicy`` canonicalisation);
  that check is covered only by the native ``verify-export`` result the operator may pass (``--rv-verify-json``).
* Stage-11 corpus exports are read with the accepted S03 strict reader (``metadata.rv_import._read_export``).

Hash agreement is self-consistency, never authenticity; the identity of a specific release is bound only when the
operator supplies an expected manifest digest from a trusted channel (06 section 5.1).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from ..core.errors import IntegrityError, ValidationError
from ..core.paths import check_no_links_on_path, refuse_unsafe_path_text, safe_input_file
from ..metadata.rv_import import _read_export, _strict

FULLTEXT_FILES = ("SHA256SUMS.txt", "fulltext_items.jsonl", "fulltext_manifest.json", "manual_queue.csv", "manual_queue.jsonl")
MANUAL_FIELDS = ("corpus_item_id", "publication_entity_id", "titles", "dois", "openalex_work_ids", "landing_pages", "candidates", "status",
                 "reasons", "release_id", "release_manifest_sha256", "fulltext_policy_sha256")
MAX_FILE = 64 * 1024 * 1024
_SUMS_LINE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9_.]+)")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def rv_canonical_json(value) -> str:
    """Reprovane ``core.provenance.canonical_json`` (sorted keys, compact, non-ASCII kept, NaN refused)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def rv_digest(value) -> str:
    return hashlib.sha256(rv_canonical_json(value).encode("utf-8")).hexdigest()


def _render(manifest: dict) -> dict[str, bytes]:
    files = {"fulltext_manifest.json": (rv_canonical_json(manifest) + "\n").encode(),
             "fulltext_items.jsonl": b"".join((rv_canonical_json(s) + "\n").encode() for s in manifest["items"]),
             "manual_queue.jsonl": b"".join((rv_canonical_json(s) + "\n").encode() for s in manifest["manual_queue"])}
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(MANUAL_FIELDS), lineterminator="\n")
    writer.writeheader()
    for row in manifest["manual_queue"]:
        writer.writerow({k: rv_canonical_json(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    files["manual_queue.csv"] = output.getvalue().encode()
    files["SHA256SUMS.txt"] = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in sorted(files.items())).encode()
    return files


def _fail(message: str, code: str = "FULLTEXT_EXPORT_INVALID"):
    raise IntegrityError(message, code=code)


def read_fulltext_export(directory: str, *, expected_manifest_sha256: str | None = None) -> dict:
    """Return {"manifest", "files" (name -> sha256), "sums_sha256", "rv_digest", "verify"} or raise (nothing is written)."""
    refuse_unsafe_path_text(directory)
    root = Path(directory)
    check_no_links_on_path(root)
    if not root.is_dir():
        raise ValidationError("Full-text export directory not found", code="EXPORT_NOT_FOUND")
    entries = list(root.iterdir())
    if sorted(p.name for p in entries) != sorted(FULLTEXT_FILES):
        raise ValidationError("Full-text export must contain exactly the five portable files", code="EXPORT_INVENTORY_MISMATCH")
    data = {name: safe_input_file(str(root / name), max_bytes=MAX_FILE).read_bytes() for name in FULLTEXT_FILES}
    listed = {}
    try:
        lines = data["SHA256SUMS.txt"].decode("utf-8").splitlines()
    except UnicodeDecodeError:
        _fail("SHA256SUMS.txt is not UTF-8", "EXPORT_MALFORMED_UTF8")
    for line in lines:
        match = _SUMS_LINE.fullmatch(line)
        if not match or match[2] in listed:
            _fail("SHA256SUMS.txt lists an unexpected entry", "EXPORT_SUMS_INVALID")
        listed[match[2]] = match[1]
    if set(listed) != set(FULLTEXT_FILES) - {"SHA256SUMS.txt"}:
        _fail("SHA256SUMS.txt does not cover the export files", "EXPORT_SUMS_INVALID")
    for name, digest in listed.items():
        if hashlib.sha256(data[name]).hexdigest() != digest:
            _fail("Export file hash differs from SHA256SUMS.txt", "EXPORT_HASH_MISMATCH")
    m = _strict(data["fulltext_manifest.json"], "fulltext_manifest.json")
    try:
        if not isinstance(m, dict) or m.get("schema") != "fulltext-manifest/1":
            raise ValidationError("Unsupported full-text export schema", code="EXPORT_SCHEMA_UNSUPPORTED")
        for key in ("release_id", "run_id", "fulltext_policy_sha256", "release_manifest_sha256", "manual_queue_sha256"):
            if not isinstance(m.get(key), str) or not m[key]:
                _fail(f"manifest field {key} missing")
        if not _HEX64.fullmatch(m["release_manifest_sha256"]):
            _fail("release_manifest_sha256 is not a SHA-256")
        ids = m["ordered_corpus_item_ids"]
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or [s["corpus_item_id"] for s in m["items"]] != ids:
            _fail("ordered_corpus_item_ids differs from items")
        for status in m["items"]:
            if rv_digest({k: v for k, v in status.items() if k != "status_sha256"}) != status["status_sha256"]:
                _fail("item status digest mismatch")
            if status["status"] not in ("ACQUIRED_AUTO", "MANUAL_REQUIRED"):
                _fail("unknown item status")
            if (status["status"] == "ACQUIRED_AUTO") != (status["selected_artifact_sha256"] is not None):
                _fail("ACQUIRED_AUTO / selected artifact inconsistency")
        unresolved = sorted(s["corpus_item_id"] for s in m["items"] if s["status"] != "ACQUIRED_AUTO")
        if [r["corpus_item_id"] for r in m["manual_queue"]] != unresolved or rv_digest(m["manual_queue"]) != m["manual_queue_sha256"]:
            _fail("manual queue is not exactly the unresolved items")
        by_id = {s["corpus_item_id"]: s for s in m["items"]}
        for row in m["manual_queue"]:
            if set(row) != set(MANUAL_FIELDS):
                _fail("manual queue row has unexpected fields")
            if row["release_id"] != m["release_id"] or row["release_manifest_sha256"] != m["release_manifest_sha256"] \
                    or row["fulltext_policy_sha256"] != m["fulltext_policy_sha256"]:
                _fail("manual queue row from another release/policy")
            item = by_id[row["corpus_item_id"]]
            if row["status"] != item["status"] or row["reasons"] != item["reasons"] or row["publication_entity_id"] != item["publication_entity_id"]:
                _fail("manual queue row differs from its item")
            for name in ("titles", "dois", "openalex_work_ids", "landing_pages", "candidates"):
                if not isinstance(row[name], list):
                    _fail("manual queue list field malformed")
            if not all(isinstance(c, dict) and isinstance(c.get("url"), str) and type(c.get("eligible")) is bool for c in row["candidates"]):
                _fail("candidate malformed")
        artifacts = {a["outcome"]["artifact_sha256"] for s in m["items"] for a in s["attempts"] if a["outcome"]["status"] == "ACQUIRED"}
        if m["artifact_count"] != len(artifacts) or m["failure_count"] != len(unresolved):
            _fail("artifact/failure counts differ")
        rendered = _render(m)
    except (KeyError, TypeError, AttributeError):
        _fail("full-text manifest is malformed")
    if any(data[name] != body for name, body in rendered.items()):
        _fail("export files are not the canonical rendering of the manifest")
    digest = rv_digest(m)
    if expected_manifest_sha256 and digest != expected_manifest_sha256:
        raise IntegrityError("Full-text manifest digest differs from the operator-supplied expected value", code="EXPECTED_MANIFEST_MISMATCH")
    return {"manifest": m, "files": {n: hashlib.sha256(data[n]).hexdigest() for n in FULLTEXT_FILES},
            "sums_sha256": hashlib.sha256(data["SHA256SUMS.txt"]).hexdigest(), "rv_digest": digest,
            "verify": {"verified": True, "mode": "PORTABLE_METADATA_ONLY_NO_DB_NO_PDF_BYTES", "checker": "rkb-independent-rv11b/1",
                       "policy_digest_recomputed": False, "manifest_rv_digest": digest, "item_count": len(m["items"]),
                       "manual_queue_count": len(m["manual_queue"]), "expected_hash_supplied": bool(expected_manifest_sha256),
                       "scope": "self-consistency of the portable files; not a signature or authenticity claim"}}


def read_corpus_export(directory: str, *, expected_manifest_sha256: str | None = None) -> dict:
    export = _read_export(directory, unique_entities=True)
    release_sem = export["manifest"]["release_semantic_manifest_sha256"]
    if expected_manifest_sha256 and expected_manifest_sha256 != release_sem:
        raise IntegrityError("Release manifest hash differs from the operator-supplied expected value", code="EXPECTED_MANIFEST_MISMATCH")
    files = {name: hashlib.sha256(body).hexdigest() for name, body in export["data"].items()}
    return {"manifest": export["manifest"], "items": export["items"], "files": files, "sums_sha256": files["SHA256SUMS.txt"],
            "verify": {"verified": True, "mode": "PORTABLE_METADATA_ONLY_NO_DB_NO_PDF_BYTES", "checker": "rkb-s03-strict-reader",
                       "release_semantic_manifest_sha256": release_sem, "item_count": len(export["items"]),
                       "expected_hash_supplied": bool(expected_manifest_sha256),
                       "scope": "self-consistency of the portable files; not a signature or authenticity claim"}}


def check_native_verify(path_text: str, *, kind: str, expected_digest: str, item_count: int) -> dict:
    """Cross-check an operator-supplied native ``reprovane ... verify-export`` JSON against the independent result."""
    data = safe_input_file(path_text, max_bytes=1024 * 1024).read_bytes()
    result = _strict(data, "rv_verify_json")
    if not isinstance(result, dict):
        raise IntegrityError("native verify result is not an object", code="NATIVE_VERIFY_INVALID")
    if kind == "fulltext":
        ok = result.get("verified") is True and result.get("manifest_sha256") == expected_digest and result.get("item_count") == item_count \
            and result.get("mode") == "PORTABLE_METADATA_ONLY_NO_DB_NO_PDF_BYTES"
    else:
        ok = result.get("valid") is True and result.get("release_manifest_sha256") == expected_digest and result.get("corpus_item_count") == item_count
    if not ok:
        raise IntegrityError("native verify-export result does not match this export", code="NATIVE_VERIFY_MISMATCH")
    return {k: result[k] for k in sorted(result) if isinstance(result[k], (str, int, bool)) and k != "scope"}


__all__ = ["read_fulltext_export", "read_corpus_export", "check_native_verify", "rv_digest"]
