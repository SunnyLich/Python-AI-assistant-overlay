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
import os
import glob
import urllib.parse
import config
from core import asset_server as _asset_server
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QSystemTrayIcon, QMenu
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal, QObject, QEvent, QPoint
from PyQt6.QtGui import QPixmap, QIcon, QAction, QColor
from doll.animation import DollAnimator

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings

    class _VRMView(QWebEngineView):
        """QWebEngineView that forwards click events to a registered handler."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._click_handler = None

        def mousePressEvent(self, event):          # noqa: N802
            if self._click_handler:
                self._click_handler(event)
            super().mousePressEvent(event)

    _WEB_ENGINE_AVAILABLE = True
except ImportError:
    _VRMView = None  # type: ignore[assignment]
    _WEB_ENGINE_AVAILABLE = False


_ASSETS_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
_DOLL_ROOT   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "doll")
ASSETS_DIR   = os.path.join(_ASSETS_ROOT, "doll")


class OverlaySignals(QObject):
    """Thread-safe signals for updating the overlay from worker threads."""
    set_state          = pyqtSignal(str)   # "idle" | "listening" | "thinking" | "speaking"
    set_mouth_amp      = pyqtSignal(float)  # 0.0–1.0 amplitude for lip sync
    show_text_popup    = pyqtSignal(str)   # full reply text
    show_intent_picker = pyqtSignal(int)  # caller index → show WASD picker for that caller
    show_snip_overlay  = pyqtSignal()      # show full-screen region selector
    bubble_listening   = pyqtSignal()      # show mic/recording indicator
    bubble_thinking    = pyqtSignal()      # show animated dots
    bubble_start_reveal = pyqtSignal()     # start word-by-word reveal synced to audio
    bubble_schedule_words = pyqtSignal(list, list)  # (words, start_ms) from Cartesia timestamps
    bubble_chunk       = pyqtSignal(str)   # buffer additional streamed text chunk
    bubble_finish      = pyqtSignal()      # response done, start hide countdown
    bubble_clear       = pyqtSignal()      # hide immediately
    show_doll          = pyqtSignal()      # make doll visible
    hide_doll          = pyqtSignal()      # hide doll after short delay
    settings_applied   = pyqtSignal()      # settings were applied; re-register hotkeys etc.
    show_new_chat      = pyqtSignal()      # tray "New chat" clicked
    show_last_chat     = pyqtSignal()      # tray "Last chat" clicked
    show_memory_viewer = pyqtSignal()      # tray "Memory…" clicked


class DollOverlay(QMainWindow):
    """
    The persistent doll window. Always on top, no taskbar entry,
    no frame. Positioned at bottom-right corner.
    """

    @property
    def DOLL_SIZE(self):
        if getattr(self, '_use_vrm', False):
            return (config.VRM_WIDTH, config.VRM_HEIGHT)
        s = config.DOLL_SIZE
        return (s, s)

    def __init__(self, signals: OverlaySignals):
        super().__init__()
        self.signals = signals

        # Doll disabled — only the speech bubble is used.
        self._use_vrm = False
        self._no_doll = True

        self._build_window()
        self._build_tray()
        self._build_icon_label()

        # Speech bubble
        from ui.bubble import SpeechBubble
        self._bubble = SpeechBubble()
        self._bubble.set_companion_callback(self._on_bubble_dragged)
        self._bubble.set_hide_callback(self._on_bubble_hidden)
        self._bubble.set_speed_callback(self._on_bubble_speed_boost)
        self._current_state = "idle"

        # Connect signals
        signals.set_state.connect(self._on_state_changed)
        signals.set_mouth_amp.connect(self._on_mouth_amp)
        signals.show_text_popup.connect(self._on_show_popup)
        signals.bubble_listening.connect(self._bubble.show_listening)
        signals.bubble_thinking.connect(self._bubble.start_thinking)
        signals.bubble_start_reveal.connect(self._bubble.start_word_reveal)
        signals.bubble_schedule_words.connect(self._bubble.schedule_words)
        signals.bubble_chunk.connect(self._bubble.append_chunk)
        signals.bubble_finish.connect(self._bubble.finish)
        signals.bubble_clear.connect(self._bubble.clear)
        signals.bubble_clear.connect(self._icon_label_clear)
        signals.show_doll.connect(self._show_doll)
        signals.hide_doll.connect(self._hide_doll)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _build_window(self):
        # No doll — create a hidden zero-size window just to anchor the tray icon.
        if self._no_doll:
            self.setWindowFlags(Qt.WindowType.Tool)
            self.setFixedSize(0, 0)
            return

    def _build_icon_label(self):
        sz = config.DOLL_SIZE
        margin = 20
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + screen.width()  - sz - margin
        y = screen.y() + screen.height() - sz - margin

        self._icon_label = QLabel(None)
        self._icon_label.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self._icon_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._icon_label.setFixedSize(sz, sz)
        self._icon_label.move(x, y)
        self._icon_label.setScaledContents(True)
        self._set_icon_pixmap("idle")
        self._icon_label.show()
        self._icon_label.setCursor(Qt.CursorShape.SizeAllCursor)
        self._icon_label.installEventFilter(self)
        self._icon_drag_offset = None

        self._icon_hide_timer = QTimer(self)
        self._icon_hide_timer.setSingleShot(True)
        self._icon_hide_timer.setInterval(30_000)  # safety backstop only — icon normally hides via _on_bubble_hidden
        self._icon_hide_timer.timeout.connect(self._icon_label.hide)

    def _build_tray(self):
        self._state_icons: dict[str, QIcon] = {}
        for state in ("idle", "listening", "thinking", "speaking"):
            p = os.path.join(ASSETS_DIR, f"{state}.png")
            self._state_icons[state] = QIcon(p) if os.path.exists(p) else QIcon()
        icon = self._state_icons.get("idle", QIcon())

        self._tray = QSystemTrayIcon(icon, self)
        self._tray_menu = self._build_tray_menu()
        self._tray.setContextMenu(self._tray_menu)
        self._tray.show()

    def _build_tray_menu(self) -> QMenu:
        menu = QMenu()

        if self._use_vrm:
            tuner_action = QAction("VRM Tuner…", self)
            tuner_action.triggered.connect(self._open_vrm_tuner)
            menu.addAction(tuner_action)
            menu.addSeparator()

        from ui.agent_task_mockup import make_agent_history_action, make_agent_task_action

        menu.addAction(make_agent_task_action(self, parent=self))
        menu.addAction(make_agent_history_action(self, parent=self))
        menu.addSeparator()

        new_chat_action = QAction("New chat", self)
        new_chat_action.triggered.connect(self.signals.show_new_chat.emit)
        last_chat_action = QAction("Last chat", self)
        last_chat_action.triggered.connect(self.signals.show_last_chat.emit)
        memory_action = QAction("Memory…", self)
        memory_action.triggered.connect(self.signals.show_memory_viewer.emit)
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self._open_settings)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(new_chat_action)
        menu.addAction(last_chat_action)
        menu.addSeparator()
        menu.addAction(memory_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    # ------------------------------------------------------------------
    # VRM Tuner
    # ------------------------------------------------------------------

    def _open_vrm_tuner(self):
        from doll.vrm_tuner import TunerWindow

        def _on_saved(json_str: str) -> None:
            config_path = os.path.join(_DOLL_ROOT, "vrm_config.json")
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print("[tuner] Saved vrm_config.json")
            except Exception as exc:
                print(f"[tuner] Save error: {exc}")
                return
            # Live-push new config to the overlay without restart
            self._webview.page().runJavaScript(
                f"if(window.applyConfig)applyConfig({json_str})"
            )

        if not hasattr(self, "_tuner_window") or not self._tuner_window.isVisible():
            self._tuner_window = TunerWindow(self._vrm_path, _on_saved,
                                             asset_port=getattr(self, "_asset_port", 0))
        self._tuner_window.show()
        self._tuner_window.raise_()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _set_icon_pixmap(self, state: str):
        p = os.path.join(ASSETS_DIR, f"{state}.png")
        if not os.path.exists(p):
            p = os.path.join(ASSETS_DIR, "idle.png")
        if os.path.exists(p):
            self._icon_label.setPixmap(QPixmap(p))

    def _on_state_changed(self, state: str):
        self._current_state = state
        icon = self._state_icons.get(state) or self._state_icons.get("idle")
        if icon:
            self._tray.setIcon(icon)
        self._set_icon_pixmap(state)
        if self._no_doll:
            return
        if self._use_vrm:
            self._webview.page().runJavaScript(f"if(window.setState)setState('{state}')")
        else:
            self._animator.play(state, self._update_pixmap)

    def _on_mouth_amp(self, amp: float):
        if self._use_vrm:
            self._webview.page().runJavaScript(f"if(window.setMouthAmp)window.setMouthAmp({amp:.3f})")

    def _update_pixmap(self, pixmap: QPixmap):
        if not self._use_vrm:
            self._label.setPixmap(pixmap)

    def apply_settings(self):
        """Apply settings that affect existing overlay widgets without restart."""
        if hasattr(self, "_icon_label"):
            sz = config.DOLL_SIZE
            self._icon_label.setFixedSize(sz, sz)
            self._set_icon_pixmap(getattr(self, "_current_state", "idle"))
        if hasattr(self, "_bubble"):
            self._bubble.apply_config()
            if hasattr(self, "_icon_label"):
                self._on_doll_dragged(self._icon_label.pos())

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
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._icon_hide_timer.stop()
        self._icon_label.show()

    def _hide_doll(self):
        if not hasattr(self, '_icon_hide_timer'):
            return
        # Start a backstop timer — the icon will normally be hidden in sync with
        # the bubble via _on_bubble_hidden, but this covers cases where the bubble
        # is never shown (e.g. empty voice transcription).
        self._icon_hide_timer.start()

    def _on_bubble_hidden(self):
        """Called by SpeechBubble.hideEvent — hides the icon in lockstep with the bubble."""
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._on_bubble_speed_boost(False)
        self._icon_hide_timer.stop()
        self._icon_label.hide()

    def _icon_label_clear(self):
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._on_bubble_speed_boost(False)
        self._icon_hide_timer.stop()
        self._icon_label.hide()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def set_click_handler(self, handler):
        pass  # doll disabled

    def _on_click(self, event):
        pass

    # ------------------------------------------------------------------
    # Drag support (doll icon + bubble kept in sync)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._icon_label:
            t = event.type()
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                self._tray_menu.popup(event.globalPosition().toPoint())
                return True
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._icon_drag_offset = self._icon_label.pos() - event.globalPosition().toPoint()
                return True
            elif t == QEvent.Type.MouseMove and self._icon_drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
                new_pos = event.globalPosition().toPoint() + self._icon_drag_offset
                self._icon_label.move(new_pos)
                self._on_doll_dragged(new_pos)
                return True
            elif t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._icon_drag_offset = None
                return True
        return super().eventFilter(obj, event)

    def _on_doll_dragged(self, doll_pos: QPoint):
        """Reposition bubble to stay to the left of the doll after a drag."""
        sz = config.DOLL_SIZE
        bw = self._bubble._bubble_w
        bh = self._bubble._bubble_h
        from ui.bubble import _TAIL_W
        bx = doll_pos.x() - bw - _TAIL_W - 6
        by = doll_pos.y() + (sz - bh) // 2
        self._bubble.move(bx, by)

    def _on_bubble_dragged(self, bubble_pos: QPoint):
        """Reposition doll icon to stay to the right of the bubble after a drag."""
        doll_pos = self._bubble.doll_pos_for_bubble(bubble_pos, config.DOLL_SIZE)
        self._icon_label.move(doll_pos)

    def _on_bubble_speed_boost(self, enabled: bool):
        from core import audio
        audio.set_tts_speed_boost(enabled)
