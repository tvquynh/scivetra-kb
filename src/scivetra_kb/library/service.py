"""Library writes: Reprovane release import with link-policy/1, human link/identity decisions, workspace status.

Rules kept here (S01 v03 03 sections 3 and 6):
* GP-1: no automatic merge. A shared MLI that cannot auto-link mints a new paper and raises an IdentityConflict;
  the matching assertions become CONFLICTING (IA-1). Only a human resolution re-activates or withdraws one.
* PPR-3/PPR-4: a ProjectPaperReference only states "this RV release contains this item"; it is immutable.
* Importing the same export twice is a no-op; a later release adds references and never rewrites old ones.
* The KB never writes to the export folder and never opens a Reprovane database.
"""
from __future__ import annotations

from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, KBError, ValidationError
from ..metadata.divergence import classify
from ..metadata.doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi
from ..metadata.integrity import IntegrityService
from ..metadata.rv_import import FILES, _import_openalex_integrity, _read_export
from ..metadata.store import MetadataStore
from . import linking
from .common import insert_chained, json_text, latest, operator_label, require_reason, sem_of

EXPORT_FILE_ORDER = ("manifest.json", "corpus.jsonl", "corpus.csv", "SHA256SUMS.txt")


class LibraryService:
    def __init__(self, kb):
        self.kb = kb
        self.store = MetadataStore(kb)

    # -- operational journal -------------------------------------------------
    def record_import_event(self, *, kind, key, inputs, outcome, reasons, context, actor, asset=None) -> dict:
        row = dict(import_event_id=self.kb.ids.new("imp"), kind=kind, idempotency_key=key, inputs_sha256_json=json_text(sorted(inputs)),
                   outcome=outcome, reason_codes_json=json_text(sorted(reasons)), asset_sha256=asset, context_json=json_text(context),
                   actor=actor, created_at_utc=self.kb.now())
        return self.kb.insert("import_event", row)

    def record_rejection(self, *, kind, key, inputs, reason, context, actor) -> dict:
        """REJECTED import events are written in their own transaction, after the failed one rolled back."""
        with self.kb.transaction():
            return self.record_import_event(kind=kind, key=key, inputs=inputs, outcome="REJECTED", reasons=[reason], context=context, actor=actor)

    # -- workspaces ------------------------------------------------------------
    def workspace(self, label: str):
        return self.kb.one("SELECT * FROM workspace_registration WHERE operator_label=?", (label,))

    def ensure_workspace(self, label: str, actor: str) -> tuple[dict, bool]:
        if not isinstance(label, str) or not 1 <= len(label) <= 200 or any(c in label for c in "\\/\r\n\t"):
            raise ValidationError("Workspace label must be 1-200 characters without path separators", code="WORKSPACE_LABEL_INVALID")
        existing = self.workspace(label)
        if existing:
            return existing, False
        row = dict(workspace_ref=self.kb.ids.new("wsr"), operator_label=label, registered_by=actor, path_hint_sha256=None,
                   created_at_utc=self.kb.now(), semantic_sha256=semantic_sha256({"workspace_label": label}))
        self.kb.insert("workspace_registration", row)
        self._availability(row, "AVAILABLE", actor, "registered by first import")
        return row, True

    def _availability(self, workspace: dict, availability: str, actor: str, reason: str) -> tuple[dict, bool]:
        content = {"workspace": workspace["semantic_sha256"], "availability": availability, "actor": actor, "reason": reason}
        return insert_chained(self.kb, "workspace_availability_event", "event_id", "wae",
                              dict(workspace_ref=workspace["workspace_ref"], availability=availability, actor=actor, reason=reason,
                                   recorded_at_utc=self.kb.now()),
                              chain_column="workspace_ref", chain_value=workspace["workspace_ref"], content=content)

    def set_workspace_availability(self, *, workspace_label: str, availability: str, operator: str, reason: str) -> dict:
        """N26: SOURCE_UNAVAILABLE only through this explicit operator action; references and history are kept."""
        actor, reason = operator_label(operator), require_reason(reason)
        if availability not in ("AVAILABLE", "SOURCE_UNAVAILABLE", "RETIRED"):
            raise ValidationError("Unknown availability", code="AVAILABILITY_INVALID")
        workspace = self.workspace(workspace_label)
        if not workspace:
            raise ValidationError("Unknown workspace label", code="WORKSPACE_UNKNOWN")
        with self.kb.transaction():
            event, created = self._availability(workspace, availability, actor, reason)
        refs = self.kb.one("SELECT count(*) n FROM project_paper_reference WHERE workspace_ref=?", (workspace["workspace_ref"],))["n"]
        return {"workspace_ref": workspace["workspace_ref"], "availability": availability, "event_id": event["event_id"], "created": created,
                "references_kept": refs}

    # -- import ------------------------------------------------------------------
    def import_release(self, directory: str, *, workspace_label: str, operator: str, expected_manifest_sha256: str | None = None) -> dict:
        actor = operator_label(operator)
        try:
            export = _read_export(directory, unique_entities=True)
            release_sem = export["manifest"]["release_semantic_manifest_sha256"]
            if expected_manifest_sha256 and expected_manifest_sha256 != release_sem:
                raise IntegrityError("Release manifest hash differs from the operator-supplied expected value", code="EXPECTED_MANIFEST_MISMATCH")
        except KBError as exc:
            self.record_rejection(kind="REPROVANE_EXPORT", key=sha256_bytes(canonical_json([workspace_label, "unreadable"]).encode()),
                                  inputs=[], reason=exc.code, context={"stage": "read_export", "workspace_label": workspace_label}, actor=actor)
            raise
        data, manifest, items = export["data"], export["manifest"], export["items"]
        hashes = {name: sha256_bytes(data[name]) for name in FILES}
        key = sha256_bytes(canonical_json([workspace_label, [hashes[n] for n in EXPORT_FILE_ORDER]]).encode())
        summary = {"workspace_label": workspace_label, "release_id": manifest["release_id"], "release_manifest_sha256": release_sem,
                   "items": len(items), "new_import": False, "references_created": 0, "papers_minted": 0, "auto_linked": 0,
                   "link_candidates": 0, "identity_conflicts": 0, "work_family_proposals": 0, "assertions_created": 0,
                   "divergences": 0, "integrity_events": 0, "coverage_rows": 0, "papers_created": 0,
                   "expected_hash_supplied": bool(expected_manifest_sha256)}
        try:
            with self.kb.transaction():
                workspace, _ = self.ensure_workspace(workspace_label, actor)
                summary["workspace_ref"] = workspace["workspace_ref"]
                existing = self.kb.one("SELECT * FROM reprovane_release_import WHERE workspace_ref=? AND release_manifest_sha256=?",
                                       (workspace["workspace_ref"], release_sem))
                if existing:
                    same = (existing["manifest_bytes_sha256"], existing["corpus_jsonl_bytes_sha256"], existing["corpus_csv_bytes_sha256"],
                            existing["sums_bytes_sha256"]) == tuple(hashes[n] for n in EXPORT_FILE_ORDER)
                    if not same:
                        raise IntegrityError("Same release manifest already imported with different file bytes", code="EXPORT_RELEASE_CONFLICT")
                    self.record_import_event(kind="REPROVANE_EXPORT", key=key, inputs=hashes.values(), outcome="DUPLICATE_NOOP", reasons=[],
                                             context={"release_import_id": existing["release_import_id"]}, actor=actor)
                    summary["release_import_id"] = existing["release_import_id"]
                    return summary
                for name in FILES:  # REPROVANE_EXPORT_COPY: metadata files only (no PDFs exist in a corpus export)
                    self.kb.cas.put(data[name])
                event = self.record_import_event(kind="REPROVANE_EXPORT", key=key, inputs=hashes.values(), outcome="REGISTERED", reasons=[],
                                                 context={"release_id": manifest["release_id"], "items": len(items)}, actor=actor)
                release = dict(release_import_id=self.kb.ids.new("rri"), workspace_ref=workspace["workspace_ref"], release_id=manifest["release_id"],
                               release_label=manifest.get("release_label") if isinstance(manifest.get("release_label"), str) else None,
                               project_revision_id=manifest.get("project_revision_id") if isinstance(manifest.get("project_revision_id"), str) else None,
                               release_manifest_sha256=release_sem, manifest_bytes_sha256=hashes["manifest.json"],
                               corpus_jsonl_bytes_sha256=hashes["corpus.jsonl"], corpus_csv_bytes_sha256=hashes["corpus.csv"],
                               sums_bytes_sha256=hashes["SHA256SUMS.txt"], item_count=len(items), import_event_id=event["import_event_id"],
                               imported_at_utc=self.kb.now(),
                               semantic_sha256=semantic_sha256({"workspace": workspace["semantic_sha256"], "release_manifest": release_sem,
                                                                "files": [hashes[n] for n in EXPORT_FILE_ORDER]}))
                self.kb.insert("reprovane_release_import", release)
                summary.update(new_import=True, release_import_id=release["release_import_id"])
                integrity = IntegrityService(self.store)
                for index, item in enumerate(items):
                    self._link_item(workspace, release, manifest, item, index, integrity, summary)
        except KBError as exc:
            self.record_rejection(kind="REPROVANE_EXPORT", key=key, inputs=hashes.values(), reason=exc.code,
                                  context={"stage": "import", "workspace_label": workspace_label}, actor=actor)
            raise
        return summary

    # -- per item ------------------------------------------------------------
    def _paper_facts(self, paper_id: str) -> dict:
        kind_event = latest(self.kb, "paper_kind_event", "paper_id", paper_id)
        claims = [json_loads(r["item_claims_json"]) for r in self.kb.all(
            "SELECT item_claims_json FROM project_paper_reference WHERE paper_id=? ORDER BY rowid", (paper_id,))]
        kind = kind_event["manifestation_kind"] if kind_event else linking.kind_from_work_types({w for c in claims for w in c["work_types"]})
        active_dois = sorted({r["value_normalized"] for r in self.kb.all(
            "SELECT value_normalized FROM identity_assertion_effective WHERE paper_id=? AND namespace='doi' AND effective_status='ACTIVE' "
            "AND value_normalized IS NOT NULL", (paper_id,))})
        versions = [r["version_class"] for r in self.kb.all("SELECT version_class FROM document_version WHERE paper_id=?", (paper_id,))]
        return {"kind": kind, "venues": sorted({v for c in claims for v in c["venues"]}), "titles": sorted({t for c in claims for t in c["titles"]}),
                "active_dois": active_dois, "version_classes": sorted(set(versions) - {"UNKNOWN"}),
                "identity_state": paper_identity_state(self.kb, paper_id)}

    def _link_item(self, workspace, release, manifest, item, index, integrity, summary):
        kb, store = self.kb, self.store
        corpus_item_id = item["corpus_item_id"]
        evidence_ref = f"{release['release_import_id']}:{corpus_item_id}"
        identity = item.get("identity") or {}
        doi_claims = [c for c in identity.get("provider_identifiers") or [] if c.get("namespace") == "doi"]
        parses = [(c, parse_doi(c.get("original") or "", DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)) for c in doi_claims]
        valid_dois = sorted({p.comparison_key for _, p in parses if p.status == VALID})
        claims = linking.item_claims(item)
        facts = {"dois": valid_dois, "kind": linking.kind_from_work_types(claims["work_types"]), "venues": claims["venues"]}
        keys = [("doi", k) for k in valid_dois] + linking.other_mli(item)
        mli = {}
        for namespace, value in keys:
            for row in kb.all("SELECT assertion_id, paper_id, effective_status FROM identity_assertion_effective WHERE namespace=? AND value_normalized=? "
                              "AND effective_status IN ('ACTIVE','CONFLICTING') ORDER BY row_seq", (namespace, value)):
                entry = mli.setdefault(row["paper_id"], {"keys": []})
                entry["keys"].append((namespace, value, row["assertion_id"], row["effective_status"]))
        signals = {}
        for work in identity.get("openalex_work_ids") or []:
            for row in kb.all("SELECT DISTINCT paper_id FROM identity_assertion WHERE namespace='openalex_work' AND value_normalized=?", (work,)):
                signals.setdefault(row["paper_id"], {"signals": []})["signals"].append({"signal": "OPENALEX_WORK_MATCH", "value": work})
        if claims["titles"]:
            for row in kb.all("SELECT DISTINCT paper_id FROM project_paper_reference"):
                score = linking.best_title_similarity(claims["titles"], self._paper_facts(row["paper_id"])["titles"])
                if score >= linking.TITLE_SIMILARITY_THRESHOLD:
                    signals.setdefault(row["paper_id"], {"signals": []})["signals"].append({"signal": "TITLE_SIMILAR", "score": score})
        for pid in set(mli) | set(signals):
            facts_c = self._paper_facts(pid)
            if pid in mli:
                mli[pid]["facts"] = facts_c
            if pid in signals:
                signals[pid]["facts"] = facts_c
        decision = linking.decide(facts, mli, {p: s for p, s in signals.items() if p not in mli})
        if decision["auto"]:
            paper = kb.one("SELECT * FROM global_paper WHERE paper_id=?", (decision["auto"],))
            basis, link_key = "EXACT_MANIFESTATION_ID", decision["link_key"]
            summary["auto_linked"] += 1
        else:
            paper = store.ensure_paper(basis="IMPORTED_REPROVANE_ITEM", evidence_ref=evidence_ref, semantic_basis={
                "policy": linking.POLICY_VERSION, "workspace": workspace["semantic_sha256"],
                "release_manifest": release["release_manifest_sha256"], "corpus_item_id": corpus_item_id})
            basis, link_key = "NEW_PAPER_MINTED", None
            summary["papers_minted"] += 1
            summary["papers_created"] += 1
        reference = dict(reference_id=kb.ids.new("ppr"), workspace_ref=workspace["workspace_ref"], paper_id=paper["paper_id"], link_basis=basis,
                         link_key_json=json_text(link_key) if link_key else None, link_policy_version=linking.POLICY_VERSION,
                         release_import_id=release["release_import_id"], release_id=release["release_id"],
                         release_manifest_sha256=release["release_manifest_sha256"], corpus_item_id=corpus_item_id,
                         item_sha256=item["item_sha256"], publication_entity_id=item["publication_entity_id"],
                         item_claims_json=json_text(claims), created_at_utc=kb.now())
        reference["semantic_sha256"] = semantic_sha256({"workspace": workspace["semantic_sha256"], "release_manifest": release["release_manifest_sha256"],
                                                        "corpus_item_id": corpus_item_id, "item_sha256": item["item_sha256"],
                                                        "paper": paper["semantic_sha256"], "link_basis": basis, "link_key": link_key})
        kb.insert("project_paper_reference", reference)
        summary["references_created"] += 1
        shared = bool(mli) and not decision["auto"]
        doi_status = "CONFLICTING" if (len(valid_dois) > 1 or shared) else "ACTIVE"
        new_doi_assertions = []
        scope_rv = f"rv:{workspace['operator_label']}:{release['release_id']}"
        for claim, parsed in parses:
            kind = classify(claim.get("original") or "", claim.get("value"), parsed)
            if kind:
                store.add_divergence(source_ref=f"{scope_rv}:{corpus_item_id}:{claim.get('observation_id')}", rv_original=claim.get("original") or "",
                                     rv_normalized=claim.get("value"), kb_parse=parsed, kind=kind)
                summary["divergences"] += 1
            row, created = store.add_assertion(paper_id=paper["paper_id"], namespace="doi", value_original=claim.get("original") or "", parse=parsed,
                                               value_normalized=parsed.comparison_key if parsed.status == VALID else None, scope_ref=None,
                                               asserted_by="REPROVANE_EXPORT", evidence_ref=evidence_ref, registration_agency="UNKNOWN",
                                               status=doi_status if parsed.status == VALID else "CONFLICTING", paper_semantic=paper["semantic_sha256"])
            summary["assertions_created"] += int(created)
            if parsed.status == VALID:
                new_doi_assertions.append(row["assertion_id"])
        for namespace, value in linking.other_mli(item):
            _, created = store.add_assertion(paper_id=paper["paper_id"], namespace=namespace, value_original=value, parse=None, value_normalized=value,
                                             scope_ref=None, asserted_by="REPROVANE_EXPORT", evidence_ref=evidence_ref,
                                             registration_agency="NOT_APPLICABLE", status="CONFLICTING" if shared else "ACTIVE",
                                             paper_semantic=paper["semantic_sha256"])
            summary["assertions_created"] += int(created)
        for work in identity.get("openalex_work_ids") or []:
            _, created = store.add_assertion(paper_id=paper["paper_id"], namespace="openalex_work", value_original=work, parse=None, value_normalized=work,
                                             scope_ref=None, asserted_by="REPROVANE_EXPORT", evidence_ref=evidence_ref,
                                             registration_agency="NOT_APPLICABLE", status="ACTIVE", paper_semantic=paper["semantic_sha256"])
            summary["assertions_created"] += int(created)
        for namespace, value in (("reprovane_corpus_item", corpus_item_id), ("reprovane_publication_entity", item["publication_entity_id"])):
            _, created = store.add_assertion(paper_id=paper["paper_id"], namespace=namespace, value_original=value, parse=None, value_normalized=value,
                                             scope_ref=workspace["workspace_ref"], asserted_by="REPROVANE_EXPORT", evidence_ref=evidence_ref,
                                             registration_agency="NOT_APPLICABLE", status="ACTIVE", paper_semantic=paper["semantic_sha256"],
                                             scope_semantic="workspace:" + workspace["semantic_sha256"])
            summary["assertions_created"] += int(created)
        _import_openalex_integrity(kb, store, integrity, item, index, manifest, valid_dois, summary)
        # IdentityConflict: several valid DOIs (L1) or a shared MLI that could not auto-link (L4, IA-1).
        if len(valid_dois) > 1 or shared:
            matching = sorted({k[2] for c in mli.values() for k in c["keys"]})
            conflict_kind = "MULTI_DOI_ITEM" if len(valid_dois) > 1 else ("DOI_COLLISION" if len(mli) > 1 else "SHARED_MLI_WITH_CONFLICT")
            detail = {"item_dois": valid_dois, "reasons": decision["reasons"],
                      "conflict_codes": {sem_of(kb, "global_paper", "paper_id", p): c for p, c in sorted(decision["conflicts"].items()) if c}}
            conflict, created = self.raise_conflict(kind=conflict_kind, key=("doi", valid_dois[0] if len(valid_dois) == 1 else None),
                                                    papers=[paper["paper_id"], *sorted(mli)], assertions=sorted(set(new_doi_assertions) | set(matching)),
                                                    detail=detail, raised_by=reference["reference_id"], raised_by_sem=reference["semantic_sha256"])
            summary["identity_conflicts"] += int(created)
        for pid, entry in sorted({**signals, **mli}.items()):
            if pid == paper["paper_id"]:
                continue
            sigs = list(signals.get(pid, {}).get("signals", []))
            sigs += [{"signal": "MLI_MATCH", "namespace": k[0], "value": k[1]} for k in mli.get(pid, {}).get("keys", [])]
            codes = decision["conflicts"].get(pid, [])
            candidate, created = self.add_link_candidate(reference, paper, pid, sigs, codes)
            summary["link_candidates"] += int(created)
            if pid not in mli and any(s["signal"] == "OPENALEX_WORK_MATCH" for s in sigs) and \
                    (linking.DOI_CONFLICT in codes or linking.KIND_CONFLICT in codes or linking.VERSION_CONFLICT in codes):
                created = self.propose_work_family(candidate, [(paper["paper_id"], facts["kind"]), (pid, entry["facts"]["kind"])])
                summary["work_family_proposals"] += int(created)

    # -- conflicts, candidates, families --------------------------------------
    def raise_conflict(self, *, kind, key, papers, assertions, detail, raised_by, raised_by_sem) -> tuple[dict, bool]:
        kb = self.kb
        papers = list(dict.fromkeys(papers))
        content = {"kind": kind, "key": list(key), "papers": sorted(sem_of(kb, "global_paper", "paper_id", p) for p in papers),
                   "assertions": sorted(sem_of(kb, "identity_assertion", "assertion_id", a) for a in assertions), "detail": detail,
                   "raised_by": raised_by_sem}
        row = dict(conflict_id=kb.ids.new("ic"), kind=kind, key_namespace=key[0], key_value=key[1], paper_ids_json=json_text(papers),
                   assertion_ids_json=json_text(sorted(assertions)), detail_json=json_text(detail), raised_by_ref=raised_by,
                   created_at_utc=kb.now(), semantic_sha256=semantic_sha256(content))
        conflict, created = kb.insert_unique("identity_conflict", row)
        if created:
            for assertion_id in sorted(assertions):
                if kb.one("SELECT effective_status s FROM identity_assertion_effective WHERE assertion_id=?", (assertion_id,))["s"] != "CONFLICTING":
                    self._assertion_status(assertion_id, "CONFLICTING", "IDENTITY_CONFLICT_RAISED", conflict["conflict_id"], conflict["semantic_sha256"])
        return conflict, created

    def _assertion_status(self, assertion_id, status, cause_kind, cause_ref, cause_sem):
        content = {"assertion": sem_of(self.kb, "identity_assertion", "assertion_id", assertion_id), "status": status, "cause": cause_sem}
        self.kb.insert("assertion_status_event", dict(event_id=self.kb.ids.new("ase"), assertion_id=assertion_id, new_status=status,
                                                      cause_kind=cause_kind, cause_ref=cause_ref, recorded_at_utc=self.kb.now(),
                                                      semantic_sha256=semantic_sha256(content)))

    def add_link_candidate(self, reference, subject, candidate_id, signals, codes) -> tuple[dict, bool]:
        kb = self.kb
        content = {"reference": reference["semantic_sha256"], "subject": subject["semantic_sha256"],
                   "candidate": sem_of(kb, "global_paper", "paper_id", candidate_id), "signals": signals, "conflicts": sorted(codes),
                   "policy": linking.POLICY_VERSION}
        return kb.insert_unique("link_candidate", dict(
            link_candidate_id=kb.ids.new("lc"), reference_id=reference["reference_id"], subject_paper_id=subject["paper_id"],
            candidate_paper_id=candidate_id, signals_json=json_text(signals), conflict_codes_json=json_text(sorted(codes)),
            link_policy_version=linking.POLICY_VERSION, created_at_utc=kb.now(), semantic_sha256=semantic_sha256(content)))

    def propose_work_family(self, candidate, members, *, created_by="PROVIDER_RELATION_PROPOSAL") -> bool:
        """L5: a WorkFamily proposal with PROPOSED members; never confirmed automatically (WF-1)."""
        kb = self.kb
        pair = sorted(sem_of(kb, "global_paper", "paper_id", p) for p, _ in members)
        family, created = kb.insert_unique("work_family", dict(
            work_family_id=kb.ids.new("wf"), created_by=created_by, origin_ref=candidate["link_candidate_id"], created_at_utc=kb.now(),
            semantic_sha256=semantic_sha256({"papers": pair, "created_by": created_by})))
        for paper_id, kind in members:
            role = linking.KIND_ROLE.get(kind, "OTHER")
            kb.insert_unique("work_family_member", dict(
                member_id=kb.ids.new("wfm"), work_family_id=family["work_family_id"], paper_id=paper_id, role=role, confirmation="PROPOSED",
                created_at_utc=kb.now(), semantic_sha256=semantic_sha256({"family": family["semantic_sha256"],
                                                                         "paper": sem_of(kb, "global_paper", "paper_id", paper_id), "role": role})))
        return created

    # -- human decisions ---------------------------------------------------------
    def decide_link(self, *, link_candidate_id: str, decision: str, operator: str, reason: str) -> dict:
        """L6. SAME_MANIFESTATION is recorded but does NOT merge papers: merge/split is not implemented in S03 (ADR-S03-04)."""
        actor, reason = operator_label(operator), require_reason(reason)
        if decision not in ("SAME_MANIFESTATION", "DIFFERENT_MANIFESTATION_SAME_WORK", "UNRELATED", "DEFER"):
            raise ValidationError("Unknown link decision", code="LINK_DECISION_INVALID")
        kb = self.kb
        candidate = kb.one("SELECT * FROM link_candidate WHERE link_candidate_id=?", (link_candidate_id,))
        if not candidate:
            raise ValidationError("Unknown link candidate", code="UNKNOWN_REFERENCE")
        with kb.transaction():
            row, created = insert_chained(kb, "link_decision", "decision_id", "ld",
                                          dict(link_candidate_id=link_candidate_id, decision=decision, reviewer_label=actor, reason=reason,
                                               decided_at_utc=kb.now()),
                                          chain_column="link_candidate_id", chain_value=link_candidate_id,
                                          content={"candidate": candidate["semantic_sha256"], "decision": decision, "actor": actor, "reason": reason})
            family_updates = []
            if decision in ("DIFFERENT_MANIFESTATION_SAME_WORK", "UNRELATED"):
                family_updates = self._family_decision(candidate, "HUMAN_CONFIRMED" if decision != "UNRELATED" else "REJECTED", actor, reason)
        out = {"decision_id": row["decision_id"], "decision": decision, "created": created, "family_member_updates": family_updates}
        if decision == "SAME_MANIFESTATION":
            out["note"] = "Recorded only. Papers are not merged: reversible merge/split is pending (not implemented in S03)."
        return out

    def _family_decision(self, candidate, confirmation, actor, reason) -> list:
        kb = self.kb
        pair = sorted([candidate["subject_paper_id"], candidate["candidate_paper_id"]])
        family = kb.one("SELECT f.* FROM work_family f WHERE (SELECT count(DISTINCT m.paper_id) FROM work_family_member m "
                        "WHERE m.work_family_id=f.work_family_id AND m.paper_id IN (?,?)) = 2 ORDER BY f.rowid LIMIT 1", tuple(pair))
        if family is None and confirmation == "HUMAN_CONFIRMED":
            members = [(p, self._paper_facts(p)["kind"]) for p in pair]
            self.propose_work_family(candidate, members, created_by="OPERATOR")
            family = kb.one("SELECT * FROM work_family WHERE origin_ref=? AND created_by='OPERATOR'", (candidate["link_candidate_id"],))
        if family is None:
            return []
        updates = []
        for member in kb.all("SELECT * FROM work_family_member WHERE work_family_id=? AND paper_id IN (?,?) ORDER BY rowid",
                             (family["work_family_id"], *pair)):
            self._member_event(member, confirmation, actor, reason)
            updates.append({"member_id": member["member_id"], "confirmation": confirmation})
        return updates

    def _member_event(self, member, confirmation, actor, reason):
        return insert_chained(self.kb, "work_family_member_event", "event_id", "wfme",
                              dict(member_id=member["member_id"], confirmation=confirmation, actor=actor, reason=reason, recorded_at_utc=self.kb.now()),
                              chain_column="member_id", chain_value=member["member_id"],
                              content={"member": member["semantic_sha256"], "confirmation": confirmation, "actor": actor, "reason": reason})

    def confirm_family_member(self, *, member_id: str, confirmation: str, operator: str, reason: str) -> dict:
        actor, reason = operator_label(operator), require_reason(reason)
        if confirmation not in ("HUMAN_CONFIRMED", "REJECTED", "PROPOSED"):
            raise ValidationError("Unknown confirmation", code="CONFIRMATION_INVALID")
        member = self.kb.one("SELECT * FROM work_family_member WHERE member_id=?", (member_id,))
        if not member:
            raise ValidationError("Unknown family member", code="UNKNOWN_REFERENCE")
        with self.kb.transaction():
            row, created = self._member_event(member, confirmation, actor, reason)
        return {"event_id": row["event_id"], "confirmation": confirmation, "created": created}

    def resolve_conflict(self, *, conflict_id: str, action: str, operator: str, reason: str, evidence_refs: list[str],
                         activate: list[str] = (), withdraw: list[str] = ()) -> dict:
        """Human-anchored identity resolution (IA-1). ``evidence_refs`` must name assertion ids or a sha256:<document>."""
        actor, reason = operator_label(operator), require_reason(reason)
        kb = self.kb
        conflict = kb.one("SELECT * FROM identity_conflict WHERE conflict_id=?", (conflict_id,))
        if not conflict:
            raise ValidationError("Unknown identity conflict", code="UNKNOWN_REFERENCE")
        if action not in ("ASSERTIONS_WITHDRAWN", "DOI_SELECTED_FOR_ITEM", "CONFIRMED_DISTINCT_MANIFESTATIONS", "DEFER"):
            raise ValidationError("Unknown resolution action", code="RESOLUTION_ACTION_INVALID")
        if not evidence_refs:
            raise ValidationError("A human resolution needs at least one evidence reference", code="RESOLUTION_EVIDENCE_REQUIRED")
        for ref in evidence_refs:
            ok = (ref.startswith("sha256:") and kb.one("SELECT 1 FROM operator_document WHERE document_sha256=?", (ref[7:],))) or \
                 kb.one("SELECT 1 FROM identity_assertion WHERE assertion_id=?", (ref,)) or \
                 kb.one("SELECT 1 FROM provider_snapshot WHERE snapshot_id=?", (ref,))
            if not ok:
                raise ValidationError("Evidence reference does not resolve", code="RESOLUTION_EVIDENCE_UNRESOLVED")
        updates = [{"assertion_id": a, "new_status": "ACTIVE"} for a in activate] + [{"assertion_id": a, "new_status": "RETRACTED_ASSERTION"} for a in withdraw]
        if action == "DOI_SELECTED_FOR_ITEM" and not activate:
            raise ValidationError("DOI_SELECTED_FOR_ITEM must activate one assertion", code="RESOLUTION_ACTION_INVALID")
        if action == "ASSERTIONS_WITHDRAWN" and (activate or not withdraw):
            raise ValidationError("ASSERTIONS_WITHDRAWN withdraws assertions only", code="RESOLUTION_ACTION_INVALID")
        if action in ("DEFER",) and updates:
            raise ValidationError("DEFER changes no assertion", code="RESOLUTION_ACTION_INVALID")
        named = set(json_loads(conflict["assertion_ids_json"]))
        if any(u["assertion_id"] not in named for u in updates):
            raise ValidationError("Resolution may only update assertions named by the conflict", code="RESOLUTION_ASSERTION_OUTSIDE_CONFLICT")
        activated_keys = {}
        for assertion_id in activate:
            row = kb.one("SELECT * FROM identity_assertion WHERE assertion_id=?", (assertion_id,))
            activated_keys.setdefault((row["namespace"], row["value_normalized"]), set()).add(row["paper_id"])
            if len(activated_keys[(row["namespace"], row["value_normalized"])]) > 1:
                raise ValidationError("Activation would map one identifier to two papers (IA-1)", code="IA1_WOULD_BE_VIOLATED")
            if row["value_normalized"] is None:
                raise ValidationError("Only a VALID identifier can be activated", code="RESOLUTION_ACTION_INVALID")
            others = kb.all("SELECT DISTINCT paper_id FROM identity_assertion_effective WHERE namespace=? AND value_normalized=? "
                            "AND effective_status='ACTIVE' AND paper_id<>?", (row["namespace"], row["value_normalized"], row["paper_id"]))
            withdrawn_papers = {kb.one("SELECT paper_id FROM identity_assertion WHERE assertion_id=?", (w,))["paper_id"] for w in withdraw}
            if [o for o in others if o["paper_id"] not in withdrawn_papers]:
                raise ValidationError("Activation would map one identifier to two papers (IA-1)", code="IA1_WOULD_BE_VIOLATED")
        with kb.transaction():
            event, created = insert_chained(kb, "identity_resolution_event", "event_id", "ire",
                                            dict(conflict_id=conflict_id, action=action, assertion_updates_json=json_text(updates),
                                                 evidence_refs_json=json_text(sorted(evidence_refs)), actor=actor, reason=reason, decided_at_utc=kb.now()),
                                            chain_column="conflict_id", chain_value=conflict_id,
                                            content={"conflict": conflict["semantic_sha256"], "action": action,
                                                     "updates": [[sem_of(kb, "identity_assertion", "assertion_id", u["assertion_id"]), u["new_status"]] for u in updates],
                                                     "evidence": sorted(evidence_refs), "actor": actor, "reason": reason})
            if created:
                for update in updates:
                    self._assertion_status(update["assertion_id"], update["new_status"], "HUMAN_RESOLUTION", event["event_id"], event["semantic_sha256"])
        return {"event_id": event["event_id"], "action": action, "created": created, "assertion_updates": updates,
                "conflict_state": conflict_state(kb, conflict_id)}

    def set_manifestation_kind(self, *, paper_id: str, kind: str, operator: str, reason: str, claim_refs: list[str]) -> dict:
        """manifestation_kind is an S column: changed only by an operator event citing claims (03 section 3.1)."""
        actor, reason = operator_label(operator), require_reason(reason)
        kb = self.kb
        if not kb.one("SELECT 1 FROM global_paper WHERE paper_id=?", (paper_id,)):
            raise ValidationError("Unknown paper", code="UNKNOWN_REFERENCE")
        if not claim_refs:
            raise ValidationError("A kind change must cite at least one claim reference", code="CLAIM_REFS_REQUIRED")
        with kb.transaction():
            row, created = insert_chained(kb, "paper_kind_event", "event_id", "pke",
                                          dict(paper_id=paper_id, manifestation_kind=kind, claim_refs_json=json_text(sorted(claim_refs)), actor=actor,
                                               reason=reason, recorded_at_utc=kb.now()),
                                          chain_column="paper_id", chain_value=paper_id,
                                          content={"paper": sem_of(kb, "global_paper", "paper_id", paper_id), "kind": kind,
                                                   "claims": sorted(claim_refs), "actor": actor, "reason": reason})
        return {"event_id": row["event_id"], "manifestation_kind": kind, "created": created}


def json_loads(text):
    import json
    return json.loads(text)


def conflict_state(kb, conflict_id: str) -> str:
    event = latest(kb, "identity_resolution_event", "conflict_id", conflict_id)
    return "OPEN" if event is None or event["action"] == "DEFER" else "RESOLVED"


def paper_identity_state(kb, paper_id: str) -> str:
    """Projection: DISPUTED while an unresolved IdentityConflict names the paper; otherwise the stored state."""
    for row in kb.all("SELECT c.conflict_id FROM identity_conflict c, json_each(c.paper_ids_json) p WHERE p.value=?", (paper_id,)):
        if conflict_state(kb, row["conflict_id"]) == "OPEN":
            return "DISPUTED"
    return kb.one("SELECT identity_state FROM global_paper WHERE paper_id=?", (paper_id,))["identity_state"]
