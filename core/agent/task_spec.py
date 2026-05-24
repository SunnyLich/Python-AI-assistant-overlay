"""Serializable agent task contracts and run-history helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path


ROLE_RESPONSIBILITIES = {
    "Coordinator": "Break down the task, assign work, merge decisions, arbitrate conflicts, and decide when the group is done.",
    "Planner": "Turn the objective into a concrete plan, identify risks and dependencies, and hand clear next steps to the right agent.",
    "Implementer": "Make the code changes inside the selected scope, keep edits focused, run relevant checks, and report risks or blockers.",
    "Reviewer": "Inspect proposed changes, call out defects or missing tests, request fixes when needed, and approve completion when no Coordinator exists.",
    "Tester": "Design and run focused verification, reproduce failures, report exact commands and results, and confirm fixes.",
    "Researcher": "Gather read-only context before implementation: inspect files, docs, logs, APIs, and constraints, then brief the team with findings and recommendations.",
}


def role_responsibility(role: str) -> str:
    return ROLE_RESPONSIBILITIES.get(role.strip(), "")


def is_role_template(text: str) -> bool:
    normalized = " ".join(text.split())
    return any(" ".join(value.split()) == normalized for value in ROLE_RESPONSIBILITIES.values())


@dataclass(frozen=True)
class AgentRoleSpec:
    """One planned agent participating in the task."""

    name: str
    role: str
    provider: str
    model: str
    responsibility: str


@dataclass(frozen=True)
class AgentCommunicationSpec:
    """Planned exchange between two agents during a multi-agent task."""

    from_agent: str
    to_agent: str
    phase: str
    trigger: str
    message: str


@dataclass(frozen=True)
class AgentTaskSpec:
    """Serializable contract between the tray GUI and the scoped runner."""

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
    shell_permission_mode: str = "auto"
    network_permission_mode: str = "never"
    git_permission_mode: str = "auto"
    file_create_permission_mode: str = "auto"
    file_edit_permission_mode: str = "auto"
    file_delete_permission_mode: str = "never"
    allowed_file_globs: list[str] = field(default_factory=list)
    blocked_file_globs: list[str] = field(default_factory=list)
    required_context: str = ""
    completion_criteria: str = ""
    report_format: str = "Summary + changed files + verification"
    agents: list[AgentRoleSpec] = field(default_factory=list)
    communications: list[AgentCommunicationSpec] = field(default_factory=list)
    parallel_read_only_briefing: bool = True
    full_turn_max_tokens: int = 4096
    delta_turn_max_tokens: int = 3072
    read_only_max_tokens: int = 1536
    agent_temperature: float = 0.0
    tool_result_text_limit: int = 6000
    tool_result_command_limit: int = 8000
    tool_result_value_limit: int = 3000
    tool_result_list_limit: int = 120
    visible_files_full_limit: int = 200
    visible_files_delta_limit: int = 80


def retry_spec_from_run(run_dir: Path) -> AgentTaskSpec:
    """Load the original task spec for a previous run."""
    task_path = run_dir / "task.json"
    if not task_path.exists():
        raise ValueError("Selected run does not have task.json.")
    data = json.loads(task_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Selected run task.json is not an object.")
    return agent_task_spec_from_dict(data)


def continue_spec_from_run(run_dir: Path) -> AgentTaskSpec:
    """Load a previous run as a continuation task with compact prior context."""
    spec = retry_spec_from_run(run_dir)
    context = previous_run_context(run_dir)
    required = spec.required_context.strip()
    combined = (required + "\n\n" if required else "") + context
    return replace(spec, title=f"Continue: {spec.title}", required_context=combined)


def previous_run_context(run_dir: Path) -> str:
    final = read_run_text(run_dir / "final.md")
    error = read_run_text(run_dir / "error.txt")
    run_log = read_run_text(run_dir / "run.log")
    parts = [f"Continuing from previous agent run: {run_dir}"]
    if final:
        parts.append("Previous final report:\n" + compact_for_continue(final, 3000))
    if error:
        parts.append("Previous error:\n" + compact_for_continue(error, 3000))
    if run_log:
        useful_log = filtered_continue_run_log(run_log)
        if useful_log:
            parts.append("Previous useful run events:\n" + compact_for_continue(useful_log, 2200, tail_only=True))
    return "\n\n".join(parts)


def read_run_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def compact_for_continue(text: str, max_chars: int, *, tail_only: bool = False) -> str:
    if len(text) <= max_chars:
        return text
    if tail_only:
        return f"... [earlier content omitted; showing last {max_chars} chars] ...\n" + text[-max_chars:]
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + f"\n... [middle truncated {len(text) - max_chars} chars] ...\n" + text[-tail:]


def filtered_continue_run_log(text: str, *, max_lines: int = 40) -> str:
    noisy_patterns = (
        "model streaming response:",
        "model response still streaming",
        "model call still waiting",
        "model first token after",
        "requesting LLM tool response",
        "requesting JSON repair",
        "JSON repair response received",
        "prompt prepared for",
    )
    useful_markers = (
        "agent turn ",
        "agent read-only turn:",
        " thought:",
        " tool call:",
        "tool ",
        "message:",
        "LLM call failed:",
        "agent response parse failed:",
        "JSON repair response invalid:",
        "using local fallback",
        "returned final response",
        "agent run paused",
        "agent run finished",
        "agent reached turn limit",
        "completion requires ",
        "routing by ",
    )
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(pattern in line for pattern in noisy_patterns):
            continue
        if any(marker in line for marker in useful_markers):
            lines.append(line)
    return "\n".join(lines[-max_lines:])


def resolve_scope_folder(raw_folder: str) -> Path:
    """
    Resolve and validate the folder that an agent may manipulate.

    This is the hard boundary for the runner. Any file operation should be
    checked by the scoped workspace before execution.
    """
    folder = Path(raw_folder).expanduser().resolve()
    if not folder.exists():
        raise ValueError("Scope folder does not exist.")
    if not folder.is_dir():
        raise ValueError("Scope must be a folder, not a file.")
    return folder


def is_inside_scope(path: str | Path, scope_folder: str | Path) -> bool:
    """Return True only when ``path`` resolves inside ``scope_folder``."""
    scope = Path(scope_folder).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    return candidate == scope or scope in candidate.parents


def agent_task_spec_from_dict(data: dict) -> AgentTaskSpec:
    agents = [
        AgentRoleSpec(
            name=str(agent.get("name") or "Agent"),
            role=str(agent.get("role") or "Implementer"),
            provider=str(agent.get("provider") or "same as task"),
            model=str(agent.get("model") or "same as task"),
            responsibility=str(agent.get("responsibility") or ""),
        )
        for agent in data.get("agents", []) or []
        if isinstance(agent, dict)
    ]
    communications = [
        AgentCommunicationSpec(
            from_agent=str(comm.get("from_agent") or ""),
            to_agent=str(comm.get("to_agent") or ""),
            phase=str(comm.get("phase") or ""),
            trigger=str(comm.get("trigger") or ""),
            message=str(comm.get("message") or ""),
        )
        for comm in data.get("communications", []) or []
        if isinstance(comm, dict)
    ]
    return AgentTaskSpec(
        title=str(data.get("title") or ""),
        objective=str(data.get("objective") or ""),
        scope_folder=str(data.get("scope_folder") or Path.cwd()),
        sandbox_mode=str(data.get("sandbox_mode") or "workspace-write: scope folder only"),
        approval_policy=str(data.get("approval_policy") or "ask before escalation"),
        provider=str(data.get("provider") or "same as app"),
        model=str(data.get("model") or ""),
        reasoning_effort=str(data.get("reasoning_effort") or "medium"),
        max_runtime_minutes=int(data.get("max_runtime_minutes") or 60),
        max_turns=int(data.get("max_turns") or 30),
        allow_shell=bool(data.get("allow_shell", True)),
        allow_network=bool(data.get("allow_network", False)),
        allow_git=bool(data.get("allow_git", True)),
        allow_file_create=bool(data.get("allow_file_create", True)),
        allow_file_edit=bool(data.get("allow_file_edit", True)),
        allow_file_delete=bool(data.get("allow_file_delete", False)),
        shell_permission_mode=str(data.get("shell_permission_mode") or ("auto" if data.get("allow_shell", True) else "never permit")),
        network_permission_mode=str(data.get("network_permission_mode") or ("auto" if data.get("allow_network", False) else "never permit")),
        git_permission_mode=str(data.get("git_permission_mode") or ("auto" if data.get("allow_git", True) else "never permit")),
        file_create_permission_mode=str(data.get("file_create_permission_mode") or ("auto" if data.get("allow_file_create", True) else "never permit")),
        file_edit_permission_mode=str(data.get("file_edit_permission_mode") or ("auto" if data.get("allow_file_edit", True) else "never permit")),
        file_delete_permission_mode=str(data.get("file_delete_permission_mode") or ("auto" if data.get("allow_file_delete", False) else "never permit")),
        allowed_file_globs=list(data.get("allowed_file_globs") or []),
        blocked_file_globs=list(data.get("blocked_file_globs") or []),
        required_context=str(data.get("required_context") or ""),
        completion_criteria=str(data.get("completion_criteria") or ""),
        report_format=str(data.get("report_format") or "Summary + changed files + verification"),
        agents=agents,
        communications=communications,
        parallel_read_only_briefing=bool(data.get("parallel_read_only_briefing", True)),
        full_turn_max_tokens=int(data.get("full_turn_max_tokens") or 4096),
        delta_turn_max_tokens=int(data.get("delta_turn_max_tokens") or 3072),
        read_only_max_tokens=int(data.get("read_only_max_tokens") or 1536),
        agent_temperature=float(data.get("agent_temperature", 0.0) or 0.0),
        tool_result_text_limit=int(data.get("tool_result_text_limit") or 6000),
        tool_result_command_limit=int(data.get("tool_result_command_limit") or 8000),
        tool_result_value_limit=int(data.get("tool_result_value_limit") or 3000),
        tool_result_list_limit=int(data.get("tool_result_list_limit") or 120),
        visible_files_full_limit=int(data.get("visible_files_full_limit") or 200),
        visible_files_delta_limit=int(data.get("visible_files_delta_limit") or 80),
    )
