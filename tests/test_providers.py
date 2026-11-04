import json
import unittest

from tests.helpers import KBTest, cr_work, dc_doi, resp, structured
from scivetra_kb.metadata import cn, crossref
from scivetra_kb.metadata.transport import FixtureTransport, Response


class CrossrefTests(KBTest):
    def fetch(self, ft, doi):
        with self.writer() as kb:
            return self.service(kb, ft).fetch([structured(doi)])[0]

    def test_mf_03_claims_kept_per_element(self):
        """IDs: MF-03. Two titles, three authors (one organisation `name`), partial issued date kept partial."""
        doi = "10.99999/cr.3"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        self.add_crossref(ft, doi, cr_work(doi, title=["Title A", "Title B"], issued={"date-parts": [[2021]]},
                                           author=[{"given": "A", "family": "One", "sequence": "first"},
                                                   {"given": "B", "family": "Two", "sequence": "additional", "affiliation": [{"name": "Lab"}]},
                                                   {"name": "Consortium X", "sequence": "additional"}]))
        result = self.fetch(ft, doi)
        self.assertEqual(result["status"], "FOUND")
        with self.reader() as kb:
            claims = kb.all("SELECT field, value_json, source_field_path FROM metadata_claim ORDER BY rowid")
        titles = [json.loads(c["value_json"]) for c in claims if c["field"] == "title"]
        authors = [json.loads(c["value_json"]) for c in claims if c["field"] == "author"]
        issued = [json.loads(c["value_json"]) for c in claims if c["field"] == "date_issued"]
        self.assertEqual(titles, ["Title A", "Title B"])
        self.assertEqual([a["position"] for a in authors], [1, 2, 3])
        self.assertEqual(authors[2]["literal"], "Consortium X")
        self.assertEqual(authors[1]["affiliations"], ["Lab"])
        self.assertEqual(issued[0]["date_parts"], [[2021]])

    def test_mf_04_404_plain_text(self):
        """IDs: MF-04. Crossref 404 with a text/plain body: NOT_FOUND, no JSON parse, coverage SUBJECT_NOT_FOUND, RA disagreement flagged."""
        doi = "10.99999/cr.404"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = crossref.build_url(doi)
        ft.add(url, resp(404, "Resource not found.", url, {"content-type": "text/plain"}))
        result = self.fetch(ft, doi)
        self.assertEqual(result["status"], "NOT_FOUND")
        self.assertEqual(result["coverage"], ["SUBJECT_NOT_FOUND"])
        self.assertIn("RA_PROVIDER_DISAGREE", result["flags"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM metadata_claim")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM integrity_event")["n"], 0)

    def test_mf_08_unchanged_snapshot(self):
        """IDs: MF-08. Same deposited date and same bytes: UNCHANGED, no new snapshot or claims, check logged."""
        doi = "10.99999/cr.8"
        work = cr_work(doi, title=["Same"], deposited={"date-time": "2025-01-01T00:00:00Z"})
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        self.add_crossref(ft, doi, work)
        first, second = self.fetch(ft, doi), self.fetch(ft, doi)
        self.assertTrue(first["new_snapshot"])
        self.assertFalse(second["new_snapshot"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM provider_snapshot WHERE provider='CROSSREF'")["n"], 1)
            self.assertEqual([r["result"] for r in kb.all("SELECT result FROM provider_check c JOIN provider_snapshot s ON s.snapshot_id=c.snapshot_id WHERE s.provider='CROSSREF' ORDER BY c.rowid")],
                             ["NEW_SNAPSHOT", "UNCHANGED"])

    def test_mf_09_changed_metadata(self):
        """IDs: MF-09. Changed title and a new author: MetadataChangeEvent with a field diff; old claims preserved."""
        doi = "10.99999/cr.9"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = self.add_crossref(ft, doi, cr_work(doi, title=["Old title"], author=[{"family": "A"}]),
                                cr_work(doi, title=["New title"], author=[{"family": "A"}, {"family": "B"}]))
        self.fetch(ft, doi)
        self.clock.advance(days=1)
        result = self.fetch(ft, doi)
        self.assertTrue(result["metadata_change"])
        with self.reader() as kb:
            change = json.loads(kb.one("SELECT diff_json FROM metadata_change_event")["diff_json"])
            titles = [json.loads(r["value_json"]) for r in kb.all("SELECT value_json FROM metadata_claim WHERE field='title' ORDER BY rowid")]
        self.assertEqual(change["fields_changed"], ["author", "title"])
        self.assertEqual(titles, ["Old title", "New title"])

    def test_mf_10_subject_mismatch(self):
        """IDs: MF-10. Response DOI differs from the requested DOI: evidence stored, no claims, coverage CHECK_FAILED."""
        doi = "10.99999/cr.10"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        self.add_crossref(ft, doi, cr_work("10.99999/someone.else", title=["Other"],
                                           **{"updated-by": [{"DOI": "10.99999/n", "type": "retraction", "source": "publisher"}]}))
        result = self.fetch(ft, doi)
        self.assertEqual((result["status"], result["reason"]), ("REFUSED_UNSAFE_RESPONSE", "RESPONSE_SUBJECT_MISMATCH"))
        self.assertEqual(result["coverage"], ["CHECK_FAILED"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM metadata_claim")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM integrity_event")["n"], 0)
            self.assertIsNotNone(kb.one("SELECT response_bytes_sha256 FROM provider_snapshot WHERE provider='CROSSREF'")["response_bytes_sha256"])

    def test_suffix_space_significant_in_subject_check(self):
        """IDs: MF-10, N28. A response for `10.99999/x` does not satisfy a request for `10.99999/x ` (suffix space significant)."""
        doi = "10.99999/x "
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        self.add_crossref(ft, doi, cr_work("10.99999/x", title=["T"]))
        result = self.fetch(ft, doi)
        self.assertEqual(result["reason"], "RESPONSE_SUBJECT_MISMATCH")
        self.assertTrue(ft.calls[-1].url.endswith("/works/10.99999/x%20"))

    def test_abstract_marked_copyrighted(self):
        """IDs: CR-ABSTRACT-01. Abstract claims carry licence_class COPYRIGHTED_ABSTRACT."""
        doi = "10.99999/cr.ab"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        self.add_crossref(ft, doi, cr_work(doi, abstract="<jats:p>Synthetic abstract</jats:p>"))
        self.fetch(ft, doi)
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT licence_class FROM metadata_claim WHERE field='abstract'")["licence_class"], "COPYRIGHTED_ABSTRACT")

    def test_mf_18_25_and_34_no_trimmed_retry(self):
        """IDs: MF-18.25, MF-18.34, N28. NOT_FOUND for a DOI with trailing characters opens a review task; the trimmed DOI is never queried."""
        for doi in ("10.99999/abc).", "10.99999/x "):
            with self.subTest(doi=doi):
                ft = FixtureTransport()
                self.route_ra(ft, doi)
                url = crossref.build_url(doi)
                ft.add(url, resp(404, "Resource not found.", url, {"content-type": "text/plain"}))
                result = self.fetch(ft, doi)
                self.assertIn("REVIEW_TASK_TRAILING_CHARACTERS", result["flags"])
                crossref_calls = [c.url for c in ft.calls if "api.crossref.org" in c.url]
                self.assertEqual(crossref_calls, [url])
        self.assertTrue(url.endswith("%20"))
        with self.reader() as kb:
            contexts = [json.loads(r["context_json"]) for r in kb.all("SELECT context_json FROM review_task WHERE trigger='DOI_NOT_FOUND_TRAILING_CHARACTERS'")]
        self.assertEqual(sorted(c["trimmed_alternative_for_human_review"] for c in contexts), ["10.99999/abc", "10.99999/x"])
        self.assertTrue(all(c["automatic_lookup_of_alternative"] is False for c in contexts))


class DataciteTests(KBTest):
    def fetch(self, ft, doi):
        with self.writer() as kb:
            return self.service(kb, ft).fetch([structured(doi)])[0]

    def test_mf_11_publisher_forms(self):
        """IDs: MF-11. Publisher as a string and as an object are both parsed; affiliations in both forms."""
        for i, publisher in enumerate(("Synthetic Repository", {"name": "Synthetic Repository", "publisherIdentifier": "https://ror.org/000000000"})):
            doi = f"10.99999/dc.{i}"
            ft = FixtureTransport()
            self.route_ra(ft, doi, "DataCite")
            self.add_datacite(ft, doi, dc_doi(doi, publisher=publisher, titles=[{"title": "Dataset"}],
                                              creators=[{"name": "Doe, J", "nameType": "Personal", "affiliation": ["Uni"] if i == 0 else [{"name": "Uni"}]}],
                                              types={"resourceTypeGeneral": "Dataset"}))
            result = self.fetch(ft, doi)
            self.assertEqual(result["status"], "FOUND")
            self.assertEqual(result["coverage"], ["NOT_SUPPORTED_BY_PROVIDER", "NO_SIGNAL_REPORTED"])
        with self.reader() as kb:
            pubs = [json.loads(r["value_json"]) for r in kb.all("SELECT value_json FROM metadata_claim WHERE field='publisher' ORDER BY rowid")]
            affs = [json.loads(r["value_json"])["affiliations"] for r in kb.all("SELECT value_json FROM metadata_claim WHERE field='author' ORDER BY rowid")]
        self.assertEqual([p["form"] for p in pubs], ["string", "object"])
        self.assertEqual(affs, [["Uni"], ["Uni"]])

    def test_mf_12_version_and_supplement_relations(self):
        """IDs: MF-12. IsNewVersionOf yields a NEW_VERSION event on the older DOI plus relation claims; IsSupplementTo is a relation only."""
        doi = "10.99999/dc.v2"
        ft = FixtureTransport()
        self.route_ra(ft, doi, "DataCite")
        self.add_datacite(ft, doi, dc_doi(doi, relatedIdentifiers=[
            {"relationType": "IsNewVersionOf", "relatedIdentifier": "10.99999/dc.v1", "relatedIdentifierType": "DOI"},
            {"relationType": "IsSupplementTo", "relatedIdentifier": "10.99999/article.1", "relatedIdentifierType": "DOI"}]))
        self.fetch(ft, doi)
        with self.reader() as kb:
            events = kb.all("SELECT subject_value, event_type, notice_doi, reported_by FROM integrity_event")
            relations = [json.loads(r["value_json"])["name"] for r in kb.all("SELECT value_json FROM metadata_claim WHERE field='relation'")]
        self.assertEqual(events, [{"subject_value": "10.99999/dc.v1", "event_type": "NEW_VERSION", "notice_doi": "10.99999/dc.v2",
                                   "reported_by": "DATACITE_RELATION"}])
        self.assertEqual(sorted(relations), ["IsNewVersionOf", "IsSupplementTo"])

    def test_mf_13_datacite_404(self):
        """IDs: MF-13."""
        doi = "10.99999/dc.404"
        ft = FixtureTransport()
        self.route_ra(ft, doi, "DataCite")
        url = self.add_datacite(ft, doi, resp(404, {"errors": [{"status": "404"}]}, "x"))
        ft.routes[url][0].final_url = url
        result = self.fetch(ft, doi)
        self.assertEqual(result["status"], "NOT_FOUND")
        self.assertEqual(result["coverage"], ["SUBJECT_NOT_FOUND"])


class ContentNegotiationTests(KBTest):
    def cn_fetch(self, ft, doi):
        with self.writer() as kb:
            return self.service(kb, ft).fetch([structured(doi)])[0]

    def test_mf_14_redirect_to_html(self):
        """IDs: MF-14. Content negotiation redirected to an HTML page (non-allowlisted host): NO_METADATA."""
        doi = "10.99999/cn.1"
        ft = FixtureTransport()
        self.route_ra(ft, doi, "mEDRA")
        url = cn.build_url(doi)
        ft.add(url, Response(200, {"content-type": "text/html"}, b"<html></html>", "http://www.doi.org/hb.html"))
        result = self.cn_fetch(ft, doi)
        self.assertEqual(result["status"], "NO_METADATA")

    def test_mf_14_html_on_allowlisted_host(self):
        """IDs: MF-14. A 200 text/html answer (no redirect) is NO_METADATA, never parsed as metadata."""
        doi = "10.99999/cn.2"
        ft = FixtureTransport()
        self.route_ra(ft, doi, "mEDRA")
        url = cn.build_url(doi)
        ft.add(url, Response(200, {"content-type": "text/html"}, b"<html></html>", url))
        result = self.cn_fetch(ft, doi)
        self.assertEqual((result["status"], result["reason"]), ("NO_METADATA", "NOT_CSL_JSON"))

    def test_mf_15_no_content(self):
        """IDs: MF-15. HTTP 204: NO_METADATA."""
        doi = "10.99999/cn.3"
        ft = FixtureTransport()
        self.route_ra(ft, doi, "mEDRA")
        url = cn.build_url(doi)
        ft.add(url, Response(204, {}, b"", url))
        self.assertEqual(self.cn_fetch(ft, doi)["status"], "NO_METADATA")

    def test_csl_json_accepted(self):
        """IDs: CN-OK-01. A 200 CSL JSON answer becomes lower-fidelity claims."""
        doi = "10.99999/cn.4"
        ft = FixtureTransport()
        self.route_ra(ft, doi, "mEDRA")
        url = cn.build_url(doi)
        ft.add(url, Response(200, {"content-type": "application/vnd.citationstyles.csl+json"}, json.dumps({"title": "CSL title", "type": "article"}).encode(), url))
        self.assertEqual(self.cn_fetch(ft, doi)["status"], "FOUND")


class StoreLevelDoiTests(KBTest):
    def test_mf_18_33_near_duplicate_trailing_space(self):
        """IDs: MF-18.33, N28. Keys differing only by a trailing space: warning + review task, separate assertions, exact-key lookup only."""
        from tests.helpers import rv_item, write_rv_export
        import os
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_a", dois=[("10.99999/x", "10.99999/x")]),
                                                                rv_item("cri_b", dois=[("10.99999/x ", "10.99999/x")])])
        from scivetra_kb.metadata.rv_import import import_corpus_export
        from scivetra_kb.metadata.store import MetadataStore
        with self.writer() as kb:
            import_corpus_export(kb, export, workspace_label="ws1")
            store = MetadataStore(kb)
            warnings = kb.all("SELECT kind, key_a, key_b FROM identifier_warning")
            tasks = kb.all("SELECT trigger FROM review_task WHERE trigger='DOI_NEAR_DUPLICATE_TRAILING_SPACE'")
            a = store.papers_for_doi_key("10.99999/x")
            b = store.papers_for_doi_key("10.99999/x ")
            papers = kb.one("SELECT count(*) n FROM global_paper")["n"]
        self.assertEqual(warnings, [{"kind": "NEAR_DUPLICATE_TRAILING_SPACE", "key_a": "10.99999/x", "key_b": "10.99999/x "}])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(papers, 2)
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
