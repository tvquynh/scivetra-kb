import unittest

from tests.helpers import KBTest, operator, resp, structured
from scivetra_kb.metadata import ra
from scivetra_kb.metadata.transport import FixtureTransport


class RoutingTests(KBTest):
    def test_mf_01_mixed_ra_response(self):
        """IDs: MF-01. doiRA array mapping: Crossref, DataCite, Public (OTHER), not found, invalid, unknown."""
        dois = ["10.99999/cr.1", "10.99999/dc.1", "10.99999/pub.1", "10.99999/missing.1", "10.99999/invalid.1", "10.99999/unknown.1"]
        body = [{"DOI": dois[0], "RA": "Crossref"}, {"DOI": dois[1], "RA": "DataCite"}, {"DOI": dois[2], "RA": "Public"},
                {"DOI": dois[3], "status": "DOI does not exist"}, {"DOI": dois[4], "status": "Invalid DOI"}, {"DOI": dois[5], "status": "Unknown"}]
        ft = FixtureTransport()
        url = ra.build_url(dois)
        ft.add(url, resp(200, body, url))
        with self.writer() as kb:
            results = self.service(kb, ft).route([structured(d) for d in dois])
        self.assertEqual([r["ra"] for r in results], ["CROSSREF", "DATACITE", "OTHER", "NOT_FOUND", "INVALID_DOI", "UNKNOWN"])
        self.assertEqual(results[2]["ra_raw"], "Public")
        self.assertEqual(len(ft.calls), 1)

    def test_mf_02_ra_case_insensitive(self):
        """IDs: MF-02."""
        for value in ("CROSSREF", "crossref", "CrossRef"):
            self.assertEqual(ra.map_ra(value), "CROSSREF")
        self.assertEqual(ra.map_ra("DATACITE"), "DATACITE")
        self.assertEqual(ra.map_ra("mEDRA"), "OTHER")

    def test_batches_of_at_most_20(self):
        """IDs: RA-BATCH-01. Batches are bounded at 20 DOIs per doiRA request."""
        dois = [f"10.99999/b.{i}" for i in range(23)]
        ft = FixtureTransport()
        for chunk in (dois[:20], dois[20:]):
            url = ra.build_url(chunk)
            ft.add(url, resp(200, [{"DOI": d, "RA": "Crossref"} for d in chunk], url))
        with self.writer() as kb:
            results = self.service(kb, ft).route([structured(d) for d in dois])
        self.assertEqual(len(ft.calls), 2)
        self.assertTrue(all(r["status"] == "ROUTED" for r in results))
        with self.assertRaises(ValueError):
            ra.build_url(dois[:21])

    def test_comma_in_suffix_cannot_split_batch(self):
        """IDs: RA-ENC-01. A comma inside a DOI is percent-encoded (%2C) in the doiRA list."""
        url = ra.build_url(["10.99999/a,b", "10.99999/c"])
        self.assertEqual(url, "https://doi.org/doiRA/10.99999/a%2Cb,10.99999/c")

    def test_ambiguous_and_invalid_refused_without_request(self):
        """IDs: RA-REFUSE-01, N28. AMBIGUOUS/INVALID inputs are refused before any request; operator trailing space opens a review task."""
        ft = FixtureTransport()
        with self.writer() as kb:
            results = self.service(kb, ft).route([operator("10.99999/x "), structured("11.1/x"), structured("10.99999/a; 10.99998/b")])
            tasks = kb.all("SELECT trigger FROM review_task")
        self.assertEqual([r["status"] for r in results], ["REFUSED"] * 3)
        self.assertEqual(results[0]["reason"], "AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION")
        self.assertEqual(ft.calls, [])
        self.assertEqual([t["trigger"] for t in tasks], ["DOI_TRAILING_SPACE_UNCERTAIN"])

    def test_unknown_or_other_ra_never_sent_to_crossref(self):
        """IDs: RA-ROUTE-01. OTHER/UNKNOWN RAs are UNROUTED, and no request goes to Crossref or DataCite."""
        ft = FixtureTransport()
        for doi, value in (("10.99999/o.1", "Public"), ("10.99999/o.2", "JaLC")):
            self.route_ra(ft, doi, value)
        url = ra.build_url(["10.99999/u.1"])
        ft.add(url, resp(200, [{"DOI": "10.99999/u.1", "status": "Unknown"}], url))
        with self.writer() as kb:
            service = self.service(kb, ft)
            results = [service.fetch([structured(d)])[0] for d in ("10.99999/o.1", "10.99999/o.2", "10.99999/u.1")]
        self.assertEqual([r["status"] for r in results], ["UNROUTED", "UNROUTED", "UNROUTED"])
        self.assertFalse(any("crossref" in c.url or "datacite" in c.url for c in ft.calls))

    def test_route_decision_table(self):
        """IDs: RA-ROUTE-02. Routing table of S01 04 section 3.2."""
        self.assertEqual(ra.route_decision("CROSSREF", "Crossref", allow_cn_probe=False), "CROSSREF")
        self.assertEqual(ra.route_decision("DATACITE", "DataCite", allow_cn_probe=False), "DATACITE")
        self.assertEqual(ra.route_decision("OTHER", "mEDRA", allow_cn_probe=False), "CONTENT_NEGOTIATION")
        self.assertEqual(ra.route_decision("OTHER", "JaLC", allow_cn_probe=False), "UNROUTED")
        self.assertEqual(ra.route_decision("OTHER", "JaLC", allow_cn_probe=True), "CONTENT_NEGOTIATION")
        self.assertEqual(ra.route_decision("UNKNOWN", None, allow_cn_probe=False), "UNROUTED")
        self.assertEqual(ra.route_decision("NOT_FOUND", None, allow_cn_probe=True), "STOP")

    def test_ra_response_mismatch_fails_closed(self):
        """IDs: RA-MISMATCH-01. A doiRA answer for a different DOI or of the wrong length creates no RA claim."""
        ft = FixtureTransport()
        url = ra.build_url(["10.99999/m.1"])
        ft.add(url, resp(200, [{"DOI": "10.99999/other", "RA": "Crossref"}], url))
        with self.writer() as kb:
            result = self.service(kb, ft).route([structured("10.99999/m.1")])[0]
            claims = kb.one("SELECT count(*) n FROM ra_claim")["n"]
            snap = kb.one("SELECT outcome, outcome_reason FROM provider_snapshot WHERE provider='DOI_RA'")
        self.assertEqual(result["status"], "RA_LOOKUP_FAILED")
        self.assertEqual(claims, 0)
        self.assertEqual(snap, {"outcome": "REFUSED_UNSAFE_RESPONSE", "outcome_reason": "RESPONSE_SUBJECT_MISMATCH"})

    def test_ra_cache_ttl(self):
        """IDs: RA-CACHE-01. A fresh RA claim is reused; after the TTL a new lookup happens."""
        ft = FixtureTransport()
        self.route_ra(ft, "10.99999/c.1", "Crossref")
        with self.writer() as kb:
            service = self.service(kb, ft)
            service.route([structured("10.99999/c.1")])
            self.assertTrue(service.route([structured("10.99999/c.1")])[0]["cached"])
            self.clock.advance(days=366)
            self.assertFalse(service.route([structured("10.99999/c.1")])[0]["cached"])
        self.assertEqual(len(ft.calls), 2)


if __name__ == "__main__":
    unittest.main()
