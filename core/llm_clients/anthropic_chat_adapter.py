"""Anthropic adapter for the provider-neutral chat tool loop."""
from __future__ import annotations

from typing import Any

from core.llm_clients.chat_tool_loop import (
    ChatLoopModel,
    ChatModelTurn,
    ChatToolRequest,
    WispObservation,
    WispToolCall,
)


class AnthropicChatLoopModel(ChatLoopModel):
    """Claude Messages adapter for the provider-neutral loop."""

    def __init__(
        self,
        client,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        provider: str = "anthropic",
    ):
        """Initialize the Anthropic adapter."""
        self._client = client
        self._model = model
        self._system = system
        self._messages = list(messages)
        self._tools = tools
        self._max_tokens = max_tokens
        self._provider = provider
        self._sent_observations = 0

    def next_turn(
        self,
        _request: ChatToolRequest,
        observations: list[WispObservation],
        _tool_calls: list[WispToolCall],
    ) -> ChatModelTurn:
        """Call Claude Messages and normalize the result into one model turn."""
        from core.llm_clients import client as llm

        if len(observations) > self._sent_observations:
            observation = observations[-1]
            self._sent_observations = len(observations)
            if observation.tool_results:
                self._messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.call_id,
                            "content": result.content,
                        }
                        for result in observation.tool_results
                    ],
                })
            else:
                self._messages.append({
                    "role": "user",
                    "content": observation.summary,
                })

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system,
            messages=self._messages,
            tools=self._tools,
        )
        llm._update_route_capabilities(self._provider, self._model, supports_tools=True)
        text_blocks: list[str] = []
        calls: list[WispToolCall] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                text = getattr(block, "text", "")
                if text:
                    text_blocks.append(text)
                continue
            if getattr(block, "type", "") != "tool_use":
                continue
            calls.append(
                WispToolCall(
                    id=str(getattr(block, "id", "")),
                    name=str(getattr(block, "name", "")),
                    arguments=dict(getattr(block, "input", None) or {}),
                    provider_payload={
                        "id": str(getattr(block, "id", "")),
                        "name": str(getattr(block, "name", "")),
                        "input": dict(getattr(block, "input", None) or {}),
                    },
                )
            )
        if calls:
            self._messages.append({"role": "assistant", "content": response.content})
            return ChatModelTurn(tool_calls=calls, progress="".join(text_blocks))
        return ChatModelTurn(final_text="".join(text_blocks), status="final")
