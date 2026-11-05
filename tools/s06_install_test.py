"""S06 offline installation test: sdist + wheel build, wheel-from-sdist rebuild, clean venv install with the pinned parser.

No network: ``pip --no-index`` everywhere, ``--no-build-isolation`` (uses the interpreter's own setuptools). The pinned parser
is the OFFICIAL ``pypdf-6.12.1-py3-none-any.whl`` (S06 v02): pip installs it with ``--require-hashes`` against the SHA-256 in
``approved_parsers`` (the architect-recorded PyPI value), and ``rkb parse runtime-check`` in the venv must report the installed
copy as ``CONTENT_MATCHES_OFFICIAL_WHEEL``. In the venv: ``rkb init`` and S06 tests run against the INSTALLED package.
Usage: python tools/s06_install_test.py --wheel pypdf-6.12.1-py3-none-any.whl --work DIR --out FILE.json
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]


def run(cmd, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, **kw)
    return {"cmd": [Path(c).name if os.path.isabs(c) else c for c in cmd], "exit": proc.returncode, "tail": (proc.stdout + proc.stderr)[-1500:]}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--wheel", required=True, help="the official pypdf-6.12.1-py3-none-any.whl")
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    from scivetra_kb.parse import runtime
    work = Path(args.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    (work / "dist").mkdir(parents=True)
    src = work / "src_copy"
    generated = shutil.ignore_patterns("__pycache__", "*.egg-info")
    # root-level output only: the source package src/scivetra_kb/evidence has the same basename (1.0.1, P0-03)
    shutil.copytree(ROOT, src, ignore=lambda d, names: set(generated(d, names)) | (
        {"evidence", "build", "dist", ".git", ".venv"} & set(names) if Path(d).resolve() == ROOT else set()))
    report = {"schema": "rkb-s06-install-test/1", "python": sys.version.split()[0], "steps": []}
    report["steps"].append(run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "-w", str(work / "dist"), str(src)]))
    sdist_code = f"import os; os.chdir({str(src)!r}); from setuptools import build_meta as b; print(b.build_sdist({str(work / 'dist')!r}))"
    report["steps"].append(run([sys.executable, "-c", sdist_code]))
    wheel = next((work / "dist").glob("scivetra_kb_dev-*.whl"))
    sdist = next((work / "dist").glob("scivetra_kb_dev-*.tar.gz"))
    (work / "from_sdist").mkdir()
    report["steps"].append(run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "-w", str(work / "from_sdist"), str(sdist)]))
    wheel2 = next((work / "from_sdist").glob("scivetra_kb_dev-*.whl"))
    names = sorted(zipfile.ZipFile(wheel).namelist())
    names2 = sorted(zipfile.ZipFile(wheel2).namelist())
    with tarfile.open(sdist) as t:
        sdist_names = t.getnames()
    report["wheel"] = {"name": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "files": len(names),
                       "has_0006": "scivetra_kb/core/migrations/0006_pdf_parse_artifacts.sql" in names,
                       "has_parse_child": "scivetra_kb/parse/parse_child.py" in names, "same_files_as_wheel_from_sdist": names == names2,
                       "requires_dist": [l for l in zipfile.ZipFile(wheel).read(next(n for n in names if n.endswith("METADATA"))).decode().splitlines()
                                         if l.startswith(("Requires-Dist", "Provides-Extra"))]}
    report["sdist"] = {"name": sdist.name, "sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(), "members": len(sdist_names),
                       "has_tests": any(n.endswith("tests/test_s06_formats.py") for n in sdist_names)}
    from scivetra_kb.parse.approved_parsers import APPROVED, PINNED
    official = runtime.verify_site(args.wheel)          # PARSER_WHEEL_NOT_OFFICIAL unless the recorded PyPI SHA-256 matches
    parser_wheel = work / "dist" / Path(args.wheel).name
    shutil.copyfile(args.wheel, parser_wheel)
    report["parser_wheel"] = {"name": parser_wheel.name, "sha256": hashlib.sha256(parser_wheel.read_bytes()).hexdigest(),
                              "expected_official_sha256": APPROVED[PINNED]["official_wheel_sha256"], "provenance": official["provenance"],
                              "origin": "official PyPI release file (downloaded once with owner approval; SHA-256 equals the architect-recorded value)"}
    (work / "requirements-pdf.txt").write_text(f"pypdf==6.12.1 --hash=sha256:{APPROVED[PINNED]['official_wheel_sha256']}" + chr(10), encoding="utf-8")
    venv = work / "venv"
    report["steps"].append(run([sys.executable, "-m", "venv", str(venv)]))
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    rkb = venv / ("Scripts/rkb.exe" if os.name == "nt" else "bin/rkb")
    report["steps"].append(run([str(py), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)]))
    report["steps"].append(run([str(py), "-m", "pip", "install", "--no-index", "--find-links", str(work / "dist"), "--require-hashes", "-r",
                                str(work / "requirements-pdf.txt")]))
    env = {k: v for k, v in os.environ.items() if k != runtime.ENV_SITE}
    env.pop("SCIVETRA_PYPDF_WHEEL", None)
    rt = run([str(rkb), "parse", "runtime-check"], env=env)
    report["steps"].append(rt)
    kb = work / "kb"
    report["steps"].append(run([str(rkb), "init", "--data-root", str(kb)], env=env))
    tests_dir = work / "tests_only"
    shutil.copytree(ROOT / "tests", tests_dir / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    where = run([str(py), "-I", "-c", "import scivetra_kb, pypdf; print(scivetra_kb.__file__); print(pypdf.__version__)"], env=env)
    report["steps"].append(where)
    site_out = subprocess.run([str(py), "-I", "-c", "import importlib.util, pathlib; "
                               "print(pathlib.Path(importlib.util.find_spec('pypdf').origin).resolve().parent.parent)"], capture_output=True, text=True)
    # the tests use the pypdf INSTALLED in the venv (as the parser site) and the official wheel for the wheel-mode checks
    test_env = dict(env, SCIVETRA_PYPDF_SITE=site_out.stdout.strip(), SCIVETRA_PYPDF_WHEEL=str(parser_wheel))
    tests = run([str(py), "-B", "-m", "unittest", "tests.test_s06_artifacts.Cli", "tests.test_s06_formats.ModernSyntax", "tests.test_s06_gates",
                 "tests.test_s06_v02.ParserProvenance"], env=test_env, cwd=str(tests_dir))
    report["steps"].append(tests)
    installed_from_venv = str(venv) in where["tail"] or "site-packages" in where["tail"]
    report["ok"] = bool(all(s["exit"] == 0 for s in report["steps"]) and report["wheel"]["has_0006"] and report["wheel"]["has_parse_child"]
                        and report["wheel"]["same_files_as_wheel_from_sdist"] and '"version":"6.12.1"' in rt["tail"].replace(" ", "")
                        and '"provenance":"CONTENT_MATCHES_OFFICIAL_WHEEL"' in rt["tail"].replace(" ", "")
                        and installed_from_venv and "OK" in tests["tail"])
    text = json.dumps(report, indent=1, sort_keys=True).replace(str(work).replace("\\", "\\\\"), "<WORK>")
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "exits": [s["exit"] for s in report["steps"]]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
