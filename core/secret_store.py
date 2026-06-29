"""Small OS-keychain wrapper for provider API keys.

All API keys are stored in a SINGLE keychain item (one JSON blob) instead of one
item per key. On macOS every keychain item is guarded by its own ACL, so the old
one-item-per-key layout made the OS prompt for the login password once per stored
key at startup (config.reload() reads them all). Consolidating to one item means
the user authorizes exactly once — click "Always Allow" a single time and every
key is available.

Existing per-key items from older versions are migrated into the blob on first
read (a one-time cost), so upgrading loses no keys.
"""
from __future__ import annotations

import os
import json
import logging
import threading
from pathlib import Path

from core.system.native_locks import keychain_lock

log = logging.getLogger("wisp.secrets")

_KEYRING_SERVICE = "python-ai-overlay"
# Single consolidated item holding {KEY_NAME: value} as JSON.
_BLOB_ACCOUNT = "__wisp_secrets__"

_META_FILE = Path(__file__).parent.parent / "private" / ".secret_status.json"
# Meta flag recording that the one-time legacy migration has run, so we don't
# re-probe the old per-key items on every startup.
_MIGRATED_FLAG = "__consolidated_v1__"

# Cache the whole decrypted blob. The keychain is read at most once per process
# (warmed by config.reload() at startup on the main thread), so worker-thread
# lookups never hit the Security framework — which can segfault off-main; see
# core.system.native_locks — and macOS prompts at most once. The lock is held
# across the read so two cold loads can't race the framework.
_blob_cache: dict[str, str] | None = None
_cache_lock = threading.Lock()

API_KEY_NAMES = (
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "CARTESIA_API_KEY",
    "ELEVENLABS_API_KEY",
    "TTS_CUSTOM_API_KEY",
    "CUSTOM_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "CEREBRAS_API_KEY",
    "ZAI_API_KEY",
    "NVIDIA_API_KEY",
    "SAMBANOVA_API_KEY",
    "GITHUB_MODELS_API_KEY",
    "HUGGINGFACE_API_KEY",
    "CHUTES_API_KEY",
    "VERCEL_API_KEY",
    "FIREWORKS_API_KEY",
    "COHERE_API_KEY",
    "AI21_API_KEY",
    "NEBIUS_API_KEY",
)


class KeychainError(RuntimeError):
    """Raised when an OS-keychain write cannot be completed or verified."""


def _account(name: str) -> str:
    """Legacy per-key account name (still read during migration)."""
    return name.lower()


def _write_blob_raw(blob: dict) -> None:
    """Write blob raw."""
    with keychain_lock():
        import keyring  # type: ignore
        keyring.set_password(_KEYRING_SERVICE, _BLOB_ACCOUNT, json.dumps(blob))


def _migrate_legacy_items() -> dict:
    """Read any old one-item-per-key secrets into a single dict."""
    blob: dict[str, str] = {}
    try:
        with keychain_lock():
            import keyring  # type: ignore
            for name in API_KEY_NAMES:
                try:
                    value = keyring.get_password(_KEYRING_SERVICE, _account(name))
                except Exception:
                    value = None
                if value:
                    blob[name] = value
    except Exception:
        return blob
    return blob


def _read_legacy_item(name: str) -> str:
    """Read one old per-key secret item.

    The bulk migration only runs on an empty consolidated blob. If a user saved
    some keys before consolidation and later saved another key into the blob, a
    remaining legacy item can still be the only copy of that key.
    """
    try:
        with keychain_lock():
            import keyring  # type: ignore
            return keyring.get_password(_KEYRING_SERVICE, _account(name)) or ""
    except Exception:
        return ""


def _delete_legacy_item(name: str) -> None:
    """Remove an old per-key keychain item if one still exists.

    No-op when the item is absent (keyring raises PasswordDeleteError), so this
    is safe to call for keys that were never stored in the legacy layout.
    """
    try:
        with keychain_lock():
            import keyring  # type: ignore
            try:
                keyring.delete_password(_KEYRING_SERVICE, _account(name))
            except Exception:
                pass
    except Exception:
        pass


def _load_blob() -> dict:
    """Return the consolidated secrets dict, reading the keychain once and caching."""
    global _blob_cache
    with _cache_lock:
        if _blob_cache is not None:
            return _blob_cache

    blob: dict[str, str] = {}
    try:
        with keychain_lock():
            import keyring  # type: ignore
            raw = keyring.get_password(_KEYRING_SERVICE, _BLOB_ACCOUNT)
            if raw:
                blob = json.loads(raw) or {}
    except Exception:
        blob = {}

    # One-time migration from the old one-item-per-key layout.
    if not blob and not _read_meta().get(_MIGRATED_FLAG):
        blob = _migrate_legacy_items()
        if blob:
            try:
                _write_blob_raw(blob)
            except Exception:
                pass
        set_configured_marker(_MIGRATED_FLAG, True)

    with _cache_lock:
        if _blob_cache is None:
            _blob_cache = blob
        return _blob_cache


def _save_blob(blob: dict) -> None:
    """Persist the consolidated dict and refresh the cache."""
    global _blob_cache
    _write_blob_raw(blob)
    with _cache_lock:
        _blob_cache = dict(blob)


def _invalidate_cache() -> None:
    """Handle invalidate cache for secret store."""
    global _blob_cache
    with _cache_lock:
        _blob_cache = None


def refresh_cache() -> None:
    """Force the next secret read to hit the backing keychain/env store."""
    _invalidate_cache()


def get_secret(name: str) -> str:
    """Return a secret from the OS keychain, falling back to env during migration."""
    value = get_keychain_secret(name)
    if value:
        return value
    return os.getenv(name, "")


def get_keychain_secret(name: str) -> str:
    """Return a secret only from the OS keychain (cached after first read)."""
    value = _load_blob().get(name, "")
    if value or name not in API_KEY_NAMES:
        return value

    legacy_value = _read_legacy_item(name)
    if not legacy_value:
        return ""

    try:
        blob = dict(_load_blob())
        blob[name] = legacy_value
        _save_blob(blob)
        set_configured_marker(name, True)
    except Exception:
        pass
    return legacy_value


def has_secret(name: str) -> bool:
    """Return whether secret is available."""
    if get_secret(name):
        return True
    if configured_marker(name) and name in API_KEY_NAMES:
        set_configured_marker(name, False)
    return False


def secret_source(name: str) -> str:
    """Return where a secret would be read from, without returning the secret."""
    if get_keychain_secret(name):
        return "keychain"
    if os.getenv(name, ""):
        return "env"
    if configured_marker(name) and name in API_KEY_NAMES:
        set_configured_marker(name, False)
    return "none"


def set_secret(name: str, value: str) -> None:
    """Write a secret to the OS keychain and verify it landed.

    The "configured" marker is only set after a successful read-back, so a
    silently-dropped write can no longer leave a stale marker without a stored
    value. Raises KeychainError (after logging) on any failure.
    """
    try:
        with keychain_lock():
            import keyring  # type: ignore  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller + log
        log.error("Cannot save %s: OS keychain support (keyring) is unavailable: %s", name, exc)
        raise KeychainError(
            "OS keychain support (the 'keyring' package) is not available, "
            f"so {name} could not be saved."
        ) from exc

    blob = dict(_load_blob())
    blob[name] = value
    try:
        _save_blob(blob)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller + log
        log.error("Failed writing %s to OS keychain: %s", name, exc)
        raise KeychainError(f"Could not write {name} to the OS keychain: {exc}") from exc

    # Read back from the keychain (not the cache) to confirm it persisted.
    _invalidate_cache()
    if get_keychain_secret(name) != value:
        log.error("Verification failed for %s: keychain read-back did not match the value written", name)
        set_configured_marker(name, False)
        raise KeychainError(
            f"{name} did not persist to the OS keychain (verification read-back failed)."
        )

    set_configured_marker(name, True)
    log.info("Saved %s to OS keychain", name)


def delete_secret(name: str) -> None:
    """Delete secret."""
    try:
        blob = dict(_load_blob())
        if name in blob:
            del blob[name]
            _save_blob(blob)
    except Exception:
        pass
    # Also remove any surviving legacy per-key item. Migration copies these into
    # the blob but never deletes them, so a blob-only delete would be resurrected
    # by get_keychain_secret's legacy fallback on the next read.
    _delete_legacy_item(name)
    set_configured_marker(name, False)


def migrate_env_secrets(env: dict[str, str]) -> list[str]:
    """
    Copy any existing .env API keys into the OS keychain.
    Returns the names that were migrated.
    """
    try:
        blob = dict(_load_blob())
    except Exception:
        blob = {}

    migrated: list[str] = []
    for name in API_KEY_NAMES:
        value = (env.get(name) or "").strip()
        if not value or blob.get(name):
            continue
        blob[name] = value
        migrated.append(name)

    if migrated:
        _save_blob(blob)
        for name in migrated:
            set_configured_marker(name, True)
    return migrated


def configured_marker(name: str) -> bool:
    """Handle configured marker for secret store."""
    return bool(_read_meta().get(name))


def set_configured_marker(name: str, configured: bool) -> None:
    """Set configured marker."""
    data = _read_meta()
    if configured:
        data[name] = True
    else:
        data.pop(name, None)
    _META_FILE.parent.mkdir(parents=True, exist_ok=True)
    _META_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_meta() -> dict:
    """Read meta."""
    try:
        return json.loads(_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
