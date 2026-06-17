"""
embeddings.py — pluggable semantic-similarity backend.

The plan weights similarity at 0.40. The DEFAULT backend is the zero-dependency
*lexical* backend: IDF-weighted bag-of-words vectors compared with cosine.
An optional sentence-transformers backend can be forced with:
    CONTEXT_ROUTER_EMBEDDER=sentence-transformers
The interface is identical, so the router doesn't care which backend is active.
"""

from __future__ import annotations

import math
import os
from collections import Counter

from .chunks import ContextChunk
from .extract import STOPWORDS, _WORD_RE


def _tokenize(text: str) -> list[str]:
    return [
        w.strip("'") for w in _WORD_RE.findall(text.lower())
        if len(w.strip("'")) >= 2 and w.strip("'") not in STOPWORDS
    ]


class LexicalEmbedder:
    """IDF-weighted bag-of-words vectors + cosine. No external dependencies."""

    name = "lexical"

    def __init__(self, chunks: list[ContextChunk]) -> None:
        n = max(1, len(chunks))
        df: Counter[str] = Counter()
        for c in chunks:
            for tok in set(_tokenize(c.text)):
                df[tok] += 1
        self._idf: dict[str, float] = {
            tok: math.log((n + 1) / (d + 1)) + 1.0 for tok, d in df.items()
        }
        self._default_idf = math.log((n + 1) / 1.0) + 1.0

    def _vec(self, text: str) -> dict[str, float]:
        tf = Counter(_tokenize(text))
        return {
            tok: count * self._idf.get(tok, self._default_idf)
            for tok, count in tf.items()
        }

    def similarity(self, query: str, chunk_text: str) -> float:
        a = self._vec(query)
        b = self._vec(chunk_text)
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0


class SentenceTransformerEmbedder:
    """Real dense embeddings via sentence-transformers (optional)."""

    name = "sentence-transformers"

    def __init__(self, chunks: list[ContextChunk], model: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        import numpy as np  # type: ignore

        self._np = np
        self._model = SentenceTransformer(model)
        self._cache: dict[str, "np.ndarray"] = {}
        for c in chunks:
            self._cache[c.text] = self._encode(c.text)

    def _encode(self, text: str):
        v = self._cache.get(text)
        if v is None:
            v = self._model.encode(text, normalize_embeddings=True)
            self._cache[text] = v
        return v

    def similarity(self, query: str, chunk_text: str) -> float:
        qv = self._encode(query)
        cv = self._encode(chunk_text)
        return float(self._np.dot(qv, cv))


def make_embedder(chunks: list[ContextChunk]):
    """Return the embedder selected by CONTEXT_ROUTER_EMBEDDER.

    Default: lexical matching with no external model dependency. Force the
    optional sentence-transformers backend with
    CONTEXT_ROUTER_EMBEDDER=sentence-transformers.
    """
    choice = os.getenv("CONTEXT_ROUTER_EMBEDDER", "lexical").lower()
    if choice in ("lexical", "bow", "tfidf"):
        return LexicalEmbedder(chunks)
    try:
        return SentenceTransformerEmbedder(chunks)
    except Exception as exc:
        print(f"[context_router] sentence-transformers unavailable ({exc}); "
              f"using lexical embedder.")
        return LexicalEmbedder(chunks)
