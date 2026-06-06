from __future__ import annotations

import sys
import types

from wisp_brain import handlers


def test_config_reload_handler_registered():
    assert "brain.config.reload" in handlers.HANDLERS


def test_llm_test_handler_registered():
    assert "brain.llm.test" in handlers.HANDLERS
    assert "brain.llm.test" not in handlers.STREAMING


def test_secret_handlers_registered():
    assert "brain.secrets.status" in handlers.HANDLERS
    assert "brain.secrets.set" in handlers.HANDLERS
    assert "brain.secrets.clear" in handlers.HANDLERS
    assert "brain.secrets.status" not in handlers.STREAMING


def test_auth_handlers_registered():
    assert "brain.auth.status" in handlers.HANDLERS
    assert "brain.auth.chatgpt.start_browser_login" in handlers.HANDLERS
    assert "brain.auth.chatgpt.clear" in handlers.HANDLERS
    assert "brain.auth.github.device_login" in handlers.HANDLERS
    assert "brain.auth.github.clear" in handlers.HANDLERS
    assert "brain.auth.copilot.set" in handlers.HANDLERS
    assert "brain.auth.copilot.test" in handlers.HANDLERS
    assert "brain.auth.copilot.clear" in handlers.HANDLERS
    assert "brain.auth.status" not in handlers.STREAMING
    assert "brain.auth.github.device_login" in handlers.STREAMING


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


def test_secret_status_does_not_expose_values(monkeypatch):
    from core import secret_store

    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY", "GROQ_API_KEY"))
    monkeypatch.setattr(secret_store, "has_secret", lambda name: name == "OPENAI_API_KEY")
    monkeypatch.setattr(secret_store, "secret_source", lambda name: "keychain" if name == "OPENAI_API_KEY" else "none")

    result = handlers.HANDLERS["brain.secrets.status"]()

    assert result == {
        "secrets": [
            {
                "name": "OPENAI_API_KEY",
                "label": "OpenAI",
                "configured": True,
                "source": "keychain",
            },
            {
                "name": "GROQ_API_KEY",
                "label": "Groq",
                "configured": False,
                "source": "none",
            },
        ]
    }
    assert "sk-" not in repr(result)


def test_secret_set_and_clear_call_shared_store(monkeypatch):
    from core import secret_store

    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",))

    def set_secret(name: str, value: str) -> None:
        calls.append(("set", name, value))

    def delete_secret(name: str) -> None:
        calls.append(("delete", name, None))

    monkeypatch.setattr(secret_store, "set_secret", set_secret)
    monkeypatch.setattr(secret_store, "delete_secret", delete_secret)
    monkeypatch.setattr(secret_store, "secret_source", lambda name: "keychain")

    set_result = handlers.HANDLERS["brain.secrets.set"]("openai_api_key", " sk-test ")
    clear_result = handlers.HANDLERS["brain.secrets.clear"]("OPENAI_API_KEY")

    assert calls == [
        ("set", "OPENAI_API_KEY", "sk-test"),
        ("delete", "OPENAI_API_KEY", None),
    ]
    assert set_result == {
        "ok": True,
        "name": "OPENAI_API_KEY",
        "label": "OpenAI",
        "source": "keychain",
    }
    assert clear_result == {
        "ok": True,
        "name": "OPENAI_API_KEY",
        "label": "OpenAI",
        "source": "keychain",
    }


def test_secret_set_rejects_unknown_names(monkeypatch):
    from core import secret_store

    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",))

    try:
        handlers.HANDLERS["brain.secrets.set"]("NOT_A_REAL_KEY", "value")
    except ValueError as exc:
        assert "Unknown API key name" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_auth_status_does_not_expose_tokens(monkeypatch):
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    monkeypatch.setattr(
        chatgpt_auth,
        "get_tokens",
        lambda: {"access": "secret-access-token", "account_id": "acct_123"},
    )
    monkeypatch.setattr(
        github_auth,
        "get_tokens",
        lambda: {"access": "gh-secret", "user": {"login": "octo"}},
    )
    monkeypatch.setattr(copilot_auth, "token_status", lambda: (True, "Stored in OS keychain. Token format OK."))

    result = handlers.HANDLERS["brain.auth.status"]()

    assert result == {
        "providers": [
            {
                "name": "chatgpt",
                "label": "ChatGPT",
                "configured": True,
                "message": "Logged in as acct_123",
            },
            {
                "name": "github",
                "label": "GitHub",
                "configured": True,
                "message": "Logged in as octo",
            },
            {
                "name": "copilot",
                "label": "GitHub Copilot",
                "configured": True,
                "message": "Stored in OS keychain. Token format OK.",
            },
        ]
    }
    assert "secret-access-token" not in repr(result)
    assert "gh-secret" not in repr(result)


def test_auth_clear_handlers_call_shared_modules(monkeypatch):
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    calls: list[str] = []
    monkeypatch.setattr(chatgpt_auth, "clear_tokens", lambda: calls.append("chatgpt"))
    monkeypatch.setattr(github_auth, "clear_tokens", lambda: calls.append("github"))
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: calls.append("copilot"))
    monkeypatch.setattr(copilot_auth, "token_status", lambda: (False, "Not configured"))

    assert handlers.HANDLERS["brain.auth.chatgpt.clear"]() == {"ok": True, "name": "chatgpt"}
    assert handlers.HANDLERS["brain.auth.github.clear"]() == {"ok": True, "name": "github"}
    assert handlers.HANDLERS["brain.auth.copilot.clear"]() == {
        "ok": True,
        "configured": False,
        "message": "Not configured",
    }
    assert calls == ["chatgpt", "github", "copilot"]


def test_auth_github_device_login_streams_code_and_success(monkeypatch):
    import config
    from core.auth import github as github_auth

    events = []
    monkeypatch.setattr(github_auth, "has_configured_client_id", lambda: True)

    def start_device_login(on_code, on_success, on_error):
        on_code("https://github.com/login/device", "ABCD-1234")
        on_success({"user": {"login": "octo"}})

    monkeypatch.setattr(github_auth, "start_device_login", start_device_login)

    ctx = handlers.StreamContext(lambda name, data, req_id: events.append((name, data, req_id)), req_id=42)
    result = handlers.HANDLERS["brain.auth.github.device_login"](
        ctx,
        client_id="client-123",
        scopes="repo read:user",
        timeout_seconds=1,
    )

    assert config.GITHUB_CLIENT_ID == "client-123"
    assert config.GITHUB_OAUTH_SCOPES == "repo read:user"
    assert events == [
        (
            "auth.code",
            {
                "provider": "github",
                "url": "https://github.com/login/device",
                "user_code": "ABCD-1234",
            },
            42,
        ),
        (
            "auth.done",
            {
                "ok": True,
                "provider": "github",
                "message": "Logged in as octo",
            },
            42,
        ),
    ]
    assert result == {
        "ok": True,
        "provider": "github",
        "message": "Logged in as octo",
    }


def test_auth_github_device_login_reports_missing_client(monkeypatch):
    from core.auth import github as github_auth

    monkeypatch.setattr(github_auth, "has_configured_client_id", lambda: False)
    ctx = handlers.StreamContext(lambda _name, _data, _req_id: None, req_id=1)

    try:
        handlers.HANDLERS["brain.auth.github.device_login"](ctx, client_id="", scopes="", timeout_seconds=1)
    except ValueError as exc:
        assert "GitHub OAuth app client ID" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_auth_copilot_set_and_test_call_shared_modules(monkeypatch):
    from core.auth import copilot_auth
    from core.auth import copilot_client

    calls: list[tuple[str, str | None]] = []

    def save_token(token: str) -> None:
        calls.append(("save", token))

    monkeypatch.setattr(copilot_auth, "save_token", save_token)
    monkeypatch.setattr(copilot_auth, "token_status", lambda: (True, "Stored in OS keychain."))
    monkeypatch.setattr(copilot_client, "test_copilot_token", lambda: (True, "Copilot token OK"))

    assert handlers.HANDLERS["brain.auth.copilot.set"](" github_pat_test ") == {
        "ok": True,
        "configured": True,
        "message": "Stored in OS keychain.",
    }
    assert handlers.HANDLERS["brain.auth.copilot.test"]() == {
        "ok": True,
        "message": "Copilot token OK",
    }
    assert calls == [("save", "github_pat_test")]


def test_auth_chatgpt_start_browser_login_uses_shared_module(monkeypatch):
    from core.auth import chatgpt as chatgpt_auth

    captured = {}

    def start_browser_login(on_success, on_error):
        captured["success"] = callable(on_success)
        captured["error"] = callable(on_error)

    monkeypatch.setattr(chatgpt_auth, "start_browser_login", start_browser_login)

    result = handlers.HANDLERS["brain.auth.chatgpt.start_browser_login"]()

    assert result == {"ok": True, "message": "Opening browser for ChatGPT sign-in"}
    assert captured == {"success": True, "error": True}
