"""Request-level log of a fault-injected simulated crawl (evidence for retry, replay, credential and termination behaviour).

Scenario: the JISA-shaped data cut to 600 Crossref / ~600 OpenAlex records, with injected faults:
Crossref page 1: 429 (Retry-After 7) then 500 then OK; page 2: timeout then OK; a crash at page 4; resume after every
cursor expired (HTTP 400 -> replay); OpenAlex first without a key (deep page not sent -> BLOCKED), then with the key.
Writes <out>/api_simulation_log.jsonl (one line per provider request as the simulator saw it, credential shown only as
present/absent) and <out>/api_simulation_runs.json (pages, events, sleeps, final states).
Usage:  python -I tools/s04j_api_simulation_log.py <empty out dir>
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def main(argv) -> int:
    from tests import netguard
    netguard.install()
    from tests.journal_helpers import JISA, TEST_KEY, small_scenario
    from scivetra_kb.core.db import init_kb, open_reader, open_writer
    from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
    from scivetra_kb.journal.access import Credentials, Secret
    from scivetra_kb.journal.harvest import JournalHarvester
    from scivetra_kb.journal.reconcile import Reconciler
    from scivetra_kb.journal.simulator import SimulatedCrash, SimulatedProviders
    out = Path(argv[0]).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("out dir must be absent or empty")
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="s04j-apisim-"))
    log, sleeps = [], []
    try:
        clock = FixedClock()
        ids = SequentialIdProvider(clock)
        root = str(tmp / "kb")
        init_kb(root, clock=clock, ids=ids)
        data = small_scenario(600)
        data["faults"] = [
            {"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [
                {"status": 429, "headers": {"Retry-After": "7", "content-type": "application/json"}}, {"status": 500}]},
            {"provider": "CROSSREF", "works_key": "JISA", "page_index": 2, "responses": [{"kind": "timeout"}]},
            {"provider": "CROSSREF", "works_key": "JISA", "page_index": 4, "responses": [{"kind": "crash"}]},
        ]

        class Logged(SimulatedProviders):
            def send(self, request):
                entry = {"seq": len(log) + 1, "process": self.process, "provider": request.provider, "purpose": request.purpose,
                         "url": request.url, "credential": "present" if getattr(request, "credential", None) else "absent",
                         "user_agent": request.headers.get("User-Agent")}
                try:
                    response = super().send(request)
                    entry.update(status=response.status, bytes=len(response.body), retry_after=response.header("retry-after"))
                    return response
                except Exception as exc:          # transport timeout or simulated crash: logged, then re-raised unchanged
                    entry.update(status=None, exception=getattr(exc, "code", type(exc).__name__))
                    raise
                finally:
                    log.append(entry)

        def harvester(kb, sim, key):
            return JournalHarvester(kb, sim, operator="apisim", credentials=Credentials(Secret(TEST_KEY) if key else None),
                                    sleeper=sleeps.append, jitter=lambda: 0.0)

        first = Logged(data)
        first.process = "process-1"
        with open_writer(root, clock=clock, ids=ids) as kb:
            h = harvester(kb, first, key=False)
            jid = h.register_journal("JISA-shaped API simulation", JISA)["journal_id"]
            h.resolve(jid)
            try:
                h.harvest(jid, "CROSSREF", page_size=100)
                crashed = None
            except SimulatedCrash:
                crashed = h.current_run_id
        second = Logged(dict(data, faults=[]))
        second.process = "process-2-after-restart"
        second.expire_all_cursors()
        with open_writer(root, clock=clock, ids=ids) as kb:
            h = harvester(kb, second, key=False)
            resumed = h.resume(crashed)
            no_key = h.harvest(jid, "OPENALEX", page_size=100)
            h = harvester(kb, second, key=True)
            with_key = h.harvest(jid, "OPENALEX", page_size=100)
            version = Reconciler(kb, operator="apisim").reconcile(jid)
        with open_reader(root) as kb:
            runs = {}
            for run in kb.all("SELECT run_id, provider, mode, query_json FROM jh_run ORDER BY rowid"):
                runs[run["run_id"]] = {
                    "provider": run["provider"], "mode": run["mode"], "query": json.loads(run["query_json"]),
                    "events": [{k: e[k] for k in ("seq", "kind", "state", "stop_reason")} | {"detail": json.loads(e["detail_json"])}
                               for e in kb.all("SELECT * FROM jh_run_event WHERE run_id=? ORDER BY seq", (run["run_id"],))],
                    "pages": [{k: p[k] for k in ("seq", "pass_no", "kind", "outcome", "outcome_reason", "http_status", "attempts", "items_returned",
                                                 "items_new", "response_bytes_sha256")}
                              for p in kb.all("SELECT * FROM jh_page WHERE run_id=? ORDER BY seq", (run["run_id"],))]}
        report = {"schema": "rkb-s04j-api-simulation/1", "sleeps_seconds": sleeps, "crashed_run_id": crashed,
                  "final": {"crossref_resumed": {k: resumed[k] for k in ("state", "stop_reason", "counters")},
                            "openalex_without_key": {k: no_key[k] for k in ("state", "stop_reason")},
                            "openalex_with_key": {k: with_key[k] for k in ("state", "stop_reason")},
                            "collection_state": version["collection_state"], "member_count": version["member_count"]},
                  "runs": runs, "request_count": len(log), "key_never_logged": TEST_KEY not in json.dumps(log)}
        report["ok"] = (resumed["state"] == "COMPLETE" and resumed["counters"]["replays"] == 1 and no_key["stop_reason"] == "API_KEY_REQUIRED_FOR_DEEP_PAGING"
                        and with_key["state"] == "COMPLETE" and sleeps[:3] == [7.0, 4.0, 2.0] and report["key_never_logged"])
        (out / "api_simulation_log.jsonl").write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in log), encoding="utf-8", newline="\n")
        (out / "api_simulation_runs.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps({"ok": report["ok"], "requests": len(log), "sleeps": sleeps, "final": report["final"]}))
        return 0 if report["ok"] else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
