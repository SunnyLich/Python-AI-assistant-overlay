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

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        tabs = QTabWidget()
        tabs.addTab(self._tab_llm(),       "LLM")
        tabs.addTab(self._tab_tts(),       "TTS / Voice")
        tabs.addTab(self._tab_prompt(),    "Prompts")
        tabs.addTab(self._tab_keybinds(),  "Keybinds")
        tabs.addTab(self._tab_app(),       "App")
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
            ["groq", "openai", "anthropic"]
        )
        self._fields["LLM_MODEL"] = QLineEdit()
        self._fields["GROQ_API_KEY"] = self._password()
        self._fields["OPENAI_API_KEY"] = self._password()
        self._fields["ANTHROPIC_API_KEY"] = self._password()

        self._fields["CHAT_LLM_PROVIDER"] = self._combo(
            ["groq", "openai", "anthropic"]
        )
        self._fields["CHAT_LLM_MODEL"] = QLineEdit()
        self._fields["CHAT_LLM_MODEL"].setPlaceholderText("Leave blank to use same model as above")

        f.addRow("Provider", self._fields["LLM_PROVIDER"])
        f.addRow("Model", self._fields["LLM_MODEL"])
        f.addRow(_sep(), _sep())
        f.addRow("Groq API key", self._fields["GROQ_API_KEY"])
        f.addRow("OpenAI API key", self._fields["OPENAI_API_KEY"])
        f.addRow("Anthropic API key", self._fields["ANTHROPIC_API_KEY"])
        f.addRow(_sep(), _sep())
        f.addRow(QLabel("<i>Chat / Elaborate model</i>"), QLabel(""))
        f.addRow("Chat provider", self._fields["CHAT_LLM_PROVIDER"])
        f.addRow("Chat model", self._fields["CHAT_LLM_MODEL"])
        f.addRow(_sep(), _sep())
        self._fields["VISION_LLM_PROVIDER"] = self._combo(
            ["", "anthropic", "openai"]
        )
        self._fields["VISION_LLM_MODEL"] = QLineEdit()
        self._fields["VISION_LLM_MODEL"].setPlaceholderText("e.g. claude-opus-4-5")
        f.addRow(QLabel("<i>Vision model (screen snip)</i>"), QLabel(""))
        f.addRow("Vision provider", self._fields["VISION_LLM_PROVIDER"])
        f.addRow("Vision model", self._fields["VISION_LLM_MODEL"])
        return w

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
        self._keybinds_layout.setSpacing(2)
        self._keybinds_layout.setContentsMargins(12, 12, 12, 12)

        # Column header
        hdr = QWidget()
        hdr_h = QHBoxLayout(hdr)
        hdr_h.setContentsMargins(0, 0, 0, 6)
        hdr_h.setSpacing(8)
        for text, w in [("Key", 112), ("Special Function", 142), ("Label", 102), ("Prompt", 0)]:
            lbl = QLabel(f"<b>{text}</b>")
            if w:
                lbl.setFixedWidth(w)
            else:
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            hdr_h.addWidget(lbl)
        hdr_h.addSpacing(36)  # align with delete-button column
        self._keybinds_layout.addWidget(hdr)

        # Special rows (in order)
        self._fields["HOTKEY_INVOKE"]            = self._kb_special_row("Call the app")
        self._fields["HOTKEY_CUSTOM_PROMPT_KEY"] = self._kb_special_row("Custom prompt")
        self._fields["HOTKEY_ADD_CONTEXT"]       = self._kb_special_row("Add as prompt")
        self._fields["HOTKEY_CLEAR_CONTEXT"]     = self._kb_special_row("Clear prompt")
        self._fields["HOTKEY_SNIP"]              = self._kb_special_row("Snip screen region")

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(128,128,128,80); margin: 4px 0px;")
        self._keybinds_layout.addWidget(sep)

        # Dynamic user rows area
        self._user_rows_widget = QWidget()
        self._user_rows_layout = QVBoxLayout(self._user_rows_widget)
        self._user_rows_layout.setSpacing(2)
        self._user_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._keybinds_layout.addWidget(self._user_rows_widget)
        self._user_bind_rows: list[dict] = []

        # Add-row button
        add_btn = QPushButton("+ Add row")
        add_btn.setFixedWidth(90)
        add_btn.clicked.connect(lambda: self._add_user_bind_row())
        btn_wrap = QHBoxLayout()
        btn_wrap.setContentsMargins(0, 6, 0, 0)
        btn_wrap.addWidget(add_btn)
        btn_wrap.addStretch()
        self._keybinds_layout.addLayout(btn_wrap)
        self._keybinds_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _kb_special_row(self, special_label: str) -> QLineEdit:
        """Add one non-deletable special keybind row; return its key QLineEdit."""
        from PyQt6.QtWidgets import QSizePolicy
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(8)

        key_edit = QLineEdit()
        key_edit.setFixedWidth(112)
        key_edit.setPlaceholderText("e.g. ctrl+q or s")
        h.addWidget(key_edit)

        s_lbl = QLabel(special_label)
        s_lbl.setFixedWidth(142)
        s_lbl.setStyleSheet("font-style: italic; color: palette(mid);")
        h.addWidget(s_lbl)

        for fixed_w in [102, 0]:
            filler = QLineEdit()
            filler.setEnabled(False)
            if fixed_w:
                filler.setFixedWidth(fixed_w)
            else:
                filler.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            h.addWidget(filler)

        h.addSpacing(36)  # align with delete-button column of user rows
        self._keybinds_layout.addWidget(row_w)
        return key_edit

    def _add_user_bind_row(self, key: str = "", label: str = "", prompt: str = "") -> None:
        from PyQt6.QtWidgets import QSizePolicy
        row_w = QWidget()
        h = QHBoxLayout(row_w)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(8)

        key_edit = QLineEdit(key)
        key_edit.setFixedWidth(112)
        key_edit.setPlaceholderText("e.g. w")
        h.addWidget(key_edit)

        empty_lbl = QLabel()
        empty_lbl.setFixedWidth(142)
        h.addWidget(empty_lbl)

        label_edit = QLineEdit(label)
        label_edit.setFixedWidth(102)
        label_edit.setPlaceholderText("Short label")
        h.addWidget(label_edit)

        prompt_edit = QLineEdit(prompt)
        prompt_edit.setPlaceholderText("Full prompt sent to LLM")
        prompt_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(prompt_edit)

        row_info: dict = {"widget": row_w, "key": key_edit, "label": label_edit, "prompt": prompt_edit}

        del_btn = QPushButton("×")
        del_btn.setFixedWidth(28)
        del_btn.clicked.connect(lambda: self._delete_user_bind_row(row_info))
        h.addWidget(del_btn)

        self._user_rows_layout.addWidget(row_w)
        self._user_bind_rows.append(row_info)

    def _delete_user_bind_row(self, row_info: dict) -> None:
        if row_info in self._user_bind_rows:
            self._user_bind_rows.remove(row_info)
        row_info["widget"].deleteLater()

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

    def _combo(self, options: list[str]) -> QComboBox:
        cb = QComboBox()
        cb.addItems(options)
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
        _set(self._fields["HOTKEY_INVOKE"],            self._env.get("HOTKEY_INVOKE",            cfg.HOTKEY_INVOKE))
        _set(self._fields["HOTKEY_CUSTOM_PROMPT_KEY"], self._env.get("HOTKEY_CUSTOM_PROMPT_KEY", cfg.HOTKEY_CUSTOM_PROMPT_KEY))
        _set(self._fields["HOTKEY_ADD_CONTEXT"],       self._env.get("HOTKEY_ADD_CONTEXT",       cfg.HOTKEY_ADD_CONTEXT))
        _set(self._fields["HOTKEY_CLEAR_CONTEXT"],     self._env.get("HOTKEY_CLEAR_CONTEXT",     cfg.HOTKEY_CLEAR_CONTEXT))
        _set(self._fields["HOTKEY_SNIP"],              self._env.get("HOTKEY_SNIP",              cfg.HOTKEY_SNIP))
        _set(self._fields["VISION_LLM_PROVIDER"],      self._env.get("VISION_LLM_PROVIDER",      cfg.VISION_LLM_PROVIDER))
        _set(self._fields["VISION_LLM_MODEL"],         self._env.get("VISION_LLM_MODEL",         cfg.VISION_LLM_MODEL))

        count = int(self._env.get("INTENT_COUNT", str(len(cfg.INTENT_ROWS))))
        for i in range(count):
            row = cfg.INTENT_ROWS[i] if i < len(cfg.INTENT_ROWS) else {"key": "", "label": "", "prompt": ""}
            self._add_user_bind_row(
                key=self._env.get(f"INTENT_{i + 1}_KEY",    row["key"]),
                label=self._env.get(f"INTENT_{i + 1}_LABEL",  row["label"]),
                prompt=self._env.get(f"INTENT_{i + 1}_PROMPT", row["prompt"]),
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
            config.reload()
            if self._on_apply:
                self._on_apply()
            self._status_lbl.setText("Applied.")
            QTimer.singleShot(4000, lambda: self._status_lbl.setText(""))

    def _save(self):
        """Save and close the dialog."""
        if self._do_save():
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
            "HOTKEY_INVOKE":            _get(self._fields["HOTKEY_INVOKE"]),
            "HOTKEY_CUSTOM_PROMPT_KEY": _get(self._fields["HOTKEY_CUSTOM_PROMPT_KEY"]),
            "HOTKEY_ADD_CONTEXT":       _get(self._fields["HOTKEY_ADD_CONTEXT"]),
            "HOTKEY_CLEAR_CONTEXT":     _get(self._fields["HOTKEY_CLEAR_CONTEXT"]),
            "HOTKEY_SNIP":              _get(self._fields["HOTKEY_SNIP"]),
            "VISION_LLM_PROVIDER":      _get(self._fields["VISION_LLM_PROVIDER"]),
            "VISION_LLM_MODEL":         _get(self._fields["VISION_LLM_MODEL"]),
            "INTENT_COUNT":             str(len(self._user_bind_rows)),
            "DOLL_AUTO_HIDE":    str(self._fields["DOLL_AUTO_HIDE"].isChecked()),  # type: ignore
            "CHAT_AUTO_ELABORATE": str(self._fields["CHAT_AUTO_ELABORATE"].isChecked()),  # type: ignore
            "CHAT_ELABORATE_PROMPT": _get(self._fields["CHAT_ELABORATE_PROMPT"]),
            "DOLL_SIZE":    _get(self._fields["DOLL_SIZE"]),
            "BUBBLE_WIDTH": _get(self._fields["BUBBLE_WIDTH"]),
            "BUBBLE_LINES": _get(self._fields["BUBBLE_LINES"]),
            "BUBBLE_REVEAL_WPM": _get(self._fields["BUBBLE_REVEAL_WPM"]),
            "SYSTEM_PROMPT_UTILITY": self._fields["SYSTEM_PROMPT_UTILITY"].toPlainText(),  # type: ignore
        }
        # Key conflict check across all bindings
        all_keys = [
            _get(self._fields[k]).strip().lower()
            for k in ("HOTKEY_INVOKE", "HOTKEY_CUSTOM_PROMPT_KEY", "HOTKEY_ADD_CONTEXT", "HOTKEY_CLEAR_CONTEXT", "HOTKEY_SNIP")
        ] + [_get(r["key"]).strip().lower() for r in self._user_bind_rows]
        non_empty = [k for k in all_keys if k]
        if len(non_empty) != len(set(non_empty)):
            QMessageBox.warning(self, "Duplicate keys",
                                "Two or more bindings share the same key.\nPlease resolve conflicts before saving.")
            return False
        for i, row in enumerate(self._user_bind_rows):
            vals[f"INTENT_{i + 1}_KEY"]    = _get(row["key"])
            vals[f"INTENT_{i + 1}_LABEL"]  = _get(row["label"])
            vals[f"INTENT_{i + 1}_PROMPT"] = _get(row["prompt"])
        _write_env(vals)
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

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
