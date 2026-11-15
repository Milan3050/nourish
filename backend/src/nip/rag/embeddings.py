"""Embedding providers behind one interface.

- ollama: local, default for development (`ollama pull nomic-embed-text`).
- fastembed: local ONNX models, no server needed (`uv sync --extra local-embeddings`).
- hash: deterministic feature hashing. Only for tests and CI where no model is available; it is
  lexical, so evals run with it measure the pipeline, not semantic retrieval quality.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

import httpx

from nip.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashEmbedder:
    def __init__(self, dim: int = 768):
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = re.findall(r"[a-z0-9]+", text.lower())
        grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
        for g in grams:
            h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) else -1.0
        return _normalise(v)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._one(text)


class OllamaEmbedder:
    """Ollama /api/embed. nomic-embed-text expects task prefixes for best quality."""

    def __init__(self, model: str, base_url: str, dim: int):
        self.model = model
        self.dim = dim
        self.url = base_url.removesuffix("/v1").rstrip("/") + "/api/embed"
        self.prefix = "nomic" in model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=120) as c:
            for i in range(0, len(texts), 32):
                r = c.post(self.url, json={"model": self.model, "input": texts[i:i + 32]})
                r.raise_for_status()
                out += r.json()["embeddings"]
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([f"search_document: {t}" if self.prefix else t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"search_query: {text}" if self.prefix else text])[0]


class FastEmbedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding  # optional dependency

        self._m = TextEmbedding(model)
        self.dim = len(next(iter(self._m.embed(["probe"]))))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._m.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._m.query_embed(text)))]


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embeddings_provider == "hash":
        return HashEmbedder(s.embeddings_dim)
    if s.embeddings_provider == "fastembed":
        return FastEmbedder(s.embeddings_model)
    return OllamaEmbedder(s.embeddings_model, s.embeddings_base_url, s.embeddings_dim)
