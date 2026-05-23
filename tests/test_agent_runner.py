from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile
import unittest
import base64
from unittest.mock import patch

from core.agent_runner import (
    AgentCancelled,
    AgentPermissions,
    AgentRunControl,
    AgentTaskRunner,
    AgentToolbox,
    PermissionDenied,
    ScopedWorkspace,
    ScopeViolation,
    ToolResult,
)


@dataclass
class DummySpec:
    title: str = "Test Agent"
    objective: str = "Inspect the project and make a plan."
    scope_folder: str = ""
    sandbox_mode: str = "workspace-write: scope folder only"
    approval_policy: str = "ask before escalation"
    provider: str = "copilot"
    model: str = "gpt-5.3-codex"
    reasoning_effort: str = "medium"
    max_runtime_minutes: int = 5
    max_turns: int = 3
    allow_shell: bool = False
    allow_network: bool = False
    allow_git: bool = False
    allow_file_create: bool = True
    allow_file_edit: bool = True
    allow_file_delete: bool = False
    allowed_file_globs: list[str] = field(default_factory=list)
    blocked_file_globs: list[str] = field(default_factory=lambda: ["private/*"])
    required_context: str = ""
    completion_criteria: str = "A plan is written."
    report_format: str = "Summary + changed files + verification"
    agent_temperature: float = 0.0
    parallel_read_only_briefing: bool = True


class ScopedWorkspaceTests(unittest.TestCase):
    def test_resolve_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scope"
            root.mkdir()
            ws = ScopedWorkspace(root)
            with self.assertRaises(ScopeViolation):
                ws.resolve(root / ".." / "outside.txt")

    def test_blocked_globs_hide_and_reject_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.txt").write_text("ok", encoding="utf-8")
            (root / "private").mkdir()
            (root / "private" / "secret.txt").write_text("secret", encoding="utf-8")

            ws = ScopedWorkspace(root, blocked_globs=["private/*"])

            self.assertEqual(ws.list_files(), ["ok.txt"])
            with self.assertRaises(ScopeViolation):
                ws.read_text("private/secret.txt")

    def test_write_respects_create_and_edit_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = ScopedWorkspace(root)

            with self.assertRaises(PermissionError):
                ws.write_text("new.txt", "hello", create=False, edit=True)

            ws.write_text("new.txt", "hello", create=True, edit=False)

            with self.assertRaises(PermissionError):
                ws.write_text("new.txt", "changed", create=True, edit=False)


class AgentToolboxTests(unittest.TestCase):
    def test_toolbox_create_patch_and_read_are_scoped_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs: list[str] = []
            tools = AgentToolbox(
                ScopedWorkspace(tmp),
                AgentPermissions(allow_file_create=True, allow_file_edit=True),
                log=logs.append,
            )

            tools.create_file("note.txt", "hello world")
            tools.patch_file("note.txt", "world", "agent")
            result = tools.read_file("note.txt")

            self.assertEqual(result.data, "hello agent")
            self.assertTrue(any("tool patch_file" in line for line in logs))

    def test_toolbox_rejects_edit_without_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("hello", encoding="utf-8")
            tools = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_file_create=True, allow_file_edit=False),
            )

            with self.assertRaises(PermissionDenied):
                tools.patch_file("note.txt", "hello", "bye")

    def test_toolbox_delete_requires_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("hello", encoding="utf-8")
            tools = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_file_delete=False),
            )

            with self.assertRaises(PermissionDenied):
                tools.delete_file("note.txt")

    def test_toolbox_command_allowlist_and_shell_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ok.py").write_text("x = 1\n", encoding="utf-8")
            no_shell = AgentToolbox(ScopedWorkspace(root), AgentPermissions(allow_shell=False))
            with self.assertRaises(PermissionDenied):
                no_shell.run_command(["python", "-m", "py_compile", "ok.py"])

            tools = AgentToolbox(ScopedWorkspace(root), AgentPermissions(allow_shell=True))
            result = tools.run_command(["python", "-m", "py_compile", "ok.py"])
            self.assertTrue(result.ok)

            with self.assertRaises(PermissionDenied):
                tools.run_command(["python", "-c", "print('not allowlisted')"])

    def test_project_verification_commands_are_gated_by_manifest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = AgentToolbox(ScopedWorkspace(root), AgentPermissions(allow_shell=True))

            with self.assertRaises(PermissionDenied):
                tools.run_command(["npm", "test"])

            (root / "package.json").write_text('{"scripts":{"test":"node --version"}}', encoding="utf-8")
            self.assertIn(["npm", "test"], tools.verification_commands())
            self.assertIn(["npm", "run", "build"], tools.verification_commands())

    def test_additional_static_verification_commands_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_shell=True))

            self.assertTrue(tools._is_command_allowed(["python", "-m", "pytest"]))
            self.assertTrue(tools._is_command_allowed(["python", "-m", "ruff", "check", "."]))
            self.assertTrue(tools._is_command_allowed(["node", "--check", "index.js"]))

    def test_approval_callback_can_decline_mutating_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("hello", encoding="utf-8")
            requests: list[dict] = []
            tools = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_file_edit=True),
                require_approval=True,
                approval_callback=lambda request: requests.append(request) or False,
            )

            with self.assertRaises(PermissionDenied):
                tools.patch_file("note.txt", "hello", "bye")

            self.assertEqual(root.joinpath("note.txt").read_text(encoding="utf-8"), "hello")
            self.assertEqual(requests[0]["action"], "patch_file")

    def test_git_status_and_diff_use_git_permission_without_shell_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess_result = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_git=True, allow_shell=False),
            ).run_command(["git", "status", "--short"])

            self.assertIn(subprocess_result.tool, {"run_command"})


class AgentBoundaryAttackTests(unittest.TestCase):
    def test_absolute_path_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scope"
            outside = Path(tmp) / "outside.txt"
            root.mkdir()
            outside.write_text("do not touch", encoding="utf-8")
            tools = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_file_edit=True),
            )

            result = AgentTaskRunner()._execute_tool_call(
                tools,
                {
                    "tool": "write_file",
                    "args": {"path": str(outside), "content": "changed"},
                },
            )

            self.assertFalse(result.ok)
            self.assertIn("escapes scope", result.message)
            self.assertEqual(outside.read_text(encoding="utf-8"), "do not touch")

    def test_relative_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scope"
            outside = Path(tmp) / "outside.txt"
            root.mkdir()
            outside.write_text("safe", encoding="utf-8")
            tools = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_file_edit=True),
            )

            result = AgentTaskRunner()._execute_tool_call(
                tools,
                {
                    "tool": "patch_file",
                    "args": {"path": "../outside.txt", "old": "safe", "new": "changed"},
                },
            )

            self.assertFalse(result.ok)
            self.assertIn("escapes scope", result.message)
            self.assertEqual(outside.read_text(encoding="utf-8"), "safe")

    def test_blocked_secret_patterns_cannot_be_read_or_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (root / "app.py").write_text("x = 1", encoding="utf-8")
            tools = AgentToolbox(
                ScopedWorkspace(root, blocked_globs=[".env", "*.key", "*secret*"]),
                AgentPermissions(allow_file_create=True, allow_file_edit=True),
            )

            read_result = AgentTaskRunner()._execute_tool_call(
                tools,
                {"tool": "read_file", "args": {"path": ".env"}},
            )
            write_result = AgentTaskRunner()._execute_tool_call(
                tools,
                {"tool": "write_file", "args": {"path": "api.key", "content": "secret"}},
            )

            self.assertFalse(read_result.ok)
            self.assertFalse(write_result.ok)
            self.assertIn("blocked", read_result.message)
            self.assertFalse((root / "api.key").exists())

    def test_dangerous_shell_commands_are_rejected_even_when_shell_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(
                ScopedWorkspace(tmp),
                AgentPermissions(allow_shell=True, allow_git=True),
            )
            runner = AgentTaskRunner()
            dangerous_commands = [
                ["powershell", "-Command", "Remove-Item", "-Recurse", "."],
                ["cmd", "/c", "del", "*"],
                ["git", "reset", "--hard"],
                ["git", "push"],
                ["pip", "install", "some-package"],
                ["curl", "https://example.com/script.ps1"],
            ]

            for command in dangerous_commands:
                with self.subTest(command=command):
                    result = runner._execute_tool_call(
                        tools,
                        {"tool": "run_command", "args": {"args": command}},
                    )
                    self.assertFalse(result.ok)
                    self.assertIn("not allowlisted", result.message)

    def test_agent_loop_logs_denied_attack_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            outside = Path(tmp) / "outside.txt"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            outside.write_text("outside", encoding="utf-8")
            (scope / "safe.txt").write_text("safe", encoding="utf-8")
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            responses = [
                {
                    "thought": "Try to escape.",
                    "tool_calls": [
                        {
                            "tool": "write_file",
                            "args": {"path": "../outside.txt", "content": "owned"},
                        }
                    ],
                    "final": None,
                },
                {
                    "thought": "Report denial.",
                    "tool_calls": [],
                    "final": "Escape attempt was denied.",
                },
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside")
            self.assertFalse(turns[0]["tool_results"][0]["ok"])
            self.assertIn("escapes scope", turns[0]["tool_results"][0]["message"])


class AgentRunnerTests(unittest.TestCase):
    def test_runner_executes_autonomous_tool_loop_and_writes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            (scope / "app.py").write_text("print('hi')", encoding="utf-8")
            spec = DummySpec(
                scope_folder=str(scope),
                allow_shell=True,
                max_turns=5,
                approval_policy="never escalate",
            )
            responses = [
                {
                    "thought": "Need to inspect the file.",
                    "tool_calls": [{"tool": "read_file", "args": {"path": "app.py"}}],
                    "final": None,
                },
                {
                    "thought": "Patch the greeting.",
                    "tool_calls": [{
                        "tool": "patch_file",
                        "args": {"path": "app.py", "old": "print('hi')", "new": "print('hello')"},
                    }],
                    "final": None,
                },
                {
                    "thought": "Verify syntax.",
                    "tool_calls": [{
                        "tool": "run_command",
                        "args": {"args": ["python", "-m", "py_compile", "app.py"]},
                    }],
                    "final": None,
                },
                {
                    "thought": "Done.",
                    "tool_calls": [],
                    "final": "Changed the greeting and verified syntax.",
                },
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            self.assertTrue((run_dir / "run.log").exists())
            self.assertTrue((run_dir / "task.json").exists())
            self.assertTrue((run_dir / "permissions.json").exists())
            self.assertTrue((run_dir / "files.json").exists())
            self.assertTrue((run_dir / "verification_commands.json").exists())
            self.assertTrue((run_dir / "turns.json").exists())
            self.assertTrue((run_dir / "agent_states.json").exists())
            self.assertTrue((run_dir / "verbose.log").exists())
            self.assertIn("tool call", (run_dir / "verbose.log").read_text(encoding="utf-8"))
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Changed the greeting and verified syntax.")
            self.assertEqual((scope / "app.py").read_text(encoding="utf-8"), "print('hello')")
            self.assertIn("agent run finished", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_runner_routes_agents_and_records_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=3)
            spec.agents = [
                {
                    "name": "Planner",
                    "role": "Planner",
                    "provider": "copilot",
                    "model": "same as task",
                    "responsibility": "Decide what to inspect.",
                },
                {
                    "name": "Reviewer",
                    "role": "Reviewer",
                    "provider": "anthropic",
                    "model": "same as task",
                    "responsibility": "Review the planner's message.",
                },
            ]
            spec.communications = []
            prompts: list[str] = []
            responses = [
                {
                    "thought": "Ask reviewer to check.",
                    "tool_calls": [{
                        "tool": "send_message",
                        "args": {"to": "Reviewer", "message": "Please review the empty scope."},
                    }],
                    "final": None,
                },
                {
                    "thought": "I saw the planner message.",
                    "tool_calls": [],
                    "final": "Reviewer received Planner's message.",
                },
            ]

            def fake_model(prompt: str) -> str:
                prompts.append(prompt)
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Planner", "Reviewer"])
            self.assertEqual(messages[0]["from"], "Planner")
            self.assertEqual(messages[0]["to"], "Reviewer")
            self.assertIn("Active agent: Planner", prompts[0])
            self.assertIn("Provider preference: copilot", prompts[0])
            self.assertIn("Active agent: Reviewer", prompts[1])
            self.assertIn("Provider preference: anthropic", prompts[1])
            self.assertIn("Please review the empty scope.", prompts[1])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Reviewer received Planner's message.")

    def test_prompt_lists_only_tools_allowed_by_permissions(self):
        spec = DummySpec(
            allow_shell=False,
            allow_git=False,
            allow_file_create=False,
            allow_file_edit=False,
            allow_file_delete=False,
        )
        prompt = AgentTaskRunner()._build_agent_prompt(spec, ["snake_game.py"], [])

        self.assertIn("Available tools for this task and phase:", prompt)
        self.assertIn("- list_files args:", prompt)
        self.assertIn("- read_file args:", prompt)
        self.assertIn("- send_message args:", prompt)
        self.assertNotIn("- create_file args:", prompt)
        self.assertNotIn("- write_file args:", prompt)
        self.assertNotIn("- patch_file args:", prompt)
        self.assertNotIn("- delete_file args:", prompt)
        self.assertNotIn("- run_command args:", prompt)
        self.assertNotIn("- git_status args:", prompt)

    def test_read_only_prompt_omits_write_and_shell_tools_even_when_enabled(self):
        spec = DummySpec(
            allow_shell=True,
            allow_git=True,
            allow_file_create=True,
            allow_file_edit=True,
            allow_file_delete=True,
        )
        prompt = AgentTaskRunner()._build_agent_prompt(spec, ["snake_game.py"], [], read_only_phase=True)

        self.assertIn("Allowed tool_calls in this phase are: list_files, read_file, send_message, git_status, git_diff", prompt)
        self.assertIn("- git_status args:", prompt)
        self.assertNotIn("- create_file args:", prompt)
        self.assertNotIn("- patch_file args:", prompt)
        self.assertNotIn("- run_command args:", prompt)

    def test_runner_honors_next_agent_for_multiple_builder_turns(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=5)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Assign work.", "status": "continue", "next_agent": "Builder", "reason": "Start implementation", "tool_calls": [], "final": None},
                {"thought": "Create first file.", "status": "continue", "next_agent": "Builder", "reason": "Need another builder turn", "tool_calls": [{"tool": "create_file", "args": {"path": "a.txt", "content": "a"}}], "final": None},
                {"thought": "Finish implementation.", "status": "ready_for_review", "next_agent": "Reviewer", "reason": "Ready for review", "tool_calls": [{"tool": "create_file", "args": {"path": "b.txt", "content": "b"}}], "final": None},
                {"thought": "Looks good.", "tool_calls": [], "final": "Reviewed."},
                {"thought": "Coordinator accepts.", "tool_calls": [], "final": "Reviewed."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder", "Builder", "Reviewer", "Coordinator"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Reviewed.")

    def test_non_coordinator_final_routes_to_coordinator_for_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=3)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Assign work.", "status": "continue", "next_agent": "Builder", "tool_calls": [], "final": None},
                {"thought": "Tests pass.", "tool_calls": [], "final": "Builder says done."},
                {"thought": "Accept final.", "tool_calls": [], "final": "Coordinator final."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder", "Coordinator"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Coordinator final.")
            self.assertIn("completion requires Coordinator", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_non_reviewer_final_routes_to_reviewer_when_no_coordinator(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=3)
            spec.agents = [
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Tests pass.", "tool_calls": [], "final": "Builder says done."},
                {"thought": "Accept final.", "tool_calls": [], "final": "Reviewer final."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Builder", "Reviewer"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Reviewer final.")

    def test_reviewer_final_reports_to_coordinator_message_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=3)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Please review.", "status": "continue", "next_agent": "Reviewer", "tool_calls": [], "final": None},
                {"thought": "Review complete.", "tool_calls": [], "final": "Review report."},
                {"thought": "Coordinator accepts.", "tool_calls": [], "final": "Final accepted."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))

            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Reviewer", "Coordinator"])
            self.assertTrue(any(message["from"] == "Reviewer" and message["to"] == "Coordinator" for message in messages))
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Final accepted.")

    def test_coordinator_final_waits_for_pending_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=4)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Ready for review, but stay here.", "status": "ready_for_review", "next_agent": "Coordinator", "tool_calls": [], "final": None},
                {"thought": "Trying to conclude early.", "tool_calls": [], "final": "Too soon."},
                {"thought": "Review done.", "tool_calls": [], "final": "Review report."},
                {"thought": "Now conclude.", "tool_calls": [], "final": "Done."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))

            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Coordinator", "Reviewer", "Coordinator"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Done.")

    def test_directed_message_recipient_gets_next_turn_without_explicit_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {
                    "thought": "Ask Reviewer directly, but no next_agent.",
                    "status": "continue",
                    "tool_calls": [{"tool": "send_message", "args": {"to": "Reviewer", "message": "Please inspect this."}}],
                    "final": None,
                },
                {"thought": "I received the handoff.", "tool_calls": [], "final": "Reviewer acted."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Reviewer"])
            self.assertIn("routing by latest directed message", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_silent_handoff_creates_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Assign silently.", "status": "continue", "next_agent": "Builder", "reason": "Inspect the files.", "tool_calls": [], "final": None},
                {"thought": "Got the handoff.", "tool_calls": [], "final": "Done."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
            self.assertEqual(messages[0]["from"], "Coordinator")
            self.assertEqual(messages[0]["to"], "Builder")
            self.assertEqual(messages[0]["source"], "auto_handoff")

    def test_role_scoped_tools_deny_coordinator_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_shell=True))
            result = AgentTaskRunner()._execute_agent_tool_call(
                tools,
                {"tool": "run_command", "args": {"args": ["python", "-m", "unittest"]}},
                "Coordinator",
                [],
                {"messages": []},
                active_agent={"name": "Coordinator", "role": "Coordinator"},
                spec=DummySpec(allow_shell=True),
            )

            self.assertFalse(result.ok)
            self.assertIn("not allowed for Coordinator", result.message)

    def test_role_scoped_tools_deny_coordinator_file_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "snake_game.py").write_text("print('hi')\n", encoding="utf-8")
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions())
            runner = AgentTaskRunner()

            result = runner._execute_agent_tool_call(
                tools,
                {"tool": "read_file", "args": {"path": "snake_game.py"}},
                "Coordinator",
                [],
                {"messages": []},
                active_agent={"name": "Coordinator", "role": "Coordinator"},
                spec=DummySpec(),
            )
            prompt = runner._build_agent_prompt(
                DummySpec(),
                ["snake_game.py"],
                [],
                active_agent={"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
            )

            self.assertFalse(result.ok)
            self.assertIn("not allowed for Coordinator", result.message)
            self.assertNotIn("- read_file args:", prompt)
            self.assertIn("- list_files args:", prompt)

    def test_run_command_accepts_command_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_shell=True))
            result = AgentTaskRunner()._execute_agent_tool_call(
                tools,
                {"tool": "run_command", "args": {"command": "python -m py_compile missing.py"}},
                "Builder",
                [],
                {"messages": []},
                active_agent={"name": "Builder", "role": "Implementer"},
                spec=DummySpec(allow_shell=True),
            )

            self.assertFalse(result.ok)
            self.assertIn("python -m py_compile missing.py", result.message)

    def test_run_command_schema_error_includes_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_shell=True))
            result = AgentTaskRunner()._execute_agent_tool_call(
                tools,
                {"tool": "run_command", "args": {"timeout_seconds": 1}},
                "Builder",
                [],
                {"messages": []},
                active_agent={"name": "Builder", "role": "Implementer"},
                spec=DummySpec(allow_shell=True),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.data["error_type"], "schema_error")
            self.assertEqual(result.data["correction"], {"args": ["python", "-m", "pytest"]})

    def test_coordinator_cannot_assign_install_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = AgentTaskRunner()._execute_agent_tool_call(
                AgentToolbox(ScopedWorkspace(tmp), AgentPermissions()),
                {"tool": "send_message", "args": {"to": "Builder", "message": "Install ruff and mypy, then run them."}},
                "Coordinator",
                [],
                {"messages": []},
                active_agent={"name": "Coordinator", "role": "Coordinator"},
                spec=DummySpec(),
                task_state=AgentTaskRunner._initial_task_state([]),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_type"], "impossible_assignment")
        self.assertIn("available verification", result.data["correction"])

    def test_duplicate_successful_verification_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            (scope / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            spec = DummySpec(scope_folder=str(scope), max_turns=2, allow_shell=True, approval_policy="never")
            spec.agents = [
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {"thought": "Verify once.", "status": "continue", "next_agent": "Builder", "tool_calls": [{"tool": "run_command", "args": {"args": ["pytest"]}}], "final": None},
                {"thought": "Try equivalent verify.", "status": "continue", "next_agent": "Builder", "tool_calls": [{"tool": "run_command", "args": {"args": ["python", "-m", "pytest"]}}], "final": None},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))

            self.assertIn("skipped duplicate successful verification", turns[-1]["tool_results"][0]["message"])
            self.assertEqual(turns[-1]["task_state"]["tests"]["pytest"], "passed")

    def test_git_not_repo_is_marked_unavailable(self):
        task_state = AgentTaskRunner._initial_task_state([])
        logs: list[str] = []
        AgentTaskRunner._update_task_state(
            task_state,
            [{
                "tool": "run_command",
                "ok": False,
                "message": "exit 128: git status --short",
                "data": {"returncode": 128, "stdout": "", "stderr": "fatal: not a git repository"},
            }],
            {"thought": "Check git."},
            "Builder",
            logs.append,
        )

        self.assertIs(task_state["git_available"], False)
        self.assertEqual(task_state["git_reason"], "not a git repository")
        self.assertIn("git", task_state["disabled_tools"])

    def test_developer_alias_routes_to_implementer(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {
                    "thought": "Ask the developer.",
                    "status": "continue",
                    "next_agent": "Developer",
                    "tool_calls": [{"tool": "send_message", "args": {"to": "Developer", "message": "Please inspect this."}}],
                    "final": None,
                },
                {"thought": "I received the developer handoff.", "tool_calls": [], "final": "Builder acted."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder"])

    def test_broadcast_message_routes_to_next_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {
                    "thought": "Tell everyone.",
                    "status": "continue",
                    "tool_calls": [{"tool": "send_message", "args": {"to": "ALL", "message": "Please inspect this."}}],
                    "final": None,
                },
                {"thought": "I received the broadcast.", "tool_calls": [], "final": "Builder acted."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder"])
            self.assertIn("routing by broadcast message", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_repeated_tool_failure_guard_routes_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=5)
            spec.agents = [
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {
                    "thought": "Try unsupported tool.",
                    "status": "continue",
                    "next_agent": "Builder",
                    "tool_calls": [{"tool": "missing_tool", "args": {}}],
                    "final": None,
                },
                {
                    "thought": "Try unsupported tool again.",
                    "status": "continue",
                    "next_agent": "Builder",
                    "tool_calls": [{"tool": "missing_tool", "args": {}}],
                    "final": None,
                },
                {
                    "thought": "Try unsupported tool a third time.",
                    "status": "continue",
                    "next_agent": "Builder",
                    "tool_calls": [{"tool": "missing_tool", "args": {}}],
                    "final": None,
                },
                {"thought": "Coordinator saw guard.", "tool_calls": [], "final": "Stopped loop."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Builder", "Builder", "Builder", "Coordinator"])
            self.assertIn("repeated failure guard", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_explicit_next_agent_overrides_directed_message_recipient(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            responses = [
                {
                    "thought": "Tell Reviewer, but hand execution to Builder.",
                    "status": "continue",
                    "next_agent": "Builder",
                    "tool_calls": [{"tool": "send_message", "args": {"to": "Reviewer", "message": "FYI only."}}],
                    "final": None,
                },
                {"thought": "I received the explicit handoff.", "tool_calls": [], "final": "Builder acted."},
            ]

            def fake_model(_prompt: str) -> str:
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder"])
            self.assertIn("routing by explicit next_agent", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_runner_keeps_persistent_history_per_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            prompts: list[str] = []
            responses = [
                {"thought": "Remember this private note.", "status": "continue", "next_agent": "same", "tool_calls": [], "final": None},
                {"thought": "Use the note.", "tool_calls": [], "final": "Done."},
            ]

            def fake_model(prompt: str) -> str:
                prompts.append(prompt)
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            self.assertIn("Continue as the active agent", prompts[1])
            self.assertIn("Your recent history:", prompts[1])
            self.assertIn("Thought: Remember this private note.", prompts[1])
            states = json.loads((run_dir / "agent_states.json").read_text(encoding="utf-8"))
            self.assertIn("Thought: Remember this private note.", states["Solo"]["history"])

    def test_delta_prompt_clips_repeated_conversation_state(self):
        spec = DummySpec()
        spec.visible_files_delta_limit = 4
        long_message = "Please audit snake_game.py, snake_gui.py, and test_snake_game.py. " * 8
        prompt = AgentTaskRunner()._build_agent_delta_prompt(
            spec,
            ["snake_game.py", "snake_gui.py", "test_snake_game.py", "__pycache__/snake_game.cpython-310.pyc"],
            [["python", "-m", "unittest"]],
            {"name": "Coordinator", "role": "Coordinator", "responsibility": "Coordinate the work."},
            [f"- From Coordinator to ALL: {long_message}"],
            [f"- Coordinator -> ALL: {long_message}"],
            [f"- Thought: {long_message}", "- Tool send_message: Message sent to ALL."],
        )

        self.assertLess(len(prompt), 2200)
        self.assertIn("... [truncated]", prompt)
        self.assertNotIn("__pycache__", prompt)
        self.assertIn("Continue as the active agent", prompt)

    def test_runner_injects_manual_nudge_before_next_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            control = AgentRunControl()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            prompts: list[str] = []
            responses = [
                {"thought": "Need another turn.", "status": "continue", "next_agent": "same", "tool_calls": [], "final": None},
                {"thought": "Saw nudge.", "tool_calls": [], "final": "Done."},
            ]

            def fake_model(prompt: str) -> str:
                prompts.append(prompt)
                if len(prompts) == 1:
                    control.add_nudge("Solo", "Please focus on tests.")
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model, control=control).run(spec)

            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
            self.assertEqual(messages[0]["from"], "User")
            self.assertIn("Please focus on tests.", prompts[1])

    def test_manual_nudge_dedupes_identical_recent_message(self):
        messages = [{"from": "User", "to": "ALL", "message": "Please inspect.", "source": "manual_nudge"}]
        control = AgentRunControl()
        control.add_nudge("ALL", "Please inspect.")
        logs: list[str] = []

        AgentTaskRunner(control=control)._apply_manual_nudges(messages, logs.append)

        self.assertEqual(len(messages), 1)
        self.assertEqual(logs, [])

    def test_control_pause_and_resume(self):
        control = AgentRunControl()
        control.pause_after_turn()
        self.assertTrue(control.is_pause_requested())
        control.resume()
        control.wait_if_paused()
        self.assertFalse(control.is_pause_requested())

    def test_parallel_read_only_round_allows_messages_but_denies_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope))
            spec.agents = [
                {"name": "Scout", "role": "Researcher", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]

            def fake_model(prompt: str) -> str:
                if "Active agent: Scout" in prompt:
                    return json.dumps({
                        "thought": "I will brief Builder.",
                        "tool_calls": [{"tool": "send_message", "args": {"to": "Builder", "message": "Scope is empty."}}],
                        "final": None,
                    })
                return json.dumps({
                    "thought": "I should not write yet.",
                    "tool_calls": [{"tool": "create_file", "args": {"path": "bad.txt", "content": "nope"}}],
                    "final": None,
                })

            runner = AgentTaskRunner(model_callback=fake_model)
            tools = AgentToolbox(ScopedWorkspace(scope), AgentPermissions(allow_file_create=True))
            agents = runner._normalise_agents(spec)
            messages: list[dict] = []
            turns: list[dict] = []
            states = runner._initial_agent_states(agents)
            runner._run_parallel_read_only_round(
                spec,
                tools,
                agents,
                [],
                [],
                messages,
                turns,
                states,
                lambda _message: None,
            )

            self.assertEqual(messages[0]["from"], "Scout")
            self.assertFalse((scope / "bad.txt").exists())
            denied = [
                result
                for turn in turns
                for result in turn["tool_results"]
                if result["tool"] == "create_file"
            ]
            self.assertEqual(denied[0]["ok"], False)
            self.assertIn("read-only", denied[0]["message"])

    def test_tool_results_are_compacted_for_followup_prompts(self):
        huge = "a" * 20000
        prompt_context = AgentTaskRunner._tool_results_for_prompt([
            {"tool": "read_file", "ok": True, "message": "big.py", "data": huge},
            {"tool": "list_files", "ok": True, "message": "files", "data": [f"f{i}.py" for i in range(150)]},
        ])

        self.assertLess(len(prompt_context), 9000)
        self.assertIn('"file_ref": "big.py"', prompt_context)
        self.assertIn('"sha256"', prompt_context)
        self.assertIn('"excerpt"', prompt_context)
        self.assertIn("middle truncated", prompt_context)
        self.assertIn("truncated 30 item", prompt_context)

    def test_runner_uses_smaller_read_only_token_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope))
            spec.agents = [
                {"name": "Scout", "role": "Researcher", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            runner = AgentTaskRunner()
            calls: list[int] = []

            def fake_call_model(_prompt, _log, *, provider=None, model=None, max_tokens=4096, temperature=0.0):
                calls.append(max_tokens)
                return json.dumps({"thought": "Brief.", "tool_calls": [], "final": None})

            runner._call_model = fake_call_model  # type: ignore[method-assign]
            agents = runner._normalise_agents(spec)
            runner._run_parallel_read_only_round(
                spec,
                AgentToolbox(ScopedWorkspace(scope), AgentPermissions()),
                agents,
                [],
                [],
                [],
                [],
                runner._initial_agent_states(agents),
                lambda _message: None,
            )

            self.assertEqual(calls, [1536])

    def test_mutating_tools_run_under_write_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = AgentTaskRunner()
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_file_create=True))
            observed = []

            def fake_unlocked(_tools, tool, _args):
                observed.append((tool, runner._write_lock.locked()))
                return ToolResult(tool, True, "ok")

            runner._execute_tool_call_unlocked = fake_unlocked  # type: ignore[method-assign]

            runner._execute_tool_call(tools, {"tool": "create_file", "args": {"path": "x.txt", "content": "x"}})
            runner._execute_tool_call(tools, {"tool": "read_file", "args": {"path": "x.txt"}})

            self.assertEqual(observed, [("create_file", True), ("read_file", False)])

    def test_placeholder_file_path_returns_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions())
            result = AgentTaskRunner()._execute_tool_call(
                tools,
                {"tool": "read_file", "args": {"path": "path"}},
            )

            self.assertFalse(result.ok)
            self.assertIn("placeholder path", result.message)
            self.assertIn("actual relative filename", result.message)

    def test_tool_call_accepts_function_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "note.txt").write_text("hello", encoding="utf-8")
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions())
            result = AgentTaskRunner()._execute_tool_call(
                tools,
                {"function": "read_file", "args": {"path": "note.txt"}},
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.tool, "read_file")
            self.assertEqual(result.data, "hello")

    def test_tool_call_accepts_nested_function_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "note.txt").write_text("hello", encoding="utf-8")
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions())
            result = AgentTaskRunner()._execute_tool_call(
                tools,
                {"function": {"name": "read_file", "arguments": {"path": "note.txt"}}},
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.tool, "read_file")
            self.assertEqual(result.data, "hello")

    def test_base64_file_tools_avoid_large_json_quoting(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = "line one\n'quoted' and \"quoted\"\nline three"
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_file_create=True, allow_file_edit=True))
            runner = AgentTaskRunner()

            create_result = runner._execute_tool_call(
                tools,
                {"tool": "create_file_base64", "args": {"path": "note.txt", "content_base64": encoded}},
            )
            write_result = runner._execute_tool_call(
                tools,
                {"tool": "write_file_base64", "args": {"path": "note.txt", "content_base64": encoded}},
            )

            self.assertTrue(create_result.ok)
            self.assertTrue(write_result.ok)
            self.assertEqual(Path(tmp, "note.txt").read_text(encoding="utf-8"), content)

    def test_runner_resolves_agent_provider_and_model_route(self):
        spec = DummySpec(provider="copilot", model="gpt-5.3-codex")
        runner = AgentTaskRunner()

        self.assertEqual(
            runner._resolve_agent_route(spec, {"provider": "same as task", "model": "same as task"}),
            ("copilot", "gpt-5.3-codex"),
        )
        self.assertEqual(
            runner._resolve_agent_route(spec, {"provider": "anthropic", "model": "claude-sonnet-4-5"}),
            ("anthropic", "claude-sonnet-4-5"),
        )
        self.assertEqual(
            runner._resolve_agent_route(spec, {"provider": "same as app", "model": "same as app"}),
            (None, None),
        )

    def test_runner_repairs_invalid_json_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2)
            responses = [
                "not json",
                json.dumps({"thought": "fixed", "tool_calls": [], "final": "Repaired final."}),
            ]

            def fake_model(_prompt: str) -> str:
                return responses.pop(0)

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Repaired final.")
            self.assertIn("model_response_repaired", (run_dir / "turns.json").read_text(encoding="utf-8"))

    def test_json_repair_uses_compact_excerpt_and_small_budget(self):
        runner = AgentTaskRunner()
        bad_response = "bad-start " + ("x" * 9000) + " bad-end"
        prompts: list[str] = []
        budgets: list[int] = []

        def fake_call_model(prompt, _log, *, provider=None, model=None, max_tokens=4096, temperature=0.0):
            prompts.append(prompt)
            budgets.append(max_tokens)
            return json.dumps({"thought": "fixed", "tool_calls": [], "final": "ok"})

        runner._call_model = fake_call_model  # type: ignore[method-assign]
        repaired = runner._repair_agent_response(bad_response, lambda _message: None)

        self.assertIsNotNone(repaired)
        self.assertEqual(budgets, [1024])
        self.assertLess(len(prompts[0]), 4500)
        self.assertIn(str(len(bad_response)), prompts[0])
        self.assertIn("middle truncated", prompts[0])

    def test_runner_emits_live_trace_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1)
            traces: list[str] = []
            response = json.dumps({"thought": "Done.", "tool_calls": [], "final": "Finished."})

            run_dir = AgentTaskRunner(
                log_root=logs,
                model_callback=lambda _prompt: response,
            ).run(spec, on_trace=traces.append)

            self.assertTrue(traces)
            self.assertIn("task spec", "".join(traces))
            self.assertEqual(
                "".join(traces),
                (run_dir / "verbose.log").read_text(encoding="utf-8"),
            )

    def test_runner_requests_large_json_response_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1, approval_policy="never")
            response = json.dumps({"thought": "Done.", "tool_calls": [], "final": "Finished."})

            with patch("core.llm.stream_response", return_value=iter([response])) as stream_response:
                AgentTaskRunner(log_root=logs).run(spec)

            self.assertEqual(stream_response.call_args.kwargs["max_tokens"], 4096)
            self.assertEqual(stream_response.call_args.kwargs["temperature"], 0.0)

    def test_llm_call_failure_retries_instead_of_finishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2, approval_policy="never")
            response = json.dumps({"thought": "Recovered.", "tool_calls": [], "final": "Finished after retry."})
            calls = 0

            def fake_stream_response(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("provider 500")
                return iter([response])

            with patch("core.llm.stream_response", side_effect=fake_stream_response):
                run_dir = AgentTaskRunner(log_root=logs).run(spec)

            self.assertEqual(calls, 2)
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Finished after retry.")
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual(turns[0]["routing"]["status"], "retry")
            self.assertIn("LLM call failed", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_runner_extracts_fenced_json_after_model_prose(self):
        response = (
            "<thought>I should not have written this prose with {example} braces.</thought>\n"
            "```json\n"
            '{"thought": "ok", "tool_calls": [], "final": "Done."}\n'
            "```"
        )

        parsed = AgentTaskRunner._parse_agent_response(response)

        self.assertEqual(parsed["final"], "Done.")

    def test_runner_accepts_parameters_tool_call_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_file_create=True))

            result = AgentTaskRunner()._execute_tool_call(
                tools,
                {
                    "tool": "create_file",
                    "parameters": {"path": "random_utility.py", "content": "print('ok')\n"},
                },
            )

            self.assertTrue(result.ok)
            self.assertEqual(Path(tmp, "random_utility.py").read_text(encoding="utf-8"), "print('ok')\n")

    def test_runner_repairs_python_literal_response_locally(self):
        repaired = AgentTaskRunner._locally_repair_agent_response(
            "{'thought': 'ok', 'tool_calls': [], 'final': 'Done.'}"
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(AgentTaskRunner._parse_agent_response(repaired or "")["final"], "Done.")

    def test_runner_repairs_literal_newline_in_json_string_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1, approval_policy="never")
            bad_response = (
                '{\n'
                '  "thought": "Create file",\n'
                '  "tool_calls": [\n'
                '    {"tool": "create_file", "args": {"path": "note.txt", "content": "hello\nworld"}}\n'
                '  ],\n'
                '  "final": null\n'
                '}'
            )

            run_dir = AgentTaskRunner(
                log_root=logs,
                model_callback=lambda _prompt: bad_response,
            ).run(spec)

            self.assertEqual((scope / "note.txt").read_text(encoding="utf-8"), "hello\nworld")
            run_log = (run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("repaired invalid JSON locally", run_log)

    def test_runner_skips_model_repair_for_truncated_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1)
            calls = 0

            def fake_model(_prompt: str) -> str:
                nonlocal calls
                calls += 1
                return (
                    '{"thought": "Create file", "tool_calls": ['
                    '{"tool": "create_file_base64", "args": {"path": "note.txt", '
                    '"content_base64": "YWJj'
                )

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            self.assertEqual(calls, 1)
            run_log = (run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("invalid JSON appears truncated", run_log)
            self.assertIn("using local fallback for invalid JSON response", run_log)

    def test_runner_falls_back_when_json_repair_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1)
            responses = [
                '{"thought": "unterminated',
                '{"thought": "still broken',
            ]

            def fake_model(_prompt: str) -> str:
                return responses.pop(0)

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            final = (run_dir / "final.md").read_text(encoding="utf-8")
            run_log = (run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("turn limit", final)
            self.assertIn("using local fallback for invalid JSON response", run_log)

    def test_runner_can_be_cancelled_before_first_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            control = AgentRunControl()
            control.cancel()
            spec = DummySpec(scope_folder=str(scope))

            run_dir = AgentTaskRunner(
                log_root=logs,
                model_callback=lambda _prompt: self.fail("model should not be called"),
                control=control,
            ).run(spec)

            self.assertIn("cancelled", (run_dir / "run.log").read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
