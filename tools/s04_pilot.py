"""Run the synthetic S04 pilot twice (determinism, wall time, peak Python memory) and write paired PBD sample files.

Usage: python -I -B tools/s04_pilot.py --out-dir <empty evidence folder>
Writes pilot_metrics.json and pbd_samples/ (a native-fixture batch: input CSV, manifest, emulated PBD output CSV; synthetic rows only).
"""
import argparse
import json
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from tests import netguard  # noqa: E402

netguard.install()
from tests.bridge_helpers import copy_native_fulltext, pbd_write_results, result_row  # noqa: E402
from tests.bridge_pilot import run_pilot  # noqa: E402
from scivetra_kb.bridge.service import BridgeService  # noqa: E402
from scivetra_kb.core.db import init_kb, open_writer  # noqa: E402
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider  # noqa: E402
from scivetra_kb.library.service import LibraryService  # noqa: E402


def samples(out: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="rkb-samples-"))
    try:
        clock = FixedClock()
        ids = SequentialIdProvider(clock)
        root = tmp / "kb"
        init_kb(str(root), clock=clock, ids=ids)
        corpus, fulltext = copy_native_fulltext(str(tmp))
        with open_writer(str(root), clock=clock, ids=ids) as kb:
            LibraryService(kb).import_release(corpus, workspace_label="samples", operator="sample.op")
            exported = BridgeService(kb).export_batch(source_dir=fulltext, kind="rv-manual-queue", workspace_label="samples",
                                                      out_dir=str(tmp / "batch"), operator="sample.op")
        manifest = json.loads((tmp / "batch" / "bridge_manifest.json").read_text(encoding="utf-8"))
        rows = manifest["rows"]
        statuses = ["Completed", "LoginRequired", "Failed", "Deferred", "NoUrl"]
        result = [result_row(r, s, file_path=("X:\\synthetic-pbd-output\\" + r["save_as"]) if s == "Completed" else "",
                             deferred_reason="Paywall" if s == "Deferred" else "") for r, s in zip(rows, statuses)]
        out.mkdir(parents=True)
        shutil.copyfile(tmp / "batch" / exported["input_csv"], out / exported["input_csv"])
        shutil.copyfile(tmp / "batch" / "bridge_manifest.json", out / "bridge_manifest.json")
        (out / f"pbd_results_{exported['batch_short']}_synthetic.csv").write_bytes(pbd_write_results(result))
        (out / "README.txt").write_text(
            "Synthetic PBD exchange sample (native Reprovane 2.0.1 synthetic fixture; no real papers, URLs on example.org).\n"
            f"{exported['input_csv']}: bridge -> PBD input (UTF-8 BOM, CRLF, 12 columns).\n"
            "bridge_manifest.json: portable batch manifest (no local paths).\n"
            f"pbd_results_{exported['batch_short']}_synthetic.csv: byte-faithful emulation of PBD V5.2.11 WriteResults (22 columns); "
            "file_path values are placeholders. No PDF bytes are included.\n", encoding="utf-8", newline="\n")
        return {"batch_short": exported["batch_short"], "rows": len(rows), "files": sorted(p.name for p in out.iterdir())}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.out_dir
    if out.exists() and any(out.iterdir()):
        raise SystemExit("--out-dir must be absent or empty")
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for _ in range(2):
        tmp = tempfile.mkdtemp(prefix="rkb-pilot-")
        try:
            tracemalloc.start()
            start = time.perf_counter()
            result = run_pilot(tmp)
            elapsed = time.perf_counter() - start
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            runs.append({"result": result, "elapsed_seconds": round(elapsed, 2), "peak_python_memory_bytes": peak})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    metrics = {"schema": "rkb-s04-pilot-metrics/1", "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
               "platform": platform.platform(), "deterministic": runs[0]["result"] == runs[1]["result"], "result": runs[0]["result"],
               "timing": [{k: r[k] for k in ("elapsed_seconds", "peak_python_memory_bytes")} for r in runs],
               "note": "Synthetic data only. Time includes KB creation, Reprovane-shaped import, export, two PBD result rounds, a repeat, "
                       "and the sandboxed G1 child process for each PDF. tracemalloc measures Python allocations of this process, not child processes."}
    metrics["samples"] = samples(out / "pbd_samples")
    (out / "pilot_metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"deterministic": metrics["deterministic"], "timing": metrics["timing"]}))
    return 0 if metrics["deterministic"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
