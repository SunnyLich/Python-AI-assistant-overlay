"""
core/copilot_auth.py - GitHub Copilot token storage.

Fine-grained GitHub PATs with the account permission
"Copilot Requests: Read-only" are sensitive reusable credentials, so this
module stores them only in the OS keychain. It intentionally has no plaintext
fallback file.
"""
from __future__ import annotations

_KEYRING_SERVICE = "python-ai-overlay"
_KEYRING_ACCOUNT = "github-copilot-token"


class CopilotTokenError(RuntimeError):
    """Raised when the Copilot token cannot be stored or read safely."""


def _keyring_module():
    try:
        import keyring  # type: ignore
    except Exception as exc:
        raise CopilotTokenError(f"OS keychain is unavailable: {exc}") from exc
    return keyring


def validate_token_format(token: str) -> tuple[bool, str]:
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
    token = token.strip()
    ok, message = validate_token_format(token)
    if not ok:
        raise CopilotTokenError(message)
    keyring = _keyring_module()
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, token)
    except Exception as exc:
        raise CopilotTokenError(f"Could not save token to OS keychain: {exc}") from exc


def get_token() -> str | None:
    keyring = _keyring_module()
    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception as exc:
        raise CopilotTokenError(f"Could not read token from OS keychain: {exc}") from exc


def clear_token() -> None:
    keyring = _keyring_module()
    try:
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        return
    except Exception as exc:
        raise CopilotTokenError(f"Could not clear token from OS keychain: {exc}") from exc


def token_status() -> tuple[bool, str]:
    token = get_token()
    if not token:
        return False, "Not configured"
    ok, message = validate_token_format(token)
    if ok:
        return True, f"Stored in OS keychain. {message}"
    return True, f"Stored, but {message}"
