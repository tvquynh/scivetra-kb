import json
import os
import shutil
import tempfile
import unittest

from tests.helpers import (KBTest, cr_work, dc_doi, openalex_retraction_claim, rv_item, structured, write_rv_export)
from scivetra_kb.core.db import init_kb, open_writer
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
from scivetra_kb.core.manifest import semantic_manifest
from scivetra_kb.metadata.rv_import import import_corpus_export
from scivetra_kb.metadata.service import MetadataService
from scivetra_kb.metadata.transport import FixtureTransport

RET = {"DOI": "10.99999/n.1", "type": "retraction", "source": "publisher"}
RW = {"DOI": "10.99999/n.1", "type": "retraction", "source": "retraction-watch", "record-id": 7}


def build_transport():
    ft = FixtureTransport()
    KBTest.route_ra(ft, "10.99999/id.1")
    KBTest.route_ra(ft, "10.99999/id.2", "DataCite")
    KBTest.add_crossref(ft, "10.99999/id.1", cr_work("10.99999/id.1", title=["Idem"], **{"updated-by": [RET, RW]}))
    KBTest.add_datacite(ft, "10.99999/id.2", dc_doi("10.99999/id.2", titles=[{"title": "Data"}], publisher="Repo"))
    return ft


def scenario(root, export_dir, clock, ids):
    with open_writer(root, clock=clock, ids=ids) as kb:
        import_corpus_export(kb, export_dir, workspace_label="ws")
        service = MetadataService(kb, build_transport(), sleeper=lambda s: None, jitter=lambda: 0.0)
        service.fetch([structured("10.99999/id.1"), structured("10.99999/id.2")])


class IdempotencyTests(KBTest):
    def export(self):
        return write_rv_export(os.path.join(self.tmp, "rv"), [
            rv_item("cri_1", dois=[("10.99999/id.1", "10.99999/id.1")], integrity=openalex_retraction_claim()),
            rv_item("cri_2", dois=[("10.99999/id.2", "10.99999/id.2")], integrity=[]),
            rv_item("cri_3", dois=[("10.99999/x ", "10.99999/x")])])

    def counts(self, kb):
        tables = [r[0] for r in kb.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        operational = {"provider_check", "request_reservation", "request_completion", "fetch_run"}
        return {t: kb.connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables if t not in operational}

    def test_repeat_import_is_idempotent(self):
        """IDs: S02-IDEMPOTENT-01. Re-running the same fixture import and fetches adds no semantic row; the semantic manifest is byte-stable."""
        export = self.export()
        scenario(self.root, export, self.clock, self.ids)
        with self.reader() as kb:
            before, counts_before = semantic_manifest(kb), self.counts(kb)
        self.clock.advance(hours=1)
        scenario(self.root, export, self.clock, self.ids)
        with self.reader() as kb:
            after, counts_after = semantic_manifest(kb), self.counts(kb)
            checks = kb.all("SELECT result, count(*) n FROM provider_check GROUP BY result ORDER BY result")
        self.assertEqual(before["manifest_sha256"], after["manifest_sha256"])
        self.assertEqual(counts_before, counts_after)
        # Second run: every provider record is re-seen with identical bytes; only the operational check log grows.
        self.assertTrue(any(c["result"] == "UNCHANGED" and c["n"] >= 2 for c in checks), checks)
        self.assertGreater(counts_after["integrity_event"], 0)
        evidence = os.environ.get("RKB_EVIDENCE_DIR")
        if evidence:
            with open(os.path.join(evidence, "idempotency_hashes.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"run_1_manifest_sha256": before["manifest_sha256"], "run_2_manifest_sha256": after["manifest_sha256"],
                           "semantic_counts": before["counts"], "table_counts_run_1": counts_before, "table_counts_run_2": counts_after},
                          fh, indent=1, sort_keys=True)
        self.manifest_a = before["manifest_sha256"]

    def test_fresh_database_same_manifest(self):
        """IDs: S02-IDEMPOTENT-02. A fresh database fed the same evidence (different IDs and timestamps) has the same semantic manifest."""
        export = self.export()
        scenario(self.root, export, self.clock, self.ids)
        with self.reader() as kb:
            a = semantic_manifest(kb)["manifest_sha256"]
        other_tmp = tempfile.mkdtemp(prefix="rkb-test-b-")
        try:
            clock = FixedClock("2027-03-03T03:03:03.000000Z")
            ids = SequentialIdProvider(clock)
            ids._n = 5000
            root_b = os.path.join(other_tmp, "kb")
            init_kb(root_b, clock=clock, ids=ids)
            scenario(root_b, export, clock, ids)
            with open_writer(root_b, clock=clock, ids=ids) as kb:
                b = semantic_manifest(kb)["manifest_sha256"]
        finally:
            shutil.rmtree(other_tmp, ignore_errors=True)
        self.assertEqual(a, b)
        evidence = os.environ.get("RKB_EVIDENCE_DIR")
        if evidence:
            with open(os.path.join(evidence, "fresh_db_manifest.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"database_a_manifest_sha256": a, "database_b_manifest_sha256": b}, fh, indent=1)


if __name__ == "__main__":
    unittest.main()
