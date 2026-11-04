"""Run the full offline suite with the outbound network guard; fail on any failure, error or skip.

Writes machine evidence to ``evidence/``:
  test_results.json   one record per test: id, outcome, seconds, contract IDs (from docstrings)
  test_run.log        verbose unittest output
Usage:  python -I tools/run_offline_tests.py
"""
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
# The ID list ends at a period followed by whitespace/end, so dotted IDs such as MF-18.01 survive.
IDS_RE = re.compile(r"IDs:\s*(.+?)\.(?:\s|$)")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self._start = {}

    def startTest(self, test):
        self._start[test.id()] = time.perf_counter()
        super().startTest(test)

    def _record(self, test, outcome, detail=None):
        doc = (test._testMethodDoc or "") if hasattr(test, "_testMethodDoc") else ""
        match = IDS_RE.search(doc)
        ids = [i.strip() for i in match.group(1).split(",")] if match else []
        self.records.append({"test": test.id(), "outcome": outcome, "seconds": round(time.perf_counter() - self._start.get(test.id(), time.perf_counter()), 4),
                             "contract_ids": ids, "description": doc.strip().split("\n")[0], "detail": detail})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "FAIL", self._exc_info_to_string(err, test)[-2000:])

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "ERROR", self._exc_info_to_string(err, test)[-2000:])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "SKIP", reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            kind = "FAIL" if issubclass(err[0], test.failureException) else "ERROR"
            self.records.append({"test": subtest.id(), "outcome": kind, "seconds": 0, "contract_ids": [], "description": "subtest",
                                 "detail": self._exc_info_to_string(err, test)[-2000:]})


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def main() -> int:
    for name in list(os.environ):
        if name.upper() in ("RKB_CONTACT_EMAIL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PYTHONPATH",
                            "OPENALEX_API_KEY", "UNPAYWALL_EMAIL", "GITHUB_TOKEN", "GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ.pop(name, None)
    EVIDENCE.mkdir(exist_ok=True)
    os.environ["RKB_EVIDENCE_DIR"] = str(EVIDENCE)
    with tempfile.TemporaryDirectory(prefix="rkb-offline-") as home:
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
            os.environ[name] = home
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["PYTHONUTF8"] = "1"
        # Child Python processes inherit the guard through sitecustomize.
        shutil.copyfile(ROOT / "tests" / "netguard.py", Path(home) / "rkb_netguard.py")
        (Path(home) / "sitecustomize.py").write_text("import rkb_netguard\nrkb_netguard.install()\n", encoding="utf-8")
        os.environ["PYTHONPATH"] = os.pathsep.join((home, str(ROOT), str(ROOT / "src")))
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "src"))
        sys.dont_write_bytecode = True
        from tests import netguard
        netguard.install()
        # Diagnostic only (not a gate): does the sitecustomize defence-in-depth layer reach a plain child here?
        # The enforced child guard is the explicit bootstrap in tests/netguard.child_command (NET-GUARD-05/CHILD).
        import subprocess
        probe = subprocess.run([sys.executable, "-c", "import sys; print('rkb_netguard' in sys.modules)"],
                               capture_output=True, text=True, timeout=60)
        sitecustomize_child = probe.stdout.strip() or ("ERROR:" + probe.stderr.strip()[-120:])
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
        with open(EVIDENCE / "test_run.log", "w", encoding="utf-8", newline="\n") as log:
            stream = Tee(sys.stdout, log)
            header = (f"rkb offline suite | python {platform.python_version()} | sqlite {sqlite3.sqlite_version} | "
                      f"{platform.system()} {platform.release()} | network guard installed: {netguard.installed()} | "
                      f"sitecustomize reaches plain child (diagnostic): {sitecustomize_child}\n")
            stream.write(header)
            runner = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordingResult)
            started = time.time()
            result = runner.run(suite)
            elapsed = time.time() - started
        summary = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
                   "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
                   "elapsed_seconds": round(elapsed, 2), "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                   "platform": f"{platform.system()} {platform.release()} {platform.machine()}", "network_guard": netguard.installed(),
                   "sitecustomize_child_guard_diagnostic": sitecustomize_child}
        ok = result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
        summary["ok"] = ok
        (EVIDENCE / "test_results.json").write_text(json.dumps({"summary": summary, "tests": result.records}, indent=1, ensure_ascii=False) + "\n",
                                                    encoding="utf-8", newline="\n")
        print(json.dumps(summary))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
