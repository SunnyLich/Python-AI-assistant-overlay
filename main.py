"""
main.py -" Entry point for Wisp.

Flow:
  Ctrl+E â†' IntentOverlay appears (WASD picker)
  WASD key â†' capture input â†' LLM stream â†' TTS stream â†' audio out
  Click icon â†' show full reply popup
"""
import sys
import os
import threading
import uuid
import logging
import signal
import traceback
from typing import Callable
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QObject, Signal, Qt, QTimer

_IS_WIN = sys.platform == "win32"
_win_console_handler = None
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")

# --- File logging setup -----------------------------------------------------------
_LOG_PATH = os.path.join(os.path.dirname(__file__), "wisp.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("wisp")

# --- Native crash diagnostics ----------------------------------------------------
# Python excepthooks below only see Python exceptions. macOS "trace trap" (SIGTRAP)
# and aborts come from native frameworks (e.g. AppKit/HIToolbox called off the main
# thread) and bypass them entirely. faulthandler dumps a traceback of every thread
# at the moment of the fault, pinpointing the exact crashing call. SIGTRAP is not in
# faulthandler's default set, so register it (and friends) explicitly. chain=True
# re-raises after dumping, so the process still crashes as before — we just get the
# stack first.
#
# The dump MUST go to a persistent file, not just sys.stderr: when the app is
# launched detached (the `.command` launcher or the native Wisp.app bundle), stderr
# is discarded, so a native crash leaves no trace at all — that is why the macOS
# segfaults were invisible (no .crash file is written either, since those only come
# from Python exceptions). We open a dedicated fault log next to wisp.log, keep the
# handle alive for the whole process, and point faulthandler at it. faulthandler
# writes to the raw file descriptor, so it stays crash-safe. When attached to a
# terminal we *also* mirror to stderr for inline visibility. Harmless on Win/Linux.
_FAULT_LOG_PATH = os.path.join(os.path.dirname(__file__), "wisp-fault.log")
_fault_file = None  # module-global so the fd is never closed/GC'd before a crash
try:
    import faulthandler
    import signal as _signal
    try:
        _fault_file = open(_FAULT_LOG_PATH, "a", buffering=1, encoding="utf-8")
        import datetime as _dt
        _fault_file.write(f"\n===== Wisp session started {_dt.datetime.now().isoformat()} "
                          f"(pid {os.getpid()}) =====\n")
        _fault_file.flush()
    except Exception:
        _fault_file = None
    # Primary sink: the persistent file (survives detached launches). If we could
    # not open it, fall back to stderr so we never lose the dump entirely.
    _fault_sink = _fault_file if _fault_file is not None else sys.stderr
    faulthandler.enable(file=_fault_sink, all_threads=True)
    for _sig_name in ("SIGTRAP", "SIGABRT", "SIGBUS", "SIGILL", "SIGSEGV"):
        _sig = getattr(_signal, _sig_name, None)
        if _sig is None:
            continue
        try:
            faulthandler.register(_sig, file=_fault_sink, all_threads=True, chain=True)
        except (ValueError, OSError, RuntimeError):
            pass
except Exception:  # never let diagnostics break startup
    pass
# ---------------------------------------------------------------------------------

def _thread_excepthook(args):
    """Capture unhandled exceptions in daemon threads, log them, and write a crash file."""
    if args.exc_type is SystemExit:
        return
    thread_name = args.thread.name if args.thread else "<unknown>"
    log.error(
        "Unhandled exception in thread %s:\n%s",
        thread_name,
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_tb)),
    )
    from core.crash import write_crash_report
    path = write_crash_report(
        args.exc_type, args.exc_value, args.exc_tb, thread_name=thread_name
    )
    if path:
        log.error("Crash report written to: %s", path)

threading.excepthook = _thread_excepthook


def _main_excepthook(exc_type, exc_value, exc_tb):
    """sys.excepthook for unhandled exceptions on the main thread."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.error(
        "Unhandled main-thread exception:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    from core.crash import write_crash_report
    path = write_crash_report(exc_type, exc_value, exc_tb)
    if path:
        log.error("Crash report written to: %s", path)

sys.excepthook = _main_excepthook
# ---------------------------------------------------------------------------------
import config
import core.plugin_manager as _plugin_manager_mod
from core.hotkeys import HotkeyListener
from core import capture, audio, context_fetcher, stt
from core.llm_clients import client as llm
from core.assistant_text import ThoughtStreamParser
from core import tts as tts_module
from core.memory_store import store as memory_module
from core.conversation_store import store as conversation_store
from core.query_pipeline import GenerationCounter, ContextInputs, build_context
from core.system.app_platform import configure_windows_app_identity
from core.system import macos_safety
from core.system import sdk_clients
from core.system.paths import ASSETS_DIR
from core.memory_store.commands import extract_remember_fact
from core import context_hotkey
from macos_py.supervisor import tool_modes
from ui.overlay import IconOverlay, OverlaySignals
from ui.intent_overlay import IntentOverlay
from ui.snip_overlay import SnipOverlay
from ui.chat_window import ChatWindow
from ui.shared.theme import apply_app_theme

sdk_clients.install_proxy_guard()


def _build_app_icon() -> QIcon:
    """Return the best available app icon (app.ico, then idle.png as fallback)."""
    for name in ("app.ico", "idle.png"):
        p = str(ASSETS_DIR / name)
        icon = QIcon(p)
        if not icon.isNull():
            return icon
    idle_p = str(ASSETS_DIR / "doll" / "idle.png")
    return QIcon(idle_p)


class _MainThreadInvoker(QObject):
    """Runs a callable on the GUI main thread and blocks the caller for its result.

    Used so worker threads can perform main-thread-only macOS work (opening the
    CoreAudio output stream) without freezing the UI. The slot is connected with a
    queued connection, so emitting from a worker hops the call onto the main thread;
    the worker waits on an Event for completion."""
    _post = Signal(object)

    def __init__(self):
        super().__init__()
        self._post.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, job: dict) -> None:
        try:
            job["result"] = job["fn"]()
        except BaseException as exc:  # propagate to the waiting worker
            job["error"] = exc
        finally:
            job["event"].set()

    def run_on_main(self, fn):
        # Already on the main thread → run directly (a queued call would deadlock).
        if threading.current_thread() is threading.main_thread():
            return fn()
        job = {"fn": fn, "event": threading.Event()}
        self._post.emit(job)
        job["event"].wait()
        if "error" in job:
            raise job["error"]
        return job.get("result")


class App(QObject):
    _shutdown_requested = Signal(str)

    # App is a QObject so that signal connections to its own slots (below) carry a
    # receiver thread affinity. Without it, PySide6 connects a signal to a method of
    # a non-QObject receiver as a DirectConnection (there is no receiver QThread to
    # queue against), so the slot runs on whichever thread emits — including the
    # _query_and_speak / LLM / TTS worker threads and the Carbon hotkey-dispatch
    # thread. Those slots create and mutate Qt widgets (the intent picker, chat
    # window, bubble), and doing Cocoa/AppKit work off the main thread segfaults
    # under Qt's run loop on macOS. With App as a QObject living on the main thread,
    # Qt.AutoConnection upgrades every cross-thread emit to a QueuedConnection, so
    # the slots actually run on the main thread (the behaviour the summon/hotkey
    # comments already assume).
    def __init__(self):
        super().__init__()  # QObject base — before any attribute assignment or signal wiring

        from core.system.paths import ADDONS_DIR
        self._plugin_manager = _plugin_manager_mod.init(ADDONS_DIR)

        configure_windows_app_identity()
        self._qt = QApplication(sys.argv)
        self._qt.setApplicationName("Wisp")
        self._qt.setApplicationDisplayName("Wisp")
        self._qt.setQuitOnLastWindowClosed(False)  # chat/settings closing must not exit the app
        _app_icon = _build_app_icon()
        if not _app_icon.isNull():
            self._qt.setWindowIcon(_app_icon)
        apply_app_theme(self._qt)
        from ui.i18n import install as install_i18n
        install_i18n(self._qt)
        self._signals = OverlaySignals()
        self._overlay = IconOverlay(self._signals)
        self._hotkeys = self._build_hotkey_listener()
        self._context_buffer: list[str] = []       # accumulated via Alt+Q
        self._context_buffer_lock = threading.Lock()
        self._drop_context_items: list[tuple] = []  # (name, content, type) from drag-drop
        self._drop_context_lock = threading.Lock()
        self._last_reply: str = ""
        self._pending_context: context_fetcher.ContextSnapshot | None = None
        self._voice_active: bool = False        # guard against spurious release events
        self._dictate_active: bool = False      # F8 push-to-talk dictation guard
        self._dictate_target_hwnd: int = 0      # window to paste the transcript into
        self._intent_picker: IntentOverlay | None = None
        self._snip_overlay: SnipOverlay | None = None
        self._chat_window: ChatWindow | None = None
        self._memory_viewer = None
        # Persisted chat history: each item is a dict with at least
        # {"id", "project_id", "messages": [...], "context": str}.
        self._active_project_id: str = conversation_store.GENERAL_PROJECT_ID
        # Conversation hotkey/voice prompts continue. None on startup so the
        # first prompt always opens a fresh conversation; updated when the user
        # selects/starts a conversation in the chat window. Not persisted.
        self._active_conversation_idx: int | None = None
        try:
            self._all_conversations: list[dict] = conversation_store.load_conversations()
        except Exception:
            log.exception("failed to load persisted conversations")
            self._all_conversations = []
        self._overlay_hwnd: int = 0          # cached after first show
        self._hotkey_warning_shown = False
        self._shutdown_started = False
        self._shutdown_lock = threading.Lock()
        self._pending_capture: tuple | None = None  # (selected_text, screenshot_b64)
        self._pending_caller_idx: int = 0    # which CALLER_ROWS entry triggered the current picker
        self._pending_context_policy: dict | None = None
        self._pending_paste_target: int = 0  # HWND to paste into (0 = no paste)
        self._pending_intent_target: int = 0 # HWND whose monitor should host the picker
        self._generations = GenerationCounter()  # bumped per query; stale workers skip bubble signals

        # Memory
        self._memory = memory_module.get_manager()
        self._apply_memory_project()

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._signals.show_snip_overlay.connect(self._show_snip_overlay)
        self._signals.settings_applied.connect(self._on_settings_applied)
        self._signals.show_new_chat.connect(self._on_show_new_chat)
        self._signals.show_last_chat.connect(self._on_show_last_chat)
        self._signals.show_memory_viewer.connect(self._open_memory_viewer)
        self._signals.bubble_highlight.connect(self._on_bubble_highlight)
        self._signals.chat_new_conversation.connect(self._on_chat_new_conversation)
        self._signals.chat_sync_conversation.connect(self._on_chat_sync_conversation)
        self._signals.context_items_dropped.connect(self._on_context_items_dropped)
        self._signals.remove_dropped_item.connect(self._on_remove_dropped_item)
        self._signals.summon_caller.connect(self._on_summon_caller)
        self._shutdown_requested.connect(self.request_shutdown, Qt.ConnectionType.QueuedConnection)
        self._overlay.set_click_handler(self._on_icon_click)

        # On macOS, register a runner that hops main-thread-only native work onto
        # the GUI thread. One registration covers every module that touches CoreAudio
        # (filler + TTS streams in core.audio, the mic stream in core.stt) and
        # AppKit/Quartz (key injection, window focus/enumeration, screen capture via
        # core.platform_utils / core.capture) — all share core.system.main_thread.
        # Doing this work on a worker thread segfaults/trace-traps under Qt's Cocoa
        # run loop. No-op on Windows/Linux (runner stays unregistered -> runs inline).
        if sys.platform == "darwin":
            self._main_invoker = _MainThreadInvoker()
            audio.set_main_thread_runner(self._main_invoker.run_on_main)

        # Pre-warm connections in background
        threading.Thread(target=self._prewarm, daemon=True).start()

        # Start fs watcher (watches Desktop/Documents/Downloads in background)
        context_fetcher.start_fs_watcher()

        # Pre-load the Whisper model so the first voice query has no cold start.
        # on_ready fires once the model is warmed so we can surface which backend
        # it actually landed on (and flag a silent GPU→CPU fallback).
        if macos_safety.stt_prewarm_enabled():
            stt.prewarm(on_ready=self._on_stt_ready)

        # Notify mods that the app is fully initialised
        from core.plugin_manager import AppContext
        from core.llm_clients.client import get_tool_registry
        self._plugin_manager.on_startup(AppContext(
            signals=self._signals,
            model_tool_registry=get_tool_registry(),
            config=config,
        ))
        # Surface each addon's declared startup notification (e.g. healthcheck's
        # "loaded" message) — the macOS supervisor does this; Windows never did.
        # Deferred so the tray icon is live and the event loop is running, else
        # the balloon is dropped.
        QTimer.singleShot(1500, self._show_addon_notifications)
        self._qt.aboutToQuit.connect(lambda: self.shutdown("qt quit"))
        self._signal_timer = QTimer(self)
        self._signal_timer.setInterval(250)
        self._signal_timer.timeout.connect(lambda: None)
        self._signal_timer.start()

    def request_shutdown(self, reason: str = "") -> None:
        """Ask the Qt event loop to exit from any thread or signal handler."""
        reason = reason or "shutdown requested"
        log.info("Shutdown requested: %s", reason)
        self.shutdown(reason)
        self._qt.quit()

    def shutdown(self, reason: str = "") -> None:
        """Stop background hooks/processes so the launcher terminal can exit."""
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
        log.info("Shutting down Wisp%s", f": {reason}" if reason else "")
        try:
            self._generations.next()
        except Exception:
            log.exception("generation shutdown marker failed")
        try:
            self._hotkeys.stop()
        except Exception:
            log.exception("hotkey shutdown failed")
        try:
            self._plugin_manager.on_shutdown()
        except Exception:
            log.exception("addon shutdown failed")
        try:
            self._memory.shutdown()
        except Exception:
            log.exception("memory shutdown failed")
        try:
            context_fetcher.stop_fs_watcher()
        except Exception:
            log.exception("context watcher shutdown failed")

    def _build_hotkey_listener(self) -> HotkeyListener:
        """Construct the HotkeyListener with all callbacks wired. Used at startup
        and again whenever settings change re-register hotkeys live."""
        # On macOS the caller path must not run AppKit/automation off the main
        # thread (it trace-traps), so route hotkeys through the same main-thread
        # summon the icon click uses. The Carbon dispatch fires on the main thread
        # but wraps callbacks in a worker thread; emitting a Qt signal hops safely
        # back to the main thread either way.
        if sys.platform == "darwin":
            on_callers = [lambda i=i: self._signals.summon_caller.emit(i)
                          for i in range(len(config.CALLER_ROWS))]
        else:
            on_callers = [lambda i=i: self._on_caller_hotkey(i)
                          for i in range(len(config.CALLER_ROWS))]
        return HotkeyListener(
            on_callers=on_callers,
            on_add_context=self._on_add_context,
            on_clear_context=self._on_clear_context,
            on_snip=self._on_snip_hotkey,
            on_voice_start=self._on_voice_start,
            on_voice_stop=self._on_voice_stop,
            on_dictate_start=self._on_dictate_start,
            on_dictate_stop=self._on_dictate_stop,
            extra_hotkeys=self._addon_hotkey_callbacks(),
        )

    def _addon_hotkey_callbacks(self) -> list[tuple[str, Callable[[], None]]]:
        callbacks: list[tuple[str, Callable[[], None]]] = []
        try:
            hotkeys = self._plugin_manager.get_hotkeys()
        except Exception:
            return callbacks
        for item in hotkeys:
            combo = str(item.get("hotkey") or "")
            addon_id = str(item.get("addon_id") or "")
            hotkey_id = str(item.get("id") or "")
            if combo and addon_id and hotkey_id:
                callbacks.append((combo, lambda aid=addon_id, hid=hotkey_id: self._on_addon_hotkey(aid, hid)))
        return callbacks

    def _on_addon_hotkey(self, addon_id: str, hotkey_id: str) -> None:
        try:
            result = self._plugin_manager.run_hotkey(addon_id, hotkey_id)
        except Exception:
            log.exception("addon hotkey failed: %s / %s", addon_id, hotkey_id)
            return
        prompt = str(result.get("prompt") or "").strip() if isinstance(result, dict) else ""
        if prompt:
            self._pending_capture = None
            self._pending_caller_idx = 0
            self._pending_context_policy = config.CALLER_ROWS[0] if config.CALLER_ROWS else {}
            self._pending_paste_target = 0
            self._pending_intent_target = 0
            self._on_intent_chosen("addon", prompt)
            return
        message = str(result.get("message") or "").strip() if isinstance(result, dict) else ""
        if message and hasattr(self._overlay, "notify_agent_approval"):
            self._overlay.notify_agent_approval(message, resolved=True)

    @staticmethod
    def _hotkey_signature() -> tuple:
        """Snapshot of every hotkey binding, used to detect whether a settings
        apply actually changed the hotkeys. Recreating the pynput listener is
        only safe to do when something here changed — on macOS recreating it
        while the Cocoa run loop is active trace-traps inside pynput's
        keycode_context, so we must avoid needless restarts."""
        return (
            tuple(r.get("hotkey") for r in config.CALLER_ROWS),
            config.HOTKEY_ADD_CONTEXT,
            config.HOTKEY_CLEAR_CONTEXT,
            config.HOTKEY_SNIP,
            config.HOTKEY_VOICE,
            config.HOTKEY_DICTATE,
            config.DICTATE_MODE,
        )

    def _set_idle(self) -> None:
        """Return the overlay to its resting state."""
        self._signals.set_state.emit("idle")
        if config.ICON_AUTO_HIDE:
            self._signals.hide_icon.emit()

    @staticmethod
    def _context_mode(caller: dict, name: str) -> str:
        return tool_modes.context_mode(caller, name)

    @staticmethod
    def _window_pid_win(hwnd: int) -> int:
        return context_hotkey.window_pid_win(hwnd, is_win=_IS_WIN)

    @staticmethod
    def _window_title_win(hwnd: int) -> str:
        return context_hotkey.window_title_win(hwnd, is_win=_IS_WIN)

    @staticmethod
    def _is_external_context_window_win(hwnd: int) -> bool:
        return context_hotkey.is_external_context_window_win(
            hwnd,
            is_win=_IS_WIN,
            pid_for_hwnd=App._window_pid_win,
            title_for_hwnd=App._window_title_win,
        )

    @staticmethod
    def _find_external_context_window_win(start_hwnd: int) -> int:
        return context_hotkey.find_external_context_window_win(
            start_hwnd,
            is_win=_IS_WIN,
            is_external=App._is_external_context_window_win,
        )

    def _context_target_hwnd(self, foreground_hwnd: int) -> int:
        """Use the real app behind Wisp if Wisp already owns foreground focus."""
        if not _IS_WIN or not foreground_hwnd:
            return foreground_hwnd
        foreground_pid = self._window_pid_win(foreground_hwnd)
        if foreground_pid and foreground_pid != os.getpid():
            return foreground_hwnd
        replacement = self._find_external_context_window_win(foreground_hwnd)
        if replacement:
            log.info(
                "context target corrected raw_hwnd=%s raw_title=%r -> hwnd=%s pid=%s title=%r",
                foreground_hwnd,
                self._window_title_win(foreground_hwnd),
                replacement,
                self._window_pid_win(replacement),
                self._window_title_win(replacement),
            )
            return replacement
        return foreground_hwnd

    @staticmethod
    def _append_context_block(ambient_context: str, extra: str) -> str:
        extra = (extra or "").strip()
        if not extra:
            return ambient_context
        return f"{ambient_context}\n\n---\n{extra}".strip() if ambient_context else extra

    @staticmethod
    def _tool_overrides(caller: dict) -> dict[str, str]:
        return tool_modes.tool_overrides(caller)

    def _allowed_model_tools(self, caller: dict) -> list[str]:
        return tool_modes.allowed_model_tools(caller)

    def _pinned_model_tools(self, caller: dict) -> list[str]:
        return tool_modes.pinned_model_tools(caller)

    def _screenshot_tool_allowed(self, caller: dict) -> bool:
        return tool_modes.screenshot_tool_allowed(caller)

    @staticmethod
    def _browser_context_text(snapshot) -> str:
        return context_hotkey.browser_context_text(snapshot)

    @staticmethod
    def _is_browser_window(window) -> bool:
        return context_hotkey.is_browser_window(window)

    @classmethod
    def _context_priority_source(cls, snapshot, ambient_text: str, active_document_text: str) -> str:
        return context_hotkey.context_priority_source(snapshot, ambient_text, active_document_text)

    def _finish_idle(self, gen_id: int) -> None:
        """Return to idle only if this generation is still the active one."""
        if self._generations.is_current(gen_id):
            self._set_idle()

    def run(self):
        if not config.ICON_AUTO_HIDE:
            self._overlay.show()
        self._overlay_hwnd = int(self._overlay.winId())  # safe: Qt main thread
        if not self._hotkeys.start():
            self._warn_hotkeys_unavailable()
        print(f"[main] Wisp running. Callers: {[c['hotkey'] for c in config.CALLER_ROWS]}")
        return int(self._qt.exec())

    def _warn_hotkeys_unavailable(self) -> None:
        if self._hotkey_warning_shown:
            return
        self._hotkey_warning_shown = True
        if sys.platform == "darwin":
            QMessageBox.information(
                self._overlay,
                "Summoning Wisp on macOS",
                "Global hotkeys could not be registered in this session.\n\n"
                "You can still summon Wisp anytime by left-clicking the floating icon, "
                "or by choosing “Ask Wisp” from its right-click / menu bar menu.",
            )
        else:
            QMessageBox.warning(
                self._overlay,
                "Hotkeys unavailable",
                "Global hotkeys could not be started in this session. Check the app log for details.",
            )

    # ------------------------------------------------------------------
    # Pre-warm
    # ------------------------------------------------------------------

    def _prewarm(self):
        if macos_safety.audio_enabled():
            audio.prewarm_filler()  # decode filler WAVs so the hotkey path does no disk I/O
        if macos_safety.tts_prewarm_enabled():
            tts_module.prewarm()
        # Build the LLM client now too. When TTS is enabled, doing this
        # sequentially keeps the two SSL contexts from being built concurrently
        # on the first query (Security-framework segfault); otherwise it still
        # removes the first query's handshake.
        llm.prewarm()
        print("[main] Connections pre-warmed.")

    def _on_stt_ready(self, backend: dict | None) -> None:
        """Called (on the STT worker thread) once the Whisper model is warmed.
        Shows the active backend so the user can see at startup whether a
        cuda/float16 request actually got the GPU or quietly fell back."""
        if not backend:
            return
        summary = f"{backend.get('model')} · {backend.get('device')} / {backend.get('compute')}"
        if backend.get("degraded"):
            title = "Speech model ready — GPU not in use"
            message = (
                f"Loaded {summary}. Expected GPU/float16; transcription quality "
                "may be reduced. Free the GPU and restart to recover it."
            )
        else:
            title = "Speech model ready"
            message = f"Loaded {summary}."
        # Cross-thread → queued onto the Qt main thread by the signal.
        self._signals.status_notification.emit(title, message)

    def _show_addon_notifications(self) -> None:
        """Surface each addon's declared startup notification (matches the macOS
        supervisor's _show_addon_notifications)."""
        try:
            items = self._plugin_manager.get_notifications()
        except Exception:
            log.exception("addon startup notifications failed")
            return
        for item in items or []:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message") or "").strip()
            if not message:
                continue
            title = str(item.get("title") or item.get("addon_id") or "Wisp").strip()
            self._signals.status_notification.emit(title, message)

    # ------------------------------------------------------------------
    # Settings applied -" reload config + re-register hotkeys live
    # ------------------------------------------------------------------

    def _on_settings_applied(self):
        before = self._hotkey_signature()
        config.reload()
        tts_module.reset_connections()
        # Only rebuild the global hotkey listener when a binding actually
        # changed. Recreating it on every apply needlessly tears down and
        # recreates pynput's keyboard listener, which trace-traps on macOS
        # (keycode_context touches main-thread-only HIToolbox APIs).
        if self._hotkey_signature() != before:
            self._hotkeys.stop()
            self._hotkeys = self._build_hotkey_listener()
            if self._hotkeys.start():
                print("[main] Hotkeys changed — listener re-registered.")
            else:
                self._warn_hotkeys_unavailable()
        self._overlay.apply_settings()
        apply_app_theme(self._qt)
        print("[main] Config reloaded.")

    # ------------------------------------------------------------------
    # Hotkey â†' show intent picker (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_add_context(self):
        """Alt+Q -" capture selected text and append to context buffer."""
        text = capture.get_selected_text()
        if text:
            with self._context_buffer_lock:
                self._context_buffer.append(text)
            print(f"[main] Context buffer: {len(self._context_buffer)} item(s) queued.")

    def _on_clear_context(self):
        """Alt+W — clear the context buffer and any dropped context."""
        with self._context_buffer_lock:
            self._context_buffer.clear()
        with self._drop_context_lock:
            self._drop_context_items.clear()
        self._signals.drop_context_cleared.emit()
        print("[main] Context buffer cleared.")

    def _on_context_items_dropped(self, items: list) -> None:
        """Called when files/text are dropped onto the icon."""
        with self._drop_context_lock:
            self._drop_context_items.extend(items)
        print(f"[main] Drop context: {len(items)} item(s) queued.")

    def _on_remove_dropped_item(self, idx: int) -> None:
        """User clicked X on a context badge — remove that item from the queue."""
        with self._drop_context_lock:
            if 0 <= idx < len(self._drop_context_items):
                removed = self._drop_context_items.pop(idx)
                print(f"[main] Removed drop context item: {removed[0]!r}")

    def _steal_foreground(self) -> None:
        """Bring the overlay to the foreground from the keyboard-hook thread."""
        if _IS_WIN:
            if not self._overlay_hwnd:
                return
            try:
                import ctypes
                user32   = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                fg_hwnd  = user32.GetForegroundWindow()
                fg_tid   = user32.GetWindowThreadProcessId(fg_hwnd, None)
                our_tid  = kernel32.GetCurrentThreadId()
                if fg_tid and fg_tid != our_tid:
                    user32.AttachThreadInput(fg_tid, our_tid, True)
                user32.SetForegroundWindow(self._overlay_hwnd)
                if fg_tid and fg_tid != our_tid:
                    user32.AttachThreadInput(fg_tid, our_tid, False)
            except Exception:
                pass
        else:
            self._signals.raise_overlay.emit()

    def _on_snip_hotkey(self):
        """Ctrl+Alt+Q — show the region selector, then the intent picker."""
        from core.platform_utils import get_foreground_window
        self._pending_intent_target = get_foreground_window()
        self._steal_foreground()
        self._signals.show_snip_overlay.emit()

    def _on_summon_caller(self, caller_idx: int):
        """Summon the prompt without a global hotkey (icon click / tray / macOS
        hotkey). Runs on the Qt main thread (signal slot)."""
        if sys.platform == "darwin":
            # macOS: stay on the main thread and skip the off-main-thread AppKit
            # automation (foreground capture, synthesised copy, window/document
            # context) that trace-traps. The user gets a clean prompt; any AppKit
            # work (focusing the picker) happens here on the main thread.
            self._summon_caller_macos(caller_idx)
        else:
            # Other platforms: _on_caller_hotkey synthesises a clipboard copy and
            # blocks, so run it off the main thread like the hotkey listener does.
            threading.Thread(
                target=self._on_caller_hotkey,
                args=(caller_idx,),
                daemon=True,
            ).start()

    def _summon_caller_macos(self, caller_idx: int) -> None:
        if config.ICON_AUTO_HIDE:
            self._signals.show_icon.emit()
        caller = dict(config.CALLER_ROWS[caller_idx]) if caller_idx < len(config.CALLER_ROWS) else {}
        # Disable the context sources whose capture queries windows/documents off
        # the main thread; these are the macOS trace-trap sources.
        caller["context_ambient"] = False
        caller["context_documents"] = False
        caller["context_screenshot"] = "off"
        caller["paste_back"] = False

        self._pending_caller_idx = caller_idx
        self._pending_context_policy = caller
        self._pending_paste_target = 0
        self._pending_intent_target = 0
        self._pending_capture = (None, None)
        self._pending_context = None

        audio.play_filler()
        self._signals.set_state.emit("listening")
        self._show_intent_picker(caller_idx)

    def _on_caller_hotkey(self, caller_idx: int):
        """Called when any caller hotkey fires. Dispatches based on caller's paste_back flag."""
        from core.platform_utils import get_foreground_window
        caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
        paste_back = caller.get("paste_back", False)

        # Save the foreground window ID BEFORE stealing focus so the picker can open
        # on the same monitor and paste-back callers can restore focus correctly.
        raw_fg_hwnd = get_foreground_window()
        fg_hwnd = self._context_target_hwnd(raw_fg_hwnd)
        target_hwnd = fg_hwnd if paste_back else 0
        try:
            from core.platform_utils import get_window_pid, get_window_title
            log.info(
                "context hotkey target raw_hwnd=%s hwnd=%s pid=%s title=%r modes: docs=%s browser=%s screenshot=%s",
                raw_fg_hwnd,
                fg_hwnd,
                get_window_pid(fg_hwnd),
                get_window_title(fg_hwnd),
                self._context_mode(caller, "documents"),
                self._context_mode(caller, "browser"),
                caller.get("context_screenshot"),
            )
        except Exception:
            log.exception("context hotkey target logging failed")

        if config.ICON_AUTO_HIDE:
            self._signals.show_icon.emit()

        # Capture selected text NOW while the original window still has focus;
        # get_selected_text() synthesises Ctrl+C so it must precede steal_foreground().
        selected = capture.get_selected_text()

        self._pending_caller_idx = caller_idx
        self._pending_context_policy = caller
        self._pending_paste_target = target_hwnd
        self._pending_intent_target = fg_hwnd

        self._steal_foreground()
        audio.play_filler()
        self._signals.set_state.emit("listening")

        # Move file I/O and optional screenshot off the hook thread.
        # Emit the picker only after all capture work is complete.
        def _fetch_and_show():
            try:
                self._pending_context = context_fetcher.fetch_and_save(active_hwnd=fg_hwnd)
                win = self._pending_context.active_window
                log.info(
                    "context snapshot hwnd=%s pid=%s process=%r title=%r url=%r browser_chars=%d",
                    win.hwnd,
                    win.pid,
                    win.process_name,
                    win.title,
                    win.url,
                    len(self._pending_context.browser_content or ""),
                )
                screenshot_b64 = None
                if caller.get("context_screenshot") == "auto" and not selected:
                    img = capture.get_screen_snippet()
                    screenshot_b64 = capture.image_to_base64(img)
                self._pending_capture = (selected, screenshot_b64)
                self._signals.show_intent_picker.emit(caller_idx)
            except Exception:
                log.exception("_fetch_and_show crashed")
                self._set_idle()

        threading.Thread(target=_fetch_and_show, daemon=True).start()

    # ------------------------------------------------------------------
    # Snip overlay (runs on Qt main thread via signal)
    # ------------------------------------------------------------------

    def _show_snip_overlay(self):
        if self._snip_overlay is not None:
            return
        self._snip_overlay = SnipOverlay()
        self._snip_overlay.region_selected.connect(self._on_region_selected)
        self._snip_overlay.cancelled.connect(self._on_snip_cancelled)
        self._snip_overlay.show()

    def _on_region_selected(self, region: dict):
        self._snip_overlay = None
        img = capture.get_screen_snippet(region)
        screenshot_b64 = capture.image_to_base64(img)
        self._pending_capture = (None, screenshot_b64)
        self._pending_caller_idx = 0
        self._pending_context_policy = {
            "context_ambient": config.SNIP_CONTEXT_AMBIENT,
            "context_documents": config.SNIP_CONTEXT_DOCUMENTS,
            "context_tools": config.SNIP_CONTEXT_TOOLS,
        }
        self._pending_paste_target = 0

        if config.ICON_AUTO_HIDE:
            self._signals.show_icon.emit()

        audio.play_filler()
        self._signals.set_state.emit("listening")
        self._signals.show_intent_picker.emit(0)

    def _on_snip_cancelled(self):
        self._snip_overlay = None
        self._pending_context_policy = None

    # ------------------------------------------------------------------
    # Voice push-to-talk (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_voice_start(self):
        """F9 key-down -" start recording."""
        if self._voice_active:
            return  # ignore held-down repeat events
        self._voice_active = True
        audio.stop()  # cut off any reply still being spoken before recording
        self._pending_context = context_fetcher.fetch_and_save()
        stt.start_recording()
        if config.ICON_AUTO_HIDE:
            self._signals.show_icon.emit()
        self._signals.set_state.emit("listening")
        self._signals.bubble_listening.emit()

    def _on_voice_stop(self):
        """F9 key-up -" stop recording, transcribe, and query."""
        if not self._voice_active:
            return
        self._voice_active = False
        # Transcription is blocking (~200-"600 ms) -" run off the hotkey thread.
        threading.Thread(target=self._voice_transcribe_and_query, daemon=True).start()

    def _voice_transcribe_and_query(self):
        text = stt.stop_and_transcribe()
        if not text:
            self._set_idle()
            return
        self._signals.set_state.emit("thinking")
        gen_id = self._generations.next()
        self._query_and_speak(text, (None, None), gen_id=gen_id)

    # ------------------------------------------------------------------
    # Push-to-talk dictation (F8): transcribe straight into the focused field,
    # no assistant. Mirrors the voice flow's icon states (orange listening, and
    # purple thinking only while LLM cleanup runs).
    # ------------------------------------------------------------------

    def _on_dictate_start(self):
        """HOTKEY_DICTATE key-down → remember the focused field, then record."""
        if self._dictate_active:
            return  # ignore held-down repeat events
        self._dictate_active = True
        # Snapshot the target window now, before recording/icon can steal focus,
        # so the transcript pastes back where the user was typing.
        from core.platform_utils import get_foreground_window
        self._dictate_target_hwnd = self._context_target_hwnd(get_foreground_window())
        audio.stop()  # cut off any reply still being spoken before recording
        stt.start_recording()
        if config.ICON_AUTO_HIDE:
            self._signals.show_icon.emit()
        self._signals.set_state.emit("listening")
        self._signals.bubble_listening.emit()

    def _on_dictate_stop(self):
        """HOTKEY_DICTATE key-up → stop, transcribe, optional cleanup, paste."""
        if not self._dictate_active:
            return
        self._dictate_active = False
        # Transcription blocks (~200–600 ms) → run off the hotkey thread.
        threading.Thread(target=self._dictate_transcribe_and_paste, daemon=True).start()

    def _dictate_transcribe_and_paste(self):
        target_hwnd = self._dictate_target_hwnd
        self._dictate_target_hwnd = 0
        text = stt.stop_and_transcribe()
        if not text:
            self._set_idle()
            return
        if str(getattr(config, "DICTATE_MODE", "raw")).lower() == "llm":
            self._signals.set_state.emit("thinking")  # purple while the LLM cleans up
            text = self._dictation_cleanup(text)
        self._paste_dictation(text, target_hwnd)
        self._set_idle()

    def _dictation_cleanup(self, text: str) -> str:
        """Run the raw transcript through the LLM for punctuation/filler cleanup.
        Any failure falls back to the raw text so dictation always pastes."""
        prompt = (
            "This is a raw speech-to-text dictation. Fix punctuation, "
            "capitalization, and obvious transcription slips, and remove filler "
            "words. Output ONLY the cleaned text, nothing else."
        )
        try:
            cleaned = "".join(llm.stream_rewrite(text, prompt)).strip()
            return cleaned or text
        except Exception as exc:  # noqa: BLE001 — never block a paste on cleanup
            print(f"[main] Dictation cleanup failed; pasting raw transcript: {exc}")
            return text

    def _paste_dictation(self, text: str, target_hwnd: int):
        """Paste *text* into the field that was focused when dictation started."""
        import time
        try:
            from core.platform_utils import set_foreground_window, send_keys, PASTE_COMBO
            if sys.platform == "darwin":
                from core.platform import macos_native
            else:
                import pyperclip
                pyperclip.copy(text)
            if target_hwnd:
                set_foreground_window(target_hwnd)
                time.sleep(0.15)  # let the focus switch settle
            if sys.platform == "darwin":
                if not macos_native.paste_text(text, PASTE_COMBO):
                    raise RuntimeError("Could not paste through macOS helper")
            else:
                send_keys(PASTE_COMBO)
        except Exception as exc:
            print(f"[main] Dictation paste error: {exc}")

    # ------------------------------------------------------------------
    # Intent picker (runs on Qt main thread via signal)
    # ------------------------------------------------------------------

    def _show_intent_picker(self, caller_idx: int):
        if self._intent_picker is not None:
            try:
                if self._intent_picker.isVisible():
                    return  # already showing — raise it and bail
            except RuntimeError:
                pass  # underlying C++ object was destroyed without emitting cancelled
            # Orphaned picker (closed without signal) — discard it and open fresh
            self._intent_picker = None

        self._intent_picker = IntentOverlay(caller_idx, target_hwnd=self._pending_intent_target)
        self._intent_picker.intent_chosen.connect(self._on_intent_chosen)
        self._intent_picker.cancelled.connect(self._on_intent_cancelled)
        self._intent_picker.show()

        # Grab keyboard focus from the Qt main thread, after the show event.
        try:
            if sys.platform == "darwin":
                # winId() is an NSView pointer (not a CGWindowNumber), so the
                # generic set_foreground_window lookup is a no-op here. Activate
                # our own process so the picker can become the key window, then
                # raise/activate the specific window.
                from core.platform_utils import activate_self
                activate_self()
                self._intent_picker.raise_()
                self._intent_picker.activateWindow()
                self._intent_picker.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            else:
                from core.platform_utils import set_foreground_window
                set_foreground_window(int(self._intent_picker.winId()))
        except Exception:
            pass

    def _on_intent_chosen(self, direction: str, prompt: str):
        self._intent_picker = None
        capture_data = self._pending_capture
        self._pending_capture = None
        caller_idx   = self._pending_caller_idx
        context_policy = self._pending_context_policy
        self._pending_context_policy = None
        target_hwnd  = self._pending_paste_target
        self._pending_paste_target = 0
        self._pending_intent_target = 0

        caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
        audio.stop()  # supersede any reply still being spoken from the previous query
        gen_id = self._generations.next()
        if caller.get("paste_back") and target_hwnd:
            threading.Thread(
                target=self._rewrite_and_paste,
                args=(prompt, capture_data, target_hwnd, gen_id),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._query_and_speak,
                args=(prompt, capture_data, context_policy or caller, gen_id),
                daemon=True,
            ).start()

    def _on_intent_cancelled(self):
        self._intent_picker = None
        self._pending_context_policy = None
        self._pending_paste_target = 0
        self._pending_intent_target = 0
        self._set_idle()

    def _rewrite_and_paste(self, intent_prompt: str, capture_data: tuple | None, target_hwnd: int, gen_id: int = 0):
        """Worker: stream LLM rewrite using intent_prompt, then paste into the original window."""
        import time

        selected_text = (capture_data[0] or "") if capture_data else ""

        if not selected_text:
            print("[main] Rewrite & Paste: no text was selected, aborting.")
            self._finish_idle(gen_id)
            return

        if self._generations.is_current(gen_id):
            self._signals.set_state.emit("thinking")
            self._signals.bubble_thinking.emit()

        # Create the conversation up front so an open chat window shows the new tab
        # as the rewrite starts; assistant_msg is filled in once the reply lands.
        assistant_msg: dict = {"role": "assistant", "content": ""}
        user_content = f"{intent_prompt}:\n\n{selected_text}"
        self._new_conversation([
            {"role": "user", "content": user_content},
            assistant_msg,
        ])
        self._signals.chat_new_conversation.emit()

        full_reply = ""
        try:
            for chunk in llm.stream_rewrite(selected_text, intent_prompt):
                full_reply += chunk
                if self._generations.is_current(gen_id):
                    self._signals.bubble_chunk.emit(chunk, False)
        except Exception as exc:
            print(f"[main] Rewrite error: {exc}")
            self._finish_idle(gen_id)
            return

        reply_text = full_reply.strip()
        if not reply_text:
            self._finish_idle(gen_id)
            return

        self._last_reply = reply_text
        if self._generations.is_current(gen_id):
            self._signals.bubble_start_reveal.emit()
            self._signals.bubble_finish.emit()

        # Paste result back into the original window (replaces the selection).
        try:
            from core.platform_utils import set_foreground_window, send_keys, PASTE_COMBO
            if sys.platform == "darwin":
                from core.platform import macos_native
            else:
                import pyperclip

                pyperclip.copy(reply_text)
            set_foreground_window(target_hwnd)
            time.sleep(0.15)   # let the focus switch settle
            if sys.platform == "darwin":
                if not macos_native.paste_text(reply_text, PASTE_COMBO):
                    raise RuntimeError("Could not paste through macOS helper")
            else:
                send_keys(PASTE_COMBO)
        except Exception as exc:
            print(f"[main] Paste-back error: {exc}")

        # Store in conversation history so the chat window can review it.
        self._new_conversation([
            {"role": "user",      "content": f"{intent_prompt}:\n\n{selected_text}"},
            {"role": "assistant", "content": reply_text},
        ])
        self._memory.record_turn(f"{intent_prompt}:\n\n{selected_text}", reply_text, "")
        self._persist_conversations()

        self._finish_idle(gen_id)

    # ------------------------------------------------------------------
    # LLM + TTS pipeline (worker thread)
    # ------------------------------------------------------------------

    def _query_and_speak(self, intent_prompt: str, capture_data: tuple | None, caller: dict | None = None, gen_id: int = 0):
        import queue
        caller = caller or {}

        # Kick off the active-document read up front so it overlaps the remaining
        # capture/clipboard I/O instead of serialising after it. A screenshot is
        # visual context only; it must not disable the user's Open docs setting.
        active_doc: dict[str, str] = {}
        active_doc_thread: threading.Thread | None = None
        if self._context_mode(caller, "documents") == "auto":
            def _read_active_doc():
                try:
                    txt = llm.read_active_document_for_context()
                    if txt and not txt.startswith(("Could not", "File type", "Failed to")):
                        active_doc["text"] = txt
                    log.info("active document context chars=%d", len(active_doc.get("text", "")))
                except Exception:
                    log.exception("active-document read failed")
            active_doc_thread = threading.Thread(target=_read_active_doc, daemon=True)
            active_doc_thread.start()

        # Use pre-captured input from hotkey time (original app still had focus then).
        if capture_data:
            selected, screenshot_b64 = capture_data
        else:
            selected = capture.get_selected_text()
            screenshot_b64 = None
            if not selected:
                img = capture.get_screen_snippet()
                screenshot_b64 = capture.image_to_base64(img)

        # Ambient context captured at hotkey time
        snap = self._pending_context
        self._pending_context = None
        ambient_text = (
            context_fetcher.format_context_for_prompt(snap)
            if snap and caller.get("context_ambient", True)
            else ""
        )
        if snap and self._context_mode(caller, "browser") == "auto":
            ambient_text = self._append_context_block(
                ambient_text,
                self._browser_context_text(snap),
            )

        with self._context_buffer_lock:
            buffered_items = self._context_buffer.copy()
            self._context_buffer.clear()

        # Consume dropped context items (files/images/text dragged onto the icon)
        with self._drop_context_lock:
            drop_items = self._drop_context_items.copy()
            self._drop_context_items.clear()
        if drop_items:
            self._signals.drop_context_cleared.emit()

        # Read the current clipboard directly (no synthesised Ctrl+C) when the
        # caller opts in — works in apps where get_selected_text() comes back empty.
        clipboard_text = capture.get_clipboard_text() if caller.get("context_clipboard") else None

        if active_doc_thread is not None:
            active_doc_thread.join()

        built = build_context(
            ContextInputs(
                intent_prompt=intent_prompt,
                selected=selected,
                screenshot_b64=screenshot_b64,
                ambient_text=ambient_text,
                buffered_items=buffered_items,
                drop_items=drop_items,
                clipboard_text=clipboard_text,
                active_document_text=active_doc.get("text", ""),
                priority_context=self._context_priority_source(
                    snap,
                    ambient_text,
                    active_doc.get("text", ""),
                ),
            ),
            read_document_file=llm.read_document_file,
        )
        user_message = built.user_message
        ambient_ctx = built.ambient_ctx
        screenshot_b64 = built.screenshot_b64

        # Give mods a chance to inspect or modify the prompt/context before the LLM call.
        user_message, ambient_ctx = self._plugin_manager.before_query(user_message, ambient_ctx)

        # Append the prompt to the active conversation (the one selected in the
        # chat window) so hotkey/voice prompts continue that thread; if none is
        # active (fresh start), open a new conversation. assistant_msg is the
        # empty, mutable turn llm_producer fills in once the reply lands. The
        # conversation's prior turns become history so the model has context.
        assistant_msg: dict = {"role": "assistant", "content": ""}
        user_msg: dict = {"role": "user", "content": user_message,
                          **({"image_base64": screenshot_b64} if screenshot_b64 else {})}
        conversation, query_history, is_new_conversation = self._continue_or_new_conversation(
            user_msg, assistant_msg, context=ambient_ctx,
        )
        # Scope memory (explicit "remember" + the model's memory_save tool) to
        # this conversation's project for the duration of the query.
        self._scope_memory(conversation.get("project_id"))
        if is_new_conversation:
            self._signals.chat_new_conversation.emit()
        else:
            self._signals.chat_sync_conversation.emit(self._active_conversation_idx)

        self._signals.set_state.emit("thinking")
        self._signals.bubble_thinking.emit()

        # Show read-only badges of the context attached to this prompt, next to the
        # icon — the same boxes the dragged-file flow uses.
        summary_badges = self._context_summary_badges(
            selected=selected,
            screenshot_b64=screenshot_b64,
            drop_items=drop_items,
            buffered_items=buffered_items,
            clipboard_text=clipboard_text,
            active_document_text=active_doc.get("text", ""),
            ambient_text=ambient_text,
        )
        if summary_badges:
            self._signals.show_context_summary.emit(summary_badges)

        # Memory: check for explicit "remember that" command, then retrieve
        # relevant LTM facts and the current STM session summary.
        remember_fact = extract_remember_fact(user_message)
        if remember_fact:
            self._memory.add_explicit_fact(remember_fact)

        query_for_retrieval = user_message + (" " + (selected or "")).strip()
        ltm_facts = ""
        stm_ctx = ""
        memory_mode = self._context_mode(caller, "memory")
        if memory_mode == "auto":
            ltm_facts = self._memory.retrieve_relevant(query_for_retrieval)
            stm_ctx = self._memory.get_stm_context()
        memory_ctx_parts: list[str] = []
        if ltm_facts:
            memory_ctx_parts.append(ltm_facts)
        if stm_ctx:
            memory_ctx_parts.append("[Session context]\n" + stm_ctx)
        memory_context = "\n\n".join(memory_ctx_parts)

        # Fresh query -" reset streamed text accumulator.
        full_text = ""
        reply_text = ""
        llm_chunk_q: queue.Queue[str | None] = queue.Queue()
        parser = ThoughtStreamParser()
        text_only_playback = (
            config.TTS_PROVIDER.lower() == "none"
            or not macos_safety.audio_enabled()
        )

        def finish_text_only_response():
            self._last_reply = reply_text
            if not self._generations.is_current(gen_id):
                return
            if not reply_text:
                self._signals.bubble_chunk.emit(
                    "⚠️ No reply from model. Check model name / API key in Settings.", False
                )
            self._signals.bubble_finish.emit()
            self._set_idle()

        def llm_producer():
            nonlocal full_text, reply_text
            allowed_tools = self._allowed_model_tools(caller)
            pinned_tools = self._pinned_model_tools(caller)
            use_tools = bool(allowed_tools)
            try:
                for chunk in llm.stream_response(
                    user_message,
                    screenshot_b64,
                    ambient_context=ambient_ctx,
                    memory_context=memory_context,
                    use_tools=use_tools,
                    allowed_tools=allowed_tools,
                    pinned_tools=pinned_tools,
                    allow_screenshot_tool=(
                        not screenshot_b64
                        and self._screenshot_tool_allowed(caller)
                    ),
                    history=query_history,
                ):
                    full_text += chunk
                    for part, is_thought in parser.feed(chunk):
                        if not part:
                            continue
                        self._signals.bubble_chunk.emit(part, is_thought)
                        if not is_thought:
                            reply_text += part
                            llm_chunk_q.put(part)
                    if not self._generations.is_current(gen_id):
                        break  # a newer query started — stop feeding stale chunks to the bubble
            except Exception as exc:
                log.exception("llm_producer crashed (gen_id=%d)", gen_id)
                if self._generations.is_current(gen_id):
                    err_msg = f"[Error: {exc}]"
                    self._signals.bubble_chunk.emit(err_msg, False)
                    reply_text += err_msg
                    llm_chunk_q.put(err_msg)
            finally:
                # Guarantee sentinel even if parser.finish() throws, so tts_consumer never hangs.
                try:
                    for part, is_thought in parser.finish():
                        if not part:
                            continue
                        self._signals.bubble_chunk.emit(part, is_thought)
                        if not is_thought:
                            reply_text += part
                            llm_chunk_q.put(part)
                except Exception:
                    log.exception("parser.finish() crashed (gen_id=%d)", gen_id)
                finally:
                    llm_chunk_q.put(None)
            # Fill in the assistant turn on the conversation created up front (the
            # chat window holds the same dict by reference, so this updates history
            # in place). Context was stored on the conversation so the chat window
            # can inject it into the system prompt for follow-ups.
            if reply_text:
                assistant_msg["content"] = reply_text
                if full_text != reply_text:
                    assistant_msg["display_content"] = full_text
                try:
                    self._memory.record_turn(user_message, reply_text, ambient_ctx)
                except Exception:
                    log.exception("memory.record_turn crashed (gen_id=%d)", gen_id)
                self._persist_conversations()
                try:
                    self._plugin_manager.after_response(reply_text)
                except Exception:
                    log.exception("plugin after_response dispatch crashed (gen_id=%d)", gen_id)
            if text_only_playback:
                finish_text_only_response()

        def llm_chunk_iter():
            while True:
                chunk = llm_chunk_q.get()
                if chunk is None:
                    return
                yield chunk

        def on_audio_start():
            # Called when first PCM chunk hits the speaker -" start word reveal + speaking anim.
            if not self._generations.is_current(gen_id):
                return
            self._signals.set_state.emit("speaking")
            self._signals.bubble_start_reveal.emit()

        def on_amplitude(amp: float):
            self._signals.set_mouth_amp.emit(amp)

        def on_word_timestamps(words, start_ms):
            # Real word timings from Cartesia -" drives precise bubble sync.
            if self._generations.is_current(gen_id):
                self._signals.bubble_schedule_words.emit(words, start_ms)

        def tts_consumer():
            def _on_done():
                self._last_reply = reply_text
                if self._generations.is_current(gen_id):
                    if not reply_text:
                        self._signals.bubble_chunk.emit(
                            "⚠️ No reply from model. Check model name / API key in Settings.", False
                        )
                    self._signals.bubble_finish.emit()
                    self._set_idle()

            try:
                audio.play_tts_stream_from_chunks(
                    llm_chunk_iter(),
                    on_done=_on_done,
                    on_audio_start=on_audio_start,
                    on_amplitude=on_amplitude,
                    on_word_timestamps=on_word_timestamps,
                )
            except Exception:
                log.exception("tts_consumer crashed (gen_id=%d)", gen_id)
                self._finish_idle(gen_id)

        threading.Thread(target=llm_producer, daemon=True).start()
        if not text_only_playback:
            threading.Thread(target=tts_consumer, daemon=True).start()

    # ------------------------------------------------------------------
    # Icon click â†' show popup
    # ------------------------------------------------------------------

    def _open_or_raise_chat(self, auto_message: str | None = None, force_new: bool = False) -> None:
        """Open the chat window, or raise it if already open."""
        if self._chat_window is not None:
            if force_new:
                self._chat_window.start_new_conversation(auto_message=auto_message)
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            return
        self._chat_window = ChatWindow(
            conversations=self._all_conversations,
            send_fn=self._make_memory_send_fn(),
            auto_message=auto_message,
            start_new=force_new,
            projects=conversation_store.load_projects(),
            active_project_id=self._active_project_id,
            on_project_change=self.set_active_project,
            on_new_project=self._create_project,
            persist_fn=self._persist_conversations,
            active_idx=self._active_conversation_idx,
            on_select=self.set_active_conversation,
        )
        self._chat_window.destroyed.connect(lambda: setattr(self, "_chat_window", None))
        self._chat_window.show()

    def _on_icon_click(self, event):
        auto_msg = config.CHAT_ELABORATE_PROMPT if config.CHAT_AUTO_ELABORATE and self._all_conversations else None
        self._open_or_raise_chat(auto_message=auto_msg)

    def _on_show_last_chat(self):
        """Tray menu 'Last chat' (and a click on the speech bubble) -" open or raise the chat window."""
        self._open_or_raise_chat()

    def _on_bubble_highlight(self, reply_text: str, revealed_count: int, finished: bool):
        """Mirror the bubble's TTS read-position onto the open chat window, if any."""
        if self._chat_window is not None:
            self._chat_window.update_live_highlight(reply_text, revealed_count, finished)

    def _on_chat_new_conversation(self):
        """A voice query just created a conversation — surface it as a new tab in
        the open chat window (and switch to it) so its live highlight is visible."""
        if self._chat_window is not None:
            self._chat_window.ingest_new_conversations()

    @staticmethod
    def _context_summary_badges(
        *,
        selected: str | None,
        screenshot_b64: str | None,
        drop_items: list | None,
        buffered_items: list | None,
        clipboard_text: str | None,
        active_document_text: str,
        ambient_text: str,
    ) -> list[tuple[str, str]]:
        """Build (label, type) pairs describing the context attached to a prompt,
        for the read-only badges shown next to the icon. type is one of
        'image' | 'text' | 'file' (matching the drag-drop badge colours)."""
        def short(text: str, n: int = 14) -> str:
            flat = " ".join((text or "").split())
            return (flat[: n - 1] + "…") if len(flat) > n else flat

        type_map = {"image": "image", "text": "text", "document_path": "file", "file": "file"}
        items: list[tuple[str, str]] = []
        if screenshot_b64:
            items.append(("Screenshot", "image"))
        if selected:
            items.append((f"Selection · {short(selected)}", "text"))
        for name, _content, item_type in (drop_items or []):
            items.append((short(name, 24), type_map.get(item_type, "file")))
        for buf in (buffered_items or []):
            items.append((short(buf, 24), "text"))
        if clipboard_text:
            items.append((f"Clipboard · {short(clipboard_text)}", "text"))
        if active_document_text:
            items.append(("Active document", "file"))
        if "[Browser/Web]" in (ambient_text or ""):
            items.append(("Browser/Web", "file"))
        ambient_without_browser = (ambient_text or "").replace("[Browser/Web]", "").strip()
        if ambient_without_browser:
            items.append(("Window context", "file"))
        return items[:8]

    def _on_show_new_chat(self):
        """Tray menu 'New chat' -" open the chat window on a fresh thread."""
        self._open_or_raise_chat(force_new=True)

    def _make_memory_send_fn(self):
        """Return a send_fn wrapper that injects relevant LTM facts per chat turn."""
        memory = self._memory

        def send_with_memory(messages: list):
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"),
                "",
            )
            if isinstance(last_user, str) and last_user:
                fact = extract_remember_fact(last_user)
                if fact:
                    try:
                        memory.add_explicit_fact(fact)
                    except Exception:
                        log.exception("explicit remember (chat) failed")
            mem_ctx = memory.retrieve_relevant(last_user) if last_user else ""
            return llm.stream_response_with_history(messages, memory_context=mem_ctx)

        return send_with_memory

    # ------------------------------------------------------------------
    # Projects & conversation persistence
    # ------------------------------------------------------------------

    def _continue_or_new_conversation(
        self, user_msg: dict, assistant_msg: dict, context: str = "",
    ) -> tuple[dict, list[dict], bool]:
        """Resolve the conversation a hotkey/voice prompt should land in.

        Returns ``(conversation, history, is_new)`` where *history* is the
        conversation's prior turns (empty for a new conversation) to replay to
        the model, and *is_new* tells the caller whether to add a tab or refresh
        an existing one in the chat window.
        """
        idx = self._active_conversation_idx
        if idx is not None and 0 <= idx < len(self._all_conversations):
            conv = self._all_conversations[idx]
            history = list(conv.get("messages", []))  # snapshot prior turns
            conv.setdefault("messages", [])
            conv["messages"].extend([user_msg, assistant_msg])
            self._persist_conversations()
            return conv, history, False
        conv = self._new_conversation([user_msg, assistant_msg], context=context)
        self._active_conversation_idx = len(self._all_conversations) - 1
        return conv, [], True

    def set_active_conversation(self, idx: int | None) -> None:
        """Called by the chat window when the user selects/starts a conversation.

        Memory scope follows the selected conversation's project so facts saved
        during a hotkey/voice follow-up land in the right pool.
        """
        if idx is None:
            self._active_conversation_idx = None
            self._scope_memory(self._active_project_id)
        elif 0 <= idx < len(self._all_conversations):
            self._active_conversation_idx = idx
            self._scope_memory(self._all_conversations[idx].get("project_id"))

    def _on_chat_sync_conversation(self, idx: int) -> None:
        """A hotkey/voice prompt appended to an existing conversation -> refresh it."""
        if self._chat_window is not None:
            self._chat_window.sync_conversation(idx)

    def _new_conversation(self, messages: list[dict], context: str = "") -> dict:
        """Append a new conversation stamped with the active project.

        Does not persist immediately — callers persist via _persist_conversations
        once the assistant reply lands, so empty placeholders never hit disk.
        """
        conv = {
            "id": str(uuid.uuid4()),
            "project_id": self._active_project_id,
            "messages": messages,
            "context": context,
        }
        self._all_conversations.append(conv)
        return conv

    def _persist_conversations(self) -> None:
        try:
            conversation_store.save_conversations(self._all_conversations)
        except Exception:
            log.exception("failed to persist conversations")

    def _scope_memory(self, project_id: str | None) -> None:
        """Point long-term memory at a project (General/None = global pool)."""
        pid = project_id or conversation_store.GENERAL_PROJECT_ID
        memory_module.set_active_project(
            None if pid == conversation_store.GENERAL_PROJECT_ID else pid
        )

    def _apply_memory_project(self) -> None:
        """Scope memory to the dropdown project (used at startup / no active chat)."""
        self._scope_memory(self._active_project_id)

    def set_active_project(self, project_id: str | None) -> None:
        """Set the project NEW conversations are filed under (the dropdown).

        Memory scope follows the *active conversation* when one exists, so the
        dropdown only re-scopes memory while nothing is being continued.
        """
        self._active_project_id = project_id or conversation_store.GENERAL_PROJECT_ID
        if self._active_conversation_idx is None:
            self._scope_memory(self._active_project_id)

    def _create_project(self, name: str) -> dict | None:
        """Create + persist a project; returns the project dict (or None on error)."""
        try:
            return conversation_store.add_project(name)
        except Exception:
            log.exception("failed to create project %r", name)
            return None

    # ------------------------------------------------------------------
    # Memory viewer
    # ------------------------------------------------------------------

    def _open_memory_viewer(self) -> None:
        from ui.memory_viewer import MemoryViewer
        if self._memory_viewer is not None:
            self._memory_viewer.raise_()
            self._memory_viewer.activateWindow()
            return
        self._memory_viewer = MemoryViewer(self._memory, parent=None)
        self._memory_viewer.destroyed.connect(lambda: setattr(self, "_memory_viewer", None))
        self._memory_viewer.show()
        self._memory_viewer.raise_()
        self._memory_viewer.activateWindow()


def _install_signal_handlers(app: App) -> None:
    if _IS_WIN:
        _install_windows_console_handler(app)
        return

    def _request(signum, _frame) -> None:
        name = signal.Signals(signum).name
        app._shutdown_requested.emit(name)

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _request)


def _install_windows_console_handler(app: App) -> None:
    global _win_console_handler
    try:
        import ctypes
        from core import platform_utils

        ctrl_c_event = 0
        ctrl_break_event = 1
        handled_events = {ctrl_c_event, ctrl_break_event}
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        @handler_type
        def _handler(ctrl_type: int) -> bool:
            if ctrl_type not in handled_events:
                return False
            if ctrl_type == ctrl_c_event and platform_utils.is_recent_synthetic_ctrl_c():
                return True
            app._shutdown_requested.emit("console ctrl+c" if ctrl_type == ctrl_c_event else "console ctrl+break")
            return True

        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler, True):
            raise ctypes.WinError()
        _win_console_handler = _handler
    except Exception:
        log.exception("Failed to install Windows console control handler")


def main():

    # Enforce a single running Wisp. The lock is shared across dev runs and
    # installed builds, so a second launch exits before doing any work.
    from core.system.single_instance import acquire
    if not acquire():
        log.warning("Another instance of Wisp is already running; exiting.")
        print("[main] Wisp is already running. Exiting.")
        sys.exit(0)

    app = App()
    _install_signal_handlers(app)
    try:
        code = app.run()
    except KeyboardInterrupt:
        app.shutdown("keyboard interrupt")
        code = 130
    finally:
        app.shutdown("main exit")
    sys.exit(code)


if __name__ == "__main__":
    main()

