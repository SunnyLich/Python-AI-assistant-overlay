"""Benchmark whether the unified live chat loop reliably uses tools and answers."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_clients.chat_flow_compare import (  # noqa: E402
    ChatScenario,
    FakeToolExecutor,
    LiveResponsesUnifiedRunner,
    ScenarioFixtures,
    score_trace,
    synthetic_live_fixtures,
    synthetic_live_scenarios,
)
from core.llm_clients.chat_tool_loop import WispToolResult  # noqa: E402
from core.llm_clients.openai_evals_style import (  # noqa: E402
    OpenAIStyleEvalItem,
    OpenAIStyleExpectedTool,
    default_items_by_scenario,
    grade_trace,
)
from core.llm_clients.chat_tool_loop import ChatToolLoop, ChatToolLoopConfig  # noqa: E402


def main() -> int:
    """Run live unified tool reliability trials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default=".tmp/unified_tool_reliability")
    parser.add_argument("--model", default="")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--max-tool-calls", type=int, default=0)
    parser.add_argument("--max-rounds", type=int, default=0)
    args = parser.parse_args()

    runner = _unified_runner(
        args.model or None,
        max_tool_calls=args.max_tool_calls or None,
        max_rounds=args.max_rounds or None,
    )
    scenarios = reliability_scenarios()
    fixtures = reliability_fixtures()
    items = reliability_items()
    generated_at = datetime.now().isoformat(timespec="seconds")
    run_dir = Path(args.output_root) / generated_at.replace(":", "-")
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for trial_index in range(1, args.trials + 1):
        for scenario in scenarios:
            print(
                f"[benchmark] trial {trial_index}/{args.trials} scenario={scenario.name}",
                flush=True,
            )
            trace_path = traces_dir / f"trial-{trial_index:02d}-{scenario.name}.json"
            try:
                trace = runner.run(scenario)
                metrics = score_trace(scenario, trace)
                item = items[scenario.name]
                grade = grade_trace(item, trace)
                reliability = _reliability_result(scenario, metrics, grade, trace)
                trace_path.write_text(json.dumps(asdict(trace), indent=2, ensure_ascii=False), encoding="utf-8")
                row = {
                    "trial": trial_index,
                    "scenario": scenario.name,
                    "prompt": scenario.prompt,
                    "passed": reliability["passed"],
                    "failure_reasons": reliability["failure_reasons"],
                    "score": grade["score"],
                    "graders": grade["graders"],
                    "tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in trace.tool_calls
                    ],
                    "observations": [
                        {
                            "summary": observation.summary,
                            "results": [
                                {
                                    "name": result.name,
                                    "ok": result.ok,
                                    "content": str(result.content),
                                }
                                for result in observation.tool_results
                            ],
                        }
                        for observation in trace.observations
                    ],
                    "final_text": trace.final_text,
                    "completion_gate_missed": bool(trace.metadata.get("completion_gate_missed")),
                    "trace_path": str(trace_path),
                }
            except Exception as exc:  # noqa: BLE001 - benchmark should keep going
                trace_path.write_text(
                    json.dumps(
                        {
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "scenario": scenario.name,
                            "trial": trial_index,
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                row = {
                    "trial": trial_index,
                    "scenario": scenario.name,
                    "prompt": scenario.prompt,
                    "passed": False,
                    "failure_reasons": [f"runner_error:{type(exc).__name__}: {exc}"],
                    "score": 0.0,
                    "graders": {},
                    "tool_calls": [],
                    "observations": [],
                    "final_text": "",
                    "completion_gate_missed": False,
                    "trace_path": str(trace_path),
                }
            rows.append({
                **row,
            })
            print(
                f"[benchmark] done trial {trial_index}/{args.trials} scenario={scenario.name} "
                f"passed={row['passed']} score={row['score']}",
                flush=True,
            )

    summary = _summary(rows, scenarios, args.trials)
    payload = {
        "generated_at": generated_at,
        "model": runner._model,  # noqa: SLF001 - benchmark report only
        "trials": args.trials,
        "summary": summary,
        "rows": rows,
        "notes": [
            "This benchmark uses the live unified Responses chat loop.",
            "Tool results are synthetic fixtures, so no real files are read or modified.",
            "The model still has to choose tools, arguments, recovery steps, and final answer.",
            "Pass requires the expected tool behavior, useful final answer, and no completion-gate miss.",
        ],
    }
    (run_dir / "reliability.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "reliability.md").write_text(_markdown_report(payload), encoding="utf-8")
    print(f"Wrote unified tool reliability benchmark to {run_dir}")
    print(f"Reliability JSON: {run_dir / 'reliability.json'}")
    print(f"Reliability report: {run_dir / 'reliability.md'}")
    return 0


def _unified_runner(
    model: str | None,
    *,
    max_tool_calls: int | None = None,
    max_rounds: int | None = None,
) -> LiveResponsesUnifiedRunner:
    """Build a live unified runner with safe synthetic fixtures."""
    from core.llm_clients import client as llm
    import config

    selected_model = model or config.CHAT_LLM_MODEL or config.LLM_MODEL
    budget = config.tool_turn_budget()
    calls = max(0, int(max_tool_calls if max_tool_calls is not None else budget.max_calls or 0))
    rounds = max(1, int(max_rounds if max_rounds is not None else min(calls + 2, 60)))
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
    return LiveResponsesUnifiedRunner(
        "unified-live-chatgpt",
        llm._get_chat_codex_client(),
        model=selected_model,
        instructions=instructions,
        tools=tools,
        loop=ChatToolLoop(ChatToolLoopConfig(max_rounds=rounds, max_tool_calls=calls)),
        fixtures_by_scenario=reliability_fixtures(),
    )


def reliability_scenarios() -> list[ChatScenario]:
    """Return live-model reliability scenarios."""
    return [
        *synthetic_live_scenarios(),
        ChatScenario(
            name="synthetic_file_edit",
            prompt=(
                "In this synthetic project, change the greeting in app.py from hi to hello. "
                "Use available tools and report what changed."
            ),
            tools=["read_file", "edit_file"],
            expected_relevant_tools=["read_file"],
            expected_change_tools=["edit_file"],
        ),
    ]


def reliability_fixtures() -> dict[str, ScenarioFixtures]:
    """Return synthetic fixtures for reliability scenarios."""
    fixtures = synthetic_live_fixtures()
    fixtures["synthetic_file_edit"] = {
        "read_file": {
            "path=app.py": ["GREETING = 'hi'\nprint(GREETING)\n"],
            "*": [
                WispToolResult(
                    call_id="fixture_missing",
                    name="read_file",
                    ok=False,
                    content="File not found",
                )
            ],
        },
        "edit_file": {
            "path=app.py": ["Updated app.py: replaced hi with hello."],
            "*": ["Updated requested file."],
        },
    }
    return fixtures


def reliability_items() -> dict[str, OpenAIStyleEvalItem]:
    """Return OpenAI-style expected tool behavior for reliability scenarios."""
    items = default_items_by_scenario()
    items["synthetic_file_edit"] = OpenAIStyleEvalItem(
        id="synthetic_file_edit",
        prompt="Change the greeting in app.py from hi to hello.",
        expected_tools=[
            OpenAIStyleExpectedTool("read_file", {"path": "app.py"}),
            OpenAIStyleExpectedTool("edit_file", {"path": "app.py"}),
        ],
        expected_output_contains=["app.py", "hello"],
    )
    return items


def _reliability_result(
    scenario: ChatScenario,
    metrics: dict[str, Any],
    grade: dict[str, Any],
    trace,
) -> dict[str, Any]:
    """Return pass/fail and concrete failure reasons."""
    reasons = []
    if not grade["passed"]:
        for name, value in grade["graders"].items():
            if value < 1.0:
                reasons.append(f"{name}={value}")
    if not metrics["answered_actual_request"]:
        reasons.append("final answer did not satisfy request heuristic")
    if trace.metadata.get("completion_gate_missed"):
        reasons.append("completion gate missed")
    if scenario.expected_change_tools and not metrics["made_allowed_change"]:
        reasons.append("expected edit/write tool did not succeed")
    if scenario.name == "synthetic_tool_recovery" and not metrics["recovered_after_failed_tool"]:
        reasons.append("did not recover after failed read")
    return {
        "passed": not reasons,
        "failure_reasons": reasons,
    }


def _summary(rows: list[dict[str, Any]], scenarios: list[ChatScenario], trials: int) -> dict[str, Any]:
    """Aggregate reliability rows."""
    total = len(rows)
    passed = sum(1 for row in rows if row["passed"])
    by_scenario = {}
    for scenario in scenarios:
        scenario_rows = [row for row in rows if row["scenario"] == scenario.name]
        scenario_passed = sum(1 for row in scenario_rows if row["passed"])
        by_scenario[scenario.name] = {
            "passed": scenario_passed,
            "total": len(scenario_rows),
            "pass_rate": round(scenario_passed / (len(scenario_rows) or 1), 4),
        }
    return {
        "passed": passed,
        "total": total,
        "pass_rate": round(passed / (total or 1), 4),
        "trials": trials,
        "by_scenario": by_scenario,
    }


def _markdown_report(payload: dict[str, Any]) -> str:
    """Render a concise reliability report."""
    lines = [
        "# Unified Tool Reliability Benchmark",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Model: `{payload['model']}`",
        f"Trials: `{payload['trials']}`",
        "",
        "## Summary",
        "",
        f"Overall pass rate: `{payload['summary']['passed']}/{payload['summary']['total']}` (`{payload['summary']['pass_rate']}`)",
        "",
        "| Scenario | Passed | Total | Pass Rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for scenario, stats in payload["summary"]["by_scenario"].items():
        lines.append(f"| `{scenario}` | {stats['passed']} | {stats['total']} | `{stats['pass_rate']}` |")
    lines.extend([
        "",
        "## Runs",
        "",
        "| Trial | Scenario | Pass | Score | Tools | Final Reply | Failure Reasons |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ])
    for row in payload["rows"]:
        tools = ", ".join(call["name"] for call in row["tool_calls"])
        reasons = "; ".join(row["failure_reasons"])
        lines.append(
            "| "
            + " | ".join([
                str(row["trial"]),
                f"`{row['scenario']}`",
                "`pass`" if row["passed"] else "`fail`",
                str(row["score"]),
                _one_line(tools),
                _one_line(row["final_text"]),
                _one_line(reasons),
            ])
            + " |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines)


def _one_line(value: str, limit: int = 160) -> str:
    """Return Markdown-table-safe one-line text."""
    text = " ".join(str(value or "").split()).replace("|", "\\|")
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


if __name__ == "__main__":
    raise SystemExit(main())
