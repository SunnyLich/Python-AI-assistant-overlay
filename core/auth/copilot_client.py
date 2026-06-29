"""core/copilot_client.py - GitHub Copilot SDK bridge."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from typing import Iterable


_SDK_MODULE = "copilot"
_COPILOT_STATE_HOME = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "private", "copilot-state")
)


def _available_sdk_module() -> str | None:
    """Handle available sdk module for auth copilot client."""
    return _SDK_MODULE if importlib.util.find_spec(_SDK_MODULE) else None


def _extract_content(response) -> str:
    """Extract content."""
    data = getattr(response, "data", None)
    content = getattr(data, "content", None) if data is not None else None
    if content is None and isinstance(data, dict):
        content = data.get("content")
    if content is None and isinstance(response, dict):
        data = response.get("data")
        content = data.get("content") if isinstance(data, dict) else response.get("content")
    return str(content or "")


def _client_options(token: str) -> dict:
    """Handle client options for auth copilot client."""
    import config

    env = dict(os.environ)
    env["COPILOT_GITHUB_TOKEN"] = token
    env.setdefault("XDG_STATE_HOME", _COPILOT_STATE_HOME)

    options: dict = {
        "github_token": token,
        "use_logged_in_user": False,
        "env": env,
    }
    cli_url = getattr(config, "COPILOT_CLI_URL", "").strip()
    cli_path = getattr(config, "COPILOT_CLI_PATH", "").strip()
    if cli_url:
        options["cli_url"] = cli_url
    if cli_path:
        options["cli_path"] = cli_path
    return options


def _deny_permission(_request, _context) -> dict:
    """Handle deny permission for auth copilot client."""
    return {"kind": "denied-by-rules", "rules": []}


def _approve_permission(_request, _context) -> dict:
    """Handle approve permission for auth copilot client."""
    return {"kind": "approved", "rules": []}


async def _ask_async(
    prompt: str,
    model: str,
    system: str = "",
    session_id: str | None = None,
    allow_tools: bool = False,
) -> str:
    """Handle ask async for auth copilot client."""
    from copilot import CopilotClient  # type: ignore
    from core.auth import copilot_auth

    token = copilot_auth.get_effective_token()
    if not token:
        raise RuntimeError(
            "No Copilot token is stored and you are not signed in to GitHub."
        )

    ok, message = copilot_auth.validate_token_format(token)
    if not ok:
        raise RuntimeError(message)

    client = CopilotClient(_client_options(token))
    try:
        await client.start()
        session_options: dict = {
            "model": model or "gpt-4.1",
            "session_id": session_id or f"wisp-{int(time.time() * 1000)}",
            "on_permission_request": _approve_permission if allow_tools else _deny_permission,
        }
        if not allow_tools:
            session_options["available_tools"] = []
        if system:
            session_options["system_message"] = {
                "mode": "replace",
                "content": system,
            }
        session = await client.create_session(session_options)
        response = await session.send_and_wait({"prompt": prompt})
        return _extract_content(response)
    finally:
        await client.stop()


def _ask_sync(
    prompt: str,
    model: str,
    system: str = "",
    session_id: str | None = None,
    allow_tools: bool = False,
) -> str:
    """Handle ask sync for auth copilot client."""
    return asyncio.run(_ask_async(prompt, model, system, session_id, allow_tools))


def ask(
    prompt: str,
    model: str,
    system: str = "",
    session_id: str | None = None,
    allow_tools: bool = False,
) -> str:
    """Handle ask for auth copilot client."""
    try:
        asyncio.get_running_loop()
    except RuntimeError as exc:
        if "no running event loop" in str(exc):
            return _ask_sync(prompt, model, system, session_id, allow_tools)
        raise
    raise RuntimeError("Copilot calls cannot run inside an existing asyncio event loop yet.")


def stream(
    prompt: str,
    model: str,
    system: str = "",
    session_id: str | None = None,
    allow_tools: bool = False,
) -> Iterable[str]:
    """Yield the Copilot reply as a single chunk (non-streaming shim)."""
    text = ask(prompt, model, system, session_id, allow_tools)
    if text:
        yield text


def test_copilot_token() -> tuple[bool, str]:
    """Verify copilot token behavior."""
    from core.auth import copilot_auth

    token = copilot_auth.get_effective_token()
    if not token:
        return False, "No Copilot token is stored and you are not signed in to GitHub."

    ok, message = copilot_auth.validate_token_format(token)
    if not ok:
        return False, message

    sdk_module = _available_sdk_module()
    if not sdk_module:
        return False, (
            "Token is stored and the format looks usable, but github-copilot-sdk "
            "is not installed yet. Install requirements, then test again."
        )

    return True, (
        f"Token is stored and SDK module '{sdk_module}' is available. "
        "Select provider 'copilot' to route text requests through it."
    )
