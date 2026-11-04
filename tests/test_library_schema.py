"""Migration 0002: additive only, every new table append-only, triggers enforce human-anchored identity changes."""
import json
import os
import re
import unittest
from pathlib import Path

from tests.pdf_fixtures import synthetic_pdf, title_page
from tests.s03_helpers import s03_item, write_export
from tests.test_assets import AssetCase, TITLE_A, TITLE_B
from tests.test_rights import CC_BY, OA_VOR
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.errors import ImmutableRecordError, KBError
from scivetra_kb.core.upgrade import upgrade_kb
from scivetra_kb.library.service import LibraryService

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "scivetra_kb" / "core" / "migrations"
S03_TABLES = ("workspace_registration", "workspace_availability_event", "import_event", "reprovane_release_import", "project_paper_reference",
              "paper_kind_event", "identity_conflict", "identity_resolution_event", "assertion_status_event", "link_candidate", "link_decision",
              "work_family", "work_family_member", "work_family_member_event", "document_version", "pdf_asset", "asset_validation_event",
              "paper_asset_link", "paper_asset_link_event", "rights_assessment", "rights_review_task", "kb_upgrade_event")


class MigrationShapeTests(unittest.TestCase):
    def test_0002_is_additive(self):
        """IDs: S03-SCHEMA-ADDITIVE, S03-MIGRATION-0001-EXACT. 0001 keeps its accepted bytes; 0002 only CREATEs new objects and never ALTERs/DROPs/re-creates a 0001 object."""
        import hashlib
        self.assertEqual(hashlib.sha256((MIGRATIONS / "0001_metadata_foundation.sql").read_bytes()).hexdigest(),
                         "2c07dd3461458695465693a53faaebeded0b7ed850dd275c4e4b93698b71f38b")
        text = re.sub(r"--[^\n]*", "", (MIGRATIONS / "0002_global_research_library.sql").read_text(encoding="utf-8"))
        old = set(re.findall(r"CREATE (?:TABLE|INDEX|TRIGGER|VIEW) (\w+)", (MIGRATIONS / "0001_metadata_foundation.sql").read_text(encoding="utf-8")))
        self.assertIsNone(re.search(r"\b(ALTER|DROP|REPLACE INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", text))
        created = re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX|TRIGGER|VIEW) (\w+)", text)
        self.assertEqual(set(created) & old, set())
        self.assertEqual(set(re.findall(r"CREATE TABLE (\w+)", text)), set(S03_TABLES))
        for table in S03_TABLES:
            self.assertIn(f"CREATE TRIGGER {table}_no_update", text)
            self.assertIn(f"CREATE TRIGGER {table}_no_delete", text)


class ImmutabilityTests(AssetCase):
    def populate(self):
        """Drive real code paths until every 0002 table holds a row."""
        with self.writer() as kb:
            lib = LibraryService(kb)
            for rid, items in (("crl_p1", [s03_item("cri_p", dois=[("10.99999/p.pre", "10.99999/p.pre")], openalex=["https://openalex.org/W9"],
                                                    work_type="preprint")]),
                               ("crl_p2", [s03_item("cri_j", dois=[("10.99999/p.jnl", "10.99999/p.jnl")], openalex=["https://openalex.org/W9"]),
                                           s03_item("cri_m", dois=[("10.99999/p.m1", "10.99999/p.m1"), ("10.99999/p.m2", "10.99999/p.m2")])])):
                lib.import_release(write_export(os.path.join(self.tmp, rid), items, release_id=rid), workspace_label="ws-p", operator="op.a")
            candidate = kb.one("SELECT link_candidate_id FROM link_candidate")["link_candidate_id"]
            lib.decide_link(link_candidate_id=candidate, decision="DIFFERENT_MANIFESTATION_SAME_WORK", operator="op.b", reason="versions")
            conflict = kb.one("SELECT * FROM identity_conflict WHERE kind='MULTI_DOI_ITEM'")
            a1, a2 = json.loads(conflict["assertion_ids_json"])
            lib.resolve_conflict(conflict_id=conflict["conflict_id"], action="DOI_SELECTED_FOR_ITEM", operator="op.b", reason="journal",
                                 evidence_refs=[a1], activate=[a1], withdraw=[a2])
            lib.set_manifestation_kind(paper_id=self.paper_a, kind="JOURNAL_ARTICLE", operator="op.b", reason="claim", claim_refs=["synthetic:c"])
            lib.set_workspace_availability(workspace_label="ws-p", availability="SOURCE_UNAVAILABLE", operator="op.b", reason="deleted")
        data = synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99999/asset.a")])
        Path(self.fixtures, "a.pdf").write_bytes(data)
        Path(self.fixtures, "b.pdf").write_bytes(data)
        first = self.register("a.pdf")
        self.register("b.pdf", dv=self.dv_b)
        with self.writer() as kb:
            AssetService(kb).assess_rights(asset_sha256=first["asset_sha256"], document_version_id=self.dv_a, operator="op.r",
                                           evidence={"license_evidence": [CC_BY], "availability_evidence": [OA_VOR]})
        backups = os.path.join(self.tmp, "b")
        kb_v1 = os.path.join(self.tmp, "v1")
        from tests.s03_helpers import make_schema1_kb
        make_schema1_kb(kb_v1, clock=self.clock, ids=self.ids)
        upgrade_kb(kb_v1, backups, clock=self.clock, ids=self.ids)
        return kb_v1

    def test_every_0002_table_rejects_update_and_delete(self):
        """IDs: CORE-IMMUTABLE-03, S03-APPEND-ONLY. With every 0002 table populated, UPDATE and DELETE are rejected on each (kb_upgrade_event checked on an upgraded KB)."""
        upgraded = self.populate()
        from scivetra_kb.core.db import open_writer
        for root in (self.root, upgraded):
            with open_writer(root, clock=self.clock, ids=self.ids) as kb:
                for table in S03_TABLES:
                    if root == self.root and table == "kb_upgrade_event" or root == upgraded and table != "kb_upgrade_event":
                        continue
                    with self.subTest(table=table):
                        self.assertGreater(kb.one(f"SELECT count(*) n FROM {table}")["n"], 0, table)
                        with self.assertRaises(ImmutableRecordError):
                            kb.execute(f"UPDATE {table} SET rowid = rowid")
                        with self.assertRaises(ImmutableRecordError):
                            kb.execute(f"DELETE FROM {table}")

    def test_triggers_require_human_anchor(self):
        """IDs: S03-IDENTITY-TRIGGER, S03-APPEND-ONLY. The DB refuses an assertion status change without a conflict/human resolution, a SYSTEM link event that is not AMBIGUOUS, a resolution by a non-operator, and a member that starts confirmed."""
        self.populate()
        with self.writer() as kb:
            assertion = kb.one("SELECT assertion_id FROM identity_assertion LIMIT 1")["assertion_id"]
            link = kb.one("SELECT link_id FROM paper_asset_link LIMIT 1")["link_id"]
            conflict = kb.one("SELECT conflict_id FROM identity_conflict LIMIT 1")["conflict_id"]
            member = kb.one("SELECT * FROM work_family_member LIMIT 1")
            bad = [
                ("assertion_status_event", dict(event_id="ase_x", assertion_id=assertion, new_status="ACTIVE", cause_kind="HUMAN_RESOLUTION",
                                                cause_ref="ire_missing", recorded_at_utc="2026-11-01T00:00:00.000000Z", semantic_sha256="1" * 64)),
                ("assertion_status_event", dict(event_id="ase_y", assertion_id=assertion, new_status="ACTIVE", cause_kind="IDENTITY_CONFLICT_RAISED",
                                                cause_ref=conflict, recorded_at_utc="2026-11-01T00:00:00.000000Z", semantic_sha256="2" * 64)),
                ("paper_asset_link_event", dict(event_id="pale_x", link_id=link, new_state="HUMAN_CONFIRMED", new_role=None, reason_codes_json="[]",
                                                evidence_json="[]", actor="SYSTEM", cause_ref=conflict, prior_event_id=None,
                                                recorded_at_utc="2026-11-01T00:00:00.000000Z", semantic_sha256="3" * 64)),
                ("identity_resolution_event", dict(event_id="ire_x", conflict_id=conflict, action="DEFER", assertion_updates_json="[]",
                                                   evidence_refs_json='["x"]', actor="SYSTEM", reason="auto", prior_event_id=None,
                                                   decided_at_utc="2026-11-01T00:00:00.000000Z", semantic_sha256="4" * 64)),
                ("work_family_member", dict(member, member_id="wfm_x", confirmation="HUMAN_CONFIRMED", semantic_sha256="5" * 64)),
            ]
            for table, row in bad:
                with self.subTest(table=table, row=row.get("event_id") or row.get("member_id")):
                    with self.assertRaises(KBError):
                        kb.insert(table, row)


if __name__ == "__main__":
    unittest.main()
