"""Tests for test settings model."""

from __future__ import annotations

import os
from unittest.mock import patch

import config
from core.settings_model import AppSettings


def _snapshot_config_globals() -> dict[str, object]:
    """Return a shallow snapshot of config globals mutated by config.reload()."""
    snapshot: dict[str, object] = {}
    for name, value in vars(config).items():
        if not name.isupper():
            continue
        if isinstance(value, list):
            snapshot[name] = list(value)
        elif isinstance(value, dict):
            snapshot[name] = dict(value)
        else:
            snapshot[name] = value
    return snapshot


def _restore_config_globals(snapshot: dict[str, object]) -> None:
    """Restore config globals after a reload-based settings test."""
    for name, value in snapshot.items():
        current = getattr(config, name, None)
        if isinstance(current, list) and isinstance(value, list):
            current[:] = value
        elif isinstance(current, dict) and isinstance(value, dict):
            current.clear()
            current.update(value)
        else:
            setattr(config, name, value)


def test_get_settings_returns_typed_snapshot():
    """Verify get settings returns typed snapshot behavior."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "anthropic",
                "LLM_MODEL": "claude-test",
                "CALLER_COUNT": "1",
                "CALLER_1_LABEL": "Typed",
                "VOICE_CONTEXT_MEMORY_MODE": "model",
                "BUBBLE_WIDTH": "420",
                "BUBBLE_FONT_SIZE": "14",
                "BUBBLE_SCROLL_ENABLED": "false",
                "BUBBLE_SCROLL_SNAP_DELAY_MS": "1800",
                "TRUST_PRIVACY_MODE": "true",
                "START_ON_LOGIN": "true",
            },
            clear=False,
        ):
            config.reload()

        settings = config.get_settings()

        assert isinstance(settings, AppSettings)
        assert settings.llm.provider == "anthropic"
        assert settings.llm.model == "claude-test"
        assert settings.ui.bubble_width == 420
        assert settings.ui.bubble_font_size == 14
        assert settings.ui.bubble_scroll_enabled is False
        assert settings.ui.bubble_scroll_snap_delay_ms == 1800
        assert settings.ui.start_on_login is True
        assert settings.privacy.trust_privacy_mode is True
        assert settings.callers.callers[0]["label"] == "Typed"
        assert settings.callers.voice["context_memory_mode"] == "model"
    finally:
        _restore_config_globals(previous_config)


def test_active_profile_overrides_model_and_budgets():
    """Verify active profile owns model choices and budget settings."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "openai",
                "LLM_MODEL": "base-model",
                "PROFILE_COUNT": "1",
                "PROFILE_1_ID": "deep-work",
                "PROFILE_1_LABEL": "Deep Work",
                "PROFILE_1_LLM_PROVIDER": "anthropic",
                "PROFILE_1_LLM_MODEL": "claude-profile",
                "PROFILE_1_CONTEXT_BROWSER_MAX_CHARS": "22222",
                "PROFILE_1_CONTEXT_TOOL_DOCUMENT_MAX_CHARS": "77777",
                "PROFILE_1_TOOL_TURN_MAX_CALLS": "6",
                "PROFILE_1_TOOL_TURN_MAX_RESULT_CHARS": "33333",
                "PROFILE_1_TOOL_TURN_MAX_TOTAL_CHARS": "99999",
                "PROFILE_1_CONTEXT_BROWSER_MODE": "model",
                "CALLER_COUNT": "1",
                "SETTINGS_PROFILE": "deep-work",
            },
            clear=True,
        ):
            config.reload()

        settings = config.get_settings()

        assert config.ACTIVE_PROFILE == "deep-work"
        assert config.LLM_PROVIDER == "anthropic"
        assert config.LLM_MODEL == "claude-profile"
        assert config.CONTEXT_BROWSER_MAX_CHARS == 22222
        assert config.CONTEXT_TOOL_DOCUMENT_MAX_CHARS == 77777
        assert config.TOOL_TURN_MAX_CALLS == 6
        assert settings.active_profile == "deep-work"
        assert settings.llm.provider == "anthropic"
        assert settings.tool_turn.max_total_chars == 99999
        assert config.CALLER_ROWS[0]["context_browser_mode"] == "model"
    finally:
        _restore_config_globals(previous_config)


def test_caller_can_select_profile_without_changing_active_profile():
    """Verify per-caller profile selection applies context defaults."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {
                "PROFILE_COUNT": "1",
                "PROFILE_1_ID": "coding-lite",
                "PROFILE_1_LABEL": "Coding Lite",
                "PROFILE_1_CONTEXT_DOCUMENTS_MODE": "model",
                "PROFILE_1_CONTEXT_BROWSER_MODE": "model",
                "PROFILE_1_CONTEXT_MEMORY_MODE": "off",
                "PROFILE_1_FILE_ACCESS": "read",
                "CALLER_COUNT": "1",
                "CALLER_1_PROFILE": "coding-lite",
            },
            clear=True,
        ):
            config.reload()

        row = config.CALLER_ROWS[0]

        assert config.ACTIVE_PROFILE == "default"
        assert row["profile"] == "coding-lite"
        assert row["context_documents_mode"] == "model"
        assert row["context_browser_mode"] == "model"
        assert row["context_memory_mode"] == "off"
        assert row["file_access"] == "read"
    finally:
        _restore_config_globals(previous_config)


def test_default_profile_preserves_legacy_second_caller_defaults():
    """Verify default profile does not accidentally enable rewrite context."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            config.reload()

        row = config.CALLER_ROWS[1]
        default_profile = config.resolve_profile("default")

        assert config.ACTIVE_PROFILE == "default"
        assert default_profile.caller_defaults["context_documents_mode"] == "off"
        assert config.CALLER_ROWS[0]["context_ambient"] is False
        assert config.CALLER_ROWS[0]["context_documents_mode"] == "off"
        assert config.CALLER_ROWS[0]["context_memory_mode"] == "off"
        assert row["context_ambient"] is False
        assert row["context_documents_mode"] == "off"
        assert row["context_memory_mode"] == "off"
    finally:
        _restore_config_globals(previous_config)


def test_trust_privacy_mode_can_be_disabled():
    """Verify trust privacy mode can be disabled behavior."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(
            os.environ,
            {
                "TRUST_PRIVACY_MODE": "false",
            },
            clear=True,
        ):
            config.reload()

        assert config.TRUST_PRIVACY_MODE is False
        assert config.get_settings().privacy.trust_privacy_mode is False
    finally:
        _restore_config_globals(previous_config)


def test_trust_privacy_mode_defaults_on():
    """Verify trust privacy mode defaults on behavior."""
    previous_config = _snapshot_config_globals()
    try:
        with patch("config.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            config.reload()

        assert config.TRUST_PRIVACY_MODE is True
        assert config.get_settings().privacy.trust_privacy_mode is True
    finally:
        _restore_config_globals(previous_config)
