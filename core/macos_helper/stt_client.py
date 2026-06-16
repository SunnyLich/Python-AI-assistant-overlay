"""
core.macos_helper.stt_client — parent-side STT shim.

Mirrors the public surface of ``core.stt`` (prewarm / start_recording /
stop_and_transcribe) but forwards each call to the worker process. ``core.stt``
delegates here when the macOS helper is enabled, so callers (main.py's voice
path) are unchanged.

Failures degrade gracefully rather than crashing the hotkey/voice threads: a
broken worker means "no transcript" (""), not an exception propagating onto a
daemon thread.
"""
from __future__ import annotations

import logging

from core.macos_helper.client import HelperError, get_client

log = logging.getLogger("wisp.macos_helper")


def prewarm() -> None:
    """Fire-and-forget model load in the worker."""
    try:
        get_client().call("stt.prewarm", wait=False)
    except HelperError as exc:
        log.warning("helper stt.prewarm failed: %s", exc)


def reset_model() -> None:
    """Drop the worker's cached Whisper model after STT settings change."""
    try:
        get_client().call("stt.reset_model", timeout=10.0)
    except HelperError as exc:
        log.warning("helper stt.reset_model failed: %s", exc)


def start_recording() -> None:
    """Open the mic in the worker. Logs (does not raise) on failure so a worker
    hiccup can't throw on the hotkey thread; stop_and_transcribe then yields ""."""
    try:
        get_client().call("stt.start_recording", timeout=10.0)
    except HelperError as exc:
        log.error("helper stt.start_recording failed: %s", exc)


def stop_and_transcribe() -> str:
    """Stop + transcribe in the worker; returns "" on any failure."""
    try:
        return get_client().call("stt.stop_and_transcribe", timeout=120.0) or ""
    except HelperError as exc:
        log.error("helper stt.stop_and_transcribe failed: %s", exc)
        return ""
