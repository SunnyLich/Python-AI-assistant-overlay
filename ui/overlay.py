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
import config
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QIcon, QAction
from ui.animation import DollAnimator


ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "doll")


class OverlaySignals(QObject):
    """Thread-safe signals for updating the overlay from worker threads."""
    set_state          = pyqtSignal(str)   # "idle" | "listening" | "thinking" | "speaking"
    show_text_popup    = pyqtSignal(str)   # full reply text
    show_intent_picker = pyqtSignal()      # show arrow-key intent chooser
    show_snip_overlay  = pyqtSignal()      # show full-screen region selector
    bubble_thinking    = pyqtSignal()      # show animated dots
    bubble_start_reveal = pyqtSignal()     # start word-by-word reveal synced to audio
    bubble_schedule_words = pyqtSignal(list, list)  # (words, start_ms) from Cartesia timestamps
    bubble_chunk       = pyqtSignal(str)   # buffer additional streamed text chunk
    bubble_finish      = pyqtSignal()      # response done, start hide countdown
    bubble_clear       = pyqtSignal()      # hide immediately
    show_doll          = pyqtSignal()      # make doll visible
    hide_doll          = pyqtSignal()      # hide doll after short delay
    settings_applied   = pyqtSignal()      # settings were applied; re-register hotkeys etc.
    show_last_chat     = pyqtSignal()      # tray "Last chat" clicked


class DollOverlay(QMainWindow):
    """
    The persistent doll window. Always on top, no taskbar entry,
    no frame. Positioned at bottom-right corner.
    """

    @property
    def DOLL_SIZE(self):
        s = config.DOLL_SIZE
        return (s, s)

    def __init__(self, signals: OverlaySignals):
        super().__init__()
        self.signals = signals
        self._animator = DollAnimator(ASSETS_DIR, self.DOLL_SIZE)

        self._build_window()
        self._build_tray()

        # Speech bubble
        from ui.bubble import SpeechBubble
        self._bubble = SpeechBubble()

        # Connect signals
        signals.set_state.connect(self._on_state_changed)
        signals.show_text_popup.connect(self._on_show_popup)
        signals.bubble_thinking.connect(self._bubble.start_thinking)
        signals.bubble_start_reveal.connect(self._bubble.start_word_reveal)
        signals.bubble_schedule_words.connect(self._bubble.schedule_words)
        signals.bubble_chunk.connect(self._bubble.append_chunk)
        signals.bubble_finish.connect(self._bubble.finish)
        signals.bubble_clear.connect(self._bubble.clear)
        signals.show_doll.connect(self._show_doll)
        signals.hide_doll.connect(self._hide_doll)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

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
        last_chat_action = QAction("Last chat", self)
        last_chat_action.triggered.connect(self.signals.show_last_chat.emit)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(last_chat_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addSeparator()
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

    def _open_settings(self):
        from ui.settings import open_settings
        open_settings(parent=self, on_apply=self.signals.settings_applied.emit)

    # ------------------------------------------------------------------
    # Doll visibility
    # ------------------------------------------------------------------

    def _show_doll(self):
        self._hide_timer.stop()
        self.show()
        self.raise_()

    def _hide_doll(self):
        """Hide doll at the same time the speech bubble hides (after _HIDE_DELAY)."""
        from ui.bubble import _HIDE_DELAY
        self._hide_timer.setInterval(_HIDE_DELAY)
        self._hide_timer.start()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def set_click_handler(self, handler):
        """Register an external click handler for the doll label."""
        self._label.mousePressEvent = handler

    def _on_click(self, event):
        pass
