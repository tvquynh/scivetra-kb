"""S05: the rkb fte command line (JSON stdout, stable refusal codes, exit codes 0/2/3)."""
import json
import os
from pathlib import Path

from tests.fte_helpers import FTECase, PROTOCOL, ROSTER, write_ta_release


class FteCliTests(FTECase):
    def test_beginner_cli_path(self):
        """IDs: S05-CLI, S05-CLI-REFUSALS. Through rkb only: list sources; create protocol and run (a journal collection id is refused with exit 2); attach a human-confirmed PDF; record attempts; two reviewers disagree, the adjudicator decides; NOT_RETRIEVED after documented attempts; status/queue/summary; resolve; lock; export; verify the export (exit 0) and a tampered copy (exit 3); verify the lock; every JSON output that describes S05 state carries authority NON_AUTHORITATIVE."""
        export = os.path.join(self.tmp, "rv")
        items, sem, verify = write_ta_release(export, 2, release_id="crl_cli")
        self.run_cli("init", "--data-root", os.path.join(self.tmp, "unused"))
        code, out = self.run_cli("library", "import-reprovane", "--data-root", self.root, "--export-dir", export, "--workspace-label", "ws",
                                 "--operator", "op.lib")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("fte", "sources", "--data-root", self.root)
        source = out["eligible_sources"][0]["release_import_id"]
        proto = Path(self.tmp, "protocol.json")
        proto.write_text(json.dumps(PROTOCOL), encoding="utf-8")
        roster = Path(self.tmp, "roster.json")
        roster.write_text(json.dumps(ROSTER), encoding="utf-8")
        code, out = self.run_cli("fte", "protocol-create", "--data-root", self.root, "--protocol-file", str(proto), "--operator", "op.fte")
        pid = out["protocol_id"]
        base = ["--data-root", self.root, "--export-dir", export, "--expected-manifest-sha256", sem, "--rv-verify-json", verify, "--protocol-id", pid,
                "--roster-file", str(roster), "--label", "cli run", "--operator", "op.fte"]
        code, out = self.run_cli("fte", "create", "--source", "jcv_00000000000000000000000001", *base)
        self.assertEqual((code, out["error"]), (2, "JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE"))
        code, out = self.run_cli("fte", "create", "--source", source, *base)
        self.assertEqual((code, out["authority"]), (0, "NON_AUTHORITATIVE"))
        run_id = out["run_id"]
        code, out = self.run_cli("fte", "candidates", "--data-root", self.root, "--run-id", run_id)
        c0, c1 = out["candidates"]
        self.titles = {c0["corpus_item_id"]: "Synthetic full-text study number 0"}
        ev = self.add_pdf(c0)
        code, out = self.run_cli("fte", "attach-evidence", "--data-root", self.root, "--candidate-id", c0["candidate_id"], "--link-id", ev["link_id"],
                                 "--operator", "op.fte")
        self.assertTrue(out["evidence_ready"])
        cli = lambda *a: self.run_cli("fte", *a, "--data-root", self.root)  # noqa: E731
        self.assertEqual(cli("decide", "--candidate-id", c0["candidate_id"], "--reviewer", "rev.a", "--outcome", "FT_INCLUDE", "--page", "3")[0], 0)
        code, out = cli("decide", "--candidate-id", c0["candidate_id"], "--reviewer", "chatgpt.reviewer", "--outcome", "FT_INCLUDE")
        self.assertEqual((code, out["error"]), (2, "UNKNOWN_REVIEWER"))
        cli("decide", "--candidate-id", c0["candidate_id"], "--reviewer", "rev.b", "--outcome", "FT_EXCLUDE", "--reason", "WRONG_POPULATION")
        code, out = cli("adjudicate", "--candidate-id", c0["candidate_id"], "--adjudicator", "adj.c", "--outcome", "FT_INCLUDE", "--note",
                        "population matches C1")
        self.assertEqual((code, out["candidate_state"]["basis"]), (0, "ADJUDICATION"))
        for k, status in enumerate(("LOGIN_REQUIRED", "BOT_CHECK")):
            self.assertEqual(cli("attempt", "--candidate-id", c1["candidate_id"], "--status", status, "--source", "publisher site",
                                 "--attempted-on", f"2026-10-0{k + 1}", "--operator", "op.acq")[0], 0)
        for who in ("rev.a", "rev.b"):
            cli("decide", "--candidate-id", c1["candidate_id"], "--reviewer", who, "--outcome", "FT_NOT_RETRIEVED", "--reason",
                "NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS")
        code, out = self.run_cli("fte", "summary", "--data-root", self.root, "--run-id", run_id)
        self.assertEqual((out["ft_include"], out["ft_not_retrieved"], out["ft_pending"], out["authority"]), (1, 1, 0, "NON_AUTHORITATIVE"))
        self.assertEqual(self.run_cli("fte", "queue", "--data-root", self.root, "--run-id", run_id)[1]["authority"], "NON_AUTHORITATIVE")
        self.assertEqual(self.run_cli("fte", "status", "--data-root", self.root, "--run-id", run_id)[1]["state"], "REVIEW_IN_PROGRESS")
        self.assertEqual(self.run_cli("fte", "resolve", "--data-root", self.root, "--run-id", run_id, "--operator", "op.fte")[0], 0)
        code, out = self.run_cli("fte", "lock", "--data-root", self.root, "--run-id", run_id, "--operator", "op.fte")
        self.assertEqual((code, out["authority"]), (0, "NON_AUTHORITATIVE"))
        code, out = self.run_cli("fte", "export", "--data-root", self.root, "--run-id", run_id, "--out-dir", os.path.join(self.tmp, "x"), "--operator", "op.fte")
        folder = os.path.join(self.tmp, "x", out["folder_name"])
        self.assertEqual(self.run_cli("fte", "verify", "--export-dir", folder)[0], 0)
        Path(folder, "summary.json").write_text("{}", encoding="utf-8")
        code, out = self.run_cli("fte", "verify", "--export-dir", folder)
        self.assertEqual((code, out["valid"]), (3, False))
        code, out = self.run_cli("fte", "verify", "--data-root", self.root, "--run-id", run_id)
        self.assertEqual((code, out["valid"]), (0, True))
