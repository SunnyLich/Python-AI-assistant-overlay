"""Environment file helpers for the settings dialog."""
from __future__ import annotations

from core.system.env_utils import format_env_value, read_env_file, write_env_file
from core.system.paths import REPO_ROOT


ENV_PATH = REPO_ROOT / ".env"


def read_settings_env() -> dict[str, str]:
    return read_env_file(ENV_PATH)


def format_settings_env_value(value: str) -> str:
    return format_env_value(value)


def write_settings_env(vals: dict[str, str], remove_keys: set[str] | None = None) -> None:
    write_env_file(ENV_PATH, vals, remove_keys=remove_keys)

