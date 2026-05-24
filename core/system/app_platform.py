"""Small platform-specific app integration helpers."""
from __future__ import annotations

import os


def configure_windows_app_identity(app_id: str = "Sunny.Wisp") -> None:
    """Set the Windows AppUserModelID so the app groups predictably in the shell."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass
