from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import tempfile
import unittest

from core.agent_runner import (
    AgentCancelled,
    AgentPermissions,
    AgentRunControl,
    AgentTaskRunner,
    AgentToolbox,
    PermissionDenied,
    ScopedWorkspace,
    ScopeViolation,
)


@dataclass
class DummySpec:
    title: str = "Test Agent"
    objective: str = "Inspect the project and make a plan."
    scope_folder: str = ""
    sandbox_mode: str = "workspace-write: scope folder only"
    approval_policy: str = "ask before escalation"
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
            self.assertTrue((run_dir / "verbose.log").exists())
            self.assertIn("tool call", (run_dir / "verbose.log").read_text(encoding="utf-8"))
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Changed the greeting and verified syntax.")
            self.assertEqual((scope / "app.py").read_text(encoding="utf-8"), "print('hello')")
            self.assertIn("agent run finished", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_runner_round_robins_agents_and_records_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp) / "scope"
            logs = Path(tmp) / "logs"
            scope.mkdir()
            spec = DummySpec(scope_folder=str(scope), max_turns=3)
            spec.agents = [
                {
                    "name": "Planner",
                    "role": "Planner",
                    "model": "same as task",
                    "responsibility": "Decide what to inspect.",
                },
                {
                    "name": "Reviewer",
                    "role": "Reviewer",
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
            self.assertIn("Active agent: Reviewer", prompts[1])
            self.assertIn("Please review the empty scope.", prompts[1])
            self.assertEqual((run_dir / "final.md").read_text(encoding="utf-8"), "Reviewer received Planner's message.")

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
