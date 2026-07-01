"""User-level launch-at-login integration."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from core.system.paths import ASSETS_DIR, REPO_ROOT

APP_NAME = "Wisp"
MACOS_LAUNCH_AGENT_ID = "com.wisp.launcher"
LINUX_DESKTOP_ID = "wisp.desktop"


def _is_frozen() -> bool:
    """Return whether Wisp is running from a packaged executable."""
    return bool(getattr(sys, "frozen", False))


def _source_command() -> list[str]:
    """Return the command that can relaunch this Wisp checkout."""
    return [sys.executable, "-m", "runtime.supervisor.app"]


def _command() -> list[str]:
    """Return the command that should be launched at login."""
    if _is_frozen():
        return [sys.executable]
    return _source_command()


def _powershell_quote(value: str) -> str:
    """Quote a string for a PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _windows_run_command() -> str:
    """Return the Windows Run-key command line."""
    if _is_frozen():
        return subprocess.list2cmdline(_command())
    script = (
        f"Set-Location -LiteralPath {_powershell_quote(str(REPO_ROOT))}; "
        f"& {_powershell_quote(sys.executable)} -m runtime.supervisor.app"
    )
    return subprocess.list2cmdline([
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-Command",
        script,
    ])


def _desktop_quote(value: str) -> str:
    """Quote one Exec argument for a freedesktop desktop file."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _linux_desktop_text() -> str:
    """Return a freedesktop autostart entry."""
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Start Wisp when you sign in",
        "Exec=" + " ".join(_desktop_quote(part) for part in _command()),
        f"Icon={ASSETS_DIR / 'app.png'}",
        "StartupWMClass=Wisp",
        f"Path={REPO_ROOT}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


def _macos_plist() -> dict:
    """Return a LaunchAgent plist for Wisp."""
    return {
        "Label": MACOS_LAUNCH_AGENT_ID,
        "ProgramArguments": _command(),
        "WorkingDirectory": str(REPO_ROOT),
        "RunAtLoad": True,
    }


def sync_start_on_login(enabled: bool, *, platform: str | None = None, home: Path | None = None) -> None:
    """Create or remove the user-level startup entry for the current platform."""
    platform = platform or sys.platform
    home = home or Path.home()
    if platform == "win32":
        _sync_windows_start_on_login(enabled)
    elif platform == "darwin":
        _sync_macos_start_on_login(enabled, home)
    else:
        _sync_linux_start_on_login(enabled, home)


def _sync_windows_start_on_login(enabled: bool) -> None:
    """Create or remove Wisp's HKCU Run entry."""
    import winreg

    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _windows_run_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def _sync_macos_start_on_login(enabled: bool, home: Path) -> None:
    """Create or remove Wisp's LaunchAgent entry."""
    path = home / "Library" / "LaunchAgents" / f"{MACOS_LAUNCH_AGENT_ID}.plist"
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(_macos_plist(), sort_keys=True))
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _sync_linux_start_on_login(enabled: bool, home: Path) -> None:
    """Create or remove Wisp's XDG autostart desktop file."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    path = base / "autostart" / LINUX_DESKTOP_ID
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_linux_desktop_text(), encoding="utf-8")
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
