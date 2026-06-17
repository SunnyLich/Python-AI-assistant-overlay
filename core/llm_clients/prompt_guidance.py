"""Prompt fragments that introduce built-in model capabilities."""
from __future__ import annotations


# Appended to the system prompt only when the screenshot tool is actually
# offered, so the model knows it can see the screen instead of denying it.
SCREENSHOT_TOOL_NOTE = (
    "You also have a capture_screen tool that takes a screenshot of the user's "
    "screen. When answering needs you to visually see what is on screen -- a "
    "website, app UI, image, chart, or error -- call capture_screen instead of "
    "saying you cannot see the screen."
)


# Appended to the system prompt only when general tools are actually attached
# to the request, so the model is never promised access it does not have.
TOOLS_NOTE = (
    "You have live tools available for this query -- use real tool calls when "
    "they would improve the answer. Never print or simulate tool calls in the "
    "reply text."
)


MEMORY_SAVE_NOTE = (
    "You have a memory_save tool. ALWAYS call it when the user explicitly asks "
    "you to remember, note, or save something (e.g. 'remember winter is "
    "coming') -- store exactly what they asked, even if it seems trivial. Also "
    "call it proactively when the user shares a durable fact worth remembering "
    "across sessions: a stable preference, a personal detail, or a fact about "
    "their current project. Scope: by default (omit scope) the fact is filed "
    "under the conversation's current project -- keep that default for anything "
    "specific to what you're working on. Only set scope='general' for universal "
    "facts that should apply across every project, such as a personal "
    "preference about how the user likes answers. Do not store one-off "
    "questions, transient task requests, or secrets, and don't announce that "
    "you saved something unless asked."
)


MEMORY_SEARCH_NOTE = (
    "You have a memory_search tool. Call it before answering when the user asks "
    "about anything that may depend on stored memory, prior saved facts, their "
    "preferences, their projects, or what you remember about them. This includes "
    "questions phrased with hints like 'remember', 'what do you know about', "
    "'what did I tell you', 'my preferences', or project-specific details. Use "
    "a short search query built from the user's request, then answer from the "
    "retrieved facts. If no relevant memory is found, say that plainly instead "
    "of guessing."
)


REWRITE_SYSTEM_PROMPT = (
    "You are a text editor assistant. "
    "Rewrite or fix the provided text. "
    "Output ONLY the corrected/rewritten text -- no explanation, "
    "no markdown, no commentary, no code fences. "
    "Your entire response will be pasted directly as a replacement for the original text."
)


def append_note(system: str, note: str) -> str:
    return f"{system}\n\n{note}" if system else note


def with_tools_note(system: str, tools_offered: bool) -> str:
    if not tools_offered:
        return system
    return append_note(system, TOOLS_NOTE)


def with_screenshot_note(system: str, allow_screenshot_tool: bool) -> str:
    """Append screenshot capability guidance when that tool is exposed."""
    if not allow_screenshot_tool:
        return system
    return append_note(system, SCREENSHOT_TOOL_NOTE)


def with_memory_save_note(system: str, allowed_tools: list[str] | None) -> str:
    """Append memory-save guidance when the memory_save tool is offered."""
    if allowed_tools is not None and "memory_save" in set(allowed_tools):
        return append_note(system, MEMORY_SAVE_NOTE)
    return system


def with_memory_search_note(system: str, allowed_tools: list[str] | None) -> str:
    """Append memory-search guidance when the memory_search tool is offered."""
    if allowed_tools is not None and "memory_search" in set(allowed_tools):
        return append_note(system, MEMORY_SEARCH_NOTE)
    return system


def apply_query_guidance(
    system: str,
    *,
    tools_offered: bool = False,
    allowed_tools: list[str] | None = None,
    allow_screenshot_tool: bool = False,
) -> str:
    """Apply all model-facing query guidance in provider-neutral order."""
    system = with_screenshot_note(system, allow_screenshot_tool)
    system = with_tools_note(system, tools_offered)
    system = with_memory_search_note(system, allowed_tools if tools_offered else None)
    system = with_memory_save_note(system, allowed_tools if tools_offered else None)
    return system
