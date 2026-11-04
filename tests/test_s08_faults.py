"""S08 adversarial checks: model fault modes, numeric / unit / locale / condition policy, strict response validation, prompt
injection, multi-page and conflicted records."""
import hashlib
import json
import unittest

from tests.s08_helpers import INJECTION_DOC, MALWARE_DOC, TWO_POINTS_DOC, S08Case
from scivetra_kb.core.errors import KBError
from scivetra_kb.evidence.profiles import builtin
from scivetra_kb.evidence.schema import claim_checks, numeric_mentions

MALWARE = builtin("rkb-malware-detection")
ROC_DOC = ["ROC-AUC of 0.981 on the EMBER 2018 test split."]
DENOM_DOC = ["TPR of 93.1% at 1% FPR on the EMBER 2018 test split.", "Detected 1,862 of 2,000 test samples."]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def check(fields, quote, cls="NUMERICAL_RESULT"):
    return claim_checks({"evidence_class": cls, "fields": fields}, [quote], profile=MALWARE)


class FaultModes(S08Case):
    def test_mock_fault_modes_fail_closed(self):
        """IDs: S08-HALLUCINATED-QUOTE, S08-FORGED-PAGE, S08-OFFSET-PARTIAL-NOT-REPAIRED, S08-WRONG-PAPER, S08-WRONG-VERSION, S08-UNSUPPORTED-NUMBER, S08-PERCENT-FROM-DECIMAL, S08-FPR-SWAP, S08-WRONG-DENOMINATOR, S08-WRONG-YEAR, S08-ROC-FOR-PR, S08-FABRICATED-CITATION, S08-TABLE-ONLY-QUARANTINED, S08-SCHEMA-VIOLATION, S08-UNIT-NEVER-INFERRED, S08-JSON-NUMBER-REFUSED, S08-STRUCTURAL-NOT-ENTAILMENT. One synthetic paper per fault mode: hallucinated quotes, forged pages, wrong numbers, percent conversions, swapped FPR operating points, wrong denominators/years, ROC-AUC relabelled as PR-AUC and fabricated citations fail verification (UNSUPPORTED); a shifted anchor is PARTIAL with a SUGGESTED (never applied) anchor; wrong paper/version and extra keys make the whole response SCHEMA_INVALID with no draft; table-only records, missing units and JSON numbers are refused per record (run PARTIAL). A pair of values swapped between two operating points of one sentence still passes structurally: verification is not entailment."""
        plan = {"hallucinate_quote": MALWARE_DOC, "wrong_page": MALWARE_DOC, "shift_offset": MALWARE_DOC, "wrong_paper": MALWARE_DOC,
                "wrong_version": MALWARE_DOC, "inflate_number": MALWARE_DOC, "percent_from_decimal": ROC_DOC, "swap_operating_point": MALWARE_DOC,
                "wrong_denominator": DENOM_DOC, "wrong_dataset_year": MALWARE_DOC, "roc_for_pr": ROC_DOC, "fabricated_citation": MALWARE_DOC,
                "table_only": MALWARE_DOC, "schema_violation": MALWARE_DOC, "drop_unit": MALWARE_DOC, "json_number": MALWARE_DOC,
                "pair_swap": TWO_POINTS_DOC}
        modes = list(plan)
        run, items = self.ev_world([[plan[m]] for m in modes])
        scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        paper_mode = {it["candidate"]["paper_id"]: m for it, m in zip(items, modes)}
        results = {paper_mode[r["paper_id"]]: r for r in self.ecall("run_mock", scope_id=scope, operator="op.runner", modes=paper_mode)["results"]}
        drafts = {}
        for d in self.drafts(scope):
            drafts.setdefault(paper_mode[d["paper_id"]], []).append(d)

        def first_state(mode, cls=None):
            d = [x for x in drafts[mode] if cls is None or x["evidence_class"] == cls][0]
            return self.ecall("verify", draft_id=d["draft_id"], operator="op.v")
        for mode, code in (("hallucinate_quote", "QUOTE_NOT_AT_ANCHOR:0"), ("wrong_page", "PAGE_OUT_OF_RANGE:0"), ("inflate_number", "NUMBER_NOT_IN_QUOTE:value"),
                           ("percent_from_decimal", "NUMBER_NOT_IN_QUOTE:value"), ("swap_operating_point", "NUMBER_NOT_IN_QUOTE:fpr_operating_point"),
                           ("wrong_denominator", "NUMBER_NOT_IN_QUOTE:denominator"), ("wrong_dataset_year", "NUMBER_NOT_IN_QUOTE:dataset_year"),
                           ("roc_for_pr", "ENUM_TERM_NOT_IN_QUOTE:metric")):
            v = first_state(mode, "NUMERICAL_RESULT")
            self.assertEqual(v["state"], "UNSUPPORTED", (mode, v))
            self.assertIn(code, v["failures"], mode)
        fab = [d for d in drafts["fabricated_citation"] if "Smith" in d["typed_fields_json"]][0]
        self.assertEqual(self.ecall("verify", draft_id=fab["draft_id"], operator="op.v")["state"], "UNSUPPORTED")
        shifted = first_state("shift_offset", "NUMERICAL_RESULT")
        self.assertEqual((shifted["state"], shifted["suggested_anchors"][0]["status"]), ("PARTIAL", "SUGGESTED_NOT_APPLIED"))
        for mode, code in (("wrong_paper", "WRONG_PAPER"), ("wrong_version", "WRONG_DOCUMENT_VERSION"), ("schema_violation", "SCHEMA_INVALID:keys")):
            self.assertEqual((results[mode]["status"], results[mode]["draft_ids"]), ("SCHEMA_INVALID", []), mode)
            self.assertIn(code, results[mode]["errors"])
        for mode, code in (("table_only", "TABLE_GEOMETRY_NOT_AVAILABLE"), ("drop_unit", "UNIT_MISSING:value"), ("json_number", "NUMBER_MUST_BE_DECIMAL_STRING:value")):
            self.assertEqual(results[mode]["status"], "PARTIAL", mode)
            self.assertIn(code, [c for e in results[mode]["record_errors"] for c in e["codes"]], mode)
        swapped = [self.ecall("verify", draft_id=d["draft_id"], operator="op.v")["state"] for d in drafts["pair_swap"]
                   if d["evidence_class"] == "NUMERICAL_RESULT"]
        self.assertEqual(swapped, ["STRUCTURALLY_VERIFIED", "STRUCTURALLY_VERIFIED"])
        values = sorted((json.loads(d["typed_fields_json"])["fpr_operating_point"], json.loads(d["typed_fields_json"])["value"])
                        for d in drafts["pair_swap"] if d["evidence_class"] == "NUMERICAL_RESULT")
        self.assertEqual(values, [("0.1", "93.1"), ("1", "80.2")])           # wrong pairing, structurally indistinguishable
        self.roster("rev.one")
        d = [x for x in drafts["shift_offset"] if x["evidence_class"] == "NUMERICAL_RESULT"][0]["draft_id"]
        self.ecall("reveal", draft_id=d, reviewer="rev.one")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=d, reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "DRAFT_NOT_VERIFIED")


class NumberPolicy(unittest.TestCase):
    def test_numbers_units_locale_and_conditions(self):
        """IDs: S08-NUMBER-WHOLE-TOKEN, S08-NUMBER-LOCALE-AMBIGUOUS, S08-THOUSANDS-GROUP, S08-PERCENT-NOT-INFERRED, S08-FPR-OPERATING-POINT-DISTINCT, S08-ROC-VS-PR, S08-TEXT-CONDITION, S08-INTERPRETIVE-FLAGGED. Whole numeric tokens only (12 is not 312); 1,234 is 1234; 1,5 is AMBIGUOUS; 93.1 with unit % needs a percent sign and a bare 0.931 is never 93.1%; TPR at 1% FPR and at 0.1% FPR are different measurements; ROC-AUC is not PR-AUC; dataset names and years must occur literally; interpretive enum values are reported as not structurally checkable."""
        base = {"metric": "TPR", "value": "93.1", "unit": "%"}
        self.assertEqual(check(base, "TPR of 93.1% at 1% FPR")[1:], ([], []))
        self.assertIn("NUMBER_NOT_IN_QUOTE:value", check({"metric": "TPR", "value": "12", "unit": "ratio"}, "TPR rose by 312 points")[1])
        self.assertEqual(check({"metric": "TPR", "value": "1234", "unit": "ratio", "denominator": "1234"}, "TPR 1,234 of 1,234 files")[1], [])
        self.assertEqual(check({"metric": "TPR", "value": "1.5", "unit": "ratio"}, "TPR of 1,5 on test")[2], ["NUMBER_AMBIGUOUS:value"])
        self.assertIn("UNIT_NOT_SUPPORTED:value", check({"metric": "TPR", "value": "93.1", "unit": "ratio"}, "TPR of 93.1% at 1% FPR")[1])
        self.assertIn("NUMBER_NOT_IN_QUOTE:value", check(base, "TPR of 0.931 at 1% FPR")[1])
        self.assertIn("UNIT_NOT_SUPPORTED:value", check(base, "TPR of 93.1 at 1% FPR")[1])
        op = dict(base, fpr_operating_point="0.1", fpr_operating_point_unit="%")
        self.assertIn("NUMBER_NOT_IN_QUOTE:fpr_operating_point", check(op, "TPR of 93.1% at 1% FPR")[1])
        self.assertEqual(check(op, "TPR of 93.1% at 0.1% FPR")[1], [])
        self.assertIn("ENUM_TERM_NOT_IN_QUOTE:metric", check({"metric": "PR_AUC", "value": "0.981", "unit": "ratio"}, "ROC-AUC of 0.981")[1])
        self.assertEqual(check({"metric": "AP", "value": "0.97", "unit": "ratio"}, "average precision of 0.97")[1], [])
        cond = dict(base, dataset="EMBER", dataset_year="2018", split="TEST")
        self.assertEqual(check(cond, "TPR of 93.1% on the EMBER 2018 test split")[1], [])
        self.assertIn("TEXT_NOT_IN_QUOTE:dataset", check(dict(cond, dataset="SOREL"), "TPR of 93.1% on the EMBER 2018 test split")[1])
        self.assertIn("NUMBER_NOT_IN_QUOTE:dataset_year", check(dict(cond, dataset_year="2019"), "TPR of 93.1% on the EMBER 2018 test split")[1])
        self.assertIn("ENUM_TERM_NOT_IN_QUOTE:split", check(dict(cond, split="CHALLENGE"), "TPR of 93.1% on the EMBER 2018 test split")[1])
        checks = check({"design_statement": "a benchmark", "design_category": "OBSERVATIONAL"}, "a benchmark", cls="STUDY_DESIGN")[0]
        self.assertIn({"field": "design_category", "check": "INTERPRETIVE_HUMAN_JUDGEMENT", "result": "NOT_STRUCTURALLY_CHECKABLE"}, checks)
        self.assertEqual([m["canonical"] for m in numeric_mentions("−3.5% and 2,000 and 1,5")], ["-3.5", "2000", None])


class ResponseValidation(S08Case):
    def pack_and_import(self, response, *, model=None):
        with self.reader() as kb:
            prow = kb.one("SELECT * FROM ev_input_pack WHERE scope_id=?", (self.scope,))
        raw = response if isinstance(response, bytes) else json.dumps(response).encode()
        return self.ecall("import_extraction", pack_id=prow["pack_id"], response=raw, operator="op.importer",
                          declared_model=model or {"provider": "local", "model_id": "operator-model"})

    def base(self):
        with self.reader() as kb:
            prow = kb.one("SELECT * FROM ev_input_pack WHERE scope_id=?", (self.scope,))
            pack = json.loads(kb.cas.read(prow["pack_sha256"]))
        seg = next(s for s in pack["segments"] if s["text"].startswith("TPR"))
        quote = seg["text"]
        anchor = {"page": seg["page"], "char_start": seg["start"], "char_end": seg["end"], "quote": quote, "quote_sha256": sha(quote)}
        record = {"evidence_class": "NUMERICAL_RESULT", "value_status": "REPORTED", "source_kind": "FULLTEXT_TEXT", "confidence": "MEDIUM", "note": "",
                  "fields": {"metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%"}, "anchors": [anchor]}
        doc = {"schema": "rkb-evidence-response/1", "input_pack_sha256": prow["pack_sha256"], "paper_id": pack["paper_id"],
               "document_version_id": pack["document_version_id"], "artifact_sha256": pack["artifact"]["artifact_sha256"],
               "profile_sha256": pack["profile"]["profile_sha256"], "records": [record], "notes": ""}
        return doc, record, anchor

    def test_strict_import_validation(self):
        """IDs: S08-IMPORT-STRICT, S08-IMPORT-DECLARED-UNVERIFIED, S08-JSON-DUPLICATE-KEYS, S08-JSON-NAN, S08-JSON-DEPTH, S08-SIZE-LIMIT, S08-LEAK-SCAN, S08-ENUM-VALIDATION, S08-ABSENCE-NOT-ZERO, S08-OCR-REFUSED, S08-QUOTE-HASH-INTEGRITY, S08-MODEL-DECLARATION. A valid operator import is recorded as OPERATOR_DECLARED_UNVERIFIED (attested false) and verifies; duplicate keys, NaN, deep nesting, oversized bodies and local paths/secrets refuse the whole response; unknown classes or fields, invalid enums or units, NOT_REPORTED with a zero, null values, CONFLICTED with one anchor, an inconsistent quote hash and OCR/figure source kinds are refused per record; a malformed model declaration is refused."""
        run, items = self.ev_world([[MALWARE_DOC]])
        self.scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=self.scope, operator="op.owner")
        doc, record, anchor = self.base()
        ok = self.pack_and_import(doc)
        self.assertEqual(ok["status"], "OK")
        with self.reader() as kb:
            r = kb.one("SELECT * FROM ev_extraction_run WHERE run_id=?", (ok["run_id"],))
        self.assertEqual((r["model_identity"], json.loads(r["model_json"])["attested"]), ("OPERATOR_DECLARED_UNVERIFIED", False))
        self.assertEqual(self.ecall("verify", draft_id=ok["draft_ids"][0], operator="op.v")["state"], "STRUCTURALLY_VERIFIED")
        raw = json.dumps(doc)
        whole = {"DUPLICATE_JSON_KEY": raw.replace('"notes": ""', '"notes": "", "notes": "x"').encode(),
                 "MALFORMED_JSON": raw.replace('"notes": ""', '"notes": NaN').encode(),
                 "MALFORMED_JSON:": ("[" * 200 + "]" * 200).encode(),
                 "RESPONSE_TOO_LARGE": raw.replace('"notes": ""', '"notes": "' + "x" * 600_000 + '"').encode(),
                 "LEAK_IN_RESPONSE": raw.replace('"notes": ""', '"notes": "see C:\\\\Users\\\\someone\\\\paper.pdf"').encode()}
        for code, body in whole.items():
            out = self.pack_and_import(body)
            self.assertEqual(out["status"], "SCHEMA_INVALID", code)
            self.assertTrue(any(e.startswith(code.rstrip(":")) for e in out["errors"]), (code, out["errors"]))
        cases = {
            "UNKNOWN_EVIDENCE_CLASS": dict(record, evidence_class="MAGIC"),
            "UNKNOWN_FIELD:p_hacked": dict(record, fields=dict(record["fields"], p_hacked="0.01")),
            "ENUM_VALUE_INVALID:metric": dict(record, fields=dict(record["fields"], metric="AUC")),
            "UNIT_INVALID:unit": dict(record, fields=dict(record["fields"], unit="percentish")),
            "NUMBER_FORMAT_INVALID:value": dict(record, fields=dict(record["fields"], value="93,1")),
            "ABSENCE_WITH_VALUES": dict(record, value_status="NOT_REPORTED", fields={"value": "0", "unit": "ratio", "metric": "TPR"}, anchors=[]),
            "NULL_FIELD_USE_VALUE_STATUS:value": dict(record, fields=dict(record["fields"], value=None)),
            "CONFLICTED_NEEDS_TWO_ANCHORS_AND_NO_VALUES": dict(record, value_status="CONFLICTED", fields={}),
            "QUOTE_HASH_INCONSISTENT": dict(record, anchors=[dict(anchor, quote_sha256=sha("other"))]),
            "OCR_NOT_AVAILABLE": dict(record, source_kind="OCR_TEXT"),
            "FIGURE_GEOMETRY_NOT_AVAILABLE": dict(record, source_kind="FULLTEXT_FIGURE_CAPTION"),
            "REQUIRED_FIELD_MISSING:value": dict(record, fields={"metric": "TPR"}),
        }
        for code, rec in cases.items():
            out = self.pack_and_import(dict(doc, records=[rec, record], notes=code))
            self.assertEqual(out["status"], "PARTIAL", code)
            self.assertIn(code, out["record_errors"][0]["codes"], code)
        with self.assertRaises(KBError) as ctx:
            self.pack_and_import(doc, model={"model_id": "x" * 200})
        self.assertEqual(ctx.exception.code, "MODEL_DECLARATION_INVALID")

    def test_multi_page_conflicted_and_uncertain_records(self):
        """IDs: S08-MULTI-PAGE-ANCHORS, S08-CONFLICTED-STATUS, S08-UNCERTAIN-STATUS, S08-ANCHOR-PAGE-BOUND. A CONFLICTED record anchors
        two passages on different pages and carries no value; an UNCERTAIN record keeps its anchor; each anchor is checked on its own page
        (a quote claimed on the wrong page fails)."""
        pages = [["TPR of 93.1% at 1% FPR on the EMBER 2018 test split."], ["Erratum: TPR of 92.4% at 1% FPR on the EMBER 2018 test split."]]
        run, items = self.ev_world([pages])
        self.scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=self.scope, operator="op.owner")
        doc, record, anchor = self.base()
        with self.reader() as kb:
            pack = json.loads(kb.cas.read(kb.one("SELECT pack_sha256 FROM ev_input_pack WHERE scope_id=?", (self.scope,))["pack_sha256"]))
        seg2 = next(s for s in pack["segments"] if s["page"] == 2 and "Erratum" in s["text"])
        a2 = {"page": 2, "char_start": seg2["start"], "char_end": seg2["end"], "quote": seg2["text"], "quote_sha256": sha(seg2["text"])}
        conflicted = dict(record, value_status="CONFLICTED", fields={}, anchors=[anchor, a2])
        uncertain = dict(record, value_status="UNCERTAIN", fields={"metric": "TPR"}, anchors=[a2])
        wrong_page = dict(record, anchors=[dict(a2, page=1)])
        out = self.pack_and_import(dict(doc, records=[conflicted, uncertain, wrong_page]))
        states = [self.ecall("verify", draft_id=d, operator="op.v")["state"] for d in out["draft_ids"]]
        self.assertEqual(states, ["STRUCTURALLY_VERIFIED", "STRUCTURALLY_VERIFIED", "UNSUPPORTED"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT typed_fields_json FROM ev_draft WHERE draft_id=?", (out["draft_ids"][0],))["typed_fields_json"], "{}")

    def test_prompt_injection_in_document_is_data(self):
        """IDs: S08-INJECTION-DATA, S08-PACK-UNTRUSTED-MARKERS. An instruction inside the PDF text does not change the extractor output
        (no 99.9% value appears); the rendered prompt wraps the pack in UNTRUSTED markers after the fixed template."""
        run, items, scope, _ = self.mocked([[INJECTION_DOC]])
        values = [json.loads(d["typed_fields_json"]).get("value") for d in self.drafts(scope) if d["evidence_class"] == "NUMERICAL_RESULT"]
        self.assertEqual(values, ["88.5"])
        with self.reader() as kb:
            from scivetra_kb.evidence.service import EvidenceService, TEMPLATE
            pack = json.loads(kb.cas.read(kb.one("SELECT pack_sha256 FROM ev_input_pack WHERE scope_id=?", (scope,))["pack_sha256"]))
            rendered = EvidenceService.render(pack)
        self.assertTrue(rendered.startswith(TEMPLATE))
        self.assertIn("<<<UNTRUSTED_INPUT_PACK", rendered)


if __name__ == "__main__":
    unittest.main()
