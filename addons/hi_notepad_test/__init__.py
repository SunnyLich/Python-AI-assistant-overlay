"""Test addon: open Notepad and make Wisp greet on standalone 'hi'."""
from __future__ import annotations

import re
import subprocess
import sys

_HI_WORD = re.compile(r"(?<![A-Za-z0-9_])hi(?![A-Za-z0-9_])", re.IGNORECASE)


def before_query(prompt: str, context: str) -> tuple[str, str]:
    """React only when 'hi' appears as its own word."""
    if not _HI_WORD.search(prompt or ""):
        return prompt, context

    _open_notepad()
    return (
        "Reply exactly with this text and nothing else: hi from wisp",
        context,
    )


def _open_notepad() -> None:
    """Launch Notepad on Windows without blocking Wisp."""
    if sys.platform != "win32":
        return
    try:
        subprocess.Popen(  # noqa: S603 - intentional local test addon action.
            ["notepad.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass
