"""
ui/chat_window.py -” Multi-turn chat window with conversation history sidebar.

Left sidebar lists all past conversations; clicking one selects it so you can
continue that thread.

Send message: Enter (Shift+Enter for newline).
"""
from __future__ import annotations

import html
import inspect
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from PySide6.QtCore import QMimeData, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from core.assistant_text import ThoughtStreamParser, merge_segment_iterables
from core.conversation_store import store as _conversation_store
from core.conversation_store.store import GENERAL_PROJECT_ID as _GENERAL_PROJECT_ID
from runtime.supervisor import tool_modes
from ui.chat_rendering import _assistant_segments_to_html, _assistant_text_to_html
from ui.i18n import t
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

_W          = 840
_H          = 640
_BG         = "#1c1c24"
_SIDEBAR_BG = "#13131a"
_TITLE_BG   = "#16161f"
_USER_BG    = "#3a3a5c"
_AI_BG      = "#26263a"
_BORDER     = "rgba(255,255,255,20)"
_TEXT       = "#e6e6e6"
_HINT       = "#888888"
_ACCENT     = "#a0a0ff"
_SEL_BG     = "rgba(160,160,255,18)"
_REVERT_DELAY_MS = 3000   # how long bold words stay highlighted after TTS finishes
_CHAT_RENDER_CHAR_LIMIT = 24_000
_CONTEXT_TOOLTIP_CHAR_LIMIT = 4_000
_ATTACHMENT_CONTEXT_CHAR_LIMIT = 40_000
_SIDEBAR_MENU_W = 32
_SIDEBAR_FADE_W = 34


def _estimate_context_tokens(text: str) -> int:
    """Fast token estimate matching the intent overlay preview."""
    cjk = 0
    for ch in text or "":
        code = ord(ch)
        if (
            0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
            or 0xFF00 <= code <= 0xFFEF
        ):
            cjk += 1
    return max(0, round(cjk * 0.85 + (len(text or "") - cjk) / 4))


def _token_label(text: str) -> str:
    tokens = _estimate_context_tokens(text)
    if tokens <= 0:
        return "0 tok"
    if tokens >= 1000:
        return f"~{tokens / 1000:.1f}k tok"
    return f"~{tokens} tok"


def _deferred_token_label() -> str:
    return t("on send")


def _now_iso() -> str:
    """Return current UTC time for conversation metadata."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse stored ISO timestamps and normalize them to local time."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone()


def _format_conversation_datetime(value: str | None) -> str:
    """Format a conversation timestamp for display only."""
    dt = _parse_iso_datetime(value)
    if dt is None:
        return ""
    hour = dt.strftime("%I").lstrip("0") or "0"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} {hour}:{dt.strftime('%M %p')}"


def _message_timestamp_text(msg: dict, fallback: str | None = None) -> str:
    """Return display-only timestamp for one chat turn."""
    return _format_conversation_datetime(msg.get("created_at") or msg.get("updated_at") or fallback)


def _touch_conversation(conv: dict, *, now: str | None = None) -> str:
    """Ensure created_at exists and update updated_at."""
    stamp = now or _now_iso()
    conv.setdefault("created_at", stamp)
    conv["updated_at"] = stamp
    return stamp


def _ensure_message_metadata(msg: dict, *, fallback_created_at: str | None = None) -> dict:
    """Ensure one persisted chat turn has stable display and action metadata."""
    if not isinstance(msg, dict):
        return msg
    msg.setdefault("id", str(uuid.uuid4()))
    msg.setdefault("created_at", fallback_created_at or _now_iso())
    return msg


def _ensure_conversation_metadata(conv: dict) -> None:
    """Backfill stable IDs/timestamps for older in-memory conversations."""
    stamp = conv.get("created_at") or conv.get("updated_at") or _now_iso()
    conv.setdefault("created_at", stamp)
    conv.setdefault("updated_at", stamp)
    for msg in conv.get("messages", []) or []:
        if isinstance(msg, dict):
            _ensure_message_metadata(msg, fallback_created_at=stamp)


def _message_context_text(raw: object) -> str:
    """Normalize one message-scoped hidden context value."""
    if isinstance(raw, list):
        return "\n\n---\n".join(
            str(item or "").strip()
            for item in raw
            if str(item or "").strip()
        )
    return str(raw or "").strip()


def _attachment_summary_context(attachments: object) -> str:
    """Return a compact, persisted reference summary for message attachments."""
    refs = _conversation_store.normalize_attachments(attachments)
    if not refs:
        return ""
    lines = ["[Attached files]"]
    for ref in refs:
        name = str(ref.get("name") or "Attachment")
        path = str(ref.get("path") or "")
        source = str(ref.get("source") or "")
        prefix = "managed" if source != "external_path" else "path"
        lines.append(f"- {name} ({prefix}: {path})")
    return "\n".join(lines)


def _chat_model_messages(messages: list[dict]) -> list[dict[str, str]]:
    """Return only model-relevant turn payload, with attachments anchored to users."""
    turns: list[dict[str, str]] = []
    for msg in messages:
        role = str(msg.get("role") or "").strip()
        content = msg.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str) and content.strip():
            model_content = content
            context_parts: list[str] = []
            context_text = _message_context_text(msg.get("context")) if role == "user" else ""
            if context_text:
                context_parts.append(context_text)
            if role == "user":
                for ref in _conversation_store.normalize_attachments(msg.get("attachments")):
                    ref_context = _conversation_store.attachment_context_text(ref)
                    if ref_context:
                        context_parts.append(ref_context)
            if context_parts:
                joined_context = "\n\n".join(context_parts)
                model_content = (
                    f"{content.rstrip()}\n\n"
                    "[Attached context for this message]\n"
                    "Use this when the user refers to the attached file, document, image, or context.\n"
                    f"{joined_context}"
                )
            turn: dict[str, str] = {"role": role, "content": model_content}
            image = _conversation_store.first_image_base64_from_message(msg)
            if role == "user" and image:
                turn["image_base64"] = str(image)
            turns.append(turn)
    return turns


def _normalized_file_context(items: list) -> list[dict]:
    """Normalize persisted local-file tool metadata."""
    out: list[dict] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = {
            "tool": str(raw.get("tool") or ""),
            "path": str(raw.get("path") or ""),
            "relative_path": str(raw.get("relative_path") or ""),
            "root": str(raw.get("root") or ""),
            "ok": bool(raw.get("ok")),
            "message": str(raw.get("message") or ""),
        }
        if item["tool"] and item["path"] and item not in out:
            out.append(item)
    return out[-20:]


def _merge_file_context(conv: dict, items: list) -> None:
    """Merge local-file metadata into a conversation."""
    merged = _normalized_file_context(list(conv.get("file_context") or []) + list(items or []))
    if merged:
        conv["file_context"] = merged


def _normalized_tool_context(raw: dict) -> dict:
    """Normalize persisted tool policy metadata."""
    if not isinstance(raw, dict):
        return {}

    def _str_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    mode = str(raw.get("file_access_mode") or "").strip().lower()
    if mode not in {"off", "read", "ask", "auto"}:
        mode = ""
    ctx = {
        "allowed_tools": _str_list(raw.get("allowed_tools")),
        "pinned_tools": _str_list(raw.get("pinned_tools")),
        "file_access_mode": mode,
    }
    if not ctx["allowed_tools"] and not ctx["pinned_tools"] and not ctx["file_access_mode"]:
        return {}
    return ctx


def _merge_tool_context(conv: dict, raw: dict) -> None:
    """Merge tool policy metadata into a conversation."""
    ctx = _normalized_tool_context(raw)
    if ctx:
        conv["tool_context"] = ctx


def _merge_file_context_from_messages(messages: list) -> list[dict]:
    """Rebuild conversation file metadata from retained message metadata."""
    items: list = []
    for msg in messages or []:
        if isinstance(msg, dict):
            items.extend(msg.get("file_context") or [])
    return _normalized_file_context(items)


def _latest_tool_context_from_messages(messages: list) -> dict:
    """Return the latest retained tool policy metadata from assistant replies."""
    latest: dict = {}
    for msg in messages or []:
        if isinstance(msg, dict):
            ctx = _normalized_tool_context(msg.get("tool_context") or {})
            if ctx:
                latest = ctx
    return latest


def _context_from_messages(messages: list) -> str:
    """Rebuild hidden context from retained message-scoped context blocks."""
    blocks: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        text = _message_context_text(msg.get("context"))
        if text:
            blocks.append(text)
    return "\n\n---\n".join(blocks)


def _context_not_anchored_to_messages(context: str, messages: list) -> str:
    """Return conversation context blocks not already carried by message turns."""
    text = str(context or "").strip()
    if not text:
        return ""
    message_context = _context_from_messages(messages)
    if not message_context:
        return text
    blocks = [block.strip() for block in re.split(r"\n\s*---\s*\n", text) if block.strip()]
    missing = [block for block in blocks if block not in message_context]
    return "\n\n---\n".join(missing)


def _context_mode(value: object, default: str = "off") -> str:
    mode = str(value or default or "off").strip().lower()
    return mode if mode in {"off", "auto", "model"} else default


def _all_context_off_policy() -> dict:
    return {
        "context_ambient": False,
        "context_documents": False,
        "context_tools": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",
        "context_clipboard": False,
        "_context_selection_enabled": False,
        "file_access": "off",
        "tools": {},
    }


def _default_context_policy() -> dict:
    """Default chat policy: first caller row, or no context when none exists."""
    rows = getattr(config, "CALLER_ROWS", [])
    if not rows:
        return _all_context_off_policy()
    return _normalized_context_policy(rows[0])


def _normalized_context_policy(raw: dict | None) -> dict:
    """Normalize persisted chat context/tool policy metadata."""
    if not isinstance(raw, dict):
        return {}
    base = _all_context_off_policy()
    tools = raw.get("tools")
    base.update(
        {
            "context_ambient": bool(raw.get("context_ambient", base["context_ambient"])),
            "context_documents_mode": tool_modes.context_mode(raw, "documents"),
            "context_browser_mode": tool_modes.context_mode(raw, "browser"),
            "context_github_mode": tool_modes.context_mode(raw, "github"),
            "context_memory_mode": tool_modes.context_mode(raw, "memory"),
            "context_screenshot": _context_mode(raw.get("context_screenshot"), "off"),
            "context_clipboard": bool(raw.get("context_clipboard", False)),
            "file_access": tool_modes.local_file_access_mode(raw),
            "tools": dict(tools) if isinstance(tools, dict) else {},
        }
    )
    base["context_documents"] = base["context_documents_mode"] == "auto"
    base["context_tools"] = any(
        base[key] == "model"
        for key in (
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
        )
    )
    base["_context_selection_enabled"] = bool(raw.get("_context_selection_enabled", False))
    return base


def _ensure_conversation_context_policy(conv: dict) -> dict:
    policy = _normalized_context_policy(conv.get("context_policy"))
    if not policy:
        policy = _default_context_policy()
        conv["context_policy"] = policy
    return policy


def _policy_state(policy: dict, source: str) -> str:
    if source == "ambient":
        if not policy.get("context_ambient") and tool_modes.context_mode(policy, "documents") == "off":
            return "off"
        return "auto" if tool_modes.context_mode(policy, "documents") == "model" else "on"
    if source == "browser":
        mode = tool_modes.context_mode(policy, "browser")
        return "auto" if mode == "model" else ("on" if mode == "auto" else "off")
    if source == "selection":
        return "on" if policy.get("_context_selection_enabled", False) else "off"
    if source == "clipboard":
        return "on" if policy.get("context_clipboard") else "off"
    if source == "screenshot":
        mode = str(policy.get("context_screenshot") or "off").lower()
        return "auto" if mode == "model" else ("on" if mode == "auto" else "off")
    if source == "memory":
        mode = tool_modes.context_mode(policy, "memory")
        return "auto" if mode == "model" else ("on" if mode == "auto" else "off")
    if source == "files":
        return tool_modes.local_file_access_mode(policy)
    return "off"


def _apply_policy_state(policy: dict, source: str, state: str) -> dict:
    updated = _normalized_context_policy(policy)
    state = str(state or "off").lower()
    if source == "ambient":
        updated["context_ambient"] = state != "off"
        updated["context_documents_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "browser":
        updated["context_browser_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "selection":
        updated["_context_selection_enabled"] = state != "off"
    elif source == "clipboard":
        updated["context_clipboard"] = state != "off"
    elif source == "screenshot":
        updated["context_screenshot"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "memory":
        updated["context_memory_mode"] = "off" if state == "off" else ("model" if state == "auto" else "auto")
    elif source == "files":
        updated["file_access"] = state if state in {"off", "read", "ask", "auto"} else "off"
    updated["context_documents"] = updated["context_documents_mode"] == "auto"
    updated["context_tools"] = any(
        updated[key] == "model"
        for key in (
            "context_documents_mode",
            "context_browser_mode",
            "context_github_mode",
            "context_memory_mode",
        )
    )
    return updated


def _append_context_block(existing: str, title: str, body: str) -> str:
    """Append a labelled context block while keeping separator formatting stable."""
    text = str(body or "").strip()
    if not text:
        return str(existing or "")
    block = f"[{title}]\n{text}"
    current = str(existing or "").strip()
    return f"{current}\n\n---\n{block}" if current else block


def _file_context_text(items: list) -> str:
    """Build hidden follow-up context for recent local-file tools."""
    normalized = _normalized_file_context(items)
    if not normalized:
        return ""
    lines = [
        "Recent local file tool context for this conversation.",
        "Use these exact paths when the user refers to 'that file' or a prior file.",
    ]
    for item in normalized[-8:]:
        status = "ok" if item.get("ok") else "failed"
        path = item.get("path") or item.get("relative_path")
        rel = item.get("relative_path") or ""
        label = f"{item.get('tool')} ({status}): {path}"
        if rel and rel != path:
            label += f" [relative: {rel}]"
        message = str(item.get("message") or "").strip()
        if message:
            label += f" - {message}"
        lines.append(f"- {label}")
    return "\n".join(lines)


def _truncate_for_display(text: str, limit: int, label: str = "display") -> str:
    """Handle truncate for display for UI chat window."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    hidden = len(text) - limit
    return text[:limit].rstrip() + f"\n\n[{label} truncated; {hidden} chars hidden]"


def _truncate_segments_for_display(
    segments: list[tuple[str, bool]],
    limit: int = _CHAT_RENDER_CHAR_LIMIT,
) -> list[tuple[str, bool]]:
    """Handle truncate segments for display for UI chat window."""
    total = sum(len(text) for text, _is_thought in segments)
    if total <= limit:
        return segments

    remaining = limit
    visible: list[tuple[str, bool]] = []
    for text, is_thought in segments:
        if remaining <= 0:
            break
        if len(text) <= remaining:
            visible.append((text, is_thought))
            remaining -= len(text)
            continue
        visible.append((text[:remaining].rstrip(), is_thought))
        remaining = 0

    hidden = total - limit
    _merge_display_segments(
        visible,
        f"\n\n[chat display truncated; {hidden} chars hidden]",
        False,
    )
    return visible


class _StreamSignals(QObject):
    """Model stream signals."""
    chunk     = Signal(object)
    final     = Signal(str)
    metadata  = Signal(object)
    finished  = Signal()


class _MessageTextView(QTextBrowser):
    """Model message text view."""
    def __init__(self, style_sheet: str):
        """Initialize the message text view instance."""
        super().__init__()
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(style_sheet)
        self.textChanged.connect(self._sync_height)

    def _sync_height(self):
        """Handle sync height for message text view."""
        doc_h = self.document().documentLayout().documentSize().height()
        margin = self.contentsMargins().top() + self.contentsMargins().bottom()
        self.setFixedHeight(max(38, int(doc_h + margin + 6)))

    def showEvent(self, event):
        """Show event."""
        super().showEvent(event)
        # Document layout hasn't run before first show — recompute height now.
        QTimer.singleShot(0, self._sync_height)

    def resizeEvent(self, event):
        """Resize event."""
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._sync_height()

    def wheelEvent(self, event):  # noqa: N802
        """Let the conversation page scroll instead of the individual bubble."""
        event.ignore()


class _ConversationTitleButton(QPushButton):
    """Paints a sidebar title with a right-edge fade under the overlaid menu."""

    def __init__(self, title: str, subtitle: str = "", *, active: bool, latest: bool) -> None:
        """Initialize the conversation title button instance."""
        super().__init__("")
        self._title = title
        self._subtitle = subtitle
        self._active = active
        self._latest = latest
        self.setCheckable(True)
        self.setChecked(active)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setToolTip(title)
        self.setAccessibleName(title)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def set_sidebar_state(self, *, active: bool, latest: bool) -> None:
        """Set sidebar state."""
        self._active = active
        self._latest = latest
        self.setChecked(active)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        """Paint event."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        bg = QColor(160, 160, 255, 18) if self._active or self.isChecked() else QColor(0, 0, 0, 0)
        if self.underMouse() and not (self._active or self.isChecked()):
            bg = QColor(255, 255, 255, 10)
        if bg.alpha():
            painter.fillRect(rect, bg)

        text_rect = rect.adjusted(10, 0, -(_SIDEBAR_MENU_W + 10), 0)
        title_font = QFont("Segoe UI", 9)
        subtitle_font = QFont("Segoe UI", 8)
        painter.setFont(title_font)
        color = QColor(_ACCENT if self._latest else _TEXT)
        painter.setPen(QPen(color))

        metrics = QFontMetrics(title_font)
        available = max(0, text_rect.width() - _SIDEBAR_FADE_W)
        title = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, available)
        title_rect = text_rect.adjusted(0, 4 if self._subtitle else 0, 0, -18 if self._subtitle else 0)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title,
        )
        if self._subtitle:
            painter.setFont(subtitle_font)
            painter.setPen(QPen(QColor(_HINT)))
            sub_metrics = QFontMetrics(subtitle_font)
            subtitle = sub_metrics.elidedText(self._subtitle, Qt.TextElideMode.ElideRight, available)
            subtitle_rect = text_rect.adjusted(0, 24, 0, -2)
            painter.drawText(
                subtitle_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                subtitle,
            )

        if metrics.horizontalAdvance(self._title) > available:
            fade_left = max(text_rect.left(), text_rect.right() - _SIDEBAR_FADE_W)
            gradient = QLinearGradient(fade_left, 0, text_rect.right(), 0)
            fade_color = QColor(31, 31, 45) if self._active or self.isChecked() else QColor(_SIDEBAR_BG)
            if self.underMouse() and not (self._active or self.isChecked()):
                fade_color = QColor(36, 36, 46)
            clear = QColor(fade_color)
            clear.setAlpha(0)
            gradient.setColorAt(0.0, clear)
            gradient.setColorAt(0.72, fade_color)
            gradient.setColorAt(1.0, fade_color)
            painter.fillRect(fade_left, rect.top(), text_rect.right() - fade_left + 1, rect.height(), gradient)

        painter.end()


class _ConversationSidebarRow(QWidget):
    """Sidebar row with a full-width title and an overlaid options button."""

    def __init__(self, title_btn: QPushButton, menu_btn: QPushButton) -> None:
        """Initialize the conversation sidebar row instance."""
        super().__init__()
        self.setFixedHeight(52)
        self.setStyleSheet("background: transparent;")
        self._title_btn = title_btn
        self._menu_btn = menu_btn
        self._title_btn.setParent(self)
        self._menu_btn.setParent(self)
        self._layout_children()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        """Resize event."""
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self) -> None:
        """Handle layout children for conversation sidebar row."""
        self._title_btn.setGeometry(self.rect())
        self._menu_btn.setGeometry(
            max(0, self.width() - _SIDEBAR_MENU_W - 4),
            0,
            _SIDEBAR_MENU_W,
            self.height(),
        )
        self._menu_btn.raise_()


def _merge_display_segments(segments: list[tuple[str, bool]], text: str, is_thought: bool) -> list[tuple[str, bool]]:
    """Merge display segments."""
    if not text:
        return segments
    if segments and segments[-1][1] == is_thought:
        segments[-1] = (segments[-1][0] + text, is_thought)
    else:
        segments.append((text, is_thought))
    return segments


class ChatWindow(QWidget):
    """Qt window for chat window."""
    def __init__(
        self,
        conversations: list[list[dict]],
        send_fn,
        auto_message: str | None = None,
        start_new: bool = False,
        projects: list[dict] | None = None,
        active_project_id: str | None = None,
        on_project_change=None,
        on_new_project=None,
        persist_fn=None,
        active_idx: int | None = None,
        on_select=None,
    ):
        """
        Args:
            conversations: Direct reference to the app's list of all past
                           conversations. Each item is a dict with keys
                           ``"messages"`` (list of role/content turns) and
                           ``"context"`` (ambient context string).
            send_fn:       Callable yielding text chunks and optional final text events.
            auto_message:  If set, automatically sent when the window opens.
            projects:      List of {"id", "name"} dicts for the project selector.
            active_project_id: Project new conversations are filed under.
            on_project_change: Callable(project_id) invoked when the user picks
                           a different project (e.g. to scope memory).
            on_new_project: Callable(name) -> project dict, creating + persisting
                           a project; returns the new project.
            persist_fn:    Callable() invoked after a reply lands to save chats.
            active_idx:    Index of the conversation to select on open (the one
                           hotkey/voice prompts currently continue).
            on_select:     Callable(idx) invoked when the user selects or starts a
                           conversation, so the app can retarget hotkey prompts.
        """
        super().__init__()
        self._conversations = conversations  # live reference -” NOT a copy
        for conv in self._conversations:
            if isinstance(conv, dict):
                _ensure_conversation_metadata(conv)
        self._send_fn = send_fn
        self._on_select = on_select
        self._projects = list(projects or [])
        if not any(p.get("id") == _GENERAL_PROJECT_ID for p in self._projects):
            self._projects.insert(0, {"id": _GENERAL_PROJECT_ID, "name": t("General")})
        self._active_project_id = active_project_id or _GENERAL_PROJECT_ID
        self._on_project_change = on_project_change
        self._on_new_project = on_new_project
        self._persist_fn = persist_fn
        self._streaming = False
        self._current_ai_label: _MessageTextView | None = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments: list[tuple[str, bool]] = []
        self._current_ai_parser: ThoughtStreamParser | None = None
        self._current_file_context: list[dict] = []
        self._current_tool_context: dict = {}
        self._pending_attachment_context = ""
        self._pending_attachment_image_b64: str | None = None
        self._pending_attachments: list[dict] = []
        self._pending_attachment_labels: list[str] = []
        self._attachment_label: QLabel | None = None
        self._attach_btn: QPushButton | None = None
        self._context_controls: dict[str, QPushButton] = {}
        self._context_control_options: dict[str, list[tuple[str, str]]] = {}
        self._context_control_labels: dict[str, str] = {}
        self._context_control_keys: dict[str, str] = {}
        self._context_control_tokens: dict[str, str] = {}
        self._context_control_warnings: dict[str, str] = {}
        self._context_controls_updating = False
        self._conversation_menu: QMenu | None = None
        self.setAcceptDrops(True)
        if active_idx is not None and 0 <= active_idx < len(conversations):
            self._active_idx = active_idx
        else:
            self._active_idx = max(0, len(conversations) - 1)
        self._built_pages: set[int] = set()

        self._signals = _StreamSignals()
        self._signals.chunk.connect(self._on_chunk)
        self._signals.final.connect(self._on_final_text)
        self._signals.metadata.connect(self._on_metadata)
        self._signals.finished.connect(self._on_finished)

        self.setWindowTitle(t("Chat"))
        self.setWindowFlags(Qt.WindowType.Window)
        enable_standard_window_controls(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")
        self.setMinimumSize(_W, _H)
        self.resize(_W, _H)

        self._build_ui()
        self._center_on_screen()
        self._new_shortcut = QShortcut(QKeySequence.StandardKey.New, self)
        self._new_shortcut.activated.connect(self.start_new_conversation)

        if start_new:
            QTimer.singleShot(0, lambda: self.start_new_conversation(auto_message=auto_message))
        elif conversations:
            if self._on_select and 0 <= self._active_idx < len(self._conversations):
                self._on_select(self._active_idx)
            if auto_message:
                QTimer.singleShot(120, lambda: self._send(auto_message))

    # ------------------------------------------------------------------ Build

    def _build_ui(self):
        """Build ui."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._make_title_bar())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(255,255,255,20); }")
        splitter.addWidget(self._make_sidebar())
        splitter.addWidget(self._make_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([185, _W - 185])
        root.addWidget(splitter, stretch=1)

    def _make_title_bar(self) -> QWidget:
        """Create title bar."""
        bar = QWidget()
        bar.setFixedHeight(38)
        bar.setStyleSheet(f"background: {_TITLE_BG}; border-bottom: 1px solid {_BORDER};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 8, 0)
        title = QLabel(t("Chat"))
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_ACCENT}; background: transparent;")
        new_chat = QPushButton(t("New"))
        new_chat.setFixedSize(52, 26)
        new_chat.setToolTip(t("Start a new conversation (Ctrl+N)"))
        new_chat.setStyleSheet(
            f"QPushButton {{ background: rgba(160,160,255,18); color: {_ACCENT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 9pt; }}"
            "QPushButton:hover { background: rgba(160,160,255,28); }"
            "QPushButton:disabled { color: #666; border-color: rgba(255,255,255,10); }"
        )
        new_chat.clicked.connect(self.start_new_conversation)
        self._new_chat_btn = new_chat
        h.addWidget(title)
        h.addStretch()
        h.addWidget(self._make_project_selector())
        h.addWidget(new_chat)
        return bar

    _NEW_PROJECT_SENTINEL = "__new_project__"

    def _make_project_selector(self) -> QWidget:
        """Dropdown that scopes new conversations (and memory) to a project."""
        combo = QComboBox()
        combo.setFixedHeight(26)
        combo.setMinimumWidth(120)
        combo.setToolTip(t("Project for new chats (memory is scoped per project)"))
        combo.setStyleSheet(
            f"QComboBox {{ background: rgba(160,160,255,12); color: {_TEXT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; padding: 2px 8px;"
            " font-size: 9pt; }"
            f" QComboBox QAbstractItemView {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" selection-background-color: {_SEL_BG}; }}"
        )
        self._project_combo = combo
        self._reload_project_combo()
        combo.currentIndexChanged.connect(self._on_project_selected)
        return combo

    def _reload_project_combo(self) -> None:
        """Handle reload project combo for chat window."""
        combo = self._project_combo
        combo.blockSignals(True)
        combo.clear()
        for proj in self._projects:
            combo.addItem(proj.get("name", t("General")), proj.get("id"))
        combo.addItem(t("＋ New project…"), self._NEW_PROJECT_SENTINEL)
        idx = combo.findData(self._active_project_id)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _on_project_selected(self, _index: int) -> None:
        """Handle project selected events."""
        data = self._project_combo.currentData()
        if data == self._NEW_PROJECT_SENTINEL:
            self._create_project_interactive()
            return
        if not data or data == self._active_project_id:
            return
        self._active_project_id = data
        if self._on_project_change:
            self._on_project_change(data)

    def _create_project_interactive(self) -> None:
        """Create project interactive."""
        name, ok = QInputDialog.getText(self, t("New project"), t("Project name:"))
        name = (name or "").strip()
        if not ok or not name or self._on_new_project is None:
            # Revert the combo to the current project (user cancelled).
            self._reload_project_combo()
            return
        project = self._on_new_project(name)
        if project:
            if not any(p.get("id") == project.get("id") for p in self._projects):
                self._projects.append(project)
            self._active_project_id = project.get("id")
            if self._on_project_change:
                self._on_project_change(self._active_project_id)
        self._reload_project_combo()

    # ------------------------------------------------------------------ Sidebar

    def _make_sidebar(self) -> QWidget:
        """Create sidebar."""
        sidebar = QWidget()
        sidebar.setMinimumWidth(100)
        sidebar.setStyleSheet(f"background: {_SIDEBAR_BG};")
        vl = QVBoxLayout(sidebar)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        hdr = QLabel(t("  History"))
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(
            f"background: {_SIDEBAR_BG}; color: {_HINT}; font-size: 9pt;"
            f" font-weight: bold; border-bottom: 1px solid {_BORDER};"
        )
        vl.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"background: {_SIDEBAR_BG};")

        self._sidebar_items = QWidget()
        self._sidebar_items.setStyleSheet(f"background: {_SIDEBAR_BG};")
        self._sidebar_layout = QVBoxLayout(self._sidebar_items)
        self._sidebar_layout.setContentsMargins(0, 4, 0, 4)
        self._sidebar_layout.setSpacing(1)
        self._sidebar_btns: list[tuple[int, QPushButton]] = []
        self._rebuild_sidebar()

        scroll.setWidget(self._sidebar_items)
        vl.addWidget(scroll, stretch=1)
        return sidebar

    def _rebuild_sidebar(self):
        """Handle rebuild sidebar for chat window."""
        while self._sidebar_layout.count():
            item = self._sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sidebar_btns.clear()

        if not self._conversations:
            lbl = QLabel(t("  No history yet."))
            lbl.setStyleSheet(
                f"color: {_HINT}; font-size: 9pt; padding: 8px; background: transparent;"
            )
            self._sidebar_layout.addWidget(lbl)
        else:
            # Pinned conversations float to the top; within each group the
            # newest is first. sort() is stable so it preserves recency order.
            order = list(range(len(self._conversations) - 1, -1, -1))
            order.sort(key=lambda ix: not self._conversations[ix].get("pinned"))
            for real_idx in order:
                row, title_btn = self._make_sidebar_row(real_idx, self._conversations[real_idx])
                self._sidebar_layout.addWidget(row)
                self._sidebar_btns.append((real_idx, title_btn))
        self._sidebar_layout.addStretch()

    def _conversation_title(self, idx: int, conv: dict) -> str:
        """Handle conversation title for chat window."""
        override = str(conv.get("title_override") or "").strip()
        if override:
            return override
        first_user = next((m for m in conv["messages"] if m["role"] == "user"), None)
        raw = first_user["content"] if first_user else f"{t('Conversation')} {idx+1}"
        has_image = bool(first_user and _conversation_store.first_image_base64_from_message(first_user))
        prefix = f"[{t('image')}] " if has_image else ""
        return prefix + str(raw).strip().replace("\n", " ")

    def _conversation_timestamp(self, conv: dict) -> str:
        """Return display timestamp for a conversation."""
        return _format_conversation_datetime(conv.get("updated_at") or conv.get("created_at"))

    def _make_sidebar_row(self, idx: int, conv: dict) -> tuple[QWidget, QPushButton]:
        """Create sidebar row."""
        title = self._conversation_title(idx, conv)
        if conv.get("pinned"):
            title = "📌 " + title
        subtitle = self._conversation_timestamp(conv)
        is_latest = (idx == len(self._conversations) - 1)
        is_active = (idx == self._active_idx)

        btn = _ConversationTitleButton(title, subtitle, active=is_active, latest=is_latest)
        btn.setToolTip("\n".join(part for part in (title, subtitle) if part))
        btn.clicked.connect(lambda _checked, ix=idx: self._switch(ix))

        menu_btn = QPushButton("⋮")
        menu_btn.setFixedSize(_SIDEBAR_MENU_W, 52)
        menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_btn.setToolTip(t("Conversation options"))
        menu_btn.setAccessibleName(t("Conversation options"))
        menu_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
            " font-family: 'Segoe UI Symbol', 'Segoe UI'; font-size: 16pt;"
            " font-weight: 700; padding: 0; margin: 0; }"
            f"QPushButton:hover {{ background: rgba(255,255,255,12); color: {_TEXT}; }}"
        )
        menu_btn.clicked.connect(
            lambda _checked, ix=idx, button=menu_btn: self._open_conversation_menu(ix, button)
        )
        row = _ConversationSidebarRow(btn, menu_btn)
        return row, btn

    def _open_conversation_menu(self, idx: int, anchor: QWidget | None = None) -> None:
        """Open conversation menu."""
        if not (0 <= idx < len(self._conversations)):
            return
        conv = self._conversations[idx]
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        pin_label = t("Unpin") if conv.get("pinned") else t("Pin")
        menu.addAction(pin_label, lambda: self._toggle_pin(idx))
        menu.addAction(t("Rename"), lambda: self._rename_conversation(idx))

        project_menu = menu.addMenu(t("Add to project"))
        for proj in self._projects:
            pid = proj.get("id")
            name = proj.get("name", "General")
            act = project_menu.addAction(name, lambda p=pid: self._assign_project(idx, p))
            act.setCheckable(True)
            act.setChecked(conv.get("project_id", _GENERAL_PROJECT_ID) == pid)

        menu.addSeparator()
        menu.addAction(t("Delete"), lambda: self._delete_conversation(idx))
        # Drop the menu just below the ⋮ button that opened it.
        pos = (
            anchor.mapToGlobal(anchor.rect().bottomLeft())
            if anchor is not None
            else self.mapToGlobal(self.rect().center())
        )
        self._conversation_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_conversation_menu", None))
        menu.popup(pos)

    def _toggle_pin(self, idx: int) -> None:
        """Handle toggle pin for chat window."""
        if not (0 <= idx < len(self._conversations)):
            return
        conv = self._conversations[idx]
        conv["pinned"] = not conv.get("pinned")
        _touch_conversation(conv)
        self._rebuild_sidebar()
        self._persist()

    def _rename_conversation(self, idx: int) -> None:
        """Handle rename conversation for chat window."""
        if not (0 <= idx < len(self._conversations)):
            return
        conv = self._conversations[idx]
        current = self._conversation_title(idx, conv)
        name, ok = QInputDialog.getText(
            self, t("Rename conversation"), t("Title:"), text=current
        )
        if not ok:
            return
        conv["title_override"] = name.strip()
        _touch_conversation(conv)
        self._rebuild_sidebar()
        self._persist()

    def _assign_project(self, idx: int, project_id: str) -> None:
        """Handle assign project for chat window."""
        if not (0 <= idx < len(self._conversations)):
            return
        self._conversations[idx]["project_id"] = project_id
        _touch_conversation(self._conversations[idx])
        self._rebuild_sidebar()
        self._persist()

    def _delete_conversation(self, idx: int) -> None:
        """Delete conversation."""
        if not (0 <= idx < len(self._conversations)):
            return
        if self._streaming and idx == self._active_idx:
            return  # don't delete the conversation mid-stream
        if QMessageBox.question(
            self, t("Delete conversation"),
            t("Delete this conversation? This cannot be undone."),
        ) != QMessageBox.StandardButton.Yes:
            return
        del self._conversations[idx]
        if self._active_idx >= idx:
            self._active_idx = max(0, self._active_idx - 1)
        self._rebuild_stack()
        self._rebuild_sidebar()
        if self._conversations:
            self._switch(min(self._active_idx, len(self._conversations) - 1))
        else:
            self._input_frame.setEnabled(False)
        self._persist()

    def _rebuild_stack(self) -> None:
        """Tear down and rebuild all stack pages 1:1 with _conversations."""
        while self._stack.count():
            w = self._stack.widget(0)
            self._stack.removeWidget(w)
            w.deleteLater()
        self._built_pages = set()
        self._has_placeholder = not self._conversations
        if self._conversations:
            for i, conv in enumerate(self._conversations):
                if i == self._active_idx:
                    self._stack.addWidget(self._make_page(i, conv))
                else:
                    self._stack.addWidget(self._make_page_placeholder())
        else:
            ph = QLabel(t("No conversations yet.\n\nPress Ctrl+Q to ask something."))
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
            self._stack.addWidget(ph)
        self._stack.setCurrentIndex(max(0, min(self._active_idx, self._stack.count() - 1)))

    def _persist(self) -> None:
        """Handle persist for chat window."""
        if self._persist_fn:
            try:
                self._persist_fn()
            except Exception:
                pass

    def _btn_style(self, active: bool, latest: bool) -> str:
        """Handle btn style for chat window."""
        bg = _SEL_BG if active else "transparent"
        c  = _ACCENT if latest else _TEXT
        return (
            f"QPushButton {{ background: {bg}; color: {c}; border: none;"
            f" text-align: left; padding: 6px 10px; font-size: 9pt; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,10); }}"
            f"QPushButton:checked {{ background: {_SEL_BG}; }}"
        )

    def _switch(self, idx: int):
        """Handle switch for chat window."""
        self._active_idx = idx
        if idx < self._stack.count():
            self._ensure_page_built(idx)
            self._stack.setCurrentIndex(idx)
        self._update_selected_conversation_notice(idx)
        self._input_frame.setEnabled(bool(self._conversations))
        for real_idx, btn in self._sidebar_btns:
            is_sel = (real_idx == idx)
            if isinstance(btn, _ConversationTitleButton):
                btn.set_sidebar_state(
                    active=is_sel,
                    latest=real_idx == len(self._conversations) - 1,
                )
            else:
                btn.setChecked(is_sel)
                btn.setStyleSheet(self._btn_style(is_sel, real_idx == len(self._conversations) - 1))
        if self._on_select and 0 <= idx < len(self._conversations):
            self._on_select(idx)
        self._refresh_context_controls()

    def _update_selected_conversation_notice(self, idx: int) -> None:
        """Show which conversation the composer will continue."""
        if not (0 <= idx < len(self._conversations)):
            self._past_notice.setVisible(False)
            return
        title = self._conversation_title(idx, self._conversations[idx])
        self._past_notice.setText(f"  {t('Continuing')}: {title}")
        self._past_notice.setToolTip(title)
        self._past_notice.setVisible(True)

    def sync_conversation(self, idx: int) -> None:
        """Rebuild and show a conversation a hotkey/voice prompt just appended to.

        Called when a prompt continued an existing thread rather than starting a
        new one, so the open window reflects the added turns and follows along.
        """
        if not (0 <= idx < len(self._conversations)):
            return
        # Force the page to rebuild with the appended turns, then show it.
        self._built_pages.discard(idx)
        self._rebuild_sidebar()
        self._switch(idx)

    # ------------------------------------------------------------------ Right panel

    def _make_right_panel(self) -> QWidget:
        """Create right panel."""
        panel = QWidget()
        vl = QVBoxLayout(panel)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {_BG};")
        # When no history exists yet a single placeholder widget sits at index 0;
        # _has_placeholder lets ingest_new_conversations swap it out for real pages.
        self._has_placeholder = not self._conversations
        if self._conversations:
            for i, conv in enumerate(self._conversations):
                if i == self._active_idx:
                    self._stack.addWidget(self._make_page(i, conv))
                else:
                    self._stack.addWidget(self._make_page_placeholder())
        else:
            ph = QLabel(t("No conversations yet.\n\nPress Ctrl+Q to ask something."))
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
            self._stack.addWidget(ph)
        self._stack.setCurrentIndex(self._active_idx)
        vl.addWidget(self._stack, stretch=1)

        self._past_notice = QLabel(t("  Selected conversation"))
        self._past_notice.setFixedHeight(26)
        self._past_notice.setStyleSheet(
            f"background: rgba(160,160,255,10); color: {_HINT};"
            f" font-size: 8pt; border-top: 1px solid {_BORDER};"
        )
        self._past_notice.setVisible(False)
        vl.addWidget(self._past_notice)

        self._input_frame = self._make_input_area()
        self._input_frame.setEnabled(bool(self._conversations))
        vl.addWidget(self._input_frame)
        return panel

    def start_new_conversation(self, auto_message: str | None = None):
        """Start new conversation."""
        if self._streaming:
            return

        was_empty = not self._conversations
        conv = {
            "id": str(uuid.uuid4()),
            "project_id": self._active_project_id,
            "messages": [],
            "context": "",
            "context_policy": _default_context_policy(),
        }
        _touch_conversation(conv)
        self._conversations.append(conv)

        if was_empty and self._has_placeholder:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._has_placeholder = False

        idx = len(self._conversations) - 1
        self._stack.addWidget(self._make_page(idx, conv))
        self._rebuild_sidebar()
        self._switch(idx)
        self._input.setFocus()

        if auto_message:
            QTimer.singleShot(0, lambda: self._send(auto_message))

    def ingest_new_conversations(self):
        """Build pages for any conversations appended to the shared list since the
        window was built (e.g. a query started via hotkey while the chat was open).

        The new tab is added to the history sidebar but NOT selected — the user
        stays on whatever tab they were reading. (Exception: if the window was
        showing the empty-history placeholder, the newest tab is shown so the
        window isn't left blank.)"""
        from_placeholder = self._has_placeholder and self._conversations
        if from_placeholder:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._has_placeholder = False

        # With no placeholder, stack index aligns 1:1 with _conversations.
        added = False
        for idx in range(self._stack.count(), len(self._conversations)):
            if idx == self._active_idx or from_placeholder:
                self._stack.addWidget(self._make_page(idx, self._conversations[idx]))
            else:
                self._stack.addWidget(self._make_page_placeholder())
            added = True
        if not added:
            return
        self._input_frame.setEnabled(True)
        self._rebuild_sidebar()
        if from_placeholder:
            self._switch(len(self._conversations) - 1)

    def _make_page_placeholder(self) -> QLabel:
        """Create page placeholder."""
        ph = QLabel(t("Loading conversation..."))
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
        return ph

    def _ensure_page_built(self, idx: int) -> None:
        """Ensure page built."""
        if idx in self._built_pages or idx < 0 or idx >= len(self._conversations):
            return
        if idx >= self._stack.count():
            return
        old = self._stack.widget(idx)
        page = self._make_page(idx, self._conversations[idx])
        self._stack.removeWidget(old)
        old.deleteLater()
        self._stack.insertWidget(idx, page)

    def _make_page(self, idx: int, conv: dict) -> QScrollArea:
        """Create page."""
        self._built_pages.add(idx)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"background: {_BG};")

        container = QWidget()
        container.setStyleSheet(f"background: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addStretch()

        _ensure_conversation_metadata(conv)
        stamp = self._conversation_time_label(conv)
        hint = self._context_hint(
            _context_not_anchored_to_messages(conv.get("context", ""), conv.get("messages", []))
        )
        insert_at = 0
        if stamp is not None:
            layout.insertWidget(insert_at, stamp)
            insert_at += 1
        if hint is not None:
            layout.insertWidget(insert_at, hint)  # sits above the first message

        last_ai: _MessageTextView | None = None
        for msg_idx, msg in enumerate(conv["messages"]):
            display_text = msg.get("display_content", msg["content"])
            view = self._bubble(
                layout,
                display_text,
                msg["role"],
                _conversation_store.first_image_base64_from_message(msg),
                created_at=msg.get("created_at") or conv.get("created_at"),
                conversation_index=idx,
                message_index=msg_idx,
            )
            if msg["role"] == "user":
                msg_hint = self._message_context_hint(msg.get("context"))
                if msg_hint is not None:
                    layout.insertWidget(layout.count() - 1, msg_hint)
            if msg["role"] == "assistant":
                last_ai = view

        scroll._last_assistant_view = last_ai  # type: ignore[attr-defined]
        scroll._msg_layout = layout  # type: ignore[attr-defined]
        scroll.setWidget(container)
        QTimer.singleShot(0, lambda s=scroll: s.verticalScrollBar().setValue(
            s.verticalScrollBar().maximum()
        ))
        return scroll

    def _context_hint(self, context: str) -> QLabel | None:
        """A small chip hinting at the context attached to this conversation
        (selected text, dropped files, ambient snapshot, ...). The full context
        is available on hover. When the document readers cut content off (they
        leave a ``[…truncated]`` marker), the chip flags it so the user knows the
        model didn't see everything. Returns None when there was no context."""
        text = (context or "").strip()
        if not text:
            return None
        truncated = "truncated]" in text  # marker left by the document/PDF readers
        preview = " ".join(text.split())  # collapse newlines/runs to one line
        if len(preview) > 160:
            preview = preview[:160].rstrip() + "…"
        body = f"{t('Context')} · {_token_label(text)} · {html.escape(preview)}"
        if truncated:
            body += f" <span style='color:#d6a04a;'>· {t('truncated')}</span>"
        lbl = QLabel(body)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        tooltip = _truncate_for_display(text, _CONTEXT_TOOLTIP_CHAR_LIMIT, "context tooltip")
        lbl.setToolTip(
            tooltip + f"\n\n[{t('context was truncated to fit the limit')}]" if truncated else tooltip
        )
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"QLabel {{ background: rgba(160,160,255,12); color: {_HINT};"
            f" font-size: 8pt; border: 1px solid {_BORDER}; border-radius: 6px;"
            f" padding: 5px 9px; }}"
        )
        return lbl

    def _message_context_hint(self, context: object) -> QLabel | None:
        """Small transcript chip for context attached to one user message."""
        text = _message_context_text(context)
        if not text:
            return None
        lines = text.splitlines()
        title = t("Attached")
        preview_text = text
        if lines:
            first = lines[0].strip()
            if first.startswith("[") and first.endswith("]"):
                title = first[1:-1].strip() or title
                preview_text = "\n".join(lines[1:]).strip() or text
        preview = " ".join(preview_text.split())
        if len(preview) > 140:
            preview = preview[:140].rstrip() + "…"
        truncated = "truncated]" in text
        body = f"{html.escape(title)} · {_token_label(text)}"
        if preview:
            body += f" · {html.escape(preview)}"
        if truncated:
            body += f" <span style='color:#d6a04a;'>· {t('truncated')}</span>"
        lbl = QLabel(body)
        lbl.setObjectName("messageAttachmentContextHint")
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        tooltip = _truncate_for_display(text, _CONTEXT_TOOLTIP_CHAR_LIMIT, "attached context tooltip")
        lbl.setToolTip(
            tooltip + f"\n\n[{t('context was truncated to fit the limit')}]" if truncated else tooltip
        )
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"QLabel {{ background: rgba(160,160,255,10); color: {_HINT};"
            f" font-size: 8pt; border: 1px solid {_BORDER}; border-radius: 6px;"
            f" padding: 4px 8px; margin-left: 4px; margin-right: 4px; }}"
        )
        return lbl

    def _conversation_time_label(self, conv: dict) -> QLabel | None:
        """Small display-only timestamp for a conversation page."""
        created = _format_conversation_datetime(conv.get("created_at"))
        updated = _format_conversation_datetime(conv.get("updated_at"))
        if not created and not updated:
            return None
        text = created or updated
        if created and updated and updated != created:
            text = f"{created} · updated {updated}"
        lbl = QLabel(html.escape(text))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setStyleSheet(
            f"color: {_HINT}; font-size: 8pt; padding: 0 4px 2px 4px;"
            " background: transparent;"
        )
        return lbl

    def _make_input_area(self) -> QWidget:
        """Create input area."""
        frame = QWidget()
        frame.setStyleSheet(f"background: {_TITLE_BG}; border-top: 1px solid {_BORDER};")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        outer.addWidget(self._make_context_policy_controls())

        self._attachment_label = QLabel("")
        self._attachment_label.setWordWrap(True)
        self._attachment_label.setVisible(False)
        self._attachment_label.setStyleSheet(
            f"QLabel {{ color: {_HINT}; background: rgba(160,160,255,12);"
            f" border: 1px solid {_BORDER}; border-radius: 6px; padding: 4px 7px;"
            " font-size: 8pt; }}"
        )
        outer.addWidget(self._attachment_label)

        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self._input = QTextEdit()
        self._input.setAcceptDrops(False)
        self._input.setFixedHeight(62)
        self._input.setPlaceholderText(t("Message... (Enter to send, Shift+Enter for newline)"))
        self._input.setStyleSheet(
            f"QTextEdit {{ background: rgba(255,255,255,8); border: 1px solid {_BORDER};"
            f" border-radius: 6px; color: {_TEXT}; padding: 6px 8px; font-size: 10pt; }}"
        )
        self._input.installEventFilter(self)

        self._attach_btn = QPushButton("+")
        self._attach_btn.setObjectName("chatAttachButton")
        self._attach_btn.setFixedSize(34, 46)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip(t("Add files or images as context"))
        self._attach_btn.setAccessibleName(t("Add files or images as context"))
        self._attach_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(160,160,255,18); color: {_ACCENT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 18pt;"
            " font-weight: 400; padding: 0 0 3px 0; }}"
            "QPushButton:hover { background: rgba(160,160,255,32); }"
            "QPushButton:disabled { color: #666; border-color: rgba(255,255,255,10); }"
        )
        self._attach_btn.clicked.connect(self._choose_attachments)

        self._send_btn = QPushButton(t("Send"))
        self._send_btn.setFixedSize(64, 46)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #1c1c24; border: none;"
            f" border-radius: 6px; font-size: 10pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #b8b8ff; }}"
            f"QPushButton:disabled {{ background: #444; color: #666; }}"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)
        h.addWidget(self._attach_btn)
        h.addWidget(self._input)
        h.addWidget(self._send_btn)
        outer.addLayout(h)
        return frame

    def _make_context_policy_controls(self) -> QWidget:
        """Create per-conversation context/tool controls above the chat input."""
        frame = QWidget()
        frame.setStyleSheet(
            "QWidget { background: transparent; }"
            "QPushButton { text-align: center; }"
        )
        outer = QHBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        raw_keys = str(getattr(config, "INTENT_CONTEXT_TOGGLE_KEYS", "1234567") or "1234567")
        keys: list[str] = []
        for ch in raw_keys + "1234567":
            if ch.isspace() or ch in keys:
                continue
            keys.append(ch)
            if len(keys) == 7:
                break
        rows = [
            (
                "ambient",
                f"{keys[0]} {t('App')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "browser",
                f"{keys[1]} {t('Browser/Web')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            ("selection", f"{keys[2]} {t('Selection')}", [("off", t("Off")), ("on", t("On"))]),
            ("clipboard", f"{keys[3]} {t('Clipboard')}", [("off", t("Off")), ("on", t("On"))]),
            (
                "screenshot",
                f"{keys[4]} {t('Screenshot')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "memory",
                f"{keys[5]} {t('Memory')}",
                [("off", t("Off")), ("on", t("On")), ("auto", t("Let model decide"))],
            ),
            (
                "files",
                f"{keys[6]} {t('Files')}",
                [("off", t("Off")), ("read", t("Read only")), ("ask", t("Ask before write")), ("auto", t("Auto"))],
            ),
        ]
        for source, label_text, options in rows:
            key, _, label = label_text.partition(" ")
            chip = QPushButton()
            chip.setObjectName(f"chatContextChip_{source}")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFixedHeight(54)
            chip.setMinimumWidth(78)
            chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chip.clicked.connect(lambda _checked=False, source=source: self._show_context_policy_menu(source))
            self._context_controls[source] = chip
            self._context_control_options[source] = options
            self._context_control_labels[source] = label or label_text
            self._context_control_keys[source] = key
            outer.addWidget(chip, 1)
        self._refresh_context_controls()
        return frame

    def _refresh_context_controls(self) -> None:
        """Refresh controls from the active conversation's saved policy."""
        if not self._context_controls:
            return
        self._context_controls_updating = True
        try:
            if 0 <= self._active_idx < len(self._conversations):
                conv = self._conversations[self._active_idx]
                policy = _ensure_conversation_context_policy(conv)
            else:
                policy = _all_context_off_policy()
            for source, chip in self._context_controls.items():
                state = _policy_state(policy, source)
                self._update_context_chip(chip, source, state)
        finally:
            self._context_controls_updating = False

    def _state_label_for_context_source(self, source: str, state: str) -> str:
        """Return display label for a context chip state."""
        if state == "auto":
            return t("auto")
        for value, label in self._context_control_options.get(source, []):
            if value == state:
                return label
        return state

    def _context_chip_style(self, state: str) -> str:
        """Return the compact intent-overlay-style chip CSS."""
        color = {
            "off": "#85889a",
            "auto": "#d1b15f",
            "model": "#d1b15f",
            "on": _ACCENT,
            "read": _ACCENT,
            "ask": "#d1b15f",
        }.get(state, _ACCENT)
        alpha = "32" if state == "off" else "46"
        return (
            f"QPushButton {{ background: rgba(160,160,255,{alpha}); color: {_TEXT};"
            f" border: 1px solid {color}; border-radius: 7px; padding: 3px 5px;"
            " font-size: 8pt; }}"
            f"QPushButton:hover {{ background: rgba(160,160,255,60); border-color: {_ACCENT}; }}"
            "QPushButton::menu-indicator { image: none; width: 0; }"
        )

    def _context_token_metadata(self, source: str, state: str) -> tuple[str, str]:
        """Return (token label, warning) for one chat context/tool chip."""
        if state == "off":
            return "0 tok", ""
        if source == "files":
            return _deferred_token_label(), t("File context depends on which file tools are used.")
        if source == "memory":
            return _deferred_token_label(), t("Memory tokens are estimated after the prompt is known.")
        if source == "screenshot":
            return _deferred_token_label(), t("Screenshot image cost is not known until it is sent or requested.")
        if source in {"ambient", "browser", "selection", "clipboard"}:
            return _deferred_token_label(), t("This context is fetched when you send the message, so this token cost is not known yet.")
        return _deferred_token_label(), ""

    def _update_context_chip(self, chip: QPushButton, source: str, state: str) -> None:
        """Paint one compact context chip from its current state."""
        key = self._context_control_keys.get(source, "")
        label = self._context_control_labels.get(source, source)
        state_label = self._state_label_for_context_source(source, state)
        tokens, warning = self._context_token_metadata(source, state)
        self._context_control_tokens[source] = tokens
        self._context_control_warnings[source] = warning
        chip.setText(f"{key} {label}\n{state_label}\n{tokens}")
        tooltip = f"{label}: {state_label}\n{t('Token estimate')}: {tokens}"
        if warning:
            tooltip += f"\n\n{warning}"
        chip.setToolTip(tooltip)
        chip.setProperty("context_state", state)
        chip.setProperty("context_tokens", tokens)
        chip.setStyleSheet(self._context_chip_style(state))

    def _show_context_policy_menu(self, source: str) -> None:
        """Open a small state list for one context chip."""
        if self._context_controls_updating:
            return
        chip = self._context_controls.get(source)
        if chip is None:
            return
        menu = QMenu(chip)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT}; border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        current = str(chip.property("context_state") or "off")
        for value, label in self._context_control_options.get(source, []):
            action = menu.addAction(label)
            action.setData(value)
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(
                lambda _checked=False, source=source, value=value: self._set_context_policy_state(source, value)
            )
        menu.popup(chip.mapToGlobal(chip.rect().bottomLeft()))

    def _set_context_policy_state(self, source: str, state: str) -> None:
        """Persist one visible context/tool control change to the conversation."""
        if self._context_controls_updating or not (0 <= self._active_idx < len(self._conversations)):
            return
        chip = self._context_controls.get(source)
        if chip is None:
            return
        conv = self._conversations[self._active_idx]
        policy = _ensure_conversation_context_policy(conv)
        conv["context_policy"] = _apply_policy_state(policy, source, state)
        _touch_conversation(conv)
        self._update_context_chip(chip, source, _policy_state(conv["context_policy"], source))
        self._persist()

    # ------------------------------------------------------------------ Bubbles

    def _bubble(
        self,
        layout,
        text: str,
        role: str,
        image_b64: str | None = None,
        *,
        created_at: str | None = None,
        conversation_index: int | None = None,
        message_index: int | None = None,
    ) -> _MessageTextView:
        """Handle bubble for chat window."""
        bg = '#3a3a5c' if role == 'user' else '#26263a'
        display_text = _truncate_for_display(text, _CHAT_RENDER_CHAR_LIMIT, "chat display")
        lbl = _MessageTextView(
            f"QTextBrowser {{ background: {bg}; color: {_TEXT}; border-radius: 8px;"
            f" padding: 8px 11px; font-size: 10pt; border: none; }}"
            f"QTextBrowser::selection {{ background: rgba(160,160,255,60); color: {_TEXT}; }}"
        )
        if role == "assistant":
            lbl.setHtml(_assistant_text_to_html(display_text))
        else:
            lbl.setPlainText(display_text)

        role_text = t("You" if role == "user" else "Assistant")
        stamp = _format_conversation_datetime(created_at)
        if stamp:
            role_text = f"{role_text} · {stamp}"
        role_lbl = QLabel(role_text)
        role_lbl.setStyleSheet(f"color: {_HINT}; background: transparent; font-size: 8pt;")
        role_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        if conversation_index is not None and message_index is not None:
            wrapper.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            wrapper.customContextMenuRequested.connect(
                lambda pos, w=wrapper, ci=conversation_index, mi=message_index: self._open_message_menu(
                    ci,
                    mi,
                    w,
                    pos,
                )
            )
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        hl.addWidget(role_lbl)
        if conversation_index is not None and message_index is not None:
            menu_btn = QPushButton("...")
            menu_btn.setFixedSize(28, 20)
            menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            menu_btn.setToolTip(t("Message options"))
            menu_btn.setAccessibleName(t("Message options"))
            menu_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_HINT}; border: none;"
                " font-size: 9pt; font-weight: 700; padding: 0; margin: 0; }"
                f"QPushButton:hover {{ background: rgba(255,255,255,12); color: {_TEXT}; }}"
            )
            menu_btn.clicked.connect(
                lambda _checked=False, button=menu_btn, ci=conversation_index, mi=message_index: self._open_message_menu(
                    ci,
                    mi,
                    button,
                )
            )
            hl.addWidget(menu_btn)
        wl.addWidget(header)

        if image_b64 and role == "user":
            try:
                import base64
                img_bytes = base64.b64decode(image_b64)
                pixmap = QPixmap()
                pixmap.loadFromData(img_bytes)
                if not pixmap.isNull():
                    thumb = pixmap.scaled(
                        280, 160,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    img_lbl = QLabel()
                    img_lbl.setPixmap(thumb)
                    img_lbl.setStyleSheet(
                        "QLabel { background: #3a3a5c; border-radius: 8px; padding: 4px; }"
                    )
                    img_lbl.setFixedSize(thumb.width() + 8, thumb.height() + 8)
                    wl.addWidget(img_lbl)
            except Exception:
                pass

        wl.addWidget(lbl)
        layout.insertWidget(layout.count() - 1, wrapper)  # before trailing stretch
        return lbl

    def _open_message_menu(
        self,
        conversation_index: int,
        message_index: int,
        anchor: QWidget | None = None,
        local_pos=None,
    ) -> None:
        """Open actions for one message bubble."""
        if self._streaming:
            return
        if not (0 <= conversation_index < len(self._conversations)):
            return
        messages = self._conversations[conversation_index].get("messages", [])
        if not (0 <= message_index < len(messages)):
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {_TITLE_BG}; color: {_TEXT};"
            f" border: 1px solid {_BORDER}; }}"
            f"QMenu::item:selected {{ background: {_SEL_BG}; }}"
        )
        menu.addAction(
            t("Branch from here"),
            lambda ci=conversation_index, mi=message_index: self._branch_from_message(ci, mi),
        )
        menu.addSeparator()
        menu.addAction(
            t("Rewind current chat to here"),
            lambda ci=conversation_index, mi=message_index: self._rewind_to_message(ci, mi),
        )
        if anchor is None:
            pos = self.mapToGlobal(self.rect().center())
        elif local_pos is not None:
            pos = anchor.mapToGlobal(local_pos)
        else:
            pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        menu.popup(pos)

    def _conversation_slice(self, conv: dict, message_index: int, *, new_id: bool) -> dict:
        """Copy a conversation through one message and rebuild hidden context."""
        retained = deepcopy((conv.get("messages") or [])[: message_index + 1])
        now = _now_iso()
        for msg in retained:
            if isinstance(msg, dict):
                _ensure_message_metadata(msg, fallback_created_at=conv.get("created_at") or now)
        retained_all = message_index == len(conv.get("messages", []) or []) - 1
        context = _context_from_messages(retained)
        file_context = _merge_file_context_from_messages(retained)
        tool_context = _latest_tool_context_from_messages(retained)
        if retained_all:
            context = context or str(conv.get("context") or "")
            file_context = file_context or _normalized_file_context(conv.get("file_context") or [])
            tool_context = tool_context or _normalized_tool_context(conv.get("tool_context") or {})
        sliced = {
            "id": str(uuid.uuid4()) if new_id else (conv.get("id") or str(uuid.uuid4())),
            "project_id": conv.get("project_id") or _GENERAL_PROJECT_ID,
            "messages": retained,
            "context": context,
            "file_context": file_context,
            "tool_context": tool_context,
            "context_policy": _normalized_context_policy(conv.get("context_policy") or {}),
            "created_at": conv.get("created_at") or now,
            "updated_at": now,
        }
        return sliced

    def _branch_from_message(self, conversation_index: int, message_index: int) -> None:
        """Create and select a non-destructive branch ending at a message."""
        if self._streaming or not (0 <= conversation_index < len(self._conversations)):
            return
        conv = self._conversations[conversation_index]
        if not (0 <= message_index < len(conv.get("messages", []))):
            return
        branch = self._conversation_slice(conv, message_index, new_id=True)
        self._conversations.append(branch)
        if self._has_placeholder:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._has_placeholder = False
        idx = len(self._conversations) - 1
        self._stack.addWidget(self._make_page(idx, branch))
        self._input_frame.setEnabled(True)
        self._rebuild_sidebar()
        self._switch(idx)
        self._persist()

    def _rewind_to_message(self, conversation_index: int, message_index: int) -> None:
        """Destructively truncate the active conversation after confirmation."""
        if self._streaming or conversation_index != self._active_idx:
            return
        if not (0 <= conversation_index < len(self._conversations)):
            return
        conv = self._conversations[conversation_index]
        messages = conv.get("messages", [])
        if not (0 <= message_index < len(messages)) or message_index == len(messages) - 1:
            return
        if QMessageBox.question(
            self,
            t("Rewind conversation"),
            t("Remove all messages after this one? This cannot be undone."),
        ) != QMessageBox.StandardButton.Yes:
            return
        sliced = self._conversation_slice(conv, message_index, new_id=False)
        conv.clear()
        conv.update(sliced)
        self._built_pages.discard(conversation_index)
        self._rebuild_sidebar()
        self._switch(conversation_index)
        self._persist()

    def _active_layout(self):
        """Handle active layout for chat window."""
        active_idx = self._active_idx
        if active_idx < 0 or active_idx >= self._stack.count():
            return None
        page = self._stack.widget(active_idx)
        return getattr(page, "_msg_layout", None)

    def _active_scroll(self) -> QScrollArea | None:
        """Handle active scroll for chat window."""
        active_idx = self._active_idx
        if active_idx < 0 or active_idx >= self._stack.count():
            return None
        page = self._stack.widget(active_idx)
        return page if isinstance(page, QScrollArea) else None

    def _scroll_bottom(self):
        """Handle scroll bottom for chat window."""
        scroll = self._active_scroll()
        if scroll:
            QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(
                scroll.verticalScrollBar().maximum()
            ))

    # ------------------------------------------------------------------ Drops

    def dragEnterEvent(self, event):  # noqa: N802
        """Accept file/text/image drops as pending message attachments."""
        mime = event.mimeData()
        if mime and (mime.hasUrls() or mime.hasText() or mime.hasImage()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):  # noqa: N802
        """Attach dropped files, text, or images to the next chat message."""
        if self._add_attachments_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _choose_attachments(self) -> None:
        """Open a picker and attach selected files to the next outgoing turn."""
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            t("Add files or images"),
            "",
            (
                f"{t('Supported files')} (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.tiff *.tif "
                "*.txt *.md *.py *.js *.ts *.json *.yaml *.yml *.csv *.html *.css *.xml "
                "*.docx *.pdf *.xlsx *.xls *.pptx *.odt *.ods *.odp);;"
                f"{t('All files')} (*)"
            ),
        )
        self._add_attachment_paths(paths)

    def _add_attachment_paths(self, paths: list[str]) -> bool:
        """Attach local files by reusing the drag/drop MIME extraction path."""
        local_paths = [str(path or "").strip() for path in paths or [] if str(path or "").strip()]
        if not local_paths:
            return False
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in local_paths])
        return self._add_attachments_from_mime(mime)

    def _add_attachments_from_mime(self, mime) -> bool:
        """Convert dropped MIME data into next-message context/image attachments."""
        external_names: set[str] = set()
        for ref in self._attachment_refs_from_mime(mime):
            self._add_pending_attachment_ref(ref)
            external_names.add(str(ref.get("name") or ""))
        try:
            from ui.drop_zone import process_drop_mime
            raw_items = process_drop_mime(mime)
        except Exception:
            raw_items = []
        return self._add_attachment_items(raw_items, external_names=external_names) or bool(external_names)

    def _attachment_refs_from_mime(self, mime) -> list[dict]:
        """Return path-only refs for local files in a drag/drop or picker MIME payload."""
        refs: list[dict] = []
        try:
            urls = mime.urls() if mime and mime.hasUrls() else []
        except Exception:
            urls = []
        for url in urls:
            try:
                if not url.isLocalFile():
                    continue
                path = str(url.toLocalFile() or "").strip()
            except Exception:
                path = ""
            if not path:
                continue
            ref = _conversation_store.external_file_attachment(path)
            if not any(existing.get("path") == ref.get("path") for existing in refs):
                refs.append(ref)
        return refs

    def _add_pending_attachment_ref(self, ref: dict) -> None:
        """Queue one attachment reference for the next outgoing message."""
        normalized = _conversation_store.normalize_attachments([ref])
        if not normalized:
            return
        item = normalized[0]
        if not any(existing.get("path") == item.get("path") for existing in self._pending_attachments):
            self._pending_attachments.append(item)
        label = str(item.get("name") or item.get("path") or "Attachment")
        if label and label not in self._pending_attachment_labels:
            self._pending_attachment_labels.append(label)

    def _add_attachment_items(
        self,
        raw_items: list[tuple[str, str, str]],
        *,
        external_names: set[str] | None = None,
    ) -> bool:
        """Attach normalized drop-zone items to the next outgoing chat turn."""
        if not raw_items:
            return False
        external_names = external_names or set()
        image_labels: list[str] = []
        context_items: list[tuple[str, str, str]] = []
        fallback_lines: list[str] = []
        for name, content, item_type in raw_items:
            label = str(name or "Attachment")
            kind = str(item_type or "text")
            if label in external_names:
                continue
            if kind == "image" and self._pending_attachment_image_b64 is None:
                self._pending_attachment_image_b64 = str(content or "")
                image_labels.append(label)
            elif kind == "image":
                fallback_lines.append(f"[Attached image: {label}]")
            else:
                context_items.append((label, str(content or ""), kind))

        context = self._attachment_context_from_items(context_items)
        parts = [
            part
            for part in (self._pending_attachment_context, context, "\n".join(fallback_lines))
            if part.strip()
        ]
        self._pending_attachment_context = "\n\n".join(parts)
        if len(self._pending_attachment_context) > _ATTACHMENT_CONTEXT_CHAR_LIMIT:
            self._pending_attachment_context = (
                self._pending_attachment_context[:_ATTACHMENT_CONTEXT_CHAR_LIMIT].rstrip()
                + "\n[attached context truncated]"
            )

        labels = image_labels + [name for name, _content, _kind in context_items]
        for label in labels:
            if label and label not in self._pending_attachment_labels:
                self._pending_attachment_labels.append(label)
        self._refresh_attachment_label()
        return bool(labels or fallback_lines)

    def _attachment_context_from_items(self, items: list[tuple[str, str, str]]) -> str:
        """Render dropped text/document items as model-visible context."""
        if not items:
            return ""
        try:
            from core.query_pipeline import ContextInputs, build_context
            built = build_context(ContextInputs(intent_prompt="", drop_items=items))
            return str(built.ambient_ctx or "").strip()
        except Exception:
            lines = []
            for name, content, _kind in items:
                text = str(content or "").strip()
                if text:
                    lines.append(f"[{name}]\n{text}")
            return "\n\n".join(lines)

    def _refresh_attachment_label(self) -> None:
        """Update the pending attachment chip above the composer."""
        if self._attachment_label is None:
            return
        if not self._pending_attachment_labels:
            self._attachment_label.setVisible(False)
            self._attachment_label.setText("")
            self._attachment_label.setToolTip("")
            return
        names = ", ".join(self._pending_attachment_labels[:4])
        if len(self._pending_attachment_labels) > 4:
            names += f", +{len(self._pending_attachment_labels) - 4}"
        self._attachment_label.setText(f"{t('Attached')} · {html.escape(names)}")
        self._attachment_label.setToolTip("\n".join(self._pending_attachment_labels))
        self._attachment_label.setVisible(True)

    def _consume_pending_attachments(self) -> tuple[str, str | None, list[str], list[dict]]:
        """Return and clear pending context/image attachments."""
        context = self._pending_attachment_context
        image = self._pending_attachment_image_b64
        labels = list(self._pending_attachment_labels)
        attachments = list(self._pending_attachments)
        self._pending_attachment_context = ""
        self._pending_attachment_image_b64 = None
        self._pending_attachments = []
        self._pending_attachment_labels = []
        self._refresh_attachment_label()
        return context, image, labels, attachments

    # ------------------------------------------------------------------ Sending

    def _on_send_clicked(self):
        """Handle send clicked events."""
        text = self._input.toPlainText().strip()
        if not text and self._pending_attachment_labels:
            text = t("Please review the attached file.")
        if text and not self._streaming:
            self._input.clear()
            self._send(text)

    def _send(self, text: str):
        """Send the chat window workflow."""
        if self._streaming or not self._conversations:
            return
        self._streaming = True
        self._send_btn.setEnabled(False)
        self._new_chat_btn.setEnabled(False)

        conv = self._conversations[self._active_idx]
        _ensure_conversation_metadata(conv)
        attachment_context, attachment_image, attachment_labels, attachment_refs = self._consume_pending_attachments()
        now = _touch_conversation(conv)
        user_message = {"role": "user", "content": text, "created_at": now}
        _ensure_message_metadata(user_message, fallback_created_at=now)
        if attachment_image:
            try:
                attachment_refs.append(
                    _conversation_store.save_image_attachment(
                        attachment_image,
                        conversation_id=str(conv.get("id") or ""),
                        message_id=str(user_message.get("id") or ""),
                        source="pasted_image",
                        name=(attachment_labels[0] if attachment_labels else "image.png"),
                    )
                )
            except Exception:
                # Keep the image available for this one live request, but do not
                # allow the base64 blob into persisted conversation history.
                user_message["image_base64"] = attachment_image
        attachments = _conversation_store.normalize_attachments(attachment_refs)
        if attachments:
            user_message["attachments"] = attachments
        if attachment_context:
            label = ", ".join(attachment_labels) if attachment_labels else t("Attachments")
            attachment_context_block = f"[{t('Attached')} · {label}]\n{attachment_context.strip()}"
        else:
            attachment_context_block = ""
        attachment_summary = _attachment_summary_context(attachments)
        message_context = "\n\n".join(
            part for part in (attachment_context_block, attachment_summary) if part.strip()
        )
        if message_context:
            user_message["context"] = message_context
        context_policy = _ensure_conversation_context_policy(conv)

        layout = self._active_layout()
        if layout:
            self._bubble(
                layout,
                text,
                "user",
                _conversation_store.first_image_base64_from_message(user_message),
                created_at=now,
                conversation_index=self._active_idx,
                message_index=len(conv["messages"]),
            )
            msg_hint = self._message_context_hint(message_context)
            if msg_hint is not None:
                layout.insertWidget(layout.count() - 1, msg_hint)
        conv["messages"].append(user_message)

        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_parser = ThoughtStreamParser()
        self._current_file_context = []
        self._current_tool_context = {}
        self._current_ai_label = self._bubble(layout, "...", "assistant", created_at=_now_iso()) if layout else None
        self._scroll_bottom()

        # Keep legacy/global context in the system prompt, while message-scoped
        # attachments ride next to the user turns that mention them.
        ctx = _context_not_anchored_to_messages(conv.get("context", ""), conv["messages"])
        sys_content = config.get_system_prompt()
        if ctx:
            sys_content += f"\n\n---\n{ctx}"
        file_ctx = _file_context_text(conv.get("file_context") or [])
        if file_ctx:
            sys_content += f"\n\n---\n{file_ctx}"
        messages = [{"role": "system", "content": sys_content}] + _chat_model_messages(conv["messages"])

        def _stream():
            """Stream the chat window workflow."""
            try:
                kwargs = {"context_policy": dict(context_policy)}
                try:
                    signature = inspect.signature(self._send_fn)
                    accepts_policy = (
                        "context_policy" in signature.parameters
                        or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
                    )
                except (TypeError, ValueError):
                    accepts_policy = False
                source = self._send_fn(messages, **kwargs) if accepts_policy else self._send_fn(messages)
                for item in source:
                    if isinstance(item, dict) and item.get("type") == "final":
                        self._signals.final.emit(str(item.get("text") or ""))
                    elif isinstance(item, dict) and item.get("type") == "metadata":
                        self._signals.metadata.emit(item)
                    elif isinstance(item, dict) and item.get("type") == "chunk":
                        self._signals.chunk.emit(item)
                    else:
                        self._signals.chunk.emit(str(item or ""))
            finally:
                self._signals.finished.emit()

        threading.Thread(target=_stream, daemon=True).start()

    def _on_chunk(self, chunk: object):
        """Handle chunk events."""
        if isinstance(chunk, dict):
            text = str(chunk.get("text") or "")
            if bool(chunk.get("is_thought")):
                _merge_display_segments(self._current_ai_segments, text, True)
                if self._current_ai_label:
                    self._current_ai_label.setHtml(
                        _assistant_segments_to_html(_truncate_segments_for_display(self._current_ai_segments))
                    )
                self._scroll_bottom()
                return
            chunk = text
        chunk = str(chunk or "")
        self._current_ai_text += chunk
        if self._current_ai_parser is None:
            self._current_ai_parser = ThoughtStreamParser()
        for text, is_thought in self._current_ai_parser.feed(chunk):
            _merge_display_segments(self._current_ai_segments, text, is_thought)
            if not is_thought:
                self._current_ai_reply_text += text
        if self._current_ai_label:
            self._current_ai_label.setHtml(
                _assistant_segments_to_html(_truncate_segments_for_display(self._current_ai_segments))
            )
        self._scroll_bottom()

    def _on_final_text(self, text: str):
        """Replace the streamed draft with the final assistant text."""
        if not text or text == self._current_ai_text:
            return
        self._current_ai_text = text
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_parser = ThoughtStreamParser()
        for segment, is_thought in self._current_ai_parser.feed(text):
            _merge_display_segments(self._current_ai_segments, segment, is_thought)
            if not is_thought:
                self._current_ai_reply_text += segment
        flushed = self._current_ai_parser.finish()
        self._current_ai_segments = merge_segment_iterables(self._current_ai_segments, flushed)
        for segment, is_thought in flushed:
            if not is_thought:
                self._current_ai_reply_text += segment
        self._current_ai_parser = None
        if self._current_ai_label:
            self._current_ai_label.setHtml(
                _assistant_segments_to_html(_truncate_segments_for_display(self._current_ai_segments))
            )
        self._scroll_bottom()

    def _on_metadata(self, item: object):
        """Capture display-hidden metadata returned with the reply."""
        if isinstance(item, dict):
            self._current_file_context = _normalized_file_context(item.get("file_context") or [])
            self._current_tool_context = _normalized_tool_context(item.get("tool_context") or {})

    def _on_finished(self):
        """Handle finished events."""
        if self._current_ai_parser is not None:
            flushed = self._current_ai_parser.finish()
            self._current_ai_segments = merge_segment_iterables(self._current_ai_segments, flushed)
            for text, is_thought in flushed:
                if not is_thought:
                    self._current_ai_reply_text += text
            if self._current_ai_label:
                self._current_ai_label.setHtml(
                    _assistant_segments_to_html(_truncate_segments_for_display(self._current_ai_segments))
                )
        if self._current_ai_reply_text and self._conversations and 0 <= self._active_idx < len(self._conversations):
            conv = self._conversations[self._active_idx]
            stamp = _touch_conversation(conv)
            message = {"role": "assistant", "content": self._current_ai_reply_text, "created_at": stamp}
            _ensure_message_metadata(message, fallback_created_at=stamp)
            if self._current_ai_text != self._current_ai_reply_text:
                message["display_content"] = self._current_ai_text
            if self._current_file_context:
                message["file_context"] = self._current_file_context
            if self._current_tool_context:
                message["tool_context"] = self._current_tool_context
            conv["messages"].append(message)
            _merge_file_context(conv, self._current_file_context)
            _merge_tool_context(conv, self._current_tool_context)
            if self._persist_fn:
                try:
                    self._persist_fn()
                except Exception:
                    pass
        self._current_ai_label = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_parser = None
        self._current_file_context = []
        self._current_tool_context = {}
        self._streaming = False
        self._send_btn.setEnabled(True)
        self._new_chat_btn.setEnabled(True)

    def update_live_highlight(self, reply_text: str, revealed_count: int, finished: bool):
        """Optionally mirror a read-position without wiring bubble/TTS events here."""
        if not self._conversations:
            return
        last_idx = len(self._conversations) - 1
        if last_idx >= self._stack.count():
            return  # page not built yet (ingest pending)
        page = self._stack.widget(last_idx)
        view = getattr(page, "_last_assistant_view", None)
        if view is None:
            return
        display_text = _truncate_for_display(reply_text, _CHAT_RENDER_CHAR_LIMIT, "chat display")
        if finished:
            # Flash all bold words highlighted, then revert to the normal colour.
            view.setHtml(_assistant_text_to_html(display_text, None))
            QTimer.singleShot(
                _REVERT_DELAY_MS,
                lambda v=view, s=display_text: self._revert_highlight(v, s),
            )
        else:
            view.setHtml(_assistant_text_to_html(display_text, max(0, revealed_count)))
        if last_idx == self._active_idx:
            self._scroll_bottom()

    @staticmethod
    def _revert_highlight(view: _MessageTextView, source: str):
        """Re-render a finished reply with no highlight (bold words back to normal)."""
        try:
            view.setHtml(_assistant_text_to_html(source, 0))
        except RuntimeError:
            pass  # the view (or its window) was destroyed before the timer fired

    # ------------------------------------------------------------------ Events

    def eventFilter(self, obj, event):
        """Handle event filter for chat window."""
        from PySide6.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                self._on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ Helpers

    def _center_on_screen(self):
        """Handle center on screen for chat window."""
        fit_window_to_screen(self, preferred_width=_W, preferred_height=_H)

    def showEvent(self, event):  # noqa: N802
        """Show event."""
        super().showEvent(event)
        self._center_on_screen()
