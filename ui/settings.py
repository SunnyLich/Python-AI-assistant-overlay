"""
ui/settings.py — Settings dialog.

A plain GUI for editing all user-configurable values.
Reads from and writes to the .env file.
Launch via tray icon → Settings, or call open_settings().
"""
from __future__ import annotations
import os
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QComboBox, QCheckBox,
    QPushButton, QTabWidget, QWidget, QFrame, QMessageBox,
    QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from dotenv import dotenv_values
from core import secret_store
from ui.window_utils import fit_window_to_screen

ENV_PATH = Path(__file__).parent.parent / ".env"
_settings_dialog: "SettingsDialog | None" = None


# ---------------------------------------------------------------------------
# Hotkey capture widget
# ---------------------------------------------------------------------------

# Map Qt key codes → hotkey-string tokens (must match _parse_hotkey in hotkeys.py)
_QT_KEY_NAMES: dict[int, str] = {
    Qt.Key.Key_Space.value:     "space",
    Qt.Key.Key_Tab.value:       "tab",
    Qt.Key.Key_Return.value:    "enter",
    Qt.Key.Key_Enter.value:     "enter",
    Qt.Key.Key_Backspace.value: "backspace",
    Qt.Key.Key_Delete.value:    "delete",
    Qt.Key.Key_Insert.value:    "insert",
    Qt.Key.Key_Home.value:      "home",
    Qt.Key.Key_End.value:       "end",
    Qt.Key.Key_PageUp.value:    "pageup",
    Qt.Key.Key_PageDown.value:  "pagedown",
    Qt.Key.Key_Left.value:      "left",
    Qt.Key.Key_Right.value:     "right",
    Qt.Key.Key_Up.value:        "up",
    Qt.Key.Key_Down.value:      "down",
    **{Qt.Key[f"Key_F{i}"].value: f"f{i}" for i in range(1, 25)},
}

_MODIFIER_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
}


class HotkeyCaptureEdit(QLineEdit):
    """
    Read-only QLineEdit that captures a hotkey combo by interaction:
      1. User clicks the field  → recording starts.
      2. User holds modifiers and presses the trigger key → combo is saved immediately.
      Esc → cancel and restore previous value.

    Commits on key-press (not release).
    """

    _IDLE_STYLE    = ""
    _RECORD_STYLE  = "background: #1e1e3a; color: #a0a0ff; border: 1px solid #6060cc;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click to set...")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recording = False
        self._prev_text = ""

    # ------------------------------------------------------------------
    # Start / stop recording
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):          # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_recording()
        super().mousePressEvent(event)

    def _start_recording(self):
        self._recording = True
        self._prev_text = self.text()
        self.setText("Press a key combo...")
        self.setStyleSheet(self._RECORD_STYLE)
        self.setFocus()

    def _commit(self, combo: str):
        """Accept a captured combo string and exit recording mode."""
        self._recording = False
        self.setStyleSheet(self._IDLE_STYLE)
        self.setText(combo)

    def _cancel(self):
        """Discard and restore the previous value."""
        self._recording = False
        self.setStyleSheet(self._IDLE_STYLE)
        self.setText(self._prev_text)

    # ------------------------------------------------------------------
    # Key capture — commit on PRESS so Alt+Space is captured before
    # Windows opens the system menu and swallows the key-release event.
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):            # noqa: N802
        if not self._recording:
            super().keyPressEvent(event)
            return
        qt_key = Qt.Key(event.key())
        if qt_key in _MODIFIER_KEYS:
            event.accept()
            return
        if qt_key == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
            return
        mods = event.modifiers()
        key  = event.key()
        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier: parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:     parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:   parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:    parts.append("win")
        key_name = _QT_KEY_NAMES.get(key)
        if key_name is None:
            ch = chr(key).lower() if 0x20 < key <= 0x7E else ""
            key_name = ch if ch else None
        if key_name:
            parts.append(key_name)
            self._commit("+".join(parts))
        # else: unrecognised key — stay in recording mode
        event.accept()

    def keyReleaseEvent(self, event):          # noqa: N802
        if self._recording:
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):            # noqa: N802
        # If still recording when focus is lost (e.g. user clicked elsewhere
        # without pressing a key, or a rare case where keyPressEvent never
        # fired), cancel to avoid leaving the field in a stuck recording state.
        if self._recording:
            self._cancel()
        super().focusOutEvent(event)


def _read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    return {
        k: v
        for k, v in dotenv_values(ENV_PATH).items()
        if k is not None and v is not None
    }


def _format_env_value(value: str) -> str:
    if any(ch in value for ch in ("\n", "\r", '"', "#")):
        escaped = (
            value.replace("\\", "\\\\")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\n")
            .replace('"', '\\"')
        )
        return f'"{escaped}"'
    return value


def _write_env(vals: dict[str, str], remove_keys: set[str] | None = None):
    remove_keys = remove_keys or set()
    lines = []
    if ENV_PATH.exists():
        raw = ENV_PATH.read_text(encoding="utf-8").splitlines()
        written = set()
        for line in raw:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in remove_keys:
                    continue
                elif k in vals:
                    lines.append(f"{k}={_format_env_value(vals[k])}")
                    written.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        for k, v in vals.items():
            if k not in written:
                lines.append(f"{k}={_format_env_value(v)}")
    else:
        for k, v in vals.items():
            lines.append(f"{k}={_format_env_value(v)}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_apply=None):
        super().__init__(parent)
        self._on_apply = on_apply  # callable() fired after a successful apply
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._env = _read_env()
        self._fields: dict[str, QLineEdit | QComboBox | QCheckBox | QTextEdit] = {}
        self._build_ui()
        self._load_values()
        fit_window_to_screen(self, preferred_width=620, preferred_height=620)

    def _save_api_keys_to_keychain(self) -> bool:
        labels = {
            "GROQ_API_KEY": "Groq",
            "OPENAI_API_KEY": "OpenAI",
            "ANTHROPIC_API_KEY": "Anthropic",
            "CARTESIA_API_KEY": "Cartesia",
            "ELEVENLABS_API_KEY": "ElevenLabs",
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
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowDeactivate:
            for w in self.findChildren(HotkeyCaptureEdit):
                if w._recording:
                    w._cancel()
        super().changeEvent(event)

    def showEvent(self, event):                 # noqa: N802
        super().showEvent(event)
        fit_window_to_screen(self, preferred_width=620, preferred_height=620)

    def _build_ui(self):
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
        save_btn   = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

    def _tab_llm(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

        self._fields["LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic", "chatgpt", "copilot"]
        )
        self._fields["LLM_MODEL"] = QLineEdit()
        self._fallback_rows: dict[str, list[dict]] = {
            "LLM_FALLBACKS": [],
            "CHAT_LLM_FALLBACKS": [],
            "VISION_LLM_FALLBACKS": [],
        }
        self._fields["GROQ_API_KEY"] = self._password()
        self._fields["OPENAI_API_KEY"] = self._password()
        self._fields["ANTHROPIC_API_KEY"] = self._password()
        self._fields["GROQ_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["OPENAI_API_KEY"].setPlaceholderText("Stored in OS keychain")
        self._fields["ANTHROPIC_API_KEY"].setPlaceholderText("Stored in OS keychain")

        self._fields["CHAT_LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic", "chatgpt", "copilot"]
        )

        def _update_model_placeholders():
            p = self._fields["LLM_PROVIDER"].currentText()
            hint = _model_hint(p)
            self._fields["LLM_MODEL"].setPlaceholderText(hint)
            cp = self._fields["CHAT_LLM_PROVIDER"].currentText()
            chint = _model_hint(cp) if cp else "Leave blank to use same model as above"
            self._fields["CHAT_LLM_MODEL"].setPlaceholderText(chint)

        self._fields["LLM_PROVIDER"].currentTextChanged.connect(lambda _: _update_model_placeholders())
        self._fields["CHAT_LLM_PROVIDER"].currentTextChanged.connect(lambda _: _update_model_placeholders())
        self._fields["CHAT_LLM_MODEL"] = QLineEdit()
        self._fields["CHAT_LLM_MODEL"].setPlaceholderText("Leave blank to use same model as above")

        f.addRow("Provider", self._fields["LLM_PROVIDER"])
        f.addRow("Model", self._fields["LLM_MODEL"])
        self._add_fallback_editor(f, "Fallback priority", "LLM_FALLBACKS")
        f.addRow(_sep(), _sep())
        note = QLabel("<small><i>openai</i> = API key (pay-per-token) &nbsp;|&nbsp; <i>chatgpt</i> = your Pro/Plus subscription</small>")
        note.setWordWrap(True)
        f.addRow("", note)
        key_note = QLabel("<small>API keys are saved to the OS keychain. Leave blank to keep the stored key.</small>")
        key_note.setWordWrap(True)
        f.addRow("", key_note)
        f.addRow("Groq API key", self._fields["GROQ_API_KEY"])
        f.addRow("OpenAI API key", self._fields["OPENAI_API_KEY"])
        f.addRow("Anthropic API key", self._fields["ANTHROPIC_API_KEY"])
        f.addRow(_sep(), _sep())
        f.addRow(QLabel("<i>Chat / Elaborate model</i>"), QLabel(""))
        f.addRow("Chat provider", self._fields["CHAT_LLM_PROVIDER"])
        f.addRow("Chat model", self._fields["CHAT_LLM_MODEL"])
        self._add_fallback_editor(f, "Chat fallback priority", "CHAT_LLM_FALLBACKS")
        f.addRow(_sep(), _sep())
        self._fields["VISION_LLM_PROVIDER"] = self._combo(
            ["", "anthropic", "openai", "chatgpt"]
        )
        self._fields["VISION_LLM_MODEL"] = QLineEdit()
        self._fields["VISION_LLM_MODEL"].setPlaceholderText("e.g. claude-opus-4-5 / gpt-4o")
        f.addRow(QLabel("<i>Vision model (screen snip)</i>"), QLabel(""))
        f.addRow("Vision provider", self._fields["VISION_LLM_PROVIDER"])
        f.addRow("Vision model", self._fields["VISION_LLM_MODEL"])
        self._add_fallback_editor(f, "Vision fallback priority", "VISION_LLM_FALLBACKS", providers=["", "anthropic", "openai", "chatgpt"])

        # ---- ChatGPT Pro/Plus OAuth section ----
        f.addRow(_sep(), _sep())
        f.addRow(QLabel("<i>ChatGPT Pro/Plus (your subscription)</i>"), QLabel(""))

        self._chatgpt_status_lbl = QLabel()
        self._chatgpt_status_lbl.setWordWrap(True)
        self._refresh_chatgpt_status()
        f.addRow("Status", self._chatgpt_status_lbl)

        cgpt_btn_w = QWidget()
        cgpt_btn_h = QHBoxLayout(cgpt_btn_w)
        cgpt_btn_h.setContentsMargins(0, 0, 0, 0)
        cgpt_btn_h.setSpacing(6)
        self._cgpt_login_btn    = QPushButton("Sign in (browser)")
        self._cgpt_device_btn   = QPushButton("Sign in (headless)")
        self._cgpt_logout_btn   = QPushButton("Sign out")
        cgpt_btn_h.addWidget(self._cgpt_login_btn)
        cgpt_btn_h.addWidget(self._cgpt_device_btn)
        cgpt_btn_h.addWidget(self._cgpt_logout_btn)
        cgpt_btn_h.addStretch()
        self._cgpt_login_btn.clicked.connect(self._chatgpt_login_browser)
        self._cgpt_device_btn.clicked.connect(self._chatgpt_login_device)
        self._cgpt_logout_btn.clicked.connect(self._chatgpt_logout)
        f.addRow("", cgpt_btn_w)

        # ---- GitHub OAuth section ----
        f.addRow(_sep(), _sep())
        f.addRow(
            _desc_label(
                "GitHub OAuth",
                "Sign in opens GitHub in your browser. Client ID is bundled; override only for development.",
            ),
            QLabel(""),
        )
        self._fields["GITHUB_CLIENT_ID"] = QLineEdit()
        self._fields["GITHUB_CLIENT_ID"].setPlaceholderText("Optional custom OAuth app client ID")
        self._fields["GITHUB_OAUTH_SCOPES"] = QLineEdit()
        self._fields["GITHUB_OAUTH_SCOPES"].setPlaceholderText("e.g. repo read:user user:email")
        f.addRow("Custom client ID", self._fields["GITHUB_CLIENT_ID"])
        f.addRow("GitHub scopes", self._fields["GITHUB_OAUTH_SCOPES"])

        self._github_status_lbl = QLabel()
        self._github_status_lbl.setWordWrap(True)
        self._refresh_github_status()
        f.addRow("Status", self._github_status_lbl)

        github_btn_w = QWidget()
        github_btn_h = QHBoxLayout(github_btn_w)
        github_btn_h.setContentsMargins(0, 0, 0, 0)
        github_btn_h.setSpacing(6)
        self._github_login_btn = QPushButton("Sign in")
        self._github_logout_btn = QPushButton("Sign out")
        github_btn_h.addWidget(self._github_login_btn)
        github_btn_h.addWidget(self._github_logout_btn)
        github_btn_h.addStretch()
        self._github_login_btn.clicked.connect(self._github_login_device)
        self._github_logout_btn.clicked.connect(self._github_logout)
        f.addRow("", github_btn_w)

        # ---- GitHub Copilot token section ----
        f.addRow(_sep(), _sep())
        f.addRow(
            _desc_label(
                "GitHub Copilot token",
                "Use a fine-grained PAT with Copilot Requests: Read-only. Stored only in the OS keychain.",
            ),
            QLabel(""),
        )

        self._copilot_token_edit = self._password()
        self._copilot_token_edit.setPlaceholderText("github_pat_... (not saved to .env)")
        f.addRow("Token", self._copilot_token_edit)

        self._copilot_status_lbl = QLabel()
        self._copilot_status_lbl.setWordWrap(True)
        self._refresh_copilot_status()
        f.addRow("Status", self._copilot_status_lbl)

        copilot_btn_w = QWidget()
        copilot_btn_h = QHBoxLayout(copilot_btn_w)
        copilot_btn_h.setContentsMargins(0, 0, 0, 0)
        copilot_btn_h.setSpacing(6)
        self._copilot_save_btn = QPushButton("Save token")
        self._copilot_test_btn = QPushButton("Test token / SDK")
        self._copilot_clear_btn = QPushButton("Clear token")
        copilot_btn_h.addWidget(self._copilot_save_btn)
        copilot_btn_h.addWidget(self._copilot_test_btn)
        copilot_btn_h.addWidget(self._copilot_clear_btn)
        copilot_btn_h.addStretch()
        self._copilot_save_btn.clicked.connect(self._copilot_save_token)
        self._copilot_test_btn.clicked.connect(self._copilot_test_token)
        self._copilot_clear_btn.clicked.connect(self._copilot_clear_token)
        f.addRow("", copilot_btn_w)

        scroll.setWidget(w)
        return scroll

    def _refresh_chatgpt_status(self) -> None:
        try:
            from core import chatgpt_auth
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
        from core import chatgpt_auth
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
                # Device code info — show it without stopping the poll
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
            from core import chatgpt_auth
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
        from core import chatgpt_auth
        self._chatgpt_status_lbl.setText("Starting device auth…")
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
            from core import chatgpt_auth
            chatgpt_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_chatgpt_status()

    def _refresh_github_status(self) -> None:
        try:
            from core import github_auth
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
        from core import github_auth

        override_client_id = _get(self._fields["GITHUB_CLIENT_ID"]).strip()
        cfg.GITHUB_CLIENT_ID = override_client_id or getattr(cfg, "GITHUB_DEFAULT_CLIENT_ID", "")
        cfg.GITHUB_OAUTH_SCOPES = _get(self._fields["GITHUB_OAUTH_SCOPES"]).strip()
        if not cfg.GITHUB_CLIENT_ID:
            self._github_status_lbl.setText("No bundled GitHub OAuth client ID is configured yet.")
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
            from core import github_auth
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
            from core import github_auth
            github_auth.clear_tokens()
        except Exception:
            pass
        self._refresh_github_status()

    def _refresh_copilot_status(self) -> None:
        try:
            from core import copilot_auth
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
            from core import copilot_auth
            copilot_auth.save_token(self._copilot_token_edit.text())
            self._copilot_token_edit.clear()
            self._refresh_copilot_status()
        except Exception as exc:
            self._copilot_status_lbl.setText(str(exc))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")
            QMessageBox.warning(self, "GitHub Copilot token", str(exc))

    def _copilot_clear_token(self) -> None:
        try:
            from core import copilot_auth
            copilot_auth.clear_token()
            self._copilot_token_edit.clear()
            self._refresh_copilot_status()
        except Exception as exc:
            self._copilot_status_lbl.setText(str(exc))
            self._copilot_status_lbl.setStyleSheet("color: #c04040;")
            QMessageBox.warning(self, "GitHub Copilot token", str(exc))

    def _copilot_test_token(self) -> None:
        try:
            from core import copilot_client
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
        f.addRow("Cartesia API key", self._fields["CARTESIA_API_KEY"])
        f.addRow("Cartesia Voice ID", self._fields["CARTESIA_VOICE_ID"])
        f.addRow(_sep(), _sep())
        f.addRow("ElevenLabs API key", self._fields["ELEVENLABS_API_KEY"])
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
        from PyQt6.QtWidgets import QScrollArea, QSizePolicy
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
        from PyQt6.QtWidgets import QSizePolicy
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
        hotkey_edit.setPlaceholderText("Hotkey…")
        hdr_h.addWidget(hotkey_edit)

        hdr_h.addWidget(QLabel("Name:"))
        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(110)
        label_edit.setPlaceholderText("Label")
        hdr_h.addWidget(label_edit)

        paste_cb = QCheckBox("Paste result back")
        paste_cb.setChecked(paste_back)
        hdr_h.addWidget(paste_cb)

        hdr_h.addWidget(QLabel("↵ key:"))
        custom_key_edit = QLineEdit(custom_key)
        custom_key_edit.setFixedWidth(36)
        custom_key_edit.setPlaceholderText("s")
        hdr_h.addWidget(custom_key_edit)

        hdr_h.addStretch()
        del_caller_btn = QPushButton("✕ Remove")
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
        from PyQt6.QtWidgets import QSizePolicy as SP
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
        from PyQt6.QtWidgets import QSizePolicy
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
        prompt_edit.setPlaceholderText("Prompt sent to LLM…")
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(prompt_edit)

        row_info: dict = {"widget": row_w, "key": key_edit, "label": label_edit, "prompt": prompt_edit}

        del_btn = QPushButton("×")
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
        from PyQt6.QtWidgets import QGroupBox, QSizePolicy

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
            ["groq", "openai", "anthropic"],
            self._env.get("MEMORY_LLM_PROVIDER", ""),
        )
        self._fields["MEMORY_LLM_PROVIDER"] = mem_provider
        f.addRow("Memory LLM provider:", mem_provider)

        mem_model = QLineEdit(self._env.get("MEMORY_LLM_MODEL", ""))
        mem_model.setPlaceholderText("e.g. llama-3.1-8b-instant")
        self._fields["MEMORY_LLM_MODEL"] = mem_model
        f.addRow("Memory LLM model:", mem_model)

        mem_interval = QLineEdit(self._env.get("MEMORY_CONSOLIDATION_INTERVAL", "15"))
        mem_interval.setPlaceholderText("minutes between consolidations")
        self._fields["MEMORY_CONSOLIDATION_INTERVAL"] = mem_interval
        f.addRow("Consolidation interval (min):", mem_interval)

        mem_topk = QLineEdit(self._env.get("MEMORY_TOP_K", "3"))
        mem_topk.setPlaceholderText("number of facts to retrieve per query")
        self._fields["MEMORY_TOP_K"] = mem_topk
        f.addRow("Retrieval top-k:", mem_topk)

        mem_budget = QLineEdit(self._env.get("MEMORY_STM_TOKEN_BUDGET", "4000"))
        mem_budget.setPlaceholderText("tokens before STM compression kicks in")
        self._fields["MEMORY_STM_TOKEN_BUDGET"] = mem_budget
        f.addRow("STM token budget:", mem_budget)

        root.addWidget(cfg_group)

        # --- Fact browser ---
        browser_group = QGroupBox("Stored Facts")
        browser_layout = QVBoxLayout(browser_group)
        browser_layout.setContentsMargins(6, 6, 6, 6)

        try:
            from core.memory import get_manager
            from ui.memory_viewer import MemoryPanel
            panel = MemoryPanel(get_manager(), browser_group)
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            browser_layout.addWidget(panel)
        except Exception as exc:
            from PyQt6.QtCore import Qt
            err = QLabel(f"Memory store unavailable:\n{exc}")
            err.setAlignment(Qt.AlignmentFlag.AlignCenter)
            err.setStyleSheet("color: #c00;")
            browser_layout.addWidget(err)

        root.addWidget(browser_group, stretch=1)

        return w

    def _tab_app(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

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

    def _combo(self, options: list[str], current: str = "") -> QComboBox:
        cb = QComboBox()
        cb.addItems(options)
        if current and current in options:
            cb.setCurrentText(current)
        return cb

    def _password(self) -> QLineEdit:
        le = QLineEdit()
        le.setEchoMode(QLineEdit.EchoMode.Password)
        return le

    def _add_fallback_editor(
        self,
        form: QFormLayout,
        label: str,
        key: str,
        providers: list[str] | None = None,
    ) -> None:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        rows_widget = QWidget()
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(4)
        layout.addWidget(rows_widget)

        add_btn = QPushButton("+ Add fallback")
        add_btn.setFixedWidth(120)
        add_btn.clicked.connect(lambda: self._add_fallback_row(key, providers=providers))
        add_wrap = QHBoxLayout()
        add_wrap.setContentsMargins(0, 0, 0, 0)
        add_wrap.addWidget(add_btn)
        add_wrap.addStretch()
        layout.addLayout(add_wrap)

        self._fields[key] = box
        self._fallback_rows[key] = []
        self._fallback_rows[f"{key}__layout"] = rows_layout  # type: ignore[index]
        self._fallback_rows[f"{key}__providers"] = providers or ["groq", "openai", "anthropic", "chatgpt", "copilot"]  # type: ignore[index]
        form.addRow(label, box)

    def _add_fallback_row(
        self,
        key: str,
        provider: str = "",
        model: str = "",
        providers: list[str] | None = None,
    ) -> None:
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        provider_options = providers or self._fallback_rows.get(f"{key}__providers", ["groq", "openai", "anthropic", "chatgpt", "copilot"])  # type: ignore[arg-type]
        provider_combo = self._combo(provider_options, provider)
        provider_combo.setFixedWidth(130)
        model_edit = QLineEdit(model)
        model_edit.setPlaceholderText("model")
        remove_btn = QPushButton("Remove")
        remove_btn.setFixedWidth(70)
        h.addWidget(provider_combo)
        h.addWidget(model_edit)
        h.addWidget(remove_btn)

        row_info = {"widget": row_w, "provider": provider_combo, "model": model_edit}
        remove_btn.clicked.connect(lambda: self._remove_fallback_row(key, row_info))
        rows_layout = self._fallback_rows[f"{key}__layout"]  # type: ignore[index]
        rows_layout.addWidget(row_w)
        self._fallback_rows[key].append(row_info)

    def _remove_fallback_row(self, key: str, row_info: dict) -> None:
        if row_info in self._fallback_rows[key]:
            self._fallback_rows[key].remove(row_info)
        row_info["widget"].deleteLater()

    def _set_fallback_rows(self, key: str, raw: str) -> None:
        for row in list(self._fallback_rows[key]):
            self._remove_fallback_row(key, row)
        for provider, model in _parse_fallback_rows(raw):
            self._add_fallback_row(key, provider, model)

    def _get_fallback_rows(self, key: str) -> str:
        parts = []
        for row in self._fallback_rows[key]:
            provider = row["provider"].currentText().strip()
            model = row["model"].text().strip()
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
            status = "stored in OS keychain" if secret_store.has_secret(name) or self._env.get(name) else "not configured"
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
        _set(self._fields["GITHUB_CLIENT_ID"],     self._env.get("GITHUB_CLIENT_ID",     cfg.GITHUB_CLIENT_ID))
        _set(self._fields["GITHUB_OAUTH_SCOPES"],  self._env.get("GITHUB_OAUTH_SCOPES",  cfg.GITHUB_OAUTH_SCOPES))
        _set(self._fields["CONTEXT_BROWSER_MAX_CHARS"], self._env.get("CONTEXT_BROWSER_MAX_CHARS", str(cfg.CONTEXT_BROWSER_MAX_CHARS)))
        _set(self._fields["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS)))
        _set(self._fields["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"], self._env.get("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", str(cfg.CONTEXT_TOOL_DOCUMENT_MAX_CHARS)))
        _set(self._fields["TOOL_PLUGIN_DIR"], self._env.get("TOOL_PLUGIN_DIR", cfg.TOOL_PLUGIN_DIR))

        _set(self._fields["MEMORY_LLM_PROVIDER"],    self._env.get("MEMORY_LLM_PROVIDER",    cfg.MEMORY_LLM_PROVIDER))
        _set(self._fields["MEMORY_LLM_MODEL"],       self._env.get("MEMORY_LLM_MODEL",       cfg.MEMORY_LLM_MODEL))
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

    def _apply(self):
        """Save without closing the dialog, then apply changes live."""
        if self._do_save():
            import config
            from core import llm as _llm
            from core import tts as _tts
            config.reload()
            _llm.reset_clients()
            _tts.reset_connections()
            if self._on_apply:
                self._on_apply()
            self._status_lbl.setText("Applied.")
            QTimer.singleShot(4000, lambda: self._status_lbl.setText(""))

    def _save(self):
        """Save and close the dialog."""
        if self._do_save():
            import config as _cfg
            from core import llm as _llm
            from core import tts as _tts
            _cfg.reload()
            _llm.reset_clients()
            _tts.reset_connections()
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
            "GITHUB_CLIENT_ID":          _get(self._fields["GITHUB_CLIENT_ID"]),
            "GITHUB_OAUTH_SCOPES":       _get(self._fields["GITHUB_OAUTH_SCOPES"]),
            "MEMORY_LLM_PROVIDER":      _get(self._fields["MEMORY_LLM_PROVIDER"]),
            "MEMORY_LLM_MODEL":         _get(self._fields["MEMORY_LLM_MODEL"]),
            "MEMORY_CONSOLIDATION_INTERVAL": _get(self._fields["MEMORY_CONSOLIDATION_INTERVAL"]),
            "MEMORY_TOP_K":             _get(self._fields["MEMORY_TOP_K"]),
            "MEMORY_STM_TOKEN_BUDGET":  _get(self._fields["MEMORY_STM_TOKEN_BUDGET"]),
            "CALLER_COUNT":  str(len(self._caller_blocks)),
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

_MODEL_HINTS: dict[str, str] = {
    "groq":      "e.g. llama3-8b-8192",
    "openai":    "e.g. gpt-4o",
    "anthropic": "e.g. claude-sonnet-4-5",
    "chatgpt":   "gpt-5.5  |  gpt-5.4  |  gpt-5.4-mini  |  gpt-5.3-codex",
    "copilot":   "e.g. gpt-4.1",
}


def _model_hint(provider: str) -> str:
    return _MODEL_HINTS.get(provider.lower(), "model name")


def _sep() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(0,0,0,0);")
    return line


def _set(widget, value: str):
    if isinstance(widget, QComboBox):
        idx = widget.findText(value)
        if idx >= 0:
            widget.setCurrentIndex(idx)
    elif isinstance(widget, QLineEdit):
        widget.setText(value)
    elif isinstance(widget, QTextEdit):
        widget.setPlainText(value)


def _get(widget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText()
    elif isinstance(widget, QLineEdit):
        return widget.text()
    elif isinstance(widget, QTextEdit):
        return widget.toPlainText()
    return ""


def _desc_label(title: str, description: str) -> QLabel:
    lbl = QLabel(f"<b>{title}</b><br><small>{description}</small>")
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: palette(mid);")
    return lbl


def _parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for part in raw.replace(";", "\n").splitlines():
        item = part.strip()
        if not item or item.startswith("#") or ":" not in item:
            continue
        provider, model = item.split(":", 1)
        provider = provider.strip()
        model = model.strip()
        if provider and model:
            rows.append((provider, model))
    return rows


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
