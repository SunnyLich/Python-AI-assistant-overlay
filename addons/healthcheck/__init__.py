"""Diagnostic addon that proves the addon host system is wired up."""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

from core.plugin_manager import plugin_setting

_LOG = Path(__file__).with_name("healthcheck.log")


def _prefix() -> str:
    return str(plugin_setting("healthcheck", "log_prefix", "[healthcheck]"))


def _log(event: str) -> None:
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {event}\n"
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(f"[addon:healthcheck] {_prefix()} {event}", file=sys.stderr, flush=True)


def get_settings() -> list[dict]:
    return [
        {
            "key": "log_prefix",
            "label": "Log prefix",
            "type": "text",
            "default": "[healthcheck]",
            "help": "Text prefixed to every line this addon writes to stderr.",
        },
        {
            "key": "echo_suffix",
            "label": "Echo suffix",
            "type": "text",
            "default": "",
            "help": "Text appended to healthcheck_ping replies.",
        },
    ]


def on_startup(app_context) -> None:
    provider = getattr(app_context.config, "LLM_PROVIDER", "?")
    _log(f"on_startup fired; LLM_PROVIDER={provider}, data_dir={app_context.data_dir}")


def on_shutdown() -> None:
    _log("on_shutdown fired")


def before_query(prompt: str, context: str) -> tuple[str, str]:
    _log(f"before_query fired; prompt={prompt[:60]!r}")
    return prompt, context


def after_response(text: str) -> None:
    _log(f"after_response fired; {len(text)} chars")


def get_tray_actions() -> list[dict]:
    return [{"label": "Healthcheck: log a line", "callback": _on_tray_click}]


def _on_tray_click() -> None:
    _log("tray action clicked")


def get_tools() -> list[dict]:
    return [
        {
            "name": "healthcheck_ping",
            "description": "Diagnostic tool from the healthcheck addon. Returns pong and logs the call.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Optional note to echo back."}
                },
                "required": [],
            },
            "executor": _ping_executor,
        }
    ]


def _ping_executor(inputs: dict) -> str:
    note = str((inputs or {}).get("note", "")).strip()
    suffix = str(plugin_setting("healthcheck", "echo_suffix", "")).strip()
    _log(f"healthcheck_ping executed; note={note!r}")
    return "pong" + (f" - {note}" if note else "") + (f" {suffix}" if suffix else "")
