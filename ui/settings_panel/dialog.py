"""
ui/settings.py -” Settings dialog.

A plain GUI for editing all user-configurable values.
Reads from and writes to the .env file.
Launch via tray icon â†’ Settings, or call open_settings().
"""
from __future__ import annotations
import os
import sys
import importlib.util
import logging
import subprocess
import threading
import re
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox,
    QPushButton, QTabWidget, QWidget, QFrame, QGroupBox, QMessageBox,
    QScrollArea, QSizePolicy, QCompleter, QInputDialog, QMenu, QSlider,
    QApplication,
)
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QFont
from core import secret_store
from core.system.env_utils import (
    format_tool_modes, normalize_file_access_mode, normalize_screenshot_mode,
    parse_tool_modes,
)
import ui.settings_panel.env as settings_env
from ui.settings_panel import context_controls
from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit
from ui.settings_panel.helpers import (
    NoScrollCombo as _NoScrollCombo,
    WarningHeaderLabel as _WarningHeaderLabel,
    expanding_form_layout as _expanding_form_layout,
    parse_fallback_rows,
)
from ui.i18n import COMBO_I18N_SOURCE_ROLE, LANGUAGE_OPTIONS, localize_widget_tree, t
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

ENV_PATH = settings_env.ENV_PATH
_settings_log = logging.getLogger("wisp.settings")
_settings_dialog: "SettingsDialog | None" = None
_settings_open_pending = False
_SETUP_CHECK_STATUS_LABELS = {
    "pass": "PASS",
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
}
_TTS_TIMESTAMPLESS_PROVIDERS = {
    "elevenlabs",
    "openai",
    "openai_compatible",
    "gpt_sovits",
    "kokoro",
}
_TTS_TIMING_NOTICE = (
    "This provider does not send real word timestamps. The bubble uses normal reveal speed "
    "instead of audio-synced word highlighting."
)
_AUTH_STATUS_TIMEOUT_MS = 7000


# Sentinel data value for the "Custom / enter manually…" model combo entry.
_CUSTOM_MODEL_SENTINEL = "__custom__"
_CUSTOM_MODEL_LABEL = "Custom / enter manually…"
# Fixed width of the leading "Priority" column shown beside each model row.
_MODEL_PRIORITY_COL_W = 46

_ASSISTANT_LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("System default", ""),
    ("Match user language", "match_user"),
    ("English", "English"),
    ("Chinese", "Chinese"),
    ("Chinese (Traditional)", "Chinese (Traditional)"),
    ("Spanish", "Spanish"),
    ("French", "French"),
    ("German", "German"),
    ("Japanese", "Japanese"),
    ("Korean", "Korean"),
    ("Portuguese", "Portuguese"),
    ("Hindi", "Hindi"),
)

_STT_LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Auto-detect", ""),
    ("English", "en"),
    ("Chinese (Mandarin)", "zh"),
    ("Cantonese", "yue"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Portuguese", "pt"),
    ("Hindi", "hi"),
    ("Italian", "it"),
    ("Russian", "ru"),
    ("Arabic", "ar"),
)

_STT_MODEL_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("tiny", "tiny", "Whisper model option: tiny"),
    ("base", "base", "Whisper model option: base"),
    ("small", "small", "Whisper model option: small"),
    ("medium", "medium", "Whisper model option: medium"),
    ("large-v3", "large-v3", "Whisper model option: large-v3"),
)

_STT_COMPUTE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("int8", "int8"),
    ("int8_float16", "int8_float16"),
    ("float16", "float16"),
    ("float32", "float32"),
)

_STT_BEAM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("1 (fastest)", "1"),
    ("3", "3"),
    ("5 (recommended)", "5"),
    ("8 (most accurate)", "8"),
)

_STT_DEVICE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Auto (GPU if available)", "auto"),
    ("CPU", "cpu"),
    ("GPU (CUDA)", "cuda"),
)

_DICTATE_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Paste raw transcript", "raw"),
    ("Light LLM cleanup", "llm"),
)

_CHAT_REASONING_EFFORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Provider default", ""),
    ("Minimal", "minimal"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
)

_SETTINGS_PRESET_KEY = "WISP_SETTINGS_PRESET"
_PRESET_ENV_PREFIX = "WISP_PRESET_"

_PRESET_LABELS: dict[str, str] = {
    "low_setup": "Low setup",
}

_PRESET_SLUGS: dict[str, str] = {
    label.lower(): slug for slug, label in _PRESET_LABELS.items()
}

_PRESET_DESCRIPTIONS: dict[str, str] = {
    "low_setup": "Use ChatGPT OAuth for every model route, with minimal context and no API keys.",
}

_PRESET_DEFAULTS: dict[str, dict[str, str]] = {
    "low_setup": {
        "LLM_PROVIDER": "chatgpt",
        "LLM_MODEL": "gpt-5.5",
        "LLM_FALLBACKS": "",
        "VISION_LLM_PROVIDER": "chatgpt",
        "VISION_LLM_MODEL": "gpt-5.5",
        "VISION_LLM_FALLBACKS": "",
        "MEMORY_LLM_PROVIDER": "chatgpt",
        "MEMORY_LLM_MODEL": "gpt-5.5",
        "MEMORY_LLM_FALLBACKS": "",
        "STT_MODEL": "base",
        "STT_BEAM_SIZE": "1",
        "MEMORY_TOP_K": "2",
        "CONTEXT_BROWSER_MAX_CHARS": "3000",
        "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": "4000",
        "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": "4000",
    },
}

_PRESET_CONTEXT_DEFAULTS: dict[str, dict[str, str]] = {
    "low_setup": {
        "documents": "off", "browser": "off", "github": "off",
        "memory": "on", "screenshot": "off", "clear_tools": "true",
    },
}


class _ModelFetchSignals(QObject):
    """Marshals a background model-list fetch result back to the Qt main thread.

    done(models: list, error: str) — error is "" on success.
    """
    done = Signal(object, str)


class _SttPreloadSignals(QObject):
    """Marshals a background STT model preload result back to the Qt main thread.

    done(backend_info: object | None, error: str) — error is "" on success.
    """
    done = Signal(object, str)


class _UpdateSignals(QObject):
    """Marshals update check/download results back to the Qt main thread."""
    done = Signal(object, str)


def _read_env() -> dict[str, str]:
    """Read env."""
    old_path = settings_env.ENV_PATH
    settings_env.ENV_PATH = ENV_PATH
    try:
        return settings_env.read_settings_env()
    finally:
        settings_env.ENV_PATH = old_path


def _format_env_value(value: str) -> str:
    """Format env value."""
    return settings_env.format_settings_env_value(value)


def _write_env(vals: dict[str, str], remove_keys: set[str] | None = None):
    """Write env."""
    old_path = settings_env.ENV_PATH
    settings_env.ENV_PATH = ENV_PATH
    try:
        settings_env.write_settings_env(vals, remove_keys=remove_keys)
    finally:
        settings_env.ENV_PATH = old_path


def _normalize_extra_tool_payloads(raw_tools) -> list[dict[str, str]]:
    """Normalize live addon/tool payloads for the settings tool picker."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_tools or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or name)
        else:
            name = str(item or "").strip()
            description = name
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "description": description})
    return out


class SettingsDialog(QDialog):
    """Qt dialog for settings dialog."""
    def __init__(self, parent=None, on_apply=None, on_setup_check=None, extra_tools=None):
        """Initialize the settings dialog instance."""
        super().__init__(parent)
        self._on_apply = on_apply  # callable() fired after a successful apply
        self._on_setup_check = on_setup_check
        self._extra_tools = _normalize_extra_tool_payloads(extra_tools)
        self._disposing = False
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setModal(False)
        enable_standard_window_controls(self)
        self._env = _read_env()
        self._fields: dict[str, QLineEdit | QComboBox | QCheckBox | QTextEdit | QSlider] = {}
        # Theme color templates: {"light": {bg,surface,text,accent}, "dark": {...}}.
        # The four App-tab swatches edit whichever mode is selected in the Theme
        # combo; switching modes swaps these without losing the other mode's edits.
        self._theme_templates: dict[str, dict[str, str]] = {}
        self._theme_shown_mode: str = ""
        self._theme_syncing: bool = False
        self._api_key_rows: list[dict] = []
        self._model_section_rows: dict[str, list[dict]] = {
            "LLM": [], "VISION_LLM": [], "MEMORY_LLM": []
        }
        self._model_section_layouts: dict[str, "QVBoxLayout"] = {}
        self._warning_headers: dict[str, QLabel] = {}
        self._warning_header_base_texts: dict[str, str] = {}
        self._chat_elaborate_prompt_label: QLabel | None = None
        self._fallback_rows: dict = {}
        self._loading_values = False
        self._saving_settings = False
        self._dirty_refresh_scheduled = False
        self._dirty_baseline: dict[str, str] = {}
        self._dirty_keys: set[str] = set()
        self._tab_base_names: list[str] = []
        self._tab_dirty_names: set[str] = set()
        self._tab_search_text: dict[str, str] = {}
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        self._pending_active_profile = ""
        self._pending_test_results: list[tuple[str, int, bool, str]] = []
        self._pending_test_results_lock = threading.Lock()
        self._pending_test_progress: list[tuple[str, int, str]] = []
        self._pending_test_progress_lock = threading.Lock()
        self._pending_status_results: list[tuple[int, str, object, str]] = []
        self._pending_status_results_lock = threading.Lock()
        self._pending_status_attrs: set[str] = set()
        self._running_test_tokens: set[tuple[str, int]] = set()
        self._latest_test_token: dict[str, int] = {}
        self._last_save_warnings: list[str] = []
        self._open_warning_boxes: list[QMessageBox] = []
        self._status_refresh_token = 0
        self._status_refresh_running = False
        self._update_check_result = None
        self._update_download_path = None
        self._update_mode = "check"
        self._update_running = False
        self._update_signal_carriers: list[_UpdateSignals] = []
        self._tabs = None
        self._test_result_timer = QTimer(self)
        self._test_result_timer.setInterval(100)
        self._test_result_timer.timeout.connect(self._drain_test_results)
        self._status_result_timer = QTimer(self)
        self._status_result_timer.setInterval(100)
        self._status_result_timer.timeout.connect(self._drain_status_results)
        self._warning_header_uppercase_keys: set[str] = set()
        self.finished.connect(self._dispose_after_finished)
        self._build_ui()
        self._load_values()
        localize_widget_tree(self)
        self._refresh_tab_labels()
        self._refresh_search_index()
        self._schedule_open_status_refresh()
        fit_window_to_screen(self, preferred_width=760, preferred_height=720)

    def _save_api_keys_to_keychain(self) -> bool:
        """Persist every typed API key to the OS keychain, one at a time.

        Each key is written and verified independently: a failure on one key is
        logged and collected for the user, but never blocks the other keys or the
        rest of the settings save. Successfully stored keys have their input field
        cleared and switched to a "stored" placeholder. Returns True only when all
        typed keys were saved.
        """
        failures: list[str] = []

        def _store(key_name: str, value: str, label: str) -> bool:
            """Handle store for settings dialog."""
            try:
                secret_store.set_secret(key_name, value)
                _settings_log.info("Saved %s (%s) to OS keychain", key_name, label)
                return True
            except Exception as exc:  # noqa: BLE001 — reported to the user + log
                _settings_log.error("Could not save %s (%s) to OS keychain: %s", key_name, label, exc)
                failures.append(f"{label}: {exc}")
                return False

        try:
            secret_store.migrate_env_secrets(self._env)
        except Exception as exc:  # noqa: BLE001 — non-fatal: just means no .env keys to migrate
            _settings_log.warning("Secret migration from .env skipped: %s", exc)

        # LLM provider keys from the API key table
        for row in self._api_key_rows:
            provider = _get(row["provider"]).strip()
            value = row["key"].text().strip()
            if not value:
                continue
            label = _PROVIDER_LABELS.get(provider, provider)
            if provider == "copilot":
                try:
                    from core.auth import copilot_auth
                    copilot_auth.save_token(value)
                    _settings_log.info("Saved GitHub Copilot token to OS keychain")
                    row["key"].clear()
                    row["key"].setPlaceholderText(t("stored in keychain"))
                    continue
                except Exception as exc:  # noqa: BLE001 - reported with other keychain failures
                    _settings_log.error("Could not save GitHub Copilot token to OS keychain: %s", exc)
                    failures.append(f"{label}: {exc}")
                    continue
            key_name = _PROVIDER_KEY_NAMES.get(provider)
            if not key_name:
                continue
            if _store(key_name, value, label):
                row["key"].clear()
                row["key"].setPlaceholderText(t("stored in keychain"))

        # TTS and custom keys still live in self._fields
        for name, label in [
            ("CARTESIA_API_KEY",   "Cartesia"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("TTS_CUSTOM_API_KEY", "Custom TTS endpoint"),
            ("CUSTOM_API_KEY",     "Custom provider"),
        ]:
            if name not in self._fields:
                continue
            value = _get(self._fields[name]).strip()
            if not value:
                continue
            if _store(name, value, label):
                self._fields[name].clear()  # type: ignore[attr-defined]
                self._fields[name].setPlaceholderText(t(f"{label} key stored in OS keychain"))  # type: ignore[attr-defined]

        if failures:
            QMessageBox.warning(
                self,
                t("Some API keys were not saved"),
                t("These keys could not be written to the OS keychain and were NOT stored:")
                + "\n\n"
                + "\n".join(f"• {item}" for item in failures)
                + "\n\n"
                + t("Your other settings were still saved. See the log for details, "
                    "then try saving the affected keys again."),
            )
            return False
        return True

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def changeEvent(self, event):               # noqa: N802
        """Cancel any active hotkey recording when the window is deactivated."""
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowDeactivate:
            for w in self.findChildren(HotkeyCaptureEdit):
                if w._recording:
                    w._cancel()
        super().changeEvent(event)

    def showEvent(self, event):                 # noqa: N802
        """Show event."""
        super().showEvent(event)
        fit_window_to_screen(self, preferred_width=760, preferred_height=720)
        self._refresh_stt_active_backend()

    def hideEvent(self, event):                 # noqa: N802
        """Hide event."""
        self._cancel_async_ui_updates()
        super().hideEvent(event)

    def closeEvent(self, event):                # noqa: N802
        """Close event."""
        self._cancel_async_ui_updates()
        super().closeEvent(event)

    def _dispose_after_finished(self, _result: int) -> None:
        """Handle dispose after finished for settings dialog."""
        self._disposing = True
        self._cancel_async_ui_updates()
        _clear_settings_dialog(self)
        self.deleteLater()

    @staticmethod
    def _dialog_style(dark: bool) -> str:
        """Build the dialog stylesheet from the active mode's template colours."""
        from ui.shared.theme import theme_colors
        c = theme_colors(dark)
        # In light mode card/tab text read better a touch dimmer than text_dim.
        return f"""
        QDialog, QWidget#wispWindowContent {{
            background: {c["bg"]};
        }}
        QTabWidget#settingsTabs {{
            background: {c["bg"]};
        }}
        QTabWidget#settingsTabs::pane {{
            border: none;
            background: {c["bg"]};
        }}
        QTabWidget#settingsTabs::tab-bar {{
            background: {c["bg"]};
            alignment: left;
        }}
        QTabWidget#settingsTabs > QWidget {{
            background: {c["bg"]};
        }}
        QTabBar#settingsTabBar {{
            background: {c["bg"]};
            background-color: {c["bg"]};
            border: none;
        }}
        QTabBar#settingsTabBar::tab {{
            color: {c["text_dim"]}; padding: 7px 20px; border-radius: 8px;
            border: 1px solid {c["border"]};
            font-size: 9pt; margin: 2px 2px; background: transparent;
        }}
        QTabBar#settingsTabBar::tab:selected {{
            background: {c["tab_selected"]}; color: {c["accent"]};
            border: 1px solid {c["accent"]}; font-weight: 600;
        }}
        QTabBar#settingsTabBar::tab:hover:!selected {{ background: {c["accent_hint"]}; }}
        QTabBar#settingsTabBar::scroller {{
            background: {c["bg"]};
            width: 0px;
        }}
        QTabBar#settingsTabBar QToolButton {{
            background: {c["bg"]};
            border: none;
        }}
        QFrame#card {{
            background: {c["card"]}; border: 1px solid {c["border"]}; border-radius: 12px;
        }}
        QLabel#sectionHeader {{
            color: {c["text"]}; font-size: 10pt; font-weight: 700;
            letter-spacing: 0.5px; padding: 0px;
        }}
        QLabel#areaHeader {{
            color: {c["text"]}; font-size: 11pt; font-weight: 700;
            letter-spacing: 0px; padding: 0px;
        }}
        QLabel#areaSubheader {{
            color: {c["text_dim"]}; font-size: 9pt; padding: 0px;
        }}
        QFrame#areaAccentLine {{
            background: {c["accent"]}; border: none; border-radius: 2px;
        }}
        QScrollArea {{
            background: {c["bg"]};
            border: none;
        }}
        QScrollArea > QWidget {{
            background: {c["bg"]};
        }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QWidget {{ color: {c["text"]}; }}
        QLineEdit {{
            background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: {c["text"]}; min-height: 30px;
        }}
        QLineEdit:focus {{ border-color: {c["accent"]}; }}
        QComboBox {{
            background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: {c["text"]}; min-height: 30px;
        }}
        QComboBox:focus {{ border-color: {c["accent"]}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{
            background: {c["card"]}; color: {c["text"]}; border: 1px solid {c["border"]};
        }}
        QPushButton {{
            border: 1.5px solid {c["accent"]}; color: {c["accent"]}; border-radius: 8px;
            padding: 5px 16px; background: transparent; font-size: 10pt;
        }}
        QPushButton:hover {{ background: {c["accent_soft"]}; }}
        QPushButton:pressed {{ background: {c["accent_strong"]}; }}
        QPushButton:flat {{ border: none; color: {c["accent"]}; background: transparent; }}
        QPushButton:flat:hover {{ color: {c["accent_hover"]}; background: transparent; }}
        QCheckBox {{ color: {c["text"]}; }}
        QLabel {{ color: {c["text"]}; }}
        QTextEdit, QPlainTextEdit {{
            background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 8px;
            color: {c["text"]};
        }}
        QLineEdit[dirty="true"], QComboBox[dirty="true"], QTextEdit[dirty="true"] {{
            border-color: {c["accent"]};
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {c["bg"]}; border: none;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {c["scroll_handle"]}; border-radius: 4px;
            min-height: 24px; min-width: 24px;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: {c["bg"]};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
    """

    def _apply_dialog_theme(self):
        """Apply dialog theme."""
        from ui.shared.theme import is_dark_mode
        self.setStyleSheet(self._dialog_style(is_dark_mode()))

    def _build_ui(self):
        """Build ui."""
        self._apply_dialog_theme()
        root = QVBoxLayout(self)
        root.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self._settings_search = QLineEdit()
        self._settings_search.setObjectName("settingsSearch")
        self._settings_search.setPlaceholderText(t("Search settings..."))
        self._settings_search.setClearButtonEnabled(True)
        self._settings_search.textChanged.connect(self._apply_settings_search)
        top_row.addWidget(self._settings_search, 1)

        profile_btn = QPushButton(t("Profiles..."))
        self._profiles_btn = profile_btn
        profile_btn.setObjectName("settingsProfilesButton")
        profile_btn.setToolTip(t("Apply or create a profile for common Wisp setups. Review changes before Apply."))
        profile_btn.setMenu(self._build_profiles_menu(profile_btn))
        top_row.addWidget(profile_btn)
        setup_btn = QPushButton(t("Run setup check"))
        setup_btn.setObjectName("settingsSetupCheckButton")
        setup_btn.setToolTip(t("Check provider, speech, hotkey, and privacy readiness."))
        setup_btn.clicked.connect(self._run_setup_check)
        top_row.addWidget(setup_btn)
        root.addLayout(top_row)

        self._search_status_lbl = QLabel()
        self._search_status_lbl.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")
        self._search_status_lbl.hide()
        root.addWidget(self._search_status_lbl)

        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        tabs.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tabs.tabBar().setObjectName("settingsTabBar")
        tabs.tabBar().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tabs.tabBar().setDrawBase(False)
        tabs.tabBar().setExpanding(True)
        # Never elide tab labels to "…"; macOS sizes the bold selected tab
        # tighter than Windows, so let each tab grow to fit its full text.
        tabs.setElideMode(Qt.TextElideMode.ElideNone)
        tabs.addTab(self._tab_app(),       "App")
        tabs.addTab(self._tab_llm(),       "LLM")
        tabs.addTab(self._tab_tts(),       "TTS / Voice")
        tabs.addTab(self._tab_keybinds(),  "Keybinds")
        tabs.addTab(self._tab_prompt(),    "Prompts")
        tabs.addTab(self._tab_advanced(),  "Advanced")
        self._tabs = tabs
        self._tab_base_names = [tabs.tabText(i) for i in range(tabs.count())]
        root.addWidget(tabs)

        # Buttons
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
        btn_row = QHBoxLayout()
        reset_page_btn = QPushButton("Reset Page…")
        reset_page_btn.setToolTip("Reset only the currently selected settings tab to defaults")
        reset_page_btn.clicked.connect(self._reset_current_page)
        btn_row.addWidget(reset_page_btn)
        reset_btn = QPushButton("Reset All…")
        reset_btn.setToolTip(
            "Delete all API keys from the OS keychain and reset every setting to defaults"
        )
        reset_btn.setStyleSheet(
            "QPushButton { border: 1.5px solid #c0392b; color: #c0392b; }"
            "QPushButton:hover { background: #3a2020; }"
            "QPushButton:pressed { background: #4c2424; }"
        )
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch()
        apply_btn = QPushButton("Apply")
        confirm_btn = QPushButton("Confirm")
        self._apply_btn = apply_btn
        apply_btn.setObjectName("settingsApplyButton")
        confirm_btn.setObjectName("settingsConfirmButton")
        confirm_btn.setDefault(True)
        apply_btn.setEnabled(False)
        apply_btn.clicked.connect(self._apply)
        confirm_btn.clicked.connect(self._confirm)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(confirm_btn)
        root.addLayout(btn_row)

    def _build_profiles_menu(self, parent: QWidget) -> QMenu:
        """Build profiles menu."""
        menu = QMenu(parent)
        for slug, name in _PRESET_LABELS.items():
            action = menu.addAction(t(name))
            action.setToolTip(t(_PRESET_DESCRIPTIONS[slug]))
            action.triggered.connect(lambda _checked=False, preset=slug: self._apply_preset(preset))
        saved_profiles = self._saved_custom_profile_entries()
        if saved_profiles:
            menu.addSeparator()
            for index, profile_id, label in saved_profiles:
                action = menu.addAction(label)
                action.setToolTip(t("Load this custom profile into Settings."))
                action.triggered.connect(
                    lambda _checked=False, slot=index: self._apply_saved_profile(slot)
                )
            menu.addSeparator()
            rename_action = menu.addAction(t("Rename profile..."))
            rename_action.setToolTip(t("Change the display name for a saved custom profile."))
            rename_action.triggered.connect(self._rename_custom_profile)
            delete_action = menu.addAction(t("Delete profile..."))
            delete_action.setToolTip(t("Delete a saved custom profile."))
            delete_action.triggered.connect(self._delete_custom_profile)
        menu.addSeparator()
        create_action = menu.addAction(t("Create custom profile..."))
        create_action.setToolTip(t("Save the current model, context, and budget settings as a reusable profile."))
        create_action.triggered.connect(self._create_custom_profile)
        return menu

    def _refresh_profiles_menu(self) -> None:
        """Refresh the Profiles menu after custom profile changes."""
        btn = getattr(self, "_profiles_btn", None)
        if isinstance(btn, QPushButton):
            old_menu = btn.menu()
            btn.setMenu(self._build_profiles_menu(btn))
            if old_menu is not None:
                old_menu.deleteLater()

    def _saved_custom_profile_entries(self) -> list[tuple[int, str, str]]:
        """Return custom PROFILE_N entries saved in the env file."""
        entries: list[tuple[int, str, str]] = []
        try:
            count = int(str(self._env.get("PROFILE_COUNT", "0") or "0"))
        except ValueError:
            count = 0
        for index in range(1, count + 1):
            profile_id = str(self._env.get(f"PROFILE_{index}_ID", "") or "").strip()
            if not profile_id:
                continue
            label = str(self._env.get(f"PROFILE_{index}_LABEL", "") or profile_id).strip()
            entries.append((index, profile_id, label))
        return entries

    def _choose_saved_profile_slot(self, title: str, prompt: str) -> tuple[int, str, str] | None:
        """Return a saved custom profile chosen by the user."""
        entries = self._saved_custom_profile_entries()
        if not entries:
            return None
        if len(entries) == 1:
            return entries[0]
        labels = [label for _slot, _profile_id, label in entries]
        duplicates = {label for label in labels if labels.count(label) > 1}
        choices = [
            f"{label} ({profile_id})" if label in duplicates else label
            for _slot, profile_id, label in entries
        ]
        choice, accepted = QInputDialog.getItem(
            self,
            title,
            prompt,
            choices,
            0,
            False,
        )
        if not accepted:
            return None
        try:
            return entries[choices.index(choice)]
        except ValueError:
            return None

    def _profile_env_slot_values(self, slot: int) -> dict[str, str]:
        """Return all PROFILE_N_* values for an env slot without the slot prefix."""
        prefix = f"PROFILE_{slot}_"
        return {
            key[len(prefix):]: value
            for key, value in self._env.items()
            if key.startswith(prefix)
        }

    def _rewrite_custom_profiles(self, profiles: list[dict[str, str]]) -> None:
        """Persist custom profiles compactly as PROFILE_1..PROFILE_N."""
        remove_keys = {
            key
            for key in self._env
            if re.match(r"^PROFILE_\d+_", key)
        }
        remove_keys.add("PROFILE_COUNT")
        vals: dict[str, str] = {"PROFILE_COUNT": str(len(profiles))}
        for index, profile in enumerate(profiles, start=1):
            vals.update({f"PROFILE_{index}_{key}": value for key, value in profile.items()})
        _write_env(vals, remove_keys=remove_keys)
        for key in remove_keys:
            self._env.pop(key, None)
        self._env.update(vals)

    @staticmethod
    def _profile_id(raw: str, default: str = "custom") -> str:
        """Normalize user-entered profile names to config profile ids."""
        text = str(raw or default or "custom").strip().lower()
        text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-")
        return text or default

    def _unique_custom_profile_id(self, label: str) -> str:
        """Return a profile id that does not shadow an existing built-in/custom profile."""
        base = self._profile_id(label)
        existing = {
            "default", "fast", "balanced", "deep", "private", "coding",
            *(profile_id for _idx, profile_id, _label in self._saved_custom_profile_entries()),
        }
        if base not in existing:
            return base
        suffix = 2
        while f"{base}-{suffix}" in existing:
            suffix += 1
        return f"{base}-{suffix}"

    def _model_section_values(self, sk: str) -> tuple[str, str, str]:
        """Return provider, primary model, and fallback rows for a model section."""
        rows = self._model_section_rows.get(sk, [])
        if not rows:
            return "", "", ""
        primary = rows[0]
        provider = str(primary["api_key_combo"].currentData() or "")
        model = self._model_value(primary)
        fallbacks = "\n".join(
            f"{r['api_key_combo'].currentData() or ''}:{self._model_value(r)}"
            for r in rows[1:]
            if (r["api_key_combo"].currentData() or "") and self._model_value(r)
        )
        return provider, model, fallbacks

    def _current_profile_values(self, label: str, profile_id: str) -> dict[str, str]:
        """Snapshot current settings into config.py's PROFILE_N_* contract."""
        import config as cfg

        llm_p, llm_m, llm_f = self._model_section_values("LLM")
        vis_p, vis_m, vis_f = self._model_section_values("VISION_LLM")
        mem_p, mem_m, mem_f = self._model_section_values("MEMORY_LLM")
        vb = getattr(self, "_voice_block", {})
        values = {
            "ID": profile_id,
            "LABEL": label,
            "LLM_PROVIDER": llm_p,
            "LLM_MODEL": llm_m,
            "LLM_FALLBACKS": llm_f,
            "VISION_LLM_PROVIDER": vis_p,
            "VISION_LLM_MODEL": vis_m,
            "VISION_LLM_FALLBACKS": vis_f,
            "MEMORY_LLM_PROVIDER": mem_p,
            "MEMORY_LLM_MODEL": mem_m,
            "MEMORY_LLM_FALLBACKS": mem_f,
            "CONTEXT_BROWSER_MAX_CHARS": _get(self._fields["CONTEXT_BROWSER_MAX_CHARS"]),
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"]),
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"]),
            "TOOL_TURN_MAX_CALLS": str(getattr(cfg, "TOOL_TURN_MAX_CALLS", 25)),
            "TOOL_TURN_MAX_RESULT_CHARS": str(getattr(cfg, "TOOL_TURN_MAX_RESULT_CHARS", 120000)),
            "TOOL_TURN_MAX_TOTAL_CHARS": str(getattr(cfg, "TOOL_TURN_MAX_TOTAL_CHARS", 300000)),
        }
        if vb:
            values.update(
                {
                    "CONTEXT_DOCUMENTS_MODE": str(vb["context_documents_mode"].currentData()),
                    "CONTEXT_BROWSER_MODE": str(vb["context_browser_mode"].currentData()),
                    "CONTEXT_GITHUB_MODE": str(vb["context_github_mode"].currentData()),
                    "CONTEXT_MEMORY_MODE": str(vb["context_memory_mode"].currentData()),
                    "CONTEXT_SCREENSHOT": str(vb["context_screenshot"].currentData()),
                    "FILE_ACCESS": str(vb["file_access"].currentData()),
                }
            )
        return values

    def _create_custom_profile(self) -> None:
        """Create a reusable custom profile from the current settings."""
        label, accepted = QInputDialog.getText(
            self,
            t("Create custom profile"),
            t("Profile name"),
        )
        label = str(label or "").strip()
        if not accepted or not label:
            return
        profile_id = self._unique_custom_profile_id(label)
        current_count = len(self._saved_custom_profile_entries())
        try:
            env_count = int(str(self._env.get("PROFILE_COUNT", "0") or "0"))
        except ValueError:
            env_count = current_count
        slot = max(env_count, current_count) + 1
        profile_values = self._current_profile_values(label, profile_id)
        vals = {
            "PROFILE_COUNT": str(slot),
        }
        vals.update({f"PROFILE_{slot}_{key}": value for key, value in profile_values.items()})
        _write_env(vals)
        self._env.update(vals)
        self._active_preset_slug = ""
        self._pending_active_profile = profile_id
        self._refresh_profiles_menu()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile created. Review changes, then Apply to use it.").format(profile=label)
        )

    def _rename_custom_profile(self) -> None:
        """Rename a saved custom profile display label."""
        chosen = self._choose_saved_profile_slot(t("Rename profile"), t("Choose profile"))
        if chosen is None:
            return
        slot, profile_id, old_label = chosen
        label, accepted = QInputDialog.getText(
            self,
            t("Rename profile"),
            t("Profile name"),
            QLineEdit.EchoMode.Normal,
            old_label,
        )
        label = str(label or "").strip()
        if not accepted or not label or label == old_label:
            return
        vals = {f"PROFILE_{slot}_LABEL": label}
        _write_env(vals)
        self._env.update(vals)
        self._refresh_profiles_menu()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile renamed.").format(profile=label)
        )

    def _delete_custom_profile(self) -> None:
        """Delete a saved custom profile."""
        chosen = self._choose_saved_profile_slot(t("Delete profile"), t("Choose profile"))
        if chosen is None:
            return
        slot, profile_id, label = chosen
        answer = QMessageBox.question(
            self,
            t("Delete profile"),
            t("Delete {profile} profile?").format(profile=label),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        profiles = [
            self._profile_env_slot_values(index)
            for index, _pid, _label in self._saved_custom_profile_entries()
            if index != slot
        ]
        remove_keys: set[str] = set()
        if str(self._env.get("ACTIVE_PROFILE", "") or "") == profile_id:
            remove_keys.add("ACTIVE_PROFILE")
            self._env.pop("ACTIVE_PROFILE", None)
        if str(self._env.get("SETTINGS_PROFILE", "") or "") == profile_id:
            remove_keys.add("SETTINGS_PROFILE")
            self._env.pop("SETTINGS_PROFILE", None)
        if str(getattr(self, "_pending_active_profile", "") or "") == profile_id:
            self._pending_active_profile = ""
        self._rewrite_custom_profiles(profiles)
        if remove_keys:
            _write_env({}, remove_keys=remove_keys)
        self._refresh_profiles_menu()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile deleted.").format(profile=label)
        )

    def _profile_values_from_env_slot(self, slot: int) -> dict[str, str]:
        """Return UI-applicable values from a PROFILE_N env slot."""
        prefix = f"PROFILE_{slot}_"
        profile_values = {
            key[len(prefix):]: value
            for key, value in self._env.items()
            if key.startswith(prefix)
        }
        mapping = {
            "LLM_PROVIDER": "LLM_PROVIDER",
            "LLM_MODEL": "LLM_MODEL",
            "LLM_FALLBACKS": "LLM_FALLBACKS",
            "VISION_LLM_PROVIDER": "VISION_LLM_PROVIDER",
            "VISION_LLM_MODEL": "VISION_LLM_MODEL",
            "VISION_LLM_FALLBACKS": "VISION_LLM_FALLBACKS",
            "MEMORY_LLM_PROVIDER": "MEMORY_LLM_PROVIDER",
            "MEMORY_LLM_MODEL": "MEMORY_LLM_MODEL",
            "MEMORY_LLM_FALLBACKS": "MEMORY_LLM_FALLBACKS",
            "CONTEXT_BROWSER_MAX_CHARS": "CONTEXT_BROWSER_MAX_CHARS",
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS",
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": "CONTEXT_TOOL_DOCUMENT_MAX_CHARS",
        }
        return {env_key: profile_values[source_key] for source_key, env_key in mapping.items() if source_key in profile_values}

    def _apply_saved_profile(self, slot: int) -> None:
        """Load a custom profile into the settings UI for review."""
        values = self._profile_values_from_env_slot(slot)
        if not values:
            return
        profile_id = str(self._env.get(f"PROFILE_{slot}_ID", "") or "").strip()
        label = str(self._env.get(f"PROFILE_{slot}_LABEL", "") or profile_id).strip()
        self._active_preset_slug = ""
        self._apply_env_values_to_ui(values)
        self._set_context_modes(
            documents=self._env.get(f"PROFILE_{slot}_CONTEXT_DOCUMENTS_MODE"),
            browser=self._env.get(f"PROFILE_{slot}_CONTEXT_BROWSER_MODE"),
            github=self._env.get(f"PROFILE_{slot}_CONTEXT_GITHUB_MODE"),
            memory=self._env.get(f"PROFILE_{slot}_CONTEXT_MEMORY_MODE"),
            screenshot=self._env.get(f"PROFILE_{slot}_CONTEXT_SCREENSHOT"),
            file_access=self._env.get(f"PROFILE_{slot}_FILE_ACCESS"),
        )
        self._pending_active_profile = profile_id
        self._refresh_stt_active_backend()
        self._schedule_warning_marker_refresh()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile selected. Review changes, then Apply.").format(profile=label)
        )

    def _run_setup_check(self) -> None:
        """Run lightweight setup checks and show a reusable report."""
        if callable(self._on_setup_check):
            self._on_setup_check()
            return
        try:
            from core.setup_check import run_setup_check

            rows = run_setup_check()
        except Exception as exc:  # noqa: BLE001 - setup check must not crash settings
            rows = [
                {
                    "name": t("Setup check"),
                    "status": "fail",
                    "message": f"{type(exc).__name__}: {exc}",
                    "recommendation": t("Recommendation: close Settings, reopen it, and try again."),
                }
            ]
        lines = []
        for row in rows:
            status = t(_SETUP_CHECK_STATUS_LABELS.get(str(row.get("status") or "warn").lower(), "WARN"))
            name = _translate_status_message(str(row.get("name") or "Check"))
            message = _translate_status_message(str(row.get("message") or ""))
            recommendation = _translate_status_message(str(row.get("recommendation") or ""))
            block = f"{status} - {name}\n{message}"
            if recommendation:
                block += f"\n{recommendation}"
            lines.append(block)
        QMessageBox.information(self, t("Setup check"), "\n\n".join(lines))

    @staticmethod
    def _preset_slug(raw: str) -> str:
        """Handle preset slug for settings dialog."""
        value = str(raw or "").strip()
        return value if value in _PRESET_LABELS else _PRESET_SLUGS.get(value.lower(), "")

    @staticmethod
    def _preset_override_key(slug: str, key: str) -> str:
        """Handle preset override key for settings dialog."""
        safe_slug = slug.upper().replace("-", "_")
        return f"{_PRESET_ENV_PREFIX}{safe_slug}_{key}"

    @staticmethod
    def _key_from_preset_override(slug: str, env_key: str) -> str:
        """Handle key from preset override for settings dialog."""
        prefix = SettingsDialog._preset_override_key(slug, "")
        return env_key[len(prefix):] if env_key.startswith(prefix) else ""

    def _preset_saved_values(self, slug: str) -> dict[str, str]:
        """Handle preset saved values for settings dialog."""
        values: dict[str, str] = {}
        prefix = self._preset_override_key(slug, "")
        for env_key, value in self._env.items():
            if env_key.startswith(prefix):
                key = env_key[len(prefix):]
                if key:
                    values[key] = value
        return values

    def _preset_effective_values(self, slug: str) -> dict[str, str]:
        """Handle preset effective values for settings dialog."""
        values = dict(_PRESET_DEFAULTS.get(slug, {}))
        values.update(self._preset_saved_values(slug))
        return values

    def _preset_page_keys(self, slug: str, page: str, env: dict[str, str]) -> set[str]:
        """Handle preset page keys for settings dialog."""
        keys = set(self._reset_env_keys_for_page(page, env))
        prefix = self._preset_override_key(slug, "")
        for env_key in env:
            if env_key.startswith(prefix):
                key = env_key[len(prefix):]
                if key and self._page_for_dirty_key(key) == page:
                    keys.add(key)
        return keys

    def _preset_override_keys_for_keys(self, slug: str, keys: set[str], env: dict[str, str]) -> set[str]:
        """Handle preset override keys for keys for settings dialog."""
        prefix = self._preset_override_key(slug, "")
        remove = {self._preset_override_key(slug, key) for key in keys}
        remove.update(env_key for env_key in env if env_key.startswith(prefix) and env_key[len(prefix):] in keys)
        return remove

    def _current_tab_name(self) -> str:
        """Handle current tab name for settings dialog."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return ""
        idx = tabs.currentIndex()
        if 0 <= idx < len(getattr(self, "_tab_base_names", [])):
            return self._tab_base_names[idx]
        return tabs.tabText(idx).replace("*", "").strip()

    def _field_page_map(self) -> dict[str, str]:
        """Handle field page map for settings dialog."""
        mapping: dict[str, str] = {}
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return mapping
        for key, widget in self._fields.items():
            for idx, page in enumerate(self._tab_base_names):
                tab_widget = tabs.widget(idx)
                if tab_widget is widget or tab_widget.isAncestorOf(widget):
                    mapping[key] = page
                    break
        return mapping

    def _page_for_dirty_key(self, key: str) -> str:
        """Handle page for dirty key for settings dialog."""
        if key.startswith("CALLER_") or key.startswith("VOICE_") or key.startswith("SNIP_") or key in {
            "HOTKEY_VOICE", "HOTKEY_DICTATE", "DICTATE_MODE",
            "HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP",
            "HOTKEY_READ_SELECTION_ALOUD",
            "INTENT_CONTEXT_TOGGLE_KEYS", "INTENT_OVERLAY_TIMEOUT_MS",
        }:
            return "Keybinds"
        if key.startswith("API_KEY_ROW"):
            return "LLM"
        if key.startswith("LLM_") or key.startswith("VISION_LLM_") or key.startswith("MEMORY_LLM_"):
            return "LLM"
        if key in {"ACTIVE_PROFILE", "SETTINGS_PROFILE"}:
            return "App"
        if key.startswith("THEME_") or key.startswith("BUBBLE_") or key.startswith("TTS_PLAYBACK"):
            return self._field_page_map().get(key, "App")
        return self._field_page_map().get(key, "")

    def _snapshot_settings(self) -> dict[str, str]:
        """Handle snapshot settings for settings dialog."""
        snapshot: dict[str, str] = {}
        snapshot["ACTIVE_PROFILE"] = (
            str(getattr(self, "_pending_active_profile", "") or "").strip()
            or str(self._env.get("ACTIVE_PROFILE", "") or "").strip()
        )

        for key, widget in self._fields.items():
            if key.endswith("_API_KEY"):
                snapshot[key] = _get(widget).strip()
            elif isinstance(widget, QCheckBox):
                snapshot[key] = str(widget.isChecked())
            elif isinstance(widget, (QLineEdit, QComboBox, QTextEdit)):
                snapshot[key] = _get(widget)

        for idx, row in enumerate(getattr(self, "_api_key_rows", []), 1):
            snapshot[f"API_KEY_ROW_{idx}_PROVIDER"] = _get(row["provider"])
            snapshot[f"API_KEY_ROW_{idx}_ALIAS"] = row["alias"].text()
            snapshot[f"API_KEY_ROW_{idx}_KEY"] = row["key"].text()
        snapshot["API_KEY_ROW_COUNT"] = str(len(getattr(self, "_api_key_rows", [])))

        for section, rows in getattr(self, "_model_section_rows", {}).items():
            snapshot[f"{section}_ROW_COUNT"] = str(len(rows))
            for idx, row in enumerate(rows, 1):
                snapshot[f"{section}_{idx}_PROVIDER"] = str(row["api_key_combo"].currentData() or "")
                snapshot[f"{section}_{idx}_MODEL"] = self._model_value(row)

        snapshot["CALLER_COUNT"] = str(len(getattr(self, "_caller_blocks", [])))
        for idx, blk in enumerate(getattr(self, "_caller_blocks", []), 1):
            prefix = f"CALLER_{idx}"
            snapshot[f"{prefix}_HOTKEY"] = _get(blk["hotkey"])
            snapshot[f"{prefix}_LABEL"] = _get(blk["label"])
            snapshot[f"{prefix}_PASTE_BACK"] = str(blk["paste_back"].isChecked())
            snapshot[f"{prefix}_CUSTOM_KEY"] = _get(blk["custom_key"])
            snapshot[f"{prefix}_CUSTOM_LABEL"] = _get(blk["custom_label"])
            snapshot[f"{prefix}_CONTEXT_AMBIENT"] = str(blk["context_ambient"].isChecked())
            snapshot[f"{prefix}_CONTEXT_CLIPBOARD"] = str(blk["context_clipboard"].isChecked())
            for name in (
                "context_documents_mode", "context_browser_mode",
                "context_github_mode", "context_memory_mode", "context_screenshot",
                "file_access",
            ):
                snapshot[f"{prefix}_{name.upper()}"] = str(blk[name].currentData())
            snapshot[f"{prefix}_TOOLS"] = format_tool_modes(blk.get("tool_overrides") or {})
            snapshot[f"{prefix}_INTENT_COUNT"] = str(len(blk["intent_rows"]))
            for row_idx, row in enumerate(blk["intent_rows"], 1):
                row_prefix = f"{prefix}_INTENT_{row_idx}"
                snapshot[f"{row_prefix}_KEY"] = _get(row["key"])
                snapshot[f"{row_prefix}_LABEL"] = _get(row["label"])
                snapshot[f"{row_prefix}_PROMPT"] = _get(row["prompt"])

        if hasattr(self, "_voice_block"):
            vb = self._voice_block
            snapshot["VOICE_CONTEXT_AMBIENT"] = str(vb["context_ambient"].isChecked())
            snapshot["VOICE_CONTEXT_CLIPBOARD"] = str(vb["context_clipboard"].isChecked())
            for name in (
                "context_documents_mode", "context_browser_mode",
                "context_github_mode", "context_memory_mode", "context_screenshot",
                "file_access",
            ):
                snapshot[f"VOICE_{name.upper()}"] = str(vb[name].currentData())
            snapshot["VOICE_TOOLS"] = format_tool_modes(vb.get("tool_overrides") or {})

        if hasattr(self, "_snip_block"):
            snapshot.update(self._snip_context_values())

        return snapshot

    def _snip_context_values(self) -> dict[str, str]:
        """Return persisted values for the region-snip context block."""
        sb = self._snip_block
        documents_mode = str(sb["context_documents_mode"].currentData())
        browser_mode = str(sb["context_browser_mode"].currentData())
        github_mode = str(sb["context_github_mode"].currentData())
        memory_mode = str(sb["context_memory_mode"].currentData())
        return {
            "SNIP_CONTEXT_AMBIENT": str(sb["context_ambient"].isChecked()),
            "SNIP_CONTEXT_CLIPBOARD": str(sb["context_clipboard"].isChecked()),
            "SNIP_CONTEXT_DOCUMENTS_MODE": documents_mode,
            "SNIP_CONTEXT_BROWSER_MODE": browser_mode,
            "SNIP_CONTEXT_GITHUB_MODE": github_mode,
            "SNIP_CONTEXT_MEMORY_MODE": memory_mode,
            "SNIP_CONTEXT_SCREENSHOT": "off",
            "SNIP_FILE_ACCESS": str(sb["file_access"].currentData()),
            "SNIP_TOOLS": format_tool_modes(sb.get("tool_overrides") or {}),
            "SNIP_CONTEXT_DOCUMENTS": str(documents_mode == "auto"),
            "SNIP_CONTEXT_TOOLS": str(
                any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode))
            ),
        }

    def _reset_dirty_baseline(self) -> None:
        """Reset dirty baseline."""
        self._dirty_refresh_scheduled = False
        self._dirty_baseline = self._snapshot_settings()
        self._refresh_dirty_state()

    def _schedule_dirty_refresh(self) -> None:
        """Schedule dirty refresh."""
        if (
            getattr(self, "_loading_values", False)
            or getattr(self, "_saving_settings", False)
            or getattr(self, "_disposing", False)
        ):
            return
        if not hasattr(self, "_dirty_baseline"):
            return
        if getattr(self, "_dirty_refresh_scheduled", False):
            return
        self._dirty_refresh_scheduled = True
        QTimer.singleShot(0, self._refresh_dirty_state)

    def _wire_change_tracking(self, root: QWidget | None = None) -> None:
        """Handle wire change tracking for settings dialog."""
        root = root or self
        widgets = [root]
        widgets.extend(root.findChildren(QWidget))
        for widget in widgets:
            if widget.property("_wisp_dirty_connected"):
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda _text="", self=self: self._schedule_dirty_refresh())
            elif isinstance(widget, QTextEdit):
                widget.textChanged.connect(self._schedule_dirty_refresh)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(lambda _idx=0, self=self: self._schedule_dirty_refresh())
                widget.currentTextChanged.connect(lambda _text="", self=self: self._schedule_dirty_refresh())
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda _checked=False, self=self: self._schedule_dirty_refresh())
            widget.setProperty("_wisp_dirty_connected", True)

    def _refresh_dirty_state(self) -> None:
        """Refresh dirty state."""
        self._dirty_refresh_scheduled = False
        current = self._snapshot_settings()
        baseline = getattr(self, "_dirty_baseline", {})
        keys = {key for key, value in current.items() if baseline.get(key) != value}
        keys.update(key for key in baseline if current.get(key) != baseline.get(key))
        self._dirty_keys = keys

        for key, widget in self._fields.items():
            is_dirty = key in keys
            if widget.property("dirty") != is_dirty:
                widget.setProperty("dirty", is_dirty)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

        dirty_pages = {
            page for page in (self._page_for_dirty_key(key) for key in keys) if page
        }
        self._tab_dirty_names = dirty_pages
        self._refresh_tab_labels()

        apply_btn = getattr(self, "_apply_btn", None)
        if apply_btn is not None:
            apply_btn.setEnabled(bool(keys))

        if keys:
            visible_pages = [page for page in self._tab_base_names if page in dirty_pages]
            self._status_lbl.setText(t("Unsaved changes") + ": " + ", ".join(visible_pages))
        elif self._status_lbl.text().startswith(t("Unsaved changes")):
            self._status_lbl.setText("")

    def _refresh_tab_labels(self) -> None:
        """Refresh tab labels."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return
        for idx, base in enumerate(self._tab_base_names):
            suffix = " *" if base in getattr(self, "_tab_dirty_names", set()) else ""
            tabs.setTabText(idx, t(base) + suffix)

    def _refresh_search_index(self) -> None:
        """Refresh search index."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return

        def _original_text(widget: QWidget, prop_name: str) -> str:
            """Handle original text for settings dialog."""
            try:
                return str(widget.property(f"_wisp_i18n_{prop_name}") or "")
            except Exception:
                return ""

        field_pages = self._field_page_map()
        per_page_fields: dict[str, list[str]] = {}
        for key, page in field_pages.items():
            per_page_fields.setdefault(page, []).append(key)
        text_by_page: dict[str, str] = {}
        for idx, page in enumerate(self._tab_base_names):
            tab_widget = tabs.widget(idx)
            parts = [page, *per_page_fields.get(page, [])]
            for widget in tab_widget.findChildren(QWidget):
                if isinstance(widget, QLabel):
                    parts.extend([widget.text(), _original_text(widget, "text")])
                elif isinstance(widget, QLineEdit):
                    parts.extend([
                        widget.placeholderText(), _original_text(widget, "placeholder"),
                        widget.toolTip(), _original_text(widget, "tooltip"),
                        widget.text(),
                    ])
                elif isinstance(widget, QTextEdit):
                    parts.extend([
                        widget.placeholderText(), _original_text(widget, "placeholder"),
                        widget.toolTip(), _original_text(widget, "tooltip"),
                    ])
                elif isinstance(widget, QComboBox):
                    parts.extend([widget.toolTip(), _original_text(widget, "tooltip")])
                    parts.extend(widget.itemText(i) for i in range(widget.count()))
                elif isinstance(widget, QCheckBox):
                    parts.extend([
                        widget.text(), _original_text(widget, "text"),
                        widget.toolTip(), _original_text(widget, "tooltip"),
                    ])
                elif isinstance(widget, QPushButton):
                    parts.extend([
                        widget.text(), _original_text(widget, "text"),
                        widget.toolTip(), _original_text(widget, "tooltip"),
                    ])
            text_by_page[page] = " ".join(part for part in parts if part).lower()
        self._tab_search_text = text_by_page
        self._apply_settings_search()

    def _apply_settings_search(self) -> None:
        """Apply settings search."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return
        search = getattr(self, "_settings_search", None)
        query = search.text().strip().lower() if search is not None else ""
        visible: list[int] = []
        for idx, page in enumerate(self._tab_base_names):
            match = not query or query in self._tab_search_text.get(page, page.lower())
            tabs.setTabVisible(idx, match)
            if match:
                visible.append(idx)
        if query and visible and not tabs.isTabVisible(tabs.currentIndex()):
            tabs.setCurrentIndex(visible[0])
        if query:
            count = len(visible)
            self._search_status_lbl.setText(
                t("No matching settings.") if count == 0 else t("{count} matching pages.").format(count=count)
            )
            self._search_status_lbl.show()
        else:
            self._search_status_lbl.hide()

    def _set_value_for_env_key(self, key: str, value: str) -> bool:
        """Set value for env key."""
        if key in self._fields:
            widget = self._fields[key]
            if isinstance(widget, QCheckBox):
                widget.setChecked(str(value).strip().lower() == "true")
            elif isinstance(widget, (QLineEdit, QComboBox, QTextEdit)):
                _set(widget, value)
            return True

        if key == "VOICE_CONTEXT_AMBIENT" and hasattr(self, "_voice_block"):
            self._voice_block["context_ambient"].setChecked(str(value).strip().lower() == "true")
            return True
        if key == "VOICE_CONTEXT_CLIPBOARD" and hasattr(self, "_voice_block"):
            self._voice_block["context_clipboard"].setChecked(str(value).strip().lower() == "true")
            return True
        voice_mode_keys = {
            "VOICE_CONTEXT_DOCUMENTS_MODE": "context_documents_mode",
            "VOICE_CONTEXT_BROWSER_MODE": "context_browser_mode",
            "VOICE_CONTEXT_GITHUB_MODE": "context_github_mode",
            "VOICE_CONTEXT_MEMORY_MODE": "context_memory_mode",
            "VOICE_CONTEXT_SCREENSHOT": "context_screenshot",
            "VOICE_FILE_ACCESS": "file_access",
        }
        if key in voice_mode_keys and hasattr(self, "_voice_block"):
            _set(self._voice_block[voice_mode_keys[key]], value)
            return True
        if key == "VOICE_TOOLS" and hasattr(self, "_voice_block"):
            self._voice_block["tool_overrides"] = parse_tool_modes(value)
            return True

        if not key.startswith("CALLER_"):
            return False
        parts = key.split("_", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            return False
        idx = int(parts[1]) - 1
        if idx < 0 or idx >= len(getattr(self, "_caller_blocks", [])):
            return False
        blk = self._caller_blocks[idx]
        caller_key = parts[2]
        simple_widgets = {
            "HOTKEY": "hotkey",
            "LABEL": "label",
            "CUSTOM_KEY": "custom_key",
            "CUSTOM_LABEL": "custom_label",
        }
        if caller_key in simple_widgets:
            _set(blk[simple_widgets[caller_key]], value)
            return True
        if caller_key == "PASTE_BACK":
            blk["paste_back"].setChecked(str(value).strip().lower() == "true")
            return True
        if caller_key == "CONTEXT_AMBIENT":
            blk["context_ambient"].setChecked(str(value).strip().lower() == "true")
            return True
        if caller_key == "CONTEXT_CLIPBOARD":
            blk["context_clipboard"].setChecked(str(value).strip().lower() == "true")
            return True
        mode_keys = {
            "CONTEXT_DOCUMENTS_MODE": "context_documents_mode",
            "CONTEXT_BROWSER_MODE": "context_browser_mode",
            "CONTEXT_GITHUB_MODE": "context_github_mode",
            "CONTEXT_MEMORY_MODE": "context_memory_mode",
            "CONTEXT_SCREENSHOT": "context_screenshot",
            "FILE_ACCESS": "file_access",
        }
        if caller_key in mode_keys:
            _set(blk[mode_keys[caller_key]], value)
            return True
        if caller_key == "TOOLS":
            blk["tool_overrides"] = parse_tool_modes(value)
            return True
        return False

    def _apply_env_values_to_ui(self, values: dict[str, str]) -> None:
        """Apply env values to ui."""
        for section, provider_key, model_key, fallbacks_key in (
            ("LLM", "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACKS"),
            ("VISION_LLM", "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS"),
            ("MEMORY_LLM", "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS"),
        ):
            if not any(key in values for key in (provider_key, model_key, fallbacks_key)):
                continue
            rows = getattr(self, "_model_section_rows", {}).get(section, [])
            current_provider = str(rows[0]["api_key_combo"].currentData() or "") if rows else ""
            current_model = self._model_value(rows[0]) if rows else ""
            for row in list(rows):
                self._remove_model_section_row(section, row)
            self._add_model_section_row(
                section,
                values.get(provider_key, current_provider),
                values.get(model_key, current_model),
            )
            for provider, model in _parse_fallback_rows(values.get(fallbacks_key, "")):
                self._add_model_section_row(section, provider, model)

        for key, value in values.items():
            if key in {
                _SETTINGS_PRESET_KEY, "CALLER_COUNT",
                "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACKS",
                "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS",
                "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS",
            }:
                continue
            self._set_value_for_env_key(key, value)

    def _preset_values_to_persist(self, vals: dict[str, str]) -> dict[str, str]:
        """Handle preset values to persist for settings dialog."""
        slug = self._preset_slug(getattr(self, "_active_preset_slug", ""))
        if not slug:
            return {}
        preset_vals = {_SETTINGS_PRESET_KEY: slug}
        for key, value in vals.items():
            if key in secret_store.API_KEY_NAMES or key.endswith("_API_KEY"):
                continue
            preset_vals[self._preset_override_key(slug, key)] = str(value)
        return preset_vals

    def _set_context_modes(self, *, documents: str | None = None, browser: str | None = None,
                           github: str | None = None, memory: str | None = None,
                           screenshot: str | None = None, file_access: str | None = None,
                           clear_tools: bool = False) -> None:
        """Set context modes."""
        blocks = list(getattr(self, "_caller_blocks", []))
        if hasattr(self, "_voice_block"):
            blocks.append(self._voice_block)
        for blk in blocks:
            updates = {
                "context_documents_mode": documents,
                "context_browser_mode": browser,
                "context_github_mode": github,
                "context_memory_mode": memory,
                "context_screenshot": screenshot,
                "file_access": file_access,
            }
            for key, value in updates.items():
                if value is not None:
                    _set(blk[key], value)
            if clear_tools:
                blk["tool_overrides"] = {}

    def _apply_preset(self, preset: str) -> None:
        """Apply preset."""
        preset_key = self._preset_slug(preset)
        if not preset_key:
            return
        self._active_preset_slug = preset_key
        self._pending_active_profile = ""
        values = self._preset_effective_values(preset_key)
        self._apply_env_values_to_ui(values)
        self._rebuild_stt_languages()
        saved_values = self._preset_saved_values(preset_key)
        has_saved_context = any(
            key.startswith("CALLER_") or key.startswith("VOICE_")
            for key in saved_values
        )
        if not has_saved_context:
            context_defaults = _PRESET_CONTEXT_DEFAULTS.get(preset_key, {})
            self._set_context_modes(
                documents=context_defaults.get("documents"),
                browser=context_defaults.get("browser"),
                github=context_defaults.get("github"),
                memory=context_defaults.get("memory"),
                screenshot=context_defaults.get("screenshot"),
                clear_tools=context_defaults.get("clear_tools", "").lower() == "true",
            )
        self._refresh_stt_active_backend()
        self._schedule_warning_marker_refresh()
        self._schedule_dirty_refresh()
        self._status_lbl.setText(
            t("{profile} profile selected. Edits saved with Apply will update this profile.").format(
                profile=t(_PRESET_LABELS[preset_key])
            )
        )

    def _tab_llm(self) -> QWidget:
        """Handle tab LLM for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        credentials_group, credentials_layout = self._area_group(
            "Provider credentials",
            "Sign in or save provider API keys and custom endpoint details before assigning models.",
        )

        # ── AUTHENTICATION card ───────────────────────────────────────────
        auth_card, auth_cv = self._card("Authentication")

        chatgpt_hdr = QLabel("ChatGPT Plus/Pro subscription login")
        chatgpt_hdr.setStyleSheet("font-weight: 600;")
        auth_cv.addWidget(chatgpt_hdr)
        self._chatgpt_status_lbl = QLabel()
        self._chatgpt_status_lbl.setWordWrap(True)
        self._set_status_label(self._chatgpt_status_lbl, None, "Checking status...")
        auth_cv.addWidget(self._chatgpt_status_lbl)
        cgpt_row = self._button_row(
            ("Sign in",  self._chatgpt_login_browser),
            ("Sign out", self._chatgpt_logout),
        )
        btns = cgpt_row.findChildren(QPushButton)
        self._cgpt_login_btn, self._cgpt_logout_btn = btns[0], btns[1]
        auth_cv.addWidget(cgpt_row)
        auth_cv.addWidget(_sep(visible=True))

        github_hdr = QLabel("GitHub OAuth")
        github_hdr.setStyleSheet("font-weight: 600;")
        auth_cv.addWidget(github_hdr)
        auth_cv.addWidget(_desc_label("", "Sign in opens GitHub in your browser and links this app to your account."))
        self._fields["GITHUB_CLIENT_ID"] = QLineEdit()
        self._fields["GITHUB_CLIENT_ID"].setPlaceholderText("Developer OAuth app client ID override")
        self._fields["GITHUB_OAUTH_SCOPES"] = QLineEdit()
        self._fields["GITHUB_OAUTH_SCOPES"].setPlaceholderText("e.g. repo read:user user:email")
        self._github_status_lbl = QLabel()
        self._github_status_lbl.setWordWrap(True)
        self._set_status_label(self._github_status_lbl, None, "Checking status...")
        auth_cv.addWidget(self._github_status_lbl)
        github_row = self._button_row(
            ("Sign in with GitHub", self._github_login_device),
            ("Sign out",            self._github_logout),
        )
        gh_btns = github_row.findChildren(QPushButton)
        self._github_login_btn, self._github_logout_btn = gh_btns[0], gh_btns[1]
        auth_cv.addWidget(github_row)
        credentials_layout.addWidget(auth_card)

        # ── API KEYS card ─────────────────────────────────────────────────
        api_keys_card, api_keys_cv = self._card("API Keys")
        note = QLabel(
            f"<small>{t('Add a row for each provider you want to use. Alias is optional - useful when you have multiple keys for the same provider. Custom endpoints are configured below.')}</small>"
        )
        note.setWordWrap(True)
        api_keys_cv.addWidget(note)

        col_hdr_w = QWidget()
        col_hdr_h = QHBoxLayout(col_hdr_w)
        col_hdr_h.setContentsMargins(0, 0, 0, 0)
        col_hdr_h.setSpacing(8)
        for txt, stretch in [("Provider", 2), ("Alias", 2), ("API Key", 3)]:
            lbl = QLabel(f"<small><b>{t(txt)}</b></small>")
            col_hdr_h.addWidget(lbl, stretch)
        col_hdr_h.addSpacing(32)
        api_keys_cv.addWidget(col_hdr_w)

        self._api_key_rows_container = QWidget()
        self._api_key_rows_layout = QVBoxLayout(self._api_key_rows_container)
        self._api_key_rows_layout.setSpacing(4)
        self._api_key_rows_layout.setContentsMargins(0, 0, 0, 0)
        api_keys_cv.addWidget(self._api_key_rows_container)

        add_key_btn = QPushButton(t("+ Add API Key"))
        akw = QHBoxLayout()
        akw.setContentsMargins(0, 0, 0, 0)
        akw.addWidget(add_key_btn)
        akw.addStretch()
        api_keys_cv.addLayout(akw)
        add_key_btn.clicked.connect(lambda: self._add_api_key_row())

        self._copilot_status_lbl = QLabel()
        self._copilot_status_lbl.setWordWrap(True)
        self._copilot_test_btn = QPushButton()
        self._copilot_clear_btn = QPushButton()
        credentials_layout.addWidget(api_keys_card)

        # ── CUSTOM PROVIDER card ──────────────────────────────────────────
        self._fields["CUSTOM_BASE_URL"] = QLineEdit()
        self._fields["CUSTOM_BASE_URL"].setPlaceholderText("https://api.example.com/v1")
        self._fields["CUSTOM_API_KEY"] = self._password()
        self._fields["CUSTOM_API_KEY"].setPlaceholderText("Stored in OS keychain")

        custom_card, custom_cv = self._card("Custom provider")
        custom_note = QLabel(
            f"<small>{t('Any OpenAI-compatible endpoint, including Ollama and LM Studio. Select Custom in a model row below after setting the base URL.')}</small>"
        )
        custom_note.setWordWrap(True)
        custom_cv.addWidget(custom_note)

        endpoints_btn = QPushButton(t("Endpoints ▾"))
        endpoints_btn.clicked.connect(self._show_custom_endpoints_menu)
        base_url_row = QWidget()
        bur_h = QHBoxLayout(base_url_row)
        bur_h.setContentsMargins(0, 0, 0, 0)
        bur_h.setSpacing(6)
        bur_h.addWidget(self._fields["CUSTOM_BASE_URL"])
        bur_h.addWidget(endpoints_btn)

        custom_f_w = QWidget()
        custom_f = _expanding_form_layout(custom_f_w)
        custom_f.setContentsMargins(0, 0, 0, 0)
        custom_f.setSpacing(8)
        custom_f.addRow(t("Base URL"), base_url_row)
        custom_f.addRow(t("API key"), self._fields["CUSTOM_API_KEY"])
        custom_cv.addWidget(custom_f_w)

        credentials_layout.addWidget(custom_card)
        outer.addWidget(credentials_group)

        model_group, model_layout = self._area_group(
            "Model routing",
            "Choose which saved credential and model powers each purpose.",
        )

        # ── MODEL SECTIONS ─────────────────────────────────────────────────
        section_configs = [
            ("LLM",        "Chat model",  "llm_test",      self._test_primary_llm_connection),
            ("VISION_LLM", "Image model", "vision_test",   self._test_vision_connection),
            ("MEMORY_LLM", "Memory model","memory_test",   self._test_memory_connection),
        ]

        for section_key, section_title, test_attr, test_fn in section_configs:
            card, cv = self._card("")

            # header: title (left) + "Apply to all" button (right)
            hdr_w = QWidget()
            hdr_h = QHBoxLayout(hdr_w)
            hdr_h.setContentsMargins(0, 0, 0, 4)
            hdr_h.setSpacing(0)
            title_lbl = _WarningHeaderLabel(section_title.upper())
            title_lbl.setObjectName("sectionHeader")
            self._register_warning_header(section_key, title_lbl, base_text=section_title, uppercase=True)
            apply_btn = QPushButton(t("Apply to all"))
            apply_btn.setToolTip(
                "Copy this section's provider/model rows into the other model sections."
            )
            apply_btn.clicked.connect(
                lambda checked, sk=section_key: self._apply_model_section_to_all(sk)
            )
            hdr_h.addWidget(title_lbl)
            hdr_h.addStretch()
            hdr_h.addWidget(apply_btn)
            cv.addWidget(hdr_w)

            # column headers
            mch_w = QWidget()
            mch_h = QHBoxLayout(mch_w)
            mch_h.setContentsMargins(0, 0, 0, 0)
            mch_h.setSpacing(8)
            lp = QLabel(f"<small><b>{t('Priority')}</b></small>")
            lp.setFixedWidth(_MODEL_PRIORITY_COL_W)
            lp.setToolTip(t("The first row is the primary model; lower rows are fallbacks tried in priority order."))
            lk = QLabel(f"<small><b>{t('Provider')}</b></small>")
            lm = QLabel(f"<small><b>{t('Model')}</b></small>")
            mch_h.addWidget(lp)
            mch_h.addWidget(lk, 2)
            mch_h.addWidget(lm, 3)
            mch_h.addSpacing(32)
            cv.addWidget(mch_w)

            # rows container
            rows_container = QWidget()
            rows_layout = QVBoxLayout(rows_container)
            rows_layout.setSpacing(4)
            rows_layout.setContentsMargins(0, 0, 0, 0)
            cv.addWidget(rows_container)
            self._model_section_layouts[section_key] = rows_layout

            # test status + button
            test_lbl = QLabel()
            test_lbl.setWordWrap(True)
            setattr(self, f"_{test_attr}_status_lbl", test_lbl)
            test_row_w = QWidget()
            tr_h = QHBoxLayout(test_row_w)
            tr_h.setContentsMargins(0, 4, 0, 0)
            tr_h.setSpacing(8)
            test_btn = QPushButton(t(f"Test {section_title}"))
            test_btn.clicked.connect(test_fn)
            tr_h.addWidget(test_btn)
            tr_h.addWidget(test_lbl, 1)
            cv.addWidget(test_row_w)

            # add row button
            add_row_btn = QPushButton(t("+ Add row"))
            arw = QHBoxLayout()
            arw.setContentsMargins(0, 0, 0, 0)
            arw.addWidget(add_row_btn)
            arw.addStretch()
            cv.addLayout(arw)
            add_row_btn.clicked.connect(
                lambda checked, sk=section_key: self._add_model_section_row(sk)
            )
            model_layout.addWidget(card)

        advanced_group, advanced_layout = self._collapsible_group("Advanced settings")
        self._fields["CHAT_TOOL_TRACE_UI"] = QCheckBox(t("Show chat tool-loop trace"))
        self._fields["CHAT_TOOL_TRACE_UI"].setToolTip(
            "Show temporary progress lines in Chat that identify the tool loop and tool calls. "
            "Useful for testing; leave off for normal use."
        )
        self._fields["WISP_PLANNED_CHUNKING"] = QCheckBox(t("Use planned chunked replies"))
        self._fields["WISP_PLANNED_CHUNKING"].setToolTip(
            "Experimental. For eligible overlay replies, privately plans the answer and emits "
            "a few stable visible parts. Tool, file, image, and history requests keep the normal path."
        )
        self._fields["WISP_PLANNED_CHUNKING_CHUNKS"] = QLineEdit()
        self._fields["WISP_PLANNED_CHUNKING_CHUNKS"].setPlaceholderText("e.g. 3")
        self._fields["WISP_PLANNED_CHUNKING_CHUNKS"].setToolTip("Number of visible parts, clamped to 2-4.")
        self._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"] = QLineEdit()
        self._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setPlaceholderText("e.g. 80")
        self._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"].setToolTip(
            "Minimum combined prompt/context length before planned chunking can run."
        )
        reasoning_combo = _NoScrollCombo()
        reasoning_combo.setToolTip(
            "OpenAI Responses reasoning effort for chat. Provider default sends no explicit reasoning field; "
            "unsupported routes automatically retry without it."
        )
        for label, value in _CHAT_REASONING_EFFORT_OPTIONS:
            reasoning_combo.addItem(t(label), value)
        self._fields["CHAT_REASONING_EFFORT"] = reasoning_combo
        self._fields["CHAT_AUTO_ELABORATE"] = QCheckBox(t("Auto-elaborate when opening chat"))
        self._fields["CHAT_AUTO_ELABORATE"].setToolTip(
            "When enabled, opening Chat after a short overlay reply asks the model for a fuller explanation."
        )
        self._fields["CHAT_ELABORATE_PROMPT"] = QLineEdit()
        self._fields["CHAT_ELABORATE_PROMPT"].setPlaceholderText(t("e.g. Please elaborate on that."))
        self._chat_elaborate_prompt_label = _tooltip_label(
            "Elaborate prompt",
            "Prompt used when Auto-elaborate asks the model to expand the latest short response.",
        )
        advanced_form_w = QWidget()
        advanced_form = _expanding_form_layout(advanced_form_w)
        advanced_form.setSpacing(8)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow("", self._fields["CHAT_TOOL_TRACE_UI"])
        advanced_form.addRow("", self._fields["WISP_PLANNED_CHUNKING"])
        advanced_form.addRow(
            _tooltip_label("Planned reply chunks", "Number of visible parts for eligible planned replies. Runtime clamps this to 2-4."),
            self._fields["WISP_PLANNED_CHUNKING_CHUNKS"],
        )
        advanced_form.addRow(
            _tooltip_label("Planned reply min chars", "Minimum combined prompt/context length before planned chunking can run."),
            self._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"],
        )
        advanced_form.addRow(
            _tooltip_label(
                "Reasoning effort",
                "OpenAI Responses reasoning effort for chat. Unsupported models are retried without this field.",
            ),
            self._fields["CHAT_REASONING_EFFORT"],
        )
        advanced_form.addRow("", self._fields["CHAT_AUTO_ELABORATE"])
        advanced_form.addRow(self._chat_elaborate_prompt_label, self._fields["CHAT_ELABORATE_PROMPT"])
        self._fields["CHAT_AUTO_ELABORATE"].toggled.connect(  # type: ignore[attr-defined]
            self._update_chat_elaborate_prompt_visibility
        )
        self._update_chat_elaborate_prompt_visibility()
        advanced_layout.addWidget(advanced_form_w)
        model_layout.addWidget(advanced_group)
        outer.addWidget(model_group)

        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    # ---- API key row helpers ----

    def _add_api_key_row(
        self,
        provider: str = "",
        alias: str = "",
        stored: bool = False,
    ) -> dict:
        """Add api key row."""
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        provider_combo = self._combo(
            ["groq", "openai", "anthropic", "google", "deepseek",
             "openrouter", "mistral", "xai", "together", "cerebras",
             "zai", "nvidia", "sambanova", "github_models", "huggingface",
             "chutes", "vercel", "fireworks", "cohere", "ai21", "nebius",
             "ollama", "custom", "copilot"],
            provider,
        )
        provider_combo.setMinimumWidth(120)

        alias_edit = QLineEdit(alias)
        alias_edit.setPlaceholderText(t("alias (optional)"))
        alias_edit.setMinimumWidth(80)

        key_edit = self._password()
        key_edit.setPlaceholderText(t("stored in keychain") if stored else t("enter API key"))

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(40)
        remove_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")

        h.addWidget(provider_combo, 2)
        h.addWidget(alias_edit, 2)
        h.addWidget(key_edit, 3)
        h.addWidget(remove_btn)

        row_info: dict = {
            "widget":   row_w,
            "provider": provider_combo,
            "alias":    alias_edit,
            "key":      key_edit,
        }
        self._sync_api_key_row_placeholder(row_info, stored=stored)

        remove_btn.clicked.connect(lambda: self._remove_api_key_row(row_info))
        provider_combo.currentIndexChanged.connect(lambda _: self._sync_api_key_row_placeholder(row_info))
        provider_combo.currentIndexChanged.connect(lambda _: self._refresh_model_api_key_combos())
        alias_edit.textChanged.connect(lambda _: self._refresh_model_api_key_combos())

        self._api_key_rows_layout.addWidget(row_w)
        self._api_key_rows.append(row_info)
        self._refresh_model_api_key_combos()
        self._wire_change_tracking(row_w)
        self._refresh_search_index()
        self._schedule_dirty_refresh()
        return row_info

    def _sync_api_key_row_placeholder(self, row_info: dict, *, stored: bool = False) -> None:
        """Refresh provider-specific API key placeholder text."""
        provider = _get(row_info["provider"])
        key_edit = row_info["key"]
        if provider == "copilot":
            key_edit.setPlaceholderText(t("stored in keychain") if stored else t("github_pat_… (not saved to .env)"))
        elif provider == "ollama":
            key_edit.setPlaceholderText(t("not required"))
        elif provider == "custom":
            key_edit.setPlaceholderText(t("stored in keychain") if stored else t("custom endpoint API key"))
        else:
            key_edit.setPlaceholderText(t("stored in keychain") if stored else t("enter API key"))

    def _remove_api_key_row(self, row_info: dict) -> None:
        """Remove api key row."""
        if row_info in self._api_key_rows:
            self._api_key_rows.remove(row_info)
        row_info["widget"].deleteLater()
        self._refresh_model_api_key_combos()
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _get_api_key_display_options(self) -> "list[tuple[str, str]]":
        """Return api key display options."""
        options: list[tuple[str, str]] = []
        for row in self._api_key_rows:
            provider = _get(row["provider"])
            alias = row["alias"].text().strip()
            label = t(_PROVIDER_LABELS.get(provider, provider))
            display = f"{label} ({alias})" if alias else label
            options.append((display, provider))
        # OAuth/keychain providers — always available regardless of API key rows
        options.append((t(_PROVIDER_LABELS.get("chatgpt", "ChatGPT Plus/Pro (OAuth subscription)")), "chatgpt"))
        if not any(provider == "copilot" for _display, provider in options):
            options.append((t(_PROVIDER_LABELS.get("copilot", "GitHub Copilot") + " (keychain token)"), "copilot"))
        if not any(provider == "custom" for _display, provider in options):
            options.append((t(_PROVIDER_LABELS.get("custom", "Custom (OpenAI-compatible)")), "custom"))
        return options

    def _credential_availability(self) -> "dict[str, tuple[bool, str]]":
        """Map OAuth/keychain providers to (available, hint-when-unavailable).

        Routes that authenticate via sign-in (chatgpt, copilot) are only usable
        once signed in. Copilot also accepts the GitHub OAuth login as a fallback
        token, so signing into GitHub enables it. Anything not listed is treated
        as always available. On any lookup error we fail open (available) so a
        keychain hiccup never hides a provider the user has configured.
        """
        avail: dict[str, tuple[bool, str]] = {}
        try:
            from core.auth import chatgpt as chatgpt_auth
            avail["chatgpt"] = (bool(chatgpt_auth.get_tokens()), t("sign in first"))
        except Exception:  # noqa: BLE001 — fail open
            avail["chatgpt"] = (True, "")
        try:
            from core.auth import copilot_auth
            avail["copilot"] = (
                copilot_auth.has_effective_token(),
                t("sign in to GitHub or add a Copilot token first"),
            )
        except Exception:  # noqa: BLE001 — fail open
            avail["copilot"] = (True, "")
        return avail

    def _fill_credential_combo(self, combo, current: str | None = None) -> None:
        """Populate a model-route credential combo, disabling un-signed-in routes.

        Unavailable entries (e.g. ChatGPT/Copilot when not signed in) stay in the
        list but are greyed out with a hint, so a route already pointed at one
        still displays its value while the inline route warning explains the fix.
        """
        options = self._get_api_key_display_options()
        avail = self._credential_availability()
        combo.blockSignals(True)
        combo.clear()
        for display, provider in options:
            enabled, hint = avail.get(provider, (True, ""))
            label = display if enabled else (f"{display} — {hint}" if hint else display)
            combo.addItem(label, provider)
            if not enabled:
                item = combo.model().item(combo.count() - 1)
                if item is not None:
                    item.setEnabled(False)
                    if hint:
                        item.setToolTip(hint)
        target = (current or "").strip()
        idx = combo.findData(target) if target else -1
        if target and idx < 0:
            # The configured route points at a provider with no saved credential
            # yet — common on a fresh install where the default Chat route is
            # openai but no key has been entered. Show that provider with an
            # actionable hint instead of silently landing on a disabled OAuth
            # entry (e.g. Copilot "sign in first"), which looks like a warning the
            # user never triggered and also misfires the subscription markers.
            label = t(_PROVIDER_LABELS.get(target, target))
            combo.addItem(f"{label} — {t('add an API key below')}", target)
            idx = combo.count() - 1
        elif not target:
            # No provider chosen (e.g. an Image/Memory model inheriting the Chat
            # model). Offer a neutral entry so the combo doesn't default onto a
            # disabled OAuth provider and raise a spurious credential warning.
            idx = combo.findData("")
            if idx < 0:
                combo.insertItem(0, t("Not set"), "")
                idx = 0
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _refresh_model_api_key_combos(self) -> None:
        """Refresh model api key combos."""
        for section_rows in self._model_section_rows.values():
            for row in section_rows:
                combo = row["api_key_combo"]
                self._fill_credential_combo(combo, combo.currentData())

    # ---- Model section row helpers ----

    def _add_model_section_row(
        self,
        section_key: str,
        provider: str = "",
        model: str = "",
    ) -> dict:
        """Add model section row."""
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        priority_lbl = QLabel()
        priority_lbl.setFixedWidth(_MODEL_PRIORITY_COL_W)
        priority_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        api_key_combo = _NoScrollCombo()
        api_key_combo.setMinimumWidth(140)
        self._fill_credential_combo(api_key_combo, provider or None)

        # Model cell: a non-editable combo (curated list + a "Custom / enter
        # manually…" sentinel) stacked over a hidden line edit that appears only
        # when the sentinel is chosen.
        model_container = QWidget()
        mc_v = QVBoxLayout(model_container)
        mc_v.setContentsMargins(0, 0, 0, 0)
        mc_v.setSpacing(2)
        model_combo = _NoScrollCombo()
        model_combo.setMinimumWidth(140)
        model_edit = QLineEdit()
        model_edit.hide()
        mc_v.addWidget(model_combo)
        mc_v.addWidget(model_edit)

        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedWidth(34)
        refresh_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")
        refresh_btn.setToolTip("Fetch the latest model names from the provider")

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(40)
        remove_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")

        h.addWidget(priority_lbl)
        h.addWidget(api_key_combo, 2)
        h.addWidget(model_container, 3)
        h.addWidget(refresh_btn)
        h.addWidget(remove_btn)

        row_info: dict = {
            "widget":        row_w,
            "priority_lbl":  priority_lbl,
            "api_key_combo": api_key_combo,
            "model_combo":   model_combo,
            "model_edit":    model_edit,
            "refresh_btn":   refresh_btn,
        }

        # Populate with the curated list for this provider; refresh fetches live.
        # For a blank "+ Add row" the combo still defaults to the first provider,
        # so fall back to its current selection rather than leaving the list empty.
        effective_provider = provider or (api_key_combo.currentData() or "")
        self._fill_model_combo(
            row_info, _PROVIDER_MODELS.get(effective_provider, []), effective_provider, model
        )

        model_combo.currentIndexChanged.connect(
            lambda _: self._on_model_combo_changed(row_info)
        )

        def _on_key_change():
            """Handle key change events."""
            p = api_key_combo.currentData() or ""
            self._fill_model_combo(
                row_info, _PROVIDER_MODELS.get(p, []), p, self._model_value(row_info)
            )
            self._schedule_warning_marker_refresh()

        api_key_combo.currentIndexChanged.connect(lambda _: _on_key_change())
        model_combo.currentIndexChanged.connect(lambda _: self._schedule_warning_marker_refresh())
        model_edit.textChanged.connect(lambda _: self._schedule_warning_marker_refresh())
        refresh_btn.clicked.connect(lambda: self._refresh_models_for_row(row_info))
        remove_btn.clicked.connect(
            lambda: self._remove_model_section_row(section_key, row_info)
        )

        self._model_section_layouts[section_key].addWidget(row_w)
        self._model_section_rows[section_key].append(row_info)
        self._relabel_section_priorities(section_key)
        self._wire_change_tracking(row_w)
        self._refresh_search_index()
        self._schedule_dirty_refresh()
        return row_info

    def _relabel_section_priorities(self, section_key: str) -> None:
        """Number each model row by priority — row 1 is primary, the rest fallbacks."""
        rows = self._model_section_rows.get(section_key, [])
        for idx, row in enumerate(rows):
            lbl = row.get("priority_lbl")
            if lbl is None:
                continue
            rank = idx + 1
            if idx == 0:
                lbl.setText(f"<b>{rank}</b>")
                lbl.setToolTip(t("Priority {n} — primary model").format(n=rank))
            else:
                lbl.setText(str(rank))
                lbl.setToolTip(t("Priority {n} — fallback (tried if the rows above fail)").format(n=rank))

    def _fill_model_combo(
        self, row_info: dict, models: list, provider: str, selected: str
    ) -> None:
        """Repopulate a row's model combo with *models* plus the Custom sentinel,
        preserving/applying the *selected* value (custom text routes to the edit)."""
        combo = row_info["model_combo"]
        edit = row_info["model_edit"]
        combo.blockSignals(True)
        combo.clear()
        for m in models:
            combo.addItem(m, m)
        combo.addItem(_CUSTOM_MODEL_LABEL, _CUSTOM_MODEL_SENTINEL)
        selected = (selected or "").strip()
        if selected and selected in models:
            combo.setCurrentIndex(combo.findData(selected))
            edit.clear()
            edit.hide()
        elif selected:
            combo.setCurrentIndex(combo.findData(_CUSTOM_MODEL_SENTINEL))
            edit.setText(selected)
            edit.show()
        else:
            combo.setCurrentIndex(-1)
            edit.hide()
        edit.setPlaceholderText(_model_hint(provider) if provider else "model name")
        completer = QCompleter(models, edit)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        edit.setCompleter(completer)
        combo.blockSignals(False)

    def _on_model_combo_changed(self, row_info: dict) -> None:
        """Handle model combo changed events."""
        combo = row_info["model_combo"]
        edit = row_info["model_edit"]
        if combo.currentData() == _CUSTOM_MODEL_SENTINEL:
            edit.show()
            edit.setFocus()
        else:
            edit.hide()

    def _model_value(self, row_info: dict) -> str:
        """Effective model string for a section row: the line-edit text when the
        Custom sentinel is selected, otherwise the chosen combo item."""
        combo = row_info["model_combo"]
        if combo.currentData() == _CUSTOM_MODEL_SENTINEL:
            return row_info["model_edit"].text().strip()
        return (combo.currentData() or "").strip()

    def _refresh_models_for_row(self, row_info: dict) -> None:
        """Fetch live model names for the row's provider on a background thread."""
        provider = (row_info["api_key_combo"].currentData() or "").strip()
        refresh_btn = row_info["refresh_btn"]
        if not provider:
            refresh_btn.setToolTip("Pick a provider first")
            return
        api_key = self._effective_secret_value_from_provider(provider)
        base_url = _get(self._fields["CUSTOM_BASE_URL"]).strip() if provider == "custom" else ""

        refresh_btn.setEnabled(False)
        refresh_btn.setText("…")

        carrier = _ModelFetchSignals()
        carrier.done.connect(
            lambda models, err, ri=row_info: self._on_models_fetched(ri, models, err)
        )
        row_info["_fetch_carrier"] = carrier  # keep alive until fetch completes

        def _worker():
            """Handle worker for settings dialog."""
            try:
                from core.llm_clients import client as llm
                models = llm.list_models(provider, api_key=api_key, base_url=base_url)
                carrier.done.emit(models, "")
            except Exception as exc:  # noqa: BLE001 — surfaced to the user as a tooltip
                carrier.done.emit([], str(exc))

        threading.Thread(target=_worker, daemon=True, name="model-list-fetch").start()

    def _on_models_fetched(self, row_info: dict, models, err: str) -> None:
        """Handle models fetched events."""
        refresh_btn = row_info["refresh_btn"]
        refresh_btn.setEnabled(True)
        refresh_btn.setText("↻")
        row_info.pop("_fetch_carrier", None)
        provider = (row_info["api_key_combo"].currentData() or "").strip()
        if err or not models:
            refresh_btn.setToolTip(
                f"Couldn't fetch — showing built-ins ({err})" if err
                else "Provider returned no models — showing built-ins"
            )
            return
        # Live list only; preserve the row's current selection.
        self._fill_model_combo(row_info, list(models), provider, self._model_value(row_info))
        refresh_btn.setToolTip(f"Live: {len(models)} models")

    def _remove_model_section_row(self, section_key: str, row_info: dict) -> None:
        """Remove model section row."""
        rows = self._model_section_rows[section_key]
        if row_info in rows:
            rows.remove(row_info)
        row_info["widget"].deleteLater()
        self._relabel_section_priorities(section_key)
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _apply_model_section_to_all(self, source_key: str) -> None:
        """Apply model section to all."""
        source_rows = self._model_section_rows[source_key]
        for sk in list(self._model_section_rows):
            if sk == source_key:
                continue
            for row in list(self._model_section_rows[sk]):
                self._remove_model_section_row(sk, row)
            for row in source_rows:
                provider = row["api_key_combo"].currentData() or ""
                model = self._model_value(row)
                self._add_model_section_row(sk, provider, model)

    def _effective_secret_value_from_provider(self, provider: str) -> str:
        """Handle effective secret value from provider for settings dialog."""
        if provider == "custom":
            field = self._fields.get("CUSTOM_API_KEY")
            typed = _get(field).strip() if field is not None else ""
            return typed or secret_store.get_keychain_secret("CUSTOM_API_KEY") or ""
        if provider == "copilot":
            for row in self._api_key_rows:
                if _get(row["provider"]) == provider:
                    typed = row["key"].text().strip()
                    if typed:
                        return typed
            try:
                from core.auth import copilot_auth
                return copilot_auth.get_token() or ""
            except Exception:
                return ""
        key_name = _PROVIDER_KEY_NAMES.get(provider, "")
        if not key_name:
            return ""
        for row in self._api_key_rows:
            if _get(row["provider"]) == provider:
                typed = row["key"].text().strip()
                if typed:
                    return typed
        return secret_store.get_keychain_secret(key_name) or ""

    # ---- Custom provider helpers ----

    _CUSTOM_ENDPOINTS: list[tuple[str, str, str, str]] = [
        ("DeepSeek",     "https://api.deepseek.com/v1",          "deepseek-chat", ""),
        ("OpenRouter",   "https://openrouter.ai/api/v1",         "openai/gpt-4o", ""),
        ("Mistral",      "https://api.mistral.ai/v1",            "mistral-large-latest", ""),
        ("xAI (Grok)",   "https://api.x.ai/v1",                  "grok-3", ""),
        ("Together AI",  "https://api.together.xyz/v1",          "meta-llama/Llama-3-70b-chat-hf", ""),
        ("Cerebras",     "https://api.cerebras.ai/v1",           "llama-3.3-70b", ""),
        ("Z.AI / GLM",   "https://api.z.ai/api/paas/v4",         "glm-4.7-flash", ""),
        ("NVIDIA",       "https://integrate.api.nvidia.com/v1",  "meta/llama-3.3-70b-instruct", ""),
        ("SambaNova",    "https://api.sambanova.ai/v1",          "Meta-Llama-3.1-8B-Instruct", ""),
        ("GitHub Models", "https://models.github.ai/inference",  "openai/gpt-4.1-mini", ""),
        ("Hugging Face", "https://router.huggingface.co/v1",     "meta-llama/Llama-3.1-8B-Instruct", ""),
        ("Chutes",       "https://llm.chutes.ai/v1",             "deepseek-ai/DeepSeek-V3-0324", ""),
        ("Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "openai/gpt-4o-mini", ""),
        ("Fireworks",    "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p1-8b-instruct", ""),
        ("Cohere",       "https://api.cohere.ai/compatibility/v1", "command-r-plus", ""),
        ("AI21",         "https://api.ai21.com/studio/v1",       "jamba-large", ""),
        ("Nebius",       "https://api.studio.nebius.com/v1",     "meta-llama/Meta-Llama-3.1-8B-Instruct", ""),
        ("Cloudflare Workers AI", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "@cf/meta/llama-3.1-8b-instruct", ""),
        ("Baseten",      "https://model-{model_id}.api.baseten.co/environments/production/sync/v1", "model-name", ""),
        ("Ollama (local)", "http://localhost:11434/v1",           "llama3", "ollama"),
        ("LM Studio (local)", "http://localhost:1234/v1",        "local-model", ""),
    ]

    def _show_custom_endpoints_menu(self) -> None:
        """Show custom provider endpoint shortcuts."""
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction

        menu = QMenu(self)
        for name, url, model_hint, api_key_hint in self._CUSTOM_ENDPOINTS:
            action = QAction(name, self)
            action.setToolTip(url)
            action.triggered.connect(
                lambda checked, u=url, h=model_hint, k=api_key_hint: self._apply_custom_preset(u, h, k)
            )
            menu.addAction(action)
        btn = self.sender()
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _apply_custom_preset(self, base_url: str, model_hint: str, api_key_hint: str = "") -> None:
        """Apply custom preset."""
        self._fields["CUSTOM_BASE_URL"].setText(base_url)
        if api_key_hint:
            self._fields["CUSTOM_API_KEY"].setText(api_key_hint)
        for section_rows in self._model_section_rows.values():
            for row in section_rows:
                if (row["api_key_combo"].currentData() or "") == "custom":
                    row["model_edit"].setPlaceholderText(f"e.g. {model_hint}")

    def _refresh_chatgpt_status(self) -> None:
        """Refresh chatgpt status."""
        try:
            from core.auth import chatgpt as chatgpt_auth
            tokens = chatgpt_auth.get_tokens()
            if tokens:
                aid = tokens.get("account_id") or ""
                label = "Logged in" + (f" \u2022 account {aid[:8]}\u2026" if aid else "")
                self._chatgpt_status_lbl.setText(_translate_status_message(label))
                self._chatgpt_status_lbl.setStyleSheet("color: #80c080;")
            else:
                self._chatgpt_status_lbl.setText(t("Not logged in"))
                self._chatgpt_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        except Exception as exc:
            self._chatgpt_status_lbl.setText(_translate_status_message(f"Error reading status: {exc}"))
            self._chatgpt_status_lbl.setStyleSheet("color: #c04040;")

    def _chatgpt_login_browser(self) -> None:
        """Handle chatgpt login browser for settings dialog."""
        from core.auth import chatgpt as chatgpt_auth
        self._chatgpt_status_lbl.setText(t("Opening browser\u2026 waiting for callback"))
        self._chatgpt_status_lbl.setStyleSheet("color: #c0c040;")
        self._start_auth_poll()

        def on_success(_tokens):
            """Handle success events."""
            pass  # polling timer will detect the saved tokens

        def on_error(msg):
            """Handle error events."""
            self._auth_poll_error = msg  # picked up by poll tick

        chatgpt_auth.start_browser_login(on_success, on_error)

    def _start_auth_poll(self) -> None:
        """Start a 1-second main-thread timer that detects when OAuth tokens land."""
        self._auth_poll_error: str | None = None
        self._auth_poll_ticks = 0
        self._auth_poll_timer = QTimer(self)
        self._auth_poll_timer.setInterval(1000)
        self._auth_poll_timer.timeout.connect(self._auth_poll_tick)
        self._auth_poll_timer.start()

    def _auth_poll_tick(self) -> None:
        # Check if the background thread stored a message
        """Handle auth poll tick for settings dialog."""
        if self._auth_poll_error is not None:
            msg = self._auth_poll_error
            self._auth_poll_error = None  # clear so we don't re-trigger
            self._auth_poll_timer.stop()
            self._chatgpt_status_lbl.setText(_translate_status_message(f"Error: {msg}"))
            self._chatgpt_status_lbl.setStyleSheet("color: #c04040;")
            return
        # Check if tokens have appeared in the keychain
        try:
            from core.auth import chatgpt as chatgpt_auth
            if chatgpt_auth.get_tokens():
                self._auth_poll_timer.stop()
                self._refresh_chatgpt_status()
                self._refresh_model_api_key_combos()
                return
        except Exception:
            pass
        # Timeout after 5 minutes
        self._auth_poll_ticks += 1
        if self._auth_poll_ticks >= 300:
            self._auth_poll_timer.stop()
            self._chatgpt_status_lbl.setText(t("Timed out waiting for login"))
            self._chatgpt_status_lbl.setStyleSheet("color: #c04040;")

    def _chatgpt_logout(self) -> None:
        """Handle chatgpt logout for settings dialog."""
        try:
            from core.auth import chatgpt as chatgpt_auth
            chatgpt_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_chatgpt_status()
        self._refresh_model_api_key_combos()

    def _refresh_github_status(self) -> None:
        """Refresh github status."""
        try:
            from core.auth import github as github_auth
            tokens = github_auth.get_tokens()
            if tokens:
                login = (tokens.get("user") or {}).get("login") or ""
                scopes = tokens.get("scope") or ""
                label = "Logged in" + (f" as {login}" if login else "")
                if scopes:
                    label += f"\nScopes: {scopes}"
                self._github_status_lbl.setText(_translate_status_message(label))
                self._github_status_lbl.setStyleSheet("color: #80c080;")
            else:
                self._github_status_lbl.setText(t("Not logged in"))
                self._github_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        except Exception as exc:
            self._github_status_lbl.setText(_translate_status_message(f"Error reading status: {exc}"))
            self._github_status_lbl.setStyleSheet("color: #c04040;")

    def _github_login_device(self) -> None:
        """Handle github login device for settings dialog."""
        import webbrowser
        import config as cfg
        from core.auth import github as github_auth

        override_client_id = _get(self._fields["GITHUB_CLIENT_ID"]).strip()
        cfg.GITHUB_CLIENT_ID = override_client_id or getattr(cfg, "GITHUB_DEFAULT_CLIENT_ID", "")
        cfg.GITHUB_OAUTH_SCOPES = _get(self._fields["GITHUB_OAUTH_SCOPES"]).strip()
        if not github_auth.has_configured_client_id():
            self._github_status_lbl.setText(t(
                "This build does not include a GitHub OAuth app client ID yet."
            ))
            self._github_status_lbl.setStyleSheet("color: #c04040;")
            return

        self._github_status_lbl.setText(t("Starting GitHub device auth..."))
        self._github_status_lbl.setStyleSheet("color: #c0c040;")
        self._start_github_auth_poll()

        def on_code(url, user_code):
            """Handle code events."""
            self._github_auth_poll_message = f"__device_code__{url}\n{user_code}"
            try:
                webbrowser.open(url)
            except Exception:
                pass

        def on_success(_tokens):
            """Handle success events."""
            pass

        def on_error(msg):
            """Handle error events."""
            self._github_auth_poll_message = msg

        github_auth.start_device_login(on_code, on_success, on_error)

    def _start_github_auth_poll(self) -> None:
        """Start github auth poll."""
        self._github_auth_poll_message: str | None = None
        self._github_auth_poll_ticks = 0
        self._github_auth_poll_timer = QTimer(self)
        self._github_auth_poll_timer.setInterval(1000)
        self._github_auth_poll_timer.timeout.connect(self._github_auth_poll_tick)
        self._github_auth_poll_timer.start()

    def _github_auth_poll_tick(self) -> None:
        """Handle github auth poll tick for settings dialog."""
        if self._github_auth_poll_message is not None:
            msg = self._github_auth_poll_message
            self._github_auth_poll_message = None
            if msg.startswith("__device_code__"):
                body = msg[len("__device_code__"):]
                url, _, code = body.partition("\n")
                self._github_status_lbl.setText(f"{t('Go to:')} {url}\n{t('Enter code:')} {code}")
                self._github_status_lbl.setStyleSheet("color: #80a0ff;")
                return
            self._github_auth_poll_timer.stop()
            self._github_status_lbl.setText(_translate_status_message(f"Error: {msg}"))
            self._github_status_lbl.setStyleSheet("color: #c04040;")
            return
        try:
            from core.auth import github as github_auth
            if github_auth.get_tokens():
                self._github_auth_poll_timer.stop()
                self._refresh_github_status()
                # GitHub login also enables the Copilot route (OAuth fallback token).
                self._refresh_copilot_status()
                self._refresh_model_api_key_combos()
                return
        except Exception:
            pass
        self._github_auth_poll_ticks += 1
        if self._github_auth_poll_ticks >= 900:
            self._github_auth_poll_timer.stop()
            self._github_status_lbl.setText(t("Timed out waiting for GitHub login"))
            self._github_status_lbl.setStyleSheet("color: #c04040;")

    def _github_logout(self) -> None:
        """Handle github logout for settings dialog."""
        try:
            from core.auth import github as github_auth
            github_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_github_status()
        # Signing out of GitHub may disable the Copilot route's fallback token.
        self._refresh_copilot_status()
        self._refresh_model_api_key_combos()

    def _refresh_copilot_status(self) -> None:
        """Refresh copilot status."""
        try:
            from core.auth import copilot_auth
            stored, message = copilot_auth.token_status()
            self._copilot_status_lbl.setText(t(message))
            self._copilot_status_lbl.setStyleSheet(
                "color: #80c080;" if stored else "color: palette(placeholder-text);"
            )
        except Exception as exc:
            self._copilot_status_lbl.setText(_translate_status_message(f"Keychain error: {exc}"))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")

    def _copilot_save_token(self) -> None:
        """Handle copilot save token for settings dialog."""
        try:
            from core.auth import copilot_auth
            token = ""
            for row in self._api_key_rows:
                if _get(row["provider"]) == "copilot":
                    token = row["key"].text()
                    if token.strip():
                        copilot_auth.save_token(token)
                        row["key"].clear()
                        row["key"].setPlaceholderText(t("stored in keychain"))
                        break
            if not token.strip():
                raise ValueError("Add a GitHub Copilot provider row and paste a token first.")
            self._refresh_copilot_status()
            self._refresh_model_api_key_combos()
        except Exception as exc:
            self._copilot_status_lbl.setText(t(str(exc)))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")
            QMessageBox.warning(self, t("GitHub Copilot token"), str(exc))

    def _copilot_clear_token(self) -> None:
        """Handle copilot clear token for settings dialog."""
        try:
            from core.auth import copilot_auth
            copilot_auth.clear_token()
            for row in self._api_key_rows:
                if _get(row["provider"]) == "copilot":
                    row["key"].clear()
                    self._sync_api_key_row_placeholder(row)
            self._refresh_copilot_status()
            self._refresh_model_api_key_combos()
        except Exception as exc:
            self._copilot_status_lbl.setText(t(str(exc)))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")
            QMessageBox.warning(self, t("GitHub Copilot token"), str(exc))

    def _copilot_test_token(self) -> None:
        """Handle copilot test token for settings dialog."""
        try:
            from core.auth import copilot_client
            ok, message = copilot_client.test_copilot_token()
            self._copilot_status_lbl.setText(t(message))
            self._copilot_status_lbl.setStyleSheet(
                "color: #80c080;" if ok else "color: #c04040;"
            )
        except Exception as exc:
            self._copilot_status_lbl.setText(_translate_status_message(f"Test failed: {exc}"))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")

    def _tab_tts(self) -> QWidget:
        """Handle tab TTS for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── PROVIDER card ─────────────────────────────────────────────────
        provider_card, provider_cv = self._card("Provider")
        self._fields["TTS_PROVIDER"] = self._combo(
            ["cartesia", "elevenlabs", "openai", "openai_compatible", "gpt_sovits", "kokoro", "none"]
        )
        tts_provider_tip = "Choose which service speaks assistant replies. None disables generated voice output."
        self._fields["TTS_PROVIDER"].currentIndexChanged.connect(
            lambda *_: self._update_tts_provider_fields()
        )
        self._fields["TTS_SPEAK_REPLIES"] = QCheckBox(t("Speak assistant replies automatically"))
        tts_speak_replies_tip = (
            "When off, configured voices are still available for read-selection-aloud and Test TTS."
        )
        pf_w = QWidget()
        pf = _expanding_form_layout(pf_w)
        pf.setContentsMargins(0, 0, 0, 0)
        pf.setSpacing(8)
        pf.addRow(_tooltip_label("TTS Provider", tts_provider_tip), self._fields["TTS_PROVIDER"])
        pf.addRow(
            _tooltip_label("Auto-speak replies", tts_speak_replies_tip),
            self._fields["TTS_SPEAK_REPLIES"],
        )
        self._tts_timing_notice_lbl = QLabel(t(_TTS_TIMING_NOTICE))
        self._tts_timing_notice_lbl.setObjectName("ttsTimingNotice")
        self._tts_timing_notice_lbl.setWordWrap(True)
        self._tts_timing_notice_lbl.setStyleSheet("color: #d8932a;")
        pf.addRow("", self._tts_timing_notice_lbl)
        provider_cv.addWidget(pf_w)
        outer.addWidget(provider_card)

        # ── SPEECH-TO-TEXT card ──────────────────────────────────────────
        stt_card, stt_cv = self._card("Speech to Text")
        stt_note_text = (
            "Whisper model settings for hold-to-talk transcription. "
            "Larger models improve Mandarin/Cantonese speech accuracy but use more disk and CPU."
        )
        stt_note = QLabel(
            f"<small>{t(stt_note_text)}</small>"
        )
        stt_note.setWordWrap(True)
        stt_cv.addWidget(stt_note)

        stt_first_use = QLabel(
            "<small>"
            + t(
                "First use downloads the model (about 150 MB for base) and needs "
                "internet once. Use the button below to download it ahead of time."
            )
            + "</small>"
        )
        stt_first_use.setWordWrap(True)
        stt_cv.addWidget(stt_first_use)

        stt_model = _NoScrollCombo()
        stt_model.setProperty("allow_custom_saved_value", True)
        for label, model, translation_key in _STT_MODEL_OPTIONS:
            stt_model.addItem(label, model)
            stt_model.setItemData(stt_model.count() - 1, translation_key, COMBO_I18N_SOURCE_ROLE)
        stt_model_tip = (
            "Local faster-whisper model. small is a good first upgrade for Chinese; "
            "medium/large-v3 are heavier."
        )
        self._fields["STT_MODEL"] = stt_model

        stt_compute = _NoScrollCombo()
        for label, value in _STT_COMPUTE_OPTIONS:
            stt_compute.addItem(label, value)
        stt_compute_tip = "Whisper compute precision. Keep int8 for CPU unless you know you need another mode."
        self._fields["STT_COMPUTE_TYPE"] = stt_compute

        stt_language = _NoScrollCombo()
        for label, value in _STT_LANGUAGE_OPTIONS:
            stt_language.addItem(label, value)
        stt_language_tip = (
            "Recognition language for hold-to-talk. Auto-detect is useful if you switch languages often."
        )
        self._fields["STT_LANGUAGE"] = stt_language
        # Cantonese (yue) only exists in large-v3; rebuild the language list
        # whenever the model changes so it can't be paired with a model that
        # can't decode it.
        stt_model.currentIndexChanged.connect(lambda *_: self._rebuild_stt_languages())

        stt_beam = _NoScrollCombo()
        for label, value in _STT_BEAM_OPTIONS:
            stt_beam.addItem(label, value)
        stt_beam_tip = (
            "Decoding beam width. 5 (Whisper's default) is noticeably more accurate than greedy (1) "
            "for a small speed cost; raise it for tricky audio."
        )
        self._fields["STT_BEAM_SIZE"] = stt_beam

        stt_device = _NoScrollCombo()
        for label, value in _STT_DEVICE_OPTIONS:
            stt_device.addItem(label, value)
        stt_device_tip = (
            "Where Whisper runs. GPU (CUDA) is much faster, especially for large-v3, but needs an "
            "NVIDIA GPU with CUDA installed. Auto uses the GPU when present and falls back to CPU."
        )
        self._fields["STT_DEVICE"] = stt_device

        stt_fw = QWidget()
        stt_f = _expanding_form_layout(stt_fw)
        stt_f.setContentsMargins(0, 0, 0, 0)
        stt_f.setSpacing(8)
        stt_f.addRow(_tooltip_label("Whisper model", stt_model_tip), self._fields["STT_MODEL"])
        stt_f.addRow(_tooltip_label("Device", stt_device_tip), self._fields["STT_DEVICE"])
        stt_f.addRow(_tooltip_label("Compute type", stt_compute_tip), self._fields["STT_COMPUTE_TYPE"])
        stt_f.addRow(_tooltip_label("Speech language", stt_language_tip), self._fields["STT_LANGUAGE"])
        stt_f.addRow(_tooltip_label("Beam size", stt_beam_tip), self._fields["STT_BEAM_SIZE"])
        stt_cv.addWidget(stt_fw)

        stt_hint = QLabel(
            "<small>"
            + t(
                "Hold the voice hotkey while you speak, then release. Hold for at "
                "least half a second and speak clearly; very short or silent taps "
                "are skipped."
            )
            + "</small>"
        )
        stt_hint.setWordWrap(True)
        stt_cv.addWidget(stt_hint)

        # Backend readout. Avoid importing core.stt while building Settings:
        # that pulls in NumPy/faster-whisper and can freeze the Qt UI thread.
        # If STT is already loaded in this process, the label below can still
        # show the live backend; otherwise it shows the configured request.
        stt_status_row = QWidget()
        ssr = QHBoxLayout(stt_status_row)
        ssr.setContentsMargins(0, 0, 0, 0)
        ssr.setSpacing(8)
        self._stt_active_lbl = QLabel()
        self._stt_active_lbl.setWordWrap(True)
        self._stt_download_btn = QPushButton(t("Download / load model now"))
        self._stt_download_btn.setToolTip(
            t(
                "Download and load the speech model now so the first hold-to-talk "
                "does not stall. The first download needs an internet connection."
            )
        )
        self._stt_download_btn.clicked.connect(self._preload_stt_model)
        stt_recheck = QPushButton(t("Recheck"))
        stt_recheck.setToolTip(
            t("Refresh the speech backend readout without loading the speech model.")
        )
        stt_recheck.clicked.connect(self._refresh_stt_active_backend)
        ssr.addWidget(self._stt_active_lbl, 1)
        ssr.addWidget(self._stt_download_btn, 0)
        ssr.addWidget(stt_recheck, 0)
        stt_cv.addWidget(stt_status_row)
        self._refresh_stt_active_backend()

        outer.addWidget(stt_card)

        # ── PROVIDER SETTINGS card ────────────────────────────────────────
        # Only the rows for the selected provider are shown (toggled by
        # _update_tts_provider_fields), so each provider gets the space it needs
        # for its key / voice / model without cluttering the others.
        keys_card, keys_cv = self._card("Voice & API key")
        tts_key_note = QLabel(
            f"<small>{t('API keys are saved to the OS keychain. Leave blank to keep the stored key.')}</small>"
        )
        tts_key_note.setWordWrap(True)
        keys_cv.addWidget(tts_key_note)

        # Fields shared/created up front so save + load can always reach them.
        self._fields["CARTESIA_API_KEY"] = self._password()
        self._fields["CARTESIA_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["CARTESIA_VOICE_ID"] = QLineEdit()
        self._fields["CARTESIA_VOICE_ID"].setPlaceholderText("e.g. a0e99841-438c-4a64-b679-ae501e7d6091")
        cartesia_voice_tip = "The Cartesia voice identifier to use for speech. Copy it from your Cartesia voices page."
        self._fields["ELEVENLABS_API_KEY"] = self._password()
        self._fields["ELEVENLABS_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["ELEVENLABS_VOICE_ID"] = QLineEdit()
        self._fields["ELEVENLABS_VOICE_ID"].setPlaceholderText("blank = account default voice")
        eleven_voice_tip = "Leave blank for the account default voice, or paste a specific ElevenLabs voice ID."
        self._fields["ELEVENLABS_MODEL"] = QLineEdit()
        self._fields["ELEVENLABS_MODEL"].setPlaceholderText("e.g. eleven_turbo_v2_5")
        eleven_model_tip = "ElevenLabs speech model name. Use the provider default unless you need a specific model."
        self._fields["OPENAI_TTS_VOICE"] = QLineEdit()
        self._fields["OPENAI_TTS_VOICE"].setPlaceholderText("alloy, echo, fable, onyx, nova, shimmer…")
        openai_voice_tip = "OpenAI voice name for spoken replies."
        self._fields["OPENAI_TTS_MODEL"] = QLineEdit()
        self._fields["OPENAI_TTS_MODEL"].setPlaceholderText("e.g. gpt-4o-mini-tts or tts-1")
        openai_model_tip = "OpenAI text-to-speech model. Newer models sound better; tts-1 is a fast fallback."
        self._fields["TTS_CUSTOM_BASE_URL"] = QLineEdit()
        self._fields["TTS_CUSTOM_BASE_URL"].setPlaceholderText("e.g. http://localhost:8880/v1")
        custom_tts_base_tip = "Base URL for an OpenAI-compatible speech server, ending at the API root such as /v1."
        self._fields["TTS_CUSTOM_API_KEY"] = self._password()
        self._fields["TTS_CUSTOM_API_KEY"].setPlaceholderText("Stored in OS keychain (blank if not needed)")
        self._fields["TTS_CUSTOM_VOICE"] = QLineEdit()
        self._fields["TTS_CUSTOM_VOICE"].setPlaceholderText("server-specific voice name")
        custom_tts_voice_tip = "Voice name or ID expected by your custom speech server."
        self._fields["TTS_CUSTOM_MODEL"] = QLineEdit()
        self._fields["TTS_CUSTOM_MODEL"].setPlaceholderText("server-specific model name")
        custom_tts_model_tip = "Model name expected by your custom speech server."
        self._fields["TTS_CUSTOM_SAMPLE_RATE"] = QLineEdit()
        self._fields["TTS_CUSTOM_SAMPLE_RATE"].setPlaceholderText("e.g. 24000")
        custom_tts_rate_tip = "Output sample rate in Hz. Match the rate your speech server returns, commonly 24000."
        self._fields["GPT_SOVITS_URL"] = QLineEdit()
        self._fields["GPT_SOVITS_URL"].setPlaceholderText("http://127.0.0.1:9880")
        gsv_url_tip = "Local GPT-SoVITS API URL. Start api_v2.py in GPT-SoVITS, then use its host and port here."
        self._fields["GPT_SOVITS_REF_AUDIO_PATH"] = QLineEdit()
        self._fields["GPT_SOVITS_REF_AUDIO_PATH"].setPlaceholderText(r"C:\voices\ref.wav")
        gsv_ref_tip = "Path to a clean reference WAV on the machine running GPT-SoVITS."
        self._fields["GPT_SOVITS_PROMPT_TEXT"] = QLineEdit()
        self._fields["GPT_SOVITS_PROMPT_TEXT"].setPlaceholderText("Exact words spoken in the reference audio")
        gsv_prompt_tip = "Exact transcript of the reference audio. Leave blank only if the GPT-SoVITS model can infer without it."
        self._fields["GPT_SOVITS_PROMPT_LANG"] = QLineEdit()
        self._fields["GPT_SOVITS_PROMPT_LANG"].setPlaceholderText("en")
        gsv_prompt_lang_tip = "Language code for the reference audio transcript, such as en, zh, ja, ko, or yue."
        self._fields["GPT_SOVITS_TEXT_LANG"] = QLineEdit()
        self._fields["GPT_SOVITS_TEXT_LANG"].setPlaceholderText("en")
        gsv_text_lang_tip = "Language code for assistant replies sent to GPT-SoVITS."
        self._fields["GPT_SOVITS_SAMPLE_RATE"] = QLineEdit()
        self._fields["GPT_SOVITS_SAMPLE_RATE"].setPlaceholderText("32000")
        gsv_rate_tip = "Playback sample rate Wisp should use. The adapter resamples GPT-SoVITS output to this rate."
        self._fields["KOKORO_VOICE"] = QLineEdit()
        self._fields["KOKORO_VOICE"].setPlaceholderText("af_heart")
        kokoro_voice_tip = "Built-in Kokoro voice name, such as af_heart, af_bella, af_sky, am_adam, or am_michael."
        self._fields["KOKORO_LANG_CODE"] = QLineEdit()
        self._fields["KOKORO_LANG_CODE"].setPlaceholderText("a")
        kokoro_lang_tip = "Kokoro language code. Use a for American English, b for British English, e for Spanish, f for French."
        kokoro_device = _NoScrollCombo()
        for label, value in _STT_DEVICE_OPTIONS:
            kokoro_device.addItem(label, value)
        kokoro_device_tip = (
            "Where local Kokoro TTS runs. Auto uses CUDA when Torch can see an NVIDIA GPU and falls back to CPU."
        )
        self._fields["KOKORO_DEVICE"] = kokoro_device
        kokoro_device.currentIndexChanged.connect(lambda _idx: self._refresh_kokoro_install_status())
        self._fields["KOKORO_SPEED"] = QLineEdit()
        self._fields["KOKORO_SPEED"].setPlaceholderText("1.0")
        kokoro_speed_tip = "Speech speed multiplier. 1.0 is normal."
        self._fields["KOKORO_SAMPLE_RATE"] = QLineEdit()
        self._fields["KOKORO_SAMPLE_RATE"].setPlaceholderText("24000")
        kokoro_rate_tip = "Kokoro's normal output is 24000 Hz. Keep this unless you have a reason to resample."
        self._fields["TTS_VOLUME"] = QSlider(Qt.Orientation.Horizontal)
        self._fields["TTS_VOLUME"].setRange(0, 150)
        self._fields["TTS_VOLUME"].setSingleStep(5)
        self._fields["TTS_VOLUME"].setPageStep(10)
        self._fields["TTS_VOLUME"].setTickPosition(QSlider.TickPosition.TicksBelow)
        self._fields["TTS_VOLUME"].setTickInterval(25)
        tts_volume_tip = "Playback volume for generated speech. 100% is normal."
        self._tts_volume_value_lbl = QLabel("100%")
        self._tts_volume_value_lbl.setMinimumWidth(44)

        def _update_volume_label(value: int) -> None:
            """Update the visible TTS volume percentage."""
            self._tts_volume_value_lbl.setText(f"{int(value)}%")

        self._fields["TTS_VOLUME"].valueChanged.connect(_update_volume_label)
        self._fields["TTS_READ_ALOUD_MIN_WORDS"] = QLineEdit()
        self._fields["TTS_READ_ALOUD_MIN_WORDS"].setPlaceholderText("50")
        tts_chunk_min_tip = "Minimum words before read-aloud TTS may split at punctuation."
        self._fields["TTS_READ_ALOUD_MAX_WORDS"] = QLineEdit()
        self._fields["TTS_READ_ALOUD_MAX_WORDS"].setPlaceholderText("110")
        tts_chunk_max_tip = "Maximum words per read-aloud TTS chunk before Wisp splits anyway."
        self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"].setPlaceholderText("15.0")
        stt_first_chunk_tip = "Recording length before background transcription starts."
        self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"].setPlaceholderText("10.0")
        stt_chunk_step_tip = "Seconds between background STT chunks after the first one."
        self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"].setPlaceholderText("4.5")
        stt_live_delay_tip = "How far background STT stays behind the live microphone edge."
        self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"] = QLineEdit()
        self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"].setPlaceholderText("1.0")
        stt_overlap_tip = "Audio overlap between STT chunks, used to avoid losing boundary words."

        # Cartesia group
        cartesia_w = QWidget()
        cf = _expanding_form_layout(cartesia_w)
        cf.setContentsMargins(0, 0, 0, 0)
        cf.setSpacing(8)
        cf.addRow(_link_label("Cartesia API key", "https://play.cartesia.ai/keys"), self._fields["CARTESIA_API_KEY"])
        cf.addRow(_tooltip_label("Cartesia Voice ID", cartesia_voice_tip), self._fields["CARTESIA_VOICE_ID"])

        # ElevenLabs group
        eleven_w = QWidget()
        ef = _expanding_form_layout(eleven_w)
        ef.setContentsMargins(0, 0, 0, 0)
        ef.setSpacing(8)
        eleven_note = QLabel(
            f"<small>{t('ElevenLabs support can be installed after setup if it was skipped during the exe build because the build path was too long.')}</small>"
        )
        eleven_note.setWordWrap(True)
        self._elevenlabs_install_btn = QPushButton(t("Install ElevenLabs"))
        self._elevenlabs_install_btn.clicked.connect(self._install_elevenlabs)
        self._elevenlabs_install_status_lbl = QLabel()
        self._elevenlabs_install_status_lbl.setWordWrap(True)
        ef.addRow(eleven_note)
        ef.addRow(self._elevenlabs_install_btn, self._elevenlabs_install_status_lbl)
        ef.addRow(_link_label("ElevenLabs API key", "https://elevenlabs.io/app/settings/api-keys"), self._fields["ELEVENLABS_API_KEY"])
        ef.addRow(_tooltip_label("ElevenLabs Voice ID", eleven_voice_tip), self._fields["ELEVENLABS_VOICE_ID"])
        ef.addRow(_tooltip_label("ElevenLabs Model", eleven_model_tip), self._fields["ELEVENLABS_MODEL"])

        # OpenAI group (reuses the OpenAI key from the Models tab)
        openai_w = QWidget()
        of = _expanding_form_layout(openai_w)
        of.setContentsMargins(0, 0, 0, 0)
        of.setSpacing(8)
        openai_note = QLabel(
            f"<small>{t('Uses your OpenAI API key from the Models tab.')}</small>"
        )
        openai_note.setWordWrap(True)
        of.addRow(openai_note)
        of.addRow(_tooltip_label("OpenAI Voice", openai_voice_tip), self._fields["OPENAI_TTS_VOICE"])
        of.addRow(_tooltip_label("OpenAI Model", openai_model_tip), self._fields["OPENAI_TTS_MODEL"])

        # OpenAI-compatible (custom endpoint) group
        custom_w = QWidget()
        cuf = _expanding_form_layout(custom_w)
        cuf.setContentsMargins(0, 0, 0, 0)
        cuf.setSpacing(8)
        custom_note = QLabel(
            f"<small>{t('Any server with an OpenAI-style /audio/speech endpoint that can return PCM (self-hosted Kokoro/LocalAI, Groq, …).')}</small>"
        )
        custom_note.setWordWrap(True)
        cuf.addRow(custom_note)
        cuf.addRow(_tooltip_label("Base URL", custom_tts_base_tip), self._fields["TTS_CUSTOM_BASE_URL"])
        cuf.addRow(t("API key"), self._fields["TTS_CUSTOM_API_KEY"])
        cuf.addRow(_tooltip_label("Voice", custom_tts_voice_tip), self._fields["TTS_CUSTOM_VOICE"])
        cuf.addRow(_tooltip_label("Model", custom_tts_model_tip), self._fields["TTS_CUSTOM_MODEL"])
        cuf.addRow(
            _tooltip_label("Output sample rate (Hz)", custom_tts_rate_tip),
            self._fields["TTS_CUSTOM_SAMPLE_RATE"],
        )

        # GPT-SoVITS local API group
        gsv_w = QWidget()
        gsvf = _expanding_form_layout(gsv_w)
        gsvf.setContentsMargins(0, 0, 0, 0)
        gsvf.setSpacing(8)
        gsv_note = QLabel(
            f"<small>{t('Runs through a local GPT-SoVITS api_v2.py server. No cloud key is needed.')}</small>"
        )
        gsv_note.setWordWrap(True)
        gsvf.addRow(gsv_note)
        gsvf.addRow(_tooltip_label("API URL", gsv_url_tip), self._fields["GPT_SOVITS_URL"])
        gsvf.addRow(_tooltip_label("Reference audio", gsv_ref_tip), self._fields["GPT_SOVITS_REF_AUDIO_PATH"])
        gsvf.addRow(_tooltip_label("Reference transcript", gsv_prompt_tip), self._fields["GPT_SOVITS_PROMPT_TEXT"])
        gsvf.addRow(_tooltip_label("Reference language", gsv_prompt_lang_tip), self._fields["GPT_SOVITS_PROMPT_LANG"])
        gsvf.addRow(_tooltip_label("Reply language", gsv_text_lang_tip), self._fields["GPT_SOVITS_TEXT_LANG"])
        gsvf.addRow(_tooltip_label("Playback sample rate (Hz)", gsv_rate_tip), self._fields["GPT_SOVITS_SAMPLE_RATE"])

        # Kokoro local library group
        kokoro_w = QWidget()
        kf = _expanding_form_layout(kokoro_w)
        kf.setContentsMargins(0, 0, 0, 0)
        kf.setSpacing(8)
        kokoro_note = QLabel(
            f"<small>{t('Runs Kokoro directly in Wisp. No server, API key, reference audio, or voice clone is needed.')}</small>"
        )
        kokoro_note.setWordWrap(True)
        self._kokoro_install_btn = QPushButton(t("Install Kokoro"))
        self._kokoro_install_btn.clicked.connect(self._install_kokoro)
        self._kokoro_install_status_lbl = QLabel()
        self._kokoro_install_status_lbl.setWordWrap(True)
        kf.addRow(kokoro_note)
        kf.addRow(self._kokoro_install_btn, self._kokoro_install_status_lbl)
        kf.addRow(_tooltip_label("Voice", kokoro_voice_tip), self._fields["KOKORO_VOICE"])
        kf.addRow(_tooltip_label("Language code", kokoro_lang_tip), self._fields["KOKORO_LANG_CODE"])
        kf.addRow(_tooltip_label("Device", kokoro_device_tip), self._fields["KOKORO_DEVICE"])
        kf.addRow(_tooltip_label("Speed", kokoro_speed_tip), self._fields["KOKORO_SPEED"])
        kf.addRow(_tooltip_label("Sample rate (Hz)", kokoro_rate_tip), self._fields["KOKORO_SAMPLE_RATE"])

        for gw in (cartesia_w, eleven_w, openai_w, custom_w, gsv_w, kokoro_w):
            keys_cv.addWidget(gw)
        self._tts_provider_groups = {
            "cartesia": cartesia_w,
            "elevenlabs": eleven_w,
            "openai": openai_w,
            "openai_compatible": custom_w,
            "gpt_sovits": gsv_w,
            "kokoro": kokoro_w,
        }
        # Sit directly under the Provider card (index 0) — the voice/key fields
        # belong with the provider that selects them, above the STT section.
        outer.insertWidget(1, keys_card)
        self._tts_provider_card = keys_card

        # ── PLAYBACK card ────────────────────────────────────────────────
        playback_card, playback_cv = self._card("Playback")
        playback_w = QWidget()
        playback_f = _expanding_form_layout(playback_w)
        playback_f.setContentsMargins(0, 0, 0, 0)
        playback_f.setSpacing(8)
        volume_row = QWidget()
        volume_h = QHBoxLayout(volume_row)
        volume_h.setContentsMargins(0, 0, 0, 0)
        volume_h.setSpacing(8)
        volume_h.addWidget(self._fields["TTS_VOLUME"], 1)
        volume_h.addWidget(self._tts_volume_value_lbl, 0)
        playback_f.addRow(_tooltip_label("Volume", tts_volume_tip), volume_row)
        playback_cv.addWidget(playback_w)
        outer.addWidget(playback_card)

        advanced_group, advanced_layout = self._collapsible_group("Advanced settings")
        advanced_note = QLabel(
            f"<small>{t('Chunking controls for read-aloud TTS and long speech-to-text recordings.')}</small>"
        )
        advanced_note.setWordWrap(True)
        advanced_form_w = QWidget()
        advanced_form = _expanding_form_layout(advanced_form_w)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(8)
        advanced_form.addRow(advanced_note)
        advanced_form.addRow(
            _tooltip_label("Read-aloud min words", tts_chunk_min_tip),
            self._fields["TTS_READ_ALOUD_MIN_WORDS"],
        )
        advanced_form.addRow(
            _tooltip_label("Read-aloud max words", tts_chunk_max_tip),
            self._fields["TTS_READ_ALOUD_MAX_WORDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT first chunk trigger (s)", stt_first_chunk_tip),
            self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT chunk cadence (s)", stt_chunk_step_tip),
            self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT live-edge delay (s)", stt_live_delay_tip),
            self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"],
        )
        advanced_form.addRow(
            _tooltip_label("STT overlap (s)", stt_overlap_tip),
            self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"],
        )
        advanced_layout.addWidget(advanced_form_w)
        outer.addWidget(advanced_group)

        # ── TEST card ─────────────────────────────────────────────────────
        test_card, test_cv = self._card("Test")
        self._tts_test_status_lbl = QLabel()
        self._tts_test_status_lbl.setWordWrap(True)
        test_cv.addWidget(self._button_row(("Test TTS", self._test_tts_connection)))
        test_cv.addWidget(self._tts_test_status_lbl)
        outer.addWidget(test_card)

        self._update_tts_provider_fields()
        self._refresh_elevenlabs_install_status()
        self._refresh_kokoro_install_status()
        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    def _update_tts_provider_fields(self) -> None:
        """Show only the selected TTS provider's settings rows."""
        groups = getattr(self, "_tts_provider_groups", None)
        if not groups:
            return
        provider = _get(self._fields["TTS_PROVIDER"]).strip().lower()
        for name, widget in groups.items():
            widget.setVisible(name == provider)
        # The whole card is pointless when TTS is off.
        card = getattr(self, "_tts_provider_card", None)
        if card is not None:
            card.setVisible(provider != "none")
        notice = getattr(self, "_tts_timing_notice_lbl", None)
        if isinstance(notice, QLabel):
            notice.setVisible(provider in _TTS_TIMESTAMPLESS_PROVIDERS)
            notice.setText(t(_TTS_TIMING_NOTICE))
        if provider == "kokoro":
            self._refresh_kokoro_install_status()
        elif provider == "elevenlabs":
            self._refresh_elevenlabs_install_status()

    @staticmethod
    def _optional_package_installed(module_name: str) -> bool:
        """Return True when an optional package is importable."""
        try:
            from core import optional_deps

            return optional_deps.is_importable(module_name)
        except Exception:
            try:
                importlib.invalidate_caches()
                return importlib.util.find_spec(module_name) is not None
            except Exception:
                return False

    def _elevenlabs_installed(self) -> bool:
        """Return True when the optional ElevenLabs package is importable."""
        return self._optional_package_installed("elevenlabs")

    def _kokoro_installed(self) -> bool:
        """Return True when Kokoro and its English G2P model are importable."""
        return all(
            self._optional_package_installed(module_name)
            for module_name in ("kokoro", "en_core_web_sm")
        )

    def _kokoro_install_mode(self) -> str:
        """Return the CPU/GPU install mode implied by the selected Kokoro device."""
        try:
            from core import optional_deps

            return optional_deps.kokoro_install_mode_for_device(_get(self._fields["KOKORO_DEVICE"]))
        except Exception:
            return "cpu"

    def _kokoro_torch_status(self) -> dict[str, object]:
        """Return Torch status for the Kokoro optional package layer."""
        try:
            from core import optional_deps

            return optional_deps.kokoro_torch_status()
        except Exception as exc:  # noqa: BLE001
            return {
                "installed": False,
                "version": "",
                "cuda_version": "",
                "cuda_available": False,
                "device": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _kokoro_needs_gpu_install(self) -> bool:
        """Return whether the selected Kokoro device needs CUDA Torch support."""
        if not self._kokoro_installed():
            return False
        selected = _get(self._fields["KOKORO_DEVICE"]).strip().lower()
        if selected == "cpu":
            return False
        torch_status = self._kokoro_torch_status()
        if bool(torch_status.get("cuda_available")):
            return False
        if selected == "cuda":
            return True
        if selected == "auto":
            try:
                from core import optional_deps

                return optional_deps.system_cuda_available()
            except Exception:
                return False
        return False

    def _refresh_elevenlabs_install_status(self) -> None:
        """Refresh ElevenLabs install button and status copy."""
        label = getattr(self, "_elevenlabs_install_status_lbl", None)
        button = getattr(self, "_elevenlabs_install_btn", None)
        if not isinstance(label, QLabel) or not isinstance(button, QPushButton):
            return
        if self._elevenlabs_installed():
            button.setEnabled(False)
            button.setText(t("ElevenLabs installed"))
            self._set_test_status(label, True, "ElevenLabs is installed.")
        else:
            button.setEnabled(True)
            button.setText(t("Install ElevenLabs"))
            self._set_test_status(
                label,
                "warn",
                "ElevenLabs is not installed. If the exe build skipped it because the build path was too long, install it here.",
            )

    def _refresh_kokoro_install_status(self) -> None:
        """Refresh Kokoro install button and status copy."""
        label = getattr(self, "_kokoro_install_status_lbl", None)
        button = getattr(self, "_kokoro_install_btn", None)
        if not isinstance(label, QLabel) or not isinstance(button, QPushButton):
            return
        installed = self._kokoro_installed()
        mode = self._kokoro_install_mode()
        torch_status = self._kokoro_torch_status() if installed else {}
        needs_gpu = self._kokoro_needs_gpu_install()
        if installed and needs_gpu:
            button.setEnabled(True)
            button.setText(t("Install Kokoro GPU support"))
            selected = _get(self._fields["KOKORO_DEVICE"]).strip().lower()
            if selected == "cuda":
                self._set_test_status(
                    label,
                    "warn",
                    "Kokoro is installed, but the selected GPU device needs a CUDA-enabled Torch install.",
                )
            else:
                self._set_test_status(
                    label,
                    "warn",
                    "Kokoro is installed with CPU-only Torch. Auto detected a CUDA-capable system, but Kokoro will use CPU until GPU support is installed.",
                )
        elif installed:
            button.setEnabled(False)
            button.setText(t("Kokoro installed"))
            if bool(torch_status.get("cuda_available")):
                device = str(torch_status.get("device") or "CUDA device")
                self._set_test_status(label, True, f"Kokoro is installed with GPU support ({device}).")
            else:
                self._set_test_status(label, True, "Kokoro is installed with CPU support.")
        else:
            button.setEnabled(True)
            button.setText(t("Install Kokoro GPU support") if mode == "gpu" else t("Install Kokoro"))
            if mode == "gpu":
                self._set_test_status(
                    label,
                    "warn",
                    "Kokoro is not installed. The selected device will install GPU support and may download several GB.",
                )
            else:
                self._set_test_status(label, "warn", "Kokoro is not installed.")

    def _install_kokoro(self) -> None:
        """Confirm and install optional Kokoro dependencies into Wisp's Python."""
        from core import optional_deps

        installed = self._kokoro_installed()
        needs_gpu = self._kokoro_needs_gpu_install()
        if installed and not needs_gpu:
            self._refresh_kokoro_install_status()
            return
        voice = _get(self._fields["KOKORO_VOICE"]).strip() or "af_heart"
        lang_code = _get(self._fields["KOKORO_LANG_CODE"]).strip() or "a"
        device = _get(self._fields["KOKORO_DEVICE"]).strip() or "auto"
        mode = optional_deps.kokoro_install_mode_for_device(device)
        package_label = t(
            "kokoro>=0.9.4, soundfile, CUDA-enabled Torch, English speech model"
            if mode == "gpu"
            else "kokoro>=0.9.4, soundfile, English speech model"
        )
        storage_note = t(
            "The GPU install may download several GB and can take a long time. It requires an NVIDIA GPU and compatible driver. "
        ) if mode == "gpu" else ""
        action_note = t(
            "Wisp will upgrade Kokoro's optional package layer with GPU support.\n\n"
            if needs_gpu
            else "Wisp will install Kokoro into its user-writable optional packages folder.\n\n"
        )
        speed = _get(self._fields["KOKORO_SPEED"]).strip() or "1.0"
        sample_rate = _get(self._fields["KOKORO_SAMPLE_RATE"]).strip() or "24000"
        volume = _get(self._fields["TTS_VOLUME"]).strip() or "1.0"
        message = t(
            "{action_note}"
            "Packages: {package_label}\n"
            "Estimated storage: up to about 2 GB for CPU, or several GB for GPU if speech dependencies are missing. "
            "{storage_note}"
            "First use may also download the Kokoro model cache.\n\n"
            "Current Kokoro settings:\n"
            "Voice: {voice}\n"
            "Language code: {lang_code}\n"
            "Device: {device}\n"
            "Speed: {speed}\n"
            "Sample rate: {sample_rate} Hz\n"
            "Volume: {volume}\n\n"
            "On Windows, Kokoro may also need eSpeak NG installed separately if Test TTS reports a phoneme/espeak error.\n\n"
            "Continue?"
        ).format(
            action_note=action_note,
            package_label=package_label,
            storage_note=storage_note,
            voice=voice,
            lang_code=lang_code,
            device=device,
            speed=speed,
            sample_rate=sample_rate,
            volume=volume,
        )
        answer = QMessageBox.question(
            self,
            t("Install Kokoro"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._install_optional_tts_package(
            test_key="kokoro_install",
            display_name="Kokoro",
            packages=optional_deps.kokoro_install_packages(device),
            button_attr="_kokoro_install_btn",
            status_attr="_kokoro_install_status_lbl",
            success_message=(
                "Kokoro GPU support installed and local voice is ready."
                if mode == "gpu"
                else "Kokoro installed and local voice is ready."
            ),
            thread_name="kokoro-install",
            post_install=lambda progress, write_log: self._prepare_kokoro_after_install(
                voice=voice,
                progress=progress,
                write_log=write_log,
            ),
        )

    @staticmethod
    def _prepare_kokoro_after_install(
        *,
        voice: str,
        progress: Callable[[str], None],
        write_log: Callable[[str], None],
    ) -> tuple[bool, str]:
        """Download Kokoro runtime assets after pip install succeeds."""
        try:
            from core import tts

            progress("Installing Kokoro: preparing local voice assets.")
            write_log("[kokoro install] Preparing Kokoro model and voice assets.")
            paths = tts.prepare_kokoro_assets(voice=voice)
            for name, path in sorted(paths.items()):
                write_log(f"[kokoro install] Prepared {name}: {path}")
            return True, "Kokoro installed and local voice is ready."
        except Exception as exc:  # noqa: BLE001
            write_log(f"[kokoro install] Voice preparation failed: {type(exc).__name__}: {exc}")
            return (
                False,
                "Kokoro installed, but local voice preparation failed: "
                f"{exc}. Connect to the internet and click Test TTS once to finish setup.",
            )

    def _install_elevenlabs(self) -> None:
        """Confirm and install optional ElevenLabs dependencies into Wisp's Python."""
        if self._elevenlabs_installed():
            self._refresh_elevenlabs_install_status()
            return
        message = t(
            "Wisp will install ElevenLabs support into its user-writable optional packages folder.\n\n"
            "Package: elevenlabs>=1.0.0\n\n"
            "Use this when the packaged exe skipped ElevenLabs because the build path was too long. "
            "The install may need internet access and will survive Wisp rebuilds.\n\n"
            "Continue?"
        )
        answer = QMessageBox.question(
            self,
            t("Install ElevenLabs"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._install_optional_tts_package(
            test_key="elevenlabs_install",
            display_name="ElevenLabs",
            packages=["elevenlabs>=1.0.0"],
            button_attr="_elevenlabs_install_btn",
            status_attr="_elevenlabs_install_status_lbl",
            success_message="ElevenLabs installed. Add your API key, then click Test TTS.",
            thread_name="elevenlabs-install",
        )

    def _install_optional_tts_package(
        self,
        *,
        test_key: str,
        display_name: str,
        packages: list[str],
        button_attr: str,
        status_attr: str,
        success_message: str,
        thread_name: str,
        post_install: Callable[[Callable[[str], None], Callable[[str], None]], tuple[bool, str]] | None = None,
    ) -> None:
        """Install optional TTS packages into Wisp's user package folder."""
        button = getattr(self, button_attr, None)
        if isinstance(button, QPushButton):
            button.setEnabled(False)
        status = getattr(self, status_attr, None)
        if not isinstance(status, QLabel):
            return

        token = self._latest_test_token.get(test_key, 0) + 1
        self._latest_test_token[test_key] = token
        self._running_test_tokens.add((test_key, token))
        self._set_test_pending(status, f"Installing {display_name}: starting installer.")

        def _progress(message: str) -> None:
            self._queue_test_progress(test_key, token, message)

        def _runner() -> tuple[bool, str]:
            """Install optional packages with pip into Wisp's user package folder."""
            from core import optional_deps

            command = optional_deps.pip_install_command(packages)
            log_path = _optional_install_log_path(display_name, optional_deps.OPTIONAL_PACKAGES_DIR)
            log_prefix = f"[{display_name.lower()} install]"
            print(f"{log_prefix} Running: {' '.join(command)}", flush=True)
            _settings_log.info("Installing %s with command: %s", display_name, command)
            _progress(f"Installing {display_name}: starting installer. Log: {log_path}")
            tail: list[str] = []
            last_progress = f"Installing {display_name}: starting installer. Log: {log_path}"
            started_at = time.monotonic()
            last_output_at = {"value": started_at}
            stop_heartbeat = threading.Event()
            no_output_timeout = _optional_install_no_output_timeout_seconds()
            stopped_reason = {"value": ""}

            def _write_log(line: str) -> None:
                try:
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(line.rstrip() + "\n")
                except Exception:
                    pass

            def _heartbeat() -> None:
                while not stop_heartbeat.wait(20.0):
                    elapsed = int(time.monotonic() - started_at)
                    quiet = int(time.monotonic() - last_output_at["value"])
                    _progress(_optional_install_elapsed_text(display_name, elapsed, quiet, log_path))
                    if no_output_timeout > 0 and quiet >= no_output_timeout and process.poll() is None:
                        reason = (
                            f"Installer produced no output for {_format_duration(quiet)} "
                            f"after: {tail[-1] if tail else 'starting installer'}"
                        )
                        stopped_reason["value"] = reason
                        _write_log(f"{log_prefix} {reason}; stopping installer.")
                        _progress(f"Installing {display_name}: stalled; stopping installer. Log: {log_path}.")
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except Exception:
                            try:
                                process.kill()
                            except Exception:
                                pass
                        return

            _write_log(f"{log_prefix} Running: {' '.join(command)}")
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=optional_deps.pip_install_env(),
                )
            except Exception as exc:
                print(f"{log_prefix} Failed to start installer: {exc}", flush=True)
                _settings_log.exception("%s install could not start installer", display_name)
                _write_log(f"{log_prefix} Failed to start installer: {exc}")
                return False, f"{display_name} install failed: {exc}"
            assert process.stdout is not None
            print(f"{log_prefix} installer started with pid {process.pid}", flush=True)
            _write_log(f"{log_prefix} installer started with pid {process.pid}")
            threading.Thread(target=_heartbeat, daemon=True, name=f"{display_name.lower()}-install-heartbeat").start()
            try:
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    last_output_at["value"] = time.monotonic()
                    print(f"{log_prefix} {line}", flush=True)
                    _write_log(f"{log_prefix} {line}")
                    _settings_log.info("%s install: %s", display_name, line)
                    tail.append(line)
                    tail = tail[-30:]
                    progress_message = _optional_install_progress_text(line, display_name)
                    if progress_message != last_progress:
                        _progress(progress_message)
                        last_progress = progress_message
                returncode = process.wait()
            finally:
                stop_heartbeat.set()
            if returncode == 0:
                importlib.invalidate_caches()
                optional_deps.add_optional_packages_to_path()
                if post_install is not None:
                    prepare_started_at = time.monotonic()
                    prepare_stop = threading.Event()

                    def _prepare_heartbeat() -> None:
                        while not prepare_stop.wait(20.0):
                            elapsed = int(time.monotonic() - prepare_started_at)
                            _progress(
                                f"Installing {display_name}: preparing local voice assets for "
                                f"{_format_duration(elapsed)}. Log: {log_path}."
                            )

                    threading.Thread(
                        target=_prepare_heartbeat,
                        daemon=True,
                        name=f"{display_name.lower()}-prepare-heartbeat",
                    ).start()
                    try:
                        ok, message = post_install(_progress, _write_log)
                    finally:
                        prepare_stop.set()
                    if not ok:
                        return False, message
                print(f"{log_prefix} Completed successfully.", flush=True)
                _write_log(f"{log_prefix} Completed successfully.")
                return True, success_message
            detail = _optional_install_failure_detail(tail)
            if stopped_reason["value"]:
                detail = stopped_reason["value"]
            _write_log(f"{log_prefix} Failed with exit code {returncode}: {detail}")
            return False, f"{display_name} install failed: {detail}"

        def _worker() -> None:
            try:
                ok, result_message = _runner()
            except Exception as exc:
                ok, result_message = False, f"{display_name} install failed: {exc}"
            with self._pending_test_results_lock:
                self._pending_test_results.append((test_key, token, ok, result_message))

        threading.Thread(target=_worker, daemon=True, name=thread_name).start()
        if not self._test_result_timer.isActive():
            self._test_result_timer.start()

    def _tab_prompt(self) -> QWidget:
        """Handle tab prompt for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── SYSTEM PROMPT card ────────────────────────────────────────────
        prompt_card, prompt_cv = self._card("System Prompt")
        note = QLabel(
            f"<small>{t('This prompt is prepended to every LLM request as the system instruction.')}</small>"
        )
        note.setWordWrap(True)
        prompt_cv.addWidget(note)
        util = QTextEdit()
        util.setMinimumHeight(260)
        util.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._fields["SYSTEM_PROMPT_UTILITY"] = util
        prompt_cv.addWidget(util)
        outer.addWidget(prompt_card, stretch=1)

        scroll.setWidget(w)
        return scroll

    def _tab_keybinds(self) -> QWidget:
        """Handle tab keybinds for settings dialog."""
        from PySide6.QtWidgets import QScrollArea, QSizePolicy
        container = QWidget()
        outer_layout = QVBoxLayout(container)
        outer_layout.setSpacing(12)
        outer_layout.setContentsMargins(12, 12, 12, 12)

        # ── CALLER HOTKEYS card ───────────────────────────────────────────
        caller_card, caller_cv = self._card("Caller Hotkeys")

        self._callers_container = QWidget()
        self._callers_vlayout = QVBoxLayout(self._callers_container)
        self._callers_vlayout.setSpacing(8)
        self._callers_vlayout.setContentsMargins(0, 0, 0, 0)
        caller_cv.addWidget(self._callers_container)
        self._caller_blocks: list[dict] = []

        add_caller_btn = QPushButton(t("+ Add Caller Hotkey"))
        add_caller_btn.clicked.connect(lambda: self._add_caller_block())
        btn_wrap = QHBoxLayout()
        btn_wrap.setContentsMargins(0, 4, 0, 4)
        btn_wrap.addWidget(add_caller_btn)
        btn_wrap.addStretch()
        caller_cv.addLayout(btn_wrap)

        caller_cv.addWidget(self._build_snip_block())

        outer_layout.addWidget(caller_card)

        # ── VOICE (PUSH-TO-TALK) card ─────────────────────────────────────
        voice_card, voice_cv = self._card("Voice (hold to talk)")
        voice_note_text = (
            "Hold the key, speak, release to transcribe and ask. "
            "Context and tools below apply to voice queries, just like a caller hotkey."
        )
        voice_note = QLabel(
            f"<small>{t(voice_note_text)}</small>"
        )
        voice_note.setWordWrap(True)
        voice_cv.addWidget(voice_note)
        self._fields["VOICE_REVIEW_TRANSCRIPT"] = QCheckBox(
            t("Review transcript and context before asking")
        )
        self._fields["VOICE_REVIEW_TRANSCRIPT"].setToolTip(
            t("After F9 transcription, open the intent overlay with the transcript in the custom prompt field.")
        )
        voice_cv.addWidget(self._fields["VOICE_REVIEW_TRANSCRIPT"])

        voice_hdr = QWidget()
        voice_hdr_h = QHBoxLayout(voice_hdr)
        voice_hdr_h.setContentsMargins(0, 2, 0, 2)
        voice_hdr_h.setSpacing(8)
        voice_hotkey_edit = HotkeyCaptureEdit()
        voice_hotkey_edit.setFixedWidth(120)
        voice_hotkey_edit.setPlaceholderText("Hotkey...")
        self._fields["HOTKEY_VOICE"] = voice_hotkey_edit
        voice_hdr_h.addWidget(voice_hotkey_edit)
        voice_lbl = QLabel(t("Hold to record voice"))
        voice_lbl.setStyleSheet("font-style: italic; color: palette(placeholder-text);")
        voice_hdr_h.addWidget(voice_lbl)
        voice_hdr_h.addStretch()
        voice_tools_btn = QPushButton(t("Allowed tools…"))
        voice_tools_btn.setToolTip("Choose which installed/addon tools voice queries may use")
        voice_hdr_h.addWidget(voice_tools_btn)
        voice_cv.addWidget(voice_hdr)

        voice_context_row, voice_controls = self._build_context_controls()
        voice_cv.addWidget(voice_context_row)
        self._voice_block: dict = {**voice_controls, "tool_overrides": {}}
        voice_tools_btn.clicked.connect(
            lambda: self._open_tool_access_dialog(self._voice_block, "Voice")
        )
        outer_layout.addWidget(voice_card)

        # ── DICTATION (PUSH-TO-TALK) card ─────────────────────────────────
        dictate_card, dictate_cv = self._card("Dictation (hold to type)")
        dictate_note_text = (
            "Hold the key, speak, release — the transcript is typed straight into "
            "whatever text field has focus (no assistant). Leave the hotkey empty to disable."
        )
        dictate_note = QLabel(
            f"<small>{t(dictate_note_text)}</small>"
        )
        dictate_note.setWordWrap(True)
        dictate_cv.addWidget(dictate_note)

        dictate_hdr = QWidget()
        dictate_hdr_h = QHBoxLayout(dictate_hdr)
        dictate_hdr_h.setContentsMargins(0, 2, 0, 2)
        dictate_hdr_h.setSpacing(8)
        dictate_hotkey_edit = HotkeyCaptureEdit()
        dictate_hotkey_edit.setFixedWidth(120)
        dictate_hotkey_edit.setPlaceholderText("Hotkey...")
        self._fields["HOTKEY_DICTATE"] = dictate_hotkey_edit
        dictate_hdr_h.addWidget(dictate_hotkey_edit)
        dictate_lbl = QLabel(t("Hold to dictate into focused field"))
        dictate_lbl.setStyleSheet("font-style: italic; color: palette(placeholder-text);")
        dictate_hdr_h.addWidget(dictate_lbl)
        dictate_hdr_h.addStretch()
        dictate_cv.addWidget(dictate_hdr)

        dictate_mode = _NoScrollCombo()
        for label, value in _DICTATE_MODE_OPTIONS:
            dictate_mode.addItem(label, value)
        dictate_mode_tip = (
            "Raw pastes exactly what Whisper heard (fast, fully local). LLM cleanup runs the "
            "transcript through your configured model for punctuation/filler cleanup before pasting."
        )
        self._fields["DICTATE_MODE"] = dictate_mode
        dictate_form = QWidget()
        dictate_f = _expanding_form_layout(dictate_form)
        dictate_f.setContentsMargins(0, 0, 0, 0)
        dictate_f.setSpacing(8)
        dictate_f.addRow(_tooltip_label("Dictated text", dictate_mode_tip), self._fields["DICTATE_MODE"])
        dictate_cv.addWidget(dictate_form)
        outer_layout.addWidget(dictate_card)

        # ── OTHER HOTKEYS card ────────────────────────────────────────────
        other_card, other_cv = self._card("Other Hotkeys")
        self._keybinds_layout = other_cv

        self._fields["HOTKEY_ADD_CONTEXT"]   = self._kb_special_row("Add selection as context")
        self._fields["HOTKEY_CLEAR_CONTEXT"] = self._kb_special_row("Clear context")
        self._fields["HOTKEY_READ_SELECTION_ALOUD"] = self._kb_special_row("Read selection aloud")
        self._fields["INTENT_CONTEXT_TOGGLE_KEYS"] = QLineEdit()
        self._fields["INTENT_CONTEXT_TOGGLE_KEYS"].setPlaceholderText("12345678")
        self._fields["INTENT_CONTEXT_TOGGLE_KEYS"].hide()
        self._fields["INTENT_OVERLAY_TIMEOUT_MS"] = QLineEdit()
        self._fields["INTENT_OVERLAY_TIMEOUT_MS"].setFixedWidth(90)
        self._fields["INTENT_OVERLAY_TIMEOUT_MS"].setPlaceholderText("60000")
        intent_timeout_tip = "How long the intent overlay stays open before closing itself. Use 0 to keep it open until you choose or cancel."
        context_key_row = QWidget()
        context_key_h = QHBoxLayout(context_key_row)
        context_key_h.setContentsMargins(0, 2, 0, 2)
        context_key_h.setSpacing(10)
        context_key_h.addSpacing(128)
        context_key_h.addWidget(_tooltip_label("Timeout ms:", intent_timeout_tip))
        context_key_h.addWidget(self._fields["INTENT_OVERLAY_TIMEOUT_MS"])
        context_key_h.addStretch()
        self._keybinds_layout.addWidget(context_key_row)

        outer_layout.addWidget(other_card)
        outer_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _kb_special_row(self, label_text: str) -> "HotkeyCaptureEdit":
        """Add a simple labeled hotkey row; return its HotkeyCaptureEdit."""
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(8)

        key_edit = HotkeyCaptureEdit()
        key_edit.setFixedWidth(120)
        h.addWidget(key_edit)

        lbl = QLabel(t(label_text))
        lbl.setStyleSheet("font-style: italic; color: palette(placeholder-text);")
        h.addWidget(lbl)
        h.addStretch()

        self._keybinds_layout.addWidget(row_w)
        return key_edit

    def _build_snip_block(self) -> QFrame:
        """Build the snip hotkey row with caller-style context controls."""
        frame = QFrame()
        frame.setObjectName("snipHotkeyBlock")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame#snipHotkeyBlock { border: 1px solid palette(mid); border-radius: 4px; }"
        )
        outer = QVBoxLayout(frame)
        outer.setSpacing(4)
        outer.setContentsMargins(8, 6, 8, 6)

        hdr = QWidget()
        hdr_h = QHBoxLayout(hdr)
        hdr_h.setContentsMargins(0, 0, 0, 0)
        hdr_h.setSpacing(6)

        hotkey_edit = HotkeyCaptureEdit()
        hotkey_edit.setFixedWidth(120)
        hotkey_edit.setPlaceholderText("Hotkey...")
        self._fields["HOTKEY_SNIP"] = hotkey_edit
        hdr_h.addWidget(hotkey_edit)

        lbl = QLabel(t("Snip screen region"))
        lbl.setStyleSheet("font-style: italic; color: palette(placeholder-text);")
        hdr_h.addWidget(lbl)
        hdr_h.addStretch()

        tools_btn = QPushButton(t("Allowed tools…"))
        tools_btn.setToolTip("Choose which installed/addon tools snip queries may use")
        hdr_h.addWidget(tools_btn)
        outer.addWidget(hdr)

        context_row, context_controls = self._build_context_controls(
            context_screenshot="off",
            screenshot_enabled=False,
        )
        outer.addWidget(context_row)

        self._fields["SNIP_CONTEXT_AMBIENT"] = context_controls["context_ambient"]
        self._fields["SNIP_CONTEXT_CLIPBOARD"] = context_controls["context_clipboard"]
        self._fields["SNIP_CONTEXT_DOCUMENTS_MODE"] = context_controls["context_documents_mode"]
        self._fields["SNIP_CONTEXT_BROWSER_MODE"] = context_controls["context_browser_mode"]
        self._fields["SNIP_CONTEXT_GITHUB_MODE"] = context_controls["context_github_mode"]
        self._fields["SNIP_CONTEXT_MEMORY_MODE"] = context_controls["context_memory_mode"]
        self._fields["SNIP_FILE_ACCESS"] = context_controls["file_access"]
        self._snip_block: dict = {**context_controls, "tool_overrides": {}}
        tools_btn.clicked.connect(
            lambda: self._open_tool_access_dialog(self._snip_block, "Snip screen region")
        )
        return frame

    def _intent_context_keys(self) -> str:
        """Return the configured context toggle keys, padded for all sources."""
        try:
            raw = _get(self._fields.get("INTENT_CONTEXT_TOGGLE_KEYS"))  # type: ignore[arg-type]
        except Exception:
            raw = ""
        raw = raw or getattr(self, "_env", {}).get("INTENT_CONTEXT_TOGGLE_KEYS", "12345678")
        keys: list[str] = []
        for ch in str(raw or "") + "12345678":
            if ch.isspace() or ch in keys:
                continue
            keys.append(ch)
            if len(keys) >= 8:
                break
        return "".join(keys)

    def _build_context_controls(
        self,
        *,
        context_ambient: bool = False,
        context_clipboard: bool = False,
        context_documents_mode: str = "off",
        context_browser_mode: str = "off",
        context_github_mode: str = "off",
        context_memory_mode: str = "off",
        context_screenshot: str = "off",
        file_access: str = "off",
        screenshot_enabled: bool = True,
    ) -> tuple[QWidget, dict]:
        """Build the shared per-hotkey context grid (used by callers and voice).

        Returns (row_widget, controls) where controls holds the ambient checkbox
        and the five mode combos keyed exactly like the caller block dict.
        """
        return context_controls.build_context_controls(
            intent_context_keys=self._intent_context_keys(),
            on_changed=self._schedule_warning_marker_refresh,
            context_ambient=context_ambient,
            context_clipboard=context_clipboard,
            context_documents_mode=context_documents_mode,
            context_browser_mode=context_browser_mode,
            context_github_mode=context_github_mode,
            context_memory_mode=context_memory_mode,
            context_screenshot=context_screenshot,
            file_access=file_access,
            screenshot_enabled=screenshot_enabled,
        )

    def _open_tool_access_dialog(self, blk: dict, method_label: str) -> None:
        """Open the per-method Allowed Tools dialog and store the result on blk."""
        from ui.settings_panel.tool_access import ToolAccessDialog

        def _mode_data(combo) -> str:
            """Handle mode data for settings dialog."""
            return str(combo.currentData() or "off")

        governed_modes = {
            "Open docs": _mode_data(blk["context_documents_mode"]),
            "Browser/Web": _mode_data(blk["context_browser_mode"]),
            "Git/GitHub": _mode_data(blk["context_github_mode"]),
            "Memory": _mode_data(blk["context_memory_mode"]),
            "Screenshot": _mode_data(blk["context_screenshot"]),
            "Files": _mode_data(blk["file_access"]),
        }
        dlg = ToolAccessDialog(
            self,
            method_label=method_label or "this hotkey",
            overrides=dict(blk.get("tool_overrides") or {}),
            governed_modes=governed_modes,
            extra_tools=self._extra_tools,
        )
        if dlg.exec():
            blk["tool_overrides"] = dlg.selected_overrides()
            self._schedule_warning_marker_refresh()
            self._schedule_dirty_refresh()

    def _add_caller_block(
        self,
        hotkey: str = "",
        label: str = "",
        paste_back: bool = False,
        custom_key: str = "s",
        custom_label: str = "",
        context_ambient: bool = False,
        context_clipboard: bool = False,
        context_documents: bool = False,
        context_tools: bool = False,
        context_documents_mode: str | None = None,
        context_browser_mode: str = "off",
        context_github_mode: str = "off",
        context_memory_mode: str = "off",
        context_screenshot: str = "off",
        file_access: str = "off",
        tools: "dict[str, str] | None" = None,
        intents: "list[dict] | None" = None,
    ) -> None:
        """Add a caller block (framed panel with header + intent rows) to the UI."""
        from PySide6.QtWidgets import QSizePolicy
        frame = QFrame()
        frame.setObjectName("callerHotkeyBlock")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame#callerHotkeyBlock { border: 1px solid palette(mid); border-radius: 4px; }"
        )
        outer = QVBoxLayout(frame)
        outer.setSpacing(4)
        outer.setContentsMargins(8, 6, 8, 6)

        # Header row
        hdr = QWidget()
        hdr_h = QHBoxLayout(hdr)
        hdr_h.setContentsMargins(0, 0, 0, 0)
        hdr_h.setSpacing(6)

        hotkey_edit = HotkeyCaptureEdit()
        hotkey_edit.setFixedWidth(120)
        if hotkey:
            hotkey_edit.setText(hotkey)
        hotkey_edit.setPlaceholderText("Hotkey...")
        hdr_h.addWidget(hotkey_edit)

        hdr_h.addWidget(
            _tooltip_label(
                "Name:",
                "Short name shown for this caller hotkey in settings and tool access dialogs.",
            )
        )
        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(110)
        label_edit.setPlaceholderText("Label")
        hdr_h.addWidget(label_edit)

        paste_cb = QCheckBox("Paste result back")
        paste_cb.setChecked(paste_back)
        paste_cb.setToolTip(
            "When enabled, Wisp pastes the final answer into the focused app instead of only showing it."
        )
        hdr_h.addWidget(paste_cb)

        hdr_h.addStretch()
        tools_btn = QPushButton("Allowed tools…")
        tools_btn.setToolTip("Choose which installed/addon tools this hotkey may use")
        hdr_h.addWidget(tools_btn)
        del_caller_btn = QPushButton("X Remove")
        hdr_h.addWidget(del_caller_btn)
        outer.addWidget(hdr)

        docs_mode = context_documents_mode or ("auto" if context_documents else ("model" if context_tools else "off"))
        context_row, context_controls = self._build_context_controls(
            context_ambient=context_ambient,
            context_clipboard=context_clipboard,
            context_documents_mode=docs_mode,
            context_browser_mode=context_browser_mode,
            context_github_mode=context_github_mode,
            context_memory_mode=context_memory_mode,
            context_screenshot=context_screenshot,
            file_access=file_access,
        )
        outer.addWidget(context_row)

        # Intent rows column header
        from PySide6.QtWidgets import QSizePolicy as SP
        int_hdr = QWidget()
        int_hdr_h = QHBoxLayout(int_hdr)
        int_hdr_h.setContentsMargins(0, 2, 0, 0)
        int_hdr_h.setSpacing(6)
        for txt, w in [("Key", 40), ("Label", 130), ("Prompt", 0)]:
            lbl = QLabel(f"<small><b>{t(txt)}</b></small>")
            if txt == "Prompt":
                lbl.setToolTip(
                    "Instruction sent with this intent. The user's selected text or context is added separately."
                )
            if w:
                lbl.setFixedWidth(w)
            else:
                lbl.setSizePolicy(SP.Policy.Expanding, SP.Policy.Preferred)
            int_hdr_h.addWidget(lbl)
        int_hdr_h.addSpacing(32)
        outer.addWidget(int_hdr)

        # Intent rows container
        intents_container = QWidget()
        intents_vlayout = QVBoxLayout(intents_container)
        intents_vlayout.setSpacing(2)
        intents_vlayout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(intents_container)

        blk: dict = {
            "widget":         frame,
            "hotkey":         hotkey_edit,
            "label":          label_edit,
            "paste_back":     paste_cb,
            "context_ambient": context_controls["context_ambient"],
            "context_clipboard": context_controls["context_clipboard"],
            "context_documents": context_controls["context_documents_mode"],
            "context_documents_mode": context_controls["context_documents_mode"],
            "context_browser_mode": context_controls["context_browser_mode"],
            "context_github_mode": context_controls["context_github_mode"],
            "context_memory_mode": context_controls["context_memory_mode"],
            "context_screenshot": context_controls["context_screenshot"],
            "file_access": context_controls["file_access"],
            "tool_overrides": dict(tools or {}),
            "intents_layout": intents_vlayout,
            "intent_rows":    [],
        }
        tools_btn.clicked.connect(
            lambda: self._open_tool_access_dialog(
                blk, _get(blk["label"]).strip() or _get(blk["hotkey"]).strip() or "Caller"
            )
        )

        for r in (intents or []):
            self._add_caller_intent_row(blk, r.get("key", ""), r.get("label", ""), r.get("prompt", ""))
        self._add_caller_custom_prompt_row(blk, custom_key=custom_key, custom_label=custom_label)

        # Add-row button
        add_row_btn = QPushButton("+ Add row")
        add_row_btn.clicked.connect(lambda: self._add_caller_intent_row(blk))
        add_wrap = QHBoxLayout()
        add_wrap.setContentsMargins(0, 2, 0, 0)
        add_wrap.addWidget(add_row_btn)
        add_wrap.addStretch()
        outer.addLayout(add_wrap)

        del_caller_btn.clicked.connect(lambda: self._delete_caller_block(blk))

        self._callers_vlayout.addWidget(frame)
        self._caller_blocks.append(blk)
        self._wire_change_tracking(frame)
        self._refresh_search_index()
        self._schedule_warning_marker_refresh()
        self._schedule_dirty_refresh()

    def _add_caller_intent_row(
        self,
        blk: dict,
        key: str = "",
        label: str = "",
        prompt: str = "",
    ) -> None:
        """Append one intent row to a caller block."""
        from PySide6.QtWidgets import QSizePolicy
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)

        key_edit = QLineEdit(key)
        key_edit.setFixedWidth(40)
        key_edit.setPlaceholderText("w")
        h.addWidget(key_edit, 0, Qt.AlignmentFlag.AlignTop)

        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(130)
        label_edit.setPlaceholderText("Label")
        h.addWidget(label_edit, 0, Qt.AlignmentFlag.AlignTop)

        prompt_edit = QTextEdit()
        prompt_edit.setAcceptRichText(False)
        prompt_edit.setTabChangesFocus(True)
        prompt_edit.setPlainText(prompt)
        prompt_edit.setPlaceholderText("Prompt sent to LLM...")
        prompt_edit.setMinimumHeight(56)
        prompt_edit.setMaximumHeight(88)
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        h.addWidget(prompt_edit)

        row_info: dict = {"widget": row_w, "key": key_edit, "label": label_edit, "prompt": prompt_edit}

        del_btn = QPushButton("X")
        del_btn.setFixedWidth(40)
        del_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")
        del_btn.clicked.connect(lambda: self._delete_caller_intent_row(blk, row_info))
        h.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

        blk["intents_layout"].addWidget(row_w)
        blk["intent_rows"].append(row_info)
        self._wire_change_tracking(row_w)
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _add_caller_custom_prompt_row(
        self,
        blk: dict,
        *,
        custom_key: str = "s",
        custom_label: str = "",
    ) -> None:
        """Append the fixed custom prompt row beside the configured intent rows."""
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)

        key_edit = QLineEdit(custom_key)
        key_edit.setFixedWidth(40)
        key_edit.setPlaceholderText("s")
        h.addWidget(key_edit)

        label_edit = QLineEdit(custom_label)
        label_edit.setFixedWidth(130)
        label_edit.setPlaceholderText(t("Custom prompt"))
        h.addWidget(label_edit)

        prompt_edit = QLineEdit(t("Custom prompt"))
        prompt_edit.setEnabled(False)
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(prompt_edit)

        h.addSpacing(40)
        blk["intents_layout"].addWidget(row_w)
        blk["custom_key"] = key_edit
        blk["custom_label"] = label_edit
        blk["custom_prompt"] = prompt_edit

    def _delete_caller_intent_row(self, blk: dict, row_info: dict) -> None:
        """Delete caller intent row."""
        if row_info in blk["intent_rows"]:
            blk["intent_rows"].remove(row_info)
        row_info["widget"].deleteLater()
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _delete_caller_block(self, blk: dict) -> None:
        """Delete caller block."""
        if blk in self._caller_blocks:
            self._caller_blocks.remove(blk)
        blk["widget"].deleteLater()
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _tab_memory(self) -> QWidget:
        """Memory tab: LTM configuration only."""
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        w = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(12)
        root.setContentsMargins(12, 12, 12, 12)

        root.addWidget(self._memory_settings_card())
        root.addStretch()
        scroll.setWidget(w)
        return scroll

    def _memory_settings_card(self) -> QWidget:
        """Build the long-term memory settings card."""
        cfg_card, cfg_cv = self._card("Memory Settings")
        fw = QWidget()
        f = _expanding_form_layout(fw)
        f.setSpacing(8)
        f.setContentsMargins(0, 0, 0, 0)

        mem_auto = QCheckBox("Automatically extract long-term facts from conversation")
        mem_auto.setToolTip("Off by default to avoid clutter. Explicit remember/note commands still save facts.")
        self._fields["MEMORY_AUTO_CONSOLIDATE"] = mem_auto
        f.addRow("", mem_auto)

        mem_topk = QLineEdit(self._env.get("MEMORY_TOP_K", "3"))
        mem_topk.setPlaceholderText("number of facts to retrieve per query")
        mem_topk_tip = "Maximum number of stored facts to add to each model request. Higher values add more context."
        self._fields["MEMORY_TOP_K"] = mem_topk
        f.addRow(_tooltip_label("Retrieval top-k:", mem_topk_tip), mem_topk)

        cfg_cv.addWidget(fw)
        return cfg_card

    def _tab_app(self) -> QWidget:
        """Handle tab app for settings dialog."""
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        card, cv = self._card("App Settings")
        fw = QWidget()
        f = _expanding_form_layout(fw)
        f.setSpacing(10)
        f.setContentsMargins(0, 0, 0, 0)

        theme_combo = _NoScrollCombo()
        theme_combo.addItem(t("System default"), "system")
        theme_combo.addItem(t("Light"), "light")
        theme_combo.addItem(t("Dark"), "dark")
        self._fields["THEME_MODE"] = theme_combo
        theme_tip = "System follows your OS theme. Light and Dark use Wisp's saved color templates."
        self._fields["TRUST_PRIVACY_MODE"] = QCheckBox(t("Trust/privacy mode"))
        self._fields["TRUST_PRIVACY_MODE"].setToolTip(
            t("Default on. Redacts sensitive text patterns from context before model requests.")
        )
        self._fields["ICON_AUTO_HIDE"] = QCheckBox(t("Auto-hide icon (only visible when active)"))
        self._fields["ICON_AUTO_HIDE"].setToolTip(
            "Hide the floating icon when Wisp is idle, then show it again while listening or responding."
        )
        self._fields["START_ON_LOGIN"] = QCheckBox(t("Start Wisp when you sign in"))
        self._fields["START_ON_LOGIN"].setToolTip(
            t("Launch Wisp automatically after you sign in to this computer.")
        )
        app_language = _NoScrollCombo()
        for label, value in LANGUAGE_OPTIONS:
            app_language.addItem(t(label), value)
        app_language_tip = "Language used for the app's menus, dialogs, and controls."
        self._fields["APP_LANGUAGE"] = app_language
        assistant_language = _NoScrollCombo()
        for label, value in _ASSISTANT_LANGUAGE_OPTIONS:
            assistant_language.addItem(t(label), value)
        assistant_language_tip = "Preferred response language. System default leaves the prompt unchanged."
        self._fields["ASSISTANT_LANGUAGE"] = assistant_language
        assistant_language.currentIndexChanged.connect(self._on_assistant_language_changed)

        self._fields["ICON_SIZE"] = QLineEdit()
        self._fields["ICON_SIZE"].setPlaceholderText(t("e.g. 60"))
        icon_size_tip = "Floating icon diameter in pixels."
        self._fields["BUBBLE_WIDTH"] = QLineEdit()
        self._fields["BUBBLE_WIDTH"].setPlaceholderText(t("e.g. 340"))
        bubble_width_tip = "Maximum width of the floating response bubble in pixels."
        self._fields["BUBBLE_LINES"] = QLineEdit()
        self._fields["BUBBLE_LINES"].setPlaceholderText(t("e.g. 4"))
        bubble_lines_tip = "How many lines of response text the bubble shows before scrolling."
        self._fields["BUBBLE_FONT_SIZE"] = QLineEdit()
        self._fields["BUBBLE_FONT_SIZE"].setPlaceholderText(t("e.g. 10"))
        bubble_font_size_tip = "Point size for response text inside the floating bubble."
        self._fields["BUBBLE_SCROLL_ENABLED"] = QCheckBox(t("Wheel-scroll text bubble"))
        self._fields["BUBBLE_SCROLL_ENABLED"].setToolTip(
            t("Let the mouse wheel scroll the bubble text while the pointer is over it.")
        )
        self._fields["BUBBLE_SCROLL_SNAP_ENABLED"] = QCheckBox(t("Snap bubble scroll back while speaking"))
        self._fields["BUBBLE_SCROLL_SNAP_ENABLED"].setToolTip(
            t("After manual scrolling, return to the current highlighted word if speech is still active.")
        )
        _bg_row      = self._color_field("THEME_BG",      "e.g. #1c1e26", alpha=False)
        _surface_row = self._color_field("THEME_SURFACE", "e.g. #17181d", alpha=False)
        _text_row    = self._color_field("THEME_TEXT",    "e.g. #e8e8f0", alpha=False)
        _accent_row  = self._color_field("THEME_ACCENT",  "e.g. #8b87ff", alpha=False)
        _bubble_color_row      = self._color_field("BUBBLE_COLOR",          "e.g. #1c1c24dc")
        _bubble_text_color_row = self._color_field("BUBBLE_TEXT_COLOR",     "e.g. #e6e6e6")
        _read_word_color_row   = self._color_field("BUBBLE_READ_WORD_COLOR", "e.g. #4da3ff")

        theme_color_tip = (
            "Colors for the theme selected above. Light and Dark each keep their\n"
            "own set — switching Theme swaps these to that mode's colors.\n"
            "Cards, borders and buttons are shaded automatically from these four."
        )
        # Repaint the swatches/values whenever the user switches Theme mode, so the
        # four pickers always show the template for the currently selected mode.
        theme_combo.currentIndexChanged.connect(self._on_theme_mode_changed)

        f.addRow(_tooltip_label("Theme", theme_tip), self._fields["THEME_MODE"])
        f.addRow(_tooltip_label("Background color", theme_color_tip), _bg_row)
        f.addRow(_tooltip_label("Surface color", theme_color_tip), _surface_row)
        f.addRow(_tooltip_label("Text color", theme_color_tip), _text_row)
        f.addRow(_tooltip_label("Accent color", theme_color_tip), _accent_row)
        f.addRow("", self._fields["TRUST_PRIVACY_MODE"])
        f.addRow("", self._fields["ICON_AUTO_HIDE"])
        f.addRow("", self._fields["START_ON_LOGIN"])
        f.addRow(_tooltip_label("App language", app_language_tip), self._fields["APP_LANGUAGE"])
        f.addRow(_tooltip_label("Assistant language", assistant_language_tip), self._fields["ASSISTANT_LANGUAGE"])
        f.addRow(_sep(), _sep())
        f.addRow(_tooltip_label("Icon size (px)", icon_size_tip), self._fields["ICON_SIZE"])
        f.addRow(_tooltip_label("Text bubble width (px)", bubble_width_tip), self._fields["BUBBLE_WIDTH"])
        f.addRow(_tooltip_label("Text bubble lines", bubble_lines_tip), self._fields["BUBBLE_LINES"])
        f.addRow(_tooltip_label("Text bubble font size (pt)", bubble_font_size_tip), self._fields["BUBBLE_FONT_SIZE"])
        f.addRow("", self._fields["BUBBLE_SCROLL_ENABLED"])
        f.addRow("", self._fields["BUBBLE_SCROLL_SNAP_ENABLED"])
        f.addRow(t("Text bubble color"), _bubble_color_row)
        f.addRow(t("Text bubble text color"), _bubble_text_color_row)
        f.addRow(t("Read word color"), _read_word_color_row)
        cv.addWidget(fw)
        outer.addWidget(card)

        updates_card, updates_cv = self._card("Updates")
        from core import updater

        self._update_current_lbl = QLabel(f"{t('Current version')}: {updater.current_version()}")
        self._update_status_lbl = QLabel(t("Ready to check for updates."))
        self._update_status_lbl.setObjectName("settingsUpdateStatusLabel")
        self._update_status_lbl.setWordWrap(True)
        self._update_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        self._update_btn = QPushButton(t("Check for updates"))
        self._update_btn.setObjectName("settingsUpdateButton")
        self._update_btn.setToolTip(t("Check GitHub Releases for a newer Wisp build."))
        self._update_btn.clicked.connect(self._on_update_button)
        updates_row = QHBoxLayout()
        updates_row.addWidget(self._update_current_lbl)
        updates_row.addStretch()
        updates_row.addWidget(self._update_btn)
        updates_cv.addLayout(updates_row)
        updates_cv.addWidget(self._update_status_lbl)
        outer.addWidget(updates_card)
        outer.addStretch()
        scroll.setWidget(outer_w)
        return scroll

    def _set_update_status(self, message: str, state: str | None = None, **format_args: object) -> None:
        """Update the Settings updater status label."""
        label = getattr(self, "_update_status_lbl", None)
        if label is None:
            return
        text = t(message)
        if format_args:
            text = text.format(**format_args)
        label.setText(text)
        if state == "ok":
            label.setStyleSheet("color: #80c080;")
        elif state == "error":
            label.setStyleSheet("color: #c04040;")
        elif state == "warn":
            label.setStyleSheet("color: #c0a040;")
        else:
            label.setStyleSheet("color: palette(placeholder-text);")

    def _on_update_button(self) -> None:
        """Handle the Settings update button based on its current mode."""
        mode = getattr(self, "_update_mode", "check")
        if mode == "download":
            self._download_available_update()
        elif mode == "apply":
            self._apply_downloaded_update()
        else:
            self._check_for_updates()

    def _remember_update_carrier(self, carrier: _UpdateSignals) -> None:
        self._update_signal_carriers.append(carrier)

    def _forget_update_carrier(self, carrier: _UpdateSignals) -> None:
        try:
            self._update_signal_carriers.remove(carrier)
        except ValueError:
            pass

    def _check_for_updates(self) -> None:
        """Check the release manifest without blocking the Settings dialog."""
        if self._update_running:
            return
        self._update_running = True
        self._update_mode = "check"
        self._update_check_result = None
        self._update_download_path = None
        self._update_btn.setEnabled(False)
        self._update_btn.setText(t("Checking..."))
        self._set_update_status("Checking for updates...")

        carrier = _UpdateSignals()
        self._remember_update_carrier(carrier)
        carrier.done.connect(lambda result, error, c=carrier: self._finish_update_check(c, result, error))

        def _worker() -> None:
            try:
                from core import updater

                carrier.done.emit(updater.check_for_updates(), "")
            except Exception as exc:  # noqa: BLE001 - update checks should be visible, not fatal
                carrier.done.emit(None, str(exc))

        threading.Thread(target=_worker, daemon=True, name="wisp-update-check").start()

    def _finish_update_check(self, carrier: _UpdateSignals, result: object, error: str) -> None:
        """Apply an update-check result on the Qt thread."""
        self._forget_update_carrier(carrier)
        self._update_running = False
        self._update_btn.setEnabled(True)
        if error:
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status("Update check failed: {error}", "error", error=error)
            return

        update_available = bool(getattr(result, "update_available", False))
        asset = getattr(result, "asset", None)
        latest_version = str(getattr(result, "latest_version", "") or "")
        if update_available and asset is not None:
            self._update_check_result = result
            self._update_mode = "download"
            self._update_btn.setText(t("Download update"))
            self._set_update_status("Version {version} is available.", "ok", version=latest_version)
            return

        from core import updater

        if updater.is_newer_version(latest_version, updater.current_version()) and asset is None:
            platform_key = updater.normalized_platform_key()
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status(
                "Version {version} is available, but no {platform} build was published.",
                "warn",
                version=latest_version,
                platform=platform_key,
            )
            return

        self._update_mode = "check"
        self._update_btn.setText(t("Check for updates"))
        self._set_update_status("Wisp is up to date.", "ok")

    def _download_available_update(self) -> None:
        """Download the selected update artifact."""
        if self._update_running:
            return
        result = self._update_check_result
        asset = getattr(result, "asset", None)
        if asset is None:
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status("No update is ready to download.", "warn")
            return

        self._update_running = True
        self._update_btn.setEnabled(False)
        self._update_btn.setText(t("Downloading..."))
        self._set_update_status("Downloading update...")

        carrier = _UpdateSignals()
        self._remember_update_carrier(carrier)
        carrier.done.connect(lambda path, error, c=carrier: self._finish_update_download(c, path, error))

        def _worker() -> None:
            try:
                from core import updater

                carrier.done.emit(str(updater.download_update(asset)), "")
            except Exception as exc:  # noqa: BLE001 - update downloads should be visible, not fatal
                carrier.done.emit("", str(exc))

        threading.Thread(target=_worker, daemon=True, name="wisp-update-download").start()

    def _finish_update_download(self, carrier: _UpdateSignals, path: object, error: str) -> None:
        """Apply an update-download result on the Qt thread."""
        self._forget_update_carrier(carrier)
        self._update_running = False
        self._update_btn.setEnabled(True)
        if error:
            self._update_mode = "download" if self._update_check_result is not None else "check"
            self._update_btn.setText(t("Download update") if self._update_mode == "download" else t("Check for updates"))
            self._set_update_status("Update download failed: {error}", "error", error=error)
            return

        from pathlib import Path

        self._update_download_path = Path(str(path))
        self._update_mode = "apply"
        self._update_btn.setText(t("Apply update"))
        self._update_btn.setToolTip(t("Apply the downloaded update and restart Wisp."))
        self._set_update_status("Update downloaded. Apply it when you are ready to restart Wisp.", "ok")

    def _apply_downloaded_update(self) -> None:
        """Apply the downloaded update via a helper process and quit Wisp."""
        path = self._update_download_path
        if path is None or not path.exists():
            self._update_mode = "check"
            self._update_btn.setText(t("Check for updates"))
            self._set_update_status("Downloaded update file is no longer available.", "error")
            return

        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Question)
        confirm.setWindowTitle(t("Apply update"))
        confirm.setText(t("Apply the downloaded update now?"))
        confirm.setInformativeText(t("Wisp will close, install the update, and restart."))
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        confirm.button(QMessageBox.StandardButton.Yes).setText(t("Apply and restart"))
        confirm.button(QMessageBox.StandardButton.Cancel).setText(t("Not now"))
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            self._set_update_status("Update downloaded. Apply it when you are ready to restart Wisp.", "ok")
            return

        try:
            from core import updater

            updater.apply_update(path)
            self._update_mode = "apply"
            self._update_btn.setEnabled(False)
            self._update_btn.setText(t("Applying..."))
            self._set_update_status(
                "Applying update. Wisp will close now; installing and reopening can take a few minutes.",
                "ok",
            )
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(1500, app.quit)
        except Exception as exc:  # noqa: BLE001 - surface launcher issues in Settings
            self._set_update_status("Could not apply update: {error}", "error", error=exc)

    def _update_chat_elaborate_prompt_visibility(self, checked: bool | None = None) -> None:
        """Show the elaborate prompt field only when auto-elaborate is enabled."""
        field = self._fields.get("CHAT_ELABORATE_PROMPT")
        checkbox = self._fields.get("CHAT_AUTO_ELABORATE")
        if checked is None and hasattr(checkbox, "isChecked"):
            checked = bool(checkbox.isChecked())  # type: ignore[attr-defined]
        visible = bool(checked)
        if self._chat_elaborate_prompt_label is not None:
            self._chat_elaborate_prompt_label.setVisible(visible)
        if field is not None:
            field.setVisible(visible)

    def _on_assistant_language_changed(self, *_args) -> None:
        """Refresh model-facing built-in prompts when the assistant language changes."""
        if getattr(self, "_loading_values", False):
            return
        import config as cfg

        assistant_language = _get(self._fields["ASSISTANT_LANGUAGE"])
        chat_field = self._fields.get("CHAT_ELABORATE_PROMPT")
        if chat_field is not None:
            _set(
                chat_field,
                cfg.localize_chat_elaborate_prompt_if_default(
                    _get(chat_field),
                    assistant_language,
                ),
            )

        system_field = self._fields.get("SYSTEM_PROMPT_UTILITY")
        if system_field is not None:
            _set(
                system_field,
                cfg.localize_system_prompt_utility_if_default(
                    _get(system_field),
                    assistant_language,
                ),
            )

        for i, blk in enumerate(getattr(self, "_caller_blocks", [])):
            for j, row in enumerate(blk.get("intent_rows", [])):
                intent = cfg.localize_intent_if_default(
                    i,
                    j,
                    {
                        "key": _get(row["key"]),
                        "label": _get(row["label"]),
                        "prompt": _get(row["prompt"]),
                    },
                    assistant_language,
                )
                row["key"].setText(str(intent.get("key", "")))
                row["label"].setText(str(intent.get("label", "")))
                _set(row["prompt"], str(intent.get("prompt", "")))

    def _tab_advanced(self) -> QWidget:
        """Handle tab advanced for settings dialog."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        outer_w = QWidget()
        outer = QVBoxLayout(outer_w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        context_card, context_cv = self._card("Context limits")
        context_note = QLabel(
            t("Tuning limits for how much external text Wisp can collect before asking the model.")
        )
        context_note.setWordWrap(True)
        context_cv.addWidget(context_note)
        context_fw = QWidget()
        context_f = _expanding_form_layout(context_fw)
        context_f.setSpacing(8)
        context_f.setContentsMargins(0, 0, 0, 0)
        self._fields["CONTEXT_BROWSER_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_BROWSER_MAX_CHARS"].setPlaceholderText("e.g. 8000")
        browser_chars_tip = "Maximum characters Wisp reads from a browser page when browser context is on."
        self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"].setPlaceholderText("e.g. 12000")
        ambient_doc_chars_tip = "Maximum characters read automatically from open documents before the model answers."
        self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"].setPlaceholderText("e.g. 12000")
        tool_doc_chars_tip = "Maximum characters returned when the model chooses to fetch document text with a tool."
        context_f.addRow(_tooltip_label("Browser fetch chars", browser_chars_tip), self._fields["CONTEXT_BROWSER_MAX_CHARS"])
        context_f.addRow(
            _tooltip_label("Auto document fetch chars", ambient_doc_chars_tip),
            self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"],
        )
        context_f.addRow(
            _tooltip_label("Tool document fetch chars", tool_doc_chars_tip),
            self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"],
        )
        context_cv.addWidget(context_fw)
        outer.addWidget(context_card)

        local_file_card, local_file_cv = self._card("Model file access")
        local_file_note = QLabel(
            t(
                "These folders are the only places the model can list or read files. "
                "Each keybind chooses whether local files are off, read-only, ask-before-write, or automatic."
            )
        )
        local_file_note.setWordWrap(True)
        local_file_cv.addWidget(local_file_note)
        local_file_fw = QWidget()
        local_file_f = _expanding_form_layout(local_file_fw)
        local_file_f.setSpacing(8)
        local_file_f.setContentsMargins(0, 0, 0, 0)
        self._fields["TOOL_FILE_ROOTS"] = QTextEdit()
        self._fields["TOOL_FILE_ROOTS"].setPlaceholderText(
            "One folder per line. Leave empty to turn local file access off."
        )
        file_roots_tip = "Folders that file tools are allowed to inspect. Paths outside this list are refused."
        self._fields["TOOL_FILE_ROOTS"].setFixedHeight(72)
        self._fields["TOOL_FILE_BLOCKED_GLOBS"] = QTextEdit()
        self._fields["TOOL_FILE_BLOCKED_GLOBS"].setPlaceholderText(
            "One private pattern per line. Matching files are always refused."
        )
        blocked_globs_tip = "Glob patterns to block even inside allowed folders, such as secrets or private notes."
        self._fields["TOOL_FILE_BLOCKED_GLOBS"].setFixedHeight(92)
        local_file_f.addRow(_tooltip_label("Folders the model may use", file_roots_tip), self._fields["TOOL_FILE_ROOTS"])
        local_file_f.addRow(_tooltip_label("Private file patterns", blocked_globs_tip), self._fields["TOOL_FILE_BLOCKED_GLOBS"])
        local_file_cv.addWidget(local_file_fw)
        outer.addWidget(local_file_card)

        outer.addWidget(self._memory_settings_card())

        memory_card, memory_cv = self._card("Memory tuning")
        memory_fw = QWidget()
        memory_f = _expanding_form_layout(memory_fw)
        memory_f.setSpacing(8)
        memory_f.setContentsMargins(0, 0, 0, 0)
        self._fields["MEMORY_CONSOLIDATION_INTERVAL"] = QLineEdit()
        self._fields["MEMORY_CONSOLIDATION_INTERVAL"].setPlaceholderText("minutes between consolidations")
        consolidation_tip = "How often Wisp compresses recent conversation into longer-term memory."
        self._fields["MEMORY_STM_TOKEN_BUDGET"] = QLineEdit()
        self._fields["MEMORY_STM_TOKEN_BUDGET"].setPlaceholderText("tokens before STM compression kicks in")
        stm_budget_tip = "Approximate short-term memory size before recent conversation is summarized."
        memory_f.addRow(
            _tooltip_label("Consolidation interval (min)", consolidation_tip),
            self._fields["MEMORY_CONSOLIDATION_INTERVAL"],
        )
        memory_f.addRow(_tooltip_label("STM token budget", stm_budget_tip), self._fields["MEMORY_STM_TOKEN_BUDGET"])
        memory_cv.addWidget(memory_fw)
        outer.addWidget(memory_card)

        timing_card, timing_cv = self._card("Speech and bubble timing")
        timing_fw = QWidget()
        timing_f = _expanding_form_layout(timing_fw)
        timing_f.setSpacing(8)
        timing_f.setContentsMargins(0, 0, 0, 0)
        self._fields["BUBBLE_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_REVEAL_WPM"].setPlaceholderText("e.g. 170")
        reveal_wpm_tip = "Words per minute used for the normal bubble reveal, including TTS providers without word timestamps."
        self._fields["BUBBLE_HOLD_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_HOLD_REVEAL_WPM"].setPlaceholderText("e.g. 480")
        hold_reveal_wpm_tip = "Words per minute used when showing text without generated speech."
        self._fields["BUBBLE_HIDE_DELAY_S"] = QLineEdit()
        self._fields["BUBBLE_HIDE_DELAY_S"].setPlaceholderText("e.g. 3.5")
        bubble_hide_tip = "How long the text bubble stays on screen after the last word, in seconds."
        self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"] = QLineEdit()
        self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"].setPlaceholderText("e.g. 2.5")
        bubble_snap_tip = "How long to wait after wheel scrolling before returning to the highlighted word."
        self._fields["TTS_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.0")
        tts_rate_tip = "Speech playback multiplier. 1.0 is normal speed; larger values speak faster."
        self._fields["TTS_HOLD_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_HOLD_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.35")
        tts_hold_rate_tip = "Playback multiplier for hold-to-talk replies, where a faster response can feel more immediate."
        timing_f.addRow(_tooltip_label("Text bubble speed (WPM)", reveal_wpm_tip), self._fields["BUBBLE_REVEAL_WPM"])
        timing_f.addRow(
            _tooltip_label("Text bubble hold speed (WPM)", hold_reveal_wpm_tip),
            self._fields["BUBBLE_HOLD_REVEAL_WPM"],
        )
        timing_f.addRow(_tooltip_label("Text bubble display time (s)", bubble_hide_tip), self._fields["BUBBLE_HIDE_DELAY_S"])
        timing_f.addRow(
            _tooltip_label("Bubble scroll snap delay (s)", bubble_snap_tip),
            self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"],
        )
        timing_f.addRow(_tooltip_label("TTS speed", tts_rate_tip), self._fields["TTS_PLAYBACK_RATE"])
        timing_f.addRow(_tooltip_label("TTS hold speed", tts_hold_rate_tip), self._fields["TTS_HOLD_PLAYBACK_RATE"])
        timing_cv.addWidget(timing_fw)
        outer.addWidget(timing_card)

        outer.addStretch()
        scroll.setWidget(outer_w)
        return scroll

    # ---- Theme template (light/dark color swatches) ----

    _THEME_ROLES = ("bg", "surface", "text", "accent")
    _THEME_FIELD_KEYS = {
        "bg": "THEME_BG", "surface": "THEME_SURFACE",
        "text": "THEME_TEXT", "accent": "THEME_ACCENT",
    }

    def _theme_edit_mode(self) -> str:
        """Which template the four swatches edit, given the Theme selection."""
        data = self._fields["THEME_MODE"].currentData()  # type: ignore[attr-defined]
        if data in ("light", "dark"):
            return data
        from ui.shared.theme import is_dark_mode  # "system" → whatever is active now
        return "dark" if is_dark_mode() else "light"

    def _flush_visible_theme_fields(self) -> None:
        """Copy the four swatch values back into the template they belong to."""
        mode = self._theme_shown_mode
        if not mode:
            return
        self._theme_templates[mode] = {
            role: _get(self._fields[self._THEME_FIELD_KEYS[role]]).strip()
            for role in self._THEME_ROLES
        }

    def _show_theme_template(self, mode: str) -> None:
        """Load *mode*'s template into the four swatches."""
        tpl = self._theme_templates.get(mode, {})
        self._theme_syncing = True
        try:
            for role in self._THEME_ROLES:
                _set(self._fields[self._THEME_FIELD_KEYS[role]], tpl.get(role, ""))
        finally:
            self._theme_syncing = False
        self._theme_shown_mode = mode

    def _on_theme_mode_changed(self) -> None:
        """Handle theme mode changed events."""
        if self._theme_syncing or not self._theme_templates:
            return
        new_mode = self._theme_edit_mode()
        if new_mode == self._theme_shown_mode:
            return
        self._flush_visible_theme_fields()
        self._show_theme_template(new_mode)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model_combo(self, provider: str = "") -> QComboBox:
        """Handle model combo for settings dialog."""
        models = _PROVIDER_MODELS.get(provider, [])
        cb = _NoScrollCombo()
        cb.setEditable(True)
        cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        cb.addItems(models)
        cb.setCurrentIndex(-1)
        completer = QCompleter(models, cb)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        cb.setCompleter(completer)
        return cb

    def _combo(self, options: list[str], current: str = "") -> QComboBox:
        """Handle combo for settings dialog."""
        cb = _NoScrollCombo()
        for opt in options:
            cb.addItem(_PROVIDER_LABELS.get(opt, opt) if opt else "", opt)
        if current is not None:
            idx = cb.findData(current)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        return cb

    def _color_field(self, field_key: str, placeholder: str, *, alpha: bool = True) -> QWidget:
        """QLineEdit + color-swatch button that opens QColorDialog.

        Stores ``#RRGGBBAA`` when *alpha* is True (text-bubble colours), or plain
        opaque ``#RRGGBB`` when False (theme colours, where alpha is meaningless).
        """
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor

        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        self._fields[field_key] = edit

        swatch = QPushButton()
        swatch.setFixedSize(26, 26)
        swatch.setToolTip("Pick color")

        def _parse(text: str) -> QColor:
            """Parse the settings dialog workflow."""
            s = text.strip()
            if s.startswith("#") and len(s) == 9:
                try:
                    return QColor(int(s[1:3],16), int(s[3:5],16), int(s[5:7],16), int(s[7:9],16))
                except ValueError:
                    pass
            c = QColor(s)
            return c if c.isValid() else QColor()

        def _fmt(c: QColor) -> str:
            """Handle fmt for settings dialog."""
            if alpha:
                return f"#{c.red():02x}{c.green():02x}{c.blue():02x}{c.alpha():02x}"
            return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"

        def _update_swatch(text=""):
            """Update swatch."""
            c = _parse(edit.text())
            if c.isValid():
                swatch.setStyleSheet(
                    f"QPushButton {{ background: #{c.alpha():02x}{c.red():02x}{c.green():02x}{c.blue():02x};"
                    f" border: 1px solid #666; border-radius: 4px; padding: 0px; }}"
                )
            else:
                swatch.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #666; border-radius: 4px; padding: 0px; }"
                )

        def _pick():
            """Handle pick for settings dialog."""
            c = _parse(edit.text())
            if not c.isValid():
                c = QColor(255, 255, 255, 255)
            options = (
                QColorDialog.ColorDialogOption.ShowAlphaChannel
                if alpha else QColorDialog.ColorDialogOption(0)
            )
            chosen = QColorDialog.getColor(c, self, "Pick color", options)
            if chosen.isValid():
                edit.setText(_fmt(chosen))

        edit.textChanged.connect(_update_swatch)
        swatch.clicked.connect(_pick)
        _update_swatch()

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit)
        h.addWidget(swatch)
        return row

    def _button_row(self, *buttons: tuple[str, object]) -> QWidget:
        """Handle button row for settings dialog."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, handler in buttons:
            btn = QPushButton(t(text))
            btn.clicked.connect(handler)
            layout.addWidget(btn)
        layout.addStretch()
        return row

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """Return a styled card frame and its inner VBoxLayout, with a header label."""
        card = QFrame()
        card.setObjectName("card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 12, 16, 16)
        cv.setSpacing(10)
        if title:
            hdr = _WarningHeaderLabel(t(title).upper())
            hdr.setObjectName("sectionHeader")
            cv.addWidget(hdr)
            self._register_warning_header(title, hdr, uppercase=True)
        return card, cv

    def _collapsible_group(self, title: str, *, checked: bool = False) -> tuple[QWidget, QVBoxLayout]:
        """Return a button-expanded settings body."""
        group = QWidget()
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toggle = QPushButton(t(title))
        toggle.setCheckable(True)
        toggle.setChecked(checked)
        toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        body = QWidget(group)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 4, 0, 0)
        body_layout.setSpacing(8)
        layout.addWidget(toggle)
        layout.addWidget(body)
        body.setVisible(checked)
        toggle.toggled.connect(body.setVisible)
        return group, body_layout

    def _register_warning_header(
        self,
        key: str,
        label: QLabel,
        *,
        base_text: str | None = None,
        uppercase: bool = False,
    ) -> None:
        """Handle register warning header for settings dialog."""
        if not hasattr(self, "_warning_headers"):
            self._warning_headers = {}
            self._warning_header_base_texts = {}
        if not hasattr(self, "_warning_header_uppercase_keys"):
            self._warning_header_uppercase_keys = set()
        self._warning_headers[key] = label
        self._warning_header_base_texts[key] = base_text or key
        if uppercase:
            self._warning_header_uppercase_keys.add(key)
        else:
            self._warning_header_uppercase_keys.discard(key)

    def _warning_header_text(self, key: str) -> str:
        """Handle warning header text for settings dialog."""
        base = self._warning_header_base_texts.get(key, key)
        text = t(base)
        if key in getattr(self, "_warning_header_uppercase_keys", set()):
            return text.upper()
        return text

    def _set_warning_markers(self, warnings_by_target: dict[str, list[str]]) -> None:
        """Set warning markers."""
        if not hasattr(self, "_warning_headers"):
            return
        for key, label in self._warning_headers.items():
            base = self._warning_header_text(key)
            target_warnings = warnings_by_target.get(key, [])
            if target_warnings:
                label.setText(f"⚠ {base}")
                label.setToolTip("\n\n".join(t(warning) for warning in target_warnings))
            else:
                label.setText(base)
                label.setToolTip("")

    def _warning_values_from_current_ui(self) -> dict[str, str]:
        """Handle warning values from current ui for settings dialog."""
        def _section_vals(sk: str) -> tuple[str, str]:
            """Handle section vals for settings dialog."""
            rows = getattr(self, "_model_section_rows", {}).get(sk, [])
            if not rows:
                return "", ""
            primary = rows[0]
            return (
                str(primary["api_key_combo"].currentData() or ""),
                self._model_value(primary),
            )

        llm_p, llm_m = _section_vals("LLM")
        vis_p, vis_m = _section_vals("VISION_LLM")
        return {
            "LLM_PROVIDER": llm_p,
            "LLM_MODEL": llm_m,
            "VISION_LLM_PROVIDER": vis_p,
            "VISION_LLM_MODEL": vis_m,
        }

    def _refresh_capability_warning_markers(self) -> None:
        """Refresh capability warning markers."""
        _warnings, warnings_by_target = self._capability_warnings_for_values(
            self._warning_values_from_current_ui()
        )
        self._set_warning_markers(warnings_by_target)

    def _schedule_warning_marker_refresh(self) -> None:
        """Schedule warning marker refresh."""
        if getattr(self, "_disposing", False):
            return
        QTimer.singleShot(0, self._refresh_capability_warning_markers)

    def _area_heading(self, title: str, subtitle: str) -> QWidget:
        """Return a compact section label with an accent rail."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(10)

        rail = QFrame()
        rail.setObjectName("areaAccentLine")
        rail.setFixedWidth(4)
        rail.setMinimumHeight(34)

        text_w = QWidget()
        text_v = QVBoxLayout(text_w)
        text_v.setContentsMargins(0, 0, 0, 0)
        text_v.setSpacing(2)
        title_lbl = _WarningHeaderLabel(title)
        title_lbl.setObjectName("areaHeader")
        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setObjectName("areaSubheader")
        subtitle_lbl.setWordWrap(True)
        text_v.addWidget(title_lbl)
        text_v.addWidget(subtitle_lbl)

        h.addWidget(rail)
        h.addWidget(text_w, 1)
        return w

    def _area_group(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        """Return a section group whose accent rail spans all contained cards."""
        w = QWidget()
        w.setObjectName("settingsAreaGroup")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(10)

        rail = QFrame()
        rail.setObjectName("areaAccentLine")
        rail.setFixedWidth(4)
        rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        content_w = QWidget()
        content_v = QVBoxLayout(content_w)
        content_v.setContentsMargins(0, 0, 0, 0)
        content_v.setSpacing(12)

        title_lbl = _WarningHeaderLabel(t(title))
        title_lbl.setObjectName("areaHeader")
        self._register_warning_header(title, title_lbl)
        subtitle_lbl = QLabel(t(subtitle))
        subtitle_lbl.setObjectName("areaSubheader")
        subtitle_lbl.setWordWrap(True)
        content_v.addWidget(title_lbl)
        content_v.addWidget(subtitle_lbl)

        body_v = QVBoxLayout()
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(12)
        content_v.addLayout(body_v)

        h.addWidget(rail)
        h.addWidget(content_w, 1)
        return w, body_v

    def _provider_model_row(
        self,
        provider_field: QComboBox,
        model_field: QComboBox,
        test_label: QLabel,
        test_slot,
        fallback_key: str,
        fallback_prefix: str,
        fallback_providers: list[str] | None = None,
        test_btn_label: str = "Test",
    ) -> QWidget:
        """Provider | Model side-by-side + test row + fallbacks, returns a widget."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        pm = QWidget()
        pmh = QHBoxLayout(pm)
        pmh.setContentsMargins(0, 0, 0, 0)
        pmh.setSpacing(12)

        pc = QWidget(); pvl = QVBoxLayout(pc)
        pvl.setContentsMargins(0, 0, 0, 0); pvl.setSpacing(4)
        pvl.addWidget(QLabel("Provider")); pvl.addWidget(provider_field)

        mc = QWidget(); mvl = QVBoxLayout(mc)
        mvl.setContentsMargins(0, 0, 0, 0); mvl.setSpacing(4)
        mvl.addWidget(QLabel("Model")); mvl.addWidget(model_field)

        pmh.addWidget(pc); pmh.addWidget(mc)
        v.addWidget(pm)

        test_label.setWordWrap(True)
        tr = QWidget(); trh = QHBoxLayout(tr)
        trh.setContentsMargins(0, 0, 0, 0); trh.setSpacing(10)
        trh.addWidget(self._button_row((test_btn_label, test_slot)))
        trh.addWidget(test_label, 1)
        v.addWidget(tr)

        fb_w = QWidget(); fb_f = _expanding_form_layout(fb_w)
        fb_f.setContentsMargins(0, 4, 0, 0); fb_f.setSpacing(8)
        self._add_fallback_section(fb_f, fallback_key, fallback_prefix, providers=fallback_providers)
        v.addWidget(fb_w)

        return w

    def _password(self) -> QLineEdit:
        """Handle password for settings dialog."""
        le = QLineEdit()
        le.setEchoMode(QLineEdit.EchoMode.Password)
        return le

    def _add_fallback_section(
        self,
        form: QFormLayout,
        key: str,
        label_prefix: str,
        providers: list[str] | None = None,
    ) -> None:
        """Add fallback section."""
        add_btn = QPushButton("+ Add fallback")
        add_wrap = QHBoxLayout()
        add_wrap.setContentsMargins(0, 0, 0, 0)
        add_wrap.addWidget(add_btn)
        add_wrap.addStretch()
        add_widget = QWidget()
        add_widget.setLayout(add_wrap)

        self._fields[key] = add_widget
        self._fallback_rows[key] = []
        self._fallback_rows[f"{key}__form"] = form  # type: ignore[index]
        self._fallback_rows[f"{key}__prefix"] = label_prefix  # type: ignore[index]
        self._fallback_rows[f"{key}__providers"] = providers or ["groq", "openai", "anthropic", "google", "chatgpt", "copilot"]  # type: ignore[index]
        self._fallback_rows[f"{key}__add_widget"] = add_widget  # type: ignore[index]
        add_btn.clicked.connect(lambda: self._add_fallback_row(key, providers=providers))
        form.addRow("", add_widget)

    def _add_fallback_row(
        self,
        key: str,
        provider: str = "",
        model: str = "",
        providers: list[str] | None = None,
    ) -> None:
        """Add fallback row."""
        provider_options = providers or self._fallback_rows.get(f"{key}__providers", ["groq", "openai", "anthropic", "google", "chatgpt", "copilot"])  # type: ignore[arg-type]
        provider_combo = self._combo(provider_options, provider)
        model_combo = self._model_combo(provider)
        if model:
            model_combo.setCurrentText(model)
        else:
            model_combo.lineEdit().setPlaceholderText("model")
        provider_combo.currentIndexChanged.connect(
            lambda _: _refresh_model_combo(model_combo, _get(provider_combo))
        )
        remove_btn = QPushButton("Remove")
        model_row = QWidget()
        model_h = QHBoxLayout(model_row)
        model_h.setContentsMargins(0, 0, 0, 0)
        model_h.setSpacing(8)
        model_h.addWidget(model_combo)
        model_h.addWidget(remove_btn)

        provider_label = QLabel()
        model_label = QLabel()
        row_info = {
            "provider_label": provider_label,
            "provider": provider_combo,
            "model_label": model_label,
            "model_row": model_row,
            "model": model_combo,
        }
        remove_btn.clicked.connect(lambda: self._remove_fallback_row(key, row_info))
        form = self._fallback_rows[f"{key}__form"]  # type: ignore[index]
        add_widget = self._fallback_rows[f"{key}__add_widget"]  # type: ignore[index]
        insert_at, _role = form.getWidgetPosition(add_widget)
        form.insertRow(insert_at, provider_label, provider_combo)
        form.insertRow(insert_at + 1, model_label, model_row)
        self._fallback_rows[key].append(row_info)
        self._renumber_fallback_rows(key)
        self._wire_change_tracking(model_row)
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _remove_fallback_row(self, key: str, row_info: dict) -> None:
        """Remove fallback row."""
        if row_info in self._fallback_rows[key]:
            self._fallback_rows[key].remove(row_info)
        form = self._fallback_rows[f"{key}__form"]  # type: ignore[index]
        form.removeRow(row_info["provider_label"])
        form.removeRow(row_info["model_label"])
        self._renumber_fallback_rows(key)
        self._refresh_search_index()
        self._schedule_dirty_refresh()

    def _renumber_fallback_rows(self, key: str) -> None:
        """Handle renumber fallback rows for settings dialog."""
        prefix = self._fallback_rows[f"{key}__prefix"]  # type: ignore[index]
        for idx, row in enumerate(self._fallback_rows[key], 1):
            row["provider_label"].setText(f"{prefix} provider {idx}")
            row["model_label"].setText(f"{prefix} model {idx}")

    def _set_fallback_rows(self, key: str, raw: str) -> None:
        """Set fallback rows."""
        for row in list(self._fallback_rows[key]):
            self._remove_fallback_row(key, row)
        for provider, model in _parse_fallback_rows(raw):
            self._add_fallback_row(key, provider, model)

    def _get_fallback_rows(self, key: str) -> str:
        """Return fallback rows."""
        parts = []
        for row in self._fallback_rows[key]:
            provider = _get(row["provider"]).strip()
            model = _get(row["model"]).strip()
            if provider and model:
                parts.append(f"{provider}:{model}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _secret_configured_fast(self, name: str) -> bool:
        """Handle secret configured fast for settings dialog."""
        return bool((self._env.get(name) or "").strip()) or secret_store.has_secret(name)

    def _load_values(self):
        """Load values."""
        import config as cfg
        self._loading_values = True
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        self._pending_active_profile = ""

        # ── API key rows ──────────────────────────────────────────────────
        for row in list(self._api_key_rows):
            self._remove_api_key_row(row)

        _LLM_KEY_MAP = [
            ("groq",       "GROQ_API_KEY"),
            ("openai",     "OPENAI_API_KEY"),
            ("anthropic",  "ANTHROPIC_API_KEY"),
            ("google",     "GOOGLE_API_KEY"),
            ("deepseek",   "DEEPSEEK_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
            ("mistral",    "MISTRAL_API_KEY"),
            ("xai",        "XAI_API_KEY"),
            ("together",   "TOGETHER_API_KEY"),
            ("cerebras",   "CEREBRAS_API_KEY"),
            ("zai",        "ZAI_API_KEY"),
            ("nvidia",     "NVIDIA_API_KEY"),
            ("sambanova",  "SAMBANOVA_API_KEY"),
            ("github_models", "GITHUB_MODELS_API_KEY"),
            ("huggingface", "HUGGINGFACE_API_KEY"),
            ("chutes",     "CHUTES_API_KEY"),
            ("vercel",     "VERCEL_API_KEY"),
            ("fireworks",  "FIREWORKS_API_KEY"),
            ("cohere",     "COHERE_API_KEY"),
            ("ai21",       "AI21_API_KEY"),
            ("nebius",     "NEBIUS_API_KEY"),
        ]
        for provider, key_name in _LLM_KEY_MAP:
            if self._secret_configured_fast(key_name):
                self._add_api_key_row(provider=provider, stored=True)
        try:
            from core.auth import copilot_auth
            stored, _message = copilot_auth.token_status()
            if stored:
                self._add_api_key_row(provider="copilot", stored=True)
        except Exception:
            pass
        # No default placeholder row — the list stays empty until the user adds
        # a key via "+ Add API Key". Avoids a spurious "Groq" row on every open.

        # ── Model sections ────────────────────────────────────────────────
        def _load_section(sk, penv, menv, fenv, pdef, mdef, fdef=""):
            """Load section."""
            for r in list(self._model_section_rows[sk]):
                self._remove_model_section_row(sk, r)
            self._add_model_section_row(
                sk,
                self._env.get(penv, pdef),
                self._env.get(menv, mdef),
            )
            for p, m in _parse_fallback_rows(self._env.get(fenv, fdef)):
                self._add_model_section_row(sk, p, m)

        _load_section("LLM",        "LLM_PROVIDER",        "LLM_MODEL",        "LLM_FALLBACKS",        cfg.LLM_PROVIDER,        cfg.LLM_MODEL,        cfg.LLM_FALLBACKS)
        _load_section("VISION_LLM", "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS", cfg.VISION_LLM_PROVIDER, cfg.VISION_LLM_MODEL, cfg.VISION_LLM_FALLBACKS)
        _load_section("MEMORY_LLM", "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS", cfg.MEMORY_LLM_PROVIDER, cfg.MEMORY_LLM_MODEL, cfg.MEMORY_LLM_FALLBACKS)
        self._fields["CHAT_TOOL_TRACE_UI"].setChecked(
            self._env.get(
                "CHAT_TOOL_TRACE_UI",
                str(getattr(cfg, "CHAT_TOOL_TRACE_UI", False)),
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )  # type: ignore[attr-defined]
        self._fields["WISP_PLANNED_CHUNKING"].setChecked(
            self._env.get(
                "WISP_PLANNED_CHUNKING",
                str(getattr(cfg, "PLANNED_CHUNKING", False)),
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )  # type: ignore[attr-defined]
        _set(
            self._fields["WISP_PLANNED_CHUNKING_CHUNKS"],
            self._env.get(
                "WISP_PLANNED_CHUNKING_CHUNKS",
                str(getattr(cfg, "PLANNED_CHUNKING_CHUNKS", 3)),
            ),
        )
        _set(
            self._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"],
            self._env.get(
                "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
                str(getattr(cfg, "PLANNED_CHUNKING_MIN_PROMPT_CHARS", 80)),
            ),
        )
        _set(
            self._fields["CHAT_REASONING_EFFORT"],
            self._env.get("CHAT_REASONING_EFFORT", getattr(cfg, "CHAT_REASONING_EFFORT", "high")),
        )

        # ── TTS / Custom keys (still in self._fields) ─────────────────────
        for name, label in [
            ("CARTESIA_API_KEY",   "Cartesia"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("TTS_CUSTOM_API_KEY", "Custom TTS endpoint"),
            ("CUSTOM_API_KEY",     "Custom provider"),
        ]:
            if name not in self._fields:
                continue
            self._fields[name].clear()  # type: ignore[attr-defined]
            status = "stored in OS keychain" if self._secret_configured_fast(name) else "not configured"
            self._fields[name].setPlaceholderText(t(status))  # type: ignore[attr-defined]

        _set(self._fields["TTS_PROVIDER"], self._env.get("TTS_PROVIDER", cfg.TTS_PROVIDER))
        _set(
            self._fields["TTS_SPEAK_REPLIES"],
            self._env.get(
                "TTS_SPEAK_REPLIES",
                str(getattr(cfg, "TTS_SPEAK_REPLIES", False)),
            ),
        )
        _set(self._fields["CARTESIA_VOICE_ID"], self._env.get("CARTESIA_VOICE_ID", ""))
        _set(self._fields["ELEVENLABS_VOICE_ID"], self._env.get("ELEVENLABS_VOICE_ID", cfg.ELEVENLABS_VOICE_ID))
        _set(self._fields["ELEVENLABS_MODEL"], self._env.get("ELEVENLABS_MODEL", cfg.ELEVENLABS_MODEL))
        _set(self._fields["OPENAI_TTS_VOICE"], self._env.get("OPENAI_TTS_VOICE", cfg.OPENAI_TTS_VOICE))
        _set(self._fields["OPENAI_TTS_MODEL"], self._env.get("OPENAI_TTS_MODEL", cfg.OPENAI_TTS_MODEL))
        _set(self._fields["TTS_CUSTOM_BASE_URL"], self._env.get("TTS_CUSTOM_BASE_URL", cfg.TTS_CUSTOM_BASE_URL))
        _set(self._fields["TTS_CUSTOM_VOICE"], self._env.get("TTS_CUSTOM_VOICE", cfg.TTS_CUSTOM_VOICE))
        _set(self._fields["TTS_CUSTOM_MODEL"], self._env.get("TTS_CUSTOM_MODEL", cfg.TTS_CUSTOM_MODEL))
        _set(self._fields["TTS_CUSTOM_SAMPLE_RATE"], self._env.get("TTS_CUSTOM_SAMPLE_RATE", str(cfg.TTS_CUSTOM_SAMPLE_RATE)))
        _set(self._fields["GPT_SOVITS_URL"], self._env.get("GPT_SOVITS_URL", cfg.GPT_SOVITS_URL))
        _set(self._fields["GPT_SOVITS_REF_AUDIO_PATH"], self._env.get("GPT_SOVITS_REF_AUDIO_PATH", cfg.GPT_SOVITS_REF_AUDIO_PATH))
        _set(self._fields["GPT_SOVITS_PROMPT_TEXT"], self._env.get("GPT_SOVITS_PROMPT_TEXT", cfg.GPT_SOVITS_PROMPT_TEXT))
        _set(self._fields["GPT_SOVITS_PROMPT_LANG"], self._env.get("GPT_SOVITS_PROMPT_LANG", cfg.GPT_SOVITS_PROMPT_LANG))
        _set(self._fields["GPT_SOVITS_TEXT_LANG"], self._env.get("GPT_SOVITS_TEXT_LANG", cfg.GPT_SOVITS_TEXT_LANG))
        _set(self._fields["GPT_SOVITS_SAMPLE_RATE"], self._env.get("GPT_SOVITS_SAMPLE_RATE", str(cfg.GPT_SOVITS_SAMPLE_RATE)))
        _set(self._fields["KOKORO_VOICE"], self._env.get("KOKORO_VOICE", cfg.KOKORO_VOICE))
        _set(self._fields["KOKORO_LANG_CODE"], self._env.get("KOKORO_LANG_CODE", cfg.KOKORO_LANG_CODE))
        _set(self._fields["KOKORO_DEVICE"], self._env.get("KOKORO_DEVICE", getattr(cfg, "KOKORO_DEVICE", "auto")))
        _set(self._fields["KOKORO_SPEED"], self._env.get("KOKORO_SPEED", str(cfg.KOKORO_SPEED)))
        _set(self._fields["KOKORO_SAMPLE_RATE"], self._env.get("KOKORO_SAMPLE_RATE", str(cfg.KOKORO_SAMPLE_RATE)))
        _set(self._fields["TTS_VOLUME"], self._env.get("TTS_VOLUME", str(getattr(cfg, "TTS_VOLUME", 1.0))))
        _set(
            self._fields["TTS_READ_ALOUD_MIN_WORDS"],
            self._env.get("TTS_READ_ALOUD_MIN_WORDS", str(getattr(cfg, "TTS_READ_ALOUD_MIN_WORDS", 50))),
        )
        _set(
            self._fields["TTS_READ_ALOUD_MAX_WORDS"],
            self._env.get("TTS_READ_ALOUD_MAX_WORDS", str(getattr(cfg, "TTS_READ_ALOUD_MAX_WORDS", 110))),
        )
        self._update_tts_provider_fields()
        _set(self._fields["STT_MODEL"], self._env.get("STT_MODEL", cfg.STT_MODEL))
        self._rebuild_stt_languages()  # drop yue if the loaded model isn't large-v3
        _set(self._fields["STT_COMPUTE_TYPE"], self._env.get("STT_COMPUTE_TYPE", cfg.STT_COMPUTE_TYPE))
        _set(self._fields["STT_LANGUAGE"], self._env.get("STT_LANGUAGE", cfg.STT_LANGUAGE))
        _set(self._fields["STT_BEAM_SIZE"], self._env.get("STT_BEAM_SIZE", str(cfg.STT_BEAM_SIZE)))
        _set(self._fields["STT_DEVICE"], self._env.get("STT_DEVICE", cfg.STT_DEVICE))
        _set(
            self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS", 15.0)),
            ),
        )
        _set(
            self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_STEP_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_STEP_SECONDS", 10.0)),
            ),
        )
        _set(
            self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS", 4.5)),
            ),
        )
        _set(
            self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"],
            self._env.get(
                "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
                str(getattr(cfg, "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS", 1.0)),
            ),
        )
        _set(self._fields["HOTKEY_ADD_CONTEXT"],   self._env.get("HOTKEY_ADD_CONTEXT",   cfg.HOTKEY_ADD_CONTEXT))
        _set(self._fields["HOTKEY_CLEAR_CONTEXT"], self._env.get("HOTKEY_CLEAR_CONTEXT", cfg.HOTKEY_CLEAR_CONTEXT))
        _set(self._fields["HOTKEY_SNIP"],          self._env.get("HOTKEY_SNIP",          cfg.HOTKEY_SNIP))
        _set(
            self._fields["HOTKEY_READ_SELECTION_ALOUD"],
            self._env.get(
                "HOTKEY_READ_SELECTION_ALOUD",
                getattr(cfg, "HOTKEY_READ_SELECTION_ALOUD", ""),
            ),
        )
        _set(self._fields["INTENT_CONTEXT_TOGGLE_KEYS"], self._env.get(
            "INTENT_CONTEXT_TOGGLE_KEYS",
            getattr(cfg, "INTENT_CONTEXT_TOGGLE_KEYS", "12345678"),
        ))
        _set(self._fields["INTENT_OVERLAY_TIMEOUT_MS"], self._env.get(
            "INTENT_OVERLAY_TIMEOUT_MS",
            str(getattr(cfg, "INTENT_OVERLAY_TIMEOUT_MS", 60000)),
        ))
        sc = dict(getattr(cfg, "SNIP_CALLER", {}) or {})
        sb = self._snip_block
        legacy_snip_documents = self._env.get(
            "SNIP_CONTEXT_DOCUMENTS",
            str(sc.get("context_documents", getattr(cfg, "SNIP_CONTEXT_DOCUMENTS", False))),
        ).lower() == "true"
        legacy_snip_tools = self._env.get(
            "SNIP_CONTEXT_TOOLS",
            str(sc.get("context_tools", getattr(cfg, "SNIP_CONTEXT_TOOLS", False))),
        ).lower() == "true"
        snip_documents_mode = self._env.get(
            "SNIP_CONTEXT_DOCUMENTS_MODE",
            sc.get("context_documents_mode")
            or ("auto" if legacy_snip_documents else ("model" if legacy_snip_tools else "off")),
        )
        sb["context_ambient"].setChecked(
            self._env.get("SNIP_CONTEXT_AMBIENT", str(sc.get("context_ambient", False))).lower() == "true"
        )
        sb["context_clipboard"].setChecked(
            self._env.get("SNIP_CONTEXT_CLIPBOARD", str(sc.get("context_clipboard", False))).lower() == "true"
        )
        _set(sb["context_documents_mode"], snip_documents_mode)
        _set(
            sb["context_browser_mode"],
            self._env.get(
                "SNIP_CONTEXT_BROWSER_MODE",
                "model" if legacy_snip_tools else (sc.get("context_browser_mode") or "off"),
            ),
        )
        _set(
            sb["context_github_mode"],
            self._env.get(
                "SNIP_CONTEXT_GITHUB_MODE",
                "model" if legacy_snip_tools else (sc.get("context_github_mode") or "off"),
            ),
        )
        _set(
            sb["context_memory_mode"],
            self._env.get("SNIP_CONTEXT_MEMORY_MODE", sc.get("context_memory_mode") or "on"),
        )
        _set(sb["context_screenshot"], "off")
        _set(
            sb["file_access"],
            normalize_file_access_mode(self._env.get("SNIP_FILE_ACCESS", sc.get("file_access", "off"))),
        )
        snip_tools_env = self._env.get("SNIP_TOOLS")
        sb["tool_overrides"] = (
            parse_tool_modes(snip_tools_env)
            if snip_tools_env is not None
            else dict(sc.get("tools") or {})
        )
        _set(self._fields["CUSTOM_BASE_URL"],      self._env.get("CUSTOM_BASE_URL",      cfg.CUSTOM_BASE_URL))
        _set(self._fields["GITHUB_CLIENT_ID"],     self._env.get("GITHUB_CLIENT_ID",     cfg.GITHUB_CLIENT_ID))
        _set(self._fields["GITHUB_OAUTH_SCOPES"],  self._env.get("GITHUB_OAUTH_SCOPES",  cfg.GITHUB_OAUTH_SCOPES))
        _set(self._fields["CONTEXT_BROWSER_MAX_CHARS"], self._env.get("CONTEXT_BROWSER_MAX_CHARS", str(cfg.CONTEXT_BROWSER_MAX_CHARS)))
        _set(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS)))
        _set(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_TOOL_DOCUMENT_MAX_CHARS)))
        _set(self._fields["TOOL_FILE_ROOTS"], self._env.get("TOOL_FILE_ROOTS", "\n".join(getattr(cfg, "TOOL_FILE_ROOTS", []))))
        _set(
            self._fields["TOOL_FILE_BLOCKED_GLOBS"],
            self._env.get(
                "TOOL_FILE_BLOCKED_GLOBS",
                "\n".join(getattr(cfg, "TOOL_FILE_BLOCKED_GLOBS", [])),
            ),
        )

        self._fields["MEMORY_AUTO_CONSOLIDATE"].setChecked(
            self._env.get("MEMORY_AUTO_CONSOLIDATE", str(cfg.MEMORY_AUTO_CONSOLIDATE)).lower() == "true"
        )  # type: ignore
        _set(self._fields["MEMORY_CONSOLIDATION_INTERVAL"], self._env.get("MEMORY_CONSOLIDATION_INTERVAL", str(cfg.MEMORY_CONSOLIDATION_INTERVAL)))
        _set(self._fields["MEMORY_TOP_K"],           self._env.get("MEMORY_TOP_K",           str(cfg.MEMORY_TOP_K)))
        _set(self._fields["MEMORY_STM_TOKEN_BUDGET"], self._env.get("MEMORY_STM_TOKEN_BUDGET", str(cfg.MEMORY_STM_TOKEN_BUDGET)))

        # Build caller blocks from CALLER_ROWS + any env overrides
        for blk in list(self._caller_blocks):
            blk["widget"].deleteLater()
        self._caller_blocks.clear()

        caller_count = int(self._env.get("CALLER_COUNT", str(len(cfg.CALLER_ROWS))))
        for i in range(caller_count):
            cr = cfg.CALLER_ROWS[i] if i < len(cfg.CALLER_ROWS) else {}
            n = i + 1
            intent_count = int(self._env.get(f"CALLER_{n}_INTENT_COUNT", str(len(cr.get("intents", [])))))
            intents = []
            for j in range(intent_count):
                m = j + 1
                di = cr["intents"][j] if j < len(cr.get("intents", [])) else {}
                intent = {
                    "key":    self._env.get(f"CALLER_{n}_INTENT_{m}_KEY",    di.get("key", "")),
                    "label":  self._env.get(f"CALLER_{n}_INTENT_{m}_LABEL",  di.get("label", "")),
                    "prompt": self._env.get(f"CALLER_{n}_INTENT_{m}_PROMPT", di.get("prompt", "")),
                }
                intents.append(cfg.localize_intent_if_default(
                    i,
                    j,
                    intent,
                    self._env.get("ASSISTANT_LANGUAGE", getattr(cfg, "ASSISTANT_LANGUAGE", "")),
                ))
            legacy_documents = self._env.get(
                f"CALLER_{n}_CONTEXT_DOCUMENTS",
                str(cr.get("context_documents", False)),
            ).lower() == "true"
            legacy_tools = self._env.get(
                f"CALLER_{n}_CONTEXT_TOOLS",
                str(cr.get("context_tools", False)),
            ).lower() == "true"
            documents_mode = self._env.get(
                f"CALLER_{n}_CONTEXT_DOCUMENTS_MODE",
                cr.get("context_documents_mode")
                or ("auto" if legacy_documents else ("model" if legacy_tools else "off")),
            )
            browser_mode = self._env.get(
                f"CALLER_{n}_CONTEXT_BROWSER_MODE",
                "model" if legacy_tools else (cr.get("context_browser_mode") or "off"),
            )
            github_mode = self._env.get(
                f"CALLER_{n}_CONTEXT_GITHUB_MODE",
                "model" if legacy_tools else (cr.get("context_github_mode") or "off"),
            )
            memory_mode = self._env.get(
                f"CALLER_{n}_CONTEXT_MEMORY_MODE",
                cr.get("context_memory_mode") or "on",
            )
            file_access = self._env.get(
                f"CALLER_{n}_FILE_ACCESS",
                cr.get("file_access") or "off",
            )
            tools_env = self._env.get(f"CALLER_{n}_TOOLS")
            caller_tools = (
                parse_tool_modes(tools_env)
                if tools_env is not None
                else dict(cr.get("tools") or {})
            )
            self._add_caller_block(
                hotkey     = self._env.get(f"CALLER_{n}_HOTKEY",     cr.get("hotkey", "")),
                label      = self._env.get(f"CALLER_{n}_LABEL",      cr.get("label", "")),
                paste_back = self._env.get(f"CALLER_{n}_PASTE_BACK", str(cr.get("paste_back", False))).lower() == "true",
                custom_key = self._env.get(f"CALLER_{n}_CUSTOM_KEY", cr.get("custom_key", "s")),
                custom_label = self._env.get(f"CALLER_{n}_CUSTOM_LABEL", cr.get("custom_label", "")),
                context_ambient = self._env.get(f"CALLER_{n}_CONTEXT_AMBIENT", str(cr.get("context_ambient", False))).lower() == "true",
                context_clipboard = self._env.get(f"CALLER_{n}_CONTEXT_CLIPBOARD", str(cr.get("context_clipboard", False))).lower() == "true",
                context_documents = documents_mode == "auto",
                context_tools = any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode)),
                context_documents_mode = documents_mode,
                context_browser_mode = browser_mode,
                context_github_mode = github_mode,
                context_memory_mode = memory_mode,
                context_screenshot = normalize_screenshot_mode(self._env.get(f"CALLER_{n}_CONTEXT_SCREENSHOT", cr.get("context_screenshot", "off"))),
                file_access = normalize_file_access_mode(file_access),
                tools      = caller_tools,
                intents    = intents,
            )

        # Voice (push-to-talk) block
        vc = dict(getattr(cfg, "VOICE_CALLER", {}) or {})
        _set(self._fields["HOTKEY_VOICE"], self._env.get("HOTKEY_VOICE", vc.get("hotkey", cfg.HOTKEY_VOICE)))
        _set(
            self._fields["VOICE_REVIEW_TRANSCRIPT"],
            self._env.get(
                "VOICE_REVIEW_TRANSCRIPT",
                str(getattr(cfg, "VOICE_REVIEW_TRANSCRIPT", False)),
            ),
        )
        _set(self._fields["HOTKEY_DICTATE"], self._env.get("HOTKEY_DICTATE", cfg.HOTKEY_DICTATE))
        _set(self._fields["DICTATE_MODE"], self._env.get("DICTATE_MODE", cfg.DICTATE_MODE))
        vb = self._voice_block
        vb["context_ambient"].setChecked(
            self._env.get("VOICE_CONTEXT_AMBIENT", str(vc.get("context_ambient", False))).lower() == "true"
        )
        vb["context_clipboard"].setChecked(
            self._env.get("VOICE_CONTEXT_CLIPBOARD", str(vc.get("context_clipboard", False))).lower() == "true"
        )
        _set(
            vb["context_documents_mode"],
            self._env.get("VOICE_CONTEXT_DOCUMENTS_MODE", vc.get("context_documents_mode") or "off"),
        )
        _set(
            vb["context_browser_mode"],
            self._env.get("VOICE_CONTEXT_BROWSER_MODE", vc.get("context_browser_mode") or "off"),
        )
        _set(
            vb["context_github_mode"],
            self._env.get("VOICE_CONTEXT_GITHUB_MODE", vc.get("context_github_mode") or "off"),
        )
        _set(
            vb["context_memory_mode"],
            self._env.get("VOICE_CONTEXT_MEMORY_MODE", vc.get("context_memory_mode") or "on"),
        )
        _set(
            vb["context_screenshot"],
            normalize_screenshot_mode(
                self._env.get("VOICE_CONTEXT_SCREENSHOT", vc.get("context_screenshot", "off"))
            ),
        )
        _set(
            vb["file_access"],
            normalize_file_access_mode(self._env.get("VOICE_FILE_ACCESS", vc.get("file_access", "off"))),
        )
        voice_tools_env = self._env.get("VOICE_TOOLS")
        vb["tool_overrides"] = (
            parse_tool_modes(voice_tools_env)
            if voice_tools_env is not None
            else dict(vc.get("tools") or {})
        )

        # Read ICON_AUTO_HIDE, falling back to the legacy DOLL_AUTO_HIDE key.
        auto_hide = self._env.get(
            "ICON_AUTO_HIDE",
            self._env.get("DOLL_AUTO_HIDE", str(cfg.ICON_AUTO_HIDE)),
        ).lower() == "true"
        theme_mode = self._env.get("THEME_MODE", getattr(cfg, "THEME_MODE", "system"))
        combo = self._fields["THEME_MODE"]
        idx = combo.findData(theme_mode)  # type: ignore[attr-defined]
        combo.setCurrentIndex(idx if idx >= 0 else 0)  # type: ignore[attr-defined]
        # Load both theme templates from env/config, then show the one for the
        # currently selected mode in the four swatches.
        self._theme_templates = {}
        for mode in ("light", "dark"):
            self._theme_templates[mode] = {
                role: self._env.get(
                    f"THEME_{mode.upper()}_{role.upper()}",
                    getattr(cfg, f"THEME_{mode.upper()}_{role.upper()}", ""),
                )
                for role in self._THEME_ROLES
            }
        self._theme_shown_mode = ""
        self._show_theme_template(self._theme_edit_mode())
        self._fields["ICON_AUTO_HIDE"].setChecked(auto_hide)  # type: ignore
        self._fields["START_ON_LOGIN"].setChecked(  # type: ignore
            self._env.get("START_ON_LOGIN", str(getattr(cfg, "START_ON_LOGIN", False))).lower()
            == "true"
        )
        self._fields["TRUST_PRIVACY_MODE"].setChecked(
            self._env.get("TRUST_PRIVACY_MODE", str(getattr(cfg, "TRUST_PRIVACY_MODE", True))).lower()
            == "true"
        )  # type: ignore

        auto_elab = self._env.get("CHAT_AUTO_ELABORATE", str(cfg.CHAT_AUTO_ELABORATE)).lower() == "true"
        self._fields["CHAT_AUTO_ELABORATE"].setChecked(auto_elab)  # type: ignore
        assistant_language = self._env.get("ASSISTANT_LANGUAGE", getattr(cfg, "ASSISTANT_LANGUAGE", ""))
        default_elaborate_prompt = cfg.localize_chat_elaborate_prompt_if_default(
            self._env.get("CHAT_ELABORATE_PROMPT", getattr(cfg, "CHAT_ELABORATE_PROMPT", "")),
            assistant_language,
        )
        _set(self._fields["CHAT_ELABORATE_PROMPT"],
             default_elaborate_prompt)
        _set(
            self._fields["APP_LANGUAGE"],
            self._env.get("APP_LANGUAGE", getattr(cfg, "APP_LANGUAGE", "")),
        )
        _set(
            self._fields["ASSISTANT_LANGUAGE"],
            assistant_language,
        )

        _set(self._fields["ICON_SIZE"],    self._env.get("ICON_SIZE", self._env.get("DOLL_SIZE", str(cfg.ICON_SIZE))))
        _set(self._fields["BUBBLE_WIDTH"], self._env.get("BUBBLE_WIDTH", str(cfg.BUBBLE_WIDTH)))
        _set(self._fields["BUBBLE_LINES"], self._env.get("BUBBLE_LINES", str(cfg.BUBBLE_LINES)))
        _set(self._fields["BUBBLE_FONT_SIZE"], self._env.get("BUBBLE_FONT_SIZE", str(cfg.BUBBLE_FONT_SIZE)))
        self._fields["BUBBLE_SCROLL_ENABLED"].setChecked(  # type: ignore
            self._env.get(
                "BUBBLE_SCROLL_ENABLED",
                str(getattr(cfg, "BUBBLE_SCROLL_ENABLED", True)),
            ).lower()
            == "true"
        )
        self._fields["BUBBLE_SCROLL_SNAP_ENABLED"].setChecked(  # type: ignore
            self._env.get(
                "BUBBLE_SCROLL_SNAP_ENABLED",
                str(getattr(cfg, "BUBBLE_SCROLL_SNAP_ENABLED", True)),
            ).lower()
            == "true"
        )
        _set(self._fields["BUBBLE_COLOR"], self._env.get("BUBBLE_COLOR", cfg.BUBBLE_COLOR))
        _set(self._fields["BUBBLE_TEXT_COLOR"], self._env.get("BUBBLE_TEXT_COLOR", cfg.BUBBLE_TEXT_COLOR))
        _set(self._fields["BUBBLE_READ_WORD_COLOR"], self._env.get("BUBBLE_READ_WORD_COLOR", cfg.BUBBLE_READ_WORD_COLOR))
        _set(self._fields["BUBBLE_REVEAL_WPM"], self._env.get("BUBBLE_REVEAL_WPM", str(cfg.BUBBLE_REVEAL_WPM)))
        _set(self._fields["BUBBLE_HOLD_REVEAL_WPM"], self._env.get("BUBBLE_HOLD_REVEAL_WPM", str(cfg.BUBBLE_HOLD_REVEAL_WPM)))
        _set(
            self._fields["BUBBLE_HIDE_DELAY_S"],
            _ms_to_seconds_str(
                self._env.get("BUBBLE_HIDE_DELAY_MS", str(cfg.BUBBLE_HIDE_DELAY_MS)),
                cfg.BUBBLE_HIDE_DELAY_MS,
            ),
        )
        _set(
            self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"],
            _ms_to_seconds_str(
                self._env.get(
                    "BUBBLE_SCROLL_SNAP_DELAY_MS",
                    str(getattr(cfg, "BUBBLE_SCROLL_SNAP_DELAY_MS", 2500)),
                ),
                getattr(cfg, "BUBBLE_SCROLL_SNAP_DELAY_MS", 2500),
            ),
        )
        _set(self._fields["TTS_PLAYBACK_RATE"], self._env.get("TTS_PLAYBACK_RATE", str(cfg.TTS_PLAYBACK_RATE)))
        _set(self._fields["TTS_HOLD_PLAYBACK_RATE"], self._env.get("TTS_HOLD_PLAYBACK_RATE", str(cfg.TTS_HOLD_PLAYBACK_RATE)))

        util_val = cfg.localize_system_prompt_utility_if_default(
            self._env.get("SYSTEM_PROMPT_UTILITY", cfg.SYSTEM_PROMPT_UTILITY),
            assistant_language,
        )
        self._fields["SYSTEM_PROMPT_UTILITY"].setPlainText(util_val)  # type: ignore
        self._refresh_capability_warning_markers()
        self._loading_values = False
        self._wire_change_tracking(self)
        self._refresh_search_index()
        self._reset_dirty_baseline()

    def _effective_secret_value(self, name: str) -> str:
        """Handle effective secret value for settings dialog."""
        typed = _get(self._fields[name]).strip()
        if typed:
            return typed
        import config as cfg

        return getattr(cfg, name, "")

    def _set_test_status(self, label: QLabel, ok, message: str) -> None:
        """Colour a test status label. ``ok`` is True (green), False (red), or the
        string "warn" (amber) for a partial pass (e.g. primary works, fallback failed)."""
        if ok == "warn":
            color = "#d8932a"
        elif ok:
            color = "#80c080"
        else:
            color = "#c04040"
        label.setText(_translate_status_message(message))
        label.setStyleSheet(f"color: {color};")

    def _set_test_pending(self, label: QLabel, message: str = "Testing...") -> None:
        """Set test pending."""
        label.setText(_translate_status_message(message))
        label.setStyleSheet("color: #c0c040;")

    def _queue_test_progress(self, test_key: str, token: int, message: str) -> None:
        """Queue an in-progress status update from a worker thread."""
        with self._pending_test_progress_lock:
            self._pending_test_progress.append((test_key, token, message))

    def _set_status_label(self, label: QLabel, ok, message: str) -> None:
        """Set status label."""
        if ok is None:
            color = "palette(placeholder-text)"
        elif ok:
            color = "#80c080"
        else:
            color = "#c04040"
        label.setText(_translate_status_message(message))
        label.setStyleSheet(f"color: {color};")

    def _queue_status_result(self, token: int, attr: str, ok, message: str) -> None:
        """Handle queue status result for settings dialog."""
        with self._pending_status_results_lock:
            self._pending_status_results.append((token, attr, ok, message))

    def _cancel_status_refresh(self) -> None:
        """Cancel status refresh."""
        self._status_refresh_token += 1
        self._status_refresh_running = False
        self._pending_status_attrs = set()
        with self._pending_status_results_lock:
            self._pending_status_results.clear()
        if self._status_result_timer.isActive():
            self._status_result_timer.stop()

    def _cancel_async_ui_updates(self) -> None:
        """Cancel async ui updates."""
        self._cancel_status_refresh()
        self._running_test_tokens.clear()
        self._latest_test_token.clear()
        with self._pending_test_results_lock:
            self._pending_test_results.clear()
        with self._pending_test_progress_lock:
            self._pending_test_progress.clear()
        if self._test_result_timer.isActive():
            self._test_result_timer.stop()
        for timer_name in ("_auth_poll_timer", "_github_auth_poll_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None and timer.isActive():
                timer.stop()

    def _schedule_open_status_refresh(self) -> None:
        """Schedule open status refresh."""
        if getattr(self, "_disposing", False):
            return
        self._status_refresh_token += 1
        token = self._status_refresh_token
        self._status_refresh_running = True
        self._pending_status_attrs = {
            "_chatgpt_status_lbl",
            "_github_status_lbl",
            "_copilot_status_lbl",
        }
        for attr in ("_chatgpt_status_lbl", "_github_status_lbl", "_copilot_status_lbl"):
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, None, "Checking status...")
        if not self._status_result_timer.isActive():
            self._status_result_timer.start()
        QTimer.singleShot(_AUTH_STATUS_TIMEOUT_MS, lambda tok=token: self._expire_status_refresh(tok))

        def _chatgpt_worker() -> None:
            """Read ChatGPT sign-in status without blocking other providers."""
            try:
                from core.auth import chatgpt as chatgpt_auth

                tokens = chatgpt_auth.get_tokens()
                if tokens:
                    aid = tokens.get("account_id") or ""
                    label = "Logged in" + (f" - account {aid[:8]}..." if aid else "")
                    self._queue_status_result(token, "_chatgpt_status_lbl", True, label)
                else:
                    self._queue_status_result(token, "_chatgpt_status_lbl", None, "Not logged in")
            except Exception as exc:
                self._queue_status_result(token, "_chatgpt_status_lbl", False, f"Error reading status: {exc}")

        def _github_worker() -> None:
            """Read GitHub sign-in status without waiting on ChatGPT/Copilot."""
            try:
                from core.auth import github as github_auth

                tokens = github_auth.get_tokens()
                if tokens:
                    login = (tokens.get("user") or {}).get("login") or ""
                    scopes = tokens.get("scope") or ""
                    label = "Logged in" + (f" as {login}" if login else "")
                    if scopes:
                        label += f"\nScopes: {scopes}"
                    self._queue_status_result(token, "_github_status_lbl", True, label)
                else:
                    self._queue_status_result(token, "_github_status_lbl", None, "Not logged in")
            except Exception as exc:
                self._queue_status_result(token, "_github_status_lbl", False, f"Error reading status: {exc}")

        def _copilot_worker() -> None:
            """Read Copilot token status independently."""
            try:
                from core.auth import copilot_auth

                stored, message = copilot_auth.token_status()
                self._queue_status_result(token, "_copilot_status_lbl", bool(stored), message)
            except Exception as exc:
                self._queue_status_result(token, "_copilot_status_lbl", False, f"Keychain error: {exc}")

        for name, worker in (
            ("settings-status-chatgpt", _chatgpt_worker),
            ("settings-status-github", _github_worker),
            ("settings-status-copilot", _copilot_worker),
        ):
            threading.Thread(target=worker, daemon=True, name=name).start()

    def _expire_status_refresh(self, token: int) -> None:
        """Replace stuck open-time auth checks with an actionable status."""
        if token != self._status_refresh_token or not self._status_refresh_running:
            return
        with self._pending_status_results_lock:
            queued = [item for item in self._pending_status_results if item[0] == token]
            self._pending_status_results = [
                item for item in self._pending_status_results if item[0] != token
            ]
        for _token, attr, ok, message in queued:
            if attr not in self._pending_status_attrs:
                continue
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, ok, message)
            self._pending_status_attrs.discard(attr)
        if not self._pending_status_attrs:
            self._status_refresh_running = False
            if self._status_result_timer.isActive():
                self._status_result_timer.stop()
            return
        message = t("Status check timed out. Sign-in may still work; try again or restart Wisp.")
        for attr in tuple(self._pending_status_attrs):
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, False, message)
        self._pending_status_attrs = set()
        self._status_refresh_running = False
        if self._status_result_timer.isActive():
            self._status_result_timer.stop()

    def _drain_status_results(self) -> None:
        """Handle drain status results for settings dialog."""
        if getattr(self, "_disposing", False):
            self._cancel_status_refresh()
            return
        with self._pending_status_results_lock:
            pending = list(self._pending_status_results)
            self._pending_status_results.clear()
        for token, attr, ok, message in pending:
            if token != self._status_refresh_token:
                continue
            if attr not in self._pending_status_attrs:
                continue
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, ok, message)
            self._pending_status_attrs.discard(attr)
        if not self._pending_status_attrs:
            self._status_refresh_running = False
        if not self._status_refresh_running and not pending:
            self._status_result_timer.stop()

    def _start_async_test(
        self,
        test_key: str,
        status_label: QLabel,
        runner,
        *,
        pending_message: str = "Testing...",
    ) -> None:
        """Start async test."""
        if getattr(self, "_disposing", False):
            return
        token = self._latest_test_token.get(test_key, 0) + 1
        self._latest_test_token[test_key] = token
        self._running_test_tokens.add((test_key, token))
        self._set_test_pending(status_label, pending_message)

        def _worker() -> None:
            """Handle worker for settings dialog."""
            try:
                ok, message = runner()
            except Exception as exc:
                ok, message = False, f"Test failed: {exc}"
            with self._pending_test_results_lock:
                self._pending_test_results.append((test_key, token, ok, message))

        threading.Thread(target=_worker, daemon=True).start()
        if not self._test_result_timer.isActive():
            self._test_result_timer.start()

    def _drain_test_results(self) -> None:
        """Handle drain test results for settings dialog."""
        if getattr(self, "_disposing", False):
            self._cancel_async_ui_updates()
            return
        with self._pending_test_progress_lock:
            progress = list(self._pending_test_progress)
            self._pending_test_progress.clear()
        with self._pending_test_results_lock:
            pending = list(self._pending_test_results)
            self._pending_test_results.clear()
        for test_key, token, message in progress:
            if self._latest_test_token.get(test_key) != token:
                continue
            label = getattr(self, f"_{test_key}_status_lbl", None)
            if isinstance(label, QLabel):
                self._set_test_pending(label, message)
        for test_key, token, ok, message in pending:
            self._running_test_tokens.discard((test_key, token))
            if self._latest_test_token.get(test_key) != token:
                continue
            label = getattr(self, f"_{test_key}_status_lbl", None)
            if isinstance(label, QLabel):
                self._set_test_status(label, ok, message)
            if test_key == "kokoro_install" and ok:
                self._refresh_kokoro_install_status()
            elif test_key == "kokoro_install":
                button = getattr(self, "_kokoro_install_btn", None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
            elif test_key == "elevenlabs_install" and ok:
                self._refresh_elevenlabs_install_status()
            elif test_key == "elevenlabs_install":
                button = getattr(self, "_elevenlabs_install_btn", None)
                if isinstance(button, QPushButton):
                    button.setEnabled(True)
        if not self._running_test_tokens and not pending and not progress:
            self._test_result_timer.stop()

    def _test_llm_route(self, *, section_key: str, route_name: str, status_label: QLabel, image: bool = False) -> None:
        """Verify llm route behavior."""
        from core.llm_clients import client as llm

        rows = self._model_section_rows.get(section_key, [])
        if not rows:
            self._set_test_status(status_label, False, "No model configured.")
            return
        # Collect the primary plus every fallback row up front — Qt widgets are
        # not safe to touch from the worker thread, so read them here.
        routes: list[tuple[str, str]] = []
        for row in rows:
            provider = (row["api_key_combo"].currentData() or "").strip().lower()
            model = self._model_value(row)
            if provider and model:
                routes.append((provider, model))
        if not routes:
            self._set_test_status(status_label, False, "No model configured.")
            return
        anthropic_api_key = self._effective_secret_value_from_provider("anthropic")
        custom_base_url = _get(self._fields["CUSTOM_BASE_URL"]).strip()
        compat_keys = {
            p: self._effective_secret_value_from_provider(p)
            for p in _PROVIDER_KEY_NAMES
        }
        test_key = {
            "LLM": "llm_test",
            "VISION_LLM": "vision_test",
            "MEMORY_LLM": "memory_test",
        }[section_key]

        def _run_all_routes():
            # Each route is probed independently; a failure on one is reported
            # against that exact provider/model and never attributed to another.
            """Run all routes."""
            results: list[tuple[str, bool, str, str, str]] = []
            for idx, (provider, model) in enumerate(routes):
                label = "Primary" if idx == 0 else f"Fallback {idx}"
                ok, message = llm.test_route_connection(
                    provider,
                    model,
                    route_name,
                    image=image,
                    anthropic_api_key=anthropic_api_key,
                    custom_base_url=custom_base_url,
                    compat_keys=compat_keys,
                )
                results.append((label, ok, provider, model, message))

            lines: list[str] = []
            for label, ok, provider, model, message in results:
                detail = "OK" if ok else _short_test_error(message, route_name)
                lines.append(f"{'✓' if ok else '✗'} {label} — {provider} / {model}: {detail}")

            primary_ok = results[0][1]
            failed = [label for label, ok, *_ in results if not ok]
            if not failed:
                status: object = True
            elif primary_ok:
                # Only fallbacks failed — the model you'll actually use works.
                status = "warn"
                lines.append("")
                lines.append(
                    f"Primary works. {len(failed)} fallback(s) failed "
                    f"({', '.join(failed)}) — fix or remove just those rows."
                )
            else:
                status = False
            return status, "\n".join(lines)

        self._start_async_test(test_key, status_label, _run_all_routes)

    def _test_primary_llm_connection(self) -> None:
        """Verify primary llm connection behavior."""
        self._test_llm_route(
            section_key="LLM",
            route_name="LLM",
            status_label=self._llm_test_status_lbl,
        )

    def _test_vision_connection(self) -> None:
        """Verify vision connection behavior."""
        self._test_llm_route(
            section_key="VISION_LLM",
            route_name="VISION_LLM",
            status_label=self._vision_test_status_lbl,
            image=True,
        )

    def _test_memory_connection(self) -> None:
        """Verify memory connection behavior."""
        self._test_llm_route(
            section_key="MEMORY_LLM",
            route_name="MEMORY_LLM",
            status_label=self._memory_test_status_lbl,
        )

    def _test_tts_connection(self) -> None:
        """Verify tts connection behavior."""
        from core import tts

        provider = _get(self._fields["TTS_PROVIDER"]).strip().lower()
        cartesia_api_key = self._effective_secret_value("CARTESIA_API_KEY")
        cartesia_voice_id = _get(self._fields["CARTESIA_VOICE_ID"]).strip()
        elevenlabs_api_key = self._effective_secret_value("ELEVENLABS_API_KEY")
        # OpenAI TTS reuses the OpenAI key managed in the Models tab's key table.
        openai_api_key = self._effective_secret_value_from_provider("openai")
        openai_voice = _get(self._fields["OPENAI_TTS_VOICE"]).strip()
        openai_model = _get(self._fields["OPENAI_TTS_MODEL"]).strip()
        custom_base_url = _get(self._fields["TTS_CUSTOM_BASE_URL"]).strip()
        custom_api_key = self._effective_secret_value("TTS_CUSTOM_API_KEY")
        custom_voice = _get(self._fields["TTS_CUSTOM_VOICE"]).strip()
        custom_model = _get(self._fields["TTS_CUSTOM_MODEL"]).strip()
        gpt_sovits_url = _get(self._fields["GPT_SOVITS_URL"]).strip()
        gpt_sovits_ref_audio_path = _get(self._fields["GPT_SOVITS_REF_AUDIO_PATH"]).strip()
        gpt_sovits_prompt_text = _get(self._fields["GPT_SOVITS_PROMPT_TEXT"]).strip()
        gpt_sovits_prompt_lang = _get(self._fields["GPT_SOVITS_PROMPT_LANG"]).strip()
        gpt_sovits_text_lang = _get(self._fields["GPT_SOVITS_TEXT_LANG"]).strip()
        kokoro_voice = _get(self._fields["KOKORO_VOICE"]).strip()
        kokoro_lang_code = _get(self._fields["KOKORO_LANG_CODE"]).strip()
        kokoro_device = _get(self._fields["KOKORO_DEVICE"]).strip()
        self._start_async_test(
            "tts_test",
            self._tts_test_status_lbl,
            lambda: tts.test_connection(
                provider,
                cartesia_api_key=cartesia_api_key,
                cartesia_voice_id=cartesia_voice_id,
                elevenlabs_api_key=elevenlabs_api_key,
                openai_api_key=openai_api_key,
                openai_voice=openai_voice,
                openai_model=openai_model,
                custom_base_url=custom_base_url,
                custom_api_key=custom_api_key,
                custom_voice=custom_voice,
                custom_model=custom_model,
                gpt_sovits_url=gpt_sovits_url,
                gpt_sovits_ref_audio_path=gpt_sovits_ref_audio_path,
                gpt_sovits_prompt_text=gpt_sovits_prompt_text,
                gpt_sovits_prompt_lang=gpt_sovits_prompt_lang,
                gpt_sovits_text_lang=gpt_sovits_text_lang,
                kokoro_voice=kokoro_voice,
                kokoro_lang_code=kokoro_lang_code,
                kokoro_device=kokoro_device,
            ),
        )

    @staticmethod
    def _reset_stt_model_in_background() -> None:
        """Reset stt model in background."""
        if os.environ.get("WISP_MACOS_PY_UI_HOST") == "1":
            # In the split worker app, the UI process must not import core.stt:
            # that pulls in NumPy/faster-whisper and can stall Qt while Python is
            # starting other threads. The supervisor's settings-applied flow
            # resets STT in the audio worker instead.
            return

        def _worker():
            """Handle worker for settings dialog."""
            try:
                from core import stt as _stt
                _stt.reset_model()
            except Exception as exc:  # noqa: BLE001 - settings save should never hang on STT reset
                _settings_log.warning("STT model reset skipped/failed: %s", exc)

        threading.Thread(target=_worker, daemon=True, name="settings-stt-reset").start()

    def _rebuild_stt_languages(self) -> None:
        """Cantonese (yue) is only supported by large-v3; omit it from the
        language list for smaller models so it can't be selected into a
        combination that produces nothing usable."""
        combo = self._fields.get("STT_LANGUAGE")
        model_combo = self._fields.get("STT_MODEL")
        if combo is None or model_combo is None:
            return
        supports_yue = _get(model_combo) == "large-v3"
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for label, value in _STT_LANGUAGE_OPTIONS:
            if value == "yue" and not supports_yue:
                continue
            combo.addItem(label, value)
        idx = combo.findData(current)
        if idx < 0:
            idx = combo.findData("")  # fall back to auto-detect
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _refresh_stt_active_backend(self) -> None:
        """Update the STT backend label without importing the heavy STT stack."""
        lbl = getattr(self, "_stt_active_lbl", None)
        if lbl is None:
            return

        import config as cfg

        env = getattr(self, "_env", {})
        model = _get(self._fields.get("STT_MODEL")) or env.get("STT_MODEL", cfg.STT_MODEL)
        device = _get(self._fields.get("STT_DEVICE")) or env.get("STT_DEVICE", cfg.STT_DEVICE)
        compute = _get(self._fields.get("STT_COMPUTE_TYPE")) or env.get(
            "STT_COMPUTE_TYPE",
            cfg.STT_COMPUTE_TYPE,
        )

        info = None
        loaded_stt = sys.modules.get("core.stt")
        if loaded_stt is not None:
            try:
                info = loaded_stt.active_backend()
            except Exception:
                info = None

        configured_summary = f"{model} · {device} / {compute}"
        if info is None:
            lbl.setText(
                t("Configured backend: {summary} — active backend appears after recording starts.").format(
                    summary=configured_summary
                )
            )
            lbl.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")
            return

        summary = f"{info['model']} · {info['device']} / {info['compute']}"
        if info["degraded"]:
            lbl.setText(
                t("Active backend: {summary} — GPU not in use. Free the GPU and "
                  "restart to recover quality.").format(summary=summary)
            )
            lbl.setStyleSheet("color: #c0c040; font-size: 9pt;")
        else:
            lbl.setText(t("Active backend: {summary}").format(summary=summary))
            lbl.setStyleSheet("color: #80c080; font-size: 9pt;")

    def _preload_stt_model(self) -> None:
        """Download (first use) and load the speech model now, with feedback.

        On a fresh machine the model weights aren't bundled — they download on
        the first hold-to-talk, which looks like a hang or silently fails when
        offline. This button fetches them up front on a worker thread and reports
        success or a clear offline message in the backend readout.
        """
        btn = getattr(self, "_stt_download_btn", None)
        lbl = getattr(self, "_stt_active_lbl", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText(t("Loading..."))
        if lbl is not None:
            lbl.setText(
                t(
                    "Loading the speech model... the first time downloads it "
                    "(about 150 MB for the base model)."
                )
            )
            lbl.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")

        carrier = _SttPreloadSignals()
        carrier.done.connect(self._on_stt_model_preloaded)
        self._stt_preload_carrier = carrier  # keep alive until the load completes

        def _worker():
            """Load the model off the UI thread; first call downloads it."""
            try:
                from core import stt as _stt
                info = _stt.preload_model()
                carrier.done.emit(info, "")
            except Exception as exc:  # noqa: BLE001 — surfaced to the user below
                carrier.done.emit(None, str(exc))

        threading.Thread(target=_worker, daemon=True, name="settings-stt-preload").start()

    def _on_stt_model_preloaded(self, _info, err: str) -> None:
        """Apply an STT preload result on the Qt thread."""
        self._stt_preload_carrier = None
        btn = getattr(self, "_stt_download_btn", None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText(t("Download / load model now"))
        lbl = getattr(self, "_stt_active_lbl", None)
        if err:
            if lbl is not None:
                lbl.setText(
                    t(
                        "Could not load the speech model. Connect to the internet "
                        "for the first download, then try again."
                    )
                )
                lbl.setStyleSheet("color: #c0392b; font-size: 9pt;")
            return
        # core.stt is now imported, so the readout can show the live backend.
        self._refresh_stt_active_backend()

    def _stt_fields_changed(self, old_env: dict[str, str]) -> bool:
        """Handle STT fields changed for settings dialog."""
        import config as cfg

        return any(
            _get(self._fields[key]) != old_env.get(key, str(getattr(cfg, key, "")))
            for key in ("STT_MODEL", "STT_COMPUTE_TYPE", "STT_LANGUAGE", "STT_BEAM_SIZE", "STT_DEVICE")
        )

    @staticmethod
    def _block_uses_live_tools(blk: dict) -> bool:
        """Handle block uses live tools for settings dialog."""
        uses_context_tool = any(
            str(blk[key].currentData()) == "model"  # type: ignore[attr-defined]
            for key in (
                "context_documents_mode",
                "context_browser_mode",
                "context_github_mode",
                "context_memory_mode",
            )
        )
        uses_override_tool = any(
            mode in {"on", "model"}
            for mode in (blk.get("tool_overrides") or {}).values()
        )
        file_access = blk.get("file_access")
        uses_file_tool = str(file_access.currentData() if file_access else "off") != "off"
        return uses_context_tool or uses_override_tool or uses_file_tool

    @staticmethod
    def _block_uses_screenshot(blk: dict, mode: str) -> bool:
        """Handle block uses screenshot for settings dialog."""
        return str(blk["context_screenshot"].currentData()) == mode  # type: ignore[attr-defined]

    def _capability_warnings_for_values(self, vals: dict[str, str]) -> tuple[list[str], dict[str, list[str]]]:
        """Handle capability warnings for values for settings dialog."""
        warnings: list[str] = []
        warnings_by_target: dict[str, list[str]] = {}

        def add_warning(targets: list[str], warning: str) -> None:
            """Add warning."""
            warnings.append(warning)
            for target in targets:
                warnings_by_target.setdefault(target, []).append(warning)

        try:
            from core.llm_clients.client import (
                screenshot_capability_warnings,
                tool_capability_warnings,
                subscription_auth_warnings,
            )

            vb = self._voice_block
            sb = getattr(self, "_snip_block", None)
            context_blocks = [*self._caller_blocks, vb]
            if sb:
                context_blocks.append(sb)
            all_blocks = context_blocks
            screenshot_modes = [
                str(blk["context_screenshot"].currentData())  # type: ignore[attr-defined]
                for blk in all_blocks
            ]
            screenshot_warnings = screenshot_capability_warnings(
                screenshot_modes,
                llm_provider=vals.get("LLM_PROVIDER", ""),
                llm_model=vals.get("LLM_MODEL", ""),
                vision_provider=vals.get("VISION_LLM_PROVIDER", ""),
                vision_model=vals.get("VISION_LLM_MODEL", ""),
            )
            screenshot_targets: set[str] = set()
            if any(self._block_uses_screenshot(blk, "auto") for blk in all_blocks):
                screenshot_targets.add("VISION_LLM")
            if any(self._block_uses_screenshot(blk, "model") for blk in all_blocks):
                screenshot_targets.add("LLM")
            if any(self._block_uses_screenshot(blk, mode) for blk in self._caller_blocks for mode in ("auto", "model")):
                screenshot_targets.add("Caller Hotkeys")
            if any(self._block_uses_screenshot(vb, mode) for mode in ("auto", "model")):
                screenshot_targets.add("Voice (hold to talk)")
            for warning in screenshot_warnings:
                add_warning(sorted(screenshot_targets) or ["LLM"], warning)

            llm_provider = vals.get("LLM_PROVIDER", "")
            vision_provider = vals.get("VISION_LLM_PROVIDER", "")
            auth_targets: list[str] = []
            if llm_provider.strip().lower() == "chatgpt":
                auth_targets.extend(["Provider credentials", "Authentication", "LLM"])
            if llm_provider.strip().lower() == "copilot":
                auth_targets.extend(["Provider credentials", "API Keys", "LLM"])
            if vision_provider.strip().lower() == "chatgpt":
                auth_targets.extend(["Provider credentials", "Authentication", "VISION_LLM"])
            if vision_provider.strip().lower() == "copilot":
                auth_targets.extend(["Provider credentials", "API Keys", "VISION_LLM"])
            for warning in subscription_auth_warnings(
                llm_provider=llm_provider,
                vision_provider=vision_provider,
            ):
                add_warning(list(dict.fromkeys(auth_targets)) or ["Authentication"], warning)

            caller_tools = any(self._block_uses_live_tools(blk) for blk in self._caller_blocks)
            voice_tools = self._block_uses_live_tools(vb)
            snip_tools = bool(sb) and self._block_uses_live_tools(sb)
            tool_warnings = tool_capability_warnings(
                caller_tools or voice_tools or snip_tools,
                llm_provider=llm_provider,
            )
            tool_targets = ["LLM"]
            if caller_tools:
                tool_targets.append("Caller Hotkeys")
            if voice_tools:
                tool_targets.append("Voice (hold to talk)")
            if snip_tools:
                tool_targets.append("Snip screen region")
            for warning in tool_warnings:
                add_warning(tool_targets, warning)
        except Exception:
            return [], {}

        return warnings, warnings_by_target

    def _apply_settings(self) -> bool:
        """Save settings and apply changes live. Returns True on success."""
        old_env = dict(self._env)
        stt_changed = self._stt_fields_changed(old_env)
        saved = False
        self._last_save_warnings = []
        self._saving_settings = True
        try:
            saved = self._do_save()
            if saved:
                import config
                from core.llm_clients import client as _llm
                from core import tts as _tts
                from ui.shared.theme import apply_app_theme
                config.reload()
                _llm.reset_clients()
                _tts.reset_connections()
                if stt_changed:
                    self._reset_stt_model_in_background()
                apply_app_theme()
                self._apply_dialog_theme()
                localize_widget_tree(self)
                if self._on_apply:
                    new_env = _read_env()
                    changed_keys = sorted(
                        key
                        for key in set(old_env) | set(new_env)
                        if old_env.get(key) != new_env.get(key)
                    )
                    apply_payload = {"changed_keys": changed_keys}
                    try:
                        self._on_apply(apply_payload)
                    except TypeError:
                        self._on_apply()
                    self._env = new_env
                else:
                    self._env = _read_env()
                self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
                self._refresh_capability_warning_markers()
                self._refresh_search_index()
                self._reset_dirty_baseline()
        except Exception as exc:  # noqa: BLE001 - surface save/apply failures to the user
            _settings_log.exception("Settings apply failed")
            QMessageBox.warning(self, t("Save failed"), str(exc))
            saved = False
        finally:
            self._saving_settings = False
        if saved:
            self._show_save_warnings()
            return True
        self._refresh_dirty_state()
        return False

    def _show_save_warnings(self) -> None:
        """Show non-fatal save warnings without blocking Apply/Confirm."""
        warnings = list(getattr(self, "_last_save_warnings", []))
        self._last_save_warnings = []
        if not warnings:
            return
        translated_warnings = [t(warning) for warning in warnings]
        box = QMessageBox(self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("Heads up"))
        box.setText(t("Your settings were saved, but:") + "\n\n• " + "\n\n• ".join(translated_warnings))
        self._open_warning_boxes.append(box)

        def _forget_box(_result: int, *, message_box=box) -> None:
            if message_box in self._open_warning_boxes:
                self._open_warning_boxes.remove(message_box)

        box.finished.connect(_forget_box)
        box.open()

    def _apply(self):
        """Save settings and keep the dialog open."""
        self._apply_settings()

    def _confirm(self):
        """Save settings, apply changes live, then close the dialog."""
        self._refresh_dirty_state()
        if not self._dirty_keys:
            # Nothing changed since the dialog opened or the last Apply, so skip
            # the heavy reload (config.reload, model/TTS/STT reconnect, worker
            # restart) that would otherwise show a needless loading pass.
            self.accept()
            return
        if self._apply_settings():
            self.accept()

    @staticmethod
    def _reset_env_keys_for_page(page_name: str, env: dict[str, str]) -> set[str]:
        """Reset env keys for page."""
        page = (page_name or "").strip()
        exact: dict[str, set[str]] = {
            "LLM": {
                "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACKS",
                "VISION_LLM_PROVIDER", "VISION_LLM_MODEL", "VISION_LLM_FALLBACKS",
                "MEMORY_LLM_PROVIDER", "MEMORY_LLM_MODEL", "MEMORY_LLM_FALLBACKS",
                "TOOL_LLM_MODEL", "CHAT_TOOL_TRACE_UI", "CHAT_REASONING_EFFORT",
                "WISP_PLANNED_CHUNKING", "WISP_PLANNED_CHUNKING_CHUNKS", "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS",
                "CHAT_AUTO_ELABORATE", "CHAT_ELABORATE_PROMPT", "CUSTOM_BASE_URL",
                "GITHUB_CLIENT_ID", "GITHUB_OAUTH_SCOPES",
                "COPILOT_CLI_URL", "COPILOT_CLI_PATH",
            },
            "TTS / Voice": {
                "TTS_PROVIDER", "TTS_SPEAK_REPLIES", "CARTESIA_VOICE_ID",
                "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL",
                "OPENAI_TTS_VOICE", "OPENAI_TTS_MODEL",
                "TTS_CUSTOM_BASE_URL", "TTS_CUSTOM_VOICE", "TTS_CUSTOM_MODEL", "TTS_CUSTOM_SAMPLE_RATE",
                "GPT_SOVITS_URL", "GPT_SOVITS_REF_AUDIO_PATH", "GPT_SOVITS_PROMPT_TEXT",
                "GPT_SOVITS_PROMPT_LANG", "GPT_SOVITS_TEXT_LANG", "GPT_SOVITS_SAMPLE_RATE",
                "KOKORO_VOICE", "KOKORO_LANG_CODE", "KOKORO_DEVICE", "KOKORO_SPEED", "KOKORO_SAMPLE_RATE",
                "TTS_VOLUME", "TTS_READ_ALOUD_MIN_WORDS", "TTS_READ_ALOUD_MAX_WORDS",
                "STT_MODEL", "STT_COMPUTE_TYPE", "STT_LANGUAGE", "STT_BEAM_SIZE", "STT_DEVICE",
                "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS", "STT_BACKGROUND_CHUNK_STEP_SECONDS",
                "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS", "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS",
            },
            "Prompts": {
                "SYSTEM_PROMPT_UTILITY",
            },
            "Keybinds": {
                "HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP", "HOTKEY_VOICE",
                "HOTKEY_READ_SELECTION_ALOUD", "HOTKEY_DICTATE", "DICTATE_MODE", "INTENT_CONTEXT_TOGGLE_KEYS",
                "INTENT_OVERLAY_TIMEOUT_MS",
                "SNIP_CONTEXT_AMBIENT", "SNIP_CONTEXT_CLIPBOARD", "SNIP_CONTEXT_DOCUMENTS",
                "SNIP_CONTEXT_DOCUMENTS_MODE", "SNIP_CONTEXT_BROWSER_MODE", "SNIP_CONTEXT_GITHUB_MODE",
                "SNIP_CONTEXT_MEMORY_MODE", "SNIP_CONTEXT_SCREENSHOT", "SNIP_CONTEXT_TOOLS",
                "SNIP_FILE_ACCESS", "SNIP_TOOLS",
                "CALLER_COUNT",
            },
            "App": {
                "THEME_MODE", "DARK_MODE", "TRUST_PRIVACY_MODE",
                "ICON_AUTO_HIDE", "DOLL_AUTO_HIDE", "START_ON_LOGIN",
                "THEME_DARK_BG", "THEME_DARK_SURFACE", "THEME_DARK_TEXT", "THEME_DARK_ACCENT",
                "THEME_LIGHT_BG", "THEME_LIGHT_SURFACE", "THEME_LIGHT_TEXT", "THEME_LIGHT_ACCENT",
                "APP_LANGUAGE", "ASSISTANT_LANGUAGE",
                "ICON_SIZE", "DOLL_SIZE", "ICON_BACKSTOP_MS", "DOLL_ICON_BACKSTOP_MS",
                "BUBBLE_WIDTH", "BUBBLE_LINES", "BUBBLE_FONT_SIZE",
                "BUBBLE_COLOR", "BUBBLE_TEXT_COLOR",
                "BUBBLE_READ_WORD_COLOR", "BUBBLE_SCROLL_ENABLED", "BUBBLE_SCROLL_SNAP_ENABLED",
            },
            "Advanced": {
                "CONTEXT_BROWSER_MAX_CHARS", "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS",
                "CONTEXT_TOOL_DOCUMENT_MAX_CHARS", "TOOL_PLUGIN_DIR", "TOOL_GIT_ROOT",
                "TOOL_FILE_ROOTS", "TOOL_FILE_MODE", "TOOL_FILE_BLOCKED_GLOBS",
                "MEMORY_AUTO_CONSOLIDATE", "MEMORY_TOP_K", "MEMORY_CONSOLIDATION_INTERVAL",
                "MEMORY_STM_TOKEN_BUDGET", "BUBBLE_HIDE_DELAY_MS", "BUBBLE_REVEAL_WPM",
                "BUBBLE_HOLD_REVEAL_WPM", "BUBBLE_SCROLL_SNAP_DELAY_MS", "TTS_PLAYBACK_RATE",
                "TTS_HOLD_PLAYBACK_RATE",
            },
        }
        keys = set(exact.get(page, set()))
        if page == "Keybinds":
            keys.update(
                key for key in env
                if key.startswith("CALLER_") or key.startswith("VOICE_") or key.startswith("SNIP_")
            )
        return keys

    def _reload_after_page_reset(self, page_name: str = "") -> None:
        """Handle reload after page reset for settings dialog."""
        try:
            import config
            from core.llm_clients import client as _llm
            from core import tts as _tts
            from ui.shared.theme import apply_app_theme

            config.reload()
            _llm.reset_clients()
            _tts.reset_connections()
            apply_app_theme()
            self._apply_dialog_theme()
        except Exception as exc:  # noqa: BLE001 - reset already happened on disk
            _settings_log.error("Live reload after page reset failed: %s", exc)
        self._env = _read_env()
        self._active_preset_slug = self._env.get(_SETTINGS_PRESET_KEY, "")
        self._load_values()
        localize_widget_tree(self)
        self._refresh_tab_labels()
        self._refresh_search_index()
        if page_name == "TTS / Voice":
            self._reset_stt_model_in_background()
        if self._on_apply:
            self._on_apply()

    def _reset_current_page(self) -> None:
        """Reset current page."""
        tabs = getattr(self, "_tabs", None)
        if tabs is None:
            return
        page = self._current_tab_name()
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("Reset page?")
        confirm.setText(f"Reset the {page} page to defaults?")
        confirm.setInformativeText(
            "Only settings on this page will be reset. API keys, OAuth sign-ins, "
            "stored memory, conversations, addons, and settings on other pages "
            "will be left alone."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        try:
            current_env = _read_env()
            active_preset = self._preset_slug(getattr(self, "_active_preset_slug", ""))
            if active_preset:
                page_keys = self._preset_page_keys(active_preset, page, current_env)
                remove_keys = set(page_keys)
                remove_keys.update(
                    self._preset_override_keys_for_keys(active_preset, page_keys, current_env)
                )
                preset_values = {
                    key: value
                    for key, value in _PRESET_DEFAULTS.get(active_preset, {}).items()
                    if key in page_keys
                }
                if page == "Keybinds":
                    ctx = _PRESET_CONTEXT_DEFAULTS.get(active_preset, {})
                    caller_count = int(current_env.get("CALLER_COUNT", "0") or "0")
                    for idx in range(1, caller_count + 1):
                        if "documents" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_DOCUMENTS_MODE"] = ctx["documents"]
                        if "browser" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_BROWSER_MODE"] = ctx["browser"]
                        if "github" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_GITHUB_MODE"] = ctx["github"]
                        if "memory" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_MEMORY_MODE"] = ctx["memory"]
                        if "screenshot" in ctx:
                            preset_values[f"CALLER_{idx}_CONTEXT_SCREENSHOT"] = ctx["screenshot"]
                        if ctx.get("clear_tools", "").lower() == "true":
                            preset_values[f"CALLER_{idx}_TOOLS"] = ""
                    if "documents" in ctx:
                        preset_values["VOICE_CONTEXT_DOCUMENTS_MODE"] = ctx["documents"]
                    if "browser" in ctx:
                        preset_values["VOICE_CONTEXT_BROWSER_MODE"] = ctx["browser"]
                    if "github" in ctx:
                        preset_values["VOICE_CONTEXT_GITHUB_MODE"] = ctx["github"]
                    if "memory" in ctx:
                        preset_values["VOICE_CONTEXT_MEMORY_MODE"] = ctx["memory"]
                    if "screenshot" in ctx:
                        preset_values["VOICE_CONTEXT_SCREENSHOT"] = ctx["screenshot"]
                    if ctx.get("clear_tools", "").lower() == "true":
                        preset_values["VOICE_TOOLS"] = ""
                preset_values[_SETTINGS_PRESET_KEY] = active_preset
            else:
                remove_keys = self._reset_env_keys_for_page(page, current_env)
                preset_values = {}
            for key in remove_keys:
                os.environ.pop(key, None)
            _write_env(preset_values, remove_keys=remove_keys - set(preset_values))
            self._reload_after_page_reset(page)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, t("Reset page failed"), str(exc))
            return

        QMessageBox.information(
            self,
            t("Page reset"),
            t("{page} settings were reset to defaults.").format(page=page),
        )

    def _reset_all(self) -> None:
        """Factory reset: erase every setting and delete all API keys.

        Pops up a detailed warning the user must confirm, then deletes all known
        secrets from the OS keychain, wipes the .env file (and the matching values
        from this process), reloads config + live app, and refreshes the dialog.
        """
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("Reset all settings?")
        confirm.setText("Reset Wisp to its defaults? This cannot be undone.")
        confirm.setInformativeText(
            "This will permanently:\n"
            "• DELETE every API key from your OS keychain "
            "(Groq, OpenAI, Anthropic, Google, DeepSeek, OpenRouter, Mistral, "
            "xAI, Together, Cerebras, Z.AI, NVIDIA, SambaNova, GitHub Models, "
            "Hugging Face, Chutes, Vercel, Fireworks, Cohere, AI21, Nebius, "
            "Cartesia, ElevenLabs, custom)\n"
            "• ERASE all saved settings (models, hotkeys, prompts, theme, "
            "callers, and everything else in your .env)\n"
            "• SIGN YOU OUT of all OAuth logins (ChatGPT, GitHub, GitHub Copilot)\n\n"
            "You will need to re-enter your API keys, sign in again, and "
            "reconfigure the app afterwards.\n\n"
            "Continue?"
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        _settings_log.warning("User requested full settings reset")

        # 1. Delete every known secret from the OS keychain, verifying removal.
        key_failures: list[str] = []
        for name in secret_store.API_KEY_NAMES:
            try:
                secret_store.delete_secret(name)
                if secret_store.get_keychain_secret(name):
                    key_failures.append(name)
                    _settings_log.error("Key %s still present in keychain after delete", name)
                else:
                    _settings_log.info("Deleted %s from OS keychain", name)
            except Exception as exc:  # noqa: BLE001 — collected and surfaced
                key_failures.append(f"{name}: {exc}")
                _settings_log.error("Failed deleting %s from keychain: %s", name, exc)

        # 1b. Sign out of every OAuth provider.
        for label, module_path, func_name in (
            ("ChatGPT",        "core.auth.chatgpt",      "clear_tokens"),
            ("GitHub",         "core.auth.github",       "clear_tokens"),
            ("GitHub Copilot", "core.auth.copilot_auth", "clear_token"),
        ):
            try:
                import importlib
                getattr(importlib.import_module(module_path), func_name)()
                _settings_log.info("Signed out of %s", label)
            except Exception as exc:  # noqa: BLE001 — collected and surfaced
                key_failures.append(f"{label} sign-out: {exc}")
                _settings_log.error("Failed signing out of %s: %s", label, exc)

        # 2. Wipe the .env file and clear matching values from this process so the
        #    reset takes effect live (load_dotenv won't unset removed keys itself).
        try:
            for key in _read_env():
                os.environ.pop(key, None)
            if ENV_PATH.exists():
                ENV_PATH.unlink()
            _settings_log.info("Erased settings file %s", ENV_PATH)
        except OSError as exc:
            _settings_log.error("Could not erase %s: %s", ENV_PATH, exc)
            QMessageBox.warning(
                self, t("Reset error"),
                t("Could not erase the settings file:\n{error}").format(error=exc),
            )

        # 3. Reload config + live app and refresh the dialog to show defaults.
        try:
            import config
            from core.llm_clients import client as _llm
            from core import tts as _tts
            from ui.shared.theme import apply_app_theme
            config.reload()
            _llm.reset_clients()
            _tts.reset_connections()
            self._reset_stt_model_in_background()
            apply_app_theme()
            self._apply_dialog_theme()
        except Exception as exc:  # noqa: BLE001 — reset already happened on disk
            _settings_log.error("Live reload after reset failed: %s", exc)
        self._env = _read_env()
        self._active_preset_slug = ""
        self._load_values()
        localize_widget_tree(self)
        self._refresh_tab_labels()
        self._refresh_search_index()
        for refresh in (
            getattr(self, "_refresh_chatgpt_status", None),
            getattr(self, "_refresh_github_status", None),
            getattr(self, "_refresh_copilot_status", None),
        ):
            if callable(refresh):
                try:
                    refresh()
                except Exception:  # noqa: BLE001 — status labels are cosmetic
                    pass
        if self._on_apply:
            self._on_apply()

        if key_failures:
            QMessageBox.warning(
                self, t("Reset partly complete"),
                t("Settings were reset, but these items could not be fully cleared:")
                + "\n\n"
                + "\n".join(f"• {item}" for item in key_failures)
                + "\n\n"
                + t("You may need to remove them manually (e.g. from your system "
                    "credential store). See the log for details."),
            )
        else:
            QMessageBox.information(
                self, t("Reset complete"),
                t("All API keys were removed from the OS keychain, you were signed "
                  "out of all OAuth logins, and every setting was reset to defaults."),
            )

    def _do_save(self) -> bool:
        """Write .env. Returns True on success, False if validation failed.

        Keychain key storage is attempted first and reports its own per-key
        warnings, but a keychain failure does NOT abort the save: the rest of the
        settings are still written to .env so the user never loses everything just
        because one API key could not reach the OS keychain.
        """
        import config as cfg

        self._save_api_keys_to_keychain()

        def _section_vals(sk):
            """Handle section vals for settings dialog."""
            rows = self._model_section_rows.get(sk, [])
            if not rows:
                return "", "", ""
            primary = rows[0]
            provider = primary["api_key_combo"].currentData() or ""
            model = self._model_value(primary)
            fallbacks = "\n".join(
                f"{r['api_key_combo'].currentData() or ''}:{self._model_value(r)}"
                for r in rows[1:]
                if (r["api_key_combo"].currentData() or "") and self._model_value(r)
            )
            return provider, model, fallbacks

        llm_p, llm_m, llm_f = _section_vals("LLM")
        vis_p, vis_m, vis_f = _section_vals("VISION_LLM")
        mem_p, mem_m, mem_f = _section_vals("MEMORY_LLM")

        # Persist both theme templates (the four swatches edit only the selected
        # mode, so fold their current values back into that template first).
        self._flush_visible_theme_fields()
        theme_vals = {
            f"THEME_{mode.upper()}_{role.upper()}": self._theme_templates.get(mode, {}).get(role, "")
            for mode in ("light", "dark")
            for role in self._THEME_ROLES
        }
        assistant_language = _get(self._fields["ASSISTANT_LANGUAGE"])
        chat_elaborate_prompt = cfg.localize_chat_elaborate_prompt_if_default(
            _get(self._fields["CHAT_ELABORATE_PROMPT"]),
            assistant_language,
        )
        _set(self._fields["CHAT_ELABORATE_PROMPT"], chat_elaborate_prompt)
        system_prompt_utility = cfg.localize_system_prompt_utility_if_default(
            self._fields["SYSTEM_PROMPT_UTILITY"].toPlainText(),  # type: ignore[attr-defined]
            assistant_language,
        )
        _set(self._fields["SYSTEM_PROMPT_UTILITY"], system_prompt_utility)

        vals = {
            "LLM_PROVIDER":      llm_p,
            "LLM_MODEL":         llm_m,
            "LLM_FALLBACKS":     llm_f,
            "VISION_LLM_PROVIDER": vis_p,
            "VISION_LLM_MODEL":    vis_m,
            "VISION_LLM_FALLBACKS": vis_f,
            "MEMORY_LLM_PROVIDER": mem_p,
            "MEMORY_LLM_MODEL":    mem_m,
            "MEMORY_LLM_FALLBACKS": mem_f,
            "CHAT_TOOL_TRACE_UI": str(self._fields["CHAT_TOOL_TRACE_UI"].isChecked()),  # type: ignore[attr-defined]
            "WISP_PLANNED_CHUNKING": str(self._fields["WISP_PLANNED_CHUNKING"].isChecked()),  # type: ignore[attr-defined]
            "WISP_PLANNED_CHUNKING_CHUNKS": _get(self._fields["WISP_PLANNED_CHUNKING_CHUNKS"]),
            "WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS": _get(self._fields["WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS"]),
            "CHAT_REASONING_EFFORT": _get(self._fields["CHAT_REASONING_EFFORT"]),
            "TTS_PROVIDER":      _get(self._fields["TTS_PROVIDER"]),
            "TTS_SPEAK_REPLIES": _get(self._fields["TTS_SPEAK_REPLIES"]),
            "CARTESIA_VOICE_ID": _get(self._fields["CARTESIA_VOICE_ID"]),
            "ELEVENLABS_VOICE_ID": _get(self._fields["ELEVENLABS_VOICE_ID"]),
            "ELEVENLABS_MODEL":  _get(self._fields["ELEVENLABS_MODEL"]),
            "OPENAI_TTS_VOICE":  _get(self._fields["OPENAI_TTS_VOICE"]),
            "OPENAI_TTS_MODEL":  _get(self._fields["OPENAI_TTS_MODEL"]),
            "TTS_CUSTOM_BASE_URL": _get(self._fields["TTS_CUSTOM_BASE_URL"]),
            "TTS_CUSTOM_VOICE":  _get(self._fields["TTS_CUSTOM_VOICE"]),
            "TTS_CUSTOM_MODEL":  _get(self._fields["TTS_CUSTOM_MODEL"]),
            "TTS_CUSTOM_SAMPLE_RATE": _get(self._fields["TTS_CUSTOM_SAMPLE_RATE"]),
            "GPT_SOVITS_URL": _get(self._fields["GPT_SOVITS_URL"]),
            "GPT_SOVITS_REF_AUDIO_PATH": _get(self._fields["GPT_SOVITS_REF_AUDIO_PATH"]),
            "GPT_SOVITS_PROMPT_TEXT": _get(self._fields["GPT_SOVITS_PROMPT_TEXT"]),
            "GPT_SOVITS_PROMPT_LANG": _get(self._fields["GPT_SOVITS_PROMPT_LANG"]),
            "GPT_SOVITS_TEXT_LANG": _get(self._fields["GPT_SOVITS_TEXT_LANG"]),
            "GPT_SOVITS_SAMPLE_RATE": _get(self._fields["GPT_SOVITS_SAMPLE_RATE"]),
            "KOKORO_VOICE": _get(self._fields["KOKORO_VOICE"]),
            "KOKORO_LANG_CODE": _get(self._fields["KOKORO_LANG_CODE"]),
            "KOKORO_DEVICE": _get(self._fields["KOKORO_DEVICE"]),
            "KOKORO_SPEED": _get(self._fields["KOKORO_SPEED"]),
            "KOKORO_SAMPLE_RATE": _get(self._fields["KOKORO_SAMPLE_RATE"]),
            "TTS_VOLUME": _get(self._fields["TTS_VOLUME"]),
            "TTS_READ_ALOUD_MIN_WORDS": _get(self._fields["TTS_READ_ALOUD_MIN_WORDS"]),
            "TTS_READ_ALOUD_MAX_WORDS": _get(self._fields["TTS_READ_ALOUD_MAX_WORDS"]),
            "STT_MODEL":         _get(self._fields["STT_MODEL"]),
            "STT_COMPUTE_TYPE":  _get(self._fields["STT_COMPUTE_TYPE"]),
            "STT_LANGUAGE":      _get(self._fields["STT_LANGUAGE"]),
            "STT_BEAM_SIZE":     _get(self._fields["STT_BEAM_SIZE"]),
            "STT_DEVICE":        _get(self._fields["STT_DEVICE"]),
            "STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS"]
            ),
            "STT_BACKGROUND_CHUNK_STEP_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_STEP_SECONDS"]
            ),
            "STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS"]
            ),
            "STT_BACKGROUND_CHUNK_OVERLAP_SECONDS": _get(
                self._fields["STT_BACKGROUND_CHUNK_OVERLAP_SECONDS"]
            ),
            "HOTKEY_ADD_CONTEXT":  _get(self._fields["HOTKEY_ADD_CONTEXT"]),
            "HOTKEY_CLEAR_CONTEXT": _get(self._fields["HOTKEY_CLEAR_CONTEXT"]),
            "HOTKEY_SNIP":         _get(self._fields["HOTKEY_SNIP"]),
            "HOTKEY_READ_SELECTION_ALOUD": _get(self._fields["HOTKEY_READ_SELECTION_ALOUD"]),
            "INTENT_CONTEXT_TOGGLE_KEYS": _get(self._fields["INTENT_CONTEXT_TOGGLE_KEYS"]),
            "INTENT_OVERLAY_TIMEOUT_MS": _get(self._fields["INTENT_OVERLAY_TIMEOUT_MS"]),
            "CONTEXT_BROWSER_MAX_CHARS": _get(self._fields["CONTEXT_BROWSER_MAX_CHARS"]),
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"]),
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"]),
            "TOOL_FILE_ROOTS": _get(self._fields["TOOL_FILE_ROOTS"]),
            "TOOL_FILE_BLOCKED_GLOBS": _get(self._fields["TOOL_FILE_BLOCKED_GLOBS"]),
            "CUSTOM_BASE_URL":            _get(self._fields["CUSTOM_BASE_URL"]),
            "GITHUB_CLIENT_ID":          _get(self._fields["GITHUB_CLIENT_ID"]),
            "GITHUB_OAUTH_SCOPES":       _get(self._fields["GITHUB_OAUTH_SCOPES"]),
            "MEMORY_AUTO_CONSOLIDATE":   str(self._fields["MEMORY_AUTO_CONSOLIDATE"].isChecked()),  # type: ignore
            "MEMORY_CONSOLIDATION_INTERVAL": _get(self._fields["MEMORY_CONSOLIDATION_INTERVAL"]),
            "MEMORY_TOP_K":             _get(self._fields["MEMORY_TOP_K"]),
            "MEMORY_STM_TOKEN_BUDGET":  _get(self._fields["MEMORY_STM_TOKEN_BUDGET"]),
            "CALLER_COUNT":  str(len(self._caller_blocks)),
            "THEME_MODE":       self._fields["THEME_MODE"].currentData(),  # type: ignore[attr-defined]
            "TRUST_PRIVACY_MODE": str(self._fields["TRUST_PRIVACY_MODE"].isChecked()),  # type: ignore
            "ICON_AUTO_HIDE":    str(self._fields["ICON_AUTO_HIDE"].isChecked()),  # type: ignore
            "START_ON_LOGIN": str(self._fields["START_ON_LOGIN"].isChecked()),  # type: ignore
            "CHAT_AUTO_ELABORATE": str(self._fields["CHAT_AUTO_ELABORATE"].isChecked()),  # type: ignore
            "CHAT_ELABORATE_PROMPT": chat_elaborate_prompt,
            "APP_LANGUAGE": _get(self._fields["APP_LANGUAGE"]),
            "ASSISTANT_LANGUAGE": assistant_language,
            "ICON_SIZE":    _get(self._fields["ICON_SIZE"]),
            "BUBBLE_WIDTH": _get(self._fields["BUBBLE_WIDTH"]),
            "BUBBLE_LINES": _get(self._fields["BUBBLE_LINES"]),
            "BUBBLE_FONT_SIZE": _get(self._fields["BUBBLE_FONT_SIZE"]),
            "BUBBLE_SCROLL_ENABLED": str(self._fields["BUBBLE_SCROLL_ENABLED"].isChecked()),  # type: ignore
            "BUBBLE_SCROLL_SNAP_ENABLED": str(self._fields["BUBBLE_SCROLL_SNAP_ENABLED"].isChecked()),  # type: ignore
            "BUBBLE_COLOR": _get(self._fields["BUBBLE_COLOR"]),
            "BUBBLE_TEXT_COLOR": _get(self._fields["BUBBLE_TEXT_COLOR"]),
            "BUBBLE_READ_WORD_COLOR": _get(self._fields["BUBBLE_READ_WORD_COLOR"]),
            "BUBBLE_REVEAL_WPM": _get(self._fields["BUBBLE_REVEAL_WPM"]),
            "BUBBLE_HOLD_REVEAL_WPM": _get(self._fields["BUBBLE_HOLD_REVEAL_WPM"]),
            "BUBBLE_HIDE_DELAY_MS": _seconds_str_to_ms(
                _get(self._fields["BUBBLE_HIDE_DELAY_S"]), 3500
            ),
            "BUBBLE_SCROLL_SNAP_DELAY_MS": _seconds_str_to_ms(
                _get(self._fields["BUBBLE_SCROLL_SNAP_DELAY_S"]), 2500
            ),
            "TTS_PLAYBACK_RATE": _get(self._fields["TTS_PLAYBACK_RATE"]),
            "TTS_HOLD_PLAYBACK_RATE": _get(self._fields["TTS_HOLD_PLAYBACK_RATE"]),
            "SYSTEM_PROMPT_UTILITY": system_prompt_utility,
        }
        vals.update(self._snip_context_values())
        vals.update(theme_vals)
        tts_provider = str(vals.get("TTS_PROVIDER", "")).strip().lower()
        if tts_provider == "kokoro" and not self._kokoro_installed():
            self._refresh_kokoro_install_status()
            QMessageBox.warning(
                self,
                t("Kokoro not installed"),
                t("Install Kokoro before applying it as the active TTS provider."),
            )
            return False
        if tts_provider == "elevenlabs" and not self._elevenlabs_installed():
            self._refresh_elevenlabs_install_status()
            QMessageBox.warning(
                self,
                t("ElevenLabs not installed"),
                t("Install ElevenLabs before applying it as the active TTS provider."),
            )
            return False
        pending_active_profile = str(getattr(self, "_pending_active_profile", "") or "").strip()
        if pending_active_profile:
            vals["ACTIVE_PROFILE"] = pending_active_profile
            vals["SETTINGS_PROFILE"] = pending_active_profile
        vb = self._voice_block
        vals.update({
            "HOTKEY_VOICE": _get(self._fields["HOTKEY_VOICE"]),
            "VOICE_REVIEW_TRANSCRIPT": _get(self._fields["VOICE_REVIEW_TRANSCRIPT"]),
            "HOTKEY_DICTATE": _get(self._fields["HOTKEY_DICTATE"]),
            "DICTATE_MODE": _get(self._fields["DICTATE_MODE"]),
            "VOICE_CONTEXT_AMBIENT": str(vb["context_ambient"].isChecked()),
            "VOICE_CONTEXT_CLIPBOARD": str(vb["context_clipboard"].isChecked()),
            "VOICE_CONTEXT_DOCUMENTS_MODE": str(vb["context_documents_mode"].currentData()),
            "VOICE_CONTEXT_BROWSER_MODE": str(vb["context_browser_mode"].currentData()),
            "VOICE_CONTEXT_GITHUB_MODE": str(vb["context_github_mode"].currentData()),
            "VOICE_CONTEXT_MEMORY_MODE": str(vb["context_memory_mode"].currentData()),
            "VOICE_CONTEXT_SCREENSHOT": str(vb["context_screenshot"].currentData()),
            "VOICE_FILE_ACCESS": str(vb["file_access"].currentData()),
            "VOICE_TOOLS": format_tool_modes(vb.get("tool_overrides") or {}),
        })
        # Key conflict check (caller hotkeys + special hotkeys)
        all_keys = (
            [_get(blk["hotkey"]).strip().lower() for blk in self._caller_blocks]
            + [
                _get(self._fields[k]).strip().lower()
                for k in (
                    "HOTKEY_ADD_CONTEXT",
                    "HOTKEY_CLEAR_CONTEXT",
                    "HOTKEY_SNIP",
                    "HOTKEY_READ_SELECTION_ALOUD",
                    "HOTKEY_VOICE",
                    "HOTKEY_DICTATE",
                )
            ]
        )
        non_empty = [k for k in all_keys if k]
        if len(non_empty) != len(set(non_empty)):
            QMessageBox.warning(self, t("Duplicate keys"),
                                t("Two or more bindings share the same key.\nPlease resolve conflicts before saving."))
            return False
        for i, blk in enumerate(self._caller_blocks):
            n = i + 1
            vals[f"CALLER_{n}_HOTKEY"]        = _get(blk["hotkey"])
            vals[f"CALLER_{n}_LABEL"]         = _get(blk["label"])
            vals[f"CALLER_{n}_PASTE_BACK"]    = str(blk["paste_back"].isChecked())  # type: ignore
            vals[f"CALLER_{n}_CUSTOM_KEY"]    = _get(blk["custom_key"])
            vals[f"CALLER_{n}_CUSTOM_LABEL"]  = _get(blk["custom_label"])
            vals[f"CALLER_{n}_CONTEXT_AMBIENT"] = str(blk["context_ambient"].isChecked())  # type: ignore
            vals[f"CALLER_{n}_CONTEXT_CLIPBOARD"] = str(blk["context_clipboard"].isChecked())  # type: ignore
            documents_mode = str(blk["context_documents_mode"].currentData())  # type: ignore[attr-defined]
            browser_mode = str(blk["context_browser_mode"].currentData())  # type: ignore[attr-defined]
            github_mode = str(blk["context_github_mode"].currentData())  # type: ignore[attr-defined]
            memory_mode = str(blk["context_memory_mode"].currentData())  # type: ignore[attr-defined]
            vals[f"CALLER_{n}_CONTEXT_DOCUMENTS_MODE"] = documents_mode
            vals[f"CALLER_{n}_CONTEXT_BROWSER_MODE"] = browser_mode
            vals[f"CALLER_{n}_CONTEXT_GITHUB_MODE"] = github_mode
            vals[f"CALLER_{n}_CONTEXT_MEMORY_MODE"] = memory_mode
            vals[f"CALLER_{n}_FILE_ACCESS"] = str(blk["file_access"].currentData())  # type: ignore[attr-defined]
            # Compatibility values for older branches/scripts.
            vals[f"CALLER_{n}_CONTEXT_DOCUMENTS"] = str(documents_mode == "auto")
            vals[f"CALLER_{n}_CONTEXT_TOOLS"] = str(
                any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode))
            )
            vals[f"CALLER_{n}_CONTEXT_SCREENSHOT"] = str(blk["context_screenshot"].currentData())  # type: ignore
            vals[f"CALLER_{n}_TOOLS"] = format_tool_modes(blk.get("tool_overrides") or {})
            vals[f"CALLER_{n}_INTENT_COUNT"]  = str(len(blk["intent_rows"]))
            for j, row in enumerate(blk["intent_rows"]):
                m = j + 1
                intent = cfg.localize_intent_if_default(
                    i,
                    j,
                    {
                        "key": _get(row["key"]),
                        "label": _get(row["label"]),
                        "prompt": _get(row["prompt"]),
                    },
                    vals.get("ASSISTANT_LANGUAGE", ""),
                )
                row["key"].setText(str(intent.get("key", "")))
                row["label"].setText(str(intent.get("label", "")))
                _set(row["prompt"], str(intent.get("prompt", "")))
                vals[f"CALLER_{n}_INTENT_{m}_KEY"]    = str(intent.get("key", ""))
                vals[f"CALLER_{n}_INTENT_{m}_LABEL"]  = str(intent.get("label", ""))
                vals[f"CALLER_{n}_INTENT_{m}_PROMPT"] = str(intent.get("prompt", ""))
        # The Chat model is combined with the Main LLM, so purge any stale
        # CHAT_LLM_* keys a previous version may have written.
        vals.update(self._preset_values_to_persist(vals))
        startup_error = ""
        try:
            from core.system.autostart import sync_start_on_login

            sync_start_on_login(str(vals.get("START_ON_LOGIN", "")).lower() == "true")
        except Exception as exc:  # noqa: BLE001 - settings still save; startup can be retried.
            startup_error = str(exc) or type(exc).__name__
        _write_env(
            vals,
            remove_keys=set(secret_store.API_KEY_NAMES)
            | {"CHAT_LLM_PROVIDER", "CHAT_LLM_MODEL", "CHAT_LLM_FALLBACKS", "TOOL_FILE_MODE"},
        )
        if startup_error:
            QMessageBox.warning(
                self,
                t("Could not update startup setting"),
                t(
                    "Your preference was saved, but Wisp could not update the operating system startup entry:\n\n{error}"
                ).format(error=startup_error),
            )

        # Honor the user's choices, but warn if their model setup probably can't
        # serve them (screenshot → vision; tools → tool-calling provider).
        warnings, warnings_by_target = self._capability_warnings_for_values(vals)
        self._set_warning_markers(warnings_by_target)
        if warnings:
            self._last_save_warnings = list(warnings)
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Maps provider name → secret store key name (OpenAI-compat providers only)
_PROVIDER_KEY_NAMES: dict[str, str] = {
    "groq":       "GROQ_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral":    "MISTRAL_API_KEY",
    "xai":        "XAI_API_KEY",
    "together":   "TOGETHER_API_KEY",
    "cerebras":   "CEREBRAS_API_KEY",
    "zai":        "ZAI_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    "sambanova":  "SAMBANOVA_API_KEY",
    "github_models": "GITHUB_MODELS_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "chutes":     "CHUTES_API_KEY",
    "vercel":     "VERCEL_API_KEY",
    "fireworks":  "FIREWORKS_API_KEY",
    "cohere":     "COHERE_API_KEY",
    "ai21":       "AI21_API_KEY",
    "nebius":     "NEBIUS_API_KEY",
    "custom":     "CUSTOM_API_KEY",
    # ollama: no key
}

_MODEL_HINTS: dict[str, str] = {
    "groq":       "e.g. llama-3.3-70b-versatile",
    "openai":     "e.g. gpt-5.5",
    "anthropic":  "e.g. claude-sonnet-4-6",
    "google":     "e.g. gemini-3.5-flash",
    "chatgpt":    "gpt-5.5  |  gpt-5.4  |  gpt-5.4-mini  |  gpt-5.4-nano  |  gpt-5.3-codex",
    "copilot":    "e.g. gpt-4.1",
    "deepseek":   "e.g. deepseek-chat",
    "openrouter": "e.g. openai/gpt-5.5",
    "mistral":    "e.g. mistral-large-latest",
    "xai":        "e.g. grok-4.3",
    "together":   "e.g. meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "cerebras":   "e.g. llama-4-scout-17b-16e-instruct",
    "zai":        "e.g. glm-4.5-flash",
    "nvidia":     "e.g. meta/llama-3.3-70b-instruct",
    "sambanova":  "e.g. Meta-Llama-3.1-8B-Instruct",
    "github_models": "e.g. openai/gpt-5.4-mini",
    "huggingface": "e.g. meta-llama/Llama-3.3-70B-Instruct",
    "chutes":     "e.g. deepseek-ai/DeepSeek-V3-0324",
    "vercel":     "e.g. openai/gpt-5.4-mini",
    "fireworks":  "e.g. accounts/fireworks/models/llama-v3p1-8b-instruct",
    "cohere":     "e.g. command-r-plus",
    "ai21":       "e.g. jamba-large",
    "nebius":     "e.g. meta-llama/Meta-Llama-3.3-70B-Instruct",
    "ollama":     "e.g. llama3  (model pulled locally)",
    "custom":     "model name for your custom endpoint",
}

_PROVIDER_MODELS: dict[str, list[str]] = {
    "groq": [
        "groq/compound",
        "groq/compound-mini",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "qwen/qwen3-32b",
        "qwen/qwen3.6-27b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gemma2-9b-it",
    ],
    "openai": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "o3",
        "o4-mini",
    ],
    "anthropic": [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
    ],
    "google": [
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-pro-latest",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
    "chatgpt": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-codex",
    ],
    "copilot": [
        "gpt-5.4-mini",
        "gpt-5.4",
        "gpt-4.1",
        "gpt-4o",
        "claude-sonnet-4-6",
        "gemini-2.5-pro",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-r1",
        "deepseek-v3.1",
    ],
    "openrouter": [
        "openai/gpt-5.5",
        "openai/gpt-5.4-mini",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-3.5-flash",
        "x-ai/grok-4.3",
        "meta-llama/llama-4-maverick",
        "deepseek/deepseek-r1",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-5",
        "google/gemini-2.5-flash",
        "meta-llama/llama-3.3-70b-instruct",
        "deepseek/deepseek-chat",
        "mistralai/mistral-large",
    ],
    "mistral": [
        "mistral-large-latest",
        "magistral-medium-latest",
        "magistral-small-latest",
        "mistral-small-latest",
        "mistral-medium-latest",
        "codestral-latest",
        "devstral-small-latest",
        "ministral-8b-latest",
    ],
    "xai": [
        "grok-4.3",
        "grok-build-0.1",
        "grok-4",
        "grok-4-latest",
        "grok-3",
        "grok-3-mini",
        "grok-2-latest",
    ],
    "together": [
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen3-235B-A22B-fp8-tput",
        "meta-llama/Llama-3-70b-chat-hf",
        "meta-llama/Llama-3-8b-chat-hf",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ],
    "cerebras": [
        "llama-4-scout-17b-16e-instruct",
        "llama-3.3-70b",
        "llama3.1-8b",
        "qwen-3-32b",
    ],
    "zai": [
        "glm-4.5-flash",
        "glm-4.5",
        "glm-4.5-air",
        "glm-4.6",
        "glm-4.7-flash",
    ],
    "nvidia": [
        "meta/llama-4-maverick-17b-128e-instruct",
        "meta/llama-4-scout-17b-16e-instruct",
        "meta/llama-3.3-70b-instruct",
        "nvidia/llama-3.3-nemotron-super-49b-v1",
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "mistralai/mixtral-8x7b-instruct-v0.1",
    ],
    "sambanova": [
        "Meta-Llama-4-Maverick-17B-128E-Instruct",
        "Meta-Llama-3.1-8B-Instruct",
        "Meta-Llama-3.1-70B-Instruct",
        "Meta-Llama-3.3-70B-Instruct",
    ],
    "github_models": [
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4",
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-6",
        "meta/Llama-3.3-70B-Instruct",
    ],
    "huggingface": [
        "meta-llama/Llama-3.3-70B-Instruct",
        "Qwen/Qwen3-32B",
        "deepseek-ai/DeepSeek-R1",
        "meta-llama/Llama-3.1-8B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
        "mistralai/Mistral-7B-Instruct-v0.3",
    ],
    "chutes": [
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3-0324",
        "Qwen/Qwen3-32B",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "meta-llama/Llama-3.3-70B-Instruct",
    ],
    "vercel": [
        "openai/gpt-5.4-mini",
        "openai/gpt-5.4",
        "anthropic/claude-sonnet-4-6",
        "google/gemini-3.5-flash",
        "xai/grok-4.3",
        "openai/gpt-4o-mini",
        "anthropic/claude-3-5-haiku",
        "xai/grok-3-mini",
    ],
    "fireworks": [
        "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "accounts/fireworks/models/deepseek-r1",
        "accounts/fireworks/models/qwen3-235b-a22b",
        "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "accounts/fireworks/models/mixtral-8x7b-instruct",
    ],
    "cohere": [
        "command-a-03-2025",
        "command-r-plus",
        "command-r",
    ],
    "ai21": [
        "jamba-large-1.7",
        "jamba-mini-1.7",
        "jamba-large",
        "jamba-mini",
    ],
    "nebius": [
        "meta-llama/Meta-Llama-3.3-70B-Instruct",
        "Qwen/Qwen3-235B-A22B",
        "deepseek-ai/DeepSeek-R1",
        "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
    ],
    "ollama": [
        "llama3.3",
        "llama3.2",
        "llama3.1",
        "qwen3",
        "deepseek-r1",
        "mistral",
        "codellama",
        "gemma3",
    ],
    "custom": [],
}

_PROVIDER_LABELS: dict[str, str] = {
    "groq":       "Groq",
    "openai":     "OpenAI API",
    "anthropic":  "Anthropic",
    "google":     "Google AI Studio",
    "chatgpt":    "ChatGPT Plus/Pro (OAuth subscription)",
    "copilot":    "GitHub Copilot",
    "deepseek":   "DeepSeek",
    "openrouter": "OpenRouter",
    "mistral":    "Mistral",
    "xai":        "xAI (Grok)",
    "together":   "Together AI",
    "cerebras":   "Cerebras",
    "zai":        "Z.AI / GLM",
    "nvidia":     "NVIDIA",
    "sambanova":  "SambaNova",
    "github_models": "GitHub Models",
    "huggingface": "Hugging Face",
    "chutes":     "Chutes",
    "vercel":     "Vercel AI Gateway",
    "fireworks":  "Fireworks",
    "cohere":     "Cohere",
    "ai21":       "AI21",
    "nebius":     "Nebius",
    "ollama":     "Ollama (local)",
    "custom":     "Custom (OpenAI-compatible)",
    "cartesia":   "Cartesia",
    "elevenlabs": "ElevenLabs",
    "openai_compatible": "OpenAI-compatible (custom)",
    "gpt_sovits": "GPT-SoVITS (local)",
    "kokoro": "Kokoro (local)",
    "none":       "None",
}


def _model_hint(provider: str) -> str:
    """Handle model hint for UI settings panel dialog."""
    return _MODEL_HINTS.get(provider.lower(), "model name")


def _refresh_model_combo(combo: QComboBox, provider: str) -> None:
    """Update a model combo's item list for the given provider without losing the current text."""
    current_text = combo.currentText()
    models = _PROVIDER_MODELS.get(provider, [])
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(models)
    combo.setCurrentText(current_text) if current_text else combo.setCurrentIndex(-1)
    completer = QCompleter(models, combo)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    combo.setCompleter(completer)
    combo.blockSignals(False)


def _sep(visible: bool = False) -> QFrame:
    """Handle sep for UI settings panel dialog."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(
        "color: #e5e5ea; margin: 2px 0;" if visible else "max-height: 0px; color: #000000; margin: 0;"
    )
    return line


def _set(widget, value: str):
    """Set *value* on a combo/line/text-edit widget, honoring custom-value rules."""
    if isinstance(widget, QComboBox):
        if value == "auto" and widget.property("legacy_auto_means_on"):
            value = "on"
        idx = widget.findData(value)
        if idx >= 0:
            widget.setCurrentIndex(idx)
        else:
            idx = widget.findText(value)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            elif widget.property("allow_custom_saved_value") and value:
                widget.addItem(value, value)
                widget.setCurrentIndex(widget.count() - 1)
            elif widget.isEditable():
                widget.setCurrentText(value)
    elif isinstance(widget, QLineEdit):
        widget.setText(value)
    elif isinstance(widget, QTextEdit):
        widget.setPlainText(value)
    elif isinstance(widget, QCheckBox):
        widget.setChecked(str(value).strip().lower() in {"1", "true", "yes", "on"})
    elif isinstance(widget, QSlider):
        try:
            if "." in str(value):
                widget.setValue(int(round(float(value) * 100)))
            else:
                raw = int(str(value).strip())
                widget.setValue(raw if raw > 2 else raw * 100)
        except Exception:
            widget.setValue(100)


def _get(widget) -> str:
    """Return the current text/data value of a combo/line/text-edit widget."""
    if isinstance(widget, QComboBox):
        data = widget.currentData()
        return data if data is not None else widget.currentText()
    elif isinstance(widget, QLineEdit):
        return widget.text()
    elif isinstance(widget, QTextEdit):
        return widget.toPlainText()
    elif isinstance(widget, QCheckBox):
        return str(widget.isChecked())
    elif isinstance(widget, QSlider):
        return f"{widget.value() / 100:.2f}".rstrip("0").rstrip(".")
    return ""


def _ms_to_seconds_str(ms_value, default_ms: int) -> str:
    """Render a millisecond env value as a compact seconds string (e.g. "3.5")."""
    try:
        ms = int(float(str(ms_value).strip()))
    except (TypeError, ValueError):
        ms = default_ms
    return f"{ms / 1000:g}"


def _seconds_str_to_ms(text, fallback_ms: int) -> str:
    """Parse a seconds field back to a millisecond env value (min 0.5s)."""
    try:
        return str(max(500, int(round(float(str(text).strip()) * 1000))))
    except (TypeError, ValueError):
        return str(fallback_ms)


def _desc_label(title: str, description: str) -> QLabel:
    """Handle desc label for UI settings panel dialog."""
    lbl = QLabel(t(description))
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")
    return lbl


def _tooltip_label(text: str, tooltip: str) -> QLabel:
    """Build a translated form/grid label that owns a settings tooltip."""
    lbl = QLabel(t(text))
    lbl.setToolTip(tooltip)
    return lbl


def _link_label(text: str, url: str) -> QLabel:
    """Handle link label for UI settings panel dialog."""
    lbl = QLabel(f'<a href="{url}">{t(text)}</a>')
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lbl.setToolTip(url)
    return lbl


def _parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    """Parse fallback rows."""
    return parse_fallback_rows(raw)


def _short_test_error(message: str, route_name: str) -> str:
    """Trim the redundant '<route> test failed: ' prefix so each route line shows
    just the underlying reason (the provider/model is already on the line)."""
    prefix = f"{route_name} test failed: "
    text = message[len(prefix):] if message.startswith(prefix) else message
    return " ".join(text.split())


def _translate_status_value(value: str) -> str:
    """Translate known status value atoms while preserving provider/model names."""
    text = str(value or "")
    if text in {"None", "unavailable", "authorized", "denied", "not_determined", "restricted"}:
        return t(text)
    return text


def _kokoro_install_progress_text(line: str) -> str:
    """Return a short user-facing install phase for a raw pip output line."""
    return _optional_install_progress_text(line, "Kokoro")


def _optional_install_progress_text(line: str, display_name: str) -> str:
    """Return a short user-facing install phase for raw pip output."""
    lower = str(line or "").lower()
    if "requirement already satisfied" in lower:
        detail = "checking installed packages"
    elif (
        "collecting " in lower
        or "looking in indexes" in lower
        or "resolving" in lower
        or "resolved " in lower
    ):
        detail = "resolving packages"
    elif "downloading" in lower or "progress " in lower or "prepared " in lower:
        detail = "downloading packages"
    elif "installing collected packages" in lower or lower.startswith("installed "):
        detail = "installing packages"
    elif "successfully installed" in lower:
        detail = "finalizing"
    else:
        detail = "working - installer is still running"
    return f"Installing {display_name}: {detail}."


def _optional_install_elapsed_text(display_name: str, elapsed_seconds: int, quiet_seconds: int, log_path: Path) -> str:
    """Return a heartbeat status while an optional dependency install is running."""
    elapsed = _format_duration(elapsed_seconds)
    if quiet_seconds >= 60:
        detail = f"still running for {elapsed}; no installer output for {_format_duration(quiet_seconds)}. Log: {log_path}"
    else:
        detail = f"still running for {elapsed}. Log: {log_path}"
    return f"Installing {display_name}: {detail}."


def _optional_install_log_path(display_name: str, optional_packages_dir: Path) -> Path:
    """Return a writable installer log path for optional dependency installs."""
    root = os.environ.get("WISP_RUN_LOG_DIR")
    if root:
        base = Path(root).expanduser() / "installers"
    else:
        base = optional_packages_dir / "_logs"
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-") or "optional-package"
    return base / f"{slug}-install.log"


def _optional_install_no_output_timeout_seconds() -> int:
    """Return how long an optional installer may be silent before Wisp stops it."""
    raw = os.environ.get("WISP_OPTIONAL_INSTALL_NO_OUTPUT_TIMEOUT_SECONDS", "300")
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 300


def _format_duration(seconds: int) -> str:
    """Format a short elapsed duration."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _optional_install_failure_detail(lines: list[str]) -> str:
    """Return a compact failure detail from installer output."""
    for line in reversed(lines):
        text = _collapse_spaces(line)
        if not text:
            continue
        lower = text.lower()
        if lower.startswith("[notice]"):
            continue
        if len(text) > 500:
            text = text[-500:]
        return f"Last installer message: {text}"
    return "Installer exited with an error before reporting details."


def _collapse_spaces(text: str) -> str:
    """Collapse whitespace from subprocess output."""
    return " ".join(str(text or "").split())


def _translate_status_message(message: str) -> str:
    """Handle translate status message for UI settings panel dialog."""
    text = str(message or "")
    if "\n" in text:
        return "\n".join(_translate_status_message(part) for part in text.splitlines())
    dynamic_patterns: tuple[tuple[str, str], ...] = (
        (r"^LLM route configured: (?P<route>.+)\.$", "LLM route configured: {route}."),
        (r"^LLM route incomplete: (?P<route>.+)\.$", "LLM route incomplete: {route}."),
        (r"^TTS provider configured: (?P<provider>.+)\.$", "TTS provider configured: {provider}."),
        (r"^STT model configured: (?P<model>.+)\.$", "STT model configured: {model}."),
        (r"^(?P<count>\d+) hotkeys configured\.$", "{count} hotkeys configured."),
        (r"^Accessibility permission: (?P<value>.+)\.$", "Accessibility permission: {value}."),
        (r"^Screen recording permission: (?P<value>.+)\.$", "Screen recording permission: {value}."),
        (r"^Microphone permission: (?P<value>.+)\.$", "Microphone permission: {value}."),
        (r"^LLM test failed: (?P<message>.+)$", "LLM test failed: {message}"),
        (r"^Kokoro install failed: (?P<message>.+)$", "Kokoro install failed: {message}"),
        (r"^Kokoro is installed with GPU support \((?P<device>.+)\)\.$", "Kokoro is installed with GPU support ({device})."),
        (r"^Installing Kokoro: (?P<detail>.+)\.$", "Installing Kokoro: {detail}."),
        (r"^LLM route uses (?P<provider>.+) but you are not logged in\.$", "LLM route uses {provider} but you are not logged in."),
    )
    for pattern, template in dynamic_patterns:
        match = re.match(pattern, text)
        if match:
            groups = match.groupdict()
            if "message" in groups:
                groups["message"] = _translate_status_message(groups["message"])
            if "value" in groups:
                groups["value"] = _translate_status_value(groups["value"])
            if "detail" in groups:
                groups["detail"] = t(groups["detail"])
            return t(template).format(**groups)
    for prefix in (
        "Error reading status: ",
        "Keychain error: ",
        "Test failed: ",
        "Could not load tools: ",
        "Error: ",
        "Logged in • account ",
        "Logged in - account ",
        "Logged in as ",
        "Scopes: ",
    ):
        if text.startswith(prefix):
            translated_prefix = t("Logged in • account ") if prefix == "Logged in - account " else t(prefix)
            return translated_prefix + text[len(prefix):]
    return t(text)


def _dialog_is_usable(dialog: "SettingsDialog | None") -> bool:
    """Handle dialog is usable for UI settings panel dialog."""
    if dialog is None or getattr(dialog, "_disposing", False):
        return False
    try:
        import shiboken6

        return bool(shiboken6.isValid(dialog))
    except Exception:
        try:
            dialog.objectName()
            return True
        except RuntimeError:
            return False


def _clear_settings_dialog(_obj=None) -> None:
    """Clear settings dialog."""
    global _settings_dialog
    if _obj is None or _settings_dialog is None or _obj is _settings_dialog:
        _settings_dialog = None


def _open_settings_now(parent=None, on_apply=None, on_setup_check=None, extra_tools=None):
    """Open settings now."""
    global _settings_dialog, _settings_open_pending
    _settings_open_pending = False
    # Never parent the settings window to the floating icon overlay: that overlay
    # is a Qt.Tool window (an NSPanel on macOS, a no-taskbar tool window on
    # Windows), and attaching a normal child window to it crashes Cocoa on show()
    # and misbehaves on Windows. Only Linux keeps the parent. Elsewhere the dialog
    # is top-level and grabs focus itself via raise_()/activateWindow() below.
    dialog_parent = parent if sys.platform.startswith("linux") else None
    if not _dialog_is_usable(_settings_dialog):
        _settings_dialog = None
    elif not _settings_dialog.isVisible():
        _settings_dialog._disposing = True
        _settings_dialog.deleteLater()
        _settings_dialog = None

    if _settings_dialog is None:
        _settings_dialog = SettingsDialog(
            dialog_parent,
            on_apply=on_apply,
            on_setup_check=on_setup_check,
            extra_tools=extra_tools,
        )
        _settings_dialog.destroyed.connect(_clear_settings_dialog)
    else:
        _settings_dialog._on_apply = on_apply
        _settings_dialog._on_setup_check = on_setup_check
        _settings_dialog._extra_tools = _normalize_extra_tool_payloads(extra_tools)

    if _settings_dialog.isMinimized():
        _settings_dialog.showNormal()
    _settings_dialog.show()
    _settings_dialog.raise_()
    _settings_dialog.activateWindow()


def open_settings(parent=None, on_apply=None, on_setup_check=None, extra_tools=None):
    """Open settings."""
    global _settings_open_pending
    if _settings_open_pending:
        return
    _settings_open_pending = True
    QTimer.singleShot(
        50,
        lambda: _open_settings_now(
            parent=parent,
            on_apply=on_apply,
            on_setup_check=on_setup_check,
            extra_tools=extra_tools,
        ),
    )
