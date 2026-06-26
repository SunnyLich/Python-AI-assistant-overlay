"""Tests for test tts connection."""

import io
import sys
import types
import unittest
import wave
from unittest.mock import patch

from core import tts


def _tiny_wav() -> bytes:
    """Return a small mono PCM WAV for fake TTS responses."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(32000)
        wf.writeframes(b"\x01\x00\x02\x00")
    return buf.getvalue()


class TtsConnectionTests(unittest.TestCase):
    """Test case for tts connection tests behavior."""
    def test_none_provider_reports_disabled(self):
        """Verify none provider reports disabled behavior."""
        ok, message = tts.test_connection("none")

        self.assertTrue(ok)
        self.assertIn("disabled", message)

    def test_cartesia_connection_requires_voice_id(self):
        """Verify cartesia connection requires voice id behavior."""
        ok, message = tts.test_connection(
            "cartesia",
            cartesia_api_key="cartesia-key",
            cartesia_voice_id="",
        )

        self.assertFalse(ok)
        self.assertIn("CARTESIA_VOICE_ID", message)

    def test_elevenlabs_connection_succeeds_when_audio_arrives(self):
        """Verify elevenlabs connection succeeds when audio arrives behavior."""
        class FakeElevenLabs:
            """Test case for fake eleven labs behavior."""
            def __init__(self, api_key):
                """Initialize the fake eleven labs instance."""
                self.api_key = api_key

            def generate(self, **kwargs):
                """Verify generate behavior."""
                yield b"audio"

        fake_module = types.ModuleType("elevenlabs.client")
        fake_module.ElevenLabs = FakeElevenLabs

        with patch.dict(sys.modules, {"elevenlabs.client": fake_module}):
            ok, message = tts.test_connection(
                "elevenlabs",
                elevenlabs_api_key="eleven-key",
            )

        self.assertTrue(ok)
        self.assertIn("elevenlabs", message)

    def test_gpt_sovits_requires_reference_audio(self):
        """Verify GPT-SoVITS connection requires reference audio."""
        ok, message = tts.test_connection(
            "gpt_sovits",
            gpt_sovits_url="http://127.0.0.1:9880",
            gpt_sovits_ref_audio_path="",
        )

        self.assertFalse(ok)
        self.assertIn("GPT_SOVITS_REF_AUDIO_PATH", message)

    def test_gpt_sovits_connection_posts_to_local_api(self):
        """Verify GPT-SoVITS connection posts the expected local API request."""
        calls = []

        class FakeResponse:
            """Fake requests response."""
            status_code = 200
            content = _tiny_wav()
            text = ""

        fake_requests = types.ModuleType("requests")

        def fake_post(url, json, timeout):
            """Capture the post request and return WAV bytes."""
            calls.append({"url": url, "json": json, "timeout": timeout})
            return FakeResponse()

        fake_requests.post = fake_post

        with patch.dict(sys.modules, {"requests": fake_requests}):
            ok, message = tts.test_connection(
                "gpt_sovits",
                gpt_sovits_url="http://127.0.0.1:9880",
                gpt_sovits_ref_audio_path=r"C:\voices\ref.wav",
                gpt_sovits_prompt_text="hello there",
                gpt_sovits_prompt_lang="en",
                gpt_sovits_text_lang="en",
            )

        self.assertTrue(ok)
        self.assertIn("gpt_sovits", message)
        self.assertEqual(calls[0]["url"], "http://127.0.0.1:9880/tts")
        self.assertEqual(calls[0]["json"]["ref_audio_path"], r"C:\voices\ref.wav")
        self.assertEqual(calls[0]["json"]["prompt_text"], "hello there")
        self.assertEqual(calls[0]["json"]["text"], "ok")

    def test_kokoro_requires_voice(self):
        """Verify Kokoro connection requires a voice name."""
        ok, message = tts.test_connection(
            "kokoro",
            kokoro_voice="",
            kokoro_lang_code="a",
        )

        self.assertFalse(ok)
        self.assertIn("KOKORO_VOICE", message)

    def test_kokoro_connection_uses_local_pipeline(self):
        """Verify Kokoro connection uses the local Python pipeline."""
        calls = []

        class FakeKPipeline:
            """Fake Kokoro pipeline."""
            def __init__(self, lang_code):
                """Capture language code."""
                self.lang_code = lang_code

            def __call__(self, text, voice, speed, split_pattern):
                """Capture synthesis args and return float audio."""
                import numpy as np

                calls.append({
                    "lang_code": self.lang_code,
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "split_pattern": split_pattern,
                })
                yield text, "phonemes", np.array([0.0, 0.25, -0.25], dtype=np.float32)

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline

        with patch.dict(sys.modules, {"kokoro": fake_module}):
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
            )

        self.assertTrue(ok)
        self.assertIn("kokoro", message)
        self.assertEqual(calls[0]["text"], "ok")
        self.assertEqual(calls[0]["voice"], "af_heart")
        self.assertEqual(calls[0]["lang_code"], "a")

    def test_kokoro_connection_passes_supported_device(self):
        """Verify Kokoro receives the configured CUDA device when supported."""
        calls = []

        class FakeCuda:
            @staticmethod
            def is_available():
                return True

        fake_torch = types.SimpleNamespace(cuda=FakeCuda())

        class FakeKPipeline:
            """Fake Kokoro pipeline with device support."""
            def __init__(self, lang_code, device="cpu"):
                """Capture language code and device."""
                self.lang_code = lang_code
                self.device = device

            def __call__(self, text, voice, speed, split_pattern):
                """Capture synthesis args and return float audio."""
                import numpy as np

                calls.append({
                    "lang_code": self.lang_code,
                    "device": self.device,
                    "text": text,
                    "voice": voice,
                })
                yield text, "phonemes", np.array([0.0, 0.25, -0.25], dtype=np.float32)

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline

        with patch.dict(sys.modules, {"kokoro": fake_module, "torch": fake_torch}):
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
                kokoro_device="cuda",
            )

        self.assertTrue(ok)
        self.assertIn("kokoro", message)
        self.assertEqual(calls[0]["device"], "cuda")

    def test_kokoro_connection_falls_back_to_cpu_when_cuda_build_fails(self):
        """Verify a bad CUDA Kokoro init does not fail local TTS outright."""
        calls = []

        class FakeCuda:
            @staticmethod
            def is_available():
                return True

        fake_torch = types.SimpleNamespace(cuda=FakeCuda())

        class FakeKPipeline:
            """Fake Kokoro pipeline that fails on CUDA but works on CPU."""
            def __init__(self, lang_code, device="cpu"):
                """Capture language code and device."""
                if device == "cuda":
                    raise RuntimeError("CUDA failed")
                self.lang_code = lang_code
                self.device = device

            def __call__(self, text, voice, speed, split_pattern):
                """Capture synthesis args and return float audio."""
                import numpy as np

                calls.append({"lang_code": self.lang_code, "device": self.device, "text": text})
                yield text, "phonemes", np.array([0.0, 0.25, -0.25], dtype=np.float32)

        fake_module = types.ModuleType("kokoro")
        fake_module.KPipeline = FakeKPipeline

        with patch.dict(sys.modules, {"kokoro": fake_module, "torch": fake_torch}):
            ok, message = tts.test_connection(
                "kokoro",
                kokoro_voice="af_heart",
                kokoro_lang_code="a",
                kokoro_device="cuda",
            )

        self.assertTrue(ok)
        self.assertIn("kokoro", message)
        self.assertEqual(calls[0]["device"], "cpu")
