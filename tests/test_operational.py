import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tests.helpers import KBTest, cr_work, resp, rv_item, structured, write_rv_export
from scivetra_kb.core.errors import PolicyRefusal, TransportError
from scivetra_kb.metadata import crossref
from scivetra_kb.metadata.rv_import import import_corpus_export
from scivetra_kb.metadata.transport import (FixtureTransport, GuardedTransport, LiveTransportNotAuthorized, Request, Response,
                                            validate_url)


class RetryAndBudgetTests(KBTest):
    def test_mf_16_backoff_then_success(self):
        """IDs: MF-16, N23. 429 (with Retry-After), 429 (without) then 200: bounded backoff schedule, success on attempt 3."""
        doi = "10.99999/rl.1"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = crossref.build_url(doi)
        ft.add(url, resp(429, "", url, {"retry-after": "7"}), resp(429, "", url, {}), resp(200, cr_work(doi, title=["T"]), url))
        with self.writer() as kb:
            result = self.service(kb, ft).fetch([structured(doi)])[0]
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["requests"], 3)
        self.assertEqual(self.sleeps, [7.0, 4.0])

    def test_mf_16_three_429_pause_provider(self):
        """IDs: MF-16, N23. Three consecutive 429s pause the provider for the rest of the run; later items send no request."""
        dois = ["10.99999/rl.2", "10.99999/rl.3"]
        ft = FixtureTransport()
        for doi in dois:
            self.route_ra(ft, doi)
            url = crossref.build_url(doi)
            ft.add(url, resp(429, "", url, {}))
        with self.writer() as kb:
            service = self.service(kb, ft)
            first = service.fetch([structured(dois[0])])[0]
            second = service.fetch([structured(dois[1])])[0]
            coverage = kb.one("SELECT outcome FROM integrity_coverage")["outcome"]
        self.assertEqual((first["status"], first["reason"]), ("RATE_LIMITED", "PROVIDER_PAUSED_AFTER_3x429"))
        self.assertEqual(coverage, "CHECK_FAILED")
        self.assertEqual(second["status"], "PROVIDER_PAUSED")
        self.assertEqual(len([c for c in ft.calls if "crossref" in c.url]), 3)
        self.assertTrue(all(s <= 60 for s in self.sleeps))

    def test_5xx_retried_and_bounded(self):
        """IDs: N23, OPS-5XX-01. Persistent 5xx gives TRANSIENT_ERROR after 4 attempts; never a negative scientific claim."""
        doi = "10.99999/rl.5"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = crossref.build_url(doi)
        ft.add(url, resp(503, "", url, {}))
        with self.writer() as kb:
            result = self.service(kb, ft).fetch([structured(doi)])[0]
            events = kb.one("SELECT count(*) n FROM integrity_event")["n"]
        self.assertEqual((result["status"], result["requests"]), ("TRANSIENT_ERROR", 4))
        self.assertEqual(result["coverage"], ["CHECK_FAILED"])
        self.assertEqual(events, 0)

    def test_mf_17_403_stops_run(self):
        """IDs: MF-17, N23. A 403 records PERMANENT_ERROR and stops the run; remaining items send nothing."""
        dois = ["10.99999/bl.1", "10.99999/bl.2"]
        ft = FixtureTransport()
        for doi in dois:
            self.route_ra(ft, doi)
            url = crossref.build_url(doi)
            ft.add(url, resp(403, "", url, {}))
        with self.writer() as kb:
            service = self.service(kb, ft)
            results = [service.fetch([structured(d)])[0] for d in dois]
        self.assertEqual(results[0]["status"], "PERMANENT_ERROR")
        self.assertEqual(results[1]["status"], "RUN_STOPPED")
        self.assertEqual(len([c for c in ft.calls if "crossref" in c.url]), 1)

    def test_mf_22_budget_exhausted(self):
        """IDs: MF-22, N23. Budget exhaustion gives BUDGET_EXHAUSTED for the remaining items, with no coverage claimed."""
        dois = [f"10.99999/bu.{i}" for i in range(3)]
        ft = FixtureTransport()
        for doi in dois:
            self.route_ra(ft, doi)
            self.add_crossref(ft, doi, cr_work(doi, title=["T"]))
        with self.writer() as kb:
            service = self.service(kb, ft, max_requests=4)  # 1 RA batch per DOI + 1 Crossref call = 2 per item
            results = [service.fetch([structured(d)])[0] for d in dois]
            reservations = kb.one("SELECT count(*) n FROM request_reservation")["n"]
            coverage = kb.one("SELECT count(*) n FROM integrity_coverage")["n"]
        self.assertEqual([r["status"] for r in results], ["FOUND", "FOUND", "BUDGET_EXHAUSTED"])
        self.assertEqual(reservations, 4)
        self.assertEqual(coverage, 2)

    def test_mf_21_no_doi_no_requests(self):
        """IDs: MF-21, N01. A paper without a DOI makes no metadata request (status NO_DOI)."""
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_nodoi", dois=[], openalex=["https://openalex.org/W1"])])
        ft = FixtureTransport()
        with self.writer() as kb:
            import_corpus_export(kb, export, workspace_label="ws")
            paper = kb.one("SELECT paper_id FROM global_paper")["paper_id"]
            result = self.service(kb, ft).fetch_paper(paper)
        self.assertEqual(result, {"paper_id": paper, "status": "NO_DOI", "requests": 0})
        self.assertEqual(ft.calls, [])


class SecretAndPolicyTests(KBTest):
    def test_mf_20_contact_email_never_persisted(self):
        """IDs: MF-20, N22. A configured contact email reaches only the in-memory request; persisted identity, DB and outputs contain no '@'."""
        doi = "10.99999/se.1"
        email = "operator@example.invalid"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = crossref.build_url(doi, email)
        ft.add(url, resp(200, cr_work(doi, title=["T"]), url))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.writer() as kb:
                result = self.service(kb, ft, contact_email=email).fetch([structured(doi)])[0]
                print(json.dumps(result))
        self.assertEqual(result["status"], "FOUND")
        self.assertIn("mailto=", ft.calls[-1].url)
        db_bytes = open(os.path.join(self.root, "db", "kb.sqlite"), "rb").read()
        self.assertNotIn(b"example.invalid", db_bytes)
        self.assertNotIn("example.invalid", out.getvalue() + err.getvalue())

    def test_request_identity_rejects_contact_data(self):
        """IDs: N22. The store and a DB CHECK both refuse request identities containing contact data."""
        from scivetra_kb.metadata.store import MetadataStore
        from scivetra_kb.core.errors import ValidationError
        with self.writer() as kb:
            with self.assertRaises(ValidationError):
                with kb.transaction():
                    MetadataStore(kb).record_snapshot(provider="CROSSREF", request_identity={"mailto": "a@b.c"}, http_status=200, body=b"{}",
                                                      media_type=None, outcome="FOUND", reason=None, parser_name="x", parser_version="1", run_id=None)
            with self.assertRaises(ValidationError):
                kb.execute("INSERT INTO provider_snapshot (snapshot_id, provider, request_identity_json, request_identity_sha256, outcome, retrieved_at_utc, client_version, parser_name, parser_version, semantic_sha256) "
                           "VALUES ('ps_x','CROSSREF','{\"e\":\"a@b.c\"}', ?, 'FOUND', '2026-01-01T00:00:00.000000Z','v','p','1', ?)", ("0" * 64, "1" * 64))

    def test_url_policy(self):
        """IDs: N22, OPS-POLICY-01. Only allowlisted HTTPS hosts; no user-info, ports, IP literals or fragments."""
        validate_url("https://api.crossref.org/works/10.99999/x")
        for bad, code in (("http://api.crossref.org/works/x", "URL_SCHEME_REFUSED"), ("https://user:pw@api.crossref.org/x", "URL_USERINFO_REFUSED"),
                          ("https://api.crossref.org:8443/x", "URL_PORT_REFUSED"), ("https://127.0.0.1/x", "URL_HOST_REFUSED"),
                          ("https://evil.example/x", "URL_HOST_REFUSED"), ("https://localhost/x", "URL_HOST_REFUSED"),
                          ("https://api.crossref.org/x#f", "URL_FRAGMENT_REFUSED")):
            with self.subTest(url=bad):
                with self.assertRaises(PolicyRefusal) as ctx:
                    validate_url(bad)
                self.assertEqual(ctx.exception.code, code)

    def test_guarded_transport_limits(self):
        """IDs: OPS-POLICY-02. Credential headers, oversized bodies, encoded bodies and unsafe redirects are refused."""
        url = "https://api.crossref.org/works/10.99999/g"
        inner = FixtureTransport({url: [Response(200, {}, b"x" * 11, url)]})
        guard = GuardedTransport(inner, max_response_bytes=10)
        with self.assertRaises(PolicyRefusal):
            guard.send(Request("GET", url, "CROSSREF", "t", {"Cookie": "a=b"}))
        with self.assertRaises(TransportError) as ctx:
            guard.send(Request("GET", url, "CROSSREF", "t"))
        self.assertEqual(ctx.exception.code, "RESPONSE_TOO_LARGE")
        inner.routes[url] = [Response(200, {"content-encoding": "gzip"}, b"x", url)]
        with self.assertRaises(TransportError):
            guard.send(Request("GET", url, "CROSSREF", "t"))
        inner.routes[url] = [Response(200, {}, b"x", "https://169.254.169.254/latest")]
        with self.assertRaises(PolicyRefusal):
            guard.send(Request("GET", url, "CROSSREF", "t"))

    def test_live_transport_not_authorized(self):
        """IDs: OPS-LIVE-01. There is no live transport in S02; constructing one is refused."""
        with self.assertRaises(PolicyRefusal) as ctx:
            LiveTransportNotAuthorized()
        self.assertEqual(ctx.exception.code, "LIVE_ACCESS_NOT_AUTHORIZED")

    def test_oversized_response_refused_end_to_end(self):
        """IDs: OPS-POLICY-03, N23. A provider body above the response cap becomes REFUSED_UNSAFE_RESPONSE / CHECK_FAILED."""
        doi = "10.99999/big.1"
        ft = FixtureTransport()
        self.route_ra(ft, doi)
        url = crossref.build_url(doi)
        ft.add(url, resp(200, b"{" + b" " * 5000 + b"}", url))
        with self.writer() as kb:
            result = self.service(kb, ft, max_response_bytes=4096).fetch([structured(doi)])[0]
        self.assertEqual((result["status"], result["reason"]), ("REFUSED_UNSAFE_RESPONSE", "RESPONSE_TOO_LARGE"))
        self.assertEqual(result["coverage"], ["CHECK_FAILED"])


if __name__ == "__main__":
    unittest.main()
