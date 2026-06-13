"""Compatibility facade for the addon system.

Historically Wisp exposed ``core.plugin_manager`` and loaded in-process Python
packages from ``plugins/``. The runtime now uses ``core.addon_manager`` and runs
each addon in its own subprocess host, but these names remain so older call
sites and addons can migrate gradually.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.addon_manager import (
    AddonManager,
    AppContext,
    LoadedAddon,
    get_manager as _get_addon_manager,
    init as _init_addon_manager,
    plugin_setting as _addon_setting,
)

PluginManager = AddonManager
_LoadedMod = LoadedAddon

_manager: AddonManager | None = None


def plugin_setting(mod_name: str, key: str, default: Any = None) -> Any:
    return _addon_setting(mod_name, key, default)


def get_manager() -> AddonManager:
    global _manager
    if _manager is not None:
        return _manager
    _manager = _get_addon_manager()
    return _manager


def init(plugins_dir: Path) -> AddonManager:
    global _manager
    _manager = _init_addon_manager(plugins_dir)
    return _manager
