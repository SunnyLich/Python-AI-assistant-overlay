"""
core/github_auth.py - GitHub OAuth device authentication.

Tokens are stored in the OS keychain via keyring, with the same local fallback
pattern used by chatgpt_auth.py.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Callable
from urllib.parse import urlencode

import config

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"

_KEYRING_SERVICE = "python-ai-overlay"
_KEYRING_ACCOUNT = "github-oauth"
_TOKEN_FILE = pathlib.Path(__file__).parent.parent / "private" / ".github_tokens.json"


def configured_client_id() -> str:
    """Return the bundled GitHub OAuth client ID, with optional user override."""
    return (
        getattr(config, "GITHUB_CLIENT_ID", "").strip()
        or getattr(config, "GITHUB_DEFAULT_CLIENT_ID", "").strip()
    )


def _keyring_get() -> str | None:
    try:
        import keyring  # type: ignore
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception:
        return None


def _keyring_set(value: str) -> bool:
    try:
        import keyring  # type: ignore
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, value)
        return True
    except Exception:
        return False


def _keyring_delete() -> None:
    try:
        import keyring  # type: ignore
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception:
        pass


def get_tokens() -> dict | None:
    raw = _keyring_get()
    if not raw and _TOKEN_FILE.exists():
        try:
            raw = _TOKEN_FILE.read_text(encoding="utf-8")
        except Exception:
            pass
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return None


def save_tokens(tokens: dict) -> None:
    serialized = json.dumps(tokens)
    if not _keyring_set(serialized):
        print(
            "[github_auth] Warning: keyring unavailable - "
            f"OAuth tokens stored in plaintext file: {_TOKEN_FILE}"
        )
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(serialized, encoding="utf-8")


def clear_tokens() -> None:
    _keyring_delete()
    try:
        _TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def get_valid_access_token() -> str | None:
    tokens = get_tokens()
    return tokens.get("access") if tokens else None


def get_user_login() -> str | None:
    tokens = get_tokens()
    user = tokens.get("user") if tokens else None
    return user.get("login") if isinstance(user, dict) else None


def _post_form(url: str, params: dict) -> dict:
    import urllib.request

    body = urlencode(params).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "python-ai-overlay",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get_json(url: str, token: str) -> dict:
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "python-ai-overlay",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _tokens_from_raw(raw: dict) -> dict:
    access = raw["access_token"]
    user = _get_json(_USER_URL, access)
    return {
        "access": access,
        "token_type": raw.get("token_type", "bearer"),
        "scope": raw.get("scope", ""),
        "user": {
            "login": user.get("login"),
            "id": user.get("id"),
            "name": user.get("name"),
        },
        "saved_at": int(time.time() * 1000),
    }


def start_device_login(
    on_code: Callable[[str, str], None],
    on_success: Callable[[dict], None],
    on_error: Callable[[str], None],
) -> None:
    """
    Start GitHub OAuth device flow in a background daemon thread.

    The GitHub OAuth app must have device flow enabled. The app client ID is
    bundled by default, with GITHUB_CLIENT_ID as an optional override.
    """

    def _run() -> None:
        client_id = configured_client_id()
        if not client_id:
            on_error("No bundled GitHub OAuth client ID is configured for this app yet.")
            return

        try:
            device = _post_form(
                _DEVICE_CODE_URL,
                {
                    "client_id": client_id,
                    "scope": getattr(config, "GITHUB_OAUTH_SCOPES", "").strip(),
                },
            )
        except Exception as exc:
            on_error(f"Device auth initiation failed: {exc}")
            return

        if "error" in device:
            on_error(device.get("error_description") or device["error"])
            return

        verification_uri = device["verification_uri"]
        user_code = device["user_code"]
        device_code = device["device_code"]
        expires_at = time.time() + int(device.get("expires_in", 900))
        interval = max(int(device.get("interval", 5)), 1)

        on_code(verification_uri, user_code)

        while time.time() < expires_at:
            time.sleep(interval)
            try:
                data = _post_form(
                    _ACCESS_TOKEN_URL,
                    {
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
            except Exception as exc:
                on_error(f"Token polling failed: {exc}")
                return

            error = data.get("error")
            if not error:
                try:
                    tokens = _tokens_from_raw(data)
                    save_tokens(tokens)
                    on_success(tokens)
                except Exception as exc:
                    on_error(f"Token validation failed: {exc}")
                return
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = int(data.get("interval", interval + 5))
                continue
            on_error(data.get("error_description") or error)
            return

        on_error("GitHub device code expired. Start sign-in again.")

    threading.Thread(target=_run, daemon=True, name="github-oauth-device").start()
