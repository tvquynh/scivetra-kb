"""DocumentVersions, explicit synthetic-file registration (G0-G4), PaperAssetLinks and rights assessments.

Intake is explicit only: the operator names a fixture root and one relative file. Nothing is scanned. G0 path and
file checks run before any byte is read; the file is streamed once into ``<data_root>/staging`` while hashing,
and its identity (device, inode, size, mtime) is compared before and after (TOCTOU, FILE_CHANGED_DURING_IMPORT).
G1 runs on the staged copy inside the sandbox child; G2 streams the staged copy into CAS and re-hashes it.
G3 identity and the PAL-1 rule decide the link state. G4 records at least an UNKNOWN rights assessment.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import date
from pathlib import Path

from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, KBError, ValidationError
from ..core.paths import check_no_links_on_path, is_reparse_or_link, refuse_unsafe_path_text, safe_child, safe_data_root
from ..library.common import insert_chained, json_text, latest, operator_label, require_reason, sem_of
from ..library.service import LibraryService
from ..rights import policy as rights_policy
from . import identity, sandbox

DEFAULT_MAX_BYTES = 100 * 1024 * 1024   # legacy DEFAULT_MAX_MB (S01 05 G0)
ROLES = ("MAIN_TEXT", "SUPPLEMENTARY", "ERRATUM_TEXT", "COVER_OR_ABSTRACT_ONLY", "UNKNOWN_ROLE")
VERSION_CLASSES = ("SUBMITTED", "ACCEPTED", "PUBLISHED", "UPDATED_PUBLISHED", "UNKNOWN")
# Same allowlist as the document_version_evidence_sources trigger in migration 0002.
VERSION_SOURCES = ("CROSSREF_LICENSE_CONTENT_VERSION", "OPENALEX_LOCATION_VERSION", "UNPAYWALL_LOCATION_VERSION", "DATACITE_VERSION_CLAIM",
                   "ARXIV_VERSION_STAMP", "PUBLISHER_PAGE_HUMAN", "PDF_WATERMARK_HUMAN")


def _fault(faults, name, **ctx):
    if faults and name in faults:
        faults[name](**ctx)


def _identity(st) -> tuple:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


class AssetService:
    def __init__(self, kb, *, pypdf_site: str | None = None, pdf_validator: str = "1", parser_site: str | None = None,
                 parser_allow_privileged: bool = False):
        if pdf_validator not in ("1", "2"):
            raise ValidationError("pdf_validator must be 1 (legacy) or 2 (S06 parser-backed)", code="PDF_VALIDATOR_INVALID")
        self.kb = kb
        self.library = LibraryService(kb)
        self.pypdf_site = pypdf_site
        self.pdf_validator, self.parser_site = pdf_validator, parser_site
        self.parser_allow_privileged = parser_allow_privileged

    # -- document versions ---------------------------------------------------
    def add_document_version(self, *, paper_id: str, version_class: str, evidence: list, operator: str, version_label: str | None = None,
                             supersedes_version_id: str | None = None) -> dict:
        """DV-1: PUBLISHED/ACCEPTED/... need evidence; filename, URL, host or login state are never version evidence."""
        actor = operator_label(operator)
        if version_class not in VERSION_CLASSES:
            raise ValidationError("Unknown version class", code="VERSION_CLASS_INVALID")
        if not isinstance(evidence, list) or any(not isinstance(e, dict) or set(e) - {"source", "claim_ref", "value"}
                                                  or e.get("source") not in VERSION_SOURCES or not e.get("claim_ref") or e.get("value") is None
                                                  for e in evidence):
            raise ValidationError("Version evidence items need source (allowlisted), claim_ref and value; filename/URL/host are not evidence",
                                  code="VERSION_EVIDENCE_INVALID")
        if version_class != "UNKNOWN" and not evidence:
            raise ValidationError("A known version class needs evidence (DV-1); use UNKNOWN", code="VERSION_EVIDENCE_REQUIRED")
        kb = self.kb
        paper_sem = sem_of(kb, "global_paper", "paper_id", paper_id)
        content = {"paper": paper_sem, "class": version_class, "evidence": evidence, "label": version_label,
                   "supersedes": sem_of(kb, "document_version", "document_version_id", supersedes_version_id) if supersedes_version_id else None}
        with kb.transaction():
            row, created = kb.insert_unique("document_version", dict(
                document_version_id=kb.ids.new("dv"), paper_id=paper_id, version_class=version_class, version_evidence_json=json_text(evidence),
                version_label=version_label, supersedes_version_id=supersedes_version_id, created_by=actor, created_at_utc=kb.now(),
                semantic_sha256=semantic_sha256(content)))
        return {"document_version_id": row["document_version_id"], "version_class": version_class, "created": created}

    # -- G0 ----------------------------------------------------------------------
    def _resolve_input(self, fixture_root: str, relative_path: str, max_bytes: int):
        refuse_unsafe_path_text(fixture_root)
        root = safe_data_root(fixture_root, must_exist=True)
        if root.parent == root:
            raise ValidationError("A filesystem root is not a fixture root (no drive-wide intake)", code="FIXTURE_ROOT_TOO_BROAD")
        if root == self.kb.root or root.is_relative_to(self.kb.root) or self.kb.root.is_relative_to(root):
            raise ValidationError("Fixture root must be outside the KB data root", code="FIXTURE_ROOT_INSIDE_KB")
        if not isinstance(relative_path, str) or "\\" in relative_path and os.name != "nt":
            raise ValidationError("Relative path invalid", code="PATH_OUTSIDE_ROOT")
        raw = root / relative_path
        if is_reparse_or_link(raw):
            raise ValidationError("Symlink, junction or reparse point refused", code="REPARSE_POINT")
        check_no_links_on_path(raw)
        target = safe_child(root, relative_path)
        st = os.lstat(target) if target.exists() else None
        if st is None or not stat.S_ISREG(st.st_mode):
            raise ValidationError("Input is not a regular file", code="NOT_REGULAR_FILE")
        if st.st_size == 0:
            raise ValidationError("Input file is empty", code="EMPTY")
        if st.st_size > max_bytes:
            raise ValidationError("Input file exceeds max_bytes", code="TOO_LARGE")
        return root, target, st

    def _stage(self, target: Path, st, max_bytes: int, faults) -> tuple[Path, str, int]:
        staging = self.kb.root / "staging"
        staging.mkdir(exist_ok=True)
        part = staging / (self.kb.ids.new("stg") + ".part")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags)
        try:
            if _identity(os.fstat(fd)) != _identity(st):
                raise ValidationError("File changed between validation and open", code="FILE_CHANGED_DURING_IMPORT")
            digest, size = hashlib.sha256(), 0
            with os.fdopen(os.dup(fd), "rb") as src, open(part, "xb") as out:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    size += len(block)
                    if size > max_bytes:
                        raise ValidationError("Input grew beyond max_bytes while reading", code="FILE_CHANGED_DURING_IMPORT")
                    digest.update(block)
                    out.write(block)
                    _fault(faults, "during_copy", target=target)
                out.flush()
                os.fsync(out.fileno())
            _fault(faults, "after_copy", target=target)
            after_fd, after_path = _identity(os.fstat(fd)), _identity(os.lstat(target))
        finally:
            os.close(fd)
        if after_fd != _identity(st) or after_path != _identity(st) or size != st.st_size:
            part.unlink()
            raise ValidationError("File changed during import", code="FILE_CHANGED_DURING_IMPORT")
        return part, digest.hexdigest(), size

    # -- registration --------------------------------------------------------
    def register_asset(self, *, fixture_root: str, relative_path: str, document_version_id: str, operator: str, role: str = "MAIN_TEXT",
                       access_route: str = "UNKNOWN", access_barriers=(), max_bytes: int = DEFAULT_MAX_BYTES,
                       parser_timeout: float = sandbox.DEFAULT_TIMEOUT_SECONDS, parser_memory: int = sandbox.DEFAULT_MEMORY_BYTES,
                       require_human_identity: bool = False, faults=None) -> dict:
        actor = operator_label(operator)
        kb = self.kb
        if role not in ROLES:
            raise ValidationError("Unknown link role", code="ROLE_INVALID")
        if access_route not in rights_policy.ACCESS_ROUTES or not all(isinstance(b, str) and len(b) <= 64 for b in access_barriers):
            raise ValidationError("access_route/access_barriers invalid", code="ACCESS_PROVENANCE_INVALID")
        version = kb.one("SELECT * FROM document_version WHERE document_version_id=?", (document_version_id,))
        if not version:
            raise ValidationError("Unknown document version", code="UNKNOWN_REFERENCE")
        base_context = {"relative_name_sha256": sha256_bytes(relative_path.encode("utf-8")), "role": role}
        try:
            _, target, st = self._resolve_input(fixture_root, relative_path, max_bytes)
            part, digest, size = self._stage(target, st, max_bytes, faults)
        except KBError as exc:
            self.library.record_rejection(kind="MANUAL_FILE", key=sha256_bytes(canonical_json(["G0", base_context]).encode()), inputs=[],
                                          reason=exc.code, context={"gate": "G0", **base_context}, actor=actor)
            raise
        try:
            return self._register_staged(part, digest, size, version, role, actor, access_route, list(access_barriers), parser_timeout,
                                         parser_memory, require_human_identity, base_context, faults)
        finally:
            if part.exists():
                part.unlink()

    def _register_staged(self, part, digest, size, version, role, actor, access_route, barriers, timeout, memory, require_human, context, faults):
        kb = self.kb
        key = sha256_bytes(canonical_json([digest, version["semantic_sha256"], role]).encode())
        if self.pdf_validator == "2":
            g1 = sandbox.validate_file_v2(part, parser_site=self.parser_site, timeout=timeout, memory=memory,
                                          allow_privileged=self.parser_allow_privileged)
        else:
            g1 = sandbox.validate_file(part, timeout=timeout, memory=memory, pypdf_site=self.pypdf_site)
        result = {k: v for k, v in g1["result"].items() if k != "pages_text"}     # page text is never persisted
        if g1["outcome"] == "REJECTED":
            with kb.transaction():
                event = self.library.record_import_event(kind="MANUAL_FILE", key=key, inputs=[digest], outcome="REJECTED", reasons=g1["reasons"],
                                                         context={"gate": "G1", **context}, actor=actor)
                kb.insert("asset_validation_event", dict(validation_id=kb.ids.new("ave"), input_sha256=digest, asset_sha256=None,
                                                         validator=result.get("validator", "rkb-pdf-structure"),
                                                         validator_version=result.get("validator_version", "1"), outcome="REJECTED",
                                                         reason_codes_json=json_text(g1["reasons"]), result_json=json_text(result),
                                                         import_event_id=event["import_event_id"], validated_at_utc=kb.now()))
            return {"outcome": "REJECTED", "gate": "G1", "reasons": g1["reasons"], "input_sha256": digest, "asset_registered": False}
        _fault(faults, "before_cas", part=part)
        try:
            kb.cas.put_file(part, digest)
        except IntegrityError as exc:
            with kb.transaction():
                self.library.record_import_event(kind="MANUAL_FILE", key=key, inputs=[digest], outcome="QUARANTINED", reasons=[exc.code],
                                                 context={"gate": "G2", **context}, actor=actor)
            raise
        paper_id = version["paper_id"]
        facts = self._identity_facts(paper_id)
        verdict = identity.assess(g1["result"], facts, role=role, version_class=version["version_class"], require_human=require_human)
        with kb.transaction():
            existing_link = kb.one("SELECT * FROM paper_asset_link WHERE asset_sha256=? AND document_version_id=? AND link_role=?",
                                   (digest, version["document_version_id"], role))
            event = self.library.record_import_event(kind="MANUAL_FILE", key=key, inputs=[digest],
                                                     outcome="DUPLICATE_NOOP" if existing_link else "REGISTERED", reasons=[],
                                                     context={"gate": "G2", **context}, actor=actor, asset=digest)
            if existing_link:
                return {"outcome": "DUPLICATE_NOOP", "asset_sha256": digest, "link_id": existing_link["link_id"], "asset_registered": True}
            asset, asset_created = kb.insert_unique("pdf_asset", dict(
                asset_sha256=digest, byte_length=size, storage_key=f"cas/sha256/{digest[:2]}/{digest}", media_format="PDF",
                first_import_event_id=event["import_event_id"], first_registered_at_utc=kb.now(), semantic_sha256=semantic_sha256({"asset": digest})),
                key_column="asset_sha256")
            kb.insert("asset_validation_event", dict(validation_id=kb.ids.new("ave"), input_sha256=digest, asset_sha256=digest,
                                                     validator=result["validator"], validator_version=result["validator_version"],
                                                     outcome="STRUCTURALLY_VALID", reason_codes_json="[]", result_json=json_text(result),
                                                     import_event_id=event["import_event_id"], validated_at_utc=kb.now()))
            # PAL-1 / N04: the same bytes already linked as MAIN_TEXT to another paper (not MISMATCH there).
            others = kb.all("SELECT * FROM paper_asset_link_effective WHERE asset_sha256=? AND paper_id<>? AND effective_role='MAIN_TEXT' "
                            "AND effective_state<>'MISMATCH' ORDER BY row_seq", (digest, paper_id))
            state, reasons = verdict["state"], list(verdict["reasons"])
            conflict = None
            if others and role == "MAIN_TEXT":
                state = "AMBIGUOUS"
                reasons = sorted(set(reasons) | {"SAME_BYTES_LINKED_TO_ANOTHER_PAPER"})
            link_content = {"asset": digest, "version": version["semantic_sha256"], "role": role, "state": state, "evidence": verdict["evidence"],
                            "reasons": reasons, "policy": identity.POLICY_VERSION}
            link = dict(link_id=kb.ids.new("pal"), asset_sha256=digest, document_version_id=version["document_version_id"], paper_id=paper_id,
                        link_role=role, identity_state=state, identity_evidence_json=json_text(verdict["evidence"]), reason_codes_json=json_text(reasons),
                        identity_policy_version=identity.POLICY_VERSION, created_from=event["import_event_id"], created_at_utc=kb.now(),
                        semantic_sha256=semantic_sha256(link_content))
            kb.insert("paper_asset_link", link)
            if others and role == "MAIN_TEXT":
                papers = [paper_id] + [o["paper_id"] for o in others]
                conflict, _ = self.library.raise_conflict(kind="SAME_BYTES_MULTIPLE_PAPERS", key=("asset_sha256", digest), papers=papers, assertions=[],
                                                          detail={"links": sorted(sem_of(kb, "paper_asset_link", "link_id", x) for x in
                                                                                  [link["link_id"]] + [o["link_id"] for o in others])},
                                                          raised_by=link["link_id"], raised_by_sem=link["semantic_sha256"])
                for other in others:
                    if other["effective_state"] != "AMBIGUOUS":
                        self._link_event(other, "AMBIGUOUS", None, ["SAME_BYTES_LINKED_TO_ANOTHER_PAPER"], [], "SYSTEM", conflict["conflict_id"])
            assessment = self._assess(digest, version, rights_policy.validate_evidence(
                {"access_provenance": {"access_route": access_route, "access_barrier_observed": sorted(barriers)}} if (barriers or access_route != "UNKNOWN") else {}),
                actor, "POLICY_ENGINE")
        return {"outcome": "REGISTERED", "asset_sha256": digest, "asset_created": asset_created, "byte_length": size, "link_id": link["link_id"],
                "identity_state": state, "identity_reasons": reasons, "identity_evidence": verdict["evidence"],
                "identity_conflict_id": conflict["conflict_id"] if conflict else None, "rights": assessment, "asset_registered": True}

    def _identity_facts(self, paper_id: str) -> dict:
        kb = self.kb
        dois = sorted({r["value_normalized"] for r in kb.all(
            "SELECT value_normalized FROM identity_assertion_effective WHERE paper_id=? AND namespace='doi' AND effective_status='ACTIVE' "
            "AND value_normalized IS NOT NULL", (paper_id,))})
        titles, families = set(), set()
        for r in kb.all("SELECT item_claims_json FROM project_paper_reference WHERE paper_id=?", (paper_id,)):
            claims = json.loads(r["item_claims_json"])
            titles.update(claims.get("titles", []))
            families.update(claims.get("first_author_families", []))
        for doi in dois:
            for c in kb.all("SELECT field, value_json FROM metadata_claim WHERE subject_namespace='doi' AND subject_value=? AND field IN ('title','author')",
                            (doi,)):
                value = json.loads(c["value_json"])
                if c["field"] == "title" and isinstance(value, str):
                    titles.add(value)
                if c["field"] == "author" and isinstance(value, dict) and value.get("position") in (0, 1) and isinstance(value.get("family"), str):
                    families.add(value["family"])
        return {"dois": dois, "titles": sorted(titles), "first_author_families": sorted(families)}

    # -- human link review -----------------------------------------------------------
    def _link_event(self, link, state, role, reasons, evidence, actor, cause_ref):
        content = {"link": link["semantic_sha256"], "state": state, "role": role, "reasons": reasons, "evidence": evidence, "actor": actor,
                   "cause": cause_ref}
        return insert_chained(self.kb, "paper_asset_link_event", "event_id", "pale",
                              dict(link_id=link["link_id"], new_state=state, new_role=role, reason_codes_json=json_text(sorted(reasons)),
                                   evidence_json=json_text(evidence), actor=actor, cause_ref=cause_ref, recorded_at_utc=self.kb.now()),
                              chain_column="link_id", chain_value=link["link_id"], content=content)

    def review_link(self, *, link_id: str, decision: str, operator: str, reason: str) -> dict:
        """CONFIRM -> HUMAN_CONFIRMED; REJECT -> MISMATCH; MARK_SUPPLEMENT -> role SUPPLEMENTARY, HUMAN_CONFIRMED (05 section 3)."""
        actor, reason = operator_label(operator), require_reason(reason)
        kb = self.kb
        link = kb.one("SELECT * FROM paper_asset_link_effective WHERE link_id=?", (link_id,))
        if not link:
            raise ValidationError("Unknown asset link", code="UNKNOWN_REFERENCE")
        mapping = {"CONFIRM": ("HUMAN_CONFIRMED", None), "REJECT": ("MISMATCH", None), "MARK_SUPPLEMENT": ("HUMAN_CONFIRMED", "SUPPLEMENTARY")}
        if decision not in mapping:
            raise ValidationError("Unknown link review decision", code="LINK_REVIEW_INVALID")
        state, role = mapping[decision]
        if state == "HUMAN_CONFIRMED" and (role or link["effective_role"]) == "MAIN_TEXT" and kb.one(
                "SELECT 1 FROM paper_asset_link_effective WHERE asset_sha256=? AND paper_id<>? AND effective_role='MAIN_TEXT' "
                "AND effective_state<>'MISMATCH'", (link["asset_sha256"], link["paper_id"])):
            raise ValidationError("The same bytes are still linked as main text to another paper; reject that link first (PAL-1)",
                                  code="SAME_BYTES_LINKED_ELSEWHERE")
        with kb.transaction():
            event, created = self._link_event(link, state, role, [decision], [{"reason": reason}], actor, None)
        return {"event_id": event["event_id"], "link_id": link_id, "new_state": state, "new_role": role or link["effective_role"], "created": created}

    # -- rights ------------------------------------------------------------------------
    def _assess(self, digest, version, evidence, actor, assessed_by, owner_approval_ref=None) -> dict:
        kb = self.kb
        verdict = rights_policy.classify(evidence, version_class=version["version_class"], as_of=date.fromisoformat(kb.now()[:10]))
        prior = kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? AND document_version_id=? ORDER BY rowid DESC LIMIT 1",
                       (digest, version["document_version_id"]))
        perms = verdict["permissions"]
        content = {"asset": digest, "version": version["semantic_sha256"], "class": verdict["rights_class"], "permissions": perms,
                   "evidence": verdict["evidence"], "flags": verdict["flags"], "policy": rights_policy.POLICY_VERSION, "assessed_by": assessed_by,
                   "prior": prior["semantic_sha256"] if prior else None}
        sem = semantic_sha256(content)
        if prior and prior["semantic_sha256"] == sem:
            row = prior
        elif prior and json.loads(prior["license_evidence_json"]) == verdict["evidence"]["license_evidence"] and prior["rights_class"] == verdict["rights_class"] \
                and json.loads(prior["terms_declarations_json"]) == verdict["evidence"]["terms_declarations"] \
                and json.loads(prior["access_provenance_json"]) == verdict["evidence"]["access_provenance"] \
                and json.loads(prior["flags_json"]) == verdict["flags"]:
            row = prior    # identical re-assessment: idempotent no-op
        else:
            row = dict(assessment_id=kb.ids.new("rga"), asset_sha256=digest, document_version_id=version["document_version_id"],
                       rights_class=verdict["rights_class"], license_evidence_json=json_text(verdict["evidence"]["license_evidence"]),
                       terms_declarations_json=json_text(verdict["evidence"]["terms_declarations"]),
                       access_provenance_json=json_text(verdict["evidence"]["access_provenance"]), flags_json=json_text(verdict["flags"]),
                       policy_version=rights_policy.POLICY_VERSION, assessed_by=assessed_by, recorded_by=actor, owner_approval_ref=owner_approval_ref,
                       supersedes_assessment_id=prior["assessment_id"] if prior else None, assessed_at_utc=kb.now(), semantic_sha256=sem, **perms)
            kb.insert("rights_assessment", row)
            triggers = []
            if "RIGHTS_EVIDENCE_CONFLICT" in verdict["flags"]:
                triggers.append("RIGHTS_EVIDENCE_CONFLICT")
            elif verdict["rights_class"] == "UNKNOWN":
                triggers.append("RIGHTS_UNKNOWN")
            if verdict["rights_class"] == "UNKNOWN" and verdict["evidence"]["access_provenance"].get("access_barrier_observed"):
                triggers.append("ACCESS_BARRIER_OBSERVED")
            if "LICENCE_VERSION_MISMATCH" in verdict["flags"]:
                triggers.append("LICENCE_VERSION_MISMATCH")
            for trigger in triggers:
                kb.insert_unique("rights_review_task", dict(
                    task_id=kb.ids.new("rrt"), asset_sha256=digest, document_version_id=version["document_version_id"], assessment_id=row["assessment_id"],
                    trigger=trigger, context_json=json_text({"flags": verdict["flags"], "rights_class": verdict["rights_class"],
                                                             "prompt": "record licence evidence or the terms the copy was obtained under"}),
                    created_at_utc=kb.now(), semantic_sha256=semantic_sha256({"assessment": sem, "trigger": trigger})))
        return {"assessment_id": row["assessment_id"], "rights_class": row["rights_class"], "permissions": {p: row[p] for p in rights_policy.PERMISSIONS},
                "flags": json.loads(row["flags_json"]), "evaluation": verdict["evaluation"]}

    def assess_rights(self, *, asset_sha256: str, document_version_id: str, evidence: dict, operator: str) -> dict:
        actor = operator_label(operator)
        kb = self.kb
        version = kb.one("SELECT * FROM document_version WHERE document_version_id=?", (document_version_id,))
        if not version or not kb.one("SELECT 1 FROM paper_asset_link WHERE asset_sha256=? AND document_version_id=?", (asset_sha256, document_version_id)):
            raise ValidationError("Rights attach to an asset linked to this document version", code="UNKNOWN_REFERENCE")
        try:
            evidence = rights_policy.validate_evidence(evidence)
        except rights_policy.EvidenceError as exc:
            raise ValidationError(f"Rights evidence invalid: {exc}", code="RIGHTS_EVIDENCE_INVALID") from None
        with kb.transaction():
            return self._assess(asset_sha256, version, evidence, actor, "POLICY_ENGINE")

    def current_assessment(self, asset_sha256: str, document_version_id: str):
        return self.kb.one("SELECT * FROM rights_assessment WHERE asset_sha256=? AND document_version_id=? ORDER BY rowid DESC LIMIT 1",
                           (asset_sha256, document_version_id))

    def gate(self, *, asset_sha256: str, document_version_id: str, action: str, quote_chars: int | None = None) -> dict:
        try:
            return rights_policy.gate(action, self.current_assessment(asset_sha256, document_version_id), quote_chars=quote_chars)
        except rights_policy.EvidenceError as exc:
            raise ValidationError(str(exc), code="GATE_ACTION_INVALID") from None
