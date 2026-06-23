"""Tests for Settings setup/health checks."""

import pytest

from core import setup_check


pytestmark = pytest.mark.workflow


def test_setup_check_reports_core_readiness(monkeypatch):
    """Setup check returns statuses, messages, and recommendations."""
    import config

    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-5.5", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "TTS_PROVIDER", "none", raising=False)
    monkeypatch.setattr(config, "STT_MODEL", "base", raising=False)
    monkeypatch.setattr(config, "HOTKEY_SNIP", "ctrl+alt+q", raising=False)
    monkeypatch.setattr(config, "HOTKEY_VOICE", "f9", raising=False)
    monkeypatch.setattr(config, "HOTKEY_ADD_CONTEXT", "alt+q", raising=False)
    monkeypatch.setattr(config, "CALLER_ROWS", [{"hotkey": "ctrl+q"}], raising=False)
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)

    rows = setup_check.run_setup_check()
    by_name = {row["name"]: row for row in rows}

    assert by_name["LLM provider"]["status"] == "fail"
    assert "Recommendation:" in by_name["LLM provider"]["recommendation"]
    assert by_name["TTS"]["status"] == "pass"
    assert by_name["Speech to text"]["status"] == "pass"
    assert by_name["Hotkeys"]["status"] == "pass"
    assert by_name["Privacy redaction"]["status"] == "pass"


def test_setup_check_treats_unconfigured_stt_as_optional(monkeypatch):
    """Missing STT settings should not warn when voice/dictation are unused."""
    import config

    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "llama-test", raising=False)
    monkeypatch.setattr(config, "TTS_PROVIDER", "none", raising=False)
    monkeypatch.setattr(config, "STT_MODEL", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_SNIP", "ctrl+alt+q", raising=False)
    monkeypatch.setattr(config, "HOTKEY_VOICE", "", raising=False)
    monkeypatch.setattr(config, "HOTKEY_ADD_CONTEXT", "", raising=False)
    monkeypatch.setattr(config, "CALLER_ROWS", [], raising=False)
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)

    rows = setup_check.run_setup_check()
    by_name = {row["name"]: row for row in rows}

    assert by_name["Speech to text"]["status"] == "pass"
    assert "voice and dictation can stay off" in by_name["Speech to text"]["message"]


def test_setup_check_accepts_gpt_sovits_when_reference_is_configured(monkeypatch):
    """GPT-SoVITS TTS is configured when URL and reference audio are present."""
    import config

    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "llama-test", raising=False)
    monkeypatch.setattr(config, "TTS_PROVIDER", "gpt_sovits", raising=False)
    monkeypatch.setattr(config, "GPT_SOVITS_URL", "http://127.0.0.1:9880", raising=False)
    monkeypatch.setattr(config, "GPT_SOVITS_REF_AUDIO_PATH", r"C:\voices\ref.wav", raising=False)
    monkeypatch.setattr(config, "STT_MODEL", "base", raising=False)
    monkeypatch.setattr(config, "HOTKEY_SNIP", "ctrl+alt+q", raising=False)
    monkeypatch.setattr(config, "HOTKEY_VOICE", "f9", raising=False)
    monkeypatch.setattr(config, "HOTKEY_ADD_CONTEXT", "alt+q", raising=False)
    monkeypatch.setattr(config, "CALLER_ROWS", [{"hotkey": "ctrl+q"}], raising=False)
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)

    rows = setup_check.run_setup_check()
    by_name = {row["name"]: row for row in rows}

    assert by_name["TTS"]["status"] == "pass"
    assert "gpt_sovits" in by_name["TTS"]["message"]


def test_setup_check_accepts_kokoro_with_voice(monkeypatch):
    """Kokoro TTS is configured when a built-in voice is selected."""
    import config

    monkeypatch.setattr(config, "reload", lambda: None)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "llama-test", raising=False)
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(config, "KOKORO_VOICE", "af_heart", raising=False)
    monkeypatch.setattr(config, "STT_MODEL", "base", raising=False)
    monkeypatch.setattr(config, "HOTKEY_SNIP", "ctrl+alt+q", raising=False)
    monkeypatch.setattr(config, "HOTKEY_VOICE", "f9", raising=False)
    monkeypatch.setattr(config, "HOTKEY_ADD_CONTEXT", "alt+q", raising=False)
    monkeypatch.setattr(config, "CALLER_ROWS", [{"hotkey": "ctrl+q"}], raising=False)
    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)

    rows = setup_check.run_setup_check()
    by_name = {row["name"]: row for row in rows}

    assert by_name["TTS"]["status"] == "pass"
    assert "kokoro" in by_name["TTS"]["message"]
