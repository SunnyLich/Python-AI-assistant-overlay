"""
main.py — Entry point for the AI Assistant Overlay.

Flow:
  Ctrl+E → IntentOverlay appears (WASD picker)
  WASD key → capture input → LLM stream → TTS stream → audio out
  Click doll → show full reply popup
"""
import sys
import os
import threading
from PyQt6.QtWidgets import QApplication
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")
import config
from core.hotkeys import HotkeyListener
from core import capture, llm, audio, context_fetcher, stt
from core import tts as tts_module
from core import memory as memory_module
from ui.overlay import DollOverlay, OverlaySignals
from ui.intent_overlay import IntentOverlay
from ui.snip_overlay import SnipOverlay
from ui.chat_window import ChatWindow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re as _re

# Matches only unambiguous imperative memory commands at the START of a message.
# The (?=[a-zA-Z]) after ^ explicitly rejects messages that begin with a
# quotation mark, symbol, or any non-letter character before the trigger word.
# Also deliberately excludes: "I remember...", "do you remember...?",
# "can you remember...", "help me remember..." — those are conversation, not storage.
_REMEMBER_RE = _re.compile(
    r"""^(?=[a-zA-Z])(?:
        (?:please\s+)?remember\s+(?:that\s+|this[:\-\s]+|these[:\-\s]+)
        |(?:please\s+)?remember\s*[:\-]\s*
        |(?:please\s+)?note\s+that\s+
        |(?:please\s+)?note\s*[:\-]\s*
        |save\s+(?:this|that)\s*[:\-\s]+
        |keep\s+in\s+mind\s+(?:that\s+)?
        |keep\s+in\s+mind\s*[:\-]\s*
        |don'?t\s+forget\s+(?:that\s+)?
        |make\s+(?:a\s+)?note\s+(?:that\s+|of\s+)?
        |make\s+(?:a\s+)?note\s*[:\-]\s*
        |store\s+(?:this|that)\s*[:\-\s]+
    )""",
    _re.IGNORECASE | _re.VERBOSE,
)


def _extract_remember_fact(text: str) -> str | None:
    """
    Return the fact text if the message is an imperative memory command,
    otherwise None.

    Triggers: "remember that X", "remember: X", "note that X", "note: X",
              "keep in mind X", "don't forget X", "make a note of X",
              "save this: X", "store that: X"  (and "please ..." variants).

    Does NOT trigger: "I remember X", "do you remember X?",
                      "can you remember X", "help me remember X".
    """
    t = text.strip()
    m = _REMEMBER_RE.match(t)
    if m:
        fact = t[m.end():].strip()
        if fact:
            return fact
    return None


class App:
    def __init__(self):
        self._qt = QApplication(sys.argv)
        self._qt.setQuitOnLastWindowClosed(False)  # chat/settings closing must not exit the app
        self._signals = OverlaySignals()
        self._overlay = DollOverlay(self._signals)
        self._hotkeys = HotkeyListener(
            on_callers=[lambda i=i: self._on_caller_hotkey(i) for i in range(len(config.CALLER_ROWS))],
            on_add_context=self._on_add_context,
            on_clear_context=self._on_clear_context,
            on_snip=self._on_snip_hotkey,
            on_voice_start=self._on_voice_start,
            on_voice_stop=self._on_voice_stop,
        )
        self._context_buffer: list[str] = []   # accumulated via Alt+Q
        self._last_reply: str = ""
        self._pending_context: context_fetcher.ContextSnapshot | None = None
        self._voice_active: bool = False        # guard against spurious release events
        self._intent_picker: IntentOverlay | None = None
        self._snip_overlay: SnipOverlay | None = None
        self._chat_window: ChatWindow | None = None
        self._all_conversations: list[dict] = []  # each item = {"messages": [...], "context": str}
        self._overlay_hwnd: int = 0          # cached after first show
        self._pending_capture: tuple | None = None  # (selected_text, screenshot_b64)
        self._pending_caller_idx: int = 0    # which CALLER_ROWS entry triggered the current picker
        self._pending_paste_target: int = 0  # HWND to paste into (0 = no paste)
        self._gen_id: int = 0                # incremented on each new query; stale workers skip bubble signals

        # Memory
        self._memory = memory_module.get_manager()

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._signals.show_snip_overlay.connect(self._show_snip_overlay)
        self._signals.settings_applied.connect(self._on_settings_applied)
        self._signals.show_last_chat.connect(self._on_show_last_chat)
        self._signals.show_memory_viewer.connect(self._open_memory_viewer)
        self._overlay.set_click_handler(self._on_doll_click)

        # Pre-warm connections in background
        threading.Thread(target=self._prewarm, daemon=True).start()

        # Start fs watcher (watches Desktop/Documents/Downloads in background)
        context_fetcher.start_fs_watcher()

        # Pre-load the Whisper model so the first voice query has no cold start
        stt.prewarm()

    def run(self):
        if not config.DOLL_AUTO_HIDE:
            self._overlay.show()
        self._overlay_hwnd = int(self._overlay.winId())  # safe: Qt main thread
        self._hotkeys.start()
        print(f"[main] AI Assistant Overlay running. Callers: {[c['hotkey'] for c in config.CALLER_ROWS]}")
        sys.exit(self._qt.exec())

    # ------------------------------------------------------------------
    # Pre-warm
    # ------------------------------------------------------------------

    def _prewarm(self):
        tts_module.prewarm()
        print("[main] Connections pre-warmed.")

    # ------------------------------------------------------------------
    # Settings applied — reload config + re-register hotkeys live
    # ------------------------------------------------------------------

    def _on_settings_applied(self):
        config.reload()
        self._hotkeys.stop()
        self._hotkeys = HotkeyListener(
            on_callers=[lambda i=i: self._on_caller_hotkey(i) for i in range(len(config.CALLER_ROWS))],
            on_add_context=self._on_add_context,
            on_clear_context=self._on_clear_context,
            on_snip=self._on_snip_hotkey,
            on_voice_start=self._on_voice_start,
            on_voice_stop=self._on_voice_stop,
        )
        self._hotkeys.start()
        print("[main] Config reloaded and hotkeys re-registered.")

    # ------------------------------------------------------------------
    # Hotkey → show intent picker (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_add_context(self):
        """Alt+Q — capture selected text and append to context buffer."""
        text = capture.get_selected_text()
        if text:
            self._context_buffer.append(text)
            print(f"[main] Context buffer: {len(self._context_buffer)} item(s) queued.")

    def _on_clear_context(self):
        """Alt+W — clear the context buffer."""
        self._context_buffer.clear()
        print("[main] Context buffer cleared.")

    def _steal_foreground(self) -> None:
        """AttachThreadInput + SetForegroundWindow — must be called from the keyboard-hook thread."""
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

    def _on_snip_hotkey(self):
        """Ctrl+Alt+Q — show the region selector, then the intent picker."""
        self._steal_foreground()
        self._signals.show_snip_overlay.emit()

    def _on_caller_hotkey(self, caller_idx: int):
        """Called when any caller hotkey fires. Dispatches based on caller's paste_back flag."""
        import ctypes
        caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
        paste_back = caller.get("paste_back", False)

        # For paste-back callers, save the target HWND BEFORE stealing focus.
        target_hwnd = ctypes.windll.user32.GetForegroundWindow() if paste_back else 0

        self._pending_context = context_fetcher.fetch_and_save()
        selected = capture.get_selected_text()
        # Regular hotkey queries are text-only; visual context comes from the
        # snip overlay (Ctrl+Alt+Q) only — never auto-capture a screenshot here.
        self._pending_capture = (selected, None)
        self._pending_caller_idx = caller_idx
        self._pending_paste_target = target_hwnd

        self._steal_foreground()
        audio.play_filler()
        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()
        self._signals.set_state.emit("listening")
        self._signals.show_intent_picker.emit(caller_idx)

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
        self._pending_paste_target = 0

        audio.play_filler()
        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()
        self._signals.set_state.emit("listening")
        self._signals.show_intent_picker.emit(0)

    def _on_snip_cancelled(self):
        self._snip_overlay = None

    # ------------------------------------------------------------------
    # Voice push-to-talk (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_voice_start(self):
        """F9 key-down — start recording."""
        if self._voice_active:
            return  # ignore held-down repeat events
        self._voice_active = True
        self._pending_context = context_fetcher.fetch_and_save()
        stt.start_recording()
        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()
        self._signals.set_state.emit("listening")
        self._signals.bubble_listening.emit()

    def _on_voice_stop(self):
        """F9 key-up — stop recording, transcribe, and query."""
        if not self._voice_active:
            return
        self._voice_active = False
        # Transcription is blocking (~200–600 ms) — run off the hotkey thread.
        threading.Thread(target=self._voice_transcribe_and_query, daemon=True).start()

    def _voice_transcribe_and_query(self):
        text = stt.stop_and_transcribe()
        if not text:
            self._signals.set_state.emit("idle")
            if config.DOLL_AUTO_HIDE:
                self._signals.hide_doll.emit()
            return
        self._signals.set_state.emit("thinking")
        self._gen_id += 1
        self._query_and_speak(text, (None, None), self._gen_id)

    # ------------------------------------------------------------------
    # Intent picker (runs on Qt main thread via signal)
    # ------------------------------------------------------------------

    def _show_intent_picker(self, caller_idx: int):
        if self._intent_picker is not None:
            return  # already showing

        self._intent_picker = IntentOverlay(caller_idx)
        self._intent_picker.intent_chosen.connect(self._on_intent_chosen)
        self._intent_picker.cancelled.connect(self._on_intent_cancelled)
        self._intent_picker.show()

        # Second focus grab from the Qt main thread — by now the picker has a
        # real HWND and Qt has had a chance to process the show event.
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(self._intent_picker.winId()))
        except Exception:
            pass

    def _on_intent_chosen(self, direction: str, prompt: str):
        self._intent_picker = None
        capture_data = self._pending_capture
        self._pending_capture = None
        caller_idx   = self._pending_caller_idx
        target_hwnd  = self._pending_paste_target
        self._pending_paste_target = 0

        caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
        self._gen_id += 1
        gen_id = self._gen_id
        if caller.get("paste_back") and target_hwnd:
            threading.Thread(
                target=self._rewrite_and_paste,
                args=(prompt, capture_data, target_hwnd, gen_id),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._query_and_speak,
                args=(prompt, capture_data, gen_id),
                daemon=True,
            ).start()

    def _on_intent_cancelled(self):
        self._intent_picker = None
        self._pending_paste_target = 0
        self._signals.set_state.emit("idle")
        if config.DOLL_AUTO_HIDE:
            self._signals.hide_doll.emit()

    def _rewrite_and_paste(self, intent_prompt: str, capture_data: tuple | None, target_hwnd: int, gen_id: int = 0):
        """Worker: stream LLM rewrite using intent_prompt, then paste into the original window."""
        import ctypes
        import time
        import pyperclip

        selected_text = (capture_data[0] or "") if capture_data else ""

        if not selected_text:
            print("[main] Rewrite & Paste: no text was selected, aborting.")
            if gen_id == self._gen_id:
                self._signals.set_state.emit("idle")
                if config.DOLL_AUTO_HIDE:
                    self._signals.hide_doll.emit()
            return

        full_reply = ""
        try:
            for chunk in llm.stream_rewrite(selected_text, intent_prompt):
                full_reply += chunk
                if gen_id == self._gen_id:
                    self._signals.bubble_chunk.emit(chunk)
        except Exception as exc:
            print(f"[main] Rewrite error: {exc}")
            if gen_id == self._gen_id:
                self._signals.set_state.emit("idle")
                if config.DOLL_AUTO_HIDE:
                    self._signals.hide_doll.emit()
            return

        reply_text = full_reply.strip()
        if not reply_text:
            if gen_id == self._gen_id:
                self._signals.set_state.emit("idle")
                if config.DOLL_AUTO_HIDE:
                    self._signals.hide_doll.emit()
            return

        self._last_reply = reply_text
        if gen_id == self._gen_id:
            self._signals.bubble_start_reveal.emit()
            self._signals.bubble_finish.emit()

        # Paste result back into the original window (replaces the selection).
        try:
            pyperclip.copy(reply_text)
            ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.15)   # let the focus switch settle
            import keyboard
            keyboard.send("ctrl+v")
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

        if gen_id == self._gen_id:
            self._signals.set_state.emit("idle")
            if config.DOLL_AUTO_HIDE:
                self._signals.hide_doll.emit()

    # ------------------------------------------------------------------
    # LLM + TTS pipeline (worker thread)
    # ------------------------------------------------------------------

    def _query_and_speak(self, intent_prompt: str, capture_data: tuple | None, gen_id: int = 0):
        import queue

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
        ambient_ctx = context_fetcher.format_context_for_prompt(snap) if snap else ""

        # Build final message — intent prompt only; context goes into the system
        # prompt so it is sent exactly once and not repeated in every follow-up turn.
        context_items = self._context_buffer.copy()
        self._context_buffer.clear()

        all_contexts = context_items + ([selected] if selected else [])
        user_message = intent_prompt  # clean turn — no embedded context
        if all_contexts:
            # Merge selected / buffered text into ambient_ctx (system-prompt layer).
            ctx_block = (
                "\n\n".join(f"Context {i + 1}:\n{c}" for i, c in enumerate(all_contexts))
                if len(all_contexts) > 1
                else all_contexts[0]
            )
            ambient_ctx = f"{ambient_ctx}\n\n---\n{ctx_block}" if ambient_ctx else ctx_block

        self._signals.set_state.emit("thinking")
        self._signals.bubble_thinking.emit()

        # Memory: check for explicit "remember that" command, then retrieve
        # relevant LTM facts and the current STM session summary.
        remember_fact = _extract_remember_fact(user_message)
        if remember_fact:
            self._memory.add_explicit_fact(remember_fact)

        query_for_retrieval = user_message + (" " + (selected or "")).strip()
        ltm_facts  = self._memory.retrieve_relevant(query_for_retrieval)
        stm_ctx    = self._memory.get_stm_context()
        memory_ctx_parts: list[str] = []
        if ltm_facts:
            memory_ctx_parts.append(ltm_facts)
        if stm_ctx:
            memory_ctx_parts.append("[Session context]\n" + stm_ctx)
        memory_context = "\n\n".join(memory_ctx_parts)

        # Fresh query — reset streamed text accumulator.
        full_text = ""
        llm_chunk_q: queue.Queue[str | None] = queue.Queue()

        def llm_producer():
            nonlocal full_text
            try:
                for chunk in llm.stream_response(
                    user_message,
                    screenshot_b64,
                    ambient_context=ambient_ctx,
                    memory_context=memory_context,
                    use_tools=not screenshot_b64,   # tools only for text queries; vision handles itself
                ):
                    full_text += chunk
                    llm_chunk_q.put(chunk)
                    self._signals.bubble_chunk.emit(chunk)   # buffered by bubble in reveal mode
                    if gen_id != self._gen_id:
                        break  # a newer query started — stop feeding stale chunks to the bubble
            finally:
                llm_chunk_q.put(None)
            # Store context separately so the chat window can inject it into
            # the system prompt for follow-ups without re-embedding it in turns.
            self._all_conversations.append({
                "messages": [
                    {"role": "user", "content": user_message,
                     **({"image_base64": screenshot_b64} if screenshot_b64 else {})},
                    {"role": "assistant", "content": full_text},
                ],
                "context": ambient_ctx,
            })
            # Record the completed turn in short-term memory.
            self._memory.record_turn(user_message, full_text, ambient_ctx)

        def llm_chunk_iter():
            while True:
                chunk = llm_chunk_q.get()
                if chunk is None:
                    return
                yield chunk

        def on_audio_start():
            # Called when first PCM chunk hits the speaker — start word reveal + speaking anim.
            if gen_id != self._gen_id:
                return
            self._signals.set_state.emit("speaking")
            self._signals.bubble_start_reveal.emit()

        def on_amplitude(amp: float):
            self._signals.set_mouth_amp.emit(amp)

        def on_word_timestamps(words, start_ms):
            # Real word timings from Cartesia — drives precise bubble sync.
            self._signals.bubble_schedule_words.emit(words, start_ms)

        def tts_consumer():
            if config.TTS_PROVIDER.lower() == "none":
                on_audio_start()
            audio.play_tts_stream_from_chunks(
                llm_chunk_iter(),
                on_done=lambda: (
                    setattr(self, "_last_reply", full_text),
                    self._signals.set_state.emit("idle") if gen_id == self._gen_id else None,
                    self._signals.bubble_finish.emit() if gen_id == self._gen_id else None,
                    self._signals.hide_doll.emit() if (config.DOLL_AUTO_HIDE and gen_id == self._gen_id) else None,
                ),
                on_audio_start=on_audio_start,
                on_amplitude=on_amplitude,
                on_word_timestamps=on_word_timestamps,
            )

        threading.Thread(target=llm_producer, daemon=True).start()
        threading.Thread(target=tts_consumer, daemon=True).start()

    # ------------------------------------------------------------------
    # Doll click → show popup
    # ------------------------------------------------------------------

    def _open_or_raise_chat(self, auto_message: str | None = None) -> None:
        """Open the chat window, or raise it if already open."""
        if self._chat_window is not None:
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            return
        self._chat_window = ChatWindow(
            conversations=self._all_conversations,
            send_fn=self._make_memory_send_fn(),
            auto_message=auto_message,
        )
        self._chat_window.destroyed.connect(lambda: setattr(self, "_chat_window", None))
        self._chat_window.show()

    def _on_doll_click(self, event):
        auto_msg = config.CHAT_ELABORATE_PROMPT if config.CHAT_AUTO_ELABORATE and self._all_conversations else None
        self._open_or_raise_chat(auto_message=auto_msg)

    def _on_show_last_chat(self):
        """Tray menu 'Last chat' — open or raise the chat window."""
        self._open_or_raise_chat()

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
        viewer = MemoryViewer(self._memory)
        viewer.exec()


def main():
    # Our clipboard helper (capture.get_selected_text) injects a synthetic Ctrl+C
    # via keyboard.send("ctrl+c") while the terminal is still the foreground window.
    # On Windows, this delivers CTRL_C_EVENT to every process in the console group —
    # including us — which becomes KeyboardInterrupt in the Qt event loop.
    # Ignore CTRL_C_EVENT at the Win32 level so the app can never be killed this way.
    # (The tray "Quit" action is the intended exit path.)
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass

    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
