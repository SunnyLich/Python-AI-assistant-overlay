"""
ui/intent_overlay.py — Compact intent picker shown on Ctrl+Q.

Small floating widget centred on screen — no background dim.
Rows are built dynamically from config.INTENT_ROWS plus a fixed
Custom Prompt row (config.HOTKEY_CUSTOM_PROMPT_KEY).
Press the matching key to pick, Escape to cancel.
"""
from __future__ import annotations
import sys
from PyQt6.QtWidgets import QWidget, QApplication, QLineEdit
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPoint, QRect
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush
import config
import keyboard



def _build_rows(caller_idx: int = 0) -> list[dict]:
    """Build the full list of overlay rows from the specified caller's config."""
    caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
    rows = []
    for r in caller.get("intents", []):
        rows.append({
            "glyph":     r["key"].upper() if r["key"] else "?",
            "label":     r["label"],
            "prompt":    r["prompt"],
            "is_custom": False,
        })
    # Custom prompt row always appended last
    custom_key = caller.get("custom_key", "s")
    rows.append({
        "glyph":     custom_key.upper(),
        "label":     "Custom prompt",
        "prompt":    "",
        "is_custom": True,
    })
    return rows


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
    _raw_key      = pyqtSignal(str)        # keyboard hook thread → Qt main thread

    def __init__(self, caller_idx: int = 0, target_hwnd: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Popup   # auto-focuses on Windows; closes on click-outside
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._rows = _build_rows(caller_idx)
        n_rows = len(self._rows)
        h = _PADDING * 2 + _ROW_H * n_rows + 28   # 28px for ESC hint
        self._normal_h = h
        self.setFixedSize(_W, h)
        self._target_hwnd = target_hwnd
        self._screen_geometry = self._resolve_screen_geometry()

        self._move_to_screen_center(h)

        self._hovered: int | None = None
        self._handled = False
        self._custom_mode = False
        self._kb_hook = None
        self._drop_next_keypress = False
        self._raw_key.connect(self._on_raw_key)

        # Inline text input (shown only in custom mode)
        self._input_line = QLineEdit(self)
        self._input_line.installEventFilter(self)
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

    def _resolve_screen_geometry(self) -> QRect:
        app = QApplication.instance()
        if self._target_hwnd:
            if sys.platform == "win32":
                try:
                    import ctypes
                    import ctypes.wintypes
                    rect = ctypes.wintypes.RECT()
                    if ctypes.windll.user32.GetWindowRect(self._target_hwnd, ctypes.byref(rect)):
                        center = QPoint(
                            (rect.left + rect.right) // 2,
                            (rect.top + rect.bottom) // 2,
                        )
                        screen = app.screenAt(center) if app is not None else None
                        if screen is not None:
                            return screen.geometry()
                except Exception:
                    pass
            else:
                # On Linux use the current cursor position to find the right screen
                from PyQt6.QtGui import QCursor
                cursor_pos = QCursor.pos()
                screen = app.screenAt(cursor_pos) if app is not None else None
                if screen is not None:
                    return screen.geometry()
        primary = QApplication.primaryScreen()
        return primary.geometry() if primary is not None else QRect(0, 0, _W, self._normal_h)

    def _move_to_screen_center(self, height: int) -> None:
        screen = self._screen_geometry
        self.move(
            screen.x() + (screen.width() - _W) // 2,
            screen.y() + (screen.height() - height) // 2,
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
        for i, row in enumerate(self._rows):
            if i == self._hovered:
                p.setBrush(QBrush(_ROW_HL))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(6, y, _W - 12, _ROW_H, 6, 6)

            p.setFont(key_font)
            p.setPen(QPen(_ARROW))
            p.drawText(12, y, 28, _ROW_H, Qt.AlignmentFlag.AlignCenter, row["glyph"])

            p.setFont(label_font)
            p.setPen(QPen(_LABEL))
            p.drawText(46, y, _W - 52, _ROW_H,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       row["label"])

            y += _ROW_H

        p.setFont(hint_font)
        p.setPen(QPen(_HINT))
        hint_y = y + (_INPUT_EXTRA if self._custom_mode else 4)
        p.drawText(0, hint_y, _W, 20, Qt.AlignmentFlag.AlignCenter, "ESC to cancel")

        p.end()

    # ------------------------------------------------------------------
    # Key input
    # ------------------------------------------------------------------

    def _select(self, idx: int):
        if self._handled:
            return
        if self._rows[idx]["is_custom"]:
            self._hovered = idx
            self._unhook()  # release grabKeyboard; Qt handles typing in QLineEdit
            QTimer.singleShot(0, self._enter_custom_mode)
            return
        self._handled = True
        self._hovered = idx
        self.update()
        QTimer.singleShot(80, lambda: self._fire(idx))

    def _enter_custom_mode(self):
        self._custom_mode = True
        self._timer.stop()   # don't auto-close while the user is typing
        new_h = self._normal_h + _INPUT_EXTRA
        self.setFixedSize(_W, new_h)
        self._move_to_screen_center(new_h)
        self._input_line.setGeometry(10, self._normal_h - 24, _W - 20, 32)
        self._input_line.show()
        self._input_line.setFocus()
        self.update()
        self._drop_next_keypress = True

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._input_line and self._drop_next_keypress:
            if event.type() == QEvent.Type.KeyPress:
                self._drop_next_keypress = False
                return True  # consume the triggering key so it never reaches the field
        return super().eventFilter(obj, event)

    def _fire_custom(self):
        text = self._input_line.text().strip()
        if not text:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        custom_row = next(r for r in self._rows if r["is_custom"])
        self.intent_chosen.emit(custom_row["glyph"], text)
        self.close()

    def _on_raw_key(self, name: str):
        """Runs on Qt main thread (dispatched from keyboard hook thread via signal)."""
        if self._custom_mode:
            return
        if name in ('escape', 'esc'):
            self._cancel()
            return
        for i, row in enumerate(self._rows):
            if name.lower() == row['glyph'].lower():
                self._select(i)
                return

    def keyPressEvent(self, event):
        """Fallback: handles keys if Qt does receive focus."""
        key_map: dict[Qt.Key, int] = {}
        for i, row in enumerate(self._rows):
            qt_key = getattr(Qt.Key, f"Key_{row['glyph']}", None)
            if qt_key is not None:
                key_map[qt_key] = i
        idx = key_map.get(event.key())
        if idx is not None:
            self._select(idx)
        elif event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self._closed = False
        # Use keyboard library hook so keys work regardless of Qt focus/Popup restrictions.
        self._kb_hook = keyboard.on_press(
            lambda e: None if (not e or not e.name or self._closed) else self._raw_key.emit(e.name),
            suppress=False,
        )

    # ------------------------------------------------------------------
    # Cleanup / fire
    # ------------------------------------------------------------------

    def _unhook(self):
        self._closed = True  # guard lambda against late keyboard events after C++ deletion
        if self._kb_hook is not None:
            try:
                keyboard.unhook(self._kb_hook)
            except Exception:
                pass
            self._kb_hook = None
        try:
            self.releaseKeyboard()
        except Exception:
            pass

    def closeEvent(self, event):
        """Unhook keyboard listener whenever the widget closes (including Popup auto-close)."""
        self._unhook()
        super().closeEvent(event)

    def _fire(self, idx: int):
        self._unhook()
        self._timer.stop()
        row = self._rows[idx]
        self.intent_chosen.emit(row["glyph"], row["prompt"])
        self.close()

    def _cancel(self):
        if self._handled:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.cancelled.emit()
        self.close()
