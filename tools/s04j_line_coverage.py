"""Line coverage of the S04J journal package with the standard library only (Python >= 3.12 ``sys.monitoring``).

No third-party coverage tool is installed on the implementation host and nothing is downloaded. This tool registers a
LINE monitor, runs the S04J test modules in-process under the network guard, and reports, per file of
``src/scivetra_kb/journal``, executed lines / executable lines (executable = every line number in the compiled code
objects' line tables, docstrings included). Child processes started by tests are not traced.
Usage:  python -I tools/s04j_line_coverage.py <out.json>
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
MODULES = ["tests.test_journal_identity", "tests.test_journal_crawl", "tests.test_journal_reconcile", "tests.test_journal_export",
           "tests.test_journal_boundaries", "tests.test_journal_migration", "tests.test_journal_batch_cli", "tests.test_s04j_static"]


def executable_lines(path: Path) -> set[int]:
    lines, stack = set(), [compile(path.read_text(encoding="utf-8"), str(path), "exec")]
    while stack:
        code = stack.pop()
        lines.update(line for _, _, line in code.co_lines() if line is not None)
        stack.extend(c for c in code.co_consts if hasattr(c, "co_lines"))
    return lines


def main(argv) -> int:
    if not hasattr(sys, "monitoring"):
        print(json.dumps({"ok": False, "error": "needs Python >= 3.12 (sys.monitoring)"}))
        return 2
    from tests import netguard
    netguard.install()
    targets = {str(p.resolve()): p for p in sorted((ROOT / "src" / "scivetra_kb" / "journal").glob("*.py"))}
    hits: dict[str, set[int]] = {}
    mon = sys.monitoring
    tool = mon.COVERAGE_ID
    mon.use_tool_id(tool, "s04j-line-coverage")

    def on_line(code, line):
        if code.co_filename in targets:
            hits.setdefault(code.co_filename, set()).add(line)
        return mon.DISABLE

    mon.register_callback(tool, mon.events.LINE, on_line)
    mon.set_events(tool, mon.events.LINE)
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=0, stream=open("nul" if sys.platform == "win32" else "/dev/null", "w")).run(suite)
    mon.set_events(tool, 0)
    mon.free_tool_id(tool)
    files, total_exec, total_hit = {}, 0, 0
    for name, path in targets.items():
        lines = executable_lines(path)
        got = hits.get(name, set()) & lines
        total_exec += len(lines)
        total_hit += len(got)
        files[path.name] = {"executable": len(lines), "executed": len(got), "percent": round(100 * len(got) / len(lines), 1) if lines else 100.0,
                            "missed_lines": sorted(lines - got)}
    report = {"schema": "rkb-s04j-line-coverage/1", "python": sys.version.split()[0], "method": "sys.monitoring LINE events (stdlib)",
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "total": {"executable": total_exec, "executed": total_hit, "percent": round(100 * total_hit / total_exec, 1)}, "files": files}
    Path(argv[0]).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "total": report["total"],
                      "per_file": {k: v["percent"] for k, v in files.items()}}))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
