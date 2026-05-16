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


class App:
    def __init__(self):
        self._qt = QApplication(sys.argv)
        self._signals = OverlaySignals()
        self._overlay = DollOverlay(self._signals)
        self._hotkeys = HotkeyListener(on_invoke=self._on_hotkey)
        self._last_reply: str = ""
        self._intent_picker: IntentOverlay | None = None

        # Wire signals
        self._signals.show_intent_picker.connect(self._show_intent_picker)
        self._overlay.set_click_handler(self._on_doll_click)

        # Pre-warm connections in background
        threading.Thread(target=self._prewarm, daemon=True).start()

    def run(self):
        self._overlay.show()
        self._hotkeys.start()
        print("[main] AI Assistant Overlay running. Press Ctrl+E to invoke.")
        sys.exit(self._qt.exec())

    # ------------------------------------------------------------------
    # Pre-warm
    # ------------------------------------------------------------------

    def _prewarm(self):
        tts_module.prewarm()
        print("[main] Connections pre-warmed.")

    # ------------------------------------------------------------------
    # Hotkey → show intent picker (runs in keyboard listener thread)
    # ------------------------------------------------------------------

    def _on_hotkey(self):
        audio.play_filler()
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

    def _on_intent_chosen(self, direction: str, prompt: str):
        self._intent_picker = None
        threading.Thread(
            target=self._query_and_speak,
            args=(prompt,),
            daemon=True,
        ).start()

    def _on_intent_cancelled(self):
        self._intent_picker = None
        self._signals.set_state.emit("idle")

    # ------------------------------------------------------------------
    # LLM + TTS pipeline (worker thread)
    # ------------------------------------------------------------------

    def _query_and_speak(self, intent_prompt: str):
        import queue

        # Capture input
        selected = capture.get_selected_text()
        screenshot_b64 = None
        if not selected:
            img = capture.get_screen_snippet()
            screenshot_b64 = capture.image_to_base64(img)

        # Build final message
        if selected:
            user_message = f"{intent_prompt}\n\n{selected}"
        else:
            user_message = intent_prompt

        self._signals.set_state.emit("thinking")
        self._signals.bubble_thinking.emit()

        full_text = ""
        llm_chunk_q: queue.Queue[str | None] = queue.Queue()

        def llm_producer():
            nonlocal full_text
            try:
                for chunk in llm.stream_response(user_message, screenshot_b64):
                    full_text += chunk
                    llm_chunk_q.put(chunk)
                    self._signals.bubble_chunk.emit(chunk)
            finally:
                llm_chunk_q.put(None)

        def llm_chunk_iter():
            while True:
                chunk = llm_chunk_q.get()
                if chunk is None:
                    return
                yield chunk

        def tts_consumer():
            self._signals.set_state.emit("speaking")
            audio.play_tts_stream_from_chunks(
                llm_chunk_iter(),
                on_done=lambda: (
                    setattr(self, "_last_reply", full_text),
                    self._signals.set_state.emit("idle"),
                    self._signals.bubble_finish.emit(),
                ),
            )

        threading.Thread(target=llm_producer, daemon=True).start()
        threading.Thread(target=tts_consumer, daemon=True).start()

    # ------------------------------------------------------------------
    # Doll click → show popup
    # ------------------------------------------------------------------

    def _on_doll_click(self, event):
        if self._last_reply:
            self._signals.show_text_popup.emit(self._last_reply)


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
