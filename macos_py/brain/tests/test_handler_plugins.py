from __future__ import annotations

import sys
import types
from pathlib import Path

from wisp_brain import handlers


def test_plugins_list_handler_registered():
    assert "brain.plugins.list" in handlers.HANDLERS
    assert "brain.plugins.run_action" in handlers.HANDLERS


def test_plugins_list_returns_discovered_plugin_folder(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    example = plugin_dir / "example"
    example.mkdir(parents=True)
    (example / "__init__.py").write_text(
        "def before_query(prompt, context):\n"
        "    return prompt, context\n\n"
        "def get_tools():\n"
        "    return []\n",
        encoding="utf-8",
    )

    import core.system.paths as paths
    import core.plugin_manager as plugin_manager

    monkeypatch.setattr(paths, "PLUGINS_DIR", plugin_dir)
    monkeypatch.setattr(plugin_manager, "_manager", None)

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins_dir"] == str(plugin_dir)
    assert result["plugins"] == [
        {
            "name": "example",
            "path": str(example),
            "status": "loaded",
            "enabled": True,
            "hooks": ["before_query", "get_tools"],
            "tray_actions": [],
            "tools": [],
            "settings": [],
            "error": "",
        }
    ]


def test_plugins_list_initializes_shared_manager_and_action_can_run(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugins"
    example = plugin_dir / "native_action"
    marker = tmp_path / "ran.txt"
    example.mkdir(parents=True)
    (example / "__init__.py").write_text(
        "from pathlib import Path\n\n"
        "def _run():\n"
        f"    Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n\n"
        "def get_tray_actions():\n"
        "    return [{'label': 'Do Native Thing', 'callback': _run}]\n",
        encoding="utf-8",
    )

    import core.system.paths as paths
    import core.plugin_manager as plugin_manager

    monkeypatch.setattr(paths, "PLUGINS_DIR", plugin_dir)
    monkeypatch.setattr(plugin_manager, "_manager", None)

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins"] == [
        {
            "name": "native_action",
            "path": str(example),
            "status": "loaded",
            "enabled": True,
            "hooks": ["get_tray_actions"],
            "tray_actions": ["Do Native Thing"],
            "tools": [],
            "settings": [],
            "error": "",
        }
    ]

    action_result = handlers.HANDLERS["brain.plugins.run_action"](
        plugin_name="native_action",
        label="Do Native Thing",
    )

    assert action_result == {
        "ok": True,
        "message": "Ran plugin action: native_action / Do Native Thing",
    }
    assert marker.read_text(encoding="utf-8") == "ran"


def test_plugins_list_prefers_loaded_manager(monkeypatch, tmp_path):
    module = types.ModuleType("plugins.loaded")
    module.__file__ = str(tmp_path / "plugins" / "loaded" / "__init__.py")

    def get_tray_actions():
        return [{"label": "Do Thing", "callback": object()}]

    def get_tools():
        return [{"name": "loaded_tool"}]

    module.get_tray_actions = get_tray_actions
    module.get_tools = get_tools
    mod = types.SimpleNamespace(name="loaded", module=module)
    manager = types.SimpleNamespace(_mods=[mod])
    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    import core.system.paths as paths

    monkeypatch.setattr(paths, "PLUGINS_DIR", tmp_path / "plugins")

    result = handlers.HANDLERS["brain.plugins.list"]()

    assert result["plugins"] == [
        {
            "name": "loaded",
            "path": str(tmp_path / "plugins" / "loaded"),
            "status": "loaded",
            "enabled": True,
            "hooks": ["get_tools", "get_tray_actions"],
            "tray_actions": ["Do Thing"],
            "tools": ["loaded_tool"],
            "settings": [],
            "error": "",
        }
    ]


def test_plugins_run_action_invokes_loaded_tray_action(monkeypatch, tmp_path):
    calls: list[str] = []
    module = types.ModuleType("plugins.loaded")
    module.__file__ = str(tmp_path / "plugins" / "loaded" / "__init__.py")

    def action():
        calls.append("ran")

    def get_tray_actions():
        return [{"label": "Do Thing", "callback": action}]

    module.get_tray_actions = get_tray_actions
    mod = types.SimpleNamespace(name="loaded", module=module)
    manager = types.SimpleNamespace(_mods=[mod])
    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    result = handlers.HANDLERS["brain.plugins.run_action"](
        plugin_name="loaded",
        label="Do Thing",
    )

    assert result == {"ok": True, "message": "Ran plugin action: loaded / Do Thing"}
    assert calls == ["ran"]


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
    handlers.run_plugin_startup()  # idempotent — must not fire on_startup again

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
    module = types.ModuleType("plugins.loaded")
    module.get_tray_actions = lambda: [{"label": "Other", "callback": lambda: None}]
    mod = types.SimpleNamespace(name="loaded", module=module)
    manager = types.SimpleNamespace(_mods=[mod])
    fake_plugin_manager = types.ModuleType("core.plugin_manager")
    fake_plugin_manager.get_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "core.plugin_manager", fake_plugin_manager)

    import pytest

    with pytest.raises(ValueError, match="Plugin action not found"):
        handlers.HANDLERS["brain.plugins.run_action"](
            plugin_name="loaded",
            label="Do Thing",
        )
