"""S11 v02 corrections (architect findings S11-F01..F04): gate A5 applied to every claim-statement projection on a long-lived
service (audit, export files, CLI, reinstatement), the strengthened export leak scan, visible-statement checks (signed numbers,
units, dataset / metric / split / PE type / temporal / comparison vocabularies, negation), the author-statement cap with
provenance-bound human exact-statement confirmation, and member-level COUNT ascertainment (unknown is never a negative), each with
positive controls. Synthetic PDFs and mock extraction only; no network, model or real paper."""
import json
import unittest
from pathlib import Path

from tests.s11_helpers import QUOTE_CANARY, STUDY_A, STUDY_B, STUDY_C, S11Case, ids_of
from tests.test_s09_v02_regressions import cas_file, supersede_rights
from scivetra_kb import cli
from scivetra_kb.core.errors import KBError
from scivetra_kb.synthesis import rules as R

EXCERPT = "packed samples were excluded from the evaluation"
GOOD = "The study reports a TPR of 93.1% at 1% FPR on the EMBER 2018 test split for Win32 files."


def files_text(folder: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.iterdir()))


class A5StatementDisclosure(S11Case):
    def test_a5_statement_lifecycle_on_a_long_lived_service(self):
        """IDs: S11-V2-F01-STATEMENT-WITHHELD, S11-V2-F01-EXPORT-NO-EXCERPT, S11-V2-F01-POSITIVE-CONTROL, S11-V2-F01-REINSTATED, S11-V2-F01-CLI, S11-V2-F01-STORED-UNCHANGED. An author statement copying a source excerpt is stored while A4 allows it and is shown (audit and export) while A5 allows it; after store_locally is withdrawn through another connection, the same long-lived service's next audit, the new export files and the CLI audit output carry WITHHELD_A5 with SHA-256 and length and no excerpt; after the owner reinstates the right, it is shown again; the stored row is never rewritten."""
        self.syn_world([[STUDY_A], [STUDY_B]])
        self.claim("T1", "THEMATIC", [self.draft(0, "LIMITATION")], statement="The authors state that " + EXCERPT + ".", evidence_class="LIMITATION")
        self.claim("R1", "REPORTED_VALUE", [self.draft(1, fpr_operating_point="1")], measurement=self.tpr("91.4"))
        from scivetra_kb.core.db import KB
        from scivetra_kb.synthesis.service import SynthesisService
        kb = KB(self.root, readonly=False, clock=self.clock, ids=self.ids)
        self.addCleanup(kb.close)
        svc = SynthesisService(kb)
        run = svc.audit(scope_id=self.syn, operator="op.auditor")
        t1 = ids_of(run["report"])["T1"]
        self.assertEqual((t1["statement_disclosure"], t1["statement"]), ("SHOWN", "The authors state that " + EXCERPT + "."))
        out = svc.export(scope_id=self.syn, out_dir=str(Path(self.tmp, "allowed")), operator="op.exporter")
        self.assertIn(EXCERPT, files_text(Path(self.tmp, "allowed", out["folder_name"])))           # positive control: rights allow
        with self.writer() as other:
            supersede_rights(other, self.items[0]["asset_sha256"], store_locally="DENY")
        run2 = svc.audit(scope_id=self.syn, operator="op.auditor")
        t1 = ids_of(run2["report"])["T1"]
        self.assertEqual((t1["statement"], t1["statement_disclosure"], t1["statement_chars"]), (None, "WITHHELD_A5", len("The authors state that " + EXCERPT + ".")))
        self.assertEqual(len(t1["statement_sha256"]), 64)
        self.assertNotIn(EXCERPT, json.dumps(run2))
        self.assertEqual(ids_of(run2["report"])["R1"]["statement_disclosure"], "SHOWN")               # paper B's own text is not denied
        out2 = svc.export(scope_id=self.syn, out_dir=str(Path(self.tmp, "denied")), operator="op.exporter")
        blob = files_text(Path(self.tmp, "denied", out2["folder_name"]))
        self.assertNotIn(EXCERPT, blob)
        self.assertNotIn(QUOTE_CANARY, blob)
        self.assertIn("WITHHELD_A5", blob)
        code, cli_out = cli.run(["synth", "audit", "--data-root", self.root, "--scope-id", self.syn, "--operator", "op.auditor"], clock=self.clock, ids=self.ids)
        self.assertEqual(code, 0)
        self.assertNotIn(EXCERPT, json.dumps(cli_out))
        with self.writer() as other:
            supersede_rights(other, self.items[0]["asset_sha256"], store_locally="ALLOW")
        run3 = svc.audit(scope_id=self.syn, operator="op.auditor")
        self.assertEqual(ids_of(run3["report"])["T1"]["statement_disclosure"], "SHOWN")
        with self.reader() as r:
            self.assertIn(EXCERPT, r.one("SELECT statement FROM syn_claim WHERE claim_key='T1'")["statement"])

    def test_leak_scan_and_fail_closed(self):
        """IDs: S11-V2-F01-LEAK-SCAN-SHORT-EXCERPT, S11-V2-F01-FAIL-CLOSED-UNREADABLE, S11-V2-F01-REVIEW-CODES. The export leak scan refuses a file line holding a short verbatim excerpt of an A5-denied source (not only whole stored segments); when the denied source cannot be read the check cannot be made, so author statements of four or more words are withheld (fail closed); reason codes spelling an excerpt are withheld in the review history after A5 is withdrawn."""
        self.syn_world([[STUDY_A], [STUDY_B]])
        self.claim("T2", "THEMATIC", [self.draft(1, "LIMITATION")], statement="Both studies describe limitations of their scope.", evidence_class="LIMITATION")
        run = self.audit()
        self.scall("review", scope_id=self.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="REQUEST_CHANGES",
                   reason_codes=["PACKED_SAMPLES_WERE_EXCLUDED_FROM_THE_EVALUATION"])
        with self.writer() as kb:
            supersede_rights(kb, self.items[0]["asset_sha256"], store_locally="DENY")
        r = self.sread("reviews", scope_id=self.syn)["reviews"][0]
        self.assertEqual((r["reason_codes_json"], r["reason_codes_disclosure"]), (None, "WITHHELD"))
        from scivetra_kb.synthesis.service import SynthesisService
        with self.writer() as kb:
            svc = SynthesisService(kb)
            scope = svc._scope(self.syn)
            with self.assertRaises(KBError) as ctx:
                svc._leak_scan(scope, {"draft.md": ("- note: " + EXCERPT.upper() + "\n").encode("utf-8")})
            self.assertEqual(ctx.exception.code, "EXPORT_LEAK_REFUSED")
            svc._leak_scan(scope, {"draft.md": b"- note: limitations differ between studies\n"})
            art = kb.one("SELECT artifact_sha256 FROM parse_artifact WHERE asset_sha256=?", (self.items[0]["asset_sha256"],))["artifact_sha256"]
            path = cas_file(kb, art)
        path.write_bytes(path.read_bytes()[:-4] + b"\x00bad")
        t2 = ids_of(self.audit()["report"])["T2"]
        self.assertEqual((t2["statement"], t2["statement_disclosure"]), (None, "WITHHELD_A5"))


class VisibleStatementChecks(S11Case):
    def test_author_statement_categorical_sign_and_unit_contradictions(self):
        """IDs: S11-V2-F03-DATASET, S11-V2-F03-METRIC, S11-V2-F03-SPLIT, S11-V2-F03-PE-TYPE, S11-V2-F03-TEMPORAL, S11-V2-F03-UNITS, S11-V2-F03-NEGATION, S11-V2-F03-YEAR, S11-V2-F04-SIGN, S11-V2-F04-UNICODE-MINUS, S11-V2-F03-AUTHOR-CAP, S11-V2-F03-TEMPLATE-CONTROL. Author statements that contradict an EMBER / TPR@1%FPR / TEST / WIN32 spec in dataset, metric, split, PE type, temporal context, unit, negation, year or sign (ASCII and Unicode minus) are stored unchanged and audited UNSUPPORTED with the matching code, although the hidden spec matches the evidence; a consistent author statement is PARTIAL (human exact-statement confirmation required); the deterministic template is SUPPORTED."""
        self.syn_world([[STUDY_A], [STUDY_B]])
        a = self.draft(0, fpr_operating_point="1")
        base = "The study reports TPR of {v} at 1% FPR on EMBER 2018 test split."
        cases = {"DATASET": ("The study reports TPR of 93.1% at 1% FPR on SOREL 2018 test split.", "STATEMENT_DATASET_CONTRADICTS_SPEC:SOREL"),
                 "METRIC": ("The study reports PR-AUC of 93.1% at 1% FPR on EMBER 2018 test split.", "STATEMENT_METRIC_CONTRADICTS_SPEC:PR_AUC"),
                 "SPLIT": ("The study reports TPR of 93.1% at 1% FPR on EMBER 2018 training split.", "STATEMENT_SPLIT_CONTRADICTS_SPEC:TRAIN"),
                 "PE": (GOOD.replace("Win32", "Win64"), "STATEMENT_PE_TYPE_CONTRADICTS_SPEC:WIN64"),
                 "TEMPORAL": (GOOD.replace(" for Win32 files.", " for Win32 files on newer samples."), "STATEMENT_CONTEXT_NOT_IN_SPEC:temporal_holdout"),
                 "UNIT": (base.format(v="93.1"), "STATEMENT_UNIT_CONTRADICTS_SPEC:93.1"),
                 "RATIO": (base.format(v="0.931"), "STATEMENT_NUMBER_NOT_IN_SPEC:0.931"),
                 "NEGATION": ("The study does not report TPR of 93.1% at 1% FPR on EMBER 2018 test split.", "STATEMENT_NEGATION_NOT_IN_SPEC"),
                 "YEAR": (GOOD.replace("2018", "2019"), "STATEMENT_NUMBER_NOT_IN_SPEC:2019"),
                 "SIGN": (base.format(v="-93.1%"), "STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:-93.1"),
                 "MINUS": (base.format(v="−93.1%"), "STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:-93.1"),
                 "FIGDASH": (base.format(v="‒93.1%"), "STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:-93.1"),
                 "FULLWIDTH": (base.format(v="－93.1%"), "STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:-93.1")}
        for key, (statement, _) in cases.items():
            out = self.claim(key, "REPORTED_VALUE", [a], measurement=self.tpr("93.1"), statement=statement)
            self.assertEqual(out["statement"], statement)                                   # stored and shown unchanged, never repaired
        self.claim("GOOD", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"), statement=GOOD)
        self.claim("PLUS", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"), statement=GOOD.replace("93.1%", "+93.1%"))
        self.claim("TEMPLATE", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"))
        rep = ids_of(self.audit()["report"])
        for key, (statement, code) in cases.items():
            self.assertEqual(rep[key]["verdict"], "UNSUPPORTED", key)
            self.assertIn(code, rep[key]["reasons"], key)
            self.assertIn("STATEMENT_NOT_CONSISTENT_WITH_SPEC", rep[key]["reasons"], key)
        for key in ("GOOD", "PLUS"):
            self.assertEqual((rep[key]["verdict"], rep[key]["marker"], rep[key]["human_judgement"]), ("PARTIAL", "[UNVERIFIED]", "REQUIRED"), key)
            self.assertIn(R.AUTHOR_CAP, rep[key]["reasons"])
        self.assertEqual((rep["TEMPLATE"]["verdict"], rep["TEMPLATE"]["statement_origin"]), ("SUPPORTED", "DETERMINISTIC_TEMPLATE"))

    def test_human_exact_statement_confirmation_is_bound_to_the_statement(self):
        """IDs: S11-V2-F03-HUMAN-EXACT-CONFIRM, S11-V2-F03-CONFIRM-REFUSED-INCONSISTENT, S11-V2-F03-CONFIRM-REFUSED-TEMPLATE, S11-V2-F03-NEW-WORDING-NEEDS-NEW-CONFIRM, S11-V2-F03-DISPUTE. A registered human confirms a consistent author statement for its exact link graph: from the next audit it is SUPPORTED and cited; a statement inconsistent with its spec and a template claim cannot be confirmed; a new version with other words is PARTIAL again (the statement SHA-256 is part of the link graph); a human dispute makes it UNSUPPORTED."""
        self.syn_world([[STUDY_A], [STUDY_B]])
        a = self.draft(0, fpr_operating_point="1")
        self.claim("OK", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"), statement=GOOD)
        self.claim("BAD", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"), statement=GOOD.replace("EMBER", "SOREL"))
        self.claim("TPL", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"))
        run = self.audit()
        self.assertEqual(ids_of(run["report"])["OK"]["verdict"], "PARTIAL")
        for key in ("BAD", "TPL"):
            with self.assertRaises(KBError) as ctx:
                self.scall("review", scope_id=self.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="CONFIRM_SUPPORT", claim_key=key)
            self.assertEqual(ctx.exception.code, "CLAIM_REVIEW_NOT_APPLICABLE", key)
        self.scall("review", scope_id=self.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="CONFIRM_SUPPORT", claim_key="OK")
        ok = ids_of(self.audit()["report"])["OK"]
        self.assertEqual((ok["verdict"], ok["marker"], ok["references"], ok["human_judgement"]), ("SUPPORTED", "CITED", [1], "CONFIRMED"))
        self.assertIn("HUMAN_CONFIRMED_EXACT_AUTHOR_STATEMENT", ok["reasons"])
        self.claim("OK", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"), statement=GOOD.replace("The study reports a", "The authors report a"))
        run3 = self.audit()
        self.assertEqual(ids_of(run3["report"])["OK"]["verdict"], "PARTIAL")
        self.scall("review", scope_id=self.syn, audit_id=run3["audit_id"], reviewer="rev.three", decision="DISPUTE_SUPPORT", claim_key="OK",
                   reason_codes=["WORDING_OVERSTATES_RESULT"])
        ok = ids_of(self.audit()["report"])["OK"]
        self.assertEqual((ok["verdict"], ok["human_judgement"]), ("UNSUPPORTED", "DISPUTED"))

    def test_comparison_statements_relation_dataset_and_signed_difference(self):
        """IDs: S11-V2-F03-RELATION, S11-V2-F03-COMPARISON-DATASET, S11-V2-F04-DIFFERENCE-SIGN, S11-V2-F04-DIFFERENCE-VALID. For a comparable HIGHER comparison with a 1.7 pp difference, an author statement in the cited order with the right relation, values, units and difference is PARTIAL (awaits exact-statement confirmation); one saying 'lower', naming another dataset, or giving a negative difference is UNSUPPORTED; the template is SUPPORTED with the derived value logged."""
        self.syn_world([[STUDY_A], [STUDY_B]])
        a, b = self.draft(0, fpr_operating_point="1"), self.draft(1, fpr_operating_point="1")
        good = "At 1% FPR on the EMBER 2018 test split, the first TPR (93.1%) is higher than the second (91.4%), a difference of 1.7 percentage points."
        spec = {"relation": "HIGHER", "difference": {"value": "1.7", "unit": "PERCENTAGE_POINTS"}}
        self.claim("CMP-GOOD", "COMPARISON", [a, b], statement=good, **spec)
        self.claim("CMP-LOWER", "COMPARISON", [a, b], statement=good.replace("higher", "lower"), **spec)
        self.claim("CMP-SOREL", "COMPARISON", [a, b], statement=good.replace("EMBER", "SOREL"), **spec)
        self.claim("CMP-NEG", "COMPARISON", [a, b], statement=good.replace("of 1.7", "of -1.7"), **spec)
        self.claim("CMP-TPL", "COMPARISON", [a, b], **spec)
        rep = ids_of(self.audit()["report"])
        self.assertEqual((rep["CMP-GOOD"]["verdict"], rep["CMP-GOOD"]["statement_check_codes"]), ("PARTIAL", []))
        self.assertTrue(any(c.startswith("STATEMENT_RELATION_CONTRADICTS_SPEC") for c in rep["CMP-LOWER"]["reasons"]))
        self.assertIn("STATEMENT_DATASET_CONTRADICTS_SPEC:SOREL", rep["CMP-SOREL"]["reasons"])
        self.assertIn("STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:-1.7", rep["CMP-NEG"]["reasons"])
        for k in ("CMP-LOWER", "CMP-SOREL", "CMP-NEG"):
            self.assertEqual(rep[k]["verdict"], "UNSUPPORTED", k)
        self.assertEqual((rep["CMP-TPL"]["verdict"], rep["CMP-TPL"]["derivation"]["rounded"]), ("SUPPORTED", "1.7"))


class CountAscertainment(S11Case):
    def test_complete_ascertainment_positive_controls(self):
        """IDs: S11-V2-F02-COMPLETE-SUPPORTED, S11-V2-F02-ZERO-WITH-NEGATIVES, S11-V2-F02-ALL-POSITIVE, S11-V2-F02-CONTRADICTED, S11-V2-F02-ZERO-NEEDS-CITED-NEGATIVES. Every member classified by current, accepted evidence: '1 of 3 report an uncertainty interval' citing C (A and B have accepted NOT_REPORTED records) is SUPPORTED; '0 of 2' on a two-paper sub-scope citing both accepted NOT_REPORTED records is SUPPORTED (zero is not missing evidence); '2 of 2' TPR@1%FPR EMBER 2018 is SUPPORTED; '0 of 3' contradicts C's positive; a zero count citing nothing is MISSING_EVIDENCE."""
        self.syn_world([[STUDY_A], [STUDY_B], [STUDY_C]])
        ci = {"evidence_class": "UNCERTAINTY_INTERVAL"}
        self.claim("CI-1", "COUNT", [self.draft(2, "UNCERTAINTY_INTERVAL", level="95")], count="1", denominator="3", criterion=ci)
        self.claim("CI-0", "COUNT", [self.draft(2, "UNCERTAINTY_INTERVAL", level="95")], count="0", denominator="3", criterion=ci)
        rep = ids_of(self.audit()["report"])
        self.assertEqual(rep["CI-1"]["verdict"], "SUPPORTED")
        self.assertEqual(sorted(s["state"] for s in rep["CI-1"]["count_ascertainment"].values()), ["NEGATIVE", "NEGATIVE", "POSITIVE"])
        self.assertIn("COUNT_ASCERTAINMENT_COMPLETE:positive=1;negative=2;unknown=0", rep["CI-1"]["reasons"])
        self.assertEqual(rep["CI-0"]["verdict"], "UNSUPPORTED")
        pa, pb = self.items[0]["candidate"]["paper_id"], self.items[1]["candidate"]["paper_id"]
        self.syn = self.scall("create_scope", ev_scope_id=self.ev_scope, operator="op.synth", label="A and B only", paper_ids=[pa, pb],
                              review_policy="SINGLE_HUMAN", review_policy_reason="synthetic test protocol: one reviewer")["scope_id"]
        negs = [self.draft(0, "UNCERTAINTY_INTERVAL"), self.draft(1, "UNCERTAINTY_INTERVAL")]
        self.claim("ZERO", "COUNT", negs, count="0", denominator="2", criterion=ci)
        self.claim("ZERO-NOCITE", "COUNT", [], count="0", denominator="2", criterion=ci)
        tpr = {"evidence_class": "NUMERICAL_RESULT", "measurement": {"metric": "TPR", "fpr_operating_point": "1", "fpr_operating_point_unit": "%",
                                                                     "dataset": "EMBER", "dataset_year": "2018"}}
        self.claim("TPR-2", "COUNT", [self.draft(0, fpr_operating_point="1"), self.draft(1, fpr_operating_point="1")], count="2", denominator="2",
                   criterion=tpr)
        out = self.audit()
        rep = ids_of(out["report"])
        self.assertEqual((rep["ZERO"]["verdict"], rep["ZERO"]["references"]), ("SUPPORTED", [1, 2]))
        self.assertEqual([l["result"] for l in rep["ZERO"]["links"]], ["ASCERTAINED_NEGATIVE", "ASCERTAINED_NEGATIVE"])
        self.assertEqual(rep["ZERO-NOCITE"]["verdict"], "MISSING_EVIDENCE")
        self.assertEqual(rep["TPR-2"]["verdict"], "SUPPORTED")
        self.assertEqual(out["report"]["citation_audit"]["traceability"], "100%")

    def test_unknown_members_are_never_negatives(self):
        """IDs: S11-V2-F02-UNACCEPTED-MATCH-UNKNOWN, S11-V2-F02-MISSING-EXTRACT-UNKNOWN, S11-V2-F02-STALE-UNKNOWN, S11-V2-F02-NOT-ESTABLISHED. Paper B's matching TPR is not accepted (only its PR-AUC is): '1 of 2' is PARTIAL with POSSIBLY_MATCHING_EVIDENCE_NOT_USABLE; '2 of 2' is UNSUPPORTED (an unknown is not a positive either); B's never-accepted NOT_REPORTED interval makes '0 of 2' PARTIAL; when A's source goes stale, A is UNKNOWN too; a paper with only results in another context has NO_ASCERTAINMENT_RECORD (a missing extract is not NOT_REPORTED)."""
        def paper_b(d):
            return any('"91.4"' in x["typed_fields_json"] and x["paper_id"] == d["paper_id"] for x in self.drafts(d["scope_id"]))
        # Paper B: its matching TPR and its NOT_REPORTED interval are never accepted; its PR-AUC and limitation are.
        self.syn_world([[STUDY_A], [STUDY_B]], skip=lambda d: '"91.4"' in d["typed_fields_json"] or (d["evidence_class"] == "UNCERTAINTY_INTERVAL" and paper_b(d)))
        a = self.draft(0, fpr_operating_point="1")
        tpr = {"evidence_class": "NUMERICAL_RESULT", "measurement": {"metric": "TPR", "fpr_operating_point": "1", "fpr_operating_point_unit": "%",
                                                                     "dataset": "EMBER", "dataset_year": "2018"}}
        self.claim("ONE", "COUNT", [a], count="1", denominator="2", criterion=tpr)
        self.claim("TWO", "COUNT", [a], count="2", denominator="2", criterion=tpr)
        self.claim("CI-0", "COUNT", [self.draft(0, "UNCERTAINTY_INTERVAL")], count="0", denominator="2", criterion={"evidence_class": "UNCERTAINTY_INTERVAL"})
        sorel = {"evidence_class": "NUMERICAL_RESULT", "measurement": {"metric": "TPR", "dataset": "SOREL"}}
        self.claim("SOREL-0", "COUNT", [self.draft(0, fpr_operating_point="1")], count="0", denominator="2", criterion=sorel)
        rep = ids_of(self.audit()["report"])
        pb = self.items[1]["candidate"]["paper_id"]
        self.assertEqual(rep["ONE"]["verdict"], "PARTIAL")
        self.assertEqual(rep["ONE"]["count_ascertainment"][pb], {"state": "UNKNOWN", "reasons": ["POSSIBLY_MATCHING_EVIDENCE_NOT_USABLE:NOT_ACCEPTED"]})
        self.assertTrue(any(r.startswith("COUNT_NOT_ESTABLISHED") for r in rep["TWO"]["reasons"]))
        self.assertEqual(rep["TWO"]["verdict"], "UNSUPPORTED")
        self.assertEqual(rep["CI-0"]["verdict"], "PARTIAL")
        self.assertEqual(rep["SOREL-0"]["verdict"], "UNSUPPORTED")                       # citing a non-matching, non-negative record
        self.assertTrue(all(s == {"state": "UNKNOWN", "reasons": ["NO_ASCERTAINMENT_RECORD_MISSING_EXTRACT_IS_NOT_NOT_REPORTED"]}
                            for s in rep["SOREL-0"]["count_ascertainment"].values()))
        with self.writer() as kb:
            art = kb.one("SELECT artifact_sha256 FROM parse_artifact WHERE asset_sha256=?", (self.items[0]["asset_sha256"],))["artifact_sha256"]
            path = cas_file(kb, art)
        path.write_bytes(path.read_bytes()[:-4] + b"\x00bad")
        one = ids_of(self.audit()["report"])["ONE"]
        self.assertEqual(one["verdict"], "STALE_SOURCE")
        self.assertTrue(all(s["state"] == "UNKNOWN" for s in R.count_ascertainment(
            tpr, {"d": {"draft_id": "d", "paper_id": "p", "category": "STALE", "evidence_class": "NUMERICAL_RESULT", "value_status": "REPORTED",
                        "fields": {"metric": "TPR", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "dataset_year": "2018"},
                        "text_dims": {"dataset": R.text_hash("EMBER")}}}, ["p"], {"metric", "fpr_operating_point", "fpr_operating_point_unit", "dataset",
                                                                                 "dataset_year"}, False).values()))


class LexicalRules(unittest.TestCase):
    def test_signed_numbers_units_and_vocabulary(self):
        """IDs: S11-V2-RULE-SIGN-EXACT, S11-V2-RULE-HYPHEN-NOT-SIGN, S11-V2-RULE-NEGATIVE-SPEC-VALUE, S11-V2-RULE-PP-UNIT, S11-V2-RULE-AUC-UNQUALIFIED, S11-V2-RULE-SYNONYMS. Signs are compared exactly; a hyphen inside 'EMBER-2018' or a range is not a minus; a negative spec value needs its minus; percentage points need 'pp' wording; a bare 'AUC' cannot stand for TPR; recall / sensitivity are TPR synonyms."""
        spec = R.validate_spec({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": {
            "metric": "TPR", "value": "93.1", "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "dataset": "EMBER",
            "dataset_year": "2018", "split": "TEST", "pe_type": "WIN32"}}, 1)
        self.assertEqual(R.statement_checks("Recall of 93.1% at 1% FPR on EMBER-2018 (test split, Win32).", spec), [])
        self.assertEqual(R.statement_checks("Sensitivity was 93.1% at 1% FPR on the EMBER 2018 test set.", spec), [])
        self.assertIn("STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:-93.1", R.statement_checks("TPR: -93.1% at 1% FPR.", spec))
        self.assertIn("STATEMENT_METRIC_CONTRADICTS_SPEC:AUC", R.statement_checks("AUC of 93.1% at 1% FPR.", spec))
        neg = R.validate_spec({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": {"metric": "MCC", "value": "-0.12", "unit": "ratio"}}, 1)
        self.assertEqual(R.statement_checks("MCC of -0.12.", neg), [])
        self.assertEqual(R.statement_checks("MCC of −0.12.", neg), [])
        self.assertIn("STATEMENT_NUMBER_SIGN_CONTRADICTS_SPEC:0.12", R.statement_checks("MCC of 0.12.", neg))
        cmp_spec = R.validate_spec({"schema": R.SPEC_SCHEMA, "kind": "COMPARISON", "relation": "HIGHER",
                                    "difference": {"value": "1.7", "unit": "PERCENTAGE_POINTS"}}, 2)
        self.assertIn("STATEMENT_UNIT_CONTRADICTS_SPEC:1.7", R.statement_checks("The first is higher by 1.7%.", cmp_spec))
        self.assertEqual(R.statement_checks("The first is higher by 1.7 pp.", cmp_spec), [])
        sorel = R.validate_spec({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": dict(spec["measurement"], dataset="SOREL 20M")}, 1)
        self.assertEqual(R.statement_checks(R.template_statement(sorel), sorel), [])          # a number inside a stated dataset name
        self.assertEqual(R.RUBRIC_ID, "scivetra-synthesis-rubric/2")


if __name__ == "__main__":
    unittest.main()
