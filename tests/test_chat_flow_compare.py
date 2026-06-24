"""Tests for chat flow comparison harness."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import unittest
from unittest import mock

from core.llm_clients import client as llm
from core.llm_clients import chat_flow_compare as compare
from core.llm_clients.anthropic_chat_adapter import AnthropicChatLoopModel
from core.llm_clients.chat_flow_compare import (
    ChatScenario,
    FakeToolExecutor,
    LoopBackedScriptedChatFlowRunner,
    ScriptedChatFlowRunner,
    ScriptedModelStep,
    compare_chat_flows,
    write_comparison_artifacts,
)
from core.llm_clients.openai_evals_style import (
    OpenAIStyleEvalItem,
    OpenAIStyleExpectedTool,
    grade_trace,
)
from core.llm_clients.responses_chat_adapter import ResponsesChatLoopModel
from core.llm_clients.chat_tool_loop import (
    ChatToolLoop,
    ChatToolLoopConfig,
    ChatToolRequest,
    WispObservation,
    WispToolCall,
    WispToolResult,
)


class ChatToolLoopTypesTests(unittest.TestCase):
    """Test provider-neutral chat tool loop contracts."""

    def test_chat_tool_request_keeps_provider_neutral_fields(self):
        """Verify provider-neutral request can describe one chat route."""
        request = ChatToolRequest(
            messages=[{"role": "user", "content": "read app.py"}],
            system_prompt="system",
            model_route={"provider": "chatgpt", "model": "gpt-test"},
            tools=[{"name": "read_file"}],
            allowed_tools=["read_file"],
            pinned_tools=["read_file"],
            permissions={"file_read": "auto"},
            budgets={"max_calls": 3},
        )

        self.assertEqual(request.model_route["provider"], "chatgpt")
        self.assertEqual(request.allowed_tools, ["read_file"])
        self.assertIsNone(request.screenshot_b64)

    def test_codex_chat_can_use_current_tool_loop_when_flag_disabled(self):
        """Verify the feature flag can route Responses tools through the old loop."""
        with (
            mock.patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "0"}, clear=False),
            mock.patch.object(llm, "_get_responses_tool_schemas", return_value=[{"name": "read_file"}]),
            mock.patch.object(llm, "_log_offered_model_tools"),
            mock.patch.object(llm, "_run_responses_tool_loop", return_value=iter(["current"])),
            mock.patch.object(llm, "_run_unified_responses_tool_loop", return_value=iter(["unified"])),
        ):
            chunks = list(llm._stream_codex("read app.py", "gpt-test", object(), use_tools=True))

        self.assertEqual(chunks, ["current"])

    def test_codex_chat_uses_unified_tool_loop_by_default(self):
        """Verify chat tools default to the unified loop."""
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(llm.config, "UNIFIED_CHAT_TOOL_LOOP", True),
            mock.patch.object(llm, "_get_responses_tool_schemas", return_value=[{"name": "read_file"}]),
            mock.patch.object(llm, "_log_offered_model_tools"),
            mock.patch.object(llm, "_run_responses_tool_loop", return_value=iter(["current"])),
            mock.patch.object(llm, "_run_unified_responses_tool_loop", return_value=iter(["unified"])),
        ):
            chunks = list(llm._stream_codex("read app.py", "gpt-test", object(), use_tools=True))

        self.assertEqual(chunks, ["unified"])

    def test_codex_chat_uses_unified_tool_loop_when_flag_enabled(self):
        """Verify the feature flag routes Responses tools through the unified loop."""
        with (
            mock.patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "1"}, clear=False),
            mock.patch.object(llm, "_get_responses_tool_schemas", return_value=[{"name": "read_file"}]),
            mock.patch.object(llm, "_log_offered_model_tools"),
            mock.patch.object(llm, "_run_responses_tool_loop", return_value=iter(["current"])),
            mock.patch.object(llm, "_run_unified_responses_tool_loop", return_value=iter(["unified"])),
        ):
            chunks = list(llm._stream_codex("read app.py", "gpt-test", object(), use_tools=True))

        self.assertEqual(chunks, ["unified"])

    def test_live_runner_filters_response_tools_to_scenario_tools(self):
        """Verify live comparison scenarios do not expose unrelated tools."""
        tools = [
            {"name": "list_files"},
            {"name": "read_file"},
            {"name": "edit_file"},
            {"type": "function", "function": {"name": "write_file"}},
        ]

        filtered = compare._filter_response_tools(tools, ["list_files", "read_file"])

        self.assertEqual([tool.get("name") for tool in filtered], ["list_files", "read_file"])

    def test_openai_style_grader_checks_tool_arguments(self):
        """Verify OpenAI-style grading fails wrong tool arguments."""
        item = OpenAIStyleEvalItem(
            id="arg_check",
            prompt="Read config.py",
            expected_tools=[OpenAIStyleExpectedTool("read_file", {"path": "config.py"})],
            expected_output_contains=["settings.json"],
        )
        wrong_trace = compare.ChatFlowTrace(
            flow="wrong",
            scenario="arg_check",
            prompt=item.prompt,
            tools_offered=["read_file"],
            tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "README.md"})],
            observations=[],
            final_text="settings.json",
            final_status="final",
        )
        right_trace = compare.ChatFlowTrace(
            flow="right",
            scenario="arg_check",
            prompt=item.prompt,
            tools_offered=["read_file"],
            tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "config.py"})],
            observations=[],
            final_text="settings.json",
            final_status="final",
        )

        wrong = grade_trace(item, wrong_trace)
        right = grade_trace(item, right_trace)

        self.assertEqual(wrong["graders"]["tool_names"], 1.0)
        self.assertEqual(wrong["graders"]["tool_arguments"], 0.0)
        self.assertFalse(wrong["passed"])
        self.assertTrue(right["passed"])

    def test_openai_style_grader_allows_extra_same_name_tool_before_expected_args(self):
        """Verify argument grading can find the later correct same-name tool call."""
        item = OpenAIStyleEvalItem(
            id="arg_check_extra",
            prompt="Find settings storage",
            expected_tools=[
                OpenAIStyleExpectedTool("list_files"),
                OpenAIStyleExpectedTool("read_file", {"path": "config.py"}),
            ],
            expected_output_contains=["settings.json"],
        )
        trace = compare.ChatFlowTrace(
            flow="extra",
            scenario="arg_check_extra",
            prompt=item.prompt,
            tools_offered=["list_files", "read_file"],
            tool_calls=[
                WispToolCall(id="list_1", name="list_files"),
                WispToolCall(id="read_1", name="read_file", arguments={"path": "app.py"}),
                WispToolCall(id="read_2", name="read_file", arguments={"path": "config.py"}),
            ],
            observations=[],
            final_text="settings.json",
            final_status="final",
        )

        grade = grade_trace(item, trace)

        self.assertEqual(grade["graders"]["tool_names"], 1.0)
        self.assertEqual(grade["graders"]["tool_arguments"], 1.0)
        self.assertTrue(grade["passed"])

    def test_anthropic_adapter_round_trips_tool_results(self):
        """Verify Anthropic adapter sends tool_result observations after tool_use."""
        class FakeMessages:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return type("Response", (), {
                        "content": [
                            type("Block", (), {"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {"path": "app.py"}})()
                        ]
                    })()
                return type("Response", (), {
                    "content": [
                        type("Block", (), {"type": "text", "text": "Read app.py."})()
                    ]
                })()

        fake_messages = FakeMessages()
        fake_client = type("Client", (), {"messages": fake_messages})()
        model = AnthropicChatLoopModel(
            fake_client,
            model="claude-test",
            system="system",
            messages=[{"role": "user", "content": "read app.py"}],
            tools=[{"name": "read_file"}],
            max_tokens=256,
        )
        request = ChatToolRequest(
            messages=[{"role": "user", "content": "read app.py"}],
            system_prompt="system",
            model_route={"provider": "anthropic", "model": "claude-test"},
            tools=[{"name": "read_file"}],
            allowed_tools=["read_file"],
            pinned_tools=["read_file"],
            permissions={},
            budgets={},
        )

        turn = model.next_turn(request, [], [])
        followup = model.next_turn(
            request,
            [WispObservation(
                tool_results=[WispToolResult(call_id="tool_1", name="read_file", ok=True, content="contents")],
                summary="read_file: ok",
            )],
            turn.tool_calls,
        )

        self.assertEqual(turn.tool_calls[0].name, "read_file")
        self.assertEqual(followup.final_text, "Read app.py.")
        tool_result_message = fake_messages.calls[1]["messages"][-1]
        self.assertEqual(tool_result_message["content"][0]["tool_use_id"], "tool_1")

    def test_anthropic_chat_uses_unified_tool_loop_when_flag_enabled(self):
        """Verify Anthropic tools route through unified loop under the flag."""
        with (
            mock.patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "1"}, clear=False),
            mock.patch.object(llm, "_get_tool_schemas", return_value=[{"name": "read_file"}]),
            mock.patch.object(llm, "_log_offered_model_tools"),
            mock.patch.object(llm, "_run_unified_anthropic_tool_loop", return_value=iter(["unified"])),
        ):
            chunks = list(
                llm._stream_anthropic(
                    "read app.py",
                    None,
                    "claude-test",
                    object(),
                    use_tools=True,
                    allowed_tools=["read_file"],
                )
            )

        self.assertEqual(chunks, ["unified"])


class ChatFlowCompareTests(unittest.TestCase):
    """Test deterministic current-vs-unified comparison behavior."""

    def test_compare_records_metrics_for_both_flows(self):
        """Verify comparison metrics show unified behavior can be distinguished."""
        scenario = ChatScenario(
            name="edit_plus_verification",
            prompt="Fix app.py and verify it.",
            tools=["read_file", "edit_file", "run_command"],
            expected_relevant_tools=["read_file"],
            expected_change_tools=["edit_file"],
            expected_verification_tools=["run_command"],
        )
        current = ScriptedChatFlowRunner(
            "current",
            {
                scenario.name: [
                    ScriptedModelStep(
                        tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "app.py"})]
                    ),
                    ScriptedModelStep(final="I inspected app.py.", status="answered_without_verification"),
                ]
            },
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
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
                    ScriptedModelStep(final="Fixed and verified app.py.", status="handled"),
                ]
            },
        )

        report = compare_chat_flows([scenario], current, unified)
        comparison = report.comparisons[0]

        self.assertTrue(comparison.current["relevant_tool_called"])
        self.assertTrue(comparison.current["relevant_tool_succeeded"])
        self.assertFalse(comparison.current["verification_attempted"])
        self.assertFalse(comparison.current["made_allowed_change"])
        self.assertTrue(comparison.unified["relevant_tool_called"])
        self.assertTrue(comparison.unified["relevant_tool_succeeded"])
        self.assertTrue(comparison.unified["verification_attempted"])
        self.assertTrue(comparison.unified["made_allowed_change"])
        self.assertEqual(report.summary["scenarios"], 1)
        self.assertEqual(report.summary["unified_verification_attempted"], 1)
        self.assertTrue(comparison.current_trace.metadata["comparison_parallel"])
        self.assertTrue(comparison.unified_trace.metadata["comparison_parallel"])

    def test_loop_backed_runner_nudges_premature_file_answer_once(self):
        """Verify unified loop records completion-gate misses after a failed nudge."""
        scenario = ChatScenario(
            name="needs_file_context",
            prompt="What does this project use for settings storage?",
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
                    ScriptedModelStep(final="It probably uses config.py.", status="premature"),
                    ScriptedModelStep(final="It probably uses config.py.", status="still_premature"),
                ]
            },
        )

        trace = unified.run(scenario)

        self.assertEqual(trace.final_status, "still_premature")
        self.assertTrue(trace.metadata["completion_gate_missed"])
        self.assertIn("file context", trace.metadata["completion_gate_message"])
        self.assertEqual(len(trace.observations), 1)

    def test_loop_backed_runner_nudges_after_failed_read_file(self):
        """Verify failed file reads trigger a discovery nudge before final."""
        scenario = ChatScenario(
            name="synthetic_tool_recovery",
            prompt="Read notes.md and summarize it.",
            tools=["read_file", "list_files"],
            expected_relevant_tools=["read_file", "list_files"],
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_1", name="read_file")]),
                    ScriptedModelStep(final="I could not find notes.md.", status="premature"),
                    ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                    ScriptedModelStep(final="I found docs/notes.md.", status="premature_after_list"),
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_2", name="read_file")]),
                    ScriptedModelStep(final="Found docs/notes.md and summarized it.", status="handled"),
                ]
            },
            fixtures_by_scenario={
                scenario.name: {
                    "list_files": ["docs/notes.md"],
                    "read_file": [
                        WispToolResult(
                            call_id="fixture",
                            name="read_file",
                            ok=False,
                            content="File not found: notes.md",
                        ),
                        "Notes: settings load from settings.json at startup.",
                    ],
                }
            },
        )

        trace = unified.run(scenario)

        self.assertEqual(trace.final_status, "handled")
        self.assertFalse(trace.metadata["completion_gate_missed"])
        self.assertIn("read_file failed", trace.observations[1].summary)
        self.assertIn("evidence source", trace.observations[3].summary)
        self.assertEqual([call.name for call in trace.tool_calls], ["read_file", "list_files", "read_file"])

    def test_loop_backed_runner_reads_likely_file_after_listing(self):
        """Verify file-context answers require evidence, not only filenames."""
        scenario = ChatScenario(
            name="synthetic_file_context",
            prompt="What does this project use for settings storage?",
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                    ScriptedModelStep(final="It probably uses config.py.", status="premature_after_list"),
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_1", name="read_file")]),
                    ScriptedModelStep(final="It uses settings.json.", status="handled"),
                ]
            },
            fixtures_by_scenario={
                scenario.name: {
                    "list_files": ["config.py\napp.py\nREADME.md"],
                    "read_file": ["SETTINGS_STORAGE = 'json-file'\nSETTINGS_PATH = 'settings.json'\n"],
                }
            },
        )

        trace = unified.run(scenario)

        self.assertEqual(trace.final_status, "handled")
        self.assertFalse(trace.metadata["completion_gate_missed"])
        self.assertIn("discovery tool", trace.observations[1].summary)
        self.assertEqual([call.name for call in trace.tool_calls], ["list_files", "read_file"])

    def test_loop_backed_runner_answers_from_evidence_after_budget_apology(self):
        """Verify budget-skipped calls do not hide already-read evidence."""
        scenario = ChatScenario(
            name="synthetic_file_context",
            prompt="What does this project use for settings storage?",
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_1", name="read_file")]),
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_2", name="read_file")]),
                    ScriptedModelStep(final="The tool budget is exhausted. Please ask again.", status="budget_apology"),
                    ScriptedModelStep(final="It uses settings.json.", status="handled"),
                ]
            },
            fixtures_by_scenario={
                scenario.name: {
                    "list_files": ["config.py\napp.py\nREADME.md"],
                    "read_file": ["SETTINGS_PATH = 'settings.json'\n"],
                }
            },
            loop=ChatToolLoop(ChatToolLoopConfig(max_tool_calls=2)),
        )

        trace = unified.run(scenario)

        self.assertEqual(trace.final_status, "handled")
        self.assertFalse(trace.metadata["completion_gate_missed"])
        self.assertIn("successful observations", trace.observations[-1].summary)
        self.assertEqual([call.name for call in trace.tool_calls], ["list_files", "read_file", "read_file"])

    def test_loop_backed_runner_enforces_tool_call_budget(self):
        """Verify unified loop returns budget-exhausted tool observations."""
        scenario = ChatScenario(
            name="budget",
            prompt="Read files.",
            tools=["read_file"],
            expected_relevant_tools=["read_file"],
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
                    ScriptedModelStep(
                        tool_calls=[
                            WispToolCall(id="read_1", name="read_file"),
                            WispToolCall(id="read_2", name="read_file"),
                        ]
                    ),
                    ScriptedModelStep(final="Done.", status="handled"),
                ]
            },
            loop=ChatToolLoop(ChatToolLoopConfig(max_tool_calls=1)),
        )

        trace = unified.run(scenario)

        self.assertEqual(trace.observations[0].tool_results[0].ok, True)
        self.assertEqual(trace.observations[0].tool_results[1].ok, False)
        self.assertEqual(trace.observations[0].tool_results[1].metadata["error_type"], "tool_budget_exhausted")

    def test_responses_adapter_round_trips_function_call_outputs(self):
        """Verify Responses adapter feeds real function-call outputs to the loop."""
        from types import SimpleNamespace

        first = SimpleNamespace(
            id="resp_1",
            output_text="",
            output=[
                SimpleNamespace(
                    id="rs_1",
                    type="reasoning",
                    summary=[{"type": "summary_text", "text": "Need to inspect the file."}],
                ),
                SimpleNamespace(
                    id="fc_1",
                    type="function_call",
                    call_id="call_1",
                    name="read_file",
                    arguments='{"path":"app.py"}',
                )
            ],
        )
        second = SimpleNamespace(id="resp_2", output_text="app.py says hello.", output=[])

        class Responses:
            def __init__(self):
                self.calls = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return first if len(self.calls) == 1 else second

        client = SimpleNamespace(responses=Responses())
        request = ChatToolRequest(
            messages=[{"role": "user", "content": "Read app.py"}],
            system_prompt="",
            model_route={"provider": "chatgpt", "model": "gpt-test"},
            tools=[{"name": "read_file"}],
            allowed_tools=["read_file"],
            pinned_tools=["read_file"],
            permissions={},
            budgets={},
        )

        final = ChatToolLoop().run(
            request,
            ResponsesChatLoopModel(client, model="gpt-test", instructions="", tools=[]),
            FakeToolExecutor({"read_file": ["contents of app.py"]}),
        )

        self.assertEqual(final.text, "app.py says hello.")
        self.assertEqual(final.tool_calls[0].name, "read_file")
        self.assertEqual(client.responses.calls[0]["store"], False)
        self.assertEqual(client.responses.calls[1]["store"], False)
        self.assertNotIn("previous_response_id", client.responses.calls[1])
        self.assertEqual(client.responses.calls[1]["input"][0]["type"], "message")
        self.assertEqual(client.responses.calls[1]["input"][1]["type"], "reasoning")
        self.assertNotIn("id", client.responses.calls[1]["input"][1])
        self.assertEqual(client.responses.calls[1]["input"][1]["summary"][0]["text"], "Need to inspect the file.")
        self.assertEqual(client.responses.calls[1]["input"][2]["type"], "function_call")
        self.assertNotIn("id", client.responses.calls[1]["input"][2])
        self.assertEqual(client.responses.calls[1]["input"][2]["call_id"], "call_1")
        self.assertEqual(client.responses.calls[1]["input"][3]["type"], "function_call_output")
        self.assertEqual(client.responses.calls[1]["input"][3]["output"], "contents of app.py")

    def test_fake_executor_stamps_fixture_result_with_actual_call_id(self):
        """Verify synthetic fixtures cannot leak stale call ids into provider output."""
        executor = FakeToolExecutor({
            "read_file": [
                WispToolResult(
                    call_id="fixture_id",
                    name="fixture_name",
                    ok=False,
                    content="File not found.",
                )
            ]
        })

        result = executor.execute(WispToolCall(id="real_call_id", name="read_file"))

        self.assertEqual(result.call_id, "real_call_id")
        self.assertEqual(result.name, "read_file")
        self.assertFalse(result.ok)
        self.assertEqual(result.content, "File not found.")

    def test_fake_executor_uses_argument_aware_fixtures(self):
        """Verify synthetic fixtures can distinguish calls by tool arguments."""
        executor = FakeToolExecutor({
            "read_file": {
                "path=notes.md": [
                    WispToolResult(
                        call_id="fixture_missing",
                        name="read_file",
                        ok=False,
                        content="File not found: notes.md",
                    )
                ],
                "path=docs/notes.md": ["Notes live here."],
            }
        })

        missing = executor.execute(WispToolCall(id="call_1", name="read_file", arguments={"path": "notes.md"}))
        found = executor.execute(WispToolCall(id="call_2", name="read_file", arguments={"path": "docs/notes.md"}))

        self.assertFalse(missing.ok)
        self.assertEqual(missing.content, "File not found: notes.md")
        self.assertTrue(found.ok)
        self.assertEqual(found.content, "Notes live here.")

    def test_openai_style_output_grader_accepts_explicit_alternatives(self):
        """Verify output contains checks can allow wording variants."""
        from core.llm_clients.openai_evals_style import _grade_output_text

        self.assertEqual(
            _grade_output_text(
                ["settings.json", "startup||app starts"],
                "The project stores settings in settings.json and loads them when the app starts.",
            ),
            1.0,
        )

    def test_score_distinguishes_failed_tool_from_recovery(self):
        """Verify scoring distinguishes failure-only traces from recovered traces."""
        scenario = ChatScenario(
            name="synthetic_tool_recovery",
            prompt="Read notes.md and summarize it.",
            tools=["read_file", "list_files"],
            expected_relevant_tools=["read_file", "list_files"],
        )
        failed = ScriptedChatFlowRunner(
            "failed",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_1", name="read_file")]),
                    ScriptedModelStep(final="I could not find notes.md.", status="final"),
                ]
            },
            fixtures_by_scenario={
                scenario.name: {
                    "read_file": [
                        WispToolResult(
                            call_id="fixture",
                            name="read_file",
                            ok=False,
                            content="File not found: notes.md",
                        )
                    ]
                }
            },
        )
        recovered = ScriptedChatFlowRunner(
            "recovered",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="read_1", name="read_file")]),
                    ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                    ScriptedModelStep(final="notes.md says settings load from settings.json at startup.", status="final"),
                ]
            },
            fixtures_by_scenario={
                scenario.name: {
                    "read_file": [
                        WispToolResult(
                            call_id="fixture",
                            name="read_file",
                            ok=False,
                            content="File not found: notes.md",
                        )
                    ],
                    "list_files": ["docs/notes.md"],
                }
            },
        )

        report = compare_chat_flows([scenario], failed, recovered)
        comparison = report.comparisons[0]

        self.assertTrue(comparison.current["failed_tool_observed"])
        self.assertFalse(comparison.current["recovered_after_failed_tool"])
        self.assertFalse(comparison.current["answered_actual_request"])
        self.assertTrue(comparison.unified["failed_tool_observed"])
        self.assertTrue(comparison.unified["recovered_after_failed_tool"])
        self.assertTrue(comparison.unified["answered_actual_request"])

    def test_write_comparison_artifacts(self):
        """Verify harness artifacts include summary, traces, and report."""
        scenario = ChatScenario(
            name="needs_file_context",
            prompt="What does settings storage use?",
            tools=["list_files"],
            expected_relevant_tools=["list_files"],
        )
        runner = ScriptedChatFlowRunner(
            "same",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                    ScriptedModelStep(final="It uses config.py.", status="handled"),
                ]
            },
        )
        report = compare_chat_flows([scenario], runner, runner)

        with TemporaryDirectory() as tmp:
            run_dir = write_comparison_artifacts(report, tmp, report_title="Scripted Harness Self-Test")

            self.assertTrue((run_dir / "summary.json").exists())
            self.assertTrue((run_dir / "results.json").exists())
            self.assertTrue((run_dir / "openai_eval_scores.json").exists())
            self.assertTrue((run_dir / "openai_eval_spec.json").exists())
            self.assertTrue((run_dir / "openai_evals_package_report.json").exists())
            self.assertTrue((run_dir / "harness_matrix.json").exists())
            self.assertTrue((run_dir / "scenarios.json").exists())
            self.assertTrue((run_dir / "current" / "needs_file_context.json").exists())
            self.assertTrue((run_dir / "unified" / "needs_file_context.json").exists())
            results = (run_dir / "results.json").read_text(encoding="utf-8")
            self.assertIn("current_trace", results)
            self.assertIn("unified_trace", results)
            self.assertIn("openai_eval_scores", results)
            self.assertIn("openai_evals_package", results)
            harness_matrix = (run_dir / "harness_matrix.json").read_text(encoding="utf-8")
            self.assertIn("wisp_behavior_score", harness_matrix)
            self.assertIn("openai_style_local", harness_matrix)
            self.assertIn("openai_evals_package", harness_matrix)
            self.assertIn("Scripted Harness Self-Test", (run_dir / "report.md").read_text(encoding="utf-8"))
            html_report = (run_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn("Current Flow", html_report)
            self.assertIn("Unified Flow", html_report)
            self.assertTrue(Path(run_dir).is_dir())

    @unittest.skipUnless(importlib.util.find_spec("evals"), "OpenAI evals package is not installed")
    def test_openai_evals_package_harness_records_native_events(self):
        """Verify the real OpenAI Evals package records native JSONL events."""
        from core.llm_clients.openai_evals_harness import run_openai_evals_package

        scenario = ChatScenario(
            name="needs_file_context",
            prompt="What does this project use for settings storage?",
            tools=["list_files", "read_file"],
            expected_relevant_tools=["list_files", "read_file"],
        )
        current = ScriptedChatFlowRunner(
            "current",
            {
                scenario.name: [
                    ScriptedModelStep(final="It likely uses config.py.", status="answered_without_tools"),
                ]
            },
        )
        unified = LoopBackedScriptedChatFlowRunner(
            "unified",
            {
                scenario.name: [
                    ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                    ScriptedModelStep(
                        tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "config.py"})]
                    ),
                    ScriptedModelStep(final="It uses settings.json for settings storage.", status="handled"),
                ]
            },
            fixtures_by_scenario={
                scenario.name: {
                    "list_files": ["config.py\nREADME.md"],
                    "read_file": {
                        "path=config.py": ["SETTINGS_PATH = 'settings.json'\n"],
                    },
                }
            },
        )
        report = compare_chat_flows([scenario], current, unified)

        with TemporaryDirectory() as tmp:
            package_report = run_openai_evals_package(report, tmp)

            self.assertTrue(package_report["available"])
            self.assertEqual(package_report["samples"], 2)
            self.assertIn("package_version", package_report)
            self.assertIn("final_report", package_report)
            self.assertIn("current", package_report["final_report"]["by_flow"])
            self.assertIn("unified", package_report["final_report"]["by_flow"])
            event_log = Path(package_report["event_log_path"])
            self.assertTrue(event_log.exists())
            events = event_log.read_text(encoding="utf-8")
            self.assertIn('"type": "match"', events)
            self.assertIn('"type": "function_call"', events)
            self.assertIn('"final_report"', events)


if __name__ == "__main__":
    unittest.main()
