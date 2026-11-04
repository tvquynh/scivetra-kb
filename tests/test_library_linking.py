"""S03-B: Reprovane portable-export import with link-policy/1, identity conflicts, human decisions, reconcile."""
import json
import os
import unittest
from pathlib import Path

from tests.helpers import KBTest, openalex_retraction_claim, rv_item, write_rv_export
from tests.s03_helpers import NATIVE, copy_native, s03_item, tree_hashes, write_export
from scivetra_kb.core.canonical import sha256_bytes
from scivetra_kb.core.errors import IntegrityError, KBError, ValidationError
from scivetra_kb.core.manifest import full_manifest, library_manifest, semantic_manifest
from scivetra_kb.library import queries
from scivetra_kb.library.service import LibraryService, conflict_state
from scivetra_kb.metadata.rv_import import import_corpus_export


def lib(kb):
    return LibraryService(kb)


class NativeExportTests(KBTest):
    def test_native_fixture_provenance_is_frozen(self):
        """IDs: S03-RV-NATIVE-01. The frozen fixture is a native Reprovane 2.0.1 export (pinned commit) that verified with the native CLI; its files still match the recorded hashes."""
        report = json.loads((NATIVE / "generation_report.json").read_text(encoding="utf-8"))
        self.assertEqual((report["reprovane_version"], report["pinned_commit"]), ("2.0.1", "6c4793cda915481a831a96f925f2646d80de6cc3"))
        for release in report["releases"]:
            self.assertEqual(release["native_cli_exit_code"], 0)
            self.assertTrue(release["verify_export"]["valid"])
            self.assertEqual(tree_hashes(NATIVE / release["label"]), release["file_sha256"])

    def test_import_native_export_twice_is_idempotent_and_read_only(self):
        """IDs: S03-RV-IMPORT-01, N16, N01. Native export: references, auto/mint decisions, OpenAlex integrity; a second import is a no-op with identical manifests; export bytes unchanged."""
        export = copy_native(self.tmp)
        before = tree_hashes(export)
        with self.writer() as kb:
            first = lib(kb).import_release(export, workspace_label="ws-native", operator="op.a")
            m1, s1 = library_manifest(kb), semantic_manifest(kb)
            second = lib(kb).import_release(export, workspace_label="ws-native", operator="op.a")
            m2, s2 = library_manifest(kb), semantic_manifest(kb)
            nodoi = kb.one("SELECT r.paper_id FROM project_paper_reference r WHERE r.item_claims_json LIKE '%without DOI%'")
            doi_count = kb.one("SELECT count(*) n FROM identity_assertion WHERE paper_id=? AND namespace='doi'", (nodoi["paper_id"],))["n"]
            outcomes = [r["outcome"] for r in kb.all("SELECT outcome FROM import_event ORDER BY rowid")]
            events = kb.all("SELECT subject_value, reported_by FROM integrity_event")
        self.assertEqual((first["new_import"], first["items"], first["references_created"], first["papers_minted"]), (True, 3, 3, 3))
        self.assertEqual((second["new_import"], second["references_created"]), (False, 0))
        self.assertEqual((m1, s1), (m2, s2))
        self.assertEqual(doi_count, 0)                 # N01: paper without DOI
        self.assertEqual(outcomes, ["REGISTERED", "DUPLICATE_NOOP"])
        self.assertEqual(events, [{"subject_value": "10.99999/s03.retracted", "reported_by": "OPENALEX_IS_RETRACTED_VIA_REPROVANE"}])
        self.assertEqual(tree_hashes(export), before)

    def test_later_release_adds_references_without_rewriting(self):
        """IDs: S03-RV-IMPORT-02, N16, N27c. A later native release auto-links single-DOI items exactly; the no-DOI item gets a new paper plus a signal-only candidate; old references are untouched."""
        r1, r2 = copy_native(self.tmp, "s03-r1"), copy_native(self.tmp, "s03-r2")
        with self.writer() as kb:
            lib(kb).import_release(r1, workspace_label="ws", operator="op.a")
            old = kb.all("SELECT * FROM project_paper_reference ORDER BY rowid")
            summary = lib(kb).import_release(r2, workspace_label="ws", operator="op.a")
            after = kb.all("SELECT * FROM project_paper_reference ORDER BY rowid")
            auto = kb.all("SELECT link_key_json FROM project_paper_reference WHERE link_basis='EXACT_MANIFESTATION_ID'")
            candidates = kb.all("SELECT signals_json FROM link_candidate")
        self.assertEqual(after[:3], old)
        self.assertEqual((summary["references_created"], summary["auto_linked"], summary["papers_minted"]), (3, 2, 1))
        self.assertEqual(sorted(json.loads(a["link_key_json"])["namespace"] for a in auto), ["doi", "doi"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual({s["signal"] for s in json.loads(candidates[0]["signals_json"])}, {"OPENALEX_WORK_MATCH", "TITLE_SIMILAR"})

    def test_reconcile_and_source_unavailable(self):
        """IDs: S03-RECONCILE-01, N26. reconcile is read-only: same export CONFIRMED, other release NEW_RELEASE_NOT_IMPORTED; SOURCE_UNAVAILABLE is an explicit operator event that keeps every reference."""
        r1, r2 = copy_native(self.tmp, "s03-r1"), copy_native(self.tmp, "s03-r2")
        with self.writer() as kb:
            lib(kb).import_release(r1, workspace_label="ws", operator="op.a")
        db = Path(self.root, "db", "kb.sqlite").read_bytes()
        with self.reader() as kb:
            self.assertEqual(queries.reconcile(kb, workspace_label="ws", export_dir=r1)["status"], "CONFIRMED")
            self.assertEqual(queries.reconcile(kb, workspace_label="ws", export_dir=r2)["status"], "NEW_RELEASE_NOT_IMPORTED")
            self.assertEqual(queries.reconcile(kb, workspace_label="other", export_dir=r1)["status"], "WORKSPACE_UNKNOWN")
        self.assertEqual(Path(self.root, "db", "kb.sqlite").read_bytes(), db)
        with self.writer() as kb:
            out = lib(kb).set_workspace_availability(workspace_label="ws", availability="SOURCE_UNAVAILABLE", operator="op.a",
                                                    reason="synthetic workspace deleted")
            refs = kb.one("SELECT count(*) n FROM project_paper_reference")["n"]
            states = [r["availability"] for r in kb.all("SELECT availability FROM workspace_availability_event ORDER BY rowid")]
        self.assertEqual((out["references_kept"], refs), (3, 3))
        self.assertEqual(states, ["AVAILABLE", "SOURCE_UNAVAILABLE"])
        with self.reader() as kb:
            self.assertEqual(queries.reconcile(kb, workspace_label="ws", export_dir=r1)["availability"], "SOURCE_UNAVAILABLE")

    def test_reconcile_detects_item_drift(self):
        """IDs: S03-RECONCILE-02. A consistently regenerated export whose item hash changed under the same release manifest is DRIFT (hash comparison, not authentication)."""
        items = [s03_item("cri_d1", dois=[("10.99999/drift", "10.99999/drift")])]
        a = write_export(os.path.join(self.tmp, "a"), items, release_id="crl_drift")
        with self.writer() as kb:
            lib(kb).import_release(a, workspace_label="ws", operator="op.a")
        changed = dict(items[0], item_sha256="e" * 64)
        b = write_export(os.path.join(self.tmp, "b"), [changed], release_id="crl_drift")
        with self.reader() as kb:
            out = queries.reconcile(kb, workspace_label="ws", export_dir=b)
        self.assertEqual((out["status"], out["item_hash_mismatches"]), ("DRIFT", ["cri_d1"]))


class LinkPolicyTests(KBTest):
    def importer(self, items, *, label="ws", release_id="crl_s03a"):
        export = write_export(os.path.join(self.tmp, release_id + label), items, release_id=release_id)
        with self.writer() as kb:
            return lib(kb).import_release(export, workspace_label=label, operator="op.a")

    def test_n27_shared_openalex_different_manifestations(self):
        """IDs: N27, N27a, S03-LINK-L5. Shared OpenAlex W100, preprint DOI vs journal DOI: no auto-link, new paper B, candidate with OPENALEX_WORK_MATCH + DOI_CONFLICT + KIND_CONFLICT, WorkFamily proposal PREPRINT/VERSION_OF_RECORD, review open."""
        self.importer([s03_item("cri_a", dois=[("10.99999/pre.1", "10.99999/pre.1")], openalex=["https://openalex.org/W100"], work_type="preprint",
                                title="Shared work study")], release_id="crl_a")
        out = self.importer([s03_item("cri_b", dois=[("10.99998/jnl.1", "10.99998/jnl.1")], openalex=["https://openalex.org/W100"],
                                      work_type="article", title="Shared work study")], release_id="crl_b")
        with self.reader() as kb:
            candidate = kb.one("SELECT * FROM link_candidate")
            roles = sorted(r["role"] for r in kb.all("SELECT role FROM work_family_member"))
            states = {r["confirmation"] for r in kb.all("SELECT confirmation FROM work_family_member")}
            queue = queries.review_queue(kb)
            refs = [r["link_basis"] for r in kb.all("SELECT link_basis FROM project_paper_reference ORDER BY rowid")]
        self.assertEqual((out["auto_linked"], out["papers_minted"]), (0, 1))
        self.assertEqual(refs, ["NEW_PAPER_MINTED", "NEW_PAPER_MINTED"])
        self.assertIn("OPENALEX_WORK_MATCH", {s["signal"] for s in json.loads(candidate["signals_json"])})
        self.assertEqual(set(json.loads(candidate["conflict_codes_json"])), {"DOI_CONFLICT", "KIND_CONFLICT"})
        self.assertEqual((roles, states), (["PREPRINT", "VERSION_OF_RECORD"], {"PROPOSED"}))
        self.assertEqual((queue["counts"]["link_candidates"], queue["counts"]["work_family_proposals"]), (1, 2))

    def test_n27b_openalex_only_never_links(self):
        """IDs: N27b, S03-LINK-OPENALEX-SIGNAL. An item with only an OpenAlex ID matching a KB paper mints a new paper and a signal-only candidate."""
        self.importer([s03_item("cri_a", dois=[("10.99999/w.1", "10.99999/w.1")], openalex=["https://openalex.org/W200"])], release_id="crl_a")
        out = self.importer([s03_item("cri_b", openalex=["https://openalex.org/W200"], title="Different words entirely")], release_id="crl_b")
        with self.reader() as kb:
            candidate = kb.one("SELECT * FROM link_candidate")
            conflicts = kb.one("SELECT count(*) n FROM identity_conflict")["n"]
        self.assertEqual((out["auto_linked"], out["papers_minted"], conflicts), (0, 1, 0))
        self.assertEqual([s["signal"] for s in json.loads(candidate["signals_json"])], ["OPENALEX_WORK_MATCH"])

    def test_n27c_exact_single_doi_auto_links(self):
        """IDs: N27c, S03-LINK-L3. The item's only DOI equals a KB paper's ACTIVE DOI, same kind, same venue: auto-link EXACT_MANIFESTATION_ID keyed by doi."""
        self.importer([s03_item("cri_a", dois=[("10.99999/c.1", "10.99999/c.1")])], release_id="crl_a")
        out = self.importer([s03_item("cri_b", dois=[("https://doi.org/10.99999/C.1", "10.99999/c.1")])], label="ws2", release_id="crl_b")
        with self.reader() as kb:
            refs = kb.all("SELECT paper_id, link_basis, link_key_json FROM project_paper_reference ORDER BY rowid")
        self.assertEqual(out["auto_linked"], 1)
        self.assertEqual(refs[0]["paper_id"], refs[1]["paper_id"])
        self.assertEqual(json.loads(refs[1]["link_key_json"]), {"namespace": "doi", "value_normalized": "10.99999/c.1"})

    def test_n27d_kind_conflict_blocks_auto_link(self):
        """IDs: N27d, S03-LINK-C1. Same DOI but the KB paper is a PREPRINT (operator kind event) and the item a journal article: no auto-link, KIND_CONFLICT, IdentityConflict, both DOI mappings CONFLICTING."""
        self.importer([s03_item("cri_a", dois=[("10.99999/d.1", "10.99999/d.1")], work_type="other-type")], release_id="crl_a")
        with self.writer() as kb:
            paper = kb.one("SELECT paper_id FROM global_paper")["paper_id"]
            lib(kb).set_manifestation_kind(paper_id=paper, kind="PREPRINT", operator="op.a", reason="posted-content claim", claim_refs=["synthetic:claim"])
        out = self.importer([s03_item("cri_b", dois=[("10.99999/d.1", "10.99999/d.1")], work_type="article")], release_id="crl_b")
        with self.reader() as kb:
            conflict = kb.one("SELECT * FROM identity_conflict")
            statuses = {r["paper_id"]: r["effective_status"] for r in kb.all(
                "SELECT paper_id, effective_status FROM identity_assertion_effective WHERE namespace='doi'")}
            codes = json.loads(kb.one("SELECT conflict_codes_json FROM link_candidate")["conflict_codes_json"])
        self.assertEqual((out["auto_linked"], out["papers_minted"]), (0, 1))
        self.assertEqual(conflict["kind"], "SHARED_MLI_WITH_CONFLICT")
        self.assertIn("KIND_CONFLICT", codes)
        self.assertEqual(set(statuses.values()), {"CONFLICTING"})
        self.assertEqual(len(statuses), 2)

    def test_n02_n27e_multiple_dois_conflicting(self):
        """IDs: N02, N27e, S03-LINK-L1. An item with a preprint DOI and a journal DOI: both assertions CONFLICTING, no auto-link, MULTI_DOI_ITEM conflict for a human."""
        self.importer([s03_item("cri_a", dois=[("10.99999/e.1", "10.99999/e.1")])], release_id="crl_a")
        out = self.importer([s03_item("cri_m", dois=[("10.99999/e.1", "10.99999/e.1"), ("10.99998/e.2", "10.99998/e.2")])], release_id="crl_b")
        with self.reader() as kb:
            conflict = kb.one("SELECT * FROM identity_conflict")
            minted = kb.one("SELECT paper_id FROM project_paper_reference WHERE corpus_item_id='cri_m'")["paper_id"]
            mine = sorted(r["effective_status"] for r in kb.all("SELECT effective_status FROM identity_assertion_effective WHERE paper_id=? AND namespace='doi'",
                                                                (minted,)))
            queue = queries.review_queue(kb)
        self.assertEqual((out["auto_linked"], conflict["kind"]), (0, "MULTI_DOI_ITEM"))
        self.assertEqual(mine, ["CONFLICTING", "CONFLICTING"])
        self.assertEqual(queue["counts"]["identity_conflicts"], 1)

    def test_n03_doi_collision_with_legacy_s02_papers(self):
        """IDs: N03, S03-LINK-COLLISION, S03-DOCTOR-IA1. Two S02-imported papers share an ACTIVE DOI (an IA-1 violation doctor reports); an S03 item with that DOI raises DOI_COLLISION, mints a paper and makes all mappings CONFLICTING, which clears the violation without a merge."""
        from scivetra_kb.core.doctor import doctor
        for name, sem in (("s2a", "a"), ("s2b", "b")):
            export = write_rv_export(os.path.join(self.tmp, name), [rv_item("cri_x", dois=[("10.99999/col", "10.99999/col")])], release_semantic=sem * 64)
            with self.writer() as kb:
                import_corpus_export(kb, export, workspace_label=name)
        self.assertIn("IA1_DOI_MAPPED_TO_SEVERAL_PAPERS_ACTIVE:1", doctor(self.root, full=True)["problems"])
        out = self.importer([s03_item("cri_c", dois=[("10.99999/col", "10.99999/col")])])
        with self.reader() as kb:
            conflict = kb.one("SELECT * FROM identity_conflict")
            papers = kb.one("SELECT count(*) n FROM global_paper")["n"]
            statuses = {r["effective_status"] for r in kb.all("SELECT effective_status FROM identity_assertion_effective WHERE namespace='doi'")}
        self.assertEqual((out["auto_linked"], conflict["kind"], len(json.loads(conflict["paper_ids_json"])), papers), (0, "DOI_COLLISION", 3, 3))
        self.assertEqual(statuses, {"CONFLICTING"})
        self.assertNotIn("IA1_DOI_MAPPED_TO_SEVERAL_PAPERS_ACTIVE:1", doctor(self.root, full=True)["problems"])

    def test_n28_trailing_space_doi_never_links(self):
        """IDs: N28, MF-18.33, S03-LINK-N28. A trailing-space DOI is a different comparison key: no auto-link, new paper, near-duplicate warning; operator search input with the space is AMBIGUOUS and not looked up."""
        self.importer([s03_item("cri_a", dois=[("10.99999/x", "10.99999/x")])], release_id="crl_a")
        out = self.importer([s03_item("cri_b", dois=[("10.99999/x ", "10.99999/x")])], release_id="crl_b")
        with self.reader() as kb:
            keys = sorted(r["value_normalized"] for r in kb.all("SELECT value_normalized FROM identity_assertion WHERE namespace='doi'"))
            warnings = kb.all("SELECT kind FROM identifier_warning")
            search = queries.search(kb, doi="10.99999/x ")
            exact = queries.search(kb, doi="10.99999/x")
        self.assertEqual((out["auto_linked"], out["papers_minted"]), (0, 1))
        self.assertEqual(keys, ["10.99999/x", "10.99999/x "])
        self.assertEqual(warnings, [{"kind": "NEAR_DUPLICATE_TRAILING_SPACE"}])
        self.assertEqual((search["doi_parse"]["status"], search["papers"]), ("AMBIGUOUS", []))
        self.assertEqual(len(exact["papers"]), 1)

    def test_venue_and_version_conflicts_block_auto_link(self):
        """IDs: S03-LINK-C2, S03-LINK-C4. Different venues (C2) or a SUBMITTED-only candidate versus a journal item (C4) prevent the exact-DOI auto-link."""
        from scivetra_kb.assets.service import AssetService
        self.importer([s03_item("cri_a", dois=[("10.99999/v.1", "10.99999/v.1")], venue="Venue One")], release_id="crl_a")
        out = self.importer([s03_item("cri_b", dois=[("10.99999/v.1", "10.99999/v.1")], venue="Venue Two")], release_id="crl_b")
        self.assertEqual(out["auto_linked"], 0)
        self.importer([s03_item("cri_c", dois=[("10.99999/v.2", "10.99999/v.2")], venue=None, work_type="unclassified")], release_id="crl_c")
        with self.writer() as kb:
            paper = kb.one("SELECT paper_id FROM project_paper_reference WHERE corpus_item_id='cri_c'")["paper_id"]
            AssetService(kb).add_document_version(paper_id=paper, version_class="SUBMITTED", operator="op.a",
                                                  evidence=[{"source": "ARXIV_VERSION_STAMP", "claim_ref": "synthetic:arxiv", "value": "v1"}])
        out = self.importer([s03_item("cri_d", dois=[("10.99999/v.2", "10.99999/v.2")], venue=None, work_type="article")], release_id="crl_d")
        with self.reader() as kb:
            codes = [json.loads(r["conflict_codes_json"]) for r in kb.all("SELECT conflict_codes_json FROM link_candidate ORDER BY rowid")]
        self.assertEqual(out["auto_linked"], 0)
        self.assertEqual(codes, [["VENUE_CONFLICT"], ["VERSION_CONFLICT"]])

    def test_pmid_only_item_auto_links_and_openalex_never_keys(self):
        """IDs: S03-LINK-MLI, S03-LINK-OPENALEX-SIGNAL. Without a DOI, an exact PMID is an MLI key for auto-link; the DB refuses an EXACT_MANIFESTATION_ID reference keyed by openalex_work."""
        self.importer([s03_item("cri_a", extra_identifiers=[("pmid", "1234567")])], release_id="crl_a")
        out = self.importer([s03_item("cri_b", extra_identifiers=[("pmid", "1234567")])], release_id="crl_b")
        self.assertEqual(out["auto_linked"], 1)
        with self.writer() as kb:
            ref = kb.one("SELECT * FROM project_paper_reference ORDER BY rowid DESC LIMIT 1")
            ref.update(reference_id="ppr_x", corpus_item_id="cri_z", semantic_sha256="9" * 64,
                       link_key_json=json.dumps({"namespace": "openalex_work", "value_normalized": "https://openalex.org/W1"}))
            with self.assertRaises(ValidationError):
                kb.insert("project_paper_reference", ref)


class UntrustedExportTests(KBTest):
    def refused(self, directory, code):
        with self.writer() as kb:
            before = {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("global_paper", "identity_assertion", "project_paper_reference",
                                                                          "metadata_claim", "integrity_event")}
            with self.assertRaises(KBError) as ctx:
                lib(kb).import_release(directory, workspace_label="ws", operator="op.a")
            after = {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in before}
            rejected = kb.one("SELECT outcome, reason_codes_json FROM import_event ORDER BY rowid DESC LIMIT 1")
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(before, after)
        self.assertEqual((rejected["outcome"], json.loads(rejected["reason_codes_json"])), ("REJECTED", [code]))
        return ctx.exception

    def test_unsafe_json_values_in_export(self):
        """IDs: S03-RV-UNSAFE-01, S02-UNSAFE-NAN-TOKEN, S02-UNSAFE-NONFINITE, S02-UNSAFE-SURROGATE. NaN, 1e9999 and a lone surrogate inside a consistently checksummed export are refused before any identity write; the message never echoes the value."""
        good = (json.dumps(s03_item("cri_u")) + "\n")
        for label, bad in (("nan", good.replace('"ordinal": 0', '"ordinal": NaN')), ("inf", good.replace('"ordinal": 0', '"ordinal": 1e9999')),
                           ("sur", good.replace("Synthetic title cri_u", "\\ud800"))):
            with self.subTest(label=label):
                export = write_export(os.path.join(self.tmp, label), [s03_item("cri_u")], jsonl_override=bad.encode())
                exc = self.refused(export, "EXPORT_UNSAFE_JSON")
                self.assertNotIn("ud800", str(exc))

    def test_duplicate_keys_ids_and_shapes(self):
        """IDs: S03-RV-UNSAFE-02. Duplicate JSON keys, duplicate corpus_item_id / publication_entity_id, a wrong-typed field and malformed UTF-8 are refused with stable codes."""
        item = s03_item("cri_k")
        dup_key = (json.dumps(item)[:-1] + ', "ordinal": 1}\n').encode()
        self.refused(write_export(os.path.join(self.tmp, "k"), [item], jsonl_override=dup_key), "EXPORT_DUPLICATE_KEY")
        twin = dict(s03_item("cri_k2"), publication_entity_id=item["publication_entity_id"])
        self.refused(write_export(os.path.join(self.tmp, "e"), [item, twin]), "EXPORT_DUPLICATE_IDENTIFIER")
        self.refused(write_export(os.path.join(self.tmp, "s"), [dict(item, item_sha256=12345)]), "EXPORT_SHAPE_INVALID")
        self.refused(write_export(os.path.join(self.tmp, "u"), [item], jsonl_override=b'{"corpus_item_id": "\xff"}\n'), "EXPORT_UNSAFE_JSON")

    def test_hash_schema_and_inventory_disagreements(self):
        """IDs: S03-RV-UNSAFE-03, RV-IMPORT-04. Tampered bytes, wrong schema, an extra file, an expected-hash mismatch and a release re-imported with different bytes are refused without partial writes."""
        export = write_export(os.path.join(self.tmp, "t"), [s03_item("cri_t")])
        Path(export, "corpus.jsonl").write_bytes(Path(export, "corpus.jsonl").read_bytes() + b"\n")
        self.refused(export, "EXPORT_HASH_MISMATCH")
        self.refused(write_export(os.path.join(self.tmp, "w"), [s03_item("cri_t")], manifest_extra={"schema": "corpus-portable/9"}),
                     "EXPORT_SCHEMA_UNSUPPORTED")
        extra = write_export(os.path.join(self.tmp, "x"), [s03_item("cri_t")])
        Path(extra, "notes.txt").write_text("x")
        self.refused(extra, "EXPORT_INVENTORY_MISMATCH")
        good = write_export(os.path.join(self.tmp, "g"), [s03_item("cri_t")], release_id="crl_same")
        with self.writer() as kb:
            with self.assertRaises(IntegrityError) as ctx:
                lib(kb).import_release(good, workspace_label="ws", operator="op.a", expected_manifest_sha256="b" * 64)
            self.assertEqual(ctx.exception.code, "EXPECTED_MANIFEST_MISMATCH")
            lib(kb).import_release(good, workspace_label="ws", operator="op.a")
        other = write_export(os.path.join(self.tmp, "o"), [s03_item("cri_t", title="changed")], release_id="crl_same")
        self.refused(other, "EXPORT_RELEASE_CONFLICT")

    def test_symlinked_export_refused(self):
        """IDs: S03-RV-UNSAFE-04. An export directory reached through a symlink or junction is refused (REPARSE_POINT)."""
        export = write_export(os.path.join(self.tmp, "real"), [s03_item("cri_l")])
        link = os.path.join(self.tmp, "link")
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(export, link)
        else:
            os.symlink(export, link)
        self.refused(link, "REPARSE_POINT")


class HumanDecisionTests(KBTest):
    def setUp(self):
        super().setUp()
        for rid, items in (("crl_a", [s03_item("cri_a", dois=[("10.99999/h.pre", "10.99999/h.pre")], openalex=["https://openalex.org/W7"],
                                               work_type="preprint", title="Human decision study")]),
                           ("crl_b", [s03_item("cri_b", dois=[("10.99999/h.jnl", "10.99999/h.jnl")], openalex=["https://openalex.org/W7"],
                                               title="Human decision study")]),
                           ("crl_c", [s03_item("cri_m", dois=[("10.99999/h.m1", "10.99999/h.m1"), ("10.99999/h.m2", "10.99999/h.m2")])])):
            export = write_export(os.path.join(self.tmp, rid), items, release_id=rid)
            with self.writer() as kb:
                lib(kb).import_release(export, workspace_label="ws", operator="op.a")

    def test_link_decisions_are_append_only_and_confirm_family(self):
        """IDs: S03-LINK-L6, WF-1. DEFER keeps the candidate open; DIFFERENT_MANIFESTATION_SAME_WORK confirms both family members; SAME_MANIFESTATION is recorded without merging; history is kept."""
        with self.writer() as kb:
            cid = kb.one("SELECT link_candidate_id FROM link_candidate")["link_candidate_id"]
            lib(kb).decide_link(link_candidate_id=cid, decision="DEFER", operator="op.b", reason="need notice")
            self.assertEqual(queries.review_queue(kb)["counts"]["link_candidates"], 1)
            out = lib(kb).decide_link(link_candidate_id=cid, decision="DIFFERENT_MANIFESTATION_SAME_WORK", operator="op.b", reason="preprint of the article")
            confirmations = {r["confirmation"] for r in kb.all("SELECT confirmation FROM work_family_member_event")}
            same = lib(kb).decide_link(link_candidate_id=cid, decision="SAME_MANIFESTATION", operator="op.c", reason="second opinion")
            papers = kb.one("SELECT count(*) n FROM global_paper")["n"]
            history = [r["decision"] for r in kb.all("SELECT decision FROM link_decision ORDER BY rowid")]
            queue = queries.review_queue(kb)
        self.assertEqual(len(out["family_member_updates"]), 2)
        self.assertEqual(confirmations, {"HUMAN_CONFIRMED"})
        self.assertIn("not merged", same["note"])
        self.assertEqual((papers, history), (3, ["DEFER", "DIFFERENT_MANIFESTATION_SAME_WORK", "SAME_MANIFESTATION"]))
        self.assertEqual((queue["counts"]["link_candidates"], queue["counts"]["work_family_proposals"]), (0, 0))

    def test_identity_conflict_needs_human_anchored_resolution(self):
        """IDs: S03-IDENTITY-HUMAN-01. Resolving the multi-DOI conflict needs an operator, a reason and resolvable evidence; DOI_SELECTED_FOR_ITEM re-activates one DOI and withdraws the other via events."""
        with self.writer() as kb:
            conflict = kb.one("SELECT * FROM identity_conflict WHERE kind='MULTI_DOI_ITEM'")
            a1, a2 = json.loads(conflict["assertion_ids_json"])
            with self.assertRaises(ValidationError) as ctx:
                lib(kb).resolve_conflict(conflict_id=conflict["conflict_id"], action="DOI_SELECTED_FOR_ITEM", operator="op.b", reason="x",
                                         evidence_refs=["not-a-ref"], activate=[a1], withdraw=[a2])
            self.assertEqual(ctx.exception.code, "RESOLUTION_EVIDENCE_UNRESOLVED")
            with self.assertRaises(ValidationError):
                lib(kb).resolve_conflict(conflict_id=conflict["conflict_id"], action="DOI_SELECTED_FOR_ITEM", operator="someone@example.invalid",
                                         reason="x", evidence_refs=[a1], activate=[a1], withdraw=[a2])
            out = lib(kb).resolve_conflict(conflict_id=conflict["conflict_id"], action="DOI_SELECTED_FOR_ITEM", operator="op.b",
                                           reason="journal DOI confirmed on the notice", evidence_refs=[a1], activate=[a1], withdraw=[a2])
            statuses = {r["assertion_id"]: (r["status"], r["effective_status"]) for r in kb.all(
                "SELECT assertion_id, status, effective_status FROM identity_assertion_effective WHERE assertion_id IN (?,?)", (a1, a2))}
        self.assertEqual(out["conflict_state"], "RESOLVED")
        self.assertEqual(statuses, {a1: ("CONFLICTING", "ACTIVE"), a2: ("CONFLICTING", "RETRACTED_ASSERTION")})

    def test_resolution_cannot_violate_ia1(self):
        """IDs: S03-IDENTITY-HUMAN-02, IA-1. A human cannot activate a DOI mapping while another paper holds the same DOI ACTIVE."""
        export = write_export(os.path.join(self.tmp, "dup"), [s03_item("cri_d", dois=[("10.99999/h.pre", "10.99999/h.pre")], work_type="article",
                                                                       venue="Elsewhere")], release_id="crl_d")
        with self.writer() as kb:
            lib(kb).import_release(export, workspace_label="ws", operator="op.a")
            conflict = kb.one("SELECT * FROM identity_conflict WHERE kind='SHARED_MLI_WITH_CONFLICT'")
            ids = json.loads(conflict["assertion_ids_json"])
            with self.assertRaises(ValidationError) as ctx:
                lib(kb).resolve_conflict(conflict_id=conflict["conflict_id"], action="DOI_SELECTED_FOR_ITEM", operator="op.b", reason="both",
                                         evidence_refs=ids[:1], activate=ids)
        self.assertEqual(ctx.exception.code, "IA1_WOULD_BE_VIOLATED")


class DeterminismTests(KBTest):
    def test_fresh_database_same_library_manifest(self):
        """IDs: S03-DETERMINISM-01. The same native and synthetic imports into a fresh KB give identical S02 and library semantic manifests (IDs and clocks excluded)."""
        def run(root):
            from scivetra_kb.core.db import init_kb, open_writer
            from scivetra_kb.core.ids import FixedClock, IdProvider
            clock = FixedClock("2026-11-01T05:00:00.000000Z")
            init_kb(root, clock=clock, ids=IdProvider(clock))
            with open_writer(root, clock=clock, ids=IdProvider(clock)) as kb:
                lib(kb).import_release(copy_native(self.tmp, "s03-r1") if not os.path.exists(os.path.join(self.tmp, "native-s03-r1")) else
                                       os.path.join(self.tmp, "native-s03-r1"), workspace_label="ws", operator="op.a")
                lib(kb).import_release(copy_native(self.tmp, "s03-r2") if not os.path.exists(os.path.join(self.tmp, "native-s03-r2")) else
                                       os.path.join(self.tmp, "native-s03-r2"), workspace_label="ws", operator="op.a")
                return full_manifest(kb)
        a, b = run(os.path.join(self.tmp, "k1")), run(os.path.join(self.tmp, "k2"))
        self.assertEqual(a, b)
        evidence = os.environ.get("RKB_EVIDENCE_DIR")
        if evidence:
            Path(evidence, "library_manifest_native.json").write_text(json.dumps(a, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
