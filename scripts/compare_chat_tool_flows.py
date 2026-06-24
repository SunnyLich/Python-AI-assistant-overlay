"""Run scripted harness self-tests or live chat-flow smoke comparisons."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_clients.chat_flow_compare import (
    ChatScenario,
    LoopBackedScriptedChatFlowRunner,
    ScriptedChatFlowRunner,
    ScriptedModelStep,
    compare_chat_flows,
    live_chatgpt_runners,
    sample_harness_self_test_scenarios,
    synthetic_live_scenarios,
    write_comparison_artifacts,
)
from core.llm_clients.chat_tool_loop import WispToolCall


def main() -> int:
    """Run the harness self-test or live comparison and write artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        default="chat_flow_harness_runs",
        help="Folder where harness artifacts should be written.",
    )
    parser.add_argument(
        "--live-chatgpt",
        action="store_true",
        help="Run a real ChatGPT/Responses smoke comparison instead of the scripted harness self-test.",
    )
    parser.add_argument(
        "--real-tools",
        action="store_true",
        help="With --live-chatgpt, execute real local Wisp tools instead of synthetic safe fixtures.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional ChatGPT model override for --live-chatgpt.",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Run flow/scenario cells serially instead of the default parallel harness mode.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=0,
        help="Maximum parallel workers. Defaults to min(8, scenarios * 2).",
    )
    args = parser.parse_args()

    if args.live_chatgpt:
        if args.real_tools:
            scenarios = [
                ChatScenario(
                    name="live_file_context",
                    prompt="What does this project use for settings storage? Use the available file tools if needed.",
                    tools=["list_files", "read_file"],
                    expected_relevant_tools=["list_files", "read_file"],
                ),
                ChatScenario(
                    name="live_memory",
                    prompt="What do you remember about how I like answers?",
                    tools=["memory_search"],
                    expected_relevant_tools=["memory_search"],
                ),
            ]
        else:
            scenarios = synthetic_live_scenarios()
        current, unified = live_chatgpt_runners(args.model or None, synthetic_tools=not args.real_tools)
        report = compare_chat_flows(
            scenarios,
            current,
            unified,
            parallel=not args.serial,
            max_workers=args.max_workers or None,
        )
        run_dir = write_comparison_artifacts(report, args.output_root, report_title="Live Chat Flow Smoke Comparison")
        print(f"Wrote live chat flow smoke-comparison artifacts to {run_dir}")
        print(f"Consolidated results: {run_dir / 'results.json'}")
        print(f"Harness matrix: {run_dir / 'harness_matrix.json'}")
        print(f"OpenAI Evals package report: {run_dir / 'openai_evals_package_report.json'}")
        return 0

    scenarios = sample_harness_self_test_scenarios()
    current = ScriptedChatFlowRunner(
        "current",
        {
            "needs_file_context": [
                ScriptedModelStep(final="It likely uses config.py.", status="answered_without_tools"),
            ],
            "edit_plus_verification": [
                ScriptedModelStep(
                    tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "app.py"})]
                ),
                ScriptedModelStep(final="I found the syntax issue.", status="answered_without_editing"),
            ],
            "permission_boundary": [
                ScriptedModelStep(final="Delete is disabled by permissions.", status="blocked"),
            ],
        },
    )
    unified = LoopBackedScriptedChatFlowRunner(
        "unified",
        {
            "needs_file_context": [
                ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                ScriptedModelStep(
                    tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "config.py"})]
                ),
                ScriptedModelStep(final="Settings storage is defined from config.py.", status="handled"),
            ],
            "edit_plus_verification": [
                ScriptedModelStep(
                    tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "app.py"})]
                ),
                ScriptedModelStep(
                    tool_calls=[
                        WispToolCall(
                            id="edit_1",
                            name="edit_file",
                            arguments={"path": "app.py", "old": "bad", "new": "good"},
                        )
                    ]
                ),
                ScriptedModelStep(
                    tool_calls=[
                        WispToolCall(
                            id="verify_1",
                            name="run_command",
                            arguments={"args": ["python", "-m", "py_compile", "app.py"]},
                        )
                    ]
                ),
                ScriptedModelStep(final="Fixed app.py and verified it.", status="handled"),
            ],
            "permission_boundary": [
                ScriptedModelStep(final="Delete is disabled by permissions.", status="blocked"),
            ],
        },
    )

    report = compare_chat_flows(
        scenarios,
        current,
        unified,
        parallel=not args.serial,
        max_workers=args.max_workers or None,
    )
    run_dir = write_comparison_artifacts(report, args.output_root, report_title="Scripted Harness Self-Test")
    print(f"Wrote scripted harness self-test artifacts to {run_dir}")
    print(f"Consolidated results: {run_dir / 'results.json'}")
    print(f"Harness matrix: {run_dir / 'harness_matrix.json'}")
    print(f"OpenAI Evals package report: {run_dir / 'openai_evals_package_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
