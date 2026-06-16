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
_IS_MAC = sys.platform == "darwin"

_HOTKEY_MODIFIER_TOKENS = {"ctrl", "control", "alt", "shift", "win", "cmd", "command", "option"}
_HOTKEY_ACTION_MODIFIER_TOKENS = {"ctrl", "control", "alt", "win", "cmd", "command", "option"}


def _hotkey_parts(hotkey_str: str) -> list[str]:
    return [part.strip().lower() for part in (hotkey_str or "").split("+") if part.strip()]


def _is_f_key(token: str) -> bool:
    if not (token.startswith("f") and token[1:].isdigit()):
        return False
    try:
        return 1 <= int(token[1:]) <= 24
    except ValueError:
        return False


def is_safe_global_hotkey(hotkey_str: str) -> bool:
    """Return True if a global hotkey won't steal ordinary typing keys.

    RegisterHotKey/RegisterEventHotKey reserve matching keys system-wide. Bare
    printable/navigation keys like "space" or "s" should never be accepted as
    global app hotkeys because they stop reaching the foreground app. Function
    keys remain allowed for push-to-talk style shortcuts.
    """
    parts = _hotkey_parts(hotkey_str)
    if not parts:
        return False
    has_action_modifier = any(part in _HOTKEY_ACTION_MODIFIER_TOKENS for part in parts)
    key_parts = [part for part in parts if part not in _HOTKEY_MODIFIER_TOKENS]
    if not key_parts:
        return False
    return has_action_modifier or any(_is_f_key(part) for part in key_parts)


# ANSI virtual keycodes (kVK_*), layout-independent. Shared by the Carbon
# RegisterEventHotKey backend and the off-console CGEventTap backend (the latter
# lives in macos_py.workers.hotkey_helper and imports this), so it is defined at
# module level rather than inside the macOS-only block.
MACOS_VIRTUAL_KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "backspace": 51, "escape": 53, "esc": 53, "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121, "left": 123, "right": 124, "down": 125,
    "up": 126,
    **{f"f{n}": vk for n, vk in {
        1: 122, 2: 120, 3: 99, 4: 118, 5: 96, 6: 97, 7: 98, 8: 100,
        9: 101, 10: 109, 11: 103, 12: 111,
    }.items()},
}


def _macos_accessibility_enabled() -> bool:
    if not _IS_MAC:
        return True
    try:
        import ctypes
        import ctypes.util

        app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices") or "ApplicationServices"
        )
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        # If the trust check is unavailable, do not block startup here.
        return True

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
        if not is_safe_global_hotkey(hotkey_str):
            raise ValueError(f"Unsafe global hotkey: {hotkey_str!r}")
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


def _to_pynput_hotkey(hotkey_str: str, *, ctrl_as_cmd: bool = False) -> str | None:
    """Convert 'ctrl+alt+q' → '<ctrl>+<alt>+q' for pynput GlobalHotKeys."""
    if not is_safe_global_hotkey(hotkey_str):
        print(f"[hotkeys] Refusing unsafe global hotkey {hotkey_str!r}")
        return None
    parts: list[str] = []
    for token in hotkey_str.lower().split("+"):
        token = token.strip()
        if token in _PYNPUT_MODS:
            if _IS_MAC and ctrl_as_cmd and token in {"ctrl", "control"}:
                parts.append("<cmd>")
                continue
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


def _to_pynput_hotkeys(hotkey_str: str) -> list[str]:
    primary = _to_pynput_hotkey(hotkey_str)
    if not primary:
        return []

    aliases = [primary]
    if not _IS_MAC:
        return aliases

    tokens = {token.strip() for token in hotkey_str.lower().split("+")}
    has_ctrl = bool(tokens & {"ctrl", "control"})
    has_cmd = bool(tokens & {"cmd", "win"})
    if has_ctrl and not has_cmd:
        cmd_alias = _to_pynput_hotkey(hotkey_str, ctrl_as_cmd=True)
        if cmd_alias and cmd_alias not in aliases:
            aliases.append(cmd_alias)
    return aliases


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
        on_dictate_start: Callable[[], None] | None = None,
        on_dictate_stop: Callable[[], None] | None = None,
        extra_hotkeys: list[tuple[str, Callable[[], None]]] | None = None,
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
        for hotkey, cb in extra_hotkeys or []:
            if hotkey and cb:
                self._hotkey_defs.append((hotkey, cb))
        self._on_voice_start = on_voice_start
        self._on_voice_stop  = on_voice_stop
        self._on_dictate_start = on_dictate_start
        self._on_dictate_stop  = on_dictate_stop
        self._dictate_hotkey = getattr(config, "HOTKEY_DICTATE", "") or ""

        if _IS_WIN:
            self._impl = _Win32Impl(self._hotkey_defs)
        elif _IS_MAC:
            self._impl = _CarbonImpl(
                self._hotkey_defs,
                voice_hotkey=config.HOTKEY_VOICE,
                on_voice_start=on_voice_start,
                on_voice_stop=on_voice_stop,
            )
        else:
            self._impl = _PynputImpl(self._hotkey_defs)
        self._voice_listener = None
        self._voice_key      = None
        # Maps a resolved pynput key object -> (on_press, on_release) so one
        # listener can serve both the voice and dictation push-to-talk keys.
        self._ptt_map: dict = {}

    def start(self) -> bool:
        started = self._impl.start()
        if not started:
            return False
        # The push-to-talk listener is a pynput keyboard tap. On macOS that tap
        # decodes every keystroke off the main thread (HIToolbox keycode_context),
        # which trace-traps (SIGTRAP) — same flaw as GlobalHotKeys. Skip it there.
        has_voice = bool(config.HOTKEY_VOICE and (self._on_voice_start or self._on_voice_stop))
        has_dictate = bool(self._dictate_hotkey and (self._on_dictate_start or self._on_dictate_stop))
        if not _IS_MAC and (has_voice or has_dictate):
            self._start_voice_listener()
        return True

    def status(self) -> dict[str, object]:
        status_fn = getattr(self._impl, "status", None)
        if callable(status_fn):
            return dict(status_fn())
        return {"started": True, "registered": len(self._hotkey_defs), "requested": len(self._hotkey_defs)}

    def stop(self) -> None:
        self._impl.stop()
        if self._voice_listener:
            vl = self._voice_listener
            self._voice_listener = None
            vl.stop()
            # Wait for the listener thread to exit so its Quartz event tap is
            # released before any replacement tap is created. On macOS two
            # overlapping CGEventTaps in one process trace-trap (SIGTRAP).
            try:
                vl.join(timeout=2.0)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Push-to-talk voice listener (pynput, works on both platforms)
    # ------------------------------------------------------------------

    def _start_voice_listener(self) -> None:
        from pynput import keyboard as _kb

        def _resolve(hotkey_str: str):
            s = (hotkey_str or "").lower().strip()
            if not s:
                return None
            try:
                return _kb.Key[s]
            except KeyError:
                return _kb.KeyCode.from_char(s) if len(s) == 1 else None

        bindings = [
            (config.HOTKEY_VOICE, self._on_voice_start, self._on_voice_stop, "voice"),
            (self._dictate_hotkey, self._on_dictate_start, self._on_dictate_stop, "dictation"),
        ]
        self._ptt_map = {}
        for hotkey_str, on_start, on_stop, label in bindings:
            if not (hotkey_str and (on_start or on_stop)):
                continue
            key = _resolve(hotkey_str)
            if key is None:
                print(f"[hotkeys] {label} hotkey {hotkey_str!r} not recognised for push-to-talk.")
                continue
            self._ptt_map[key] = (on_start, on_stop)

        if not self._ptt_map:
            return
        self._voice_listener = _kb.Listener(
            on_press=self._voice_press,
            on_release=self._voice_release,
        )
        self._voice_listener.daemon = True
        self._voice_listener.start()

    def _voice_press(self, key) -> None:
        binding = self._ptt_map.get(key)
        if binding and binding[0]:
            binding[0]()

    def _voice_release(self, key) -> None:
        binding = self._ptt_map.get(key)
        if binding and binding[1]:
            binding[1]()


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
        self._started = False
        self._status_reason = "not started"

    def start(self) -> bool:
        self._pump_thread = threading.Thread(
            target=self._message_pump,
            daemon=True,
            name="hotkey-pump",
        )
        self._pump_thread.start()
        self._pump_ready.wait(timeout=2.0)
        return self._started

    def status(self) -> dict[str, object]:
        return {
            "started": self._started,
            "registered": len(self._callbacks),
            "requested": len(self._hotkey_defs),
            "reason": self._status_reason,
        }

    def stop(self) -> None:
        if self._pump_tid:
            _user32.PostThreadMessageW(self._pump_tid, WM_QUIT, 0, 0)
            self._pump_tid = 0

    def _message_pump(self) -> None:
        import ctypes
        self._pump_tid = _kernel32.GetCurrentThreadId()

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

        self._started = bool(registered)
        if registered:
            self._status_reason = f"registered {len(registered)} of {len(self._hotkey_defs)} hotkey(s)"
        elif self._hotkey_defs:
            self._status_reason = "all RegisterHotKey calls failed; hotkeys may be reserved by another app"
        else:
            self._status_reason = "no hotkeys configured"
        print(f"[hotkeys] Win32 {self._status_reason}")
        self._pump_ready.set()
        if not registered:
            return

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
        self._callbacks.clear()
        self._started = False
        self._status_reason = "stopped"


# ---------------------------------------------------------------------------
# macOS implementation — Carbon RegisterEventHotKey
# ---------------------------------------------------------------------------
#
# pynput's GlobalHotKeys installs a CGEventTap that decodes EVERY keystroke via
# main-thread-only HIToolbox APIs on its own background thread — that trace-traps
# (SIGTRAP) on macOS. RegisterEventHotKey instead asks the OS to watch for one
# specific combo and delivers a Carbon event to the application event target;
# Qt pumps that event on the main run loop, so our handler runs on the main
# thread. Bonus: registered hot keys need NO Accessibility permission.

if _IS_MAC:
    import ctypes
    import ctypes.util

    class _EventTypeSpec(ctypes.Structure):
        _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

    class _EventHotKeyID(ctypes.Structure):
        _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

    # OSStatus handler(EventHandlerCallRef, EventRef, void *userData)
    _CARBON_HANDLER = ctypes.CFUNCTYPE(
        ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )

    def _four_char_code(s: str) -> int:
        return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])

    _kEventClassKeyboard     = _four_char_code("keyb")
    _kEventHotKeyPressed     = 5
    _kEventHotKeyReleased    = 6
    _kEventParamDirectObject = _four_char_code("----")  # kEventParamDirectObject is '----'
    _typeEventHotKeyID       = _four_char_code("hkid")

    # Loading Carbon only works on a real macOS host. The macOS unit tests reload
    # this module with sys.platform faked to "darwin" on Windows/Linux, where the
    # framework is absent — degrade to None there instead of raising on import.
    _carbon = None
    try:
        _carbon = ctypes.CDLL(ctypes.util.find_library("Carbon") or "Carbon")
        _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        _carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        ]
        _carbon.RegisterEventHotKey.restype = ctypes.c_int32
        _carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        _carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        _carbon.GetEventParameter.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
        ]
        _carbon.GetEventParameter.restype = ctypes.c_int32
        if hasattr(_carbon, "GetEventKind"):
            _carbon.GetEventKind.argtypes = [ctypes.c_void_p]
            _carbon.GetEventKind.restype = ctypes.c_uint32
        if hasattr(_carbon, "RemoveEventHandler"):
            _carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
            _carbon.RemoveEventHandler.restype = ctypes.c_int32
    except Exception as _carbon_exc:  # noqa: BLE001
        _carbon = None
        print(f"[hotkeys] Carbon framework unavailable: {_carbon_exc}")

    # Old-style Carbon modifier masks (NOT the Cocoa CGEventFlags).
    _CARBON_MODS: dict[str, int] = {
        "cmd": 0x0100, "command": 0x0100, "win": 0x0100,  # cmdKey
        "shift": 0x0200,                                   # shiftKey
        "alt": 0x0800, "option": 0x0800,                   # optionKey
        "ctrl": 0x1000, "control": 0x1000,                 # controlKey
    }

    # ANSI virtual keycodes (kVK_*), layout-independent. Defined at module level.
    _CARBON_VK = MACOS_VIRTUAL_KEYCODES

    def _parse_hotkey_carbon(hotkey_str: str) -> tuple[int, int] | None:
        """Return (carbon_modifiers, virtual_keycode) or None if unparseable."""
        if not is_safe_global_hotkey(hotkey_str):
            return None
        mods = 0
        keycode: int | None = None
        for part in hotkey_str.lower().split("+"):
            part = part.strip()
            if part in _CARBON_MODS:
                mods |= _CARBON_MODS[part]
            elif part in _CARBON_VK:
                keycode = _CARBON_VK[part]
        return (mods, keycode) if keycode is not None else None


class _CarbonImpl:
    def __init__(
        self,
        hotkey_defs: list[tuple[str, Callable]],
        *,
        voice_hotkey: str = "",
        on_voice_start: Callable[[], None] | None = None,
        on_voice_stop: Callable[[], None] | None = None,
    ):
        self._hotkey_defs = hotkey_defs
        self._handler_ref = ctypes.c_void_p()
        self._hotkey_refs: list = []
        self._callbacks: dict[int, Callable] = {}
        self._voice_hotkey = voice_hotkey
        self._on_voice_start = on_voice_start
        self._on_voice_stop = on_voice_stop
        self._voice_id = 0
        self._handler_upp = None  # keep the CFUNCTYPE alive while installed
        self._signature = _four_char_code("Wisp")

    def start(self) -> bool:
        if _carbon is None:
            print("[hotkeys] Carbon backend unavailable; global hotkeys disabled.")
            return False
        target = _carbon.GetApplicationEventTarget()
        if not target:
            print("[hotkeys] Carbon: no application event target.")
            return False

        # One handler dispatches all of our registered hot keys.
        self._handler_upp = _CARBON_HANDLER(self._dispatch)
        specs = (_EventTypeSpec * 2)(
            _EventTypeSpec(_kEventClassKeyboard, _kEventHotKeyPressed),
            _EventTypeSpec(_kEventClassKeyboard, _kEventHotKeyReleased),
        )
        install = getattr(_carbon, "InstallEventHandlerUPP", None) or _carbon.InstallEventHandler
        install.argtypes = [
            ctypes.c_void_p, _CARBON_HANDLER, ctypes.c_uint32,
            ctypes.POINTER(_EventTypeSpec), ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        install.restype = ctypes.c_int32
        status = install(
            target, self._handler_upp, len(specs), specs, None,
            ctypes.byref(self._handler_ref),
        )
        if status != 0:
            print(f"[hotkeys] Carbon InstallEventHandler failed (status {status}).")
            self._handler_upp = None
            return False

        registered = 0
        for i, (hotkey_str, cb) in enumerate(self._hotkey_defs):
            parsed = _parse_hotkey_carbon(hotkey_str)
            if parsed is None:
                print(f"[hotkeys] Carbon: cannot parse {hotkey_str!r}.")
                continue
            mods, keycode = parsed
            hk_id = i + 1
            ref = ctypes.c_void_p()
            status = _carbon.RegisterEventHotKey(
                keycode, mods, _EventHotKeyID(self._signature, hk_id),
                target, 0, ctypes.byref(ref),
            )
            if status != 0:
                print(f"[hotkeys] Carbon RegisterEventHotKey({hotkey_str!r}) failed (status {status}).")
                continue
            self._callbacks[hk_id] = cb
            self._hotkey_refs.append(ref)
            registered += 1
            print(f"[hotkeys] Carbon registered {hotkey_str!r} (keycode={keycode}, mods={mods}).")

        if self._voice_hotkey and (self._on_voice_start or self._on_voice_stop):
            parsed = _parse_hotkey_carbon(self._voice_hotkey)
            if parsed is None:
                print(f"[hotkeys] Carbon: cannot parse voice hotkey {self._voice_hotkey!r}.")
            else:
                mods, keycode = parsed
                hk_id = len(self._hotkey_defs) + 1
                ref = ctypes.c_void_p()
                status = _carbon.RegisterEventHotKey(
                    keycode, mods, _EventHotKeyID(self._signature, hk_id),
                    target, 0, ctypes.byref(ref),
                )
                if status != 0:
                    print(
                        f"[hotkeys] Carbon RegisterEventHotKey({self._voice_hotkey!r}) "
                        f"failed (status {status})."
                    )
                else:
                    self._voice_id = hk_id
                    self._hotkey_refs.append(ref)
                    registered += 1
                    print(
                        f"[hotkeys] Carbon registered voice {self._voice_hotkey!r} "
                        f"(keycode={keycode}, mods={mods})."
                    )

        if registered == 0:
            print("[hotkeys] Carbon: no hotkeys registered.")
            self.stop()
            return False
        print(f"[hotkeys] Registered {registered} hotkey(s) via Carbon RegisterEventHotKey.")
        return True

    def _dispatch(self, _next_handler, event, _user_data) -> int:
        # This handler runs in the HIToolbox Carbon event-dispatch context on the
        # main thread. Offload the callback to a daemon thread (mirroring the Win32
        # backend) — do NOT run it inline here: the callbacks do heavy/blocking work
        # (STT, LLM streaming, screen capture) and touch CoreAudio/AppKit/Quartz, and
        # re-entering those native frameworks from inside the Carbon callback (rather
        # than cleanly on the run loop) segfaults. The individual native sub-ops are
        # already hopped onto the main thread by core.system.main_thread.run_on_main
        # (see core.platform_utils / core.capture / core.stt), so running the callback
        # on a worker thread is safe — the main-thread-only work still lands on main.
        try:
            hk_id = _EventHotKeyID()
            status = _carbon.GetEventParameter(
                event, _kEventParamDirectObject, _typeEventHotKeyID, None,
                ctypes.sizeof(hk_id), None, ctypes.byref(hk_id),
            )
            if status == 0 and hk_id.signature == self._signature:
                kind = (
                    int(_carbon.GetEventKind(event))
                    if hasattr(_carbon, "GetEventKind")
                    else _kEventHotKeyPressed
                )
                print(f"[hotkeys] Carbon hotkey fired (id={hk_id.id}, kind={kind}).")
                if hk_id.id == self._voice_id:
                    cb = (
                        self._on_voice_start
                        if kind == _kEventHotKeyPressed
                        else self._on_voice_stop
                        if kind == _kEventHotKeyReleased
                        else None
                    )
                else:
                    cb = self._callbacks.get(hk_id.id) if kind == _kEventHotKeyPressed else None
                if cb:
                    threading.Thread(target=cb, daemon=True).start()
        except Exception as exc:
            print(f"[hotkeys] Carbon dispatch error: {exc}")
        return 0  # noErr

    def stop(self) -> None:
        for ref in self._hotkey_refs:
            try:
                _carbon.UnregisterEventHotKey(ref)
            except Exception:
                pass
        self._hotkey_refs = []
        self._callbacks = {}
        self._voice_id = 0
        if self._handler_ref and hasattr(_carbon, "RemoveEventHandler"):
            try:
                _carbon.RemoveEventHandler(self._handler_ref)
            except Exception:
                pass
        self._handler_ref = ctypes.c_void_p()
        self._handler_upp = None


# ---------------------------------------------------------------------------
# Linux implementation — pynput GlobalHotKeys
# ---------------------------------------------------------------------------

class _PynputImpl:
    def __init__(self, hotkey_defs: list[tuple[str, Callable]]):
        self._hotkey_defs = hotkey_defs
        self._global_hotkeys = None

    def start(self) -> bool:
        from pynput import keyboard as _kb  # type: ignore

        if _IS_MAC and not _macos_accessibility_enabled():
            print("[hotkeys] macOS Accessibility permission is required for global hotkeys.")
            return False

        mapping: dict[str, Callable] = {}
        for hotkey_str, cb in self._hotkey_defs:
            pynput_hotkeys = _to_pynput_hotkeys(hotkey_str)
            if not pynput_hotkeys:
                print(f"[hotkeys] Skipping hotkey {hotkey_str!r}: cannot convert to pynput format")
                continue

            # Wrap cb so it runs in its own thread (mirrors Win32 behaviour)
            def _make_cb(f: Callable) -> Callable:
                def _cb():
                    threading.Thread(target=f, daemon=True).start()
                return _cb

            wrapped_cb = _make_cb(cb)
            for pynput_str in pynput_hotkeys:
                mapping[pynput_str] = wrapped_cb

        if not mapping:
            print("[hotkeys] No hotkeys registered (pynput).")
            return False

        try:
            self._global_hotkeys = _kb.GlobalHotKeys(mapping)
            self._global_hotkeys.daemon = True
            self._global_hotkeys.start()
        except Exception as exc:
            self._global_hotkeys = None
            print(f"[hotkeys] Failed to start pynput hotkeys: {exc}")
            return False
        print(f"[hotkeys] Registered {len(mapping)} hotkey(s) via pynput.")
        return True

    def stop(self) -> None:
        if self._global_hotkeys is not None:
            gh = self._global_hotkeys
            self._global_hotkeys = None
            gh.stop()
            # Block until the listener thread has torn down its Quartz event
            # tap + run loop; creating the next tap before this one is released
            # trace-traps (SIGTRAP) on macOS. Harmless no-op on Linux.
            try:
                gh.join(timeout=2.0)
            except RuntimeError:
                pass
