"""
core/context_router — Intelligent context-level routing for memory retrieval.

Decides how much stored context to feed the LLM based on the query:
    none     — generic/definition question; skip memory entirely
    tiny     — short follow-up; include at most 1 fact
    selected — project-specific query; include the most relevant facts
    full     — highly distinctive identifier match; include full relevant set

This package is promoted from experiments/context_router/ and wired into
core/memory_store so that every LLM call gets the right amount of context,
not just a fixed top-k regardless of what was asked.
"""

from .chunks import ContextChunk, load_seed_chunks
from .router import ContextRouter, RouteResult

__all__ = ["ContextChunk", "load_seed_chunks", "ContextRouter", "RouteResult"]
