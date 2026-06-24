"""Responses API adapter for the provider-neutral chat tool loop."""
from __future__ import annotations

import json
from typing import Any

from core.llm_clients.chat_tool_loop import (
    ChatLoopModel,
    ChatModelTurn,
    ChatToolRequest,
    WispObservation,
    WispToolCall,
    WispToolResult,
)


class ResponsesChatLoopModel(ChatLoopModel):
    """ChatGPT/Responses adapter for the provider-neutral loop."""

    def __init__(
        self,
        client,
        *,
        model: str,
        instructions: str,
        tools: list[dict[str, Any]],
        initial_input: list[dict[str, Any]] | None = None,
        provider: str = "chatgpt",
    ):
        """Initialize live Responses adapter."""
        self._client = client
        self._model = model
        self._instructions = instructions
        self._tools = tools
        self._initial_input = initial_input
        self._provider = provider
        self._response = None
        self._sent_observations = 0
        self._last_provider_calls: list[dict[str, str]] = []
        self._last_response_output: list[dict[str, Any]] = []
        self._last_response_output_replayed = True
        self._transcript_input: list[dict[str, Any]] = []

    def next_turn(
        self,
        request: ChatToolRequest,
        observations: list[WispObservation],
        _tool_calls: list[WispToolCall],
    ) -> ChatModelTurn:
        """Call Responses and normalize the result into one model turn."""
        from core.llm_clients import client as llm

        kwargs = self._next_kwargs(request, observations)
        try:
            response = llm._responses_create_with_retries(
                self._client,
                kwargs,
                provider=self._provider,
                model=self._model,
            )
        except Exception as exc:
            if not llm._no_matching_tool_call_error(exc) or not self._last_provider_calls:
                raise
            fallback_kwargs = {
                "model": self._model,
                "input": self._input_after_observation(observations[-1]),
                "store": False,
            }
            if self._instructions:
                fallback_kwargs["instructions"] = self._instructions
            if self._tools:
                fallback_kwargs["tools"] = self._tools
            response = llm._responses_create_with_retries(
                self._client,
                fallback_kwargs,
                provider=self._provider,
                model=self._model,
            )
        self._response = response
        self._last_response_output = [
            stateless_response_output_item(llm._normalized_response_item(item))
            for item in llm._response_output_items(response)
        ]
        self._last_response_output_replayed = False
        calls = llm._response_function_calls(response)
        self._last_provider_calls = calls
        progress = llm._response_output_text(response) if calls else ""
        if calls:
            return ChatModelTurn(
                tool_calls=[
                    WispToolCall(
                        id=call["call_id"],
                        name=call["name"],
                        arguments=json_object(call.get("arguments") or "{}"),
                        provider_payload=call,
                    )
                    for call in calls
                ],
                progress=progress,
            )
        return ChatModelTurn(
            final_text=llm._response_output_text(response),
            status="final",
        )

    def _next_kwargs(self, request: ChatToolRequest, observations: list[WispObservation]) -> dict[str, Any]:
        """Build the next Responses API kwargs."""
        if self._response is None:
            self._transcript_input = self._initial_request_input(request)
            kwargs = {
                "model": self._model,
                "input": list(self._transcript_input),
                "instructions": self._instructions,
                "tools": self._tools,
                "store": False,
            }
            return kwargs
        if len(observations) > self._sent_observations:
            observation = observations[-1]
            self._sent_observations = len(observations)
            input_items = self._input_after_observation(observation)
            kwargs = {
                "model": self._model,
                "input": input_items,
                "store": False,
            }
        else:
            self._transcript_input.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue."}],
            })
            kwargs = {
                "model": self._model,
                "input": list(self._transcript_input),
                "store": False,
            }
        if self._instructions:
            kwargs["instructions"] = self._instructions
        if self._tools:
            kwargs["tools"] = self._tools
        return kwargs

    def _initial_request_input(self, request: ChatToolRequest) -> list[dict[str, Any]]:
        """Return the transcript input that anchors every stateless turn."""
        if self._initial_input:
            return [dict(item) for item in self._initial_input]
        return [{
            "type": "message",
            "role": "user",
            "content": request_input_content(request),
        }]

    def _input_after_observation(self, observation: WispObservation) -> list[dict[str, Any]]:
        """Append one observation to the stateless transcript and return it."""
        from core.llm_clients import client as llm

        if observation.tool_results:
            if not self._last_response_output_replayed:
                self._transcript_input.extend(dict(item) for item in self._last_response_output)
                self._last_response_output_replayed = True
            self._transcript_input.extend(
                function_outputs_from_observation(observation)
            )
            self._last_provider_calls = []
        else:
            self._transcript_input.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": observation.summary}],
            })
        return list(self._transcript_input)


class LiveModelToolExecutor:
    """Execute real Wisp model tools while returning normalized results."""

    def __init__(self, *, allowed_tools: list[str] | None = None, clip_results: bool = True):
        """Initialize live tool executor."""
        self.allowed_tools = allowed_tools
        self.clip_results = clip_results
        self._spent_chars = 0

    def execute(self, call: WispToolCall) -> WispToolResult:
        """Execute a real Wisp model tool."""
        from core.llm_clients import client as llm

        content = llm._execute_model_tool(call.name, call.arguments, allowed_tools=self.allowed_tools)
        clipped = False
        if self.clip_results:
            clipped_content, self._spent_chars = llm._clip_tool_result_for_turn(content, self._spent_chars)
            clipped = clipped_content != str(content or "")
            content = clipped_content
        return WispToolResult(
            call_id=call.id,
            name=call.name,
            ok=not looks_like_tool_failure(content),
            content=content,
            clipped=clipped,
        )


def function_outputs_from_observation(observation: WispObservation) -> list[dict[str, str]]:
    """Convert neutral tool results into Responses function_call_output items."""
    outputs: list[dict[str, str]] = []
    for result in observation.tool_results:
        content = result.content
        output = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        outputs.append({
            "type": "function_call_output",
            "call_id": result.call_id,
            "output": output,
        })
    return outputs


def stateless_response_output_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return a Responses output item safe to replay with store=false."""
    return _strip_response_item_ids(dict(item))


def _strip_response_item_ids(value: Any) -> Any:
    """Drop server-persistence item ids while preserving call_id links."""
    if isinstance(value, dict):
        return {
            key: _strip_response_item_ids(child)
            for key, child in value.items()
            if key != "id"
        }
    if isinstance(value, list):
        return [_strip_response_item_ids(item) for item in value]
    return value


def request_input_content(request: ChatToolRequest) -> list[dict[str, Any]]:
    """Build Responses input content from a neutral request."""
    text = ""
    for message in reversed(request.messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            return content
        text = str(content or "")
        break
    parts = [text]
    if request.ambient_context:
        parts.append(request.ambient_context)
    if request.memory_context:
        parts.append(request.memory_context)
    return [{"type": "input_text", "text": "\n\n".join(part for part in parts if part)}]


def json_object(text: str) -> dict[str, Any]:
    """Parse JSON object text defensively."""
    try:
        value = json.loads(text or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def looks_like_tool_failure(content: str) -> bool:
    """Return whether a live tool result reads like an in-band failure."""
    text = str(content or "").lower()
    return any(
        marker in text
        for marker in (
            " is disabled ",
            "failed",
            "not found",
            "no such file",
            "could not find",
            "couldn't find",
            "cannot find",
            "permission",
            "not allowed",
            "tool call skipped",
            "requires ",
        )
    )
