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
    Registers global hotkeys and dispatches to callbacks.

    Usage:
        listener = HotkeyListener(
            on_invoke=my_callback,
            on_add_context=add_cb,
            on_clear_context=clear_cb,
        )
        listener.start()
        ...
        listener.stop()
    """

    def __init__(
        self,
        on_invoke: Callable[[], None],
        on_add_context: Callable[[], None] | None = None,
        on_clear_context: Callable[[], None] | None = None,
        on_snip: Callable[[], None] | None = None,
    ):
        self._on_invoke = on_invoke
        self._on_add_context = on_add_context
        self._on_clear_context = on_clear_context
        self._on_snip = on_snip

    def start(self):
        """Register hotkeys. Call from the main thread."""
        keyboard.add_hotkey(config.HOTKEY_INVOKE, self._on_invoke, suppress=True)
        if self._on_add_context:
            keyboard.add_hotkey(config.HOTKEY_ADD_CONTEXT, self._on_add_context, suppress=True)
        if self._on_clear_context:
            keyboard.add_hotkey(config.HOTKEY_CLEAR_CONTEXT, self._on_clear_context, suppress=True)
        if self._on_snip:
            keyboard.add_hotkey(config.HOTKEY_SNIP, self._on_snip, suppress=True)

    def stop(self):
        """Unregister all hotkeys."""
        keyboard.unhook_all()
