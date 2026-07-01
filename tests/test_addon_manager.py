"""Tests for process-hosted addons and the plugin compatibility facade."""
from __future__ import annotations

import os
import sys
import time
import textwrap
import zipfile
from pathlib import Path

import pytest
import config
from core.addon_distribution import install_addon_archive, install_addon_folder
import core.addon_manager as am
import core.addon_runtime as addon_runtime
import core.addon_store as addon_store
import core.addon_manager as pm
from core.tool_registry import ToolRegistry


_ADDON_SRC = """
import os
import sys
from core.addon_manager import addon_setting

def before_query(prompt, context):
    print("before-query-log", file=sys.stderr, flush=True)
    return prompt + "!" + str(os.getpid()), context + "|addon"

def after_response(text):
    pass

def on_event(event, payload):
    print("event-log:" + event, file=sys.stderr, flush=True)
    return {"event": event, "seen": sorted((payload or {}).keys())}

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

def get_intents():
    return [{"id": "dynamic", "label": "Dynamic", "key": "d", "prompt": "Dynamic prompt"}]

def get_notifications():
    return [{"title": "Demo", "message": "Loaded"}]

def get_hotkeys():
    return [{"id": "dynamic-hotkey", "label": "Dynamic hotkey", "hotkey": "ctrl+alt+d", "callback": lambda payload: {"message": "hotkey ok"}}]
"""


def _make_manager(tmp_path: Path, monkeypatch) -> tuple[am.AddonManager, Path]:
    """Verify make manager behavior."""
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
            hotkeys = true
            ui = ["tray", "settings", "intents", "notifications"]
            events = ["demo.event"]

            [[intents]]
            id = "static"
            label = "Static"
            key = "s"
            prompt = "Static prompt"

            [[notifications]]
            title = "Static"
            message = "Ready"

            [[hotkeys]]
            id = "static-hotkey"
            label = "Static hotkey"
            hotkey = "ctrl+alt+s"
            prompt = "Static hotkey prompt"
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
    """Verify addon hooks and tools run in host process behavior."""
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


def test_addon_events_intents_and_notifications(tmp_path, monkeypatch):
    """Verify addon events intents and notifications behavior."""
    manager, _store_path = _make_manager(tmp_path, monkeypatch)

    intents = manager.get_intents(caller_idx=0)
    assert {item["id"] for item in intents} == {"static", "dynamic"}
    assert manager.get_notifications() == [
        {"addon_id": "demo", "title": "Static", "message": "Ready"},
        {"addon_id": "demo", "title": "Demo", "message": "Loaded"},
    ]

    result = manager.dispatch_event("demo.event", {"answer": 42})
    assert result == [{"addon_id": "demo", "event": "demo.event", "seen": ["answer"]}]
    assert {item["id"] for item in manager.get_hotkeys()} == {"static-hotkey", "dynamic-hotkey"}
    assert manager.run_hotkey("demo", "static-hotkey") == {"prompt": "Static hotkey prompt"}
    assert manager.run_hotkey("demo", "dynamic-hotkey") == {"message": "hotkey ok"}
    manager.on_shutdown()


def test_text_annotation_hook_requires_explicit_permission_and_sanitizes(tmp_path, monkeypatch):
    """Verify text annotation addons are permission-gated and sanitized."""
    addons_dir = tmp_path / "addons"
    addons_dir.mkdir()
    specs = {
        "allowed": 'ui = ["text_annotations"]',
        "blocked": 'ui = ["settings"]',
        "broad": "ui = true",
    }
    for addon_id, ui_permission in specs.items():
        folder = addons_dir / addon_id
        folder.mkdir()
        (folder / "addon.toml").write_text(
            textwrap.dedent(
                f"""
                [addon]
                id = "{addon_id}"
                name = "{addon_id}"
                entry = "__init__.py"

                [permissions]
                {ui_permission}
                """
            ).strip(),
            encoding="utf-8",
        )
        (folder / "__init__.py").write_text(
            textwrap.dedent(
                """
                def get_text_annotations(payload):
                    assert "context" not in payload
                    return [
                        {"start": 0, "end": 4, "color": "#00ffaa", "tooltip": "visible"},
                        {"start": 5, "end": 9, "color": "not-a-color", "source": "addon:fake"},
                        {"start": 999, "end": 1000},
                    ]
                """
            ).strip(),
            encoding="utf-8",
        )
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    annotations = manager.get_text_annotations(
        {
            "text": "CUDA test",
            "context": "secret hidden context",
            "message_id": "m1",
            "conversation_id": "c1",
            "surface": "chat",
            "role": "assistant",
        }
    )

    assert len(annotations) == 2
    assert {item["source"] for item in annotations} == {"addon:allowed"}
    assert annotations[0]["color"] == "#00ffaa"
    assert annotations[1]["color"] == "#ffd166"
    assert all(item["message_id"] == "m1" for item in annotations)
    assert all(item["conversation_id"] == "c1" for item in annotations)
    manager.on_shutdown()


def test_addon_enable_and_settings_round_trip(tmp_path, monkeypatch):
    """Verify addon enable and settings round trip behavior."""
    manager, store_path = _make_manager(tmp_path, monkeypatch)
    registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
    manager.on_startup(am.AppContext(signals=None, model_tool_registry=registry, config=config))

    settings = manager.get_settings("demo")
    assert settings == [
        {"key": "greeting", "label": "Greeting", "type": "text", "default": "hi", "value": "hi"}
    ]
    assert pm.addon_setting("demo", "greeting", "fallback") == "fallback"

    manager.set_setting("demo", "greeting", "hello")
    assert pm.addon_setting("demo", "greeting") == "hello"
    assert manager.get_settings("demo")[0]["value"] == "hello"
    assert "hello" in store_path.read_text(encoding="utf-8")

    manager.set_enabled("demo", False)
    assert not manager.is_enabled("demo")
    assert "demo_tool" not in {s["name"] for s in registry.schemas()}

    manager.set_enabled("demo", True)
    assert manager.is_enabled("demo")
    assert "demo_tool" in {s["name"] for s in registry.schemas()}
    manager.on_shutdown()


def test_addon_stderr_is_exposed_in_summary_logs(tmp_path, monkeypatch):
    """Verify addon stderr is exposed in summary logs behavior."""
    manager, _store_path = _make_manager(tmp_path, monkeypatch)

    manager.before_query("hi", "")
    logs = ""
    for _ in range(20):
        logs = str(manager.summaries()[0].get("logs") or "")
        if "before-query-log" in logs:
            break
        time.sleep(0.05)

    assert "before-query-log" in logs
    manager.on_shutdown()


def test_addon_with_dependencies_waits_for_environment(tmp_path, monkeypatch):
    """Verify addon with dependencies waits for environment behavior."""
    monkeypatch.setattr(addon_runtime, "ADDON_ENVS_DIR", tmp_path / "addon_envs")
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "needs_deps"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "needs-deps"
            name = "needs_deps"
            entry = "__init__.py"

            [dependencies]
            python = ">=3.11"
            packages = ["requests>=2.31"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("def before_query(prompt, context):\n    return prompt, context\n", encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    summary = manager.summaries()[0]
    assert summary["status"] == "needs_approval"
    assert summary["runtime"]["tier"] == "2"
    assert summary["runtime"]["ready"] is False
    assert summary["runtime"]["needs_approval"] is True
    assert summary["dependencies"]["packages"] == ["requests>=2.31"]
    assert manager.before_query("hi", "") == ("hi", "")


def test_addon_manifest_accepts_cp1252_punctuation(tmp_path, monkeypatch):
    """Verify addon manifest accepts cp1252 punctuation behavior."""
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "legacy"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_bytes(
        b'[addon]\nid = "legacy"\nname = "Legacy"\ndescription = "old\x97new"\n'
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(addon_store, "_STORE_PATH", tmp_path / "addons.json")
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", tmp_path / "addons.json")

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    summary = manager.summaries()[0]
    assert summary["status"] == "loaded"
    assert summary["description"].startswith("old")
    assert summary["description"].endswith("new")
    assert ord(summary["description"][3]) == 0x2014


def test_manager_seeds_bundled_default_addons_when_missing(tmp_path, monkeypatch):
    """Verify bundled default addons are copied into the writable addon folder."""
    bundled_root = tmp_path / "bundle" / "addons"
    bundled_addon = bundled_root / "mcp_bridge"
    bundled_addon.mkdir(parents=True)
    (bundled_addon / "addon.toml").write_text("[addon]\nid = 'mcp-bridge'\nname = 'MCP Bridge'\n", encoding="utf-8")
    (bundled_addon / "__init__.py").write_text("", encoding="utf-8")
    (bundled_addon / "servers.json").write_text('{"servers": []}', encoding="utf-8")

    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    addon_store.set_enabled("mcp-bridge", False)

    addons_dir = tmp_path / "runtime" / "addons"
    manager = am.AddonManager(addons_dir, bundled_addons_dir=bundled_root)
    manager.load_all()

    assert (addons_dir / "mcp_bridge" / "addon.toml").exists()
    assert (addons_dir / "mcp_bridge" / "servers.json").read_text(encoding="utf-8") == '{"servers": []}'
    assert manager.summaries()[0]["id"] == "mcp-bridge"
    assert manager.summaries()[0]["enabled"] is False


def test_manager_does_not_overwrite_existing_default_addon(tmp_path, monkeypatch):
    """Verify seeded default addons preserve existing user configuration."""
    bundled_root = tmp_path / "bundle" / "addons"
    bundled_addon = bundled_root / "mcp_bridge"
    bundled_addon.mkdir(parents=True)
    (bundled_addon / "addon.toml").write_text("[addon]\nid = 'mcp-bridge'\nname = 'MCP Bridge'\n", encoding="utf-8")
    (bundled_addon / "__init__.py").write_text("", encoding="utf-8")
    (bundled_addon / "servers.json").write_text('{"servers": []}', encoding="utf-8")

    addons_dir = tmp_path / "runtime" / "addons"
    existing_addon = addons_dir / "mcp_bridge"
    existing_addon.mkdir(parents=True)
    (existing_addon / "addon.toml").write_text("[addon]\nid = 'mcp-bridge'\nname = 'MCP Bridge'\n", encoding="utf-8")
    (existing_addon / "__init__.py").write_text("", encoding="utf-8")
    (existing_addon / "servers.json").write_text('{"servers": [{"name": "custom"}]}', encoding="utf-8")

    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    addon_store.set_enabled("mcp-bridge", False)

    manager = am.AddonManager(addons_dir, bundled_addons_dir=bundled_root)
    manager.load_all()

    assert (existing_addon / "servers.json").read_text(encoding="utf-8") == '{"servers": [{"name": "custom"}]}'


def test_approved_addon_with_missing_environment_needs_install(tmp_path, monkeypatch):
    """Verify approved addon with missing environment needs install behavior."""
    monkeypatch.setattr(addon_runtime, "ADDON_ENVS_DIR", tmp_path / "addon_envs")
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "needs_deps"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "needs-deps"
            name = "needs_deps"
            entry = "__init__.py"

            [dependencies]
            packages = ["requests>=2.31"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)
    deps = addon_runtime.dependencies_from_manifest({"packages": ["requests>=2.31"]})
    addon_store.set_approved_dependency_hash("needs-deps", addon_runtime.dependency_hash(deps))

    manager = am.AddonManager(addons_dir)
    manager.load_all()

    summary = manager.summaries()[0]
    assert summary["status"] == "needs_dependencies"
    assert summary["runtime"]["needs_approval"] is False
    assert summary["runtime"]["ready"] is False


def test_addon_repair_environment_uses_ready_runtime(tmp_path, monkeypatch):
    """Verify addon repair environment uses ready runtime behavior."""
    deps = addon_runtime.AddonDependencies(python=">=3.11", packages=["demo-pkg"])
    monkeypatch.setattr(addon_runtime, "ADDON_ENVS_DIR", tmp_path / "addon_envs")
    env_dir = addon_runtime.env_path("demo")
    env_dir.mkdir(parents=True)
    addon_runtime.python_path(env_dir).parent.mkdir(parents=True, exist_ok=True)
    addon_runtime.python_path(env_dir).write_text("", encoding="utf-8")
    addon_runtime._write_marker(env_dir, deps)

    status = addon_runtime.environment_status("demo", deps)
    assert status["ready"] is True
    assert status["tier"] == "2"
    assert status["python"] == str(addon_runtime.python_path(env_dir))


def test_repair_environment_approves_current_dependency_hash(tmp_path, monkeypatch):
    """Verify repair environment approves current dependency hash behavior."""
    addons_dir = tmp_path / "addons"
    addon_dir = addons_dir / "needs_deps"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            id = "needs-deps"
            name = "needs_deps"
            entry = "__init__.py"

            [dependencies]
            packages = ["demo-pkg"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("", encoding="utf-8")
    store_path = tmp_path / "addons.json"
    monkeypatch.setattr(addon_store, "_STORE_PATH", store_path)
    monkeypatch.setattr(am.addon_store, "_STORE_PATH", store_path)

    def fake_provision(addon_id, deps, *, force=False):
        """Verify fake provision behavior."""
        return {
            "tier": "2",
            "ready": True,
            "python": sys.executable,
            "env_path": str(tmp_path / "env"),
            "packages": list(deps.packages),
            "python_requirement": deps.python,
            "hash": addon_runtime.dependency_hash(deps),
            "error": "",
        }

    monkeypatch.setattr(addon_runtime, "provision_environment", fake_provision)
    manager = am.AddonManager(addons_dir)
    manager.load_all()

    manager.repair_environment("needs-deps")

    deps = manager._mods[0].manifest.dependencies
    assert addon_store.approved_dependency_hash("needs-deps") == addon_runtime.dependency_hash(deps)


def test_install_addon_archive_rejects_path_traversal(tmp_path):
    """Verify install addon archive rejects path traversal behavior."""
    archive = tmp_path / "bad.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "nope")

    with pytest.raises(ValueError, match="unsafe"):
        install_addon_archive(archive, tmp_path / "addons")


def test_install_addon_archive_extracts_single_addon(tmp_path):
    """Verify install addon archive extracts single addon behavior."""
    archive = tmp_path / "demo.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("demo/addon.toml", "[addon]\nid = 'demo'\nname = 'Demo'\n")
        zf.writestr("demo/__init__.py", "")

    result = install_addon_archive(archive, tmp_path / "addons")

    assert result["id"] == "demo"
    assert (tmp_path / "addons" / "demo" / "addon.toml").exists()


def test_install_addon_folder_copies_single_addon(tmp_path):
    """Verify install addon folder copies single addon behavior."""
    source = tmp_path / "source" / "demo"
    source.mkdir(parents=True)
    (source / "addon.toml").write_text("[addon]\nid = 'demo'\nname = 'Demo'\n", encoding="utf-8")
    (source / "__init__.py").write_text("", encoding="utf-8")

    result = install_addon_folder(source, tmp_path / "addons")

    assert result["id"] == "demo"
    assert (tmp_path / "addons" / "demo" / "addon.toml").exists()


def test_missing_permissions_deny_surfaces(tmp_path, monkeypatch):
    """Verify missing permissions deny surfaces behavior."""
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
