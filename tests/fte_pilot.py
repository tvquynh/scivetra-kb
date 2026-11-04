"""S05 synthetic pilot: 100 TA-included candidates with a planned mix of evidence, acquisition and review situations.

Plan (candidate index -> situation):
  0-24  verified, human-confirmed published PDF; consensus FT_INCLUDE
  25-31 same evidence; consensus FT_EXCLUDE (WRONG_POPULATION / WRONG_STUDY_DESIGN)
  32-35 reviewer disagreement; human adjudication (32-33 include, 34-35 exclude WRONG_OUTCOME)
  36-37 FT_UNSURE + FT_INCLUDE; adjudicated FT_INCLUDE
  38-39 duplicate reports of 0 and 1; consensus FT_EXCLUDE DUPLICATE_REPORT_OF_INCLUDED_STUDY
  40    retraction integrity alert; humans exclude with RETRACTED
  41-42 disagreement left unresolved (PENDING NEEDS_ADJUDICATION)
  43    only one review (PENDING AWAITING_REVIEWS)
  44    consensus include, then newer confirmed bytes -> stale votes (PENDING STALE)
  45-49 accepted manuscript (allowed by protocol); consensus FT_INCLUDE
  50-54 only a submitted preprint (blocked by protocol); 50-52 FT_NOT_RETRIEVED after documented attempts; 53-54 pending
  55-59 confirmed PDF but restricted rights (blocked); pending
  60-64 verified PDF whose identity no human confirmed (cannot be evidence); pending
  65-79 no PDF; LOGIN_REQUIRED + BOT_CHECK attempts; FT_NOT_RETRIEVED by both reviewers
  80-84 no PDF; RETRY_QUEUED; pending
  85-89 no KB PDF; verified RV-Stage11B reference (synthetic Stage-11B export, trusted digest), never evidence-ready; pending
  90-94 no PDF, nothing done yet; pending
  95-99 RV-Stage11B selected artifact verified against the export, then the same bytes imported into the KB, confirmed by a human
        and rights-assessed; attached as KB_ASSET evidence; consensus FT_INCLUDE (the external reference alone never counts)
Expected: 100 = 39 FT_INCLUDE + 12 FT_EXCLUDE + 18 FT_NOT_RETRIEVED + 31 FT_PENDING. Synthetic data only; no Reprovane corpus is produced.
"""
import copy
import hashlib
import os
from pathlib import Path

from tests.fte_helpers import PROTOCOL, RESTRICTED, RIGHTS, ROSTER, VERSION_EVIDENCE, write_stage11b_export, write_ta_release
from tests.pdf_fixtures import synthetic_pdf, title_page
from scivetra_kb.assets.service import AssetService
from scivetra_kb.core.db import init_kb, open_writer
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
from scivetra_kb.eligibility.handoff import export_run
from scivetra_kb.eligibility.service import FTEService
from scivetra_kb.library.service import LibraryService

EXPECTED = {"ft_include": 39, "ft_exclude": 12, "ft_not_retrieved": 18, "ft_pending": 31}


def run_fte_pilot(tmp: str) -> dict:
    clock = FixedClock()
    ids = SequentialIdProvider(clock)
    root = os.path.join(tmp, "kb")
    init_kb(root, clock=clock, ids=ids)
    fixtures = os.path.join(tmp, "fixtures")
    os.makedirs(fixtures)
    export = os.path.join(tmp, "release")
    items, sem, verify = write_ta_release(export, 100, release_id="crl_fte_pilot")
    with open_writer(root, clock=clock, ids=ids) as kb:
        LibraryService(kb).import_release(export, workspace_label="pilot", operator="pilot.lib")
        source = kb.one("SELECT release_import_id FROM reprovane_release_import")["release_import_id"]
        svc = FTEService(kb)
        pid = svc.create_protocol(copy.deepcopy(PROTOCOL), operator="pilot.fte")["protocol_id"]
        run = svc.create_run(source_id=source, export_dir=export, expected_manifest_sha256=sem, rv_verify_json=verify, protocol_id=pid,
                             roster=copy.deepcopy(ROSTER), label="S05 synthetic 100-candidate pilot", operator="pilot.fte")
        c = svc.candidates(run["run_id"])
        assets = AssetService(kb)

        def pdf(i, version="PUBLISHED", confirm=True, rights="OPEN", variant=""):
            name = f"p{i}_{version}_{variant}.pdf"
            Path(fixtures, name).write_bytes(synthetic_pdf([title_page(title=f"Synthetic full-text study number {i}", authors=["First Synthetic"],
                                                                        doi=f"10.99999/fte.{i}", body=[f"Synthetic pilot body {variant}"])]))
            dv = assets.add_document_version(paper_id=c[i]["paper_id"], version_class=version, evidence=VERSION_EVIDENCE[version],
                                             operator="pilot.lib")["document_version_id"]
            out = assets.register_asset(fixture_root=fixtures, relative_path=name, document_version_id=dv, operator="pilot.lib")
            if confirm:
                assets.review_link(link_id=out["link_id"], decision="CONFIRM", operator="pilot.human", reason="title page and DOI checked")
            if rights == "OPEN":
                assets.assess_rights(asset_sha256=out["asset_sha256"], document_version_id=dv, operator="pilot.lib", evidence=RIGHTS.get(version, RIGHTS["PUBLISHED"]))
            elif rights == "RESTRICTED":
                assets.assess_rights(asset_sha256=out["asset_sha256"], document_version_id=dv, operator="pilot.lib", evidence=RESTRICTED)
            return out["link_id"]

        def attach(i, **kw):
            return svc.attach_kb_asset(candidate_id=c[i]["candidate_id"], link_id=pdf(i, **kw), operator="pilot.fte")

        def vote(i, who, outcome, reasons=(), **kw):
            return svc.decide(candidate_id=c[i]["candidate_id"], reviewer=who, outcome=outcome, reasons=list(reasons), **kw)

        def both(i, outcome, reasons=(), **kw):
            return vote(i, "rev.a", outcome, reasons, **kw), vote(i, "rev.b", outcome, reasons, **kw)

        def tries(i, statuses):
            for k, status in enumerate(statuses):
                svc.record_attempt(candidate_id=c[i]["candidate_id"], status=status, source_description="publisher landing page (synthetic)",
                                   attempted_on=f"2026-10-{10 + k:02d}", retry_after="2026-11-20" if status == "RETRY_QUEUED" else None,
                                   operator="pilot.acq")

        for i in range(0, 45):
            attach(i)
        for i in range(0, 25):
            both(i, "FT_INCLUDE")
        for i in range(25, 32):
            both(i, "FT_EXCLUDE", ["WRONG_POPULATION" if i % 2 else "WRONG_STUDY_DESIGN"])
        for i in range(32, 36):
            vote(i, "rev.a", "FT_INCLUDE")
            vote(i, "rev.b", "FT_EXCLUDE", ["WRONG_OUTCOME"])
            final = ("FT_INCLUDE", []) if i < 34 else ("FT_EXCLUDE", ["WRONG_OUTCOME"])
            svc.adjudicate(candidate_id=c[i]["candidate_id"], adjudicator="adj.c", outcome=final[0], reasons=final[1], note="adjudicated against C1/C2")
        for i in (36, 37):
            vote(i, "rev.a", "FT_UNSURE", note="population description incomplete")
            vote(i, "rev.b", "FT_INCLUDE")
            svc.adjudicate(candidate_id=c[i]["candidate_id"], adjudicator="adj.c", outcome="FT_INCLUDE", note="supplementary table settles C1")
        for i, target in ((38, 0), (39, 1)):
            both(i, "FT_EXCLUDE", ["DUPLICATE_REPORT_OF_INCLUDED_STUDY"], related_corpus_item_id=c[target]["corpus_item_id"])
        key = kb.one("SELECT value_normalized v FROM identity_assertion WHERE paper_id=? AND namespace='doi'", (c[40]["paper_id"],))["v"]
        kb.insert("integrity_event", {"event_id": "ige_pilot_40", "subject_paper_id": c[40]["paper_id"], "subject_namespace": "doi", "subject_value": key,
                                      "event_type": "RETRACTION", "event_subtype": "retraction", "notice_doi": None, "reported_by": "OPERATOR",
                                      "record_id": None, "first_snapshot_id": None, "source_field_path": None,
                                      "operator_evidence_ref": "synthetic notice", "event_date_json": None, "payload_json": "{}",
                                      "created_at_utc": kb.now(), "semantic_sha256": "0" * 63 + "1"})
        both(40, "FT_EXCLUDE", ["RETRACTED"])
        for i in (41, 42):
            vote(i, "rev.a", "FT_INCLUDE")
            vote(i, "rev.b", "FT_EXCLUDE", ["WRONG_POPULATION"])
        vote(43, "rev.a", "FT_INCLUDE")
        both(44, "FT_INCLUDE")
        attach(44, variant="corrected-bytes")
        for i in range(45, 50):
            attach(i, version="ACCEPTED")
            both(i, "FT_INCLUDE")
        for i in range(50, 55):
            attach(i, version="SUBMITTED", rights="NONE")
            if i < 53:
                tries(i, ["LOGIN_REQUIRED", "NO_ACCESS"])
                both(i, "FT_NOT_RETRIEVED", ["NOT_AVAILABLE_FROM_AUTHORS"])
        for i in range(55, 60):
            attach(i, rights="RESTRICTED")
        for i in range(60, 65):
            pdf(i, confirm=False)
        for i in range(65, 80):
            tries(i, ["LOGIN_REQUIRED", "BOT_CHECK"])
            svc.attach_none(candidate_id=c[i]["candidate_id"], operator="pilot.fte")
            both(i, "FT_NOT_RETRIEVED", ["NO_ACCESS_AFTER_DOCUMENTED_ATTEMPTS"])
        for i in range(80, 85):
            tries(i, ["RETRY_QUEUED"])
        kb_links = {i: pdf(i) for i in range(95, 100)}
        selected = {c[i]["corpus_item_id"]: kb.one("SELECT asset_sha256 FROM paper_asset_link WHERE link_id=?", (kb_links[i],))["asset_sha256"]
                    for i in range(95, 100)}
        selected.update({c[i]["corpus_item_id"]: hashlib.sha256(f"synthetic rv artifact {i}".encode()).hexdigest() for i in range(85, 90)})
        stage11b = os.path.join(tmp, "stage11b")
        stage11b_digest = write_stage11b_export(stage11b, items, release_id="crl_fte_pilot", release_sem=sem, acquired=selected)
        external = []
        for i in list(range(85, 90)) + list(range(95, 100)):
            external.append(svc.attach_external(candidate_id=c[i]["candidate_id"], operator="pilot.fte", artifact={
                "fulltext_export_dir": stage11b, "expected_manifest_sha256": stage11b_digest,
                "expected_artifact_sha256": selected[c[i]["corpus_item_id"]]}))
        for i in range(95, 100):
            svc.attach_kb_asset(candidate_id=c[i]["candidate_id"], link_id=kb_links[i], operator="pilot.fte")
            both(i, "FT_INCLUDE")
        summary = svc.summary(run["run_id"])
        queue = svc.queue(run["run_id"])
        categories = {}
        for entry in queue:
            for f in {f["category"] for f in entry["flags"]}:
                categories[f] = categories.get(f, 0) + 1
        refused = {}
        for i, link_kind in ((60, "unconfirmed"),):
            link = kb.one("SELECT link_id FROM paper_asset_link WHERE paper_id=?", (c[i]["paper_id"],))["link_id"]
            try:
                svc.attach_kb_asset(candidate_id=c[i]["candidate_id"], link_id=link, operator="pilot.fte")
            except Exception as exc:  # noqa: BLE001 - recorded for the pilot report
                refused[link_kind] = getattr(exc, "code", type(exc).__name__)
        exported = export_run(kb, run["run_id"], os.path.join(tmp, "export"), operator="pilot.fte")
        try:
            svc.lock(run["run_id"], operator="pilot.fte")
            lock_refusal = None
        except Exception as exc:  # noqa: BLE001
            lock_refusal = getattr(exc, "code", type(exc).__name__)
    return {"run_id": run["run_id"], "summary": summary, "queue_categories": dict(sorted(categories.items())), "refused_attach": refused,
            "external_references": {"attached_verified": len(external), "evidence_ready": sum(bool(e["evidence_ready"]) for e in external),
                                    "levels": sorted({e["verification_level"] for e in external}),
                                    "with_same_human_confirmed_kb_bytes": sum(bool(e["kb_asset_links_with_same_bytes"]) for e in external)},
            "lock_refusal": lock_refusal, "export": exported, "export_dir": os.path.join(tmp, "export", exported["folder_name"])}


__all__ = ["run_fte_pilot", "EXPECTED"]
