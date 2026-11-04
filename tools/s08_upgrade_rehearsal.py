"""S08 upgrade rehearsal with the REAL accepted S07 v02 code: schema 7 -> 8, backup first, rollback, restore.

Phase A (child process, accepted S07 v02 source): init a KB, import a synthetic release, register a classic synthetic PDF
(G1 v1), confirm the link, record an open-licence rights assessment, and run an S07 TA screening proposal; record every
table digest.
Phase B (this S08 code): normal opens refuse (KB_UPGRADE_REQUIRED); 'upgrade' writes a verified backup, applies only 0008,
keeps every prior table (including the S07 proposal) byte-identical; the asset parses; S07 screening still works; an S08
profile registers; doctor --full is clean with zero S08 invariant violations.
Phase C (child, S07 v02 code): the kept pre-upgrade file restores the exact schema-7 bytes and S07 v02 opens it (doctor ok).
Phase D: the backup verifies and restores into a new root; doctor reports it needs an explicit upgrade (no implicit migration).
Phase E (optional, --s08v01-source; S08 v02 changed the not-yet-accepted 0008): the same schema-7 KB upgraded by the S08 v01 code
is refused by this code (MIGRATION_DRIFT, nothing written); recovery restores the kept pre-upgrade schema-7 file and upgrades it
with this code.
Phase F (optional, --s08v02-source; S08 v03 did not change 0008): the same schema-7 KB upgraded by the S08 v02 code opens with this
code without any migration, doctor --full is clean and the S08 invariants are 0.
Usage: python -I tools/s08_upgrade_rehearsal.py --s07-source DIR --work DIR --out FILE.json [--s08v01-source DIR] [--s08v02-source DIR]   (pinned pypdf required)
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
    release = kb.one("SELECT release_import_id FROM reprovane_release_import")["release_import_id"]
    from tests.s07_helpers import CRITERIA
    from scivetra_kb.screening.service import ScreeningService
    s7 = ScreeningService(kb)
    cs = s7.create_criteria_set(task_type="TA_SCREEN", criteria=CRITERIA, label="pre-upgrade", operator="op.r")["criteria_set_id"]
    scope = s7.create_scope(task_type="TA_SCREEN", criteria_set_id=cs, operator="op.r", label="pre-upgrade", content_classes=["METADATA_ONLY"],
                            paper_ids=[paper], release_import_id=release)["scope_id"]
    s7.build_packs(scope_id=scope, operator="op.r")
    s7.run_mock(scope_id=scope, operator="op.r2")
    print(json.dumps({"client": __version__, "schema": kb.schema["schema_version"], "asset": out["asset_sha256"], "link": out["link_id"], "paper": paper,
                      "release": release}))
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


PHASE_E = r'''
import json, os, sys
src = sys.argv[1]; root = sys.argv[2]; target = sys.argv[3]
sys.path[:0] = [os.path.join(src, "src"), src]
from scivetra_kb import __version__
from scivetra_kb.core.upgrade import upgrade_kb
up = upgrade_kb(root, target)
print(json.dumps({"client": __version__, "from_to": [up["from_version"], up["to_version"]], "pre_upgrade_file": up["pre_upgrade_file"]}))
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
    for flag in ("--s07-source", "--work", "--out"):
        p.add_argument(flag, required=True)
    p.add_argument("--s08v01-source")
    p.add_argument("--s08v02-source")
    args = p.parse_args(argv)
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    root, fx = work / "kb", work / "fixtures"
    fx.mkdir()
    report = {"schema": "rkb-s08-upgrade-rehearsal/1"}
    report["phase_a_s07"] = child(PHASE_A, args.s07_source, str(root), str(fx))
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
    from tests.s07_helpers import CRITERIA
    from scivetra_kb.screening.service import ScreeningService
    with open_writer(str(root)) as kb:
        parsed = ParseService(kb, allow_language_level_network_guard=True).parse(asset_sha256=report["phase_a_s07"]["asset"],
                                                                                  link_id=report["phase_a_s07"]["link"], purpose="RESEARCH_PARSE", operator="op.r")
        verified = ParseService(kb).verify(artifact_id=parsed["artifact_id"]) if parsed.get("artifact_id") else {"valid": False}
        svc = ScreeningService(kb)
        cs = svc.create_criteria_set(task_type="TA_SCREEN", criteria=CRITERIA, label="rehearsal", operator="op.r")["criteria_set_id"]
        scope = svc.create_scope(task_type="TA_SCREEN", criteria_set_id=cs, operator="op.r", label="rehearsal", content_classes=["METADATA_ONLY"],
                                 paper_ids=[report["phase_a_s07"]["paper"]], release_import_id=report["phase_a_s07"]["release"])["scope_id"]
        svc.build_packs(scope_id=scope, operator="op.r")
        proposal = svc.run_mock(scope_id=scope, operator="op.r2")["results"][0]
        screening = {"proposal_status": proposal.get("status"), "verification": svc.verify(proposal_id=proposal["proposal_id"], operator="op.v")["state"]}
        from scivetra_kb.evidence.service import EvidenceService
        profile = EvidenceService(kb).register_profile(operator="op.r", builtin_key="rkb-malware-detection")
    doc = doctor(str(root), full=True)
    report["phase_b_s08"] = {"normal_open_refused": refused, "from_to": [up["from_version"], up["to_version"]], "applied": [a["name"] for a in up["applied"]],
                             "checks": up["checks"], "prior_tables_byte_identical_recomputed": prior_identical,
                             "new_tables": sorted(set(after) - set(before)), "parse_outcome": parsed["outcome"], "parse_reasons": parsed.get("reasons", []), "parse_status": parsed.get("status"),
                             "artifact_verified": verified["valid"], "doctor_problems": doc["problems"], "parse_invariants": doc.get("parse_invariants"),
                             "screening_after_upgrade": screening, "screening_invariants": doc.get("screening_invariants"),
                             "evidence_profile_created": profile["created"], "evidence_invariants": doc.get("evidence_invariants")}
    rollback = work / "rollback"
    shutil.copytree(root, rollback)
    shutil.copyfile(root / "db" / up["pre_upgrade_file"], rollback / "db" / "kb.sqlite")
    report["phase_c_rollback"] = {"db_sha256_equals_original": sha(rollback / "db" / "kb.sqlite") == original,
                                  "s07_v02_doctor": child(PHASE_C, args.s07_source, str(rollback))}
    backup = work / "backups" / up["backup"]["backup_name"]
    restored = work / "restored"
    report["phase_d_backup"] = {"verify_backup_valid": verify_backup(str(backup))["valid"], "restore": bool(restore_backup(str(backup), str(restored))),
                                "restored_doctor_problems": doctor(str(restored), full=False)["problems"]}
    if args.s08v01_source:
        v01 = work / "s08v01_kb"
        shutil.copytree(rollback, v01)
        e = {"s08_v01_upgrade": child(PHASE_E, args.s08v01_source, str(v01), str(work / "s08v01_backups"))}
        v01_db = v01 / "db" / "kb.sqlite"
        before_v01 = sha(v01_db)
        try:
            open_reader(str(v01)).__enter__()
            e["v02_open"] = "OPENED"
        except SchemaError as exc:
            e["v02_open"] = exc.code
        try:
            upgrade_kb(str(v01), str(work / "s08v01_backups_v02"))
            e["v02_upgrade"] = "UPGRADED"
        except SchemaError as exc:
            e["v02_upgrade"] = exc.code
        e["v01_db_unchanged"] = sha(v01_db) == before_v01
        shutil.copyfile(v01 / "db" / e["s08_v01_upgrade"]["pre_upgrade_file"], v01_db)
        e["pre_upgrade_file_is_schema7_original"] = sha(v01_db) == original
        rec = upgrade_kb(str(v01), str(work / "s08v01_backups_recovery"))
        e["recovery_upgrade"] = {"from_to": [rec["from_version"], rec["to_version"]], "applied": [a["name"] for a in rec["applied"]]}
        e["recovery_doctor_problems"] = doctor(str(v01), full=True)["problems"]
        e["ok"] = (e["s08_v01_upgrade"]["from_to"] == [7, 8] and e["v02_open"] == "MIGRATION_DRIFT" and e["v02_upgrade"] == "MIGRATION_DRIFT"
                   and e["v01_db_unchanged"] and e["pre_upgrade_file_is_schema7_original"] and e["recovery_upgrade"]["from_to"] == [7, 8]
                   and not e["recovery_doctor_problems"])
        report["phase_e_s08v01_kb"] = e
    if args.s08v02_source:
        v02 = work / "s08v02_kb"
        shutil.copytree(rollback, v02)
        f = {"s08_v02_upgrade": child(PHASE_E, args.s08v02_source, str(v02), str(work / "s08v02_backups"))}
        with open_reader(str(v02)) as kb:
            f["opens_with_this_code_schema"] = kb.schema["schema_version"]
        rep_f = doctor(str(v02), full=True)
        f["doctor_problems"], f["evidence_invariants"] = rep_f["problems"], rep_f.get("evidence_invariants")
        f["ok"] = (f["s08_v02_upgrade"]["from_to"] == [7, 8] and f["opens_with_this_code_schema"] == 8 and not f["doctor_problems"]
                   and set((f["evidence_invariants"] or {"x": 1}).values()) == {0})
        report["phase_f_s08v02_kb"] = f
    b, c, d = report["phase_b_s08"], report["phase_c_rollback"], report["phase_d_backup"]
    report["ok"] = bool(refused == "KB_UPGRADE_REQUIRED" and b["from_to"] == [7, 8] and b["applied"] == ["0008_scientific_evidence_extraction.sql"]
                        and b["screening_after_upgrade"]["proposal_status"] == "OK" and b["evidence_profile_created"]
                        and set(b["evidence_invariants"].values()) == {0}
                        and b["checks"]["prior_tables_byte_identical"] and prior_identical and b["parse_outcome"] == "PRODUCED" and b["artifact_verified"]
                        and not b["doctor_problems"] and c["db_sha256_equals_original"] and c["s07_v02_doctor"]["ok"] and c["s07_v02_doctor"]["schema"] == 7
                        and d["verify_backup_valid"] and d["restored_doctor_problems"] == ["KB_UPGRADE_REQUIRED"]
                        and report.get("phase_e_s08v01_kb", {"ok": True})["ok"] and report.get("phase_f_s08v02_kb", {"ok": True})["ok"])
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"]}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
