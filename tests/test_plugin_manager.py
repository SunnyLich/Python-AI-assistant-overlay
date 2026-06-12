"""Tests for per-mod enable/disable and settings in core.plugin_manager."""
from __future__ import annotations

from pathlib import Path

import config
import core.plugin_manager as pm
from core.system.env_utils import read_env_file
from core.tool_registry import ToolRegistry


_MOD_SRC = """
def before_query(prompt, context):
    return prompt + "!", context

def get_tools():
    return [{
        "name": "demo_tool",
        "description": "demo",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "executor": lambda inputs: "ok",
    }]

def get_tray_actions():
    return [{"label": "Act", "callback": lambda: None}]

def get_settings():
    return [{"key": "greeting", "label": "Greeting", "type": "text", "default": "hi"}]
"""


def _make_manager(tmp_path: Path, monkeypatch) -> tuple[pm.PluginManager, Path]:
    plugins_dir = tmp_path / "plugins"
    mod_dir = plugins_dir / "demo"
    mod_dir.mkdir(parents=True)
    (mod_dir / "__init__.py").write_text(_MOD_SRC, encoding="utf-8")

    env_path = tmp_path / ".env"
    monkeypatch.setattr(pm, "_ENV_PATH", env_path)

    manager = pm.PluginManager(plugins_dir)
    manager.load_all()
    return manager, env_path


def test_disabled_mod_skips_hooks_and_tools(tmp_path, monkeypatch):
    manager, env_path = _make_manager(tmp_path, monkeypatch)
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))

    manager.on_startup(pm.AppContext(signals=None, model_tool_registry=registry, config=config))

    # Enabled by default: hook fires, tool registered, tray action present.
    assert manager.is_enabled("demo")
    assert manager.before_query("hi", "")[0] == "hi!"
    assert "demo_tool" in {s["name"] for s in registry.schemas()}
    assert [a["label"] for a in manager.get_tray_actions()] == ["Act"]

    # Disable: persisted to .env, hook no-ops, tool unregistered, no tray action.
    manager.set_enabled("demo", False)
    assert read_env_file(env_path).get("PLUGIN_DEMO_ENABLED") == "false"
    assert manager.before_query("hi", "")[0] == "hi"
    assert "demo_tool" not in {s["name"] for s in registry.schemas()}
    assert manager.get_tray_actions() == []

    # Re-enable: tool comes back.
    manager.set_enabled("demo", True)
    assert read_env_file(env_path).get("PLUGIN_DEMO_ENABLED") == "true"
    assert "demo_tool" in {s["name"] for s in registry.schemas()}


def test_enabled_flag_read_from_env_on_load(tmp_path, monkeypatch):
    manager, env_path = _make_manager(tmp_path, monkeypatch)
    manager.set_enabled("demo", False)

    # A fresh manager over the same dir/.env should load the mod as disabled.
    reloaded = pm.PluginManager(manager._dir)
    reloaded.load_all()
    assert reloaded.is_enabled("demo") is False


def test_settings_round_trip(tmp_path, monkeypatch):
    manager, env_path = _make_manager(tmp_path, monkeypatch)

    settings = manager.get_settings("demo")
    assert settings == [
        {"key": "greeting", "label": "Greeting", "type": "text",
         "default": "hi", "value": "hi"}
    ]
    assert pm.plugin_setting("demo", "greeting", "fallback") == "fallback"

    manager.set_setting("demo", "greeting", "hello")
    assert pm.plugin_setting("demo", "greeting") == "hello"
    assert manager.get_settings("demo")[0]["value"] == "hello"
    assert read_env_file(env_path).get("PLUGIN_DEMO_GREETING") == "hello"
