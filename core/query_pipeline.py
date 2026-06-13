"""
core/query_pipeline.py — Pure context-assembly logic for a single query.

Extracted from main.App so the precedence rules (buffered text, drag-dropped
items, clipboard, selected text, ambient snapshot, active document) can be unit
tested without Qt, hotkeys, or any I/O. The only side effect lives behind the
injectable ``read_document_file`` callable so tests can supply a fake.

GenerationCounter is the thread-safe successor to the ad-hoc _gen_id/_gen_id_lock
pair: each new query bumps the counter, and stale worker threads check
``is_current()`` before touching shared UI state.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable


class GenerationCounter:
    """Monotonic, thread-safe generation id used to cancel superseded queries."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self._value += 1
            return self._value

    @property
    def current(self) -> int:
        with self._lock:
            return self._value

    def is_current(self, gen_id: int) -> bool:
        with self._lock:
            return gen_id == self._value


@dataclass
class ContextInputs:
    """Everything build_context needs, with all I/O already performed by the caller."""

    intent_prompt: str
    selected: str | None = None
    screenshot_b64: str | None = None
    ambient_text: str = ""                              # pre-formatted ambient snapshot, or ""
    buffered_items: list[str] = field(default_factory=list)
    drop_items: list[tuple] = field(default_factory=list)  # (name, content, type)
    clipboard_text: str | None = None                  # already read when caller opted in
    active_document_text: str = ""                      # already read + filtered, or ""


@dataclass
class BuiltContext:
    user_message: str
    ambient_ctx: str
    screenshot_b64: str | None


def build_context(
    inp: ContextInputs,
    *,
    read_document_file: Callable[[str], str] | None = None,
) -> BuiltContext:
    """Assemble the final user message, ambient context block, and vision input.

    Precedence mirrors the original App._query_and_speak logic:
      1. buffered (Alt+Q) text
      2. dropped items — first image becomes the vision input when none exists;
         document paths are read and labelled; anything else is appended verbatim
      3. clipboard text (only when the caller opted in, i.e. clipboard_text set)
      4. selected text
    The active document is appended to ambient context whenever the caller
    provided it — including alongside a screenshot, which shows pixels but not
    the document text the user explicitly enabled.
    """
    if read_document_file is None:
        from core.llm_clients.client import read_document_file as _rdf
        read_document_file = _rdf

    screenshot_b64 = inp.screenshot_b64
    context_items: list[str] = list(inp.buffered_items)

    for name, content, item_type in inp.drop_items:
        if item_type == "image" and screenshot_b64 is None:
            screenshot_b64 = content  # first dropped image becomes the vision input
        elif item_type == "document_path":
            doc_text = read_document_file(content)
            if doc_text:
                context_items.append(f"[{name}]\n{doc_text}")
        else:
            context_items.append(content)

    if inp.clipboard_text:
        context_items.append(inp.clipboard_text)

    all_contexts = context_items + ([inp.selected] if inp.selected else [])

    ambient_ctx = inp.ambient_text
    if all_contexts:
        ctx_block = (
            "\n\n".join(f"Context {i + 1}:\n{c}" for i, c in enumerate(all_contexts))
            if len(all_contexts) > 1
            else all_contexts[0]
        )
        ambient_ctx = f"{ambient_ctx}\n\n---\n{ctx_block}" if ambient_ctx else ctx_block

    if inp.active_document_text:
        ambient_ctx = (
            f"{ambient_ctx}\n\n---\n[Active document]\n{inp.active_document_text}"
            if ambient_ctx
            else f"[Active document]\n{inp.active_document_text}"
        )

    return BuiltContext(
        user_message=inp.intent_prompt,
        ambient_ctx=ambient_ctx,
        screenshot_b64=screenshot_b64,
    )
