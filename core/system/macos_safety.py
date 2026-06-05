"""macOS stability gates for native and streaming subsystems.

The shared Qt UI should stay the same across platforms, but macOS can crash
inside native libraries before Python can catch an exception. These helpers keep
the risky in-process paths opt-in while the backend is validated on real Macs.
"""
from __future__ import annotations

import os
import sys

_FALSE_VALUES = {"0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _env_false(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _FALSE_VALUES


def is_macos() -> bool:
    return sys.platform == "darwin"


def safe_mode_enabled() -> bool:
    """Return True when macOS crash-prone paths should use conservative defaults."""
    return is_macos() and not _env_false("WISP_MACOS_SAFE_MODE")


def audio_enabled() -> bool:
    """Allow in-process CoreAudio/PortAudio only after an explicit macOS opt-in."""
    return (not safe_mode_enabled()) or _env_true("WISP_MACOS_ENABLE_AUDIO")


def tts_prewarm_enabled() -> bool:
    return audio_enabled()


def stt_prewarm_enabled() -> bool:
    return (
        not safe_mode_enabled()
        or _env_true("WISP_MACOS_ENABLE_AUDIO")
        or _env_true("WISP_MACOS_ENABLE_STT_PREWARM")
    )


def fs_watcher_enabled() -> bool:
    """File watching is optional ambient context, so keep it out of macOS safe mode."""
    return (not safe_mode_enabled()) or _env_true("WISP_MACOS_ENABLE_FS_WATCHER")


def chromadb_enabled() -> bool:
    """ChromaDB loads native/vector dependencies; keep it explicit on macOS."""
    return (not is_macos()) or _env_true("WISP_MACOS_ENABLE_CHROMADB")


def memory_background_llm_enabled() -> bool:
    """Background memory summarization is useful, but not needed for prompt flow."""
    return (not is_macos()) or _env_true("WISP_MACOS_ENABLE_MEMORY_BACKGROUND_LLM")


def openai_compat_streaming_enabled(provider: str = "") -> bool:
    """OpenAI-compatible streaming is opt-in on macOS safe mode.

    The legacy Google-specific flag is honored for focused validation, but the
    broader flag is the preferred switch once the streaming path is proven.
    """
    provider = (provider or "").strip().lower()
    if not safe_mode_enabled():
        return True
    if _env_true("WISP_MACOS_OPENAI_COMPAT_STREAMING"):
        return True
    return provider == "google" and _env_true("WISP_MACOS_GOOGLE_STREAMING")


def openai_compat_tools_enabled() -> bool:
    """Live OpenAI-compatible tool loops are opt-in while safe mode is active."""
    return (not safe_mode_enabled()) or _env_true("WISP_MACOS_ENABLE_OPENAI_TOOLS")
