"""Message construction helpers shared by LLM provider clients."""
from __future__ import annotations

from typing import Any

import config


def sanitize_history(history: list[dict] | None) -> list[dict[str, str]]:
    """Return prior turns as clean text {role, content} dicts for replay."""
    if not history:
        return []
    clean: list[dict[str, str]] = []
    for msg in history:
        role = str((msg or {}).get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if text:
            clean.append({"role": role, "content": text})
    return clean


def build_openai_messages(
    user_message: str,
    image_base64: str | None,
    ambient_context: str = "",
    memory_context: str = "",
    history: list[dict] | None = None,
) -> list[dict[str, Any]]:
    system = config.get_system_prompt()
    if memory_context:
        system += f"\n\n{memory_context}"
    if ambient_context:
        system += f"\n\n---\n{ambient_context}"
    if image_base64:
        content: str | list[dict[str, Any]] = [
            {"type": "text", "text": user_message},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            },
        ]
    else:
        content = user_message

    return [
        {"role": "system", "content": system},
        *sanitize_history(history),
        {"role": "user", "content": content},
    ]
