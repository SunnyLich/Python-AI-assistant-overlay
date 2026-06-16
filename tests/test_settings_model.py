from __future__ import annotations

import os
from unittest.mock import patch

import config
from core.settings_model import AppSettings


def test_get_settings_returns_typed_snapshot():
    previous_rows = list(config.CALLER_ROWS)
    previous_voice = dict(config.VOICE_CALLER)
    previous_settings = config.SETTINGS
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
            },
            clear=False,
        ):
            config.reload()

        settings = config.get_settings()

        assert isinstance(settings, AppSettings)
        assert settings.llm.provider == "anthropic"
        assert settings.llm.model == "claude-test"
        assert settings.ui.bubble_width == 420
        assert settings.callers.callers[0]["label"] == "Typed"
        assert settings.callers.voice["context_memory_mode"] == "model"
    finally:
        config.CALLER_ROWS[:] = previous_rows
        config.VOICE_CALLER.clear()
        config.VOICE_CALLER.update(previous_voice)
        config.SETTINGS = previous_settings
