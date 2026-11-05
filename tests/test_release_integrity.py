"""1.0.1 release integrity: the shipped source cannot silently lose a package again (review 2026-11-05, P0-01..P0-03).

1.0.0 shipped without ``src/scivetra_kb/evidence`` (S08): ``.gitignore`` had ``evidence/`` (every level) and the release copy used
``ignore_patterns("evidence")`` (every level). These tests pin the fixes; G7 in tools/s12_release_readiness.py additionally installs
the built wheel and a wheel rebuilt from the sdist into clean venvs. Synthetic data only; no network."""
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.helpers import ROOT

spec = importlib.util.spec_from_file_location("s12_release_readiness", ROOT / "tools" / "s12_release_readiness.py")
RR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RR)

S08_FILES = ["scivetra_kb/evidence/__init__.py", "scivetra_kb/evidence/excerpt.py", "scivetra_kb/evidence/mock.py",
             "scivetra_kb/evidence/profiles.py", "scivetra_kb/evidence/schema.py", "scivetra_kb/evidence/service.py"]


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


class ReleaseIntegrity(unittest.TestCase):
    def test_gitignore_excludes_root_output_only(self):
        """IDs: REL-GITIGNORE-ROOT-ONLY. Generated output (evidence, build, dist) is ignored at the repository root only: no
        unanchored 'evidence/' rule; with git available, root evidence/ is ignored and src/scivetra_kb/evidence/ is not."""
        rules = [l.strip() for l in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
        for name in ("evidence", "build", "dist"):
            self.assertNotIn(name + "/", rules)
            self.assertIn("/" + name + "/", rules)
        if (ROOT / ".git").exists() and shutil.which("git"):
            self.assertEqual(_git("check-ignore", "-q", "evidence/test_results.json").returncode, 0)
            self.assertEqual(_git("check-ignore", "-q", "src/scivetra_kb/evidence/service.py").returncode, 1)

    def test_checkout_keeps_exact_bytes(self):
        """IDs: REL-EXACT-BYTES. Files pinned by SHA-256 (migrations, fixtures, baselines) must reach every checkout byte for byte:
        .gitattributes turns line-ending conversion off for everything except the .cmd / .ps1 / .sh launchers; with git
        available, every other tracked file carries the -text attribute (a Windows clone with core.autocrlf=true broke 62
        tests before this rule)."""
        rules = [l.split() for l in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
        self.assertEqual(rules[0], ["*", "-text"])
        if (ROOT / ".git").exists() and shutil.which("git"):
            rows = [l.split("\t", 1) for l in _git("ls-files", "--eol").stdout.splitlines()]
            converted = [path for info, path in rows if "attr/-text" not in info and not path.endswith((".cmd", ".ps1", ".sh"))]
            self.assertEqual(converted, [])

    def test_every_package_file_is_tracked(self):
        """IDs: REL-SOURCE-TRACKED. In a git checkout every file of the shipped packages (modules and package data) is tracked, so a
        clone or the GitHub source ZIP holds the same code as the developer tree (S08 files included)."""
        files = RR.source_package_files()
        for f in S08_FILES:
            self.assertIn(f, files)
        if (ROOT / ".git").exists() and shutil.which("git"):
            tracked = set(_git("ls-files", "src").stdout.split("\n"))
            self.assertEqual([f for f in files if "src/" + f not in tracked], [])

    def test_internal_imports_resolve_and_a_missing_package_is_detected(self):
        """IDs: REL-IMPORT-CLOSURE, REL-IMPORT-CLOSURE-NEGATIVE. Every internal import (absolute, relative and lazy imports inside
        functions) resolves to a module of the source tree; with the S08 package removed from a scratch copy, the imports of the
        CLI, S09, S11 and LLM extraction are reported."""
        self.assertEqual(RR.unresolved_internal_imports(), [])
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            shutil.copytree(ROOT / "src", src, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.rmtree(src / "scivetra_kb" / "evidence")
            found = {x["file"] for x in RR.unresolved_internal_imports(src)}
        for f in ("scivetra_kb/cli.py", "scivetra_kb/retrieval/service.py", "scivetra_kb/synthesis/service.py",
                  "scivetra_kb/llm/evidence_extract.py"):
            self.assertIn(f, found)

    def test_release_copy_keeps_nested_evidence_package(self):
        """IDs: REL-COPY-TREE. The release / scan copy drops root-level generated output (evidence/) but keeps the source package
        src/scivetra_kb/evidence/ that has the same basename."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            (root / "evidence").mkdir(parents=True)
            (root / "evidence" / "test_results.json").write_text("{}", encoding="utf-8")
            pkg = root / "src" / "scivetra_kb" / "evidence"
            pkg.mkdir(parents=True)
            (pkg / "service.py").write_text("X = 1\n", encoding="utf-8")
            (pkg / "__pycache__").mkdir()
            out = Path(d) / "copy"
            RR.copy_tree(out, root)
            self.assertFalse((out / "evidence").exists())
            self.assertTrue((out / "src" / "scivetra_kb" / "evidence" / "service.py").is_file())
            self.assertFalse((out / "src" / "scivetra_kb" / "evidence" / "__pycache__").exists())

    def test_archive_without_s08_fails_the_member_check(self):
        """IDs: REL-ARCHIVE-MEMBERS, REL-ARCHIVE-MEMBERS-NEGATIVE. A wheel holding every package file passes the G7 member check; a
        simulated wheel / sdist without the S08 package lists exactly the six S08 files as missing (G7 then FAILs)."""
        files = RR.source_package_files()
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d) / "good.whl", Path(d) / "bad.whl"
            for path, members in ((good, files), (bad, [f for f in files if not f.startswith("scivetra_kb/evidence/")])):
                with zipfile.ZipFile(path, "w") as z:
                    for m in members:
                        z.writestr(m, "")
            self.assertEqual(RR.missing_members(files, zipfile.ZipFile(good).namelist()), [])
            self.assertEqual(RR.missing_members(files, zipfile.ZipFile(bad).namelist()), S08_FILES)
        prefix = "scivetra_kb_dev-1.0.1/src/"
        sdist = [prefix + f for f in files if "/evidence/" not in f]
        self.assertEqual(RR.missing_members(files, sdist, prefix), S08_FILES)


if __name__ == "__main__":
    unittest.main()
