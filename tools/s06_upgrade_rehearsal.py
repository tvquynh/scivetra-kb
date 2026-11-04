"""S06 upgrade rehearsal with the REAL accepted S05 v02 code: schema 5 -> 6, backup first, rollback, restore.

Phase A (child process, accepted S05 v02 source): init a KB, import a synthetic release, register a classic synthetic PDF
(G1 v1), confirm the link, record an open-licence rights assessment; record every table digest.
Phase B (this S06 code): normal opens refuse (KB_UPGRADE_REQUIRED); 'upgrade' writes a verified backup, applies only 0006,
keeps every prior table byte-identical; doctor --full is clean; the pre-existing asset parses and verifies.
Phase C (child, S05 v02 code): the kept pre-upgrade file restores the exact schema-5 bytes and S05 v02 opens it (doctor ok).
Phase D: the backup verifies and restores into a new root; S06 doctor reports it needs an explicit upgrade (no implicit migration).
Usage: python -I tools/s06_upgrade_rehearsal.py --s05-source DIR --work DIR --out FILE.json   (pinned pypdf required)
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PHASE_A = r'''
import json, os, sys
from pathlib import Path
src = sys.argv[1]; root = sys.argv[2]; fx = sys.argv[3]
sys.path[:0] = [os.path.join(src, "src"), src]
from scivetra_kb import __version__
from scivetra_kb.core.db import init_kb, open_writer
from scivetra_kb.assets.service import AssetService
from scivetra_kb.library.service import LibraryService
from tests.s03_helpers import s03_item, write_export
from tests.pdf_fixtures import synthetic_pdf, title_page
init_kb(root)
export = write_export(os.path.join(fx, "rv"), [s03_item("cri_r", dois=[("10.99999/s06.r", "10.99999/s06.r")], title="Synthetic rehearsal paper",
                                                        author="First Synthetic")])
os.makedirs(os.path.join(fx, "pdf"))
Path(fx, "pdf", "r.pdf").write_bytes(synthetic_pdf([title_page(title="Synthetic rehearsal paper", authors=["First Synthetic"], doi="10.99999/s06.r",
                                                               body=["Rehearsal body text."])]))
with open_writer(root) as kb:
    LibraryService(kb).import_release(export, workspace_label="ws", operator="op.r")
    paper = kb.one("SELECT paper_id FROM project_paper_reference")["paper_id"]
    svc = AssetService(kb)
    dv = svc.add_document_version(paper_id=paper, version_class="PUBLISHED", operator="op.r",
                                  evidence=[{"source": "CROSSREF_LICENSE_CONTENT_VERSION", "claim_ref": "synthetic:vor", "value": "vor"}])["document_version_id"]
    out = svc.register_asset(fixture_root=os.path.join(fx, "pdf"), relative_path="r.pdf", document_version_id=dv, operator="op.r")
    svc.review_link(link_id=out["link_id"], decision="CONFIRM", operator="op.r", reason="human check")
    svc.assess_rights(asset_sha256=out["asset_sha256"], document_version_id=dv, operator="op.r", evidence={"license_evidence": [
        {"source": "CROSSREF_LICENSE", "license_uri": "https://creativecommons.org/licenses/by/4.0/", "content_version": "vor", "claim_ref": "synthetic:lic"}],
        "availability_evidence": [{"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion", "claim_ref": "synthetic:oa"}]})
    print(json.dumps({"client": __version__, "schema": kb.schema["schema_version"], "asset": out["asset_sha256"], "link": out["link_id"]}))
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
    proc = subprocess.run([sys.executable, "-I", "-c", script, *args], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit("child failed: " + proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv=None):
    p = argparse.ArgumentParser()
    for flag in ("--s05-source", "--work", "--out"):
        p.add_argument(flag, required=True)
    args = p.parse_args(argv)
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    root, fx = work / "kb", work / "fixtures"
    fx.mkdir()
    report = {"schema": "rkb-s06-upgrade-rehearsal/1"}
    report["phase_a_s05"] = child(PHASE_A, args.s05_source, str(root), str(fx))
    db = root / "db" / "kb.sqlite"
    before, original = tables(db), sha(db)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from scivetra_kb.core.backup import restore_backup, verify_backup
    from scivetra_kb.core.db import open_reader, open_writer
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import SchemaError
    from scivetra_kb.core.upgrade import upgrade_kb
    from scivetra_kb.parse.service import ParseService
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
        parsed = ParseService(kb, allow_language_level_network_guard=True).parse(asset_sha256=report["phase_a_s05"]["asset"],
                                                                                  link_id=report["phase_a_s05"]["link"], purpose="RESEARCH_PARSE", operator="op.r")
        verified = ParseService(kb).verify(artifact_id=parsed["artifact_id"]) if parsed.get("artifact_id") else {"valid": False}
    doc = doctor(str(root), full=True)
    report["phase_b_s06"] = {"normal_open_refused": refused, "from_to": [up["from_version"], up["to_version"]], "applied": [a["name"] for a in up["applied"]],
                             "checks": up["checks"], "prior_tables_byte_identical_recomputed": prior_identical,
                             "new_tables": sorted(set(after) - set(before)), "parse_outcome": parsed["outcome"], "parse_reasons": parsed.get("reasons", []), "parse_status": parsed.get("status"),
                             "artifact_verified": verified["valid"], "doctor_problems": doc["problems"], "parse_invariants": doc.get("parse_invariants")}
    rollback = work / "rollback"
    shutil.copytree(root, rollback)
    shutil.copyfile(root / "db" / up["pre_upgrade_file"], rollback / "db" / "kb.sqlite")
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / "db" / "kb.sqlite") == original,
                                  "s05_v02_doctor": child(PHASE_C, args.s05_source, str(rollback))}
    backup = work / "backups" / up["backup"]["backup_name"]
    restored = work / "restored"
    report["phase_d_backup"] = {"verify_backup_valid": verify_backup(str(backup))["valid"], "restore": bool(restore_backup(str(backup), str(restored))),
                                "restored_doctor_problems": doctor(str(restored), full=False)["problems"]}
    b, c, d = report["phase_b_s06"], report["phase_c_rollback"], report["phase_d_backup"]
    report["ok"] = bool(refused == "KB_UPGRADE_REQUIRED" and b["from_to"] == [5, 6] and b["applied"] == ["0006_pdf_parse_artifacts.sql"]
                        and b["checks"]["prior_tables_byte_identical"] and prior_identical and b["parse_outcome"] == "PRODUCED" and b["artifact_verified"]
                        and not b["doctor_problems"] and c["db_sha256_equals_original"] and c["s05_v02_doctor"]["ok"] and c["s05_v02_doctor"]["schema"] == 5
                        and d["verify_backup_valid"] and d["restored_doctor_problems"] == ["KB_UPGRADE_REQUIRED"])
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
