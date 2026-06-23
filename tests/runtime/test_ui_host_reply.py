from __future__ import annotations


def test_context_source_labels_translate_without_touching_custom_labels(monkeypatch) -> None:
    """Verify built-in context badge labels are localized but user labels remain."""
    from runtime.workers import ui_host

    monkeypatch.setattr(ui_host, "t", lambda text: f"tx:{text}")

    assert ui_host._context_display_label("App") == "tx:App"
    assert ui_host._context_display_label("Browser/Web") == "tx:Browser/Web"
    assert ui_host._context_display_label("notes.txt") == "notes.txt"


def test_health_text_translates_nested_messages_and_values(monkeypatch) -> None:
    """Verify health text translation handles composed messages and value atoms."""
    from runtime.workers import ui_host

    translations = {
        "LLM test failed: {message}": "LLM \u6e2c\u8a66\u5931\u6557\uff1a{message}",
        "LLM route uses {provider} but you are not logged in.": "LLM \u8def\u7531\u4f7f\u7528 {provider}\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002",
        "Microphone permission: {value}.": "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a{value}\u3002",
        "unavailable": "\u7121\u6cd5\u4f7f\u7528",
    }
    monkeypatch.setattr(ui_host, "t", lambda text: translations.get(text, text))

    assert ui_host._translate_health_text(
        "LLM test failed: LLM route uses chatgpt but you are not logged in."
    ) == "LLM \u6e2c\u8a66\u5931\u6557\uff1aLLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
    assert (
        ui_host._translate_health_text("Microphone permission: unavailable.")
        == "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a\u7121\u6cd5\u4f7f\u7528\u3002"
    )


def test_notice_text_translates_known_bubble_messages(monkeypatch) -> None:
    """Verify system bubble notices translate known lines while preserving layout."""
    from runtime.workers import ui_host

    translations = {
        "Addon folder installed.": "\u5916\u639b\u8cc7\u6599\u593e\u5df2\u5b89\u88dd\u3002",
        "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it.": "\u5efa\u8b70\uff1a\u958b\u555f\u5916\u639b\u7ba1\u7406\u5668\uff0c\u6aa2\u67e5\u5916\u639b\u8a3a\u65b7\u8cc7\u8a0a\uff0c\u7136\u5f8c\u4fee\u5fa9\u6216\u505c\u7528\u5b83\u3002",
        "Technical detail: ": "\u6280\u8853\u7d30\u7bc0\uff1a",
    }
    monkeypatch.setattr(ui_host, "t", lambda text: translations.get(text, text))

    assert ui_host._translate_notice_text(
        "Addon folder installed.\n\n"
        "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it.\n"
        "Technical detail: addon.json missing"
    ) == (
        "\u5916\u639b\u8cc7\u6599\u593e\u5df2\u5b89\u88dd\u3002\n\n"
        "\u5efa\u8b70\uff1a\u958b\u555f\u5916\u639b\u7ba1\u7406\u5668\uff0c\u6aa2\u67e5\u5916\u639b\u8a3a\u65b7\u8cc7\u8a0a\uff0c\u7136\u5f8c\u4fee\u5fa9\u6216\u505c\u7528\u5b83\u3002\n"
        "\u6280\u8853\u7d30\u7bc0\uff1aaddon.json missing"
    )


def test_memory_proxy_accepts_project_scope() -> None:
    """Verify UI memory proxy forwards project-scoped add/update payloads."""
    from runtime.workers.ui_host import MemoryProxy

    emitted = []
    proxy = MemoryProxy(lambda event, payload: emitted.append((event, payload)))

    proxy.add_fact_manual("ships on Fridays", project="proj-1")
    fact_id = proxy.get_all_facts()[0]["id"]
    proxy.update_fact(fact_id, "ships on Mondays", project="")

    assert emitted == [
        (
            "ui.memory.add",
            {"text": "ships on Fridays", "category": "project_context", "project": "proj-1"},
        ),
        (
            "ui.memory.update",
            {"id": fact_id, "text": "ships on Mondays", "category": "general", "project": ""},
        ),
    ]
    assert proxy.get_all_facts()[0]["category"] == "general"
    assert proxy.get_all_facts()[0]["project"] == ""


class _Bubble:
    """Capture reply chunks sent to the speech bubble."""

    def __init__(self) -> None:
        self.chunks: list[tuple[str, bool]] = []

    def append_chunk(self, text: str, is_thought: bool = False) -> None:
        self.chunks.append((text, is_thought))


def test_reply_chunk_accepts_progress_metadata() -> None:
    """Verify ui.reply.chunk accepts supervisor progress metadata."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_chunk(text="Reading files...", is_progress=True)

    assert result == {"appended": len("Reading files..."), "is_progress": True}
    assert bubble.chunks == [("Reading files...", False)]


def test_ui_shutdown_message_closes_stdin_reader() -> None:
    """Verify UI shutdown unblocks the stdin reader before interpreter teardown."""
    import json
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    stopped = []
    quit_calls = []
    responses = []
    closed = []
    host._closing = False
    host._pump = SimpleNamespace(stop=lambda: stopped.append(True))
    host._app = SimpleNamespace(quit=lambda: quit_calls.append(True))
    host._stdin_stream = SimpleNamespace(close=lambda: closed.append(True))
    host._respond = lambda req_id, ok, **kwargs: responses.append((req_id, ok, kwargs))  # type: ignore[method-assign]

    host._handle_line(json.dumps({"id": 7, "method": "__shutdown__", "params": {}}).encode("utf-8"))

    assert responses == [(7, True, {"result": None})]
    assert host._closing is True
    assert stopped == [True]
    assert closed == [True]
    assert quit_calls == [True]


def test_bubble_highlight_does_not_mutate_chat_window() -> None:
    """Verify TTS bubble highlight leaves selectable chat transcript alone."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    chat_updates = []
    emitted = []
    host._chat = SimpleNamespace(
        update_live_highlight=lambda *args: chat_updates.append(args)
    )
    host.emit = lambda event, payload: emitted.append((event, payload))  # type: ignore[method-assign]

    host._bubble_highlight("done", 1, False)

    assert chat_updates == []
    assert emitted == [
        ("ui.bubble.highlight", {"text": "done", "revealed_count": 1, "finished": False})
    ]


def test_chat_add_conversation_stamps_metadata() -> None:
    """Verify hotkey-created conversations carry display-only timestamps."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    result = host._chat_add_conversation(user="hi", assistant="hello")

    assert result == {"count": 1, "continued": False}
    assert persisted == [True]
    conv = host._all_conversations[0]
    assert conv["created_at"]
    assert conv["updated_at"] == conv["created_at"]
    assert conv["messages"][0]["created_at"] == conv["created_at"]
    assert conv["messages"][1]["created_at"] == conv["created_at"]


def test_chat_add_conversation_selects_new_chat_when_window_is_open() -> None:
    """Verify externally created chats become visible in an open chat window."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
    ingest_calls = []
    host._chat = SimpleNamespace(
        ingest_new_conversations=lambda **kwargs: ingest_calls.append(kwargs)
    )

    result = host._chat_add_conversation(user="hi", assistant="hello")

    assert result == {"count": 1, "continued": False}
    assert ingest_calls == [{"select_new": True}]


def test_chat_add_conversation_persists_file_context() -> None:
    """Verify hotkey-created conversations store file tool metadata."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
    file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "root": r"C:\repo\model_files",
            "ok": True,
            "message": "Created hello_world.py.",
        }
    ]

    host._chat_add_conversation(user="create", assistant="done", file_context=file_context)

    assert host._all_conversations[0]["file_context"] == file_context


def test_chat_request_reuses_active_conversation_tool_context() -> None:
    """Verify chat sends stored tool policy when continuing a conversation."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tool_context": {
                "allowed_tools": ["read_file", "edit_file"],
                "pinned_tools": ["read_file", "edit_file"],
                "file_access_mode": "ask",
            },
        }
    ]
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()
    emitted = []

    def emit(event, payload):
        emitted.append((event, payload))
        request_id = payload["request_id"]
        host._chat_done(request_id=request_id, text="ok", tool_context=payload["tool_context"])

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "continue"}]))

    assert emitted[0][0] == "ui.chat.request"
    assert emitted[0][1]["tool_context"]["file_access_mode"] == "ask"
    assert emitted[0][1]["tool_context"]["allowed_tools"] == ["read_file", "edit_file"]
    assert result == [
        {
            "type": "metadata",
            "file_context": [],
            "tool_context": emitted[0][1]["tool_context"],
        },
        {"type": "final", "text": "ok"},
    ]


def test_selecting_chat_shows_overlay_continuation_notice() -> None:
    """Verify chat selection reflects the target conversation in the bubble."""
    from types import SimpleNamespace

    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "old chat"}]},
        {"messages": [{"role": "user", "content": "new chat"}]},
    ]
    host._chat = SimpleNamespace(_streaming=False)
    notices = []
    host._ensure_bubble = lambda: SimpleNamespace(show_notice=lambda text, timeout_ms=0: notices.append((text, timeout_ms)))  # type: ignore[attr-defined]

    host._set_active_conversation(1)

    assert host._active_conversation_idx == 1
    assert notices == [("Continuing: new chat", 2500)]


def test_intent_conversation_options_start_new_until_chat_is_active() -> None:
    """Verify loaded history is listed but not continued by default on app start."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "old chat"}], "project_id": "general"},
        {"messages": [{"role": "user", "content": "latest chat"}], "project_id": "proj-1"},
    ]

    options = host._intent_conversation_options()

    assert [option["index"] for option in options[:2]] == [1, 0]
    assert options[0]["project_id"] == "proj-1"
    assert not any(option["selected"] for option in options)

    host._active_conversation_idx = 0
    selected_options = host._intent_conversation_options()

    assert [option for option in selected_options if option["selected"]][0]["index"] == 0


def test_apply_intent_project_choice_sets_active_or_creates_project(monkeypatch) -> None:
    """Verify intent overlay project choice updates the active project."""
    from core.conversation_store import store as conversation_store
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    applied = []
    host._active_project_id = "general"
    host._apply_memory_project = lambda: applied.append(host._active_project_id)  # type: ignore[method-assign]
    monkeypatch.setattr(
        conversation_store,
        "load_projects",
        lambda: [
            {"id": "general", "name": "General"},
            {"id": "proj-1", "name": "Personal OS"},
        ],
    )
    monkeypatch.setattr(
        conversation_store,
        "add_project",
        lambda name: {"id": "proj-new", "name": name},
    )

    existing = host._apply_intent_project_choice({"mode": "existing", "project_id": "proj-1"})

    assert existing == {"mode": "existing", "project_id": "proj-1"}
    assert host._active_project_id == "proj-1"

    created = host._apply_intent_project_choice({"mode": "new_project", "name": "New Work"})

    assert created == {"mode": "existing", "project_id": "proj-new"}
    assert host._active_project_id == "proj-new"
    assert applied[-2:] == ["proj-1", "proj-new"]


def test_chat_stream_preserves_structured_thought_chunks() -> None:
    """Verify chat stream yields thought metadata instead of flattening it."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = []
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()

    def emit(_event, payload):
        request_id = payload["request_id"]
        host._chat_chunk(request_id=request_id, text="Thinking first.", is_thought=True)
        host._chat_chunk(request_id=request_id, text="Answer.")
        host._chat_done(request_id=request_id, text="Answer.")

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "hi"}]))

    assert result == [
        {"type": "chunk", "text": "Thinking first.", "is_thought": True},
        "Answer.",
    ]


def test_active_history_includes_context_and_attachment_refs() -> None:
    """Verify selected conversation replay includes ambient context and refs."""
    from runtime.workers.ui_host import QtProtocolHost

    attachment = {
        "id": "att_1",
        "kind": "image",
        "source": "external_path",
        "path": r"C:\Users\sunny\Downloads\shot.png",
        "name": "shot.png",
        "mime": "image/png",
    }
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._all_conversations = [
        {
            "project_id": "general",
            "context": "Original ambient context",
            "messages": [
                {"role": "user", "content": "what is this?", "attachments": [attachment]},
                {"role": "assistant", "content": "a screenshot"},
            ],
        }
    ]

    history = host._chat_active_history()

    assert history["context"] == "Original ambient context"
    assert history["history"][0]["attachments"] == [attachment]
    assert "image_base64" not in history["history"][0]
