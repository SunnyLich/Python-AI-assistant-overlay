"""
ui/overlay.py — Persistent doll overlay widget.

A small, always-on-top, click-through frameless window that lives in the
bottom-right corner. It shows the doll sprite and hosts the system tray icon.

States:
  idle      — static doll, sitting quietly
  listening — hotkey pressed; plays animate_listen()
  thinking  — LLM request in flight
  speaking  — TTS playing; plays animate_speak()
"""
from __future__ import annotations
import sys
import os
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QIcon, QAction
from ui.animation import DollAnimator


ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "doll")


class OverlaySignals(QObject):
    """Thread-safe signals for updating the overlay from worker threads."""
    set_state = pyqtSignal(str)          # "idle" | "listening" | "thinking" | "speaking"
    show_text_popup = pyqtSignal(str)    # full reply text


class DollOverlay(QMainWindow):
    """
    The persistent doll window. Always on top, no taskbar entry,
    no frame. Positioned at bottom-right corner.
    """

    DOLL_SIZE = (80, 80)  # px

    def __init__(self, signals: OverlaySignals):
        super().__init__()
        self.signals = signals
        self._animator = DollAnimator(ASSETS_DIR, self.DOLL_SIZE)

        self._build_window()
        self._build_tray()

        # Connect signals
        signals.set_state.connect(self._on_state_changed)
        signals.show_text_popup.connect(self._on_show_popup)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _build_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar entry
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(*self.DOLL_SIZE)

        self._label = QLabel(self)
        self._label.setFixedSize(*self.DOLL_SIZE)
        self._update_pixmap(self._animator.frame("idle"))

        # Position: bottom-right corner with 20px margin
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.width() - self.DOLL_SIZE[0] - 20
        y = screen.height() - self.DOLL_SIZE[1] - 20
        self.move(x, y)

        self._label.mousePressEvent = self._on_click

    def _build_tray(self):
        icon_path = os.path.join(ASSETS_DIR, "idle.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self._tray = QSystemTrayIcon(icon, self)
        menu = QMenu()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.show()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: str):
        self._animator.play(state, self._update_pixmap)

    def _update_pixmap(self, pixmap: QPixmap):
        self._label.setPixmap(pixmap)

    # ------------------------------------------------------------------
    # Popup
    # ------------------------------------------------------------------

    def _on_show_popup(self, text: str):
        from ui.popup import TextPopup
        popup = TextPopup(text, parent=None)
        popup.show()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def _on_click(self, event):
        """Left click: show full text popup. Could also drag to reposition."""
        pass  # wired up by main.py after a response is available
