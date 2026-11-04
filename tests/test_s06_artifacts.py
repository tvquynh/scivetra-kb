"""S06: immutable versioned ParseArtifacts, anchors, determinism, sandbox controls, CAS integrity, backup/restore, upgrade 5->6, CLI."""
import hashlib
import json
import os
import re
import shutil
import stat
import unittest
from pathlib import Path
from unittest import mock

from tests import s06_pdf_fixtures as F
from tests.helpers import ROOT
from tests.journal_helpers import make_older_kb
from tests.s06_helpers import S06Case
from scivetra_kb import cli
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader, open_writer
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import ImmutableRecordError, ValidationError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.parse import runtime, sandbox
from scivetra_kb.parse.service import ParseService, anchor, check_anchor

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Artifacts(S06Case):
    def doc(self, i=0, body=("Alpha beta gamma.", "Delta epsilon.")):
        return F.build_pdf([self.title_page(i, list(body)), F.text_page(["Page two line.", "Café über line."])], syntax="object_stream")

    def produced(self, i=0, **kw):
        a = self.register(f"d{i}.pdf", self.doc(i, **kw), paper=i)
        return a, self.parse(a["asset_sha256"], a["link_id"])

    def test_anchor_round_trip_and_page_numbering(self):
        """IDs: S06-ANCHOR-ROUNDTRIP, S06-ANCHOR-PAGE-1BASED, S06-ANCHOR-HASH. Every block anchor slices the stored canonical page text to exactly its
        recorded SHA-256; a quote anchor built for a Unicode span round-trips; a wrong hash, wrong page or out-of-range anchor fails; pages are 1-based."""
        a, p = self.produced()
        with self.reader() as kb:
            svc = self.svc(kb)
            verified = svc.verify(artifact_id=p["artifact_id"])
            row = kb.one("SELECT * FROM parse_artifact WHERE artifact_id=?", (p["artifact_id"],))
            payload = json.loads(kb.cas.read(row["artifact_sha256"]))
        self.assertTrue(verified["valid"], verified)
        self.assertGreaterEqual(verified["anchors_checked"], 6)
        self.assertEqual([pg["page"] for pg in payload["pages"]], [1, 2])
        text = payload["pages"][1]["text"]
        start = text.index("Café")
        ref = anchor(payload, row["artifact_sha256"], 2, start, start + len("Café über"))
        self.assertEqual(text[ref["start"]:ref["end"]], "Café über")
        self.assertEqual(ref["text_sha256"], hashlib.sha256("Café über".encode("utf-8")).hexdigest())
        self.assertTrue(check_anchor(payload, ref))
        self.assertFalse(check_anchor(payload, dict(ref, text_sha256="0" * 64)))
        self.assertFalse(check_anchor(payload, dict(ref, page=1)))
        self.assertFalse(check_anchor(payload, dict(ref, page=3)))
        with self.assertRaises(ValidationError):
            anchor(payload, row["artifact_sha256"], 2, 0, len(text) + 1)
        for block in payload["pages"][0]["blocks"]:
            self.assertEqual(hashlib.sha256(payload["pages"][0]["text"][block["start"]:block["end"]].encode()).hexdigest(), block["text_sha256"])
        with self.reader() as kb:
            self.assertTrue(self.svc(kb).verify_anchor(artifact_id=p["artifact_id"], page=2, start=ref["start"], end=ref["end"],
                                                       text_sha256=ref["text_sha256"])["valid"])

    def test_rerun_is_idempotent_and_config_change_creates_new_artifact(self):
        """IDs: S06-IDEMPOTENT, S06-RECHECK-DETERMINISTIC, S06-VERSIONED-CONFIG, S06-IMMUTABLE. A second parse REUSES the artifact; --recheck re-parses
        and reproduces identical bytes; a changed configuration creates a distinct artifact while the old one stays; artifact rows cannot be updated or
        deleted; no timestamps or local paths are embedded in the payload."""
        a, p = self.produced()
        again = self.parse(a["asset_sha256"], a["link_id"])
        self.assertEqual((again["outcome"], again["artifact_id"]), ("REUSED", p["artifact_id"]))
        recheck = self.parse(a["asset_sha256"], a["link_id"], recheck=True)
        self.assertEqual((recheck["outcome"], recheck["recheck"], recheck["artifact_sha256"]), ("REUSED", "IDENTICAL_BYTES", p["artifact_sha256"]))
        other = self.parse(a["asset_sha256"], a["link_id"], limits={"max_chars_per_page": 300_000})
        self.assertEqual(other["outcome"], "PRODUCED")
        self.assertNotEqual((other["artifact_id"], other["config_sha256"]), (p["artifact_id"], p["config_sha256"]))
        with self.writer() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM parse_artifact")["n"], 2)
            for sql in ("UPDATE parse_artifact SET status='SCANNED_NO_TEXT'", "DELETE FROM parse_artifact", "DELETE FROM parse_run",
                        "UPDATE parse_run SET outcome='REFUSED'"):
                with self.assertRaises(ImmutableRecordError):
                    with kb.transaction():
                        kb.execute(sql)
            raw = kb.cas.read(p["artifact_sha256"]).decode("utf-8")
        for needle in (self.tmp, self.root, "2026-11-01", self.site):
            self.assertNotIn(needle, raw)

    def test_identical_input_gives_identical_bytes_in_independent_kbs(self):
        """IDs: S06-DETERMINISM-CROSS-KB. The same PDF bytes parsed in two independent KBs give the same artifact SHA-256."""
        _, p = self.produced()
        other = S06Case("run")
        other.setUp()
        try:
            a2 = other.register("d0.pdf", self.doc(0))
            p2 = other.parse(a2["asset_sha256"], a2["link_id"])
        finally:
            other.tearDown()
        self.assertEqual(p2["artifact_sha256"], p["artifact_sha256"])

    def test_parser_runtime_pin_and_tamper_refused(self):
        """IDs: S06-PARSER-PIN, S06-PARSER-TAMPER, S06-PARSER-MISSING. A pypdf copy with another version, a modified file, or no parser at all is
        refused before any byte is opened (PARSER_VERSION_NOT_PINNED / PARSER_SITE_TAMPERED / PARSER_NOT_INSTALLED)."""
        a = self.register("d.pdf", self.doc())
        bad = Path(self.tmp, "site_bad")
        shutil.copytree(self.site, bad, ignore=shutil.ignore_patterns("__pycache__"))
        target = bad / "pypdf" / "_version.py"
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        target.write_text(target.read_text() + "\n# tampered\n")
        out = self.parse(a["asset_sha256"], a["link_id"], site=str(bad))
        self.assertEqual((out["outcome"], out["reasons"]), ("REFUSED", ["PARSER_SITE_TAMPERED"]))
        renamed = Path(self.tmp, "site_ver")
        shutil.copytree(self.site, renamed, ignore=shutil.ignore_patterns("__pycache__"))
        (renamed / "pypdf-6.12.1.dist-info").rename(renamed / "pypdf-6.12.0.dist-info")
        # S06 v02 (F01): a folder name that disagrees with METADATA and _version.py is an identity mismatch
        self.assertEqual(self.parse(a["asset_sha256"], a["link_id"], site=str(renamed))["reasons"], ["PARSER_METADATA_MISMATCH"])
        with mock.patch.object(runtime, "default_site", lambda: None):
            with self.writer() as kb:
                out = ParseService(kb, allow_language_level_network_guard=True).parse(asset_sha256=a["asset_sha256"], link_id=a["link_id"],
                                                                                       purpose="RESEARCH_PARSE", operator="op.x")
        self.assertEqual(out["reasons"], ["PARSER_NOT_INSTALLED"])
        self.assertEqual(self.count("parse_artifact"), 0)

    def test_parse_time_resource_limits_fail_safely(self):
        """IDs: S06-LIMIT-TIMEOUT, S06-LIMIT-CPU, S06-LIMIT-MEMORY, S06-LIMIT-PAGES, S06-LIMIT-OUTPUT, S06-LIMIT-DECOMPRESSION, S06-FAIL-AUDIT. A
        200,000-operator page under a 1 s wall clock (PARSER_TIMEOUT) or an 80 MiB OS memory cap (RESOURCE_LIMIT_EXCEEDED; measured: pypdf loads, the flood does not fit, on 3.11 and 3.14), a page limit below the
        page count, a per-page character limit and a stream limit all FAIL (the OS CPU limit is exercised by the sandbox self-test); each failure is an audited run without page content; the CAS
        asset is unchanged; no artifact exists."""
        flood = self.register("flood.pdf", F.operator_flood(200_000))
        normal = self.register("n.pdf", self.doc(1), paper=1)
        asset_before = sha(Path(self.root, "cas", "sha256", flood["asset_sha256"][:2], flood["asset_sha256"]))
        cases = [(flood, {"timeout_seconds": 1}, "PARSER_TIMEOUT"), (flood, {"memory_bytes": 80 * 1024 * 1024}, "RESOURCE_LIMIT_EXCEEDED"),
                 (normal, {"max_pages": 1}, "PAGE_LIMIT_EXCEEDED"),
                 (normal, {"max_chars_per_page": 5}, "OUTPUT_LIMIT_EXCEEDED"), (flood, {"max_stream_bytes": 1000}, "DECOMPRESSION_LIMIT_EXCEEDED")]
        for asset, limits, code in cases:
            with self.subTest(limits):
                out = self.parse(asset["asset_sha256"], asset["link_id"], limits=limits)
                self.assertEqual((out["outcome"], out["reasons"]), ("FAILED", [code]))
        self.assertEqual(self.count("parse_artifact"), 0)
        self.assertEqual(sha(Path(self.root, "cas", "sha256", flood["asset_sha256"][:2], flood["asset_sha256"])), asset_before)
        with self.reader() as kb:
            for run in kb.all("SELECT * FROM parse_run"):
                self.assertNotIn("Synthetic", run["sandbox_json"] + run["reason_codes_json"])

    def test_sandbox_self_tests_report_real_controls(self):
        """IDs: S06-SANDBOX-MEMORY, S06-SANDBOX-CPU, S06-SANDBOX-SPAWN-OS, S06-SANDBOX-NETWORK-HOOK, S06-SANDBOX-OPEN, S06-SANDBOX-TIMEOUT, S06-SANDBOX-OS-NETWORK.
        The child's OS memory cap, OS CPU limit and OS process limit act (Windows Job Object / POSIX rlimits); socket, DNS,
        file-open and process events are refused by the audit hook; a sleeping child is killed at the wall clock. OS network isolation is reported as
        it is: NONE on Windows (language-level guard only), LINUX_NETNS_EMPTY only where unshare succeeded."""
        probe = Path(self.tmp, "probe.bin")
        probe.write_bytes(b"x")
        limits = dict(sandbox.DEFAULT_LIMITS, memory_bytes=256 * 1024 * 1024, cpu_seconds=2, timeout_seconds=40)
        results = {name: sandbox.run_child(probe, self.site, self_test=name, limits=limits) for name in
                   ("memory", "network", "dns", "open", "spawn_hook", "spawn_os")}
        self.assertEqual(results["memory"]["result"]["outcome"], "LIMITED")
        for name in ("network", "dns", "open", "spawn_hook"):
            self.assertEqual(results[name]["result"]["outcome"], "REFUSED_BY_AUDIT_HOOK", name)
        expect_os_spawn = os.name == "nt" or results["spawn_os"]["result"]["sandbox"]["process_creation"] == "OS_RLIMIT_NPROC_0"
        if expect_os_spawn:
            self.assertEqual(results["spawn_os"]["result"]["outcome"], "REFUSED_BY_OS")
        self.assertEqual(sandbox.run_child(probe, self.site, self_test="cpu", limits=limits)["code"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(sandbox.run_child(probe, self.site, self_test="sleep", limits=dict(limits, timeout_seconds=3))["code"], "PARSER_TIMEOUT")
        iso = results["network"]["result"]["sandbox"]["os_network_isolation"]
        if os.name == "nt":
            self.assertEqual(iso, "NONE")
        self.assertIn(iso, ("NONE", "LINUX_NETNS_EMPTY"))
        a = self.register("d.pdf", self.doc())
        out = self.parse(a["asset_sha256"], a["link_id"], guard=False)
        if iso == "NONE":
            self.assertEqual((out["outcome"], out["reasons"], out["bytes_opened"]), ("REFUSED", ["OS_NETWORK_ISOLATION_UNAVAILABLE"], False))
        else:
            self.assertEqual(out["outcome"], "PRODUCED")

    def test_tampered_cas_file_swap_and_missing_derived_object(self):
        """IDs: S06-CAS-TAMPER, S06-FILE-SWAP, S06-DOCTOR-MISSING-DERIVED, S06-ORIGINAL-UNCHANGED. Altered asset bytes in CAS refuse parsing
        (CAS_HASH_MISMATCH); a staged copy changed during the parse fails STAGED_COPY_CHANGED; the original CAS asset is never modified by parsing;
        deleting a derived artifact object makes doctor --full and parse verify report it."""
        a, p = self.produced()
        cas_file = Path(self.root, "cas", "sha256", a["asset_sha256"][:2], a["asset_sha256"])
        original = cas_file.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), a["asset_sha256"])

        def swap(staged):
            os.chmod(staged, stat.S_IREAD | stat.S_IWRITE)
            staged.write_bytes(original + b"\n% swapped\n")
        out = self.parse(a["asset_sha256"], a["link_id"], recheck=True, faults={"after_child": swap})
        self.assertEqual(out["reasons"], ["STAGED_COPY_CHANGED"])
        self.assertEqual(cas_file.read_bytes(), original)
        os.chmod(cas_file, stat.S_IREAD | stat.S_IWRITE)
        cas_file.write_bytes(original[:-10] + b"tampered\n\n")
        out = self.parse(a["asset_sha256"], a["link_id"], recheck=True)
        self.assertEqual(out["reasons"], ["CAS_HASH_MISMATCH"])
        cas_file.write_bytes(original)
        derived = Path(self.root, "cas", "sha256", p["artifact_sha256"][:2], p["artifact_sha256"])
        os.chmod(derived, stat.S_IREAD | stat.S_IWRITE)
        derived.unlink()
        report = doctor(self.root, full=True)
        self.assertIn(f"CAS_MISSING:{p['artifact_sha256']}", report["problems"])
        with self.reader() as kb:
            self.assertEqual(self.svc(kb).verify(artifact_id=p["artifact_id"]), {"artifact_id": p["artifact_id"], "valid": False, "problems": ["CAS_MISSING"]})
        reused = self.parse(a["asset_sha256"], a["link_id"])
        self.assertEqual((reused["outcome"], reused["reasons"]), ("FAILED", ["CAS_MISSING"]))

    def test_backup_verify_restore_cover_derived_objects(self):
        """IDs: S06-BACKUP-DERIVED, S06-RESTORE-DERIVED. A backup carries the derived artifact objects; verify-backup passes; a restored KB verifies
        every artifact and anchor and doctor --full is clean (including the S06 invariants)."""
        _, p = self.produced()
        backup = create_backup(self.root, os.path.join(self.tmp, "backups"), clock=self.clock)
        self.assertTrue(verify_backup(os.path.join(self.tmp, "backups", backup["backup_name"]))["valid"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(os.path.join(self.tmp, "backups", backup["backup_name"]), restored)
        with open_reader(restored) as kb:
            self.assertTrue(ParseService(kb).verify(artifact_id=p["artifact_id"])["valid"])
        report = doctor(restored, full=True)
        self.assertEqual(report["problems"], [])
        self.assertEqual(set(report["parse_invariants"].values()), {0})


class Migration(S06Case):
    N_PAPERS = 1

    def test_0006_is_additive_and_prior_migrations_unchanged(self):
        """IDs: S06-MIG-HASH, S06-MIG-ADDITIVE, S06-MIG-FRESH-INIT. 0001-0005 are byte-identical to accepted S05 v02; 0006_pdf_parse_artifacts.sql
        only CREATEs parse_* tables, indexes and triggers; every parse_* table has no-update/no-delete triggers; a fresh KB is schema 6."""
        expected = json.loads((ROOT / "tools" / "s05v02_migration_hashes.json").read_text())
        actual = {m[1]: m[2] for m in packaged_migrations()}
        for name, digest in expected.items():
            self.assertEqual(actual[name], digest, name)
        self.assertEqual([m[1] for m in packaged_migrations()][5], "0006_pdf_parse_artifacts.sql")  # S07: docs/S07_TEST_ADAPTATIONS.md #19 (after S06)
        code = "\n".join(line.split("--")[0] for line in (MIG / "0006_pdf_parse_artifacts.sql").read_text(encoding="utf-8").splitlines())
        statements = list(migrations._statements((MIG / "0006_pdf_parse_artifacts.sql").read_text(encoding="utf-8")))
        self.assertGreater(len(statements), 10)
        for statement in statements:
            self.assertRegex(re.sub(r"--[^\n]*", "", statement).strip(), r"^CREATE (TABLE|INDEX|TRIGGER) ")
        self.assertNotRegex(code, r"(?i)\b(ALTER|DROP)\b")
        with self.reader() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)  # S07: docs/S07_TEST_ADAPTATIONS.md #19 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #19 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #18 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #18 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #18 (after S10, S09, S08, S07, S06)
            triggers = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
        for table in ("parse_artifact", "parse_run", "parse_export_event"):
            self.assertTrue({f"{table}_no_update", f"{table}_no_delete"} <= triggers)

    def test_schema5_upgrade_to_6_with_backup_and_rollback(self):
        """IDs: S06-UPGRADE-5-6, S06-ROLLBACK. A schema-5 KB is refused by normal opens; 'upgrade' backs up first, applies only 0006, keeps every prior
        table byte-identical; the kept pre-upgrade file restores the exact schema-5 bytes; parsing then works on the upgraded KB."""
        v5 = os.path.join(self.tmp, "kb_v5")
        make_older_kb(v5, 5, clock=self.clock, ids=self.ids, client="0.5.0.dev2")
        with mock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:5]):
            with open_reader(v5) as kb:
                before = {t: table_content_sha256(kb.connection, t) for t in
                          [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]}
        original = sha(Path(v5, DB_RELATIVE))
        out = upgrade_kb(v5, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (5, 11, ["0006_pdf_parse_artifacts.sql", "0007_ai_screening_proposals.sql", "0008_scientific_evidence_extraction.sql", "0009_local_retrieval.sql", "0010_ai_integration_gateway.sql", "0011_scientific_synthesis.sql"]))  # S07: docs/S07_TEST_ADAPTATIONS.md #20 (after S06); S08: docs/S08_TEST_ADAPTATIONS.md #20 (after S07, S06); S09: docs/S09_TEST_ADAPTATIONS.md #19 (after S08, S07, S06); S10: docs/S10_TEST_ADAPTATIONS.md #19 (after S09, S08, S07, S06); S11: docs/S11_TEST_ADAPTATIONS.md #19 (after S10, S09, S08, S07, S06)
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        with open_reader(v5) as kb:
            after = {t: table_content_sha256(kb.connection, t) for t in before}
        audit = ("kb_upgrade_event", "schema_migration")       # the upgrade appends its own ledger and audit rows
        self.assertEqual({k: v for k, v in after.items() if k not in audit}, {k: v for k, v in before.items() if k not in audit})
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v5, rollback)
        shutil.copyfile(Path(v5, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        self.assertEqual(doctor(v5, full=True)["problems"], [])


class Cli(S06Case):
    N_PAPERS = 1

    def cli(self, *argv):
        return cli.run(list(argv), clock=self.clock, ids=self.ids)

    def test_parse_commands(self):
        """IDs: S06-CLI-RUN, S06-CLI-DRY-RUN, S06-CLI-SHOW-VERIFY, S06-CLI-EXPORT, S06-CLI-REFUSAL-EXIT. dry-run opens no bytes and records nothing;
        run/status/show/verify/verify-anchor/export work; a refusal exits 3 with stable codes; show without --with-text carries no text."""
        a = self.register("d.pdf", F.build_pdf([self.title_page(0, ["CLI body line."])], syntax="xref_stream"))
        base = ["--data-root", self.root, "--asset", a["asset_sha256"], "--link-id", a["link_id"], "--operator", "op.cli", "--purpose", "RESEARCH_PARSE",
                "--parser-site", self.site, "--accept-language-level-network-guard"]
        code, dry = self.cli("parse", "run", *base, "--dry-run")
        self.assertEqual((code, dry["dry_run"], dry["would_refuse"], self.count("parse_run")), (0, True, [], 0))
        code, out = self.cli("parse", "run", *base)
        self.assertEqual((code, out["outcome"]), (0, "PRODUCED"))
        code, status = self.cli("parse", "status", "--data-root", self.root, "--asset", a["asset_sha256"])
        self.assertEqual((code, len(status["artifacts"])), (0, 1))
        code, shown = self.cli("parse", "show", "--data-root", self.root, "--artifact-id", out["artifact_id"])
        self.assertEqual(code, 0)
        self.assertNotIn("text", shown["pages"][0])
        code, shown = self.cli("parse", "show", "--data-root", self.root, "--artifact-id", out["artifact_id"], "--with-text", "--page", "1")
        self.assertIn("CLI body line.", shown["pages"][0]["text"])
        self.assertEqual(self.cli("parse", "verify", "--data-root", self.root, "--artifact-id", out["artifact_id"])[0], 0)
        b = shown["pages"][0]["blocks"][0]
        self.assertEqual(self.cli("parse", "verify-anchor", "--data-root", self.root, "--artifact-id", out["artifact_id"], "--page", "1", "--start",
                                  str(b["start"]), "--end", str(b["end"]), "--text-sha256", b["text_sha256"])[0], 0)
        self.assertEqual(self.cli("parse", "verify-anchor", "--data-root", self.root, "--artifact-id", out["artifact_id"], "--page", "1", "--start",
                                  str(b["start"]), "--end", str(b["end"]), "--text-sha256", "0" * 64)[0], 3)
        code, exported = self.cli("parse", "export", "--data-root", self.root, "--artifact-id", out["artifact_id"], "--out-dir",
                                  os.path.join(self.tmp, "ex"), "--operator", "op.cli")
        self.assertEqual((code, exported["contains_text"]), (0, False))
        code, refused = self.cli("parse", "run", "--data-root", self.root, "--asset", "cd" * 32, "--link-id", a["link_id"], "--operator", "op.cli",
                                 "--purpose", "RESEARCH_PARSE", "--parser-site", self.site, "--accept-language-level-network-guard")
        self.assertEqual((code, refused["outcome"], refused["reasons"]), (3, "REFUSED", ["ASSET_NOT_IN_KB"]))
        code, rt = self.cli("parse", "runtime-check", "--parser-site", self.site)
        self.assertEqual((code, rt["version"], rt["license"]), (0, "6.12.1", "BSD-3-Clause"))
        self.assertNotIn("site", rt)


if __name__ == "__main__":
    unittest.main()
