"""
chunks.py — the ContextChunk model.

A ContextChunk is one retrievable unit of stored memory/knowledge. Chunks
carry their own extracted terms/phrases/identifiers so the index can be built
once and queries scored fast. Call ``ContextChunk.from_text`` to build a chunk
with automatic extraction.

In production the router is always instantiated with chunks loaded from the
live LTM store (see core/memory_store/store.py). The seed corpus below is kept
for standalone testing and the experiments/context_router CLI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .extract import extract


@dataclass
class ContextChunk:
    id: str
    text: str
    source: str = "memory"          # memory | recent_chat | project_doc | error_log | code_note
    terms: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    created_at: float = 0.0
    last_used_at: float = 0.0
    importance: float = 0.5         # 0..1 author-assigned prior

    @classmethod
    def from_text(
        cls,
        id: str,
        text: str,
        source: str = "memory",
        *,
        age_days: float = 0.0,
        importance: float = 0.5,
    ) -> "ContextChunk":
        ex = extract(text)
        now = time.time()
        ts = now - age_days * 86400
        return cls(
            id=id,
            text=text,
            source=source,
            terms=ex.terms,
            phrases=ex.phrases,
            identifiers=ex.identifiers,
            created_at=ts,
            last_used_at=ts,
            importance=importance,
        )


# (id, text, source, age_days, importance)
_SEED: list[tuple[str, str, str, float, float]] = [
    ("git-origin-main-001",
     "User often gets confused about Git remotes, origin/main, local branches, "
     "and remote tracking branches.", "memory", 12, 0.8),
    ("git-fetch-pull-002",
     "User asked the difference between git fetch and git pull and when a "
     "detached HEAD happens.", "recent_chat", 3, 0.5),
    ("git-branch-switch-003",
     "To move a Linux clone onto the bug fix branch: git fetch origin then "
     "git checkout bugfix or git switch bugfix.", "project_doc", 20, 0.6),
    ("pyside-linux-dll-010",
     "PySide6 works on Windows but fails on Linux with a DLL load failed / "
     "missing libxcb error; install libxcb-cursor0 and set QT_QPA_PLATFORM.",
     "error_log", 2, 0.9),
    ("pyside-migration-011",
     "The overlay UI was migrated from Tkinter to PySide6 for the chat window, "
     "intent overlay, and settings panel.", "project_doc", 30, 0.7),
    ("pyside-qthread-012",
     "Long LLM calls must run off the Qt main thread; the app uses worker "
     "threads guarded by a GenerationCounter to cancel stale queries.",
     "code_note", 8, 0.6),
    ("wisp-arch-020",
     "The Wisp AI assistant overlay captures ambient context (active window, "
     "clipboard, UI automation) and feeds it into the LLM prompt builder.",
     "project_doc", 25, 0.8),
    ("wisp-context-fetcher-021",
     "context_fetcher.py writes a redacted JSON snapshot to a temp file so any "
     "part of the app can read it without re-fetching.", "code_note", 15, 0.6),
    ("wisp-hotkey-022",
     "The overlay is triggered by a global hotkey; the window captured at "
     "hotkey time is cached so the overlay does not shadow the user's document.",
     "code_note", 10, 0.5),
    ("supabase-cors-030",
     "Supabase Edge Functions returned a CORS error; fix by setting the "
     "Access-Control-Allow-Origin header in the function response.",
     "error_log", 5, 0.9),
    ("supabase-env-031",
     "The frontend reads VITE_SUPABASE_URL and the anon key from the .env file; "
     "the project ref is gndhwhhytyoaudfmmavk.", "project_doc", 18, 0.7),
    ("supabase-oauth-032",
     "GitHub OAuth app login through Supabase needs the callback URL added to "
     "the GitHub OAuth app settings.", "memory", 22, 0.5),
    ("aiteam-workflow-040",
     "User runs a multi-agent AI team workflow where a planner agent delegates "
     "to coder and reviewer agents; it sometimes stalls when agents disagree.",
     "memory", 7, 0.8),
    ("aiteam-taskjar-041",
     "Tasks for the AI team are queued in TaskJar; the task_store.py module "
     "persists them between runs.", "code_note", 9, 0.6),
    ("env-winlinux-050",
     "User develops on Windows 11 but deploys to Linux; path separators and "
     "global-hotkey handling differ between platforms.", "memory", 14, 0.7),
    ("env-venv-051",
     "Use python -m unittest to run the test suite inside the project venv.",
     "project_doc", 16, 0.4),
    ("routing-otp-060",
     "An earlier project used OpenTripPlanner for transit routing; bfcache "
     "caused stale results on browser back-navigation.", "memory", 40, 0.4),
    ("clipboard-redact-061",
     "Clipboard text is passed through a redaction filter that removes API "
     "keys, bearer tokens, and password assignments before disk.",
     "code_note", 11, 0.5),
    ("generic-markup-900",
     "Markup language is a system for annotating text so it is syntactically "
     "distinguishable, like HTML or Markdown.", "memory", 60, 0.2),
    ("generic-lifecycle-901",
     "A lifecycle hook is a function that runs at a specific stage of a "
     "component's existence, such as mount or unmount.", "memory", 60, 0.2),
    ("generic-whowhom-902",
     "Who is a subject pronoun; whom is an object pronoun used as the object "
     "of a verb or preposition.", "memory", 90, 0.1),
]


def load_seed_chunks() -> list[ContextChunk]:
    """Build the in-memory seed corpus (for testing and standalone CLI use)."""
    return [
        ContextChunk.from_text(cid, text, source, age_days=age, importance=imp)
        for cid, text, source, age, imp in _SEED
    ]
