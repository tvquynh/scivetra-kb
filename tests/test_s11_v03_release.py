"""S11 v03 (architect finding S11-F05): the export release boundary. The final rights / source / currency check, the atomic rename
and the PUBLISHED event run inside one SQLite write transaction, so every KB writer is serialized against the release. Each test
shows the syn_export_event trail and the on-disk folder state. Synthetic PDFs only; no network or model."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from tests.s11_helpers import STUDY_A, STUDY_B, S11Case
from tests.test_s09_v02_regressions import cas_file, supersede_rights
from scivetra_kb.core.db import DB_RELATIVE, KB
from scivetra_kb.core.errors import KBError
from scivetra_kb.synthesis.service import SynthesisService

PHRASE = "packed samples were excluded from the evaluation"


class ReleaseBoundary(S11Case):
    def prepare(self):
        self.syn_world([[STUDY_A], [STUDY_B]])
        self.claim("EXCERPT", "THEMATIC", [self.draft(0, "LIMITATION")], statement="The authors state that " + PHRASE + ".", evidence_class="LIMITATION")
        self.claim("TPR", "REPORTED_VALUE", [self.draft(1, fpr_operating_point="1")], measurement=self.tpr("91.4"))
        self.audit()
        self.out = Path(self.tmp, "release")

    def export(self, hooks=None):
        """Export on one long-lived handle; ``hooks``: {fault point: callable} run at that point. Returns (result or error code,
        export events as (phase, outcome), entries left in the output folder)."""
        kb = KB(self.root, readonly=False, clock=self.clock, ids=self.ids)
        self.addCleanup(kb.close)
        svc = SynthesisService(kb)
        svc._fault = lambda name: (hooks or {}).get(name, lambda: None)()
        try:
            got = svc.export(scope_id=self.syn, out_dir=str(self.out), operator="op.exporter")
        except KBError as exc:
            got = exc.code
        with self.reader() as r:
            events = [(e["phase"], e["outcome_code"]) for e in r.all("SELECT phase, outcome_code FROM syn_export_event ORDER BY rowid")]
        left = sorted(os.listdir(self.out)) if self.out.exists() else []
        return got, events, left

    def test_rights_revoked_or_pdf_deleted_before_release_publish_nothing(self):
        """IDs: S11-V3-F05A-REVOKED-BEFORE-RELEASE, S11-V3-F05B-PDF-DELETED-BEFORE-RELEASE, S11-V3-AUDIT-TRAIL, S11-V3-NO-ORPHAN-FILES. Store_locally withdrawn from another connection, or the pinned PDF's CAS object deleted, after staging and before release: EXPORT_RELEASE_REFUSED; trail RESERVED -> ABANDONED RELEASE_REFUSED:AUDIT_NO_LONGER_REPRODUCES; no folder, no staging folder and no file with the excerpt is left."""
        self.prepare()

        def revoke():
            with self.writer() as other:
                supersede_rights(other, self.items[0]["asset_sha256"], store_locally="DENY")
        got, events, left = self.export({"write": revoke})
        self.assertEqual((got, events, left), ("EXPORT_RELEASE_REFUSED", [("RESERVED", None), ("ABANDONED", "RELEASE_REFUSED:AUDIT_NO_LONGER_REPRODUCES")], []))
        with self.writer() as other:
            supersede_rights(other, self.items[0]["asset_sha256"], store_locally="ALLOW")
        self.audit()

        def delete_pdf():
            with self.reader() as r:
                os.remove(cas_file(r, self.items[0]["asset_sha256"]))
        got, events, left = self.export({"write": delete_pdf})
        self.assertEqual((got, events[-2:], left), ("EXPORT_RELEASE_REFUSED", [("RESERVED", None), ("ABANDONED", "RELEASE_REFUSED:AUDIT_NO_LONGER_REPRODUCES")], []))
        self.assertFalse(any(p == "PUBLISHED" for p, _ in events))

    def test_positive_control_and_manifest(self):
        """IDs: S11-V3-POSITIVE-CONTROL. With rights and sources unchanged the export is PUBLISHED (RESERVED -> PUBLISHED), the allowed excerpt is visible, the MANIFEST hashes match every file and no staging folder is left."""
        self.prepare()
        got, events, left = self.export()
        self.assertEqual((events, left), ([("RESERVED", None), ("PUBLISHED", None)], [got["folder_name"]]))
        folder = self.out / got["folder_name"]
        manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
        import hashlib
        self.assertEqual({k: hashlib.sha256((folder / k).read_bytes()).hexdigest() for k in manifest["files"]}, manifest["files"])
        self.assertIn(PHRASE, (folder / "draft.md").read_text(encoding="utf-8"))

    def test_writers_are_serialized_against_the_release(self):
        """IDs: S11-V3-SERIALIZED-WAITING-WRITER, S11-V3-SERIALIZED-WRITE-DURING-RELEASE. A rights change committed by a writer that holds the lock when the release starts is waited for and then seen (refused); a writer that tries to change rights while the release holds the lock cannot commit before publication (database is locked), so what is published was current at release time."""
        self.prepare()
        acquired = threading.Event()

        def slow_revoke():
            with self.writer() as other:
                old = other.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (self.items[0]["asset_sha256"],))
                with other.transaction():                       # holds the write lock for 1 s, then commits the revocation
                    acquired.set()
                    time.sleep(1.0)
                    other.insert("rights_assessment", dict(old, assessment_id=other.ids.new("rga"), supersedes_assessment_id=old["assessment_id"],
                                                           semantic_sha256=other.ids.new("x").ljust(64, "0")[:64], store_locally="DENY"))
        threads = []

        def start_writer():
            t = threading.Thread(target=slow_revoke)
            t.start()
            threads.append(t)
            self.assertTrue(acquired.wait(5))
        got, events, left = self.export({"write": start_writer})
        threads[0].join(10)
        self.assertEqual((got, events[-1], left), ("EXPORT_RELEASE_REFUSED", ("ABANDONED", "RELEASE_REFUSED:AUDIT_NO_LONGER_REPRODUCES"), []))
        with self.writer() as other:
            supersede_rights(other, self.items[0]["asset_sha256"], store_locally="ALLOW")
        self.audit()
        attempts = []

        def write_during_release():
            con = sqlite3.connect(str(Path(self.root) / DB_RELATIVE), timeout=0.2)
            try:
                con.execute("BEGIN IMMEDIATE")
                attempts.append("ACQUIRED")
                con.execute("ROLLBACK")
            except sqlite3.OperationalError as exc:
                attempts.append(str(exc))
            finally:
                con.close()
        got, events, left = self.export({"release": write_during_release})
        self.assertEqual(attempts, ["database is locked"])
        self.assertEqual(events[-1], ("PUBLISHED", None))

    def test_lock_timeout_io_and_commit_failures_leave_nothing(self):
        """IDs: S11-V3-DB-LOCKED-AT-RELEASE, S11-V3-RELEASE-FAULT, S11-V3-COMMIT-FAILURE-WITHDRAWN, S11-V3-STAGED-FILE-TAMPERED, S11-V3-NEWER-AUDIT. Another connection holding the write lock past busy_timeout: EXPORT_RELEASE_FAILED, ABANDONED RELEASE_FAILED, nothing visible; a failure inside the release before the rename: same; a failure after the rename (PUBLISHED not committed): the folder is withdrawn, ABANDONED AUDIT_COMPLETION_FAILED_WITHDRAWN; a staged file altered before release and a newer audit run are refused."""
        self.prepare()
        con = sqlite3.connect(str(Path(self.root) / DB_RELATIVE), timeout=0.2, check_same_thread=False)

        def hold_lock():
            con.execute("BEGIN IMMEDIATE")
            threading.Timer(6.5, lambda: con.execute("ROLLBACK")).start()
        got, events, left = self.export({"write": hold_lock})
        time.sleep(1.0)
        con.close()
        self.assertEqual((got, left), ("EXPORT_RELEASE_FAILED", []))
        self.assertEqual(events[-1], ("ABANDONED", "RELEASE_FAILED"))

        def boom():
            raise OSError("injected")
        got, events, left = self.export({"release": boom})
        self.assertEqual((got, events[-1], left), ("EXPORT_RELEASE_FAILED", ("ABANDONED", "RELEASE_FAILED"), []))
        got, events, left = self.export({"complete": boom})
        self.assertEqual((got, events[-1], left), ("EXPORT_AUDIT_FAILED", ("ABANDONED", "AUDIT_COMPLETION_FAILED_WITHDRAWN"), []))

        def tamper():
            staging = [p for p in self.out.iterdir() if ".partial-" in p.name][0]
            (staging / "draft.md").write_text("tampered", encoding="utf-8")
        got, events, left = self.export({"write": tamper})
        self.assertEqual((got, events[-1], left), ("EXPORT_RELEASE_REFUSED", ("ABANDONED", "RELEASE_REFUSED:STAGED_FILE_CHANGED"), []))
        got, events, left = self.export({"write": self.audit})
        self.assertEqual((got, events[-1], left), ("EXPORT_RELEASE_REFUSED", ("ABANDONED", "RELEASE_REFUSED:NEWER_AUDIT_EXISTS"), []))
        self.assertFalse(any(p == "PUBLISHED" for p, _ in events))
