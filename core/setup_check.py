"""Lightweight setup checks safe to run from the Settings UI."""
from __future__ import annotations

from typing import Any

from core.error_recommendations import recommendation_for


def _status(ok: bool, warning: bool = False) -> str:
    if ok:
        return "pass"
    return "warn" if warning else "fail"


def _secret_for_provider(config: Any, provider: str) -> str:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "xai": "XAI_API_KEY",
        "together": "TOGETHER_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "custom": "CUSTOM_API_KEY",
    }
    key_name = mapping.get(provider.lower(), "")
    return str(getattr(config, key_name, "") or "") if key_name else ""


def run_setup_check() -> list[dict[str, str]]:
    """Return setup check rows without importing audio, STT, or provider SDKs."""
    import config

    config.reload()
    rows: list[dict[str, str]] = []

    provider = str(getattr(config, "LLM_PROVIDER", "") or "").strip()
    model = str(getattr(config, "LLM_MODEL", "") or "").strip()
    llm_ok = bool(provider and model and (_secret_for_provider(config, provider) or provider in {"copilot", "chatgpt", "ollama"}))
    llm_message = (
        f"LLM route configured: {provider}/{model}."
        if llm_ok
        else f"LLM route incomplete: {provider or 'missing provider'}/{model or 'missing model'}."
    )
    rows.append(
        {
            "name": "LLM provider",
            "status": _status(llm_ok),
            "message": llm_message,
            "recommendation": "" if llm_ok else recommendation_for("missing API key"),
        }
    )

    tts_provider = str(getattr(config, "TTS_PROVIDER", "none") or "none").strip().lower()
    tts_ok = tts_provider == "none"
    if tts_provider == "cartesia":
        tts_ok = bool(getattr(config, "CARTESIA_API_KEY", ""))
    elif tts_provider == "elevenlabs":
        tts_ok = bool(getattr(config, "ELEVENLABS_API_KEY", ""))
    elif tts_provider == "openai":
        tts_ok = bool(getattr(config, "OPENAI_API_KEY", ""))
    elif tts_provider == "openai_compatible":
        tts_ok = bool(getattr(config, "TTS_CUSTOM_BASE_URL", ""))
    elif tts_provider == "gpt_sovits":
        tts_ok = bool(
            getattr(config, "GPT_SOVITS_URL", "")
            and getattr(config, "GPT_SOVITS_REF_AUDIO_PATH", "")
        )
    elif tts_provider == "kokoro":
        tts_ok = bool(getattr(config, "KOKORO_VOICE", ""))
    rows.append(
        {
            "name": "TTS",
            "status": _status(tts_ok, warning=tts_provider == "none"),
            "message": "TTS is off." if tts_provider == "none" else f"TTS provider configured: {tts_provider}.",
            "recommendation": "" if tts_ok else recommendation_for("tts no audio"),
        }
    )

    stt_model = str(getattr(config, "STT_MODEL", "") or "").strip()
    rows.append(
        {
            "name": "Speech to text",
            "status": "pass",
            "message": (
                f"STT model configured: {stt_model}."
                if stt_model
                else "STT is not configured; voice and dictation can stay off."
            ),
            "recommendation": "",
        }
    )

    hotkeys = [
        str(getattr(config, "HOTKEY_SNIP", "") or ""),
        str(getattr(config, "HOTKEY_VOICE", "") or ""),
        str(getattr(config, "HOTKEY_ADD_CONTEXT", "") or ""),
    ]
    caller_rows = getattr(config, "CALLER_ROWS", []) or []
    hotkeys.extend(str(row.get("hotkey") or "") for row in caller_rows if isinstance(row, dict))
    enabled_hotkeys = [key for key in hotkeys if key.strip()]
    rows.append(
        {
            "name": "Hotkeys",
            "status": _status(bool(enabled_hotkeys)),
            "message": f"{len(enabled_hotkeys)} hotkeys configured.",
            "recommendation": "" if enabled_hotkeys else recommendation_for("hotkey conflict"),
        }
    )

    privacy_enabled = bool(getattr(config, "TRUST_PRIVACY_MODE", True))
    rows.append(
        {
            "name": "Privacy redaction",
            "status": _status(privacy_enabled, warning=True),
            "message": "Privacy redaction is on." if privacy_enabled else "Privacy redaction is off.",
            "recommendation": "" if privacy_enabled else "Recommendation: turn on Trust/privacy mode before sending sensitive context.",
        }
    )
    return rows
