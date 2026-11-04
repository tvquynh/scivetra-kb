"""Drive an INSTALLED ``rkb`` executable through the S03 surface with synthetic data only.

Usage: python -I tools/s03_cli_smoke.py --rkb <path to rkb(.exe) in a fresh venv> --work <empty dir> --out <log file>

Uses the frozen native Reprovane 2.0.1 fixture and generated synthetic PDFs from this source tree. The log
replaces the work directory path by <WORK>; it holds commands, exit codes and the key fields of each result.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--rkb", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    work = Path(args.work).resolve()
    args.rkb = str(Path(args.rkb).resolve())
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work directory must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(ROOT)]
    from tests.pdf_fixtures import synthetic_pdf, title_page
    lines, results = [], {}

    def rkb(label, *argv, expect=0):
        proc = subprocess.run([args.rkb, *argv], capture_output=True, text=True, timeout=300)
        out = json.loads(proc.stdout)
        shown = " ".join(a.replace(str(work), "<WORK>") for a in argv)
        lines.append(f"$ rkb {shown}\n  exit={proc.returncode}")
        results[label] = out
        if proc.returncode != expect:
            lines.append("  UNEXPECTED: " + json.dumps(out)[:400])
            raise SystemExit("\n".join(lines))
        return out

    kb, export = str(work / "kb"), work / "rv-export"
    shutil.copytree(ROOT / "tests" / "fixtures" / "rv_native_2_0_1" / "s03-r1", export)
    before = {f.name: f.read_bytes() for f in export.iterdir()}
    version = subprocess.run([args.rkb, "--version"], capture_output=True, text=True).stdout.strip()
    lines.append(f"$ rkb --version\n  {version}")
    rkb("init", "init", "--data-root", kb)
    out = rkb("import", "library", "import-reprovane", "--data-root", kb, "--export-dir", str(export), "--workspace-label", "smoke-ws", "--operator", "smoke.op")
    lines.append(f"  references_created={out['references_created']} papers_minted={out['papers_minted']} integrity_events={out['integrity_events']}")
    out = rkb("import_again", "library", "import-reprovane", "--data-root", kb, "--export-dir", str(export), "--workspace-label", "smoke-ws", "--operator", "smoke.op")
    lines.append(f"  new_import={out['new_import']} references_created={out['references_created']}")
    lines.append(f"  export files unchanged: {before == {f.name: f.read_bytes() for f in export.iterdir()}}")
    out = rkb("search", "library", "search", "--data-root", kb, "--doi", "10.99999/s03.alpha")
    paper = out["papers"][0]["paper_id"]
    out = rkb("search_space", "library", "search", "--data-root", kb, "--doi", "10.99999/s03.alpha ")
    lines.append(f"  trailing-space operator input: status={out['doi_parse']['status']} papers={len(out['papers'])}")
    (work / "version.json").write_text(json.dumps([{"source": "CROSSREF_LICENSE_CONTENT_VERSION", "claim_ref": "synthetic:vor", "value": "vor"}]))
    dv = rkb("version", "asset", "add-version", "--data-root", kb, "--paper-id", paper, "--version-class", "PUBLISHED", "--evidence-file",
             str(work / "version.json"), "--operator", "smoke.op")["document_version_id"]
    fixtures = work / "fixtures"
    fixtures.mkdir()
    (fixtures / "alpha.pdf").write_bytes(synthetic_pdf([title_page(title="Synthetic alpha study", authors=["First Synthetic"], doi="10.99999/s03.alpha")]))
    (fixtures / "html.pdf").write_bytes(b"<!DOCTYPE html><html><body>challenge</body></html>")
    out = rkb("register", "asset", "register", "--data-root", kb, "--fixture-root", str(fixtures), "--file", "alpha.pdf", "--document-version-id", dv,
              "--operator", "smoke.op", "--access-route", "INSTITUTIONAL_LOGIN", "--access-barrier", "LoginRequired")
    asset = out["asset_sha256"]
    lines.append(f"  outcome={out['outcome']} identity_state={out['identity_state']} rights_class={out['rights']['rights_class']}")
    out = rkb("register_html", "asset", "register", "--data-root", kb, "--fixture-root", str(fixtures), "--file", "html.pdf", "--document-version-id", dv,
              "--operator", "smoke.op")
    lines.append(f"  outcome={out['outcome']} reasons={out['reasons']}")
    out = rkb("traversal", "asset", "register", "--data-root", kb, "--fixture-root", str(fixtures), "--file", "../kb/db/kb.sqlite", "--document-version-id", dv,
              "--operator", "smoke.op", expect=2)
    lines.append(f"  error={out['error']}")
    (work / "rights.json").write_text(json.dumps({"license_evidence": [{"source": "CROSSREF_LICENSE", "license_uri": "https://creativecommons.org/licenses/by/4.0/",
                                                                       "content_version": "vor", "claim_ref": "synthetic:license"}],
                                                  "availability_evidence": [{"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion",
                                                                             "claim_ref": "synthetic:oa"}],
                                                  "access_provenance": {"access_route": "INSTITUTIONAL_LOGIN", "access_barrier_observed": ["LoginRequired"]}}))
    out = rkb("assess", "rights", "assess", "--data-root", kb, "--asset", asset, "--document-version-id", dv, "--evidence-file", str(work / "rights.json"),
              "--operator", "smoke.op")
    lines.append(f"  rights_class={out['rights_class']} permissions={json.dumps(out['permissions'], sort_keys=True)}")
    for action in ("A1", "A2", "A7", "A8", "A9"):
        out = rkb("gate_" + action, "rights", "gate", "--data-root", kb, "--asset", asset, "--document-version-id", dv, "--action", action)
        lines.append(f"  {action}: {out['decision']} ({out['reason']})")
    out = rkb("queue", "library", "review-queue", "--data-root", kb)
    lines.append(f"  review counts={json.dumps(out['counts'], sort_keys=True)}")
    out = rkb("doctor", "doctor", "--data-root", kb, "--full")
    lines.append(f"  ok={out['ok']} problems={out['problems']} semantic_manifest_v2={out['semantic_manifest_v2']['manifest_sha256']}")
    out = rkb("backup", "backup", "--data-root", kb, "--target", str(work / "operator-backups"))
    backup = out["backup_dir"]
    out = rkb("verify", "verify-backup", "--backup", backup)
    lines.append(f"  valid={out['valid']} cas_object_count={out['cas_object_count']}")
    out = rkb("restore", "restore", "--backup", backup, "--data-root", str(work / "restored"))
    lines.append(f"  comparison={json.dumps(out['comparison'], sort_keys=True)}")
    out = rkb("doctor_restored", "doctor", "--data-root", str(work / "restored"), "--full")
    lines.append(f"  restored semantic_manifest_v2={out['semantic_manifest_v2']['manifest_sha256']} identical={out['semantic_manifest_v2'] == results['doctor']['semantic_manifest_v2']}")
    out = rkb("reconcile", "reconcile", "--data-root", kb, "--workspace-label", "smoke-ws", "--export-dir", str(export))
    lines.append(f"  status={out['status']}")
    # S03-F01: a fake CC URI with an OA location must not be classified as an open licence.
    fake = json.loads((work / "rights.json").read_text())
    fake["license_evidence"][0]["license_uri"] = "https://creativecommons.org/licenses/by/not-a-real-license"
    (work / "rights-fake.json").write_text(json.dumps(fake))
    out = rkb("assess_fake", "rights", "assess", "--data-root", str(work / "restored"), "--asset", asset, "--document-version-id", dv,
              "--evidence-file", str(work / "rights-fake.json"), "--operator", "smoke.op")
    lines.append(f"  fake CC URI: rights_class={out['rights_class']} flags={out['flags']}")
    # S03-F02: a KB whose DB references a missing CAS object is refused by backup, and no backup folder is created.
    (work / "restored" / "cas" / "sha256" / asset[:2] / asset).unlink()
    out = rkb("doctor_broken", "doctor", "--data-root", str(work / "restored"), "--full", expect=3)
    lines.append(f"  ok={out['ok']} problems={out['problems']}")
    out = rkb("backup_broken", "backup", "--data-root", str(work / "restored"), "--target", str(work / "broken-backups"), expect=3)
    created = sorted(p.name for p in (work / "broken-backups").glob("rkb-backup-*")) if (work / "broken-backups").exists() else []
    lines.append(f"  error={out['error']} backup_folders_created={len(created)}")
    if created:
        raise SystemExit("backup folder created for an incomplete KB")
    out = rkb("upgrade", "upgrade", "--data-root", kb, "--backup-target", str(work / "operator-backups"))
    lines.append(f"  code={out['code']}")
    out = rkb("protected", "init", "--data-root", "E:\\ResearchKB", expect=2)
    lines.append(f"  error={out['error']}")
    out = rkb("live", "metadata", "fetch", "--data-root", kb, "--doi", "10.99999/s03.alpha", "--live", expect=2)
    lines.append(f"  error={out['error']}")
    Path(args.out).write_text("# S03 installed-CLI smoke (synthetic data only; paths shown as <WORK>)\n" + "\n".join(lines) + "\n",
                              encoding="utf-8", newline="\n")
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
