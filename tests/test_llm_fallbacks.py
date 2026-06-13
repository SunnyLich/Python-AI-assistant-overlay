import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.llm_clients import client as llm


class LlmFallbackTests(unittest.TestCase):
    def setUp(self):
        llm._route_capabilities.clear()

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

    def test_stream_with_fallbacks_cools_down_no_content_route(self):
        def factory(provider, model):
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

    def test_vision_route_preserves_ambient_and_memory_context(self):
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
        captured = {}

        def fake_response_stream(_client, kwargs, **_meta):
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
        with patch("core.context_fetcher.get_all_open_document_paths", return_value=[]), \
             patch(
                 "core.context_fetcher.get_all_open_document_window_texts",
                 return_value=[("Notes.txt", "hello from notepad")],
             ):
            text = llm.read_active_document_for_context()

        self.assertEqual(text, "[Notes.txt]\nhello from notepad")

    def test_stream_with_fallbacks_cools_down_transient_503_and_summarizes_failures(self):
        class TransientError(RuntimeError):
            status_code = 503

        def factory(provider, model):
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
        llm._route_cooldowns.clear()
        llm._mark_route_cooling("google", "primary")
        llm._mark_route_cooling("google", "fallback")

        def factory(provider, model):
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
        with patch.object(llm.config, "GOOGLE_API_KEY", "google-key"):
            self.assertEqual(llm._api_key_for("google"), "google-key")

    def test_capture_screen_uses_provided_supervisor_image(self):
        with patch("core.capture.get_screen_snippet") as capture:
            self.assertEqual(llm._capture_screen_b64("provided-image"), "provided-image")
            capture.assert_not_called()

    def test_capture_screen_failed_precapture_does_not_fallback_to_brain_capture(self):
        with patch("core.capture.get_screen_snippet") as capture:
            self.assertIsNone(llm._capture_screen_b64(""))
            capture.assert_not_called()

    def test_openai_vision_model_skips_configured_vision_route_when_cooling(self):
        llm._route_cooldowns.clear()
        llm._mark_route_cooling("google", "gemini-3.5-flash")

        with patch.object(llm.config, "VISION_LLM_PROVIDER", "google"), \
             patch.object(llm.config, "VISION_LLM_MODEL", "gemini-3.5-flash"):
            model = llm._openai_vision_model("google", "gemma-4-31b-it")

        self.assertEqual(model, "gemma-4-31b-it")

    def test_openai_tool_call_suppresses_preamble_until_final_answer(self):
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
            def __enter__(self):
                return iter(first_round_chunks)

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
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

        self.assertEqual(chunks, ["Here is what I can see."])

    def test_openai_text_screenshot_request_continues_with_implicit_tool_call(self):
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
            def __enter__(self):
                return iter(first_round_chunks)

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeCompletions:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
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

        self.assertEqual(chunks, ["I can see the Wisp window."])
        follow_up_messages = completions.calls[1]["messages"]
        self.assertEqual(follow_up_messages[-2]["role"], "tool")
        self.assertEqual(follow_up_messages[-1]["role"], "user")
        self.assertEqual(follow_up_messages[-1]["content"][1]["type"], "image_url")

    def test_anthropic_tool_call_suppresses_preamble_until_final_answer(self):
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
            text_stream = ["I need a screenshot."]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get_final_message(self):
                return first_response

        class FakeMessages:
            def stream(self, **kwargs):
                return FakeStream()

            def create(self, **kwargs):
                return SimpleNamespace(
                    stop_reason="stop",
                    content=[SimpleNamespace(type="text", text="Here is what I can see.")],
                )

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

        self.assertEqual(chunks, ["Here is what I can see."])

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

        class FakeStream:
            def __enter__(self):
                return iter([object()])

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return FakeStream()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
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
        calls = []

        class StreamModeError(RuntimeError):
            status_code = 400

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                if kwargs.get("stream"):
                    raise StreamModeError("stream is not supported for this model")
                return SimpleNamespace(choices=[])

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
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
        calls = []

        class StreamModeError(RuntimeError):
            status_code = 400

        class FakeCompletions:
            def create(self, **kwargs):
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

    def test_openai_compat_defaults_to_single_tool_call_without_parallel_param(self):
        calls = []

        class FakeStream:
            def __enter__(self):
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
                return False

        class FakeCompletions:
            def create(self, **kwargs):
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
        calls = []

        class UnsupportedParameterError(RuntimeError):
            status_code = 400

        class FakeStream:
            def __enter__(self):
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
                return False

        class FakeCompletions:
            def create(self, **kwargs):
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
        calls = []

        class ToolUnsupportedError(RuntimeError):
            status_code = 400

        class FakeStream:
            def __enter__(self):
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
                return False

        class FakeCompletions:
            def create(self, **kwargs):
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
        calls = []

        class StreamRequiredError(RuntimeError):
            status_code = 400

        class FakeResponseStream:
            def __enter__(self):
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeResponses:
            def create(self, **kwargs):
                calls.append(("create", kwargs))
                raise StreamRequiredError("stream must be true for this model")

            def stream(self, **kwargs):
                calls.append(("stream", kwargs))
                return FakeResponseStream()

        with patch.object(llm, "_get_codex_client", return_value=SimpleNamespace(responses=FakeResponses())):
            llm._probe_chatgpt_route("gpt-5.5")

        self.assertEqual([kind for kind, _kwargs in calls], ["create", "stream"])

    def test_chatgpt_route_probe_retries_without_unsupported_max_output_tokens(self):
        calls = []

        class UnsupportedParameterError(RuntimeError):
            status_code = 400

        class FakeResponses:
            def create(self, **kwargs):
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
        calls = []

        class StreamRequiredError(RuntimeError):
            status_code = 400

        class UnsupportedParameterError(RuntimeError):
            status_code = 400

        class FakeResponseStream:
            def __enter__(self):
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeResponses:
            def create(self, **kwargs):
                calls.append(("create", kwargs))
                raise StreamRequiredError("stream must be true for this model")

            def stream(self, **kwargs):
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
        calls = []

        class StreamModeError(RuntimeError):
            status_code = 400

        class FakeResponses:
            def stream(self, **kwargs):
                calls.append(("stream", kwargs))
                raise StreamModeError("streaming is not supported for this model")

            def create(self, **kwargs):
                calls.append(("create", kwargs))
                return SimpleNamespace(output_text="created reply")

        chunks = list(llm._stream_codex("hi", "gpt-5.5", SimpleNamespace(responses=FakeResponses())))

        self.assertEqual(chunks, ["created reply"])
        self.assertEqual([kind for kind, _kwargs in calls], ["stream", "create"])
        self.assertFalse(llm._get_route_capabilities("chatgpt", "gpt-5.5").supports_stream)

    def test_chatgpt_runtime_accepts_allowed_tools_allowlist(self):
        calls = []

        class FakeResponseStream:
            def __enter__(self):
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeResponses:
            def stream(self, **kwargs):
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

    def test_macos_openai_compat_query_uses_non_streaming_safe_mode(self):
        calls = []

        class FakeMessage:
            content = "hello"

        class FakeChoice:
            message = FakeMessage()

        class FakeResponse:
            choices = [FakeChoice()]

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs)
                return FakeResponse()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        stdlib_calls = []

        def fake_stdlib(provider, kwargs):
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
        self.assertNotIn("tools", stdlib_calls[0][1])

    def test_macos_openai_compat_route_skips_sdk_client_construction(self):
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
        calls = []

        class FakeMessages:
            def create(self, **kwargs):
                calls.append(kwargs)
                return object()

        class FakeClient:
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
        with patch("core.auth.copilot_auth.get_token", return_value="github_pat_test"), patch(
            "core.auth.copilot_auth.validate_token_format",
            return_value=(True, "ok"),
        ), patch("core.auth.copilot_client.ask", return_value="OK"):
            ok, message = llm.test_route_connection("copilot", "gpt-4.1", "LLM")

        self.assertTrue(ok)
        self.assertIn("copilot", message)


if __name__ == "__main__":
    unittest.main()
