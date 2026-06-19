"""Pure helpers for caller context modes and model-tool grants."""
from __future__ import annotations

from typing import Any

from core.system.env_utils import normalize_file_access_mode
from core.tools.local_files import file_tool_pins_for_access, file_tools_for_access


def context_mode(caller: dict[str, Any], name: str) -> str:
    """Handle context mode for runtime supervisor tool modes."""
    key = f"context_{name}_mode"
    mode = str(caller.get(key) or "").strip().lower()
    if mode in {"off", "auto", "model"}:
        return mode
    if name == "documents":
        if caller.get("context_documents", False):
            return "auto"
        if caller.get("context_tools", False):
            return "model"
    if name in {"browser", "github"} and caller.get("context_tools", False):
        return "model"
    if name == "memory":
        return "auto"
    return "off"


def tool_overrides(caller: dict[str, Any]) -> dict[str, str]:
    """Handle tool overrides for runtime supervisor tool modes."""
    overrides = caller.get("tools")
    if not isinstance(overrides, dict):
        return {}
    return {
        str(name): str(mode).strip().lower()
        for name, mode in overrides.items()
        if str(mode).strip().lower() in {"on", "model", "off"}
    }


def local_file_access_mode(caller: dict[str, Any]) -> str:
    """Return the per-caller local-file access mode."""
    explicit = caller.get("file_access")
    if explicit is not None:
        return normalize_file_access_mode(explicit)
    overrides = tool_overrides(caller)
    enabled = {name for name, mode in overrides.items() if mode in {"on", "model"}}
    if enabled & {"create_file", "edit_file", "write_file"}:
        return "ask"
    if enabled & {"list_files", "read_file"}:
        return "read"
    return "off"


def allowed_model_tools(caller: dict[str, Any]) -> list[str]:
    """Handle allowed model tools for runtime supervisor tool modes."""
    allowed: list[str] = []
    if context_mode(caller, "documents") == "model":
        allowed.append("get_context.documents")
    if context_mode(caller, "browser") == "model":
        allowed.extend(["web_search", "get_context.browser", "retrieve_website"])
    if context_mode(caller, "github") == "model":
        allowed.extend(["git_status", "git_diff", "github_repo", "github_issue"])
    memory_mode = context_mode(caller, "memory")
    if memory_mode == "model":
        allowed.append("memory_search")
    if memory_mode in ("auto", "model"):
        allowed.append("memory_save")
    for name in file_tools_for_access(local_file_access_mode(caller)):
        if name not in allowed:
            allowed.append(name)
    overrides = tool_overrides(caller)
    for name, mode in overrides.items():
        if mode != "off" and name not in allowed:
            allowed.append(name)
    removed = {name for name, mode in overrides.items() if mode == "off"}
    if removed:
        allowed = [
            name
            for name in allowed
            if name not in removed
            and not (name.startswith("get_context.") and "get_context" in removed)
        ]
    return allowed


def pinned_model_tools(caller: dict[str, Any]) -> list[str]:
    """Handle pinned model tools for runtime supervisor tool modes."""
    pinned: list[str] = []
    if context_mode(caller, "documents") == "model":
        pinned.append("get_context")
    if context_mode(caller, "browser") == "model":
        pinned.extend(["web_search", "get_context", "retrieve_website"])
    if context_mode(caller, "github") == "model":
        pinned.extend(["git_status", "git_diff", "github_repo", "github_issue"])
    if context_mode(caller, "memory") == "model":
        pinned.append("memory_search")
    pinned.extend(file_tool_pins_for_access(local_file_access_mode(caller)))
    overrides = tool_overrides(caller)
    pinned.extend(name for name, mode in overrides.items() if mode == "on")
    removed = {name for name, mode in overrides.items() if mode == "off"}
    allowed = set(allowed_model_tools(caller))
    result: list[str] = []
    for name in pinned:
        if name == "get_context":
            if not ({"get_context", "get_context.browser", "get_context.documents"} & allowed):
                continue
        elif name not in allowed:
            continue
        if name in removed:
            continue
        if name == "get_context" and (
            "get_context" in removed
            or (
                "get_context.browser" in removed
                and "get_context.documents" in removed
            )
        ):
            continue
        if name not in result:
            result.append(name)
    return result


def screenshot_tool_allowed(caller: dict[str, Any]) -> bool:
    """Handle screenshot tool allowed for runtime supervisor tool modes."""
    override = tool_overrides(caller).get("capture_screen")
    if override == "off":
        return False
    if override in {"on", "model"}:
        return True
    return caller.get("context_screenshot") == "model"


def frontloaded_model_tools(caller: dict[str, Any]) -> list[str]:
    """Handle frontloaded model tools for runtime supervisor tool modes."""
    frontload: list[str] = []
    if context_mode(caller, "github") == "auto":
        frontload.extend(["git_status", "git_diff"])
    overrides = tool_overrides(caller)
    return [name for name in frontload if overrides.get(name) != "off"]
