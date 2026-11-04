"""S04 bridge test helpers: native fixture copies, a KB-built synthetic Stage-11B export, a PBD WriteResults emulator and a
Python port of PBD ReadJobs (both pinned to PBD V5.2.11 @ 2901b046, CsvUtils.cs blob 3d4e607f). Synthetic data only."""
import hashlib
import json
import os
import shutil
from pathlib import Path

from tests.helpers import FIXTURES, KBTest
from tests.pdf_fixtures import synthetic_pdf, title_page
from tests.s03_helpers import s03_item, write_export
from scivetra_kb.bridge import pbd
from scivetra_kb.bridge.service import BridgeService
from scivetra_kb.bridge.sources import rv_canonical_json, rv_digest
from scivetra_kb.library.service import LibraryService

NATIVE_FT = FIXTURES / "rv_native_2_0_1_fulltext"


def copy_native_fulltext(tmp) -> tuple[str, str]:
    corpus, fulltext = os.path.join(tmp, "native-corpus"), os.path.join(tmp, "native-fulltext")
    shutil.copytree(NATIVE_FT / "corpus", corpus)
    shutil.copytree(NATIVE_FT / "fulltext", fulltext)
    return corpus, fulltext


# -- KB-built synthetic Stage-11B export (NOT native; same file shapes and rendering rules as Reprovane 2.0.1) ----------
def manual_row(item, release_id, release_sem, policy_sha, *, candidates=(), landing=(), reasons=("AUTH_REQUIRED",)):
    dois = item["identity"]["normalized_dois"]
    title = item["metadata"]["records"][0]["fields"]["title"][0]["value"]
    return {"candidates": [dict(c) for c in candidates], "corpus_item_id": item["corpus_item_id"], "dois": list(dois),
            "fulltext_policy_sha256": policy_sha, "landing_pages": list(landing), "openalex_work_ids": list(item["identity"]["openalex_work_ids"]),
            "publication_entity_id": item["publication_entity_id"], "reasons": list(reasons), "release_id": release_id,
            "release_manifest_sha256": release_sem, "status": "MANUAL_REQUIRED", "titles": [title]}


def write_fulltext_export(directory, items, *, release_id, release_sem, manual=None, acquired=(), run_id="fte_synthetic"):
    """``manual``: {corpus_item_id: {"candidates": [...], "landing": [...], "reasons": [...]}} for MANUAL_REQUIRED items;
    ``acquired``: corpus_item_ids that RV acquired automatically (never exported by the bridge)."""
    policy_sha = hashlib.sha256(b"synthetic fulltext policy").hexdigest()
    manual = manual or {}
    statuses, queue, attempts_all = [], [], []
    for item in items:
        cid = item["corpus_item_id"]
        is_acq = cid in acquired
        art = hashlib.sha256(("artifact:" + cid).encode()).hexdigest() if is_acq else None
        attempts = [{"candidate_id": "ftc_" + cid, "outcome": {"status": "ACQUIRED", "artifact_sha256": art}}] if is_acq else []
        status = {"attempts": attempts, "corpus_item_id": cid, "publication_entity_id": item["publication_entity_id"],
                  "reasons": [] if is_acq else list(manual.get(cid, {}).get("reasons", ["AUTH_REQUIRED"])),
                  "selected_artifact_sha256": art, "selected_candidate_id": "ftc_" + cid if is_acq else None,
                  "status": "ACQUIRED_AUTO" if is_acq else "MANUAL_REQUIRED"}
        status["status_sha256"] = rv_digest(status)
        statuses.append(status)
        attempts_all += attempts
    for cid in sorted(s["corpus_item_id"] for s in statuses if s["status"] != "ACQUIRED_AUTO"):
        item = next(i for i in items if i["corpus_item_id"] == cid)
        spec = manual.get(cid, {})
        queue.append(manual_row(item, release_id, release_sem, policy_sha, candidates=spec.get("candidates", ()), landing=spec.get("landing", ()),
                                reasons=spec.get("reasons", ("AUTH_REQUIRED",))))
    artifacts = {a["outcome"]["artifact_sha256"] for a in attempts_all}
    manifest = {"artifact_count": len(artifacts), "budget": {"artifacts": len(artifacts)}, "evidence_inventory": [], "failure_count": len(queue),
                "fulltext_policy": {"synthetic": True}, "fulltext_policy_sha256": policy_sha, "handoff_sha256": "0" * 64,
                "interpretation": "synthetic KB-built fixture", "items": statuses, "manual_queue": queue, "manual_queue_sha256": rv_digest(queue),
                "ordered_corpus_item_ids": [s["corpus_item_id"] for s in statuses], "provider_lookup_evidence": [], "release_id": release_id,
                "release_manifest_sha256": release_sem, "run_id": run_id, "schema": "fulltext-manifest/1"}
    from scivetra_kb.bridge.sources import _render
    os.makedirs(directory, exist_ok=True)
    for name, body in _render(manifest).items():
        Path(directory, name).write_bytes(body)
    return directory


def candidate(url, eligible=True, reason="ELIGIBLE", version="publishedVersion"):
    return {"candidate_id": "ftc_" + hashlib.sha256(url.encode()).hexdigest()[:12], "eligible": eligible, "reason": reason, "url": url, "version": version}


# -- PBD emulation (pinned V5.2.11) ---------------------------------------------------------------------------------
def _q(value):
    return '"' + (value or "").replace('"', '""') + '"'


def pbd_write_results(rows, *, bom=True, newline="\r\n") -> bytes:
    """Byte-faithful emulation of CsvUtils.WriteResults: header line, id and attempts unquoted, every other field quoted."""
    lines = [pbd.OUTPUT_HEADER]
    for r in rows:
        values = [str(r.get("id", ""))] + [_q(r.get(c, "")) for c in pbd.OUTPUT_COLUMNS[1:19]] + [str(r.get("attempts", 0)), _q(r.get("error", "")),
                                                                                                    _q(r.get("save_as", ""))]
        lines.append(",".join(values))
    return (b"\xef\xbb\xbf" if bom else b"") + (newline.join(lines) + newline).encode("utf-8")


def pbd_read_csv_rows(text):
    """Port of CsvUtils.ReadCsvRows (streaming RFC-4180-style parser)."""
    rows, row, field, quoted, i = [], [], [], False, 0
    while True:
        if i >= len(text):
            if quoted or field or row:
                row.append("".join(field))
                rows.append(row)
            return rows
        c = text[i]
        i += 1
        if quoted:
            if c == '"':
                if i < len(text) and text[i] == '"':
                    i += 1
                    field.append('"')
                else:
                    quoted = False
            else:
                field.append(c)
        elif c == '"' and not field:
            quoted = True
        elif c == ",":
            row.append("".join(field))
            field = []
        elif c in "\r\n":
            if c == "\r" and i < len(text) and text[i] == "\n":
                i += 1
            row.append("".join(field))
            field = []
            rows.append(row)
            row = []
        else:
            field.append(c)


def pbd_read_jobs(data: bytes) -> list[dict]:
    """Port of CsvUtils.ReadJobs field mapping and SelectBestUrl precedence (the parts the bridge relies on)."""
    text = data.decode("utf-8-sig")
    rows = pbd_read_csv_rows(text)
    header = rows[0]
    columns = {}
    for i, name in enumerate(header):
        columns.setdefault(name.strip().lstrip("\ufeff").lower(), i)
    jobs, job_id = [], 1
    for fields in rows[1:]:
        if not fields or all(not f.strip() for f in fields):
            continue

        def get(name):
            idx = columns.get(name)
            return fields[idx].strip() if idx is not None and idx < len(fields) else ""
        doi = get("doi") or get("doi_url")
        for prefix in ("https://doi.org/", "http://doi.org/"):
            if doi.lower().startswith(prefix):
                doi = doi[len(prefix):]
        if doi.lower().startswith("doi:"):
            doi = doi[4:].strip()
        job = {"id": job_id, "openalex_id": get("openalex_id") or get("record_id"), "doi": doi, "title": get("title"), "save_as": get("save_as"),
               "best_oa_pdf_url": get("best_oa_pdf_url"), "oa_url": get("pdf_url") or get("oa_url"),
               "landing": get("best_oa_landing_page") or get("landing_page")}
        is_http = lambda u: u.startswith(("http://", "https://")) and "://" in u and len(u.split("://", 1)[1]) > 0  # noqa: E731
        if is_http(job["best_oa_pdf_url"]):
            job["url"], job["url_source"] = job["best_oa_pdf_url"], "best_oa_pdf_url"
        elif is_http(job["oa_url"]):
            job["url"], job["url_source"] = job["oa_url"], "pdf_url" if get("pdf_url") else "oa_url"
        elif is_http(job["landing"]):
            job["url"], job["url_source"] = job["landing"], "best_oa_landing_page"
        elif doi:
            job["url"], job["url_source"] = "https://doi.org/" + doi, "doi"
        else:
            job["url"], job["url_source"] = "", ""
        job["status"] = "NoUrl" if not job["url"] else "Pending"
        jobs.append(job)
        job_id += 1
    return jobs


def result_row(row: dict, status: str, *, ordinal=None, record_token=None, save_as=None, deferred_reason="", file_path=None, **extra) -> dict:
    """One PBD output row for a manifest row (keys as in the manifest)."""
    out = {"id": ordinal if ordinal is not None else row["row_ordinal"], "openalex_id": record_token if record_token is not None else row["bridge_row_id"],
           "doi": row["export_doi"] or "", "title": "Synthetic", "status": status, "preflight": "no", "url_source": "doi",
           "selected_url": "https://doi.org/" + (row["export_doi"] or ""), "deferred_reason": deferred_reason,
           "file_path": file_path if file_path is not None else ("C:\\Users\\operator\\Downloads\\pbd\\" + row["save_as"] if status == "Completed" else ""),
           "attempts": 1, "save_as": save_as if save_as is not None else row["save_as"]}
    out.update(extra)
    return out


class BridgeCase(KBTest):
    """A KB with the NATIVE Reprovane 2.0.1 Stage-11 release imported (workspace 'ws') and its Stage-11B export available."""

    def setUp(self):
        super().setUp()
        self.corpus, self.fulltext = copy_native_fulltext(self.tmp)
        with self.writer() as kb:
            LibraryService(kb).import_release(self.corpus, workspace_label="ws", operator="op.a")
        self.out = os.path.join(self.tmp, "batch1")

    def export(self, **kw):
        args = dict(source_dir=self.fulltext, kind="rv-manual-queue", workspace_label="ws", out_dir=self.out, operator="op.b")
        args.update(kw)
        with self.writer() as kb:
            return BridgeService(kb).export_batch(**args)

    def manifest(self, out_dir=None):
        return json.loads(Path(out_dir or self.out, "bridge_manifest.json").read_text(encoding="utf-8"))

    def folder(self, out=None, out_dir=None):
        out = out or self.last
        return os.path.join(out_dir or self.out, out["output_folder"])

    def write_result(self, rows, name="results.csv", **kw):
        path = os.path.join(self.tmp, name)
        Path(path).write_bytes(pbd_write_results(rows, **kw))
        return path

    def run_import(self, result, *, batch=None, folder=None, **kw):
        with self.writer() as kb:
            return BridgeService(kb).import_results(batch_id=(batch or self.last)["batch_id"], result_csv=result,
                                                    pdf_folder=folder or self.folder(), operator="op.c", **kw)

    def status(self, batch=None):
        with self.reader() as kb:
            return BridgeService(kb).status((batch or self.last)["batch_id"])

    def pdf_for(self, row, *, title="Synthetic Work", doi=None):
        return synthetic_pdf([title_page(title=title, authors=["First Synthetic"], doi=doi or row["export_doi"] or "10.99999/none")])

    def put_pdf(self, row, data=None, folder=None):
        Path(folder or self.folder(), row["save_as"]).write_bytes(data if data is not None else self.pdf_for(row))


def synthetic_corpus(directory, n, *, release_id="crl_pilot", doi_for=None):
    """``n`` synthetic corpus items with DOIs 10.99999/pilot.<i> unless ``doi_for(i)`` returns a list of (original, normalized)."""
    items = []
    for i in range(n):
        dois = doi_for(i) if doi_for else [(f"10.99999/pilot.{i}", f"10.99999/pilot.{i}")]
        items.append(s03_item(f"cri_pilot_{i:04d}", dois=dois, title=f"Synthetic pilot study number {i}", author="First Synthetic"))
    write_export(directory, items, release_id=release_id)
    release_sem = json.loads(Path(directory, "manifest.json").read_text(encoding="utf-8"))["release_semantic_manifest_sha256"]
    return items, release_sem


__all__ = ["BridgeCase", "copy_native_fulltext", "write_fulltext_export", "candidate", "pbd_write_results", "pbd_read_jobs", "result_row",
           "synthetic_corpus", "rv_canonical_json"]
