"""
wisp_brain.handlers — methods that execute INSIDE the brain sidecar.

Each entry in ``HANDLERS`` maps a protocol ``method`` to a callable. Methods in
``STREAMING`` receive a ``StreamContext`` as their first positional argument and
may push ``reply.chunk``-style events (tagged with the request id) before they
return their final result; everything else is a plain unary call whose return
value becomes the response ``result``.

Heavy / OS-agnostic brain modules (``core.query_pipeline``,
``core.llm_clients.client``, faster-whisper, ...) are imported LAZILY inside the
handlers, never at module import, so the sidecar boots and can answer ``ping`` on
any platform with no API keys or models present. That is what lets this file be
tested from Windows/CI without the LLM stack.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

# Keep optional-dependency chatter off the protocol channel's stderr mirror.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

HANDLERS: dict[str, Callable[..., Any]] = {}
STREAMING: set[str] = set()


class StreamContext:
    """Passed to streaming handlers; ``emit`` tags events with the request id so
    the host can route partial output back to the originating call."""

    __slots__ = ("_emit", "req_id", "cancelled")

    def __init__(self, emit: Callable[[str, Any, Any], None], req_id: Any) -> None:
        self._emit = emit          # (event_name, data, req_id) -> None
        self.req_id = req_id
        self.cancelled = False

    def emit(self, event: str, data: Any = None) -> None:
        self._emit(event, data, self.req_id)


def handler(name: str, *, streaming: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        HANDLERS[name] = fn
        if streaming:
            STREAMING.add(name)
        return fn
    return deco


def _log(msg: str) -> None:
    print(f"[brain] {msg}", flush=True)  # -> stderr (host redirects fd 1 to fd 2)


# ---------------------------------------------------------------------------
# Diagnostics (no heavy imports -- always available)
# ---------------------------------------------------------------------------

@handler("ping")
def ping(value: Any = None) -> dict[str, Any]:
    """Liveness / round-trip check. Echoes *value* and reports the sidecar pid."""
    return {"pong": True, "value": value, "pid": os.getpid()}


@handler("brain.echo", streaming=True)
def brain_echo(ctx: StreamContext, text: str = "", chunk_size: int = 1, delay: float = 0.0) -> dict[str, Any]:
    """Stream *text* back word-by-word as ``reply.chunk`` events, then return the
    whole string. Pure-Python, no models or network -- this is the streaming
    handshake the Phase-1 test exercises to prove event correlation works."""
    words = text.split(" ") if text else []
    sent: list[str] = []
    for i in range(0, len(words), max(1, chunk_size)):
        if ctx.cancelled:
            break
        piece = " ".join(words[i:i + max(1, chunk_size)])
        if i + max(1, chunk_size) < len(words):
            piece += " "
        sent.append(piece)
        ctx.emit("reply.chunk", {"text": piece})
        if delay:
            time.sleep(delay)
    full = "".join(sent)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


# ---------------------------------------------------------------------------
# Real query path -- wired to the existing pipeline, exercised on the Mac / online.
# Imports are lazy so this module still loads with no LLM deps/keys present.
# ---------------------------------------------------------------------------

@handler("brain.query", streaming=True)
def brain_query(
    ctx: StreamContext,
    intent_prompt: str = "",
    selected: str | None = None,
    screenshot_b64: str | None = None,
    ambient_text: str = "",
    memory_context: str = "",
    use_tools: bool = False,
) -> dict[str, Any]:
    """Assemble context and stream an LLM reply, mirroring App._query_and_speak.

    Reuses the OS-agnostic brain verbatim: ``core.query_pipeline.build_context``
    for precedence rules and ``core.llm_clients.client.stream_response`` for the
    token stream. Each chunk becomes a ``reply.chunk`` event tagged with this
    request's id; the full text is the final response result.
    """
    from core.query_pipeline import ContextInputs, build_context
    from core.llm_clients.client import stream_response

    built = build_context(
        ContextInputs(
            intent_prompt=intent_prompt,
            selected=selected,
            screenshot_b64=screenshot_b64,
            ambient_text=ambient_text,
        )
    )

    parts: list[str] = []
    for chunk in stream_response(
        built.user_message,
        image_base64=built.screenshot_b64,
        ambient_context=built.ambient_ctx,
        memory_context=memory_context,
        use_tools=use_tools,
    ):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


__all__ = ["HANDLERS", "STREAMING", "StreamContext", "handler"]
