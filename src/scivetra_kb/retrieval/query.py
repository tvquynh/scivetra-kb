"""Query handling: bounded, injection-proof FTS5 expressions and reciprocal rank fusion (``rkb-rrf/1``).

User text is never passed to FTS5 as syntax. It is NFKC-normalised, case-folded and split into words (letters and digits); at
most ``MAX_TERMS`` distinct words are kept, each emitted as a double-quoted FTS5 string (so ``AND``, ``NEAR``, ``*``, ``:``,
``^``, column filters and parentheses are inert), joined with ``OR`` and ranked by FTS5 ``bm25``. Common English function words
(``STOPWORDS``) are dropped unless the query consists only of them, so "in" or "the" alone never make an unrelated passage match.
``term_coverage`` is the share of the query's content words that occur in a passage (used by the RAG context to abstain)."""
from __future__ import annotations

import hashlib
import unicodedata

from ..core.errors import ValidationError
from ..evidence.excerpt import words

MAX_QUERY_CHARS = 1000
MAX_TERMS = 32
MAX_LIMIT = 50
RRF_K = 60
RRF_POLICY = "rkb-rrf/1"
STOPWORDS = frozenset(("a", "an", "the", "of", "in", "on", "at", "for", "to", "and", "or", "with", "by", "from", "is", "are", "was", "were", "be",
                       "been", "which", "what", "who", "whom", "how", "when", "where", "why", "did", "does", "do", "this", "that", "these", "those",
                       "it", "its", "as", "into", "out", "not", "no", "than", "then", "there", "their", "they", "we", "our", "you", "your", "i",
                       "has", "have", "had", "can", "could", "would", "should", "about", "any", "all", "some"))


def normalize_query(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("query must be a non-empty string", code="QUERY_INVALID")
    if len(text) > MAX_QUERY_CHARS:
        raise ValidationError(f"query longer than {MAX_QUERY_CHARS} characters", code="QUERY_TOO_LONG")
    if any(unicodedata.category(c) in ("Cs", "Co", "Cn") or (unicodedata.category(c) == "Cc" and c not in "\t\n\r") for c in text):
        raise ValidationError("query contains control, private-use, unassigned or surrogate characters", code="QUERY_INVALID")
    return text


def query_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def terms(text: str) -> list[str]:
    seen, out = set(), []
    ws = words(text)
    content = [w for w in ws if w not in STOPWORDS]
    for w in content or ws:
        if w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) == MAX_TERMS:
            break
    return out


def term_coverage(query_text: str, passage_text: str) -> float:
    t = terms(query_text)
    if not t:
        return 0.0
    present = set(words(passage_text))
    return sum(1 for w in t if w in present) / len(t)


def fts_expression(text: str) -> str | None:
    t = terms(text)
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in t) if t else None


def bounded_limit(limit) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ValidationError(f"limit must be an integer 1..{MAX_LIMIT}", code="LIMIT_INVALID")
    return limit


def rrf(rankings: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float, dict]]:
    """Fuse ranked id lists: score = sum 1/(k + rank); ties by id. Returns (id, score, {branch: rank})."""
    scores, ranks = {}, {}
    for branch, ids in sorted(rankings.items()):
        for rank, i in enumerate(ids, start=1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(i, {})[branch] = rank
    return sorted(((i, round(s, 9), ranks[i]) for i, s in scores.items()), key=lambda t: (-t[1], t[0]))
