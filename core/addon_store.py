"""Persistent addon enablement and settings storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.system.paths import REPO_ROOT

_STORE_PATH = REPO_ROOT / "addons.json"


def store_path() -> Path:
    override = os.getenv("WISP_ADDON_STORE")
    return Path(override) if override else _STORE_PATH


def _read() -> dict[str, Any]:
    path = store_path()
    if not path.exists():
        return {"addons": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"addons": {}}
    return data if isinstance(data, dict) else {"addons": {}}


def _write(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _addon(data: dict[str, Any], addon_id: str) -> dict[str, Any]:
    addons = data.setdefault("addons", {})
    if not isinstance(addons, dict):
        data["addons"] = addons = {}
    item = addons.setdefault(addon_id, {})
    if not isinstance(item, dict):
        addons[addon_id] = item = {}
    return item


def is_enabled(addon_id: str, default: bool = True) -> bool:
    item = _read().get("addons", {}).get(addon_id, {})
    if isinstance(item, dict) and "enabled" in item:
        return bool(item.get("enabled"))
    return default


def set_enabled(addon_id: str, enabled: bool) -> None:
    data = _read()
    _addon(data, addon_id)["enabled"] = bool(enabled)
    _write(data)


def get_setting(addon_id: str, key: str, default: Any = None) -> Any:
    item = _read().get("addons", {}).get(addon_id, {})
    settings = item.get("settings", {}) if isinstance(item, dict) else {}
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    return default


def set_setting(addon_id: str, key: str, value: Any) -> None:
    data = _read()
    item = _addon(data, addon_id)
    settings = item.setdefault("settings", {})
    if not isinstance(settings, dict):
        item["settings"] = settings = {}
    settings[str(key)] = value
    _write(data)
