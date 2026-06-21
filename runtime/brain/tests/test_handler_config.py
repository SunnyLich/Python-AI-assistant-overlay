"""Tests for macos py brain test handler config."""

from __future__ import annotations

import sys
import types
import os

from wisp_brain import handlers


def test_config_reload_handler_registered():
    """Verify config reload handler registered behavior."""
    assert "brain.config.reload" in handlers.HANDLERS


def test_llm_test_handler_registered():
    """Verify llm test handler registered behavior."""
    assert "brain.llm.test" in handlers.HANDLERS
    assert "brain.llm.test" not in handlers.STREAMING


def test_secret_handlers_registered():
    """Verify secret handlers registered behavior."""
    assert "brain.secrets.status" in handlers.HANDLERS
    assert "brain.secrets.set" in handlers.HANDLERS
    assert "brain.secrets.clear" in handlers.HANDLERS
    assert "brain.secrets.status" not in handlers.STREAMING


def test_auth_handlers_registered():
    """Verify auth handlers registered behavior."""
    assert "brain.auth.status" in handlers.HANDLERS
    assert "brain.auth.chatgpt.start_browser_login" in handlers.HANDLERS
    assert "brain.auth.chatgpt.browser_login" in handlers.HANDLERS
    assert "brain.auth.chatgpt.clear" in handlers.HANDLERS
    assert "brain.auth.github.device_login" in handlers.HANDLERS
    assert "brain.auth.github.clear" in handlers.HANDLERS
    assert "brain.auth.copilot.set" in handlers.HANDLERS
    assert "brain.auth.copilot.test" in handlers.HANDLERS
    assert "brain.auth.copilot.clear" in handlers.HANDLERS
    assert "brain.settings.reset_credentials" in handlers.HANDLERS
    assert "brain.auth.status" not in handlers.STREAMING
    assert "brain.auth.chatgpt.browser_login" in handlers.STREAMING
    assert "brain.auth.github.device_login" in handlers.STREAMING


def test_config_reload_calls_config_reload(monkeypatch):
    """Verify config reload calls config reload behavior."""
    calls: list[str] = []

    fake_config = types.ModuleType("config")
    fake_config.LLM_PROVIDER = "openai"
    fake_config.LLM_MODEL = "gpt-5.4"
    fake_config.TTS_PROVIDER = "none"

    def reload() -> None:
        """Verify reload behavior."""
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
    """Verify llm test offline seam behavior."""
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
        "routes": [
            {
                "label": "Primary",
                "ok": True,
                "provider": "openai",
                "model": "gpt-5.4",
                "message": "OK",
            }
        ],
    }


def test_llm_test_offline_reports_fallback_chain(monkeypatch):
    """Verify llm test offline reports fallback chain behavior."""
    monkeypatch.setenv("WISP_BRAIN_FAKE_LLM", "1")

    result = handlers.HANDLERS["brain.llm.test"](
        provider="openai",
        model="gpt-5.4",
        fallbacks="anthropic:claude-sonnet-4-5\ngroq:llama-3.3-70b-versatile",
        route_name="LLM",
    )

    assert result["ok"] is True
    assert result["message"] == (
        "LLM route OK:\n"
        "Primary OK: openai / gpt-5.4\n"
        "Fallback 1 OK: anthropic / claude-sonnet-4-5\n"
        "Fallback 2 OK: groq / llama-3.3-70b-versatile"
    )
    assert result["routes"] == [
        {
            "label": "Primary",
            "ok": True,
            "provider": "openai",
            "model": "gpt-5.4",
            "message": "OK",
        },
        {
            "label": "Fallback 1",
            "ok": True,
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "message": "OK",
        },
        {
            "label": "Fallback 2",
            "ok": True,
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "message": "OK",
        },
    ]


def test_llm_test_offline_vision_message(monkeypatch):
    """Verify llm test offline vision message behavior."""
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
    """Verify llm test requires provider and model behavior."""
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
        "routes": [],
    }


def test_llm_test_forwards_route_to_client(monkeypatch):
    """Verify llm test forwards route to client behavior."""
    captured = {}
    fake_client = types.ModuleType("core.llm_clients.client")

    def fake_test_route_connection(provider, model, route_name, *, image=False, custom_base_url=None):
        """Verify fake route connection behavior."""
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
        "routes": [
            {
                "label": "Primary",
                "ok": True,
                "provider": "custom",
                "model": "my-model",
                "message": "ok",
            }
        ],
    }
    assert captured == {
        "provider": "custom",
        "model": "my-model",
        "route_name": "VISION_LLM",
        "image": True,
        "custom_base_url": "https://api.example.test/v1",
    }


def test_llm_test_forwards_fallback_chain_to_client(monkeypatch):
    """Verify llm test forwards fallback chain to client behavior."""
    calls = []
    fake_client = types.ModuleType("core.llm_clients.client")

    def fake_test_route_connection(provider, model, route_name, *, image=False, custom_base_url=None):
        """Verify fake route connection behavior."""
        calls.append((provider, model, route_name, image, custom_base_url))
        if provider == "groq":
            return False, f"{route_name} test failed: no key"
        return True, f"{route_name} route OK: {provider} / {model}"

    fake_client.test_route_connection = fake_test_route_connection
    monkeypatch.setitem(sys.modules, "core.llm_clients.client", fake_client)

    result = handlers.HANDLERS["brain.llm.test"](
        provider="openai",
        model="gpt-5.4",
        fallbacks="anthropic:claude-sonnet-4-5\ngroq:llama-3.3-70b-versatile",
        route_name="LLM",
        custom_base_url="https://api.example.test/v1",
    )

    assert result["ok"] is False
    assert result["message"] == (
        "LLM route chain failed:\n"
        "Primary - openai / gpt-5.4: OK\n"
        "Fallback 1 - anthropic / claude-sonnet-4-5: OK\n"
        "Fallback 2 - groq / llama-3.3-70b-versatile: no key"
    )
    assert calls == [
        ("openai", "gpt-5.4", "LLM", False, None),
        ("anthropic", "claude-sonnet-4-5", "LLM", False, None),
        ("groq", "llama-3.3-70b-versatile", "LLM", False, None),
    ]


def test_llm_test_scopes_custom_base_url_to_custom_provider(monkeypatch):
    """Verify llm test scopes custom base url to custom provider behavior."""
    calls = []
    fake_client = types.ModuleType("core.llm_clients.client")

    def fake_test_route_connection(provider, model, route_name, *, image=False, custom_base_url=None):
        """Verify fake route connection behavior."""
        calls.append((provider, model, route_name, image, custom_base_url))
        return True, f"{route_name} route OK: {provider} / {model}"

    fake_client.test_route_connection = fake_test_route_connection
    monkeypatch.setitem(sys.modules, "core.llm_clients.client", fake_client)

    result = handlers.HANDLERS["brain.llm.test"](
        provider="custom",
        model="custom-model",
        fallbacks="openai:gpt-4.1\nanthropic:claude-sonnet-4-5",
        route_name="LLM",
        custom_base_url="https://api.example.test/v1",
    )

    assert result["ok"] is True
    assert calls == [
        ("custom", "custom-model", "LLM", False, "https://api.example.test/v1"),
        ("openai", "gpt-4.1", "LLM", False, None),
        ("anthropic", "claude-sonnet-4-5", "LLM", False, None),
    ]


def test_secret_status_does_not_expose_values(monkeypatch):
    """Verify secret status does not expose values behavior."""
    from core import secret_store

    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY", "GROQ_API_KEY"))
    monkeypatch.setattr(secret_store, "get_secret", lambda name: "sk-test" if name == "OPENAI_API_KEY" else "")
    monkeypatch.setattr(secret_store, "configured_marker", lambda name: name == "OPENAI_API_KEY")
    monkeypatch.setattr(secret_store, "secret_source", lambda name: "keychain" if name == "OPENAI_API_KEY" else "none")

    result = handlers.HANDLERS["brain.secrets.status"]()

    assert result == {
        "secrets": [
            {
                "name": "OPENAI_API_KEY",
                "label": "OpenAI",
                "configured": True,
                "available": True,
                "source": "keychain",
            },
            {
                "name": "GROQ_API_KEY",
                "label": "Groq",
                "configured": False,
                "available": False,
                "source": "none",
            },
        ]
    }
    assert "sk-" not in repr(result)


def test_secret_set_and_clear_call_shared_store(monkeypatch):
    """Verify secret set and clear call shared store behavior."""
    from core import secret_store

    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",))

    def set_secret(name: str, value: str) -> None:
        """Verify set secret behavior."""
        calls.append(("set", name, value))

    def delete_secret(name: str) -> None:
        """Verify delete secret behavior."""
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
    """Verify secret set rejects unknown names behavior."""
    from core import secret_store

    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",))

    try:
        handlers.HANDLERS["brain.secrets.set"]("NOT_A_REAL_KEY", "value")
    except ValueError as exc:
        assert "Unknown API key name" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_auth_status_does_not_expose_tokens(monkeypatch):
    """Verify auth status does not expose tokens behavior."""
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
    """Verify auth clear handlers call shared modules behavior."""
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
    """Verify auth github device login streams code and success behavior."""
    import config
    from core.auth import github as github_auth

    events = []
    monkeypatch.setattr(github_auth, "has_configured_client_id", lambda: True)

    def start_device_login(on_code, on_success, on_error):
        """Verify start device login behavior."""
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
    """Verify auth github device login reports missing client behavior."""
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
    """Verify auth copilot set and test call shared modules behavior."""
    from core.auth import copilot_auth
    from core.auth import copilot_client

    calls: list[tuple[str, str | None]] = []

    def save_token(token: str) -> None:
        """Verify save token behavior."""
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
    """Verify auth chatgpt start browser login uses shared module behavior."""
    from core.auth import chatgpt as chatgpt_auth

    captured = {}

    def start_browser_login(on_success, on_error):
        """Verify start browser login behavior."""
        captured["success"] = callable(on_success)
        captured["error"] = callable(on_error)

    monkeypatch.setattr(chatgpt_auth, "start_browser_login", start_browser_login)

    result = handlers.HANDLERS["brain.auth.chatgpt.start_browser_login"]()

    assert result == {"ok": True, "message": "Opening browser for ChatGPT sign-in"}
    assert captured == {"success": True, "error": True}


def test_auth_chatgpt_browser_login_streams_started_and_success(monkeypatch):
    """Verify auth chatgpt browser login streams started and success behavior."""
    from core.auth import chatgpt as chatgpt_auth

    events = []

    def start_browser_login(on_success, on_error):
        """Verify start browser login behavior."""
        on_success({"account_id": "acct_123"})

    monkeypatch.setattr(chatgpt_auth, "start_browser_login", start_browser_login)

    ctx = handlers.StreamContext(lambda name, data, req_id: events.append((name, data, req_id)), req_id=7)
    result = handlers.HANDLERS["brain.auth.chatgpt.browser_login"](ctx, timeout_seconds=1)

    assert events == [
        (
            "auth.started",
            {
                "provider": "chatgpt",
                "message": "Opening browser for ChatGPT sign-in",
            },
            7,
        ),
        (
            "auth.done",
            {
                "ok": True,
                "provider": "chatgpt",
                "message": "Logged in as acct_123",
            },
            7,
        ),
    ]
    assert result == {
        "ok": True,
        "provider": "chatgpt",
        "message": "Logged in as acct_123",
    }


def test_auth_chatgpt_browser_login_streams_error(monkeypatch):
    """Verify auth chatgpt browser login streams error behavior."""
    from core.auth import chatgpt as chatgpt_auth

    events = []

    def start_browser_login(on_success, on_error):
        """Verify start browser login behavior."""
        on_error("browser failed")

    monkeypatch.setattr(chatgpt_auth, "start_browser_login", start_browser_login)

    ctx = handlers.StreamContext(lambda name, data, req_id: events.append((name, data, req_id)), req_id=8)
    result = handlers.HANDLERS["brain.auth.chatgpt.browser_login"](ctx, timeout_seconds=1)

    assert events[0] == (
        "auth.started",
        {
            "provider": "chatgpt",
            "message": "Opening browser for ChatGPT sign-in",
        },
        8,
    )
    assert events[1] == (
        "auth.error",
        {
            "ok": False,
            "provider": "chatgpt",
            "message": "browser failed",
        },
        8,
    )
    assert result == {
        "ok": False,
        "provider": "chatgpt",
        "message": "browser failed",
    }


def test_settings_reset_credentials_clears_secrets_auth_and_env(monkeypatch, tmp_path):
    """Verify settings reset credentials clears secrets auth and env behavior."""
    from core import secret_store
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    calls: list[str] = []
    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY", "GROQ_API_KEY"))
    monkeypatch.setattr(secret_store, "delete_secret", lambda name: calls.append(f"secret:{name}"))
    monkeypatch.setattr(chatgpt_auth, "clear_tokens", lambda: calls.append("chatgpt"))
    monkeypatch.setattr(github_auth, "clear_tokens", lambda: calls.append("github"))
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: calls.append("copilot"))

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=env-openai\nLLM_PROVIDER=anthropic\nTHEME_MODE=dark\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("THEME_MODE", "dark")

    fake_config = types.ModuleType("config")
    fake_config._ENV_FILE = env_file
    fake_config.reload = lambda: calls.append("reload")
    monkeypatch.setitem(sys.modules, "config", fake_config)

    result = handlers.HANDLERS["brain.settings.reset_credentials"]()

    assert result == {
        "ok": True,
        "cleared": [
            "OPENAI_API_KEY",
            "GROQ_API_KEY",
            "ChatGPT",
            "GitHub",
            "GitHub Copilot",
        ],
        "failures": [],
    }
    assert calls == [
        "secret:OPENAI_API_KEY",
        "secret:GROQ_API_KEY",
        "chatgpt",
        "github",
        "copilot",
        "reload",
    ]
    assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert os.environ["THEME_MODE"] == "dark"
    assert "OPENAI_API_KEY" not in env_file.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=anthropic" in env_file.read_text(encoding="utf-8")


def test_settings_reset_credentials_collects_failures(monkeypatch, tmp_path):
    """Verify settings reset credentials collects failures behavior."""
    from core import secret_store
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    monkeypatch.setattr(secret_store, "API_KEY_NAMES", ("OPENAI_API_KEY",))

    def fail_secret(_name: str) -> None:
        """Verify fail secret behavior."""
        raise RuntimeError("keychain denied")

    monkeypatch.setattr(secret_store, "delete_secret", fail_secret)
    monkeypatch.setattr(chatgpt_auth, "clear_tokens", lambda: None)
    monkeypatch.setattr(github_auth, "clear_tokens", lambda: None)
    monkeypatch.setattr(copilot_auth, "clear_token", lambda: None)

    fake_config = types.ModuleType("config")
    fake_config._ENV_FILE = tmp_path / ".env"
    fake_config.reload = lambda: None
    monkeypatch.setitem(sys.modules, "config", fake_config)

    result = handlers.HANDLERS["brain.settings.reset_credentials"]()

    assert result["ok"] is False
    assert result["cleared"] == ["ChatGPT", "GitHub", "GitHub Copilot"]
    assert result["failures"] == ["OPENAI_API_KEY: keychain denied"]
