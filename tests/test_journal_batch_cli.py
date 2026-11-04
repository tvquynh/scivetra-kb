"""S04J: three-journal batch isolation and the beginner CLI smoke path (register -> resolve -> crawl -> interruption ->
resume with expired cursors -> OpenAlex -> reconcile -> export -> coverage -> backup/verify/restore -> doctor)."""
import json
import os
from unittest import mock

from tests.journal_helpers import JISA_DIR, KBTest, TEST_KEY, THREE_DIR, harvester, SimulatedProviders
from scivetra_kb.journal.service import run_batch, run_pipeline


class BatchTests(KBTest):
    def test_three_mock_publishers_are_isolated(self):
        """IDs: JH-27, S04J-BATCH. One batch over three mock journals and one invalid-ISSN entry: Alpha (two ISSNs incl. print/electronic) is COMPLETE; Beta (X check digit) has OpenAlex 5xx after the first page and is PARTIAL; Beta's CROSSREF_ONLY subjects carry OPENALEX_RUN_NOT_COMPLETE_SET_DIFFERENCE_UNRELIABLE and its coverage says set_differences_reliable=false; Gamma (absent from Crossref, two OpenAlex sources for its ISSN) is BLOCKED; the invalid entry is REFUSED (INVALID_ISSN) without stopping the batch. Alpha's subjects equal an Alpha-only run: failures elsewhere do not leak."""
        entries = json.loads((THREE_DIR / "batch.json").read_text(encoding="utf-8"))
        with self.writer() as kb:
            out = run_batch(harvester(kb, SimulatedProviders.from_directory(str(THREE_DIR))), entries, page_sizes={"CROSSREF": 100, "OPENALEX": 100})
        outcomes = [(j["label"].split(" (")[0], j["outcome"]) for j in out["journals"]]
        self.assertEqual(outcomes, [("Synthetic Alpha Journal of Systems", "COMPLETE_RELATIVE_TO_RECORDED_QUERIES"), ("Synthetic Beta Letters", "PARTIAL"),
                                    ("Synthetic Gamma Review", "BLOCKED"), ("Entry with an invalid ISSN", "REFUSED")])
        alpha, beta, gamma, bad = out["journals"]
        self.assertEqual(bad["error"], "INVALID_ISSN")
        self.assertEqual(beta["runs"]["OPENALEX"]["stop_reason"], "TRANSIENT_ERROR_RETRIES_EXHAUSTED_HTTP_502")
        self.assertEqual(beta["runs"]["CROSSREF"]["state"], "COMPLETE")
        rec = beta["version"]["coverage"]["reconciliation"]
        # Beta's OpenAlex crawl stopped early: its CROSSREF_ONLY subjects are flagged, and the set differences are declared unreliable.
        self.assertEqual((rec["set_differences_reliable"], rec["members_flagged_set_difference_unreliable"]), (False, rec["crossref_only"]))
        self.assertTrue(alpha["version"]["coverage"]["reconciliation"]["set_differences_reliable"])
        self.assertEqual((gamma["runs"]["CROSSREF"]["stop_reason"], gamma["runs"]["OPENALEX"]["stop_reason"]),
                         ("CROSSREF_JOURNAL_NOT_RESOLVED", "SOURCE_AMBIGUOUS_OPERATOR_SELECTION_REQUIRED"))
        self.assertEqual(alpha["version"]["coverage"]["reconciliation"]["work_types_of_members"], {"journal-article": 225, "other": 25})
        self.tearDown()
        self.setUp()
        with self.writer() as kb:
            solo = run_pipeline(harvester(kb, SimulatedProviders.from_directory(str(THREE_DIR))), entries[0], page_sizes={"CROSSREF": 100, "OPENALEX": 100})
        self.assertEqual(solo["version"]["coverage"]["reconciliation"], alpha["version"]["coverage"]["reconciliation"])


class CliSmokeTests(KBTest):
    def test_beginner_cli_path_end_to_end(self):
        """IDs: JH-31, JH-07, S04J-CLI-SMOKE. The documented beginner path runs through the rkb CLI only: init; register with the owner's historical count; resolve; Crossref harvest with the interrupt_once profile ends in SIMULATED_INTERRUPTION (exit 4) and status UNFINISHED; resume under the after_restart profile (every old cursor expired) replays and completes; OpenAlex primary and alternate crawls with the key from the environment; reconcile; export; coverage; backup, verify-backup, restore; doctor --full clean. Every stdout is one JSON document."""
        sim, cli = str(JISA_DIR), self.run_cli
        code, out = cli("journal", "register", "--data-root", self.root, "--label", "JISA-shaped synthetic", "--operator", "novice",
                        "--issn", "2214-2126", "--issn", "2214-2134", "--owner-count", "2151", "--owner-count-observed-on", "2026-11-01",
                        "--owner-count-note", "Owner-observed Crossref rows=0 total-results; historical only")
        self.assertEqual(code, 0, out)
        jid = out["journal_id"]
        self.assertEqual(cli("journal", "resolve", "--data-root", self.root, "--journal-id", jid, "--operator", "novice", "--transport-sim", sim)[0], 0)
        code, out = cli("journal", "harvest", "--data-root", self.root, "--journal-id", jid, "--operator", "novice", "--provider", "crossref",
                        "--page-size", "200", "--transport-sim", sim, "--sim-profile", "interrupt_once")
        self.assertEqual((code, out["error"]), (4, "SIMULATED_INTERRUPTION"))
        run_id = out["run_id"]
        code, out = cli("journal", "status", "--data-root", self.root, "--run-id", run_id)
        self.assertEqual((code, out["state"], out["counters"]["pages_ok"]), (0, "UNFINISHED", 5))
        code, out = cli("journal", "resume", "--data-root", self.root, "--run-id", run_id, "--operator", "novice", "--transport-sim", sim,
                        "--sim-profile", "after_restart")
        self.assertEqual((code, out["state"], out["counters"]["replays"]), (0, "COMPLETE", 1))
        self.assertGreaterEqual(out["counters"]["observations_stored"], 2200)
        with mock.patch.dict(os.environ, {"SCIVETRA_OPENALEX_API_KEY": TEST_KEY}):
            for provider in ("openalex", "openalex-alternate"):
                code, out = cli("journal", "harvest", "--data-root", self.root, "--journal-id", jid, "--operator", "novice", "--provider", provider,
                                "--transport-sim", sim)
                self.assertEqual((code, out["state"]), (0, "COMPLETE"), out)
        code, out = cli("journal", "reconcile", "--data-root", self.root, "--journal-id", jid, "--operator", "novice")
        self.assertEqual((code, out["collection_state"]), (0, "COMPLETE_RELATIVE_TO_RECORDED_QUERIES"))
        version = out["version_id"]
        code, out = cli("journal", "export", "--data-root", self.root, "--version-id", version, "--out-dir", os.path.join(self.tmp, "export"),
                        "--operator", "novice")
        self.assertEqual((code, out["written"]), (0, True))
        code, out = cli("journal", "coverage", "--data-root", self.root, "--version-id", version)
        self.assertEqual(out["coverage"]["owner_reported_counts"][0]["value"], 2151)
        self.assertEqual(cli("backup", "--data-root", self.root, "--target", os.path.join(self.tmp, "bk"))[0], 0)
        backup_dir = os.path.join(self.tmp, "bk", os.listdir(os.path.join(self.tmp, "bk"))[0])
        code, out = cli("verify-backup", "--backup", backup_dir)
        self.assertEqual((code, out["valid"]), (0, True))
        code, out = cli("restore", "--backup", backup_dir, "--data-root", os.path.join(self.tmp, "restored"))
        self.assertEqual(code, 0, out)
        code, out = cli("doctor", "--data-root", os.path.join(self.tmp, "restored"), "--full")
        self.assertEqual((code, out["problems"]), (0, []))
