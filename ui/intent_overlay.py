"""
ui/intent_overlay.py — Compact intent picker shown on Ctrl+Q.

Small floating widget centred on screen — no background dim.
Rows are built dynamically from config.INTENT_ROWS plus a fixed
Custom Prompt row (config.HOTKEY_CUSTOM_PROMPT_KEY).
Press the matching key to pick, Escape to cancel.
"""
from __future__ import annotations
import sys
from PySide6.QtWidgets import QWidget, QApplication, QLineEdit
from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QRect
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QBrush, QPainterPath, QKeyEvent
import config

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"


def _build_rows(caller_idx: int = 0) -> list[dict]:
    """Build the full list of overlay rows from the specified caller's config."""
    caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
    rows = []
    for r in caller.get("intents", []):
        rows.append({
            "glyph":     r["key"].upper() if r["key"] else "?",
            "label":     r["label"],
            "hint":      r.get("hint", ""),
            "prompt":    r["prompt"],
            "is_custom": False,
        })
    custom_key = caller.get("custom_key", "s")
    rows.append({
        "glyph":     custom_key.upper(),
        "label":     "Custom prompt",
        "hint":      "Ask anything",
        "prompt":    "",
        "is_custom": True,
    })
    return rows


# ── Layout constants ────────────────────────────────────────────────────────
_W             = 300
_ROW_H         = 64
_PAD_V         = 10       # vertical padding around all rows
_PAD_H         = 10       # horizontal margin inside the widget
_RADIUS        = 14       # widget corner radius
_ROW_RADIUS    = 9        # per-row highlight corner radius
_BADGE_W       = 38
_BADGE_H       = 38
_BADGE_R       = 8        # badge corner radius
_BADGE_X       = 12       # badge left offset inside row
_TEXT_X        = _BADGE_X + _BADGE_W + 12
_AUTO_CLOSE_MS = 5000
_INPUT_EXTRA   = 54

# ── Palette ─────────────────────────────────────────────────────────────────
_BG         = QColor(20, 20, 30, 248)
_BORDER     = QColor(255, 255, 255, 18)
_ROW_HL     = QColor(255, 255, 255, 16)
_BADGE_BG   = QColor(38, 38, 54, 255)
_BADGE_HL   = QColor(100, 90, 200, 60)   # tinted badge on hover
_KEY_COLOR  = QColor(155, 140, 255, 240) # accent purple
_LABEL      = QColor(238, 238, 250, 228)
_HINT       = QColor(135, 130, 160, 180)
_HINT_ESC   = QColor(100, 96, 118, 140)
_SEP        = QColor(255, 255, 255, 14)


class _PromptLineEdit(QLineEdit):
    """Line edit that keeps text input normal inside transient overlay windows."""

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Space and not event.text():
            normalized = QKeyEvent(
                event.type(),
                event.key(),
                event.modifiers(),
                " ",
                event.isAutoRepeat(),
                event.count(),
            )
            super().keyPressEvent(normalized)
            event.setAccepted(normalized.isAccepted())
            return
        super().keyPressEvent(event)


class IntentOverlay(QWidget):
    intent_chosen = Signal(str, str)
    cancelled     = Signal()
    _raw_key      = Signal(str)

    def __init__(self, caller_idx: int = 0, target_hwnd: int = 0, parent=None):
        super().__init__(parent)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if not _IS_MAC:
            # Popup gives click-outside-to-dismiss on Win/Linux, where we read
            # keys via a global hook. On macOS a Popup window cannot become the
            # key window, so the QLineEdit / keyPressEvent would never receive
            # input — use a plain frameless top-level (Qt overrides
            # canBecomeKeyWindow for it) and dismiss via Esc / focus-out / timer.
            flags |= Qt.WindowType.Popup
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._rows = _build_rows(caller_idx)
        n_rows = len(self._rows)
        h = _PAD_V * 2 + _ROW_H * n_rows + 26   # 26px ESC hint
        self._normal_h = h
        self.setFixedSize(_W, h)
        self._target_hwnd = target_hwnd
        self._screen_geometry = self._resolve_screen_geometry()

        self._move_to_screen_center(h)

        self._hovered: int | None = None
        self._handled = False
        self._custom_mode = False
        self._was_activated = False   # macOS: dismiss on focus-out once activated
        self._kb_hook = None
        self._input_grabbed_keyboard = False
        self._drop_next_keypress = False
        self._custom_input_started = False
        self._raw_key.connect(self._on_raw_key)

        self._input_line = _PromptLineEdit(self)
        self._input_line.installEventFilter(self)
        self._input_line.setPlaceholderText("Type your prompt, press Enter…")
        self._input_line.setStyleSheet(
            "QLineEdit {"
            "  background: rgba(255,255,255,10);"
            "  border: 1px solid rgba(155,140,255,80);"
            "  border-radius: 6px;"
            "  color: #eeeef8;"
            "  padding: 4px 10px;"
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
                from PySide6.QtGui import QCursor
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

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Widget background with border
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, _W - 1, self.height() - 1, _RADIUS, _RADIUS)
        p.fillPath(path, QBrush(_BG))
        p.setPen(QPen(_BORDER, 1))
        p.drawPath(path)

        label_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        hint_font  = QFont("Segoe UI", 8)
        key_font   = QFont("Segoe UI", 12, QFont.Weight.Bold)
        esc_font   = QFont("Segoe UI", 7)

        y = _PAD_V
        for i, row in enumerate(self._rows):
            row_rect = QRect(_PAD_H, y, _W - _PAD_H * 2, _ROW_H)
            hovered  = (i == self._hovered)

            # Row highlight
            if hovered:
                rp = QPainterPath()
                rp.addRoundedRect(
                    _PAD_H, y, _W - _PAD_H * 2, _ROW_H,
                    _ROW_RADIUS, _ROW_RADIUS,
                )
                p.fillPath(rp, QBrush(_ROW_HL))

            # Separator above (skip first row)
            if i > 0:
                p.setPen(QPen(_SEP, 1))
                p.drawLine(_PAD_H + _BADGE_W + 12, y, _W - _PAD_H, y)

            # Badge background
            badge_y = y + (_ROW_H - _BADGE_H) // 2
            bp = QPainterPath()
            bp.addRoundedRect(_BADGE_X, badge_y, _BADGE_W, _BADGE_H, _BADGE_R, _BADGE_R)
            p.fillPath(bp, QBrush(_BADGE_HL if hovered else _BADGE_BG))

            # Key letter
            p.setFont(key_font)
            p.setPen(QPen(_KEY_COLOR))
            p.drawText(_BADGE_X, badge_y, _BADGE_W, _BADGE_H,
                       Qt.AlignmentFlag.AlignCenter, row["glyph"])

            # Label
            p.setFont(label_font)
            p.setPen(QPen(_LABEL))
            text_w = _W - _PAD_H - _TEXT_X
            label_y = y + (_ROW_H // 2) - 12
            p.drawText(_TEXT_X, label_y, text_w, 20,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       row["label"])

            # Subtitle: actual prompt snippet (configured rows) or hint (custom row)
            subtitle = row["hint"] if row["is_custom"] else (row["prompt"] or row["hint"])
            if subtitle:
                p.setFont(hint_font)
                p.setPen(QPen(_HINT))
                hint_y = y + (_ROW_H // 2) + 2
                from PySide6.QtGui import QFontMetrics
                elided = QFontMetrics(hint_font).elidedText(
                    subtitle, Qt.TextElideMode.ElideRight, text_w
                )
                p.drawText(_TEXT_X, hint_y, text_w, 18,
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           elided)

            y += _ROW_H

        # ESC hint
        if self._custom_mode:
            esc_y = y + _INPUT_EXTRA + 4
        else:
            esc_y = y + 4
        p.setFont(esc_font)
        p.setPen(QPen(_HINT_ESC))
        p.drawText(0, esc_y, _W, 18, Qt.AlignmentFlag.AlignCenter, "ESC to cancel")

        p.end()

    # ── Key input ─────────────────────────────────────────────────────────

    def _select(self, idx: int):
        if self._handled:
            return
        if self._rows[idx]["is_custom"]:
            self._hovered = idx
            self._unhook()
            QTimer.singleShot(0, self._enter_custom_mode)
            return
        self._handled = True
        self._hovered = idx
        self.update()
        QTimer.singleShot(80, lambda: self._fire(idx))

    def _enter_custom_mode(self):
        self._custom_mode = True
        self._custom_input_started = False
        self._timer.stop()
        new_h = self._normal_h + _INPUT_EXTRA
        self.setFixedSize(_W, new_h)
        self._move_to_screen_center(new_h)
        self._input_line.setGeometry(
            _PAD_H, self._normal_h - 20, _W - _PAD_H * 2, 34
        )
        self._input_line.show()
        self._focus_custom_input()
        self.update()
        self._drop_next_keypress = True
        for delay_ms in (25, 75, 150):
            QTimer.singleShot(delay_ms, self._retry_focus_custom_input)

    def _focus_custom_input(self) -> None:
        if not self._custom_mode or self._input_line.isHidden():
            return
        self.raise_()
        self.activateWindow()
        if not self._input_line.hasFocus():
            self._input_line.setFocus(Qt.FocusReason.OtherFocusReason)
        if _IS_WIN and not self._input_grabbed_keyboard:
            try:
                self._input_line.grabKeyboard()
                self._input_grabbed_keyboard = True
            except Exception:
                pass

    def _retry_focus_custom_input(self) -> None:
        if self._custom_input_started:
            return
        self._focus_custom_input()

    def _forward_key_to_custom_input(self, event) -> None:
        self._focus_custom_input()
        forwarded = QKeyEvent(
            event.type(),
            event.key(),
            event.modifiers(),
            event.text(),
            event.isAutoRepeat(),
            event.count(),
        )
        QApplication.sendEvent(self._input_line, forwarded)

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        # macOS lacks the Popup flag (it blocks key-window status), so emulate
        # Popup's click-outside dismissal: once we've gained activation, cancel
        # when the window loses it (user clicked another app/window).
        if _IS_MAC and event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_activated = True
            elif self._was_activated and not self._handled:
                self._cancel()
        super().changeEvent(event)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._input_line and event.type() == QEvent.Type.KeyPress:
            if self._drop_next_keypress:
                custom_key = next((r["glyph"].lower() for r in self._rows if r["is_custom"]), "")
                if event.text().lower() == custom_key:
                    self._drop_next_keypress = False
                    return True  # consume the triggering key so it never reaches the field
                else:
                    self._drop_next_keypress = False  # not the trigger key — let it through
            if event.key() not in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._custom_input_started = True
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
        if self._custom_mode:
            if event.key() == Qt.Key.Key_Escape:
                self._cancel()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._fire_custom()
                event.accept()
                return
            self._custom_input_started = True
            self._forward_key_to_custom_input(event)
            event.accept()
            return
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
        if _IS_WIN:
            import keyboard  # type: ignore
            self._kb_hook = keyboard.on_press(
                lambda e: None if (not e or not e.name or self._closed) else self._raw_key.emit(e.name),
                suppress=False,
            )
        elif _IS_MAC:
            # No global keyboard hook on macOS: pynput's Listener installs a
            # CGEventTap that decodes keystrokes via main-thread-only HIToolbox
            # APIs on its own thread, which trace-traps (SIGTRAP). This Popup has
            # StrongFocus and we just activated it, so Qt delivers keys straight
            # to keyPressEvent on the main thread — and the custom-input QLineEdit
            # keeps working (grabKeyboard would have stolen its keystrokes).
            self._kb_hook = None
            self.setFocus(Qt.FocusReason.PopupFocusReason)
        else:
            from pynput import keyboard as _kb  # type: ignore

            def _on_press(key):
                if self._closed:
                    return
                try:
                    name = key.char if (hasattr(key, "char") and key.char) else key.name
                except AttributeError:
                    return
                if name:
                    self._raw_key.emit(name.lower())

            listener = _kb.Listener(on_press=_on_press)
            listener.daemon = True
            listener.start()
            self._kb_hook = listener

    # ── Cleanup / fire ────────────────────────────────────────────────────

    def _unhook(self):
        self._closed = True
        if self._kb_hook is not None:
            try:
                if _IS_WIN:
                    import keyboard  # type: ignore
                    keyboard.unhook(self._kb_hook)
                else:
                    self._kb_hook.stop()
            except Exception:
                pass
            self._kb_hook = None
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        try:
            self._input_line.releaseKeyboard()
        except Exception:
            pass
        self._input_grabbed_keyboard = False

    def closeEvent(self, event):
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
