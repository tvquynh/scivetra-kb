"""Upgrade rehearsal S04J v02 (schema 4) -> S05 (schema 5) using the ACCEPTED S04J v02 code to build and re-open the KB.

Usage: python -I -B tools/s05_upgrade_rehearsal.py --s04j-source <S04J v02 source/> --work <empty dir> --out <json>

Phase A (S04J v02 code, separate interpreter): init a schema-4 KB, library-import a synthetic screened Stage-11 TA
release, export one PBD bridge batch from the native Reprovane fixture, run a full simulated journal pipeline; record
the S02/library/bridge/journal manifests and the DB hash.
Phase B (this S05 code): reads refuse (KB_UPGRADE_REQUIRED); 'upgrade' backs up first and applies only 0005 to a copy;
every prior table byte-identical except the two upgrade audit tables, which gain exactly one row each; all four manifests identical; an
S05 run is created on the imported release, reviewed, resolved, locked and exported; the journal manifest is unchanged
by S05; doctor --full is clean; backup/verify/restore keep identical domain summaries incl. the eligibility manifest and
the CAS-stored handoff manifest; a missing CAS object makes backup verification fail.
Phase C (S04J v02 code): refuses the upgraded KB (KB_CLIENT_TOO_OLD); the pre-upgrade file reopens with identical manifests.
"""
import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

S04J_SCRIPT = r'''
import json, sys
from pathlib import Path
src, work, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(src), str(src / "src")]
from scivetra_kb import __version__
from scivetra_kb.core.db import init_kb, open_reader, open_writer
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import bridge_manifest, journal_manifest, library_manifest, semantic_manifest
kb_root = work / ("kb" if phase != "rollback" else "rollback")
if phase == "build":
    from tests import netguard
    netguard.install()
    from scivetra_kb.bridge.service import BridgeService
    from scivetra_kb.library.service import LibraryService
    from tests.journal_helpers import SimulatedProviders, pipeline, small_scenario
    init_kb(str(kb_root))
    with open_writer(str(kb_root)) as kb:
        LibraryService(kb).import_release(str(work / "ta_release"), workspace_label="ws", operator="s04j.op")
        LibraryService(kb).import_release(str(work / "corpus"), workspace_label="ws_native", operator="s04j.op")
        BridgeService(kb).export_batch(source_dir=str(work / "fulltext"), kind="rv-manual-queue", workspace_label="ws_native",
                                       out_dir=str(work / "batch"), operator="s04j.op")
        pipeline(kb, SimulatedProviders(small_scenario(40)), sizes=(10, 10, 10))
try:
    with open_reader(str(kb_root)) as kb:
        print(json.dumps({"opened": True, "client_version": __version__, "schema_version": kb.schema["schema_version"],
                          "s02_manifest": semantic_manifest(kb)["manifest_sha256"], "library_manifest": library_manifest(kb)["manifest_sha256"],
                          "bridge_manifest": bridge_manifest(kb)["manifest_sha256"], "journal_manifest": journal_manifest(kb)["manifest_sha256"]}))
except KBError as exc:
    print(json.dumps({"opened": False, "client_version": __version__, "error": exc.code}))
'''
MANIFESTS = ("s02_manifest", "library_manifest", "bridge_manifest", "journal_manifest")


def s04j(src, work, phase):
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", S04J_SCRIPT, str(src), str(work), phase], capture_output=True, text=True, timeout=600)
    if proc.returncode:
        raise SystemExit("S04J v02 phase failed: " + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--s04j-source", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    work = args.work.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("--work must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    from tests import netguard
    netguard.install()
    from tests.bridge_helpers import NATIVE_FT
    from tests.fte_helpers import PROTOCOL, ROSTER, write_ta_release
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import KBError
    from scivetra_kb.core.manifest import (bridge_manifest, eligibility_manifest, journal_manifest, library_manifest, semantic_manifest,
                                           table_content_sha256)
    from scivetra_kb.core.upgrade import upgrade_kb
    from scivetra_kb.eligibility.handoff import export_run, verify_export, verify_lock
    from scivetra_kb.eligibility.service import FTEService
    shutil.copytree(NATIVE_FT / "corpus", work / "corpus")
    shutil.copytree(NATIVE_FT / "fulltext", work / "fulltext")
    items, sem, verify_json = write_ta_release(str(work / "ta_release"), 4, release_id="crl_s05_rehearsal")
    report = {"schema": "rkb-s05-upgrade-rehearsal/1", "data": "SYNTHETIC ONLY",
              "s04j_source_migration_sha256": {p.name: sha(p) for p in sorted((args.s04j_source / "src/scivetra_kb/core/migrations").glob("*.sql"))},
              "s05_migration_sha256": {p.name: sha(p) for p in sorted((ROOT / "src/scivetra_kb/core/migrations").glob("*.sql"))}}
    report["migrations_0001_0004_identical"] = all(report["s05_migration_sha256"].get(k) == v for k, v in report["s04j_source_migration_sha256"].items())
    report["phase_a_s04j_v02_code"] = a = s04j(args.s04j_source, work, "build")
    db = work / "kb" / DB_RELATIVE
    report["original_db_sha256"] = original = sha(db)
    import sqlite3
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    prior_tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    before = {t: table_content_sha256(con, t) for t in prior_tables}
    before_audit = {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in ("schema_migration", "kb_upgrade_event")}
    con.close()
    try:
        with open_reader(str(work / "kb")):
            pass
        report["phase_b_readonly_refusal"] = "OPENED"
    except KBError as exc:
        report["phase_b_readonly_refusal"] = exc.code
    up = upgrade_kb(str(work / "kb"), str(work / "operator-backups"))
    report["phase_b_upgrade"] = {k: up[k] for k in ("from_version", "to_version", "applied", "checks", "backup", "pre_upgrade_file", "original_db_sha256")}
    with open_reader(str(work / "kb")) as kb:
        after = {"schema_version": kb.schema["schema_version"], "s02_manifest": semantic_manifest(kb)["manifest_sha256"],
                 "library_manifest": library_manifest(kb)["manifest_sha256"], "bridge_manifest": bridge_manifest(kb)["manifest_sha256"],
                 "journal_manifest": journal_manifest(kb)["manifest_sha256"]}
        changed = sorted(t for t in prior_tables if table_content_sha256(kb.connection, t) != before[t])
        # The two upgrade audit tables gain exactly one row each (schema_migration 5, kb_upgrade_event 4->5); old rows stay.
        audit = {t: [tuple(r) for r in kb.connection.execute(f"SELECT * FROM {t} ORDER BY rowid")] for t in before_audit}
        appended_only = (all(audit[t][:len(before_audit[t])] == before_audit[t] and len(audit[t]) == len(before_audit[t]) + 1 for t in audit)
                         and kb.one("SELECT count(*) n FROM kb_upgrade_event WHERE from_version=4 AND to_version=5")["n"] == 1
                         and kb.one("SELECT max(version) v FROM schema_migration")["v"] == 5)
    report["phase_b_after"] = after
    report["phase_b_prior_tables_changed"] = changed
    report["phase_b_audit_tables_appended_only"] = appended_only
    report["phase_b_manifests_identical_to_phase_a"] = all(after[k] == a[k] for k in MANIFESTS)
    with open_writer(str(work / "kb")) as kb:
        svc = FTEService(kb)
        source = kb.one("SELECT release_import_id FROM reprovane_release_import WHERE release_id='crl_s05_rehearsal'")["release_import_id"]
        pid = svc.create_protocol(copy.deepcopy(PROTOCOL), operator="s05.op")["protocol_id"]
        run = svc.create_run(source_id=source, export_dir=str(work / "ta_release"), expected_manifest_sha256=sem, rv_verify_json=verify_json,
                             protocol_id=pid, roster=copy.deepcopy(ROSTER), label="upgrade rehearsal", operator="s05.op")
        for k, c in enumerate(svc.candidates(run["run_id"])):
            for n, status in enumerate(("LOGIN_REQUIRED", "BOT_CHECK")):
                svc.record_attempt(candidate_id=c["candidate_id"], status=status, source_description="publisher site (synthetic)",
                                   attempted_on=f"2026-10-0{n + 1}", operator="s05.acq")
            svc.attach_none(candidate_id=c["candidate_id"], operator="s05.op")
            for who in ("rev.a", "rev.b"):
                svc.decide(candidate_id=c["candidate_id"], reviewer=who, outcome="FT_NOT_RETRIEVED", reasons=["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"])
        svc.resolve(run["run_id"], operator="s05.op")
        lock = svc.lock(run["run_id"], operator="s05.op")
        exported = export_run(kb, run["run_id"], str(work / "fte_export"), operator="s05.op")
        report["phase_b_fte"] = {"run_state": svc.state(run["run_id"]), "summary": {k: svc.summary(run["run_id"])[k] for k in
                                 ("authority", "ta_candidate_count", "ft_include", "ft_exclude", "ft_not_retrieved", "ft_pending")},
                                 "handoff_manifest_sha256": lock["handoff_manifest_sha256"],
                                 "verify_lock": verify_lock(kb, run["run_id"])["valid"]}
        report["phase_b_journal_manifest_unchanged_by_s05"] = journal_manifest(kb)["manifest_sha256"] == a["journal_manifest"]
        report["phase_b_eligibility_manifest"] = eligibility_manifest(kb)["manifest_sha256"]
    report["phase_b_fte"]["verify_export"] = verify_export(str(work / "fte_export" / exported["folder_name"]))["valid"]
    full = doctor(str(work / "kb"), full=True)
    report["phase_b_doctor_full_problems"] = full["problems"]
    backup = create_backup(str(work / "kb"), str(work / "operator-backups"))
    verified = verify_backup(backup["backup_dir"])
    restored = restore_backup(backup["backup_dir"], str(work / "restored"))
    report["phase_b_backup_restore"] = {"verify_valid": verified["valid"], "schema": verified["kb_schema_version"],
                                        "cas_referenced": verified["cas_referenced_count"], "restore_comparison": restored["comparison"],
                                        "eligibility_manifest_in_backup": verified["domain"].get("eligibility_manifest_sha256")}
    broken = work / "broken-backup"
    shutil.copytree(backup["backup_dir"], broken)
    handoff = lock["handoff_manifest_sha256"]
    victims = [p for p in broken.rglob("*") if p.is_file() and handoff in p.as_posix()]
    for p in victims:
        p.unlink()
    try:
        broken_result = verify_backup(str(broken))
        report["phase_b_missing_cas_object"] = {"removed": len(victims), "verify_valid": broken_result["valid"],
                                                "problems": broken_result.get("problems", [])[:5]}
    except KBError as exc:
        report["phase_b_missing_cas_object"] = {"removed": len(victims), "verify_valid": False, "refused": exc.code}
    report["phase_c_s04j_code_on_upgraded_kb"] = s04j(args.s04j_source, work, "open")
    rollback = work / "rollback"
    (rollback / "db").mkdir(parents=True)
    shutil.copytree(work / "kb" / "cas", rollback / "cas")
    shutil.copyfile(work / "kb" / "db" / up["pre_upgrade_file"], rollback / DB_RELATIVE)
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / DB_RELATIVE) == original,
                                  "s04j_v02_code_opens": s04j(args.s04j_source, work, "rollback")}
    rb = report["phase_c_rollback"]["s04j_v02_code_opens"]
    fte = report["phase_b_fte"]
    report["ok"] = (report["migrations_0001_0004_identical"] and report["phase_b_readonly_refusal"] == "KB_UPGRADE_REQUIRED"
                    and (up["from_version"], up["to_version"]) == (4, 5) and [m["name"] for m in up["applied"]] == ["0005_fulltext_eligibility_staging.sql"]
                    and all(v in (True, "ok", 0, "CURRENT") for v in up["checks"].values()) and changed == ["kb_upgrade_event", "schema_migration"] and appended_only
                    and report["phase_b_manifests_identical_to_phase_a"] and report["phase_b_journal_manifest_unchanged_by_s05"]
                    and fte["run_state"] == "LOCKED_FOR_HANDOFF" and fte["summary"]["authority"] == "NON_AUTHORITATIVE"
                    and fte["summary"]["ft_not_retrieved"] == 4 and fte["summary"]["ft_pending"] == 0 and fte["verify_lock"] and fte["verify_export"]
                    and not full["problems"] and verified["valid"] and all(restored["comparison"].values())
                    and report["phase_b_backup_restore"]["eligibility_manifest_in_backup"] == report["phase_b_eligibility_manifest"]
                    and report["phase_b_missing_cas_object"]["removed"] >= 1 and report["phase_b_missing_cas_object"]["verify_valid"] is False
                    and report["phase_c_s04j_code_on_upgraded_kb"] == {"opened": False, "client_version": a["client_version"], "error": "KB_CLIENT_TOO_OLD"}
                    and report["phase_c_rollback"]["db_sha256_equals_original"] and rb["opened"] and rb["schema_version"] == 4
                    and all(rb[k] == a[k] for k in MANIFESTS))
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
