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
from typing import Any

from macos_py.bootstrap import configure_paths
from macos_py import protocol


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
            print(f"[hotkeys] TransformProcessType failed (status {status}).")
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        print(f"[hotkeys] Could not become UI element: {exc}")
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
    # Must happen on the main thread, before any hotkey is registered, or
    # Carbon delivers no events to this background process (see the function).
    _become_ui_element()
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
        send(
            {
                "status": "started" if started else "failed",
                "started": started,
                "backend": "carbon-helper",
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
