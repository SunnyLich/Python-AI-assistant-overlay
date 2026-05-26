"""
core/platform_utils.py — Thin cross-platform helpers for keyboard injection
and window-focus management.

Windows: uses ctypes Win32 APIs.
Linux:   uses pynput.keyboard.Controller (X11) for key injection and
         python-xlib + ewmh for window management (both pip-installable;
         no system packages required).
macOS:   stubs — to be completed when macOS support is added.
"""
from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


# ---------------------------------------------------------------------------
# Key injection
# ---------------------------------------------------------------------------

def send_keys(combo: str) -> None:
    """
    Inject a key combo (e.g. "ctrl+c", "ctrl+v") into the focused window.

    Windows: delegates to the *keyboard* library (pywin32-based injection).
    Linux:   uses pynput.keyboard.Controller via Xlib — no root required.
    """
    if IS_WIN:
        import keyboard  # type: ignore
        keyboard.send(combo)
    else:
        _send_keys_pynput(combo)


def _send_keys_pynput(combo: str) -> None:
    from pynput.keyboard import Controller, Key, KeyCode  # type: ignore

    _KEY_MAP = {
        "ctrl":      Key.ctrl,
        "control":   Key.ctrl,
        "alt":       Key.alt,
        "shift":     Key.shift,
        "win":       Key.cmd,
        "cmd":       Key.cmd,
        "enter":     Key.enter,
        "return":    Key.enter,
        "tab":       Key.tab,
        "space":     Key.space,
        "backspace": Key.backspace,
        "delete":    Key.delete,
        "esc":       Key.esc,
        "escape":    Key.esc,
        "up":        Key.up,
        "down":      Key.down,
        "left":      Key.left,
        "right":     Key.right,
        "home":      Key.home,
        "end":       Key.end,
        "pageup":    Key.page_up,
        "pagedown":  Key.page_down,
        **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 13)},
    }

    ctrl = Controller()
    keys = []
    for token in combo.lower().split("+"):
        token = token.strip()
        if token in _KEY_MAP:
            keys.append(_KEY_MAP[token])
        elif len(token) == 1:
            keys.append(KeyCode.from_char(token))

    for k in keys:
        ctrl.press(k)
    for k in reversed(keys):
        ctrl.release(k)


# ---------------------------------------------------------------------------
# Active window / window ID
# ---------------------------------------------------------------------------

def get_foreground_window() -> int:
    """
    Return an opaque integer identifying the current foreground window.
    Windows: Win32 HWND.
    Linux:   X11 window ID via python-xlib/ewmh (0 if unavailable).
    macOS:   always 0 (not yet implemented).
    """
    if IS_WIN:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    elif not IS_MAC:
        return _xlib_active_window()
    return 0


def set_foreground_window(wid: int) -> None:
    """
    Bring *wid* to the foreground.
    Windows: SetForegroundWindow.
    Linux:   ewmh setActiveWindow + display flush (no-op if unavailable).
    macOS:   no-op (not yet implemented).
    """
    if not wid:
        return
    if IS_WIN:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(wid)
    elif not IS_MAC:
        _xlib_focus_window(wid)


# ---------------------------------------------------------------------------
# X11 window helpers (Linux only) — backed by python-xlib + ewmh
# ---------------------------------------------------------------------------

_ewmh_instance = None


def _get_ewmh():
    global _ewmh_instance
    if _ewmh_instance is None:
        from ewmh import EWMH  # type: ignore
        _ewmh_instance = EWMH()
    return _ewmh_instance


def _xlib_active_window() -> int:
    try:
        ew = _get_ewmh()
        win = ew.getActiveWindow()
        return win.id if win is not None else 0
    except Exception:
        return 0


def _xlib_focus_window(wid: int) -> None:
    try:
        ew = _get_ewmh()
        win = ew.display.create_resource_object("window", wid)
        ew.setActiveWindow(win)
        ew.display.flush()
    except Exception:
        pass


def get_window_title(wid: int) -> str:
    """Return the title of X11 window *wid*, or "" if unavailable."""
    try:
        ew = _get_ewmh()
        win = ew.display.create_resource_object("window", wid)
        return ew.getWmName(win) or ""
    except Exception:
        return ""


def get_window_pid(wid: int) -> int:
    """Return the PID of X11 window *wid*, or 0 if unavailable."""
    try:
        ew = _get_ewmh()
        win = ew.display.create_resource_object("window", wid)
        return ew.getWmPid(win) or 0
    except Exception:
        return 0


def list_visible_windows() -> list[int]:
    """Return X11 window IDs for all client windows reported by the WM."""
    try:
        ew = _get_ewmh()
        return [win.id for win in (ew.getClientList() or [])]
    except Exception:
        return []
