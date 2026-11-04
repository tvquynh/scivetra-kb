"""S03-D: rights-policy/1 classification (N14a-N14e), access-route independence, default-deny gates A1-A10, A9 handoff scan."""
import os
import unittest
from datetime import date
from pathlib import Path

from tests.pdf_fixtures import synthetic_pdf, title_page
from tests.test_assets import AssetCase, TITLE_A
from scivetra_kb.core.errors import ValidationError
from scivetra_kb.assets.service import AssetService
from scivetra_kb.library import queries
from scivetra_kb.rights import policy

AS_OF = date(2026, 11, 1)
CC_BY = {"source": "CROSSREF_LICENSE", "license_uri": "https://creativecommons.org/licenses/by/4.0/", "content_version": "vor",
         "start_date": "2024-01-01", "delay_in_days": 0, "claim_ref": "synthetic:crossref:license"}
OA_VOR = {"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion", "claim_ref": "synthetic:rv:oa_location"}
LOGIN = {"access_route": "INSTITUTIONAL_LOGIN", "access_barrier_observed": ["LoginRequired"]}
SUBSCRIPTION = {"terms": "institutional subscription, publisher terms", "evidence_ref": "synthetic-agreement-17", "restrictive": True}


class PolicyTests(unittest.TestCase):
    def classify(self, evidence, version_class="PUBLISHED"):
        return policy.classify(evidence, version_class=version_class, as_of=AS_OF)

    def test_n14a_open_licence_via_institutional_login(self):
        """IDs: N14a. CC BY vor (elapsed) + OA publishedVersion, obtained through institutional login: PUBLIC_OA_LICENSED; route is provenance only; redistribute/external_ai stay UNDETERMINED (OAG-6)."""
        out = self.classify({"license_evidence": [CC_BY], "availability_evidence": [OA_VOR], "access_provenance": LOGIN})
        self.assertEqual(out["rights_class"], "PUBLIC_OA_LICENSED")
        self.assertEqual(out["permissions"], {"store_locally": "ALLOW", "redistribute": "UNDETERMINED", "local_process": "ALLOW",
                                              "external_ai_process": "UNDETERMINED"})
        self.assertIn("ACCESS_ROUTE_RECORDED_AS_PROVENANCE_ONLY", out["flags"])

    def test_n14b_subscriber_only(self):
        """IDs: N14b. Non-open vor licence, no OA location, operator terms declaration: INSTITUTIONAL_RESTRICTED; redistribute and external AI DENY."""
        tdm = dict(CC_BY, license_uri="https://publisher.example.invalid/tdm-licence")
        out = self.classify({"license_evidence": [tdm], "terms_declarations": [SUBSCRIPTION], "access_provenance": LOGIN})
        self.assertEqual(out["rights_class"], "INSTITUTIONAL_RESTRICTED")
        self.assertEqual((out["permissions"]["redistribute"], out["permissions"]["external_ai_process"]), ("DENY", "DENY"))

    def test_n14c_login_history_only(self):
        """IDs: N14c, S03-RIGHTS-ROUTE-INDEPENDENT. Login history alone is UNKNOWN; every access route gives the same class for the same evidence."""
        self.assertEqual(self.classify({"access_provenance": LOGIN})["rights_class"], "UNKNOWN")
        classes = {route: self.classify({"license_evidence": [CC_BY], "availability_evidence": [OA_VOR],
                                          "access_provenance": {"access_route": route, "access_barrier_observed": ["Paywall", "BotCheck"]}})["rights_class"]
                   for route in policy.ACCESS_ROUTES}
        self.assertEqual(set(classes.values()), {"PUBLIC_OA_LICENSED"})

    def test_n14d_conflicting_evidence(self):
        """IDs: N14d. Open licence + OA location contradicted by a restrictive terms declaration: UNKNOWN + RIGHTS_EVIDENCE_CONFLICT; all external actions DENY."""
        out = self.classify({"license_evidence": [CC_BY], "availability_evidence": [OA_VOR], "terms_declarations": [SUBSCRIPTION]})
        self.assertEqual((out["rights_class"], "RIGHTS_EVIDENCE_CONFLICT" in out["flags"]), ("UNKNOWN", True))
        for action in ("A2", "A7", "A8"):
            self.assertEqual(policy.gate(action, out["permissions"])["decision"], "DENY")

    def test_n14e_licence_for_other_version(self):
        """IDs: N14e. An open licence only for 'am' with the file on the PUBLISHED version: not LICENSED; PUBLIC_OA_UNVERIFIED with OA for this version, else UNKNOWN."""
        am = dict(CC_BY, content_version="am")
        with_oa = self.classify({"license_evidence": [am], "availability_evidence": [OA_VOR]})
        self.assertEqual((with_oa["rights_class"], "LICENCE_VERSION_MISMATCH" in with_oa["flags"]), ("PUBLIC_OA_UNVERIFIED", True))
        self.assertEqual(self.classify({"license_evidence": [am]})["rights_class"], "UNKNOWN")
        self.assertEqual(self.classify({"license_evidence": [am], "availability_evidence": [OA_VOR]}, "ACCEPTED")["rights_class"], "UNKNOWN")

    def test_embargo_and_evidence_shape(self):
        """IDs: S03-RIGHTS-EMBARGO, S03-RIGHTS-EVIDENCE-SHAPE. An unelapsed embargo is not an applicable licence; unknown keys or a URL-only claim are refused."""
        future = dict(CC_BY, start_date="2026-10-01", delay_in_days=365)
        out = self.classify({"license_evidence": [future], "availability_evidence": [OA_VOR]})
        self.assertEqual((out["rights_class"], "EMBARGO_NOT_ELAPSED" in out["flags"]), ("PUBLIC_OA_UNVERIFIED", True))
        for bad in ({"pdf_url": "https://example.invalid/x.pdf"}, {"license_evidence": [{"license_uri": "https://creativecommons.org/licenses/by/4.0/"}]},
                    {"access_provenance": {"pbd_status": "Completed"}}):
            with self.subTest(bad=bad):
                with self.assertRaises(policy.EvidenceError):
                    self.classify(bad)

    def test_gates_default_deny(self):
        """IDs: S03-RIGHTS-GATES, A1-A10. UNDETERMINED behaves as DENY; no assessment denies; A9 is always denied; quotes respect max_quote_chars; storage never implies disclosure."""
        licensed = policy.CLASS_PERMISSIONS["PUBLIC_OA_LICENSED"]
        decisions = {a: policy.gate(a, licensed, quote_chars=200)["decision"] for a in policy.ACTIONS}
        self.assertEqual(decisions, {"A1": "ALLOW", "A2": "ALLOW", "A3": "ALLOW", "A4": "ALLOW", "A5": "ALLOW", "A6": "ALLOW", "A7": "DENY",
                                     "A8": "DENY", "A9": "DENY", "A10": "ALLOW"})
        unknown = policy.CLASS_PERMISSIONS["UNKNOWN"]
        self.assertEqual({a for a in policy.ACTIONS if policy.gate(a, unknown, quote_chars=10)["decision"] == "ALLOW"}, {"A1", "A4", "A5", "A10"})
        self.assertEqual(policy.gate("A3", licensed, quote_chars=5000)["reason"], "QUOTE_LENGTH_EXCEEDS_LIMIT")
        self.assertEqual({policy.gate(a, None)["decision"] for a in policy.ACTIONS}, {"DENY"})
        self.assertTrue(all(perms[p] != "ALLOW" for perms in policy.CLASS_PERMISSIONS.values() for p in ("redistribute", "external_ai_process")))

    def test_a9_handoff_scan(self):
        """IDs: S03-RIGHTS-A9, A9. The handoff scan flags a synthetic PDF (by header, by name, or embedded in another file) but not source code quoting PDF tokens."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp, "a.bin")
            pdf.write_bytes(synthetic_pdf([["x"]]))
            renamed = Path(tmp, "b.pdf")
            renamed.write_bytes(b"not really")
            embedded = Path(tmp, "c.json")
            embedded.write_bytes(b'{"x": "' + synthetic_pdf([["y"]]).replace(b'"', b"'") + b'"}')
            source = Path(__file__).resolve().parent / "pdf_fixtures.py"
            child = Path(policy.__file__).resolve().parents[1] / "assets" / "pdf_child.py"
            flagged = policy.handoff_scan([pdf, renamed, embedded, source, child])
        self.assertEqual([Path(p).name for p in flagged], ["a.bin", "b.pdf", "c.json"])


class RightsServiceTests(AssetCase):
    def test_registration_records_unknown_and_review_prompt(self):
        """IDs: S03-RIGHTS-G4, N14c. Registration always records rights-policy/1 UNKNOWN (G4); a LoginRequired history opens review prompts, never a class change."""
        data = synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99999/asset.a")])
        Path(self.fixtures, "a.pdf").write_bytes(data)
        out = self.register("a.pdf", access_route="INSTITUTIONAL_LOGIN", access_barriers=["LoginRequired"])
        self.assertEqual(out["rights"]["rights_class"], "UNKNOWN")
        with self.reader() as kb:
            triggers = sorted(t["trigger"] for t in queries.review_queue(kb)["rights_tasks"])
            gate = AssetService(kb).gate(asset_sha256=out["asset_sha256"], document_version_id=self.dv_a, action="A8")
        self.assertEqual(triggers, ["ACCESS_BARRIER_OBSERVED", "RIGHTS_UNKNOWN"])
        self.assertEqual(gate["decision"], "DENY")

    def test_reassessment_is_append_only_and_db_enforces_default_deny(self):
        """IDs: S03-RIGHTS-APPEND, S03-RIGHTS-DB-DENY. A new assessment supersedes (history kept, review task closed); an identical re-assessment is a no-op; the DB refuses ALLOW for redistribution/remote AI without an owner approval."""
        Path(self.fixtures, "a.pdf").write_bytes(synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99999/asset.a")]))
        asset = self.register("a.pdf")["asset_sha256"]
        with self.writer() as kb:
            svc = AssetService(kb)
            first = svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r",
                                      evidence={"license_evidence": [CC_BY], "availability_evidence": [OA_VOR]})
            again = svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r",
                                      evidence={"license_evidence": [CC_BY], "availability_evidence": [OA_VOR]})
            rows = kb.all("SELECT rights_class, supersedes_assessment_id FROM rights_assessment ORDER BY rowid")
            open_tasks = queries.review_queue(kb)["rights_tasks"]
            self.assertEqual(svc.gate(asset_sha256=asset, document_version_id=self.dv_a, action="A2")["decision"], "ALLOW")
            self.assertEqual(svc.gate(asset_sha256=asset, document_version_id=self.dv_a, action="A7")["decision"], "DENY")
            with self.assertRaises(ValidationError):
                svc.assess_rights(asset_sha256=asset, document_version_id=self.dv_a, operator="op.r", evidence={"guess": "open"})
            row = kb.one("SELECT * FROM rights_assessment ORDER BY rowid DESC LIMIT 1")
            for change in ({"redistribute": "ALLOW"}, {"external_ai_process": "ALLOW"},
                           {"rights_class": "UNKNOWN", "local_process": "ALLOW", "redistribute": "DENY", "external_ai_process": "DENY"}):
                with self.subTest(change=change):
                    bad = dict(row, assessment_id="rga_x", semantic_sha256="3" * 64, **change)
                    with self.assertRaises(ValidationError):
                        kb.insert("rights_assessment", bad)
        self.assertEqual(first["assessment_id"], again["assessment_id"])
        self.assertEqual([r["rights_class"] for r in rows], ["UNKNOWN", "PUBLIC_OA_LICENSED"])
        self.assertIsNotNone(rows[1]["supersedes_assessment_id"])
        self.assertEqual(open_tasks, [])


if __name__ == "__main__":
    unittest.main()
