"""Shared runtime types for scoped agent execution."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol


LogCallback = Callable[[str], None]
ModelCallback = Callable[[str], str]
ApprovalCallback = Callable[[dict], bool | dict[str, Any]]


class AgentTaskLike(Protocol):
    """Model agent task like."""
    title: str
    objective: str
    scope_folder: str
    sandbox_mode: str
    approval_policy: str
    provider: str
    model: str
    model_fallbacks: str
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
    parallel_execution: bool
    max_parallel_agents: int


class ScopeViolation(ValueError):
    """Raised when an operation tries to escape the configured scope."""


class PermissionDenied(PermissionError):
    """Raised when task permissions do not allow a requested operation."""


class AgentCancelled(RuntimeError):
    """Raised when the user cancels a running agent task."""


class AgentRunControl:
    """Thread-safe cancellation token for a running agent task."""

    def __init__(self):
        """Initialize the agent run control instance."""
        self._cancelled = threading.Event()
        self._pause_after_turn = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._nudge_lock = threading.Lock()
        self._nudges: list[dict[str, str]] = []
        self._permission_lock = threading.Lock()
        self._permission_updates: list[dict[str, str]] = []

    def cancel(self) -> None:
        """Signal the run to cancel by setting the cancelled flag."""
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        """Return whether cancelled is true."""
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        """Handle raise if cancelled for agent run control."""
        if self.is_cancelled():
            raise AgentCancelled("Agent task was cancelled by the user.")

    def pause_after_turn(self) -> None:
        """Handle pause after turn for agent run control."""
        self._pause_after_turn.set()
        self._resume.clear()

    def resume(self) -> None:
        """Handle resume for agent run control."""
        self._pause_after_turn.clear()
        self._resume.set()

    def is_pause_requested(self) -> bool:
        """Return whether pause requested is true."""
        return self._pause_after_turn.is_set()

    def wait_if_paused(self) -> None:
        """Handle wait if paused for agent run control."""
        while self.is_pause_requested() and not self.is_cancelled():
            self._resume.wait(timeout=0.25)
        self.raise_if_cancelled()

    def add_nudge(self, target: str, message: str, source: str = "User") -> None:
        """Add nudge."""
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
        """Handle drain nudges for agent run control."""
        with self._nudge_lock:
            nudges = list(self._nudges)
            self._nudges.clear()
        return nudges

    def update_permission_modes(self, modes: dict[str, str]) -> None:
        """Queue permission-mode changes for the next agent turn."""
        cleaned = {
            str(key): str(value).strip().lower()
            for key, value in (modes or {}).items()
            if str(key).strip()
        }
        if not cleaned:
            return
        with self._permission_lock:
            self._permission_updates.append(cleaned)

    def drain_permission_updates(self) -> list[dict[str, str]]:
        """Return queued permission updates."""
        with self._permission_lock:
            updates = list(self._permission_updates)
            self._permission_updates.clear()
        return updates


class FileLeaseRegistry:
    """Thread-safe exclusive file ownership for concurrently running agents.

    A lease is keyed by a normalized relative path. While an agent holds a
    lease on a path, no other agent may acquire it, so parallel writers can
    only ever touch disjoint files. Acquiring a path you already hold is a
    no-op, and releasing only affects leases the caller owns.
    """

    def __init__(self):
        """Initialize the file lease registry instance."""
        self._lock = threading.Lock()
        self._holders: dict[str, str] = {}

    def acquire(self, agent: str, path: str) -> str | None:
        """Claim ``path`` for ``agent``.

        Returns ``None`` if the lease is now held by ``agent`` (newly acquired
        or already owned), or the name of the blocking holder if another agent
        owns it.
        """
        with self._lock:
            current = self._holders.get(path)
            if current is None or current == agent:
                self._holders[path] = agent
                return None
            return current

    def claim(self, agent: str, paths: list[str]) -> tuple[list[str], dict[str, str]]:
        """Best-effort claim of several paths; returns (granted, denied->holder)."""
        granted: list[str] = []
        denied: dict[str, str] = {}
        with self._lock:
            for path in paths:
                current = self._holders.get(path)
                if current is None or current == agent:
                    self._holders[path] = agent
                    granted.append(path)
                else:
                    denied[path] = current
        return granted, denied

    def release(self, agent: str, paths: list[str] | None = None) -> None:
        """Handle release for file lease registry."""
        with self._lock:
            if paths is None:
                self._holders = {p: h for p, h in self._holders.items() if h != agent}
                return
            for path in paths:
                if self._holders.get(path) == agent:
                    del self._holders[path]

    def release_all(self) -> None:
        """Handle release all for file lease registry."""
        with self._lock:
            self._holders.clear()

    def holder(self, path: str) -> str | None:
        """Handle holder for file lease registry."""
        with self._lock:
            return self._holders.get(path)

    def held_by(self, agent: str) -> list[str]:
        """Handle held by for file lease registry."""
        with self._lock:
            return sorted(path for path, holder in self._holders.items() if holder == agent)


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
        """Handle from spec for agent permissions."""
        def enabled(flag_name: str, mode_name: str) -> bool:
            """Handle enabled for agent permissions."""
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
