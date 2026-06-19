"""Tests for test intent overlay."""

import os
import sys

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_prompt_input_grabs_keyboard_on_windows(monkeypatch):
    """Verify custom prompt input grabs keyboard on windows behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    grabs: list[QLineEdit] = []
    releases: list[QLineEdit] = []
    force_foreground_calls: list[bool] = []

    def grab_keyboard(self):
        """Verify grab keyboard behavior."""
        grabs.append(self)

    def release_keyboard(self):
        """Verify release keyboard behavior."""
        releases.append(self)

    monkeypatch.setattr(intent_overlay, "_IS_WIN", True)
    monkeypatch.setattr(
        intent_overlay.IntentOverlay,
        "_win_force_foreground",
        lambda self: force_foreground_calls.append(True),
    )
    monkeypatch.setattr(QLineEdit, "grabKeyboard", grab_keyboard)
    monkeypatch.setattr(QLineEdit, "releaseKeyboard", release_keyboard)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_custom_mode()

        assert overlay._input_line.isHidden() is False
        assert force_foreground_calls == [True]
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
def test_intent_overlay_key_debug_avoids_prompt_text(monkeypatch, capsys):
    """Verify intent overlay key debug avoids prompt text behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_DEBUG_KEYS", True)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._custom_mode = True
        overlay._input_line.show()
        overlay._debug_key(
            "test",
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_A,
                Qt.KeyboardModifier.NoModifier,
                "a",
            ),
        )

        err = capsys.readouterr().err
        assert "[wisp-intent]" in err
        assert "text=printable-len:1" in err
        assert " a " not in err
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_auto_custom_prompt_keeps_first_typed_key(monkeypatch):
    """Verify blank custom key starts typing mode without dropping first input."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    monkeypatch.setattr(intent_overlay, "_IS_WIN", False)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": ""}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    try:
        overlay._enter_auto_custom_mode()

        assert overlay._custom_mode is True
        assert overlay._input_line.isHidden() is False
        assert overlay._drop_next_keypress is False
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_cancel_if_focus_leaves(monkeypatch):
    """Verify clicking away cancels a pending custom prompt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    cancelled: list[bool] = []
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(caller_idx=0)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    try:
        overlay.show()
        overlay._enter_custom_mode()
        monkeypatch.setattr(
            intent_overlay.QApplication,
            "focusWidget",
            staticmethod(lambda: overlay._input_line),
        )

        overlay._cancel_if_focus_left()

        assert cancelled == []
        assert overlay._handled is False

        monkeypatch.setattr(
            intent_overlay.QApplication,
            "focusWidget",
            staticmethod(lambda: None),
        )

        overlay._cancel_if_focus_left()

        assert cancelled == [True]
        assert overlay._handled is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_translates_default_custom_prompt_label(monkeypatch):
    """Verify intent overlay translates default custom prompt label behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": ""}]
    try:
        row = intent_overlay._build_rows(0)[-1]

        assert row["label"] == i18n.t("Custom prompt")
        assert row["label"] != "Custom prompt"
    finally:
        config.CALLER_ROWS[:] = old_rows
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_intent_overlay_preserves_custom_prompt_label():
    """Verify intent overlay preserves custom prompt label behavior."""
    import config
    import ui.intent_overlay as intent_overlay

    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s", "custom_label": "Freeform"}]
    try:
        row = intent_overlay._build_rows(0)[-1]

        assert row["label"] == "Freeform"
    finally:
        config.CALLER_ROWS[:] = old_rows


def test_intent_overlay_context_palette_uses_theme_settings():
    """Verify context chip colors derive from the active settings theme."""
    import config
    import ui.intent_overlay as intent_overlay

    old_values = {
        "THEME_MODE": getattr(config, "THEME_MODE", "system"),
        "THEME_DARK_BG": getattr(config, "THEME_DARK_BG", "#1c1e26"),
        "THEME_DARK_SURFACE": getattr(config, "THEME_DARK_SURFACE", "#17181d"),
        "THEME_DARK_TEXT": getattr(config, "THEME_DARK_TEXT", "#e8e8f0"),
        "THEME_DARK_ACCENT": getattr(config, "THEME_DARK_ACCENT", "#8b87ff"),
    }
    try:
        config.THEME_MODE = "dark"
        config.THEME_DARK_BG = "#101820"
        config.THEME_DARK_SURFACE = "#203040"
        config.THEME_DARK_TEXT = "#f0ead6"
        config.THEME_DARK_ACCENT = "#ff3366"

        palette = intent_overlay._theme_palette()

        assert palette["ctx_on"].name().lower() == "#ff3366"
        assert palette["ctx_text"].name().lower() == "#f0ead6"
        assert palette["badge_bg"].name().lower() == "#203040"
        assert palette["bg"].name().lower() == "#101820"
    finally:
        for key, value in old_values.items():
            setattr(config, key, value)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_cycles_context_chip(monkeypatch):
    """Verify numeric context chips cycle independently of intent rows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay.update_context_items(
            [{"id": "browser", "key": "2", "label": "Browser", "state": "auto", "tokens": "? tok"}]
        )
        assert overlay.context_choices()[0]["state"] == "auto"
        assert overlay.context_choices()[0]["touched"] is False

        assert overlay._cycle_context_key("2") is True
        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is True

        overlay.update_context_items(
            [{"id": "browser", "key": "2", "label": "Browser", "state": "auto", "tokens": "? tok"}]
        )
        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay.context_choices()[0]["touched"] is True

        overlay.show()
        app.processEvents()
        QTest.mouseClick(
            overlay,
            Qt.MouseButton.LeftButton,
            pos=QPoint(
                intent_overlay._PAD_H + intent_overlay._CTX_CHIP_W // 2,
                intent_overlay._PAD_V
                + intent_overlay._CONV_H
                + intent_overlay._CTX_TOP
                + intent_overlay._CTX_CHIP_H // 2,
            ),
        )
        app.processEvents()
        assert overlay.context_choices()[0]["state"] == "on"

        assert overlay._cycle_context_key("2") is True
        assert overlay.context_choices()[0]["state"] == "off"
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_conversation_choice_toggles_new_and_continue():
    """Verify the intent overlay exposes the selected conversation mode."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat", "selected": True},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}

        overlay._toggle_conversation_mode()
        assert overlay.conversation_choice() == {"mode": "new"}

        overlay._set_conversation_choice(0)
        assert overlay.conversation_choice() == {"mode": "continue", "index": 0}
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_defaults_to_new_when_history_has_no_active_selection():
    """Verify loaded history does not imply continuation on app start."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        conversation_options=[
            {"index": 1, "title": "Latest chat"},
            {"index": 0, "title": "Older chat"},
        ],
    )
    try:
        assert overlay.conversation_choice() == {"mode": "new"}

        overlay._toggle_conversation_mode()
        assert overlay.conversation_choice() == {"mode": "continue", "index": 1}
    finally:
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_intent_overlay_dedupes_raw_and_qt_context_key(monkeypatch):
    """Verify a Windows raw-hook context key is not immediately toggled again by Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    import ui.intent_overlay as intent_overlay

    app = QApplication.instance() or QApplication(sys.argv)
    old_rows = list(config.CALLER_ROWS)
    config.CALLER_ROWS[:] = [{"intents": [], "custom_key": "s"}]
    overlay = intent_overlay.IntentOverlay(
        caller_idx=0,
        context_items=[{"id": "browser", "key": "2", "label": "Browser", "state": "on"}],
    )
    try:
        overlay._on_raw_key("2")

        assert overlay.context_choices()[0]["state"] == "off"
        assert overlay._is_duplicate_qt_context_key("2") is True
    finally:
        config.CALLER_ROWS[:] = old_rows
        overlay.close()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_apply_intent_context_choices_updates_caller_policy():
    """Verify overlay context choices become real per-prompt caller policy."""
    from runtime.supervisor.flows import FlowController

    caller = {
        "context_ambient": True,
        "context_documents_mode": "off",
        "context_browser_mode": "auto",
        "context_memory_mode": "auto",
        "context_screenshot": "auto",
        "file_access": "ask",
    }

    updated = FlowController._apply_intent_context_choices(
        caller,
        [
            {"id": "browser", "state": "off"},
            {"id": "selection", "state": "off"},
            {"id": "memory", "state": "auto"},
            {"id": "files", "state": "off"},
            {"id": "ambient", "state": "on", "default_state": "off", "touched": True},
        ],
    )

    assert updated["context_documents_mode"] == "auto"
    assert updated["context_browser_mode"] == "off"
    assert updated["_context_selection_enabled"] is False
    assert updated["context_memory_mode"] == "model"
    assert updated["file_access"] == "off"

    unchanged = FlowController._apply_intent_context_choices(
        caller,
        [{"id": "ambient", "state": "on", "default_state": "on"}],
    )
    assert unchanged["context_documents_mode"] == "off"
