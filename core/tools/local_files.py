"""Shared local-file tools for live model calls and agent-style execution."""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Callable

import config
from core.agent.runtime import AgentPermissions, PermissionDenied, ToolResult
from core.agent.toolbox import AgentToolbox
from core.agent.workspace import ScopedWorkspace


LOCAL_FILE_TOOLS = {"list_files", "read_file", "create_file", "edit_file", "write_file"}
READ_FILE_TOOLS = {"list_files", "read_file"}
WRITE_FILE_TOOLS = {"create_file", "edit_file", "write_file"}
FILE_ACCESS_MODES = ("off", "read", "ask", "auto")

ApprovalCallback = Callable[[dict], bool | dict[str, Any]]
FileEventCallback = Callable[[dict], None]


def normalize_file_access_mode(value, default: str = "off") -> str:
    """Normalize per-caller local-file access mode."""
    mode = str(value or default or "off").strip().lower()
    aliases = {
        "none": "off",
        "never": "off",
        "disabled": "off",
        "readonly": "read",
        "read-only": "read",
        "read_only": "read",
        "on": "ask",
        "true": "ask",
        "yes": "ask",
        "model": "ask",
        "write": "ask",
        "always": "auto",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in FILE_ACCESS_MODES else default


def file_tools_for_access(mode: str) -> list[str]:
    """Return model tool names granted by a per-caller file access mode."""
    normalized = normalize_file_access_mode(mode)
    if normalized == "read":
        return sorted(READ_FILE_TOOLS)
    if normalized in {"ask", "auto"}:
        return sorted(LOCAL_FILE_TOOLS)
    return []


def file_tool_pins_for_access(mode: str) -> list[str]:
    """Pin local-file tools when access is enabled from a keybind setting."""
    return file_tools_for_access(mode)


def configured_file_roots() -> list[Path]:
    """Return existing configured local-file roots, resolved and deduplicated."""
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in getattr(config, "TOOL_FILE_ROOTS", []) or []:
        try:
            root = Path(str(raw)).expanduser().resolve()
        except Exception:
            continue
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        roots.append(root)
    return roots


def configured_file_blocked_globs() -> list[str]:
    """Return configured blocked globs for local-file access."""
    return [
        str(g).strip()
        for g in getattr(config, "TOOL_FILE_BLOCKED_GLOBS", []) or []
        if str(g).strip()
    ]


def execute_live_file_tool(
    name: str,
    inputs: dict,
    *,
    access_mode: str,
    approval_callback: ApprovalCallback | None = None,
    event_callback: FileEventCallback | None = None,
) -> str:
    """Execute a local-file model tool with per-caller access controls."""
    mode = normalize_file_access_mode(access_mode)
    if name not in LOCAL_FILE_TOOLS:
        return f"Unknown local file tool: {name}"
    if name in WRITE_FILE_TOOLS and mode not in {"ask", "auto"}:
        return f"Tool {name!r} is disabled because local file writing is off for this caller."
    if name in READ_FILE_TOOLS and mode == "off":
        return f"Tool {name!r} is disabled because local file access is off for this caller."

    try:
        workspace, rel_path = workspace_for_input(
            inputs or {},
            path_key="folder" if name == "list_files" else "path",
            allow_missing=name == "list_files",
        )
        permissions = AgentPermissions(
            allow_file_create=mode in {"ask", "auto"},
            allow_file_edit=mode in {"ask", "auto"},
        )
        mutation_mode = "ask" if mode == "ask" else ("auto" if mode == "auto" else "never")
        toolbox = AgentToolbox(
            workspace,
            permissions,
            approval_callback=approval_callback,
            require_approval=False,
            permission_modes={
                "file_create": mutation_mode,
                "file_edit": mutation_mode,
            },
        )
        result = _dispatch_tool(toolbox, name, inputs or {}, rel_path)
        if event_callback is not None:
            try:
                resolved = workspace.resolve(rel_path or ".")
                event_callback(
                    {
                        "tool": name,
                        "path": str(resolved),
                        "relative_path": str(workspace.relative(rel_path or ".")),
                        "root": str(workspace.root),
                        "ok": bool(result.ok),
                        "message": str(result.message or ""),
                    }
                )
            except Exception:
                pass
        return _format_result(result)
    except Exception as exc:  # noqa: BLE001 - model tools should fail in-band
        return f"Local file tool failed: {exc}"


def workspace_for_input(
    inputs: dict,
    *,
    path_key: str,
    allow_missing: bool = False,
) -> tuple[ScopedWorkspace, str]:
    """Resolve a model-supplied path to the most specific configured root."""
    configured_roots = configured_file_roots()
    if not configured_roots:
        raise ValueError("Local file access is disabled: no TOOL_FILE_ROOTS are configured.")

    raw_path = str((inputs or {}).get(path_key) or "").strip()
    root_hint = str((inputs or {}).get("root") or "").strip()
    if not raw_path:
        if allow_missing:
            raw_path = "."
        else:
            raise ValueError(f"{path_key} is required.")

    target = Path(raw_path).expanduser()
    root: Path | None = None
    relative_path = raw_path
    if target.is_absolute():
        resolved_target = target.resolve()
        matches = [
            candidate
            for candidate in configured_roots
            if resolved_target == candidate or candidate in resolved_target.parents
        ]
        if not matches:
            raise ValueError(f"Path is not under an allowed local-file root: {resolved_target}")
        root = max(matches, key=lambda p: len(p.parts))
        relative_path = str(resolved_target.relative_to(root))
    else:
        if root_hint:
            hinted = Path(root_hint).expanduser().resolve()
            matches = [candidate for candidate in configured_roots if candidate == hinted]
            if not matches:
                raise ValueError("The requested root is not configured for local file access.")
            root = matches[0]
        elif len(configured_roots) == 1:
            root = configured_roots[0]
        else:
            raise ValueError("Relative paths require a root when multiple TOOL_FILE_ROOTS are configured.")

    workspace = ScopedWorkspace(root, blocked_globs=configured_file_blocked_globs())
    workspace.resolve(relative_path)
    return workspace, relative_path


def unified_text_diff(before: str, after: str, filename: str) -> str:
    """Build a compact unified diff for approval UI."""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return "\n".join(diff)[:20_000]


def _dispatch_tool(toolbox: AgentToolbox, name: str, inputs: dict, rel_path: str) -> ToolResult:
    """Map live model tool names onto the shared agent toolbox."""
    if name == "list_files":
        limit = _clamp_int(inputs.get("limit"), default=300, minimum=1, maximum=1000)
        return toolbox.list_files(folder=rel_path or ".", limit=limit)
    if name == "read_file":
        max_chars = _clamp_int(inputs.get("max_chars"), default=20_000, minimum=1, maximum=80_000)
        return toolbox.read_file(rel_path, max_chars=max_chars)
    if name == "edit_file":
        old = str(inputs.get("old") or "")
        new = str(inputs.get("new") or "")
        if not old:
            raise ValueError("edit_file requires a non-empty old string.")
        result = toolbox.patch_file(rel_path, old, new, action_name="edit_file")
        return ToolResult(
            tool="edit_file",
            ok=result.ok,
            message=f"Edited {toolbox.workspace.relative(rel_path)}.",
            data=result.data,
        )
    if name == "create_file":
        return toolbox.create_file(rel_path, str(inputs.get("content") or ""))
    if name == "write_file":
        return toolbox.write_file(rel_path, str(inputs.get("content") or ""))
    raise PermissionDenied(f"Unknown local file tool: {name}")


def _format_result(result: ToolResult) -> str:
    """Render shared tool results as plain text for live model tool loops."""
    if result.tool == "list_files":
        files = result.data if isinstance(result.data, list) else []
        return "\n".join(str(item) for item in files) if files else "No files found."
    if result.tool == "read_file":
        return str(result.data or "")
    return str(result.message or "")


def _clamp_int(value, *, default: int, minimum: int, maximum: int) -> int:
    """Coerce and clamp an integer input."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))
