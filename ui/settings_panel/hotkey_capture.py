"""Hotkey capture widget used by the settings dialog."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLineEdit


_QT_KEY_NAMES: dict[int, str] = {
    Qt.Key.Key_Space.value: "space",
    Qt.Key.Key_Tab.value: "tab",
    Qt.Key.Key_Return.value: "enter",
    Qt.Key.Key_Enter.value: "enter",
    Qt.Key.Key_Backspace.value: "backspace",
    Qt.Key.Key_Delete.value: "delete",
    Qt.Key.Key_Insert.value: "insert",
    Qt.Key.Key_Home.value: "home",
    Qt.Key.Key_End.value: "end",
    Qt.Key.Key_PageUp.value: "pageup",
    Qt.Key.Key_PageDown.value: "pagedown",
    Qt.Key.Key_Left.value: "left",
    Qt.Key.Key_Right.value: "right",
    Qt.Key.Key_Up.value: "up",
    Qt.Key.Key_Down.value: "down",
    **{Qt.Key[f"Key_F{i}"].value: f"f{i}" for i in range(1, 25)},
}

_MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
}


class HotkeyCaptureEdit(QLineEdit):
    """Read-only field that captures a hotkey combo by interaction."""

    _IDLE_STYLE = ""
    _RECORD_STYLE = "background: #1e1e3a; color: #a0a0ff; border: 1px solid #6060cc;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click to set...")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recording = False
        self._prev_text = ""

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_recording()
        super().mousePressEvent(event)

    def _start_recording(self) -> None:
        self._recording = True
        self._prev_text = self.text()
        self.setText("Press a key combo...")
        self.setStyleSheet(self._RECORD_STYLE)
        self.setFocus()

    def _commit(self, combo: str) -> None:
        self._recording = False
        self.setStyleSheet(self._IDLE_STYLE)
        self.setText(combo)

    def _cancel(self) -> None:
        self._recording = False
        self.setStyleSheet(self._IDLE_STYLE)
        self.setText(self._prev_text)

    def keyPressEvent(self, event):  # noqa: N802
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
        key = event.key()
        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")
        key_name = _QT_KEY_NAMES.get(key)
        if key_name is None:
            ch = chr(key).lower() if 0x20 < key <= 0x7E else ""
            key_name = ch if ch else None
        if key_name:
            parts.append(key_name)
            self._commit("+".join(parts))
        event.accept()

    def keyReleaseEvent(self, event):  # noqa: N802
        if self._recording:
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        if self._recording:
            self._cancel()
        super().focusOutEvent(event)
