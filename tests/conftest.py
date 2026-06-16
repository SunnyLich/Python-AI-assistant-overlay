"""Shared pytest process setup."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("PYTHONFAULTHANDLER", "1")


def _suppress_windows_crash_dialogs() -> None:
    """Keep native child-process crashes from blocking pytest with a modal box."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetErrorMode.restype = ctypes.c_uint
        current = int(kernel32.GetErrorMode())
        sem_failcriticalerrors = 0x0001
        sem_nogpfaultbox = 0x0002
        sem_noopenfileerrorbox = 0x8000
        kernel32.SetErrorMode(
            current
            | sem_failcriticalerrors
            | sem_nogpfaultbox
            | sem_noopenfileerrorbox
        )
    except Exception:
        pass


_suppress_windows_crash_dialogs()
