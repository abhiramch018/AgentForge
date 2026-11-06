"""Local embeddings for memory retrieval.

Default ``HashingEmbedder`` is deterministic and offline — suitable for tests
and demos. It does **not** claim real semantic quality. Swap in a provider
embedder later without changing the store schema.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingClient(ABC):
    """Embed text into a fixed-dimension vector."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    @property
    @abstractmethod
    def dimensions(self) -> int:
        raise NotImplementedError


_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


class HashingEmbedder(EmbeddingClient):
    """Feature-hash bag-of-tokens embedder (no network, no paid calls)."""

    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions < 8:
            raise ValueError("dimensions must be >= 8.")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dimensions
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            tokens = ["_empty"]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        return _l2_normalize(vec)


class CachingEmbedder(EmbeddingClient):
    """Avoid re-embedding unchanged content (keyed by sha256 of text)."""

    def __init__(self, inner: EmbeddingClient) -> None:
        self._inner = inner
        self._cache: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.embed_ms = 0.0

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed(self, text: str) -> list[float]:
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.hits += 1
                return list(cached)
        started = time.perf_counter()
        vector = self._inner.embed(text)
        elapsed = (time.perf_counter() - started) * 1000
        with self._lock:
            self.misses += 1
            self.embed_ms += elapsed
            self._cache[key] = list(vector)
        return list(vector)

    def reset_stats(self) -> None:
        with self._lock:
            self.hits = 0
            self.misses = 0
            self.embed_ms = 0.0


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]
