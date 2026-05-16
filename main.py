"""
main.py — Entry point for the AI Assistant Overlay.

Wires together:
  - HotkeyListener   (core/hotkeys.py)
  - Capture          (core/capture.py)
  - LLM streaming    (core/llm.py)
  - TTS + audio      (core/audio.py)
  - Doll overlay UI  (ui/overlay.py)
"""
import sys
import threading
from PyQt6.QtWidgets import QApplication
import config
from core.hotkeys import HotkeyListener
from core import capture, llm, audio
from ui.overlay import DollOverlay, OverlaySignals


class App:
    def __init__(self):
        self._qt = QApplication(sys.argv)
        self._signals = OverlaySignals()
        self._overlay = DollOverlay(self._signals)
        self._hotkeys = HotkeyListener(on_invoke=self._on_invoke)
        self._last_reply: str = ""

        # Wire up doll click → show popup with last reply
        self._overlay._label.mousePressEvent = self._on_doll_click

    def run(self):
        self._overlay.show()
        self._hotkeys.start()
        print("[main] AI Assistant Overlay running. Press Ctrl+U to invoke.")
        sys.exit(self._qt.exec())

    # ------------------------------------------------------------------
    # Hotkey callback — runs in keyboard listener thread
    # ------------------------------------------------------------------

    def _on_invoke(self, intent_key: str, intent_prompt: str):
        """
        Called when the user presses the invoke hotkey (+ optional arrow key).
        Everything in this method runs off the main thread.
        """
        # Immediately: play filler audio + animate doll
        audio.play_filler()
        self._signals.set_state.emit("listening")

        # Capture input
        selected = capture.get_selected_text()
        screenshot_b64 = None

        if not selected:
            # No text selected — fall back to screen snippet
            img = capture.get_screen_snippet()
            screenshot_b64 = capture.image_to_base64(img)

        # Build prompt
        if intent_prompt:
            user_message = intent_prompt
            if selected:
                user_message = f"{intent_prompt}\n\n{selected}"
        elif selected:
            user_message = selected
        else:
            user_message = "What is on my screen?"

        # Kick off LLM + TTS in a worker thread
        threading.Thread(
            target=self._query_and_speak,
            args=(user_message, screenshot_b64),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # LLM + TTS pipeline — runs in worker thread
    # ------------------------------------------------------------------

    def _query_and_speak(self, user_message: str, image_b64: str | None):
        self._signals.set_state.emit("thinking")

        # Stream LLM response
        full_text = ""
        for chunk in llm.stream_response(user_message, image_b64):
            full_text += chunk

        self._last_reply = full_text

        # Speak + animate
        self._signals.set_state.emit("speaking")
        audio.play_tts_stream(
            full_text,
            on_done=lambda: self._signals.set_state.emit("idle"),
        )

    # ------------------------------------------------------------------
    # Doll click — show full reply popup
    # ------------------------------------------------------------------

    def _on_doll_click(self, event):
        if self._last_reply:
            self._signals.show_text_popup.emit(self._last_reply)


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
