"""S11 upgrade rehearsal with the REAL accepted S10 v02 code: schema 10 -> 11, backup first, rollback, restore, S11 synthesis on top.

Phase A (child process, accepted S10 v02 source and its own fixtures): a synthetic S05 run with two parsed synthetic PDFs, an S08
evidence scope, deterministic mock extraction, structural verification and human acceptance; the KB folder is copied out.
Phase B (this S11 code): normal opens refuse (KB_UPGRADE_REQUIRED); 'upgrade' writes a verified backup, applies only 0011 and keeps
every prior table byte-identical; an S11 scope, a claim, an audit (SUPPORTED, traceability 100%) and a LOCAL_REVIEW_ONLY export work
on the upgraded KB; doctor --full is clean.
Phase C (child, S10 v02 code): the kept pre-upgrade file restores the exact schema-10 bytes and S10 v02 opens it (doctor ok).
Phase D: the upgrade backup verifies and restores (then needs an explicit upgrade); a backup of the upgraded KB restores with every
S11 record byte-identical.
Usage: python -I tools/s11_upgrade_rehearsal.py --s10-source DIR --work DIR --out FILE.json   (pinned pypdf required)
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PHASE_A = r'''
import json, os, shutil, sys
src = sys.argv[1]; target = sys.argv[2]
sys.path[:0] = [os.path.join(src, "src"), src]
from tests import netguard
netguard.install()
from scivetra_kb import __version__
from tests.s08_helpers import S08Case
DOC_A = ["TPR of 93.1% at 1% FPR on the EMBER 2018 test split of Win32 files.", "Limitation: packed samples were excluded from the evaluation."]
DOC_B = ["TPR of 91.4% at 1% FPR on the EMBER 2018 test split of Win32 files.", "Limitation: obfuscated files were not studied."]
t = S08Case(methodName="run")
t.run = lambda *a, **k: None
t.setUp()
try:
    run, items, scope, _ = t.mocked([[DOC_A], [DOC_B]])
    t.roster("rev.one")
    drafts = t.drafts(scope)
    for d in drafts:
        t.ecall("verify", draft_id=d["draft_id"], operator="op.v")
        t.ecall("reveal", draft_id=d["draft_id"], reviewer="rev.one")
        t.ecall("review", draft_id=d["draft_id"], reviewer="rev.one", decision="ACCEPT")
    with t.reader() as kb:
        schema = kb.schema["schema_version"]
    shutil.copytree(t.root, target)
    tpr = [d["draft_id"] for d in drafts if d["evidence_class"] == "NUMERICAL_RESULT" and '"93.1"' in d["typed_fields_json"]]
    print(json.dumps({"client": __version__, "schema": schema, "ev_scope": scope, "tpr_draft": tpr[0], "drafts": len(drafts)}))
finally:
    t.tearDown()
'''

PHASE_C = r'''
import json, os, sys
src = sys.argv[1]; root = sys.argv[2]
sys.path[:0] = [os.path.join(src, "src"), src]
from scivetra_kb import __version__
from scivetra_kb.core.doctor import doctor
r = doctor(root, full=True)
print(json.dumps({"client": __version__, "ok": r["ok"], "problems": r["problems"], "schema": r["schema_status"]["schema_version"]}))
'''


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tables(db) -> dict:
    from scivetra_kb.core.manifest import table_content_sha256
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: table_content_sha256(con, n) for n in sorted(names)}
    finally:
        con.close()


def child(script, *args):
    proc = subprocess.run([sys.executable, "-I", "-c", script, *args], capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise SystemExit("child failed: " + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv=None):
    p = argparse.ArgumentParser()
    for flag in ("--s10-source", "--work", "--out"):
        p.add_argument(flag, required=True)
    args = p.parse_args(argv)
    work = Path(args.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    root = work / "kb"
    report = {"schema": "rkb-s11-upgrade-rehearsal/1"}
    a = report["phase_a_s10_v02"] = child(PHASE_A, args.s10_source, str(root))
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    db = root / "db" / "kb.sqlite"
    before, original = tables(db), sha(db)
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import open_reader, open_writer
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import SchemaError
    from scivetra_kb.core.upgrade import upgrade_kb
    from scivetra_kb.synthesis import rules as R
    from scivetra_kb.synthesis.service import SynthesisService
    try:
        open_reader(str(root)).__enter__()
        refused = None
    except SchemaError as exc:
        refused = exc.code
    up = upgrade_kb(str(root), str(work / "backups"))
    after = tables(db)
    audit = ("kb_upgrade_event", "schema_migration")
    prior_identical = {k: v for k, v in after.items() if k in before and k not in audit} == {k: v for k, v in before.items() if k not in audit}
    with open_writer(str(root)) as kb:
        svc = SynthesisService(kb)
        scope = svc.create_scope(ev_scope_id=a["ev_scope"], operator="op.synth", label="rehearsal", review_policy="SINGLE_HUMAN",
                                 review_policy_reason="rehearsal: one reviewer")["scope_id"]
        svc.add_claim(scope_id=scope, claim_key="C1", operator="op.author", cites=[a["tpr_draft"]],
                      spec={"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": {
                          "metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "dataset": "EMBER",
                          "dataset_year": "2018", "split": "TEST", "pe_type": "WIN32"}})
        run = svc.audit(scope_id=scope, operator="op.auditor")
        exported = svc.export(scope_id=scope, out_dir=str(work / "exports"), operator="op.exporter")
    doc = doctor(str(root), full=True)
    report["phase_b_s11"] = {"normal_open_refused": refused, "from_to": [up["from_version"], up["to_version"]], "applied": [x["name"] for x in up["applied"]],
                             "checks": up["checks"], "prior_tables_byte_identical_recomputed": prior_identical, "new_tables": sorted(set(after) - set(before)),
                             "claim_verdict": run["report"]["claims"][0]["verdict"], "traceability": run["report"]["citation_audit"]["traceability"],
                             "export_destination": exported["destination"], "export_files": sorted(exported["files"]), "doctor_problems": doc["problems"]}
    rollback = work / "rollback"
    shutil.copytree(root, rollback)
    shutil.copyfile(root / "db" / up["pre_upgrade_file"], rollback / "db" / "kb.sqlite")
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / "db" / "kb.sqlite") == original,
                                  "s10_v02_doctor": child(PHASE_C, args.s10_source, str(rollback))}
    backup = work / "backups" / up["backup"]["backup_name"]
    restored = work / "restored_pre_upgrade"
    post = create_backup(str(root), str(work / "backups_post"))
    restored_post = work / "restored_post_upgrade"
    restore_backup(str(work / "backups_post" / post["backup_name"]), str(restored_post))
    syn_tables = [t for t in tables(db) if t.startswith("syn_")]
    report["phase_d_backup"] = {"verify_backup_valid": verify_backup(str(backup))["valid"], "restore": bool(restore_backup(str(backup), str(restored))),
                                "restored_doctor_problems": doctor(str(restored), full=False)["problems"],
                                "post_upgrade_s11_tables_equal": {t: tables(restored_post / "db" / "kb.sqlite")[t] for t in syn_tables}
                                == {t: tables(db)[t] for t in syn_tables}}
    b, c, d = report["phase_b_s11"], report["phase_c_rollback"], report["phase_d_backup"]
    report["ok"] = bool(a["schema"] == 10 and refused == "KB_UPGRADE_REQUIRED" and b["from_to"] == [10, 11] and b["applied"] == ["0011_scientific_synthesis.sql"]
                        and b["checks"]["prior_tables_byte_identical"] and prior_identical and len(b["new_tables"]) == 9
                        and b["claim_verdict"] == "SUPPORTED" and b["traceability"] == "100%" and b["export_destination"] == "LOCAL_REVIEW_ONLY"
                        and not b["doctor_problems"] and c["db_sha256_equals_original"] and c["s10_v02_doctor"]["ok"] and c["s10_v02_doctor"]["schema"] == 10
                        and d["verify_backup_valid"] and d["restored_doctor_problems"] == ["KB_UPGRADE_REQUIRED"] and d["post_upgrade_s11_tables_equal"])
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
