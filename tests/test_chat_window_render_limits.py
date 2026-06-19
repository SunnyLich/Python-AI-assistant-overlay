"""Tests for test chat window render limits."""

import os
import sys
import time

import pytest

from ui.chat_window import (
    ChatWindow,
    _CHAT_RENDER_CHAR_LIMIT,
    _chat_model_messages,
    _file_context_text,
    _format_conversation_datetime,
    _message_timestamp_text,
    _truncate_for_display,
    _truncate_segments_for_display,
)


def test_truncate_for_display_caps_large_text():
    """Verify truncate for display caps large text behavior."""
    text = "x" * (_CHAT_RENDER_CHAR_LIMIT + 50)

    result = _truncate_for_display(text, _CHAT_RENDER_CHAR_LIMIT, "chat display")

    assert len(result) < len(text)
    assert "chat display truncated" in result
    assert "50 chars hidden" in result


def test_truncate_segments_preserves_visible_prefix_and_adds_marker():
    """Verify truncate segments preserves visible prefix and adds marker behavior."""
    segments = [
        ("thought:" + "a" * 20, True),
        ("reply:" + "b" * 20, False),
    ]

    result = _truncate_segments_for_display(segments, limit=24)

    assert result[0] == ("thought:" + "a" * 16, True)
    assert result[1][1] is False
    assert "chat display truncated" in result[1][0]


def test_conversation_datetime_formats_for_history_display():
    """Verify conversation timestamps are display metadata."""
    assert _format_conversation_datetime("2026-06-19T15:52:16+00:00")


def test_chat_model_messages_excludes_timestamp_metadata():
    """Verify model payload only carries role/content and screenshots."""
    messages = [
        {"role": "user", "content": "hi", "created_at": "2026-06-19T15:52:16+00:00"},
        {"role": "assistant", "content": "hello", "updated_at": "2026-06-19T15:52:17+00:00"},
    ]

    assert _chat_model_messages(messages) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_message_timestamp_formats_from_metadata():
    """Verify message timestamps are display metadata."""
    assert _message_timestamp_text(
        {"role": "user", "content": "hi", "created_at": "2026-06-19T15:52:16+00:00"}
    )


def test_file_context_text_mentions_exact_prior_path():
    """Verify file metadata can resolve later 'that file' references."""
    text = _file_context_text([
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "ok": True,
            "message": "Created hello_world.py.",
        }
    ])

    assert r"C:\repo\model_files\hello_world.py" in text
    assert "that file" in text


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_window_is_not_always_on_top():
    """Verify chat window behaves like a normal app window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = ChatWindow([{"messages": [{"role": "user", "content": "hello"}]}], lambda _messages: iter(()))
    try:
        assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_window_opens_large_last_chat_without_render_freeze():
    """Verify chat window opens large last chat without render freeze behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {"role": "user", "content": "what can it see?"},
                {"role": "assistant", "content": "tools help\n" + ("x" * 180_000)},
            ],
            "context": "ambient context\n" + ("y" * 180_000),
        }
    ]

    started = time.perf_counter()
    window = ChatWindow(conversations, lambda _messages: iter(()))
    elapsed = time.perf_counter() - started
    try:
        assert elapsed < 1.0
        assert window._built_pages == {0}
        page = window._stack.widget(0)
        assert getattr(page, "_msg_layout", None) is not None
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_sidebar_options_button_stays_visible_for_long_titles():
    """Verify chat sidebar options button stays visible for long titles behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {
                    "role": "user",
                    "content": "this is a very long conversation title " * 12,
                }
            ],
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        row, title_btn = window._make_sidebar_row(0, conversations[0])
        buttons = row.findChildren(QPushButton)
        menu_btn = next(button for button in buttons if button is not title_btn)

        assert title_btn.minimumWidth() == 0
        assert "this is a very long conversation title" in title_btn.toolTip()
        assert menu_btn.width() == 32
        assert menu_btn.text() == "⋮"
        assert menu_btn.isHidden() is False
    finally:
        row.deleteLater()
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_sidebar_shows_conversation_timestamp():
    """Verify history rows include conversation date/time metadata."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "updated_at": "2026-06-19T15:52:16+00:00",
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        row, title_btn = window._make_sidebar_row(0, conversations[0])

        assert title_btn._subtitle
        assert title_btn._subtitle in title_btn.toolTip()
    finally:
        row.deleteLater()
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_bubble_header_shows_message_timestamp():
    """Verify each chat turn displays its own date/time metadata."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [
                {
                    "role": "user",
                    "content": "hello",
                    "created_at": "2026-06-19T15:52:16+00:00",
                }
            ],
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        labels = [label.text() for label in window.findChildren(QLabel)]

        assert any("2026" in text for text in labels)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_context_policy_controls_are_compact_menu_chips(monkeypatch):
    """Verify chat context controls render as compact chips with popup choices."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QComboBox, QMenu, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "context_policy": {
                "context_ambient": False,
                "context_documents_mode": "off",
                "context_browser_mode": "off",
                "context_github_mode": "off",
                "context_memory_mode": "off",
                "context_screenshot": "off",
                "context_clipboard": False,
                "file_access": "off",
                "tools": {},
            },
        }
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    captured = []

    def fake_popup(self, pos):
        """Capture the menu that would be opened for a context chip."""
        captured.append((self, pos, [action.data() for action in self.actions()]))
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    try:
        assert set(window._context_controls) == {
            "ambient",
            "browser",
            "selection",
            "clipboard",
            "screenshot",
            "memory",
            "files",
        }
        assert all(isinstance(control, QPushButton) for control in window._context_controls.values())
        assert not any(isinstance(control, QComboBox) for control in window._context_controls.values())

        browser_chip = window._context_controls["browser"]
        assert browser_chip.objectName() == "chatContextChip_browser"
        assert "\n" in browser_chip.text()
        assert window._context_controls["selection"].property("context_state") == "off"

        window._show_context_policy_menu("browser")

        assert captured
        assert {"off", "on", "auto"} <= set(captured[0][2])

        window._set_context_policy_state("browser", "on")

        assert conversations[0]["context_policy"]["context_browser_mode"] == "auto"
        assert browser_chip.property("context_state") == "on"
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_window_reports_initial_active_conversation():
    """Verify opening chat retargets follow-up prompts to the shown conversation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    selected = []
    conversations = [
        {"messages": [{"role": "user", "content": "old"}]},
        {"messages": [{"role": "user", "content": "new"}]},
    ]
    window = ChatWindow(conversations, lambda _messages: iter(()), active_idx=0, on_select=selected.append)
    try:
        assert selected == [0]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_sidebar_options_menu_anchors_to_button(monkeypatch):
    """Verify chat sidebar options menu anchors to button behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMenu, QPushButton

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hello"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    captured = []

    def fake_popup(self, pos):
        """Verify fake popup behavior."""
        captured.append(pos)
        return None

    monkeypatch.setattr(QMenu, "popup", fake_popup)
    try:
        row, title_btn = window._make_sidebar_row(0, conversations[0])
        menu_btn = next(button for button in row.findChildren(QPushButton) if button is not title_btn)

        window._open_conversation_menu(0, menu_btn)

        assert captured == [menu_btn.mapToGlobal(menu_btn.rect().bottomLeft())]
    finally:
        row.deleteLater()
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_final_text_replaces_partial_stream_before_persist():
    """Verify final chat text replaces an incomplete streamed draft."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hi"}]}]
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window._current_ai_text = "first part"
        window._current_ai_reply_text = "first part"
        window._current_ai_segments = [("first part", False)]
        window._current_ai_parser = None

        window._on_final_text("first part plus second part")
        window._on_finished()

        assert conversations[0]["messages"][-1] == {
            "role": "assistant",
            "content": "first part plus second part",
            "created_at": conversations[0]["messages"][-1]["created_at"],
        }
        assert conversations[0]["updated_at"]
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_followup_injects_hidden_file_context():
    """Verify file metadata is sent as hidden system context, not message turns."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    captured = []
    conversations = [
        {
            "messages": [{"role": "user", "content": "add a comment"}],
            "file_context": [
                {
                    "tool": "create_file",
                    "path": r"C:\repo\model_files\hello_world.py",
                    "relative_path": "hello_world.py",
                    "ok": True,
                }
            ],
        }
    ]

    def send_fn(messages):
        captured.append(messages)
        yield "ok"

    window = ChatWindow(conversations, send_fn)
    try:
        window._send("edit that file")
        for _ in range(20):
            app.processEvents()
            if captured:
                break

        assert captured
        assert r"C:\repo\model_files\hello_world.py" in captured[0][0]["content"]
        assert all("file_context" not in message for message in captured[0])
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_window_persists_returned_tool_context():
    """Verify returned tool policy metadata is stored with the conversation."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    conversations = [{"messages": [{"role": "user", "content": "hi"}]}]
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    window = ChatWindow(conversations, lambda _messages: iter(()))
    try:
        window._current_ai_text = "done"
        window._current_ai_reply_text = "done"
        window._on_metadata({"tool_context": tool_context})
        window._on_finished()

        assert conversations[0]["tool_context"] == tool_context
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_chat_window_drop_attachments_feed_next_message_context_and_image():
    """Verify dropped files/images attach to the next outgoing chat message."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    captured = []
    conversations = [{"messages": []}]

    def send_fn(messages):
        captured.append(messages)
        yield "ok"

    window = ChatWindow(conversations, send_fn)
    try:
        added = window._add_attachment_items([
            ("notes.txt", "remember this text", "text"),
            ("shot.png", "IMAGEB64", "image"),
        ])
        assert added is True

        window._send("use the attachment")
        for _ in range(20):
            app.processEvents()
            if captured:
                break

        assert captured
        assert "remember this text" in captured[0][0]["content"]
        assert captured[0][-1]["image_base64"] == "IMAGEB64"
        assert conversations[0]["messages"][0]["image_base64"] == "IMAGEB64"
        assert "remember this text" in conversations[0]["context"]
    finally:
        window.close()
        app.processEvents()
