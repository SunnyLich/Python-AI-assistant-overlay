import unittest
import sys
import types
from unittest import mock

from core import stt
from core.macos_helper import handlers as helper_handlers
from core.stt_postprocess import clean_transcript, looks_like_repeated_token_noise


class STTMacOSSafetyTests(unittest.TestCase):
    def test_stt_prewarm_skips_model_load_in_safe_mode(self):
        with mock.patch.object(stt.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(stt.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(stt.macos_helper, "is_enabled", return_value=False), \
             mock.patch.object(stt, "_get_model", side_effect=AssertionError("no model load")):
            stt.prewarm()

    def test_recording_skips_sounddevice_in_safe_mode(self):
        with mock.patch.object(stt.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(stt.macos_safety.os.environ, {}, clear=True), \
             mock.patch.object(stt.macos_helper, "is_enabled", return_value=False), \
             mock.patch.object(stt, "run_on_main", side_effect=AssertionError("no CoreAudio open")):
            stt.start_recording()

        self.assertEqual(stt.stop_and_transcribe(), "")

    def test_repeated_token_noise_is_discarded(self):
        noisy = " ".join(["Cont"] * 20)

        self.assertTrue(looks_like_repeated_token_noise(noisy))
        self.assertEqual(clean_transcript(noisy), "")

    def test_normal_repetition_is_kept(self):
        text = "yes yes yes I can hear you now"

        self.assertFalse(looks_like_repeated_token_noise(text))
        self.assertEqual(clean_transcript(text), text)

    def test_helper_record_start_replaces_existing_stream_and_stop_cleans_up(self):
        streams = []

        class FakeStream:
            def __init__(self, **_kwargs):
                self.started = False
                self.stopped = False
                self.closed = False
                streams.append(self)

            def start(self):
                self.started = True

            def stop(self):
                self.stopped = True

            def close(self):
                self.closed = True

        fake_sounddevice = types.SimpleNamespace(InputStream=FakeStream)
        old_stream = helper_handlers._stream
        old_recording = helper_handlers._recording
        old_chunks = list(helper_handlers._chunks)
        try:
            helper_handlers._stream = None
            helper_handlers._recording = False
            helper_handlers._chunks.clear()
            with mock.patch.dict(sys.modules, {"sounddevice": fake_sounddevice}):
                helper_handlers.stt_start_recording()
                helper_handlers.stt_start_recording()
                text = helper_handlers.stt_stop_and_transcribe()

            self.assertEqual(text, "")
            self.assertEqual(len(streams), 2)
            self.assertTrue(streams[0].stopped)
            self.assertTrue(streams[0].closed)
            self.assertTrue(streams[1].stopped)
            self.assertTrue(streams[1].closed)
            self.assertIsNone(helper_handlers._stream)
            self.assertFalse(helper_handlers._recording)
        finally:
            helper_handlers._stream = old_stream
            helper_handlers._recording = old_recording
            helper_handlers._chunks[:] = old_chunks


if __name__ == "__main__":
    unittest.main()
