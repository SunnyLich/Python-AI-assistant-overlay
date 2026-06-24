"""OpenAI Evals-style local graders for chat tool-loop traces.

This module mirrors the parts of OpenAI's eval mental model that are useful for
Wisp's chat-flow harness: dataset items, samples, `sample.output_text`,
`sample.output_tools`, and graders that check tool names and tool arguments.
It is local and deterministic; it does not call the OpenAI Evals API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class OpenAIStyleExpectedTool:
    """Expected tool call for an OpenAI-style trace grader."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    match: str = "contains"


@dataclass(frozen=True)
class OpenAIStyleEvalItem:
    """One OpenAI Evals-style dataset item."""

    id: str
    prompt: str
    expected_tools: list[OpenAIStyleExpectedTool] = field(default_factory=list)
    expected_output_contains: list[str] = field(default_factory=list)
    require_recovery: bool = False
    reject_completion_gate_miss: bool = True


def eval_spec(items: list[OpenAIStyleEvalItem], *, name: str = "Wisp Chat Tool Flow") -> dict[str, Any]:
    """Return an OpenAI Evals-like spec with data source and testing criteria."""
    return {
        "name": name,
        "data_source_config": {
            "type": "custom",
            "item_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "expected_tools": {"type": "array"},
                    "expected_output_contains": {"type": "array"},
                },
                "required": ["id", "prompt"],
            },
            "include_sample_schema": True,
        },
        "testing_criteria": [
            {
                "type": "multi",
                "name": "Tool calls, arguments, recovery, and final answer",
                "graders": {
                    "tool_names": {
                        "type": "python_local",
                        "input": "{{ sample.output_tools }}",
                        "reference": "{{ item.expected_tools[*].name }}",
                    },
                    "tool_arguments": {
                        "type": "python_local",
                        "input": "{{ sample.output_tools[*].function.arguments }}",
                        "reference": "{{ item.expected_tools[*].arguments }}",
                    },
                    "final_answer": {
                        "type": "string_check",
                        "input": "{{ sample.output_text }}",
                        "operation": "contains_all",
                        "reference": "{{ item.expected_output_contains }}",
                    },
                },
                "calculate_output": "0.35 * tool_names + 0.35 * tool_arguments + 0.30 * final_answer",
            }
        ],
        "data": [_item_dict(item) for item in items],
    }


def sample_from_trace(trace) -> dict[str, Any]:
    """Convert a Wisp trace into OpenAI-style `sample` fields."""
    output_tools = []
    for call in trace.tool_calls:
        output_tools.append({
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments or {}, sort_keys=True),
            },
        })
    return {
        "output_text": trace.final_text,
        "output_tools": output_tools,
        "metadata": dict(trace.metadata or {}),
    }


def grade_trace(item: OpenAIStyleEvalItem, trace) -> dict[str, Any]:
    """Grade one trace using OpenAI-style tool and answer checks."""
    sample = sample_from_trace(trace)
    tool_name_score = _grade_tool_names(item.expected_tools, sample["output_tools"])
    tool_argument_score = _grade_tool_arguments(item.expected_tools, sample["output_tools"])
    final_answer_score = _grade_output_text(item.expected_output_contains, sample["output_text"])
    recovery_score = _grade_recovery(trace) if item.require_recovery else 1.0
    gate_score = 0.0 if item.reject_completion_gate_miss and trace.metadata.get("completion_gate_missed") else 1.0
    score = (
        0.30 * tool_name_score
        + 0.30 * tool_argument_score
        + 0.25 * final_answer_score
        + 0.10 * recovery_score
        + 0.05 * gate_score
    )
    passed = score >= 0.999
    return {
        "score": round(score, 4),
        "passed": passed,
        "graders": {
            "tool_names": tool_name_score,
            "tool_arguments": tool_argument_score,
            "final_answer": final_answer_score,
            "recovery": recovery_score,
            "completion_gate": gate_score,
        },
        "sample": sample,
    }


def grade_comparison(item: OpenAIStyleEvalItem, current_trace, unified_trace) -> dict[str, Any]:
    """Grade current and unified traces for one item."""
    return {
        "item": _item_dict(item),
        "current": grade_trace(item, current_trace),
        "unified": grade_trace(item, unified_trace),
    }


def default_items_by_scenario() -> dict[str, OpenAIStyleEvalItem]:
    """Return OpenAI-style eval items for the built-in harness scenarios."""
    return {
        "synthetic_file_context": OpenAIStyleEvalItem(
            id="synthetic_file_context",
            prompt="In this synthetic project, what does the app use for settings storage?",
            expected_tools=[
                OpenAIStyleExpectedTool("list_files"),
                OpenAIStyleExpectedTool("read_file", {"path": "config.py"}),
            ],
            expected_output_contains=["settings", "settings.json"],
        ),
        "synthetic_tool_recovery": OpenAIStyleEvalItem(
            id="synthetic_tool_recovery",
            prompt="Read notes.md and summarize it.",
            expected_tools=[
                OpenAIStyleExpectedTool("read_file", {"path": "notes.md"}),
                OpenAIStyleExpectedTool("list_files"),
                OpenAIStyleExpectedTool("read_file", {"path": "docs/notes.md"}),
            ],
            expected_output_contains=["settings.json", "startup||app starts||at startup"],
            require_recovery=True,
        ),
        "needs_file_context": OpenAIStyleEvalItem(
            id="needs_file_context",
            prompt="What does this project use for settings storage?",
            expected_tools=[
                OpenAIStyleExpectedTool("list_files"),
                OpenAIStyleExpectedTool("read_file"),
            ],
            expected_output_contains=["settings"],
        ),
        "edit_plus_verification": OpenAIStyleEvalItem(
            id="edit_plus_verification",
            prompt="Fix the syntax error in app.py and verify it.",
            expected_tools=[
                OpenAIStyleExpectedTool("read_file", {"path": "app.py"}),
                OpenAIStyleExpectedTool("edit_file", {"path": "app.py"}),
                OpenAIStyleExpectedTool("run_command"),
            ],
            expected_output_contains=["fixed", "verified"],
        ),
    }


def _grade_tool_names(expected: list[OpenAIStyleExpectedTool], output_tools: list[dict[str, Any]]) -> float:
    """Return 1 when expected tool names appear in order."""
    if not expected:
        return 1.0
    index = 0
    names = [str(tool.get("function", {}).get("name") or "") for tool in output_tools]
    for expected_tool in expected:
        try:
            found = names.index(expected_tool.name, index)
        except ValueError:
            return 0.0
        index = found + 1
    return 1.0


def _grade_tool_arguments(expected: list[OpenAIStyleExpectedTool], output_tools: list[dict[str, Any]]) -> float:
    """Return 1 when expected tool arguments match corresponding calls."""
    if not expected:
        return 1.0
    start = 0
    for expected_tool in expected:
        match_index = _find_matching_tool_with_arguments(output_tools, expected_tool, start)
        if match_index < 0:
            return 0.0
        start = match_index + 1
    return 1.0


def _grade_output_text(expected_contains: list[str], output_text: str) -> float:
    """Return 1 when all expected strings or explicit alternatives appear."""
    if not expected_contains:
        return 1.0
    text = str(output_text or "").lower()
    for needle in expected_contains:
        alternatives = [part.strip().lower() for part in str(needle).split("||") if part.strip()]
        if not alternatives or not any(alternative in text for alternative in alternatives):
            return 0.0
    return 1.0


def _grade_recovery(trace) -> float:
    """Return 1 when a successful tool result follows a failed one."""
    saw_failure = False
    for observation in trace.observations:
        for result in observation.tool_results:
            if saw_failure and result.ok:
                return 1.0
            if not result.ok:
                saw_failure = True
    return 0.0


def _find_matching_tool(output_tools: list[dict[str, Any]], name: str, start: int) -> int:
    """Find the next tool call with the requested name."""
    for index in range(start, len(output_tools)):
        if output_tools[index].get("function", {}).get("name") == name:
            return index
    return -1


def _find_matching_tool_with_arguments(
    output_tools: list[dict[str, Any]],
    expected_tool: OpenAIStyleExpectedTool,
    start: int,
) -> int:
    """Find the next tool call matching the requested name and expected args."""
    for index in range(start, len(output_tools)):
        if output_tools[index].get("function", {}).get("name") != expected_tool.name:
            continue
        if not expected_tool.arguments:
            return index
        actual = _tool_arguments(output_tools[index])
        if expected_tool.match == "eq":
            if actual == expected_tool.arguments:
                return index
            continue
        if all(actual.get(key) == expected_value for key, expected_value in expected_tool.arguments.items()):
            return index
    return -1


def _tool_arguments(output_tool: dict[str, Any]) -> dict[str, Any]:
    """Parse OpenAI-style tool arguments."""
    raw = output_tool.get("function", {}).get("arguments") or "{}"
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _item_dict(item: OpenAIStyleEvalItem) -> dict[str, Any]:
    """Convert an item to a JSON-friendly dictionary."""
    return {
        "id": item.id,
        "prompt": item.prompt,
        "expected_tools": [
            {"name": tool.name, "arguments": tool.arguments, "match": tool.match}
            for tool in item.expected_tools
        ],
        "expected_output_contains": list(item.expected_output_contains),
        "require_recovery": item.require_recovery,
        "reject_completion_gate_miss": item.reject_completion_gate_miss,
    }
