"""S11 operational checks: migration 0011 (additive; 0001-0010 byte-identical to the accepted S10 v02), CHECK constraints, upgrade
10 -> 11 with backup and rollback, backup / restore of S11 records, the CLI, documentation / version and the S10 v02 baseline guard
(accepted tests changed only on marked lines)."""
import difflib
import hashlib
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.helpers import ROOT
from tests.journal_helpers import make_older_kb
from tests.s11_helpers import STUDY_A, STUDY_B, S11Case, ids_of
from scivetra_kb import cli
from scivetra_kb.core import migrations
from scivetra_kb.core.backup import create_backup, restore_backup, verify_backup
from scivetra_kb.core.db import DB_RELATIVE, open_reader
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError
from scivetra_kb.core.manifest import table_content_sha256
from scivetra_kb.core.migrations import packaged_migrations
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.synthesis import rules as R

MIG = ROOT / "src" / "scivetra_kb" / "core" / "migrations"
SYN_TABLES = ("syn_scope", "syn_claim", "syn_snapshot", "syn_audit_run", "syn_claim_audit", "syn_contradiction", "syn_review", "syn_export_event",
              "syn_share_request")


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contents(root):
    with open_reader(root) as kb:
        names = [r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: table_content_sha256(kb.connection, t) for t in names}


class Migration(S11Case):
    def populated(self):
        self.syn_world([[STUDY_A], [STUDY_B]])
        a = self.draft(0, fpr_operating_point="1")
        self.claim("C1", "REPORTED_VALUE", [a], measurement=self.tpr("93.1"))
        self.claim("C2", "COMPARISON", [a, self.draft(1, fpr_operating_point="1")], relation="LOWER")
        self.claim("C3", "THEMATIC", [self.draft(0, "LIMITATION")], evidence_class="LIMITATION")
        run = self.audit()
        self.scall("review", scope_id=self.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="CONFIRM_SUPPORT", claim_key="C3")
        self.scall("review", scope_id=self.syn, audit_id=run["audit_id"], reviewer="rev.two", decision="ATTEST_DRAFT_ACCURATE",
                   reason_codes=["UNVERIFIED_CLAIMS_ACKNOWLEDGED"])
        self.scall("export", scope_id=self.syn, out_dir=str(Path(self.tmp, "exp")), operator="op.exporter")
        with self.assertRaises(KBError):
            self.scall("share", scope_id=self.syn, destination_class="PUBLIC_REPOSITORY", operator="op.exporter")
        return run

    def test_0011_additive_and_0001_0010_unchanged(self):
        """IDs: S11-MIG-HASH, S11-MIG-ADDITIVE, S11-MIG-FRESH-INIT, S11-APPEND-ONLY-TRIGGERS. 0001-0010 are byte-identical to the accepted
        S10 v02 (tools/s10v02_migration_hashes.json); 0011_scientific_synthesis.sql only CREATEs syn_* tables and triggers; every syn_*
        table refuses UPDATE and DELETE; a fresh KB is schema 11."""
        expected = json.loads((ROOT / "tools" / "s10v02_migration_hashes.json").read_text())["migrations"]
        actual = {m[1]: m[2] for m in packaged_migrations()}
        self.assertEqual(len(expected), 10)
        self.assertEqual({k: actual[k] for k in expected}, expected)
        self.assertEqual([m[1] for m in packaged_migrations()][-1], "0011_scientific_synthesis.sql")
        for statement in migrations._statements((MIG / "0011_scientific_synthesis.sql").read_text(encoding="utf-8")):
            body = re.sub(r"--[^\n]*", "", statement).strip()
            self.assertRegex(body, r"^CREATE (TABLE|TRIGGER) syn_")
        self.populated()
        with self.writer() as kb:
            self.assertEqual(kb.schema["schema_version"], 11)
            triggers = {r["name"] for r in kb.all("SELECT name FROM sqlite_master WHERE type='trigger'")}
            for t in SYN_TABLES:
                self.assertTrue({f"{t}_no_update", f"{t}_no_delete"} <= triggers, t)
                self.assertGreater(kb.one(f"SELECT count(*) n FROM {t}")["n"], 0, t)
                col = kb.all(f"PRAGMA table_info({t})")[1]["name"]
                for sql in (f"UPDATE {t} SET {col}={col}", f"DELETE FROM {t}"):
                    with self.assertRaises(KBError, msg=sql):
                        kb.execute(sql)

    def test_check_constraints_fail_closed(self):
        """IDs: S11-MIG-CHECKS. The schema refuses a publication other than PUBLICATION_NOT_AUTHORIZED, a SUPPORTED verdict without the
        CITED marker, a non-SUPPORTED verdict without a visible marker, a snapshot with quotes, an ALLOWED share request, an export
        destination other than LOCAL_REVIEW_ONLY and a review bound to another report hash."""
        self.populated()
        with self.writer() as kb:
            scope, ca = kb.one("SELECT * FROM syn_scope"), kb.one("SELECT * FROM syn_claim_audit")
            snap, share = kb.one("SELECT * FROM syn_snapshot"), kb.one("SELECT * FROM syn_share_request")
            ex, rv = kb.one("SELECT * FROM syn_export_event"), kb.one("SELECT * FROM syn_review")
            for table, row in (("syn_scope", dict(scope, scope_id="sys_x", publication="PUBLISHED", semantic_sha256="1" * 64)),
                               ("syn_claim_audit", dict(ca, row_id="syl_x", verdict="SUPPORTED", marker="[UNVERIFIED]")),
                               ("syn_claim_audit", dict(ca, row_id="syl_y", verdict="PARTIAL", marker="CITED")),
                               ("syn_snapshot", dict(snap, snapshot_id="syq_x", contains_quotes=1)),
                               ("syn_share_request", dict(share, request_id="syh_x", decision="ALLOWED")),
                               ("syn_export_event", dict(ex, export_id="sye_x", phase="RESERVED", reservation_id=None, outcome_code=None, destination="EMAIL")),
                               ("syn_review", dict(rv, review_id="syr_x", report_sha256="2" * 64))):
                with self.assertRaises(KBError, msg=table):
                    with kb.transaction():
                        kb.insert(table, row)

    def test_upgrade_10_to_11_backup_first_and_rollback(self):
        """IDs: S11-UPGRADE-10-11, S11-ROLLBACK. A schema-10 KB is upgraded only after a verified backup; only 0011 is applied; every prior
        table is byte-identical (except the ledger and upgrade audit rows); the kept pre-upgrade file restores the exact schema-10 bytes;
        doctor reports no problem."""
        v10 = os.path.join(self.tmp, "kb_v10")
        make_older_kb(v10, 10, clock=self.clock, ids=self.ids, client="0.10.0.dev2")
        with umock.patch.object(migrations, "packaged_migrations", lambda: packaged_migrations()[:10]):
            before = contents(v10)
        original = sha(Path(v10, DB_RELATIVE))
        out = upgrade_kb(v10, os.path.join(self.tmp, "backups"), clock=self.clock, ids=self.ids)
        self.assertEqual((out["from_version"], out["to_version"], [a["name"] for a in out["applied"]]), (10, 11, ["0011_scientific_synthesis.sql"]))
        self.assertTrue(out["checks"]["prior_tables_byte_identical"])
        after = contents(v10)
        audit = ("kb_upgrade_event", "schema_migration")
        self.assertEqual({k: v for k, v in after.items() if k in before and k not in audit}, {k: v for k, v in before.items() if k not in audit})
        self.assertEqual(sorted(set(after) - set(before)), sorted(SYN_TABLES))
        rollback = os.path.join(self.tmp, "rollback")
        shutil.copytree(v10, rollback)
        shutil.copyfile(Path(v10, "db", out["pre_upgrade_file"]), Path(rollback, DB_RELATIVE))
        self.assertEqual(sha(Path(rollback, DB_RELATIVE)), original)
        self.assertEqual(doctor(v10, full=True)["problems"], [])

    def test_backup_and_restore_carry_s11_records(self):
        """IDs: S11-BACKUP-RESTORE. A KB with scopes, claims, snapshots, audits, contradictions, reviews, exports and a denied share backs
        up, verifies and restores with every table byte-identical; the restored KB reproduces the same audit report and review state."""
        run = self.populated()
        backup = create_backup(self.root, os.path.join(self.tmp, "bk"), clock=self.clock)
        self.assertTrue(verify_backup(backup["backup_dir"])["valid"])
        restored = os.path.join(self.tmp, "restored")
        restore_backup(backup["backup_dir"], restored)
        self.assertEqual(contents(restored), contents(self.root))
        from scivetra_kb.core.db import KB
        from scivetra_kb.synthesis.service import SynthesisService
        kb = KB(restored, readonly=False, clock=self.clock, ids=self.ids)
        try:
            st = SynthesisService(kb).status(scope_id=self.syn)
        finally:
            kb.close()
        self.assertEqual((st["audit_id"], st["report_sha256"], st["audit_current"], st["review_state"]),
                         (run["audit_id"], run["report_sha256"], True, "HUMAN_REVIEWED_NON_AUTHORITATIVE_DRAFT"))


class CliAndDocs(S11Case):
    def test_cli_journey(self):
        """IDs: S11-CLI, S11-CLI-SHARE-DENIED. rkb synth scope-create / claim-add (spec file) / audit / review / status / export run end to
        end; share exits non-zero with SHARING_NOT_AUTHORIZED."""
        run, items, ev_scope, _ = self.mocked([[STUDY_A], [STUDY_B]])
        self.roster("rev.one", "rev.two")
        self.accept_all(ev_scope)
        self.items, self.ev_scope = items, ev_scope

        def rkb(*argv):
            return cli.run(list(argv), clock=self.clock, ids=self.ids)
        code, out = rkb("synth", "scope-create", "--data-root", self.root, "--evidence-scope-id", ev_scope, "--label", "cli scope", "--operator", "op.synth",
                        "--review-policy", "SINGLE_HUMAN", "--review-policy-reason", "one reviewer")
        self.assertEqual((code, out["status"]), (0, "DRAFT_NON_AUTHORITATIVE"))
        spec = Path(self.tmp, "spec.json")
        spec.write_text(json.dumps({"schema": R.SPEC_SCHEMA, "kind": "REPORTED_VALUE", "measurement": self.tpr("93.1")}), encoding="utf-8")
        scope = out["scope_id"]
        self.assertEqual(rkb("synth", "claim-add", "--data-root", self.root, "--scope-id", scope, "--claim-key", "C1", "--spec-file", str(spec),
                             "--cite", self.draft(0, fpr_operating_point="1"), "--operator", "op.author")[0], 0)
        code, audit = rkb("synth", "audit", "--data-root", self.root, "--scope-id", scope, "--operator", "op.auditor")
        self.assertEqual((code, audit["report"]["claims"][0]["verdict"], audit["report"]["citation_audit"]["traceability"]), (0, "SUPPORTED", "100%"))
        self.assertEqual(rkb("synth", "review", "--data-root", self.root, "--scope-id", scope, "--audit-id", audit["audit_id"], "--reviewer", "rev.two",
                             "--decision", "ATTEST_DRAFT_ACCURATE")[1]["state"], "HUMAN_REVIEWED_NON_AUTHORITATIVE_DRAFT")
        self.assertEqual(rkb("synth", "status", "--data-root", self.root, "--scope-id", scope)[1]["publication"], "PUBLICATION_NOT_AUTHORIZED")
        code, exp = rkb("synth", "export", "--data-root", self.root, "--scope-id", scope, "--out-dir", str(Path(self.tmp, "cli_exp")), "--operator", "op.e")
        self.assertEqual((code, exp["destination"]), (0, "LOCAL_REVIEW_ONLY"))
        with self.assertRaises(KBError) as ctx:
            rkb("synth", "share", "--data-root", self.root, "--scope-id", scope, "--destination-class", "JOURNAL_SUBMISSION", "--operator", "op.e")
        self.assertEqual(ctx.exception.code, "SHARING_NOT_AUTHORIZED")

    def test_docs_and_version(self):
        """IDs: S11-DOCS, S11-STATIC-VERSION. Version 1.0.0; the ADR, beginner guide, rubric, scenario catalogue and test adaptations
        exist and state the authority boundary, gates, verdicts and limits."""
        from scivetra_kb import __version__
        self.assertEqual(__version__, "1.0.0")
        docs = ROOT / "docs"
        adr = (docs / "ADR_S11.md").read_text(encoding="utf-8")
        for needle in ("DRAFT_NON_AUTHORITATIVE", "PUBLICATION_NOT_AUTHORIZED", "OAG-7", "A3", "A4", "A5", "A7", "A8", "synthesis_eligible",
                       "0011_scientific_synthesis.sql", R.RUBRIC_ID, R.SPEC_SCHEMA, "[UNVERIFIED]", "[CONFLICTED]", "NOT_ASSESSED", *R.VERDICTS):
            self.assertIn(needle, adr)
        guide = (docs / "S11_SYNTHESIS_GUIDE.md").read_text(encoding="utf-8")
        for needle in ("rkb synth scope-create", "rkb synth claim-add", "rkb synth audit", "rkb synth review", "rkb synth export", "LOCAL_REVIEW_ONLY"):
            self.assertIn(needle, guide)
        catalogue = (docs / "S11_SCENARIO_CATALOGUE.md").read_text(encoding="utf-8")
        for needle in ("S11-JOURNEY-POSITIVE", "S11-JOURNEY-NEGATIVE", "S11-NUM-12-312", "S11-OAG7-A7-SHARE-DENIED"):
            self.assertIn(needle, catalogue)
        adaptations = (docs / "S11_TEST_ADAPTATIONS.md").read_text(encoding="utf-8")
        for n in range(1, 32):
            self.assertIn(f"| {n} |", adaptations)

    def test_accepted_s10_tests_changed_only_on_marked_lines(self):
        """IDs: S11-TEST-ADAPTATIONS, S11-BASELINE-PRESERVED. Every S10 v02 test and tool file is still present; files outside the adapted
        set are byte-identical to the accepted S10 v02 ZIP (tools/s10v02_test_baseline.json); in adapted files every changed or added
        block carries an 'S11' marker; no accepted line is deleted."""
        adapted = {"tests/test_core.py", "tests/test_cli.py", "tests/test_s03_cli.py", "tests/test_upgrade_backup.py", "tests/test_bridge_paths_backup.py",
                   "tests/test_journal_migration.py", "tests/test_s04j_static.py", "tests/test_s05_static.py", "tests/test_fte_migration.py",
                   "tests/test_s06_static.py", "tests/test_s06_artifacts.py", "tests/test_s07_operational.py", "tests/test_s08_operational.py",
                   "tests/test_s09_operational.py", "tests/test_s10_operational.py", "tools/required_ids.json"}
        baseline = json.loads((ROOT / "tools" / "s10v02_test_baseline.json").read_text(encoding="utf-8"))["files"]
        changed = 0
        for rel, info in sorted(baseline.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), rel)
            data = path.read_bytes()
            if rel not in adapted:
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"], rel)
                continue
            lines = data.splitlines()
            now = [hashlib.sha256(line).hexdigest()[:16] for line in lines]
            for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=info["line_sha256"], b=now, autojunk=False).get_opcodes():
                if tag == "equal":
                    continue
                self.assertNotEqual(tag, "delete", (rel, a0, a1))
                block = [lines[i].decode("utf-8") for i in range(b0, b1)]
                if rel.startswith("tests/"):
                    self.assertTrue(any("S11" in line for line in block), (rel, block))
                    changed += b1 - b0
        self.assertLess(changed, 45)


if __name__ == "__main__":
    unittest.main()
