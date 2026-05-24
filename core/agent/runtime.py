"""Shared runtime types for scoped agent execution."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol


LogCallback = Callable[[str], None]
ModelCallback = Callable[[str], str]
ApprovalCallback = Callable[[dict], bool]


class AgentTaskLike(Protocol):
    title: str
    objective: str
    scope_folder: str
    sandbox_mode: str
    approval_policy: str
    provider: str
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
    parallel_read_only_briefing: bool
    full_turn_max_tokens: int
    delta_turn_max_tokens: int
    read_only_max_tokens: int
    agent_temperature: float
    tool_result_text_limit: int
    tool_result_command_limit: int
    tool_result_value_limit: int
    tool_result_list_limit: int
    visible_files_full_limit: int
    visible_files_delta_limit: int
    shell_permission_mode: str
    network_permission_mode: str
    git_permission_mode: str
    file_create_permission_mode: str
    file_edit_permission_mode: str
    file_delete_permission_mode: str


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
        self._pause_after_turn = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._nudge_lock = threading.Lock()
        self._nudges: list[dict[str, str]] = []

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise AgentCancelled("Agent task was cancelled by the user.")

    def pause_after_turn(self) -> None:
        self._pause_after_turn.set()
        self._resume.clear()

    def resume(self) -> None:
        self._pause_after_turn.clear()
        self._resume.set()

    def is_pause_requested(self) -> bool:
        return self._pause_after_turn.is_set()

    def wait_if_paused(self) -> None:
        while self.is_pause_requested() and not self.is_cancelled():
            self._resume.wait(timeout=0.25)
        self.raise_if_cancelled()

    def add_nudge(self, target: str, message: str, source: str = "User") -> None:
        target = target.strip() or "ALL"
        message = message.strip()
        if not message:
            return
        with self._nudge_lock:
            self._nudges.append({
                "from": source.strip() or "User",
                "to": target,
                "message": message,
                "source": "manual_nudge",
            })

    def drain_nudges(self) -> list[dict[str, str]]:
        with self._nudge_lock:
            nudges = list(self._nudges)
            self._nudges.clear()
        return nudges


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
        def enabled(flag_name: str, mode_name: str) -> bool:
            mode = str(getattr(spec, mode_name, "") or "").strip().lower()
            if mode in {"never", "never permit", "deny"}:
                return False
            return bool(getattr(spec, flag_name))

        return cls(
            allow_shell=enabled("allow_shell", "shell_permission_mode"),
            allow_network=enabled("allow_network", "network_permission_mode"),
            allow_git=enabled("allow_git", "git_permission_mode"),
            allow_file_create=enabled("allow_file_create", "file_create_permission_mode"),
            allow_file_edit=enabled("allow_file_edit", "file_edit_permission_mode"),
            allow_file_delete=enabled("allow_file_delete", "file_delete_permission_mode"),
        )


@dataclass(frozen=True)
class ToolResult:
    """Structured result returned by scoped tools."""

    tool: str
    ok: bool
    message: str
    data: dict | list | str | None = None
