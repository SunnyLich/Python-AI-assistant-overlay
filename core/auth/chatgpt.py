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
import html
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
import logging
from urllib.parse import urlencode, parse_qs, urlparse

from core.system.native_locks import keychain_lock

log = logging.getLogger("wisp.chatgpt_auth")

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
    """Handle generate code verifier for auth chatgpt."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _generate_code_challenge(verifier: str) -> str:
    """Handle generate code challenge for auth chatgpt."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _generate_state() -> str:
    """Handle generate state for auth chatgpt."""
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Token storage — keyring with fallback to a local file
# ---------------------------------------------------------------------------

import pathlib as _pathlib

_TOKEN_FILE = _pathlib.Path(__file__).parent.parent / "private" / ".chatgpt_tokens.json"
_APP_ICON_FILE = _pathlib.Path(__file__).resolve().parents[2] / "assets" / "app.ico"


def _app_icon_data_uri() -> str:
    """Return the bundled Wisp app icon as an embeddable data URI."""
    try:
        return "data:image/x-icon;base64," + base64.b64encode(_APP_ICON_FILE.read_bytes()).decode("ascii")
    except Exception:
        return ""


def _keyring_get() -> str | None:
    """Handle keyring get for auth chatgpt."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            return keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception:
        return None


def _keyring_set(value: str) -> bool:
    """Handle keyring set for auth chatgpt."""
    try:
        with keychain_lock():
            import keyring  # type: ignore
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, value)
        return True
    except Exception:
        return False


def _keyring_delete() -> None:
    """Handle keyring delete for auth chatgpt."""
    try:
        with keychain_lock():
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
    """Handle post form for auth chatgpt."""
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
    """Handle post json for auth chatgpt."""
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
    """Handle exchange code for auth chatgpt."""
    return _post_form(f"{_ISSUER}/oauth/token", {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     _CLIENT_ID,
        "code_verifier": verifier,
    })


def _do_refresh(refresh_token: str) -> dict:
    """Handle do refresh for auth chatgpt."""
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

def _needs_refresh(tokens: dict) -> bool:
    """True if the access token expires within 60 seconds."""
    return tokens.get("expires", 0) < (time.time() + 60) * 1000


# Serializes token refresh. ChatGPT OAuth uses rotating, single-use refresh
# tokens with reuse detection: if two callers refresh with the same refresh
# token at once, OpenAI treats the second as a reuse and invalidates the whole
# credential — forcing a fresh sign-in. The lock ensures only one refresh runs;
# others wait and reuse the freshly-rotated token instead of spending it twice.
_refresh_lock = threading.Lock()


def get_valid_access_token() -> str | None:
    """
    Return a valid access token, transparently refreshing if it is near expiry.
    Returns None if the user is not logged in.
    """
    tokens = get_tokens()
    if not tokens:
        return None
    if not _needs_refresh(tokens):
        return tokens.get("access")

    with _refresh_lock:
        # Re-read under the lock: another caller may have refreshed while we
        # waited, in which case we use their rotated token rather than spending
        # our now-stale refresh token a second time.
        tokens = get_tokens() or tokens
        if not _needs_refresh(tokens):
            return tokens.get("access")
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
        """Drive the browser PKCE OAuth flow on the background thread."""
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
            """Model handler."""
            def log_message(self, *args):   # silence access log
                """Log message."""
                pass

            def do_GET(self):              # noqa: N802
                """Handle do g e t for handler."""
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

                # Diagnostics: record which params came back so a failed sign-in
                # is debuggable. Never log the authorization code value itself.
                present = sorted(qs.keys())
                log.info("ChatGPT OAuth callback received; params=%s", present)

                if error:
                    msg = error_desc or error
                    log.warning("ChatGPT OAuth callback returned error: %s", msg)
                    result.append(("error", msg))
                    self._send_html(_html_error(msg))
                elif not code:
                    # No code and no error: the authorize step never produced one.
                    # Common for managed / SSO / ChatGPT Edu accounts that aren't
                    # entitled to the personal Codex login this flow uses.
                    log.warning("ChatGPT OAuth callback missing code; params=%s", present)
                    result.append((
                        "error",
                        "Sign-in did not return an authorization code. This usually "
                        "means the account isn't eligible for the ChatGPT login "
                        "(managed, SSO, or ChatGPT Edu/Team accounts often aren't) — "
                        "try a personal ChatGPT Plus/Pro account, or use an API key.",
                    ))
                    self._send_html(_html_error("Sign-in did not return an authorization code."))
                elif got_state != state:
                    # A code came back but the state doesn't match ours — typically an
                    # organization/workspace selection step rewrote the redirect.
                    log.warning(
                        "ChatGPT OAuth state mismatch (expected %s…, got %s…)",
                        state[:6], (got_state or "")[:6],
                    )
                    result.append((
                        "error",
                        "Sign-in state did not match — the login likely went through "
                        "an organization/SSO step. Try again and, if prompted, pick "
                        "your personal workspace rather than an institutional one.",
                    ))
                    self._send_html(_html_error("OAuth state mismatch"))
                else:
                    result.append(("code", code))
                    self._send_html(_html_success())
                done.set()

            def _send_html(self, html: str) -> None:
                """Send html."""
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
        """Drive the device-code OAuth flow (poll until authorised) on the background thread."""
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

_HTML_SHELL = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  color-scheme: light;
  --wisp-blue: #2563eb;
  --wisp-blue-soft: #dbeafe;
  --wisp-ink: #0f172a;
  --wisp-muted: #475569;
  --wisp-page: #f8fbff;
  --wisp-white: #ffffff;
}}
* {{ box-sizing: border-box; }}
body {{
  min-height: 100vh;
  margin: 0;
  display: grid;
  place-items: center;
  background:
    radial-gradient(circle at 50% 0%, rgba(37, 99, 235, 0.10), transparent 34rem),
    var(--wisp-page);
  color: var(--wisp-ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
main {{
  width: min(92vw, 27rem);
  padding: 2.75rem 2rem;
  text-align: center;
}}
.icon {{
  width: 4.5rem;
  height: 4.5rem;
  object-fit: contain;
  margin-bottom: 1.5rem;
  filter: drop-shadow(0 1rem 1.5rem rgba(37, 99, 235, 0.18));
}}
.icon-fallback {{
  width: 4.5rem;
  height: 4.5rem;
  margin: 0 auto 1.5rem;
  display: grid;
  place-items: center;
  border-radius: 1.25rem;
  background: var(--wisp-blue);
  color: var(--wisp-white);
  font-size: 2rem;
  font-weight: 750;
  box-shadow: 0 1rem 1.5rem rgba(37, 99, 235, 0.18);
}}
h1 {{
  margin: 0;
  color: var(--wisp-ink);
  font-size: clamp(1.35rem, 5vw, 1.75rem);
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: 0;
}}
p {{
  margin: 0.75rem 0 0;
  color: var(--wisp-muted);
  font-size: 1rem;
  line-height: 1.55;
}}
.error {{
  display: inline-block;
  max-width: 100%;
  margin-top: 1rem;
  padding: 0.65rem 0.85rem;
  border: 1px solid #bfdbfe;
  border-radius: 0.5rem;
  background: var(--wisp-white);
  color: #1d4ed8;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 0.9rem;
}}
</style>
</head>
<body>
<main>
{icon}
{content}
</main>
{script}
</body>
</html>"""


def _html_icon() -> str:
    icon_uri = _app_icon_data_uri()
    if icon_uri:
        return f'<img class="icon" src="{icon_uri}" alt="Wisp">'
    return '<div class="icon-fallback" aria-label="Wisp">W</div>'


def _html_success() -> str:
    """Return the branded OAuth success page."""
    return _HTML_SHELL.format(
        title="Wisp - Authorization Complete",
        icon=_html_icon(),
        content=(
            "<h1>Authorization completed successfully.</h1>\n"
            "<p>You can close this window and return to Wisp.</p>"
        ),
        script="<script>setTimeout(() => window.close(), 2000)</script>",
    )


def _html_error(error: str) -> str:
    """Return the branded OAuth error page."""
    return _HTML_SHELL.format(
        title="Wisp - Authorization Failed",
        icon=_html_icon(),
        content=(
            "<h1>Authorization failed.</h1>\n"
            "<p>Return to Wisp and try signing in again.</p>\n"
            f'<div class="error">{html.escape(error)}</div>'
        ),
        script="",
    )
