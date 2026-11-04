"""Journal identity resolution and checkpointed provider crawls (S04J steps 1-3).

Run model (all rows append-only, migration 0004):

* ``jh_run`` + ``jh_run_event``: a run is COMPLETE only when its FINISHED event says so, and only after the provider's
  documented termination condition was observed (Crossref: a page with fewer than ``rows`` items; OpenAlex: no
  ``next_cursor`` or an empty page). Any failed page, cap, cancellation, refusal, missing cursor or unfinished run is
  PARTIAL; a missing/rejected credential, an unresolved journal/source or HTTP 401/403/404 is BLOCKED.
* Each provider page is committed in ONE transaction together with its observations and field claims (the checkpoint).
  A crash loses at most the page in flight. ``resume`` continues from the last committed page's ``next-cursor``; if the
  provider rejects that cursor (HTTP 400), the run replays the same query from ``cursor=*`` in a new pass (bounded by
  ``max_replays``) and de-duplicates by content, so committed work is never lost or duplicated. Persisted cursors are
  never assumed to stay valid.
* Requests go through the accepted S02 ``Executor`` + ``RunBudget`` (bounded attempts, exponential backoff with jitter,
  Retry-After honoured, 3x429 pauses the provider, request journal in ``request_reservation``/``request_completion``)
  behind ``GuardedTransport`` limited to the two provider hosts.
* ``total-results`` / ``meta.count`` are stored as timestamped count observations; a change within a run is a
  COUNT_DRIFT event. No count is ever used as a completeness criterion.
"""
from __future__ import annotations

import json
import random
import time

from .. import __version__
from ..core.canonical import canonical_json, semantic_sha256, sha256_bytes
from ..core.errors import ValidationError
from ..library.common import operator_label
from ..metadata.transport import Executor, RunBudget
from . import providers as pv
from .access import Credentials, guarded, make_request
from .issn import VALID as ISSN_VALID, parse_issn

CRAWL_PROVIDERS = ("CROSSREF", "OPENALEX", "OPENALEX_ALTERNATE")
DEFAULT_CAPS = {"max_pages": 5000, "max_requests": 20000, "max_bytes": 64 * 1024 ** 3, "max_response_bytes": 32 * 1024 * 1024,
                "max_replays": 2}
ISSN_ROLE = {"print": "PRINT", "electronic": "ELECTRONIC", "linking": "LINKING"}
NOT_A_STATE_OF_MEMBERSHIP = "Journal inventory evidence only; never an SLR inclusion, screening decision or download entitlement."


def _fault(faults, name, **ctx):
    if faults and name in faults:
        faults[name](**ctx)


class JournalHarvester:
    def __init__(self, kb, transport, *, operator: str, transport_mode: str = "SIMULATED", credentials: Credentials | None = None,
                 sleeper=time.sleep, jitter=lambda: random.uniform(0, 0.5), faults=None, cancel=None):
        if transport_mode not in ("SIMULATED", "LIVE"):
            raise ValidationError("transport_mode must be SIMULATED or LIVE", code="TRANSPORT_MODE_INVALID")
        self.kb = kb
        self.transport = guarded(transport)
        self.operator = operator_label(operator)
        self.transport_mode = transport_mode
        self.credentials = credentials or Credentials()
        self.sleeper, self.jitter = sleeper, jitter
        self.faults, self.cancel = faults, cancel
        self.last_executor = None
        self.current_run_id = None

    # -- journal registration ----------------------------------------------------------------------------------------
    def register_journal(self, label: str, issns: list[str], *, work_type_filter: list[str] | None = None) -> dict:
        parsed = [parse_issn(i) for i in issns]
        bad = [p for p in parsed if p["status"] != ISSN_VALID]
        if not parsed or bad:
            raise ValidationError("Invalid ISSN: " + ", ".join(f"{p['input']!r} ({p['reason']})" for p in bad) if bad else "No ISSN given",
                                  code="INVALID_ISSN")
        requested = sorted({p["issn"] for p in parsed})
        key = semantic_sha256({"issns": requested})
        existing = self.kb.one("SELECT * FROM jh_journal WHERE collection_key=?", (key,))
        if existing:
            return {"journal_id": existing["journal_id"], "created": False, "requested_issns": requested}
        policy = {"inventory": "ALL_WORK_TYPES_RETAINED", "default_export_type_filter": sorted(work_type_filter) if work_type_filter else None}
        journal_id = self.kb.ids.new("jj")
        now = self.kb.now()
        with self.kb.transaction():
            self.kb.insert("jh_journal", dict(journal_id=journal_id, label=label, requested_issns_json=canonical_json(requested),
                                              collection_key=key, work_type_policy_json=canonical_json(policy), created_by=self.operator,
                                              created_at_utc=now))
            for issn in requested:
                self.kb.insert("jh_issn_evidence", dict(
                    evidence_id=self.kb.ids.new("jie"), journal_id=journal_id, issn=issn, issn_role="REQUESTED", provider="OPERATOR",
                    page_id=None, source_field_path=None, provider_source_id=None, created_at_utc=now,
                    semantic_sha256=semantic_sha256({"journal": journal_id, "issn": issn, "role": "REQUESTED", "provider": "OPERATOR"})))
        return {"journal_id": journal_id, "created": True, "requested_issns": requested}

    def record_owner_count(self, journal_id: str, *, count: int, observed_on: str, note: str) -> dict:
        """A count someone observed earlier (e.g. the owner's Crossref ``rows=0`` check). Stored as history, never as an expectation."""
        self._journal(journal_id)
        row = dict(count_id=self.kb.ids.new("jhc"), journal_id=journal_id, run_id=None, page_id=None, provider="OWNER_REPORTED",
                   basis="OWNER_HISTORICAL_OBSERVATION", reported_count=int(count), observed_at=observed_on,
                   scope="SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION", note=note, recorded_at_utc=self.kb.now())
        row["semantic_sha256"] = semantic_sha256({k: row[k] for k in ("journal_id", "provider", "basis", "reported_count", "observed_at", "note")})
        with self.kb.transaction():
            stored, created = self.kb.insert_unique("jh_count_observation", row)
        return {"count_id": stored["count_id"], "created": created}

    def _journal(self, journal_id: str) -> dict:
        row = self.kb.one("SELECT * FROM jh_journal WHERE journal_id=?", (journal_id,))
        if not row:
            raise ValidationError("Unknown journal", code="UNKNOWN_JOURNAL")
        return row

    # -- run plumbing ------------------------------------------------------------------------------------------------
    def _fetch_run(self, command: str, caps: dict) -> str:
        fetch_run_id = self.kb.ids.new("run")
        with self.kb.transaction():
            self.kb.insert("fetch_run", dict(run_id=fetch_run_id, started_at_utc=self.kb.now(),
                                             mode="FIXTURE" if self.transport_mode == "SIMULATED" else "LIVE", command=command,
                                             max_requests=caps["max_requests"], max_bytes=caps["max_bytes"],
                                             max_response_bytes=caps["max_response_bytes"], client_version=__version__))
        return fetch_run_id

    def _start_run(self, journal_id: str, provider: str, mode: str, query: dict, caps: dict) -> dict:
        adapter = pv.CROSSREF_ADAPTER if provider.startswith("CROSSREF") else pv.OPENALEX_ADAPTER
        fetch_run_id = self._fetch_run(f"journal {provider.lower()} {mode.lower()}", caps)
        run = dict(run_id=self.kb.ids.new("jhr"), journal_id=journal_id, provider=provider, mode=mode, fetch_run_id=fetch_run_id,
                   query_json=canonical_json(query), query_sha256=semantic_sha256(query), adapter_name=adapter[0], adapter_version=adapter[1],
                   transport_mode=self.transport_mode, caps_json=canonical_json(caps), created_by=self.operator,
                   started_at_utc=self.kb.now(), client_version=__version__)
        with self.kb.transaction():
            self.kb.insert("jh_run", run)
        self.current_run_id = run["run_id"]
        self._event(run["run_id"], "STARTED", detail={"credentials": self.credentials.summary()}, fetch_run_id=fetch_run_id)
        return run

    def _event(self, run_id: str, kind: str, *, state=None, stop_reason=None, detail=None, fetch_run_id=None) -> None:
        seq = (self.kb.one("SELECT max(seq) m FROM jh_run_event WHERE run_id=?", (run_id,))["m"] or 0) + 1
        with self.kb.transaction():
            self.kb.insert("jh_run_event", dict(event_id=self.kb.ids.new("jhe"), run_id=run_id, seq=seq, kind=kind, state=state,
                                                stop_reason=stop_reason, fetch_run_id=fetch_run_id, detail_json=canonical_json(detail or {}),
                                                created_at_utc=self.kb.now()))

    def _executor(self, fetch_run_id: str, caps: dict) -> Executor:
        budget = RunBudget(self.kb, fetch_run_id, max_requests=caps["max_requests"], max_bytes=caps["max_bytes"],
                           max_response_bytes=caps["max_response_bytes"])
        self.last_executor = Executor(self.transport, budget, sleeper=self.sleeper, jitter=self.jitter)
        return self.last_executor

    def _next_seq(self, run_id: str) -> int:
        return (self.kb.one("SELECT max(seq) m FROM jh_page WHERE run_id=?", (run_id,))["m"] or 0) + 1

    def _page(self, run_id: str, *, kind: str, pass_no: int, identity: dict, cursor_in, result, outcome: str, reason=None,
              body: bytes | None = None, items_returned=None, items_new=None, cursor_out=None, in_transaction=False) -> dict:
        response = result.response if result is not None else None
        digest = None
        if body is None and response is not None:
            body = response.body
        if body is not None:
            digest = self.kb.cas.put(body)
        row = dict(page_id=self.kb.ids.new("jhp"), run_id=run_id, seq=self._next_seq(run_id), pass_no=pass_no, kind=kind,
                   request_identity_json=canonical_json(identity), request_identity_sha256=semantic_sha256(identity), cursor_in=cursor_in,
                   cursor_out=cursor_out, http_status=response.status if response is not None else None,
                   attempts=result.attempts if result is not None else 0, response_bytes_sha256=digest,
                   response_media_type=response.header("content-type") if response is not None else None, items_returned=items_returned,
                   items_new=items_new, outcome=outcome, outcome_reason=reason, retrieved_at_utc=self.kb.now())
        if in_transaction:
            self.kb.insert("jh_page", row)
        else:
            with self.kb.transaction():
                self.kb.insert("jh_page", row)
        return row

    @staticmethod
    def _failure(result) -> tuple[str, str, str]:
        """Map an Executor result that is not a usable 2xx response to (page outcome, run state, stop reason)."""
        status = result.response.status if result.response is not None else None
        if status == 401:
            return "CREDENTIAL_REJECTED", "BLOCKED", "HTTP_401_CREDENTIAL_REJECTED"
        if status == 403:
            return "HTTP_ERROR", "BLOCKED", "HTTP_403_ACCESS_DENIED"
        if status == 404:
            return "NOT_FOUND", "BLOCKED", "HTTP_404_NOT_FOUND"
        if result.outcome == "RATE_LIMITED":
            return "RATE_LIMITED", "PARTIAL", "RATE_LIMITED_RETRIES_EXHAUSTED" if result.reason != "PROVIDER_PAUSED_AFTER_3x429" else "RATE_LIMITED_PROVIDER_PAUSED"
        if result.outcome == "PROVIDER_PAUSED":
            return "RATE_LIMITED", "PARTIAL", "RATE_LIMITED_PROVIDER_PAUSED"
        if result.outcome == "BUDGET_EXHAUSTED":
            return "BUDGET_EXHAUSTED", "PARTIAL", "REQUEST_BUDGET_EXHAUSTED"
        if result.outcome == "REFUSED":
            return "REFUSED", "PARTIAL", f"REFUSED_{result.reason}"
        if result.outcome == "TRANSIENT_ERROR":
            return ("HTTP_ERROR" if (result.reason or "").startswith("HTTP_") else "TRANSPORT_FAILED"), "PARTIAL", \
                f"TRANSIENT_ERROR_RETRIES_EXHAUSTED_{result.reason}"
        return "HTTP_ERROR", "PARTIAL", f"HTTP_{status}"

    # -- resolution --------------------------------------------------------------------------------------------------
    def resolve(self, journal_id: str, *, openalex_source_id: str | None = None) -> dict:
        """Resolve the requested ISSNs at Crossref (journal records, ISSN types) and OpenAlex (Source IDs) by exact ISSN."""
        journal = self._journal(journal_id)
        requested = json.loads(journal["requested_issns_json"])
        caps = dict(DEFAULT_CAPS)
        out = {"journal_id": journal_id, "requested_issns": requested}
        out["crossref"] = self._resolve_crossref(journal_id, requested, caps)
        out["openalex"] = self._resolve_openalex(journal_id, requested, caps, openalex_source_id)
        return out

    def _resolve_crossref(self, journal_id, requested, caps) -> dict:
        run = self._start_run(journal_id, "CROSSREF_JOURNAL_LOOKUP", "RESOLVE", {"endpoint": "/journals/{issn}", "issns": requested}, caps)
        executor = self._executor(run["fetch_run_id"], caps)
        records, failures = {}, {}
        for issn in requested:
            url, identity = pv.crossref_journal_request(issn)
            result = executor.execute(make_request(url, "CROSSREF", "journal-lookup"))
            if result.outcome != "HTTP_OK":
                outcome, _, reason = self._failure(result)
                self._page(run["run_id"], kind="JOURNAL_LOOKUP", pass_no=0, identity=identity, cursor_in=None, result=result, outcome=outcome, reason=reason)
                failures[issn] = reason
                continue
            try:
                record = pv.parse_crossref_journal(result.response.body)
            except pv.PageRefused as exc:
                self._page(run["run_id"], kind="JOURNAL_LOOKUP", pass_no=0, identity=identity, cursor_in=None, result=result,
                           outcome="UNPARSEABLE", reason=exc.reason)
                failures[issn] = exc.reason
                continue
            with self.kb.transaction():
                page = self._page(run["run_id"], kind="JOURNAL_LOOKUP", pass_no=0, identity=identity, cursor_in=None, result=result,
                                  outcome="OK", items_returned=1, items_new=1, in_transaction=True)
                listed = sorted({i["issn"] for i in record["typed_issns"]} | {i["issn"] for i in record["issns"]})
                for item in record["typed_issns"]:
                    self._issn_evidence(journal_id, item["issn"], ISSN_ROLE.get((item["type"] or "").lower(), "UNSPECIFIED"), "CROSSREF",
                                        page["page_id"], item["path"], issn)
                for item in record["issns"]:
                    if item["issn"] not in {t["issn"] for t in record["typed_issns"]}:
                        self._issn_evidence(journal_id, item["issn"], "UNSPECIFIED", "CROSSREF", page["page_id"], item["path"], issn)
                self._resolution(journal_id, run["run_id"], page["page_id"], "CROSSREF", issn, record["title"], None, listed,
                                 [issn] if issn in listed else [], "ISSN_EXACT_PROVIDER_EVIDENCE" if issn in listed else None, "SELECTED", "/message")
            records[issn] = {"title": record["title"], "publisher": record["publisher"], "issns": listed,
                             "typed": [{"issn": t["issn"], "type": t["type"]} for t in record["typed_issns"]]}
        conflicts = self._issn_conflicts(journal_id, requested, records)
        state = "COMPLETE" if not failures else ("PARTIAL" if records else "BLOCKED")
        self._finish_simple(run["run_id"], state, "ALL_LOOKUPS_ANSWERED" if not failures else "LOOKUP_FAILED", {"failures": failures})
        return {"run_id": run["run_id"], "state": state, "records": records, "failures": failures, "issn_conflicts": conflicts}

    def _issn_conflicts(self, journal_id, requested, records) -> list[dict]:
        conflicts = []
        for a in requested:
            for b in requested:
                if a < b and a in records and b in records:
                    if b not in records[a]["issns"] and a not in records[b]["issns"]:
                        conflicts.append({"kind": "REQUESTED_ISSNS_NOT_LINKED_BY_PROVIDER", "issns": [a, b]})
        roles = {}
        for rec in records.values():
            for t in rec["typed"]:
                roles.setdefault(t["issn"], set()).add((t["type"] or "").lower())
        for issn, kinds in sorted(roles.items()):
            if len(kinds) > 1:
                conflicts.append({"kind": "ISSN_TYPE_DISAGREES", "issn": issn, "types": sorted(kinds)})
        for conflict in conflicts:
            self.task(journal_id, None, subject="journal:" + journal_id, trigger="ISSN_RELATIONSHIP_CONFLICT", context=conflict)
        return conflicts

    def _resolve_openalex(self, journal_id, requested, caps, operator_choice) -> dict:
        run = self._start_run(journal_id, "OPENALEX_SOURCE_LOOKUP", "RESOLVE", {"endpoint": "/sources", "filter": "issn", "issns": requested}, caps)
        executor = self._executor(run["fetch_run_id"], caps)
        candidates, failures = {}, {}
        for issn in requested:
            url, identity = pv.openalex_sources_request(issn)
            result = executor.execute(make_request(url, "OPENALEX", "source-lookup"))
            if result.outcome != "HTTP_OK":
                outcome, _, reason = self._failure(result)
                self._page(run["run_id"], kind="SOURCE_LOOKUP", pass_no=0, identity=identity, cursor_in=None, result=result, outcome=outcome, reason=reason)
                failures[issn] = reason
                continue
            try:
                sources = pv.parse_openalex_sources(result.response.body)
            except pv.PageRefused as exc:
                self._page(run["run_id"], kind="SOURCE_LOOKUP", pass_no=0, identity=identity, cursor_in=None, result=result,
                           outcome="UNPARSEABLE", reason=exc.reason)
                failures[issn] = exc.reason
                continue
            page = self._page(run["run_id"], kind="SOURCE_LOOKUP", pass_no=0, identity=identity, cursor_in=None, result=result, outcome="OK",
                              items_returned=len(sources), items_new=len(sources))
            for source in sources:
                if not source["source_id"] or issn not in source["issns"] + ([source["issn_l"]] if source["issn_l"] else []):
                    continue        # a returned source that does not list the ISSN is not evidence of anything
                entry = candidates.setdefault(source["source_id"], dict(source, matched=set(), page_id=page["page_id"]))
                entry["matched"].add(issn)
        ids = sorted(candidates)
        if operator_choice is not None and operator_choice not in ids:
            raise ValidationError("Operator-selected OpenAlex source is not among the ISSN-matched candidates", code="SOURCE_NOT_A_CANDIDATE")
        selected = ids if len(ids) == 1 else ([operator_choice] if operator_choice else [])
        with self.kb.transaction():
            for sid in ids:
                c = candidates[sid]
                status = "SELECTED" if sid in selected else "CANDIDATE_AMBIGUOUS"
                basis = "OPERATOR_SELECTED_AMONG_CANDIDATES" if (len(ids) > 1 and sid in selected) else "ISSN_EXACT_PROVIDER_EVIDENCE"
                self._resolution(journal_id, run["run_id"], c["page_id"], "OPENALEX", sid, c["display_name"], c["issn_l"], c["issns"],
                                 sorted(c["matched"]), basis, status, c["path"])
                for issn in c["issns"]:
                    self._issn_evidence(journal_id, issn, "UNSPECIFIED", "OPENALEX", c["page_id"], c["path"] + "/issn", sid)
                if c["issn_l"]:
                    self._issn_evidence(journal_id, c["issn_l"], "LINKING", "OPENALEX", c["page_id"], c["path"] + "/issn_l", sid)
        if len(ids) > 1:
            self.task(journal_id, None, subject="journal:" + journal_id, trigger="SOURCE_AMBIGUOUS",
                      context={"provider": "OPENALEX", "candidates": ids, "operator_selected": operator_choice})
        state = "COMPLETE" if not failures else ("PARTIAL" if len(failures) < len(requested) else "BLOCKED")
        reason = "ALL_LOOKUPS_ANSWERED" if not failures else "LOOKUP_FAILED"
        self._finish_simple(run["run_id"], state, reason, {"failures": failures, "candidates": ids, "selected": selected})
        return {"run_id": run["run_id"], "state": state, "candidates": ids, "selected": selected, "failures": failures,
                "ambiguous": len(ids) > 1 and not operator_choice}

    def _issn_evidence(self, journal_id, issn, role, provider, page_id, path, source_id) -> None:
        self.kb.insert_unique("jh_issn_evidence", dict(
            evidence_id=self.kb.ids.new("jie"), journal_id=journal_id, issn=issn, issn_role=role, provider=provider, page_id=page_id,
            source_field_path=path, provider_source_id=source_id, created_at_utc=self.kb.now(),
            semantic_sha256=semantic_sha256({"journal": journal_id, "issn": issn, "role": role, "provider": provider, "page": page_id, "path": path})))

    def _resolution(self, journal_id, run_id, page_id, provider, source_id, name, issn_l, issns, matched, basis, status, path) -> None:
        if basis is None:
            return
        row = dict(resolution_id=self.kb.ids.new("jsr"), journal_id=journal_id, run_id=run_id, page_id=page_id, provider=provider,
                   provider_source_id=source_id, display_name=name, issn_l=issn_l, issns_json=canonical_json(issns),
                   matched_issns_json=canonical_json(matched), basis=basis, status=status, source_field_path=path, created_at_utc=self.kb.now())
        row["semantic_sha256"] = semantic_sha256({k: row[k] for k in ("journal_id", "run_id", "provider", "provider_source_id", "issns_json",
                                                                       "matched_issns_json", "basis", "status")})
        self.kb.insert_unique("jh_source_resolution", row)

    def selected_sources(self, journal_id: str, provider: str) -> dict:
        """Sources of the most recent resolution run for ``provider``: {"selected": [...], "candidates": [...]}."""
        lookup = "CROSSREF_JOURNAL_LOOKUP" if provider == "CROSSREF" else "OPENALEX_SOURCE_LOOKUP"
        run = self.kb.one("SELECT run_id FROM jh_run WHERE journal_id=? AND provider=? ORDER BY rowid DESC LIMIT 1", (journal_id, lookup))
        if not run:
            return {"selected": [], "candidates": [], "resolution_run_id": None}
        rows = self.kb.all("SELECT provider_source_id, status FROM jh_source_resolution WHERE run_id=? ORDER BY provider_source_id", (run["run_id"],))
        return {"selected": [r["provider_source_id"] for r in rows if r["status"] == "SELECTED"],
                "candidates": [r["provider_source_id"] for r in rows], "resolution_run_id": run["run_id"]}

    # -- crawls --------------------------------------------------------------------------------------------------------
    def crossref_issn_plan(self, journal_id: str) -> dict:
        """Deterministic Crossref query plan (S04J-F01): every requested ISSN that the latest Crossref journal lookup resolved,
        plus every ISSN that a resolved Crossref journal record itself lists (print/electronic siblings). Sorted. Requested ISSNs
        that did not resolve and are not listed by a resolved record are reported and keep the inventory from being COMPLETE."""
        journal = self._journal(journal_id)
        requested = json.loads(journal["requested_issns_json"])
        resolution = self.selected_sources(journal_id, "CROSSREF")
        basis: dict[str, list] = {}
        if resolution["resolution_run_id"]:
            for row in self.kb.all("SELECT provider_source_id, issns_json, page_id FROM jh_source_resolution WHERE run_id=? AND provider='CROSSREF' "
                                   "AND status='SELECTED' ORDER BY provider_source_id", (resolution["resolution_run_id"],)):
                basis.setdefault(row["provider_source_id"], []).append({"basis": "REQUESTED_AND_RESOLVED", "journal_record_of": row["provider_source_id"],
                                                                        "page_id": row["page_id"]})
                for issn in json.loads(row["issns_json"]):
                    if issn != row["provider_source_id"]:
                        basis.setdefault(issn, []).append({"basis": "LISTED_BY_RESOLVED_JOURNAL_RECORD", "journal_record_of": row["provider_source_id"],
                                                           "page_id": row["page_id"]})
        plan = [{"issn": issn, "evidence": basis[issn]} for issn in sorted(basis)]
        return {"plan": plan, "requested_not_resolved": sorted(set(requested) - set(basis)), "resolution_run_id": resolution["resolution_run_id"]}

    def harvest(self, journal_id: str, provider: str, *, mode: str = "FULL", since: str | None = None, page_size: int | None = None,
                caps: dict | None = None) -> dict:
        if provider not in CRAWL_PROVIDERS:
            raise ValidationError("provider must be CROSSREF, OPENALEX or OPENALEX_ALTERNATE", code="PROVIDER_INVALID")
        if mode not in ("FULL", "INCREMENTAL"):
            raise ValidationError("mode must be FULL or INCREMENTAL", code="MODE_INVALID")
        if (mode == "INCREMENTAL") != (since is not None):
            raise ValidationError("INCREMENTAL requires --since YYYY-MM-DD (and FULL forbids it)", code="MODE_INVALID")
        pv.check_since(since)
        self._journal(journal_id)
        caps = dict(DEFAULT_CAPS, **(caps or {}))
        if provider == "CROSSREF":
            size = page_size or pv.CROSSREF_MAX_ROWS
            if not 1 <= size <= pv.CROSSREF_MAX_ROWS:
                raise ValidationError("Crossref rows must be 1..1000", code="PAGE_SIZE_INVALID")
            plan = self.crossref_issn_plan(journal_id)
            query = {"endpoint": "/journals/{issn}/works", "issn_plan": plan["plan"], "issns_requested_not_resolved": plan["requested_not_resolved"],
                     "rows": size, "cursor_start": "*", "filter": f"from-update-date:{since}" if since else None,
                     "termination": "STOP_WHEN_ITEMS_RETURNED_LT_ROWS",
                     "inventory_complete_rule": "EVERY_PLANNED_ISSN_QUERY_TERMINATED_AND_NO_REQUESTED_ISSN_UNRESOLVED",
                     "resolution_run_id": plan["resolution_run_id"]}
            run = self._start_run(journal_id, provider, mode, query, caps)
            if not plan["plan"]:
                return self._finish(run, "BLOCKED", "CROSSREF_JOURNAL_NOT_RESOLVED")
            return self._crawl(run, fetch_run_id=run["fetch_run_id"])
        resolution = self.selected_sources(journal_id, "OPENALEX")
        size = page_size or pv.OPENALEX_MAX_PER_PAGE
        if not 1 <= size <= pv.OPENALEX_MAX_PER_PAGE:
            raise ValidationError("OpenAlex per-page must be 1..100", code="PAGE_SIZE_INVALID")
        source = resolution["selected"][0] if len(resolution["selected"]) == 1 else None
        query = {"endpoint": "/works", "source_id": source, "venue_filter": pv.VENUE_FILTERS[provider], "per_page": size,
                 "cursor_start": "*", "filter_since": f"from_updated_date:{since}" if since else None,
                 "termination": "STOP_WHEN_NEXT_CURSOR_NULL_OR_EMPTY_PAGE", "resolution_run_id": resolution["resolution_run_id"],
                 "source_candidates": resolution["candidates"]}
        run = self._start_run(journal_id, provider, mode, query, caps)
        if source is None:
            reason = "SOURCE_AMBIGUOUS_OPERATOR_SELECTION_REQUIRED" if len(resolution["candidates"]) > 1 else "OPENALEX_SOURCE_NOT_RESOLVED"
            return self._finish(run, "BLOCKED", reason)
        return self._crawl(run, fetch_run_id=run["fetch_run_id"])

    def resume(self, run_id: str) -> dict:
        run = self.kb.one("SELECT * FROM jh_run WHERE run_id=?", (run_id,))
        if not run:
            raise ValidationError("Unknown journal run", code="UNKNOWN_RUN")
        if run["provider"] not in CRAWL_PROVIDERS:
            raise ValidationError("Resolution runs are not resumed; run 'journal resolve' again", code="RUN_NOT_RESUMABLE")
        if self.kb.one("SELECT 1 x FROM jh_run_event WHERE run_id=? AND kind='FINISHED'", (run_id,)):
            raise ValidationError("Run already finished; start a new run instead", code="RUN_ALREADY_FINISHED")
        caps = json.loads(run["caps_json"])
        self.current_run_id = run_id
        fetch_run_id = self._fetch_run(f"journal {run['provider'].lower()} resume", caps)
        last = self.kb.one("SELECT seq FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' AND outcome='OK' ORDER BY seq DESC LIMIT 1", (run_id,))
        self._event(run_id, "RESUMED", fetch_run_id=fetch_run_id,
                    detail={"from_page_seq": last["seq"] if last else None, "credentials": self.credentials.summary()})
        return self._crawl(run, fetch_run_id=fetch_run_id, resuming=True)

    # Branch helpers: one branch per planned Crossref ISSN (``issn``), or the single OpenAlex query (``issn`` None).
    # Branch state lives in existing append-only rows: page request identities carry the ISSN; PAGE_FAILED / REPLAY_STARTED /
    # CURSOR_INVALID events carry it in their detail. Migration 0004 is unchanged.
    def _last_ok_page(self, run_id: str, issn=None):
        return self.kb.one("SELECT * FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' AND outcome='OK' "
                           "AND json_extract(request_identity_json,'$.issn') IS ? ORDER BY seq DESC LIMIT 1", (run_id, issn))

    def _branch_failure(self, run_id: str, issn):
        row = self.kb.one("SELECT detail_json FROM jh_run_event WHERE run_id=? AND kind='PAGE_FAILED' AND json_extract(detail_json,'$.issn') IS ? "
                          "ORDER BY seq DESC LIMIT 1", (run_id, issn))
        return json.loads(row["detail_json"]) if row else None

    def _terminal(self, run: dict, page: dict) -> str | None:
        query = json.loads(run["query_json"])
        if run["provider"] == "CROSSREF":
            if page["items_returned"] < query["rows"]:
                return "EMPTY_PAGE" if page["items_returned"] == 0 else "SHORT_PAGE"
            return None
        if page["items_returned"] == 0:
            return "EMPTY_PAGE"
        return "NO_NEXT_CURSOR" if page["cursor_out"] is None else None

    def _crawl(self, run: dict, *, fetch_run_id: str, resuming: bool = False) -> dict:
        query, caps = json.loads(run["query_json"]), json.loads(run["caps_json"])
        branches = [entry["issn"] for entry in query["issn_plan"]] if run["provider"] == "CROSSREF" else [None]
        executor = self._executor(fetch_run_id, caps)
        outcomes = {}
        stop = None
        for issn in branches:
            if stop is not None:
                outcomes[issn] = {"state": stop[0], "stop_reason": "NOT_ATTEMPTED_" + stop[1]}
                continue
            state, reason, run_stop = self._crawl_branch(run, issn, query, caps, executor, fetch_run_id, resuming)
            outcomes[issn] = {"state": state, "stop_reason": reason}
            if run_stop:
                stop = (state, reason)
        if run["provider"] != "CROSSREF":
            only = outcomes[None]
            return self._finish(run, only["state"], only["stop_reason"])
        unresolved = query.get("issns_requested_not_resolved", [])
        states = [o["state"] for o in outcomes.values()]
        if all(s == "COMPLETE" for s in states) and not unresolved:
            reason = outcomes[branches[0]]["stop_reason"] if len(branches) == 1 else "ALL_PLANNED_ISSN_QUERIES_TERMINATED"
            return self._finish(run, "COMPLETE", reason, branches=outcomes)
        problems = [f"ISSN_QUERY_{o['state']}:{issn}:{o['stop_reason']}" for issn, o in sorted(outcomes.items()) if o["state"] != "COMPLETE"]
        problems += [f"REQUESTED_ISSN_NOT_RESOLVED:{issn}" for issn in unresolved]
        if len(branches) == 1 and not unresolved:
            only = outcomes[branches[0]]
            return self._finish(run, only["state"], only["stop_reason"], branches=outcomes)
        state = "PARTIAL" if any(s in ("COMPLETE", "PARTIAL") for s in states) else "BLOCKED"
        return self._finish(run, state, ";".join(problems), branches=outcomes)

    def _crawl_branch(self, run, issn, query, caps, executor, fetch_run_id, resuming) -> tuple[str, str, bool]:
        """Crawl one query branch to its documented termination. Returns (state, stop_reason, stop_whole_run)."""
        provider, run_id = run["provider"], run["run_id"]
        failed = self._branch_failure(run_id, issn)
        if failed is not None:
            return failed["branch_state"], failed["reason"], False          # a branch that failed earlier in this run stays failed
        last = self._last_ok_page(run_id, issn)
        pass_no = self.kb.one("SELECT max(pass_no) p FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' AND json_extract(request_identity_json,'$.issn') IS ?",
                              (run_id, issn))["p"] or 0
        if last:
            pass_no = max(pass_no, last["pass_no"])
            terminal = self._terminal(run, last)
            if terminal:
                return "COMPLETE", terminal + ("_BEFORE_INTERRUPTION" if resuming else ""), False
            if last["cursor_out"] is None:
                return self._branch_failed(run_id, issn, fetch_run_id, "PARTIAL", "NEXT_CURSOR_MISSING", pass_no)
        cursor = last["cursor_out"] if last else "*"
        replays = self.kb.one("SELECT count(*) n FROM jh_run_event WHERE run_id=? AND kind='REPLAY_STARTED' AND json_extract(detail_json,'$.issn') IS ?",
                              (run_id, issn))["n"]
        since = (query.get("filter") or query.get("filter_since") or "").partition(":")[2] or None
        if provider == "CROSSREF" and not self.kb.one("SELECT 1 x FROM jh_page WHERE run_id=? AND kind='COUNT_PROBE' "
                                                      "AND json_extract(request_identity_json,'$.issn') IS ?", (run_id, issn)):
            url, identity = pv.crossref_count_request(issn, since=since)
            result = executor.execute(make_request(url, "CROSSREF", "journal-count-probe"))
            if result.outcome == "HTTP_OK":
                try:
                    listing = pv.parse_crossref_list(result.response.body)
                    page = self._page(run_id, kind="COUNT_PROBE", pass_no=pass_no, identity=identity, cursor_in=None, result=result,
                                      outcome="OK", items_returned=0, items_new=0)
                    self._count(run, page, listing["total_results"], issn)
                except pv.PageRefused as exc:
                    self._page(run_id, kind="COUNT_PROBE", pass_no=pass_no, identity=identity, cursor_in=None, result=result,
                               outcome="UNPARSEABLE", reason=exc.reason)
            else:
                # A failed probe is recorded; the works query below decides the branch (a count is never the criterion).
                outcome, state, reason = self._failure(result)
                self._page(run_id, kind="COUNT_PROBE", pass_no=pass_no, identity=identity, cursor_in=None, result=result, outcome=outcome, reason=reason)
                if executor.stopped:
                    return self._branch_failed(run_id, issn, fetch_run_id, state, reason, pass_no, attempts=result.attempts, run_stop=True)
        credential = self.credentials.openalex_api_key if provider != "CROSSREF" else None
        while True:
            if self.cancel is not None and self.cancel():
                return "PARTIAL", "CANCELLED_BY_OPERATOR", True
            ok_pages = self.kb.one("SELECT count(*) n FROM jh_page WHERE run_id=? AND kind='WORKS_PAGE' AND outcome='OK'", (run_id,))["n"]
            if ok_pages >= caps["max_pages"]:
                return "PARTIAL", "PAGE_CAP_REACHED", True
            if provider == "CROSSREF":
                url, identity = pv.crossref_works_request(issn, rows=query["rows"], cursor=cursor, since=since)
            else:
                url, identity = pv.openalex_works_request(query["source_id"], provider=provider, per_page=query["per_page"], cursor=cursor, since=since)
            if provider != "CROSSREF" and cursor != "*" and credential is None:
                self._page(run_id, kind="WORKS_PAGE", pass_no=pass_no, identity=identity, cursor_in=cursor, result=None,
                           outcome="NOT_SENT_CREDENTIAL_REQUIRED", reason="OPENALEX_API_KEY_NOT_CONFIGURED")
                return self._branch_failed(run_id, issn, fetch_run_id, "BLOCKED", "API_KEY_REQUIRED_FOR_DEEP_PAGING", pass_no, page_recorded=True)
            result = executor.execute(make_request(url, "CROSSREF" if provider == "CROSSREF" else "OPENALEX", "journal-works", credential=credential))
            status = result.response.status if result.response is not None else None
            if result.outcome != "HTTP_OK":
                if status == 400 and cursor != "*":
                    self._page(run_id, kind="WORKS_PAGE", pass_no=pass_no, identity=identity, cursor_in=cursor, result=result,
                               outcome="CURSOR_INVALID", reason="HTTP_400_ON_CURSOR_CONTINUATION")
                    self._event(run_id, "CURSOR_INVALID", detail={"issn": issn, "pass_no": pass_no, "http_status": 400}, fetch_run_id=fetch_run_id)
                    if replays >= caps["max_replays"]:
                        return self._branch_failed(run_id, issn, fetch_run_id, "PARTIAL", "CURSOR_INVALID_REPLAY_LIMIT_REACHED", pass_no,
                                                   page_recorded=True)
                    replays += 1
                    pass_no += 1
                    cursor = "*"
                    self._event(run_id, "REPLAY_STARTED", detail={"issn": issn, "pass_no": pass_no, "reason": "CURSOR_INVALID",
                                                                  "policy": "REPLAY_SAME_QUERY_FROM_START_DEDUP_BY_CONTENT"}, fetch_run_id=fetch_run_id)
                    continue
                outcome, state, reason = self._failure(result)
                self._page(run_id, kind="WORKS_PAGE", pass_no=pass_no, identity=identity, cursor_in=cursor, result=result, outcome=outcome, reason=reason)
                return self._branch_failed(run_id, issn, fetch_run_id, state, reason, pass_no, outcome=outcome, attempts=result.attempts,
                                           page_recorded=True, run_stop=executor.stopped or result.outcome == "BUDGET_EXHAUSTED")
            try:
                listing = (pv.parse_crossref_list if provider == "CROSSREF" else pv.parse_openalex_list)(result.response.body)
            except pv.PageRefused as exc:
                self._page(run_id, kind="WORKS_PAGE", pass_no=pass_no, identity=identity, cursor_in=cursor, result=result,
                           outcome="UNPARSEABLE", reason=exc.reason)
                return self._branch_failed(run_id, issn, fetch_run_id, "PARTIAL", "PAGE_UNPARSEABLE_" + exc.reason, pass_no, outcome="UNPARSEABLE",
                                           page_recorded=True)
            page = self._commit_page(run, pass_no, identity, cursor, result, listing, issn)
            self._count(run, page, listing["total_results"], issn)
            terminal = self._terminal(run, page)
            if terminal:
                return "COMPLETE", terminal, False
            if listing["next_cursor"] is None:
                return self._branch_failed(run_id, issn, fetch_run_id, "PARTIAL", "NEXT_CURSOR_MISSING", pass_no)
            cursor = listing["next_cursor"]

    def _branch_failed(self, run_id, issn, fetch_run_id, state, reason, pass_no, *, outcome=None, attempts=None, page_recorded=False,
                       run_stop=False) -> tuple[str, str, bool]:
        self._event(run_id, "PAGE_FAILED", detail={"issn": issn, "pass_no": pass_no, "outcome": outcome, "reason": reason, "attempts": attempts,
                                                   "branch_state": state, "failed_page_recorded": page_recorded}, fetch_run_id=fetch_run_id)
        return state, reason, run_stop

    def _commit_page(self, run, pass_no, identity, cursor, result, listing, issn=None) -> dict:
        """Checkpoint: page row + new observations + their claims in ONE transaction (content-deduplicated within the run and
        query branch: the same record returned by two Crossref ISSN queries is kept once per ISSN, so both sightings stay traceable)."""
        provider, run_id = run["provider"], run["run_id"]
        venue = {"CROSSREF": "CROSSREF_JOURNAL_ENDPOINT", "OPENALEX": "OPENALEX_PRIMARY_LOCATION", "OPENALEX_ALTERNATE": "OPENALEX_ANY_LOCATION"}[provider]
        base = "/message/items" if provider == "CROSSREF" else "/results"
        parse = pv.crossref_item if provider == "CROSSREF" else pv.openalex_item
        fresh, seen = [], set()
        for index, item in enumerate(listing["items"]):
            pointer = f"{base}/{index}"
            parsed = parse(item, pointer)
            item_sha = semantic_sha256(item)
            ident = {"run": run_id, "provider": provider, "record": parsed["provider_record_id"], "item": item_sha, "venue": venue}
            if issn is not None:
                ident["query_issn"] = issn
            semantic = semantic_sha256(ident)
            if semantic in seen or self.kb.one("SELECT 1 x FROM jh_observation WHERE semantic_sha256=?", (semantic,)):
                continue        # replayed page, page overlap after an offset shift, or a repeated item: already committed
            seen.add(semantic)
            fresh.append((index, pointer, parsed, item_sha, semantic))
        now = self.kb.now()
        with self.kb.transaction():
            page = self._page(run_id, kind="WORKS_PAGE", pass_no=pass_no, identity=identity, cursor_in=cursor, result=result, outcome="OK",
                              items_returned=len(listing["items"]), items_new=len(fresh), cursor_out=listing["next_cursor"], in_transaction=True)
            for index, pointer, parsed, item_sha, semantic in fresh:
                observation_id = self.kb.ids.new("jho")
                self.kb.insert("jh_observation", dict(
                    observation_id=observation_id, run_id=run_id, page_id=page["page_id"], item_index=index, item_pointer=pointer,
                    provider="CROSSREF" if provider == "CROSSREF" else "OPENALEX", venue_evidence=venue,
                    provider_record_id=parsed["provider_record_id"], doi_original=parsed["doi_original"], doi_status=parsed["doi_status"],
                    doi_key=parsed["doi_key"], doi_parse_json=canonical_json(parsed["doi_parse"]) if parsed["doi_parse"] is not None else None,
                    shape_status=parsed["shape_status"], shape_reason=parsed["shape_reason"], work_type_raw=parsed["work_type_raw"],
                    item_sha256=item_sha, observed_at_utc=now, semantic_sha256=semantic))
                claimed = set()
                for claim in parsed["claims"]:
                    claim_sha = semantic_sha256({"field": claim["field"], "value": claim["value"], "path": claim["path"]})
                    if claim_sha in claimed:
                        continue
                    claimed.add(claim_sha)
                    self.kb.insert("jh_field_claim", dict(claim_id=self.kb.ids.new("jfc"), observation_id=observation_id, field=claim["field"],
                                                          value_json=canonical_json(claim["value"]), source_field_path=claim["path"],
                                                          claim_semantic_sha256=claim_sha))
            _fault(self.faults, "before_page_commit", run_id=run_id, page_seq=page["seq"], kb=self.kb)
        return page

    def _count(self, run, page, value, issn=None) -> None:
        provider = "CROSSREF" if run["provider"] == "CROSSREF" else "OPENALEX"
        if value is None:
            return
        previous = self.kb.one("SELECT c.reported_count FROM jh_count_observation c JOIN jh_page p ON p.page_id = c.page_id WHERE c.run_id=? "
                               "AND json_extract(p.request_identity_json,'$.issn') IS ? ORDER BY c.rowid DESC LIMIT 1", (run["run_id"], issn))
        if previous is not None and previous["reported_count"] == value and page["kind"] == "WORKS_PAGE" and page["cursor_in"] != "*":
            return          # recorded at the probe / first page of each pass, and whenever it changes (per ISSN query)
        where = f" ISSN {issn}" if issn else ""
        row = dict(count_id=self.kb.ids.new("jhc"), journal_id=run["journal_id"], run_id=run["run_id"], page_id=page["page_id"], provider=provider,
                   basis="CROSSREF_TOTAL_RESULTS" if provider == "CROSSREF" else "OPENALEX_META_COUNT", reported_count=value,
                   observed_at=page["retrieved_at_utc"], scope="SNAPSHOT_AT_OBSERVATION_TIME_NOT_AN_INVENTORY_ASSERTION",
                   note=f"{run['provider']}{where} page seq {page['seq']} (pass {page['pass_no']})", recorded_at_utc=self.kb.now())
        row["semantic_sha256"] = semantic_sha256({"page": page["page_id"], "count": value})
        with self.kb.transaction():
            self.kb.insert_unique("jh_count_observation", row)
        if previous is not None and previous["reported_count"] != value:
            self._event(run["run_id"], "COUNT_DRIFT", detail={"issn": issn, "from": previous["reported_count"], "to": value, "page_seq": page["seq"]})

    def _finish_simple(self, run_id, state, reason, detail) -> None:
        self._event(run_id, "FINISHED", state=state, stop_reason=reason, detail=detail)

    def run_counters(self, run_id: str) -> dict:
        one = self.kb.one
        pages = one("SELECT count(*) ok, coalesce(sum(items_returned),0) items, coalesce(sum(items_new),0) new FROM jh_page "
                    "WHERE run_id=? AND kind='WORKS_PAGE' AND outcome='OK'", (run_id,))
        failed = one("SELECT count(*) n FROM jh_page WHERE run_id=? AND outcome<>'OK'", (run_id,))["n"]
        obs = self.kb.all("SELECT doi_status, shape_status, count(*) n FROM jh_observation WHERE run_id=? GROUP BY doi_status, shape_status", (run_id,))
        status_counts = {}
        for r in obs:
            status_counts[r["doi_status"]] = status_counts.get(r["doi_status"], 0) + r["n"]
        observations = sum(r["n"] for r in obs)
        records = one("SELECT count(DISTINCT coalesce(provider_record_id, observation_id)) n FROM jh_observation WHERE run_id=?", (run_id,))["n"]
        # A record seen by two Crossref ISSN queries is one record in two query branches, not a change.
        branch_records = one("SELECT count(*) n FROM (SELECT DISTINCT coalesce(o.provider_record_id, o.observation_id), "
                             "json_extract(p.request_identity_json,'$.issn') FROM jh_observation o JOIN jh_page p ON p.page_id = o.page_id "
                             "WHERE o.run_id=?)", (run_id,))["n"]
        keys = one("SELECT count(DISTINCT doi_key) n FROM jh_observation WHERE run_id=? AND doi_key IS NOT NULL", (run_id,))["n"]
        counts = [r["reported_count"] for r in self.kb.all("SELECT reported_count FROM jh_count_observation WHERE run_id=? ORDER BY rowid", (run_id,))]
        return {"pages_ok": pages["ok"], "pages_not_ok": failed, "items_returned": pages["items"], "observations_stored": observations,
                "items_deduplicated_within_run": pages["items"] - observations, "distinct_provider_records": records,
                "records_changed_within_run": observations - branch_records, "unique_valid_doi_keys": keys,
                "doi_status_counts": dict(sorted(status_counts.items())),
                "malformed_records": sum(r["n"] for r in obs if r["shape_status"] == "MALFORMED"),
                "replays": one("SELECT count(*) n FROM jh_run_event WHERE run_id=? AND kind='REPLAY_STARTED'", (run_id,))["n"],
                "reported_counts": counts, "count_drift_events": one("SELECT count(*) n FROM jh_run_event WHERE run_id=? AND kind='COUNT_DRIFT'", (run_id,))["n"],
                **self._issn_counters(run_id)}

    def _issn_counters(self, run_id: str) -> dict:
        """Per-ISSN query breakdown for Crossref runs (S04J-F01). Rows fetched are per query; unique DOI keys are a union."""
        run = self.kb.one("SELECT provider, query_json FROM jh_run WHERE run_id=?", (run_id,))
        if run is None or run["provider"] != "CROSSREF":
            return {}
        query = json.loads(run["query_json"])
        out, seen = {}, {}
        for entry in query.get("issn_plan", []):
            issn = entry["issn"]
            pages = self.kb.one("SELECT count(*) ok, coalesce(sum(items_returned),0) items, coalesce(sum(items_new),0) new FROM jh_page "
                                "WHERE run_id=? AND kind='WORKS_PAGE' AND outcome='OK' AND json_extract(request_identity_json,'$.issn')=?", (run_id, issn))
            keys = {r["doi_key"] for r in self.kb.all("SELECT DISTINCT o.doi_key FROM jh_observation o JOIN jh_page p ON p.page_id = o.page_id "
                                                      "WHERE o.run_id=? AND o.doi_key IS NOT NULL AND json_extract(p.request_identity_json,'$.issn')=?",
                                                      (run_id, issn))}
            for key in keys:
                seen[key] = seen.get(key, 0) + 1
            out[issn] = {
                "plan_evidence": entry["evidence"], "pages_ok": pages["ok"], "rows_fetched": pages["items"], "observations_stored": pages["new"],
                "unique_valid_doi_keys": len(keys),
                "pages_not_ok": self.kb.one("SELECT count(*) n FROM jh_page WHERE run_id=? AND outcome<>'OK' AND json_extract(request_identity_json,'$.issn')=?",
                                            (run_id, issn))["n"],
                "reported_counts": [r["reported_count"] for r in self.kb.all(
                    "SELECT c.reported_count FROM jh_count_observation c JOIN jh_page p ON p.page_id = c.page_id WHERE c.run_id=? "
                    "AND json_extract(p.request_identity_json,'$.issn')=? ORDER BY c.rowid", (run_id, issn))],
                "replays": self.kb.one("SELECT count(*) n FROM jh_run_event WHERE run_id=? AND kind='REPLAY_STARTED' AND json_extract(detail_json,'$.issn')=?",
                                       (run_id, issn))["n"]}
        return {"issn_queries": out, "issns_requested_not_resolved": query.get("issns_requested_not_resolved", []),
                "doi_keys_in_more_than_one_issn_query": sum(1 for n in seen.values() if n > 1)}

    def _finish(self, run: dict, state: str, reason: str, *, branches: dict | None = None) -> dict:
        counters = self.run_counters(run["run_id"])
        if branches is not None:
            for issn, outcome in branches.items():
                if issn is not None and issn in counters.get("issn_queries", {}):
                    counters["issn_queries"][issn].update(state=outcome["state"], stop_reason=outcome["stop_reason"])
        self._event(run["run_id"], "FINISHED", state=state, stop_reason=reason,
                    detail={"counters": counters, "completion_basis": completion_basis(run, state, reason)})
        return {"run_id": run["run_id"], "provider": run["provider"], "mode": run["mode"], "state": state, "stop_reason": reason,
                "counters": counters, "completion_basis": completion_basis(run, state, reason)}

    def status(self, run_id: str) -> dict:
        run = self.kb.one("SELECT * FROM jh_run WHERE run_id=?", (run_id,))
        if not run:
            raise ValidationError("Unknown journal run", code="UNKNOWN_RUN")
        finished = self.kb.one("SELECT state, stop_reason FROM jh_run_event WHERE run_id=? AND kind='FINISHED'", (run_id,))
        return {"run_id": run_id, "provider": run["provider"], "mode": run["mode"], "query": json.loads(run["query_json"]),
                "state": finished["state"] if finished else "UNFINISHED", "stop_reason": finished["stop_reason"] if finished else None,
                "counters": self.run_counters(run_id),
                "events": [dict(seq=e["seq"], kind=e["kind"], state=e["state"], stop_reason=e["stop_reason"])
                           for e in self.kb.all("SELECT * FROM jh_run_event WHERE run_id=? ORDER BY seq", (run_id,))]}

    # -- review tasks (shared with reconcile) --------------------------------------------------------------------------
    def task(self, journal_id, version_id, *, subject, trigger, context) -> tuple[dict, bool]:
        return add_task(self.kb, journal_id, version_id, subject=subject, trigger=trigger, context=context)


def add_task(kb, journal_id, version_id, *, subject, trigger, context, in_transaction=False) -> tuple[dict, bool]:
    row = dict(task_id=kb.ids.new("jrt"), journal_id=journal_id, first_version_id=version_id, subject=subject, trigger=trigger,
               context_json=canonical_json(context), created_at_utc=kb.now(),
               semantic_sha256=semantic_sha256({"journal": journal_id, "subject": subject, "trigger": trigger, "context": context}))
    if in_transaction:
        return kb.insert_unique("jh_review_task", row)
    with kb.transaction():
        return kb.insert_unique("jh_review_task", row)


def completion_basis(run: dict, state: str, reason: str) -> str:
    if state == "COMPLETE" and run["provider"] == "CROSSREF":
        issns = [e["issn"] for e in json.loads(run["query_json"]).get("issn_plan", [])]
        return (f"CROSSREF {run['mode']}: every planned ISSN query ({', '.join(issns)}) terminated by the provider's documented pagination rule "
                f"({reason}) at the recorded time. COMPLETE is relative to these recorded queries and filters only; it does not assert that "
                "every work the journal published was found.")
    if state == "COMPLETE":
        return (f"{run['provider']} {run['mode']} query terminated by the provider's documented pagination rule ({reason}) at the recorded "
                "time. COMPLETE is relative to this recorded query and filters only; it does not assert that every work the journal "
                "published was found.")
    return f"{run['provider']} {run['mode']} query did not reach its documented termination ({state}: {reason})."


__all__ = ["JournalHarvester", "add_task", "completion_basis", "DEFAULT_CAPS", "sha256_bytes"]
