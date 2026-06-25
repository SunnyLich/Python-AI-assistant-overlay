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
import config

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


def test_query_uses_planned_chunking_when_enabled_and_eligible(record_ctx, monkeypatch):
    """Verify eligible query streams planned visible chunks as one final reply."""
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(handlers, "_offline_brain", lambda: False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING", True, raising=False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING_CHUNKS", 3, raising=False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING_MIN_PROMPT_CHARS", 0, raising=False)
    planned_calls: list[dict] = []

    def fake_planned(*args, **kwargs):
        planned_calls.append({"args": args, "kwargs": kwargs})
        yield "First part."
        yield " Second part."
        yield " Final part."

    monkeypatch.setattr(llm_client, "stream_planned_chunk_response", fake_planned)
    monkeypatch.setattr(
        llm_client,
        "stream_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("normal path should not run")),
    )

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="Explain planned chunking with enough detail to pass the prompt length gate.",
        memory_enabled=False,
    )

    assert _chunks(events) == ["First part.", " Second part.", " Final part."]
    assert result["text"] == "First part. Second part. Final part."
    assert planned_calls
    assert planned_calls[0]["kwargs"]["chunks"] == 3
    assert [data for event, data in events if event == "reply.done"] == [{"text": result["text"]}]


def test_query_uses_normal_stream_when_planned_chunking_is_disabled(record_ctx, monkeypatch):
    """Verify disabled feature flag keeps the existing query path."""
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(handlers, "_offline_brain", lambda: False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING", False, raising=False)
    monkeypatch.setattr(
        llm_client,
        "stream_planned_chunk_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("planned path should not run")),
    )
    monkeypatch.setattr(llm_client, "stream_response", lambda *_args, **_kwargs: iter(["normal answer"]))

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="Explain planned chunking with enough detail to pass the prompt length gate.",
        memory_enabled=False,
    )

    assert _chunks(events) == ["normal answer"]
    assert result["text"] == "normal answer"


@pytest.mark.parametrize(
    "params",
    [
        {"use_tools": True, "allowed_tools": ["get_context.documents"]},
        {"screenshot_b64": "image-data"},
        {"allow_screenshot_tool": True, "screenshot_tool_b64": "screen-data"},
        {"file_access_mode": "ask"},
        {"history": [{"role": "user", "content": "prior"}]},
    ],
)
def test_query_ineligible_planned_chunking_cases_use_normal_stream(record_ctx, monkeypatch, params):
    """Verify tools, images, file access, and history force the normal path."""
    from core.llm_clients import client as llm_client

    monkeypatch.setattr(handlers, "_offline_brain", lambda: False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING", True, raising=False)
    monkeypatch.setattr(config, "PLANNED_CHUNKING_MIN_PROMPT_CHARS", 0, raising=False)
    monkeypatch.setattr(
        llm_client,
        "stream_planned_chunk_response",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("planned path should not run")),
    )
    monkeypatch.setattr(llm_client, "stream_response", lambda *_args, **_kwargs: iter(["normal answer"]))

    events, ctx = record_ctx()
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="Explain planned chunking with enough detail to pass the prompt length gate.",
        memory_enabled=False,
        **params,
    )

    assert _chunks(events) == ["normal answer"]
    assert result["text"] == "normal answer"


def test_query_normalizes_history_context_and_attachments(record_ctx, monkeypatch):
    """Verify hotkey followups replay file context with source boundaries."""
    from core.conversation_store import store as conversation_store

    captured = {}

    monkeypatch.setattr(conversation_store, "normalize_attachments", lambda refs: list(refs or []))
    monkeypatch.setattr(
        conversation_store,
        "attachment_context_text",
        lambda ref: f"Path: {ref['path']}\n\n{ref['text']}",
    )
    monkeypatch.setattr(conversation_store, "first_image_base64_from_message", lambda _msg: "")

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
        captured["history"] = history
        yield "ok"

    monkeypatch.setattr(handlers, "_stream_query_reply", fake_stream)
    _events, ctx = record_ctx()

    handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="follow up",
        memory_context="(none)",
        history=[
            {
                "role": "user",
                "content": "summarize",
                "context": "Original ambient context",
                "attachments": [
                    {"name": "notes.md", "path": "/tmp/notes.md", "text": "notes content"},
                ],
            },
            {"role": "assistant", "content": "summary"},
        ],
    )

    content = captured["history"][0]["content"]
    assert content.startswith("summarize\n\n[Attached context for this message]")
    assert "--- BEGIN MESSAGE CONTEXT ---" in content
    assert "Original ambient context" in content
    assert "--- BEGIN ATTACHED FILE: notes.md ---" in content
    assert "Path: /tmp/notes.md" in content
    assert "notes content" in content
    assert "--- END ATTACHED FILE: notes.md ---" in content
    assert captured["history"][1] == {"role": "assistant", "content": "summary"}


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
        active_document_label="Code - notes.py",
        context_priority="Browser/Web",
        memory_context="(none)",
    )

    assert "[Context priority]\nPrioritize Browser/Web" in result["text"]
    assert "--- BEGIN ACTIVE DOCUMENT: Code - notes.py ---\nDOC TEXT" in result["text"]
    assert "--- END ACTIVE DOCUMENT: Code - notes.py ---" in result["text"]
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
    assert result["visible_text"] == ""
    assert _chunks(events) == []
    done = [data for event, data in events if event == "reply.done"]
    assert done == [{"text": result["text"], "visible_text": ""}]


def test_rewrite_forwards_source_context(record_ctx, monkeypatch):
    """Verify rewrite source context reaches the LLM route."""
    captured = {}

    def fake_stream(selected_text: str, intent_prompt: str, rewrite_context: str = ""):
        from core.llm_clients.client import _rewrite_result_chunk

        captured["selected_text"] = selected_text
        captured["intent_prompt"] = intent_prompt
        captured["rewrite_context"] = rewrite_context
        yield _rewrite_result_chunk("replacement")

    monkeypatch.setattr(handlers, "_stream_rewrite_reply", fake_stream)
    events, ctx = record_ctx()

    result = handlers.HANDLERS["brain.rewrite"](
        ctx,
        intent_prompt="Use VS Code",
        selected_text="notepad text",
        rewrite_context="--- BEGIN ACTIVE DOCUMENT: Code - demo.py ---\nVS Code paragraph",
    )

    assert captured == {
        "selected_text": "notepad text",
        "intent_prompt": "Use VS Code",
        "rewrite_context": "--- BEGIN ACTIVE DOCUMENT: Code - demo.py ---\nVS Code paragraph",
    }
    assert result["text"] == "replacement"
    assert result["visible_text"] == ""
    assert _chunks(events) == []


def test_rewrite_streams_commentary_but_returns_tool_result(record_ctx, monkeypatch):
    """Verify visible rewrite commentary is not pasted back."""
    from core.llm_clients.client import _progress_chunk, _rewrite_result_chunk

    def fake_stream(selected_text: str, intent_prompt: str, rewrite_context: str = ""):
        yield _progress_chunk("I found the source text.")
        yield _rewrite_result_chunk("replacement from tool")

    monkeypatch.setattr(handlers, "_stream_rewrite_reply", fake_stream)
    events, ctx = record_ctx()

    result = handlers.HANDLERS["brain.rewrite"](
        ctx,
        intent_prompt="Use source app",
        selected_text="target text",
        rewrite_context="[Source]\nreplacement from tool",
    )

    assert result["text"] == "replacement from tool"
    assert result["visible_text"] == "I found the source text."
    assert _chunks(events) == ["I found the source text."]


def test_rewrite_requires_selected_text(record_ctx):
    """Verify rewrite requires selected text behavior."""
    _events, ctx = record_ctx()

    with pytest.raises(ValueError, match="selected_text"):
        handlers.HANDLERS["brain.rewrite"](ctx, intent_prompt="Fix grammar")
