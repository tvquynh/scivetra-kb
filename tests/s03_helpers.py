"""S03 test helpers: synthetic portable exports, the frozen native Reprovane 2.0.1 fixture, schema-1 KBs."""
import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

from tests.helpers import FIXTURES, KBTest
from scivetra_kb.core.canonical import canonical_json, sha256_bytes
from scivetra_kb.core.db import DB_RELATIVE, KB, _configure
from scivetra_kb.core.migrations import _statements, packaged_migrations

NATIVE = FIXTURES / "rv_native_2_0_1"
NATIVE_FILES = ("SHA256SUMS.txt", "corpus.csv", "corpus.jsonl", "manifest.json")


def tree_hashes(directory) -> dict:
    root = Path(directory)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def copy_native(tmp, release="s03-r1") -> str:
    """Work on a copy of the frozen native export, never on the fixture itself."""
    target = os.path.join(tmp, "native-" + release)
    shutil.copytree(NATIVE / release, target)
    return target


def s03_item(cid, *, dois=(), openalex=(), work_type="article", title=None, venue="Synthetic Journal", entity=None, author="Ada Synthetic",
             extra_identifiers=(), integrity="ABSENT"):
    """Portable-export item in the native Reprovane 2.0.1 shape (subset). ``dois`` = [(original, rv_normalized)]."""
    fields = {"title": [{"observation_id": "obs-t", "value": title or f"Synthetic title {cid}"}],
              "work_type": [{"observation_id": "obs-w", "value": work_type}],
              "venue": [{"observation_id": "obs-v", "title": venue}] if venue else [],
              "authors": [{"author_display_name": author, "author_family_name": None, "author_position": 1}] if author else []}
    if integrity != "ABSENT":
        fields["integrity"] = integrity
    identifiers = [{"provider_record_id": "prr_1", "namespace": "doi", "original": o, "value": v, "observation_id": f"obs-d{i}"}
                   for i, (o, v) in enumerate(dois)]
    identifiers += [{"provider_record_id": "prr_1", "namespace": ns, "original": v, "value": v, "observation_id": f"obs-x{i}"}
                    for i, (ns, v) in enumerate(extra_identifiers)]
    entity = entity or ("pub_" + hashlib.sha256(cid.encode()).hexdigest()[:32])
    return {"corpus_item_id": cid, "publication_entity_id": entity, "release_id": "crl_s03",
            "identity": {"publication_entity_id": entity, "normalized_dois": sorted({v for _, v in dois if v}), "openalex_work_ids": list(openalex),
                         "provider_identifiers": identifiers},
            "metadata": {"records": [{"provider_record_id": "prr_1", "source": {"provider": "openalex"}, "fields": fields}]},
            "item_sha256": hashlib.sha256(canonical_json([cid, list(dois), list(openalex), work_type, title]).encode()).hexdigest(), "ordinal": 0}


def write_export(directory, items, *, release_id="crl_s03a", release_semantic=None, jsonl_override=None, manifest_extra=None):
    """A self-consistent synthetic portable export (KB-built, NOT a native Reprovane export)."""
    os.makedirs(directory, exist_ok=True)
    jsonl = jsonl_override if jsonl_override is not None else b"".join((canonical_json(i) + "\n").encode("utf-8") for i in items)
    csv = b"corpus_item_id,doi\n" + b"".join(f"{i['corpus_item_id']},[]\n".encode() for i in items)
    inventory = [{"name": "corpus.csv", "sha256": sha256_bytes(csv), "bytes": len(csv)},
                 {"name": "corpus.jsonl", "sha256": sha256_bytes(jsonl), "bytes": len(jsonl)}]
    manifest = {"schema": "corpus-portable/1", "release_id": release_id, "release_label": release_id,
                "release_semantic_manifest_sha256": release_semantic or sha256_bytes(release_id.encode()), "item_count": len(items),
                "file_inventory": inventory, **(manifest_extra or {})}
    files = {"corpus.csv": csv, "corpus.jsonl": jsonl, "manifest.json": (canonical_json(manifest) + "\n").encode("utf-8")}
    for name, data in files.items():
        Path(directory, name).write_bytes(data)
    Path(directory, "SHA256SUMS.txt").write_bytes("".join(f"{sha256_bytes(d)}  {n}\n" for n, d in sorted(files.items())).encode())
    return directory


def make_schema1_kb(root, *, clock, ids) -> None:
    """An S02 schema-1 KB: only migration 0001 applied (exact packaged bytes), ledger version 1, instance row."""
    os.makedirs(os.path.join(root, "db"))
    os.makedirs(os.path.join(root, "cas", "sha256"))
    connection = sqlite3.connect(os.path.join(root, DB_RELATIVE), isolation_level=None)
    try:
        _configure(connection)
        connection.execute("PRAGMA journal_mode = DELETE")
        version, name, digest, script = packaged_migrations()[0]
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE schema_migration (version INTEGER PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at_utc TEXT NOT NULL)")
        for statement in _statements(script):
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migration VALUES (?,?,?,?)", (version, name, digest, clock.now_utc_text()))
        connection.execute("PRAGMA user_version = 1")
        connection.execute("INSERT INTO kb_instance VALUES (?,?,?)", (ids.new("kbi"), clock.now_utc_text(), "0.2.0.dev3"))
        connection.execute("COMMIT")
    finally:
        connection.close()


def schema1_writer(root, *, clock, ids):
    from scivetra_kb.core.db import open_writer
    return open_writer(root, clock=clock, ids=ids, _allow_older_schema=True)


def populate_s02(kb, tmp, test: KBTest) -> None:
    """Representative S02 content in a schema-1 KB: Crossref fetch with retraction, DataCite, S02 RV import, counter-evidence."""
    from tests.helpers import cr_work, dc_doi, openalex_retraction_claim, resp, rv_item, structured, write_rv_export
    from scivetra_kb.metadata.integrity import IntegrityService
    from scivetra_kb.metadata.rv_import import import_corpus_export
    from scivetra_kb.metadata.store import MetadataStore
    from scivetra_kb.metadata.transport import FixtureTransport
    ft = FixtureTransport()
    test.route_ra(ft, "10.99999/s1.cr")
    test.add_crossref(ft, "10.99999/s1.cr", cr_work("10.99999/s1.cr", title=["Schema one paper"],
                                                    **{"updated-by": [{"DOI": "10.99999/s1.notice", "type": "retraction", "source": "publisher",
                                                                       "updated": {"date-parts": [[2025, 1, 2]]}}]}))
    test.route_ra(ft, "10.99999/s1.dc", "DataCite")
    test.add_datacite(ft, "10.99999/s1.dc", dc_doi("10.99999/s1.dc", titles=[{"title": "Schema one dataset"}]))
    service = test.service(kb, ft)
    for doi in ("10.99999/s1.cr", "10.99999/s1.dc"):       # doiRA lookups are batched per call; one DOI per call matches route_ra
        assert service.fetch([structured(doi)])[0]["status"] == "FOUND"
    export = write_rv_export(os.path.join(tmp, "s1rv"), [rv_item("cri_s1", dois=[("10.99999/s1.rv", "10.99999/s1.rv")],
                                                                 integrity=openalex_retraction_claim())])
    import_corpus_export(kb, export, workspace_label="s1")
    integrity = IntegrityService(MetadataStore(kb))
    event = kb.one("SELECT event_id FROM integrity_event ORDER BY rowid LIMIT 1")["event_id"]
    assert kb.one("SELECT count(*) n FROM metadata_claim")["n"] > 0
    with kb.transaction():
        text = "Synthetic notice: the retraction was issued in error."
        doc = integrity.register_document("reviewer.a", text.encode(), "synthetic notice")
        start = text.index("issued in error")
        integrity.record_counter_evidence(operator="reviewer.a", opposes_event_id=event, kind="NOTICE_STATES_ERROR",
                                          primary_document_ref="sha256:" + doc["document_sha256"],
                                          evidence_anchor={"document_sha256": doc["document_sha256"], "page": 1, "char_start": start,
                                                           "char_end": start + 15}, statement_summary="error notice")


__all__ = ["NATIVE", "NATIVE_FILES", "tree_hashes", "copy_native", "s03_item", "write_export", "make_schema1_kb", "schema1_writer",
           "populate_s02", "KB"]
