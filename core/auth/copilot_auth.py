"""
core/copilot_auth.py - GitHub Copilot token storage.

Fine-grained GitHub PATs with the account permission
"Copilot Requests: Read-only" are sensitive reusable credentials, so this
module stores them only in the OS keychain. It intentionally has no plaintext
fallback file.
"""
from __future__ import annotations

from core.system.native_locks import keychain_lock

_KEYRING_SERVICE = "python-ai-overlay"
_KEYRING_ACCOUNT = "github-copilot-token"


class CopilotTokenError(RuntimeError):
    """Raised when the Copilot token cannot be stored or read safely."""


def _keyring_module():
    """Handle keyring module for auth copilot auth."""
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise CopilotTokenError(f"OS keychain is unavailable: {exc}") from exc
    return keyring


def validate_token_format(token: str) -> tuple[bool, str]:
    """Validate token format."""
    token = token.strip()
    if not token:
        return False, "Paste a GitHub Copilot-capable token first."
    if token.startswith("github_pat_"):
        return True, "Token format looks like a fine-grained GitHub PAT."
    if token.startswith("ghp_"):
        return False, (
            "Classic GitHub PATs (ghp_...) are not enough for Copilot SDK "
            "access. Use a fine-grained PAT with Copilot Requests: Read-only."
        )
    if token.startswith(("gho_", "ghu_", "ghs_")):
        return True, (
            "Token format looks like a GitHub OAuth/App token. It may work if "
            "GitHub granted Copilot access to that token."
        )
    return False, "This does not look like a GitHub token."


def save_token(token: str) -> None:
    """Save token."""
    token = token.strip()
    ok, message = validate_token_format(token)
    if not ok:
        raise CopilotTokenError(message)
    try:
        with keychain_lock():
            keyring = _keyring_module()
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, token)
    except Exception as exc:
        raise CopilotTokenError(f"Could not save token to OS keychain: {exc}") from exc


def get_token() -> str | None:
    """Return the dedicated Copilot PAT, if one was saved via save_token()."""
    try:
        with keychain_lock():
            keyring = _keyring_module()
            return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception as exc:
        raise CopilotTokenError(f"Could not read token from OS keychain: {exc}") from exc


def github_oauth_token() -> str | None:
    """Return the 'Sign in with GitHub' OAuth access token, if signed in."""
    try:
        from core.auth import github
        return github.get_valid_access_token()
    except Exception:
        return None


def get_effective_token() -> str | None:
    """Return the token the Copilot route should authenticate with.

    Prefers a dedicated Copilot PAT (saved via save_token). Falls back to the
    GitHub OAuth access token from "Sign in with GitHub", so signing into GitHub
    also enables the Copilot route. The OAuth token only works if GitHub has
    granted that token/account Copilot access, but it lets the common case
    (already signed into GitHub) work without a separate PAT.
    """
    try:
        token = get_token()
    except CopilotTokenError:
        token = None
    if token:
        return token
    return github_oauth_token()


def has_effective_token() -> bool:
    """Return whether the Copilot route has any usable token (PAT or GitHub OAuth)."""
    try:
        return bool(get_effective_token())
    except Exception:
        return False


def clear_token() -> None:
    """Clear token."""
    try:
        with keychain_lock():
            keyring = _keyring_module()
            try:
                keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
            except keyring.errors.PasswordDeleteError:
                return
    except Exception as exc:
        raise CopilotTokenError(f"Could not clear token from OS keychain: {exc}") from exc


def token_status() -> tuple[bool, str]:
    """Handle token status for auth copilot auth."""
    token = get_token()
    if not token:
        if github_oauth_token():
            return True, "Using your GitHub sign-in (no separate Copilot token saved)."
        return False, "Not configured"
    ok, message = validate_token_format(token)
    if ok:
        return True, f"Stored in OS keychain. {message}"
    return True, f"Stored, but {message}"
