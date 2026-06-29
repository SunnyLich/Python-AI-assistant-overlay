"""
ui/snip_overlay.py — Full-screen region selector (Ctrl+Alt+Q).

Covers the entire virtual desktop (all monitors) with a semi-dark overlay.
The user clicks and drags to select a region; the widget emits
region_selected with an mss-compatible region dict on release,
or cancelled on Escape / zero-size drag.
"""
from __future__ import annotations
import sys
import time
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QCursor, QPen

_DIM_ALPHA = 110   # overlay darkness 0–255


class SnipOverlay(QWidget):
    """Model snip overlay."""
    region_selected = Signal(dict)   # mss-format: {left, top, width, height}
    cancelled       = Signal()

    def __init__(self, parent=None):
        """Initialize the snip overlay instance."""
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

        # Cover the full virtual desktop (all monitors combined)
        vg = QRect()
        for screen in QApplication.screens():
            vg = vg.united(screen.geometry())
        self.setGeometry(vg)
        self._virtual_origin = vg.topLeft()

        self._t_created = time.monotonic()
        self._first_paint_logged = False
        self._origin: QPoint | None = None
        # Current drag rectangle, drawn directly in paintEvent. We deliberately
        # avoid QRubberBand here: on macOS it is backed by its own native window,
        # and a child window on a translucent Qt.Tool overlay aborts Cocoa
        # (SIGABRT) the moment the selector is shown.
        self._sel_rect: QRect | None = None

    # ------------------------------------------------------------------
    # Paint — dim the whole screen; rubber band provides the selection box
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        """Paint event."""
        if not self._first_paint_logged:
            self._first_paint_logged = True
            print(
                f"[snip.timing] first paint {time.monotonic() - self._t_created:.2f}s "
                "after build (macOS compositing the overlay)",
                file=sys.stderr,
                flush=True,
            )
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, _DIM_ALPHA))

        # Selection box: punch a brighter, outlined hole where the user is
        # dragging (replaces the old QRubberBand child widget).
        if self._sel_rect is not None and not self._sel_rect.isNull():
            p.fillRect(self._sel_rect, QColor(0, 0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self._sel_rect, QColor(255, 255, 255, 20))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(77, 163, 255, 230), 1))
            p.drawRect(self._sel_rect.adjusted(0, 0, -1, -1))

        font = QFont("Segoe UI", 11)
        p.setFont(font)
        p.setPen(QColor(220, 220, 220, 200))
        p.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            "  Click and drag to select a region  ·  ESC to cancel  ",
        )
        p.end()

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        """Handle mouse press event for snip overlay."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._sel_rect = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event):
        """Handle mouse move event for snip overlay."""
        if self._origin is not None:
            self._sel_rect = QRect(self._origin, event.pos()).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        """Handle mouse release event for snip overlay."""
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            rect = QRect(self._origin, event.pos()).normalized()
            self._sel_rect = None
            self._unhook()
            if rect.width() > 4 and rect.height() > 4:
                self.region_selected.emit(self._region_for(rect))
            else:
                self.cancelled.emit()
            self.close()

    def _region_for(self, rect: QRect) -> dict:
        """Translate the widget-local selection into an mss-style region.

        Qt reports mouse coordinates in device-independent (logical) pixels, but
        mss.grab on Windows/Linux captures in physical pixels. On a scaled
        display (e.g. 150%) feeding logical coordinates straight to mss grabs a
        region shifted up and to the left, with the error growing further from
        the top-left corner. Scale by the screen's device-pixel ratio so the
        grab lands exactly where the user dragged. macOS' screencapture -R takes
        logical points, so that path keeps the unscaled coordinates.
        """
        # Widget-local coords -> absolute logical screen coords.
        g_left = rect.x() + self._virtual_origin.x()
        g_top  = rect.y() + self._virtual_origin.y()
        width  = rect.width()
        height = rect.height()

        if sys.platform == "darwin":
            return {"left": g_left, "top": g_top, "width": width, "height": height}

        screen = QApplication.screenAt(QPoint(g_left, g_top)) or self.screen()
        dpr = screen.devicePixelRatio() if screen is not None else 1.0
        return {
            "left":   round(g_left * dpr),
            "top":    round(g_top * dpr),
            "width":  round(width * dpr),
            "height": round(height * dpr),
        }

    # ------------------------------------------------------------------
    # Key input
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        """Handle key press event for snip overlay."""
        if event.key() == Qt.Key.Key_Escape:
            self._unhook()
            self.cancelled.emit()
            self.close()

    # ------------------------------------------------------------------
    # Focus / keyboard grab
    # ------------------------------------------------------------------

    def showEvent(self, event):
        """Show event."""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        # Defer the keyboard grab: on Windows, grabKeyboard() only works after the
        # window is truly in the foreground.  A zero-delay timer fires after the
        # current event-loop iteration, by which time the OS has processed Show.
        QTimer.singleShot(0, self._grab_keyboard)

    def _grab_keyboard(self):
        """Handle grab keyboard for snip overlay."""
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()

    def _unhook(self):
        """Handle unhook for snip overlay."""
        try:
            self.releaseKeyboard()
        except Exception:
            pass
