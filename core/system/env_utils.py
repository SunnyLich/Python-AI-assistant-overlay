"""Shared helpers for reading and writing environment-style config."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}

# Tri-state screenshot context modes (per caller hotkey):
#   "off"   — never capture
#   "auto"  — always capture at hotkey time and attach it to the query
#   "model" — expose the capture_screen tool so the model grabs one on demand
SCREENSHOT_MODES = ("off", "auto", "model")
FILE_ACCESS_MODES = ("off", "read", "ask", "auto")


def normalize_screenshot_mode(value, default: str = "off") -> str:
    """Map a raw value (incl. legacy booleans) to "off" | "auto" | "model"."""
    if value is None:
        return default
    v = str(value).strip().lower()
    if v in {"auto", "on", "true", "1", "yes", "always"}:
        return "auto"
    if v in {"model", "decide", "ask", "tool", "tools"}:
        return "model"
    if v in {"off", "false", "0", "no", "none", ""}:
        return "off"
    return default


def env_screenshot_mode(name: str, default: str = "off") -> str:
    """Handle env screenshot mode for system env utils."""
    return normalize_screenshot_mode(os.getenv(name), default)


def normalize_file_access_mode(value, default: str = "off") -> str:
    """Map a raw value to off/read/ask/auto local-file access."""
    v = str(value if value is not None else default).strip().lower()
    aliases = {
        "none": "off",
        "never": "off",
        "disabled": "off",
        "readonly": "read",
        "read-only": "read",
        "read_only": "read",
        "on": "ask",
        "true": "ask",
        "yes": "ask",
        "model": "ask",
        "write": "ask",
        "always": "auto",
    }
    v = aliases.get(v, v)
    return v if v in FILE_ACCESS_MODES else default


def env_file_access_mode(name: str, default: str = "off") -> str:
    """Read a per-caller local-file access mode from the environment."""
    return normalize_file_access_mode(os.getenv(name), default)


# Per-caller tool override modes:
#   "on"    — tool is always offered to the model for this caller
#   "model" — offered subject to the per-prompt keyword filter (Tools tab)
#   "off"   — never offered
# Context-fetch tools are governed by context controls, not this mapping.
# Tools absent from the mapping follow their default (off for installed/plugin
# tools, Local files dropdown for file tools).
TOOL_OVERRIDE_MODES = ("on", "model", "off")
CONTEXT_GOVERNED_TOOL_NAMES = {
    "web_search",
    "get_context",
    "get_context.browser",
    "get_context.documents",
    "retrieve_website",
    "git_status",
    "git_diff",
    "github_repo",
    "github_issue",
    "memory_search",
    "capture_screen",
}


def parse_tool_modes(value: str | None) -> dict[str, str]:
    """Parse a tool override list like "web_search:on,my_tool:model"."""
    modes: dict[str, str] = {}
    for entry in (value or "").split(","):
        name, _, mode = entry.strip().partition(":")
        name = name.strip()
        mode = mode.strip().lower()
        if name and name not in CONTEXT_GOVERNED_TOOL_NAMES and mode in TOOL_OVERRIDE_MODES:
            modes[name] = mode
    return modes


def format_tool_modes(modes: dict[str, str]) -> str:
    """Inverse of parse_tool_modes; drops entries that are not real overrides."""
    return ",".join(
        f"{name}:{str(mode).strip().lower()}"
        for name, mode in sorted(modes.items())
        if str(mode).strip().lower() in TOOL_OVERRIDE_MODES
        and str(name).strip() not in CONTEXT_GOVERNED_TOOL_NAMES
    )


def env_bool(name: str, default: bool = False) -> bool:
    """Handle env bool for system env utils."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def env_int(name: str, default: int) -> int:
    """Handle env int for system env utils."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    """Handle env float for system env utils."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def read_env_file(path: Path) -> dict[str, str]:
    """Read env file."""
    if not path.exists():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if key is not None and value is not None
    }


def format_env_value(value: str) -> str:
    """Format env value."""
    if any(ch in value for ch in ("\n", "\r", '"', "#")):
        escaped = (
            value.replace("\\", "\\\\")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\n")
            .replace('"', '\\"')
        )
        return f'"{escaped}"'
    return value


def write_env_file(
    path: Path,
    values: dict[str, str],
    remove_keys: set[str] | None = None,
) -> None:
    """Write env file."""
    remove_keys = remove_keys or set()
    lines: list[str] = []
    written: set[str] = set()

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remove_keys:
                    continue
                if key in values:
                    lines.append(f"{key}={format_env_value(values[key])}")
                    written.add(key)
                    continue
            lines.append(line)

    for key, value in values.items():
        if key not in written:
            lines.append(f"{key}={format_env_value(value)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
