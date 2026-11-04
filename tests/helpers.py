"""Shared test helpers and synthetic fixture builders (no real DOIs, papers or provider data)."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from scivetra_kb import cli  # noqa: E402
from scivetra_kb.core.canonical import canonical_json, sha256_bytes  # noqa: E402
from scivetra_kb.core.db import init_kb, open_reader, open_writer  # noqa: E402
from scivetra_kb.core.ids import FixedClock, SequentialIdProvider  # noqa: E402
from scivetra_kb.metadata import crossref, datacite, ra  # noqa: E402
from scivetra_kb.metadata.doi import (DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT, STRUCTURED_PROVIDER_FIELD,  # noqa: E402
                                      parse_doi)
from scivetra_kb.metadata.service import MetadataService  # noqa: E402
from scivetra_kb.metadata.transport import FixtureTransport, Response  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
JSON_HEADERS = {"content-type": "application/json"}


def structured(doi):
    return parse_doi(doi, DOI_IDENTIFIER_FIELD, STRUCTURED_PROVIDER_FIELD)


def operator(doi):
    return parse_doi(doi, DOI_IDENTIFIER_FIELD, OPERATOR_OR_TABULAR_INPUT)


def resp(status, body=b"", url="", headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode("utf-8")
    elif isinstance(body, str):
        body = body.encode("utf-8")
    return Response(status, dict(headers if headers is not None else JSON_HEADERS), body, url)


def cr_work(doi, **message):
    return {"status": "ok", "message-type": "work", "message-version": "1.0.0", "message": {"DOI": doi, **message}}


def dc_doi(doi, **attributes):
    return {"data": {"id": doi.lower(), "type": "dois", "attributes": {"doi": doi.lower(), **attributes}, "relationships": {}}}


class KBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rkb-test-")
        self.root = os.path.join(self.tmp, "kb")
        self.clock = FixedClock()
        self.ids = SequentialIdProvider(self.clock)
        init_kb(self.root, clock=self.clock, ids=self.ids)
        self.sleeps = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def writer(self):
        return open_writer(self.root, clock=self.clock, ids=self.ids)

    def reader(self):
        return open_reader(self.root, clock=self.clock, ids=self.ids)

    def service(self, kb, transport, **kw):
        return MetadataService(kb, transport, sleeper=self.sleeps.append, jitter=lambda: 0.0, **kw)

    # -- transport builders --------------------------------------------------
    @staticmethod
    def route_ra(ft, doi, ra_value="Crossref"):
        url = ra.build_url([doi])
        ft.add(url, resp(200, [{"DOI": doi, "RA": ra_value}], url))

    @staticmethod
    def add_crossref(ft, doi, *bodies):
        url = crossref.build_url(doi)
        for body in bodies:
            ft.add(url, body if isinstance(body, Response) else resp(200, body, url))
        return url

    @staticmethod
    def add_datacite(ft, doi, *bodies):
        url = datacite.build_url(doi)
        for body in bodies:
            ft.add(url, body if isinstance(body, Response) else resp(200, body, url))
        return url

    def run_cli(self, *argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(list(argv))
        return code, json.loads(buffer.getvalue())


# -- synthetic Reprovane portable export -----------------------------------------
def rv_item(corpus_item_id, dois=(), openalex=(), integrity="ABSENT", entity="pub_" + "0" * 32):
    """``dois`` = [(original, rv_normalized_or_None)]. ``integrity``: "ABSENT" (not projected), None, [] or claim list."""
    fields = {"title": [{"observation_id": "obs-t", "value": "Synthetic title"}]}
    if integrity != "ABSENT":
        fields["integrity"] = integrity
    return {
        "corpus_item_id": corpus_item_id, "release_id": "crl_synthetic", "publication_entity_id": entity,
        "identity": {"publication_entity_id": entity, "normalized_dois": sorted({v for _, v in dois if v}),
                     "openalex_work_ids": list(openalex),
                     "provider_identifiers": [{"provider_record_id": "prr_1", "namespace": "doi", "original": o, "value": v,
                                               "observation_id": f"obs-d{i}"} for i, (o, v) in enumerate(dois)]},
        "metadata": {"records": [{"provider_record_id": "prr_1", "source": {"provider": "openalex"}, "fields": fields}]},
        "item_sha256": "0" * 64, "ordinal": 0,
    }


def write_rv_export(directory, items, *, release_semantic="a" * 64):
    os.makedirs(directory, exist_ok=True)
    jsonl = b"".join((canonical_json(i) + "\n").encode("utf-8") for i in items)
    csv = b"corpus_item_id,doi\n" + b"".join(f"{i['corpus_item_id']},[]\n".encode() for i in items)
    inventory = [{"name": "corpus.csv", "sha256": sha256_bytes(csv), "bytes": len(csv)},
                 {"name": "corpus.jsonl", "sha256": sha256_bytes(jsonl), "bytes": len(jsonl)}]
    manifest = (canonical_json({"schema": "corpus-portable/1", "release_id": "crl_synthetic", "release_semantic_manifest_sha256": release_semantic,
                                "item_count": len(items), "file_inventory": inventory}) + "\n").encode("utf-8")
    files = {"corpus.csv": csv, "corpus.jsonl": jsonl, "manifest.json": manifest}
    for name, data in files.items():
        Path(directory, name).write_bytes(data)
    Path(directory, "SHA256SUMS.txt").write_bytes("".join(f"{sha256_bytes(d)}  {n}\n" for n, d in sorted(files.items())).encode())
    return directory


def openalex_retraction_claim():
    return [{"observation_id": "obs-i1", "event_type": "RETRACTION", "status_code": "REPORTED_BY_OPENALEX",
             "subject_identifier_namespace": "openalex_work_id", "source_field_path": "is_retracted"}]
