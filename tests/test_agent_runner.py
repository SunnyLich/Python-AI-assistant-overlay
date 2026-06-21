"""Tests for test agent runner."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from core.agent.runner import (
    AgentPermissions,
    AgentRunControl,
    AgentTaskRunner,
    AgentToolbox,
    FileLeaseRegistry,
    PermissionDenied,
    ScopedWorkspace,
    ScopeViolation,
    ToolResult,
)


@dataclass
class DummySpec:
    """Store dummy spec configuration data."""
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
    parallel_execution: bool = False
    max_parallel_agents: int = 4


class ScopedWorkspaceTests(unittest.TestCase):
    """Test case for scoped workspace tests behavior."""
    def test_resolve_rejects_path_escape(self):
        """Verify resolve rejects path escape behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "scope"
            root.mkdir()
            ws = ScopedWorkspace(root)
            with self.assertRaises(ScopeViolation):
                ws.resolve(root / ".." / "outside.txt")

    def test_blocked_globs_hide_and_reject_files(self):
        """Verify blocked globs hide and reject files behavior."""
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
        """Verify write respects create and edit permissions behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = ScopedWorkspace(root)

            with self.assertRaises(PermissionError):
                ws.write_text("new.txt", "hello", create=False, edit=True)

            ws.write_text("new.txt", "hello", create=True, edit=False)

            with self.assertRaises(PermissionError):
                ws.write_text("new.txt", "changed", create=True, edit=False)


class AgentToolboxTests(unittest.TestCase):
    """Test case for agent toolbox tests behavior."""
    def test_toolbox_create_patch_and_read_are_scoped_and_logged(self):
        """Verify toolbox create patch and read are scoped and logged behavior."""
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
        """Verify toolbox rejects edit without permission behavior."""
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
        """Verify toolbox delete requires permission behavior."""
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
        """Verify toolbox command allowlist and shell permission behavior."""
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
        """Verify project verification commands are gated by manifest files behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = AgentToolbox(ScopedWorkspace(root), AgentPermissions(allow_shell=True))

            with self.assertRaises(PermissionDenied):
                tools.run_command(["npm", "test"])

            (root / "package.json").write_text('{"scripts":{"test":"node --version"}}', encoding="utf-8")
            self.assertIn(["npm", "test"], tools.verification_commands())
            self.assertIn(["npm", "run", "build"], tools.verification_commands())

    def test_verification_commands_include_visible_python_files(self):
        """Verify visible Python files get concrete py_compile commands."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tic_tac_toe.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "notes.txt").write_text("skip me\n", encoding="utf-8")
            tools = AgentToolbox(ScopedWorkspace(root), AgentPermissions(allow_shell=True))

            commands = tools.verification_commands()

            self.assertIn(["python", "-m", "py_compile", "tic_tac_toe.py"], commands)
            self.assertNotIn(["python", "-m", "py_compile", "notes.txt"], commands)

    def test_additional_static_verification_commands_are_allowlisted(self):
        """Verify additional static verification commands are allowlisted behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_shell=True))

            self.assertTrue(tools._is_command_allowed(["python", "-m", "pytest"]))
            self.assertTrue(tools._is_command_allowed(["python", "-m", "ruff", "check", "."]))
            self.assertTrue(tools._is_command_allowed(["node", "--check", "index.js"]))

    def test_approval_callback_can_decline_mutating_tools(self):
        """Verify approval callback can decline mutating tools behavior."""
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
        """Verify git status and diff use git permission without shell permission behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess_result = AgentToolbox(
                ScopedWorkspace(root),
                AgentPermissions(allow_git=True, allow_shell=False),
            ).run_command(["git", "status", "--short"])

            self.assertIn(subprocess_result.tool, {"run_command"})

    def test_git_tools_short_circuit_outside_worktree(self):
        """Verify git tools short circuit outside worktree behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            logs: list[str] = []
            tools = AgentToolbox(
                ScopedWorkspace(tmp),
                AgentPermissions(allow_git=True, allow_shell=False),
                log=logs.append,
            )

            with (
                patch.object(tools, "_find_git_root", return_value=None),
                patch("core.agent.toolbox.subprocess.run", side_effect=AssertionError("git should not spawn")),
            ):
                status = tools.git_status()
                diff = tools.git_diff()

            self.assertFalse(status.ok)
            self.assertFalse(diff.ok)
            self.assertEqual(status.data["returncode"], 128)
            self.assertIn("not a git repository", status.data["stderr"])
            self.assertTrue(any("git status --short" in line for line in logs))
            self.assertTrue(any("git diff -- ." in line for line in logs))

    def test_run_command_timeout_returns_tool_result(self):
        """Verify run command timeout returns tool result behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            tools = AgentToolbox(
                ScopedWorkspace(tmp),
                AgentPermissions(allow_shell=True),
            )

            with patch(
                "core.agent.toolbox.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["python", "-m", "unittest"], 2),
            ):
                result = tools.run_command(["python", "-m", "unittest"], timeout_seconds=2)

            self.assertFalse(result.ok)
            self.assertIn("timed out after 2s", result.message)
            self.assertEqual(result.data["timeout_seconds"], 2)


class AgentBoundaryAttackTests(unittest.TestCase):
    """Test case for agent boundary attack tests behavior."""
    def test_absolute_path_escape_is_rejected(self):
        """Verify absolute path escape is rejected behavior."""
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
        """Verify relative path traversal is rejected behavior."""
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
        """Verify blocked secret patterns cannot be read or written behavior."""
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
        """Verify dangerous shell commands are rejected even when shell allowed behavior."""
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
        """Verify agent loop logs denied attack and preserves files behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside")
            self.assertFalse(turns[0]["tool_results"][0]["ok"])
            self.assertIn("escapes scope", turns[0]["tool_results"][0]["message"])


class AgentRunnerTests(unittest.TestCase):
    """Test case for agent runner tests behavior."""
    def test_runner_executes_autonomous_tool_loop_and_writes_artifacts(self):
        """Verify runner executes autonomous tool loop and writes artifacts behavior."""
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
                """Verify fake model behavior."""
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
        """Verify runner routes agents and records messages behavior."""
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
                """Verify fake model behavior."""
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
        """Verify prompt lists only tools allowed by permissions behavior."""
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
        """Verify read only prompt omits write and shell tools even when enabled behavior."""
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
        """Verify runner honors next agent for multiple builder turns behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder", "Builder", "Reviewer", "Coordinator"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Reviewed.")

    def test_non_coordinator_final_routes_to_coordinator_for_completion(self):
        """Verify non coordinator final routes to coordinator for completion behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder", "Coordinator"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Coordinator final.")
            self.assertIn("completion requires Coordinator", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_non_reviewer_final_routes_to_reviewer_when_no_coordinator(self):
        """Verify non reviewer final routes to reviewer when no coordinator behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Builder", "Reviewer"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Reviewer final.")

    def test_reviewer_final_reports_to_coordinator_message_board(self):
        """Verify reviewer final reports to coordinator message board behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))

            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Reviewer", "Coordinator"])
            self.assertTrue(any(message["from"] == "Reviewer" and message["to"] == "Coordinator" for message in messages))
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Final accepted.")

    def test_coordinator_final_waits_for_pending_review(self):
        """Verify coordinator final waits for pending review behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))

            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Coordinator", "Reviewer", "Coordinator"])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Done.")

    def test_directed_message_recipient_gets_next_turn_without_explicit_handoff(self):
        """Verify directed message recipient gets next turn without explicit handoff behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Reviewer"])
            self.assertIn("routing by latest directed message", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_silent_handoff_creates_message(self):
        """Verify silent handoff creates message behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
            self.assertEqual(messages[0]["from"], "Coordinator")
            self.assertEqual(messages[0]["to"], "Builder")
            self.assertEqual(messages[0]["source"], "auto_handoff")

    def test_role_scoped_tools_deny_coordinator_shell(self):
        """Verify role scoped tools deny coordinator shell behavior."""
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
        """Verify role scoped tools deny coordinator file read behavior."""
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
        """Verify run command accepts command string behavior."""
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
        """Verify run command schema error includes correction behavior."""
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
        """Verify coordinator cannot assign install work behavior."""
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

    def test_send_message_log_marks_truncated_text(self):
        """Verify send message log marks truncated text behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            logs: list[str] = []
            long_message = "Build the app. " * 80
            result = AgentTaskRunner()._execute_agent_tool_call(
                AgentToolbox(ScopedWorkspace(tmp), AgentPermissions()),
                {"tool": "send_message", "args": {"to": "Builder", "message": long_message}},
                "Coordinator",
                [],
                {"messages": []},
                log=logs.append,
                active_agent={"name": "Coordinator", "role": "Coordinator"},
                spec=DummySpec(),
                task_state=AgentTaskRunner._initial_task_state([]),
            )

        self.assertTrue(result.ok)
        self.assertIn("... [truncated]", logs[0])

    def test_duplicate_successful_verification_is_skipped(self):
        """Verify duplicate successful verification is skipped behavior."""
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
                {"thought": "Verify once.", "status": "continue", "next_agent": "Builder", "tool_calls": [{"tool": "run_command", "args": {"args": ["python", "-m", "pytest"]}}], "final": None},
                {"thought": "Try equivalent verify.", "status": "continue", "next_agent": "Builder", "tool_calls": [{"tool": "run_command", "args": {"args": ["pytest"]}}], "final": None},
            ]

            def fake_model(_prompt: str) -> str:
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))

            self.assertIn("skipped duplicate successful verification", turns[-1]["tool_results"][0]["message"])
            self.assertEqual(turns[-1]["task_state"]["tests"]["pytest"], "passed")

    def test_parallel_read_only_briefing_converts_invalid_repair_into_tool_result(self):
        """Verify parallel read only briefing converts invalid repair into tool result behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1)
            agents = [
                {"name": "Planner", "role": "Planner", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            runner = AgentTaskRunner()
            tools = AgentToolbox(ScopedWorkspace(scope), AgentPermissions())
            turns: list[dict] = []
            agent_states = runner._initial_agent_states(agents)
            task_state = runner._initial_task_state([])

            with patch.object(runner, "_call_model", return_value="{"), \
                 patch.object(runner, "_repair_agent_response", return_value="still not json"):
                runner._run_parallel_read_only_round(
                    spec,
                    tools,
                    agents,
                    [],
                    [],
                    [],
                    turns,
                    agent_states,
                    task_state,
                    lambda _message: None,
                )

            self.assertEqual(len(turns), 2)
            self.assertTrue(all(turn["phase"] == "read_only_briefing" for turn in turns))
            self.assertTrue(all(turn["tool_results"] for turn in turns))
            self.assertTrue(all(turn["tool_results"][0]["tool"] == "response_parser" for turn in turns))
            self.assertTrue(all(turn["tool_results"][0]["ok"] is False for turn in turns))

    def test_git_not_repo_is_marked_unavailable(self):
        """Verify git not repo is marked unavailable behavior."""
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

    def test_read_only_briefing_marks_git_unavailable(self):
        """Verify read-only briefing shares git availability failures with the main run."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), allow_git=True)
            spec.agents = [
                {"name": "Reviewer", "role": "Reviewer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]

            def fake_model(_prompt: str) -> str:
                """Request git status during the briefing phase."""
                return json.dumps({
                    "thought": "Check repository state.",
                    "tool_calls": [{"tool": "git_status", "args": {}}],
                    "final": None,
                })

            runner = AgentTaskRunner(model_callback=fake_model)
            tools = AgentToolbox(ScopedWorkspace(scope), AgentPermissions(allow_git=True))
            agents = runner._normalise_agents(spec)
            task_state = runner._initial_task_state([])
            turns: list[dict] = []

            runner._run_parallel_read_only_round(
                spec,
                tools,
                agents,
                [],
                [],
                [],
                turns,
                runner._initial_agent_states(agents),
                task_state,
                lambda _message: None,
            )

            self.assertIs(task_state["git_available"], False)
            self.assertEqual(task_state["git_reason"], "not a git repository")
            self.assertIn("git", task_state["disabled_tools"])
            self.assertEqual(turns[0]["tool_results"][0]["tool"], "run_command")

    def test_full_run_carries_briefing_git_failure_and_specific_python_verification(self):
        """Verify a realistic non-git Python task stops advertising git and suggests py_compile."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            (scope / "tic_tac_toe.py").write_text("print('ok')\n", encoding="utf-8")
            spec = DummySpec(scope_folder=str(scope), max_turns=4, allow_git=True, allow_shell=True)
            spec.git_permission_mode = "auto"
            spec.agents = [
                {"name": "Coordinator", "role": "Coordinator", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            prompts: list[str] = []
            coordinator_turns = 0

            def fake_call_model(prompt, _log, *, provider=None, model=None, fallbacks=None, max_tokens=4096, temperature=0.0):
                """Drive a tiny multi-agent run through read-only briefing and one handoff."""
                nonlocal coordinator_turns
                prompts.append(prompt)
                if "Parallel read-only briefing phase" in prompt:
                    if "Active agent: Builder" in prompt:
                        return json.dumps({
                            "thought": "Check repository state.",
                            "tool_calls": [{"tool": "git_status", "args": {}}],
                            "final": None,
                        })
                    return json.dumps({"thought": "Brief.", "tool_calls": [], "final": None})
                if "Active agent: Builder" in prompt:
                    return json.dumps({"thought": "Implement.", "tool_calls": [], "final": "Builder done."})
                coordinator_turns += 1
                if coordinator_turns > 1:
                    return json.dumps({"thought": "Approve.", "tool_calls": [], "final": "Done."})
                return json.dumps({
                    "thought": "Hand off.",
                    "status": "continue",
                    "next_agent": "Builder",
                    "reason": "Builder should implement.",
                    "tool_calls": [{"tool": "send_message", "args": {"to": "Builder", "message": "Please implement."}}],
                    "final": None,
                })

            runner = AgentTaskRunner(log_root=logs)
            runner._call_model = fake_call_model  # type: ignore[method-assign]
            run_dir = runner.run(spec)

            builder_prompts = [
                prompt for prompt in prompts
                if "Active agent: Builder" in prompt and "Parallel read-only briefing phase" not in prompt
            ]
            self.assertTrue(builder_prompts)
            self.assertNotIn("- git_status args: {}", builder_prompts[0])
            self.assertIn("- python -m py_compile tic_tac_toe.py", builder_prompts[0])
            self.assertIn(
                "shared task state: git marked unavailable",
                (run_dir / "run.log").read_text(encoding="utf-8"),
            )

    def test_run_log_includes_schema_tool_failures(self):
        """Verify malformed tool calls are visible in run.log, not only turns.json."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1, allow_shell=True)
            spec.agents = [
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            response = json.dumps({
                "thought": "Try malformed command.",
                "status": "continue",
                "next_agent": "Builder",
                "tool_calls": [{"tool": "run_command", "args": {}}],
                "final": None,
            })

            run_dir = AgentTaskRunner(log_root=logs, model_callback=lambda _prompt: response).run(spec)

            run_log = (run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("tool run_command failed: schema_error", run_log)

    def test_developer_alias_routes_to_implementer(self):
        """Verify developer alias routes to implementer behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder"])

    def test_broadcast_message_routes_to_next_agent(self):
        """Verify broadcast message routes to next agent behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder"])
            self.assertIn("routing by broadcast message", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_repeated_tool_failure_guard_routes_away(self):
        """Verify repeated tool failure guard routes away behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Builder", "Builder", "Builder", "Coordinator"])
            self.assertIn("repeated failure guard", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_explicit_next_agent_overrides_directed_message_recipient(self):
        """Verify explicit next agent overrides directed message recipient behavior."""
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
                """Verify fake model behavior."""
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual([turn["agent"] for turn in turns], ["Coordinator", "Builder"])
            self.assertIn("routing by explicit next_agent", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_runner_keeps_persistent_history_per_agent(self):
        """Verify runner keeps persistent history per agent behavior."""
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
                """Verify fake model behavior."""
                prompts.append(prompt)
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            self.assertIn("Continue as the active agent", prompts[1])
            self.assertIn("Your recent history:", prompts[1])
            self.assertIn("Thought: Remember this private note.", prompts[1])
            states = json.loads((run_dir / "agent_states.json").read_text(encoding="utf-8"))
            self.assertIn("Thought: Remember this private note.", states["Solo"]["history"])

    def test_delta_prompt_clips_repeated_conversation_state(self):
        """Verify delta prompt clips repeated conversation state behavior."""
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
        """Verify runner injects manual nudge before next prompt behavior."""
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
                """Verify fake model behavior."""
                prompts.append(prompt)
                if len(prompts) == 1:
                    control.add_nudge("Solo", "Please focus on tests.")
                return json.dumps(responses.pop(0))

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model, control=control).run(spec)

            messages = json.loads((run_dir / "messages.json").read_text(encoding="utf-8"))
            self.assertEqual(messages[0]["from"], "User")
            self.assertIn("Please focus on tests.", prompts[1])

    def test_manual_nudge_dedupes_identical_recent_message(self):
        """Verify manual nudge dedupes identical recent message behavior."""
        messages = [{"from": "User", "to": "ALL", "message": "Please inspect.", "source": "manual_nudge"}]
        control = AgentRunControl()
        control.add_nudge("ALL", "Please inspect.")
        logs: list[str] = []

        AgentTaskRunner(control=control)._apply_manual_nudges(messages, logs.append)

        self.assertEqual(len(messages), 1)
        self.assertEqual(logs, [])

    def test_control_pause_and_resume(self):
        """Verify control pause and resume behavior."""
        control = AgentRunControl()
        control.pause_after_turn()
        self.assertTrue(control.is_pause_requested())
        control.resume()
        control.wait_if_paused()
        self.assertFalse(control.is_pause_requested())

    def test_parallel_read_only_round_allows_messages_but_denies_writes(self):
        """Verify parallel read only round allows messages but denies writes behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope))
            spec.agents = [
                {"name": "Scout", "role": "Researcher", "provider": "same as task", "model": "same as task", "responsibility": ""},
                {"name": "Builder", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]

            def fake_model(prompt: str) -> str:
                """Verify fake model behavior."""
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
            task_state = runner._initial_task_state([])
            logs: list[str] = []
            runner._run_parallel_read_only_round(
                spec,
                tools,
                agents,
                [],
                [],
                messages,
                turns,
                states,
                task_state,
                logs.append,
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
            self.assertTrue(any("tool create_file failed" in line for line in logs))

    def test_tool_results_are_compacted_for_followup_prompts(self):
        """Verify tool results are compacted for followup prompts behavior."""
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
        """Verify runner uses smaller read only token budget behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope))
            spec.agents = [
                {"name": "Scout", "role": "Researcher", "provider": "same as task", "model": "same as task", "responsibility": ""},
            ]
            runner = AgentTaskRunner()
            calls: list[int] = []

            def fake_call_model(_prompt, _log, *, provider=None, model=None, fallbacks=None, max_tokens=4096, temperature=0.0):
                """Verify fake call model behavior."""
                calls.append(max_tokens)
                return json.dumps({"thought": "Brief.", "tool_calls": [], "final": None})

            runner._call_model = fake_call_model  # type: ignore[method-assign]
            agents = runner._normalise_agents(spec)
            task_state = runner._initial_task_state([])
            runner._run_parallel_read_only_round(
                spec,
                AgentToolbox(ScopedWorkspace(scope), AgentPermissions()),
                agents,
                [],
                [],
                [],
                [],
                runner._initial_agent_states(agents),
                task_state,
                lambda _message: None,
            )

            self.assertEqual(calls, [3072])

    def test_mutating_tools_run_under_write_lock(self):
        """Verify mutating tools run under write lock behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            runner = AgentTaskRunner()
            tools = AgentToolbox(ScopedWorkspace(tmp), AgentPermissions(allow_file_create=True))
            observed = []

            def fake_unlocked(_tools, tool, _args):
                """Verify fake unlocked behavior."""
                observed.append((tool, runner._write_lock.locked()))
                return ToolResult(tool, True, "ok")

            runner._execute_tool_call_unlocked = fake_unlocked  # type: ignore[method-assign]

            runner._execute_tool_call(tools, {"tool": "create_file", "args": {"path": "x.txt", "content": "x"}})
            runner._execute_tool_call(tools, {"tool": "read_file", "args": {"path": "x.txt"}})

            self.assertEqual(observed, [("create_file", True), ("read_file", False)])

    def test_placeholder_file_path_returns_actionable_error(self):
        """Verify placeholder file path returns actionable error behavior."""
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
        """Verify tool call accepts function alias behavior."""
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
        """Verify tool call accepts nested function arguments behavior."""
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
        """Verify base64 file tools avoid large json quoting behavior."""
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
        """Verify runner resolves agent provider and model route behavior."""
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
        """Verify runner repairs invalid json once behavior."""
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
                """Verify fake model behavior."""
                return responses.pop(0)

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Repaired final.")
            self.assertIn("model_response_repaired", (run_dir / "turns.json").read_text(encoding="utf-8"))

    def test_json_repair_uses_compact_excerpt_and_small_budget(self):
        """Verify json repair uses compact excerpt and small budget behavior."""
        runner = AgentTaskRunner()
        # A complete protocol object (balanced braces, protocol keys) that is still
        # unparseable (unquoted key) and large enough to force excerpting: the case
        # a model repair call is actually meant for.
        bad_response = (
            '{badkey: "' + ("x" * 9000) + '", "tool_calls": [], '
            '"status": "continue", "final": null}'
        )
        prompts: list[str] = []
        budgets: list[int] = []

        def fake_call_model(prompt, _log, *, provider=None, model=None, fallbacks=None, max_tokens=4096, temperature=0.0):
            """Verify fake call model behavior."""
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
        """Verify runner emits live trace entries behavior."""
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
        """Verify runner requests large json response budget behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1, approval_policy="never")
            response = json.dumps({"thought": "Done.", "tool_calls": [], "final": "Finished."})

            with patch("core.llm_clients.client.stream_response", return_value=iter([response])) as stream_response:
                AgentTaskRunner(log_root=logs).run(spec)

            self.assertEqual(stream_response.call_args.kwargs["max_tokens"], 8192)
            self.assertEqual(stream_response.call_args.kwargs["temperature"], 0.0)

    def test_zero_token_budget_passes_through_as_no_cap(self):
        """Verify zero token budget passes through as no cap behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1, approval_policy="never")
            spec.full_turn_max_tokens = 0  # "No limit (model max)"
            response = json.dumps({"thought": "Done.", "tool_calls": [], "final": "Finished."})

            with patch("core.llm_clients.client.stream_response", return_value=iter([response])) as stream_response:
                AgentTaskRunner(log_root=logs).run(spec)

            # 0 is forwarded verbatim so the client omits the cap for the provider.
            self.assertEqual(stream_response.call_args.kwargs["max_tokens"], 0)

    def test_llm_call_failure_retries_instead_of_finishing(self):
        """Verify llm call failure retries instead of finishing behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=2, approval_policy="never")
            response = json.dumps({"thought": "Recovered.", "tool_calls": [], "final": "Finished after retry."})
            calls = 0

            def fake_stream_response(*_args, **_kwargs):
                """Verify fake stream response behavior."""
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("provider 500")
                return iter([response])

            with patch("core.llm_clients.client.stream_response", side_effect=fake_stream_response):
                run_dir = AgentTaskRunner(log_root=logs).run(spec)

            self.assertEqual(calls, 2)
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Finished after retry.")
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual(turns[0]["routing"]["status"], "retry")
            self.assertIn("LLM call failed", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_llm_auth_failure_stops_instead_of_retrying(self):
        """Verify permanent auth failures stop instead of consuming every turn."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=5, approval_policy="never")
            calls = 0
            error = (
                "All query model routes failed. Tried chatgpt/gpt-5.5: "
                "LLM route uses chatgpt but you are not logged in."
            )

            def fake_stream_response(*_args, **_kwargs):
                """Raise the auth failure reported by ChatGPT routes without local login."""
                nonlocal calls
                calls += 1
                raise RuntimeError(error)

            with patch("core.llm_clients.client.stream_response", side_effect=fake_stream_response):
                run_dir = AgentTaskRunner(log_root=logs).run(spec)

            self.assertEqual(calls, 1)
            final = (run_dir / "final.md").read_text(encoding="utf-8")
            self.assertIn("selected model route is not available", final)
            self.assertIn("not logged in", final)
            turns = json.loads((run_dir / "turns.json").read_text(encoding="utf-8"))
            self.assertEqual(turns[0]["routing"]["status"], "model_blocked")
            self.assertIn(
                "requires authentication or configuration",
                (run_dir / "run.log").read_text(encoding="utf-8"),
            )

    def test_runner_extracts_fenced_json_after_model_prose(self):
        """Verify runner extracts fenced json after model prose behavior."""
        response = (
            "<thought>I should not have written this prose with {example} braces.</thought>\n"
            "```json\n"
            '{"thought": "ok", "tool_calls": [], "final": "Done."}\n'
            "```"
        )

        parsed = AgentTaskRunner._parse_agent_response(response)

        self.assertEqual(parsed["final"], "Done.")

    def test_runner_extracts_json_after_unfenced_prose_with_braces(self):
        # Gemini/Gemma emit a <thought> preamble (no fence) that contains code
        # snippets with stray braces; the naive first-{/last-} slice grabbed those.
        """Verify runner extracts json after unfenced prose with braces behavior."""
        response = (
            "<thought>The user wants a label like f\"score {n}\" rendered each frame.\n"
            "I will read the file and patch it.</thought>\n"
            '{"thought": "patching", "status": "continue", "next_agent": "Builder", '
            '"reason": "implement", "tool_calls": [], "final": null}'
        )

        parsed = AgentTaskRunner._parse_agent_response(response)

        self.assertEqual(parsed["next_agent"], "Builder")
        self.assertEqual(parsed["tool_calls"], [])

    def test_runner_selects_protocol_object_over_embedded_fragment(self):
        # A reasoning preamble that quotes a lone tool-call fragment must not be
        # mistaken for the real protocol object (which carries the protocol keys).
        """Verify runner selects protocol object over embedded fragment behavior."""
        response = (
            "I plan to message the builder, e.g. "
            '{"name": "send_message", "arguments": {"to": "Builder"}}.\n'
            '{"thought": "go", "status": "continue", "next_agent": "Builder", '
            '"reason": "handoff", "tool_calls": [], "final": null}'
        )

        parsed = AgentTaskRunner._parse_agent_response(response)

        self.assertEqual(parsed["status"], "continue")
        self.assertEqual(parsed["reason"], "handoff")

    def test_truncated_response_routes_to_local_fallback_without_model_call(self):
        # A response cut off mid-JSON (unbalanced braces) must retry cheaply rather
        # than spend a model repair call that can only guess the lost content.
        """Verify truncated response routes to local fallback without model call behavior."""
        runner = AgentTaskRunner()
        truncated = (
            '{"thought": "writing", "tool_calls": [{"tool": "create_file", '
            '"args": {"path": "game.py", "content": "import pygame'
        )
        called = False

        def fake_call_model(*_args, **_kwargs):
            """Verify fake call model behavior."""
            nonlocal called
            called = True
            return "{}"

        runner._call_model = fake_call_model  # type: ignore[method-assign]
        repaired = runner._repair_agent_response(truncated, lambda _message: None)

        self.assertFalse(called)
        self.assertEqual(AgentTaskRunner._parse_agent_response(repaired or "")["status"], "retry")

    def test_runner_accepts_parameters_tool_call_alias(self):
        """Verify runner accepts parameters tool call alias behavior."""
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
        """Verify runner repairs python literal response locally behavior."""
        repaired = AgentTaskRunner._locally_repair_agent_response(
            "{'thought': 'ok', 'tool_calls': [], 'final': 'Done.'}"
        )

        self.assertIsNotNone(repaired)
        self.assertEqual(AgentTaskRunner._parse_agent_response(repaired or "")["final"], "Done.")

    def test_runner_repairs_literal_newline_in_json_string_locally(self):
        """Verify runner repairs literal newline in json string locally behavior."""
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
        """Verify runner skips model repair for truncated file content behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=1)
            calls = 0

            def fake_model(_prompt: str) -> str:
                """Verify fake model behavior."""
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
            self.assertIn("no complete protocol object to repair", run_log)
            self.assertIn("using local fallback for invalid JSON response", run_log)

    def test_runner_falls_back_when_json_repair_is_invalid(self):
        """Verify runner falls back when json repair is invalid behavior."""
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
                """Verify fake model behavior."""
                return responses.pop(0)

            run_dir = AgentTaskRunner(log_root=logs, model_callback=fake_model).run(spec)

            final = (run_dir / "final.md").read_text(encoding="utf-8")
            run_log = (run_dir / "run.log").read_text(encoding="utf-8")
            self.assertIn("turn limit", final)
            self.assertIn("using local fallback for invalid JSON response", run_log)

    def test_runner_can_be_cancelled_before_first_turn(self):
        """Verify runner can be cancelled before first turn behavior."""
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


class FileLeaseRegistryTests(unittest.TestCase):
    """Test case for file lease registry tests behavior."""
    def test_exclusive_acquire_blocks_other_agents_only(self):
        """Verify exclusive acquire blocks other agents only behavior."""
        leases = FileLeaseRegistry()

        self.assertIsNone(leases.acquire("Alpha", "a.py"))
        # Same agent re-acquiring its own lease is a no-op, not a conflict.
        self.assertIsNone(leases.acquire("Alpha", "a.py"))
        # Another agent is blocked and told who holds it.
        self.assertEqual(leases.acquire("Beta", "a.py"), "Alpha")
        # A disjoint file is free.
        self.assertIsNone(leases.acquire("Beta", "b.py"))

    def test_release_returns_file_to_the_pool(self):
        """Verify release returns file to the pool behavior."""
        leases = FileLeaseRegistry()
        leases.acquire("Alpha", "a.py")
        leases.release("Alpha", ["a.py"])

        self.assertIsNone(leases.holder("a.py"))
        self.assertIsNone(leases.acquire("Beta", "a.py"))

    def test_release_only_affects_caller_owned_leases(self):
        """Verify release only affects caller owned leases behavior."""
        leases = FileLeaseRegistry()
        leases.acquire("Alpha", "a.py")
        # Beta cannot release Alpha's lease.
        leases.release("Beta", ["a.py"])

        self.assertEqual(leases.holder("a.py"), "Alpha")

    def test_claim_partitions_granted_and_denied(self):
        """Verify claim partitions granted and denied behavior."""
        leases = FileLeaseRegistry()
        leases.acquire("Alpha", "shared.py")
        granted, denied = leases.claim("Beta", ["shared.py", "own.py"])

        self.assertEqual(granted, ["own.py"])
        self.assertEqual(denied, {"shared.py": "Alpha"})
        self.assertEqual(sorted(leases.held_by("Beta")), ["own.py"])


class ParallelWorkRoundTests(unittest.TestCase):
    """Test case for parallel work round tests behavior."""
    def _workers(self):
        """Verify workers behavior."""
        return [
            {"name": "Alpha", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
            {"name": "Beta", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""},
        ]

    def _run_round(self, scope, fake_model):
        """Verify run round behavior."""
        spec = DummySpec(scope_folder=str(scope), parallel_execution=True)
        spec.agents = self._workers()
        runner = AgentTaskRunner(model_callback=fake_model)
        tools = AgentToolbox(ScopedWorkspace(scope), AgentPermissions(allow_file_create=True, allow_file_edit=True))
        agents = runner._normalise_agents(spec)
        messages: list[dict] = []
        turns: list[dict] = []
        states = runner._initial_agent_states(agents)
        task_state = runner._initial_task_state([])
        runner._run_parallel_work_round(
            spec, tools, agents, [], [], messages, turns, states, task_state, lambda _message: None,
        )
        return turns

    def test_disjoint_files_are_written_in_parallel(self):
        """Verify disjoint files are written in parallel behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()

            def fake_model(prompt: str) -> str:
                """Verify fake model behavior."""
                if "Active agent: Alpha" in prompt:
                    return json.dumps({"thought": "build alpha", "tool_calls": [{"tool": "create_file", "args": {"path": "alpha.txt", "content": "alpha"}}], "final": None})
                return json.dumps({"thought": "build beta", "tool_calls": [{"tool": "create_file", "args": {"path": "beta.txt", "content": "beta"}}], "final": None})

            turns = self._run_round(scope, fake_model)

            self.assertEqual((scope / "alpha.txt").read_text(encoding="utf-8"), "alpha")
            self.assertEqual((scope / "beta.txt").read_text(encoding="utf-8"), "beta")
            self.assertEqual({turn["agent"] for turn in turns}, {"Alpha", "Beta"})
            self.assertTrue(all(turn["phase"] == "parallel_work" for turn in turns))
            self.assertTrue(all(r["ok"] for turn in turns for r in turn["tool_results"]))

    def test_same_file_write_is_leased_to_exactly_one_agent(self):
        """Verify same file write is leased to exactly one agent behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()

            def fake_model(prompt: str) -> str:
                """Verify fake model behavior."""
                content = "alpha" if "Active agent: Alpha" in prompt else "beta"
                return json.dumps({"thought": "edit shared", "tool_calls": [{"tool": "write_file", "args": {"path": "shared.txt", "content": content}}], "final": None})

            turns = self._run_round(scope, fake_model)
            results = [r for turn in turns for r in turn["tool_results"] if r["tool"] == "write_file"]
            granted = [r for r in results if r["ok"]]
            denied = [r for r in results if not r["ok"]]

            # Exactly one writer wins; the other is safely rejected, not interleaved.
            self.assertEqual(len(granted), 1)
            self.assertEqual(len(denied), 1)
            self.assertEqual(denied[0]["data"]["error_type"], "file_leased")
            self.assertIn((scope / "shared.txt").read_text(encoding="utf-8"), {"alpha", "beta"})

    def test_round_is_skipped_with_fewer_than_two_workers(self):
        """Verify round is skipped with fewer than two workers behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), parallel_execution=True)
            spec.agents = [{"name": "Solo", "role": "Implementer", "provider": "same as task", "model": "same as task", "responsibility": ""}]
            called = False

            def fake_model(_prompt: str) -> str:
                """Verify fake model behavior."""
                nonlocal called
                called = True
                return json.dumps({"thought": "x", "tool_calls": [], "final": None})

            runner = AgentTaskRunner(model_callback=fake_model)
            agents = runner._normalise_agents(spec)
            turns: list[dict] = []
            runner._run_parallel_work_round(
                spec,
                AgentToolbox(ScopedWorkspace(scope), AgentPermissions(allow_file_create=True)),
                agents, [], [], [], turns, runner._initial_agent_states(agents),
                runner._initial_task_state([]), lambda _message: None,
            )

            self.assertEqual(turns, [])
            self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
