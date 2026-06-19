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
import base64
import hashlib
import mimetypes
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.system.paths import CHATS_DIR, CHAT_ATTACHMENTS_DIR, CONVERSATIONS_FILE, PROJECTS_FILE

GENERAL_PROJECT_ID = "general"
_GENERAL_PROJECT_NAME = "General"

_lock = threading.RLock()
_IMAGE_EXT_BY_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
    (b"RIFF", ".webp", "image/webp"),
    (b"BM", ".bmp", "image/bmp"),
)


def _now_iso() -> str:
    """Handle now iso for conversation store store."""
    return datetime.now(timezone.utc).isoformat()


def _read_json(path, default):
    """Read json."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else default
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(path, data) -> None:
    """Handle atomic write json for conversation store store."""
    os.makedirs(CHATS_DIR, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def _safe_segment(value: object, default: str = "item") -> str:
    """Return a filesystem-safe path segment."""
    text = str(value or "").strip() or default
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return text[:80] or default


def _strip_data_url(raw: str) -> tuple[str, str]:
    """Return (base64_text, mime_hint) for plain base64 or data URLs."""
    text = (raw or "").strip()
    if text.startswith("data:") and "," in text:
        header, payload = text.split(",", 1)
        mime = header[5:].split(";", 1)[0].strip().lower()
        return payload.strip(), mime
    return text, ""


def _image_suffix_and_mime(data: bytes, mime_hint: str = "", name: str = "") -> tuple[str, str]:
    """Infer a stable image suffix and MIME type."""
    lowered_mime = (mime_hint or "").lower()
    if lowered_mime.startswith("image/"):
        guessed = mimetypes.guess_extension(lowered_mime) or ""
        if guessed == ".jpe":
            guessed = ".jpg"
        if guessed:
            return guessed, lowered_mime
    for magic, suffix, mime in _IMAGE_EXT_BY_MAGIC:
        if data.startswith(magic):
            if suffix == ".webp" and data[8:12] != b"WEBP":
                continue
            return suffix, mime
    suffix = Path(name or "").suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}:
        return suffix, mimetypes.guess_type("x" + suffix)[0] or "application/octet-stream"
    return ".png", "image/png"


def _attachment_relpath(conversation_id: str, message_id: str, filename: str) -> str:
    """Return the JSON-stored attachment path relative to CHATS_DIR."""
    return str(
        Path("attachments")
        / _safe_segment(conversation_id, "conversation")
        / _safe_segment(message_id, "message")
        / filename
    ).replace("\\", "/")


def _managed_attachment_abs_path(rel_path: str) -> Path:
    """Resolve a managed attachment path below CHATS_DIR."""
    rel = Path(str(rel_path or "").replace("\\", "/"))
    if rel.parts and rel.parts[0] == "attachments":
        return CHAT_ATTACHMENTS_DIR.joinpath(*rel.parts[1:])
    return CHATS_DIR / rel


def save_image_attachment(
    image_base64: str,
    *,
    conversation_id: str,
    message_id: str,
    source: str = "screenshot",
    name: str = "image",
) -> dict:
    """Persist an image blob outside conversations.json and return a reference."""
    payload, mime_hint = _strip_data_url(image_base64)
    data = base64.b64decode(payload, validate=False)
    digest = hashlib.sha256(data).hexdigest()
    suffix, mime = _image_suffix_and_mime(data, mime_hint, name)
    stem = _safe_segment(Path(name or "image").stem, "image")
    filename = f"{stem}-{digest[:12]}{suffix}"
    rel_path = _attachment_relpath(conversation_id, message_id, filename)
    abs_path = _managed_attachment_abs_path(rel_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    if not abs_path.exists():
        abs_path.write_bytes(data)
    return {
        "id": f"att_{digest[:16]}",
        "kind": "image",
        "source": source or "screenshot",
        "path": rel_path,
        "name": Path(name or filename).name,
        "mime": mime,
        "sha256": digest,
        "size": len(data),
        "created_at": _now_iso(),
    }


def external_file_attachment(path: str, *, kind: str | None = None, source: str = "external_path") -> dict:
    """Return an attachment reference to a user-owned file path."""
    p = Path(str(path or "")).expanduser()
    mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    inferred_kind = kind or ("image" if mime.startswith("image/") else "file")
    stat = None
    try:
        stat = p.stat()
    except OSError:
        pass
    ref = {
        "id": f"att_{uuid.uuid4().hex[:16]}",
        "kind": inferred_kind,
        "source": source,
        "path": str(p),
        "name": p.name or str(p),
        "mime": mime,
    }
    if stat is not None:
        ref["size"] = int(stat.st_size)
        ref["mtime"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    return ref


def normalize_attachments(raw_attachments: object) -> list[dict]:
    """Return JSON-safe attachment references; drop inline/base64 payloads."""
    if not isinstance(raw_attachments, list):
        return []
    out: list[dict] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            continue
        kind = str(raw.get("kind") or "file").strip().lower()
        if kind not in {"image", "file", "text"}:
            kind = "file"
        item = {
            "id": str(raw.get("id") or f"att_{uuid.uuid4().hex[:16]}"),
            "kind": kind,
            "source": str(raw.get("source") or "external_path"),
            "path": path,
            "name": str(raw.get("name") or Path(path).name or "attachment"),
            "mime": str(raw.get("mime") or mimetypes.guess_type(path)[0] or "application/octet-stream"),
        }
        for key in ("sha256", "created_at", "mtime"):
            value = raw.get(key)
            if value:
                item[key] = str(value)
        for key in ("size",):
            value = raw.get(key)
            if isinstance(value, int):
                item[key] = value
        out.append(item)
    return out


def attachment_path(ref: dict) -> Path:
    """Resolve an attachment reference to a local filesystem path."""
    path = str((ref or {}).get("path") or "")
    source = str((ref or {}).get("source") or "")
    if source == "external_path" or Path(path).is_absolute():
        return Path(path).expanduser()
    return _managed_attachment_abs_path(path)


def attachment_image_base64(ref: dict) -> str:
    """Read one image attachment as base64 for a model call or thumbnail."""
    if not isinstance(ref, dict) or str(ref.get("kind") or "") != "image":
        return ""
    try:
        data = attachment_path(ref).read_bytes()
    except OSError:
        return ""
    return base64.b64encode(data).decode("ascii")


def first_image_base64_from_message(message: dict) -> str:
    """Return the first image for a message, supporting old in-memory blobs."""
    legacy = message.get("image_base64") if isinstance(message, dict) else None
    if legacy:
        return str(legacy)
    for ref in normalize_attachments(message.get("attachments") if isinstance(message, dict) else []):
        image = attachment_image_base64(ref)
        if image:
            return image
    return ""


def attachment_context_text(ref: dict, *, max_chars: int = 50_000) -> str:
    """Return model-visible context for an attachment reference."""
    if not isinstance(ref, dict):
        return ""
    name = str(ref.get("name") or ref.get("path") or "attachment")
    kind = str(ref.get("kind") or "file")
    path = attachment_path(ref)
    if kind == "image":
        if path.exists():
            return f"[Attached image: {name}]\nPath: {path}"
        return f"[Attached image missing: {name}]\nPath: {path}"
    if not path.exists():
        return f"[Attached file missing: {name}]\nPath: {path}"
    try:
        from core.llm_clients.client import read_document_file

        content = read_document_file(str(path))
    except Exception:
        content = ""
    if not content:
        return f"[Attached file: {name}]\nPath: {path}"
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + "\n[attached file context truncated]"
    return f"[Attached file: {name}]\nPath: {path}\n\n{content}"


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _default_general() -> dict:
    """Handle default general for conversation store store."""
    return {"id": GENERAL_PROJECT_ID, "name": _GENERAL_PROJECT_NAME, "created_at": _now_iso()}


def _ensure_general(projects: list[dict]) -> list[dict]:
    """Ensure general."""
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
    """Save projects."""
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
        changed = False
        for conv in conversations:
            conv.setdefault("project_id", GENERAL_PROJECT_ID)
            for msg in conv.get("messages", []) or []:
                if isinstance(msg, dict):
                    if "image_base64" in msg:
                        msg.pop("image_base64", None)
                        changed = True
                    raw_attachments = msg.get("attachments")
                    attachments = normalize_attachments(raw_attachments)
                    if attachments:
                        if raw_attachments != attachments:
                            changed = True
                        msg["attachments"] = attachments
                    else:
                        if "attachments" in msg:
                            msg.pop("attachments", None)
                            changed = True
        if changed:
            _atomic_write_json(CONVERSATIONS_FILE, conversations)
        return conversations


def _has_content(conv: dict) -> bool:
    """Return whether content is available."""
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
    created_at = conv.get("created_at") or _now_iso()
    return {
        "id": conv.get("id") or str(uuid.uuid4()),
        "project_id": conv.get("project_id") or GENERAL_PROJECT_ID,
        "title": conv.get("title") or _derive_title(conv),
        "title_override": conv.get("title_override", ""),
        "pinned": bool(conv.get("pinned")),
        "messages": _clean_messages(conv.get("messages", []), created_at),
        "context": conv.get("context", ""),
        "file_context": conv.get("file_context", []),
        "tool_context": conv.get("tool_context", {}),
        "context_policy": conv.get("context_policy", {}),
        "created_at": created_at,
        "updated_at": _now_iso(),
    }


def _clean_messages(messages: list, fallback_created_at: str) -> list[dict]:
    """Ensure persisted chat turns carry display metadata."""
    cleaned: list[dict] = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.setdefault("id", str(uuid.uuid4()))
        item.setdefault("created_at", fallback_created_at)
        item.pop("image_base64", None)
        attachments = normalize_attachments(item.get("attachments"))
        if attachments:
            item["attachments"] = attachments
        else:
            item.pop("attachments", None)
        cleaned.append(item)
    return cleaned


def _derive_title(conv: dict, limit: int = 48) -> str:
    """Handle derive title for conversation store store."""
    for msg in conv.get("messages", []):
        if msg.get("role") == "user" and msg.get("content"):
            text = " ".join(str(msg["content"]).split())
            return text[:limit] + ("…" if len(text) > limit else "")
    return "New chat"
