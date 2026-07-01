from __future__ import annotations

import plistlib

from core.system import autostart


def test_linux_start_on_login_writes_xdg_desktop_file(tmp_path, monkeypatch):
    """Linux autostart uses the per-user XDG autostart folder."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(autostart, "_command", lambda: ["python", "-m", "runtime.supervisor.app"])

    autostart.sync_start_on_login(True, platform="linux", home=tmp_path)

    path = tmp_path / ".config" / "autostart" / autostart.LINUX_DESKTOP_ID
    text = path.read_text(encoding="utf-8")
    assert "Name=Wisp" in text
    assert 'Exec="python" "-m" "runtime.supervisor.app"' in text
    assert "Icon=" in text
    assert "app.png" in text
    assert "StartupWMClass=Wisp" in text
    assert "Terminal=false" in text

    autostart.sync_start_on_login(False, platform="linux", home=tmp_path)

    assert not path.exists()


def test_macos_start_on_login_writes_launch_agent(tmp_path, monkeypatch):
    """macOS autostart uses a per-user LaunchAgent plist."""
    monkeypatch.setattr(autostart, "_command", lambda: ["python", "-m", "runtime.supervisor.app"])

    autostart.sync_start_on_login(True, platform="darwin", home=tmp_path)

    path = tmp_path / "Library" / "LaunchAgents" / f"{autostart.MACOS_LAUNCH_AGENT_ID}.plist"
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == autostart.MACOS_LAUNCH_AGENT_ID
    assert data["ProgramArguments"] == ["python", "-m", "runtime.supervisor.app"]
    assert data["RunAtLoad"] is True

    autostart.sync_start_on_login(False, platform="darwin", home=tmp_path)

    assert not path.exists()
