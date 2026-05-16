"""
core/hotkeys.py — Global hotkey listener.

Listens for:
  - HOTKEY_INVOKE (default: ctrl+u) to trigger the AI assistant.
  - After invoke, the next arrow key press selects an intent shortcut.
  - Escape cancels the pending intent selection.
"""
import threading
import time
import keyboard
from typing import Callable
import config


class HotkeyListener:
    """
    Registers global hotkeys and dispatches to callbacks.

    Usage:
        listener = HotkeyListener(on_invoke=my_callback)
        listener.start()
        ...
        listener.stop()
    """

    ARROW_KEYS = {"up", "down", "left", "right"}

    def __init__(self, on_invoke: Callable[[str, str], None]):
        """
        Args:
            on_invoke: Called with (intent_key, intent_prompt) when an intent
                       shortcut is selected. intent_key is the arrow direction.
                       For a plain invoke (no arrow), called with ("", "").
        """
        self._on_invoke = on_invoke
        self._pending = False
        self._pending_timer: threading.Timer | None = None
        self._hooks: list = []

    def start(self):
        """Register hotkeys. Call from the main thread."""
        self._hooks.append(
            keyboard.add_hotkey(config.HOTKEY_INVOKE, self._handle_invoke, suppress=True)
        )
        for key in self.ARROW_KEYS:
            self._hooks.append(
                keyboard.on_press_key(key, self._handle_arrow, suppress=False)
            )
        keyboard.on_press_key("esc", self._cancel, suppress=False)

    def stop(self):
        """Unregister all hotkeys."""
        keyboard.unhook_all()
        self._hooks.clear()

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _handle_invoke(self):
        """Called when the invoke hotkey fires."""
        self._pending = True
        # If the user doesn't press an arrow within 600ms, treat as plain invoke
        self._pending_timer = threading.Timer(0.6, self._fire_plain)
        self._pending_timer.start()

    def _handle_arrow(self, event):
        if not self._pending:
            return
        key = event.name  # "up" | "down" | "left" | "right"
        self._cancel_timer()
        self._pending = False
        prompt = config.INTENT_SHORTCUTS.get(key, "")
        self._on_invoke(key, prompt)

    def _fire_plain(self):
        """No arrow key pressed in time — plain invocation, no intent shortcut."""
        self._pending = False
        self._on_invoke("", "")

    def _cancel(self, _event=None):
        """Escape pressed — cancel pending intent selection."""
        if self._pending:
            self._cancel_timer()
            self._pending = False

    def _cancel_timer(self):
        if self._pending_timer:
            self._pending_timer.cancel()
            self._pending_timer = None
