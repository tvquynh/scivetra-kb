"""Per-journal pipeline and batch isolation.

``run_pipeline`` = register -> resolve -> Crossref full crawl -> OpenAlex primary-venue crawl -> (optional) OpenAlex
any-location crawl -> reconcile. ``run_batch`` runs it for several journals; a refusal or provider failure for one
journal (invalid ISSN, unresolved source, BLOCKED/PARTIAL run) is recorded in that journal's result and never stops the
others. Only a process-level interruption (``SimulatedCrash`` in tests, a real crash in production) ends the batch;
every journal's committed pages stay resumable.
"""
from __future__ import annotations

from ..core.errors import KBError
from .harvest import JournalHarvester
from .reconcile import Reconciler


def run_pipeline(harvester: JournalHarvester, entry: dict, *, include_alternate: bool = True, page_sizes: dict | None = None) -> dict:
    page_sizes = page_sizes or {}
    journal = harvester.register_journal(entry["label"], entry["issns"], work_type_filter=entry.get("work_type_filter"))
    jid = journal["journal_id"]
    out = {"label": entry["label"], "journal_id": jid, "requested_issns": journal["requested_issns"]}
    if entry.get("owner_count") is not None:
        out["owner_count"] = harvester.record_owner_count(jid, count=entry["owner_count"]["value"], observed_on=entry["owner_count"]["observed_on"],
                                                          note=entry["owner_count"]["note"])
    out["resolution"] = harvester.resolve(jid, openalex_source_id=entry.get("openalex_source_id"))
    providers = ["CROSSREF", "OPENALEX"] + (["OPENALEX_ALTERNATE"] if include_alternate else [])
    out["runs"] = {}
    for provider in providers:
        out["runs"][provider] = harvester.harvest(jid, provider, page_size=page_sizes.get(provider))
    out["version"] = Reconciler(harvester.kb, operator=harvester.operator).reconcile(jid)
    return out


def run_batch(harvester: JournalHarvester, entries: list[dict], **kwargs) -> dict:
    results = []
    for entry in entries:
        try:
            result = run_pipeline(harvester, entry, **kwargs)
            result["outcome"] = result["version"]["collection_state"]
        except KBError as exc:
            result = {"label": entry.get("label"), "requested_issns": entry.get("issns"), "outcome": "REFUSED", "error": exc.code, "message": str(exc)}
        results.append(result)
    summary = {}
    for r in results:
        summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1
    return {"journals": results, "outcome_counts": dict(sorted(summary.items()))}


__all__ = ["run_pipeline", "run_batch"]
