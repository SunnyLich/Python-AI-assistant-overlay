"""
ui/chat_window.py -” Multi-turn chat window with conversation history sidebar.

Left sidebar lists all past conversations; clicking one selects it so you can
continue that thread.

Send message: Enter (Shift+Enter for newline).
"""
from __future__ import annotations
import html
import re
import threading
import uuid
import config
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QTextEdit, QPushButton, QFrame, QApplication, QTextBrowser,
    QSizePolicy, QStackedWidget, QSplitter, QComboBox, QInputDialog,
    QMenu, QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QKeySequence,
)
from core.assistant_text import ThoughtStreamParser, merge_segment_iterables, split_tagged_text
from core.conversation_store.store import GENERAL_PROJECT_ID as _GENERAL_PROJECT_ID
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
_SIDEBAR_MENU_W = 32
_SIDEBAR_FADE_W = 34


def _truncate_for_display(text: str, limit: int, label: str = "display") -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    hidden = len(text) - limit
    return text[:limit].rstrip() + f"\n\n[{label} truncated; {hidden} chars hidden]"


def _truncate_segments_for_display(
    segments: list[tuple[str, bool]],
    limit: int = _CHAT_RENDER_CHAR_LIMIT,
) -> list[tuple[str, bool]]:
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
    chunk     = Signal(str)
    finished  = Signal()


class _MessageTextView(QTextBrowser):
    def __init__(self, style_sheet: str):
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
        doc_h = self.document().documentLayout().documentSize().height()
        margin = self.contentsMargins().top() + self.contentsMargins().bottom()
        self.setFixedHeight(max(38, int(doc_h + margin + 6)))

    def showEvent(self, event):
        super().showEvent(event)
        # Document layout hasn't run before first show — recompute height now.
        QTimer.singleShot(0, self._sync_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._sync_height()


class _ConversationTitleButton(QPushButton):
    """Paints a sidebar title with a right-edge fade under the overlaid menu."""

    def __init__(self, title: str, *, active: bool, latest: bool) -> None:
        super().__init__("")
        self._title = title
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
        self._active = active
        self._latest = latest
        self.setChecked(active)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        bg = QColor(160, 160, 255, 18) if self._active or self.isChecked() else QColor(0, 0, 0, 0)
        if self.underMouse() and not (self._active or self.isChecked()):
            bg = QColor(255, 255, 255, 10)
        if bg.alpha():
            painter.fillRect(rect, bg)

        text_rect = rect.adjusted(10, 0, -(_SIDEBAR_MENU_W + 10), 0)
        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        color = QColor(_ACCENT if self._latest else _TEXT)
        painter.setPen(QPen(color))

        metrics = QFontMetrics(font)
        available = max(0, text_rect.width() - _SIDEBAR_FADE_W)
        title = metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, available)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title,
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
        super().__init__()
        self.setFixedHeight(52)
        self.setStyleSheet("background: transparent;")
        self._title_btn = title_btn
        self._menu_btn = menu_btn
        self._title_btn.setParent(self)
        self._menu_btn.setParent(self)
        self._layout_children()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self) -> None:
        self._title_btn.setGeometry(self.rect())
        self._menu_btn.setGeometry(
            max(0, self.width() - _SIDEBAR_MENU_W - 4),
            0,
            _SIDEBAR_MENU_W,
            self.height(),
        )
        self._menu_btn.raise_()


def _merge_display_segments(segments: list[tuple[str, bool]], text: str, is_thought: bool) -> list[tuple[str, bool]]:
    if not text:
        return segments
    if segments and segments[-1][1] == is_thought:
        segments[-1] = (segments[-1][0] + text, is_thought)
    else:
        segments.append((text, is_thought))
    return segments


def _segment_text_to_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


_WS_RE = re.compile(r"(\s+)")


def _accent_color() -> str:
    """The same colour the speech bubble uses to highlight TTS-read words."""
    return getattr(config, "BUBBLE_READ_WORD_COLOR", "#4da3ff") or "#4da3ff"


def _reply_html(text: str, start_idx: int, read_count: int | None) -> tuple[int, str]:
    """Render a reply (non-thought) segment.

    Parses ``**``/``__`` markdown bold the same way the speech bubble does. Bold
    words are accent-coloured only while the TTS read-position is sweeping over
    them; otherwise they stay bold in the normal text colour. Every whitespace-
    delimited word advances a running index so it lines up with the bubble's read
    position. ``read_count`` meanings:

    * ``0``    — no bold words coloured (resting / history / after the highlight
                 has reverted). This is the default.
    * ``N>0``  — bold words whose index < N are coloured (live read-position).
    * ``None`` — all bold words coloured (the brief "fully read" flash before the
                 colour reverts).

    Returns ``(next_idx, html)``.
    """
    accent = _accent_color()
    parts: list[str] = []
    idx = start_idx

    def flush(segment: str, is_bold: bool) -> None:
        nonlocal idx
        for piece in _WS_RE.split(segment):
            if not piece:
                continue
            if piece.isspace():
                parts.append(piece.replace("\n", "<br>"))
                continue
            if is_bold:
                read = read_count is None or idx < read_count
                style = "font-weight:bold;"
                if read:
                    style += f"color:{accent};"
                parts.append(f'<span style="{style}">{html.escape(piece)}</span>')
            else:
                parts.append(html.escape(piece))
            idx += 1

    bold = False
    buf = ""
    i = 0
    while i < len(text):
        if text.startswith("**", i) or text.startswith("__", i):
            flush(buf, bold)
            buf = ""
            bold = not bold
            i += 2
            continue
        buf += text[i]
        i += 1
    flush(buf, bold)
    return idx, "".join(parts)


def _assistant_segments_to_html(
    segments: list[tuple[str, bool]], read_count: int | None = 0
) -> str:
    parts: list[str] = []
    idx = 0
    prev_is_thought: bool | None = None
    for text, is_thought in segments:
        if is_thought:
            parts.append(f'<span style="color: #8f8f9e;">{_segment_text_to_html(text)}</span>')
        else:
            # Separate the model's thinking from its reply with a line break.
            if prev_is_thought:
                parts.append("<br>")
            idx, body = _reply_html(text, idx, read_count)
            parts.append(body)
        prev_is_thought = is_thought
    return "".join(parts)


def _assistant_text_to_html(text: str, read_count: int | None = 0) -> str:
    return _assistant_segments_to_html(split_tagged_text(text), read_count)


class ChatWindow(QWidget):
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
            send_fn:       Callable(messages: list) -> Generator[str]
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
        self._conversation_menu: QMenu | None = None
        if active_idx is not None and 0 <= active_idx < len(conversations):
            self._active_idx = active_idx
        else:
            self._active_idx = max(0, len(conversations) - 1)
        self._built_pages: set[int] = set()

        self._signals = _StreamSignals()
        self._signals.chunk.connect(self._on_chunk)
        self._signals.finished.connect(self._on_finished)

        self.setWindowTitle(t("Chat"))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
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
        elif auto_message and conversations:
            QTimer.singleShot(120, lambda: self._send(auto_message))

    # ------------------------------------------------------------------ Build

    def _build_ui(self):
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
        override = str(conv.get("title_override") or "").strip()
        if override:
            return override
        first_user = next((m for m in conv["messages"] if m["role"] == "user"), None)
        raw = first_user["content"] if first_user else f"{t('Conversation')} {idx+1}"
        has_image = bool(first_user and first_user.get("image_base64"))
        prefix = f"[{t('image')}] " if has_image else ""
        return prefix + str(raw).strip().replace("\n", " ")

    def _make_sidebar_row(self, idx: int, conv: dict) -> tuple[QWidget, QPushButton]:
        title = self._conversation_title(idx, conv)
        if conv.get("pinned"):
            title = "📌 " + title
        is_latest = (idx == len(self._conversations) - 1)
        is_active = (idx == self._active_idx)

        btn = _ConversationTitleButton(title, active=is_active, latest=is_latest)
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
        if not (0 <= idx < len(self._conversations)):
            return
        conv = self._conversations[idx]
        conv["pinned"] = not conv.get("pinned")
        self._rebuild_sidebar()
        self._persist()

    def _rename_conversation(self, idx: int) -> None:
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
        self._rebuild_sidebar()
        self._persist()

    def _assign_project(self, idx: int, project_id: str) -> None:
        if not (0 <= idx < len(self._conversations)):
            return
        self._conversations[idx]["project_id"] = project_id
        self._rebuild_sidebar()
        self._persist()

    def _delete_conversation(self, idx: int) -> None:
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
        if self._persist_fn:
            try:
                self._persist_fn()
            except Exception:
                pass

    def _btn_style(self, active: bool, latest: bool) -> str:
        bg = _SEL_BG if active else "transparent"
        c  = _ACCENT if latest else _TEXT
        return (
            f"QPushButton {{ background: {bg}; color: {c}; border: none;"
            f" text-align: left; padding: 6px 10px; font-size: 9pt; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,10); }}"
            f"QPushButton:checked {{ background: {_SEL_BG}; }}"
        )

    def _switch(self, idx: int):
        self._active_idx = idx
        if idx < self._stack.count():
            self._ensure_page_built(idx)
            self._stack.setCurrentIndex(idx)
        self._past_notice.setVisible(False)
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
        if self._streaming:
            return

        was_empty = not self._conversations
        conv = {
            "id": str(uuid.uuid4()),
            "project_id": self._active_project_id,
            "messages": [],
            "context": "",
        }
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
        ph = QLabel(t("Loading conversation..."))
        ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
        return ph

    def _ensure_page_built(self, idx: int) -> None:
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

        hint = self._context_hint(conv.get("context", ""))
        if hint is not None:
            layout.insertWidget(0, hint)  # sits above the first message

        last_ai: _MessageTextView | None = None
        for msg in conv["messages"]:
            display_text = msg.get("display_content", msg["content"])
            view = self._bubble(layout, display_text, msg["role"], msg.get("image_base64"))
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
        body = f"{t('Context')} · {html.escape(preview)}"
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

    def _make_input_area(self) -> QWidget:
        frame = QWidget()
        frame.setStyleSheet(f"background: {_TITLE_BG}; border-top: 1px solid {_BORDER};")
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(8)

        self._input = QTextEdit()
        self._input.setFixedHeight(62)
        self._input.setPlaceholderText(t("Message... (Enter to send, Shift+Enter for newline)"))
        self._input.setStyleSheet(
            f"QTextEdit {{ background: rgba(255,255,255,8); border: 1px solid {_BORDER};"
            f" border-radius: 6px; color: {_TEXT}; padding: 6px 8px; font-size: 10pt; }}"
        )
        self._input.installEventFilter(self)

        self._send_btn = QPushButton(t("Send"))
        self._send_btn.setFixedSize(64, 46)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: #1c1c24; border: none;"
            f" border-radius: 6px; font-size: 10pt; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #b8b8ff; }}"
            f"QPushButton:disabled {{ background: #444; color: #666; }}"
        )
        self._send_btn.clicked.connect(self._on_send_clicked)
        h.addWidget(self._input)
        h.addWidget(self._send_btn)
        return frame

    # ------------------------------------------------------------------ Bubbles

    def _bubble(self, layout, text: str, role: str, image_b64: str | None = None) -> _MessageTextView:
        bg = '#3a3a5c' if role == 'user' else '#26263a'
        display_text = _truncate_for_display(text, _CHAT_RENDER_CHAR_LIMIT, "chat display")
        lbl = _MessageTextView(
            f"QTextBrowser {{ background: {bg}; color: {_TEXT}; border-radius: 8px;"
            f" padding: 8px 11px; font-size: 10pt; border: none; }}"
            f"QTextBrowser::selection {{ background: rgba(160,160,255,60); color: {_TEXT}; }}"
        )
        if role == "assistant":
            lbl._assistant_source = display_text  # type: ignore[attr-defined]  used by live highlight
            lbl.setHtml(_assistant_text_to_html(display_text))
        else:
            lbl.setPlainText(display_text)

        role_lbl = QLabel(t("You" if role == "user" else "Assistant"))
        role_lbl.setStyleSheet(f"color: {_HINT}; background: transparent; font-size: 8pt;")

        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(2)
        wl.addWidget(role_lbl)

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

    def _active_layout(self):
        active_idx = self._active_idx
        if active_idx < 0 or active_idx >= self._stack.count():
            return None
        page = self._stack.widget(active_idx)
        return getattr(page, "_msg_layout", None)

    def _active_scroll(self) -> QScrollArea | None:
        active_idx = self._active_idx
        if active_idx < 0 or active_idx >= self._stack.count():
            return None
        page = self._stack.widget(active_idx)
        return page if isinstance(page, QScrollArea) else None

    def _scroll_bottom(self):
        scroll = self._active_scroll()
        if scroll:
            QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(
                scroll.verticalScrollBar().maximum()
            ))

    # ------------------------------------------------------------------ Sending

    def _on_send_clicked(self):
        text = self._input.toPlainText().strip()
        if text and not self._streaming:
            self._input.clear()
            self._send(text)

    def _send(self, text: str):
        if self._streaming or not self._conversations:
            return
        self._streaming = True
        self._send_btn.setEnabled(False)
        self._new_chat_btn.setEnabled(False)

        conv = self._conversations[self._active_idx]

        layout = self._active_layout()
        if layout:
            self._bubble(layout, text, "user")
        conv["messages"].append({"role": "user", "content": text})

        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_parser = ThoughtStreamParser()
        self._current_ai_label = self._bubble(layout, "...", "assistant") if layout else None
        self._scroll_bottom()

        # Inject stored context into system prompt so it is available for every
        # follow-up without being repeated inside the conversation turns.
        ctx = conv.get("context", "")
        sys_content = config.get_system_prompt()
        if ctx:
            sys_content += f"\n\n---\n{ctx}"
        messages = [{"role": "system", "content": sys_content}] + conv["messages"]

        def _stream():
            try:
                for chunk in self._send_fn(messages):
                    self._signals.chunk.emit(chunk)
            finally:
                self._signals.finished.emit()

        threading.Thread(target=_stream, daemon=True).start()

    def _on_chunk(self, chunk: str):
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

    def _on_finished(self):
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
            message = {"role": "assistant", "content": self._current_ai_reply_text}
            if self._current_ai_text != self._current_ai_reply_text:
                message["display_content"] = self._current_ai_text
            self._conversations[self._active_idx]["messages"].append(message)
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
        self._streaming = False
        self._send_btn.setEnabled(True)
        self._new_chat_btn.setEnabled(True)

    def update_live_highlight(self, reply_text: str, revealed_count: int, finished: bool):
        """Mirror the speech bubble's TTS read-position onto the latest reply.

        The voice reply is the last assistant message of the newest conversation.
        The full streamed text is shown as soon as it arrives; while audio plays
        we re-render so its bold words light up (accent colour) up to the spoken
        word. When playback finishes every bold word is highlighted briefly, then
        the colour reverts to normal a few seconds later. Works whether the chat
        was already open (the page was ingested) or opened mid-reply.
        """
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
        view._assistant_source = display_text  # type: ignore[attr-defined]
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
    def _revert_highlight(view: "_MessageTextView", source: str):
        """Re-render a finished reply with no highlight (bold words back to normal)."""
        try:
            view.setHtml(_assistant_text_to_html(source, 0))
        except RuntimeError:
            pass  # the view (or its window) was destroyed before the timer fired

    # ------------------------------------------------------------------ Events

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
                self._on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ Helpers

    def _center_on_screen(self):
        fit_window_to_screen(self, preferred_width=_W, preferred_height=_H)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._center_on_screen()

