"""S02 v03 / review finding S02-F04: unsafe scalar content in provider JSON fails closed with provenance.

Covers values that have the right Python type but cannot be persisted safely: escaped unpaired surrogates,
``NaN``/``Infinity`` tokens, numbers that overflow to an infinite float, integers outside SQLite range, and
nesting beyond the decoder bound. Every body is synthetic (DOI prefix 10.99999) and built as exact bytes, so
the CAS round-trip is checked byte for byte.
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

from tests.helpers import KBTest, cr_work, dc_doi, resp, structured
from scivetra_kb import cli
from scivetra_kb.metadata import cn, crossref, datacite, ra, strict_json
from scivetra_kb.metadata.integrity import IntegrityService
from scivetra_kb.metadata.store import MetadataStore
from scivetra_kb.metadata.transport import FixtureTransport, Response

CR_DOI, DC_DOI, CN_DOI = "10.99999/unsafe.cr", "10.99999/unsafe.dc", "10.99999/unsafe.cn"
LONE_HIGH, LONE_LOW = chr(0xD800), chr(0xDC00)
CR_BASE = cr_work(CR_DOI, title=["Synthetic"], member="78",
                  **{"updated-by": [{"DOI": "10.99999/unsafe.notice", "type": "retraction", "source": "publisher",
                                     "updated": {"date-parts": [[2025, 1, 1]]}}]})
DC_BASE = dc_doi(DC_DOI, titles=[{"title": "Data"}], version="1")


def dumps(doc) -> bytes:
    """ASCII JSON: lone surrogates become escapes such as \\ud800, exactly as a provider could send them."""
    return json.dumps(doc, separators=(",", ":")).encode("ascii")


def cr(mutate) -> bytes:
    doc = copy.deepcopy(CR_BASE)
    mutate(doc["message"])
    return dumps(doc)


def dc_token(token: str) -> bytes:
    """DataCite body with ``version`` replaced by a raw JSON token (e.g. 1e9999, NaN)."""
    doc = copy.deepcopy(DC_BASE)
    doc["data"]["attributes"]["version"] = 0
    body = dumps(doc).replace(b'"version":0', b'"version":' + token.encode("ascii"))
    assert token.encode("ascii") in body
    return body


def nested(depth: int):
    value = "leaf"
    for _ in range(depth):
        value = [value]
    return value


# (case id, provider module, body, expected reason, expected field path)
PROVIDER_CASES = [
    ("S02-UNSAFE-SURROGATE", crossref, cr(lambda m: m.__setitem__("title", [LONE_HIGH])), "UNPAIRED_SURROGATE", "/message/title/0"),
    ("S02-UNSAFE-SURROGATE", crossref, cr(lambda m: m["updated-by"][0].__setitem__("DOI", "10.99999/n" + LONE_LOW)),
     "UNPAIRED_SURROGATE", "/message/updated-by/0/DOI"),
    ("S02-UNSAFE-SURROGATE", crossref, cr(lambda m: m.__setitem__("x" + LONE_HIGH, 1)), "UNPAIRED_SURROGATE", "/message"),
    ("S02-UNSAFE-NONFINITE", datacite, dc_token("1e9999"), "NON_FINITE_NUMBER", "/data/attributes/version"),
    ("S02-UNSAFE-NONFINITE", datacite, dc_token("-1e9999"), "NON_FINITE_NUMBER", "/data/attributes/version"),
    ("S02-UNSAFE-NAN-TOKEN", datacite, dc_token("NaN"), "NONSTANDARD_JSON_CONSTANT", "/data/attributes/version"),
    ("S02-UNSAFE-NAN-TOKEN", datacite, dc_token("Infinity"), "NONSTANDARD_JSON_CONSTANT", "/data/attributes/version"),
    ("S02-UNSAFE-NAN-TOKEN", datacite, dc_token("-Infinity"), "NONSTANDARD_JSON_CONSTANT", "/data/attributes/version"),
    ("S02-UNSAFE-INT-RANGE", crossref, cr(lambda m: m.__setitem__("member", 2 ** 63)), "INTEGER_OUT_OF_RANGE", "/message/member"),
    ("S02-UNSAFE-INT-RANGE", crossref, cr(lambda m: m.__setitem__("member", -(10 ** 30))), "INTEGER_OUT_OF_RANGE", "/message/member"),
    ("S02-UNSAFE-DEPTH", crossref, cr(lambda m: m.__setitem__("extra", nested(strict_json.MAX_DEPTH + 5))),
     "NESTING_TOO_DEEP", None),     # path checked by prefix below: it points inside /message/extra
    ("S02-UNSAFE-DEPTH", datacite, b'{"data":' + b"[" * 50000 + b"]" * 50000 + b"}", "NESTING_TOO_DEEP", None),
    ("S02-UNSAFE-PARSER", crossref, b"\xff\xfe not utf-8", "UNPARSEABLE_RESPONSE", None),
]


class DecoderTests(unittest.TestCase):
    def test_provider_parsers_refuse_unsafe_scalars(self):
        """IDs: S02-UNSAFE-PARSER, S02-UNSAFE-SURROGATE, S02-UNSAFE-NONFINITE, S02-UNSAFE-NAN-TOKEN, S02-UNSAFE-INT-RANGE, S02-UNSAFE-DEPTH. Crossref/DataCite parsers return REFUSED with a stable reason and JSON-pointer path (a bad object key is reported at its object, never echoed); no exception escapes."""
        for case_id, module, body, reason, path in PROVIDER_CASES:
            doi = CR_DOI if module is crossref else DC_DOI
            with self.subTest(case=case_id, reason=reason, path=path):
                result = module.parse_response(body, doi, doi)
                self.assertEqual((result["status"], result["reason"]), ("REFUSED", reason))
                self.assertEqual((result["claims"], result["events"]), ([], []))
                if case_id == "S02-UNSAFE-DEPTH" and module is crossref:
                    self.assertTrue(result["field_path"].startswith("/message/extra/0/0"), result["field_path"])
                else:
                    self.assertEqual(result["field_path"], path)
                json.dumps(result, ensure_ascii=False).encode("utf-8")   # the result itself is always serialisable

    def test_ra_and_cn_parsers_refuse_unsafe_scalars(self):
        """IDs: S02-UNSAFE-RA, S02-UNSAFE-CN, S02-UNSAFE-PARSER. doiRA and CSL parsers use the same strict boundary and produce no items or claims."""
        ra_cases = [(dumps([{"DOI": CR_DOI, "RA": "Cross" + LONE_HIGH}]), "UNPAIRED_SURROGATE", "/0/RA"),
                    (b'[{"DOI":"10.99999/unsafe.cr","RA":NaN}]', "NONSTANDARD_JSON_CONSTANT", "/0/RA"),
                    (b"[" * 50000, "NESTING_TOO_DEEP", None)]
        for body, reason, path in ra_cases:
            with self.subTest(parser="ra", reason=reason):
                result = ra.parse_response(body, [(CR_DOI, CR_DOI)])
                self.assertEqual((result["status"], result["reason"], result["field_path"], result["items"]), ("REFUSED", reason, path, []))
        cn_cases = [(dumps({"title": LONE_HIGH}), "UNPAIRED_SURROGATE", "/title"),
                    (b'{"title":"t","issued":{"date-parts":[[1e9999]]}}', "NON_FINITE_NUMBER", "/issued/date-parts/0/0"),
                    (b"{", "UNPARSEABLE_RESPONSE", None)]
        for body, reason, path in cn_cases:
            with self.subTest(parser="cn", reason=reason):
                result = cn.parse_response(200, cn.ACCEPT, body)
                self.assertEqual((result["status"], result["reason"], result["field_path"], result["claims"]), ("REFUSED", reason, path, []))

    def test_valid_edge_values_still_accepted(self):
        """IDs: S02-UNSAFE-POSITIVE. Paired surrogate escapes (emoji), non-ASCII text, the int64 boundary, large finite floats and moderate nesting are still FOUND."""
        body = cr(lambda m: m.update(title=["Caf" + chr(0xE9) + " " + chr(0x1F600)], member=2 ** 63 - 1, score=1.7e308,
                                     extra=nested(20)))
        self.assertIn(b"\\ud83d\\ude00", body)             # the emoji travels as a valid surrogate pair escape
        result = crossref.parse_response(body, CR_DOI, CR_DOI)
        self.assertEqual(result["status"], "FOUND")
        self.assertIn("Caf" + chr(0xE9) + " " + chr(0x1F600), [c["value"] for c in result["claims"] if c["field"] == "title"])
        self.assertEqual(datacite.parse_response(dumps(DC_BASE), DC_DOI, DC_DOI)["status"], "FOUND")
        self.assertEqual(strict_json.decode(b'{"a":-9223372036854775808,"b":0.0,"c":-0.0,"d":null}')["a"], -(2 ** 63))

    def test_reason_codes_are_stable_and_documented(self):
        """IDs: S02-UNSAFE-PARSER. The reason-code set is fixed and every code is documented in the module docstring."""
        self.assertEqual(strict_json.REASONS, ("UNPARSEABLE_RESPONSE", "NONSTANDARD_JSON_CONSTANT", "NON_FINITE_NUMBER",
                                               "INTEGER_OUT_OF_RANGE", "UNPAIRED_SURROGATE", "NESTING_TOO_DEEP"))
        for code in strict_json.REASONS:
            self.assertIn("``" + code + "``", strict_json.__doc__)


class ServiceTests(KBTest):
    def fetch(self, provider, doi, *bodies):
        ft = FixtureTransport()
        url = crossref.build_url(doi) if provider == "CROSSREF" else datacite.build_url(doi)
        ft.routes[url] = [Response(200, {"content-type": "application/json"}, b, url) for b in bodies]
        with self.writer() as kb:
            service = self.service(kb, ft)
            return [service.fetch_provider(provider, structured(doi)) for _ in bodies]

    def assert_failed_closed(self, result, body, reason, path):
        self.assertEqual((result["status"], result["reason"], result.get("field_path")), ("REFUSED_UNSAFE_RESPONSE", reason, path))
        self.assertEqual(result["coverage"], ["CHECK_FAILED"])
        with self.reader() as kb:
            snap = kb.one("SELECT * FROM provider_snapshot WHERE snapshot_id=?", (result["snapshot_id"],))
            self.assertEqual((snap["outcome"], snap["outcome_reason"], snap["http_status"]), ("REFUSED_UNSAFE_RESPONSE", reason, 200))
            self.assertEqual(snap["response_bytes_sha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(kb.cas.read(snap["response_bytes_sha256"]), body)
            cov = kb.one("SELECT * FROM integrity_coverage WHERE snapshot_id=?", (snap["snapshot_id"],))
            self.assertEqual(cov["outcome"], "CHECK_FAILED")
            scope = json.loads(cov["scope_note_json"])
            self.assertEqual((scope["reason"], scope.get("field_path")), (reason, path))
            for table in ("metadata_claim", "integrity_event", "integrity_event_sighting", "integrity_counter_evidence", "identity_assertion"):
                self.assertEqual(kb.one(f"SELECT count(*) n FROM {table}")["n"], 0, table)
            rows = kb.all("SELECT c.outcome, c.http_status FROM request_reservation r LEFT JOIN request_completion c ON c.reservation_id=r.reservation_id")
            self.assertEqual([(r["outcome"], r["http_status"]) for r in rows], [("HTTP", 200)])

    def test_unsafe_provider_bodies_end_to_end(self):
        """IDs: S02-UNSAFE-SERVICE, S02-UNSAFE-SURROGATE, S02-UNSAFE-NONFINITE, S02-UNSAFE-NAN-TOKEN, S02-UNSAFE-INT-RANGE, S02-UNSAFE-DEPTH. Each unsafe body persists reservation + completion, one REFUSED_UNSAFE_RESPONSE snapshot with byte-exact raw bytes and CHECK_FAILED coverage (reason + path), and no claim, event, sighting, counter-evidence or identity assertion."""
        for case_id, module, body, reason, path in PROVIDER_CASES:
            if path is None and case_id == "S02-UNSAFE-DEPTH" and module is crossref:
                continue                                       # exact-path depth case is covered at parser level
            with self.subTest(case=case_id, reason=reason, path=path):
                self.tearDown()
                self.setUp()
                provider, doi = ("CROSSREF", CR_DOI) if module is crossref else ("DATACITE", DC_DOI)
                self.assert_failed_closed(self.fetch(provider, doi, body)[0], body, reason, path)

    def test_repeated_unsafe_is_idempotent(self):
        """IDs: S02-UNSAFE-IDEMPOTENT. The same unsafe bytes twice reuse one snapshot and one coverage row; both requests stay accounted."""
        body = dc_token("1e9999")
        first, second = self.fetch("DATACITE", DC_DOI, body, body)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertFalse(second["new_snapshot"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM integrity_coverage")["n"], 1)
            self.assertEqual(kb.one("SELECT count(*) n FROM request_completion")["n"], 2)

    def test_unsafe_refresh_keeps_retraction_alert_then_valid_recheck(self):
        """IDs: S02-UNSAFE-INTEGRITY, S02-UNSAFE-RECHECK, N06c. After a reported retraction, an unsafe refresh neither closes nor contradicts the alert; a later valid refresh is FOUND and the alert stays open."""
        unsafe = cr(lambda m: m.__setitem__("title", [LONE_HIGH]))
        ft = FixtureTransport()
        url = crossref.build_url(CR_DOI)
        ft.routes[url] = [Response(200, {"content-type": "application/json"}, b, url) for b in (dumps(CR_BASE), unsafe, dumps(CR_BASE))]
        with self.writer() as kb:
            service = self.service(kb, ft)
            self.assertEqual(service.fetch_provider("CROSSREF", structured(CR_DOI))["status"], "FOUND")
            refreshed = service.refresh(older_than_days=0)
            self.assertEqual([i["status"] for i in refreshed["items"]], ["REFUSED_UNSAFE_RESPONSE"])
            self.assertEqual(refreshed["errors"], 1)
        with self.reader() as kb:
            view = IntegrityService(MetadataStore(kb)).view("doi", CR_DOI)
            counts = [kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("integrity_counter_evidence", "integrity_event")]
        self.assertEqual((view["families"]["RETRACTION"]["state"], view["alert"]), ("REPORTED", "OPEN_ALERT"))
        self.assertEqual(counts, [0, 1])
        with self.writer() as kb:
            again = self.service(kb, ft).refresh(older_than_days=0)
        self.assertEqual([i["status"] for i in again["items"]], ["FOUND"])
        with self.reader() as kb:
            view = IntegrityService(MetadataStore(kb)).view("doi", CR_DOI)
            outcomes = [r["outcome"] for r in kb.all("SELECT outcome FROM provider_snapshot WHERE provider='CROSSREF' ORDER BY rowid")]
        self.assertEqual((view["families"]["RETRACTION"]["state"], view["alert"]), ("REPORTED", "OPEN_ALERT"))
        self.assertEqual(outcomes[:2], ["FOUND", "REFUSED_UNSAFE_RESPONSE"])

    def test_unsafe_ra_response_routes_nothing(self):
        """IDs: S02-UNSAFE-RA, S02-UNSAFE-SERVICE. An unsafe doiRA body is kept as a refused snapshot with exact raw bytes; no RA claim is written and no provider request follows."""
        body = dumps([{"DOI": CR_DOI, "RA": "Cross" + LONE_HIGH}])
        ft = FixtureTransport()
        url = ra.build_url([CR_DOI])
        ft.add(url, Response(200, {"content-type": "application/json"}, body, url))
        with self.writer() as kb:
            results = self.service(kb, ft).fetch([structured(CR_DOI)])
        self.assertEqual([(r["status"], r["snapshot_outcome"], r["reason"]) for r in results],
                         [("RA_LOOKUP_FAILED", "REFUSED_UNSAFE_RESPONSE", "UNPAIRED_SURROGATE")])
        with self.reader() as kb:
            snap = kb.one("SELECT * FROM provider_snapshot WHERE provider='DOI_RA'")
            self.assertEqual((snap["outcome"], snap["outcome_reason"]), ("REFUSED_UNSAFE_RESPONSE", "UNPAIRED_SURROGATE"))
            self.assertEqual(kb.cas.read(snap["response_bytes_sha256"]), body)
            self.assertEqual([kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("ra_claim", "request_reservation", "request_completion", "metadata_claim")],
                             [0, 1, 1, 0])

    def test_unsafe_csl_is_no_metadata_with_refused_snapshot(self):
        """IDs: S02-UNSAFE-CN, S02-UNSAFE-SERVICE. Unsafe or unparseable CSL JSON: user status NO_METADATA, snapshot REFUSED_UNSAFE_RESPONSE with the reason, raw bytes kept, no claims."""
        for body, reason, path in ((dumps({"title": LONE_HIGH}), "UNPAIRED_SURROGATE", "/title"),
                                   (b'{"title":"t","n":NaN}', "NONSTANDARD_JSON_CONSTANT", "/n"),
                                   (b"{", "UNPARSEABLE_RESPONSE", None)):
            with self.subTest(reason=reason):
                self.tearDown()
                self.setUp()
                ft = FixtureTransport()
                url = cn.build_url(CN_DOI)
                ft.add(url, Response(200, {"content-type": cn.ACCEPT}, body, url))
                with self.writer() as kb:
                    result = self.service(kb, ft).fetch_cn(structured(CN_DOI))
                    snap = kb.one("SELECT * FROM provider_snapshot WHERE snapshot_id=?", (result["snapshot_id"],))
                    raw = kb.cas.read(snap["response_bytes_sha256"])
                    claims = kb.one("SELECT count(*) n FROM metadata_claim")["n"]
                self.assertEqual((result["status"], result["snapshot_outcome"], result["reason"], result.get("field_path")),
                                 ("NO_METADATA", "REFUSED_UNSAFE_RESPONSE", reason, path))
                self.assertEqual((snap["outcome_reason"], raw, claims), (reason, body, 0))


class CliUnsafeSmokeTests(unittest.TestCase):
    def test_cli_fetch_unsafe_body_is_controlled(self):
        """IDs: S02-UNSAFE-CLI, OPS-LIVE-01. Through the CLI an unsafe provider body gives a JSON result (exit 0, REFUSED_UNSAFE_RESPONSE / UNPAIRED_SURROGATE); doctor stays ok and --live is still refused."""
        tmp = tempfile.mkdtemp(prefix="rkb-unsafe-cli-")
        try:
            fixtures = Path(tmp, "fx")
            fixtures.mkdir()
            Path(fixtures, "ra.json").write_bytes(dumps([{"DOI": CR_DOI, "RA": "Crossref"}]))
            Path(fixtures, "bad.json").write_bytes(cr(lambda m: m.__setitem__("title", [LONE_HIGH])))
            Path(fixtures, "transport.json").write_text(json.dumps([
                {"url": ra.build_url([CR_DOI]), "responses": [{"status": 200, "headers": {"content-type": "application/json"}, "body_file": "ra.json"}]},
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
            first = out["results"][0]
            self.assertEqual((first["status"], first["reason"], first["field_path"], first["coverage"]),
                             ("REFUSED_UNSAFE_RESPONSE", "UNPAIRED_SURROGATE", "/message/title/0", ["CHECK_FAILED"]))
            code, out = run("doctor", "--data-root", root)
            self.assertTrue(out["ok"])
            code, out = run("metadata", "fetch", "--data-root", root, "--doi", CR_DOI, "--live")
            self.assertEqual((code, out["error"]), (2, "LIVE_ACCESS_NOT_AUTHORIZED"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
