"""S07 v02 regressions: S07-F01 outcome/criteria/reason consistency, S07-F02 numeric tokens and typed anchors."""
import copy
import hashlib
import json
import unittest

from tests.s07_helpers import CRITERIA, S07Case
from scivetra_kb.core.errors import KBError
from scivetra_kb.screening.schema import aggregation_failures, numeric_tokens

ABSTRACT = ("A randomized controlled trial in adult participants measuring blood pressure: 312 patients, 1,234 visits, dose 12mg, "
            "mean change −3.2, ratio 1,5 and protocol 2.0.1.")


def h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class NumericTokenPolicy(unittest.TestCase):
    def test_numeric_token_normalisation(self):
        """IDs: S07-F02-NUMBER-TOKENS, S07-F02-NUMBER-POLICY. rkb-number-match/1: complete tokens only (12 is not in 312); thousands groups are removed (1,234 = 1234); units may be adjacent (12mg); Unicode minus and en dash are '-'; decimals keep their digits (12 != 12.0); a decimal comma (1,5), a malformed group (1,2345) or a dotted version (2.0.1) is AMBIGUOUS and never matches."""
        self.assertEqual(numeric_tokens("312 patients"), ({"312"}, set()))
        self.assertNotIn("12", numeric_tokens("312 patients")[0])
        self.assertEqual(numeric_tokens("1,234 visits")[0], {"1234"})
        self.assertEqual(numeric_tokens("dose 12mg and 12.5%")[0], {"12", "12.5"})
        self.assertEqual(numeric_tokens("−3.2 and –3.2 and -3.2")[0], {"-3.2"})
        self.assertNotIn("12", numeric_tokens("12.0")[0])
        for text in ("1,5", "1,2345", "version 2.0.1"):
            canonical, ambiguous = numeric_tokens(text)
            self.assertTrue(ambiguous, text)
        self.assertEqual(numeric_tokens("No evidence"), (set(), set()))


class AggregationRule(unittest.TestCase):
    def payload(self, outcome, judgements, reasons=()):
        return {"suggested_outcome": outcome, "suggested_reason_codes": list(reasons),
                "criteria": [{"criterion_id": f"C{i + 1}", "judgement": j, "rationale": "r", "anchors": []} for i, j in enumerate(judgements)]}

    def test_aggregation_rule_matrix(self):
        """IDs: S07-F01-AGGREGATION-RULE, S07-F01-REASON-MAPPING, S07-F01-UNSURE-PRESERVED. rkb-screening-aggregation/1 (ALL_CRITERIA_REQUIRED): INCLUDE needs every criterion MET and no reason; EXCLUDE needs a NOT_MET criterion and exactly its reason codes; UNSURE needs no NOT_MET, not all MET and no reason. Consistent cases produce no failure; abstention stays valid."""
        ok = [("INCLUDE", ["MET", "MET", "MET"], []), ("EXCLUDE", ["NOT_MET", "UNCLEAR", "MET"], ["WRONG_POPULATION"]),
              ("EXCLUDE", ["NOT_MET", "NOT_MET", "UNCLEAR"], ["WRONG_POPULATION", "WRONG_STUDY_DESIGN"]), ("UNSURE", ["UNCLEAR"] * 3, []),
              ("UNSURE", ["MET", "UNCLEAR", "MET"], [])]
        for outcome, js, reasons in ok:
            self.assertEqual(aggregation_failures(self.payload(outcome, js, reasons), CRITERIA), [], (outcome, js, reasons))
        bad = {("INCLUDE", ("UNCLEAR",) * 3, ()): "OUTCOME_CRITERIA_INCONSISTENT:INCLUDE_WITHOUT_ALL_MET",
               ("INCLUDE", ("MET", "MET", "NOT_MET"), ()): "OUTCOME_CRITERIA_INCONSISTENT:INCLUDE_WITHOUT_ALL_MET",
               ("EXCLUDE", ("UNCLEAR",) * 3, ("WRONG_POPULATION",)): "OUTCOME_CRITERIA_INCONSISTENT:EXCLUDE_WITHOUT_NOT_MET",
               ("EXCLUDE", ("NOT_MET", "MET", "MET"), ("WRONG_STUDY_DESIGN",)): "REASON_CODE_UNSUPPORTED:WRONG_STUDY_DESIGN",
               ("EXCLUDE", ("NOT_MET", "NOT_MET", "MET"), ("WRONG_POPULATION",)): "REASON_CODE_MISSING:WRONG_STUDY_DESIGN",
               ("UNSURE", ("MET", "MET", "MET"), ()): "OUTCOME_CRITERIA_INCONSISTENT:UNSURE_WITH_DECISIVE_CRITERIA",
               ("UNSURE", ("NOT_MET", "UNCLEAR", "MET"), ()): "OUTCOME_CRITERIA_INCONSISTENT:UNSURE_WITH_DECISIVE_CRITERIA",
               ("UNSURE", ("UNCLEAR",) * 3, ("WRONG_OUTCOME",)): "REASON_CODE_UNSUPPORTED:WRONG_OUTCOME"}
        for (outcome, js, reasons), code in bad.items():
            self.assertIn(code, aggregation_failures(self.payload(outcome, list(js), reasons), CRITERIA), (outcome, js, reasons))


class VerifierIntegration(S07Case):
    def setUp(self):
        super().setUp()
        self.run_ = self.ta_world([ABSTRACT])
        self.scope = self.ta_scope(self.run_)
        self.call("build_packs", scope_id=self.scope, operator="op.owner")
        with self.reader() as kb:
            row = kb.one("SELECT pack_id, pack_sha256 FROM ai_input_pack")
            self.pack_id = row["pack_id"]
            self.pack = dict(json.loads(kb.cas.read(row["pack_sha256"])), pack_sha256=row["pack_sha256"])
        self.abstract = next(i for i in self.pack["items"] if i.get("field") == "abstract")

    def anchor(self, phrase, page=None):
        i = self.abstract["value"].index(phrase)
        return {"kind": "METADATA_CLAIM", "ref": self.abstract["ref"], "page": page, "char_start": i, "char_end": i + len(phrase), "quote": phrase,
                "quote_sha256": h(phrase)}

    def submit(self, outcome, criteria, reasons=()):
        doc = {"schema": "rkb-ai-response/1", "task_type": "TA_SCREEN", "paper_id": self.pack["paper_id"], "input_pack_sha256": self.pack["pack_sha256"],
               "suggested_outcome": outcome, "criteria": criteria, "suggested_reason_codes": list(reasons), "uncertainty": "MEDIUM", "notes": ""}
        out = self.call("import_proposal", pack_id=self.pack_id, response=json.dumps(doc).encode(), operator="op.importer",
                        declared_model={"provider": "synthetic", "model_id": "adversary"})
        self.assertEqual(out["status"], "OK", out)
        return self.call("verify", proposal_id=out["proposal_id"], operator="op.verifier")

    def crit(self, c1=None, c2=None, c3=None):
        base = {"judgement": "UNCLEAR", "rationale": "No evidence", "anchors": []}
        return [dict(base, criterion_id=f"C{i + 1}", **(x or {})) for i, x in enumerate((c1, c2, c3))]

    def test_f01_inconsistent_outcomes_fail_and_consistent_ones_pass(self):
        """IDs: S07-F01-INCLUDE-ALL-UNCLEAR, S07-F01-EXCLUDE-WITHOUT-NOT-MET, S07-F01-UNSUPPORTED-REASON, S07-F01-CONSISTENT-PASSES, S07-F01-NO-REWRITE. An INCLUDE with all criteria UNCLEAR and an EXCLUDE without a NOT_MET criterion are FAILED with OUTCOME_CRITERIA_INCONSISTENT; an EXCLUDE whose reason does not belong to the NOT_MET criterion fails REASON_CODE_UNSUPPORTED; fully anchored consistent INCLUDE and EXCLUDE pass; the stored proposal is not rewritten."""
        v = self.submit("INCLUDE", self.crit())
        self.assertEqual(v["state"], "FAILED")
        self.assertIn("OUTCOME_CRITERIA_INCONSISTENT:INCLUDE_WITHOUT_ALL_MET", v["failures"])
        v = self.submit("EXCLUDE", self.crit(), ["WRONG_POPULATION"])
        self.assertIn("OUTCOME_CRITERIA_INCONSISTENT:EXCLUDE_WITHOUT_NOT_MET", v["failures"])
        met = lambda p: {"judgement": "MET", "rationale": "quoted", "anchors": [self.anchor(p)]}  # noqa: E731
        not_met = {"judgement": "NOT_MET", "rationale": "quoted", "anchors": [self.anchor("adult participants")]}
        v = self.submit("EXCLUDE", self.crit(c1=not_met), ["WRONG_STUDY_DESIGN"])
        self.assertIn("REASON_CODE_UNSUPPORTED:WRONG_STUDY_DESIGN", v["failures"])
        self.assertEqual(self.submit("EXCLUDE", self.crit(c1=not_met), ["WRONG_POPULATION"])["state"], "PASSED")
        self.assertEqual(self.submit("INCLUDE", self.crit(met("adult participants"), met("randomized controlled"), met("blood pressure")))["state"], "PASSED")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM ai_proposal WHERE suggested_outcome='INCLUDE' AND output_status='OK'")["n"], 2)

    def test_f02_numbers_and_metadata_pages(self):
        """IDs: S07-F02-SUBSTRING-REFUSED, S07-F02-EXACT-NUMBER-PASSES, S07-F02-GROUPS-UNITS-SIGNS, S07-F02-AMBIGUOUS-NUMBER, S07-F02-METADATA-PAGE-NULL. A rationale claiming 12 against a quote of 312 fails NUMBER_NOT_IN_QUOTE; 312 passes; 1234 against a quote '1,234', '12 mg' against '12mg' and -3.2 against a Unicode minus pass; 12.0 against 12mg fails; a decimal comma (1,5) or a dotted version (2.0.1) fails NUMBER_AMBIGUOUS; a METADATA_CLAIM anchor with any page (999 or 1) fails METADATA_ANCHOR_HAS_PAGE and is not repaired."""
        def one(rationale, phrase, page=None):
            return self.submit("UNSURE", self.crit({"judgement": "MET", "rationale": rationale, "anchors": [self.anchor(phrase, page)]}))
        self.assertIn("NUMBER_NOT_IN_QUOTE:C1", one("reports 12 participants", "312 patients")["failures"])
        self.assertEqual(one("reports 312 participants", "312 patients")["state"], "PASSED")
        self.assertEqual(one("1234 visits in total", "1,234 visits")["state"], "PASSED")
        self.assertEqual(one("a dose of 12 mg", "dose 12mg")["state"], "PASSED")
        self.assertEqual(one("change of -3.2", "mean change −3.2")["state"], "PASSED")
        self.assertIn("NUMBER_NOT_IN_QUOTE:C1", one("a dose of 12.0 mg", "dose 12mg")["failures"])
        self.assertIn("NUMBER_AMBIGUOUS:C1", one("ratio 1,5", "ratio 1,5")["failures"])
        self.assertIn("NUMBER_AMBIGUOUS:C1", one("protocol 2.0.1", "protocol 2.0.1")["failures"])
        for page in (999, 1):
            v = one("reports 312 participants", "312 patients", page)
            self.assertEqual(v["state"], "FAILED")
            self.assertIn("METADATA_ANCHOR_HAS_PAGE:C1", v["failures"])

    def test_mock_proposals_and_rule_version_unchanged_behaviour(self):
        """IDs: S07-V02-MOCK-STILL-PASSES, S07-V02-VERIFIER-VERSION, S07-V02-CRITERIA-AGGREGATION-FIELD. Consistent mock proposals still verify PASSED under rkb-ai-verify/2 and record an OUTCOME_CONSISTENCY check; a criteria set may name the aggregation rule explicitly, but an unknown rule is refused (CRITERIA_INVALID)."""
        pid = self.call("run_mock", scope_id=self.scope, operator="op.runner")["results"][0]["proposal_id"]
        self.assertEqual(self.call("verify", proposal_id=pid, operator="op.v")["state"], "PASSED")
        with self.reader() as kb:
            row = kb.one("SELECT verifier_version, checks_json FROM ai_proposal_verification ORDER BY rowid DESC LIMIT 1")
        self.assertEqual(row["verifier_version"], "rkb-ai-verify/2")
        self.assertIn('"check":"OUTCOME_CONSISTENCY"', row["checks_json"])
        explicit = dict(copy.deepcopy(CRITERIA), aggregation="ALL_CRITERIA_REQUIRED", rubric_version="3")
        self.assertTrue(self.criteria_set(criteria=explicit, label="explicit rule"))
        with self.assertRaises(KBError) as ctx:
            self.criteria_set(criteria=dict(copy.deepcopy(CRITERIA), aggregation="ANY_ONE_SUFFICES"), label="unknown rule")
        self.assertEqual(ctx.exception.code, "CRITERIA_INVALID")


class FtAnchorTypes(S07Case):
    def test_parse_segment_page_offset_hash_fail_closed(self):
        """IDs: S07-F02-SEGMENT-PAGE, S07-F02-SEGMENT-OFFSET, S07-F02-SEGMENT-HASH, S07-F02-SEGMENT-LEGIT. For FT PARSE_SEGMENT anchors a wrong page fails PAGE_MISMATCH, shifted offsets or a forged quote/hash fail QUOTE_MISMATCH or QUOTE_HASH_INCONSISTENT, and a legitimate exact anchor with a supported number passes."""
        run = self.make_run(1)
        pdf = self.ft_pdf(run["candidates"][0], ["Synthetic full-text study number 0", "We enrolled 312 adults in a randomized controlled trial.",
                                                 "Blood pressure was the outcome."])
        scope = self.ft_scope(run, [{"candidate_id": run["candidates"][0]["candidate_id"], "parse_artifact_id": pdf["artifact_id"]}])
        self.call("build_packs", scope_id=scope, operator="op.owner")
        with self.reader() as kb:
            row = kb.one("SELECT pack_id, pack_sha256 FROM ai_input_pack")
            pack = json.loads(kb.cas.read(row["pack_sha256"]))
        seg = next(i for i in pack["items"] if "312" in i.get("text", ""))
        j = seg["text"].index("312 adults")

        def submit(anchor, rationale="312 adults were enrolled"):
            items = [{"criterion_id": "C1", "judgement": "MET", "rationale": rationale, "anchors": [anchor]},
                     {"criterion_id": "C2", "judgement": "UNCLEAR", "rationale": "n/a", "anchors": []},
                     {"criterion_id": "C3", "judgement": "UNCLEAR", "rationale": "n/a", "anchors": []}]
            doc = {"schema": "rkb-ai-response/1", "task_type": "FT_SCREEN", "paper_id": pack["paper_id"], "input_pack_sha256": row["pack_sha256"],
                   "suggested_outcome": "UNSURE", "criteria": items, "suggested_reason_codes": [], "uncertainty": "MEDIUM", "notes": ""}
            out = self.call("import_proposal", pack_id=row["pack_id"], response=json.dumps(doc).encode(), operator="op.importer",
                            declared_model={"model_id": "adversary"})
            return self.call("verify", proposal_id=out["proposal_id"], operator="op.v")
        good = {"kind": "PARSE_SEGMENT", "ref": seg["ref"], "page": seg["page"], "char_start": seg["start"] + j, "char_end": seg["start"] + j + 10,
                "quote": "312 adults", "quote_sha256": h("312 adults")}
        self.assertEqual(submit(good)["state"], "PASSED")
        self.assertIn("PAGE_MISMATCH:C1", submit(dict(good, page=seg["page"] + 1))["failures"])
        self.assertIn("QUOTE_MISMATCH:C1", submit(dict(good, char_start=good["char_start"] + 1, char_end=good["char_end"] + 1))["failures"])
        self.assertIn("QUOTE_HASH_INCONSISTENT:C1", submit(dict(good, quote_sha256=h("31 adults")))["failures"])
        self.assertIn("NUMBER_NOT_IN_QUOTE:C1", submit(good, rationale="12 adults were enrolled")["failures"])


if __name__ == "__main__":
    unittest.main()
