"""
router.py — combine signals, score chunks, choose a context level.

Pipeline:
    query
      -> extract terms / phrases / identifiers
      -> exact identifier + phrase lookup (inverted index)
      -> rarity (IDF) scoring of matched terms
      -> vector similarity (pluggable embedder)
      -> per-chunk combined score
      -> pick context level (none | tiny | selected | full)

Scoring formula (per chunk):
    score = 0.40*vec + 0.30*rareTerm + 0.25*phraseOrIdent
            + 0.10*recency - 0.15*commonTermPenalty

The routing *decision* layers rules on top of the raw scores: a single
rare identifier match (e.g. ``origin/main``) is enough for "selected" even if
vector similarity is mediocre, while a generic definition question is pushed
toward "none"/"tiny" regardless of incidental overlap.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .chunks import ContextChunk, load_seed_chunks
from .embeddings import make_embedder
from .extract import extract, Extracted
from .index import RetrievalIndexes, build_indexes

log = logging.getLogger("context_router")


# --- tunable weights --------------------------------------------------------
W_VEC = 0.40
W_RARE_TERM = 0.30
W_PHRASE_IDENT = 0.25
W_RECENCY = 0.10
W_COMMON_PENALTY = 0.15

# Level thresholds on the top chunk's combined score.
T_SELECTED = 0.30     # at/above -> at least "selected"
T_TINY = 0.12         # between T_TINY and T_SELECTED -> weak; tiny unless rule fires
RECENCY_HALFLIFE_DAYS = 14.0

# Per-match-type inclusion floors.
FLOORS: dict[str, float] = {
    "identifier": 0.18,
    "phrase":     0.18,
    "semantic":   0.20,
    "uncertain":  T_TINY,
}

# Relative cutoff: a chunk must also score at least this fraction of the top
# chunk's score to be kept. Value 0.55 maximises F1 on the eval set.
# 0.0 disables the relative cutoff entirely.
REL_CUTOFF = 0.55

# Definition / generic question patterns -> bias toward none/tiny.
_DEFINITION_RE = re.compile(
    r"^\s*(what\s+(is|are|does|do)\b|what'?s\b|define\b|meaning\s+of\b|"
    r"explain\s+(what|the\s+(term|word|concept))\b|"
    r"\w+\s+vs\.?\s+\w+\s*\??$)",
    re.IGNORECASE,
)
# Short anaphoric follow-ups ("explain that simpler", "why is it slow?").
_FOLLOWUP_RE = re.compile(
    r"\b(that|this|it|these|those|again|simpler|the same)\b", re.IGNORECASE
)


@dataclass
class ChunkScore:
    chunk_id: str
    score: float
    vec: float
    rare_term: float
    phrase_ident: float
    recency: float
    common_penalty: float
    matched_identifiers: list[str] = field(default_factory=list)
    matched_phrases: list[str] = field(default_factory=list)
    matched_rare_terms: list[str] = field(default_factory=list)


@dataclass
class RouteResult:
    context_level: str            # none | tiny | selected | full
    selected_chunk_ids: list[str]
    confidence: float
    reason: str
    scores: list[ChunkScore] = field(default_factory=list)
    match_type: str = ""
    applied_floor: float = 0.0
    dropped_chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "context_level": self.context_level,
            "selected_chunk_ids": self.selected_chunk_ids,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
        }


class ContextRouter:
    def __init__(
        self, chunks: list[ContextChunk] | None = None, *, rel_cutoff: float = REL_CUTOFF
    ) -> None:
        self.chunks: list[ContextChunk] = chunks if chunks is not None else load_seed_chunks()
        self.by_id: dict[str, ContextChunk] = {c.id: c for c in self.chunks}
        self.index: RetrievalIndexes = build_indexes(self.chunks)
        self.embedder = make_embedder(self.chunks)
        self.rel_cutoff: float = rel_cutoff

    def _eff_floor(self, base_floor: float, top: "ChunkScore | None") -> float:
        if top is None or self.rel_cutoff <= 0:
            return base_floor
        return max(base_floor, self.rel_cutoff * top.score)

    def _recency(self, chunk: ContextChunk, now: float) -> float:
        import time
        age_days = max(0.0, (now - chunk.last_used_at) / 86400.0)
        return 0.5 ** (age_days / RECENCY_HALFLIFE_DAYS)

    def _score_chunk(self, chunk: ContextChunk, q: Extracted, query: str, now: float) -> ChunkScore:
        idx = self.index
        c_terms = {t.lower() for t in chunk.terms}
        c_phrases = {p.lower() for p in chunk.phrases}
        c_idents = {i.lower() for i in chunk.identifiers}

        matched_idents = [i for i in q.identifiers if i.lower() in c_idents]
        matched_phrases = [p for p in q.phrases if p.lower() in c_phrases]
        ident_score = sum(idx.identifier_idf(i) for i in matched_idents)
        phrase_score = sum(idx.phrase_idf(p) for p in matched_phrases)
        phrase_ident = min(1.0, (ident_score + phrase_score) / 3.0)

        matched_terms = [t for t in q.terms if t.lower() in c_terms]
        max_idf = idx.max_term_idf() or 1.0
        rare_sum = 0.0
        rare_hits: list[str] = []
        for t in matched_terms:
            if idx.is_common(t):
                continue
            w = idx.term_idf(t) / max_idf
            rare_sum += w
            if w > 0.35:
                rare_hits.append(t)
        rare_term = min(1.0, rare_sum / 2.0)

        vec = max(0.0, min(1.0, self.embedder.similarity(query, chunk.text)))
        recency = self._recency(chunk, now)

        if matched_terms:
            common_frac = sum(1 for t in matched_terms if idx.is_common(t)) / len(matched_terms)
        else:
            common_frac = 0.0
        common_penalty = common_frac if (rare_term < 0.1 and phrase_ident < 0.1) else 0.0

        score = (
            W_VEC * vec
            + W_RARE_TERM * rare_term
            + W_PHRASE_IDENT * phrase_ident
            + W_RECENCY * recency
            - W_COMMON_PENALTY * common_penalty
        )
        score *= 0.75 + 0.5 * chunk.importance

        return ChunkScore(
            chunk_id=chunk.id,
            score=round(score, 4),
            vec=round(vec, 4),
            rare_term=round(rare_term, 4),
            phrase_ident=round(phrase_ident, 4),
            recency=round(recency, 4),
            common_penalty=round(common_penalty, 4),
            matched_identifiers=matched_idents,
            matched_phrases=matched_phrases,
            matched_rare_terms=rare_hits,
        )

    def route(self, query: str) -> RouteResult:
        import time
        now = time.time()
        q = extract(query)

        scores = sorted(
            (self._score_chunk(c, q, query, now) for c in self.chunks),
            key=lambda s: s.score,
            reverse=True,
        )
        top = scores[0] if scores else None

        is_definition = bool(_DEFINITION_RE.search(query.strip()))
        is_followup = bool(_FOLLOWUP_RE.search(query)) and len(q.terms) <= 4
        has_rare_ident = bool(top and top.matched_identifiers)
        has_rare_phrase = bool(top and top.matched_phrases)

        log.debug(
            "route start q=%r terms=%s idents=%s phrases=%s | top=%s score=%.3f | "
            "definition=%s followup=%s",
            query, q.terms, q.identifiers, q.phrases,
            top.chunk_id if top else None, top.score if top else 0.0,
            is_definition, is_followup,
        )

        if is_definition and not has_rare_ident:
            level = "tiny" if (top and top.score >= T_TINY) else "none"
            return self._mk(
                query, level, [], 0.6,
                "Generic/definition question; no distinctive project identifier.",
                scores, match_type="definition",
            )

        if has_rare_ident or has_rare_phrase:
            match_type = "identifier" if has_rare_ident else "phrase"
            floor = self._eff_floor(FLOORS[match_type], top)
            sel, dropped = self._collect(scores, limit=5, floor=floor, match_type=match_type)
            hit = (top.matched_identifiers or top.matched_phrases)[0]
            level = "full" if (top.score >= T_SELECTED and len(sel) >= 4) else "selected"
            conf = min(0.95, 0.7 + 0.25 * min(1.0, top.score / T_SELECTED))
            return self._mk(
                query, level, sel, conf, f"Matched rare {match_type} '{hit}'.",
                scores, match_type=match_type, floor=floor, dropped=dropped,
            )

        if is_followup and (top is None or top.score < T_SELECTED):
            return self._mk(
                query, "tiny", [], 0.55,
                "Short follow-up; relies on recent turns, not stored context.",
                scores, match_type="followup",
            )

        if top and top.score >= T_SELECTED:
            floor = self._eff_floor(FLOORS["semantic"], top)
            sel, dropped = self._collect(scores, limit=5, floor=floor, match_type="semantic")
            return self._mk(
                query, "selected", sel, min(0.8, 0.5 + top.score),
                "Strong relevance but no exact identifier; selected, kept small.",
                scores, match_type="semantic", floor=floor, dropped=dropped,
            )

        if top and top.score >= T_TINY:
            floor = FLOORS["uncertain"]
            sel, dropped = self._collect(scores, limit=2, floor=floor, match_type="uncertain")
            return self._mk(
                query, "selected", sel, 0.4,
                "Uncertain match; using a small amount of context.",
                scores, match_type="uncertain", floor=floor, dropped=dropped,
            )

        return self._mk(
            query, "none", [], 0.5,
            "No relevant or distinctive context found.", scores, match_type="none",
        )

    def _collect(
        self, scores: list[ChunkScore], *, limit: int, floor: float, match_type: str
    ) -> tuple[list[str], list[str]]:
        kept: list[str] = []
        dropped: list[str] = []
        for s in scores[:limit]:
            (kept if s.score >= floor else dropped).append(s.chunk_id)
        if dropped:
            log.debug(
                "collect[%s] floor=%.3f kept=%s dropped_below_floor=%s",
                match_type, floor, kept,
                [(cid, round(s.score, 3)) for s in scores[:limit]
                 for cid in [s.chunk_id] if s.score < floor],
            )
        return kept, dropped

    def _mk(
        self, query: str, level: str, sel: list[str], conf: float, reason: str,
        scores: list[ChunkScore], *, match_type: str = "", floor: float = 0.0,
        dropped: list[str] | None = None,
    ) -> RouteResult:
        res = RouteResult(
            level, sel, conf, reason, scores,
            match_type=match_type, applied_floor=floor, dropped_chunk_ids=dropped or [],
        )
        log.info(
            "route q=%r -> level=%s conf=%.2f match=%s floor=%.3f sel=%s dropped=%s",
            query, level, conf, match_type, floor, sel, res.dropped_chunk_ids,
        )
        return res

    def explain(self, query: str, top_n: int = 5) -> str:
        res = self.route(query)
        lines = [
            f"Q: {query}",
            f"-> level={res.context_level}  conf={res.confidence:.2f}  "
            f"match={res.match_type}  floor={res.applied_floor:.2f}",
            f"   reason: {res.reason}",
            f"   selected: {res.selected_chunk_ids or '[]'}",
            f"   dropped (below floor): {res.dropped_chunk_ids or '[]'}",
            "   top chunks:",
        ]
        for s in res.scores[:top_n]:
            bits = []
            if s.matched_identifiers:
                bits.append(f"id={s.matched_identifiers}")
            if s.matched_phrases:
                bits.append(f"ph={s.matched_phrases}")
            if s.matched_rare_terms:
                bits.append(f"rare={s.matched_rare_terms}")
            lines.append(
                f"     {s.score:+.3f} {s.chunk_id:<24} "
                f"vec={s.vec:.2f} rare={s.rare_term:.2f} "
                f"pi={s.phrase_ident:.2f} rec={s.recency:.2f} "
                f"{' '.join(bits)}"
            )
        return "\n".join(lines)
