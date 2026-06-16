"""
core/conversation_store/store.py — disk persistence for chats and projects.

Two JSON files under ``chats/`` (gitignored, user-writable):

  projects.json       list of {id, name, created_at}
  conversations.json  list of {id, project_id, title, messages, context,
                              created_at, updated_at}

A single built-in "General" project (id == GENERAL_PROJECT_ID) always exists
and cannot be deleted; it is the bucket for plain, non-project chatting.

Writes are atomic (temp file + os.replace) so a crash mid-write never corrupts
the store. All public functions are guarded by a module lock.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone

from core.system.paths import CHATS_DIR, CONVERSATIONS_FILE, PROJECTS_FILE

GENERAL_PROJECT_ID = "general"
_GENERAL_PROJECT_NAME = "General"

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else default
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(path, data) -> None:
    os.makedirs(CHATS_DIR, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _default_general() -> dict:
    return {"id": GENERAL_PROJECT_ID, "name": _GENERAL_PROJECT_NAME, "created_at": _now_iso()}


def _ensure_general(projects: list[dict]) -> list[dict]:
    if not any(p.get("id") == GENERAL_PROJECT_ID for p in projects):
        projects = [_default_general(), *projects]
    return projects


def load_projects() -> list[dict]:
    """Return all projects, guaranteeing the built-in General project is first."""
    with _lock:
        projects = _ensure_general(_read_json(PROJECTS_FILE, []))
        general = [p for p in projects if p.get("id") == GENERAL_PROJECT_ID]
        others = [p for p in projects if p.get("id") != GENERAL_PROJECT_ID]
        return [*general, *others]


def save_projects(projects: list[dict]) -> None:
    with _lock:
        _atomic_write_json(PROJECTS_FILE, _ensure_general(projects))


def add_project(name: str) -> dict:
    """Create and persist a new project; returns the project dict."""
    name = (name or "").strip()
    if not name:
        raise ValueError("project name is required")
    with _lock:
        projects = load_projects()
        existing = next(
            (p for p in projects if p.get("name", "").lower() == name.lower()), None
        )
        if existing is not None:
            return existing
        project = {"id": str(uuid.uuid4()), "name": name, "created_at": _now_iso()}
        projects.append(project)
        save_projects(projects)
        return project


def delete_project(project_id: str) -> bool:
    """Delete a project (never General). Conversations are reassigned to General."""
    if project_id == GENERAL_PROJECT_ID or not project_id:
        return False
    with _lock:
        projects = load_projects()
        kept = [p for p in projects if p.get("id") != project_id]
        if len(kept) == len(projects):
            return False
        save_projects(kept)
        conversations = load_conversations()
        changed = False
        for conv in conversations:
            if conv.get("project_id") == project_id:
                conv["project_id"] = GENERAL_PROJECT_ID
                changed = True
        if changed:
            save_conversations(conversations)
        return True


def project_name(project_id: str) -> str:
    """Human-readable name for a project id (falls back to 'General')."""
    for p in load_projects():
        if p.get("id") == project_id:
            return p.get("name", _GENERAL_PROJECT_NAME)
    return _GENERAL_PROJECT_NAME


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def load_conversations() -> list[dict]:
    """Return persisted conversations (oldest first), each with a project_id."""
    with _lock:
        conversations = _read_json(CONVERSATIONS_FILE, [])
        for conv in conversations:
            conv.setdefault("project_id", GENERAL_PROJECT_ID)
        return conversations


def _has_content(conv: dict) -> bool:
    return any(str(m.get("content") or "").strip() for m in conv.get("messages", []))


def save_conversations(conversations: list[dict]) -> None:
    """Persist conversations that have at least one non-empty message.

    Empty placeholders (a conversation created up front before its reply lands)
    are skipped so they never reach disk.
    """
    with _lock:
        serializable = [_clean_conversation(c) for c in conversations if _has_content(c)]
        _atomic_write_json(CONVERSATIONS_FILE, serializable)


def _clean_conversation(conv: dict) -> dict:
    """Project a conversation dict down to persistable fields."""
    return {
        "id": conv.get("id") or str(uuid.uuid4()),
        "project_id": conv.get("project_id") or GENERAL_PROJECT_ID,
        "title": conv.get("title") or _derive_title(conv),
        "title_override": conv.get("title_override", ""),
        "pinned": bool(conv.get("pinned")),
        "messages": conv.get("messages", []),
        "context": conv.get("context", ""),
        "created_at": conv.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
    }


def _derive_title(conv: dict, limit: int = 48) -> str:
    for msg in conv.get("messages", []):
        if msg.get("role") == "user" and msg.get("content"):
            text = " ".join(str(msg["content"]).split())
            return text[:limit] + ("…" if len(text) > limit else "")
    return "New chat"
