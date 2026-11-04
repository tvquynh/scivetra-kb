"""The derived retrieval index file ``<data_root>/index/fts.sqlite`` (``rkb-retrieval-index/1``).

It is disposable and rebuilt from the KB: never authoritative, never part of a backup, never the only way to read the KB. A build
writes a new file next to it (``.fts.sqlite.building-<random>``) and publishes it with one atomic rename only after it is
complete and its manifest SHA-256 is recorded in the KB (``rt_index_build``). A leftover building file is an explicit
incomplete state that is never read and is removed by the next build. Readers open the published file read-only and refuse it
when FTS5 is missing, the file is corrupt, its manifest does not match its rows, or its build is not recorded in this KB.

Tables: ``meta``; ``chunk`` (anchors and hashes of every chunk of every indexed artifact; ``text_indexed`` = 1 only where gate
A3 allowed storing the text); ``chunk_fts`` (contentless FTS5 over the chunk text, rows only where ``text_indexed``); ``vec``
(float32 vectors, only where ``text_indexed``); ``paper`` / ``paper_fts`` (metadata facts: titles, venues, DOIs, years, first
authors; never abstracts); ``evidence`` / ``evidence_fts`` (currently accepted S08 drafts: class, non-text typed values and,
only where stored under A3, text-field values). No document text column exists anywhere.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from ..core.canonical import canonical_json, semantic_sha256
from ..core.errors import IntegrityError, PolicyRefusal

INDEX_SCHEMA = "rkb-retrieval-index/1"
INDEX_DIR = "index"
INDEX_FILE = "fts.sqlite"
TOKENIZER = "unicode61 remove_diacritics 2"

DDL = [
    "CREATE TABLE meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
    "CREATE TABLE chunk (rid INTEGER PRIMARY KEY, chunk_id TEXT NOT NULL UNIQUE, artifact_id TEXT NOT NULL, artifact_sha256 TEXT NOT NULL, "
    "paper_id TEXT NOT NULL, document_version_id TEXT NOT NULL, asset_sha256 TEXT NOT NULL, link_id TEXT NOT NULL, page INTEGER NOT NULL, "
    "char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, text_sha256 TEXT NOT NULL, chunker TEXT NOT NULL, text_indexed INTEGER NOT NULL)",
    f"CREATE VIRTUAL TABLE chunk_fts USING fts5(body, content='', tokenize='{TOKENIZER}')",
    "CREATE TABLE vec (chunk_id TEXT PRIMARY KEY NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL)",
    "CREATE TABLE paper (rid INTEGER PRIMARY KEY, paper_id TEXT NOT NULL UNIQUE, facts_json TEXT NOT NULL)",
    f"CREATE VIRTUAL TABLE paper_fts USING fts5(body, content='', tokenize='{TOKENIZER}')",
    "CREATE TABLE evidence (rid INTEGER PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE, paper_id TEXT NOT NULL, artifact_id TEXT NOT NULL, "
    "evidence_class TEXT NOT NULL, draft_semantic_sha256 TEXT NOT NULL, text_indexed INTEGER NOT NULL)",
    f"CREATE VIRTUAL TABLE evidence_fts USING fts5(body, content='', tokenize='{TOKENIZER}')",
]


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            con.close()
        return True
    except sqlite3.OperationalError:
        return False


def require_fts5():
    if not fts5_available():
        raise PolicyRefusal(f"SQLite {sqlite3.sqlite_version} has no FTS5: local retrieval is NOT_SUPPORTED on this interpreter",
                            code="FTS5_NOT_SUPPORTED")


def index_dir(root: Path) -> Path:
    return Path(root) / INDEX_DIR


def manifest(con) -> str:
    """ID-independent digest of every row that determines retrieval results (the contentless FTS tables are derived from the
    same CAS text and are covered by the chunk hashes and the per-hit CAS re-verification)."""
    def rows(sql):
        return [list(r) for r in con.execute(sql)]
    meta = {k: v for k, v in con.execute("SELECT key, value FROM meta") if k in ("schema", "chunker", "embedder", "kb_instance_id")}
    return semantic_sha256({
        "meta": meta,
        "chunk": rows("SELECT chunk_id, artifact_id, artifact_sha256, paper_id, document_version_id, asset_sha256, link_id, page, char_start, "
                      "char_end, text_sha256, chunker, text_indexed FROM chunk ORDER BY chunk_id"),
        "vec": rows("SELECT chunk_id, dim, lower(hex(vector)) FROM vec ORDER BY chunk_id"),
        "paper": rows("SELECT paper_id, facts_json FROM paper ORDER BY paper_id"),
        "evidence": rows("SELECT draft_id, paper_id, artifact_id, evidence_class, draft_semantic_sha256, text_indexed FROM evidence ORDER BY draft_id"),
    })


def write(path: Path, *, meta: dict, chunks, vectors, papers, evidence) -> str:
    """Write a complete index file at ``path`` (which must not exist); return its manifest SHA-256."""
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode = OFF")
        con.execute("BEGIN")
        for ddl in DDL:
            con.execute(ddl)
        for i, c in enumerate(chunks, start=1):
            con.execute("INSERT INTO chunk VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (i, c["chunk_id"], c["artifact_id"], c["artifact_sha256"], c["paper_id"], c["document_version_id"], c["asset_sha256"],
                         c["link_id"], c["page"], c["char_start"], c["char_end"], c["text_sha256"], c["chunker"], 1 if c["text"] is not None else 0))
            if c["text"] is not None:
                con.execute("INSERT INTO chunk_fts(rowid, body) VALUES (?,?)", (i, c["text"]))
        for chunk_id, dim, blob in vectors:
            con.execute("INSERT INTO vec VALUES (?,?,?)", (chunk_id, dim, blob))
        for i, (paper_id, facts, body) in enumerate(papers, start=1):
            con.execute("INSERT INTO paper VALUES (?,?,?)", (i, paper_id, canonical_json(facts)))
            con.execute("INSERT INTO paper_fts(rowid, body) VALUES (?,?)", (i, body))
        for i, e in enumerate(evidence, start=1):
            con.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?)", (i, e["draft_id"], e["paper_id"], e["artifact_id"], e["evidence_class"],
                                                                      e["draft_semantic_sha256"], e["text_indexed"]))
            con.execute("INSERT INTO evidence_fts(rowid, body) VALUES (?,?)", (i, e["body"]))
        for k, v in meta.items():
            con.execute("INSERT INTO meta VALUES (?,?)", (k, v if isinstance(v, str) else canonical_json(v)))
        digest = manifest(con)
        con.execute("INSERT INTO meta VALUES ('manifest_sha256', ?)", (digest,))
        con.execute("INSERT INTO meta VALUES ('state', 'COMPLETE')")
        con.execute("COMMIT")
    finally:
        con.close()
    return digest


def open_readonly(path: Path):
    """Open a published index read-only and check that it is structurally intact and self-consistent; return (con, meta)."""
    require_fts5()
    if not path.is_file():
        raise PolicyRefusal("no published retrieval index; run 'rkb index build'", code="INDEX_NOT_BUILT")
    if path.is_symlink():
        raise IntegrityError("the index file is a link", code="INDEX_CORRUPT")
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise IntegrityError("index failed quick_check", code="INDEX_CORRUPT")
            meta = dict(con.execute("SELECT key, value FROM meta"))
            if meta.get("schema") != INDEX_SCHEMA or meta.get("state") != "COMPLETE" or manifest(con) != meta.get("manifest_sha256"):
                raise IntegrityError("index metadata, state or manifest mismatch", code="INDEX_CORRUPT")
        except BaseException:
            con.close()
            raise
    except sqlite3.DatabaseError as exc:
        raise IntegrityError("index is not a readable SQLite database: " + type(exc).__name__, code="INDEX_CORRUPT") from None
    meta["embedder"] = json.loads(meta["embedder"])
    return con, meta


def leftover_builds(root: Path) -> list[str]:
    d = index_dir(root)
    return sorted(p.name for p in d.glob(".fts.sqlite.building-*")) if d.is_dir() else []


def remove_leftovers(root: Path) -> int:
    n = 0
    for name in leftover_builds(root):
        try:
            os.remove(index_dir(root) / name)
            n += 1
        except OSError:
            pass
    return n
