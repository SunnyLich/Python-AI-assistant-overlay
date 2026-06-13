from __future__ import annotations

import sys
import types
from pathlib import Path

from wisp_brain import handlers


def test_plugins_list_handler_registered():
    assert "brain.plugins.list" in handlers.HANDLERS
    assert "brain.plugins.run_action" in handlers.HANDLERS


def test_plugins_list_returns_discovered_addon_folder(tmp_path, monkeypatch):
    addon_dir = tmp_path / "addons"
    example = addon_dir / "example"
    example.mkdir(parents=True)
    (example / "addon.toml").write_text(
        "[addon]\nid = 'example'\nname = 'Example'\nentry = '__init__.py'\n",
        encoding="utf-8",
    )
    (example / "__init__.py").write_text(
        "def before_query(prompt, context):\n"
        "    return prompt, context\n\n"
        "def get_tools():\n"
        "    return []\n",
        encoding="utf-8",
    )

    import core.addon_manager as addon_manager
    import core.plugin_manager as plugin_manager
    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", addon_dir)
    monkeypatch.setattr(addon_manager, "_manager", None)
    monkeypatch.setattr(plugin_manager, "_manager", None)

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins_dir"] == str(addon_dir)
    assert result["plugins"][0]["id"] == "example"
    assert result["plugins"][0]["name"] == "Example"
    assert result["plugins"][0]["hooks"] == ["before_query", "get_tools"]

    plugin_manager.get_manager().on_shutdown()


def test_plugins_list_initializes_shared_manager_and_action_can_run(tmp_path, monkeypatch):
    addon_dir = tmp_path / "addons"
    example = addon_dir / "native_action"
    marker = tmp_path / "ran.txt"
    example.mkdir(parents=True)
    (example / "addon.toml").write_text(
        "[addon]\nid = 'native-action'\nname = 'native_action'\nentry = '__init__.py'\n\n"
        "[permissions]\nui = ['tray']\n",
        encoding="utf-8",
    )
    (example / "__init__.py").write_text(
        "from pathlib import Path\n\n"
        "def _run():\n"
        f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n\n"
        "def get_tray_actions():\n"
        "    return [{'label': 'Do Native Thing', 'callback': _run}]\n",
        encoding="utf-8",
    )

    import core.addon_manager as addon_manager
    import core.plugin_manager as plugin_manager
    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", addon_dir)
    monkeypatch.setattr(addon_manager, "_manager", None)
    monkeypatch.setattr(plugin_manager, "_manager", None)

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins"][0]["id"] == "native-action"
    assert result["plugins"][0]["tray_actions"] == ["Do Native Thing"]

    action_result = handlers.HANDLERS["brain.plugins.run_action"](
        plugin_name="native-action",
        label="Do Native Thing",
    )

    assert action_result == {
        "ok": True,
        "message": "Ran addon action: native-action / Do Native Thing",
    }
    assert marker.read_text(encoding="utf-8") == "ran"
    plugin_manager.get_manager().on_shutdown()


def test_plugins_list_prefers_loaded_manager(monkeypatch, tmp_path):
    manager = types.SimpleNamespace(
        summaries=lambda: [
            {
                "id": "loaded",
                "name": "loaded",
                "path": str(tmp_path / "addons" / "loaded"),
                "status": "loaded",
                "enabled": True,
                "hooks": ["get_tools", "get_tray_actions"],
                "tray_actions": ["Do Thing"],
                "tools": ["loaded_tool"],
                "settings": [],
                "permissions": {},
                "description": "",
                "error": "",
            }
        ]
    )
    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    import core.system.paths as paths

    monkeypatch.setattr(paths, "ADDONS_DIR", tmp_path / "addons")

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins"] == manager.summaries()


def test_plugins_run_action_invokes_loaded_tray_action(monkeypatch):
    calls: list[tuple[str, str]] = []

    class FakeManager:
        def run_tray_action(self, name: str, label: str) -> None:
            calls.append((name, label))

    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    result = handlers.HANDLERS["brain.plugins.run_action"](
        plugin_name="loaded",
        label="Do Thing",
    )

    assert result == {"ok": True, "message": "Ran addon action: loaded / Do Thing"}
    assert calls == [("loaded", "Do Thing")]


def test_run_plugin_startup_runs_once_with_app_context(monkeypatch):
    import core.llm_clients.client as client

    sentinel_registry = object()
    monkeypatch.setattr(client, "get_tool_registry", lambda: sentinel_registry)

    class AppContext:
        def __init__(self, *, signals, model_tool_registry, config):
            self.signals = signals
            self.model_tool_registry = model_tool_registry
            self.config = config

    class FakeManager:
        def __init__(self):
            self.startups = []

        def on_startup(self, ctx):
            self.startups.append(ctx)

    manager = FakeManager()
    fake_pm = types.ModuleType("core.plugin_manager")
    fake_pm.AppContext = AppContext
    fake_pm.get_manager = lambda: manager
    fake_pm.init = lambda _dir: manager
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_pm)
    monkeypatch.setattr(handlers, "_plugin_startup_done", False)

    handlers.run_plugin_startup()
    handlers.run_plugin_startup()

    assert len(manager.startups) == 1
    ctx = manager.startups[0]
    assert ctx.signals is None
    assert ctx.model_tool_registry is sentinel_registry

    import config as cfg

    assert ctx.config is cfg


def test_plugins_run_action_validates_inputs():
    import pytest

    with pytest.raises(ValueError, match="plugin_name"):
        handlers.HANDLERS["brain.plugins.run_action"](plugin_name="", label="Do Thing")
    with pytest.raises(ValueError, match="label"):
        handlers.HANDLERS["brain.plugins.run_action"](plugin_name="loaded", label="")


def test_plugins_run_action_reports_missing_action(monkeypatch):
    class FakeManager:
        def run_tray_action(self, name: str, label: str) -> None:
            raise ValueError(f"Addon action not found: {name} / {label}")

    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: FakeManager()
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    import pytest

    with pytest.raises(ValueError, match="Addon action not found"):
        handlers.HANDLERS["brain.plugins.run_action"](
            plugin_name="loaded",
            label="Do Thing",
        )
