"""Tests for Ctrl+Shift+Q rewrite tool-call extraction."""

from types import SimpleNamespace

from core.llm_clients import client as llm


def test_openai_compat_rewrite_forces_tool_call_and_extracts_replacement(monkeypatch):
    """Verify OpenAI-compatible rewrite pastes only rewrite_selection text."""
    calls: list[dict] = []

    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="rewrite_selection",
            arguments=(
                '{"replacement_text": "Text 2 body", '
                '"assistant_response": "I used the source text for the replacement."}'
            ),
        ),
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Text 2 body", tool_calls=[tool_call])
            )
        ]
    )

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return response

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    monkeypatch.setattr(llm, "_dynamic_openai_client", lambda _provider: fake_client)
    monkeypatch.setattr(llm, "_use_macos_openai_compat_non_streaming", lambda _provider: True)

    chunks = list(llm._stream_openai_compat_rewrite_tool("openai", "gpt-test", "prompt"))

    assert calls
    assert calls[0]["tools"][0]["function"]["name"] == "rewrite_selection"
    assert calls[0]["tool_choice"]["function"]["name"] == "rewrite_selection"
    assert "assistant_response" in calls[0]["tools"][0]["function"]["parameters"]["required"]
    assert [(getattr(chunk, "kind", ""), str(chunk)) for chunk in chunks] == [
        ("progress", "I used the source text for the replacement."),
        ("rewrite_result", "Text 2 body"),
    ]


def test_responses_rewrite_forces_tool_call_and_extracts_replacement(monkeypatch):
    """Verify Responses rewrite asks for rewrite_selection and extracts it."""
    calls: list[dict] = []

    def fake_create(_client, kwargs, *, model):
        calls.append(kwargs)
        return {
            "output_text": "Ready.",
            "output": [
                {
                    "type": "function_call",
                    "name": "rewrite_selection",
                    "call_id": "call_1",
                    "arguments": (
                        '{"replacement_text": "Text 2 body", '
                        '"assistant_response": "I used the source text for the replacement."}'
                    ),
                }
            ],
        }

    monkeypatch.setattr(llm, "_get_codex_client", lambda: object())
    monkeypatch.setattr(llm, "_responses_rewrite_create_with_retries", fake_create)

    chunks = list(llm._stream_responses_rewrite_tool("gpt-test", "prompt"))

    assert calls
    assert calls[0]["tools"][0]["name"] == "rewrite_selection"
    assert calls[0]["tool_choice"]["name"] == "rewrite_selection"
    assert "assistant_response" in calls[0]["tools"][0]["parameters"]["required"]
    assert [(getattr(chunk, "kind", ""), str(chunk)) for chunk in chunks] == [
        ("progress", "I used the source text for the replacement."),
        ("rewrite_result", "Text 2 body"),
    ]
