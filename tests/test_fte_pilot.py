"""S05: 100-candidate synthetic pilot with exact provisional accounting (no final Reprovane corpus is produced)."""
import json
import shutil
import tempfile
import unittest

from tests.fte_pilot import EXPECTED, run_fte_pilot
from scivetra_kb.eligibility.handoff import verify_export


class PilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rkb-fte-pilot-")
        cls.out = run_fte_pilot(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pilot_accounting_reconciles_exactly(self):
        """IDs: S05-PILOT-100, S05-ACCOUNTING, S05-NOT-RETRIEVED-DISTINCT. 100 TA candidates = 39 FT_INCLUDE + 12 FT_EXCLUDE + 18 FT_NOT_RETRIEVED + 31 FT_PENDING, exactly as planned; NOT_RETRIEVED reasons are counted separately from exclusion reasons; studies (39) and reports of included studies (41, two human-linked duplicate reports) are distinct; superseded/stale votes are not counted; the run cannot be locked while pending (RUN_NOT_RESOLVED)."""
        s = self.out["summary"]
        self.assertEqual({k: s[k] for k in EXPECTED}, EXPECTED)
        self.assertEqual(s["ta_candidate_count"], 100)
        self.assertTrue(s["invariant"]["holds"])
        self.assertEqual(s["ft_exclude_by_reason"], {"DUPLICATE_REPORT_OF_INCLUDED_STUDY": 2, "RETRACTED": 1, "WRONG_OUTCOME": 2, "WRONG_POPULATION": 4,
                                                     "WRONG_STUDY_DESIGN": 3})
        self.assertEqual(s["ft_not_retrieved_by_reason"], {"NOT_AVAILABLE_FROM_AUTHORS": 3, "NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS": 15})
        self.assertEqual(s["ft_pending_by_reason"], {"AWAITING_REVIEWS": 28, "NEEDS_ADJUDICATION": 2, "STALE_DECISION_EVIDENCE_CHANGED": 1})
        self.assertEqual((s["reports_vs_studies"]["studies_included"], s["reports_vs_studies"]["reports_of_included_studies"]), (39, 41))
        self.assertEqual(s["final_basis"], {"ADJUDICATION": 6, "CONSENSUS": 63})
        self.assertEqual(s["status"], "PROVISIONAL")
        self.assertEqual(self.out["lock_refusal"], "RUN_NOT_RESOLVED")

    def test_pilot_queue_and_export(self):
        """IDs: S05-PILOT-QUEUE, S05-QUEUE. The project queue lists missing full text, manual acquisition (login/bot check), retryable acquisition, restricted rights, protocol-version mismatch (preprint), unconfirmed identity, external hash-only evidence, integrity alert, unresolved reviews, needed adjudications and changed evidence; an unconfirmed PDF is refused as evidence; the provisional export verifies offline and states NON_AUTHORITATIVE."""
        cats = self.out["queue_categories"]
        for cat in ("MISSING_FULLTEXT", "MANUAL_ACQUISITION_NEEDED", "RETRYABLE_ACQUISITION", "RESTRICTED_RIGHTS", "VERSION_MISMATCH",
                    "IDENTITY_UNCONFIRMED_ASSET", "EXTERNAL_EVIDENCE_HASH_ONLY", "INTEGRITY_ALERT", "UNRESOLVED_REVIEW", "NEEDS_ADJUDICATION",
                    "CHANGED_EVIDENCE"):
            self.assertIn(cat, cats)
        # 5 restricted licences + 5 preprints whose rights stay UNKNOWN (default deny: local processing UNDETERMINED)
        self.assertEqual(cats["RESTRICTED_RIGHTS"], 10)
        self.assertEqual(cats["IDENTITY_UNCONFIRMED_ASSET"], 5)
        self.assertEqual(self.out["refused_attach"], {"unconfirmed": "EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED"})
        check = verify_export(self.out["export_dir"])
        self.assertTrue(check["valid"], check)
        self.assertEqual((check["state"], check["authority"]), ("UNLOCKED_PROVISIONAL", "NON_AUTHORITATIVE"))
        summary = json.load(open(self.out["export_dir"] + "/summary.json", encoding="utf-8"))
        self.assertEqual(summary["ft_pending"], 31)
