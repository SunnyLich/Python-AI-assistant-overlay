"""
main.py — Entry point for the AI Assistant Overlay.

Flow:
  Ctrl+E → IntentOverlay appears (WASD picker)
  WASD key → capture input → LLM stream → TTS stream → audio out
  Click doll → show full reply popup
"""
import sys
import threading
from PyQt6.QtWidgets import QApplication
import config
from core.hotkeys import HotkeyListener
from core import capture, llm, audio
from core import tts as tts_module
from ui.overlay import DollOverlay, OverlaySignals
from ui.intent_overlay import IntentOverlay
from ui.snip_overlay import SnipOverlay
from ui.chat_window import ChatWindow


class App:
    def __init__(self):
        self._qt = QApplication(sys.argv)
        self._qt.setQuitOnLastWindowClosed(False)  # chat/settings closing must not exit the app
        self._signals = OverlaySignals()
        self._overlay = DollOverlay(self._signals)
        self._hotkeys = HotkeyListener(
            on_invoke=self._on_hotkey,
            on_add_context=self._on_add_context,
            on_clear_context=self._on_clear_context,
            on_snip=self._on_snip_hotkey,
        )
        self._context_buffer: list[str] = []   # accumulated via Alt+Q
        self._last_reply: str = ""
        self._intent_picker: IntentOverlay | None = None
        self._snip_overlay: SnipOverlay | None = None
        self._chat_window: ChatWindow | None = None
        self._all_conversations: list[list[dict]] = []  # each item = one full Q&A session
        self._overlay_hwnd: int = 0          # cached after first show
        self._pending_capture: tuple | None = None  # (selected_text, screenshot_b64)

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._signals.show_snip_overlay.connect(self._show_snip_overlay)
        self._signals.settings_applied.connect(self._on_settings_applied)
        self._signals.show_last_chat.connect(self._on_show_last_chat)
        self._overlay.set_click_handler(self._on_doll_click)

        # Pre-warm connections in background
        threading.Thread(target=self._prewarm, daemon=True).start()

    def run(self):
        if not config.DOLL_AUTO_HIDE:
            self._overlay.show()
        self._overlay_hwnd = int(self._overlay.winId())  # safe: Qt main thread
        self._hotkeys.start()
        print("[main] AI Assistant Overlay running. Press Ctrl+Q to invoke.")
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
            on_invoke=self._on_hotkey,
            on_add_context=self._on_add_context,
            on_clear_context=self._on_clear_context,
            on_snip=self._on_snip_hotkey,
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

    def _on_snip_hotkey(self):
        """Ctrl+Alt+Q — show the region selector, then the intent picker."""
        # Steal foreground from the keyboard hook thread (same pattern as _on_hotkey)
        if self._overlay_hwnd:
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
        self._signals.show_snip_overlay.emit()

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

        audio.play_filler()
        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()
        self._signals.set_state.emit("listening")
        self._signals.show_intent_picker.emit()

    def _on_snip_cancelled(self):
        self._snip_overlay = None

    def _on_hotkey(self):
        # 1. Capture FIRST — original app still has focus, so Ctrl+C works.
        selected = capture.get_selected_text()
        screenshot_b64 = None
        if not selected:
            img = capture.get_screen_snippet()
            screenshot_b64 = capture.image_to_base64(img)
        self._pending_capture = (selected, screenshot_b64)

        # 2. Steal foreground from the keyboard hook thread.
        #    Plain SetForegroundWindow fails silently when called from a non-foreground
        #    thread.  AttachThreadInput borrows the input state of whichever thread
        #    owns the foreground right now, which unlocks SetForegroundWindow for us.
        if self._overlay_hwnd:
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

        audio.play_filler()
        if config.DOLL_AUTO_HIDE:
            self._signals.show_doll.emit()
        self._signals.set_state.emit("listening")
        self._signals.show_intent_picker.emit()

    # ------------------------------------------------------------------
    # Intent picker (runs on Qt main thread via signal)
    # ------------------------------------------------------------------

    def _show_intent_picker(self):
        if self._intent_picker is not None:
            return  # already showing

        self._intent_picker = IntentOverlay()
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
        threading.Thread(
            target=self._query_and_speak,
            args=(prompt, capture_data),
            daemon=True,
        ).start()

    def _on_intent_cancelled(self):
        self._intent_picker = None
        self._signals.set_state.emit("idle")
        if config.DOLL_AUTO_HIDE:
            self._signals.hide_doll.emit()

    # ------------------------------------------------------------------
    # LLM + TTS pipeline (worker thread)
    # ------------------------------------------------------------------

    def _query_and_speak(self, intent_prompt: str, capture_data: tuple | None):
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

        # Build final message, incorporating any buffered context items
        context_items = self._context_buffer.copy()
        self._context_buffer.clear()

        all_contexts = context_items + ([selected] if selected else [])
        if len(all_contexts) > 1:
            labelled = "\n\n".join(
                f"Context {i + 1}:\n{ctx}" for i, ctx in enumerate(all_contexts)
            )
            user_message = f"{intent_prompt}\n\n{labelled}"
        elif all_contexts:
            user_message = f"{intent_prompt}\n\n{all_contexts[0]}"
        else:
            user_message = intent_prompt

        self._signals.set_state.emit("thinking")
        self._signals.bubble_thinking.emit()

        # Reset conversation for a fresh query (new hotkey invocation)
        self._conversation_history = []
        full_text = ""
        llm_chunk_q: queue.Queue[str | None] = queue.Queue()

        def llm_producer():
            nonlocal full_text
            try:
                for chunk in llm.stream_response(user_message, screenshot_b64):
                    full_text += chunk
                    llm_chunk_q.put(chunk)
                    self._signals.bubble_chunk.emit(chunk)   # buffered by bubble in reveal mode
            finally:
                llm_chunk_q.put(None)
            self._all_conversations.append([
                {"role": "user", "content": user_message,
                 **(({"image_base64": screenshot_b64}) if screenshot_b64 else {})},
                {"role": "assistant", "content": full_text},
            ])

        def llm_chunk_iter():
            while True:
                chunk = llm_chunk_q.get()
                if chunk is None:
                    return
                yield chunk

        def on_audio_start():
            # Called when first PCM chunk hits the speaker — start word reveal.
            self._signals.bubble_start_reveal.emit()

        def on_word_timestamps(words, start_ms):
            # Real word timings from Cartesia — drives precise bubble sync.
            self._signals.bubble_schedule_words.emit(words, start_ms)

        def tts_consumer():
            self._signals.set_state.emit("speaking")
            audio.play_tts_stream_from_chunks(
                llm_chunk_iter(),
                on_done=lambda: (
                    setattr(self, "_last_reply", full_text),
                    self._signals.set_state.emit("idle"),
                    self._signals.bubble_finish.emit(),
                    self._signals.hide_doll.emit() if config.DOLL_AUTO_HIDE else None,
                ),
                on_audio_start=on_audio_start,
                on_word_timestamps=on_word_timestamps,
            )

        threading.Thread(target=llm_producer, daemon=True).start()
        threading.Thread(target=tts_consumer, daemon=True).start()

    # ------------------------------------------------------------------
    # Doll click → show popup
    # ------------------------------------------------------------------

    def _on_doll_click(self, event):
        auto_msg = config.CHAT_ELABORATE_PROMPT if config.CHAT_AUTO_ELABORATE and self._all_conversations else None
        if self._chat_window is not None:
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            return
        self._chat_window = ChatWindow(
            conversations=self._all_conversations,
            send_fn=llm.stream_response_with_history,
            auto_message=auto_msg,
        )
        self._chat_window.destroyed.connect(lambda: setattr(self, "_chat_window", None))
        self._chat_window.show()

    def _on_show_last_chat(self):
        """Tray menu 'Last chat' — open or raise the chat window."""
        if self._chat_window is not None:
            self._chat_window.raise_()
            self._chat_window.activateWindow()
            return
        self._chat_window = ChatWindow(
            conversations=self._all_conversations,
            send_fn=llm.stream_response_with_history,
        )
        self._chat_window.destroyed.connect(lambda: setattr(self, "_chat_window", None))
        self._chat_window.show()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
