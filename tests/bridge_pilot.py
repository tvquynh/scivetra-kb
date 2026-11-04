"""S04 synthetic pilot: 100 candidate items, ~46 simulated PDFs, injected missing / duplicate / invalid / late / changed /
access-barrier / no-url / unknown-status cases, two PBD result rounds plus an exact repeat. Synthetic data only.

Used by tests/test_bridge_pilot.py (assertions) and tools/s04_pilot.py (timing, memory and determinism evidence).
"""
import json
import os
from pathlib import Path

from tests.bridge_helpers import candidate, pbd_write_results, result_row, synthetic_corpus, write_fulltext_export
from tests.pdf_fixtures import html_challenge, synthetic_pdf, title_page, truncated
from scivetra_kb.bridge.service import BridgeService
from scivetra_kb.core.db import init_kb, open_reader, open_writer
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider
from scivetra_kb.library.service import LibraryService


def _dois(i):
    if i % 25 == 0:
        return []
    if i in (7, 57):
        return [(f"10.99999/pilot.{i}", f"10.99999/pilot.{i}"), (f"10.99999/pilot.{i}b", f"10.99999/pilot.{i}b")]
    return [(f"10.99999/pilot.{i}", f"10.99999/pilot.{i}")]


def _pdf(title, doi, variant=""):
    return synthetic_pdf([title_page(title=title, authors=["First Synthetic"], doi=doi or "10.99999/none", body=["Synthetic body " + variant])])


def run_pilot(tmp: str) -> dict:
    clock = FixedClock()
    ids = SequentialIdProvider(clock)
    root = os.path.join(tmp, "kb")
    init_kb(root, clock=clock, ids=ids)
    corpus_dir = os.path.join(tmp, "corpus")
    items, release_sem = synthetic_corpus(corpus_dir, 100, doi_for=_dois)
    manual = {}
    acquired = [it["corpus_item_id"] for i, it in enumerate(items) if i % 10 == 9]
    for i, it in enumerate(items):
        cid = it["corpus_item_id"]
        if i % 3 == 0:
            manual[cid] = {"candidates": [candidate(f"https://papers.example.org/pilot/{i}.pdf")], "reasons": ["AUTH_REQUIRED"]}
        elif i % 3 == 1:
            manual[cid] = {"candidates": [candidate(f"https://papers.example.org/landing/{i}", eligible=False, reason="NO_DIRECT_PDF")],
                           "landing": [f"https://papers.example.org/landing/{i}"], "reasons": ["NO_DIRECT_PDF"]}
        else:
            manual[cid] = {"candidates": [candidate(f"https://preprints.example.org/{i}.pdf", eligible=False, reason="ONLY_SUBMITTED_VERSION"),
                                          candidate(f"https://cdn.example.org/{i}.pdf?Expires=REDACTED&Signature=REDACTED")],
                           "reasons": ["ONLY_SUBMITTED_VERSION"]}
    fulltext_dir = write_fulltext_export(os.path.join(tmp, "fulltext"), items, release_id="crl_pilot", release_sem=release_sem, manual=manual,
                                         acquired=acquired)
    with open_writer(root, clock=clock, ids=ids) as kb:
        LibraryService(kb).import_release(corpus_dir, workspace_label="pilot", operator="pilot.op")
        out_dir = os.path.join(tmp, "batch")
        exported = BridgeService(kb).export_batch(source_dir=fulltext_dir, kind="rv-manual-queue", workspace_label="pilot", out_dir=out_dir,
                                                  operator="pilot.op")
    manifest = json.loads(Path(out_dir, "bridge_manifest.json").read_text(encoding="utf-8"))
    rows = manifest["rows"]
    folder = Path(out_dir, exported["output_folder"])
    title = {r["bridge_row_id"]: r["titles"][0] for r in rows}
    pdfs_written = 0

    def put(row, data):
        nonlocal pdfs_written
        (folder / row["save_as"]).write_bytes(data)
        pdfs_written += 1

    first = []
    for k, row in enumerate(rows):
        good = _pdf(title[row["bridge_row_id"]], row["export_doi"])
        if k < 33:
            put(row, good)
            first.append(result_row(row, "Completed"))
        elif k < 35:
            first.append(result_row(row, "Completed"))                       # file missing
        elif k < 38:
            put(row, [html_challenge(), truncated(good), b"plain text, not a PDF " * 10][k - 35])
            first.append(result_row(row, "Completed"))
        elif k < 40:
            put(row, good)
            first += [result_row(row, "Completed"), result_row(row, "Completed")]    # duplicated output rows
        elif k < 48:
            first.append(result_row(row, "LoginRequired" if k % 2 == 0 else "InstitutionalAccessRequired"))
        elif k < 54:
            first.append(result_row(row, "Failed", deferred_reason="Permanent page-not-found response" if k % 2 == 0 else "HTTP 500"))
        elif k < 59:
            first.append(result_row(row, "NoUrl"))
        elif k < 65:
            if k < 61:
                put(row, good)                                                      # late background file
            first.append(result_row(row, "Deferred", deferred_reason="Native download idle; continuing in background" if k < 61 else "Paywall"))
        elif k < 69:
            first.append(result_row(row, "BotCheck"))
        elif k < 72:
            first.append(result_row(row, "Skipped"))
        elif k < 74:
            first.append(result_row(row, "Throttled"))                              # unknown to profile pbd-csv-v1
        # k >= 74: absent from the result file (PBD stopped early)
    first.append(result_row(rows[0], "Completed", save_as="b" + "f" * 23 + ".pdf", record_token="b" + "f" * 23))   # unexpected row
    (folder / "notes.txt").write_text("operator note", encoding="utf-8")                                      # extra file
    first_path = Path(tmp, "pbd_results_round1.csv")
    first_path.write_bytes(pbd_write_results(first))
    results = {}
    with open_writer(root, clock=clock, ids=ids) as kb:
        results["round1"] = BridgeService(kb).import_results(batch_id=exported["batch_id"], result_csv=str(first_path), pdf_folder=str(folder),
                                                             operator="pilot.op")
    second = []
    for k, row in enumerate(rows):
        good = _pdf(title[row["bridge_row_id"]], row["export_doi"])
        if k < 2:
            os.remove(folder / row["save_as"])
            put(row, _pdf(title[row["bridge_row_id"]], row["export_doi"], variant="re-downloaded"))   # changed bytes, same row
            second.append(result_row(row, "Completed", attempts=2))
        elif k < 33:
            second.append(result_row(row, "Completed"))                                               # unchanged
        elif 40 <= k < 44:
            put(row, good)                                                                            # access resolved by the operator
            second.append(result_row(row, "Completed", attempts=2))
        elif 59 <= k < 61:
            second.append(result_row(row, "Completed", attempts=2))                                   # late file now Completed
    second_path = Path(tmp, "pbd_results_round2.csv")
    second_path.write_bytes(pbd_write_results(second))
    with open_writer(root, clock=clock, ids=ids) as kb:
        results["round2"] = BridgeService(kb).import_results(batch_id=exported["batch_id"], result_csv=str(second_path), pdf_folder=str(folder),
                                                             operator="pilot.op")
        results["repeat"] = BridgeService(kb).import_results(batch_id=exported["batch_id"], result_csv=str(second_path), pdf_folder=str(folder),
                                                             operator="pilot.op")
    with open_reader(root, clock=clock, ids=ids) as kb:
        status = BridgeService(kb).status(exported["batch_id"])
        tasks = {}
        for t in kb.all("SELECT trigger FROM bridge_review_task"):
            tasks[t["trigger"]] = tasks.get(t["trigger"], 0) + 1
        links = {}
        for link in kb.all("SELECT effective_state s FROM paper_asset_link_effective"):
            links[link["s"]] = links.get(link["s"], 0) + 1
        rights = {}
        for a in kb.all("SELECT rights_class c FROM rights_assessment"):
            rights[a["c"]] = rights.get(a["c"], 0) + 1
        counts = {t: kb.one(f"SELECT count(*) n FROM {t}")["n"] for t in ("pdf_asset", "bridge_attempt", "bridge_file_event", "bridge_result_import")}
    return {"exported": {k: exported[k] for k in ("row_count", "manifest_bytes_sha256", "rows_semantic_sha256", "input_csv_bytes_sha256",
                                                  "doi_cells_written", "flag_counts", "excluded_already_acquired")},
            "source_items": len(items), "pdfs_written": pdfs_written,
            "imports": {k: (v[0], v[1]["outcome"], v[1].get("counts")) for k, v in results.items()},
            "final_status_counts": status["counts"], "open_rows": len(status["open_rows"]), "review_tasks": dict(sorted(tasks.items())),
            "link_states": links, "rights_classes": rights, "counts": counts,
            "review_load": {"identity_reviews_needed": links.get("UNVERIFIED", 0) + links.get("AMBIGUOUS", 0),
                            "bridge_review_tasks": sum(tasks.values())}}
