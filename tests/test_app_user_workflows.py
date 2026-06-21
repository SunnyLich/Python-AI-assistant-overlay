"""Broad user-visible workflow tests for Wisp.

These tests intentionally exercise product functions through the same seams a
user touches: project/chat state, context choices, prompt assembly, UI widgets,
memory scope, file/tool permissions, and route/stream contracts. External
boundaries stay fake unless a test is explicitly opt-in elsewhere.
"""
from __future__ import annotations

import os
import sys
import json
import types
import shutil
import textwrap
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.workflow


@dataclass(frozen=True)
class IsolatedAppState:
    root: Path
    chats: Path
    attachments: Path
    memory: Path


def _is_relative_to(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


@pytest.fixture
def isolated_app_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsolatedAppState:
    """Patch app stores to temp paths so workflow tests cannot touch user data."""
    from core.conversation_store import store as conversation_store
    from core.memory_store import store as memory_store

    root = tmp_path / "wisp-user-state"
    chats = root / "chats"
    attachments = chats / "attachments"
    memory = root / "memory"

    monkeypatch.setattr(conversation_store, "CHATS_DIR", chats)
    monkeypatch.setattr(conversation_store, "CHAT_ATTACHMENTS_DIR", attachments)
    monkeypatch.setattr(conversation_store, "PROJECTS_FILE", chats / "projects.json")
    monkeypatch.setattr(conversation_store, "CONVERSATIONS_FILE", chats / "conversations.json")

    monkeypatch.setattr(memory_store, "_MEMORY_DIR", str(memory))
    monkeypatch.setattr(memory_store, "_FALLBACK_PATH", str(memory / "facts_fallback.json"))
    monkeypatch.setattr(memory_store, "_manager", None)
    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", False, raising=False)
    memory_store.set_active_project(None)

    state = IsolatedAppState(root=root, chats=chats, attachments=attachments, memory=memory)
    for path in (state.chats, state.attachments, state.memory):
        assert _is_relative_to(path, state.root)
    yield state
    memory_store.set_active_project(None)
    monkeypatch.setattr(memory_store, "_manager", None)


@pytest.fixture
def qapp():
    """Return an offscreen QApplication, or skip when PySide6 is unavailable."""
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["wisp-workflow-tests"])


def _pump_until(app: Any, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def _ensure_brain_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    brain_dir = repo_root / "runtime" / "brain"
    if str(brain_dir) not in sys.path:
        sys.path.insert(0, str(brain_dir))


def _recording_stream_context(req_id: Any = "workflow"):
    _ensure_brain_path()
    from wisp_brain.handlers import StreamContext

    events: list[tuple[str, Any]] = []
    ctx = StreamContext(lambda event, data, _rid: events.append((event, data)), req_id)
    return events, ctx


def _minimal_agent_spec(scope: Path) -> dict[str, Any]:
    return {
        "title": "Workflow Agent",
        "objective": "Inspect the sample project and report what exists.",
        "scope_folder": str(scope),
        "sandbox_mode": "workspace-write: scope folder only",
        "approval_policy": "ask before escalation",
        "provider": "fake",
        "model": "workflow-fake",
        "reasoning_effort": "low",
        "max_runtime_minutes": 1,
        "max_turns": 1,
        "allow_shell": False,
        "allow_network": False,
        "allow_git": False,
        "allow_file_create": False,
        "allow_file_edit": False,
        "allow_file_delete": False,
        "required_context": "Use read-only inspection only.",
        "completion_criteria": "A final report exists.",
        "agents": [
            {
                "name": "Builder",
                "role": "Implementer",
                "provider": "same as task",
                "model": "same as task",
                "responsibility": "Read the scope and summarize it.",
            }
        ],
    }


def test_workflow_state_isolation_guard_and_memory_project_scope(isolated_app_state: IsolatedAppState):
    """A real project/conversation/memory workflow stays scoped to temp state."""
    from core.conversation_store import store as conversations
    from core.memory_store import store as memory

    project_a = conversations.add_project("Aurora")
    project_b = conversations.add_project("Borealis")
    conv = {
        "id": "conv-aurora",
        "project_id": project_a["id"],
        "messages": [
            {"role": "user", "content": "Remember that this project codename is Aurora Nebula."},
            {"role": "assistant", "content": "Stored."},
        ],
        "context": "[Conversation Context]\nProject test conversation.",
    }
    conversations.save_conversations([conv])

    assert _is_relative_to(conversations.PROJECTS_FILE, isolated_app_state.root)
    assert _is_relative_to(conversations.CONVERSATIONS_FILE, isolated_app_state.root)
    assert conversations.load_conversations()[0]["project_id"] == project_a["id"]

    memory.set_active_project(project_a["id"])
    manager = memory.get_manager()
    saved = manager.save_memory("This project codename is Aurora Nebula.")
    assert saved["ok"] is True
    assert saved["scope"] == "project"
    assert saved["project"] == project_a["id"]

    saved_general = manager.save_memory("The user prefers compact workflow reports.", scope="general")
    assert saved_general["ok"] is True
    rejected = manager.save_memory("api_key sk-proj-abcdefghijklmnopqrstuvwxyz1234567890")
    assert rejected["ok"] is False

    aurora_memory = manager.retrieve_relevant("what do you remember", project_id=project_a["id"])
    other_memory = manager.retrieve_relevant("what do you remember", project_id=project_b["id"])
    assert "Aurora Nebula" in aurora_memory
    assert "Aurora Nebula" not in other_memory
    assert "compact workflow reports" in aurora_memory
    assert "compact workflow reports" in other_memory

    facts_path = Path(memory._FALLBACK_PATH)
    assert _is_relative_to(facts_path, isolated_app_state.root)
    assert facts_path.exists()


def test_brain_query_workflow_assembles_context_and_redacts_secrets(
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """The normal query handler proves selected/doc/memory context reaches the model."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    from core.memory_store import store as memory

    memory.set_active_project("project-1")
    memory.get_manager().add_fact_manual(
        "The project marker phrase is workflow quartz.",
        "project_context",
        project="project-1",
    )

    events, ctx = _recording_stream_context("workflow-query")
    from wisp_brain import handlers

    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt="Use all context to answer.",
        selected="Selected traceback text",
        ambient_text="[Browser/Web]\nCurrent browser article.",
        active_document_text="Open document notes with password=supersecret",
        include_active_document=True,
        context_priority="Browser/Web",
        memory_enabled=True,
        memory_project="project-1",
        use_tools=False,
    )

    text = result["text"]
    assert "[fake-llm]" in text
    assert "Selected traceback text" in text
    assert "[Browser/Web]" in text
    assert "Context priority: Prioritize Browser/Web" in text
    assert "[REDACTED_CREDENTIAL]" in text
    assert "supersecret" not in text
    assert any(event == "reply.chunk" for event, _data in events)
    assert ("reply.done", {"text": text}) in events


def test_stream_cancel_stops_query_and_returns_partial_result(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cancelling a stream leaves a partial done payload instead of hanging stale UI."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    _ensure_brain_path()
    from wisp_brain import handlers

    events: list[tuple[str, Any]] = []
    ctx: Any = None

    def emit_and_cancel(event: str, data: Any, req_id: Any) -> None:
        events.append((event, data))
        assert req_id == "cancel-flow"
        if event == "reply.chunk":
            ctx.cancelled = True

    ctx = handlers.StreamContext(emit_and_cancel, "cancel-flow")
    result = handlers.brain_query(
        ctx,
        intent_prompt="Cancel after the first streamed token",
        selected="This selected text should not all stream.",
        memory_enabled=False,
    )

    chunks = [payload["text"] for event, payload in events if event == "reply.chunk"]
    assert len(chunks) == 1
    assert result["text"] == chunks[0]
    assert events[-1][0] == "reply.done"
    assert events[-1][1]["text"] == chunks[0]


def test_intent_overlay_project_conversation_and_context_chip_workflow(qapp, monkeypatch: pytest.MonkeyPatch):
    """Intent overlay exposes project/chat choice and stable context chip state."""
    monkeypatch.setattr(sys, "platform", "darwin")
    import config
    from runtime.supervisor.flows import FlowController
    from ui.intent_overlay import IntentOverlay

    monkeypatch.setattr(config, "INTENT_CONTEXT_TOGGLE_KEYS", "1234567", raising=False)
    screenshot_tokens = FlowController._screen_token_label({"screen_size": {"width": 1440, "height": 900}})
    assert screenshot_tokens not in {"", "0 tok", "? tok"}

    context_items = [
        {"id": "ambient", "key": "1", "label": "App", "state": "on", "tokens": "~4 tok"},
        {"id": "browser", "key": "2", "label": "Browser/Web", "state": "on", "tokens": "~8 tok"},
        {"id": "selection", "key": "3", "label": "Selection", "state": "on", "tokens": "~5 tok"},
        {"id": "clipboard", "key": "4", "label": "Clipboard", "state": "off", "tokens": "0 tok"},
        {"id": "screenshot", "key": "5", "label": "Screenshot", "state": "auto", "tokens": screenshot_tokens},
        {"id": "memory", "key": "6", "label": "Memory", "state": "auto", "tokens": "? tok"},
        {"id": "files", "key": "7", "label": "Files", "state": "auto", "tokens": ""},
    ]
    overlay = IntentOverlay(
        context_items=context_items,
        project_options=[{"id": "p1", "name": "Project One"}, {"id": "p2", "name": "Project Two"}],
        conversation_options=[
            {"index": 0, "title": "P1 old", "project_id": "p1", "selected": True},
            {"index": 2, "title": "P2 old", "project_id": "p2"},
        ],
        active_project_id="p1",
    )

    chosen: list[tuple[str, str]] = []
    cancelled: list[bool] = []
    overlay.intent_chosen.connect(lambda glyph, prompt: chosen.append((glyph, prompt)))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        assert overlay.project_choice() == {"mode": "existing", "project_id": "p1"}
        assert overlay.conversation_choice() == {"mode": "continue", "index": 0}
        assert {item["id"] for item in overlay.context_choices()} == {
            "ambient", "browser", "selection", "clipboard", "screenshot", "memory", "files"
        }

        assert overlay._cycle_context_key("3") is True
        selection = next(item for item in overlay.context_choices() if item["id"] == "selection")
        assert selection["state"] == "off"
        assert selection["touched"] is True

        overlay.update_context_items([
            {**item, "tokens": "~99 tok" if item["id"] == "selection" else item["tokens"]}
            for item in context_items
        ])
        selection = next(item for item in overlay.context_choices() if item["id"] == "selection")
        assert selection["state"] == "off"
        assert selection["tokens"] == "~99 tok"

        overlay._set_project_choice("p2")
        assert overlay.project_choice() == {"mode": "existing", "project_id": "p2"}
        assert overlay.conversation_choice() == {"mode": "new"}
        overlay._set_conversation_choice(2)
        assert overlay.conversation_choice() == {"mode": "continue", "index": 2}

        overlay._input_line.setText("Please answer with the selected project context.")
        overlay._fire_custom()
        assert chosen and chosen[-1][1] == "Please answer with the selected project context."
        assert cancelled == []
    finally:
        overlay.close()
        overlay.deleteLater()
        qapp.processEvents()


def test_chat_window_context_preview_send_and_history_workflow(qapp, isolated_app_state: IsolatedAppState):
    """Chat chips, previews, attachments, streaming, and history mutate one conversation."""
    from core.conversation_store import store as conversation_store
    from ui.chat_window import ChatWindow

    attachment = isolated_app_state.root / "attached.txt"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("attached file body", encoding="utf-8")
    conversations = [
        {
            "id": "conv-a",
            "project_id": "p1",
            "messages": [{"role": "user", "content": "Initial user turn"}],
            "context": "[Current Chat Context]\nLegacy context block",
            "context_policy": {},
        }
    ]
    preview_requests: list[dict] = []
    send_calls: list[dict] = []
    persisted: list[bool] = []

    def send_fn(messages, *, context_policy):
        send_calls.append({"messages": messages, "context_policy": dict(context_policy)})
        yield {
            "type": "metadata",
            "file_context": [
                {
                    "tool": "read_file",
                    "path": str(attachment),
                    "relative_path": "attached.txt",
                    "root": str(isolated_app_state.root),
                    "ok": True,
                    "message": "read attached.txt",
                }
            ],
            "tool_context": {"allowed_tools": ["read_file"], "file_access_mode": "read"},
        }
        yield "Workflow "
        yield "answer."

    window = ChatWindow(
        conversations,
        send_fn,
        projects=[{"id": "general", "name": "General"}, {"id": "p1", "name": "Project One"}],
        active_project_id="p1",
        persist_fn=lambda: persisted.append(True),
        on_context_preview=preview_requests.append,
    )
    try:
        window.show()
        qapp.processEvents()
        _pump_until(qapp, lambda: bool(preview_requests))
        preview_requests.clear()

        assert set(window._context_controls) == {
            "ambient", "browser", "selection", "clipboard", "screenshot", "memory", "files"
        }
        window._set_context_policy_state("browser", "on")
        window._set_context_policy_state("selection", "on")
        window._set_context_policy_state("memory", "auto")
        window._set_context_policy_state("files", "read")
        assert preview_requests
        preview_id = preview_requests[-1]["preview_id"]
        old_selection_tokens = window._context_control_tokens["selection"]
        window.update_context_preview("stale-preview", [{"id": "selection", "tokens": "~500 tok"}])
        assert window._context_control_tokens["selection"] == old_selection_tokens
        window.update_context_preview(
            preview_id,
            [
                {"id": "selection", "tokens": "~7 tok", "warning": ""},
                {"id": "memory", "tokens": "? tok", "warning": "Memory tokens are estimated after prompt."},
            ],
        )
        assert window._context_control_tokens["selection"] == "~7 tok"
        assert "Memory tokens" in window._context_controls["memory"].toolTip()

        conversations[0]["context_policy"]["context_browser_mode"] = "auto"
        window._pending_attachment_context = "Attached context from drop zone."
        window._pending_attachment_labels = ["attached.txt"]
        window._pending_attachments = [
            conversation_store.external_file_attachment(str(attachment), kind="text", source="external_path")
        ]
        window._refresh_attachment_label()
        qapp.processEvents()
        assert window._attachment_label is not None and window._attachment_label.isVisible()

        window._send("Question with current chat context")
        _pump_until(qapp, lambda: not window._streaming and len(conversations[0]["messages"]) >= 3)

        assert send_calls
        call = send_calls[0]
        assert call["context_policy"]["context_browser_mode"] == "auto"
        assert call["context_policy"]["context_memory_mode"] == "model"
        assert call["context_policy"]["file_access"] == "read"
        assert call["messages"][0]["role"] == "system"
        assert "Legacy context block" in call["messages"][0]["content"]
        assert "[Attached context for this message]" in call["messages"][-1]["content"]

        assert conversations[0]["messages"][-2]["role"] == "user"
        assert "Attached context from drop zone" in conversations[0]["messages"][-2]["context"]
        assert conversations[0]["messages"][-1]["role"] == "assistant"
        assert conversations[0]["messages"][-1]["content"] == "Workflow answer."
        assert conversations[0]["file_context"][0]["relative_path"] == "attached.txt"
        assert conversations[0]["tool_context"]["allowed_tools"] == ["read_file"]
        assert persisted

        conversations.append(
            {
                "id": "conv-b",
                "project_id": "p1",
                "messages": [{"role": "user", "content": "Externally added"}],
            }
        )
        window.ingest_new_conversations()
        assert window._stack.count() == 2
        window._toggle_pin(0)
        assert conversations[0]["pinned"] is True
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_context_buffer_drop_priority_and_privacy_workflow():
    """Context order, dropped documents, image promotion, and privacy are visible."""
    from core.query_pipeline import ContextInputs, GenerationCounter, build_context

    counter = GenerationCounter()
    first = counter.next()
    second = counter.next()
    assert not counter.is_current(first)
    assert counter.is_current(second)

    out = build_context(
        ContextInputs(
            intent_prompt="Summarize the current task.",
            selected="Selected user text",
            screenshot_b64=None,
            ambient_text="[Browser/Web]\nBrowser page",
            buffered_items=["Buffered Alt+Q context"],
            drop_items=[
                ("screen.png", "BASE64PNG", "image"),
                ("notes.md", "/tmp/notes.md", "document_path"),
                ("raw.txt", "raw dropped text", "text"),
            ],
            clipboard_text="Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            active_document_text="active doc password=supersecret",
            priority_context="Browser/Web",
        ),
        read_document_file=lambda path: f"document body from {path}",
    )

    assert out.screenshot_b64 == "BASE64PNG"
    assert out.user_message == "Summarize the current task."
    assert "Context priority: Prioritize Browser/Web" in out.ambient_ctx
    assert out.ambient_ctx.index("Buffered Alt+Q context") < out.ambient_ctx.index("[notes.md]")
    assert out.ambient_ctx.index("[notes.md]") < out.ambient_ctx.index("raw dropped text")
    assert out.ambient_ctx.index("raw dropped text") < out.ambient_ctx.index("Selected user text")
    assert "[BEARER_TOKEN]" in out.ambient_ctx
    assert "[REDACTED_CREDENTIAL]" in out.ambient_ctx
    assert "supersecret" not in out.ambient_ctx


def test_chat_history_projects_and_corruption_recovery_workflow(
    isolated_app_state: IsolatedAppState,
):
    """Chat/project persistence handles user actions and bad disk state."""
    from core.conversation_store import store as conversations

    conversations.PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    conversations.PROJECTS_FILE.write_text("{not valid json", encoding="utf-8")
    conversations.CONVERSATIONS_FILE.write_text("{not valid json", encoding="utf-8")

    projects_after_corruption = conversations.load_projects()
    assert projects_after_corruption[0]["id"] == conversations.GENERAL_PROJECT_ID
    assert conversations.load_conversations() == []

    project = conversations.add_project("Workflow History")
    assert conversations.add_project("workflow history")["id"] == project["id"]
    conversations.save_conversations(
        [
            {
                "id": "empty-placeholder",
                "project_id": project["id"],
                "messages": [{"role": "user", "content": "   "}],
            },
            {
                "id": "real-chat",
                "project_id": project["id"],
                "title": "",
                "pinned": True,
                "messages": [
                    {
                        "role": "user",
                        "content": "Summarize this attachment",
                        "image_base64": "base64-should-not-persist",
                        "attachments": [{"type": "image", "path": "shot.png", "mime": "image/png"}],
                    },
                    {"role": "assistant", "content": "Done"},
                ],
                "context_policy": {"memory": "on", "screenshot": "model"},
                "file_context": [{"path": "notes.txt", "text": "hello"}],
                "tool_context": {"read_file": "on"},
            },
        ]
    )

    loaded = conversations.load_conversations()
    assert [conv["id"] for conv in loaded] == ["real-chat"]
    saved_message = loaded[0]["messages"][0]
    assert "image_base64" not in saved_message
    assert saved_message["attachments"][0]["kind"] == "file"
    assert saved_message["attachments"][0]["mime"] == "image/png"
    assert loaded[0]["pinned"] is True
    assert loaded[0]["context_policy"] == {"memory": "on", "screenshot": "model"}

    assert conversations.delete_project(project["id"]) is True
    reassigned = conversations.load_conversations()[0]
    assert reassigned["project_id"] == conversations.GENERAL_PROJECT_ID
    assert conversations.delete_project(conversations.GENERAL_PROJECT_ID) is False
    assert _is_relative_to(conversations.CONVERSATIONS_FILE, isolated_app_state.root)


def test_bubble_settings_and_platform_popup_styles_workflow(qapp, monkeypatch: pytest.MonkeyPatch):
    """Bubble width and font size change independently; popup styles stay opaque."""
    import config
    from ui.bubble import SpeechBubble
    from ui.intent_overlay import IntentOverlay
    from ui.shared.theme import apply_app_theme

    monkeypatch.setattr(config, "BUBBLE_WIDTH", 320, raising=False)
    monkeypatch.setattr(config, "BUBBLE_LINES", 2, raising=False)
    monkeypatch.setattr(config, "BUBBLE_FONT_SIZE", 10, raising=False)
    monkeypatch.setattr(config, "ICON_SIZE", 80, raising=False)
    monkeypatch.setattr(config, "BUBBLE_COLOR", "#1c1c24dc", raising=False)
    monkeypatch.setattr(config, "BUBBLE_TEXT_COLOR", "#eeeeee", raising=False)
    monkeypatch.setattr(config, "BUBBLE_READ_WORD_COLOR", "#4da3ff", raising=False)
    monkeypatch.setattr(config, "THEME_MODE", "dark", raising=False)

    apply_app_theme(qapp)
    bubble = SpeechBubble()
    try:
        assert bubble._bubble_w == 320
        assert bubble._font.pointSize() == 10
        initial_height = bubble.height()

        monkeypatch.setattr(config, "BUBBLE_WIDTH", 520, raising=False)
        bubble.apply_config()
        assert bubble._bubble_w == 520
        assert bubble._font.pointSize() == 10

        monkeypatch.setattr(config, "BUBBLE_FONT_SIZE", 16, raising=False)
        bubble.apply_config()
        assert bubble._bubble_w == 520
        assert bubble._font.pointSize() == 16
        assert bubble.height() > initial_height

        app_style = qapp.styleSheet()
        assert "QComboBox QAbstractItemView" in app_style
        assert "background-color:" in app_style
        overlay = IntentOverlay()
        try:
            assert "background: transparent" not in overlay._menu_style()
        finally:
            overlay.close()
            overlay.deleteLater()
    finally:
        bubble.close()
        bubble.deleteLater()
        qapp.processEvents()


def test_tool_file_permission_and_approval_workflow(tmp_path: Path):
    """Agent file tools enforce roots, blocked globs, approvals, and diffs."""
    from core.agent.runtime import AgentPermissions, PermissionDenied, ScopeViolation
    from core.agent.toolbox import AgentToolbox
    from core.agent.workspace import ScopedWorkspace

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "public.txt").write_text("old text", encoding="utf-8")
    (root / "private").mkdir()
    (root / "private" / "secret.txt").write_text("secret", encoding="utf-8")

    ws = ScopedWorkspace(root, blocked_globs=["private/*"])
    assert ws.list_files() == ["public.txt"]
    with pytest.raises(ScopeViolation):
        ws.read_text("../outside.txt")
    with pytest.raises(ScopeViolation):
        ws.read_text("private/secret.txt")

    approval_requests: list[dict] = []

    def deny_once(request: dict) -> bool:
        approval_requests.append(request)
        return False

    tools = AgentToolbox(
        ws,
        AgentPermissions(allow_file_edit=True, allow_file_create=True),
        approval_callback=deny_once,
        require_approval=True,
        permission_modes={"file_edit": "ask permission"},
    )
    with pytest.raises(PermissionDenied, match="declined"):
        tools.write_file("public.txt", "new text")
    assert approval_requests[0]["action"] == "write_file"
    assert "-old text" in approval_requests[0]["diff"]
    assert "+new text" in approval_requests[0]["diff"]
    assert (root / "public.txt").read_text(encoding="utf-8") == "old text"

    approvals: list[dict] = []
    tools = AgentToolbox(
        ws,
        AgentPermissions(allow_file_edit=True, allow_file_create=True, allow_shell=False),
        approval_callback=lambda request: approvals.append(request) or True,
        require_approval=True,
        permission_modes={"file_edit": "ask permission"},
    )
    result = tools.patch_file("public.txt", "old", "approved")
    assert result.ok is True
    assert (root / "public.txt").read_text(encoding="utf-8") == "approved text"
    assert approvals and approvals[0]["action"] == "patch_file"

    with pytest.raises(PermissionDenied):
        tools.run_command(["python", "-m", "py_compile", "public.txt"])


def test_provider_fallback_cooldown_capability_and_auth_redaction_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Route fallback, cooldown, capability warnings, and auth status are user-safe."""
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth
    from core.llm_clients import client as llm_client

    _ensure_brain_path()
    from wisp_brain import handlers

    events: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        llm_client,
        "log_event",
        lambda name, message, **data: events.append((name, message, data)),
    )
    llm_client._route_cooldowns.clear()

    calls: list[tuple[str, str]] = []

    def fallback_factory(provider: str, model: str):
        calls.append((provider, model))
        if model == "primary":
            raise RuntimeError("429 rate limit from provider")
        yield "fallback answer"

    chunks = list(
        llm_client._stream_with_fallbacks(
            "query",
            [("openai", "primary"), ("openai", "fallback")],
            fallback_factory,
        )
    )
    assert chunks == ["fallback answer"]
    assert calls == [("openai", "primary"), ("openai", "fallback")]
    assert llm_client._is_route_cooling("openai", "primary") is True
    assert any(name == "llm.route_transient_fallback" for name, _message, _data in events)

    calls.clear()
    chunks = list(
        llm_client._stream_with_fallbacks(
            "query",
            [("openai", "primary"), ("openai", "fallback")],
            fallback_factory,
        )
    )
    assert chunks == ["fallback answer"]
    assert calls == [("openai", "fallback")]

    warnings = llm_client.screenshot_capability_warnings(
        ["model", "auto"],
        llm_provider="chatgpt",
        llm_model="gpt-5.5",
        vision_provider="copilot",
        vision_model="copilot-chat",
    )
    assert any("Let model decide" in warning for warning in warnings)
    assert any("Copilot cannot read screenshots" in warning for warning in warnings)
    assert llm_client.tool_capability_warnings(True, llm_provider="chatgpt")
    assert llm_client.subscription_auth_warnings(llm_provider="chatgpt", vision_provider="copilot")

    monkeypatch.setattr(
        chatgpt_auth,
        "get_tokens",
        lambda: {"access_token": "secret-chatgpt-token", "account_id": "acct-visible"},
    )
    monkeypatch.setattr(
        github_auth,
        "get_tokens",
        lambda: {"access_token": "secret-github-token", "user": {"login": "octo-user"}},
    )
    monkeypatch.setattr(
        copilot_auth,
        "token_status",
        lambda: (True, "Stored in OS keychain. Token format OK."),
    )
    status = handlers.brain_auth_status()
    rendered = repr(status)
    assert "acct-visible" in rendered
    assert "octo-user" in rendered
    assert "secret-chatgpt-token" not in rendered
    assert "secret-github-token" not in rendered


def test_supervisor_rewrite_snip_voice_and_dictation_workflow(tmp_path: Path):
    """Supervisor-level user paths cover paste-back, screenshots, voice, and dictation."""
    from tests.runtime.test_flows import (
        FakeWorker,
        caller_config,
        context_handler,
        make_flow,
        query_stream,
        voice_config,
    )

    rewrite_rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="bad grammar", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": query_stream("good grammar")})
    with caller_config(rewrite_rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    assert native.last_call("native.context.snapshot")["params"]["capture_focus"] is True
    assert brain.last_call("brain.rewrite")["params"]["selected_text"] == "bad grammar"
    paste = native.last_call("native.paste_text")["params"]
    assert paste["text"] == "good grammar"
    assert paste["target_pid"] == 777
    assert paste["focus_token"] == 9
    assert not ui.calls_for("ui.reply.notice")

    image_path = tmp_path / "snip.png"
    image_bytes = b"workflow-snip-image"
    image_path.write_bytes(image_bytes)
    snip_rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.capture.region": lambda _params: {"ok": True, "path": str(image_path)},
            "native.context.snapshot": context_handler(selected=""),
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    with caller_config(snip_rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "snip"})
        ui.emit("ui.snip.region", {"x": 1, "y": 2, "width": 30, "height": 40})
        ui.emit("ui.intent.chosen", {"custom": "What is in this image?"})

    assert ui.calls_for("ui.show_snip")
    assert native.last_call("native.capture.region")["params"]["region"]["height"] == 40
    assert brain.last_call("brain.query")["params"]["screenshot_b64"] == (
        __import__("base64").b64encode(image_bytes).decode("ascii")
    )

    voice_row = {
        "label": "Voice",
        "paste_back": False,
        "context_ambient": True,
        "context_clipboard": False,
        "context_documents_mode": "off",
        "context_browser_mode": "model",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "tools": {"alpha": "on", "beta": "model"},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    with voice_config(voice_row):
        _flow, native, ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.start")
    assert ui.last_call("ui.reply.transcript")["params"]["text"] == "voice prompt"
    voice_query = brain.last_call("brain.query")["params"]
    assert voice_query["intent_prompt"] == "voice prompt"
    assert voice_query["memory_enabled"] is False
    assert set(voice_query["allowed_tools"]) >= {"web_search", "get_context.browser", "alpha", "beta"}

    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "dictated words"}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)
    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    dictation_snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    dictation_paste = native.last_call("native.paste_text")["params"]
    assert dictation_snapshot["capture_focus"] is True
    assert dictation_paste["text"] == "dictated words"
    assert dictation_paste["target_pid"] == 777
    assert dictation_paste["focus_token"] == 9
    assert not _brain.calls_for("brain.query")


def test_context_disabled_sources_preview_and_os_native_contract_workflow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Disabled context does not leak, previews still estimate, and native fakes degrade."""
    from tests.runtime.test_flows import FakeWorker, caller_config, context_handler, make_flow, query_stream

    all_off_policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected chat bubble")})
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)

    ui.emit(
        "ui.chat.request",
        {
            "request_id": "chat-1",
            "messages": [{"role": "user", "content": "can you see hidden context?"}],
            "context_policy": all_off_policy,
        },
    )
    chat_params = brain.last_call("brain.chat")["params"]
    assert not native.calls_for("native.context.snapshot")
    assert "selected chat bubble" not in repr(chat_params["messages"])
    assert chat_params["memory_enabled"] is False
    assert chat_params["allowed_tools"] == []

    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "selected chat text",
                "clipboard_text": "clipboard chat text",
                "active_app": {"name": "Preview App", "pid": 42, "bundle_id": "com.preview"},
                "screen_size": {"width": 1920, "height": 1080},
            },
        }
    )
    _flow, native, ui, _brain, _audio = make_flow(native=native)
    ui.emit("ui.chat.context_preview", {"preview_id": "preview-all-off", "context_policy": all_off_policy})
    chips = {item["id"]: item for item in ui.last_call("ui.chat.context_preview")["params"]["context_items"]}
    assert chips["screenshot"]["state"] == "off"
    assert chips["screenshot"]["tokens"] == "~1.1k tok"
    assert chips["selection"]["tokens"].startswith("~")
    assert chips["clipboard"]["tokens"].startswith("~")
    assert not native.calls_for("native.capture.fullscreen")

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "model",
            "context_github_mode": "auto",
            "context_memory_mode": "off",
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
            "tools": {
                "web_search": "off",
                "get_context": "off",
                "retrieve_website": "off",
                "git_status": "off",
                "capture_screen": "off",
            },
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="picked")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "go"})
    query = brain.last_call("brain.query")["params"]
    assert query["allowed_tools"] == []
    assert query["use_tools"] is False
    assert query["frontload_tools"] == ["git_diff"]
    assert query["allow_screenshot_tool"] is False
    assert not native.calls_for("native.capture.fullscreen")

    from core import context_fetcher
    from runtime.workers import native_host

    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", True)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "TextEdit", "pid": 101, "bundle_id": "com.apple.TextEdit"},
    )
    monkeypatch.setattr(native_host, "selected_text", lambda: "")
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})
    monkeypatch.setattr(
        "core.platform.macos_native.list_document_windows",
        lambda: [
            {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt"},
            {"process_name": "Safari", "pid": 303, "frontmost": False, "title": "Example Page"},
        ],
    )
    monkeypatch.setattr(context_fetcher, "_mac_browser_url", lambda _app: "https://example.test/page")
    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )
    assert snapshot["document_window"]["title"] == "Notes.txt"
    assert snapshot["browser_app"] == "Safari"
    assert snapshot["browser_url"] == "https://example.test/page"


def test_prompt_tool_keywords_and_memory_scheduler_settings_workflow(
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Prompt, tool keyword, and memory scheduling settings change runtime behavior."""
    import config
    from core.memory_store import store as memory_store
    from core.tool_registry import ToolRegistry, ToolSpec

    monkeypatch.setattr(config, "SYSTEM_PROMPT_UTILITY", "Base workflow system prompt.", raising=False)
    monkeypatch.setattr(config, "ASSISTANT_LANGUAGE", "Spanish", raising=False)
    prompt = config.get_system_prompt()
    assert "Base workflow system prompt." in prompt
    assert "Respond in Spanish" in prompt

    registry = ToolRegistry(plugin_dir=tmp_path / "no-tools")
    registry.register_builtin(
        ToolSpec(
            name="workflow_tool",
            description="Workflow demo tool",
            input_schema={"type": "object", "properties": {}},
            executor=lambda _inputs: "ok",
        )
    )
    registry.set_keyword_filter("workflow_tool", ["deploy"])
    assert "workflow_tool" not in {
        schema["name"] for schema in registry.filtered_schemas("hello there", include_server_tools=False)
    }
    assert "workflow_tool" in {
        schema["name"] for schema in registry.filtered_schemas("please deploy", include_server_tools=False)
    }
    keywords_path = tmp_path / "tool_keywords.json"
    registry.save_keyword_filters(keywords_path)
    reloaded = ToolRegistry(plugin_dir=tmp_path / "no-tools")
    reloaded.register_builtin(
        ToolSpec(
            name="workflow_tool",
            description="Workflow demo tool",
            input_schema={"type": "object", "properties": {}},
            executor=lambda _inputs: "ok",
        )
    )
    reloaded.load_keyword_filters(keywords_path)
    assert "workflow_tool" in {
        schema["name"] for schema in reloaded.filtered_schemas("deploy now", include_server_tools=False)
    }

    timers: list[Any] = []

    class FakeTimer:
        def __init__(self, interval: float, callback):
            self.interval = interval
            self.callback = callback
            self.daemon = False
            self.started = False
            self.cancelled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(memory_store.threading, "Timer", FakeTimer)
    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", True, raising=False)
    monkeypatch.setattr(memory_store.config, "MEMORY_CONSOLIDATION_INTERVAL", 7, raising=False)
    manager = memory_store.MemoryManager()
    assert timers[-1].interval == 7 * 60
    assert timers[-1].started is True
    assert timers[-1].daemon is True

    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", False, raising=False)
    manager._sync_consolidation_timer()
    assert timers[-1].cancelled is True
    assert manager._consolidation_timer is None
    assert _is_relative_to(isolated_app_state.memory, isolated_app_state.root)


def test_brain_memory_crud_and_project_scope_workflow(
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """The Memory UI backend can add, search, edit, delete, and scope facts."""
    _ensure_brain_path()
    from core.memory_store import store as memory_store
    from wisp_brain import handlers

    memory_store.set_active_project("alpha-project")
    project_add = handlers.brain_memory_add(
        text="The alpha project launch phrase is silver comet.",
        scope="project",
    )
    general_add = handlers.brain_memory_add(
        text="The global preferred editor is Nova.",
        category="preference",
    )

    assert project_add["ok"] is True
    assert project_add["project"] == "alpha-project"
    assert general_add["ok"] is True
    listed = handlers.brain_memory_list()["facts"]
    assert {item["text"] for item in listed} >= {
        "The alpha project launch phrase is silver comet.",
        "The global preferred editor is Nova.",
    }

    project_fact = next(item for item in listed if "silver comet" in item["text"])
    search = handlers.brain_memory_search(query="What is the launch phrase?", top_k=5)
    assert "silver comet" in search["text"]

    updated = handlers.brain_memory_update(
        fact_id=project_fact["id"],
        text="The alpha project launch phrase is blue comet.",
        project="alpha-project",
    )
    assert updated["ok"] is True
    assert "blue comet" in handlers.brain_memory_search(query="launch phrase", top_k=5)["text"]

    deleted = handlers.brain_memory_delete(fact_id=project_fact["id"])
    assert deleted["ok"] is True
    after_delete = handlers.brain_memory_list()["facts"]
    assert all(item["id"] != project_fact["id"] for item in after_delete)
    assert _is_relative_to(isolated_app_state.memory, isolated_app_state.root)

    memory_store.set_active_project(None)
    monkeypatch.setattr(memory_store, "_manager", None)


def test_brain_rewrite_chat_tts_and_route_setting_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Settings test buttons and text workflows work offline without spending tokens."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    monkeypatch.setenv("WISP_RUNTIME_OUTPUT_DIR", str(tmp_path / "runtime-output"))
    _ensure_brain_path()
    from wisp_brain import handlers

    rewrite_events, rewrite_ctx = _recording_stream_context("rewrite")
    rewrite = handlers.brain_rewrite(
        rewrite_ctx,
        selected_text="this sentence need polish",
        intent_prompt="Make it concise",
    )
    assert rewrite["text"].startswith("[fake-rewrite]")
    assert any(event == "reply.done" for event, _payload in rewrite_events)

    chat_events, chat_ctx = _recording_stream_context("chat")
    chat = handlers.brain_chat(
        chat_ctx,
        messages=[
            {"role": "system", "content": "Keep replies tiny."},
            {"role": "user", "content": "What is my last message?"},
        ],
        memory_enabled=False,
    )
    assert "[fake-chat] What is my last message?" in chat["text"]
    assert any(event == "reply.chunk" for event, _payload in chat_events)

    route = handlers.brain_llm_test(
        provider="chatgpt",
        model="gpt-5.5",
        fallbacks="openai:gpt-4.1-mini",
        route_name="Chat",
    )
    assert route["ok"] is True
    assert [item["label"] for item in route["routes"]] == ["Primary", "Fallback 1"]
    assert route["routes"][0]["model"] == "gpt-5.5"

    tts_test = handlers.brain_tts_test(provider="none")
    assert tts_test["ok"] is True
    assert tts_test["provider"] == "none"
    tts = handlers.brain_tts_synthesize(text="short test")
    assert Path(tts["path"]).exists()
    assert tts["provider"] == "fake"


def test_settings_env_changes_reach_runtime_surfaces_workflow(
    tmp_path: Path,
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """Saved settings reload into config and change the surfaces users touch."""
    import config
    from core.memory_store import store as memory_store

    managed_keys = {
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_FALLBACKS",
        "CHAT_LLM_PROVIDER",
        "CHAT_LLM_MODEL",
        "VISION_LLM_PROVIDER",
        "VISION_LLM_MODEL",
        "TTS_PROVIDER",
        "STT_MODEL",
        "STT_DEVICE",
        "STT_LANGUAGE",
        "STT_BEAM_SIZE",
        "BUBBLE_WIDTH",
        "BUBBLE_LINES",
        "BUBBLE_FONT_SIZE",
        "BUBBLE_HIDE_DELAY_MS",
        "INTENT_CONTEXT_TOGGLE_KEYS",
        "CALLER_COUNT",
        "CALLER_1_LABEL",
        "CALLER_1_CONTEXT_AMBIENT",
        "CALLER_1_CONTEXT_DOCUMENTS_MODE",
        "CALLER_1_CONTEXT_BROWSER_MODE",
        "CALLER_1_CONTEXT_MEMORY_MODE",
        "CALLER_1_CONTEXT_SCREENSHOT",
        "CALLER_1_FILE_ACCESS",
        "VOICE_CONTEXT_AMBIENT",
        "VOICE_CONTEXT_DOCUMENTS_MODE",
        "VOICE_CONTEXT_BROWSER_MODE",
        "VOICE_CONTEXT_MEMORY_MODE",
        "VOICE_CONTEXT_SCREENSHOT",
        "MEMORY_LLM_PROVIDER",
        "MEMORY_LLM_MODEL",
        "MEMORY_LLM_FALLBACKS",
        "MEMORY_TOP_K",
        "MEMORY_STM_TOKEN_BUDGET",
        "CONTEXT_BROWSER_MAX_CHARS",
        "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS",
        "CONTEXT_TOOL_DOCUMENT_MAX_CHARS",
        "TRUST_PRIVACY_MODE",
    }
    original_env_file = config._ENV_FILE
    original_loaded_keys = set(config._LOADED_DOTENV_KEYS)
    original_env_values = {key: os.environ.get(key) for key in managed_keys}
    temp_env = tmp_path / ".env"
    temp_env.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=chatgpt",
                "LLM_MODEL=gpt-5.5",
                "LLM_FALLBACKS=openai:gpt-4.1-mini",
                "CHAT_LLM_PROVIDER=anthropic",
                "CHAT_LLM_MODEL=claude-sonnet-4-5",
                "VISION_LLM_PROVIDER=openai",
                "VISION_LLM_MODEL=gpt-4.1-mini",
                "TTS_PROVIDER=none",
                "STT_MODEL=small",
                "STT_DEVICE=cpu",
                "STT_LANGUAGE=en",
                "STT_BEAM_SIZE=3",
                "BUBBLE_WIDTH=456",
                "BUBBLE_LINES=4",
                "BUBBLE_FONT_SIZE=19",
                "BUBBLE_HIDE_DELAY_MS=2400",
                "INTENT_CONTEXT_TOGGLE_KEYS=7654321",
                "CALLER_COUNT=1",
                "CALLER_1_LABEL=Workflow Caller",
                "CALLER_1_CONTEXT_AMBIENT=False",
                "CALLER_1_CONTEXT_DOCUMENTS_MODE=model",
                "CALLER_1_CONTEXT_BROWSER_MODE=auto",
                "CALLER_1_CONTEXT_MEMORY_MODE=off",
                "CALLER_1_CONTEXT_SCREENSHOT=model",
                "CALLER_1_FILE_ACCESS=read",
                "VOICE_CONTEXT_AMBIENT=False",
                "VOICE_CONTEXT_DOCUMENTS_MODE=off",
                "VOICE_CONTEXT_BROWSER_MODE=model",
                "VOICE_CONTEXT_MEMORY_MODE=model",
                "VOICE_CONTEXT_SCREENSHOT=auto",
                "MEMORY_LLM_PROVIDER=openai",
                "MEMORY_LLM_MODEL=gpt-4.1-mini",
                "MEMORY_LLM_FALLBACKS=chatgpt:gpt-5.5",
                "MEMORY_TOP_K=1",
                "MEMORY_STM_TOKEN_BUDGET=1234",
                "CONTEXT_BROWSER_MAX_CHARS=111",
                "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS=222",
                "CONTEXT_TOOL_DOCUMENT_MAX_CHARS=333",
                "TRUST_PRIVACY_MODE=True",
            ]
        ),
        encoding="utf-8",
    )

    try:
        for key in managed_keys:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(config, "_ENV_FILE", temp_env)
        monkeypatch.setattr(config, "_LOADED_DOTENV_KEYS", set())
        monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
        config.reload()

        settings = config.get_settings()
        assert settings.llm.model == "gpt-5.5"
        assert settings.chat_llm.provider == "chatgpt"
        assert settings.chat_llm.model == "gpt-5.5"
        assert settings.vision_llm.model == "gpt-4.1-mini"
        assert settings.ui.bubble_width == 456
        assert settings.ui.bubble_font_size == 19
        assert settings.audio.tts_provider == "none"
        assert settings.audio.stt_model == "small"
        assert settings.memory.top_k == 1
        assert settings.context.browser_max_chars == 111
        assert config.INTENT_CONTEXT_TOGGLE_KEYS == "7654321"

        caller = settings.callers.callers[0]
        assert caller["label"] == "Workflow Caller"
        assert caller["context_ambient"] is False
        assert caller["context_documents_mode"] == "model"
        assert caller["context_browser_mode"] == "auto"
        assert caller["context_memory_mode"] == "off"
        assert caller["context_screenshot"] == "model"
        assert caller["file_access"] == "read"

        voice = settings.callers.voice
        assert voice["context_ambient"] is False
        assert voice["context_browser_mode"] == "model"
        assert voice["context_memory_mode"] == "model"
        assert voice["context_screenshot"] == "auto"

        _ensure_brain_path()
        from wisp_brain import handlers

        route = handlers.brain_llm_test(
            provider=settings.memory.model.provider,
            model=settings.memory.model.model,
            fallbacks=settings.memory.model.fallbacks,
            route_name="Memory",
        )
        assert route["ok"] is True
        assert [(item["provider"], item["model"]) for item in route["routes"]] == [
            ("openai", "gpt-4.1-mini"),
            ("chatgpt", "gpt-5.5"),
        ]

        memory_store.set_active_project(None)
        memory_store.get_manager().add_fact_manual("first workflow memory fact", "general")
        memory_store.get_manager().add_fact_manual("second workflow memory fact", "general")
        search = handlers.brain_memory_search(query="workflow memory fact")
        assert search["text"].count("workflow memory fact") <= config.MEMORY_TOP_K
        assert _is_relative_to(isolated_app_state.memory, isolated_app_state.root)
    finally:
        for key, value in original_env_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        config._ENV_FILE = original_env_file
        config._LOADED_DOTENV_KEYS = original_loaded_keys
        config.reload()
        memory_store.set_active_project(None)
        monkeypatch.setattr(memory_store, "_manager", None)


def test_brain_addon_install_settings_actions_and_toggle_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Add-on management covers install, list, setting save, actions, hotkeys, enable state."""
    _ensure_brain_path()
    from core import addon_store
    import core.addon_manager as addon_manager
    import core.system.paths as system_paths
    from wisp_brain import handlers

    addons_dir = tmp_path / "installed-addons"
    source = tmp_path / "source-addon"
    source.mkdir()
    (source / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "workflow-demo"
            name = "workflow_demo"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            response = "read"
            hotkeys = true
            ui = ["tray", "settings", "intents", "notifications"]

            [[hotkeys]]
            id = "static-hotkey"
            label = "Static hotkey"
            hotkey = "ctrl+alt+w"
            prompt = "Static workflow prompt"

            [[notifications]]
            title = "Workflow"
            message = "Loaded"
            """
        ).strip(),
        encoding="utf-8",
    )
    (source / "__init__.py").write_text(
        textwrap.dedent(
            """
            def before_query(prompt, context):
                return prompt + " from addon", context + " addon-context"

            def get_tray_actions():
                return [{"label": "Act", "callback": lambda: None}]

            def run_tray_action(label):
                return {"message": "ran " + str(label)}

            def get_settings():
                return [{"key": "greeting", "label": "Greeting", "type": "text", "default": "hi"}]

            def get_hotkeys():
                return [{"id": "dynamic-hotkey", "label": "Dynamic", "hotkey": "ctrl+alt+d", "callback": lambda payload: {"message": "hotkey ok"}}]

            def get_intents():
                return [{"id": "dynamic", "label": "Dynamic", "key": "d", "prompt": "Dynamic prompt"}]

            def get_notifications():
                return [{"title": "Runtime", "message": "Ready"}]
            """
        ).strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(system_paths, "ADDONS_DIR", addons_dir)
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager.addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager, "_manager", None)
    monkeypatch.setattr(handlers, "_addon_startup_done", False)

    try:
        installed = handlers.brain_addons_install_folder(path=str(source))
        assert installed["id"] == "workflow-demo"
        assert Path(installed["path"]).is_dir()
        listed = handlers.brain_addons_list()["addons"]
        addon = next(item for item in listed if item["id"] == "workflow-demo")
        assert addon["enabled"] is True
        assert "Act" in addon["tray_actions"]
        assert addon["settings"][0]["value"] == "hi"
        assert {item["id"] for item in addon["hotkeys"]} >= {"static-hotkey", "dynamic-hotkey"}

        assert handlers.brain_addons_set_setting(
            addon_id="workflow-demo",
            key="greeting",
            value="hello",
        )["ok"] is True
        settings_after_save = next(
            item for item in handlers.brain_addons_list()["addons"] if item["id"] == "workflow-demo"
        )["settings"]
        assert settings_after_save[0]["value"] == "hello"

        assert handlers.brain_addons_run_hotkey(
            addon_id="workflow-demo",
            hotkey_id="static-hotkey",
        ) == {"prompt": "Static workflow prompt"}
        dynamic_hotkey = handlers.brain_addons_run_hotkey(
            addon_id="workflow-demo",
            hotkey_id="dynamic-hotkey",
        )
        assert dynamic_hotkey["message"] == "hotkey ok"
        assert handlers.brain_addons_run_action(addon_id="workflow-demo", label="Act")["ok"] is True

        assert handlers.brain_addons_set_enabled(addon_id="workflow-demo", enabled=False)["enabled"] is False
        disabled = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "workflow-demo")
        assert disabled["enabled"] is False
        with pytest.raises(ValueError):
            handlers.brain_addons_run_action(addon_id="workflow-demo", label="Act")

        assert handlers.brain_addons_set_enabled(addon_id="workflow-demo", enabled=True)["enabled"] is True
        enabled = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "workflow-demo")
        assert enabled["enabled"] is True
    finally:
        try:
            addon_manager.get_manager().on_shutdown()
        except Exception:
            pass
        monkeypatch.setattr(addon_manager, "_manager", None)


def test_addon_update_remove_bad_archive_and_credentials_reset_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Add-on reload/update/remove failures and credential reset stay isolated."""
    _ensure_brain_path()
    from core import addon_store, secret_store
    from core.addon_distribution import install_addon_archive, install_addon_folder
    import core.addon_manager as addon_manager
    import core.system.paths as system_paths
    import config
    from wisp_brain import handlers

    addons_dir = tmp_path / "addons"
    monkeypatch.setattr(system_paths, "ADDONS_DIR", addons_dir)
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager.addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(addon_manager, "_manager", None)

    source = tmp_path / "archive-source" / "archive_demo"
    source.mkdir(parents=True)
    (source / "addon.toml").write_text(
        "[addon]\nid = 'archive-demo'\nname = 'Archive Demo'\nentry = '__init__.py'\n"
        "[permissions]\nui = ['settings']\n"
        "[[settings]]\nkey = 'version'\nlabel = 'Version'\ntype = 'text'\ndefault = 'v1'\n",
        encoding="utf-8",
    )
    (source / "__init__.py").write_text("", encoding="utf-8")
    archive = tmp_path / "archive-demo.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(source / "addon.toml", "archive_demo/addon.toml")
        zf.write(source / "__init__.py", "archive_demo/__init__.py")

    unsafe = tmp_path / "unsafe.wisp"
    with zipfile.ZipFile(unsafe, "w") as zf:
        zf.writestr("../escape.txt", "nope")
    with pytest.raises(ValueError, match="unsafe"):
        install_addon_archive(unsafe, addons_dir)

    installed = handlers.brain_addons_install_archive(path=str(archive))
    assert installed["id"] == "archive-demo"
    addon = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "archive-demo")
    assert addon["settings"][0]["value"] == "v1"
    assert handlers.brain_addons_set_setting("archive-demo", "version", "custom")["ok"] is True
    assert "custom" in (tmp_path / "addons.json").read_text(encoding="utf-8")

    updated = tmp_path / "updated-source" / "archive_demo"
    updated.mkdir(parents=True)
    (updated / "addon.toml").write_text(
        "[addon]\nid = 'archive-demo'\nname = 'Archive Demo'\nentry = '__init__.py'\n"
        "description = 'updated manifest'\n"
        "[permissions]\nui = ['settings']\n"
        "[[settings]]\nkey = 'version'\nlabel = 'Version'\ntype = 'text'\ndefault = 'v2'\n",
        encoding="utf-8",
    )
    (updated / "__init__.py").write_text("", encoding="utf-8")
    install_addon_folder(updated, addons_dir, replace=True)
    addon_manager.get_manager().load_all()
    updated_payload = next(item for item in handlers.brain_addons_list()["addons"] if item["id"] == "archive-demo")
    assert updated_payload["description"] == "updated manifest"
    assert updated_payload["settings"][0]["value"] == "custom"

    shutil.rmtree(addons_dir / "archive-demo")
    addon_manager.get_manager().load_all()
    assert all(item["id"] != "archive-demo" for item in handlers.brain_addons_list()["addons"])

    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=should-be-cleared\nUNRELATED_VALUE=kept\n", encoding="utf-8")
    monkeypatch.setattr(config, "_ENV_FILE", env_file)
    monkeypatch.setenv("OPENAI_API_KEY", "should-be-cleared")
    monkeypatch.setenv("UNRELATED_VALUE", "kept")
    deleted: list[str] = []
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ["OPENAI_API_KEY", "CUSTOM_API_KEY"])
    monkeypatch.setattr(secret_store, "delete_secret", lambda name: deleted.append(name))

    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    cleared_auth: list[str] = []
    monkeypatch.setattr(chatgpt_auth, "clear_tokens", lambda: cleared_auth.append("chatgpt"))
    monkeypatch.setattr(github_auth, "clear_tokens", lambda: cleared_auth.append("github"))
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: cleared_auth.append("copilot"))

    reset = handlers.brain_settings_reset_credentials()
    assert reset["ok"] is True
    assert set(deleted) == {"OPENAI_API_KEY", "CUSTOM_API_KEY"}
    assert cleared_auth == ["chatgpt", "github", "copilot"]
    assert os.environ.get("OPENAI_API_KEY") is None
    assert os.environ.get("UNRELATED_VALUE") == "kept"

    try:
        addon_manager.get_manager().on_shutdown()
    except Exception:
        pass
    monkeypatch.setattr(addon_manager, "_manager", None)


def test_agent_history_last_spec_and_approval_response_workflow(tmp_path: Path):
    """The agent task UI can save/copy-last, read run history, continue, and resolve approvals."""
    _ensure_brain_path()
    from wisp_brain import handlers

    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "README.md").write_text("# Demo\n\nWorkflow scope.", encoding="utf-8")
    log_root = tmp_path / "agent-log-root"
    spec = _minimal_agent_spec(scope)

    saved = handlers.brain_agent_last_spec_write(spec=spec, log_root=str(log_root))
    assert saved["ok"] is True
    assert saved["spec"]["title"] == "Workflow Agent"
    assert handlers.brain_agent_last_spec_read(log_root=str(log_root))["spec"]["scope_folder"] == str(scope)

    run_dir = log_root / "20260620-010203-workflow"
    run_dir.mkdir(parents=True)
    (run_dir / "task.json").write_text(json.dumps(spec), encoding="utf-8")
    (run_dir / "run.log").write_text("agent run finished\n", encoding="utf-8")
    (run_dir / "final.md").write_text("Final report for workflow.", encoding="utf-8")
    (run_dir / "diff.patch").write_text("diff --git a/README.md b/README.md\n", encoding="utf-8")

    history = handlers.brain_agent_history_list(log_root=str(log_root), limit=5)
    assert history["runs"][0]["id"] == run_dir.name
    assert history["runs"][0]["status"] == "complete"
    read = handlers.brain_agent_history_read(run_dir=str(run_dir))
    assert "Final report for workflow." in read["final"]
    assert handlers.brain_agent_history_retry_spec(run_dir=str(run_dir))["spec"]["title"] == "Workflow Agent"
    continued = handlers.brain_agent_history_continue_spec(run_dir=str(run_dir))["spec"]
    assert continued["title"].startswith("Continue:")
    assert "Previous final report" in continued["required_context"]

    approval_id = "approval-workflow"
    event = threading.Event()
    with handlers._AGENT_APPROVALS_LOCK:
        handlers._AGENT_APPROVALS[approval_id] = {"event": event, "approved": False}
    response = handlers.brain_agent_approval_respond(approval_id=approval_id, approved=True)
    assert response == {"ok": True, "approved": True}
    assert event.wait(0.1)
    with handlers._AGENT_APPROVALS_LOCK:
        handlers._AGENT_APPROVALS.pop(approval_id, None)
    assert handlers.brain_agent_approval_respond(approval_id=approval_id, approved=True)["ok"] is False

    live_id = "live-file-workflow"
    live_event = threading.Event()
    with handlers._LIVE_FILE_APPROVALS_LOCK:
        handlers._LIVE_FILE_APPROVALS[live_id] = {"event": live_event, "approved": False}
    live_response = handlers.brain_live_file_approval_respond(approval_id=live_id, approved=False)
    assert live_response == {"ok": True, "approved": False}
    assert live_event.wait(0.1)
    with handlers._LIVE_FILE_APPROVALS_LOCK:
        handlers._LIVE_FILE_APPROVALS.pop(live_id, None)


def test_auto_agent_run_streams_logs_and_persists_artifacts_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A real brain.agent.run completes offline and produces UI-readable artifacts."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")
    _ensure_brain_path()
    from wisp_brain import handlers

    scope = tmp_path / "agent-scope"
    scope.mkdir()
    (scope / "README.md").write_text("# Agent Workflow\n", encoding="utf-8")
    log_root = tmp_path / "agent-runs"
    spec = _minimal_agent_spec(scope)

    events, ctx = _recording_stream_context("agent-run")
    result = handlers.brain_agent_run(ctx, spec=spec, log_root=str(log_root))

    run_dir = Path(result["run_dir"])
    assert run_dir.is_dir()
    assert result["error"] == ""
    assert "Fake agent run complete." in result["final"]
    assert (run_dir / "task.json").exists()
    assert (run_dir / "run.log").exists()
    assert (run_dir / "final.md").read_text(encoding="utf-8").strip()
    assert any(event == "agent.log" for event, _payload in events)
    assert events[-1][0] == "agent.done"
    assert events[-1][1]["run_dir"] == str(run_dir)

    history = handlers.brain_agent_history_list(log_root=str(log_root), limit=5)
    assert any(item["run_dir"] == str(run_dir) for item in history["runs"])
    continued = handlers.brain_agent_history_continue_spec(run_dir=str(run_dir))["spec"]
    assert continued["title"].startswith("Continue:")
    assert "Fake agent run complete." in continued["required_context"]


def test_launch_duplicate_crash_log_and_worker_lifecycle_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Launch behavior covers duplicate handoff, UI-worker exit, crash logs, and shutdown."""
    from runtime.supervisor import app as supervisor_app

    monkeypatch.delenv("WISP_RUN_LOG_DIR", raising=False)
    monkeypatch.delenv("WISP_RUNTIME_LOG_MODE", raising=False)
    monkeypatch.setattr(supervisor_app, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(supervisor_app, "suppress_console_ctrl_c", lambda: None)
    monkeypatch.setattr(supervisor_app, "install_crash_diagnostics", lambda: None)

    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: False)

    class ShouldNotStart:
        def __init__(self):
            raise AssertionError("duplicate launch must not start workers")

    monkeypatch.setattr(supervisor_app, "WispSupervisor", ShouldNotStart)
    assert supervisor_app.main() == 2
    assert not (tmp_path / "build_logs").exists()

    instances: list[Any] = []

    class FakeWorker:
        def __init__(self, name: str):
            self.name = name
            self.exit_handler = None

        def on_exit(self, handler):
            self.exit_handler = handler

        def stderr_tail(self, _max_lines: int = 20) -> str:
            return f"{self.name} recent diagnostic line"

    class FakeSupervisor:
        def __init__(self):
            self.workers = {name: FakeWorker(name) for name in ("native", "ui", "brain", "audio")}
            self.shutdown_called = False
            instances.append(self)

        def start_all(self):
            self.workers["ui"].exit_handler(9)

        def shutdown(self):
            self.shutdown_called = True

    class FakeFlows:
        def __init__(self, **_workers):
            pass

        def start(self):
            pass

        def start_hotkeys(self):
            pass

    monkeypatch.setattr(supervisor_app.single_instance, "acquire", lambda: True)
    monkeypatch.setattr(supervisor_app, "WispSupervisor", FakeSupervisor)
    monkeypatch.setattr(supervisor_app, "FlowController", FakeFlows)

    assert supervisor_app.main() == 0
    assert instances and instances[-1].shutdown_called is True
    crash_logs = list((tmp_path / "build_logs").glob("wisp_crash_*/supervisor-crash.log"))
    assert len(crash_logs) == 1
    report = crash_logs[0].read_text(encoding="utf-8")
    assert "UI worker exited with code 9" in report
    assert "ui recent diagnostic line" in report


def test_persistence_corruption_migration_and_reset_scope_workflow(
    tmp_path: Path,
    isolated_app_state: IsolatedAppState,
    monkeypatch: pytest.MonkeyPatch,
):
    """Corrupt/legacy user data recovers while resets remove only intended app state."""
    from core.conversation_store import store as conversations
    import config
    from core.system.env_utils import read_env_file

    conversations.PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    conversations.PROJECTS_FILE.write_text("{not valid json", encoding="utf-8")
    conversations.CONVERSATIONS_FILE.write_text("{not valid json", encoding="utf-8")

    projects = conversations.load_projects()
    assert projects[0]["id"] == conversations.GENERAL_PROJECT_ID
    assert conversations.load_conversations() == []

    legacy = {
        "id": "legacy-chat",
        "project_id": "missing-project",
        "title": "Legacy",
        "messages": [
            {
                "role": "user",
                "content": "hello",
                "image_base64": "legacy-inline-image",
                "attachments": [{"path": ""}, {"path": str(tmp_path / "note.txt"), "kind": "text"}],
            }
        ],
    }
    conversations.CONVERSATIONS_FILE.write_text(json.dumps([legacy]), encoding="utf-8")
    loaded = conversations.load_conversations()
    assert loaded[0]["project_id"] == "missing-project"
    assert "image_base64" not in loaded[0]["messages"][0]
    assert loaded[0]["messages"][0]["attachments"][0]["kind"] == "text"

    _ensure_brain_path()
    from wisp_brain import handlers
    from core import secret_store
    from core.auth import chatgpt, copilot_auth, github

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890\n"
        "LLM_PROVIDER=chatgpt\n"
        "THEME_MODE=dark\n",
        encoding="utf-8",
    )
    original_env_file = config._ENV_FILE
    original_loaded = set(config._LOADED_DOTENV_KEYS)
    try:
        monkeypatch.setattr(config, "_ENV_FILE", env_file)
        monkeypatch.setattr(config, "_LOADED_DOTENV_KEYS", set())
        monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",), raising=False)
        monkeypatch.setattr(secret_store, "delete_secret", lambda _name: None)
        monkeypatch.setattr(chatgpt, "clear_tokens", lambda: None)
        monkeypatch.setattr(github, "clear_tokens", lambda: None)
        monkeypatch.setattr(copilot_auth, "clear_token", lambda: None)
        config.reload()

        result = handlers.brain_settings_reset_credentials()
        assert result["ok"] is True
        env_values = read_env_file(env_file)
        assert "OPENAI_API_KEY" not in env_values
        assert env_values["LLM_PROVIDER"] == "chatgpt"
        assert env_values["THEME_MODE"] == "dark"
        assert _is_relative_to(isolated_app_state.chats, isolated_app_state.root)
    finally:
        config._ENV_FILE = original_env_file
        config._LOADED_DOTENV_KEYS = original_loaded
        config.reload()


def test_ui_accessibility_layout_and_model_popup_workflow(qapp, monkeypatch: pytest.MonkeyPatch):
    """Settings and auto-agent expose usable labels, focusable controls, and opaque popups."""
    from PySide6.QtWidgets import QComboBox, QLineEdit, QPushButton

    import config
    from ui.agent.task_window import AgentTaskDialog
    from ui.settings_panel.dialog import SettingsDialog, _PROVIDER_LABELS, _PROVIDER_MODELS
    from ui.shared.theme import apply_app_theme

    monkeypatch.setattr(config, "LLM_PROVIDER", "chatgpt", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-5.5", raising=False)
    monkeypatch.setattr(config, "THEME_MODE", "dark", raising=False)

    apply_app_theme(qapp)
    assert "QComboBox QAbstractItemView" in qapp.styleSheet()
    assert "background:" in qapp.styleSheet().split("QComboBox QAbstractItemView", 1)[1]

    settings = SettingsDialog()
    agent = AgentTaskDialog()
    try:
        assert settings.findChild(QLineEdit, "settingsSearch") is not None
        assert settings.findChild(QPushButton, "settingsApplyButton") is not None
        assert settings.findChild(QPushButton, "settingsConfirmButton") is not None
        assert "BUBBLE_FONT_SIZE" in settings._fields
        assert "CHAT_ELABORATE_PROMPT" in settings._fields

        provider_labels = {agent.provider_combo.itemText(i) for i in range(agent.provider_combo.count())}
        assert _PROVIDER_LABELS["chatgpt"] in provider_labels
        assert _PROVIDER_LABELS["openai"] in provider_labels

        chatgpt_models = _PROVIDER_MODELS["chatgpt"]
        agent_models = {agent.model_edit.itemText(i) for i in range(agent.model_edit.count())}
        assert set(chatgpt_models[: min(3, len(chatgpt_models))]) <= agent_models
        assert agent.model_edit.lineEdit().placeholderText()

        focusable = [
            widget
            for widget in [settings.findChild(QLineEdit, "settingsSearch"), agent.title_edit, agent.objective_edit]
            if widget is not None
        ]
        assert all(widget.focusPolicy() for widget in focusable)
        assert any(combo.count() > 1 for combo in agent.findChildren(QComboBox))
    finally:
        settings.deleteLater()
        agent.deleteLater()


def test_agent_permission_notice_and_bubble_notice_workflow(qapp, tmp_path: Path):
    """Approval and notice plumbing makes tool/file permission states visible."""
    from PySide6.QtCore import QEventLoop, QTimer

    from core.agent.task_spec import agent_task_spec_from_dict
    from ui.agent.task_window import AgentRunWindow
    from ui.bubble import SpeechBubble

    notices: list[tuple[str, bool]] = []
    spec = agent_task_spec_from_dict(_minimal_agent_spec(tmp_path))
    window = AgentRunWindow(spec, approval_notice_callback=lambda text, done: notices.append((text, done)))
    bubble = SpeechBubble()
    try:
        window.show()
        qapp.processEvents()
        approval_state = {"event": threading.Event(), "approved": False}
        window._show_approval({"action": "write file", "details": {"path": "README.md"}}, approval_state)
        qapp.processEvents()
        assert window.approval_panel.isVisible()
        assert "Permission needed" in window.approval_label.text()
        assert notices[-1][0].startswith("Permission needed: write file")
        assert notices[-1][1] is False

        window._finish_approval(False)
        assert approval_state["event"].wait(0.1)
        assert approval_state["approved"] is False
        assert notices[-1] == ("Permission declined.", True)

        bubble.show_notice("Browser permission denied.", timeout_ms=20)
        assert "Browser permission denied." in bubble._full_text
        loop = QEventLoop()
        QTimer.singleShot(40, loop.quit)
        loop.exec()
        assert not bubble.isVisible()
    finally:
        window.deleteLater()
        bubble.deleteLater()


def test_real_gpt55_entrypoint_stays_opt_in_by_default():
    """The live-provider workflow test is present but cannot spend tokens accidentally."""
    import importlib.util

    real_test = Path(__file__).with_name("test_real_gpt55_integration.py")
    spec = importlib.util.spec_from_file_location("_workflow_real_gpt55_contract", real_test)
    assert spec is not None and spec.loader is not None
    real_gpt55 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_gpt55)

    assert real_gpt55._DEFAULT_MODEL == "gpt-5.5"
    assert real_gpt55._RUN_ENV == "WISP_RUN_REAL_GPT55_TESTS"
    assert any(mark.name == "real_gpt55" for mark in real_gpt55.pytestmark)
    assert any(mark.name == "workflow" for mark in real_gpt55.pytestmark)
