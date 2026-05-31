"""
index.py — document-frequency indexes that turn "matched" into "rare match".

The whole router hinges on rarity: a term matching a chunk only matters if that
term is *distinctive*. We measure distinctiveness with inverse document
frequency (IDF) over the chunk corpus — a token in 1 of 30 chunks scores high,
a token in 20 of 30 scores near zero.

We also keep inverted indexes (token -> chunk ids) so exact identifier/phrase
lookups are O(1) instead of scanning every chunk.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .chunks import ContextChunk
from .extract import COMMON_WORDS


@dataclass
class RetrievalIndexes:
    n_chunks: int = 0
    term_doc_freq: dict[str, int] = field(default_factory=dict)
    phrase_doc_freq: dict[str, int] = field(default_factory=dict)
    identifier_doc_freq: dict[str, int] = field(default_factory=dict)
    chunks_by_identifier: dict[str, list[str]] = field(default_factory=dict)
    chunks_by_phrase: dict[str, list[str]] = field(default_factory=dict)
    chunks_by_term: dict[str, list[str]] = field(default_factory=dict)

    # --- rarity scores (higher = rarer = stronger evidence) ---------------
    def term_idf(self, term: str) -> float:
        df = self.term_doc_freq.get(term.lower(), 0)
        if df == 0:
            return math.log((self.n_chunks + 1) / 1.0)
        return math.log((self.n_chunks + 1) / (df + 1))

    def identifier_idf(self, ident: str) -> float:
        df = self.identifier_doc_freq.get(ident.lower(), 0)
        if df == 0:
            return 0.0
        return math.log((self.n_chunks + 1) / (df + 1)) + 1.0

    def phrase_idf(self, phrase: str) -> float:
        df = self.phrase_doc_freq.get(phrase.lower(), 0)
        if df == 0:
            return 0.0
        return math.log((self.n_chunks + 1) / (df + 1)) + 0.5

    def max_term_idf(self) -> float:
        return math.log((self.n_chunks + 1) / 1.0)

    def is_common(self, term: str) -> bool:
        """A term is 'common' if it's on the blocklist or appears in >40% of chunks."""
        t = term.lower()
        if t in COMMON_WORDS:
            return True
        if self.n_chunks == 0:
            return False
        return self.term_doc_freq.get(t, 0) / self.n_chunks > 0.40


def build_indexes(chunks: list[ContextChunk]) -> RetrievalIndexes:
    idx = RetrievalIndexes(n_chunks=len(chunks))
    tdf: dict[str, int] = defaultdict(int)
    pdf: dict[str, int] = defaultdict(int)
    idf: dict[str, int] = defaultdict(int)
    by_id: dict[str, list[str]] = defaultdict(list)
    by_ph: dict[str, list[str]] = defaultdict(list)
    by_tm: dict[str, list[str]] = defaultdict(list)

    for c in chunks:
        for t in set(x.lower() for x in c.terms):
            tdf[t] += 1
            by_tm[t].append(c.id)
        for p in set(x.lower() for x in c.phrases):
            pdf[p] += 1
            by_ph[p].append(c.id)
        for i in set(x.lower() for x in c.identifiers):
            idf[i] += 1
            by_id[i].append(c.id)

    idx.term_doc_freq = dict(tdf)
    idx.phrase_doc_freq = dict(pdf)
    idx.identifier_doc_freq = dict(idf)
    idx.chunks_by_identifier = dict(by_id)
    idx.chunks_by_phrase = dict(by_ph)
    idx.chunks_by_term = dict(by_tm)
    return idx
