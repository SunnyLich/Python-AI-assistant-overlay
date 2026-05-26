"""
core/hotkeys.py — Global hotkey listener.

Windows: Uses Win32 RegisterHotKey — the OS consumes the keystroke before it
         reaches any foreground window, so no stray characters are typed.
         Works without administrator privileges.

Linux:   Uses pynput.keyboard.GlobalHotKeys — does NOT consume the keystroke,
         but fires callbacks reliably on X11 without root.

Push-to-talk (press + release on a single key) always uses
pynput.keyboard.Listener, which works on both platforms.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

import config

_IS_WIN = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Windows-only Win32 setup
# ---------------------------------------------------------------------------

if _IS_WIN:
    import ctypes
    import ctypes.wintypes as _wintypes

    _user32   = ctypes.WinDLL("user32",   use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    MOD_ALT      = 0x0001
    MOD_CONTROL  = 0x0002
    MOD_SHIFT    = 0x0004
    MOD_WIN      = 0x0008
    MOD_NOREPEAT = 0x4000

    WM_HOTKEY = 0x0312
    WM_QUIT   = 0x0012

    _MODIFIER_FLAGS: dict[str, int] = {
        "ctrl":    MOD_CONTROL,
        "control": MOD_CONTROL,
        "alt":     MOD_ALT,
        "shift":   MOD_SHIFT,
        "win":     MOD_WIN,
        "cmd":     MOD_WIN,
    }

    _VK_FN: dict[str, int] = {f"f{i}": 0x6F + i for i in range(1, 25)}

    _VK_NAMED: dict[str, int] = {
        "space":     0x20,
        "space_bar": 0x20,
        "tab":       0x09,
        "enter":     0x0D,
        "return":    0x0D,
        "backspace": 0x08,
        "delete":    0x2E,
        "insert":    0x2D,
        "home":      0x24,
        "end":       0x23,
        "pageup":    0x21,
        "pagedown":  0x22,
        "left":      0x25,
        "right":     0x27,
        "up":        0x26,
        "down":      0x28,
    }

    def _parse_hotkey_win32(hotkey_str: str) -> tuple[int, int]:
        """Return (modifier_flags, vk_code) from a string like 'ctrl+alt+q'."""
        mods = MOD_NOREPEAT
        vk   = 0
        for part in hotkey_str.lower().split("+"):
            part = part.strip()
            if part in _MODIFIER_FLAGS:
                mods |= _MODIFIER_FLAGS[part]
            elif part in _VK_FN:
                vk = _VK_FN[part]
            elif part in _VK_NAMED:
                vk = _VK_NAMED[part]
            elif len(part) == 1:
                res = _user32.VkKeyScanA(ctypes.c_char(part.encode()))
                vk  = res & 0xFF
            else:
                raise ValueError(f"Unrecognised hotkey token: {part!r}")
        if vk == 0:
            raise ValueError(f"No non-modifier key found in: {hotkey_str!r}")
        return mods, vk


# ---------------------------------------------------------------------------
# Linux pynput hotkey string conversion
# ---------------------------------------------------------------------------

_PYNPUT_MODS: dict[str, str] = {
    "ctrl":    "<ctrl>",
    "control": "<ctrl>",
    "alt":     "<alt>",
    "shift":   "<shift>",
    "win":     "<cmd>",
    "cmd":     "<cmd>",
}

_PYNPUT_SPECIAL: dict[str, str] = {
    "space":     "<space>",
    "space_bar": "<space>",
    "tab":       "<tab>",
    "enter":     "<enter>",
    "return":    "<enter>",
    "backspace": "<backspace>",
    "delete":    "<delete>",
    "insert":    "<insert>",
    "home":      "<home>",
    "end":       "<end>",
    "pageup":    "<page_up>",
    "pagedown":  "<page_down>",
    "left":      "<left>",
    "right":     "<right>",
    "up":        "<up>",
    "down":      "<down>",
}


def _to_pynput_hotkey(hotkey_str: str) -> str | None:
    """Convert 'ctrl+alt+q' → '<ctrl>+<alt>+q' for pynput GlobalHotKeys."""
    parts: list[str] = []
    for token in hotkey_str.lower().split("+"):
        token = token.strip()
        if token in _PYNPUT_MODS:
            parts.append(_PYNPUT_MODS[token])
        elif token.startswith("f") and token[1:].isdigit():
            parts.append(f"<{token}>")
        elif token in _PYNPUT_SPECIAL:
            parts.append(_PYNPUT_SPECIAL[token])
        elif len(token) == 1:
            parts.append(token)
        else:
            print(f"[hotkeys] Cannot convert token {token!r} to pynput format")
            return None
    return "+".join(parts) if parts else None


# ---------------------------------------------------------------------------
# HotkeyListener — public API (platform-transparent)
# ---------------------------------------------------------------------------

class HotkeyListener:
    """
    Registers global hotkeys and dispatches to callbacks.

    Usage:
        listener = HotkeyListener(on_callers=[...], ...)
        listener.start()
        ...
        listener.stop()
    """

    def __init__(
        self,
        on_callers: list[Callable[[], None]],
        on_add_context: Callable[[], None] | None = None,
        on_clear_context: Callable[[], None] | None = None,
        on_snip: Callable[[], None] | None = None,
        on_voice_start: Callable[[], None] | None = None,
        on_voice_stop: Callable[[], None] | None = None,
    ):
        self._hotkey_defs: list[tuple[str, Callable]] = []
        for i, cb in enumerate(on_callers):
            if i < len(config.CALLER_ROWS):
                hotkey = config.CALLER_ROWS[i]["hotkey"]
                if hotkey:
                    self._hotkey_defs.append((hotkey, cb))
        if on_add_context:
            self._hotkey_defs.append((config.HOTKEY_ADD_CONTEXT, on_add_context))
        if on_clear_context:
            self._hotkey_defs.append((config.HOTKEY_CLEAR_CONTEXT, on_clear_context))
        if on_snip:
            self._hotkey_defs.append((config.HOTKEY_SNIP, on_snip))

        self._on_voice_start = on_voice_start
        self._on_voice_stop  = on_voice_stop

        self._impl = _Win32Impl(self._hotkey_defs) if _IS_WIN else _PynputImpl(self._hotkey_defs)
        self._voice_listener = None
        self._voice_key      = None

    def start(self) -> None:
        self._impl.start()
        if config.HOTKEY_VOICE and (self._on_voice_start or self._on_voice_stop):
            self._start_voice_listener()

    def stop(self) -> None:
        self._impl.stop()
        if self._voice_listener:
            self._voice_listener.stop()
            self._voice_listener = None

    # ------------------------------------------------------------------
    # Push-to-talk voice listener (pynput, works on both platforms)
    # ------------------------------------------------------------------

    def _start_voice_listener(self) -> None:
        from pynput import keyboard as _kb

        s   = config.HOTKEY_VOICE.lower().strip()
        key = None
        try:
            key = _kb.Key[s]
        except KeyError:
            if len(s) == 1:
                key = _kb.KeyCode.from_char(s)

        if key is None:
            print(f"[hotkeys] Voice hotkey {s!r} not recognised for push-to-talk.")
            return

        self._voice_key      = key
        self._voice_listener = _kb.Listener(
            on_press=self._voice_press,
            on_release=self._voice_release,
        )
        self._voice_listener.daemon = True
        self._voice_listener.start()

    def _voice_press(self, key) -> None:
        if key == self._voice_key and self._on_voice_start:
            self._on_voice_start()

    def _voice_release(self, key) -> None:
        if key == self._voice_key and self._on_voice_stop:
            self._on_voice_stop()


# ---------------------------------------------------------------------------
# Windows implementation — Win32 RegisterHotKey message pump
# ---------------------------------------------------------------------------

class _Win32Impl:
    def __init__(self, hotkey_defs: list[tuple[str, Callable]]):
        self._hotkey_defs = hotkey_defs
        self._callbacks: dict[int, Callable] = {}
        self._pump_tid   = 0
        self._pump_ready = threading.Event()
        self._pump_thread: threading.Thread | None = None

    def start(self) -> None:
        self._pump_thread = threading.Thread(
            target=self._message_pump,
            daemon=True,
            name="hotkey-pump",
        )
        self._pump_thread.start()
        self._pump_ready.wait(timeout=2.0)

    def stop(self) -> None:
        if self._pump_tid:
            _user32.PostThreadMessageW(self._pump_tid, WM_QUIT, 0, 0)
            self._pump_tid = 0

    def _message_pump(self) -> None:
        import ctypes
        self._pump_tid = _kernel32.GetCurrentThreadId()
        self._pump_ready.set()

        registered: list[int] = []
        for i, (hotkey_str, cb) in enumerate(self._hotkey_defs):
            hk_id = i + 1
            try:
                mods, vk = _parse_hotkey_win32(hotkey_str)
                if _user32.RegisterHotKey(None, hk_id, mods, vk):
                    self._callbacks[hk_id] = cb
                    registered.append(hk_id)
                else:
                    err = ctypes.get_last_error()
                    print(f"[hotkeys] RegisterHotKey({hotkey_str!r}) failed (error {err})")
            except ValueError as exc:
                print(f"[hotkeys] Cannot parse {hotkey_str!r}: {exc}")

        msg = _wintypes.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_HOTKEY:
                cb = self._callbacks.get(msg.wParam)
                if cb:
                    threading.Thread(target=cb, daemon=True).start()

        for hk_id in registered:
            _user32.UnregisterHotKey(None, hk_id)


# ---------------------------------------------------------------------------
# Linux implementation — pynput GlobalHotKeys
# ---------------------------------------------------------------------------

class _PynputImpl:
    def __init__(self, hotkey_defs: list[tuple[str, Callable]]):
        self._hotkey_defs = hotkey_defs
        self._global_hotkeys = None

    def start(self) -> None:
        from pynput import keyboard as _kb  # type: ignore

        mapping: dict[str, Callable] = {}
        for hotkey_str, cb in self._hotkey_defs:
            pynput_str = _to_pynput_hotkey(hotkey_str)
            if pynput_str:
                # Wrap cb so it runs in its own thread (mirrors Win32 behaviour)
                def _make_cb(f: Callable) -> Callable:
                    def _cb():
                        threading.Thread(target=f, daemon=True).start()
                    return _cb
                mapping[pynput_str] = _make_cb(cb)
            else:
                print(f"[hotkeys] Skipping hotkey {hotkey_str!r}: cannot convert to pynput format")

        if not mapping:
            print("[hotkeys] No hotkeys registered (pynput).")
            return

        self._global_hotkeys = _kb.GlobalHotKeys(mapping)
        self._global_hotkeys.daemon = True
        self._global_hotkeys.start()
        print(f"[hotkeys] Registered {len(mapping)} hotkey(s) via pynput.")

    def stop(self) -> None:
        if self._global_hotkeys is not None:
            self._global_hotkeys.stop()
            self._global_hotkeys = None
