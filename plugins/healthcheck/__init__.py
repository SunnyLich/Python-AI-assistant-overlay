"""
healthcheck — a tiny Wisp mod that proves the plugin system is wired up.

Every hook appends a timestamped line to ``healthcheck.log`` next to this file
(and also prints to stderr, which the brain worker captures to
``wisp-brain.stderr.log``), and it contributes one model tool the LLM can call.

This is a diagnostic / smoke-test mod — safe to delete.

SECURITY: Mods run in-process with full Python access. Only install mods you trust.
"""
from __future__ import annotations

import datetime
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
    print(f"{_prefix()} {event}", flush=True)


def get_settings() -> list[dict]:
    return [
        {
            "key": "log_prefix",
            "label": "Log prefix",
            "type": "text",
            "default": "[healthcheck]",
            "help": "Text prefixed to every line this mod writes to stderr.",
        },
        {
            "key": "echo_emoji",
            "label": "Echo emoji in pong",
            "type": "bool",
            "default": "false",
            "help": "Append a checkmark to healthcheck_ping replies.",
        },
    ]


def on_startup(app_context) -> None:
    provider = getattr(app_context.config, "LLM_PROVIDER", "?")
    _log(f"on_startup fired — LLM_PROVIDER={provider}, signals={app_context.signals!r}")


def on_shutdown() -> None:
    _log("on_shutdown fired")


def before_query(prompt: str, context: str) -> tuple[str, str]:
    _log(f"before_query fired — prompt={prompt[:60]!r}")
    return prompt, context


def after_response(text: str) -> None:
    _log(f"after_response fired — {len(text)} chars")


def get_tray_actions() -> list[dict]:
    return [{"label": "Healthcheck: log a line", "callback": _on_tray_click}]


def _on_tray_click() -> None:
    # Runs headless in the brain worker — logging/side effects only, no UI.
    _log("tray action clicked")


def get_tools() -> list[dict]:
    return [
        {
            "name": "healthcheck_ping",
            "description": (
                "Diagnostic tool from the healthcheck mod. Returns 'pong' and "
                "logs the call. Use it to confirm plugin tools are callable."
            ),
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
    _log(f"healthcheck_ping executed — note={note!r}")
    reply = f"pong{(' — ' + note) if note else ''}"
    if str(plugin_setting("healthcheck", "echo_emoji", "false")).strip().lower() in ("1", "true", "yes", "on"):
        reply += " ✓"
    return reply
