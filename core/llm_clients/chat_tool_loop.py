"""Provider-neutral chat tool loop contracts.

These types are intentionally small and behavior-light. They give Wisp one
internal shape for chat tool calls/results before provider-specific adapters
translate to or from OpenAI Responses, Anthropic, or OpenAI-compatible payloads.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from typing import Protocol


@dataclass(frozen=True)
class ChatToolRequest:
    """Input needed by a provider-neutral chat tool loop."""

    messages: list[dict[str, Any]]
    system_prompt: str
    model_route: dict[str, Any]
    tools: list[dict[str, Any]]
    allowed_tools: list[str] | None
    pinned_tools: list[str] | None
    permissions: dict[str, Any]
    budgets: dict[str, Any]
    ambient_context: str = ""
    memory_context: str = ""
    screenshot_b64: str | None = None


@dataclass(frozen=True)
class WispToolCall:
    """One normalized tool call requested by a model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    provider_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class WispToolResult:
    """One normalized tool result returned to a model."""

    call_id: str
    name: str
    ok: bool
    content: str | list[dict[str, Any]]
    clipped: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class WispObservation:
    """A compact observation generated after one or more tool calls."""

    tool_results: list[WispToolResult]
    summary: str
    remaining_budget: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatLoopFinal:
    """Final output and trace metadata from a chat tool loop."""

    text: str
    status: str
    observations: list[WispObservation] = field(default_factory=list)
    tool_calls: list[WispToolCall] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatModelTurn:
    """One normalized model turn returned to the neutral loop."""

    tool_calls: list[WispToolCall] = field(default_factory=list)
    final_text: str = ""
    status: str = "continue"
    progress: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatToolLoopConfig:
    """Budgets and policy switches for the provider-neutral loop."""

    max_rounds: int = 7
    max_tool_calls: int = 4
    completion_gate: bool = True


class ChatLoopModel(Protocol):
    """Provider adapter/model interface consumed by the neutral loop."""

    def next_turn(
        self,
        request: ChatToolRequest,
        observations: list[WispObservation],
        tool_calls: list[WispToolCall],
    ) -> ChatModelTurn:
        """Return the next normalized model turn."""


class ChatToolExecutor(Protocol):
    """Tool executor interface consumed by the neutral loop."""

    def execute(self, call: WispToolCall) -> WispToolResult:
        """Execute one normalized tool call."""


class ChatToolLoop:
    """Provider-neutral observe-act-observe loop for chat tools."""

    def __init__(self, config: ChatToolLoopConfig | None = None):
        """Initialize the chat tool loop."""
        self.config = config or ChatToolLoopConfig()

    def run(
        self,
        request: ChatToolRequest,
        model: ChatLoopModel,
        executor: ChatToolExecutor,
    ) -> ChatLoopFinal:
        """Run the neutral tool loop until final text, block, or budget exhaustion."""
        observations: list[WispObservation] = []
        tool_calls: list[WispToolCall] = []
        progress_chunks: list[str] = []
        metadata: dict[str, Any] = {"completion_gate_missed": False}
        gate_nudges: set[str] = set()
        tool_call_count = 0

        for _round in range(max(1, self.config.max_rounds)):
            turn = model.next_turn(request, observations, tool_calls)
            if turn.progress:
                progress_chunks.append(turn.progress)
            if turn.tool_calls:
                results: list[WispToolResult] = []
                for call in turn.tool_calls:
                    tool_calls.append(call)
                    if tool_call_count >= max(0, self.config.max_tool_calls):
                        results.append(
                            WispToolResult(
                                call_id=call.id,
                                name=call.name,
                                ok=False,
                                content=(
                                    "Tool call skipped: this reply's tool-call budget is exhausted. "
                                    "Ask for a narrower request or run a deeper profile."
                                ),
                                metadata={"error_type": "tool_budget_exhausted"},
                            )
                        )
                        continue
                    tool_call_count += 1
                    results.append(executor.execute(call))
                observations.append(
                    WispObservation(
                        tool_results=results,
                        summary=self._observation_summary(results),
                        remaining_budget={
                            "tool_calls": max(0, self.config.max_tool_calls - tool_call_count),
                        },
                    )
                )
                continue
            if turn.final_text:
                gate_message = self._completion_gate_message(request, tool_calls, observations, turn.final_text)
                if self.config.completion_gate and gate_message and gate_message not in gate_nudges:
                    gate_nudges.add(gate_message)
                    observations.append(
                        WispObservation(
                            tool_results=[],
                            summary=gate_message,
                            remaining_budget={
                                "tool_calls": max(0, self.config.max_tool_calls - tool_call_count),
                                "completion_gate": "nudged",
                            },
                        )
                    )
                    continue
                if self.config.completion_gate and gate_message and gate_message in gate_nudges:
                    metadata["completion_gate_missed"] = True
                    metadata["completion_gate_message"] = gate_message
                metadata["completion_gate_nudges"] = list(gate_nudges)
                metadata["progress_chunks"] = progress_chunks
                metadata.update(turn.metadata)
                return ChatLoopFinal(
                    text=turn.final_text,
                    status=turn.status or "final",
                    observations=observations,
                    tool_calls=tool_calls,
                    metadata=metadata,
                )

        metadata["progress_chunks"] = progress_chunks
        metadata["completion_gate_nudges"] = list(gate_nudges)
        metadata["tool_budget_exhausted"] = tool_call_count >= max(0, self.config.max_tool_calls)
        return ChatLoopFinal(
            text="Chat tool loop stopped before a final answer because the round budget was exhausted.",
            status="round_budget_exhausted",
            observations=observations,
            tool_calls=tool_calls,
            metadata=metadata,
        )

    @staticmethod
    def _observation_summary(results: Sequence[WispToolResult]) -> str:
        """Build a compact observation summary."""
        if not results:
            return "No tool results."
        parts = []
        for result in results:
            status = "ok" if result.ok else "failed"
            parts.append(f"{result.name}: {status}")
        return "; ".join(parts)

    def _completion_gate_message(
        self,
        request: ChatToolRequest,
        tool_calls: list[WispToolCall],
        observations: list[WispObservation],
        final_text: str = "",
    ) -> str:
        """Return a completion nudge when final text skipped obvious available work."""
        prompt = _request_user_text(request).lower()
        called = {call.name for call in tool_calls}
        succeeded = _successful_tool_names(observations)
        available = _available_tool_names(request)
        if (
            _latest_result_hit_tool_budget(observations)
            and _has_successful_evidence(prompt, observations)
            and _looks_like_budget_or_retry_answer(final_text)
        ):
            return (
                "The answer is not complete yet. A later tool call hit the tool budget, but successful "
                "tool results are already available. Answer from the successful observations instead "
                "of asking the user to retry, or explain the concrete missing evidence."
            )
        if _looks_like_memory_request(prompt) and "memory_search" in available and "memory_search" not in called:
            return _gate_nudge("memory_search")
        if _looks_like_screen_request(prompt) and available.intersection({"capture_screen", "get_context"}).isdisjoint(called):
            return _gate_nudge("screen or context")
        if _looks_like_file_mutation_request(prompt):
            mutation_tools = available.intersection({"create_file", "edit_file", "write_file", "delete_file"})
            if mutation_tools and mutation_tools.isdisjoint(succeeded):
                return _gate_nudge("file mutation")
        failed_names = _failed_tool_names(observations)
        if "read_file" in failed_names and "list_files" in available and "list_files" not in called:
            return (
                "The answer is not complete yet. read_file failed, and list_files is available. "
                "Try discovering the correct path before finalizing, or explain the concrete "
                "permission or capability boundary."
            )
        if (
            "read_file" in failed_names
            and "list_files" in _successful_tool_names(observations)
            and not _successful_read_after_latest_list(observations)
        ):
            return (
                "The answer is not complete yet. read_file failed, then list_files succeeded. "
                "Use the discovered file list to choose and inspect a relevant evidence source before finalizing, "
                "or explain the concrete permission or capability boundary."
            )
        if _looks_like_verification_request(prompt) and "run_command" in available and "run_command" not in called:
            return _gate_nudge("verification")
        if _looks_like_file_context_request(prompt):
            context_tools = available.intersection({"list_files", "read_file"})
            if context_tools and context_tools.isdisjoint(called):
                return _gate_nudge("file context")
            if "list_files" in succeeded and "read_file" in available and "read_file" not in succeeded:
                return (
                    "The answer is not complete yet. list_files is a discovery tool, not an evidence source. "
                    "Inspect relevant file contents with read_file before finalizing, or explain "
                    "the concrete permission or capability boundary."
                )
        return ""


def _gate_nudge(kind: str) -> str:
    """Build a conservative completion-gate nudge."""
    return (
        "The answer is not complete yet. Available tools look relevant to the user's "
        f"request ({kind}). Continue the tool loop or explain the concrete permission "
        "or capability boundary."
    )


def _request_user_text(request: ChatToolRequest) -> str:
    """Extract the latest user text from a request."""
    for message in reversed(request.messages):
        if str(message.get("role") or "").lower() != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
            return " ".join(part for part in parts if part)
    return ""


def _available_tool_names(request: ChatToolRequest) -> set[str]:
    """Return tool names made available to the neutral loop."""
    if request.allowed_tools is not None:
        return set(request.allowed_tools)
    names: set[str] = set()
    for tool in request.tools:
        name = str(tool.get("name") or "")
        if not name and isinstance(tool.get("function"), dict):
            name = str(tool["function"].get("name") or "")
        if name:
            names.add(name)
    return names


def _successful_tool_names(observations: list[WispObservation]) -> set[str]:
    """Return tool names with at least one successful result."""
    names: set[str] = set()
    for observation in observations:
        for result in observation.tool_results:
            if result.ok:
                names.add(result.name)
    return names


def _failed_tool_names(observations: list[WispObservation]) -> set[str]:
    """Return tool names with at least one failed result."""
    names: set[str] = set()
    for observation in observations:
        for result in observation.tool_results:
            if not result.ok:
                names.add(result.name)
    return names


def _latest_result_hit_tool_budget(observations: list[WispObservation]) -> bool:
    """Return whether the latest tool observation includes a budget-skipped call."""
    for observation in reversed(observations):
        if not observation.tool_results:
            continue
        return any(
            result.metadata and result.metadata.get("error_type") == "tool_budget_exhausted"
            for result in observation.tool_results
        )
    return False


def _has_successful_evidence(prompt: str, observations: list[WispObservation]) -> bool:
    """Return whether previous successful tool output likely contains answer evidence."""
    if _looks_like_file_context_request(prompt):
        return "read_file" in _successful_tool_names(observations)
    if _looks_like_memory_request(prompt):
        return "memory_search" in _successful_tool_names(observations)
    if _looks_like_screen_request(prompt):
        return bool({"capture_screen", "get_context"}.intersection(_successful_tool_names(observations)))
    return bool(_successful_tool_names(observations))


def _looks_like_budget_or_retry_answer(text: str) -> bool:
    """Return whether final text mainly asks the user to retry because tools stopped."""
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in (
            "budget",
            "exhausted",
            "ask again",
            "try again",
            "retry",
            "narrower",
            "couldn't access",
            "could not access",
            "unable to access",
        )
    )


def _successful_read_after_latest_list(observations: list[WispObservation]) -> bool:
    """Return whether read_file succeeded after the latest successful list_files."""
    latest_list_index = -1
    for index, observation in enumerate(observations):
        for result in observation.tool_results:
            if result.name == "list_files" and result.ok:
                latest_list_index = index
    if latest_list_index < 0:
        return False
    for observation in observations[latest_list_index + 1:]:
        for result in observation.tool_results:
            if result.name == "read_file" and result.ok:
                return True
    return False


def _looks_like_file_context_request(prompt: str) -> bool:
    """Return whether the prompt likely needs file/project context."""
    return any(word in prompt for word in ("file", "project", "repo", "code", "settings", "storage"))


def _looks_like_file_mutation_request(prompt: str) -> bool:
    """Return whether the prompt likely asks for a file change."""
    return any(word in prompt for word in ("change", "fix", "update", "edit", "write", "create", "delete"))


def _looks_like_verification_request(prompt: str) -> bool:
    """Return whether the prompt likely asks for verification."""
    return any(word in prompt for word in ("verify", "test", "syntax", "run"))


def _looks_like_memory_request(prompt: str) -> bool:
    """Return whether the prompt likely needs memory search."""
    return "remember" in prompt or "what do you know about" in prompt or "my preferences" in prompt


def _looks_like_screen_request(prompt: str) -> bool:
    """Return whether the prompt likely needs screen or app context."""
    return any(phrase in prompt for phrase in ("what am i looking at", "on my screen", "this screen", "this app"))
