"""Embedding providers for S09 (offline only).

``EmbeddingProvider`` is the interface a future adapter implements: ``embed_query``, ``embed_passages``, ``model_id``,
``fingerprint`` (build identity), ``dim``, ``query_prefix`` / ``passage_prefix``, ``max_chars``, ``cache_identity`` and
``status``. Two providers exist:

* ``MockHashEmbedder`` (``MOCK_LOCAL_RETRIEVAL``): deterministic feature hashing of words and character trigrams. It exists so
  the vector and hybrid code paths can be tested offline. It is NOT a semantic model; it only rewards shared words and word
  fragments, and no result obtained with it says anything about semantic retrieval quality.
* ``LocalSentenceTransformer``: an optional adapter for a locally supplied ``sentence-transformers`` model (candidate
  ``intfloat/multilingual-e5-small``). It never downloads anything: it needs an existing local model folder and an owner-supplied
  manifest of the SHA-256 of every file in it; it sets the Hugging Face offline switches and loads with ``local_files_only``. Its
  status is ``NOT_CONFIGURED`` without a folder, ``MODEL_FILES_UNVERIFIED`` when the files do not match the manifest exactly and
  ``DEPENDENCY_MISSING`` without the library. E5 models expect the prefixes ``query: `` and ``passage: ``; inputs are truncated
  by the model to 512 tokens.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path

from ..core.errors import PolicyRefusal, ValidationError
from ..evidence.excerpt import words

MOCK_ID = "MOCK_LOCAL_RETRIEVAL"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingProvider:
    kind = "ABSTRACT"
    model_id = ""
    dim = 0
    query_prefix = ""
    passage_prefix = ""
    max_chars = 4000
    semantic = False          # True only for a verified real embedding model

    def status(self) -> dict:
        raise NotImplementedError

    @property
    def fingerprint(self) -> str:
        raise NotImplementedError

    def cache_identity(self) -> dict:
        return {"kind": self.kind, "model_id": self.model_id, "fingerprint": self.fingerprint, "dim": self.dim,
                "query_prefix": self.query_prefix, "passage_prefix": self.passage_prefix, "max_chars": self.max_chars, "semantic": self.semantic}

    def embed_query(self, text: str) -> list[float]:
        return self.embed([self.query_prefix + text[:self.max_chars]])[0]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return self.embed([self.passage_prefix + t[:self.max_chars] for t in texts])

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class MockHashEmbedder(EmbeddingProvider):
    """Deterministic feature hashing (``rkb-mock-hash-embedding/1``): words weight 1, character trigrams of words weight 0.5,
    signed buckets from SHA-256, L2-normalised. Identical on every platform and interpreter. NOT semantic."""
    kind = MOCK_ID
    model_id = "rkb-mock-hash-embedding/1"
    dim = 256

    def status(self) -> dict:
        return {"status": "READY", "kind": self.kind, "semantic": False,
                "note": "deterministic test double; not a semantic model; scores are not evidence of retrieval quality"}

    @property
    def fingerprint(self) -> str:
        return _sha(f"{self.model_id}|dim={self.dim}|words=1.0|trigrams=0.5|sha256-buckets")

    def _features(self, text):
        for w in words(text):
            yield w, 1.0
            padded = f"#{w}#"
            for i in range(len(padded) - 2):
                yield "3:" + padded[i:i + 3], 0.5

    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for feature, weight in self._features(text):
                h = hashlib.sha256(feature.encode("utf-8")).digest()
                vec[int.from_bytes(h[:4], "little") % self.dim] += weight if h[4] & 1 else -weight
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class LocalSentenceTransformer(EmbeddingProvider):
    kind = "LOCAL_SENTENCE_TRANSFORMER"
    query_prefix = "query: "
    passage_prefix = "passage: "
    semantic = True

    def __init__(self, *, model_dir: str | None = None, manifest_file: str | None = None, model_id: str = "intfloat/multilingual-e5-small",
                 revision: str | None = None):
        self.model_dir = Path(model_dir) if model_dir else None
        self.manifest_file = Path(manifest_file) if manifest_file else None
        self.model_id, self.revision = model_id, revision
        self._model = None
        self._manifest = None

    def _verify(self) -> list[str]:
        if not self.model_dir:
            return ["NOT_CONFIGURED"]
        if not self.model_dir.is_dir() or self.model_dir.is_symlink():
            return ["MODEL_DIR_NOT_A_LOCAL_FOLDER"]
        if not self.manifest_file or not self.manifest_file.is_file():
            return ["MODEL_MANIFEST_MISSING"]
        try:
            manifest = json.loads(self.manifest_file.read_text(encoding="utf-8"))
            files = manifest["files"]
            assert isinstance(files, dict) and files and all(isinstance(v, str) and len(v) == 64 for v in files.values())
        except (ValueError, KeyError, AssertionError, OSError):
            return ["MODEL_MANIFEST_INVALID"]
        present = {p.relative_to(self.model_dir).as_posix() for p in self.model_dir.rglob("*") if p.is_file()}
        problems = [f"MODEL_FILE_NOT_IN_MANIFEST:{p}" for p in sorted(present - set(files))]
        for rel, digest in sorted(files.items()):
            path = self.model_dir / rel
            if rel not in present:
                problems.append(f"MODEL_FILE_MISSING:{rel}")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                problems.append(f"MODEL_FILE_HASH_MISMATCH:{rel}")
        self._manifest = manifest
        return problems

    def status(self) -> dict:
        problems = self._verify()
        if problems == ["NOT_CONFIGURED"]:
            return {"status": "NOT_CONFIGURED", "kind": self.kind, "semantic": False, "note": "no owner-approved local model folder supplied"}
        if problems:
            return {"status": "MODEL_FILES_UNVERIFIED", "kind": self.kind, "semantic": False, "problems": problems}
        if importlib.util.find_spec("sentence_transformers") is None:
            return {"status": "DEPENDENCY_MISSING", "kind": self.kind, "semantic": False, "note": "sentence-transformers is not installed"}
        return {"status": "READY", "kind": self.kind, "semantic": True, "model_id": self.model_id, "revision": self.revision}

    @property
    def fingerprint(self) -> str:
        if self._manifest is None:
            self._verify()
        return _sha(json.dumps({"model_id": self.model_id, "revision": self.revision, "files": (self._manifest or {}).get("files")}, sort_keys=True))

    def _load(self):
        st = self.status()
        if st["status"] != "READY":
            raise PolicyRefusal("local embedding model is not usable: " + st["status"], code="EMBEDDER_" + st["status"])
        if self._model is None:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer      # optional dependency, imported only when configured
            self._model = SentenceTransformer(str(self.model_dir), device="cpu", local_files_only=True)
            self.dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def embed(self, texts):
        vectors = self._load().encode(list(texts), normalize_embeddings=True, convert_to_numpy=False, show_progress_bar=False)
        return [[float(x) for x in v] for v in vectors]


def provider(kind: str | None, **config) -> EmbeddingProvider | None:
    """``none`` / None: lexical only; ``mock``: MockHashEmbedder; ``local-model``: LocalSentenceTransformer (must be READY)."""
    if kind in (None, "none"):
        return None
    if kind == "mock":
        return MockHashEmbedder()
    if kind == "local-model":
        p = LocalSentenceTransformer(**config)
        st = p.status()
        if st["status"] != "READY":
            raise PolicyRefusal("local embedding model not usable: " + st["status"], code="EMBEDDER_" + st["status"])
        return p
    raise ValidationError("embedder: none, mock or local-model", code="EMBEDDER_INVALID")
