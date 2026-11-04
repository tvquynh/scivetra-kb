"""Append-only persistence for snapshots, claims, RA claims, change events, assertions and warnings.

Idempotency: every semantic row has a ``semantic_sha256`` computed from content
only (never from IDs or timestamps), so re-importing identical evidence creates
no new rows. Operational journals (provider_check, request_*) are separate and
excluded from the semantic manifest.
"""
from __future__ import annotations

import unicodedata

from .. import __version__
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import ValidationError
from .doi import DoiParse, VALID


def zs_rstrip(value: str) -> str:
    end = len(value)
    while end and unicodedata.category(value[end - 1]) == "Zs":
        end -= 1
    return value[:end]


class MetadataStore:
    def __init__(self, kb):
        self.kb = kb

    # -- snapshots ---------------------------------------------------------
    def snapshot_semantic(self, row: dict) -> str:
        return semantic_sha256({k: row[k] for k in ("provider", "request_identity_sha256", "http_status", "response_bytes_sha256",
                                                     "outcome", "outcome_reason", "parser_name", "parser_version")})

    def record_snapshot(self, *, provider: str, request_identity: dict, http_status, body: bytes | None, media_type,
                        outcome: str, reason: str | None, parser_name: str, parser_version: str, run_id: str | None) -> tuple[dict, bool]:
        text = canonical_json(request_identity)
        if "@" in text or "mailto" in text.lower():
            raise ValidationError("Request identity must not contain contact data", code="SECRET_IN_REQUEST_IDENTITY")
        body_sha = self.kb.cas.put(body) if body else None
        row = dict(provider=provider, request_identity_json=text, request_identity_sha256=sha256_bytes(text.encode("utf-8")),
                   http_status=http_status, response_bytes_sha256=body_sha, response_media_type=media_type, outcome=outcome,
                   outcome_reason=reason, parser_name=parser_name, parser_version=parser_version)
        row["semantic_sha256"] = self.snapshot_semantic(row)
        existing = self.kb.one("SELECT * FROM provider_snapshot WHERE semantic_sha256=?", (row["semantic_sha256"],))
        if existing:
            self._check(existing["snapshot_id"], run_id, "UNCHANGED")
            return existing, False
        previous = self.latest_snapshot(provider, row["request_identity_sha256"])
        row.update(snapshot_id=self.kb.ids.new("ps"), retrieved_at_utc=self.kb.now(), client_version=__version__,
                   supersedes_snapshot_id=previous["snapshot_id"] if previous else None)
        self.kb.insert("provider_snapshot", row)
        self._check(row["snapshot_id"], run_id, "NEW_SNAPSHOT")
        return row, True

    def _check(self, snapshot_id: str, run_id: str | None, result: str) -> None:
        self.kb.insert("provider_check", dict(check_id=self.kb.ids.new("chk"), snapshot_id=snapshot_id, run_id=run_id,
                                              checked_at_utc=self.kb.now(), result=result))

    def latest_snapshot(self, provider: str, request_identity_sha256: str, *, exclude: str | None = None):
        return self.kb.one("SELECT * FROM provider_snapshot WHERE provider=? AND request_identity_sha256=? AND snapshot_id<>? "
                           "ORDER BY rowid DESC LIMIT 1", (provider, request_identity_sha256, exclude or ""))

    def snapshots_for(self, provider: str, request_identity_sha256: str) -> list[dict]:
        return self.kb.all("SELECT * FROM provider_snapshot WHERE provider=? AND request_identity_sha256=? ORDER BY rowid",
                           (provider, request_identity_sha256))

    # -- claims ------------------------------------------------------------
    def add_claims(self, snapshot: dict, subject_namespace: str, subject_value: str, claims: list[dict]) -> int:
        created = 0
        for claim in claims:
            content = {"subject_namespace": subject_namespace, "subject_value": subject_value, "field": claim["field"],
                       "value": claim["value"], "source_field_path": claim["path"], "licence_class": claim["licence_class"]}
            sem = semantic_sha256(content)
            if self.kb.one("SELECT 1 FROM metadata_claim WHERE snapshot_id=? AND claim_semantic_sha256=?", (snapshot["snapshot_id"], sem)):
                continue
            self.kb.insert("metadata_claim", dict(claim_id=self.kb.ids.new("mc"), snapshot_id=snapshot["snapshot_id"],
                                                  subject_namespace=subject_namespace, subject_value=subject_value, field=claim["field"],
                                                  value_json=canonical_json(claim["value"]), source_field_path=claim["path"],
                                                  licence_class=claim["licence_class"], claim_semantic_sha256=sem))
            created += 1
        return created

    def claim_set(self, snapshot_id: str) -> set[tuple]:
        return {(r["field"], r["value_json"], r["source_field_path"]) for r in
                self.kb.all("SELECT field, value_json, source_field_path FROM metadata_claim WHERE snapshot_id=?", (snapshot_id,))}

    def record_change_event(self, provider: str, request_identity_sha256: str, prior: dict, new: dict) -> dict | None:
        """Field-level diff between the previous FOUND snapshot and a new FOUND snapshot. Old claims are kept."""
        before, after = self.claim_set(prior["snapshot_id"]), self.claim_set(new["snapshot_id"])
        removed = sorted(before - after)
        added = sorted(after - before)
        if not removed and not added:
            return None
        diff = {"added": [list(x) for x in added], "removed": [list(x) for x in removed],
                "fields_changed": sorted({x[0] for x in removed} | {x[0] for x in added})}
        row = dict(change_id=self.kb.ids.new("mce"), provider=provider, request_identity_sha256=request_identity_sha256,
                   prior_snapshot_id=prior["snapshot_id"], new_snapshot_id=new["snapshot_id"], diff_json=canonical_json(diff),
                   created_at_utc=self.kb.now(),
                   semantic_sha256=semantic_sha256({"prior": prior["semantic_sha256"], "new": new["semantic_sha256"], "diff": diff}))
        return self.kb.insert_unique("metadata_change_event", row)[0]

    # -- RA claims ---------------------------------------------------------
    def add_ra_claim(self, snapshot: dict, item: dict) -> dict:
        row = dict(ra_claim_id=self.kb.ids.new("ra"), snapshot_id=snapshot["snapshot_id"], doi_key=item["key"], ra=item["ra"],
                   ra_raw=item["ra_raw"], status_raw=item["status_raw"], created_at_utc=self.kb.now(),
                   semantic_sha256=semantic_sha256({"snapshot": snapshot["semantic_sha256"], "key": item["key"], "ra": item["ra"],
                                                    "ra_raw": item["ra_raw"], "status_raw": item["status_raw"]}))
        return self.kb.insert_unique("ra_claim", row)[0]

    def latest_ra_claim(self, key: str):
        return self.kb.one("SELECT r.*, s.retrieved_at_utc FROM ra_claim r JOIN provider_snapshot s ON s.snapshot_id=r.snapshot_id "
                           "WHERE r.doi_key=? ORDER BY r.rowid DESC LIMIT 1", (key,))

    # -- papers and assertions ---------------------------------------------
    def ensure_paper(self, *, basis: str, evidence_ref: str, semantic_basis: dict) -> dict:
        row = dict(paper_id=self.kb.ids.new("gp"), created_at_utc=self.kb.now(), creation_basis=basis, creation_evidence_ref=evidence_ref,
                   manifestation_kind="UNKNOWN", identity_state="PROVISIONAL", semantic_sha256=semantic_sha256(semantic_basis))
        return self.kb.insert_unique("global_paper", row)[0]

    def add_assertion(self, *, paper_id: str, namespace: str, value_original: str, parse: DoiParse | None, value_normalized: str | None,
                      scope_ref: str | None, asserted_by: str, evidence_ref: str, registration_agency: str, status: str,
                      paper_semantic: str, scope_semantic: str | None = None) -> tuple[dict, bool]:
        # S03: a scope_ref that is a minted ID (wsr_...) enters the semantic hash through the scope's own semantic hash.
        content = {"paper": paper_semantic, "namespace": namespace, "value_original": value_original, "value_normalized": value_normalized,
                   "scope_ref": scope_semantic if scope_semantic is not None else scope_ref, "asserted_by": asserted_by, "status": status}
        row = dict(assertion_id=self.kb.ids.new("ia"), paper_id=paper_id, namespace=namespace, value_original=value_original,
                   value_normalized=value_normalized, parse_result_json=canonical_json(parse.as_dict()) if parse else None,
                   scope_ref=scope_ref, asserted_by=asserted_by, evidence_ref=evidence_ref, registration_agency=registration_agency,
                   status=status, created_at_utc=self.kb.now(), semantic_sha256=semantic_sha256(content))
        result = self.kb.insert_unique("identity_assertion", row)
        if result[1] and namespace == "doi" and value_normalized:
            self.check_near_duplicates(value_normalized)
        return result

    def papers_for_doi_key(self, key: str) -> list[str]:
        """Exact comparison-key lookup only (trailing spaces significant; S01 03 section 6.3, I13)."""
        return [r["paper_id"] for r in self.kb.all(
            "SELECT DISTINCT paper_id FROM identity_assertion WHERE namespace='doi' AND value_normalized=? AND status='ACTIVE'", (key,))]

    def check_near_duplicates(self, key: str) -> list[dict]:
        base = zs_rstrip(key)
        warnings = []
        for row in self.kb.all("SELECT DISTINCT value_normalized v FROM identity_assertion WHERE namespace='doi' AND value_normalized IS NOT NULL"):
            other = row["v"]
            if other != key and zs_rstrip(other) == base:
                a, b = sorted([key, other])
                warning, created = self.kb.insert_unique("identifier_warning", dict(
                    warning_id=self.kb.ids.new("iw"), kind="NEAR_DUPLICATE_TRAILING_SPACE", key_a=a, key_b=b,
                    created_at_utc=self.kb.now(), semantic_sha256=semantic_sha256({"kind": "NEAR_DUPLICATE_TRAILING_SPACE", "a": a, "b": b})))
                if created:
                    self.add_task(subject_namespace="doi", subject_value=a, trigger="DOI_NEAR_DUPLICATE_TRAILING_SPACE",
                                  event_ids=[], coverage=None, context={"keys": [a, b]})
                warnings.append(warning)
            elif other != key and unicodedata.normalize("NFC", other) == unicodedata.normalize("NFC", key):
                a, b = sorted([key, other])
                self.kb.insert_unique("identifier_warning", dict(
                    warning_id=self.kb.ids.new("iw"), kind="UNICODE_NORMALIZATION_VARIANT", key_a=a, key_b=b,
                    created_at_utc=self.kb.now(), semantic_sha256=semantic_sha256({"kind": "UNICODE_NORMALIZATION_VARIANT", "a": a, "b": b})))
        return warnings

    # -- divergence --------------------------------------------------------
    def add_divergence(self, *, source_ref: str, rv_original: str, rv_normalized: str | None, kb_parse: DoiParse, kind: str) -> dict:
        content = {"source_ref": source_ref, "rv_original": rv_original, "rv_normalized": rv_normalized, "kb_status": kb_parse.status,
                   "kb_key": kb_parse.comparison_key, "kind": kind}
        row = dict(divergence_id=self.kb.ids.new("nd"), source_ref=source_ref, rv_value_original=rv_original, rv_value_normalized=rv_normalized,
                   kb_status=kb_parse.status, kb_comparison_key=kb_parse.comparison_key, divergence_kind=kind,
                   created_at_utc=self.kb.now(), semantic_sha256=semantic_sha256(content))
        return self.kb.insert_unique("normalization_divergence", row)[0]

    # -- review tasks --------------------------------------------------------
    def add_task(self, *, subject_namespace: str, subject_value: str, trigger: str, event_ids: list[str], coverage: dict | None,
                 context: dict) -> tuple[dict, bool]:
        event_sems = sorted(r["semantic_sha256"] for r in (
            self.kb.one("SELECT semantic_sha256 FROM integrity_event WHERE event_id=?", (e,)) for e in event_ids) if r)
        content = {"subject": [subject_namespace, subject_value], "trigger": trigger, "events": event_sems,
                   "coverage": coverage["semantic_sha256"] if coverage else None, "context": context}
        row = dict(task_id=self.kb.ids.new("irt"), subject_namespace=subject_namespace, subject_value=subject_value, trigger=trigger,
                   event_ids_json=canonical_json(sorted(event_ids)), coverage_id=coverage["coverage_id"] if coverage else None,
                   context_json=canonical_json(context), created_at_utc=self.kb.now(), semantic_sha256=semantic_sha256(content))
        return self.kb.insert_unique("review_task", row)


def doi_assertion_value(parse: DoiParse) -> str | None:
    return parse.comparison_key if parse.status == VALID else None
