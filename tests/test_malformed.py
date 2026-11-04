"""S02 v02 / review finding S02-F01: malformed provider JSON fails closed with provenance.

Every case is synthetic (DOI prefix 10.99999). Parser-level tests check the controlled result and exact field
path. Service-level tests check what is persisted: the request reservation and completion, a ProviderSnapshot
with ``REFUSED_UNSAFE_RESPONSE`` / ``MALFORMED_FIELD_TYPE`` and the exact raw bytes in CAS, ``CHECK_FAILED``
coverage carrying the field path, and no MetadataClaim, IntegrityEvent, counter-evidence or identity assertion.
"""
import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.helpers import FIXTURES, KBTest, cr_work, dc_doi, resp, structured
from scivetra_kb import cli
from scivetra_kb.metadata import cn, crossref, datacite
from scivetra_kb.metadata.integrity import IntegrityService
from scivetra_kb.metadata.store import MetadataStore
from scivetra_kb.metadata.transport import FixtureTransport, Response

CR_DOI, DC_DOI = "10.99999/mal.cr", "10.99999/mal.dc"
CR_BASE = cr_work(CR_DOI, title=["Synthetic"], deposited={"date-time": "2025-01-01T00:00:00Z"},
                  license=[{"URL": "https://example.invalid/l", "start": {"date-parts": [[2024, 1, 1]]}}],
                  author=[{"family": "A", "affiliation": [{"name": "Lab"}]}],
                  **{"updated-by": [{"DOI": "10.99999/mal.notice", "type": "retraction", "source": "publisher", "updated": {"date-parts": [[2025, 1, 1]]}}]})
DC_BASE = dc_doi(DC_DOI, titles=[{"title": "Data"}], types={"resourceTypeGeneral": "Dataset"},
                 relatedIdentifiers=[{"relationType": "IsSupplementTo", "relatedIdentifier": "10.99999/x", "relatedIdentifierType": "DOI"}])


def cr_case(mutate):
    doc = copy.deepcopy(CR_BASE)
    mutate(doc["message"])
    return doc


def dc_case(mutate):
    doc = copy.deepcopy(DC_BASE)
    mutate(doc["data"]["attributes"])
    return doc


def setitem(key, value):
    def apply(target):
        target[key] = value
    return apply


# (case id, mutated document, expected field path)
CROSSREF_CASES = [
    ("S02-MALFORMED-CR-DEPOSITED", cr_case(setitem("deposited", "2026-11-01")), "/message/deposited"),
    ("S02-MALFORMED-CR-LICENSE-START", cr_case(lambda m: m["license"][0].__setitem__("start", "2026-01-01")), "/message/license/0/start"),
    ("S02-MALFORMED-CR-UPDATE-DATE", cr_case(lambda m: m["updated-by"][0].__setitem__("updated", "2026")), "/message/updated-by/0/updated"),
    ("S02-MALFORMED-CR-SUBJECT-INT", cr_case(setitem("DOI", 42)), "/message/DOI"),
    ("S02-MALFORMED-CR-SUBJECT-MISSING", cr_case(lambda m: m.pop("DOI")), "/message/DOI"),
    ("S02-MALFORMED-CR-TITLE-ELEMENT", cr_case(setitem("title", ["ok", 7])), "/message/title/1"),
    ("S02-MALFORMED-CR-TITLE-SCALAR", cr_case(setitem("title", "Not a list")), "/message/title"),
    ("S02-MALFORMED-CR-AUTHOR-ELEMENT", cr_case(setitem("author", ["Doe, J."])), "/message/author/0"),
    ("S02-MALFORMED-CR-AFFILIATION-NAME", cr_case(lambda m: m["author"][0]["affiliation"][0].__setitem__("name", 5)), "/message/author/0/affiliation/0/name"),
    ("S02-MALFORMED-CR-UPDATES-ELEMENT", cr_case(setitem("updated-by", ["10.99999/n"])), "/message/updated-by/0"),
    ("S02-MALFORMED-CR-UPDATES-SCALAR", cr_case(setitem("updated-by", True)), "/message/updated-by"),
    ("S02-MALFORMED-CR-RELATION-LIST", cr_case(setitem("relation", {"is-preprint-of": "10.99999/p"})), "/message/relation/is-preprint-of"),
    ("S02-MALFORMED-CR-DATEPARTS", cr_case(setitem("issued", {"date-parts": [["2021"]]})), "/message/issued/date-parts/0/0"),
    ("S02-MALFORMED-CR-ISSNTYPE-ELEMENT", cr_case(setitem("issn-type", ["1234-5678"])), "/message/issn-type/0"),
    ("S02-MALFORMED-CR-RECORD-ID-BOOL", cr_case(lambda m: m["updated-by"][0].__setitem__("record-id", True)), "/message/updated-by/0/record-id"),
]
DATACITE_CASES = [
    ("S02-MALFORMED-DC-TYPES", dc_case(setitem("types", "Dataset")), "/data/attributes/types"),
    ("S02-MALFORMED-DC-RELATED-BOOL", dc_case(setitem("relatedIdentifiers", True)), "/data/attributes/relatedIdentifiers"),
    ("S02-MALFORMED-DC-SUBJECT-INT", dc_case(setitem("doi", 42)), "/data/attributes/doi"),
    ("S02-MALFORMED-DC-RELATED-ELEMENT", dc_case(setitem("relatedIdentifiers", ["10.99999/x"])), "/data/attributes/relatedIdentifiers/0"),
    ("S02-MALFORMED-DC-TITLE-FIELD", dc_case(setitem("titles", [{"title": ["nested"]}])), "/data/attributes/titles/0/title"),
    ("S02-MALFORMED-DC-PUBLISHER-LIST", dc_case(setitem("publisher", ["Repo"])), "/data/attributes/publisher"),
    ("S02-MALFORMED-DC-METADATA-VERSION", dc_case(setitem("metadataVersion", "28")), "/data/attributes/metadataVersion"),
]


class ParserLevelTests(unittest.TestCase):
    def test_crossref_cases(self):
        """IDs: S02-MALFORMED-PARSER, S02-MALFORMED-CR-DEPOSITED, S02-MALFORMED-CR-LICENSE-START, S02-MALFORMED-CR-UPDATE-DATE, S02-MALFORMED-CR-SUBJECT-INT. Each malformed Crossref field gives REFUSED / MALFORMED_FIELD_TYPE with the exact JSON path; no exception escapes."""
        for case_id, doc, path in CROSSREF_CASES:
            with self.subTest(case=case_id):
                result = crossref.parse_response(json.dumps(doc).encode(), CR_DOI, CR_DOI)
                self.assertEqual((result["status"], result["reason"], result["field_path"]), ("REFUSED", "MALFORMED_FIELD_TYPE", path))
                self.assertEqual((result["claims"], result["events"]), ([], []))

    def test_datacite_cases(self):
        """IDs: S02-MALFORMED-PARSER, S02-MALFORMED-DC-TYPES, S02-MALFORMED-DC-RELATED-BOOL, S02-MALFORMED-DC-SUBJECT-INT. Each malformed DataCite field gives REFUSED / MALFORMED_FIELD_TYPE with the exact JSON path."""
        for case_id, doc, path in DATACITE_CASES:
            with self.subTest(case=case_id):
                result = datacite.parse_response(json.dumps(doc).encode(), DC_DOI, DC_DOI)
                self.assertEqual((result["status"], result["reason"], result["field_path"]), ("REFUSED", "MALFORMED_FIELD_TYPE", path))
                self.assertEqual((result["claims"], result["events"]), ([], []))

    def test_envelope_level_types(self):
        """IDs: S02-MALFORMED-PARSER, S02-MALFORMED-ENVELOPE. Wrong root/message/data/attributes types are controlled refusals."""
        cases = [
            (crossref, [1, 2], CR_DOI, "UNEXPECTED_ENVELOPE"),
            (crossref, {"status": "ok", "message-type": "work", "message": ["x"]}, CR_DOI, "UNEXPECTED_ENVELOPE"),
            (datacite, [], DC_DOI, "MALFORMED_FIELD_TYPE"),
            (datacite, {"data": ["x"]}, DC_DOI, "MALFORMED_FIELD_TYPE"),
            (datacite, {"data": {"type": "dois", "attributes": "x"}}, DC_DOI, "MALFORMED_FIELD_TYPE"),
            (datacite, {"data": {"type": "dois", "attributes": {}}}, DC_DOI, "MALFORMED_FIELD_TYPE"),
        ]
        for module, doc, doi, reason in cases:
            with self.subTest(doc=json.dumps(doc)[:40]):
                result = module.parse_response(json.dumps(doc).encode(), doi, doi)
                self.assertEqual((result["status"], result["reason"]), ("REFUSED", reason))

    def test_csl_root_list_and_field_types(self):
        """IDs: S02-MALFORMED-CN-ROOT, S02-MALFORMED-PARSER. CSL JSON root [] (and a wrong-typed title) is REFUSED / MALFORMED_FIELD_TYPE."""
        for body, path in ((b"[]", "/"), (b'"x"', "/"), (json.dumps({"title": ["x"]}).encode(), "/title")):
            with self.subTest(body=body):
                result = cn.parse_response(200, cn.ACCEPT, body)
                self.assertEqual((result["status"], result["reason"], result["field_path"]), ("REFUSED", "MALFORMED_FIELD_TYPE", path))

    def test_valid_and_null_optional_fields_still_accepted(self):
        """IDs: S02-MALFORMED-POSITIVE. Well-formed responses, null optional fields and [[null]] date-parts are still FOUND (no over-rejection)."""
        ok = crossref.parse_response(json.dumps(CR_BASE).encode(), CR_DOI, CR_DOI)
        self.assertEqual(ok["status"], "FOUND")
        nulls = cr_case(lambda m: m.update(deposited=None, subtitle=None, issued={"date-parts": [[None]]}, author=[{"family": "A", "affiliation": []}]))
        self.assertEqual(crossref.parse_response(json.dumps(nulls).encode(), CR_DOI, CR_DOI)["status"], "FOUND")
        member_int = cr_case(setitem("member", 78))
        self.assertEqual(crossref.parse_response(json.dumps(member_int).encode(), CR_DOI, CR_DOI)["status"], "FOUND")
        self.assertEqual(datacite.parse_response(json.dumps(DC_BASE).encode(), DC_DOI, DC_DOI)["status"], "FOUND")
        self.assertEqual(datacite.parse_response(json.dumps(dc_case(setitem("publisher", {"name": "Repo"}))).encode(), DC_DOI, DC_DOI)["status"], "FOUND")
        self.assertEqual(cn.parse_response(200, cn.ACCEPT, b'{"title": "t"}')["status"], "FOUND")


class ServiceLevelTests(KBTest):
    def fetch(self, provider, doi, *docs):
        ft = FixtureTransport()
        url = crossref.build_url(doi) if provider == "CROSSREF" else datacite.build_url(doi)
        ft.routes[url] = [resp(200, d, url) if not isinstance(d, Response) else d for d in docs]
        with self.writer() as kb:
            service = self.service(kb, ft)
            return [service.fetch_provider(provider, structured(doi)) for _ in docs]

    def assert_failed_closed(self, result, doc, path):
        body = json.dumps(doc).encode("utf-8")
        self.assertEqual((result["status"], result["reason"], result["field_path"]), ("REFUSED_UNSAFE_RESPONSE", "MALFORMED_FIELD_TYPE", path))
        self.assertEqual(result["coverage"], ["CHECK_FAILED"])
        with self.reader() as kb:
            snap = kb.one("SELECT * FROM provider_snapshot WHERE snapshot_id=?", (result["snapshot_id"],))
            self.assertEqual((snap["outcome"], snap["outcome_reason"], snap["http_status"]), ("REFUSED_UNSAFE_RESPONSE", "MALFORMED_FIELD_TYPE", 200))
            self.assertEqual(snap["response_bytes_sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(kb.cas.read(snap["response_bytes_sha256"]), body)
            cov = kb.one("SELECT * FROM integrity_coverage WHERE snapshot_id=?", (snap["snapshot_id"],))
            self.assertEqual(cov["outcome"], "CHECK_FAILED")
            scope = json.loads(cov["scope_note_json"])
            self.assertEqual((scope["reason"], scope["field_path"]), ("MALFORMED_FIELD_TYPE", path))
            for table in ("metadata_claim", "integrity_event", "integrity_counter_evidence", "identity_assertion", "integrity_event_sighting"):
                self.assertEqual(kb.one(f"SELECT count(*) n FROM {table}")["n"], 0, table)
            reservations = kb.all("SELECT r.reservation_id, c.outcome, c.http_status FROM request_reservation r "
                                  "LEFT JOIN request_completion c ON c.reservation_id=r.reservation_id")
            self.assertEqual([(r["outcome"], r["http_status"]) for r in reservations], [("HTTP", 200)])

    def test_review_cases_end_to_end(self):
        """IDs: S02-MALFORMED-SERVICE, S02-MALFORMED-CR-DEPOSITED, S02-MALFORMED-CR-LICENSE-START, S02-MALFORMED-CR-UPDATE-DATE, S02-MALFORMED-CR-SUBJECT-INT, S02-MALFORMED-DC-TYPES, S02-MALFORMED-DC-RELATED-BOOL, S02-MALFORMED-DC-SUBJECT-INT. Each review case persists reservation + snapshot + exact raw bytes + CHECK_FAILED coverage, and no claim/event/counter-evidence/assertion."""
        selected = [c for c in CROSSREF_CASES if c[0] in ("S02-MALFORMED-CR-DEPOSITED", "S02-MALFORMED-CR-LICENSE-START",
                                                          "S02-MALFORMED-CR-UPDATE-DATE", "S02-MALFORMED-CR-SUBJECT-INT")]
        selected += [c for c in DATACITE_CASES if c[0] in ("S02-MALFORMED-DC-TYPES", "S02-MALFORMED-DC-RELATED-BOOL", "S02-MALFORMED-DC-SUBJECT-INT")]
        for case_id, doc, path in selected:
            with self.subTest(case=case_id):
                self.tearDown()
                self.setUp()
                provider, doi = ("CROSSREF", CR_DOI) if case_id.startswith("S02-MALFORMED-CR") else ("DATACITE", DC_DOI)
                result = self.fetch(provider, doi, doc)[0]
                self.assert_failed_closed(result, doc, path)

    def test_nested_list_element_cases_end_to_end(self):
        """IDs: S02-MALFORMED-SERVICE, S02-MALFORMED-NESTED. Nested list-element type errors fail closed the same way."""
        for case_id, doc, path in [c for c in CROSSREF_CASES if c[0] in ("S02-MALFORMED-CR-AUTHOR-ELEMENT", "S02-MALFORMED-CR-UPDATES-ELEMENT")] + \
                                  [c for c in DATACITE_CASES if c[0] == "S02-MALFORMED-DC-RELATED-ELEMENT"]:
            with self.subTest(case=case_id):
                self.tearDown()
                self.setUp()
                provider, doi = ("CROSSREF", CR_DOI) if "-CR-" in case_id else ("DATACITE", DC_DOI)
                self.assert_failed_closed(self.fetch(provider, doi, doc)[0], doc, path)

    def test_valid_recheck_after_malformed_and_history_kept(self):
        """IDs: S02-MALFORMED-RECHECK. Malformed then valid: a new FOUND snapshot with claims and events; the malformed snapshot and its coverage remain unchanged."""
        bad = cr_case(setitem("deposited", "2026-11-01"))
        first, second = self.fetch("CROSSREF", CR_DOI, bad, CR_BASE)
        self.assertEqual((first["status"], second["status"]), ("REFUSED_UNSAFE_RESPONSE", "FOUND"))
        with self.reader() as kb:
            outcomes = [r["outcome"] for r in kb.all("SELECT outcome FROM provider_snapshot WHERE provider='CROSSREF' ORDER BY rowid")]
            coverage = [r["outcome"] for r in kb.all("SELECT outcome FROM integrity_coverage ORDER BY rowid")]
            claims = kb.one("SELECT count(*) n FROM metadata_claim WHERE snapshot_id=?", (second["snapshot_id"],))["n"]
            bad_claims = kb.one("SELECT count(*) n FROM metadata_claim WHERE snapshot_id=?", (first["snapshot_id"],))["n"]
        self.assertEqual(outcomes, ["REFUSED_UNSAFE_RESPONSE", "FOUND"])
        self.assertEqual(coverage, ["CHECK_FAILED", "SIGNAL_REPORTED"])
        self.assertGreater(claims, 0)
        self.assertEqual(bad_claims, 0)

    def test_repeated_malformed_is_idempotent(self):
        """IDs: S02-MALFORMED-IDEMPOTENT. The same malformed bytes twice reuse one snapshot (UNCHANGED check) and one coverage row."""
        bad = cr_case(setitem("deposited", "2026-11-01"))
        first, second = self.fetch("CROSSREF", CR_DOI, bad, bad)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertFalse(second["new_snapshot"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM integrity_coverage")["n"], 1)
            self.assertEqual(kb.one("SELECT count(*) n FROM request_reservation")["n"], 2)

    def test_malformed_after_retraction_keeps_alert(self):
        """IDs: S02-MALFORMED-INTEGRITY, N06c. A malformed later response never closes or contradicts an earlier retraction report."""
        bad = cr_case(setitem("deposited", "2026-11-01"))
        self.fetch("CROSSREF", CR_DOI, CR_BASE, bad)
        with self.reader() as kb:
            view = IntegrityService(MetadataStore(kb)).view("doi", CR_DOI)
            counter = kb.one("SELECT count(*) n FROM integrity_counter_evidence")["n"]
            events = kb.one("SELECT count(*) n FROM integrity_event")["n"]
        family = view["families"]["RETRACTION"]
        self.assertEqual((family["state"], view["alert"]), ("REPORTED", "OPEN_ALERT"))
        self.assertIn("REPORTING_SOURCE_LATEST_SNAPSHOT_OMITS_REPORT", family["flags"])
        self.assertEqual((counter, events), (0, 1))

    def test_csl_root_list_service(self):
        """IDs: S02-MALFORMED-CN-ROOT, S02-MALFORMED-SERVICE. CSL root [] through the service: user status NO_METADATA, snapshot REFUSED_UNSAFE_RESPONSE / MALFORMED_FIELD_TYPE, raw bytes kept, no claims."""
        doi = "10.99999/mal.cn"
        ft = FixtureTransport()
        url = cn.build_url(doi)
        ft.add(url, Response(200, {"content-type": cn.ACCEPT}, b"[]", url))
        with self.writer() as kb:
            result = self.service(kb, ft).fetch_cn(structured(doi))
            snap = kb.one("SELECT * FROM provider_snapshot WHERE snapshot_id=?", (result["snapshot_id"],))
            claims = kb.one("SELECT count(*) n FROM metadata_claim")["n"]
            raw = kb.cas.read(snap["response_bytes_sha256"])
        self.assertEqual((result["status"], result["snapshot_outcome"], result["reason"], result["field_path"]),
                         ("NO_METADATA", "REFUSED_UNSAFE_RESPONSE", "MALFORMED_FIELD_TYPE", "/"))
        self.assertEqual((raw, claims), (b"[]", 0))


class CliMalformedSmokeTests(unittest.TestCase):
    def test_cli_fetch_malformed_is_controlled_and_live_refused(self):
        """IDs: S02-MALFORMED-CLI, OPS-LIVE-01. Through the CLI, a malformed provider body gives a JSON result (exit 0, REFUSED_UNSAFE_RESPONSE), and --live is still refused."""
        tmp = tempfile.mkdtemp(prefix="rkb-mal-cli-")
        try:
            fixtures = Path(tmp, "fx")
            fixtures.mkdir()
            bad = cr_case(setitem("deposited", "2026-11-01"))
            Path(fixtures, "ra.json").write_text(json.dumps([{"DOI": CR_DOI, "RA": "Crossref"}]), encoding="utf-8")
            Path(fixtures, "bad.json").write_text(json.dumps(bad), encoding="utf-8")
            Path(fixtures, "transport.json").write_text(json.dumps([
                {"url": "https://doi.org/doiRA/" + CR_DOI, "responses": [{"status": 200, "headers": {"content-type": "application/json"}, "body_file": "ra.json"}]},
                {"url": crossref.build_url(CR_DOI), "responses": [{"status": 200, "headers": {"content-type": "application/json"}, "body_file": "bad.json"}]}]),
                encoding="utf-8")
            root = os.path.join(tmp, "kb")

            def run(*argv):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(list(argv))
                return code, json.loads(buffer.getvalue())

            run("init", "--data-root", root)
            code, out = run("metadata", "fetch", "--data-root", root, "--doi", CR_DOI, "--transport-fixtures", str(fixtures))
            self.assertEqual(code, 0)
            self.assertEqual((out["results"][0]["status"], out["results"][0]["field_path"]), ("REFUSED_UNSAFE_RESPONSE", "/message/deposited"))
            code, out = run("metadata", "fetch", "--data-root", root, "--doi", CR_DOI, "--live")
            self.assertEqual((code, out["error"]), (2, "LIVE_ACCESS_NOT_AUTHORIZED"))
            code, out = run("doctor", "--data-root", root)
            self.assertTrue(out["ok"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
