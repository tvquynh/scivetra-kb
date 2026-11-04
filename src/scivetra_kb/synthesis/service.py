"""S11 SynthesisService: scientific synthesis DRAFTS and claim-to-citation audits (ADR_S11; S01 v03 08 section 6 synthesis rule).

Lifecycle: SynthesisScope (pinned members of ONE S08 evidence scope; rubric hash) -> ClaimUnits (typed spec + statement, versioned)
-> audit: SynthesisEvidenceSnapshot re-derived NOW (S08 current acceptance and verification, S05 / S06 currency, rights, CAS
re-read, anchor re-check) -> ClaimEvidenceLinks and verdicts, ComparisonGroups, ContradictionRecords, numbered references ->
HumanSynthesisReview (append-only; derived state; stale when the audit no longer reproduces) -> local review export (audited
before visible) | external share (always DENIED: A7 + OAG-7 not authorized).

Authority: every output is DRAFT_NON_AUTHORITATIVE / PUBLICATION_NOT_AUTHORIZED. S11 reads S05-S10 state only through their
services and tables; it inserts only into ``syn_*`` tables. S08 ``synthesis_eligible = false`` is not reinterpreted: S11 derives its
own, separate eligibility (``ELIGIBLE_NON_AUTHORITATIVE_DRAFT``) for a non-authoritative research draft. No model, network, quote or
document text is used or emitted; text dimensions are compared by SHA-256 of their normalized form.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
from pathlib import Path

from ..bridge.service import guard_path_text, path_hash
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import IntegrityError, KBError, PolicyRefusal, ValidationError
from ..core.paths import safe_data_root
from ..evidence.excerpt import MIN_WHOLE_WORDS
from ..evidence.excerpt import words as excerpt_words
from ..evidence.service import OK_VERIFICATION, EvidenceService, text_field_names
from ..library.common import operator_label
from ..parse.service import check_anchor
from ..rights import policy as rights_policy
from . import rules as R

REPORT_SCHEMA = "scivetra-synthesis/1"
AUTHORITY = {"authority": "NON_AUTHORITATIVE", "status": "DRAFT_NON_AUTHORITATIVE", "publication": "PUBLICATION_NOT_AUTHORIZED",
             "authoritative_corpus": "NOT_AVAILABLE_KB_STAGING_ONLY", "semantic_entailment": "NOT_ASSESSED_BY_MACHINE",
             "external_sharing": "DENIED_A7_OAG7_NOT_AUTHORIZED"}
DRAFT_DECISIONS = ("ATTEST_DRAFT_ACCURATE", "REQUEST_CHANGES", "REJECT_DRAFT")
CLAIM_DECISIONS = ("CONFIRM_SUPPORT", "DISPUTE_SUPPORT")
ACK_UNVERIFIED = "UNVERIFIED_CLAIMS_ACKNOWLEDGED"
REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,59}")
CLAIM_KEY = re.compile(r"[A-Za-z0-9._-]{1,40}")
DRAFT_ID = re.compile(r"evd_[0-9A-Za-z]{1,40}")


class _ReleaseRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class SynthesisService:
    def __init__(self, kb, *, faults=()):
        self.kb = kb
        self.ev = EvidenceService(kb)
        self.faults = set(faults)

    def _fault(self, name):
        if name in self.faults:
            raise OSError("injected fault: " + name)

    # -- scope ---------------------------------------------------------------------------------------------------------------
    def create_scope(self, *, ev_scope_id: str, operator: str, label: str, paper_ids: list | None = None, review_policy: str = "DUAL_INDEPENDENT",
                     review_policy_reason: str | None = None) -> dict:
        """Pin members of ONE S08 evidence scope. Each member must be current NOW (S05 FT_INCLUDE staging, S06 artifact and link,
        rights, CAS source) and non-provisional; a metadata-only list, a DOI inventory or another scope's papers are refused."""
        actor = operator_label(operator)
        kb = self.kb
        if not isinstance(label, str) or not 1 <= len(label) <= 200:
            raise ValidationError("label: 1..200 characters", code="LABEL_INVALID")
        if review_policy not in ("DUAL_INDEPENDENT", "SINGLE_HUMAN"):
            raise ValidationError("review_policy: DUAL_INDEPENDENT or SINGLE_HUMAN", code="REVIEW_POLICY_INVALID")
        if review_policy == "SINGLE_HUMAN" and not (isinstance(review_policy_reason, str) and review_policy_reason.strip()):
            raise ValidationError("SINGLE_HUMAN needs a protocol reason", code="REVIEW_POLICY_REASON_REQUIRED")
        ev = self.ev._scope(ev_scope_id)
        members = json.loads(ev["members_json"])
        if paper_ids is not None:
            if not isinstance(paper_ids, list) or not paper_ids or len(set(paper_ids)) != len(paper_ids):
                raise ValidationError("paper_ids: a non-empty list without duplicates", code="MEMBERS_INVALID")
            unknown = sorted(set(paper_ids) - {m["paper_id"] for m in members})
            if unknown:
                raise PolicyRefusal("papers are not members of the S08 evidence scope (no metadata-only or inventory members)",
                                    code="SYNTHESIS_MEMBER_NOT_IN_EVIDENCE_SCOPE")
            members = [m for m in members if m["paper_id"] in set(paper_ids)]
        if ev["eligibility_mode"] != "FINAL_FT_INCLUDE_ONLY":
            raise PolicyRefusal("a PROVISIONAL_NON_AUTHORITATIVE evidence scope cannot be synthesised", code="SYNTHESIS_SCOPE_PROVISIONAL")
        refused = {}
        for m in members:
            problems = self.ev.member_problems(ev, m) + self._source_problems(m["artifact_sha256"], m["asset_sha256"])
            if problems:
                refused[m["paper_id"]] = sorted(set(problems))
        if refused:
            raise PolicyRefusal("members are not currently usable: " + canonical_json(refused)[:600], code="SYNTHESIS_SCOPE_MEMBER_REFUSED")
        pinned = sorted(({k: m[k] for k in ("candidate_id", "paper_id", "document_version_id", "asset_sha256", "parse_artifact_id", "artifact_sha256",
                                            "link_id")} for m in members), key=lambda m: m["paper_id"])
        self.ev._gate_human_text(self._sources(pinned), [label, review_policy_reason], "label / reason")
        row = dict(scope_id=kb.ids.new("sys"), ev_scope_id=ev_scope_id, ev_scope_sha256=ev["semantic_sha256"], fte_run_id=ev["fte_run_id"],
                   release_import_id=ev["release_import_id"], protocol_sha256=ev["protocol_sha256"], profile_sha256=ev["profile_sha256"],
                   rubric_sha256=R.RUBRIC_SHA256, members_json=canonical_json(pinned), membership_sha256=semantic_sha256(pinned),
                   review_policy=review_policy, review_policy_reason=review_policy_reason if review_policy == "SINGLE_HUMAN" else None, label=label,
                   authority="NON_AUTHORITATIVE", output_status="DRAFT_NON_AUTHORITATIVE", publication="PUBLICATION_NOT_AUTHORIZED",
                   created_by=actor, created_at_utc=kb.now())
        row["semantic_sha256"] = semantic_sha256({k: row[k] for k in row if k not in ("scope_id", "created_at_utc")})
        with kb.transaction():
            kb.insert("syn_scope", row)
        return {"scope_id": row["scope_id"], "members": len(pinned), "membership_sha256": row["membership_sha256"], "rubric_sha256": R.RUBRIC_SHA256,
                **AUTHORITY}

    def _scope(self, scope_id) -> dict:
        row = self.kb.one("SELECT * FROM syn_scope WHERE scope_id=?", (str(scope_id)[:80],))
        if not row:
            raise ValidationError("unknown synthesis scope", code="UNKNOWN_REFERENCE")
        members = json.loads(row["members_json"])
        if semantic_sha256(members) != row["membership_sha256"]:
            raise IntegrityError("synthesis scope membership digest mismatch", code="SCOPE_MEMBERSHIP_TAMPERED")
        if row["rubric_sha256"] != R.RUBRIC_SHA256:
            raise PolicyRefusal("the synthesis rubric changed; create a new scope", code="RUBRIC_CHANGED")
        return dict(row, members=members)

    def _sources(self, members) -> list:
        out = []
        for m in members:
            art = self.kb.one("SELECT * FROM parse_artifact WHERE artifact_id=?", (m["parse_artifact_id"],))
            out.append((m["artifact_sha256"], self.ev._assessment(art, m["link_id"]) if art else None))
        return out

    def _source_problems(self, artifact_sha256, asset_sha256) -> list:
        out = []
        try:
            payload = json.loads(self.kb.cas.read(artifact_sha256))
            if not isinstance(payload, dict) or payload.get("asset_sha256") != asset_sha256:
                out.append("STALE:SOURCE_INTEGRITY:PARSE_ARTIFACT_NOT_FOR_ASSET")
        except KBError as exc:
            out.append("STALE:SOURCE_INTEGRITY:PARSE_ARTIFACT:" + exc.code)
        except ValueError:
            out.append("STALE:SOURCE_INTEGRITY:PARSE_ARTIFACT:UNREADABLE")
        try:
            self.kb.cas.verify_object(asset_sha256)
        except KBError as exc:
            out.append("STALE:SOURCE_INTEGRITY:PDF_ASSET:" + exc.code)
        return out

    def _profile(self, scope):
        ev = self.ev._scope(scope["ev_scope_id"])
        _, profile = self.ev._profile(ev["profile_id"])
        fields = profile["classes"].get("NUMERICAL_RESULT", {}).get("fields", {})
        return ev, profile, set(fields), fields.get("metric", {}).get("type") == "text"

    # -- claims --------------------------------------------------------------------------------------------------------------
    def add_claim(self, *, scope_id: str, claim_key: str, spec: dict, cites: list, operator: str, statement: str | None = None) -> dict:
        """A new claim, or a new version of ``claim_key``. The spec is validated strictly; the statement (author-supplied, else a
        deterministic template) may not say more than the spec, and author text that copies a source excerpt is refused where gate
        A4 does not allow it. Cited evidence is NOT judged here: the audit does that, NOW, every time."""
        actor = operator_label(operator)
        kb = self.kb
        scope = self._scope(scope_id)
        if not isinstance(claim_key, str) or not CLAIM_KEY.fullmatch(claim_key):
            raise ValidationError("claim_key: 1..40 of A-Z a-z 0-9 . _ -", code="CLAIM_KEY_INVALID")
        if not isinstance(cites, list) or len(cites) > 20 or len(set(cites)) != len(cites) or not all(isinstance(c, str) and DRAFT_ID.fullmatch(c) for c in cites):
            raise ValidationError("cites: up to 20 distinct S08 draft ids", code="CITES_INVALID")
        try:
            norm = R.validate_spec(spec, len(cites))
        except R.SpecInvalid as exc:
            raise ValidationError("claim spec invalid: " + ",".join(exc.codes)[:400], code="CLAIM_SPEC_INVALID") from None
        if statement is None:
            statement, origin = R.template_statement(norm), "DETERMINISTIC_TEMPLATE"
        else:
            origin = "AUTHOR_SUPPLIED"
            hard = [c for c in R.statement_checks(statement, norm) if c in R.HARD_STATEMENT_CODES]
            if hard:
                raise ValidationError("statement refused: " + ",".join(hard), code="STATEMENT_INPUT_REFUSED")
            self.ev._gate_human_text(self._sources(scope["members"]), [statement], "statement")
        # Consistency with the spec is judged at every audit (rubric /2); it is reported here only as an early warning. The author's
        # words are stored unchanged (append-only provenance) and are never repaired or normalized.
        codes = R.statement_checks(statement, norm, **self._statement_inputs(scope, norm, cites))
        prev = kb.one("SELECT * FROM syn_claim WHERE scope_id=? AND claim_key=? ORDER BY version DESC LIMIT 1", (scope_id, claim_key))
        row = dict(claim_id=kb.ids.new("syc"), scope_id=scope_id, claim_key=claim_key, version=(prev["version"] + 1) if prev else 1,
                   supersedes_claim_id=prev["claim_id"] if prev else None, claim_kind=norm["kind"], statement=statement, statement_origin=origin,
                   spec_json=canonical_json(norm), spec_sha256=semantic_sha256(norm), cited_draft_ids_json=canonical_json(cites), created_by=actor,
                   created_at_utc=kb.now())
        row["semantic_sha256"] = semantic_sha256({k: row[k] for k in row if k not in ("claim_id", "created_at_utc", "supersedes_claim_id")})
        if kb.one("SELECT 1 FROM syn_claim WHERE semantic_sha256=?", (row["semantic_sha256"],)):
            raise ValidationError("an identical claim version already exists", code="CLAIM_DUPLICATE")
        with kb.transaction():
            kb.insert("syn_claim", row)
        shown = self._project_statement(statement, self._disclosure_blockers(scope))
        status = ("INCONSISTENT_WITH_SPEC_AUDIT_WILL_BE_UNSUPPORTED" if codes else
                  "AUTHOR_SUPPLIED_AT_MOST_PARTIAL_UNTIL_HUMAN_EXACT_STATEMENT_CONFIRMATION" if origin == "AUTHOR_SUPPLIED" else "DETERMINISTIC_TEMPLATE")
        return {"claim_id": row["claim_id"], "claim_key": claim_key, "version": row["version"], "statement_origin": origin, **shown,
                "statement_check_codes": codes, "statement_status": status, "spec_sha256": row["spec_sha256"], "verdict": "NOT_YET_AUDITED"}

    def _statement_inputs(self, scope, spec, cites) -> dict:
        """For a COMPARISON statement: the (value, unit) of the two cited results and the context dimensions they share (text
        dimensions by hash), read from the immutable S08 drafts of the pinned evidence scope."""
        if spec["kind"] != "COMPARISON":
            return {}
        _, profile, _, _ = self._profile(scope)
        dims = []
        for c in cites:
            d = self.kb.one("SELECT * FROM ev_draft WHERE draft_id=? AND scope_id=?", (c, scope["ev_scope_id"]))
            if d:
                fields, text_dims = self._dims_of(d, profile)
                dims.append({**{k: v for k, v in fields.items() if k in R.DIMENSIONS}, **{k: v for k, v in text_dims.items() if k in R.DIMENSIONS}})
        compared = [(x.get("value"), x.get("unit")) for x in dims if x.get("value")]
        context = {k: v for k, v in dims[0].items() if k not in R.COMPARED_NOT_CONTEXT and all(x.get(k) == v for x in dims)} if dims else {}
        return {"compared": compared, "context": context}

    # -- disclosure (gate A5) ------------------------------------------------------------------------------------------------
    def _disclosure_blockers(self, scope) -> list:
        """Pinned sources whose stored excerpts may not be shown NOW (gate A5 not ALLOW, or no current rights assessment)."""
        out = []
        for sha, a in self._sources(scope["members"]):
            if rights_policy.gate("A5", a)["decision"] != "ALLOW":
                out.append(sha)
        return out

    def _withheld(self, text, blockers) -> bool:
        """True when ``text`` contains a verbatim excerpt (rkb-excerpt-detect/1) of a source that gate A5 does not allow to show, or
        when such a source cannot be read to make the check (fail closed)."""
        if not text or len(excerpt_words(text)) < MIN_WHOLE_WORDS:      # shorter text is never an excerpt (rkb-excerpt-detect/1)
            return False
        for sha in blockers:
            index = self.ev._source_index(sha)
            if index is None or index.contains_excerpt(text):
                return True
        return False

    def _project_statement(self, statement, blockers) -> dict:
        if self._withheld(statement, blockers):
            return {"statement": None, "statement_disclosure": "WITHHELD_A5", "statement_sha256": sha256_bytes(statement.encode("utf-8")),
                    "statement_chars": len(statement)}
        return {"statement": statement, "statement_disclosure": "SHOWN", "statement_sha256": sha256_bytes(statement.encode("utf-8")),
                "statement_chars": len(statement)}

    def _project_spec(self, spec, blockers):
        """The spec with any text value that carries a denied source excerpt replaced by WITHHELD (the stored spec is unchanged)."""
        def walk(v):
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, str) and self._withheld(v, blockers):
                return "WITHHELD"
            return v
        return walk(spec)

    def _current_claims(self, scope_id) -> list:
        rows = self.kb.all("SELECT * FROM syn_claim WHERE scope_id=? ORDER BY claim_key, version", (scope_id,))
        latest = {}
        for r in rows:
            latest[r["claim_key"]] = r
        return [latest[k] for k in sorted(latest)]

    # -- snapshot ------------------------------------------------------------------------------------------------------------
    def _entry(self, d, member, profile, ev) -> dict:
        st = self.ev.state(draft_id=d["draft_id"])
        reasons, category = [], "ELIGIBLE"
        pinned = {k: member[k] for k in ("paper_id", "document_version_id", "asset_sha256", "parse_artifact_id", "artifact_sha256")}
        if {k: d[k] for k in pinned} != pinned:
            reasons.append("STALE:PINNED_IDENTITY_CHANGED")
        source = self._source_problems(d["artifact_sha256"], d["asset_sha256"])
        anchors = json.loads(d["anchors_json"])
        if not source and anchors:
            try:
                payload = json.loads(self.kb.cas.read(d["artifact_sha256"]))
                for a in anchors:
                    if not check_anchor(payload, {"page": a["page"], "start": a["char_start"], "end": a["char_end"], "text_sha256": a["quote_sha256"]}):
                        source.append("STALE:ANCHOR_VERIFY_FAILED")
            except (KBError, ValueError, KeyError, TypeError):
                source.append("STALE:ANCHOR_VERIFY_FAILED")
        reasons += source + st["stale_reasons"]
        if st["current_state"] == "BLOCKED" or any(r.startswith("BLOCKED:") for r in reasons):
            category = "BLOCKED"
        elif st["current_state"] == "STALE" or any(r.startswith("STALE:") for r in reasons):
            category = "STALE"
        elif not st["counts_as_accepted_evidence"] or st["verification_state"] not in OK_VERIFICATION:
            category = "NOT_ACCEPTED"
            reasons.append("S08_STATE:" + st["current_state"])
        fields, text_dims = self._dims_of(d, profile)
        ver = self.kb.one("SELECT verification_id, state FROM ev_verification WHERE draft_id=? ORDER BY rowid DESC LIMIT 1", (d["draft_id"],))
        reviews = [r["review_id"] for r in self.ev._latest_reviews(d["draft_id"]).values()]
        adj = self.kb.one("SELECT adjudication_id FROM ev_adjudication WHERE draft_id=? ORDER BY rowid DESC LIMIT 1", (d["draft_id"],))
        return {"draft_id": d["draft_id"], "draft_semantic_sha256": d["draft_semantic_sha256"], "paper_id": d["paper_id"],
                "document_version_id": d["document_version_id"], "asset_sha256": d["asset_sha256"], "parse_artifact_id": d["parse_artifact_id"],
                "artifact_sha256": d["artifact_sha256"], "evidence_class": d["evidence_class"], "value_status": d["value_status"],
                "fields": fields, "text_dims": text_dims, "anchors": anchors,
                "quote": None, "verification": {"verification_id": ver["verification_id"] if ver else None, "state": st["verification_state"]},
                "s08_review_ids": sorted(reviews), "s08_adjudication_id": adj["adjudication_id"] if adj else None, "s08_current_state": st["current_state"],
                "s08_conflicts_with": sorted(st["conflicts_with"]), "s08_synthesis_eligible": st["synthesis_eligible"], "category": category,
                "eligibility": R.ELIGIBLE if category == "ELIGIBLE" else "INELIGIBLE_" + category, "eligibility_reasons": sorted(set(reasons))}

    @staticmethod
    def _dims_of(d, profile) -> tuple[dict, dict]:
        """(typed non-text fields, text dimensions as SHA-256 of their normalized form or WITHHELD) of an S08 draft."""
        fields = json.loads(d["typed_fields_json"])
        texts = text_field_names(profile, d["evidence_class"], fields) | set(json.loads(d["withheld_text_json"]))
        text_dims = {}
        for name in sorted(texts):
            text_dims[name] = R.text_hash(fields[name]) if (name in fields and d["text_storage"] == "STORED_GATE_A3") else "WITHHELD"
        return {k: v for k, v in sorted(fields.items()) if k not in texts}, text_dims

    def _snapshot(self, scope) -> tuple[list, dict]:
        ev, profile, pdims, metric_text = self._profile(scope)
        by_paper = {m["paper_id"]: m for m in scope["members"]}
        entries = []
        for d in self.kb.all("SELECT * FROM ev_draft WHERE scope_id=? ORDER BY paper_id, draft_semantic_sha256", (scope["ev_scope_id"],)):
            if d["paper_id"] in by_paper:
                entries.append(self._entry(d, by_paper[d["paper_id"]], profile, ev))
        dois = {}
        for p in sorted(by_paper):
            for r in self.kb.all("SELECT value_normalized FROM identity_assertion_effective WHERE paper_id=? AND namespace='doi' AND effective_status='ACTIVE' "
                                 "AND value_normalized IS NOT NULL", (p,)):
                dois.setdefault(r["value_normalized"], set()).add(p)
        collisions = {doi: ps for doi, ps in dois.items() if len(ps) > 1}
        ctx = {"profile_dims": pdims, "metric_is_text": metric_text, "members": sorted(by_paper), "doi_collisions": collisions,
               "identity_conflicts": sorted({p for ps in collisions.values() for p in ps}), "dois": dois}
        return entries, ctx

    # -- audit ---------------------------------------------------------------------------------------------------------------
    def _human_support(self, scope, claim_id, link_sha, before) -> str | None:
        """Human support judgement for a THEMATIC claim or an AUTHOR_SUPPLIED statement, bound to the exact link graph (which
        includes the statement SHA-256), from claim reviews recorded against audit runs EARLIER
        than ``before`` (an audit run rowid): a confirmation takes effect at the next audit, so an audit stays reproducible."""
        latest = {}
        for r in self.kb.all("SELECT w.* FROM syn_review w JOIN syn_audit_run a ON a.audit_id = w.audit_id WHERE w.target='CLAIM' AND w.claim_id=? "
                             "AND w.link_graph_sha256=? AND a.rowid < ? ORDER BY w.rowid", (claim_id, link_sha, before)):
            latest[r["reviewer_id"]] = r["decision"]
        if "DISPUTE_SUPPORT" in latest.values():
            return "DISPUTED"
        needed = 2 if scope["review_policy"] == "DUAL_INDEPENDENT" else 1
        return "CONFIRMED" if sum(v == "CONFIRM_SUPPORT" for v in latest.values()) >= needed else None

    def _build(self, scope, before=None) -> dict:
        """The deterministic report NOW. ``before``: rowid of the audit run being reproduced (human claim judgements recorded against
        it or later are not part of it); None for a new audit run."""
        if before is None:
            before = (self.kb.one("SELECT max(rowid) m FROM syn_audit_run")["m"] or 0) + 1
        entries, ctx = self._snapshot(scope)
        by_id = {e["draft_id"]: e for e in entries}
        blockers = self._disclosure_blockers(scope)
        claims, human = [], {}
        for c in self._current_claims(scope["scope_id"]):
            spec, cites = json.loads(c["spec_json"]), json.loads(c["cited_draft_ids_json"])
            claim = {"claim_id": c["claim_id"], "kind": c["claim_kind"], "spec": spec, "cites": cites, "statement_origin": c["statement_origin"],
                     "statement_sha256": sha256_bytes(c["statement"].encode("utf-8")),
                     "statement_codes": R.statement_checks(c["statement"], spec, **self._statement_inputs(scope, spec, cites))}
            first = R.evaluate_claim(claim, by_id, dict(ctx, human={}))
            needs_human = c["claim_kind"] == "THEMATIC" or c["statement_origin"] == "AUTHOR_SUPPLIED"
            h = self._human_support(scope, c["claim_id"], first["link_graph_sha256"], before) if needs_human else None
            res = R.evaluate_claim(claim, by_id, dict(ctx, human={c["claim_id"]: h})) if h else first
            claims.append((c, claim, res))
        refs, numbering = [], {}
        for c, claim, res in claims:
            if res["verdict"] != "SUPPORTED":
                continue
            for l in res["links"]:
                key = (l["paper_id"], l["document_version_id"], l["asset_sha256"])
                if key not in numbering:
                    numbering[key] = len(numbering) + 1
                    dv = self.kb.one("SELECT version_class FROM document_version WHERE document_version_id=?", (l["document_version_id"],))
                    doi = sorted(d for d, ps in ctx["dois"].items() if l["paper_id"] in ps)
                    refs.append({"ref": numbering[key], "paper_id": l["paper_id"], "document_version_id": l["document_version_id"],
                                 "version_class": dv["version_class"] if dv else None, "asset_sha256": l["asset_sha256"], "artifact_sha256": l["artifact_sha256"],
                                 "doi": doi[0] if len(doi) == 1 else None,
                                 "doi_status": "ACTIVE_IDENTITY_ASSERTION" if len(doi) == 1 else ("NO_ACTIVE_DOI_ASSERTION" if not doi else "MULTIPLE_DOIS_UNRESOLVED"),
                                 "title": None, "title_status": "NOT_INCLUDED_S11_CITES_IDENTIFIERS_ONLY",
                                 "note": "a reference entry is not validation; validation is the claim's link"})
        contr = R.contradictions(entries, ctx)
        out_claims = []
        for c, claim, res in claims:
            ref_numbers = sorted({numbering[(l["paper_id"], l["document_version_id"], l["asset_sha256"])] for l in res["links"]}) if res["verdict"] == "SUPPORTED" else []
            if res["verdict"] == "UNSUPPORTED" and any(r.startswith(("RELATION_CONTRADICTED", "CLAIMED_VALUE_CONTRADICTED")) for r in res["reasons"]):
                contr.append({"kind": "CLAIM_CONTRADICTED_BY_EVIDENCE", "key_sha256": semantic_sha256({"claim": c["claim_key"], "v": c["version"]}),
                              "draft_ids": sorted(claim["cites"]), "claim_ids": [c["claim_id"]], "detail": {"claim_key": c["claim_key"], "reasons": res["reasons"]}})
            # Disclosure is decided NOW (gate A5) for every projection; the stored claim row is never rewritten.
            out_claims.append({"claim_id": c["claim_id"], "claim_key": c["claim_key"], "version": c["version"], "kind": c["claim_kind"],
                               **self._project_statement(c["statement"], blockers), "statement_origin": c["statement_origin"],
                               "statement_check_codes": claim["statement_codes"], "spec": self._project_spec(claim["spec"], blockers),
                               "spec_sha256": c["spec_sha256"], "factual": True, "verdict": res["verdict"], "marker": res["marker"], "references": ref_numbers,
                               "reasons": res["reasons"], "human_judgement": res["human_judgement"], "count_ascertainment": res["count_ascertainment"],
                               "links": res["links"], "comparability": res["comparability"], "derivation": res["derivation"],
                               "link_graph_sha256": res["link_graph_sha256"]})
        counts = {v: sum(1 for c in out_claims if c["verdict"] == v) for v in R.VERDICTS}
        flagged = sum(1 for c in out_claims if c["marker"] != "CITED")
        traced = sum(1 for c in out_claims if c["marker"] == "CITED" and c["references"] and all(l["eligibility"] == R.ELIGIBLE for l in c["links"]))
        excluded = {}
        for e in entries:
            if e["category"] != "ELIGIBLE":
                excluded[e["category"]] = excluded.get(e["category"], 0) + 1
        snapshot = {"entries": entries, "excluded": dict(sorted(excluded.items()))}
        report = {"schema": REPORT_SCHEMA, "engine": R.ENGINE, "rubric_sha256": R.RUBRIC_SHA256, **AUTHORITY,
                  "scope": {k: scope[k] for k in ("scope_id", "semantic_sha256", "ev_scope_id", "ev_scope_sha256", "membership_sha256", "protocol_sha256",
                                                 "profile_sha256", "review_policy")} | {"members": len(scope["members"])},
                  "prisma": {"identified": "NOT_AVAILABLE", "screened": "NOT_AVAILABLE", "included_authoritative": "NOT_AVAILABLE",
                             "missing_source": "NO_AUTHORITATIVE_SEALED_CORPUS_AND_NO_SELECTION_COUNTS_IN_S11", "kb_staging_pinned_members": len(scope["members"])},
                  "snapshot_sha256": semantic_sha256(snapshot), "evidence": entries, "evidence_excluded": snapshot["excluded"],
                  "comparison_groups": R.comparison_groups(entries, ctx),
                  "contradictions": sorted(contr, key=lambda x: (x["kind"], x["key_sha256"])), "claims": out_claims, "references": refs,
                  "citation_audit": {"factual_claims": len(out_claims), "supported_and_cited": traced, "visibly_flagged": flagged,
                                     "traceable_or_flagged": traced + flagged, "traceability": "100%" if traced + flagged == len(out_claims) else "INCOMPLETE",
                                     "verdict_counts": counts, "invented_references": 0, "pooled_estimates": 0}}
        if report["citation_audit"]["traceability"] != "100%":
            raise IntegrityError("traceability invariant violated", code="TRACEABILITY_INCOMPLETE")
        report["report_sha256"] = R.report_sha256(report)
        return report

    def audit(self, *, scope_id: str, operator: str) -> dict:
        """Re-derive the snapshot NOW and audit every current claim version; persist the run (append-only)."""
        actor = operator_label(operator)
        kb = self.kb
        scope = self._scope(scope_id)
        report = self._build(scope)
        snap = dict(snapshot_id=kb.ids.new("syq"), scope_id=scope_id, entries_json=canonical_json(report["evidence"]), snapshot_sha256=report["snapshot_sha256"],
                    eligible_count=sum(1 for e in report["evidence"] if e["category"] == "ELIGIBLE"), excluded_json=canonical_json(report["evidence_excluded"]),
                    contains_quotes=0, created_by=actor, created_at_utc=kb.now())
        run = dict(audit_id=kb.ids.new("sya"), scope_id=scope_id, snapshot_id=snap["snapshot_id"], claim_ids_json=canonical_json([c["claim_id"] for c in report["claims"]]),
                   claim_set_sha256=semantic_sha256([c["claim_id"] for c in report["claims"]]), rubric_sha256=R.RUBRIC_SHA256, engine=R.ENGINE,
                   report_sha256=report["report_sha256"], counts_json=canonical_json(report["citation_audit"]["verdict_counts"]), traceability="COMPLETE",
                   created_by=actor, created_at_utc=kb.now())
        with kb.transaction():
            kb.insert("syn_snapshot", snap)
            kb.insert("syn_audit_run", run)
            for c in report["claims"]:
                kb.insert("syn_claim_audit", dict(row_id=kb.ids.new("syl"), audit_id=run["audit_id"], claim_id=c["claim_id"], verdict=c["verdict"],
                                                  marker=c["marker"], reasons_json=canonical_json(c["reasons"]), links_json=canonical_json(c["links"]),
                                                  comparability_json=canonical_json(c["comparability"]), derivation_json=canonical_json(c["derivation"]),
                                                  link_graph_sha256=c["link_graph_sha256"]))
            for x in report["contradictions"]:
                kb.insert("syn_contradiction", dict(contradiction_id=kb.ids.new("syx"), audit_id=run["audit_id"], kind=x["kind"], key_sha256=x["key_sha256"],
                                                    draft_ids_json=canonical_json(x["draft_ids"]), claim_ids_json=canonical_json(x["claim_ids"]),
                                                    detail_json=canonical_json(x["detail"])))
        return {"audit_id": run["audit_id"], "snapshot_id": snap["snapshot_id"], "report_sha256": report["report_sha256"],
                "verdict_counts": report["citation_audit"]["verdict_counts"], "traceability": report["citation_audit"]["traceability"],
                "contradictions": len(report["contradictions"]), "report": report}

    def _latest_audit(self, scope_id):
        return self.kb.one("SELECT rowid AS run_rowid, * FROM syn_audit_run WHERE scope_id=? ORDER BY rowid DESC LIMIT 1", (scope_id,))

    # -- human review --------------------------------------------------------------------------------------------------------
    def review(self, *, scope_id: str, audit_id: str, reviewer: str, decision: str, claim_key: str | None = None, reason_codes=(),
               rationale: str | None = None) -> dict:
        """Append a human review of the CURRENT audit (it must still reproduce NOW). DRAFT decisions: ATTEST_DRAFT_ACCURATE (needs
        UNVERIFIED_CLAIMS_ACKNOWLEDGED when any claim is not SUPPORTED), REQUEST_CHANGES, REJECT_DRAFT. CLAIM decisions (claim_key):
        CONFIRM_SUPPORT / DISPUTE_SUPPORT for its exact link graph. Only owner-registered HUMAN reviewers; no self-review."""
        kb = self.kb
        scope = self._scope(scope_id)
        rev = self.ev._reviewer(reviewer)
        run = kb.one("SELECT * FROM syn_audit_run WHERE audit_id=? AND scope_id=?", (str(audit_id)[:80], scope_id))
        latest = self._latest_audit(scope_id)
        if not run or not latest or run["audit_id"] != latest["audit_id"]:
            raise PolicyRefusal("only the latest audit run can be reviewed", code="REVIEW_TARGET_NOT_LATEST_AUDIT")
        fresh = self._build(scope, latest["run_rowid"])
        if fresh["report_sha256"] != run["report_sha256"]:
            raise PolicyRefusal("evidence, rights or claims changed since this audit; run the audit again", code="REVIEW_TARGET_NOT_CURRENT")
        codes = list(reason_codes or ())
        if not all(isinstance(c, str) and REASON_CODE.fullmatch(c) for c in codes) or len(codes) > 20:
            raise ValidationError("reason codes: UPPER_SNAKE_CASE", code="REASON_CODES_INVALID")
        if rationale is not None and (not isinstance(rationale, str) or not 1 <= len(rationale) <= 2000):
            raise ValidationError("rationale: 1..2000 characters", code="RATIONALE_INVALID")
        authors = {c["created_by"] for c in kb.all("SELECT created_by FROM syn_claim WHERE scope_id=?", (scope_id,))} | {scope["created_by"], run["created_by"]}
        target, claim_id, link_sha = "DRAFT", None, None
        if decision in CLAIM_DECISIONS:
            claim = next((c for c in fresh["claims"] if c["claim_key"] == claim_key), None)
            if claim is None:
                raise ValidationError("unknown claim_key in this audit", code="UNKNOWN_REFERENCE")
            target, claim_id, link_sha = "CLAIM", claim["claim_id"], claim["link_graph_sha256"]
            authors = {kb.one("SELECT created_by FROM syn_claim WHERE claim_id=?", (claim_id,))["created_by"], run["created_by"]}
            if claim["kind"] != "THEMATIC" and claim["statement_origin"] != "AUTHOR_SUPPLIED":
                raise PolicyRefusal("support judgements apply to THEMATIC claims and author-supplied statements; template claims are checked "
                                    "against their evidence", code="CLAIM_REVIEW_NOT_APPLICABLE")
            if any(l["eligibility"] != R.ELIGIBLE for l in claim["links"]) or not claim["links"]:
                raise PolicyRefusal("cannot confirm support from evidence that is not currently eligible", code="CLAIM_EVIDENCE_NOT_ELIGIBLE")
            if decision == "CONFIRM_SUPPORT" and claim["statement_disclosure"] != "SHOWN":
                raise PolicyRefusal("the exact statement is withheld (gate A5); it cannot be confirmed", code="CLAIM_STATEMENT_WITHHELD")
            if decision == "CONFIRM_SUPPORT" and claim["human_judgement"] != "REQUIRED":
                raise PolicyRefusal("only a claim whose machine checks pass and that awaits a human judgement can be confirmed "
                                    "(a statement inconsistent with its spec, or failing evidence, cannot be confirmed)", code="CLAIM_REVIEW_NOT_APPLICABLE")
        elif decision not in DRAFT_DECISIONS:
            raise ValidationError("decision: " + "/".join(DRAFT_DECISIONS + CLAIM_DECISIONS), code="DECISION_INVALID")
        elif claim_key is not None:
            raise ValidationError("a draft decision does not name a claim", code="DECISION_INVALID")
        if "OPERATOR:" + rev["label"] in authors:
            raise PolicyRefusal("the author of the claims, scope or audit cannot review them", code="SELF_REVIEW_REFUSED")
        if decision in ("REQUEST_CHANGES", "REJECT_DRAFT", "DISPUTE_SUPPORT") and not codes:
            raise ValidationError("this decision needs reason codes", code="REASON_CODES_REQUIRED")
        if decision == "ATTEST_DRAFT_ACCURATE" and any(c["verdict"] != "SUPPORTED" for c in fresh["claims"]) and ACK_UNVERIFIED not in codes:
            raise PolicyRefusal("some claims are not SUPPORTED; attest only with UNVERIFIED_CLAIMS_ACKNOWLEDGED", code="UNVERIFIED_CLAIMS_NOT_ACKNOWLEDGED")
        self.ev._gate_human_text(self._sources(scope["members"]), [rationale] + codes, "rationale / reason codes")
        row = dict(review_id=kb.ids.new("syr"), scope_id=scope_id, audit_id=run["audit_id"], report_sha256=run["report_sha256"], target=target,
                   claim_id=claim_id, link_graph_sha256=link_sha, reviewer_id=rev["reviewer_id"], decision=decision, reason_codes_json=canonical_json(codes),
                   rationale=rationale, authority="NON_AUTHORITATIVE", created_at_utc=kb.now())
        with kb.transaction():
            kb.insert("syn_review", row)
        return {"review_id": row["review_id"], "target": target, "decision": decision, "state": self.status(scope_id=scope_id)["review_state"]}

    def status(self, *, scope_id: str) -> dict:
        """Derived, current state: does the latest audit still reproduce NOW, and what do the latest human draft reviews say."""
        scope = self._scope(scope_id)
        run = self._latest_audit(scope_id)
        if run is None:
            return {"scope_id": scope_id, "audit_id": None, "audit_current": False, "review_state": "NOT_AUDITED", **AUTHORITY}
        fresh = self._build(scope, run["run_rowid"])
        current = fresh["report_sha256"] == run["report_sha256"]
        latest = {}
        for r in self.kb.all("SELECT * FROM syn_review WHERE scope_id=? AND target='DRAFT' ORDER BY rowid", (scope_id,)):
            latest[r["reviewer_id"]] = r
        on_run = [r for r in latest.values() if r["audit_id"] == run["audit_id"]]
        needed = 2 if scope["review_policy"] == "DUAL_INDEPENDENT" else 1
        decisions = sorted(r["decision"] for r in on_run)
        if not current:
            state = "REVIEW_STALE_AUDIT_NO_LONGER_REPRODUCES" if latest else "AUDIT_OUTDATED_RUN_AGAIN"
        elif "REJECT_DRAFT" in decisions:
            state = "REJECTED"
        elif "REQUEST_CHANGES" in decisions:
            state = "REVIEW_CONFLICT" if "ATTEST_DRAFT_ACCURATE" in decisions else "CHANGES_REQUESTED"
        elif decisions.count("ATTEST_DRAFT_ACCURATE") >= needed:
            state = "HUMAN_REVIEWED_NON_AUTHORITATIVE_DRAFT"
        elif decisions:
            state = "PARTIALLY_REVIEWED"
        elif latest:
            state = "REVIEW_STALE_EARLIER_AUDIT"
        else:
            state = "NOT_REVIEWED"
        return {"scope_id": scope_id, "audit_id": run["audit_id"], "audit_current": current, "report_sha256": run["report_sha256"],
                "fresh_report_sha256": fresh["report_sha256"], "review_state": state, "review_policy": scope["review_policy"], "draft_review_decisions": decisions,
                "verdict_counts": fresh["citation_audit"]["verdict_counts"], "traceability": fresh["citation_audit"]["traceability"], **AUTHORITY}

    def reviews(self, *, scope_id: str) -> dict:
        """Review history; rationales shown only while gate A5 allows every pinned source, otherwise only when they contain no
        source excerpt (else WITHHELD with SHA-256 and length; the stored row is unchanged)."""
        scope = self._scope(scope_id)
        rows = self.kb.all("SELECT w.*, v.label FROM syn_review w JOIN ai_reviewer v USING (reviewer_id) WHERE w.scope_id=? ORDER BY w.rowid", (scope_id,))
        blockers = self._disclosure_blockers(scope)
        out = []
        for r in rows:
            r = dict(r)
            text = r["rationale"]
            hidden = bool(text) and any((self.ev._source_index(s) is None) or self.ev._source_index(s).contains_excerpt(text) for s in blockers)
            if hidden:
                r.update(rationale=None, rationale_disclosure="WITHHELD", rationale_sha256=sha256_bytes(text.encode("utf-8")), rationale_chars=len(text))
            else:
                r["rationale_disclosure"] = "SHOWN" if text else "NONE"
            codes = json.loads(r["reason_codes_json"])
            if self._withheld(" ".join(codes), blockers):        # a reason code can spell an excerpt (S08-F03); A5 applies to it too
                r.update(reason_codes_json=None, reason_codes_disclosure="WITHHELD", reason_codes_sha256=sha256_bytes(r["reason_codes_json"].encode("utf-8"))
                         if r["reason_codes_json"] else None)
            else:
                r["reason_codes_disclosure"] = "SHOWN"
            out.append(r)
        return {"scope_id": scope_id, "reviews": out}

    # -- export / share ------------------------------------------------------------------------------------------------------
    def _bundle(self, report) -> dict:
        md = ["# Synthesis draft (review only)", "", "**Status: DRAFT_NON_AUTHORITATIVE. PUBLICATION_NOT_AUTHORIZED. LOCAL_REVIEW_ONLY - not for distribution.**",
              "", "Every statement below is either cited to current, human-accepted, anchor-verified evidence ([n]) or visibly marked "
              "[UNVERIFIED] / [CONFLICTED]. Machine checks are structural, not semantic entailment. No pooled estimate, significance "
              "test or causal inference is made. Source text and quotes are not included.", "",
              f"Report SHA-256: `{report['report_sha256']}` - rubric `{report['rubric_sha256']}` - members: {report['scope']['members']} (KB staging; "
              "no authoritative corpus; PRISMA counts NOT_AVAILABLE).", "", "## Claims", ""]
        for c in report["claims"]:
            tag = "".join(f"[{n}]" for n in c["references"]) if c["marker"] == "CITED" else c["marker"]
            md.append(f"- **{c['claim_key']}** (v{c['version']}, {c['kind']}): {c['statement']} {tag} - `{c['verdict']}`"
                      + (f" ({'; '.join(c['reasons'][:4])})" if c["reasons"] else ""))
        md += ["", "## Contradictions (both sides kept; nothing resolved by vote)", ""]
        md += [f"- {x['kind']}: drafts {', '.join(x['draft_ids']) or '-'}" for x in report["contradictions"]] or ["- none detected"]
        md += ["", "## Comparison groups (values side by side; never pooled or ranked)", ""]
        for g in report["comparison_groups"]:
            vals = ", ".join(f"{m['value']}{'%' if m['unit'] == '%' else ''} ({m['paper_id']})" for m in g["members"])
            md.append(f"- group `{g['group_key_sha256'][:12]}` metric {g['context'].get('metric')}: {vals}" + (f" - {'; '.join(g['notes'])}" if g["notes"] else ""))
        md += ["", "## References (identifiers; a reference is not validation)", ""]
        for r in report["references"]:
            md.append(f"[{r['ref']}] paper {r['paper_id']}, document version {r['document_version_id']} ({r['version_class']}), "
                      f"DOI {r['doi'] or r['doi_status']}, PDF SHA-256 {r['asset_sha256'][:16]}..., S06 artifact {r['artifact_sha256'][:16]}...")
        buf = io.StringIO(newline="")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["claim_key", "version", "kind", "verdict", "marker", "references", "draft_semantic_sha256", "paper_id", "document_version_id",
                    "page", "char_start", "char_end", "excerpt_sha256", "evidence_class", "value_status", "eligibility", "link_result", "reasons"])
        for c in report["claims"]:
            for l in c["links"] or [{}]:
                anchors = l.get("anchors") or [{}]
                for a in anchors:
                    w.writerow([c["claim_key"], c["version"], c["kind"], c["verdict"], c["marker"], " ".join(str(n) for n in c["references"]),
                                l.get("draft_semantic_sha256", ""), l.get("paper_id", ""), l.get("document_version_id", ""), a.get("page", ""),
                                a.get("char_start", ""), a.get("char_end", ""), a.get("quote_sha256", ""), l.get("evidence_class", ""),
                                l.get("value_status", ""), l.get("eligibility", ""), l.get("result", ""), ";".join(c["reasons"])])
        files = {"synthesis.json": (canonical_json(report) + "\n").encode("utf-8"), "evidence_matrix.csv": buf.getvalue().encode("utf-8"),
                 "draft.md": ("\n".join(md) + "\n").encode("utf-8")}
        manifest = {"schema": "scivetra-synthesis-export/1", "destination": "LOCAL_REVIEW_ONLY", **AUTHORITY,
                    "files": {k: sha256_bytes(v) for k, v in sorted(files.items())}, "report_sha256": report["report_sha256"]}
        files["MANIFEST.json"] = (canonical_json(manifest) + "\n").encode("utf-8")
        return files

    def _leak_scan(self, scope, files):
        texts = []
        for r in self.kb.all("SELECT quote_text_json FROM ev_draft WHERE scope_id=? AND quote_text_json IS NOT NULL", (scope["ev_scope_id"],)):
            texts.extend(t for t in json.loads(r["quote_text_json"]) if len(t) >= 20)
        for p in self.kb.all("SELECT pack_sha256 FROM ev_input_pack WHERE scope_id=?", (scope["ev_scope_id"],)):
            try:
                texts.extend(s["text"] for s in json.loads(self.kb.cas.read(p["pack_sha256"]))["segments"] if len(s["text"]) >= 40)
            except (KBError, ValueError, KeyError):
                continue
        blockers = self._disclosure_blockers(scope)
        for name, data in files.items():
            if any(t.encode("utf-8") in data for t in texts):
                raise IntegrityError("export would contain document text", code="EXPORT_LEAK_REFUSED")
            # Shorter verbatim copies of a source that gate A5 does not allow to show (S11-F01), checked line by line with
            # rkb-excerpt-detect/1; an unreadable denied source fails closed (the check cannot be made).
            for line in data.decode("utf-8").splitlines():
                if self._withheld(line, blockers):
                    raise IntegrityError("export would disclose a source excerpt that gate A5 does not allow", code="EXPORT_LEAK_REFUSED")
            if re.search(rb"(?i)[a-z]:[\\/]+|/home/|/root/|/users/", data):
                raise IntegrityError("export would contain a local path", code="EXPORT_LEAK_REFUSED")

    def export(self, *, scope_id: str, out_dir: str, operator: str) -> dict:
        """LOCAL_REVIEW_ONLY bundle (synthesis.json, evidence_matrix.csv, draft.md, MANIFEST.json) of the latest audit, only if it
        still reproduces NOW (rights, sources and acceptance re-checked). RESERVED event committed first; files staged; then the
        release (S11-F05, ADR SY-D20): final re-check, atomic rename and PUBLISHED event in one SQLite write transaction, so no KB
        writer can change rights or evidence between the check and the publication; ABANDONED terminal event otherwise; a refused
        or failed release leaves no visible file."""
        actor = operator_label(operator)
        kb = self.kb
        guard_path_text(out_dir, "--out-dir")
        if not isinstance(out_dir, str) or not os.path.isabs(out_dir) or ".." in Path(out_dir).parts:
            raise ValidationError("--out-dir must be an absolute path without '..'", code="OUT_DIR_INVALID")
        target = safe_data_root(out_dir, must_exist=False)
        if target == kb.root or target.is_relative_to(kb.root) or kb.root.is_relative_to(target):
            raise ValidationError("--out-dir must be outside the KB data root", code="OUT_DIR_INVALID")
        scope = self._scope(scope_id)
        run = self._latest_audit(scope_id)
        if run is None:
            raise PolicyRefusal("audit the scope before exporting", code="EXPORT_NOT_AUDITED")
        report = self._build(scope, run["run_rowid"])
        if report["report_sha256"] != run["report_sha256"]:
            raise PolicyRefusal("evidence, rights or claims changed since the latest audit; run the audit again", code="EXPORT_AUDIT_NOT_CURRENT")
        files = self._bundle(report)
        self._leak_scan(scope, files)
        bundle = semantic_sha256({k: sha256_bytes(v) for k, v in sorted(files.items())})
        folder = target / f"synthesis_{scope_id[-8:].lower()}_{bundle[:8]}"
        files_json = canonical_json({k: sha256_bytes(v) for k, v in sorted(files.items())})
        if folder.exists():
            raise ValidationError("export folder exists", code="EXPORT_FOLDER_EXISTS")
        reservation = dict(export_id=kb.ids.new("sye"), scope_id=scope_id, audit_id=run["audit_id"], phase="RESERVED", reservation_id=None, outcome_code=None,
                           destination="LOCAL_REVIEW_ONLY", bundle_sha256=bundle, files_json=files_json, contains_quotes=0,
                           target_path_hash=path_hash(target), folder_name=folder.name, created_by=actor, created_at_utc=kb.now())
        try:
            self._fault("reserve")
            with kb.transaction():
                kb.insert("syn_export_event", reservation)
        except Exception as exc:
            raise IntegrityError("export audit reservation could not be recorded; nothing was written", code="EXPORT_AUDIT_FAILED") from exc
        staging = target / f".{folder.name}.partial-{os.urandom(6).hex()}"
        try:
            target.mkdir(parents=True, exist_ok=True)
            staging.mkdir()
            for name, data in sorted(files.items()):
                with open(staging / name, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            self._fault("write")
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            self._close(reservation, "ABANDONED", "PUBLICATION_FAILED", quiet=True)
            raise IntegrityError("export could not be written; nothing is visible", code="EXPORT_WRITE_FAILED") from exc
        # Release (S11-F05): the final rights / source / currency check, the atomic rename and the PUBLISHED event happen inside ONE
        # SQLite write transaction (BEGIN IMMEDIATE). Every KB writer (rights assessments, S08 reviews, S05 / S06 changes, other
        # audits) is serialized against it: a change committed before the lock is seen by the check; a change after it waits
        # (busy_timeout) until the bundle is published and is then simply later. Anything not current refuses and withdraws.
        published = False
        done = dict({k: reservation[k] for k in ("scope_id", "audit_id", "destination", "bundle_sha256", "files_json", "contains_quotes",
                                                  "target_path_hash", "folder_name", "created_by")},
                    export_id=kb.ids.new("sye"), phase="PUBLISHED", reservation_id=reservation["export_id"], outcome_code=None)
        try:
            with kb.transaction():
                self._fault("release")
                refusal = self._release_check(scope, run, files, staging)
                if refusal:
                    raise _ReleaseRefused(refusal)
                os.replace(staging, folder)
                published = True
                self._fault("complete")
                done["created_at_utc"] = kb.now()
                kb.insert("syn_export_event", done)
        except _ReleaseRefused as exc:
            shutil.rmtree(staging, ignore_errors=True)
            self._close(reservation, "ABANDONED", ("RELEASE_REFUSED:" + exc.code)[:80], quiet=True)
            raise PolicyRefusal("rights, sources or evidence changed before release; nothing was published (" + exc.code + ")",
                                code="EXPORT_RELEASE_REFUSED") from None
        except Exception as exc:
            if not published:
                shutil.rmtree(staging, ignore_errors=True)
                self._close(reservation, "ABANDONED", "RELEASE_FAILED", quiet=True)
                raise IntegrityError("export release could not be completed (lock or I/O); nothing was published", code="EXPORT_RELEASE_FAILED") from exc
            withdrawn = target / f".{folder.name}.withdrawn-{os.urandom(6).hex()}"
            try:
                os.replace(folder, withdrawn)
                shutil.rmtree(withdrawn, ignore_errors=True)
            except OSError:
                raise IntegrityError("export completion could not be recorded and the files could not be withdrawn", code="EXPORT_AUDIT_FAILED") from exc
            self._close(reservation, "ABANDONED", "AUDIT_COMPLETION_FAILED_WITHDRAWN", quiet=True)
            raise IntegrityError("export completion could not be recorded; the files were withdrawn", code="EXPORT_AUDIT_FAILED") from exc
        return {"export_id": done["export_id"], "folder_name": folder.name, "bundle_sha256": bundle, "files": json.loads(files_json),
                "report_sha256": report["report_sha256"], "destination": "LOCAL_REVIEW_ONLY", "path_hash": reservation["target_path_hash"], **AUTHORITY}

    def _release_check(self, scope, run, files, staging) -> str | None:
        """Refusal code, or None when the staged bundle may be released NOW: this audit is still the latest; the report re-derived
        NOW (current S08 acceptance, A2 / A3 / A5 rights, S05 / S06 currency, CAS re-read of every artifact and PDF, anchors) still
        reproduces; the bundle rebuilt from it is byte-identical to the staged files; and the leak scan (A5 excerpts) passes."""
        latest = self._latest_audit(scope["scope_id"])
        if not latest or latest["audit_id"] != run["audit_id"]:
            return "NEWER_AUDIT_EXISTS"
        try:
            fresh = self._build(scope, run["run_rowid"])
        except KBError as exc:
            return "REDERIVATION_FAILED:" + exc.code
        if fresh["report_sha256"] != run["report_sha256"]:
            return "AUDIT_NO_LONGER_REPRODUCES"
        rebuilt = self._bundle(fresh)
        if {k: sha256_bytes(v) for k, v in rebuilt.items()} != {k: sha256_bytes(v) for k, v in files.items()}:
            return "BUNDLE_CHANGED"
        for name, data in files.items():
            if not (staging / name).is_file() or sha256_bytes((staging / name).read_bytes()) != sha256_bytes(data):
                return "STAGED_FILE_CHANGED"
        try:
            self._leak_scan(scope, files)
        except KBError as exc:
            return exc.code
        return None

    def _close(self, reservation, phase, outcome, *, quiet=False):
        kb = self.kb
        row = {k: reservation[k] for k in ("scope_id", "audit_id", "destination", "bundle_sha256", "files_json", "contains_quotes", "target_path_hash",
                                           "folder_name", "created_by")}
        row.update(export_id=kb.ids.new("sye"), phase=phase, reservation_id=reservation["export_id"], outcome_code=outcome, created_at_utc=kb.now())
        try:
            with kb.transaction():
                kb.insert("syn_export_event", row)
        except Exception:
            if not quiet:
                raise
            return None
        return row

    def share(self, *, scope_id: str, destination_class: str, operator: str) -> dict:
        """External sharing / publication of a synthesis package: always DENIED in S11 (owner gate OAG-7 has no policy; gate A7
        redistribute is evaluated per member and reported). The refusal is recorded; no file is written."""
        actor = operator_label(operator)
        kb = self.kb
        scope = self._scope(scope_id)
        if not isinstance(destination_class, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,59}", destination_class):
            raise ValidationError("destination_class: UPPER_SNAKE_CASE", code="DESTINATION_INVALID")
        reasons = ["OAG7_SHARING_POLICY_NOT_AUTHORIZED"]
        for (sha, a) in self._sources(scope["members"]):
            g = rights_policy.gate("A7", a)
            if g["decision"] != "ALLOW":
                reasons.append("A7_" + g["reason"])
        reasons = sorted(set(reasons))
        with kb.transaction():
            kb.insert("syn_share_request", dict(request_id=kb.ids.new("syh"), scope_id=scope_id, destination_class=destination_class, decision="DENIED",
                                                reason_codes_json=canonical_json(reasons), created_by=actor, created_at_utc=kb.now()))
        raise PolicyRefusal("external sharing of synthesis packages is not authorized (A7 + OAG-7): " + ",".join(reasons), code="SHARING_NOT_AUTHORIZED")
