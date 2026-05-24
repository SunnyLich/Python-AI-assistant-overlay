"""Parsing helpers for explicit user memory commands."""
from __future__ import annotations

import re


# Matches only unambiguous imperative memory commands at the START of a message.
# The (?=[a-zA-Z]) after ^ rejects messages that begin with a quotation mark,
# symbol, or any non-letter character before the trigger word.
_REMEMBER_RE = re.compile(
    r"""^(?=[a-zA-Z])(?:
        (?:please\s+)?remember\s+(?:that\s+|this[:\-\s]+|these[:\-\s]+)
        |(?:please\s+)?remember\s*[:\-]\s*
        |(?:please\s+)?note\s+that\s+
        |(?:please\s+)?note\s*[:\-]\s*
        |save\s+(?:this|that)\s*[:\-\s]+
        |keep\s+in\s+mind\s+(?:that\s+)?
        |keep\s+in\s+mind\s*[:\-]\s*
        |don'?t\s+forget\s+(?:that\s+)?
        |make\s+(?:a\s+)?note\s+(?:that\s+|of\s+)?
        |make\s+(?:a\s+)?note\s*[:\-]\s*
        |store\s+(?:this|that)\s*[:\-\s]+
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def extract_remember_fact(text: str) -> str | None:
    """
    Return the fact text if the message is an imperative memory command.

    This intentionally does not trigger on conversational phrases like
    "I remember X", "do you remember X?", or "can you remember X".
    """
    stripped = text.strip()
    match = _REMEMBER_RE.match(stripped)
    if not match:
        return None
    fact = stripped[match.end():].strip()
    return fact or None
