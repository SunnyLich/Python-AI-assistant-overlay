"""
ui/window_utils.py - Small helpers for keeping app windows reachable.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QWidget


def enable_standard_window_controls(window: QWidget) -> None:
    """Give a top-level app window the usual minimize, maximize, and close buttons."""
    window.setWindowFlag(Qt.WindowType.Window, True)
    window.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
    window.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
    window.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
    window.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
    window.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)


def fit_window_to_screen(
    window: QWidget,
    *,
    margin: int = 32,
    preferred_width: int | None = None,
    preferred_height: int | None = None,
) -> None:
    """Resize and move a top-level window so its title bar stays on screen."""
    app = QApplication.instance()
    screen = app.screenAt(QCursor.pos()) if app is not None else None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        return

    available = screen.availableGeometry()
    max_w = max(320, available.width() - margin * 2)
    max_h = max(260, available.height() - margin * 2)

    width = preferred_width or window.width() or window.sizeHint().width()
    height = preferred_height or window.height() or window.sizeHint().height()
    width = min(max(width, window.minimumWidth()), max_w)
    height = min(max(height, window.minimumHeight()), max_h)

    window.resize(width, height)

    x = available.x() + (available.width() - width) // 2
    y = available.y() + (available.height() - height) // 2
    x = min(max(x, available.left() + margin), available.right() - width - margin + 1)
    y = min(max(y, available.top() + margin), available.bottom() - height - margin + 1)
    window.move(x, y)
