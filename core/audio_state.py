"""Lightweight shared audio state.

Keep tiny control flags here so worker IPC handlers can update them without
importing the full playback stack and its native audio dependencies.
"""

from __future__ import annotations

import threading

_tts_speed_boost = False
_tts_speed_lock = threading.Lock()


def set_tts_speed_boost(enabled: bool) -> None:
    """Called while the user holds the speech bubble."""
    global _tts_speed_boost
    with _tts_speed_lock:
        _tts_speed_boost = bool(enabled)


def tts_speed_boost_enabled() -> bool:
    with _tts_speed_lock:
        return _tts_speed_boost


def current_tts_rate(*, playback_rate: float, hold_playback_rate: float) -> float:
    rate = hold_playback_rate if tts_speed_boost_enabled() else playback_rate
    return max(0.25, min(4.0, float(rate)))
