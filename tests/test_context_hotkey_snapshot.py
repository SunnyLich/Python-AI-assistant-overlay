from types import SimpleNamespace

import pytest

from core import context_fetcher


def test_fetch_and_save_uses_hotkey_time_hwnd(monkeypatch):
    calls: list[tuple[str, int | None]] = []
    hotkey_window = context_fetcher.WindowInfo(
        title="Example",
        process_name="chrome.exe",
        url="https://example.test/page",
        hwnd=777,
    )

    monkeypatch.setattr(context_fetcher, "_IS_WIN", True)
    monkeypatch.setattr(context_fetcher, "start_fs_watcher", lambda: None)
    monkeypatch.setattr(context_fetcher, "_fs_observer", object())
    monkeypatch.setattr(
        context_fetcher,
        "_fetch_window_info_win",
        lambda hwnd: calls.append(("hwnd", hwnd)) or hotkey_window,
    )
    monkeypatch.setattr(
        context_fetcher,
        "_fetch_active_window",
        lambda: calls.append(("foreground", None)) or context_fetcher.WindowInfo(title="Overlay"),
    )
    monkeypatch.setattr(context_fetcher, "_fetch_clipboard", context_fetcher.ClipboardInfo)
    monkeypatch.setattr(context_fetcher, "_fetch_ui_focused", context_fetcher.UIElementInfo)
    monkeypatch.setattr(context_fetcher, "_fetch_recent_files", lambda: [])
    monkeypatch.setattr(context_fetcher, "_get_fs_events", lambda: [])
    monkeypatch.setattr(context_fetcher, "_persist", lambda snapshot: None)

    snapshot = context_fetcher.fetch_and_save(active_hwnd=777)

    assert snapshot.active_window is hotkey_window
    assert calls == [("hwnd", 777)]


def test_browser_context_text_reads_page_by_saved_hwnd(monkeypatch):
    pytest.importorskip("PySide6")
    import main

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        main.context_fetcher,
        "fetch_browser_content_for_window",
        lambda url, hwnd: calls.append((url, hwnd)) or "Rendered page text",
    )
    snapshot = SimpleNamespace(
        active_window=SimpleNamespace(url="https://example.test/page", hwnd=777),
        browser_content="",
    )

    text = main.App._browser_context_text(snapshot)

    assert calls == [("https://example.test/page", 777)]
    assert "URL: https://example.test/page" in text
    assert "Rendered page text" in text


def test_context_target_replaces_wisp_foreground_with_external_window(monkeypatch):
    pytest.importorskip("PySide6")
    import main

    monkeypatch.setattr(main, "_IS_WIN", True)
    monkeypatch.setattr(main.os, "getpid", lambda: 123)
    monkeypatch.setattr(main.App, "_window_pid_win", staticmethod(lambda hwnd: 123 if hwnd == 111 else 456))
    monkeypatch.setattr(main.App, "_window_title_win", staticmethod(lambda hwnd: "Wisp" if hwnd == 111 else "Chrome"))
    monkeypatch.setattr(main.App, "_find_external_context_window_win", staticmethod(lambda _hwnd: 777))

    app = main.App.__new__(main.App)

    assert app._context_target_hwnd(111) == 777
