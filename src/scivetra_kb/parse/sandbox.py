"""Parent side of the S06 parser sandbox: one bounded child process per staged file.

The child (``parse_child.py``) runs ``-I -S -B`` with an empty environment (SYSTEMROOT only on Windows), stdin closed,
its working directory the private staging folder, OS memory/CPU/process limits it applies to itself, and a wall-clock
timeout enforced here (the process is killed). Output above ``max_output`` bytes is refused. Every failure maps to a
stable reason code; child stderr is never stored (only its length).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from ..metadata.strict_json import UnsafeJson, decode

CHILD = Path(__file__).resolve().with_name("parse_child.py")
DEFAULT_LIMITS = {"timeout_seconds": 60, "memory_bytes": 768 * 1024 * 1024, "cpu_seconds": 45, "max_output": 64 * 1024 * 1024,
                  "max_pages": 2000, "max_chars_per_page": 400_000, "max_total_chars": 20_000_000, "max_stream_bytes": 64 * 1024 * 1024,
                  "max_objects": 500_000, "max_chunks_per_page": 200_000}
# Limits that shape a successful output are part of the artifact configuration; time/memory/CPU only decide failure.
OUTPUT_LIMIT_KEYS = ("max_pages", "max_chars_per_page", "max_total_chars", "max_stream_bytes", "max_objects", "max_chunks_per_page")
FAILURE_CODES = ("HTML_OR_CHALLENGE", "NOT_PDF_HEADER", "TRUNCATED_NO_EOF", "ENCRYPTED", "ZERO_PAGES", "PARSER_REJECTED", "PAGE_LIMIT_EXCEEDED",
                 "OUTPUT_LIMIT_EXCEEDED", "DECOMPRESSION_LIMIT_EXCEEDED", "RESOURCE_LIMIT_EXCEEDED", "PARSER_TIMEOUT", "SANDBOX_REFUSED_EVENT",
                 "PARSER_SANDBOX_UNAVAILABLE", "PARSER_CHILD_FAILED", "PARSER_IDENTITY_MISMATCH", "PARSER_PRIVILEGED_ACCOUNT_REFUSED")


def is_privileged() -> bool:
    """A POSIX root process: RLIMIT_NPROC does not confine it (R-S06-06). Windows: False (Job Object limits still apply)."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _env() -> dict:
    env = {}
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def command(path: Path, site: str, mode: str, limits: dict, self_test: str | None = None) -> list[str]:
    cmd = [sys.executable, "-I", "-S", "-B", str(CHILD), "--input", str(Path(path).resolve()), "--mode", mode, "--pypdf-site", site,
           "--memory", str(limits["memory_bytes"]), "--cpu-seconds", str(limits["cpu_seconds"]), "--max-output", str(limits["max_output"])]
    for key in OUTPUT_LIMIT_KEYS:
        cmd += ["--" + key.replace("_", "-"), str(limits[key])]
    if self_test:
        cmd += ["--self-test", self_test]
    return cmd


def run_child(path: Path, site: str, *, mode: str = "extract", limits: dict | None = None, self_test: str | None = None,
              expected_identity: dict | None = None, allow_privileged: bool = False) -> dict:
    """``{"status": OK|FAILED, "result"|"code", "sandbox": {...}}``; never raises for parser behaviour.

    ``expected_identity`` (``runtime.identity(rt)``): the child must report having imported exactly that version and package
    fingerprint, from inside the given site, or the result is discarded (PARSER_IDENTITY_MISMATCH). A privileged POSIX account is
    refused before any process starts unless ``allow_privileged`` (synthetic fixtures only; recorded)."""
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    started = time.monotonic()
    meta = {"python": sys.version.split()[0], "platform": sys.platform, "wall_clock_limit_seconds": limits["timeout_seconds"],
            "network_guard": "AUDIT_HOOK_LANGUAGE_LEVEL", "privileged_account": is_privileged(), "privileged_account_accepted": bool(allow_privileged)}
    if meta["privileged_account"] and not allow_privileged:
        return {"status": "FAILED", "code": "PARSER_PRIVILEGED_ACCOUNT_REFUSED", "sandbox": meta}
    try:
        proc = subprocess.run(command(path, site, mode, limits, self_test), capture_output=True, timeout=limits["timeout_seconds"],
                              env=_env(), stdin=subprocess.DEVNULL, cwd=str(Path(path).resolve().parent))
    except subprocess.TimeoutExpired:
        return {"status": "FAILED", "code": "PARSER_TIMEOUT", "sandbox": dict(meta, elapsed_ms=int((time.monotonic() - started) * 1000),
                                                                                 exit_code=None, killed=True)}
    meta.update(elapsed_ms=int((time.monotonic() - started) * 1000), exit_code=proc.returncode, stdout_bytes=len(proc.stdout),
                stderr_bytes=len(proc.stderr))
    if len(proc.stdout) > limits["max_output"]:
        return {"status": "FAILED", "code": "OUTPUT_LIMIT_EXCEEDED", "sandbox": meta}
    try:
        payload = decode(proc.stdout.strip() or b"null")
    except UnsafeJson:
        payload = None
    if not isinstance(payload, dict):
        # Killed by the OS limit (Windows job CPU/memory, POSIX SIGXCPU/SIGKILL) or crashed before printing.
        code = "RESOURCE_LIMIT_EXCEEDED" if proc.returncode not in (0, 1, 4) else "PARSER_CHILD_FAILED"
        return {"status": "FAILED", "code": code, "sandbox": meta}
    meta["child"] = payload.get("sandbox")
    if proc.returncode == 4 or "sandbox_error" in payload:
        return {"status": "FAILED", "code": "PARSER_SANDBOX_UNAVAILABLE", "detail": payload.get("sandbox_error"), "sandbox": meta}
    reported = (payload.get("sandbox") or {}).get("parser_identity") or {}
    if expected_identity is not None and (not reported.get("imported_from_site") or {k: reported.get(k) for k in expected_identity} != expected_identity):
        return {"status": "FAILED", "code": "PARSER_IDENTITY_MISMATCH", "detail": str({k: reported.get(k) for k in ("version", "build_sha256")})[:200],
                "sandbox": meta}
    if self_test:
        return {"status": "OK", "result": payload, "sandbox": meta}
    if "error" in payload:
        code = payload["error"] if payload["error"] in FAILURE_CODES else "PARSER_REJECTED"
        return {"status": "FAILED", "code": code, "detail": str(payload.get("detail", ""))[:120], "sandbox": meta}
    return {"status": "OK", "result": payload["result"], "sandbox": meta}
