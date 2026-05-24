"""
core/agent_runner.py - Scoped background agent task runner.

This is the first real execution layer behind the tray "Start agent task"
dialog.  It deliberately starts conservative: validate the hard filesystem
scope, log every step, inventory allowed files, and ask the configured LLM for
an implementation plan.  File mutation is routed through ScopedWorkspace so the
next iteration can add edit tools without weakening the boundary.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import ast
import fnmatch
import base64
import hashlib
import json
import re
import shlex
import subprocess
import threading
import traceback
import time
from typing import Callable, Iterable, Sequence

from core.agent.runtime import (
    AgentCancelled,
    AgentPermissions,
    AgentRunControl,
    AgentTaskLike,
    ApprovalCallback,
    LogCallback,
    ModelCallback,
    PermissionDenied,
    ScopeViolation,
    ToolResult,
)
from core.system.paths import AGENT_RUNS_DIR


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
        permission_modes: dict[str, str] | None = None,
    ):
        self.workspace = workspace
        self.permissions = permissions
        self._log = log
        self._approval_callback = approval_callback
        self._require_approval = require_approval
        self._permission_modes = permission_modes or {}

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
        else:
            self._approve("git", {"args": clean_args})
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
        category = self._permission_category(action)
        mode = str(self._permission_modes.get(category, "") or "").lower()
        if mode in {"never", "never permit", "deny"}:
            raise PermissionDenied(f"{category.replace('_', ' ').title()} permission is set to never permit.")
        if mode in {"auto", "allow", "always"}:
            return
        if not self._require_approval and mode not in {"ask", "ask permission", "ask for permission"}:
            return
        if self._approval_callback is None:
            raise PermissionDenied(f"Approval required for {action}, but no approval UI is available.")
        request = {"action": action, "details": details}
        if not self._approval_callback(request):
            raise PermissionDenied(f"User declined {action}.")

    @staticmethod
    def _permission_category(action: str) -> str:
        if action in {"create_file", "create_file_base64"}:
            return "file_create"
        if action in {"write_file", "write_file_base64", "patch_file"}:
            return "file_edit"
        if action == "delete_file":
            return "file_delete"
        if action == "git":
            return "git"
        if action == "run_command":
            return "shell"
        return action

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
        self.log_root = Path(log_root) if log_root else AGENT_RUNS_DIR
        self._model_callback = model_callback
        self._approval_callback = approval_callback
        self._control = control or AgentRunControl()
        self._write_lock = threading.Lock()

    def start(self, spec: AgentTaskLike, on_log: LogCallback | None = None) -> threading.Thread:
        thread = threading.Thread(target=self.run, args=(spec, on_log), daemon=True)
        thread.start()
        return thread

    def run(
        self,
        spec: AgentTaskLike,
        on_log: LogCallback | None = None,
        on_trace: LogCallback | None = None,
    ) -> Path:
        run_dir = self._make_run_dir(spec.title)
        log_path = run_dir / "run.log"
        verbose_path = run_dir / "verbose.log"
        log_lock = threading.Lock()

        def log(message: str) -> None:
            stamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_lock:
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(stamped + "\n")
            if on_log:
                on_log(stamped)

        def verbose(label: str, payload) -> None:  # noqa: ANN001
            stamped = f"\n[{datetime.now().strftime('%H:%M:%S')}] {label}\n"
            text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
            entry = stamped + self._truncate(text, 60_000) + "\n"
            with log_lock:
                with verbose_path.open("a", encoding="utf-8") as f:
                    f.write(entry)
            if on_trace:
                on_trace(entry)

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
                permission_modes=self._permission_modes_from_spec(spec),
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

            final, turns, messages, agent_states = self._run_agent_loop(spec, tools, files, verify_commands, log, verbose)
            (run_dir / "turns.json").write_text(
                json.dumps(turns, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (run_dir / "messages.json").write_text(
                json.dumps(messages, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (run_dir / "agent_states.json").write_text(
                json.dumps(agent_states, indent=2, ensure_ascii=False),
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
    ) -> tuple[str, list[dict], list[dict], dict[str, dict]]:
        agents = self._normalise_agents(spec)
        messages: list[dict] = []
        self._seed_communication_rules(spec, messages, log)
        turns: list[dict] = []
        agent_states = self._initial_agent_states(agents)
        task_state = self._initial_task_state(files)
        agent_states["_shared_task_state"] = task_state
        max_turns = max(1, int(spec.max_turns))
        agent_by_name = {agent["name"]: agent for agent in agents}
        current_agent = agents[0]
        consecutive_agent = current_agent["name"]
        consecutive_turns = 0
        invalid_json_counts: dict[str, int] = {}
        repeated_failure_streaks: dict[str, tuple[str, int]] = {}
        if len(agents) > 1 and self._model_callback is None and bool(getattr(spec, "parallel_read_only_briefing", True)):
            self._run_parallel_read_only_round(
                spec,
                tools,
                agents,
                files,
                verify_commands,
                messages,
                turns,
                agent_states,
                log,
                verbose,
            )

        for turn_idx in range(max_turns):
            self._control.raise_if_cancelled()
            self._apply_manual_nudges(messages, log)
            if self._control.is_pause_requested():
                log("agent run paused after turn; waiting for resume")
                self._control.wait_if_paused()
                log("agent run resumed")
                self._apply_manual_nudges(messages, log)
            agent = current_agent
            agent_name = agent["name"]
            if agent_name == consecutive_agent:
                consecutive_turns += 1
            else:
                consecutive_agent = agent_name
                consecutive_turns = 1
            log(f"agent turn {turn_idx + 1}/{max_turns}: {agent_name}")
            compact_prompt = bool(agent_states[agent_name].get("briefed"))
            model_input = self._build_agent_prompt(
                spec,
                files,
                verify_commands,
                active_agent=agent,
                messages=messages,
                agent_history=agent_states[agent_name]["history"],
                task_state=task_state,
                compact=compact_prompt,
            )
            tool_context = str(agent_states[agent_name].get("tool_context") or "")
            if tool_context:
                model_input += "\n\nYour previous tool results:\n" + tool_context
            if verbose:
                verbose(f"turn {turn_idx + 1} model input", model_input)
            log(f"prompt prepared for {agent_name}: {len(model_input)} chars ({'delta' if compact_prompt else 'full'})")
            provider, model = self._resolve_agent_route(spec, agent)
            response_text = self._call_model(
                model_input,
                log,
                provider=provider,
                model=model,
                max_tokens=self._spec_int(
                    spec,
                    "delta_turn_max_tokens" if compact_prompt else "full_turn_max_tokens",
                    3072 if compact_prompt else 4096,
                ),
                temperature=self._spec_float(spec, "agent_temperature", 0.0),
            )
            agent_states[agent_name]["briefed"] = True
            file_payload = self._file_payload_summary(response_text)
            if file_payload:
                log(file_payload)
            if verbose:
                verbose(f"turn {turn_idx + 1} model response", response_text)
            turn: dict = {
                "turn": turn_idx + 1,
                "agent": agent_name,
                "model_response": response_text,
                "tool_results": [],
                "messages": [],
                "routing": {},
                "task_state": self._compact_task_state(task_state),
            }
            turns.append(turn)

            try:
                parsed = self._parse_agent_response(response_text)
            except ValueError as exc:
                log(f"agent response parse failed: {exc}")
                invalid_json_counts[agent_name] = invalid_json_counts.get(agent_name, 0) + 1
                if invalid_json_counts[agent_name] >= 2:
                    log(f"model health warning: {agent_name} returned invalid JSON {invalid_json_counts[agent_name]} times; consider a stricter or faster fallback model")
                repaired = self._repair_agent_response(
                    response_text,
                    log,
                    verbose,
                    provider=provider,
                    model=model,
                )
                if repaired is None:
                    return f"Agent stopped because the model returned invalid JSON.\n\n{exc}", turns, messages, agent_states
                response_text = repaired
                turn["model_response_repaired"] = repaired
                try:
                    parsed = self._parse_agent_response(repaired)
                except ValueError as repair_exc:
                    log(f"agent response repair failed: {repair_exc}")
                    return f"Agent stopped because JSON repair failed.\n\n{repair_exc}", turns, messages, agent_states
            if verbose:
                verbose(f"turn {turn_idx + 1} parsed response", parsed)
            thought = str(parsed.get("thought") or "").strip()
            if thought:
                log(f"{agent_name} thought: {thought}")
                self._append_agent_history(agent_states, agent_name, f"Thought: {thought}")
            final = str(parsed.get("final") or "").strip()
            calls = parsed.get("tool_calls") or []
            if final and not calls:
                pending_review_agent = self._pending_review_agent(agent, agents, task_state)
                if pending_review_agent is not None:
                    log(
                        f"completion blocked: review is still pending; "
                        f"{agent_name} routed to {pending_review_agent['name']}"
                    )
                    turn["routing"] = {
                        "status": "review_pending",
                        "next_agent": pending_review_agent["name"],
                        "reason": "A review was requested and has not reported back yet.",
                    }
                    self._ensure_handoff_message(messages, turn, agent, pending_review_agent, {
                        "status": "review_pending",
                        "reason": "Please complete the pending review and report back to Coordinator.",
                    }, [], log)
                    current_agent = pending_review_agent
                    continue
                authority = self._completion_authority_agent(agent, agents)
                if authority is not None:
                    log(
                        f"completion requires {authority['name']}; "
                        f"{agent_name} final response routed for approval"
                    )
                    self._append_agent_history(agent_states, agent_name, "Proposed final: " + final)
                    agent_states[agent_name]["tool_context"] = json.dumps(
                        [{
                            "tool": "proposed_final",
                            "ok": True,
                            "message": f"{agent_name} proposed a final response; completion requires {authority['name']}.",
                            "data": final,
                        }],
                        indent=2,
                        ensure_ascii=False,
                    )
                    turn["routing"] = {
                        "status": "completion_review",
                        "next_agent": authority["name"],
                        "reason": f"Completion authority is {authority['name']}.",
                    }
                    self._ensure_handoff_message(messages, turn, agent, authority, {
                        "status": "completion_review",
                        "reason": f"{agent_name} completed its report and needs {authority['name']} to decide the final task outcome.",
                    }, [], log)
                    if self._is_role(agent, "reviewer"):
                        task_state["review_status"] = "reported"
                        task_state["review_reporter"] = agent_name
                        agent_states["_shared_task_state"] = task_state
                    log(f"next agent: {authority['name']}")
                    current_agent = authority
                    continue
                log(f"{agent_name} returned final response")
                self._append_agent_history(agent_states, agent_name, "Final: " + final)
                return final, turns, messages, agent_states
            if not isinstance(calls, list) or not calls:
                self._update_task_state(task_state, [], parsed, agent_name, log)
                agent_states["_shared_task_state"] = task_state
                next_agent = self._select_next_agent(
                    parsed,
                    agent,
                    agents,
                    consecutive_turns=consecutive_turns,
                    tool_results=[],
                    log=log,
                )
                turn["routing"] = {
                    "status": str(parsed.get("status") or ""),
                    "next_agent": next_agent["name"],
                    "reason": str(parsed.get("reason") or parsed.get("next_agent_reason") or ""),
                }
                self._ensure_handoff_message(messages, turn, agent, next_agent, parsed, [], log)
                if next_agent["name"] != agent_name or parsed.get("next_agent") or parsed.get("status"):
                    if parsed.get("raw_response_excerpt"):
                        tool_context = json.dumps(
                            [{
                                "tool": "response_parser",
                                "ok": False,
                                "message": "Previous response was malformed JSON. Retry with a small valid JSON object and prefer base64 file tools for long content.",
                                "data": str(parsed.get("raw_response_excerpt") or "")[:1200],
                            }],
                            indent=2,
                            ensure_ascii=False,
                        )
                        agent_states[agent_name]["tool_context"] = tool_context
                    log(f"next agent: {next_agent['name']}")
                    current_agent = next_agent
                    continue
                fallback = response_text.strip() or "Agent stopped without tool calls."
                log("agent stopped without tool calls")
                return fallback, turns, messages, agent_states

            results: list[dict] = []
            for call in calls:
                self._control.raise_if_cancelled()
                if isinstance(call, dict):
                    tool_name = self._tool_call_name(call) or "unknown"
                    log(f"{agent_name} tool call: {tool_name}")
                if verbose:
                    verbose(f"turn {turn_idx + 1} tool call", call)
                result = self._execute_agent_tool_call(
                    tools,
                    call,
                    agent_name,
                    messages,
                    turn,
                    log=log,
                    active_agent=agent,
                    spec=spec,
                    task_state=task_state,
                )
                result_dict = asdict(result)
                if verbose:
                    verbose(f"turn {turn_idx + 1} tool result", result_dict)
                results.append(result_dict)
                turn["tool_results"].append(result_dict)
                self._append_agent_history(
                    agent_states,
                    agent_name,
                    f"Tool {result.tool}: {result.message}",
                )
            self._update_task_state(task_state, results, parsed, agent_name, log)
            agent_states["_shared_task_state"] = task_state
            turn["task_state"] = self._compact_task_state(task_state)
            agent_states[agent_name]["tool_context"] = self._tool_results_for_prompt(results, spec)
            repeated_guard_agent = self._repeated_failure_guard(
                agent_name,
                results,
                repeated_failure_streaks,
                agents,
                log,
            )
            if repeated_guard_agent is not None:
                agent_states[agent_name]["tool_context"] = json.dumps(
                    [{
                        "tool": "repeated_failure_guard",
                        "ok": False,
                        "message": (
                            "The same tool failure repeated three times. Stop retrying the same call; "
                            "change approach or ask another agent/coordinator for help."
                        ),
                    }],
                    indent=2,
                    ensure_ascii=False,
                )
                turn["routing"] = {
                    "status": "repeated_failure_guard",
                    "next_agent": repeated_guard_agent["name"],
                    "reason": "Same failed tool action repeated three times.",
                }
                log(f"next agent: {repeated_guard_agent['name']}")
                current_agent = repeated_guard_agent
                continue
            next_agent = self._select_next_agent(
                parsed,
                agent,
                agents,
                consecutive_turns=consecutive_turns,
                tool_results=results,
                log=log,
            )
            turn["routing"] = {
                "status": str(parsed.get("status") or ""),
                "next_agent": next_agent["name"],
                "reason": str(parsed.get("reason") or parsed.get("next_agent_reason") or ""),
            }
            self._ensure_handoff_message(messages, turn, agent, next_agent, parsed, results, log)
            if next_agent["name"] not in agent_by_name:
                next_agent = agents[0]
            log(f"next agent: {next_agent['name']}")
            current_agent = next_agent

        log("agent reached turn limit")
        return "Agent stopped after reaching the configured turn limit.", turns, messages, agent_states

    @staticmethod
    def _ensure_handoff_message(
        messages: list[dict],
        turn: dict,
        active_agent: dict,
        next_agent: dict,
        parsed: dict,
        tool_results: list[dict],
        log: LogCallback,
    ) -> None:
        source = str(active_agent.get("name") or "")
        target = str(next_agent.get("name") or "")
        if not source or not target or source == target:
            return
        for result in tool_results:
            if result.get("tool") != "send_message" or not result.get("ok", True):
                continue
            data = result.get("data")
            if isinstance(data, dict) and str(data.get("to") or "").lower() in {target.lower(), "all"}:
                return
        reason = str(parsed.get("reason") or parsed.get("next_agent_reason") or "Please continue from here.").strip()
        status = str(parsed.get("status") or "continue").strip() or "continue"
        message = f"Handoff ({status}): {reason}"
        item = {
            "from": source,
            "to": target,
            "message": message,
            "source": "auto_handoff",
        }
        if AgentTaskRunner._message_already_present(messages, item):
            return
        messages.append(item)
        turn.setdefault("messages", []).append(item)
        log(f"message: {source} -> {target}: {message[:500]}")

    @staticmethod
    def _repeated_failure_guard(
        agent_name: str,
        results: list[dict],
        streaks: dict[str, tuple[str, int]],
        agents: list[dict],
        log: LogCallback,
    ) -> dict | None:
        failed = next((result for result in results if not result.get("ok", True)), None)
        if failed is None:
            streaks.pop(agent_name, None)
            return None
        signature = f"{failed.get('tool') or 'unknown'}:{failed.get('message') or ''}"
        previous_signature, previous_count = streaks.get(agent_name, ("", 0))
        count = previous_count + 1 if previous_signature == signature else 1
        streaks[agent_name] = (signature, count)
        if count < 3:
            return None
        names = {agent["name"].lower(): agent for agent in agents}
        role_names = {agent["role"].lower(): agent for agent in agents}
        active = names.get(agent_name.lower()) or agents[0]
        target = role_names.get("coordinator")
        if target is None or target.get("name") == agent_name:
            target = AgentTaskRunner._next_round_robin_agent(active, agents)
        log(f"repeated failure guard: {agent_name} repeated {signature!r} {count} times; routing to {target['name']}")
        streaks.pop(agent_name, None)
        return target

    @staticmethod
    def _completion_authority_agent(active_agent: dict, agents: list[dict]) -> dict | None:
        active_name = str(active_agent.get("name") or "")
        active_role = str(active_agent.get("role") or "").lower()
        coordinator = next((agent for agent in agents if str(agent.get("role") or "").lower() == "coordinator"), None)
        if coordinator is not None and coordinator.get("name") != active_name:
            return coordinator
        reviewer = next((agent for agent in agents if str(agent.get("role") or "").lower() == "reviewer"), None)
        if coordinator is None and reviewer is not None and reviewer.get("name") != active_name and active_role != "reviewer":
            return reviewer
        return None

    @staticmethod
    def _is_role(agent: dict | None, role_name: str) -> bool:
        role = str((agent or {}).get("role") or "").lower()
        name = str((agent or {}).get("name") or "").lower()
        expected = role_name.lower()
        return role == expected or name == expected

    @classmethod
    def _pending_review_agent(cls, active_agent: dict, agents: list[dict], task_state: dict | None) -> dict | None:
        if not cls._is_role(active_agent, "coordinator"):
            return None
        if not isinstance(task_state, dict) or task_state.get("review_status") != "pending":
            return None
        return next((agent for agent in agents if cls._is_role(agent, "reviewer")), None)

    def _apply_manual_nudges(self, messages: list[dict], log: LogCallback) -> None:
        for nudge in self._control.drain_nudges():
            if self._message_already_present(messages, nudge):
                continue
            messages.append(dict(nudge))
            text = str(nudge.get("message") or "").replace("\n", " ")[:500]
            source = str(nudge.get("from") or "User")
            target = str(nudge.get("to") or "ALL")
            log(f"message: {source} -> {target}: {text}")
            log(f"manual nudge queued for {target}")

    @staticmethod
    def _message_already_present(messages: list[dict], item: dict) -> bool:
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        message = str(item.get("message") or "")
        return any(
            str(existing.get("from") or "") == source
            and str(existing.get("to") or "") == target
            and str(existing.get("message") or "") == message
            for existing in messages[-12:]
        )

    @staticmethod
    def _initial_agent_states(agents: list[dict]) -> dict[str, dict]:
        return {
            agent["name"]: {
                "history": [],
                "tool_context": "",
                "briefed": False,
            }
            for agent in agents
        }

    @staticmethod
    def _append_agent_history(agent_states: dict[str, dict], agent_name: str, item: str) -> None:
        state = agent_states.setdefault(agent_name, {"history": [], "tool_context": "", "briefed": False})
        history = state.setdefault("history", [])
        history.append(item)
        del history[:-40]

    @staticmethod
    def _file_payload_summary(response_text: str) -> str:
        try:
            parsed = AgentTaskRunner._parse_agent_response(response_text)
        except ValueError:
            repaired = AgentTaskRunner._locally_repair_agent_response(response_text)
            if not repaired:
                return ""
            try:
                parsed = AgentTaskRunner._parse_agent_response(repaired)
            except ValueError:
                return ""
        payloads: list[str] = []
        for call in parsed.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            tool = str(call.get("tool") or "")
            args = AgentTaskRunner._tool_call_args(call)
            if not isinstance(args, dict):
                continue
            path = str(args.get("path") or "?")
            if "content" in args:
                payloads.append(f"{tool}:{path} content={len(str(args.get('content') or ''))} chars")
            if "content_base64" in args:
                payloads.append(f"{tool}:{path} content_base64={len(str(args.get('content_base64') or ''))} chars")
        if not payloads:
            return ""
        return "file payload in JSON response: " + "; ".join(payloads)

    @staticmethod
    def _spec_int(spec: AgentTaskLike, name: str, default: int) -> int:
        try:
            value = int(getattr(spec, name, default) or default)
        except (TypeError, ValueError):
            return default
        return max(1, value)

    @staticmethod
    def _spec_float(spec: AgentTaskLike, name: str, default: float) -> float:
        try:
            value = float(getattr(spec, name, default))
        except (TypeError, ValueError):
            return default
        return max(0.0, value)

    @staticmethod
    def _permission_modes_from_spec(spec: AgentTaskLike) -> dict[str, str]:
        return {
            "shell": str(getattr(spec, "shell_permission_mode", "") or "").strip().lower(),
            "network": str(getattr(spec, "network_permission_mode", "") or "").strip().lower(),
            "git": str(getattr(spec, "git_permission_mode", "") or "").strip().lower(),
            "file_create": str(getattr(spec, "file_create_permission_mode", "") or "").strip().lower(),
            "file_edit": str(getattr(spec, "file_edit_permission_mode", "") or "").strip().lower(),
            "file_delete": str(getattr(spec, "file_delete_permission_mode", "") or "").strip().lower(),
        }

    @staticmethod
    def _tool_results_for_prompt(results: list[dict], spec: AgentTaskLike | None = None) -> str:
        compact_results = []
        for result in results:
            compact = dict(result)
            compact["data"] = AgentTaskRunner._compact_tool_result_data(
                result.get("data"),
                spec,
                tool=str(result.get("tool") or ""),
                message=str(result.get("message") or ""),
            )
            compact_results.append(compact)
        return json.dumps(compact_results, indent=2, ensure_ascii=False)

    @staticmethod
    def _initial_task_state(files: list[str]) -> dict:
        return {
            "relevant_files": [],
            "implementation_status": "unknown",
            "tests": {},
            "known_issues": [],
            "next_step": "inspect task state, inbox, and only then act",
            "git_available": None,
            "git_reason": "",
            "disabled_tools": {},
            "verification_passed": {},
            "visible_file_count": len(files),
        }

    @staticmethod
    def _compact_task_state(task_state: dict | None) -> dict:
        if not isinstance(task_state, dict):
            return {}
        compact = dict(task_state)
        compact["relevant_files"] = list(compact.get("relevant_files") or [])[-20:]
        compact["known_issues"] = list(compact.get("known_issues") or [])[-20:]
        return compact

    @staticmethod
    def _task_state_prompt_text(task_state: dict | None) -> str:
        return json.dumps(AgentTaskRunner._compact_task_state(task_state), indent=2, ensure_ascii=False)

    @staticmethod
    def _add_unique(items: list, value) -> None:  # noqa: ANN001
        if value and value not in items:
            items.append(value)

    @staticmethod
    def _command_from_tool_args(args: dict) -> list[str] | None:
        command_args = args.get("args")
        if command_args is None:
            command_args = args.get("command")
        if command_args is None:
            command_args = args.get("cmd")
        if isinstance(command_args, str):
            try:
                return shlex.split(command_args, posix=False)
            except ValueError:
                return command_args.split()
        if isinstance(command_args, Sequence) and not isinstance(command_args, (str, bytes, bytearray)):
            return [str(part) for part in command_args]
        return None

    @staticmethod
    def _verification_key(command_args: Sequence[str] | None) -> str | None:
        if not command_args:
            return None
        args = [str(part).lower() for part in command_args]
        if args[:3] in (["python", "-m", "pytest"], ["py", "-m", "pytest"]) or args[0] == "pytest":
            return "pytest"
        if args[:3] in (["python", "-m", "unittest"], ["py", "-m", "unittest"]):
            return "unittest"
        if args[:4] in (["python", "-m", "ruff", "check"], ["py", "-m", "ruff", "check"]) or args[:2] == ["ruff", "check"]:
            return "ruff check"
        if args[:3] in (["python", "-m", "mypy"], ["py", "-m", "mypy"]) or args[0] == "mypy":
            return "mypy"
        return " ".join(args)

    @staticmethod
    def _command_tool_name(command_args: Sequence[str] | None) -> str | None:
        if not command_args:
            return None
        args = [str(part).lower() for part in command_args]
        if args[0] == "git":
            return "git"
        if args[0] in {"ruff", "mypy", "pytest"}:
            return args[0]
        if len(args) >= 3 and args[1] == "-m" and args[2] in {"ruff", "mypy", "pytest", "unittest"}:
            return args[2]
        return None

    @staticmethod
    def _command_from_result_message(message: str) -> list[str] | None:
        if ":" in message and message.lower().startswith("exit "):
            message = message.split(":", 1)[1].strip()
        if not message:
            return None
        try:
            return shlex.split(message, posix=False)
        except ValueError:
            return message.split()

    @staticmethod
    def _is_unavailable_message(message: str, data) -> bool:  # noqa: ANN001
        text = message.lower()
        if isinstance(data, dict):
            text += "\n" + str(data.get("stderr") or "").lower()
            text += "\n" + str(data.get("stdout") or "").lower()
        return any(
            needle in text
            for needle in (
                "not a git repository",
                "no such file or directory",
                "not recognized",
                "not found",
                "no module named",
            )
        )

    @classmethod
    def _update_task_state(
        cls,
        task_state: dict,
        results: list[dict],
        parsed: dict,
        agent_name: str,
        log: LogCallback,
    ) -> None:
        thought = str(parsed.get("thought") or "").strip()
        if thought:
            task_state["next_step"] = cls._clip_prompt_line(thought, 220)
        status = str(parsed.get("status") or "").strip()
        if status in {"ready_for_review", "done", "changes_requested", "blocked"}:
            task_state["implementation_status"] = status
        lowered_agent = agent_name.lower()
        if status in {"ready_for_review", "review"} and "reviewer" not in lowered_agent:
            task_state["review_status"] = "pending"
            task_state["review_requested_by"] = agent_name
        if "reviewer" in lowered_agent and status in {"done", "ready_for_review", "reviewed", "changes_requested", "needs_changes"}:
            task_state["review_status"] = "reported"
            task_state["review_reporter"] = agent_name
        for result in results:
            tool = str(result.get("tool") or "")
            message = str(result.get("message") or "")
            data = result.get("data")
            if tool in {"read_file", "create_file", "write_file", "patch_file", "delete_file", "create_file_base64", "write_file_base64"}:
                path = str(message or "")
                if tool == "read_file":
                    path = message
                elif isinstance(data, dict):
                    path = str(data.get("path") or message)
                cls._add_unique(task_state["relevant_files"], path)
            if tool in {"git_status", "git_diff"} or (tool == "run_command" and "git " in message.lower()):
                if "not a git repository" in message.lower() or (
                    isinstance(data, dict)
                    and "not a git repository" in (str(data.get("stderr") or "") + str(data.get("stdout") or "")).lower()
                ):
                    task_state["git_available"] = False
                    task_state["git_reason"] = "not a git repository"
                    task_state["disabled_tools"]["git"] = "not a git repository"
                    cls._add_unique(task_state["known_issues"], "git unavailable: not a git repository")
                    log("shared task state: git marked unavailable (not a git repository)")
            if tool == "run_command":
                command_args = None
                exit_code = None
                if isinstance(data, dict):
                    exit_code = data.get("returncode")
                command_args = cls._command_from_result_message(message)
                command = " ".join(command_args or [])
                key = cls._verification_key(command_args)
                if key:
                    if result.get("ok", False):
                        task_state["tests"][key] = "passed"
                        task_state["verification_passed"][key] = command or key
                        task_state["implementation_status"] = "verification passed"
                    else:
                        task_state["tests"][key] = f"failed ({exit_code if exit_code is not None else 'unknown exit'})"
                        cls._add_unique(task_state["known_issues"], f"{key} failed")
                command_tool = cls._command_tool_name(command_args)
                if command_tool and not result.get("ok", False) and cls._is_unavailable_message(message, data):
                    task_state["disabled_tools"][command_tool] = message or "unavailable"
                    cls._add_unique(task_state["known_issues"], f"{command_tool} unavailable")

    @staticmethod
    def _compact_tool_result_data(
        data,  # noqa: ANN001
        spec: AgentTaskLike | None = None,
        *,
        tool: str = "",
        message: str = "",
    ):
        text_limit = AgentTaskRunner._spec_int(spec, "tool_result_text_limit", 6000) if spec else 6000
        command_limit = AgentTaskRunner._spec_int(spec, "tool_result_command_limit", 8000) if spec else 8000
        value_limit = AgentTaskRunner._spec_int(spec, "tool_result_value_limit", 3000) if spec else 3000
        list_limit = AgentTaskRunner._spec_int(spec, "tool_result_list_limit", 120) if spec else 120
        if tool == "read_file" and isinstance(data, str):
            return AgentTaskRunner._file_reference_for_prompt(message, data, text_limit)
        if isinstance(data, str):
            return AgentTaskRunner._compact_text(data, text_limit)
        if isinstance(data, list):
            if len(data) > list_limit:
                return data[:list_limit] + [f"... truncated {len(data) - list_limit} item(s)"]
            return data
        if isinstance(data, dict):
            compact = {}
            for key, value in data.items():
                if isinstance(value, str):
                    limit = command_limit if key in {"stdout", "stderr"} else value_limit
                    compact[key] = AgentTaskRunner._compact_text(value, limit)
                else:
                    compact[key] = AgentTaskRunner._compact_tool_result_data(value, spec)
            return compact
        return data

    @staticmethod
    def _file_reference_for_prompt(path: str, content: str, excerpt_limit: int) -> dict:
        sha = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        return {
            "file_ref": path or "(unknown)",
            "sha256": sha[:16],
            "chars": len(content),
            "lines": len(content.splitlines()),
            "excerpt": AgentTaskRunner._compact_text(content, excerpt_limit),
            "note": (
                "Compact file reference for prompt speed. Use exact text from excerpt for small patches; "
                "call read_file again with the same path if omitted content is needed."
            ),
        }

    @staticmethod
    def _compact_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head
        return (
            text[:head]
            + f"\n... [middle truncated {len(text) - max_chars} chars for prompt; full data is in run artifacts] ...\n"
            + text[-tail:]
        )

    def _run_parallel_read_only_round(
        self,
        spec: AgentTaskLike,
        tools: AgentToolbox,
        agents: list[dict],
        files: list[str],
        verify_commands: list[list[str]],
        messages: list[dict],
        turns: list[dict],
        agent_states: dict[str, dict],
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
    ) -> None:
        log(f"parallel read-only briefing started: {len(agents)} agents")

        def worker(agent: dict) -> dict:
            self._control.raise_if_cancelled()
            agent_name = agent["name"]
            log(f"agent read-only turn: {agent_name}")
            model_input = self._build_agent_prompt(
                spec,
                files,
                verify_commands,
                active_agent=agent,
                messages=messages,
                agent_history=agent_states[agent_name]["history"],
                read_only_phase=True,
                compact=bool(agent_states[agent_name].get("briefed")),
            )
            if verbose:
                verbose(f"read-only {agent_name} model input", model_input)
            log(f"prompt prepared for {agent_name}: {len(model_input)} chars (read-only full)")
            provider, model = self._resolve_agent_route(spec, agent)
            response_text = self._call_model(
                model_input,
                log,
                provider=provider,
                model=model,
                max_tokens=self._spec_int(spec, "read_only_max_tokens", 1536),
                temperature=self._spec_float(spec, "agent_temperature", 0.0),
            )
            agent_states[agent_name]["briefed"] = True
            file_payload = self._file_payload_summary(response_text)
            if file_payload:
                log(file_payload)
            if verbose:
                verbose(f"read-only {agent_name} model response", response_text)
            turn: dict = {
                "turn": 0,
                "phase": "read_only_briefing",
                "agent": agent_name,
                "model_response": response_text,
                "tool_results": [],
                "messages": [],
                "routing": {},
            }
            try:
                parsed = self._parse_agent_response(response_text)
            except ValueError as exc:
                log(f"{agent_name} read-only response parse failed: {exc}")
                repaired = self._repair_agent_response(response_text, log, verbose, provider=provider, model=model)
                if repaired is None:
                    turn["tool_results"].append(asdict(ToolResult("response_parser", False, str(exc))))
                    return turn
                turn["model_response_repaired"] = repaired
                parsed = self._parse_agent_response(repaired)
            if verbose:
                verbose(f"read-only {agent_name} parsed response", parsed)
            thought = str(parsed.get("thought") or "").strip()
            if thought:
                log(f"{agent_name} thought: {thought}")
                self._append_agent_history(agent_states, agent_name, f"Thought: {thought}")
            results: list[dict] = []
            for call in parsed.get("tool_calls") or []:
                if isinstance(call, dict):
                    log(f"{agent_name} tool call: {self._tool_call_name(call) or 'unknown'}")
                result = self._execute_agent_tool_call(
                    tools,
                    call,
                    agent_name,
                    messages,
                    turn,
                    log=log,
                    read_only=True,
                    active_agent=agent,
                    spec=spec,
                )
                result_dict = asdict(result)
                results.append(result_dict)
                turn["tool_results"].append(result_dict)
                self._append_agent_history(agent_states, agent_name, f"Tool {result.tool}: {result.message}")
            agent_states[agent_name]["tool_context"] = self._tool_results_for_prompt(results, spec)
            return turn

        with ThreadPoolExecutor(max_workers=min(len(agents), 4)) as executor:
            future_map = {executor.submit(worker, agent): agent for agent in agents}
            for future in as_completed(future_map):
                self._control.raise_if_cancelled()
                turns.append(future.result())
        log("parallel read-only briefing finished")

    @staticmethod
    def _allowed_tool_names(
        spec: AgentTaskLike,
        *,
        read_only_phase: bool = False,
        active_agent: dict | None = None,
        task_state: dict | None = None,
    ) -> list[str]:
        role = str((active_agent or {}).get("role") or "").lower()
        name = str((active_agent or {}).get("name") or "").lower()
        is_coordinator = role == "coordinator" or name == "coordinator"
        is_reviewer = role == "reviewer" or name == "reviewer"
        is_implementer = role in {"implementer", "builder", "developer"} or name in {"builder", "developer"}
        tools = ["list_files", "send_message"] if is_coordinator else ["list_files", "read_file", "send_message"]
        git_disabled = isinstance(task_state, dict) and task_state.get("git_available") is False
        if AgentTaskRunner._capability_enabled(spec, "allow_git", "git_permission_mode") and not git_disabled and not is_coordinator:
            tools.extend(["git_status", "git_diff"])
        if read_only_phase:
            return tools
        if is_coordinator:
            return tools
        if is_reviewer:
            if AgentTaskRunner._capability_enabled(spec, "allow_shell", "shell_permission_mode"):
                tools.append("run_command")
            return tools
        if AgentTaskRunner._capability_enabled(spec, "allow_file_edit", "file_edit_permission_mode"):
            tools.append("patch_file")
        if AgentTaskRunner._capability_enabled(spec, "allow_file_create", "file_create_permission_mode"):
            tools.extend(["create_file", "create_file_base64"])
        if AgentTaskRunner._capability_enabled(spec, "allow_file_create", "file_create_permission_mode") or AgentTaskRunner._capability_enabled(spec, "allow_file_edit", "file_edit_permission_mode"):
            tools.extend(["write_file", "write_file_base64"])
        if AgentTaskRunner._capability_enabled(spec, "allow_file_delete", "file_delete_permission_mode"):
            tools.append("delete_file")
        if AgentTaskRunner._capability_enabled(spec, "allow_shell", "shell_permission_mode") and (active_agent is None or is_implementer):
            tools.append("run_command")
        return tools

    @staticmethod
    def _capability_enabled(spec: AgentTaskLike, flag_name: str, mode_name: str) -> bool:
        mode = str(getattr(spec, mode_name, "") or "").strip().lower()
        if mode in {"never", "never permit", "deny"}:
            return False
        return bool(getattr(spec, flag_name, False))

    @staticmethod
    def _tool_reference_text(tool_names: list[str]) -> str:
        references = {
            "list_files": '- list_files args: {"limit": 300}',
            "read_file": '- read_file args: {"path": "ACTUAL_RELATIVE_FILE_PATH_FROM_VISIBLE_FILES"}',
            "patch_file": '- patch_file args: {"path": "ACTUAL_RELATIVE_FILE_PATH_FROM_VISIBLE_FILES", "old": "exact text already read", "new": "replacement text"}',
            "create_file": '- create_file args: {"path": "new_concrete_filename.py", "content": "file content with escaped \\n"}',
            "write_file": '- write_file args: {"path": "ACTUAL_RELATIVE_FILE_PATH_FROM_VISIBLE_FILES_OR_NEW_FILE", "content": "file content with escaped \\n"}',
            "create_file_base64": '- create_file_base64 args: {"path": "new_concrete_filename.py", "content_base64": "short base64 encoded content"}',
            "write_file_base64": '- write_file_base64 args: {"path": "ACTUAL_RELATIVE_FILE_PATH_FROM_VISIBLE_FILES_OR_NEW_FILE", "content_base64": "short base64 encoded content"}',
            "delete_file": '- delete_file args: {"path": "ACTUAL_RELATIVE_FILE_PATH_FROM_VISIBLE_FILES"}',
            "run_command": '- run_command args: {"args": ["python", "-m", "py_compile", "ACTUAL_RELATIVE_FILE_PATH_FROM_VISIBLE_FILES"], "timeout_seconds": 30}',
            "git_status": "- git_status args: {}",
            "git_diff": "- git_diff args: {}",
            "send_message": '- send_message args: {"to": "Agent name or ALL", "message": "short message for another agent"}',
        }
        return "\n".join(references[name] for name in tool_names if name in references)

    @staticmethod
    def _tool_guidance_text(tool_names: list[str]) -> str:
        guidance: list[str] = []
        if "patch_file" in tool_names:
            guidance.append("Prefer patch_file over write_file for edits. Only patch exact text you have read. ")
        if any(name in tool_names for name in ("create_file", "write_file")):
            guidance.append("For text files, use escaped \\n sequences in create_file or write_file content. ")
        if any(name in tool_names for name in ("create_file_base64", "write_file_base64")):
            guidance.append(
                "Use base64 file tools only when the encoded content is short enough to fit comfortably "
                "in one response and will not risk truncation. "
            )
        if any(name in tool_names for name in ("create_file", "write_file", "patch_file", "create_file_base64", "write_file_base64")):
            guidance.append("Use at most one file-writing tool call per turn, then verify in a later turn if needed. ")
        else:
            guidance.append("File-writing tools are not available in this task or phase. ")
        return "".join(guidance)

    def _build_agent_prompt(
        self,
        spec: AgentTaskLike,
        files: list[str],
        verify_commands: list[list[str]] | None = None,
        active_agent: dict | None = None,
        messages: list[dict] | None = None,
        agent_history: list[str] | None = None,
        read_only_phase: bool = False,
        compact: bool = False,
        task_state: dict | None = None,
    ) -> str:
        verify_commands = verify_commands or []
        agents = self._normalise_agents(spec)
        communications = getattr(spec, "communications", []) or []
        active_agent = active_agent or agents[0]
        messages = messages or []
        agent_history = agent_history or []

        def field_value(obj, name: str) -> str:  # noqa: ANN001
            if isinstance(obj, dict):
                return str(obj.get(name, "") or "")
            return str(getattr(obj, name, "") or "")

        agent_lines = []
        for agent in agents:
            name = agent["name"]
            role = agent["role"]
            provider = agent["provider"]
            model = agent["model"]
            responsibility = agent["responsibility"]
            agent_lines.append(f"- {name} ({role}, {provider} / {model}): {responsibility}")
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
        history_lines = [f"- {item}" for item in agent_history[-20:]]
        phase_rules = ""
        if read_only_phase:
            read_only_tools = ", ".join(self._allowed_tool_names(spec, read_only_phase=True, active_agent=active_agent, task_state=task_state))
            phase_rules = (
                "Parallel read-only briefing phase: inspect, reason, and communicate only. "
                f"Allowed tool_calls in this phase are: {read_only_tools}. "
                "Do not request file-writing tools or run_command. "
                "Return status=continue and final=null.\n\n"
            )
        allowed_tools = self._allowed_tool_names(spec, read_only_phase=read_only_phase, active_agent=active_agent, task_state=task_state)
        tool_reference = self._tool_reference_text(allowed_tools)
        tool_guidance = self._tool_guidance_text(allowed_tools)
        shared_state_text = self._task_state_prompt_text(task_state)
        if compact:
            return self._build_agent_delta_prompt(
                spec,
                files,
                verify_commands,
                active_agent,
                inbox_lines,
                board_lines,
                history_lines,
                phase_rules,
                read_only_phase=read_only_phase,
                task_state=task_state,
            )
        prompt = (
            "You are an autonomous coding agent running inside a strictly scoped "
            "desktop assistant. You are one participant in a multi-agent run. "
            "Act only as the active agent named below. You may only use the JSON tool protocol below. "
            "Do not write prose outside JSON.\n\n"
            + phase_rules
            + "Return exactly one JSON object in this shape:\n"
            "{\n"
            '  "thought": "brief private plan",\n'
            '  "status": "continue|blocked|ready_for_review|changes_requested|done|retry",\n'
            '  "next_agent": "agent name to run next, or your own name if you need another turn",\n'
            '  "reason": "brief reason for that routing choice",\n'
            '  "tool_calls": [],\n'
            '  "final": null\n'
            "}\n\n"
            "Available tools for this task and phase:\n"
            + tool_reference
            + "\n\n"
            "For any args.path value, use an actual relative path from Visible files "
            "or a new concrete filename requested by the task. Never copy placeholder "
            "words like path, filename, or relative/path.py.\n\n"
            "For run_command, args.command strings such as \"python -m pytest\" are accepted "
            "and normalized to args arrays by the framework, but prefer args arrays when possible. "
            "Only request commands listed under Allowed verification commands. Never ask another "
            "agent to install packages or missing tools; if a tool is unavailable, record it as skipped.\n\n"
            "When finished, return JSON with an empty tool_calls list and a final "
            "Markdown report in the final field. "
            + tool_guidance
            + "Use verification commands when allowed. "
            "Every string must be valid JSON: escape quotes, backslashes, tabs, and "
            "newlines; never place raw line breaks inside quoted strings. "
            "Do not put large files or long generated content into one JSON response.\n\n"
            "Turn routing: if you still need to work, set next_agent to your own name. "
            "If implementation is ready, set status=ready_for_review and next_agent to Reviewer. "
            "If blocked or coordination is needed, route to Coordinator. "
            "Coordinator must check capabilities and shared task state before assigning work, "
            "and may only assign available actions. "
            "Do not route to Reviewer until there is something concrete to review. "
            "When routing to a different agent, include a send_message tool call to that agent with objective, files, and expected output.\n\n"
            f"Title: {spec.title}\n"
            f"Objective:\n{spec.objective}\n\n"
            f"Required context:\n{spec.required_context or '(none)'}\n\n"
            f"Completion criteria:\n{spec.completion_criteria or '(none)'}\n\n"
            f"Task provider preference: {getattr(spec, 'provider', 'same as app')}\n"
            f"Task model preference: {spec.model}\n\n"
            f"Active agent: {active_agent['name']}\n"
            f"Role: {active_agent['role']}\n"
            f"Provider preference: {active_agent['provider']}\n"
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
            "Your persistent agent history:\n"
            + ("\n".join(history_lines) if history_lines else "- (empty)")
            + "\n\n"
            "Shared task state:\n"
            + shared_state_text
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
            "Visible files:\n" + "\n".join(f"- {f}" for f in files[:self._spec_int(spec, "visible_files_full_limit", 200)])
        )
        return prompt

    @staticmethod
    def _clip_prompt_line(line: str, max_chars: int = 220) -> str:
        if len(line) <= max_chars:
            return line
        return line[: max_chars - 18].rstrip() + " ... [truncated]"

    @classmethod
    def _prompt_lines(cls, lines: list[str], *, limit: int, max_chars: int = 220) -> str:
        selected = lines[-limit:]
        if not selected:
            return "- (empty)"
        return "\n".join(cls._clip_prompt_line(line, max_chars) for line in selected)

    @staticmethod
    def _visible_file_summary(files: list[str], limit: int) -> str:
        filtered = [
            path for path in files
            if "__pycache__/" not in path.replace("\\", "/") and not path.endswith((".pyc", ".pyo"))
        ]
        selected = filtered[:limit]
        if not selected:
            return "- (empty)"
        extra = len(filtered) - len(selected)
        suffix = f"\n- ... {extra} more file(s)" if extra > 0 else ""
        return "\n".join(f"- {path}" for path in selected) + suffix

    def _build_agent_delta_prompt(
        self,
        spec: AgentTaskLike,
        files: list[str],
        verify_commands: list[list[str]],
        active_agent: dict,
        inbox_lines: list[str],
        board_lines: list[str],
        history_lines: list[str],
        phase_rules: str = "",
        read_only_phase: bool = False,
        task_state: dict | None = None,
    ) -> str:
        allowed_tool_names = self._allowed_tool_names(
            spec,
            read_only_phase=read_only_phase,
            active_agent=active_agent,
            task_state=task_state,
        )
        allowed_tools = ", ".join(allowed_tool_names)
        tool_guidance = self._tool_guidance_text(allowed_tool_names)
        agents = self._normalise_agents(spec)
        roster = ", ".join(f"{agent['name']} ({agent['role']})" for agent in agents)
        return (
            "Continue as the active agent. Static task details remain in force.\n\n"
            + phase_rules
            + "Return one valid JSON object with keys thought, status, next_agent, reason, tool_calls, final. "
            f"Tools: {allowed_tools}. "
            "Use real relative paths, never placeholders. "
            + tool_guidance
            + "No large file content. Use only allowed verification commands; do not assign package installation or unavailable tools. "
            "If handing off, send_message to the next agent with objective, files, and expected output.\n\n"
            f"Title: {spec.title}\n"
            f"Active agent: {active_agent['name']} ({active_agent['role']})\n"
            f"Agents: {roster or active_agent['name']}\n"
            f"Responsibility: {self._clip_prompt_line(active_agent['responsibility'] or '(none)', 180)}\n\n"
            "Inbox:\n"
            + self._prompt_lines(inbox_lines, limit=4, max_chars=220)
            + "\n\n"
            "Board:\n"
            + self._prompt_lines(board_lines, limit=5, max_chars=180)
            + "\n\n"
            "Your recent history:\n"
            + self._prompt_lines(history_lines, limit=6, max_chars=220)
            + "\n\n"
            "Shared task state:\n"
            + self._task_state_prompt_text(task_state)
            + "\n\n"
            "Files:\n"
            + self._visible_file_summary(files, self._spec_int(spec, "visible_files_delta_limit", 80))
            + "\n\n"
            "Verify:\n"
            + (
                "\n".join("- " + " ".join(cmd) for cmd in verify_commands)
                if verify_commands else "- (none)"
            )
        )

    def _call_model(
        self,
        prompt: str,
        log: LogCallback,
        *,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = 0.0,
    ) -> str:
        self._control.raise_if_cancelled()
        if self._model_callback is not None:
            started = time.perf_counter()
            response = self._model_callback(prompt)
            log(f"model callback response received in {time.perf_counter() - started:.1f}s ({len(response)} chars)")
            return response
        try:
            from core import llm

            route = f"{provider or 'configured provider'} / {model or 'configured model'}"
            log(f"requesting LLM tool response via {route}")
            started = time.perf_counter()
            heartbeat_done = threading.Event()
            progress_state = {"phase": "waiting", "chars": 0}

            def heartbeat() -> None:
                while not heartbeat_done.wait(5):
                    elapsed = time.perf_counter() - started
                    if progress_state["phase"] == "waiting":
                        log(f"model call still waiting after {elapsed:.0f}s via {route}")
                    else:
                        log(f"model response still streaming after {elapsed:.0f}s ({progress_state['chars']} chars received)")

            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()
            chunks: list[str] = []
            received_chars = 0
            first_chunk = True
            last_progress = started
            next_char_report = 1000
            try:
                for chunk in llm.stream_response(
                    prompt,
                    use_tools=True,
                    route_provider=provider,
                    route_model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    if first_chunk:
                        first_chunk = False
                        progress_state["phase"] = "streaming"
                        log(f"model first token after {time.perf_counter() - started:.1f}s via {route}")
                    chunks.append(chunk)
                    received_chars += len(chunk)
                    progress_state["chars"] = received_chars
                    now = time.perf_counter()
                    if received_chars >= next_char_report or now - last_progress >= 10:
                        log(f"model streaming response: {received_chars} chars received after {now - started:.1f}s")
                        last_progress = now
                        while received_chars >= next_char_report:
                            next_char_report += 1000
            finally:
                heartbeat_done.set()
                heartbeat_thread.join(timeout=0.2)
            response = "".join(chunks).strip()
            log(f"model response received in {time.perf_counter() - started:.1f}s ({len(response)} chars)")
            return response
        except Exception as exc:
            log(f"LLM call failed: {exc}")
            return json.dumps({
                "thought": "LLM call failed. This appears transient, so I will retry instead of treating the task as complete.",
                "status": "retry",
                "next_agent": "same",
                "reason": "The model provider call failed before returning a usable agent response.",
                "tool_calls": [],
                "final": None,
                "model_error": str(exc)[:1000],
            })

    def _resolve_agent_route(self, spec: AgentTaskLike, agent: dict) -> tuple[str | None, str | None]:
        task_provider = str(getattr(spec, "provider", "") or "").strip()
        task_model = str(getattr(spec, "model", "") or "").strip()
        provider = str(agent.get("provider", "") or "").strip()
        model = str(agent.get("model", "") or "").strip()

        if provider.lower() in {"", "same as task"}:
            provider = task_provider
        if provider.lower() in {"", "same as app"}:
            provider = None

        if model.lower() in {"", "same as task"}:
            model = task_model
        if model.lower() in {"", "same as app"}:
            model = None

        return provider, model

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
                "provider": field_value(agent, "provider").strip() or "same as task",
                "model": field_value(agent, "model").strip() or "same as task",
                "responsibility": field_value(agent, "responsibility").strip(),
            })
        if not agents:
            agents.append({
                "name": "Solo",
                "role": "Implementer",
                "provider": getattr(spec, "provider", "same as app") or "same as app",
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

    def _select_next_agent(
        self,
        parsed: dict,
        active_agent: dict,
        agents: list[dict],
        *,
        consecutive_turns: int,
        tool_results: list[dict],
        log: LogCallback,
    ) -> dict:
        names = {agent["name"].lower(): agent for agent in agents}
        role_names = {agent["role"].lower(): agent for agent in agents}
        active_name = active_agent["name"]
        status = str(parsed.get("status") or "").strip().lower()
        requested = str(parsed.get("next_agent") or "").strip()
        coordinator = role_names.get("coordinator") or names.get("coordinator")
        reviewer = role_names.get("reviewer") or names.get("reviewer")

        def by_name_or_role(value: str) -> dict | None:
            key = value.strip().lower()
            if key in {"self", "same", "me", active_agent["name"].lower()}:
                return active_agent
            return names.get(key) or role_names.get(key) or self._agent_alias_match(key, names, role_names)

        requested_agent = by_name_or_role(requested) if requested else None
        requested_is_handoff = requested_agent is not None and requested_agent["name"] != active_name
        selected = requested_agent
        message_recipient = self._latest_direct_message_recipient(tool_results, names, role_names)
        broadcast_recipient = self._latest_broadcast_message_recipient(tool_results, active_agent, agents)
        if requested_is_handoff and message_recipient is not None and message_recipient["name"] != requested_agent["name"]:
            log(
                "routing by explicit next_agent: "
                f"{active_name} -> {requested_agent['name']} "
                f"(latest message went to {message_recipient['name']})"
            )
        if not requested_is_handoff and message_recipient is not None:
            log(f"routing by latest directed message: {active_name} -> {message_recipient['name']}")
            selected = message_recipient
        if not requested_is_handoff and message_recipient is None and broadcast_recipient is not None:
            log(f"routing by broadcast message: {active_name} -> {broadcast_recipient['name']}")
            selected = broadcast_recipient
        if selected is None:
            if self._is_role(active_agent, "reviewer") and coordinator is not None and status in {
                "done",
                "ready_for_review",
                "reviewed",
                "changes_requested",
                "needs_changes",
            }:
                selected = coordinator
            if status in {"continue", "retry"}:
                selected = active_agent
            elif status in {"ready_for_review", "review"}:
                selected = reviewer
            elif status in {"changes_requested", "needs_changes"}:
                selected = coordinator or role_names.get("implementer") or names.get("builder")
            elif status in {"blocked", "coordination_needed"}:
                selected = coordinator

        if selected is None:
            failed_results = [result for result in tool_results if not result.get("ok", True)]
            if failed_results:
                selected = active_agent
            elif any(result.get("tool") in {"create_file", "write_file", "patch_file", "create_file_base64", "write_file_base64"} for result in tool_results):
                selected = active_agent
            else:
                selected = self._next_round_robin_agent(active_agent, agents)

        max_consecutive = 4
        if selected["name"] == active_name and consecutive_turns >= max_consecutive:
            coordinator = role_names.get("coordinator") or names.get("coordinator")
            if coordinator and coordinator["name"] != active_name:
                log(f"routing guard: {active_name} reached {max_consecutive} consecutive turns; sending to {coordinator['name']}")
                return coordinator
            return self._next_round_robin_agent(active_agent, agents)
        return selected

    @staticmethod
    def _agent_alias_match(key: str, names: dict[str, dict], role_names: dict[str, dict]) -> dict | None:
        aliases = {
            "developer": ("implementer", "builder"),
            "dev": ("implementer", "builder"),
            "coder": ("implementer", "builder"),
            "engineer": ("implementer", "builder"),
            "tester": ("reviewer",),
            "qa": ("reviewer",),
        }
        for alias in aliases.get(key, ()):
            agent = role_names.get(alias) or names.get(alias)
            if agent is not None:
                return agent
        return None

    @classmethod
    def _latest_direct_message_recipient(
        cls,
        tool_results: list[dict],
        names: dict[str, dict],
        role_names: dict[str, dict],
    ) -> dict | None:
        for result in reversed(tool_results):
            if result.get("tool") != "send_message" or not result.get("ok", True):
                continue
            data = result.get("data")
            if not isinstance(data, dict):
                continue
            target = str(data.get("to") or "").strip()
            if not target or target.upper() == "ALL":
                continue
            key = target.lower()
            agent = names.get(key) or role_names.get(key) or cls._agent_alias_match(key, names, role_names)
            if agent is not None:
                return agent
        return None

    @staticmethod
    def _latest_broadcast_message_recipient(tool_results: list[dict], active_agent: dict, agents: list[dict]) -> dict | None:
        for result in reversed(tool_results):
            if result.get("tool") != "send_message" or not result.get("ok", True):
                continue
            data = result.get("data")
            if not isinstance(data, dict):
                continue
            if str(data.get("to") or "").strip().upper() != "ALL":
                continue
            return AgentTaskRunner._next_round_robin_agent(active_agent, agents)
        return None

    @staticmethod
    def _next_round_robin_agent(active_agent: dict, agents: list[dict]) -> dict:
        if not agents:
            return active_agent
        for idx, agent in enumerate(agents):
            if agent["name"] == active_agent["name"]:
                return agents[(idx + 1) % len(agents)]
        return agents[0]

    def _execute_agent_tool_call(
        self,
        tools: AgentToolbox,
        call,
        agent_name: str,
        messages: list[dict],
        turn: dict,
        *,
        log: LogCallback | None = None,
        read_only: bool = False,
        active_agent: dict | None = None,
        spec: AgentTaskLike | None = None,
        task_state: dict | None = None,
    ) -> ToolResult:
        if not isinstance(call, dict):
            return ToolResult("invalid", False, "Tool call must be an object.")
        tool = self._tool_call_name(call)
        args = self._tool_call_args(call)
        if not isinstance(args, dict):
            return ToolResult(tool or "invalid", False, "Tool args must be an object.")
        if read_only and not self._is_read_only_agent_tool(tool):
            return ToolResult(tool or "invalid", False, f"Tool is not allowed during read-only briefing: {tool}")
        if active_agent is not None and spec is not None:
            allowed_tools = set(self._allowed_tool_names(spec, read_only_phase=read_only, active_agent=active_agent, task_state=task_state))
            if tool not in allowed_tools:
                role = str(active_agent.get("role") or active_agent.get("name") or "agent")
                return ToolResult(tool or "invalid", False, f"Tool is not allowed for {role}: {tool}")
        if tool == "send_message":
            target = str(args.get("to") or "ALL").strip() or "ALL"
            message = str(args.get("message") or "").strip()
            if not message:
                return ToolResult("send_message", False, "Message cannot be empty.")
            impossible = self._impossible_assignment_error(active_agent, message, spec, task_state)
            if impossible:
                return impossible
            item = {
                "from": agent_name,
                "to": target,
                "message": message,
                "source": "tool_call",
            }
            messages.append(item)
            turn["messages"].append(item)
            log_message = message.replace("\n", " ")[:500]
            if log:
                if target.upper() == "ALL":
                    log(f"message: {agent_name} -> ALL: {log_message}")
                else:
                    log(f"message: {agent_name} -> {target}: {log_message}")
            return ToolResult("send_message", True, f"Message sent to {target}.", item)
        if task_state is not None:
            guard = self._guard_disabled_or_duplicate_tool(tool, args, task_state)
            if guard is not None:
                return guard
        return self._execute_tool_call(tools, call)

    @classmethod
    def _impossible_assignment_error(
        cls,
        active_agent: dict | None,
        message: str,
        spec: AgentTaskLike | None,
        task_state: dict | None,
    ) -> ToolResult | None:
        role = str((active_agent or {}).get("role") or "").lower()
        name = str((active_agent or {}).get("name") or "").lower()
        if role != "coordinator" and name != "coordinator":
            return None
        lowered = message.lower()
        install_patterns = (
            "pip install",
            "npm install",
            "install ruff",
            "install mypy",
            "install pytest",
            "install missing",
            "install package",
            "install packages",
            "install dependencies",
        )
        if any(pattern in lowered for pattern in install_patterns):
            return ToolResult(
                "send_message",
                False,
                "Coordinator cannot assign package installation; installation is not an available task capability.",
                {
                    "error_type": "impossible_assignment",
                    "correction": "Ask the agent to run available verification commands only, and report unavailable tools as skipped.",
                },
            )
        disabled = task_state.get("disabled_tools") if isinstance(task_state, dict) else {}
        if isinstance(disabled, dict):
            for tool_name, reason in disabled.items():
                if str(tool_name).lower() in lowered:
                    return ToolResult(
                        "send_message",
                        False,
                        f"Coordinator cannot assign disabled tool {tool_name}: {reason}",
                        {
                            "error_type": "impossible_assignment",
                            "disabled_tool": tool_name,
                            "correction": "Use shared task state and choose the next available action instead.",
                        },
                    )
        if spec is not None and "git" in lowered and not cls._capability_enabled(spec, "allow_git", "git_permission_mode"):
            return ToolResult(
                "send_message",
                False,
                "Coordinator cannot assign Git work because Git permission is disabled.",
                {"error_type": "impossible_assignment", "correction": "Skip Git checks and report that Git is disabled."},
            )
        return None

    @staticmethod
    def _is_read_only_agent_tool(tool: str) -> bool:
        return tool in {"list_files", "read_file", "git_status", "git_diff", "send_message"}

    @staticmethod
    def _is_mutating_agent_tool(tool: str) -> bool:
        return tool in {
            "create_file",
            "write_file",
            "patch_file",
            "delete_file",
            "create_file_base64",
            "write_file_base64",
        }

    def _guard_disabled_or_duplicate_tool(self, tool: str, args: dict, task_state: dict) -> ToolResult | None:
        disabled_tools = task_state.get("disabled_tools") if isinstance(task_state, dict) else {}
        if not isinstance(disabled_tools, dict):
            disabled_tools = {}
        if tool in {"git_status", "git_diff"} and task_state.get("git_available") is False:
            reason = str(task_state.get("git_reason") or disabled_tools.get("git") or "git unavailable")
            return ToolResult(tool, False, f"Tool permanently disabled: {reason}")
        if tool != "run_command":
            return None
        command_args = self._command_from_tool_args(args)
        command_tool = self._command_tool_name(command_args)
        if command_tool in disabled_tools:
            return ToolResult("run_command", False, f"Tool permanently disabled: {command_tool}: {disabled_tools[command_tool]}")
        key = self._verification_key(command_args)
        passed = task_state.get("verification_passed")
        if key and isinstance(passed, dict) and key in passed:
            return ToolResult(
                "run_command",
                True,
                f"skipped duplicate successful verification: {key} already passed as {passed[key]}",
                {"skipped": True, "equivalent_to": passed[key]},
            )
        return None

    def _repair_agent_response(
        self,
        bad_response: str,
        log: LogCallback,
        verbose: Callable[[str, object], None] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> str | None:
        local_repair = self._locally_repair_agent_response(bad_response)
        if local_repair is not None:
            log("repaired invalid JSON locally")
            return local_repair
        if self._looks_like_truncated_agent_response(bad_response):
            log("invalid JSON appears truncated; using local fallback without model repair")
            fallback = self._fallback_invalid_response(bad_response)
            log("using local fallback for invalid JSON response")
            return fallback
        excerpt = self._repair_response_excerpt(bad_response)
        repair_prompt = (
            "Convert the following text into valid JSON for the agent protocol. "
            "Return only one JSON object with keys thought, status, next_agent, "
            "reason, tool_calls, and final. Do not explain, do not use markdown, "
            "and do not wrap the result in an output field. If the response is "
            "truncated or missing file content, return a retry JSON object with "
            "an empty tool_calls list instead of inventing missing content. "
            "Preserve intended complete tool calls and final text. The bad response "
            "may be excerpted; do not attempt to reconstruct omitted file content.\n\n"
            f"Bad response excerpt ({len(bad_response)} original chars):\n"
            + excerpt
        )
        log(f"requesting JSON repair with {len(excerpt)} char excerpt from {len(bad_response)} char response")
        started = time.perf_counter()
        repaired = self._call_model(repair_prompt, log, provider=provider, model=model, max_tokens=1024)
        log(f"JSON repair response received in {time.perf_counter() - started:.1f}s ({len(repaired)} chars)")
        if verbose:
            verbose("JSON repair response", repaired)
        try:
            self._parse_agent_response(repaired)
        except ValueError as exc:
            log(f"JSON repair response invalid: {exc}")
            fallback = self._fallback_invalid_response(bad_response)
            log("using local fallback for invalid JSON response")
            return fallback
        return repaired

    @staticmethod
    def _repair_response_excerpt(response_text: str, max_chars: int = 3000) -> str:
        return AgentTaskRunner._compact_text(response_text, max_chars)

    @staticmethod
    def _parse_agent_response(response_text: str) -> dict:
        text = AgentTaskRunner._extract_json_text(response_text)
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
            if isinstance(parsed, list):
                parsed = {"thought": "", "tool_calls": parsed, "final": None}
            else:
                raise ValueError("Agent response JSON must be an object.")
        parsed.setdefault("tool_calls", [])
        parsed.setdefault("final", None)
        return parsed

    @staticmethod
    def _locally_repair_agent_response(response_text: str) -> str | None:
        text = AgentTaskRunner._strip_json_fence(AgentTaskRunner._extract_json_text(response_text))
        candidates = [text]
        sanitized = AgentTaskRunner._escape_control_chars_inside_json_strings(text)
        if sanitized != text:
            candidates.append(sanitized)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
                    nested = AgentTaskRunner._locally_repair_agent_response(parsed["output"])
                    if nested is not None:
                        return nested
                if isinstance(parsed, (dict, list)):
                    try:
                        AgentTaskRunner._parse_agent_response(json.dumps(parsed, ensure_ascii=False))
                    except ValueError:
                        continue
                    return json.dumps(parsed, ensure_ascii=False)
            try:
                literal = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                continue
            if isinstance(literal, (dict, list)):
                try:
                    AgentTaskRunner._parse_agent_response(json.dumps(literal, ensure_ascii=False))
                except ValueError:
                    continue
                return json.dumps(literal, ensure_ascii=False)
        return None

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        text = text.strip()
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _escape_control_chars_inside_json_strings(text: str) -> str:
        result: list[str] = []
        in_string = False
        escaped = False
        for char in text:
            if in_string:
                if escaped:
                    result.append(char)
                    escaped = False
                    continue
                if char == "\\":
                    result.append(char)
                    escaped = True
                    continue
                if char == '"':
                    result.append(char)
                    in_string = False
                    continue
                if char == "\n":
                    result.append("\\n")
                    continue
                if char == "\r":
                    result.append("\\r")
                    continue
                if char == "\t":
                    result.append("\\t")
                    continue
            else:
                if char == '"':
                    in_string = True
            result.append(char)
        return "".join(result)

    @staticmethod
    def _looks_like_truncated_agent_response(response_text: str) -> bool:
        text = AgentTaskRunner._strip_json_fence(AgentTaskRunner._extract_json_text(response_text))
        if not text:
            return False
        stripped = text.rstrip()
        if text.count("{") > text.count("}") or text.count("[") > text.count("]"):
            return True
        return any(key in text for key in ('"content_base64"', '"content"')) and not stripped.endswith(("}", "]"))

    @staticmethod
    def _extract_json_text(response_text: str) -> str:
        text = response_text.strip()
        if text.startswith("```"):
            return text
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1).strip()
            if candidate.startswith(("{", "[")):
                return candidate
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            return text[start:end + 1].strip()
        return text

    @staticmethod
    def _tool_call_name(call: dict) -> str:
        name = call.get("tool")
        if name is None:
            name = call.get("function")
        if name is None:
            name = call.get("name")
        if isinstance(name, dict):
            name = name.get("name")
        return str(name or "")

    @staticmethod
    def _tool_call_args(call: dict) -> dict | object:
        args = call.get("args")
        if args is None:
            args = call.get("parameters")
        if args is None and isinstance(call.get("function"), dict):
            args = call["function"].get("arguments")
        if args is None:
            args = {}
        return args

    @staticmethod
    def _fallback_invalid_response(response_text: str) -> str:
        return json.dumps(
            {
                "thought": "The previous model response was malformed JSON and could not be repaired.",
                "status": "retry",
                "next_agent": "same",
                "reason": "Retry the same agent because the prior JSON response was malformed.",
                "tool_calls": [],
                "final": None,
                "raw_response_excerpt": response_text[:4000],
            },
            ensure_ascii=False,
        )

    def _execute_tool_call(self, tools: AgentToolbox, call) -> ToolResult:  # noqa: ANN001
        if not isinstance(call, dict):
            return ToolResult("invalid", False, "Tool call must be an object.")
        tool = self._tool_call_name(call)
        args = self._tool_call_args(call)
        if not isinstance(args, dict):
            return ToolResult(tool or "invalid", False, "Tool args must be an object.")
        if self._is_mutating_agent_tool(tool):
            with self._write_lock:
                return self._execute_tool_call_unlocked(tools, tool, args)
        return self._execute_tool_call_unlocked(tools, tool, args)

    def _execute_tool_call_unlocked(self, tools: AgentToolbox, tool: str, args: dict) -> ToolResult:
        try:
            placeholder_error = self._placeholder_path_error(tools, tool, args)
            if placeholder_error:
                return ToolResult(tool or "invalid", False, placeholder_error)
            if tool == "list_files":
                return tools.list_files(limit=int(args.get("limit", 300)))
            if tool == "read_file":
                return tools.read_file(str(args["path"]), max_chars=int(args.get("max_chars", 20_000)))
            if tool == "create_file":
                return tools.create_file(str(args["path"]), str(args.get("content", "")))
            if tool == "write_file":
                return tools.write_file(str(args["path"]), str(args.get("content", "")))
            if tool == "create_file_base64":
                content = base64.b64decode(str(args.get("content_base64", ""))).decode("utf-8", errors="replace")
                return tools.create_file(str(args["path"]), content)
            if tool == "write_file_base64":
                content = base64.b64decode(str(args.get("content_base64", ""))).decode("utf-8", errors="replace")
                return tools.write_file(str(args["path"]), content)
            if tool == "patch_file":
                return tools.patch_file(str(args["path"]), str(args["old"]), str(args.get("new", "")))
            if tool == "delete_file":
                return tools.delete_file(str(args["path"]))
            if tool == "run_command":
                command_args = self._command_from_tool_args(args)
                if not command_args:
                    return ToolResult(
                        "run_command",
                        False,
                        "schema_error: run_command needs args.args as a list or args.command as a string.",
                        {
                            "error_type": "schema_error",
                            "accepted_shapes": [
                                {"args": ["python", "-m", "pytest"]},
                                {"command": "python -m pytest"},
                            ],
                            "correction": {"args": ["python", "-m", "pytest"]},
                        },
                    )
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

    @staticmethod
    def _placeholder_path_error(tools: AgentToolbox, tool: str, args: dict) -> str | None:
        if tool not in {
            "read_file",
            "create_file",
            "write_file",
            "patch_file",
            "delete_file",
            "create_file_base64",
            "write_file_base64",
        }:
            return None
        path = str(args.get("path") or "").strip()
        if not path:
            return f"{tool} requires args.path with an actual relative file path from list_files."
        normalized = path.replace("\\", "/").strip("'\"")
        placeholders = {
            "path",
            "<path>",
            "file",
            "filename",
            "relative/path.py",
            "/path/to/file",
        }
        if normalized not in placeholders:
            return None
        try:
            exists = tools.workspace.resolve(path).exists()
        except Exception:
            exists = False
        if exists:
            return None
        return (
            f"{tool} received placeholder path {path!r}. Use the actual relative filename from list_files "
            "or the task context, for example 'snake_game.py', and do not reuse the placeholder."
        )

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
        return {name: getattr(spec, name, None) for name in AgentTaskLike.__annotations__}

