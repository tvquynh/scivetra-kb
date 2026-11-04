"""Upgrade/backup/restore/rollback rehearsal on a REAL S02 v03 schema-1 KB (S03-A item 5, report 07).

Usage (from this source tree)::

    python -I tools/s02_upgrade_rehearsal.py --s02-source <extracted SCIVETRA_S02_METADATA_ENRICHMENT_v03/source> --work <empty dir> --out <file.json>

Phase A runs the accepted S02 v03 code itself (subprocess, its own source tree) to build a schema-1 KB with the
S02 idempotency scenario and report its semantic manifest. Phase B uses THIS (S03) code: read the schema-1 KB,
``rkb upgrade`` with a backup target, verify, back up again, verify-backup, restore into a new root. Phase C runs
S02 v03 code again: it must refuse the upgraded KB (KB_CLIENT_TOO_OLD) and open the rolled-back pre-upgrade file.
All data is synthetic; an audit hook refuses network use in every phase. Output holds hashes and codes, no paths.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ("import sys\n"
         "def _g(e, a):\n"
         "    if e in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):\n"
         "        raise PermissionError('network refused in rehearsal')\n"
         "sys.addaudithook(_g)\n")

PHASE_A = GUARD + """
import json, os, sys
src = sys.argv[1]; root = sys.argv[2]; export = sys.argv[3]
sys.path[:0] = [os.path.join(src, 'src'), src]
import scivetra_kb
from tests.helpers import openalex_retraction_claim, rv_item, write_rv_export
from tests.test_idempotency import scenario
from scivetra_kb.core.db import init_kb, open_reader
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
from scivetra_kb.core.manifest import semantic_manifest
clock = FixedClock(); ids = SequentialIdProvider(clock)
write_rv_export(export, [rv_item('cri_1', dois=[('10.99999/id.1', '10.99999/id.1')], integrity=openalex_retraction_claim()),
                         rv_item('cri_2', dois=[('10.99999/id.2', '10.99999/id.2')], integrity=[]),
                         rv_item('cri_3', dois=[('10.99999/x ', '10.99999/x')])])
init = init_kb(root, clock=clock, ids=ids)
scenario(root, export, clock, ids)
with open_reader(root) as kb:
    print(json.dumps({'client_version': scivetra_kb.__version__, 'schema_version': kb.schema['schema_version'],
                      'semantic_manifest': semantic_manifest(kb)}))
"""

PHASE_C = GUARD + """
import json, os, sys
src = sys.argv[1]; root = sys.argv[2]
sys.path[:0] = [os.path.join(src, 'src'), src]
from scivetra_kb.core.db import open_reader
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import semantic_manifest
try:
    with open_reader(root) as kb:
        print(json.dumps({'opened': True, 'schema_version': kb.schema['schema_version'], 'semantic_manifest_sha256': semantic_manifest(kb)['manifest_sha256']}))
except KBError as exc:
    print(json.dumps({'opened': False, 'error': exc.code}))
"""


def child(code, *args):
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, *args], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit("phase failed: " + proc.stderr[-1500:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--s02-source", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    work = Path(args.work)
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work directory must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    exec(compile(GUARD, "<guard>", "exec"))
    sys.path[:0] = [str(ROOT / "src")]
    import scivetra_kb
    from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
    from scivetra_kb.core.db import DB_RELATIVE, KB, open_reader
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.manifest import full_manifest, semantic_manifest, table_content_sha256
    from scivetra_kb.core.upgrade import S02_TABLES, upgrade_kb
    from scivetra_kb.core.backup import file_sha256

    kb_root, export = work / "kb_s02v03", work / "rv_export"
    report = {"schema": "rkb-s02-upgrade-rehearsal/1", "s03_client_version": scivetra_kb.__version__}
    a = child(PHASE_A, args.s02_source, str(kb_root), str(export))
    report["phase_a_s02_v03_code"] = a
    db = kb_root / DB_RELATIVE
    report["original_db_sha256"] = original = file_sha256(db)
    probe = KB(str(kb_root), readonly=True, _allow_older_schema=True)
    try:
        before = {"semantic_manifest": semantic_manifest(probe), "tables": {t: table_content_sha256(probe.connection, t) for t in S02_TABLES}}
    finally:
        probe.close()
    report["phase_b_before"] = {"s02_semantic_manifest_sha256": before["semantic_manifest"]["manifest_sha256"],
                                "matches_phase_a": before["semantic_manifest"] == a["semantic_manifest"]}
    report["phase_b_readonly_refusal"] = doctor(str(kb_root))["problems"]
    upgrade = upgrade_kb(str(kb_root), str(work / "backups-upgrade"))
    report["phase_b_upgrade"] = {k: upgrade[k] for k in ("upgraded", "from_version", "to_version", "checks", "backup", "original_db_sha256",
                                                          "s02_semantic_manifest_sha256")}
    with open_reader(str(kb_root)) as kb:
        after = {"semantic_manifest": semantic_manifest(kb), "tables": {t: table_content_sha256(kb.connection, t) for t in S02_TABLES}}
        report["phase_b_after"] = {"schema_version": kb.schema["schema_version"], "semantic_manifest_v2": full_manifest(kb)}
    report["phase_b_comparison"] = {"s02_semantic_manifest_identical": after["semantic_manifest"] == before["semantic_manifest"],
                                    "s02_tables_byte_identical": after["tables"] == before["tables"],
                                    "tables_compared": len(S02_TABLES)}
    report["phase_b_doctor_full_problems"] = doctor(str(kb_root), full=True)["problems"]
    backup = create_backup(str(kb_root), str(work / "backups-post"))
    verified = verify_backup(backup["backup_dir"])
    restored = restore_backup(backup["backup_dir"], str(work / "restored"))
    report["phase_b_backup_restore"] = {"verify_valid": verified["valid"], "restore_comparison": restored["comparison"],
                                        "restored_doctor_full_problems": doctor(str(work / "restored"), full=True)["problems"],
                                        "restored_semantic_manifest_v2_identical": doctor(str(work / "restored"), full=True)["semantic_manifest_v2"]
                                        == report["phase_b_after"]["semantic_manifest_v2"]}
    report["phase_c_s02_code_on_upgraded_kb"] = child(PHASE_C, args.s02_source, str(kb_root))
    rollback = work / "rollback"
    shutil.copytree(kb_root, rollback)
    shutil.copyfile(rollback / "db" / upgrade["pre_upgrade_file"], rollback / DB_RELATIVE)
    os.remove(rollback / "db" / upgrade["pre_upgrade_file"])
    report["phase_c_rollback"] = {"db_sha256_equals_original": file_sha256(rollback / DB_RELATIVE) == original,
                                  "s02_v03_code_opens": child(PHASE_C, args.s02_source, str(rollback))}
    ok = (report["phase_b_before"]["matches_phase_a"] and report["phase_b_comparison"]["s02_semantic_manifest_identical"]
          and report["phase_b_comparison"]["s02_tables_byte_identical"] and not report["phase_b_doctor_full_problems"]
          and verified["valid"] and all(restored["comparison"].values()) and report["phase_c_s02_code_on_upgraded_kb"].get("error") == "KB_CLIENT_TOO_OLD"
          and report["phase_c_rollback"]["db_sha256_equals_original"] and report["phase_c_rollback"]["s02_v03_code_opens"]["opened"]
          and report["phase_c_rollback"]["s02_v03_code_opens"]["semantic_manifest_sha256"] == a["semantic_manifest"]["manifest_sha256"])
    report["ok"] = ok
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": ok, "s02_manifest": a["semantic_manifest"]["manifest_sha256"]}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
