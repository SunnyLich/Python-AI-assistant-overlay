"""
ui/overlay.py -” Persistent icon overlay widget.

A small, always-on-top, click-through frameless window that lives in the
bottom-right corner. It shows the icon sprite and hosts the system tray icon.

States:
  idle      -” static icon, sitting quietly
  listening -” hotkey pressed; plays animate_listen()
  thinking  -” LLM request in flight
  speaking  -” TTS playing; plays animate_speak()
"""
from __future__ import annotations
import os
import config
from core.system.paths import DOLL_ASSETS_DIR
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QSystemTrayIcon, QMenu
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QEvent, QPoint
from PySide6.QtGui import QPixmap, QIcon, QAction
from ui.i18n import t


ASSETS_DIR = str(DOLL_ASSETS_DIR)


class OverlaySignals(QObject):
    """Thread-safe signals for updating the overlay from worker threads."""
    set_state          = Signal(str)   # "idle" | "listening" | "thinking" | "speaking"
    set_mouth_amp      = Signal(float)  # 0.0-“1.0 amplitude for lip sync
    show_text_popup    = Signal(str)   # full reply text
    show_intent_picker = Signal(int)  # caller index â†’ show WASD picker for that caller
    summon_caller      = Signal(int)  # icon clicked → run caller at this index (hotkey-free trigger)
    show_snip_overlay  = Signal()      # show full-screen region selector
    bubble_listening   = Signal()      # show mic/recording indicator
    bubble_thinking    = Signal()      # show animated dots
    bubble_start_reveal = Signal()     # start word-by-word reveal synced to audio
    bubble_schedule_words = Signal(list, list)  # (words, start_ms) from Cartesia timestamps
    bubble_chunk       = Signal(str, bool)   # (chunk, is_thought)
    bubble_finish      = Signal()      # response done, start hide countdown
    bubble_clear       = Signal()      # hide immediately
    bubble_highlight   = Signal(str, int, bool)  # (reply_text, revealed_count, finished)
    bubble_stop_requested = Signal()   # user clicked X on the bubble
    show_icon          = Signal()      # make icon visible
    hide_icon          = Signal()      # hide icon after short delay
    raise_overlay      = Signal()      # bring overlay to foreground (Linux)
    settings_applied   = Signal()      # settings were applied; re-register hotkeys etc.
    show_new_chat          = Signal()        # tray "New chat" clicked
    show_last_chat         = Signal()        # tray "Last chat" clicked
    chat_new_conversation  = Signal()        # a voice query created a new conversation
    chat_sync_conversation = Signal(int)     # a voice query appended to an existing conversation (idx)
    show_memory_viewer     = Signal()        # tray "Memory-¦" clicked
    show_addon_manager    = Signal()        # tray "Addon Manager" clicked
    show_agent_task        = Signal()        # tray "Start agent task" clicked
    show_agent_history     = Signal()        # tray "Agent task history" clicked
    request_setup_check    = Signal()        # Settings setup check requested
    context_items_dropped  = Signal(object)  # list[(name, content, type)] from drag-drop
    add_context_item       = Signal(str, str) # (name, type) add one removable badge (hotkey/voice add)
    show_context_summary   = Signal(object)  # list[(name, type)] of context sent with a prompt
    drop_context_cleared   = Signal()        # context panel should be cleared
    remove_dropped_item    = Signal(int)     # user clicked X on badge at this index
    bubble_speed           = Signal(bool)     # hold-to-speed state changed
    status_notification    = Signal(str, str) # (title, message) startup/status notice (addons, STT-ready)


class IconOverlay(QMainWindow):
    """
    The persistent icon window. Always on top, no taskbar entry,
    no frame. Positioned at bottom-right corner.
    """

    @property
    def ICON_SIZE(self):
        """Handle i c o NS i z e for icon overlay."""
        s = config.ICON_SIZE
        return (s, s)

    def __init__(self, signals: OverlaySignals):
        """Initialize the icon overlay instance."""
        super().__init__()
        self.signals = signals

        self._build_window()
        self._build_tray()
        self._build_icon_label()

        # Speech bubble
        from ui.bubble import SpeechBubble
        self._bubble = SpeechBubble()
        self._bubble.set_companion_callback(self._on_bubble_dragged)
        self._bubble.set_hide_callback(self._on_bubble_hidden)
        self._bubble.set_speed_callback(self._on_bubble_speed_boost)
        self._bubble.set_click_callback(signals.show_last_chat.emit)
        self._bubble.set_highlight_callback(
            lambda text, count, finished: signals.bubble_highlight.emit(text, count, finished)
        )
        self._bubble.set_stop_callback(signals.bubble_stop_requested.emit)
        self._current_state = "idle"

        # Drop-context panel (right side of icon)
        from ui.drop_zone import ContextPanel
        self._context_panel = ContextPanel()
        self._context_panel.set_remove_callback(signals.remove_dropped_item.emit)
        self._context_panel.reposition(self._icon_label.pos(), config.ICON_SIZE)
        signals.drop_context_cleared.connect(self._context_panel.clear_items)
        signals.show_context_summary.connect(self._on_show_context_summary)
        signals.add_context_item.connect(self._on_add_context_item)

        # Connect signals
        signals.set_state.connect(self._on_state_changed)
        signals.set_mouth_amp.connect(self._on_mouth_amp)
        signals.show_text_popup.connect(self._on_show_popup)
        signals.bubble_listening.connect(self._show_icon)
        signals.bubble_listening.connect(self._bubble.show_listening)
        signals.bubble_thinking.connect(self._show_icon)
        signals.bubble_thinking.connect(self._bubble.start_thinking)
        signals.bubble_start_reveal.connect(self._show_icon)
        signals.bubble_start_reveal.connect(self._bubble.start_word_reveal)
        signals.bubble_schedule_words.connect(self._bubble.schedule_words)
        signals.bubble_chunk.connect(lambda *_args: self._show_icon())
        signals.bubble_chunk.connect(self._bubble.append_chunk)
        signals.bubble_finish.connect(self._bubble.finish)
        signals.bubble_finish.connect(self._on_bubble_finish)
        signals.bubble_clear.connect(self._bubble.clear)
        signals.bubble_clear.connect(self._icon_label_clear)
        signals.show_icon.connect(self._show_icon)
        signals.hide_icon.connect(self._hide_icon)
        signals.raise_overlay.connect(self._raise_overlay)
        signals.status_notification.connect(self._on_status_notification)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # macOS: a Qt.Tool window is backed by an NSPanel that hides itself
        # whenever our app is not frontmost. Pin the persistent overlay windows
        # so the icon/context panel stay put when the user clicks into another
        # app (independent of our own ICON_AUTO_HIDE logic).
        self._pin_overlay_windows()

    def _pin_overlay_windows(self):
        """Stop the Tool overlay windows from auto-hiding on app deactivation (macOS)."""
        from core.platform_utils import keep_overlay_visible_across_apps
        for w in (
            getattr(self, "_icon_label", None),
            getattr(self, "_context_panel", None),
            getattr(self, "_bubble", None),
        ):
            if w is not None:
                keep_overlay_visible_across_apps(w)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _build_window(self):
        # Hidden zero-size window anchors the tray icon. The visible companion is
        # a small independent QLabel so it can be dragged without a window frame.
        """Build window."""
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setFixedSize(0, 0)

    def _build_icon_label(self):
        """Build icon label."""
        sz = config.ICON_SIZE
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
        self._icon_label.setWindowTitle(t("AI Assistant Icon"))
        self._icon_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        _app_icon = QApplication.instance().windowIcon()
        if not _app_icon.isNull():
            self._icon_label.setWindowIcon(_app_icon)
        self._icon_label.setFixedSize(sz, sz)
        self._icon_label.move(x, y)
        self._icon_label.setScaledContents(True)
        self._set_icon_pixmap("idle")
        if config.ICON_AUTO_HIDE:
            self._icon_label.hide()
        else:
            self._icon_label.show()
        self._icon_label.setCursor(Qt.CursorShape.SizeAllCursor)
        self._icon_label.setAcceptDrops(True)
        self._icon_label.installEventFilter(self)
        self._icon_drag_offset = None
        self._icon_press_pos = QPoint()
        self._icon_dragged = False

        self._icon_hide_timer = QTimer(self)
        self._icon_hide_timer.setSingleShot(True)
        self._icon_hide_timer.setInterval(self._icon_backstop_ms())
        # Route the backstop through a guarded slot rather than hiding directly:
        # several callers (e.g. notify_agent_approval) start this timer without
        # checking ICON_AUTO_HIDE, and the icon must NEVER auto-hide when the user
        # has auto-hide off. The manual "Hide icon" action bypasses this via
        # _hide_icon_now.
        self._icon_hide_timer.timeout.connect(self._on_icon_hide_timeout)

    def _update_tray_context_menu(self) -> None:
        """Update tray context menu."""
        if hasattr(self, "_tray") and hasattr(self, "_tray_menu"):
            self._tray.setContextMenu(self._tray_menu)

    def _build_tray(self):
        """Build tray."""
        self._state_icons: dict[str, QIcon] = {}
        for state in ("idle", "listening", "thinking", "speaking"):
            p = os.path.join(ASSETS_DIR, f"{state}.png")
            self._state_icons[state] = QIcon(p) if os.path.exists(p) else QIcon()
        icon = self._state_icons.get("idle", QIcon())

        self._tray = QSystemTrayIcon(icon, self)
        self._tray_menu = self._build_tray_menu()
        self._update_tray_context_menu()
        self._tray.show()

    def _build_tray_menu(self) -> QMenu:
        """Build tray menu."""
        menu = QMenu(self)

        if os.environ.get("WISP_MACOS_PY_UI_HOST") == "1":
            agent_task_action = QAction(t("Start agent task..."), self)
            agent_task_action.triggered.connect(self.signals.show_agent_task.emit)
            agent_history_action = QAction(t("Agent task history..."), self)
            agent_history_action.triggered.connect(self.signals.show_agent_history.emit)
            menu.addAction(agent_task_action)
            menu.addAction(agent_history_action)
        else:
            from ui.agent.task_window import make_agent_history_action, make_agent_task_action

            menu.addAction(make_agent_task_action(self, parent=self))
            menu.addAction(make_agent_history_action(self, parent=self))
        menu.addSeparator()

        last_chat_action = QAction(t("Last chat"), self)
        last_chat_action.triggered.connect(self.signals.show_last_chat.emit)
        # Toggle: label reflects current icon visibility, refreshed on menu open
        # (aboutToShow) so it reads "Show icon" once the icon is hidden — the only
        # way back to a visible icon after Hide.
        self._icon_toggle_action = QAction(t("Hide icon"), self)
        self._icon_toggle_action.triggered.connect(self._toggle_icon)
        menu.aboutToShow.connect(self._sync_icon_toggle_text)
        memory_action = QAction(t("Memory"), self)
        memory_action.triggered.connect(self.signals.show_memory_viewer.emit)
        settings_action = QAction(t("Settings"), self)
        settings_action.triggered.connect(self._open_settings)
        quit_action = QAction(t("Quit"), self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(last_chat_action)
        menu.addAction(self._icon_toggle_action)
        menu.addSeparator()
        menu.addAction(memory_action)
        addon_manager_action = QAction(t("Addon Manager"), self)
        if os.environ.get("WISP_MACOS_PY_UI_HOST") == "1":
            addon_manager_action.triggered.connect(self.signals.show_addon_manager.emit)
        else:
            addon_manager_action.triggered.connect(self._open_addon_manager)
        menu.addAction(addon_manager_action)
        menu.addSeparator()

        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        return menu

    def _set_icon_pixmap(self, state: str):
        """Set icon pixmap."""
        p = os.path.join(ASSETS_DIR, f"{state}.png")
        if not os.path.exists(p):
            p = os.path.join(ASSETS_DIR, "idle.png")
        if os.path.exists(p):
            self._icon_label.setPixmap(QPixmap(p))

    def _on_state_changed(self, state: str):
        """Handle state changed events."""
        self._current_state = state
        icon = self._state_icons.get(state) or self._state_icons.get("idle")
        if icon:
            self._tray.setIcon(icon)
        self._set_icon_pixmap(state)
        if config.ICON_AUTO_HIDE and state != "idle":
            self._show_icon()

    def _on_mouth_amp(self, amp: float):
        """Handle mouth amp events."""
        pass

    def apply_settings(self):
        """Apply settings that affect existing overlay widgets without restart."""
        if hasattr(self, "_icon_label"):
            sz = config.ICON_SIZE
            self._icon_label.setFixedSize(sz, sz)
            self._set_icon_pixmap(getattr(self, "_current_state", "idle"))
            if not config.ICON_AUTO_HIDE:
                self._icon_label.show()
            elif getattr(self, "_current_state", "idle") == "idle":
                self._icon_label.hide()
        if hasattr(self, "_bubble"):
            self._bubble.apply_config()
            if hasattr(self, "_icon_label"):
                self._on_icon_dragged(self._icon_label.pos())
        if hasattr(self, "_context_panel") and hasattr(self, "_icon_label"):
            self._context_panel.reposition(self._icon_label.pos(), config.ICON_SIZE)
        if hasattr(self, "_icon_hide_timer"):
            self._icon_hide_timer.setInterval(self._icon_backstop_ms())
        if hasattr(self, "_tray"):
            old_menu = getattr(self, "_tray_menu", None)
            self._tray_menu = self._build_tray_menu()
            if old_menu is not None:
                old_menu.deleteLater()
        self._update_tray_context_menu()

    # ------------------------------------------------------------------
    # Popup
    # ------------------------------------------------------------------

    def _on_show_popup(self, text: str):
        """Handle show popup events."""
        from ui.popup import TextPopup
        popup = TextPopup(text, parent=None)
        popup.show()

    def _on_status_notification(self, title: str, message: str) -> None:
        """Show a lightweight startup/status notice (addon notifications, the
        STT-ready/backend message). Tray balloon is the durable surface; the
        bubble gives an immediate on-screen confirmation."""
        if not message:
            return
        if hasattr(self, "_bubble"):
            self._show_icon()
            self._bubble.show_notice(message, timeout_ms=5000)
        if hasattr(self, "_tray"):
            self._tray.showMessage(
                title or t("Wisp"),
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def notify_agent_approval(
        self,
        text: str,
        *,
        resolved: bool = False,
        on_approve=None,
        on_decline=None,
    ) -> dict:
        """Raise the icon and show an agent approval notice bubble."""
        if hasattr(self, "_icon_hide_timer"):
            self._icon_hide_timer.stop()
        if hasattr(self, "_icon_label"):
            self._set_icon_pixmap("thinking" if not resolved else "idle")
            self._icon_label.show()
            self._icon_label.raise_()
        shown = False
        actionable = bool(on_approve and on_decline and not resolved)
        if hasattr(self, "_bubble"):
            timeout = 4500 if resolved else 15000
            actions = None
            if actionable:
                actions = [(t("Approve"), on_approve), (t("Decline"), on_decline)]
            self._bubble.show_notice(text, timeout_ms=timeout, actions=actions)
            shown = True
        if hasattr(self, "_tray") and not shown:
            self._tray.showMessage(
                t("Agent permission") if not resolved else t("Agent permission resolved"),
                text,
                QSystemTrayIcon.MessageIcon.Warning if not resolved else QSystemTrayIcon.MessageIcon.Information,
                10000 if not resolved else 4000,
            )
        if hasattr(self, "_icon_hide_timer"):
            self._icon_hide_timer.setInterval(15000 if not resolved else self._icon_backstop_ms())
            self._icon_hide_timer.start()
        return {"shown": shown, "actionable": shown and actionable}

    def _open_settings(self):
        # Defer the actual open to the next event-loop turn. This action fires
        # from inside the right-click QMenu; showing a window synchronously while
        # the menu's native Cocoa tracking loop is still unwinding segfaults on
        # macOS. singleShot(0) lets the menu fully close first. (The menu actions
        # wired to App's *queued* signals — memory/chat — already get this for
        # free, which is why they don't crash and Settings did.)
        """Open settings."""
        from ui.settings_panel.dialog import open_settings
        QTimer.singleShot(
            0,
            lambda: open_settings(
                parent=self,
                on_apply=self.signals.settings_applied.emit,
                on_setup_check=self.signals.request_setup_check.emit,
            ),
        )

    def _open_addon_manager(self):
        # Deferred for the same reason as _open_settings (opened from a QMenu action).
        """Open plugin manager."""
        from ui.addon_manager import open_addon_manager
        QTimer.singleShot(0, lambda: open_addon_manager(parent=self))

    # ------------------------------------------------------------------
    # Icon visibility
    #
    # Invariant: the icon is only ever hidden by (a) auto-hide returning to
    # idle (the backstop timer / bubble-lockstep paths below, all gated on
    # config.ICON_AUTO_HIDE), or (b) the user's manual "Hide icon" action
    # (_hide_icon_now). When auto-hide is off the icon stays visible until the
    # user hides it. Nothing else should hide it.
    # ------------------------------------------------------------------

    def _raise_overlay(self):
        """Handle raise overlay for icon overlay."""
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_icon(self):
        """Show icon."""
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._icon_hide_timer.stop()
        self._icon_label.show()
        self._icon_label.raise_()

    def _hide_icon(self):
        """Hide icon."""
        if not hasattr(self, '_icon_hide_timer'):
            return
        if not config.ICON_AUTO_HIDE:
            return
        # Start a backstop timer -” the icon will normally be hidden in sync with
        # the bubble via _on_bubble_hidden, but this covers cases where the bubble
        # is never shown (e.g. empty voice transcription).
        self._icon_hide_timer.start()

    def _on_bubble_finish(self):
        """Bubble is winding down its reveal — stop the backstop so the icon
        stays until the bubble actually hides via _on_bubble_hidden."""
        if hasattr(self, '_icon_hide_timer'):
            self._icon_hide_timer.stop()

    def _on_icon_hide_timeout(self):
        """Backstop timer fired — only hide if auto-hide is on (see _icon_hide_timer)."""
        if config.ICON_AUTO_HIDE and hasattr(self, "_icon_label"):
            self._icon_label.hide()

    def _hide_icon_now(self):
        """Manual 'Hide icon' tray action — always hides, regardless of auto-hide."""
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._icon_hide_timer.stop()
        if hasattr(self, "_bubble"):
            self._bubble.clear()
        self._icon_label.hide()

    def _toggle_icon(self):
        """Tray/right-click 'Show/Hide icon' toggle for the floating icon."""
        if hasattr(self, "_icon_label") and self._icon_label.isVisible():
            self._hide_icon_now()
        else:
            self._show_icon()

    def _sync_icon_toggle_text(self):
        """Keep the toggle action label in step with the icon's current visibility."""
        if hasattr(self, "_icon_toggle_action") and hasattr(self, "_icon_label"):
            visible = self._icon_label.isVisible()
            original = "Hide icon" if visible else "Show icon"
            self._icon_toggle_action.setText(t(original))

    @staticmethod
    def _icon_backstop_ms() -> int:
        """Handle icon backstop ms for icon overlay."""
        return max(500, int(getattr(config, "ICON_BACKSTOP_MS", 5000)))

    def _on_show_context_summary(self, items):
        """Show read-only badges of the context attached to the current prompt."""
        if not hasattr(self, "_context_panel"):
            return
        if hasattr(self, "_icon_label"):
            self._context_panel.reposition(self._icon_label.pos(), config.ICON_SIZE)
        self._context_panel.show_context_summary(items)

    def _on_add_context_item(self, name: str, item_type: str):
        """Add one removable badge to the right of the icon (hotkey/voice 'add context').

        Surfaces added context the same way dropped files do — on the panel, not
        in the speech bubble."""
        if not hasattr(self, "_context_panel"):
            return
        if hasattr(self, "_icon_label"):
            self._context_panel.reposition(self._icon_label.pos(), config.ICON_SIZE)
        self._context_panel.add_item(name or t("Context"), item_type or "text")

    def _on_bubble_hidden(self):
        """Called by SpeechBubble.hideEvent -- hides the icon in lockstep with the bubble."""
        if hasattr(self, "_context_panel"):
            self._context_panel.clear_summary()
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._on_bubble_speed_boost(False)
        self._icon_hide_timer.stop()
        if config.ICON_AUTO_HIDE:
            self._icon_label.hide()

    def _icon_label_clear(self):
        """Handle icon label clear for icon overlay."""
        if not hasattr(self, '_icon_hide_timer') or not hasattr(self, '_icon_label'):
            return
        self._on_bubble_speed_boost(False)
        self._icon_hide_timer.stop()
        if config.ICON_AUTO_HIDE:
            self._icon_label.hide()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def set_click_handler(self, handler):
        """Set click handler."""
        pass  # icon click disabled

    def _on_click(self, event):
        """Handle click events."""
        pass

    # ------------------------------------------------------------------
    # Drag support (icon + bubble kept in sync)
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        """Handle event filter for icon overlay."""
        if obj is self._icon_label:
            t = event.type()

            # ---- mouse events ----
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                self._right_press_on_icon = True
                return True
            if t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
                if getattr(self, "_right_press_on_icon", False):
                    self._right_press_on_icon = False
                    self._tray_menu.popup(event.globalPosition().toPoint())
                return True
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._icon_press_pos = event.globalPosition().toPoint()
                self._icon_drag_offset = self._icon_label.pos() - self._icon_press_pos
                self._icon_dragged = False
                return True
            elif t == QEvent.Type.MouseMove and self._icon_drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
                cur = event.globalPosition().toPoint()
                # Only count as a drag once the pointer moves past the OS drag
                # threshold, so a small jitter during a click still registers as a click.
                if (cur - self._icon_press_pos).manhattanLength() >= QApplication.startDragDistance():
                    self._icon_dragged = True
                self._icon_label.move(cur + self._icon_drag_offset)
                self._on_icon_dragged(cur + self._icon_drag_offset)
                return True
            elif t == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                was_drag = self._icon_dragged
                self._icon_drag_offset = None
                self._icon_dragged = False
                # A clean left-click (no drag) summons the prompt — the
                # permission-free alternative to the global hotkey.
                if not was_drag:
                    self.signals.summon_caller.emit(0)
                return True

            # ---- drag-and-drop events ----
            elif t == QEvent.Type.DragEnter:
                mime = event.mimeData()
                if mime.hasUrls() or mime.hasText() or mime.hasImage():
                    event.acceptProposedAction()
                    self._icon_label.setCursor(Qt.CursorShape.DragCopyCursor)
                    if hasattr(self, "_context_panel"):
                        self._context_panel.set_drag_active(True)
                return True
            elif t == QEvent.Type.DragMove:
                event.acceptProposedAction()
                return True
            elif t == QEvent.Type.DragLeave:
                self._icon_label.setCursor(Qt.CursorShape.SizeAllCursor)
                if hasattr(self, "_context_panel"):
                    self._context_panel.set_drag_active(False)
                return True
            elif t == QEvent.Type.Drop:
                self._icon_label.setCursor(Qt.CursorShape.SizeAllCursor)
                if hasattr(self, "_context_panel"):
                    self._context_panel.set_drag_active(False)
                global_pos = self._icon_label.mapToGlobal(event.position().toPoint())
                self._process_drop(event.mimeData(), global_pos)
                event.acceptProposedAction()
                return True

        return super().eventFilter(obj, event)

    def _process_drop(self, mime, global_pos: QPoint) -> None:
        """Handle a completed drop: extract content, show VFX, update panel, emit signal."""
        from ui.drop_zone import process_drop_mime, VanishEffect, AddedContextToast

        items = process_drop_mime(mime)
        if not items:
            return

        # Particle burst at cursor
        VanishEffect(global_pos)

        # "Added as context!" toast above the icon
        AddedContextToast(self._icon_label.pos(), config.ICON_SIZE)

        # Populate the right-side context panel
        for name, _content, item_type in items:
            self._context_panel.add_item(name, item_type)

        # Notify main.py
        self.signals.context_items_dropped.emit(items)

    def _on_icon_dragged(self, icon_pos: QPoint):
        """Reposition bubble and context panel after a drag."""
        sz = config.ICON_SIZE
        bw = self._bubble._bubble_w
        bh = self._bubble._bubble_h
        from ui.bubble import _TAIL_W
        bx = icon_pos.x() - bw - _TAIL_W - 6
        by = icon_pos.y() + (sz - bh) // 2
        self._bubble.move(bx, by)
        if hasattr(self, "_context_panel"):
            self._context_panel.reposition(icon_pos, sz)

    def _on_bubble_dragged(self, bubble_pos: QPoint):
        """Reposition icon to stay to the right of the bubble after a drag."""
        icon_pos = self._bubble.icon_pos_for_bubble(bubble_pos, config.ICON_SIZE)
        self._icon_label.move(icon_pos)

    def _on_bubble_speed_boost(self, enabled: bool):
        """Handle bubble speed boost events."""
        self.signals.bubble_speed.emit(bool(enabled))
