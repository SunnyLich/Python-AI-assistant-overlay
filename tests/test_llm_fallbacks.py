"""Tests for test llm fallbacks."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.llm_clients import client as llm


class LlmFallbackTests(unittest.TestCase):
    """Test case for llm fallback tests behavior."""
    def setUp(self):
        """Verify set up behavior."""
        llm._route_capabilities.clear()

    def test_route_candidates_dedupes_primary_and_fallbacks(self):
        """Verify route candidates dedupes primary and fallbacks behavior."""
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
        """Verify stream with fallbacks tries next route before output behavior."""
        attempts = []

        def factory(provider, model):
            """Verify factory behavior."""
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

    def test_stream_with_fallbacks_cools_down_no_content_route(self):
        """Verify stream with fallbacks cools down no content route behavior."""
        def factory(provider, model):
            """Verify factory behavior."""
            if provider == "empty":
                return
                yield "unreachable"
            yield "ok"

        llm._route_cooldowns.clear()
        chunks = list(
            llm._stream_with_fallbacks(
                "query",
                [("empty", "first"), ("good", "second")],
                factory,
            )
        )

        self.assertEqual(chunks, ["ok"])
        self.assertTrue(llm._is_route_cooling("empty", "first"))

    def test_stream_with_fallbacks_does_not_mix_after_output(self):
        """Verify stream with fallbacks does not mix after output behavior."""
        def factory(provider, model):
            """Verify factory behavior."""
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

    def test_vision_route_preserves_ambient_and_memory_context(self):
        """Verify vision route preserves ambient and memory context behavior."""
        captured = {}

        def fake_stream_openai(
            user_message,
            image_base64,
            model,
            client,
            ambient_context="",
            memory_context="",
            **_kwargs,
        ):
            """Verify fake stream openai behavior."""
            captured["user_message"] = user_message
            captured["image_base64"] = image_base64
            captured["ambient_context"] = ambient_context
            captured["memory_context"] = memory_context
            yield "ok"

        with patch.object(llm, "_check_route_config"), \
             patch.object(llm, "_normalize_model_for_provider", lambda _provider, model: model), \
             patch.object(llm, "_dynamic_openai_client", return_value=object()), \
             patch.object(llm, "_stream_openai_compat", side_effect=fake_stream_openai):
            chunks = list(
                llm._stream_single_response_route(
                    "openai",
                    "gpt-4o",
                    "What is this?",
                    "image-b64",
                    "[Active document]\nDocument text",
                    "[Session context]\nMemory text",
                    False,
                    "VISION_LLM",
                )
            )

        self.assertEqual(chunks, ["ok"])
        self.assertEqual(captured["image_base64"], "image-b64")
        self.assertIn("Document text", captured["ambient_context"])
        self.assertIn("Memory text", captured["memory_context"])

    def test_codex_vision_input_text_includes_context(self):
        """Verify codex vision input text includes context behavior."""
        captured = {}

        def fake_response_stream(_client, kwargs, **_meta):
            """Verify fake response stream behavior."""
            captured["kwargs"] = kwargs
            yield "ok"

        with patch.object(llm, "_response_stream_text", side_effect=fake_response_stream):
            chunks = list(
                llm._stream_codex_vision(
                    "What is this?",
                    "image-b64",
                    "gpt-5",
                    object(),
                    "[Browser/Web]\nPage text",
                    "[Session context]\nMemory text",
                )
            )

        self.assertEqual(chunks, ["ok"])
        content = captured["kwargs"]["input"][0]["content"]
        input_text = content[0]["text"]
        self.assertIn("Page text", input_text)
        self.assertIn("Memory text", input_text)
        self.assertEqual(content[1]["type"], "input_image")

    def test_active_document_falls_back_to_window_text_when_no_path(self):
        """Verify active document falls back to window text when no path behavior."""
        with patch("core.context_fetcher.get_all_open_document_paths", return_value=[]), \
             patch(
                 "core.context_fetcher.get_all_open_document_window_texts",
                 return_value=[("Notes.txt", "hello from notepad")],
             ):
            text = llm.read_active_document_for_context()

        self.assertEqual(text, "[Notes.txt]\nhello from notepad")

    def test_stream_with_fallbacks_cools_down_transient_503_and_summarizes_failures(self):
        """Verify stream with fallbacks cools down transient 503 and summarizes failures behavior."""
        class TransientError(RuntimeError):
            """Exception raised for transient error failures."""
            status_code = 503

        def factory(provider, model):
            """Verify factory behavior."""
            raise TransientError(f"{provider}/{model} high demand")
            yield "unreachable"

        llm._route_cooldowns.clear()
        with self.assertRaisesRegex(RuntimeError, "All query model routes failed"):
            list(
                llm._stream_with_fallbacks(
                    "query",
                    [("google", "primary"), ("google", "fallback")],
                    factory,
                )
            )

        self.assertTrue(llm._is_route_cooling("google", "primary"))
        self.assertTrue(llm._is_route_cooling("google", "fallback"))

    def test_stream_with_fallbacks_fails_fast_when_all_routes_are_cooling(self):
        """Verify stream with fallbacks fails fast when all routes are cooling behavior."""
        llm._route_cooldowns.clear()
        llm._mark_route_cooling("google", "primary")
        llm._mark_route_cooling("google", "fallback")

        def factory(provider, model):
            """Verify factory behavior."""
            raise AssertionError(f"should not call {provider}/{model}")
            yield "unreachable"

        with self.assertRaisesRegex(RuntimeError, "temporarily cooling down"):
            list(
                llm._stream_with_fallbacks(
                    "query",
                    [("google", "primary"), ("google", "fallback")],
                    factory,
                )
            )

    def test_google_provider_uses_google_api_key(self):
        """Verify google provider uses google api key behavior."""
        with patch.object(llm.config, "GOOGLE_API_KEY", "google-key"):
            self.assertEqual(llm._api_key_for("google"), "google-key")

    def test_capture_screen_uses_provided_supervisor_image(self):
        """Verify capture screen uses provided supervisor image behavior."""
        with patch("core.capture.get_screen_snippet") as capture:
            self.assertEqual(llm._capture_screen_b64("provided-image"), "provided-image")
            capture.assert_not_called()

    def test_capture_screen_failed_precapture_does_not_fallback_to_brain_capture(self):
        """Verify capture screen failed precapture does not fallback to brain capture behavior."""
        with patch("core.capture.get_screen_snippet") as capture:
            self.assertIsNone(llm._capture_screen_b64(""))
            capture.assert_not_called()

    def test_openai_vision_model_skips_configured_vision_route_when_cooling(self):
        """Verify openai vision model skips configured vision route when cooling behavior."""
        llm._route_cooldowns.clear()
        llm._mark_route_cooling("google", "gemini-3.5-flash")

        with patch.object(llm.config, "VISION_LLM_PROVIDER", "google"), \
             patch.object(llm.config, "VISION_LLM_MODEL", "gemini-3.5-flash"):
            model = llm._openai_vision_model("google", "gemma-4-31b-it")

        self.assertEqual(model, "gemma-4-31b-it")

    def test_openai_tool_call_marks_preamble_as_progress_before_final_answer(self):
        """Verify OpenAI-compatible tool preambles are progress, not final answer."""
        first_round_chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="I need a screenshot.", tool_calls=[]),
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(name="capture_screen", arguments="{}"),
                                )
                            ],
                        ),
                    )
                ]
            ),
        ]

        class FakeStream:
            """Test case for fake stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter(first_round_chunks)

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def __init__(self):
                """Initialize the fake completions instance."""
                self.calls = []

            def create(self, **kwargs):
                """Verify create behavior."""
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return FakeStream()
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content="Here is what I can see.", tool_calls=None),
                        )
                    ]
                )

        completions = FakeCompletions()
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        with patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True), \
             patch.object(llm, "_capture_screen_b64", return_value="image-b64"):
            chunks = list(
                llm._stream_openai_compat(
                    "what can you see?",
                    None,
                    "gemini-3.5-flash",
                    fake_client,
                    allow_screenshot_tool=True,
                    provider="google",
                )
            )

        self.assertEqual(chunks, ["I need a screenshot.", "Here is what I can see."])
        self.assertEqual(getattr(chunks[0], "kind", ""), "progress")
        self.assertEqual(getattr(chunks[1], "kind", "answer"), "answer")

    def test_openai_followup_tool_call_marks_intermediate_text_as_progress(self):
        """Verify follow-up text with another tool call is progress, not final."""
        first_round_chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="read_file",
                                        arguments='{"path":"hello_world.py"}',
                                    ),
                                )
                            ],
                        ),
                    )
                ]
            )
        ]

        class FakeStream:
            """Fake first-round stream."""
            def __enter__(self):
                """Enter fake stream."""
                return iter(first_round_chunks)

            def __exit__(self, exc_type, exc, tb):
                """Exit fake stream."""
                return False

        class FakeCompletions:
            """Fake chat completions API."""
            def __init__(self):
                """Initialize fake completions."""
                self.calls = []

            def create(self, **kwargs):
                """Record request and return staged responses."""
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return FakeStream()
                if len(self.calls) == 2:
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="tool_calls",
                                message=SimpleNamespace(
                                    content="hello_world.py contains print('Hello, world!').",
                                    tool_calls=[
                                        SimpleNamespace(
                                            id="call_2",
                                            function=SimpleNamespace(
                                                name="edit_file",
                                                arguments=(
                                                    '{"path":"hello_world.py",'
                                                    '"old":"print(\\"Hello, world!\\")",'
                                                    '"new":"# Say hello\\nprint(\\"Hello, world!\\")"}'
                                                ),
                                            ),
                                        )
                                    ],
                                ),
                            )
                        ]
                    )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content="Updated hello_world.py.", tool_calls=None),
                        )
                    ]
                )

        completions = FakeCompletions()
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        with (
            patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True),
            patch.object(llm, "_execute_model_tool", return_value="ok"),
        ):
            chunks = list(
                llm._stream_openai_compat(
                    "add a comment to hello_world.py",
                    None,
                    "gpt-4o",
                    fake_client,
                    use_tools=True,
                    allowed_tools=["read_file", "edit_file"],
                    pinned_tools=["read_file", "edit_file"],
                    provider="openai",
                )
            )

        self.assertEqual(chunks, ["hello_world.py contains print('Hello, world!').", "Updated hello_world.py."])
        self.assertEqual(getattr(chunks[0], "kind", ""), "progress")
        self.assertEqual(getattr(chunks[1], "kind", "answer"), "answer")
        self.assertEqual(len(completions.calls), 3)

    def test_openai_file_edit_continues_when_followup_stops_after_read(self):
        """Verify OpenAI-compatible file edits do not stop after only reading."""
        outer_self = self
        first_round_chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="tool_calls",
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    function=SimpleNamespace(
                                        name="read_file",
                                        arguments='{"path":"hello_world.py"}',
                                    ),
                                )
                            ],
                        ),
                    )
                ]
            )
        ]

        class FakeStream:
            """Fake first-round stream."""
            def __enter__(self):
                """Enter fake stream."""
                return iter(first_round_chunks)

            def __exit__(self, exc_type, exc, tb):
                """Exit fake stream."""
                return False

        class FakeCompletions:
            """Fake chat completions API."""
            def __init__(self):
                """Initialize fake completions."""
                self.calls = []

            def create(self, **kwargs):
                """Record request and return staged responses."""
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return FakeStream()
                if len(self.calls) == 2:
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                message=SimpleNamespace(
                                    content="hello_world.py contains print('Hello, world!').",
                                    tool_calls=None,
                                ),
                            )
                        ]
                )
                if len(self.calls) == 3:
                    outer_self.assertIn("only gathered file context", str(kwargs.get("messages")))
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="tool_calls",
                                message=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            id="call_2",
                                            function=SimpleNamespace(
                                                name="edit_file",
                                                arguments=(
                                                    '{"path":"hello_world.py",'
                                                    '"old":"print(\\"Hello, world!\\")",'
                                                    '"new":"# Say hello\\nprint(\\"Hello, world!\\")"}'
                                                ),
                                            ),
                                        )
                                    ],
                                ),
                            )
                        ]
                    )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content="Updated hello_world.py.", tool_calls=None),
                        )
                    ]
                )

        completions = FakeCompletions()
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        with (
            patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True),
            patch.object(llm, "_execute_model_tool", return_value="ok"),
        ):
            chunks = list(
                llm._stream_openai_compat(
                    "add a comment to hello_world.py",
                    None,
                    "gpt-4o",
                    fake_client,
                    use_tools=True,
                    allowed_tools=["read_file", "edit_file"],
                    pinned_tools=["read_file", "edit_file"],
                    provider="openai",
                )
            )

        self.assertEqual(chunks, ["Updated hello_world.py."])
        self.assertEqual(len(completions.calls), 4)

    def test_openai_text_screenshot_request_continues_with_implicit_tool_call(self):
        """Verify openai text screenshot request continues with implicit tool call behavior."""
        first_round_chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        delta=SimpleNamespace(
                            content="I need to use the screenshot tool to answer this.",
                            tool_calls=[],
                        ),
                    )
                ]
            )
        ]

        class FakeStream:
            """Test case for fake stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter(first_round_chunks)

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def __init__(self):
                """Initialize the fake completions instance."""
                self.calls = []

            def create(self, **kwargs):
                """Verify create behavior."""
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return FakeStream()
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content="I can see the Wisp window.", tool_calls=None),
                        )
                    ]
                )

        completions = FakeCompletions()
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        with patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True), \
             patch.object(llm, "_capture_screen_b64", return_value="image-b64"):
            chunks = list(
                llm._stream_openai_compat(
                    "what can you see?",
                    None,
                    "gemini-3.5-flash",
                    fake_client,
                    allow_screenshot_tool=True,
                    provider="google",
                )
            )

        self.assertEqual(chunks, [
            "I need to use the screenshot tool to answer this.",
            "I can see the Wisp window.",
        ])
        self.assertEqual(getattr(chunks[0], "kind", ""), "progress")
        self.assertEqual(getattr(chunks[1], "kind", "answer"), "answer")
        follow_up_messages = completions.calls[1]["messages"]
        self.assertEqual(follow_up_messages[-2]["role"], "tool")
        self.assertEqual(follow_up_messages[-1]["role"], "user")
        self.assertEqual(follow_up_messages[-1]["content"][1]["type"], "image_url")

    def test_anthropic_tool_call_marks_preamble_as_progress_before_final_answer(self):
        """Verify Anthropic tool preambles are progress, not final answer."""
        first_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="capture_screen",
                    id="tool_1",
                    input={},
                )
            ],
        )

        class FakeStream:
            """Test case for fake stream behavior."""
            text_stream = ["I need a screenshot."]

            def __enter__(self):
                """Enter the context manager."""
                return self

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

            def get_final_message(self):
                """Verify get final message behavior."""
                return first_response

        class FakeMessages:
            """Test case for fake messages behavior."""
            def stream(self, **kwargs):
                """Verify stream behavior."""
                calls.append(("stream", kwargs))
                return FakeStream()

            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(("create", kwargs))
                return SimpleNamespace(
                    stop_reason="stop",
                    content=[SimpleNamespace(type="text", text="Here is what I can see.")],
                )

        calls = []
        fake_client = SimpleNamespace(messages=FakeMessages())

        with patch.object(llm, "_capture_screen_b64", return_value="image-b64"):
            chunks = list(
                llm._stream_anthropic(
                    "what can you see?",
                    None,
                    "claude-sonnet-4-5",
                    fake_client,
                    allow_screenshot_tool=True,
                )
            )

        self.assertEqual(chunks, ["I need a screenshot.", "Here is what I can see."])
        self.assertEqual(getattr(chunks[0], "kind", ""), "progress")
        self.assertEqual(getattr(chunks[1], "kind", "answer"), "answer")
        self.assertEqual(calls[0][1]["max_tokens"], llm._QUERY_DEFAULT_MAX_TOKENS)
        self.assertEqual(calls[1][1]["max_tokens"], llm._QUERY_DEFAULT_MAX_TOKENS)

    def test_dynamic_openai_client_uses_google_base_url(self):
        """Verify dynamic openai client uses google base url behavior."""
        class FakeOpenAI:
            """Test case for fake open a i behavior."""
            def __init__(self, **kwargs):
                """Initialize the fake open a i instance."""
                self.kwargs = kwargs

        with patch.object(llm.config, "GOOGLE_API_KEY", "google-key"), patch(
            "openai.OpenAI",
            FakeOpenAI,
        ):
            client = llm._dynamic_openai_client("google")

        self.assertEqual(client.kwargs["api_key"], "google-key")
        self.assertEqual(client.kwargs["base_url"], llm._GOOGLE_OPENAI_BASE_URL)

    def test_openai_compatible_provider_base_urls_match_official_docs(self):
        """Verify openai compatible provider base urls match official docs behavior."""
        expected = {
            "deepseek": "https://api.deepseek.com",
            "together": "https://api.together.ai/v1",
        }

        for provider, base_url in expected.items():
            with self.subTest(provider=provider):
                self.assertEqual(llm._OPENAI_COMPAT_PROVIDERS[provider][1], base_url)

    def test_openrouter_credential_probe_uses_provider_base_url(self):
        """Verify openrouter credential probe uses provider base url behavior."""
        calls = []

        def fake_openai_client(**kwargs):
            """Verify fake openai client behavior."""
            calls.append(kwargs)
            return object()

        with patch.object(llm.sdk_clients, "openai_client", fake_openai_client), \
             patch.object(llm, "_run_openai_compat_probe"):
            llm._probe_openai_compat_route_with_credentials(
                "openrouter",
                "deepseek/deepseek-v4-flash",
                api_key="sk-or-v1-test",
            )

        self.assertEqual(
            calls,
            [{"api_key": "sk-or-v1-test", "base_url": llm._OPENROUTER_BASE_URL}],
        )

    def test_openrouter_route_test_reports_missing_explicit_key_before_probe(self):
        """Verify route tests catch a missing OpenRouter key before probing."""
        with patch.object(llm, "_probe_openai_compat_route_with_credentials") as probe:
            ok, message = llm.test_route_connection(
                "openrouter",
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "LLM",
                compat_keys={"openrouter": ""},
            )

        self.assertFalse(ok)
        self.assertIn("API key is not configured", message)
        probe.assert_not_called()

    def test_text_route_probe_uses_openai_compatible_client(self):
        """Verify text route probe uses openai compatible client behavior."""
        calls = []

        class FakeStream:
            """Test case for fake stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([object()])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                return FakeStream()

        class FakeChat:
            """Test case for fake chat behavior."""
            completions = FakeCompletions()

        class FakeClient:
            """Client for fake client communication."""
            chat = FakeChat()

        with patch("core.llm_clients.client._check_route_config_with_credentials"), patch("openai.OpenAI", return_value=FakeClient()):
            ok, message = llm.test_route_connection(
                "google",
                "gemini-2.5-flash",
                "LLM",
                compat_keys={"google": "google-key"},
            )

        self.assertTrue(ok)
        self.assertIn("LLM route OK", message)
        self.assertEqual(calls[0]["model"], "gemini-2.5-flash")
        self.assertTrue(calls[0]["stream"])

    def test_text_route_probe_retries_non_streaming_when_stream_rejected(self):
        """Verify text route probe retries non streaming when stream rejected behavior."""
        calls = []

        class StreamModeError(RuntimeError):
            """Exception raised for stream mode error failures."""
            status_code = 400

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                if kwargs.get("stream"):
                    raise StreamModeError("stream is not supported for this model")
                return SimpleNamespace(choices=[])

        class FakeChat:
            """Test case for fake chat behavior."""
            completions = FakeCompletions()

        class FakeClient:
            """Client for fake client communication."""
            chat = FakeChat()

        with patch("core.llm_clients.client._check_route_config_with_credentials"), patch("openai.OpenAI", return_value=FakeClient()):
            ok, message = llm.test_route_connection(
                "google",
                "gemini-2.5-flash",
                "LLM",
                compat_keys={"google": "google-key"},
            )

        self.assertTrue(ok)
        self.assertIn("LLM route OK", message)
        self.assertEqual([call["stream"] for call in calls], [True, False])

    def test_openai_compat_runtime_retries_non_streaming_when_stream_rejected(self):
        """Verify openai compat runtime retries non streaming when stream rejected behavior."""
        calls = []

        class StreamModeError(RuntimeError):
            """Exception raised for stream mode error failures."""
            status_code = 400

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                if kwargs.get("stream"):
                    raise StreamModeError("stream is not supported for this model")
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            message=SimpleNamespace(content="non-stream reply", tool_calls=None),
                        )
                    ]
                )

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        chunks = list(
            llm._stream_openai_compat(
                "hi",
                None,
                "model",
                fake_client,
                provider="google",
            )
        )

        self.assertEqual(chunks, ["non-stream reply"])
        self.assertEqual([call["stream"] for call in calls], [True, False])
        self.assertFalse(llm._get_route_capabilities("google", "model").supports_stream)

    def test_request_specific_stream_false_error_does_not_disable_route_streaming(self):
        """Verify request specific stream false error does not disable route streaming behavior."""
        calls = []

        class StreamFalseRequiredError(RuntimeError):
            """Exception raised when one request shape must be non-streaming."""
            status_code = 400

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                if kwargs.get("stream"):
                    raise StreamFalseRequiredError(
                        "json_object is not compatible with streaming - stream must be false"
                    )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="json reply", tool_calls=None),
                            finish_reason="stop",
                        )
                    ]
                )

        class FakeChat:
            """Test case for fake chat behavior."""
            completions = FakeCompletions()

        fake_client = SimpleNamespace(chat=FakeChat())

        chunks = list(
            llm._stream_openai_compat(
                "hi",
                None,
                "gpt-oss-120b",
                fake_client,
                "",
                "",
                use_tools=False,
                provider="cerebras",
                json_mode=True,
            )
        )

        self.assertEqual(chunks, ["json reply"])
        self.assertEqual([call["stream"] for call in calls], [True, False])
        self.assertIsNot(llm._get_route_capabilities("cerebras", "gpt-oss-120b").supports_stream, False)

    def test_openai_compat_defaults_to_single_tool_call_without_parallel_param(self):
        """Verify openai compat defaults to single tool call without parallel param behavior."""
        calls = []

        class FakeStream:
            """Test case for fake stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(content="ok", tool_calls=[]),
                            )
                        ]
                    )
                ])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                return FakeStream()

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        with patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True):
            chunks = list(
                llm._stream_openai_compat(
                    "git status",
                    None,
                    "model",
                    fake_client,
                    use_tools=True,
                    allowed_tools=["git_status"],
                    provider="google",
                )
            )

        self.assertEqual(chunks, ["ok"])
        self.assertIn("tools", calls[0])
        self.assertNotIn("parallel_tool_calls", calls[0])

    def test_openai_compat_parallel_tool_param_is_removed_without_disabling_tools(self):
        """Verify openai compat parallel tool param is removed without disabling tools behavior."""
        calls = []

        class UnsupportedParameterError(RuntimeError):
            """Exception raised for unsupported parameter error failures."""
            status_code = 400

        class FakeStream:
            """Test case for fake stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(content="ok", tool_calls=[]),
                            )
                        ]
                    )
                ])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                if "parallel_tool_calls" in kwargs:
                    raise UnsupportedParameterError("Unsupported parameter: 'parallel_tool_calls'")
                return FakeStream()

        llm._update_route_capabilities("google", "model", supports_parallel_tools=True)
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        with patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True):
            chunks = list(
                llm._stream_openai_compat(
                    "git status",
                    None,
                    "model",
                    fake_client,
                    use_tools=True,
                    allowed_tools=["git_status"],
                    provider="google",
                )
            )

        self.assertEqual(chunks, ["ok"])
        self.assertIn("tools", calls[0])
        self.assertIn("parallel_tool_calls", calls[0])
        self.assertIn("tools", calls[1])
        self.assertNotIn("parallel_tool_calls", calls[1])
        self.assertFalse(llm._get_route_capabilities("google", "model").supports_parallel_tools)

    def test_openai_compat_tools_unsupported_downgrades_to_frontloaded_context(self):
        """Verify openai compat tools unsupported downgrades to frontloaded context behavior."""
        calls = []

        class ToolUnsupportedError(RuntimeError):
            """Exception raised for tool unsupported error failures."""
            status_code = 400

        class FakeStream:
            """Test case for fake stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(content="ok", tool_calls=[]),
                            )
                        ]
                    )
                ])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                if "tools" in kwargs:
                    raise ToolUnsupportedError("tools are not supported for this model")
                return FakeStream()

        llm._route_capabilities.clear()
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        with patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True), \
             patch.object(llm, "read_active_document_for_context", return_value="Document text"):
            chunks = list(
                llm._stream_openai_compat(
                    "summarize this",
                    None,
                    "model",
                    fake_client,
                    use_tools=True,
                    allowed_tools=["get_context.documents"],
                    provider="google",
                )
            )

        self.assertEqual(chunks, ["ok"])
        self.assertIn("tools", calls[0])
        self.assertNotIn("tools", calls[1])
        self.assertIn("Document text", calls[1]["messages"][0]["content"])
        self.assertFalse(llm._get_route_capabilities("google", "model").supports_tools)

    def test_chatgpt_route_probe_retries_streaming_when_create_rejected(self):
        """Verify chatgpt route probe retries streaming when create rejected behavior."""
        calls = []

        class StreamRequiredError(RuntimeError):
            """Exception raised for stream required error failures."""
            status_code = 400

        class FakeResponseStream:
            """Test case for fake response stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeResponses:
            """Test case for fake responses behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(("create", kwargs))
                raise StreamRequiredError("stream must be true for this model")

            def stream(self, **kwargs):
                """Verify stream behavior."""
                calls.append(("stream", kwargs))
                return FakeResponseStream()

        with patch.object(llm, "_get_codex_client", return_value=SimpleNamespace(responses=FakeResponses())):
            llm._probe_chatgpt_route("gpt-5.5")

        self.assertEqual([kind for kind, _kwargs in calls], ["create", "stream"])

    def test_chatgpt_route_probe_retries_without_unsupported_max_output_tokens(self):
        """Verify chatgpt route probe retries without unsupported max output tokens behavior."""
        calls = []

        class UnsupportedParameterError(RuntimeError):
            """Exception raised for unsupported parameter error failures."""
            status_code = 400

        class FakeResponses:
            """Test case for fake responses behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                if "max_output_tokens" in kwargs:
                    raise UnsupportedParameterError(
                        "Error code: 400 - {'detail': 'Unsupported parameter: max_output_tokens'}"
                    )
                return SimpleNamespace(output_text="OK")

        with patch.object(llm, "_get_codex_client", return_value=SimpleNamespace(responses=FakeResponses())):
            llm._probe_chatgpt_route("gpt-5.5")

        self.assertEqual(len(calls), 2)
        self.assertIn("max_output_tokens", calls[0])
        self.assertNotIn("max_output_tokens", calls[1])

    def test_chatgpt_route_probe_stream_retry_strips_unsupported_max_output_tokens(self):
        """Verify chatgpt route probe stream retry strips unsupported max output tokens behavior."""
        calls = []

        class StreamRequiredError(RuntimeError):
            """Exception raised for stream required error failures."""
            status_code = 400

        class UnsupportedParameterError(RuntimeError):
            """Exception raised for unsupported parameter error failures."""
            status_code = 400

        class FakeResponseStream:
            """Test case for fake response stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeResponses:
            """Test case for fake responses behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(("create", kwargs))
                raise StreamRequiredError("stream must be true for this model")

            def stream(self, **kwargs):
                """Verify stream behavior."""
                calls.append(("stream", kwargs))
                if "max_output_tokens" in kwargs:
                    raise UnsupportedParameterError(
                        "Error code: 400 - {'detail': 'Unsupported parameter: max_output_tokens'}"
                    )
                return FakeResponseStream()

        with patch.object(llm, "_get_codex_client", return_value=SimpleNamespace(responses=FakeResponses())):
            llm._probe_chatgpt_route("gpt-5.5")

        self.assertEqual([kind for kind, _kwargs in calls], ["create", "stream", "stream"])
        self.assertIn("max_output_tokens", calls[1][1])
        self.assertNotIn("max_output_tokens", calls[2][1])

    def test_chatgpt_runtime_retries_create_when_stream_rejected(self):
        """Verify chatgpt runtime retries create when stream rejected behavior."""
        calls = []

        class StreamModeError(RuntimeError):
            """Exception raised for stream mode error failures."""
            status_code = 400

        class FakeResponses:
            """Test case for fake responses behavior."""
            def stream(self, **kwargs):
                """Verify stream behavior."""
                calls.append(("stream", kwargs))
                raise StreamModeError("streaming is not supported for this model")

            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(("create", kwargs))
                return SimpleNamespace(output_text="created reply")

        chunks = list(llm._stream_codex("hi", "gpt-5.5", SimpleNamespace(responses=FakeResponses())))

        self.assertEqual(chunks, ["created reply"])
        self.assertEqual([kind for kind, _kwargs in calls], ["stream", "create"])
        self.assertFalse(llm._get_route_capabilities("chatgpt", "gpt-5.5").supports_stream)

    def test_chatgpt_runtime_accepts_allowed_tools_allowlist(self):
        """Verify chatgpt runtime accepts allowed tools allowlist behavior."""
        calls = []

        class FakeResponseStream:
            """Test case for fake response stream behavior."""
            def __enter__(self):
                """Enter the context manager."""
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class FakeResponses:
            """Test case for fake responses behavior."""
            def stream(self, **kwargs):
                """Verify stream behavior."""
                calls.append(kwargs)
                return FakeResponseStream()

        chunks = list(
            llm._stream_codex(
                "hi",
                "gpt-5.5",
                SimpleNamespace(responses=FakeResponses()),
                use_tools=True,
                allowed_tools=[],
            )
        )

        self.assertEqual(chunks, ["OK"])
        self.assertEqual(len(calls), 1)

    def test_chatgpt_history_route_uses_allowed_tools(self):
        """Verify ChatGPT chat history route can offer granted file tools."""
        calls = []

        class FakeResponses:
            """Fake Responses API for a no-tool-call response."""
            def create(self, **kwargs):
                """Record the request and return final text."""
                calls.append(kwargs)
                return SimpleNamespace(id="resp_1", output_text="done", output=[])

        with (
            patch.object(llm, "_check_route_config", return_value=None),
            patch.object(llm, "_get_codex_client", return_value=SimpleNamespace(responses=FakeResponses())),
        ):
            chunks = list(
                llm._stream_single_history_route(
                    "chatgpt",
                    "gpt-5.5",
                    [
                        {"role": "system", "content": "Be useful."},
                        {"role": "user", "content": "write a file"},
                    ],
                    use_tools=True,
                    allowed_tools=["write_file"],
                    pinned_tools=["write_file"],
                )
            )

        self.assertEqual(chunks, ["done"])
        self.assertEqual(len(calls), 1)
        self.assertIn("tools", calls[0])
        self.assertEqual([tool["name"] for tool in calls[0]["tools"]], ["write_file"])
        self.assertIn("Be useful.", calls[0]["instructions"])

    def test_openai_compat_history_route_uses_allowed_tools(self):
        """Verify OpenAI-compatible chat history routes can offer file tools."""
        calls = []

        class FakeStream:
            """Fake streaming response."""
            def __enter__(self):
                """Enter fake stream."""
                return iter([
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                delta=SimpleNamespace(content="done", tool_calls=[]),
                            )
                        ]
                    )
                ])

            def __exit__(self, exc_type, exc, tb):
                """Exit fake stream."""
                return False

        class FakeCompletions:
            """Fake chat completions API."""
            def create(self, **kwargs):
                """Record request and return fake stream."""
                calls.append(kwargs)
                return FakeStream()

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        with (
            patch.object(llm, "_check_route_config", return_value=None),
            patch.object(llm, "_dynamic_openai_client", return_value=fake_client),
            patch.object(llm.macos_safety, "openai_compat_tools_enabled", return_value=True),
        ):
            chunks = list(
                llm._stream_single_history_route(
                    "openai",
                    "gpt-4o",
                    [
                        {"role": "user", "content": "previous"},
                        {"role": "assistant", "content": "ok"},
                        {"role": "system", "content": "Chat rules."},
                        {"role": "user", "content": "create hello_world.py"},
                    ],
                    use_tools=True,
                    allowed_tools=["create_file"],
                    pinned_tools=[],
                )
            )

        self.assertEqual(chunks, ["done"])
        self.assertEqual(len(calls), 1)
        self.assertIn("tools", calls[0])
        self.assertEqual(
            [tool["function"]["name"] for tool in calls[0]["tools"]],
            ["create_file"],
        )
        self.assertIn("Chat rules.", calls[0]["messages"][0]["content"])
        self.assertEqual(calls[0]["temperature"], 0.7)
        self.assertEqual(calls[0]["max_tokens"], llm._CHAT_DEFAULT_MAX_TOKENS)
        self.assertEqual(
            [message["role"] for message in calls[0]["messages"][1:]],
            ["user", "assistant", "user"],
        )

    def test_copilot_history_route_preserves_history_and_system(self):
        """Verify Copilot chat history uses the shared route without losing turns."""
        calls = []

        def fake_stream(prompt, model, *, system="", session_id=None, allow_tools=True):
            """Record Copilot stream arguments."""
            calls.append({
                "prompt": prompt,
                "model": model,
                "system": system,
                "session_id": session_id,
                "allow_tools": allow_tools,
            })
            yield "done"

        with (
            patch.object(llm, "_check_route_config", return_value=None),
            patch("core.auth.copilot_client.stream", fake_stream),
        ):
            chunks = list(
                llm._stream_single_history_route(
                    "copilot",
                    "gpt-4.1",
                    [
                        {"role": "system", "content": "Chat rules."},
                        {"role": "user", "content": "previous"},
                        {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": "now"},
                    ],
                    use_tools=True,
                    allowed_tools=["create_file"],
                )
            )

        self.assertEqual(chunks, ["done"])
        self.assertEqual(calls[0]["system"], "Chat rules.")
        self.assertIn("User: previous", calls[0]["prompt"])
        self.assertIn("Assistant: ok", calls[0]["prompt"])
        self.assertTrue(calls[0]["prompt"].endswith("now"))
        self.assertEqual(calls[0]["session_id"], "wisp-chat")
        self.assertFalse(calls[0]["allow_tools"])

    def test_macos_openai_compat_query_uses_non_streaming_safe_mode(self):
        """Verify macos openai compat query uses non streaming safe mode behavior."""
        calls = []

        class FakeMessage:
            """Test case for fake message behavior."""
            content = "hello"

        class FakeChoice:
            """Test case for fake choice behavior."""
            message = FakeMessage()

        class FakeResponse:
            """Test case for fake response behavior."""
            choices = [FakeChoice()]

        class FakeCompletions:
            """Test case for fake completions behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                return FakeResponse()

        class FakeChat:
            """Test case for fake chat behavior."""
            completions = FakeCompletions()

        class FakeClient:
            """Client for fake client communication."""
            chat = FakeChat()

        stdlib_calls = []

        def fake_stdlib(provider, kwargs):
            """Verify fake stdlib behavior."""
            stdlib_calls.append((provider, kwargs))
            return "hello"

        with patch.object(llm.macos_safety.sys, "platform", "darwin"), \
             patch.dict(llm.macos_safety.os.environ, {}, clear=True), \
             patch.object(llm, "_openai_compat_stdlib_completion_text", side_effect=fake_stdlib):
            chunks = list(
                llm._stream_openai_compat(
                    "hi",
                    None,
                    "gpt-4o",
                    FakeClient(),
                    use_tools=True,
                    provider="openai",
                )
            )

        self.assertEqual(chunks, ["hello"])
        self.assertEqual(calls, [])
        self.assertEqual(stdlib_calls[0][0], "openai")
        self.assertEqual(stdlib_calls[0][1]["stream"], False)
        self.assertEqual(stdlib_calls[0][1]["max_tokens"], llm._QUERY_DEFAULT_MAX_TOKENS)
        self.assertNotIn("tools", stdlib_calls[0][1])

    def test_macos_openai_compat_route_skips_sdk_client_construction(self):
        """Verify macos openai compat route skips sdk client construction behavior."""
        with patch.object(llm.macos_safety.sys, "platform", "darwin"), \
             patch.dict(llm.macos_safety.os.environ, {}, clear=True), \
             patch.object(llm, "_check_route_config"), \
             patch.object(llm, "_dynamic_openai_client",
                          side_effect=AssertionError("no SDK client in macOS safe mode")), \
             patch.object(llm, "_openai_compat_stdlib_completion_text", return_value="ok"):
            chunks = list(
                llm._stream_single_response_route(
                    "google",
                    "gemini-3.5-flash",
                    "hi",
                    None,
                    "",
                    "",
                    use_tools=False,
                    route_name="LLM",
                )
            )

        self.assertEqual(chunks, ["ok"])

    def test_vision_route_probe_uses_test_image(self):
        """Verify vision route probe uses test image behavior."""
        calls = []

        class FakeMessages:
            """Test case for fake messages behavior."""
            def create(self, **kwargs):
                """Verify create behavior."""
                calls.append(kwargs)
                return object()

        class FakeClient:
            """Client for fake client communication."""
            messages = FakeMessages()

        fake_anthropic = type("FakeAnthropicModule", (), {"Anthropic": lambda self=None, api_key=None: FakeClient()})()
        with patch("core.llm_clients.client._check_route_config_with_credentials"), patch.dict("sys.modules", {"anthropic": fake_anthropic}):
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
        """Verify copilot route probe requires non empty response behavior."""
        with patch("core.auth.copilot_auth.get_token", return_value="github_pat_test"), patch(
            "core.auth.copilot_auth.validate_token_format",
            return_value=(True, "ok"),
        ), patch("core.auth.copilot_client.ask", return_value="OK"):
            ok, message = llm.test_route_connection("copilot", "gpt-4.1", "LLM")

        self.assertTrue(ok)
        self.assertIn("copilot", message)


if __name__ == "__main__":
    unittest.main()
