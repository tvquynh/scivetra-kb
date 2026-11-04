"""Static checks for the S02 deliverable tree: byte-compile, secret scan, forbidden-file scan.

Usage: python -I tools/static_checks.py <tree-root> [--out evidence/static_checks.json]
Exit code 0 only if every check passes.
"""
import json
import py_compile
import re
import sys
import tempfile
from pathlib import Path

FORBIDDEN_SUFFIXES = {".pdf", ".exe", ".dll", ".whl", ".sqlite", ".db", ".pyc", ".pyd", ".so", ".zip", ".egg"}
FORBIDDEN_DIRS = {".git", ".venv", "venv", "__pycache__", "build", "dist", ".eggs"}
SECRET_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.invalid\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "bearer": re.compile(r"Bearer\s+[A-Za-z0-9._-]{12,}"),
    "api_key_assign": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]{8,}['\"]"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "user_profile_path": re.compile(r"(?i)C:\\Users\\[^\\<>\s]+\\"),
}
ALLOWED_EMAIL_CONTEXT = ()
# Deliberate negative-test literals (not secrets): (file, exact match).
ALLOWED_HITS = {("tests/test_operational.py", "pw@api.crossref.org"),  # user-info URL refusal test
                ("tools/static_checks.py", "pw@api.crossref.org")}  # this allowlist line itself


def main(argv):
    root = Path(argv[1]).resolve()
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    report = {"root_name": root.name, "compiled": 0, "compile_errors": [], "forbidden_files": [], "secret_hits": [], "files_scanned": 0}
    with tempfile.TemporaryDirectory() as tmp:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if any(part in FORBIDDEN_DIRS for part in path.relative_to(root).parts):
                if path.is_dir() and path.name in FORBIDDEN_DIRS:
                    report["forbidden_files"].append(rel + "/")
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() in FORBIDDEN_SUFFIXES:
                report["forbidden_files"].append(rel)
            if path.suffix == ".py":
                try:
                    py_compile.compile(str(path), cfile=str(Path(tmp) / (str(report["compiled"]) + ".pyc")), doraise=True)
                    report["compiled"] += 1
                except py_compile.PyCompileError as exc:
                    report["compile_errors"].append(f"{rel}: {exc.msg[:200]}")
            if path.suffix.lower() in {".py", ".md", ".json", ".txt", ".csv", ".log", ".toml", ".sql", ".ps1"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                report["files_scanned"] += 1
                for name, pattern in SECRET_PATTERNS.items():
                    for match in pattern.finditer(text):
                        if match.group(0) in ALLOWED_EMAIL_CONTEXT or (rel, match.group(0)) in ALLOWED_HITS:
                            report["allowlisted"] = report.get("allowlisted", []) + [{"file": rel, "excerpt": match.group(0)}]
                            continue
                        report["secret_hits"].append({"file": rel, "kind": name, "excerpt": match.group(0)[:40]})
    report["ok"] = not (report["compile_errors"] or report["forbidden_files"] or report["secret_hits"])
    text = json.dumps(report, indent=1, ensure_ascii=False) + "\n"
    if out:
        out.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
