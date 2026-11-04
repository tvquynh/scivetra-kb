"""DOI-union reconciliation, deterministic read projection, conflicts and coverage (S04J steps 3-5).

A collection version is computed from named runs (the latest FINISHED full run per provider, plus later incremental
runs) under named policies, and stored append-only. Rebuilding from the same runs and policies is a no-op.

Rules (``POLICY``):

* Identity: the S02 DOI comparison key (ASCII-only case folding, no Unicode normalization, no suffix trimming). Records
  without a usable DOI are kept as their own subjects (NO_DOI / DOI_AMBIGUOUS / DOI_INVALID), never dropped or merged.
  Near-duplicates (trailing Zs, NFC variants) are review tasks, never merges. Two OpenAlex works with one DOI stay two
  works. A preprint/journal pair is only ever a VERSION_LINK_CANDIDATE task.
* Membership: asserted only on venue evidence: Crossref ``/journals/{ISSN}/works`` or OpenAlex
  ``primary_location.source.id``. ``locations.source.id`` matches are alternate-copy evidence (membership_asserted = 0).
  A DOI seen earlier but absent from the latest runs stays listed (NOT_SEEN_IN_LATEST_RUNS), never deleted.
* Projection precedence ``jh-projection/1``: Crossref first for bibliographic fields, OpenAlex for OA/location fields and
  as fallback; every chosen value names its provider/observation/run/page/JSON pointer; disagreements are stored as
  conflicts with all values (nothing overwritten) and raise review tasks.
* Integrity: every integrity signal ever observed for the DOI (any provider, any run) stays in the projection. A signal
  missing from the latest snapshot is flagged INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT; it is never a counter-retraction.
* OA dry run ``jh-oa-dryrun/1``: a location becomes a DRY_RUN_CANDIDATE only if OpenAlex reports it OA with a PDF URL,
  the accepted S03 ``rights-policy/1`` classifies it PUBLIC_OA_LICENSED from Crossref licence evidence for the same
  version, and the S04 URL guard accepts the URL. PDF identity stays UNVERIFIED; nothing is downloaded.
* Coverage: COMPLETE_RELATIVE_TO_RECORDED_QUERIES only if every provider run in the basis is COMPLETE; BLOCKED if no
  provider produced a usable inventory; otherwise PARTIAL. No count is a completeness criterion.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date

from ..core.canonical import canonical_json, semantic_sha256
from ..core.errors import ValidationError
from ..core.ids import parse_utc
from ..library.common import operator_label
from ..metadata.doi import url_form
from ..metadata.store import zs_rstrip
from ..rights import policy as rights
from ..bridge.policy import url_refusal
from .harvest import add_task

POLICY = {"projection": "jh-projection/1", "type_compatibility": "jh-type-compat/1", "oa_dry_run": "jh-oa-dryrun/1",
          "doi_identity": "s02-doi-comparison-key (ASCII-only case fold; DOI Handbook 2025 4.3.4)", "rights_policy": rights.POLICY_VERSION,
          "licence_uri_rules": rights.LICENCE_URI_RULES, "membership": "venue-evidence-only/1"}
# Crossref type -> OpenAlex types that do not indicate a different kind of manifestation.
TYPE_COMPAT = {"journal-article": {"article", "review", "letter", "editorial", "erratum", "retraction", "paratext", "other"},
               "posted-content": {"preprint"}, "peer-review": {"peer-review"}, "component": {"other", "paratext"},
               "journal-issue": {"paratext", "other"}, "journal-volume": {"paratext", "other"}, "dataset": {"dataset"},
               "book-chapter": {"book-chapter"}, "proceedings-article": {"article"}, "report": {"report"}, "other": {"other", "paratext"}}
VERSION_RELATIONS = ("has-preprint", "is-preprint-of", "has-version", "is-version-of", "has-manuscript", "is-manuscript-of")
STATEMENT = ("DISCOVERY_EVIDENCE_ONLY: a journal inventory is not an SLR inclusion, a screening decision, a sealed Reprovane corpus "
             "member, an identity-confirmed PDF or a download entitlement.")
LIMITATIONS = [
    "Counts reported by providers are snapshots at request time; they are never used as completeness criteria.",
    "COMPLETE is relative to the recorded provider queries, filters and pagination termination; it does not assert that every work the journal published was found.",
    "Works neither provider lists for the journal (indexing gaps, other venues) cannot appear.",
    "OpenAlex alternate-location matches are evidence of a copy, not of journal membership.",
    "A DOI prefix is a diagnostic only and is never used to decide membership.",
]


def _date_text(value) -> str | None:
    parts = (value or {}).get("date_parts") if isinstance(value, dict) else None
    if not parts or not isinstance(parts, list) or not parts[0] or parts[0][0] is None:
        return None
    row = [p for p in parts[0] if isinstance(p, int)]
    return "-".join([f"{row[0]:04d}"] + [f"{p:02d}" for p in row[1:3]]) if row else None


def _norm_title(text) -> str | None:
    if not isinstance(text, str):
        return None
    return " ".join(re.sub(r"<[^>]{1,40}>", " ", text).split()).casefold() or None   # conflict detection only, never identity


class _Obs:
    def __init__(self, row: dict, claims: list[dict], run_order: int):
        self.row, self.claims, self.run_order = row, claims, run_order
        self.order = (run_order, row["page_seq"], row["item_index"])

    def first(self, field):
        for c in self.claims:
            if c["field"] == field:
                return c
        return None

    def all(self, field):
        return [c for c in self.claims if c["field"] == field]

    def ref(self, claim=None) -> dict:
        out = {"provider": self.row["provider"], "observation_id": self.row["observation_id"], "run_id": self.row["run_id"],
               "page_id": self.row["page_id"], "path": claim["path"] if claim else self.row["item_pointer"],
               "observed_at": self.row["observed_at_utc"], "venue_evidence": self.row["venue_evidence"]}
        if self.row.get("query_issn"):
            out["query_issn"] = self.row["query_issn"]
        return out


class Reconciler:
    def __init__(self, kb, *, operator: str):
        self.kb = kb
        self.operator = operator_label(operator)

    # -- inputs --------------------------------------------------------------------------------------------------------
    def _runs(self, journal_id: str) -> list[dict]:
        rows = self.kb.all("SELECT r.*, r.rowid AS run_order, e.state, e.stop_reason, e.created_at_utc AS finished_at FROM jh_run r "
                           "LEFT JOIN jh_run_event e ON e.run_id = r.run_id AND e.kind='FINISHED' WHERE r.journal_id=? "
                           "AND r.provider IN ('CROSSREF','OPENALEX','OPENALEX_ALTERNATE') ORDER BY r.rowid", (journal_id,))
        return rows

    def basis(self, journal_id: str) -> dict:
        """Latest FINISHED FULL run per provider, plus FINISHED INCREMENTAL runs started after it."""
        runs = self._runs(journal_id)
        out = {}
        for provider in ("CROSSREF", "OPENALEX", "OPENALEX_ALTERNATE"):
            mine = [r for r in runs if r["provider"] == provider]
            full = [r for r in mine if r["mode"] == "FULL" and r["state"] is not None]
            base = full[-1] if full else None
            incremental = [r for r in mine if r["mode"] == "INCREMENTAL" and r["state"] is not None and base and r["run_order"] > base["run_order"]]
            unfinished = [r["run_id"] for r in mine if r["state"] is None]
            out[provider] = {"full_run_id": base["run_id"] if base else None, "state": base["state"] if base else "NOT_RUN",
                             "stop_reason": base["stop_reason"] if base else None,
                             "incremental_run_ids": [r["run_id"] for r in incremental],
                             "incremental_states": [r["state"] for r in incremental], "unfinished_run_ids": unfinished}
        return out

    def _observations(self, run_ids: list[str]) -> list[_Obs]:
        if not run_ids:
            return []
        marks = ",".join("?" for _ in run_ids)
        order = {r["run_id"]: r["rowid"] for r in self.kb.all(f"SELECT run_id, rowid FROM jh_run WHERE run_id IN ({marks})", run_ids)}
        rows = self.kb.all(f"SELECT o.*, p.seq AS page_seq, json_extract(p.request_identity_json,'$.issn') AS query_issn "
                           f"FROM jh_observation o JOIN jh_page p ON p.page_id = o.page_id "
                           f"WHERE o.run_id IN ({marks})", run_ids)
        claims = {}
        for c in self.kb.all(f"SELECT c.claim_id, c.observation_id, c.field, c.value_json, c.source_field_path FROM jh_field_claim c "
                             f"JOIN jh_observation o ON o.observation_id = c.observation_id WHERE o.run_id IN ({marks}) ORDER BY c.rowid", run_ids):
            claims.setdefault(c["observation_id"], []).append({"claim_id": c["claim_id"], "field": c["field"],
                                                               "value": json.loads(c["value_json"]), "path": c["source_field_path"]})
        out = [_Obs(r, claims.get(r["observation_id"], []), order[r["run_id"]]) for r in rows]
        out.sort(key=lambda o: o.order)
        return out

    # -- reconcile -----------------------------------------------------------------------------------------------------
    def reconcile(self, journal_id: str) -> dict:
        journal = self.kb.one("SELECT * FROM jh_journal WHERE journal_id=?", (journal_id,))
        if not journal:
            raise ValidationError("Unknown journal", code="UNKNOWN_JOURNAL")
        basis = self.basis(journal_id)
        if all(b["full_run_id"] is None for b in basis.values()):
            raise ValidationError("No finished full run to reconcile; run 'journal harvest' first", code="NOTHING_TO_RECONCILE")
        current_ids = [rid for b in basis.values() for rid in ([b["full_run_id"]] if b["full_run_id"] else []) + b["incremental_run_ids"]]
        all_ids = [r["run_id"] for r in self._runs(journal_id)]
        current = self._observations(current_ids)
        history = self._observations(all_ids)
        as_of_text = self.kb.now()
        as_of = parse_utc(as_of_text).date()
        known_issns = {r["issn"] for r in self.kb.all("SELECT DISTINCT issn FROM jh_issn_evidence WHERE journal_id=?", (journal_id,))}
        selected_sources = {r["provider_source_id"] for r in self.kb.all(
            "SELECT provider_source_id FROM jh_source_resolution WHERE journal_id=? AND provider='OPENALEX' AND status='SELECTED'", (journal_id,))}

        by_key: dict[str, dict[str, list[_Obs]]] = {}
        no_doi: dict[str, list[_Obs]] = {}
        for obs in current:
            key = obs.row["doi_key"]
            slot = {"CROSSREF_JOURNAL_ENDPOINT": "CROSSREF", "OPENALEX_PRIMARY_LOCATION": "OPENALEX",
                    "OPENALEX_ANY_LOCATION": "OPENALEX_ALTERNATE"}[obs.row["venue_evidence"]]
            if key is None:
                ref = f"{obs.row['provider']}:{obs.row['provider_record_id'] or obs.row['observation_id']}"
                no_doi.setdefault(ref, []).append(obs)
            else:
                by_key.setdefault(key, {}).setdefault(slot, []).append(obs)
        historic_members = {}
        for obs in history:
            if obs.row["doi_key"] and obs.row["venue_evidence"] != "OPENALEX_ANY_LOCATION":
                historic_members.setdefault(obs.row["doi_key"], []).append(obs)
        signals_hist, signals_now = self._signals(history), self._signals(current)

        members, conflicts, tasks = [], [], []
        all_keys = sorted(set(by_key) | set(historic_members))
        near = self._near_duplicates(sorted(by_key))
        for key in all_keys:
            slots = by_key.get(key, {})
            member, mconf, mtasks = self._doi_member(key, slots, historic_members.get(key, []), basis, signals_hist, signals_now,
                                                     known_issns, selected_sources, near.get(key, []), as_of)
            members.append(member)
            conflicts += mconf
            tasks += mtasks
        for ref in sorted(no_doi):
            member, mtasks = self._record_member(ref, no_doi[ref])
            members.append(member)
            tasks += mtasks
        for member in members:
            member["semantic_sha256"] = semantic_sha256({k: member[k] for k in member if k not in ("semantic_sha256",)})
        members_sha = semantic_sha256(sorted(m["semantic_sha256"] for m in members))
        coverage = self._coverage(journal, basis, members, conflicts, as_of_text)
        state = coverage["collection_state"]
        semantic = semantic_sha256({"journal": journal_id, "basis": basis, "policy": POLICY, "members": members_sha})
        existing = self.kb.one("SELECT * FROM jh_collection_version WHERE semantic_sha256=?", (semantic,))
        if existing:
            return {"version_id": existing["version_id"], "version_no": existing["version_no"], "created": False,
                    "collection_state": existing["collection_state"], "member_count": existing["member_count"],
                    "coverage": json.loads(existing["coverage_json"])}
        version_no = (self.kb.one("SELECT max(version_no) m FROM jh_collection_version WHERE journal_id=?", (journal_id,))["m"] or 0) + 1
        version_id = self.kb.ids.new("jcv")
        coverage["collection_version"] = {"version_id": version_id, "version_no": version_no}
        with self.kb.transaction():
            self.kb.insert("jh_collection_version", dict(
                version_id=version_id, journal_id=journal_id, version_no=version_no, basis_json=canonical_json(basis),
                policy_json=canonical_json(POLICY), coverage_json=canonical_json(coverage), collection_state=state,
                member_count=len(members), members_sha256=members_sha, created_by=self.operator, created_at_utc=as_of_text,
                semantic_sha256=semantic))
            for m in members:
                self.kb.insert("jh_member", dict(
                    member_id=self.kb.ids.new("jcm"), version_id=version_id, subject_kind=m["subject_kind"], doi_key=m["doi_key"],
                    record_ref=m["record_ref"], category=m["category"], membership_asserted=m["membership_asserted"],
                    providers_json=canonical_json(m["providers"]), evidence_json=canonical_json(m["evidence"]),
                    projection_json=canonical_json(m["projection"]), provenance_json=canonical_json(m["provenance"]),
                    flags_json=canonical_json(m["flags"]), paper_candidates_json=canonical_json(m["paper_candidates"]),
                    semantic_sha256=m["semantic_sha256"]))
            for c in conflicts:
                self.kb.insert_unique("jh_conflict", dict(conflict_id=self.kb.ids.new("jcf"), version_id=version_id, subject=c["subject"],
                                                          field=c["field"], kind=c["kind"], values_json=canonical_json(c["values"]),
                                                          semantic_sha256=semantic_sha256(c)), )
            created_tasks = 0
            for t in tasks:
                _, created = add_task(self.kb, journal_id, version_id, subject=t["subject"], trigger=t["trigger"], context=t["context"],
                                      in_transaction=True)
                created_tasks += created
        coverage_out = json.loads(self.kb.one("SELECT coverage_json FROM jh_collection_version WHERE version_id=?", (version_id,))["coverage_json"])
        return {"version_id": version_id, "version_no": version_no, "created": True, "collection_state": state, "member_count": len(members),
                "conflicts": len(conflicts), "review_tasks_created": created_tasks, "coverage": coverage_out}

    # -- members -------------------------------------------------------------------------------------------------------
    @staticmethod
    def _signals(observations: list[_Obs]) -> dict:
        out = {}
        for obs in observations:
            for claim in obs.all("integrity_signal"):
                v = claim["value"]
                ident = (v["subject_doi_key"], obs.row["provider"], v["reported_by"], v["event_type"], v.get("notice_doi_key"))
                out.setdefault(v["subject_doi_key"], {}).setdefault(ident, []).append((obs, claim))
        return out

    @staticmethod
    def _near_duplicates(keys: list[str]) -> dict:
        out = {}
        by_strip, by_nfc = {}, {}
        for key in keys:
            by_strip.setdefault(zs_rstrip(key), []).append(key)
            by_nfc.setdefault(unicodedata.normalize("NFC", key), []).append(key)
        for group, kind in [(g, "DOI_NEAR_DUPLICATE_TRAILING_SPACE") for g in by_strip.values()] + \
                           [(g, "DOI_UNICODE_NORMALIZATION_VARIANT") for g in by_nfc.values()]:
            if len(group) > 1:
                for key in group:
                    out.setdefault(key, []).append({"kind": kind, "keys": sorted(group)})
        return out

    def _paper_candidates(self, key: str) -> list[str]:
        return [r["paper_id"] for r in self.kb.all(
            "SELECT DISTINCT paper_id FROM identity_assertion_effective WHERE namespace='doi' AND value_normalized=? "
            "AND effective_status IN ('ACTIVE','CONFLICTING') ORDER BY paper_id", (key,))]

    def _doi_member(self, key, slots, historic, basis, signals_hist, signals_now, known_issns, selected_sources, near, as_of):
        flags, conflicts, tasks = set(), [], []
        cr = slots.get("CROSSREF", [])
        oa = slots.get("OPENALEX", [])
        alt = slots.get("OPENALEX_ALTERNATE", [])
        cr_latest = cr[-1] if cr else None
        oa_ids = sorted({o.row["provider_record_id"] for o in oa if o.row["provider_record_id"]})
        oa_latest_by_id = {}
        for o in oa:
            oa_latest_by_id[o.row["provider_record_id"]] = o
        oa_latest = oa_latest_by_id[oa_ids[0]] if oa_ids else (oa[-1] if oa else None)
        alt_latest = alt[-1] if alt else None
        if len(oa_ids) > 1:
            flags.add("DUPLICATE_PROVIDER_RECORDS_SAME_DOI")
            tasks.append({"subject": key, "trigger": "DUPLICATE_PROVIDER_RECORDS_SAME_DOI", "context": {"provider": "OPENALEX", "work_ids": oa_ids}})
        if cr and oa:
            category, asserted = "BOTH", 1
        elif cr:
            category, asserted = "CROSSREF_ONLY", 1
        elif oa:
            category, asserted = "OPENALEX_ONLY", 1
        elif alt:
            category, asserted = "OPENALEX_ALTERNATE_LOCATION_ONLY", 0
            tasks.append({"subject": key, "trigger": "ALTERNATE_LOCATION_ONLY", "context": {"openalex_work_ids": sorted({o.row["provider_record_id"] for o in alt})}})
        else:
            category, asserted = "NOT_SEEN_IN_LATEST_RUNS", 0
        # A one-provider category is only meaningful if the OTHER provider's basis run reached its termination.
        other = {"CROSSREF_ONLY": "OPENALEX", "OPENALEX_ONLY": "CROSSREF"}.get(category)
        if other and basis[other]["state"] != "COMPLETE":
            flags.add(f"{other}_RUN_NOT_COMPLETE_SET_DIFFERENCE_UNRELIABLE")
        if not cr and not oa and historic:
            last = historic[-1]
            providers_seen = sorted({o.row["provider"] for o in historic})
            complete = [p for p in ("CROSSREF", "OPENALEX") if p in providers_seen and basis[p]["state"] == "COMPLETE"]
            flags.add("NOT_SEEN_IN_LATEST_COMPLETE_RUN" if complete else "NOT_SEEN_IN_LATEST_RUN_WHICH_WAS_NOT_COMPLETE")
            if category == "NOT_SEEN_IN_LATEST_RUNS" or complete:
                tasks.append({"subject": key, "trigger": "NOT_SEEN_IN_LATEST_COMPLETE_RUN",
                              "context": {"last_seen_run_id": last.row["run_id"], "providers_seen_before": providers_seen,
                                          "latest_complete_providers": complete}})
        if alt and (cr or oa) and not oa:
            alt_primary = (alt_latest.first("primary_source") or {}).get("value") or {}
            if alt_primary.get("source_id") and alt_primary.get("source_id") not in selected_sources:
                flags.add("VENUE_MISMATCH")
                conflicts.append({"subject": key, "field": "venue", "kind": "VENUE_MISMATCH", "values": [
                    dict(cr_latest.ref(), value="CROSSREF_JOURNAL_ENDPOINT"),
                    dict(alt_latest.ref(alt_latest.first("primary_source")), value=alt_primary.get("source_id"))]})
                tasks.append({"subject": key, "trigger": "VENUE_MISMATCH", "context": {"openalex_primary_source": alt_primary.get("source_id"),
                                                                                        "selected_sources": sorted(selected_sources)}})
        if cr_latest is not None:
            same_run = [o for o in cr if o.row["run_id"] == cr_latest.row["run_id"]]
            by_issn = {}
            for o in same_run:
                by_issn[o.row["query_issn"]] = o          # latest observation per ISSN query within the latest Crossref run
            if len({o.row["item_sha256"] for o in by_issn.values()}) > 1:
                flags.add("CROSSREF_ISSN_QUERIES_DISAGREE")
                tasks.append({"subject": key, "trigger": "FIELD_CONFLICT", "context": {
                    "kind": "CROSSREF_ISSN_QUERIES_DISAGREE", "issn_records": {i: o.row["observation_id"] for i, o in sorted(by_issn.items())}}})
        projection, provenance = self._project(key, cr_latest, oa_latest, alt_latest if not (cr or oa) else None)
        projection["crossref_query_issns"] = sorted({o.row["query_issn"] for o in cr if o.row["query_issn"]})
        projection["openalex_work_ids"] = oa_ids or sorted({o.row["provider_record_id"] for o in alt if o.row["provider_record_id"]})
        # field conflicts (Crossref vs OpenAlex), every value kept with its source
        if cr_latest and oa_latest:
            for field, kind, a, b in self._compare(cr_latest, oa_latest):
                flags.add(kind)
                conflicts.append({"subject": key, "field": field, "kind": kind, "values": [a, b]})
                trigger = "WORK_TYPE_VERSION_MISMATCH" if kind == "WORK_TYPE_CONFLICT" else "FIELD_CONFLICT"
                tasks.append({"subject": key, "trigger": trigger, "context": {"field": field, "kind": kind,
                                                                              "values": [a.get("value"), b.get("value")]}})
            oa_primary = (oa_latest.first("primary_source") or {}).get("value") or {}
            if oa_primary.get("source_id") and selected_sources and oa_primary["source_id"] not in selected_sources:
                flags.add("VENUE_MISMATCH")
        if cr_latest:
            issns = {(c["value"] or {}).get("value") for c in cr_latest.all("issn")}
            if issns and known_issns and not (issns & known_issns):
                flags.add("ISSN_NOT_AMONG_JOURNAL_ISSNS")
                conflicts.append({"subject": key, "field": "issn", "kind": "ISSN_MISMATCH", "values": [
                    dict(cr_latest.ref(cr_latest.first("issn")), value=sorted(i for i in issns if i)),
                    {"provider": "JOURNAL_RESOLUTION", "value": sorted(known_issns)}]})
                tasks.append({"subject": key, "trigger": "FIELD_CONFLICT", "context": {"field": "issn", "kind": "ISSN_MISMATCH"}})
            for rel in cr_latest.all("relation"):
                name = (rel["value"] or {}).get("name")
                if name in VERSION_RELATIONS:
                    flags.add("VERSION_LINK_CANDIDATE")
                    tasks.append({"subject": key, "trigger": "VERSION_LINK_CANDIDATE", "context": {
                        "relation": name, "related_id": rel["value"].get("id"), "id_type": rel["value"].get("id_type"),
                        "policy": "NEVER_AUTO_MERGED"}})
        # history: earlier observations of the same provider record with different values (non-destructive change log)
        projection_history = self._history(historic + alt)
        if projection_history:
            flags.add("METADATA_CHANGED_ACROSS_RUNS")
        # integrity
        signals = []
        for ident, seen in sorted(signals_hist.get(key, {}).items(), key=lambda kv: canonical_json(list(kv[0]))):
            obs0, claim0 = seen[0]
            latest_now = ident in signals_now.get(key, {})
            entry = {"provider": ident[1], "reported_by": ident[2], "event_type": ident[3], "notice_doi_key": ident[4],
                     "first_seen": obs0.ref(claim0), "last_seen": seen[-1][0].ref(seen[-1][1]), "present_in_latest_snapshot": latest_now,
                     "event_subtype": claim0["value"].get("event_subtype")}
            signals.append(entry)
            tasks.append({"subject": key, "trigger": "INTEGRITY_SIGNAL_REPORTED", "context": {k: entry[k] for k in
                                                                                                 ("provider", "reported_by", "event_type", "notice_doi_key")}})
            if not latest_now:
                flags.add("INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT")
                tasks.append({"subject": key, "trigger": "INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT", "context": {
                    k: entry[k] for k in ("provider", "reported_by", "event_type", "notice_doi_key")}})
        projection["integrity_signals"] = signals
        projection["retraction_reported_ever"] = any(s["event_type"] == "RETRACTION" for s in signals)
        projection["integrity_event_types"] = sorted({s["event_type"] for s in signals})
        for n in near:
            flags.add(n["kind"])
            tasks.append({"subject": n["keys"][0], "trigger": n["kind"], "context": {"keys": n["keys"]}})
        for o in (cr + oa + alt):
            if o.row["shape_status"] == "MALFORMED":
                flags.add("MALFORMED_PROVIDER_RECORD")
                tasks.append({"subject": key, "trigger": "MALFORMED_PROVIDER_RECORD", "context": {"observation_id": o.row["observation_id"],
                                                                                                   "reason": o.row["shape_reason"]}})
        seen_obs = sorted(historic + cr + oa + alt, key=lambda o: o.order)
        projection["first_seen_at"] = seen_obs[0].row["observed_at_utc"] if seen_obs else None
        projection["last_seen_at"] = seen_obs[-1].row["observed_at_utc"] if seen_obs else None
        projection["oa_dry_run"] = self._oa_dry_run(cr_latest, oa_latest if asserted else None, as_of)
        if projection["retraction_reported_ever"] and projection["oa_dry_run"]["status"] == "DRY_RUN_CANDIDATE":
            # A retraction ever reported for the DOI needs a human decision first; it is never queued automatically.
            projection["oa_dry_run"] = {k: v for k, v in projection["oa_dry_run"].items() if k in ("pdf_identity", "download_authorized", "rule")}
            projection["oa_dry_run"].update(status="NOT_A_CANDIDATE", reasons=["RETRACTION_REPORTED_REVIEW_REQUIRED"])
        projection["history"] = projection_history
        providers = (["CROSSREF"] if cr else []) + (["OPENALEX"] if oa else []) + (["OPENALEX_ALTERNATE"] if alt else [])
        evidence = {"crossref_observations": [o.row["observation_id"] for o in cr],
                    "crossref_observations_by_issn": {i: [o.row["observation_id"] for o in cr if o.row["query_issn"] == i]
                                                      for i in sorted({o.row["query_issn"] for o in cr if o.row["query_issn"]})},
                    "openalex_observations": [o.row["observation_id"] for o in oa],
                    "openalex_alternate_observations": [o.row["observation_id"] for o in alt],
                    "historic_observations": [o.row["observation_id"] for o in historic][-20:]}
        member = {"subject_kind": "DOI", "doi_key": key, "record_ref": None, "category": category, "membership_asserted": asserted,
                  "providers": providers, "evidence": evidence, "projection": projection, "provenance": provenance,
                  "flags": sorted(flags), "paper_candidates": self._paper_candidates(key)}
        return member, conflicts, tasks

    def _record_member(self, ref: str, observations: list[_Obs]):
        members = [o for o in observations if o.row["venue_evidence"] != "OPENALEX_ANY_LOCATION"]
        latest = (members or observations)[-1]
        status = latest.row["doi_status"]
        category = {"MISSING": "NO_DOI", "AMBIGUOUS": "DOI_AMBIGUOUS", "INVALID": "DOI_INVALID"}[status]
        asserted = 1 if members else 0
        projection, provenance = self._project(None, latest if latest.row["provider"] == "CROSSREF" else None,
                                               latest if latest.row["provider"] == "OPENALEX" else None, None)
        projection["crossref_query_issns"] = sorted({o.row["query_issn"] for o in observations if o.row.get("query_issn")})
        projection.update(doi_original=latest.row["doi_original"], integrity_signals=[], retraction_reported_ever=False, integrity_event_types=[],
                          first_seen_at=observations[0].row["observed_at_utc"], last_seen_at=latest.row["observed_at_utc"],
                          oa_dry_run={"status": "NOT_A_CANDIDATE", "reasons": ["NO_USABLE_DOI"], "pdf_identity": "UNVERIFIED",
                                      "download_authorized": False}, history=[],
                          openalex_work_ids=[latest.row["provider_record_id"]] if latest.row["provider"] == "OPENALEX" else [])
        flags = ["NO_USABLE_DOI"] + (["MALFORMED_PROVIDER_RECORD"] if latest.row["shape_status"] == "MALFORMED" else [])
        tasks = []
        if status in ("AMBIGUOUS", "INVALID"):
            tasks.append({"subject": ref, "trigger": "DOI_AMBIGUOUS" if status == "AMBIGUOUS" else "DOI_INVALID",
                          "context": {"doi_original": latest.row["doi_original"], "parse": json.loads(latest.row["doi_parse_json"] or "null")}})
        if latest.row["shape_status"] == "MALFORMED":
            tasks.append({"subject": ref, "trigger": "MALFORMED_PROVIDER_RECORD", "context": {"observation_id": latest.row["observation_id"],
                                                                                               "reason": latest.row["shape_reason"]}})
        venues = sorted({o.row["venue_evidence"] for o in observations})
        member = {"subject_kind": "RECORD_WITHOUT_USABLE_DOI", "doi_key": None, "record_ref": ref, "category": category,
                  "membership_asserted": asserted, "providers": [latest.row["provider"]] + (["OPENALEX_ALTERNATE"] if
                                                                                          "OPENALEX_ANY_LOCATION" in venues and asserted else []),
                  "evidence": {"observations": [o.row["observation_id"] for o in observations]}, "projection": projection,
                  "provenance": provenance, "flags": flags, "paper_candidates": []}
        return member, tasks

    # -- projection ----------------------------------------------------------------------------------------------------
    def _project(self, key, cr: _Obs | None, oa: _Obs | None, alt: _Obs | None):
        proj, prov = {}, {}

        def take(name, *options):
            for obs, field, convert in options:
                if obs is None:
                    continue
                claim = obs.first(field)
                if claim is None:
                    continue
                value = convert(claim["value"]) if convert else claim["value"]
                if value is None:
                    continue
                proj[name] = value
                prov[name] = obs.ref(claim)
                return
            proj[name] = None

        same = None
        oa_like = oa or alt
        proj["doi_key"] = key
        proj["doi_url"] = url_form(key) if key else None
        proj["doi_original_crossref"] = cr.row["doi_original"] if cr else None
        proj["doi_original_openalex"] = oa_like.row["doi_original"] if oa_like else None
        take("title", (cr, "title", same), (oa_like, "title", same))
        take("work_type", (cr, "work_type", same))
        take("openalex_type", (oa_like, "work_type", same))
        take("container_title", (cr, "container_title", same), (oa_like, "primary_source", lambda v: v.get("display_name")))
        take("publisher", (cr, "publisher", same))
        take("published_online", (cr, "date_published_online", _date_text))
        take("published_print", (cr, "date_published_print", _date_text))
        take("issued", (cr, "date_issued", _date_text))
        take("openalex_publication_date", (oa_like, "publication_date", same))
        year_basis = None
        for name in ("published_online", "published_print", "issued"):
            if proj.get(name):
                proj["publication_year"], year_basis = int(proj[name][:4]), f"CROSSREF_{name.upper()}"
                prov["publication_year"] = prov[name]
                break
        else:
            take("publication_year", (oa_like, "publication_year", same))
            year_basis = "OPENALEX_PUBLICATION_YEAR" if proj.get("publication_year") is not None else None
        proj["publication_year_basis"] = year_basis
        take("volume", (cr, "volume", same), (oa_like, "volume", same))
        take("issue", (cr, "issue", same), (oa_like, "issue", same))
        take("pages", (cr, "pages", same), (oa_like, "pages", same))
        take("article_number", (cr, "article_number", same))
        take("landing_page", (cr, "landing_page", same), (oa_like, "landing_page", same))
        take("crossref_updated_at", (cr, "provider_updated_at", same))
        take("openalex_updated_at", (oa_like, "provider_updated_at", same))
        proj["issns"] = sorted({(c["value"] or {}).get("value") for c in cr.all("issn")} - {None}) if cr else []
        if cr and cr.all("author"):
            authors = [c["value"] for c in cr.all("author")]
            proj["authors"] = [", ".join(filter(None, [a.get("family"), a.get("given")])) if a.get("family") else (a.get("literal") or "")
                               for a in authors]
            prov["authors"] = cr.ref(cr.first("author"))
        elif oa_like and oa_like.all("author"):
            proj["authors"] = [c["value"].get("literal") or "" for c in oa_like.all("author")]
            prov["authors"] = oa_like.ref(oa_like.first("author"))
        else:
            proj["authors"] = []
        proj["license_urls"] = sorted({(c["value"] or {}).get("url") for c in cr.all("license")} - {None}) if cr else []
        if cr and cr.all("license"):
            prov["license_urls"] = cr.ref(cr.first("license"))
        oa_info = oa_like.first("open_access") if oa_like else None
        proj["is_oa"] = (oa_info["value"] or {}).get("is_oa") if oa_info else None
        proj["oa_status"] = (oa_info["value"] or {}).get("oa_status") if oa_info else None
        if oa_info:
            prov["is_oa"] = oa_like.ref(oa_info)
        best = oa_like.first("best_oa_location") if oa_like else None
        proj["best_oa_landing_url"] = (best["value"] or {}).get("landing_page_url") if best else None
        proj["best_oa_pdf_url"] = (best["value"] or {}).get("pdf_url") if best else None
        if best:
            prov["best_oa_location"] = oa_like.ref(best)
        retracted = oa_like.first("is_retracted") if oa_like else None
        proj["openalex_is_retracted_latest"] = retracted["value"] if retracted else None
        return proj, prov

    @staticmethod
    def _compare(cr: _Obs, oa: _Obs):
        out = []
        a, b = cr.first("title"), oa.first("title")
        if a and b and _norm_title(a["value"]) != _norm_title(b["value"]):
            out.append(("title", "TITLE_CONFLICT", dict(cr.ref(a), value=a["value"]), dict(oa.ref(b), value=b["value"])))
        year_claim = None
        for field in ("date_published_online", "date_published_print", "date_issued"):
            year_claim = cr.first(field)
            if year_claim and _date_text(year_claim["value"]):
                break
            year_claim = None
        oy = oa.first("publication_year")
        if year_claim and oy and int(_date_text(year_claim["value"])[:4]) != oy["value"]:
            out.append(("publication_year", "DATE_CONFLICT", dict(cr.ref(year_claim), value=_date_text(year_claim["value"])),
                        dict(oa.ref(oy), value=oy["value"])))
        ct, ot = cr.first("work_type"), oa.first("work_type")
        if ct and ot and ot["value"] not in TYPE_COMPAT.get(ct["value"], set()):
            out.append(("work_type", "WORK_TYPE_CONFLICT", dict(cr.ref(ct), value=ct["value"]), dict(oa.ref(ot), value=ot["value"])))
        return out

    @staticmethod
    def _history(observations: list[_Obs]) -> list[dict]:
        """Value changes between successive observations of the same provider record (oldest first)."""
        tracked = ("title", "work_type", "volume", "issue", "pages", "license", "is_retracted", "date_published_online",
                   "date_published_print", "publication_year", "integrity_signal", "open_access", "best_oa_location")
        out, last = [], {}
        for obs in sorted(observations, key=lambda o: o.order):
            ident = (obs.row["provider"], obs.row["provider_record_id"], obs.row.get("query_issn"))
            values = {f: sorted(canonical_json(c["value"]) for c in obs.all(f)) for f in tracked}
            if ident in last:
                prior_obs, prior = last[ident]
                for field in tracked:
                    if prior[field] != values[field]:
                        out.append({"provider": ident[0], "record": ident[1], "query_issn": ident[2], "field": field,
                                    "before": [json.loads(v) for v in prior[field]], "after": [json.loads(v) for v in values[field]],
                                    "before_ref": prior_obs.ref(), "after_ref": obs.ref()})
            last[ident] = (obs, values)
        return out

    # -- OA dry run (jh-oa-dryrun/1) ----------------------------------------------------------------------------------
    @staticmethod
    def _oa_dry_run(cr: _Obs | None, oa: _Obs | None, as_of: date) -> dict:
        base = {"pdf_identity": "UNVERIFIED", "download_authorized": False, "rule": POLICY["oa_dry_run"]}
        if oa is None:
            return dict(base, status="NOT_A_CANDIDATE", reasons=["NO_OPENALEX_PRIMARY_RECORD"])
        locations = []
        best = oa.first("best_oa_location")
        if best:
            locations.append(("best_oa_location", best))
        locations += [("location", c) for c in oa.all("location")]
        oa_locations = [(kind, c) for kind, c in locations if (c["value"] or {}).get("is_oa") is True]
        if not oa_locations:
            return dict(base, status="NOT_A_CANDIDATE", reasons=["NO_OA_LOCATION_REPORTED"])
        reasons = []
        licences = []
        if cr:
            for c in cr.all("license"):
                v = c["value"] or {}
                if v.get("content_version") not in rights.CONTENT_VERSION_CLASS or not isinstance(v.get("url"), str):
                    reasons.append("LICENCE_CONTENT_VERSION_NOT_EVALUABLE")
                    continue
                item = {"source": "CROSSREF_LICENSE", "license_uri": v["url"], "content_version": v["content_version"], "claim_ref": c["claim_id"]}
                start = v.get("start")
                if isinstance(start, list) and start and isinstance(start[0], list) and len(start[0]) == 3 and all(isinstance(p, int) for p in start[0]):
                    item["start_date"] = f"{start[0][0]:04d}-{start[0][1]:02d}-{start[0][2]:02d}"
                if isinstance(v.get("delay_in_days"), int) and v["delay_in_days"] >= 0:
                    item["delay_in_days"] = v["delay_in_days"]
                licences.append(item)
        for kind, claim in oa_locations:
            loc = claim["value"]
            if not loc.get("pdf_url"):
                reasons.append("NO_PDF_URL_LANDING_PAGE_ONLY")
                continue
            if loc.get("version") not in rights.LOCATION_VERSION_CLASS:
                reasons.append("LOCATION_VERSION_UNKNOWN")
                continue
            version_class = rights.LOCATION_VERSION_CLASS[loc["version"]][0]
            try:
                result = rights.classify({"license_evidence": licences, "availability_evidence": [
                    {"source": "OPENALEX_LOCATION", "is_oa": True, "version": loc["version"], "claim_ref": claim["claim_id"]}]},
                    version_class=version_class, as_of=as_of)
            except rights.EvidenceError:
                reasons.append("RIGHTS_EVIDENCE_NOT_EVALUABLE")
                continue
            refusal = url_refusal(loc["pdf_url"])
            if result["rights_class"] != "PUBLIC_OA_LICENSED":
                reasons.append("RIGHTS_" + result["rights_class"])
                reasons += result["flags"]
                continue
            if refusal:
                reasons.append(refusal)
                continue
            return dict(base, status="DRY_RUN_CANDIDATE", reasons=[], pdf_url=loc["pdf_url"], location_kind=kind, version=loc["version"],
                        version_class=version_class, rights_class=result["rights_class"], rights_flags=result["flags"],
                        licence_families=sorted({e["family"] for e in result["evaluation"]["licence_items"] if e["family"]}),
                        evidence={"location": oa.ref(claim), "licence_claims": [l["claim_ref"] for l in licences]})
        return dict(base, status="NOT_A_CANDIDATE", reasons=sorted(set(reasons)) or ["NO_ELIGIBLE_LOCATION"])

    # -- coverage ------------------------------------------------------------------------------------------------------
    def _provider_coverage(self, run_id: str | None) -> dict | None:
        if run_id is None:
            return None
        finished = self.kb.one("SELECT state, stop_reason, created_at_utc, detail_json FROM jh_run_event WHERE run_id=? AND kind='FINISHED'", (run_id,))
        run = self.kb.one("SELECT * FROM jh_run WHERE run_id=?", (run_id,))
        detail = json.loads(finished["detail_json"])
        failures = self.kb.all("SELECT outcome, count(*) n FROM jh_page WHERE run_id=? AND outcome<>'OK' GROUP BY outcome ORDER BY outcome", (run_id,))
        counts = self.kb.all("SELECT reported_count, observed_at FROM jh_count_observation WHERE run_id=? ORDER BY rowid", (run_id,))
        return {"run_id": run_id, "mode": run["mode"], "state": finished["state"], "stop_reason": finished["stop_reason"],
                "started_at": run["started_at_utc"], "finished_at": finished["created_at_utc"], "query": json.loads(run["query_json"]),
                "reported_counts": [{"value": c["reported_count"], "observed_at": c["observed_at"],
                                     "scope": "SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION"} for c in counts],
                "counters": detail.get("counters"), "api_errors": {f["outcome"]: f["n"] for f in failures},
                "completion_basis": detail.get("completion_basis")}

    @staticmethod
    def _issn_sets(members) -> dict:
        """How many Crossref DOI subjects each combination of ISSN queries returned, e.g. {"2214-2126+2214-2134": 2186}."""
        out = {}
        for m in members:
            issns = m["projection"].get("crossref_query_issns") or []
            if m["subject_kind"] == "DOI" and issns:
                label = "+".join(issns)
                out[label] = out.get(label, 0) + 1
        return dict(sorted(out.items()))

    @staticmethod
    def _issn_query_table(crossref) -> dict:
        """Per planned Crossref ISSN query: state, stop reason, count snapshots, pages, rows, observations, unique DOIs, replays."""
        if not crossref:
            return {"plan": [], "queries": {}, "requested_not_resolved": [], "note": "no finished Crossref full run in the basis"}
        counters = crossref.get("counters") or {}
        return {"plan": [e["issn"] for e in crossref["query"].get("issn_plan", [])], "queries": counters.get("issn_queries", {}),
                "requested_not_resolved": counters.get("issns_requested_not_resolved", []),
                "doi_keys_in_more_than_one_issn_query": counters.get("doi_keys_in_more_than_one_issn_query"),
                "rows_fetched_all_queries": counters.get("items_returned"), "unique_valid_doi_keys_union": counters.get("unique_valid_doi_keys"),
                "semantics": ("Rows are counted per ISSN query (a DOI returned by two ISSN queries is two rows); unique DOI keys are the union "
                              "over all planned ISSN queries. The Crossref inventory is COMPLETE only if every planned ISSN query reached its "
                              "documented termination and no requested ISSN remained unresolved.")}

    def _coverage(self, journal, basis, members, conflicts, as_of_text) -> dict:
        providers = {p: self._provider_coverage(b["full_run_id"]) for p, b in basis.items()}
        for p, b in basis.items():
            if providers[p] is not None:
                providers[p]["incremental_runs"] = [self._provider_coverage(r) for r in b["incremental_run_ids"]]
                providers[p]["unfinished_newer_runs"] = b["unfinished_run_ids"]
        core = [basis["CROSSREF"]["state"], basis["OPENALEX"]["state"]]
        if all(s == "COMPLETE" for s in core) and all(s == "COMPLETE" for b in basis.values() for s in b["incremental_states"]) \
                and basis["OPENALEX_ALTERNATE"]["state"] in ("COMPLETE", "NOT_RUN"):
            state = "COMPLETE_RELATIVE_TO_RECORDED_QUERIES"
        elif all(s in ("BLOCKED", "NOT_RUN") for s in core):
            state = "BLOCKED"
        else:
            state = "PARTIAL"
        cat = {}
        for m in members:
            cat[m["category"]] = cat.get(m["category"], 0) + 1
        conflict_kinds = {}
        for c in conflicts:
            conflict_kinds[c["kind"]] = conflict_kinds.get(c["kind"], 0) + 1
        type_counts = {}
        for m in members:
            if m["membership_asserted"]:
                t = m["projection"].get("work_type") or (("openalex:" + m["projection"]["openalex_type"]) if m["projection"].get("openalex_type")
                                                         else "UNKNOWN_MALFORMED_OR_UNTYPED")
                type_counts[t] = type_counts.get(t, 0) + 1
        integrity = {}
        for m in members:
            for t in m["projection"].get("integrity_event_types", []):
                integrity[t] = integrity.get(t, 0) + 1
        open_tasks = self.kb.one("SELECT count(*) n FROM jh_review_task WHERE journal_id=?", (journal["journal_id"],))["n"]
        owner = [{"value": r["reported_count"], "observed_at": r["observed_at"], "note": r["note"],
                  "scope": "SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION"}
                 for r in self.kb.all("SELECT * FROM jh_count_observation WHERE journal_id=? AND provider='OWNER_REPORTED' ORDER BY rowid",
                                      (journal["journal_id"],))]
        asserted_doi = [m for m in members if m["subject_kind"] == "DOI" and m["membership_asserted"]]
        return {
            "schema": "jh-coverage/1", "journal_id": journal["journal_id"], "label": journal["label"],
            "requested_issns": json.loads(journal["requested_issns_json"]), "computed_at": as_of_text, "policy": POLICY,
            "providers": providers, "owner_reported_counts": owner,
            "reconciliation": {
                "union_unique_dois_with_membership_evidence": len(asserted_doi),
                "both_providers": cat.get("BOTH", 0), "crossref_only": cat.get("CROSSREF_ONLY", 0), "openalex_only": cat.get("OPENALEX_ONLY", 0),
                "set_differences_reliable": basis["CROSSREF"]["state"] == "COMPLETE" and basis["OPENALEX"]["state"] == "COMPLETE",
                "members_flagged_set_difference_unreliable": sum(1 for m in members if any(f.endswith("_SET_DIFFERENCE_UNRELIABLE")
                                                                                          for f in m["flags"])),
                "openalex_alternate_location_only_not_members": cat.get("OPENALEX_ALTERNATE_LOCATION_ONLY", 0),
                "not_seen_in_latest_runs_retained": cat.get("NOT_SEEN_IN_LATEST_RUNS", 0),
                "records_without_usable_doi": {k: cat.get(k, 0) for k in ("NO_DOI", "DOI_AMBIGUOUS", "DOI_INVALID")},
                "crossref_unique_dois": sum(1 for m in members if m["subject_kind"] == "DOI" and "CROSSREF" in m["providers"]),
                "crossref_rows_fetched_all_issn_queries": (providers["CROSSREF"] or {}).get("counters", {}).get("items_returned") if providers["CROSSREF"] else None,
                "crossref_dois_by_issn_query_set": self._issn_sets(members),
                "crossref_issn_queries_disagree": sum(1 for m in members if "CROSSREF_ISSN_QUERIES_DISAGREE" in m["flags"]),
                "openalex_unique_dois": sum(1 for m in members if m["subject_kind"] == "DOI" and "OPENALEX" in m["providers"]),
                "duplicate_provider_records_same_doi": sum(1 for m in members if "DUPLICATE_PROVIDER_RECORDS_SAME_DOI" in m["flags"]),
                "near_duplicate_doi_keys": sum(1 for m in members if "DOI_NEAR_DUPLICATE_TRAILING_SPACE" in m["flags"]
                                               or "DOI_UNICODE_NORMALIZATION_VARIANT" in m["flags"]),
                "field_conflicts_by_kind": dict(sorted(conflict_kinds.items())),
                "members_with_field_conflicts": sum(1 for m in members if set(m["flags"]) & {"TITLE_CONFLICT", "DATE_CONFLICT", "WORK_TYPE_CONFLICT",
                                                                                            "VENUE_MISMATCH", "ISSN_NOT_AMONG_JOURNAL_ISSNS"}),
                "work_types_of_members": dict(sorted(type_counts.items())),
                "integrity_signal_members_by_type": dict(sorted(integrity.items())),
                "integrity_signals_not_in_latest_snapshot": sum(1 for m in members if "INTEGRITY_SIGNAL_NOT_IN_LATEST_SNAPSHOT" in m["flags"]),
                "metadata_changed_across_runs": sum(1 for m in members if "METADATA_CHANGED_ACROSS_RUNS" in m["flags"]),
                "oa_reported_members": sum(1 for m in asserted_doi if m["projection"].get("is_oa") is True),
                "oa_dry_run_candidates": sum(1 for m in members if m["projection"]["oa_dry_run"]["status"] == "DRY_RUN_CANDIDATE"),
                "pdf_download_success": "NOT_APPLICABLE_NO_DOWNLOADS_IN_S04J",
                "existing_library_paper_candidates": sum(1 for m in members if m["paper_candidates"]),
                "review_tasks_total_for_journal_before_this_version": open_tasks,
            },
            "crossref_issn_queries": self._issn_query_table(providers["CROSSREF"]),
            "collection_state": state,
            "crawl_complete": state == "COMPLETE_RELATIVE_TO_RECORDED_QUERIES",
            "completion_statement": ("All provider runs in the basis reached their documented pagination termination under the recorded queries."
                                     if state.startswith("COMPLETE") else
                                     "At least one provider run in the basis did not reach its documented termination; see providers.*.state."),
            "limitations": LIMITATIONS, "scope_statement": STATEMENT,
        }


__all__ = ["Reconciler", "POLICY", "TYPE_COMPAT", "STATEMENT"]
