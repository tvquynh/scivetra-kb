"""S06 v02 regressions: S06-F01 parser identity/provenance, S06-F02 durable export audit, privileged POSIX accounts."""
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import unittest
from pathlib import Path
from unittest import mock

from tests import s06_pdf_fixtures as F
from tests.s06_helpers import S06Case, extract_wheel, official_wheel
from tests.test_s06_gates import insert_assessment
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError, PolicyRefusal, ValidationError
from scivetra_kb.parse import runtime, sandbox
from scivetra_kb.parse import service as parse_service
from scivetra_kb.parse.approved_parsers import APPROVED, PINNED
from scivetra_kb.parse.service import ParseService


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def fake_site(root: Path, *, folder="6.12.1", meta="6.12.1", module="6.12.1") -> str:
    """A self-consistent, self-signed counterfeit: complete RECORD, chosen versions, one module file."""
    root.mkdir(parents=True)
    code = f'__version__ = "{module}"\n'.encode()
    (root / "pypdf").mkdir()
    (root / "pypdf" / "__init__.py").write_bytes(b"from ._version import __version__\n")
    (root / "pypdf" / "_version.py").write_bytes(code)
    dist = root / f"pypdf-{folder}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(f"Metadata-Version: 2.4\nName: pypdf\nVersion: {meta}\nLicense-Expression: BSD-3-Clause\n", encoding="utf-8")
    rows = [f"pypdf/{n},sha256={b64((root / 'pypdf' / n).read_bytes())},{(root / 'pypdf' / n).stat().st_size}" for n in ("__init__.py", "_version.py")]
    (dist / "RECORD").write_text("\n".join(rows + [f"pypdf-{folder}.dist-info/RECORD,,"]) + "\n", encoding="utf-8")
    return str(root)


def code_of(call):
    try:
        call()
    except KBError as exc:
        return exc.code
    return "ACCEPTED"


class ParserProvenance(S06Case):
    N_PAPERS = 1

    def test_counterfeit_and_mutated_parsers_are_refused(self):
        """IDs: S06-F01-FAKE-SITE, S06-F01-FORGED-METADATA, S06-F01-SELF-SIGNED-RECORD, S06-F01-MODIFIED-RECORD, S06-F01-MUTATED-CODE-UPDATED-RECORD, S06-F01-UNLISTED-FILE, S06-F01-OTHER-VERSION. The architect's fake (folder 6.12.1, METADATA 0.0.0) and every disagreement of folder, METADATA and _version.py fail PARSER_METADATA_MISMATCH; a fully consistent self-signed 6.12.1 counterfeit fails PARSER_BUILD_NOT_APPROVED (a self-attested RECORD is integrity, not provenance); a real copy with a wrong RECORD hash or an unlisted file fails PARSER_SITE_TAMPERED; real code mutated with its RECORD line updated fails PARSER_BUILD_NOT_APPROVED; a consistent other version fails PARSER_VERSION_NOT_PINNED."""
        t = Path(self.tmp)
        self.assertEqual(code_of(lambda: runtime.verify_site(fake_site(t / "a", meta="0.0.0", module="0.0.0"))), "PARSER_METADATA_MISMATCH")
        self.assertEqual(code_of(lambda: runtime.verify_site(fake_site(t / "b", module="0.0.0"))), "PARSER_METADATA_MISMATCH")
        self.assertEqual(code_of(lambda: runtime.verify_site(fake_site(t / "c", folder="6.12.0"))), "PARSER_METADATA_MISMATCH")
        self.assertEqual(code_of(lambda: runtime.verify_site(fake_site(t / "d"))), "PARSER_BUILD_NOT_APPROVED")
        self.assertEqual(code_of(lambda: runtime.verify_site(fake_site(t / "e", folder="7.0.0", meta="7.0.0", module="7.0.0"))), "PARSER_VERSION_NOT_PINNED")
        real = t / "real"
        shutil.copytree(self.site, real, ignore=shutil.ignore_patterns("__pycache__"))
        record = next(real.glob("pypdf-*.dist-info")) / "RECORD"
        original = record.read_text(encoding="utf-8")
        target = real / "pypdf" / "_utils.py"
        self.assertTrue(target.is_file())
        record.write_text(original.replace("pypdf/_utils.py,sha256=", "pypdf/_utils.py,sha256=AAAA", 1), encoding="utf-8")
        self.assertEqual(code_of(lambda: runtime.verify_site(str(real))), "PARSER_SITE_TAMPERED")
        mutated = target.read_bytes() + b"\n# mutated\n"
        target.write_bytes(mutated)
        lines = [l if not l.startswith("pypdf/_utils.py,") else f"pypdf/_utils.py,sha256={b64(mutated)},{len(mutated)}" for l in original.splitlines()]
        record.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(code_of(lambda: runtime.verify_site(str(real))), "PARSER_BUILD_NOT_APPROVED")
        record.write_text(original, encoding="utf-8")
        target.write_bytes(mutated[: -len(b"\n# mutated\n")])
        self.assertEqual(runtime.verify_site(str(real))["provenance"], "CONTENT_MATCHES_OFFICIAL_WHEEL")
        (real / "pypdf" / "extra_payload.py").write_bytes(b"x = 1\n")
        self.assertEqual(code_of(lambda: runtime.verify_site(str(real))), "PARSER_SITE_TAMPERED")

    def test_official_wheel_and_identical_installed_copy_are_trusted(self):
        """IDs: S06-F01-OFFICIAL-WHEEL, S06-F01-APPROVED-COPY, S06-F01-WHEEL-ALTERED, S06-F01-TRUST-ANCHOR. The official wheel verifies by its SHA-256 against the independently recorded PyPI value (never read from the wheel); an install-like extraction of it reproduces the reviewed fingerprint; both report the same build; one appended byte makes the wheel PARSER_WHEEL_NOT_OFFICIAL; the trust anchor is the reviewed constant table."""
        wheel = official_wheel()
        w = runtime.verify_site(wheel)
        self.assertEqual((w["mode"], w["provenance"], w["wheel_sha256"]),
                         ("OFFICIAL_WHEEL", "OFFICIAL_WHEEL_SHA256_VERIFIED", "8fa2a2321cf16247ed848bd7c97f193a60c08670d04abed5b0138327e51c43b0"))
        d = runtime.verify_site(extract_wheel(wheel, Path(self.tmp, "site")))
        self.assertEqual((d["mode"], d["provenance"], d["files_verified"]), ("SITE_DIRECTORY", "CONTENT_MATCHES_OFFICIAL_WHEEL", 56))
        self.assertEqual(w["build_sha256"], d["build_sha256"])
        self.assertEqual(w["build_sha256"], APPROVED[PINNED]["package_files_sha256"])
        altered = Path(self.tmp, "pypdf-6.12.1-py3-none-any.whl")
        altered.write_bytes(Path(wheel).read_bytes() + b"\0")
        self.assertEqual(code_of(lambda: runtime.verify_site(str(altered))), "PARSER_WHEEL_NOT_OFFICIAL")

    def test_child_reports_imported_identity_and_parent_rejects_mismatch(self):
        """IDs: S06-F01-CHILD-IDENTITY, S06-F01-PARENT-COMPARE, S06-F01-WHEEL-IMPORT, S06-F01-NO-ARTIFACT-ON-MISMATCH. The sandbox child reports the version and package fingerprint it actually imported (from a site directory and directly from the official wheel); a different expected identity is PARSER_IDENTITY_MISMATCH; a parse whose staged parser copy no longer matches fails with no artifact; artifacts record the verified build and provenance."""
        probe = Path(self.tmp, "probe.bin")
        probe.write_bytes(b"x")
        for site in (self.site, official_wheel()):
            rt = runtime.verify_site(site)
            ok = sandbox.run_child(probe, rt["site"], self_test="open", expected_identity=runtime.identity(rt))
            self.assertEqual(ok["status"], "OK", ok)
            reported = ok["result"]["sandbox"]["parser_identity"]
            self.assertEqual((reported["version"], reported["build_sha256"], reported["imported_from_site"]), ("6.12.1", rt["build_sha256"], True))
            bad = sandbox.run_child(probe, rt["site"], self_test="open", expected_identity={"version": "6.12.1", "build_sha256": "0" * 64})
            self.assertEqual(bad["code"], "PARSER_IDENTITY_MISMATCH")
        a = self.register("d.pdf", F.build_pdf([self.title_page(0)], syntax="object_stream"))
        with mock.patch.object(ParseService, "_stage_parser", staticmethod(lambda rt, work: None)):
            out = self.parse(a["asset_sha256"], a["link_id"])
        self.assertEqual((out["outcome"], out["reasons"], self.count("parse_artifact")), ("FAILED", ["PARSER_IDENTITY_MISMATCH"], 0))
        out = self.parse(a["asset_sha256"], a["link_id"], site=official_wheel())
        self.assertEqual(out["outcome"], "PRODUCED")
        with self.reader() as kb:
            payload = json.loads(kb.cas.read(out["artifact_sha256"]))
            run = json.loads(kb.one("SELECT sandbox_json FROM parse_run WHERE run_id=?", (out["run_id"],))["sandbox_json"])
        self.assertEqual(payload["parser"]["provenance"], "OFFICIAL_BUILD_VERIFIED")
        self.assertEqual(payload["parser"]["official_wheel_sha256"], APPROVED[PINNED]["official_wheel_sha256"])
        self.assertEqual(run["parser_runtime"]["provenance"], "OFFICIAL_WHEEL_SHA256_VERIFIED")
        dir_out = self.parse(a["asset_sha256"], a["link_id"], site=self.site)
        self.assertEqual((dir_out["outcome"], dir_out["artifact_id"]), ("REUSED", out["artifact_id"]))       # same build either way

    def test_privileged_posix_account_is_refused_and_labelled(self):
        """IDs: S06-ROOT-REFUSED, S06-ROOT-LABEL, S06-ROOT-EXPLICIT-ACCEPT. A privileged POSIX account (RLIMIT_NPROC does not bind root) refuses parsing and parser-backed intake with PARSER_PRIVILEGED_ACCOUNT_REFUSED before any document byte is read, unless the operator explicitly accepts it for synthetic input (recorded); the child labels process creation NOT_ENFORCED_PRIVILEGED_ACCOUNT for root (label logic checked statically here; Linux root run NOT_TESTED on Windows)."""
        a = self.register("d.pdf", F.build_pdf([self.title_page(0)], syntax="object_stream"))
        probe = Path(self.tmp, "probe.bin")
        probe.write_bytes(b"x")
        with mock.patch.object(sandbox, "is_privileged", lambda: True):
            self.assertEqual(sandbox.run_child(probe, self.site, self_test="open")["code"], "PARSER_PRIVILEGED_ACCOUNT_REFUSED")
            refused = self.parse(a["asset_sha256"], a["link_id"])
            self.assertEqual((refused["outcome"], refused["reasons"]), ("REFUSED", ["PARSER_PRIVILEGED_ACCOUNT_REFUSED"]))
            with self.writer() as kb:
                accepted = ParseService(kb, parser_site=self.site, allow_language_level_network_guard=True, allow_privileged_account=True).parse(
                    asset_sha256=a["asset_sha256"], link_id=a["link_id"], purpose="RESEARCH_PARSE", operator="op.x")
            self.assertEqual(accepted["outcome"], "PRODUCED")
            self.assertTrue(accepted["isolation"]["privileged_account"])
            self.put("i.pdf", F.build_pdf([self.title_page(0, ["intake"])], syntax="object_stream"))
            with self.writer() as kb:
                intake = AssetService(kb, pdf_validator="2", parser_site=self.site).register_asset(
                    fixture_root=self.fixtures, relative_path="i.pdf", document_version_id=self.dvs[0], operator="op.lib")
            self.assertEqual((intake["outcome"], intake["reasons"]), ("REJECTED", ["PARSER_SANDBOX_UNAVAILABLE"]))
        child = (Path(sandbox.CHILD)).read_text(encoding="utf-8")
        self.assertIn('"NOT_ENFORCED_PRIVILEGED_ACCOUNT" if os.geteuid() == 0 else "OS_RLIMIT_NPROC_0"', child)


class DurableExportAudit(S06Case):
    N_PAPERS = 2

    def setUp(self):
        super().setUp()
        self.a = self.register("d.pdf", F.build_pdf([self.title_page(0, ["Sentinel EXPORTSECRET line."])], syntax="object_stream"))
        self.art = self.parse(self.a["asset_sha256"], self.a["link_id"])["artifact_id"]
        self.out = Path(self.tmp, "exports")
        self.out.mkdir()
        (self.out / "operator_notes.txt").write_text("caller-owned file, must survive", encoding="utf-8")

    def export(self, faults=None, kb_patch=None, **kw):
        with self.writer() as kb:
            svc = ParseService(kb, parser_site=self.site, allow_language_level_network_guard=True, faults=faults)
            if kb_patch:
                kb_patch(kb)
            return svc.export(artifact_id=self.art, out_dir=str(self.out), operator="op.x", include_text=True, reason="local reading", **kw)

    def leaked(self):
        return [p for p in self.out.rglob("*") if p.is_file() and b"EXPORTSECRET" in p.read_bytes()]

    def events(self):
        with self.reader() as kb:
            return [(r["state"], json.loads(r["reason_codes_json"]), r["authorization_event_id"]) for r in kb.all("SELECT * FROM parse_export_event ORDER BY rowid")]

    def open_count(self):
        with self.reader() as kb:
            return len(ParseService(kb).export_status()["recovery_required"])

    def assert_operator_file_intact(self):
        self.assertEqual((self.out / "operator_notes.txt").read_text(encoding="utf-8"), "caller-owned file, must survive")

    @staticmethod
    def failing_insert(kb, states, exc=sqlite3.OperationalError("database is locked (injected)")):
        real = kb.insert

        def insert(table, row):
            if table == "parse_export_event" and row.get("state") in states:
                raise exc
            return real(table, row)
        kb.insert = insert

    def test_happy_path_authorization_precedes_completion(self):
        """IDs: S06-F02-STATE-MACHINE, S06-F02-AUTH-BEFORE-VISIBLE, S06-F02-COMPLETION-RECORDED. A text export records AUTHORIZED, then exactly one COMPLETED that references it; the result says COMPLETED; nothing is open; a rerun finds the identical folder and its COMPLETED audit (EXPORT_ALREADY_PRESENT_AUDITED) without writing."""
        out = self.export()
        self.assertEqual(out["audit_state"], "COMPLETED")
        ev = self.events()
        self.assertEqual([e[0] for e in ev], ["AUTHORIZED", "COMPLETED"])
        self.assertIsNotNone(ev[1][2])
        self.assertEqual(self.open_count(), 0)
        again = self.export()
        self.assertEqual((again["written"], again["code"]), (False, "EXPORT_ALREADY_PRESENT_AUDITED"))
        self.assert_operator_file_intact()

    def test_audit_failure_before_any_write_leaves_nothing(self):
        """IDs: S06-F02-AUDIT-INSERT-FAILURE, S06-F02-NO-UNAUDITED-PLAINTEXT. An injected SQLite failure while committing the AUTHORIZED event raises and leaves no plaintext (not even a staging folder) and no event."""
        with self.assertRaises(sqlite3.OperationalError):
            self.export(kb_patch=lambda kb: self.failing_insert(kb, {"AUTHORIZED"}))
        self.assertEqual((self.leaked(), self.events()), ([], []))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["operator_notes.txt"])

    def test_write_fsync_rename_failures_roll_back_and_record_failed(self):
        """IDs: S06-F02-WRITE-FAILURE, S06-F02-FSYNC-FAILURE, S06-F02-RENAME-FAILURE, S06-F02-FAILED-RECORDED, S06-F02-OPERATOR-FILES-SAFE. A failure during a staged file write, in fsync, before the rename and in the rename itself removes the staging folder, makes nothing visible, appends FAILED for the authorization and leaves caller-owned files untouched."""
        def boom(**_):
            raise OSError("injected I/O error")
        cases = [{"faults": {"export_during_write": boom}}, {"faults": {"export_before_rename": boom}}]
        for kw in cases:
            with self.subTest(kw):
                with self.assertRaises(KBError) as ctx:
                    self.export(**kw)
                self.assertEqual(ctx.exception.code, "EXPORT_FAILED_ROLLED_BACK")
        with mock.patch.object(parse_service.os, "fsync", side_effect=OSError("injected fsync failure")):
            with self.assertRaises(KBError):
                self.export()
        with mock.patch.object(parse_service.os, "replace", side_effect=OSError("injected rename failure")):
            with self.assertRaises(KBError):
                self.export()
        self.assertEqual(self.leaked(), [])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["operator_notes.txt"])
        states = [e[0] for e in self.events()]
        self.assertEqual(states, ["AUTHORIZED", "FAILED"] * 4)
        self.assertEqual(self.open_count(), 0)

    def test_completion_failure_withdraws_visible_plaintext(self):
        """IDs: S06-F02-COMPLETION-FAILURE, S06-F02-WITHDRAW-VISIBLE, S06-F02-NO-FALSE-SUCCESS. If the COMPLETED event cannot be committed after the rename, the folder this call made visible is withdrawn, FAILED is recorded (withdrawn_visible_folder) and the call raises; no success is claimed."""
        with self.assertRaises(KBError) as ctx:
            self.export(kb_patch=lambda kb: self.failing_insert(kb, {"COMPLETED"}))
        self.assertEqual(ctx.exception.code, "EXPORT_FAILED_ROLLED_BACK")
        self.assertEqual(self.leaked(), [])
        with self.reader() as kb:
            failed = kb.one("SELECT * FROM parse_export_event WHERE state='FAILED'")
        self.assertTrue(json.loads(failed["detail_json"])["withdrawn_visible_folder"])
        self.assert_operator_file_intact()

    def test_database_down_leaves_recovery_required_then_reconciles(self):
        """IDs: S06-F02-DB-DOWN-RECOVERY, S06-F02-RECOVERY-REQUIRED, S06-F02-RETRY. With the database failing for every terminal event, the visible folder is still withdrawn and the authorization stays open (export_status RECOVERY_REQUIRED, doctor warning); the next export closes it as FAILED(INTERRUPTED_BEFORE_COMPLETION) and completes normally."""
        with self.assertRaises(KBError):
            self.export(kb_patch=lambda kb: self.failing_insert(kb, {"COMPLETED", "FAILED"}))
        self.assertEqual(self.leaked(), [])
        self.assertEqual(self.open_count(), 1)
        self.assertIn("PARSE_EXPORT_RECOVERY_REQUIRED:1", doctor(self.root, full=True)["warnings"])
        out = self.export()
        self.assertEqual(out["audit_state"], "COMPLETED")
        states = [(e[0], e[1]) for e in self.events()]
        self.assertEqual(states, [("AUTHORIZED", []), ("AUTHORIZED", []), ("FAILED", ["INTERRUPTED_BEFORE_COMPLETION"]), ("COMPLETED", [])])
        self.assertEqual(self.open_count(), 0)

    def test_crash_after_rename_and_crash_before_rename_are_reconciled(self):
        """IDs: S06-F02-CRASH-AFTER-RENAME, S06-F02-CRASH-BEFORE-RENAME, S06-F02-RECONCILE. A crash between rename and completion (AUTHORIZED + identical visible folder) is reconciled by the next export into COMPLETED (recovered) without rewriting the folder; a crash before rename (AUTHORIZED + leftover rkb staging folder) is reconciled by removing only the rkb-named staging folder, closing the old authorization FAILED and completing a fresh export."""
        keep = Path(self.tmp, "keep")

        def snapshot(folder, **_):
            shutil.copytree(folder, keep / folder.name)
        with self.assertRaises(KBError):
            self.export(faults={"export_before_complete": snapshot}, kb_patch=lambda kb: self.failing_insert(kb, {"COMPLETED", "FAILED"}))
        shutil.copytree(keep / next(keep.iterdir()).name, self.out / next(keep.iterdir()).name)      # the state a crash would have left
        out = self.export()
        self.assertEqual((out["written"], out["code"]), (False, "EXPORT_RECOVERED_AND_COMPLETED"))
        self.assertEqual([e[0] for e in self.events()], ["AUTHORIZED", "COMPLETED"])
        shutil.rmtree(self.out / out["folder_name"])
        shutil.rmtree(keep)
        keep.mkdir()

        def snap_staging(staging, **_):
            shutil.copytree(staging, keep / staging.name)
        before = len(self.events())
        with self.assertRaises(KBError):
            self.export(faults={"export_after_stage": snap_staging}, kb_patch=lambda kb: self.failing_insert(kb, {"COMPLETED", "FAILED"}))
        leftover = next(keep.iterdir()).name
        shutil.copytree(keep / leftover, self.out / leftover)
        out = self.export()
        self.assertEqual(out["audit_state"], "COMPLETED")
        self.assertFalse((self.out / leftover).exists())
        self.assertEqual([e[0] for e in self.events()][before:], ["AUTHORIZED", "AUTHORIZED", "FAILED", "COMPLETED"])
        self.assert_operator_file_intact()

    def test_existing_unaudited_folder_is_never_treated_as_audited(self):
        """IDs: S06-F02-UNAUDITED-FOLDER, S06-F02-NO-DELETE-CALLER-DATA. An identical export folder with no audit record for this target is refused EXPORT_FOLDER_UNAUDITED and left untouched; a different folder of the same name is EXPORT_FOLDER_EXISTS_DIFFERENT and left untouched."""
        done = self.export()
        other = Path(self.tmp, "elsewhere")
        shutil.copytree(self.out, other)
        with self.writer() as kb:
            svc = ParseService(kb, parser_site=self.site)
            with self.assertRaises(PolicyRefusal) as ctx:
                svc.export(artifact_id=self.art, out_dir=str(other), operator="op.x", include_text=True, reason="r")
        self.assertEqual(ctx.exception.code, "EXPORT_FOLDER_UNAUDITED")
        self.assertTrue((other / done["folder_name"] / "page-0001.txt").is_file())
        (other / done["folder_name"] / "page-0001.txt").write_text("changed by the operator", encoding="utf-8")
        with self.writer() as kb:
            with self.assertRaises(ValidationError) as ctx:
                ParseService(kb).export(artifact_id=self.art, out_dir=str(other), operator="op.x", include_text=True, reason="r")
        self.assertEqual(ctx.exception.code, "EXPORT_FOLDER_EXISTS_DIFFERENT")
        self.assertEqual((other / done["folder_name"] / "page-0001.txt").read_text(encoding="utf-8"), "changed by the operator")

    def test_rights_downgrade_and_identity_change_mid_export(self):
        """IDs: S06-F02-RIGHTS-MID-EXPORT, S06-F02-IDENTITY-GATE, S06-F02-METADATA-STILL-TEXT-FREE, S06-F02-REASON-REQUIRED. A DENY assessment recorded after authorization but before the rename refuses the export (TEXT_EXPORT_REFUSED), removes the staging folder and records FAILED; a link rejected as MISMATCH refuses text export (IDENTITY_MISMATCH, REFUSED event); a reason is still required; the metadata-only export stays text-free."""
        holder = []

        def downgrade(staging, **_):          # another rights decision lands between authorization and rename
            with holder[0].transaction():
                insert_assessment(holder[0], self.a["asset_sha256"], self.a["document_version_id"], "DENY")
        with self.assertRaises(PolicyRefusal) as ctx:
            self.export(faults={"export_after_stage": downgrade}, kb_patch=holder.append)
        self.assertEqual(ctx.exception.code, "TEXT_EXPORT_REFUSED")
        self.assertEqual(self.leaked(), [])
        self.assertEqual([e[0] for e in self.events()], ["AUTHORIZED", "FAILED"])
        b = self.register("b.pdf", F.build_pdf([self.title_page(1, ["Second EXPORTSECRET doc."])], syntax="xref_stream"), paper=1)
        art_b = self.parse(b["asset_sha256"], b["link_id"])["artifact_id"]
        with self.writer() as kb:
            AssetService(kb).review_link(link_id=b["link_id"], decision="REJECT", operator="op.lib", reason="wrong paper")
        with self.writer() as kb:
            svc = ParseService(kb)
            with self.assertRaises(PolicyRefusal) as ctx:
                svc.export(artifact_id=art_b, out_dir=str(self.out), operator="op.x", include_text=True, reason="r")
            self.assertIn("IDENTITY_MISMATCH", str(ctx.exception))
            with self.assertRaises(ValidationError):
                svc.export(artifact_id=art_b, out_dir=str(self.out), operator="op.x", include_text=True)
            meta = svc.export(artifact_id=art_b, out_dir=str(self.out), operator="op.x")
        self.assertFalse(any(b"EXPORTSECRET" in p.read_bytes() for p in (self.out / meta["folder_name"]).iterdir()))
        self.assertEqual(self.leaked(), [])

    def test_sql_state_machine_constraints(self):
        """IDs: S06-F02-SQL-TERMINAL-MATCH, S06-F02-SQL-ONE-TERMINAL, S06-F02-APPEND-ONLY. A terminal event must reference an AUTHORIZED event of the same artifact, manifest, target and folder; a second terminal event for one authorization is refused; events cannot be updated or deleted."""
        self.export()
        with self.writer() as kb:
            auth = kb.one("SELECT * FROM parse_export_event WHERE state='AUTHORIZED'")
            base = {k: auth[k] for k in auth if k not in ("event_id", "semantic_sha256")}
            for i, extra in enumerate(({"state": "FAILED", "reason_codes_json": '["X"]', "authorization_event_id": auth["event_id"]},
                                       {"state": "COMPLETED", "authorization_event_id": auth["event_id"], "export_manifest_sha256": "f" * 64})):
                with self.assertRaises(ValidationError):
                    with kb.transaction():
                        kb.insert("parse_export_event", dict(base, event_id=f"pze_x{i}", semantic_sha256=str(i) * 64, **extra))
            for sql in ("UPDATE parse_export_event SET state='FAILED'", "DELETE FROM parse_export_event"):
                with self.assertRaises(KBError):
                    with kb.transaction():
                        kb.execute(sql)


if __name__ == "__main__":
    unittest.main()
