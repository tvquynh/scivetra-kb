"""Orchestration: parse -> route (doiRA) -> fetch (Crossref/DataCite/CN) -> persist; refresh.

Every network-shaped action goes through ``Executor`` (budget journal, bounded
retries) and ``GuardedTransport`` (policy). The transport is injected; S02 has
no live transport. Persistence for one item is a single transaction executed
after the request completes, so request reservations are committed before I/O.
"""
from __future__ import annotations

from datetime import timedelta

from .. import __version__
from ..core.canonical import canonical_json, sha256_bytes
from ..core.ids import parse_utc
from . import cn, crossref, datacite, ra
from .doi import (AMBIGUOUS, DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT, STRUCTURED_PROVIDER_FIELD, VALID, DoiParse,
                  parse_doi)
from .integrity import IntegrityService
from .store import MetadataStore, zs_rstrip
from .transport import Executor, GuardedTransport, Request, RunBudget, RunStopped

DEFAULTS = dict(max_requests=5000, max_bytes=200 * 1024 * 1024, max_response_bytes=5 * 1024 * 1024)
USER_AGENT = f"rkb/{__version__} (SciVetra KB local development)"
TRAILING_FLAGS = ("TRAILING_PUNCTUATION_REVIEW", "TRAILING_SLASH", "TRAILING_SPACE_IN_SUFFIX")


def snapshot_outcome(execution, parsed_status: str | None = None, parsed_reason: str | None = None) -> tuple[str, str | None]:
    if execution.outcome == "HTTP_OK":
        if parsed_status == "FOUND":
            return "FOUND", None
        if parsed_status == "NO_METADATA":
            return "NOT_FOUND", parsed_reason
        return "REFUSED_UNSAFE_RESPONSE", parsed_reason
    return {"HTTP_NOT_FOUND": ("NOT_FOUND", None), "RATE_LIMITED": ("RATE_LIMITED", execution.reason),
            "TRANSIENT_ERROR": ("TRANSIENT_ERROR", execution.reason), "REFUSED": ("REFUSED_UNSAFE_RESPONSE", execution.reason),
            "HTTP_OTHER": ("PERMANENT_ERROR", execution.reason)}[execution.outcome]


class MetadataService:
    def __init__(self, kb, transport, *, mode: str = "FIXTURE", command: str = "metadata", contact_email: str | None = None,
                 allow_cn_probe: bool = False, sleeper=None, jitter=None, **limits):
        self.kb = kb
        self.store = MetadataStore(kb)
        self.integrity = IntegrityService(self.store)
        self.contact_email = contact_email
        self.allow_cn_probe = allow_cn_probe
        params = {**DEFAULTS, **limits}
        self.run_id = kb.ids.new("run")
        with kb.transaction():
            kb.insert("fetch_run", dict(run_id=self.run_id, started_at_utc=kb.now(), mode=mode, command=command,
                                        max_requests=params["max_requests"], max_bytes=params["max_bytes"],
                                        max_response_bytes=params["max_response_bytes"], client_version=__version__))
        self.budget = RunBudget(kb, self.run_id, **params)
        kwargs = {}
        if sleeper is not None:
            kwargs["sleeper"] = sleeper
        if jitter is not None:
            kwargs["jitter"] = jitter
        self.executor = Executor(GuardedTransport(transport, max_response_bytes=params["max_response_bytes"]), self.budget, **kwargs)

    # -- input -------------------------------------------------------------------
    @staticmethod
    def parse_inputs(values: list[str], trust: str = OPERATOR_OR_TABULAR_INPUT) -> list[DoiParse]:
        return [parse_doi(v, DOI_IDENTIFIER_FIELD, trust) for v in values]

    def _refuse(self, parse: DoiParse) -> dict:
        result = {"input": parse.value_original, "status": "REFUSED", "parse": parse.as_dict(),
                  "reason": "AMBIGUOUS_DOI_NO_AUTOMATIC_ACTION" if parse.status == AMBIGUOUS else "INVALID_DOI", "requests": 0}
        if parse.status == AMBIGUOUS and parse.reason == "TRAILING_SPACE_UNCERTAIN":
            with self.kb.transaction():
                exact = parse.candidates[0]
                self.store.add_task(subject_namespace="doi", subject_value=exact.get("comparison_key") or exact.get("raw") or parse.value_original,
                                    trigger="DOI_TRAILING_SPACE_UNCERTAIN", event_ids=[], coverage=None,
                                    context={"value_original": parse.value_original,
                                             "candidates": [c.get("comparison_key") for c in parse.candidates]})
        return result

    # -- routing -----------------------------------------------------------------
    def _ra_fresh(self, claim) -> bool:
        if not claim:
            return False
        ttl = ra.TTL_FOUND_DAYS if claim["ra"] in ("CROSSREF", "DATACITE", "OTHER") else ra.TTL_NEGATIVE_DAYS
        return self.kb.clock.now() - parse_utc(claim["retrieved_at_utc"]) < timedelta(days=ttl)

    def route(self, parses: list[DoiParse]) -> list[dict]:
        results, pending = [], []
        for parse in parses:
            if not parse.automatic_action_allowed:
                results.append(self._refuse(parse))
                continue
            cached = self.store.latest_ra_claim(parse.comparison_key)
            if self._ra_fresh(cached):
                results.append({"input": parse.value_original, "doi_key": parse.comparison_key, "status": "ROUTED", "ra": cached["ra"],
                                "ra_raw": cached["ra_raw"], "cached": True, "parse": parse.as_dict()})
            else:
                pending.append(parse)
        for start in range(0, len(pending), ra.MAX_BATCH):
            batch = pending[start:start + ra.MAX_BATCH]
            results.extend(self._route_batch(batch))
        return results

    def _route_batch(self, batch: list[DoiParse]) -> list[dict]:
        keys = [p.comparison_key for p in batch]
        request = Request("GET", ra.build_url([p.doi for p in batch]), "DOI_RA", "ra_lookup", {"User-Agent": USER_AGENT})
        try:
            execution = self.executor.execute(request)
        except RunStopped:
            return [{"input": p.value_original, "doi_key": p.comparison_key, "status": "RUN_STOPPED"} for p in batch]
        if execution.outcome in ("BUDGET_EXHAUSTED", "PROVIDER_PAUSED"):
            return [{"input": p.value_original, "doi_key": p.comparison_key, "status": execution.outcome} for p in batch]
        parsed = ra.parse_response(execution.response.body, [(p.doi, p.comparison_key) for p in batch]) if execution.outcome == "HTTP_OK" else None
        outcome, reason = snapshot_outcome(execution, parsed and parsed["status"], parsed and parsed["reason"])
        out = []
        with self.kb.transaction():
            snapshot, _ = self.store.record_snapshot(
                provider="DOI_RA", request_identity=ra.request_identity(keys), http_status=execution.response.status if execution.response else None,
                body=execution.response.body if execution.response else None,
                media_type=execution.response.header("content-type") if execution.response else None,
                outcome=outcome, reason=reason, parser_name=ra.PARSER_NAME, parser_version=ra.PARSER_VERSION, run_id=self.run_id)
            items = {i["key"]: i for i in parsed["items"]} if parsed and parsed["status"] == "FOUND" else {}
            for p in batch:
                item = items.get(p.comparison_key)
                if item:
                    self.store.add_ra_claim(snapshot, item)
                    out.append({"input": p.value_original, "doi_key": p.comparison_key, "status": "ROUTED", "ra": item["ra"],
                                "ra_raw": item["ra_raw"], "status_raw": item["status_raw"], "cached": False, "parse": p.as_dict()})
                else:
                    out.append({"input": p.value_original, "doi_key": p.comparison_key, "status": "RA_LOOKUP_FAILED",
                                "snapshot_outcome": outcome, "reason": reason})
        return out

    # -- fetching -----------------------------------------------------------------
    def fetch_paper(self, paper_id: str) -> dict:
        """Fetch by KB paper. Papers without exactly one ACTIVE, VALID DOI make no request (MF-21)."""
        rows = self.kb.all("SELECT value_original, value_normalized, status, parse_result_json FROM identity_assertion "
                           "WHERE paper_id=? AND namespace='doi'", (paper_id,))
        active = sorted({r["value_normalized"] for r in rows if r["status"] == "ACTIVE" and r["value_normalized"]})
        if not active:
            return {"paper_id": paper_id, "status": "NO_DOI", "requests": 0}
        if len(active) > 1 or any(r["status"] == "CONFLICTING" for r in rows):
            return {"paper_id": paper_id, "status": "DOI_CONFLICTING_NO_AUTOMATIC_ACTION", "requests": 0}
        original = next(r["value_original"] for r in rows if r["value_normalized"] == active[0])
        return dict(self.fetch([parse_doi(original, DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)])[0], paper_id=paper_id)

    def fetch(self, parses: list[DoiParse]) -> list[dict]:
        routed = self.route(parses)
        by_key = {p.comparison_key: p for p in parses if p.automatic_action_allowed}
        results = []
        for route in routed:
            if route["status"] != "ROUTED":
                results.append(route)
                continue
            parse = by_key[route["doi_key"]]
            decision = ra.route_decision(route["ra"], route.get("ra_raw"), allow_cn_probe=self.allow_cn_probe)
            try:
                if decision == "CROSSREF":
                    results.append(self.fetch_provider("CROSSREF", parse, ra_said=route["ra"]))
                elif decision == "DATACITE":
                    results.append(self.fetch_provider("DATACITE", parse, ra_said=route["ra"]))
                elif decision == "CONTENT_NEGOTIATION":
                    results.append(self.fetch_cn(parse))
                else:
                    results.append({"doi_key": parse.comparison_key, "status": "UNROUTED" if decision == "UNROUTED" else "NOT_REGISTERED",
                                    "ra": route["ra"], "ra_raw": route.get("ra_raw"), "requests": 0})
            except RunStopped:
                results.append({"doi_key": parse.comparison_key, "status": "RUN_STOPPED", "requests": 0})
        return results

    def fetch_provider(self, provider: str, parse: DoiParse, *, ra_said: str | None = None) -> dict:
        module = crossref if provider == "CROSSREF" else datacite
        key, doi = parse.comparison_key, parse.doi
        url = crossref.build_url(doi, self.contact_email) if provider == "CROSSREF" else datacite.build_url(doi)
        headers = {"User-Agent": USER_AGENT + (f"; mailto:{self.contact_email}" if self.contact_email else "")}
        execution = self.executor.execute(Request("GET", url, provider, "metadata", headers))
        if execution.outcome in ("BUDGET_EXHAUSTED", "PROVIDER_PAUSED"):
            return {"doi_key": key, "provider": provider, "status": execution.outcome, "requests": execution.attempts}
        parsed = module.parse_response(execution.response.body, doi, key) if execution.outcome == "HTTP_OK" else None
        outcome, reason = snapshot_outcome(execution, parsed and parsed["status"], parsed and parsed["reason"])
        identity = module.request_identity(doi, key)
        identity_sha = sha256_bytes(canonical_json(identity).encode("utf-8"))
        field_path = parsed.get("field_path") if parsed else None
        result = {"doi_key": key, "provider": provider, "status": outcome, "reason": reason, "requests": execution.attempts, "flags": []}
        if field_path:
            result["field_path"] = field_path
        with self.kb.transaction():
            snapshot, created = self.store.record_snapshot(
                provider=provider, request_identity=identity, http_status=execution.response.status if execution.response else None,
                body=execution.response.body if execution.response else None,
                media_type=execution.response.header("content-type") if execution.response else None,
                outcome=outcome, reason=reason, parser_name=module.PARSER_NAME, parser_version=module.PARSER_VERSION, run_id=self.run_id)
            result.update(snapshot_id=snapshot["snapshot_id"], new_snapshot=created)
            if outcome == "FOUND":
                result["claims_created"] = self.store.add_claims(snapshot, "doi", key, parsed["claims"])
                if created and snapshot["supersedes_snapshot_id"]:
                    prior = self._latest_found_before(provider, identity_sha, snapshot["snapshot_id"])
                    if prior:
                        change = self.store.record_change_event(provider, identity_sha, prior, snapshot)
                        result["metadata_change"] = bool(change)
                recorded = self.integrity.record_provider_events(snapshot, parsed["events"])
                present = [e["event_id"] for e, _ in recorded if e["subject_value"] == key]
                result["events"] = [e["event_id"] for e, _ in recorded]
                if provider == "CROSSREF":
                    scope = {"event_types": crossref.EVENT_TYPES_COVERED,
                             "crossref_rw_included": "YES" if parsed.get("rw_seen") else "RW_ENTRY_NOT_SEEN", "record": "crossref_work"}
                    cov, _ = self.integrity.provider_record_coverage(provider="CROSSREF", subject_key=key, snapshot=snapshot,
                                                                     request_identity_sha256=identity_sha, found=True,
                                                                     present_event_ids=present, scope_note=scope)
                    result["coverage"] = [cov["outcome"]]
                else:
                    notices = {"event_types": datacite.NOTICE_TYPES, "crossref_rw_included": "NOT_APPLICABLE", "record": "datacite_doi"}
                    versions = {"event_types": datacite.VERSION_TYPES, "crossref_rw_included": "NOT_APPLICABLE", "record": "datacite_doi"}
                    c1, _ = self.integrity.add_coverage(subject_namespace="doi", subject_value=key, provider="DATACITE", snapshot=snapshot,
                                                        outcome="NOT_SUPPORTED_BY_PROVIDER", scope_note=notices)
                    c2, _ = self.integrity.provider_record_coverage(provider="DATACITE", subject_key=key, snapshot=snapshot,
                                                                    request_identity_sha256=identity_sha, found=True,
                                                                    present_event_ids=present, scope_note=versions)
                    result["coverage"] = [c1["outcome"], c2["outcome"]]
            else:
                scope = ({"event_types": crossref.EVENT_TYPES_COVERED, "crossref_rw_included": "RW_ENTRY_NOT_SEEN", "record": "crossref_work"}
                         if provider == "CROSSREF" else
                         {"event_types": datacite.VERSION_TYPES, "crossref_rw_included": "NOT_APPLICABLE", "record": "datacite_doi"})
                failure = "SUBJECT_NOT_FOUND" if outcome == "NOT_FOUND" else "CHECK_FAILED"
                cov, _ = self.integrity.provider_record_coverage(provider=provider, subject_key=key, snapshot=snapshot,
                                                                 request_identity_sha256=identity_sha, found=False, present_event_ids=[],
                                                                 scope_note=dict(scope, failure=outcome, reason=reason,
                                                                                 **({"field_path": field_path} if field_path else {})),
                                                                 failure_outcome=failure)
                result["coverage"] = [cov["outcome"]]
                if outcome == "NOT_FOUND":
                    if ra_said == provider:
                        result["flags"].append("RA_PROVIDER_DISAGREE")
                    trailing = [f for f in parse.flags if f in TRAILING_FLAGS]
                    if trailing:
                        alternative = zs_rstrip(doi).rstrip(".,;:)]}'\"/") or None
                        self.store.add_task(subject_namespace="doi", subject_value=key, trigger="DOI_NOT_FOUND_TRAILING_CHARACTERS",
                                            event_ids=[], coverage=cov,
                                            context={"flags": trailing, "trimmed_alternative_for_human_review": alternative,
                                                     "automatic_lookup_of_alternative": False})
                        result["flags"].append("REVIEW_TASK_TRAILING_CHARACTERS")
        return result

    def _latest_found_before(self, provider: str, identity_sha: str, snapshot_id: str):
        return self.kb.one("SELECT * FROM provider_snapshot WHERE provider=? AND request_identity_sha256=? AND outcome='FOUND' "
                           "AND snapshot_id<>? AND rowid < (SELECT rowid FROM provider_snapshot WHERE snapshot_id=?) ORDER BY rowid DESC LIMIT 1",
                           (provider, identity_sha, snapshot_id, snapshot_id))

    def fetch_cn(self, parse: DoiParse) -> dict:
        request = Request("GET", cn.build_url(parse.doi), "DOI_CONTENT_NEGOTIATION", "metadata", {"Accept": cn.ACCEPT, "User-Agent": USER_AGENT})
        execution = self.executor.execute(request)
        if execution.outcome in ("BUDGET_EXHAUSTED", "PROVIDER_PAUSED"):
            return {"doi_key": parse.comparison_key, "provider": "DOI_CONTENT_NEGOTIATION", "status": execution.outcome}
        parsed = None
        if execution.response is not None and execution.outcome in ("HTTP_OK",):
            parsed = cn.parse_response(execution.response.status, execution.response.header("content-type"), execution.response.body)
        outcome, reason = snapshot_outcome(execution, parsed and parsed["status"], parsed and parsed["reason"])
        with self.kb.transaction():
            snapshot, _ = self.store.record_snapshot(
                provider="DOI_CONTENT_NEGOTIATION", request_identity=cn.request_identity(parse.doi, parse.comparison_key),
                http_status=execution.response.status if execution.response else None,
                body=execution.response.body if execution.response and execution.response.body else None,
                media_type=execution.response.header("content-type") if execution.response else None, outcome=outcome, reason=reason,
                parser_name=cn.PARSER_NAME, parser_version=cn.PARSER_VERSION, run_id=self.run_id)
            if outcome == "FOUND":
                self.store.add_claims(snapshot, "doi", parse.comparison_key, parsed["claims"])
        status = "NO_METADATA" if outcome in ("NOT_FOUND", "REFUSED_UNSAFE_RESPONSE") else outcome
        result = {"doi_key": parse.comparison_key, "provider": "DOI_CONTENT_NEGOTIATION", "status": status,
                  "snapshot_outcome": outcome, "reason": reason, "snapshot_id": snapshot["snapshot_id"]}
        if parsed and parsed.get("field_path"):
            result["field_path"] = parsed["field_path"]
        return result

    # -- refresh ----------------------------------------------------------------
    def refresh(self, *, older_than_days: int = 90, integrity_only: bool = False) -> dict:
        import json
        providers = ("CROSSREF",) if integrity_only else ("CROSSREF", "DATACITE")
        threshold = self.kb.clock.now() - timedelta(days=older_than_days)
        rows = self.kb.all("SELECT provider, request_identity_json, max(rowid) r FROM provider_snapshot WHERE provider IN (%s) "
                           "GROUP BY provider, request_identity_sha256" % ",".join("?" for _ in providers), providers)
        report = {"checked": 0, "unchanged": 0, "new_snapshot": 0, "changed": 0, "errors": 0, "skipped_recent": 0, "items": []}
        for row in rows:
            latest = self.kb.one("SELECT * FROM provider_snapshot WHERE rowid=?", (row["r"],))
            # Staleness = time of the most recent check of this request identity (UNCHANGED re-checks reuse old snapshots).
            last_check = self.kb.one("SELECT max(c.checked_at_utc) t FROM provider_check c JOIN provider_snapshot s ON s.snapshot_id=c.snapshot_id "
                                     "WHERE s.provider=? AND s.request_identity_sha256=?", (latest["provider"], latest["request_identity_sha256"]))["t"]
            if parse_utc(last_check or latest["retrieved_at_utc"]) > threshold:
                report["skipped_recent"] += 1
                continue
            identity = json.loads(row["request_identity_json"])
            parse = parse_doi(identity["doi"], DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
            if parse.status != VALID or parse.comparison_key != identity["doi_key"]:
                report["errors"] += 1
                continue
            try:
                result = self.fetch_provider(row["provider"], parse)
            except RunStopped:
                report["items"].append({"doi_key": identity["doi_key"], "status": "RUN_STOPPED"})
                break
            report["checked"] += 1
            if result.get("new_snapshot"):
                report["new_snapshot"] += 1
            elif "snapshot_id" in result:
                report["unchanged"] += 1
            if result.get("metadata_change"):
                report["changed"] += 1
            if result["status"] not in ("FOUND", "NOT_FOUND"):
                report["errors"] += 1
            report["items"].append(result)
        return report
