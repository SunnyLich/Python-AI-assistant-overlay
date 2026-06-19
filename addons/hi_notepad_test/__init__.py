"""Test addon: open Notepad with a Wisp greeting on standalone 'hi'."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tempfile

_HI_WORD = re.compile(r"(?<![A-Za-z0-9_])hi(?![A-Za-z0-9_])", re.IGNORECASE)
_GREETING = "hi from wisp"


def before_query(prompt: str, context: str) -> tuple[str, str]:
    """React only when 'hi' appears as its own word."""
    if not _HI_WORD.search(prompt or ""):
        return prompt, context

    _open_notepad_with_text(_GREETING)
    return prompt, context


def _open_notepad_with_text(text: str) -> None:
    """Launch Notepad on Windows with *text* in the opened document."""
    if sys.platform != "win32":
        return
    try:
        note_path = Path(tempfile.gettempdir()) / "wisp-hi-from-wisp.txt"
        note_path.write_text(text, encoding="utf-8")
        subprocess.Popen(  # noqa: S603 - intentional local test addon action.
            ["notepad.exe", str(note_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass
