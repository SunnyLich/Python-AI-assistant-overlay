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


def test_chat_begin_conversation_persists_user_then_final_appends_assistant() -> None:
    """Verify overlay prompts are recoverable before the assistant reply lands."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    begin = host._chat_begin_conversation(user="edit notes", context="ctx", context_policy={"context_memory_mode": "on"})
    idx = begin["conversation_index"]

    assert begin["started"] is True
    assert idx == 0
    assert [message["role"] for message in host._all_conversations[0]["messages"]] == ["user"]
    assert host._all_conversations[0]["messages"][0]["content"] == "edit notes"

    host._chat_add_conversation(
        user="edit notes",
        assistant="done",
        append_user=False,
        conversation_index=idx,
        tool_context={"allowed_tools": ["edit_file"], "pinned_tools": [], "file_access_mode": "ask"},
    )

    messages = host._all_conversations[0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "done"
    assert len(persisted) == 2


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


def test_apply_intent_conversation_choice_preserves_new_selection() -> None:
    """Verify a canceled picker can retarget future prompts to a new chat."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "existing chat"}]},
    ]

    result = host._apply_intent_conversation_choice({"mode": "new"})

    assert result == {"mode": "new"}
    assert host._active_conversation_idx is None


def test_cancelled_intent_only_applies_touched_conversation_choice() -> None:
    """Verify plain cancel keeps the active chat but explicit picker changes stick."""
    from runtime.workers.ui_host import QtProtocolHost

    class FakeOverlay:
        def __init__(self, touched: bool, choice: dict):
            self._touched = touched
            self._choice = choice

        def conversation_choice_touched(self) -> bool:
            return self._touched

        def conversation_choice(self) -> dict:
            return self._choice

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {"messages": [{"role": "user", "content": "existing chat"}]},
        {"messages": [{"role": "user", "content": "latest chat"}]},
    ]

    host._apply_cancelled_intent_conversation_choice(FakeOverlay(False, {"mode": "new"}))
    assert host._active_conversation_idx == 0

    host._apply_cancelled_intent_conversation_choice(FakeOverlay(True, {"mode": "new"}))
    assert host._active_conversation_idx is None

    host._apply_cancelled_intent_conversation_choice(FakeOverlay(True, {"mode": "continue", "index": 1}))
    assert host._active_conversation_idx == 1


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


def test_chat_stream_preserves_progress_without_flattening_into_answer() -> None:
    """Verify chat progress chunks stay display-only and do not pollute answer text."""
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
        host._chat_chunk(request_id=request_id, text="Tool loop: unified Responses.", is_progress=True)
        host._chat_chunk(request_id=request_id, text="Answer.")
        host._chat_done(request_id=request_id, text="Answer.")

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "hi"}]))

    assert result == [
        {"type": "chunk", "text": "Tool loop: unified Responses.", "is_progress": True},
        "Answer.",
    ]


def test_live_file_approval_shows_chat_and_bubble() -> None:
    """Verify live file approvals render in chat and bubble together."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def __init__(self) -> None:
            self.requests: list[dict] = []

        def isVisible(self) -> bool:
            return True

        def request_live_file_approval(self, request: dict) -> dict:
            self.requests.append(request)
            return {"shown": True}

    class Overlay:
        def __init__(self) -> None:
            self.notices: list[str] = []

        def notify_agent_approval(self, text: str, **kwargs) -> dict:
            self.notices.append(text)
            kwargs["on_approve"]()
            return {"shown": True, "actionable": True}

    chat = Chat()
    overlay = Overlay()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = chat
    host._show_chat = lambda force_new=False: {"shown": True}  # type: ignore[method-assign]
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]

    result = host._live_file_approval_request(
        approval_id="file-1",
        action="edit_file",
        path="note.txt",
        details={"old_chars": 4, "new_chars": 8, "diff": "--- a/note.txt\n+++ b/note.txt\n-old\n+new text"},
    )

    assert result == {"approved": True, "feedback": "", "surface": "bubble"}
    assert len(chat.requests) == 1
    assert chat.requests[0]["approval_id"] == "file-1"
    assert overlay.notices
    assert "Why:" in overlay.notices[0]
    assert "Target:" in overlay.notices[0]
    assert "Diff: +1 -1 lines" in overlay.notices[0]


def test_live_file_approval_uses_bubble_when_chat_is_not_visible() -> None:
    """Verify live file approvals fall back to actionable bubble buttons."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def isVisible(self) -> bool:
            return False

    class Overlay:
        def __init__(self) -> None:
            self.notices: list[str] = []

        def notify_agent_approval(self, text: str, **kwargs) -> dict:
            self.notices.append(text)
            kwargs["on_approve"]()
            return {"shown": True, "actionable": True}

    overlay = Overlay()
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = Chat()
    host._show_chat = lambda force_new=False: {"shown": False}  # type: ignore[method-assign]
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]

    result = host._live_file_approval_request(approval_id="file-1", action="edit_file", path="note.txt")

    assert result == {"approved": True, "feedback": "", "surface": "bubble"}
    assert overlay.notices
    assert "edit_file" in overlay.notices[0]
    assert "note.txt" in overlay.notices[0]


def test_live_file_approval_can_be_resolved_from_chat_while_bubble_is_shown() -> None:
    """Verify the chat approval panel can resolve a request also shown in the bubble."""
    from runtime.workers.ui_host import QtProtocolHost

    class Chat:
        def __init__(self) -> None:
            self.callback = None
            self.resolver = None

        def isVisible(self) -> bool:
            return True

        def request_live_file_approval(self, request: dict) -> dict:
            self.callback = request.get("_on_decision")
            register = request.get("_register_resolver")
            if callable(register):
                register(lambda *_args: None)
            return {"shown": True}

    class Overlay:
        def __init__(self, chat: Chat) -> None:
            self.chat = chat
            self.notices: list[str] = []

        def notify_agent_approval(self, text: str, **_kwargs) -> dict:
            self.notices.append(text)
            self.chat.callback({"approved": False, "feedback": "Use a smaller patch.", "shown": True})
            return {"shown": True, "actionable": True}

    chat = Chat()
    overlay = Overlay(chat)
    host = QtProtocolHost.__new__(QtProtocolHost)
    host._chat = chat
    host._ensure_overlay = lambda: overlay  # type: ignore[method-assign]
    host._show_chat = lambda force_new=False: {"shown": True}  # type: ignore[method-assign]
    host._ensure_bubble = lambda: type("Bubble", (), {"start_thinking": lambda self: None})()  # type: ignore[method-assign]

    result = host._live_file_approval_request(approval_id="file-1", action="edit_file", path="note.txt")

    assert result == {
        "approved": False,
        "feedback": "Use a smaller patch.",
        "surface": "chat",
    }
    assert overlay.notices


def test_agent_approval_bubble_notice_does_not_timeout() -> None:
    """Verify unresolved approval bubble notices stay actionable indefinitely."""
    import pytest

    pytest.importorskip("PySide6")
    from ui.overlay import IconOverlay

    class Timer:
        def __init__(self) -> None:
            self.interval = None
            self.starts = 0
            self.stops = 0

        def stop(self) -> None:
            self.stops += 1

        def setInterval(self, value: int) -> None:  # noqa: N802 - Qt-style fake
            self.interval = value

        def start(self) -> None:
            self.starts += 1

    class Icon:
        def show(self) -> None:
            pass

        def raise_(self) -> None:
            pass

    class Bubble:
        def __init__(self) -> None:
            self.notice = None

        def show_notice(self, text: str, *, timeout_ms: int, actions: list) -> None:
            self.notice = {"text": text, "timeout_ms": timeout_ms, "actions": actions}

    bubble = Bubble()
    overlay = IconOverlay.__new__(IconOverlay)
    overlay._bubble = bubble
    timer = Timer()
    overlay._icon_hide_timer = timer
    overlay._icon_label = Icon()
    overlay._set_icon_pixmap = lambda _name: None  # type: ignore[method-assign]
    overlay._icon_backstop_ms = lambda: 4000  # type: ignore[method-assign]

    result = overlay.notify_agent_approval(
        "Permission needed.",
        on_approve=lambda: None,
        on_feedback=lambda: None,
        on_decline=lambda: None,
    )

    assert result == {"shown": True, "actionable": True}
    assert bubble.notice["timeout_ms"] == 0
    assert [label for label, _callback in bubble.notice["actions"]] == ["Approve", "Request Changes", "Decline"]
    assert timer.starts == 0
    assert timer.stops >= 1


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
