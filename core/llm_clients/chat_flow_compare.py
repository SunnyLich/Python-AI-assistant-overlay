"""Deterministic comparison harness for chat tool-loop behavior."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import html
import json
from pathlib import Path
import threading
import time
from typing import Any, Protocol

from core.llm_clients.chat_tool_loop import (
    ChatLoopModel,
    ChatModelTurn,
    ChatToolLoop,
    ChatToolRequest,
    WispObservation,
    WispToolCall,
    WispToolResult,
)


@dataclass(frozen=True)
class ChatScenario:
    """One behavior scenario to run through current and candidate chat flows."""

    name: str
    prompt: str
    tools: list[str]
    expected_relevant_tools: list[str] = field(default_factory=list)
    expected_change_tools: list[str] = field(default_factory=list)
    expected_verification_tools: list[str] = field(default_factory=list)
    permissions: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatFlowTrace:
    """Comparable trace for one scenario run through one flow."""

    flow: str
    scenario: str
    prompt: str
    tools_offered: list[str]
    tool_calls: list[WispToolCall]
    observations: list[WispObservation]
    final_text: str
    final_status: str
    progress_chunks: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatFlowComparison:
    """Current-vs-unified result for one scenario."""

    scenario: str
    current: dict[str, Any]
    unified: dict[str, Any]
    current_trace: ChatFlowTrace
    unified_trace: ChatFlowTrace


@dataclass(frozen=True)
class ChatFlowComparisonReport:
    """Full comparison report for all scenarios."""

    generated_at: str
    comparisons: list[ChatFlowComparison]

    @property
    def summary(self) -> dict[str, Any]:
        """Return compact aggregate counts for the comparison run."""
        totals = {
            "scenarios": len(self.comparisons),
            "current_relevant_tool_called": 0,
            "unified_relevant_tool_called": 0,
            "current_answered_actual_request": 0,
            "unified_answered_actual_request": 0,
            "current_recovered_after_failed_tool": 0,
            "unified_recovered_after_failed_tool": 0,
            "current_completion_gate_missed": 0,
            "unified_completion_gate_missed": 0,
            "current_verification_attempted": 0,
            "unified_verification_attempted": 0,
        }
        for comparison in self.comparisons:
            if comparison.current.get("relevant_tool_called"):
                totals["current_relevant_tool_called"] += 1
            if comparison.unified.get("relevant_tool_called"):
                totals["unified_relevant_tool_called"] += 1
            if comparison.current.get("answered_actual_request"):
                totals["current_answered_actual_request"] += 1
            if comparison.unified.get("answered_actual_request"):
                totals["unified_answered_actual_request"] += 1
            if comparison.current.get("recovered_after_failed_tool"):
                totals["current_recovered_after_failed_tool"] += 1
            if comparison.unified.get("recovered_after_failed_tool"):
                totals["unified_recovered_after_failed_tool"] += 1
            if comparison.current.get("completion_gate_missed"):
                totals["current_completion_gate_missed"] += 1
            if comparison.unified.get("completion_gate_missed"):
                totals["unified_completion_gate_missed"] += 1
            if comparison.current.get("verification_attempted"):
                totals["current_verification_attempted"] += 1
            if comparison.unified.get("verification_attempted"):
                totals["unified_verification_attempted"] += 1
        return totals


class ChatFlowRunner(Protocol):
    """Runner interface used by the comparison harness."""

    name: str

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run one scenario and return a normalized trace."""


@dataclass(frozen=True)
class ScriptedModelStep:
    """One deterministic fake-model step for harness tests and dry runs."""

    tool_calls: list[WispToolCall] = field(default_factory=list)
    final: str = ""
    status: str = "continue"
    progress: str = ""


ToolFixtureQueue = list[WispToolResult | str]
ToolFixtureMap = dict[str, ToolFixtureQueue]
ScenarioFixtures = dict[str, ToolFixtureQueue | ToolFixtureMap]


class FakeToolExecutor:
    """Deterministic tool executor with per-tool or argument-aware fixture results."""

    def __init__(self, fixtures: ScenarioFixtures | None = None):
        """Initialize fake executor fixtures."""
        self._fixtures: ScenarioFixtures = {}
        for name, results in (fixtures or {}).items():
            if isinstance(results, dict):
                self._fixtures[name] = {key: list(value) for key, value in results.items()}
            else:
                self._fixtures[name] = list(results)

    def execute(self, call: WispToolCall) -> WispToolResult:
        """Execute one fake tool call."""
        fixture = self._fixtures.get(call.name)
        if isinstance(fixture, dict):
            key = _fixture_key(call)
            queue = fixture.get(key)
            if queue is None:
                queue = fixture.get("*") or []
                key = "*"
            value = queue.pop(0) if queue else f"{call.name} completed."
            fixture[key] = queue
        else:
            queue = fixture or []
            value = queue.pop(0) if queue else f"{call.name} completed."
            self._fixtures[call.name] = queue
        if isinstance(value, WispToolResult):
            return replace(value, call_id=call.id, name=call.name)
        return WispToolResult(
            call_id=call.id,
            name=call.name,
            ok=True,
            content=str(value),
        )


def _fixture_key(call: WispToolCall) -> str:
    """Return a stable fixture key for a tool call."""
    if "path" in call.arguments:
        path = str(call.arguments.get("path") or "").replace("\\", "/")
        return f"path={path}"
    if "folder" in call.arguments:
        folder = str(call.arguments.get("folder") or "").replace("\\", "/")
        return f"folder={folder}"
    return json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)


class ScriptedChatFlowRunner:
    """Small deterministic runner that exercises the harness without live LLMs."""

    def __init__(
        self,
        name: str,
        steps_by_scenario: dict[str, list[ScriptedModelStep]],
        *,
        fixtures_by_scenario: dict[str, ScenarioFixtures] | None = None,
    ):
        """Initialize scripted runner."""
        self.name = name
        self._steps_by_scenario = steps_by_scenario
        self._fixtures_by_scenario = fixtures_by_scenario or {}

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run a scenario using scripted model steps and fake tool results."""
        steps = list(self._steps_by_scenario.get(scenario.name) or [])
        executor = FakeToolExecutor(self._fixtures_by_scenario.get(scenario.name))
        tool_calls: list[WispToolCall] = []
        observations: list[WispObservation] = []
        progress_chunks: list[str] = []
        final_text = ""
        final_status = "no_final"
        for step in steps:
            if step.progress:
                progress_chunks.append(step.progress)
            if step.tool_calls:
                results = [executor.execute(call) for call in step.tool_calls]
                tool_calls.extend(step.tool_calls)
                observations.append(
                    WispObservation(
                        tool_results=results,
                        summary=_observation_summary(results),
                        remaining_budget={},
                    )
                )
                continue
            if step.final:
                final_text = step.final
                final_status = step.status or "final"
                break
        return ChatFlowTrace(
            flow=self.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=tool_calls,
            observations=observations,
            final_text=final_text,
            final_status=final_status,
            progress_chunks=progress_chunks,
            metadata={},
        )


class ScriptedChatLoopModel(ChatLoopModel):
    """Scripted model adapter consumed by the provider-neutral loop."""

    def __init__(self, steps: list[ScriptedModelStep]):
        """Initialize the scripted model."""
        self._steps = list(steps)

    def next_turn(
        self,
        _request: ChatToolRequest,
        _observations: list[WispObservation],
        _tool_calls: list[WispToolCall],
    ) -> ChatModelTurn:
        """Return the next scripted model turn."""
        if not self._steps:
            return ChatModelTurn(final_text="Scripted model had no more steps.", status="script_exhausted")
        step = self._steps.pop(0)
        return ChatModelTurn(
            tool_calls=step.tool_calls,
            final_text=step.final,
            status=step.status,
            progress=step.progress,
        )


class LoopBackedScriptedChatFlowRunner:
    """Harness runner that exercises the real provider-neutral ChatToolLoop."""

    def __init__(
        self,
        name: str,
        steps_by_scenario: dict[str, list[ScriptedModelStep]],
        *,
        fixtures_by_scenario: dict[str, ScenarioFixtures] | None = None,
        loop: ChatToolLoop | None = None,
    ):
        """Initialize loop-backed scripted runner."""
        self.name = name
        self._steps_by_scenario = steps_by_scenario
        self._fixtures_by_scenario = fixtures_by_scenario or {}
        self._loop = loop or ChatToolLoop()

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run a scenario through the neutral chat tool loop."""
        request = ChatToolRequest(
            messages=[{"role": "user", "content": scenario.prompt}],
            system_prompt="",
            model_route={"provider": "scripted", "model": self.name},
            tools=[{"name": name} for name in scenario.tools],
            allowed_tools=list(scenario.tools),
            pinned_tools=[],
            permissions=dict(scenario.permissions),
            budgets={},
            ambient_context=str(scenario.context.get("ambient_context") or ""),
            memory_context=str(scenario.context.get("memory_context") or ""),
            screenshot_b64=scenario.context.get("screenshot_b64"),
        )
        final = self._loop.run(
            request,
            ScriptedChatLoopModel(self._steps_by_scenario.get(scenario.name) or []),
            FakeToolExecutor(self._fixtures_by_scenario.get(scenario.name)),
        )
        return ChatFlowTrace(
            flow=self.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=final.tool_calls,
            observations=final.observations,
            final_text=final.text,
            final_status=final.status,
            progress_chunks=list(final.metadata.get("progress_chunks") or []),
            metadata=final.metadata,
        )


class LiveResponsesUnifiedRunner:
    """Run a scenario through the provider-neutral loop using live Responses."""

    def __init__(
        self,
        name: str,
        client,
        *,
        model: str,
        instructions: str,
        tools: list[dict],
        loop: ChatToolLoop | None = None,
        fixtures_by_scenario: dict[str, ScenarioFixtures] | None = None,
    ):
        """Initialize live unified runner."""
        self.name = name
        self._client = client
        self._model = model
        self._instructions = instructions
        self._tools = tools
        self._loop = loop or ChatToolLoop()
        self._fixtures_by_scenario = fixtures_by_scenario or {}

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run one live scenario through the neutral loop."""
        from core.llm_clients.responses_chat_adapter import ResponsesChatLoopModel as RuntimeResponsesChatLoopModel

        scenario_tools = _filter_response_tools(self._tools, scenario.tools)
        request = ChatToolRequest(
            messages=[{"role": "user", "content": scenario.prompt}],
            system_prompt=self._instructions,
            model_route={"provider": "chatgpt", "model": self._model},
            tools=scenario_tools,
            allowed_tools=list(scenario.tools),
            pinned_tools=list(scenario.tools),
            permissions=dict(scenario.permissions),
            budgets={},
        )
        final = self._loop.run(
            request,
            RuntimeResponsesChatLoopModel(
                self._client,
                model=self._model,
                instructions=self._instructions,
                tools=scenario_tools,
            ),
            self._executor_for(scenario),
        )
        return ChatFlowTrace(
            flow=self.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=final.tool_calls,
            observations=final.observations,
            final_text=final.text,
            final_status=final.status,
            progress_chunks=list(final.metadata.get("progress_chunks") or []),
            metadata=final.metadata,
        )

    def _executor_for(self, scenario: ChatScenario):
        """Return synthetic or live executor for this scenario."""
        fixtures = self._fixtures_by_scenario.get(scenario.name)
        if fixtures is not None:
            return FakeToolExecutor(fixtures)
        from core.llm_clients.responses_chat_adapter import LiveModelToolExecutor as RuntimeLiveModelToolExecutor

        return RuntimeLiveModelToolExecutor(allowed_tools=scenario.tools)


class LiveCodexCurrentRunner:
    """Run a scenario through Wisp's existing ChatGPT/Responses chat path."""

    requires_serial_run = True

    def __init__(
        self,
        name: str,
        client,
        *,
        model: str,
        instructions: str = "",
        fixtures_by_scenario: dict[str, ScenarioFixtures] | None = None,
    ):
        """Initialize live current runner."""
        self.name = name
        self._client = client
        self._model = model
        self._instructions = instructions
        self._fixtures_by_scenario = fixtures_by_scenario or {}

    def run(self, scenario: ChatScenario) -> ChatFlowTrace:
        """Run one live scenario through the existing Wisp path and capture tools."""
        from core.llm_clients import client as llm

        tool_calls: list[WispToolCall] = []
        observations: list[WispObservation] = []
        progress_chunks: list[str] = []
        answer_chunks: list[str] = []
        original_execute = llm._execute_model_tool
        call_index = 0
        fake_executor = (
            FakeToolExecutor(self._fixtures_by_scenario[scenario.name])
            if scenario.name in self._fixtures_by_scenario
            else None
        )

        def recording_execute(name: str, inputs: dict, allowed_tools: list[str] | None = None) -> str:
            nonlocal call_index
            call_index += 1
            call = WispToolCall(id=f"current_{call_index}", name=name, arguments=dict(inputs or {}))
            tool_calls.append(call)
            if fake_executor is not None:
                fake_result = fake_executor.execute(call)
                content = fake_result.content if isinstance(fake_result.content, str) else json.dumps(fake_result.content)
            else:
                content = original_execute(name, inputs, allowed_tools=allowed_tools)
            result = WispToolResult(
                call_id=call.id,
                name=name,
                ok=not _looks_like_tool_failure(content),
                content=content,
            )
            observations.append(
                WispObservation(
                    tool_results=[result],
                    summary=_observation_summary([result]),
                    remaining_budget={},
                )
            )
            return content

        try:
            llm._execute_model_tool = recording_execute
            for chunk in llm._stream_codex(
                scenario.prompt,
                self._model,
                self._client,
                use_tools=True,
                allowed_tools=scenario.tools,
                pinned_tools=scenario.tools,
                system_prompt=self._instructions or None,
            ):
                if getattr(chunk, "kind", "answer") == "progress":
                    progress_chunks.append(str(chunk))
                else:
                    answer_chunks.append(str(chunk))
        finally:
            llm._execute_model_tool = original_execute
        return ChatFlowTrace(
            flow=self.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=tool_calls,
            observations=observations,
            final_text="".join(answer_chunks),
            final_status="final",
            progress_chunks=progress_chunks,
            metadata={},
        )


def compare_chat_flows(
    scenarios: list[ChatScenario],
    current_runner: ChatFlowRunner,
    unified_runner: ChatFlowRunner,
    *,
    parallel: bool = True,
    max_workers: int | None = None,
) -> ChatFlowComparisonReport:
    """Run scenarios through both flows and return metrics plus traces."""
    if parallel:
        return _compare_chat_flows_parallel(
            scenarios,
            current_runner,
            unified_runner,
            max_workers=max_workers,
        )
    comparisons: list[ChatFlowComparison] = []
    for scenario in scenarios:
        current_trace = _run_or_error_trace(current_runner, scenario)
        unified_trace = _run_or_error_trace(unified_runner, scenario)
        comparisons.append(
            ChatFlowComparison(
                scenario=scenario.name,
                current=score_trace(scenario, current_trace),
                unified=score_trace(scenario, unified_trace),
                current_trace=current_trace,
                unified_trace=unified_trace,
            )
        )
    return ChatFlowComparisonReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        comparisons=comparisons,
    )


_SERIAL_RUN_LOCK = threading.Lock()


def _compare_chat_flows_parallel(
    scenarios: list[ChatScenario],
    current_runner: ChatFlowRunner,
    unified_runner: ChatFlowRunner,
    *,
    max_workers: int | None = None,
) -> ChatFlowComparisonReport:
    """Run flow/scenario cells concurrently and gather them into one report."""
    if not scenarios:
        return ChatFlowComparisonReport(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            comparisons=[],
        )
    workers = max_workers or min(8, max(1, len(scenarios) * 2))
    futures = {}
    traces: dict[tuple[str, str], ChatFlowTrace] = {}
    started_at = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chat-flow") as executor:
        for scenario in scenarios:
            futures[executor.submit(_run_trace_cell, "current", current_runner, scenario)] = ("current", scenario.name)
            futures[executor.submit(_run_trace_cell, "unified", unified_runner, scenario)] = ("unified", scenario.name)
        for future in as_completed(futures):
            flow_key, scenario_name = futures[future]
            traces[(flow_key, scenario_name)] = future.result()

    comparisons: list[ChatFlowComparison] = []
    for scenario in scenarios:
        current_trace = traces[("current", scenario.name)]
        unified_trace = traces[("unified", scenario.name)]
        comparisons.append(
            ChatFlowComparison(
                scenario=scenario.name,
                current=score_trace(scenario, current_trace),
                unified=score_trace(scenario, unified_trace),
                current_trace=current_trace,
                unified_trace=unified_trace,
            )
        )
    report = ChatFlowComparisonReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        comparisons=comparisons,
    )
    for comparison in report.comparisons:
        comparison.current_trace.metadata.setdefault("comparison_parallel", True)
        comparison.current_trace.metadata.setdefault("comparison_elapsed_seconds", round(time.monotonic() - started_at, 3))
        comparison.unified_trace.metadata.setdefault("comparison_parallel", True)
        comparison.unified_trace.metadata.setdefault("comparison_elapsed_seconds", round(time.monotonic() - started_at, 3))
    return report


def _run_trace_cell(flow_key: str, runner: ChatFlowRunner, scenario: ChatScenario) -> ChatFlowTrace:
    """Run one flow/scenario cell with timing and unsafe-run locking."""
    started_at = time.monotonic()
    if getattr(runner, "requires_serial_run", False):
        with _SERIAL_RUN_LOCK:
            trace = _run_or_error_trace(runner, scenario)
    else:
        trace = _run_or_error_trace(runner, scenario)
    trace.metadata.setdefault("flow_key", flow_key)
    trace.metadata.setdefault("duration_seconds", round(time.monotonic() - started_at, 3))
    return trace


def _run_or_error_trace(runner: ChatFlowRunner, scenario: ChatScenario) -> ChatFlowTrace:
    """Run a flow, converting exceptions into traceable comparison output."""
    try:
        return runner.run(scenario)
    except Exception as exc:  # noqa: BLE001 - harness must record flow failures
        return ChatFlowTrace(
            flow=runner.name,
            scenario=scenario.name,
            prompt=scenario.prompt,
            tools_offered=list(scenario.tools),
            tool_calls=[],
            observations=[],
            final_text=f"{type(exc).__name__}: {exc}",
            final_status="runner_error",
            metadata={"error_type": type(exc).__name__, "error": str(exc)},
        )


def score_trace(scenario: ChatScenario, trace: ChatFlowTrace) -> dict[str, Any]:
    """Score one trace against the scenario's behavioral checkpoints."""
    called_names = [call.name for call in trace.tool_calls]
    relevant = set(scenario.expected_relevant_tools)
    change_tools = set(scenario.expected_change_tools)
    verification_tools = set(scenario.expected_verification_tools)
    first_relevant_turn = None
    for idx, call in enumerate(trace.tool_calls, start=1):
        if call.name in relevant:
            first_relevant_turn = idx
            break
    return {
        "tool_calls_total": len(trace.tool_calls),
        "relevant_tool_called": bool(relevant and relevant.intersection(called_names)),
        "first_relevant_tool_turn": first_relevant_turn,
        "final_after_observation": bool(trace.final_text and trace.observations),
        "completion_gate_missed": bool(trace.metadata.get("completion_gate_missed")),
        "permission_boundary_reported": _permission_boundary_reported(trace),
        "verification_attempted": bool(verification_tools.intersection(called_names)),
        "made_allowed_change": _made_allowed_change(trace, change_tools),
        "relevant_tool_succeeded": _relevant_tool_succeeded(trace, relevant),
        "failed_tool_observed": _failed_tool_observed(trace),
        "recovered_after_failed_tool": _recovered_after_failed_tool(trace),
        "answered_actual_request": _answered_actual_request(scenario, trace),
        "hallucinated_context": bool(trace.metadata.get("hallucinated_context")),
        "final_status": trace.final_status,
        "final_text": trace.final_text,
    }


def write_comparison_artifacts(
    report: ChatFlowComparisonReport,
    output_root: str | Path,
    *,
    report_title: str = "Chat Flow Comparison",
) -> Path:
    """Write comparison traces and summaries to a timestamped artifact folder."""
    root = Path(output_root)
    run_dir = root / report.generated_at.replace(":", "-")
    current_dir = run_dir / "current"
    unified_dir = run_dir / "unified"
    current_dir.mkdir(parents=True, exist_ok=True)
    unified_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(
        json.dumps(report.summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    openai_eval_scores = _openai_eval_scores(report)
    (run_dir / "openai_eval_scores.json").write_text(
        json.dumps(openai_eval_scores, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    from core.llm_clients.openai_evals_harness import build_harness_matrix, run_openai_evals_package

    openai_package_report = run_openai_evals_package(report, run_dir / "openai_evals_package")
    (run_dir / "openai_evals_package_report.json").write_text(
        json.dumps(openai_package_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "harness_matrix.json").write_text(
        json.dumps(build_harness_matrix(report, openai_package_report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "openai_eval_spec.json").write_text(
        json.dumps(_openai_eval_spec(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "results.json").write_text(
        json.dumps(_report_dict(report, openai_package_report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    scenario_payload = [
        {
            "scenario": comparison.scenario,
            "current": comparison.current,
            "unified": comparison.unified,
        }
        for comparison in report.comparisons
    ]
    (run_dir / "scenarios.json").write_text(
        json.dumps(scenario_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for comparison in report.comparisons:
        (current_dir / f"{comparison.scenario}.json").write_text(
            json.dumps(_trace_dict(comparison.current_trace), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (unified_dir / f"{comparison.scenario}.json").write_text(
            json.dumps(_trace_dict(comparison.unified_trace), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    (run_dir / "report.md").write_text(render_markdown_report(report, title=report_title), encoding="utf-8")
    (run_dir / "report.html").write_text(render_html_report(report, title=report_title), encoding="utf-8")
    return run_dir


def render_markdown_report(report: ChatFlowComparisonReport, *, title: str = "Chat Flow Comparison") -> str:
    """Render a compact human-readable comparison report."""
    lines = [
        f"# {title}",
        "",
        f"Generated: {report.generated_at}",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(report.summary, indent=2, ensure_ascii=False),
        "```",
        "",
        "## Scenarios",
        "",
    ]
    for comparison in report.comparisons:
        current_final = _one_line(comparison.current["final_text"])
        unified_final = _one_line(comparison.unified["final_text"])
        lines.extend(
            [
                f"### {comparison.scenario}",
                "",
                "| Checkpoint | Current Flow | Unified Flow |",
                "| --- | --- | --- |",
                f"| Final status | `{comparison.current['final_status']}` | `{comparison.unified['final_status']}` |",
                f"| Tool calls | `{comparison.current['tool_calls_total']}` | `{comparison.unified['tool_calls_total']}` |",
                f"| Relevant tool called | `{comparison.current['relevant_tool_called']}` | `{comparison.unified['relevant_tool_called']}` |",
                f"| Relevant tool succeeded | `{comparison.current['relevant_tool_succeeded']}` | `{comparison.unified['relevant_tool_succeeded']}` |",
                f"| Recovered after failed tool | `{comparison.current['recovered_after_failed_tool']}` | `{comparison.unified['recovered_after_failed_tool']}` |",
                f"| Answered actual request | `{comparison.current['answered_actual_request']}` | `{comparison.unified['answered_actual_request']}` |",
                f"| Verification attempted | `{comparison.current['verification_attempted']}` | `{comparison.unified['verification_attempted']}` |",
                f"| Completion gate missed | `{comparison.current['completion_gate_missed']}` | `{comparison.unified['completion_gate_missed']}` |",
                f"| Final answer excerpt | {current_final} | {unified_final} |",
                "",
            ]
        )
    return "\n".join(lines)


def _one_line(text: str, limit: int = 160) -> str:
    """Return escaped one-line text for Markdown tables."""
    value = " ".join(str(text or "").split())
    value = value.replace("|", "\\|")
    if len(value) > limit:
        return f"{value[:limit].rstrip()}..."
    return value or "(empty)"


def render_html_report(report: ChatFlowComparisonReport, *, title: str = "Chat Flow Comparison") -> str:
    """Render an inspectable side-by-side HTML comparison report."""
    scenario_cards = []
    for comparison in report.comparisons:
        scenario_cards.append(
            f"""
            <section class="scenario">
              <h2>{html.escape(comparison.scenario)}</h2>
              <div class="grid">
                {render_html_flow_panel("Current Flow", comparison.current)}
                {render_html_flow_panel("Unified Flow", comparison.unified)}
              </div>
            </section>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #202124; background: #f7f7f8; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    .meta {{ color: #5f6368; margin-bottom: 20px; }}
    .summary, .scenario {{ background: #fff; border: 1px solid #dadce0; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .panel {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; background: #fcfcfd; }}
    .panel h3 {{ font-size: 15px; margin: 0 0 10px; }}
    dl {{ display: grid; grid-template-columns: 180px 1fr; gap: 8px 10px; margin: 0; }}
    dt {{ color: #5f6368; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    pre {{ white-space: pre-wrap; background: #f1f3f4; border-radius: 6px; padding: 10px; max-height: 220px; overflow: auto; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="meta">Generated: {html.escape(report.generated_at)}</div>
  <section class="summary">
    <h2>Summary</h2>
    <pre>{html.escape(json.dumps(report.summary, indent=2, ensure_ascii=False))}</pre>
  </section>
  {''.join(scenario_cards)}
</body>
</html>
"""


def render_html_flow_panel(title: str, metrics: dict[str, Any]) -> str:
    """Render one flow panel for the HTML report."""
    fields = [
        ("Final status", metrics["final_status"]),
        ("Tool calls", metrics["tool_calls_total"]),
        ("Relevant tool called", metrics["relevant_tool_called"]),
        ("Relevant tool succeeded", metrics["relevant_tool_succeeded"]),
        ("Recovered after failure", metrics["recovered_after_failed_tool"]),
        ("Answered request", metrics["answered_actual_request"]),
        ("Verification", metrics["verification_attempted"]),
        ("Gate missed", metrics["completion_gate_missed"]),
    ]
    items = "\n".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(value))}</dd>"
        for label, value in fields
    )
    final_text = html.escape(str(metrics.get("final_text") or ""))
    return f"""
      <div class="panel">
        <h3>{html.escape(title)}</h3>
        <dl>{items}</dl>
        <h3>Final Answer</h3>
        <pre>{final_text}</pre>
      </div>
    """


def live_chatgpt_runners(
    model: str | None = None,
    *,
    synthetic_tools: bool = False,
) -> tuple[LiveCodexCurrentRunner, LiveResponsesUnifiedRunner]:
    """Build live current/unified runners for ChatGPT Responses comparison."""
    from core.llm_clients import client as llm
    import config

    selected_model = model or config.CHAT_LLM_MODEL or config.LLM_MODEL
    allowed_tools = ["list_files", "read_file", "edit_file", "write_file", "memory_search"]
    tools = llm._get_responses_tool_schemas(
        "",
        include_general=True,
        allowed_tools=allowed_tools,
        pinned_tools=allowed_tools,
    )
    instructions = llm._with_local_file_tools_note(
        llm._with_tools_note(config.get_system_prompt(), True),
        allowed_tools,
    )
    client = llm._get_chat_codex_client()
    fixtures = synthetic_live_fixtures() if synthetic_tools else {}
    return (
        LiveCodexCurrentRunner(
            "current-live-chatgpt",
            client,
            model=selected_model,
            instructions=instructions,
            fixtures_by_scenario=fixtures,
        ),
        LiveResponsesUnifiedRunner(
            "unified-live-chatgpt",
            client,
            model=selected_model,
            instructions=instructions,
            tools=tools,
            fixtures_by_scenario=fixtures,
        ),
    )


def synthetic_live_scenarios() -> list[ChatScenario]:
    """Return live-model scenarios that use only synthetic tool data."""
    return [
        ChatScenario(
            name="synthetic_file_context",
            prompt=(
                "In this synthetic project, what does the app use for settings storage? "
                "Use available tools if you need context."
            ),
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        ),
        ChatScenario(
            name="synthetic_tool_recovery",
            prompt="Read notes.md and summarize it. Use available tools if needed.",
            tools=["read_file", "list_files"],
            expected_relevant_tools=["read_file", "list_files"],
        ),
    ]


def synthetic_live_fixtures() -> dict[str, ScenarioFixtures]:
    """Return synthetic tool outputs for safe live-model comparisons."""
    return {
        "synthetic_file_context": {
            "list_files": ["config.py\napp.py\nREADME.md"],
            "read_file": {
                "path=config.py": ["SETTINGS_STORAGE = 'json-file'\nSETTINGS_PATH = 'settings.json'\n"],
                "path=app.py": ["from config import SETTINGS_PATH, SETTINGS_STORAGE\n"],
                "path=README.md": ["Synthetic project README.\n"],
                "*": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found",
                    )
                ],
            },
        },
        "synthetic_tool_recovery": {
            "read_file": {
                "path=notes.md": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found: notes.md",
                    )
                ],
                "path=docs/notes.md": [
                    "Notes: The project stores settings in settings.json and loads them at startup.",
                ],
                "path=README.md": ["README: see docs/notes.md for project notes."],
                "*": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found",
                    )
                ],
            },
            "list_files": ["docs/notes.md\nREADME.md"],
        },
    }


def _filter_response_tools(tools: list[dict], allowed_tool_names: list[str]) -> list[dict]:
    """Return Responses tool schemas allowed for one scenario."""
    allowed = set(allowed_tool_names)
    filtered = []
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name and isinstance(tool.get("function"), dict):
            name = str(tool["function"].get("name") or "")
        if name in allowed:
            filtered.append(tool)
    return filtered


def _observation_summary(results: list[WispToolResult]) -> str:
    """Build a compact observation summary."""
    if not results:
        return "No tool results."
    parts = []
    for result in results:
        status = "ok" if result.ok else "failed"
        parts.append(f"{result.name}: {status}")
    return "; ".join(parts)


def _looks_like_tool_failure(content: str) -> bool:
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


def _made_allowed_change(trace: ChatFlowTrace, change_tools: set[str]) -> bool:
    """Return whether a mutating expected tool succeeded."""
    if not change_tools:
        return False
    for observation in trace.observations:
        for result in observation.tool_results:
            if result.name in change_tools and result.ok:
                return True
    return False


def _relevant_tool_succeeded(trace: ChatFlowTrace, relevant_tools: set[str]) -> bool:
    """Return whether a relevant expected tool produced an ok result."""
    if not relevant_tools:
        return False
    for observation in trace.observations:
        for result in observation.tool_results:
            if result.name in relevant_tools and result.ok:
                return True
    return False


def _failed_tool_observed(trace: ChatFlowTrace) -> bool:
    """Return whether any tool result failed."""
    return any(
        not result.ok
        for observation in trace.observations
        for result in observation.tool_results
    )


def _recovered_after_failed_tool(trace: ChatFlowTrace) -> bool:
    """Return whether a later successful tool call followed a failed tool result."""
    saw_failure = False
    for observation in trace.observations:
        for result in observation.tool_results:
            if saw_failure and result.ok:
                return True
            if not result.ok:
                saw_failure = True
    return False


def _answered_actual_request(scenario: ChatScenario, trace: ChatFlowTrace) -> bool:
    """Conservative heuristic for whether the final text answered the scenario."""
    if trace.final_status == "runner_error" or not trace.final_text.strip():
        return False
    text = trace.final_text.lower()
    if scenario.name in {"synthetic_file_context", "needs_file_context"}:
        return "settings" in text and ("json" in text or "settings.json" in text or "storage" in text)
    if scenario.name == "synthetic_tool_recovery":
        return "settings.json" in text or ("settings" in text and "startup" in text)
    if scenario.expected_change_tools:
        return any(word in text for word in ("fixed", "changed", "updated", "created"))
    if _failed_tool_observed(trace) and not _recovered_after_failed_tool(trace):
        return False
    return True


def _permission_boundary_reported(trace: ChatFlowTrace) -> bool:
    """Return whether trace evidence or final text reports a permission boundary."""
    text = trace.final_text.lower()
    if "permission" in text or "disabled" in text or "not allowed" in text:
        return True
    for observation in trace.observations:
        for result in observation.tool_results:
            metadata = result.metadata or {}
            if metadata.get("permission_denied") or metadata.get("error_type") == "permission_disabled":
                return True
    return False


def _trace_dict(trace: ChatFlowTrace) -> dict[str, Any]:
    """Convert trace dataclasses to JSON-friendly dictionaries."""
    return asdict(trace)


def _report_dict(
    report: ChatFlowComparisonReport,
    openai_package_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a full comparison report into one consolidated JSON payload."""
    openai_scores = _openai_eval_scores(report)
    return {
        "generated_at": report.generated_at,
        "summary": report.summary,
        "openai_eval_scores": openai_scores,
        "openai_evals_package": openai_package_report or {},
        "comparisons": [
            {
                "scenario": comparison.scenario,
                "current": comparison.current,
                "unified": comparison.unified,
                "openai_eval": openai_scores.get(comparison.scenario),
                "current_trace": _trace_dict(comparison.current_trace),
                "unified_trace": _trace_dict(comparison.unified_trace),
            }
            for comparison in report.comparisons
        ],
    }


def _openai_eval_scores(report: ChatFlowComparisonReport) -> dict[str, Any]:
    """Return OpenAI-Evals-style scores for scenarios with configured items."""
    from core.llm_clients.openai_evals_style import default_items_by_scenario, grade_comparison

    items = default_items_by_scenario()
    scores = {}
    for comparison in report.comparisons:
        item = items.get(comparison.scenario)
        if item is None:
            continue
        scores[comparison.scenario] = grade_comparison(item, comparison.current_trace, comparison.unified_trace)
    return scores


def _openai_eval_spec(report: ChatFlowComparisonReport) -> dict[str, Any]:
    """Return an OpenAI-Evals-style spec for scenarios present in a report."""
    from core.llm_clients.openai_evals_style import default_items_by_scenario, eval_spec

    items_by_scenario = default_items_by_scenario()
    items = [
        items_by_scenario[comparison.scenario]
        for comparison in report.comparisons
        if comparison.scenario in items_by_scenario
    ]
    return eval_spec(items)


def sample_harness_self_test_scenarios() -> list[ChatScenario]:
    """Return scripted scenarios used to self-test the harness plumbing."""
    return [
        ChatScenario(
            name="needs_file_context",
            prompt="What does this project use for settings storage?",
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        ),
        ChatScenario(
            name="edit_plus_verification",
            prompt="Fix the syntax error in app.py and verify it.",
            tools=["read_file", "edit_file", "run_command"],
            expected_relevant_tools=["read_file"],
            expected_change_tools=["edit_file"],
            expected_verification_tools=["run_command"],
        ),
        ChatScenario(
            name="permission_boundary",
            prompt="Delete old.log.",
            tools=[],
            expected_relevant_tools=[],
            permissions={"delete_file": "disabled"},
        ),
    ]
