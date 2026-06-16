"""Context targeting helpers shared by legacy and supervisor runtimes."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from core import context_fetcher

log = logging.getLogger("wisp.context.hotkey")


def window_pid_win(hwnd: int, *, is_win: bool) -> int:
    if not is_win or not hwnd:
        return 0
    try:
        import ctypes

        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        return 0


def window_title_win(hwnd: int, *, is_win: bool) -> str:
    if not is_win or not hwnd:
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(int(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(int(hwnd), buf, length + 1)
        return str(buf.value or "")
    except Exception:
        return ""


def is_external_context_window_win(
    hwnd: int,
    *,
    is_win: bool,
    pid_for_hwnd: Callable[[int], int],
    title_for_hwnd: Callable[[int], str],
) -> bool:
    if not is_win or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(hwnd)
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False
        if pid_for_hwnd(hwnd) == os.getpid():
            return False
        return bool(title_for_hwnd(hwnd).strip())
    except Exception:
        return False


def find_external_context_window_win(
    start_hwnd: int,
    *,
    is_win: bool,
    is_external: Callable[[int], bool],
) -> int:
    if not is_win:
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        gw_hwndnext = 2
        hwnd = user32.GetWindow(int(start_hwnd or 0), gw_hwndnext) if start_hwnd else 0
        if not hwnd:
            hwnd = user32.GetTopWindow(0)
        seen: set[int] = set()
        while hwnd and len(seen) < 200:
            hwnd_i = int(hwnd)
            if hwnd_i in seen:
                break
            seen.add(hwnd_i)
            if is_external(hwnd_i):
                return hwnd_i
            hwnd = user32.GetWindow(hwnd_i, gw_hwndnext)
    except Exception:
        return 0
    return 0


def browser_context_text(snapshot: Any) -> str:
    active_window = getattr(snapshot, "active_window", None)
    url = str(getattr(active_window, "url", "") or "").strip()
    hwnd = int(getattr(active_window, "hwnd", 0) or 0)
    content = str(getattr(snapshot, "browser_content", "") or "").strip()
    if not content and (url or hwnd):
        content = context_fetcher.fetch_browser_content_for_window(url, hwnd)
    log.info("browser context url=%r hwnd=%s chars=%d", url, hwnd, len(content or ""))
    bits: list[str] = []
    if url:
        bits.append(f"URL: {url}")
    if content:
        bits.append(content)
    return "[Browser/Web]\n" + "\n\n".join(bits) if bits else ""


def is_browser_window(window: Any) -> bool:
    if not window:
        return False
    if str(getattr(window, "url", "") or "").strip():
        return True
    process = str(getattr(window, "process_name", "") or "").strip().lower()
    browser_procs = getattr(context_fetcher, "_BROWSER_PROCS", frozenset())
    return bool(process and process in browser_procs)


def context_priority_source(snapshot: Any, ambient_text: str, active_document_text: str) -> str:
    if not active_document_text or "[Browser/Web]" not in (ambient_text or ""):
        return ""
    return "Browser/Web" if is_browser_window(getattr(snapshot, "active_window", None)) else "Active document"
