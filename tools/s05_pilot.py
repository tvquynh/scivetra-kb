"""Synthetic S05 pilot: the 100-candidate TA release of tests/fte_pilot.py, built TWICE in fresh KBs with a fixed clock and
sequential IDs, to show reproducibility (identical summary, queue counts and provisional export bytes).

Writes into <out dir>:
  pilot_summary.json            accounting, reason breakdowns, queue categories, refusals, timing, memory, determinism
  export/<fte_export_...>/      the provisional (unlocked) NON_AUTHORITATIVE export of the first build
Usage:  python -I tools/s05_pilot.py <empty out dir>
"""
import hashlib
import json
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def tree(path: Path) -> dict:
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()}


def main(argv) -> int:
    from tests import netguard
    netguard.install()
    from tests.fte_pilot import EXPECTED, run_fte_pilot
    from scivetra_kb.eligibility.handoff import verify_export
    out = Path(argv[0]).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("out dir must be absent or empty")
    out.mkdir(parents=True, exist_ok=True)
    builds = []
    for n in range(2):
        tmp = Path(tempfile.mkdtemp(prefix="rkb-s05-pilot-"))
        try:
            tracemalloc.start()
            t0 = time.perf_counter()
            result = run_fte_pilot(str(tmp))
            elapsed = round(time.perf_counter() - t0, 2)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            export_dir = Path(result["export_dir"])
            builds.append({"result": result, "elapsed_s_traced": elapsed, "peak_mib": round(peak / 2**20, 1), "tree": tree(export_dir),
                           "verify": verify_export(str(export_dir))})
            if n == 0:
                shutil.copytree(export_dir, out / "export" / export_dir.name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    a, b = builds
    s = a["result"]["summary"]
    summary = {
        "schema": "rkb-s05-pilot/1", "python": sys.version.split()[0], "data": "SYNTHETIC ONLY (tests/fte_pilot.py, DOIs 10.99999/fte.*)",
        "authority": s["authority"], "status": s["status"],
        "ta_candidate_count": s["ta_candidate_count"], "accounting": {k: s[k] for k in EXPECTED}, "expected": EXPECTED,
        "accounting_matches_plan": {k: s[k] for k in EXPECTED} == EXPECTED, "invariant": s["invariant"],
        "ft_exclude_by_reason": s["ft_exclude_by_reason"], "ft_not_retrieved_by_reason": s["ft_not_retrieved_by_reason"],
        "ft_pending_by_reason": s["ft_pending_by_reason"], "final_basis": s["final_basis"], "reports_vs_studies": s["reports_vs_studies"],
        "queue_categories": a["result"]["queue_categories"], "refused_attach": a["result"]["refused_attach"],
        "lock_refusal": a["result"]["lock_refusal"], "export_folder": Path(a["result"]["export_dir"]).name,
        "export_verify": {k: a["verify"][k] for k in ("valid", "state", "authority")},
        "timing_note": "elapsed measured with tracemalloc active (slower than untraced)",
        "builds": [{"elapsed_s_traced": x["elapsed_s_traced"], "peak_python_mib": x["peak_mib"]} for x in builds],
        "determinism": {
            "summary_identical": a["result"]["summary"] == b["result"]["summary"],
            "queue_categories_identical": a["result"]["queue_categories"] == b["result"]["queue_categories"],
            "export_bytes_identical": a["tree"] == b["tree"],
            "export_folder_identical": Path(a["result"]["export_dir"]).name == Path(b["result"]["export_dir"]).name,
        },
    }
    (out / "pilot_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    ok = summary["accounting_matches_plan"] and summary["invariant"]["holds"] and all(summary["determinism"].values()) and a["verify"]["valid"]
    print(json.dumps({"ok": ok, "accounting": summary["accounting"], "determinism": summary["determinism"], "builds": summary["builds"]}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
