"""S04J v02 (architect finding S04J-F01): every verified Crossref ISSN of a journal is queried, with per-ISSN provenance,
per-ISSN completion state, per-ISSN resumability, and a correct DOI union."""
import copy
import csv
import io
import json
import os

from tests.journal_helpers import JISA, KBTest, Reconciler, SimulatedProviders, export_version, harvester, members, small_scenario
from scivetra_kb.journal.simulator import SimulatedCrash

PRINT, ELEC = JISA
EXCLUSIVE = "10.5555/s04j-v02-exclusive-eissn"


def two_inventories(n_shared=3, n_exclusive=1, *, faults=(), shared_on_eissn=False) -> dict:
    """Print ISSN -> works list JISA (first n_shared articles); electronic ISSN -> its OWN works list with n_exclusive records
    (plus, optionally, the shared ones too)."""
    data = small_scenario(n_shared, n_shared)
    data["crossref_journals"][ELEC]["works_key"] = "EISSN"
    extra = []
    for i in range(n_exclusive):
        item = copy.deepcopy(data["crossref_works"]["JISA"][0])
        item["DOI"] = EXCLUSIVE if i == 0 else f"{EXCLUSIVE}-{i}"
        item["title"] = [f"Synthetic record indexed only through the electronic ISSN {i}"]
        extra.append(item)
    data["crossref_works"]["EISSN"] = (copy.deepcopy(data["crossref_works"]["JISA"]) if shared_on_eissn else []) + extra
    data["faults"] = list(faults)
    return data


def works_paths(sim):
    return [c.url.split("?")[0].rsplit("/journals/", 1)[1] for c in sim.calls if "/journals/" in c.url and "/works" in c.url]


class PlanTests(KBTest):
    def test_plan_is_deterministic_and_evidence_backed(self):
        """IDs: S04J-F01-PLAN, JH-04, JH-05. The Crossref query plan lists every requested ISSN the Crossref journal lookup resolved plus every sibling ISSN a resolved journal record lists, sorted, each with its evidence (basis, journal record, lookup page). Registering only the print ISSN still plans the electronic ISSN (LISTED_BY_RESOLVED_JOURNAL_RECORD). The plan is stored in the run's query."""
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(small_scenario(3)))
            jid = h.register_journal("print only", [PRINT])["journal_id"]
            h.resolve(jid)
            plan = h.crossref_issn_plan(jid)
            self.assertEqual([e["issn"] for e in plan["plan"]], [PRINT, ELEC])
            self.assertEqual([b["basis"] for b in plan["plan"][0]["evidence"]], ["REQUESTED_AND_RESOLVED"])
            self.assertEqual([(b["basis"], b["journal_record_of"]) for b in plan["plan"][1]["evidence"]], [("LISTED_BY_RESOLVED_JOURNAL_RECORD", PRINT)])
            self.assertTrue(all(kb.one("SELECT kind FROM jh_page WHERE page_id=?", (b["page_id"],))["kind"] == "JOURNAL_LOOKUP"
                                for e in plan["plan"] for b in e["evidence"]))
            self.assertEqual(plan["requested_not_resolved"], [])
            run = h.harvest(jid, "CROSSREF", page_size=2)
            query = json.loads(kb.one("SELECT query_json FROM jh_run WHERE run_id=?", (run["run_id"],))["query_json"])
            self.assertEqual(([e["issn"] for e in query["issn_plan"]], query["inventory_complete_rule"]),
                             ([PRINT, ELEC], "EVERY_PLANNED_ISSN_QUERY_TERMINATED_AND_NO_REQUESTED_ISSN_UNRESOLVED"))
            self.assertNotIn("issn", query)

    def test_unresolved_requested_issn_prevents_complete(self):
        """IDs: S04J-F01-UNRESOLVED, JH-20, JH-34. A requested ISSN that Crossref did not resolve (and that no resolved record lists) is not silently dropped: the resolved ISSN is crawled, but the Crossref inventory is PARTIAL with REQUESTED_ISSN_NOT_RESOLVED:<issn> and the coverage lists it."""
        data = small_scenario(3)
        data["crossref_journals"] = {PRINT: dict(data["crossref_journals"][PRINT], issn_type=[{"value": PRINT, "type": "print"}])}
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            jid = h.register_journal("one unresolved", JISA)["journal_id"]
            h.resolve(jid)
            out = h.harvest(jid, "CROSSREF", page_size=2)
            self.assertEqual((out["state"], out["stop_reason"]), ("PARTIAL", f"REQUESTED_ISSN_NOT_RESOLVED:{ELEC}"))
            self.assertEqual(out["counters"]["issns_requested_not_resolved"], [ELEC])
            h.harvest(jid, "OPENALEX", page_size=10)
            cov = Reconciler(kb, operator="tester").reconcile(jid)["coverage"]
            self.assertEqual(cov["collection_state"], "PARTIAL")
            self.assertEqual(cov["crossref_issn_queries"]["requested_not_resolved"], [ELEC])


class InventoryTests(KBTest):
    def _run(self, data, **kw):
        sim = SimulatedProviders(data)
        with self.writer() as kb:
            h = harvester(kb, sim, **kw)
            jid = h.register_journal("two ISSNs", JISA)["journal_id"]
            h.resolve(jid)
            cr = h.harvest(jid, "CROSSREF", page_size=2)
            h.harvest(jid, "OPENALEX", page_size=10)
            version = Reconciler(kb, operator="tester").reconcile(jid)
            m = members(kb, version["version_id"])
            out = export_version(kb, version["version_id"], os.path.join(self.tmp, "exp"), operator="tester")
            ev = {k: json.loads(v["evidence_json"]) for k, v in m.items()}
        rows = list(csv.DictReader(open(os.path.join(self.tmp, "exp", out["folder_name"], "journal_works.csv"), encoding="utf-8", newline="")))
        return sim, cr, version, m, ev, {r["subject_id"]: r for r in rows}

    def test_distinct_inventories_are_unioned_with_per_issn_provenance(self):
        """IDs: S04J-F01-DISTINCT, JH-06, JH-15, JH-23. Print ISSN 3 DOIs, electronic ISSN 1 different DOI: both works queries are sent, the Crossref inventory is COMPLETE (ALL_PLANNED_ISSN_QUERIES_TERMINATED), the union has 4 DOI subjects, the exclusive DOI's projection, evidence and CSV row name only the electronic ISSN query, and the per-ISSN coverage table shows each query's rows and unique DOIs separately from the union."""
        sim, cr, version, m, ev, rows = self._run(two_inventories())
        self.assertEqual(sorted(set(works_paths(sim))), [f"{PRINT}/works", f"{ELEC}/works"])
        self.assertEqual((cr["state"], cr["stop_reason"]), ("COMPLETE", "ALL_PLANNED_ISSN_QUERIES_TERMINATED"))
        q = cr["counters"]["issn_queries"]
        self.assertEqual({i: (q[i]["state"], q[i]["rows_fetched"], q[i]["unique_valid_doi_keys"]) for i in q},
                         {PRINT: ("COMPLETE", 3, 3), ELEC: ("COMPLETE", 1, 1)})
        self.assertEqual((cr["counters"]["unique_valid_doi_keys"], cr["counters"]["doi_keys_in_more_than_one_issn_query"]), (4, 0))
        crossref_subjects = {k for k, v in m.items() if "CROSSREF" in v["providers"]}
        self.assertEqual(len(crossref_subjects), 4)
        self.assertEqual(m[EXCLUSIVE]["projection"]["crossref_query_issns"], [ELEC])
        self.assertEqual(list(ev[EXCLUSIVE]["crossref_observations_by_issn"]), [ELEC])
        self.assertEqual(rows[EXCLUSIVE]["crossref_query_issns"], ELEC)
        rec = version["coverage"]["reconciliation"]
        self.assertEqual(rec["crossref_dois_by_issn_query_set"], {PRINT: 3, ELEC: 1})
        self.assertEqual((rec["crossref_rows_fetched_all_issn_queries"], rec["crossref_unique_dois"]), (4, 4))
        table = version["coverage"]["crossref_issn_queries"]
        self.assertEqual((table["plan"], sorted(table["queries"])), ([PRINT, ELEC], [PRINT, ELEC]))
        self.assertTrue(all(table["queries"][i]["reported_counts"] for i in JISA))

    def test_shared_inventory_is_one_subject_per_doi_with_both_sources(self):
        """IDs: S04J-F01-SHARED, JH-14, JH-19. Both ISSN queries return the same 3 records: 6 rows are fetched and 6 observations kept (one per ISSN query, each pointing at its own raw page), but the union is 3 DOI subjects (never 6); every subject's evidence lists one observation per ISSN, its projection and CSV row name both ISSNs, and no disagreement is flagged."""
        data = two_inventories(n_exclusive=0, shared_on_eissn=True)
        sim, cr, version, m, ev, rows = self._run(data)
        self.assertEqual(cr["state"], "COMPLETE")
        self.assertEqual((cr["counters"]["items_returned"], cr["counters"]["observations_stored"], cr["counters"]["unique_valid_doi_keys"]), (6, 6, 3))
        self.assertEqual(cr["counters"]["doi_keys_in_more_than_one_issn_query"], 3)
        self.assertEqual(cr["counters"]["records_changed_within_run"], 0)
        crossref = {k: v for k, v in m.items() if "CROSSREF" in v["providers"]}
        self.assertEqual(len(crossref), 3)
        for key in crossref:
            self.assertEqual(m[key]["projection"]["crossref_query_issns"], JISA)
            self.assertEqual(sorted(ev[key]["crossref_observations_by_issn"]), JISA)
            pages = {ev_id: None for ev_id in ev[key]["crossref_observations"]}
            self.assertEqual(len(pages), 2)
            self.assertEqual(rows[key]["crossref_query_issns"], f"{PRINT}; {ELEC}")
            self.assertNotIn("CROSSREF_ISSN_QUERIES_DISAGREE", m[key]["flags"])
        self.assertEqual(version["coverage"]["reconciliation"]["crossref_dois_by_issn_query_set"], {f"{PRINT}+{ELEC}": 3})

    def test_issn_queries_that_disagree_are_flagged_not_merged_silently(self):
        """IDs: S04J-F01-DISAGREE, JH-17. If the two ISSN queries return one DOI with different metadata, the subject stays one DOI subject, is flagged CROSSREF_ISSN_QUERIES_DISAGREE, and a FIELD_CONFLICT review task names both observations."""
        data = two_inventories(n_exclusive=0, shared_on_eissn=True)
        data["crossref_works"]["EISSN"][0]["title"] = ["A different title returned through the electronic ISSN"]
        sim, cr, version, m, ev, rows = self._run(data)
        key = data["crossref_works"]["JISA"][0]["DOI"]
        self.assertIn("CROSSREF_ISSN_QUERIES_DISAGREE", m[key]["flags"])
        with self.reader() as kb:
            ctx = [json.loads(r["context_json"]) for r in kb.all("SELECT context_json FROM jh_review_task WHERE trigger='FIELD_CONFLICT' AND subject=?", (key,))]
        self.assertEqual([sorted(c["issn_records"]) for c in ctx if c.get("kind") == "CROSSREF_ISSN_QUERIES_DISAGREE"], [JISA])


class FailureTests(KBTest):
    def _harvest(self, data, **kw):
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data), **kw)
            jid = h.register_journal("two ISSNs", JISA)["journal_id"]
            h.resolve(jid)
            out = h.harvest(jid, "CROSSREF", page_size=2)
            failed = kb.all("SELECT outcome, outcome_reason FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' AND outcome<>'OK' "
                            "AND json_extract(request_identity_json,'$.issn')=?", (out["run_id"], ELEC))
            h.harvest(jid, "OPENALEX", page_size=10)
            version = Reconciler(kb, operator="tester").reconcile(jid)
        return out, failed, version

    def test_secondary_issn_failures_are_never_complete(self):
        """IDs: S04J-F01-FAILURE, JH-10, JH-20, JH-34. The print query completes; the electronic query then fails by 404 (journal gone), four 5xx, four timeouts, three 429 (provider paused) or an unparseable page. Every time: the Crossref inventory is PARTIAL with ISSN_QUERY_<state>:2214-2134:<reason>, the per-ISSN table keeps print COMPLETE with its rows and records the electronic state and stop reason, the failed request is a WORKS_PAGE row carrying the electronic ISSN, and the collection is PARTIAL."""
        e_fault = lambda responses: [{"provider": "CROSSREF", "works_key": "EISSN", "page_index": 0, "responses": responses}]  # noqa: E731
        cases = [
            ("404", None, "BLOCKED", "HTTP_404_NOT_FOUND", "NOT_FOUND"),
            ("5xx", e_fault([{"status": 503}] * 4), "PARTIAL", "TRANSIENT_ERROR_RETRIES_EXHAUSTED_HTTP_503", "HTTP_ERROR"),
            ("timeout", e_fault([{"kind": "timeout"}] * 4), "PARTIAL", "TRANSIENT_ERROR_RETRIES_EXHAUSTED_TIMEOUT", "TRANSPORT_FAILED"),
            ("429", e_fault([{"status": 429}] * 3), "PARTIAL", "RATE_LIMITED_PROVIDER_PAUSED", "RATE_LIMITED"),
            ("bad_json", e_fault([{"kind": "bad_json"}]), "PARTIAL", "PAGE_UNPARSEABLE_UNPARSEABLE_RESPONSE", "UNPARSEABLE"),
        ]
        for name, faults, branch_state, reason, outcome in cases:
            with self.subTest(name):
                self.tearDown()
                self.setUp()
                data = two_inventories(n_exclusive=2, faults=faults or ())
                if name == "404":
                    data["crossref_journals"][ELEC]["works_key"] = "MISSING"
                    data["crossref_works"].pop("EISSN")
                    sim_data = data
                else:
                    sim_data = data
                if name == "404":
                    # the electronic record resolves, then its works endpoint answers 404 (as in the architect's repro)
                    from tests.journal_helpers import harvester as hv
                    with self.writer() as kb:
                        sim = SimulatedProviders(two_inventories(n_exclusive=2))
                        h = hv(kb, sim)
                        jid = h.register_journal("two ISSNs", JISA)["journal_id"]
                        h.resolve(jid)
                        sim.crossref_journals.pop(ELEC)
                        out = h.harvest(jid, "CROSSREF", page_size=2)
                        failed = kb.all("SELECT outcome, outcome_reason FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' AND outcome<>'OK' "
                                        "AND json_extract(request_identity_json,'$.issn')=?", (out["run_id"], ELEC))
                        h.harvest(jid, "OPENALEX", page_size=10)
                        version = Reconciler(kb, operator="tester").reconcile(jid)
                else:
                    out, failed, version = self._harvest(sim_data)
                self.assertEqual(out["state"], "PARTIAL")
                self.assertEqual(out["stop_reason"], f"ISSN_QUERY_{branch_state}:{ELEC}:{reason}")
                q = out["counters"]["issn_queries"]
                self.assertEqual((q[PRINT]["state"], q[PRINT]["rows_fetched"]), ("COMPLETE", 3))
                self.assertEqual((q[ELEC]["state"], q[ELEC]["stop_reason"]), (branch_state, reason))
                self.assertEqual([f["outcome"] for f in failed][-1:], [outcome])
                self.assertEqual(version["collection_state"], "PARTIAL")
                self.assertFalse(version["coverage"]["crawl_complete"])
                self.assertEqual(version["coverage"]["crossref_issn_queries"]["queries"][ELEC]["state"], branch_state)

    def test_all_issn_queries_blocked_is_blocked_and_403_stops_the_run(self):
        """IDs: S04J-F01-BLOCKED, JH-34. When both ISSN works endpoints answer 404 the Crossref inventory is BLOCKED; a 403 on the first ISSN stops the whole run (the provider refused access) and the second ISSN is recorded as NOT_ATTEMPTED, never as complete."""
        with self.writer() as kb:
            sim = SimulatedProviders(two_inventories())
            h = harvester(kb, sim)
            jid = h.register_journal("both gone", JISA)["journal_id"]
            h.resolve(jid)
            sim.crossref_journals.clear()
            out = h.harvest(jid, "CROSSREF", page_size=2)
            self.assertEqual(out["state"], "BLOCKED")
            self.assertEqual(sorted(v["state"] for v in out["counters"]["issn_queries"].values()), ["BLOCKED", "BLOCKED"])
        self.tearDown()
        self.setUp()
        data = two_inventories(faults=[{"provider": "CROSSREF", "works_key": "JISA", "page_index": 0, "responses": [{"status": 403}]}])
        with self.writer() as kb:
            sim = SimulatedProviders(data)
            h = harvester(kb, sim)
            jid = h.register_journal("forbidden", JISA)["journal_id"]
            h.resolve(jid)
            out = h.harvest(jid, "CROSSREF", page_size=2)
            q = out["counters"]["issn_queries"]
            self.assertEqual(out["state"], "BLOCKED")
            self.assertEqual((q[PRINT]["state"], q[PRINT]["stop_reason"]), ("BLOCKED", "HTTP_403_ACCESS_DENIED"))
            self.assertEqual((q[ELEC]["state"], q[ELEC]["stop_reason"]), ("BLOCKED", "NOT_ATTEMPTED_HTTP_403_ACCESS_DENIED"))
            self.assertNotIn(f"{ELEC}/works", works_paths(sim))


class ResumeTests(KBTest):
    def _crash_in_second(self, data):
        crash = copy.deepcopy(data)
        crash["faults"] = [{"provider": "CROSSREF", "works_key": "EISSN", "page_index": 1, "responses": [{"kind": "crash"}]}]
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(crash))
            jid = h.register_journal("two ISSNs", JISA)["journal_id"]
            h.resolve(jid)
            with self.assertRaises(SimulatedCrash):
                h.harvest(jid, "CROSSREF", page_size=2)
            run_id = h.current_run_id
            before = kb.all("SELECT seq, json_extract(request_identity_json,'$.issn') issn, outcome FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' ORDER BY seq",
                            (run_id,))
            self.assertEqual(h.status(run_id)["state"], "UNFINISHED")
        return jid, run_id, before

    def test_crash_during_second_issn_then_resume_continues_only_that_query(self):
        """IDs: S04J-F01-RESUME, JH-07, JH-09. The process dies while the electronic ISSN query is on its second page. The print query's committed pages and observations are kept; 'resume' sends NO further print works request (that query already terminated), continues the electronic query from its own checkpoint, finishes COMPLETE, and the final DOI set equals an uninterrupted run with no duplicate observation."""
        data = two_inventories(n_exclusive=5)
        jid, run_id, before = self._crash_in_second(data)
        self.assertEqual([(p["issn"], p["outcome"]) for p in before], [(PRINT, "OK"), (PRINT, "OK"), (ELEC, "OK")])
        with self.writer() as kb:
            sim = SimulatedProviders(data)
            h = harvester(kb, sim)
            out = h.resume(run_id)
            self.assertEqual((out["state"], out["stop_reason"]), ("COMPLETE", "ALL_PLANNED_ISSN_QUERIES_TERMINATED"))
            self.assertEqual(out["counters"]["issn_queries"][PRINT]["stop_reason"], "SHORT_PAGE_BEFORE_INTERRUPTION")
            self.assertEqual([p for p in works_paths(sim) if p.startswith(PRINT)], [])
            self.assertTrue(all(c.url.split("cursor=")[1] != "%2A" for c in sim.calls if f"{ELEC}/works" in c.url and "cursor=" in c.url))
            keys = [r["doi_key"] for r in kb.all("SELECT doi_key FROM jh_observation WHERE run_id=?", (run_id,))]
            self.assertEqual(len(keys), 8)
            self.assertEqual(len(set(keys)), 8)
            dupes = kb.all("SELECT semantic_sha256, count(*) n FROM jh_observation GROUP BY semantic_sha256 HAVING n > 1")
            self.assertEqual(dupes, [])
        self.tearDown()
        self.setUp()
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            j2 = h.register_journal("two ISSNs", JISA)["journal_id"]
            h.resolve(j2)
            clean = h.harvest(j2, "CROSSREF", page_size=2)
            self.assertEqual({r["doi_key"] for r in kb.all("SELECT doi_key FROM jh_observation WHERE run_id=?", (clean["run_id"],))}, set(keys))

    def test_expired_cursor_after_restart_replays_only_the_unfinished_issn(self):
        """IDs: S04J-F01-REPLAY, JH-07. After the crash in the electronic query, every saved cursor is expired: the electronic query gets HTTP 400, records CURSOR_INVALID and REPLAY_STARTED for ISSN 2214-2134 only, re-reads that query from cursor=*, de-duplicates its already-committed page, and completes; the print query is not replayed."""
        data = two_inventories(n_exclusive=5)
        jid, run_id, before = self._crash_in_second(data)
        with self.writer() as kb:
            sim = SimulatedProviders(data)
            sim.expire_all_cursors()
            h = harvester(kb, sim)
            out = h.resume(run_id)
            self.assertEqual(out["state"], "COMPLETE")
            q = out["counters"]["issn_queries"]
            self.assertEqual((q[PRINT]["replays"], q[ELEC]["replays"]), (0, 1))
            events = kb.all("SELECT kind, json_extract(detail_json,'$.issn') issn FROM jh_run_event WHERE run_id=? AND kind IN "
                            "('CURSOR_INVALID','REPLAY_STARTED') ORDER BY seq", (run_id,))
            self.assertEqual(events, [{"kind": "CURSOR_INVALID", "issn": ELEC}, {"kind": "REPLAY_STARTED", "issn": ELEC}])
            self.assertEqual(q[ELEC]["observations_stored"], 5)
            self.assertEqual(q[ELEC]["rows_fetched"], 7)
            self.assertEqual([p for p in works_paths(sim) if p.startswith(PRINT)], [])

    def test_repeated_runs_are_idempotent_and_keep_history(self):
        """IDs: S04J-F01-IDEMPOTENT, JH-09, JH-29. Two complete multi-ISSN runs: reconciling twice is a no-op; the second run gives a new version with exactly the same subject set; within each run every (ISSN query, record) is stored once; earlier versions and observations are untouched."""
        data = two_inventories(n_exclusive=2, shared_on_eissn=True)
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            jid = h.register_journal("two ISSNs", JISA)["journal_id"]
            h.resolve(jid)
            h.harvest(jid, "CROSSREF", page_size=2)
            h.harvest(jid, "OPENALEX", page_size=10)
            rec = Reconciler(kb, operator="tester")
            v1 = rec.reconcile(jid)
            self.assertFalse(rec.reconcile(jid)["created"])
            obs_before = kb.one("SELECT count(*) n FROM jh_observation")["n"]
            h.harvest(jid, "CROSSREF", page_size=3)
            v2 = rec.reconcile(jid)
            self.assertTrue(v2["created"])
            self.assertEqual(set(members(kb, v1["version_id"])), set(members(kb, v2["version_id"])))
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_member WHERE version_id=?", (v1["version_id"],))["n"], v1["member_count"])
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_observation")["n"], obs_before + 8)
            per = kb.all("SELECT o.run_id, json_extract(p.request_identity_json,'$.issn') q, o.provider_record_id, count(*) n FROM jh_observation o "
                         "JOIN jh_page p ON p.page_id = o.page_id GROUP BY 1, 2, 3 HAVING n > 1")
            self.assertEqual(per, [])

    def test_export_is_deterministic_with_issn_provenance(self):
        """IDs: S04J-F01-EXPORT, JH-23. Exporting the multi-ISSN version twice gives byte-identical files and SHA256SUMS; the provenance sidecar's Crossref references carry query_issn; coverage.json contains the per-ISSN query table."""
        data = two_inventories(n_exclusive=1, shared_on_eissn=True)
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            jid = h.register_journal("two ISSNs", JISA)["journal_id"]
            h.resolve(jid)
            h.harvest(jid, "CROSSREF", page_size=2)
            h.harvest(jid, "OPENALEX", page_size=10)
            v = Reconciler(kb, operator="tester").reconcile(jid)
            a = export_version(kb, v["version_id"], os.path.join(self.tmp, "a"), operator="tester")
            b = export_version(kb, v["version_id"], os.path.join(self.tmp, "b"), operator="tester")
        fa, fb = os.path.join(self.tmp, "a", a["folder_name"]), os.path.join(self.tmp, "b", b["folder_name"])
        for name in sorted(os.listdir(fa)):
            self.assertEqual(open(os.path.join(fa, name), "rb").read(), open(os.path.join(fb, name), "rb").read(), name)
        prov = [json.loads(l) for l in open(os.path.join(fa, "field_provenance.jsonl"), encoding="utf-8")]
        crossref_refs = [ref for p in prov for ref in p["fields"].values() if ref.get("provider") == "CROSSREF"]
        self.assertTrue(crossref_refs and all(ref["query_issn"] in JISA for ref in crossref_refs))
        cov = json.load(open(os.path.join(fa, "coverage.json"), encoding="utf-8"))
        self.assertEqual(sorted(cov["crossref_issn_queries"]["queries"]), JISA)
        rows = list(csv.DictReader(io.StringIO(open(os.path.join(fa, "journal_works.csv"), encoding="utf-8", newline="").read())))
        self.assertIn("crossref_query_issns", rows[0])
