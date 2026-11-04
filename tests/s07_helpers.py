"""S07 test helpers: synthetic TA metadata (offline Crossref fixtures through the S02 path), synthetic full texts parsed by
S06, frozen criteria with mock-only hints. Everything is synthetic: DOIs 10.99999/fte.*, generated PDFs, no network, no model."""
import copy
from pathlib import Path

from tests.fte_helpers import FTECase, RIGHTS, VERSION_EVIDENCE
from tests.helpers import cr_work, structured
from tests.pdf_fixtures import synthetic_pdf
from tests.s06_helpers import parser_site
from scivetra_kb.assets.service import AssetService
from scivetra_kb.metadata.transport import FixtureTransport
from scivetra_kb.parse.service import ParseService
from scivetra_kb.screening.service import ScreeningService

CRITERIA = {
    "schema": "rkb-screening-criteria/1", "rubric_version": "1",
    "exclude_reason_codes": ["WRONG_POPULATION", "WRONG_STUDY_DESIGN", "WRONG_OUTCOME"],
    "criteria": [
        {"id": "C1", "text": "Population: adults", "exclude_reason_code": "WRONG_POPULATION",
         "mock_hint": {"met_any": ["adult participants", "adults"], "not_met_any": ["children", "paediatric"]}},
        {"id": "C2", "text": "Design: randomized controlled trial", "exclude_reason_code": "WRONG_STUDY_DESIGN",
         "mock_hint": {"met_any": ["randomized controlled", "randomised controlled"], "not_met_any": ["non-randomized", "not randomized", "observational"]}},
        {"id": "C3", "text": "Outcome: blood pressure", "exclude_reason_code": "WRONG_OUTCOME",
         "mock_hint": {"met_any": ["blood pressure"], "not_met_any": []}},
    ],
}
INCLUDE_TEXT = "A randomized controlled trial in adult participants measuring blood pressure."
EXCLUDE_TEXT = "An observational cohort of children measuring blood pressure."
UNSURE_TEXT = "A study of a synthetic intervention."


def retraction(doi):
    return [{"DOI": doi + ".notice", "type": "retraction", "source": "publisher", "updated": {"date-parts": [[2025, 1, 2]]}}]


class S07Case(FTECase):
    def setUp(self):
        super().setUp()
        self.site = parser_site()

    def svc(self, kb):
        return ScreeningService(kb)

    def call(self, method, **kw):
        with self.writer() as kb:
            return getattr(ScreeningService(kb), method)(**kw)

    def read(self, method, **kw):
        with self.reader() as kb:
            return getattr(ScreeningService(kb), method)(**kw)

    def ta_world(self, abstracts, *, retracted=(), with_titles=True):
        """A screened release with one paper per abstract; Crossref claims (title + abstract) fetched offline through S02."""
        run = self.make_run(len(abstracts))
        ft = FixtureTransport()
        for i, abstract in enumerate(abstracts):
            doi = f"10.99999/fte.{i}"
            extra = {"updated-by": retraction(doi)} if i in retracted else {}
            self.route_ra(ft, doi)
            body = {"abstract": abstract} if abstract is not None else {}
            if with_titles:
                body["title"] = [f"Synthetic screening paper {i}"]
            self.add_crossref(ft, doi, cr_work(doi, **body, **extra))
        with self.writer() as kb:
            service = self.service(kb, ft)
            for i in range(len(abstracts)):
                assert service.fetch([structured(f"10.99999/fte.{i}")])[0]["status"] == "FOUND"
        return run

    def criteria_set(self, task="TA_SCREEN", criteria=None, supersedes=None, label="synthetic criteria"):
        return self.call("create_criteria_set", task_type=task, criteria=copy.deepcopy(criteria or CRITERIA), label=label, operator="op.owner",
                         supersedes=supersedes)["criteria_set_id"]

    def ta_scope(self, run, papers=None, classes=("METADATA_AND_ABSTRACT",), criteria_set=None, **kw):
        cs = criteria_set or self.criteria_set()
        papers = papers or [c["paper_id"] for c in run["candidates"]]
        return self.call("create_scope", task_type="TA_SCREEN", criteria_set_id=cs, operator="op.owner", label="synthetic TA scope",
                         content_classes=list(classes), paper_ids=papers, release_import_id=run["source"], **kw)["scope_id"]

    def ft_pdf(self, candidate, lines, *, rights="OPEN", confirm=True, role="MAIN_TEXT", purpose="RESEARCH_PARSE", name=None):
        """Register a classic synthetic PDF for the candidate's paper, confirm, assess rights, parse with the official parser."""
        name = name or f"{candidate['corpus_item_id']}_{abs(hash(tuple(lines))) % 10**8}.pdf"
        Path(self.fixtures, name).write_bytes(synthetic_pdf([lines]))
        with self.writer() as kb:
            svc = AssetService(kb)
            dv = svc.add_document_version(paper_id=candidate["paper_id"], version_class="PUBLISHED", evidence=VERSION_EVIDENCE["PUBLISHED"],
                                          operator="op.lib")["document_version_id"]
            out = svc.register_asset(fixture_root=self.fixtures, relative_path=name, document_version_id=dv, operator="op.lib", role=role)
            if confirm:
                svc.review_link(link_id=out["link_id"], decision="CONFIRM", operator="op.lib", reason="title page checked by a human")
            if rights == "OPEN":
                svc.assess_rights(asset_sha256=out["asset_sha256"], document_version_id=dv, operator="op.lib", evidence=RIGHTS["PUBLISHED"])
        with self.writer() as kb:
            parsed = ParseService(kb, parser_site=self.site, allow_language_level_network_guard=True).parse(
                asset_sha256=out["asset_sha256"], link_id=out["link_id"], purpose=purpose, operator="op.parse")
        return {"artifact_id": parsed.get("artifact_id"), "outcome": parsed["outcome"], "link_id": out["link_id"], "asset_sha256": out["asset_sha256"],
                "document_version_id": dv}

    def ft_scope(self, run, members, classes=("FULLTEXT_EXCERPT",), criteria_set=None, **kw):
        cs = criteria_set or self.criteria_set("FT_SCREEN")
        return self.call("create_scope", task_type="FT_SCREEN", criteria_set_id=cs, operator="op.owner", label="synthetic FT scope",
                         content_classes=list(classes), fte_run_id=run["run_id"], ft_members=members, **kw)["scope_id"]
