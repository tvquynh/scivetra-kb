"""``rkb-excerpt-detect/1``: deterministic detection of verbatim source excerpts in human-typed free text (S08 v03, S08-F03).

Human reviewers and adjudicators may copy a passage of the document into a rationale (or spell it as a reason code). Rights gate
A4 governs storing a human-typed excerpt and A5 governs showing a stored one, so the KB must recognise such copies. The test is
grounded in the pinned S06 artifact text of the draft's document (processed locally under A2) and is conservative:

- both texts are NFKC-normalised, case-folded and split into words of letters / digits (punctuation, underscores, whitespace and
  line breaks are separators), so case, spacing, punctuation and Unicode-compatibility variants of a copy still match;
- a text CONTAINS an excerpt when any run of ``WINDOW_WORDS`` (6) consecutive words occurs in the source word stream, or when
  the whole text has at least ``MIN_WHOLE_WORDS`` (4) words and occurs in it as a whole.

Limitations (documented, ADR_S08 ES-D15): paraphrases, reordered words, translations, abbreviations and copies with an edit at
least every five words are not detected; very short copies (fewer than four words) are not treated as excerpts; common
six-word phrases that also occur in the source are treated as excerpts (fail closed). It is a rights safeguard, not plagiarism
detection or semantic analysis.
"""
from __future__ import annotations

import re
import unicodedata

POLICY = "rkb-excerpt-detect/1"
WINDOW_WORDS = 6
MIN_WHOLE_WORDS = 4
_WORD = re.compile(r"[^\W_]+")


def words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text or "").casefold())


class SourceIndex:
    """Word shingles of one document's text (never returned or stored; used only for the containment test)."""

    def __init__(self, texts):
        stream = []
        for t in texts:
            stream.extend(words(t))
        self.joined = " " + " ".join(stream) + " "
        self.shingles = {tuple(stream[i:i + WINDOW_WORDS]) for i in range(max(0, len(stream) - WINDOW_WORDS + 1))}

    def contains_excerpt(self, text: str) -> bool:
        w = words(text)
        if len(w) >= WINDOW_WORDS and any(tuple(w[i:i + WINDOW_WORDS]) in self.shingles for i in range(len(w) - WINDOW_WORDS + 1)):
            return True
        return len(w) >= MIN_WHOLE_WORDS and (" " + " ".join(w) + " ") in self.joined
