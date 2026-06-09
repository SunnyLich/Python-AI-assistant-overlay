import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_input_grabs_keyboard_on_windows(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QLineEdit] = []
    releases: list[QLineEdit] = []

    def grab_keyboard(self):
        grabs.append(self)

    def release_keyboard(self):
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", True)
    monkeypatch.setattr(QLineEdit, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QLineEdit, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()

        assert overlay._input_line.isHidden() is False
        assert grabs == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is True

        overlay._unhook()

        assert releases == [overlay._input_line]
        assert overlay._input_grabbed_keyboard is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_parent_forwards_space_to_input(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()
        overlay._input_line.clear()
        overlay._input_line.clearFocus()
        overlay.setFocus()
        overlay._drop_next_keypress = False

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier,
            " ",
        )
        overlay.keyPressEvent(event)

        assert overlay._input_line.text() == " "
        assert event.isAccepted()
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_space_inserts_even_when_qt_event_text_is_empty(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()
        overlay._input_line.setText("hello")
        overlay._input_line.setCursorPosition(len("hello"))
        overlay._drop_next_keypress = False

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier,
            "",
        )
        QApplication.sendEvent(overlay._input_line, event)

        assert overlay._input_line.text() == "hello "
        assert overlay._custom_input_started is True
        assert event.isAccepted()
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_parent_drops_trigger_key_before_forwarding(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()
        overlay._input_line.clear()
        overlay._input_line.clearFocus()
        overlay.setFocus()
        overlay._drop_next_keypress = True

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_S,
            Qt.KeyboardModifier.NoModifier,
            "s",
        )
        overlay.keyPressEvent(event)

        assert overlay._input_line.text() == ""
        assert overlay._drop_next_keypress is False
        assert event.isAccepted()
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_focus_retry_stops_after_typing_starts(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        calls = []
        overlay._custom_mode = True
        overlay._input_line.show()
        overlay._focus_custom_input = lambda: calls.append("focus")

        overlay._custom_input_started = True
        overlay._retry_focus_custom_input()
        assert calls == []

        overlay._custom_input_started = False
        overlay._retry_focus_custom_input()
        assert calls == ["focus"]
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()
