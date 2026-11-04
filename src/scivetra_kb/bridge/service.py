"""S04 Download Bridge v1 (S01 v03 06): Reprovane portable export -> PBD input CSV; PBD result CSV + files -> KB.

A FILE-EXCHANGE bridge only. It never downloads, never opens a Reprovane database, never writes to the source export,
the operator's result CSV or the PBD output folder, and never decides paper identity, full-text eligibility or rights:
* ``Completed`` only starts KB verification (S04-D03). Files enter through S03 ``AssetService.register_asset`` (G0-G3)
  with ``require_human_identity=True``, so a bridge file is at most UNVERIFIED/AMBIGUOUS until a human confirms it.
* ``Failed`` is a failed attempt, ``LoginRequired`` an observed access barrier (S04-D04): provenance, not facts.
* Every attempt is append-only; an unchanged result file is a DUPLICATE_NOOP; new bytes for a row keep both assets and
  open a review (S04-D05). Every write is keyed, so re-running an interrupted import resumes it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .. import __version__
from ..assets.service import AssetService
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, KBError, PolicyRefusal, ValidationError
from ..core.paths import _protected_lexical, check_no_links_on_path, is_reparse_or_link, refuse_unsafe_path_text, safe_data_root, safe_input_file
from ..library.common import json_text, operator_label
from ..library.service import LibraryService
from . import pbd, policy, sources

MANIFEST_SCHEMA = "rkb-bridge-manifest/1"
MAX_FILE_BYTES = 104_857_600
KINDS = {"rv-manual-queue": "RV_MANUAL_QUEUE", "rv-corpus": "RV_CORPUS"}
OPEN_REVIEW_STATUSES = ("NEEDS_HUMAN_ACCESS", "NEEDS_HUMAN_ACTION")
_ABS_PATH = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\|(?<![A-Za-z0-9:/.])/(?:home|users|mnt|tmp|var|etc|root)/)[^\s\"']*", re.I)
_URLISH = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"']+", re.I)
_URL_SOURCES = ("best_oa_pdf_url", "oa_url", "pdf_url", "best_oa_landing_page", "doi", "url", "")


_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def guard_path_text(text, what: str) -> str:
    """Bridge path arguments (stricter than the S02/S03 core): explicit, absolute-or-plain paths only. Refuses NUL, UNC/device
    and foreign-host forms (core), drive-relative ``C:x``, ``..`` traversal, any further ``:`` (alternate data streams) and
    reserved device names / trailing dot or space in any component. The value itself is never echoed."""
    if not isinstance(text, str) or not text or "\x00" in text or len(text) > 1024:
        raise ValidationError(f"{what}: invalid path", code="PATH_INVALID")
    # Drive-relative forms are recognised lexically BEFORE the host-specific core check, so 'C:x' gets the same S04 code on
    # every OS (on POSIX the core would otherwise answer WINDOWS_PATH_ON_POSIX first). Both refuse; only the code differs.
    drive = re.match(r"^[A-Za-z]:", text)
    if drive and not re.match(r"^[A-Za-z]:[\\/]", text):
        raise ValidationError(f"{what}: drive-relative paths are refused; give a full path", code="DRIVE_RELATIVE_PATH_REFUSED")
    # The protected production root is refused on the raw text and on its cwd-absolute form (os.path.abspath is lexical), before any
    # stat/open, for every bridge argument, including input files the core would otherwise stat first.
    if _protected_lexical(text) or _protected_lexical(os.path.abspath(text)):
        raise PolicyRefusal(f"{what}: protected production data root (OAG-2 approval required)", code="PROTECTED_DATA_ROOT")
    refuse_unsafe_path_text(text)
    rest = text[2:] if drive else text
    if ":" in rest:
        raise ValidationError(f"{what}: ':' (alternate data stream) refused", code="ADS_OR_COLON_IN_PATH")
    for part in re.split(r"[\\/]+", rest):
        if part == "..":
            raise ValidationError(f"{what}: '..' traversal refused", code="TRAVERSAL_REFUSED")
        if part in ("", "."):
            continue
        if part.split(".")[0].upper().rstrip(" ") in _RESERVED_NAMES or part.endswith((" ", ".")):
            raise ValidationError(f"{what}: reserved device name or trailing dot/space refused", code="RESERVED_NAME_REFUSED")
    return text


def path_hash(path: Path) -> str:
    return sha256_bytes(os.path.normcase(os.path.realpath(path)).encode("utf-8"))


def bridge_row_id(batch_id: str, source_item_key: str) -> str:
    return "b" + hashlib.sha256((batch_id + "\n" + source_item_key).encode("utf-8")).hexdigest()[:23]


def batch_short(batch_id: str) -> str:
    return hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:8]


def sanitize_text(value: str, limit: int = 200) -> str:
    """Bounded provenance text: control chars removed, URLs reduced to scheme+host, local paths and long tokens dropped."""
    text = "".join(c if 32 <= ord(c) < 127 or ord(c) > 159 else " " for c in (value or ""))
    text = _URLISH.sub(lambda m: _url_host(m.group(0)), text)
    text = _ABS_PATH.sub("<path>", text)
    text = re.sub(r"[A-Za-z0-9_\-]{40,}", "<token>", text)
    return " ".join(text.split())[:limit]


def _url_host(url: str) -> str:
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url.strip())
        return f"{parts.scheme}://{parts.hostname}" if parts.scheme and parts.hostname else "<url>"
    except ValueError:
        return "<url>"


class BridgeService:
    def __init__(self, kb, *, pypdf_site: str | None = None):
        self.kb = kb
        self.library = LibraryService(kb)
        self.assets = AssetService(kb, pypdf_site=pypdf_site)

    # -- shared ------------------------------------------------------------------------------
    def _require_schema(self):
        if self.kb.schema["schema_version"] < 3:
            raise ValidationError("The download bridge needs schema 3; run 'rkb upgrade --backup-target'", code="KB_UPGRADE_REQUIRED")

    def batch(self, batch_id: str) -> dict:
        row = self.kb.one("SELECT * FROM bridge_batch WHERE batch_id=?", (batch_id,)) if isinstance(batch_id, str) else None
        if not row:
            raise ValidationError("Unknown bridge batch", code="BATCH_UNKNOWN")
        return row

    def row_status(self, bridge_row_id_: str) -> str | None:
        row = self.kb.one("SELECT new_status s FROM bridge_row_event WHERE bridge_row_id=? ORDER BY rowid DESC LIMIT 1", (bridge_row_id_,))
        return row["s"] if row else None

    def batch_state(self, batch_id: str) -> str | None:
        row = self.kb.one("SELECT new_state s FROM bridge_batch_event WHERE batch_id=? ORDER BY rowid DESC LIMIT 1", (batch_id,))
        return row["s"] if row else None

    def _row_event(self, row_id: str, status: str, cause_kind: str, cause_ref: str) -> bool:
        if self.row_status(row_id) == status and cause_kind != "BATCH_EXPORTED":
            return False
        content = {"row": row_id, "status": status, "cause": [cause_kind, cause_ref]}
        _, created = self.kb.insert_unique("bridge_row_event", dict(event_id=self.kb.ids.new("bre"), bridge_row_id=row_id, new_status=status,
                                                                    cause_kind=cause_kind, cause_ref=cause_ref, recorded_at_utc=self.kb.now(),
                                                                    semantic_sha256=semantic_sha256(content)))
        return created

    def _review(self, batch_id, row_id, trigger, context) -> None:
        content = {"batch": batch_id, "row": row_id, "trigger": trigger, "context": context}
        self.kb.insert_unique("bridge_review_task", dict(task_id=self.kb.ids.new("brt"), batch_id=batch_id, bridge_row_id=row_id, trigger=trigger,
                                                         context_json=json_text(context), created_at_utc=self.kb.now(),
                                                         semantic_sha256=semantic_sha256(content)))

    # -- export --------------------------------------------------------------------------------
    def _paper_dois(self, paper_id: str) -> tuple[list, list]:
        rows = self.kb.all("SELECT value_normalized v, effective_status s FROM identity_assertion_effective WHERE paper_id=? AND namespace='doi' "
                           "AND value_normalized IS NOT NULL", (paper_id,))
        return sorted({r["v"] for r in rows if r["s"] == "ACTIVE"}), sorted({r["v"] for r in rows if r["s"] == "CONFLICTING"})

    def _source_rows(self, kind, source_dir, expected, fulltext_dir):
        if kind == "RV_MANUAL_QUEUE":
            ft = sources.read_fulltext_export(source_dir, expected_manifest_sha256=expected)
            m = ft["manifest"]
            items = [{"corpus_item_id": r["corpus_item_id"], "publication_entity_id": r["publication_entity_id"], "titles": r["titles"],
                      "dois": r["dois"], "openalex_work_ids": r["openalex_work_ids"], "landing_pages": r["landing_pages"],
                      "candidates": r["candidates"], "rv_status": r["status"], "rv_reasons": r["reasons"]} for r in m["manual_queue"]]
            return ft, {"release_id": m["release_id"], "release_manifest_sha256": m["release_manifest_sha256"], "fulltext_run_id": m["run_id"],
                        "rv_digest": ft["rv_digest"]}, items, []
        corpus = sources.read_corpus_export(source_dir, expected_manifest_sha256=expected)
        manifest = corpus["manifest"]
        excluded = []
        acquired = set()
        if fulltext_dir:
            ft = sources.read_fulltext_export(fulltext_dir)
            if ft["manifest"]["release_manifest_sha256"] != manifest["release_semantic_manifest_sha256"]:
                raise ValidationError("Full-text export belongs to another release", code="FULLTEXT_RELEASE_MISMATCH")
            acquired = {s["corpus_item_id"] for s in ft["manifest"]["items"] if s["status"] == "ACQUIRED_AUTO"}
        items = []
        for item in corpus["items"]:
            if item["corpus_item_id"] in acquired:
                excluded.append(item["corpus_item_id"])
                continue
            from ..library import linking
            claims = linking.item_claims(item)
            dois = sorted({d.get("value") for r in (item.get("metadata") or {}).get("records") or [] for d in (r.get("fields") or {}).get("doi") or []
                           if isinstance(d, dict) and isinstance(d.get("value"), str)})
            items.append({"corpus_item_id": item["corpus_item_id"], "publication_entity_id": item["publication_entity_id"], "titles": claims["titles"],
                          "dois": dois, "openalex_work_ids": [], "landing_pages": [], "candidates": [], "rv_status": None, "rv_reasons": []})
        return corpus, {"release_id": manifest["release_id"], "release_manifest_sha256": manifest["release_semantic_manifest_sha256"],
                        "fulltext_run_id": None, "rv_digest": None, "acquisition_state_known": bool(fulltext_dir)}, items, excluded

    def export_batch(self, *, source_dir: str, kind: str, workspace_label: str, out_dir: str, operator: str,
                     expected_manifest_sha256: str | None = None, rv_verify_json: str | None = None, fulltext_dir: str | None = None,
                     limit: int | None = None, include_ineligible: bool = False) -> dict:
        self._require_schema()
        actor = operator_label(operator)
        kb = self.kb
        if kind not in KINDS:
            raise ValidationError("Unknown source kind", code="SOURCE_KIND_INVALID")
        source_kind = KINDS[kind]
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValidationError("--limit must be a positive integer", code="LIMIT_INVALID")
        guard_path_text(out_dir, "--out-dir")
        guard_path_text(source_dir, "--source-dir")
        for value, what in ((fulltext_dir, "--fulltext-dir"), (rv_verify_json, "--rv-verify-json")):
            if value is not None:
                guard_path_text(value, what)
        target = safe_data_root(out_dir, must_exist=False)
        source_root = Path(os.path.realpath(source_dir))
        if target == kb.root or target.is_relative_to(kb.root) or kb.root.is_relative_to(target) or target.is_relative_to(source_root) \
                or source_root.is_relative_to(target):
            raise ValidationError("--out-dir must be outside the KB and the source export", code="OUT_DIR_INVALID")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValidationError("--out-dir must be absent or empty (one new folder per batch)", code="OUT_DIR_NOT_EMPTY")
        source, release, items, excluded = self._source_rows(source_kind, source_dir, expected_manifest_sha256, fulltext_dir)
        verify = dict(source["verify"])
        if rv_verify_json:
            digest = release["rv_digest"] if source_kind == "RV_MANUAL_QUEUE" else release["release_manifest_sha256"]
            verify["native_verify_export"] = sources.check_native_verify(rv_verify_json, kind="fulltext" if source_kind == "RV_MANUAL_QUEUE" else "corpus",
                                                                         expected_digest=digest, item_count=verify["item_count"])
        else:
            verify["native_verify_export"] = "NOT_SUPPLIED"
        workspace = self.library.workspace(workspace_label)
        if not workspace:
            raise ValidationError("Unknown workspace label; import the Stage-11 release with 'rkb library import-reprovane' first",
                                  code="WORKSPACE_UNKNOWN")
        if limit is not None:
            items = items[:limit]
        if not items:
            raise ValidationError("No source items to export", code="EXPORT_EMPTY")
        batch_id = kb.ids.new("ab")
        short = batch_short(batch_id)
        rows, csv_rows = [], []
        for ordinal, item in enumerate(items, start=1):
            ref = kb.one("SELECT * FROM project_paper_reference WHERE workspace_ref=? AND release_manifest_sha256=? AND corpus_item_id=?",
                         (workspace["workspace_ref"], release["release_manifest_sha256"], item["corpus_item_id"]))
            if not ref:
                raise ValidationError("A source item is not registered in this KB workspace (no ProjectPaperReference)", code="SOURCE_NOT_REGISTERED")
            if ref["publication_entity_id"] != item["publication_entity_id"]:
                raise IntegrityError("Source item entity differs from the registered reference", code="SOURCE_IDENTITY_MISMATCH")
            key = f"rv:{release['release_manifest_sha256']}:{item['corpus_item_id']}"
            row_id = bridge_row_id(batch_id, key)
            flags = []
            active, conflicting = self._paper_dois(ref["paper_id"])
            doi, doi_flags = policy.select_doi(item["dois"], active, conflicting)
            flags += doi_flags
            direct, ineligible_exported = [], []
            landing = set(item["landing_pages"])
            for cand in item["candidates"]:
                if cand["url"] in landing:
                    continue
                if not cand["eligible"] and not include_ineligible:
                    flags.append("RV_INELIGIBLE_URL_NOT_EXPORTED")
                    continue
                refusal = policy.url_refusal(cand["url"])
                if refusal:
                    flags.append(refusal)
                    continue
                if not cand["eligible"]:
                    flags.append("RV_INELIGIBLE_URL_EXPORTED")
                    ineligible_exported.append({"candidate_id": cand.get("candidate_id"), "rv_reason": cand.get("reason")})
                direct.append(cand["url"])
            landing_url = ""
            for url in item["landing_pages"]:
                refusal = policy.url_refusal(url)
                if refusal:
                    flags.append(refusal)
                    continue
                landing_url = url
                break
            if source_kind == "RV_CORPUS":
                flags.append("NO_RV_ELIGIBILITY_DECISION")
            urls = {"best_oa_pdf_url": direct[0] if direct else "", "pdf_url": direct[1] if len(direct) > 1 else "", "landing_page": landing_url}
            if not doi and not any(urls.values()):
                flags.append("NO_URL_EXPECTED")
            claims = json.loads(ref["item_claims_json"])
            display_flags = []
            title = policy.display((item["titles"] or claims.get("titles") or [""])[0], flags=display_flags, name="title")
            years = claims.get("years") or []
            venue = policy.display((claims.get("venues") or [""])[0], flags=display_flags, name="source")
            flags += display_flags
            if sum(1 for r in rows if r["paper_id"] == ref["paper_id"]):
                flags.append("PAPER_ALREADY_IN_BATCH")
            flags = sorted(set(flags))
            manifest_row = {"row_ordinal": ordinal, "bridge_row_id": row_id, "save_as": row_id + ".pdf", "source_item_key": key,
                            "corpus_item_id": item["corpus_item_id"], "publication_entity_id": item["publication_entity_id"], "paper_id": ref["paper_id"],
                            "reference_id": ref["reference_id"], "export_doi": doi, "all_dois": item["dois"], "openalex_work_ids": item["openalex_work_ids"],
                            "titles": item["titles"], "rv_status": item["rv_status"], "rv_reasons": item["rv_reasons"], "flags": flags,
                            "exported_urls": urls, "ineligible_urls_exported": ineligible_exported}
            rows.append(manifest_row)
            csv_rows.append({"record_id": row_id, "doi": doi or "", "title": title, "publication_year": str(years[0]) if len(years) == 1 else "",
                             "publisher": "", "source": venue, "is_open_access": "", "oa_status": "", "best_oa_pdf_url": urls["best_oa_pdf_url"],
                             "pdf_url": urls["pdf_url"], "landing_page": urls["landing_page"], "save_as": row_id + ".pdf"})
        csv_name, folder_name = f"pbd_input_{short}.csv", f"pbd_output_{short}"
        csv_bytes = pbd.write_input_csv(csv_rows)
        rows_sem = semantic_sha256(rows)
        manifest = {"schema": MANIFEST_SCHEMA, "batch_id": batch_id, "batch_short": short,
                    "created_by": {"component": "rkb.bridge", "version": __version__},
                    "source": {"kind": source_kind, "workspace_semantic_sha256": workspace["semantic_sha256"], "release_id": release["release_id"],
                               "release_manifest_sha256": release["release_manifest_sha256"], "fulltext_run_id": release["fulltext_run_id"],
                               "fulltext_manifest_rv_digest": release["rv_digest"], "source_export_sums_sha256": source["sums_sha256"],
                               "source_files_sha256": source["files"], "verify_export_result": verify,
                               "excluded_already_acquired": sorted(excluded), "acquisition_state_known": release.get("acquisition_state_known", True)},
                    "pbd_contract": {"profile": pbd.PROFILE, "input_header": pbd.INPUT_HEADER, "expected_output_header": pbd.OUTPUT_HEADER,
                                     "pbd_version_expected": pbd.PBD_VERSION_EXPECTED, "pbd_reference_commit": pbd.PBD_PINNED_COMMIT,
                                     "csvutils_git_blob": pbd.PBD_CSVUTILS_BLOB},
                    "exported_csv": {"file_name": csv_name, "bytes_sha256": sha256_bytes(csv_bytes), "row_count": len(rows)},
                    "path_policy": {"output_folder_name": folder_name, "output_folder_must_be_empty": True, "reparse_points": "REFUSE",
                                    "max_file_bytes": MAX_FILE_BYTES, "flat_names_only": True},
                    "rows": rows, "rows_semantic_sha256": rows_sem}
        manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")
        _no_local_paths(manifest_bytes + csv_bytes)
        # CAS first (an unreferenced object is the only possible crash residue), then the files, then the DB rows.
        kb.cas.put(manifest_bytes)
        kb.cas.put(csv_bytes)
        created_target = not target.exists()
        written = []
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name, body in ((csv_name, csv_bytes), ("bridge_manifest.json", manifest_bytes)):
                with open(target / name, "xb") as handle:
                    handle.write(body)
                written.append(target / name)
            (target / folder_name).mkdir()
            written.append(target / folder_name)
            with kb.transaction():
                batch = dict(batch_id=batch_id, batch_short=short, source_kind=source_kind, workspace_ref=workspace["workspace_ref"],
                             release_id=release["release_id"], release_manifest_sha256=release["release_manifest_sha256"],
                             fulltext_run_id=release["fulltext_run_id"], fulltext_manifest_rv_digest=release["rv_digest"],
                             source_files_json=json_text(source["files"]), source_sums_sha256=source["sums_sha256"],
                             expected_hash_supplied=int(bool(expected_manifest_sha256)), verify_result_json=json_text(verify), pbd_profile=pbd.PROFILE,
                             manifest_bytes_sha256=sha256_bytes(manifest_bytes), rows_semantic_sha256=rows_sem, input_csv_bytes_sha256=sha256_bytes(csv_bytes),
                             input_csv_name=csv_name, row_count=len(rows), out_dir_path_sha256=path_hash(target), output_folder_name=folder_name,
                             output_folder_path_sha256=path_hash(target / folder_name), output_folder_created_empty=1, created_by=actor,
                             client_version=__version__, created_at_utc=kb.now(),
                             semantic_sha256=semantic_sha256({"batch_id": batch_id, "manifest": sha256_bytes(manifest_bytes)}))
                kb.insert("bridge_batch", batch)
                for r in rows:
                    kb.insert("bridge_row", dict(bridge_row_id=r["bridge_row_id"], batch_id=batch_id, row_ordinal=r["row_ordinal"], save_as=r["save_as"],
                                                 source_item_key=r["source_item_key"], paper_id=r["paper_id"], reference_id=r["reference_id"],
                                                 corpus_item_id=r["corpus_item_id"], publication_entity_id=r["publication_entity_id"],
                                                 export_doi=r["export_doi"], flags_json=json_text(r["flags"]), exported_urls_json=json_text(r["exported_urls"]),
                                                 row_semantic_sha256=semantic_sha256(r)))
                    self._row_event(r["bridge_row_id"], "EXPORTED", "BATCH_EXPORTED", batch_id)
                self._batch_event(batch_id, "EXPORTED", None, [], actor)
        except BaseException:
            for path in reversed(written):
                if path.is_dir():
                    path.rmdir()
                elif path.exists():
                    path.unlink()
            if created_target and target.exists() and not any(target.iterdir()):
                target.rmdir()
            raise
        flag_counts = {}
        for r in rows:
            for f in r["flags"]:
                flag_counts[f] = flag_counts.get(f, 0) + 1
        return {"batch_id": batch_id, "batch_short": short, "row_count": len(rows), "input_csv": csv_name, "manifest": "bridge_manifest.json",
                "output_folder": folder_name, "manifest_bytes_sha256": sha256_bytes(manifest_bytes), "rows_semantic_sha256": rows_sem,
                "input_csv_bytes_sha256": sha256_bytes(csv_bytes), "doi_cells_written": sum(1 for r in rows if r["export_doi"]),
                "flag_counts": dict(sorted(flag_counts.items())), "excluded_already_acquired": len(excluded),
                "expected_hash_supplied": bool(expected_manifest_sha256), "verify_export": verify,
                "note": "Hash checks are integrity comparisons, not authentication. Give PBD the CSV and the empty output folder."}

    def _batch_event(self, batch_id, state, reason, open_rows, actor):
        content = {"batch": batch_id, "state": state, "reason": reason, "open": open_rows, "n": self.kb.one(
            "SELECT count(*) n FROM bridge_batch_event WHERE batch_id=?", (batch_id,))["n"]}
        self.kb.insert("bridge_batch_event", dict(event_id=self.kb.ids.new("bbe"), batch_id=batch_id, new_state=state, reason=reason,
                                                  open_rows_json=json_text(open_rows), actor=actor, recorded_at_utc=self.kb.now(),
                                                  semantic_sha256=semantic_sha256(content)))

    # -- import --------------------------------------------------------------------------------
    def _refuse(self, batch, result_sha, code, actor, exc_type=ValidationError, message=None, *, folder_hash=None, different=False, late=False):
        with self.kb.transaction():
            self.kb.insert("bridge_result_import", dict(import_id=self.kb.ids.new("bri"), batch_id=batch["batch_id"], result_csv_sha256=result_sha or "0" * 64,
                                                        outcome="REFUSED", refusal_code=code, data_row_count=0, counts_json="{}", anomalies_json="[]",
                                                        pdf_folder_path_sha256=folder_hash, different_output_folder=int(different),
                                                        accept_late_files=int(late), operator=actor, completed_at_utc=self.kb.now()))
        raise exc_type(message or code, code=code)

    def import_results(self, *, batch_id: str, result_csv: str, pdf_folder: str, operator: str, manifest_file: str | None = None,
                       accept_late_files: bool = False, allow_different_folder: bool = False, faults=None, asset_faults=None) -> tuple[int, dict]:
        self._require_schema()
        actor = operator_label(operator)
        kb = self.kb
        batch = self.batch(batch_id)
        guard_path_text(result_csv, "--result-csv")
        guard_path_text(pdf_folder, "--pdf-folder")
        if manifest_file is not None:
            guard_path_text(manifest_file, "--manifest")
        if self.batch_state(batch_id) in ("CLOSED", "CLOSED_WITH_OPEN_ROWS"):
            self._refuse(batch, None, "BATCH_CLOSED", actor)
        # 1. recorded hashes: KB copy of the manifest, and the operator's manifest file if given (integrity comparison only).
        recorded = kb.cas.read(batch["manifest_bytes_sha256"])
        if json.loads(recorded)["rows_semantic_sha256"] != batch["rows_semantic_sha256"]:
            self._refuse(batch, None, "MANIFEST_HASH_MISMATCH", actor, IntegrityError)
        if manifest_file is not None:
            if sha256_bytes(safe_input_file(manifest_file, max_bytes=64 * 1024 * 1024).read_bytes()) != batch["manifest_bytes_sha256"]:
                self._refuse(batch, None, "MANIFEST_HASH_MISMATCH", actor, IntegrityError,
                             "bridge_manifest.json differs from the value recorded at export (integrity comparison, not authentication)")
        # 2. result CSV: explicit file, read once, size bounded; never auto-discovered.
        data = safe_input_file(result_csv, max_bytes=pbd.MAX_RESULT_CSV_BYTES).read_bytes()
        result_sha = sha256_bytes(data)
        try:
            records = pbd.parse_csv(data, pbd.OUTPUT_HEADER)
        except pbd.CsvFormatError as exc:
            self._refuse(batch, result_sha, exc.code, actor)
        # 3. whole-file idempotency.
        done = kb.one("SELECT * FROM bridge_result_import WHERE batch_id=? AND result_csv_sha256=? AND outcome<>'REFUSED'", (batch_id, result_sha))
        if done:
            return 0, {"batch_id": batch_id, "outcome": "DUPLICATE_NOOP", "import_id": done["import_id"], "result_csv_sha256": result_sha,
                       "counts": json.loads(done["counts_json"])}
        # 4. PDF folder (explicit, flat, no links, outside the KB).
        folder = safe_data_root(pdf_folder, must_exist=True)
        check_no_links_on_path(folder)
        if not folder.is_dir() or folder == kb.root or folder.is_relative_to(kb.root) or kb.root.is_relative_to(folder):
            self._refuse(batch, result_sha, "PDF_FOLDER_INVALID", actor)
        folder_hash = path_hash(folder)
        different = folder_hash != batch["output_folder_path_sha256"]
        if different and not allow_different_folder:
            self._refuse(batch, result_sha, "DIFFERENT_OUTPUT_FOLDER", actor, folder_hash=folder_hash, different=True, late=accept_late_files)
        names = sorted(p.name for p in folder.iterdir())
        rows = {r["save_as"]: r for r in kb.all("SELECT * FROM bridge_row WHERE batch_id=? ORDER BY row_ordinal", (batch_id,))}
        by_fold = {}
        for save_as in rows:
            by_fold.setdefault(save_as.casefold(), save_as)
        # 5. match rows by save_as (never by row number), then token check.
        anomalies, matched, seen = [], [], {}
        for number, record in enumerate(records, start=1):
            if len(record) != len(pbd.OUTPUT_COLUMNS):
                anomalies.append({"output_row": number, "code": "ROW_MALFORMED"})
                continue
            fields = dict(zip(pbd.OUTPUT_COLUMNS, record))
            save_as = fields["save_as"].strip()
            if save_as not in rows:
                code = "CASE_VARIANT_NAME" if save_as.casefold() in by_fold else "ROW_UNEXPECTED"
                anomalies.append({"output_row": number, "code": code, "save_as_sha256": sha256_bytes(save_as.encode("utf-8"))})
                continue
            seen.setdefault(save_as, []).append(number)
            matched.append((number, fields, rows[save_as]))
        if records and len(matched) * 2 < len(records):
            self._refuse(batch, result_sha, "WRONG_BATCH", actor, message="Most result rows do not belong to this batch", folder_hash=folder_hash,
                         different=different, late=accept_late_files)
        duplicates = {s for s, numbers in seen.items() if len(numbers) > 1}
        manifest_names = set(rows)
        for name in names:
            if name not in manifest_names:
                anomalies.append({"code": "CASE_VARIANT_NAME" if name.casefold() in by_fold else "EXTRA_FILE",
                                  "name_sha256": sha256_bytes(name.encode("utf-8"))})
        dv_cache, counts = {}, {}
        for index, (number, fields, row) in enumerate(matched):
            outcome = self._import_row(batch, row, number, fields, result_sha, folder, set(names), duplicates, dv_cache, actor,
                                       accept_late_files, allow_different_folder and different, asset_faults)
            counts[outcome] = counts.get(outcome, 0) + 1
            if faults and "after_row" in faults:
                faults["after_row"](index=index)
        missing = sorted(s for s in rows if s not in seen)
        for save_as in missing:
            anomalies.append({"code": "ROW_MISSING", "bridge_row_id": rows[save_as]["bridge_row_id"]})
        statuses = {r["bridge_row_id"]: self.row_status(r["bridge_row_id"]) for r in rows.values()}
        complete = all(s in pbd.FINAL_ROW_STATUSES for s in statuses.values())
        anomaly_counts = {}
        for a in anomalies:
            anomaly_counts[a["code"]] = anomaly_counts.get(a["code"], 0) + 1
        summary = {"row_outcomes": dict(sorted(counts.items())), "anomalies": dict(sorted(anomaly_counts.items())),
                   "row_status": dict(sorted(_count(statuses.values()).items()))}
        with kb.transaction():
            import_id = kb.ids.new("bri")
            kb.insert("bridge_result_import", dict(import_id=import_id, batch_id=batch_id, result_csv_sha256=result_sha,
                                                   outcome="RECONCILED_COMPLETE" if complete else "RECONCILED_PARTIAL", refusal_code=None,
                                                   data_row_count=len(records), counts_json=json_text(summary), anomalies_json=json_text(anomalies),
                                                   pdf_folder_path_sha256=folder_hash, different_output_folder=int(different),
                                                   accept_late_files=int(accept_late_files), operator=actor, completed_at_utc=kb.now()))
            self._batch_event(batch_id, "RECONCILED_COMPLETE" if complete else "RECONCILED_PARTIAL", None, [], actor)
        partial = bool(anomalies) or any(k in counts for k in ("QUARANTINED", "UNKNOWN_STATUS"))
        return (4 if partial else 0), {"batch_id": batch_id, "outcome": "RECONCILED_COMPLETE" if complete else "RECONCILED_PARTIAL",
                                       "import_id": import_id, "result_csv_sha256": result_sha, "data_rows": len(records),
                                       "different_output_folder": different, "counts": summary, "anomalies": anomalies,
                                       "note": "VERIFIED_PDF = bytes and structure verified; paper identity stays UNVERIFIED until a human review."}

    def _import_row(self, batch, row, number, fields, result_sha, folder, names, duplicates, dv_cache, actor, accept_late, different_folder,
                    asset_faults=None) -> str:
        kb = self.kb
        raw = fields["status"].strip()
        status, sub = pbd.map_status(raw)
        codes = []
        if fields["openalex_id"].strip() != row["bridge_row_id"]:
            codes.append("ROW_IDENTITY_MISMATCH")
        if row["save_as"] in duplicates:
            codes.append("ROW_DUPLICATE")
        if fields["id"].strip() != str(row["row_ordinal"]):
            codes.append("ROW_ORDINAL_MISMATCH")
        if different_folder:
            codes.append("DIFFERENT_OUTPUT_FOLDER_ACCEPTED_BY_OPERATOR")
        if status == "FAILED":
            sub = pbd.failed_sub_reason(fields["deferred_reason"].strip())
        quarantined = "ROW_IDENTITY_MISMATCH" in codes or "ROW_DUPLICATE" in codes
        file_present = row["save_as"] in names
        if file_present and status not in ("RECEIVED_PENDING_VERIFY",):
            codes.append("FILE_PRESENT_FOR_NON_COMPLETED_ROW")
        reported = fields["file_path"].strip()
        observed = {"status_raw": raw[:64], "url_source": fields["url_source"].strip() if fields["url_source"].strip() in _URL_SOURCES else "<other>",
                    "selected_url_host": _url_host(fields["selected_url"]) if fields["selected_url"].strip() else "",
                    "pdf_url_host": _url_host(fields["pdf_url"]) if fields["pdf_url"].strip() else "",
                    "preflight": fields["preflight"].strip() if fields["preflight"].strip() in ("yes", "no") else "<other>",
                    "page_decision": sanitize_text(fields["page_decision"], 64), "attention_reason": sanitize_text(fields["attention_reason"]),
                    "deferred_reason": sanitize_text(fields["deferred_reason"]), "error": sanitize_text(fields["error"]),
                    "attempts": int(fields["attempts"]) if fields["attempts"].strip().isdigit() and len(fields["attempts"].strip()) < 6 else None,
                    "file_path_reported": bool(reported), "file_path_basename_matches_save_as": os.path.basename(reported.replace("\\", "/")) == row["save_as"],
                    "doi_echo_matches_export": fields["doi"].strip().lower() == (row["export_doi"] or "")}
        bridge_status = "QUARANTINED" if quarantined else status
        content = {"batch": batch["batch_id"], "row": row["bridge_row_id"], "result": result_sha, "n": number, "status": bridge_status,
                   "sub": sub, "codes": sorted(codes), "observed": observed}
        with kb.transaction():
            attempt, _ = kb.insert_unique("bridge_attempt", dict(
                attempt_id=kb.ids.new("bat"), batch_id=batch["batch_id"], bridge_row_id=row["bridge_row_id"], result_csv_sha256=result_sha,
                output_row_number=number, status_raw=raw[:64], bridge_status=bridge_status, sub_reason=sub, codes_json=json_text(sorted(codes)),
                observed_json=json_text(observed), recorded_at_utc=kb.now(), semantic_sha256=semantic_sha256(content)))
            verified_before = self.row_status(row["bridge_row_id"]) == "VERIFIED_PDF"
            if not verified_before:
                self._row_event(row["bridge_row_id"], bridge_status, "ATTEMPT", attempt["attempt_id"])
            if quarantined:
                self._review(batch["batch_id"], row["bridge_row_id"], "ROW_DUPLICATE" if "ROW_DUPLICATE" in codes else "ROW_IDENTITY_MISMATCH",
                             {"result_csv_sha256": result_sha, "output_row": number})
            elif status == "UNKNOWN_STATUS":
                self._review(batch["batch_id"], row["bridge_row_id"], "UNKNOWN_STATUS", {"status_raw_sha256": sha256_bytes(raw.encode("utf-8"))})
            elif status in OPEN_REVIEW_STATUSES:
                self._review(batch["batch_id"], row["bridge_row_id"], status, {"status_raw": raw, "result_csv_sha256": result_sha})
        if quarantined or status == "UNKNOWN_STATUS":
            return bridge_status
        resumed = kb.one("SELECT outcome FROM bridge_file_event WHERE attempt_id=? ORDER BY rowid LIMIT 1", (attempt["attempt_id"],))
        if resumed:   # crash recovery: this attempt's file step already completed in an interrupted run
            return resumed["outcome"]
        intake = status == "RECEIVED_PENDING_VERIFY" or (status == "DEFERRED" and file_present and accept_late)
        if status == "DEFERRED" and file_present and not accept_late:
            with kb.transaction():
                self._file_event(attempt, row, "LATE_FILE_PRESENT", ["LATE_FILE_PRESENT"], None, None, None)
                self._review(batch["batch_id"], row["bridge_row_id"], "LATE_FILE_PRESENT", {"result_csv_sha256": result_sha})
            return "LATE_FILE_PRESENT"
        if not intake:
            return bridge_status
        extra = ["ACCEPTED_LATE_FILE_BY_OPERATOR"] if status == "DEFERRED" else []
        if not file_present:
            code = "CASE_VARIANT_NAME" if any(n.casefold() == row["save_as"].casefold() for n in names) else "FILE_MISSING"
            return self._reject(attempt, row, [code] + extra, verified_before)
        barriers = sorted({b["status_raw"] for b in kb.all(
            "SELECT status_raw FROM bridge_attempt WHERE bridge_row_id=? AND bridge_status='NEEDS_HUMAN_ACCESS'", (row["bridge_row_id"],))})
        if row["paper_id"] not in dv_cache:
            dv_cache[row["paper_id"]] = self.assets.add_document_version(paper_id=row["paper_id"], version_class="UNKNOWN", evidence=[],
                                                                         operator=actor.split(":", 1)[1])["document_version_id"]
        try:
            out = self.assets.register_asset(fixture_root=str(folder), relative_path=row["save_as"], document_version_id=dv_cache[row["paper_id"]],
                                             operator=actor.split(":", 1)[1], access_route="UNKNOWN", access_barriers=barriers,
                                             max_bytes=MAX_FILE_BYTES, require_human_identity=True, faults=asset_faults)
        except KBError as exc:
            return self._reject(attempt, row, [exc.code] + extra, verified_before)
        if out["outcome"] == "REJECTED":
            return self._reject(attempt, row, list(out["reasons"]) + extra, verified_before, input_sha=out["input_sha256"])
        digest, link_id = out["asset_sha256"], out["link_id"]
        with kb.transaction():
            prior = kb.all("SELECT DISTINCT asset_sha256 FROM bridge_file_event WHERE bridge_row_id=? AND outcome IN ('VERIFIED_PDF','NEW_BYTES_SAME_ROW')",
                           (row["bridge_row_id"],))
            prior = {p["asset_sha256"] for p in prior}
            if digest in prior:
                self._file_event(attempt, row, "DUPLICATE_NOOP", extra, digest, digest, link_id)
                result = "DUPLICATE_NOOP"
            elif prior:
                self._file_event(attempt, row, "NEW_BYTES_SAME_ROW", ["NEW_BYTES_SAME_ROW"] + extra, digest, digest, link_id)
                self._review(batch["batch_id"], row["bridge_row_id"], "NEW_BYTES_SAME_ROW", {"assets": sorted(prior | {digest})})
                result = "NEW_BYTES_SAME_ROW"
            else:
                self._file_event(attempt, row, "VERIFIED_PDF", extra, digest, digest, link_id)
                result = "VERIFIED_PDF"
            self._row_event(row["bridge_row_id"], "VERIFIED_PDF", "FILE_EVENT", attempt["attempt_id"])
        return result

    def _reject(self, attempt, row, codes, verified_before, input_sha=None) -> str:
        with self.kb.transaction():
            self._file_event(attempt, row, "REJECTED_FILE", codes, input_sha, None, None)
            if not verified_before:
                self._row_event(row["bridge_row_id"], "REJECTED_FILE", "FILE_EVENT", attempt["attempt_id"])
        return "REJECTED_FILE"

    def _file_event(self, attempt, row, outcome, codes, input_sha, asset, link_id):
        content = {"attempt": attempt["semantic_sha256"], "outcome": outcome, "codes": sorted(codes), "input": input_sha, "asset": asset}
        self.kb.insert_unique("bridge_file_event", dict(event_id=self.kb.ids.new("bfe"), attempt_id=attempt["attempt_id"], bridge_row_id=row["bridge_row_id"],
                                                        outcome=outcome, reason_codes_json=json_text(sorted(codes)), input_sha256=input_sha, asset_sha256=asset,
                                                        link_id=link_id, recorded_at_utc=self.kb.now(), semantic_sha256=semantic_sha256(content)))

    # -- status / close ------------------------------------------------------------------------
    def status(self, batch_id: str) -> dict:
        self._require_schema()
        batch = self.batch(batch_id)
        kb = self.kb
        out_rows = []
        for r in kb.all("SELECT * FROM bridge_row WHERE batch_id=? ORDER BY row_ordinal", (batch_id,)):
            assets = sorted({e["asset_sha256"] for e in kb.all("SELECT asset_sha256 FROM bridge_file_event WHERE bridge_row_id=? AND asset_sha256 IS NOT NULL",
                                                                (r["bridge_row_id"],))})
            links = [dict(l) for l in kb.all("SELECT link_id, effective_state FROM paper_asset_link_effective WHERE paper_id=? AND asset_sha256 IN (%s)"
                                              % ",".join("?" * len(assets)), (r["paper_id"], *assets))] if assets else []
            out_rows.append({"row_ordinal": r["row_ordinal"], "bridge_row_id": r["bridge_row_id"], "paper_id": r["paper_id"],
                             "status": self.row_status(r["bridge_row_id"]), "final": self.row_status(r["bridge_row_id"]) in pbd.FINAL_ROW_STATUSES,
                             "export_doi": r["export_doi"], "flags": json.loads(r["flags_json"]), "assets": assets,
                             "identity_states": sorted({l["effective_state"] for l in links}),
                             "attempts": kb.one("SELECT count(*) n FROM bridge_attempt WHERE bridge_row_id=?", (r["bridge_row_id"],))["n"],
                             "review_tasks": [t["trigger"] for t in kb.all("SELECT trigger FROM bridge_review_task WHERE bridge_row_id=? ORDER BY rowid",
                                                                            (r["bridge_row_id"],))]})
        imports = [{"import_id": i["import_id"], "outcome": i["outcome"], "refusal_code": i["refusal_code"], "result_csv_sha256": i["result_csv_sha256"],
                    "data_rows": i["data_row_count"]} for i in kb.all("SELECT * FROM bridge_result_import WHERE batch_id=? ORDER BY rowid", (batch_id,))]
        return {"batch_id": batch_id, "batch_short": batch["batch_short"], "state": self.batch_state(batch_id), "source_kind": batch["source_kind"],
                "row_count": batch["row_count"], "counts": dict(sorted(_count(r["status"] for r in out_rows).items())),
                "open_rows": [r["bridge_row_id"] for r in out_rows if not r["final"]], "rows": out_rows, "imports": imports,
                "expected_hash_supplied": bool(batch["expected_hash_supplied"]), "manifest_bytes_sha256": batch["manifest_bytes_sha256"]}

    def close(self, *, batch_id: str, operator: str, reason: str | None = None) -> dict:
        self._require_schema()
        actor = operator_label(operator)
        self.batch(batch_id)
        if self.batch_state(batch_id) in ("CLOSED", "CLOSED_WITH_OPEN_ROWS"):
            raise ValidationError("Batch already closed", code="BATCH_CLOSED")
        if reason not in (None, "OPERATOR_CLOSE_WITH_OPEN_ROWS"):
            raise ValidationError("Only --reason OPERATOR_CLOSE_WITH_OPEN_ROWS is accepted", code="CLOSE_REASON_INVALID")
        open_rows = [r["bridge_row_id"] for r in self.kb.all("SELECT bridge_row_id FROM bridge_row WHERE batch_id=? ORDER BY row_ordinal", (batch_id,))
                     if self.row_status(r["bridge_row_id"]) not in pbd.FINAL_ROW_STATUSES]
        if open_rows and reason is None:
            raise ValidationError("Rows are still open; close with --reason OPERATOR_CLOSE_WITH_OPEN_ROWS", code="BATCH_HAS_OPEN_ROWS")
        state = "CLOSED_WITH_OPEN_ROWS" if open_rows else "CLOSED"
        with self.kb.transaction():
            self._batch_event(batch_id, state, reason if open_rows else None, open_rows, actor)
        return {"batch_id": batch_id, "state": state, "open_rows": open_rows}


def _count(values) -> dict:
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _no_local_paths(data: bytes) -> None:
    """Portable artifacts never carry local absolute paths (06 section 5)."""
    text = data.decode("utf-8", errors="replace")
    if re.search(r"(?<![A-Za-z0-9])[A-Za-z]:\\\\|\\\\\\\\[A-Za-z0-9]|(?<![:/A-Za-z0-9])/(?:home|Users|mnt|tmp)/", text):
        raise IntegrityError("A local path would be written to a portable bridge file", code="LOCAL_PATH_IN_PORTABLE_FILE")


__all__ = ["BridgeService", "guard_path_text", "bridge_row_id", "batch_short", "sanitize_text", "path_hash", "MANIFEST_SCHEMA"]
