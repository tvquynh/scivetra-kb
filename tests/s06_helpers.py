"""S06 test helpers: synthetic papers, parser-backed (G1 v2) intake of generated PDFs, rights, and the pinned parser site.

The pinned parser is ``$SCIVETRA_PYPDF_SITE`` (a site directory or the official wheel), else the interpreter's installed pypdf,
else the official wheel shipped for review at ``../reference/dependencies/`` (or ``$SCIVETRA_PYPDF_WHEEL``). It must verify as
the reviewed official build (S06-F01) or the S06 tests FAIL (they are never skipped): see docs/PARSE_USER_GUIDE.md section 1.
"""
import os
import tempfile
import zipfile
from pathlib import Path

from tests.helpers import KBTest
from tests.s03_helpers import s03_item, write_export
from tests import s06_pdf_fixtures as F
from scivetra_kb.assets.service import AssetService
from scivetra_kb.library.service import LibraryService
from scivetra_kb.parse import runtime
from scivetra_kb.parse.service import ParseService

VERSION_EVIDENCE = [{"source": "CROSSREF_LICENSE_CONTENT_VERSION", "claim_ref": "synthetic:claim:vor", "value": "vor"}]
OPEN_RIGHTS = {"license_evidence": [{"source": "CROSSREF_LICENSE", "license_uri": "https://creativecommons.org/licenses/by/4.0/",
                                     "content_version": "vor", "claim_ref": "synthetic:license"}],
               "availability_evidence": [{"source": "OPENALEX_LOCATION", "is_oa": True, "version": "publishedVersion", "claim_ref": "synthetic:oa"}]}
RESTRICTED_RIGHTS = {"license_evidence": [{"source": "CROSSREF_LICENSE", "license_uri": "https://publisher.example/licenses/standard-terms",
                                           "content_version": "vor", "claim_ref": "synthetic:license:closed"}],
                     "terms_declarations": [{"terms": "institutional subscription terms", "evidence_ref": "synthetic:terms", "restrictive": True}]}


WHEEL_NAME = "pypdf-6.12.1-py3-none-any.whl"
REVIEW_WHEEL = Path(__file__).resolve().parents[2] / "reference" / "dependencies" / WHEEL_NAME
_EXTRACTED = {}


def official_wheel() -> str:
    """The official pypdf 6.12.1 wheel (its SHA-256 is checked against the recorded PyPI value by verify_site)."""
    env_site = os.environ.get(runtime.ENV_SITE, "")
    for candidate in (os.environ.get("SCIVETRA_PYPDF_WHEEL"), env_site if env_site.endswith(".whl") else None, str(REVIEW_WHEEL)):
        if candidate and Path(candidate).is_file():
            return runtime.verify_site(candidate)["site"]
    raise AssertionError("The official pypdf-6.12.1 wheel is required (SCIVETRA_PYPDF_WHEEL or reference/dependencies/); see PARSE_USER_GUIDE 1")


def extract_wheel(wheel: str, dest: Path) -> str:
    """Install-like extraction of a wheel's pypdf/ and dist-info into a site directory (no code is run)."""
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.startswith("pypdf") and not name.endswith("/"):
                target = dest / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    return str(dest)


def _review_wheel():
    for candidate in (os.environ.get("SCIVETRA_PYPDF_WHEEL"), str(REVIEW_WHEEL)):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def parser_site() -> str:
    """A verified site DIRECTORY (tests copy and mutate it); a wheel is extracted once per process into a temp folder."""
    # explicit env first; then the review wheel (or $SCIVETRA_PYPDF_WHEEL); only then an installed pypdf
    site = os.environ.get(runtime.ENV_SITE) or _review_wheel() or runtime.default_site()
    if site is None or site.endswith(".whl"):
        wheel = site or official_wheel()
        if wheel not in _EXTRACTED:
            _EXTRACTED[wheel] = extract_wheel(runtime.verify_site(wheel)["site"], Path(tempfile.mkdtemp(prefix="rkb-pypdf-site-")))
        site = _EXTRACTED[wheel]
    return runtime.verify_site(site)["site"]   # raises PARSER_* codes -> test error, never a skip


def title_lines(title, doi, body=()):
    return [title, "First Synthetic, Second Synthetic", "https://doi.org/" + doi] + list(body)


class S06Case(KBTest):
    N_PAPERS = 4

    def setUp(self):
        super().setUp()
        self.site = parser_site()
        self.fixtures = os.path.join(self.tmp, "fixture-root")
        os.makedirs(self.fixtures)
        items = [s03_item(f"cri_{i}", dois=[(f"10.99999/s06.{i}", f"10.99999/s06.{i}")], title=f"Synthetic parsing paper {i}",
                          author="First Synthetic") for i in range(self.N_PAPERS)]
        export = write_export(os.path.join(self.tmp, "rv"), items)
        with self.writer() as kb:
            LibraryService(kb).import_release(export, workspace_label="ws", operator="op.lib")
            self.papers = [kb.one("SELECT paper_id FROM project_paper_reference WHERE corpus_item_id=?", (f"cri_{i}",))["paper_id"]
                           for i in range(self.N_PAPERS)]
            svc = AssetService(kb)
            self.dvs = [svc.add_document_version(paper_id=p, version_class="PUBLISHED", evidence=VERSION_EVIDENCE, operator="op.lib")["document_version_id"]
                        for p in self.papers]

    def title_page(self, i, body=()):
        return F.text_page(title_lines(f"Synthetic parsing paper {i}", f"10.99999/s06.{i}", body))

    def put(self, name, data: bytes) -> str:
        Path(self.fixtures, name).write_bytes(data)
        return name

    def register(self, name, data: bytes, *, paper=0, validator="2", role="MAIN_TEXT", confirm=True, rights="OPEN", dv=None, **kw):
        self.put(name, data)
        with self.writer() as kb:
            svc = AssetService(kb, pdf_validator=validator, parser_site=self.site)
            dv = dv or self.dvs[paper]
            out = svc.register_asset(fixture_root=self.fixtures, relative_path=name, document_version_id=dv, operator="op.lib", role=role, **kw)
            if out["outcome"] != "REGISTERED":
                return out
            if confirm:
                svc.review_link(link_id=out["link_id"], decision="CONFIRM", operator="op.lib", reason="title page checked by a human")
            if rights == "OPEN":
                svc.assess_rights(asset_sha256=out["asset_sha256"], document_version_id=dv, operator="op.lib", evidence=OPEN_RIGHTS)
            elif rights == "RESTRICTED":
                svc.assess_rights(asset_sha256=out["asset_sha256"], document_version_id=dv, operator="op.lib", evidence=RESTRICTED_RIGHTS)
        return dict(out, document_version_id=dv)

    def parse(self, asset, link, purpose="RESEARCH_PARSE", *, limits=None, guard=True, faults=None, site=None, **kw):
        with self.writer() as kb:
            return ParseService(kb, parser_site=site or self.site, limits=limits, allow_language_level_network_guard=guard,
                                faults=faults).parse(asset_sha256=asset, link_id=link, purpose=purpose, operator="op.parse", **kw)

    def svc(self, kb):
        return ParseService(kb, parser_site=self.site, allow_language_level_network_guard=True)

    def count(self, table):
        with self.reader() as kb:
            return kb.one(f"SELECT count(*) n FROM {table}")["n"]

    def cas_names(self):
        root = Path(self.root, "cas", "sha256")
        return sorted(p.name for p in root.rglob("*") if p.is_file())
