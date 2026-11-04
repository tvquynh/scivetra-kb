"""S04J: Crossref cursor crawl, checkpoints, interruption/replay, retries, caps and idempotency (offline simulated providers)."""
import json
import os

from tests.journal_helpers import (JISA, KBTest, Reconciler, SimulatedProviders, harvester, members, scenario, shared_jisa, single_issn_scenario,
                                   small_scenario)
from scivetra_kb.core.db import open_reader
from scivetra_kb.core.errors import ValidationError
from scivetra_kb.journal.simulator import SimulatedCrash


def _journal(kb, sim, **kw):
    # S04J v02: single-query mechanics tests use a one-ISSN journal (the only ISSN Crossref knows in that scenario); two-ISSN
    # behaviour is tested in test_journal_multi_issn.
    h = harvester(kb, sim, **kw)
    jid = h.register_journal("JISA-shaped", sorted(sim.crossref_journals) or JISA)["journal_id"]
    h.resolve(jid)
    return h, jid


class CrossrefPagingTests(KBTest):
    def test_short_and_empty_final_pages_terminate(self):
        """IDs: JH-06, S04J-PAGING-01. rows=7 over 30 records: 5 cursor pages, the last short (2 items) -> COMPLETE/SHORT_PAGE; rows=10 over 30: 3 full pages then an EMPTY_PAGE -> COMPLETE. Every page records cursor_in/cursor_out, items_returned and its raw bytes in CAS; the rows=0 count probe is a separate COUNT_PROBE page."""
        for rows, pages, reason in ((7, 5, "SHORT_PAGE"), (10, 4, "EMPTY_PAGE")):
            with self.subTest(rows=rows):
                self.setUp()
                with self.writer() as kb:
                    h, jid = _journal(kb, SimulatedProviders(single_issn_scenario(30)))
                    out = h.harvest(jid, "CROSSREF", page_size=rows)
                    self.assertEqual((out["state"], out["stop_reason"], out["counters"]["pages_ok"]), ("COMPLETE", reason, pages))
                    self.assertEqual(out["counters"]["observations_stored"], 30)
                    works = kb.all("SELECT * FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' ORDER BY seq", (out["run_id"],))
                    self.assertEqual(works[0]["cursor_in"], "*")
                    for prev, nxt in zip(works, works[1:]):
                        self.assertEqual(nxt["cursor_in"], prev["cursor_out"])
                    self.assertTrue(all(kb.cas.exists(p["response_bytes_sha256"]) for p in works))
                    self.assertEqual(kb.one("SELECT count(*) n FROM jh_page WHERE run_id=? AND kind='COUNT_PROBE'", (out["run_id"],))["n"], 1)
                    query = json.loads(kb.one("SELECT query_json FROM jh_run WHERE run_id=?", (out["run_id"],))["query_json"])
                    self.assertEqual((query["termination"], query["rows"]), ("STOP_WHEN_ITEMS_RETURNED_LT_ROWS", rows))
                self.tearDown()

    def test_total_results_is_only_a_timestamped_observation(self):
        """IDs: JH-08, S04J-COUNT-01. total-results that changes during the crawl is stored as separate timestamped count observations scoped SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION, with a COUNT_DRIFT event; the run is still COMPLETE by its pagination rule (counts are never a completeness criterion), and a count larger than the records fetched does not make it PARTIAL."""
        data = single_issn_scenario(25)
        data["settings"]["crossref_total_drift"] = 7
        with self.writer() as kb:
            h, jid = _journal(kb, SimulatedProviders(data))
            out = h.harvest(jid, "CROSSREF", page_size=10)
            self.assertEqual(out["state"], "COMPLETE")
            self.assertEqual(out["counters"]["reported_counts"], [25, 25, 32])
            self.assertEqual(out["counters"]["count_drift_events"], 1)
            scopes = {r["scope"] for r in kb.all("SELECT scope FROM jh_count_observation")}
            self.assertEqual(scopes, {"SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION"})
            self.assertTrue(all(r["observed_at"] for r in kb.all("SELECT observed_at FROM jh_count_observation")))

    def test_owner_count_2151_is_history_not_expectation(self):
        """IDs: JH-08, S04J-JISA-2151. The owner's JISA observation (2151, 2026-11-01) is stored as OWNER_REPORTED history and reported as such in coverage; the JISA-shaped fixture deliberately has a different size, the run is COMPLETE, and nothing compares the inventory with 2151."""
        shared = shared_jisa()
        cov = shared["version"]["coverage"]
        self.assertEqual([c["value"] for c in cov["owner_reported_counts"]], [2151])
        self.assertEqual(cov["owner_reported_counts"][0]["scope"], "SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION")
        self.assertNotEqual(cov["providers"]["CROSSREF"]["counters"]["observations_stored"], 2151)
        self.assertEqual(cov["providers"]["CROSSREF"]["state"], "COMPLETE")
        text = json.dumps({k: v for k, v in cov.items() if k != "owner_reported_counts"})
        self.assertNotIn("2151", text)

    def test_page_overlap_after_offset_shift_is_deduplicated(self):
        """IDs: JH-07, JH-09, JH-28, S04J-SCALE, S04J-F01-PROVENANCE. The JISA-shaped Crossref inventory is crawled once per planned ISSN (v02). Print query (2214-2126): two records inserted at the head mid-crawl shift the offsets, so the 2 records repeated on the next page are stored once (2228 rows, 2226 observations) and the count drift is recorded; the 2 inserted records are absent from that query. Electronic query (2214-2134), made after the insertion: 2228 rows, 2228 observations, including the 2 inserted records. Union: 2228 DOI keys; 2226 keys returned by both queries."""
        c = shared_jisa()["runs"]["CROSSREF"]["counters"]
        q = c["issn_queries"]
        self.assertEqual(sorted(q), JISA)
        self.assertEqual((q["2214-2126"]["rows_fetched"], q["2214-2126"]["observations_stored"], q["2214-2126"]["unique_valid_doi_keys"]), (2228, 2226, 2226))
        self.assertEqual((q["2214-2134"]["rows_fetched"], q["2214-2134"]["observations_stored"], q["2214-2134"]["unique_valid_doi_keys"]), (2228, 2228, 2228))
        self.assertEqual((c["items_returned"], c["observations_stored"], c["items_deduplicated_within_run"]), (4456, 4454, 2))
        self.assertEqual((c["unique_valid_doi_keys"], c["doi_keys_in_more_than_one_issn_query"]), (2228, 2226))
        self.assertGreaterEqual(c["unique_valid_doi_keys"], 2200)
        self.assertEqual(c["count_drift_events"], 1)
        self.assertEqual(q["2214-2126"]["reported_counts"], [2226, 2226, 2228])
        with open_reader(shared_jisa()["root"]) as kb:
            sources = {r["q"] for r in kb.all("SELECT DISTINCT json_extract(p.request_identity_json,'$.issn') q FROM jh_observation o "
                                               "JOIN jh_page p ON p.page_id = o.page_id WHERE o.doi_key LIKE '%inserted-%'")}
            self.assertEqual(sources, {"2214-2134"})


class InterruptionTests(KBTest):
    def _full(self, data, rows=10):
        with self.writer() as kb:
            h, jid = _journal(kb, SimulatedProviders(data))
            out = h.harvest(jid, "CROSSREF", page_size=rows)
            return {r["doi_key"] for r in kb.all("SELECT doi_key FROM jh_observation WHERE run_id=?", (out["run_id"],))}

    def test_crash_mid_page_then_resume(self):
        """IDs: JH-07, JH-09, JH-20, S04J-RESUME-01. A crash inside the 4th page transaction rolls that page back (no page row, no observations), leaves the run UNFINISHED (never COMPLETE), and 'resume' continues from the last committed cursor: the final DOI set equals an uninterrupted crawl, with no duplicate observations."""
        data = single_issn_scenario(45)
        clean = self._full(data)
        self.tearDown()
        self.setUp()

        def boom(run_id, page_seq, kb):
            if kb.one("SELECT count(*) n FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE'", (run_id,))["n"] == 4:
                raise SimulatedCrash("killed mid-page")

        with self.writer() as kb:
            h, jid = _journal(kb, SimulatedProviders(data), faults={"before_page_commit": boom})
            with self.assertRaises(SimulatedCrash):
                h.harvest(jid, "CROSSREF", page_size=10)
            run_id = h.current_run_id
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE'", (run_id,))["n"], 3)
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_observation WHERE run_id=?", (run_id,))["n"], 30)
            self.assertEqual(h.status(run_id)["state"], "UNFINISHED")
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            out = h.resume(run_id)
            self.assertEqual((out["state"], out["stop_reason"]), ("COMPLETE", "SHORT_PAGE"))
            keys = [r["doi_key"] for r in kb.all("SELECT doi_key FROM jh_observation WHERE run_id=?", (run_id,))]
            self.assertEqual((len(keys), set(keys)), (45, clean))
            kinds = [e["kind"] for e in h.status(run_id)["events"]]
            self.assertEqual(kinds, ["STARTED", "RESUMED", "FINISHED"])
            with self.assertRaises(ValidationError) as ctx:
                h.resume(run_id)
            self.assertEqual(ctx.exception.code, "RUN_ALREADY_FINISHED")

    def test_expired_cursor_on_resume_replays_without_duplicates(self):
        """IDs: JH-07, S04J-REPLAY-01. A process crash (simulator) at page 3, then a resume after every persisted cursor expired: the provider answers HTTP 400, the run records CURSOR_INVALID and REPLAY_STARTED (pass 1), re-reads the same query from cursor=*, de-duplicates the 20 already-committed records by content and finishes COMPLETE with exactly the records of a clean crawl."""
        data = single_issn_scenario(45)
        clean = self._full(data)
        self.tearDown()
        self.setUp()
        crash = json.loads(json.dumps(data))
        crash["faults"] = [{"provider": "CROSSREF", "works_key": "JISA", "page_index": 2, "responses": [{"kind": "crash"}]}]
        with self.writer() as kb:
            sim = SimulatedProviders(crash)
            h, jid = _journal(kb, sim)
            with self.assertRaises(SimulatedCrash):
                h.harvest(jid, "CROSSREF", page_size=10)
            run_id = h.current_run_id
        with self.writer() as kb:
            sim = SimulatedProviders(data)
            sim.expire_all_cursors()
            out = harvester(kb, sim).resume(run_id)
            self.assertEqual(out["state"], "COMPLETE")
            self.assertEqual(out["counters"]["replays"], 1)
            self.assertEqual(out["counters"]["observations_stored"], 45)
            self.assertEqual(out["counters"]["items_deduplicated_within_run"], 20)
            pages = kb.all("SELECT pass_no, outcome FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' ORDER BY seq", (run_id,))
            self.assertEqual(pages[2], {"pass_no": 0, "outcome": "CURSOR_INVALID"})
            self.assertEqual({p["pass_no"] for p in pages[3:]}, {1})
            self.assertEqual({r["doi_key"] for r in kb.all("SELECT doi_key FROM jh_observation WHERE run_id=?", (run_id,))}, clean)
            kinds = [e["kind"] for e in harvester(kb, sim).status(run_id)["events"]]
            self.assertEqual(kinds, ["STARTED", "RESUMED", "CURSOR_INVALID", "REPLAY_STARTED", "FINISHED"])

    def test_replay_limit_gives_partial(self):
        """IDs: JH-07, JH-20. Cursors that keep expiring exhaust the bounded replay budget (max_replays=2): the run ends PARTIAL with CURSOR_INVALID_REPLAY_LIMIT_REACHED, never COMPLETE."""
        data = single_issn_scenario(30)
        data["cursor_expiries"] = [{"provider": "CROSSREF", "page_index": 1, "times": 5}]
        with self.writer() as kb:
            h, jid = _journal(kb, SimulatedProviders(data))
            out = h.harvest(jid, "CROSSREF", page_size=10)
            self.assertEqual((out["state"], out["stop_reason"], out["counters"]["replays"]), ("PARTIAL", "CURSOR_INVALID_REPLAY_LIMIT_REACHED", 2))

    def test_unfinished_run_reported_and_not_used_as_complete(self):
        """IDs: JH-20, JH-34. A crash leaves an unfinished run; reconciling then uses no Crossref basis (NOT_RUN) and lists the unfinished run, and the collection is PARTIAL (OpenAlex COMPLETE) rather than complete."""
        data = single_issn_scenario(30)
        data["faults"] = [{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [{"kind": "crash"}]}]
        with self.writer() as kb:
            h, jid = _journal(kb, SimulatedProviders(data))
            with self.assertRaises(SimulatedCrash):
                h.harvest(jid, "CROSSREF", page_size=10)
            self.assertEqual(h.harvest(jid, "OPENALEX", page_size=10)["state"], "COMPLETE")
            v = Reconciler(kb, operator="tester").reconcile(jid)
            self.assertEqual(v["collection_state"], "PARTIAL")
            basis = json.loads(kb.one("SELECT basis_json FROM jh_collection_version")["basis_json"])
            self.assertEqual(basis["CROSSREF"]["state"], "NOT_RUN")
            self.assertEqual(len(basis["CROSSREF"]["unfinished_run_ids"]), 1)


class RetryTests(KBTest):
    def _run(self, faults, *, caps=None, cancel=None, rows=10, n=30):
        data = single_issn_scenario(n)
        data["faults"] = faults
        sleeps = []
        with self.writer() as kb:
            h, jid = _journal(kb, SimulatedProviders(data), sleeps=sleeps, cancel=cancel)
            out = h.harvest(jid, "CROSSREF", page_size=rows, caps=caps)
            out["reservations"] = kb.one("SELECT count(*) n FROM request_reservation r JOIN jh_run j ON j.fetch_run_id = r.run_id "
                                         "WHERE j.run_id=?", (out["run_id"],))["n"]
            out["pages"] = kb.all("SELECT outcome, outcome_reason, attempts, http_status FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' ORDER BY seq",
                                  (out["run_id"],))
        out["sleeps"] = sleeps
        return out

    def test_429_retry_after_and_5xx_are_retried_with_bounds(self):
        """IDs: JH-10, S04J-RETRY-01. 429 with Retry-After: 7 then 500 then success: the page commits after 3 attempts with deterministic sleeps [7, 4] (Retry-After honoured, exponential base 2**attempt, jitter injected as 0), and every attempt is journalled before sending (request_reservation)."""
        out = self._run([{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1,
                          "responses": [{"status": 429, "headers": {"Retry-After": "7", "content-type": "application/json"}}, {"status": 500}]}])
        self.assertEqual(out["state"], "COMPLETE")
        self.assertEqual(out["sleeps"], [7.0, 4.0])
        self.assertEqual(out["pages"][1]["attempts"], 3)
        self.assertEqual(out["reservations"], 1 + 4 + 2)   # probe + 4 pages (incl. empty final) + 2 retries

    def test_retry_after_is_capped_and_exhaustion_is_partial(self):
        """IDs: JH-10, JH-20. A Retry-After of 120 s is capped at 60 s; three consecutive 429 pause the provider and the run is PARTIAL (RATE_LIMITED_PROVIDER_PAUSED); four timeouts are PARTIAL (TRANSIENT_ERROR_RETRIES_EXHAUSTED_TIMEOUT); four 5xx are PARTIAL; none is ever COMPLETE."""
        limited = self._run([{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [
            {"status": 429, "headers": {"Retry-After": "120"}}, {"status": 429}, {"status": 429}]}])
        self.assertEqual((limited["state"], limited["stop_reason"]), ("PARTIAL", "RATE_LIMITED_PROVIDER_PAUSED"))
        self.assertEqual(limited["sleeps"][0], 60.0)
        self.tearDown()
        self.setUp()
        timeouts = self._run([{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [{"kind": "timeout"}] * 4}])
        self.assertEqual((timeouts["state"], timeouts["stop_reason"]), ("PARTIAL", "TRANSIENT_ERROR_RETRIES_EXHAUSTED_TIMEOUT"))
        self.assertEqual(timeouts["pages"][-1]["outcome"], "TRANSPORT_FAILED")
        self.tearDown()
        self.setUp()
        server = self._run([{"provider": "CROSSREF", "works_key": "JISA", "page_index": 2, "responses": [{"status": 503}] * 4}])
        self.assertEqual((server["state"], server["stop_reason"]), ("PARTIAL", "TRANSIENT_ERROR_RETRIES_EXHAUSTED_HTTP_503"))

    def test_bad_json_403_caps_budget_and_cancellation(self):
        """IDs: JH-10, JH-20, S04J-CAPS. An unparseable page is stored (raw bytes in CAS, outcome UNPARSEABLE) and the run is PARTIAL; HTTP 403 is BLOCKED; a page cap, a request budget and operator cancellation each end PARTIAL with their own stop reason."""
        bad = self._run([{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [{"kind": "bad_json"}]}])
        self.assertEqual((bad["state"], bad["stop_reason"]), ("PARTIAL", "PAGE_UNPARSEABLE_UNPARSEABLE_RESPONSE"))
        self.assertEqual(bad["pages"][-1]["outcome"], "UNPARSEABLE")
        for faults, kw, expected in (
                ([{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [{"status": 403}]}], {}, ("BLOCKED", "HTTP_403_ACCESS_DENIED")),
                ([], {"caps": {"max_pages": 2}}, ("PARTIAL", "PAGE_CAP_REACHED")),
                ([], {"caps": {"max_requests": 3}}, ("PARTIAL", "REQUEST_BUDGET_EXHAUSTED")),
                ([], {"cancel": iter([False, False, True]).__next__}, ("PARTIAL", "CANCELLED_BY_OPERATOR"))):
            self.tearDown()
            self.setUp()
            out = self._run(faults, **kw)
            self.assertEqual((out["state"], out["stop_reason"]), expected)


class IdempotencyTests(KBTest):
    def test_repeated_full_runs_and_reconcile_are_idempotent(self):
        """IDs: JH-09, S04J-IDEMPOTENT. Reconciling twice without new runs is a no-op (same version); a second full Crossref+OpenAlex run creates new runs and a new version with exactly the same subject set and no duplicate subject within any version; within a run a replayed item is never stored twice."""
        with self.writer() as kb:
            sim = SimulatedProviders(small_scenario(40))
            h, jid = _journal(kb, sim)
            for provider in ("CROSSREF", "OPENALEX"):
                h.harvest(jid, provider, page_size=10)
            rec = Reconciler(kb, operator="tester")
            v1 = rec.reconcile(jid)
            again = rec.reconcile(jid)
            self.assertEqual((again["created"], again["version_id"]), (False, v1["version_id"]))
            for provider in ("CROSSREF", "OPENALEX"):
                h.harvest(jid, provider, page_size=7)
            v2 = rec.reconcile(jid)
            self.assertTrue(v2["created"])
            self.assertEqual(set(members(kb, v1["version_id"])), set(members(kb, v2["version_id"])))
            dupes = kb.all("SELECT version_id, doi_key, count(*) n FROM jh_member WHERE doi_key IS NOT NULL GROUP BY version_id, doi_key HAVING n > 1")
            self.assertEqual(dupes, [])
            per_run = kb.all("SELECT run_id, semantic_sha256, count(*) n FROM jh_observation GROUP BY run_id, semantic_sha256 HAVING n > 1")
            self.assertEqual(per_run, [])
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_collection_version")["n"], 2)

    def test_work_types_beyond_journal_article_are_kept(self):
        """IDs: JH-21, S04J-TYPES. The JISA-shaped inventory keeps other, component, peer-review and journal-issue records and the correction/retraction notices as members (all-types policy); none is silently excluded."""
        cov = shared_jisa()["version"]["coverage"]["reconciliation"]["work_types_of_members"]
        for kind, n in (("other", 6), ("component", 4), ("peer-review", 3), ("journal-issue", 2)):
            self.assertEqual(cov.get(kind), n, kind)
        with open_reader(shared_jisa()["root"]) as kb:
            m = members(kb, shared_jisa()["version"]["version_id"])
            for k in (1, 2, 3):
                self.assertEqual(m[f"10.5555/jisa-synthetic.cn-{k}"]["membership_asserted"], 1)
            self.assertEqual(m["10.5555/jisa-synthetic.rn-2"]["projection"]["work_type"], "journal-article")
