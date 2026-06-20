"""Tests for test settings dialog controls."""

import os
import sys
import threading

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
    from PySide6.QtWidgets import QApplication, QLabel

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


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tts_voice_tab_exposes_stt_settings():
    """Verify tts voice tab exposes stt settings behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from ui.i18n import t
    from ui.settings_panel.dialog import SettingsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dialog = SettingsDialog.__new__(SettingsDialog)
    dialog._fields = {}
    tab = SettingsDialog._tab_tts(dialog)

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
        labels = {label.text() for label in tab.findChildren(QLabel)}
        checkboxes = {checkbox.text() for checkbox in tab.findChildren(QCheckBox)}
        assert "App language" in labels
        assert "Assistant language" in labels
        assert "Trust/privacy mode" in checkboxes
        app_values = {
            dialog._fields["APP_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["APP_LANGUAGE"].count())
        }
        assert {"", "en", "zh", "zh-Hant", "es", "fr"} <= app_values
        values = {
            dialog._fields["ASSISTANT_LANGUAGE"].itemData(i)
            for i in range(dialog._fields["ASSISTANT_LANGUAGE"].count())
        }
        assert {"", "match_user", "English", "Chinese", "Spanish"} <= values
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
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
        assert i18n.t("Browser/Web: ") == "\u700f\u89bd\u5668/\u7db2\u9801\uff1a"
        assert i18n.t("Browser/Web: On") == "Browser/Web: On"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)


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
        assert any("\u6c92\u6709<b>\u95dc\u9375\u5b57</b>\u7684\u5de5\u5177" in text for text in label_texts)
        assert "\u6a21\u578b\u8def\u7531" in label_texts
        assert "\u9078\u64c7\u6bcf\u500b\u7528\u9014\u4f7f\u7528\u54ea\u500b\u5df2\u5132\u5b58\u6191\u8b49\u548c\u6a21\u578b\u3002" in label_texts
        assert "<small><b>\u63d0\u4f9b\u8005</b></small>" in label_texts
        assert any(text.endswith("\u804a\u5929\u6a21\u578b") for text in label_texts)
        assert "\u6e2c\u8a66\u804a\u5929\u6a21\u578b" in button_texts
        assert "Codex\uff08ChatGPT \u8a02\u95b1\uff09[OAuth]" in combo_texts
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

        def __init__(self, parent=None, on_apply=None) -> None:
            """Initialize the fake dialog instance."""
            self.parent = parent
            self._on_apply = on_apply
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

    def make_dialog(parent=None, on_apply=None):
        """Verify make dialog behavior."""
        dialog = FakeDialog(parent=parent, on_apply=on_apply)
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
        "BUBBLE_SCROLL_ENABLED": "False",
        "BUBBLE_SCROLL_SNAP_DELAY_MS": "2200",
        "MEMORY_TOP_K": "7",
        "STT_MODEL": "small",
        "APP_LANGUAGE": "zh",
        "ASSISTANT_LANGUAGE": "Chinese",
    }

    assert SettingsDialog._reset_env_keys_for_page("LLM", env) >= {"LLM_PROVIDER"}
    assert "GROQ_API_KEY" not in SettingsDialog._reset_env_keys_for_page("LLM", env)
    assert SettingsDialog._reset_env_keys_for_page("Keybinds", env) >= {
        "CALLER_COUNT",
        "CALLER_1_HOTKEY",
        "CALLER_2_CONTEXT_MEMORY_MODE",
        "HOTKEY_SNIP",
    }
    assert "BUBBLE_WIDTH" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_SCROLL_ENABLED" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "BUBBLE_SCROLL_SNAP_DELAY_MS" in SettingsDialog._reset_env_keys_for_page("Advanced", env)
    assert "APP_LANGUAGE" in SettingsDialog._reset_env_keys_for_page("App", env)
    assert "ASSISTANT_LANGUAGE" in SettingsDialog._reset_env_keys_for_page("App", env)
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
            "Tools",
            "Advanced",
        ]
        advanced_tab = dialog._tabs.widget(dialog._tab_base_names.index("Advanced"))
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_TOP_K"])
        assert advanced_tab.isAncestorOf(dialog._fields["MEMORY_AUTO_CONSOLIDATE"])
    finally:
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_custom_provider_is_model_route_option_without_api_key_table_row():
    """Verify custom provider is model route option without api key table row behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

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

        api_key_row = dialog._add_api_key_row()
        assert api_key_row["provider"].findText("custom") == -1
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

        dialog._confirm()
        assert calls == ["saved", "saved"]
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
        app_index = dialog._tab_base_names.index("App")
        assert dialog._tabs.tabText(app_index).endswith("*")
        assert dialog._fields["CHAT_ELABORATE_PROMPT"].property("dirty") is True
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

        dialog._apply_preset("Low cost")
        app.processEvents()

        assert apply_btn.isEnabled()
        assert _get(dialog._fields["STT_MODEL"]) == "base"
        assert _get(dialog._fields["CONTEXT_BROWSER_MAX_CHARS"]) == "3000"
        assert dialog._active_preset_slug == "low_cost"
        assert {"TTS / Voice", "Advanced", "Keybinds"} <= dialog._tab_dirty_names
    finally:
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
        dialog._apply_preset("Fast")
        _set_value = "7"
        dialog._fields["MEMORY_TOP_K"].setText(_set_value)
        vals = {"MEMORY_TOP_K": _get(dialog._fields["MEMORY_TOP_K"])}

        persisted = dialog._preset_values_to_persist(vals)

        assert persisted["WISP_SETTINGS_PRESET"] == "fast"
        assert persisted["WISP_PRESET_FAST_MEMORY_TOP_K"] == _set_value
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
def test_caller_memory_combo_uses_third_column_second_row():
    """Verify caller memory combo uses third column second row behavior."""
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
            layout = child.layout()
            if not isinstance(layout, QGridLayout):
                continue
            for idx in range(layout.count()):
                item = layout.itemAt(idx)
                widget = item.widget()
                if isinstance(widget, QLabel) and widget.text() == t("Memory:"):
                    memory_pos = layout.getItemPosition(idx)
                    break
            if memory_pos is not None:
                break

        assert memory_pos is not None
        assert memory_pos[:2] == (1, 4)
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
        tools_buttons = [
            b
            for b in dialog.findChildren(QPushButton)
            if b.text() in {"Allowed tools…", "允许的工具…", "允許的工具…"}
        ]
        # One per caller block plus one for the voice block.
        assert len(tools_buttons) == len(dialog._caller_blocks) + 1
    finally:
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
    } <= keys


@pytest.mark.skipif(pytest.importorskip("PySide6", reason="PySide6 not installed") is None, reason="PySide6 not installed")
def test_tool_access_dialog_round_trips_overrides():
    """Verify tool access dialog round trips overrides behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ui.settings_panel.tool_access import ToolAccessDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = ToolAccessDialog(
        method_label="Test",
        overrides={"github_repo": "off"},
        governed_modes={
            "Open docs": "auto",
            "Browser/Web": "off",
            "Git/GitHub": "model",
            "Memory": "model",
            "Screenshot": "off",
            "Files": "read",
        },
    )

    try:
        combos = dlg._combos
        # Every context tool gets its own selector now.
        assert {
            "web_search", "get_context", "retrieve_website", "git_status", "git_diff",
            "github_repo", "github_issue", "memory_search", "capture_screen",
            "list_files", "read_file", "create_file", "edit_file", "write_file",
        } <= set(combos)
        # Defaults follow the dropdowns: Git/GitHub + Memory are "Let model
        # decide"; a stored override (github_repo: off) wins over that.
        assert combos["git_status"].currentData() == "model"
        assert combos["memory_search"].currentData() == "model"
        assert combos["github_repo"].currentData() == "off"
        assert combos["web_search"].currentData() == "off"
        assert combos["read_file"].currentData() == "on"
        assert combos["create_file"].currentData() == "off"

        # A selector left matching its dropdown default stores nothing; the
        # explicit deviations round-trip.
        combos["web_search"].setCurrentIndex(combos["web_search"].findData("on"))
        combos["read_file"].setCurrentIndex(combos["read_file"].findData("model"))
        combos["edit_file"].setCurrentIndex(combos["edit_file"].findData("on"))
        result = dlg.selected_overrides()
        assert result["web_search"] == "on"
        assert result["read_file"] == "model"
        assert result["edit_file"] == "on"
        assert result["github_repo"] == "off"
        assert "git_status" not in result
        assert "memory_search" not in result
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
