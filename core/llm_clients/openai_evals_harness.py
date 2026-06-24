"""Adapter that runs Wisp chat traces through the installed OpenAI Evals package.

This module intentionally imports `evals` lazily. The package is a developer
dependency for comparison runs, not part of normal chat runtime startup.
"""
from __future__ import annotations

from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.llm_clients.chat_tool_loop import WispObservation, WispToolCall, WispToolResult
from core.llm_clients.openai_evals_style import (
    OpenAIStyleEvalItem,
    OpenAIStyleExpectedTool,
    default_items_by_scenario,
    grade_trace,
)


def run_openai_evals_package(report, output_dir: str | Path) -> dict[str, Any]:
    """Run current/unified traces through OpenAI Evals' Eval and LocalRecorder.

    The installed `evals` package requires an OpenAI API key at import time even
    for local deterministic evals. We provide a dummy key only when the caller
    has not configured one, because this adapter does not make API calls.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    samples = _samples_from_report(report)
    result_path = output_path / "openai_evals_events.jsonl"
    recorder_path = _blobfile_local_path(result_path)
    event_log_path = str(result_path)
    if not samples:
        return {
            "harness": "openai-evals-package",
            "available": True,
            "samples": 0,
            "event_log_path": event_log_path,
            "final_report": {},
            "note": "No report scenarios had OpenAI-style eval items.",
        }

    try:
        evals = _import_openai_evals()
    except Exception as exc:  # noqa: BLE001 - comparison artifacts should record missing dev deps
        return {
            "harness": "openai-evals-package",
            "available": False,
            "samples": len(samples),
            "event_log_path": event_log_path,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    run_spec = evals.base.RunSpec(
        completion_fns=["wisp-trace-replay"],
        eval_name="wisp_chat_tool_flow.package",
        base_eval="wisp_chat_tool_flow",
        split="package",
        run_config={
            "source": "Wisp chat flow comparison traces",
            "samples": len(samples),
            "flows": sorted({sample["flow_key"] for sample in samples}),
            "scenarios": sorted({sample["scenario"] for sample in samples}),
        },
        created_by="wisp-chat-flow-harness",
    )
    recorder = evals.record.LocalRecorder(recorder_path, run_spec=run_spec)
    wisp_trace_eval = _make_wisp_trace_eval_class(evals)
    package_eval = wisp_trace_eval(
        completion_fns=[],
        eval_registry_path=Path("."),
        name="wisp_chat_tool_flow.package",
        samples=samples,
    )
    final_report = package_eval.run(recorder)
    recorder.record_final_report(final_report)
    recorder.flush_events()
    return {
        "harness": "openai-evals-package",
        "available": True,
        "package_version": importlib.metadata.version("evals"),
        "samples": len(samples),
        "event_log_path": event_log_path,
        "final_report": final_report,
        "events": _event_counts(recorder),
    }


def build_harness_matrix(report, openai_package_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one payload comparing all eval harness layers for the same traces."""
    from core.llm_clients.chat_flow_compare import _openai_eval_scores

    return {
        "flows": ["current", "unified"],
        "harnesses": {
            "wisp_behavior_score": {
                comparison.scenario: {
                    "current": comparison.current,
                    "unified": comparison.unified,
                }
                for comparison in report.comparisons
            },
            "openai_style_local": _openai_eval_scores(report),
            "openai_evals_package": openai_package_report or {},
        },
    }


def _make_wisp_trace_eval_class(evals):
    """Create an OpenAI Evals Eval subclass after the package is available."""

    class WispTraceEval(evals.Eval):
        """OpenAI Evals Eval subclass for already-captured Wisp traces."""

        def __init__(self, *args, samples: list[dict[str, Any]], **kwargs):
            """Initialize with precomputed trace samples."""
            self.samples = list(samples)
            super().__init__(*args, **kwargs)

        def eval_sample(self, sample: dict[str, Any], _rng):
            """Grade one trace sample and record OpenAI Evals events."""
            item = _item_from_dict(sample["item"])
            trace = _trace_from_dict(sample["trace"])
            grade = grade_trace(item, trace)

            evals.record.record_raw({
                "scenario": sample["scenario"],
                "flow_key": sample["flow_key"],
                "flow": sample["flow"],
                "prompt": sample["prompt"],
            })
            for call in trace.tool_calls:
                result = _result_for_call(trace, call.id)
                evals.record.record_function_call(
                    call.name,
                    call.arguments,
                    result.content if result is not None else None,
                    ok=result.ok if result is not None else None,
                    flow_key=sample["flow_key"],
                    scenario=sample["scenario"],
                )
            for grader_name, score in grade["graders"].items():
                evals.record.record_match(
                    bool(score >= 1.0),
                    expected=_expected_for_grader(item, grader_name),
                    picked=_picked_for_grader(trace, grader_name),
                    grader=grader_name,
                    score=score,
                    flow_key=sample["flow_key"],
                    scenario=sample["scenario"],
                )
            evals.record.record_metrics(
                score=grade["score"],
                passed=1.0 if grade["passed"] else 0.0,
                flow_key=sample["flow_key"],
                scenario=sample["scenario"],
                flow=sample["flow"],
            )
            return grade

        def run(self, recorder):
            """Run all trace samples and return aggregate package-style metrics."""
            self.eval_all_samples(recorder, self.samples, show_progress=False)
            metrics = [event.data for event in recorder.get_events("metrics")]
            by_flow: dict[str, dict[str, Any]] = {}
            by_scenario: dict[str, dict[str, Any]] = {}
            for metric in metrics:
                flow_key = metric["flow_key"]
                scenario = metric["scenario"]
                by_flow.setdefault(flow_key, {"scores": [], "passed": 0, "total": 0})
                by_flow[flow_key]["scores"].append(float(metric["score"]))
                by_flow[flow_key]["passed"] += int(metric["passed"] >= 1.0)
                by_flow[flow_key]["total"] += 1
                by_scenario.setdefault(scenario, {})[flow_key] = {
                    "score": metric["score"],
                    "passed": bool(metric["passed"] >= 1.0),
                }
            for flow_metrics in by_flow.values():
                total = flow_metrics["total"] or 1
                scores = flow_metrics.pop("scores")
                flow_metrics["average_score"] = round(sum(scores) / total, 4)
                flow_metrics["pass_rate"] = round(flow_metrics["passed"] / total, 4)
            return {
                "accuracy": round(
                    sum(float(metric["passed"]) for metric in metrics) / (len(metrics) or 1),
                    4,
                ),
                "average_score": round(
                    sum(float(metric["score"]) for metric in metrics) / (len(metrics) or 1),
                    4,
                ),
                "samples": len(metrics),
                "by_flow": by_flow,
                "by_scenario": by_scenario,
            }

    return WispTraceEval


def _import_openai_evals():
    """Import OpenAI Evals with a local dummy key if none is configured."""
    os.environ.setdefault("OPENAI_API_KEY", "sk-local-openai-evals-harness")
    import evals  # type: ignore

    return evals


def _blobfile_local_path(path: Path) -> str:
    """Return a local path format accepted by OpenAI Evals' blobfile on Windows."""
    resolved = path.resolve()
    try:
        return os.path.relpath(resolved, Path.cwd().resolve())
    except ValueError:
        return resolved.as_posix()


def _samples_from_report(report) -> list[dict[str, Any]]:
    """Create OpenAI Evals samples for every scored flow trace."""
    items = default_items_by_scenario()
    samples: list[dict[str, Any]] = []
    for comparison in report.comparisons:
        item = items.get(comparison.scenario)
        if item is None:
            continue
        for flow_key, trace in (
            ("current", comparison.current_trace),
            ("unified", comparison.unified_trace),
        ):
            samples.append({
                "id": f"{comparison.scenario}.{flow_key}",
                "scenario": comparison.scenario,
                "flow_key": flow_key,
                "flow": trace.flow,
                "prompt": trace.prompt,
                "item": _item_dict(item),
                "trace": asdict(trace),
            })
    return samples


def _event_counts(recorder) -> dict[str, int]:
    """Return event counts by OpenAI Evals event type."""
    counts: dict[str, int] = {}
    for event in recorder._events:  # noqa: SLF001 - local package recorder exposes no aggregate helper
        counts[event.type] = counts.get(event.type, 0) + 1
    return counts


def _item_from_dict(data: dict[str, Any]) -> OpenAIStyleEvalItem:
    """Rebuild an OpenAIStyleEvalItem from JSON-friendly data."""
    return OpenAIStyleEvalItem(
        id=str(data["id"]),
        prompt=str(data["prompt"]),
        expected_tools=[
            OpenAIStyleExpectedTool(
                name=str(tool["name"]),
                arguments=dict(tool.get("arguments") or {}),
                match=str(tool.get("match") or "contains"),
            )
            for tool in data.get("expected_tools", [])
        ],
        expected_output_contains=[str(value) for value in data.get("expected_output_contains", [])],
        require_recovery=bool(data.get("require_recovery")),
        reject_completion_gate_miss=bool(data.get("reject_completion_gate_miss", True)),
    )


def _trace_from_dict(data: dict[str, Any]):
    """Rebuild the minimum trace object needed by the graders."""
    return SimpleNamespace(
        flow=data.get("flow", ""),
        scenario=data.get("scenario", ""),
        prompt=data.get("prompt", ""),
        tools_offered=list(data.get("tools_offered") or []),
        tool_calls=[
            WispToolCall(
                id=str(call.get("id") or ""),
                name=str(call.get("name") or ""),
                arguments=dict(call.get("arguments") or {}),
            )
            for call in data.get("tool_calls", [])
        ],
        observations=[
            WispObservation(
                tool_results=[
                    WispToolResult(
                        call_id=str(result.get("call_id") or ""),
                        name=str(result.get("name") or ""),
                        ok=bool(result.get("ok")),
                        content=result.get("content"),
                        metadata=dict(result.get("metadata") or {}),
                    )
                    for result in observation.get("tool_results", [])
                ],
                summary=str(observation.get("summary") or ""),
                remaining_budget=dict(observation.get("remaining_budget") or {}),
            )
            for observation in data.get("observations", [])
        ],
        final_text=str(data.get("final_text") or ""),
        final_status=str(data.get("final_status") or ""),
        progress_chunks=list(data.get("progress_chunks") or []),
        metadata=dict(data.get("metadata") or {}),
    )


def _result_for_call(trace, call_id: str) -> WispToolResult | None:
    """Find the recorded tool result for a call id."""
    for observation in trace.observations:
        for result in observation.tool_results:
            if result.call_id == call_id:
                return result
    return None


def _expected_for_grader(item: OpenAIStyleEvalItem, grader_name: str) -> Any:
    """Return the expected value recorded for an OpenAI Evals match event."""
    if grader_name == "tool_names":
        return [tool.name for tool in item.expected_tools]
    if grader_name == "tool_arguments":
        return [tool.arguments for tool in item.expected_tools]
    if grader_name == "final_answer":
        return item.expected_output_contains
    if grader_name == "recovery":
        return item.require_recovery
    if grader_name == "completion_gate":
        return not item.reject_completion_gate_miss
    return None


def _picked_for_grader(trace, grader_name: str) -> Any:
    """Return the picked value recorded for an OpenAI Evals match event."""
    if grader_name == "tool_names":
        return [call.name for call in trace.tool_calls]
    if grader_name == "tool_arguments":
        return [call.arguments for call in trace.tool_calls]
    if grader_name == "final_answer":
        return trace.final_text
    if grader_name == "recovery":
        return any(
            not result.ok
            for observation in trace.observations
            for result in observation.tool_results
        )
    if grader_name == "completion_gate":
        return bool(trace.metadata.get("completion_gate_missed"))
    return None


def _item_dict(item: OpenAIStyleEvalItem) -> dict[str, Any]:
    """Convert an eval item into JSON-friendly data."""
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
