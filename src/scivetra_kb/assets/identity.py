"""G3 identity check, ``identity-policy/1`` (S01 v03 05 section 3). Structural validity is never identity.

Evidence comes from the first pages' text and PDF document info returned by the G1 child. Only derived facts are
stored (evidence codes, matched identifiers, similarity scores); no page text or excerpt is persisted.

AUTO_EVIDENCE_MATCH iff (E1 title-page DOI or E2 title match) AND a supporting item (E3 first author, E4 metadata
DOI) AND no negative item, for a MAIN_TEXT link. Any negative item -> AMBIGUOUS (quarantined for human review).
Otherwise UNVERIFIED (pending human review).
"""
from __future__ import annotations

import re

from ..library.linking import best_title_similarity, title_tokens, jaccard
from ..metadata.doi import DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD, VALID, parse_doi

POLICY_VERSION = "identity-policy/1"
_DOI_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
_REFERENCES = re.compile(r"^\s*(references|bibliography)\s*$", re.I)
_SUPPLEMENT = re.compile(r"\b(supplementary|supporting information|appendix|erratum|correction to)\b", re.I)
_VERSION_MARKER = re.compile(r"\b(preprint|accepted manuscript|author accepted manuscript)\b|\barxiv:", re.I)


def _doi_key(text: str):
    candidate = text.rstrip(".,;:)]}")
    parse = parse_doi(candidate, DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)
    return parse.comparison_key if parse.status == VALID else None


def _split_references(text: str):
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _REFERENCES.match(line):
            return "\n".join(lines[:index]), "\n".join(lines[index + 1:]), True
    return text, "", False


def assess(validation: dict, paper: dict, *, role: str, version_class: str, require_human: bool = False) -> dict:
    """``paper``: {"dois": [...], "titles": [...], "first_author_families": [...]}. Returns state, evidence, reason codes."""
    pages = [p for p in validation.get("pages_text") or []]
    evidence, negatives = [], []
    if not pages or not any((p or "").strip() for p in pages[:2]):
        return {"state": "AMBIGUOUS", "evidence": [], "reasons": ["SCANNED_NO_TEXT"], "policy": POLICY_VERSION}
    paper_dois = set(paper["dois"])
    front, refs_text, refs_seen = [], [], False
    for number, text in enumerate(pages[:3], 1):
        text = text or ""
        if refs_seen:
            refs_text.append(text)
            continue
        before, after, found = _split_references(text)
        front.append((number, before))
        if found:
            refs_seen = True
            refs_text.append(after)
    page1 = front[0][1] if front and front[0][0] == 1 else ""
    front_dois = {}
    for number, text in front:
        for match in _DOI_TEXT.findall(text):
            key = _doi_key(match)
            if key:
                front_dois.setdefault(key, number)
    ref_dois = {k for t in refs_text for m in _DOI_TEXT.findall(t) for k in [_doi_key(m)] if k}
    e1 = sorted(k for k, page in front_dois.items() if k in paper_dois and page <= 2)
    if e1:
        evidence.append({"item": "E1", "doi": e1[0], "page": front_dois[e1[0]]})
    title_score = 0.0
    lines = [line for line in page1.split("\n") if line.strip()]
    windows = lines + [lines[i] + " " + lines[i + 1] for i in range(len(lines) - 1)]
    if paper["titles"] and windows:
        title_score = best_title_similarity(paper["titles"], windows[:40])
    if title_score >= 0.9:
        evidence.append({"item": "E2", "score": title_score})
    page1_tokens = title_tokens(page1)
    families = [f for f in paper["first_author_families"] if f]
    if families and any(title_tokens(f) and title_tokens(f) <= page1_tokens for f in families):
        evidence.append({"item": "E3"})
    info_doi = validation.get("info", {}).get("doi")
    if info_doi and _doi_key(info_doi) in paper_dois:
        evidence.append({"item": "E4"})
    if paper_dois:
        other_title_page = sorted(k for k, page in front_dois.items() if page == 1 and k not in paper_dois)
        if other_title_page:
            negatives.append("DOI_MISMATCH")
            evidence.append({"item": "N1", "doi": other_title_page[0]})
    if len(front_dois) > 1:
        negatives.append("MULTI_ARTICLE_PDF")
        evidence.append({"item": "N3", "distinct_dois": len(front_dois)})
    if _SUPPLEMENT.search(page1):
        negatives.append("SUPPLEMENT_SUSPECTED")
        evidence.append({"item": "N2"})
    if version_class in ("PUBLISHED", "UPDATED_PUBLISHED") and _VERSION_MARKER.search(page1):
        negatives.append("OTHER_VERSION")
        evidence.append({"item": "N4"})
    reasons = sorted(set(negatives))
    if not e1 and paper_dois & ref_dois:
        reasons.append("DOI_ONLY_IN_REFERENCES")
        evidence.append({"item": "DOI_IN_REFERENCES_IGNORED"})
    strong = bool(e1) or title_score >= 0.9
    support = any(e["item"] in ("E3", "E4") for e in evidence)
    if negatives:
        state = "AMBIGUOUS"
    elif strong and support and role == "MAIN_TEXT" and not require_human:
        state = "AUTO_EVIDENCE_MATCH"
    else:
        state = "UNVERIFIED"
        if not strong:
            reasons.append("NO_TITLE_PAGE_EVIDENCE")
        elif not support:
            reasons.append("INSUFFICIENT_SUPPORTING_EVIDENCE")
        elif role != "MAIN_TEXT":
            reasons.append("ROLE_REQUIRES_HUMAN_REVIEW")
        else:
            reasons.append("HUMAN_CONFIRMATION_REQUIRED")
    return {"state": state, "evidence": evidence, "reasons": sorted(set(reasons)), "policy": POLICY_VERSION}


__all__ = ["assess", "POLICY_VERSION", "jaccard"]
