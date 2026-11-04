"""S05 Full-text Eligibility CANDIDATE workflow: KB-side, NON_AUTHORITATIVE staging with human-only decisions.

Authority boundary (ADR-05-01..05; S01 v03 07 sections 3, 6): a run here is never a Reprovane RV-FTE run, a SEALED
corpus release or an official PRISMA count. Its single input is one verified, SEALED Reprovane Stage-11 title/abstract
release already imported through the accepted S03 path. S04J journal inventories are refused as sources.

Run creation re-verifies the source: the operator supplies the export folder, the trusted expected release digest and
the output of Reprovane's own ``verify-export``. The export bytes must equal the bytes S03 imported, the manifest must
carry the Stage-10 screening binding, every item must be a ``SCREEN_INCLUDE`` member, and the KB project references
must be exactly the export items. Reprovane 2.0.1 writes a portable export only for a SEALED release
(``corpus_release/store.py``: "Portable export requires sealed release").

Evidence is never inferred. A KB PDF becomes evidence only through a link to the candidate's own paper that a human
CONFIRMED (S03), with CAS bytes re-hashed at attach. Protocol version/rights rules produce explicit blockers.
Evidence currency (S05 v02, FTE-D17): while a run is unlocked, the attach-time facts of a KB asset (effective identity
event and state, role, paper, rights assessment) are re-derived on every projection without reading CAS bytes; any
difference makes the candidate PENDING ``STALE_EVIDENCE_NOT_CURRENT`` until new evidence is attached and re-reviewed.
An RV-Stage11B reference (FTE-D18) is verified against the actual read-only portable export and a trusted digest, but is
never evidence-ready: Stage-11B selection is automatic (no human identity confirmation) and SciVetra holds no bytes.
Acquisition attempts (login, bot check, ...) are operational records, never votes. Decisions and adjudications are
human records bound to the candidate item hash, the protocol hash and the evidence fingerprint. A changed evidence
fingerprint makes earlier votes stale. Every projection (outcome, queue, summary) is computed from append-only rows.
"""
from __future__ import annotations

import json
import re

from .. import __version__
from ..assets.service import AssetService
from ..bridge.sources import check_native_verify, read_corpus_export, read_fulltext_export
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, KBError, ValidationError
from ..library.common import operator_label
from . import protocol as P

AUTHORITY = "NON_AUTHORITATIVE"
DISCLAIMER = ("NON_AUTHORITATIVE KB-side full-text eligibility staging. Not a Reprovane RV-FTE run, not a SEALED corpus release, "
              "not an official PRISMA count. Reviewer labels are self-declared, not authenticated identities.")
NO_EVIDENCE_FP = semantic_sha256({"kind": "NO_EVIDENCE_ATTACHED"})


def note_sha(note):
    """Decision hashes cover the SHA-256 of the free-text note, so exports can be replayed without carrying the note."""
    return sha256_bytes(note.encode("utf-8")) if note else None
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SECRETISH = re.compile(r"(?i)(password|passwd|token|cookie|session|api[_-]?key|bearer|secret|signature|x-amz-|authorization)")
_URL_WITH_QUERY = re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^\s]*[?#]")
_ABS_PATH = re.compile(r"(?i)(^|\s)([a-z]:[\\/]|\\\\|/(home|users|mnt|var|tmp|etc)/)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
STALE_NOT_CURRENT = "STALE_EVIDENCE_NOT_CURRENT"
RV_VERSIONS = {"publishedVersion": "PUBLISHED", "acceptedVersion": "ACCEPTED", "submittedVersion": "SUBMITTED"}
EXTERNAL_BLOCKERS = ("IDENTITY_NOT_HUMAN_CONFIRMED_EXTERNAL", "BYTES_NOT_IN_KB_EXTERNAL", "RIGHTS_NOT_DETERMINED_EXTERNAL")
_SELF_ASSERTED_KEYS = {"export_verified", "fulltext_run_id", "fulltext_manifest_sha256", "artifact_sha256", "corpus_item_id", "version_class",
                       "rights_class"}
ATTEMPT_STATUSES = ("LOGIN_REQUIRED", "BOT_CHECK", "INSTITUTIONAL_RESTRICTED", "NO_ACCESS", "RETRY_QUEUED", "WRONG_PDF", "NOT_FOUND_ONLINE",
                    "OA_LOCATION_FAILED", "OTHER")


def safe_text(value, what: str, *, max_len: int, required: bool = False):
    """Free text written into the KB: no secrets, URL query strings, absolute local paths or e-mail addresses."""
    if value is None or value == "":
        if required:
            raise ValidationError(f"{what} is required", code="TEXT_REQUIRED")
        return None
    if not isinstance(value, str) or len(value) > max_len or "\x00" in value:
        raise ValidationError(f"{what} must be text of at most {max_len} characters", code="TEXT_INVALID")
    for pattern, code in ((_SECRETISH, "TEXT_LOOKS_LIKE_SECRET"), (_URL_WITH_QUERY, "TEXT_URL_WITH_QUERY_REFUSED"),
                          (_ABS_PATH, "TEXT_LOCAL_PATH_REFUSED"), (_EMAIL, "TEXT_EMAIL_REFUSED")):
        if pattern.search(value):
            raise ValidationError(f"{what} looks like it contains a secret, signed URL, local path or e-mail address", code=code)
    return value


class FTEService:
    def __init__(self, kb):
        self.kb = kb

    # -- protocol ----------------------------------------------------------------------------------------------------
    def create_protocol(self, protocol: dict, *, operator: str) -> dict:
        actor = operator_label(operator)
        canonical = P.validate_protocol(protocol)
        digest = P.protocol_sha(canonical)
        existing = self.kb.one("SELECT protocol_id FROM fte_protocol WHERE protocol_sha256=?", (digest,))
        if existing:
            return {"protocol_id": existing["protocol_id"], "protocol_sha256": digest, "created": False, "code": "DUPLICATE_NOOP"}
        row = dict(protocol_id=self.kb.ids.new("ftp"), label=canonical["label"], protocol_json=canonical_json(canonical), protocol_sha256=digest,
                   reason_vocabulary_version=canonical["reason_vocabulary_version"], consensus_rule=canonical["consensus_rule"],
                   created_by=actor, created_at_utc=self.kb.now())
        with self.kb.transaction():
            self.kb.insert("fte_protocol", row)
        return {"protocol_id": row["protocol_id"], "protocol_sha256": digest, "created": True}

    def _protocol(self, protocol_id: str) -> dict:
        row = self.kb.one("SELECT * FROM fte_protocol WHERE protocol_id=?", (protocol_id,))
        if not row:
            raise ValidationError("Unknown FTE protocol", code="UNKNOWN_PROTOCOL")
        return row

    # -- run creation ------------------------------------------------------------------------------------------------
    def eligible_sources(self) -> list[dict]:
        return self.kb.all("SELECT r.release_import_id, w.operator_label AS workspace_label, r.release_id, r.release_label, r.project_revision_id, "
                           "r.release_manifest_sha256, r.item_count FROM reprovane_release_import r JOIN workspace_registration w "
                           "ON w.workspace_ref = r.workspace_ref ORDER BY r.rowid")

    def create_run(self, *, source_id: str, export_dir: str, expected_manifest_sha256: str, rv_verify_json: str, protocol_id: str,
                   roster: list, label: str, operator: str) -> dict:
        actor = operator_label(operator)
        if not isinstance(source_id, str) or not source_id:
            raise ValidationError("source id required", code="SOURCE_REQUIRED")
        if source_id.startswith(("jcv_", "jj_", "jcm_")) or (self._has_table("jh_collection_version") and self.kb.one(
                "SELECT 1 x FROM jh_collection_version WHERE version_id=? UNION SELECT 1 FROM jh_journal WHERE journal_id=? "
                "UNION SELECT 1 FROM jh_member WHERE member_id=?", (source_id, source_id, source_id))):
            raise ValidationError("A journal inventory (S04J) is discovery evidence, never a screened candidate set; FTE candidates come only "
                                  "from a SEALED Reprovane Stage-11 title/abstract release (ADR-05-01)", code="JOURNAL_INVENTORY_NOT_AN_FTE_SOURCE")
        release = self.kb.one("SELECT * FROM reprovane_release_import WHERE release_import_id=?", (source_id,))
        if not release:
            raise ValidationError("Source is not a Reprovane Stage-11 release imported through S03", code="SOURCE_NOT_A_SEALED_TA_RELEASE")
        if not isinstance(expected_manifest_sha256, str) or not _HEX64.fullmatch(expected_manifest_sha256):
            raise ValidationError("--expected-manifest-sha256 (64 lowercase hex, from a trusted channel) is required", code="EXPECTED_MANIFEST_REQUIRED")
        if expected_manifest_sha256 != release["release_manifest_sha256"]:
            raise IntegrityError("Expected release digest differs from the imported release", code="EXPECTED_MANIFEST_MISMATCH")
        if not rv_verify_json:
            raise ValidationError("--rv-verify-json (output of Reprovane's own verify-export) is required", code="NATIVE_VERIFY_REQUIRED")
        export = read_corpus_export(export_dir, expected_manifest_sha256=expected_manifest_sha256)
        recorded = {"manifest.json": release["manifest_bytes_sha256"], "corpus.jsonl": release["corpus_jsonl_bytes_sha256"],
                    "corpus.csv": release["corpus_csv_bytes_sha256"], "SHA256SUMS.txt": release["sums_bytes_sha256"]}
        if any(export["files"].get(name) != digest for name, digest in recorded.items()):
            raise IntegrityError("Export bytes differ from the release imported through S03", code="SOURCE_EXPORT_CHANGED")
        manifest, items = export["manifest"], export["items"]
        screening = {k: manifest.get(k) for k in ("project_revision_id", "screening_run_id", "screening_manifest_sha256")}
        if not all(isinstance(v, str) and v for v in screening.values()) or not _HEX64.fullmatch(screening["screening_manifest_sha256"]) \
                or manifest.get("screen_include_count") != len(items) or manifest.get("item_count", len(items)) != len(items) or not items:
            raise ValidationError("Release manifest lacks the Stage-10 screening binding (screening run, screening manifest digest, "
                                  "SCREEN_INCLUDE count = items): not a screened title/abstract release", code="UNSCREENED_OR_NOT_A_TA_RELEASE")
        for item in items:
            prov = item.get("screening_provenance")
            if prov is not None and (not isinstance(prov, dict) or prov.get("outcome") != "SCREEN_INCLUDE"
                                     or prov.get("screening_manifest_sha256") != screening["screening_manifest_sha256"]):
                raise ValidationError("An item is not a SCREEN_INCLUDE member of the bound screening run", code="UNSCREENED_OR_NOT_A_TA_RELEASE")
        native = check_native_verify(rv_verify_json, kind="corpus", expected_digest=expected_manifest_sha256, item_count=len(items))
        if native.get("screen_include_count") not in (None, len(items)):
            raise IntegrityError("native verify-export screen_include_count differs", code="NATIVE_VERIFY_MISMATCH")
        refs = self.kb.all("SELECT * FROM project_paper_reference WHERE release_import_id=? ORDER BY corpus_item_id", (source_id,))
        by_item = {r["corpus_item_id"]: r for r in refs}
        if len(refs) != len(items) or {i["corpus_item_id"] for i in items} != set(by_item) \
                or any(by_item[i["corpus_item_id"]]["item_sha256"] != i["item_sha256"] for i in items) \
                or {r["workspace_ref"] for r in refs} != {release["workspace_ref"]}:
            raise IntegrityError("KB project references differ from the release items (missing, extra, changed or cross-project)",
                                 code="SOURCE_REFERENCE_MISMATCH")
        protocol = self._protocol(protocol_id)
        roster_c = P.validate_roster(roster)
        candidates = [{"ordinal": n, "corpus_item_id": i["corpus_item_id"], "item_sha256": i["item_sha256"],
                       "publication_entity_id": i["publication_entity_id"], "reference": by_item[i["corpus_item_id"]]}
                      for n, i in enumerate(items)]
        assignment = P.assign(roster_c, candidates)
        item_set = semantic_sha256(sorted([c["corpus_item_id"], c["item_sha256"]] for c in candidates))
        roster_sha = semantic_sha256(roster_c)
        assignment_sha = semantic_sha256(sorted(assignment.items()))
        semantic = semantic_sha256({"workspace": release["workspace_ref"], "release": release["release_manifest_sha256"], "item_set": item_set,
                                    "protocol": protocol["protocol_sha256"], "roster": roster_sha})
        existing = self.kb.one("SELECT run_id FROM fte_run WHERE semantic_sha256=?", (semantic,))
        if existing:
            return {"run_id": existing["run_id"], "created": False, "code": "DUPLICATE_NOOP", "authority": AUTHORITY}
        verification = {"checker": export["verify"]["checker"], "mode": export["verify"]["mode"], "expected_digest_supplied": True,
                        "export_bytes_equal_s03_import": True, "native_verify_export": native,
                        "sealed_basis": "Reprovane 2.0.1 writes corpus-portable/1 only for SEALED releases; native verify-export valid",
                        "scope": "self-consistency plus operator-trusted digest; not a signature"}
        run_id = self.kb.ids.new("ftr")
        now = self.kb.now()
        with self.kb.transaction():
            self.kb.insert("fte_run", dict(
                run_id=run_id, authority=AUTHORITY, workspace_ref=release["workspace_ref"], release_import_id=source_id, release_id=release["release_id"],
                release_manifest_sha256=release["release_manifest_sha256"], project_revision_id=screening["project_revision_id"],
                screening_run_id=screening["screening_run_id"], screening_manifest_sha256=screening["screening_manifest_sha256"],
                source_verification_json=canonical_json(verification), item_count=len(candidates), item_set_sha256=item_set,
                protocol_id=protocol_id, protocol_sha256=protocol["protocol_sha256"], roster_sha256=roster_sha, assignment_sha256=assignment_sha,
                label=safe_text(label, "label", max_len=200, required=True), created_by=actor, created_at_utc=now, client_version=__version__,
                semantic_sha256=semantic))
            self._event(run_id, "OPEN", actor, "run created", {"source": source_id}, in_transaction=True)
            reviewer_ids = {}
            for entry in roster_c:
                rid = self.kb.ids.new("ftv")
                reviewer_ids[entry["label"]] = rid
                self.kb.insert("fte_reviewer", dict(reviewer_id=rid, run_id=run_id, label=entry["label"], role=entry["role"],
                                                    actor_type="HUMAN", conflicts_json=canonical_json(entry["conflicts"])))
            for c in candidates:
                cid = self.kb.ids.new("ftc")
                ref = c["reference"]
                self.kb.insert("fte_candidate", dict(candidate_id=cid, run_id=run_id, ordinal=c["ordinal"], corpus_item_id=c["corpus_item_id"],
                                                     reference_id=ref["reference_id"], paper_id=ref["paper_id"],
                                                     publication_entity_id=c["publication_entity_id"], item_sha256=c["item_sha256"]))
                for slot, label_ in enumerate(assignment[c["corpus_item_id"]], start=1):
                    self.kb.insert("fte_assignment", dict(assignment_id=self.kb.ids.new("fta"), candidate_id=cid, reviewer_id=reviewer_ids[label_],
                                                          slot=slot))
            self._event(run_id, "INPUT_FROZEN", actor, "candidate set, roster and assignments frozen",
                        {"item_count": len(candidates), "item_set_sha256": item_set}, in_transaction=True)
        return {"run_id": run_id, "created": True, "authority": AUTHORITY, "item_count": len(candidates), "item_set_sha256": item_set,
                "protocol_sha256": protocol["protocol_sha256"], "roster_sha256": roster_sha, "state": "INPUT_FROZEN"}

    def _has_table(self, name: str) -> bool:
        return bool(self.kb.one("SELECT 1 x FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    def _event(self, run_id, state, actor, reason, detail, *, in_transaction=False):
        seq = (self.kb.one("SELECT max(seq) m FROM fte_run_event WHERE run_id=?", (run_id,))["m"] or 0) + 1
        row = dict(event_id=self.kb.ids.new("fre"), run_id=run_id, seq=seq, new_state=state, actor=actor, reason=reason,
                   detail_json=canonical_json(detail), created_at_utc=self.kb.now())
        if in_transaction:
            self.kb.insert("fte_run_event", row)
        else:
            with self.kb.transaction():
                self.kb.insert("fte_run_event", row)

    def run(self, run_id: str) -> dict:
        row = self.kb.one("SELECT * FROM fte_run WHERE run_id=?", (run_id,))
        if not row:
            raise ValidationError("Unknown FTE run", code="UNKNOWN_RUN")
        return row

    def state(self, run_id: str) -> str:
        return self.kb.one("SELECT new_state FROM fte_run_event WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,))["new_state"]

    def _candidate(self, candidate_id: str) -> dict:
        row = self.kb.one("SELECT * FROM fte_candidate WHERE candidate_id=?", (candidate_id,))
        if not row:
            raise ValidationError("Unknown FTE candidate", code="UNKNOWN_CANDIDATE")
        return row

    def _require_open(self, run_id: str) -> None:
        if self.kb.one("SELECT 1 x FROM fte_lock WHERE run_id=?", (run_id,)):
            raise ValidationError("FTE run is LOCKED_FOR_HANDOFF; start a new run for further work", code="RUN_LOCKED")

    def _protocol_of(self, run: dict) -> dict:
        protocol = json.loads(self._protocol(run["protocol_id"])["protocol_json"])
        if P.protocol_sha(protocol) != run["protocol_sha256"]:
            raise IntegrityError("Stored protocol no longer matches the run's frozen protocol hash", code="PROTOCOL_HASH_CHANGED")
        return protocol

    # -- evidence ----------------------------------------------------------------------------------------------------
    def current_evidence(self, candidate_id: str):
        return self.kb.one("SELECT * FROM fte_evidence WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1", (candidate_id,))

    def evidence_fingerprint(self, candidate_id: str) -> str:
        ev = self.current_evidence(candidate_id)
        return ev["evidence_fingerprint"] if ev else NO_EVIDENCE_FP

    def _attach(self, candidate: dict, row: dict, actor: str) -> dict:
        current = self.current_evidence(candidate["candidate_id"])
        if current and current["evidence_fingerprint"] == row["evidence_fingerprint"]:
            return {"evidence_id": current["evidence_id"], "created": False, "code": "DUPLICATE_NOOP", "evidence_ready": bool(current["evidence_ready"]),
                    "blockers": json.loads(current["blockers_json"])}
        row.update(evidence_id=self.kb.ids.new("fev"), candidate_id=candidate["candidate_id"],
                   supersedes_evidence_id=current["evidence_id"] if current else None, attached_by=actor, attached_at_utc=self.kb.now())
        with self.kb.transaction():
            self.kb.insert("fte_evidence", row)
        stale = self.kb.one("SELECT count(*) n FROM fte_decision WHERE candidate_id=?", (candidate["candidate_id"],))["n"]
        return {"evidence_id": row["evidence_id"], "created": True, "evidence_ready": bool(row["evidence_ready"]),
                "blockers": json.loads(row["blockers_json"]), "supersedes_evidence_id": row["supersedes_evidence_id"],
                "earlier_votes_now_stale": stale > 0, "evidence_fingerprint": row["evidence_fingerprint"]}

    def _kb_asset_facts(self, protocol: dict, link: dict) -> dict:
        """Attach-time facts of a KB asset link, derived from the current KB state without reading CAS bytes.

        The same function re-derives the facts for the currency check, so attach, projections and lock cannot disagree."""
        version = self.kb.one("SELECT * FROM document_version WHERE document_version_id=?", (link["document_version_id"],))
        assessment = AssetService(self.kb).current_assessment(link["asset_sha256"], link["document_version_id"])
        identity = self.kb.one("SELECT event_id FROM paper_asset_link_event WHERE link_id=? ORDER BY rowid DESC LIMIT 1", (link["link_id"],))
        blockers = []
        if version["version_class"] not in protocol["evidence_policy"]["allowed_version_classes"]:
            blockers.append(f"VERSION_NOT_ALLOWED_BY_PROTOCOL:{version['version_class']}")
        if assessment is None:
            blockers.append("RIGHTS_NOT_ASSESSED")
        elif assessment["local_process"] != "ALLOW":
            blockers.append(f"RIGHTS_LOCAL_PROCESS_{assessment['local_process']}")
        return {"kind": "KB_ASSET", "asset_sha256": link["asset_sha256"], "link_id": link["link_id"], "paper_id": link["paper_id"],
                "document_version_id": link["document_version_id"], "version_class": version["version_class"], "link_state": link["effective_state"],
                "role": link["effective_role"], "identity_event_id": identity["event_id"] if identity else None,
                "rights_assessment_id": assessment["assessment_id"] if assessment else None,
                "rights_class": assessment["rights_class"] if assessment else None,
                "local_process": assessment["local_process"] if assessment else None, "blockers": sorted(blockers)}

    def attach_kb_asset(self, *, candidate_id: str, link_id: str, operator: str) -> dict:
        actor = operator_label(operator)
        candidate = self._candidate(candidate_id)
        run = self.run(candidate["run_id"])
        self._require_open(run["run_id"])
        protocol = self._protocol_of(run)
        if "KB_ASSET" not in protocol["evidence_policy"]["allowed_kinds"]:
            raise ValidationError("Protocol does not allow KB asset evidence", code="EVIDENCE_KIND_NOT_ALLOWED")
        link = self.kb.one("SELECT * FROM paper_asset_link_effective WHERE link_id=?", (link_id,))
        if not link:
            raise ValidationError("Unknown asset link", code="UNKNOWN_REFERENCE")
        if link["paper_id"] != candidate["paper_id"]:
            raise ValidationError("The asset is linked to a different paper than this candidate's TA item", code="EVIDENCE_PAPER_MISMATCH")
        if link["effective_state"] != "HUMAN_CONFIRMED":
            raise ValidationError(f"Asset identity is {link['effective_state']}; only a HUMAN_CONFIRMED link is evidence "
                                  "(a structurally valid PDF is not an identity-confirmed paper)", code="EVIDENCE_IDENTITY_NOT_HUMAN_CONFIRMED")
        if link["effective_role"] != "MAIN_TEXT":
            raise ValidationError("Only a MAIN_TEXT link can be full-text evidence (supplementary/erratum/cover are not)", code="EVIDENCE_NOT_MAIN_TEXT")
        try:
            self.kb.cas.verify_object(link["asset_sha256"])
        except KBError as exc:
            raise IntegrityError(f"Evidence bytes are missing or changed in CAS ({exc.code})", code="EVIDENCE_BYTES_UNVERIFIABLE") from None
        facts = self._kb_asset_facts(protocol, link)
        row = dict(kind="KB_ASSET", asset_sha256=link["asset_sha256"], link_id=link_id, document_version_id=link["document_version_id"],
                   version_class=facts["version_class"], link_state_at_attach="HUMAN_CONFIRMED",
                   rights_assessment_id=facts["rights_assessment_id"], rights_class=facts["rights_class"], external_ref_json=None,
                   evidence_ready=0 if facts["blockers"] else 1, blockers_json=canonical_json(facts["blockers"]),
                   byte_verification="KB_CAS_REHASHED_AT_ATTACH", evidence_fingerprint=semantic_sha256(facts))
        return self._attach(candidate, row, actor)

    def attach_external(self, *, candidate_id: str, artifact: dict, operator: str) -> dict:
        """Reference to the artifact a read-only RV-Stage11B portable export selected for this candidate (FTE-D18).

        ``artifact`` names the export, never its content: ``fulltext_export_dir`` and ``expected_manifest_sha256`` (from a
        trusted channel), optionally ``rv_verify_json`` (Reprovane's own ``fulltext verify-export`` output) and
        ``expected_artifact_sha256``. Self-asserted references (``export_verified``, typed digests, version or rights
        claims) are refused. The export is verified with the accepted S04 reader, then bound to this run's release, this
        candidate's item and its own selected artifact. The reference is recorded with blockers and is never evidence-ready:
        the selection is automatic, SciVetra holds no bytes and no local-processing rights are determined. A reviewer
        reads full text only through a KB asset with the same bytes that a human confirmed (attach it with --link-id)."""
        actor = operator_label(operator)
        candidate = self._candidate(candidate_id)
        run = self.run(candidate["run_id"])
        self._require_open(run["run_id"])
        protocol = self._protocol_of(run)
        if "RV_STAGE11B_ARTIFACT" not in protocol["evidence_policy"]["allowed_kinds"]:
            raise ValidationError("Protocol does not allow RV-Stage11B artifact evidence", code="EVIDENCE_KIND_NOT_ALLOWED")
        if not isinstance(artifact, dict):
            raise ValidationError("RV-Stage11B reference must be an object", code="EXTERNAL_REFERENCE_INVALID")
        if set(artifact) & _SELF_ASSERTED_KEYS:
            raise ValidationError("A self-asserted Stage-11B reference (export_verified flag, typed digests, version or rights claims) is "
                                  "never accepted; name the read-only Stage-11B export folder and its trusted manifest digest instead",
                                  code="EXTERNAL_SELF_ASSERTION_REFUSED")
        required, optional = {"fulltext_export_dir", "expected_manifest_sha256"}, {"rv_verify_json", "expected_artifact_sha256"}
        if not required <= set(artifact) <= required | optional or not isinstance(artifact["fulltext_export_dir"], str) \
                or not all(isinstance(artifact[k], str) and _HEX64.fullmatch(artifact[k])
                           for k in ("expected_manifest_sha256", "expected_artifact_sha256") if k in artifact):
            raise ValidationError("RV-Stage11B reference needs fulltext_export_dir and expected_manifest_sha256 (64 lowercase hex, from a "
                                  "trusted channel); optional rv_verify_json and expected_artifact_sha256", code="EXTERNAL_REFERENCE_INVALID")
        export = read_fulltext_export(artifact["fulltext_export_dir"], expected_manifest_sha256=artifact["expected_manifest_sha256"])
        m = export["manifest"]
        if m["release_id"] != run["release_id"] or m["release_manifest_sha256"] != run["release_manifest_sha256"]:
            raise IntegrityError("The Stage-11B export belongs to another Stage-11 release than this run's frozen source",
                                 code="EXTERNAL_RELEASE_MISMATCH")
        item = next((st for st in m["items"] if st["corpus_item_id"] == candidate["corpus_item_id"]), None)
        if item is None:
            raise IntegrityError("The Stage-11B export has no item for this candidate", code="EXTERNAL_ITEM_NOT_IN_EXPORT")
        if item["publication_entity_id"] != candidate["publication_entity_id"]:
            raise IntegrityError("The Stage-11B item names another publication entity than this candidate", code="EXTERNAL_ITEM_MISMATCH")
        if item["status"] != "ACQUIRED_AUTO":
            raise ValidationError(f"Stage-11B item status is {item['status']}: no artifact was selected (record acquisition attempts instead)",
                                  code="EXTERNAL_ITEM_NOT_ACQUIRED")
        selected = item["selected_artifact_sha256"]
        if not isinstance(selected, str) or not _HEX64.fullmatch(selected) or not any(
                a.get("candidate_id") == item["selected_candidate_id"] and a["outcome"]["status"] == "ACQUIRED"
                and a["outcome"]["artifact_sha256"] == selected for a in item["attempts"]):
            raise IntegrityError("The selected artifact does not occur as this item's own ACQUIRED attempt", code="EXTERNAL_ARTIFACT_NOT_IN_EXPORT")
        if any(st is not item and (st["selected_artifact_sha256"] == selected or any(a["outcome"]["artifact_sha256"] == selected for a in st["attempts"]))
               for st in m["items"]):
            raise IntegrityError("The same artifact bytes are claimed by another item of the export", code="EXTERNAL_ARTIFACT_SHARED_ACROSS_ITEMS")
        if artifact.get("expected_artifact_sha256") not in (None, selected):
            raise IntegrityError("The export selected different bytes than the expected artifact digest", code="EXTERNAL_ARTIFACT_MISMATCH")
        native = None
        if artifact.get("rv_verify_json"):
            native = check_native_verify(artifact["rv_verify_json"], kind="fulltext", expected_digest=export["rv_digest"], item_count=len(m["items"]))
        claims = [e for e in item.get("selected_evidence") or [] if isinstance(e, dict)]
        versions = sorted({str(e.get("version")) for e in claims})
        version_class = RV_VERSIONS.get(versions[0], "UNKNOWN") if len(versions) == 1 else "UNKNOWN"
        blockers = list(EXTERNAL_BLOCKERS)
        if version_class not in protocol["evidence_policy"]["allowed_version_classes"]:
            blockers.append(f"VERSION_NOT_ALLOWED_BY_PROTOCOL:{version_class}")
        verification = {
            "level": "RV11B_PORTABLE_EXPORT_VERIFIED_WITH_TRUSTED_DIGEST" + ("_AND_NATIVE_VERIFY" if native else ""),
            "checker": export["verify"]["checker"], "native_verify_export": native,
            "verified": ["exact five-file inventory, safe paths, no links", "SHA256SUMS and canonical re-rendering of every file",
                         "manifest digest equals the trusted expected digest", "release_id and release_manifest_sha256 equal the run's frozen source",
                         "item is this candidate's corpus_item_id and publication_entity_id", "item status ACQUIRED_AUTO with its own ACQUIRED attempt",
                         "selected artifact digest not claimed by another item"] + (["policy digest via native verify-export"] if native else []),
            "not_verified": ["PDF bytes (SciVetra holds none and re-hashed nothing)", "human identity of the selected PDF",
                             "local-processing rights (provider OA/licence claims are recorded, not adopted)", "publisher signature or authenticity"]
                            + ([] if native else ["fulltext policy digest (no native verify-export supplied)"])}
        ref = {"fulltext_run_id": m["run_id"], "fulltext_manifest_sha256": export["rv_digest"], "files": export["files"], "release_id": m["release_id"],
               "release_manifest_sha256": m["release_manifest_sha256"], "fulltext_policy_sha256": m["fulltext_policy_sha256"],
               "corpus_item_id": item["corpus_item_id"], "publication_entity_id": item["publication_entity_id"], "item_status": item["status"],
               "item_status_sha256": item["status_sha256"], "selected_candidate_id": item["selected_candidate_id"], "artifact_sha256": selected,
               "provider_version_claims": versions, "provider_licence_claims": sorted({str(e.get("license")) for e in claims}),
               "provider_is_oa_claims": sorted({str(e.get("is_oa")) for e in claims}), "verification": verification,
               "limitation": "EXTERNAL_HASH_ONLY_NOT_REVERIFIED: SciVetra did not re-hash these bytes; never evidence-ready"}
        facts = {"kind": "RV_STAGE11B_ARTIFACT", "ref": ref, "blockers": sorted(blockers)}
        row = dict(kind="RV_STAGE11B_ARTIFACT", asset_sha256=None, link_id=None, document_version_id=None, version_class=version_class,
                   link_state_at_attach=None, rights_assessment_id=None, rights_class=None, external_ref_json=canonical_json(ref),
                   evidence_ready=0, blockers_json=canonical_json(sorted(blockers)),
                   byte_verification="EXTERNAL_HASH_ONLY_NOT_REVERIFIED", evidence_fingerprint=semantic_sha256(facts))
        out = self._attach(candidate, row, actor)
        same_bytes = [r["link_id"] for r in self.kb.all("SELECT link_id FROM paper_asset_link_effective WHERE asset_sha256=? AND paper_id=? "
                                                        "AND effective_state='HUMAN_CONFIRMED' AND effective_role='MAIN_TEXT' ORDER BY row_seq",
                                                        (selected, candidate["paper_id"]))]
        return dict(out, verification_level=verification["level"], kb_asset_links_with_same_bytes=same_bytes)

    def attach_none(self, *, candidate_id: str, operator: str) -> dict:
        actor = operator_label(operator)
        candidate = self._candidate(candidate_id)
        self._require_open(candidate["run_id"])
        facts = {"kind": "NONE_NOT_RETRIEVED", "blockers": ["NO_FULLTEXT_ATTACHED"]}
        row = dict(kind="NONE_NOT_RETRIEVED", asset_sha256=None, link_id=None, document_version_id=None, version_class=None,
                   link_state_at_attach=None, rights_assessment_id=None, rights_class=None, external_ref_json=None, evidence_ready=0,
                   blockers_json=canonical_json(["NO_FULLTEXT_ATTACHED"]), byte_verification="NOT_APPLICABLE", evidence_fingerprint=semantic_sha256(facts))
        return self._attach(candidate, row, actor)

    # -- acquisition attempts (operational only) ----------------------------------------------------------------------
    def record_attempt(self, *, candidate_id: str, status: str, source_description: str, attempted_on: str, operator: str,
                       retry_after: str | None = None, note: str | None = None) -> dict:
        actor = operator_label(operator)
        candidate = self._candidate(candidate_id)
        self._require_open(candidate["run_id"])
        if status not in ATTEMPT_STATUSES:
            raise ValidationError("Unknown acquisition status", code="ATTEMPT_STATUS_INVALID")
        for value, what in ((attempted_on, "attempted_on"), (retry_after, "retry_after")):
            if value is not None and (not isinstance(value, str) or not _DATE.fullmatch(value)):
                raise ValidationError(f"{what} must be YYYY-MM-DD", code="ATTEMPT_DATE_INVALID")
        if attempted_on is None:
            raise ValidationError("attempted_on is required", code="ATTEMPT_DATE_INVALID")
        source = safe_text(source_description, "source_description", max_len=300, required=True)
        note = safe_text(note, "note", max_len=1000)
        content = {"candidate": candidate_id, "status": status, "source": source, "on": attempted_on, "retry": retry_after, "note": note}
        row = dict(attempt_id=self.kb.ids.new("ftq"), candidate_id=candidate_id, status=status, source_description=source, attempted_on=attempted_on,
                   retry_after=retry_after, note=note, recorded_by=actor, recorded_at_utc=self.kb.now(), semantic_sha256=semantic_sha256(content))
        with self.kb.transaction():
            stored, created = self.kb.insert_unique("fte_acquisition_attempt", row)
        return {"attempt_id": stored["attempt_id"], "created": created, "code": None if created else "DUPLICATE_NOOP",
                "note": "operational record only; never an eligibility decision"}

    # -- decisions --------------------------------------------------------------------------------------------------
    def _reviewer(self, run_id: str, label: str, role: str) -> dict:
        actor = operator_label(label)
        row = self.kb.one("SELECT * FROM fte_reviewer WHERE run_id=? AND label=?", (run_id, actor))
        if not row:
            raise ValidationError("Not on this run's frozen human roster (unregistered labels, AI and system actors cannot vote)",
                                  code="UNKNOWN_REVIEWER")
        if row["role"] != role:
            raise ValidationError(f"{actor} is a {row['role']}, not a {role}", code="WRONG_ROLE")
        return row

    def _validate_outcome(self, run, protocol, candidate, outcome, reasons, related, *, allow_unsure: bool):
        allowed = P.OUTCOMES if allow_unsure else P.FINAL_OUTCOMES
        if outcome not in allowed:
            raise ValidationError("Outcome must be one of " + ", ".join(allowed), code="OUTCOME_INVALID")
        reasons = sorted(set(reasons or []))
        if outcome == "FT_EXCLUDE":
            if not reasons or set(reasons) - set(protocol["exclude_reasons"]):
                raise ValidationError("FT_EXCLUDE needs reason codes from the protocol's exclusion vocabulary", code="REASON_INVALID")
        elif outcome == "FT_NOT_RETRIEVED":
            if not reasons or set(reasons) - set(protocol["not_retrieved_reasons"]):
                raise ValidationError("FT_NOT_RETRIEVED needs reason codes from the protocol's not-retrieved vocabulary", code="REASON_INVALID")
        elif reasons:
            raise ValidationError(f"{outcome} takes no reason codes", code="REASON_INVALID")
        if P.DUPLICATE_REASON in reasons:
            other = self.kb.one("SELECT 1 x FROM fte_candidate WHERE run_id=? AND corpus_item_id=? AND corpus_item_id<>?",
                                (run["run_id"], related, candidate["corpus_item_id"])) if related else None
            if not other:
                raise ValidationError("DUPLICATE_REPORT_OF_INCLUDED_STUDY needs --related-corpus-item-id of another candidate of this run",
                                      code="DUPLICATE_LINK_INVALID")
        elif related is not None:
            raise ValidationError("--related-corpus-item-id is only for DUPLICATE_REPORT_OF_INCLUDED_STUDY", code="DUPLICATE_LINK_INVALID")
        evidence = self.current_evidence(candidate["candidate_id"])
        problems = self.evidence_currency(candidate, evidence)
        if problems:
            raise ValidationError("The attached evidence is no longer current (" + ", ".join(problems) + "); attach the evidence again "
                                  "(or other evidence) before deciding", code="EVIDENCE_NOT_CURRENT")
        ready = bool(evidence and evidence["evidence_ready"])
        if outcome in ("FT_INCLUDE", "FT_EXCLUDE") and not ready:
            raise ValidationError(f"{outcome} needs evidence-ready full text (a HUMAN_CONFIRMED, protocol-version, rights-cleared "
                                  "manifestation); missing or blocked full text is never an inclusion or exclusion", code="EVIDENCE_NOT_READY")
        if outcome == "FT_NOT_RETRIEVED":
            if ready:
                raise ValidationError("Evidence-ready full text is attached; FT_NOT_RETRIEVED would contradict it",
                                      code="NOT_RETRIEVED_CONTRADICTS_READY_EVIDENCE")
            attempts = self.kb.one("SELECT count(*) n FROM fte_acquisition_attempt WHERE candidate_id=?", (candidate["candidate_id"],))["n"]
            if attempts < protocol["not_retrieved_min_documented_attempts"]:
                raise ValidationError(f"FT_NOT_RETRIEVED needs at least {protocol['not_retrieved_min_documented_attempts']} documented "
                                      f"acquisition attempts ({attempts} recorded)", code="RETRIEVAL_EFFORT_NOT_DOCUMENTED")
        return reasons, evidence

    @staticmethod
    def _locator(page, section):
        if page is None and section is None:
            return None
        if page is not None and (type(page) is not int or not 1 <= page <= 100000):
            raise ValidationError("locator page must be an integer 1..100000", code="LOCATOR_INVALID")
        if section is not None:
            safe_text(section, "locator section", max_len=200)
        return {k: v for k, v in (("page", page), ("section", section)) if v is not None}

    def current_decisions(self, candidate_id: str) -> dict:
        """reviewer_id -> current (latest, not superseded) decision."""
        out = {}
        for d in self.kb.all("SELECT * FROM fte_decision WHERE candidate_id=? ORDER BY rowid", (candidate_id,)):
            out[d["reviewer_id"]] = d
        return out

    def decide(self, *, candidate_id: str, reviewer: str, outcome: str, reasons=None, note=None, page=None, section=None,
               related_corpus_item_id=None, supersedes=None) -> dict:
        candidate = self._candidate(candidate_id)
        run = self.run(candidate["run_id"])
        self._require_open(run["run_id"])
        protocol = self._protocol_of(run)
        rev = self._reviewer(run["run_id"], reviewer, "REVIEWER")
        if not self.kb.one("SELECT 1 x FROM fte_assignment WHERE candidate_id=? AND reviewer_id=?", (candidate_id, rev["reviewer_id"])):
            raise ValidationError("This reviewer is not assigned to this candidate (independent assignment is frozen)", code="NOT_ASSIGNED_REVIEWER")
        reasons, evidence = self._validate_outcome(run, protocol, candidate, outcome, reasons, related_corpus_item_id, allow_unsure=True)
        note = safe_text(note, "note", max_len=2000, required=outcome == "FT_UNSURE")
        locator = self._locator(page, section)
        fp = evidence["evidence_fingerprint"] if evidence else NO_EVIDENCE_FP
        current = self.current_decisions(candidate_id).get(rev["reviewer_id"])
        content = {"candidate": candidate_id, "item_sha256": candidate["item_sha256"], "reviewer": rev["label"], "outcome": outcome, "reasons": reasons,
                   "note_sha256": note_sha(note), "locator": locator, "related": related_corpus_item_id, "evidence": fp, "protocol": run["protocol_sha256"]}
        if current is not None:
            same = {"candidate": candidate_id, "item_sha256": current["item_sha256"], "reviewer": rev["label"], "outcome": current["outcome"],
                    "reasons": json.loads(current["reason_codes_json"]), "note_sha256": note_sha(current["note"]),
                    "locator": json.loads(current["locator_json"]) if current["locator_json"] else None, "related": current["related_corpus_item_id"],
                    "evidence": current["evidence_fingerprint"], "protocol": current["protocol_sha256"]}
            if same == content:
                return {"decision_id": current["decision_id"], "created": False, "code": "DUPLICATE_NOOP"}
            if supersedes != current["decision_id"]:
                raise ValidationError("This reviewer already decided; a new decision must name --supersedes <current decision id> "
                                      "(decisions are never overwritten)", code="SUPERSEDE_REQUIRED")
        elif supersedes is not None:
            raise ValidationError("Nothing to supersede for this reviewer", code="SUPERSEDE_MISMATCH")
        content["supersedes"] = supersedes
        row = dict(decision_id=self.kb.ids.new("ftd"), candidate_id=candidate_id, reviewer_id=rev["reviewer_id"], outcome=outcome,
                   reason_codes_json=canonical_json(reasons), note=note, locator_json=canonical_json(locator) if locator else None,
                   related_corpus_item_id=related_corpus_item_id, evidence_id=evidence["evidence_id"] if evidence else None, evidence_fingerprint=fp,
                   protocol_sha256=run["protocol_sha256"], item_sha256=candidate["item_sha256"], supersedes_decision_id=supersedes,
                   decided_at_utc=self.kb.now(), decision_sha256=semantic_sha256(content))
        with self.kb.transaction():
            self.kb.insert("fte_decision", row)
            if self.state(run["run_id"]) in ("INPUT_FROZEN", "REVIEW_RESOLVED"):
                self._event(run["run_id"], "REVIEW_IN_PROGRESS", rev["label"], "a human decision was recorded", {"decision_id": row["decision_id"]},
                            in_transaction=True)
        return {"decision_id": row["decision_id"], "created": True, "decision_sha256": row["decision_sha256"], "authority": AUTHORITY,
                "candidate_state": self.candidate_state(candidate)}

    def adjudicate(self, *, candidate_id: str, adjudicator: str, outcome: str, note: str, reasons=None, related_corpus_item_id=None,
                   supersedes=None) -> dict:
        candidate = self._candidate(candidate_id)
        run = self.run(candidate["run_id"])
        self._require_open(run["run_id"])
        protocol = self._protocol_of(run)
        adj = self._reviewer(run["run_id"], adjudicator, "ADJUDICATOR")
        assigned = [a["reviewer_id"] for a in self.kb.all("SELECT reviewer_id FROM fte_assignment WHERE candidate_id=? ORDER BY slot", (candidate_id,))]
        current = self.current_decisions(candidate_id)
        fp = self.evidence_fingerprint(candidate_id)
        decisions = [current.get(r) for r in assigned]
        if any(d is None for d in decisions):
            raise ValidationError("Both assigned reviewers must decide before adjudication", code="REVIEWS_INCOMPLETE")
        if any(d["evidence_fingerprint"] != fp for d in decisions):
            raise ValidationError("A reviewer decision was made on different evidence; reviewers must re-decide first", code="STALE_DECISION")
        outcomes = {d["outcome"] for d in decisions}
        if len(outcomes) == 1 and "FT_UNSURE" not in outcomes:
            raise ValidationError("The two independent reviewers agree; there is nothing to adjudicate (no fabricated conflict)",
                                  code="ADJUDICATION_WITHOUT_DISAGREEMENT")
        reasons, evidence = self._validate_outcome(run, protocol, candidate, outcome, reasons, related_corpus_item_id, allow_unsure=False)
        note = safe_text(note, "note", max_len=2000, required=True)
        resolved = [d["decision_id"] for d in decisions]
        latest = self.kb.one("SELECT * FROM fte_adjudication WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1", (candidate_id,))
        content = {"candidate": candidate_id, "adjudicator": adj["label"], "outcome": outcome, "reasons": reasons, "note_sha256": note_sha(note),
                   "related": related_corpus_item_id, "resolves": resolved, "evidence": fp, "protocol": run["protocol_sha256"]}
        if latest is not None:
            same = {"candidate": candidate_id, "adjudicator": self.kb.one("SELECT label FROM fte_reviewer WHERE reviewer_id=?", (latest["adjudicator_id"],))["label"],
                    "outcome": latest["outcome"], "reasons": json.loads(latest["reason_codes_json"]), "note_sha256": note_sha(latest["note"]),
                    "related": latest["related_corpus_item_id"], "resolves": json.loads(latest["resolved_decision_ids_json"]),
                    "evidence": latest["evidence_fingerprint"], "protocol": latest["protocol_sha256"]}
            if same == content:
                return {"adjudication_id": latest["adjudication_id"], "created": False, "code": "DUPLICATE_NOOP"}
            if supersedes != latest["adjudication_id"]:
                raise ValidationError("An adjudication exists; a new one must name --supersedes <current adjudication id>", code="SUPERSEDE_REQUIRED")
        elif supersedes is not None:
            raise ValidationError("Nothing to supersede", code="SUPERSEDE_MISMATCH")
        content["supersedes"] = supersedes
        row = dict(adjudication_id=self.kb.ids.new("ftj"), candidate_id=candidate_id, adjudicator_id=adj["reviewer_id"], outcome=outcome,
                   reason_codes_json=canonical_json(reasons), note=note, related_corpus_item_id=related_corpus_item_id,
                   resolved_decision_ids_json=canonical_json(resolved), evidence_id=evidence["evidence_id"] if evidence else None,
                   evidence_fingerprint=fp, protocol_sha256=run["protocol_sha256"], supersedes_adjudication_id=supersedes,
                   decided_at_utc=self.kb.now(), adjudication_sha256=semantic_sha256(content))
        with self.kb.transaction():
            self.kb.insert("fte_adjudication", row)
            if self.state(run["run_id"]) in ("INPUT_FROZEN", "REVIEW_RESOLVED"):
                self._event(run["run_id"], "REVIEW_IN_PROGRESS", adj["label"], "a human adjudication was recorded",
                            {"adjudication_id": row["adjudication_id"]}, in_transaction=True)
        return {"adjudication_id": row["adjudication_id"], "created": True, "adjudication_sha256": row["adjudication_sha256"], "authority": AUTHORITY,
                "candidate_state": self.candidate_state(candidate)}

    # -- projections -------------------------------------------------------------------------------------------------
    def candidate_state(self, candidate: dict) -> dict:
        """Final outcome (CONSENSUS / ADJUDICATION) or PENDING with a reason. Superseded and stale votes never count.

        Unlocked run: evidence that is no longer current (identity, role, paper or rights changed since attach) makes the
        candidate PENDING STALE_EVIDENCE_NOT_CURRENT whatever the votes say. Locked run: the historical snapshot projection
        is kept; later drift is reported separately (``post_lock_drift``), never by rewriting the locked manifest."""
        cid = candidate["candidate_id"]
        evidence = self.current_evidence(cid)
        fp = evidence["evidence_fingerprint"] if evidence else NO_EVIDENCE_FP
        assigned = [a["reviewer_id"] for a in self.kb.all("SELECT reviewer_id FROM fte_assignment WHERE candidate_id=? ORDER BY slot", (cid,))]
        current = self.current_decisions(cid)
        decisions = [current.get(r) for r in assigned]
        base = {"candidate_id": cid, "corpus_item_id": candidate["corpus_item_id"], "evidence_fingerprint": fp,
                "decision_ids": [d["decision_id"] for d in decisions if d]}
        if evidence is not None and not self._locked(candidate["run_id"]):
            problems = self.evidence_currency(candidate, evidence)
            if problems:
                return dict(base, status="PENDING", pending_reason=STALE_NOT_CURRENT, outcome=None, evidence_problems=problems)
        if any(d is None for d in decisions):
            return dict(base, status="PENDING", pending_reason="AWAITING_REVIEWS", outcome=None)
        if any(d["evidence_fingerprint"] != fp for d in decisions):
            return dict(base, status="PENDING", pending_reason="STALE_DECISION_EVIDENCE_CHANGED", outcome=None)
        outcomes = {d["outcome"] for d in decisions}
        if len(outcomes) == 1 and "FT_UNSURE" not in outcomes:
            d = decisions[0]
            reasons = sorted(set(json.loads(decisions[0]["reason_codes_json"])) | set(json.loads(decisions[1]["reason_codes_json"])))
            related = d["related_corpus_item_id"] or decisions[1]["related_corpus_item_id"]
            return dict(base, status="FINAL", basis="CONSENSUS", outcome=d["outcome"], reason_codes=reasons, related_corpus_item_id=related,
                        adjudication_id=None)
        adj = self.kb.one("SELECT * FROM fte_adjudication WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1", (cid,))
        resolved = sorted(base["decision_ids"])
        if adj is None:
            return dict(base, status="PENDING", pending_reason="NEEDS_ADJUDICATION" if "FT_UNSURE" not in outcomes or len(outcomes) > 1
                        else "NEEDS_ADJUDICATION", outcome=None)
        if adj["evidence_fingerprint"] != fp or sorted(json.loads(adj["resolved_decision_ids_json"])) != resolved:
            return dict(base, status="PENDING", pending_reason="STALE_ADJUDICATION", outcome=None)
        return dict(base, status="FINAL", basis="ADJUDICATION", outcome=adj["outcome"], reason_codes=json.loads(adj["reason_codes_json"]),
                    related_corpus_item_id=adj["related_corpus_item_id"], adjudication_id=adj["adjudication_id"])

    def candidates(self, run_id: str) -> list[dict]:
        return self.kb.all("SELECT * FROM fte_candidate WHERE run_id=? ORDER BY ordinal", (run_id,))

    def summary(self, run_id: str) -> dict:
        """Provisional, candidate-level flow: TA_candidates = FT_INCLUDE + FT_EXCLUDE + FT_NOT_RETRIEVED + FT_PENDING (exact)."""
        run = self.run(run_id)
        states = [self.candidate_state(c) for c in self.candidates(run_id)]
        count = {"FT_INCLUDE": 0, "FT_EXCLUDE": 0, "FT_NOT_RETRIEVED": 0, "FT_PENDING": 0}
        excluded_by_reason, nr_by_reason, pending_by_reason, basis = {}, {}, {}, {"CONSENSUS": 0, "ADJUDICATION": 0}
        included_ids = set()
        for s in states:
            if s["status"] == "FINAL":
                count[s["outcome"]] += 1
                basis[s["basis"]] += 1
                if s["outcome"] == "FT_INCLUDE":
                    included_ids.add(s["corpus_item_id"])
                target = excluded_by_reason if s["outcome"] == "FT_EXCLUDE" else (nr_by_reason if s["outcome"] == "FT_NOT_RETRIEVED" else None)
                for r in (s["reason_codes"] if target is not None else []):
                    target[r] = target.get(r, 0) + 1
            else:
                count["FT_PENDING"] += 1
                pending_by_reason[s["pending_reason"]] = pending_by_reason.get(s["pending_reason"], 0) + 1
        duplicates = [s for s in states if s["status"] == "FINAL" and s["outcome"] == "FT_EXCLUDE" and P.DUPLICATE_REASON in s["reason_codes"]
                      and s["related_corpus_item_id"] in included_ids]
        votes = self.kb.one("SELECT count(*) n FROM fte_decision d JOIN fte_candidate c USING (candidate_id) WHERE c.run_id=?", (run_id,))["n"]
        superseded = self.kb.one("SELECT count(*) n FROM fte_decision d JOIN fte_candidate c USING (candidate_id) WHERE c.run_id=? "
                                 "AND d.supersedes_decision_id IS NOT NULL", (run_id,))["n"]
        adjudications = self.kb.one("SELECT count(*) n FROM fte_adjudication a JOIN fte_candidate c USING (candidate_id) WHERE c.run_id=?", (run_id,))["n"]
        total = sum(count.values())
        return {"schema": "fte-summary/1", "authority": AUTHORITY, "status": "PROVISIONAL" if count["FT_PENDING"] or not self._locked(run_id) else
                "LOCKED_FOR_HANDOFF_STAGING_SNAPSHOT", "disclaimer": DISCLAIMER, "run_id": run_id, "release_id": run["release_id"],
                "release_manifest_sha256": run["release_manifest_sha256"], "protocol_sha256": run["protocol_sha256"],
                "ta_candidate_count": run["item_count"], **{k.lower(): v for k, v in count.items()},
                "invariant": {"rule": "ta_candidate_count = ft_include + ft_exclude + ft_not_retrieved + ft_pending", "holds": total == run["item_count"]},
                "ft_exclude_by_reason": dict(sorted(excluded_by_reason.items())), "ft_not_retrieved_by_reason": dict(sorted(nr_by_reason.items())),
                "ft_pending_by_reason": dict(sorted(pending_by_reason.items())), "final_basis": basis,
                "reports_vs_studies": {"reports_sought": run["item_count"], "reports_not_retrieved": count["FT_NOT_RETRIEVED"],
                                       "reports_assessed_on_full_text": count["FT_INCLUDE"] + count["FT_EXCLUDE"],
                                       "studies_included": count["FT_INCLUDE"],
                                       "reports_of_included_studies": count["FT_INCLUDE"] + len(duplicates),
                                       "rule": "a report is a candidate; a duplicate report is linked to its study only by a human "
                                               "DUPLICATE_REPORT_OF_INCLUDED_STUDY decision"},
                "review_load": {"current_and_superseded_votes": votes, "superseded_votes_not_counted": superseded, "adjudications": adjudications},
                "state": self.state(run_id), **({"post_lock_drift": self.post_lock_drift(run_id)} if self._locked(run_id) else {})}

    def _locked(self, run_id: str) -> bool:
        return bool(self.kb.one("SELECT 1 x FROM fte_lock WHERE run_id=?", (run_id,)))

    def _doi_keys(self, paper_id: str) -> list[str]:
        return [r["value_normalized"] for r in self.kb.all(
            "SELECT DISTINCT value_normalized FROM identity_assertion_effective WHERE paper_id=? AND namespace='doi' AND value_normalized IS NOT NULL "
            "AND effective_status='ACTIVE' ORDER BY value_normalized", (paper_id,))]

    def journal_context(self, candidate: dict) -> dict:
        """Read-only S04J context, only for a human-confirmed manifestation and an exact DOI key (no fuzzy joins)."""
        evidence = self.current_evidence(candidate["candidate_id"])
        if not evidence or evidence["kind"] != "KB_ASSET" or not self._has_table("jh_member"):
            return {"association": "NOT_LINKED", "reason": "no HUMAN_CONFIRMED KB manifestation attached"}
        keys = self._doi_keys(candidate["paper_id"])
        out = []
        for key in keys:
            for m in self.kb.all("SELECT m.*, v.journal_id, v.version_no FROM jh_member m JOIN jh_collection_version v USING (version_id) "
                                 "WHERE m.doi_key=? AND v.version_no = (SELECT max(version_no) FROM jh_collection_version w WHERE w.journal_id = v.journal_id) "
                                 "ORDER BY v.journal_id", (key,)):
                proj = json.loads(m["projection_json"])
                tasks = sorted({t["trigger"] for t in self.kb.all("SELECT trigger FROM jh_review_task WHERE journal_id=? AND subject=?",
                                                                  (m["journal_id"], key))})
                out.append({"journal_id": m["journal_id"], "collection_version_id": m["version_id"], "doi_key": key, "category": m["category"],
                            "flags": json.loads(m["flags_json"]), "retraction_reported_ever": proj.get("retraction_reported_ever"),
                            "crossref_query_issns": proj.get("crossref_query_issns", []), "journal_review_task_triggers": tasks,
                            "screening_status": "NOT_SCREENED", "use": "READ_ONLY_CONTEXT_NEVER_A_VOTE"})
        return {"association": "EXACT_DOI_KEY_OF_HUMAN_CONFIRMED_MANIFESTATION" if out else "NOT_LINKED", "entries": out,
                "reason": None if out else "no S04J subject with this DOI key"}

    def queue(self, run_id: str) -> list[dict]:
        """Project-scoped review/acquisition queue. Every entry is a flag for humans, never a decision."""
        run = self.run(run_id)
        out = []
        for c in self.candidates(run_id):
            flags = []
            state = self.candidate_state(c)
            evidence = self.current_evidence(c["candidate_id"])
            if evidence is None:
                flags.append(("MISSING_FULLTEXT", "no evidence attached"))
            else:
                for b in json.loads(evidence["blockers_json"]):
                    kind = "EXTERNAL_EVIDENCE_HASH_ONLY" if b.endswith("_EXTERNAL") else ("RESTRICTED_RIGHTS" if b.startswith("RIGHTS_") else (
                        "VERSION_MISMATCH" if b.startswith("VERSION_") else "MISSING_FULLTEXT"))
                    flags.append((kind, b))
                for problem in self.evidence_currency(c, evidence):
                    flags.append(("CHANGED_EVIDENCE", f"{problem} since attach; re-attach and re-review"))
                if evidence["kind"] == "RV_STAGE11B_ARTIFACT":
                    flags.append(("EXTERNAL_EVIDENCE_HASH_ONLY", "verified RV-Stage11B reference; never evidence-ready (no KB bytes, no human "
                                                                 "identity confirmation): attach the human-confirmed KB asset with the same bytes"))
            if evidence is None or evidence["kind"] != "KB_ASSET":
                for link in self.kb.all("SELECT link_id, effective_state, effective_role FROM paper_asset_link_effective WHERE paper_id=? ORDER BY row_seq",
                                        (c["paper_id"],)):
                    if link["effective_state"] in ("UNVERIFIED", "AUTO_EVIDENCE_MATCH", "AMBIGUOUS"):
                        flags.append(("IDENTITY_UNCONFIRMED_ASSET", f"{link['link_id']} is {link['effective_state']}: needs human identity review"))
                    elif link["effective_state"] == "MISMATCH":
                        flags.append(("IDENTITY_MISMATCH", f"{link['link_id']} rejected as a different paper"))
            for a in self.kb.all("SELECT a.observed_json, a.bridge_status FROM bridge_attempt a JOIN bridge_row r USING (bridge_row_id) WHERE r.paper_id=? "
                                 "ORDER BY a.rowid", (c["paper_id"],)):
                observed = json.loads(a["observed_json"])
                if observed.get("doi_echo_matches_export") is False:
                    flags.append(("DOI_ECHO_MISMATCH", "PBD echoed a DOI different from the exported DOI (C20)"))
                if a["bridge_status"] in ("NEEDS_HUMAN_ACCESS", "NEEDS_HUMAN_ACTION", "DEFERRED", "FAILED", "QUARANTINED"):
                    flags.append(("ACQUISITION_BRIDGE_STATUS", a["bridge_status"]))
            attempts = self.kb.all("SELECT status, retry_after FROM fte_acquisition_attempt WHERE candidate_id=? ORDER BY rowid", (c["candidate_id"],))
            if attempts:
                last = attempts[-1]
                kind = {"RETRY_QUEUED": "RETRYABLE_ACQUISITION", "LOGIN_REQUIRED": "MANUAL_ACQUISITION_NEEDED", "BOT_CHECK": "MANUAL_ACQUISITION_NEEDED",
                        "INSTITUTIONAL_RESTRICTED": "RESTRICTED_RIGHTS", "WRONG_PDF": "IDENTITY_MISMATCH"}.get(last["status"], "ACQUISITION_ATTEMPTED")
                flags.append((kind, f"last attempt {last['status']}" + (f", retry after {last['retry_after']}" if last["retry_after"] else "")))
            for key in self._doi_keys(c["paper_id"]):
                for e in self.kb.all("SELECT event_type, reported_by FROM integrity_event WHERE subject_namespace='doi' AND subject_value=? ORDER BY rowid",
                                     (key,)):
                    flags.append(("INTEGRITY_ALERT", f"{e['event_type']} reported by {e['reported_by']} (warning; never an automatic exclusion)"))
            context = self.journal_context(c)
            for entry in context.get("entries", []):
                if entry["retraction_reported_ever"]:
                    flags.append(("INTEGRITY_ALERT", f"S04J retraction signal for {entry['doi_key']} (read-only context)"))
                for t in entry["journal_review_task_triggers"]:
                    if t in ("WORK_TYPE_VERSION_MISMATCH", "VERSION_LINK_CANDIDATE"):
                        flags.append(("JOURNAL_VS_PREPRINT_MISMATCH", f"S04J {t} (read-only context)"))
            if state["status"] == "PENDING":
                flags.append(("UNRESOLVED_REVIEW" if state["pending_reason"] == "AWAITING_REVIEWS" else
                              ("CHANGED_EVIDENCE" if state["pending_reason"].startswith("STALE") else "NEEDS_ADJUDICATION"), state["pending_reason"]))
            seen, unique = set(), []
            for f in flags:
                if f not in seen:
                    seen.add(f)
                    unique.append({"category": f[0], "detail": f[1]})
            out.append({"candidate_id": c["candidate_id"], "corpus_item_id": c["corpus_item_id"], "status": state["status"],
                        "outcome": state.get("outcome"), "flags": unique, "journal_context": context["association"], "authority": AUTHORITY,
                        "project": run["workspace_ref"]})
        return out

    # -- lifecycle ----------------------------------------------------------------------------------------------------
    def evidence_currency(self, candidate: dict, evidence) -> list[str]:
        """Problems that make attached evidence no longer current; [] when current. Cheap: database rows only, no CAS read
        (the full byte re-hash stays at attach and at the lock gate)."""
        if evidence is None:
            return []
        if evidence["kind"] == "RV_STAGE11B_ARTIFACT":
            # FTE-D18: an external reference is never evidence-ready; a ready row can only be a v01 self-asserted reference.
            return ["EXTERNAL_READY_WITHOUT_HUMAN_CONFIRMED_KB_BYTES"] if evidence["evidence_ready"] else []
        if evidence["kind"] != "KB_ASSET":
            return []
        link = self.kb.one("SELECT * FROM paper_asset_link_effective WHERE link_id=?", (evidence["link_id"],))
        problems = []
        if link["paper_id"] != candidate["paper_id"]:
            problems.append("PAPER_CHANGED")
        if link["effective_state"] != "HUMAN_CONFIRMED":
            problems.append(f"IDENTITY_NOW_{link['effective_state']}")
        if link["effective_role"] != "MAIN_TEXT":
            problems.append(f"ROLE_NOW_{link['effective_role']}")
        current = AssetService(self.kb).current_assessment(evidence["asset_sha256"], evidence["document_version_id"])
        if (current["assessment_id"] if current else None) != evidence["rights_assessment_id"]:
            problems.append("RIGHTS_REASSESSED" + (f"_LOCAL_PROCESS_{current['local_process']}" if current else ""))
        if not problems:
            run = self.run(candidate["run_id"])
            # Stored protocol read without the hash gate: an out-of-band protocol edit is refused by every write and the lock.
            protocol = json.loads(self._protocol(run["protocol_id"])["protocol_json"])
            if semantic_sha256(self._kb_asset_facts(protocol, link)) != evidence["evidence_fingerprint"]:
                problems.append("IDENTITY_REVIEWED_AGAIN_OR_FACTS_CHANGED")
        return problems

    def post_lock_drift(self, run_id: str) -> dict:
        """Current evidence drift of a LOCKED run, reported beside (never inside) the immutable locked snapshot."""
        items = [{"corpus_item_id": c["corpus_item_id"], "problems": problems} for c in self.candidates(run_id)
                 for problems in [self.evidence_currency(c, self.current_evidence(c["candidate_id"]))] if problems]
        return {"count": len(items), "items": items, "scope": "database rows only, no CAS re-hash; the locked snapshot is not rewritten",
                "action": "start a new run for further work; the locked staging snapshot stays historical"}

    def _evidence_still_valid(self, candidate: dict) -> list[str]:
        evidence = self.current_evidence(candidate["candidate_id"])
        problems = self.evidence_currency(candidate, evidence)
        if evidence and evidence["kind"] == "KB_ASSET":
            try:
                self.kb.cas.verify_object(evidence["asset_sha256"])
            except KBError as exc:
                problems.append("EVIDENCE_BYTES_" + exc.code)
        return problems

    def resolve(self, run_id: str, *, operator: str) -> dict:
        actor = operator_label(operator)
        self.run(run_id)
        self._require_open(run_id)
        states = [self.candidate_state(c) for c in self.candidates(run_id)]
        pending = [s for s in states if s["status"] != "FINAL"]
        if pending:
            raise ValidationError(f"{len(pending)} candidate(s) are not final (unresolved, conflicting, unsure or stale)", code="RUN_HAS_PENDING_CANDIDATES")
        if self.state(run_id) == "REVIEW_RESOLVED":
            return {"run_id": run_id, "state": "REVIEW_RESOLVED", "created": False, "code": "DUPLICATE_NOOP"}
        self._event(run_id, "REVIEW_RESOLVED", actor, "every candidate has a final human outcome", {"candidates": len(states)})
        return {"run_id": run_id, "state": "REVIEW_RESOLVED", "created": True, "authority": AUTHORITY}

    def lock(self, run_id: str, *, operator: str) -> dict:
        from .handoff import build_handoff_manifest
        actor = operator_label(operator)
        run = self.run(run_id)
        existing = self.kb.one("SELECT * FROM fte_lock WHERE run_id=?", (run_id,))
        if existing:
            return {"run_id": run_id, "lock_id": existing["lock_id"], "handoff_manifest_sha256": existing["handoff_manifest_sha256"], "created": False,
                    "code": "DUPLICATE_NOOP", "authority": AUTHORITY}
        self._protocol_of(run)
        if self.state(run_id) != "REVIEW_RESOLVED":
            raise ValidationError("Run must be REVIEW_RESOLVED (rkb fte resolve) before LOCKED_FOR_HANDOFF", code="RUN_NOT_RESOLVED")
        problems = {}
        for c in self.candidates(run_id):
            state = self.candidate_state(c)
            issues = sorted(set(([] if state["status"] == "FINAL" else [state["pending_reason"]]) + self._evidence_still_valid(c)))
            if issues:
                problems[c["corpus_item_id"]] = issues
        if problems:
            raise ValidationError(f"Lock refused: {len(problems)} candidate(s) unresolved or with changed evidence/identity/rights/bytes",
                                  code="LOCK_REFUSED_STALE_OR_UNRESOLVED")
        summary = self.summary(run_id)
        if not summary["invariant"]["holds"] or summary["ft_pending"]:
            raise IntegrityError("Accounting invariant failed", code="LOCK_REFUSED_ACCOUNTING")
        manifest_bytes, manifest = build_handoff_manifest(self, run_id)
        digest = self.kb.cas.put(manifest_bytes)
        row = dict(lock_id=self.kb.ids.new("ftl"), run_id=run_id, authority=AUTHORITY, handoff_manifest_sha256=digest,
                   outcomes_sha256=manifest["outcomes_sha256"], summary_json=canonical_json(manifest["summary"]), locked_by=actor,
                   locked_at_utc=self.kb.now())
        with self.kb.transaction():
            self._event(run_id, "LOCKED_FOR_HANDOFF", actor, "local NON_AUTHORITATIVE staging snapshot (not a SEALED corpus release)",
                        {"handoff_manifest_sha256": digest}, in_transaction=True)
            self.kb.insert("fte_lock", row)
        return {"run_id": run_id, "lock_id": row["lock_id"], "handoff_manifest_sha256": digest, "outcomes_sha256": row["outcomes_sha256"],
                "created": True, "authority": AUTHORITY, "summary": manifest["summary"]}


__all__ = ["FTEService", "AUTHORITY", "DISCLAIMER", "safe_text", "ATTEMPT_STATUSES", "STALE_NOT_CURRENT", "EXTERNAL_BLOCKERS"]
