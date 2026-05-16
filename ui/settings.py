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
from PyQt6.QtCore import Qt
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
    def __init__(self, parent=None):
        super().__init__(parent)
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
        tabs.addTab(self._tab_llm(),     "LLM")
        tabs.addTab(self._tab_tts(),     "TTS / Voice")
        tabs.addTab(self._tab_prompt(),  "Prompts")
        tabs.addTab(self._tab_intents(), "Intents")
        tabs.addTab(self._tab_app(),     "App")
        root.addWidget(tabs)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
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

        f.addRow("Provider", self._fields["LLM_PROVIDER"])
        f.addRow("Model", self._fields["LLM_MODEL"])
        f.addRow(_sep(), _sep())
        f.addRow("Groq API key", self._fields["GROQ_API_KEY"])
        f.addRow("OpenAI API key", self._fields["OPENAI_API_KEY"])
        f.addRow("Anthropic API key", self._fields["ANTHROPIC_API_KEY"])
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

        layout.addWidget(QLabel("Utility mode system prompt:"))
        util = QTextEdit()
        util.setFixedHeight(80)
        self._fields["SYSTEM_PROMPT_UTILITY"] = util
        layout.addWidget(util)

        layout.addWidget(QLabel("Cute mode system prompt:"))
        cute = QTextEdit()
        cute.setFixedHeight(80)
        self._fields["SYSTEM_PROMPT_CUTE"] = cute
        layout.addWidget(cute)

        cute_mode = QCheckBox("Enable cute mode")
        self._fields["CUTE_MODE"] = cute_mode
        layout.addWidget(cute_mode)
        layout.addStretch()
        return w

    def _tab_intents(self) -> QWidget:
        from PyQt6.QtWidgets import QScrollArea, QSizePolicy
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(14)
        layout.setContentsMargins(12, 12, 12, 12)

        directions = [
            ("up",    "↑  Up"),
            ("down",  "↓  Down"),
            ("left",  "←  Left"),
            ("right", "→  Right"),
        ]
        for key, title in directions:
            lbl_key    = f"INTENT_{key.upper()}_LABEL"
            prompt_key = f"INTENT_{key.upper()}_PROMPT"

            layout.addWidget(QLabel(f"<b>{title}</b>"))

            label_edit = QLineEdit()
            label_edit.setPlaceholderText("Short label shown on picker")
            self._fields[lbl_key] = label_edit

            prompt_edit = QTextEdit()
            prompt_edit.setFixedHeight(62)
            prompt_edit.setPlaceholderText("Full instruction sent to LLM")
            self._fields[prompt_key] = prompt_edit

            f = QFormLayout()
            f.setSpacing(6)
            f.addRow("Label",  label_edit)
            f.addRow("Prompt", prompt_edit)
            layout.addLayout(f)

        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    def _tab_app(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setSpacing(10)
        f.setContentsMargins(12, 12, 12, 12)

        self._fields["HOTKEY_INVOKE"] = QLineEdit()
        self._fields["HOTKEY_INVOKE"].setPlaceholderText("e.g. ctrl+u")

        f.addRow("Invoke hotkey", self._fields["HOTKEY_INVOKE"])
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
        _set(self._fields["GROQ_API_KEY"], self._env.get("GROQ_API_KEY", ""))
        _set(self._fields["OPENAI_API_KEY"], self._env.get("OPENAI_API_KEY", ""))
        _set(self._fields["ANTHROPIC_API_KEY"], self._env.get("ANTHROPIC_API_KEY", ""))
        _set(self._fields["TTS_PROVIDER"], self._env.get("TTS_PROVIDER", cfg.TTS_PROVIDER))
        _set(self._fields["CARTESIA_API_KEY"], self._env.get("CARTESIA_API_KEY", ""))
        _set(self._fields["CARTESIA_VOICE_ID"], self._env.get("CARTESIA_VOICE_ID", ""))
        _set(self._fields["ELEVENLABS_API_KEY"], self._env.get("ELEVENLABS_API_KEY", ""))
        _set(self._fields["HOTKEY_INVOKE"], self._env.get("HOTKEY_INVOKE", cfg.HOTKEY_INVOKE))

        for key in ("up", "down", "left", "right"):
            lbl_key    = f"INTENT_{key.upper()}_LABEL"
            prompt_key = f"INTENT_{key.upper()}_PROMPT"
            _set(self._fields[lbl_key],
                 self._env.get(lbl_key, cfg.INTENT_SHORTCUTS[key]["label"]))
            self._fields[prompt_key].setPlainText(  # type: ignore
                self._env.get(prompt_key, cfg.INTENT_SHORTCUTS[key]["prompt"]))

        util_val = self._env.get("SYSTEM_PROMPT_UTILITY", cfg.SYSTEM_PROMPT_UTILITY)
        cute_val = self._env.get("SYSTEM_PROMPT_CUTE", cfg.SYSTEM_PROMPT_CUTE)
        self._fields["SYSTEM_PROMPT_UTILITY"].setPlainText(util_val)  # type: ignore
        self._fields["SYSTEM_PROMPT_CUTE"].setPlainText(cute_val)      # type: ignore

        cute_mode = self._env.get("CUTE_MODE", str(cfg.CUTE_MODE)).lower() == "true"
        self._fields["CUTE_MODE"].setChecked(cute_mode)  # type: ignore

    def _save(self):
        vals = {
            "LLM_PROVIDER":      _get(self._fields["LLM_PROVIDER"]),
            "LLM_MODEL":         _get(self._fields["LLM_MODEL"]),
            "GROQ_API_KEY":      _get(self._fields["GROQ_API_KEY"]),
            "OPENAI_API_KEY":    _get(self._fields["OPENAI_API_KEY"]),
            "ANTHROPIC_API_KEY": _get(self._fields["ANTHROPIC_API_KEY"]),
            "TTS_PROVIDER":      _get(self._fields["TTS_PROVIDER"]),
            "CARTESIA_API_KEY":  _get(self._fields["CARTESIA_API_KEY"]),
            "CARTESIA_VOICE_ID": _get(self._fields["CARTESIA_VOICE_ID"]),
            "ELEVENLABS_API_KEY": _get(self._fields["ELEVENLABS_API_KEY"]),
            "HOTKEY_INVOKE":     _get(self._fields["HOTKEY_INVOKE"]),
            "SYSTEM_PROMPT_UTILITY": self._fields["SYSTEM_PROMPT_UTILITY"].toPlainText(),  # type: ignore
            "SYSTEM_PROMPT_CUTE":    self._fields["SYSTEM_PROMPT_CUTE"].toPlainText(),     # type: ignore
            "CUTE_MODE":         str(self._fields["CUTE_MODE"].isChecked()),               # type: ignore
        }
        for key in ("up", "down", "left", "right"):
            lbl_key    = f"INTENT_{key.upper()}_LABEL"
            prompt_key = f"INTENT_{key.upper()}_PROMPT"
            vals[lbl_key]    = _get(self._fields[lbl_key])
            vals[prompt_key] = self._fields[prompt_key].toPlainText()  # type: ignore
        _write_env(vals)
        QMessageBox.information(self, "Saved", "Settings saved. Restart the app to apply changes.")
        self.accept()


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


def open_settings(parent=None):
    dlg = SettingsDialog(parent)
    dlg.exec()
