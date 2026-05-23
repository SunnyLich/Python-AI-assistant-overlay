import sys
import types
import unittest
from unittest.mock import patch

from core import tts


class TtsConnectionTests(unittest.TestCase):
    def test_none_provider_reports_disabled(self):
        ok, message = tts.test_connection("none")

        self.assertTrue(ok)
        self.assertIn("disabled", message)

    def test_cartesia_connection_requires_voice_id(self):
        ok, message = tts.test_connection(
            "cartesia",
            cartesia_api_key="cartesia-key",
            cartesia_voice_id="",
        )

        self.assertFalse(ok)
        self.assertIn("CARTESIA_VOICE_ID", message)

    def test_elevenlabs_connection_succeeds_when_audio_arrives(self):
        class FakeElevenLabs:
            def __init__(self, api_key):
                self.api_key = api_key

            def generate(self, **kwargs):
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