import json
import os
import unittest
from pathlib import Path

from tests.helpers import KBTest, openalex_retraction_claim, rv_item, write_rv_export
from scivetra_kb.core.canonical import sha256_bytes
from scivetra_kb.core.errors import IntegrityError, ValidationError
from scivetra_kb.metadata.rv_import import import_corpus_export


class ReprovaneImportTests(KBTest):
    def test_import_creates_papers_assertions_and_divergence(self):
        """IDs: RV-IMPORT-01, MF-18b. Papers, scoped context assertions and RV divergences from a synthetic RV-Stage11 export."""
        export = write_rv_export(os.path.join(self.tmp, "rv"), [
            rv_item("cri_1", dois=[("https://doi.org/10.99999/a%2Cb", "10.99999/a%2cb")], openalex=["https://openalex.org/W1"]),
            rv_item("cri_2", dois=[("10.99999/one", "10.99999/one"), ("10.99998/two", "10.99998/two")])])
        before = {p.name: p.read_bytes() for p in Path(export).iterdir()}
        with self.writer() as kb:
            summary = import_corpus_export(kb, export, workspace_label="ws1")
            assertions = kb.all("SELECT namespace, value_normalized, status, scope_ref FROM identity_assertion ORDER BY rowid")
            divergences = kb.all("SELECT divergence_kind, rv_value_normalized, kb_comparison_key FROM normalization_divergence")
        self.assertEqual(summary["items"], 2)
        self.assertEqual(divergences, [{"divergence_kind": "PERCENT_ENCODING", "rv_value_normalized": "10.99999/a%2cb", "kb_comparison_key": "10.99999/a,b"}])
        doi_rows = [a for a in assertions if a["namespace"] == "doi"]
        self.assertEqual([a["status"] for a in doi_rows], ["ACTIVE", "CONFLICTING", "CONFLICTING"])
        scoped = [a for a in assertions if a["namespace"].startswith("reprovane_")]
        self.assertTrue(scoped and all(a["scope_ref"] == "rv:ws1:crl_synthetic" for a in scoped))
        self.assertEqual({p.name: p.read_bytes() for p in Path(export).iterdir()}, before)

    def test_import_is_idempotent(self):
        """IDs: RV-IMPORT-02. Importing the same export twice creates no new rows."""
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_1", dois=[("10.99999/i", "10.99999/i")], integrity=openalex_retraction_claim())])
        with self.writer() as kb:
            first = import_corpus_export(kb, export, workspace_label="ws1")
            second = import_corpus_export(kb, export, workspace_label="ws1")
        self.assertTrue(first["new_import"])
        self.assertFalse(second["new_import"])
        self.assertEqual(second["papers_created"], 0)
        self.assertEqual(second["assertions_created"], 0)
        self.assertEqual(second["integrity_events"], 0)

    def test_openalex_integrity_coverage_only_when_projected(self):
        """IDs: RV-IMPORT-03, N05. OpenAlex RETRACTION via RV gives an event; an empty list gives NO_SIGNAL; an unprojected field gives no coverage."""
        export = write_rv_export(os.path.join(self.tmp, "rv"), [
            rv_item("cri_r", dois=[("10.99999/r", "10.99999/r")], integrity=openalex_retraction_claim()),
            rv_item("cri_s", dois=[("10.99999/s", "10.99999/s")], integrity=[]),
            rv_item("cri_n", dois=[("10.99999/n", "10.99999/n")])])
        with self.writer() as kb:
            import_corpus_export(kb, export, workspace_label="ws1")
            cov = {r["subject_value"]: r["outcome"] for r in kb.all("SELECT subject_value, outcome FROM integrity_coverage")}
            events = kb.all("SELECT subject_value, reported_by FROM integrity_event")
        self.assertEqual(cov, {"10.99999/r": "SIGNAL_REPORTED", "10.99999/s": "NO_SIGNAL_REPORTED"})
        self.assertEqual(events, [{"subject_value": "10.99999/r", "reported_by": "OPENALEX_IS_RETRACTED_VIA_REPROVANE"}])

    def test_tampered_or_incomplete_export_refused(self):
        """IDs: RV-IMPORT-04, N01. Hash mismatch, extra files, wrong schema and a wrong expected manifest hash are refused before any write."""
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_1", dois=[("10.99999/t", "10.99999/t")])])
        with self.writer() as kb:
            with self.assertRaises(IntegrityError):
                import_corpus_export(kb, export, workspace_label="ws1", expected_manifest_sha256="b" * 64)
        Path(export, "corpus.jsonl").write_bytes(Path(export, "corpus.jsonl").read_bytes() + b"\n")
        with self.writer() as kb:
            with self.assertRaises(IntegrityError):
                import_corpus_export(kb, export, workspace_label="ws1")
        export2 = write_rv_export(os.path.join(self.tmp, "rv2"), [rv_item("cri_1")])
        Path(export2, "extra.txt").write_text("x")
        with self.writer() as kb:
            with self.assertRaises(ValidationError):
                import_corpus_export(kb, export2, workspace_label="ws1")
            self.assertEqual(kb.one("SELECT count(*) n FROM global_paper")["n"], 0)

    def test_no_reprovane_database_access(self):
        """IDs: RV-IMPORT-05, N16-SUBSET. The importer accepts only the four portable files; a folder containing db/litintel.sqlite is refused."""
        export = write_rv_export(os.path.join(self.tmp, "rv"), [rv_item("cri_1")])
        os.makedirs(os.path.join(export, "db"))
        Path(export, "db", "litintel.sqlite").write_bytes(b"not opened")
        with self.writer() as kb:
            with self.assertRaises(ValidationError) as ctx:
                import_corpus_export(kb, export, workspace_label="ws1")
        self.assertEqual(ctx.exception.code, "EXPORT_INVENTORY_MISMATCH")
        self.assertEqual(Path(export, "db", "litintel.sqlite").read_bytes(), b"not opened")


if __name__ == "__main__":
    unittest.main()
