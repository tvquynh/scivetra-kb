"""S03-C: DocumentVersion, explicit synthetic-file registration (G0-G2), G1 sandbox, G3 identity, PAL-1 (N04), N10."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import KBTest
from tests.pdf_fixtures import html_challenge, malformed_xref, synthetic_pdf, title_page, truncated
from tests.s03_helpers import s03_item, write_export
from scivetra_kb.assets import sandbox
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core import paths
from scivetra_kb.core.errors import ImmutableRecordError, IntegrityError, KBError, ValidationError
from scivetra_kb.library import queries
from scivetra_kb.library.service import LibraryService

TITLE_A, TITLE_B = "Synthetic evidence of coastal sediment transport", "Synthetic model of glacier melt timing"
PUBLISHED = [{"source": "CROSSREF_LICENSE_CONTENT_VERSION", "claim_ref": "synthetic:claim:vor", "value": "vor"}]


class AssetCase(KBTest):
    def setUp(self):
        super().setUp()
        self.fixtures = os.path.join(self.tmp, "fixture-root")
        os.makedirs(self.fixtures)
        export = write_export(os.path.join(self.tmp, "rv"), [
            s03_item("cri_a", dois=[("10.99999/asset.a", "10.99999/asset.a")], title=TITLE_A, author="Ada Lovelace-Synthetic"),
            s03_item("cri_b", dois=[("10.99999/asset.b", "10.99999/asset.b")], title=TITLE_B, author="Grace Synthetic")])
        with self.writer() as kb:
            LibraryService(kb).import_release(export, workspace_label="ws", operator="op.a")
            self.paper_a = kb.one("SELECT paper_id FROM project_paper_reference WHERE corpus_item_id='cri_a'")["paper_id"]
            self.paper_b = kb.one("SELECT paper_id FROM project_paper_reference WHERE corpus_item_id='cri_b'")["paper_id"]
            svc = AssetService(kb)
            self.dv_a = svc.add_document_version(paper_id=self.paper_a, version_class="PUBLISHED", evidence=PUBLISHED, operator="op.a")["document_version_id"]
            self.dv_b = svc.add_document_version(paper_id=self.paper_b, version_class="PUBLISHED", evidence=PUBLISHED, operator="op.a")["document_version_id"]

    def put(self, name, data):
        Path(self.fixtures, name).write_bytes(data)
        return name

    def register(self, name, dv=None, **kw):
        with self.writer() as kb:
            return AssetService(kb).register_asset(fixture_root=self.fixtures, relative_path=name, document_version_id=dv or self.dv_a,
                                                   operator="op.a", **kw)

    def paper_a_pdf(self, **kw):
        return synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99999/asset.a", body=["Body."], **kw)])


class DocumentVersionTests(AssetCase):
    def test_dv1_version_needs_allowlisted_evidence(self):
        """IDs: S03-DV-01, DV-1. PUBLISHED without evidence and evidence from a filename/URL/host are refused (service and DB trigger); UNKNOWN needs none."""
        with self.writer() as kb:
            svc = AssetService(kb)
            for evidence in ([], [{"source": "FILENAME", "claim_ref": "x", "value": "vor"}], [{"source": "URL_HOST", "claim_ref": "x", "value": "publisher"}]):
                with self.subTest(evidence=evidence):
                    with self.assertRaises(ValidationError):
                        svc.add_document_version(paper_id=self.paper_a, version_class="ACCEPTED", evidence=evidence, operator="op.a")
            self.assertTrue(svc.add_document_version(paper_id=self.paper_a, version_class="UNKNOWN", evidence=[], operator="op.a")["created"])
            row = kb.one("SELECT * FROM document_version WHERE document_version_id=?", (self.dv_a,))
            row.update(document_version_id="dv_x", version_class="ACCEPTED", semantic_sha256="7" * 64,
                       version_evidence_json=json.dumps([{"source": "PDF_FILENAME", "claim_ref": "x", "value": "am"}]))
            with self.assertRaises(KBError):
                kb.insert("document_version", row)


class IntakeG0Tests(AssetCase):
    def test_g0_path_and_file_refusals(self):
        """IDs: S03-G0-01, N18. Traversal, absolute paths, directories, empty and oversized files, a fixture root inside the KB and a drive-wide root are refused before any byte is read; each refusal is journaled."""
        self.put("ok.pdf", self.paper_a_pdf())
        os.makedirs(os.path.join(self.fixtures, "dir"))
        self.put("empty.pdf", b"")
        cases = [("../outside.pdf", {}, "PATH_OUTSIDE_ROOT"), (os.path.abspath(os.path.join(self.fixtures, "ok.pdf")), {}, "PATH_OUTSIDE_ROOT"),
                 ("dir", {}, "NOT_REGULAR_FILE"), ("empty.pdf", {}, "EMPTY"), ("ok.pdf", {"max_bytes": 100}, "TOO_LARGE"),
                 ("missing.pdf", {}, "NOT_REGULAR_FILE")]
        for rel, kw, code in cases:
            with self.subTest(rel=rel):
                with self.assertRaises(KBError) as ctx:
                    self.register(rel, **kw)
                self.assertEqual(ctx.exception.code, code)
        for root, code in ((os.path.join(self.root, "cas"), "FIXTURE_ROOT_INSIDE_KB"), (os.path.abspath(os.sep), "FIXTURE_ROOT_TOO_BROAD")):
            with self.subTest(root=root):
                with self.writer() as kb:
                    with self.assertRaises(KBError) as ctx:
                        AssetService(kb).register_asset(fixture_root=root, relative_path="x.pdf", document_version_id=self.dv_a, operator="op.a")
                self.assertEqual(ctx.exception.code, code)
        with self.reader() as kb:
            rejected = kb.one("SELECT count(*) n FROM import_event WHERE kind='MANUAL_FILE' AND outcome='REJECTED'")["n"]
            assets = kb.one("SELECT count(*) n FROM pdf_asset")["n"]
        self.assertEqual((rejected, assets), (len(cases) + 2, 0))

    def test_g0_link_and_foreign_path_refusals(self):
        """IDs: S03-G0-02, N18, S02-PATH-POSIX. A junction/symlink inside the fixture root, UNC/device roots and (on a simulated POSIX host) Windows paths are refused."""
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        Path(outside, "x.pdf").write_bytes(self.paper_a_pdf())
        link = os.path.join(self.fixtures, "linked")
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(outside, link)
        else:
            os.symlink(outside, link)
        with self.assertRaises(ValidationError) as ctx:
            self.register("linked/x.pdf")
        self.assertEqual(ctx.exception.code, "REPARSE_POINT")
        for root in ("\\\\server\\share\\pdfs", "\\\\?\\C:\\pdfs"):
            with self.writer() as kb:
                with self.assertRaises(ValidationError) as ctx:
                    AssetService(kb).register_asset(fixture_root=root, relative_path="x.pdf", document_version_id=self.dv_a, operator="op.a")
            self.assertEqual(ctx.exception.code, "UNSUPPORTED_PATH_FORM")
        with self.writer() as kb:
            with mock.patch.object(paths, "host_is_windows", return_value=False):
                with self.assertRaises(ValidationError) as ctx:
                    AssetService(kb).register_asset(fixture_root="D:fixtures", relative_path="x.pdf", document_version_id=self.dv_a, operator="op.a")
        self.assertEqual(ctx.exception.code, "WINDOWS_PATH_ON_POSIX")

    def test_n19_toctou_file_changed_during_import(self):
        """IDs: N19, S03-G0-TOCTOU. A file modified after validation (during or right after the streamed copy) is FILE_CHANGED_DURING_IMPORT; nothing is placed in CAS."""
        for hook in ("during_copy", "after_copy"):
            with self.subTest(hook=hook):
                name = self.put(f"race-{hook}.pdf", self.paper_a_pdf())

                done = []

                def grow(target):
                    if not done:       # one concurrent modification
                        done.append(1)
                        with open(target, "ab") as handle:
                            handle.write(b"% appended by a concurrent writer\n")
                        os.utime(target, ns=(1, 1))
                with self.assertRaises(ValidationError) as ctx:
                    self.register(name, faults={hook: grow})
                self.assertEqual(ctx.exception.code, "FILE_CHANGED_DURING_IMPORT")
        self.assertFalse(any(Path(self.root, "cas", "sha256").rglob("*.pdf")))
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
        self.assertEqual(list(Path(self.root, "staging").iterdir()), [])


def child_network_probe() -> str:
    """Run the sandbox child's own hook installer in an isolated interpreter and try an outbound connection (TEST-NET-1).

    The socket import lives here, not in the package (STATIC-01); the hook under test is pdf_child._install_hook.
    """
    import subprocess
    import sys
    code = "\n".join([
        "import importlib.util",
        f"spec = importlib.util.spec_from_file_location('pdf_child', {str(sandbox.CHILD)!r})",
        "child = importlib.util.module_from_spec(spec); spec.loader.exec_module(child)",
        "import socket",
        "assert child._limit_memory(256 * 1024 * 1024)",
        "child._install_hook()",
        "try:",
        "    socket.create_connection(('192.0.2.1', 9), timeout=1); print('CONNECTED')",
        "except PermissionError:",
        "    print('REFUSED')",
        "except OSError:",
        "    print('OS_ERROR')"])
    proc = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], capture_output=True, text=True, timeout=60)
    return proc.stdout.strip()


class G1Tests(AssetCase):
    def test_n10_structural_rejections(self):
        """IDs: N10, S03-G1-01. HTML challenge, missing header, truncated, malformed xref, encrypted and zero-page synthetic files get stable G1 codes; no asset, no CAS bytes."""
        good = self.paper_a_pdf()
        cases = {"html.pdf": (html_challenge(), "HTML_OR_CHALLENGE"), "plain.pdf": (b"just text " * 20, "NOT_PDF_HEADER"),
                 "trunc.pdf": (truncated(good), "TRUNCATED_NO_EOF"), "xref.pdf": (malformed_xref(good), "PARSER_REJECTED"),
                 "enc.pdf": (synthetic_pdf([["secret"]], encrypt=True), "ENCRYPTED"), "zero.pdf": (synthetic_pdf([]), "ZERO_PAGES"),
                 "count.pdf": (synthetic_pdf([["a"]], page_count_override=2), "PARSER_REJECTED")}
        for name, (data, code) in cases.items():
            with self.subTest(name=name):
                out = self.register(self.put(name, data))
                self.assertEqual((out["outcome"], out["reasons"], out["asset_registered"]), ("REJECTED", [code], False))
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 0)
            self.assertEqual(kb.one("SELECT count(*) n FROM asset_validation_event WHERE outcome='REJECTED'")["n"], len(cases))
        self.assertEqual([p for p in Path(self.root, "cas", "sha256").rglob("*") if p.is_file() and p.read_bytes().startswith(b"%PDF")], [])

    def test_parser_timeout_memory_cap_and_isolation(self):
        """IDs: S03-G1-SANDBOX, NET-GUARD-PARSER. The validator child is time-bounded (PARSER_TIMEOUT), memory-capped (fails closed), refuses network connections and any further file open."""
        name = self.put("t.pdf", self.paper_a_pdf())
        self.assertEqual(self.register(name, parser_timeout=0.001)["reasons"], ["PARSER_TIMEOUT"])
        path = Path(self.fixtures, name)
        self.assertEqual(sandbox.run_child(path, self_test="memory", memory=64 * 1024 * 1024)["result"]["outcome"], "LIMITED")
        self.assertEqual(child_network_probe(), "REFUSED")
        self.assertEqual(sandbox.run_child(path, self_test="open")["result"]["outcome"], "REFUSED")
        self.assertEqual(sandbox.validate_file(path, pypdf_site=os.path.join(self.tmp, "no-pypdf-here"))["reasons"], ["PARSER_SANDBOX_UNAVAILABLE"])

    def test_g2_duplicate_bytes_one_asset_and_cas_mismatch(self):
        """IDs: S03-G2-01, PA-1, S03-G2-CAS-MISMATCH. Identical bytes give one PdfAsset (second registration DUPLICATE_NOOP); staged bytes that differ from the intake hash are CAS_MISMATCH and the import is QUARANTINED."""
        data = self.paper_a_pdf()
        first = self.register(self.put("one.pdf", data))
        second = self.register(self.put("two.pdf", data))
        self.assertEqual((first["outcome"], second["outcome"], first["asset_sha256"]), ("REGISTERED", "DUPLICATE_NOOP", second["asset_sha256"]))
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 1)
            stored = kb.cas.read(first["asset_sha256"])
        self.assertEqual(stored, data)

        def tamper(part):
            Path(part).write_bytes(Path(part).read_bytes() + b"%tamper\n")
        name = self.put("three.pdf", synthetic_pdf([title_page(title=TITLE_B, authors=["Grace Synthetic"], doi="10.99999/asset.b")]))
        with self.assertRaises(IntegrityError) as ctx:
            self.register(name, dv=self.dv_b, faults={"before_cas": tamper})
        self.assertEqual(ctx.exception.code, "CAS_MISMATCH")
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT outcome FROM import_event ORDER BY rowid DESC LIMIT 1")["outcome"], "QUARANTINED")
            self.assertEqual(kb.one("SELECT count(*) n FROM pdf_asset")["n"], 1)
        with self.writer() as kb:
            with self.assertRaises(ImmutableRecordError):
                kb.execute("UPDATE pdf_asset SET byte_length = 1")


class G3Tests(AssetCase):
    def test_auto_match_requires_title_page_evidence(self):
        """IDs: S03-G3-01, E1, E3. Title-page DOI and first author give AUTO_EVIDENCE_MATCH; only derived evidence is stored, never page text."""
        out = self.register(self.put("a.pdf", self.paper_a_pdf()))
        self.assertEqual(out["identity_state"], "AUTO_EVIDENCE_MATCH")
        self.assertEqual({e["item"] for e in out["identity_evidence"]}, {"E1", "E2", "E3"})
        with self.reader() as kb:
            stored = json.dumps(kb.all("SELECT * FROM asset_validation_event")) + json.dumps(kb.all("SELECT * FROM paper_asset_link"))
        self.assertNotIn("Body.", stored)
        self.assertNotIn("pages_text", stored)

    def test_g3_negative_cases(self):
        """IDs: N07, N08, N09, S03-G3-02. Wrong title-page DOI (DOI_MISMATCH), DOI only in references, supplement words, multi-article PDF, other-version marker and no text: never AUTO_EVIDENCE_MATCH."""
        cases = {
            "wrong.pdf": (synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99998/someone.else")]),
                          "AMBIGUOUS", "DOI_MISMATCH"),
            "refs.pdf": (synthetic_pdf([title_page(title="Unrelated synthetic heading", authors=["Nobody"], references=["See 10.99999/asset.a"])]),
                         "UNVERIFIED", "DOI_ONLY_IN_REFERENCES"),
            "supp.pdf": (synthetic_pdf([title_page(title="Supplementary material for " + TITLE_A, authors=["Ada Lovelace-Synthetic"],
                                                   doi="10.99999/asset.a")]), "AMBIGUOUS", "SUPPLEMENT_SUSPECTED"),
            "multi.pdf": (synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99999/asset.a"),
                                         title_page(title=TITLE_B, authors=["Grace Synthetic"], doi="10.99999/asset.b")]), "AMBIGUOUS", "MULTI_ARTICLE_PDF"),
            "pre.pdf": (synthetic_pdf([title_page(title=TITLE_A, authors=["Ada Lovelace-Synthetic"], doi="10.99999/asset.a",
                                                  extra_first_lines=["Preprint - not peer reviewed"])]), "AMBIGUOUS", "OTHER_VERSION"),
            "blank.pdf": (synthetic_pdf([[" "]]), "AMBIGUOUS", "SCANNED_NO_TEXT"),
        }
        for name, (data, state, reason) in cases.items():
            with self.subTest(name=name):
                out = self.register(self.put(name, data))
                self.assertEqual(out["identity_state"], state)
                self.assertIn(reason, out["identity_reasons"])
        with self.reader() as kb:
            self.assertEqual(kb.one("SELECT count(*) n FROM paper_asset_link_effective WHERE effective_state='AUTO_EVIDENCE_MATCH'")["n"], 0)
            self.assertEqual(queries.review_queue(kb)["counts"]["asset_links"], len(cases))

    def test_n04_same_bytes_two_papers(self):
        """IDs: N04, PAL-1. The same bytes linked to a second paper make BOTH links AMBIGUOUS with a SAME_BYTES_MULTIPLE_PAPERS conflict; the DB refuses a second AUTO match; confirmation waits until the other link is rejected."""
        data = self.paper_a_pdf()
        first = self.register(self.put("a.pdf", data))
        second = self.register(self.put("a-copy.pdf", data), dv=self.dv_b)
        self.assertEqual(first["identity_state"], "AUTO_EVIDENCE_MATCH")
        self.assertEqual(second["identity_state"], "AMBIGUOUS")
        with self.writer() as kb:
            states = {r["paper_id"]: r["effective_state"] for r in kb.all("SELECT paper_id, effective_state FROM paper_asset_link_effective")}
            conflict = kb.one("SELECT kind FROM identity_conflict WHERE kind='SAME_BYTES_MULTIPLE_PAPERS'")
            link = kb.one("SELECT * FROM paper_asset_link WHERE paper_id=?", (self.paper_b,))
            link.update(link_id="pal_x", link_role="UNKNOWN_ROLE", identity_state="AUTO_EVIDENCE_MATCH", semantic_sha256="5" * 64)
            link.update(link_role="MAIN_TEXT", document_version_id=AssetService(kb).add_document_version(
                paper_id=self.paper_b, version_class="UNKNOWN", evidence=[], operator="op.a")["document_version_id"])
            with self.assertRaises(KBError):
                kb.insert("paper_asset_link", link)
            svc = AssetService(kb)
            with self.assertRaises(ValidationError) as ctx:
                svc.review_link(link_id=second["link_id"], decision="CONFIRM", operator="op.b", reason="looks right")
            self.assertEqual(ctx.exception.code, "SAME_BYTES_LINKED_ELSEWHERE")
            svc.review_link(link_id=second["link_id"], decision="REJECT", operator="op.b", reason="title page is paper A")
            confirmed = svc.review_link(link_id=first["link_id"], decision="CONFIRM", operator="op.b", reason="title page matches")
        self.assertEqual(set(states.values()), {"AMBIGUOUS"})
        self.assertEqual((conflict["kind"], confirmed["new_state"]), ("SAME_BYTES_MULTIPLE_PAPERS", "HUMAN_CONFIRMED"))

    def test_require_human_identity_policy_flag(self):
        """IDs: S03-G3-03. With require_human_identity the same strong evidence stays UNVERIFIED (HUMAN_CONFIRMATION_REQUIRED)."""
        out = self.register(self.put("a.pdf", self.paper_a_pdf()), require_human_identity=True)
        self.assertEqual((out["identity_state"], out["identity_reasons"]), ("UNVERIFIED", ["HUMAN_CONFIRMATION_REQUIRED"]))


if __name__ == "__main__":
    unittest.main()
