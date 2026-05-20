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
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

ENV_PATH = Path(__file__).parent.parent / ".env"


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
    vals: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
    return vals


def _write_env(vals: dict[str, str]):
    lines = []
    if ENV_PATH.exists():
        raw = ENV_PATH.read_text(encoding="utf-8").splitlines()
        written = set()
        for line in raw:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in vals:
                    lines.append(f"{k}={vals[k]}")
                    written.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        for k, v in vals.items():
            if k not in written:
                lines.append(f"{k}={v}")
    else:
        for k, v in vals.items():
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_apply=None):
        super().__init__(parent)
        self._on_apply = on_apply  # callable() fired after a successful apply
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._env = _read_env()
        self._fields: dict[str, QLineEdit | QComboBox | QCheckBox | QTextEdit] = {}
        self._build_ui()
        self._load_values()

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
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

        self._fields["LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic", "chatgpt"]
        )
        self._fields["LLM_MODEL"] = QLineEdit()
        self._fields["GROQ_API_KEY"] = self._password()
        self._fields["OPENAI_API_KEY"] = self._password()
        self._fields["ANTHROPIC_API_KEY"] = self._password()

        self._fields["CHAT_LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic", "chatgpt"]
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
        f.addRow(_sep(), _sep())
        note = QLabel("<small><i>openai</i> = API key (pay-per-token) &nbsp;|&nbsp; <i>chatgpt</i> = your Pro/Plus subscription</small>")
        note.setWordWrap(True)
        f.addRow("", note)
        f.addRow("Groq API key", self._fields["GROQ_API_KEY"])
        f.addRow("OpenAI API key", self._fields["OPENAI_API_KEY"])
        f.addRow("Anthropic API key", self._fields["ANTHROPIC_API_KEY"])
        f.addRow(_sep(), _sep())
        f.addRow(QLabel("<i>Chat / Elaborate model</i>"), QLabel(""))
        f.addRow("Chat provider", self._fields["CHAT_LLM_PROVIDER"])
        f.addRow("Chat model", self._fields["CHAT_LLM_MODEL"])
        f.addRow(_sep(), _sep())
        self._fields["VISION_LLM_PROVIDER"] = self._combo(
            ["", "anthropic", "openai", "chatgpt"]
        )
        self._fields["VISION_LLM_MODEL"] = QLineEdit()
        self._fields["VISION_LLM_MODEL"].setPlaceholderText("e.g. claude-opus-4-5 / gpt-4o")
        f.addRow(QLabel("<i>Vision model (screen snip)</i>"), QLabel(""))
        f.addRow("Vision provider", self._fields["VISION_LLM_PROVIDER"])
        f.addRow("Vision model", self._fields["VISION_LLM_MODEL"])

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

        return w

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

    def _tab_tts(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

        self._fields["TTS_PROVIDER"] = self._combo(
            ["cartesia", "elevenlabs", "none"]
        )
        self._fields["CARTESIA_API_KEY"] = self._password()
        self._fields["CARTESIA_VOICE_ID"] = QLineEdit()
        self._fields["CARTESIA_VOICE_ID"].setPlaceholderText("e.g. a0e99841-438c-4a64-b679-ae501e7d6091")
        self._fields["ELEVENLABS_API_KEY"] = self._password()

        f.addRow("Provider", self._fields["TTS_PROVIDER"])
        f.addRow(_sep(), _sep())
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
        util.setFixedHeight(80)
        self._fields["SYSTEM_PROMPT_UTILITY"] = util
        layout.addWidget(util)
        layout.addStretch()
        return w

    def _tab_keybinds(self) -> QWidget:
        from PyQt6.QtWidgets import QScrollArea, QSizePolicy
        container = QWidget()
        self._keybinds_layout = QVBoxLayout(container)
        self._keybinds_layout.setSpacing(6)
        self._keybinds_layout.setContentsMargins(12, 12, 12, 12)

        # Caller hotkeys section
        self._keybinds_layout.addWidget(QLabel("<b>Caller Hotkeys</b>"))

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
        self._fields["BUBBLE_REVEAL_WPM"] = QLineEdit()
        self._fields["BUBBLE_REVEAL_WPM"].setPlaceholderText("e.g. 170")

        f.addRow("", self._fields["DOLL_AUTO_HIDE"])
        f.addRow("", self._fields["CHAT_AUTO_ELABORATE"])
        f.addRow("Elaborate prompt", self._fields["CHAT_ELABORATE_PROMPT"])
        f.addRow(_sep(), _sep())
        f.addRow("Doll icon size (px)", self._fields["DOLL_SIZE"])
        f.addRow("Bubble width (px)", self._fields["BUBBLE_WIDTH"])
        f.addRow("Bubble lines", self._fields["BUBBLE_LINES"])
        f.addRow("Bubble text speed (WPM)", self._fields["BUBBLE_REVEAL_WPM"])
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

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _load_values(self):
        import config as cfg

        _set(self._fields["LLM_PROVIDER"], self._env.get("LLM_PROVIDER", cfg.LLM_PROVIDER))
        _set(self._fields["LLM_MODEL"], self._env.get("LLM_MODEL", cfg.LLM_MODEL))
        _set(self._fields["CHAT_LLM_PROVIDER"], self._env.get("CHAT_LLM_PROVIDER", cfg.CHAT_LLM_PROVIDER))
        _set(self._fields["CHAT_LLM_MODEL"], self._env.get("CHAT_LLM_MODEL", cfg.CHAT_LLM_MODEL))
        _set(self._fields["GROQ_API_KEY"], self._env.get("GROQ_API_KEY", ""))
        _set(self._fields["OPENAI_API_KEY"], self._env.get("OPENAI_API_KEY", ""))
        _set(self._fields["ANTHROPIC_API_KEY"], self._env.get("ANTHROPIC_API_KEY", ""))
        _set(self._fields["TTS_PROVIDER"], self._env.get("TTS_PROVIDER", cfg.TTS_PROVIDER))
        _set(self._fields["CARTESIA_API_KEY"], self._env.get("CARTESIA_API_KEY", ""))
        _set(self._fields["CARTESIA_VOICE_ID"], self._env.get("CARTESIA_VOICE_ID", ""))
        _set(self._fields["ELEVENLABS_API_KEY"], self._env.get("ELEVENLABS_API_KEY", ""))
        _set(self._fields["HOTKEY_ADD_CONTEXT"],   self._env.get("HOTKEY_ADD_CONTEXT",   cfg.HOTKEY_ADD_CONTEXT))
        _set(self._fields["HOTKEY_CLEAR_CONTEXT"], self._env.get("HOTKEY_CLEAR_CONTEXT", cfg.HOTKEY_CLEAR_CONTEXT))
        _set(self._fields["HOTKEY_SNIP"],          self._env.get("HOTKEY_SNIP",          cfg.HOTKEY_SNIP))
        _set(self._fields["VISION_LLM_PROVIDER"],  self._env.get("VISION_LLM_PROVIDER",  cfg.VISION_LLM_PROVIDER))
        _set(self._fields["VISION_LLM_MODEL"],     self._env.get("VISION_LLM_MODEL",     cfg.VISION_LLM_MODEL))

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
        _set(self._fields["BUBBLE_REVEAL_WPM"], self._env.get("BUBBLE_REVEAL_WPM", str(cfg.BUBBLE_REVEAL_WPM)))

        util_val = self._env.get("SYSTEM_PROMPT_UTILITY", cfg.SYSTEM_PROMPT_UTILITY)
        self._fields["SYSTEM_PROMPT_UTILITY"].setPlainText(util_val)  # type: ignore

    def _apply(self):
        """Save without closing the dialog, then apply changes live."""
        if self._do_save():
            import config
            from core import llm as _llm
            config.reload()
            _llm.reset_clients()
            if self._on_apply:
                self._on_apply()
            self._status_lbl.setText("Applied.")
            QTimer.singleShot(4000, lambda: self._status_lbl.setText(""))

    def _save(self):
        """Save and close the dialog."""
        if self._do_save():
            import config as _cfg
            from core import llm as _llm
            _cfg.reload()
            _llm.reset_clients()
            if self._on_apply:
                self._on_apply()
            self.accept()

    def _do_save(self) -> bool:
        """Write .env. Returns True on success, False if validation failed."""
        vals = {
            "LLM_PROVIDER":      _get(self._fields["LLM_PROVIDER"]),
            "LLM_MODEL":         _get(self._fields["LLM_MODEL"]),
            "CHAT_LLM_PROVIDER": _get(self._fields["CHAT_LLM_PROVIDER"]),
            "CHAT_LLM_MODEL":    _get(self._fields["CHAT_LLM_MODEL"]),
            "GROQ_API_KEY":      _get(self._fields["GROQ_API_KEY"]),
            "OPENAI_API_KEY":    _get(self._fields["OPENAI_API_KEY"]),
            "ANTHROPIC_API_KEY": _get(self._fields["ANTHROPIC_API_KEY"]),
            "TTS_PROVIDER":      _get(self._fields["TTS_PROVIDER"]),
            "CARTESIA_API_KEY":  _get(self._fields["CARTESIA_API_KEY"]),
            "CARTESIA_VOICE_ID": _get(self._fields["CARTESIA_VOICE_ID"]),
            "ELEVENLABS_API_KEY": _get(self._fields["ELEVENLABS_API_KEY"]),
            "HOTKEY_ADD_CONTEXT":  _get(self._fields["HOTKEY_ADD_CONTEXT"]),
            "HOTKEY_CLEAR_CONTEXT": _get(self._fields["HOTKEY_CLEAR_CONTEXT"]),
            "HOTKEY_SNIP":         _get(self._fields["HOTKEY_SNIP"]),
            "VISION_LLM_PROVIDER":      _get(self._fields["VISION_LLM_PROVIDER"]),
            "VISION_LLM_MODEL":         _get(self._fields["VISION_LLM_MODEL"]),
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
            "BUBBLE_REVEAL_WPM": _get(self._fields["BUBBLE_REVEAL_WPM"]),
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
            vals[f"CALLER_{n}_INTENT_COUNT"]  = str(len(blk["intent_rows"]))
            for j, row in enumerate(blk["intent_rows"]):
                m = j + 1
                vals[f"CALLER_{n}_INTENT_{m}_KEY"]    = _get(row["key"])
                vals[f"CALLER_{n}_INTENT_{m}_LABEL"]  = _get(row["label"])
                vals[f"CALLER_{n}_INTENT_{m}_PROMPT"] = _get(row["prompt"])
        _write_env(vals)
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_MODEL_HINTS: dict[str, str] = {
    "groq":      "e.g. llama3-8b-8192",
    "openai":    "e.g. gpt-4o",
    "anthropic": "e.g. claude-sonnet-4-5",
    "chatgpt":   "gpt-5.5  |  gpt-5.4  |  gpt-5.4-mini  |  gpt-5.3-codex",
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


def _get(widget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText()
    elif isinstance(widget, QLineEdit):
        return widget.text()
    return ""


def open_settings(parent=None, on_apply=None):
    dlg = SettingsDialog(parent, on_apply=on_apply)
    dlg.exec()
