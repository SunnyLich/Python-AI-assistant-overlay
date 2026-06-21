"""
ui/popup.py — Transient text popup for the full LLM reply.

Appears near the icon, auto-dismisses after a timeout, or on click.
"""
from __future__ import annotations
import config
from PySide6.QtWidgets import QLabel, QWidget, QApplication, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont


POPUP_WIDTH = 320
POPUP_MAX_HEIGHT = 240
AUTO_DISMISS_MS = 12_000   # disappears after 12 s if not clicked
ICON_MARGIN = 20           # gap between popup and icon, px


class TextPopup(QWidget):
    """
    A small floating tooltip-style window for displaying the full reply.
    """

    def __init__(self, text: str, parent=None):
        """Initialize the text popup instance."""
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(POPUP_WIDTH)

        self._build_ui(text)
        self._position_near_icon()

        # Auto-dismiss
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(AUTO_DISMISS_MS)

    def _build_ui(self, text: str):
        """Build ui."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.MarkdownText)
        label.setWordWrap(True)
        label.setFont(QFont("Segoe UI", 10))
        label.setStyleSheet(
            "color: #1a1a1a;"
            "background: #ffffff;"
            "border-radius: 10px;"
            "padding: 8px;"
        )
        layout.addWidget(label)
        self.adjustSize()

    def _position_near_icon(self):
        """Handle position near icon for text popup."""
        screen = QApplication.primaryScreen().availableGeometry()
        # Place above the icon (bottom-right)
        x = screen.width() - POPUP_WIDTH - 20
        y = screen.height() - config.ICON_SIZE - POPUP_MAX_HEIGHT - ICON_MARGIN
        self.move(x, y)

    def mousePressEvent(self, event):
        """Handle mouse press event for text popup."""
        self.close()
