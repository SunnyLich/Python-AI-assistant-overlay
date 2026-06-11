"""Unit tests for the ``brain.query`` handler.

These run fully offline via the ``WISP_BRAIN_FAKE_LLM`` seam: the real
``core.query_pipeline.build_context`` still assembles the prompt (so context
precedence is exercised for real), but the token stream is a deterministic echo
instead of a provider call. That lets us assert streaming, reassembly, that the
caller's inputs reached the model, and cooperative cancel -- with no API key.
"""
from __future__ import annotations

import sys
import types

import pytest

from wisp_brain import handlers


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")


def _chunks(events):
    return [data["text"] for event, data in events if event == "reply.chunk"]


def test_query_is_registered_as_streaming():
    assert "brain.query" in handlers.HANDLERS
    assert "brain.query" in handlers.STREAMING


def test_query_streams_and_reassembles(record_ctx):
    events, ctx = record_ctx(req_id=7)
    result = handlers.HANDLERS["brain.query"](
        ctx, intent_prompt="hello world", memory_context="(none)"
    )
    assert result["text"].startswith("[fake-llm]")
    assert "".join(_chunks(events)) == result["text"]
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"]}]


def test_query_includes_intent_and_selected_in_prompt(record_ctx):
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="summarize this function",
        selected="def add(a, b): return a + b",
        ambient_text="VSCode - main.py",
        memory_context="(none)",
    )
    # The fake reply echoes the assembled user_message, so the inputs that
    # build_context folded in must be visible in the streamed answer.
    assert "summarize this function" in result["text"]
    assert "def add(a, b)" in result["text"]


def test_query_memory_disabled_skips_retrieval(record_ctx, monkeypatch):
    from core.memory_store import store

    class FailingManager:
        def retrieve_relevant(self, _query):
            raise AssertionError("memory should not be fetched")

    monkeypatch.setattr(store, "get_manager", lambda: FailingManager())

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="what do you remember",
        memory_enabled=False,
    )

    assert result["text"].startswith("[fake-llm]")
    assert "".join(_chunks(events)) == result["text"]


def test_query_forwards_tool_policy(record_ctx, monkeypatch):
    captured = {}

    def fake_stream(
        _built,
        _memory_context,
        use_tools,
        allowed_tools,
        allow_screenshot_tool,
        screenshot_tool_b64,
    ):
        captured["use_tools"] = use_tools
        captured["allowed_tools"] = allowed_tools
        captured["allow_screenshot_tool"] = allow_screenshot_tool
        captured["screenshot_tool_b64"] = screenshot_tool_b64
        yield "ok"

    monkeypatch.setattr(handlers, "_stream_query_reply", fake_stream)
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="look if needed",
        memory_context="(none)",
        use_tools=True,
        allowed_tools=["get_context.documents"],
        allow_screenshot_tool=True,
        screenshot_tool_b64="screen-data",
    )

    assert result["text"] == "ok"
    assert "".join(_chunks(events)) == "ok"
    assert captured == {
        "use_tools": True,
        "allowed_tools": ["get_context.documents"],
        "allow_screenshot_tool": True,
        "screenshot_tool_b64": "screen-data",
    }


def test_query_includes_active_document_when_requested(record_ctx, monkeypatch):
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(llm_client, "read_active_document_for_context", lambda: "ACTIVE DOC TEXT")

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="use open docs",
        memory_context="(none)",
        include_active_document=True,
    )

    assert "[Active document]\nACTIVE DOC TEXT" in result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_query_injects_frontloaded_tool_context(record_ctx, monkeypatch):
    from core.llm_clients import client as llm_client

    def fake_inject(ambient_context, allowed_tools):
        assert allowed_tools == ["git_status"]
        return f"{ambient_context}\n\n---\n[Git status]\nM file.py".strip()

    monkeypatch.setattr(llm_client, "_inject_frontloaded_tool_context", fake_inject)

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="what changed",
        ambient_text="Active app: Editor",
        memory_context="(none)",
        frontload_tools=["git_status"],
    )

    assert "[Git status]\nM file.py" in result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_query_runs_shared_plugin_before_and_after_hooks(record_ctx, monkeypatch):
    calls: dict[str, object] = {}

    class FakeManager:
        def before_query(self, prompt, context):
            calls["before"] = (prompt, context)
            return f"{prompt} + plugin prompt", f"{context}\nplugin context".strip()

        def after_response(self, text):
            calls["after"] = text

    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="original prompt",
        ambient_text="original context",
        memory_context="(none)",
    )

    assert calls["before"] == ("original prompt", "original context")
    assert "original prompt + plugin prompt" in result["text"]
    assert "plugin context" in result["text"]
    assert calls["after"] == result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_query_skips_active_document_when_screenshot_attached(record_ctx, monkeypatch):
    from core.llm_clients import client as llm_client

    def fail_read():
        raise AssertionError("active document should not be read for screenshot query")

    monkeypatch.setattr(llm_client, "read_active_document_for_context", fail_read)

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="look at this",
        screenshot_b64="image-data",
        memory_context="(none)",
        include_active_document=True,
    )

    assert "ACTIVE DOC" not in result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_active_document_context_handler_filters_error_strings(monkeypatch):
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(llm_client, "read_active_document_for_context", lambda: "Failed to read document")

    result = handlers.HANDLERS["brain.context.active_document"]()

    assert result == {"text": ""}


def test_query_precancelled_yields_empty(record_ctx):
    events, ctx = record_ctx()
    ctx.cancelled = True
    result = handlers.HANDLERS["brain.query"](
        ctx, intent_prompt="anything", memory_context="(none)"
    )
    assert result["text"] == ""
    assert _chunks(events) == []


def test_rewrite_is_registered_as_streaming():
    assert "brain.rewrite" in handlers.HANDLERS
    assert "brain.rewrite" in handlers.STREAMING


def test_rewrite_streams_selected_text(record_ctx):
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.rewrite"](
        ctx,
        intent_prompt="Fix grammar",
        selected_text="this are rough",
    )

    assert result["text"].startswith("[fake-rewrite]")
    assert "Fix grammar" in result["text"]
    assert "this are rough" in result["text"]
    assert "".join(_chunks(events)) == result["text"]
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"]}]


def test_rewrite_requires_selected_text(record_ctx):
    _events, ctx = record_ctx()

    with pytest.raises(ValueError, match="selected_text"):
        handlers.HANDLERS["brain.rewrite"](ctx, intent_prompt="Fix grammar")
