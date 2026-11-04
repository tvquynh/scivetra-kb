"""S05 handoff manifest (``fte-handoff/1``), deterministic export (``fte-export/1``) and offline verification.

Portable outputs contain metadata and hashes only: never PDF bytes, excerpts, free-text notes (only their SHA-256),
absolute paths, URLs with query strings, sessions or credentials. Every file states ``authority: NON_AUTHORITATIVE``.
Identical frozen content gives identical bytes. ``verify_export`` recomputes SHA256SUMS, every decision and adjudication
hash (replay), the outcomes digest and the manifest cross-references, so any edit is detected.
"""
from __future__ import annotations

import csv
import io
import json
import os
import shutil

from ..bridge.service import guard_path_text, path_hash
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, ValidationError
from ..core.paths import safe_data_root, safe_input_file
from ..library.common import operator_label
from .service import AUTHORITY, DISCLAIMER, FTEService, note_sha

HANDOFF_SCHEMA = "fte-handoff/1"
EXPORT_SCHEMA = "fte-export/1"
CANDIDATE_COLUMNS = ("authority", "ordinal", "corpus_item_id", "item_sha256", "publication_entity_id", "paper_id", "status", "outcome", "basis",
                     "reason_codes", "related_corpus_item_id", "pending_reason", "evidence_kind", "evidence_ready", "evidence_blockers", "asset_sha256",
                     "version_class", "rights_class", "byte_verification", "evidence_fingerprint", "queue_categories", "journal_context")


def _decision_record(d, label) -> dict:
    portable = {"candidate": d["candidate_id"], "item_sha256": d["item_sha256"], "reviewer": label, "outcome": d["outcome"],
                "reasons": json.loads(d["reason_codes_json"]), "note_sha256": note_sha(d["note"]),
                "locator": json.loads(d["locator_json"]) if d["locator_json"] else None, "related": d["related_corpus_item_id"],
                "evidence": d["evidence_fingerprint"], "protocol": d["protocol_sha256"], "supersedes": d["supersedes_decision_id"]}
    return {"decision_id": d["decision_id"], "decided_at_utc": d["decided_at_utc"], "decision_sha256": d["decision_sha256"],
            "content": portable, "authority": AUTHORITY}


def _adjudication_record(a, label) -> dict:
    portable = {"candidate": a["candidate_id"], "adjudicator": label, "outcome": a["outcome"], "reasons": json.loads(a["reason_codes_json"]),
                "note_sha256": note_sha(a["note"]), "related": a["related_corpus_item_id"], "resolves": json.loads(a["resolved_decision_ids_json"]),
                "evidence": a["evidence_fingerprint"], "protocol": a["protocol_sha256"], "supersedes": a["supersedes_adjudication_id"]}
    return {"adjudication_id": a["adjudication_id"], "decided_at_utc": a["decided_at_utc"], "adjudication_sha256": a["adjudication_sha256"],
            "content": portable, "authority": AUTHORITY}


def _evidence_record(ev) -> dict | None:
    if ev is None:
        return None
    return {"evidence_id": ev["evidence_id"], "kind": ev["kind"], "asset_sha256": ev["asset_sha256"], "document_version_id": ev["document_version_id"],
            "version_class": ev["version_class"], "link_state_at_attach": ev["link_state_at_attach"], "rights_class": ev["rights_class"],
            "evidence_ready": bool(ev["evidence_ready"]), "blockers": json.loads(ev["blockers_json"]), "byte_verification": ev["byte_verification"],
            "external_ref": json.loads(ev["external_ref_json"]) if ev["external_ref_json"] else None, "evidence_fingerprint": ev["evidence_fingerprint"],
            "supersedes_evidence_id": ev["supersedes_evidence_id"]}


def collect(svc: FTEService, run_id: str) -> dict:
    kb = svc.kb
    run = svc.run(run_id)
    labels = {r["reviewer_id"]: r["label"] for r in kb.all("SELECT reviewer_id, label FROM fte_reviewer WHERE run_id=?", (run_id,))}
    candidates, decisions, adjudications = [], [], []
    queue = {q["candidate_id"]: q for q in svc.queue(run_id)}
    for c in svc.candidates(run_id):
        state = svc.candidate_state(c)
        ev = svc.current_evidence(c["candidate_id"])
        candidates.append({"ordinal": c["ordinal"], "candidate_id": c["candidate_id"], "corpus_item_id": c["corpus_item_id"], "item_sha256": c["item_sha256"],
                           "publication_entity_id": c["publication_entity_id"], "paper_id": c["paper_id"], "status": state["status"],
                           "outcome": state.get("outcome"), "basis": state.get("basis"), "reason_codes": state.get("reason_codes", []),
                           "related_corpus_item_id": state.get("related_corpus_item_id"), "pending_reason": state.get("pending_reason"),
                           "evidence_problems": state.get("evidence_problems", []),
                           "current_decision_ids": state["decision_ids"], "adjudication_id": state.get("adjudication_id"),
                           "evidence": _evidence_record(ev), "evidence_fingerprint": state["evidence_fingerprint"],
                           "queue": queue[c["candidate_id"]]["flags"], "journal_context": queue[c["candidate_id"]]["journal_context"],
                           "assigned_reviewers": [labels[a["reviewer_id"]] for a in kb.all(
                               "SELECT reviewer_id FROM fte_assignment WHERE candidate_id=? ORDER BY slot", (c["candidate_id"],))]})
        decisions += [_decision_record(d, labels[d["reviewer_id"]]) for d in kb.all(
            "SELECT * FROM fte_decision WHERE candidate_id=? ORDER BY rowid", (c["candidate_id"],))]
        adjudications += [_adjudication_record(a, labels[a["adjudicator_id"]]) for a in kb.all(
            "SELECT * FROM fte_adjudication WHERE candidate_id=? ORDER BY rowid", (c["candidate_id"],))]
    attempts = [{"attempt_id": a["attempt_id"], "candidate_id": a["candidate_id"], "status": a["status"], "source_description": a["source_description"],
                 "attempted_on": a["attempted_on"], "retry_after": a["retry_after"], "note_sha256": sha256_bytes(a["note"].encode()) if a["note"] else None,
                 "kind": "OPERATIONAL_NOT_A_DECISION", "authority": AUTHORITY}
                for a in kb.all("SELECT a.* FROM fte_acquisition_attempt a JOIN fte_candidate c USING (candidate_id) WHERE c.run_id=? ORDER BY a.rowid",
                                (run_id,))]
    roster = [{"label": r["label"], "role": r["role"], "actor_type": r["actor_type"], "conflicts": json.loads(r["conflicts_json"])}
              for r in kb.all("SELECT * FROM fte_reviewer WHERE run_id=? ORDER BY label", (run_id,))]
    protocol = json.loads(kb.one("SELECT protocol_json FROM fte_protocol WHERE protocol_id=?", (run["protocol_id"],))["protocol_json"])
    return {"run": run, "candidates": candidates, "decisions": decisions, "adjudications": adjudications, "attempts": attempts, "roster": roster,
            "protocol": protocol, "summary": svc.summary(run_id),
            "events": [{k: e[k] for k in ("seq", "new_state", "actor", "reason", "created_at_utc")}
                       for e in kb.all("SELECT * FROM fte_run_event WHERE run_id=? ORDER BY seq", (run_id,))]}


def outcomes_digest(candidates: list[dict]) -> str:
    return semantic_sha256([[c["corpus_item_id"], c["item_sha256"], c["status"], c["outcome"], c["basis"], c["reason_codes"], c["related_corpus_item_id"],
                             c["evidence_fingerprint"], c["current_decision_ids"], c["adjudication_id"]] for c in candidates])


def build_handoff_manifest(svc: FTEService, run_id: str) -> tuple[bytes, dict]:
    data = collect(svc, run_id)
    run = data["run"]
    summary = dict(data["summary"], status="LOCKED_FOR_HANDOFF_STAGING_SNAPSHOT", state="LOCKED_FOR_HANDOFF")
    manifest = {
        "schema": HANDOFF_SCHEMA, "authority": AUTHORITY, "disclaimer": DISCLAIMER,
        "not": ["Reprovane RV-FTE run", "SEALED corpus release", "official PRISMA count", "authenticated reviewer identity"],
        "run": {k: run[k] for k in ("run_id", "label", "release_id", "release_manifest_sha256", "project_revision_id", "screening_run_id",
                                    "screening_manifest_sha256", "item_count", "item_set_sha256", "protocol_sha256", "roster_sha256", "assignment_sha256",
                                    "created_by", "created_at_utc", "client_version")},
        "source_verification": json.loads(run["source_verification_json"]),
        "protocol": data["protocol"], "roster": data["roster"],
        "candidates": [{k: c[k] for k in ("ordinal", "corpus_item_id", "item_sha256", "publication_entity_id", "status", "outcome", "basis", "reason_codes",
                                          "related_corpus_item_id", "current_decision_ids", "adjudication_id", "evidence", "evidence_fingerprint")}
                       for c in data["candidates"]],
        "decision_journal": [{"decision_id": d["decision_id"], "decision_sha256": d["decision_sha256"], "supersedes": d["content"]["supersedes"]}
                             for d in data["decisions"]],
        "adjudication_journal": [{"adjudication_id": a["adjudication_id"], "adjudication_sha256": a["adjudication_sha256"],
                                  "supersedes": a["content"]["supersedes"]} for a in data["adjudications"]],
        "outcomes_sha256": outcomes_digest(data["candidates"]), "summary": summary,
        "future_reprovane_handoff": "proposal only (docs/RV_FTE_FUTURE_HANDOFF_CONTRACT.md); OAG-5 CLOSED",
    }
    return (canonical_json(manifest) + "\n").encode("utf-8"), manifest


def _csv(rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CANDIDATE_COLUMNS)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else ("; ".join(r[c]) if isinstance(r[c], list) else str(r[c])) for c in CANDIDATE_COLUMNS])
    return buffer.getvalue().encode("utf-8")


def _jsonl(rows) -> bytes:
    return "".join(canonical_json(r) + "\n" for r in rows).encode("utf-8")


def build_export(svc: FTEService, run_id: str) -> tuple[dict, dict]:
    data = collect(svc, run_id)
    lock = svc.kb.one("SELECT * FROM fte_lock WHERE run_id=?", (run_id,))
    rows = []
    for c in data["candidates"]:
        ev = c["evidence"] or {}
        rows.append({"authority": AUTHORITY, "ordinal": c["ordinal"], "corpus_item_id": c["corpus_item_id"], "item_sha256": c["item_sha256"],
                     "publication_entity_id": c["publication_entity_id"], "paper_id": c["paper_id"], "status": c["status"], "outcome": c["outcome"],
                     "basis": c["basis"], "reason_codes": c["reason_codes"], "related_corpus_item_id": c["related_corpus_item_id"],
                     "pending_reason": c["pending_reason"], "evidence_kind": ev.get("kind"), "evidence_ready": ev.get("evidence_ready"),
                     "evidence_blockers": ev.get("blockers") or [], "asset_sha256": ev.get("asset_sha256"), "version_class": ev.get("version_class"),
                     "rights_class": ev.get("rights_class"), "byte_verification": ev.get("byte_verification"),
                     "evidence_fingerprint": c["evidence_fingerprint"], "queue_categories": sorted({f["category"] for f in c["queue"]}),
                     "journal_context": c["journal_context"]})
    files = {
        "summary.json": (canonical_json(data["summary"]) + "\n").encode("utf-8"),
        "candidates.csv": _csv(rows),
        "candidates.jsonl": _jsonl([dict(c, authority=AUTHORITY) for c in data["candidates"]]),
        "decisions.jsonl": _jsonl(data["decisions"]),
        "adjudications.jsonl": _jsonl(data["adjudications"]),
        "acquisition_attempts.jsonl": _jsonl(data["attempts"]),
        "run_events.jsonl": _jsonl(data["events"]),
    }
    if lock:
        files["handoff_manifest.json"] = svc.kb.cas.read(lock["handoff_manifest_sha256"])
    manifest = {"schema": EXPORT_SCHEMA, "authority": AUTHORITY, "disclaimer": DISCLAIMER, "run_id": run_id,
                "state": "LOCKED_FOR_HANDOFF" if lock else "UNLOCKED_PROVISIONAL",
                "handoff_manifest_sha256": lock["handoff_manifest_sha256"] if lock else None,
                "outcomes_sha256": outcomes_digest(data["candidates"]), "release_manifest_sha256": data["run"]["release_manifest_sha256"],
                "protocol_sha256": data["run"]["protocol_sha256"], "files": {n: {"sha256": sha256_bytes(b), "bytes": len(b)} for n, b in sorted(files.items())},
                "content_policy": "metadata and hashes only: no PDF bytes, excerpts, free-text notes (SHA-256 only), paths, URLs with queries or secrets"}
    files["export_manifest.json"] = (canonical_json(manifest) + "\n").encode("utf-8")
    files["SHA256SUMS.txt"] = "".join(f"{sha256_bytes(b)}  {n}\n" for n, b in sorted(files.items())).encode("utf-8")
    return files, manifest


def export_run(kb, run_id: str, out_dir: str, *, operator: str) -> dict:
    operator_label(operator)
    guard_path_text(out_dir, "--out-dir")
    target = safe_data_root(out_dir, must_exist=False)
    if target == kb.root or target.is_relative_to(kb.root) or kb.root.is_relative_to(target):
        raise ValidationError("--out-dir must be outside the KB data root", code="OUT_DIR_INVALID")
    files, manifest = build_export(FTEService(kb), run_id)
    digest = sha256_bytes(files["export_manifest.json"])
    folder = target / f"fte_export_{run_id[-8:].lower()}_{'locked' if manifest['state'] == 'LOCKED_FOR_HANDOFF' else 'provisional'}_{digest[:8]}"
    result = {"run_id": run_id, "folder_name": folder.name, "export_manifest_sha256": digest, "state": manifest["state"], "authority": AUTHORITY,
              "path_hash": path_hash(target)}
    if folder.exists():
        present = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        if present != files:
            raise ValidationError("Export folder exists with different content", code="EXPORT_FOLDER_EXISTS_DIFFERENT")
        return dict(result, written=False, code="EXPORT_ALREADY_PRESENT_IDENTICAL")
    target.mkdir(parents=True, exist_ok=True)
    staging = target / (folder.name + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for name, data in sorted(files.items()):
        with open(staging / name, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    os.replace(staging, folder)
    return dict(result, written=True)


def verify_export(export_dir: str) -> dict:
    """Offline verification of an exported folder: checksums, manifest cross-hashes, outcomes digest, journal consistency."""
    guard_path_text(export_dir, "--export-dir")
    root = safe_data_root(export_dir, must_exist=True)
    sums = safe_input_file(str(root / "SHA256SUMS.txt"), max_bytes=1024 * 1024).read_text(encoding="utf-8").splitlines()
    problems = []
    listed = {}
    for line in sums:
        digest, _, name = line.partition("  ")
        listed[name] = digest
        path = root / name
        if "/" in name or "\\" in name or not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
            problems.append(f"CHECKSUM_MISMATCH:{name}")
    present = {p.name for p in root.iterdir() if p.is_file()} - {"SHA256SUMS.txt"}
    if present != set(listed):
        problems.append("FILE_SET_MISMATCH")
    if problems:
        return {"valid": False, "problems": problems}
    manifest = json.loads((root / "export_manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        if listed.get(name) != meta["sha256"]:
            problems.append(f"MANIFEST_MISMATCH:{name}")
    if manifest.get("authority") != AUTHORITY:
        problems.append("AUTHORITY_NOT_NON_AUTHORITATIVE")
    candidates = [json.loads(l) for l in (root / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    if outcomes_digest(candidates) != manifest["outcomes_sha256"]:
        problems.append("OUTCOMES_DIGEST_MISMATCH")
    decisions = {json.loads(l)["decision_id"]: json.loads(l) for l in (root / "decisions.jsonl").read_text(encoding="utf-8").splitlines()}
    # Replay: every decision / adjudication hash is recomputed from its exported content.
    for d in decisions.values():
        if semantic_sha256(d["content"]) != d["decision_sha256"]:
            problems.append(f"DECISION_HASH_REPLAY_FAILED:{d['decision_id']}")
    for line in (root / "adjudications.jsonl").read_text(encoding="utf-8").splitlines():
        a = json.loads(line)
        if semantic_sha256(a["content"]) != a["adjudication_sha256"]:
            problems.append(f"ADJUDICATION_HASH_REPLAY_FAILED:{a['adjudication_id']}")
    for c in candidates:
        for did in c["current_decision_ids"]:
            if did not in decisions:
                problems.append(f"MISSING_DECISION:{did}")
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    final = sum(1 for c in candidates if c["status"] == "FINAL")
    if summary["ft_include"] + summary["ft_exclude"] + summary["ft_not_retrieved"] + summary["ft_pending"] != summary["ta_candidate_count"] \
            or summary["ta_candidate_count"] != len(candidates) or summary["ft_pending"] != len(candidates) - final:
        problems.append("ACCOUNTING_INVARIANT_FAILED")
    if manifest["state"] == "LOCKED_FOR_HANDOFF":
        handoff = (root / "handoff_manifest.json").read_bytes()
        if sha256_bytes(handoff) != manifest["handoff_manifest_sha256"]:
            problems.append("HANDOFF_MANIFEST_HASH_MISMATCH")
        hm = json.loads(handoff)
        if hm.get("authority") != AUTHORITY or hm["outcomes_sha256"] != outcomes_digest(hm["candidates"]):
            problems.append("HANDOFF_MANIFEST_INCONSISTENT")
        journal = {d["decision_id"]: d["decision_sha256"] for d in hm["decision_journal"]}
        if any(journal.get(i) != d["decision_sha256"] for i, d in decisions.items()):
            problems.append("DECISION_JOURNAL_MISMATCH")
    return {"valid": not problems, "problems": problems, "state": manifest["state"], "authority": manifest.get("authority"),
            "candidates": len(candidates), "scope": "self-consistency of the exported files; not a signature"}


def verify_lock(kb, run_id: str) -> dict:
    """Rebuild the handoff manifest from the KB and compare with the locked CAS bytes (detects any later change)."""
    lock = kb.one("SELECT * FROM fte_lock WHERE run_id=?", (run_id,))
    if not lock:
        raise ValidationError("Run is not locked", code="RUN_NOT_LOCKED")
    stored = kb.cas.read(lock["handoff_manifest_sha256"])
    if sha256_bytes(stored) != lock["handoff_manifest_sha256"]:
        raise IntegrityError("Locked handoff manifest bytes changed in CAS", code="CAS_HASH_MISMATCH")
    svc = FTEService(kb)
    rebuilt, manifest = build_handoff_manifest(svc, run_id)
    current = json.loads(stored)
    same_core = {k: v for k, v in manifest.items() if k != "summary"} == {k: v for k, v in current.items() if k != "summary"}
    return {"run_id": run_id, "valid": same_core and manifest["outcomes_sha256"] == lock["outcomes_sha256"], "handoff_manifest_sha256": lock["handoff_manifest_sha256"],
            "authority": AUTHORITY, "current_evidence_drift": svc.post_lock_drift(run_id),
            "scope": "valid = the locked snapshot is unchanged; current_evidence_drift = later identity/rights changes, reported separately"}


__all__ = ["build_handoff_manifest", "build_export", "export_run", "verify_export", "verify_lock", "collect", "outcomes_digest"]
