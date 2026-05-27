"""
ui/settings.py -” Settings dialog.

A plain GUI for editing all user-configurable values.
Reads from and writes to the .env file.
Launch via tray icon â†’ Settings, or call open_settings().
"""
from __future__ import annotations
import os
import threading
from contextlib import contextmanager
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox,
    QPushButton, QTabWidget, QWidget, QFrame, QGroupBox, QMessageBox,
    QScrollArea, QSizePolicy, QCompleter,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from core import secret_store
import ui.settings_panel.env as settings_env
from ui.settings_panel.hotkey_capture import HotkeyCaptureEdit
from ui.settings_panel.helpers import parse_fallback_rows
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

ENV_PATH = settings_env.ENV_PATH
_settings_dialog: "SettingsDialog | None" = None


class _NoScrollCombo(QComboBox):
    """QComboBox that ignores wheel events unless it already has focus."""
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


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
        self._pending_test_results: list[tuple[str, int, bool, str]] = []
        self._pending_test_results_lock = threading.Lock()
        self._running_test_tokens: set[tuple[str, int]] = set()
        self._latest_test_token: dict[str, int] = {}
        self._memory_panel = None
        self._test_result_timer = QTimer(self)
        self._test_result_timer.setInterval(100)
        self._test_result_timer.timeout.connect(self._drain_test_results)
        self._build_ui()
        self._load_values()
        fit_window_to_screen(self, preferred_width=620, preferred_height=620)

    def _save_api_keys_to_keychain(self) -> bool:
        labels = {
            "GROQ_API_KEY": "Groq",
            "OPENAI_API_KEY": "OpenAI",
            "ANTHROPIC_API_KEY": "Anthropic",
            "GOOGLE_API_KEY": "Google AI Studio",
            "CARTESIA_API_KEY": "Cartesia",
            "ELEVENLABS_API_KEY": "ElevenLabs",
            "CUSTOM_API_KEY": "Custom provider",
            "DEEPSEEK_API_KEY": "DeepSeek",
            "OPENROUTER_API_KEY": "OpenRouter",
            "MISTRAL_API_KEY": "Mistral",
            "XAI_API_KEY": "xAI (Grok)",
            "TOGETHER_API_KEY": "Together AI",
            "CEREBRAS_API_KEY": "Cerebras",
        }
        try:
            secret_store.migrate_env_secrets(self._env)
            for name in secret_store.API_KEY_NAMES:
                value = _get(self._fields[name]).strip()
                if value:
                    secret_store.set_secret(name, value)
                    self._fields[name].clear()  # type: ignore[attr-defined]
                    self._fields[name].setPlaceholderText(f"{labels[name]} key stored in OS keychain")  # type: ignore[attr-defined]
            return True
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Keychain error",
                f"Could not save API keys to the OS keychain:\n{exc}",
            )
            return False

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

    _LIGHT_STYLE = """
        QDialog { background: #f2f2f7; }
        QTabWidget::pane { border: none; background: transparent; }
        QTabBar { background: transparent; border: none; }
        QTabBar::tab {
            color: #636366; padding: 6px 14px; border-radius: 8px;
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
            padding: 5px 10px; font-size: 10pt; color: #1c1c1e;
        }
        QLineEdit:focus { border-color: #5856d6; }
        QComboBox {
            background: white; border: 1px solid #d1d1d6; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: #1c1c1e; min-height: 22px;
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
            color: #9d9daa; padding: 6px 14px; border-radius: 8px;
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
            padding: 5px 10px; font-size: 10pt; color: #e8e8f0;
        }
        QLineEdit:focus { border-color: #8b87ff; }
        QComboBox {
            background: #17181d; border: 1px solid #454854; border-radius: 8px;
            padding: 5px 10px; font-size: 10pt; color: #e8e8f0; min-height: 22px;
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
        tabs.addTab(self._tab_llm(),       "LLM")
        tabs.addTab(self._tab_tts(),       "TTS / Voice")
        tabs.addTab(self._tab_prompt(),    "Prompts")
        tabs.addTab(self._tab_keybinds(),  "Keybinds")
        tabs.addTab(self._tab_app(),       "App")
        tabs.addTab(self._tab_memory(),    "Memory")
        root.addWidget(tabs)

        # Buttons
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color: #80c080; font-size: 9pt;")
        btn_row = QHBoxLayout()
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

        # ── Field creation ────────────────────────────────────────────────
        self._fields["LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic", "google", "chatgpt", "copilot",
             "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "ollama",
             "custom"]
        )
        self._fields["LLM_MODEL"] = self._model_combo()
        self._fallback_rows: dict[str, list[dict]] = {
            "LLM_FALLBACKS": [],
            "CHAT_LLM_FALLBACKS": [],
            "VISION_LLM_FALLBACKS": [],
        }
        self._fields["CHAT_LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic", "google", "chatgpt", "copilot",
             "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "ollama",
             "custom"]
        )
        self._fields["CHAT_LLM_MODEL"] = self._model_combo()
        self._fields["VISION_LLM_PROVIDER"] = self._combo(
            ["", "anthropic", "openai", "google", "chatgpt",
             "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "ollama",
             "custom"]
        )
        self._fields["VISION_LLM_MODEL"] = self._model_combo()

        self._fields["GROQ_API_KEY"] = self._password()
        self._fields["OPENAI_API_KEY"] = self._password()
        self._fields["ANTHROPIC_API_KEY"] = self._password()
        self._fields["GOOGLE_API_KEY"] = self._password()
        self._fields["GROQ_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["OPENAI_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["ANTHROPIC_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["GOOGLE_API_KEY"].setPlaceholderText("Stored in OS keychain")

        for name, _label, _url in [
            ("DEEPSEEK_API_KEY",   "DeepSeek",    "https://platform.deepseek.com/api_keys"),
            ("OPENROUTER_API_KEY", "OpenRouter",  "https://openrouter.ai/settings/keys"),
            ("MISTRAL_API_KEY",    "Mistral",     "https://console.mistral.ai/api-keys"),
            ("XAI_API_KEY",        "xAI (Grok)",  "https://console.x.ai"),
            ("TOGETHER_API_KEY",   "Together AI", "https://api.together.xyz/settings/api-keys"),
            ("CEREBRAS_API_KEY",   "Cerebras",    "https://cloud.cerebras.ai"),
        ]:
            field = self._password()
            field.setPlaceholderText("Stored in OS keychain")
            self._fields[name] = field

        def _update_model_placeholders():
            p = _get(self._fields["LLM_PROVIDER"])
            _refresh_model_combo(self._fields["LLM_MODEL"], p)
            self._fields["LLM_MODEL"].lineEdit().setPlaceholderText(_model_hint(p))
            cp = _get(self._fields["CHAT_LLM_PROVIDER"])
            _refresh_model_combo(self._fields["CHAT_LLM_MODEL"], cp)
            chint = _model_hint(cp) if cp else "Leave blank to use same model as above"
            self._fields["CHAT_LLM_MODEL"].lineEdit().setPlaceholderText(chint)

        def _update_vision_model():
            vp = _get(self._fields["VISION_LLM_PROVIDER"])
            _refresh_model_combo(self._fields["VISION_LLM_MODEL"], vp)
            self._fields["VISION_LLM_MODEL"].lineEdit().setPlaceholderText(_model_hint(vp))

        self._fields["LLM_PROVIDER"].currentTextChanged.connect(lambda _: _update_model_placeholders())
        self._fields["CHAT_LLM_PROVIDER"].currentTextChanged.connect(lambda _: _update_model_placeholders())
        self._fields["VISION_LLM_PROVIDER"].currentIndexChanged.connect(lambda _: _update_vision_model())

        # ── MODEL card ────────────────────────────────────────────────────
        self._llm_test_status_lbl = QLabel()
        model_card, model_cv = self._card("Model")
        model_cv.addWidget(self._provider_model_row(
            self._fields["LLM_PROVIDER"], self._fields["LLM_MODEL"],
            self._llm_test_status_lbl, self._test_primary_llm_connection,
            "LLM_FALLBACKS", "Fallback",
            test_btn_label="Test LLM",
        ))
        outer.addWidget(model_card)

        # ── CHAT MODEL card ───────────────────────────────────────────────
        self._chat_llm_test_status_lbl = QLabel()
        chat_card, chat_cv = self._card("Chat model")
        chat_desc = QLabel("<small>Used for the chat window and the Elaborate action.</small>")
        chat_desc.setWordWrap(True)
        chat_cv.addWidget(chat_desc)
        chat_cv.addWidget(self._provider_model_row(
            self._fields["CHAT_LLM_PROVIDER"], self._fields["CHAT_LLM_MODEL"],
            self._chat_llm_test_status_lbl, self._test_chat_llm_connection,
            "CHAT_LLM_FALLBACKS", "Chat fallback",
            test_btn_label="Test chat",
        ))
        outer.addWidget(chat_card)

        # ── VISION MODEL card ─────────────────────────────────────────────
        self._vision_test_status_lbl = QLabel()
        vision_card, vision_cv = self._card("Vision model")
        vision_desc = QLabel("<small>Used when you draw a screen snip with Ctrl+Alt+Q.</small>")
        vision_desc.setWordWrap(True)
        vision_cv.addWidget(vision_desc)
        vision_cv.addWidget(self._provider_model_row(
            self._fields["VISION_LLM_PROVIDER"], self._fields["VISION_LLM_MODEL"],
            self._vision_test_status_lbl, self._test_vision_connection,
            "VISION_LLM_FALLBACKS", "Vision fallback",
            fallback_providers=["", "anthropic", "openai", "google", "chatgpt",
                       "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "ollama",
                       "custom"],
            test_btn_label="Test vision",
        ))
        outer.addWidget(vision_card)

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
            "Select <b>custom</b> in any provider dropdown above to route requests here.</small>"
        )
        custom_note.setWordWrap(True)
        custom_cv.addWidget(custom_note)

        presets_btn = QPushButton("Presets ▾")
        presets_btn.setFixedWidth(90)
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

        # ── AUTHENTICATION card ───────────────────────────────────────────
        auth_card, auth_cv = self._card("Authentication")

        # ChatGPT
        chatgpt_hdr = QLabel("ChatGPT Pro / Plus")
        chatgpt_hdr.setStyleSheet("font-weight: 600; color: #1c1c1e;")
        auth_cv.addWidget(chatgpt_hdr)
        self._chatgpt_status_lbl = QLabel()
        self._chatgpt_status_lbl.setWordWrap(True)
        self._refresh_chatgpt_status()
        auth_cv.addWidget(self._chatgpt_status_lbl)
        cgpt_row = self._button_row(
            ("Sign in (browser)",   self._chatgpt_login_browser),
            ("Sign in (headless)",  self._chatgpt_login_device),
            ("Sign out",            self._chatgpt_logout),
        )
        # Keep refs for external callers that may reference these buttons
        btns = cgpt_row.findChildren(QPushButton)
        self._cgpt_login_btn, self._cgpt_device_btn, self._cgpt_logout_btn = btns[0], btns[1], btns[2]
        auth_cv.addWidget(cgpt_row)

        auth_cv.addWidget(_sep(visible=True))

        # GitHub
        github_hdr = QLabel("GitHub OAuth")
        github_hdr.setStyleSheet("font-weight: 600; color: #1c1c1e;")
        auth_cv.addWidget(github_hdr)
        auth_cv.addWidget(_desc_label("", "Sign in opens GitHub in your browser and links this app to your account."))
        self._fields["GITHUB_CLIENT_ID"] = QLineEdit()
        self._fields["GITHUB_CLIENT_ID"].setPlaceholderText("Developer OAuth app client ID override")
        self._fields["GITHUB_OAUTH_SCOPES"] = QLineEdit()
        self._fields["GITHUB_OAUTH_SCOPES"].setPlaceholderText("e.g. repo read:user user:email")
        self._github_status_lbl = QLabel()
        self._github_status_lbl.setWordWrap(True)
        self._refresh_github_status()
        auth_cv.addWidget(self._github_status_lbl)
        github_row = self._button_row(
            ("Sign in with GitHub", self._github_login_device),
            ("Sign out",            self._github_logout),
        )
        gh_btns = github_row.findChildren(QPushButton)
        self._github_login_btn, self._github_logout_btn = gh_btns[0], gh_btns[1]
        auth_cv.addWidget(github_row)

        auth_cv.addWidget(_sep(visible=True))

        # GitHub Copilot
        copilot_hdr = QLabel("GitHub Copilot")
        copilot_hdr.setStyleSheet("font-weight: 600; color: #1c1c1e;")
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
        self._refresh_copilot_status()
        auth_cv.addWidget(self._copilot_status_lbl)
        copilot_row = self._button_row(
            ("Save token",       self._copilot_save_token),
            ("Test token / SDK", self._copilot_test_token),
            ("Clear token",      self._copilot_clear_token),
        )
        cp_btns = copilot_row.findChildren(QPushButton)
        self._copilot_save_btn, self._copilot_test_btn, self._copilot_clear_btn = cp_btns[0], cp_btns[1], cp_btns[2]
        auth_cv.addWidget(copilot_row)
        outer.addWidget(auth_card)

        # ── API KEYS card (bottom) ────────────────────────────────────────
        self._extra_keys_widget = QWidget()
        extra_keys_form = QFormLayout(self._extra_keys_widget)
        extra_keys_form.setContentsMargins(0, 4, 0, 0)
        extra_keys_form.setSpacing(8)
        for name, label, url in [
            ("DEEPSEEK_API_KEY",   "DeepSeek API key",    "https://platform.deepseek.com/api_keys"),
            ("OPENROUTER_API_KEY", "OpenRouter API key",  "https://openrouter.ai/settings/keys"),
            ("MISTRAL_API_KEY",    "Mistral API key",     "https://console.mistral.ai/api-keys"),
            ("XAI_API_KEY",        "xAI (Grok) API key",  "https://console.x.ai"),
            ("TOGETHER_API_KEY",   "Together AI API key", "https://api.together.xyz/settings/api-keys"),
            ("CEREBRAS_API_KEY",   "Cerebras API key",    "https://cloud.cerebras.ai"),
        ]:
            extra_keys_form.addRow(_link_label(label, url), self._fields[name])
        extra_keys_form.addRow("", QLabel("<small><i>Ollama</i> runs locally — no API key needed.</small>"))
        self._extra_keys_widget.setVisible(False)

        self._extra_keys_toggle = QPushButton("▶  More provider keys (DeepSeek, OpenRouter, Mistral, xAI…)")
        self._extra_keys_toggle.setFlat(True)
        self._extra_keys_toggle.setStyleSheet("text-align: left; font-weight: bold;")
        self._extra_keys_toggle.clicked.connect(self._toggle_extra_keys)

        keys_card, keys_cv = self._card("API Keys")
        provider_note = QLabel("<small><i>openai</i> / <i>google</i> = pay-per-token &nbsp;·&nbsp; <i>chatgpt</i> = your Pro/Plus subscription</small>")
        provider_note.setWordWrap(True)
        keys_cv.addWidget(provider_note)

        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.addWidget(_link_label("Groq", "https://console.groq.com/keys"), 0, 0)
        grid.addWidget(_link_label("OpenAI", "https://platform.openai.com/api-keys"), 0, 1)
        grid.addWidget(self._fields["GROQ_API_KEY"], 1, 0)
        grid.addWidget(self._fields["OPENAI_API_KEY"], 1, 1)
        grid.addWidget(_link_label("Anthropic", "https://console.anthropic.com/settings/keys"), 2, 0)
        grid.addWidget(_link_label("Google AI Studio", "https://aistudio.google.com/apikey"), 2, 1)
        grid.addWidget(self._fields["ANTHROPIC_API_KEY"], 3, 0)
        grid.addWidget(self._fields["GOOGLE_API_KEY"], 3, 1)
        keys_cv.addWidget(grid_w)
        keys_cv.addWidget(self._extra_keys_toggle)
        keys_cv.addWidget(self._extra_keys_widget)
        outer.addWidget(keys_card)

        outer.addStretch()
        scroll.setWidget(w)
        return scroll

    def _toggle_extra_keys(self) -> None:
        visible = not self._extra_keys_widget.isVisible()
        self._extra_keys_widget.setVisible(visible)
        self._extra_keys_toggle.setText(
            ("▼" if visible else "▶") +
            "  More provider keys (DeepSeek, OpenRouter, Mistral, xAI…)"
        )

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
        # Update model placeholder for all custom-selected combos
        for key in ("LLM_MODEL", "CHAT_LLM_MODEL", "VISION_LLM_MODEL"):
            if key in self._fields:
                combo = self._fields[key]
                provider_key = key.replace("_MODEL", "_PROVIDER")
                if provider_key in self._fields and _get(self._fields[provider_key]) == "custom":
                    combo.lineEdit().setPlaceholderText(f"e.g. {model_hint}")

    def _test_custom_connection(self) -> None:
        from core.llm_clients import client as llm

        provider = "custom"
        model = _get(self._fields.get("LLM_MODEL", QLineEdit())).strip()
        custom_api_key = self._effective_secret_value("CUSTOM_API_KEY")
        custom_base_url = _get(self._fields["CUSTOM_BASE_URL"]).strip()

        if not model:
            self._set_test_status(self._custom_test_status_lbl, False, "Enter a model name in the LLM Model field first.")
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
                self._chatgpt_status_lbl.setStyleSheet("color: palette(mid);")
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
            if msg.startswith("__device_code__"):
                # Device code info -” show it without stopping the poll
                body = msg[len("__device_code__"):]
                url, _, code = body.partition("\n")
                self._chatgpt_status_lbl.setText(f"Go to: {url}\nEnter code: {code}")
                self._chatgpt_status_lbl.setStyleSheet("color: #80a0ff;")
                return
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

    def _chatgpt_login_device(self) -> None:
        from core.auth import chatgpt as chatgpt_auth
        self._chatgpt_status_lbl.setText("Starting device auth...")
        self._chatgpt_status_lbl.setStyleSheet("color: #c0c040;")
        self._start_auth_poll()

        def on_code(url, user_code):
            self._auth_poll_error = f"__device_code__{url}\n{user_code}"

        def on_success(_tokens):
            pass  # polling timer will detect the saved tokens

        def on_error(msg):
            self._auth_poll_error = msg

        chatgpt_auth.start_device_login(on_code, on_success, on_error)

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
                self._github_status_lbl.setStyleSheet("color: palette(mid);")
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
                "color: #80c080;" if stored else "color: palette(mid);"
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
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

        self._fields["TTS_PROVIDER"] = self._combo(
            ["cartesia", "elevenlabs", "none"]
        )
        self._fields["CARTESIA_API_KEY"] = self._password()
        self._fields["CARTESIA_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["CARTESIA_VOICE_ID"] = QLineEdit()
        self._fields["CARTESIA_VOICE_ID"].setPlaceholderText("e.g. a0e99841-438c-4a64-b679-ae501e7d6091")
        self._fields["ELEVENLABS_API_KEY"] = self._password()
        self._fields["ELEVENLABS_API_KEY"].setPlaceholderText("Stored in OS keychain")

        f.addRow("Provider", self._fields["TTS_PROVIDER"])
        f.addRow(_sep(), _sep())
        tts_key_note = QLabel("<small>API keys are saved to the OS keychain. Leave blank to keep the stored key.</small>")
        tts_key_note.setWordWrap(True)
        f.addRow("", tts_key_note)
        f.addRow(_link_label("Cartesia API key", "https://play.cartesia.ai/keys"), self._fields["CARTESIA_API_KEY"])
        f.addRow("Cartesia Voice ID", self._fields["CARTESIA_VOICE_ID"])
        f.addRow(_sep(), _sep())
        f.addRow(_link_label("ElevenLabs API key", "https://elevenlabs.io/app/settings/api-keys"), self._fields["ELEVENLABS_API_KEY"])
        self._tts_test_status_lbl = QLabel()
        self._tts_test_status_lbl.setWordWrap(True)
        f.addRow("", self._button_row(("Test TTS", self._test_tts_connection)))
        f.addRow("", self._tts_test_status_lbl)
        return w

    def _tab_prompt(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        layout.addWidget(QLabel("System prompt:"))
        util = QTextEdit()
        util.setMinimumHeight(260)
        util.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._fields["SYSTEM_PROMPT_UTILITY"] = util
        layout.addWidget(util, stretch=1)
        return w

    def _tab_keybinds(self) -> QWidget:
        from PySide6.QtWidgets import QScrollArea, QSizePolicy
        container = QWidget()
        self._keybinds_layout = QVBoxLayout(container)
        self._keybinds_layout.setSpacing(6)
        self._keybinds_layout.setContentsMargins(12, 12, 12, 12)

        # Caller hotkeys section
        self._keybinds_layout.addWidget(QLabel("<b>Caller Hotkeys</b>"))

        limits_frame = QFrame()
        limits_frame.setFrameShape(QFrame.Shape.StyledPanel)
        limits_layout = QFormLayout(limits_frame)
        limits_layout.setContentsMargins(8, 6, 8, 6)
        limits_layout.setSpacing(6)
        self._fields["CONTEXT_BROWSER_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"] = QLineEdit()
        self._fields["TOOL_PLUGIN_DIR"] = QLineEdit()
        limits_layout.addRow("Browser fetch chars", self._fields["CONTEXT_BROWSER_MAX_CHARS"])
        limits_layout.addRow("Auto document chars", self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"])
        limits_layout.addRow("Tool document chars", self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"])
        limits_layout.addRow("Tool plugin folder", self._fields["TOOL_PLUGIN_DIR"])
        self._keybinds_layout.addWidget(limits_frame)

        self._callers_container = QWidget()
        self._callers_vlayout = QVBoxLayout(self._callers_container)
        self._callers_vlayout.setSpacing(8)
        self._callers_vlayout.setContentsMargins(0, 0, 0, 0)
        self._keybinds_layout.addWidget(self._callers_container)
        self._caller_blocks: list[dict] = []

        add_caller_btn = QPushButton("+ Add Caller Hotkey")
        add_caller_btn.setFixedWidth(160)
        add_caller_btn.clicked.connect(lambda: self._add_caller_block())
        btn_wrap = QHBoxLayout()
        btn_wrap.setContentsMargins(0, 4, 0, 4)
        btn_wrap.addWidget(add_caller_btn)
        btn_wrap.addStretch()
        self._keybinds_layout.addLayout(btn_wrap)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(128,128,128,80); margin: 4px 0px;")
        self._keybinds_layout.addWidget(sep)

        # Other (non-caller) hotkeys
        self._keybinds_layout.addWidget(QLabel("<b>Other Hotkeys</b>"))
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

        self._keybinds_layout.addStretch()

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
        lbl.setStyleSheet("font-style: italic; color: palette(mid);")
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
        context_tools: bool = True,
        context_screenshot: bool = False,
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
        del_caller_btn.setFixedWidth(80)
        hdr_h.addWidget(del_caller_btn)
        outer.addWidget(hdr)

        context_row = QWidget()
        context_h = QHBoxLayout(context_row)
        context_h.setContentsMargins(0, 0, 0, 0)
        context_h.setSpacing(10)
        ambient_cb = QCheckBox("Ambient")
        ambient_cb.setChecked(context_ambient)
        docs_cb = QCheckBox("Open docs")
        docs_cb.setChecked(context_documents)
        tools_cb = QCheckBox("Tools")
        tools_cb.setChecked(context_tools)
        screenshot_cb = QCheckBox("Auto screenshot")
        screenshot_cb.setChecked(context_screenshot)
        context_h.addWidget(QLabel("Context:"))
        context_h.addWidget(ambient_cb)
        context_h.addWidget(docs_cb)
        context_h.addWidget(tools_cb)
        context_h.addWidget(screenshot_cb)
        context_h.addStretch()
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
            "context_documents": docs_cb,
            "context_tools": tools_cb,
            "context_screenshot": screenshot_cb,
            "intents_layout": intents_vlayout,
            "intent_rows":    [],
        }

        for r in (intents or []):
            self._add_caller_intent_row(blk, r.get("key", ""), r.get("label", ""), r.get("prompt", ""))

        # Add-row button
        add_row_btn = QPushButton("+ Add row")
        add_row_btn.setFixedWidth(80)
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
        del_btn.setFixedWidth(28)
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
        from PySide6.QtWidgets import QGroupBox, QSizePolicy

        w = QWidget()
        root = QVBoxLayout(w)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # --- Config group ---
        cfg_group = QGroupBox("Memory LLM & Settings")
        f = QFormLayout(cfg_group)
        f.setSpacing(8)
        f.setContentsMargins(8, 8, 8, 8)

        mem_provider = self._combo(
            ["groq", "openai", "anthropic", "google",
             "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "ollama",
             "custom"],
            self._env.get("MEMORY_LLM_PROVIDER", ""),
        )
        self._fields["MEMORY_LLM_PROVIDER"] = mem_provider
        f.addRow("Memory LLM provider:", mem_provider)

        mem_model = self._model_combo()
        self._fields["MEMORY_LLM_MODEL"] = mem_model
        mem_provider.currentIndexChanged.connect(
            lambda _: _refresh_model_combo(mem_model, _get(mem_provider))
        )
        f.addRow("Memory LLM model:", mem_model)

        self._memory_test_status_lbl = QLabel()
        self._memory_test_status_lbl.setWordWrap(True)
        f.addRow("", self._button_row(("Test Memory LLM", self._test_memory_connection)))
        f.addRow("", self._memory_test_status_lbl)

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
        f.addRow("", self._button_row(("Clean up low-value stored facts", self._cleanup_memory)))

        root.addWidget(cfg_group)

        # --- Fact browser ---
        browser_group = QGroupBox("Stored Facts")
        browser_layout = QVBoxLayout(browser_group)
        browser_layout.setContentsMargins(6, 6, 6, 6)

        try:
            from core.memory_store.store import get_manager
            from ui.memory_viewer import MemoryPanel
            panel = MemoryPanel(get_manager(), browser_group)
            self._memory_panel = panel
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            browser_layout.addWidget(panel)
        except Exception as exc:
            from PySide6.QtCore import Qt
            err = QLabel(f"Memory store unavailable:\n{exc}")
            err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err.setStyleSheet("color: #c00;")
            browser_layout.addWidget(err)

        root.addWidget(browser_group, stretch=1)

        return w

    def _cleanup_memory(self) -> None:
        try:
            from core.memory_store.store import get_manager
            count = get_manager().prune_low_value_facts()
            panel = getattr(self, "_memory_panel", None)
            if panel is not None:
                panel._load_facts()
            QMessageBox.information(
                self,
                "Memory cleanup",
                f"Archived {count} low-value fact(s).",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Memory cleanup failed", str(exc))

    def _tab_app(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

        theme_combo = _NoScrollCombo()
        theme_combo.addItem("System default", "system")
        theme_combo.addItem("Light", "light")
        theme_combo.addItem("Dark", "dark")
        self._fields["THEME_MODE"] = theme_combo
        self._fields["DOLL_AUTO_HIDE"] = QCheckBox("Auto-hide doll (only visible when active)")
        self._fields["CHAT_AUTO_ELABORATE"] = QCheckBox("Auto-elaborate when opening chat")
        self._fields["CHAT_ELABORATE_PROMPT"] = QLineEdit()
        self._fields["CHAT_ELABORATE_PROMPT"].setPlaceholderText("e.g. Please elaborate on that.")

        self._fields["DOLL_SIZE"] = QLineEdit()
        self._fields["DOLL_SIZE"].setPlaceholderText("e.g. 80")
        self._fields["BUBBLE_WIDTH"] = QLineEdit()
        self._fields["BUBBLE_WIDTH"].setPlaceholderText("e.g. 340")
        self._fields["BUBBLE_LINES"] = QLineEdit()
        self._fields["BUBBLE_LINES"].setPlaceholderText("e.g. 2")
        self._fields["BUBBLE_COLOR"] = QLineEdit()
        self._fields["BUBBLE_COLOR"].setPlaceholderText("e.g. #1c1c24dc")
        self._fields["BUBBLE_TEXT_COLOR"] = QLineEdit()
        self._fields["BUBBLE_TEXT_COLOR"].setPlaceholderText("e.g. #e6e6e6")
        self._fields["BUBBLE_READ_WORD_COLOR"] = QLineEdit()
        self._fields["BUBBLE_READ_WORD_COLOR"].setPlaceholderText("e.g. #4da3ff")
        self._fields["BUBBLE_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_REVEAL_WPM"].setPlaceholderText("e.g. 170")
        self._fields["BUBBLE_HOLD_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_HOLD_REVEAL_WPM"].setPlaceholderText("e.g. 480")
        self._fields["TTS_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.0")
        self._fields["TTS_HOLD_PLAYBACK_RATE"] = QLineEdit()
        self._fields["TTS_HOLD_PLAYBACK_RATE"].setPlaceholderText("e.g. 1.35")

        f.addRow("Theme", self._fields["THEME_MODE"])
        f.addRow("", self._fields["DOLL_AUTO_HIDE"])
        f.addRow("", self._fields["CHAT_AUTO_ELABORATE"])
        f.addRow("Elaborate prompt", self._fields["CHAT_ELABORATE_PROMPT"])
        f.addRow(_sep(), _sep())
        f.addRow("Doll icon size (px)", self._fields["DOLL_SIZE"])
        f.addRow("Bubble width (px)", self._fields["BUBBLE_WIDTH"])
        f.addRow("Bubble lines", self._fields["BUBBLE_LINES"])
        f.addRow("Bubble color", self._fields["BUBBLE_COLOR"])
        f.addRow("Bubble text color", self._fields["BUBBLE_TEXT_COLOR"])
        f.addRow("Read word color", self._fields["BUBBLE_READ_WORD_COLOR"])
        f.addRow("Bubble text speed (WPM)", self._fields["BUBBLE_REVEAL_WPM"])
        f.addRow("Bubble hold speed (WPM)", self._fields["BUBBLE_HOLD_REVEAL_WPM"])
        f.addRow("TTS speed", self._fields["TTS_PLAYBACK_RATE"])
        f.addRow("TTS hold speed", self._fields["TTS_HOLD_PLAYBACK_RATE"])
        return w

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
        add_btn.setFixedWidth(120)
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
        remove_btn.setFixedWidth(70)
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

    def _load_values(self):
        import config as cfg

        _set(self._fields["LLM_PROVIDER"], self._env.get("LLM_PROVIDER", cfg.LLM_PROVIDER))
        _set(self._fields["LLM_MODEL"], self._env.get("LLM_MODEL", cfg.LLM_MODEL))
        self._set_fallback_rows("LLM_FALLBACKS", self._env.get("LLM_FALLBACKS", cfg.LLM_FALLBACKS))
        _set(self._fields["CHAT_LLM_PROVIDER"], self._env.get("CHAT_LLM_PROVIDER", cfg.CHAT_LLM_PROVIDER))
        _set(self._fields["CHAT_LLM_MODEL"], self._env.get("CHAT_LLM_MODEL", cfg.CHAT_LLM_MODEL))
        self._set_fallback_rows("CHAT_LLM_FALLBACKS", self._env.get("CHAT_LLM_FALLBACKS", cfg.CHAT_LLM_FALLBACKS))
        _set(self._fields["TTS_PROVIDER"], self._env.get("TTS_PROVIDER", cfg.TTS_PROVIDER))
        _set(self._fields["CARTESIA_VOICE_ID"], self._env.get("CARTESIA_VOICE_ID", ""))
        for name in secret_store.API_KEY_NAMES:
            self._fields[name].clear()  # type: ignore[attr-defined]
            status = "stored in OS keychain" if secret_store.get_keychain_secret(name) else "not configured"
            self._fields[name].setPlaceholderText(status)  # type: ignore[attr-defined]
        _set(self._fields["HOTKEY_ADD_CONTEXT"],   self._env.get("HOTKEY_ADD_CONTEXT",   cfg.HOTKEY_ADD_CONTEXT))
        _set(self._fields["HOTKEY_CLEAR_CONTEXT"], self._env.get("HOTKEY_CLEAR_CONTEXT", cfg.HOTKEY_CLEAR_CONTEXT))
        _set(self._fields["HOTKEY_SNIP"],          self._env.get("HOTKEY_SNIP",          cfg.HOTKEY_SNIP))
        self._fields["SNIP_CONTEXT_AMBIENT"].setChecked(self._env.get("SNIP_CONTEXT_AMBIENT", str(cfg.SNIP_CONTEXT_AMBIENT)).lower() == "true")  # type: ignore
        self._fields["SNIP_CONTEXT_DOCUMENTS"].setChecked(self._env.get("SNIP_CONTEXT_DOCUMENTS", str(cfg.SNIP_CONTEXT_DOCUMENTS)).lower() == "true")  # type: ignore
        self._fields["SNIP_CONTEXT_TOOLS"].setChecked(self._env.get("SNIP_CONTEXT_TOOLS", str(cfg.SNIP_CONTEXT_TOOLS)).lower() == "true")  # type: ignore
        _set(self._fields["VISION_LLM_PROVIDER"],  self._env.get("VISION_LLM_PROVIDER",  cfg.VISION_LLM_PROVIDER))
        _set(self._fields["VISION_LLM_MODEL"],     self._env.get("VISION_LLM_MODEL",     cfg.VISION_LLM_MODEL))
        self._set_fallback_rows("VISION_LLM_FALLBACKS", self._env.get("VISION_LLM_FALLBACKS", cfg.VISION_LLM_FALLBACKS))
        _set(self._fields["CUSTOM_BASE_URL"],      self._env.get("CUSTOM_BASE_URL",      cfg.CUSTOM_BASE_URL))
        _set(self._fields["GITHUB_CLIENT_ID"],     self._env.get("GITHUB_CLIENT_ID",     cfg.GITHUB_CLIENT_ID))
        _set(self._fields["GITHUB_OAUTH_SCOPES"],  self._env.get("GITHUB_OAUTH_SCOPES",  cfg.GITHUB_OAUTH_SCOPES))
        _set(self._fields["CONTEXT_BROWSER_MAX_CHARS"], self._env.get("CONTEXT_BROWSER_MAX_CHARS", str(cfg.CONTEXT_BROWSER_MAX_CHARS)))
        _set(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS)))
        _set(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_TOOL_DOCUMENT_MAX_CHARS)))
        _set(self._fields["TOOL_PLUGIN_DIR"], self._env.get("TOOL_PLUGIN_DIR", cfg.TOOL_PLUGIN_DIR))

        _set(self._fields["MEMORY_LLM_PROVIDER"],    self._env.get("MEMORY_LLM_PROVIDER",    cfg.MEMORY_LLM_PROVIDER))
        _set(self._fields["MEMORY_LLM_MODEL"],       self._env.get("MEMORY_LLM_MODEL",       cfg.MEMORY_LLM_MODEL))
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
            self._add_caller_block(
                hotkey     = self._env.get(f"CALLER_{n}_HOTKEY",     cr.get("hotkey", "")),
                label      = self._env.get(f"CALLER_{n}_LABEL",      cr.get("label", "")),
                paste_back = self._env.get(f"CALLER_{n}_PASTE_BACK", str(cr.get("paste_back", False))).lower() == "true",
                custom_key = self._env.get(f"CALLER_{n}_CUSTOM_KEY", cr.get("custom_key", "s")),
                context_ambient = self._env.get(f"CALLER_{n}_CONTEXT_AMBIENT", str(cr.get("context_ambient", True))).lower() == "true",
                context_documents = self._env.get(f"CALLER_{n}_CONTEXT_DOCUMENTS", str(cr.get("context_documents", True))).lower() == "true",
                context_tools = self._env.get(f"CALLER_{n}_CONTEXT_TOOLS", str(cr.get("context_tools", True))).lower() == "true",
                context_screenshot = self._env.get(f"CALLER_{n}_CONTEXT_SCREENSHOT", str(cr.get("context_screenshot", False))).lower() == "true",
                intents    = intents,
            )

        auto_hide = self._env.get("DOLL_AUTO_HIDE", str(cfg.DOLL_AUTO_HIDE)).lower() == "true"
        theme_mode = self._env.get("THEME_MODE", getattr(cfg, "THEME_MODE", "system"))
        combo = self._fields["THEME_MODE"]
        idx = combo.findData(theme_mode)  # type: ignore[attr-defined]
        combo.setCurrentIndex(idx if idx >= 0 else 0)  # type: ignore[attr-defined]
        self._fields["DOLL_AUTO_HIDE"].setChecked(auto_hide)  # type: ignore

        auto_elab = self._env.get("CHAT_AUTO_ELABORATE", str(cfg.CHAT_AUTO_ELABORATE)).lower() == "true"
        self._fields["CHAT_AUTO_ELABORATE"].setChecked(auto_elab)  # type: ignore
        _set(self._fields["CHAT_ELABORATE_PROMPT"],
             self._env.get("CHAT_ELABORATE_PROMPT", cfg.CHAT_ELABORATE_PROMPT))

        _set(self._fields["DOLL_SIZE"],    self._env.get("DOLL_SIZE",    str(cfg.DOLL_SIZE)))
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

    def _set_test_status(self, label: QLabel, ok: bool, message: str) -> None:
        label.setText(message)
        label.setStyleSheet("color: #80c080;" if ok else "color: #c04040;")

    def _set_test_pending(self, label: QLabel, message: str = "Testing...") -> None:
        label.setText(message)
        label.setStyleSheet("color: #c0c040;")

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

    def _test_llm_route(self, *, provider_key: str, model_key: str, route_name: str, status_label: QLabel, image: bool = False) -> None:
        from core.llm_clients import client as llm

        provider = _get(self._fields[provider_key]).strip().lower()
        model = _get(self._fields[model_key]).strip()
        anthropic_api_key = self._effective_secret_value("ANTHROPIC_API_KEY")
        custom_base_url = _get(self._fields["CUSTOM_BASE_URL"]).strip()
        compat_keys = {
            p: self._effective_secret_value(k)
            for p, k in _PROVIDER_KEY_NAMES.items()
        }
        test_key = {
            "LLM": "llm_test",
            "CHAT_LLM": "chat_llm_test",
            "VISION_LLM": "vision_test",
            "MEMORY_LLM": "memory_test",
        }[route_name]

        self._start_async_test(
            test_key,
            status_label,
            lambda: llm.test_route_connection(
                provider,
                model,
                route_name,
                image=image,
                anthropic_api_key=anthropic_api_key,
                custom_base_url=custom_base_url,
                compat_keys=compat_keys,
            ),
        )

    def _test_primary_llm_connection(self) -> None:
        self._test_llm_route(
            provider_key="LLM_PROVIDER",
            model_key="LLM_MODEL",
            route_name="LLM",
            status_label=self._llm_test_status_lbl,
        )

    def _test_chat_llm_connection(self) -> None:
        self._test_llm_route(
            provider_key="CHAT_LLM_PROVIDER",
            model_key="CHAT_LLM_MODEL",
            route_name="CHAT_LLM",
            status_label=self._chat_llm_test_status_lbl,
        )

    def _test_vision_connection(self) -> None:
        self._test_llm_route(
            provider_key="VISION_LLM_PROVIDER",
            model_key="VISION_LLM_MODEL",
            route_name="VISION_LLM",
            status_label=self._vision_test_status_lbl,
            image=True,
        )

    def _test_memory_connection(self) -> None:
        self._test_llm_route(
            provider_key="MEMORY_LLM_PROVIDER",
            model_key="MEMORY_LLM_MODEL",
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
            if self._on_apply:
                self._on_apply()
            self.accept()

    def _do_save(self) -> bool:
        """Write .env. Returns True on success, False if validation failed."""
        if not self._save_api_keys_to_keychain():
            return False
        vals = {
            "LLM_PROVIDER":      _get(self._fields["LLM_PROVIDER"]),
            "LLM_MODEL":         _get(self._fields["LLM_MODEL"]),
            "LLM_FALLBACKS":     self._get_fallback_rows("LLM_FALLBACKS"),
            "CHAT_LLM_PROVIDER": _get(self._fields["CHAT_LLM_PROVIDER"]),
            "CHAT_LLM_MODEL":    _get(self._fields["CHAT_LLM_MODEL"]),
            "CHAT_LLM_FALLBACKS": self._get_fallback_rows("CHAT_LLM_FALLBACKS"),
            "TTS_PROVIDER":      _get(self._fields["TTS_PROVIDER"]),
            "CARTESIA_VOICE_ID": _get(self._fields["CARTESIA_VOICE_ID"]),
            "HOTKEY_ADD_CONTEXT":  _get(self._fields["HOTKEY_ADD_CONTEXT"]),
            "HOTKEY_CLEAR_CONTEXT": _get(self._fields["HOTKEY_CLEAR_CONTEXT"]),
            "HOTKEY_SNIP":         _get(self._fields["HOTKEY_SNIP"]),
            "SNIP_CONTEXT_AMBIENT": str(self._fields["SNIP_CONTEXT_AMBIENT"].isChecked()),  # type: ignore
            "SNIP_CONTEXT_DOCUMENTS": str(self._fields["SNIP_CONTEXT_DOCUMENTS"].isChecked()),  # type: ignore
            "SNIP_CONTEXT_TOOLS": str(self._fields["SNIP_CONTEXT_TOOLS"].isChecked()),  # type: ignore
            "VISION_LLM_PROVIDER":      _get(self._fields["VISION_LLM_PROVIDER"]),
            "VISION_LLM_MODEL":         _get(self._fields["VISION_LLM_MODEL"]),
            "VISION_LLM_FALLBACKS":     self._get_fallback_rows("VISION_LLM_FALLBACKS"),
            "CONTEXT_BROWSER_MAX_CHARS": _get(self._fields["CONTEXT_BROWSER_MAX_CHARS"]),
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"]),
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": _get(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"]),
            "TOOL_PLUGIN_DIR": _get(self._fields["TOOL_PLUGIN_DIR"]),
            "CUSTOM_BASE_URL":            _get(self._fields["CUSTOM_BASE_URL"]),
            "GITHUB_CLIENT_ID":          _get(self._fields["GITHUB_CLIENT_ID"]),
            "GITHUB_OAUTH_SCOPES":       _get(self._fields["GITHUB_OAUTH_SCOPES"]),
            "MEMORY_LLM_PROVIDER":      _get(self._fields["MEMORY_LLM_PROVIDER"]),
            "MEMORY_LLM_MODEL":         _get(self._fields["MEMORY_LLM_MODEL"]),
            "MEMORY_AUTO_CONSOLIDATE":   str(self._fields["MEMORY_AUTO_CONSOLIDATE"].isChecked()),  # type: ignore
            "MEMORY_CONSOLIDATION_INTERVAL": _get(self._fields["MEMORY_CONSOLIDATION_INTERVAL"]),
            "MEMORY_TOP_K":             _get(self._fields["MEMORY_TOP_K"]),
            "MEMORY_RELEVANCE_MAX_DISTANCE": _get(self._fields["MEMORY_RELEVANCE_MAX_DISTANCE"]),
            "MEMORY_STM_TOKEN_BUDGET":  _get(self._fields["MEMORY_STM_TOKEN_BUDGET"]),
            "CALLER_COUNT":  str(len(self._caller_blocks)),
            "THEME_MODE":       self._fields["THEME_MODE"].currentData(),  # type: ignore[attr-defined]
            "DOLL_AUTO_HIDE":    str(self._fields["DOLL_AUTO_HIDE"].isChecked()),  # type: ignore
            "CHAT_AUTO_ELABORATE": str(self._fields["CHAT_AUTO_ELABORATE"].isChecked()),  # type: ignore
            "CHAT_ELABORATE_PROMPT": _get(self._fields["CHAT_ELABORATE_PROMPT"]),
            "DOLL_SIZE":    _get(self._fields["DOLL_SIZE"]),
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
            vals[f"CALLER_{n}_CONTEXT_DOCUMENTS"] = str(blk["context_documents"].isChecked())  # type: ignore
            vals[f"CALLER_{n}_CONTEXT_TOOLS"] = str(blk["context_tools"].isChecked())  # type: ignore
            vals[f"CALLER_{n}_CONTEXT_SCREENSHOT"] = str(blk["context_screenshot"].isChecked())  # type: ignore
            vals[f"CALLER_{n}_INTENT_COUNT"]  = str(len(blk["intent_rows"]))
            for j, row in enumerate(blk["intent_rows"]):
                m = j + 1
                vals[f"CALLER_{n}_INTENT_{m}_KEY"]    = _get(row["key"])
                vals[f"CALLER_{n}_INTENT_{m}_LABEL"]  = _get(row["label"])
                vals[f"CALLER_{n}_INTENT_{m}_PROMPT"] = _get(row["prompt"])
        _write_env(vals, remove_keys=set(secret_store.API_KEY_NAMES))
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
    lbl.setStyleSheet("color: palette(mid); font-size: 9pt;")
    return lbl


def _link_label(text: str, url: str) -> QLabel:
    lbl = QLabel(f'<a href="{url}">{text}</a>')
    lbl.setOpenExternalLinks(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lbl.setToolTip(url)
    return lbl


def _parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    return parse_fallback_rows(raw)


def open_settings(parent=None, on_apply=None):
    global _settings_dialog
    dialog_parent = None if os.name == "nt" else parent
    if _settings_dialog is None:
        _settings_dialog = SettingsDialog(dialog_parent, on_apply=on_apply)
    else:
        _settings_dialog._on_apply = on_apply
        _settings_dialog._env = _read_env()
        _settings_dialog._load_values()

    if _settings_dialog.isMinimized():
        _settings_dialog.showNormal()
    _settings_dialog.show()
    _settings_dialog.raise_()
    _settings_dialog.activateWindow()


