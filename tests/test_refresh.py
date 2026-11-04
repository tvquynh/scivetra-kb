import unittest

from tests.helpers import KBTest, cr_work, structured
from scivetra_kb.metadata.transport import FixtureTransport


class RefreshTests(KBTest):
    doi = "10.99999/rf.1"

    def test_refresh_staleness_uses_last_check(self):
        """IDs: REFRESH-01. Refresh re-checks only stale records; an UNCHANGED re-check counts as fresh for the next run."""
        ft = FixtureTransport()
        self.route_ra(ft, self.doi)
        self.add_crossref(ft, self.doi, cr_work(self.doi, title=["T"]))
        with self.writer() as kb:
            service = self.service(kb, ft)
            service.fetch([structured(self.doi)])
            self.assertEqual(service.refresh(older_than_days=90)["skipped_recent"], 1)
            self.clock.advance(days=100)
            first = service.refresh(older_than_days=90)
            second = service.refresh(older_than_days=90)
        self.assertEqual((first["checked"], first["unchanged"]), (1, 1))
        self.assertEqual((second["checked"], second["skipped_recent"]), (0, 1))

    def test_refresh_detects_change_without_overwriting(self):
        """IDs: REFRESH-02, MF-09. A refreshed record with new content creates a new snapshot and a change event; earlier claims remain."""
        ft = FixtureTransport()
        self.route_ra(ft, self.doi)
        self.add_crossref(ft, self.doi, cr_work(self.doi, title=["Before"]), cr_work(self.doi, title=["After"]))
        with self.writer() as kb:
            service = self.service(kb, ft)
            service.fetch([structured(self.doi)])
            self.clock.advance(days=100)
            report = service.refresh(older_than_days=90)
            titles = [r["value_json"] for r in kb.all("SELECT value_json FROM metadata_claim WHERE field='title' ORDER BY rowid")]
        self.assertEqual((report["new_snapshot"], report["changed"]), (1, 1))
        self.assertEqual(titles, ['"Before"', '"After"'])

    def test_integrity_only_refresh_is_crossref_only(self):
        """IDs: REFRESH-03. --integrity-only refreshes Crossref records only (DataCite has no retraction field)."""
        from tests.helpers import dc_doi
        ft = FixtureTransport()
        self.route_ra(ft, self.doi)
        self.add_crossref(ft, self.doi, cr_work(self.doi, title=["T"]))
        self.route_ra(ft, "10.99999/rf.dc", "DataCite")
        self.add_datacite(ft, "10.99999/rf.dc", dc_doi("10.99999/rf.dc", titles=[{"title": "D"}]))
        with self.writer() as kb:
            service = self.service(kb, ft)
            service.fetch([structured(self.doi)])
            service.fetch([structured("10.99999/rf.dc")])
            self.clock.advance(days=100)
            report = service.refresh(older_than_days=90, integrity_only=True)
        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["items"][0]["provider"], "CROSSREF")


if __name__ == "__main__":
    unittest.main()
