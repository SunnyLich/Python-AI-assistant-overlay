"""Tests for process-hosted addons and the plugin compatibility facade."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import config
import core.addon_manager as am
import core.addon_store as addon_store
import core.plugin_manager as pm
from core.tool_registry import ToolRegistry


_ADDON_SRC = """
import os
from core.plugin_manager import plugin_setting

def before_query(prompt, context):
    return prompt + "!" + str(os.getpid()), context + "|addon"

def after_response(text):
    pass

def get_tools():
    return [{
        "name": "demo_tool",
        "description": "demo",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "executor": lambda inputs: "ok:" + str(os.getpid()),
    }]

def get_tray_actions():
    return [{"label": "Act", "callback": lambda: None}]

def get_settings():
    return [{"key": "greeting", "label": "Greeting", "type": "text", "default": "hi"}]
"""


def _make_manager(tmp_path: Path, monkeypatch) -> tuple[am.AddonManager, Path]:
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "demo"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "demo"
            name = "demo"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            response = "read"
            tools = true
            ui = ["tray", "settings"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(textwrap.dedent(_ADDON_SRC).strip(), encoding="utf-8")

    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)

    manager = am.AddonManager(addons_dir)
    manager.load_all()
    return manager, store_path


def test_addon_hooks_and_tools_run_in_host_process(tmp_path, monkeypatch):
    manager, _store_path = _make_manager(tmp_path, monkeypatch)
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    host_pid = manager.before_query("hi", "")[0].removeprefix("hi!")
    assert host_pid and int(host_pid) != os.getpid()
    assert manager.before_query("hi", "")[1] == "|addon"
    assert manager.get_tray_actions()[0]["label"] == "Act"
    assert "demo_tool" in {s["name"] for s in registry.schemas()}

    tool_result = registry.execute("demo_tool", {})
    assert tool_result.startswith("ok:")
    assert int(tool_result.removeprefix("ok:")) != os.getpid()

    manager.on_shutdown()


def test_addon_enable_and_settings_round_trip(tmp_path, monkeypatch):
    manager, store_path = _make_manager(tmp_path, monkeypatch)
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    settings = manager.get_settings("demo")
    assert settings == [
        {"key": "greeting", "label": "Greeting", "type": "text", "default": "hi", "value": "hi"}
    ]
    assert pm.plugin_setting("demo", "greeting", "fallback") == "fallback"

    manager.set_setting("demo", "greeting", "hello")
    assert pm.plugin_setting("demo", "greeting") == "hello"
    assert manager.get_settings("demo")[0]["value"] == "hello"
    assert "hello" in store_path.read_text(encoding="utf-8")

    manager.set_enabled("demo", False)
    assert not manager.is_enabled("demo")
    assert "demo_tool" not in {s["name"] for s in registry.schemas()}

    manager.set_enabled("demo", True)
    assert manager.is_enabled("demo")
    assert "demo_tool" in {s["name"] for s in registry.schemas()}
    manager.on_shutdown()


def test_missing_permissions_deny_surfaces(tmp_path, monkeypatch):
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "locked"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        "[addon]\nid = 'locked'\nname = 'locked'\nentry = '__init__.py'\n",
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(textwrap.dedent(_ADDON_SRC).strip(), encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    assert manager.before_query("hi", "") == ("hi", "")
    assert manager.get_tray_actions() == []
    assert manager.get_settings("locked") == []
    assert registry.schemas(include_server_tools=False) == []
    manager.on_shutdown()
