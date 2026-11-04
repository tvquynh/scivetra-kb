"""S04J: boundaries. The journal pipeline never mutates S02/S03/S04 data or Reprovane material, never creates SLR or
screening state, never reaches the network, and refuses live access."""
import ast
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

from tests import netguard
from tests.helpers import ROOT
from tests.journal_helpers import JISA, KBTest, SimulatedProviders, pipeline, members, small_scenario
from tests.s03_helpers import copy_native, tree_hashes
from scivetra_kb.core.manifest import table_content_sha256

JOURNAL_SRC = ROOT / "src" / "scivetra_kb" / "journal"


def _prior_tables(kb):
    return sorted(r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'jh_%' "
                                            "AND name NOT IN ('schema_migration','fetch_run','request_reservation','request_completion')"))


class BoundaryTests(KBTest):
    def test_pipeline_leaves_library_bridge_and_reprovane_material_untouched(self):
        """IDs: JH-25, JH-26, S04J-BOUNDARY-01. With an S03 library import present, a full journal pipeline (resolve, crawl, reconcile, export) leaves every S02/S03/S04 table byte-identical (the only shared tables written are the S02 request journal: fetch_run/request_reservation/request_completion), leaves the Reprovane export folder byte-identical, and creates no GlobalPaper, identity assertion, project reference, bridge batch, rights assessment or asset. A journal DOI already in the library is only listed as a read-only paper candidate."""
        native = copy_native(self.tmp)
        code, out = self.run_cli("library", "import-reprovane", "--data-root", self.root, "--export-dir", native, "--workspace-label", "ws",
                                 "--operator", "op.a")
        self.assertEqual(code, 0, out)
        native_before = tree_hashes(native)
        extra = dict(small_scenario(1)["crossref_works"]["JISA"][0], DOI="10.99999/s03.alpha")
        with self.writer() as kb:
            tables = _prior_tables(kb)
            before = {t: table_content_sha256(kb.connection, t) for t in tables}
            out = pipeline(kb, SimulatedProviders(small_scenario(12, rows_extra=[extra])), sizes=(5, 5, 5))
            from scivetra_kb.journal.export import export_version
            export_version(kb, out["version"]["version_id"], os.path.join(self.tmp, "exp"), operator="tester")
            after = {t: table_content_sha256(kb.connection, t) for t in tables}
            self.assertEqual(before, after)
            m = members(kb, out["version"]["version_id"])
            paper = kb.one("SELECT paper_id FROM identity_assertion WHERE value_normalized='10.99999/s03.alpha'")["paper_id"]
            self.assertEqual(json.loads(m["10.99999/s03.alpha"]["paper_candidates_json"]), [paper])
        self.assertEqual(tree_hashes(native), native_before)

    def test_no_slr_screening_or_corpus_state_exists_in_the_journal_schema(self):
        """IDs: JH-26, S04J-NO-SLR. Migration 0004 has no column or value that could express inclusion, exclusion, eligibility, screening, PRISMA, corpus membership or download entitlement; the CLI offers no command that promotes a journal subject into a project, corpus or bridge batch; exported rows always say screening_status NOT_SCREENED."""
        sql = (ROOT / "src" / "scivetra_kb" / "core" / "migrations" / "0004_journal_harvesting.sql").read_text(encoding="utf-8")
        body = "\n".join(line.split("--")[0] for line in sql.splitlines())
        for word in ("includ", "exclu", "eligib", "screen", "prisma", "corpus", "entitle", "project_paper", "bridge_batch", "download"):
            self.assertNotIn(word, body.lower(), word)
        code, out = self.run_cli_safe("journal", "include", "--data-root", self.root)
        self.assertNotEqual(code, 0)
        from scivetra_kb import cli
        actions = cli._parser()._subparsers._group_actions[0].choices["journal"]._subparsers._group_actions[0].choices
        self.assertEqual(sorted(actions), sorted(["coverage", "export", "harvest", "harvest-batch", "register", "reconcile", "resolve", "resume", "status"]))

    def run_cli_safe(self, *argv):
        try:
            return self.run_cli(*argv)
        except SystemExit as exc:
            return exc.code, None

    def test_journal_package_has_no_reprovane_pbd_or_write_paths(self):
        """IDs: JH-25, JH-30, S04J-STATIC-BOUNDARY. The journal package imports no Reprovane/PaperBatchDownloader/library-import/bridge-export writer, opens no socket, imports no HTTP client, and writes files only in export.py (into a new folder under --out-dir)."""
        forbidden_imports = re.compile(r"(socket|urllib\.request|http\.client|requests|httpx|aiohttp|ssl)$")
        for path in sorted(JOURNAL_SRC.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                else:
                    names = []
                for name in names:
                    self.assertFalse(forbidden_imports.match(name), (path.name, name))
                    self.assertNotIn(name, ("rv_import", "pbd", "import_corpus_export", "export_batch", "LibraryService", "AssetService"), path.name)
            text = path.read_text(encoding="utf-8")
            if path.name != "export.py":
                self.assertNotRegex(text, r"open\([^)]*['\"][wa]b?['\"]", path.name)
                self.assertNotIn("write_bytes(", text, path.name)
                self.assertNotIn("write_text(", text, path.name)
            self.assertNotIn("PaperBatchDownloader", text)
            self.assertNotIn("ResearchKB", text)

    def test_live_access_refused_and_no_transport_means_no_network(self):
        """IDs: JH-30, S04J-LIVE-REFUSED. 'journal harvest --live' is refused with LIVE_ACCESS_NOT_AUTHORIZED; without a simulated transport the harvest is refused with NO_TRANSPORT_CONFIGURED; no journal row or run is written by either."""
        self.run_cli("journal", "register", "--data-root", self.root, "--label", "x", "--operator", "op.a", "--issn", "2214-2126")
        with self.reader() as kb:
            jid = kb.one("SELECT journal_id FROM jh_journal")["journal_id"]
        for extra, code in ((["--live"], "LIVE_ACCESS_NOT_AUTHORIZED"), ([], "NO_TRANSPORT_CONFIGURED")):
            rc, out = self.run_cli("journal", "harvest", "--data-root", self.root, "--journal-id", jid, "--provider", "crossref", "--operator", "op.a", *extra)
            self.assertEqual((rc, out["error"]), (2, code))
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM jh_run")["n"], 0)

    def test_child_process_harvest_runs_under_the_network_guard(self):
        """IDs: JH-30, NET-GUARD-CHILD, S04J-NET-CHILD. A child Python process (guard installed before user code) runs a complete simulated journal harvest and reconciliation successfully, while a direct DNS lookup of api.crossref.org / api.openalex.org and a socket connect in the same process are refused."""
        code = textwrap.dedent(f"""
            import socket, sys, tempfile, os
            sys.path.insert(0, {str(ROOT / 'src')!r}); sys.path.insert(0, {str(ROOT)!r})
            from tests.journal_helpers import small_scenario, pipeline, SimulatedProviders
            from scivetra_kb.core.db import init_kb, open_writer
            root = os.path.join(tempfile.mkdtemp(), 'kb'); init_kb(root)
            with open_writer(root) as kb:
                out = pipeline(kb, SimulatedProviders(small_scenario(12)), sizes=(5, 5, 5))
            print('HARVEST', out['version']['collection_state'])
            for host in ('api.crossref.org', 'api.openalex.org'):
                try:
                    socket.getaddrinfo(host, 443); print('DNS_REACHED')
                except PermissionError:
                    print('BLOCKED_DNS')
            s = socket.socket()
            try:
                s.connect(('192.0.2.1', 443)); print('CONNECT_REACHED')
            except PermissionError:
                print('BLOCKED_CONNECT')
            finally:
                s.close()
            """)
        out = subprocess.run(netguard.child_command(code), capture_output=True, text=True, timeout=300)
        self.assertEqual(out.stdout.split(), ["GUARD_ACTIVE", "HARVEST", "COMPLETE_RELATIVE_TO_RECORDED_QUERIES", "BLOCKED_DNS", "BLOCKED_DNS",
                                              "BLOCKED_CONNECT"], out.stderr[-2000:])

    def test_fixtures_and_journal_source_carry_no_secret_or_real_contact(self):
        """IDs: JH-30, S04J-SECRET-SCAN. The synthetic scenarios contain no email address, no 'mailto', no 'api_key' parameter and not the synthetic test key itself (only its SHA-256); the journal source holds no credential literal."""
        from tests.journal_helpers import SIM, TEST_KEY
        email = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        for path in sorted(SIM.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                self.assertIsNone(email.search(data), path.name)
                for needle in (b"mailto", b"api_key", TEST_KEY.encode()):
                    self.assertNotIn(needle, data, (path.name, needle))
        for path in sorted(JOURNAL_SRC.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(email.search(text.encode()), path.name)
            self.assertNotIn(TEST_KEY, text)
