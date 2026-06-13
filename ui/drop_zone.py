"""
ui/drop_zone.py -- Drag-and-drop context panel for the icon.

Components:
  VanishEffect       -- particle burst animation at the cursor on drop
  AddedContextToast  -- "Added as context!" label that fades above the icon
  ContextBadge       -- single row badge showing one queued context item (with X to remove)
  ContextPanel       -- frameless always-on panel to the right of the icon
  process_drop_mime  -- converts dropped MIME data to (name, content, type) items
"""
from __future__ import annotations
import math
import os
import base64
from typing import Callable, TYPE_CHECKING

from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QRect, QRectF,
    QPropertyAnimation, QEasingCurve, QMimeData,
)
from PySide6.QtGui import QPainter, QColor, QFont, QBrush, QPen

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml",
    ".yml", ".csv", ".html", ".htm", ".css", ".xml", ".sh", ".bat", ".ps1",
    ".c", ".cpp", ".h", ".java", ".rs", ".go", ".rb", ".php", ".sql",
    ".toml", ".ini", ".cfg", ".conf", ".log",
}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
_DOCUMENT_EXTS = {".docx", ".pdf", ".xlsx", ".xls", ".pptx", ".odt", ".ods", ".odp"}

_MAX_TEXT_BYTES = 51_200  # 50 KB cap when reading text files

_TYPE_COLORS = {
    "image": QColor(180,  90, 255),
    "text":  QColor( 77, 163, 255),
    "file":  QColor( 80, 220, 140),
}

_BADGE_W   = 172   # wider to accommodate X button
_BADGE_H   = 28
_BADGE_GAP = 5
_DOT_R     = 5
_X_W       = 22    # width of the remove button hit area
_ZONE_H    = 64    # placeholder panel height when empty


# ---------------------------------------------------------------------------
# process_drop_mime -- pure data extraction, no Qt UI
# ---------------------------------------------------------------------------

def process_drop_mime(mime: QMimeData) -> list[tuple[str, str, str]]:
    """
    Extract context items from a QMimeData object.

    Returns a list of (display_name, content, item_type) tuples where
    item_type is "text", "image", or "file".
    """
    items: list[tuple[str, str, str]] = []

    if mime.hasUrls():
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            name = os.path.basename(path)
            ext  = os.path.splitext(name)[1].lower()

            if ext in _IMAGE_EXTS:
                try:
                    with open(path, "rb") as fh:
                        data = base64.b64encode(fh.read()).decode()
                    items.append((name, data, "image"))
                except OSError:
                    items.append((name, f"[Image file: {path}]", "file"))

            elif ext in _TEXT_EXTS or ext == "":
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read(_MAX_TEXT_BYTES)
                    items.append((name, content, "text"))
                except OSError:
                    items.append((name, f"[File: {path}]", "file"))

            elif ext in _DOCUMENT_EXTS:
                # Pass the path; main.py will read the content in the worker thread.
                items.append((name, path, "document_path"))

            else:
                items.append((name, f"[File: {path}]", "file"))

    elif mime.hasText():
        text = mime.text().strip()
        if text:
            preview = text[:28].replace("\n", " ")
            if len(text) > 28:
                preview += "…"
            items.append((f'"{preview}"', text, "text"))

    elif mime.hasImage():
        image = mime.imageData()
        if image is not None and not image.isNull():
            from PySide6.QtCore import QByteArray, QBuffer
            ba  = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            image.save(buf, "PNG")
            data = base64.b64encode(bytes(ba)).decode()
            items.append(("Pasted image", data, "image"))

    return items


# ---------------------------------------------------------------------------
# VanishEffect
# ---------------------------------------------------------------------------

class VanishEffect(QWidget):
    """Particle-burst animation at the cursor when a drop is accepted."""

    _SIZE     = 90
    _FRAMES   = 18
    _INTERVAL = 28   # ~36 fps -> ~504 ms total

    def __init__(self, global_pos: QPoint):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.move(
            global_pos.x() - self._SIZE // 2,
            global_pos.y() - self._SIZE // 2,
        )
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._INTERVAL)
        self.show()

    def _tick(self) -> None:
        self._frame += 1
        if self._frame >= self._FRAMES:
            self._timer.stop()
            self.close()
            return
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = self._frame / self._FRAMES
        c = self._SIZE // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Outer expanding ring
        ring_r = int(t * 38)
        alpha  = int(255 * (1.0 - t))
        p.setPen(QPen(QColor(255, 210, 80, alpha), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c - ring_r, c - ring_r, ring_r * 2, ring_r * 2)

        # Inner ring with slight delay
        if t > 0.15:
            t2 = (t - 0.15) / 0.85
            r2 = int(t2 * 22)
            a2 = int(200 * (1.0 - t2))
            p.setPen(QPen(QColor(200, 120, 255, a2), 1))
            p.drawEllipse(c - r2, c - r2, r2 * 2, r2 * 2)

        # 8 particles flying outward
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            angle = i * (math.pi * 2 / 8) + t * 0.5
            r     = int(t * 34)
            px    = c + int(math.cos(angle) * r)
            py    = c + int(math.sin(angle) * r)
            ap    = int(255 * max(0.0, 1.0 - t * 1.4))
            sz    = max(2, int(5 * (1.0 - t)))
            color = QColor(255, 200 + int(40 * math.sin(i)), 80, ap)
            p.setBrush(QBrush(color))
            p.drawEllipse(px - sz // 2, py - sz // 2, sz, sz)

        p.end()


# ---------------------------------------------------------------------------
# AddedContextToast
# ---------------------------------------------------------------------------

class AddedContextToast(QWidget):
    """'Added as context!' label that appears above the icon and fades out."""

    def __init__(self, icon_pos: QPoint, icon_size: int):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        from PySide6.QtWidgets import QLabel
        lbl = QLabel("Added as context!", self)
        lbl.setFont(QFont("Segoe UI", 9))
        lbl.setStyleSheet(
            "color: rgba(220,220,255,230);"
            "background: rgba(35,35,58,195);"
            "border-radius: 8px;"
            "padding: 4px 10px;"
        )
        lbl.adjustSize()
        self.resize(lbl.size())

        # Centre above the icon
        x = icon_pos.x() + icon_size // 2 - self.width() // 2
        y = icon_pos.y() - self.height() - 8
        self.move(x, y)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(1.0)
        self.show()

        # Hold 700 ms, then fade over 500 ms
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)
        self._hold.start(700)

    def _fade_out(self) -> None:
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(500)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._anim.finished.connect(self.close)
        self._anim.start()


# ---------------------------------------------------------------------------
# ContextBadge
# ---------------------------------------------------------------------------

class ContextBadge(QWidget):
    """Single rounded-rect badge showing one queued context item with an X to remove."""

    def __init__(
        self,
        display_name: str,
        item_type: str,
        on_remove: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        removable: bool = True,
    ):
        super().__init__(parent)
        self._name      = display_name
        self._type      = item_type
        self._on_remove = on_remove
        self._removable = removable   # False = read-only "this was sent" badge (no X)
        self._hovered   = False   # is the X button hovered?
        self._removing  = False   # guard against double-remove

        self.setFixedSize(_BADGE_W, _BADGE_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(removable)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fade_out(self, callback: Callable[[], None]) -> None:
        """Fade to transparent then call callback."""
        if self._removing:
            return
        self._removing = True
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(200)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._anim.finished.connect(callback)
        self._anim.start()

    # ------------------------------------------------------------------
    # Internal geometry
    # ------------------------------------------------------------------

    @staticmethod
    def _x_rect() -> QRect:
        return QRect(_BADGE_W - _X_W, 0, _X_W, _BADGE_H)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        if not self._removable:
            return
        hover = self._x_rect().contains(event.pos())
        if hover != self._hovered:
            self._hovered = hover
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if hover else Qt.CursorShape.ArrowCursor
            )
            self.update()

    def leaveEvent(self, _event) -> None:
        if self._hovered:
            self._hovered = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event) -> None:
        if (
            self._removable
            and event.button() == Qt.MouseButton.LeftButton
            and self._x_rect().contains(event.pos())
            and not self._removing
            and callable(self._on_remove)
        ):
            self._on_remove()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background pill
        p.setBrush(QBrush(QColor(28, 28, 48, 210)))
        p.setPen(QPen(QColor(80, 80, 128, 160), 1))
        p.drawRoundedRect(0, 0, _BADGE_W, _BADGE_H, 6, 6)

        # Type indicator dot
        dot = _TYPE_COLORS.get(self._type, QColor(150, 150, 150))
        p.setBrush(QBrush(dot))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(8, _BADGE_H // 2 - _DOT_R // 2, _DOT_R, _DOT_R)

        # Display name (leaves room for the X button only when removable)
        text_x = 8 + _DOT_R + 6
        reserved = _X_W if self._removable else 8
        text_w = _BADGE_W - text_x - reserved - 2
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(210, 210, 235, 225))
        p.drawText(
            QRectF(text_x, 0, text_w, _BADGE_H),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._name,
        )

        if not self._removable:
            p.end()
            return

        # X button
        x_alpha = 220 if self._hovered else 110
        x_bg    = QColor(200, 60, 60, 60) if self._hovered else QColor(0, 0, 0, 0)
        p.setBrush(QBrush(x_bg))
        p.setPen(Qt.PenStyle.NoPen)
        xr = self._x_rect()
        p.drawRoundedRect(xr.adjusted(2, 3, -2, -3), 4, 4)

        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.setPen(QColor(220, 100, 100, x_alpha))
        p.drawText(
            QRectF(xr),
            Qt.AlignmentFlag.AlignCenter,
            "×",   # × multiplication sign
        )

        p.end()


# ---------------------------------------------------------------------------
# ContextPanel
# ---------------------------------------------------------------------------

class ContextPanel(QWidget):
    """
    Frameless always-on-top panel to the right of the icon.
    Shows a translucent drop-zone placeholder when empty,
    or stacked ContextBadge widgets (each with an X to remove) when items are queued.
    """

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._badges: list[ContextBadge] = []
        self._on_remove_item: Callable[[int], None] | None = None
        self._icon_pos  = QPoint(0, 0)
        self._icon_size = 80
        self._drag_active = False
        self._summary_mode = False   # showing read-only "context sent" badges
        self._summary_timer = QTimer(self)
        self._summary_timer.setSingleShot(True)
        self._summary_timer.timeout.connect(self.clear_summary)
        self.setFixedWidth(_BADGE_W)
        self.resize(_BADGE_W, _ZONE_H)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_remove_callback(self, cb: Callable[[int], None]) -> None:
        """Register callback called with the item index when an X is clicked."""
        self._on_remove_item = cb

    def add_item(self, display_name: str, item_type: str) -> None:
        if self._summary_mode:
            self._clear_badges()           # a real drop supersedes the sent-summary
            self._summary_mode = False
        idx = len(self._badges)
        badge = ContextBadge(
            display_name,
            item_type,
            on_remove=lambda b=None, i=idx: self._badge_remove_clicked(i),
            parent=self,
        )
        self._badges.append(badge)
        self._relayout()
        self._update_visibility()

    def show_context_summary(self, items: list[tuple[str, str]], timeout_ms: int = 120000) -> None:
        """Display read-only badges of the context attached to the current prompt
        (selection, dropped files, clipboard, active document, ...). They sit to
        the right of the icon just like dropped items, but carry no X button and
        clear when the reply ends (clear_summary) or after a backstop timeout."""
        self._summary_timer.stop()
        self._clear_badges()
        self._summary_mode = True
        for name, item_type in items:
            badge = ContextBadge(name, item_type, on_remove=None, parent=self, removable=False)
            self._badges.append(badge)
        self._relayout()
        self._update_visibility()
        if timeout_ms > 0:
            self._summary_timer.start(timeout_ms)

    def clear_summary(self) -> None:
        """Remove the read-only context-summary badges (no-op for dropped items)."""
        self._summary_timer.stop()
        if not self._summary_mode:
            return
        self._summary_mode = False
        self._clear_badges()
        self._relayout()
        self._update_visibility()

    def _clear_badges(self) -> None:
        for b in self._badges:
            b.deleteLater()
        self._badges.clear()

    def clear_items(self) -> None:
        self._summary_timer.stop()
        self._summary_mode = False
        self._clear_badges()
        self._relayout()
        self._update_visibility()

    def reposition(self, icon_pos: QPoint, icon_size: int) -> None:
        self._icon_pos  = icon_pos
        self._icon_size = icon_size
        self._relayout()
        self._update_visibility()

    def set_drag_active(self, active: bool) -> None:
        """Show drop-zone overlay during a drag, hide it when drag ends (unless badges exist)."""
        if active and self._summary_mode:
            self.clear_summary()   # a new drag supersedes the sent-context summary
        self._drag_active = active
        self._relayout()
        self._update_visibility()

    def _update_visibility(self) -> None:
        should_show = self._drag_active or bool(self._badges)
        if should_show and not self.isVisible():
            self.show()
        elif not should_show and self.isVisible():
            self.hide()

    # ------------------------------------------------------------------
    # Badge removal
    # ------------------------------------------------------------------

    def _badge_remove_clicked(self, clicked_idx: int) -> None:
        """Find the badge at clicked_idx, animate it out, then remove it."""
        if clicked_idx >= len(self._badges):
            return
        badge = self._badges[clicked_idx]
        # Capture the *current* index at click time for the data callback
        current_idx = self._badges.index(badge)
        badge.fade_out(lambda: self._finish_remove(badge, current_idx))

    def _finish_remove(self, badge: ContextBadge, data_idx: int) -> None:
        if badge in self._badges:
            self._badges.remove(badge)
        badge.deleteLater()
        self._relayout()
        # Re-bind remaining badges' remove lambdas to their new indices
        self._rebind_remove_callbacks()
        self._update_visibility()
        if self._on_remove_item is not None:
            self._on_remove_item(data_idx)

    def _rebind_remove_callbacks(self) -> None:
        """Update each badge's on_remove to reflect its current index."""
        for i, badge in enumerate(self._badges):
            badge._on_remove = lambda b=badge, idx=i: self._badge_remove_clicked(idx)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _relayout(self) -> None:
        n = len(self._badges)
        if n == 0:
            panel_h = _ZONE_H
        else:
            panel_h = n * (_BADGE_H + _BADGE_GAP) - _BADGE_GAP
            panel_h = max(panel_h, _ZONE_H)

        self.resize(_BADGE_W, panel_h)

        for i, badge in enumerate(self._badges):
            badge.move(0, i * (_BADGE_H + _BADGE_GAP))
            badge.show()

        # Sit just to the right of the icon (matches the bubble's right-side
        # offset pattern in bubble.icon_pos_for_bubble), not centred over it.
        x = self._icon_pos.x() + self._icon_size + _BADGE_GAP
        if not self._badges:
            y = self._icon_pos.y() + self._icon_size // 2 - panel_h // 2
        else:
            y = self._icon_pos.y() + self._icon_size // 2 - _ZONE_H // 2
        self.move(x, y)
        self.update()

    # ------------------------------------------------------------------
    # Paint -- drop-zone background + placeholder text
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        if self._badges or not self._drag_active:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Translucent dashed border while the user is actively dragging context.
        dash_pen = QPen(QColor(120, 120, 200, 65), 1.5, Qt.PenStyle.DashLine)
        dash_pen.setDashPattern([4, 4])
        p.setPen(dash_pen)
        p.setBrush(QBrush(QColor(18, 18, 45, 22)))
        p.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 10, 10)

        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(150, 150, 220, 85))
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "↓ Drop here",
        )

        p.end()
