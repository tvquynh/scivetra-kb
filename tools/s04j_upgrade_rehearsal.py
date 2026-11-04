"""Upgrade rehearsal S04 v02 (schema 3) -> S04J (schema 4) using the ACCEPTED S04 v02 code to build and re-open the KB.

Usage: python -I -B tools/s04j_upgrade_rehearsal.py --s04-source <S04 v02 source/> --work <empty dir> --out <json>

Phase A (S04 v02 code, separate interpreter): init a schema-3 KB, library-import the native Reprovane 2.0.1 Stage-11
fixture, export one PBD bridge batch (rv-manual-queue); record the S02/library/bridge manifests and the DB hash.
Phase B (this S04J code): reads refuse (KB_UPGRADE_REQUIRED); 'upgrade' backs up first and applies only 0004 to a copy;
every prior table byte-identical; S02/library/bridge manifests identical; a full simulated journal pipeline runs;
backup/verify/restore with identical domain summaries; doctor --full clean.
Phase C (S04 v02 code): refuses the upgraded KB (KB_CLIENT_TOO_OLD); the pre-upgrade file reopens with identical manifests.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

S04_SCRIPT = r'''
import json, sys
from pathlib import Path
src, work, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path[:0] = [str(src), str(src / "src")]
from scivetra_kb import __version__
from scivetra_kb.core.db import init_kb, open_reader, open_writer
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import bridge_manifest, library_manifest, semantic_manifest
kb_root = work / ("kb" if phase != "rollback" else "rollback")
if phase == "build":
    from scivetra_kb.bridge.service import BridgeService
    from scivetra_kb.library.service import LibraryService
    init_kb(str(kb_root))
    with open_writer(str(kb_root)) as kb:
        LibraryService(kb).import_release(str(work / "corpus"), workspace_label="ws", operator="s04.op")
        BridgeService(kb).export_batch(source_dir=str(work / "fulltext"), kind="rv-manual-queue", workspace_label="ws",
                                       out_dir=str(work / "batch"), operator="s04.op")
try:
    with open_reader(str(kb_root)) as kb:
        print(json.dumps({"opened": True, "client_version": __version__, "schema_version": kb.schema["schema_version"],
                          "s02_manifest": semantic_manifest(kb)["manifest_sha256"], "library_manifest": library_manifest(kb)["manifest_sha256"],
                          "bridge_manifest": bridge_manifest(kb)["manifest_sha256"]}))
except KBError as exc:
    print(json.dumps({"opened": False, "client_version": __version__, "error": exc.code}))
'''


def s04(src, work, phase):
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", S04_SCRIPT, str(src), str(work), phase], capture_output=True, text=True, timeout=300)
    if proc.returncode:
        raise SystemExit("S04 v02 phase failed: " + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--s04-source", required=True, type=Path)
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
    from tests.journal_helpers import SimulatedProviders, pipeline, small_scenario
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import KBError
    from scivetra_kb.core.manifest import bridge_manifest, journal_manifest, library_manifest, semantic_manifest
    from scivetra_kb.core.upgrade import upgrade_kb
    shutil.copytree(NATIVE_FT / "corpus", work / "corpus")
    shutil.copytree(NATIVE_FT / "fulltext", work / "fulltext")
    report = {"schema": "rkb-s04j-upgrade-rehearsal/1",
              "s04_source_migration_sha256": {p.name: sha(p) for p in sorted((args.s04_source / "src/scivetra_kb/core/migrations").glob("*.sql"))}}
    report["phase_a_s04_v02_code"] = a = s04(args.s04_source, work, "build")
    db = work / "kb" / DB_RELATIVE
    report["original_db_sha256"] = original = sha(db)
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
                 "library_manifest": library_manifest(kb)["manifest_sha256"], "bridge_manifest": bridge_manifest(kb)["manifest_sha256"]}
    report["phase_b_after"] = after
    report["phase_b_manifests_identical_to_phase_a"] = all(after[k] == a[k] for k in ("s02_manifest", "library_manifest", "bridge_manifest"))
    with open_writer(str(work / "kb")) as kb:
        run = pipeline(kb, SimulatedProviders(small_scenario(40)), sizes=(10, 10, 10))
        report["phase_b_journal_pipeline"] = {"collection_state": run["version"]["collection_state"], "members": run["version"]["member_count"],
                                              "runs": {p: r["state"] for p, r in run["runs"].items()}}
        report["phase_b_bridge_manifest_unchanged_by_journal"] = bridge_manifest(kb)["manifest_sha256"] == a["bridge_manifest"]
        report["phase_b_journal_manifest"] = journal_manifest(kb)["manifest_sha256"]
    full = doctor(str(work / "kb"), full=True)
    report["phase_b_doctor_full_problems"] = full["problems"]
    report["phase_b_journal_invariants"] = full.get("journal_invariants")
    backup = create_backup(str(work / "kb"), str(work / "operator-backups"))
    verified = verify_backup(backup["backup_dir"])
    restored = restore_backup(backup["backup_dir"], str(work / "restored"))
    report["phase_b_backup_restore"] = {"verify_valid": verified["valid"], "schema": verified["kb_schema_version"],
                                        "cas_referenced": verified["cas_referenced_count"], "restore_comparison": restored["comparison"],
                                        "journal_manifest_in_backup": verified["domain"].get("journal_manifest_sha256")}
    report["phase_c_s04_code_on_upgraded_kb"] = s04(args.s04_source, work, "open")
    rollback = work / "rollback"
    (rollback / "db").mkdir(parents=True)
    shutil.copytree(work / "kb" / "cas", rollback / "cas")
    shutil.copyfile(work / "kb" / "db" / up["pre_upgrade_file"], rollback / DB_RELATIVE)
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / DB_RELATIVE) == original,
                                  "s04_v02_code_opens": s04(args.s04_source, work, "rollback")}
    rb = report["phase_c_rollback"]["s04_v02_code_opens"]
    report["ok"] = (report["phase_b_readonly_refusal"] == "KB_UPGRADE_REQUIRED" and (up["from_version"], up["to_version"]) == (3, 4)
                    and [m["name"] for m in up["applied"]] == ["0004_journal_harvesting.sql"]
                    and all(v in (True, "ok", 0, "CURRENT") for v in up["checks"].values()) and report["phase_b_manifests_identical_to_phase_a"]
                    and report["phase_b_journal_pipeline"]["collection_state"] == "COMPLETE_RELATIVE_TO_RECORDED_QUERIES"
                    and report["phase_b_bridge_manifest_unchanged_by_journal"] and not full["problems"] and verified["valid"]
                    and all(restored["comparison"].values())
                    and report["phase_b_backup_restore"]["journal_manifest_in_backup"] == report["phase_b_journal_manifest"]
                    and report["phase_c_s04_code_on_upgraded_kb"] == {"opened": False, "client_version": a["client_version"], "error": "KB_CLIENT_TOO_OLD"}
                    and report["phase_c_rollback"]["db_sha256_equals_original"] and rb["opened"] and rb["schema_version"] == 3
                    and all(rb[k] == a[k] for k in ("s02_manifest", "library_manifest", "bridge_manifest")))
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
