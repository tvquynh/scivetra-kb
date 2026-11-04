"""Publication-integrity service (S01 v03 03 section 5.3, 04 section 5).

Safety properties (V03-F01):
* IntegrityEvents are created only from affirmative provider entries or
  documented operator reports, and are never altered or removed.
* Silence, omission in a later snapshot, 404, failures and ``false`` values are
  recorded only as IntegrityCoverage. They never create counter-evidence and
  never change an alert.
* IntegrityCounterEvidence is recorded only through ``record_counter_evidence``
  by an identified operator with a resolvable primary document and anchor; the
  database triggers enforce the same rules at the persistence boundary.
"""
from __future__ import annotations

import json
import re

from ..core.canonical import canonical_json, is_sha256_hex, semantic_sha256
from ..core.errors import ValidationError
from .doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi

EVENT_TYPES = ["CORRECTION", "EXPRESSION_OF_CONCERN", "NEW_VERSION", "OTHER", "REMOVAL", "RETRACTION", "WITHDRAWAL"]
STRONG_TYPES = {"RETRACTION", "WITHDRAWAL", "REMOVAL", "EXPRESSION_OF_CONCERN"}
COUNTER_KINDS = {"NOTICE_STATES_ERROR", "REINSTATEMENT_NOTICE", "DIFFERENT_SUBJECT_DETERMINATION", "PROVIDER_AFFIRMATIVE_STATEMENT"}
REVIEW_DECISIONS = {"CONFIRMED_REPORT", "REPORT_NOT_APPLICABLE_WITH_EVIDENCE", "INCONCLUSIVE"}
REVIEW_TRIGGERS = {"NEW_REPORT", "REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT", "POSSIBLE_OPPOSING_NOTICE", "OPERATOR_REQUEST"}
OPERATOR_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RESERVED_LABELS = {"system", "ai", "rkb", "auto", "automatic", "bot", "model", "scivetra"}
PROVIDER_OF = {"CROSSREF_PUBLISHER_UPDATE": "CROSSREF", "CROSSREF_RETRACTION_WATCH": "CROSSREF", "DATACITE_RELATION": "DATACITE",
               "OPENALEX_IS_RETRACTED_VIA_REPROVANE": "OPENALEX_VIA_REPROVANE_EXPORT", "OPERATOR": "OPERATOR_CHECK"}
OMISSION_OUTCOMES = {"REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT", "SUBJECT_NOT_FOUND", "CHECK_FAILED"}


def _order(record_id: str) -> str:
    return record_id.split("_", 1)[1]


def operator_label(label: str) -> str:
    if not isinstance(label, str) or not OPERATOR_LABEL.match(label) or label.lower() in RESERVED_LABELS:
        raise ValidationError("An identified human operator label is required", code="OPERATOR_LABEL_REQUIRED")
    return "OPERATOR:" + label


def json_pointer(document, pointer: str):
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    node = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


class IntegrityService:
    def __init__(self, store):
        self.store = store
        self.kb = store.kb

    # -- events and sightings ------------------------------------------------
    def event_semantic(self, ev: dict) -> str:
        return semantic_sha256({k: ev.get(k) for k in ("subject_namespace", "subject_value", "event_type", "event_subtype",
                                                         "notice_doi", "reported_by", "record_id")})

    def record_provider_events(self, snapshot: dict, events: list[dict]) -> list[tuple[dict, bool]]:
        out = []
        for ev in events:
            sem = self.event_semantic(ev)
            existing = self.kb.one("SELECT * FROM integrity_event WHERE semantic_sha256=?", (sem,))
            created = existing is None
            if created:
                papers = self.store.papers_for_doi_key(ev["subject_value"]) if ev["subject_namespace"] == "doi" else []
                existing = self.kb.insert("integrity_event", dict(
                    event_id=self.kb.ids.new("ige"), subject_paper_id=papers[0] if len(papers) == 1 else None,
                    subject_namespace=ev["subject_namespace"], subject_value=ev["subject_value"], event_type=ev["event_type"],
                    event_subtype=ev.get("event_subtype"), notice_doi=ev.get("notice_doi"), reported_by=ev["reported_by"],
                    record_id=ev.get("record_id"), first_snapshot_id=snapshot["snapshot_id"], source_field_path=ev["source_field_path"],
                    operator_evidence_ref=None, event_date_json=canonical_json(ev.get("event_date")) if ev.get("event_date") is not None else None,
                    payload_json=canonical_json(ev.get("payload") or {}), created_at_utc=self.kb.now(), semantic_sha256=sem))
            if not self.kb.one("SELECT 1 FROM integrity_event_sighting WHERE event_id=? AND snapshot_id=?",
                               (existing["event_id"], snapshot["snapshot_id"])):
                self.kb.insert("integrity_event_sighting", dict(sighting_id=self.kb.ids.new("igs"), event_id=existing["event_id"],
                                                                snapshot_id=snapshot["snapshot_id"], source_field_path=ev["source_field_path"]))
            out.append((existing, created))
            if created and ev["event_type"] not in STRONG_TYPES:
                self._possible_opposing_notice(existing)
        return out

    def _possible_opposing_notice(self, new_event: dict) -> None:
        """IC-2: a later, weaker notice on a subject with a strong report is a review task, never counter-evidence."""
        strong = [r["event_id"] for r in self.kb.all(
            "SELECT event_id, event_type FROM integrity_event WHERE subject_namespace=? AND subject_value=? AND event_id<>?",
            (new_event["subject_namespace"], new_event["subject_value"], new_event["event_id"])) if r["event_type"] in STRONG_TYPES]
        if strong:
            self.store.add_task(subject_namespace=new_event["subject_namespace"], subject_value=new_event["subject_value"],
                                trigger="POSSIBLE_OPPOSING_NOTICE", event_ids=strong + [new_event["event_id"]], coverage=None,
                                context={"new_notice_doi": new_event["notice_doi"], "new_event_type": new_event["event_type"],
                                         "note": "Human must read the notice; no counter-evidence is created automatically"})

    # -- coverage ---------------------------------------------------------------
    def add_coverage(self, *, subject_namespace: str, subject_value: str, provider: str, snapshot: dict | None, outcome: str,
                     scope_note: dict, prior_snapshot: dict | None = None, affected_event_ids: list[str] | None = None,
                     document_ref: str | None = None) -> tuple[dict, bool]:
        affected = sorted(affected_event_ids or [])
        affected_sems = sorted(self.kb.one("SELECT semantic_sha256 s FROM integrity_event WHERE event_id=?", (e,))["s"] for e in affected)
        content = {"subject": [subject_namespace, subject_value], "provider": provider,
                   "snapshot": snapshot["semantic_sha256"] if snapshot else None,
                   "prior": prior_snapshot["semantic_sha256"] if prior_snapshot else None, "affected": affected_sems,
                   "outcome": outcome, "scope_note": scope_note, "document_ref": document_ref}
        row = dict(coverage_id=self.kb.ids.new("igc"), subject_namespace=subject_namespace, subject_value=subject_value, provider=provider,
                   snapshot_id=snapshot["snapshot_id"] if snapshot else None, document_ref=document_ref,
                   prior_snapshot_id=prior_snapshot["snapshot_id"] if prior_snapshot else None,
                   affected_event_ids_json=canonical_json(affected), checked_at_utc=self.kb.now(), outcome=outcome,
                   scope_note_json=canonical_json(scope_note), semantic_sha256=semantic_sha256(content))
        return self.kb.insert_unique("integrity_coverage", row)

    def provider_record_coverage(self, *, provider: str, subject_key: str, snapshot: dict, request_identity_sha256: str,
                                 found: bool, present_event_ids: list[str], scope_note: dict, failure_outcome: str | None = None):
        """Coverage for one retrieval of one provider record about ``subject_key`` (incl. omission detection)."""
        if not found:
            return self.add_coverage(subject_namespace="doi", subject_value=subject_key, provider=provider, snapshot=snapshot,
                                     outcome=failure_outcome or "CHECK_FAILED", scope_note=scope_note)
        reporters = {p for p, v in PROVIDER_OF.items() if v == provider}
        earlier = self.kb.all(
            "SELECT g.event_id, g.snapshot_id, s.semantic_sha256 snap_sem FROM integrity_event_sighting g "
            "JOIN integrity_event e ON e.event_id=g.event_id JOIN provider_snapshot s ON s.snapshot_id=g.snapshot_id "
            "WHERE e.subject_namespace='doi' AND e.subject_value=? AND s.provider=? AND s.request_identity_sha256=? AND g.snapshot_id<>?",
            (subject_key, provider, request_identity_sha256, snapshot["snapshot_id"]))
        earlier = [r for r in earlier if self.kb.one("SELECT reported_by FROM integrity_event WHERE event_id=?", (r["event_id"],))["reported_by"] in reporters]
        missing = sorted({r["event_id"] for r in earlier} - set(present_event_ids))
        if missing:
            prior_id = max((r["snapshot_id"] for r in earlier if r["event_id"] in missing), key=_order)
            prior = self.kb.one("SELECT * FROM provider_snapshot WHERE snapshot_id=?", (prior_id,))
            coverage, created = self.add_coverage(subject_namespace="doi", subject_value=subject_key, provider=provider, snapshot=snapshot,
                                                  outcome="REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT", scope_note=scope_note,
                                                  prior_snapshot=prior, affected_event_ids=missing)
            self.store.add_task(subject_namespace="doi", subject_value=subject_key, trigger="REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT",
                                event_ids=missing, coverage=coverage,
                                context={"prior_snapshot": prior["semantic_sha256"], "latest_snapshot": snapshot["semantic_sha256"]})
            return coverage, created
        outcome = "SIGNAL_REPORTED" if present_event_ids else "NO_SIGNAL_REPORTED"
        return self.add_coverage(subject_namespace="doi", subject_value=subject_key, provider=provider, snapshot=snapshot,
                                 outcome=outcome, scope_note=scope_note)

    # -- operator documents, counter-evidence, reviews --------------------------
    def register_document(self, operator: str, data: bytes, label: str) -> dict:
        who = operator_label(operator)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError("Operator documents must be UTF-8 text in S02", code="DOCUMENT_NOT_TEXT") from None
        if not text.strip():
            raise ValidationError("Empty document", code="DOCUMENT_EMPTY")
        digest = self.kb.cas.put(data)
        row = dict(document_sha256=digest, media_type="text/plain", byte_length=len(data), label=label, registered_by=who,
                   registered_at_utc=self.kb.now())
        return self.kb.insert_unique("operator_document", row, key_column="document_sha256")[0]

    def _resolve_anchor(self, anchor: dict) -> str:
        if not isinstance(anchor, dict):
            raise ValidationError("Anchor must be an object", code="ANCHOR_UNRESOLVABLE")
        if anchor.get("snapshot_id") is not None:
            snapshot = self.kb.one("SELECT * FROM provider_snapshot WHERE snapshot_id=?", (anchor["snapshot_id"],))
            if not snapshot or not snapshot["response_bytes_sha256"]:
                raise ValidationError("Anchor snapshot unknown or has no stored response", code="ANCHOR_UNRESOLVABLE")
            try:
                value = json_pointer(json.loads(self.kb.cas.read(snapshot["response_bytes_sha256"])), anchor.get("source_field_path", ""))
            except (KeyError, IndexError, ValueError, TypeError):
                raise ValidationError("Anchor field path does not resolve", code="ANCHOR_UNRESOLVABLE") from None
            if value in (None, "", [], {}):
                raise ValidationError("Anchor points at an empty value", code="ANCHOR_UNRESOLVABLE")
            return "snapshot"
        digest = anchor.get("document_sha256")
        if not is_sha256_hex(digest) or not self.kb.one("SELECT 1 FROM operator_document WHERE document_sha256=?", (digest,)):
            raise ValidationError("Anchor document is not registered", code="ANCHOR_UNRESOLVABLE")
        page, start, end = anchor.get("page"), anchor.get("char_start"), anchor.get("char_end")
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (page, start, end)):
            raise ValidationError("Document anchor needs integer page/char_start/char_end", code="ANCHOR_UNRESOLVABLE")
        pages = self.kb.cas.read(digest).decode("utf-8").split("\f")
        if not 1 <= page <= len(pages) or not 0 <= start < end <= len(pages[page - 1]) or not pages[page - 1][start:end].strip():
            raise ValidationError("Document anchor span does not resolve to text", code="ANCHOR_UNRESOLVABLE")
        return "document"

    def record_counter_evidence(self, *, operator: str, opposes_event_id: str, kind: str, primary_document_ref: str,
                                evidence_anchor: dict, statement_summary: str) -> dict:
        who = operator_label(operator)
        if kind not in COUNTER_KINDS:
            raise ValidationError("Unknown counter-evidence kind", code="COUNTER_EVIDENCE_KIND_INVALID")
        if not self.kb.one("SELECT 1 FROM integrity_event WHERE event_id=?", (opposes_event_id,)):
            raise ValidationError("Target integrity event does not exist", code="TARGET_EVENT_UNKNOWN")
        if not isinstance(primary_document_ref, str):
            raise ValidationError("Primary document reference required", code="PRIMARY_DOCUMENT_REQUIRED")
        if primary_document_ref.startswith("sha256:"):
            if not self.kb.one("SELECT 1 FROM operator_document WHERE document_sha256=?", (primary_document_ref[7:],)):
                raise ValidationError("Primary document not registered", code="PRIMARY_DOCUMENT_REQUIRED")
        elif primary_document_ref.startswith("doi:"):
            if parse_doi(primary_document_ref[4:], DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD).status != VALID:
                raise ValidationError("Primary document DOI invalid", code="PRIMARY_DOCUMENT_REQUIRED")
        else:
            raise ValidationError("Free-text references are not primary documents", code="PRIMARY_DOCUMENT_REQUIRED")
        if not isinstance(statement_summary, str) or not statement_summary.strip():
            raise ValidationError("Statement summary required", code="STATEMENT_REQUIRED")
        self._resolve_anchor(evidence_anchor)
        with_self = dict(counter_evidence_id=self.kb.ids.new("ice"), opposes_event_id=opposes_event_id, kind=kind,
                         primary_document_ref=primary_document_ref, evidence_anchor_json=canonical_json(evidence_anchor),
                         statement_summary=statement_summary, recorded_by=who, recorded_at_utc=self.kb.now())
        return self.kb.insert("integrity_counter_evidence", with_self)

    def record_review(self, *, operator: str, subject_namespace: str, subject_value: str, event_ids: list[str], trigger: str,
                      decision: str, reason: str, counter_evidence_ids: list[str] | None = None, task_id: str | None = None) -> dict:
        who = operator_label(operator)
        if decision not in REVIEW_DECISIONS or trigger not in REVIEW_TRIGGERS:
            raise ValidationError("Unknown review decision or trigger", code="REVIEW_INVALID")
        ce = list(counter_evidence_ids or [])
        if decision == "REPORT_NOT_APPLICABLE_WITH_EVIDENCE" and not ce:
            raise ValidationError("Closing as not applicable requires anchored counter-evidence", code="COUNTER_EVIDENCE_REQUIRED")
        for event_id in event_ids:
            event = self.kb.one("SELECT * FROM integrity_event WHERE event_id=?", (event_id,))
            if not event or (event["subject_namespace"], event["subject_value"]) != (subject_namespace, subject_value):
                raise ValidationError("Review events must belong to the subject", code="REVIEW_INVALID")
        for ce_id in ce:
            row = self.kb.one("SELECT opposes_event_id FROM integrity_counter_evidence WHERE counter_evidence_id=?", (ce_id,))
            if not row or row["opposes_event_id"] not in event_ids:
                raise ValidationError("Counter-evidence does not oppose a reviewed event", code="COUNTER_EVIDENCE_REQUIRED")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("Review reason required", code="REVIEW_INVALID")
        return self.kb.insert("integrity_review", dict(
            review_id=self.kb.ids.new("igr"), subject_namespace=subject_namespace, subject_value=subject_value, task_id=task_id,
            event_ids_json=canonical_json(sorted(event_ids)), trigger=trigger, decision=decision,
            counter_evidence_ids_json=canonical_json(sorted(ce)), reviewer_label=who, reason=reason, decided_at_utc=self.kb.now()))

    # -- derived view ------------------------------------------------------------
    def view(self, subject_namespace: str, subject_value: str) -> dict:
        events = self.kb.all("SELECT * FROM integrity_event WHERE subject_namespace=? AND subject_value=?", (subject_namespace, subject_value))
        coverage = self.kb.all("SELECT * FROM integrity_coverage WHERE subject_namespace=? AND subject_value=?", (subject_namespace, subject_value))
        counter = self.kb.all("SELECT ce.* FROM integrity_counter_evidence ce JOIN integrity_event e ON e.event_id=ce.opposes_event_id "
                              "WHERE e.subject_namespace=? AND e.subject_value=?", (subject_namespace, subject_value))
        reviews = self.kb.all("SELECT * FROM integrity_review WHERE subject_namespace=? AND subject_value=?", (subject_namespace, subject_value))
        tasks = self.kb.all("SELECT * FROM review_task WHERE subject_namespace=? AND subject_value=? AND trigger IN "
                            "('REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT','POSSIBLE_OPPOSING_NOTICE','OPERATOR_REQUEST','NEW_REPORT')",
                            (subject_namespace, subject_value))
        reviewed_tasks = {r["task_id"] for r in reviews if r["task_id"] and r["decision"] != "INCONCLUSIVE"}
        open_tasks = [t for t in tasks if t["task_id"] not in reviewed_tasks]

        def latest_by_provider(family: str) -> dict:
            latest: dict = {}
            for row in coverage:
                if family in json.loads(row["scope_note_json"]).get("event_types", []):
                    if row["provider"] not in latest or _order(row["coverage_id"]) > _order(latest[row["provider"]]["coverage_id"]):
                        latest[row["provider"]] = row
            return latest

        families = {}
        for family in EVENT_TYPES:
            fam_events = [e for e in events if e["event_type"] == family]
            latest = latest_by_provider(family)
            if not fam_events and not latest:
                continue
            reporters = {PROVIDER_OF[e["reported_by"]] for e in fam_events}
            fam_ids = {e["event_id"] for e in fam_events}
            fam_counter = [c for c in counter if c["opposes_event_id"] in fam_ids]
            # A provider that never reported this family and whose latest record was retrieved is silent for it,
            # whatever that record said about other families (SIGNAL / omission of a different family's event).
            silent = sorted(p for p, row in latest.items() if p not in reporters and row["outcome"] in
                            ("NO_SIGNAL_REPORTED", "SIGNAL_REPORTED", "REPORT_NO_LONGER_PRESENT_IN_LATEST_SNAPSHOT"))
            flags = []
            if any(latest.get(p, {}).get("outcome") in OMISSION_OUTCOMES for p in reporters):
                flags.append("REPORTING_SOURCE_LATEST_SNAPSHOT_OMITS_REPORT")
            if any(fam_ids & set(json.loads(t["event_ids_json"])) for t in open_tasks):
                flags.append("INVESTIGATION_PENDING")
            if fam_events and fam_counter:
                state = "EXPLICIT_CONTRADICTION"
            elif fam_events and silent:
                state = "REPORTED_NOT_CORROBORATED"
            elif fam_events:
                state = "REPORTED"
            elif silent:
                state = "NO_SIGNAL_REPORTED"
            else:
                state = "COVERAGE_UNAVAILABLE"
            cov = [{"provider": p, "outcome": r["outcome"], "checked_at_utc": r["checked_at_utc"],
                    "scope_note": json.loads(r["scope_note_json"])} for p, r in sorted(latest.items())]
            if state == "NO_SIGNAL_REPORTED":
                display = "No {} report found in {} as of the listed check dates".format(family.lower(), ", ".join(silent))
            elif state == "COVERAGE_UNAVAILABLE":
                display = "No usable {} coverage; status unknown".format(family.lower())
            else:
                display = "{} reported; see events, coverage and reviews".format(family.title())
            families[family] = {"state": state, "flags": flags, "event_ids": sorted(fam_ids),
                                "counter_evidence_ids": sorted(c["counter_evidence_id"] for c in fam_counter),
                                "coverage": cov, "display": display}
        precedence = ["EXPLICIT_CONTRADICTION", "REPORTED_NOT_CORROBORATED", "REPORTED", "NO_SIGNAL_REPORTED", "COVERAGE_UNAVAILABLE"]
        states = [f["state"] for f in families.values()]
        summary = next((s for s in precedence if s in states), "COVERAGE_UNAVAILABLE")
        return {"subject": [subject_namespace, subject_value], "schema": "integrity_view/2", "summary_state": summary, "families": families,
                "alert": self.alert(events, counter, reviews), "open_review_task_ids": sorted(t["task_id"] for t in open_tasks)}

    @staticmethod
    def alert(events: list[dict], counter: list[dict], reviews: list[dict]) -> str:
        if not events:
            return "NO_ALERT"
        closing = [r for r in reviews if r["decision"] in ("CONFIRMED_REPORT", "REPORT_NOT_APPLICABLE_WITH_EVIDENCE")]
        for event in events:
            covering = [r for r in closing if event["event_id"] in json.loads(r["event_ids_json"]) and _order(r["review_id"]) > _order(event["event_id"])]
            if not covering:
                return "OPEN_ALERT"
            last = max(covering, key=lambda r: _order(r["review_id"]))
            if any(c["opposes_event_id"] == event["event_id"] and _order(c["counter_evidence_id"]) > _order(last["review_id"]) for c in counter):
                return "OPEN_ALERT"
        last = max(closing, key=lambda r: _order(r["review_id"]))
        return "CLOSED_CONFIRMED" if last["decision"] == "CONFIRMED_REPORT" else "CLOSED_NOT_APPLICABLE"
