"""Path safety for data roots and file inputs (S01 06 section 7, 09 section 6).

Rules: refuse symlinks/junctions/reparse points, refuse device/UNC paths,
require containment after canonicalization, and refuse protected roots that
need owner approval (OAG-2: the production data root ``E:\\ResearchKB``).
"""
import os
import re
import stat
from pathlib import Path

from .errors import PolicyRefusal, ValidationError

FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# Owner gate OAG-2: S02 must never create or touch the production data root.
PROTECTED_ROOTS = (r"E:\ResearchKB",)
# The PBD local state folder is never a valid input (S01 09 section 6).
DENIED_FRAGMENTS = (os.sep + "PaperBatchDownloader" + os.sep,)


# Any drive prefix: "C:\x", "C:/x", bare "C:" and drive-relative "E:ResearchKB" / "D:records/sub" (S02 v03, S02-F05).
# A colon later in a POSIX name ("project:v1") is not a drive prefix and stays valid.
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def host_is_windows() -> bool:
    """Patchable in tests so the POSIX branch is exercised deterministically on any host."""
    return os.name == "nt"


def _protected_lexical(text: str) -> bool:
    r"""Protected-root check on the raw text, before any host path resolution (S02 v02, S02-F02).

    On POSIX, ``E:\ResearchKB`` is not a drive path at all; resolving it first would turn it into a
    relative directory name. Comparing the text lexically (case-insensitive, both slash styles) refuses it
    on every OS without touching the filesystem.
    """
    norm = text.replace("/", "\\").rstrip("\\").lower()
    for root in PROTECTED_ROOTS:
        r = root.replace("/", "\\").rstrip("\\").lower()
        if norm == r or norm.startswith(r + "\\"):
            return True
    return False


def _refuse_foreign_windows_path(text: str) -> None:
    """On a non-Windows host, Windows drive paths and backslash paths are refused, never created literally."""
    if not host_is_windows() and (WINDOWS_DRIVE_RE.match(text) or "\\" in text):
        raise ValidationError("Windows-style path on a non-Windows host", code="WINDOWS_PATH_ON_POSIX")


def is_reparse_or_link(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def _refuse_device_paths(text: str) -> None:
    if text.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")) or (text.startswith(("\\\\", "//")) and len(text) > 2):
        raise ValidationError("Device or UNC paths are refused", code="UNSUPPORTED_PATH_FORM")


def _protected(resolved: Path) -> bool:
    norm = os.path.normcase(str(resolved))
    for root in PROTECTED_ROOTS:
        r = os.path.normcase(root)
        if norm == r or norm.startswith(r.rstrip("\\/") + os.sep):
            return True
    return False


def check_no_links_on_path(path: Path) -> None:
    """Refuse if the path or any existing ancestor is a link/reparse point."""
    p = Path(os.path.abspath(path))
    for candidate in [p, *p.parents]:
        if candidate.exists() and is_reparse_or_link(candidate):
            raise ValidationError("Symlink, junction or reparse point refused on data path", code="REPARSE_POINT")


def safe_data_root(path_text: str, *, must_exist: bool) -> Path:
    if not path_text:
        raise ValidationError("Data root required", code="DATA_ROOT_REQUIRED")
    _refuse_device_paths(path_text)
    if _protected_lexical(path_text):
        raise PolicyRefusal("Protected production data root (OAG-2 approval required)", code="PROTECTED_DATA_ROOT")
    _refuse_foreign_windows_path(path_text)
    raw = Path(path_text)
    check_no_links_on_path(raw)
    resolved = Path(os.path.realpath(raw))
    if _protected(resolved):
        raise PolicyRefusal("Protected production data root (OAG-2 approval required)", code="PROTECTED_DATA_ROOT")
    if must_exist and not resolved.is_dir():
        raise ValidationError("Data root does not exist or is not a directory", code="KB_NOT_INITIALIZED")
    return resolved


def refuse_unsafe_path_text(path_text: str) -> None:
    """Lexical checks shared by every operator path entrypoint, run before any filesystem call."""
    _refuse_device_paths(path_text)
    _refuse_foreign_windows_path(path_text)


def safe_child(root: Path, relative: str) -> Path:
    """Join and verify containment; refuses traversal and absolute parts."""
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValidationError("Unsafe relative path", code="PATH_OUTSIDE_ROOT")
    target = Path(os.path.realpath(root / relative))
    if not target.is_relative_to(Path(os.path.realpath(root))):
        raise ValidationError("Path escapes root", code="PATH_OUTSIDE_ROOT")
    return target


def safe_input_file(path_text: str, *, max_bytes: int) -> Path:
    """Validate an operator-supplied input file (fixture directory, DOI list, notice document)."""
    refuse_unsafe_path_text(path_text)
    raw = Path(path_text)
    if any(fragment.lower() in (os.sep + str(Path(os.path.abspath(raw))) + os.sep).lower() for fragment in DENIED_FRAGMENTS):
        raise PolicyRefusal("PaperBatchDownloader local state is never read", code="DENIED_INPUT_LOCATION")
    check_no_links_on_path(raw)
    if not raw.is_file():
        raise ValidationError("Input is not a regular file", code="NOT_REGULAR_FILE")
    size = raw.stat().st_size
    if size > max_bytes:
        raise ValidationError("Input file too large", code="TOO_LARGE")
    return Path(os.path.realpath(raw))
