"""``rkb-chunker/1``: deterministic, page-bounded chunks of an S06 RESEARCH_PARSE artifact.

Each chunk is one S06 text block (a paragraph-level unit of the content stream) on one page; a block longer than ``MAX_CHARS``
is split at the last whitespace before the limit (or hard at the limit). Chunks never cross a page, so every chunk is an exact
S06 anchor ``(page, char_start, char_end, text_sha256)`` that ``parse.service.check_anchor`` can verify. ``MAX_CHARS`` is 1000,
stricter than the ~1500 of S01 v03 09 section 1.1, so that a stored chunk never exceeds the rights-policy/1 quote limit of gate
A3. Blocks with no visible character are skipped. There is no overlap and no section path (S06 has no section structure).
"""
from __future__ import annotations

import hashlib

CHUNKER = "rkb-chunker/1"
MAX_CHARS = 1000


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_id(artifact_sha256: str, page: int, start: int, end: int) -> str:
    return "rch_" + _sha(f"{CHUNKER}|{artifact_sha256}|{page}|{start}|{end}")[:32]


def _spans(text: str, start: int, end: int):
    while end - start > MAX_CHARS:
        cut = text.rfind(" ", start + 1, start + MAX_CHARS + 1)
        cut = cut if cut > start else start + MAX_CHARS
        yield start, cut
        start = cut
    yield start, end


def chunks(payload: dict, artifact_sha256: str) -> list[dict]:
    out = []
    for p in payload["pages"]:
        text = p["text"]
        for b in p["blocks"]:
            for s, e in _spans(text, b["start"], b["end"]):
                piece = text[s:e]
                if not piece.strip():
                    continue
                out.append({"chunk_id": chunk_id(artifact_sha256, p["page"], s, e), "page": p["page"], "char_start": s, "char_end": e,
                            "text_sha256": _sha(piece), "text": piece})
    return out
