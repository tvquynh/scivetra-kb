"""S08 v02 corrective tests: verbatim typed text under rights gates A3 / A5 (S08-F01) and the durable export audit lifecycle
(S08-F02). Synthetic PDFs only, offline, no model."""
import hashlib
import json
import os
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest import mock as umock

from tests.s08_helpers import MALWARE_DOC, S08Case
from scivetra_kb import cli
from scivetra_kb.core.doctor import doctor
from scivetra_kb.core.errors import KBError
from scivetra_kb.evidence import service as evidence_service
from scivetra_kb.evidence.service import EvidenceService

PHRASE = "packed samples were excluded"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def by_class(drafts, cls):
    return [d for d in drafts if d["evidence_class"] == cls]


class RightsAwareText(S08Case):
    def supersede_rights(self, asset_sha256, **changes):
        with self.writer() as kb:
            old = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? ORDER BY rowid DESC LIMIT 1", (asset_sha256,))
            row = dict(old, assessment_id=kb.ids.new("rga"), supersedes_assessment_id=old["assessment_id"], assessed_by="OPERATOR:owner",
                       recorded_by="OPERATOR:owner", semantic_sha256=kb.ids.new("x").ljust(64, "0")[:64], **changes)
            with kb.transaction():
                kb.insert("rights_assessment", row)

    def ev_rows_json(self):
        with self.reader() as kb:
            return json.dumps({t: kb.all(f"SELECT * FROM {t}") for t in ("ev_draft", "ev_extraction_run", "ev_verification", "ev_reveal",
                                                                         "ev_review", "ev_adjudication", "ev_export_event")})

    def cli(self, *argv):
        return cli.run(list(argv), clock=self.clock, ids=self.ids)

    def test_a3_denied_text_is_never_stored_and_cannot_be_accepted(self):
        """IDs: S08-V02-R2-A3-TEXT-NOT-STORED, S08-V02-R2-HASH-ONLY-VERIFIED, S08-V02-R5-NO-ACCEPT-UNSEEN, S08-V02-REVEAL-TRIGGER. When gate A3
        is denied at extraction, every verbatim text field value is stored nowhere in the KB tables (only SHA-256, length and position
        in the quote); numeric, unit and enum values stay; structural verification re-checks the withheld text by hash against the S06
        artifact; reveal, show, audit and the CLI withhold it with explicit codes; ACCEPT and adjudication ACCEPT are refused; a SHOWN
        reveal of such a draft is refused by trigger; doctor stays clean."""
        def deny_quotes(pdf):
            self.supersede_rights(pdf["asset_sha256"], store_locally="DENY", local_process="ALLOW")
        run, items, scope, _ = self.mocked([[MALWARE_DOC]], before_attach=deny_quotes)
        drafts = self.drafts(scope)
        lim = by_class(drafts, "LIMITATION")[0]
        self.assertEqual((lim["quote_storage"], lim["text_storage"], lim["typed_fields_json"], lim["quote_text_json"]),
                         ("HASH_ONLY_GATE_A3_DENIED", "WITHHELD_GATE_A3_DENIED", "{}", None))
        withheld = json.loads(lim["withheld_text_json"])["limitation"]
        self.assertEqual((withheld["anchor"], withheld["offset"], withheld["chars"]), (0, 0, len("packed samples were excluded from the evaluation")))
        self.assertEqual(withheld["sha256"], sha("packed samples were excluded from the evaluation"))
        num = [d for d in by_class(drafts, "NUMERICAL_RESULT") if "93.1" in d["typed_fields_json"]][0]
        fields = json.loads(num["typed_fields_json"])
        self.assertEqual((fields["value"], fields["unit"], fields["metric"]), ("93.1", "%", "TPR"))
        self.assertNotIn("dataset", fields)
        self.assertIn("dataset", json.loads(num["withheld_text_json"]))
        self.assertNotIn(PHRASE, self.ev_rows_json())
        self.assertNotIn("EMBER", self.ev_rows_json())
        ver = self.ecall("verify", draft_id=lim["draft_id"], operator="op.v")
        self.assertEqual(ver["state"], "STRUCTURALLY_VERIFIED")
        with self.reader() as kb:
            checks = json.loads(kb.one("SELECT checks_json FROM ev_verification WHERE verification_id=?", (ver["verification_id"],))["checks_json"])
        self.assertIn({"field": "limitation", "check": "WITHHELD_TEXT_HASH_IN_QUOTE", "result": "PASS"}, checks)
        self.roster("rev.one", "rev.two", "rev.adj")
        shown = self.ecall("reveal", draft_id=lim["draft_id"], reviewer="rev.one")
        self.assertEqual((shown["quote_disclosure"], shown["text_disclosure"], shown["quotes"], shown["typed_fields"]), ("WITHHELD", "WITHHELD", [], {}))
        self.assertEqual(shown["withheld_text_fields"], ["limitation"])
        self.assertIn("TEXT_NOT_STORED_GATE_A3_DENIED", shown["withheld_reasons"])
        self.assertIn("QUOTE_NOT_STORED_GATE_A3_DENIED", shown["withheld_reasons"])
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=lim["draft_id"], reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "EVIDENCE_NOT_SEEN")
        for blob in (self.eread("show", draft_id=lim["draft_id"]), self.eread("audit", draft_id=lim["draft_id"]),
                     self.cli("evidence", "show", "--data-root", self.root, "--draft-id", lim["draft_id"])[1],
                     self.cli("evidence", "audit", "--data-root", self.root, "--draft-id", lim["draft_id"])[1]):
            self.assertNotIn(PHRASE, json.dumps(blob))
        self.assertEqual(self.eread("show", draft_id=lim["draft_id"])["text_storage"], "WITHHELD_GATE_A3_DENIED")
        with self.writer() as kb, self.assertRaises(KBError):
            rid = kb.one("SELECT reviewer_id FROM ai_reviewer WHERE label='rev.two'")["reviewer_id"]
            kb.insert("ev_reveal", dict(reveal_id=kb.ids.new("evrl"), draft_id=lim["draft_id"], reviewer_id=rid, verification_id=ver["verification_id"],
                                        quote_disclosure="SHOWN", withheld_reasons_json="[]", created_at_utc=kb.now()))
        # REJECT / CORRECT stay possible; a disagreement cannot be adjudicated to ACCEPT unseen.
        self.ecall("reveal", draft_id=lim["draft_id"], reviewer="rev.two")
        self.ecall("review", draft_id=lim["draft_id"], reviewer="rev.two", decision="REJECT", reason_codes=["SOURCE_NOT_VISIBLE"], rationale="not visible")
        self.assertEqual(self.eread("state", draft_id=lim["draft_id"])["counts_as_accepted_evidence"], False)
        self.assertEqual(doctor(self.root, full=True)["problems"], [])

    def test_text_not_literally_locatable_is_not_assessable(self):
        """IDs: S08-V02-R2-NOT-ASSESSABLE, S08-V02-R4-NO-FABRICATION. An imported text value that matches the quote only after case /
        whitespace normalisation has no exact position; under gate A3 denial it cannot be re-checked by hash, so verification is
        NOT_ASSESSABLE (never STRUCTURALLY_VERIFIED) and the draft cannot be accepted."""
        def deny_quotes(pdf):
            self.supersede_rights(pdf["asset_sha256"], store_locally="DENY", local_process="ALLOW")
        run, items = self.ev_world([[MALWARE_DOC]], before_attach=deny_quotes)
        scope = self.ev_scope(run, items)
        self.ecall("build_packs", scope_id=scope, operator="op.owner")
        with self.reader() as kb:
            prow = kb.one("SELECT * FROM ev_input_pack WHERE scope_id=?", (scope,))
            pack = json.loads(kb.cas.read(prow["pack_sha256"]))
        seg = next(s for s in pack["segments"] if s["text"].startswith("Limitation"))
        anchor = {"page": seg["page"], "char_start": seg["start"], "char_end": seg["end"], "quote": seg["text"], "quote_sha256": sha(seg["text"])}
        record = {"evidence_class": "LIMITATION", "value_status": "REPORTED", "source_kind": "FULLTEXT_TEXT", "confidence": "MEDIUM", "note": "",
                  "fields": {"limitation": "Packed  samples were excluded"}, "anchors": [anchor]}
        doc = {"schema": "rkb-evidence-response/1", "input_pack_sha256": prow["pack_sha256"], "paper_id": pack["paper_id"],
               "document_version_id": pack["document_version_id"], "artifact_sha256": pack["artifact"]["artifact_sha256"],
               "profile_sha256": pack["profile"]["profile_sha256"], "records": [record], "notes": ""}
        out = self.ecall("import_extraction", pack_id=prow["pack_id"], response=json.dumps(doc).encode(), operator="op.importer",
                         declared_model={"provider": "local", "model_id": "operator-model"})
        draft = out["draft_ids"][0]
        with self.reader() as kb:
            row = kb.one("SELECT * FROM ev_draft WHERE draft_id=?", (draft,))
        self.assertEqual((row["text_storage"], json.loads(row["withheld_text_json"])["limitation"]["anchor"]), ("WITHHELD_GATE_A3_DENIED", None))
        ver = self.ecall("verify", draft_id=draft, operator="op.v")
        self.assertEqual((ver["state"], ver["failures"]), ("NOT_ASSESSABLE", ["TEXT_NOT_ASSESSABLE_GATE_A3_DENIED:limitation"]))
        self.assertEqual(self.eread("state", draft_id=draft)["current_state"], "NOT_ASSESSABLE")
        self.roster("rev.one")
        self.ecall("reveal", draft_id=draft, reviewer="rev.one")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=draft, reviewer="rev.one", decision="ACCEPT")
        self.assertEqual(ctx.exception.code, "DRAFT_NOT_VERIFIED")

    def test_a5_revoked_withholds_text_everywhere_and_allowed_reveal_works(self):
        """IDs: S08-V02-R3-A5-CURRENT, S08-V02-R3-SHOW-AUDIT-CLI, S08-V02-R4-ALLOWED-REVEAL, S08-V02-R4-NUMERIC-ABSENCE, S08-V02-R5-ADJUDICATION.
        With A3 / A5 allowed, the audited reveal shows the quote and the verbatim text fields, numeric / enum / unit values and absence
        drafts work as before and the draft can be accepted; show and audit never carry text values. After the store-locally permission
        is revoked (A5 denied) the reveal withholds quote and text with GATE_A5_DENIED, show / audit / CLI (raw JSON) carry no text, and
        ACCEPT and adjudication ACCEPT are refused."""
        run, items, scope, _ = self.mocked([[MALWARE_DOC]])
        drafts = self.drafts(scope)
        lim = by_class(drafts, "LIMITATION")[0]["draft_id"]
        num = [d for d in by_class(drafts, "NUMERICAL_RESULT") if "93.1" in d["typed_fields_json"]][0]["draft_id"]
        absent = [d for d in drafts if d["value_status"] == "NOT_REPORTED"][0]["draft_id"]
        self.roster("rev.one", "rev.two", "rev.adj")
        for d in (lim, num, absent):
            self.ecall("verify", draft_id=d, operator="op.v")
        shown = self.ecall("reveal", draft_id=lim, reviewer="rev.one")
        self.assertEqual((shown["quote_disclosure"], shown["text_disclosure"], shown["typed_fields"]["limitation"]),
                         ("SHOWN", "SHOWN", "packed samples were excluded from the evaluation"))
        nshown = self.ecall("reveal", draft_id=num, reviewer="rev.one")
        self.assertEqual((nshown["typed_fields"]["value"], nshown["typed_fields"]["dataset"]), ("93.1", "EMBER"))
        meta = self.eread("show", draft_id=num)
        self.assertEqual((meta["typed_fields"]["value"], meta["typed_fields"]["unit"], meta["text"]), ("93.1", "%", "WITHHELD_USE_REVEAL"))
        self.assertIn("dataset", meta["withheld_text_fields"])
        self.assertNotIn("EMBER", json.dumps(meta))
        self.assertNotIn(PHRASE, json.dumps(self.eread("show", draft_id=lim)))
        self.assertEqual(self.ecall("reveal", draft_id=absent, reviewer="rev.one")["quote_disclosure"], "NO_QUOTE")
        for r in ("rev.one", "rev.two"):
            self.ecall("reveal", draft_id=absent, reviewer=r)
            self.ecall("review", draft_id=absent, reviewer=r, decision="ACCEPT")
        self.assertTrue(self.eread("state", draft_id=absent)["counts_as_accepted_evidence"])
        # rev.one accepts, rev.two rejects the limitation: a conflict to adjudicate
        self.ecall("review", draft_id=lim, reviewer="rev.one", decision="ACCEPT")
        self.ecall("reveal", draft_id=lim, reviewer="rev.two")
        self.ecall("review", draft_id=lim, reviewer="rev.two", decision="REJECT", reason_codes=["WRONG_SCOPE"], rationale="disagree")
        self.supersede_rights(items[0]["asset_sha256"], store_locally="DENY")
        after = self.ecall("reveal", draft_id=lim, reviewer="rev.adj")
        self.assertEqual((after["quote_disclosure"], after["quotes"], after["typed_fields"], after["withheld_text_fields"]), ("WITHHELD", [], {}, ["limitation"]))
        self.assertIn("GATE_A5_DENIED:STORE_LOCALLY_DENY", after["withheld_reasons"])
        with self.assertRaises(KBError) as ctx:
            self.ecall("adjudicate", draft_id=lim, adjudicator="rev.adj", outcome="ACCEPT", rationale="unseen")
        self.assertEqual(ctx.exception.code, "ADJUDICATION_ACCEPT_REFUSED")
        self.ecall("reveal", draft_id=num, reviewer="rev.two")
        with self.assertRaises(KBError) as ctx:
            self.ecall("review", draft_id=num, reviewer="rev.two", decision="ACCEPT")
        self.assertIn(ctx.exception.code, ("DRAFT_STALE_OR_BLOCKED", "EVIDENCE_NOT_SEEN"))
        for blob in (self.eread("show", draft_id=lim), self.eread("audit", draft_id=lim)):
            self.assertNotIn(PHRASE, json.dumps(blob))
        for action in ("show", "audit"):
            code, out = self.cli("evidence", action, "--data-root", self.root, "--draft-id", lim)
            self.assertEqual(code, 0)
            self.assertNotIn(PHRASE, json.dumps(out))
            self.assertNotIn("limitation\": \"", json.dumps(out))
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT quote_disclosure FROM ev_reveal ORDER BY rowid DESC LIMIT 1")["quote_disclosure"], "WITHHELD")


class ExportAudit(S08Case):
    def setUp(self):
        super().setUp()
        self.run_, self.items, self.scope, _ = self.mocked([[MALWARE_DOC]])
        self.out = os.path.join(self.tmp, "exports")

    def export(self, **kw):
        with self.writer() as kb:
            return EvidenceService(kb).export(scope_id=self.scope, out_dir=kw.pop("out_dir", self.out), operator="op.owner", **kw)

    def events(self):
        with self.reader() as kb:
            return kb.all("SELECT phase, outcome_code, reservation_id, export_id, report_sha256 FROM ev_export_event ORDER BY rowid")

    def visible(self, out=None):
        root = Path(out or self.out)
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def history(self):
        return [(e["outcome"], e["outcome_code"]) for e in self.eread("export_history", scope_id=self.scope)["exports"]]

    def failing_insert(self, kb, predicate, exc):
        original = kb.insert

        def insert(table, row):
            if table == "ev_export_event" and predicate(row):
                raise exc
            return original(table, row)
        kb.insert = insert

    def test_reservation_failure_publishes_nothing(self):
        """IDs: S08-V02-R6-DB-FAIL-NO-REPORT. The RESERVED audit event is committed before anything is written; if the database
        refuses it (locked), export fails closed: no report, no staging folder, no event."""
        with self.writer() as kb:
            self.failing_insert(kb, lambda r: True, sqlite3.OperationalError("database is locked (simulated)"))
            with self.assertRaises(KBError) as ctx:
                EvidenceService(kb).export(scope_id=self.scope, out_dir=self.out, operator="op.owner")
        self.assertEqual(ctx.exception.code, "EXPORT_AUDIT_FAILED")
        self.assertEqual((self.visible(), self.events()), ([], []))

    def test_write_and_rename_failures_are_abandoned_and_retry_publishes(self):
        """IDs: S08-V02-R6-WRITE-FAIL, S08-V02-R6-RENAME-FAIL, S08-V02-R6-RETRY. A failed file write or atomic rename leaves no report and
        no partial folder; the reservation is closed as ABANDONED (PUBLICATION_FAILED). A later export publishes the identical,
        deterministic bytes with its own RESERVED -> PUBLISHED pair."""
        with umock.patch.object(evidence_service.os, "fsync", side_effect=OSError("disk full (simulated)")):
            with self.assertRaises(KBError) as ctx:
                self.export()
        self.assertEqual(ctx.exception.code, "EXPORT_WRITE_FAILED")
        self.assertEqual(self.visible(), [])
        with umock.patch.object(evidence_service.os, "replace", side_effect=PermissionError("rename refused (simulated)")):
            with self.assertRaises(KBError) as ctx:
                self.export()
        self.assertEqual(ctx.exception.code, "EXPORT_WRITE_FAILED")
        self.assertEqual(self.visible(), [])
        self.assertEqual(self.history(), [("ABANDONED", "PUBLICATION_FAILED")] * 2)
        out = self.export()
        self.assertEqual((out["written"], out["code"]), (True, "EXPORT_PUBLISHED"))
        self.assertEqual(self.visible(), [out["folder_name"]])
        data = (Path(self.out) / out["folder_name"] / "evidence_report.json").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), out["report_sha256"])
        self.assertEqual(self.history()[-1], ("PUBLISHED", None))
        again = self.export()
        self.assertEqual((again["written"], again["code"], again["report_sha256"], again["export_id"]),
                         (False, "EXPORT_ALREADY_PUBLISHED", out["report_sha256"], out["export_id"]))
        self.assertEqual([e["phase"] for e in self.events()], ["RESERVED", "ABANDONED", "RESERVED", "ABANDONED", "RESERVED", "PUBLISHED"])

    def test_completion_failure_withdraws_report(self):
        """IDs: S08-V02-R6-COMPLETION-FAIL. If the PUBLISHED event cannot be committed after the rename, the report is withdrawn
        (no visible unlogged completion) and the reservation is closed ABANDONED (AUDIT_COMPLETION_FAILED_WITHDRAWN)."""
        with self.writer() as kb:
            self.failing_insert(kb, lambda r: r["phase"] == "PUBLISHED", sqlite3.OperationalError("database is locked (simulated)"))
            with self.assertRaises(KBError) as ctx:
                EvidenceService(kb).export(scope_id=self.scope, out_dir=self.out, operator="op.owner")
        self.assertEqual(ctx.exception.code, "EXPORT_AUDIT_FAILED")
        self.assertEqual(self.visible(), [])
        self.assertEqual(self.history(), [("ABANDONED", "AUDIT_COMPLETION_FAILED_WITHDRAWN")])

    def test_crash_after_publication_is_unconfirmed_then_recovered(self):
        """IDs: S08-V02-R6-CRASH-RECOVERY, S08-V02-R6-UNAMBIGUOUS. A crash between the atomic rename and the PUBLISHED event leaves a
        report whose RESERVED event was committed before it became visible; the history reports it UNCONFIRMED (never as a completed
        export). The next export of the same bytes completes it as PUBLISHED (RECOVERED_AFTER_INTERRUPTION) without rewriting the
        file; a third call is a no-op. Identical bytes in a folder that no reservation records are refused."""
        with self.writer() as kb:
            self.failing_insert(kb, lambda r: r["phase"] == "PUBLISHED", KeyboardInterrupt())
            with self.assertRaises(KeyboardInterrupt):
                EvidenceService(kb).export(scope_id=self.scope, out_dir=self.out, operator="op.owner")
        folder = self.visible()
        self.assertEqual(len(folder), 1)
        self.assertEqual(self.history(), [("UNCONFIRMED", None)])
        report = Path(self.out) / folder[0] / "evidence_report.json"
        mtime = report.stat().st_mtime_ns
        rec = self.export()
        self.assertEqual((rec["written"], rec["code"], rec["folder_name"]), (False, "EXPORT_RECOVERED", folder[0]))
        self.assertEqual(self.history(), [("PUBLISHED", "RECOVERED_AFTER_INTERRUPTION")])
        self.assertEqual(report.stat().st_mtime_ns, mtime)
        self.assertEqual(self.export()["code"], "EXPORT_ALREADY_PUBLISHED")
        self.assertEqual(len(self.events()), 2)
        foreign = os.path.join(self.tmp, "copied")
        shutil.copytree(self.out, foreign)
        with self.assertRaises(KBError) as ctx:
            self.export(out_dir=foreign)
        self.assertEqual(ctx.exception.code, "EXPORT_FOLDER_UNAUDITED")
        code, hist = cli.run(["evidence", "exports", "--data-root", self.root, "--scope-id", self.scope], clock=self.clock, ids=self.ids)
        self.assertEqual((code, [e["outcome"] for e in hist["exports"]]), (0, ["PUBLISHED"]))

    def test_export_event_lifecycle_is_enforced_by_schema(self):
        """IDs: S08-V02-R6-SCHEMA-LIFECYCLE, S08-V02-R7-EXPORT-PRESERVED. An outcome event must close exactly one matching RESERVED
        event (trigger); events stay append-only; doctor reports no outcome without reservation; the report stays metadata-only and
        deterministic."""
        out = self.export()
        with self.reader() as kb:
            res = kb.one("SELECT * FROM ev_export_event WHERE phase='RESERVED'")
        with self.writer() as kb:
            for bad in (dict(res, export_id=kb.ids.new("eve"), phase="PUBLISHED", reservation_id=res["export_id"]),
                        dict(res, export_id=kb.ids.new("eve"), phase="ABANDONED", reservation_id="eve_missing", outcome_code="X_Y"),
                        dict(res, export_id=kb.ids.new("eve"), phase="PUBLISHED", reservation_id=res["export_id"], report_sha256="0" * 64)):
                with self.assertRaises(KBError):
                    with kb.transaction():
                        kb.insert("ev_export_event", bad)
            with self.assertRaises(KBError):
                with kb.transaction():
                    kb.execute("UPDATE ev_export_event SET phase='ABANDONED'")
        self.assertEqual(doctor(self.root, full=True)["problems"], [])
        data = (Path(self.out) / out["folder_name"] / "evidence_report.json").read_bytes()
        self.assertNotIn(b"packed samples", data)
        self.assertFalse(json.loads(data)["contains_quotes"])


if __name__ == "__main__":
    unittest.main()
