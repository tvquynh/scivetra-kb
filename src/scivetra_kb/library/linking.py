"""``link-policy/1`` (S01 v03 03 section 6.3): conservative linking of Reprovane items to GlobalPapers.

Only identical manifestation-level identifiers (MLI: doi, pmid, pmcid, arxiv) can produce an automatic link,
and only when the candidate is unique, the key is the item's only DOI (or, without DOI, a PMID/PMCID/arXiv key),
no conflict check C1-C4 fires and the candidate is not disputed. ``openalex_work`` matches, title similarity and
WorkFamily relations are signals only: they create LinkCandidates for human review, never a link.
"""
from __future__ import annotations

import re
import unicodedata

POLICY_VERSION = "link-policy/1"
MLI_NAMESPACES = ("doi", "pmid", "pmcid", "arxiv")
TITLE_SIMILARITY_THRESHOLD = 0.9

# Versioned with link-policy/1: provider work_type value -> manifestation_kind (C1). Unlisted values map to UNKNOWN.
WORK_TYPE_KIND = {
    "article": "JOURNAL_ARTICLE", "journal-article": "JOURNAL_ARTICLE",
    "preprint": "PREPRINT", "posted-content": "PREPRINT",
    "proceedings-article": "PROCEEDINGS_PAPER", "conference-paper": "PROCEEDINGS_PAPER",
    "book-chapter": "BOOK_CHAPTER", "book": "BOOK", "dissertation": "THESIS", "dataset": "DATASET",
    "software": "SOFTWARE", "report": "REPORT",
}
# L5 WorkFamily role inferred from the kind of each paper.
KIND_ROLE = {"PREPRINT": "PREPRINT", "JOURNAL_ARTICLE": "VERSION_OF_RECORD", "PROCEEDINGS_PAPER": "CONFERENCE_VERSION",
             "DATASET": "RELATED_DATASET", "SOFTWARE": "RELATED_SOFTWARE"}

KIND_CONFLICT, VENUE_CONFLICT, DOI_CONFLICT, VERSION_CONFLICT = "KIND_CONFLICT", "VENUE_CONFLICT", "DOI_CONFLICT", "VERSION_CONFLICT"


def kind_from_work_types(work_types) -> str:
    kinds = {WORK_TYPE_KIND.get(str(w).casefold(), "UNKNOWN") for w in work_types}
    kinds.discard("UNKNOWN")
    return kinds.pop() if len(kinds) == 1 else "UNKNOWN"


def _venue_key(text: str) -> str:
    return " ".join(text.casefold().split())


def title_tokens(text: str) -> frozenset:
    """E2 normalization (05 section 3): NFKC, casefold, punctuation stripped, whitespace collapsed; token set."""
    norm = unicodedata.normalize("NFKC", text).casefold()
    norm = "".join(c if (c.isalnum() or c.isspace()) else " " for c in norm)
    return frozenset(norm.split())


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def best_title_similarity(titles_a, titles_b) -> float:
    best = 0.0
    for x in titles_a:
        for y in titles_b:
            best = max(best, jaccard(title_tokens(x), title_tokens(y)))
    return round(best, 4)


def conflict_checks(item: dict, candidate: dict) -> list[str]:
    """C1-C4 for one (item, candidate) pair. ``item``/``candidate`` are fact dicts built by the importer."""
    codes = []
    if candidate["kind"] != "UNKNOWN" and item["kind"] != "UNKNOWN" and candidate["kind"] != item["kind"]:
        codes.append(KIND_CONFLICT)
    if item["venues"] and candidate["venues"] and not ({_venue_key(v) for v in item["venues"]} & {_venue_key(v) for v in candidate["venues"]}):
        codes.append(VENUE_CONFLICT)
    if item["dois"] and candidate["active_dois"] and set(candidate["active_dois"]) - set(item["dois"]):
        codes.append(DOI_CONFLICT)
    classes = set(candidate["version_classes"])
    if (classes and classes <= {"SUBMITTED"} and item["kind"] in ("JOURNAL_ARTICLE", "PROCEEDINGS_PAPER")) or \
            (classes & {"PUBLISHED", "UPDATED_PUBLISHED"} and item["kind"] == "PREPRINT"):
        codes.append(VERSION_CONFLICT)
    return codes


def decide(item: dict, mli_candidates: dict, signal_candidates: dict) -> dict:
    """Apply L1-L4 to collected facts.

    ``mli_candidates``: paper_id -> {"facts": candidate facts, "keys": [(namespace, value, assertion_id, effective_status)]}
    ``signal_candidates``: paper_id -> {"facts": ..., "signals": [...]}
    Returns {"auto": paper_id|None, "link_key": {...}|None, "conflicts": {paper_id: [codes]}, "reasons": [...]}.
    """
    reasons = []
    conflicts = {pid: conflict_checks(item, c["facts"]) for pid, c in {**signal_candidates, **mli_candidates}.items()}
    if len(item["dois"]) > 1:                                                  # L1
        reasons.append("MULTIPLE_VALID_DOIS")
        return {"auto": None, "link_key": None, "conflicts": conflicts, "reasons": reasons}
    if len(mli_candidates) != 1:                                               # L3 (a)
        reasons.append("NO_MLI_CANDIDATE" if not mli_candidates else "SEVERAL_MLI_CANDIDATES")
        return {"auto": None, "link_key": None, "conflicts": conflicts, "reasons": reasons}
    (paper_id, candidate), = mli_candidates.items()
    usable = [k for k in candidate["keys"] if k[3] == "ACTIVE" and (
        (k[0] == "doi" and item["dois"] == [k[1]]) or (k[0] != "doi" and not item["dois"]))]  # L3 (b)
    if not usable:
        reasons.append("MLI_KEY_NOT_ELIGIBLE")
    if conflicts[paper_id]:                                                   # L3 (c)
        reasons.append("CONFLICT_CHECK_FIRED")
    if candidate["facts"]["identity_state"] in ("DISPUTED", "MERGED_INTO", "SPLIT"):  # L3 (d)
        reasons.append("CANDIDATE_" + candidate["facts"]["identity_state"])
    if reasons:
        return {"auto": None, "link_key": None, "conflicts": conflicts, "reasons": reasons}
    namespace, value = sorted(usable)[0][:2]
    return {"auto": paper_id, "link_key": {"namespace": namespace, "value_normalized": value}, "conflicts": conflicts, "reasons": []}


def item_claims(item: dict) -> dict:
    """Facts copied from the RV item into the reference (titles, venues, work types, years). Values only, no IDs."""
    titles, venues, work_types, years, families = set(), set(), set(), set(), set()
    for record in (item.get("metadata") or {}).get("records") or []:
        fields = record.get("fields") or {}
        for entry in fields.get("title") or []:
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                titles.add(entry["value"])
        for entry in fields.get("venue") or []:
            if isinstance(entry, dict) and isinstance(entry.get("title"), str):
                venues.add(entry["title"])
        for entry in fields.get("work_type") or []:
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                work_types.add(entry["value"])
        for entry in fields.get("publication_year") or []:
            if isinstance(entry, dict) and type(entry.get("value")) is int:
                years.add(entry["value"])
        for entry in fields.get("authors") or []:
            if isinstance(entry, dict) and entry.get("author_position") == 1:
                family = entry.get("author_family_name")
                display = entry.get("author_display_name")
                if isinstance(family, str) and family:
                    families.add(family)
                elif isinstance(display, str) and display.split():
                    families.add(display.split()[-1])
    return {"titles": sorted(titles), "venues": sorted(venues), "work_types": sorted(work_types), "years": sorted(years),
            "first_author_families": sorted(families)}


_PMCID = re.compile(r"PMC[0-9]{1,12}")
_PMID = re.compile(r"[0-9]{1,12}")
_ARXIV = re.compile(r"[0-9]{4}\.[0-9]{4,5}|[a-z-]+(\.[A-Z]{2})?/[0-9]{7}")


def other_mli(item: dict) -> list[tuple[str, str]]:
    """PMID/PMCID/arXiv keys from provider identifiers, accepted only in their exact canonical forms."""
    out = set()
    for claim in (item.get("identity") or {}).get("provider_identifiers") or []:
        namespace, value = claim.get("namespace"), claim.get("value")
        if not isinstance(value, str):
            continue
        if namespace == "pmid" and _PMID.fullmatch(value):
            out.add(("pmid", value))
        elif namespace == "pmcid" and _PMCID.fullmatch(value):
            out.add(("pmcid", value))
        elif namespace == "arxiv" and _ARXIV.fullmatch(value):
            out.add(("arxiv", value))
    return sorted(out)
