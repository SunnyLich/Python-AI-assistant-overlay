"""Tests for macos py test native context."""

from __future__ import annotations

import pytest

from core import context_fetcher
from runtime.workers import native_host


def test_win_context_window_skips_wisp_foreground(monkeypatch):
    """Verify win context window skips wisp foreground behavior."""
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "_win_is_external_context_window", lambda hwnd: hwnd == 777)
    monkeypatch.setattr(native_host, "_win_find_external_context_window", lambda _hwnd: 777)
    monkeypatch.setattr(native_host, "_win_window_title", lambda hwnd: "Wisp" if hwnd == 111 else "Chrome")

    assert native_host._win_context_window_id(111) == 777


def test_context_snapshot_reads_browser_url_from_corrected_window(monkeypatch):
    """Verify context snapshot reads browser url from corrected window behavior."""
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", True)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Chrome", "pid": 42, "window_id": 777, "bundle_id": ""},
    )
    monkeypatch.setattr(native_host, "selected_text", lambda: "")
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    calls: list[int] = []

    def fake_fetch_window(hwnd: int):
        """Verify fake fetch window behavior."""
        calls.append(hwnd)
        return context_fetcher.WindowInfo(
            title="Example",
            process_name="chrome.exe",
            url="https://example.test/page",
            hwnd=hwnd,
        )

    monkeypatch.setattr(context_fetcher, "_fetch_window_info_win", fake_fetch_window)

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert calls == [777]
    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["browser_hwnd"] == 777


def test_context_snapshot_reads_background_browser_when_foreground_is_document(monkeypatch):
    """Verify context snapshot reads background browser when foreground is document behavior."""
    monkeypatch.setattr(native_host, "IS_WIN", True)
    monkeypatch.setattr(native_host, "IS_MAC", False)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", True)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", False)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "Untitled 1 \u2014 LibreOffice Calc", "pid": 42, "window_id": 111, "bundle_id": ""},
    )
    monkeypatch.setattr(native_host, "selected_text", lambda: "")
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    background_browser = context_fetcher.WindowInfo(
        title="Example - Chrome",
        process_name="chrome.exe",
        url="https://example.test/page",
        hwnd=777,
    )
    monkeypatch.setattr(
        context_fetcher,
        "get_browser_window_for_context",
        lambda preferred_hwnd=0: background_browser,
    )

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["browser_hwnd"] == 777
    assert snapshot["debug"]["browser_window"]["process_name"] == "chrome.exe"


def test_macos_context_snapshot_separates_document_and_browser(monkeypatch):
    """Verify macOS snapshots collect front document and browser independently."""
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", True)
    monkeypatch.setattr(context_fetcher, "_IS_WIN", False)
    monkeypatch.setattr(context_fetcher, "_IS_MAC", True)
    monkeypatch.setattr(
        native_host,
        "_active_app",
        lambda: {"name": "TextEdit", "pid": 101, "bundle_id": "com.apple.TextEdit"},
    )
    monkeypatch.setattr(native_host, "selected_text", lambda: "")
    monkeypatch.setattr(native_host, "clipboard_get", lambda: {"text": ""})

    rows = [
        {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt"},
        {"process_name": "Safari", "pid": 303, "frontmost": False, "title": "Example Page"},
    ]
    monkeypatch.setattr("core.platform.macos_native.list_document_windows", lambda: rows)
    monkeypatch.setattr(context_fetcher, "_mac_browser_url", lambda _app: "https://example.test/page")

    snapshot = native_host.context_snapshot(
        include_clipboard=False,
        include_selection=False,
        include_browser_url=True,
    )

    assert snapshot["document_window"] == {
        "title": "Notes.txt",
        "process_name": "TextEdit",
        "pid": 101,
        "window_id": 0,
    }
    assert snapshot["browser_app"] == "Safari"
    assert snapshot["browser_url"] == "https://example.test/page"
    assert snapshot["debug"]["browser_window"]["process_name"] == "Safari"


def test_linux_active_app_includes_real_process_name(monkeypatch):
    """Verify linux active app includes real process name behavior."""
    monkeypatch.setattr(native_host, "IS_WIN", False)
    monkeypatch.setattr(native_host, "IS_MAC", False)

    import core.platform_utils as platform_utils

    monkeypatch.setattr(platform_utils, "get_foreground_window", lambda: 144703501)
    monkeypatch.setattr(platform_utils, "get_window_title", lambda _wid: "Summary.txt \u2014 KWrite")
    monkeypatch.setattr(platform_utils, "get_window_pid", lambda _wid: 1651464)

    class FakeProcess:
        """Test case for fake process behavior."""
        def __init__(self, pid):
            """Initialize the fake process instance."""
            self.pid = pid

        def name(self):
            """Verify name behavior."""
            return "kwrite"

    psutil = pytest.importorskip("psutil")

    monkeypatch.setattr(psutil, "Process", FakeProcess)

    active = native_host._active_app()

    assert active["name"] == "Summary.txt \u2014 KWrite"
    assert active["process_name"] == "kwrite"
    assert active["pid"] == 1651464
    assert native_host._last_context_window_debug["raw_process"] == "kwrite"
