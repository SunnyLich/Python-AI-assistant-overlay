from __future__ import annotations

import sys
import types

from wisp_brain import handlers


def test_config_reload_handler_registered():
    assert "brain.config.reload" in handlers.HANDLERS


def test_llm_test_handler_registered():
    assert "brain.llm.test" in handlers.HANDLERS
    assert "brain.llm.test" not in handlers.STREAMING


def test_config_reload_calls_config_reload(monkeypatch):
    calls: list[str] = []

    fake_config = types.ModuleType("config")
    fake_config.LLM_PROVIDER = "openai"
    fake_config.LLM_MODEL = "gpt-5.4"
    fake_config.TTS_PROVIDER = "none"

    def reload() -> None:
        calls.append("reload")
        fake_config.LLM_PROVIDER = "anthropic"
        fake_config.LLM_MODEL = "claude-sonnet-4-5"
        fake_config.TTS_PROVIDER = "cartesia"

    fake_config.reload = reload
    monkeypatch.setitem(sys.modules, "config", fake_config)

    result = handlers.HANDLERS["brain.config.reload"]()

    assert calls == ["reload"]
    assert result == {
        "ok": True,
        "llm_provider": "anthropic",
        "llm_model": "claude-sonnet-4-5",
        "tts_provider": "cartesia",
    }


def test_llm_test_offline_seam(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")

    result = handlers.HANDLERS["brain.llm.test"](
        provider="openai",
        model="gpt-5.4",
        route_name="LLM",
    )

    assert result == {
        "ok": True,
        "message": "LLM route OK: openai / gpt-5.4",
        "provider": "openai",
        "model": "gpt-5.4",
    }


def test_llm_test_offline_vision_message(monkeypatch):
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")

    result = handlers.HANDLERS["brain.llm.test"](
        provider="anthropic",
        model="claude-sonnet-4-5",
        route_name="VISION_LLM",
        image=True,
    )

    assert result["ok"] is True
    assert result["message"] == "VISION_LLM vision route OK: anthropic / claude-sonnet-4-5"


def test_llm_test_requires_provider_and_model():
    result = handlers.HANDLERS["brain.llm.test"](
        provider="",
        model="",
        route_name="MEMORY_LLM",
    )

    assert result == {
        "ok": False,
        "message": "MEMORY_LLM test failed: No model configured.",
        "provider": "",
        "model": "",
    }


def test_llm_test_forwards_route_to_client(monkeypatch):
    captured = {}
    fake_client = types.ModuleType("core.llm_clients.client")

    def fake_test_route_connection(provider, model, route_name, *, image=False, custom_base_url=None):
        captured["provider"] = provider
        captured["model"] = model
        captured["route_name"] = route_name
        captured["image"] = image
        captured["custom_base_url"] = custom_base_url
        return True, "ok"

    fake_client.test_route_connection = fake_test_route_connection
    monkeypatch.setitem(sys.modules, "core.llm_clients.client", fake_client)

    result = handlers.HANDLERS["brain.llm.test"](
        provider="custom",
        model="my-model",
        route_name="VISION_LLM",
        image=True,
        custom_base_url="https://api.example.test/v1",
    )

    assert result == {
        "ok": True,
        "message": "ok",
        "provider": "custom",
        "model": "my-model",
    }
    assert captured == {
        "provider": "custom",
        "model": "my-model",
        "route_name": "VISION_LLM",
        "image": True,
        "custom_base_url": "https://api.example.test/v1",
    }
