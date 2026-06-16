"""Conversation-continuity history replay in the LLM client."""
from core.llm_clients import client


def test_sanitize_history_keeps_only_text_user_assistant_turns():
    raw = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "display_content": "<b>hello</b>"},
        {"role": "user", "content": "   "},          # empty -> dropped
        {"role": "system", "content": "ignore me"},   # wrong role -> dropped
        {"role": "user", "content": ["not", "a", "string"]},  # non-str -> dropped
    ]
    assert client._sanitize_history(raw) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_sanitize_history_handles_none_and_empty():
    assert client._sanitize_history(None) == []
    assert client._sanitize_history([]) == []


def test_openai_messages_splice_history_between_system_and_current_turn():
    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
    ]
    msgs = client._build_openai_messages("now", None, "", "", history)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "first"
    assert msgs[2]["content"] == "reply"
    assert msgs[-1]["content"] == "now"


def test_openai_messages_no_history_is_system_then_user():
    msgs = client._build_openai_messages("solo", None, "", "")
    assert [m["role"] for m in msgs] == ["system", "user"]
