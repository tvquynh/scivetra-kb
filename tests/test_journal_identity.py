"""S04J: ISSN validation, evidence-only journal identity, OpenAlex source resolution and S02 DOI equality."""
import json

from tests.helpers import resp
from tests.journal_helpers import (JISA, KBTest, SimulatedProviders, harvester, members, scenario, shared_jisa, small_scenario)
from scivetra_kb.core.errors import ValidationError
from scivetra_kb.journal.issn import check_digit, parse_issn
from scivetra_kb.metadata.doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, parse_doi
from scivetra_kb.metadata.transport import FixtureTransport


class IssnTests(KBTest):
    def test_issn_syntax_and_check_digit(self):
        """IDs: JH-04, S04J-ISSN-01. Valid hyphenated, compact and X-check-digit ISSNs are accepted (normalized to NNNN-NNNC); a wrong check digit, X outside the check position, separators other than one hyphen, surrounding spaces, prefixes, full-width digits and wrong lengths are refused with stable reasons."""
        self.assertEqual(check_digit("2214212"), "6")
        self.assertEqual(check_digit("0000006"), "X")
        for raw, issn, flags in (("2214-2126", "2214-2126", []), ("2214-2134", "2214-2134", []), ("22142126", "2214-2126", ["COMPACT_FORM"]),
                                 ("0000-006X", "0000-006X", []), ("0000-006x", "0000-006X", ["LOWERCASE_X_UPPERCASED"])):
            out = parse_issn(raw)
            self.assertEqual((out["status"], out["issn"], out["flags"]), ("VALID", issn, flags), raw)
        for raw, reason in (("2214-2127", "CHECK_DIGIT_MISMATCH"), ("0000-0060", "CHECK_DIGIT_MISMATCH"), ("22X4-2126", "BAD_FORMAT"),
                            (" 2214-2126", "BAD_FORMAT"), ("2214-2126 ", "BAD_FORMAT"), ("ISSN 2214-2126", "BAD_FORMAT"),
                            ("2214–2126", "BAD_FORMAT"), ("２２１４-2126", "BAD_FORMAT"), ("2214-212", "BAD_FORMAT"),
                            ("2214--2126", "BAD_FORMAT"), ("", "BAD_FORMAT"), (None, "NOT_A_STRING")):
            out = parse_issn(raw)
            self.assertEqual((out["status"], out["reason"], out["issn"]), ("INVALID", reason, None), repr(raw))

    def test_register_is_conservative_and_idempotent(self):
        """IDs: JH-04, JH-09. Registration refuses any invalid ISSN (nothing written); duplicate spellings of one ISSN collapse; the same ISSN set is the same journal; before resolution the KB holds only the operator's REQUESTED ISSNs, with no print/electronic/linking relationship."""
        with self.writer() as kb:
            h = harvester(kb, FixtureTransport())
            with self.assertRaises(ValidationError) as ctx:
                h.register_journal("bad", ["2214-2126", "2214-2127"])
            self.assertEqual(ctx.exception.code, "INVALID_ISSN")
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_journal")["n"], 0)
            first = h.register_journal("JISA-shaped", ["2214-2134", "22142126", "2214-2126"])
            again = h.register_journal("relabelled", ["2214-2126", "2214-2134"])
            self.assertEqual((first["created"], again["created"], first["journal_id"]), (True, False, again["journal_id"]))
            self.assertEqual(first["requested_issns"], JISA)
            roles = kb.all("SELECT issn, issn_role, provider FROM jh_issn_evidence ORDER BY issn")
            self.assertEqual(roles, [{"issn": "2214-2126", "issn_role": "REQUESTED", "provider": "OPERATOR"},
                                     {"issn": "2214-2134", "issn_role": "REQUESTED", "provider": "OPERATOR"}])


class ResolutionTests(KBTest):
    def test_print_electronic_mapping_comes_from_provider_evidence(self):
        """IDs: JH-04, JH-05, S04J-ISSN-EVIDENCE. After resolution the print/electronic roles are rows citing the Crossref journal-record page and JSON pointer; the linking ISSN comes only from OpenAlex issn_l (never derived locally); both requested ISSNs are linked by the provider, so no relationship conflict is raised."""
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(small_scenario(5)))
            jid = h.register_journal("JISA-shaped", JISA)["journal_id"]
            out = h.resolve(jid)
            self.assertEqual((out["crossref"]["state"], out["openalex"]["state"], out["openalex"]["selected"]), ("COMPLETE", "COMPLETE", ["S900000001"]))
            self.assertEqual(out["crossref"]["issn_conflicts"], [])
            rows = kb.all("SELECT e.issn, e.issn_role, e.provider, e.source_field_path, p.kind FROM jh_issn_evidence e "
                          "LEFT JOIN jh_page p ON p.page_id = e.page_id WHERE e.provider <> 'OPERATOR' ORDER BY e.provider, e.issn, e.issn_role")
            roles = {(r["provider"], r["issn"], r["issn_role"]) for r in rows}
            self.assertTrue({("CROSSREF", "2214-2126", "PRINT"), ("CROSSREF", "2214-2134", "ELECTRONIC"), ("OPENALEX", "2214-2126", "LINKING")} <= roles)
            self.assertNotIn(("CROSSREF", "2214-2126", "LINKING"), roles)
            self.assertTrue(all(r["source_field_path"].startswith(("/message/issn-type/", "/message/ISSN/", "/results/")) for r in rows))
            self.assertTrue(all(r["kind"] in ("JOURNAL_LOOKUP", "SOURCE_LOOKUP") for r in rows))

    def test_unlinked_requested_issns_raise_relationship_conflict(self):
        """IDs: JH-04, JH-05. Two requested ISSNs whose provider journal records do not list each other are NOT merged into one identity silently: an ISSN_RELATIONSHIP_CONFLICT review task records both."""
        data = small_scenario(3)
        data["crossref_journals"]["2214-2134"] = {"title": "A different synthetic journal", "publisher": "x", "works_key": "JISA",
                                                  "issn_type": [{"value": "2214-2134", "type": "electronic"}]}
        data["crossref_journals"]["2214-2126"]["issn_type"] = [{"value": "2214-2126", "type": "print"}]
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            jid = h.register_journal("two records", JISA)["journal_id"]
            out = h.resolve(jid)
            self.assertEqual(out["crossref"]["issn_conflicts"], [{"kind": "REQUESTED_ISSNS_NOT_LINKED_BY_PROVIDER", "issns": JISA}])
            task = kb.one("SELECT trigger, context_json FROM jh_review_task WHERE trigger='ISSN_RELATIONSHIP_CONFLICT'")
            self.assertEqual(json.loads(task["context_json"])["issns"], JISA)

    def test_source_matching_requires_exact_issn_never_title(self):
        """IDs: JH-05, JH-12, S04J-SOURCE-01. An OpenAlex /sources answer is evidence only for sources whose issn/issn_l list contains the requested ISSN: a same-titled source without that ISSN is ignored; a journal with no ISSN-matched source is not resolved and its OpenAlex harvest is BLOCKED (OPENALEX_SOURCE_NOT_RESOLVED), never filled by title."""
        ft = FixtureTransport()
        from scivetra_kb.journal import providers as pv
        for issn in ("2214-2126",):
            url, _ = pv.crossref_journal_request(issn)
            ft.add(url, resp(404, {"status": "error"}, url))
            url, _ = pv.openalex_sources_request(issn)
            ft.add(url, resp(200, {"meta": {"count": 1}, "results": [{"id": "https://openalex.org/S1", "display_name": "Synthetic JISA-shaped Journal (fixture)",
                                                                      "issn_l": "9999-9999", "issn": ["0000-0019"], "type": "journal"}]}, url))
        with self.writer() as kb:
            h = harvester(kb, ft)
            jid = h.register_journal("title-only", ["2214-2126"])["journal_id"]
            out = h.resolve(jid)
            self.assertEqual((out["openalex"]["candidates"], out["openalex"]["selected"]), ([], []))
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_source_resolution WHERE provider='OPENALEX'")["n"], 0)
            self.assertEqual(out["crossref"]["state"], "BLOCKED")
            run = h.harvest(jid, "OPENALEX")
            self.assertEqual((run["state"], run["stop_reason"]), ("BLOCKED", "OPENALEX_SOURCE_NOT_RESOLVED"))
            run = h.harvest(jid, "CROSSREF")
            self.assertEqual((run["state"], run["stop_reason"]), ("BLOCKED", "CROSSREF_JOURNAL_NOT_RESOLVED"))

    def test_ambiguous_source_needs_operator_choice_among_candidates(self):
        """IDs: JH-05, JH-12, S04J-SOURCE-AMBIGUOUS. Two OpenAlex sources listing the same ISSN: none is selected, a SOURCE_AMBIGUOUS task is raised and the OpenAlex harvest is BLOCKED; an operator choice outside the candidates is refused; a choice among them is recorded with basis OPERATOR_SELECTED_AMONG_CANDIDATES and unblocks the harvest."""
        data = scenario(THREE)
        data["faults"] = []          # the batch scenario's injected OpenAlex 5xx belong to the batch test, not to this one
        with self.writer() as kb:
            h = harvester(kb, SimulatedProviders(data))
            jid = h.register_journal("Gamma", ["0000-0043"])["journal_id"]
            out = h.resolve(jid)
            self.assertTrue(out["openalex"]["ambiguous"])
            self.assertEqual(out["openalex"]["candidates"], ["S930000001", "S930000002"])
            self.assertEqual(h.harvest(jid, "OPENALEX")["stop_reason"], "SOURCE_AMBIGUOUS_OPERATOR_SELECTION_REQUIRED")
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_review_task WHERE trigger='SOURCE_AMBIGUOUS'")["n"], 1)
            with self.assertRaises(ValidationError) as ctx:
                h.resolve(jid, openalex_source_id="S999")
            self.assertEqual(ctx.exception.code, "SOURCE_NOT_A_CANDIDATE")
            chosen = h.resolve(jid, openalex_source_id="S930000001")
            self.assertEqual(chosen["openalex"]["selected"], ["S930000001"])
            basis = kb.one("SELECT basis FROM jh_source_resolution WHERE status='SELECTED'")["basis"]
            self.assertEqual(basis, "OPERATOR_SELECTED_AMONG_CANDIDATES")
            self.assertEqual(h.harvest(jid, "OPENALEX")["state"], "COMPLETE")


THREE = __import__("tests.journal_helpers", fromlist=["THREE_DIR"]).THREE_DIR


class DoiEqualityTests(KBTest):
    def test_doi_identity_reuses_s02_semantics(self):
        """IDs: JH-14, S04J-DOI-01. Subjects are keyed by the accepted S02 comparison key: an OpenAlex DOI differing only in ASCII case and a percent-encoded '/' join the Crossref subject; A-umlaut vs a-umlaut (non-ASCII case) stay two subjects; NFC/NFD variants stay two subjects with a UNICODE_NORMALIZATION_VARIANT flag and task; a suffix trailing space is a different key, flagged DOI_NEAR_DUPLICATE_TRAILING_SPACE, never merged."""
        shared = shared_jisa()
        with self.reader_for(shared) as kb:
            m = members(kb, shared["version"]["version_id"])
            key = lambda raw: parse_doi(raw, DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD).comparison_key  # noqa: E731
            self.assertEqual(m["10.5555/jisa-synthetic.000100"]["category"], "BOTH")
            self.assertEqual(m["10.5555/jisa-synthetic.000100"]["projection"]["doi_original_openalex"], "https://doi.org/10.5555/JISA-SYNTHETIC.000100")
            self.assertEqual(key("https://doi.org/10.5555/JISA-SYNTHETIC.000100"), "10.5555/jisa-synthetic.000100")
            self.assertEqual(m["10.5555/jisa-synthetic.a/b"]["category"], "BOTH")
            upper, lower = key("10.5555/jisa-synthetic.Äx"), key("https://doi.org/10.5555/jisa-synthetic.%C3%A4x")
            self.assertNotEqual(upper, lower)
            self.assertEqual((m[upper]["category"], m[lower]["category"]), ("CROSSREF_ONLY", "OPENALEX_ONLY"))
            nfd, nfc = "10.5555/jisa-synthetic.ét", "10.5555/jisa-synthetic.ét"
            self.assertEqual((m[nfd]["category"], m[nfc]["category"]), ("CROSSREF_ONLY", "OPENALEX_ONLY"))
            self.assertIn("DOI_UNICODE_NORMALIZATION_VARIANT", m[nfd]["flags"])
            plain, spaced = "10.5555/jisa-synthetic.trail", "10.5555/jisa-synthetic.trail "
            self.assertIn(spaced, m)
            self.assertIn("DOI_NEAR_DUPLICATE_TRAILING_SPACE", m[plain]["flags"])
            self.assertIn("DOI_NEAR_DUPLICATE_TRAILING_SPACE", m[spaced]["flags"])
            triggers = {r["trigger"] for r in kb.all("SELECT trigger FROM jh_review_task")}
            self.assertTrue({"DOI_UNICODE_NORMALIZATION_VARIANT", "DOI_NEAR_DUPLICATE_TRAILING_SPACE"} <= triggers)
            # no generic Unicode folding anywhere in the identity column
            keys = [r["doi_key"] for r in kb.all("SELECT DISTINCT doi_key FROM jh_observation WHERE doi_key IS NOT NULL")]
            self.assertTrue(all(k == k.translate({c: c + 32 for c in range(0x41, 0x5B)}) for k in keys))
            self.assertIn(upper, keys)

    def reader_for(self, shared):
        from scivetra_kb.core.db import open_reader
        return open_reader(shared["root"])
