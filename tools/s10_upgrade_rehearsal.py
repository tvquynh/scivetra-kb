"""S10 upgrade rehearsal with the REAL accepted S09 v02 code: schema 9 -> 10, backup first, rollback, restore, S10 gateway on top.

Phase A (child process, accepted S09 v02 source and its own fixtures): a synthetic S05 run with two parsed synthetic PDFs, a
retrieval scope, a built index and LOCAL_CLI searches (rt_access_event rows); the KB folder is copied out; table digests recorded.
Phase B (this S10 code): normal opens refuse (KB_UPGRADE_REQUIRED); 'upgrade' writes a verified backup, applies only 0010 and keeps
every prior table byte-identical; the S09 index built by v02 is still valid; LOCAL_CLI search gives the same hits as before; an S10
profile is denied by default and released after an owner A8 determination and OAG-6 approval; doctor --full is clean.
Phase C (child, S09 v02 code): the kept pre-upgrade file restores the exact schema-9 bytes and S09 v02 opens it (doctor ok).
Phase D: the upgrade backup verifies and restores into a new root (which then needs an explicit upgrade); a backup of the upgraded
KB restores with every S10 record.
Usage: python -I tools/s10_upgrade_rehearsal.py --s09-source DIR --work DIR --out FILE.json   (pinned pypdf required)
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
from tests.s09_helpers import S09Case
DOC_A = ["Objective: evaluate a static Windows PE malware detector on a public benchmark.",
         "Limitation: packed samples were excluded from the evaluation."]
DOC_B = ["Objective: compare gradient boosted trees for Android permission analysis.", "Limitation: obfuscated applications were not studied."]
t = S09Case(methodName="run")
t.run = lambda *a, **k: None
t.setUp()
try:
    run, items, scope, built = t.rworld([[DOC_A], [DOC_B]], embedder="mock")
    res = t.passages(scope, "packed samples excluded evaluation", mode="lexical")
    with t.reader() as kb:
        schema = kb.schema["schema_version"]
    shutil.copytree(t.root, target)
    print(json.dumps({"client": __version__, "schema": schema, "scope": scope, "index_manifest": built["manifest_sha256"],
                      "hits": [[h["chunk_id"], h["rights_view"], h["excerpt_sha256"]] for h in res["hits"]],
                      "items": [{"paper_id": it["candidate"]["paper_id"], "asset_sha256": it["asset_sha256"],
                                 "document_version_id": it["document_version_id"]} for it in items]}))
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
    for flag in ("--s09-source", "--work", "--out"):
        p.add_argument(flag, required=True)
    args = p.parse_args(argv)
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    root = work / "kb"
    report = {"schema": "rkb-s10-upgrade-rehearsal/1"}
    a = report["phase_a_s09_v02"] = child(PHASE_A, args.s09_source, str(root))
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    db = root / "db" / "kb.sqlite"
    before, original = tables(db), sha(db)
    from scivetra_kb.ai.gateway import GatewaySession, IntegrationGateway
    from scivetra_kb.ai.records import OwnerRecords
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import KB, open_reader, open_writer
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
    query = "packed samples excluded evaluation"
    with open_writer(str(root)) as kb:
        svc = RetrievalService(kb)
        verify = svc.verify_index()
        res = svc.search_passages(scope_id=a["scope"], query=query, consumer="LOCAL_CLI", operator="op.r", mode="lexical")
        rec = OwnerRecords(kb)
        rec.register_owner(owner="owner.one", reason="rehearsal owner", operator="op.owner")
        profile = rec.create_profile(consumer="CLAUDE_DESKTOP_LOCAL_MCP", scope_ids=[a["scope"]], label="rehearsal", operator="op.owner")["profile_id"]
    gw = IntegrationGateway(lambda: KB(str(root), readonly=False), GatewaySession(profile, "IN_PROCESS_TEST"))
    denied = gw.handle("build_rag_context", {"scope_id": a["scope"], "query": query})
    with open_writer(str(root)) as kb:
        rec = OwnerRecords(kb)
        it = a["items"][0]
        rec.determine_rights(asset_sha256=it["asset_sha256"], document_version_id=it["document_version_id"], permission="external_ai_process",
                             basis_ref="synthetic CC BY", valid_hours=2, owner="owner.one", operator="op.owner")
        rec.approve(profile_id=profile, scope_id=a["scope"], operations=["build_rag_context"], content_classes=["PASSAGE_EXCERPT"], valid_hours=2,
                    reason="rehearsal", owner="owner.one", operator="op.owner", paper_ids=[it["paper_id"]])
    released = gw.handle("build_rag_context", {"scope_id": a["scope"], "query": query})
    doc = doctor(str(root), full=True)
    report["phase_b_s10"] = {"normal_open_refused": refused, "from_to": [up["from_version"], up["to_version"]], "applied": [x["name"] for x in up["applied"]],
                             "checks": up["checks"], "prior_tables_byte_identical_recomputed": prior_identical, "new_tables": sorted(set(after) - set(before)),
                             "v02_index_valid": verify.get("valid"),
                             "local_cli_hits_equal": [[h["chunk_id"], h["rights_view"], h["excerpt_sha256"]] for h in res["hits"]] == a["hits"],
                             "gateway_default": denied["status"], "gateway_after_approval": released["status"],
                             "released_passages": len(released["results"].get("passages", [])), "doctor_problems": doc["problems"]}
    rollback = work / "rollback"
    shutil.copytree(root, rollback, ignore=shutil.ignore_patterns("index"))
    shutil.copyfile(root / "db" / up["pre_upgrade_file"], rollback / "db" / "kb.sqlite")
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / "db" / "kb.sqlite") == original,
                                  "s09_v02_doctor": child(PHASE_C, args.s09_source, str(rollback))}
    backup = work / "backups" / up["backup"]["backup_name"]
    restored = work / "restored_pre_upgrade"
    post = create_backup(str(root), str(work / "backups_post"))
    restored_post = work / "restored_post_upgrade"
    restore_backup(str(work / "backups_post" / post["backup_name"]), str(restored_post))
    ai_tables = [t for t in after if t not in before]
    report["phase_d_backup"] = {"verify_backup_valid": verify_backup(str(backup))["valid"], "restore": bool(restore_backup(str(backup), str(restored))),
                                "restored_doctor_problems": doctor(str(restored), full=False)["problems"],
                                "post_upgrade_s10_tables_equal": {t: tables(restored_post / "db" / "kb.sqlite")[t] for t in ai_tables}
                                == {t: tables(db)[t] for t in ai_tables}}
    b, c, d = report["phase_b_s10"], report["phase_c_rollback"], report["phase_d_backup"]
    report["ok"] = bool(a["schema"] == 9 and refused == "KB_UPGRADE_REQUIRED" and b["from_to"] == [9, 10] and b["applied"] == ["0010_ai_integration_gateway.sql"]
                        and b["checks"]["prior_tables_byte_identical"] and prior_identical and len(b["new_tables"]) == 7 and b["v02_index_valid"]
                        and b["local_cli_hits_equal"] and b["gateway_default"] == "DENIED" and b["gateway_after_approval"] == "OK"
                        and b["released_passages"] >= 1 and not b["doctor_problems"]
                        and c["db_sha256_equals_original"] and c["s09_v02_doctor"]["ok"] and c["s09_v02_doctor"]["schema"] == 9
                        and d["verify_backup_valid"] and d["restored_doctor_problems"] == ["KB_UPGRADE_REQUIRED"] and d["post_upgrade_s10_tables_equal"])
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
