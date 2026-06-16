"""Pure helpers for caller context modes and model-tool grants."""
from __future__ import annotations

from typing import Any


def context_mode(caller: dict[str, Any], name: str) -> str:
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
    overrides = caller.get("tools")
    if not isinstance(overrides, dict):
        return {}
    return {
        str(name): str(mode).strip().lower()
        for name, mode in overrides.items()
        if str(mode).strip().lower() in {"on", "model", "off"}
    }


def allowed_model_tools(caller: dict[str, Any]) -> list[str]:
    allowed: list[str] = []
    if context_mode(caller, "documents") == "model":
        allowed.append("get_context.documents")
    if context_mode(caller, "browser") == "model":
        allowed.extend(["web_search", "get_context.browser"])
    if context_mode(caller, "github") == "model":
        allowed.extend(["git_status", "git_diff", "github_repo", "github_issue"])
    memory_mode = context_mode(caller, "memory")
    if memory_mode == "model":
        allowed.append("memory_search")
    if memory_mode in ("auto", "model"):
        allowed.append("memory_save")
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
    pinned: list[str] = []
    if context_mode(caller, "documents") == "model":
        pinned.append("get_context")
    if context_mode(caller, "browser") == "model":
        pinned.extend(["web_search", "get_context"])
    if context_mode(caller, "github") == "model":
        pinned.extend(["git_status", "git_diff", "github_repo", "github_issue"])
    if context_mode(caller, "memory") == "model":
        pinned.append("memory_search")
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
    override = tool_overrides(caller).get("capture_screen")
    if override == "off":
        return False
    if override in {"on", "model"}:
        return True
    return caller.get("context_screenshot") == "model"


def frontloaded_model_tools(caller: dict[str, Any]) -> list[str]:
    frontload: list[str] = []
    if context_mode(caller, "github") == "auto":
        frontload.extend(["git_status", "git_diff"])
    overrides = tool_overrides(caller)
    return [name for name in frontload if overrides.get(name) != "off"]
