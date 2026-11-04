"""Offline simulated Crossref/OpenAlex providers for S04J (development/testing transport; NOT a data source).

The simulator implements the S02 ``Transport`` interface and answers only the journal endpoints the harvester uses,
from a synthetic scenario (``jh-sim/1``). It reproduces the behaviours the harvester must survive:

* Crossref deep paging with opaque cursors; ``rows=0`` count probes; ``total-results`` that can drift; records inserted
  at the head of the result list mid-crawl (offset shift -> the same record on two pages); a record replaced by a
  publisher update between two runs; ``from-update-date`` filtering.
* OpenAlex ``/sources?filter=issn:`` and ``/works`` with ``primary_location.source.id`` / ``locations.source.id`` filters,
  ``per-page`` and cursors; a free API key required to follow a cursor after the first page (401 without a valid key).
* Faults per (provider, page index): HTTP status with headers (429 + Retry-After, 5xx, 401, 403), ``timeout``
  (TransportError TIMEOUT), ``bad_json`` (unparseable body), ``crash`` (``SimulatedCrash``: the process dies mid-run).
* Cursor invalidation: ``expire_all_cursors()`` or scheduled expiries make continuation requests answer HTTP 400.

Scenario data is synthetic. Fixture DOIs use the Crossref test prefix 10.5555; any resemblance to a real record is
accidental. Nothing here represents the real metadata of any journal or publisher.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..core.errors import TransportError
from ..core.paths import refuse_unsafe_path_text, safe_input_file
from ..metadata.transport import Response, Transport

SCENARIO_FILE = "journal_sim.json"
SCHEMA = "jh-sim/1"
JSON_HEADERS = {"content-type": "application/json"}


class SimulatedCrash(Exception):
    """Stands in for a process kill / power loss while a request or page is in flight."""


def _body(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SimulatedProviders(Transport):
    def __init__(self, scenario: dict, profile: str | None = None):
        if not isinstance(scenario, dict) or scenario.get("schema") != SCHEMA:
            raise ValueError("not a jh-sim/1 scenario")
        self.scenario = copy.deepcopy(scenario)
        overrides = {}
        if profile is not None:
            # A named profile replaces faults/expiries/mutations and may start at a later cursor generation, e.g. the
            # "after_restart" profile of the smoke scenario: the new process sees every persisted cursor as expired.
            if profile not in self.scenario.get("profiles", {}):
                raise ValueError("unknown simulator profile")
            overrides = self.scenario["profiles"][profile]
            for key in ("faults", "cursor_expiries", "mutations"):
                if key in overrides:
                    self.scenario[key] = copy.deepcopy(overrides[key])
            self.scenario.setdefault("settings", {}).update(overrides.get("settings", {}))
        self.crossref_journals = self.scenario.get("crossref_journals", {})
        self.crossref_works = self.scenario.get("crossref_works", {})
        self.openalex_sources = self.scenario.get("openalex_sources", [])
        self.openalex_works = self.scenario.get("openalex_works", [])
        self.settings = self.scenario.get("settings", {})
        self.faults = {}
        for fault in self.scenario.get("faults", []):
            self.faults.setdefault((fault["provider"], fault.get("works_key", "*"), fault["page_index"]), []).extend(fault["responses"])
        self.mutations = list(self.scenario.get("mutations", []))
        self.expiries = {(e["provider"], e["page_index"]): e.get("times", 1) for e in self.scenario.get("cursor_expiries", [])}
        self.valid_keys = set(self.settings.get("openalex_valid_key_sha256", []))
        self.generation = int(overrides.get("generation", 0))
        # Snapshot edits of a profile (e.g. "second_snapshot": a later state of the providers).
        for works_key, items in sorted(overrides.get("replace_crossref", {}).items()):
            self.apply_update(works_key, items)
        for works_key, dois in sorted(overrides.get("remove_crossref_dois", {}).items()):
            self.crossref_works[works_key] = [i for i in self.crossref_works[works_key] if i.get("DOI") not in set(dois)]
        if overrides.get("replace_openalex"):
            self.apply_update("OPENALEX", overrides["replace_openalex"])
        if overrides.get("remove_openalex_ids"):
            gone = set(overrides["remove_openalex_ids"])
            self.openalex_works[:] = [w for w in self.openalex_works if w.get("id") not in gone]
        self.request_counts: dict[str, int] = {}
        self.calls: list = []

    @classmethod
    def from_directory(cls, directory: str, profile: str | None = None) -> "SimulatedProviders":
        refuse_unsafe_path_text(directory)
        path = safe_input_file(str(Path(directory) / SCENARIO_FILE), max_bytes=64 * 1024 * 1024)
        return cls(json.loads(path.read_text(encoding="utf-8")), profile)

    # -- controls used by tests / the smoke guide ------------------------------------------------------------------
    def expire_all_cursors(self) -> None:
        self.generation += 1

    def apply_update(self, works_key: str, replacements: list[dict]) -> None:
        """Publisher update between runs: replace Crossref items by DOI (OpenAlex items by id)."""
        for new in replacements:
            items = self.crossref_works[works_key] if works_key != "OPENALEX" else self.openalex_works
            field = "DOI" if works_key != "OPENALEX" else "id"
            for i, old in enumerate(items):
                if old.get(field) == new.get(field):
                    items[i] = copy.deepcopy(new)
                    break
            else:
                items.append(copy.deepcopy(new))

    # -- cursors ---------------------------------------------------------------------------------------------------
    def _cursor(self, key: str, offset: int) -> str:
        raw = _body({"g": self.generation, "k": key, "o": offset})
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def _decode_cursor(self, token: str, key: str):
        if token == "*":
            return 0
        try:
            data = json.loads(base64.urlsafe_b64decode(token.encode("ascii")))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or data.get("k") != key or data.get("g") != self.generation:
            return None
        return data.get("o")

    # -- transport ---------------------------------------------------------------------------------------------------
    def send(self, request) -> Response:
        self.calls.append(request)
        parts = urlsplit(request.url)
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        if parts.hostname == "api.crossref.org":
            return self._crossref(request, parts.path, query)
        if parts.hostname == "api.openalex.org":
            return self._openalex(request, parts.path, query)
        raise TransportError("simulator: unknown host", code="NO_FIXTURE")

    def _json(self, request, status: int, value, headers=None) -> Response:
        return Response(status, dict(headers or JSON_HEADERS), _body(value), request.url)

    def _fault(self, request, provider: str, works_key: str, page_index: int, source: str | None = None):
        keys = [(provider, works_key, page_index), (provider, "*", page_index)]
        if source:
            keys.insert(0, (provider, f"{works_key}:{source}", page_index))
        for key in keys:
            queue = self.faults.get(key)
            if queue:
                spec = queue.pop(0)
                kind = spec.get("kind", "status")
                if kind == "timeout":
                    raise TransportError("simulated timeout", code="TIMEOUT")
                if kind == "crash":
                    raise SimulatedCrash(f"simulated crash at {provider} page {page_index}")
                if kind == "bad_json":
                    return Response(200, dict(JSON_HEADERS), b'{"status":"ok","message":{"items":[', request.url)
                return Response(spec["status"], dict(spec.get("headers", JSON_HEADERS)), _body(spec.get("body", {"status": "error"})), request.url)
        return None

    def _mutate(self, works_key: str) -> None:
        count = self.request_counts.get(works_key, 0) + 1
        self.request_counts[works_key] = count
        for mutation in list(self.mutations):
            if mutation["works_key"] == works_key and mutation["after_request"] == count:
                target = self.crossref_works[works_key] if works_key != "OPENALEX" else self.openalex_works
                target[0:0] = copy.deepcopy(mutation.get("insert_front", []))
                self.mutations.remove(mutation)

    def _expired(self, provider: str, page_index: int) -> bool:
        remaining = self.expiries.get((provider, page_index), 0)
        if remaining:
            self.expiries[(provider, page_index)] = remaining - 1
            return True
        return False

    # Crossref ----------------------------------------------------------------------------------------------------------
    def _crossref(self, request, path: str, query: dict) -> Response:
        segments = [s for s in path.split("/") if s]
        if len(segments) < 2 or segments[0] != "journals":
            return self._json(request, 404, {"status": "error", "message": "resource not found"})
        issn = segments[1]
        journal = self.crossref_journals.get(issn)
        if journal is None:
            return self._json(request, 404, {"status": "error", "message-type": "exception", "message": "Resource not found."})
        if len(segments) == 2:
            return self._json(request, 200, {"status": "ok", "message-type": "journal", "message-version": "1.0.0", "message": {
                "title": journal.get("title"), "publisher": journal.get("publisher"),
                "ISSN": [i["value"] for i in journal.get("issn_type", [])], "issn-type": journal.get("issn_type", [])}})
        key = journal["works_key"]
        rows = int(query.get("rows", "20"))
        since = (query.get("filter") or "").partition("from-update-date:")[2] or None
        cursor = query.get("cursor")
        offset = 0 if cursor is None else self._decode_cursor(cursor, key)
        page_index = 0 if not rows or offset is None else offset // rows
        if cursor not in (None, "*"):
            if offset is None or self._expired("CROSSREF", page_index):
                return self._json(request, 400, {"status": "failed", "message-type": "validation-failure",
                                                 "message": [{"type": "cursor-expired", "message": "Cursor is no longer valid"}]})
        fault = self._fault(request, "CROSSREF", key, page_index if rows else -1)
        if fault is not None:
            return fault
        if rows:
            self._mutate(key)
        items = self.crossref_works.get(key, [])
        if since:
            items = [i for i in items if ((i.get("deposited") or {}).get("date-time") or "")[:10] >= since]
        total = len(items) + int(self.settings.get("crossref_total_offset", 0)) + \
            (int(self.settings.get("crossref_total_drift", 0)) if self.request_counts.get(key, 0) > 1 else 0)
        message = {"total-results": max(total, 0), "items-per-page": rows, "query": {"start-index": 0, "search-terms": None},
                   "facets": {}, "items": items[offset:offset + rows] if rows else []}
        if rows and cursor is not None:
            message["next-cursor"] = self._cursor(key, offset + rows)
        return self._json(request, 200, {"status": "ok", "message-type": "work-list", "message-version": "1.0.0", "message": message})

    # OpenAlex ----------------------------------------------------------------------------------------------------------
    def _openalex(self, request, path: str, query: dict) -> Response:
        if path == "/sources":
            issn = (query.get("filter") or "").partition("issn:")[2]
            results = [s for s in self.openalex_sources if issn in (s.get("issn") or [])]
            return self._json(request, 200, {"meta": {"count": len(results), "page": 1, "per_page": 25}, "results": results,
                                             "group_by": []})
        if path != "/works":
            return self._json(request, 404, {"error": "not found"})
        filters = dict(f.split(":", 1) for f in (query.get("filter") or "").split(",") if ":" in f)
        per_page = int(query.get("per-page", "25"))
        cursor = query.get("cursor", "*")
        venue_key = "primary" if "primary_location.source.id" in filters else "any"
        source = filters.get("primary_location.source.id") or filters.get("locations.source.id")
        key = f"OA:{venue_key}:{source}"
        offset = self._decode_cursor(cursor, key)
        page_index = 0 if offset is None else offset // per_page
        if cursor != "*":
            if self.settings.get("openalex_require_key_after_first_page", True):
                secret = getattr(request, "credential", None)
                digest = hashlib.sha256(secret.reveal().encode("utf-8")).hexdigest() if secret is not None else None
                if digest not in self.valid_keys:
                    return self._json(request, 401, {"error": "API key required", "message": "A valid API key is required to page past the first page."})
            if offset is None or self._expired("OPENALEX", page_index):
                return self._json(request, 400, {"error": "Invalid cursor", "message": "cursor expired"})
        fault = self._fault(request, "OPENALEX", venue_key, page_index, source)
        if fault is not None:
            return fault
        self._mutate("OPENALEX")
        since = filters.get("from_updated_date")

        def matches(work):
            primary = ((work.get("primary_location") or {}).get("source") or {}).get("id") or ""
            if venue_key == "primary":
                ok = primary.rsplit("/", 1)[-1] == source
            else:
                ok = any(((loc.get("source") or {}).get("id") or "").rsplit("/", 1)[-1] == source for loc in work.get("locations") or [])
            return ok and (since is None or (work.get("updated_date") or "")[:10] >= since)

        items = [w for w in self.openalex_works if matches(w)]
        page = items[offset:offset + per_page]
        next_cursor = self._cursor(key, offset + per_page) if offset + per_page < len(items) else None
        return self._json(request, 200, {"meta": {"count": len(items), "db_response_time_ms": 1, "page": None, "per_page": per_page,
                                                  "next_cursor": next_cursor, "groups_count": None}, "results": page, "group_by": []})


__all__ = ["SimulatedProviders", "SimulatedCrash", "SCENARIO_FILE", "SCHEMA"]
