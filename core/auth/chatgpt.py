"""
core/chatgpt_auth.py — ChatGPT Pro/Plus OAuth authentication.

Implements PKCE browser flow and device-code flow against OpenAI's auth
endpoints, mirroring the approach used by opencode (MIT licence,
https://github.com/anomalyco/opencode).

Tokens are stored in the OS keychain via keyring — never in plaintext files.

Typical usage
-------------
# Browser flow (opens system browser):
chatgpt_auth.start_browser_login(on_success=..., on_error=...)

# Headless / device-code flow:
chatgpt_auth.start_device_login(on_code=..., on_success=..., on_error=...)

# Obtain a valid (auto-refreshed) access token:
token = chatgpt_auth.get_valid_access_token()   # None if not logged in

# Check login status:
info = chatgpt_auth.get_tokens()   # dict or None
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import urlencode, parse_qs, urlparse

# ---------------------------------------------------------------------------
# Constants (sourced from opencode's built-in Codex plugin, MIT)
# ---------------------------------------------------------------------------

_CLIENT_ID    = "app_EMoamEEZ73f0CkXaXp7hrann"
_ISSUER       = "https://auth.openai.com"
_OAUTH_PORT   = 1455
_REDIRECT_URI = f"http://localhost:{_OAUTH_PORT}/auth/callback"

_KEYRING_SERVICE = "python-ai-overlay"
_KEYRING_ACCOUNT = "chatgpt-oauth"

# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _generate_state() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Token storage — keyring with fallback to a local file
# ---------------------------------------------------------------------------

import pathlib as _pathlib

_TOKEN_FILE = _pathlib.Path(__file__).parent.parent / "private" / ".chatgpt_tokens.json"


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
    """Return stored OAuth tokens dict or None if the user is not logged in."""
    # Try keyring first, then fall back to local file
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
    """Persist tokens — keyring preferred, local file as fallback."""
    serialised = json.dumps(tokens)
    if not _keyring_set(serialised):
        print(
            "[chatgpt_auth] Warning: keyring unavailable — "
            f"OAuth tokens stored in plaintext file: {_TOKEN_FILE}"
        )
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(serialised, encoding="utf-8")


def clear_tokens() -> None:
    """Remove stored tokens."""
    _keyring_delete()
    try:
        _TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Token exchange / refresh
# ---------------------------------------------------------------------------

def _post_form(url: str, params: dict) -> dict:
    import urllib.request
    body = urlencode(params).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _post_json(url: str, payload: dict) -> dict:
    import urllib.request
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    return _post_form(f"{_ISSUER}/oauth/token", {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     _CLIENT_ID,
        "code_verifier": verifier,
    })


def _do_refresh(refresh_token: str) -> dict:
    return _post_form(f"{_ISSUER}/oauth/token", {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     _CLIENT_ID,
    })


def _parse_account_id(raw: dict) -> str | None:
    """Extract the ChatGPT account ID from JWT claims in id_token or access_token."""
    for key in ("id_token", "access_token"):
        token = raw.get(key, "")
        parts = token.split(".")
        if len(parts) != 3:
            continue
        try:
            padding = "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
            account_id = (
                claims.get("chatgpt_account_id")
                or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
                or ((claims.get("organizations") or [{}])[0]).get("id")
            )
            if account_id:
                return account_id
        except Exception:
            pass
    return None


def _tokens_from_raw(raw: dict, existing: dict | None = None) -> dict:
    """Build the internal token dict from a raw token endpoint response."""
    return {
        "access":     raw["access_token"],
        "refresh":    raw.get("refresh_token") or (existing or {}).get("refresh", ""),
        "expires":    int(time.time() * 1000) + raw.get("expires_in", 3600) * 1000,
        "account_id": _parse_account_id(raw) or (existing or {}).get("account_id"),
    }


# ---------------------------------------------------------------------------
# Public token accessors
# ---------------------------------------------------------------------------

def get_valid_access_token() -> str | None:
    """
    Return a valid access token, transparently refreshing if it is near expiry.
    Returns None if the user is not logged in.
    """
    tokens = get_tokens()
    if not tokens:
        return None

    # Refresh if the token expires within 60 seconds
    if tokens.get("expires", 0) < (time.time() + 60) * 1000:
        try:
            raw = _do_refresh(tokens["refresh"])
            tokens = _tokens_from_raw(raw, existing=tokens)
            save_tokens(tokens)
        except Exception as exc:
            print(f"[chatgpt_auth] Token refresh failed: {exc}")
            return None

    return tokens.get("access")


def get_account_id() -> str | None:
    """Return the stored ChatGPT account ID, or None."""
    tokens = get_tokens()
    return tokens.get("account_id") if tokens else None


# ---------------------------------------------------------------------------
# Browser-based PKCE flow
# ---------------------------------------------------------------------------

def start_browser_login(
    on_success: Callable[[dict], None],
    on_error: Callable[[str], None],
) -> None:
    """
    Start the browser-based PKCE OAuth flow in a background daemon thread.

    The system browser is opened to OpenAI's authorize endpoint.
    A temporary HTTP server on port 1455 catches the redirect callback.
    ``on_success(tokens)`` / ``on_error(message)`` are called from that thread.
    """
    def _run() -> None:
        import webbrowser

        verifier  = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        state     = _generate_state()

        auth_params = urlencode({
            "response_type":            "code",
            "client_id":                _CLIENT_ID,
            "redirect_uri":             _REDIRECT_URI,
            "scope":                    "openid profile email offline_access",
            "code_challenge":           challenge,
            "code_challenge_method":    "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state":                    state,
            "originator":               "opencode",
        })
        auth_url = f"{_ISSUER}/oauth/authorize?{auth_params}"

        result: list = []      # filled by the request handler
        done   = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # silence access log
                pass

            def do_GET(self):              # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return

                qs          = parse_qs(parsed.query)
                code        = (qs.get("code")  or [None])[0]
                got_state   = (qs.get("state") or [None])[0]
                error       = (qs.get("error") or [None])[0]
                error_desc  = (qs.get("error_description") or [None])[0]

                if error:
                    msg = error_desc or error
                    result.append(("error", msg))
                    self._send_html(_HTML_ERROR.format(error=msg))
                elif not code or got_state != state:
                    result.append(("error", "Invalid state or missing code"))
                    self._send_html(_HTML_ERROR.format(error="Invalid OAuth callback"))
                else:
                    result.append(("code", code))
                    self._send_html(_HTML_SUCCESS)
                done.set()

            def _send_html(self, html: str) -> None:
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            server = HTTPServer(("localhost", _OAUTH_PORT), _Handler)
        except OSError as exc:
            on_error(f"Cannot bind to port {_OAUTH_PORT}: {exc}")
            return

        webbrowser.open(auth_url)
        server.timeout = 300   # 5-minute timeout
        deadline = time.time() + 300
        while not done.is_set() and time.time() < deadline:
            server.handle_request()
        server.server_close()

        if not result:
            on_error("OAuth flow timed out — no callback received within 5 minutes")
            return

        kind, value = result[0]
        if kind == "error":
            on_error(value)
            return

        try:
            raw    = _exchange_code(value, verifier, _REDIRECT_URI)
            tokens = _tokens_from_raw(raw)
            save_tokens(tokens)
            on_success(tokens)
        except Exception as exc:
            on_error(f"Token exchange failed: {exc}")

    threading.Thread(target=_run, daemon=True, name="chatgpt-oauth-browser").start()


# ---------------------------------------------------------------------------
# Device-code flow (headless / no-browser)
# ---------------------------------------------------------------------------

def start_device_login(
    on_code:    Callable[[str, str], None],   # (verify_url, user_code)
    on_success: Callable[[dict], None],
    on_error:   Callable[[str], None],
) -> None:
    """
    Start the device-code OAuth flow in a background daemon thread.

    ``on_code(url, user_code)`` is called first so the caller can show the
    user where to go and what code to enter.  The thread then polls until
    the user authorises, then calls ``on_success`` or ``on_error``.
    """
    _POLLING_SAFETY_MS = 3000  # extra margin on top of the server-supplied interval

    def _run() -> None:
        try:
            device = _post_json(
                f"{_ISSUER}/api/accounts/deviceauth/usercode",
                {"client_id": _CLIENT_ID},
            )
        except Exception as exc:
            on_error(f"Device auth initiation failed: {exc}")
            return

        on_code(f"{_ISSUER}/codex/device", device["user_code"])

        interval_ms = max(int(device.get("interval", 5)), 1) * 1000 + _POLLING_SAFETY_MS

        while True:
            time.sleep(interval_ms / 1000)
            try:
                data = _post_json(
                    f"{_ISSUER}/api/accounts/deviceauth/token",
                    {
                        "device_auth_id": device["device_auth_id"],
                        "user_code":      device["user_code"],
                    },
                )
                # Server responded 200 — exchange the returned auth code
                raw = _exchange_code(
                    data["authorization_code"],
                    data["code_verifier"],
                    f"{_ISSUER}/deviceauth/callback",
                )
                tokens = _tokens_from_raw(raw)
                save_tokens(tokens)
                on_success(tokens)
                return
            except Exception:
                # 403/404 == still pending; keep polling
                pass

    threading.Thread(target=_run, daemon=True, name="chatgpt-oauth-device").start()


# ---------------------------------------------------------------------------
# HTML templates for the redirect page
# ---------------------------------------------------------------------------

_HTML_SUCCESS = """\
<!doctype html><html><head><title>Login Successful</title><style>
body{{background:#131010;color:#f1ecec;font-family:system-ui;
     display:flex;justify-content:center;align-items:center;height:100vh;margin:0}}
.box{{text-align:center;padding:2rem}}h1{{color:#f1ecec}}p{{color:#b7b1b1}}
</style></head><body><div class="box">
<h1>Authorization Successful</h1>
<p>You can close this window and return to the app.</p>
</div><script>setTimeout(()=>window.close(),2000)</script></body></html>"""

_HTML_ERROR = """\
<!doctype html><html><head><title>Login Failed</title><style>
body{{background:#131010;color:#f1ecec;font-family:system-ui;
     display:flex;justify-content:center;align-items:center;height:100vh;margin:0}}
.box{{text-align:center;padding:2rem}}h1{{color:#fc533a}}
.err{{font-family:monospace;background:#3c140d;padding:.5rem 1rem;border-radius:4px;margin-top:1rem}}
</style></head><body><div class="box">
<h1>Authorization Failed</h1><div class="err">{error}</div>
</div></body></html>"""
