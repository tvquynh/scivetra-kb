"""S04J test helpers: synthetic provider scenarios, harvesters and a shared, once-built JISA-shaped KB (no network)."""
import atexit
import copy
import json
import os
import shutil
import sqlite3
import tempfile
from functools import lru_cache
from pathlib import Path

from tests.helpers import FIXTURES, KBTest  # noqa: F401  (sys.path setup happens in tests.helpers)
from scivetra_kb.core.db import DB_RELATIVE, _configure, open_reader, open_writer, init_kb
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
from scivetra_kb.core.migrations import _statements, packaged_migrations
from scivetra_kb.journal.access import Credentials, Secret
from scivetra_kb.journal.export import export_version
from scivetra_kb.journal.harvest import JournalHarvester
from scivetra_kb.journal.reconcile import Reconciler
from scivetra_kb.journal.simulator import SimulatedProviders

SIM = FIXTURES / "journal_sim"
JISA_DIR = SIM / "jisa_shaped"
THREE_DIR = SIM / "three_publishers"
# The simulated OpenAlex accepts this synthetic string for deep paging (only its SHA-256 is in the scenario file).
TEST_KEY = "SYNTHETIC-OA-TEST-KEY-not-a-real-credential"
JISA = ["2214-2126", "2214-2134"]
ACCEPTED_MIGRATIONS = {1: "2c07dd3461458695465693a53faaebeded0b7ed850dd275c4e4b93698b71f38b",
                       2: "fdfd11f4ac69a43cf25eae6e08c0407362aee578005b87a7ef96fc0cf8d7b9fe", 3: "094dc7595a1ee97e08a853eda55fa5c2fe2e9c490061e37fc17b5b3158c86a57"}


@lru_cache(maxsize=None)
def _scenario_text(directory: str) -> str:
    return (Path(directory) / "journal_sim.json").read_text(encoding="utf-8")


def scenario(directory=JISA_DIR) -> dict:
    return json.loads(_scenario_text(str(directory)))


def small_scenario(n_cr: int, n_oa: int | None = None, *, rows_extra=None) -> dict:
    """The JISA-shaped scenario cut down to its first articles (fast tests). Faults/mutations/profiles removed."""
    data = scenario()
    data["crossref_works"]["JISA"] = data["crossref_works"]["JISA"][:n_cr] + (rows_extra or [])
    keep = {f"https://doi.org/{i['DOI']}" for i in data["crossref_works"]["JISA"][:n_oa if n_oa is not None else n_cr]}
    data["openalex_works"] = [w for w in data["openalex_works"] if w.get("doi") in keep]
    data["mutations"], data["faults"], data["cursor_expiries"], data["profiles"] = [], [], [], {}
    return data


def single_issn_scenario(n_cr: int, n_oa: int | None = None) -> dict:
    """``small_scenario`` for a journal with ONE ISSN: the Crossref journal record lists only the print ISSN (no sibling), so the
    Crossref plan has exactly one query. Used by the single-query mechanics tests (paging, drift, retries, crash, replay)."""
    data = small_scenario(n_cr, n_oa)
    record = dict(data["crossref_journals"][JISA[0]], issn_type=[{"value": JISA[0], "type": "print"}])
    data["crossref_journals"] = {JISA[0]: record}
    return data


def harvester(kb, sim, *, key=True, operator="tester", **kw) -> JournalHarvester:
    sleeps = kw.pop("sleeps", None)
    return JournalHarvester(kb, sim, operator=operator, credentials=Credentials(Secret(TEST_KEY) if key else None),
                            sleeper=(sleeps.append if sleeps is not None else (lambda s: None)), jitter=lambda: 0.0, **kw)


def pipeline(kb, sim, *, issns=JISA, label="JISA-shaped synthetic", key=True, sizes=(200, 100, 100), alternate=True, reconcile=True, **kw) -> dict:
    h = harvester(kb, sim, key=key, **kw)
    journal = h.register_journal(label, issns)
    jid = journal["journal_id"]
    out = {"journal_id": jid, "harvester": h, "resolution": h.resolve(jid), "runs": {}}
    providers = ("CROSSREF", "OPENALEX") + (("OPENALEX_ALTERNATE",) if alternate else ())
    for provider, size in zip(providers, sizes):
        out["runs"][provider] = h.harvest(jid, provider, page_size=size)
    if reconcile:
        out["version"] = Reconciler(kb, operator="tester").reconcile(jid)
    return out


def members(kb, version_id) -> dict:
    out = {}
    for m in kb.all("SELECT * FROM jh_member WHERE version_id=?", (version_id,)):
        m["projection"] = json.loads(m["projection_json"])
        m["flags"] = json.loads(m["flags_json"])
        m["providers"] = json.loads(m["providers_json"])
        out[m["doi_key"] or m["record_ref"]] = m
    return out


def make_older_kb(root, version: int, *, clock, ids, client="0.4.0.dev2"):
    """A KB at schema ``version`` built from the exact packaged migration bytes 0001..version (S04 v02 = schema 3)."""
    os.makedirs(os.path.join(root, "db"))
    os.makedirs(os.path.join(root, "cas", "sha256"))
    connection = sqlite3.connect(os.path.join(root, DB_RELATIVE), isolation_level=None)
    try:
        _configure(connection)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at_utc TEXT NOT NULL)")
        for v, name, digest, script in packaged_migrations()[:version]:
            for statement in _statements(script):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migration VALUES (?,?,?,?)", (v, name, digest, clock.now_utc_text()))
            connection.execute(f"PRAGMA user_version = {v}")
        connection.execute("INSERT INTO kb_instance VALUES (?,?,?)", (ids.new("kbi"), clock.now_utc_text(), client))
        connection.execute("COMMIT")
    finally:
        connection.close()


_SHARED = {}


def shared_jisa() -> dict:
    """Build the full JISA-shaped pipeline ONCE per test process (read-only use; copy the folder before writing)."""
    if "jisa" not in _SHARED:
        tmp = tempfile.mkdtemp(prefix="rkb-jisa-shared-")
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        root = os.path.join(tmp, "kb")
        clock = FixedClock()
        ids = SequentialIdProvider(clock)
        init_kb(root, clock=clock, ids=ids)
        sim = SimulatedProviders.from_directory(str(JISA_DIR))
        with open_writer(root, clock=clock, ids=ids) as kb:
            out = pipeline(kb, sim, reconcile=False)
            out["harvester"].record_owner_count(out["journal_id"], count=2151, observed_on="2026-11-01",
                                                note="Owner-observed Crossref total-results (rows=0), historical; not an expected value")
            out["version"] = Reconciler(kb, operator="tester").reconcile(out["journal_id"])
            exports = [export_version(kb, out["version"]["version_id"], os.path.join(tmp, f"out{i}"), operator="tester") for i in (1, 2)]
        out.pop("harvester")
        _SHARED["jisa"] = dict(out, tmp=tmp, root=root, clock=clock, ids=ids, sim_calls=[c.url for c in sim.calls], exports=exports)
    return _SHARED["jisa"]


def copy_shared(dest) -> str:
    shared = shared_jisa()
    shutil.copytree(shared["root"], dest)
    return dest


__all__ = ["KBTest", "SIM", "JISA_DIR", "THREE_DIR", "TEST_KEY", "JISA", "scenario", "small_scenario", "single_issn_scenario", "harvester", "pipeline", "members",
           "make_older_kb", "shared_jisa", "copy_shared", "SimulatedProviders", "Reconciler", "export_version", "open_reader", "open_writer",
           "copy", "FixedClock", "SequentialIdProvider"]
