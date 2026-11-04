"""S11 v01 practice database opened by S11 v02 code (rubric /2): what happens to existing synthesis records.

Phase A (child process, the S11 v01 source): a synthetic two-paper S08 world, an S11 scope, a template claim and an author-supplied
thematic claim, an audit and a human attestation; the KB folder is copied out.
Phase B (this v02 code): the schema is still 11 (migration 0011 is byte-identical, no upgrade step); doctor --full is clean; the v01
scope refuses status / audit / export with RUBRIC_CHANGED (its audits were made under rubric /1); every v01 syn_* row is unchanged;
a new scope over the same S08 evidence scope works under rubric /2 (template claim SUPPORTED, author claim PARTIAL).
Usage: python -I tools/s11_v01_practice_db.py --v01-source DIR --work DIR --out FILE.json   (pinned pypdf required)
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("syn_scope", "syn_claim", "syn_snapshot", "syn_audit_run", "syn_claim_audit", "syn_contradiction", "syn_review", "syn_export_event",
          "syn_share_request")

PHASE_A = r'''
import json, os, shutil, sys
src = sys.argv[1]; target = sys.argv[2]
sys.path[:0] = [os.path.join(src, "src"), src]
from tests import netguard
netguard.install()
from scivetra_kb import __version__
from scivetra_kb.synthesis import rules as R
from tests.s11_helpers import S11Case, STUDY_A, STUDY_B
t = S11Case(methodName="run")
t.run = lambda *a, **k: None
t.setUp()
try:
    t.syn_world([[STUDY_A], [STUDY_B]])
    t.claim("C1", "REPORTED_VALUE", [t.draft(0, fpr_operating_point="1")], measurement=t.tpr("93.1"))
    t.claim("C2", "THEMATIC", [t.draft(0, "LIMITATION"), t.draft(1, "LIMITATION")], statement="Both studies state limitations of their evaluation scope.",
            evidence_class="LIMITATION")
    run = t.audit()
    t.scall("review", scope_id=t.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="ATTEST_DRAFT_ACCURATE",
            reason_codes=["UNVERIFIED_CLAIMS_ACKNOWLEDGED"])
    shutil.copytree(t.root, target)
    print(json.dumps({"client": __version__, "rubric": R.RUBRIC_ID, "scope_id": t.syn, "ev_scope": t.ev_scope, "audit_id": run["audit_id"],
                      "verdicts": t.verdicts(run["report"]), "tpr_draft": t.draft(0, fpr_operating_point="1"),
                      "lim_drafts": [t.draft(0, "LIMITATION"), t.draft(1, "LIMITATION")]}))
finally:
    t.tearDown()
'''


def table_digest(kb, table):
    rows = kb.all(f"SELECT * FROM {table} ORDER BY rowid")
    return {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--v01-source", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    work = Path(args.work).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    kb_dir = work / "kb_v01"
    a = subprocess.run([sys.executable, "-I", "-B", "-c", PHASE_A, str(Path(args.v01_source).resolve()), str(kb_dir)], capture_output=True, text=True)
    if a.returncode:
        print(a.stdout[-2000:], a.stderr[-4000:])
        return 2
    v01 = json.loads(a.stdout.strip().splitlines()[-1])
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from tests import netguard
    netguard.install()
    from scivetra_kb import __version__
    from scivetra_kb.core.db import open_writer
    from scivetra_kb.core.doctor import doctor
    from scivetra_kb.core.errors import KBError
    from scivetra_kb.synthesis import rules as R
    from scivetra_kb.synthesis.service import SynthesisService
    out = {"schema": "scivetra-s11-v01-practice-db/1", "v01": v01, "v02_client": __version__, "v02_rubric": R.RUBRIC_ID, "checks": {}}
    with open_writer(str(kb_dir)) as kb:
        out["schema_version"] = kb.schema["schema_version"]
        before = {t: table_digest(kb, t) for t in TABLES}
    out["doctor_full_ok"] = doctor(str(kb_dir), full=True).get("ok")
    refusals = {}
    with open_writer(str(kb_dir)) as kb:
        svc = SynthesisService(kb)
        for name, call in (("status", lambda: svc.status(scope_id=v01["scope_id"])), ("audit", lambda: svc.audit(scope_id=v01["scope_id"], operator="op.auditor")),
                           ("reviews", lambda: svc.reviews(scope_id=v01["scope_id"])),
                           ("export", lambda: svc.export(scope_id=v01["scope_id"], out_dir=str(work / "export"), operator="op.exporter"))):
            try:
                call()
                refusals[name] = "NOT_REFUSED"
            except KBError as exc:
                refusals[name] = exc.code
        after = {t: table_digest(kb, t) for t in TABLES}
        scope = svc.create_scope(ev_scope_id=v01["ev_scope"], operator="op.synth", label="new scope under rubric 2", review_policy="SINGLE_HUMAN",
                                 review_policy_reason="synthetic practice database check")["scope_id"]
        spec = {"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": {
            "metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "dataset": "EMBER",
            "dataset_year": "2018", "split": "TEST", "pe_type": "WIN32"}}
        svc.add_claim(scope_id=scope, claim_key="C1", spec=spec, cites=[v01["tpr_draft"]], operator="op.author")
        svc.add_claim(scope_id=scope, claim_key="C1-AUTHOR", spec=spec, cites=[v01["tpr_draft"]], operator="op.author",
                      statement="The study reports a TPR of 93.1% at 1% FPR on the EMBER 2018 test split for Win32 files.")
        rep = svc.audit(scope_id=scope, operator="op.auditor")["report"]
        new_verdicts = {c["claim_key"]: c["verdict"] for c in rep["claims"]}
    out["v01_scope_refusals"] = refusals
    out["v01_rows_unchanged"] = all(before[t] == after[t] for t in TABLES)
    out["v01_rows"] = {t: before[t]["rows"] for t in TABLES}
    out["new_scope_verdicts"] = new_verdicts
    out["checks"] = {"schema_still_11": out["schema_version"] == 11, "doctor_ok": out["doctor_full_ok"] is True,
                     "v01_scope_refused_rubric_changed": set(refusals.values()) == {"RUBRIC_CHANGED"}, "v01_rows_unchanged": out["v01_rows_unchanged"],
                     "new_scope_rubric2": new_verdicts == {"C1": "SUPPORTED", "C1-AUTHOR": "PARTIAL"}}
    out["ok"] = all(out["checks"].values())
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": out["ok"], "checks": out["checks"], "refusals": refusals}, indent=1))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
