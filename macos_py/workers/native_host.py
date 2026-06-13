"""wisp-native worker: platform permissions, hotkeys, context, capture, clipboard."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from macos_py.bootstrap import repo_root
from macos_py.service_host import run_host

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
_emit: Callable[[str, Any, Any], None] | None = None
_hotkeys = None
_last_context_window_debug: dict[str, Any] = {}


class _HotkeyHelper:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._status: dict[str, Any] = {
            "started": False,
            "backend": "carbon-helper",
            "reason": "not started",
        }

    def start(self) -> dict[str, Any]:
        self._stop_stale_helpers()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("WISP_REPO_ROOT", str(repo_root()))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "macos_py.workers.hotkey_helper"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(repo_root()),
            env=env,
            bufsize=0,
        )
        threading.Thread(target=self._stdout_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()
        if not self._ready.wait(timeout=5.0):
            self._status = {
                "started": False,
                "backend": "carbon-helper",
                "reason": "helper did not report readiness",
            }
            self.stop()
        return dict(self._status)

    def _stop_stale_helpers(self) -> None:
        if not IS_MAC:
            return
        try:
            subprocess.run(
                ["/usr/bin/pkill", "-f", "macos_py.workers.hotkey_helper"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except Exception:
            pass

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)

    def _stdout_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"[hotkeys] helper stdout: {line}", file=sys.stderr)
                continue
            if "status" in msg:
                self._status = msg
                self._ready.set()
                continue
            if msg.get("event") == "native.hotkey":
                _event("native.hotkey", msg.get("data") or {})
        if not self._ready.is_set():
            self._status = {
                "started": False,
                "backend": "carbon-helper",
                "reason": "helper exited before readiness",
            }
            self._ready.set()

    def _stderr_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                print(f"[hotkeys] helper: {line}", file=sys.stderr)


class _DirectHotkeys:
    """Windows/Linux hotkeys using the shared core listener in this worker."""

    def __init__(self) -> None:
        self.listener = None
        self._status: dict[str, Any] = {
            "started": False,
            "backend": "core-hotkeys",
            "reason": "not started",
        }

    def start(self) -> dict[str, Any]:
        try:
            import config
            from core.hotkeys import HotkeyListener

            def emit_hotkey(kind: str, **extra: Any) -> None:
                _event("native.hotkey", {"kind": kind, **extra})

            caller_count = len(getattr(config, "CALLER_ROWS", []))
            callers = [
                (lambda idx=idx: emit_hotkey("caller", index=idx))
                for idx in range(caller_count)
            ]
            self.listener = HotkeyListener(
                on_callers=callers,
                on_add_context=lambda: emit_hotkey("add_context"),
                on_clear_context=lambda: emit_hotkey("clear_context"),
                on_snip=lambda: emit_hotkey("snip"),
                on_voice_start=lambda: emit_hotkey("voice_start"),
                on_voice_stop=lambda: emit_hotkey("voice_stop"),
            )
            started = bool(self.listener.start())
            status = self.listener.status() if hasattr(self.listener, "status") else {}
            self._status = {
                "started": started,
                "backend": "core-hotkeys",
                **status,
            }
            if not started:
                self.stop()
                self._status.setdefault("reason", "no hotkeys registered")
        except Exception as exc:  # noqa: BLE001 - report hotkey backend failures
            self.stop()
            self._status = {
                "started": False,
                "backend": "core-hotkeys",
                "error": f"{type(exc).__name__}: {exc}",
            }
        return dict(self._status)

    def stop(self) -> None:
        listener = self.listener
        self.listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass


def set_event_sink(fn: Callable[[str, Any, Any], None]) -> None:
    global _emit
    _emit = fn


def _event(name: str, data: Any = None) -> None:
    if _emit is not None:
        _emit(name, data, None)


def _ax_trusted() -> bool | None:
    if not IS_MAC:
        return None
    try:
        import ctypes
        import ctypes.util

        app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices") or "ApplicationServices"
        )
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return None


def _screen_trusted() -> bool | None:
    if not IS_MAC:
        return None
    try:
        import Quartz  # type: ignore

        fn = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if fn is not None:
            return bool(fn())
    except Exception:
        return None
    return None


def _microphone_status() -> str:
    if not IS_MAC:
        return "unavailable"
    try:
        import AVFoundation  # type: ignore

        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        names = {
            0: "not_determined",
            1: "restricted",
            2: "denied",
            3: "authorized",
        }
        return names.get(int(status), str(status))
    except Exception:
        return "unknown"


def permissions_snapshot() -> dict[str, Any]:
    return {
        "platform": sys.platform,
        "accessibility": _ax_trusted(),
        "screen_recording": _screen_trusted(),
        "microphone": _microphone_status(),
    }


def _win_window_pid(hwnd: int) -> int:
    if not IS_WIN or not hwnd:
        return 0
    try:
        import ctypes

        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value or 0)
    except Exception:
        return 0


def _win_window_title(hwnd: int) -> str:
    if not IS_WIN or not hwnd:
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(int(hwnd))
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(int(hwnd), buf, length + 1)
        return str(buf.value or "")
    except Exception:
        return ""


def _win_process_name(pid: int) -> str:
    if not IS_WIN or pid <= 0:
        return ""
    try:
        import psutil  # type: ignore

        return str(psutil.Process(pid).name() or "")
    except Exception:
        return ""


def _win_is_wisp_ui_window(hwnd: int) -> bool:
    pid = _win_window_pid(hwnd)
    title = _win_window_title(hwnd).strip().lower()
    proc = _win_process_name(pid).strip().lower()
    if pid == os.getpid():
        return True
    if proc == "wisp.exe":
        return True
    if proc in {"python.exe", "pythonw.exe"} and title in {"wisp", "wisp settings", "wisp memory"}:
        return True
    return False


def _win_is_external_context_window(hwnd: int) -> bool:
    if not IS_WIN or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(hwnd)
        if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False
        if _win_is_wisp_ui_window(hwnd):
            return False
        return bool(_win_window_title(hwnd).strip())
    except Exception:
        return False


def _win_find_external_context_window(start_hwnd: int) -> int:
    if not IS_WIN:
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        gw_hwndnext = 2
        hwnd = user32.GetWindow(int(start_hwnd or 0), gw_hwndnext) if start_hwnd else 0
        if not hwnd:
            hwnd = user32.GetTopWindow(0)
        seen: set[int] = set()
        while hwnd and len(seen) < 200:
            hwnd_i = int(hwnd)
            if hwnd_i in seen:
                break
            seen.add(hwnd_i)
            if _win_is_external_context_window(hwnd_i):
                return hwnd_i
            hwnd = user32.GetWindow(hwnd_i, gw_hwndnext)
    except Exception:
        return 0
    return 0


def _win_context_window_id(raw_hwnd: int = 0) -> int:
    global _last_context_window_debug
    if not IS_WIN:
        _last_context_window_debug = {}
        return int(raw_hwnd or 0)
    if not raw_hwnd:
        try:
            import ctypes

            raw_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            raw_hwnd = 0
    _last_context_window_debug = {
        "raw_hwnd": int(raw_hwnd or 0),
        "raw_title": _win_window_title(raw_hwnd),
        "raw_pid": _win_window_pid(raw_hwnd),
        "raw_process": _win_process_name(_win_window_pid(raw_hwnd)),
        "corrected": False,
        "chosen_hwnd": int(raw_hwnd or 0),
        "chosen_title": _win_window_title(raw_hwnd),
        "chosen_pid": _win_window_pid(raw_hwnd),
        "chosen_process": _win_process_name(_win_window_pid(raw_hwnd)),
    }
    if raw_hwnd and _win_is_external_context_window(raw_hwnd):
        return int(raw_hwnd)
    replacement = _win_find_external_context_window(raw_hwnd)
    if replacement:
        _last_context_window_debug.update(
            {
                "corrected": True,
                "chosen_hwnd": int(replacement),
                "chosen_title": _win_window_title(replacement),
                "chosen_pid": _win_window_pid(replacement),
                "chosen_process": _win_process_name(_win_window_pid(replacement)),
            }
        )
        print(
            "[context.snapshot] corrected foreground "
            f"raw_hwnd={raw_hwnd} raw_title={_win_window_title(raw_hwnd)!r} "
            f"-> hwnd={replacement} title={_win_window_title(replacement)!r}",
            flush=True,
        )
        return int(replacement)
    return int(raw_hwnd or 0)


def _runtime_debug() -> dict[str, Any]:
    debug = {
        "cwd": os.getcwd(),
        "repo_root": str(repo_root()),
        "executable": sys.executable,
        "platform": sys.platform,
    }
    try:
        import config

        debug["config_file"] = str(getattr(config, "__file__", "") or "")
        debug["env_file"] = str(getattr(config, "_ENV_FILE", "") or "")
    except Exception as exc:
        debug["config_error"] = f"{type(exc).__name__}: {exc}"
    return debug


def _active_app() -> dict[str, Any]:
    global _last_context_window_debug
    if not IS_MAC:
        try:
            from core.platform_utils import (
                get_window_pid,
                get_window_title,
            )

            wid = _win_context_window_id() if IS_WIN else 0
            if not wid:
                from core.platform_utils import get_foreground_window

                wid = int(get_foreground_window() or 0)
            return {
                "name": get_window_title(wid),
                "bundle_id": "",
                "pid": int(get_window_pid(wid) or 0),
                "window_id": wid,
            }
        except Exception:
            return {}
    try:
        import AppKit  # type: ignore

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return {}
        app_name = str(app.localizedName() or "")
        pid = int(app.processIdentifier())
        _last_context_window_debug = {}
        try:
            from core.platform import macos_native

            rows = macos_native.list_document_windows()
            frontmost = [
                row for row in rows
                if bool(row.get("frontmost"))
                and (
                    int(row.get("pid") or 0) == pid
                    or str(row.get("process_name") or "") == app_name
                )
            ]
            if frontmost:
                row = frontmost[0]
                title = str(row.get("title") or "")
                process_name = str(row.get("process_name") or app_name)
                row_pid = int(row.get("pid") or pid)
                _last_context_window_debug = {
                    "raw_hwnd": 0,
                    "raw_title": title,
                    "raw_pid": row_pid,
                    "raw_process": process_name,
                    "corrected": False,
                    "chosen_hwnd": 0,
                    "chosen_title": title,
                    "chosen_pid": row_pid,
                    "chosen_process": process_name,
                }
        except Exception:
            _last_context_window_debug = {}
        return {
            "name": app_name,
            "bundle_id": str(app.bundleIdentifier() or ""),
            "pid": pid,
        }
    except Exception:
        return {}


def _clipboard_text_primary() -> str | None:
    if IS_MAC:
        try:
            import AppKit  # type: ignore

            pb = AppKit.NSPasteboard.generalPasteboard()
            value = pb.stringForType_(AppKit.NSPasteboardTypeString)
            return str(value) if value is not None else None
        except Exception:
            return None
    try:
        from core.capture import get_clipboard_text

        return get_clipboard_text()
    except Exception:
        return None


def clipboard_get() -> dict[str, Any]:
    text = _clipboard_text_primary()
    if text is None and IS_MAC:
        from core.platform import macos_native

        text = macos_native.get_clipboard_text()
    return {"text": text or ""}


def _clipboard_set_primary(text: str) -> bool:
    if IS_MAC:
        try:
            import AppKit  # type: ignore

            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            return bool(pb.setString_forType_(text or "", AppKit.NSPasteboardTypeString))
        except Exception:
            return False
    try:
        import pyperclip

        pyperclip.copy(text or "")
        return True
    except Exception:
        return False


def clipboard_set(text: str = "") -> dict[str, Any]:
    ok = _clipboard_set_primary(text)
    if not ok and IS_MAC:
        from core.platform import macos_native

        ok = macos_native.set_clipboard_text(text)
    return {"ok": bool(ok)}


def selected_text() -> str:
    if IS_MAC:
        # Prefer Accessibility: reading AXSelectedText injects no keystrokes. The
        # old clipboard path synthesises Cmd+C, and System Events clearing the
        # command flag around that copy desyncs a physically-held hotkey modifier
        # (option/ctrl) -- so the next hotkey key arrives with no modifier, isn't
        # swallowed, and leaks into the app (e.g. holding the modifier, pressing
        # the add- then clear-context keys, and watching the selection get
        # replaced). Fall back to the copy only when AX can't answer (apps that
        # don't expose selection, e.g. some web/Electron views).
        ax = _ax_selected_text()
        if ax is not None:
            return ax.strip()
        from core.platform import macos_native

        return macos_native.get_selected_text() or ""
    try:
        from core.capture import get_selected_text

        return get_selected_text() or ""
    except Exception:
        return ""


def context_snapshot(
    include_clipboard: bool = True,
    include_selection: bool = True,
    include_browser_content: bool = False,
    include_browser_url: bool = False,
    capture_focus: bool = False,
) -> dict[str, Any]:
    t0 = time.monotonic()
    active = _active_app()
    t_app = time.monotonic()
    snapshot = {
        "platform": sys.platform,
        "active_app": active,
        "selected_text": "",
        "clipboard_text": "",
        "browser_url": "",
        "browser_hwnd": 0,
        "browser_content": "",
        "focus_token": 0,
        "captured_at": time.time(),
        "debug": {
            "runtime": _runtime_debug(),
            "window": dict(_last_context_window_debug),
        },
    }
    # Grab the focused text element first (before selection/clipboard work), while
    # the user's field is still focused, so a later rewrite can be written back
    # in place via Accessibility even if focus has since moved.
    if capture_focus:
        snapshot["focus_token"] = _ax_capture_focus()
    sel_dt = clip_dt = br_dt = 0.0
    if include_selection:
        _s = time.monotonic()
        snapshot["selected_text"] = selected_text()
        sel_dt = time.monotonic() - _s
    if include_clipboard:
        _s = time.monotonic()
        snapshot["clipboard_text"] = clipboard_get()["text"]
        clip_dt = time.monotonic() - _s
    if include_browser_content:
        _s = time.monotonic()
        try:
            from core.context_fetcher import _browser_content, get_browser_window_for_context

            active_hwnd = int(active.get("window_id") or 0) if IS_WIN else None
            browser_window = get_browser_window_for_context(active_hwnd or 0)
            snapshot["browser_url"] = getattr(browser_window, "url", "") or ""
            snapshot["browser_hwnd"] = int(getattr(browser_window, "hwnd", 0) or 0)
            snapshot["browser_content"] = _browser_content(browser_window) if browser_window.hwnd or browser_window.url else ""
            snapshot["debug"]["browser_window"] = {
                "title": getattr(browser_window, "title", ""),
                "process_name": getattr(browser_window, "process_name", ""),
                "pid": getattr(browser_window, "pid", 0),
                "hwnd": getattr(browser_window, "hwnd", 0),
                "url": getattr(browser_window, "url", ""),
            }
        except Exception as exc:  # noqa: BLE001 - browser context should not block answering
            snapshot["browser_error"] = f"{type(exc).__name__}: {exc}"
        br_dt = time.monotonic() - _s
    elif include_browser_url and not IS_MAC:
        # Cheap URL + window-handle grab while the browser is still foreground
        # (hotkey time). The page text itself is fetched later via
        # native.context.browser_content, which reads the window by handle and
        # therefore does not care that focus has moved to the picker by then.
        _s = time.monotonic()
        try:
            from core.context_fetcher import get_browser_window_for_context

            active_hwnd = int(active.get("window_id") or 0)
            win = get_browser_window_for_context(active_hwnd)
            snapshot["debug"]["browser_window"] = {
                "title": getattr(win, "title", ""),
                "process_name": getattr(win, "process_name", ""),
                "pid": getattr(win, "pid", 0),
                "hwnd": getattr(win, "hwnd", 0),
                "url": getattr(win, "url", ""),
            }
            if win.url:
                snapshot["browser_url"] = win.url
            if win.hwnd:
                snapshot["browser_hwnd"] = int(win.hwnd or 0)
        except Exception as exc:  # noqa: BLE001 - browser context should not block the picker
            snapshot["browser_error"] = f"{type(exc).__name__}: {exc}"
        br_dt = time.monotonic() - _s
    print(
        f"[context.snapshot] active_app={t_app - t0:.2f}s selected={sel_dt:.2f}s "
        f"clipboard={clip_dt:.2f}s browser={br_dt:.2f}s total={time.monotonic() - t0:.2f}s "
        f"(app={active.get('name')!r} hwnd={active.get('window_id') or 0} "
        f"sel_len={len(snapshot['selected_text'])} url={'y' if snapshot['browser_url'] else 'n'})",
        flush=True,
    )
    return snapshot


def context_browser_content(url: str = "", hwnd: int = 0) -> dict[str, Any]:
    """Read the page text for a browser window captured at hotkey time.

    Reads the rendered window by handle first (works even when the browser is
    no longer foreground — UIA does not need focus), then falls back to an
    HTTP fetch of the URL. Returns {"url", "content"}.
    """
    try:
        from core.context_fetcher import WindowInfo, _browser_content

        win = WindowInfo(url=str(url or ""), hwnd=int(hwnd or 0))
        content = _browser_content(win)
        return {"url": win.url, "content": content or "", "hwnd": int(hwnd or 0)}
    except Exception as exc:  # noqa: BLE001 - browser context should not block answering
        return {"url": url, "content": "", "hwnd": int(hwnd or 0), "error": f"{type(exc).__name__}: {exc}"}


def capture_fullscreen(path: str = "") -> dict[str, Any]:
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"wisp-capture-{int(time.time() * 1000)}.png")
    if not IS_MAC:
        try:
            from core.capture import get_screen_snippet

            img = get_screen_snippet()
            img.save(path, format="PNG")
            return {
                "ok": True,
                "path": path,
                "size": os.path.getsize(path) if os.path.exists(path) else 0,
            }
        except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
            return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}
    from core.platform import macos_native

    ok = macos_native.capture_screen_to_file(path)
    return {
        "ok": ok,
        "path": path,
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
    }


def _normalize_region(region: dict[str, Any] | None) -> dict[str, Any] | None:
    if not region:
        return None
    try:
        left = int(region.get("left", region.get("x", 0)))
        top = int(region.get("top", region.get("y", 0)))
        width = int(region["width"])
        height = int(region["height"])
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def capture_region(path: str = "", region: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path:
        import tempfile

        path = str(Path(tempfile.gettempdir()) / f"wisp-region-{int(time.time() * 1000)}.png")
    if not IS_MAC:
        try:
            from core.capture import get_screen_snippet

            normalized = _normalize_region(region)
            img = get_screen_snippet(normalized)
            img.save(path, format="PNG")
            return {"ok": True, "path": path, "region": normalized or region}
        except Exception as exc:  # noqa: BLE001 - surface capture failure to caller
            return {
                "ok": False,
                "path": path,
                "region": region,
                "error": f"{type(exc).__name__}: {exc}",
            }
    from core.platform import macos_native

    normalized = _normalize_region(region)
    ok = macos_native.capture_screen_to_file(path, region=normalized)
    return {"ok": ok, "path": path, "region": normalized or region}


def _plog(event: str) -> None:
    """Paste-back diagnostics → native.stderr.log (captured by the supervisor)."""
    line = f"{time.strftime('%H:%M:%S')} [native.paste] {event}"
    print(line, file=sys.stderr, flush=True)


def _frontmost_pid() -> int:
    """pid of the app macOS currently considers frontmost (0 if unknown)."""
    if not IS_MAC:
        return 0
    try:
        import AppKit  # type: ignore

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else 0
    except Exception:
        return 0


def _activate_pid(pid: int) -> dict[str, Any]:
    """Bring the app with `pid` to the front and confirm it actually came forward.

    `activateWithOptions_(NSApplicationActivateIgnoringOtherApps)` is deprecated on
    macOS 14+ and is frequently ignored, especially on remote/headless sessions, so
    we (1) prefer the modern no-arg `activate()` when available, (2) fall back to the
    legacy options, and (3) poll `frontmostApplication()` to verify focus landed on
    the target before the caller synthesises Cmd+V. Returns a diagnostics dict.
    """
    result: dict[str, Any] = {
        "requested_pid": int(pid or 0),
        "called": False,
        "confirmed": False,
        "app_name": "",
        "frontmost_pid": 0,
        "error": "",
    }
    if not IS_MAC or not pid:
        result["error"] = "no pid" if IS_MAC else "not macos"
        return result
    try:
        import AppKit  # type: ignore

        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if app is None:
            result["error"] = "pid not running"
            _plog(f"activate pid={pid} -> app not running")
            return result
        result["app_name"] = str(app.localizedName() or "")
        # Prefer the non-deprecated activate() (10.15+); fall back to the legacy
        # options API if the modern selector is unavailable.
        if app.respondsToSelector_("activate"):
            app.activate()
            result["called"] = True
        else:
            opts = (
                AppKit.NSApplicationActivateIgnoringOtherApps
                | AppKit.NSApplicationActivateAllWindows
            )
            app.activateWithOptions_(opts)
            result["called"] = True
        # Activation is asynchronous; poll until the target is actually frontmost.
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            front = _frontmost_pid()
            result["frontmost_pid"] = front
            if front == int(pid):
                result["confirmed"] = True
                break
            time.sleep(0.05)
        _plog(
            f"activate pid={pid} name={result['app_name']!r} "
            f"confirmed={result['confirmed']} frontmost={result['frontmost_pid']}"
        )
        return result
    except Exception as exc:  # noqa: BLE001 - report activation failure to caller
        result["error"] = f"{type(exc).__name__}: {exc}"
        _plog(f"activate pid={pid} raised {result['error']}")
        return result


# --- Accessibility (AX) in-place text replacement --------------------------
# Lets a rewrite land in the originally-focused text field without refocusing
# the app or synthesising Cmd+V, so it survives the user moving on to something
# else. The focused AXUIElement is a live CFTypeRef, so capture (at hotkey time)
# and apply (after the rewrite) must both run in THIS long-lived worker process;
# only a small integer token crosses IPC. Best-effort: any failure returns False
# and the caller falls back to activate + Cmd+V.
_AX_FOCUSED_ATTR = "AXFocusedUIElement"
_AX_SELECTED_TEXT_ATTR = "AXSelectedText"
_AX_ROLE_ATTR = "AXRole"
_AX_ERROR_SUCCESS = 0  # kAXErrorSuccess

_focus_seq = 0
_focus_cache: dict[str, Any] = {}  # {"token": int, "element": AXUIElement}


def _ax_capture_focus() -> int:
    """Cache the system-wide focused UI element; return a token (0 on failure)."""
    global _focus_seq
    if not IS_MAC:
        return 0
    try:
        import HIServices  # type: ignore  # pyobjc-framework-ApplicationServices

        system = HIServices.AXUIElementCreateSystemWide()
        err, focused = HIServices.AXUIElementCopyAttributeValue(
            system, _AX_FOCUSED_ATTR, None
        )
        if err != _AX_ERROR_SUCCESS or focused is None:
            _plog(f"ax capture: no focused element (err={err})")
            return 0
        _focus_seq += 1
        _focus_cache.clear()
        _focus_cache["token"] = _focus_seq
        _focus_cache["element"] = focused
        _plog(f"ax capture token={_focus_seq} ok")
        return _focus_seq
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        _plog(f"ax capture raised {type(exc).__name__}: {exc}")
        return 0


def _ax_selected_text() -> str | None:
    """Read the focused element's selected text via Accessibility (no keystrokes).

    Returns the selection (``""`` when a text element is focused but nothing is
    selected), or ``None`` when AX can't answer -- no focused element, or the
    element doesn't expose ``AXSelectedText`` (some web/Electron views) -- so the
    caller can fall back to the clipboard copy. Reading AX avoids synthesising
    Cmd+C, whose flag changes desync a physically-held hotkey modifier and make
    the next hotkey key leak into the foreground app.
    """
    if not IS_MAC:
        return None
    try:
        import HIServices  # type: ignore  # pyobjc-framework-ApplicationServices

        system = HIServices.AXUIElementCreateSystemWide()
        err, focused = HIServices.AXUIElementCopyAttributeValue(
            system, _AX_FOCUSED_ATTR, None
        )
        if err != _AX_ERROR_SUCCESS or focused is None:
            return None
        err, value = HIServices.AXUIElementCopyAttributeValue(
            focused, _AX_SELECTED_TEXT_ATTR, None
        )
        if err != _AX_ERROR_SUCCESS or value is None:
            return None
        return str(value)
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        _plog(f"ax selected-text raised {type(exc).__name__}: {exc}")
        return None


def _ax_apply_selected_text(token: int, text: str) -> dict[str, Any]:
    """Replace the cached element's selected text in place. Best-effort."""
    if not IS_MAC:
        return {"ok": False, "error": "not macos"}
    if not token or _focus_cache.get("token") != token:
        return {"ok": False, "error": "stale or missing focus token"}
    element = _focus_cache.get("element")
    if element is None:
        return {"ok": False, "error": "no cached element"}
    try:
        import HIServices  # type: ignore

        # Confirm the element is still alive before writing to it.
        err, _role = HIServices.AXUIElementCopyAttributeValue(element, _AX_ROLE_ATTR, None)
        if err != _AX_ERROR_SUCCESS:
            return {"ok": False, "error": f"element stale (err={err})"}
        set_err = HIServices.AXUIElementSetAttributeValue(element, _AX_SELECTED_TEXT_ATTR, text)
        if set_err == _AX_ERROR_SUCCESS:
            return {"ok": True}
        return {"ok": False, "error": f"set failed (err={set_err})"}
    except Exception as exc:  # noqa: BLE001 - AX is best-effort
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def paste_text(text: str = "", paste_combo: str = "", target_pid: int = 0, focus_token: int = 0) -> dict[str, Any]:
    if IS_MAC:
        from core.platform import macos_native

        # Preferred path: write straight into the originally-focused text element
        # via Accessibility. No app refocus, no Cmd+V — survives the user moving
        # to another window. Falls through to activate + Cmd+V if it can't.
        if focus_token:
            ax = _ax_apply_selected_text(int(focus_token), text)
            if ax.get("ok"):
                _plog(f"paste via AX in-place token={focus_token} ok")
                return {
                    "ok": True,
                    "method": "ax",
                    "activated": True,
                    "confirmed": True,
                    "keystroke_sent": False,
                    "clipboard_ok": False,  # AX write doesn't touch the clipboard
                    "target_pid": int(target_pid or 0),
                    "app_name": "",
                    "error": "",
                }
            _plog(
                f"paste AX in-place token={focus_token} failed "
                f"({ax.get('error')}); falling back to activate+Cmd+V"
            )

        act = _activate_pid(target_pid)
        confirmed = bool(act.get("confirmed"))
        # Settle longer when we couldn't confirm focus — the activation may still
        # be in flight. The clipboard is always populated below, so even a missed
        # Cmd+V leaves the rewrite recoverable via a manual paste.
        time.sleep(0.15 if confirmed else 0.3)
        # Ensure the rewrite is on the clipboard regardless of paste success, so
        # the caller can offer a manual-paste fallback when focus didn't land.
        clip_ok = macos_native.set_clipboard_text(text)
        time.sleep(0.05)  # let pbcopy propagate before Cmd+V
        sent = macos_native.send_key_combo(paste_combo or "cmd+v")
        _plog(
            f"paste target_pid={target_pid} confirmed={confirmed} "
            f"clipboard={clip_ok} keystroke={sent} app={act.get('app_name')!r}"
        )
        return {
            "ok": bool(sent and confirmed),
            "activated": confirmed,
            "confirmed": confirmed,
            "keystroke_sent": bool(sent),
            "clipboard_ok": bool(clip_ok),
            "target_pid": int(target_pid or 0),
            "frontmost_pid": int(act.get("frontmost_pid") or 0),
            "app_name": act.get("app_name") or "",
            "error": act.get("error") or "",
        }
    try:
        from core.platform_utils import PASTE_COMBO, send_keys, set_foreground_window

        activated = False
        if target_pid:
            set_foreground_window(int(target_pid))
            activated = True
            time.sleep(0.15)
        if not clipboard_set(text).get("ok"):
            _plog(f"paste target_pid={target_pid} clipboard write FAILED")
            return {"ok": False, "activated": activated, "clipboard_ok": False, "error": "clipboard write failed"}
        send_keys(paste_combo or PASTE_COMBO)
        _plog(f"paste target_pid={target_pid} activated={activated} keystroke sent")
        return {"ok": True, "activated": activated, "confirmed": activated, "keystroke_sent": True, "clipboard_ok": True}
    except Exception as exc:  # noqa: BLE001 - report pasteback failure to caller
        _plog(f"paste target_pid={target_pid} raised {type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def notify(title: str = "Wisp", message: str = "") -> dict[str, Any]:
    """Post a system notification (Notification Center) so the supervisor can
    surface paste-back status without writing into the reply bubble."""
    if IS_MAC:
        try:
            import json as _json

            script = (
                f"display notification {_json.dumps(message or '')} "
                f"with title {_json.dumps(title or 'Wisp')}"
            )
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5.0,
                check=False,
            )
            return {"ok": result.returncode == 0}
        except Exception as exc:  # noqa: BLE001 - notification is best-effort
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # No system-toast path wired for Windows/Linux yet; callers fall back to logs.
    return {"ok": False, "error": "unsupported platform"}


def open_privacy_settings(pane: str = "Privacy") -> dict[str, Any]:
    if not IS_MAC:
        return {"ok": False, "error": "System Settings is only available on macOS"}
    urls = {
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "privacy": "x-apple.systempreferences:com.apple.preference.security?Privacy",
    }
    target = urls.get((pane or "privacy").strip().lower(), urls["privacy"])
    result = subprocess.run(["/usr/bin/open", target], check=False)
    return {"ok": result.returncode == 0, "url": target}


def hotkeys_start() -> dict[str, Any]:
    """Start global hotkeys in the native process.

    Carbon hotkeys need a Carbon event loop. The native worker's main thread is
    reserved for IPC, so a tiny helper process owns that loop and streams events
    back here.
    """
    global _hotkeys
    if _hotkeys is not None:
        return {"started": True, "backend": "existing"}
    helper = _HotkeyHelper() if IS_MAC else _DirectHotkeys()
    result = helper.start()
    if result.get("started"):
        _hotkeys = helper
        return result
    helper.stop()
    _hotkeys = None
    return result


def hotkeys_stop() -> dict[str, Any]:
    global _hotkeys
    if _hotkeys is not None:
        _hotkeys.stop()
        _hotkeys = None
    return {"stopped": True}


atexit.register(hotkeys_stop)


HANDLERS = {
    "native.permissions.snapshot": permissions_snapshot,
    "native.hotkeys.start": hotkeys_start,
    "native.hotkeys.stop": hotkeys_stop,
    "native.context.snapshot": context_snapshot,
    "native.context.browser_content": context_browser_content,
    "native.capture.fullscreen": capture_fullscreen,
    "native.capture.region": capture_region,
    "native.clipboard.get": clipboard_get,
    "native.clipboard.set": clipboard_set,
    "native.paste_text": paste_text,
    "native.notify": notify,
    "native.open_privacy_settings": open_privacy_settings,
}


def main() -> int:
    return run_host(role="native", handlers=HANDLERS, event_sink_setter=set_event_sink)


if __name__ == "__main__":
    raise SystemExit(main())
