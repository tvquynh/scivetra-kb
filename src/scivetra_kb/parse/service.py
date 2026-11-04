"""S06 ParseService: rights- and identity-gated local PDF parsing into immutable, page-anchored ParseArtifacts.

Gate order (every refusal is recorded as a ``parse_run`` row and NO byte of the asset is opened):

1. the asset is a registered KB ``pdf_asset`` (a Reprovane hash-only reference or a PBD ``Completed`` row is not);
2. the link belongs to the asset; MISMATCH is never parsed; ``RESEARCH_PARSE`` needs a HUMAN_CONFIRMED MAIN_TEXT link,
   ``IDENTITY_INSPECTION`` accepts an unconfirmed/ambiguous link but its output is pinned NOT_ELIGIBLE_FOR_EVIDENCE;
3. the CURRENT rights assessment of (asset, document version) is ``rights-policy/1`` and gate A2 (``local_process``)
   is ALLOW; missing, UNDETERMINED, DENY or stale assessments refuse;
4. OS-level network isolation is established, or the operator explicitly accepted the language-level guard;
5. the pinned pypdf copy verifies against its RECORD.

Then the CAS object is re-hashed, copied into a private staging folder (hash checked while copying), made read-only,
parsed in the sandbox child, re-hashed after the child exits, and deleted. The canonical ``parse-artifact/1`` JSON goes
to CAS first, then one transaction records the artifact and the run (a crash can only leave an unreferenced object).
Reads and exports re-check the CURRENT rights; parsed text is withheld once local processing is no longer allowed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from .. import __version__
from ..bridge.service import guard_path_text, path_hash
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, KBError, PolicyRefusal, ValidationError
from ..core.paths import safe_data_root
from ..library.common import operator_label, require_reason
from ..rights import policy as rights_policy
from . import runtime, sandbox

ARTIFACT_SCHEMA, EXTRACTOR = "parse-artifact/1", "rkb-parse/1"
PURPOSES = ("RESEARCH_PARSE", "IDENTITY_INSPECTION")
NORMALIZATION = "rkb-text-norm/1: CRLF and CR -> LF, NUL removed; nothing else changed"
HEURISTICS = "rkb-quality-flags/1"
ALWAYS_FLAGS = ("BBOX_NOT_AVAILABLE", "CITATIONS_NOT_PARSED", "READING_ORDER_IS_CONTENT_STREAM_ORDER")
ANCHOR_RULE = "page is 1-based; start/end are Unicode code point offsets [start, end) into pages[page-1].text; text_sha256 = SHA-256 of the UTF-8 slice"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def anchor(payload: dict, artifact_sha256: str, page: int, start: int, end: int) -> dict:
    text = payload["pages"][page - 1]["text"]
    if not (0 <= start < end <= len(text)):
        raise ValidationError("Anchor range outside the page text", code="ANCHOR_OUT_OF_RANGE")
    return {"artifact_sha256": artifact_sha256, "page": page, "start": start, "end": end, "text_sha256": _sha(text[start:end])}


def check_anchor(payload: dict, ref: dict) -> bool:
    pages = payload["pages"]
    if not isinstance(ref.get("page"), int) or not 1 <= ref["page"] <= len(pages):
        return False
    text = pages[ref["page"] - 1]["text"]
    s, e = ref.get("start"), ref.get("end")
    return isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text) and _sha(text[s:e]) == ref.get("text_sha256")


def document_text_sha256(pages) -> str:
    return _sha("\x0c".join(p["text"] for p in pages))


class ParseService:
    def __init__(self, kb, *, parser_site: str | None = None, limits: dict | None = None, allow_language_level_network_guard: bool = False,
                 allow_privileged_account: bool = False, faults=None):
        self.kb = kb
        self.parser_site = parser_site
        self.limits = dict(sandbox.DEFAULT_LIMITS, **(limits or {}))
        self.allow_language_guard = allow_language_level_network_guard
        self.allow_privileged = allow_privileged_account
        self.faults = faults or {}
        self._isolation = None

    # -- configuration -------------------------------------------------------------
    def config(self) -> dict:
        return {"extractor": EXTRACTOR, "text_source": "pypdf PageObject.extract_text(extraction_mode='plain') visitor chunks, concatenated",
                "normalization": NORMALIZATION, "quality_flags": HEURISTICS, "anchor_rule": ANCHOR_RULE, "strict_open": True,
                "limits": {k: self.limits[k] for k in sandbox.OUTPUT_LIMIT_KEYS}}

    def runtime(self) -> dict:
        return runtime.verify_site(self.parser_site or runtime.default_site())

    def isolation_profile(self, site: str) -> dict:
        """Probe the child's OS controls once (self-test; no document bytes involved)."""
        if self._isolation is None:
            probe_dir = self.kb.root / "staging"
            probe_dir.mkdir(exist_ok=True)
            probe = probe_dir / "parse-isolation-probe.bin"
            probe.write_bytes(b"probe")
            try:
                # the probe checks the platform controls, so it always uses the default limits (not a caller's tighter cap)
                out = sandbox.run_child(probe, site, self_test="network", limits=dict(sandbox.DEFAULT_LIMITS, timeout_seconds=30),
                                        allow_privileged=True)      # a self-test reads no document bytes
            finally:
                probe.unlink()
            child = (out.get("result") or {}).get("sandbox") or {}
            self._isolation = {"available": out["status"] == "OK", "os_network_isolation": child.get("os_network_isolation", "UNKNOWN"),
                               "language_guard": (out.get("result") or {}).get("outcome"), "mechanism": child.get("mechanism"),
                               "process_creation": child.get("process_creation"), "privileged_account": sandbox.is_privileged(),
                               "parser_identity": child.get("parser_identity")}
        return self._isolation

    # -- gates ---------------------------------------------------------------------
    def _gates(self, asset_sha256: str, link_id: str, purpose: str) -> dict:
        kb = self.kb
        g = {"asset": None, "link": None, "assessment": None, "reasons": [], "rights_gate": {}, "eligibility": "NOT_ELIGIBLE_FOR_EVIDENCE"}
        if not kb.one("SELECT 1 FROM pdf_asset WHERE asset_sha256=?", (asset_sha256,)):
            g["reasons"].append("ASSET_NOT_IN_KB")
            return g
        g["asset"] = asset_sha256
        link = kb.one("SELECT * FROM paper_asset_link_effective WHERE link_id=?", (link_id,)) if link_id else None
        if not link or link["asset_sha256"] != asset_sha256:
            g["reasons"].append("LINK_NOT_FOR_ASSET")
            return g
        g["link"] = link
        if link["effective_state"] == "MISMATCH":
            g["reasons"].append("IDENTITY_MISMATCH")
        elif purpose == "RESEARCH_PARSE":
            if link["effective_state"] != "HUMAN_CONFIRMED":
                g["reasons"].append("IDENTITY_NOT_HUMAN_CONFIRMED")
            if link["effective_role"] != "MAIN_TEXT":
                g["reasons"].append("LINK_ROLE_NOT_MAIN_TEXT")
        assessment = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? AND document_version_id=? ORDER BY rowid DESC LIMIT 1",
                            (asset_sha256, link["document_version_id"]))
        g["assessment"] = assessment
        if assessment is not None and assessment["policy_version"] != rights_policy.POLICY_VERSION:
            g["reasons"].append("RIGHTS_ASSESSMENT_STALE")
        decision = rights_policy.gate("A2", assessment)
        g["rights_gate"] = {"action": "A2", "decision": decision["decision"], "reason": decision["reason"],
                            "assessment_id": assessment["assessment_id"] if assessment else None,
                            "rights_class": assessment["rights_class"] if assessment else None}
        if decision["decision"] != "ALLOW":
            g["reasons"].append("RIGHTS_" + decision["reason"])
        if purpose == "RESEARCH_PARSE" and not g["reasons"]:
            g["eligibility"] = "ELIGIBLE_WHILE_CURRENT"
        return g

    def _record_run(self, *, asset, gates, purpose, outcome, reasons, artifact_id=None, rt=None, config_sha=None, sandbox_meta=None, actor):
        kb = self.kb
        link = gates.get("link")
        run_id = kb.ids.new("pzr")
        row = dict(run_id=run_id, requested_asset_sha256=asset, link_id=link["link_id"] if link else None,
                   document_version_id=link["document_version_id"] if link else None, purpose=purpose, outcome=outcome,
                   reason_codes_json=canonical_json(sorted(set(reasons))), artifact_id=artifact_id,
                   evidence_eligibility=gates["eligibility"] if outcome in ("PRODUCED", "REUSED") else "NOT_ELIGIBLE_FOR_EVIDENCE",
                   identity_state_at_run=link["effective_state"] if link else None, link_role_at_run=link["effective_role"] if link else None,
                   rights_assessment_id=(gates["assessment"] or {}).get("assessment_id"),
                   rights_gate_json=canonical_json(gates.get("rights_gate") or {}), parser_version=(rt or {}).get("version"),
                   parser_build_sha256=(rt or {}).get("build_sha256"), config_sha256=config_sha, sandbox_json=canonical_json(sandbox_meta or {}),
                   actor=actor, client_version=__version__, created_at_utc=kb.now())
        row["semantic_sha256"] = semantic_sha256({k: v for k, v in row.items()})
        kb.insert("parse_run", row)
        return row

    # -- parse -----------------------------------------------------------------------
    def parse(self, *, asset_sha256: str, link_id: str, purpose: str, operator: str, dry_run: bool = False, recheck: bool = False) -> dict:
        actor = operator_label(operator)
        if purpose not in PURPOSES:
            raise ValidationError("purpose must be RESEARCH_PARSE or IDENTITY_INSPECTION", code="PARSE_PURPOSE_INVALID")
        if not isinstance(asset_sha256, str) or len(asset_sha256) != 64 or set(asset_sha256) - set("0123456789abcdef"):
            raise ValidationError("asset must be a lowercase SHA-256", code="INVALID_HASH")
        kb = self.kb
        gates = self._gates(asset_sha256, link_id, purpose)
        reasons = list(gates["reasons"])
        rt, iso = None, None
        if not reasons:
            try:
                rt = self.runtime()
            except ValidationError as exc:
                reasons.append(exc.code)
        if not reasons:
            iso = self.isolation_profile(rt["site"])
            if not iso["available"]:
                reasons.append("PARSER_SANDBOX_UNAVAILABLE")
            elif iso["os_network_isolation"] == "NONE" and not self.allow_language_guard:
                reasons.append("OS_NETWORK_ISOLATION_UNAVAILABLE")
            if iso["privileged_account"] and not self.allow_privileged:
                reasons.append("PARSER_PRIVILEGED_ACCOUNT_REFUSED")
        config = self.config()
        config_sha = semantic_sha256(config)
        summary = {"asset_sha256": asset_sha256, "link_id": link_id, "purpose": purpose, "evidence_eligibility": gates["eligibility"],
                   "rights_gate": gates["rights_gate"], "config_sha256": config_sha, "parser": {k: rt[k] for k in ("name", "version", "build_sha256", "provenance", "mode")} if rt else None,
                   "isolation": iso}
        if dry_run:
            return dict(summary, dry_run=True, would_refuse=sorted(set(reasons)), bytes_opened=False)
        if reasons:
            with kb.transaction():
                run = self._record_run(asset=asset_sha256, gates=gates, purpose=purpose, outcome="REFUSED", reasons=reasons, rt=rt,
                                       config_sha=config_sha, sandbox_meta={"isolation": iso}, actor=actor)
            return dict(summary, outcome="REFUSED", reasons=sorted(set(reasons)), run_id=run["run_id"], artifact_id=None, bytes_opened=False)
        key = semantic_sha256({"asset": asset_sha256, "parser": "pypdf", "version": rt["version"], "build": rt["build_sha256"],
                               "extractor": EXTRACTOR, "config": config_sha})
        existing = kb.one("SELECT * FROM parse_artifact WHERE semantic_sha256=?", (key,))
        if existing and not recheck:
            try:
                kb.cas.verify_object(existing["artifact_sha256"])
            except IntegrityError as exc:
                return self._fail(summary, gates, purpose, asset_sha256, [exc.code], rt, config_sha, {"isolation": iso}, actor)
            with kb.transaction():
                run = self._record_run(asset=asset_sha256, gates=gates, purpose=purpose, outcome="REUSED", reasons=[], artifact_id=existing["artifact_id"],
                                       rt=rt, config_sha=config_sha, sandbox_meta={"isolation": iso}, actor=actor)
            return dict(summary, outcome="REUSED", run_id=run["run_id"], artifact_id=existing["artifact_id"],
                        artifact_sha256=existing["artifact_sha256"], status=existing["status"], bytes_opened=False)
        produced = self._run_sandbox(asset_sha256, rt)
        meta = dict(produced.get("sandbox", {}), isolation=iso,
                    parser_runtime={k: rt[k] for k in ("mode", "provenance", "integrity", "build_sha256", "official_wheel_sha256")})
        if produced["status"] != "OK":
            return self._fail(summary, gates, purpose, asset_sha256, [produced["code"]], rt, config_sha, meta, actor)
        payload = self._payload(asset_sha256, rt, config, config_sha, produced["result"])
        data = (canonical_json(payload) + "\n").encode("utf-8")
        digest = sha256_bytes(data)
        if existing:      # recheck: an identical rerun must reproduce the identical bytes
            if digest != existing["artifact_sha256"]:
                return self._fail(summary, gates, purpose, asset_sha256, ["PARSE_NONDETERMINISTIC"], rt, config_sha, meta, actor)
            with kb.transaction():
                run = self._record_run(asset=asset_sha256, gates=gates, purpose=purpose, outcome="REUSED", reasons=[], artifact_id=existing["artifact_id"],
                                       rt=rt, config_sha=config_sha, sandbox_meta=dict(meta, recheck="IDENTICAL_BYTES"), actor=actor)
            return dict(summary, outcome="REUSED", recheck="IDENTICAL_BYTES", run_id=run["run_id"], artifact_id=existing["artifact_id"],
                        artifact_sha256=digest, status=existing["status"], bytes_opened=True)
        kb.cas.put(data)
        with kb.transaction():
            artifact = dict(artifact_id=kb.ids.new("pza"), asset_sha256=asset_sha256, artifact_schema=ARTIFACT_SCHEMA, parser_name="pypdf",
                            parser_version=rt["version"], parser_build_sha256=rt["build_sha256"], extractor_version=EXTRACTOR,
                            config_json=canonical_json(config), config_sha256=config_sha, status=payload["status"], page_count=payload["page_count"],
                            text_page_count=payload["text_page_count"], flags_json=canonical_json(payload["flags"]), artifact_sha256=digest,
                            artifact_bytes=len(data), document_text_sha256=payload["document_text_sha256"], created_by=actor,
                            created_at_utc=kb.now(), semantic_sha256=key)
            kb.insert("parse_artifact", artifact)
            run = self._record_run(asset=asset_sha256, gates=gates, purpose=purpose, outcome="PRODUCED", reasons=[], artifact_id=artifact["artifact_id"],
                                   rt=rt, config_sha=config_sha, sandbox_meta=meta, actor=actor)
        return dict(summary, outcome="PRODUCED", run_id=run["run_id"], artifact_id=artifact["artifact_id"], artifact_sha256=digest,
                    status=payload["status"], page_count=payload["page_count"], flags=payload["flags"], bytes_opened=True)

    def _fail(self, summary, gates, purpose, asset, reasons, rt, config_sha, meta, actor) -> dict:
        with self.kb.transaction():
            run = self._record_run(asset=asset, gates=gates, purpose=purpose, outcome="FAILED", reasons=reasons, rt=rt, config_sha=config_sha,
                                   sandbox_meta=meta, actor=actor)
        return dict(summary, outcome="FAILED", reasons=sorted(set(reasons)), run_id=run["run_id"], artifact_id=None)

    def _run_sandbox(self, digest: str, rt: dict) -> dict:
        """Verify CAS, stage a private read-only copy (hash-checked), run the child, re-check both copies, clean up."""
        kb = self.kb
        try:
            kb.cas.verify_object(digest)
        except IntegrityError as exc:
            return {"status": "FAILED", "code": exc.code, "sandbox": {}}
        source = kb.cas.path_for(digest)
        work = kb.root / "staging" / ("parse-" + kb.ids.new("pzs"))
        work.mkdir(parents=True)
        staged = work / "input.pdf"
        try:
            h = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(source, flags)
            with os.fdopen(fd, "rb") as src, open(staged, "xb") as out:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    h.update(block)
                    out.write(block)
            if h.hexdigest() != digest:
                return {"status": "FAILED", "code": "STAGED_COPY_MISMATCH", "sandbox": {}}
            os.chmod(staged, stat.S_IREAD)
            if "after_stage" in self.faults:
                self.faults["after_stage"](staged=staged)
            parser_copy = self._stage_parser(rt, work)
            if parser_copy is None:
                return {"status": "FAILED", "code": "PARSER_IDENTITY_MISMATCH", "sandbox": {}}
            result = sandbox.run_child(staged, str(parser_copy), mode="extract", limits=self.limits, expected_identity=runtime.identity(rt),
                                       allow_privileged=self.allow_privileged)
            if "after_child" in self.faults:
                self.faults["after_child"](staged=staged)
            if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
                return {"status": "FAILED", "code": "STAGED_COPY_CHANGED", "sandbox": result.get("sandbox", {})}
            try:
                kb.cas.verify_object(digest)
            except IntegrityError:
                return {"status": "FAILED", "code": "CAS_CHANGED_DURING_PARSE", "sandbox": result.get("sandbox", {})}
            return result
        finally:
            if staged.exists():
                os.chmod(staged, stat.S_IREAD | stat.S_IWRITE)
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _stage_parser(rt, work):
        """Private copy of the verified parser for this run, re-verified (closes the verify-then-import window)."""
        source, dest = Path(rt["site"]), work / "parser"
        dest.mkdir()
        if rt["mode"] == "OFFICIAL_WHEEL":
            copy = dest / source.name
            shutil.copyfile(source, copy)
        else:
            copy = dest
            shutil.copytree(source / "pypdf", dest / "pypdf", ignore=shutil.ignore_patterns("__pycache__"))
            dist = next(source.glob("pypdf-*.dist-info"))
            shutil.copytree(dist, dest / dist.name)
        try:
            again = runtime.verify_site(str(copy))
        except ValidationError:
            return None
        return copy if runtime.identity(again) == runtime.identity(rt) else None

    def _payload(self, asset, rt, config, config_sha, result) -> dict:
        pages, text_pages, flags = [], 0, set(ALWAYS_FLAGS)
        for number, page in enumerate(result["pages"], 1):
            text = page["text"]
            blocks = []
            for i, b in enumerate(page["blocks"]):
                blocks.append({"i": i, "start": b["start"], "end": b["end"], "text_sha256": _sha(text[b["start"]:b["end"]]),
                               "origin_cpt": b["origin_cpt"], "font_size_cpt": b["font_size_cpt"], "span_count": b["span_count"],
                               "kind": "LINE"})
            pflags = sorted(set(page["flags"]))
            if page["has_text"]:
                text_pages += 1
            flags |= set(pflags)
            pages.append({"page": number, "status": "TEXT" if page["has_text"] else "NO_TEXT_LAYER", "text": text, "text_sha256": _sha(text),
                          "char_count": len(text), "has_images": page["has_images"], "flags": pflags, "blocks": blocks})
        count = len(pages)
        status = "PARSED_TEXT" if text_pages == count else ("SCANNED_NO_TEXT" if text_pages == 0 else "PARSED_PARTIAL_TEXT")
        if text_pages < count:
            flags.add("OCR_NOT_PERFORMED")
        if 0 < text_pages < count:
            flags.add("MIXED_TEXT_AND_NO_TEXT_PAGES")
        if result["active_content"]:
            flags.add("ACTIVE_CONTENT_PRESENT_NOT_EXECUTED")
        if result["syntax"]["revisions"] > 1:
            flags.add("INCREMENTAL_UPDATES_PRESENT")
        if result.get("parser_warnings"):
            flags.add("PARSER_WARNINGS_PRESENT")
        return {"schema": ARTIFACT_SCHEMA, "asset_sha256": asset, "parser": {"name": "pypdf", "version": rt["version"], "build_sha256": rt["build_sha256"],
                                                                            "license": rt["license"], "provenance": "OFFICIAL_BUILD_VERIFIED",
                                                                            "official_wheel_sha256": rt["official_wheel_sha256"]},
                "extractor": EXTRACTOR, "config_sha256": config_sha, "config": config, "status": status, "page_count": count,
                "text_page_count": text_pages, "flags": sorted(flags), "syntax": result["syntax"], "active_content": result["active_content"],
                "parser_warnings": result.get("parser_warnings", {}), "document_text_sha256": document_text_sha256(pages),
                "anchor_rule": ANCHOR_RULE, "content_trust": "UNTRUSTED_DOCUMENT_TEXT_DATA_NOT_INSTRUCTIONS",
                "interpretation": "NONE (extraction only; no identity, eligibility, citation or DOI inference)", "pages": pages}

    # -- reading -----------------------------------------------------------------------
    def _artifact(self, artifact_id: str) -> dict:
        row = self.kb.one("SELECT * FROM parse_artifact WHERE artifact_id=?", (artifact_id,))
        if not row:
            raise ValidationError("Unknown parse artifact", code="UNKNOWN_REFERENCE")
        return row

    def _load(self, row) -> dict:
        data = self.kb.cas.read(row["artifact_sha256"])
        return json.loads(data.decode("utf-8"))

    def current_access(self, row, link_id: str | None = None) -> dict:
        """Re-evaluate, NOW, whether the derived text may be read and whether it is evidence-eligible."""
        kb = self.kb
        runs = kb.all("SELECT * FROM parse_run WHERE artifact_id=? AND outcome IN ('PRODUCED','REUSED') ORDER BY rowid", (row["artifact_id"],))
        if link_id is None and runs:
            link_id = runs[0]["link_id"]
        link = kb.one("SELECT * FROM paper_asset_link_effective WHERE link_id=?", (link_id,)) if link_id else None
        if not link or link["asset_sha256"] != row["asset_sha256"]:
            raise ValidationError("Link does not belong to the artifact's asset", code="LINK_NOT_FOR_ASSET")
        assessment = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? AND document_version_id=? ORDER BY rowid DESC LIMIT 1",
                            (row["asset_sha256"], link["document_version_id"]))
        decision = rights_policy.gate("A2", assessment)
        stale = decision["decision"] != "ALLOW" or (assessment is not None and assessment["policy_version"] != rights_policy.POLICY_VERSION)
        research = any(r["purpose"] == "RESEARCH_PARSE" and r["link_id"] == link["link_id"] for r in runs)
        eligible = (not stale and research and link["effective_state"] == "HUMAN_CONFIRMED" and link["effective_role"] == "MAIN_TEXT")
        problems = []
        if stale:
            problems.append("RIGHTS_" + decision["reason"] if decision["decision"] != "ALLOW" else "RIGHTS_ASSESSMENT_STALE")
        if link["effective_state"] != "HUMAN_CONFIRMED":
            problems.append("IDENTITY_NOW_" + link["effective_state"])
        if link["effective_role"] != "MAIN_TEXT":
            problems.append("ROLE_NOW_" + link["effective_role"])
        if not research:
            problems.append("NO_RESEARCH_PARSE_RUN_FOR_LINK")
        return {"link_id": link["link_id"], "text_readable": not stale, "stale_derived_content": stale, "evidence_eligible_now": eligible,
                "current_rights_assessment_id": assessment["assessment_id"] if assessment else None, "rights_gate": decision["reason"],
                "assessment": assessment, "problems": sorted(problems)}

    def metadata(self, row) -> dict:
        return {k: row[k] for k in ("artifact_id", "asset_sha256", "artifact_schema", "parser_name", "parser_version", "parser_build_sha256",
                                    "extractor_version", "config_sha256", "status", "page_count", "text_page_count", "artifact_sha256",
                                    "artifact_bytes", "document_text_sha256", "created_at_utc")} | {"flags": json.loads(row["flags_json"])}

    def show(self, *, artifact_id: str, link_id: str | None = None, with_text: bool = False, page: int | None = None) -> dict:
        row = self._artifact(artifact_id)
        access = self.current_access(row, link_id)
        payload = self._load(row)
        pages = payload["pages"] if page is None else [p for p in payload["pages"] if p["page"] == page]
        if page is not None and not pages:
            raise ValidationError("No such page", code="PAGE_OUT_OF_RANGE")
        out_pages = []
        for p in pages:
            item = {k: p[k] for k in ("page", "status", "text_sha256", "char_count", "has_images", "flags")}
            item["blocks"] = [{k: b[k] for k in ("i", "start", "end", "text_sha256", "origin_cpt", "font_size_cpt", "kind")} for b in p["blocks"]]
            if with_text and access["text_readable"]:
                item["text"] = p["text"]
            out_pages.append(item)
        result = {"artifact": self.metadata(row), "access": {k: v for k, v in access.items() if k != "assessment"}, "pages": out_pages,
                  "syntax": payload["syntax"], "active_content": payload["active_content"], "content_trust": payload["content_trust"]}
        if with_text and not access["text_readable"]:
            result["text_withheld"] = "DERIVED_CONTENT_WITHHELD:" + ",".join(access["problems"])
        return result

    def status(self, *, asset_sha256: str) -> dict:
        kb = self.kb
        return {"asset_sha256": asset_sha256,
                "artifacts": [self.metadata(r) for r in kb.all("SELECT * FROM parse_artifact WHERE asset_sha256=? ORDER BY rowid", (asset_sha256,))],
                "runs": [{k: r[k] for k in ("run_id", "purpose", "outcome", "artifact_id", "evidence_eligibility", "link_id", "created_at_utc")}
                         | {"reasons": json.loads(r["reason_codes_json"])}
                         for r in kb.all("SELECT * FROM parse_run WHERE requested_asset_sha256=? ORDER BY rowid", (asset_sha256,))]}

    def verify(self, *, artifact_id: str) -> dict:
        """Re-hash the CAS bytes, check the payload against the row, and round-trip EVERY block anchor."""
        row = self._artifact(artifact_id)
        problems = []
        try:
            payload = self._load(row)
        except KBError as exc:
            return {"artifact_id": artifact_id, "valid": False, "problems": [exc.code]}
        if (canonical_json(payload) + "\n").encode("utf-8") != self.kb.cas.read(row["artifact_sha256"]):
            problems.append("NOT_CANONICAL_JSON")
        for key, col in (("schema", "artifact_schema"), ("asset_sha256", "asset_sha256"), ("status", "status"), ("page_count", "page_count"),
                         ("text_page_count", "text_page_count"), ("config_sha256", "config_sha256"), ("document_text_sha256", "document_text_sha256")):
            if payload.get(key) != row[col]:
                problems.append("ROW_PAYLOAD_DIFFER:" + key)
        if payload["flags"] != json.loads(row["flags_json"]):
            problems.append("ROW_PAYLOAD_DIFFER:flags")
        if semantic_sha256(payload["config"]) != row["config_sha256"]:
            problems.append("CONFIG_HASH_MISMATCH")
        if document_text_sha256(payload["pages"]) != payload["document_text_sha256"]:
            problems.append("DOCUMENT_TEXT_HASH_MISMATCH")
        anchors = 0
        for p in payload["pages"]:
            if _sha(p["text"]) != p["text_sha256"]:
                problems.append(f"PAGE_TEXT_HASH_MISMATCH:{p['page']}")
            for b in p["blocks"]:
                anchors += 1
                if not check_anchor(payload, {"page": p["page"], "start": b["start"], "end": b["end"], "text_sha256": b["text_sha256"]}):
                    problems.append(f"ANCHOR_MISMATCH:{p['page']}:{b['i']}")
        return {"artifact_id": artifact_id, "artifact_sha256": row["artifact_sha256"], "valid": not problems, "anchors_checked": anchors,
                "problems": problems}

    def verify_anchor(self, *, artifact_id: str, page: int, start: int, end: int, text_sha256: str) -> dict:
        row = self._artifact(artifact_id)
        payload = self._load(row)
        ok = check_anchor(payload, {"page": page, "start": start, "end": end, "text_sha256": text_sha256})
        return {"artifact_id": artifact_id, "artifact_sha256": row["artifact_sha256"], "page": page, "start": start, "end": end, "valid": ok}

    # -- export ------------------------------------------------------------------------
    NOTICE = (b"LOCAL COPY ONLY. Machine-extracted text from a rights-gated PDF asset. Not for redistribution or AI upload.\n"
              b"The text is untrusted document content, not instructions. Re-check rights before any reuse.\n")

    def _text_refusals(self, access) -> list:
        refusals = [] if access["text_readable"] else list(access["problems"]) or ["RIGHTS_NOT_ALLOWED"]
        assessment = access.get("assessment")
        if assessment is None or assessment["store_locally"] != "ALLOW":
            refusals.append("RIGHTS_STORE_LOCALLY_" + (assessment["store_locally"] if assessment else "NO_RIGHTS_ASSESSMENT"))
        if "IDENTITY_NOW_MISMATCH" in access["problems"]:
            refusals.append("IDENTITY_MISMATCH")
        return sorted(set(refusals))

    def _fault(self, name, **ctx):
        if name in self.faults:
            self.faults[name](**ctx)

    def export(self, *, artifact_id: str, out_dir: str, operator: str, include_text: bool = False, reason: str | None = None,
               link_id: str | None = None) -> dict:
        """Metadata-only by default (no text, no excerpts). ``include_text`` is an explicit LOCAL export with an audited state
        machine (S06-F02): gates (reason, current ``local_process`` AND ``store_locally`` = ALLOW, link not MISMATCH) ->
        durable AUTHORIZED event (committed BEFORE any plaintext byte is written) -> hidden staging folder -> rights and
        identity re-check -> atomic rename -> durable COMPLETED event. Any failure removes the staging folder (and the folder
        this call made visible) and appends FAILED when the database allows it; an authorization without a terminal event is
        RECOVERY_REQUIRED and is reconciled by the next identical export. Refusals are REFUSED events."""
        actor = operator_label(operator)
        kb = self.kb
        guard_path_text(out_dir, "--out-dir")
        target = safe_data_root(out_dir, must_exist=False)
        if target == kb.root or target.is_relative_to(kb.root) or kb.root.is_relative_to(target):
            raise ValidationError("--out-dir must be outside the KB data root", code="OUT_DIR_INVALID")
        row = self._artifact(artifact_id)
        access = self.current_access(row, link_id)
        payload = self._load(row)
        files = {}
        meta = {"schema": "rkb-parse-export/1", "artifact": self.metadata(row), "access_at_export": {k: v for k, v in access.items() if k != "assessment"},
                "pages": [{"page": p["page"], "status": p["status"], "char_count": p["char_count"], "text_sha256": p["text_sha256"], "flags": p["flags"],
                           "anchors": [{"start": b["start"], "end": b["end"], "text_sha256": b["text_sha256"]} for b in p["blocks"]]}
                          for p in payload["pages"]],
                "contains_text": bool(include_text)}
        if include_text:
            reason = require_reason(reason)
            refusals = self._text_refusals(access)
            if refusals:
                with kb.transaction():
                    self._export_event(row, access, "REFUSED", refusals, None, target, reason, actor, state="REFUSED")
                raise PolicyRefusal("Local text export refused: " + ",".join(refusals), code="TEXT_EXPORT_REFUSED")
            files["parse_artifact.json"] = kb.cas.read(row["artifact_sha256"])
            for p in payload["pages"]:
                files[f"page-{p['page']:04d}.txt"] = p["text"].encode("utf-8")
            files["NOTICE.txt"] = self.NOTICE
        files["parse_metadata.json"] = (canonical_json(meta) + "\n").encode("utf-8")
        manifest = {name: sha256_bytes(data) for name, data in sorted(files.items())}
        files["SHA256SUMS.txt"] = "".join(f"{d}  {n}\n" for n, d in manifest.items()).encode()
        digest = sha256_bytes(files["SHA256SUMS.txt"])
        folder = target / f"parse_export_{artifact_id[-8:].lower()}_{'text' if include_text else 'metadata'}_{digest[:8]}"
        result = {"artifact_id": artifact_id, "folder_name": folder.name, "contains_text": bool(include_text), "export_manifest_sha256": digest,
                  "files": sorted(files), "path_hash": path_hash(target)}
        if not include_text:
            if folder.exists():
                if self._present(folder) != files:
                    raise ValidationError("Export folder exists with different content", code="EXPORT_FOLDER_EXISTS_DIFFERENT")
                return dict(result, written=False)
            staging = self._stage(target, folder, files)
            os.replace(staging, folder)
            return dict(result, written=True)
        if folder.exists():
            return self._reconcile_text_folder(row, access, files, digest, target, folder, reason, actor, result)
        # 1. Durable authorization BEFORE any plaintext exists outside the KB. A failure here raises with nothing written.
        with kb.transaction():
            auth = self._export_event(row, access, "ALLOWED", [], digest, target, reason, actor, state="AUTHORIZED", folder=folder.name)
        auth_id = auth["event_id"] if isinstance(auth, dict) else None
        staging, visible = None, False
        try:
            self._close_interrupted(row, access, digest, target, folder, reason, actor, exclude=auth_id)
            self._fault("export_before_stage", target=target)
            staging = self._stage(target, folder, files)
            self._fault("export_after_stage", staging=staging)
            again = self.current_access(row, access["link_id"])
            changed = self._text_refusals(again)
            if changed:
                raise PolicyRefusal("Rights or identity changed during the export: " + ",".join(changed), code="TEXT_EXPORT_REFUSED")
            self._fault("export_before_rename", staging=staging, folder=folder)
            if folder.exists():
                raise ValidationError("Export folder appeared during the export", code="EXPORT_FOLDER_EXISTS_DIFFERENT")
            os.replace(staging, folder)
            staging, visible = None, True
            self._fault("export_after_rename", folder=folder)
            # 2. Durable completion; if it cannot be recorded, the plaintext this call made visible is withdrawn.
            self._fault("export_before_complete", folder=folder)
            with kb.transaction():
                self._export_event(row, again, "ALLOWED", [], digest, target, reason, actor, state="COMPLETED", folder=folder.name,
                                   authorization=auth_id)
        except BaseException as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if visible and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            failed_recorded = False
            try:
                with kb.transaction():
                    self._export_event(row, access, "ALLOWED", ["EXPORT_FAILED:" + str(code)[:60]], digest, target, reason, actor, state="FAILED",
                                       folder=folder.name, authorization=auth_id, detail={"withdrawn_visible_folder": visible})
                failed_recorded = True
            except BaseException:          # the database itself is failing: the authorization stays open (RECOVERY_REQUIRED)
                pass
            if isinstance(exc, KBError):
                raise
            raise IntegrityError(f"Local text export failed and was rolled back ({code}); failure recorded: {failed_recorded}",
                                 code="EXPORT_FAILED_ROLLED_BACK") from exc
        return dict(result, written=True, audit_state="COMPLETED", authorization_event_id=auth_id)

    @staticmethod
    def _present(folder) -> dict:
        return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    def _stage(self, target, folder, files) -> Path:
        """A hidden, exclusively created staging folder named after the export (never a caller-owned path)."""
        target.mkdir(parents=True, exist_ok=True)
        staging = target / f".{folder.name}.partial-{os.urandom(6).hex()}"
        staging.mkdir()
        try:
            for name, data in sorted(files.items()):
                with open(staging / name, "xb") as handle:
                    handle.write(data)
                    handle.flush()
                    self._fault("export_during_write", path=staging / name)
                    os.fsync(handle.fileno())
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return staging

    def _open_authorizations(self, artifact_id, digest, target):
        return self.kb.all(
            "SELECT a.* FROM parse_export_event a WHERE a.state='AUTHORIZED' AND a.artifact_id=? AND a.export_manifest_sha256=? AND a.target_path_sha256=? "
            "AND NOT EXISTS (SELECT 1 FROM parse_export_event t WHERE t.authorization_event_id = a.event_id) ORDER BY a.rowid",
            (artifact_id, digest, path_hash(target)))

    def _close_interrupted(self, row, access, digest, target, folder, reason, actor, *, exclude):
        """Earlier identical attempts that never finished (crash before rename): remove their rkb-named staging folders and
        append FAILED(INTERRUPTED_BEFORE_COMPLETION) so no authorization stays open."""
        open_auth = [a for a in self._open_authorizations(row["artifact_id"], digest, target) if a["event_id"] != exclude]
        if not open_auth:
            return
        for leftover in (target.glob(f".{folder.name}.partial-*") if target.exists() else []):   # rkb-named staging only
            shutil.rmtree(leftover, ignore_errors=True)
        with self.kb.transaction():
            for a in open_auth:
                self._export_event(row, access, "ALLOWED", ["INTERRUPTED_BEFORE_COMPLETION"], digest, target, reason, actor, state="FAILED",
                                   folder=folder.name, authorization=a["event_id"], detail={"recovered": True})

    def _reconcile_text_folder(self, row, access, files, digest, target, folder, reason, actor, result) -> dict:
        """An identical folder is accepted only against audit state; it is never silently treated as audited or deleted."""
        if self._present(folder) != files:
            raise ValidationError("Export folder exists with different content", code="EXPORT_FOLDER_EXISTS_DIFFERENT")
        done = self.kb.one("SELECT event_id FROM parse_export_event WHERE state='COMPLETED' AND artifact_id=? AND export_manifest_sha256=? "
                           "AND target_path_sha256=? ORDER BY rowid LIMIT 1", (row["artifact_id"], digest, path_hash(target)))
        if done:
            return dict(result, written=False, audit_state="COMPLETED", code="EXPORT_ALREADY_PRESENT_AUDITED", completion_event_id=done["event_id"])
        open_auth = self._open_authorizations(row["artifact_id"], digest, target)
        if not open_auth:
            raise PolicyRefusal("An identical export folder exists without any audit record; it is left untouched",
                                code="EXPORT_FOLDER_UNAUDITED")
        with self.kb.transaction():
            self._export_event(row, access, "ALLOWED", [], digest, target, reason, actor, state="COMPLETED", folder=folder.name,
                               authorization=open_auth[0]["event_id"], detail={"recovered_after_interruption": True})
        return dict(result, written=False, audit_state="COMPLETED", code="EXPORT_RECOVERED_AND_COMPLETED")

    def export_status(self) -> dict:
        """Authorizations without a COMPLETED/FAILED event are RECOVERY_REQUIRED (an interrupted or un-auditable export)."""
        rows = self.kb.all("SELECT a.event_id, a.artifact_id, a.export_folder_name, a.created_at_utc FROM parse_export_event a WHERE a.state='AUTHORIZED' "
                           "AND NOT EXISTS (SELECT 1 FROM parse_export_event t WHERE t.authorization_event_id = a.event_id) ORDER BY a.rowid")
        counts = {r["state"]: r["n"] for r in self.kb.all("SELECT state, count(*) n FROM parse_export_event GROUP BY state")}
        return {"counts": counts, "recovery_required": rows}

    def _export_event(self, row, access, decision, reasons, digest, target, reason, actor, *, state, folder=None, authorization=None, detail=None):
        kb = self.kb
        ev = dict(event_id=kb.ids.new("pze"), artifact_id=row["artifact_id"], link_id=access["link_id"], kind="LOCAL_TEXT_EXPORT", decision=decision,
                  state=state, authorization_event_id=authorization, export_folder_name=folder,
                  reason_codes_json=canonical_json(sorted(set(reasons))), rights_assessment_id=access["current_rights_assessment_id"],
                  target_path_sha256=path_hash(target), export_manifest_sha256=digest, operator_reason=reason, detail_json=canonical_json(detail or {}),
                  actor=actor, created_at_utc=kb.now())
        ev["semantic_sha256"] = semantic_sha256(ev)
        kb.insert("parse_export_event", ev)
        return ev

def python_info() -> dict:
    return {"python": sys.version.split()[0], "platform": sys.platform}
