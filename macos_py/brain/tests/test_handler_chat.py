"""Unit tests for the ``brain.chat`` handler."""
from __future__ import annotations

import pytest

from wisp_brain import handlers


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")


def _chunks(events):
    return [data["text"] for event, data in events if event == "reply.chunk"]


def test_chat_is_registered_as_streaming():
    assert "brain.chat" in handlers.HANDLERS
    assert "brain.chat" in handlers.STREAMING


def test_chat_streams_and_reassembles_last_user_turn(record_ctx):
    events, ctx = record_ctx(req_id=11)
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "continue please"},
        ],
        memory_context="(none)",
    )

    assert result["text"].startswith("[fake-chat]")
    assert "continue please" in result["text"]
    assert "".join(_chunks(events)) == result["text"]
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"]}]


def test_chat_requires_a_user_turn(record_ctx):
    _events, ctx = record_ctx()
    with pytest.raises(ValueError, match="at least one user"):
        handlers.HANDLERS["brain.chat"](ctx, messages=[], memory_context="(none)")


def test_chat_normalizes_messages_and_forwards_memory_context(record_ctx, monkeypatch):
    captured = {}

    def fake_stream(messages, memory_context):
        captured["messages"] = messages
        captured["memory_context"] = memory_context
        yield "ok"

    monkeypatch.setattr(handlers, "_stream_chat_reply", fake_stream)
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[
            {"role": "system", "content": "Be brief."},
            {"role": "ignored", "content": "drop me"},
            {"role": "user", "content": "  tell me more  "},
            {"role": "assistant", "content": ""},
        ],
        memory_context="[Memory]\n- one fact",
    )

    assert result["text"] == "ok"
    assert "".join(_chunks(events)) == "ok"
    assert captured == {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "tell me more"},
        ],
        "memory_context": "[Memory]\n- one fact",
    }


def test_chat_memory_disabled_skips_retrieval(record_ctx, monkeypatch):
    from core.memory_store import store

    class FailingManager:
        def retrieve_relevant(self, _query):
            raise AssertionError("memory should not be fetched")

    monkeypatch.setattr(store, "get_manager", lambda: FailingManager())

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[{"role": "user", "content": "what do you know about me"}],
        memory_enabled=False,
    )

    assert result["text"].startswith("[fake-chat]")
    assert "".join(_chunks(events)) == result["text"]


def test_chat_precancelled_yields_empty(record_ctx):
    events, ctx = record_ctx()
    ctx.cancelled = True
    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=[{"role": "user", "content": "anything"}],
        memory_context="(none)",
    )

    assert result["text"] == ""
    assert _chunks(events) == []
