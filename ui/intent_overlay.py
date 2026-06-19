"""
ui/intent_overlay.py — Compact intent picker shown on Ctrl+Q.

Small floating widget centred on screen — no background dim.
Rows are built dynamically from config.INTENT_ROWS plus a fixed
Custom Prompt row (config.HOTKEY_CUSTOM_PROMPT_KEY).
Press the matching key to pick, Escape to cancel.
"""
from __future__ import annotations
import os
import sys
from PySide6.QtWidgets import QWidget, QApplication, QLineEdit, QMenu, QToolTip
from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QRect
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QBrush, QPainterPath, QFontMetrics
import config
from ui.i18n import t

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"
_DEBUG_KEYS = os.environ.get("WISP_INTENT_KEY_DEBUG", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def _key_name(key: int) -> str:
    """Handle key name for UI intent overlay."""
    try:
        return Qt.Key(key).name
    except Exception:
        return str(key)


def _safe_text_desc(text: str) -> str:
    """Handle safe text desc for UI intent overlay."""
    if text == "":
        return "empty"
    if text == " ":
        return "space"
    if text.isspace():
        return "whitespace:" + ",".join(str(ord(ch)) for ch in text)
    return f"printable-len:{len(text)}"


def _event_type_name(event) -> str:
    """Handle event type name for UI intent overlay."""
    try:
        return event.type().name
    except Exception:
        return str(event.type())


def _build_rows(caller_idx: int = 0) -> list[dict]:
    """Build the full list of overlay rows from the specified caller's config."""
    caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
    rows = []
    used_keys: set[str] = set()
    for r in caller.get("intents", []):
        key = str(r.get("key") or "").upper()
        if key:
            used_keys.add(key)
        rows.append({
            "glyph":     key if key else "?",
            "label":     r["label"],
            "hint":      r.get("hint", ""),
            "prompt":    r["prompt"],
            "is_custom": False,
        })
    for r in _addon_intent_rows(caller_idx, used_keys):
        rows.append(r)
    custom_key = str(caller.get("custom_key", "s") or "").strip()
    custom_label = str(caller.get("custom_label") or "").strip() or t("Custom prompt")
    rows.append({
        "glyph":     custom_key.upper(),
        "label":     custom_label,
        "hint":      t("Ask anything"),
        "prompt":    "",
        "is_custom": True,
    })
    return rows


def _addon_intent_rows(caller_idx: int, used_keys: set[str]) -> list[dict]:
    """Handle addon intent rows for UI intent overlay."""
    try:
        from core.addon_manager import get_manager

        manager = get_manager()
        intents = manager.get_intents(caller_idx) if hasattr(manager, "get_intents") else []
    except Exception:
        return []
    rows: list[dict] = []
    for item in intents:
        if not isinstance(item, dict) or item.get("callback"):
            continue
        prompt = str(item.get("prompt") or "").strip()
        label = str(item.get("label") or "").strip()
        if not prompt or not label:
            continue
        key = str(item.get("key") or "").strip().upper()
        if not key or key in used_keys:
            key = _choose_addon_intent_key(label, used_keys)
        used_keys.add(key)
        rows.append({
            "glyph": key,
            "label": label,
            "hint": str(item.get("hint") or f"Addon: {item.get('addon_id', '')}").strip(),
            "prompt": prompt,
            "is_custom": False,
        })
    return rows


def _choose_addon_intent_key(label: str, used_keys: set[str]) -> str:
    """Handle choose addon intent key for UI intent overlay."""
    for char in label.upper():
        if char.isalnum() and char not in used_keys:
            return char
    for char in "ZXCVBNM123456789":
        if char not in used_keys:
            return char
    return "?"


# ── Layout constants ────────────────────────────────────────────────────────
_W             = 520
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
_AUTO_CLOSE_MS = 60000
_INPUT_EXTRA   = 54
_CONV_H        = 38
_CONV_TOP      = 4
_CTX_H         = 92
_CTX_GAP       = 4
_CTX_CHIP_H    = 58
_CTX_CHIP_W    = 68
_CTX_TOP       = 8

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
_CTX_OFF    = QColor(105, 108, 124, 170)
_CTX_ON     = QColor(54, 177, 112, 220)
_CTX_AUTO   = QColor(224, 176, 62, 230)
_CTX_TEXT   = QColor(244, 245, 250, 235)
_CTX_SUB    = QColor(190, 192, 205, 190)
_WARN       = QColor(246, 197, 76, 245)


def _qcolor(value: str | None, fallback: QColor | str, alpha: int | None = None) -> QColor:
    """Parse a theme color with a fallback and optional alpha override."""
    color = QColor(str(value or ""))
    if not color.isValid():
        color = QColor(fallback)
    if alpha is not None:
        color.setAlpha(max(0, min(255, alpha)))
    return color


def _theme_palette() -> dict[str, QColor]:
    """Return intent overlay colors derived from the active settings theme."""
    try:
        from ui.shared.theme import theme_colors

        colors = theme_colors()
    except Exception:
        colors = {}
    return {
        "bg": _qcolor(colors.get("bg"), _BG, 248),
        "border": _qcolor(colors.get("border"), _BORDER, 54),
        "row_hl": _qcolor(colors.get("accent"), _ROW_HL, 24),
        "badge_bg": _qcolor(colors.get("surface"), _BADGE_BG, 255),
        "badge_hl": _qcolor(colors.get("accent"), _BADGE_HL, 64),
        "key": _qcolor(colors.get("accent"), _KEY_COLOR, 240),
        "label": _qcolor(colors.get("text"), _LABEL, 228),
        "hint": _qcolor(colors.get("text_dim"), _HINT, 190),
        "hint_esc": _qcolor(colors.get("text_dim"), _HINT_ESC, 150),
        "sep": _qcolor(colors.get("border"), _SEP, 55),
        "ctx_off": _qcolor(colors.get("text_dim"), _CTX_OFF, 170),
        "ctx_on": _qcolor(colors.get("accent"), _CTX_ON, 230),
        "ctx_auto": _qcolor(colors.get("accent_hover") or colors.get("accent"), _CTX_AUTO, 230),
        "ctx_text": _qcolor(colors.get("text"), _CTX_TEXT, 235),
        "ctx_sub": _qcolor(colors.get("text_dim"), _CTX_SUB, 190),
        "warn": _qcolor(colors.get("accent_hover") or colors.get("accent"), _WARN, 245),
    }


def _context_toggle_keys() -> str:
    """Return seven unique overlay-local context toggle keys."""
    raw = str(getattr(config, "INTENT_CONTEXT_TOGGLE_KEYS", "1234567") or "1234567")
    keys: list[str] = []
    for ch in raw + "1234567":
        if ch.isspace() or ch in keys:
            continue
        keys.append(ch)
        if len(keys) >= 7:
            break
    return "".join(keys)


def _default_context_items() -> list[dict]:
    """Fallback context chips for callers that do not provide live metadata."""
    keys = _context_toggle_keys()
    labels = [
        ("ambient", t("App")),
        ("browser", t("Browser/Web")),
        ("selection", t("Selection")),
        ("clipboard", t("Clipboard")),
        ("screenshot", t("Screenshot")),
        ("memory", t("Memory")),
        ("files", t("Files")),
    ]
    return [
        {
            "id": source,
            "key": keys[idx],
            "label": label,
            "state": "off",
            "default_state": "off",
            "touched": False,
            "tokens": "0 tok",
            "warning": "",
        }
        for idx, (source, label) in enumerate(labels)
    ]


class IntentOverlay(QWidget):
    """Model intent overlay."""
    intent_chosen = Signal(str, str)
    cancelled     = Signal()
    _raw_key      = Signal(str)

    def __init__(
        self,
        caller_idx: int = 0,
        target_hwnd: int = 0,
        context_items: list[dict] | None = None,
        conversation_options: list[dict] | None = None,
        parent=None,
    ):
        """Initialize the intent overlay instance."""
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
        self.setMouseTracking(True)

        self._rows = _build_rows(caller_idx)
        self._context_items = []
        for item in context_items or _default_context_items():
            next_item = dict(item)
            next_item.setdefault("default_state", next_item.get("state", "off"))
            next_item.setdefault("touched", False)
            self._context_items.append(next_item)
        self._conversation_options = self._normalize_conversation_options(conversation_options or [])
        selected = next(
            (item for item in self._conversation_options if item.get("selected")),
            self._conversation_options[0] if self._conversation_options else None,
        )
        self._conversation_mode = "continue" if selected is not None else "new"
        self._conversation_index = int(selected["index"]) if selected is not None else None
        self._conversation_mode_rect = QRect()
        self._conversation_list_rect = QRect()
        self._conversation_menu: QMenu | None = None
        self._warning_rects: list[tuple[QRect, str]] = []
        self._last_warning_idx: int | None = None
        self._auto_custom_mode = self._custom_row_index_without_key()
        n_rows = len(self._rows)
        conversation_h = _CONV_H if self._show_conversation_selector else 0
        context_h = _CTX_H if self._context_items else 0
        h = _PAD_V * 2 + conversation_h + context_h + _ROW_H * n_rows + 26   # 26px ESC hint
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
        self._last_raw_context_key = ""
        self._last_raw_context_at = 0.0
        self._raw_key.connect(self._on_raw_key)

        self._input_line = QLineEdit(self)
        self._input_line.installEventFilter(self)
        self._input_line.setPlaceholderText(t("Type your prompt, press Enter…"))
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
        timeout_ms = max(
            0,
            int(getattr(config, "INTENT_OVERLAY_TIMEOUT_MS", _AUTO_CLOSE_MS) or 0),
        )
        if timeout_ms:
            self._timer.start(timeout_ms)

    def _debug(self, message: str) -> None:
        """Handle debug for intent overlay."""
        if not _DEBUG_KEYS:
            return
        focus = QApplication.focusWidget()
        focus_name = type(focus).__name__ if focus is not None else "None"
        try:
            selection_len = len(self._input_line.selectedText())
            cursor = self._input_line.cursorPosition()
            input_focus = self._input_line.hasFocus()
            input_visible = not self._input_line.isHidden()
        except Exception:
            selection_len = -1
            cursor = -1
            input_focus = False
            input_visible = False
        print(
            "[wisp-intent] "
            f"{message} "
            f"custom={self._custom_mode} drop_next={self._drop_next_keypress} "
            f"input_focus={input_focus} input_visible={input_visible} "
            f"cursor={cursor} selection_len={selection_len} "
            f"focus={focus_name}",
            file=sys.stderr,
            flush=True,
        )

    def _debug_key(self, source: str, event) -> None:
        """Handle debug key for intent overlay."""
        if not _DEBUG_KEYS:
            return
        self._debug(
            f"{source} key={_key_name(int(event.key()))} "
            f"type={_event_type_name(event)} "
            f"text={_safe_text_desc(event.text())} "
            f"mods={int(event.modifiers().value)} accepted={event.isAccepted()}"
        )

    def _resolve_screen_geometry(self) -> QRect:
        """Handle resolve screen geometry for intent overlay."""
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
        """Handle move to screen center for intent overlay."""
        screen = self._screen_geometry
        self.move(
            screen.x() + (screen.width() - _W) // 2,
            screen.y() + (screen.height() - height) // 2,
        )

    def context_choices(self) -> list[dict]:
        """Return the current per-prompt context source states."""
        return [dict(item) for item in self._context_items]

    @staticmethod
    def _normalize_conversation_options(options: list[dict]) -> list[dict]:
        """Return compact history options for the overlay selector."""
        normalized: list[dict] = []
        for raw in options or []:
            if not isinstance(raw, dict):
                continue
            try:
                idx = int(raw.get("index"))
            except (TypeError, ValueError):
                continue
            title = " ".join(str(raw.get("title") or "").split()).strip()
            if not title:
                title = t("Conversation")
            subtitle = " ".join(str(raw.get("subtitle") or "").split()).strip()
            normalized.append(
                {
                    "index": idx,
                    "title": title,
                    "subtitle": subtitle,
                    "selected": bool(raw.get("selected")),
                }
            )
        return normalized

    @property
    def _show_conversation_selector(self) -> bool:
        return True

    def conversation_choice(self) -> dict:
        """Return the selected chat continuation mode for this prompt."""
        if self._conversation_mode == "continue" and self._conversation_index is not None:
            return {"mode": "continue", "index": int(self._conversation_index)}
        return {"mode": "new"}

    def _selected_conversation_option(self) -> dict | None:
        if self._conversation_index is None:
            return None
        for option in self._conversation_options:
            if int(option.get("index", -1)) == int(self._conversation_index):
                return option
        return None

    def _selected_conversation_title(self) -> str:
        option = self._selected_conversation_option()
        if option is None:
            return t("Latest conversation") if self._conversation_options else t("No history yet")
        return str(option.get("title") or t("Conversation"))

    def update_context_items(self, items: list[dict]) -> None:
        """Refresh context chip metadata while preserving user-toggled states."""
        if not items:
            return
        current_by_id = {str(item.get("id") or ""): item for item in self._context_items}
        refreshed: list[dict] = []
        for item in items:
            next_item = dict(item)
            next_item.setdefault("default_state", next_item.get("state", "off"))
            next_item.setdefault("touched", False)
            current = current_by_id.get(str(next_item.get("id") or ""))
            if current is not None:
                touched = bool(current.get("touched", False))
                if touched:
                    next_item["state"] = current.get("state", next_item.get("state", "off"))
                    next_item["default_state"] = current.get(
                        "default_state",
                        next_item.get("default_state", next_item.get("state", "off")),
                    )
                    next_item["touched"] = True
            refreshed.append(next_item)
        self._context_items = refreshed
        self._warning_rects = []
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        """Paint event."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = _theme_palette()

        # Widget background with border
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, _W - 1, self.height() - 1, _RADIUS, _RADIUS)
        p.fillPath(path, QBrush(palette["bg"]))
        p.setPen(QPen(palette["border"], 1))
        p.drawPath(path)

        label_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        hint_font  = QFont("Segoe UI", 8)
        key_font   = QFont("Segoe UI", 12, QFont.Weight.Bold)
        esc_font   = QFont("Segoe UI", 7)
        ctx_label_font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        ctx_state_font = QFont("Segoe UI", 7, QFont.Weight.DemiBold)
        ctx_token_font = QFont("Segoe UI", 7)

        y = _PAD_V
        self._warning_rects = []
        if self._show_conversation_selector:
            self._paint_conversation_selector(p, y, hint_font, ctx_label_font, palette)
            y += _CONV_H
        if self._context_items:
            self._paint_context_items(p, y, ctx_label_font, ctx_state_font, ctx_token_font, palette)
            y += _CTX_H

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
                p.fillPath(rp, QBrush(palette["row_hl"]))

            # Separator above (skip first row)
            if i > 0:
                p.setPen(QPen(palette["sep"], 1))
                p.drawLine(_PAD_H + _BADGE_W + 12, y, _W - _PAD_H, y)

            # Badge background
            badge_y = y + (_ROW_H - _BADGE_H) // 2
            bp = QPainterPath()
            bp.addRoundedRect(_BADGE_X, badge_y, _BADGE_W, _BADGE_H, _BADGE_R, _BADGE_R)
            p.fillPath(bp, QBrush(palette["badge_hl"] if hovered else palette["badge_bg"]))

            # Key letter
            p.setFont(key_font)
            p.setPen(QPen(palette["key"]))
            p.drawText(_BADGE_X, badge_y, _BADGE_W, _BADGE_H,
                       Qt.AlignmentFlag.AlignCenter, row["glyph"])

            # Label
            p.setFont(label_font)
            p.setPen(QPen(palette["label"]))
            text_w = _W - _PAD_H - _TEXT_X
            label_y = y + (_ROW_H // 2) - 12
            p.drawText(_TEXT_X, label_y, text_w, 20,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       row["label"])

            # Subtitle: actual prompt snippet (configured rows) or hint (custom row)
            subtitle = row["hint"] if row["is_custom"] else (row["prompt"] or row["hint"])
            if subtitle:
                p.setFont(hint_font)
                p.setPen(QPen(palette["hint"]))
                hint_y = y + (_ROW_H // 2) + 2
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
        p.setPen(QPen(palette["hint_esc"]))
        p.drawText(0, esc_y, _W, 18, Qt.AlignmentFlag.AlignCenter, t("ESC to cancel"))

        p.end()

    def _paint_conversation_selector(
        self,
        p: QPainter,
        y: int,
        label_font: QFont,
        value_font: QFont,
        palette: dict[str, QColor],
    ) -> None:
        """Paint the chat continuation selector row."""
        top = y + _CONV_TOP
        mode_rect = QRect(_PAD_H, top, 164, 28)
        list_rect = QRect(mode_rect.right() + 6, top, _W - _PAD_H - mode_rect.right() - 6, 28)
        self._conversation_mode_rect = mode_rect
        self._conversation_list_rect = list_rect

        for rect, active in (
            (mode_rect, self._conversation_mode == "continue"),
            (list_rect, self._conversation_mode == "continue" and self._conversation_index is not None),
        ):
            path = QPainterPath()
            path.addRoundedRect(rect, 7, 7)
            bg = QColor(palette["badge_hl"] if active else palette["badge_bg"])
            bg.setAlpha(58 if active else 200)
            p.fillPath(path, QBrush(bg))
            p.setPen(QPen(palette["border"], 1))
            p.drawPath(path)

        mode_text = t("Continuing conversation") if self._conversation_mode == "continue" else t("New conversation")
        p.setFont(label_font)
        p.setPen(QPen(palette["ctx_text"]))
        p.drawText(mode_rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, mode_text)

        value = (
            self._selected_conversation_title()
            if self._conversation_mode == "continue"
            else t("Start a new chat")
        )
        if self._conversation_mode == "continue" and self._conversation_options:
            value += "  ▾"
        p.setFont(value_font)
        p.setPen(QPen(palette["ctx_sub"] if self._conversation_mode == "new" else palette["ctx_text"]))
        elided = QFontMetrics(value_font).elidedText(
            value,
            Qt.TextElideMode.ElideRight,
            max(20, list_rect.width() - 14),
        )
        p.drawText(list_rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)

    def _paint_context_items(
        self,
        p: QPainter,
        y: int,
        label_font: QFont,
        state_font: QFont,
        token_font: QFont,
        palette: dict[str, QColor] | None = None,
    ) -> None:
        """Paint the per-prompt context controls."""
        palette = palette or _theme_palette()
        key_font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        x = _PAD_H
        top = y + _CTX_TOP
        for item in self._context_items:
            rect = QRect(x, top, _CTX_CHIP_W, _CTX_CHIP_H)
            state = str(item.get("state") or "off").lower()
            color = palette["ctx_on"] if state == "on" else (
                palette["ctx_auto"] if state == "auto" else palette["ctx_off"]
            )

            path = QPainterPath()
            path.addRoundedRect(rect, 7, 7)
            bg = QColor(color)
            bg.setAlpha(42 if state == "off" else 56)
            p.fillPath(path, QBrush(bg))
            p.setPen(QPen(color, 1))
            p.drawPath(path)

            key = str(item.get("key") or "")
            if key:
                p.setFont(key_font)
                p.setPen(QPen(color))
                p.drawText(rect.x() + 5, rect.y() + 4, 14, 14, Qt.AlignmentFlag.AlignCenter, key)

            warning = str(item.get("warning") or "").strip()
            if warning:
                warn_rect = QRect(rect.right() - 18, rect.y() + 4, 14, 14)
                self._warning_rects.append((warn_rect, warning))
                p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                p.setPen(QPen(palette["warn"]))
                p.drawText(warn_rect, Qt.AlignmentFlag.AlignCenter, "⚠")

            label = str(item.get("label") or "")
            p.setFont(label_font)
            p.setPen(QPen(palette["ctx_text"]))
            label = QFontMetrics(label_font).elidedText(
                label, Qt.TextElideMode.ElideRight, rect.width() - 12
            )
            p.drawText(rect.x() + 6, rect.y() + 18, rect.width() - 12, 16,
                       Qt.AlignmentFlag.AlignCenter, label)

            state_label = {"on": t("On"), "auto": t("auto"), "off": t("Off")}.get(state, state)
            p.setFont(state_font)
            p.setPen(QPen(color))
            p.drawText(rect.x() + 6, rect.y() + 34, rect.width() - 12, 12,
                       Qt.AlignmentFlag.AlignCenter, state_label)

            tokens = str(item.get("tokens") or "")
            if tokens:
                p.setFont(token_font)
                p.setPen(QPen(palette["ctx_sub"]))
                tokens = QFontMetrics(token_font).elidedText(
                    tokens, Qt.TextElideMode.ElideRight, rect.width() - 8
                )
                p.drawText(rect.x() + 4, rect.y() + 46, rect.width() - 8, 11,
                           Qt.AlignmentFlag.AlignCenter, tokens)

            x += _CTX_CHIP_W + _CTX_GAP
            if x + _CTX_CHIP_W > _W - _PAD_H:
                break

    def _cycle_context_key(self, name: str) -> bool:
        """Cycle a context source when its numeric overlay key is pressed."""
        for item in self._context_items:
            if name.lower() != str(item.get("key") or "").lower():
                continue
            self._cycle_context_item(item)
            return True
        return False

    def _cycle_context_item(self, item: dict) -> None:
        """Cycle one context source through its explicit prompt states."""
        state = str(item.get("state") or "off").lower()
        if state == "auto":
            item["state"] = "off"
        elif state == "off":
            item["state"] = "on"
        else:
            item["state"] = "off"
        item["touched"] = True
        self.update()

    def _context_item_at(self, pos: QPoint) -> dict | None:
        """Return the context chip under a mouse position."""
        if not self._context_items:
            return None
        x = _PAD_H
        top = _PAD_V + (_CONV_H if self._show_conversation_selector else 0) + _CTX_TOP
        for item in self._context_items:
            rect = QRect(x, top, _CTX_CHIP_W, _CTX_CHIP_H)
            if rect.contains(pos):
                return item
            x += _CTX_CHIP_W + _CTX_GAP
            if x + _CTX_CHIP_W > _W - _PAD_H:
                break
        return None

    def _toggle_conversation_mode(self) -> bool:
        """Swap between continuing the selected chat and starting fresh."""
        if self._conversation_mode == "continue":
            self._conversation_mode = "new"
        elif self._conversation_options:
            self._conversation_mode = "continue"
            if self._conversation_index is None:
                self._conversation_index = int(self._conversation_options[0]["index"])
        else:
            self._conversation_mode = "new"
        self.update()
        return True

    def _show_conversation_menu(self) -> None:
        """Open the chat history selector menu."""
        if not self._conversation_options:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: rgba(22,22,32,248); color: #eeeef8; border: 1px solid rgba(255,255,255,30); }"
            "QMenu::item:selected { background: rgba(160,160,255,34); }"
        )
        current = self._conversation_index
        for option in self._conversation_options:
            idx = int(option["index"])
            title = str(option.get("title") or t("Conversation"))
            subtitle = str(option.get("subtitle") or "")
            label = title if not subtitle else f"{title}  ·  {subtitle}"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(idx == current and self._conversation_mode == "continue")
            action.triggered.connect(lambda _checked=False, idx=idx: self._set_conversation_choice(idx))
        self._conversation_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_conversation_menu", None))
        menu.popup(self.mapToGlobal(self._conversation_list_rect.bottomLeft()))

    def _set_conversation_choice(self, idx: int) -> None:
        self._conversation_mode = "continue"
        self._conversation_index = int(idx)
        self.update()

    def _handle_conversation_click(self, pos: QPoint) -> bool:
        """Handle clicks in the continuation selector."""
        if not self._show_conversation_selector:
            return False
        if self._conversation_mode_rect.contains(pos):
            return self._toggle_conversation_mode()
        if self._conversation_list_rect.contains(pos):
            if self._conversation_mode == "new" and self._conversation_options:
                self._conversation_mode = "continue"
            self._show_conversation_menu()
            self.update()
            return True
        return False

    def _cycle_context_at(self, pos: QPoint) -> bool:
        """Cycle a context chip at a mouse position."""
        item = self._context_item_at(pos)
        if item is None:
            return False
        self._cycle_context_item(item)
        return True

    def _mark_raw_context_key(self, name: str) -> None:
        """Remember a raw-hook context key so Qt does not toggle it twice."""
        import time

        self._last_raw_context_key = str(name or "").lower()
        self._last_raw_context_at = time.monotonic()

    def _is_duplicate_qt_context_key(self, name: str) -> bool:
        """Return whether this Qt key press was already handled by the raw hook."""
        import time

        key = str(name or "").lower()
        return (
            bool(key)
            and key == self._last_raw_context_key
            and time.monotonic() - self._last_raw_context_at < 0.18
        )

    def _context_warning_at(self, pos: QPoint) -> tuple[int, str] | None:
        """Return the warning tooltip at a mouse position, if any."""
        for idx, (rect, text) in enumerate(self._warning_rects):
            if rect.contains(pos):
                return idx, text
        return None

    def mouseMoveEvent(self, event):  # noqa: N802
        """Show context warning reasons when hovering the warning sign."""
        found = self._context_warning_at(event.position().toPoint())
        if found is None:
            if self._last_warning_idx is not None:
                QToolTip.hideText()
                self._last_warning_idx = None
            super().mouseMoveEvent(event)
            return
        idx, text = found
        if idx != self._last_warning_idx:
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
            self._last_warning_idx = idx
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        """Toggle context chips when clicked."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._handle_conversation_click(event.position().toPoint()):
                event.accept()
                return
            if self._cycle_context_at(event.position().toPoint()):
                event.accept()
                return
        super().mousePressEvent(event)

    # ── Key input ─────────────────────────────────────────────────────────

    def _select(self, idx: int):
        """Handle select for intent overlay."""
        if self._handled:
            return
        if self._rows[idx]["is_custom"]:
            self._hovered = idx
            self._debug(f"select-custom idx={idx}")
            self._unhook()
            QTimer.singleShot(0, self._enter_custom_mode)
            return
        self._handled = True
        self._hovered = idx
        self.update()
        QTimer.singleShot(80, lambda: self._fire(idx))

    def _custom_row_index_without_key(self) -> int | None:
        """Return the custom row index when it has no configured shortcut."""
        for idx, row in enumerate(self._rows):
            if row["is_custom"] and not row["glyph"]:
                return idx
        return None

    def _enter_auto_custom_mode(self) -> None:
        """Open the custom prompt directly when no custom shortcut is configured."""
        if self._handled or self._custom_mode or self._auto_custom_mode is None:
            return
        self._hovered = self._auto_custom_mode
        self._debug(f"auto-custom idx={self._auto_custom_mode}")
        self._unhook()
        self._enter_custom_mode(drop_trigger_key=False)

    def _enter_custom_mode(self, *, drop_trigger_key: bool = True):
        """Handle enter custom mode for intent overlay."""
        self._custom_mode = True
        self._debug("enter-custom-before")
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
        self._drop_next_keypress = drop_trigger_key
        self._debug("enter-custom-after")
        for delay_ms in (25, 75, 150):
            QTimer.singleShot(delay_ms, self._focus_custom_input)

    def _focus_custom_input(self) -> None:
        """Focus custom input."""
        if not self._custom_mode or self._input_line.isHidden():
            return
        if self._input_line.hasFocus() and (not _IS_WIN or self._input_grabbed_keyboard):
            return
        self._debug("focus-custom-before")
        self.raise_()
        if _IS_WIN:
            self._win_force_foreground()
        self.activateWindow()
        self._input_line.setFocus(Qt.FocusReason.OtherFocusReason)
        if _IS_WIN and not self._input_grabbed_keyboard:
            try:
                self._input_line.grabKeyboard()
                self._input_grabbed_keyboard = True
            except Exception:
                pass
        self._debug("focus-custom-after")

    def _focus_overlay(self) -> None:
        """Give the picker itself keyboard focus when not typing a custom prompt."""
        if self._custom_mode:
            return
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._debug("focus-overlay")

    def changeEvent(self, event):
        """Handle change event for intent overlay."""
        from PySide6.QtCore import QEvent
        # Once we've gained activation, cancel when the window loses it (user
        # clicked another app/window). macOS needs this because it cannot use
        # Popup here; Windows/Linux need it when custom prompt mode grabs keys.
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_activated = True
            elif self._was_activated and not self._handled:
                QTimer.singleShot(0, self._cancel_if_focus_left)
        super().changeEvent(event)

    def eventFilter(self, obj, event):
        """Handle event filter for intent overlay."""
        from PySide6.QtCore import QEvent
        if obj is self._input_line and event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self._cancel_if_focus_left)
        if obj is self._input_line and event.type() in {
            QEvent.Type.KeyPress,
            QEvent.Type.ShortcutOverride,
        }:
            self._debug_key("input-filter-key", event)
            if self._drop_next_keypress and event.type() == QEvent.Type.KeyPress:
                self._debug_key("input-filter-before-drop", event)
                custom_key = next((r["glyph"].lower() for r in self._rows if r["is_custom"]), "")
                if event.text().lower() == custom_key:
                    self._drop_next_keypress = False
                    self._debug_key("input-filter-drop-trigger", event)
                    return True  # consume the triggering key so it never reaches the field
                else:
                    self._drop_next_keypress = False  # not the trigger key — let it through
                    self._debug_key("input-filter-pass-first-key", event)
        return super().eventFilter(obj, event)

    def _cancel_if_focus_left(self) -> None:
        """Cancel when focus has moved outside the overlay."""
        if self._handled or not self.isVisible():
            return
        if self._conversation_menu is not None and self._conversation_menu.isVisible():
            return
        focus = QApplication.focusWidget()
        if focus is not None and (focus is self or self.isAncestorOf(focus)):
            return
        self._debug("focus-left-cancel")
        self._cancel()

    def _fire_custom(self):
        """Handle fire custom for intent overlay."""
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
        """Handle raw key events."""
        if self._custom_mode:
            return
        if name in ('escape', 'esc'):
            self._cancel()
            return
        if self._cycle_context_key(name):
            self._mark_raw_context_key(name)
            return
        for i, row in enumerate(self._rows):
            if name.lower() == row['glyph'].lower():
                self._select(i)
                return

    def keyPressEvent(self, event):
        """Handle key press event for intent overlay."""
        self._debug_key("overlay-keypress", event)
        key_map: dict[Qt.Key, int] = {}
        for i, row in enumerate(self._rows):
            qt_key = getattr(Qt.Key, f"Key_{row['glyph']}", None)
            if qt_key is not None:
                key_map[qt_key] = i
        text = (event.text() or "").strip()
        if text and self._is_duplicate_qt_context_key(text):
            event.accept()
            return
        if text and self._cycle_context_key(text):
            event.accept()
            return
        idx = key_map.get(event.key())
        if idx is not None:
            self._select(idx)
        elif event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _win_force_foreground(self) -> None:
        """Force the overlay to the foreground on Windows, past the foreground lock.

        The hotkey is received by the native worker, but THIS (UI) process shows
        the window — so Windows denies plain SetForegroundWindow/activateWindow and
        the overlay never gets keyboard focus (you'd have to click it before WASD
        works). Briefly attaching to the current foreground thread's input queue
        lifts that restriction for the duration of the call. Best-effort; never
        raises.
        """
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(self.winId())
            fg = user32.GetForegroundWindow()
            if not hwnd or fg == hwnd:
                return
            target_tid = user32.GetWindowThreadProcessId(fg, None)
            our_tid = kernel32.GetCurrentThreadId()
            attached = bool(user32.AttachThreadInput(target_tid, our_tid, True))
            try:
                user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetFocus(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(target_tid, our_tid, False)
        except Exception:
            pass

    def showEvent(self, event):
        """Show event."""
        super().showEvent(event)
        self.raise_()
        if not _IS_WIN:
            self.activateWindow()
            self._focus_overlay()
        self._closed = False
        self._debug("show")
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
                """Handle press events."""
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
        if self._auto_custom_mode is not None:
            QTimer.singleShot(0, self._enter_auto_custom_mode)
        elif _IS_WIN:
            for delay_ms in (25, 75, 150):
                QTimer.singleShot(delay_ms, self._focus_overlay)

    # ── Cleanup / fire ────────────────────────────────────────────────────

    def _unhook(self):
        """Handle unhook for intent overlay."""
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
        """Close event."""
        self._unhook()
        super().closeEvent(event)

    def _fire(self, idx: int):
        """Handle fire for intent overlay."""
        self._unhook()
        self._timer.stop()
        row = self._rows[idx]
        self.intent_chosen.emit(row["glyph"], row["prompt"])
        self.close()

    def _cancel(self):
        """Cancel the intent overlay workflow."""
        if self._handled:
            return
        self._handled = True
        self._unhook()
        self._timer.stop()
        self.cancelled.emit()
        self.close()
