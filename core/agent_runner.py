"""
core/agent_runner.py - Scoped background agent task runner.

This is the first real execution layer behind the tray "Start agent task"
dialog.  It deliberately starts conservative: validate the hard filesystem
scope, log every step, inventory allowed files, and ask the configured LLM for
an implementation plan.  File mutation is routed through ScopedWorkspace so the
next iteration can add edit tools without weakening the boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path
import fnmatch
import json
import subprocess
import threading
import traceback
from typing import Callable, Iterable, Protocol, Sequence


LogCallback = Callable[[str], None]
ModelCallback = Callable[[str], str]
ApprovalCallback = Callable[[dict], bool]


class AgentTaskLike(Protocol):
    title: str
    objective: str
    scope_folder: str
    sandbox_mode: str
    approval_policy: str
    model: str
    reasoning_effort: str
    max_runtime_minutes: int
    max_turns: int
    allow_shell: bool
    allow_network: bool
    allow_git: bool
    allow_file_create: bool
    allow_file_edit: bool
    allow_file_delete: bool
    allowed_file_globs: list[str]
    blocked_file_globs: list[str]
    required_context: str
    completion_criteria: str
    report_format: str
    agents: list
    communications: list


class ScopeViolation(ValueError):
    """Raised when an operation tries to escape the configured scope."""


class PermissionDenied(PermissionError):
    """Raised when task permissions do not allow a requested operation."""


class AgentCancelled(RuntimeError):
    """Raised when the user cancels a running agent task."""


class AgentRunControl:
    """Thread-safe cancellation token for a running agent task."""

    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise AgentCancelled("Agent task was cancelled by the user.")


@dataclass(frozen=True)
class AgentPermissions:
    """Capability flags derived from a task spec."""

    allow_shell: bool = False
    allow_network: bool = False
    allow_git: bool = False
    allow_file_create: bool = False
    allow_file_edit: bool = False
    allow_file_delete: bool = False

    @classmethod
    def from_spec(cls, spec: AgentTaskLike) -> "AgentPermissions":
        return cls(
            allow_shell=spec.allow_shell,
            allow_network=spec.allow_network,
            allow_git=spec.allow_git,
            allow_file_create=spec.allow_file_create,
            allow_file_edit=spec.allow_file_edit,
            allow_file_delete=spec.allow_file_delete,
        )


@dataclass(frozen=True)
class ToolResult:
    """Structured result returned by scoped tools."""

    tool: str
    ok: bool
    message: str
    data: dict | list | str | None = None


class ScopedWorkspace:
    """Filesystem facade that enforces a resolved folder boundary."""

    def __init__(
        self,
        scope_folder: str | Path,
        *,
        allowed_globs: Iterable[str] | None = None,
        blocked_globs: Iterable[str] | None = None,
    ):
        self.root = Path(scope_folder).expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"Invalid scope folder: {scope_folder}")
        self.allowed_globs = [g for g in (allowed_globs or []) if g]
        self.blocked_globs = [g for g in (blocked_globs or []) if g]

    def resolve(self, path: str | Path = ".") -> Path:
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self.root / raw
        candidate = candidate.expanduser().resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ScopeViolation(f"Path escapes scope: {candidate}")
        self._check_globs(candidate)
        return candidate

    def relative(self, path: str | Path) -> str:
        return str(self.resolve(path).relative_to(self.root)).replace("\\", "/")

    def list_files(self, *, limit: int = 300) -> list[str]:
        files: list[str] = []
        for path in self.root.rglob("*"):
            if len(files) >= limit:
                break
            if not path.is_file():
                continue
            try:
                self._check_globs(path.resolve())
            except ScopeViolation:
                continue
            rel = str(path.relative_to(self.root)).replace("\\", "/")
            files.append(rel)
        return files

    def read_text(self, path: str | Path, *, max_chars: int = 20_000) -> str:
        resolved = self.resolve(path)
        return resolved.read_text(encoding="utf-8", errors="replace")[:max_chars]

    def write_text(self, path: str | Path, content: str, *, create: bool, edit: bool) -> None:
        resolved = self.resolve(path)
        exists = resolved.exists()
        if exists and not edit:
            raise PermissionError("Editing files is disabled for this task.")
        if not exists and not create:
            raise PermissionError("Creating files is disabled for this task.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def patch_text(self, path: str | Path, old: str, new: str, *, edit: bool) -> int:
        if not edit:
            raise PermissionDenied("Editing files is disabled for this task.")
        if not old:
            raise ValueError("Patch old text cannot be empty.")
        resolved = self.resolve(path)
        text = resolved.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count != 1:
            raise ValueError(f"Patch expected exactly 1 match, found {count}.")
        resolved.write_text(text.replace(old, new, 1), encoding="utf-8")
        return 1

    def delete_file(self, path: str | Path, *, delete: bool) -> None:
        if not delete:
            raise PermissionDenied("Deleting files is disabled for this task.")
        resolved = self.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(str(resolved))
        if not resolved.is_file():
            raise PermissionDenied("Only file deletion is supported.")
        resolved.unlink()

    def _check_globs(self, path: Path) -> None:
        rel = str(path.relative_to(self.root)).replace("\\", "/") if path != self.root else "."
        if self.allowed_globs and not any(fnmatch.fnmatch(rel, g) for g in self.allowed_globs):
            raise ScopeViolation(f"Path is not in allowed globs: {rel}")
        if any(fnmatch.fnmatch(rel, g) for g in self.blocked_globs):
            raise ScopeViolation(f"Path is blocked by globs: {rel}")


class AgentToolbox:
    """Scoped tools available to the future autonomous agent loop."""

    _BASE_COMMAND_ALLOWLIST: tuple[tuple[str, ...], ...] = (
        ("python", "-m", "py_compile"),
        ("python", "-m", "unittest"),
        ("python", "-m", "pytest"),
        ("python", "-m", "ruff"),
        ("python", "-m", "mypy"),
        ("pytest",),
        ("ruff",),
        ("mypy",),
        ("rg",),
        ("node", "--check"),
    )
    _PROJECT_COMMAND_ALLOWLIST: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (("npm", "test"), ("package.json",)),
        (("npm", "run", "build"), ("package.json",)),
        (("cargo", "test"), ("Cargo.toml",)),
        (("go", "test"), ("go.mod",)),
    )
    _GIT_COMMAND_ALLOWLIST: tuple[tuple[str, ...], ...] = (
        ("git", "status"),
        ("git", "diff"),
    )

    def __init__(
        self,
        workspace: ScopedWorkspace,
        permissions: AgentPermissions,
        *,
        log: LogCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
        require_approval: bool = False,
    ):
        self.workspace = workspace
        self.permissions = permissions
        self._log = log
        self._approval_callback = approval_callback
        self._require_approval = require_approval

    def list_files(self, *, limit: int = 300) -> ToolResult:
        files = self.workspace.list_files(limit=limit)
        return self._result("list_files", True, f"{len(files)} file(s)", files)

    def read_file(self, path: str, *, max_chars: int = 20_000) -> ToolResult:
        text = self.workspace.read_text(path, max_chars=max_chars)
        return self._result("read_file", True, self.workspace.relative(path), text)

    def create_file(self, path: str, content: str) -> ToolResult:
        if not self.permissions.allow_file_create:
            raise PermissionDenied("Creating files is disabled for this task.")
        self._approve("create_file", {"path": path, "chars": len(content)})
        self.workspace.write_text(path, content, create=True, edit=False)
        return self._result("create_file", True, self.workspace.relative(path))

    def write_file(self, path: str, content: str) -> ToolResult:
        resolved = self.workspace.resolve(path)
        exists = resolved.exists()
        if exists and not self.permissions.allow_file_edit:
            raise PermissionDenied("Editing files is disabled for this task.")
        if not exists and not self.permissions.allow_file_create:
            raise PermissionDenied("Creating files is disabled for this task.")
        self._approve("write_file", {"path": path, "exists": exists, "chars": len(content)})
        self.workspace.write_text(
            path,
            content,
            create=self.permissions.allow_file_create,
            edit=self.permissions.allow_file_edit,
        )
        return self._result("write_file", True, self.workspace.relative(path))

    def patch_file(self, path: str, old: str, new: str) -> ToolResult:
        self._approve("patch_file", {"path": path, "old_chars": len(old), "new_chars": len(new)})
        count = self.workspace.patch_text(
            path,
            old,
            new,
            edit=self.permissions.allow_file_edit,
        )
        return self._result("patch_file", True, f"{self.workspace.relative(path)} patched", {"replacements": count})

    def delete_file(self, path: str) -> ToolResult:
        self._approve("delete_file", {"path": path})
        self.workspace.delete_file(path, delete=self.permissions.allow_file_delete)
        return self._result("delete_file", True, self.workspace.relative(path))

    def run_command(self, args: Sequence[str], *, timeout_seconds: int = 30) -> ToolResult:
        clean_args = [str(arg) for arg in args if str(arg)]
        if not clean_args:
            raise ValueError("Command cannot be empty.")
        if not self.permissions.allow_shell and not self._is_read_only_git_command(clean_args):
            raise PermissionDenied("Shell commands are disabled for this task.")
        if not self._is_command_allowed(clean_args):
            raise PermissionDenied(f"Command is not allowlisted: {' '.join(clean_args)}")
        if not self._is_read_only_git_command(clean_args):
            self._approve("run_command", {"args": clean_args})
        completed = subprocess.run(
            clean_args,
            cwd=str(self.workspace.root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
        data = {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-20_000:],
            "stderr": completed.stderr[-20_000:],
        }
        return self._result(
            "run_command",
            completed.returncode == 0,
            f"exit {completed.returncode}: {' '.join(clean_args)}",
            data,
        )

    def _is_command_allowed(self, args: list[str]) -> bool:
        allowed = list(self._BASE_COMMAND_ALLOWLIST)
        if self.permissions.allow_git:
            allowed.extend(self._GIT_COMMAND_ALLOWLIST)
        lowered = [arg.lower() for arg in args]
        for prefix in allowed:
            if lowered[: len(prefix)] == list(prefix):
                return True
        for prefix, required_files in self._PROJECT_COMMAND_ALLOWLIST:
            if lowered[: len(prefix)] != list(prefix):
                continue
            if all((self.workspace.root / required).exists() for required in required_files):
                return True
        return False

    def verification_commands(self) -> list[list[str]]:
        commands = [
            ["python", "-m", "unittest"],
            ["python", "-m", "pytest"],
            ["pytest"],
            ["python", "-m", "ruff", "check", "."],
            ["ruff", "check", "."],
            ["python", "-m", "mypy", "."],
            ["mypy", "."],
        ]
        if (self.workspace.root / "package.json").exists():
            commands.extend([["npm", "test"], ["npm", "run", "build"]])
        if (self.workspace.root / "Cargo.toml").exists():
            commands.append(["cargo", "test"])
        if (self.workspace.root / "go.mod").exists():
            commands.append(["go", "test", "./..."])
        return [cmd for cmd in commands if self._is_command_allowed(cmd)]

    def git_status(self) -> ToolResult:
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self.run_command(["git", "status", "--short"])

    def git_diff(self) -> ToolResult:
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self.run_command(["git", "diff", "--", "."])

    @staticmethod
    def _is_read_only_git_command(args: list[str]) -> bool:
        lowered = [arg.lower() for arg in args]
        return lowered[:2] in (["git", "status"], ["git", "diff"])

    def _approve(self, action: str, details: dict) -> None:
        if not self._require_approval:
            return
        if self._approval_callback is None:
            raise PermissionDenied(f"Approval required for {action}, but no approval UI is available.")
        request = {"action": action, "details": details}
        if not self._approval_callback(request):
            raise PermissionDenied(f"User declined {action}.")

    def _result(
        self,
        tool: str,
        ok: bool,
        message: str,
        data: dict | list | str | None = None,
    ) -> ToolResult:
        result = ToolResult(tool=tool, ok=ok, message=message, data=data)
        if self._log:
            self._log(f"tool {tool}: {message}")
        return result


class AgentTaskRunner:
    """Runs one agent task in a background thread and writes an auditable log."""

    def __init__(
        self,
        log_root: str | Path | None = None,
        *,
        model_callback: ModelCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
        control: AgentRunControl | None = None,
    ):
        repo_root = Path(__file__).resolve().parents[1]
        self.log_root = Path(log_root) if log_root else repo_root / "memory" / "agent_runs"
        self._model_callback = model_callback
        self._approval_callback = approval_callback
        self._control = control or AgentRunControl()

    def start(self, spec: AgentTaskLike, on_log: LogCallback | None = None) -> threading.Thread:
        thread = threading.Thread(target=self.run, args=(spec, on_log), daemon=True)
        thread.start()
        return thread

    def run(self, spec: AgentTaskLike, on_log: LogCallback | None = None) -> Path:
        run_dir = self._make_run_dir(spec.title)
        log_path = run_dir / "run.log"
        verbose_path = run_dir / "verbose.log"

        def log(message: str) -> None:
            stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(stamped + "\n")
            if on_log:
                on_log(stamped)

        def verbose(label: str, payload) -> None:  # noqa: ANN001
            stamped = f"\n[{datetime.now().strftime('%H:%M:%S')}] {label}\n"
            text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
            with verbose_path.open("a", encoding="utf-8") as f:
                f.write(stamped)
                f.write(self._truncate(text, 60_000))
                f.write("\n")

        try:
            log("agent run started")
            self._control.raise_if_cancelled()
            verbose("task spec", self._spec_dict(spec))
            workspace = ScopedWorkspace(
                spec.scope_folder,
                allowed_globs=spec.allowed_file_globs,
                blocked_globs=spec.blocked_file_globs,
            )
            permissions = AgentPermissions.from_spec(spec)
            require_approval = "ask" in spec.approval_policy.lower()
            tools = AgentToolbox(
                workspace,
                permissions,
                log=log,
                approval_callback=self._approval_callback,
                require_approval=require_approval,
            )
            self._write_json(run_dir / "task.json", self._spec_dict(spec))
            self._write_json(run_dir / "permissions.json", asdict(permissions))
            log(f"scope: {workspace.root}")
            log(f"sandbox: {spec.sandbox_mode}")

            files_result = tools.list_files()
            files = files_result.data if isinstance(files_result.data, list) else []
            self._write_json(run_dir / "files.json", files)
            verbose("visible files", files)
            log(f"inventory complete: {len(files)} file(s) visible")
            verify_commands = tools.verification_commands() if permissions.allow_shell else []
            self._write_json(run_dir / "verification_commands.json", verify_commands)
            verbose("allowed verification commands", verify_commands)

            final, turns, messages = self._run_agent_loop(spec, tools, files, verify_commands, log, verbose)
            (run_dir / "turns.json").write_text(
                json.dumps(turns, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (run_dir / "messages.json").write_text(
                json.dumps(messages, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._write_diff_artifacts(run_dir, tools, permissions, log, verbose)
            (run_dir / "final.md").write_text(final, encoding="utf-8")
            verbose("final report", final)
            log("final report written")
            log(f"run artifacts: {run_dir}")
            log("agent run finished")
        except AgentCancelled as exc:
            log(str(exc))
            (run_dir / "final.md").write_text(str(exc), encoding="utf-8")
            log("agent run cancelled")
        except Exception as exc:
            log(f"ERROR: {exc}")
            verbose("error traceback", traceback.format_exc())
            (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            log("agent run failed")
        return run_dir

    def _run_agent_loop(
        self,
        spec: AgentTaskLike,
        tools: AgentToolbox,
        files: list[str],
        verify_commands: list[list[str]],
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
    ) -> tuple[str, list[dict], list[dict]]:
        agents = self._normalise_agents(spec)
        messages: list[dict] = []
        self._seed_communication_rules(spec, messages, log)
        turns: list[dict] = []
        tool_context = ""
        max_turns = max(1, int(spec.max_turns))

        for turn_idx in range(max_turns):
            self._control.raise_if_cancelled()
            agent = agents[turn_idx % len(agents)]
            agent_name = agent["name"]
            log(f"agent turn {turn_idx + 1}/{max_turns}: {agent_name}")
            model_input = self._build_agent_prompt(
                spec,
                files,
                verify_commands,
                active_agent=agent,
                messages=messages,
            )
            if tool_context:
                model_input += "\n\nPrevious tool results:\n" + tool_context
            if verbose:
                verbose(f"turn {turn_idx + 1} model input", model_input)
            response_text = self._call_model(model_input, log)
            if verbose:
                verbose(f"turn {turn_idx + 1} model response", response_text)
            turn: dict = {
                "turn": turn_idx + 1,
                "agent": agent_name,
                "model_response": response_text,
                "tool_results": [],
                "messages": [],
            }
            turns.append(turn)

            try:
                parsed = self._parse_agent_response(response_text)
            except ValueError as exc:
                log(f"agent response parse failed: {exc}")
                repaired = self._repair_agent_response(response_text, log, verbose)
                if repaired is None:
                    return f"Agent stopped because the model returned invalid JSON.\n\n{exc}", turns, messages
                response_text = repaired
                turn["model_response_repaired"] = repaired
                try:
                    parsed = self._parse_agent_response(repaired)
                except ValueError as repair_exc:
                    log(f"agent response repair failed: {repair_exc}")
                    return f"Agent stopped because JSON repair failed.\n\n{repair_exc}", turns, messages
            if verbose:
                verbose(f"turn {turn_idx + 1} parsed response", parsed)
            thought = str(parsed.get("thought") or "").strip()
            if thought:
                log(f"{agent_name} thought: {thought}")
            final = str(parsed.get("final") or "").strip()
            calls = parsed.get("tool_calls") or []
            if final and not calls:
                log(f"{agent_name} returned final response")
                return final, turns, messages
            if not isinstance(calls, list) or not calls:
                fallback = final or response_text.strip() or "Agent stopped without tool calls."
                log("agent stopped without tool calls")
                return fallback, turns, messages

            results: list[dict] = []
            for call in calls:
                self._control.raise_if_cancelled()
                if isinstance(call, dict):
                    tool_name = str(call.get("tool") or "unknown")
                    log(f"{agent_name} tool call: {tool_name}")
                if verbose:
                    verbose(f"turn {turn_idx + 1} tool call", call)
                result = self._execute_agent_tool_call(tools, call, agent_name, messages, turn)
                result_dict = asdict(result)
                if verbose:
                    verbose(f"turn {turn_idx + 1} tool result", result_dict)
                results.append(result_dict)
                turn["tool_results"].append(result_dict)
            tool_context = json.dumps(results, indent=2, ensure_ascii=False)

        log("agent reached turn limit")
        return "Agent stopped after reaching the configured turn limit.", turns, messages

    def _build_agent_prompt(
        self,
        spec: AgentTaskLike,
        files: list[str],
        verify_commands: list[list[str]] | None = None,
        active_agent: dict | None = None,
        messages: list[dict] | None = None,
    ) -> str:
        verify_commands = verify_commands or []
        agents = self._normalise_agents(spec)
        communications = getattr(spec, "communications", []) or []
        active_agent = active_agent or agents[0]
        messages = messages or []

        def field_value(obj, name: str) -> str:  # noqa: ANN001
            if isinstance(obj, dict):
                return str(obj.get(name, "") or "")
            return str(getattr(obj, name, "") or "")

        agent_lines = []
        for agent in agents:
            name = agent["name"]
            role = agent["role"]
            model = agent["model"]
            responsibility = agent["responsibility"]
            agent_lines.append(f"- {name} ({role}, {model}): {responsibility}")
        communication_lines = []
        for comm in communications:
            source = field_value(comm, "from_agent")
            target = field_value(comm, "to_agent")
            phase = field_value(comm, "phase")
            trigger = field_value(comm, "trigger")
            message = field_value(comm, "message")
            communication_lines.append(f"- {source} -> {target} [{phase}] when {trigger}: {message}")
        inbox_lines = [
            f"- From {m['from']} to {m['to']}: {m['message']}"
            for m in messages
            if m.get("to") in (active_agent["name"], "ALL")
        ]
        board_lines = [
            f"- {m['from']} -> {m['to']}: {m['message']}"
            for m in messages[-20:]
        ]
        prompt = (
            "You are an autonomous coding agent running inside a strictly scoped "
            "desktop assistant. You are one participant in a multi-agent run. "
            "Act only as the active agent named below. You may only use the JSON tool protocol below. "
            "Do not write prose outside JSON.\n\n"
            "Return exactly one JSON object in this shape:\n"
            "{\n"
            '  "thought": "brief private plan",\n'
            '  "tool_calls": [\n'
            '    {"tool": "list_files", "args": {"limit": 300}},\n'
            '    {"tool": "read_file", "args": {"path": "relative/path.py"}},\n'
            '    {"tool": "patch_file", "args": {"path": "relative/path.py", "old": "exact old text", "new": "replacement text"}},\n'
            '    {"tool": "create_file", "args": {"path": "relative/path.py", "content": "file content"}},\n'
            '    {"tool": "write_file", "args": {"path": "relative/path.py", "content": "file content"}},\n'
            '    {"tool": "delete_file", "args": {"path": "relative/path.py"}},\n'
            '    {"tool": "run_command", "args": {"args": ["python", "-m", "py_compile", "relative/path.py"], "timeout_seconds": 30}},\n'
            '    {"tool": "git_status", "args": {}},\n'
            '    {"tool": "git_diff", "args": {}},\n'
            '    {"tool": "send_message", "args": {"to": "Agent name or ALL", "message": "short message for another agent"}}\n'
            "  ],\n"
            '  "final": null\n'
            "}\n\n"
            "When finished, return JSON with an empty tool_calls list and a final "
            "Markdown report in the final field. Prefer patch_file over write_file. "
            "Only patch exact text you have read. Use verification commands when allowed.\n\n"
            f"Title: {spec.title}\n"
            f"Objective:\n{spec.objective}\n\n"
            f"Required context:\n{spec.required_context or '(none)'}\n\n"
            f"Completion criteria:\n{spec.completion_criteria or '(none)'}\n\n"
            f"Active agent: {active_agent['name']}\n"
            f"Role: {active_agent['role']}\n"
            f"Model preference: {active_agent['model']}\n"
            f"Responsibility:\n{active_agent['responsibility'] or '(none)'}\n\n"
            "Planned agents:\n"
            + ("\n".join(agent_lines) if agent_lines else "- Single agent")
            + "\n\n"
            "Planned agent communications:\n"
            + ("\n".join(communication_lines) if communication_lines else "- (none)")
            + "\n\n"
            "Your inbox:\n"
            + ("\n".join(inbox_lines) if inbox_lines else "- (empty)")
            + "\n\n"
            "Shared message board:\n"
            + ("\n".join(board_lines) if board_lines else "- (empty)")
            + "\n\n"
            f"Scope folder: {spec.scope_folder}\n"
            f"Capabilities: shell={spec.allow_shell}, network={spec.allow_network}, "
            f"git={spec.allow_git}, create={spec.allow_file_create}, "
            f"edit={spec.allow_file_edit}, delete={spec.allow_file_delete}\n\n"
            "Allowed verification commands:\n"
            + (
                "\n".join("- " + " ".join(cmd) for cmd in verify_commands)
                if verify_commands else "- (none)"
            )
            + "\n\n"
            "Visible files:\n" + "\n".join(f"- {f}" for f in files[:200])
        )
        return prompt

    def _call_model(self, prompt: str, log: LogCallback) -> str:
        self._control.raise_if_cancelled()
        if self._model_callback is not None:
            return self._model_callback(prompt)
        try:
            from core import llm

            log("requesting LLM tool response")
            chunks: list[str] = []
            for chunk in llm.stream_response(prompt, use_tools=True):
                chunks.append(chunk)
            return "".join(chunks).strip()
        except Exception as exc:
            log(f"LLM call failed: {exc}")
            return json.dumps({
                "thought": "LLM call failed.",
                "tool_calls": [],
                "final": f"Agent could not contact the model.\n\nError: {exc}",
            })

    def _normalise_agents(self, spec: AgentTaskLike) -> list[dict]:
        raw_agents = getattr(spec, "agents", None) or []

        def field_value(obj, name: str) -> str:  # noqa: ANN001
            if isinstance(obj, dict):
                return str(obj.get(name, "") or "")
            return str(getattr(obj, name, "") or "")

        agents: list[dict] = []
        for idx, agent in enumerate(raw_agents):
            name = field_value(agent, "name").strip() or f"Agent {idx + 1}"
            agents.append({
                "name": name,
                "role": field_value(agent, "role").strip() or "Implementer",
                "model": field_value(agent, "model").strip() or "same as task",
                "responsibility": field_value(agent, "responsibility").strip(),
            })
        if not agents:
            agents.append({
                "name": "Solo",
                "role": "Implementer",
                "model": getattr(spec, "model", "same as task") or "same as task",
                "responsibility": "Complete the task end to end.",
            })
        return agents

    def _seed_communication_rules(self, spec: AgentTaskLike, messages: list[dict], log: LogCallback) -> None:
        """Convert configured start-time communications into real board messages."""
        for comm in getattr(spec, "communications", []) or []:
            source = self._field_value(comm, "from_agent")
            target = self._field_value(comm, "to_agent")
            phase = self._field_value(comm, "phase").lower()
            trigger = self._field_value(comm, "trigger").lower()
            message = self._field_value(comm, "message")
            if not source or not target or not message:
                continue
            if "start" not in phase and "start" not in trigger and phase not in {"", "planning"}:
                continue
            item = {
                "from": source,
                "to": target,
                "message": message,
                "source": "communication_rule",
            }
            messages.append(item)
            log(f"message seeded: {source} -> {target}")

    @staticmethod
    def _field_value(obj, name: str) -> str:  # noqa: ANN001
        if isinstance(obj, dict):
            return str(obj.get(name, "") or "")
        return str(getattr(obj, name, "") or "")

    def _execute_agent_tool_call(
        self,
        tools: AgentToolbox,
        call,
        agent_name: str,
        messages: list[dict],
        turn: dict,
    ) -> ToolResult:
        if not isinstance(call, dict):
            return ToolResult("invalid", False, "Tool call must be an object.")
        tool = str(call.get("tool") or "")
        args = call.get("args") or {}
        if not isinstance(args, dict):
            return ToolResult(tool or "invalid", False, "Tool args must be an object.")
        if tool == "send_message":
            target = str(args.get("to") or "ALL").strip() or "ALL"
            message = str(args.get("message") or "").strip()
            if not message:
                return ToolResult("send_message", False, "Message cannot be empty.")
            item = {
                "from": agent_name,
                "to": target,
                "message": message,
                "source": "tool_call",
            }
            messages.append(item)
            turn["messages"].append(item)
            return ToolResult("send_message", True, f"Message sent to {target}.", item)
        return self._execute_tool_call(tools, call)

    def _repair_agent_response(
        self,
        bad_response: str,
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
    ) -> str | None:
        repair_prompt = (
            "Convert the following text into valid JSON for the agent protocol. "
            "Return only the JSON object. Preserve intended tool calls and final text.\n\n"
            "Bad response:\n"
            + bad_response
        )
        log("requesting JSON repair")
        repaired = self._call_model(repair_prompt, log)
        if verbose:
            verbose("JSON repair response", repaired)
        try:
            self._parse_agent_response(repaired)
        except ValueError:
            return None
        return repaired

    @staticmethod
    def _parse_agent_response(response_text: str) -> dict:
        text = response_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Agent response was not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Agent response JSON must be an object.")
        parsed.setdefault("tool_calls", [])
        parsed.setdefault("final", None)
        return parsed

    def _execute_tool_call(self, tools: AgentToolbox, call) -> ToolResult:  # noqa: ANN001
        if not isinstance(call, dict):
            return ToolResult("invalid", False, "Tool call must be an object.")
        tool = str(call.get("tool") or "")
        args = call.get("args") or {}
        if not isinstance(args, dict):
            return ToolResult(tool or "invalid", False, "Tool args must be an object.")
        try:
            if tool == "list_files":
                return tools.list_files(limit=int(args.get("limit", 300)))
            if tool == "read_file":
                return tools.read_file(str(args["path"]), max_chars=int(args.get("max_chars", 20_000)))
            if tool == "create_file":
                return tools.create_file(str(args["path"]), str(args.get("content", "")))
            if tool == "write_file":
                return tools.write_file(str(args["path"]), str(args.get("content", "")))
            if tool == "patch_file":
                return tools.patch_file(str(args["path"]), str(args["old"]), str(args.get("new", "")))
            if tool == "delete_file":
                return tools.delete_file(str(args["path"]))
            if tool == "run_command":
                command_args = args.get("args")
                if not isinstance(command_args, list):
                    raise ValueError("run_command args.args must be a list.")
                return tools.run_command(
                    [str(part) for part in command_args],
                    timeout_seconds=int(args.get("timeout_seconds", 30)),
                )
            if tool == "git_status":
                return tools.git_status()
            if tool == "git_diff":
                return tools.git_diff()
            return ToolResult(tool or "invalid", False, f"Unknown tool: {tool!r}")
        except Exception as exc:
            return ToolResult(tool or "invalid", False, str(exc))

    def _write_diff_artifacts(
        self,
        run_dir: Path,
        tools: AgentToolbox,
        permissions: AgentPermissions,
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
    ) -> None:
        if not permissions.allow_git:
            return
        status = tools.git_status()
        diff = tools.git_diff()
        self._write_json(run_dir / "git_status.json", asdict(status))
        self._write_json(run_dir / "git_diff.json", asdict(diff))
        if verbose:
            verbose("git status", asdict(status))
            verbose("git diff", asdict(diff))
        if diff.ok and isinstance(diff.data, dict):
            patch = str(diff.data.get("stdout", ""))
            (run_dir / "diff.patch").write_text(patch, encoding="utf-8")
            log("git diff artifact written")

    def _make_run_dir(self, title: str) -> Path:
        safe_title = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title.lower())
        safe_title = "-".join(part for part in safe_title.split("-") if part)[:48] or "task"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.log_root / f"{stamp}-{safe_title}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [truncated {len(text) - max_chars} chars]"

    @staticmethod
    def _spec_dict(spec: AgentTaskLike) -> dict:
        if is_dataclass(spec):
            return asdict(spec)
        return {name: getattr(spec, name) for name in AgentTaskLike.__annotations__}
