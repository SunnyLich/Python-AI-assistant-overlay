"""
ui/snip_overlay.py — Full-screen region selector (Ctrl+Alt+Q).

Covers the entire virtual desktop (all monitors) with a semi-dark overlay.
The user clicks and drags to select a region; the widget emits
region_selected with an mss-compatible region dict on release,
or cancelled on Escape / zero-size drag.
"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QApplication, QRubberBand
from PySide6.QtCore import Qt, Signal, QRect, QPoint, QSize, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QCursor

_DIM_ALPHA = 110   # overlay darkness 0–255


class SnipOverlay(QWidget):
    region_selected = Signal(dict)   # mss-format: {left, top, width, height}
    cancelled       = Signal()

    def __init__(self, parent=None):
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

        self._origin: QPoint | None = None
        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)

    # ------------------------------------------------------------------
    # Paint — dim the whole screen; rubber band provides the selection box
    # ------------------------------------------------------------------

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, _DIM_ALPHA))

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
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._rubber.setGeometry(
                QRect(self._origin, event.pos()).normalized()
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            rect = QRect(self._origin, event.pos()).normalized()
            self._rubber.hide()
            self._unhook()
            if rect.width() > 4 and rect.height() > 4:
                # Translate widget-local coords to absolute screen coords
                abs_x = rect.x() + self._virtual_origin.x()
                abs_y = rect.y() + self._virtual_origin.y()
                self.region_selected.emit({
                    "left":   abs_x,
                    "top":    abs_y,
                    "width":  rect.width(),
                    "height": rect.height(),
                })
            else:
                self.cancelled.emit()
            self.close()

    # ------------------------------------------------------------------
    # Key input
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._unhook()
            self.cancelled.emit()
            self.close()

    # ------------------------------------------------------------------
    # Focus / keyboard grab
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        # Defer the keyboard grab: on Windows, grabKeyboard() only works after the
        # window is truly in the foreground.  A zero-delay timer fires after the
        # current event-loop iteration, by which time the OS has processed Show.
        QTimer.singleShot(0, self._grab_keyboard)

    def _grab_keyboard(self):
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()

    def _unhook(self):
        try:
            self.releaseKeyboard()
        except Exception:
            pass
