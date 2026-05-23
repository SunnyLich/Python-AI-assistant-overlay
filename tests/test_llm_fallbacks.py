import unittest
from unittest.mock import patch

from core import llm


class LlmFallbackTests(unittest.TestCase):
    def test_route_candidates_dedupes_primary_and_fallbacks(self):
        routes = llm._route_candidates(
            "chatgpt",
            "gpt-5.5",
            "chatgpt:gpt-5.5\nanthropic:claude-sonnet-4-5; openai:gpt-4o",
        )

        self.assertEqual(
            routes,
            [
                ("chatgpt", "gpt-5.5"),
                ("anthropic", "claude-sonnet-4-5"),
                ("openai", "gpt-4o"),
            ],
        )

    def test_stream_with_fallbacks_tries_next_route_before_output(self):
        attempts = []

        def factory(provider, model):
            attempts.append((provider, model))
            if provider == "bad":
                raise RuntimeError("boom")
            yield "ok"

        chunks = list(
            llm._stream_with_fallbacks(
                "query",
                [("bad", "first"), ("good", "second")],
                factory,
            )
        )

        self.assertEqual(chunks, ["ok"])
        self.assertEqual(attempts, [("bad", "first"), ("good", "second")])

    def test_stream_with_fallbacks_does_not_mix_after_output(self):
        def factory(provider, model):
            yield "partial"
            raise RuntimeError("late")

        with self.assertRaises(RuntimeError):
            list(
                llm._stream_with_fallbacks(
                    "query",
                    [("bad", "first"), ("good", "second")],
                    factory,
                )
            )

    def test_google_provider_uses_google_api_key(self):
        with patch.object(llm.config, "GOOGLE_API_KEY", "google-key"):
            self.assertEqual(llm._api_key_for("google"), "google-key")

    def test_dynamic_openai_client_uses_google_base_url(self):
        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        with patch.object(llm.config, "GOOGLE_API_KEY", "google-key"), patch(
            "openai.OpenAI",
            FakeOpenAI,
        ):
            client = llm._dynamic_openai_client("google")

        self.assertEqual(client.kwargs["api_key"], "google-key")
        self.assertEqual(client.kwargs["base_url"], llm._GOOGLE_OPENAI_BASE_URL)

    def test_text_route_probe_uses_openai_compatible_client(self):
        calls = []

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return object()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        with patch("core.llm._check_route_config_with_credentials"), patch("openai.OpenAI", return_value=FakeClient()):
            ok, message = llm.test_route_connection(
                "google",
                "gemini-2.5-flash",
                "LLM",
                google_api_key="google-key",
            )

        self.assertTrue(ok)
        self.assertIn("LLM route OK", message)
        self.assertEqual(calls[0]["model"], "gemini-2.5-flash")
        self.assertFalse(calls[0]["stream"])

    def test_vision_route_probe_uses_test_image(self):
        calls = []

        class FakeMessages:
            def create(self, **kwargs):
                calls.append(kwargs)
                return object()

        class FakeClient:
            messages = FakeMessages()

        fake_anthropic = type("FakeAnthropicModule", (), {"Anthropic": lambda self=None, api_key=None: FakeClient()})()
        with patch("core.llm._check_route_config_with_credentials"), patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            ok, message = llm.test_route_connection(
                "anthropic",
                "claude-sonnet-4-5",
                "VISION_LLM",
                image=True,
                anthropic_api_key="anthropic-key",
            )

        self.assertTrue(ok)
        self.assertIn("vision route OK", message)
        content = calls[0]["messages"][0]["content"]
        self.assertEqual(content[0]["source"]["data"], llm._TEST_IMAGE_BASE64)

    def test_copilot_route_probe_requires_non_empty_response(self):
        with patch("core.copilot_auth.get_token", return_value="github_pat_test"), patch(
            "core.copilot_auth.validate_token_format",
            return_value=(True, "ok"),
        ), patch("core.copilot_client.ask", return_value="OK"):
            ok, message = llm.test_route_connection("copilot", "gpt-4.1", "LLM")

        self.assertTrue(ok)
        self.assertIn("copilot", message)


if __name__ == "__main__":
    unittest.main()
