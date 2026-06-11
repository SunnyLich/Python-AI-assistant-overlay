"""
ui/settings.py -” Settings dialog.

A plain GUI for editing all user-configurable values.
Reads from and writes to the .env file.
Launch via tray icon â†’ Settings, or call open_settings().
"""
from __future__ import annotations
import os
import sys
import logging
import threading
from contextlib import contextmanager
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox,
    QPushButton, QTabWidget, QWidget, QFrame, QGroupBox, QMessageBox,
    QScrollArea, QSizePolicy, QCompleter,
)
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QFont
from core import secret_store
from core.system.env_utils import normalize_screenshot_mode
import ui.settings_panel.env as settings_env
from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit
from ui.settings_panel.helpers import parse_fallback_rows
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

ENV_PATH = settings_env.ENV_PATH
_settings_log = logging.getLogger("wisp.settings")
_settings_dialog: "SettingsDialog | None" = None


class _NoScrollCombo(QComboBox):
    """QComboBox that keeps passive wheel scrolling on the settings page.

    A focused but closed combo should not hijack mouse-wheel scrolling from the
    surrounding QScrollArea. Wheel selection is still available while the popup
    list is open.
    """
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()


def _context_mode_combo(value: str, *, allow_auto: bool = True) -> _NoScrollCombo:
    combo = _NoScrollCombo()
    combo.addItem("Off", "off")
    if allow_auto:
        combo.addItem("On", "auto")
    combo.addItem("Let model decide", "model")
    idx = combo.findData((value or "off").strip().lower())
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    return combo


# Sentinel data value for the "Custom / enter manually…" model combo entry.
_CUSTOM_MODEL_SENTINEL = "__custom__"
_CUSTOM_MODEL_LABEL = "Custom / enter manually…"


class _ModelFetchSignals(QObject):
    """Marshals a background model-list fetch result back to the Qt main thread.

    done(models: list, error: str) — error is "" on success.
    """
    done = Signal(object, str)


class _MemoryLoadSignals(QObject):
    """Marshals the lazy memory-store load back to the Qt main thread."""

    done = Signal(int, object, object, str)


class _SettingsMemoryManagerProxy:
    """Read-only bridge for the Settings memory tab."""

    def get_all_facts(self) -> list[dict]:
        from core.memory_store.store import get_all_facts_lightweight

        return get_all_facts_lightweight()


def _read_env() -> dict[str, str]:
    old_path = settings_env.ENV_PATH
    settings_env.ENV_PATH = ENV_PATH
    try:
        return settings_env.read_settings_env()
    finally:
        settings_env.ENV_PATH = old_path


def _format_env_value(value: str) -> str:
    return settings_env.format_settings_env_value(value)


def _write_env(vals: dict[str, str], remove_keys: set[str] | None = None):
    old_path = settings_env.ENV_PATH
    settings_env.ENV_PATH = ENV_PATH
    try:
        settings_env.write_settings_env(vals, remove_keys=remove_keys)
    finally:
        settings_env.ENV_PATH = old_path


class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_apply=None):
        super().__init__(parent)
        self._on_apply = on_apply  # callable() fired after a successful apply
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setModal(False)
        enable_standard_window_controls(self)
        self._env = _read_env()
        self._fields: dict[str, QLineEdit | QComboBox | QCheckBox | QTextEdit] = {}
        self._api_key_rows: list[dict] = []
        self._model_section_rows: dict[str, list[dict]] = {
            "LLM": [], "VISION_LLM": [], "MEMORY_LLM": []
        }
        self._model_section_layouts: dict[str, "QVBoxLayout"] = {}
        self._fallback_rows: dict = {}
        self._pending_test_results: list[tuple[str, int, bool, str]] = []
        self._pending_test_results_lock = threading.Lock()
        self._pending_status_results: list[tuple[int, str, object, str]] = []
        self._pending_status_results_lock = threading.Lock()
        self._running_test_tokens: set[tuple[str, int]] = set()
        self._latest_test_token: dict[str, int] = {}
        self._status_refresh_token = 0
        self._status_refresh_running = False
        self._tabs = None
        self._memory_panel = None
        self._memory_tab_index = -1
        self._memory_browser_cv = None
        self._memory_loading = False
        self._memory_load_token = 0
        self._memory_load_timeout_ms = 12000
        self._memory_load_start_delay_ms = 250
        self._memory_load_signals = _MemoryLoadSignals(self)
        self._memory_load_signals.done.connect(self._on_memory_panel_loaded)
        self._test_result_timer = QTimer(self)
        self._test_result_timer.setInterval(100)
        self._test_result_timer.timeout.connect(self._drain_test_results)
        self._status_result_timer = QTimer(self)
        self._status_result_timer.setInterval(100)
        self._status_result_timer.timeout.connect(self._drain_status_results)
        self._build_ui()
        self._load_values()
        self._schedule_open_status_refresh()
        fit_window_to_screen(self, preferred_width=620, preferred_height=620)

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
            key_name = _PROVIDER_KEY_NAMES.get(provider)
            if not key_name:
                continue
            value = row["key"].text().strip()
            if not value:
                continue
            label = _PROVIDER_LABELS.get(provider, provider)
            if _store(key_name, value, label):
                row["key"].clear()
                row["key"].setPlaceholderText("stored in keychain")

        # TTS and custom keys still live in self._fields
        for name, label in [
            ("CARTESIA_API_KEY",   "Cartesia"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("CUSTOM_API_KEY",     "Custom provider"),
        ]:
            if name not in self._fields:
                continue
            value = _get(self._fields[name]).strip()
            if not value:
                continue
            if _store(name, value, label):
                self._fields[name].clear()  # type: ignore[attr-defined]
                self._fields[name].setPlaceholderText(f"{label} key stored in OS keychain")  # type: ignore[attr-defined]

        if failures:
            QMessageBox.warning(
                self,
                "Some API keys were not saved",
                "These keys could not be written to the OS keychain and were "
                "NOT stored:\n\n"
                + "\n".join(f"• {item}" for item in failures)
                + "\n\nYour other settings were still saved. See the log for "
                "details, then try saving the affected keys again.",
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
        super().showEvent(event)
        fit_window_to_screen(self, preferred_width=620, preferred_height=620)
        if self._tabs is not None:
            self._maybe_load_memory_tab(self._tabs.currentIndex())

    def reject(self) -> None:
        self._cancel_memory_panel_load()
        super().reject()

    def closeEvent(self, event):                # noqa: N802
        self._cancel_memory_panel_load()
        super().closeEvent(event)

    _LIGHT_STYLE = """
        QDialog { background: #f2f2f7; }
        QTabWidget::pane { border: none; background: transparent; }
        QTabBar { background: transparent; border: none; }
        QTabBar::tab {
            color: #636366; padding: 7px 20px; border-radius: 8px;
            font-size: 9pt; margin: 2px 2px; background: transparent;
        }
        QTabBar::tab:selected { background: white; color: #5856d6; font-weight: 600; }
        QTabBar::tab:hover:!selected { background: rgba(88,86,214,0.07); }
        QFrame#card {
            background: white; border: 1px solid #e5e5ea; border-radius: 12px;
        }
        QLabel#sectionHeader {
            color: #8e8e93; font-size: 8pt; font-weight: 700;
            letter-spacing: 0.5px; padding: 0px;
        }
        QScrollArea { background: transparent; border: none; }
        QScrollArea > QWidget > QWidget { background: transparent; }
        QLineEdit {
            background: white; border: 1px solid #d1d1d6; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: #1c1c1e; min-height: 30px;
        }
        QLineEdit:focus { border-color: #5856d6; }
        QComboBox {
            background: white; border: 1px solid #d1d1d6; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: #1c1c1e; min-height: 30px;
        }
        QComboBox:focus { border-color: #5856d6; }
        QComboBox::drop-down { border: none; width: 20px; }
        QPushButton {
            border: 1.5px solid #5856d6; color: #5856d6; border-radius: 8px;
            padding: 5px 16px; background: transparent; font-size: 10pt;
        }
        QPushButton:hover { background: rgba(88,86,214,0.06); }
        QPushButton:pressed { background: rgba(88,86,214,0.12); }
        QPushButton:flat { border: none; color: #5856d6; background: transparent; }
        QPushButton:flat:hover { color: #3634a3; background: transparent; }
        QCheckBox { color: #1c1c1e; }
        QLabel { color: #1c1c1e; }
    """

    _DARK_STYLE = """
        QDialog { background: #1c1e26; }
        QTabWidget::pane { border: none; background: transparent; }
        QTabBar { background: transparent; border: none; }
        QTabBar::tab {
            color: #9d9daa; padding: 7px 20px; border-radius: 8px;
            font-size: 9pt; margin: 2px 2px; background: transparent;
        }
        QTabBar::tab:selected { background: #2b2d3a; color: #8b87ff; font-weight: 600; }
        QTabBar::tab:hover:!selected { background: rgba(139,135,255,0.08); }
        QFrame#card {
            background: #25273a; border: 1px solid #35374d; border-radius: 12px;
        }
        QLabel#sectionHeader {
            color: #6b6b7e; font-size: 8pt; font-weight: 700;
            letter-spacing: 0.5px; padding: 0px;
        }
        QScrollArea { background: transparent; border: none; }
        QScrollArea > QWidget > QWidget { background: transparent; }
        QWidget { background-color: transparent; color: #e8e8f0; }
        QLineEdit {
            background: #17181d; border: 1px solid #454854; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: #e8e8f0; min-height: 30px;
        }
        QLineEdit:focus { border-color: #8b87ff; }
        QComboBox {
            background: #17181d; border: 1px solid #454854; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: #e8e8f0; min-height: 30px;
        }
        QComboBox:focus { border-color: #8b87ff; }
        QComboBox::drop-down { border: none; width: 20px; }
        QComboBox QAbstractItemView {
            background: #25273a; color: #e8e8f0; border: 1px solid #454854;
        }
        QPushButton {
            border: 1.5px solid #8b87ff; color: #8b87ff; border-radius: 8px;
            padding: 5px 16px; background: transparent; font-size: 10pt;
        }
        QPushButton:hover { background: rgba(139,135,255,0.10); }
        QPushButton:pressed { background: rgba(139,135,255,0.20); }
        QPushButton:flat { border: none; color: #8b87ff; background: transparent; }
        QPushButton:flat:hover { color: #b0acff; background: transparent; }
        QCheckBox { color: #e8e8f0; }
        QLabel { color: #e8e8f0; }
        QTextEdit, QPlainTextEdit {
            background: #17181d; border: 1px solid #454854; border-radius: 8px;
            color: #e8e8f0;
        }
        QScrollBar:vertical, QScrollBar:horizontal {
            background: #1c1e26; border: none;
        }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #454854; border-radius: 4px;
            min-height: 24px; min-width: 24px;
        }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
    """

    def _apply_dialog_theme(self):
        from ui.shared.theme import is_dark_mode
        self.setStyleSheet(self._DARK_STYLE if is_dark_mode() else self._LIGHT_STYLE)

    def _build_ui(self):
        self._apply_dialog_theme()
        root = QVBoxLayout(self)
        root.setSpacing(12)

        tabs = QTabWidget()
        # Never elide tab labels to "…"; macOS sizes the bold selected tab
        # tighter than Windows, so let each tab grow to fit its full text.
        tabs.setElideMode(Qt.TextElideMode.ElideNone)
        tabs.addTab(self._tab_llm(),       "LLM")
        tabs.addTab(self._tab_tts(),       "TTS / Voice")
        tabs.addTab(self._tab_prompt(),    "Prompts")
        tabs.addTab(self._tab_keybinds(),  "Keybinds")
        tabs.addTab(self._tab_app(),       "App")
        self._memory_tab_index = tabs.addTab(self._tab_memory(), "Memory")
        tabs.addTab(self._tab_tools(),     "Tools")
        tabs.currentChanged.connect(self._maybe_load_memory_tab)
        self._tabs = tabs
        root.addWidget(tabs)

        # Buttons
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("Reset All…")
        reset_btn.setToolTip(
            "Delete all API keys from the OS keychain and reset every setting to defaults"
        )
        reset_btn.setStyleSheet(
            "QPushButton { border: 1.5px solid #c0392b; color: #c0392b; }"
            "QPushButton:hover { background: rgba(192,57,43,0.10); }"
            "QPushButton:pressed { background: rgba(192,57,43,0.20); }"
        )
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch()
        apply_btn  = QPushButton("Apply")
        cancel_btn = QPushButton("Cancel")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        root.addLayout(btn_row)

    def _tab_llm(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── AUTHENTICATION card ───────────────────────────────────────────
        auth_card, auth_cv = self._card("Authentication")

        chatgpt_hdr = QLabel("ChatGPT Pro / Plus")
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
        auth_cv.addWidget(_sep(visible=True))

        copilot_hdr = QLabel("GitHub Copilot")
        copilot_hdr.setStyleSheet("font-weight: 600;")
        auth_cv.addWidget(copilot_hdr)
        auth_cv.addWidget(_desc_label("", "Fine-grained PAT with Copilot Requests: Read-only. Stored in OS keychain."))
        self._copilot_token_edit = self._password()
        self._copilot_token_edit.setPlaceholderText("github_pat_… (not saved to .env)")
        copilot_f_w = QWidget()
        copilot_f = QFormLayout(copilot_f_w)
        copilot_f.setContentsMargins(0, 0, 0, 0)
        copilot_f.setSpacing(8)
        copilot_f.addRow("Token", self._copilot_token_edit)
        auth_cv.addWidget(copilot_f_w)
        self._copilot_status_lbl = QLabel()
        self._copilot_status_lbl.setWordWrap(True)
        self._set_status_label(self._copilot_status_lbl, None, "Checking status...")
        auth_cv.addWidget(self._copilot_status_lbl)
        copilot_row = self._button_row(
            ("Save token",       self._copilot_save_token),
            ("Test token / SDK", self._copilot_test_token),
            ("Clear token",      self._copilot_clear_token),
        )
        cp_btns = copilot_row.findChildren(QPushButton)
        self._copilot_save_btn, self._copilot_test_btn, self._copilot_clear_btn = (
            cp_btns[0], cp_btns[1], cp_btns[2]
        )
        auth_cv.addWidget(copilot_row)
        outer.addWidget(auth_card)

        # ── API KEYS card ─────────────────────────────────────────────────
        api_keys_card, api_keys_cv = self._card("API Keys")
        note = QLabel(
            "<small>Add a row for each provider you want to use. "
            "Alias is optional — useful when you have multiple keys for the same provider.</small>"
        )
        note.setWordWrap(True)
        api_keys_cv.addWidget(note)

        col_hdr_w = QWidget()
        col_hdr_h = QHBoxLayout(col_hdr_w)
        col_hdr_h.setContentsMargins(0, 0, 0, 0)
        col_hdr_h.setSpacing(8)
        for txt, stretch in [("Provider", 2), ("Alias", 2), ("API Key", 3)]:
            lbl = QLabel(f"<small><b>{txt}</b></small>")
            col_hdr_h.addWidget(lbl, stretch)
        col_hdr_h.addSpacing(32)
        api_keys_cv.addWidget(col_hdr_w)

        self._api_key_rows_container = QWidget()
        self._api_key_rows_layout = QVBoxLayout(self._api_key_rows_container)
        self._api_key_rows_layout.setSpacing(4)
        self._api_key_rows_layout.setContentsMargins(0, 0, 0, 0)
        api_keys_cv.addWidget(self._api_key_rows_container)

        add_key_btn = QPushButton("+ Add API Key")
        akw = QHBoxLayout()
        akw.setContentsMargins(0, 0, 0, 0)
        akw.addWidget(add_key_btn)
        akw.addStretch()
        api_keys_cv.addLayout(akw)
        add_key_btn.clicked.connect(lambda: self._add_api_key_row())
        outer.addWidget(api_keys_card)

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
            title_lbl = QLabel(section_title.upper())
            title_lbl.setObjectName("sectionHeader")
            apply_btn = QPushButton("Apply to all")
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
            lk = QLabel("<small><b>API Key</b></small>")
            lm = QLabel("<small><b>Model</b></small>")
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
            test_btn = QPushButton(f"Test {section_title}")
            test_btn.clicked.connect(test_fn)
            tr_h.addWidget(test_btn)
            tr_h.addWidget(test_lbl, 1)
            cv.addWidget(test_row_w)

            # add row button
            add_row_btn = QPushButton("+ Add row")
            arw = QHBoxLayout()
            arw.setContentsMargins(0, 0, 0, 0)
            arw.addWidget(add_row_btn)
            arw.addStretch()
            cv.addLayout(arw)
            add_row_btn.clicked.connect(
                lambda checked, sk=section_key: self._add_model_section_row(sk)
            )
            outer.addWidget(card)

        # ── CUSTOM PROVIDER card ──────────────────────────────────────────
        self._fields["CUSTOM_BASE_URL"] = QLineEdit()
        self._fields["CUSTOM_BASE_URL"].setPlaceholderText("https://api.example.com/v1")
        self._fields["CUSTOM_API_KEY"] = self._password()
        self._fields["CUSTOM_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._custom_test_status_lbl = QLabel()
        self._custom_test_status_lbl.setWordWrap(True)

        custom_card, custom_cv = self._card("Custom provider")
        custom_note = QLabel(
            "<small>Any OpenAI-compatible endpoint — Ollama, LM Studio, and more. "
            "Add a <b>custom</b> row in the API Keys table above, then select it in a model section.</small>"
        )
        custom_note.setWordWrap(True)
        custom_cv.addWidget(custom_note)

        presets_btn = QPushButton("Presets ▾")
        presets_btn.clicked.connect(self._show_custom_presets_menu)
        base_url_row = QWidget()
        bur_h = QHBoxLayout(base_url_row)
        bur_h.setContentsMargins(0, 0, 0, 0)
        bur_h.setSpacing(6)
        bur_h.addWidget(self._fields["CUSTOM_BASE_URL"])
        bur_h.addWidget(presets_btn)

        custom_f_w = QWidget()
        custom_f = QFormLayout(custom_f_w)
        custom_f.setContentsMargins(0, 0, 0, 0)
        custom_f.setSpacing(8)
        custom_f.addRow("Base URL", base_url_row)
        custom_f.addRow("API key", self._fields["CUSTOM_API_KEY"])
        custom_cv.addWidget(custom_f_w)

        test_custom_row = QWidget()
        tcrh = QHBoxLayout(test_custom_row)
        tcrh.setContentsMargins(0, 0, 0, 0)
        tcrh.setSpacing(10)
        tcrh.addWidget(self._button_row(("Test custom", self._test_custom_connection)))
        tcrh.addWidget(self._custom_test_status_lbl, 1)
        custom_cv.addWidget(test_custom_row)
        outer.addWidget(custom_card)

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
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        provider_combo = self._combo(
            ["groq", "openai", "anthropic", "google", "deepseek",
             "openrouter", "mistral", "xai", "together", "cerebras",
             "ollama", "custom"],
            provider,
        )
        provider_combo.setMinimumWidth(120)

        alias_edit = QLineEdit(alias)
        alias_edit.setPlaceholderText("alias (optional)")
        alias_edit.setMinimumWidth(80)

        key_edit = self._password()
        key_edit.setPlaceholderText("stored in keychain" if stored else "enter API key")

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
        remove_btn.clicked.connect(lambda: self._remove_api_key_row(row_info))
        provider_combo.currentIndexChanged.connect(lambda _: self._refresh_model_api_key_combos())
        alias_edit.textChanged.connect(lambda _: self._refresh_model_api_key_combos())

        self._api_key_rows_layout.addWidget(row_w)
        self._api_key_rows.append(row_info)
        self._refresh_model_api_key_combos()
        return row_info

    def _remove_api_key_row(self, row_info: dict) -> None:
        if row_info in self._api_key_rows:
            self._api_key_rows.remove(row_info)
        row_info["widget"].deleteLater()
        self._refresh_model_api_key_combos()

    def _get_api_key_display_options(self) -> "list[tuple[str, str]]":
        options: list[tuple[str, str]] = []
        for row in self._api_key_rows:
            provider = _get(row["provider"])
            alias = row["alias"].text().strip()
            label = _PROVIDER_LABELS.get(provider, provider)
            display = f"{label} ({alias})" if alias else label
            options.append((display, provider))
        # OAuth/keychain providers — always available regardless of API key rows
        options.append((_PROVIDER_LABELS.get("chatgpt", "Codex (ChatGPT)") + " [OAuth]", "chatgpt"))
        options.append((_PROVIDER_LABELS.get("copilot", "GitHub Copilot") + " [OAuth]", "copilot"))
        return options

    def _refresh_model_api_key_combos(self) -> None:
        options = self._get_api_key_display_options()
        for section_rows in self._model_section_rows.values():
            for row in section_rows:
                combo = row["api_key_combo"]
                current = combo.currentData()
                combo.blockSignals(True)
                combo.clear()
                for display, provider in options:
                    combo.addItem(display, provider)
                if current:
                    idx = combo.findData(current)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                combo.blockSignals(False)

    # ---- Model section row helpers ----

    def _add_model_section_row(
        self,
        section_key: str,
        provider: str = "",
        model: str = "",
    ) -> dict:
        options = self._get_api_key_display_options()

        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        api_key_combo = _NoScrollCombo()
        api_key_combo.setMinimumWidth(140)
        for display, prov in options:
            api_key_combo.addItem(display, prov)
        if provider:
            idx = api_key_combo.findData(provider)
            if idx >= 0:
                api_key_combo.setCurrentIndex(idx)

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

        h.addWidget(api_key_combo, 2)
        h.addWidget(model_container, 3)
        h.addWidget(refresh_btn)
        h.addWidget(remove_btn)

        row_info: dict = {
            "widget":        row_w,
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
            p = api_key_combo.currentData() or ""
            self._fill_model_combo(
                row_info, _PROVIDER_MODELS.get(p, []), p, self._model_value(row_info)
            )

        api_key_combo.currentIndexChanged.connect(lambda _: _on_key_change())
        refresh_btn.clicked.connect(lambda: self._refresh_models_for_row(row_info))
        remove_btn.clicked.connect(
            lambda: self._remove_model_section_row(section_key, row_info)
        )

        self._model_section_layouts[section_key].addWidget(row_w)
        self._model_section_rows[section_key].append(row_info)
        return row_info

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
            try:
                from core.llm_clients import client as llm
                models = llm.list_models(provider, api_key=api_key, base_url=base_url)
                carrier.done.emit(models, "")
            except Exception as exc:  # noqa: BLE001 — surfaced to the user as a tooltip
                carrier.done.emit([], str(exc))

        threading.Thread(target=_worker, daemon=True, name="model-list-fetch").start()

    def _on_models_fetched(self, row_info: dict, models, err: str) -> None:
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
        rows = self._model_section_rows[section_key]
        if row_info in rows:
            rows.remove(row_info)
        row_info["widget"].deleteLater()

    def _apply_model_section_to_all(self, source_key: str) -> None:
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

    _CUSTOM_PRESETS: list[tuple[str, str, str]] = [
        ("DeepSeek",     "https://api.deepseek.com/v1",          "deepseek-chat"),
        ("OpenRouter",   "https://openrouter.ai/api/v1",         "openai/gpt-4o"),
        ("Mistral",      "https://api.mistral.ai/v1",            "mistral-large-latest"),
        ("xAI (Grok)",   "https://api.x.ai/v1",                  "grok-3"),
        ("Together AI",  "https://api.together.xyz/v1",          "meta-llama/Llama-3-70b-chat-hf"),
        ("Cerebras",     "https://api.cerebras.ai/v1",           "llama-3.3-70b"),
        ("Ollama (local)", "http://localhost:11434/v1",           "llama3"),
        ("LM Studio (local)", "http://localhost:1234/v1",        "local-model"),
    ]

    def _show_custom_presets_menu(self) -> None:
        from PySide6.QtWidgets import QMenu
        from PySide6.QtGui import QAction

        menu = QMenu(self)
        for name, url, model_hint in self._CUSTOM_PRESETS:
            action = QAction(name, self)
            action.setToolTip(url)
            action.triggered.connect(
                lambda checked, u=url, h=model_hint: self._apply_custom_preset(u, h)
            )
            menu.addAction(action)
        btn = self.sender()
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _apply_custom_preset(self, base_url: str, model_hint: str) -> None:
        self._fields["CUSTOM_BASE_URL"].setText(base_url)
        for section_rows in self._model_section_rows.values():
            for row in section_rows:
                if (row["api_key_combo"].currentData() or "") == "custom":
                    row["model_edit"].setPlaceholderText(f"e.g. {model_hint}")

    def _test_custom_connection(self) -> None:
        from core.llm_clients import client as llm

        provider = "custom"
        rows = self._model_section_rows.get("LLM", [])
        model = self._model_value(rows[0]) if rows else ""
        custom_api_key = (
            _get(self._fields.get("CUSTOM_API_KEY", QLineEdit())).strip()
            or secret_store.get_keychain_secret("CUSTOM_API_KEY")
            or ""
        )
        custom_base_url = _get(self._fields["CUSTOM_BASE_URL"]).strip()

        if not model:
            self._set_test_status(self._custom_test_status_lbl, False, "Enter a model name in the Main LLM row first.")
            return
        if not custom_base_url:
            self._set_test_status(self._custom_test_status_lbl, False, "Enter a base URL first.")
            return

        self._start_async_test(
            "custom_test",
            self._custom_test_status_lbl,
            lambda: llm.test_route_connection(
                provider,
                model,
                "Custom",
                custom_base_url=custom_base_url,
                compat_keys={"custom": custom_api_key},
            ),
        )

    def _refresh_chatgpt_status(self) -> None:
        try:
            from core.auth import chatgpt as chatgpt_auth
            tokens = chatgpt_auth.get_tokens()
            if tokens:
                aid = tokens.get("account_id") or ""
                label = "Logged in" + (f" \u2022 account {aid[:8]}\u2026" if aid else "")
                self._chatgpt_status_lbl.setText(label)
                self._chatgpt_status_lbl.setStyleSheet("color: #80c080;")
            else:
                self._chatgpt_status_lbl.setText("Not logged in")
                self._chatgpt_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        except Exception as exc:
            self._chatgpt_status_lbl.setText(f"Error reading status: {exc}")
            self._chatgpt_status_lbl.setStyleSheet("color: #c04040;")

    def _chatgpt_login_browser(self) -> None:
        from core.auth import chatgpt as chatgpt_auth
        self._chatgpt_status_lbl.setText("Opening browser\u2026 waiting for callback")
        self._chatgpt_status_lbl.setStyleSheet("color: #c0c040;")
        self._start_auth_poll()

        def on_success(_tokens):
            pass  # polling timer will detect the saved tokens

        def on_error(msg):
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
        if self._auth_poll_error is not None:
            msg = self._auth_poll_error
            self._auth_poll_error = None  # clear so we don't re-trigger
            self._auth_poll_timer.stop()
            self._chatgpt_status_lbl.setText(f"Error: {msg}")
            self._chatgpt_status_lbl.setStyleSheet("color: #c04040;")
            return
        # Check if tokens have appeared in the keychain
        try:
            from core.auth import chatgpt as chatgpt_auth
            if chatgpt_auth.get_tokens():
                self._auth_poll_timer.stop()
                self._refresh_chatgpt_status()
                return
        except Exception:
            pass
        # Timeout after 5 minutes
        self._auth_poll_ticks += 1
        if self._auth_poll_ticks >= 300:
            self._auth_poll_timer.stop()
            self._chatgpt_status_lbl.setText("Timed out waiting for login")
            self._chatgpt_status_lbl.setStyleSheet("color: #c04040;")

    def _chatgpt_logout(self) -> None:
        try:
            from core.auth import chatgpt as chatgpt_auth
            chatgpt_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_chatgpt_status()

    def _refresh_github_status(self) -> None:
        try:
            from core.auth import github as github_auth
            tokens = github_auth.get_tokens()
            if tokens:
                login = (tokens.get("user") or {}).get("login") or ""
                scopes = tokens.get("scope") or ""
                label = "Logged in" + (f" as {login}" if login else "")
                if scopes:
                    label += f"\nScopes: {scopes}"
                self._github_status_lbl.setText(label)
                self._github_status_lbl.setStyleSheet("color: #80c080;")
            else:
                self._github_status_lbl.setText("Not logged in")
                self._github_status_lbl.setStyleSheet("color: palette(placeholder-text);")
        except Exception as exc:
            self._github_status_lbl.setText(f"Error reading status: {exc}")
            self._github_status_lbl.setStyleSheet("color: #c04040;")

    def _github_login_device(self) -> None:
        import webbrowser
        import config as cfg
        from core.auth import github as github_auth

        override_client_id = _get(self._fields["GITHUB_CLIENT_ID"]).strip()
        cfg.GITHUB_CLIENT_ID = override_client_id or getattr(cfg, "GITHUB_DEFAULT_CLIENT_ID", "")
        cfg.GITHUB_OAUTH_SCOPES = _get(self._fields["GITHUB_OAUTH_SCOPES"]).strip()
        if not github_auth.has_configured_client_id():
            self._github_status_lbl.setText(
                "This build does not include a GitHub OAuth app client ID yet."
            )
            self._github_status_lbl.setStyleSheet("color: #c04040;")
            return

        self._github_status_lbl.setText("Starting GitHub device auth...")
        self._github_status_lbl.setStyleSheet("color: #c0c040;")
        self._start_github_auth_poll()

        def on_code(url, user_code):
            self._github_auth_poll_message = f"__device_code__{url}\n{user_code}"
            try:
                webbrowser.open(url)
            except Exception:
                pass

        def on_success(_tokens):
            pass

        def on_error(msg):
            self._github_auth_poll_message = msg

        github_auth.start_device_login(on_code, on_success, on_error)

    def _start_github_auth_poll(self) -> None:
        self._github_auth_poll_message: str | None = None
        self._github_auth_poll_ticks = 0
        self._github_auth_poll_timer = QTimer(self)
        self._github_auth_poll_timer.setInterval(1000)
        self._github_auth_poll_timer.timeout.connect(self._github_auth_poll_tick)
        self._github_auth_poll_timer.start()

    def _github_auth_poll_tick(self) -> None:
        if self._github_auth_poll_message is not None:
            msg = self._github_auth_poll_message
            self._github_auth_poll_message = None
            if msg.startswith("__device_code__"):
                body = msg[len("__device_code__"):]
                url, _, code = body.partition("\n")
                self._github_status_lbl.setText(f"Go to: {url}\nEnter code: {code}")
                self._github_status_lbl.setStyleSheet("color: #80a0ff;")
                return
            self._github_auth_poll_timer.stop()
            self._github_status_lbl.setText(f"Error: {msg}")
            self._github_status_lbl.setStyleSheet("color: #c04040;")
            return
        try:
            from core.auth import github as github_auth
            if github_auth.get_tokens():
                self._github_auth_poll_timer.stop()
                self._refresh_github_status()
                return
        except Exception:
            pass
        self._github_auth_poll_ticks += 1
        if self._github_auth_poll_ticks >= 900:
            self._github_auth_poll_timer.stop()
            self._github_status_lbl.setText("Timed out waiting for GitHub login")
            self._github_status_lbl.setStyleSheet("color: #c04040;")

    def _github_logout(self) -> None:
        try:
            from core.auth import github as github_auth
            github_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_github_status()

    def _refresh_copilot_status(self) -> None:
        try:
            from core.auth import copilot_auth
            stored, message = copilot_auth.token_status()
            self._copilot_status_lbl.setText(message)
            self._copilot_status_lbl.setStyleSheet(
                "color: #80c080;" if stored else "color: palette(placeholder-text);"
            )
        except Exception as exc:
            self._copilot_status_lbl.setText(f"Keychain error: {exc}")
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")

    def _copilot_save_token(self) -> None:
        try:
            from core.auth import copilot_auth
            copilot_auth.save_token(self._copilot_token_edit.text())
            self._copilot_token_edit.clear()
            self._refresh_copilot_status()
        except Exception as exc:
            self._copilot_status_lbl.setText(str(exc))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")
            QMessageBox.warning(self, "GitHub Copilot token", str(exc))

    def _copilot_clear_token(self) -> None:
        try:
            from core.auth import copilot_auth
            copilot_auth.clear_token()
            self._copilot_token_edit.clear()
            self._refresh_copilot_status()
        except Exception as exc:
            self._copilot_status_lbl.setText(str(exc))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")
            QMessageBox.warning(self, "GitHub Copilot token", str(exc))

    def _copilot_test_token(self) -> None:
        try:
            from core.auth import copilot_client
            ok, message = copilot_client.test_copilot_token()
            self._copilot_status_lbl.setText(message)
            self._copilot_status_lbl.setStyleSheet(
                "color: #80c080;" if ok else "color: #c04040;"
            )
        except Exception as exc:
            self._copilot_status_lbl.setText(f"Test failed: {exc}")
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")

    def _tab_tts(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── PROVIDER card ─────────────────────────────────────────────────
        provider_card, provider_cv = self._card("Provider")
        self._fields["TTS_PROVIDER"] = self._combo(["cartesia", "elevenlabs", "none"])
        pf_w = QWidget()
        pf = QFormLayout(pf_w)
        pf.setContentsMargins(0, 0, 0, 0)
        pf.setSpacing(8)
        pf.addRow("TTS Provider", self._fields["TTS_PROVIDER"])
        provider_cv.addWidget(pf_w)
        outer.addWidget(provider_card)

        # ── API KEYS card ─────────────────────────────────────────────────
        keys_card, keys_cv = self._card("API Keys")
        tts_key_note = QLabel("<small>API keys are saved to the OS keychain. Leave blank to keep the stored key.</small>")
        tts_key_note.setWordWrap(True)
        keys_cv.addWidget(tts_key_note)

        self._fields["CARTESIA_API_KEY"] = self._password()
        self._fields["CARTESIA_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["CARTESIA_VOICE_ID"] = QLineEdit()
        self._fields["CARTESIA_VOICE_ID"].setPlaceholderText("e.g. a0e99841-438c-4a64-b679-ae501e7d6091")
        self._fields["ELEVENLABS_API_KEY"] = self._password()
        self._fields["ELEVENLABS_API_KEY"].setPlaceholderText("Stored in OS keychain")

        kf_w = QWidget()
        kf = QFormLayout(kf_w)
        kf.setContentsMargins(0, 0, 0, 0)
        kf.setSpacing(8)
        kf.addRow(_link_label("Cartesia API key", "https://play.cartesia.ai/keys"), self._fields["CARTESIA_API_KEY"])
        kf.addRow("Cartesia Voice ID", self._fields["CARTESIA_VOICE_ID"])
        kf.addRow(_sep(), _sep())
        kf.addRow(_link_label("ElevenLabs API key", "https://elevenlabs.io/app/settings/api-keys"), self._fields["ELEVENLABS_API_KEY"])
        keys_cv.addWidget(kf_w)
        outer.addWidget(keys_card)

        # ── TEST card ─────────────────────────────────────────────────────
        test_card, test_cv = self._card("Test")
        self._tts_test_status_lbl = QLabel()
        self._tts_test_status_lbl.setWordWrap(True)
        test_cv.addWidget(self._button_row(("Test TTS", self._test_tts_connection)))
        test_cv.addWidget(self._tts_test_status_lbl)
        outer.addWidget(test_card)

        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    def _tab_prompt(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── SYSTEM PROMPT card ────────────────────────────────────────────
        prompt_card, prompt_cv = self._card("System Prompt")
        note = QLabel("<small>This prompt is prepended to every LLM request as the system instruction.</small>")
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
        from PySide6.QtWidgets import QScrollArea, QSizePolicy
        container = QWidget()
        outer_layout = QVBoxLayout(container)
        outer_layout.setSpacing(12)
        outer_layout.setContentsMargins(12, 12, 12, 12)

        # ── CALLER HOTKEYS card ───────────────────────────────────────────
        caller_card, caller_cv = self._card("Caller Hotkeys")

        limits_fw = QWidget()
        limits_layout = QFormLayout(limits_fw)
        limits_layout.setContentsMargins(0, 0, 0, 0)
        limits_layout.setSpacing(6)
        self._fields["CONTEXT_BROWSER_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["TOOL_PLUGIN_DIR"] = QLineEdit()
        limits_layout.addRow("Browser fetch chars", self._fields["CONTEXT_BROWSER_MAX_CHARS"])
        limits_layout.addRow("Auto document chars", self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"])
        limits_layout.addRow("Tool document chars", self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"])
        limits_layout.addRow("Tool plugin folder", self._fields["TOOL_PLUGIN_DIR"])
        caller_cv.addWidget(limits_fw)

        self._callers_container = QWidget()
        self._callers_vlayout = QVBoxLayout(self._callers_container)
        self._callers_vlayout.setSpacing(8)
        self._callers_vlayout.setContentsMargins(0, 0, 0, 0)
        caller_cv.addWidget(self._callers_container)
        self._caller_blocks: list[dict] = []

        add_caller_btn = QPushButton("+ Add Caller Hotkey")
        add_caller_btn.clicked.connect(lambda: self._add_caller_block())
        btn_wrap = QHBoxLayout()
        btn_wrap.setContentsMargins(0, 4, 0, 4)
        btn_wrap.addWidget(add_caller_btn)
        btn_wrap.addStretch()
        caller_cv.addLayout(btn_wrap)

        outer_layout.addWidget(caller_card)

        # ── OTHER HOTKEYS card ────────────────────────────────────────────
        other_card, other_cv = self._card("Other Hotkeys")
        self._keybinds_layout = other_cv

        self._fields["HOTKEY_ADD_CONTEXT"]   = self._kb_special_row("Add selection as context")
        self._fields["HOTKEY_CLEAR_CONTEXT"] = self._kb_special_row("Clear context")
        self._fields["HOTKEY_SNIP"]          = self._kb_special_row("Snip screen region")

        snip_ctx = QWidget()
        snip_h = QHBoxLayout(snip_ctx)
        snip_h.setContentsMargins(0, 2, 0, 2)
        snip_h.setSpacing(10)
        self._fields["SNIP_CONTEXT_AMBIENT"] = QCheckBox("Ambient")
        self._fields["SNIP_CONTEXT_DOCUMENTS"] = QCheckBox("Open docs")
        self._fields["SNIP_CONTEXT_TOOLS"] = QCheckBox("Tools")
        snip_h.addSpacing(128)
        snip_h.addWidget(QLabel("Snip context:"))
        snip_h.addWidget(self._fields["SNIP_CONTEXT_AMBIENT"])
        snip_h.addWidget(self._fields["SNIP_CONTEXT_DOCUMENTS"])
        snip_h.addWidget(self._fields["SNIP_CONTEXT_TOOLS"])
        snip_h.addStretch()
        self._keybinds_layout.addWidget(snip_ctx)

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

        lbl = QLabel(label_text)
        lbl.setStyleSheet("font-style: italic; color: palette(placeholder-text);")
        h.addWidget(lbl)
        h.addStretch()

        self._keybinds_layout.addWidget(row_w)
        return key_edit

    def _add_caller_block(
        self,
        hotkey: str = "",
        label: str = "",
        paste_back: bool = False,
        custom_key: str = "s",
        context_ambient: bool = True,
        context_documents: bool = True,
        context_tools: bool = False,
        context_documents_mode: str | None = None,
        context_browser_mode: str = "off",
        context_github_mode: str = "off",
        context_memory_mode: str = "auto",
        context_screenshot: str = "off",
        intents: "list[dict] | None" = None,
    ) -> None:
        """Add a caller block (framed panel with header + intent rows) to the UI."""
        from PySide6.QtWidgets import QSizePolicy
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("QFrame { border: 1px solid palette(mid); border-radius: 4px; }")
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

        hdr_h.addWidget(QLabel("Name:"))
        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(110)
        label_edit.setPlaceholderText("Label")
        hdr_h.addWidget(label_edit)

        paste_cb = QCheckBox("Paste result back")
        paste_cb.setChecked(paste_back)
        hdr_h.addWidget(paste_cb)

        hdr_h.addWidget(QLabel("Enter key:"))
        custom_key_edit = QLineEdit(custom_key)
        custom_key_edit.setFixedWidth(36)
        custom_key_edit.setPlaceholderText("s")
        hdr_h.addWidget(custom_key_edit)

        hdr_h.addStretch()
        del_caller_btn = QPushButton("X Remove")
        hdr_h.addWidget(del_caller_btn)
        outer.addWidget(hdr)

        context_row = QWidget()
        context_h = QGridLayout(context_row)
        context_h.setContentsMargins(0, 0, 0, 0)
        context_h.setHorizontalSpacing(8)
        context_h.setVerticalSpacing(4)
        ambient_cb = QCheckBox("Ambient")
        ambient_cb.setChecked(context_ambient)
        docs_mode = context_documents_mode or ("auto" if context_documents else ("model" if context_tools else "off"))
        docs_combo = _context_mode_combo(docs_mode, allow_auto=True)
        docs_combo.setToolTip(
            "Open documents:\n"
            "Off — do not include document text.\n"
            "On — read supported open documents before sending the prompt.\n"
            "Let model decide — expose an open-document tool during the answer."
        )
        browser_combo = _context_mode_combo(context_browser_mode, allow_auto=True)
        browser_combo.setToolTip(
            "Browser/Web:\n"
            "Off — no web/browser tools.\n"
            "On — read the current browser page before sending the prompt.\n"
            "Let model decide — expose web search and browser page fetch tools."
        )
        github_combo = _context_mode_combo(context_github_mode, allow_auto=True)
        github_combo.setToolTip(
            "Git/GitHub:\n"
            "Off — no git or GitHub tools.\n"
            "On — read local git status and diff before sending the prompt.\n"
            "Let model decide — expose git status/diff and GitHub repo/issue tools."
        )
        memory_combo = _context_mode_combo(context_memory_mode, allow_auto=True)
        memory_combo.setToolTip(
            "Memory:\n"
            "Off — do not use stored facts for this caller.\n"
            "On — fetch relevant stored facts before sending the prompt.\n"
            "Let model decide — expose a memory search tool during the answer."
        )
        screenshot_combo = _context_mode_combo(context_screenshot, allow_auto=True)
        screenshot_combo.setToolTip(
            "Screenshot of your screen:\n"
            "• Off — never capture.\n"
            "• On — capture at hotkey time and send it with the query.\n"
            "• Let model decide — expose a screenshot tool during the answer."
        )
        context_h.addWidget(QLabel("Context:"), 0, 0)
        context_h.addWidget(ambient_cb, 0, 1)
        context_h.addWidget(QLabel("Screenshot:"), 0, 2)
        context_h.addWidget(screenshot_combo, 0, 3)
        context_h.addWidget(QLabel("Open docs:"), 0, 4)
        context_h.addWidget(docs_combo, 0, 5)
        context_h.addWidget(QLabel("Browser/Web:"), 1, 2)
        context_h.addWidget(browser_combo, 1, 3)
        context_h.addWidget(QLabel("Git/GitHub:"), 1, 4)
        context_h.addWidget(github_combo, 1, 5)
        context_h.addWidget(QLabel("Memory:"), 2, 2)
        context_h.addWidget(memory_combo, 2, 3)
        context_h.setColumnStretch(6, 1)
        outer.addWidget(context_row)

        # Intent rows column header
        from PySide6.QtWidgets import QSizePolicy as SP
        int_hdr = QWidget()
        int_hdr_h = QHBoxLayout(int_hdr)
        int_hdr_h.setContentsMargins(0, 2, 0, 0)
        int_hdr_h.setSpacing(6)
        for txt, w in [("Key", 40), ("Label", 130), ("Prompt", 0)]:
            lbl = QLabel(f"<small><b>{txt}</b></small>")
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
            "custom_key":     custom_key_edit,
            "context_ambient": ambient_cb,
            "context_documents": docs_combo,
            "context_documents_mode": docs_combo,
            "context_browser_mode": browser_combo,
            "context_github_mode": github_combo,
            "context_memory_mode": memory_combo,
            "context_screenshot": screenshot_combo,
            "intents_layout": intents_vlayout,
            "intent_rows":    [],
        }

        for r in (intents or []):
            self._add_caller_intent_row(blk, r.get("key", ""), r.get("label", ""), r.get("prompt", ""))

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
        h.addWidget(key_edit)

        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(130)
        label_edit.setPlaceholderText("Label")
        h.addWidget(label_edit)

        prompt_edit = QLineEdit(prompt)
        prompt_edit.setPlaceholderText("Prompt sent to LLM...")
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(prompt_edit)

        row_info: dict = {"widget": row_w, "key": key_edit, "label": label_edit, "prompt": prompt_edit}

        del_btn = QPushButton("X")
        del_btn.setFixedWidth(40)
        del_btn.setStyleSheet("QPushButton { padding: 5px 4px; }")
        del_btn.clicked.connect(lambda: self._delete_caller_intent_row(blk, row_info))
        h.addWidget(del_btn)

        blk["intents_layout"].addWidget(row_w)
        blk["intent_rows"].append(row_info)

    def _delete_caller_intent_row(self, blk: dict, row_info: dict) -> None:
        if row_info in blk["intent_rows"]:
            blk["intent_rows"].remove(row_info)
        row_info["widget"].deleteLater()

    def _delete_caller_block(self, blk: dict) -> None:
        if blk in self._caller_blocks:
            self._caller_blocks.remove(blk)
        blk["widget"].deleteLater()

    def _tab_memory(self) -> QWidget:
        """Memory tab: LTM config knobs + embedded fact browser."""
        from PySide6.QtWidgets import QScrollArea, QSizePolicy

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        w = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(12)
        root.setContentsMargins(12, 12, 12, 12)

        # --- Config card ---
        cfg_card, cfg_cv = self._card("Memory Settings")
        fw = QWidget()
        f = QFormLayout(fw)
        f.setSpacing(8)
        f.setContentsMargins(0, 0, 0, 0)

        note_lbl = QLabel("<small>Memory model is configured in the <b>LLM</b> tab → Memory model section.</small>")
        note_lbl.setWordWrap(True)
        f.addRow("", note_lbl)

        mem_auto = QCheckBox("Automatically extract long-term facts from conversation")
        mem_auto.setToolTip("Off by default to avoid clutter. Explicit remember/note commands still save facts.")
        self._fields["MEMORY_AUTO_CONSOLIDATE"] = mem_auto
        f.addRow("", mem_auto)

        mem_interval = QLineEdit(self._env.get("MEMORY_CONSOLIDATION_INTERVAL", "15"))
        mem_interval.setPlaceholderText("minutes between consolidations")
        self._fields["MEMORY_CONSOLIDATION_INTERVAL"] = mem_interval
        f.addRow("Consolidation interval (min):", mem_interval)

        mem_topk = QLineEdit(self._env.get("MEMORY_TOP_K", "3"))
        mem_topk.setPlaceholderText("number of facts to retrieve per query")
        self._fields["MEMORY_TOP_K"] = mem_topk
        f.addRow("Retrieval top-k:", mem_topk)

        mem_distance = QLineEdit(self._env.get("MEMORY_RELEVANCE_MAX_DISTANCE", "0.55"))
        mem_distance.setPlaceholderText("lower is stricter; 0.55 is conservative")
        self._fields["MEMORY_RELEVANCE_MAX_DISTANCE"] = mem_distance
        f.addRow("Retrieval max distance:", mem_distance)

        mem_budget = QLineEdit(self._env.get("MEMORY_STM_TOKEN_BUDGET", "4000"))
        mem_budget.setPlaceholderText("tokens before STM compression kicks in")
        self._fields["MEMORY_STM_TOKEN_BUDGET"] = mem_budget
        f.addRow("STM token budget:", mem_budget)

        cfg_cv.addWidget(fw)
        root.addWidget(cfg_card)

        # --- Fact browser card ---
        browser_card, browser_cv = self._card("Stored Facts")
        self._memory_browser_cv = browser_cv
        placeholder = QLabel("Stored facts load when this tab is opened.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: palette(placeholder-text);")
        browser_cv.addWidget(placeholder)

        root.addWidget(browser_card, stretch=1)
        scroll.setWidget(w)
        return scroll

    def _maybe_load_memory_tab(self, index: int) -> None:
        if index != self._memory_tab_index or self._memory_panel is not None or self._memory_loading:
            return
        self._memory_loading = True
        self._memory_load_token = getattr(self, "_memory_load_token", 0) + 1
        token = self._memory_load_token
        if self._memory_browser_cv is not None:
            self._replace_memory_browser_message("Loading stored facts...")
        QTimer.singleShot(
            self._memory_load_timeout_ms,
            lambda: self._on_memory_panel_load_timeout(token),
        )
        QTimer.singleShot(
            self._memory_load_start_delay_ms,
            lambda: self._load_memory_panel(token),
        )

    def _cancel_memory_panel_load(self) -> None:
        if not getattr(self, "_memory_loading", False):
            return
        self._memory_load_token = getattr(self, "_memory_load_token", 0) + 1
        self._memory_loading = False

    def _replace_memory_browser_message(self, text: str, *, error: bool = False) -> None:
        layout = self._memory_browser_cv
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet("color: #c00;" if error else "color: palette(placeholder-text);")
        layout.addWidget(label)

    def _load_memory_panel(self, token: int | None = None) -> None:
        layout = self._memory_browser_cv
        if layout is None:
            self._memory_loading = False
            return
        if token is None:
            token = getattr(self, "_memory_load_token", 0)
        if token != getattr(self, "_memory_load_token", 0) or not self._memory_loading:
            return
        try:
            visible = self.isVisible()
        except RuntimeError:
            visible = True
        if not visible:
            self._cancel_memory_panel_load()
            return

        def worker() -> None:
            try:
                from core.memory_store.store import get_all_facts_lightweight

                manager = _SettingsMemoryManagerProxy()
                facts = get_all_facts_lightweight()
                self._memory_load_signals.done.emit(token, manager, facts, "")
            except Exception as exc:
                self._memory_load_signals.done.emit(token, None, [], str(exc))

        threading.Thread(
            target=worker,
            name="wisp-memory-settings-load",
            daemon=True,
        ).start()

    def _on_memory_panel_load_timeout(self, token: int) -> None:
        if token != self._memory_load_token or not self._memory_loading or self._memory_panel is not None:
            return
        self._memory_loading = False
        self._replace_memory_browser_message(
            "Memory store is still starting up. Close and reopen Settings to try again.",
            error=True,
        )

    def _on_memory_panel_loaded(self, token: int, manager, facts, error: str) -> None:
        if token != self._memory_load_token:
            return
        layout = self._memory_browser_cv
        self._memory_loading = False
        if layout is None:
            return
        if error:
            self._replace_memory_browser_message(f"Memory store unavailable:\n{error}", error=True)
            return
        try:
            from ui.memory_viewer import MemoryPanel

            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            panel = MemoryPanel(manager, self, initial_facts=list(facts or []), read_only=True)
            self._memory_panel = panel
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout.addWidget(panel)
        except Exception as exc:
            self._replace_memory_browser_message(f"Memory store unavailable:\n{exc}", error=True)

    def _tab_tools(self) -> QWidget:
        from core.llm_clients.client import get_tool_registry
        from core.system.paths import TOOL_KEYWORDS_FILE

        registry = get_tool_registry()
        self._tool_keyword_fields: list[tuple[str, QLineEdit]] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        card, cv = self._card("Tool Calling Keywords")

        note = QLabel(
            "Tools with <b>no keywords</b> are always sent to the model.<br>"
            "Tools with keywords are only sent when the prompt contains at least one.<br>"
            "Separate multiple keywords with commas."
        )
        note.setWordWrap(True)
        cv.addWidget(note)

        try:
            first = True
            for spec in registry.list_tools():
                keywords = registry._keyword_map.get(spec.name, [])

                if not first:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("max-height: 1px; background: rgba(128,128,128,0.25); margin: 2px 0;")
                    cv.addWidget(sep)
                first = False

                tool_w = QWidget()
                tool_h = QHBoxLayout(tool_w)
                tool_h.setContentsMargins(0, 4, 0, 4)
                tool_h.setSpacing(12)

                name_col = QWidget()
                name_v = QVBoxLayout(name_col)
                name_v.setContentsMargins(0, 0, 0, 0)
                name_v.setSpacing(3)

                name_lbl = QLabel(f"<b>{spec.name}</b>")
                name_v.addWidget(name_lbl)

                if spec.description:
                    desc_lbl = QLabel(spec.description)
                    desc_lbl.setWordWrap(True)
                    desc_lbl.setStyleSheet("color: #6b6b7e; font-size: 9pt;")
                    name_v.addWidget(desc_lbl)

                field = QLineEdit(", ".join(keywords))
                field.setPlaceholderText("leave empty to always include")
                self._tool_keyword_fields.append((spec.name, field))

                tool_h.addWidget(name_col, 3)
                tool_h.addWidget(field, 2)
                cv.addWidget(tool_w)

        except Exception as exc:
            cv.addWidget(QLabel(f"Could not load tools: {exc}"))

        save_btn = QPushButton("Save keyword filters")
        save_btn.clicked.connect(lambda: self._save_tool_keywords(registry, TOOL_KEYWORDS_FILE))
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(save_btn)
        cv.addLayout(save_row)

        outer.addWidget(card)
        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    def _save_tool_keywords(self, registry, path) -> None:
        for tool_name, field in getattr(self, "_tool_keyword_fields", []):
            raw = field.text()
            keywords = [k.strip().lower() for k in raw.split(",") if k.strip()]
            registry.set_keyword_filter(tool_name, keywords)
        try:
            registry.save_keyword_filters(path)
            self._status_lbl.setText("Tool keyword filters saved.")
            QTimer.singleShot(3000, lambda: self._status_lbl.setText(""))
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def _tab_app(self) -> QWidget:
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
        f = QFormLayout(fw)
        f.setSpacing(10)
        f.setContentsMargins(0, 0, 0, 0)

        theme_combo = _NoScrollCombo()
        theme_combo.addItem("System default", "system")
        theme_combo.addItem("Light", "light")
        theme_combo.addItem("Dark", "dark")
        self._fields["THEME_MODE"] = theme_combo
        self._fields["ICON_AUTO_HIDE"] = QCheckBox("Auto-hide icon (only visible when active)")
        self._fields["CHAT_AUTO_ELABORATE"] = QCheckBox("Auto-elaborate when opening chat")
        self._fields["CHAT_ELABORATE_PROMPT"] = QLineEdit()
        self._fields["CHAT_ELABORATE_PROMPT"].setPlaceholderText("e.g. Please elaborate on that.")

        self._fields["ICON_SIZE"] = QLineEdit()
        self._fields["ICON_SIZE"].setPlaceholderText("e.g. 80")
        self._fields["BUBBLE_WIDTH"] = QLineEdit()
        self._fields["BUBBLE_WIDTH"].setPlaceholderText("e.g. 340")
        self._fields["BUBBLE_LINES"] = QLineEdit()
        self._fields["BUBBLE_LINES"].setPlaceholderText("e.g. 3")
        _bubble_color_row      = self._color_field("BUBBLE_COLOR",          "e.g. #1c1c24dc")
        _bubble_text_color_row = self._color_field("BUBBLE_TEXT_COLOR",     "e.g. #e6e6e6")
        _read_word_color_row   = self._color_field("BUBBLE_READ_WORD_COLOR", "e.g. #4da3ff")
        self._fields["BUBBLE_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_REVEAL_WPM"].setPlaceholderText("e.g. 170")
        self._fields["BUBBLE_HOLD_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_HOLD_REVEAL_WPM"].setPlaceholderText("e.g. 480")
        self._fields["TTS_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.0")
        self._fields["TTS_HOLD_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_HOLD_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.35")

        f.addRow("Theme", self._fields["THEME_MODE"])
        f.addRow("", self._fields["ICON_AUTO_HIDE"])
        f.addRow("", self._fields["CHAT_AUTO_ELABORATE"])
        f.addRow("Elaborate prompt", self._fields["CHAT_ELABORATE_PROMPT"])
        f.addRow(_sep(), _sep())
        f.addRow("Icon size (px)", self._fields["ICON_SIZE"])
        f.addRow("Bubble width (px)", self._fields["BUBBLE_WIDTH"])
        f.addRow("Bubble lines", self._fields["BUBBLE_LINES"])
        f.addRow("Bubble color", _bubble_color_row)
        f.addRow("Bubble text color", _bubble_text_color_row)
        f.addRow("Read word color", _read_word_color_row)
        f.addRow("Bubble text speed (WPM)", self._fields["BUBBLE_REVEAL_WPM"])
        f.addRow("Bubble hold speed (WPM)", self._fields["BUBBLE_HOLD_REVEAL_WPM"])
        f.addRow("TTS speed", self._fields["TTS_PLAYBACK_RATE"])
        f.addRow("TTS hold speed", self._fields["TTS_HOLD_PLAYBACK_RATE"])
        cv.addWidget(fw)
        outer.addWidget(card)
        outer.addStretch()
        scroll.setWidget(outer_w)
        return scroll

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model_combo(self, provider: str = "") -> QComboBox:
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
        cb = _NoScrollCombo()
        for opt in options:
            cb.addItem(_PROVIDER_LABELS.get(opt, opt) if opt else "", opt)
        if current is not None:
            idx = cb.findData(current)
            if idx >= 0:
                cb.setCurrentIndex(idx)
        return cb

    def _color_field(self, field_key: str, placeholder: str) -> QWidget:
        """QLineEdit + color-swatch button that opens QColorDialog. Stores #RRGGBBAA."""
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor

        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        self._fields[field_key] = edit

        swatch = QPushButton()
        swatch.setFixedSize(26, 26)
        swatch.setToolTip("Pick color")

        def _parse(text: str) -> QColor:
            s = text.strip()
            if s.startswith("#") and len(s) == 9:
                try:
                    return QColor(int(s[1:3],16), int(s[3:5],16), int(s[5:7],16), int(s[7:9],16))
                except ValueError:
                    pass
            c = QColor(s)
            return c if c.isValid() else QColor()

        def _fmt(c: QColor) -> str:
            return f"#{c.red():02x}{c.green():02x}{c.blue():02x}{c.alpha():02x}"

        def _update_swatch(text=""):
            c = _parse(edit.text())
            if c.isValid():
                swatch.setStyleSheet(
                    f"QPushButton {{ background: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()});"
                    f" border: 1px solid #666; border-radius: 4px; padding: 0px; }}"
                )
            else:
                swatch.setStyleSheet(
                    "QPushButton { background: transparent; border: 1px solid #666; border-radius: 4px; padding: 0px; }"
                )

        def _pick():
            c = _parse(edit.text())
            if not c.isValid():
                c = QColor(255, 255, 255, 255)
            chosen = QColorDialog.getColor(
                c, self, "Pick color",
                QColorDialog.ColorDialogOption.ShowAlphaChannel,
            )
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
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, handler in buttons:
            btn = QPushButton(text)
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
            hdr = QLabel(title.upper())
            hdr.setObjectName("sectionHeader")
            cv.addWidget(hdr)
        return card, cv

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

        fb_w = QWidget(); fb_f = QFormLayout(fb_w)
        fb_f.setContentsMargins(0, 4, 0, 0); fb_f.setSpacing(8)
        self._add_fallback_section(fb_f, fallback_key, fallback_prefix, providers=fallback_providers)
        v.addWidget(fb_w)

        return w

    def _password(self) -> QLineEdit:
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

    def _remove_fallback_row(self, key: str, row_info: dict) -> None:
        if row_info in self._fallback_rows[key]:
            self._fallback_rows[key].remove(row_info)
        form = self._fallback_rows[f"{key}__form"]  # type: ignore[index]
        form.removeRow(row_info["provider_label"])
        form.removeRow(row_info["model_label"])
        self._renumber_fallback_rows(key)

    def _renumber_fallback_rows(self, key: str) -> None:
        prefix = self._fallback_rows[f"{key}__prefix"]  # type: ignore[index]
        for idx, row in enumerate(self._fallback_rows[key], 1):
            row["provider_label"].setText(f"{prefix} provider {idx}")
            row["model_label"].setText(f"{prefix} model {idx}")

    def _set_fallback_rows(self, key: str, raw: str) -> None:
        for row in list(self._fallback_rows[key]):
            self._remove_fallback_row(key, row)
        for provider, model in _parse_fallback_rows(raw):
            self._add_fallback_row(key, provider, model)

    def _get_fallback_rows(self, key: str) -> str:
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
        return bool((self._env.get(name) or "").strip()) or secret_store.configured_marker(name)

    def _load_values(self):
        import config as cfg

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
        ]
        for provider, key_name in _LLM_KEY_MAP:
            if self._secret_configured_fast(key_name):
                self._add_api_key_row(provider=provider, stored=True)
        # No default placeholder row — the list stays empty until the user adds
        # a key via "+ Add API Key". Avoids a spurious "Groq" row on every open.

        # ── Model sections ────────────────────────────────────────────────
        def _load_section(sk, penv, menv, fenv, pdef, mdef, fdef=""):
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

        # ── TTS / Custom keys (still in self._fields) ─────────────────────
        for name, label in [
            ("CARTESIA_API_KEY",   "Cartesia"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"),
            ("CUSTOM_API_KEY",     "Custom provider"),
        ]:
            if name not in self._fields:
                continue
            self._fields[name].clear()  # type: ignore[attr-defined]
            status = "stored in OS keychain" if self._secret_configured_fast(name) else "not configured"
            self._fields[name].setPlaceholderText(status)  # type: ignore[attr-defined]

        _set(self._fields["TTS_PROVIDER"], self._env.get("TTS_PROVIDER", cfg.TTS_PROVIDER))
        _set(self._fields["CARTESIA_VOICE_ID"], self._env.get("CARTESIA_VOICE_ID", ""))
        _set(self._fields["HOTKEY_ADD_CONTEXT"],   self._env.get("HOTKEY_ADD_CONTEXT",   cfg.HOTKEY_ADD_CONTEXT))
        _set(self._fields["HOTKEY_CLEAR_CONTEXT"], self._env.get("HOTKEY_CLEAR_CONTEXT", cfg.HOTKEY_CLEAR_CONTEXT))
        _set(self._fields["HOTKEY_SNIP"],          self._env.get("HOTKEY_SNIP",          cfg.HOTKEY_SNIP))
        self._fields["SNIP_CONTEXT_AMBIENT"].setChecked(self._env.get("SNIP_CONTEXT_AMBIENT", str(cfg.SNIP_CONTEXT_AMBIENT)).lower() == "true")  # type: ignore
        self._fields["SNIP_CONTEXT_DOCUMENTS"].setChecked(self._env.get("SNIP_CONTEXT_DOCUMENTS", str(cfg.SNIP_CONTEXT_DOCUMENTS)).lower() == "true")  # type: ignore
        self._fields["SNIP_CONTEXT_TOOLS"].setChecked(self._env.get("SNIP_CONTEXT_TOOLS", str(cfg.SNIP_CONTEXT_TOOLS)).lower() == "true")  # type: ignore
        _set(self._fields["CUSTOM_BASE_URL"],      self._env.get("CUSTOM_BASE_URL",      cfg.CUSTOM_BASE_URL))
        _set(self._fields["GITHUB_CLIENT_ID"],     self._env.get("GITHUB_CLIENT_ID",     cfg.GITHUB_CLIENT_ID))
        _set(self._fields["GITHUB_OAUTH_SCOPES"],  self._env.get("GITHUB_OAUTH_SCOPES",  cfg.GITHUB_OAUTH_SCOPES))
        _set(self._fields["CONTEXT_BROWSER_MAX_CHARS"], self._env.get("CONTEXT_BROWSER_MAX_CHARS", str(cfg.CONTEXT_BROWSER_MAX_CHARS)))
        _set(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS)))
        _set(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_TOOL_DOCUMENT_MAX_CHARS)))
        _set(self._fields["TOOL_PLUGIN_DIR"], self._env.get("TOOL_PLUGIN_DIR", cfg.TOOL_PLUGIN_DIR))

        self._fields["MEMORY_AUTO_CONSOLIDATE"].setChecked(
            self._env.get("MEMORY_AUTO_CONSOLIDATE", str(cfg.MEMORY_AUTO_CONSOLIDATE)).lower() == "true"
        )  # type: ignore
        _set(self._fields["MEMORY_CONSOLIDATION_INTERVAL"], self._env.get("MEMORY_CONSOLIDATION_INTERVAL", str(cfg.MEMORY_CONSOLIDATION_INTERVAL)))
        _set(self._fields["MEMORY_TOP_K"],           self._env.get("MEMORY_TOP_K",           str(cfg.MEMORY_TOP_K)))
        _set(self._fields["MEMORY_RELEVANCE_MAX_DISTANCE"], self._env.get("MEMORY_RELEVANCE_MAX_DISTANCE", str(cfg.MEMORY_RELEVANCE_MAX_DISTANCE)))
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
                intents.append({
                    "key":    self._env.get(f"CALLER_{n}_INTENT_{m}_KEY",    di.get("key", "")),
                    "label":  self._env.get(f"CALLER_{n}_INTENT_{m}_LABEL",  di.get("label", "")),
                    "prompt": self._env.get(f"CALLER_{n}_INTENT_{m}_PROMPT", di.get("prompt", "")),
                })
            legacy_documents = self._env.get(
                f"CALLER_{n}_CONTEXT_DOCUMENTS",
                str(cr.get("context_documents", True)),
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
                cr.get("context_memory_mode") or "auto",
            )
            self._add_caller_block(
                hotkey     = self._env.get(f"CALLER_{n}_HOTKEY",     cr.get("hotkey", "")),
                label      = self._env.get(f"CALLER_{n}_LABEL",      cr.get("label", "")),
                paste_back = self._env.get(f"CALLER_{n}_PASTE_BACK", str(cr.get("paste_back", False))).lower() == "true",
                custom_key = self._env.get(f"CALLER_{n}_CUSTOM_KEY", cr.get("custom_key", "s")),
                context_ambient = self._env.get(f"CALLER_{n}_CONTEXT_AMBIENT", str(cr.get("context_ambient", True))).lower() == "true",
                context_documents = documents_mode == "auto",
                context_tools = any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode)),
                context_documents_mode = documents_mode,
                context_browser_mode = browser_mode,
                context_github_mode = github_mode,
                context_memory_mode = memory_mode,
                context_screenshot = normalize_screenshot_mode(self._env.get(f"CALLER_{n}_CONTEXT_SCREENSHOT", cr.get("context_screenshot", "off"))),
                intents    = intents,
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
        self._fields["ICON_AUTO_HIDE"].setChecked(auto_hide)  # type: ignore

        auto_elab = self._env.get("CHAT_AUTO_ELABORATE", str(cfg.CHAT_AUTO_ELABORATE)).lower() == "true"
        self._fields["CHAT_AUTO_ELABORATE"].setChecked(auto_elab)  # type: ignore
        _set(self._fields["CHAT_ELABORATE_PROMPT"],
             self._env.get("CHAT_ELABORATE_PROMPT", cfg.CHAT_ELABORATE_PROMPT))

        _set(self._fields["ICON_SIZE"],    self._env.get("ICON_SIZE", self._env.get("DOLL_SIZE", str(cfg.ICON_SIZE))))
        _set(self._fields["BUBBLE_WIDTH"], self._env.get("BUBBLE_WIDTH", str(cfg.BUBBLE_WIDTH)))
        _set(self._fields["BUBBLE_LINES"], self._env.get("BUBBLE_LINES", str(cfg.BUBBLE_LINES)))
        _set(self._fields["BUBBLE_COLOR"], self._env.get("BUBBLE_COLOR", cfg.BUBBLE_COLOR))
        _set(self._fields["BUBBLE_TEXT_COLOR"], self._env.get("BUBBLE_TEXT_COLOR", cfg.BUBBLE_TEXT_COLOR))
        _set(self._fields["BUBBLE_READ_WORD_COLOR"], self._env.get("BUBBLE_READ_WORD_COLOR", cfg.BUBBLE_READ_WORD_COLOR))
        _set(self._fields["BUBBLE_REVEAL_WPM"], self._env.get("BUBBLE_REVEAL_WPM", str(cfg.BUBBLE_REVEAL_WPM)))
        _set(self._fields["BUBBLE_HOLD_REVEAL_WPM"], self._env.get("BUBBLE_HOLD_REVEAL_WPM", str(cfg.BUBBLE_HOLD_REVEAL_WPM)))
        _set(self._fields["TTS_PLAYBACK_RATE"], self._env.get("TTS_PLAYBACK_RATE", str(cfg.TTS_PLAYBACK_RATE)))
        _set(self._fields["TTS_HOLD_PLAYBACK_RATE"], self._env.get("TTS_HOLD_PLAYBACK_RATE", str(cfg.TTS_HOLD_PLAYBACK_RATE)))

        util_val = self._env.get("SYSTEM_PROMPT_UTILITY", cfg.SYSTEM_PROMPT_UTILITY)
        self._fields["SYSTEM_PROMPT_UTILITY"].setPlainText(util_val)  # type: ignore

    def _effective_secret_value(self, name: str) -> str:
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
        label.setText(message)
        label.setStyleSheet(f"color: {color};")

    def _set_test_pending(self, label: QLabel, message: str = "Testing...") -> None:
        label.setText(message)
        label.setStyleSheet("color: #c0c040;")

    def _set_status_label(self, label: QLabel, ok, message: str) -> None:
        if ok is None:
            color = "palette(placeholder-text)"
        elif ok:
            color = "#80c080"
        else:
            color = "#c04040"
        label.setText(message)
        label.setStyleSheet(f"color: {color};")

    def _queue_status_result(self, token: int, attr: str, ok, message: str) -> None:
        with self._pending_status_results_lock:
            self._pending_status_results.append((token, attr, ok, message))

    def _schedule_open_status_refresh(self) -> None:
        self._status_refresh_token += 1
        token = self._status_refresh_token
        self._status_refresh_running = True
        for attr in ("_chatgpt_status_lbl", "_github_status_lbl", "_copilot_status_lbl"):
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, None, "Checking status...")
        if not self._status_result_timer.isActive():
            self._status_result_timer.start()

        def _worker() -> None:
            try:
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

                try:
                    from core.auth import copilot_auth

                    stored, message = copilot_auth.token_status()
                    self._queue_status_result(token, "_copilot_status_lbl", bool(stored), message)
                except Exception as exc:
                    self._queue_status_result(token, "_copilot_status_lbl", False, f"Keychain error: {exc}")
            finally:
                self._queue_status_result(token, "__done__", None, "")

        threading.Thread(target=_worker, daemon=True, name="settings-status-refresh").start()

    def _drain_status_results(self) -> None:
        with self._pending_status_results_lock:
            pending = list(self._pending_status_results)
            self._pending_status_results.clear()
        for token, attr, ok, message in pending:
            if token != self._status_refresh_token:
                continue
            if attr == "__done__":
                self._status_refresh_running = False
                continue
            label = getattr(self, attr, None)
            if isinstance(label, QLabel):
                self._set_status_label(label, ok, message)
        if not self._status_refresh_running and not pending:
            self._status_result_timer.stop()

    def _start_async_test(self, test_key: str, status_label: QLabel, runner) -> None:
        token = self._latest_test_token.get(test_key, 0) + 1
        self._latest_test_token[test_key] = token
        self._running_test_tokens.add((test_key, token))
        self._set_test_pending(status_label)

        def _worker() -> None:
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
        with self._pending_test_results_lock:
            pending = list(self._pending_test_results)
            self._pending_test_results.clear()
        for test_key, token, ok, message in pending:
            self._running_test_tokens.discard((test_key, token))
            if self._latest_test_token.get(test_key) != token:
                continue
            label = getattr(self, f"_{test_key}_status_lbl", None)
            if isinstance(label, QLabel):
                self._set_test_status(label, ok, message)
        if not self._running_test_tokens and not pending:
            self._test_result_timer.stop()

    def _test_llm_route(self, *, section_key: str, route_name: str, status_label: QLabel, image: bool = False) -> None:
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
        self._test_llm_route(
            section_key="LLM",
            route_name="LLM",
            status_label=self._llm_test_status_lbl,
        )

    def _test_vision_connection(self) -> None:
        self._test_llm_route(
            section_key="VISION_LLM",
            route_name="VISION_LLM",
            status_label=self._vision_test_status_lbl,
            image=True,
        )

    def _test_memory_connection(self) -> None:
        self._test_llm_route(
            section_key="MEMORY_LLM",
            route_name="MEMORY_LLM",
            status_label=self._memory_test_status_lbl,
        )

    def _test_tts_connection(self) -> None:
        from core import tts

        provider = _get(self._fields["TTS_PROVIDER"]).strip().lower()
        cartesia_api_key = self._effective_secret_value("CARTESIA_API_KEY")
        cartesia_voice_id = _get(self._fields["CARTESIA_VOICE_ID"]).strip()
        elevenlabs_api_key = self._effective_secret_value("ELEVENLABS_API_KEY")
        self._start_async_test(
            "tts_test",
            self._tts_test_status_lbl,
            lambda: tts.test_connection(
                provider,
                cartesia_api_key=cartesia_api_key,
                cartesia_voice_id=cartesia_voice_id,
                elevenlabs_api_key=elevenlabs_api_key,
            ),
        )

    def _apply(self):
        """Save settings, apply changes live, then close the dialog."""
        if self._do_save():
            import config
            from core.llm_clients import client as _llm
            from core import tts as _tts
            from ui.shared.theme import apply_app_theme
            config.reload()
            _llm.reset_clients()
            _tts.reset_connections()
            apply_app_theme()
            self._apply_dialog_theme()
            # Silently (re)bake voice-matched filler clips for the now-current
            # TTS settings. No-ops when the cache already matches the voice id
            # or when TTS is disabled. Background thread so Apply stays snappy
            # and the user is never prompted.
            try:
                from core import filler_bake
                filler_bake.bake_in_background()
            except Exception:
                pass
            if self._on_apply:
                self._on_apply()
            self.accept()

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
            "xAI, Together, Cerebras, Cartesia, ElevenLabs, custom)\n"
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
                self, "Reset error",
                f"Could not erase the settings file:\n{exc}",
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
            apply_app_theme()
            self._apply_dialog_theme()
        except Exception as exc:  # noqa: BLE001 — reset already happened on disk
            _settings_log.error("Live reload after reset failed: %s", exc)
        self._env = _read_env()
        self._load_values()
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
                self, "Reset partly complete",
                "Settings were reset, but these items could not be fully cleared:\n\n"
                + "\n".join(f"• {item}" for item in key_failures)
                + "\n\nYou may need to remove them manually (e.g. from your system "
                "credential store). See the log for details.",
            )
        else:
            QMessageBox.information(
                self, "Reset complete",
                "All API keys were removed from the OS keychain, you were signed "
                "out of all OAuth logins, and every setting was reset to defaults.",
            )

    def _do_save(self) -> bool:
        """Write .env. Returns True on success, False if validation failed.

        Keychain key storage is attempted first and reports its own per-key
        warnings, but a keychain failure does NOT abort the save: the rest of the
        settings are still written to .env so the user never loses everything just
        because one API key could not reach the OS keychain.
        """
        self._save_api_keys_to_keychain()

        def _section_vals(sk):
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
            "TTS_PROVIDER":      _get(self._fields["TTS_PROVIDER"]),
            "CARTESIA_VOICE_ID": _get(self._fields["CARTESIA_VOICE_ID"]),
            "HOTKEY_ADD_CONTEXT":  _get(self._fields["HOTKEY_ADD_CONTEXT"]),
            "HOTKEY_CLEAR_CONTEXT": _get(self._fields["HOTKEY_CLEAR_CONTEXT"]),
            "HOTKEY_SNIP":         _get(self._fields["HOTKEY_SNIP"]),
            "SNIP_CONTEXT_AMBIENT": str(self._fields["SNIP_CONTEXT_AMBIENT"].isChecked()),  # type: ignore
            "SNIP_CONTEXT_DOCUMENTS": str(self._fields["SNIP_CONTEXT_DOCUMENTS"].isChecked()),  # type: ignore
            "SNIP_CONTEXT_TOOLS": str(self._fields["SNIP_CONTEXT_TOOLS"].isChecked()),  # type: ignore
            "CONTEXT_BROWSER_MAX_CHARS": _get(self._fields["CONTEXT_BROWSER_MAX_CHARS"]),
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"]),
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"]),
            "TOOL_PLUGIN_DIR": _get(self._fields["TOOL_PLUGIN_DIR"]),
            "CUSTOM_BASE_URL":            _get(self._fields["CUSTOM_BASE_URL"]),
            "GITHUB_CLIENT_ID":          _get(self._fields["GITHUB_CLIENT_ID"]),
            "GITHUB_OAUTH_SCOPES":       _get(self._fields["GITHUB_OAUTH_SCOPES"]),
            "MEMORY_AUTO_CONSOLIDATE":   str(self._fields["MEMORY_AUTO_CONSOLIDATE"].isChecked()),  # type: ignore
            "MEMORY_CONSOLIDATION_INTERVAL": _get(self._fields["MEMORY_CONSOLIDATION_INTERVAL"]),
            "MEMORY_TOP_K":             _get(self._fields["MEMORY_TOP_K"]),
            "MEMORY_RELEVANCE_MAX_DISTANCE": _get(self._fields["MEMORY_RELEVANCE_MAX_DISTANCE"]),
            "MEMORY_STM_TOKEN_BUDGET":  _get(self._fields["MEMORY_STM_TOKEN_BUDGET"]),
            "CALLER_COUNT":  str(len(self._caller_blocks)),
            "THEME_MODE":       self._fields["THEME_MODE"].currentData(),  # type: ignore[attr-defined]
            "ICON_AUTO_HIDE":    str(self._fields["ICON_AUTO_HIDE"].isChecked()),  # type: ignore
            "CHAT_AUTO_ELABORATE": str(self._fields["CHAT_AUTO_ELABORATE"].isChecked()),  # type: ignore
            "CHAT_ELABORATE_PROMPT": _get(self._fields["CHAT_ELABORATE_PROMPT"]),
            "ICON_SIZE":    _get(self._fields["ICON_SIZE"]),
            "BUBBLE_WIDTH": _get(self._fields["BUBBLE_WIDTH"]),
            "BUBBLE_LINES": _get(self._fields["BUBBLE_LINES"]),
            "BUBBLE_COLOR": _get(self._fields["BUBBLE_COLOR"]),
            "BUBBLE_TEXT_COLOR": _get(self._fields["BUBBLE_TEXT_COLOR"]),
            "BUBBLE_READ_WORD_COLOR": _get(self._fields["BUBBLE_READ_WORD_COLOR"]),
            "BUBBLE_REVEAL_WPM": _get(self._fields["BUBBLE_REVEAL_WPM"]),
            "BUBBLE_HOLD_REVEAL_WPM": _get(self._fields["BUBBLE_HOLD_REVEAL_WPM"]),
            "TTS_PLAYBACK_RATE": _get(self._fields["TTS_PLAYBACK_RATE"]),
            "TTS_HOLD_PLAYBACK_RATE": _get(self._fields["TTS_HOLD_PLAYBACK_RATE"]),
            "SYSTEM_PROMPT_UTILITY": self._fields["SYSTEM_PROMPT_UTILITY"].toPlainText(),  # type: ignore
        }
        # Key conflict check (caller hotkeys + special hotkeys)
        all_keys = (
            [_get(blk["hotkey"]).strip().lower() for blk in self._caller_blocks]
            + [_get(self._fields[k]).strip().lower() for k in ("HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP")]
        )
        non_empty = [k for k in all_keys if k]
        if len(non_empty) != len(set(non_empty)):
            QMessageBox.warning(self, "Duplicate keys",
                                "Two or more bindings share the same key.\nPlease resolve conflicts before saving.")
            return False
        for i, blk in enumerate(self._caller_blocks):
            n = i + 1
            vals[f"CALLER_{n}_HOTKEY"]        = _get(blk["hotkey"])
            vals[f"CALLER_{n}_LABEL"]         = _get(blk["label"])
            vals[f"CALLER_{n}_PASTE_BACK"]    = str(blk["paste_back"].isChecked())  # type: ignore
            vals[f"CALLER_{n}_CUSTOM_KEY"]    = _get(blk["custom_key"])
            vals[f"CALLER_{n}_CONTEXT_AMBIENT"] = str(blk["context_ambient"].isChecked())  # type: ignore
            documents_mode = str(blk["context_documents_mode"].currentData())  # type: ignore[attr-defined]
            browser_mode = str(blk["context_browser_mode"].currentData())  # type: ignore[attr-defined]
            github_mode = str(blk["context_github_mode"].currentData())  # type: ignore[attr-defined]
            memory_mode = str(blk["context_memory_mode"].currentData())  # type: ignore[attr-defined]
            vals[f"CALLER_{n}_CONTEXT_DOCUMENTS_MODE"] = documents_mode
            vals[f"CALLER_{n}_CONTEXT_BROWSER_MODE"] = browser_mode
            vals[f"CALLER_{n}_CONTEXT_GITHUB_MODE"] = github_mode
            vals[f"CALLER_{n}_CONTEXT_MEMORY_MODE"] = memory_mode
            # Compatibility values for older branches/scripts.
            vals[f"CALLER_{n}_CONTEXT_DOCUMENTS"] = str(documents_mode == "auto")
            vals[f"CALLER_{n}_CONTEXT_TOOLS"] = str(
                any(mode == "model" for mode in (documents_mode, browser_mode, github_mode, memory_mode))
            )
            vals[f"CALLER_{n}_CONTEXT_SCREENSHOT"] = str(blk["context_screenshot"].currentData())  # type: ignore
            vals[f"CALLER_{n}_INTENT_COUNT"]  = str(len(blk["intent_rows"]))
            for j, row in enumerate(blk["intent_rows"]):
                m = j + 1
                vals[f"CALLER_{n}_INTENT_{m}_KEY"]    = _get(row["key"])
                vals[f"CALLER_{n}_INTENT_{m}_LABEL"]  = _get(row["label"])
                vals[f"CALLER_{n}_INTENT_{m}_PROMPT"] = _get(row["prompt"])
        # The Chat model is combined with the Main LLM, so purge any stale
        # CHAT_LLM_* keys a previous version may have written.
        _write_env(
            vals,
            remove_keys=set(secret_store.API_KEY_NAMES)
            | {"CHAT_LLM_PROVIDER", "CHAT_LLM_MODEL", "CHAT_LLM_FALLBACKS"},
        )

        # Honor the user's choices, but warn if their model setup probably can't
        # serve them (screenshot → vision; tools → tool-calling provider).
        warnings: list[str] = []
        try:
            from core.llm_clients.client import (
                screenshot_capability_warnings,
                tool_capability_warnings,
                subscription_auth_warnings,
            )
            warnings += screenshot_capability_warnings(
                [blk["context_screenshot"].currentData() for blk in self._caller_blocks],  # type: ignore
                llm_provider=vals.get("LLM_PROVIDER", ""),
                llm_model=vals.get("LLM_MODEL", ""),
                vision_provider=vals.get("VISION_LLM_PROVIDER", ""),
                vision_model=vals.get("VISION_LLM_MODEL", ""),
            )
            warnings += subscription_auth_warnings(
                llm_provider=vals.get("LLM_PROVIDER", ""),
                vision_provider=vals.get("VISION_LLM_PROVIDER", ""),
            )
            live_tool_modes = []
            for blk in self._caller_blocks:
                live_tool_modes.extend(
                    [
                        str(blk["context_documents_mode"].currentData()),  # type: ignore[attr-defined]
                        str(blk["context_browser_mode"].currentData()),  # type: ignore[attr-defined]
                        str(blk["context_github_mode"].currentData()),  # type: ignore[attr-defined]
                        str(blk["context_memory_mode"].currentData()),  # type: ignore[attr-defined]
                    ]
                )
            warnings += tool_capability_warnings(
                any(mode == "model" for mode in live_tool_modes),
                llm_provider=vals.get("LLM_PROVIDER", ""),
            )
        except Exception:
            warnings = []
        if warnings:
            QMessageBox.warning(
                self,
                "Heads up",
                "Your settings were saved, but:\n\n• " + "\n\n• ".join(warnings),
            )
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
    "custom":     "CUSTOM_API_KEY",
    # ollama: no key
}

_MODEL_HINTS: dict[str, str] = {
    "groq":       "e.g. llama3-8b-8192",
    "openai":     "e.g. gpt-4o",
    "anthropic":  "e.g. claude-sonnet-4-5",
    "google":     "e.g. gemini-2.5-flash",
    "chatgpt":    "gpt-5.5  |  gpt-5.4  |  gpt-5.4-mini  |  gpt-5.3-codex",
    "copilot":    "e.g. gpt-4.1",
    "deepseek":   "e.g. deepseek-chat",
    "openrouter": "e.g. openai/gpt-4o",
    "mistral":    "e.g. mistral-large-latest",
    "xai":        "e.g. grok-3",
    "together":   "e.g. meta-llama/Llama-3-70b-chat-hf",
    "cerebras":   "e.g. llama-3.3-70b",
    "ollama":     "e.g. llama3  (model pulled locally)",
    "custom":     "model name for your custom endpoint",
}

_PROVIDER_MODELS: dict[str, list[str]] = {
    "groq": [
        "llama-3.3-70b-versatile",
        "llama3-70b-8192",
        "llama3-8b-8192",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ],
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "o3",
        "o4-mini",
    ],
    "anthropic": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
    ],
    "google": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
    "chatgpt": [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
    ],
    "copilot": [
        "gpt-4.1",
        "gpt-4o",
        "claude-sonnet-4-5",
        "gemini-2.5-pro",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-reasoner",
    ],
    "openrouter": [
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
        "mistral-small-latest",
        "mistral-medium-latest",
        "codestral-latest",
    ],
    "xai": [
        "grok-3",
        "grok-3-mini",
        "grok-2-latest",
    ],
    "together": [
        "meta-llama/Llama-3-70b-chat-hf",
        "meta-llama/Llama-3-8b-chat-hf",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ],
    "cerebras": [
        "llama-3.3-70b",
        "llama3.1-8b",
        "qwen-3-32b",
    ],
    "ollama": [
        "llama3",
        "llama3:70b",
        "mistral",
        "codellama",
        "phi3",
        "gemma2",
    ],
    "custom": [],
}

_PROVIDER_LABELS: dict[str, str] = {
    "groq":       "Groq",
    "openai":     "OpenAI",
    "anthropic":  "Anthropic",
    "google":     "Google AI Studio",
    "chatgpt":    "Codex (ChatGPT subscription)",
    "copilot":    "GitHub Copilot",
    "deepseek":   "DeepSeek",
    "openrouter": "OpenRouter",
    "mistral":    "Mistral",
    "xai":        "xAI (Grok)",
    "together":   "Together AI",
    "cerebras":   "Cerebras",
    "ollama":     "Ollama (local)",
    "custom":     "Custom (OpenAI-compatible)",
    "cartesia":   "Cartesia",
    "elevenlabs": "ElevenLabs",
    "none":       "None",
}


def _model_hint(provider: str) -> str:
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
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(
        "color: #e5e5ea; margin: 2px 0;" if visible else "color: rgba(0,0,0,0);"
    )
    return line


def _set(widget, value: str):
    if isinstance(widget, QComboBox):
        idx = widget.findData(value)
        if idx >= 0:
            widget.setCurrentIndex(idx)
        else:
            idx = widget.findText(value)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            elif widget.isEditable():
                widget.setCurrentText(value)
    elif isinstance(widget, QLineEdit):
        widget.setText(value)
    elif isinstance(widget, QTextEdit):
        widget.setPlainText(value)


def _get(widget) -> str:
    if isinstance(widget, QComboBox):
        data = widget.currentData()
        return data if data is not None else widget.currentText()
    elif isinstance(widget, QLineEdit):
        return widget.text()
    elif isinstance(widget, QTextEdit):
        return widget.toPlainText()
    return ""


def _desc_label(title: str, description: str) -> QLabel:
    lbl = QLabel(description)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")
    return lbl


def _link_label(text: str, url: str) -> QLabel:
    lbl = QLabel(f'<a href="{url}">{text}</a>')
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lbl.setToolTip(url)
    return lbl


def _parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    return parse_fallback_rows(raw)


def _short_test_error(message: str, route_name: str) -> str:
    """Trim the redundant '<route> test failed: ' prefix so each route line shows
    just the underlying reason (the provider/model is already on the line)."""
    prefix = f"{route_name} test failed: "
    text = message[len(prefix):] if message.startswith(prefix) else message
    return " ".join(text.split())


def open_settings(parent=None, on_apply=None):
    global _settings_dialog
    # Never parent the settings window to the floating icon overlay: that overlay
    # is a Qt.Tool window (an NSPanel on macOS, a no-taskbar tool window on
    # Windows), and attaching a normal child window to it crashes Cocoa on show()
    # and misbehaves on Windows. Only Linux keeps the parent. Elsewhere the dialog
    # is top-level and grabs focus itself via raise_()/activateWindow() below.
    dialog_parent = parent if sys.platform.startswith("linux") else None
    if _settings_dialog is None:
        _settings_dialog = SettingsDialog(dialog_parent, on_apply=on_apply)
    else:
        _settings_dialog._on_apply = on_apply
        _settings_dialog._env = _read_env()
        _settings_dialog._load_values()
        _settings_dialog._schedule_open_status_refresh()

    if _settings_dialog.isMinimized():
        _settings_dialog.showNormal()
    _settings_dialog.show()
    _settings_dialog.raise_()
    _settings_dialog.activateWindow()


