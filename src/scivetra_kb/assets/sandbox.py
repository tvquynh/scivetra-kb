"""Parent side of the G1 validator sandbox: one bounded, isolated, no-network child process per file.

The child (``pdf_child.py``) runs with ``-I -S -B`` (no user site, no sitecustomize, no bytecode), an empty
environment (only SYSTEMROOT on Windows), a wall-clock timeout enforced here and a memory cap it applies to itself.
Every failure maps to a stable G1 reason code; the result JSON is decoded through the strict decoder.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..core.errors import KBError
from ..metadata.strict_json import UnsafeJson, decode

CHILD = Path(__file__).resolve().with_name("pdf_child.py")
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024
G1_REASONS = ("NOT_PDF_HEADER", "HTML_OR_CHALLENGE", "TRUNCATED_NO_EOF", "PARSER_REJECTED", "ZERO_PAGES", "ENCRYPTED", "PARSER_TIMEOUT",
              "PARSER_SANDBOX_UNAVAILABLE")


def _command(path: Path, memory: int, pypdf_site: str | None, self_test: str | None) -> list[str]:
    cmd = [sys.executable, "-I", "-S", "-B", str(CHILD), "--input", str(Path(path).resolve()), "--memory", str(memory)]
    if pypdf_site:
        cmd += ["--pypdf-site", str(pypdf_site)]
    if self_test:
        cmd += ["--self-test", self_test]
    return cmd


def _env() -> dict:
    env = {}
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def run_child(path: Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, memory: int = DEFAULT_MEMORY_BYTES, pypdf_site: str | None = None,
              self_test: str | None = None) -> dict:
    try:
        proc = subprocess.run(_command(path, memory, pypdf_site, self_test), capture_output=True, timeout=timeout, env=_env(),
                              stdin=subprocess.DEVNULL, cwd=str(Path(path).resolve().parent))
    except subprocess.TimeoutExpired:
        return {"sandbox": "TIMEOUT"}
    try:
        payload = decode(proc.stdout.strip() or b"null")
    except UnsafeJson:
        payload = None
    if proc.returncode == 4 and isinstance(payload, dict) and "sandbox_error" in payload:
        return {"sandbox": "UNAVAILABLE", "detail": payload["sandbox_error"]}
    if proc.returncode != 0 or not isinstance(payload, dict):
        return {"sandbox": "CHILD_FAILED", "exit_code": proc.returncode}
    return {"sandbox": "OK", "result": payload}


def validate_file(path: Path, **kwargs) -> dict:
    """Return ``{"outcome": "STRUCTURALLY_VALID"|"REJECTED", "reasons": [...], "result": {...}}`` (G1)."""
    run = run_child(path, **kwargs)
    if run["sandbox"] == "TIMEOUT":
        return {"outcome": "REJECTED", "reasons": ["PARSER_TIMEOUT"], "result": {"validator": "rkb-pdf-structure", "validator_version": "1"}}
    if run["sandbox"] == "UNAVAILABLE":
        return {"outcome": "REJECTED", "reasons": ["PARSER_SANDBOX_UNAVAILABLE"],
                "result": {"validator": "rkb-pdf-structure", "validator_version": "1", "detail": run["detail"]}}
    if run["sandbox"] != "OK":
        return {"outcome": "REJECTED", "reasons": ["PARSER_REJECTED"],
                "result": {"validator": "rkb-pdf-structure", "validator_version": "1", "child_exit_code": run.get("exit_code")}}
    result = run["result"]
    reasons = sorted({e for e in result.get("errors", []) if e in G1_REASONS} | ({"PARSER_REJECTED"} if any(
        e not in G1_REASONS for e in result.get("errors", [])) else set()))
    return {"outcome": "REJECTED" if reasons else "STRUCTURALLY_VALID", "reasons": reasons, "result": result}


# -- G1 validator v2 (S06, C17): legacy checks first, pinned-parser decision only for modern cross-reference syntax ---------
MODERN_SYNTAX_DETAILS = ("xref stream or invalid xref (unsupported)", "object stream (unsupported)")
_V2_PASSTHROUGH = {"ENCRYPTED": "ENCRYPTED", "ZERO_PAGES": "ZERO_PAGES", "PARSER_TIMEOUT": "PARSER_TIMEOUT", "HTML_OR_CHALLENGE": "HTML_OR_CHALLENGE",
                   "NOT_PDF_HEADER": "NOT_PDF_HEADER", "TRUNCATED_NO_EOF": "TRUNCATED_NO_EOF", "PARSER_SANDBOX_UNAVAILABLE": "PARSER_SANDBOX_UNAVAILABLE",
                   # S06 v02: an unverified parser identity or a privileged account means the sandbox is not established (fail closed)
                   "PARSER_IDENTITY_MISMATCH": "PARSER_SANDBOX_UNAVAILABLE", "PARSER_PRIVILEGED_ACCOUNT_REFUSED": "PARSER_SANDBOX_UNAVAILABLE"}


def validate_file_v2(path: Path, *, parser_site: str | None, timeout: float = DEFAULT_TIMEOUT_SECONDS, memory: int = DEFAULT_MEMORY_BYTES,
                     allow_privileged: bool = False) -> dict:
    """``rkb-pdf-structure/2``. Every legacy (v1) check still runs first and keeps its verdict: HTML/challenge, header, EOF,
    encryption, zero pages, timeouts and every structural rejection OTHER than "this file uses an xref stream / object
    stream" stay rejections. Files the legacy parser accepts must ALSO pass the pinned pypdf strict validation with the same
    page count. Files it refuses only for modern cross-reference syntax are decided by the pinned pypdf strict validation
    (every xref/object-stream entry resolves, page tree bounded and consistent, every content stream decodes within limits).
    No parser available => PARSER_SANDBOX_UNAVAILABLE (fail closed)."""
    from ..parse import runtime
    from ..parse import sandbox as parse_sandbox
    legacy = validate_file(path, timeout=timeout, memory=memory)
    legacy_detail = legacy["result"].get("parser_detail")
    modern = legacy["reasons"] == ["PARSER_REJECTED"] and legacy_detail in MODERN_SYNTAX_DETAILS
    base = {"validator": "rkb-pdf-structure", "validator_version": "2", "legacy_validator_version": "1", "legacy_reasons": legacy["reasons"],
            "legacy_detail": legacy_detail}
    if legacy["outcome"] == "REJECTED" and not modern:
        return {"outcome": "REJECTED", "reasons": legacy["reasons"], "result": dict(legacy["result"], **base)}
    try:
        rt = runtime.verify_site(parser_site or runtime.default_site())
    except KBError as exc:
        return {"outcome": "REJECTED", "reasons": ["PARSER_SANDBOX_UNAVAILABLE"], "result": dict(base, parser_runtime_error=exc.code)}
    limits = dict(parse_sandbox.DEFAULT_LIMITS, timeout_seconds=timeout, memory_bytes=max(memory, 256 * 1024 * 1024))
    run = parse_sandbox.run_child(path, rt["site"], mode="validate", limits=limits, expected_identity=runtime.identity(rt),
                                  allow_privileged=allow_privileged)
    parser = {"name": rt["name"], "version": rt["version"], "build_sha256": rt["build_sha256"], "provenance": rt["provenance"]}
    if run["status"] != "OK":
        reason = _V2_PASSTHROUGH.get(run["code"], "PARSER_REJECTED")
        return {"outcome": "REJECTED", "reasons": [reason], "result": dict(base, parser=parser, parser_code=run["code"])}
    res = run["result"]
    if not modern and res["page_count"] != legacy["result"]["page_count"]:
        return {"outcome": "REJECTED", "reasons": ["PARSER_REJECTED"], "result": dict(base, parser=parser, parser_detail="validators disagree on page count")}
    result = dict(base, header_ok=True, eof_ok=True, encrypted=False, errors=[], page_count=res["page_count"], parser=parser,
                  decided_by="PINNED_PARSER_STRICT" if modern else "LEGACY_AND_PINNED_PARSER", syntax=res["syntax"],
                  active_content=res["active_content"], parser_warnings=res["parser_warnings"], info=res.get("info", {}),
                  pages_text=[p["text"] for p in res["pages"]] if modern else legacy["result"].get("pages_text", []))
    if not modern:
        result["info"] = legacy["result"].get("info", {})
    return {"outcome": "STRUCTURALLY_VALID", "reasons": [], "result": result}
