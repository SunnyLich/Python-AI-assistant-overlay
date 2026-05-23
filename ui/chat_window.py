"""
ui/chat_window.py — Multi-turn chat window with conversation history sidebar.

Left sidebar lists all past conversations; clicking one selects it so you can
continue that thread.

Send message: Enter (Shift+Enter for newline).
"""
from __future__ import annotations
import html
import threading
import config
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QTextEdit, QPushButton, QFrame, QApplication, QTextBrowser,
    QSizePolicy, QStackedWidget, QSplitter,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont, QPixmap, QShortcut, QKeySequence
from core.assistant_text import ThoughtStreamParser, merge_segment_iterables, split_tagged_text
from ui.window_utils import enable_standard_window_controls, fit_window_to_screen

_W          = 680
_H          = 520
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


class _StreamSignals(QObject):
    chunk     = pyqtSignal(str)
    finished  = pyqtSignal()


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


def _assistant_segments_to_html(segments: list[tuple[str, bool]]) -> str:
    parts: list[str] = []
    for text, is_thought in segments:
        body = _segment_text_to_html(text)
        if is_thought:
            parts.append(f'<span style="color: #8f8f9e;">{body}</span>')
        else:
            parts.append(body)
    return "".join(parts)


def _assistant_text_to_html(text: str) -> str:
    return _assistant_segments_to_html(split_tagged_text(text))


class ChatWindow(QWidget):
    def __init__(
        self,
        conversations: list[list[dict]],
        send_fn,
        auto_message: str | None = None,
        start_new: bool = False,
    ):
        """
        Args:
            conversations: Direct reference to the app's list of all past
                           conversations. Each item is a dict with keys
                           ``"messages"`` (list of role/content turns) and
                           ``"context"`` (ambient context string).
            send_fn:       Callable(messages: list) -> Generator[str]
            auto_message:  If set, automatically sent when the window opens.
        """
        super().__init__()
        self._conversations = conversations  # live reference — NOT a copy
        self._send_fn = send_fn
        self._streaming = False
        self._current_ai_label: _MessageTextView | None = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments: list[tuple[str, bool]] = []
        self._current_ai_parser: ThoughtStreamParser | None = None
        self._active_idx = max(0, len(conversations) - 1)

        self._signals = _StreamSignals()
        self._signals.chunk.connect(self._on_chunk)
        self._signals.finished.connect(self._on_finished)

        self.setWindowTitle("Chat")
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
        title = QLabel("Chat")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_ACCENT}; background: transparent;")
        new_chat = QPushButton("New")
        new_chat.setFixedSize(52, 26)
        new_chat.setToolTip("Start a new conversation (Ctrl+N)")
        new_chat.setStyleSheet(
            f"QPushButton {{ background: rgba(160,160,255,18); color: {_ACCENT};"
            f" border: 1px solid {_BORDER}; border-radius: 6px; font-size: 9pt; }}"
            "QPushButton:hover { background: rgba(160,160,255,28); }"
            "QPushButton:disabled { color: #666; border-color: rgba(255,255,255,10); }"
        )
        new_chat.clicked.connect(self.start_new_conversation)
        self._new_chat_btn = new_chat
        close = QPushButton("✕")
        close.setFixedSize(26, 26)
        close.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; font-size: 13px; }"
            "QPushButton:hover { color: #e66; }"
        )
        close.clicked.connect(self.close)
        h.addWidget(title)
        h.addStretch()
        h.addWidget(new_chat)
        h.addWidget(close)
        return bar

    # ------------------------------------------------------------------ Sidebar

    def _make_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setMinimumWidth(100)
        sidebar.setStyleSheet(f"background: {_SIDEBAR_BG};")
        vl = QVBoxLayout(sidebar)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        hdr = QLabel("  History")
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
            lbl = QLabel("  No history yet.")
            lbl.setStyleSheet(
                f"color: {_HINT}; font-size: 9pt; padding: 8px; background: transparent;"
            )
            self._sidebar_layout.addWidget(lbl)
        else:
            # Newest conversation at the top
            for i, conv in enumerate(reversed(self._conversations)):
                real_idx = len(self._conversations) - 1 - i
                btn = self._make_sidebar_btn(real_idx, conv)
                self._sidebar_layout.addWidget(btn)
                self._sidebar_btns.append((real_idx, btn))
        self._sidebar_layout.addStretch()

    def _make_sidebar_btn(self, idx: int, conv: dict) -> QPushButton:
        first_user = next((m for m in conv["messages"] if m["role"] == "user"), None)
        raw = first_user["content"] if first_user else f"Conversation {idx+1}"
        has_image = bool(first_user and first_user.get("image_base64"))
        prefix = "📷 " if has_image else ""
        title = (prefix + raw.strip().replace("\n", " "))
        if len(title) > 42:
            title = title[:42] + "…"
        is_latest = (idx == len(self._conversations) - 1)
        is_active = (idx == self._active_idx)
        btn = QPushButton(title)
        btn.setCheckable(True)
        btn.setChecked(is_active)
        btn.setFixedHeight(52)
        btn.setStyleSheet(self._btn_style(is_active, is_latest))
        btn.clicked.connect(lambda _checked, ix=idx: self._switch(ix))
        return btn

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
            self._stack.setCurrentIndex(idx)
        self._past_notice.setVisible(False)
        self._input_frame.setEnabled(bool(self._conversations))
        for real_idx, btn in self._sidebar_btns:
            is_sel = (real_idx == idx)
            btn.setChecked(is_sel)
            btn.setStyleSheet(self._btn_style(is_sel, real_idx == len(self._conversations) - 1))

    # ------------------------------------------------------------------ Right panel

    def _make_right_panel(self) -> QWidget:
        panel = QWidget()
        vl = QVBoxLayout(panel)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {_BG};")
        if self._conversations:
            for i, conv in enumerate(self._conversations):
                self._stack.addWidget(self._make_page(i, conv))
        else:
            ph = QLabel("No conversations yet.\n\nPress Ctrl+Q to ask something.")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ph.setStyleSheet(f"color: {_HINT}; background: {_BG};")
            self._stack.addWidget(ph)
        self._stack.setCurrentIndex(self._active_idx)
        vl.addWidget(self._stack, stretch=1)

        self._past_notice = QLabel("  Selected conversation")
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
        conv = {"messages": [], "context": ""}
        self._conversations.append(conv)

        if was_empty and self._stack.count() == 1:
            placeholder = self._stack.widget(0)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()

        idx = len(self._conversations) - 1
        self._stack.addWidget(self._make_page(idx, conv))
        self._rebuild_sidebar()
        self._switch(idx)
        self._input.setFocus()

        if auto_message:
            QTimer.singleShot(0, lambda: self._send(auto_message))

    def _make_page(self, idx: int, conv: dict) -> QScrollArea:
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

        for msg in conv["messages"]:
            display_text = msg.get("display_content", msg["content"])
            self._bubble(layout, display_text, msg["role"], msg.get("image_base64"))

        scroll._msg_layout = layout  # type: ignore[attr-defined]
        scroll.setWidget(container)
        QTimer.singleShot(0, lambda s=scroll: s.verticalScrollBar().setValue(
            s.verticalScrollBar().maximum()
        ))
        return scroll

    def _make_input_area(self) -> QWidget:
        frame = QWidget()
        frame.setStyleSheet(f"background: {_TITLE_BG}; border-top: 1px solid {_BORDER};")
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(8)

        self._input = QTextEdit()
        self._input.setFixedHeight(62)
        self._input.setPlaceholderText("Message… (Enter to send, Shift+Enter for newline)")
        self._input.setStyleSheet(
            f"QTextEdit {{ background: rgba(255,255,255,8); border: 1px solid {_BORDER};"
            f" border-radius: 6px; color: {_TEXT}; padding: 6px 8px; font-size: 10pt; }}"
        )
        self._input.installEventFilter(self)

        self._send_btn = QPushButton("Send")
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
        lbl = _MessageTextView(
            f"QTextBrowser {{ background: {bg}; color: {_TEXT}; border-radius: 8px;"
            f" padding: 8px 11px; font-size: 10pt; border: none; }}"
            f"QTextBrowser::selection {{ background: rgba(160,160,255,60); color: {_TEXT}; }}"
        )
        if role == "assistant":
            lbl.setHtml(_assistant_text_to_html(text))
        else:
            lbl.setPlainText(text)

        role_lbl = QLabel("You" if role == "user" else "Assistant")
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
        self._current_ai_label = self._bubble(layout, "…", "assistant") if layout else None
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
            self._current_ai_label.setHtml(_assistant_segments_to_html(self._current_ai_segments))
        self._scroll_bottom()

    def _on_finished(self):
        if self._current_ai_parser is not None:
            flushed = self._current_ai_parser.finish()
            self._current_ai_segments = merge_segment_iterables(self._current_ai_segments, flushed)
            for text, is_thought in flushed:
                if not is_thought:
                    self._current_ai_reply_text += text
            if self._current_ai_label:
                self._current_ai_label.setHtml(_assistant_segments_to_html(self._current_ai_segments))
        if self._current_ai_reply_text and self._conversations and 0 <= self._active_idx < len(self._conversations):
            message = {"role": "assistant", "content": self._current_ai_reply_text}
            if self._current_ai_text != self._current_ai_reply_text:
                message["display_content"] = self._current_ai_text
            self._conversations[self._active_idx]["messages"].append(message)
        self._current_ai_label = None
        self._current_ai_text = ""
        self._current_ai_reply_text = ""
        self._current_ai_segments = []
        self._current_ai_parser = None
        self._streaming = False
        self._send_btn.setEnabled(True)
        self._new_chat_btn.setEnabled(True)

    # ------------------------------------------------------------------ Events

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
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
