"""Parser runtime identity and provenance (S06-F01): locate the pinned pypdf and prove it is the reviewed official build.

pypdf is an OPTIONAL dependency (extra ``pdf``: ``pypdf==6.12.1``). Two accepted forms, both checked completely:

* **official wheel file** (``pypdf-6.12.1-py3-none-any.whl``): its SHA-256 must equal the independently recorded PyPI value in
  ``approved_parsers`` (provenance ``OFFICIAL_WHEEL_SHA256_VERIFIED``); the child imports from it (zipimport).
* **installed site directory** (``pypdf/`` + exactly one ``pypdf-<v>.dist-info``): the dist-info folder version, METADATA
  ``Name``/``Version``, the package's own ``_version.py`` and the pin must agree; RECORD must list exactly the package files
  and every hash must match (integrity); and the package-file fingerprint must equal the fingerprint of the official wheel's
  files (provenance ``CONTENT_MATCHES_OFFICIAL_WHEEL``). A self-consistent RECORD is integrity, never provenance.

Fail closed with stable codes: PARSER_NOT_INSTALLED, PARSER_SITE_INVALID, PARSER_METADATA_MISMATCH, PARSER_VERSION_NOT_PINNED,
PARSER_SITE_TAMPERED, PARSER_WHEEL_NOT_OFFICIAL, PARSER_BUILD_NOT_APPROVED, PARSER_PROVENANCE_UNVERIFIED. The child reports the
version and fingerprint of what it actually imported; the parent compares them (PARSER_IDENTITY_MISMATCH).
"""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import os
import re
import zipfile
from email.parser import Parser
from pathlib import Path

from ..core.canonical import canonical_json, sha256_bytes
from ..core.errors import ValidationError
from .approved_parsers import APPROVED, PINNED

PINNED_NAME, PINNED_VERSION = "pypdf", APPROVED[PINNED]["version"]
PINNED_LICENSE = APPROVED[PINNED]["license"]
ENV_SITE = "SCIVETRA_PYPDF_SITE"
_VERSION_PY = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']\s*$""", re.M)


def default_site() -> str | None:
    """``$SCIVETRA_PYPDF_SITE`` (a site directory or the official wheel file), else the directory holding the pypdf
    importable by this interpreter (nothing is imported)."""
    if os.environ.get(ENV_SITE):
        return os.environ[ENV_SITE]
    spec = importlib.util.find_spec("pypdf")
    if spec is None or not spec.origin:
        return None
    return str(Path(spec.origin).resolve().parent.parent)


def fingerprint(entries) -> str:
    """Package-file fingerprint: SHA-256 of canonical JSON of the sorted ``[path, sha256]`` list (``pypdf/...`` paths)."""
    return sha256_bytes(canonical_json(sorted([p, h] for p, h in entries)).encode())


def _fail(message, code):
    raise ValidationError(message, code=code)


def _metadata(text: str) -> dict:
    msg = Parser().parsestr(text)
    return {"name": (msg.get("Name") or "").strip(), "version": (msg.get("Version") or "").strip(),
            "license": (msg.get("License-Expression") or msg.get("License") or "").strip()}


def _record_rows(text: str):
    return [r for r in csv.reader(io.StringIO(text)) if r]


def _b64(data: bytes, algo="sha256") -> str:
    return base64.urlsafe_b64encode(hashlib.new(algo, data).digest()).rstrip(b"=").decode()


def _check_identity(dist_dir_name: str, meta: dict, version_py: str | None):
    m = re.fullmatch(r"pypdf-([^/]+)\.dist-info", dist_dir_name)
    folder_version = m.group(1) if m else None
    declared = _VERSION_PY.search(version_py or "")
    module_version = declared.group(1) if declared else None
    if meta["name"].lower() != PINNED_NAME or not folder_version or len({folder_version, meta["version"], module_version}) != 1:
        _fail(f"pypdf identity disagrees: dist-info folder {folder_version!r}, METADATA {meta['name']!r} {meta['version']!r}, "
              f"_version.py {module_version!r}", "PARSER_METADATA_MISMATCH")
    if meta["version"] != PINNED_VERSION:
        _fail(f"pypdf {meta['version']} is not the pinned {PINNED_VERSION}", "PARSER_VERSION_NOT_PINNED")


def _approve(fp: str, count: int) -> dict:
    approved = APPROVED[PINNED]
    if approved["package_files_sha256"] is None:
        _fail("No official pypdf build fingerprint has been established; provenance cannot be verified", "PARSER_PROVENANCE_UNVERIFIED")
    if fp != approved["package_files_sha256"] or count != approved["package_file_count"]:
        _fail("pypdf package files differ from the reviewed official build", "PARSER_BUILD_NOT_APPROVED")
    return approved


def _verify_wheel(path: Path) -> dict:
    approved = APPROVED[PINNED]
    data = path.read_bytes()
    if sha256_bytes(data) != approved["official_wheel_sha256"]:
        _fail("Wheel SHA-256 differs from the recorded official PyPI release file", "PARSER_WHEEL_NOT_OFFICIAL")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        dists = sorted({n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")})
        if len(dists) != 1:
            _fail("Wheel must hold exactly one dist-info", "PARSER_SITE_INVALID")
        meta = _metadata(z.read(f"{dists[0]}/METADATA").decode("utf-8"))
        _check_identity(dists[0], meta, z.read("pypdf/_version.py").decode("utf-8") if "pypdf/_version.py" in names else None)
        entries = [(n, hashlib.sha256(z.read(n)).hexdigest()) for n in names if n.startswith("pypdf/") and not n.endswith("/")]
    return {"entries": entries, "meta": meta, "provenance": "OFFICIAL_WHEEL_SHA256_VERIFIED", "mode": "OFFICIAL_WHEEL", "wheel_sha256": sha256_bytes(data)}


def _verify_dir(site: Path) -> dict:
    dists = sorted(site.glob("pypdf-*.dist-info"))
    if not (site / "pypdf").is_dir() or len(dists) != 1 or not (dists[0] / "METADATA").is_file() or not (dists[0] / "RECORD").is_file():
        _fail("Parser site must hold pypdf/ and exactly one pypdf dist-info with METADATA and RECORD", "PARSER_SITE_INVALID")
    meta = _metadata((dists[0] / "METADATA").read_text(encoding="utf-8"))
    version_file = site / "pypdf" / "_version.py"
    _check_identity(dists[0].name, meta, version_file.read_text(encoding="utf-8") if version_file.is_file() else None)
    listed, entries = set(), []
    for row in _record_rows((dists[0] / "RECORD").read_text(encoding="utf-8")):
        name, digest = row[0], (row[1] if len(row) > 1 else "")
        if not name.startswith("pypdf/") or name.endswith(".pyc"):
            continue
        if not digest or "=" not in digest:
            _fail("RECORD entry without hash", "PARSER_SITE_TAMPERED")
        algo, value = digest.split("=", 1)
        target = site / name
        if not target.is_file() or _b64(target.read_bytes(), algo) != value:
            _fail("pypdf copy differs from its RECORD", "PARSER_SITE_TAMPERED")
        listed.add(name)
        entries.append((name, hashlib.sha256(target.read_bytes()).hexdigest()))
    present = {p.relative_to(site).as_posix() for p in (site / "pypdf").rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if present != listed:
        _fail("pypdf files and RECORD differ (unlisted or missing files)", "PARSER_SITE_TAMPERED")
    return {"entries": entries, "meta": meta, "provenance": "CONTENT_MATCHES_OFFICIAL_WHEEL", "mode": "SITE_DIRECTORY", "wheel_sha256": None}


def verify_site(site_text: str | None) -> dict:
    """``{"site", "mode", "name", "version", "license", "build_sha256", "files_verified", "provenance", ...}`` or a stable refusal."""
    if not site_text:
        _fail("pypdf is not installed; install the pinned extra (pip install pypdf==6.12.1) or pass --parser-site", "PARSER_NOT_INSTALLED")
    path = Path(site_text).resolve()
    if path.is_file() and path.suffix == ".whl":
        found = _verify_wheel(path)
    elif path.is_dir():
        found = _verify_dir(path)
    else:
        _fail("Parser site is neither a directory nor a wheel file", "PARSER_NOT_INSTALLED")
    fp = fingerprint(found["entries"])
    approved = _approve(fp, len(found["entries"]))
    return {"site": str(path), "mode": found["mode"], "name": PINNED_NAME, "version": found["meta"]["version"],
            "license": approved["license"], "declared_license": found["meta"]["license"], "build_sha256": fp,
            "files_verified": len(found["entries"]), "provenance": found["provenance"],
            "official_wheel_sha256": approved["official_wheel_sha256"], "wheel_sha256": found["wheel_sha256"],
            "integrity": "RECORD_COMPLETE_AND_MATCHING" if found["mode"] == "SITE_DIRECTORY" else "WHEEL_SHA256"}


def identity(rt: dict) -> dict:
    """What the child must report having imported."""
    return {"version": rt["version"], "build_sha256": rt["build_sha256"]}
