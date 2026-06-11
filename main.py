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
import logging
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QObject, Signal, Qt

_IS_WIN = sys.platform == "win32"
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
from core.query_pipeline import GenerationCounter, ContextInputs, build_context
from core.system.app_platform import configure_windows_app_identity
from core.system import macos_safety
from core.system import sdk_clients
from core.system.paths import ASSETS_DIR
from core.memory_store.commands import extract_remember_fact
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

        from core.system.paths import PLUGINS_DIR
        self._plugin_manager = _plugin_manager_mod.init(PLUGINS_DIR)

        configure_windows_app_identity()
        self._qt = QApplication(sys.argv)
        self._qt.setApplicationName("Wisp")
        self._qt.setApplicationDisplayName("Wisp")
        self._qt.setQuitOnLastWindowClosed(False)  # chat/settings closing must not exit the app
        _app_icon = _build_app_icon()
        if not _app_icon.isNull():
            self._qt.setWindowIcon(_app_icon)
        apply_app_theme(self._qt)
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
        self._intent_picker: IntentOverlay | None = None
        self._snip_overlay: SnipOverlay | None = None
        self._chat_window: ChatWindow | None = None
        self._memory_viewer = None
        self._all_conversations: list[dict] = []  # each item = {"messages": [...], "context": str}
        self._overlay_hwnd: int = 0          # cached after first show
        self._hotkey_warning_shown = False
        self._pending_capture: tuple | None = None  # (selected_text, screenshot_b64)
        self._pending_caller_idx: int = 0    # which CALLER_ROWS entry triggered the current picker
        self._pending_context_policy: dict | None = None
        self._pending_paste_target: int = 0  # HWND to paste into (0 = no paste)
        self._pending_intent_target: int = 0 # HWND whose monitor should host the picker
        self._generations = GenerationCounter()  # bumped per query; stale workers skip bubble signals

        # Memory
        self._memory = memory_module.get_manager()

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._signals.show_snip_overlay.connect(self._show_snip_overlay)
        self._signals.settings_applied.connect(self._on_settings_applied)
        self._signals.show_new_chat.connect(self._on_show_new_chat)
        self._signals.show_last_chat.connect(self._on_show_last_chat)
        self._signals.show_memory_viewer.connect(self._open_memory_viewer)
        self._signals.bubble_highlight.connect(self._on_bubble_highlight)
        self._signals.chat_new_conversation.connect(self._on_chat_new_conversation)
        self._signals.context_items_dropped.connect(self._on_context_items_dropped)
        self._signals.remove_dropped_item.connect(self._on_remove_dropped_item)
        self._signals.summon_caller.connect(self._on_summon_caller)
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

        # Pre-load the Whisper model so the first voice query has no cold start
        if macos_safety.stt_prewarm_enabled():
            stt.prewarm()

        # Notify mods that the app is fully initialised
        from core.plugin_manager import AppContext
        from core.llm_clients.client import get_tool_registry
        self._plugin_manager.on_startup(AppContext(
            signals=self._signals,
            model_tool_registry=get_tool_registry(),
            config=config,
        ))
        self._qt.aboutToQuit.connect(self._plugin_manager.on_shutdown)

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
        )

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
        )

    def _set_idle(self) -> None:
        """Return the overlay to its resting state."""
        self._signals.set_state.emit("idle")
        if config.ICON_AUTO_HIDE:
            self._signals.hide_icon.emit()

    @staticmethod
    def _context_mode(caller: dict, name: str) -> str:
        mode = str(caller.get(f"context_{name}_mode") or "").strip().lower()
        if mode in {"off", "auto", "model"}:
            return mode
        if name == "memory":
            return "auto"
        if name == "documents":
            if caller.get("context_documents", False):
                return "auto"
            if caller.get("context_tools", False):
                return "model"
        if name in {"browser", "github"} and caller.get("context_tools", False):
            return "model"
        return "off"

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
        sys.exit(self._qt.exec())

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
        fg_hwnd = get_foreground_window()
        target_hwnd = fg_hwnd if paste_back else 0

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
                self._pending_context = context_fetcher.fetch_and_save()
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
        self._all_conversations.append({
            "messages": [
                {"role": "user", "content": user_content},
                assistant_msg,
            ],
            "context": "",
        })
        self._signals.chat_new_conversation.emit()

        full_reply = ""
        try:
            for chunk in llm.stream_rewrite(selected_text, intent_prompt):
                full_reply += chunk
                if self._generations.is_current(gen_id):
                    self._signals.bubble_chunk.emit(chunk)
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
        self._all_conversations.append({
            "messages": [
                {"role": "user",      "content": f"{intent_prompt}:\n\n{selected_text}"},
                {"role": "assistant", "content": reply_text},
            ],
            "context": "",
        })
        self._memory.record_turn(f"{intent_prompt}:\n\n{selected_text}", reply_text, "")

        self._finish_idle(gen_id)

    # ------------------------------------------------------------------
    # LLM + TTS pipeline (worker thread)
    # ------------------------------------------------------------------

    def _query_and_speak(self, intent_prompt: str, capture_data: tuple | None, caller: dict | None = None, gen_id: int = 0):
        import queue
        caller = caller or {}

        # Kick off the active-document read up front so it overlaps the remaining
        # capture/clipboard I/O instead of serialising after it. Speculative: it
        # only feeds the prompt when no screenshot is present (build_context gates
        # it), so a vision query that appears later simply discards the result.
        pre_screenshot = capture_data[1] if capture_data else None
        active_doc: dict[str, str] = {}
        active_doc_thread: threading.Thread | None = None
        if caller.get("context_documents", True) and not pre_screenshot:
            def _read_active_doc():
                try:
                    txt = llm.read_active_document_for_context()
                    if txt and not txt.startswith(("Could not", "File type", "Failed to")):
                        active_doc["text"] = txt
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
            ),
            read_document_file=llm.read_document_file,
        )
        user_message = built.user_message
        ambient_ctx = built.ambient_ctx
        screenshot_b64 = built.screenshot_b64

        # Give mods a chance to inspect or modify the prompt/context before the LLM call.
        user_message, ambient_ctx = self._plugin_manager.before_query(user_message, ambient_ctx)

        # Create the conversation up front (with an empty, mutable assistant turn)
        # so an open chat window can show the new tab and live-highlight the reply
        # as it streams. llm_producer fills assistant_msg in once the reply lands.
        assistant_msg: dict = {"role": "assistant", "content": ""}
        conversation = {
            "messages": [
                {"role": "user", "content": user_message,
                 **({"image_base64": screenshot_b64} if screenshot_b64 else {})},
                assistant_msg,
            ],
            "context": ambient_ctx,
        }
        self._all_conversations.append(conversation)
        self._signals.chat_new_conversation.emit()

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
            use_tools = not screenshot_b64 and caller.get("context_tools", True)
            allowed_tools = None
            if memory_mode == "model":
                use_tools = True
                allowed_tools = [
                    "web_search",
                    "get_context",
                    "get_context.browser",
                    "get_context.documents",
                    "git_status",
                    "git_diff",
                    "github_repo",
                    "github_issue",
                    "memory_search",
                ]
            try:
                for chunk in llm.stream_response(
                    user_message,
                    screenshot_b64,
                    ambient_context=ambient_ctx,
                    memory_context=memory_context,
                    use_tools=use_tools,
                    allowed_tools=allowed_tools,
                    allow_screenshot_tool=(
                        not screenshot_b64
                        and caller.get("context_screenshot") == "model"
                    ),
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
        if ambient_text:
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
            mem_ctx = memory.retrieve_relevant(last_user) if last_user else ""
            return llm.stream_response_with_history(messages, memory_context=mem_ctx)

        return send_with_memory

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


def main():
    # On Windows, synthesising Ctrl+C via keyboard.send() also delivers
    # CTRL_C_EVENT to our own process, becoming a KeyboardInterrupt in Qt.
    # Block it at the Win32 level; the tray "Quit" action is the exit path.
    if _IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
        except Exception:
            pass

    # Enforce a single running Wisp. The lock is shared across dev runs and
    # installed builds, so a second launch exits before doing any work.
    from core.system.single_instance import acquire
    if not acquire():
        log.warning("Another instance of Wisp is already running; exiting.")
        print("[main] Wisp is already running. Exiting.")
        sys.exit(0)

    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()

