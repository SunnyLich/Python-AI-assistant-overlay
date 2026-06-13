from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def linux_context_fetcher():
    import core.context_fetcher as context_fetcher

    platform_patch = patch.object(sys, "platform", "linux")
    platform_patch.start()
    module = importlib.reload(context_fetcher)
    try:
        yield module
    finally:
        platform_patch.stop()
        importlib.reload(context_fetcher)


def test_kwrite_window_resolves_open_file_by_pid(linux_context_fetcher, monkeypatch):
    cf = linux_context_fetcher
    path = "/home/alvinli/Documents/Summary.txt"
    win = cf.WindowInfo(
        title="Summary.txt \u2014 KWrite",
        process_name="Summary.txt \u2014 KWrite",
        pid=1651464,
    )

    monkeypatch.setattr(cf, "_linux_open_files_for_pid", lambda _pid: [path])
    monkeypatch.setattr(cf, "_fetch_recent_files", lambda _max_files=10: [])

    assert cf._extract_doc_name_from_window(win) == "Summary.txt"
    assert cf._resolve_doc_path(win) == path


def test_kate_process_title_resolves_open_file_by_pid(linux_context_fetcher, monkeypatch):
    cf = linux_context_fetcher
    path = "/home/alvinli/Documents/Notes.md"
    win = cf.WindowInfo(title="Notes.md", process_name="kate", pid=202)

    monkeypatch.setattr(cf, "_linux_open_files_for_pid", lambda _pid: [path])
    monkeypatch.setattr(cf, "_fetch_recent_files", lambda _max_files=10: [])

    assert cf._extract_doc_name_from_window(win) == "Notes.md"
    assert cf._resolve_doc_path(win) == path
