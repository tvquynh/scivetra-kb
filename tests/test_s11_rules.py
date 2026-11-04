"""S11 rules (pure functions, no KB): claim spec validation, statement discipline, comparability, missingness, identity conflicts,
document-version mismatches, S08 CONFLICTED records, mixed settings and derived-arithmetic edge cases."""
import unittest

from scivetra_kb.synthesis import rules as R

DIMS = {"metric", "value", "unit", "fpr_operating_point", "fpr_operating_point_unit", "dataset", "dataset_year", "split", "pe_type", "denominator",
        "subset", "seeds", "model_version", "temporal_holdout"}


def entry(did, paper, value="93.1", *, dv="dv_1", cls="NUMERICAL_RESULT", status="REPORTED", category="ELIGIBLE", text=None, **fields):
    f = {"metric": "TPR", "value": value, "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "dataset_year": "2018",
         "split": "TEST", "pe_type": "WIN32"}
    f.update(fields)
    f = {k: v for k, v in f.items() if v is not None}
    if cls != "NUMERICAL_RESULT" or status != "REPORTED":
        f = {}
    return {"draft_id": did, "draft_semantic_sha256": did * 2, "paper_id": paper, "document_version_id": dv, "asset_sha256": "a" * 64,
            "artifact_sha256": "b" * 64, "parse_artifact_id": "pza_1", "evidence_class": cls, "value_status": status, "fields": f,
            "text_dims": text if text is not None else ({"dataset": R.text_hash("EMBER")} if f else {}), "anchors": [{"page": 1, "char_start": 0,
            "char_end": 5, "quote_sha256": "c" * 64}], "category": category, "eligibility": R.ELIGIBLE if category == "ELIGIBLE" else "INELIGIBLE_" + category,
            "eligibility_reasons": [] if category == "ELIGIBLE" else [category + ":X"], "s08_conflicts_with": []}


def meas(value="93.1", **extra):
    m = {"metric": "TPR", "value": value, "unit": "%", "fpr_operating_point": "1", "fpr_operating_point_unit": "%", "dataset": "EMBER",
         "dataset_year": "2018", "split": "TEST", "pe_type": "WIN32"}
    m.update(extra)
    return {k: v for k, v in m.items() if v is not None}


def ctx(**kw):
    return dict({"profile_dims": DIMS, "metric_is_text": False, "members": ["p1", "p2", "p3"], "identity_conflicts": [], "doi_collisions": {}}, **kw)


def run(kind, cites, entries, c=None, **spec):
    claim = {"claim_id": "syc_1", "kind": kind, "cites": cites, "spec": R.validate_spec({"schema": R.SPEC_SCHEMA, "kind": kind, **spec}, len(cites))}
    return R.evaluate_claim(claim, {e["draft_id"]: e for e in entries}, c or ctx())


class SpecAndStatement(unittest.TestCase):
    def test_spec_validation_fails_closed(self):
        """IDs: S11-RULE-SPEC-STRICT. Unknown dimensions, JSON numbers, locale decimals, an operating point without unit, extra keys, a
        comparison with != 2 cites, a negative difference and a missing schema are all refused."""
        bad = [({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": meas(colour="red")}, 1),
               ({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": dict(meas(), value=93.1)}, 1),
               ({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": meas("93,1")}, 1),
               ({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": meas(fpr_operating_point_unit=None)}, 1),
               ({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": meas(), "note": "x"}, 1),
               ({"schema": R.SPEC_SCHEMA, "kind": "COMPARISON", "relation": "HIGHER"}, 3),
               ({"schema": R.SPEC_SCHEMA, "kind": "COMPARISON", "relation": "HIGHER", "difference": {"value": "-1", "unit": "PERCENTAGE_POINTS"}}, 2),
               ({"kind": "REPORTED_VALUE", "measurement": meas()}, 1),
               ({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": meas()}, 0)]
        for spec, n in bad:
            with self.assertRaises(R.SpecInvalid, msg=spec):
                R.validate_spec(spec, n)

    def test_templates_are_consistent_with_their_spec_and_statements_are_checked(self):
        """IDs: S11-RULE-TEMPLATE-CONSISTENT, S11-RULE-STATEMENT-UNIT, S11-RULE-STATEMENT-AMBIGUOUS. Every template statement passes the
        statement check of its own spec; '0.96%' for a ratio spec and the ambiguous '93,1' are refused."""
        specs = [{"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": meas()},
                 {"schema": R.SPEC_SCHEMA, "kind": "COMPARISON", "relation": "LOWER", "difference": {"value": "2.5", "unit": "PERCENTAGE_POINTS"}},
                 {"schema": R.SPEC_SCHEMA, "kind": "ABSENCE", "evidence_class": "UNCERTAINTY_INTERVAL", "status": "NOT_REPORTED"},
                 {"schema": R.SPEC_SCHEMA, "kind": "THEMATIC", "evidence_class": "LIMITATION", "topic": "PACKING"},
                 {"schema": R.SPEC_SCHEMA, "kind": "COUNT", "count": "2", "denominator": "3", "criterion": {"evidence_class": "LIMITATION"}}]
        for spec in specs:
            norm = R.validate_spec(spec, 2)
            self.assertEqual(R.statement_checks(R.template_statement(norm), norm), [], spec["kind"])
        ratio = R.validate_spec({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": dict(meas("0.96"), unit="ratio")}, 1)
        self.assertTrue(any(c.startswith("STATEMENT_UNIT_CONTRADICTS_SPEC") for c in R.statement_checks("TPR of 0.96% at 1% FPR on EMBER 2018.", ratio)))
        self.assertTrue(any(c.startswith("STATEMENT_NUMBER_AMBIGUOUS") for c in R.statement_checks("TPR 93,1 at 1 on EMBER 2018", R.validate_spec(specs[0], 1))))
        self.assertEqual(R.RUBRIC_SHA256, R.semantic_sha256(R.RUBRIC))


class Comparability(unittest.TestCase):
    def test_missing_dimension_is_never_a_wildcard(self):
        """IDs: S11-RULE-MISSING-NOT-WILDCARD, S11-RULE-ONE-SIDED-CONTEXT, S11-RULE-WITHHELD-TEXT. A core dimension missing on both sides is
        NOT_ESTABLISHED (not comparable); a PE type stated on one side only is PARTIALLY_COMPARABLE; a withheld dataset name is never
        EQUAL; a claim whose dataset cannot be checked because the text was withheld is PARTIAL."""
        a, b = entry("d1", "p1", dataset_year=None), entry("d2", "p2", "91.0", dataset_year=None)
        comp = R.comparability(R.entry_dims(a), R.entry_dims(b), DIMS)
        self.assertEqual(comp["state"], "NOT_COMPARABLE")
        self.assertIn("NOT_ESTABLISHED:dataset_year", comp["blocking"])
        a, b = entry("d1", "p1"), entry("d2", "p2", "91.0", pe_type=None)
        self.assertEqual(R.comparability(R.entry_dims(a), R.entry_dims(b), DIMS)["state"], "PARTIALLY_COMPARABLE")
        w = entry("d3", "p3", text={"dataset": "WITHHELD"})
        self.assertNotEqual(R.comparability(R.entry_dims(a), R.entry_dims(w), DIMS)["state"], "COMPARABLE")
        res = run("REPORTED_VALUE", ["d3"], [w], measurement=meas())
        self.assertEqual(res["verdict"], "PARTIAL")
        self.assertIn("TEXT_DIMENSION_WITHHELD:dataset", res["reasons"])

    def test_comparison_edge_cases(self):
        """IDs: S11-RULE-ZERO-BASE, S11-RULE-PP-NEEDS-PERCENT, S11-RULE-SAME-PAPER-SAME-KEY, S11-RULE-ZERO-IS-A-VALUE. A relative change
        from a zero base is undefined (UNSUPPORTED); percentage points need percent values; comparing two values of the same paper and
        measurement is CONFLICTING; zero is an observed value (EQUAL relation to 0 is checked, not treated as missing)."""
        z = entry("d2", "p2", "0")
        x = entry("d1", "p1", "5")
        self.assertIn("RELATIVE_CHANGE_UNDEFINED_ZERO_BASE", run("COMPARISON", ["d1", "d2"], [x, z], relation="HIGHER",
                                                                 difference={"value": "5", "unit": "RELATIVE_PERCENT"})["reasons"])
        self.assertEqual(run("COMPARISON", ["d1", "d2"], [x, z], relation="HIGHER", difference={"value": "5", "unit": "PERCENTAGE_POINTS"})["verdict"],
                         "SUPPORTED")
        r1, r2 = entry("d1", "p1", "0.95", unit="ratio"), entry("d2", "p2", "0.93", unit="ratio")
        self.assertIn("PERCENTAGE_POINTS_NEED_PERCENT_VALUES", run("COMPARISON", ["d1", "d2"], [r1, r2], relation="HIGHER",
                                                                  difference={"value": "2", "unit": "PERCENTAGE_POINTS"})["reasons"])
        s1, s2 = entry("d1", "p1", "93.1"), entry("d2", "p1", "90.0")
        self.assertEqual(run("COMPARISON", ["d1", "d2"], [s1, s2], relation="HIGHER")["verdict"], "CONFLICTING")
        self.assertEqual(run("REPORTED_VALUE", ["d2"], [z, x], measurement=meas("0"))["verdict"], "SUPPORTED")
        self.assertEqual(run("ABSENCE", ["d2"], [z], evidence_class="NUMERICAL_RESULT", status="NOT_REPORTED")["verdict"], "UNSUPPORTED")

    def test_mixed_settings_and_eligibility_precedence(self):
        """IDs: S11-RULE-MIXED-SETTINGS, S11-RULE-PRECEDENCE. One claim citing results of two different settings is UNSUPPORTED
        (MIXED_SETTINGS_IN_ONE_CLAIM); a blocked source wins over a stale one, a stale over a not-accepted one."""
        a, b = entry("d1", "p1"), entry("d2", "p1", fpr_operating_point="0.1")
        self.assertIn("MIXED_SETTINGS_IN_ONE_CLAIM", run("REPORTED_VALUE", ["d1", "d2"], [a, b], measurement=meas())["reasons"])
        es = [entry("d1", "p1", category="NOT_ACCEPTED"), entry("d2", "p2", category="STALE"), entry("d3", "p3", category="BLOCKED")]
        self.assertEqual(run("REPORTED_VALUE", ["d1", "d2", "d3"], es, measurement=meas())["verdict"], "BLOCKED_RIGHTS")
        self.assertEqual(run("REPORTED_VALUE", ["d1", "d2"], es, measurement=meas())["verdict"], "STALE_SOURCE")
        self.assertEqual(run("REPORTED_VALUE", ["d1"], es, measurement=meas())["verdict"], "MISSING_EVIDENCE")


class Contradictions(unittest.TestCase):
    def test_contradiction_kinds(self):
        """IDs: S11-RULE-VERSION-MISMATCH, S11-RULE-S08-CONFLICTED, S11-RULE-IDENTITY-CONFLICT, S11-RULE-CROSS-STUDY-NOT-CONTRADICTION.
        Two document versions of one paper with different values for one key are a DOCUMENT_VERSION_VALUE_MISMATCH; an eligible S08
        CONFLICTED record is recorded; one DOI active on two papers is a PAPER_IDENTITY_CONFLICT that makes a count PARTIAL; different
        values in different papers are not a contradiction."""
        es = [entry("d1", "p1", "93.1", dv="dv_pre"), entry("d2", "p1", "92.0", dv="dv_pub"), entry("d3", "p2", "80.0"),
              entry("d4", "p3", cls="NUMERICAL_RESULT", status="CONFLICTED")]
        c = ctx(doi_collisions={"10.99999/x": {"p2", "p3"}}, identity_conflicts=["p2", "p3"])
        kinds = sorted(x["kind"] for x in R.contradictions(es, c))
        self.assertEqual(kinds, ["DOCUMENT_VERSION_VALUE_MISMATCH", "PAPER_IDENTITY_CONFLICT", "S08_VALUE_STATUS_CONFLICTED"])
        count = run("COUNT", ["d1", "d3"], es, c, count="2", denominator="3", criterion={"evidence_class": "NUMERICAL_RESULT"})
        self.assertEqual(count["verdict"], "PARTIAL")
        self.assertIn("PAPER_IDENTITY_CONFLICT_INDEPENDENCE_UNKNOWN", count["reasons"])
        groups = R.comparison_groups(es, ctx())
        self.assertTrue(all(g["pooled_estimate"] is None and g["ranking"] is None for g in groups))


if __name__ == "__main__":
    unittest.main()
