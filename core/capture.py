"""
core/capture.py — Input capture: highlighted text and screen snippets.

Two modes:
  1. Text selection  — reads whatever the user has highlighted via clipboard.
  2. Screen snippet  — takes a screenshot of the active monitor region (Win+Shift+S style).
"""
import time
import threading
import pyperclip
import mss
import mss.tools
from PIL import Image
import io
import win32clipboard
import win32con


def get_selected_text() -> str | None:
    """
    Returns the currently highlighted text by briefly copying it to clipboard.
    Saves and restores the previous clipboard content.
    """
    import keyboard

    # Save previous clipboard
    previous = _safe_get_clipboard()

    # Simulate Ctrl+C to copy selection
    keyboard.send("ctrl+c")
    time.sleep(0.08)  # brief wait for clipboard to populate

    text = pyperclip.paste().strip()

    # Restore previous clipboard content
    if previous is not None:
        pyperclip.copy(previous)

    return text if text else None


def get_screen_snippet(region: dict | None = None) -> Image.Image:
    """
    Captures a screenshot of the specified region or the primary monitor.

    Args:
        region: dict with keys top, left, width, height (mss format).
                If None, captures the entire primary monitor.

    Returns:
        PIL Image of the captured region.
    """
    with mss.mss() as sct:
        monitor = region if region else sct.monitors[1]  # monitors[1] = primary
        raw = sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def image_to_base64(img: Image.Image) -> str:
    """Encode a PIL Image as a base64 PNG string for LLM vision input."""
    import base64
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _safe_get_clipboard() -> str | None:
    try:
        return pyperclip.paste()
    except Exception:
        return None
