"""Qt-free chat reply HTML rendering helpers."""

from __future__ import annotations

import html
import re

import config
from core.assistant_text import split_tagged_text


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")

# Colours for code blocks and "thinking" text in rendered replies. Seeded with
# the original dark values and refreshed from the app theme by
# ui.chat_window._refresh_chat_palette() so light mode renders readable code.
_RENDER_PALETTE: dict[str, str] = {
    "code_bg": "#26263a",
    "code_inline_bg": "#303044",
    "thought": "#8f8f9e",
}


def set_render_palette(*, code_bg: str = "", code_inline_bg: str = "", thought: str = "") -> None:
    """Update the themed colours used when rendering chat reply HTML."""
    if code_bg:
        _RENDER_PALETTE["code_bg"] = code_bg
    if code_inline_bg:
        _RENDER_PALETTE["code_inline_bg"] = code_inline_bg
    if thought:
        _RENDER_PALETTE["thought"] = thought
_NUMBER_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_WS_RE = re.compile(r"(\s+)")


def _segment_text_to_html(text: str) -> str:
    """Render plain text for QTextBrowser HTML."""
    return html.escape(text).replace("\n", "<br>")


def _inline_markdown_html(text: str) -> str:
    """Render a small, safe inline markdown subset for chat messages."""
    placeholders: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        placeholders.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(placeholders) - 1}\x00"

    escaped = re.sub(r"`([^`\n]+)`", stash_code, text)
    escaped = html.escape(escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(?!\s)(.+?)(?<!\s)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"_(?!\s)(.+?)(?<!\s)_", r"<em>\1</em>", escaped)
    for idx, rendered in enumerate(placeholders):
        escaped = escaped.replace(html.escape(f"\x00{idx}\x00"), rendered)
    return escaped


def _chat_markdown_html(text: str) -> str:
    """Render model replies with readable paragraphs, lists, and code blocks."""
    lines = str(text or "").splitlines()
    if not lines:
        return ""
    parts: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    code_lines: list[str] | None = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            body = "<br>".join(_inline_markdown_html(line) for line in paragraph)
            parts.append(f"<p>{body}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            parts.append(f"</{list_kind}>")
            list_kind = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if code_lines is not None:
            if _FENCE_RE.match(line):
                code = html.escape("\n".join(code_lines))
                parts.append(f"<pre><code>{code}</code></pre>")
                code_lines = None
            else:
                code_lines.append(raw_line)
            continue
        if _FENCE_RE.match(line):
            flush_paragraph()
            close_list()
            code_lines = []
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            close_list()
            level = min(6, len(heading.group(1)))
            parts.append(f"<h{level}>{_inline_markdown_html(heading.group(2))}</h{level}>")
            continue
        bullet = _BULLET_RE.match(line)
        number = _NUMBER_RE.match(line)
        if bullet or number:
            flush_paragraph()
            desired = "ul" if bullet else "ol"
            if list_kind != desired:
                close_list()
                parts.append(f"<{desired}>")
                list_kind = desired
            item = (bullet or number).group(1)
            parts.append(f"<li>{_inline_markdown_html(item)}</li>")
            continue
        close_list()
        paragraph.append(line)

    if code_lines is not None:
        code = html.escape("\n".join(code_lines))
        parts.append(f"<pre><code>{code}</code></pre>")
    flush_paragraph()
    close_list()
    return "".join(parts)


def _accent_color() -> str:
    """The same colour the speech bubble uses to highlight TTS-read words."""
    return getattr(config, "BUBBLE_READ_WORD_COLOR", "#4da3ff") or "#4da3ff"


def _reply_html(text: str, start_idx: int, read_count: int | None) -> tuple[int, str]:
    """Render a reply segment for live TTS read-position highlighting."""
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
    """Render assistant thought/reply segments for the chat transcript."""
    if read_count == 0:
        code_bg = _RENDER_PALETTE["code_bg"]
        code_inline_bg = _RENDER_PALETTE["code_inline_bg"]
        thought_color = _RENDER_PALETTE["thought"]
        parts: list[str] = [
            "<style>"
            "p { margin: 0 0 8px 0; }"
            "p:last-child { margin-bottom: 0; }"
            "ul, ol { margin: 0 0 8px 22px; padding: 0; }"
            "li { margin: 2px 0; }"
            f"pre {{ margin: 0 0 8px 0; padding: 8px; border-radius: 6px;"
            f" background: {code_bg}; white-space: pre-wrap; }}"
            f"code {{ font-family: Consolas, 'Cascadia Mono', monospace;"
            f" background: {code_inline_bg}; padding: 1px 3px; border-radius: 3px; }}"
            "pre code { background: transparent; padding: 0; }"
            "h1, h2, h3, h4, h5, h6 { margin: 0 0 8px 0; font-weight: 700; }"
            "</style>"
        ]
        prev_is_thought: bool | None = None
        for text, is_thought in segments:
            if is_thought:
                parts.append(f'<div style="color: {thought_color};">{_chat_markdown_html(text)}</div>')
            else:
                if prev_is_thought:
                    parts.append("<div style='height: 6px;'></div>")
                parts.append(_chat_markdown_html(text))
            prev_is_thought = is_thought
        return "".join(parts)

    parts: list[str] = []
    idx = 0
    prev_is_thought: bool | None = None
    for text, is_thought in segments:
        if is_thought:
            parts.append(
                f'<span style="color: {_RENDER_PALETTE["thought"]};">{_segment_text_to_html(text)}</span>'
            )
        else:
            if prev_is_thought:
                parts.append("<br>")
            idx, body = _reply_html(text, idx, read_count)
            parts.append(body)
        prev_is_thought = is_thought
    return "".join(parts)


def _assistant_text_to_html(text: str, read_count: int | None = 0) -> str:
    """Render tagged assistant text for the chat transcript."""
    return _assistant_segments_to_html(split_tagged_text(text), read_count)
