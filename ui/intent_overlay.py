"""
ui/intent_overlay.py — Compact intent picker shown on Ctrl+E.

Small floating widget centred on screen — no background dim.
Shows 4 WASD + label rows. Press a key to pick, Escape to cancel.
S = Custom prompt (opens inline text input).
"""
from __future__ import annotations
from PyQt6.QtWidgets import QWidget, QApplication, QLineEdit
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush
import keyboard
import config


_DIRECTIONS = [
    ("up",    "W"),
    ("left",  "A"),
    ("right", "D"),
    ("down",  "S"),
]

# Physical key -> logical direction (matches INTENT_SHORTCUTS keys)
_KEY_MAP = {"w": "up", "a": "left", "d": "right", "s": "down"}

_CUSTOM_DIRECTION = "down"   # S key triggers free-text input

_W             = 230
_ROW_H         = 36
_PADDING       = 14
_RADIUS        = 10
_AUTO_CLOSE_MS = 5000
_INPUT_EXTRA   = 54   # extra height when custom-input mode is active

_BG     = QColor(28, 28, 36, 230)
_ROW_HL = QColor(255, 255, 255, 22)
_ARROW  = QColor(160, 160, 255, 220)
_LABEL  = QColor(230, 230, 230, 230)
_HINT   = QColor(120, 120, 140, 180)


class IntentOverlay(QWidget):
    intent_chosen = pyqtSignal(str, str)   # (direction_key, prompt)
    cancelled     = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        n_rows = len(_DIRECTIONS)
        h = _PADDING * 2 + _ROW_H * n_rows + 28   # 28px for ESC hint
        self._normal_h = h
        self.setFixedSize(_W, h)

        screen = QApplication.primaryScreen().geometry()
        self.move(
            screen.x() + (screen.width()  - _W) // 2,
            screen.y() + (screen.height() - h)  // 2,
        )

        self._hovered: str | None = None
        self._handled = False
        self._custom_mode = False

        # Inline text input (shown only in custom mode)
        self._input_line = QLineEdit(self)
        self._input_line.setPlaceholderText("Type your prompt, press Enter...")
        self._input_line.setStyleSheet(
            "QLineEdit {"
            "  background: rgba(255,255,255,12);"
            "  border: 1px solid rgba(255,255,255,55);"
            "  border-radius: 5px;"
            "  color: #e6e6e6;"
            "  padding: 4px 8px;"
            "  font-size: 10pt;"
            "}"
        )
        self._input_line.hide()
        self._input_line.returnPressed.connect(self._fire_custom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._cancel)
        self._timer.start(_AUTO_CLOSE_MS)

        # Global key hooks -- work regardless of Qt window focus
        self._hooks: list = []
        for key in ("w", "a", "s", "d"):
            self._hooks.append(keyboard.on_press_key(key, self._on_key, suppress=True))
        self._hooks.append(
            keyboard.on_press_key("escape", lambda _: QTimer.singleShot(0, self._cancel), suppress=True)
        )

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.setBrush(QBrush(_BG))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, _W, self.height(), _RADIUS, _RADIUS)

        key_font   = QFont("Segoe UI", 13, QFont.Weight.Bold)
        label_font = QFont("Segoe UI", 10)
        hint_font  = QFont("Segoe UI", 8)

        y = _PADDING
        for direction, glyph in _DIRECTIONS:
            if direction == self._hovered:
                p.setBrush(QBrush(_ROW_HL))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(6, y, _W - 12, _ROW_H, 6, 6)

            p.setFont(key_font)
            p.setPen(QPen(_ARROW))
            p.drawText(12, y, 28, _ROW_H, Qt.AlignmentFlag.AlignCenter, glyph)

            label = config.INTENT_SHORTCUTS[direction]["label"]
            p.setFont(label_font)
            p.setPen(QPen(_LABEL))
            p.drawText(46, y, _W - 52, _ROW_H,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       label)

            y += _ROW_H

        p.setFont(hint_font)
        p.setPen(QPen(_HINT))
        hint_y = y + (_INPUT_EXTRA if self._custom_mode else 4)
        p.drawText(0, hint_y, _W, 20, Qt.AlignmentFlag.AlignCenter, "ESC to cancel")

        p.end()

    # ------------------------------------------------------------------
    # Key input
    # ------------------------------------------------------------------

    def _on_key(self, event):
        """Called from keyboard library thread -- marshal to Qt main thread."""
        direction = _KEY_MAP.get(event.name)
        if direction:
            QTimer.singleShot(0, lambda: self._select(direction))

    def _select(self, direction: str):
        if self._handled:
            return
        if direction == _CUSTOM_DIRECTION:
            self._hovered = direction
            self._unhook()
            self._hooks.append(
                keyboard.on_press_key("escape", lambda _: QTimer.singleShot(0, self._cancel), suppress=True)
            )
            QTimer.singleShot(0, self._enter_custom_mode)
            return
        self._handled = True
        self._hovered = direction
        self.update()
        QTimer.singleShot(80, lambda: self._fire(direction))

    def _enter_custom_mode(self):
        self._custom_mode = True
        new_h = self._normal_h + _INPUT_EXTRA
        screen = QApplication.primaryScreen().geometry()
        self.setFixedSize(_W, new_h)
        self.move(
            screen.x() + (screen.width()  - _W) // 2,
            screen.y() + (screen.height() - new_h) // 2,
        )
        self._input_line.setGeometry(10, self._normal_h - 24, _W - 20, 32)
        self._input_line.show()
        self._input_line.setFocus()
        self.update()

    def _fire_custom(self):
        text = self._input_line.text().strip()
        if not text:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.intent_chosen.emit(_CUSTOM_DIRECTION, text)
        self.close()

    def keyPressEvent(self, event):
        """Fallback for when Qt window has focus."""
        wasd = {
            Qt.Key.Key_W: "up",
            Qt.Key.Key_A: "left",
            Qt.Key.Key_D: "right",
            Qt.Key.Key_S: "down",
        }
        direction = wasd.get(event.key())
        if direction:
            self._select(direction)
        elif event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.raise_()

    # ------------------------------------------------------------------
    # Cleanup / fire
    # ------------------------------------------------------------------

    def _unhook(self):
        for hook in self._hooks:
            try:
                keyboard.unhook(hook)
            except Exception:
                pass
        self._hooks.clear()

    def _fire(self, direction: str):
        self._unhook()
        self._timer.stop()
        prompt = config.INTENT_SHORTCUTS[direction]["prompt"]
        self.intent_chosen.emit(direction, prompt)
        self.close()

    def _cancel(self):
        if self._handled:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.cancelled.emit()
        self.close()
