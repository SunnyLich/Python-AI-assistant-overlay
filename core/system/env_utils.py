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
    return normalize_screenshot_mode(os.getenv(name), default)


# Per-caller tool override modes:
#   "on"    — tool is always offered to the model for this caller
#   "model" — offered subject to the per-prompt keyword filter (Tools tab)
#   "off"   — never offered, even when a context dropdown would expose it
# Tools absent from the mapping follow their default (context dropdown for
# context tools, off for installed/plugin tools).
TOOL_OVERRIDE_MODES = ("on", "model", "off")


def parse_tool_modes(value: str | None) -> dict[str, str]:
    """Parse a tool override list like "web_search:on,my_tool:model"."""
    modes: dict[str, str] = {}
    for entry in (value or "").split(","):
        name, _, mode = entry.strip().partition(":")
        name = name.strip()
        mode = mode.strip().lower()
        if name and mode in TOOL_OVERRIDE_MODES:
            modes[name] = mode
    return modes


def format_tool_modes(modes: dict[str, str]) -> str:
    """Inverse of parse_tool_modes; drops entries that are not real overrides."""
    return ",".join(
        f"{name}:{str(mode).strip().lower()}"
        for name, mode in sorted(modes.items())
        if str(mode).strip().lower() in TOOL_OVERRIDE_MODES
    )


def env_bool(name: str, default: bool = False) -> bool:
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
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if key is not None and value is not None
    }


def format_env_value(value: str) -> str:
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
