"""Application icon and desktop identity helpers."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from core.system.paths import ASSETS_DIR

APP_ID = "app.wisp.desktop"
LINUX_DESKTOP_FILE_NAME = "wisp"
APP_NAME = "Wisp"

log = logging.getLogger("wisp.app_icon")


def _asset_path(name: str) -> Path | None:
    """Return an existing icon asset path from the bundled assets directory."""
    path = ASSETS_DIR / name
    return path if path.exists() else None


def app_icon_path(platform: str | None = None) -> Path | None:
    """Return the best icon file for the current platform."""
    platform = platform or sys.platform
    if platform == "win32":
        preferred = ("app.ico", "app.png")
    elif platform == "darwin":
        preferred = ("app.icns", "app.png", "app.ico")
    else:
        preferred = ("app.png", "app.ico")
    for name in preferred:
        if path := _asset_path(name):
            return path
    return None


def set_windows_app_user_model_id(app_id: str = APP_ID, platform: str | None = None) -> bool:
    """Set the Windows taskbar identity for the current process."""
    if (platform or sys.platform) != "win32":
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        log.exception("Failed to set Windows AppUserModelID")
        return False


def install_app_icon(app: Any, platform: str | None = None) -> Path | None:
    """Apply Wisp's app metadata and icon to a Qt application object."""
    platform = platform or sys.platform
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if platform.startswith("linux") and hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName(LINUX_DESKTOP_FILE_NAME)

    icon_path = app_icon_path(platform)
    if icon_path is None:
        return None

    try:
        from PySide6.QtGui import QIcon

        icon = QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:
        log.exception("Failed to apply Wisp application icon")
    return icon_path
