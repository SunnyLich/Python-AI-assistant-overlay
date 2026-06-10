"""Unit tests for the off-console CGEventTap hotkey matching logic.

These cover the pure parse/match helpers (no Quartz, no macOS required), which
are what decide whether a keystroke counts as a configured hotkey.
"""
from __future__ import annotations

from types import SimpleNamespace

from macos_py.workers import hotkey_helper

# Minimal keycode map (subset of core.hotkeys.MACOS_VIRTUAL_KEYCODES).
VK = {"q": 12, "a": 0, "1": 18, "f1": 122}

SHIFT = 0x00020000
CTRL = 0x00040000
ALT = 0x00080000
CMD = 0x00100000
FN = 0x00800000  # rides along on F-keys; must be ignored when matching


def test_parse_combo_rejects_bare_key():
    # A bare printable key would steal ordinary typing -> rejected.
    assert hotkey_helper._parse_combo_to_tap("q", VK) is None


def test_parse_combo_ctrl_q():
    assert hotkey_helper._parse_combo_to_tap("ctrl+q", VK) == (12, CTRL)


def test_parse_combo_cmd_shift_1():
    assert hotkey_helper._parse_combo_to_tap("cmd+shift+1", VK) == (18, CMD | SHIFT)


def test_match_exact_modifier():
    table = hotkey_helper._build_tap_table([("ctrl+q", "caller", {"index": 0})], VK)
    # ctrl+q pressed -> match.
    assert hotkey_helper._match_tap_event(12, CTRL, table) == ("caller", {"index": 0})
    # ctrl+shift+q pressed -> NOT a match (extra modifier).
    assert hotkey_helper._match_tap_event(12, CTRL | SHIFT, table) is None
    # q alone -> no match.
    assert hotkey_helper._match_tap_event(12, 0, table) is None
    # different key with ctrl -> no match.
    assert hotkey_helper._match_tap_event(0, CTRL, table) is None


def test_match_ignores_fn_and_capslock_bits():
    table = hotkey_helper._build_tap_table([("shift+f1", "snip", {})], VK)
    # Real F-key events carry the Fn bit; matching must ignore it.
    assert hotkey_helper._match_tap_event(122, SHIFT | FN, table) == ("snip", {})


def test_build_table_skips_unparseable():
    specs = [("ctrl+q", "caller", {"index": 0}), ("", "snip", {}), ("boguskey", "x", {})]
    table = hotkey_helper._build_tap_table(specs, VK)
    assert table == [(12, CTRL, "caller", {"index": 0})]


def test_specs_from_config():
    config = SimpleNamespace(
        CALLER_ROWS=[{"hotkey": "ctrl+q"}, {"hotkey": ""}, {"hotkey": "cmd+shift+1"}],
        HOTKEY_ADD_CONTEXT="ctrl+a",
        HOTKEY_CLEAR_CONTEXT="",
        HOTKEY_SNIP="cmd+shift+s",
    )
    specs = hotkey_helper._hotkey_specs_from_config(config)
    assert ("ctrl+q", "caller", {"index": 0}) in specs
    assert ("cmd+shift+1", "caller", {"index": 2}) in specs
    assert ("ctrl+a", "add_context", {}) in specs
    assert ("cmd+shift+s", "snip", {}) in specs
    # Empty caller hotkey and empty HOTKEY_CLEAR_CONTEXT are skipped.
    assert all(combo for combo, _, _ in specs)
    assert all(kind != "clear_context" for _, kind, _ in specs)
