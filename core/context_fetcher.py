"""
core/context_fetcher.py — Ambient context collection.

Gathers context from multiple sources and writes a JSON snapshot to a
stable temporary file so any part of the app (or the LLM prompt builder)
can read it without re-fetching.

Sources implemented (Windows):
  • Active Window    — title, process name, exe path, browser URL
  • Clipboard        — plain text currently on the clipboard (redacted)
  • UI Automation    — focused element: name, value, control type, window title
  • Recent Files     — Windows %APPDATA%\\Microsoft\\Windows\\Recent
  • File System Events — background watchdog watcher on Desktop/Documents/Downloads
  • Browser Content  — fetches & parses the current browser page as plain text (redacted)
  • Online Search    — DuckDuckGo text search, no API key required (redacted)

Optional:
  • Screen Capture   — enable by passing capture_screen=True to fetch_and_save()

All text written to the snapshot is passed through a redaction filter that
removes credit-card numbers, SSNs, API keys, bearer tokens, private keys,
and password/secret assignments before they reach disk.

Usage:
    from core.context_fetcher import fetch_and_save, load_latest, get_temp_path, start_fs_watcher

    start_fs_watcher()                          # call once at app startup
    snapshot = fetch_and_save()                 # fetch + write to disk
    snapshot = fetch_and_save(
        online_query="what is asyncio")          # also search online
    data     = load_latest()                    # read last saved snapshot as dict
    path     = get_temp_path()                  # path of the JSON file
"""

from __future__ import annotations

import config
import json
import os
import re
import sys
import tempfile
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Optional
from urllib.parse import urlparse, unquote

from core.system import macos_safety
from core.system.native_locks import ssl_init_lock
from core.system import sdk_clients

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"

# ---------------------------------------------------------------------------
# Snapshot data classes
# ---------------------------------------------------------------------------

@dataclass
class WindowInfo:
    title: str = ""
    process_name: str = ""
    pid: int = 0
    exe_path: str = ""
    url: str = ""          # browser address-bar URL if detectable
    hwnd: int = 0          # native window handle (Windows) — lets us read the page text locally


@dataclass
class ClipboardInfo:
    text: str = ""
    fmt: str = "empty"     # "text" | "image" | "other" | "empty"


@dataclass
class UIElementInfo:
    name: str = ""
    value: str = ""
    control_type: str = ""
    class_name: str = ""
    window_title: str = ""


@dataclass
class ContextSnapshot:
    timestamp: float = 0.0
    active_window: WindowInfo = field(default_factory=WindowInfo)
    clipboard: ClipboardInfo = field(default_factory=ClipboardInfo)
    ui_focused: UIElementInfo = field(default_factory=UIElementInfo)
    recent_files: list[str] = field(default_factory=list)
    fs_events: list[str] = field(default_factory=list)            # recent file-system changes
    browser_content: str = ""                                     # current browser page text (redacted)
    online_results: list[dict] = field(default_factory=list)      # [{title, url, snippet}]
    # Populated only when capture_screen=True is passed to fetch_and_save()
    screen_capture_path: str = ""


# ---------------------------------------------------------------------------
# Sensitive-data redaction
# ---------------------------------------------------------------------------

_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Credit / debit card numbers (13-19 digits, optionally separated)
    (re.compile(r'\b(?:\d[ \-]?){13,19}\b'), "[CARD_NUMBER]"),
    # Social Security Numbers
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), "[SSN]"),
    # PEM private keys (RSA, EC, DSA, OpenSSH)
    (re.compile(
        r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----.*?'
        r'-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
        re.DOTALL,
    ), "[PRIVATE_KEY]"),
    # OpenAI-style API keys  sk-…  and project keys  sk-proj-…
    (re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9]{20,}\b'), "[API_KEY]"),
    # Anthropic  sk-ant-…
    (re.compile(r'\bsk-ant-[A-Za-z0-9\-_]{20,}\b'), "[API_KEY]"),
    # Bearer tokens in Authorization headers / config
    (re.compile(r'(?i)\bBearer\s+[A-Za-z0-9\-_.~+/=]{20,}'), "[BEARER_TOKEN]"),
    # Generic long hex / base64 tokens that look like secrets (32+ chars)
    (re.compile(
        r'(?i)(?:token|api[_\-]?key|access[_\-]?key|secret[_\-]?key|client[_\-]?secret)'
        r'[\s]*[:=][\s]*(?:'
        r'[\'"][A-Za-z0-9\-_./+]{20,}[\'"]'
        r'|'
        r'[A-Za-z0-9\-_./+]{32,}(?=$|[\s,#;\]}])'
        r')'
    ), "[API_KEY]"),
    # password / passwd / secret assignments  (key: value  or  key=value)
    (re.compile(r'(?i)(?:password|passwd|pwd|secret)\s*[:=]\s*\S+'), "[REDACTED_CREDENTIAL]"),
]


def _redact(text: str) -> str:
    """Remove sensitive patterns from *text* before it is written to disk."""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Stable temp file path
# ---------------------------------------------------------------------------

# Model used for the web-search call.  Haiku does not invoke web search;
# Sonnet is the cheapest tier that actually uses the tool.
# Override with SEARCH_LLM_MODEL in .env if needed.
_SEARCH_MODEL = os.getenv("SEARCH_LLM_MODEL", "claude-sonnet-4-5")
# web_search_20250305: basic search, works on all capable models.
# web_search_20260209: adds dynamic filtering (requires code-execution tool
#   enabled in your org and a Sonnet 4.6 / Opus 4.6+ model).
_WEB_SEARCH_TOOL_TYPE = os.getenv("WEB_SEARCH_TOOL_TYPE", "web_search_20250305")

_TEMP_FILE = os.path.join(tempfile.gettempdir(), "ai_assistant_context.json")


def get_temp_path() -> str:
    """Return the path of the JSON snapshot file."""
    return _TEMP_FILE


# ---------------------------------------------------------------------------
# Source: File System Events  (background watchdog watcher)
# ---------------------------------------------------------------------------

_fs_events_buf: deque[str] = deque(maxlen=30)
_fs_events_lock = Lock()
_fs_observer = None   # watchdog Observer | False
_context_window: "WindowInfo | None" = None  # window captured at last fetch_and_save()


def start_fs_watcher(paths: list[str] | None = None) -> None:
    """
    Start the background file-system watcher.  Safe to call multiple times.
    By default watches ~/Desktop, ~/Documents, ~/Downloads recursively.
    Call once from app startup (e.g. main.py __init__).
    """
    global _fs_observer
    if _fs_observer is not None:
        return
    if not macos_safety.fs_watcher_enabled():
        _fs_observer = False
        print("[context_fetcher] fs watcher disabled in macOS safe mode.")
        return

    try:
        if sys.platform == "darwin":
            # Avoid watchdog's native FSEvents backend in this Qt/PyObjC process.
            # It spins native callback threads (_watchdog_fsevents), which showed
            # up in repeated macOS segfault dumps. Polling is slower but keeps this
            # optional ambient-context feature in pure Python.
            from watchdog.observers.polling import PollingObserver as Observer  # type: ignore
            observer_backend = "polling"
        else:
            from watchdog.observers import Observer  # type: ignore
            observer_backend = "native"
        from watchdog.events import FileSystemEventHandler  # type: ignore

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                with _fs_events_lock:
                    _fs_events_buf.append(event.src_path)

        if paths is None:
            home = os.path.expanduser("~")
            candidates = [
                os.path.join(home, "Desktop"),
                os.path.join(home, "Documents"),
                os.path.join(home, "Downloads"),
            ]
            paths = [p for p in candidates if os.path.isdir(p)]

        if not paths:
            _fs_observer = False
            return

        handler = _Handler()
        observer = Observer()
        for path in paths:
            observer.schedule(handler, path, recursive=True)
        observer.daemon = True
        observer.start()
        _fs_observer = observer
        print(f"[context_fetcher] fs watcher started on {len(paths)} path(s) ({observer_backend}).")

    except Exception as exc:
        print(f"[context_fetcher] fs watcher failed to start: {exc}")
        _fs_observer = False


def _get_fs_events() -> list[str]:
    with _fs_events_lock:
        return list(_fs_events_buf)


# ---------------------------------------------------------------------------
# Source: Browser page content
# ---------------------------------------------------------------------------

_PRIVATE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PRIVATE_PREFIXES = ("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                     "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                     "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                     "172.29.", "172.30.", "172.31.")


def _fetch_browser_content(url: str, max_chars: int | None = None) -> str:
    """
    Fetch the plain text of a public web page.
    Skips non-HTTP, localhost, and private-network URLs.
    JavaScript-heavy SPAs will return minimal content (only static HTML).
    All output is passed through the sensitive-data redactor.
    """
    if max_chars is None:
        max_chars = config.CONTEXT_BROWSER_MAX_CHARS

    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        return ""

    host = urlparse(url).hostname or ""
    if host in _PRIVATE_HOSTS or any(host.startswith(p) for p in _PRIVATE_PREFIXES):
        return ""

    try:
        import requests                        # type: ignore
        from bs4 import BeautifulSoup          # type: ignore

        resp = requests.get(
            url,
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AI-assistant-overlay/1.0)"},
            allow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Strip boilerplate tags
        for tag in soup(["script", "style", "nav", "header",
                         "footer", "aside", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        # Collapse runs of whitespace
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        text = text[:max_chars]

        return _redact(text)

    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Source: Online search  (Anthropic web search tool, charges via ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

def _search_online(query: str, max_results: int = 5) -> list[dict]:
    """
    Use the Anthropic web search tool to find up to *max_results* hits for
    *query* and return them as [{"title", "url", "snippet"}].

    Charges through ANTHROPIC_API_KEY at ~$10 / 1 000 searches plus token
    costs.  Returns [] immediately if the key is not configured or on any
    failure.

    Response structure parsed here:
      • web_search_tool_result blocks  → raw {url, title} from Anthropic
      • text blocks with citations     → {cited_text} snippets (≤150 chars)
    Both are merged by URL and passed through the sensitive-data redactor.
    """
    if not query or not query.strip():
        return []
    if not config.ANTHROPIC_API_KEY:
        print("[context_fetcher] ANTHROPIC_API_KEY not set — online search skipped.")
        return []

    try:
        with ssl_init_lock():
            client = sdk_clients.anthropic_client(api_key=config.ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=_SEARCH_MODEL,
            max_tokens=1024,
            tools=[{
                "type": _WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": 2,           # cap at 2 search queries per call
            }],
            messages=[{"role": "user", "content": query.strip()}],
        )

        # Collect results preserving insertion order (dict, Python 3.7+)
        results: dict[str, dict] = {}   # url → {title, url, snippet}

        for block in response.content:
            btype = getattr(block, "type", "")

            # Raw search results list returned by Anthropic infrastructure
            if btype == "web_search_tool_result":
                for item in (getattr(block, "content", None) or []):
                    if getattr(item, "type", "") == "web_search_result":
                        url   = (getattr(item, "url",   "") or "").strip()
                        title = (getattr(item, "title", "") or "").strip()
                        if url and url not in results:
                            results[url] = {
                                "title":   _redact(title),
                                "url":     url,
                                "snippet": "",
                            }

            # Text blocks carry inline citations with short cited_text snippets
            elif btype == "text":
                for citation in (getattr(block, "citations", None) or []):
                    url        = (getattr(citation, "url",        "") or "").strip()
                    title      = (getattr(citation, "title",      "") or "").strip()
                    cited_text = (getattr(citation, "cited_text", "") or "").strip()
                    if url:
                        if url not in results:
                            results[url] = {
                                "title":   _redact(title),
                                "url":     url,
                                "snippet": "",
                            }
                        if cited_text and not results[url]["snippet"]:
                            results[url]["snippet"] = _redact(cited_text)

        return list(results.values())[:max_results]

    except Exception as exc:
        print(f"[context_fetcher] online search failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Source: Active Window
# ---------------------------------------------------------------------------

_BROWSER_PROCS_WIN = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "arc.exe",
})

_BROWSER_PROCS_LINUX = frozenset({
    "chrome", "chromium", "chromium-browser", "firefox",
    "firefox-esr", "brave", "brave-browser", "opera", "vivaldi",
})

# macOS reports the application's localized name (NSRunningApplication.localizedName
# / System Events process name), e.g. "Safari", "Google Chrome", "Brave Browser".
# These also double as the AppleScript `tell application "<name>"` targets.
_BROWSER_PROCS_MAC = frozenset({
    "safari", "safari technology preview",
    "google chrome", "google chrome canary", "google chrome beta",
    "chromium", "brave browser", "microsoft edge", "microsoft edge beta",
    "arc", "vivaldi", "opera", "firefox",
})

# Safari-family speaks `current tab`; Chromium-family speaks `active tab` + JS.
_MAC_SAFARI_FAMILY = frozenset({"safari", "safari technology preview"})
_MAC_CHROME_FAMILY = frozenset({
    "google chrome", "google chrome canary", "google chrome beta",
    "chromium", "brave browser", "microsoft edge", "microsoft edge beta",
    "arc", "vivaldi", "opera",
})

if _IS_WIN:
    _BROWSER_PROCS = _BROWSER_PROCS_WIN
elif _IS_MAC:
    _BROWSER_PROCS = _BROWSER_PROCS_MAC
else:
    _BROWSER_PROCS = _BROWSER_PROCS_LINUX


def _osascript_run(script: str, timeout: float = 4.0) -> str:
    """Run a one-liner AppleScript and return trimmed stdout ("" on any error)."""
    import subprocess
    try:
        proc = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
    except Exception:
        pass
    return ""


def _mac_browser_url(app_name: str) -> str:
    """Active-tab URL of the named macOS browser via AppleScript ("" if none)."""
    fam = (app_name or "").strip().lower()
    if fam in _MAC_SAFARI_FAMILY:
        return _osascript_run(
            f'tell application "{app_name}" to return URL of current tab of front window'
        )
    if fam in _MAC_CHROME_FAMILY:
        return _osascript_run(
            f'tell application "{app_name}" to return URL of active tab of front window'
        )
    return ""


def _mac_browser_text(app_name: str, max_chars: int) -> str:
    """Active-tab page text of the named macOS browser via AppleScript.

    Safari exposes `text of current tab` directly (needs only Automation
    permission). Chromium-family browsers require `execute ... javascript`,
    which additionally needs "Allow JavaScript from Apple Events" enabled; when
    that is off the call simply returns "" and the URL alone is used.
    """
    fam = (app_name or "").strip().lower()
    raw = ""
    if fam in _MAC_SAFARI_FAMILY:
        raw = _osascript_run(
            f'tell application "{app_name}" to return text of current tab of front window',
            timeout=8.0,
        )
    elif fam in _MAC_CHROME_FAMILY:
        raw = _osascript_run(
            f'tell application "{app_name}" to return execute active tab of front window '
            f'javascript "document.body.innerText"',
            timeout=8.0,
        )
    if not raw:
        return ""
    text = re.sub(r"[ \t]+", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars]
    return _redact(text)


def _fetch_active_window() -> WindowInfo:
    if _IS_WIN:
        return _fetch_active_window_win()
    if _IS_MAC:
        return _fetch_active_window_macos()
    return _fetch_active_window_linux()


def _fetch_active_window_win() -> WindowInfo:
    import ctypes
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return WindowInfo()
        return _fetch_window_info_win(int(hwnd))
    except Exception:
        return WindowInfo()

def _fetch_window_info_win(hwnd: int) -> WindowInfo:
    """Build WindowInfo for a specific Windows HWND without requiring focus."""
    import ctypes
    import ctypes.wintypes

    info = WindowInfo()
    if not hwnd:
        return info
    try:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return info
        info.hwnd = int(hwnd)

        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        info.title = buf.value.strip()

        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info.pid = pid.value
        try:
            import psutil
            proc = psutil.Process(pid.value)
            info.process_name = proc.name()
            try:
                info.exe_path = proc.exe()
            except (psutil.AccessDenied, Exception):
                pass
        except Exception:
            pass

        if info.process_name.lower() in _BROWSER_PROCS:
            info.url = _get_browser_url_uia(hwnd) or ""

    except Exception:
        pass

    return info


def get_browser_window_for_context(preferred_hwnd: int = 0) -> WindowInfo:
    """Return the preferred/first visible browser window for Browser/Web context.

    Browser/Web set to "On" should mean "include browser context" even when the
    foreground target is a document. Prefer the hotkey-time foreground window
    when it is a browser, otherwise scan visible browser windows.
    """
    if _IS_WIN:
        if preferred_hwnd:
            preferred = _fetch_window_info_win(int(preferred_hwnd))
            if (preferred.process_name or "").lower() in _BROWSER_PROCS:
                return preferred
        return _find_visible_browser_window_win()

    active = _fetch_active_window()
    if (active.process_name or "").lower() in _BROWSER_PROCS:
        if _IS_MAC and not active.url:
            active.url = _mac_browser_url(active.process_name)
        return active
    return WindowInfo()


def _find_visible_browser_window_win() -> WindowInfo:
    if not _IS_WIN:
        return WindowInfo()
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    results: list[WindowInfo] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            win = _fetch_window_info_win(int(hwnd))
            if (win.process_name or "").lower() in _BROWSER_PROCS:
                results.append(win)
                if win.url:
                    return False
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_callback), 0)
    except Exception:
        return WindowInfo()
    if not results:
        return WindowInfo()
    with_url = [win for win in results if win.url]
    return with_url[0] if with_url else results[0]


def _fetch_active_window_linux() -> WindowInfo:
    from core.platform_utils import get_foreground_window, get_window_title, get_window_pid
    info = WindowInfo()
    try:
        wid = get_foreground_window()
        if not wid:
            return info
        info.title = get_window_title(wid)
        pid = get_window_pid(wid)
        info.pid = pid
        if pid:
            try:
                import psutil
                proc = psutil.Process(pid)
                info.process_name = proc.name()
                try:
                    info.exe_path = proc.exe()
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    return info


def _fetch_active_window_macos() -> WindowInfo:
    info = WindowInfo()
    try:
        from core.platform import macos_native

        rows = macos_native.list_document_windows()
        rows.sort(key=lambda row: (not bool(row.get("frontmost")), str(row.get("title") or "")))
        for row in rows:
            if not bool(row.get("frontmost")):
                continue
            title = str(row.get("title") or "").strip()
            process_name = str(row.get("process_name") or "").strip()
            if not title or not process_name:
                continue
            info.title = title
            info.process_name = process_name
            try:
                info.pid = int(row.get("pid") or 0)
            except Exception:
                info.pid = 0
            return info
    except Exception:
        pass
    try:
        from core.platform_utils import get_foreground_window, get_window_pid, get_window_title

        wid = get_foreground_window()
        if not wid:
            return info
        info.hwnd = int(wid)
        info.title = get_window_title(wid)
        info.pid = int(get_window_pid(wid) or 0)
        if info.pid:
            try:
                import psutil

                info.process_name = psutil.Process(info.pid).name()
            except Exception:
                pass
    except Exception:
        pass
    return info


def _get_browser_url_uia(hwnd: int) -> str | None:
    """Walk the UIA tree of a browser window to find the address-bar URL (Windows only)."""
    if not _IS_WIN:
        return None
    try:
        import comtypes.client
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        uia = _get_uia()
        if uia is None:
            return None

        root = uia.ElementFromHandle(hwnd)
        if root is None:
            return None

        # Find all Edit controls (ControlType == 50004) under the window
        condition = uia.CreatePropertyCondition(
            30003,  # UIA_ControlTypePropertyId
            50004,  # UIA_EditControlTypeId
        )
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore
        el = root.FindFirst(uiac.TreeScope_Descendants, condition)
        if el is None:
            return None

        raw = el.GetCurrentPattern(10002)  # UIA_ValuePatternId
        if raw is None:
            return None

        import comtypes
        vp = raw.QueryInterface(uiac.IUIAutomationValuePattern)
        val = (vp.CurrentValue or "").strip()
        if val.startswith(("http://", "https://", "file://", "ftp://")):
            return val
        return None

    except Exception:
        return None


def _get_browser_text_uia(hwnd: int, max_chars: int) -> str | None:
    """Read the rendered page text from a browser window via UIA (Windows only).

    Reads what the browser actually shows — including JavaScript-rendered content
    the HTTP fetch misses — straight from the open window, with no network round
    trip. ``GetText(max_chars)`` caps how much is materialized so huge pages stay
    fast."""
    if not _IS_WIN:
        return None
    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        uia = _get_uia()
        if uia is None:
            return None
        root = uia.ElementFromHandle(hwnd)
        if root is None:
            return None
        # The page content is exposed as a Document control under the window.
        condition = uia.CreatePropertyCondition(
            30003,  # UIA_ControlTypePropertyId
            50030,  # UIA_DocumentControlTypeId
        )
        el = root.FindFirst(uiac.TreeScope_Descendants, condition)
        if el is None:
            return None
        raw = el.GetCurrentPattern(10014)  # UIA_TextPatternId
        if raw is None:
            return None
        tp = raw.QueryInterface(uiac.IUIAutomationTextPattern)
        text = tp.DocumentRange.GetText(max_chars if max_chars and max_chars > 0 else -1)
        return (text or "").strip() or None
    except Exception:
        return None


def _get_window_text_uia(hwnd: int, max_chars: int) -> str | None:
    """Read text from a document/editor window by handle on Windows.

    Some apps, notably modern Notepad, do not reliably expose their backing file
    path to the process open-file list. UIA can still expose the visible editor
    content through Document or Edit controls, which is enough for context.
    """
    if not _IS_WIN or not hwnd:
        return None
    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        uia = _get_uia()
        if uia is None:
            return None
        root = uia.ElementFromHandle(hwnd)
        if root is None:
            return None

        parts: list[str] = []
        seen: set[str] = set()
        for control_type in (50030, 50004):  # Document, Edit
            condition = uia.CreatePropertyCondition(
                30003,  # UIA_ControlTypePropertyId
                control_type,
            )
            elements = root.FindAll(uiac.TreeScope_Descendants, condition)
            for idx in range(getattr(elements, "Length", 0) or 0):
                el = elements.GetElement(idx)
                text = ""
                try:
                    raw = el.GetCurrentPattern(_UIA_TextPatternId)
                    if raw is not None:
                        tp = raw.QueryInterface(uiac.IUIAutomationTextPattern)
                        text = tp.DocumentRange.GetText(max_chars if max_chars and max_chars > 0 else -1)
                except Exception:
                    text = ""
                if not text:
                    try:
                        raw = el.GetCurrentPattern(_UIA_ValuePatternId)
                        if raw is not None:
                            vp = raw.QueryInterface(uiac.IUIAutomationValuePattern)
                            text = vp.CurrentValue or ""
                    except Exception:
                        text = ""
                text = re.sub(r"\r\n?", "\n", text or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                parts.append(text)
                if sum(len(part) for part in parts) >= max_chars:
                    break
            if parts:
                break
        combined = "\n\n".join(parts).strip()
        if not combined:
            return None
        combined = combined[:max_chars] if max_chars and max_chars > 0 else combined
        return _redact(combined)
    except Exception:
        return None


# Per-URL page-text cache so repeated questions about the same page are instant
# instead of re-reading/re-fetching it each time.
_browser_cache: dict[str, tuple[float, str]] = {}
_BROWSER_CACHE_TTL = 60.0  # seconds


def _browser_content(active_win: WindowInfo, max_chars: int | None = None) -> str:
    """Active tab's page text: read the live window first (fast, JS-aware), fall
    back to an HTTP fetch of the URL, and cache the result per-URL briefly."""
    if max_chars is None:
        max_chars = config.CONTEXT_BROWSER_MAX_CHARS

    if _IS_MAC:
        # No read-by-handle on macOS — ask the named browser app for its active
        # tab text directly (works even when the overlay holds focus, since
        # AppleScript targets the app's own front window).
        app = (active_win.process_name or "").strip()
        if app.lower() in _BROWSER_PROCS_MAC:
            return _mac_browser_text(app, max_chars)
        return ""

    url = active_win.url or ""

    now = time.time()
    cached = _browser_cache.get(url)
    if cached and now - cached[0] < _BROWSER_CACHE_TTL:
        return cached[1]

    content = ""
    # 1. Local read of the rendered window — no network.
    if active_win.hwnd:
        raw = _get_browser_text_uia(active_win.hwnd, max_chars)
        if raw:
            text = re.sub(r"[ \t]+", " ", raw)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars]
            content = _redact(text)
    # 2. Fall back to re-fetching the URL if the window read was empty/too short.
    if len(content) < 200:
        http = _fetch_browser_content(url, max_chars)
        if len(http) > len(content):
            content = http

    if url and content:
        _browser_cache[url] = (now, content)
    return content


# ---------------------------------------------------------------------------
# Source: Clipboard
# ---------------------------------------------------------------------------

_CF_UNICODETEXT = 13
_CF_BITMAP = 2
_CF_DIB = 8


def _fetch_clipboard() -> ClipboardInfo:
    if _IS_WIN:
        return _fetch_clipboard_win()
    if _IS_MAC:
        return _fetch_clipboard_macos()
    return _fetch_clipboard_linux()


def _fetch_clipboard_win() -> ClipboardInfo:
    info = ClipboardInfo()
    try:
        import win32clipboard  # type: ignore

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                raw = win32clipboard.GetClipboardData(_CF_UNICODETEXT)
                info.text = _redact((raw or "").strip())
                info.fmt = "text"
            elif win32clipboard.IsClipboardFormatAvailable(_CF_BITMAP) or \
                 win32clipboard.IsClipboardFormatAvailable(_CF_DIB):
                info.fmt = "image"
            else:
                info.fmt = "other"
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        info.fmt = "empty"
    return info


def _fetch_clipboard_linux() -> ClipboardInfo:
    info = ClipboardInfo()
    try:
        import pyperclip  # type: ignore
        text = pyperclip.paste()
        if text:
            info.text = _redact(text.strip())
            info.fmt = "text"
        else:
            info.fmt = "empty"
    except Exception:
        info.fmt = "empty"
    return info


def _fetch_clipboard_macos() -> ClipboardInfo:
    info = ClipboardInfo()
    try:
        from core.platform import macos_native

        text = macos_native.get_clipboard_text()
        if text:
            info.text = _redact(text.strip())
            info.fmt = "text"
        else:
            info.fmt = "empty"
    except Exception:
        info.fmt = "empty"
    return info


# ---------------------------------------------------------------------------
# Source: UI Automation — focused element
# ---------------------------------------------------------------------------

_UIA_ValuePatternId = 10002
_UIA_TextPatternId  = 10014

_CONTROL_TYPES: dict[int, str] = {
    50000: "Button",     50001: "Calendar",    50002: "CheckBox",
    50003: "ComboBox",   50004: "Edit",        50005: "Hyperlink",
    50006: "Image",      50007: "ListItem",    50008: "List",
    50009: "Menu",       50010: "MenuBar",     50011: "MenuItem",
    50012: "ProgressBar",50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider",     50016: "Spinner",     50017: "StatusBar",
    50018: "Tab",        50019: "TabItem",     50020: "Text",
    50021: "ToolBar",    50022: "ToolTip",     50023: "Tree",
    50024: "TreeItem",   50025: "Custom",      50026: "Group",
    50027: "Thumb",      50028: "DataGrid",    50029: "DataItem",
    50030: "Document",   50031: "SplitButton", 50032: "Window",
    50033: "Pane",       50034: "Header",      50035: "HeaderItem",
    50036: "Table",      50037: "TitleBar",    50038: "Separator",
}

_uia_singleton = None   # IUIAutomation | False


def _get_uia():
    """Return a cached IUIAutomation instance (or None if unavailable)."""
    global _uia_singleton
    if _uia_singleton is None:
        try:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as uiac  # type: ignore
            _uia_singleton = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=uiac.IUIAutomation,
            )
        except Exception:
            _uia_singleton = False

    return _uia_singleton if _uia_singleton is not False else None


def _fetch_ui_focused() -> UIElementInfo:
    info = UIElementInfo()
    if not _IS_WIN:
        return info
    uia = _get_uia()
    if uia is None:
        return info

    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore

        el = uia.GetFocusedElement()
        if el is None:
            return info

        # Name
        try:
            info.name = (el.CurrentName or "").strip()
        except Exception:
            pass

        # Class name
        try:
            info.class_name = (el.CurrentClassName or "").strip()
        except Exception:
            pass

        # Control type
        try:
            ct = el.CurrentControlType
            info.control_type = _CONTROL_TYPES.get(ct, str(ct))
        except Exception:
            pass

        # Containing window title — walk up UIA tree
        try:
            walker = uia.ControlViewWalker
            parent = walker.GetParentElement(el)
            depth = 0
            while parent and depth < 20:
                try:
                    if parent.CurrentControlType == 50032:  # Window
                        info.window_title = (parent.CurrentName or "").strip()
                        break
                except Exception:
                    break
                parent = walker.GetParentElement(parent)
                depth += 1
        except Exception:
            pass

        # Value (Edit / ComboBox / address bars)
        try:
            raw = el.GetCurrentPattern(_UIA_ValuePatternId)
            if raw is not None:
                vp = raw.QueryInterface(uiac.IUIAutomationValuePattern)
                info.value = _redact((vp.CurrentValue or "").strip())
        except Exception:
            pass

        # Text selection — try if value is empty
        if not info.value:
            try:
                raw = el.GetCurrentPattern(_UIA_TextPatternId)
                if raw is not None:
                    tp = raw.QueryInterface(uiac.IUIAutomationTextPattern)
                    sels = tp.GetSelection()
                    if sels.Length > 0:
                        info.value = _redact((sels.GetElement(0).GetText(-1) or "").strip())
            except Exception:
                pass

    except Exception:
        pass

    return info


# ---------------------------------------------------------------------------
# Source: Recent Files (Windows Recent folder, max 10)
# ---------------------------------------------------------------------------

def _fetch_recent_files(max_files: int = 10) -> list[str]:
    return _fetch_recent_files_win(max_files) if _IS_WIN else _fetch_recent_files_linux(max_files)


def _fetch_recent_files_win(max_files: int = 10) -> list[str]:
    """Return recently touched files from %APPDATA%\\Microsoft\\Windows\\Recent."""
    results: list[str] = []
    try:
        recent_dir = os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Recent"
        )
        if not os.path.isdir(recent_dir):
            return results

        entries: list[tuple[float, str]] = []
        for name in os.listdir(recent_dir):
            if not name.lower().endswith(".lnk"):
                continue
            full = os.path.join(recent_dir, name)
            try:
                entries.append((os.path.getmtime(full), full))
            except OSError:
                pass

        entries.sort(reverse=True)

        shell = None
        try:
            import win32com.client  # type: ignore
            shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            pass

        for _, lnk_path in entries[:max_files]:
            if shell is not None:
                try:
                    shortcut = shell.CreateShortcut(lnk_path)
                    target = (shortcut.TargetPath or "").strip()
                    if target:
                        results.append(target)
                        continue
                except Exception:
                    pass
            results.append(os.path.splitext(os.path.basename(lnk_path))[0])

    except Exception:
        pass

    return results


def _fetch_recent_files_linux(max_files: int = 10) -> list[str]:
    """Return recently touched files from ~/.local/share/recently-used.xbel."""
    import xml.etree.ElementTree as ET

    xbel = os.path.expanduser("~/.local/share/recently-used.xbel")
    if not os.path.isfile(xbel):
        return []
    results: list[str] = []
    try:
        tree = ET.parse(xbel)
        root = tree.getroot()
        bookmarks: list[tuple[str, str]] = []
        for bm in root.iter("bookmark"):
            href = bm.get("href", "")
            visited = bm.get("visited") or bm.get("modified") or ""
            if href.startswith("file://"):
                path = unquote(href[7:])
                if os.path.isfile(path):
                    bookmarks.append((visited, path))
        bookmarks.sort(reverse=True)
        results = [p for _, p in bookmarks[:max_files]]
    except Exception:
        pass
    return results


# ---------------------------------------------------------------------------
# Source: Screen Capture (opt-in)
# ---------------------------------------------------------------------------

def _capture_screen_to_file() -> str:
    """
    Take a full-screen screenshot and save it as a PNG next to the JSON
    snapshot.  Returns the path, or "" on failure.
    """
    out_path = os.path.join(tempfile.gettempdir(), "ai_assistant_screen.png")
    if sys.platform == "darwin":
        try:
            from core.platform import macos_native

            return out_path if macos_native.capture_screen_to_file(out_path) else ""
        except Exception:
            return ""

    try:
        import mss
        import mss.tools
        from core.system.main_thread import run_on_main

        def _grab() -> None:
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # primary monitor
                raw = sct.grab(monitor)
                mss.tools.to_png(raw.rgb, raw.size, output=out_path)

        run_on_main(_grab)
        return out_path
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def fetch_and_save(
    capture_screen: bool = False,
    fetch_browser_content: bool = False,  # opt-in; now driven by LLM tools instead
    online_query: str | None = None,
    active_hwnd: int | None = None,
) -> ContextSnapshot:
    """
    Collect all context sources, persist to the temp JSON file, and return
    the snapshot.  Always produces at least an active-window entry.

    Args:
        capture_screen:       Take a full-screen screenshot (off by default).
        fetch_browser_content: Fetch and parse the URL in the active browser
                              window as plain text (on by default; only fires
                              when a supported browser is in the foreground).
        online_query:         If provided, run a DuckDuckGo search and include
                              up to 5 results in the snapshot.
        active_hwnd:          Windows-only foreground window handle captured at
                              hotkey time. Avoids re-detecting the overlay after
                              focus has moved.
    """
    global _context_window

    # Lazily start fs watcher on first fetch
    if _fs_observer is None:
        start_fs_watcher()

    active_win = (
        _fetch_window_info_win(int(active_hwnd))
        if _IS_WIN and active_hwnd
        else _fetch_active_window()
    )
    _context_window = active_win  # cache so get_active_document_path() can use it after focus changes

    browser_content = ""
    if fetch_browser_content and active_win.url:
        browser_content = _browser_content(active_win)

    snapshot = ContextSnapshot(
        timestamp=time.time(),
        active_window=active_win,
        clipboard=_fetch_clipboard(),
        ui_focused=_fetch_ui_focused(),
        recent_files=_fetch_recent_files(),
        fs_events=_get_fs_events(),
        browser_content=browser_content,
        online_results=_search_online(online_query) if online_query else [],
        screen_capture_path=_capture_screen_to_file() if capture_screen else "",
    )
    _persist(snapshot)
    return snapshot


def load_latest() -> dict | None:
    """Load the last persisted snapshot from disk as a plain dict."""
    try:
        with open(_TEMP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def fetch_browser_content_for_tool(url: str) -> str:
    """
    Public wrapper around _fetch_browser_content for use as an LLM tool.
    Fetches the plain-text content of *url* with redaction applied.
    Returns "" on failure or if the URL is private/non-HTTP.
    """
    return _fetch_browser_content(url)


def fetch_browser_content_for_window(url: str = "", hwnd: int = 0) -> str:
    """
    Read browser page text from a previously captured browser window handle,
    falling back to an HTTP fetch of *url*. This is the query-time half of the
    hotkey-time snapshot: the browser no longer needs to be foreground.
    """
    if not (url or hwnd):
        return ""
    try:
        return _browser_content(WindowInfo(url=url, hwnd=int(hwnd)))
    except Exception:
        return ""


# Apps that open documents whose content Claude might want to read.
# (suffix in window title → display label)
_DOC_APP_TITLE_SUFFIXES: list[str] = [
    # Microsoft Office
    " - Microsoft Word",
    " - Word",
    " - Microsoft Excel",
    " - Excel",
    " - Microsoft PowerPoint",
    " - PowerPoint",
    " - Microsoft Publisher",
    " - Publisher",
    " - Microsoft Visio",
    " - Visio",
    # LibreOffice / WPS Office
    " - LibreOffice Writer",
    " - LibreOffice Calc",
    " - LibreOffice Impress",
    " - LibreOffice Draw",
    " - LibreOffice Math",
    " - WPS Writer",
    " - WPS Spreadsheet",
    " - WPS Presentation",
    # Plain text / Markdown editors
    " - TextEdit",
    " - CotEditor",
    " - BBEdit",
    " - TextMate",
    " - Notepad",
    " - Notepad++",
    " - Sublime Text",
    " - Typora",
    " - Zettlr",
    " - Mark Text",
    " - GNU Emacs",
    " - GVIM",
    # PDF viewers / editors
    " - Preview",
    " - Skim",
    " - Adobe Acrobat",
    " - Adobe Reader",
    " - Foxit PDF Reader",
    " - Foxit Reader",
    " - PDF-XChange Editor",
    " - PDF-XChange Viewer",
    " - SumatraPDF",
    # Image / design
    " - Paint.NET",
    " - Krita",
    " - Inkscape",
    " - GNU Image Manipulation Program",  # GIMP
    " - draw.io",
    " - Blender",
    # Apple productivity apps
    " - Pages",
    " - Numbers",
    " - Keynote",
    # VS Code family  (Insiders must come before plain VS Code)
    " - Visual Studio Code - Insiders",
    " - Visual Studio Code",
    " - Cursor",
    " - Windsurf",
    # JetBrains IDEs use en-dash (\u2013) as separator
    " \u2013 PyCharm",
    " \u2013 IntelliJ IDEA",
    " \u2013 WebStorm",
    " \u2013 GoLand",
    " \u2013 Rider",
    " \u2013 CLion",
    " \u2013 RubyMine",
    " \u2013 PhpStorm",
    " \u2013 DataGrip",
    " \u2013 Android Studio",
]

_DOC_APP_PROCESS_NAMES: set[str] = {
    # Microsoft Office / viewers
    "winword.exe", "winword",
    "excel.exe", "excel",
    "powerpnt.exe", "powerpnt",
    "mspub.exe", "mspub",
    "visio.exe", "visio",
    "wordpad.exe", "wordpad",
    # LibreOffice / OpenOffice
    "soffice.exe", "soffice.bin", "soffice",
    "swriter.exe", "swriter",
    "scalc.exe", "scalc",
    "simpress.exe", "simpress",
    "sdraw.exe", "sdraw",
    # PDF readers
    "acrobat.exe", "acrord32.exe", "acrocef.exe",
    "foxitpdfreader.exe", "foxitreader.exe",
    "pdfxedit.exe", "pdfxview.exe", "pxceditor.exe",
    "sumatrapdf.exe", "sumatrapdf",
    # Plain-text / markdown editors
    "textedit",
    "coteditor",
    "bbedit",
    "textmate",
    "notepad.exe", "notepad",
    "notepad++.exe", "notepad++",
    "sublime_text.exe", "sublime_text",
    "typora.exe", "typora",
    "zettlr.exe", "zettlr",
    "marktext.exe", "marktext",
    "gvim.exe", "gvim",
    "emacs.exe", "emacs",
    # macOS document apps whose titles are commonly just the document name
    "preview",
    "skim",
    "pages",
    "numbers",
    "keynote",
}

_DOC_TITLE_SEPARATORS: tuple[str, ...] = (" - ", " \u2013 ", " \u2014 ")

def _config_dir() -> str:
    """Return the per-user config base directory for app data.

    Windows: %APPDATA%; macOS: ~/Library/Application Support; Linux: ~/.config.
    """
    if _IS_WIN:
        return os.environ.get("APPDATA", "")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    return os.path.join(os.path.expanduser("~"), ".config")


# Maps VS Code-like process names to their storage.json path under the config dir.
if _IS_WIN:
    _VSCODE_LIKE_STORAGE: dict[str, str] = {
        "code.exe":            r"Code\User\globalStorage\storage.json",
        "code - insiders.exe": r"Code - Insiders\User\globalStorage\storage.json",
        "cursor.exe":          r"Cursor\User\globalStorage\storage.json",
        "windsurf.exe":        r"Windsurf\User\globalStorage\storage.json",
    }
else:
    _VSCODE_LIKE_STORAGE = {
        "code":            "Code/User/globalStorage/storage.json",
        "code-insiders":   "Code - Insiders/User/globalStorage/storage.json",
        "cursor":          "Cursor/User/globalStorage/storage.json",
        "windsurf":        "Windsurf/User/globalStorage/storage.json",
    }

if _IS_WIN:
    _JETBRAINS_PROCS = frozenset({
        "pycharm64.exe", "pycharm.exe",
        "idea64.exe",    "idea.exe",
        "webstorm64.exe", "webstorm.exe",
        "goland64.exe",  "goland.exe",
        "clion64.exe",   "clion.exe",
        "rider64.exe",   "rider.exe",
        "rubymine64.exe", "rubymine.exe",
        "phpstorm64.exe", "phpstorm.exe",
        "datagrip64.exe", "datagrip.exe",
        "studio64.exe",
    })
else:
    _JETBRAINS_PROCS = frozenset({
        "pycharm", "idea", "webstorm", "goland", "clion",
        "rider", "rubymine", "phpstorm", "datagrip", "studio",
    })

_VSCODE_TITLE_MARKERS: tuple[str, ...] = (
    " - Visual Studio Code - Insiders",
    " - Visual Studio Code",
    " - Cursor",
    " - Windsurf",
)


def _decode_vscode_uri(uri: str) -> str:
    """Convert a VS Code file:/// URI to a filesystem path (cross-platform)."""
    if not uri.startswith("file:///"):
        return ""
    if _IS_WIN:
        # file:///C:/Users/... → C:\Users\...
        path = unquote(uri[8:]).replace("/", os.sep)
        if len(path) >= 2 and path[1] == ":":
            path = path[0].upper() + path[1:]
        return path
    else:
        # file:///home/user/... → /home/user/...
        return unquote(uri[7:])


def _vscode_find_file(filename: str, workspace_hint: str = "", storage_path: str = "") -> str:
    """
    Resolve a bare filename via a VS Code-like storage.json.
    Works for VS Code, VS Code Insiders, Cursor, Windsurf, and any other
    Electron editor that uses the same storage format.
    *storage_path* should be the full path to storage.json; defaults to the
    standard VS Code location when omitted.
    """
    import json

    if not storage_path:
        storage_path = os.path.join(_config_dir(), "Code", "User", "globalStorage", "storage.json")
    try:
        with open(storage_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    history = data.get("history.recentlyOpenedPathsList", {})
    fn_lower = filename.lower()

    # 1. Exact match against recently opened files.
    for item in history.get("files2", []):
        uri = item.get("fileUri", "")
        if uri:
            path = _decode_vscode_uri(uri)
            if path and os.path.basename(path).lower() == fn_lower:
                return path

    # 2. Search within recently opened workspace folders.
    folders: list[str] = []
    for item in history.get("workspaces3", []):
        uri = item.get("folderUri", "")
        if uri:
            path = _decode_vscode_uri(uri)
            if path and os.path.isdir(path):
                folders.append(path)

    if not folders:
        return ""

    # Prioritise the folder whose name matches the workspace hint from the title.
    if workspace_hint:
        hint_lower = workspace_hint.lower()
        folders.sort(key=lambda p: 0 if os.path.basename(p).lower() == hint_lower else 1)

    return _search_filename_in_folders(filename, folders)


def _vscode_running_roots(process_name: str, workspace_hint: str = "") -> list[str]:
    """Collect plausible workspace roots from running VS Code-like processes."""
    roots: list[str] = []
    seen: set[str] = set()

    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name", "cwd", "cmdline"]):
            try:
                if (proc.info.get("name") or "").lower() != process_name.lower():
                    continue

                candidates: list[str] = []

                cwd = (proc.info.get("cwd") or "").strip()
                if cwd:
                    candidates.append(cwd)

                for arg in proc.info.get("cmdline") or []:
                    if not isinstance(arg, str):
                        continue
                    arg = arg.strip().strip('"')
                    if not arg:
                        continue
                    if os.path.isdir(arg):
                        candidates.append(arg)
                    elif arg.lower().endswith(".code-workspace") and os.path.isfile(arg):
                        candidates.append(os.path.dirname(arg))

                for candidate in candidates:
                    norm = os.path.normpath(candidate)
                    if not os.path.isdir(norm) or norm in seen:
                        continue
                    seen.add(norm)
                    roots.append(norm)
            except Exception:
                pass
    except Exception:
        return []

    if workspace_hint:
        hint_lower = workspace_hint.lower()
        roots.sort(key=lambda p: 0 if os.path.basename(p).lower() == hint_lower else 1)

    return roots


def _search_filename_in_folders(filename: str, folders: list[str], max_depth: int = 5) -> str:
    """Search a bare filename under candidate folders up to *max_depth* deep."""
    import glob

    for folder in folders[:8]:
        for depth in range(max_depth):
            pattern = os.path.join(glob.escape(folder), *(["*"] * depth), filename)
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

    return ""


def _jetbrains_find_file(filename: str, project_hint: str = "") -> str:
    """
    Resolve a filename using JetBrains IDE recent project directories.
    Reads recentProjectDirectories.xml from all installed JetBrains products
    (and Android Studio under %APPDATA%\\Google\\AndroidStudio*).
    """
    import glob as _glob
    import xml.etree.ElementTree as ET

    cfg = _config_dir()
    xml_paths: list[str] = []

    jb_base = os.path.join(cfg, "JetBrains")
    if os.path.isdir(jb_base):
        xml_paths.extend(
            _glob.glob(os.path.join(jb_base, "*", "options", "recentProjectDirectories.xml"))
        )

    if _IS_WIN:
        google_base = os.path.join(cfg, "Google")
        if os.path.isdir(google_base):
            xml_paths.extend(
                _glob.glob(os.path.join(google_base, "AndroidStudio*", "options", "recentProjectDirectories.xml"))
            )
    else:
        # Android Studio on Linux stores config under ~/.config/Google/AndroidStudio* or
        # the legacy ~/.AndroidStudio* location.
        for base in (os.path.join(cfg, "Google"), os.path.expanduser("~")):
            xml_paths.extend(
                _glob.glob(os.path.join(base, "AndroidStudio*", "options", "recentProjectDirectories.xml"))
            )

    project_dirs: list[str] = []
    home = os.path.expanduser("~")
    for xml_path in xml_paths:
        try:
            tree = ET.parse(xml_path)
            for option in tree.iter("option"):
                val = option.get("value", "")
                if val:
                    val = val.replace("$USER_HOME$", home)
                    val = os.path.normpath(val)
                    if os.path.isdir(val) and val not in project_dirs:
                        project_dirs.append(val)
        except Exception:
            pass

    if not project_dirs:
        return ""

    if project_hint:
        hint_lower = project_hint.lower()
        project_dirs.sort(
            key=lambda p: 0 if os.path.basename(p).lower() == hint_lower else 1
        )

    import glob
    for proj_dir in project_dirs[:8]:
        for depth in range(5):
            pattern = os.path.join(glob.escape(proj_dir), *(["*"] * depth), filename)
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

    return ""


def _obsidian_find_note(stripped_title: str) -> str:
    """
    Resolve an Obsidian note to a filesystem path.
    *stripped_title* is the window title with the " - Obsidian vX.Y.Z" suffix
    already removed, e.g. "Note Name" or "Note Name - Vault Name".
    Reads vault paths from %APPDATA%\\obsidian\\obsidian.json.
    """
    import json
    import glob

    # Split "Note Name - Vault Name" from the right so note names with " - "
    # in them are preserved correctly.
    parts = stripped_title.rsplit(" - ", 1)
    note_name = parts[0].strip()
    vault_hint = parts[1].strip() if len(parts) > 1 else ""

    if not note_name:
        return ""

    storage = os.path.join(_config_dir(), "obsidian", "obsidian.json")
    try:
        with open(storage, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""

    vaults = [
        v["path"] for v in data.get("vaults", {}).values()
        if isinstance(v, dict) and "path" in v and os.path.isdir(v["path"])
    ]
    if not vaults:
        return ""

    if vault_hint:
        hint_lower = vault_hint.lower()
        vaults.sort(key=lambda p: 0 if os.path.basename(p).lower() == hint_lower else 1)

    for vault in vaults[:5]:
        for ext in (".md", ".txt", ""):
            fn = note_name + ext if ext else note_name
            for depth in range(5):
                pattern = os.path.join(glob.escape(vault), *(["*"] * depth), fn)
                matches = glob.glob(pattern)
                if matches:
                    return matches[0]

    return ""


def _extract_doc_name_from_window(win: WindowInfo) -> str:
    """Extract the document portion of a supported app window title."""
    title = (win.title or "").strip()
    if not title:
        return ""

    proc_lower = (win.process_name or "").lower()

    def _leading_title_piece() -> str:
        for sep in _DOC_TITLE_SEPARATORS:
            if sep in title:
                return title.rsplit(sep, 1)[0].strip()
        return title

    if proc_lower in ("obsidian.exe", "obsidian"):
        return re.sub(
            r"\s*-\s*Obsidian\s+v[\d.]+.*$", "", title, flags=re.IGNORECASE
        ).strip()

    if proc_lower in ("notepad.exe", "notepad"):
        # Notepad localizes the app name in the title bar, so process name is a
        # more reliable signal than the visible suffix.
        return _leading_title_piece()

    if proc_lower in _VSCODE_LIKE_STORAGE:
        for marker in _VSCODE_TITLE_MARKERS:
            idx = title.find(marker)
            if idx != -1:
                return title[:idx].strip()

    for suffix in _DOC_APP_TITLE_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()

    if proc_lower in _DOC_APP_PROCESS_NAMES:
        # Localized Office/PDF/editor windows may not end in our English suffixes.
        # Keep them eligible for open-file matching and UIA fallback.
        return _leading_title_piece()

    return ""


def _resolve_doc_path(win: WindowInfo) -> str:
    """
    Internal: resolve the open-document path from a WindowInfo.
    Extracted so it can be called for any window, focused or not.
    """
    try:
        title = win.title
        if not title:
            return ""

        proc_lower = win.process_name.lower()

        doc_name = _extract_doc_name_from_window(win)

        if not doc_name:
            return ""

        if proc_lower in ("obsidian.exe", "obsidian"):
            return _obsidian_find_note(doc_name)

        # Strip bracketed modifiers like "[Compatibility Mode]" and common
        # unsaved/modified markers that editors prefix to the visible filename.
        doc_name = re.sub(r"\s*\[.*?\]\s*$", "", doc_name).strip()
        doc_name = doc_name.lstrip("\u25cf\u2022*").strip()
        if not doc_name:
            return ""

        # VS Code and forks (Cursor, Windsurf, …) — resolve via their storage.json.
        if proc_lower in _VSCODE_LIKE_STORAGE:
            doc_name = doc_name.lstrip("\u25cf\u2022").strip()  # strip ● unsaved marker
            workspace_hint = ""
            if " - " in doc_name:
                parts = doc_name.split(" - ", 1)
                doc_name = parts[0].strip()
                workspace_hint = re.sub(r"\s*\(Workspace\)\s*$", "", parts[1]).strip()
            if not doc_name:
                return ""
            storage_path = os.path.join(_config_dir(), _VSCODE_LIKE_STORAGE[proc_lower])
            vscode_path = _vscode_find_file(doc_name, workspace_hint, storage_path)
            if not vscode_path:
                vscode_path = _search_filename_in_folders(
                    doc_name,
                    _vscode_running_roots(proc_lower, workspace_hint),
                )
            if vscode_path:
                return vscode_path
            # Fall through to generic recent-files lookup below

        # JetBrains IDEs — title uses en-dash: "filename \u2013 project \u2013 IDE".
        elif proc_lower in _JETBRAINS_PROCS:
            project_hint = ""
            if "\u2013" in doc_name:
                parts = doc_name.split("\u2013", 1)
                doc_name = parts[0].strip()
                project_hint = parts[1].strip()
            if not doc_name:
                return ""
            jb_path = _jetbrains_find_file(doc_name, project_hint)
            if jb_path:
                return jb_path
            # Fall through to generic recent-files lookup below

        if _IS_MAC and win.pid:
            mac_path = _mac_match_open_file(win.pid, doc_name)
            if mac_path:
                return mac_path
        elif _IS_WIN and win.pid:
            win_path = _win_match_open_file(win.pid, doc_name)
            if win_path:
                return win_path

        # If it looks like an absolute path already, trust it.
        if os.path.isfile(doc_name):
            return doc_name

        # Match against recently opened files (resolved .lnk paths).
        recent = _fetch_recent_files(30)
        doc_lower = doc_name.lower()
        for path in recent:
            if os.path.basename(path).lower() == doc_lower:
                return path
        # Fuzzy: strip extension from both sides
        doc_stem = os.path.splitext(doc_lower)[0]
        for path in recent:
            if os.path.splitext(os.path.basename(path))[0].lower() == doc_stem:
                return path
    except Exception:
        pass
    return ""


def get_active_document_path(active_window: WindowInfo | None = None) -> str:
    """
    Try to find the full filesystem path of the document open in the
    foreground application window.  Returns "" if not detectable.

    Uses the window captured at hotkey time so the overlay's own window
    does not shadow the user's document after focus transfer.
    """
    if active_window is not None:
        win = active_window
    elif _context_window is not None:
        win = _context_window
    else:
        win = _fetch_active_window()
    return _resolve_doc_path(win)


def _enumerate_open_doc_windows() -> list[WindowInfo]:
    """
    Return a WindowInfo for every visible top-level window whose title matches
    a known doc-app suffix or the Obsidian version pattern.
    """
    if _IS_WIN:
        return _enumerate_open_doc_windows_win()
    if _IS_MAC:
        return _enumerate_open_doc_windows_macos()
    return _enumerate_open_doc_windows_linux()


def _enumerate_open_doc_windows_win() -> list[WindowInfo]:
    import ctypes
    import ctypes.wintypes

    results: list[WindowInfo] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def _callback(hwnd, _):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value.strip()
        if not title:
            return True
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        try:
            import psutil
            proc_name = psutil.Process(pid.value).name()
        except Exception:
            pass
        win = WindowInfo(title=title, process_name=proc_name, pid=pid.value, hwnd=int(hwnd))
        if _extract_doc_name_from_window(win):
            results.append(win)
        return True

    ctypes.windll.user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return results


def _enumerate_open_doc_windows_linux() -> list[WindowInfo]:
    from core.platform_utils import list_visible_windows, get_window_title, get_window_pid

    results: list[WindowInfo] = []
    for wid in list_visible_windows()[:60]:
        try:
            title = get_window_title(wid)
            if not title:
                continue
            pid = get_window_pid(wid)
            proc_name = ""
            if pid:
                try:
                    import psutil
                    proc_name = psutil.Process(pid).name()
                except Exception:
                    pass
            win = WindowInfo(title=title, process_name=proc_name, pid=pid)
            if _extract_doc_name_from_window(win):
                results.append(win)
        except Exception:
            pass
    return results


def _enumerate_open_doc_windows_macos() -> list[WindowInfo]:
    from core.platform import macos_native

    results: list[WindowInfo] = []
    rows = macos_native.list_document_windows()
    rows.sort(key=lambda row: (not bool(row.get("frontmost")), str(row.get("title") or "")))
    seen: set[tuple[int, str]] = set()
    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        try:
            pid = int(row.get("pid") or 0)
        except Exception:
            pid = 0
        proc_name = ""
        if pid:
            try:
                import psutil
                proc_name = psutil.Process(pid).name()
            except Exception:
                proc_name = ""
        if not proc_name:
            proc_name = str(row.get("process_name") or "")
        key = (pid, title)
        if key in seen:
            continue
        seen.add(key)
        win = WindowInfo(title=title, process_name=proc_name, pid=pid)
        if _extract_doc_name_from_window(win):
            results.append(win)
    return results


def _mac_open_files_for_pid(pid: int) -> list[str]:
    if not _IS_MAC or pid <= 0:
        return []
    import subprocess

    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-Fn", "-p", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return []
    if result.returncode not in (0, 1):
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for line in (result.stdout or "").splitlines():
        if not line.startswith("n"):
            continue
        path = line[1:].strip()
        if not path or not path.startswith("/"):
            continue
        if path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        paths.append(path)
    return paths


def _win_open_files_for_pid(pid: int) -> list[str]:
    if not _IS_WIN or pid <= 0:
        return []
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        paths: list[str] = []
        seen: set[str] = set()
        for item in proc.open_files() or []:
            path = os.path.normpath(str(getattr(item, "path", "") or ""))
            if not path or path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            paths.append(path)
        return paths
    except Exception:
        return []


def _match_open_file_paths(candidates: list[str], doc_name: str) -> str:
    if not candidates:
        return ""
    target = os.path.basename((doc_name or "").strip()).lower()
    if not target:
        return ""
    target_stem = os.path.splitext(target)[0]
    exact: list[str] = []
    stem_matches: list[str] = []
    partial: list[str] = []
    for path in candidates:
        name = os.path.basename(path).lower()
        stem = os.path.splitext(name)[0]
        path_lower = path.lower()
        if name == target or path_lower == target:
            exact.append(path)
        elif stem == target_stem:
            stem_matches.append(path)
        elif target in path_lower or (target_stem and target_stem in stem):
            partial.append(path)
    for group in (exact, stem_matches, partial):
        if group:
            return group[0]
    return ""


def _mac_match_open_file(pid: int, doc_name: str) -> str:
    return _match_open_file_paths(_mac_open_files_for_pid(pid), doc_name)


def _win_match_open_file(pid: int, doc_name: str) -> str:
    return _match_open_file_paths(_win_open_files_for_pid(pid), doc_name)


def get_all_open_document_paths(
    max_docs: int = 5,
    active_window: WindowInfo | None = None,
) -> list[str]:
    """
    Resolve filesystem paths for ALL open doc-app windows, focused or not.
    Returns a deduplicated list of up to *max_docs* paths, with the window
    that had focus at hotkey time listed first (if resolvable).
    """
    # Put the hotkey-time window first so it has priority.
    primary = get_active_document_path(active_window=active_window)
    seen: set[str] = set()
    paths: list[str] = []
    if primary:
        seen.add(primary)
        paths.append(primary)

    for win in _enumerate_open_doc_windows():
        if len(paths) >= max_docs:
            break
        path = _resolve_doc_path(win)
        if path and path not in seen:
            seen.add(path)
            paths.append(path)

    return paths


def get_all_open_document_window_texts(
    max_docs: int = 5,
    max_chars_per_doc: int | None = None,
    active_window: WindowInfo | None = None,
) -> list[tuple[str, str]]:
    """Read visible text directly from open document/editor windows.

    This is a Windows fallback for cases where a document app exposes text via
    UI Automation but does not expose a reliable filesystem path. Returns
    ``(label, text)`` pairs. Empty/unreadable windows are skipped.
    """
    if not _IS_WIN:
        return []
    if max_chars_per_doc is None:
        max_chars_per_doc = config.CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS

    windows: list[WindowInfo] = []
    seen_windows: set[int] = set()

    primary = active_window or _context_window
    if primary is not None and primary.hwnd and _extract_doc_name_from_window(primary):
        windows.append(primary)
        seen_windows.add(int(primary.hwnd))

    for win in _enumerate_open_doc_windows():
        if len(windows) >= max_docs:
            break
        if not win.hwnd or int(win.hwnd) in seen_windows:
            continue
        windows.append(win)
        seen_windows.add(int(win.hwnd))

    results: list[tuple[str, str]] = []
    for win in windows:
        if len(results) >= max_docs:
            break
        label = _extract_doc_name_from_window(win) or win.title or "Open document"
        label = re.sub(r"\s*\[.*?\]\s*$", "", label).strip().lstrip("\u25cf\u2022*").strip()
        text = _get_window_text_uia(win.hwnd, max_chars_per_doc) or ""
        if text:
            results.append((label or "Open document", text))
    return results


def format_context_for_prompt(snapshot: ContextSnapshot) -> str:
    """
    Format a ContextSnapshot as a compact plain-text block suitable for
    inclusion in an LLM system prompt.
    """
    lines: list[str] = ["AMBIENT CONTEXT (captured at hotkey time):"]

    w = snapshot.active_window
    if w.title or w.process_name:
        win_str = f"{w.process_name} — \u201c{w.title}\u201d" if w.title else w.process_name
        lines.append(f"- Active window: {win_str}")
    if w.url:
        lines.append(f"- Browser URL: {w.url}")
    if snapshot.browser_content:
        lines.append("[Browser/Web]\n" + snapshot.browser_content)

    cb = snapshot.clipboard
    if cb.fmt == "text" and cb.text:
        preview = cb.text[:300].replace("\n", " ")
        if len(cb.text) > 300:
            preview += "\u2026"
        lines.append(f"- Clipboard: \u201c{preview}\u201d")
    elif cb.fmt == "image":
        lines.append("- Clipboard: [image]")

    ui = snapshot.ui_focused
    if ui.control_type or ui.name:
        el_str = ui.control_type or "Element"
        if ui.name:
            el_str += f" \u2018{ui.name[:80]}\u2019"
        if ui.window_title:
            el_str += f" in \u201c{ui.window_title[:80]}\u201d"
        lines.append(f"- Focused element: {el_str}")
    if ui.value:
        preview = ui.value[:200].replace("\n", " ")
        lines.append(f"- Element value: \u201c{preview}\u201d")

    if snapshot.fs_events:
        recent = snapshot.fs_events[-5:]
        lines.append("- Recent file changes: " + ", ".join(recent))

    # If a document app is in the foreground, hint that its content is readable.
    doc_name = _extract_doc_name_from_window(snapshot.active_window)
    if doc_name:
        doc_name = re.sub(r"\s*\[.*?\]\s*$", "", doc_name).strip()
        if doc_name:
            lines.append(
                f"- Open document: \"{doc_name}\" "
                "(call get_context tool to read full content)"
            )

    if not lines[1:]:  # nothing useful was added
        return ""

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snapshot_to_dict(snapshot: ContextSnapshot) -> dict:
    """Recursively convert dataclasses to plain dicts."""
    def _convert(obj):
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _convert(v) for k, v in vars(obj).items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        return obj

    return _convert(snapshot)


def _persist(snapshot: ContextSnapshot) -> None:
    try:
        data = _snapshot_to_dict(snapshot)
        with open(_TEMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[context_fetcher] Failed to write snapshot: {e}")


# ---------------------------------------------------------------------------
# CLI smoke-test: python -m core.context_fetcher
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Writing snapshot to: {get_temp_path()}")
    if query:
        print(f"Online query: {query!r}")
    print()
    snap = fetch_and_save(capture_screen=False, online_query=query)
    pprint.pprint(_snapshot_to_dict(snap))
