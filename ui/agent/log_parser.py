"""
ui/agent_run_log_parser.py - Small helpers for live agent-run log events.

The runner still writes human-readable logs, but the meeting UI should not have
to know timestamp stripping and common event prefixes inline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveLogEvent:
    kind: str
    body: str
    agent: str = ""
    value: str = ""


def log_body(line: str) -> str:
    """Strip the leading [HH:MM:SS] timestamp from a run log line."""
    return line.split("] ", 1)[1] if line.startswith("[") and "] " in line else line


def parse_live_log_event(line: str) -> LiveLogEvent:
    body = log_body(line)
    if body.startswith("agent turn ") and ": " in body:
        return LiveLogEvent("agent_turn", body, agent=body.rsplit(": ", 1)[1].strip())
    if body.startswith("agent read-only turn: "):
        return LiveLogEvent("agent_read_only_turn", body, agent=body.rsplit(": ", 1)[1].strip())
    if body.startswith("model response received") or body.startswith("model callback response received"):
        return LiveLogEvent("model_response_received", body)
    if body.startswith("agent response parse failed"):
        return LiveLogEvent("invalid_json", body)
    if body.startswith("using local fallback"):
        return LiveLogEvent("fallback", body)
    if body.startswith("message: ") and " -> " in body and ": " in body[9:]:
        return LiveLogEvent("message", body)
    return LiveLogEvent("log", body)
