import unittest

from core.llm_clients.client import (
    screenshot_capability_warnings,
    subscription_auth_warnings,
    tool_capability_warnings,
)


def warn(modes, **cfg):
    base = dict(llm_provider="", llm_model="", vision_provider="", vision_model="")
    base.update(cfg)
    return screenshot_capability_warnings(modes, **base)


class ScreenshotCapabilityWarnings(unittest.TestCase):
    def test_all_off_is_silent(self):
        self.assertEqual(warn(["off", "off"]), [])

    def test_auto_without_vision_model_warns(self):
        w = warn(["auto"], llm_provider="anthropic", llm_model="claude-opus-4-5",
                 vision_provider="", vision_model="")
        self.assertEqual(len(w), 1)
        self.assertIn("Image model", w[0])

    def test_auto_with_vision_capable_model_is_silent(self):
        self.assertEqual(
            warn(["auto"], vision_provider="anthropic", vision_model="claude-opus-4-5"),
            [],
        )

    def test_auto_with_text_only_vision_model_warns(self):
        w = warn(["auto"], vision_provider="groq", vision_model="llama-3.3-70b")
        self.assertEqual(len(w), 1)
        self.assertIn("may not read screenshots", w[0])

    def test_model_mode_anthropic_is_silent(self):
        self.assertEqual(
            warn(["model"], llm_provider="anthropic", llm_model="claude-sonnet-4-5"),
            [],
        )

    def test_model_mode_copilot_warns_unsupported(self):
        w = warn(["model"], llm_provider="copilot", llm_model="gpt-4o")
        self.assertEqual(len(w), 1)
        self.assertIn("do not work", w[0])

    def test_model_mode_openai_vision_capable_main_model_is_silent(self):
        self.assertEqual(
            warn(["model"], llm_provider="openai", llm_model="gpt-4o"),
            [],
        )

    def test_model_mode_groq_text_only_warns(self):
        w = warn(["model"], llm_provider="groq", llm_model="llama-3.3-70b")
        self.assertEqual(len(w), 1)
        self.assertIn("may not read screenshots", w[0])

    def test_model_mode_groq_with_same_provider_vision_model_is_silent(self):
        self.assertEqual(
            warn(["model"], llm_provider="groq", llm_model="llama-3.3-70b",
                 vision_provider="groq", vision_model="llama-3.2-90b-vision"),
            [],
        )

    def test_legacy_and_mixed_modes_dedupe(self):
        # Two callers both on auto with a bad vision model → still one warning.
        w = warn(["auto", "auto"], vision_provider="groq", vision_model="llama-3.3-70b")
        self.assertEqual(len(w), 1)


class ToolCapabilityWarnings(unittest.TestCase):
    def test_tools_off_is_silent(self):
        self.assertEqual(tool_capability_warnings(False, llm_provider="chatgpt"), [])

    def test_tools_on_chatgpt_warns(self):
        w = tool_capability_warnings(True, llm_provider="chatgpt")
        self.assertEqual(len(w), 1)
        self.assertIn("ChatGPT", w[0])

    def test_tools_on_anthropic_is_silent(self):
        self.assertEqual(tool_capability_warnings(True, llm_provider="anthropic"), [])

    def test_tools_on_groq_is_silent(self):
        self.assertEqual(tool_capability_warnings(True, llm_provider="groq"), [])


class SubscriptionAuthWarnings(unittest.TestCase):
    def test_api_key_providers_are_silent(self):
        self.assertEqual(
            subscription_auth_warnings(llm_provider="anthropic", vision_provider="openai"),
            [],
        )

    def test_chatgpt_vision_warns(self):
        w = subscription_auth_warnings(vision_provider="chatgpt")
        self.assertEqual(len(w), 1)
        self.assertIn("Image model", w[0])

    def test_copilot_main_warns(self):
        w = subscription_auth_warnings(llm_provider="copilot")
        self.assertEqual(len(w), 1)
        self.assertIn("Chat model", w[0])

    def test_both_roles_warn_separately(self):
        w = subscription_auth_warnings(llm_provider="chatgpt", vision_provider="copilot")
        self.assertEqual(len(w), 2)


if __name__ == "__main__":
    unittest.main()
