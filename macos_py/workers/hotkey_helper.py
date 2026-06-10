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


# ---------------------------------------------------------------------------
# Off-console hotkey backend (CGEventTap)
# ---------------------------------------------------------------------------
# Carbon RegisterEventHotKey only delivers to the *console* session, so it never
# fires over RDP/VNC into a virtual session (e.g. a rented MacinCloud host, where
# on_console is False). A CGEventTap, by contrast, observes keystrokes inside our
# own session -- the WISP_HOTKEY_DEBUG monitor proved this works there. This
# backend reuses that proven listen-only tap, but matches each keyDown against
# the configured hotkeys and emits the same events the Carbon backend would.
#
# It reads only raw keycode + modifier flags on the main run loop (no off-thread
# character decoding), so it avoids the SIGTRAP that sinks pynput on macOS.

# Device-independent CGEventFlags modifier bits (NOT the Carbon mod masks).
_CGEVENT_MODS: dict[str, int] = {
    "shift": 0x00020000,
    "ctrl": 0x00040000, "control": 0x00040000,
    "alt": 0x00080000, "option": 0x00080000,
    "cmd": 0x00100000, "command": 0x00100000, "win": 0x00100000,
}
# Mask of just those four modifiers; used to ignore caps-lock/Fn/numpad bits
# (which ride along on F-keys) when matching, so the match is exact.
_CGEVENT_MOD_MASK = 0x00020000 | 0x00040000 | 0x00080000 | 0x00100000


def _parse_combo_to_tap(combo: str, vk_map: dict[str, int]) -> tuple[int, int] | None:
    """Return (keycode, modifier_mask) for a hotkey string, or None.

    Mirrors core.hotkeys parsing but yields a CGEventFlags modifier mask instead
    of Carbon masks. Bare/unsafe combos are rejected so ordinary typing keys are
    never captured.
    """
    from core.hotkeys import is_safe_global_hotkey

    if not is_safe_global_hotkey(combo):
        return None
    mods = 0
    keycode: int | None = None
    for part in (combo or "").lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part in _CGEVENT_MODS:
            mods |= _CGEVENT_MODS[part]
        elif part in vk_map:
            keycode = vk_map[part]
    return (keycode, mods) if keycode is not None else None


def _build_tap_table(
    specs: list[tuple[str, str, dict]], vk_map: dict[str, int]
) -> list[tuple[int, int, str, dict]]:
    """Build [(keycode, modmask, kind, extra), ...] from (combo, kind, extra)."""
    table: list[tuple[int, int, str, dict]] = []
    for combo, kind, extra in specs:
        parsed = _parse_combo_to_tap(combo, vk_map)
        if parsed is None:
            continue
        keycode, modmask = parsed
        table.append((keycode, modmask, kind, extra))
    return table


def _match_tap_event(
    keycode: int, flags: int, table: list[tuple[int, int, str, dict]]
) -> tuple[str, dict] | None:
    """Return (kind, extra) for the hotkey matching this keyDown, or None.

    Requires an exact modifier match within the four real modifiers, so ctrl+q
    does not fire when ctrl+shift+q is pressed, and vice versa.
    """
    event_mods = flags & _CGEVENT_MOD_MASK
    for kc, modmask, kind, extra in table:
        if kc == keycode and modmask == event_mods:
            return kind, extra
    return None


# Keep the hotkey event-tap + callback alive for the process lifetime.
_HOTKEY_TAP: Any = None


def _hotkey_specs_from_config(config: Any) -> list[tuple[str, str, dict]]:
    """The same hotkeys the Carbon backend registers, as (combo, kind, extra)."""
    specs: list[tuple[str, str, dict]] = []
    for i, row in enumerate(getattr(config, "CALLER_ROWS", []) or []):
        combo = (row or {}).get("hotkey")
        if combo:
            specs.append((combo, "caller", {"index": i}))
    for attr, kind in (
        ("HOTKEY_ADD_CONTEXT", "add_context"),
        ("HOTKEY_CLEAR_CONTEXT", "clear_context"),
        ("HOTKEY_SNIP", "snip"),
    ):
        combo = getattr(config, attr, "")
        if combo:
            specs.append((combo, kind, {}))
    return specs


def _install_hotkey_tap(table: list[tuple[int, int, str, dict]], emit) -> bool:
    """Install a listen-only tap that fires `emit(kind, **extra)` on a match.

    Listen-only: the matched key still reaches the foreground app (so e.g. ctrl+q
    may also do whatever ctrl+q does there). That is the safe trade-off -- a
    listen-only tap cannot swallow a key or strand a modifier.
    """
    global _HOTKEY_TAP
    if not table:
        _dbg("hotkey tap: no parseable hotkeys; not installing.")
        return False
    try:
        import Quartz  # type: ignore

        def _on_key(_proxy, type_, event, _refcon):
            try:
                if type_ == Quartz.kCGEventTapDisabledByTimeout or type_ == getattr(
                    Quartz, "kCGEventTapDisabledByUserInput", -2
                ):
                    Quartz.CGEventTapEnable(_HOTKEY_TAP[0], True)
                    return event
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                flags = int(Quartz.CGEventGetFlags(event))
                match = _match_tap_event(int(keycode), flags, table)
                if match is not None:
                    kind, extra = match
                    emit(kind, **extra)
            except Exception as exc:  # noqa: BLE001
                _dbg(f"hotkey tap callback error: {exc}")
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            _on_key,
            None,
        )
        if not tap:
            _dbg("hotkey tap: CGEventTapCreate returned NULL "
                 "(grant Input Monitoring to the Python/Wisp process).")
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)
        _HOTKEY_TAP = (tap, source, _on_key)
        _dbg(f"hotkey tap installed for {len(table)} hotkey(s) (off-console backend).")
        return True
    except Exception as exc:  # noqa: BLE001 - never crash the helper on the fallback
        _dbg(f"hotkey tap unavailable: {exc}")
        return False


def _teardown_hotkey_tap() -> None:
    global _HOTKEY_TAP
    tap = _HOTKEY_TAP
    _HOTKEY_TAP = None
    if not tap:
        return
    try:
        import Quartz  # type: ignore

        Quartz.CGEventTapEnable(tap[0], False)
        Quartz.CFRunLoopRemoveSource(
            Quartz.CFRunLoopGetCurrent(), tap[1], Quartz.kCFRunLoopCommonModes
        )
    except Exception:
        pass


# Keep the debug event-tap + callback alive for the process lifetime.
_DEBUG_KEY_TAP: Any = None


def _install_debug_key_monitor() -> bool:
    """Log the keycode of EVERY key press the process can see (opt-in).

    Enable with WISP_HOTKEY_DEBUG=1. Answers "is the app hearing my keystrokes
    at all?" -- independent of whether any hotkey matches. Listen-only Quartz
    tap reading only the raw keycode + flags on the main run loop, so it cannot
    swallow or modify events (it can't cause a stuck modifier).
    """
    global _DEBUG_KEY_TAP
    try:
        import Quartz  # type: ignore

        def _on_key(_proxy, _type, event, _refcon):
            try:
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                flags = int(Quartz.CGEventGetFlags(event))
                _dbg(f"heard keyDown keycode={keycode} flags={hex(flags)}")
            except Exception as exc:  # noqa: BLE001
                _dbg(f"key monitor callback error: {exc}")
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            _on_key,
            None,
        )
        if not tap:
            _dbg("debug key monitor: CGEventTapCreate returned NULL "
                 "(grant Input Monitoring, or wrong session).")
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)
        _DEBUG_KEY_TAP = (tap, source, _on_key)
        _dbg("debug key monitor installed (listen-only); press keys to see keycodes.")
        return True
    except Exception as exc:  # noqa: BLE001 - diagnostics must never crash the helper
        _dbg(f"debug key monitor unavailable: {exc}")
        return False


def _teardown_debug_key_monitor() -> None:
    """Disable the debug tap on graceful shutdown so nothing lingers."""
    global _DEBUG_KEY_TAP
    tap = _DEBUG_KEY_TAP
    _DEBUG_KEY_TAP = None
    if not tap:
        return
    try:
        import Quartz  # type: ignore

        Quartz.CGEventTapEnable(tap[0], False)
        Quartz.CFRunLoopRemoveSource(
            Quartz.CFRunLoopGetCurrent(), tap[1], Quartz.kCFRunLoopCommonModes
        )
    except Exception:
        pass


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
    if os.environ.get("WISP_HOTKEY_DEBUG") == "1":
        _install_debug_key_monitor()
    write_lock = threading.Lock()
    stop = threading.Event()

    def send(obj: dict[str, Any]) -> None:
        with write_lock:
            protocol.write_message(out, obj)

    def emit_hotkey(kind: str, **extra: Any) -> None:
        data = {"kind": kind, **extra}
        _dbg(f"HOTKEY FIRED -> {kind} {extra}")
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

        # Carbon only fires on the console session. Off-console (RDP/VNC into a
        # virtual session, e.g. a rented MacinCloud host) fall back to a tap that
        # observes keys inside our own session. Forceable with WISP_HOTKEY_TAP=1.
        forced_tap = os.environ.get("WISP_HOTKEY_TAP") == "1"
        use_tap = forced_tap or diagnostics.get("on_console") is False
        tap_active = False
        if use_tap:
            from core.hotkeys import MACOS_VIRTUAL_KEYCODES

            specs = _hotkey_specs_from_config(config)
            table = _build_tap_table(specs, MACOS_VIRTUAL_KEYCODES)
            tap_active = _install_hotkey_tap(table, emit_hotkey)

        # Off-console, Carbon "starts" but never fires; the tap is what makes
        # hotkeys actually work there, so count either backend as success.
        ok = started or tap_active
        send(
            {
                "status": "started" if ok else "failed",
                "started": ok,
                "backend": "event-tap" if tap_active else "carbon-helper",
                "ui_element": ui_element,
                "hotkey_tap": tap_active,
                "diagnostics": diagnostics,
            }
        )
        if not ok:
            return 1
        _run_carbon_loop(stop)
        _teardown_hotkey_tap()
        _teardown_debug_key_monitor()
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
