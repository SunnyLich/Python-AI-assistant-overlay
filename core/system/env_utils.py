"""Shared helpers for reading and writing environment-style config."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


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
