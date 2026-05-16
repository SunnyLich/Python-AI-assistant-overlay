"""
core/hotkeys.py — Global hotkey listener.

Listens for HOTKEY_INVOKE (default: ctrl+u) and calls on_invoke().
Arrow key detection is handled by the Qt intent picker overlay,
which grabs keyboard focus when it appears.
"""
import keyboard
from typing import Callable
import config


class HotkeyListener:
    """
    Registers the global invoke hotkey and dispatches to a callback.

    Usage:
        listener = HotkeyListener(on_invoke=my_callback)
        listener.start()
        ...
        listener.stop()
    """

    def __init__(self, on_invoke: Callable[[], None]):
        """
        Args:
            on_invoke: Called (with no arguments) when the invoke hotkey fires.
        """
        self._on_invoke = on_invoke

    def start(self):
        """Register hotkeys. Call from the main thread."""
        keyboard.add_hotkey(config.HOTKEY_INVOKE, self._on_invoke, suppress=True)

    def stop(self):
        """Unregister all hotkeys."""
        keyboard.unhook_all()
