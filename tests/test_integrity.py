import json
import os
import unittest

from tests.helpers import KBTest, cr_work, openalex_retraction_claim, resp, rv_item, structured, write_rv_export
from scivetra_kb.core.errors import ValidationError
from scivetra_kb.metadata import crossref
from scivetra_kb.metadata.integrity import IntegrityService
from scivetra_kb.metadata.rv_import import import_corpus_export
from scivetra_kb.metadata.store import MetadataStore
from scivetra_kb.metadata.transport import FixtureTransport

RET = {"DOI": "10.99999/notice.r1", "type": "retraction", "label": "Retraction", "source": "publisher", "updated": {"date-parts": [[2020, 1, 2]]}}
NOTICE_TEXT = ("Notice of reinstatement.\nThe retraction notice 10.99999/notice.r1 was issued in error; "
               "the article 10.99999/ret.1 is reinstated.\n")


class IntegrityBase(KBTest):
    doi = "10.99999/ret.1"

    def setUp(self):
        super().setUp()
        self.ft = FixtureTransport()
        self.route_ra(self.ft, self.doi)
        self.url = crossref.build_url(self.doi)

    def fetch(self, *bodies, kb=None):
        # Fresh queue per call: one response per fetch_provider() call, in order.
        self.ft.routes[self.url] = [body if not isinstance(body, dict) else resp(200, body, self.url) for body in bodies]
        with self.writer() as kb:
            service = self.service(kb, self.ft)
            results = [service.fetch_provider("CROSSREF", structured(self.doi)) for _ in bodies]
        return results

    def view(self):
        with self.reader() as kb:
            return IntegrityService(MetadataStore(kb)).view("doi", self.doi)

    def count(self, table):
        with self.reader() as kb:
            return kb.one(f"SELECT count(*) n FROM {table}")["n"]

    def events(self):
        with self.reader() as kb:
            return kb.all("SELECT * FROM integrity_event ORDER BY rowid")


class ProviderEventTests(IntegrityBase):
    def test_mf_05_publisher_and_retraction_watch(self):
        """IDs: MF-05, N05. Publisher and Retraction Watch reports of one retraction are two events; record-id string and int accepted."""
        rw1 = dict(RET, source="retraction-watch", **{"record-id": "4036"})
        rw2 = {"DOI": "10.99999/notice.r2", "type": "retraction", "source": "retraction-watch", "record-id": 44124}
        result = self.fetch(cr_work(self.doi, **{"updated-by": [RET, rw1, rw2]}))[0]
        self.assertEqual(result["coverage"], ["SIGNAL_REPORTED"])
        events = self.events()
        self.assertEqual(sorted(e["reported_by"] for e in events),
                         ["CROSSREF_PUBLISHER_UPDATE", "CROSSREF_RETRACTION_WATCH", "CROSSREF_RETRACTION_WATCH"])
        self.assertEqual(sorted(e["record_id"] for e in events if e["record_id"]), ["4036", "44124"])
        with self.reader() as kb:
            scope = json.loads(kb.one("SELECT scope_note_json FROM integrity_coverage")["scope_note_json"])
        self.assertEqual(scope["crossref_rw_included"], "YES")
        view = self.view()
        self.assertEqual((view["families"]["RETRACTION"]["state"], view["alert"]), ("REPORTED", "OPEN_ALERT"))
        self.assertEqual(self.count("integrity_counter_evidence"), 0)

    def test_mf_06_correction_and_concern(self):
        """IDs: MF-06."""
        self.fetch(cr_work(self.doi, **{"updated-by": [{"DOI": "10.99999/c", "type": "correction", "source": "publisher"},
                                                        {"DOI": "10.99999/e", "type": "expression_of_concern", "source": "publisher"}]}))
        self.assertEqual(sorted(e["event_type"] for e in self.events()), ["CORRECTION", "EXPRESSION_OF_CONCERN"])

    def test_mf_07_notice_update_to(self):
        """IDs: MF-07. A notice record's update-to withdrawal is an event on the referenced work."""
        notice = "10.99999/notice.w1"
        ft = FixtureTransport()
        self.route_ra(ft, notice)
        self.add_crossref(ft, notice, cr_work(notice, type="retraction", **{"update-to": [{"DOI": "10.99999/subject.w1", "type": "withdrawal", "source": "publisher"}]}))
        with self.writer() as kb:
            self.service(kb, ft).fetch([structured(notice)])
            event = kb.one("SELECT subject_value, event_type, notice_doi, source_field_path FROM integrity_event")
        self.assertEqual(event, {"subject_value": "10.99999/subject.w1", "event_type": "WITHDRAWAL", "notice_doi": notice,
                                 "source_field_path": "/message/update-to/0"})


class SilenceAndOmissionTests(IntegrityBase):
    def import_openalex(self, integrity):
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_1", dois=[(self.doi, self.doi)], integrity=integrity)])
        with self.writer() as kb:
            return import_corpus_export(kb, export, workspace_label="ws")

    def test_mf_19_other_provider_silence_is_not_contradiction(self):
        """IDs: MF-19, N06. OpenAlex reports a retraction; Crossref is silent: REPORTED_NOT_CORROBORATED, OPEN_ALERT, no counter-evidence."""
        self.import_openalex(openalex_retraction_claim())
        self.fetch(cr_work(self.doi, title=["T"]))
        view = self.view()
        family = view["families"]["RETRACTION"]
        self.assertEqual(family["state"], "REPORTED_NOT_CORROBORATED")
        self.assertEqual(view["alert"], "OPEN_ALERT")
        self.assertEqual(self.count("integrity_counter_evidence"), 0)
        crossref_cov = [c for c in family["coverage"] if c["provider"] == "CROSSREF"][0]
        self.assertEqual((crossref_cov["outcome"], crossref_cov["scope_note"]["crossref_rw_included"]), ("NO_SIGNAL_REPORTED", "RW_ENTRY_NOT_SEEN"))
        self.assertNotIn("not retracted", json.dumps(view).lower())

    def test_mf_19b_same_provider_omission(self):
        """IDs: MF-19b, N06c. A later snapshot omitting the report: event kept, omission coverage with both snapshots, 0 counter-evidence, REPORTED + flag, OPEN_ALERT, review task."""
        first, second = self.fetch(cr_work(self.doi, **{"updated-by": [RET]}), cr_work(self.doi, title=["T"]))
        events = self.events()
        self.assertEqual(len(events), 1)
        with self.reader() as kb:
            omission = kb.one("SELECT * FROM integrity_coverage WHERE outcome='REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT'")
            task = kb.one("SELECT * FROM review_task WHERE trigger='REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT'")
        self.assertEqual(omission["prior_snapshot_id"], first["snapshot_id"])
        self.assertEqual(omission["snapshot_id"], second["snapshot_id"])
        self.assertEqual(json.loads(omission["affected_event_ids_json"]), [events[0]["event_id"]])
        self.assertEqual(json.loads(task["event_ids_json"]), [events[0]["event_id"]])
        self.assertEqual(self.count("integrity_counter_evidence"), 0)
        view = self.view()
        family = view["families"]["RETRACTION"]
        self.assertEqual(family["state"], "REPORTED")
        self.assertIn("REPORTING_SOURCE_LATEST_SNAPSHOT_OMITS_REPORT", family["flags"])
        self.assertIn("INVESTIGATION_PENDING", family["flags"])
        self.assertEqual(view["alert"], "OPEN_ALERT")
        self.assertEqual(events[0]["first_snapshot_id"], first["snapshot_id"])

    def test_mf_19b2_precedence_with_other_silent_provider(self):
        """IDs: MF-19b2, N06c. As MF-19b plus a silent OpenAlex: REPORTED_NOT_CORROBORATED + omission flag, OPEN_ALERT."""
        self.import_openalex([])
        self.fetch(cr_work(self.doi, **{"updated-by": [RET]}), cr_work(self.doi, title=["T"]))
        view = self.view()
        family = view["families"]["RETRACTION"]
        self.assertEqual(family["state"], "REPORTED_NOT_CORROBORATED")
        self.assertIn("REPORTING_SOURCE_LATEST_SNAPSHOT_OMITS_REPORT", family["flags"])
        self.assertEqual(view["alert"], "OPEN_ALERT")

    def test_mf_19b3_later_404_and_5xx(self):
        """IDs: MF-19b3, N06c. Later 404 then 5xx: SUBJECT_NOT_FOUND then CHECK_FAILED; event kept; same view and alert; no counter-evidence."""
        self.fetch(cr_work(self.doi, **{"updated-by": [RET]}))
        self.clock.advance(days=1)
        self.fetch(resp(404, "Resource not found.", self.url, {"content-type": "text/plain"}))
        view_after_404 = self.view()
        self.clock.advance(days=1)
        self.fetch(resp(503, "", self.url, {}))
        with self.reader() as kb:
            outcomes = [r["outcome"] for r in kb.all("SELECT outcome FROM integrity_coverage ORDER BY rowid")]
        self.assertEqual(outcomes, ["SIGNAL_REPORTED", "SUBJECT_NOT_FOUND", "CHECK_FAILED"])
        for view in (view_after_404, self.view()):
            family = view["families"]["RETRACTION"]
            self.assertEqual(family["state"], "REPORTED")
            self.assertIn("REPORTING_SOURCE_LATEST_SNAPSHOT_OMITS_REPORT", family["flags"])
            self.assertEqual(view["alert"], "OPEN_ALERT")
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.count("integrity_counter_evidence"), 0)

    def test_mf_19d_silence_without_events(self):
        """IDs: MF-19d, N06b. No events; Crossref silent; DataCite unsupported: NO_SIGNAL_REPORTED with providers/dates, no 'not retracted' wording."""
        self.fetch(cr_work(self.doi, title=["T"]))
        from tests.helpers import dc_doi
        self.add_datacite(self.ft, self.doi, dc_doi(self.doi, titles=[{"title": "T"}]))
        with self.writer() as kb:
            self.service(kb, self.ft).fetch_provider("DATACITE", structured(self.doi))
        view = self.view()
        family = view["families"]["RETRACTION"]
        self.assertEqual(family["state"], "NO_SIGNAL_REPORTED")
        self.assertEqual(view["alert"], "NO_ALERT")
        providers = {c["provider"]: c["outcome"] for c in family["coverage"]}
        self.assertEqual(providers, {"CROSSREF": "NO_SIGNAL_REPORTED", "DATACITE": "NOT_SUPPORTED_BY_PROVIDER"})
        self.assertIn("CROSSREF", family["display"])
        self.assertNotIn("not retracted", json.dumps(view).lower())

    def test_mf_19e_never_checked_or_failed(self):
        """IDs: MF-19e. Never checked gives COVERAGE_UNAVAILABLE; only a failed check also gives COVERAGE_UNAVAILABLE."""
        self.assertEqual(self.view()["summary_state"], "COVERAGE_UNAVAILABLE")
        self.fetch(resp(503, "", self.url, {}))
        view = self.view()
        self.assertEqual(view["families"]["RETRACTION"]["state"], "COVERAGE_UNAVAILABLE")
        self.assertEqual(view["summary_state"], "COVERAGE_UNAVAILABLE")


class CounterEvidenceTests(IntegrityBase):
    def setup_event(self):
        self.fetch(cr_work(self.doi, **{"updated-by": [RET]}))
        return self.events()[0]["event_id"]

    def register_notice(self, kb, text=NOTICE_TEXT):
        return IntegrityService(MetadataStore(kb)).register_document("reviewer.a", text.encode("utf-8"), "synthetic notice")

    def anchor(self, digest, text=NOTICE_TEXT, phrase="was issued in error"):
        start = text.index(phrase)
        return {"document_sha256": digest, "page": 1, "char_start": start, "char_end": start + len(phrase)}

    def test_mf_19b4_counter_evidence_requires_primary_document(self):
        """IDs: MF-19b4, N06c, S02-PROP-8. Counter-evidence without a primary document, with free text, with a bad anchor or from a non-human label is rejected."""
        event_id = self.setup_event()
        with self.writer() as kb:
            service = IntegrityService(MetadataStore(kb))
            doc = self.register_notice(kb)
            cases = [
                dict(operator="reviewer.a", primary_document_ref="", evidence_anchor=self.anchor(doc["document_sha256"])),
                dict(operator="reviewer.a", primary_document_ref="I am fairly sure it was reinstated", evidence_anchor=self.anchor(doc["document_sha256"])),
                dict(operator="reviewer.a", primary_document_ref="sha256:" + "0" * 64, evidence_anchor=self.anchor(doc["document_sha256"])),
                dict(operator="reviewer.a", primary_document_ref="sha256:" + doc["document_sha256"], evidence_anchor={}),
                dict(operator="reviewer.a", primary_document_ref="sha256:" + doc["document_sha256"],
                     evidence_anchor={"document_sha256": doc["document_sha256"], "page": 1, "char_start": 0, "char_end": 100000}),
                dict(operator="reviewer.a", primary_document_ref="sha256:" + doc["document_sha256"],
                     evidence_anchor={"snapshot_id": "ps_does_not_exist", "source_field_path": "/message"}),
                dict(operator="SYSTEM", primary_document_ref="sha256:" + doc["document_sha256"], evidence_anchor=self.anchor(doc["document_sha256"])),
                dict(operator="", primary_document_ref="sha256:" + doc["document_sha256"], evidence_anchor=self.anchor(doc["document_sha256"])),
            ]
            for case in cases:
                with self.subTest(case=case["primary_document_ref"][:20] + "/" + case["operator"]):
                    with self.assertRaises(ValidationError):
                        with kb.transaction():
                            service.record_counter_evidence(opposes_event_id=event_id, kind="REINSTATEMENT_NOTICE",
                                                            statement_summary="reinstated", **case)
            with self.assertRaises(ValidationError):
                with kb.transaction():
                    service.record_review(operator="reviewer.a", subject_namespace="doi", subject_value=self.doi, event_ids=[event_id],
                                          trigger="REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT", decision="REPORT_NOT_APPLICABLE_WITH_EVIDENCE",
                                          reason="Gone from Crossref")
            with kb.transaction():
                service.record_review(operator="reviewer.a", subject_namespace="doi", subject_value=self.doi, event_ids=[event_id],
                                      trigger="REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT", decision="INCONCLUSIVE", reason="Need the notice")
        self.assertEqual(self.count("integrity_counter_evidence"), 0)
        self.assertEqual(self.view()["alert"], "OPEN_ALERT")
        with self.writer() as kb:
            with kb.transaction():
                IntegrityService(MetadataStore(kb)).record_review(operator="reviewer.a", subject_namespace="doi", subject_value=self.doi,
                                                                  event_ids=[event_id], trigger="REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT",
                                                                  decision="CONFIRMED_REPORT", reason="Publisher notice still online")
        self.assertEqual(self.view()["alert"], "CLOSED_CONFIRMED")

    def test_persistence_boundary_rejects_direct_inserts(self):
        """IDs: MF-19b4, S02-PROP-8. Direct SQL inserts bypassing the service are rejected by DB triggers/CHECKs."""
        event_id = self.setup_event()
        base = dict(counter_evidence_id="ice_direct", opposes_event_id=event_id, kind="REINSTATEMENT_NOTICE",
                    primary_document_ref="sha256:" + "0" * 64, evidence_anchor_json=json.dumps({"document_sha256": "0" * 64, "page": 1, "char_start": 0, "char_end": 5}),
                    statement_summary="x", recorded_by="OPERATOR:reviewer.a", recorded_at_utc="2026-11-01T00:00:00.000000Z")
        variants = [dict(base), dict(base, recorded_by="SYSTEM_DIFF"), dict(base, primary_document_ref="free text"),
                    dict(base, opposes_event_id="ige_missing"), dict(base, evidence_anchor_json="{}")]
        with self.writer() as kb:
            for row in variants:
                with self.subTest(row=row["recorded_by"] + row["primary_document_ref"][:10]):
                    with self.assertRaises(ValidationError):
                        kb.insert("integrity_counter_evidence", row)
            with self.assertRaises(ValidationError):
                kb.insert("integrity_review", dict(review_id="igr_direct", subject_namespace="doi", subject_value=self.doi, task_id=None,
                                                   event_ids_json=json.dumps([event_id]), trigger="OPERATOR_REQUEST",
                                                   decision="REPORT_NOT_APPLICABLE_WITH_EVIDENCE", counter_evidence_ids_json="[]",
                                                   reviewer_label="OPERATOR:reviewer.a", reason="x", decided_at_utc="2026-11-01T00:00:00.000000Z"))
        self.assertEqual(self.count("integrity_counter_evidence"), 0)

    def test_mf_19c_operator_error_notice(self):
        """IDs: MF-19c. An anchored publisher error notice gives EXPLICIT_CONTRADICTION; an evidenced review closes as CLOSED_NOT_APPLICABLE."""
        event_id = self.setup_event()
        text = "Publisher notice: the retraction 10.99999/notice.r1 was issued in error.\n"
        with self.writer() as kb:
            service = IntegrityService(MetadataStore(kb))
            with kb.transaction():
                doc = self.register_notice(kb, text)
                ce = service.record_counter_evidence(operator="reviewer.a", opposes_event_id=event_id, kind="NOTICE_STATES_ERROR",
                                                     primary_document_ref="sha256:" + doc["document_sha256"],
                                                     evidence_anchor=self.anchor(doc["document_sha256"], text), statement_summary="Issued in error")
        view = self.view()
        self.assertEqual((view["families"]["RETRACTION"]["state"], view["alert"]), ("EXPLICIT_CONTRADICTION", "OPEN_ALERT"))
        with self.writer() as kb:
            with kb.transaction():
                IntegrityService(MetadataStore(kb)).record_review(operator="reviewer.b", subject_namespace="doi", subject_value=self.doi,
                                                                  event_ids=[event_id], trigger="OPERATOR_REQUEST",
                                                                  decision="REPORT_NOT_APPLICABLE_WITH_EVIDENCE", reason="Notice states error",
                                                                  counter_evidence_ids=[ce["counter_evidence_id"]])
        self.assertEqual(self.view()["alert"], "CLOSED_NOT_APPLICABLE")
        self.assertEqual(len(self.events()), 1)

    def test_mf_19f_positive_reinstatement_notice(self):
        """IDs: MF-19f. A later notice creates an event and a review task only; a human-anchored reinstatement gives EXPLICIT_CONTRADICTION; a new report reopens the alert."""
        correction = {"DOI": "10.99999/notice.c1", "type": "correction", "label": "Notice of reinstatement", "source": "publisher"}
        self.fetch(cr_work(self.doi, **{"updated-by": [RET]}), cr_work(self.doi, **{"updated-by": [RET, correction]}))
        events = self.events()
        self.assertEqual([e["event_type"] for e in events], ["RETRACTION", "CORRECTION"])
        with self.reader() as kb:
            task = kb.one("SELECT * FROM review_task WHERE trigger='POSSIBLE_OPPOSING_NOTICE'")
        self.assertEqual(sorted(json.loads(task["event_ids_json"])), sorted(e["event_id"] for e in events))
        self.assertEqual(self.count("integrity_counter_evidence"), 0)
        view = self.view()
        self.assertEqual((view["families"]["RETRACTION"]["state"], view["alert"]), ("REPORTED", "OPEN_ALERT"))
        with self.writer() as kb:
            service = IntegrityService(MetadataStore(kb))
            with kb.transaction():
                doc = self.register_notice(kb)
                ce = service.record_counter_evidence(operator="reviewer.a", opposes_event_id=events[0]["event_id"], kind="REINSTATEMENT_NOTICE",
                                                     primary_document_ref="doi:10.99999/notice.c1",
                                                     evidence_anchor=self.anchor(doc["document_sha256"], phrase="the article 10.99999/ret.1 is reinstated"),
                                                     statement_summary="Notice reinstates the article")
        self.assertEqual(self.view()["families"]["RETRACTION"]["state"], "EXPLICIT_CONTRADICTION")
        with self.writer() as kb:
            with kb.transaction():
                IntegrityService(MetadataStore(kb)).record_review(operator="reviewer.b", subject_namespace="doi", subject_value=self.doi,
                                                                  event_ids=[e["event_id"] for e in events], trigger="POSSIBLE_OPPOSING_NOTICE",
                                                                  decision="REPORT_NOT_APPLICABLE_WITH_EVIDENCE", reason="Reinstated",
                                                                  counter_evidence_ids=[ce["counter_evidence_id"]], task_id=task["task_id"])
        self.assertEqual(self.view()["alert"], "CLOSED_NOT_APPLICABLE")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM provider_snapshot WHERE provider='CROSSREF'")["n"], 2)
        concern = {"DOI": "10.99999/notice.e1", "type": "expression_of_concern", "source": "publisher"}
        self.fetch(cr_work(self.doi, **{"updated-by": [RET, correction, concern]}))
        self.assertEqual(self.view()["alert"], "OPEN_ALERT")


if __name__ == "__main__":
    unittest.main()
