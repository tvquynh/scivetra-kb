"""S12 release readiness (local release candidate; never a public release): one report with every release gate.

Gates (each PASS / FAIL with its evidence):
  G1 VERSION_AND_NOTES     package version, change log entry, release notes, ADR_S12, user manual, operator runbook, security review
  G2 MIGRATIONS            packaged migrations 0001..0011 contiguous and byte-identical to the pinned hashes (tools/s12_migration_hashes.json)
  G3 UPGRADE_CHAIN         a KB at every earlier schema 1..10 (exact packaged migration bytes) upgrades to 11 with a verified backup
                           first, every prior table byte-identical, doctor --full clean, the kept pre-upgrade file equal to the
                           original bytes (rollback), and the upgrade backup verifies and restores
  G4 BACKUP_RESTORE        a populated synthetic S05..S11 KB (scope, claims, audit, review, export, denied share) backs up, verifies,
                           restores with every syn_* / ev_* table byte-identical and the same audit report and review state
  G5 SECURITY_SCAN         AST scan of src/: no eval / exec / pickle / marshal / os.system / os.popen / shell=True / tempfile.mktemp;
                           network modules (socket, http, urllib.request, ssl, ...) and subprocess / ctypes only where allowlisted
                           (ALLOWLIST below, each with its reason); secret / path / forbidden-file scan (tools/static_checks.py)
  G6 CLI_DOCS              every `rkb` sub-command appears in the user documentation; the generated command reference and the S10 / S11
                           scenario catalogues are current
  G7 PACKAGING             offline wheel + sdist build; the wheel holds migrations 0001..0011 and no tests, fixtures, PDFs or databases;
                           METADATA carries the private classifier and no runtime dependency; a clean venv installs it with --no-index
                           and `rkb --version`, `rkb init`, `rkb doctor` work from the INSTALLED package
  G8 DEPENDENCIES          dependency inventory: runtime none; optional pdf extra pinned to the official pypdf 6.12.1 wheel SHA-256
Overall status is READY_FOR_OWNER_RELEASE_DECISION when every gate passes, else NOT_READY. Nothing is uploaded or published.
Usage: python -I tools/s12_release_readiness.py --work DIR --out FILE.json [--dist DIR]   (pinned pypdf wheel/site for G4)
"""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

RELEASE_VERSION = "1.0.0rc1"
REQUIRED_DOCS = ("ADR_S12.md", "SCIVETRA_USER_MANUAL.md", "OPERATOR_RUNBOOK.md", "SECURITY_REVIEW_S12.md", "RELEASE_NOTES_1.0.0rc1.md",
                 "CAPABILITY_MATRIX.md", "COMMAND_REFERENCE.md", "CLI_GUIDE.md", "CHANGELOG.md", "OPEN_ISSUES.md")
FORBIDDEN_CALLS = {"eval", "exec", "os.system", "os.popen", "tempfile.mktemp", "pickle.load", "pickle.loads", "marshal.load", "marshal.loads",
                   "yaml.load", "__import__"}
FORBIDDEN_IMPORTS = {"pickle", "marshal", "shelve", "telnetlib", "ftplib", "smtplib", "xmlrpc", "requests", "httpx", "aiohttp", "paramiko"}
NETWORK_IMPORTS = {"socket", "http", "ssl", "urllib.request", "asyncio"}
# Reviewed exceptions (docs/SECURITY_REVIEW_S12.md section 3): module -> {import: reason}.
ALLOWLIST = {
    "scivetra_kb/assets/sandbox.py": {"subprocess": "runs the PDF inspection child with a minimal environment, resource limits and timeout"},
    "scivetra_kb/assets/pdf_child.py": {"ctypes": "Windows job-object limits for the inspection child (no dlopen of other libraries)"},
    "scivetra_kb/parse/sandbox.py": {"subprocess": "runs the parse child with a minimal environment, resource limits and timeout"},
    "scivetra_llm_transport/__init__.py": {"urllib": "ADR_LLM L2: OpenAI-compatible POST to a LOOPBACK model server only (host must be a loopback "
                                                "literal; no proxies, no redirects, size-capped); remote https is stage L3"},
    "scivetra_kb/parse/parse_child.py": {"subprocess": "self-test proving the audit hook refuses process creation",
                                         "ctypes": "Windows job-object limits for the parse child",
                                         "socket": "self-test proving the audit hook refuses sockets"},
}


def gate(name, ok, **evidence):
    return {"gate": name, "status": "PASS" if ok else "FAIL", **evidence}


def g1_version():
    from scivetra_kb import __version__
    docs = ROOT / "docs"
    missing = [d for d in REQUIRED_DOCS if not (docs / d).is_file()]
    changelog = (docs / "CHANGELOG.md").read_text(encoding="utf-8")
    return gate("G1_VERSION_AND_NOTES", __version__ == RELEASE_VERSION and not missing and f"## {RELEASE_VERSION}" in changelog,
                version=__version__, missing_docs=missing, changelog_entry=f"## {RELEASE_VERSION}" in changelog)


def g2_migrations():
    from scivetra_kb.core.migrations import packaged_migrations
    pinned = json.loads((ROOT / "tools" / "s12_migration_hashes.json").read_text(encoding="utf-8"))["migrations"]
    got = {name: digest for _, name, digest, _ in packaged_migrations()}
    versions = [v for v, *_ in packaged_migrations()]
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "src" / "scivetra_kb" / "core" / "migrations").glob("*.sql"))}
    return gate("G2_MIGRATIONS", got == pinned == files and versions == list(range(1, len(versions) + 1)) and len(versions) == 11,
                count=len(versions), contiguous=versions == list(range(1, len(versions) + 1)), equal_to_pinned=got == pinned, file_bytes_equal=files == pinned)


def g3_upgrade_chain(work: Path):
    from tests.helpers import FixedClock, SequentialIdProvider
    from tests.journal_helpers import make_older_kb
    from scivetra_kb.core.backup import file_sha256, restore_backup, verify_backup
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.upgrade import upgrade_kb
    rows = []
    for v in range(1, 11):
        clock = FixedClock()
        ids = SequentialIdProvider(clock)
        root = work / f"chain_{v:02d}" / "kb"
        make_older_kb(str(root), v, clock=clock, ids=ids)
        original = file_sha256(root / "db" / "kb.sqlite")
        res = upgrade_kb(str(root), str(work / f"chain_{v:02d}" / "backups"), clock=clock, ids=ids)
        doc = doctor(str(root), full=True)
        pre = root / "db" / res["pre_upgrade_file"]
        backup_dir = next((work / f"chain_{v:02d}" / "backups").iterdir())
        ver = verify_backup(str(backup_dir))
        restored = restore_backup(str(backup_dir), str(work / f"chain_{v:02d}" / "restored"))
        rows.append({"from": v, "to": res["to_version"], "checks_ok": all(x is True or x == "ok" or x == 0 or x == "CURRENT" for x in res["checks"].values()),
                     "doctor_ok": doc.get("ok") is True, "rollback_file_equals_original": file_sha256(pre) == original,
                     "backup_verifies": ver.get("valid") is True, "restored": restored.get("restored") is True})
    ok = all(r["to"] == 11 and r["checks_ok"] and r["doctor_ok"] and r["rollback_file_equals_original"] and r["backup_verifies"] and r["restored"] for r in rows)
    return gate("G3_UPGRADE_CHAIN", ok, chain=rows)


def _tables(db: Path, prefixes):
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") if r[0].startswith(prefixes)]
        return {n: hashlib.sha256(json.dumps(con.execute(f"SELECT * FROM {n} ORDER BY rowid").fetchall(), default=str).encode()).hexdigest() for n in names}
    finally:
        con.close()


def g4_backup_restore(work: Path):
    from tests import netguard
    netguard.install()
    from tests.s11_helpers import STUDY_A, STUDY_B, S11Case
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import KB
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import KBError
    from scivetra_kb.synthesis.service import SynthesisService
    t = S11Case(methodName="run")
    t.run = lambda *a, **k: None
    t.setUp()
    try:
        t.syn_world([[STUDY_A], [STUDY_B]])
        t.claim("C1", "REPORTED_VALUE", [t.draft(0, fpr_operating_point="1")], measurement=t.tpr("93.1"))
        t.claim("C2", "THEMATIC", [t.draft(0, "LIMITATION")], statement="Both studies state limitations of their evaluation scope.", evidence_class="LIMITATION")
        run = t.audit()
        t.scall("review", scope_id=t.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="ATTEST_DRAFT_ACCURATE", reason_codes=["UNVERIFIED_CLAIMS_ACKNOWLEDGED"])
        t.scall("export", scope_id=t.syn, out_dir=str(work / "g4_export"), operator="op.exporter")
        try:
            t.scall("share", scope_id=t.syn, destination_class="JOURNAL_SUBMISSION", operator="op.exporter")
        except KBError:
            pass
        status_before = t.sread("status", scope_id=t.syn)
        before = _tables(Path(t.root) / "db" / "kb.sqlite", ("syn_", "ev_", "rights_", "parse_"))
        backup = create_backup(t.root, str(work / "g4_backups"), clock=t.clock)
        backup_dir = work / "g4_backups" / backup["backup_name"]
        ver = verify_backup(str(backup_dir))
        new_root = work / "g4_restored"
        restore_backup(str(backup_dir), str(new_root))
        after = _tables(new_root / "db" / "kb.sqlite", ("syn_", "ev_", "rights_", "parse_"))
        with KB(str(new_root), readonly=False, clock=t.clock, ids=t.ids) as kb:
            status_after = SynthesisService(kb).status(scope_id=t.syn)
        doc = doctor(str(new_root), full=True)
    finally:
        t.tearDown()
    same = {k: status_before[k] == status_after[k] for k in ("report_sha256", "fresh_report_sha256", "review_state", "audit_current")}
    return gate("G4_BACKUP_RESTORE", before == after and all(same.values()) and doc.get("ok") is True and ver.get("valid") is True,
                tables_compared=len(before), tables_identical=before == after, status_identical=same, doctor_ok=doc.get("ok"),
                review_state=status_after["review_state"])


def scan_source(src: Path) -> dict:
    findings, allowed = [], []
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    top = m.split(".")[0]
                    kind = None
                    if top in FORBIDDEN_IMPORTS or m in FORBIDDEN_IMPORTS:
                        kind = "FORBIDDEN_IMPORT"
                    elif top in {"socket", "http", "ssl", "asyncio", "subprocess", "ctypes", "multiprocessing"} or m.startswith("urllib.request"):
                        kind = "RESTRICTED_IMPORT"
                    if kind is None:
                        continue
                    reason = ALLOWLIST.get(rel, {}).get(top)
                    (allowed if reason and kind == "RESTRICTED_IMPORT" else findings).append({"file": rel, "line": node.lineno, "kind": kind, "name": m,
                                                                                              **({"reason": reason} if reason else {})})
            elif isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in FORBIDDEN_CALLS:
                    findings.append({"file": rel, "line": node.lineno, "kind": "FORBIDDEN_CALL", "name": name})
                for kw in node.keywords:
                    if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                        findings.append({"file": rel, "line": node.lineno, "kind": "SHELL_TRUE", "name": name})
    return {"findings": findings, "allowlisted": allowed}


def g5_security(work: Path):
    scan = scan_source(ROOT / "src")
    out = work / "static_checks.json"
    tree = ROOT
    if any((ROOT / d).exists() for d in (".git", ".venv", "dist", "build")):   # a git clone or a working copy: scan the tracked content only
        tree = work / "static_tree" / ROOT.name
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(".git", ".venv", "dist", "build", "__pycache__", "*.egg-info", "evidence"))
    proc = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools" / "static_checks.py"), str(tree), "--out", str(out)], capture_output=True, text=True)
    static = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    return gate("G5_SECURITY_SCAN", not scan["findings"] and proc.returncode == 0, ast_findings=scan["findings"], allowlisted=scan["allowlisted"],
                static_checks_ok=proc.returncode == 0, static_secret_hits=static.get("secret_hits"), static_forbidden_files=static.get("forbidden_files"))


def cli_commands() -> list:
    from scivetra_kb import cli
    parser = cli._parser()
    out = []

    def walk(p, prefix):
        subs = [a for a in p._actions if a.__class__.__name__ == "_SubParsersAction"]
        if not subs:
            out.append(" ".join(prefix))
            return
        for name, sp in subs[0].choices.items():
            walk(sp, prefix + [name])
    walk(parser, ["rkb"])
    return sorted(out)


def g6_cli_docs():
    commands = cli_commands()
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs").glob("*.md"))) + (ROOT / "README.md").read_text(encoding="utf-8")
    undocumented = [c for c in commands if c not in text]
    current = {name: subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools" / name), "--check"], capture_output=True, text=True).returncode == 0
               for name in ("s12_command_reference.py", "s10_scenario_catalogue.py", "s11_scenario_catalogue.py")}
    return gate("G6_CLI_DOCS", bool(commands) and not undocumented and all(current.values()), commands=len(commands), undocumented=undocumented,
                generated_docs_current=current)


def run(cmd, **kw):
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=900, **kw)
    return {"cmd": [Path(str(c)).name if os.path.isabs(str(c)) else str(c) for c in cmd], "exit": proc.returncode, "tail": (proc.stdout + proc.stderr)[-800:]}


def g7_packaging(work: Path, dist: Path, use_dist: bool = False):
    """Build (or, with ``use_dist``, take the already built pure-Python wheel / sdist from ``dist``, e.g. on an interpreter without
    setuptools) and install the wheel into a clean venv."""
    dist.mkdir(parents=True, exist_ok=True)
    steps = []
    if not use_dist:
        src = work / "src_copy"
        shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns("evidence", "__pycache__", "*.egg-info", "build", "dist"))
        steps.append(run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "-w", dist, src]))
        steps.append(run([sys.executable, "-c", f"import os; os.chdir({str(src)!r}); from setuptools import build_meta as b; print(b.build_sdist({str(dist)!r}))"]))
    wheel = next(dist.glob(f"scivetra_kb_dev-{RELEASE_VERSION}-*.whl"))
    sdist = next(dist.glob(f"scivetra_kb_dev-{RELEASE_VERSION}.tar.gz"))
    names = zipfile.ZipFile(wheel).namelist()
    meta = zipfile.ZipFile(wheel).read(next(n for n in names if n.endswith(".dist-info/METADATA"))).decode()
    migrations = sorted(n.rsplit("/", 1)[1] for n in names if n.startswith("scivetra_kb/core/migrations/") and n.endswith(".sql"))
    bad = [n for n in names if n.startswith("tests/") or n.endswith((".pdf", ".sqlite", ".db", ".whl", ".pyc"))]
    with tarfile.open(sdist) as t:
        sdist_bad = [n for n in t.getnames() if n.endswith((".pdf", ".sqlite", ".db", ".whl", ".pyc", ".zip"))]
    venv = work / "venv"
    steps.append(run([sys.executable, "-m", "venv", venv]))
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    rkb = venv / ("Scripts/rkb.exe" if os.name == "nt" else "bin/rkb")
    steps.append(run([py, "-m", "pip", "install", "--no-index", "--no-deps", wheel]))
    version = run([rkb, "--version"])
    steps.append(version)
    kb = work / "venv_kb"
    steps.append(run([rkb, "init", "--data-root", kb]))
    doctor = run([rkb, "doctor", "--data-root", kb])
    steps.append(doctor)
    where = run([py, "-I", "-c", "import scivetra_kb; print(scivetra_kb.__file__)"])
    steps.append(where)
    ok = (all(s["exit"] == 0 for s in steps) and len(migrations) == 11 and not bad and not sdist_bad and "Private :: Do Not Upload" in meta
          and "Requires-Dist: pypdf==6.12.1; extra == \"pdf\"" in meta and not [l for l in meta.splitlines() if l.startswith("Requires-Dist") and "extra ==" not in l]
          and RELEASE_VERSION in version["tail"] and "site-packages" in where["tail"])
    return gate("G7_PACKAGING", ok, built_here=not use_dist, wheel={"name": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "files": len(names),
                                           "migrations": migrations, "forbidden_members": bad},
                sdist={"name": sdist.name, "sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(), "forbidden_members": sdist_bad},
                private_classifier="Private :: Do Not Upload" in meta, steps=[{k: s[k] for k in ("cmd", "exit")} for s in steps],
                installed_version=version["tail"].strip(), installed_from_site_packages="site-packages" in where["tail"])


def g8_dependencies():
    import tomllib
    from scivetra_kb.parse.approved_parsers import APPROVED, PINNED
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    inventory = {"runtime": project.get("dependencies", []), "optional": project.get("optional-dependencies", {}),
                 "pdf_parser": {"name": "pypdf", "requirement": PINNED, "license": "BSD-3-Clause",
                                "official_wheel_sha256": APPROVED[PINNED]["official_wheel_sha256"]},
                 "python": project.get("requires-python"), "stdlib_only_runtime": project.get("dependencies", []) == []}
    ok = inventory["runtime"] == [] and inventory["optional"] == {"pdf": [PINNED]} and PINNED == "pypdf==6.12.1"
    return gate("G8_DEPENDENCIES", ok, inventory=inventory)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--dist")
    p.add_argument("--use-dist", action="store_true", help="G7: install the wheel already in --dist instead of building it")
    p.add_argument("--skip", action="append", default=[], help="gate id to skip (recorded as SKIPPED; status then NOT_READY)")
    args = p.parse_args(argv)
    work = Path(args.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    dist = Path(args.dist).resolve() if args.dist else work / "dist"
    gates = []
    for gid, fn in (("G1", g1_version), ("G2", g2_migrations), ("G3", lambda: g3_upgrade_chain(work)), ("G4", lambda: g4_backup_restore(work)),
                    ("G5", lambda: g5_security(work)), ("G6", g6_cli_docs), ("G7", lambda: g7_packaging(work, dist, args.use_dist)), ("G8", g8_dependencies)):
        if gid in args.skip:
            gates.append({"gate": gid, "status": "SKIPPED"})
            continue
        try:
            gates.append(fn())
        except Exception as exc:                                     # a gate that cannot run is a failed gate
            gates.append({"gate": gid, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"[:600]})
    status = "READY_FOR_OWNER_RELEASE_DECISION" if all(g["status"] == "PASS" for g in gates) else "NOT_READY"
    report = {"schema": "scivetra-release-readiness/1", "release": RELEASE_VERSION, "status": status, "gates": gates,
              "publication": "NOT_PUBLISHED: no upload to GitHub, PyPI or any index; public release needs the owner's separate approval",
              "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version}
    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    for path in (str(work), str(ROOT), tempfile.gettempdir(), str(Path.home())):
        text = text.replace(path.replace("\\", "\\\\"), "<LOCAL>").replace(path, "<LOCAL>")
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "gates": {g["gate"]: g["status"] for g in gates}}, indent=1))
    return 0 if status == "READY_FOR_OWNER_RELEASE_DECISION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
