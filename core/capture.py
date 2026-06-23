"""
core/capture.py — Input capture: highlighted text and screen snippets.

Two modes:
  1. Text selection  — reads whatever the user has highlighted.
                       Tries Windows UI Automation first (no clipboard touch),
                       falls back to clipboard save/restore if UIA fails.
  2. Screen snippet  — takes a screenshot of the active monitor region.
"""
from __future__ import annotations

import sys
import time
import logging
import pyperclip
from PIL import Image
import io

from core.system.main_thread import run_on_main

_IS_LINUX = sys.platform.startswith("linux")
_IS_MAC = sys.platform == "darwin"
_log = logging.getLogger("wisp.capture")


# ------------------------------------------------------------------
# UIA singleton — initialised once, reused across calls
# ------------------------------------------------------------------
_uia = None
_UIA_TextPatternId = 10014
_UIA_TextPatternRangeEndpoint_Start = 0
_UIA_TextPatternRangeEndpoint_End = 1


def _get_uia():
    """Return uia."""
    global _uia
    if _uia is None:
        try:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as uiac  # type: ignore
            _uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=uiac.IUIAutomation,
            )
        except Exception:
            _uia = False   # mark as unavailable so we don't retry
    return _uia if _uia is not False else None


def _get_selected_text_uia() -> str | None:
    """Read selected text via Windows UI Automation — no clipboard involved."""
    uia = _get_uia()
    if uia is None:
        return None
    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore
        el = uia.GetFocusedElement()
        raw_pattern = el.GetCurrentPattern(_UIA_TextPatternId)
        tp = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
        selections = tp.GetSelection()
        if selections.Length == 0:
            return None
        start_endpoint = getattr(
            uiac,
            "TextPatternRangeEndpoint_Start",
            _UIA_TextPatternRangeEndpoint_Start,
        )
        end_endpoint = getattr(
            uiac,
            "TextPatternRangeEndpoint_End",
            _UIA_TextPatternRangeEndpoint_End,
        )
        selected_parts: list[str] = []
        for idx in range(selections.Length):
            text_range = selections.GetElement(idx)
            try:
                if text_range.CompareEndpoints(start_endpoint, text_range, end_endpoint) == 0:
                    continue
            except Exception:
                pass
            text = text_range.GetText(-1)
            text = text.strip() if text else ""
            if text:
                selected_parts.append(text)
        return "\n".join(selected_parts) or None
    except Exception:
        return None


def _get_selected_text_clipboard() -> str | None:
    """Fallback: Ctrl+C with save/restore so existing clipboard is preserved."""
    from core.platform_utils import send_keys, COPY_COMBO

    if _IS_MAC:
        from core.platform import macos_native

        return macos_native.get_selected_text(COPY_COMBO)

    previous = _safe_get_clipboard()
    send_keys(COPY_COMBO)
    time.sleep(0.08)
    text = pyperclip.paste().strip()

    # Restore original clipboard content
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass

    return text if text and text != previous else None


def _get_primary_selection_linux() -> str | None:
    """
    Read the X11/Wayland PRIMARY selection (the current highlight) on Linux.

    Highlighting text auto-fills PRIMARY, so this captures the selection without
    synthesising Ctrl+C — works even in terminals and apps that don't map copy,
    and never touches the regular clipboard. Tries the same backends pyperclip
    already relies on; returns None if none are installed or nothing is selected.
    """
    import os
    import subprocess

    xclip = ["xclip", "-selection", "primary", "-o"]
    xsel  = ["xsel", "-p"]
    wl    = ["wl-paste", "--primary", "--no-newline"]
    # Prefer the backend that matches the session type.
    commands = [wl, xclip, xsel] if os.environ.get("WAYLAND_DISPLAY") else [xclip, xsel, wl]

    for cmd in commands:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
        except Exception:
            continue
        if out.returncode == 0:
            text = (out.stdout or "").strip()
            if text:
                return text
    return None


def get_selected_text() -> str | None:
    """
    Returns the currently highlighted text.

    Windows: UIA (no clipboard touch), then Ctrl+C fallback.
    Linux:   PRIMARY selection (no keypress), then Ctrl+C fallback.
    macOS:   Ctrl+C fallback.
    """
    try:
        text = _get_selected_text_uia()
    except Exception:
        _log.exception("Selected-text UIA capture failed.")
        text = None
    if not text and _IS_LINUX:
        try:
            text = _get_primary_selection_linux()
        except Exception:
            _log.exception("Selected-text PRIMARY capture failed.")
            text = None
    if not text:
        try:
            text = _get_selected_text_clipboard()
        except Exception:
            _log.exception("Selected-text clipboard capture failed.")
            text = None
    return text


def get_screen_snippet(region: dict | None = None) -> Image.Image:
    """
    Captures a screenshot of the specified region or the primary monitor.

    Args:
        region: dict with keys top, left, width, height (mss format).
                If None, captures the entire primary monitor.

    Returns:
        PIL Image of the captured region.
    """
    if _IS_MAC:
        from tempfile import gettempdir
        from pathlib import Path
        from core.platform import macos_native

        out_path = Path(gettempdir()) / "wisp_screen_snippet.png"
        if macos_native.capture_screen_to_file(out_path, region=region):
            with Image.open(out_path) as img:
                return img.convert("RGB")
        raise RuntimeError("macOS screen capture failed")

    # Windows/Linux path. mss is imported lazily so macOS does not load or drive
    # the Python CoreGraphics capture backend inside the Qt process.
    def _grab() -> Image.Image:
        """Handle grab for capture."""
        import mss

        mss_factory = getattr(mss, "MSS", mss.mss)
        with mss_factory() as sct:
            monitor = region if region else sct.monitors[1]  # monitors[1] = primary
            raw = sct.grab(monitor)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    return run_on_main(_grab)


def image_to_base64(img: Image.Image) -> str:
    """Encode a PIL Image as a base64 PNG string for LLM vision input."""
    import base64
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def get_clipboard_text() -> str | None:
    """
    Return the current clipboard text as context, without synthesising a copy
    keypress (unlike get_selected_text's fallback path).

    Cross-platform via pyperclip on Windows/Linux; on Linux it needs
    xclip/xsel (X11) or wl-clipboard (Wayland). If none is available pyperclip
    raises, which _safe_get_clipboard swallows — so this degrades to None
    rather than crashing.
    """
    if _IS_MAC:
        from core.platform import macos_native

        text = macos_native.get_clipboard_text()
    else:
        text = _safe_get_clipboard()
    if not text:
        return None
    text = text.strip()
    return text or None


def _safe_get_clipboard() -> str | None:
    """Handle safe get clipboard for capture."""
    try:
        return pyperclip.paste()
    except Exception:
        return None
