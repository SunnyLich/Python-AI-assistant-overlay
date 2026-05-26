"""
core/capture.py — Input capture: highlighted text and screen snippets.

Two modes:
  1. Text selection  — reads whatever the user has highlighted.
                       Tries Windows UI Automation first (no clipboard touch),
                       falls back to clipboard save/restore if UIA fails.
  2. Screen snippet  — takes a screenshot of the active monitor region.
"""
import time
import pyperclip
import mss
import mss.tools
from PIL import Image
import io


# ------------------------------------------------------------------
# UIA singleton — initialised once, reused across calls
# ------------------------------------------------------------------
_uia = None
_UIA_TextPatternId = 10014


def _get_uia():
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
        text = selections.GetElement(0).GetText(-1)
        return text.strip() if text else None
    except Exception:
        return None


def _get_selected_text_clipboard() -> str | None:
    """Fallback: Ctrl+C with save/restore so existing clipboard is preserved."""
    from core.platform_utils import send_keys

    previous = _safe_get_clipboard()
    send_keys("ctrl+c")
    time.sleep(0.08)
    text = pyperclip.paste().strip()

    # Restore original clipboard content
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass

    return text if text and text != previous else None


def get_selected_text() -> str | None:
    """
    Returns the currently highlighted text.
    Tries UIA first; falls back to clipboard copy-restore.
    """
    text = _get_selected_text_uia()
    if not text:
        text = _get_selected_text_clipboard()
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
