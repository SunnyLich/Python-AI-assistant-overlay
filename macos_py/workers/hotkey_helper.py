"""Standalone Carbon hotkey event loop for the Mac Python target.

The native worker is a stdin/stdout service and cannot block its main thread in
Carbon's event loop. This helper owns that loop in a tiny child process and
streams hotkey events back to the native worker as newline-delimited JSON.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from typing import Any

from macos_py.bootstrap import configure_paths
from macos_py import protocol


# A dedicated, easy-to-read log so hotkey behaviour can be inspected on a remote
# Mac without digging through worker stderr. Tail it with:
#   tail -f ~/wisp-hotkey-debug.log
_DEBUG_LOG = os.path.expanduser("~/wisp-hotkey-debug.log")


def _dbg(msg: str) -> None:
    """Log a hotkey diagnostic line to stderr and the debug log file."""
    line = f"[hotkeys] {time.strftime('%H:%M:%S')} {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _session_diagnostics() -> dict[str, Any]:
    """Probe whether this process can actually receive global hotkeys.

    The most common reason hotkeys silently never fire is that the app is not
    running in an active, on-console window-server session (SSH/headless, or a
    remote/fast-user-switched login). RegisterEventHotKey cannot work there at
    all. These probes make that visible instead of looking like a code bug.
    """
    info: dict[str, Any] = {}
    try:
        import ctypes
        import ctypes.util

        app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices") or "ApplicationServices"
        )
        app_services.AXIsProcessTrusted.restype = ctypes.c_bool
        info["accessibility"] = bool(app_services.AXIsProcessTrusted())
    except Exception as exc:  # noqa: BLE001
        info["accessibility"] = f"unknown ({exc})"

    try:
        import Quartz  # type: ignore

        session = Quartz.CGSessionCopyCurrentDictionary()
        if session is None:
            # No window-server session at all -> almost certainly headless/SSH.
            info["window_server_session"] = False
            info["on_console"] = None
        else:
            info["window_server_session"] = True
            info["on_console"] = bool(session.get("kCGSessionOnConsoleKey", False))
            info["session_user"] = str(session.get("kCGSSessionUserNameKey", "") or "")
    except Exception as exc:  # noqa: BLE001
        info["window_server_session"] = f"unknown ({exc})"

    return info


def _protect_stdout():
    real_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real_out


def _become_ui_element() -> bool:
    """Give this background subprocess a window-server connection.

    Carbon ``RegisterEventHotKey`` registers successfully (status 0) from any
    process, but it only *delivers* hot-key events to a process the window
    server knows as a GUI app. A plain ``subprocess.Popen`` helper is a
    non-GUI ("background-only") process, so without this its hotkeys silently
    never fire. ``TransformProcessType`` to a UIElement app establishes the
    connection and adds no Dock icon. Must run on the main thread.
    """
    if sys.platform != "darwin":
        return False
    import ctypes
    import ctypes.util

    try:
        app_services = ctypes.CDLL(
            ctypes.util.find_library("ApplicationServices") or "ApplicationServices"
        )

        class _ProcessSerialNumber(ctypes.Structure):
            _fields_ = [
                ("highLongOfPSN", ctypes.c_uint32),
                ("lowLongOfPSN", ctypes.c_uint32),
            ]

        _kCurrentProcess = 2
        _kProcessTransformToUIElementApplication = 4

        transform = app_services.TransformProcessType
        transform.argtypes = [ctypes.POINTER(_ProcessSerialNumber), ctypes.c_uint32]
        transform.restype = ctypes.c_int32

        psn = _ProcessSerialNumber(0, _kCurrentProcess)
        status = transform(
            ctypes.byref(psn), _kProcessTransformToUIElementApplication
        )
        if status != 0:
            _dbg(f"TransformProcessType failed (status {status}) -- no window-server connection.")
            return False
        _dbg("Became UI element app (window-server connection established).")
        return True
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        _dbg(f"Could not become UI element: {exc}")
        return False


def _run_carbon_loop(stop: threading.Event) -> None:
    import ctypes
    import ctypes.util

    carbon = ctypes.CDLL(ctypes.util.find_library("Carbon") or "Carbon")
    run_current = carbon.RunCurrentEventLoop
    run_current.argtypes = [ctypes.c_double]
    run_current.restype = ctypes.c_int32
    while not stop.is_set():
        run_current(0.25)


def _stop_on_parent_pipe_close(stop: threading.Event) -> None:
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass
    stop.set()


def main() -> int:
    configure_paths()
    out = _protect_stdout()
    _dbg(f"helper starting (pid={os.getpid()}); debug log at {_DEBUG_LOG}")
    # Must happen on the main thread, before any hotkey is registered, or
    # Carbon delivers no events to this background process (see the function).
    ui_element = _become_ui_element()
    diagnostics = _session_diagnostics()
    _dbg(f"session diagnostics: {diagnostics}")
    if not diagnostics.get("window_server_session"):
        _dbg("NO window-server session -- global hotkeys cannot work here "
             "(running headless/SSH, or not in an active login session).")
    elif diagnostics.get("on_console") is False:
        _dbg("session is NOT on-console (remote / fast-user-switched) -- "
             "hotkeys may not receive keystrokes.")
    write_lock = threading.Lock()
    stop = threading.Event()

    def send(obj: dict[str, Any]) -> None:
        with write_lock:
            protocol.write_message(out, obj)

    def emit_hotkey(kind: str, **extra: Any) -> None:
        data = {"kind": kind, **extra}
        send({"event": "native.hotkey", "data": data})

    def request_stop(_signum=None, _frame=None) -> None:
        stop.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, request_stop)

    threading.Thread(
        target=_stop_on_parent_pipe_close,
        args=(stop,),
        daemon=True,
        name="hotkey-helper-parent-watch",
    ).start()

    try:
        import config
        from core.hotkeys import HotkeyListener

        caller_count = len(getattr(config, "CALLER_ROWS", []))
        callers = [
            (lambda idx=idx: emit_hotkey("caller", index=idx))
            for idx in range(caller_count)
        ]
        listener = HotkeyListener(
            on_callers=callers,
            on_add_context=lambda: emit_hotkey("add_context"),
            on_clear_context=lambda: emit_hotkey("clear_context"),
            on_snip=lambda: emit_hotkey("snip"),
            on_voice_start=lambda: emit_hotkey("voice_start"),
            on_voice_stop=lambda: emit_hotkey("voice_stop"),
        )
        started = bool(listener.start())
        _dbg(f"hotkey registration {'started' if started else 'FAILED'} "
             f"(ui_element={ui_element})")
        send(
            {
                "status": "started" if started else "failed",
                "started": started,
                "backend": "carbon-helper",
                "ui_element": ui_element,
                "diagnostics": diagnostics,
            }
        )
        if not started:
            return 1
        _run_carbon_loop(stop)
        listener.stop()
        return 0
    except Exception as exc:  # noqa: BLE001 - report startup failure to parent
        import traceback

        traceback.print_exc()
        send(
            {
                "status": "failed",
                "started": False,
                "backend": "carbon-helper",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
