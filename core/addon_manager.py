"""Addon lifecycle management for Wisp.

Addons are folders with an ``addon.toml`` manifest. Each enabled addon runs in
its own subprocess host so hook crashes and long-running code do not execute in
the brain process.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import addon_store
from core.system.paths import ADDONS_DIR, REPO_ROOT

log = logging.getLogger("wisp.addons")

_HOST_TIMEOUT_SECONDS = 2.0


def _terminal(event: str) -> None:
    try:
        print(f"[addon] {event}", file=sys.stderr, flush=True)
    except Exception:
        pass


@dataclass(frozen=True)
class AddonManifest:
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    entry: str = "__init__.py"
    api_version: str = "1"
    permissions: dict[str, Any] = field(default_factory=dict)
    settings: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoadedAddon:
    id: str
    name: str
    path: Path
    manifest: AddonManifest
    host: "AddonHostProcess | None" = None
    enabled: bool = True
    status: str = "loaded"
    error: str = ""
    hooks: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    tray_actions: list[str] = field(default_factory=list)


@dataclass
class AppContext:
    """Compatibility context for callers that still use ``core.plugin_manager``."""

    signals: Any
    model_tool_registry: Any
    config: Any


class AddonHostProcess:
    def __init__(self, addon: LoadedAddon, *, timeout: float = _HOST_TIMEOUT_SECONDS) -> None:
        self.addon = addon
        self.timeout = timeout
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"addon-{addon.id}")
        self._proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        cmd = [
            sys.executable,
            "-m",
            "core.addon_host",
            "--id",
            self.addon.id,
            "--folder",
            str(self.addon.path),
            "--entry",
            self.addon.manifest.entry,
            "--store",
            str(addon_store.store_path()),
        ]
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
        )

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self.call("on_shutdown", {}, timeout=1.0)
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    def restart(self) -> None:
        self.stop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"addon-{self.addon.id}")
        self.start()

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        self.start()
        future = self._executor.submit(self._raw_call, method, params or {})
        try:
            return future.result(timeout=self.timeout if timeout is None else timeout)
        except TimeoutError:
            self._kill_for_timeout(method)
            raise TimeoutError(f"addon {self.addon.id} timed out during {method}")

    def _raw_call(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None or proc.poll() is not None:
                raise RuntimeError(f"addon host is not running: {self.addon.id}")
            req_id = uuid.uuid4().hex
            proc.stdin.write(json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            while True:
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"addon host exited: {self.addon.id}")
                reply = json.loads(line)
                if reply.get("id") not in {req_id, None}:
                    continue
                if reply.get("error"):
                    raise RuntimeError(str(reply["error"]).strip())
                return reply.get("result")

    def _kill_for_timeout(self, method: str) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            _terminal(f"{self.addon.id} timed out in {method}; killing host")
            proc.kill()
        self._proc = None


class AddonManager:
    def __init__(self, addons_dir: Path | None = None):
        self._dir = addons_dir or ADDONS_DIR
        self._mods: list[LoadedAddon] = []  # compatibility name used by callers/tests
        self._tool_registry: Any = None

    def load_all(self) -> None:
        self.shutdown_hosts()
        self._mods = []
        if not self._dir.exists():
            log.debug("[addons] dir %s does not exist; skipping.", self._dir)
            return
        for child in sorted(p for p in self._dir.iterdir() if p.is_dir()):
            self._load_addon(child)

    def _load_addon(self, folder: Path) -> None:
        try:
            manifest = load_manifest(folder)
            enabled = addon_store.is_enabled(manifest.id, True)
            addon = LoadedAddon(
                id=manifest.id,
                name=manifest.name,
                path=folder,
                manifest=manifest,
                enabled=enabled,
            )
            if enabled:
                addon.host = AddonHostProcess(addon)
                addon.hooks = _safe_list(addon.host.call("hooks", {}, timeout=3.0))
                if _has_ui_permission(addon, "tray"):
                    addon.tray_actions = _safe_list(addon.host.call("get_tray_actions"))
                if _has_permission(addon, "tools"):
                    addon.tools = _safe_tool_specs(addon.host.call("get_tools"))
            else:
                addon.status = "disabled"
            self._mods.append(addon)
            _terminal(f"loaded {manifest.id} enabled={enabled}")
        except Exception:
            fallback_id = _valid_id(folder.name)
            self._mods.append(
                LoadedAddon(
                    id=fallback_id,
                    name=folder.name,
                    path=folder,
                    manifest=AddonManifest(id=fallback_id, name=folder.name),
                    enabled=False,
                    status="error",
                    error=traceback.format_exc(),
                )
            )
            log.error("[addons] Failed to load addon at %s:\n%s", folder, traceback.format_exc())
            _terminal(f"failed to load {folder.name}")

    def on_startup(self, app_context: AppContext) -> None:
        self._tool_registry = app_context.model_tool_registry
        for addon in self._enabled_addons():
            if addon.host is None:
                continue
            _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
            self._register_tools(addon)

    def on_shutdown(self) -> None:
        self.shutdown_hosts()

    def shutdown_hosts(self) -> None:
        for addon in getattr(self, "_mods", []):
            if addon.host is not None:
                addon.host.stop()
                addon.host = None

    def before_query(self, prompt: str, context_snapshot: str) -> tuple[str, str]:
        for addon in self._enabled_addons():
            if addon.host is None:
                continue
            query_perm = str(addon.manifest.permissions.get("query") or "none").lower()
            if query_perm not in {"read", "modify"}:
                continue
            result = _call_host(
                addon,
                "before_query",
                {"prompt": prompt, "context": context_snapshot},
            )
            if isinstance(result, dict):
                if query_perm == "modify":
                    prompt = str(result.get("prompt", prompt))
                    context_snapshot = str(result.get("context", context_snapshot))
        return prompt, context_snapshot

    def after_response(self, response_text: str) -> None:
        for addon in self._enabled_addons():
            response_perm = str(addon.manifest.permissions.get("response") or "none").lower()
            if addon.host is not None and response_perm in {"read", "modify"}:
                _call_host(addon, "after_response", {"text": response_text})

    def get_tray_actions(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for addon in self._enabled_addons():
            if not _has_ui_permission(addon, "tray"):
                continue
            labels = addon.tray_actions
            if addon.host is not None:
                labels = _safe_list(_call_host(addon, "get_tray_actions"))
                addon.tray_actions = labels
            for label in labels:
                actions.append({"label": label, "callback": _make_action_callback(addon, label)})
        return actions

    def run_tray_action(self, name: str, label: str) -> None:
        addon = self._find(name)
        if addon is None or addon.host is None or not addon.enabled:
            raise ValueError(f"Addon not loaded: {name}")
        if not _has_ui_permission(addon, "tray"):
            raise PermissionError(f"Addon is missing ui tray permission: {name}")
        _call_host(addon, "run_tray_action", {"label": label}, timeout=5.0)

    def mod_names(self) -> list[str]:
        return [m.name for m in self._mods]

    def is_enabled(self, name: str) -> bool:
        addon = self._find(name)
        return bool(addon and addon.enabled)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        addon = self._find(name)
        if addon is None:
            return False
        enabled = bool(enabled)
        if addon.enabled == enabled:
            return enabled
        addon_store.set_enabled(addon.id, enabled)
        addon.enabled = enabled
        addon.status = "loaded" if enabled else "disabled"
        if self._tool_registry is not None:
            self._tool_registry.unregister_source(f"addon:{addon.id}")
        if enabled:
            addon.host = AddonHostProcess(addon)
            addon.hooks = _safe_list(_call_host(addon, "hooks", {}, timeout=3.0))
            addon.tray_actions = (
                _safe_list(_call_host(addon, "get_tray_actions"))
                if _has_ui_permission(addon, "tray")
                else []
            )
            addon.tools = (
                _safe_tool_specs(_call_host(addon, "get_tools"))
                if _has_permission(addon, "tools")
                else []
            )
            if self._tool_registry is not None:
                self._register_tools(addon)
                _call_host(addon, "on_startup", {"data_dir": str(_data_dir(addon.id))}, timeout=3.0)
        elif addon.host is not None:
            addon.host.stop()
            addon.host = None
        return enabled

    def get_settings(self, name: str) -> list[dict[str, Any]]:
        addon = self._find(name)
        if addon is None:
            return []
        descriptors: list[dict[str, Any]] = []
        descriptors.extend(dict(s) for s in addon.manifest.settings if isinstance(s, dict))
        if addon.enabled and addon.host is not None and _has_ui_permission(addon, "settings"):
            descriptors.extend(
                dict(s)
                for s in _safe_list(_call_host(addon, "get_settings"))
                if isinstance(s, dict)
            )
        out: list[dict[str, Any]] = []
        for item in descriptors:
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            item["value"] = addon_store.get_setting(addon.id, key, item.get("default"))
            out.append(item)
        return out

    def set_setting(self, name: str, key: str, value: Any) -> None:
        addon = self._find(name)
        if addon is None or not str(key).strip():
            return
        addon_store.set_setting(addon.id, str(key).strip(), value)

    def summaries(self) -> list[dict[str, Any]]:
        return [self.payload(addon) for addon in self._mods]

    def payload(self, addon: LoadedAddon) -> dict[str, Any]:
        return {
            "id": addon.id,
            "name": addon.name,
            "path": str(addon.path),
            "status": addon.status,
            "enabled": bool(addon.enabled),
            "hooks": list(addon.hooks),
            "tray_actions": list(addon.tray_actions),
            "tools": [str(t.get("name") or "") for t in addon.tools if isinstance(t, dict)],
            "settings": self.get_settings(addon.name),
            "permissions": addon.manifest.permissions,
            "description": addon.manifest.description,
            "error": addon.error,
        }

    def _register_tools(self, addon: LoadedAddon) -> None:
        if self._tool_registry is None:
            return
        from core.tool_registry import ToolSpec

        for item in addon.tools:
            name = str(item.get("name") or "").strip()
            if not name or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
                continue
            spec = ToolSpec(
                name=name,
                description=str(item.get("description") or name),
                input_schema=item.get("input_schema") or {"type": "object", "properties": {}, "required": []},
                executor=_make_tool_executor(addon, name) if item.get("has_executor") else None,
                source=f"addon:{addon.id}",
            )
            self._tool_registry.register_builtin(spec)

    def _enabled_addons(self) -> list[LoadedAddon]:
        return [addon for addon in self._mods if addon.enabled and addon.status != "error"]

    def _find(self, name: str) -> LoadedAddon | None:
        for addon in self._mods:
            if addon.id == name or addon.name == name:
                return addon
        return None


def load_manifest(folder: Path) -> AddonManifest:
    path = _first_existing(folder / "addon.toml", folder / "plugin.toml")
    if path is None:
        legacy = folder / "__init__.py"
        if legacy.exists():
            addon_id = _valid_id(folder.name)
            return AddonManifest(id=addon_id, name=folder.name, entry="__init__.py")
        raise FileNotFoundError("missing addon.toml")
    data = _load_toml(path)
    plugin = data.get("addon") or data.get("plugin") or {}
    if not isinstance(plugin, dict):
        plugin = {}
    addon_id = _valid_id(str(plugin.get("id") or folder.name))
    raw_settings = data.get("settings") or []
    if isinstance(raw_settings, dict):
        raw_settings = [
            {"key": key, **value} if isinstance(value, dict) else {"key": key, "default": value}
            for key, value in raw_settings.items()
        ]
    return AddonManifest(
        id=addon_id,
        name=str(plugin.get("name") or folder.name),
        version=str(plugin.get("version") or "0.0.0"),
        description=str(plugin.get("description") or ""),
        entry=str(plugin.get("entry") or "__init__.py"),
        api_version=str(plugin.get("api_version") or "1"),
        permissions=data.get("permissions") if isinstance(data.get("permissions"), dict) else {},
        settings=raw_settings if isinstance(raw_settings, list) else [],
        tools=data.get("tools") if isinstance(data.get("tools"), list) else [],
    )


def plugin_setting(addon_id: str, key: str, default: Any = None) -> Any:
    return addon_store.get_setting(_valid_id(addon_id), key, default)


def init(addons_dir: Path | None = None) -> AddonManager:
    global _manager
    _manager = AddonManager(addons_dir or ADDONS_DIR)
    _manager.load_all()
    return _manager


def get_manager() -> AddonManager:
    if _manager is None:
        raise RuntimeError("AddonManager not initialised yet; call init() first.")
    return _manager


def _make_tool_executor(addon: LoadedAddon, name: str):
    def _executor(inputs: dict[str, Any]) -> str:
        if addon.host is None:
            addon.host = AddonHostProcess(addon)
        result = _call_host(addon, "execute_tool", {"name": name, "inputs": inputs or {}}, timeout=8.0)
        return "" if result is None else str(result)

    return _executor


def _make_action_callback(addon: LoadedAddon, label: str):
    def _callback() -> None:
        if addon.host is None:
            addon.host = AddonHostProcess(addon)
        _call_host(addon, "run_tray_action", {"label": label}, timeout=5.0)

    return _callback


def _call_host(addon: LoadedAddon, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
    if addon.host is None:
        return None
    try:
        return addon.host.call(method, params or {}, timeout=timeout)
    except Exception as exc:
        addon.error = f"{type(exc).__name__}: {exc}"
        log.error("[addons] %s.%s failed:\n%s", addon.id, method, traceback.format_exc())
        return None


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_tool_specs(value: Any) -> list[dict[str, Any]]:
    return [item for item in _safe_list(value) if isinstance(item, dict)]


def _has_permission(addon: LoadedAddon, key: str) -> bool:
    return bool(addon.manifest.permissions.get(key))


def _has_ui_permission(addon: LoadedAddon, feature: str) -> bool:
    ui = addon.manifest.permissions.get("ui")
    if isinstance(ui, list):
        return feature in {str(item) for item in ui}
    return ui is True or str(ui).lower() == "true"


def _data_dir(addon_id: str) -> Path:
    return REPO_ROOT / "addon_data" / addon_id


def _valid_id(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip().lower()).strip("-")
    return value or "addon"


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _load_toml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        from core.tool_registry import _load_simple_toml

        return _load_simple_toml(text)


_manager: AddonManager | None = None
