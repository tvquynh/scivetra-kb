import json
import unicodedata
import unittest

from tests.helpers import FIXTURES, operator, structured
from scivetra_kb.metadata.divergence import classify
from scivetra_kb.metadata.doi import (AMBIGUOUS, DOI_IDENTIFIER_FIELD, DOI_LIST_FIELD, FREE_TEXT_EXTRACT, INVALID,
                                      OPERATOR_OR_TABULAR_INPUT, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi)


class DoiFixtureTests(unittest.TestCase):
    def check(self, parse, status, key=None, url=None, flags=(), reason=None):
        self.assertEqual(parse.status, status, parse.as_dict())
        if key is not None:
            self.assertEqual(parse.comparison_key, key)
        if url is not None:
            self.assertEqual(parse.url_form, url)
        for flag in flags:
            self.assertIn(flag, parse.flags)
        if reason is not None:
            self.assertEqual(parse.reason, reason)

    def test_mf_18_01(self):
        """IDs: MF-18.01. dx proxy, case preserved in doi, ASCII-lowercased key, deprecated flag."""
        p = structured("https://dx.doi.org/10.99999/ABC")
        self.check(p, VALID, "10.99999/abc", flags=["DEPRECATED_DX_PROXY"])
        self.assertEqual(p.doi, "10.99999/ABC")

    def test_mf_18_02(self):
        """IDs: MF-18.02. doi: label form."""
        self.check(structured("doi:10.99999/abc"), VALID, "10.99999/abc")

    def test_mf_18_03(self):
        """IDs: MF-18.03. Trailing slash kept: a different DOI."""
        p = structured("10.99999/abc/")
        self.check(p, VALID, "10.99999/abc/", flags=["TRAILING_SLASH"])
        self.assertNotEqual(p.comparison_key, structured("10.99999/abc").comparison_key)

    def test_mf_18_04(self):
        """IDs: MF-18.04. Trailing punctuation kept with review flag."""
        self.check(structured("10.99999/abc)."), VALID, "10.99999/abc).", flags=["TRAILING_PUNCTUATION_REVIEW"])

    def test_mf_18_05(self):
        """IDs: MF-18.05. Free text with trailing punctuation gives AMBIGUOUS variants."""
        p = parse_doi("see doi 10.99999/abc). Next", FREE_TEXT_EXTRACT)
        self.assertEqual(p.status, AMBIGUOUS)
        self.assertEqual(p.candidates[0]["reason"], "TRAILING_PUNCTUATION")
        self.assertEqual([v["comparison_key"] for v in p.candidates[0]["variants"]], ["10.99999/abc).", "10.99999/abc)", "10.99999/abc"])
        self.assertFalse(p.automatic_action_allowed)

    def test_mf_18_06(self):
        """IDs: MF-18.06. Internal space in an identifier field is a legal suffix code point."""
        self.check(structured("10.99999/a b"), VALID, "10.99999/a b", "https://doi.org/10.99999/a%20b", ["SUFFIX_CONTAINS_SPACE"])

    def test_mf_18_07(self):
        """IDs: MF-18.07. Free text stops at whitespace; the result is a candidate only."""
        p = parse_doi("10.99999/a b", FREE_TEXT_EXTRACT)
        self.assertEqual(p.comparison_key, "10.99999/a")
        self.assertIn("FREE_TEXT_CANDIDATE_ONLY", p.flags)
        self.assertFalse(p.automatic_action_allowed)

    def test_mf_18_08(self):
        """IDs: MF-18.08. Comma and semicolon in the suffix; comma percent-encoded, semicolon unreserved."""
        self.check(structured("10.99999/a,b;c"), VALID, "10.99999/a,b;c", "https://doi.org/10.99999/a%2Cb;c", ["SUFFIX_CONTAINS_LIST_PUNCTUATION"])

    def test_mf_18_09(self):
        """IDs: MF-18.09. Two prefix occurrences in one identifier field are AMBIGUOUS."""
        p = structured("10.99999/a; 10.99998/b")
        self.check(p, AMBIGUOUS, reason="MULTIPLE_PREFIX_OCCURRENCES")
        self.assertEqual([c["comparison_key"] for c in p.candidates], ["10.99999/a; 10.99998/b", "10.99999/a", "10.99998/b"])

    def test_mf_18_10(self):
        """IDs: MF-18.10. List field elements are parsed individually; no splitting inside an element."""
        p = parse_doi(["10.99999/a", "10.99998/b"], DOI_LIST_FIELD)
        self.assertEqual(p.status, VALID)
        self.assertEqual([c["comparison_key"] for c in p.candidates], ["10.99999/a", "10.99998/b"])
        p2 = parse_doi(json.dumps(["10.99999/a,b"]), DOI_LIST_FIELD)
        self.assertEqual([c["comparison_key"] for c in p2.candidates], ["10.99999/a,b"])

    def test_mf_18_11(self):
        """IDs: MF-18.11. Two DOIs in free text: MULTIPLE_CANDIDATES, none promoted."""
        p = parse_doi("cites 10.99999/a and 10.99998/b", FREE_TEXT_EXTRACT)
        self.check(p, AMBIGUOUS, reason="MULTIPLE_CANDIDATES", flags=["MULTIPLE_CANDIDATES"])
        self.assertIsNone(p.doi)

    def test_mf_18_12(self):
        """IDs: MF-18.12. Percent-encoded UTF-8 in a proxy URL decodes once to the Unicode suffix."""
        p = structured("https://doi.org/10.99999/%C3%A1b")
        self.check(p, VALID, "10.99999/áb", "https://doi.org/10.99999/%C3%A1b")

    def test_mf_18_13(self):
        """IDs: MF-18.13."""
        self.check(structured("https://doi.org/10.99999/a%2"), INVALID, reason="MALFORMED_PERCENT_ESCAPE")

    def test_mf_18_14(self):
        """IDs: MF-18.14."""
        self.check(structured("https://doi.org/10.99999/%FF"), INVALID, reason="INVALID_UTF8_AFTER_DECODE")

    def test_mf_18_15(self):
        """IDs: MF-18.15. Decoded exactly once."""
        self.check(structured("https://doi.org/10.99999/a%2525"), VALID, "10.99999/a%25")

    def test_mf_18_16(self):
        """IDs: MF-18.16."""
        self.check(structured("https://doi.org/10.99999/a#b"), INVALID, reason="URL_QUERY_OR_FRAGMENT")

    def test_mf_18_17(self):
        """IDs: MF-18.17. doi: label with %HH is AMBIGUOUS between visual and URI form."""
        p = structured("doi:10.99999/a%20b")
        self.check(p, AMBIGUOUS, reason="DOI_LABEL_PERCENT_AMBIGUOUS")
        self.assertEqual([c["comparison_key"] for c in p.candidates], ["10.99999/a%20b", "10.99999/a b"])

    def test_mf_18_18(self):
        """IDs: MF-18.18. Non-ASCII case is significant (ASCII-only equivalence)."""
        self.assertNotEqual(structured("10.99999/ÄBC").comparison_key, structured("10.99999/äbc").comparison_key)
        self.assertEqual(structured("10.99999/ÄBC").comparison_key, "10.99999/Äbc")

    def test_mf_18_19(self):
        """IDs: MF-18.19."""
        self.assertEqual(structured("10.99999/ABC").comparison_key, structured("10.99999/abc").comparison_key)

    def test_mf_18_20(self):
        """IDs: MF-18.20. NFC and NFD forms are different keys (no Unicode normalization)."""
        nfc, nfd = unicodedata.normalize("NFC", "10.99999/é"), unicodedata.normalize("NFD", "10.99999/é")
        self.assertNotEqual(structured(nfc).comparison_key, structured(nfd).comparison_key)

    def test_mf_18_21(self):
        """IDs: MF-18.21."""
        for raw in ("https://example.org/10.99999/x", "https://www.doi.org/10.99999/x", "www.doi.org/10.99999/x"):
            self.check(structured(raw), INVALID, reason="UNSUPPORTED_URL_FORM")

    def test_mf_18_22(self):
        """IDs: MF-18.22."""
        self.check(structured("11.1234/x"), INVALID, reason="NON_10_DIRECTORY")
        self.check(structured("10/x"), INVALID, reason="BAD_PREFIX")
        self.check(structured("10.99999/"), INVALID, reason="EMPTY_SUFFIX")

    def test_mf_18_23(self):
        """IDs: MF-18.23. Control (TAB) and format (U+200B) code points inside a DOI are invalid."""
        self.check(structured("10.99999/x\ty"), INVALID, reason="NON_GRAPHIC_CODE_POINT")
        self.check(structured("10.99999/x​y"), INVALID, reason="NON_GRAPHIC_CODE_POINT")

    def test_mf_18_24(self):
        """IDs: MF-18.24. Operator cell with leading and trailing spaces: leading removed, trailing kept, AMBIGUOUS, no automatic action."""
        raw = "  10.99999/abc  "
        p = operator(raw)
        self.check(p, AMBIGUOUS, reason="TRAILING_SPACE_UNCERTAIN", flags=["LEADING_WHITESPACE_REMOVED", "AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION"])
        self.assertEqual([c["comparison_key"] for c in p.candidates], ["10.99999/abc  ", "10.99999/abc"])
        self.assertEqual(p.value_original, raw)
        self.assertFalse(p.automatic_action_allowed)

    def test_mf_18_26(self):
        """IDs: MF-18.26."""
        for p in (structured(" 10.99999/x"), operator(" 10.99999/x")):
            self.check(p, VALID, "10.99999/x", flags=["LEADING_WHITESPACE_REMOVED"])

    def test_mf_18_27(self):
        """IDs: MF-18.27, N28. Structured trailing space is kept in doi and key; URL uses %20."""
        p = structured("10.99999/x ")
        self.check(p, VALID, "10.99999/x ", "https://doi.org/10.99999/x%20", ["TRAILING_SPACE_IN_SUFFIX"])
        self.assertNotEqual(p.comparison_key, structured("10.99999/x").comparison_key)

    def test_mf_18_28(self):
        """IDs: MF-18.28, N28. Operator trailing space is AMBIGUOUS with no automatic action."""
        p = operator("10.99999/x ")
        self.check(p, AMBIGUOUS, reason="TRAILING_SPACE_UNCERTAIN")
        self.assertEqual([c["comparison_key"] for c in p.candidates], ["10.99999/x ", "10.99999/x"])
        self.assertFalse(p.automatic_action_allowed)

    def test_mf_18_29(self):
        """IDs: MF-18.29, N28. Two trailing spaces: a third distinct key."""
        p = structured("10.99999/x  ")
        self.check(p, VALID, "10.99999/x  ", "https://doi.org/10.99999/x%20%20")
        keys = {structured(v).comparison_key for v in ("10.99999/x", "10.99999/x ", "10.99999/x  ")}
        self.assertEqual(len(keys), 3)

    def test_mf_18_30(self):
        """IDs: MF-18.30, N28. Encoded %20 decodes to a kept trailing space, in both trust modes."""
        for p in (structured("https://doi.org/10.99999/x%20"), operator("https://doi.org/10.99999/x%20")):
            self.check(p, VALID, "10.99999/x ", "https://doi.org/10.99999/x%20", ["TRAILING_SPACE_IN_SUFFIX"])
            self.assertEqual(p.comparison_key, structured("10.99999/x ").comparison_key)
            self.assertNotEqual(p.comparison_key, "10.99999/x")

    def test_mf_18_31(self):
        """IDs: MF-18.31. Raw whitespace after a URL is formatting; raw whitespace inside is invalid."""
        self.check(structured("https://doi.org/10.99999/x "), VALID, "10.99999/x", flags=["WHITESPACE_AFTER_URL_REMOVED"])
        self.check(structured("https://doi.org/10.99999/a b"), INVALID, reason="RAW_WHITESPACE_IN_URL_FORM")

    def test_mf_18_32(self):
        """IDs: MF-18.32. Trailing CR/LF removed; space before them kept; TAB inside invalid."""
        self.check(structured("10.99999/x\r\n"), VALID, "10.99999/x", flags=["TRAILING_CONTROL_REMOVED"])
        self.check(structured("10.99999/x \r\n"), VALID, "10.99999/x ", flags=["TRAILING_CONTROL_REMOVED", "TRAILING_SPACE_IN_SUFFIX"])
        self.check(structured("10.99999/x\ty"), INVALID, reason="NON_GRAPHIC_CODE_POINT")

    def test_no_unicode_strip_semantics(self):
        """IDs: N28, S02-PROP-9. Non-ASCII spaces (U+00A0, U+3000) at the end of a structured DOI are kept; never trimmed."""
        for space in (" ", "　"):
            p = structured("10.99999/x" + space)
            self.assertEqual(p.comparison_key, "10.99999/x" + space)


class DivergenceTests(unittest.TestCase):
    def test_mf_18b_rv_divergence(self):
        """IDs: MF-18b. Every recorded RV normalize_doi output is classified exactly as documented in S01 v03 04 section 2.4."""
        data = json.loads((FIXTURES / "rv_normalize_doi_outputs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["cases"]), 20)
        for case in data["cases"]:
            with self.subTest(input=case["input"]):
                kb = parse_doi(case["input"], DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
                self.assertEqual(classify(case["input"], case["rv"], kb), case["kind"])

    def test_mf_18b_operator_trailing_space(self):
        """IDs: MF-18b, N28. Operator/tabular trailing space versus RV strip is reported as TRAILING_SPACE_STRIPPED."""
        kb = parse_doi("10.99999/x ", DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT)
        self.assertEqual(classify("10.99999/x ", "10.99999/x", kb), "TRAILING_SPACE_STRIPPED")


if __name__ == "__main__":
    unittest.main()
