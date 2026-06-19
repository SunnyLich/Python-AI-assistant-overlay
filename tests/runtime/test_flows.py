"""Tests for macos py test flows."""

from __future__ import annotations

import base64
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import config
from runtime.supervisor.flows import FlowController


class FakeWorker:
    """Test case for fake worker behavior."""
    def __init__(
        self,
        handlers: dict[str, Any] | None = None,
        stream_handlers: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the fake worker instance."""
        self.handlers = handlers or {}
        self.stream_handlers = stream_handlers or {}
        self.calls: list[dict[str, Any]] = []
        self.events: dict[str, list[Any]] = {}

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        wait: bool = True,
    ) -> Any:
        """Verify call behavior."""
        payload = params or {}
        self.calls.append({"method": method, "params": payload, "timeout": timeout, "wait": wait})
        handler = self.handlers.get(method)
        if handler is None:
            return {}
        return handler(payload)

    def call_with_events(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        on_event,
        on_started=None,
    ) -> Any:
        """Verify call with events behavior."""
        payload = params or {}
        request_id = len(self.calls) + 1
        self.calls.append(
            {"method": method, "params": payload, "timeout": timeout, "wait": True, "stream": True}
        )
        if on_started is not None:
            on_started(request_id)
        handler = self.stream_handlers.get(method)
        if handler is not None:
            return handler(payload, on_event)
        return self.handlers.get(method, lambda _params: {})(payload)

    def on_event(self, event: str, handler) -> None:
        """Verify on event behavior."""
        self.events.setdefault(event, []).append(handler)

    def emit(self, event: str, data: Any = None) -> None:
        """Verify emit behavior."""
        for handler in list(self.events.get(event, [])):
            handler(data or {}, None)

    def calls_for(self, method: str) -> list[dict[str, Any]]:
        """Verify calls for behavior."""
        return [call for call in self.calls if call["method"] == method]

    def last_call(self, method: str) -> dict[str, Any]:
        """Verify last call behavior."""
        calls = self.calls_for(method)
        assert calls, f"expected call {method!r}"
        return calls[-1]


@contextmanager
def caller_config(rows: list[dict[str, Any]]):
    """Verify caller config behavior."""
    old_rows = list(config.CALLER_ROWS)
    old_tts = getattr(config, "TTS_PROVIDER", "none")
    config.CALLER_ROWS[:] = rows
    config.TTS_PROVIDER = "none"
    try:
        yield
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.TTS_PROVIDER = old_tts


@contextmanager
def voice_config(row: dict[str, Any]):
    """Verify voice config behavior."""
    old_row = dict(getattr(config, "VOICE_CALLER", {}))
    old_tts = getattr(config, "TTS_PROVIDER", "none")
    config.VOICE_CALLER.clear()
    config.VOICE_CALLER.update(row)
    config.TTS_PROVIDER = "none"
    try:
        yield
    finally:
        config.VOICE_CALLER.clear()
        config.VOICE_CALLER.update(old_row)
        config.TTS_PROVIDER = old_tts


def make_flow(
    *,
    native: FakeWorker | None = None,
    ui: FakeWorker | None = None,
    brain: FakeWorker | None = None,
    audio: FakeWorker | None = None,
) -> tuple[FlowController, FakeWorker, FakeWorker, FakeWorker, FakeWorker]:
    """Verify make flow behavior."""
    native = native or FakeWorker()
    ui = ui or FakeWorker()
    brain = brain or FakeWorker()
    audio = audio or FakeWorker()
    flow = FlowController(native=native, ui=ui, brain=brain, audio=audio, run_async=False)
    flow.start()
    return flow, native, ui, brain, audio


def context_handler(selected: str = "selected", clipboard: str = "", pid: int = 42, focus_token: int = 0):
    """Verify context handler behavior."""
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        """Verify handler behavior."""
        result = {
            "selected_text": selected,
            "clipboard_text": clipboard,
            "active_app": {"name": "Notes", "pid": pid, "bundle_id": "com.apple.Notes"},
        }
        # Mirror the native worker: a paste-back caller asks to capture the
        # focused element and gets a token back for AX in-place write.
        if params.get("capture_focus"):
            result["focus_token"] = focus_token
        return result

    return handler


def browser_context_handler(selected: str = "selected"):
    """Verify browser context handler behavior."""
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        """Verify handler behavior."""
        result = {
            "selected_text": selected,
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        # The page fetch is deferred off the picker path: begin_caller's snapshot
        # asks without it, and the query-time fetch asks with it.
        if params.get("include_browser_content"):
            result["browser_url"] = "https://example.test/page"
            result["browser_content"] = "Example page text"
        return result

    return handler


def query_stream(reply: str = "reply"):
    """Verify query stream behavior."""
    def handler(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Verify handler behavior."""
        on_event("reply.chunk", {"text": reply[:2]}, 1)
        on_event("reply.chunk", {"text": reply[2:]}, 1)
        on_event("reply.done", {"text": reply}, 1)
        return {"text": reply}

    return handler


def test_caller_hotkey_collects_context_and_shows_intent():
    """Verify caller hotkey collects context and shows intent behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": True,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler()})
    with caller_config(rows):
        _flow, native, ui, _brain, audio = make_flow(native=native)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})

    assert ui.calls_for("ui.show_overlay")
    assert ui.last_call("ui.prewarm_intent")["wait"] is False
    assert audio.last_call("audio.prewarm")["wait"] is False
    assert native.last_call("native.context.snapshot")["params"]["include_selection"] is True
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0
    assert not ui.calls_for("ui.reply.listening")


def test_intent_context_uses_unknown_tokens_for_deferred_sources():
    """Verify deferred context chips do not show fake-small token estimates."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_memory_mode": "auto",
            "context_screenshot": "model",
            "context_clipboard": True,
            "file_access": "ask",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected", clipboard="clip text")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native)
        _flow.begin_caller(0)

    chips = {
        item["id"]: item
        for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
    }

    assert chips["ambient"]["tokens"] == "? tok"
    assert "not known yet" in chips["ambient"]["warning"]
    assert chips["selection"]["tokens"] != "? tok"
    assert chips["clipboard"]["tokens"] != "? tok"
    assert chips["screenshot"]["tokens"] == "? tok"
    assert chips["memory"]["tokens"] == "? tok"
    assert chips["files"]["tokens"] == "? tok"


def test_begin_caller_reloads_supervisor_config_when_env_changed(monkeypatch):
    """Verify begin caller reloads supervisor config when env changed behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "intents": [{"key": "w", "label": "Ask", "prompt": "Ask"}],
        }
    ]
    updated_rows = [{**rows[0], "context_tools": True, "context_screenshot": "model"}]
    mtimes = iter([1.0, 2.0])
    reload_calls: list[str] = []
    monkeypatch.setattr(FlowController, "_current_config_mtime", staticmethod(lambda: next(mtimes)))

    def reload_config() -> None:
        """Verify reload config behavior."""
        reload_calls.append("reload")
        config.CALLER_ROWS[:] = updated_rows

    monkeypatch.setattr(config, "reload", reload_config)

    with caller_config(rows):
        flow, _native, ui, _brain, _audio = make_flow(
            native=FakeWorker({"native.context.snapshot": context_handler(selected="")})
        )
        flow.begin_caller(0)

    assert reload_calls == ["reload"]
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0
    assert flow._pending is not None
    assert flow._pending.caller["context_tools"] is True
    assert flow._pending.caller["context_screenshot"] == "model"


def test_bubble_speed_event_forwards_to_audio_worker():
    """Verify bubble speed event forwards to audio worker behavior."""
    _flow, _native, ui, _brain, audio = make_flow()

    ui.emit("ui.bubble.speed", {"enabled": True})

    assert audio.last_call("audio.speed_boost")["params"] == {"enabled": True}


def test_query_flow_streams_reply_and_adds_chat_conversation_with_context():
    """Verify query flow streams reply and adds chat conversation with context behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": True,
            "context_screenshot": "off",
            "context_clipboard": True,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(clipboard="clip text")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("hello")})
    with caller_config(rows):
        flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        flow._context_buffer.append("buffered text")
        ui.emit("ui.context.dropped", {"items": [{"name": "notes.txt", "content": "drop text", "type": "text"}]})
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    query = brain.last_call("brain.query")["params"]
    assert "context_policy" not in query
    assert query["intent_prompt"] == "Explain this"
    assert query["selected"] == "selected"
    assert query["use_tools"] is True
    assert "web_search" in query["allowed_tools"]
    assert "github_repo" in query["allowed_tools"]
    assert "Active app: Notes" in query["ambient_text"]
    assert "Clipboard:" in query["ambient_text"]
    assert "Buffered context:" in query["ambient_text"]
    assert "Dropped context:" in query["ambient_text"]
    assert [c["params"]["text"] for c in ui.calls_for("ui.reply.chunk")] == ["he", "llo"]
    assert ui.calls_for("ui.reply.done")
    chat_params = ui.last_call("ui.chat.add_conversation")["params"]
    assert chat_params["assistant"] == "hello"
    assert chat_params["context_policy"]["context_clipboard"] is True
    assert chat_params["context_policy"]["context_documents_mode"] == "auto"
    assert ui.calls_for("ui.context.summary")
    assert ui.calls_for("ui.context.clear")


def test_query_bubble_splits_exposed_thought_segments():
    """Verify speech bubble receives exposed model thought segments separately."""
    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        reply = "<thought>checking files</thought>Created it."
        on_event("reply.chunk", {"text": reply[:13]}, 1)
        on_event("reply.chunk", {"text": reply[13:]}, 1)
        on_event("reply.done", {"text": reply}, 1)
        return {"text": reply}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": stream})
    with caller_config([{"paste_back": False, "context_ambient": True}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "create a file"})

    chunks = [call["params"] for call in ui.calls_for("ui.reply.chunk")]
    assert "".join(chunk["text"] for chunk in chunks if chunk["is_thought"]) == "checking files"
    assert "".join(chunk["text"] for chunk in chunks if not chunk["is_thought"]) == "Created it."


def test_query_bubble_replaces_partial_stream_with_final_text():
    """Verify final query text replaces incomplete streamed bubble text."""
    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit an incomplete stream and a complete final answer."""
        on_event("reply.chunk", {"text": "draft only"}, 1)
        on_event("reply.done", {"text": "final answer"}, 1)
        return {"text": "final answer"}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": stream})
    with caller_config([{"paste_back": False, "context_ambient": True}]):
        flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "answer"})

    assert len(ui.calls_for("ui.reply.reset")) >= 2
    assert ui.calls_for("ui.reply.chunk")[-1]["params"]["text"] == "final answer"
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == "final answer"


def test_add_context_shows_panel_badge_not_bubble():
    """Verify add context shows panel badge not bubble behavior."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="hello world selection")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native)
        flow.add_context()
    add_calls = ui.calls_for("ui.context.add_item")
    assert add_calls, "added context should surface as a right-of-icon badge"
    assert add_calls[-1]["params"]["item_type"] == "text"
    assert not ui.calls_for("ui.reply.notice"), "added context must not go to the bubble"
    assert len(flow._drop_context_items) == 1
    assert flow._drop_context_items[0]["content"] == "hello world selection"


def test_add_context_without_text_falls_back_to_notice():
    """Verify add context without text falls back to notice behavior."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", clipboard="")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native)
        flow.add_context()
    assert not ui.calls_for("ui.context.add_item")
    assert ui.last_call("ui.reply.notice")["params"]["text"].startswith("No selected")


def test_clear_context_empties_panel_without_bubble():
    """Verify clear context empties panel without bubble behavior."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="some text")})
    with caller_config([{}]):
        flow, native, ui, brain, _audio = make_flow(native=native)
        flow.add_context()
        flow.clear_context()
    assert ui.calls_for("ui.context.clear"), "clear should empty the panel"
    assert not ui.calls_for("ui.reply.notice"), "clear must not go to the bubble"
    assert flow._drop_context_items == []


def test_context_modes_map_to_auto_documents_and_allowed_tools():
    """Verify context modes map to auto documents and allowed tools behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "off",
            "context_github_mode": "model",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler()})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Use context"})

    query = brain.last_call("brain.query")["params"]
    assert query["include_active_document"] is False
    assert query["use_tools"] is True
    # memory defaults to "auto", which now also offers the memory_save write tool.
    assert query["allowed_tools"] == ["get_context.documents", "git_status", "git_diff", "github_repo", "github_issue", "memory_save"]
    assert query["pinned_tools"] == ["get_context", "git_status", "git_diff", "github_repo", "github_issue"]
    assert query["frontload_tools"] == []


def test_context_modes_map_on_browser_and_git_to_frontloaded_context():
    """Verify context modes map on browser and git to frontloaded context behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "auto",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": browser_context_handler()})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, _native, _ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        _ui.emit("ui.intent.chosen", {"custom": "Use context"})

    query = brain.last_call("brain.query")["params"]
    # memory defaults to "auto" -> memory_save offered, so tools are active even
    # though browser/git context is frontloaded rather than offered as tools.
    assert query["use_tools"] is True
    assert query["allowed_tools"] == ["memory_save"]
    assert query["frontload_tools"] == ["git_status", "git_diff"]
    assert "[Browser/Web]" in query["ambient_text"]
    assert "https://example.test/page" in query["ambient_text"]
    assert "Example page text" in query["ambient_text"]


def test_browser_url_captured_at_hotkey_time_fetches_content_by_handle():
    """Verify browser url captured at hotkey time fetches content by handle behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        """Verify snapshot handler behavior."""
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        # Mirrors the native worker: URL + window handle are grabbed at hotkey
        # time while the browser is still foreground; the page text is not.
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Window text via hwnd {params.get('hwnd')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.calls_for("ui.intent.context_items")[0]["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert browser_chip["state"] == "on"
        assert browser_chip["tokens"] == "? tok"
        assert "not known yet" in browser_chip["warning"]
        updated_browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert updated_browser_chip["state"] == "on"
        assert updated_browser_chip["tokens"].startswith("~")
        assert updated_browser_chip["warning"] == ""
        ui.emit("ui.intent.chosen", {"custom": "What is this page?"})

    fetch = native.last_call("native.context.browser_content")["params"]
    assert fetch == {"url": "https://example.test/page", "hwnd": 777, "app": ""}
    assert len(native.calls_for("native.context.browser_content")) == 1
    # No focus-race re-detection: the snapshot ran exactly once, at hotkey time.
    assert len(native.calls_for("native.context.snapshot")) == 1
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/page" in ambient
    assert "Window text via hwnd 777" in ambient


def test_browser_hwnd_without_url_fetches_content_by_handle():
    """Verify browser hwnd without url fetches content by handle behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Calc", "pid": 42, "window_id": 111, "bundle_id": ""},
                "browser_url": "",
                "browser_hwnd": 777 if params.get("include_browser_url") else 0,
            },
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Rendered browser text {params.get('hwnd')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.calls_for("ui.intent.context_items")[0]["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert browser_chip["state"] == "on"
        assert browser_chip["tokens"] == "? tok"
        assert "not known yet" in browser_chip["warning"]
        updated_browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert updated_browser_chip["state"] == "on"
        assert updated_browser_chip["tokens"].startswith("~")
        assert updated_browser_chip["warning"] == ""
        ui.emit("ui.intent.chosen", {"custom": "What is the page?"})

    assert native.last_call("native.context.browser_content")["params"] == {"url": "", "hwnd": 777, "app": ""}
    assert len(native.calls_for("native.context.browser_content")) == 1
    assert "Rendered browser text 777" in brain.last_call("brain.query")["params"]["ambient_text"]


def test_browser_app_captured_at_hotkey_time_fetches_text_via_applescript():
    """macOS path: the browser app name + URL are grabbed at hotkey time, then
    the page text is read by app (AppleScript) â€” no read-by-handle on macOS."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        """Verify snapshot handler behavior."""
        result = {
            "platform": "darwin",
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Safari", "pid": 42, "bundle_id": "com.apple.Safari"},
        }
        # Mirrors the macOS worker: URL + browser app are grabbed at hotkey time;
        # the page text is deferred (no window handle on macOS).
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_app"] = "Safari"
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Page text via {params.get('app')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "What is this page?"})

    fetch = native.last_call("native.context.browser_content")["params"]
    assert fetch == {"url": "https://example.test/page", "hwnd": 0, "app": "Safari"}
    # No focus-race re-detection: the snapshot ran exactly once, at hotkey time.
    assert len(native.calls_for("native.context.snapshot")) == 1
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/page" in ambient
    assert "Page text via Safari" in ambient


def test_intent_enabled_browser_fetches_from_hotkey_time_target_when_setting_off():
    """Verify a per-prompt Browser/Web toggle can use the original foreground tab."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "window_id": 777, "bundle_id": ""},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/off-by-default"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda params: {
                "url": params.get("url"),
                "content": f"Deferred page text {params.get('hwnd')}",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        browser_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "browser"
        )
        assert browser_chip["state"] == "off"
        assert browser_chip["tokens"] == "? tok"
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use the page",
                "context_choices": [{"id": "browser", "state": "on"}],
            },
        )

    snapshot_params = native.calls_for("native.context.snapshot")[0]["params"]
    assert snapshot_params["include_browser_url"] is True
    assert snapshot_params["include_browser_content"] is False
    assert len(native.calls_for("native.context.snapshot")) == 1
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "https://example.test/off-by-default",
        "hwnd": 777,
        "app": "",
    }
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "https://example.test/off-by-default" in ambient
    assert "Deferred page text 777" in ambient


def test_intent_enabled_clipboard_uses_hotkey_time_clipboard_when_setting_off():
    """Verify Clipboard can be enabled per prompt even when disabled in settings."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "selected_text": "",
            "clipboard_text": "clipboard from hotkey time" if params.get("include_clipboard") else "",
            "active_app": {"name": "Notes", "pid": 42, "bundle_id": "com.apple.Notes"},
        }

    native = FakeWorker({"native.context.snapshot": snapshot_handler})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("ok")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        clipboard_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "clipboard"
        )
        assert clipboard_chip["state"] == "off"
        assert clipboard_chip["tokens"].startswith("~")
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use clipboard",
                "context_choices": [{"id": "clipboard", "state": "on"}],
            },
        )

    assert native.calls_for("native.context.snapshot")[0]["params"]["include_clipboard"] is True
    ambient = brain.last_call("brain.query")["params"]["ambient_text"]
    assert "Clipboard:" in ambient
    assert "clipboard from hotkey time" in ambient


def test_intent_enabled_app_fetches_active_document_when_setting_off():
    """Verify the App chip can enable document context for one prompt."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        app_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "ambient"
        )
        assert app_chip["state"] == "off"
        ui.emit(
            "ui.intent.chosen",
            {
                "custom": "Use open document",
                "context_choices": [
                    {"id": "ambient", "state": "on", "default_state": "off", "touched": True},
                ],
            },
        )

    query = brain.last_call("brain.query")["params"]
    assert query["active_document_text"] == "DOC TEXT"
    assert query["include_active_document"] is False
    assert {"label": "Active document", "type": "file"} in ui.last_call("ui.context.summary")["params"]["items"]


def test_context_priority_marks_browser_when_browser_was_active():
    """Verify context priority marks browser when browser was active behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        """Verify snapshot handler behavior."""
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda _params: {
                "url": "https://example.test/page",
                "content": "Browser text",
            },
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use both"})

    query = brain.last_call("brain.query")["params"]
    assert query["context_priority"] == "Browser/Web"


def test_context_priority_marks_document_when_browser_was_background_context():
    """Verify context priority marks document when browser was background context behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def snapshot_handler(params: dict[str, Any]) -> dict[str, Any]:
        """Verify snapshot handler behavior."""
        result = {
            "selected_text": "",
            "clipboard_text": "",
            "active_app": {"name": "Notes", "pid": 42, "bundle_id": "com.apple.Notes"},
        }
        if params.get("include_browser_url"):
            result["browser_url"] = "https://example.test/page"
            result["browser_hwnd"] = 777
        return result

    native = FakeWorker(
        {
            "native.context.snapshot": snapshot_handler,
            "native.context.browser_content": lambda _params: {
                "url": "https://example.test/page",
                "content": "Browser text",
            },
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use both"})

    query = brain.last_call("brain.query")["params"]
    assert query["context_priority"] == "Active document"


def test_active_document_auto_fetches_before_query_and_summary():
    """Verify active document auto fetches before query and summary behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "DOC TEXT"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        updated_app_chip = next(
            item
            for item in ui.last_call("ui.intent.context_items")["params"]["context_items"]
            if item["id"] == "ambient"
        )
        assert updated_app_chip["tokens"].startswith("~")
        assert updated_app_chip["warning"] == ""
        ui.emit("ui.intent.chosen", {"custom": "Use open docs"})

    query = brain.last_call("brain.query")["params"]
    assert query["active_document_text"] == "DOC TEXT"
    assert query["include_active_document"] is False
    assert len(brain.calls_for("brain.context.active_document")) == 1
    summary = ui.last_call("ui.context.summary")["params"]["items"]
    assert {"label": "Active document", "type": "file"} in summary


def test_active_document_request_includes_hotkey_time_window():
    """Verify active document request includes hotkey time window behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {
                    "name": "Untitled 1 \u2014 LibreOffice Calc",
                    "pid": 202,
                    "window_id": 222,
                    "bundle_id": "",
                },
                "debug": {
                    "window": {
                        "chosen_process": "soffice.bin",
                        "chosen_title": "Untitled 1 \u2014 LibreOffice Calc",
                        "chosen_pid": 202,
                        "chosen_hwnd": 222,
                    }
                },
            }
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "CALC CELLS"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Read the sheet"})

    params = brain.last_call("brain.context.active_document")["params"]
    assert params["active_window"] == {
        "title": "Untitled 1 \u2014 LibreOffice Calc",
        "process_name": "soffice.bin",
        "pid": 202,
        "window_id": 222,
    }
    assert brain.last_call("brain.query")["params"]["active_document_text"] == "CALC CELLS"


def test_active_document_request_prefers_captured_macos_window_title():
    """Verify active document request prefers captured macos window title behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": lambda _params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {
                    "name": "TextEdit",
                    "pid": 202,
                    "bundle_id": "com.apple.TextEdit",
                },
                "debug": {
                    "window": {
                        "chosen_process": "TextEdit",
                        "chosen_title": "Notes.txt",
                        "chosen_pid": 202,
                    }
                },
            }
        }
    )
    brain = FakeWorker(
        handlers={"brain.context.active_document": lambda _params: {"text": "TXT BODY"}},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Read the txt"})

    params = brain.last_call("brain.context.active_document")["params"]
    assert params["active_window"] == {
        "title": "Notes.txt",
        "process_name": "TextEdit",
        "pid": 202,
        "window_id": 0,
    }
    assert brain.last_call("brain.query")["params"]["active_document_text"] == "TXT BODY"


def test_no_tts_reply_done_lets_wpm_reveal_drain():
    # With TTS off, reply.done must NOT flush the bubble: the WPM reveal keeps
    # pacing the text (flush=False), instead of the full reply slamming in the
    # moment the LLM finishes streaming.
    """Verify no tts reply done lets wpm reveal drain behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("a fairly long reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "tell me"})

    done_calls = ui.calls_for("ui.reply.done")
    assert done_calls
    assert all(call["params"] == {"flush": False} for call in done_calls)


def test_tts_speaks_completed_segments_before_full_reply_done():
    """Verify TTS starts after the first stable segment, before final reply.done."""
    first = "This is the first completed spoken part with enough detail to start audio now."
    second = "This is the second completed spoken part."
    first_synth_started = threading.Event()

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit one completed segment, wait for TTS, then finish."""
        on_event("reply.chunk", {"text": first + " "}, 1)
        assert first_synth_started.wait(2.0), "first segment should synthesize before reply.done"
        on_event("reply.chunk", {"text": second}, 1)
        on_event("reply.done", {"text": f"{first} {second}"}, 1)
        return {"text": f"{first} {second}"}

    def wait_for_audio_calls(audio: FakeWorker, count: int) -> None:
        """Wait until the background TTS queue drains."""
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(audio.calls_for("audio.play_file")) >= count:
                return
            time.sleep(0.01)
        raise AssertionError(f"expected {count} audio.play_file calls")

    def synth(params: dict[str, Any]) -> dict[str, Any]:
        """Return a fake WAV path for a TTS segment."""
        if params["text"] == first:
            first_synth_started.set()
        return {"path": f"{params['text'][:8]}.wav", "word_timestamps": {"words": [], "start_ms": []}}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": synth,
            "audio.play_file": lambda _params: {"played": True, "stopped": False},
        }
    )
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    with caller_config(rows):
        config.TTS_PROVIDER = "cartesia"
        _flow, _native, ui, _brain, audio = make_flow(native=native, brain=brain, audio=audio)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "tell me"})
        wait_for_audio_calls(audio, 2)

    synth_texts = [call["params"]["text"] for call in audio.calls_for("audio.tts.synthesize")]
    assert synth_texts == [first, second]
    assert len(audio.calls_for("audio.play_file")) == 2
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == f"{first} {second}"


def test_tts_speaks_short_progress_before_final_answer():
    """Verify short progress narration speaks during tool waits."""
    progress = "Checking the file first."
    final = "Done."
    progress_synth_started = threading.Event()

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit short progress, wait for TTS, then answer."""
        on_event("reply.chunk", {"text": progress, "is_progress": True}, 1)
        assert progress_synth_started.wait(2.0), "progress should synthesize before final answer"
        on_event("reply.chunk", {"text": final}, 1)
        on_event("reply.done", {"text": final}, 1)
        return {"text": final}

    def synth(params: dict[str, Any]) -> dict[str, Any]:
        """Return a fake WAV path for a TTS segment."""
        if params["text"] == progress:
            progress_synth_started.set()
        return {"path": f"{params['text'][:8]}.wav", "word_timestamps": {"words": [], "start_ms": []}}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    audio = FakeWorker(
        handlers={
            "audio.tts.synthesize": synth,
            "audio.play_file": lambda _params: {"played": True, "stopped": False},
        }
    )
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    with caller_config(rows):
        config.TTS_PROVIDER = "cartesia"
        _flow, _native, ui, _brain, audio = make_flow(native=native, brain=brain, audio=audio)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "edit"})
        deadline = time.monotonic() + 2.0
        while len(audio.calls_for("audio.play_file")) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

    synth_texts = [call["params"]["text"] for call in audio.calls_for("audio.tts.synthesize")]
    assert synth_texts == [progress, final]
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == final


def test_thought_chunks_show_without_becoming_final_answer():
    """Verify structured thought chunks render early but do not pollute final text."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit thought text before the visible answer."""
        on_event("reply.chunk", {"text": "Thinking first.", "is_thought": True}, 1)
        on_event("reply.chunk", {"text": "Answer."}, 1)
        on_event("reply.done", {"text": "Answer."}, 1)
        return {"text": "Answer."}

    brain = FakeWorker(stream_handlers={"brain.query": stream})
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Explain"})

    chunks = [call["params"] for call in ui.calls_for("ui.reply.chunk")]
    assert {"text": "Thinking first.", "is_thought": True, "is_progress": False} in chunks
    assert {"text": "Answer.", "is_thought": False, "is_progress": False} in chunks
    assert len(ui.calls_for("ui.reply.reset")) == 1
    assert ui.last_call("ui.chat.add_conversation")["params"]["assistant"] == "Answer."


def test_model_screenshot_mode_precaptures_through_native_worker():
    """Verify model screenshot mode precaptures through native worker behavior."""
    image_bytes = b"fake screenshot"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, _ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            _ui.emit("ui.intent.chosen", {"custom": "what can you see?"})
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert native.last_call("native.capture.fullscreen")["timeout"] == 8.0
    assert query["allow_screenshot_tool"] is True
    assert query["screenshot_tool_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_auto_screenshot_mode_captures_even_with_selected_text():
    """Verify auto screenshot mode captures even with selected text behavior."""
    image_bytes = b"selected plus screenshot"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "auto",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="some selected text"),
            "native.capture.fullscreen": lambda _params: {"ok": True, "path": str(image_path)},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("vision reply")})
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "caller", "index": 0})
            ui.emit("ui.intent.chosen", {"custom": "what can you see?"})
    finally:
        image_path.unlink(missing_ok=True)

    query = brain.last_call("brain.query")["params"]
    assert native.last_call("native.capture.fullscreen")["timeout"] == 30.0
    assert query["selected"] == "some selected text"
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_caller_overlay_listens_before_context_and_model_screenshot_capture():
    """Verify caller overlay listens before slow context and screenshot capture."""
    image_bytes = b"target screen"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    order: list[str] = []
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]

    def context(_params: dict[str, Any]) -> dict[str, Any]:
        """Verify context behavior."""
        order.append("context")
        return context_handler(selected="")(_params)

    def capture(_params: dict[str, Any]) -> dict[str, Any]:
        """Verify capture behavior."""
        order.append("capture")
        return {"ok": True, "path": str(image_path)}

    def overlay(_params: dict[str, Any]) -> dict[str, Any]:
        """Verify overlay behavior."""
        order.append("overlay")
        return {}

    native = FakeWorker({"native.context.snapshot": context, "native.capture.fullscreen": capture})
    ui = FakeWorker({"ui.overlay.state": overlay})
    try:
        with caller_config(rows):
            flow, _native, _ui, _brain, _audio = make_flow(native=native, ui=ui)
            flow.begin_caller(0)
    finally:
        image_path.unlink(missing_ok=True)

    assert order == ["overlay", "context", "capture"]
    assert flow._pending is not None
    assert flow._pending.screenshot_tool_b64 == base64.b64encode(image_bytes).decode("ascii")


def test_intent_cancel_stops_context_collection_before_screenshot_capture():
    """Verify Escape cancellation prevents follow-up capture work."""
    order: list[str] = []
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
        }
    ]
    ui = FakeWorker()

    def context(params: dict[str, Any]) -> dict[str, Any]:
        """Cancel while the initial native snapshot is in flight."""
        order.append("context")
        ui.emit("ui.intent.cancelled", {})
        return context_handler(selected="")(params)

    def capture(_params: dict[str, Any]) -> dict[str, Any]:
        """This should not run after cancellation."""
        order.append("capture")
        return {"ok": True, "path": ""}

    native = FakeWorker({"native.context.snapshot": context, "native.capture.fullscreen": capture})
    with caller_config(rows):
        flow, _native, ui, _brain, _audio = make_flow(native=native, ui=ui)
        flow.begin_caller(0)

    assert order == ["context"]
    assert flow._pending is None
    assert not ui.calls_for("ui.intent.context_items")
    assert not ui.calls_for("ui.chat.add_conversation")


def test_intent_cancel_stops_prefetch_before_browser_fetch():
    """Verify Escape cancellation prevents later prefetch stages."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "auto",
            "context_browser_mode": "auto",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    ui = FakeWorker()
    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Browser", "pid": 42, "bundle_id": "com.browser"},
                "browser_url": "https://example.test/page" if params.get("include_browser_url") else "",
                "browser_hwnd": 777 if params.get("include_browser_url") else 0,
            },
            "native.context.browser_content": lambda _params: {"content": "should not fetch"},
        }
    )

    def active_doc(_params: dict[str, Any]) -> dict[str, Any]:
        """Cancel during active-document prefetch."""
        ui.emit("ui.intent.cancelled", {})
        return {"text": "DOC TEXT"}

    brain = FakeWorker(
        handlers={"brain.context.active_document": active_doc},
        stream_handlers={"brain.query": query_stream("ok")},
    )
    with caller_config(rows):
        flow, native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        flow.begin_caller(0)

    assert flow._pending is None
    assert brain.calls_for("brain.context.active_document")
    assert not native.calls_for("native.context.browser_content")
    assert not ui.calls_for("ui.chat.add_conversation")


def test_query_failure_reports_notice_and_returns_idle():
    """Verify query failure reports notice and returns idle behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]

    def fail_query(_params: dict[str, Any], _on_event) -> dict[str, Any]:
        """Verify fail query behavior."""
        raise RuntimeError("ValueError: LLM route uses 'google', but its API key is not configured.")

    native = FakeWorker({"native.context.snapshot": context_handler()})
    brain = FakeWorker(stream_handlers={"brain.query": fail_query})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Explain this"})

    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "LLM request failed: LLM route uses 'google', but its API key is not configured."
    )
    assert ui.calls_for("ui.reply.done")
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


def test_rewrite_flow_pastes_back_to_original_pid():
    """Verify rewrite flow pastes back to original pid behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="bad grammar", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": query_stream("good grammar")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 1})
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    # The paste-back caller asked the native worker to capture the focused element.
    snap = native.last_call("native.context.snapshot")["params"]
    assert snap["capture_focus"] is True
    rewrite = brain.last_call("brain.rewrite")["params"]
    assert rewrite["selected_text"] == "bad grammar"
    paste = native.last_call("native.paste_text")["params"]
    assert paste["text"] == "good grammar"
    assert paste["target_pid"] == 777
    # ...and the captured token is forwarded so paste_text can do the AX write.
    assert paste["focus_token"] == 9
    # Success is silent: the bubble finishes, no status banner is written into it.
    assert ui.calls_for("ui.reply.done"), "bubble should be finished after paste"
    assert not ui.calls_for("ui.reply.notice"), "rewrite status must not go in the bubble"
    assert not native.calls_for("native.notify"), "successful paste should not notify"


def test_paste_back_file_prompt_routes_to_local_file_tools():
    """Verify paste-back callers can edit files instead of clipboard-pasting."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        },
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="selected text", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("edited file")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "Edit local file notes.md to say hi"})

    assert not brain.calls_for("brain.rewrite")
    assert not native.calls_for("native.paste_text")
    params = brain.last_call("brain.query")["params"]
    assert params["file_access_mode"] == "ask"
    assert set(params["allowed_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}
    assert set(params["pinned_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}
    assert ui.calls_for("ui.chat.add_conversation")


def test_rewrite_falls_back_to_clipboard_when_focus_not_confirmed():
    """Verify rewrite falls back to clipboard when focus not confirmed behavior."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="bad grammar", pid=777),
            # Focus didn't land on the target app, but the rewrite is on the clipboard.
            "native.paste_text": lambda _params: {
                "ok": False,
                "clipboard_ok": True,
                "confirmed": False,
                "app_name": "TextEdit",
                "frontmost_pid": 999,
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.rewrite": query_stream("good grammar")})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    # The recovery hint goes to a system notification, never the reply bubble.
    assert not ui.calls_for("ui.reply.notice"), "fallback status must not go in the bubble"
    notify = native.last_call("native.notify")["params"]
    assert "paste" in notify["message"].lower()


def test_rewrite_failure_reports_notice_and_returns_idle():
    """Verify rewrite failure reports notice and returns idle behavior."""
    rows = [
        {
            "paste_back": True,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]

    def fail_rewrite(_params: dict[str, Any], _on_event) -> dict[str, Any]:
        """Verify fail rewrite behavior."""
        raise RuntimeError("ValueError: LLM route uses 'google', but its API key is not configured.")

    native = FakeWorker({"native.context.snapshot": context_handler(selected="bad grammar")})
    brain = FakeWorker(stream_handlers={"brain.rewrite": fail_rewrite})
    with caller_config(rows):
        _flow, _native, ui, _brain, _audio = make_flow(native=native, brain=brain)
        ui.emit("ui.intent.chosen", {"custom": "Fix grammar"})

    assert ui.last_call("ui.reply.notice")["params"]["text"] == (
        "Rewrite failed: LLM route uses 'google', but its API key is not configured."
    )
    assert ui.calls_for("ui.reply.done")
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


def test_snip_region_captures_file_and_queries_with_image():
    """Verify snip region captures file and queries with image behavior."""
    image_bytes = b"not really a png but enough for base64"
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(image_bytes)
        image_path = Path(tmp.name)

    rows = [
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
    try:
        with caller_config(rows):
            _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
            native.emit("native.hotkey", {"kind": "snip"})
            ui.emit("ui.snip.region", {"x": 1, "y": 2, "width": 3, "height": 4})
            ui.emit("ui.intent.chosen", {"custom": "What is in this image?"})
    finally:
        image_path.unlink(missing_ok=True)

    assert ui.calls_for("ui.show_snip")
    assert native.last_call("native.capture.region")["params"]["region"]["width"] == 3
    query = brain.last_call("brain.query")["params"]
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_voice_flow_records_transcribes_and_queries():
    """Verify voice flow records transcribes and queries behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": True,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "voice prompt"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("voice reply")})
    with caller_config(rows):
        _flow, native, ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.start")
    assert audio.calls_for("audio.record.stop_transcribe")
    assert ui.last_call("ui.reply.transcript")["params"]["text"] == "voice prompt"
    assert brain.last_call("brain.query")["params"]["intent_prompt"] == "voice prompt"


def test_voice_start_starts_recording_before_context_capture():
    """Verify voice start starts recording before context capture behavior."""
    audio = FakeWorker()
    native = FakeWorker()

    def snapshot(_params):
        """Verify snapshot behavior."""
        assert audio.calls_for("audio.record.start")
        return context_handler(selected="")(_params)

    native.handlers["native.context.snapshot"] = snapshot

    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)
    native.emit("native.hotkey", {"kind": "voice_start"})

    assert audio.calls_for("audio.record.start")
    assert ui.last_call("ui.reply.listening")["params"] == {}
    assert ui.calls_for("ui.overlay.state")[0]["params"]["state"] == "listening"


def test_voice_start_does_not_show_recording_bubble_when_recording_fails():
    """Verify recording bubble only appears after the recorder actually starts."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})

    def fail_start(_params):
        """Simulate recorder startup failure."""
        raise RuntimeError("mic unavailable")

    audio = FakeWorker({"audio.record.start": fail_start})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")
    assert ui.calls_for("ui.reply.notice")


def test_voice_start_does_not_show_recording_bubble_when_recorder_reports_false():
    """Verify explicit non-recording result stays quiet."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.start": lambda _params: {"recording": False}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")


def test_voice_start_key_repeat_is_ignored_until_release():
    """Verify voice start key repeat is ignored until release behavior."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": ""}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_stop"})

    assert len(audio.calls_for("audio.record.start")) == 1
    assert len(ui.calls_for("ui.reply.listening")) == 1
    assert len(audio.calls_for("audio.record.stop_transcribe")) == 1


def test_voice_stop_leaves_recording_bubble_before_transcribing():
    """Verify voice stop leaves recording bubble before transcribing behavior."""
    ui = FakeWorker()

    def transcribe(_params):
        """Verify transcribe behavior."""
        assert ui.last_call("ui.overlay.state")["params"]["state"] == "thinking"
        assert ui.calls_for("ui.reply.thinking")
        return {"text": ""}

    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    audio = FakeWorker({"audio.record.stop_transcribe": transcribe})
    _flow, native, ui, _brain, audio = make_flow(native=native, ui=ui, audio=audio)

    native.emit("native.hotkey", {"kind": "voice_start"})
    native.emit("native.hotkey", {"kind": "voice_stop"})

    assert audio.calls_for("audio.record.stop_transcribe")
    assert ui.calls_for("ui.reply.reset")
    assert ui.last_call("ui.overlay.state")["params"]["state"] == "idle"


def test_voice_flow_uses_voice_caller_config():
    """Verify voice flow uses voice caller config behavior."""
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
        _flow, native, _ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    query = brain.last_call("brain.query")["params"]
    assert query["use_tools"] is True
    assert set(query["allowed_tools"]) == {"web_search", "get_context.browser", "alpha", "beta"}
    assert query["pinned_tools"] == ["web_search", "get_context", "alpha"]
    assert query["memory_enabled"] is False
    # The record-start path must not wait on the slow browser page fetch.
    snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    assert snapshot["include_browser_content"] is False


def test_voice_screenshot_auto_captures_at_voice_start(tmp_path):
    """Verify voice screenshot auto captures at voice start behavior."""
    image_bytes = b"voice-shot"
    image_path = tmp_path / "voice.png"
    image_path.write_bytes(image_bytes)
    voice_row = {
        "label": "Voice",
        "paste_back": False,
        "context_ambient": True,
        "context_clipboard": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "auto",
        "tools": {},
    }
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected=""),
            "native.capture.fullscreen": lambda _params: {"path": str(image_path)},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "what is on screen"}})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("screen reply")})
    with voice_config(voice_row):
        _flow, native, _ui, brain, audio = make_flow(native=native, audio=audio, brain=brain)
        native.emit("native.hotkey", {"kind": "voice_start"})
        native.emit("native.hotkey", {"kind": "voice_stop"})

    assert native.calls_for("native.capture.fullscreen")
    query = brain.last_call("brain.query")["params"]
    assert query["screenshot_b64"] == base64.b64encode(image_bytes).decode("ascii")


def test_dictation_shows_recording_ui_after_recording_starts():
    """Verify dictation shows recording UI only after the recorder starts."""
    native = FakeWorker(
        {
            "native.context.snapshot": context_handler(selected="", pid=777, focus_token=9),
            "native.paste_text": lambda _params: {"ok": True},
        }
    )
    audio = FakeWorker({"audio.record.stop_transcribe": lambda _params: {"text": "hello there"}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})
    native.emit("native.hotkey", {"kind": "dictate_stop"})

    snapshot = native.calls_for("native.context.snapshot")[0]["params"]
    paste = native.last_call("native.paste_text")["params"]
    assert snapshot["capture_focus"] is True
    assert paste["text"] == "hello there"
    assert paste["target_pid"] == 777
    assert paste["focus_token"] == 9
    assert audio.calls_for("audio.record.start")
    assert audio.calls_for("audio.record.stop_transcribe")
    assert ui.calls_for("ui.overlay.state")[0]["params"]["state"] == "listening"
    assert ui.calls_for("ui.reply.listening")
    assert not ui.calls_for("ui.reply.reset")


def test_dictation_does_not_show_recording_ui_when_recording_fails():
    """Verify failed dictation start does not claim recording."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", pid=777, focus_token=9)})

    def fail_start(_params):
        """Simulate recorder startup failure."""
        raise RuntimeError("mic unavailable")

    audio = FakeWorker({"audio.record.start": fail_start})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")
    assert not any(call["params"].get("state") == "listening" for call in ui.calls_for("ui.overlay.state"))
    assert ui.calls_for("ui.reply.notice")


def test_dictation_does_not_show_recording_ui_when_recorder_reports_false():
    """Verify explicit dictation non-recording result stays quiet."""
    native = FakeWorker({"native.context.snapshot": context_handler(selected="", pid=777, focus_token=9)})
    audio = FakeWorker({"audio.record.start": lambda _params: {"recording": False}})
    _flow, native, ui, _brain, audio = make_flow(native=native, audio=audio)

    native.emit("native.hotkey", {"kind": "dictate_start"})

    assert audio.calls_for("audio.record.start")
    assert not ui.calls_for("ui.reply.listening")
    assert not any(call["params"].get("state") == "listening" for call in ui.calls_for("ui.overlay.state"))


def test_caller_tool_overrides_reach_brain_query():
    """Verify caller tool overrides reach brain query behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "tools": {"my_tool": "on", "other_tool": "model"},
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="picked")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("tool reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        native.emit("native.hotkey", {"kind": "caller", "index": 0})
        ui.emit("ui.intent.chosen", {"custom": "run it"})

    query = brain.last_call("brain.query")["params"]
    assert set(query["allowed_tools"]) == {"my_tool", "other_tool"}
    assert query["pinned_tools"] == ["my_tool"]
    assert query["use_tools"] is True


def test_off_tool_override_beats_context_dropdown():
    """Verify off tool override beats context dropdown behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_documents_mode": "model",
            "context_browser_mode": "model",
            "context_github_mode": "auto",
            "context_memory_mode": "off",
            "context_tools": True,
            "context_screenshot": "model",
            "context_clipboard": False,
            # web_search and get_context forced off despite browser/docs
            # granting them; git_status forced off drops it from the
            # frontload list; capture_screen off kills the screenshot tool.
            "tools": {
                "web_search": "off",
                "get_context": "off",
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
    # capture_screen off also means no pre-captured screenshot for the tool.
    assert not native.calls_for("native.capture.fullscreen")


def test_chat_request_streams_through_brain_chat():
    """Verify chat request streams through brain chat behavior."""
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    _flow, native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "hi"}]})

    assert brain.last_call("brain.chat")["params"]["messages"][0]["content"] == "hi"
    assert [c["params"]["text"] for c in ui.calls_for("ui.chat.chunk")] == ["ch", "at reply"]
    done_params = ui.last_call("ui.chat.done")["params"]
    assert done_params["request_id"] == "chat-1"
    assert done_params["text"] == "chat reply"


def test_chat_request_forwards_file_context_metadata():
    """Verify chat request forwards display-hidden file metadata to the UI."""
    file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "ok": True,
        }
    ]

    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit a final reply with local-file metadata."""
        on_event("reply.done", {"text": "done", "file_context": file_context}, 1)
        return {"text": "done", "file_context": file_context}

    brain = FakeWorker(stream_handlers={"brain.chat": chat_stream})
    _flow, _native, ui, _brain, _audio = make_flow(brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "create"}]})

    done_params = ui.last_call("ui.chat.done")["params"]
    assert done_params["text"] == "done"
    assert done_params["file_context"] == file_context


def test_chat_request_reuses_conversation_tool_context():
    """Verify switched conversations keep their stored tool grants."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit(
            "ui.chat.request",
            {
                "request_id": "chat-1",
                "messages": [{"role": "user", "content": "edit again"}],
                "tool_context": tool_context,
            },
        )

    params = brain.last_call("brain.chat")["params"]
    assert params["allowed_tools"] == ["read_file", "edit_file"]
    assert params["pinned_tools"] == ["read_file", "edit_file"]
    assert params["file_access_mode"] == "ask"
    assert ui.last_call("ui.chat.done")["params"]["tool_context"] == tool_context


def test_chat_request_context_policy_is_absolute_over_legacy_tool_context():
    """Verify visible chat policy does not inherit older hidden tool grants."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    context_policy = {
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
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit(
            "ui.chat.request",
            {
                "request_id": "chat-1",
                "messages": [{"role": "user", "content": "edit again"}],
                "tool_context": tool_context,
                "context_policy": context_policy,
            },
        )

    params = brain.last_call("brain.chat")["params"]
    assert params["allowed_tools"] == []
    assert params["pinned_tools"] == []
    assert params["file_access_mode"] == "off"


def test_chat_request_all_off_policy_does_not_inject_selection_context():
    """Verify all-off chat policy does not silently capture selected UI text."""
    context_policy = {
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
            "messages": [{"role": "user", "content": "can you see the webpage?"}],
            "context_policy": context_policy,
        },
    )

    params = brain.last_call("brain.chat")["params"]
    assert not native.calls_for("native.context.snapshot")
    assert "selected chat bubble" not in "\n\n".join(str(msg.get("content") or "") for msg in params["messages"])


def test_chat_request_browser_on_fetches_context_from_chat_policy():
    """Verify chat Browser/Web On frontloads browser text independent of caller defaults."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    context_policy = {
        "context_ambient": False,
        "context_documents_mode": "off",
        "context_browser_mode": "auto",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "file_access": "off",
        "tools": {},
    }
    native = FakeWorker(
        {
            "native.context.snapshot": lambda params: {
                "selected_text": "",
                "clipboard_text": "",
                "active_app": {"name": "Browser", "pid": 42},
                "browser_url": "https://example.test/chat" if params.get("include_browser_url") else "",
                "browser_hwnd": 777 if params.get("include_browser_url") else 0,
            },
            "native.context.browser_content": lambda params: {
                "url": params.get("url") or "",
                "content": "Fetched chat browser page",
            },
        }
    )
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})

    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        ui.emit(
            "ui.chat.request",
            {
                "request_id": "chat-1",
                "messages": [{"role": "user", "content": "summarize the page"}],
                "context_policy": context_policy,
            },
        )

    params = brain.last_call("brain.chat")["params"]
    assert "Fetched chat browser page" in params["messages"][0]["content"]
    assert native.last_call("native.context.browser_content")["params"] == {
        "url": "https://example.test/chat",
        "hwnd": 777,
        "app": "",
    }


def test_chat_request_keeps_file_tools_off_when_caller_files_are_off():
    """Verify chat does not grant hidden file tools when Files is off."""
    rows = [
        {
            "file_access": "off",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "create a file"}]})

    params = brain.last_call("brain.chat")["params"]
    assert params["use_tools"] is False
    assert params["file_access_mode"] == "off"
    assert not ({"list_files", "read_file", "create_file", "edit_file", "write_file"} & set(params["allowed_tools"]))


def test_chat_request_inherits_first_caller_file_tools():
    """Verify chat requests carry file tool grants from the first caller row."""
    rows = [
        {
            "file_access": "ask",
            "tools": {},
            "context_documents_mode": "off",
            "context_browser_mode": "off",
            "context_github_mode": "off",
            "context_memory_mode": "off",
        }
    ]
    brain = FakeWorker(stream_handlers={"brain.chat": query_stream("chat reply")})
    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(brain=brain)
        ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "edit a file"}]})

    params = brain.last_call("brain.chat")["params"]
    assert params["use_tools"] is True
    assert params["file_access_mode"] == "ask"
    assert set(params["allowed_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}
    assert set(params["pinned_tools"]) >= {"list_files", "read_file", "create_file", "edit_file", "write_file"}


def test_chat_live_file_approval_routes_to_ui_and_brain():
    """Verify live file approval requests are resolved back to the brain worker."""
    def chat_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Emit one live file approval request during chat."""
        on_event(
            "live_file.approval.request",
            {"approval_id": "file-1", "action": "edit_file", "path": "note.txt"},
            1,
        )
        on_event("reply.done", {"text": "ok"}, 1)
        return {"text": "ok"}

    brain = FakeWorker(
        handlers={"brain.live_file.approval.respond": lambda params: {"ok": True, "approved": params["approved"]}},
        stream_handlers={"brain.chat": chat_stream},
    )
    ui = FakeWorker(handlers={"ui.live_file.approval.request": lambda _params: {"approved": True}})
    _flow, _native, ui, brain, _audio = make_flow(ui=ui, brain=brain)

    ui.emit("ui.chat.request", {"request_id": "chat-1", "messages": [{"role": "user", "content": "edit"}]})

    assert ui.last_call("ui.live_file.approval.request")["params"]["approval_id"] == "file-1"
    assert brain.last_call("brain.live_file.approval.respond")["params"] == {
        "approval_id": "file-1",
        "approved": True,
    }


def test_icon_summon_routes_to_first_caller_like_default_hotkey():
    """Verify icon summon routes to first caller like default hotkey behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="icon selected")})
    with caller_config(rows):
        _flow, native, ui, _brain, _audio = make_flow(native=native)
        ui.emit("ui.summon_caller", {"caller_idx": 0})

    assert native.last_call("native.context.snapshot")["params"]["include_selection"] is True
    assert ui.last_call("ui.show_intent")["params"]["caller_idx"] == 0


def test_hotkey_followup_injects_active_chat_file_context():
    """Verify prior file metadata is replayed as hidden context for hotkey follow-ups."""
    path = r"C:\repo\model_files\hello_world.py"
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_screenshot": "off",
            "context_clipboard": False,
            "context_memory_mode": "off",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    ui = FakeWorker(
        {
            "ui.chat.active_history": lambda _params: {
                "history": [{"role": "user", "content": "create a file"}],
                "project_id": None,
                "context": "Original ambient context",
                "file_context": [
                    {
                        "tool": "create_file",
                        "path": path,
                        "relative_path": "hello_world.py",
                        "ok": True,
                    }
                ],
                "tool_context": {
                    "allowed_tools": ["read_file", "edit_file"],
                    "pinned_tools": ["read_file", "edit_file"],
                    "file_access_mode": "ask",
                },
            }
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "edit that file"})

    query = brain.last_call("brain.query")["params"]
    assert path in query["ambient_text"]
    assert "Original ambient context" in query["ambient_text"]
    assert "Conversation Context" in query["ambient_text"]
    assert "Conversation File Context" in query["ambient_text"]
    assert query["allowed_tools"] == []
    assert query["pinned_tools"] == []
    assert query["file_access_mode"] == "off"


def test_hotkey_followup_keeps_current_tools_separate_from_active_chat_tools():
    """Verify chat tool grants are history context, not current hotkey tools."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents_mode": "model",
            "context_browser_mode": "model",
            "context_github_mode": "off",
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
            "file_access": "off",
        }
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="")})
    ui = FakeWorker(
        {
            "ui.chat.active_history": lambda _params: {
                "history": [{"role": "user", "content": "create a file"}],
                "project_id": None,
                "tool_context": {
                    "allowed_tools": ["read_file", "edit_file"],
                    "pinned_tools": ["read_file", "edit_file"],
                    "file_access_mode": "ask",
                },
            }
        }
    )
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("done")})

    with caller_config(rows):
        _flow, _native, ui, brain, _audio = make_flow(native=native, ui=ui, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "Use page and edit that file"})

    query = brain.last_call("brain.query")["params"]
    assert query["allowed_tools"] == ["get_context.documents", "web_search", "get_context.browser"]
    assert query["pinned_tools"] == ["get_context", "web_search"]
    assert query["file_access_mode"] == "off"
    assert query["use_tools"] is True


def test_caller_memory_modes_map_to_injected_or_model_decided_access():
    """Verify caller memory modes map to injected or model decided access behavior."""
    rows = [
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_memory_mode": "off",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": True,
            "context_memory_mode": "model",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
        {
            "paste_back": False,
            "context_ambient": True,
            "context_documents": False,
            "context_tools": False,
            "context_memory_mode": "auto",
            "context_screenshot": "off",
            "context_clipboard": False,
        },
    ]
    native = FakeWorker({"native.context.snapshot": context_handler(selected="selected")})
    brain = FakeWorker(stream_handlers={"brain.query": query_stream("reply")})
    with caller_config(rows):
        _flow, native, ui, brain, _audio = make_flow(native=native, brain=brain)
        _flow.begin_caller(0)
        ui.emit("ui.intent.chosen", {"custom": "use no memory"})
        _flow.begin_caller(1)
        ui.emit("ui.intent.chosen", {"custom": "model may search memory"})
        _flow.begin_caller(2)
        ui.emit("ui.intent.chosen", {"custom": "use injected memory"})

    calls = brain.calls_for("brain.query")
    assert calls[0]["params"]["memory_enabled"] is False
    assert "memory_search" not in calls[0]["params"]["allowed_tools"]
    assert calls[1]["params"]["memory_enabled"] is False
    assert "memory_search" in calls[1]["params"]["allowed_tools"]
    assert calls[2]["params"]["memory_enabled"] is True
    assert "memory_search" not in calls[2]["params"]["allowed_tools"]


def test_memory_events_route_to_brain_and_seed_ui_viewer():
    """Verify memory events route to brain and seed ui viewer behavior."""
    brain = FakeWorker({"brain.memory.list": lambda _params: {"facts": [{"id": "1", "text": "remember"}]}})
    _flow, native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.memory.open_requested", {})
    ui.emit("ui.memory.add", {"text": "new fact", "category": "general"})
    ui.emit("ui.memory.update", {"id": "1", "text": "updated", "category": "project_context"})
    ui.emit("ui.memory.delete", {"id": "1"})

    assert ui.last_call("ui.show_memory")["params"]["facts"][0]["text"] == "remember"
    assert brain.last_call("brain.memory.add")["params"]["text"] == "new fact"
    assert brain.last_call("brain.memory.update")["params"]["fact_id"] == "1"
    assert brain.last_call("brain.memory.delete")["params"]["fact_id"] == "1"


def test_addon_and_agent_tray_events_route_through_supervisor():
    """Verify addon and agent tray events route through supervisor behavior."""
    brain = FakeWorker(
        {
            "brain.addons.list": lambda _params: {
                "addons_dir": "/tmp/addons",
                "addons": [{"name": "demo", "status": "loaded", "tray_actions": ["Run"]}],
            },
            "brain.addons.run_action": lambda _params: {"ok": True, "message": "ran demo"},
            "brain.addons.repair_environment": lambda _params: {"ready": True},
            "brain.addons.install_archive": lambda _params: {"id": "demo2"},
            "brain.addons.install_folder": lambda _params: {"id": "demo3"},
            "brain.addons.run_hotkey": lambda _params: {"prompt": "hotkey prompt"},
            "brain.query": lambda _params: {"text": "hotkey answer"},
            "brain.agent.history.list": lambda _params: {
                "runs_root": "/tmp/runs",
                "runs": [{"title": "recent", "run_dir": "/tmp/runs/1"}],
            },
        }
    )
    _flow, native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.addons.open_requested", {})
    ui.emit("ui.addons.run_action", {"addon_id": "demo", "label": "Run"})
    ui.emit("ui.addons.repair_environment", {"addon_id": "demo"})
    ui.emit("ui.addons.install_archive", {"path": "/tmp/demo.wisp"})
    ui.emit("ui.addons.install_folder", {"path": "/tmp/demo-folder"})
    native.emit("native.hotkey", {"kind": "addon", "addon_id": "demo", "hotkey_id": "hk"})
    ui.emit("ui.agent.task_requested", {})
    ui.emit("ui.agent.history_requested", {})

    assert ui.last_call("ui.show_addons")["params"]["addons"][0]["name"] == "demo"
    assert brain.last_call("brain.addons.run_action")["params"]["addon_id"] == "demo"
    assert brain.last_call("brain.addons.repair_environment")["params"]["addon_id"] == "demo"
    assert brain.last_call("brain.addons.install_archive")["params"]["path"] == "/tmp/demo.wisp"
    assert brain.last_call("brain.addons.install_folder")["params"]["path"] == "/tmp/demo-folder"
    assert brain.last_call("brain.addons.run_hotkey")["params"]["hotkey_id"] == "hk"
    assert ui.calls_for("ui.reply.notice")
    assert ui.calls_for("ui.show_agent_task")
    assert ui.last_call("ui.show_agent_history")["params"]["runs"][0]["title"] == "recent"


def test_agent_run_request_streams_through_brain_agent_run():
    """Verify agent run request streams through brain agent run behavior."""
    def agent_stream(params: dict[str, Any], on_event) -> dict[str, Any]:
        """Verify agent stream behavior."""
        assert params["spec"]["title"] == "demo task"
        on_event("agent.log", {"line": "started"}, 1)
        on_event("agent.trace", {"entry": "trace"}, 1)
        on_event("agent.approval.request", {"approval_id": "abc", "action": "shell"}, 1)
        on_event("agent.done", {"run_dir": "/tmp/run", "final": "done"}, 1)
        return {"run_dir": "/tmp/run", "final": "done"}

    brain = FakeWorker(stream_handlers={"brain.agent.run": agent_stream})
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.agent.run_requested",
        {"spec": {"title": "demo task", "max_runtime_minutes": 1}},
    )

    assert brain.last_call("brain.agent.run")["params"]["spec"]["title"] == "demo task"
    assert ui.last_call("ui.agent.log")["params"]["line"] == "started"
    assert ui.last_call("ui.agent.trace")["params"]["entry"] == "trace"
    assert ui.last_call("ui.agent.approval.request")["params"]["approval_id"] == "abc"
    assert ui.last_call("ui.agent.done")["params"]["final"] == "done"


def test_agent_approval_and_cancel_route_to_brain():
    """Verify agent approval and cancel route to brain behavior."""
    held_on_event = {}

    def agent_stream(_params: dict[str, Any], on_event) -> dict[str, Any]:
        """Verify agent stream behavior."""
        held_on_event["handler"] = on_event
        return {"run_dir": "/tmp/run", "cancelled": True}

    brain = FakeWorker(
        handlers={
            "brain.cancel": lambda params: {"cancelled": params.get("target") == 1},
            "brain.agent.approval.respond": lambda params: {"ok": True, "approved": params["approved"]},
        },
        stream_handlers={"brain.agent.run": agent_stream},
    )
    flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit(
        "ui.agent.run_requested",
        {"spec": {"title": "demo task", "max_runtime_minutes": 1}},
    )
    with flow._lock:
        flow._active_agent_stream_id = 1
    ui.emit("ui.agent.cancel_requested", {})
    ui.emit("ui.agent.approval.respond", {"approval_id": "abc", "approved": True})

    assert brain.last_call("brain.cancel")["params"]["target"] == 1
    assert brain.last_call("brain.agent.approval.respond")["params"] == {
        "approval_id": "abc",
        "approved": True,
    }


def test_agent_history_routes_read_retry_and_continue_specs():
    """Verify agent history routes read retry and continue specs behavior."""
    brain = FakeWorker(
        {
            "brain.agent.history.list": lambda _params: {
                "runs_root": "/tmp/runs",
                "runs": [{"title": "recent", "run_dir": "/tmp/runs/1"}],
            },
            "brain.agent.history.read": lambda params: {
                "run_dir": params["run_dir"],
                "final": "final",
                "run_log": "log",
                "verbose_log": "trace",
            },
            "brain.agent.history.retry_spec": lambda _params: {"spec": {"title": "retry"}},
            "brain.agent.history.continue_spec": lambda _params: {"spec": {"title": "continue"}},
        }
    )
    _flow, _native, ui, brain, _audio = make_flow(brain=brain)

    ui.emit("ui.agent.history_requested", {})
    ui.emit("ui.agent.history.read", {"run_dir": "/tmp/runs/1"})
    ui.emit("ui.agent.history.retry", {"run_dir": "/tmp/runs/1"})
    ui.emit("ui.agent.history.continue", {"run_dir": "/tmp/runs/1"})

    assert ui.last_call("ui.show_agent_history")["params"]["runs_root"] == "/tmp/runs"
    assert ui.last_call("ui.agent.history.detail")["params"]["verbose_log"] == "trace"
    task_calls = ui.calls_for("ui.show_agent_task")
    assert task_calls[-2]["params"]["spec"]["title"] == "retry"
    assert task_calls[-1]["params"]["spec"]["title"] == "continue"


def test_settings_reload_refreshes_supervisor_brain_audio_and_hotkeys(monkeypatch):
    """Verify settings reload refreshes supervisor brain audio and hotkeys behavior."""
    reload_calls: list[str] = []
    monkeypatch.setattr(config, "reload", lambda: reload_calls.append("supervisor"))
    _flow, native, ui, brain, audio = make_flow()

    ui.emit("ui.settings.applied", {})

    assert reload_calls == ["supervisor"]
    assert brain.calls_for("brain.config.reload")
    assert audio.calls_for("audio.prewarm")
    # The native worker must reload its own config and replace registrations in
    # one operation, else a changed hotkey can keep the old listener alive until
    # a second Apply.
    assert native.calls_for("native.hotkeys.reload")
    assert not native.calls_for("native.config.reload")
    assert not native.calls_for("native.hotkeys.stop")
    assert not native.calls_for("native.hotkeys.start")


def test_start_hotkeys_surfaces_failed_registration_to_user():
    """Verify start hotkeys surfaces failed registration to user behavior."""
    native = FakeWorker(
        {"native.hotkeys.start": lambda _params: {"started": False, "reason": "Carbon unavailable"}}
    )
    flow, _native, ui, _brain, _audio = make_flow(native=native)

    result = flow.start_hotkeys()

    assert result["started"] is False
    assert ui.last_call("ui.reply.notice")["params"]["text"].startswith("Global hotkeys did not start")
