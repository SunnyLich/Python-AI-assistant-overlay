"""Tests for test settings dialog controls."""

import os
import sys
import threading
from pathlib import Path

import pytest


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_combo_ignores_wheel_when_popup_closed():
    """Verify settings combo ignores wheel when popup closed behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _NoScrollCombo

    class FakeWheelEvent:
        """Test case for fake wheel event behavior."""
        def __init__(self) -> None:
            """Initialize the fake wheel event instance."""
            self.ignored = False

        def ignore(self) -> None:
            """Verify ignore behavior."""
            self.ignored = True

    app = QApplication.instance() or QApplication(sys.argv)
    combo = _NoScrollCombo()
    event = FakeWheelEvent()
    try:
        combo.addItems(["one", "two"])
        combo.setFocus()

        combo.wheelEvent(event)

        assert event.ignored is True
    finally:
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_memory_tab_does_not_show_stored_facts():
    """Verify settings memory tab does not show stored facts behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._env = {}
    tab = SettingsDialog._tab_memory(dialog)

    try:
        labels = {label.text() for label in tab.findChildren(QLabel)}
        combined = "\n".join(labels)
        assert "Stored Facts" not in combined
        assert "Stored facts" not in combined
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.workflow
@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_exposes_setup_check_button():
    """Verify Settings exposes the reusable setup check entry point."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    calls = []
    dialog = SettingsDialog(on_setup_check=lambda: calls.append("setup"))
    try:
        button = dialog.findChild(QPushButton, "settingsSetupCheckButton")
        assert button is not None
        assert button.text() == t("Run setup check")
        button.click()
        assert calls == ["setup"]
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_exposes_stt_settings():
    """Verify tts voice tab exposes stt settings behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)
    tab.show()
    app.processEvents()

    try:
        assert {"STT_MODEL", "STT_COMPUTE_TYPE", "STT_LANGUAGE"} <= set(dialog._fields)
        labels = {label.text() for label in tab.findChildren(QLabel)}
        assert t("Whisper model") in labels
        assert t("Compute type") in labels
        assert t("Speech language") in labels
        stt_model = dialog._fields["STT_MODEL"]
        assert not stt_model.isEditable()
        model_values = {
            stt_model.itemData(i)
            for i in range(stt_model.count())
        }
        assert {"tiny", "base", "small", "medium", "large-v3"} <= model_values
        model_labels = {stt_model.itemText(i) for i in range(stt_model.count())}
        assert "base" in model_labels
        assert "small" in model_labels
        stt_compute = dialog._fields["STT_COMPUTE_TYPE"]
        compute_labels = {stt_compute.itemText(i) for i in range(stt_compute.count())}
        assert "int8_float16" in compute_labels
        assert "float16" in compute_labels
        language_values = {
            dialog._fields["STT_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["STT_LANGUAGE"].count())
        }
        assert {"", "en", "zh", "yue", "es", "fr", "ja"} <= language_values
        language_labels = {
            dialog._fields["STT_LANGUAGE"].itemText(i)
            for i in range(dialog._fields["STT_LANGUAGE"].count())
        }
        assert "Chinese (Mandarin)" in language_labels
        assert "Cantonese" in language_labels
        assert "Chinese (Mandarin / Cantonese)" not in language_labels
        assert dialog._fields["STT_LANGUAGE"].itemData(
            dialog._fields["STT_LANGUAGE"].findText("Chinese (Mandarin)")
        ) == "zh"
        assert dialog._fields["STT_LANGUAGE"].itemData(
            dialog._fields["STT_LANGUAGE"].findText("Cantonese")
        ) == "yue"
        for key in (
            "TTS_READ_ALOUD_MIN_WORDS",
            "TTS_READ_ALOUD_MAX_WORDS",
            "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS",
            "STT_BACKGROUND_CHUNK_STEP_SECONDS",
            "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS",
            "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
        ):
            assert key in dialog._fields
        buttons = [button for button in tab.findChildren(QPushButton) if button.text() == t("Advanced settings")]
        assert buttons
        advanced_button = buttons[0]
        assert advanced_button.isCheckable()
        assert not advanced_button.isChecked()
        advanced_button.setChecked(True)
        assert dialog._fields["TTS_READ_ALOUD_MIN_WORDS"].isVisible()
        assert dialog._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"].isVisible()
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_does_not_import_stt_stack():
    """Verify tts voice tab does not import stt stack behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import core
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_stt_module = sys.modules.pop("core.stt", None)
    old_core_stt = getattr(core, "stt", None)
    had_core_stt = hasattr(core, "stt")
    if had_core_stt:
        delattr(core, "stt")

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    dialog._env = {}
    tab = None
    try:
        tab = SettingsDialog._tab_tts(dialog)

        assert "core.stt" not in sys.modules
        assert "tiny · auto / int8" in dialog._stt_active_lbl.text()
    finally:
        if tab is not None:
            tab.deleteLater()
        if old_stt_module is not None:
            sys.modules["core.stt"] = old_stt_module
        else:
            sys.modules.pop("core.stt", None)
        if had_core_stt:
            core.stt = old_core_stt
        elif hasattr(core, "stt"):
            delattr(core, "stt")
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_technical_combo_options_translate_model_names_only():
    """Verify technical combo options translate model names only behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.i18n import localize_widget_tree
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)

    try:
        localize_widget_tree(tab)

        stt_model = dialog._fields["STT_MODEL"]
        labels = {stt_model.itemText(i) for i in range(stt_model.count())}
        assert "base\uFF08\u57FA\u790E\uFF09" in labels
        assert "small\uFF08\u5C0F\u578B\uFF09" in labels
        assert stt_model.itemData(stt_model.findText("base\uFF08\u57FA\u790E\uFF09")) == "base"

        stt_compute = dialog._fields["STT_COMPUTE_TYPE"]
        compute_labels = {stt_compute.itemText(i) for i in range(stt_compute.count())}
        assert "int8" in compute_labels
        assert "float16" in compute_labels
    finally:
        config.APP_LANGUAGE = old_language
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_context_combo_localization_preserves_app_context_labels():
    """Verify app context combo localization does not leak internal boolean roles."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    import config
    from ui import i18n
    from ui.settings_panel.context_controls import AppContextCombo

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language("zh-Hant", app=app)
    host = QWidget()
    layout = QVBoxLayout(host)
    combo = AppContextCombo(False, "off")
    layout.addWidget(combo)

    try:
        i18n.localize_widget_tree(host)

        labels = [combo.itemText(i) for i in range(combo.count())]
        assert labels == ["關閉", "開啟", "開啟 + 開啟文件", "讓模型決定"]
        assert "False" not in labels
        assert "True" not in labels
        assert combo.itemData(0, int(Qt.ItemDataRole.UserRole) + 1) is False
        assert combo.itemData(1, int(Qt.ItemDataRole.UserRole) + 1) is True
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(old_language or None, app=app)
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_stt_model_dropdown_preserves_saved_custom_value():
    """Verify stt model dropdown preserves saved custom value behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import _NoScrollCombo, _set

    app = QApplication.instance() or QApplication(sys.argv)
    combo = _NoScrollCombo()
    combo.setProperty("allow_custom_saved_value", True)
    for model in ("tiny", "base", "small", "medium", "large-v3"):
        combo.addItem(model, model)

    try:
        _set(combo, "distil-large-v3")

        assert not combo.isEditable()
        assert combo.currentData() == "distil-large-v3"
        assert combo.findData("distil-large-v3") >= 0
    finally:
        combo.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_provider_model_lists_include_current_defaults():
    """Verify the static provider model dropdowns include current common choices."""
    from ui.settings_panel.dialog import _PROVIDER_MODELS

    expected = {
        "openai": {"gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"},
        "chatgpt": {"gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"},
        "google": {"gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash"},
        "anthropic": {"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"},
        "groq": {"openai/gpt-oss-120b", "meta-llama/llama-4-scout-17b-16e-instruct"},
        "xai": {"grok-4.3", "grok-4"},
        "ollama": {"llama3.3", "qwen3", "deepseek-r1"},
    }

    for provider, models in expected.items():
        assert models <= set(_PROVIDER_MODELS[provider])


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_app_tab_exposes_assistant_language_setting():
    """Verify app tab exposes assistant language setting behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

    import config
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "en"
    i18n.set_language(app=app)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_app(dialog)

    try:
        assert "APP_LANGUAGE" in dialog._fields
        assert "ASSISTANT_LANGUAGE" in dialog._fields
        assert "TRUST_PRIVACY_MODE" in dialog._fields
        assert "START_ON_LOGIN" in dialog._fields
        labels = {label.text() for label in tab.findChildren(QLabel)}
        checkboxes = {checkbox.text() for checkbox in tab.findChildren(QCheckBox)}
        assert "App language" in labels
        assert "Assistant language" in labels
        assert "Trust/privacy mode" in checkboxes
        assert "Start Wisp when you sign in" in checkboxes
        app_values = {
            dialog._fields["APP_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["APP_LANGUAGE"].count())
        }
        assert {"", "en", "zh", "zh-Hant", "es", "fr"} <= app_values
        values = {
            dialog._fields["ASSISTANT_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["ASSISTANT_LANGUAGE"].count())
        }
        assert {"", "match_user", "English", "Chinese", "Chinese (Traditional)", "Spanish"} <= values
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_app_tab_exposes_update_controls():
    """Verify the App tab exposes manual update controls."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_app(dialog)

    try:
        update_button = tab.findChild(QPushButton, "settingsUpdateButton")
        update_status = tab.findChild(QLabel, "settingsUpdateStatusLabel")

        assert update_button is not None
        assert update_button.text() == t("Check for updates")
        assert update_status is not None
        assert update_status.text() == t("Ready to check for updates.")
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_localize_widget_tree_uses_app_language(monkeypatch):
    """Verify localize widget tree uses app language behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget, QVBoxLayout

    import config
    from ui.i18n import localize_widget_tree

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    widget = QWidget()
    layout = QVBoxLayout(widget)
    button = QPushButton("Settings")
    layout.addWidget(button)

    try:
        localize_widget_tree(widget)

        assert button.text() == "设置"
    finally:
        config.APP_LANGUAGE = old_language
        widget.deleteLater()
        app.processEvents()


def test_i18n_uses_qt_catalog_without_dynamic_matching(monkeypatch):
    """Verify i18n uses qt catalog without dynamic matching behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    try:
        i18n.set_language(app=app)

        assert i18n.t("Settings") == "\u8bbe\u7f6e"
        assert i18n.t("Selection") == "\u9009\u62e9\u5185\u5bb9"
        assert i18n.t("Clipboard") == "\u526a\u8d34\u677f"
        assert i18n.t("Files") == "\u6587\u4ef6"
        assert i18n.t("Hooks: ") == "\u4e8b\u4ef6\u6302\u94a9\uff1a"
        assert i18n.t("Demo Addon Settings") == "Demo Addon Settings"
        assert i18n.t("Hooks: startup, query") == "Hooks: startup, query"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_i18n_supports_traditional_chinese(monkeypatch):
    """Verify i18n supports traditional chinese behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    try:
        i18n.set_language(app=app)

        assert i18n.current_language() == "zh-Hant"
        assert i18n.t("Settings") == "\u8a2d\u7f6e"
        assert i18n.t("Memory") == "\u8a18\u61b6"
        assert i18n.t("Selection") == "\u9078\u53d6\u5167\u5bb9"
        assert i18n.t("Clipboard") == "\u526a\u8cbc\u7c3f"
        assert i18n.t("Files") == "\u6a94\u6848"
        assert i18n.t("Chinese (Traditional)") == "\u7e41\u9ad4\u4e2d\u6587"
        assert i18n.t("Intent context keys:") == "\u610f\u5716\u4e0a\u4e0b\u6587\u9375\uff1a"
        assert i18n.t("Timeout ms:") == "\u903e\u6642\uff08\u6beb\u79d2\uff09\uff1a"
        assert i18n.t("App Settings") == "\u7a0b\u5f0f\u8a2d\u5b9a"
        assert i18n.t("Memory Settings") == "\u8a18\u61b6\u8a2d\u5b9a"
        assert i18n.t("Dictation (hold to type)") == "\u807d\u5beb\uff08\u6309\u4f4f\u5373\u53ef\u8f38\u5165\uff09"
        assert i18n.t("OpenAI API") == "OpenAI API"
        assert i18n.t("unavailable") == "\u7121\u6cd5\u4f7f\u7528"
        assert i18n.t("LLM route uses {provider} but you are not logged in.").format(
            provider="chatgpt"
        ) == "LLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
        assert i18n.t("Addon folder installed.") == "\u5916\u639b\u8cc7\u6599\u593e\u5df2\u5b89\u88dd\u3002"
        assert i18n.t(
            "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it."
        ).startswith("\u5efa\u8b70\uff1a\u958b\u555f\u5916\u639b\u7ba1\u7406\u5668")
        assert i18n.t("Agent") == "\u4ee3\u7406"
        assert i18n.t("Waiting {elapsed}").format(elapsed="5s") == "\u7b49\u5f85 5s"
        assert i18n.t("tool {tool} failed: {message}").format(
            tool="send_message",
            message="\u8a0a\u606f\u4e0d\u53ef\u70ba\u7a7a\u3002",
        ) == "\u5de5\u5177 send_message \u5931\u6557\uff1a\u8a0a\u606f\u4e0d\u53ef\u70ba\u7a7a\u3002"
        assert i18n.t(
            "Fetch the full readable text of a specific web page URL on demand. "
            "Use this when the user asks about a website/page and the passive "
            "browser preview is missing, partial, stale, or not enough."
        ).startswith("\u6309\u9700\u64f7\u53d6")
        assert i18n.t("Browser/Web: ") == "\u700f\u89bd\u5668/\u7db2\u9801\uff1a"
        assert i18n.t("Browser/Web: On") == "Browser/Web: On"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_settings_status_messages_translate_nested_values(monkeypatch):
    """Verify composed settings health messages translate dynamic values."""
    from ui.settings_panel import dialog

    translations = {
        "LLM test failed: {message}": "LLM \u6e2c\u8a66\u5931\u6557\uff1a{message}",
        "LLM route uses {provider} but you are not logged in.": "LLM \u8def\u7531\u4f7f\u7528 {provider}\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002",
        "Microphone permission: {value}.": "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a{value}\u3002",
        "Installing Kokoro: {detail}.": "\u6b63\u5728\u5b89\u88dd Kokoro\uff1a{detail}\u3002",
        "downloading packages": "\u6b63\u5728\u4e0b\u8f09\u5957\u4ef6",
        "unavailable": "\u7121\u6cd5\u4f7f\u7528",
    }
    monkeypatch.setattr(dialog, "t", lambda text: translations.get(text, text))

    assert dialog._translate_status_message(
        "LLM test failed: LLM route uses chatgpt but you are not logged in."
    ) == "LLM \u6e2c\u8a66\u5931\u6557\uff1aLLM \u8def\u7531\u4f7f\u7528 chatgpt\uff0c\u4f46\u4f60\u5c1a\u672a\u767b\u5165\u3002"
    assert (
        dialog._translate_status_message("Microphone permission: unavailable.")
        == "\u9ea5\u514b\u98a8\u6b0a\u9650\uff1a\u7121\u6cd5\u4f7f\u7528\u3002"
    )
    assert (
        dialog._translate_status_message("Installing Kokoro: downloading packages.")
        == "\u6b63\u5728\u5b89\u88dd Kokoro\uff1a\u6b63\u5728\u4e0b\u8f09\u5957\u4ef6\u3002"
    )


def test_kokoro_install_progress_text_classifies_pip_output():
    """Verify raw pip output becomes short progress phases for the Settings UI."""
    from ui.settings_panel.dialog import (
        _kokoro_install_progress_text,
        _optional_install_elapsed_text,
        _optional_install_failure_detail,
        _optional_install_log_path,
        _optional_install_no_output_timeout_seconds,
        _optional_install_progress_text,
    )

    assert _kokoro_install_progress_text("Collecting kokoro>=0.9.4") == (
        "Installing Kokoro: resolving packages."
    )
    assert _kokoro_install_progress_text("Downloading torch-2.9.0.whl") == (
        "Installing Kokoro: downloading packages."
    )
    assert _kokoro_install_progress_text("Installing collected packages: kokoro") == (
        "Installing Kokoro: installing packages."
    )
    assert _optional_install_progress_text("Collecting elevenlabs", "ElevenLabs") == (
        "Installing ElevenLabs: resolving packages."
    )
    assert _kokoro_install_progress_text("Building wheel for misaki") == (
        "Installing Kokoro: working - installer is still running."
    )
    assert _optional_install_failure_detail([
        "[notice] A new pip is available",
        "ERROR: Could not find a version that satisfies the requirement kokoro>=0.9.4",
    ]) == (
        "Last installer message: ERROR: Could not find a version that satisfies the requirement kokoro>=0.9.4"
    )
    log_path = _optional_install_log_path("Kokoro", Path("python_packages"))
    assert log_path.name == "kokoro-install.log"
    assert _optional_install_elapsed_text("Kokoro", 600, 600, log_path) == (
        f"Installing Kokoro: still running for 10m 00s; no installer output for 10m 00s. Log: {log_path}."
    )
    assert _optional_install_no_output_timeout_seconds() == 300


def test_i18n_translates_settings_apply_tool_warning(monkeypatch):
    """Verify i18n translates settings apply tool warning behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from core.llm_clients.client import tool_capability_warnings
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    warning = tool_capability_warnings(True, llm_provider="chatgpt")[0]
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    try:
        i18n.set_language(app=app)

        assert i18n.t("Heads up") == "\u63d0\u9192"
        assert i18n.t("Your settings were saved, but:") == "\u4f60\u7684\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u4f46\uff1a"
        translated = i18n.t(warning)
        assert translated != warning
        assert "ChatGPT" in translated
        assert "\u5de5\u5177" in translated
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_i18n_translates_stt_backend_status_messages(monkeypatch):
    """Verify i18n translates stt backend status messages behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    source = "Configured backend: {summary} — active backend appears after recording starts."
    try:
        for language in ("zh", "zh-Hant", "es", "fr"):
            config.APP_LANGUAGE = language
            i18n.set_language(app=app)

            translated = i18n.t(source)

            assert translated != source
            assert "{summary}" in translated
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_llm_model_routing_surface_translates_to_traditional_chinese():
    """Verify llm model routing surface translates to traditional chinese behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QComboBox, QLineEdit, QPushButton

    import config
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    dialog = SettingsDialog()

    try:
        dialog._add_api_key_row("openai")
        label_texts = {label.text() for label in dialog.findChildren(QLabel)}
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        placeholder_texts = {
            edit.placeholderText()
            for edit in dialog.findChildren(QLineEdit)
            if edit.placeholderText()
        }
        all_combo_texts = {
            combo.itemText(i)
            for combo in dialog.findChildren(QComboBox)
            for i in range(combo.count())
        }
        tooltip_texts = set()
        for cls in (QLabel, QComboBox, QLineEdit, QPushButton):
            tooltip_texts.update(
                widget.toolTip()
                for widget in dialog.findChildren(cls)
                if widget.toolTip()
            )
        visible_texts = label_texts | button_texts | placeholder_texts | all_combo_texts | tooltip_texts
        combo_texts = {
            row["api_key_combo"].itemText(i)
            for rows in dialog._model_section_rows.values()
            for row in rows
            for i in range(row["api_key_combo"].count())
        }

        assert "Model routing" not in label_texts
        assert "Choose which saved credential and model powers each purpose." not in label_texts
        assert "<small><b>Provider</b></small>" not in label_texts
        assert not any("CHAT MODEL" in text for text in label_texts)
        assert "Test Chat model" not in button_texts
        assert "Codex (ChatGPT subscription) [OAuth]" not in combo_texts
        for fragment in (
            "Provider credentials",
            "Sign in or save provider API keys",
            "AUTHENTICATION",
            "Sign in opens GitHub in your browser",
            "Add a row for each provider",
            "<small><b>Alias</b></small>",
            "alias (optional)",
            "Custom provider",
            "Any OpenAI-compatible endpoint",
            "Speech to Text",
            "Whisper model settings",
            "API keys are saved to the OS keychain",
            "Beam size",
            "Device",
            "+ Add Caller Hotkey",
            "Tools with <b>no keywords</b>",
        ):
            assert not any(fragment in text for text in visible_texts)

        assert any(text.endswith("\u63d0\u4f9b\u8005\u6191\u8b49") for text in label_texts)
        assert any(
            "\u767b\u5165\u6216\u5132\u5b58\u63d0\u4f9b\u8005 API \u91d1\u9470" in text
            for text in label_texts
        )
        assert any(
            "\u767b\u5165\u6703\u5728\u700f\u89bd\u5668\u4e2d\u958b\u555f GitHub" in text
            for text in label_texts
        )
        assert any(
            "\u70ba\u6bcf\u500b\u8981\u4f7f\u7528\u7684\u63d0\u4f9b\u8005\u65b0\u589e\u4e00\u5217" in text
            for text in label_texts
        )
        assert "<small><b>\u5225\u540d</b></small>" in label_texts
        assert "\u5225\u540d\uff08\u9078\u586b\uff09" in placeholder_texts
        assert "\u81ea\u8a02\u63d0\u4f9b\u8005".upper() in label_texts
        assert any("\u4efb\u4f55\u76f8\u5bb9 OpenAI" in text for text in label_texts)
        assert "\u6e2c\u8a66\u81ea\u8a02" in button_texts
        assert "\u8a9e\u97f3\u8f49\u6587\u5b57".upper() in label_texts
        assert any("\u6309\u4f4f\u8aaa\u8a71\u8f49\u5beb\u4f7f\u7528\u7684 Whisper" in text for text in label_texts)
        assert "\u88dd\u7f6e" in label_texts
        assert "\u675f\u5bec" in label_texts
        assert "5\uff08\u5efa\u8b70\uff09" in all_combo_texts
        assert "\u81ea\u52d5\uff08\u6709 GPU \u6642\u4f7f\u7528\uff09" in all_combo_texts
        assert any("API \u5bc6\u9470\u6703\u4fdd\u5b58\u5230\u7cfb\u7d71\u9470\u5319\u4e32" in text for text in label_texts)
        assert "+ \u65b0\u589e\u547c\u53eb\u5feb\u6377\u9375" in button_texts
        assert "\u6a21\u578b\u8def\u7531" in label_texts
        assert "\u9078\u64c7\u6bcf\u500b\u7528\u9014\u4f7f\u7528\u54ea\u500b\u5df2\u5132\u5b58\u6191\u8b49\u548c\u6a21\u578b\u3002" in label_texts
        assert "<small><b>\u63d0\u4f9b\u8005</b></small>" in label_texts
        assert any(text.endswith("\u804a\u5929\u6a21\u578b") for text in label_texts)
        assert "\u6e2c\u8a66\u804a\u5929\u6a21\u578b" in button_texts
        assert "ChatGPT Plus/Pro\uff08OAuth \u8a02\u95b1\uff09" in combo_texts
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        dialog.deleteLater()
        app.processEvents()


def test_qt_catalogs_translate_exact_spanish_and_french_sources(monkeypatch):
    """Verify qt catalogs translate exact spanish and french sources behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    try:
        config.APP_LANGUAGE = "es"
        i18n.set_language(app=app)
        assert i18n.t("Browser/Web: ") == "Navegador/Web: "
        assert i18n.t("App") == "Aplicaci\u00f3n"
        assert i18n.t("Selection") == "Selecci\u00f3n"
        assert i18n.t("Clipboard") == "Portapapeles"
        assert i18n.t("Files") == "Archivos"
        assert i18n.t("Settings") == "Configuración"

        config.APP_LANGUAGE = "fr"
        i18n.set_language(app=app)
        assert i18n.t("Browser/Web: ") == "Navigateur/Web : "
        assert i18n.t("App") == "Application"
        assert i18n.t("Selection") == "S\u00e9lection"
        assert i18n.t("Clipboard") == "Presse-papiers"
        assert i18n.t("Files") == "Fichiers"
        assert i18n.t("Settings") == "Paramètres"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


def test_i18n_translates_auth_ui_but_keeps_technical_tokens(monkeypatch):
    """Verify i18n translates auth ui but keeps technical tokens behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui import i18n

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh"
    try:
        i18n.set_language(app=app)

        assert i18n.t("Clear token") == "\u6e05\u9664\u4ee4\u724c"
        assert i18n.t("Not configured") == "\u672a\u914d\u7f6e"
        assert i18n.t("github_pat_\u2026 (not saved to .env)") == (
            "github_pat_\u2026\uff08\u4e0d\u4f1a\u4fdd\u5b58\u5230 .env\uff09"
        )
        assert "Copilot Requests: Read-only" in i18n.t(
            "Fine-grained PAT with Copilot Requests: Read-only. Stored in OS keychain."
        )
        assert ".env" in i18n.t("github_pat_\u2026 (not saved to .env)")
        assert "SDK" in i18n.t("Test token / SDK")
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_app_settings_surface_translates_to_traditional_chinese():
    """Verify app settings page checkboxes and labels translate to Traditional Chinese."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel, QLineEdit

    import config
    from ui import i18n
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    dialog = SettingsDialog()

    try:
        visible_texts = {
            label.text()
            for label in dialog.findChildren(QLabel)
            if label.text()
        }
        visible_texts.update(
            button.text()
            for button in dialog.findChildren(QAbstractButton)
            if button.text()
        )
        visible_texts.update(
            edit.placeholderText()
            for edit in dialog.findChildren(QLineEdit)
            if edit.placeholderText()
        )

        for fragment in (
            "Trust/privacy mode",
            "Wheel-scroll text bubble",
            "Snap bubble scroll back while speaking",
            "Elaborate prompt",
            "App language",
            "Assistant language",
            "Icon size (px)",
            "Text bubble width (px)",
            "Text bubble lines",
            "Text bubble font size (pt)",
        ):
            assert not any(fragment in text for text in visible_texts)

        assert "\u4fe1\u4efb\uff0f\u96b1\u79c1\u6a21\u5f0f" in visible_texts
        assert "\u5141\u8a31\u6efe\u8f2a\u6372\u52d5\u6587\u5b57\u6c23\u6ce1" in visible_texts
        assert "\u6717\u8b80\u6642\u81ea\u52d5\u6372\u56de\u76ee\u524d\u4f4d\u7f6e" in visible_texts
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_status_label_setters_localize_dynamic_text():
    """Verify status label setters localize dynamic text behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    import config
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    label = QLabel()
    dialog = SettingsDialog.__new__(SettingsDialog)

    try:
        SettingsDialog._set_status_label(dialog, label, None, "Not configured")
        assert label.text() == "\u672a\u8a2d\u5b9a"

        SettingsDialog._set_test_status(dialog, label, False, "Error reading status: keychain")
        assert label.text().startswith("\u8b80\u53d6\u72c0\u614b\u6642\u767c\u751f\u932f\u8aa4")
        assert "keychain" in label.text()
    finally:
        config.APP_LANGUAGE = old_language
        label.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_elaborate_prompt_field_follows_auto_elaborate_checkbox():
    """Verify elaborate prompt is only shown when auto-elaborate is enabled."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import config
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    dialog = SettingsDialog()

    try:
        checkbox = dialog._fields["CHAT_AUTO_ELABORATE"]
        field = dialog._fields["CHAT_ELABORATE_PROMPT"]
        label = dialog._chat_elaborate_prompt_label

        checkbox.setChecked(False)
        assert field.isHidden()
        assert label is not None and label.isHidden()

        checkbox.setChecked(True)
        assert not field.isHidden()
        assert label is not None and not label.isHidden()

        checkbox.setChecked(False)
        assert field.isHidden()
        assert label is not None and label.isHidden()
    finally:
        config.APP_LANGUAGE = old_language
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_llm_tab_hides_advanced_chat_harness_controls_until_expanded():
    """Verify chat harness settings live in a closed LLM Advanced drawer."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import config
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = ""
    dialog = SettingsDialog()

    try:
        dialog.show()
        dialog._tabs.setCurrentIndex(dialog._tab_base_names.index("LLM"))
        app.processEvents()

        for key in (
            "CHAT_TOOL_TRACE_UI",
            "WISP_PLANNED_CHUNKING",
            "WISP_PLANNED_CHUNKING_CHUNKS",
            "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
            "CHAT_REASONING_EFFORT",
            "CHAT_AUTO_ELABORATE",
            "CHAT_ELABORATE_PROMPT",
        ):
            assert key in dialog._fields

        current_tab = dialog._tabs.currentWidget()
        buttons = [button for button in current_tab.findChildren(QPushButton) if button.text() == "Advanced settings"]
        assert buttons
        advanced_button = buttons[0]
        assert advanced_button.isCheckable()
        assert not advanced_button.isChecked()
        assert not dialog._fields["CHAT_REASONING_EFFORT"].isVisible()

        advanced_button.setChecked(True)
        app.processEvents()

        assert dialog._fields["CHAT_REASONING_EFFORT"].isVisible()
    finally:
        config.APP_LANGUAGE = old_language
        dialog.deleteLater()
        app.processEvents()


def test_settings_open_requests_are_coalesced(monkeypatch):
    """Verify settings open requests are coalesced behavior."""
    from ui.settings_panel import dialog as settings_dialog

    callbacks = []

    def fake_single_shot(_delay, callback):
        """Verify fake single shot behavior."""
        callbacks.append(callback)

    def fake_open_now(**_kwargs):
        """Verify fake open now behavior."""
        settings_dialog._settings_open_pending = False

    monkeypatch.setattr(settings_dialog.QTimer, "singleShot", fake_single_shot)
    monkeypatch.setattr(settings_dialog, "_open_settings_now", fake_open_now)
    settings_dialog._settings_open_pending = False

    try:
        settings_dialog.open_settings(parent=None, on_apply=None)
        settings_dialog.open_settings(parent=None, on_apply=None)
        assert len(callbacks) == 1

        callbacks.pop()()
        settings_dialog.open_settings(parent=None, on_apply=None)
        assert len(callbacks) == 1
    finally:
        settings_dialog._settings_open_pending = False


def test_hidden_settings_dialog_is_replaced_without_clearing_new_one(monkeypatch):
    """Verify hidden settings dialog is replaced without clearing new one behavior."""
    from ui.settings_panel import dialog as settings_dialog

    class FakeSignal:
        """Test case for fake signal behavior."""
        def __init__(self) -> None:
            """Initialize the fake signal instance."""
            self._callbacks = []

        def connect(self, callback) -> None:
            """Verify connect behavior."""
            self._callbacks.append(callback)

        def emit(self, obj=None) -> None:
            """Verify emit behavior."""
            for callback in list(self._callbacks):
                callback(obj)

    class FakeDialog:
        """Qt dialog for fake dialog."""
        created = []

        def __init__(self, parent=None, on_apply=None, on_setup_check=None, extra_tools=None) -> None:
            """Initialize the fake dialog instance."""
            self.parent = parent
            self._on_apply = on_apply
            self._on_setup_check = on_setup_check
            self._extra_tools = extra_tools or []
            self._disposing = False
            self.visible = True
            self.deleted = False
            self.destroyed = FakeSignal()
            FakeDialog.created.append(self)

        def objectName(self):
            """Verify object name behavior."""
            return "fake-settings"

        def isVisible(self):
            """Verify is visible behavior."""
            return self.visible

        def isMinimized(self):
            """Verify is minimized behavior."""
            return False

        def showNormal(self):
            """Verify show normal behavior."""
            pass

        def show(self):
            """Verify show behavior."""
            self.visible = True

        def raise_(self):
            """Verify raise behavior."""
            pass

        def activateWindow(self):
            """Verify activate window behavior."""
            pass

        def deleteLater(self):
            """Verify delete later behavior."""
            self.deleted = True

    old = FakeDialog()
    old.visible = False
    new_dialogs = []

    def make_dialog(parent=None, on_apply=None, on_setup_check=None, extra_tools=None):
        """Verify make dialog behavior."""
        dialog = FakeDialog(
            parent=parent,
            on_apply=on_apply,
            on_setup_check=on_setup_check,
            extra_tools=extra_tools,
        )
        new_dialogs.append(dialog)
        return dialog

    monkeypatch.setattr(settings_dialog, "SettingsDialog", make_dialog)
    settings_dialog._settings_dialog = old
    settings_dialog._settings_open_pending = True

    try:
        settings_dialog._open_settings_now(parent=None, on_apply=lambda: None)

        assert old.deleted is True
        assert old._disposing is True
        assert new_dialogs
        assert settings_dialog._settings_dialog is new_dialogs[-1]

        old.destroyed.emit(old)
        assert settings_dialog._settings_dialog is new_dialogs[-1]
    finally:
        settings_dialog._settings_dialog = None
        settings_dialog._settings_open_pending = False
        FakeDialog.created.clear()


def test_cancel_status_refresh_invalidates_pending_results():
    """Verify cancel status refresh invalidates pending results behavior."""
    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        """Test case for fake timer behavior."""
        def __init__(self) -> None:
            """Initialize the fake timer instance."""
            self.stopped = False

        def isActive(self):
            """Verify is active behavior."""
            return True

        def stop(self):
            """Verify stop behavior."""
            self.stopped = True

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._status_refresh_token = 3
    dialog._status_refresh_running = True
    dialog._pending_status_results = [(3, "_copilot_status_lbl", None, "Checking status...")]
    dialog._pending_status_results_lock = threading.Lock()
    dialog._status_result_timer = FakeTimer()

    SettingsDialog._cancel_status_refresh(dialog)

    assert dialog._status_refresh_token == 4
    assert dialog._status_refresh_running is False
    assert dialog._pending_status_results == []
    assert dialog._status_result_timer.stopped is True


def test_cancel_async_ui_updates_stops_test_and_auth_timers():
    """Verify cancel async ui updates stops test and auth timers behavior."""
    from ui.settings_panel.dialog import SettingsDialog

    class FakeTimer:
        """Test case for fake timer behavior."""
        def __init__(self) -> None:
            """Initialize the fake timer instance."""
            self.stopped = False

        def isActive(self):
            """Verify is active behavior."""
            return True

        def stop(self):
            """Verify stop behavior."""
            self.stopped = True

    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._status_refresh_token = 0
    dialog._status_refresh_running = True
    dialog._pending_status_results = [(0, "_status", None, "Checking status...")]
    dialog._pending_status_results_lock = threading.Lock()
    dialog._status_result_timer = FakeTimer()
    dialog._running_test_tokens = {("llm_test", 1)}
    dialog._latest_test_token = {"llm_test": 1}
    dialog._pending_test_results = [("llm_test", 1, True, "OK")]
    dialog._pending_test_results_lock = threading.Lock()
    dialog._pending_test_progress = [("llm_test", 1, "Testing...")]
    dialog._pending_test_progress_lock = threading.Lock()
    dialog._test_result_timer = FakeTimer()
    dialog._auth_poll_timer = FakeTimer()
    dialog._github_auth_poll_timer = FakeTimer()

    SettingsDialog._cancel_async_ui_updates(dialog)

    assert dialog._status_refresh_running is False
    assert dialog._pending_status_results == []
    assert dialog._status_result_timer.stopped is True
    assert dialog._running_test_tokens == set()
    assert dialog._latest_test_token == {}
    assert dialog._pending_test_results == []
    assert dialog._pending_test_progress == []
    assert dialog._test_result_timer.stopped is True
    assert dialog._auth_poll_timer.stopped is True
    assert dialog._github_auth_poll_timer.stopped is True


def test_ui_host_skips_direct_stt_reset_thread(monkeypatch):
    """Verify ui host skips direct stt reset thread behavior."""
    from ui.settings_panel.dialog import SettingsDialog

    def fail_thread_start(*_args, **_kwargs):
        """Verify fail thread start behavior."""
        raise AssertionError("UI host must not start an STT reset thread")

    monkeypatch.setenv("WISP_MACOS_PY_UI_HOST", "1")
    monkeypatch.setattr(threading, "Thread", fail_thread_start)

    SettingsDialog._reset_stt_model_in_background()


def test_reset_page_key_mapping_is_scoped():
    """Verify reset page key mapping is scoped behavior."""
    from ui.settings_panel.dialog import SettingsDialog

    env = {
        "LLM_PROVIDER": "anthropic",
        "GROQ_API_KEY": "secret",
        "CALLER_COUNT": "3",
        "CALLER_1_HOTKEY": "ctrl+q",
        "CALLER_2_CONTEXT_MEMORY_MODE": "model",
        "BUBBLE_WIDTH": "420",
        "BUBBLE_FONT_SIZE": "14",
        "BUBBLE_SCROLL_ENABLED": "False",
        "BUBBLE_SCROLL_SNAP_DELAY_MS": "2200",
        "MEMORY_TOP_K": "7",
        "STT_MODEL": "small",
        "APP_LANGUAGE": "zh",
        "ASSISTANT_LANGUAGE": "Chinese",
        "CHAT_TOOL_TRACE_UI": "True",
        "WISP_PLANNED_CHUNKING": "True",
        "WISP_PLANNED_CHUNKING_CHUNKS": "3",
        "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS": "80",
        "CHAT_REASONING_EFFORT": "high",
        "CHAT_AUTO_ELABORATE": "True",
        "CHAT_ELABORATE_PROMPT": "Please elaborate.",
    }

    assert SettingsDialog._reset_env_keys_for_page("LLM", env) >= {
        "LLM_PROVIDER",
        "CHAT_TOOL_TRACE_UI",
        "WISP_PLANNED_CHUNKING",
        "WISP_PLANNED_CHUNKING_CHUNKS",
        "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
        "CHAT_REASONING_EFFORT",
        "CHAT_AUTO_ELABORATE",
        "CHAT_ELABORATE_PROMPT",
    }
    assert "GROQ_API_KEY" not in SettingsDialog._reset_env_keys_for_page("LLM", env)
    assert SettingsDialog._reset_env_keys_for_page("Keybinds", env) >= {
        "CALLER_COUNT",
        "CALLER_1_HOTKEY",
        "CALLER_2_CONTEXT_MEMORY_MODE",
        "HOTKEY_SNIP",
    }
    assert "BUBBLE_WIDTH" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_FONT_SIZE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_SCROLL_ENABLED" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_SCROLL_SNAP_DELAY_MS" in SettingsDialog._reset_env_keys_for_page("Advanced", env)
    assert "START_ON_LOGIN" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "APP_LANGUAGE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "ASSISTANT_LANGUAGE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "CHAT_AUTO_ELABORATE" not in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "MEMORY_TOP_K" in SettingsDialog._reset_env_keys_for_page("Advanced", env)
    assert "STT_MODEL" in SettingsDialog._reset_env_keys_for_page("TTS / Voice", env)
    assert SettingsDialog._reset_env_keys_for_page("Tools", env) == set()


def test_settings_tab_strip_uses_theme_background():
    """Verify settings tab strip uses theme background behavior."""
    from ui.settings_panel.dialog import SettingsDialog
    from ui.shared.theme import theme_colors

    colors = theme_colors(True)
    style = SettingsDialog._dialog_style(True)

    assert "QTabWidget#settingsTabs" in style
    assert "QTabWidget#settingsTabs::tab-bar" in style
    assert "QTabBar#settingsTabBar" in style
    assert "QWidget#wispWindowContent" in style
    assert f"background: {colors['bg']};" in style
    assert "QTabBar { background: transparent" not in style
    assert "QWidget { background-color: transparent" not in style


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_tab_bar_has_explicit_painted_backing():
    """Verify settings tab bar has explicit painted backing behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        bar = dialog._tabs.tabBar()
        assert dialog._tabs.objectName() == "settingsTabs"
        assert dialog._tabs.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert bar.objectName() == "settingsTabBar"
        assert bar.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert bar.drawBase() is False
        assert bar.expanding() is True
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_tabs_use_app_first_order_without_memory_tab():
    """Verify settings tab order starts with App and folds Memory into Advanced."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        assert dialog._tab_base_names == [
            "App",
            "LLM",
            "TTS / Voice",
            "Keybinds",
            "Prompts",
            "Advanced",
        ]
        advanced_tab = dialog._tabs.widget(dialog._tab_base_names.index("Advanced"))
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_TOP_K"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_AUTO_CONSOLIDATE"])
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_provider_is_model_route_and_api_key_table_option():
    """Verify custom provider is available for both model routes and API key rows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        options = dialog._get_api_key_display_options()
        assert "custom" in {provider for _label, provider in options}

        for rows in dialog._model_section_rows.values():
            for row in rows:
                assert row["api_key_combo"].findData("custom") >= 0

        dialog._fields["CUSTOM_API_KEY"].setText("not-a-real-key")
        assert dialog._effective_secret_value_from_provider("custom") == "not-a-real-key"

        api_key_row = dialog._add_api_key_row("custom")
        assert api_key_row["provider"].findData("custom") >= 0
        assert api_key_row["provider"].currentData() == "custom"
        assert "custom endpoint" in api_key_row["key"].placeholderText()
        assert "Test custom" not in {button.text() for button in dialog.findChildren(QPushButton)}
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_copilot_is_api_key_provider_option(monkeypatch):
    """Verify Copilot can be entered through the API key provider list."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core.auth import copilot_auth
    from ui.settings_panel.dialog import SettingsDialog

    monkeypatch.setattr(copilot_auth, "token_status", lambda: (False, "Not configured"))

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        api_key_row = dialog._add_api_key_row("copilot")

        assert api_key_row["provider"].findData("copilot") >= 0
        assert api_key_row["provider"].currentData() == "copilot"
        assert "github_pat_" in api_key_row["key"].placeholderText()
        assert dialog._copilot_status_lbl is not None
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_zai_is_api_key_and_model_route_option():
    """Verify native OpenAI-compatible providers are available in Settings provider lists."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    provider_ids = {
        "zai",
        "nvidia",
        "sambanova",
        "github_models",
        "huggingface",
        "chutes",
        "vercel",
        "fireworks",
        "cohere",
        "ai21",
        "nebius",
    }

    try:
        for provider in provider_ids:
            api_key_row = dialog._add_api_key_row(provider)
            assert api_key_row["provider"].findData(provider) >= 0
            assert api_key_row["provider"].currentData() == provider

        options = dialog._get_api_key_display_options()
        assert provider_ids <= {provider for _label, provider in options}

        model_values = []
        for rows in dialog._model_section_rows.values():
            for row in rows:
                for provider in provider_ids:
                    idx = row["api_key_combo"].findData(provider)
                    if idx >= 0:
                        model_values.append(row["api_key_combo"].itemData(idx))
        assert provider_ids <= set(model_values)
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_copilot_api_key_row_saves_through_copilot_auth(monkeypatch):
    """Verify Copilot API key rows use the Copilot keychain helper."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from core import secret_store
    from core.auth import copilot_auth
    from ui.settings_panel.dialog import SettingsDialog

    saved_tokens = []
    monkeypatch.setattr(copilot_auth, "token_status", lambda: (False, "Not configured"))
    monkeypatch.setattr(copilot_auth, "save_token", lambda token: saved_tokens.append(token))
    monkeypatch.setattr(secret_store, "migrate_env_secrets", lambda _env: None)

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        api_key_row = dialog._add_api_key_row("copilot")
        api_key_row["key"].setText("github_pat_test")

        assert dialog._save_api_keys_to_keychain() is True

        assert saved_tokens == ["github_pat_test"]
        assert api_key_row["key"].text() == ""
        assert api_key_row["key"].placeholderText() == "stored in keychain"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_ollama_custom_preset_fills_dummy_api_key():
    """Verify Ollama custom preset fills the local dummy API key."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._fields["CUSTOM_API_KEY"].clear()
        dialog._apply_custom_preset("http://localhost:11434/v1", "llama3", "ollama")

        assert dialog._fields["CUSTOM_BASE_URL"].text() == "http://localhost:11434/v1"
        assert dialog._fields["CUSTOM_API_KEY"].text() == "ollama"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tool_warning_marks_associated_settings_headlines():
    """Verify tool warning marks associated settings headlines behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        if not dialog._caller_blocks:
            dialog._add_caller_block()
        caller = dialog._caller_blocks[0]
        browser_mode = caller["context_browser_mode"]
        browser_mode.setCurrentIndex(browser_mode.findData("model"))

        warnings, targets = dialog._capability_warnings_for_values(
            {
                "LLM_PROVIDER": "chatgpt",
                "LLM_MODEL": "gpt-5.5",
                "VISION_LLM_PROVIDER": "anthropic",
                "VISION_LLM_MODEL": "claude-sonnet-4-5",
            }
        )
        assert warnings
        assert "LLM" in targets
        assert "Caller Hotkeys" in targets

        dialog._set_warning_markers(targets)

        assert dialog._warning_headers["LLM"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["Caller Hotkeys"].text().startswith("\u26a0 ")
        assert "ChatGPT" in dialog._warning_headers["LLM"].toolTip()

        dialog._set_warning_markers({})
        assert not dialog._warning_headers["LLM"].text().startswith("\u26a0 ")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_llm_tab_groups_credentials_and_models_under_full_height_rails():
    """Verify llm tab groups credentials and models under full height rails behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFrame, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        groups = [
            widget
            for widget in dialog.findChildren(QWidget)
            if widget.objectName() == "settingsAreaGroup"
        ]
        assert len(groups) >= 2
        assert "Provider credentials" in dialog._warning_headers
        assert "Model routing" in dialog._warning_headers

        first_group_headers = {
            dialog._warning_header_base_texts.get(key)
            for key in ("Provider credentials", "Authentication")
        }
        assert first_group_headers == {"Provider credentials", "Authentication"}
        assert groups[0].findChildren(QFrame, "areaAccentLine")
        assert groups[1].findChildren(QFrame, "areaAccentLine")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_subscription_warning_marks_provider_credentials_headline():
    """Verify subscription warning marks provider credentials headline behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        warnings, targets = dialog._capability_warnings_for_values(
            {
                "LLM_PROVIDER": "chatgpt",
                "LLM_MODEL": "gpt-5.5",
                "VISION_LLM_PROVIDER": "anthropic",
                "VISION_LLM_MODEL": "claude-sonnet-4-5",
            }
        )
        assert warnings
        assert any("sign in again" in warning for warning in warnings)
        assert "Provider credentials" in targets

        dialog._set_warning_markers(targets)

        assert dialog._warning_headers["Provider credentials"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["Provider credentials"].toolTip()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_provider_timing_notice_only_for_providers_without_word_timestamps():
    """Verify TTS timing notice appears only when word sync is approximate."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog, _TTS_TIMING_NOTICE, _get, _set

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        notice = dialog.findChild(QLabel, "ttsTimingNotice")
        assert notice is not None

        for provider in ("elevenlabs", "openai", "openai_compatible", "gpt_sovits", "kokoro"):
            _set(dialog._fields["TTS_PROVIDER"], provider)
            dialog._update_tts_provider_fields()
            assert _get(dialog._fields["TTS_PROVIDER"]) == provider
            assert not notice.isHidden()
            assert notice.text() == t(_TTS_TIMING_NOTICE)

        for provider in ("cartesia", "none"):
            _set(dialog._fields["TTS_PROVIDER"], provider)
            dialog._update_tts_provider_fields()
            assert _get(dialog._fields["TTS_PROVIDER"]) == provider
            assert notice.isHidden()
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_warning_markers_refresh_from_loaded_settings():
    """Verify warning markers refresh from loaded settings behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._env.update(
            {
                "LLM_PROVIDER": "chatgpt",
                "LLM_MODEL": "gpt-5.5",
                "VISION_LLM_PROVIDER": "anthropic",
                "VISION_LLM_MODEL": "claude-sonnet-4-5",
                "CALLER_COUNT": "1",
                "CALLER_1_CONTEXT_BROWSER_MODE": "model",
                "CALLER_1_CONTEXT_DOCUMENTS_MODE": "off",
                "CALLER_1_CONTEXT_GITHUB_MODE": "off",
                "CALLER_1_CONTEXT_MEMORY_MODE": "off",
                "CALLER_1_CONTEXT_SCREENSHOT": "off",
                "VOICE_CONTEXT_BROWSER_MODE": "off",
                "VOICE_CONTEXT_DOCUMENTS_MODE": "off",
                "VOICE_CONTEXT_GITHUB_MODE": "off",
                "VOICE_CONTEXT_MEMORY_MODE": "off",
                "VOICE_CONTEXT_SCREENSHOT": "off",
            }
        )

        dialog._load_values()

        assert dialog._warning_headers["Provider credentials"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["LLM"].text().startswith("\u26a0 ")
        assert dialog._warning_headers["Caller Hotkeys"].text().startswith("\u26a0 ")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_footer_apply_stays_open_and_confirm_closes():
    """Verify settings footer apply stays open and confirm closes behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        confirm_btn = dialog.findChild(QPushButton, "settingsConfirmButton")
        assert apply_btn is not None
        assert confirm_btn is not None
        assert apply_btn.text() in {"Apply", "应用", "應用", "Aplicar", "Appliquer"}
        assert confirm_btn.text() in {"Confirm", "确认", "確認", "Confirmar", "Confirmer"}
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        assert not {"Cancel", "取消", "Cancelar", "Annuler"} & button_texts

        calls = []
        dialog._apply_settings = lambda: calls.append("saved") or True

        dialog._apply()
        assert calls == ["saved"]
        assert dialog.result() == int(QDialog.DialogCode.Rejected)

        # Nothing edited since Apply, so Confirm just closes without re-applying:
        # no redundant config reload / model-reconnect "loading" pass.
        dialog._confirm()
        assert calls == ["saved"]
        assert dialog.result() == int(QDialog.DialogCode.Accepted)
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_confirm_applies_when_there_are_unsaved_changes():
    """Confirm still saves+applies when the user edited something (the common path)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QDialog

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        calls = []
        dialog._apply_settings = lambda: calls.append("saved") or True

        # Make a real edit so the dialog is dirty.
        box = dialog._fields["CHAT_TOOL_TRACE_UI"]
        assert isinstance(box, QCheckBox)
        box.setChecked(not box.isChecked())

        dialog._confirm()
        assert calls == ["saved"]
        assert dialog.result() == int(QDialog.DialogCode.Accepted)
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_has_reset_page_button():
    """Verify settings has reset page button behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
        assert {
            "Reset Page…",
            "重置本页…",
            "重置本頁…",
            "Restablecer página…",
            "Réinitialiser la page…",
        } & button_texts
        assert {
            "Reset All…",
            "全部重置…",
            "全部重置…",
            "Restablecer todo…",
            "Tout réinitialiser…",
        } & button_texts
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_search_filters_to_matching_page():
    """Verify settings search filters to matching page behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLineEdit

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        search = dialog.findChild(QLineEdit, "settingsSearch")
        assert search is not None

        search.setText("tool_file_roots")
        app.processEvents()

        tabs = dialog._tabs
        visible_pages = {
            dialog._tab_base_names[i]
            for i in range(tabs.count())
            if tabs.isTabVisible(i)
        }
        assert visible_pages == {"Advanced"}

        search.clear()
        app.processEvents()
        assert all(tabs.isTabVisible(i) for i in range(tabs.count()))
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_dirty_marker_enables_apply_after_change():
    """Verify settings dirty marker enables apply after change behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None
        assert not apply_btn.isEnabled()

        dialog._fields["CHAT_ELABORATE_PROMPT"].setText("Please add more detail.")
        app.processEvents()

        assert apply_btn.isEnabled()
        llm_index = dialog._tab_base_names.index("LLM")
        assert dialog._tabs.tabText(llm_index).endswith("*")
        assert dialog._fields["CHAT_ELABORATE_PROMPT"].property("dirty") is True
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_apply_clears_dirty_before_showing_save_warning(monkeypatch):
    """Successful Apply should clear dirty state before non-fatal warnings appear."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel.dialog import SettingsDialog, _get, _set
    from ui.shared import theme

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None

        combo = dialog._fields["ASSISTANT_LANGUAGE"]
        new_language = "Chinese (Traditional)" if _get(combo) != "Chinese (Traditional)" else "English"
        _set(combo, new_language)
        app.processEvents()
        assert apply_btn.isEnabled()

        def fake_save():
            row = dialog._caller_blocks[0]["intent_rows"][0]
            row["label"].setText("\u9019\u662f\u4ec0\u9ebc\uff1f")
            _set(row["prompt"], "\u9019\u662f\u4ec0\u9ebc\uff1f\u8acb\u7528\u7e41\u9ad4\u4e2d\u6587\u89e3\u91cb\u3002")
            dialog._last_save_warnings = ["Non-fatal model capability warning."]
            return True

        opened = []

        def fake_open(message_box):
            opened.append({
                "dirty_keys": set(dialog._dirty_keys),
                "apply_enabled": apply_btn.isEnabled(),
                "status": dialog._status_lbl.text(),
                "text": message_box.text(),
            })

        dialog._do_save = fake_save
        monkeypatch.setattr(config, "reload", lambda: None)
        monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
        monkeypatch.setattr(tts, "reset_connections", lambda: None)
        monkeypatch.setattr(theme, "apply_app_theme", lambda: None)
        monkeypatch.setattr(settings_dialog, "_read_env", lambda: {"ASSISTANT_LANGUAGE": new_language})
        monkeypatch.setattr(QMessageBox, "open", fake_open)

        assert dialog._apply_settings() is True
        app.processEvents()

        assert opened
        assert opened[0]["dirty_keys"] == set()
        assert opened[0]["apply_enabled"] is False
        assert not opened[0]["status"].startswith("Unsaved changes")
        assert dialog._dirty_keys == set()
        assert apply_btn.isEnabled() is False
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_do_save_localizes_qtextedit_prompt_fields(monkeypatch):
    """The real save path should localize WASD prompt QTextEdits without crashing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTextEdit

    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel.dialog import SettingsDialog, _get, _set

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        captured = {}
        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        monkeypatch.setattr(settings_dialog, "_write_env", lambda vals, remove_keys=None: captured.update(vals))

        for index, block in enumerate(dialog._caller_blocks, 1):
            block["hotkey"].setText(f"ctrl+alt+{index}")
        for index, key in enumerate(("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE", "HOTKEY_DICTATE"), 1):
            dialog._fields[key].setText(f"ctrl+shift+alt+{index}")

        _set(dialog._fields["ASSISTANT_LANGUAGE"], "Chinese (Traditional)")
        _set(dialog._fields["CHAT_ELABORATE_PROMPT"], "Please elaborate on that.")
        dialog._fields["WISP_PLANNED_CHUNKING"].setChecked(True)
        dialog._fields["WISP_PLANNED_CHUNKING_CHUNKS"].setText("4")
        dialog._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setText("120")
        row = dialog._caller_blocks[0]["intent_rows"][0]
        assert isinstance(row["prompt"], QTextEdit)
        assert not row["prompt"].acceptRichText()
        row["key"].setText("w")
        row["label"].setText("What is this?")
        _set(row["prompt"], "What is this? Give me a clear, plain-English explanation in 2-3 sentences.")

        assert dialog._do_save() is True
        assert captured["CALLER_1_INTENT_1_LABEL"] == "\u9019\u662f\u4ec0\u9ebc\uff1f"
        assert "\u7e41\u9ad4\u4e2d\u6587" in captured["CALLER_1_INTENT_1_PROMPT"]
        assert _get(row["prompt"]) == captured["CALLER_1_INTENT_1_PROMPT"]
        assert captured["CHAT_ELABORATE_PROMPT"] == "\u8acb\u8a73\u7d30\u8aaa\u660e\u4e00\u4e0b\u3002"
        assert _get(dialog._fields["CHAT_ELABORATE_PROMPT"]) == captured["CHAT_ELABORATE_PROMPT"]
        assert captured["WISP_PLANNED_CHUNKING"] == "True"
        assert captured["WISP_PLANNED_CHUNKING_CHUNKS"] == "4"
        assert captured["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"] == "120"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_apply_real_save_clears_dirty_after_language_change(monkeypatch):
    """Changing Assistant language should save, localize prompts, and clear dirty state."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    import config
    from core import tts
    from core.llm_clients import client as llm_client
    from ui.settings_panel import dialog as settings_dialog
    from ui.settings_panel.dialog import SettingsDialog, _set
    from ui.shared import theme

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None

        captured = {}
        monkeypatch.setattr(dialog, "_save_api_keys_to_keychain", lambda: True)
        monkeypatch.setattr(dialog, "_capability_warnings_for_values", lambda _vals: ([], {}))
        monkeypatch.setattr(dialog, "_set_warning_markers", lambda _warnings: None)
        monkeypatch.setattr(settings_dialog, "_write_env", lambda vals, remove_keys=None: captured.update(vals))
        monkeypatch.setattr(settings_dialog, "_read_env", lambda: dict(captured))
        monkeypatch.setattr(config, "reload", lambda: None)
        monkeypatch.setattr(llm_client, "reset_clients", lambda: None)
        monkeypatch.setattr(tts, "reset_connections", lambda: None)
        monkeypatch.setattr(theme, "apply_app_theme", lambda: None)

        for index, block in enumerate(dialog._caller_blocks, 1):
            block["hotkey"].setText(f"ctrl+alt+{index}")
        for index, key in enumerate(("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE", "HOTKEY_DICTATE"), 1):
            dialog._fields[key].setText(f"ctrl+shift+alt+{index}")

        _set(dialog._fields["ASSISTANT_LANGUAGE"], "Chinese (Traditional)")
        row = dialog._caller_blocks[0]["intent_rows"][0]
        row["key"].setText("w")
        row["label"].setText("What is this?")
        _set(row["prompt"], "What is this? Give me a clear, plain-English explanation in 2-3 sentences.")
        app.processEvents()

        assert apply_btn.isEnabled()
        assert dialog._apply_settings() is True
        app.processEvents()

        assert "\u7e41\u9ad4\u4e2d\u6587" in captured["CALLER_1_INTENT_1_PROMPT"]
        assert dialog._dirty_keys == set()
        assert apply_btn.isEnabled() is False
        assert not dialog._status_lbl.text().startswith("Unsaved changes")
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_apply_reports_unexpected_save_exception(monkeypatch):
    """Confirm/Apply failures should show a visible reason instead of doing nothing."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        warnings = []

        def fail_save():
            raise RuntimeError("prompt field update failed")

        def capture_warning(_parent, title, message):
            warnings.append((title, message))
            return QMessageBox.StandardButton.Ok

        dialog._do_save = fail_save
        monkeypatch.setattr(QMessageBox, "warning", capture_warning)

        assert dialog._apply_settings() is False
        assert warnings
        assert warnings[0][0]
        assert "prompt field update failed" in warnings[0][1]
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_preset_marks_reviewable_changes_without_saving():
    """Verify settings preset marks reviewable changes without saving behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog, _get

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        apply_btn = dialog.findChild(QPushButton, "settingsApplyButton")
        assert apply_btn is not None

        dialog._apply_preset("Low setup")
        app.processEvents()

        assert apply_btn.isEnabled()
        assert _get(dialog._fields["STT_MODEL"]) == "base"
        assert _get(dialog._fields["CONTEXT_BROWSER_MAX_CHARS"]) == "3000"
        assert dialog._active_preset_slug == "low_setup"
        assert {"LLM", "TTS / Voice", "Advanced", "Keybinds"} <= dialog._tab_dirty_names
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_low_setup_preset_uses_chatgpt_oauth_routes():
    """Verify low setup preset only needs ChatGPT OAuth for model routes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._apply_preset("Low setup")
        app.processEvents()

        assert dialog._active_preset_slug == "low_setup"
        for section in ("LLM", "VISION_LLM", "MEMORY_LLM"):
            rows = dialog._model_section_rows[section]
            assert len(rows) == 1
            assert rows[0]["api_key_combo"].currentData() == "chatgpt"
            assert dialog._model_value(rows[0]) == "gpt-5.5"

        assert dialog._voice_block["context_browser_mode"].currentData() == "off"
        assert dialog._voice_block["context_github_mode"].currentData() == "off"
        assert dialog._voice_block["context_screenshot"].currentData() == "off"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_profiles_menu_only_exposes_low_setup_builtin_preset():
    """Verify old behavior presets are not shown as built-in Settings presets."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    menu = None

    try:
        menu = dialog._build_profiles_menu(dialog)
        action_texts = [action.text() for action in menu.actions()]

        assert "Low setup" in action_texts
        for old_preset in ("Fast", "Best quality", "Private/local", "Coding assistant", "Low cost"):
            assert old_preset not in action_texts
    finally:
        if menu is not None:
            menu.deleteLater()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_active_preset_persists_user_edits_as_preset_overrides():
    """Verify settings active preset persists user edits as preset overrides behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.dialog import SettingsDialog, _get

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        dialog._apply_preset("Low setup")
        _set_value = "7"
        dialog._fields["MEMORY_TOP_K"].setText(_set_value)
        vals = {"MEMORY_TOP_K": _get(dialog._fields["MEMORY_TOP_K"])}

        persisted = dialog._preset_values_to_persist(vals)

        assert persisted["WISP_SETTINGS_PRESET"] == "low_setup"
        assert persisted["WISP_PRESET_LOW_SETUP_MEMORY_TOP_K"] == _set_value
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_can_create_custom_profile(tmp_path, monkeypatch):
    """Verify Settings can create a real PROFILE_N custom profile."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QPushButton

    import ui.settings_panel.dialog as settings_dialog
    from ui.settings_panel import env as settings_env

    env_path = tmp_path / ".env"
    monkeypatch.setattr(settings_dialog, "ENV_PATH", env_path)
    monkeypatch.setattr(settings_env, "ENV_PATH", env_path)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Local Research", True))

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = settings_dialog.SettingsDialog()

    try:
        profile_btn = dialog.findChild(QPushButton, "settingsProfilesButton")
        assert profile_btn is not None
        assert profile_btn.text() == "Profiles..."

        dialog._fields["CONTEXT_BROWSER_MAX_CHARS"].setText("12345")
        dialog._create_custom_profile()

        saved = settings_env.read_settings_env()
        assert saved["PROFILE_COUNT"] == "1"
        assert saved["PROFILE_1_ID"] == "local-research"
        assert saved["PROFILE_1_LABEL"] == "Local Research"
        assert saved["PROFILE_1_CONTEXT_BROWSER_MAX_CHARS"] == "12345"
        assert "ACTIVE_PROFILE" not in saved
        assert "SETTINGS_PROFILE" not in saved
        assert dialog._pending_active_profile == "local-research"
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_advanced_tab_contains_tuning_and_memory_controls():
    """Verify settings advanced tab contains tuning and memory controls."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        advanced_tab = dialog._tabs.widget(dialog._tab_base_names.index("Advanced"))

        assert advanced_tab.isAncestorOf(dialog._fields["BUBBLE_REVEAL_WPM"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_TOP_K"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_STM_TOKEN_BUDGET"])
        assert advanced_tab.isAncestorOf(dialog._fields["CONTEXT_BROWSER_MAX_CHARS"])
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_caller_memory_context_block_uses_second_row():
    """Verify caller memory context block stays in the second context row."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QVBoxLayout, QWidget

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._caller_blocks = []
    dialog._callers_vlayout = QVBoxLayout(host)
    dialog._fields = {}

    try:
        SettingsDialog._add_caller_block(dialog, intents=[])
        frame = dialog._caller_blocks[0]["widget"]
        memory_pos = None
        for child in frame.findChildren(QWidget):
            labels = child.findChildren(QLabel)
            if not any(label.text() == t("Memory") for label in labels):
                continue
            parent = child.parentWidget()
            layout = parent.layout() if parent is not None else None
            if isinstance(layout, QGridLayout):
                idx = layout.indexOf(child)
                if idx >= 0:
                    memory_pos = layout.getItemPosition(idx)
                    break
            if memory_pos is not None:
                break

        assert memory_pos is not None
        assert memory_pos[:2] == (2, 1)
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_caller_custom_prompt_row_lives_with_intent_rows():
    """Verify caller custom prompt row lives with intent rows behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog, _get

    app = QApplication.instance() or QApplication(sys.argv)
    host = QWidget()
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._caller_blocks = []
    dialog._callers_vlayout = QVBoxLayout(host)
    dialog._fields = {}

    try:
        SettingsDialog._add_caller_block(
            dialog,
            custom_key="s",
            custom_label="Freeform",
            intents=[
                {"key": "w", "label": "Ask", "prompt": "Ask"},
                {"key": "a", "label": "Explain", "prompt": "Explain"},
                {"key": "d", "label": "Fix", "prompt": "Fix"},
            ],
        )
        blk = dialog._caller_blocks[0]
        custom_row = blk["custom_key"].parentWidget()

        assert _get(blk["custom_key"]) == "s"
        assert _get(blk["custom_label"]) == "Freeform"
        assert blk["custom_prompt"].isEnabled() is False
        assert blk["custom_prompt"].text() == t("Custom prompt")
        assert blk["intents_layout"].indexOf(custom_row) == 3
    finally:
        host.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_refresh_runs_on_background_thread(monkeypatch):
    """Verify memory panel refresh runs on background thread behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        """Coordinate fake manager behavior."""
        def get_all_facts(self):
            """Verify get all facts behavior."""
            raise AssertionError("refresh should not run on the UI thread")

    class FakeThread:
        """Test case for fake thread behavior."""
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            """Initialize the fake thread instance."""
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            """Verify start behavior."""
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel.refresh_facts()

        assert started
        assert started[0]["name"] == "wisp-memory-refresh"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_read_only_hides_mutation_controls():
    """Verify memory panel read only hides mutation controls behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.memory_viewer import MemoryPanel
    from ui.i18n import t

    app = QApplication.instance() or QApplication(sys.argv)

    class FakeManager:
        """Coordinate fake manager behavior."""
        def get_all_facts(self):
            """Verify get all facts behavior."""
            return []

    panel = MemoryPanel(
        FakeManager(),
        initial_facts=[
            {"id": "fact-1", "text": "I prefer stable settings", "category": "general"}
        ],
        read_only=True,
    )

    try:
        assert not hasattr(panel, "_add_text")
        button_texts = {button.text() for button in panel.findChildren(QPushButton)}
        assert "Add" not in button_texts
        assert "X" not in button_texts
        assert t("Refresh") in button_texts
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_memory_panel_add_runs_on_background_thread(monkeypatch):
    """Verify memory panel add runs on background thread behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import ui.memory_viewer as memory_viewer
    from ui.memory_viewer import MemoryPanel

    app = QApplication.instance() or QApplication(sys.argv)
    started: list[dict] = []

    class FakeManager:
        """Coordinate fake manager behavior."""
        def add_fact_manual(self, _text, category="general", project=None):
            """Verify add fact manual behavior."""
            raise AssertionError("add should not run on the UI thread")

    class FakeThread:
        """Test case for fake thread behavior."""
        def __init__(self, *, target, name: str, daemon: bool) -> None:
            """Initialize the fake thread instance."""
            started.append({"target": target, "name": name, "daemon": daemon, "started": False})

        def start(self) -> None:
            """Verify start behavior."""
            started[-1]["started"] = True

    monkeypatch.setattr(memory_viewer.threading, "Thread", FakeThread)
    panel = MemoryPanel(FakeManager(), initial_facts=[])

    try:
        panel._add_text.setText("I prefer fast settings")
        panel._on_add_fact()

        assert panel._add_text.text() == ""
        assert started
        assert started[0]["name"] == "wisp-memory-add"
        assert started[0]["daemon"] is True
        assert started[0]["started"] is True
    finally:
        panel.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_keybinds_has_voice_block_and_tools_buttons():
    """Verify settings keybinds has voice block and tools buttons behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()

    try:
        assert "HOTKEY_VOICE" in dialog._fields
        assert "HOTKEY_READ_SELECTION_ALOUD" in dialog._fields
        vb = dialog._voice_block
        assert set(vb) >= {
            "context_ambient",
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
            "context_screenshot",
            "tool_overrides",
        }
        sb = dialog._snip_block
        assert set(sb) >= {
            "context_ambient",
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
            "context_screenshot",
            "file_access",
            "tool_overrides",
        }
        assert sb["context_screenshot"].currentData() == "off"
        assert not sb["context_screenshot"].isEnabled()
        tools_buttons = [
            b
            for b in dialog.findChildren(QPushButton)
            if b.text() in {"Allowed tools…", "允许的工具…", "允許的工具…"}
        ]
        # One per caller block plus snip and voice.
        assert len(tools_buttons) == len(dialog._caller_blocks) + 2
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_settings_context_source_blocks_use_even_columns():
    """Verify caller context source blocks keep consistent tile widths."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFrame, QVBoxLayout, QWidget

    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog()
    host = QWidget()

    try:
        row, _controls = dialog._build_context_controls()
        layout = QVBoxLayout(host)
        layout.addWidget(row)
        host.resize(1800, 260)
        host.show()
        app.processEvents()

        frames = [
            frame
            for frame in row.findChildren(QFrame)
            if frame.objectName() == "contextSourceBlock"
        ]
        first_row_y = min(frame.y() for frame in frames)
        first_row_widths = [
            frame.width()
            for frame in frames
            if frame.y() == first_row_y
        ]

        assert len(first_row_widths) == 4
        assert max(first_row_widths) - min(first_row_widths) <= 1
        assert all(frame.minimumWidth() >= 160 for frame in frames)
        assert all(not frame.findChildren(QCheckBox) for frame in frames)
        assert all(len(frame.findChildren(QComboBox)) == 1 for frame in frames)
    finally:
        host.deleteLater()
        dialog.deleteLater()
        app.processEvents()


def test_reset_keybinds_page_includes_voice_keys():
    """Verify reset keybinds page includes voice keys behavior."""
    from ui.settings_panel.dialog import SettingsDialog

    env = {
        "VOICE_TOOLS": "x:on",
        "VOICE_CONTEXT_BROWSER_MODE": "model",
        "CALLER_1_TOOLS": "y:model",
    }
    keys = SettingsDialog._reset_env_keys_for_page("Keybinds", env)
    assert {
        "VOICE_TOOLS",
        "VOICE_CONTEXT_BROWSER_MODE",
        "CALLER_1_TOOLS",
        "HOTKEY_VOICE",
        "HOTKEY_READ_SELECTION_ALOUD",
    } <= keys


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tool_access_dialog_round_trips_overrides():
    """Verify tool access dialog only round trips non-context tool overrides."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.tool_access import ToolAccessDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = ToolAccessDialog(
        method_label="Test",
        overrides={"github_repo": "off", "read_file": "model"},
        governed_modes={
            "Open docs": "auto",
            "Browser/Web": "off",
            "Git/GitHub": "model",
            "Memory": "model",
            "Screenshot": "off",
            "Files": "read",
        },
        extra_tools=[
            {
                "name": "mcp_example_echo",
                "description": "[MCP:example] Echo back text.",
            }
        ],
    )

    try:
        combos = dlg._combos
        assert {
            "list_files", "read_file", "create_file", "edit_file", "write_file",
            "mcp_server.example", "mcp_example_echo",
        } <= set(combos)
        assert {
            "web_search", "get_context", "retrieve_website", "git_status", "git_diff",
            "github_repo", "github_issue", "memory_search", "capture_screen",
        }.isdisjoint(combos)
        assert combos["read_file"].currentData() == "on"
        assert combos["create_file"].currentData() == "off"
        assert combos["mcp_server.example"].currentData() == "on"
        assert combos["mcp_example_echo"].currentData() == ""

        # Selectors left matching their defaults store nothing; explicit
        # deviations round-trip. Hidden context-tool overrides are dropped.
        combos["edit_file"].setCurrentIndex(combos["edit_file"].findData("on"))
        combos["mcp_server.example"].setCurrentIndex(combos["mcp_server.example"].findData("off"))
        combos["mcp_example_echo"].setCurrentIndex(combos["mcp_example_echo"].findData("on"))
        result = dlg.selected_overrides()
        assert result["edit_file"] == "on"
        assert result["mcp_server.example"] == "off"
        assert result["mcp_example_echo"] == "on"
        assert "github_repo" not in result
    finally:
        dlg.deleteLater()
        app.processEvents()


def test_bubble_hide_delay_seconds_round_trip():
    """Verify bubble hide delay seconds round trip behavior."""
    from ui.settings_panel.dialog import _ms_to_seconds_str, _seconds_str_to_ms

    assert _ms_to_seconds_str("3500", 3500) == "3.5"
    assert _ms_to_seconds_str("8000", 3500) == "8"
    assert _ms_to_seconds_str("garbage", 3500) == "3.5"
    assert _seconds_str_to_ms("3.5", 3500) == "3500"
    assert _seconds_str_to_ms("8", 3500) == "8000"
    assert _seconds_str_to_ms("0.1", 3500) == "500"  # clamped to the 0.5s floor
    assert _seconds_str_to_ms("garbage", 3500) == "3500"
