"""
main.py â€” Entry point for Wisp.

Flow:
  Ctrl+E â†’ IntentOverlay appears (WASD picker)
  WASD key â†’ capture input â†’ LLM stream â†’ TTS stream â†’ audio out
  Click doll â†’ show full reply popup
"""
import sys
import os
import threading
from PyQt6.QtWidgets import QApplication
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.screen=false")
import config
from core.hotkeys import HotkeyListener
from core import capture, llm, audio, context_fetcher, stt
from core.assistant_text import ThoughtStreamParser
from core import tts as tts_module
from core import memory as memory_module
from core.system.app_platform import configure_windows_app_identity
from core.memory_store.commands import extract_remember_fact
from ui.overlay import DollOverlay, OverlaySignals
from ui.intent_overlay import IntentOverlay
from ui.snip_overlay import SnipOverlay
from ui.chat_window import ChatWindow


class App:
    def __init__(self):
        configure_windows_app_identity()
        self._qt = QApplication(sys.argv)
        self._qt.setApplicationName("Wisp")
        self._qt.setApplicationDisplayName("Wisp")
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
        self._context_buffer_lock = threading.Lock()
        self._last_reply: str = ""
        self._pending_context: context_fetcher.ContextSnapshot | None = None
        self._voice_active: bool = False        # guard against spurious release events
        self._intent_picker: IntentOverlay | None = None
        self._snip_overlay: SnipOverlay | None = None
        self._chat_window: ChatWindow | None = None
        self._memory_viewer = None
        self._all_conversations: list[dict] = []  # each item = {"messages": [...], "context": str}
        self._overlay_hwnd: int = 0          # cached after first show
        self._pending_capture: tuple | None = None  # (selected_text, screenshot_b64)
        self._pending_caller_idx: int = 0    # which CALLER_ROWS entry triggered the current picker
        self._pending_context_policy: dict | None = None
        self._pending_paste_target: int = 0  # HWND to paste into (0 = no paste)
        self._pending_intent_target: int = 0 # HWND whose monitor should host the picker
        self._gen_id: int = 0                # incremented on each new query; stale workers skip bubble signals
        self._gen_id_lock = threading.Lock()

        # Memory
        self._memory = memory_module.get_manager()

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._signals.show_snip_overlay.connect(self._show_snip_overlay)
        self._signals.settings_applied.connect(self._on_settings_applied)
        self._signals.show_new_chat.connect(self._on_show_new_chat)
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
        print(f"[main] Wisp running. Callers: {[c['hotkey'] for c in config.CALLER_ROWS]}")
        sys.exit(self._qt.exec())

    # ------------------------------------------------------------------
    # Pre-warm
    # ------------------------------------------------------------------

    def _prewarm(self):
        tts_module.prewarm()
        print("[main] Connections pre-warmed.")

    # ------------------------------------------------------------------
    # Settings applied â€” reload config + re-register hotkeys live
    # ------------------------------------------------------------------

    def _on_settings_applied(self):
        config.reload()
        tts_module.reset_connections()
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
        self._overlay.apply_settings()
        print("[main] Config reloaded and hotkeys re-registered.")

    # ------------------------------------------------------------------
    # Hotkey â†’ show intent picker (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_add_context(self):
        """Alt+Q â€” capture selected text and append to context buffer."""
        text = capture.get_selected_text()
        if text:
            with self._context_buffer_lock:
                self._context_buffer.append(text)
            print(f"[main] Context buffer: {len(self._context_buffer)} item(s) queued.")

    def _on_clear_context(self):
        """Alt+W â€” clear the context buffer."""
        with self._context_buffer_lock:
            self._context_buffer.clear()
        print("[main] Context buffer cleared.")

    def _steal_foreground(self) -> None:
        """AttachThreadInput + SetForegroundWindow â€” must be called from the keyboard-hook thread."""
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
        """Ctrl+Alt+Q â€” show the region selector, then the intent picker."""
        try:
            import ctypes
            self._pending_intent_target = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            self._pending_intent_target = 0
        self._steal_foreground()
        self._signals.show_snip_overlay.emit()

    def _on_caller_hotkey(self, caller_idx: int):
        """Called when any caller hotkey fires. Dispatches based on caller's paste_back flag."""
        import ctypes
        caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
        paste_back = caller.get("paste_back", False)

        # Save the foreground HWND BEFORE stealing focus so the picker can open
        # on the same monitor and paste-back callers can restore focus correctly.
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        target_hwnd = fg_hwnd if paste_back else 0

        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()

        self._pending_context = context_fetcher.fetch_and_save()
        selected = capture.get_selected_text()
        screenshot_b64 = None
        if caller.get("context_screenshot") and not selected:
            img = capture.get_screen_snippet()
            screenshot_b64 = capture.image_to_base64(img)
        self._pending_capture = (selected, screenshot_b64)
        self._pending_caller_idx = caller_idx
        self._pending_context_policy = caller
        self._pending_paste_target = target_hwnd
        self._pending_intent_target = fg_hwnd

        self._steal_foreground()
        audio.play_filler()
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
        self._pending_context_policy = {
            "context_ambient": config.SNIP_CONTEXT_AMBIENT,
            "context_documents": config.SNIP_CONTEXT_DOCUMENTS,
            "context_tools": config.SNIP_CONTEXT_TOOLS,
        }
        self._pending_paste_target = 0

        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()

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
        """F9 key-down â€” start recording."""
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
        """F9 key-up â€” stop recording, transcribe, and query."""
        if not self._voice_active:
            return
        self._voice_active = False
        # Transcription is blocking (~200â€“600 ms) â€” run off the hotkey thread.
        threading.Thread(target=self._voice_transcribe_and_query, daemon=True).start()

    def _voice_transcribe_and_query(self):
        text = stt.stop_and_transcribe()
        if not text:
            self._signals.set_state.emit("idle")
            if config.DOLL_AUTO_HIDE:
                self._signals.hide_doll.emit()
            return
        self._signals.set_state.emit("thinking")
        with self._gen_id_lock:
            self._gen_id += 1
            gen_id = self._gen_id
        self._query_and_speak(text, (None, None), gen_id=gen_id)

    # ------------------------------------------------------------------
    # Intent picker (runs on Qt main thread via signal)
    # ------------------------------------------------------------------

    def _show_intent_picker(self, caller_idx: int):
        if self._intent_picker is not None:
            return  # already showing

        self._intent_picker = IntentOverlay(caller_idx, target_hwnd=self._pending_intent_target)
        self._intent_picker.intent_chosen.connect(self._on_intent_chosen)
        self._intent_picker.cancelled.connect(self._on_intent_cancelled)
        self._intent_picker.show()

        # Second focus grab from the Qt main thread â€” by now the picker has a
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
        context_policy = self._pending_context_policy
        self._pending_context_policy = None
        target_hwnd  = self._pending_paste_target
        self._pending_paste_target = 0
        self._pending_intent_target = 0

        caller = config.CALLER_ROWS[caller_idx] if caller_idx < len(config.CALLER_ROWS) else {}
        with self._gen_id_lock:
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
                args=(prompt, capture_data, context_policy or caller, gen_id),
                daemon=True,
            ).start()

    def _on_intent_cancelled(self):
        self._intent_picker = None
        self._pending_context_policy = None
        self._pending_paste_target = 0
        self._pending_intent_target = 0
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

        if gen_id == self._gen_id:
            self._signals.set_state.emit("thinking")
            self._signals.bubble_thinking.emit()

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

    def _query_and_speak(self, intent_prompt: str, capture_data: tuple | None, caller: dict | None = None, gen_id: int = 0):
        import queue
        caller = caller or {}

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
        ambient_ctx = (
            context_fetcher.format_context_for_prompt(snap)
            if snap and caller.get("context_ambient", True)
            else ""
        )

        # Build final message â€” intent prompt only; context goes into the system
        # prompt so it is sent exactly once and not repeated in every follow-up turn.
        with self._context_buffer_lock:
            context_items = self._context_buffer.copy()
            self._context_buffer.clear()

        all_contexts = context_items + ([selected] if selected else [])
        user_message = intent_prompt  # clean turn â€” no embedded context
        if all_contexts:
            # Merge selected / buffered text into ambient_ctx (system-prompt layer).
            ctx_block = (
                "\n\n".join(f"Context {i + 1}:\n{c}" for i, c in enumerate(all_contexts))
                if len(all_contexts) > 1
                else all_contexts[0]
            )
            ambient_ctx = f"{ambient_ctx}\n\n---\n{ctx_block}" if ambient_ctx else ctx_block

        # Proactively read the active document (if any) so the model has full context
        # without needing a tool round-trip.  Only for text queries â€” vision queries
        # already have a screenshot as context.
        if not screenshot_b64 and caller.get("context_documents", True):
            doc_text = llm.read_active_document_for_context()
            if doc_text and not doc_text.startswith(("Could not", "File type", "Failed to")):
                ambient_ctx = (
                    f"{ambient_ctx}\n\n---\n[Active document]\n{doc_text}"
                    if ambient_ctx else f"[Active document]\n{doc_text}"
                )

        self._signals.set_state.emit("thinking")
        self._signals.bubble_thinking.emit()

        # Memory: check for explicit "remember that" command, then retrieve
        # relevant LTM facts and the current STM session summary.
        remember_fact = extract_remember_fact(user_message)
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

        # Fresh query â€” reset streamed text accumulator.
        full_text = ""
        reply_text = ""
        llm_chunk_q: queue.Queue[str | None] = queue.Queue()
        parser = ThoughtStreamParser()

        def llm_producer():
            nonlocal full_text, reply_text
            try:
                for chunk in llm.stream_response(
                    user_message,
                    screenshot_b64,
                    ambient_context=ambient_ctx,
                    memory_context=memory_context,
                    use_tools=(not screenshot_b64 and caller.get("context_tools", True)),
                ):
                    full_text += chunk
                    for part, is_thought in parser.feed(chunk):
                        if not part:
                            continue
                        self._signals.bubble_chunk.emit(part, is_thought)
                        if not is_thought:
                            reply_text += part
                            llm_chunk_q.put(part)
                    if gen_id != self._gen_id:
                        break  # a newer query started â€” stop feeding stale chunks to the bubble
            finally:
                for part, is_thought in parser.finish():
                    if not part:
                        continue
                    self._signals.bubble_chunk.emit(part, is_thought)
                    if not is_thought:
                        reply_text += part
                        llm_chunk_q.put(part)
                llm_chunk_q.put(None)
            # Store context separately so the chat window can inject it into
            # the system prompt for follow-ups without re-embedding it in turns.
            assistant_msg = {"role": "assistant", "content": reply_text}
            if full_text != reply_text:
                assistant_msg["display_content"] = full_text
            self._all_conversations.append({
                "messages": [
                    {"role": "user", "content": user_message,
                     **({"image_base64": screenshot_b64} if screenshot_b64 else {})},
                    assistant_msg,
                ],
                "context": ambient_ctx,
            })
            # Record the completed turn in short-term memory.
            self._memory.record_turn(user_message, reply_text, ambient_ctx)

        def llm_chunk_iter():
            while True:
                chunk = llm_chunk_q.get()
                if chunk is None:
                    return
                yield chunk

        def on_audio_start():
            # Called when first PCM chunk hits the speaker â€” start word reveal + speaking anim.
            if gen_id != self._gen_id:
                return
            self._signals.set_state.emit("speaking")
            self._signals.bubble_start_reveal.emit()

        def on_amplitude(amp: float):
            self._signals.set_mouth_amp.emit(amp)

        def on_word_timestamps(words, start_ms):
            # Real word timings from Cartesia â€” drives precise bubble sync.
            if gen_id == self._gen_id:
                self._signals.bubble_schedule_words.emit(words, start_ms)

        def tts_consumer():
            if config.TTS_PROVIDER.lower() == "none":
                on_audio_start()

            def _on_done():
                self._last_reply = reply_text
                if gen_id == self._gen_id:
                    self._signals.set_state.emit("idle")
                    self._signals.bubble_finish.emit()
                    if config.DOLL_AUTO_HIDE:
                        self._signals.hide_doll.emit()

            audio.play_tts_stream_from_chunks(
                llm_chunk_iter(),
                on_done=_on_done,
                on_audio_start=on_audio_start,
                on_amplitude=on_amplitude,
                on_word_timestamps=on_word_timestamps,
            )

        threading.Thread(target=llm_producer, daemon=True).start()
        threading.Thread(target=tts_consumer, daemon=True).start()

    # ------------------------------------------------------------------
    # Doll click â†’ show popup
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

    def _on_doll_click(self, event):
        auto_msg = config.CHAT_ELABORATE_PROMPT if config.CHAT_AUTO_ELABORATE and self._all_conversations else None
        self._open_or_raise_chat(auto_message=auto_msg)

    def _on_show_last_chat(self):
        """Tray menu 'Last chat' â€” open or raise the chat window."""
        self._open_or_raise_chat()

    def _on_show_new_chat(self):
        """Tray menu 'New chat' â€” open the chat window on a fresh thread."""
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
    # Our clipboard helper (capture.get_selected_text) injects a synthetic Ctrl+C
    # via keyboard.send("ctrl+c") while the terminal is still the foreground window.
    # On Windows, this delivers CTRL_C_EVENT to every process in the console group â€”
    # including us â€” which becomes KeyboardInterrupt in the Qt event loop.
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

