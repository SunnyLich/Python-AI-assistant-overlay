from __future__ import annotations

from ui.shared import app_icon


class _FakeApp:
    def __init__(self) -> None:
        self.application_name = ""
        self.display_name = ""
        self.desktop_file_name = ""

    def setApplicationName(self, value: str) -> None:
        self.application_name = value

    def setApplicationDisplayName(self, value: str) -> None:
        self.display_name = value

    def setDesktopFileName(self, value: str) -> None:
        self.desktop_file_name = value


def test_app_icon_path_prefers_native_platform_assets() -> None:
    assert app_icon.app_icon_path("win32").name == "app.ico"
    assert app_icon.app_icon_path("darwin").name == "app.icns"
    assert app_icon.app_icon_path("linux").name == "app.png"


def test_install_app_icon_sets_application_metadata_without_qt(monkeypatch) -> None:
    fake = _FakeApp()
    monkeypatch.setattr(app_icon, "app_icon_path", lambda platform=None: None)

    assert app_icon.install_app_icon(fake, platform="linux") is None

    assert fake.application_name == "Wisp"
    assert fake.display_name == "Wisp"
    assert fake.desktop_file_name == "wisp"


def test_windows_app_user_model_id_is_noop_off_windows() -> None:
    assert app_icon.set_windows_app_user_model_id(platform="linux") is False
