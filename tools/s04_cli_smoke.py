"""Drive an INSTALLED ``rkb`` through the S04 bridge surface with synthetic data only.

Usage: python -I -B tools/s04_cli_smoke.py --rkb <rkb(.exe) in a fresh venv> --work <empty dir> --out <log file>
Uses the frozen native Reprovane 2.0.1 Stage-11/11B fixture and generated synthetic PDFs; emulates the PBD result CSV.
Paths are written to the log as <WORK>.
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
    rkb_exe = str(Path(args.rkb).resolve())
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work directory must be absent or empty")
    work.mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [str(ROOT)]
    from tests.bridge_helpers import pbd_write_results, result_row
    from tests.pdf_fixtures import html_challenge, synthetic_pdf, title_page
    lines = []

    def rkb(*argv, expect=0):
        proc = subprocess.run([rkb_exe, *argv], capture_output=True, text=True, timeout=300)
        out = json.loads(proc.stdout)
        lines.append(f"$ rkb {' '.join(a.replace(str(work), '<WORK>') for a in argv)}\n  exit={proc.returncode} stderr={proc.stderr.strip()!r}")
        if proc.returncode != expect:
            lines.append("  UNEXPECTED: " + json.dumps(out)[:600])
            Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            raise SystemExit("unexpected exit code")
        if str(work) in proc.stdout or str(work) in proc.stderr:
            raise SystemExit("a local path leaked into CLI output")
        return out

    kb = str(work / "kb")
    shutil.copytree(ROOT / "tests" / "fixtures" / "rv_native_2_0_1_fulltext" / "corpus", work / "rv-corpus")
    shutil.copytree(ROOT / "tests" / "fixtures" / "rv_native_2_0_1_fulltext" / "fulltext", work / "rv-fulltext")
    lines.append("$ rkb --version\n  " + subprocess.run([rkb_exe, "--version"], capture_output=True, text=True).stdout.strip())
    out = rkb("init", "--data-root", kb)
    lines.append(f"  schema_version={out['schema_version']}")
    rkb("library", "import-reprovane", "--data-root", kb, "--export-dir", str(work / "rv-corpus"), "--workspace-label", "smoke-ws", "--operator", "smoke.op")
    out = rkb("bridge", "export", "--data-root", kb, "--source-dir", str(work / "rv-fulltext"), "--kind", "rv-manual-queue", "--workspace-label", "smoke-ws",
              "--out-dir", str(work / "batch"), "--operator", "smoke.op")
    batch = out["batch_id"]
    lines.append(f"  rows={out['row_count']} flags={json.dumps(out['flag_counts'], sort_keys=True)} expected_hash_supplied={out['expected_hash_supplied']}")
    out2 = rkb("bridge", "export", "--data-root", kb, "--source-dir", str(work / "rv-fulltext"), "--kind", "rv-manual-queue", "--workspace-label",
               "smoke-ws", "--out-dir", str(work / "batch"), "--operator", "smoke.op", expect=2)
    lines.append(f"  error={out2['error']}")
    manifest = json.loads((work / "batch" / "bridge_manifest.json").read_text(encoding="utf-8"))
    rows = manifest["rows"]
    folder = work / "batch" / out["output_folder"]
    (folder / rows[0]["save_as"]).write_bytes(synthetic_pdf([title_page(title=rows[0]["titles"][0], authors=["First Synthetic"], doi=rows[0]["export_doi"])]))
    (folder / rows[1]["save_as"]).write_bytes(html_challenge())
    result = work / "pbd_results.csv"
    result.write_bytes(pbd_write_results([result_row(rows[0], "Completed"), result_row(rows[1], "Completed"), result_row(rows[2], "LoginRequired"),
                                          result_row(rows[3], "Failed", deferred_reason="Permanent page-not-found response")]))
    imp = ["bridge", "import", "--data-root", kb, "--batch-id", batch, "--result-csv", str(result), "--pdf-folder", str(folder), "--operator", "smoke.op"]
    out = rkb(*imp, "--manifest", str(work / "batch" / "bridge_manifest.json"), expect=4)
    lines.append(f"  outcome={out['outcome']} rows={json.dumps(out['counts']['row_outcomes'], sort_keys=True)} anomalies={json.dumps(out['counts']['anomalies'])}")
    out = rkb(*imp)
    lines.append(f"  repeat outcome={out['outcome']}")
    bad = work / "pbd_results_semicolon.csv"
    bad.write_bytes(result.read_bytes().replace(b",", b";", 21))
    out = rkb(*imp[:7], str(bad), *imp[8:], expect=2)
    lines.append(f"  error={out['error']}")
    out = rkb("bridge", "status", "--data-root", kb, "--batch-id", batch)
    lines.append(f"  state={out['state']} counts={json.dumps(out['counts'], sort_keys=True)} identity={[r['identity_states'] for r in out['rows']]}")
    out = rkb("bridge", "close", "--data-root", kb, "--batch-id", batch, "--operator", "smoke.op", expect=2)
    lines.append(f"  error={out['error']}")
    out = rkb("bridge", "close", "--data-root", kb, "--batch-id", batch, "--operator", "smoke.op", "--reason", "OPERATOR_CLOSE_WITH_OPEN_ROWS")
    lines.append(f"  state={out['state']} open_rows={len(out['open_rows'])}")
    out = rkb("doctor", "--data-root", kb, "--full")
    lines.append(f"  ok={out['ok']} problems={out['problems']} bridge_invariants={json.dumps(out['bridge_invariants'], sort_keys=True)}")
    out = rkb("backup", "--data-root", kb, "--target", str(work / "operator-backups"))
    backup = out["backup_dir"]
    out = rkb("verify-backup", "--backup", backup)
    lines.append(f"  valid={out['valid']} schema={out['kb_schema_version']} cas_referenced={out['cas_referenced_count']}")
    out = rkb("restore", "--backup", backup, "--data-root", str(work / "restored"))
    lines.append(f"  comparison={json.dumps(out['comparison'], sort_keys=True)}")
    out = rkb("bridge", "status", "--data-root", str(work / "restored"), "--batch-id", batch)
    lines.append(f"  restored state={out['state']}")
    out = rkb("bridge", "import", "--data-root", kb, "--batch-id", batch, "--result-csv", str(result), "--pdf-folder", "C:relative", "--operator", "smoke.op",
              expect=2)
    lines.append(f"  error={out['error']}")
    Path(args.out).write_text("# S04 installed-CLI smoke (synthetic data only; paths shown as <WORK>)\n" + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
