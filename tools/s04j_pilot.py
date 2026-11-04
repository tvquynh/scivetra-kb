"""Synthetic S04J pilot: the JISA-shaped journal and the three-mock-publisher batch, each built TWICE in fresh KBs with a
fixed clock and sequential IDs, to show reproducibility (identical members, coverage and export bytes).

Writes into <out dir>:
  pilot_summary.json                      runs, states, counters, coverage reconciliation, timing, determinism checks
  jisa_shaped/export/...                  the complete export of the JISA-shaped collection version
  jisa_shaped/second_snapshot/...         coverage of a later provider snapshot (history, retraction, not-seen)
  three_publishers/<journal>/export/...   one export per journal of the batch (where a version exists)
  three_publishers/batch_result.json
Usage:  python -I tools/s04j_pilot.py <empty out dir>
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


def strip_ids(value):
    return json.loads(json.dumps(value))


def main(argv) -> int:
    from tests import netguard
    netguard.install()
    from tests.journal_helpers import JISA_DIR, THREE_DIR, TEST_KEY, pipeline
    from scivetra_kb.core.db import init_kb, open_writer
    from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
    from scivetra_kb.journal.access import Credentials, Secret
    from scivetra_kb.journal.export import export_version
    from scivetra_kb.journal.harvest import JournalHarvester
    from scivetra_kb.journal.reconcile import Reconciler
    from scivetra_kb.journal.service import run_batch
    from scivetra_kb.journal.simulator import SimulatedProviders
    out = Path(argv[0]).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("out dir must be absent or empty")
    out.mkdir(parents=True, exist_ok=True)
    summary = {"schema": "rkb-s04j-pilot/1", "python": sys.version.split()[0], "data": "SYNTHETIC ONLY (tests/fixtures/journal_sim)",
               "jisa_shaped": {}, "three_publishers": {}}

    def jisa(tmp: Path, keep: Path | None):
        clock = FixedClock()
        ids = SequentialIdProvider(clock)
        root = tmp / "kb"
        init_kb(str(root), clock=clock, ids=ids)
        tracemalloc.start()
        t0 = time.perf_counter()
        with open_writer(str(root), clock=clock, ids=ids) as kb:
            sim = SimulatedProviders.from_directory(str(JISA_DIR))
            h = JournalHarvester(kb, sim, operator="pilot", credentials=Credentials(Secret(TEST_KEY)), sleeper=lambda s: None, jitter=lambda: 0.0)
            jid = h.register_journal("JISA-shaped synthetic (ISSN 2214-2126 / 2214-2134)", ["2214-2126", "2214-2134"])["journal_id"]
            h.record_owner_count(jid, count=2151, observed_on="2026-11-01", note="Owner-observed Crossref rows=0 total-results; historical only")
            resolution = h.resolve(jid)
            runs = {p: h.harvest(jid, p, page_size=s) for p, s in (("CROSSREF", 200), ("OPENALEX", 100), ("OPENALEX_ALTERNATE", 100))}
            v1 = Reconciler(kb, operator="pilot").reconcile(jid)
            elapsed = round(time.perf_counter() - t0, 2)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            exp = export_version(kb, v1["version_id"], str(tmp / "export"), operator="pilot")
            clock.advance(days=10)
            sim2 = SimulatedProviders.from_directory(str(JISA_DIR), "second_snapshot")
            h2 = JournalHarvester(kb, sim2, operator="pilot", credentials=Credentials(Secret(TEST_KEY)), sleeper=lambda s: None, jitter=lambda: 0.0)
            runs2 = {p: h2.harvest(jid, p, page_size=s) for p, s in (("CROSSREF", 200), ("OPENALEX", 100), ("OPENALEX_ALTERNATE", 100))}
            v2 = Reconciler(kb, operator="pilot").reconcile(jid)
            exp2 = export_version(kb, v2["version_id"], str(tmp / "export2"), operator="pilot")
        if keep is not None:
            shutil.copytree(tmp / "export" / exp["folder_name"], keep / "export" / exp["folder_name"])
            shutil.copytree(tmp / "export2" / exp2["folder_name"], keep / "second_snapshot" / exp2["folder_name"])
        return {"resolution": {"crossref": resolution["crossref"]["state"], "openalex_selected": resolution["openalex"]["selected"]},
                "runs": {p: {k: r[k] for k in ("state", "stop_reason", "counters")} for p, r in runs.items()},
                "version": {k: v1[k] for k in ("version_no", "collection_state", "member_count", "conflicts", "review_tasks_created")},
                "coverage_reconciliation": v1["coverage"]["reconciliation"], "export_folder": exp["folder_name"], "export_files": exp["files"],
                "second_snapshot": {"runs": {p: {k: r[k] for k in ("state", "stop_reason")} for p, r in runs2.items()},
                                    "version": {k: v2[k] for k in ("version_no", "collection_state", "member_count")},
                                    "coverage_reconciliation": v2["coverage"]["reconciliation"], "export_folder": exp2["folder_name"]},
                "timing": {"pipeline_seconds": elapsed, "peak_python_memory_bytes": peak}}

    def three(tmp: Path, keep: Path | None):
        clock = FixedClock()
        ids = SequentialIdProvider(clock)
        root = tmp / "kb"
        init_kb(str(root), clock=clock, ids=ids)
        entries = json.loads((THREE_DIR / "batch.json").read_text(encoding="utf-8"))
        with open_writer(str(root), clock=clock, ids=ids) as kb:
            h = JournalHarvester(kb, SimulatedProviders.from_directory(str(THREE_DIR)), operator="pilot", credentials=Credentials(Secret(TEST_KEY)),
                                 sleeper=lambda s: None, jitter=lambda: 0.0)
            result = run_batch(h, entries, page_sizes={"CROSSREF": 100, "OPENALEX": 100})
            exports = {}
            for j in result["journals"]:
                if "version" in j:
                    e = export_version(kb, j["version"]["version_id"], str(tmp / "exports"), operator="pilot")
                    exports[j["label"]] = e["folder_name"]
                    if keep is not None:
                        shutil.copytree(tmp / "exports" / e["folder_name"], keep / j["label"].split(" (")[0].replace(" ", "_") / "export" / e["folder_name"])
        if keep is not None:
            (keep / "batch_result.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return {"outcomes": {j["label"]: j["outcome"] for j in result["journals"]},
                "runs": {j["label"]: {p: (r["state"], r["stop_reason"]) for p, r in j.get("runs", {}).items()} for j in result["journals"]},
                "errors": {j["label"]: j.get("error") for j in result["journals"] if j.get("error")},
                "coverage_reconciliation": {j["label"]: j["version"]["coverage"]["reconciliation"] for j in result["journals"] if "version" in j},
                "exports": exports}

    for name, fn, keep in (("jisa_shaped", jisa, out / "jisa_shaped"), ("three_publishers", three, out / "three_publishers")):
        first_tmp, second_tmp = Path(tempfile.mkdtemp(prefix="s04j-pilot-a-")), Path(tempfile.mkdtemp(prefix="s04j-pilot-b-"))
        try:
            a = fn(first_tmp, keep)
            b = fn(second_tmp, None)
            exports_a = tree(first_tmp / ("export" if name == "jisa_shaped" else "exports"))
            exports_b = tree(second_tmp / ("export" if name == "jisa_shaped" else "exports"))
            summary[name] = a
            summary[name]["determinism"] = {"summaries_identical": strip_ids({k: v for k, v in a.items() if k != "timing"})
                                            == strip_ids({k: v for k, v in b.items() if k != "timing"}),
                                            "export_bytes_identical": exports_a == exports_b, "files_compared": len(exports_a)}
        finally:
            shutil.rmtree(first_tmp, ignore_errors=True)
            shutil.rmtree(second_tmp, ignore_errors=True)
    summary["ok"] = all(summary[n]["determinism"]["summaries_identical"] and summary[n]["determinism"]["export_bytes_identical"]
                        for n in ("jisa_shaped", "three_publishers"))
    (out / "pilot_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": summary["ok"], "jisa": summary["jisa_shaped"]["version"], "jisa_timing": summary["jisa_shaped"]["timing"],
                      "three": summary["three_publishers"]["outcomes"]}))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
