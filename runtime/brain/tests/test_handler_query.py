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
    """Verify offline behavior."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")


def _chunks(events):
    """Verify chunks behavior."""
    return [data["text"] for event, data in events if event == "reply.chunk"]


def test_query_is_registered_as_streaming():
    """Verify query is registered as streaming behavior."""
    assert "brain.query" in handlers.HANDLERS
    assert "brain.query" in handlers.STREAMING


def test_query_streams_and_reassembles(record_ctx):
    """Verify query streams and reassembles behavior."""
    events, ctx = record_ctx(req_id=7)
    result = handlers.HANDLERS["brain.query"](
        ctx, intent_prompt="hello world", memory_context="(none)"
    )
    assert result["text"].startswith("[fake-llm]")
    assert "".join(_chunks(events)) == result["text"]
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"]}]


def test_query_includes_intent_and_selected_in_prompt(record_ctx):
    """Verify query includes intent and selected in prompt behavior."""
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="summarize this function",
        selected="def add(a, b): return a + b",
        ambient_text="VSCode - scratch.py",
        memory_context="(none)",
    )
    # The fake reply echoes the assembled user_message, so the inputs that
    # build_context folded in must be visible in the streamed answer.
    assert "summarize this function" in result["text"]
    assert "def add(a, b)" in result["text"]


def test_query_memory_disabled_skips_retrieval(record_ctx, monkeypatch):
    """Verify query memory disabled skips retrieval behavior."""
    from core.memory_store import store

    class FailingManager:
        """Coordinate failing manager behavior."""
        def retrieve_relevant(self, _query):
            """Verify retrieve relevant behavior."""
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
    """Verify query forwards tool policy behavior."""
    captured = {}

    def fake_stream(
        _built,
        _memory_context,
        use_tools,
        allowed_tools,
        allow_screenshot_tool,
        screenshot_tool_b64,
        pinned_tools=None,
        history=None,
        ctx=None,
        file_access_mode="",
        file_context=None,
    ):
        """Verify fake stream behavior."""
        captured["use_tools"] = use_tools
        captured["allowed_tools"] = allowed_tools
        captured["allow_screenshot_tool"] = allow_screenshot_tool
        captured["screenshot_tool_b64"] = screenshot_tool_b64
        captured["pinned_tools"] = pinned_tools
        captured["history"] = history
        captured["ctx"] = ctx
        captured["file_access_mode"] = file_access_mode
        captured["file_context"] = file_context
        yield "ok"

    monkeypatch.setattr(handlers, "_stream_query_reply", fake_stream)
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="look if needed",
        memory_context="(none)",
        use_tools=True,
        allowed_tools=["get_context.documents"],
        pinned_tools=["my_tool"],
        allow_screenshot_tool=True,
        screenshot_tool_b64="screen-data",
        file_access_mode="ask",
    )

    assert result["text"] == "ok"
    assert "".join(_chunks(events)) == "ok"
    assert captured == {
        "use_tools": True,
        "allowed_tools": ["get_context.documents"],
        "allow_screenshot_tool": True,
        "screenshot_tool_b64": "screen-data",
        "pinned_tools": ["my_tool"],
        "history": None,
        "ctx": ctx,
        "file_access_mode": "ask",
        "file_context": [],
    }


def test_query_emits_progress_chunks_without_saving_them(record_ctx, monkeypatch):
    """Verify progress narration streams live but is excluded from final text."""
    class ProgressChunk(str):
        """String-like progress chunk."""
        kind = "progress"

    def fake_stream(*_args, **_kwargs):
        """Emit progress then final answer."""
        yield ProgressChunk("Checking the file first.")
        yield "Done."

    monkeypatch.setattr(handlers, "_stream_query_reply", fake_stream)
    events, ctx = record_ctx()

    result = handlers.HANDLERS["brain.query"](ctx, intent_prompt="edit file", memory_context="(none)")

    assert result["text"] == "Done."
    assert _chunks(events) == ["Checking the file first.", "Done."]
    progress = [data for event, data in events if event == "reply.chunk" and data.get("is_progress")]
    assert progress[0]["text"] == "Checking the file first."
    assert progress[0]["is_progress"] is True
    assert [data for event, data in events if event == "reply.done"] == [{"text": "Done."}]


def test_query_reloads_config_before_live_file_tools(record_ctx, monkeypatch):
    """Verify query refreshes TOOL_FILE_ROOTS before live local-file tools run."""
    import config

    reloads = []
    monkeypatch.setattr(config, "reload", lambda: reloads.append(True))

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="create hello.py",
        memory_enabled=False,
        use_tools=True,
        allowed_tools=["create_file"],
        file_access_mode="ask",
    )

    assert result["text"].startswith("[fake-llm]")
    assert "".join(_chunks(events)) == result["text"]
    assert reloads == [True]


def test_query_includes_active_document_when_requested(record_ctx, monkeypatch):
    """Verify query includes active document when requested behavior."""
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(
        llm_client,
        "read_active_document_for_context_with_debug",
        lambda **_kwargs: ("ACTIVE DOC TEXT", {"paths": ["active.docx"]}),
    )

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="use open docs",
        memory_context="(none)",
        include_active_document=True,
    )

    assert "[Active document]\nACTIVE DOC TEXT" in result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_query_includes_context_priority_note(record_ctx):
    """Verify query includes context priority note behavior."""
    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="use both",
        ambient_text="[Browser/Web]\nPAGE TEXT",
        active_document_text="DOC TEXT",
        context_priority="Browser/Web",
        memory_context="(none)",
    )

    assert "[Context priority]\nPrioritize Browser/Web" in result["text"]
    assert "[Active document]\nDOC TEXT" in result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_query_injects_frontloaded_tool_context(record_ctx, monkeypatch):
    """Verify query injects frontloaded tool context behavior."""
    from core.llm_clients import client as llm_client

    def fake_inject(ambient_context, allowed_tools, *, query=None):
        """Verify fake inject behavior."""
        assert allowed_tools == ["git_status"]
        assert query == "what changed"
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


def test_query_runs_shared_addon_before_and_after_hooks(record_ctx, monkeypatch):
    """Verify query runs shared addon before and after hooks behavior."""
    calls: dict[str, object] = {}

    class FakeManager:
        """Coordinate fake manager behavior."""
        def before_query(self, prompt, context):
            """Verify before query behavior."""
            calls["before"] = (prompt, context)
            return f"{prompt} + addon prompt", f"{context}\naddon context".strip()

        def after_response(self, text):
            """Verify after response behavior."""
            calls["after"] = text

    fake_addon_manager = types.ModuleType("core.addon_manager")
    fake_addon_manager.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "core.addon_manager", fake_addon_manager)

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="original prompt",
        ambient_text="original context",
        memory_context="(none)",
    )

    assert calls["before"] == ("original prompt", "original context")
    assert "original prompt + addon prompt" in result["text"]
    assert "addon context" in result["text"]
    assert calls["after"] == result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_query_reads_active_document_alongside_screenshot(record_ctx, monkeypatch):
    # A screenshot must not silently disable the documents setting: the image
    # shows pixels while the document text carries the actual content.
    """Verify query reads active document alongside screenshot behavior."""
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(
        llm_client,
        "read_active_document_for_context_with_debug",
        lambda **_kwargs: ("ACTIVE DOC TEXT", {"paths": ["active.docx"]}),
    )

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="look at this",
        screenshot_b64="image-data",
        memory_context="(none)",
        include_active_document=True,
    )

    assert "[Active document]\nACTIVE DOC TEXT" in result["text"]
    assert "".join(_chunks(events)) == result["text"]


def test_active_document_context_handler_filters_error_strings(monkeypatch):
    """Verify active document context handler filters error strings behavior."""
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(
        llm_client,
        "read_active_document_for_context_with_debug",
        lambda **_kwargs: ("Failed to read document", {"paths": ["bad.docx"]}),
    )

    result = handlers.HANDLERS["brain.context.active_document"]()

    assert result == {"text": "", "debug": {"paths": ["bad.docx"]}}


def test_query_precancelled_yields_empty(record_ctx):
    """Verify query precancelled yields empty behavior."""
    events, ctx = record_ctx()
    ctx.cancelled = True
    result = handlers.HANDLERS["brain.query"](
        ctx, intent_prompt="anything", memory_context="(none)"
    )
    assert result["text"] == ""
    assert _chunks(events) == []


def test_rewrite_is_registered_as_streaming():
    """Verify rewrite is registered as streaming behavior."""
    assert "brain.rewrite" in handlers.HANDLERS
    assert "brain.rewrite" in handlers.STREAMING


def test_rewrite_streams_selected_text(record_ctx):
    """Verify rewrite streams selected text behavior."""
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
    """Verify rewrite requires selected text behavior."""
    _events, ctx = record_ctx()

    with pytest.raises(ValueError, match="selected_text"):
        handlers.HANDLERS["brain.rewrite"](ctx, intent_prompt="Fix grammar")
