"""Bounded exact cosine similarity over stored float32 vectors. Backends: ``numpy`` (optional dependency, used when importable)
and ``python`` (always available). Scores are rounded to 6 decimals and ties broken by id, so both backends rank identically.
An optional ``sqlite-vec`` backend could implement ``top_k`` behind the same signature; it is not required and not shipped."""
from __future__ import annotations

import importlib.util
import math
import struct

MAX_VECTORS = 500_000


def pack(vec) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def available_backends() -> list[str]:
    return (["numpy"] if importlib.util.find_spec("numpy") is not None else []) + ["python"]


def top_k(query, items, k: int, *, backend: str | None = None) -> list[tuple[str, float]]:
    """``items``: list of (id, blob, dim). Returns [(id, cosine)] best first; ties by id."""
    if len(items) > MAX_VECTORS:
        raise ValueError("too many vectors for exact search")
    backend = backend or available_backends()[0]
    if not items:
        return []
    if backend == "numpy":
        import numpy as np                                        # optional
        q = np.asarray(query, dtype=np.float64)
        q = q / (np.linalg.norm(q) or 1.0)
        mat = np.frombuffer(b"".join(b for _, b, _ in items), dtype="<f4").reshape(len(items), items[0][2]).astype(np.float64)
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1.0
        scores = [round(float(s), 6) for s in (mat @ q) / norms]
    elif backend == "python":
        qn = math.sqrt(sum(v * v for v in query)) or 1.0
        scores = []
        for _, blob, dim in items:
            v = unpack(blob, dim)
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            scores.append(round(sum(a * b for a, b in zip(query, v)) / (qn * n), 6))
    else:
        raise ValueError("unknown vector backend")
    ranked = sorted(zip((i for i, _, _ in items), scores), key=lambda t: (-t[1], t[0]))
    return ranked[:k]
