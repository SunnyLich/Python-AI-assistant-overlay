"""Opt-in real GPT 5.5 integration checks.

These tests intentionally call a live GPT 5.5 route and may spend tokens. They
prefer OPENAI_API_KEY when present, but can also use the app's ChatGPT OAuth
credential. They are skipped unless WISP_RUN_REAL_GPT55_TESTS=1 is set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BRAIN_DIR = _REPO_ROOT / "runtime" / "brain"
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

_RUN_ENV = "WISP_RUN_REAL_GPT55_TESTS"
_MODEL_ENV = "WISP_REAL_GPT55_MODEL"
_PROVIDER_ENV = "WISP_REAL_GPT55_PROVIDER"
_DEFAULT_MODEL = "gpt-5.5"
_MARKER_PHRASE = "wisp-real-gpt55-celadon"

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.real_gpt55,
    pytest.mark.skipif(
        os.getenv(_RUN_ENV) != "1",
        reason=f"set {_RUN_ENV}=1 to spend real GPT 5.5 API tokens",
    ),
]


def _recording_stream_context(req_id: Any = 1):
    """Create a real brain StreamContext while recording emitted events."""
    from wisp_brain.handlers import StreamContext

    events: list[tuple[str, Any]] = []
    ctx = StreamContext(lambda event, data, _rid: events.append((event, data)), req_id)
    return events, ctx


@pytest.fixture
def real_gpt55_route(monkeypatch):
    """Force the normal app route to a real gpt-5.5 credential for this test."""
    import config
    from core.auth import chatgpt as chatgpt_auth
    from core.llm_clients import client as llm_client

    config.reload()
    model = os.getenv(_MODEL_ENV, _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    requested_provider = os.getenv(_PROVIDER_ENV, "").strip().lower()
    openai_key_available = bool(getattr(config, "OPENAI_API_KEY", ""))
    chatgpt_available = bool(chatgpt_auth.get_tokens())
    if requested_provider:
        provider = requested_provider
    elif openai_key_available:
        provider = "openai"
    elif chatgpt_available:
        provider = "chatgpt"
    else:
        pytest.skip(
            "no real GPT 5.5 credential is visible: configure OPENAI_API_KEY "
            "or sign in with ChatGPT"
        )
    if provider == "openai" and not openai_key_available:
        pytest.skip("WISP_REAL_GPT55_PROVIDER=openai but OPENAI_API_KEY is not visible")
    if provider == "chatgpt" and not chatgpt_available:
        pytest.skip("WISP_REAL_GPT55_PROVIDER=chatgpt but ChatGPT auth is not visible")

    monkeypatch.delenv("WISP_BRAIN_FAKE_LLM", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", provider)
    monkeypatch.setattr(config, "LLM_MODEL", model)
    monkeypatch.setattr(config, "LLM_FALLBACKS", "")
    monkeypatch.setattr(config, "CHAT_LLM_PROVIDER", provider)
    monkeypatch.setattr(config, "CHAT_LLM_MODEL", model)
    monkeypatch.setattr(config, "CHAT_LLM_FALLBACKS", "")
    monkeypatch.setattr(config, "MEMORY_AUTO_CONSOLIDATE", False)
    monkeypatch.setattr(llm_client, "_CHAT_DEFAULT_MAX_TOKENS", 96)
    monkeypatch.setattr(llm_client, "_QUERY_DEFAULT_MAX_TOKENS", 96)
    llm_client.reset_clients()
    return f"{provider}/{model}"


@pytest.fixture
def isolated_real_conversation_and_memory(tmp_path, monkeypatch):
    """Use real project/conversation/memory stores, isolated to temp files."""
    from core.conversation_store import store as conversation_store
    from core.memory_store import store as memory_store

    chats = tmp_path / "chats"
    monkeypatch.setattr(conversation_store, "CHATS_DIR", chats)
    monkeypatch.setattr(conversation_store, "CHAT_ATTACHMENTS_DIR", chats / "attachments")
    monkeypatch.setattr(conversation_store, "PROJECTS_FILE", chats / "projects.json")
    monkeypatch.setattr(conversation_store, "CONVERSATIONS_FILE", chats / "conversations.json")

    memory_dir = tmp_path / "memory"
    monkeypatch.setattr(memory_store, "_MEMORY_DIR", str(memory_dir))
    monkeypatch.setattr(memory_store, "_FALLBACK_PATH", str(memory_dir / "facts_fallback.json"))
    monkeypatch.setattr(memory_store, "_manager", None)
    monkeypatch.setattr(memory_store.config, "MEMORY_AUTO_CONSOLIDATE", False)
    memory_store.set_active_project(None)

    project = conversation_store.add_project("Real GPT55 Integration")
    conversation = {
        "project_id": project["id"],
        "messages": [
            {"role": "user", "content": "Start the real GPT 5.5 integration conversation."},
            {"role": "assistant", "content": "Ready."},
        ],
        "context": "[Conversation Context]\nThis conversation is part of the real GPT 5.5 integration test.",
    }
    conversation_store.save_conversations([conversation])

    memory_store.set_active_project(project["id"])
    manager = memory_store.get_manager()
    manager.add_fact_manual(
        f"The GPT 5.5 integration project marker phrase is {_MARKER_PHRASE}.",
        "project_context",
        project=project["id"],
    )

    retrieved = manager.retrieve_relevant(
        "What is this project's GPT 5.5 integration marker phrase?",
        project_id=project["id"],
    )
    assert _MARKER_PHRASE in retrieved
    other_project = conversation_store.add_project("Other Project")
    other_retrieved = manager.retrieve_relevant(
        "What is this project's GPT 5.5 integration marker phrase?",
        project_id=other_project["id"],
    )
    assert _MARKER_PHRASE not in other_retrieved

    return conversation_store, project


def test_real_gpt55_chat_uses_real_conversation_and_project_memory(
    real_gpt55_route,
    isolated_real_conversation_and_memory,
):
    """Spend a tiny real GPT 5.5 request and prove chat sees project memory."""
    from wisp_brain import handlers

    conversation_store, project = isolated_real_conversation_and_memory
    conversations = conversation_store.load_conversations()
    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation["project_id"] == project["id"]

    user_prompt = "What is this project's integration marker phrase? Reply with only the phrase."
    messages = [
        {
            "role": "system",
            "content": (
                "You are running a Wisp integration test. "
                "Use supplied memory context. If a project marker phrase is present, "
                "reply only with that phrase. If no marker phrase is present, reply MISSING."
            ),
        },
        *conversation["messages"],
        {"role": "user", "content": user_prompt},
    ]
    events, ctx = _recording_stream_context(req_id="real-gpt55-chat-memory")

    result = handlers.HANDLERS["brain.chat"](
        ctx,
        messages=messages,
        memory_enabled=True,
        memory_project=project["id"],
        use_tools=False,
    )

    text = str(result.get("text") or "").strip()
    assert _MARKER_PHRASE in text.lower()
    assert any(event == "reply.chunk" for event, _data in events)
    assert ("reply.done", {"text": result["text"]}) in events

    conversation["messages"].append({"role": "user", "content": user_prompt})
    conversation["messages"].append({"role": "assistant", "content": result["text"]})
    conversation_store.save_conversations([conversation])
    reloaded = conversation_store.load_conversations()[0]
    assert reloaded["project_id"] == project["id"]
    assert reloaded["messages"][-1]["role"] == "assistant"
    assert _MARKER_PHRASE in reloaded["messages"][-1]["content"].lower()


def test_real_gpt55_query_uses_selected_ambient_and_project_memory(
    real_gpt55_route,
    isolated_real_conversation_and_memory,
):
    """Spend a tiny real GPT 5.5 request through the intent/query path."""
    from core.memory_store import store as memory_store
    from wisp_brain import handlers

    _conversation_store, project = isolated_real_conversation_and_memory
    memory_context = memory_store.get_manager().retrieve_relevant(
        "What marker phrase is in project memory?",
        project_id=project["id"],
    )
    assert _MARKER_PHRASE in memory_context

    events, ctx = _recording_stream_context(req_id="real-gpt55-query-context")
    result = handlers.HANDLERS["brain.query"](
        ctx,
        intent_prompt=(
            "This is a Wisp real-provider query test. Reply with exactly two comma-separated "
            f"tokens: selected-cobalt, {_MARKER_PHRASE}."
        ),
        selected="The selected-context token is selected-cobalt.",
        ambient_text="[Active App]\nThe active app is Workflow Harness.",
        memory_context=memory_context,
        memory_enabled=True,
        memory_project=project["id"],
        use_tools=False,
    )

    text = str(result.get("text") or "").strip().lower()
    assert "selected-cobalt" in text
    assert _MARKER_PHRASE in text
    assert any(event == "reply.chunk" for event, _data in events)
    assert events[-1] == ("reply.done", {"text": result["text"]})
