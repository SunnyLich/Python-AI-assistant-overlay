"""Small OS-keychain wrapper for provider API keys."""
from __future__ import annotations

import os
import json
from pathlib import Path

_KEYRING_SERVICE = "python-ai-overlay"
_META_FILE = Path(__file__).parent.parent / "private" / ".secret_status.json"

API_KEY_NAMES = (
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "CARTESIA_API_KEY",
    "ELEVENLABS_API_KEY",
)


def _account(name: str) -> str:
    return name.lower()


def get_secret(name: str) -> str:
    """Return a secret from the OS keychain, falling back to env during migration."""
    value = get_keychain_secret(name)
    if value:
        return value
    return os.getenv(name, "")


def get_keychain_secret(name: str) -> str:
    """Return a secret only from the OS keychain."""
    try:
        import keyring  # type: ignore

        value = keyring.get_password(_KEYRING_SERVICE, _account(name))
        if value:
            return value
    except Exception:
        pass
    return ""


def has_secret(name: str) -> bool:
    return bool(get_secret(name)) or configured_marker(name)


def secret_source(name: str) -> str:
    """Return where a secret would be read from, without returning the secret."""
    if get_keychain_secret(name):
        return "keychain"
    if os.getenv(name, ""):
        return "env"
    return "none"


def set_secret(name: str, value: str) -> None:
    import keyring  # type: ignore

    keyring.set_password(_KEYRING_SERVICE, _account(name), value)
    set_configured_marker(name, True)


def delete_secret(name: str) -> None:
    try:
        import keyring  # type: ignore

        keyring.delete_password(_KEYRING_SERVICE, _account(name))
    except Exception:
        pass
    set_configured_marker(name, False)


def migrate_env_secrets(env: dict[str, str]) -> list[str]:
    """
    Copy any existing .env API keys into the OS keychain.
    Returns the names that were migrated.
    """
    migrated: list[str] = []
    for name in API_KEY_NAMES:
        value = (env.get(name) or "").strip()
        if not value:
            continue
        if get_keychain_secret(name):
            continue
        set_secret(name, value)
        migrated.append(name)
    return migrated


def configured_marker(name: str) -> bool:
    return bool(_read_meta().get(name))


def set_configured_marker(name: str, configured: bool) -> None:
    data = _read_meta()
    if configured:
        data[name] = True
    else:
        data.pop(name, None)
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_meta() -> dict:
    try:
        return json.loads(_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
