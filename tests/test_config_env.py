import os
import unittest
from unittest.mock import patch

import config


class ConfigEnvTests(unittest.TestCase):
    def test_reload_parses_icon_size_and_bool_aliases(self):
        previous = {
            "ICON_SIZE": config.ICON_SIZE,
            "DARK_MODE": config.DARK_MODE,
            "ICON_AUTO_HIDE": config.ICON_AUTO_HIDE,
            "SNIP_CONTEXT_DOCUMENTS": config.SNIP_CONTEXT_DOCUMENTS,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "ICON_SIZE": "96",
                    "DARK_MODE": "true",
                    "ICON_AUTO_HIDE": "yes",
                    "SNIP_CONTEXT_DOCUMENTS": "off",
                },
                clear=False,
            ):
                config.reload()

            self.assertEqual(config.ICON_SIZE, 96)
            self.assertTrue(config.DARK_MODE)
            self.assertTrue(config.ICON_AUTO_HIDE)
            self.assertFalse(config.SNIP_CONTEXT_DOCUMENTS)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_legacy_doll_keys_still_honored(self):
        """Old DOLL_* env keys remain valid via back-compat fallback."""
        previous = {
            "ICON_SIZE": config.ICON_SIZE,
            "ICON_AUTO_HIDE": config.ICON_AUTO_HIDE,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "DOLL_SIZE": "72",
                    "DOLL_AUTO_HIDE": "false",
                },
                clear=False,
            ):
                # Ensure the new keys are absent so the fallback path is exercised.
                os.environ.pop("ICON_SIZE", None)
                os.environ.pop("ICON_AUTO_HIDE", None)
                config.reload()

            self.assertEqual(config.ICON_SIZE, 72)
            self.assertFalse(config.ICON_AUTO_HIDE)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_new_icon_keys_win_over_legacy(self):
        """When both new and legacy keys are set, the new ICON_* key takes precedence."""
        previous = {"ICON_AUTO_HIDE": config.ICON_AUTO_HIDE}
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "ICON_AUTO_HIDE": "false",
                    "DOLL_AUTO_HIDE": "true",
                },
                clear=False,
            ):
                config.reload()

            self.assertFalse(config.ICON_AUTO_HIDE)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_assistant_language_is_appended_to_system_prompt(self):
        previous = {
            "ASSISTANT_LANGUAGE": config.ASSISTANT_LANGUAGE,
            "SYSTEM_PROMPT_UTILITY": config.SYSTEM_PROMPT_UTILITY,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "ASSISTANT_LANGUAGE": "Chinese",
                    "SYSTEM_PROMPT_UTILITY": "Base prompt.",
                },
                clear=False,
            ):
                config.reload()

            prompt = config.get_system_prompt()
            self.assertIn("Base prompt.", prompt)
            self.assertIn("Respond in Chinese", prompt)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_app_language_loads_from_env(self):
        previous = {"APP_LANGUAGE": config.APP_LANGUAGE}
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {"APP_LANGUAGE": "zh"},
                clear=False,
            ):
                config.reload()

            self.assertEqual(config.APP_LANGUAGE, "zh")
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_assistant_language_can_match_user(self):
        previous = {
            "ASSISTANT_LANGUAGE": config.ASSISTANT_LANGUAGE,
            "SYSTEM_PROMPT_UTILITY": config.SYSTEM_PROMPT_UTILITY,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "ASSISTANT_LANGUAGE": "match_user",
                    "SYSTEM_PROMPT_UTILITY": "Base prompt.",
                },
                clear=False,
            ):
                config.reload()

            self.assertIn("same language as the user's latest request", config.get_system_prompt())
        finally:
            for name, value in previous.items():
                setattr(config, name, value)

    def test_reload_refreshes_secret_cache(self):
        with patch("config.load_dotenv"), patch.object(config.secret_store, "refresh_cache") as refresh:
            config.reload()

        refresh.assert_called_once_with()

    def test_caller_context_modes_load_from_new_env_keys(self):
        previous_rows = list(config.CALLER_ROWS)
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "CALLER_COUNT": "1",
                    "CALLER_1_CONTEXT_DOCUMENTS_MODE": "model",
                    "CALLER_1_CONTEXT_BROWSER_MODE": "model",
                    "CALLER_1_CONTEXT_GITHUB_MODE": "off",
                    "CALLER_1_CONTEXT_MEMORY_MODE": "model",
                    "CALLER_1_CONTEXT_SCREENSHOT": "auto",
                },
                clear=False,
            ):
                config.reload()

            row = config.CALLER_ROWS[0]
            self.assertEqual(row["context_documents_mode"], "model")
            self.assertEqual(row["context_browser_mode"], "model")
            self.assertEqual(row["context_github_mode"], "off")
            self.assertEqual(row["context_memory_mode"], "model")
            self.assertFalse(row["context_documents"])
            self.assertTrue(row["context_tools"])
        finally:
            config.CALLER_ROWS[:] = previous_rows

    def test_caller_context_modes_migrate_legacy_tool_keys(self):
        previous_rows = list(config.CALLER_ROWS)
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "CALLER_COUNT": "1",
                    "CALLER_1_CONTEXT_DOCUMENTS": "false",
                    "CALLER_1_CONTEXT_TOOLS": "true",
                },
                clear=False,
            ):
                for key in (
                    "CALLER_1_CONTEXT_DOCUMENTS_MODE",
                    "CALLER_1_CONTEXT_BROWSER_MODE",
                    "CALLER_1_CONTEXT_GITHUB_MODE",
                ):
                    os.environ.pop(key, None)
                config.reload()

            row = config.CALLER_ROWS[0]
            self.assertEqual(row["context_documents_mode"], "model")
            self.assertEqual(row["context_browser_mode"], "model")
            self.assertEqual(row["context_github_mode"], "model")
            self.assertTrue(row["context_tools"])
        finally:
            config.CALLER_ROWS[:] = previous_rows

    _VOICE_ENV_KEYS = (
        "VOICE_CONTEXT_AMBIENT",
        "VOICE_CONTEXT_CLIPBOARD",
        "VOICE_CONTEXT_DOCUMENTS_MODE",
        "VOICE_CONTEXT_BROWSER_MODE",
        "VOICE_CONTEXT_GITHUB_MODE",
        "VOICE_CONTEXT_MEMORY_MODE",
        "VOICE_CONTEXT_SCREENSHOT",
        "VOICE_TOOLS",
    )

    def test_voice_caller_defaults_mirror_general_caller(self):
        previous = dict(config.VOICE_CALLER)
        try:
            with patch("config.load_dotenv"), patch.dict(os.environ, {}, clear=False):
                for key in self._VOICE_ENV_KEYS:
                    os.environ.pop(key, None)
                config.reload()

            voice = config.VOICE_CALLER
            self.assertTrue(voice["context_ambient"])
            self.assertEqual(voice["context_documents_mode"], "auto")
            self.assertEqual(voice["context_browser_mode"], "off")
            self.assertEqual(voice["context_github_mode"], "off")
            self.assertEqual(voice["context_memory_mode"], "auto")
            self.assertEqual(voice["context_screenshot"], "off")
            self.assertFalse(voice["paste_back"])
            self.assertEqual(voice["tools"], {})
        finally:
            config.VOICE_CALLER.clear()
            config.VOICE_CALLER.update(previous)

    def test_voice_caller_loads_env_overrides_and_tools(self):
        previous = dict(config.VOICE_CALLER)
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "VOICE_CONTEXT_AMBIENT": "false",
                    "VOICE_CONTEXT_DOCUMENTS_MODE": "off",
                    "VOICE_CONTEXT_BROWSER_MODE": "model",
                    "VOICE_CONTEXT_MEMORY_MODE": "off",
                    "VOICE_CONTEXT_SCREENSHOT": "auto",
                    "VOICE_TOOLS": "alpha:on,beta:model",
                },
                clear=False,
            ):
                config.reload()

            voice = config.VOICE_CALLER
            self.assertFalse(voice["context_ambient"])
            self.assertEqual(voice["context_documents_mode"], "off")
            self.assertEqual(voice["context_browser_mode"], "model")
            self.assertEqual(voice["context_memory_mode"], "off")
            self.assertEqual(voice["context_screenshot"], "auto")
            self.assertTrue(voice["context_tools"])
            self.assertEqual(voice["tools"], {"alpha": "on", "beta": "model"})
        finally:
            config.VOICE_CALLER.clear()
            config.VOICE_CALLER.update(previous)

    def test_caller_tool_overrides_load_from_env(self):
        previous_rows = list(config.CALLER_ROWS)
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "CALLER_COUNT": "1",
                    "CALLER_1_TOOLS": "my_tool:on, other:model, junk, off_tool:off",
                },
                clear=False,
            ):
                config.reload()

            self.assertEqual(
                config.CALLER_ROWS[0]["tools"],
                {"my_tool": "on", "other": "model", "off_tool": "off"},
            )
        finally:
            config.CALLER_ROWS[:] = previous_rows


if __name__ == "__main__":
    unittest.main()
