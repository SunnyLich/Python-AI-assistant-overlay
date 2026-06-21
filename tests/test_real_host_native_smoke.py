"""Opt-in tests for real OS integration that cannot be proven with fakes.

These tests intentionally touch the host desktop. They are skipped by default
and enabled through scripts/run_app_workflow_tests.py --real-host.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.real_host,
    pytest.mark.skipif(
        os.environ.get("WISP_RUN_REAL_HOST_TESTS") != "1",
        reason="set WISP_RUN_REAL_HOST_TESTS=1 or use --real-host",
    ),
]


def _qapp_real_display():
    pytest.importorskip("PySide6", reason="PySide6 not installed")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["wisp-real-host-tests"])
    platform_name = app.platformName().lower()
    if platform_name == "offscreen":
        pytest.fail("real-host tests require a real Qt platform, not QT_QPA_PLATFORM=offscreen")
    return app


def _pump_until(app, predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    app.processEvents()
    assert predicate()


def test_real_host_macos_permission_snapshot_is_ready_for_native_checks():
    """macOS privacy grants are visible before tests depend on native APIs."""
    if sys.platform != "darwin":
        pytest.skip("macOS-only permission preflight")

    from runtime.workers import native_host

    snapshot = native_host.permissions_snapshot()
    missing: list[str] = []
    screen = snapshot.get("screen_recording")
    if screen is False:
        missing.append("Screen Recording")
    if os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") == "1":
        accessibility = snapshot.get("accessibility")
        if accessibility is False:
            missing.append("Accessibility")
    microphone = snapshot.get("microphone")
    if microphone == "denied":
        missing.append("Microphone")

    if missing:
        pytest.fail(
            "macOS permission preflight failed for the process running pytest "
            f"({sys.executable}). Missing: {', '.join(missing)}. "
            "Open System Settings > Privacy & Security and grant the permission "
            "to the launcher you used for this test, such as Terminal, Codex, or Python. "
            "A packaged Wisp.app has its own separate macOS permission entries."
        )


def test_real_host_clipboard_and_context_snapshot_roundtrip():
    """The real clipboard path feeds the same native context snapshot users send."""
    from runtime.workers import native_host

    original = native_host.clipboard_get().get("text", "")
    marker = f"wisp-real-host-clipboard-{uuid.uuid4()}"
    try:
        assert native_host.clipboard_set(marker)["ok"] is True
        time.sleep(0.1)
        assert native_host.clipboard_get()["text"] == marker

        snapshot = native_host.context_snapshot(
            include_clipboard=True,
            include_selection=False,
            include_browser_content=False,
            include_browser_url=False,
        )
        assert snapshot["platform"]
        assert snapshot["clipboard_text"] == marker
        assert isinstance(snapshot["active_app"], dict)
        assert set(snapshot["screen_size"]) == {"width", "height"}
    finally:
        native_host.clipboard_set(original)


def test_real_host_screenshot_capture_returns_pixels():
    """The actual screen-capture backend can capture non-empty pixels."""
    from core.capture import get_screen_snippet, image_to_base64

    try:
        image = get_screen_snippet()
    except Exception as exc:
        if sys.platform == "darwin":
            hint = (
                "On macOS, grant Screen Recording to the launcher running pytest "
                "(Terminal, Codex, or Python). A packaged Wisp.app has separate "
                "permission entries."
            )
        elif sys.platform == "win32":
            hint = (
                "On Windows, this usually means the test process cannot capture "
                "the current desktop session, such as a locked, secure, remote, "
                "or protected desktop."
            )
        else:
            hint = "Make sure the test is running in a real graphical desktop session."
        pytest.fail(f"real screen capture failed: {exc!r}. {hint}")
    assert image.width > 0
    assert image.height > 0
    assert image_to_base64(image)


def test_real_host_qt_screen_and_tray_capability():
    """Qt can attach to the real desktop, grab pixels, and expose tray support."""
    from PySide6.QtWidgets import QSystemTrayIcon

    app = _qapp_real_display()
    screen = app.primaryScreen()
    assert screen is not None
    capture = screen.grabWindow(0, 0, 0, 64, 64)
    assert not capture.isNull()
    assert capture.width() > 0 and capture.height() > 0

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    if not tray_available and os.environ.get("WISP_REAL_HOST_ALLOW_NO_TRAY") != "1":
        pytest.fail("system tray is not available; set WISP_REAL_HOST_ALLOW_NO_TRAY=1 to accept this host limitation")


@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") != "1",
    reason="use --real-host-interactive to synthesize paste into a focused test window",
)
def test_real_host_interactive_paste_shortcut_targets_focused_qt_field():
    """Paste-back input injection reaches the focused test field and preserves clipboard."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTextEdit

    from core.platform_utils import PASTE_COMBO, send_keys
    from runtime.workers import native_host

    app = _qapp_real_display()
    original = native_host.clipboard_get().get("text", "")
    marker = f"wisp-real-host-paste-{uuid.uuid4()}"
    edit = QTextEdit()
    edit.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    edit.resize(420, 180)
    try:
        assert native_host.clipboard_set(marker)["ok"] is True
        edit.show()
        edit.raise_()
        edit.activateWindow()
        edit.setFocus()
        _pump_until(app, edit.hasFocus, timeout=3.0)

        send_keys(PASTE_COMBO)
        _pump_until(app, lambda: marker in edit.toPlainText(), timeout=3.0)
    finally:
        native_host.clipboard_set(original)
        edit.close()
        edit.deleteLater()
        app.processEvents()


@pytest.mark.skipif(
    os.environ.get("WISP_RUN_REAL_HOST_INTERACTIVE_TESTS") != "1",
    reason="use --real-host-interactive to register and unregister real global hotkeys",
)
def test_real_host_interactive_hotkey_backend_starts_and_stops():
    """The real hotkey backend can register the configured keys and cleanly stop."""
    from runtime.workers import native_host

    started = native_host.hotkeys_start(addon_hotkeys=[])
    try:
        assert started.get("ok") is True
    finally:
        stopped = native_host.hotkeys_stop()
        assert stopped.get("ok") is True
