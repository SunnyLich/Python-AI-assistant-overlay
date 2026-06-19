"""Regression tests for the bundled hi Notepad addon."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = ROOT / "addons" / "hi_notepad_test" / "__init__.py"


def _load_addon():
    spec = importlib.util.spec_from_file_location("hi_notepad_test_addon", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hi_addon_opens_notepad_file_without_rewriting_prompt(monkeypatch, tmp_path):
    """Verify standalone hi opens Notepad content but leaves the model prompt alone."""
    addon = _load_addon()
    popen_calls: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        popen_calls.append(list(args))
        return object()

    monkeypatch.setattr(addon.sys, "platform", "win32")
    monkeypatch.setattr(addon.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(addon.subprocess, "Popen", fake_popen)

    prompt, context = addon.before_query("well hi there", "ctx")

    assert (prompt, context) == ("well hi there", "ctx")
    assert popen_calls == [["notepad.exe", str(tmp_path / "wisp-hi-from-wisp.txt")]]
    assert (tmp_path / "wisp-hi-from-wisp.txt").read_text(encoding="utf-8") == "hi from wisp"


def test_hi_addon_ignores_non_standalone_hi(monkeypatch):
    """Verify words containing hi do not trigger Notepad."""
    addon = _load_addon()

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("Notepad should not launch")

    monkeypatch.setattr(addon.sys, "platform", "win32")
    monkeypatch.setattr(addon.subprocess, "Popen", fail_popen)

    assert addon.before_query("this should not match", "ctx") == ("this should not match", "ctx")


def test_hi_addon_swallows_notepad_launch_errors(monkeypatch, tmp_path):
    """Verify Notepad failures do not block the query."""
    addon = _load_addon()

    monkeypatch.setattr(addon.sys, "platform", "win32")
    monkeypatch.setattr(addon.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        addon.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")),
    )

    assert addon.before_query("hi", "ctx") == ("hi", "ctx")
