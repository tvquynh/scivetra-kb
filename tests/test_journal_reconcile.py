"""S04J: independent OpenAlex inventory, venue vs alternate location, credentials, reconciliation sets, conflicts,
non-destructive history, raw evidence and coverage states."""
import hashlib
import json
import os
import pickle

from tests.journal_helpers import (JISA, JISA_DIR, KBTest, Reconciler, SimulatedProviders, TEST_KEY, copy_shared, harvester, members, scenario,
                                   shared_jisa, small_scenario)
from scivetra_kb.core.db import open_reader, open_writer
from scivetra_kb.core.errors import ImmutableRecordError, ValidationError
from scivetra_kb.journal.access import Credentials, Secret
from scivetra_kb.metadata.doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, parse_doi


def _key(raw):
    return parse_doi(raw, DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD).comparison_key


def _expected_sets():
    """Independent computation from the raw scenario file (not from the harvester)."""
    data = scenario()
    cr = {_key(i["DOI"]) for i in data["crossref_works"]["JISA"] if isinstance(i.get("DOI"), str)}
    # v02: the electronic-ISSN query runs after the scenario's mid-crawl insertion, so it also returns the inserted records.
    cr |= {_key(i["DOI"]) for m in data["mutations"] for i in m.get("insert_front", [])}
    primary, alternate = set(), set()
    for w in data["openalex_works"]:
        if not w.get("doi"):
            continue
        p = parse_doi(w["doi"], DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
        if p.status != "VALID":
            continue
        src = (((w.get("primary_location") or {}).get("source") or {}).get("id") or "").rsplit("/", 1)[-1]
        if src == "S900000001":
            primary.add(p.comparison_key)
        elif any(((l.get("source") or {}).get("id") or "").endswith("S900000001") for l in w.get("locations") or []):
            alternate.add(p.comparison_key)
    return cr, primary, alternate


class OpenAlexTests(KBTest):
    def test_openalex_inventory_is_independent_of_crossref(self):
        """IDs: JH-11, S04J-OA-INDEPENDENT. OpenAlex is enumerated by source filter only (no request names a DOI); its 15 synthetic OpenAlex-only DOIs are retained as OPENALEX_ONLY members with provenance; the OpenAlex crawl works even when it runs before any Crossref crawl."""
        shared = shared_jisa()
        oa_calls = [u for u in shared["sim_calls"] if "api.openalex.org/works" in u]
        self.assertTrue(oa_calls)
        self.assertTrue(all("doi" not in u.split("?", 1)[1].lower() for u in oa_calls))
        self.assertTrue(all("primary_location.source.id%3AS900000001" in u or "locations.source.id%3AS900000001" in u for u in oa_calls))
        with open_reader(shared["root"]) as kb:
            m = members(kb, shared["version"]["version_id"])
            for j in range(1, 16):
                member = m[f"10.5555/jisa-synthetic.oa{j:03d}"]
                self.assertEqual((member["category"], member["membership_asserted"], member["providers"]), ("OPENALEX_ONLY", 1, ["OPENALEX", "OPENALEX_ALTERNATE"]))
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(small_scenario(30)))
            jid = h.register_journal("OA first", JISA)["journal_id"]
            h.resolve(jid)
            self.assertEqual(h.harvest(jid, "OPENALEX", page_size=10)["state"], "COMPLETE")

    def test_reconciliation_sets_match_independent_computation(self):
        """IDs: JH-15, JH-28, S04J-RECONCILE-01. BOTH / CROSSREF_ONLY / OPENALEX_ONLY / alternate-only subject sets equal the sets computed independently from the raw scenario with the S02 parser; counts add up (union = both + crossref_only + openalex_only) and the union exceeds 2,200 DOIs."""
        shared = shared_jisa()
        cr, primary, alternate = _expected_sets()
        with open_reader(shared["root"]) as kb:
            m = members(kb, shared["version"]["version_id"])
        got = lambda cat: {k for k, v in m.items() if v["category"] == cat}  # noqa: E731
        self.assertEqual(got("BOTH"), cr & primary)
        self.assertEqual(got("CROSSREF_ONLY"), cr - primary)
        self.assertEqual(got("OPENALEX_ONLY"), primary - cr)
        self.assertEqual(got("OPENALEX_ALTERNATE_LOCATION_ONLY"), alternate - cr - primary)
        rec = shared["version"]["coverage"]["reconciliation"]
        self.assertEqual(rec["union_unique_dois_with_membership_evidence"], rec["both_providers"] + rec["crossref_only"] + rec["openalex_only"])
        self.assertEqual(rec["union_unique_dois_with_membership_evidence"], len(cr | primary))
        self.assertGreater(rec["union_unique_dois_with_membership_evidence"], 2200)

    def test_primary_venue_versus_alternate_location(self):
        """IDs: JH-12, JH-16, S04J-VENUE. A work whose OpenAlex primary source is a repository and which lists the journal only as an alternate location is never a member by OpenAlex evidence: the arXiv-style preprint is OPENALEX_ALTERNATE_LOCATION_ONLY (membership_asserted 0, review task); DOI 000500, listed by Crossref, is CROSSREF_ONLY with a VENUE_MISMATCH conflict holding both sources."""
        shared = shared_jisa()
        with open_reader(shared["root"]) as kb:
            m = members(kb, shared["version"]["version_id"])
            pre = m["10.48550/arxiv.2401.00001"]
            self.assertEqual((pre["category"], pre["membership_asserted"]), ("OPENALEX_ALTERNATE_LOCATION_ONLY", 0))
            v500 = m["10.5555/jisa-synthetic.000500"]
            self.assertEqual(v500["category"], "CROSSREF_ONLY")
            self.assertIn("VENUE_MISMATCH", v500["flags"])
            conflict = kb.one("SELECT values_json FROM jh_conflict WHERE subject='10.5555/jisa-synthetic.000500' AND kind='VENUE_MISMATCH'")
            values = json.loads(conflict["values_json"])
            self.assertEqual([v["provider"] for v in values], ["CROSSREF", "OPENALEX"])
            self.assertEqual(values[1]["value"], "S900000099")
            self.assertIsNotNone(kb.one("SELECT 1 x FROM jh_review_task WHERE trigger='ALTERNATE_LOCATION_ONLY' AND subject='10.48550/arxiv.2401.00001'"))
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_member WHERE category='OPENALEX_ALTERNATE_LOCATION_ONLY' AND membership_asserted=1")["n"], 0)

    def test_missing_or_rejected_key_blocks_deep_paging(self):
        """IDs: JH-13, JH-20, JH-34, S04J-OA-KEY. Without a configured key the first OpenAlex page (cursor=*) is fetched and kept, the deep-paging request is NOT sent (page NOT_SENT_CREDENTIAL_REQUIRED) and the run is BLOCKED (API_KEY_REQUIRED_FOR_DEEP_PAGING); the collection is PARTIAL, not COMPLETE and not empty-as-complete. A wrong key gets HTTP 401 -> BLOCKED HTTP_401_CREDENTIAL_REJECTED."""
        with self.writer() as kb:
            sim = SimulatedProviders(small_scenario(30))
            h = harvester(kb, sim, key=False)
            jid = h.register_journal("no key", JISA)["journal_id"]
            h.resolve(jid)
            h.harvest(jid, "CROSSREF", page_size=10)
            out = h.harvest(jid, "OPENALEX", page_size=10)
            self.assertEqual((out["state"], out["stop_reason"], out["counters"]["observations_stored"]), ("BLOCKED", "API_KEY_REQUIRED_FOR_DEEP_PAGING", 10))
            self.assertEqual(kb.one("SELECT outcome FROM jh_page WHERE run_id=? ORDER BY seq DESC LIMIT 1", (out["run_id"],))["outcome"],
                             "NOT_SENT_CREDENTIAL_REQUIRED")
            self.assertEqual([u for u in (c.url for c in sim.calls) if "/works" in u and "cursor=%2A" not in u and "openalex" in u], [])
            v = Reconciler(kb, operator="tester").reconcile(jid)
            self.assertEqual(v["collection_state"], "PARTIAL")
            wrong = JournalHarvesterWithKey(kb, sim, "not-the-test-key")
            out = wrong.harvest(jid, "OPENALEX", page_size=10)
            self.assertEqual((out["state"], out["stop_reason"]), ("BLOCKED", "HTTP_401_CREDENTIAL_REJECTED"))

    def test_credentials_never_persist(self):
        """IDs: JH-30, S04J-SECRET. The configured key travels only inside a Secret (redacted repr/str, not picklable); it appears nowhere in the KB dump, CAS objects, run/page identities, coverage or export files; stored request identities contain no '@', 'mailto' or 'api_key' (also enforced by CHECK constraints)."""
        secret = Secret(TEST_KEY)
        self.assertEqual((repr(secret), str(secret)), ("Secret(<redacted>)", "Secret(<redacted>)"))
        with self.assertRaises(TypeError):
            pickle.dumps(secret)
        self.assertEqual(Credentials.from_env({"SCIVETRA_OPENALEX_API_KEY": TEST_KEY}).summary(),
                         {"openalex_api_key_configured": True, "crossref_contact_configured": False})
        shared = shared_jisa()
        needle = TEST_KEY.encode()
        with open_reader(shared["root"]) as kb:
            dump = "\n".join(kb.connection.iterdump()).encode()
            self.assertNotIn(needle, dump)
            for path, state in kb.cas.iter_objects():
                self.assertNotIn(needle, path.read_bytes())
            self.assertEqual(kb.all("SELECT page_id FROM jh_page WHERE instr(request_identity_json,'@') OR instr(lower(request_identity_json),'api_key')"), [])
            self.assertEqual(kb.all("SELECT run_id FROM jh_run WHERE instr(lower(query_json),'api_key') OR instr(query_json,'@')"), [])
        for export in shared["exports"]:
            folder = os.path.join(shared["tmp"], "out1", export["folder_name"])
            for name in os.listdir(folder):
                with open(os.path.join(folder, name), "rb") as fh:
                    self.assertNotIn(needle, fh.read())


class JournalHarvesterWithKey:
    def __new__(cls, kb, sim, key):
        from scivetra_kb.journal.harvest import JournalHarvester
        return JournalHarvester(kb, sim, operator="tester", credentials=Credentials(Secret(key)), sleeper=lambda s: None, jitter=lambda: 0.0)


class ConflictTests(KBTest):
    def test_conflicts_keep_every_value_and_projection_is_deterministic(self):
        """IDs: JH-17, JH-16, S04J-CONFLICT. Title (000400), publication-year (000401) and work-type (000300: Crossref journal-article vs OpenAlex preprint) disagreements are stored with BOTH values and their provider/observation/run/page/JSON-pointer sources; the projection keeps the Crossref value by the documented precedence and names its source; review tasks FIELD_CONFLICT / WORK_TYPE_VERSION_MISMATCH are raised; nothing is overwritten."""
        shared = shared_jisa()
        with open_reader(shared["root"]) as kb:
            m = members(kb, shared["version"]["version_id"])
            conflicts = {(c["subject"], c["kind"]): json.loads(c["values_json"]) for c in kb.all("SELECT * FROM jh_conflict")}
            t400 = conflicts[("10.5555/jisa-synthetic.000400", "TITLE_CONFLICT")]
            self.assertEqual([v["value"] for v in t400], ["Synthetic security study number 400", "A different synthetic title for study 400"])
            self.assertTrue(all({"provider", "observation_id", "run_id", "page_id", "path", "observed_at"} <= set(v) for v in t400))
            self.assertEqual(m["10.5555/jisa-synthetic.000400"]["projection"]["title"], "Synthetic security study number 400")
            prov = json.loads(kb.one("SELECT provenance_json FROM jh_member WHERE doi_key='10.5555/jisa-synthetic.000400'")["provenance_json"])
            self.assertEqual(prov["title"]["provider"], "CROSSREF")
            self.assertIn(("10.5555/jisa-synthetic.000401", "DATE_CONFLICT"), conflicts)
            wt = conflicts[("10.5555/jisa-synthetic.000300", "WORK_TYPE_CONFLICT")]
            self.assertEqual([v["value"] for v in wt], ["journal-article", "preprint"])
            triggers = {(r["subject"], r["trigger"]) for r in kb.all("SELECT subject, trigger FROM jh_review_task")}
            self.assertIn(("10.5555/jisa-synthetic.000300", "WORK_TYPE_VERSION_MISMATCH"), triggers)
            self.assertIn(("10.5555/jisa-synthetic.000400", "FIELD_CONFLICT"), triggers)

    def test_no_automatic_merge_of_versions_or_duplicate_records(self):
        """IDs: JH-16, S04J-NO-MERGE. A Crossref has-preprint relation (000301) raises only a VERSION_LINK_CANDIDATE task (policy NEVER_AUTO_MERGED) and the preprint DOI stays a separate subject; two OpenAlex works with one DOI (000200) stay two work IDs on one DOI subject with DUPLICATE_PROVIDER_RECORDS_SAME_DOI; no GlobalPaper or identity assertion is created by the journal pipeline."""
        shared = shared_jisa()
        with open_reader(shared["root"]) as kb:
            m = members(kb, shared["version"]["version_id"])
            self.assertIn("VERSION_LINK_CANDIDATE", m["10.5555/jisa-synthetic.000301"]["flags"])
            ctx = json.loads(kb.one("SELECT context_json FROM jh_review_task WHERE trigger='VERSION_LINK_CANDIDATE'")["context_json"])
            self.assertEqual((ctx["related_id"], ctx["policy"]), ("10.48550/arxiv.2401.00001", "NEVER_AUTO_MERGED"))
            self.assertIn("10.48550/arxiv.2401.00001", m)
            self.assertEqual(m["10.5555/jisa-synthetic.000200"]["projection"]["openalex_work_ids"], ["W8000000200", "W9000000200"])
            self.assertIn("DUPLICATE_PROVIDER_RECORDS_SAME_DOI", m["10.5555/jisa-synthetic.000200"]["flags"])
            self.assertEqual((kb.one("SELECT count(*) n FROM global_paper")["n"], kb.one("SELECT count(*) n FROM identity_assertion")["n"]), (0, 0))

    def test_records_without_usable_doi_are_retained(self):
        """IDs: JH-22, S04J-QUARANTINE. 5 OpenAlex works without DOI, 1 with an ambiguous DOI (two prefixes) and 1 malformed Crossref record are kept as subjects (NO_DOI / DOI_AMBIGUOUS / MALFORMED flag) with review tasks; a missing DOI is not a crawl failure (runs stay COMPLETE)."""
        shared = shared_jisa()
        with open_reader(shared["root"]) as kb:
            rows = kb.all("SELECT category, record_ref, flags_json FROM jh_member WHERE version_id=? AND subject_kind='RECORD_WITHOUT_USABLE_DOI'",
                          (shared["version"]["version_id"],))
            cats = sorted(r["category"] for r in rows)
            self.assertEqual(cats, ["DOI_AMBIGUOUS"] + ["NO_DOI"] * 5)
            m = members(kb, shared["version"]["version_id"])
            self.assertIn("MALFORMED_PROVIDER_RECORD", m["10.5555/jisa-synthetic.malformed"]["flags"])
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_review_task WHERE trigger='DOI_AMBIGUOUS'")["n"], 1)
            self.assertIsNotNone(kb.one("SELECT 1 x FROM jh_review_task WHERE trigger='MALFORMED_PROVIDER_RECORD'"))
        self.assertEqual({r["state"] for r in shared["runs"].values()}, {"COMPLETE"})


class HistoryTests(KBTest):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmpdir = tempfile.mkdtemp(prefix="rkb-history-")
        cls.hroot = copy_shared(os.path.join(cls.tmpdir, "kb"))
        shared = shared_jisa()
        cls.v1 = shared["version"]["version_id"]
        cls.jid = shared["journal_id"]
        with open_writer(cls.hroot, clock=shared["clock"], ids=shared["ids"]) as kb:
            shared["clock"].advance(days=10)
            sim = SimulatedProviders.from_directory(str(JISA_DIR), "second_snapshot")
            h = harvester(kb, sim)
            cls.incremental = h.harvest(cls.jid, "CROSSREF", mode="INCREMENTAL", since="2026-10-01", page_size=200)
            cls.v_inc = Reconciler(kb, operator="tester").reconcile(cls.jid)
            cls.runs = {p: h.harvest(cls.jid, p, page_size=s) for p, s in (("CROSSREF", 200), ("OPENALEX", 100), ("OPENALEX_ALTERNATE", 100))}
            cls.v2 = Reconciler(kb, operator="tester").reconcile(cls.jid)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_incremental_run_updates_without_implying_deletion(self):
        """IDs: JH-29, S04J-INCREMENTAL. An INCREMENTAL Crossref run (from-update-date 2026-10-01) fetches only the 3 updated records and 1 new one; the reconciled version keeps every earlier member (an incremental run never implies deletion), applies the newer values and logs the change history non-destructively."""
        # v02: both planned ISSN queries are incremental too: 4 records each, 4 DOI keys in the union.
        self.assertEqual(self.incremental["counters"]["observations_stored"], 8)
        self.assertEqual(self.incremental["counters"]["unique_valid_doi_keys"], 4)
        self.assertEqual({k: v["observations_stored"] for k, v in self.incremental["counters"]["issn_queries"].items()}, {"2214-2126": 4, "2214-2134": 4})
        with open_reader(self.hroot) as kb:
            q = json.loads(kb.one("SELECT query_json FROM jh_run WHERE run_id=?", (self.incremental["run_id"],))["query_json"])
            self.assertEqual(q["filter"], "from-update-date:2026-10-01")
            m = members(kb, self.v_inc["version_id"])
            self.assertEqual(m["10.5555/jisa-synthetic.001999"]["category"], "BOTH")
            self.assertEqual(m["10.5555/jisa-synthetic.000600"]["projection"]["title"], "Synthetic security study number 600 (revised title)")
            self.assertEqual(m["10.5555/jisa-synthetic.new-0001"]["category"], "CROSSREF_ONLY")

    def test_changed_records_keep_history_and_old_versions_are_immutable(self):
        """IDs: JH-29, JH-19, S04J-HISTORY. After a second full snapshot, 000600's new title is projected and its history lists before/after with both observation references; version 1 still holds the old title; a DOI absent from the new complete runs (001999) stays listed as NOT_SEEN_IN_LATEST_RUNS (membership not asserted, task raised), never deleted; UPDATE/DELETE on journal tables are refused."""
        with open_reader(self.hroot) as kb:
            new, old = members(kb, self.v2["version_id"]), members(kb, self.v1)
            self.assertEqual(old["10.5555/jisa-synthetic.000600"]["projection"]["title"], "Synthetic security study number 600")
            p600 = new["10.5555/jisa-synthetic.000600"]["projection"]
            self.assertEqual(p600["title"], "Synthetic security study number 600 (revised title)")
            titles = [h for h in p600["history"] if h["field"] == "title" and h["provider"] == "CROSSREF"]
            self.assertEqual(titles[0]["before"], ["Synthetic security study number 600"])
            self.assertNotEqual(titles[0]["before_ref"]["observation_id"], titles[0]["after_ref"]["observation_id"])
            gone = new["10.5555/jisa-synthetic.001999"]
            self.assertEqual((gone["category"], gone["membership_asserted"]), ("NOT_SEEN_IN_LATEST_RUNS", 0))
            self.assertIn("NOT_SEEN_IN_LATEST_COMPLETE_RUN", gone["flags"])
        with open_writer(self.hroot) as kb:
            for sql in ("UPDATE jh_member SET category='BOTH'", "DELETE FROM jh_observation", "UPDATE jh_field_claim SET value_json='1'",
                        "DELETE FROM jh_run_event", "UPDATE jh_collection_version SET collection_state='BLOCKED'"):
                with self.assertRaises(ImmutableRecordError):
                    kb.execute(sql)

    def test_retraction_history_survives_a_silent_newer_snapshot(self):
        """IDs: JH-18, S04J-RETRACTION. 000040 was reported retracted (Crossref Retraction-Watch update, Crossref notice, OpenAlex is_retracted); the second snapshot no longer reports any of it. Version 2 still says retraction_reported_ever = true with every signal's first/last sighting, flags INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT and raises a review task; the disappearance is never a counter-retraction."""
        with open_reader(self.hroot) as kb:
            p = members(kb, self.v2["version_id"])["10.5555/jisa-synthetic.000040"]
            self.assertTrue(p["projection"]["retraction_reported_ever"])
            self.assertIn("INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT", p["flags"])
            signals = p["projection"]["integrity_signals"]
            self.assertEqual({(s["provider"], s["reported_by"]) for s in signals},
                             {("CROSSREF", "CROSSREF_RETRACTION_WATCH"), ("CROSSREF", "CROSSREF_PUBLISHER_UPDATE"), ("OPENALEX", "OPENALEX_IS_RETRACTED")})
            self.assertTrue(all(s["present_in_latest_snapshot"] is False for s in signals))
            self.assertIs(p["projection"]["openalex_is_retracted_latest"], False)
            self.assertGreaterEqual(kb.one("SELECT count(*) n FROM jh_review_task WHERE trigger='INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT' "
                                           "AND subject='10.5555/jisa-synthetic.000040'")["n"], 1)
            self.assertEqual(kb.one("SELECT count(*) n FROM integrity_counter_evidence")["n"], 0)

    def test_raw_evidence_is_retained_and_resolvable(self):
        """IDs: JH-19, S04J-RAW. Every OK page's raw bytes are in CAS with a matching SHA-256; an observation's item_pointer resolves inside its raw page to an item whose canonical hash is item_sha256, and sampled field claims resolve at their JSON pointer to the stored value; pages record retrieval time, HTTP status, attempts and the secret-free request identity."""
        from scivetra_kb.core.canonical import semantic_sha256
        with open_reader(self.hroot) as kb:
            pages = kb.all("SELECT * FROM jh_page WHERE outcome='OK' AND kind='WORKS_PAGE'")
            self.assertTrue(pages)
            for page in pages[:: max(1, len(pages) // 15)]:
                data = kb.cas.read(page["response_bytes_sha256"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), page["response_bytes_sha256"])
                self.assertTrue(page["retrieved_at_utc"] and page["http_status"] == 200 and page["attempts"] >= 1)
                doc = json.loads(data)
                for obs in kb.all("SELECT * FROM jh_observation WHERE page_id=? LIMIT 5", (page["page_id"],)):
                    item = _pointer(doc, obs["item_pointer"])
                    self.assertEqual(semantic_sha256(item), obs["item_sha256"])
                    for claim in kb.all("SELECT * FROM jh_field_claim WHERE observation_id=? AND field IN ('title','work_type','volume') LIMIT 3",
                                        (obs["observation_id"],)):
                        raw = _pointer(doc, claim["source_field_path"])
                        self.assertEqual(raw, json.loads(claim["value_json"]))


def _pointer(doc, pointer):
    node = doc
    for part in pointer.lstrip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


class CoverageStateTests(KBTest):
    def _state(self, *, cr_faults=(), key=True, cr_present=True, oa_present=True):
        data = small_scenario(30)
        data["faults"] = list(cr_faults)
        if not cr_present:
            data["crossref_journals"] = {}
        if not oa_present:
            data["openalex_sources"] = []
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data), key=key)
            jid = h.register_journal("states", JISA)["journal_id"]
            h.resolve(jid)
            runs = {p: h.harvest(jid, p, page_size=10)["state"] for p in ("CROSSREF", "OPENALEX")}
            v = Reconciler(kb, operator="tester").reconcile(jid)
            return runs, v

    def test_collection_state_reflects_both_provider_outcomes(self):
        """IDs: JH-34, JH-20, S04J-STATES. COMPLETE+COMPLETE -> COMPLETE_RELATIVE_TO_RECORDED_QUERIES (crawl_complete true, scope statement and limitations present); COMPLETE+BLOCKED(no key) -> PARTIAL; PARTIAL(503s)+COMPLETE -> PARTIAL; BLOCKED+BLOCKED -> BLOCKED. No coverage ever contains ALL_JOURNAL_ARTICLES_FOUND and the KB CHECK refuses it."""
        cases = [({}, {"CROSSREF": "COMPLETE", "OPENALEX": "COMPLETE"}, "COMPLETE_RELATIVE_TO_RECORDED_QUERIES"),
                 ({"key": False}, {"CROSSREF": "COMPLETE", "OPENALEX": "BLOCKED"}, "PARTIAL"),
                 ({"cr_faults": [{"provider": "CROSSREF", "works_key": "JISA", "page_index": 1, "responses": [{"status": 503}] * 4}]},
                  {"CROSSREF": "PARTIAL", "OPENALEX": "COMPLETE"}, "PARTIAL"),
                 ({"cr_present": False, "oa_present": False}, {"CROSSREF": "BLOCKED", "OPENALEX": "BLOCKED"}, "BLOCKED")]
        for kw, runs_expected, state in cases:
            with self.subTest(state=state, runs=runs_expected):
                self.tearDown()
                self.setUp()
                runs, v = self._state(**kw)
                self.assertEqual(runs, runs_expected)
                self.assertEqual(v["collection_state"], state)
                cov = v["coverage"]
                self.assertEqual(cov["crawl_complete"], state.startswith("COMPLETE"))
                self.assertNotIn("ALL_JOURNAL_ARTICLES_FOUND", json.dumps(cov))
                self.assertIn("DISCOVERY_EVIDENCE_ONLY", cov["scope_statement"])
                self.assertTrue(any("does not assert that every work" in s for s in cov["limitations"]))
                for p in ("CROSSREF", "OPENALEX"):
                    self.assertEqual(cov["providers"][p]["state"], runs_expected[p])
        with self.writer() as kb:
            row = kb.one("SELECT * FROM jh_collection_version")
            row.update(version_id="jcv_x", version_no=99, semantic_sha256="0" * 64, coverage_json='{"x":"ALL_JOURNAL_ARTICLES_FOUND"}')
            with self.assertRaises(ValidationError):
                with kb.transaction():
                    kb.insert("jh_collection_version", row)
