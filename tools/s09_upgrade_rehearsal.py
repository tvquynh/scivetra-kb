"""S09 upgrade rehearsal with the REAL accepted S08 v03 code: schema 8 -> 9, backup first, rollback, restore, index rebuild.

Phase A (child process, accepted S08 v03 source and its own test fixtures): a synthetic S05 run with two parsed synthetic PDFs, an
S08 extraction scope, mock drafts, verification and one human-accepted draft; the KB folder is copied out; every table digest is
recorded.
Phase B (this S09 code): normal opens refuse (KB_UPGRADE_REQUIRED); 'upgrade' writes a verified backup, applies only 0009 and keeps
every prior table byte-identical; a retrieval scope over the S05 members builds an index; passage, paper and evidence search work
(the accepted S08 draft is found); doctor --full is clean with zero S08 and S09 invariant violations.
Phase C (child, S08 v03 code): the kept pre-upgrade file restores the exact schema-8 bytes and S08 v03 opens it (doctor ok).
Phase D: the upgrade backup verifies and restores into a new root that needs an explicit upgrade; a backup of the upgraded KB
restores without any index (INDEX_NOT_BUILT) and a rebuild reproduces the same index manifest.
Usage: python -I tools/s09_upgrade_rehearsal.py --s08-source DIR --work DIR --out FILE.json   (pinned pypdf required)
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
from tests.s08_helpers import S08Case, MALWARE_DOC
t = S08Case(methodName="runTest")
t.setUp()
try:
    run, items = t.ev_world([[MALWARE_DOC], [["Accuracy of 0.912 on the AndroZoo 2020 hold-out set.", "Limitation: obfuscated apps were not studied."]]])
    scope = t.ev_scope(run, items)
    t.ecall("build_packs", scope_id=scope, operator="op.owner")
    t.ecall("run_mock", scope_id=scope, operator="op.runner")
    t.roster("rev.one")
    d = [x for x in t.drafts(scope) if x["evidence_class"] == "NUMERICAL_RESULT"][0]["draft_id"]
    t.ecall("verify", draft_id=d, operator="op.v")
    t.ecall("reveal", draft_id=d, reviewer="rev.one")
    t.ecall("review", draft_id=d, reviewer="rev.one", decision="ACCEPT")
    with t.reader() as kb:
        schema = kb.schema["schema_version"]
    shutil.copytree(t.root, target)
    print(json.dumps({"client": __version__, "schema": schema, "run_id": run["run_id"], "accepted_draft": d,
                      "members": [{"candidate_id": it["candidate"]["candidate_id"], "parse_artifact_id": it["artifact_id"]} for it in items]}))
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
    sys.path[:0] = [str(ROOT / "src")]
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
    for flag in ("--s08-source", "--work", "--out"):
        p.add_argument(flag, required=True)
    args = p.parse_args(argv)
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    root = work / "kb"
    report = {"schema": "rkb-s09-upgrade-rehearsal/1"}
    a = report["phase_a_s08"] = child(PHASE_A, args.s08_source, str(root))
    db = root / "db" / "kb.sqlite"
    before, original = tables(db), sha(db)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import open_reader, open_writer
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import SchemaError
    from scivetra_kb.core.upgrade import upgrade_kb
    from scivetra_kb.retrieval.service import RetrievalService
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
        svc = RetrievalService(kb)
        scope = svc.create_scope(basis_kind="FTE_RUN", basis_id=a["run_id"], members=a["members"], label="rehearsal scope", operator="op.r")["scope_id"]
        built = svc.build_index(operator="op.index", embedder="mock")
        passages = svc.search_passages(scope_id=scope, query="TPR at 1% FPR EMBER", consumer="LOCAL_CLI", operator="op.r")
        evidence = svc.search_evidence(scope_id=scope, query="TPR EMBER", consumer="LOCAL_CLI", operator="op.r")
        papers = svc.search_papers(scope_id=scope, query="Synthetic", consumer="LOCAL_CLI", operator="op.r")
    doc = doctor(str(root), full=True)
    report["phase_b_s09"] = {"normal_open_refused": refused, "from_to": [up["from_version"], up["to_version"]], "applied": [x["name"] for x in up["applied"]],
                             "checks": up["checks"], "prior_tables_byte_identical_recomputed": prior_identical, "new_tables": sorted(set(after) - set(before)),
                             "index_counts": built["counts"], "index_manifest": built["manifest_sha256"],
                             "passage_top_view": passages["hits"][0]["rights_view"] if passages["hits"] else None,
                             "evidence_hits": [h["draft_id"] for h in evidence["hits"]], "paper_hits": len(papers["hits"]),
                             "doctor_problems": doc["problems"], "evidence_invariants": doc.get("evidence_invariants"),
                             "retrieval_invariants": doc.get("retrieval_invariants")}
    rollback = work / "rollback"
    shutil.copytree(root, rollback, ignore=shutil.ignore_patterns("index"))
    shutil.copyfile(root / "db" / up["pre_upgrade_file"], rollback / "db" / "kb.sqlite")
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / "db" / "kb.sqlite") == original,
                                  "s08_v03_doctor": child(PHASE_C, args.s08_source, str(rollback))}
    backup = work / "backups" / up["backup"]["backup_name"]
    restored = work / "restored_pre_upgrade"
    post = create_backup(str(root), str(work / "backups_post"))
    restored_post = work / "restored_post_upgrade"
    restore_backup(str(work / "backups_post" / post["backup_name"]), str(restored_post))
    with open_writer(str(restored_post)) as kb:
        svc = RetrievalService(kb)
        state_after_restore = svc.index_status()["state"]
        rebuilt = svc.build_index(operator="op.index", embedder="mock")
    report["phase_d_backup"] = {"verify_backup_valid": verify_backup(str(backup))["valid"], "restore": bool(restore_backup(str(backup), str(restored))),
                                "restored_doctor_problems": doctor(str(restored), full=False)["problems"],
                                "post_upgrade_backup_has_index": (restored_post / "index").exists() and state_after_restore != "INDEX_NOT_BUILT",
                                "index_state_after_restore": state_after_restore, "rebuilt_manifest_equal": rebuilt["manifest_sha256"] == built["manifest_sha256"]}
    b, c, d = report["phase_b_s09"], report["phase_c_rollback"], report["phase_d_backup"]
    report["ok"] = bool(a["schema"] == 8 and refused == "KB_UPGRADE_REQUIRED" and b["from_to"] == [8, 9] and b["applied"] == ["0009_local_retrieval.sql"]
                        and b["checks"]["prior_tables_byte_identical"] and prior_identical and b["new_tables"] == ["rt_access_event", "rt_index_build", "rt_scope"]
                        and b["passage_top_view"] == "FULL_EXCERPT" and b["evidence_hits"] == [a["accepted_draft"]] and not b["doctor_problems"]
                        and set(b["retrieval_invariants"].values()) == {0} and set(b["evidence_invariants"].values()) == {0}
                        and c["db_sha256_equals_original"] and c["s08_v03_doctor"]["ok"] and c["s08_v03_doctor"]["schema"] == 8
                        and d["verify_backup_valid"] and d["restored_doctor_problems"] == ["KB_UPGRADE_REQUIRED"]
                        and not d["post_upgrade_backup_has_index"] and d["index_state_after_restore"] == "INDEX_NOT_BUILT" and d["rebuilt_manifest_equal"])
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
