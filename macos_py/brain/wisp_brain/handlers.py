"""
wisp_brain.handlers — methods that execute INSIDE the brain worker.

Each entry in ``HANDLERS`` maps a protocol ``method`` to a callable. Methods in
``STREAMING`` receive a ``StreamContext`` as their first positional argument and
may push ``reply.chunk``-style events (tagged with the request id) before they
return their final result; everything else is a plain unary call whose return
value becomes the response ``result``.

Heavy / OS-agnostic brain modules (``core.query_pipeline``,
``core.llm_clients.client``, faster-whisper, ...) are imported LAZILY inside the
handlers, never at module import, so the brain worker boots and can answer ``ping`` on
any platform with no API keys or models present. That is what lets this file be
tested from Windows/CI without the LLM stack.
"""
from __future__ import annotations

import json
import os
import ast
import importlib
import threading
import time
import uuid
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator

# Keep optional-dependency chatter off the protocol channel's stderr mirror.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

HANDLERS: dict[str, Callable[..., Any]] = {}
STREAMING: set[str] = set()
_STT_MODEL = None
_AGENT_APPROVALS: dict[str, dict[str, Any]] = {}
_AGENT_APPROVALS_LOCK = threading.Lock()


class StreamContext:
    """Passed to streaming handlers; ``emit`` tags events with the request id so
    the host can route partial output back to the originating call."""

    __slots__ = ("_emit", "req_id", "cancelled")

    def __init__(self, emit: Callable[[str, Any, Any], None], req_id: Any) -> None:
        self._emit = emit          # (event_name, data, req_id) -> None
        self.req_id = req_id
        self.cancelled = False

    def emit(self, event: str, data: Any = None) -> None:
        self._emit(event, data, self.req_id)


def handler(name: str, *, streaming: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        HANDLERS[name] = fn
        if streaming:
            STREAMING.add(name)
        return fn
    return deco


def _log(msg: str) -> None:
    print(f"[brain] {msg}", flush=True)  # -> stderr (host redirects fd 1 to fd 2)


def _runtime_output_dir() -> Path:
    """Directory for large brain-worker artifacts returned by path over IPC."""
    run_log_dir = os.getenv("WISP_RUN_LOG_DIR")
    if run_log_dir:
        out = Path(run_log_dir)
    else:
        import tempfile
        out = Path(tempfile.gettempdir()) / "wisp-brain"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Offline / deterministic seams (tests + dev smoke; OFF in production).
#
# These are guarded by environment variables that are unset in the shipped app,
# so the real LLM / TTS paths run normally on the Mac. When set, the brain
# produces deterministic output with no network or models, which is what lets
# the full handler set -- including ``brain.query`` and ``brain.agent.run`` --
# be exercised end-to-end from Windows/CI. This mirrors the existing
# ``WISP_MACOS_*`` env-flag style used elsewhere in the codebase.
# ---------------------------------------------------------------------------

def _offline_brain() -> bool:
    """True when the brain should answer deterministically without network/models.

    Driven by ``WISP_BRAIN_FAKE_LLM`` (any non-empty value). Used by the query,
    tts, and agent handlers so an off-Mac integration run never touches a real
    provider, model, or API key.
    """
    return bool(os.getenv("WISP_BRAIN_FAKE_LLM"))


def _write_silent_wav(path: Path, *, sample_rate: int = 22_050, milliseconds: int = 120) -> int:
    """Write a mono int16 silent WAV and return the byte count of its frames."""
    frames = max(1, int(sample_rate * milliseconds / 1000))
    silence = b"\x00\x00" * frames
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence)
    return len(silence)


def _agent_test_model_callback() -> Callable[[str], str] | None:
    """Return a model callback that drives the agent loop without a real LLM.

    Resolution order:
      1. ``WISP_BRAIN_AGENT_TEST_SCRIPT`` -> path to a JSON array of agent
         response strings (or objects). Each model turn pops the next entry; once
         exhausted the agent is told to finish. This lets a test script multi-turn
         tool-call behavior deterministically.
      2. ``WISP_BRAIN_FAKE_LLM`` -> single-turn callback that immediately returns a
         valid ``final`` agent response, so the loop completes in one turn offline.
      3. Otherwise ``None`` -> the runner calls the configured provider (production).
    """
    script_path = os.getenv("WISP_BRAIN_AGENT_TEST_SCRIPT")
    if script_path:
        raw = json.loads(Path(script_path).read_text(encoding="utf-8"))
        responses = [r if isinstance(r, str) else json.dumps(r) for r in raw]
        index = {"i": 0}
        lock = threading.Lock()
        done = json.dumps({"thought": "script exhausted", "final": "Done.", "tool_calls": []})

        def scripted(_prompt: str) -> str:
            with lock:
                i = index["i"]
                index["i"] = i + 1
            return responses[i] if i < len(responses) else done

        return scripted

    if _offline_brain():
        canned = json.dumps(
            {"thought": "fake offline run", "final": "Fake agent run complete.", "tool_calls": []}
        )

        def fake(_prompt: str) -> str:
            return canned

        return fake

    return None


# ---------------------------------------------------------------------------
# Diagnostics (no heavy imports -- always available)
# ---------------------------------------------------------------------------

@handler("ping")
def ping(value: Any = None) -> dict[str, Any]:
    """Liveness / round-trip check. Echoes *value* and reports the brain worker pid."""
    return {"pong": True, "value": value, "pid": os.getpid()}


@handler("brain.config.reload")
def brain_config_reload() -> dict[str, Any]:
    """Reload .env-backed Python config after the native Settings panel saves."""
    import config

    config.reload()
    # Drop cached TTS connections so brain.tts.test reconnects under the new
    # provider/voice/key instead of reusing a socket from the old settings.
    try:
        importlib.import_module("core.tts").reset_connections()
    except Exception:  # noqa: BLE001 — best effort; never block a config reload
        pass
    return {
        "ok": True,
        "llm_provider": getattr(config, "LLM_PROVIDER", ""),
        "llm_model": getattr(config, "LLM_MODEL", ""),
        "tts_provider": getattr(config, "TTS_PROVIDER", ""),
    }


_SECRET_LABELS = {
    "GROQ_API_KEY": "Groq",
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic",
    "GOOGLE_API_KEY": "Google",
    "CARTESIA_API_KEY": "Cartesia",
    "ELEVENLABS_API_KEY": "ElevenLabs",
    "CUSTOM_API_KEY": "Custom provider",
    "DEEPSEEK_API_KEY": "DeepSeek",
    "OPENROUTER_API_KEY": "OpenRouter",
    "MISTRAL_API_KEY": "Mistral",
    "XAI_API_KEY": "xAI",
    "TOGETHER_API_KEY": "Together",
    "CEREBRAS_API_KEY": "Cerebras",
}


def _secret_name(raw: str) -> str:
    from core import secret_store

    name = (raw or "").strip().upper()
    if name not in secret_store.API_KEY_NAMES:
        raise ValueError(f"Unknown API key name: {raw}")
    return name


@handler("brain.secrets.status")
def brain_secrets_status() -> dict[str, Any]:
    """Return API-key presence/source metadata without exposing secret values."""
    from core import secret_store

    secrets = []
    for name in secret_store.API_KEY_NAMES:
        available = bool(secret_store.get_secret(name))
        secrets.append(
            {
                "name": name,
                "label": _SECRET_LABELS.get(name, name),
                "configured": available or secret_store.configured_marker(name),
                "available": available,
                "source": secret_store.secret_source(name),
            }
        )
    return {"secrets": secrets}


@handler("brain.secrets.set")
def brain_secrets_set(name: str = "", value: str = "") -> dict[str, Any]:
    """Save one API key through the shared OS keychain secret store."""
    from core import secret_store

    key_name = _secret_name(name)
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("value is required")

    secret_store.set_secret(key_name, cleaned)
    return {
        "ok": True,
        "name": key_name,
        "label": _SECRET_LABELS.get(key_name, key_name),
        "source": secret_store.secret_source(key_name),
    }


@handler("brain.secrets.clear")
def brain_secrets_clear(name: str = "") -> dict[str, Any]:
    """Clear one API key through the shared OS keychain secret store."""
    from core import secret_store

    key_name = _secret_name(name)
    secret_store.delete_secret(key_name)
    return {
        "ok": True,
        "name": key_name,
        "label": _SECRET_LABELS.get(key_name, key_name),
        "source": secret_store.secret_source(key_name),
    }


@handler("brain.auth.status")
def brain_auth_status() -> dict[str, Any]:
    """Return OAuth/token-auth provider status without exposing credentials."""
    from core.auth import chatgpt as chatgpt_auth
    from core.auth import copilot_auth
    from core.auth import github as github_auth

    chatgpt_tokens = chatgpt_auth.get_tokens()
    chatgpt_account = ""
    if isinstance(chatgpt_tokens, dict):
        chatgpt_account = str(chatgpt_tokens.get("account_id") or "")

    github_tokens = github_auth.get_tokens()
    github_login = ""
    if isinstance(github_tokens, dict):
        user = github_tokens.get("user")
        if isinstance(user, dict):
            github_login = str(user.get("login") or "")

    try:
        copilot_configured, copilot_message = copilot_auth.token_status()
    except Exception as exc:  # noqa: BLE001 - shown as status, never fatal
        copilot_configured = False
        copilot_message = f"Keychain error: {exc}"

    return {
        "providers": [
            {
                "name": "chatgpt",
                "label": "ChatGPT",
                "configured": bool(chatgpt_tokens),
                "message": "Logged in" + (f" as {chatgpt_account}" if chatgpt_account else "")
                if chatgpt_tokens
                else "Not logged in",
            },
            {
                "name": "github",
                "label": "GitHub",
                "configured": bool(github_tokens),
                "message": "Logged in" + (f" as {github_login}" if github_login else "")
                if github_tokens
                else "Not logged in",
            },
            {
                "name": "copilot",
                "label": "GitHub Copilot",
                "configured": bool(copilot_configured),
                "message": copilot_message,
            },
        ]
    }


@handler("brain.auth.chatgpt.start_browser_login")
def brain_auth_chatgpt_start_browser_login() -> dict[str, Any]:
    """Start ChatGPT browser OAuth through the shared auth module."""
    from core.auth import chatgpt as chatgpt_auth

    def on_success(_tokens: dict) -> None:
        _log("chatgpt login complete")

    def on_error(message: str) -> None:
        _log(f"chatgpt login error: {message}")

    chatgpt_auth.start_browser_login(on_success=on_success, on_error=on_error)
    return {"ok": True, "message": "Opening browser for ChatGPT sign-in"}


@handler("brain.auth.chatgpt.browser_login", streaming=True)
def brain_auth_chatgpt_browser_login(
    ctx: StreamContext,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run ChatGPT browser OAuth and stream completion back to the supervisor."""
    from core.auth import chatgpt as chatgpt_auth

    done = threading.Event()
    result: dict[str, Any] = {
        "ok": False,
        "provider": "chatgpt",
        "message": "ChatGPT sign-in did not finish.",
    }

    def on_success(tokens: dict) -> None:
        account = tokens.get("account_id") if isinstance(tokens, dict) else ""
        result.update({
            "ok": True,
            "provider": "chatgpt",
            "message": "Logged in" + (f" as {account}" if account else ""),
        })
        ctx.emit("auth.done", result)
        done.set()

    def on_error(message: str) -> None:
        result.update({"ok": False, "provider": "chatgpt", "message": message})
        ctx.emit("auth.error", result)
        done.set()

    ctx.emit("auth.started", {"provider": "chatgpt", "message": "Opening browser for ChatGPT sign-in"})
    chatgpt_auth.start_browser_login(on_success=on_success, on_error=on_error)
    deadline = time.time() + max(1, int(timeout_seconds or 300))
    while not done.wait(0.25):
        if ctx.cancelled:
            return {"ok": False, "provider": "chatgpt", "cancelled": True, "message": "ChatGPT sign-in cancelled"}
        if time.time() >= deadline:
            result.update({"ok": False, "provider": "chatgpt", "message": "Timed out waiting for ChatGPT login"})
            ctx.emit("auth.error", result)
            break
    return result


@handler("brain.auth.chatgpt.clear")
def brain_auth_chatgpt_clear() -> dict[str, Any]:
    from core.auth import chatgpt as chatgpt_auth

    chatgpt_auth.clear_tokens()
    return {"ok": True, "name": "chatgpt"}


@handler("brain.auth.github.clear")
def brain_auth_github_clear() -> dict[str, Any]:
    from core.auth import github as github_auth

    github_auth.clear_tokens()
    return {"ok": True, "name": "github"}


@handler("brain.auth.github.device_login", streaming=True)
def brain_auth_github_device_login(
    ctx: StreamContext,
    client_id: str = "",
    scopes: str = "",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """Run GitHub device auth and stream the verification code to the supervisor."""
    import config
    from core.auth import github as github_auth

    config.GITHUB_CLIENT_ID = (client_id or "").strip() or getattr(config, "GITHUB_DEFAULT_CLIENT_ID", "")
    config.GITHUB_OAUTH_SCOPES = (scopes or "").strip()
    if not github_auth.has_configured_client_id():
        raise ValueError("This build does not include a GitHub OAuth app client ID yet.")

    done = threading.Event()
    result: dict[str, Any] = {"ok": False, "message": "GitHub sign-in did not finish."}

    def on_code(url: str, user_code: str) -> None:
        ctx.emit("auth.code", {"provider": "github", "url": url, "user_code": user_code})

    def on_success(tokens: dict) -> None:
        user = tokens.get("user") if isinstance(tokens, dict) else {}
        login = user.get("login") if isinstance(user, dict) else ""
        result.update({
            "ok": True,
            "provider": "github",
            "message": "Logged in" + (f" as {login}" if login else ""),
        })
        ctx.emit("auth.done", result)
        done.set()

    def on_error(message: str) -> None:
        result.update({"ok": False, "provider": "github", "message": message})
        ctx.emit("auth.error", result)
        done.set()

    github_auth.start_device_login(on_code, on_success, on_error)
    deadline = time.time() + max(1, int(timeout_seconds or 900))
    while not done.wait(0.25):
        if ctx.cancelled:
            return {"ok": False, "provider": "github", "cancelled": True, "message": "GitHub sign-in cancelled"}
        if time.time() >= deadline:
            result.update({"ok": False, "provider": "github", "message": "Timed out waiting for GitHub login"})
            ctx.emit("auth.error", result)
            break
    return result


@handler("brain.auth.copilot.set")
def brain_auth_copilot_set(token: str = "") -> dict[str, Any]:
    from core.auth import copilot_auth

    copilot_auth.save_token((token or "").strip())
    configured, message = copilot_auth.token_status()
    return {"ok": True, "configured": configured, "message": message}


@handler("brain.auth.copilot.test")
def brain_auth_copilot_test() -> dict[str, Any]:
    from core.auth import copilot_client

    ok, message = copilot_client.test_copilot_token()
    return {"ok": ok, "message": message}


@handler("brain.auth.copilot.clear")
def brain_auth_copilot_clear() -> dict[str, Any]:
    from core.auth import copilot_auth

    copilot_auth.clear_token()
    configured, message = copilot_auth.token_status()
    return {"ok": True, "configured": configured, "message": message}


@handler("brain.settings.reset_credentials")
def brain_settings_reset_credentials() -> dict[str, Any]:
    """Clear shared credential stores during native Settings factory reset."""
    import os

    from core import secret_store

    cleared: list[str] = []
    failures: list[str] = []
    for name in secret_store.API_KEY_NAMES:
        try:
            secret_store.delete_secret(name)
            cleared.append(name)
        except Exception as exc:  # noqa: BLE001 - surfaced in reset summary
            failures.append(f"{name}: {exc}")

    for label, module_path, function_name in (
        ("ChatGPT", "core.auth.chatgpt", "clear_tokens"),
        ("GitHub", "core.auth.github", "clear_tokens"),
        ("GitHub Copilot", "core.auth.copilot_auth", "clear_token"),
    ):
        try:
            import importlib

            getattr(importlib.import_module(module_path), function_name)()
            cleared.append(label)
        except Exception as exc:  # noqa: BLE001 - surfaced in reset summary
            failures.append(f"{label}: {exc}")

    try:
        import config
        from core.system.env_utils import read_env_file

        for key in read_env_file(getattr(config, "_ENV_FILE")):
            os.environ.pop(key, None)

        config.reload()
    except Exception as exc:  # noqa: BLE001 - reset already cleared credentials
        failures.append(f"config reload: {exc}")

    return {"ok": not failures, "cleared": cleared, "failures": failures}


@handler("brain.plugins.list")
def brain_plugins_list() -> dict[str, Any]:
    """Return loaded/discoverable addons for the Python macOS addon manager."""
    from core.system.paths import ADDONS_DIR

    plugins_dir = Path(ADDONS_DIR)
    return {
        "plugins_dir": str(plugins_dir),
        "plugins": _plugin_summaries(plugins_dir),
    }


@handler("brain.plugins.run_action")
def brain_plugins_run_action(plugin_name: str = "", label: str = "") -> dict[str, Any]:
    """Run a loaded addon tray action by addon name/id and label."""
    plugin_name = plugin_name.strip()
    label = label.strip()
    if not plugin_name:
        raise ValueError("plugin_name is required")
    if not label:
        raise ValueError("label is required")

    from core.system.paths import ADDONS_DIR

    manager = _loaded_plugin_manager(Path(ADDONS_DIR))
    manager.run_tray_action(plugin_name, label)
    return {"ok": True, "message": f"Ran addon action: {plugin_name} / {label}"}


@handler("brain.plugins.set_enabled")
def brain_plugins_set_enabled(plugin_name: str = "", enabled: bool = True) -> dict[str, Any]:
    """Enable or disable a loaded addon; persists to addons.json and applies live."""
    plugin_name = plugin_name.strip()
    if not plugin_name:
        raise ValueError("plugin_name is required")
    from core.system.paths import ADDONS_DIR

    manager = _loaded_plugin_manager(Path(ADDONS_DIR))
    state = manager.set_enabled(plugin_name, bool(enabled))
    return {"ok": True, "name": plugin_name, "enabled": bool(state)}


@handler("brain.plugins.set_setting")
def brain_plugins_set_setting(plugin_name: str = "", key: str = "", value: Any = "") -> dict[str, Any]:
    """Persist a single addon setting value to addons.json."""
    plugin_name = plugin_name.strip()
    key = str(key).strip()
    if not plugin_name:
        raise ValueError("plugin_name is required")
    if not key:
        raise ValueError("key is required")
    from core.system.paths import ADDONS_DIR

    manager = _loaded_plugin_manager(Path(ADDONS_DIR))
    manager.set_setting(plugin_name, key, value)
    return {"ok": True, "name": plugin_name, "key": key}


def _plugin_summaries(plugins_dir: Path) -> list[dict[str, Any]]:
    try:
        manager = _loaded_plugin_manager(plugins_dir)
        if hasattr(manager, "summaries"):
            return manager.summaries()
        mods = getattr(manager, "_mods", [])
        return [_loaded_plugin_payload(mod, manager) for mod in mods]
    except Exception:
        return _discover_plugin_payloads(plugins_dir)


def _loaded_plugin_manager(plugins_dir: Path) -> Any:
    plugin_manager = importlib.import_module("core.plugin_manager")

    try:
        return plugin_manager.get_manager()
    except Exception:
        return plugin_manager.init(plugins_dir)


_plugin_startup_done = False


def run_plugin_startup() -> None:
    """Fire plugin ``on_startup`` once and register their ``get_tools``.

    The legacy ``main.py`` ran this at app init; the headless brain must do it
    too, or plugin model-tools never reach the LLM and ``on_startup`` never runs.
    Called lazily from the query path (not at boot) to keep the brain's ping-only
    startup free of the LLM stack. ``signals`` is ``None`` here — there is no Qt in
    the brain worker; plugins drive the UI via tray actions / protocol, not Qt
    signals. Idempotent and best-effort.
    """
    global _plugin_startup_done
    if _plugin_startup_done:
        return
    _plugin_startup_done = True
    try:
        import config
        from core.system.paths import ADDONS_DIR
        from core.llm_clients.client import get_tool_registry

        plugin_manager = importlib.import_module("core.plugin_manager")
        try:
            manager = plugin_manager.get_manager()
        except Exception:
            manager = plugin_manager.init(Path(ADDONS_DIR))
        manager.on_startup(
            plugin_manager.AppContext(
                signals=None,
                model_tool_registry=get_tool_registry(),
                config=config,
            )
        )
    except Exception as exc:  # noqa: BLE001 - plugin startup must not block the brain
        _log(f"plugin startup skipped: {type(exc).__name__}: {exc}")


def run_plugin_shutdown() -> None:
    """Fire plugin ``on_shutdown`` if plugins were started. Called by the host on
    exit. Best-effort; no-op when no plugins were ever loaded."""
    try:
        plugin_manager = importlib.import_module("core.plugin_manager")
        manager = plugin_manager.get_manager()  # raises if never initialised
    except Exception:
        return
    try:
        manager.on_shutdown()
    except Exception as exc:  # noqa: BLE001
        _log(f"plugin shutdown skipped: {type(exc).__name__}: {exc}")


def _loaded_plugin_payload(mod: Any, manager: Any = None) -> dict[str, Any]:
    module = getattr(mod, "module", None)
    path = getattr(module, "__file__", "") or ""
    hooks = _plugin_hook_names(module)
    name = str(getattr(mod, "name", ""))
    settings: list[dict[str, Any]] = []
    if manager is not None:
        try:
            settings = manager.get_settings(name)
        except Exception:
            settings = []
    return {
        "name": name,
        "path": str(Path(path).parent) if path else "",
        "status": "loaded",
        "enabled": bool(getattr(mod, "enabled", True)),
        "hooks": hooks,
        "tray_actions": _safe_tray_action_labels(module),
        "tools": _safe_tool_names(module),
        "settings": settings,
        "error": "",
    }


def _discover_plugin_payloads(plugins_dir: Path) -> list[dict[str, Any]]:
    if not plugins_dir.exists():
        return []

    payloads: list[dict[str, Any]] = []
    for child in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        init_path = child / "__init__.py"
        manifest_path = child / "addon.toml"
        if not init_path.exists() and not manifest_path.exists():
            continue
        hooks = _declared_hook_names(init_path) if init_path.exists() else []
        payloads.append({
            "id": child.name,
            "name": child.name,
            "path": str(child),
            "status": "discovered",
            "enabled": True,
            "hooks": hooks,
            "tray_actions": [],
            "tools": [],
            "settings": [],
            "permissions": {},
            "description": "",
            "error": "",
        })
    return payloads


def _plugin_hook_names(module: Any) -> list[str]:
    return [
        hook
        for hook in _PLUGIN_HOOKS
        if module is not None and hasattr(module, hook)
    ]


def _declared_hook_names(init_path: Path) -> list[str]:
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    except Exception:
        return []
    declared = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return [hook for hook in _PLUGIN_HOOKS if hook in declared]


def _safe_tray_action_labels(module: Any) -> list[str]:
    fn = getattr(module, "get_tray_actions", None)
    if not callable(fn):
        return []
    try:
        items = fn()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [
        str(item.get("label", "Action"))
        for item in items
        if isinstance(item, dict)
    ]


def _safe_tool_names(module: Any) -> list[str]:
    fn = getattr(module, "get_tools", None)
    if not callable(fn):
        return []
    try:
        items = fn()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [
        str(item.get("name", "?"))
        for item in items
        if isinstance(item, dict)
    ]


_PLUGIN_HOOKS = (
    "on_startup",
    "on_shutdown",
    "before_query",
    "after_response",
    "get_tools",
    "get_tray_actions",
    "get_settings",
    "get_system_prompt_section",
)


@handler("brain.echo", streaming=True)
def brain_echo(ctx: StreamContext, text: str = "", chunk_size: int = 1, delay: float = 0.0) -> dict[str, Any]:
    """Stream *text* back word-by-word as ``reply.chunk`` events, then return the
    whole string. Pure-Python, no models or network -- this is the streaming
    handshake the Phase-1 test exercises to prove event correlation works."""
    words = text.split(" ") if text else []
    sent: list[str] = []
    for i in range(0, len(words), max(1, chunk_size)):
        if ctx.cancelled:
            break
        piece = " ".join(words[i:i + max(1, chunk_size)])
        if i + max(1, chunk_size) < len(words):
            piece += " "
        sent.append(piece)
        ctx.emit("reply.chunk", {"text": piece})
        if delay:
            time.sleep(delay)
    full = "".join(sent)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


# ---------------------------------------------------------------------------
# Audio model endpoints -- The audio worker owns playback; the brain only reads/writes paths.
# ---------------------------------------------------------------------------

@handler("brain.transcribe")
def brain_transcribe(pcm_path: str = "", language: str | None = None) -> dict[str, Any]:
    """Transcribe a WAV/audio file recorded by the audio worker.

    This deliberately does NOT import ``core.stt`` because that module still owns
    legacy sounddevice recording. The native shell has already captured audio;
    the brain worker only loads faster-whisper and transcribes a normalized numpy
    array. Large PCM never crosses IPC, only ``pcm_path``.
    """
    if not pcm_path:
        raise ValueError("pcm_path is required")

    import numpy as np
    import soundfile as sf
    import config

    data, sample_rate = sf.read(pcm_path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if sample_rate != 16_000:
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(sample_rate), 16_000)
            data = resample_poly(data, 16_000 // g, int(sample_rate) // g).astype("float32")
        except Exception:
            # Linear fallback keeps the handler usable if scipy is missing.
            duration = len(data) / float(sample_rate)
            src_x = np.linspace(0.0, duration, num=len(data), endpoint=False)
            dst_len = max(1, int(duration * 16_000))
            dst_x = np.linspace(0.0, duration, num=dst_len, endpoint=False)
            data = np.interp(dst_x, src_x, data).astype("float32")

    if len(data) < 16_000 * 0.25:
        return {"text": "", "duration": len(data) / 16_000, "reason": "too_short"}

    global _STT_MODEL
    if _STT_MODEL is None:
        from faster_whisper import WhisperModel
        _STT_MODEL = WhisperModel(
            config.STT_MODEL,
            device="cpu",
            compute_type=config.STT_COMPUTE_TYPE,
        )
        _log(f"loaded STT model {config.STT_MODEL!r}")
    model = _STT_MODEL
    segments, _info = model.transcribe(
        data,
        beam_size=1,
        language=language or config.STT_LANGUAGE or None,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    _log(f"transcribed {pcm_path!r}: {text!r}")
    return {"text": text, "duration": len(data) / 16_000}


@handler("brain.tts.synthesize")
def brain_tts_synthesize(text: str = "", voice: str | None = None) -> dict[str, Any]:
    """Synthesize text to a standard int16 WAV file for audio-worker playback."""
    if not text.strip():
        raise ValueError("text is required")

    if _offline_brain():
        out_path = _runtime_output_dir() / f"tts-{int(time.time() * 1000)}.wav"
        n_bytes = _write_silent_wav(out_path, sample_rate=22_050, milliseconds=120)
        return {"path": str(out_path), "sample_rate": 22_050, "bytes": n_bytes, "provider": "fake"}

    import numpy as np
    import config

    tts = importlib.import_module("core.tts")

    chunks = list(tts.stream_audio(text))
    out_path = _runtime_output_dir() / f"tts-{int(time.time() * 1000)}.wav"

    provider = config.TTS_PROVIDER.lower()
    if provider == "none" or not chunks:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22_050)
            wf.writeframes(b"")
        return {"path": str(out_path), "sample_rate": 22_050, "bytes": 0, "provider": provider}

    if provider == "elevenlabs":
        sample_rate = tts._EL_SAMPLE_RATE
        pcm_i16 = b"".join(chunks)
    else:
        sample_rate = tts.SAMPLE_RATE
        # np.frombuffer yields a read-only view over the immutable bytes, so
        # nan_to_num must copy (copy=False raises "assignment destination is
        # read-only"). clip then returns its own writable array.
        audio_f32 = np.frombuffer(b"".join(chunks), dtype=np.float32)
        audio_f32 = np.nan_to_num(audio_f32)
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_i16 = (audio_f32 * 32767.0).astype("<i2").tobytes()

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16)

    _log(f"tts synthesized {len(pcm_i16)} bytes -> {out_path}")
    return {"path": str(out_path), "sample_rate": sample_rate, "bytes": len(pcm_i16), "provider": provider}


@handler("brain.tts.test")
def brain_tts_test(provider: str = "", cartesia_voice_id: str = "") -> dict[str, Any]:
    """Validate the configured TTS route for the native Settings panel."""
    if _offline_brain():
        selected = (provider or "fake").strip().lower()
        return {"ok": True, "message": f"TTS route OK: {selected}", "provider": selected}

    import config

    tts = importlib.import_module("core.tts")

    selected = (provider or config.TTS_PROVIDER or "none").strip().lower()
    voice = cartesia_voice_id if cartesia_voice_id is not None else config.CARTESIA_VOICE_ID
    ok, message = tts.test_connection(
        selected,
        cartesia_voice_id=voice,
    )
    return {"ok": ok, "message": message, "provider": selected}


@handler("brain.llm.test")
def brain_llm_test(
    provider: str = "",
    model: str = "",
    fallbacks: str = "",
    route_name: str = "LLM",
    image: bool = False,
    custom_base_url: str = "",
) -> dict[str, Any]:
    """Validate a configured LLM route and its fallback chain for Settings."""
    selected_provider = provider.strip().lower()
    selected_model = model.strip()
    label = route_name.strip() or "LLM"
    from core.llm_clients.routes import route_candidates

    routes = route_candidates(selected_provider, selected_model, fallbacks)
    if not routes:
        return {
            "ok": False,
            "message": f"{label} test failed: No model configured.",
            "provider": selected_provider,
            "model": selected_model,
            "routes": [],
        }

    if _offline_brain():
        suffix = " vision route" if image else " route"
        if len(routes) > 1:
            lines = [
                f"{_route_test_label(index)} OK: {route_provider} / {route_model}"
                for index, (route_provider, route_model) in enumerate(routes)
            ]
            return {
                "ok": True,
                "message": f"{label}{suffix} OK:\n" + "\n".join(lines),
                "provider": selected_provider,
                "model": selected_model,
                "routes": _route_payloads(routes),
            }
        return {
            "ok": True,
            "message": f"{label}{suffix} OK: {selected_provider} / {selected_model}",
            "provider": selected_provider,
            "model": selected_model,
            "routes": _route_payloads(routes),
        }

    llm = importlib.import_module("core.llm_clients.client")

    results: list[dict[str, Any]] = []
    for index, (route_provider, route_model) in enumerate(routes):
        ok, message = llm.test_route_connection(
            route_provider,
            route_model,
            label,
            image=image,
            custom_base_url=_custom_base_url_for_route(route_provider, custom_base_url),
        )
        results.append({
            "label": _route_test_label(index),
            "ok": ok,
            "provider": route_provider,
            "model": route_model,
            "message": message,
        })

    if len(results) == 1:
        only = results[0]
        return {
            "ok": bool(only["ok"]),
            "message": str(only["message"]),
            "provider": selected_provider,
            "model": selected_model,
            "routes": results,
        }

    ok = all(bool(result["ok"]) for result in results)
    lines = [
        f"{result['label']} - {result['provider']} / {result['model']}: {_short_route_test_message(str(result['message']), label)}"
        for result in results
    ]
    status = "OK" if ok else "failed"
    return {
        "ok": ok,
        "message": f"{label} route chain {status}:\n" + "\n".join(lines),
        "provider": selected_provider,
        "model": selected_model,
        "routes": results,
    }


def _route_payloads(routes: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "label": _route_test_label(index),
            "ok": True,
            "provider": provider,
            "model": model,
            "message": "OK",
        }
        for index, (provider, model) in enumerate(routes)
    ]


def _route_test_label(index: int) -> str:
    return "Primary" if index == 0 else f"Fallback {index}"


def _short_route_test_message(message: str, route_name: str) -> str:
    prefix = f"{route_name} test failed: "
    if message.startswith(prefix):
        return message[len(prefix):]
    prefix = f"{route_name} route OK: "
    if message.startswith(prefix):
        return "OK"
    prefix = f"{route_name} vision route OK: "
    if message.startswith(prefix):
        return "OK"
    return message


def _custom_base_url_for_route(provider: str, custom_base_url: str) -> str | None:
    if provider.strip().lower() != "custom":
        return None
    return custom_base_url.strip() or None


# ---------------------------------------------------------------------------
# Real query path -- wired to the existing pipeline, exercised on the Mac / online.
# Imports are lazy so this module still loads with no LLM deps/keys present.
# ---------------------------------------------------------------------------

@handler("brain.query", streaming=True)
def brain_query(
    ctx: StreamContext,
    intent_prompt: str = "",
    selected: str | None = None,
    screenshot_b64: str | None = None,
    ambient_text: str = "",
    memory_context: str = "",
    memory_enabled: bool = True,
    use_tools: bool = False,
    allowed_tools: list[str] | None = None,
    pinned_tools: list[str] | None = None,
    frontload_tools: list[str] | None = None,
    allow_screenshot_tool: bool = False,
    screenshot_tool_b64: str | None = None,
    include_active_document: bool = False,
    active_document_text: str = "",
) -> dict[str, Any]:
    """Assemble context and stream an LLM reply, mirroring App._query_and_speak.

    Reuses the OS-agnostic brain verbatim: ``core.query_pipeline.build_context``
    for precedence rules and ``core.llm_clients.client.stream_response`` for the
    token stream. Each chunk becomes a ``reply.chunk`` event tagged with this
    request's id; the full text is the final response result.
    """
    from core.query_pipeline import ContextInputs, build_context

    if memory_enabled and not memory_context:
        try:
            from core.memory_store import store
            memory_context = store.get_manager().retrieve_relevant(intent_prompt) or ""
        except Exception as exc:  # memory should not block answering
            _log(f"memory retrieval skipped: {type(exc).__name__}: {exc}")

    active_document = active_document_text
    # Read open documents whenever the caller asked for them ("On"), including
    # alongside a screenshot — a screenshot shows pixels, not document text, so
    # it must not silently disable the documents setting.
    if include_active_document and not active_document:
        active_document = brain_context_active_document().get("text", "")

    built = build_context(
        ContextInputs(
            intent_prompt=intent_prompt,
            selected=selected,
            screenshot_b64=screenshot_b64,
            ambient_text=ambient_text,
            active_document_text=active_document,
        )
    )
    built = _apply_frontloaded_tools(built, frontload_tools)
    built = _apply_plugin_before_query(built)

    parts: list[str] = []
    for chunk in _stream_query_reply(
        built,
        memory_context,
        use_tools,
        allowed_tools,
        allow_screenshot_tool,
        screenshot_tool_b64,
        pinned_tools=pinned_tools,
    ):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    _notify_plugin_after_response(full)
    return {"text": full}


def _apply_frontloaded_tools(built: Any, frontload_tools: list[str] | None) -> Any:
    if not frontload_tools:
        return built
    try:
        from core.llm_clients.client import _inject_frontloaded_tool_context

        ambient_ctx = _inject_frontloaded_tool_context(
            getattr(built, "ambient_ctx", ""),
            frontload_tools,
        )
        return type(built)(
            user_message=getattr(built, "user_message", ""),
            ambient_ctx=ambient_ctx,
            screenshot_b64=getattr(built, "screenshot_b64", None),
        )
    except Exception as exc:  # noqa: BLE001 - injected context should not block answering
        _log(f"frontloaded tools skipped: {type(exc).__name__}: {exc}")
        return built


def _apply_plugin_before_query(built: Any) -> Any:
    try:
        from core.system.paths import ADDONS_DIR

        # Ensure on_startup ran and plugin get_tools are registered before the
        # LLM gathers tools for this query.
        run_plugin_startup()
        user_message, ambient_ctx = _loaded_plugin_manager(Path(ADDONS_DIR)).before_query(
            getattr(built, "user_message", ""),
            getattr(built, "ambient_ctx", ""),
        )
        return type(built)(
            user_message=user_message,
            ambient_ctx=ambient_ctx,
            screenshot_b64=getattr(built, "screenshot_b64", None),
        )
    except Exception as exc:  # noqa: BLE001 - plugin hooks should not block answering
        _log(f"plugin before_query skipped: {type(exc).__name__}: {exc}")
        return built


def _notify_plugin_after_response(text: str) -> None:
    if not text:
        return
    try:
        from core.system.paths import ADDONS_DIR

        _loaded_plugin_manager(Path(ADDONS_DIR)).after_response(text)
    except Exception as exc:  # noqa: BLE001 - plugin hooks should not block answering
        _log(f"plugin after_response skipped: {type(exc).__name__}: {exc}")


@handler("brain.context.active_document")
def brain_context_active_document(active_window: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return active/open document text through the shared context reader."""
    try:
        from core.llm_clients.client import read_active_document_for_context_with_debug

        text, debug = read_active_document_for_context_with_debug(active_window=active_window)
        if text.startswith(("Could not", "File type", "Failed to")):
            text = ""
        return {"text": text, "debug": debug}
    except Exception as exc:  # noqa: BLE001 - context should not block answering
        _log(f"active document read failed: {type(exc).__name__}: {exc}")
        return {"text": "", "error": str(exc)}


def _stream_query_reply(
    built: Any,
    memory_context: str,
    use_tools: bool,
    allowed_tools: list[str] | None,
    allow_screenshot_tool: bool,
    screenshot_tool_b64: str | None = None,
    pinned_tools: list[str] | None = None,
) -> Iterator[str]:
    """Token stream for ``brain.query``: real provider, or deterministic offline.

    In offline mode (``WISP_BRAIN_FAKE_LLM``) the assembled prompt is still built
    by ``core.query_pipeline.build_context`` -- so context precedence is exercised
    for real -- and the reply just echoes the intent plus the assembled ambient
    context (selected text, clipboard, app/window) with a ``[fake-llm]`` tag, so
    tests can assert reassembly and that each of their inputs reached the model.
    """
    if _offline_brain():
        prompt = (getattr(built, "user_message", "") or "").strip()
        ambient = (getattr(built, "ambient_ctx", "") or "").strip()
        combined = (prompt + ("\n" + ambient if ambient else "")).strip()
        reply = f"[fake-llm] {combined}".strip()
        for word in reply.split(" "):
            yield word + " "
        return

    from core.llm_clients.client import stream_response

    yield from stream_response(
        built.user_message,
        image_base64=built.screenshot_b64,
        ambient_context=built.ambient_ctx,
        memory_context=memory_context,
        use_tools=use_tools,
        allowed_tools=allowed_tools,
        pinned_tools=pinned_tools,
        allow_screenshot_tool=allow_screenshot_tool,
        screenshot_tool_b64=screenshot_tool_b64,
    )


@handler("brain.rewrite", streaming=True)
def brain_rewrite(
    ctx: StreamContext,
    selected_text: str = "",
    intent_prompt: str = "Rewrite or fix the following text",
) -> dict[str, Any]:
    """Stream an inline rewrite for native paste-back callers."""
    selected_text = selected_text.strip()
    if not selected_text:
        raise ValueError("selected_text is required")

    parts: list[str] = []
    for chunk in _stream_rewrite_reply(selected_text, intent_prompt):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


def _stream_rewrite_reply(selected_text: str, intent_prompt: str) -> Iterator[str]:
    if _offline_brain():
        reply = f"[fake-rewrite] {intent_prompt}: {selected_text}"
        for word in reply.split(" "):
            yield word + " "
        return

    from core.llm_clients.client import stream_rewrite

    yield from stream_rewrite(selected_text, intent_prompt)


@handler("brain.chat", streaming=True)
def brain_chat(
    ctx: StreamContext,
    messages: list[dict[str, Any]] | None = None,
    memory_context: str = "",
    memory_enabled: bool = True,
) -> dict[str, Any]:
    """Stream a multi-turn chat reply from the existing chat LLM path."""
    turns = _normalize_chat_messages(messages or [])
    if not turns:
        raise ValueError("messages must include at least one user turn")

    if memory_enabled and not memory_context:
        last_user = next(
            (str(m.get("content") or "") for m in reversed(turns) if m.get("role") == "user"),
            "",
        )
        if last_user:
            try:
                from core.memory_store import store
                memory_context = store.get_manager().retrieve_relevant(last_user) or ""
            except Exception as exc:  # memory should not block chat
                _log(f"chat memory retrieval skipped: {type(exc).__name__}: {exc}")

    parts: list[str] = []
    for chunk in _stream_chat_reply(turns, memory_context):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


def _normalize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    allowed_roles = {"system", "user", "assistant"}
    turns: list[dict[str, str]] = []
    for raw in messages:
        role = str(raw.get("role") or "").strip().lower()
        content = raw.get("content")
        if role not in allowed_roles or content is None:
            continue
        text = str(content).strip()
        turn: dict[str, str] = {"role": role, "content": text}
        # Carry attached screenshots forward so the model sees them on every
        # follow-up turn, the way ChatGPT/Claude replay the full transcript.
        image = raw.get("image_base64")
        if role == "user" and image:
            turn["image_base64"] = str(image)
        if text or turn.get("image_base64"):
            turns.append(turn)
    return turns


def _stream_chat_reply(messages: list[dict[str, str]], memory_context: str) -> Iterator[str]:
    if _offline_brain():
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = f"[fake-chat] {last_user}".strip()
        for word in reply.split(" "):
            yield word + " "
        return

    from core.llm_clients.client import stream_response_with_history

    yield from stream_response_with_history(messages, memory_context=memory_context)


@handler("brain.memory.add")
def brain_memory_add(text: str = "", category: str | None = None) -> dict[str, Any]:
    """Add a durable memory fact through the existing memory store."""
    fact = text.strip()
    if not fact:
        raise ValueError("text is required")

    from core.memory_store import store

    manager = store.get_manager()
    if category:
        manager.add_fact_manual(fact, category)
        used_category = category
    else:
        manager.add_explicit_fact(fact)
        used_category = "auto"
    return {"ok": True, "category": used_category, "text": fact}


@handler("brain.memory.search")
def brain_memory_search(query: str = "", top_k: int | None = None) -> dict[str, Any]:
    """Return the same memory block injected into LLM context."""
    if not query.strip():
        raise ValueError("query is required")

    from core.memory_store import store

    text = store.get_manager().retrieve_relevant(query, top_k=top_k) or ""
    return {"text": text}


@handler("brain.memory.list")
def brain_memory_list() -> dict[str, Any]:
    """Return active memory facts for the Python macOS memory UI."""
    from core.memory_store import store

    facts = store.get_manager().get_all_facts()
    return {"facts": [_memory_fact_payload(fact) for fact in facts]}


@handler("brain.memory.update")
def brain_memory_update(fact_id: str = "", text: str = "", category: str | None = None) -> dict[str, Any]:
    """Update one durable memory fact through the existing memory store."""
    cleaned_id = fact_id.strip()
    cleaned_text = text.strip()
    if not cleaned_id:
        raise ValueError("fact_id is required")
    if not cleaned_text:
        raise ValueError("text is required")

    from core.memory_store import store

    store.get_manager().update_fact(cleaned_id, cleaned_text, category)
    return {"ok": True, "id": cleaned_id, "text": cleaned_text, "category": category}


@handler("brain.memory.delete")
def brain_memory_delete(fact_id: str = "") -> dict[str, Any]:
    """Delete one durable memory fact through the existing memory store."""
    cleaned_id = fact_id.strip()
    if not cleaned_id:
        raise ValueError("fact_id is required")

    from core.memory_store import store

    store.get_manager().delete_fact(cleaned_id)
    return {"ok": True, "id": cleaned_id}


def _memory_fact_payload(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(fact.get("id") or ""),
        "text": str(fact.get("text") or ""),
        "category": str(fact.get("category") or "general"),
        "source": str(fact.get("source") or "unknown"),
        "created_at": str(fact.get("created_at") or ""),
        "last_seen": str(fact.get("last_seen") or ""),
    }


def _agent_runs_root(log_root: str | None = None) -> Path:
    if log_root:
        root = Path(log_root)
    else:
        root = _runtime_output_dir() / "agent-runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _agent_last_task_path(log_root: str | None = None) -> Path:
    return _agent_runs_root(log_root) / "last_task.json"


def _save_agent_last_task_spec(spec: dict[str, Any], log_root: str | None = None) -> bool:
    path = _agent_last_task_path(log_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def _load_agent_last_task_spec(log_root: str | None = None) -> dict[str, Any] | None:
    from core.agent.task_spec import agent_task_spec_from_dict

    root = _agent_runs_root(log_root)
    candidates = [_agent_last_task_path(log_root)]
    run_task_files = sorted(
        (path / "task.json" for path in root.iterdir() if path.is_dir() and (path / "task.json").exists()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    candidates.extend(run_task_files)

    for path in candidates:
        data = _read_json(path)
        if not data:
            continue
        try:
            return asdict(agent_task_spec_from_dict(data))
        except Exception:
            continue
    return None


@handler("brain.agent.last_spec.read")
def brain_agent_last_spec_read(log_root: str | None = None) -> dict[str, Any]:
    """Return the last started task spec, matching the Windows copy-last flow."""
    return {"spec": _load_agent_last_task_spec(log_root)}


@handler("brain.agent.last_spec.write")
def brain_agent_last_spec_write(spec: Any = None, log_root: str | None = None) -> dict[str, Any]:
    """Persist a validated task spec as the next copy-last source."""
    if not isinstance(spec, dict):
        raise ValueError("spec (a serialized agent task dict) is required")

    from core.agent.task_spec import agent_task_spec_from_dict

    validated = asdict(agent_task_spec_from_dict(spec))
    return {
        "ok": _save_agent_last_task_spec(validated, log_root),
        "path": str(_agent_last_task_path(log_root)),
        "spec": validated,
    }


def _agent_approval_callback(ctx: StreamContext) -> Callable[[dict], bool]:
    def request_approval(request: dict) -> bool:
        approval_id = uuid.uuid4().hex
        event = threading.Event()
        state: dict[str, Any] = {"event": event, "approved": False}
        with _AGENT_APPROVALS_LOCK:
            _AGENT_APPROVALS[approval_id] = state

        payload = dict(request)
        payload["approval_id"] = approval_id
        ctx.emit("agent.approval.request", payload)

        try:
            while not event.wait(0.1):
                if ctx.cancelled:
                    return False
            return bool(state["approved"])
        finally:
            with _AGENT_APPROVALS_LOCK:
                _AGENT_APPROVALS.pop(approval_id, None)

    return request_approval


@handler("brain.agent.approval.respond")
def brain_agent_approval_respond(approval_id: str = "", approved: bool = False) -> dict[str, Any]:
    """Resolve one pending agent approval prompt emitted by ``brain.agent.run``."""
    cleaned = approval_id.strip()
    if not cleaned:
        raise ValueError("approval_id is required")

    with _AGENT_APPROVALS_LOCK:
        state = _AGENT_APPROVALS.get(cleaned)
    if state is None:
        return {"ok": False, "message": "approval request is no longer pending"}

    state["approved"] = bool(approved)
    event = state.get("event")
    if isinstance(event, threading.Event):
        event.set()
    return {"ok": True, "approved": bool(approved)}


@handler("brain.agent.history.list")
def brain_agent_history_list(log_root: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Return recent agent run folders and lightweight metadata for native UI."""
    root = _agent_runs_root(log_root)
    run_dirs = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    runs = [
        _agent_run_summary(path)
        for path in run_dirs
        if (path / "task.json").exists() or (path / "final.md").exists() or (path / "run.log").exists()
    ]
    return {"runs_root": str(root), "runs": runs[:max(1, int(limit or 100))]}


@handler("brain.agent.history.read")
def brain_agent_history_read(run_dir: str = "") -> dict[str, Any]:
    """Return full readable artifacts for one agent run."""
    path = _agent_run_dir(run_dir)
    if not any((path / name).exists() for name in ("task.json", "final.md", "run.log", "error.txt")):
        raise ValueError("run_dir is not an agent run")

    return {
        **_agent_run_summary(path),
        "task_json": _read_text(path / "task.json"),
        "final": _read_text(path / "final.md"),
        "error": _read_text(path / "error.txt"),
        "run_log": _read_text(path / "run.log"),
        "verbose_log": _read_text(path / "verbose.log"),
        "diff_patch": _read_text(path / "diff.patch"),
    }


@handler("brain.agent.history.retry_spec")
def brain_agent_history_retry_spec(run_dir: str = "") -> dict[str, Any]:
    """Return the original task spec for a previous run."""
    from core.agent.task_spec import retry_spec_from_run

    spec = retry_spec_from_run(_agent_run_dir(run_dir))
    return {"spec": asdict(spec)}


@handler("brain.agent.history.continue_spec")
def brain_agent_history_continue_spec(run_dir: str = "") -> dict[str, Any]:
    """Return a continuation task spec with compact prior-run context."""
    from core.agent.task_spec import continue_spec_from_run

    spec = continue_spec_from_run(_agent_run_dir(run_dir))
    return {"spec": asdict(spec)}


def _agent_run_dir(run_dir: str) -> Path:
    cleaned = run_dir.strip()
    if not cleaned:
        raise ValueError("run_dir is required")
    path = Path(cleaned).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("run_dir does not exist")
    return path


def _agent_run_summary(run_dir: Path) -> dict[str, Any]:
    task = _read_json(run_dir / "task.json")
    final = _read_text(run_dir / "final.md")
    error = _read_text(run_dir / "error.txt")
    run_log = _read_text(run_dir / "run.log", max_chars=12_000)
    title = str((task or {}).get("title") or run_dir.name)
    status = _agent_run_status(final, error, run_log)
    modified = run_dir.stat().st_mtime
    return {
        "id": run_dir.name,
        "run_dir": str(run_dir),
        "title": title,
        "objective": str((task or {}).get("objective") or ""),
        "status": status,
        "modified": modified,
        "modified_display": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(modified)),
        "has_final": bool(final.strip()),
        "has_error": bool(error.strip()),
        "has_diff": (run_dir / "diff.patch").exists(),
    }


def _agent_run_status(final: str, error: str, run_log: str) -> str:
    if error.strip():
        return "failed"
    if "agent run cancelled" in run_log:
        return "cancelled"
    if final.strip() or "agent run finished" in run_log:
        return "complete"
    return "in progress"


def _read_text(path: Path, *, max_chars: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if max_chars is not None and len(text) > max_chars:
        return text[-max_chars:]
    return text


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Agent runtime -- the scoped multi-agent task runner behind the Windows tray's
# "Start agent task" dialog, exposed to the native shell. Reuses the OS-agnostic
# ``core.agent`` verbatim; only the log/trace plumbing and cancel bridging are
# brain-specific. Run artifacts are written under the run-log dir (never the
# repo's memory/agent_runs) so the brain worker stays self-contained on the Mac.
# ---------------------------------------------------------------------------

@handler("brain.agent.run", streaming=True)
def brain_agent_run(ctx: StreamContext, spec: Any = None, log_root: str | None = None) -> dict[str, Any]:
    """Run one scoped agent task, streaming its log/trace lines as events.

    ``spec`` is the same serialized ``AgentTaskSpec`` dict the Windows GUI builds
    (title/objective/scope_folder/permissions/agents/...). Each run.log line is
    emitted as an ``agent.log`` event and each verbose entry as ``agent.trace``,
    both tagged with this request id so the host can render live progress. The
    final result carries the run directory and the final report; the runner never
    raises (it captures failures into ``error.txt``), so a failed task returns its
    error text rather than erroring the call.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec (a serialized agent task dict) is required")

    from core.agent.task_spec import agent_task_spec_from_dict
    from core.agent.runner import AgentTaskRunner
    from core.agent.runtime import AgentRunControl

    task_spec = agent_task_spec_from_dict(spec)
    _save_agent_last_task_spec(asdict(task_spec), log_root)
    control = AgentRunControl()

    # Bridge cooperative cancel: the host cancels the stream (brain.cancel) ->
    # ctx.cancelled flips -> propagate into the agent's own cancel token so the
    # run loop stops at its next checkpoint.
    stop_watch = threading.Event()

    def _watch_cancel() -> None:
        while not stop_watch.wait(0.1):
            if ctx.cancelled:
                control.cancel()
                return

    watcher = threading.Thread(target=_watch_cancel, daemon=True)
    watcher.start()

    runs_dir = _agent_runs_root(log_root)
    runner = AgentTaskRunner(
        log_root=runs_dir,
        model_callback=_agent_test_model_callback(),
        approval_callback=_agent_approval_callback(ctx),
        control=control,
    )

    def on_log(line: str) -> None:
        ctx.emit("agent.log", {"line": line})

    def on_trace(entry: str) -> None:
        ctx.emit("agent.trace", {"entry": entry})

    try:
        run_dir = runner.run(task_spec, on_log, on_trace)
    finally:
        stop_watch.set()

    run_dir = Path(run_dir)
    final_text = ""
    final_path = run_dir / "final.md"
    if final_path.exists():
        final_text = final_path.read_text(encoding="utf-8", errors="replace")
    error_text = ""
    error_path = run_dir / "error.txt"
    if error_path.exists():
        error_text = error_path.read_text(encoding="utf-8", errors="replace")

    result = {
        "run_dir": str(run_dir),
        "final": final_text,
        "error": error_text,
        "cancelled": control.is_cancelled(),
    }
    ctx.emit("agent.done", result)
    return result


__all__ = ["HANDLERS", "STREAMING", "StreamContext", "handler"]



