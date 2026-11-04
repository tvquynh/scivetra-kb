"""S04-G9: synthetic 100-item pilot (no real data): status counts, review load and deterministic hashes."""
import shutil
import tempfile
import unittest

from tests.bridge_pilot import run_pilot


class PilotTests(unittest.TestCase):
    def test_synthetic_pilot_counts_and_determinism(self):
        """IDs: S04-PILOT, S04-G9. 100 candidate items (10 already acquired by RV) -> 90 exported rows; ~46 simulated PDFs with injected missing, duplicate, invalid, late, changed, access-barrier, no-url and unknown-status cases; round 2 verifies the retried rows, keeps both byte versions of re-downloaded rows, and an exact repeat is DUPLICATE_NOOP; no link is auto-accepted and no rights class is upgraded; two independent runs give identical manifest hashes and facts."""
        runs = []
        for _ in range(2):
            tmp = tempfile.mkdtemp(prefix="rkb-pilot-")
            try:
                runs.append(run_pilot(tmp))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        a, b = runs
        self.assertEqual(a, b)
        # The 10 ACQUIRED_AUTO items are not in RV's manual queue, so they are never exported.
        self.assertEqual((a["source_items"], a["exported"]["row_count"]), (100, 90))
        self.assertGreaterEqual(a["pdfs_written"], 30)
        self.assertLessEqual(a["pdfs_written"], 50)
        self.assertEqual(a["imports"]["round1"][0], 4)
        self.assertEqual(a["imports"]["repeat"][:2], (0, "DUPLICATE_NOOP"))
        self.assertEqual(a["final_status_counts"], {"DEFERRED": 4, "EXPORTED": 16, "FAILED": 6, "NEEDS_HUMAN_ACCESS": 4, "NEEDS_HUMAN_ACTION": 4,
                                                    "NO_URL": 5, "QUARANTINED": 2, "REJECTED_FILE": 5, "SKIPPED": 3, "UNKNOWN_STATUS": 2,
                                                    "VERIFIED_PDF": 39})
        self.assertEqual(a["review_tasks"], {"LATE_FILE_PRESENT": 2, "NEEDS_HUMAN_ACCESS": 8, "NEEDS_HUMAN_ACTION": 4, "NEW_BYTES_SAME_ROW": 2,
                                             "ROW_DUPLICATE": 4, "UNKNOWN_STATUS": 2})
        self.assertEqual(a["link_states"], {"UNVERIFIED": 41})
        self.assertEqual(a["rights_classes"], {"UNKNOWN": 41})
        self.assertEqual(a["counts"]["pdf_asset"], 41)
        self.assertEqual(a["exported"]["flag_counts"].get("SIGNED_URL_OMITTED"), 30)
        self.assertEqual(a["exported"]["flag_counts"].get("NO_DOI"), 4)
        self.assertEqual(a["exported"]["flag_counts"].get("MULTIPLE_DOI_SUPPRESSED"), 2)


if __name__ == "__main__":
    unittest.main()
