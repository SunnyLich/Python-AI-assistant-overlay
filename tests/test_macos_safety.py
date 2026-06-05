import unittest
from unittest.mock import patch

from core.system import macos_safety


class MacOSSafetyTests(unittest.TestCase):
    def test_safe_mode_defaults_disable_optional_native_paths(self):
        with patch.object(macos_safety.sys, "platform", "darwin"), \
             patch.dict(macos_safety.os.environ, {}, clear=True):
            self.assertTrue(macos_safety.safe_mode_enabled())
            self.assertFalse(macos_safety.audio_enabled())
            self.assertFalse(macos_safety.tts_prewarm_enabled())
            self.assertFalse(macos_safety.stt_prewarm_enabled())
            self.assertFalse(macos_safety.fs_watcher_enabled())
            self.assertFalse(macos_safety.chromadb_enabled())
            self.assertFalse(macos_safety.memory_background_llm_enabled())
            self.assertFalse(macos_safety.openai_compat_streaming_enabled("openai"))
            self.assertFalse(macos_safety.openai_compat_tools_enabled())

    def test_safe_mode_can_be_disabled_for_validation(self):
        with patch.object(macos_safety.sys, "platform", "darwin"), \
             patch.dict(macos_safety.os.environ, {"WISP_MACOS_SAFE_MODE": "0"}, clear=True):
            self.assertFalse(macos_safety.safe_mode_enabled())
            self.assertTrue(macos_safety.audio_enabled())
            self.assertTrue(macos_safety.fs_watcher_enabled())
            self.assertTrue(macos_safety.chromadb_enabled())
            self.assertTrue(macos_safety.memory_background_llm_enabled())
            self.assertTrue(macos_safety.openai_compat_streaming_enabled("openai"))
            self.assertTrue(macos_safety.openai_compat_tools_enabled())

    def test_safe_mode_has_targeted_opt_ins(self):
        env = {
            "WISP_MACOS_ENABLE_AUDIO": "1",
            "WISP_MACOS_ENABLE_FS_WATCHER": "1",
            "WISP_MACOS_ENABLE_CHROMADB": "1",
            "WISP_MACOS_ENABLE_MEMORY_BACKGROUND_LLM": "1",
            "WISP_MACOS_ENABLE_OPENAI_TOOLS": "1",
            "WISP_MACOS_OPENAI_COMPAT_STREAMING": "1",
        }
        with patch.object(macos_safety.sys, "platform", "darwin"), \
             patch.dict(macos_safety.os.environ, env, clear=True):
            self.assertTrue(macos_safety.safe_mode_enabled())
            self.assertTrue(macos_safety.audio_enabled())
            self.assertTrue(macos_safety.tts_prewarm_enabled())
            self.assertTrue(macos_safety.stt_prewarm_enabled())
            self.assertTrue(macos_safety.fs_watcher_enabled())
            self.assertTrue(macos_safety.chromadb_enabled())
            self.assertTrue(macos_safety.memory_background_llm_enabled())
            self.assertTrue(macos_safety.openai_compat_streaming_enabled("openai"))
            self.assertTrue(macos_safety.openai_compat_tools_enabled())


if __name__ == "__main__":
    unittest.main()
