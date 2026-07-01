"""Tests for Qt-free chat reply rendering helpers."""

from __future__ import annotations

from ui.chat_rendering import _assistant_text_to_html, _user_text_to_html
from ui.text_annotations import annotations_from_keyword_rules


def test_assistant_html_preserves_markdown_blocks():
    """Verify assistant replies render paragraphs, lists, and code blocks."""
    rendered = _assistant_text_to_html(
        "First line\nSecond line\n\n- one\n- two\n\n```py\nprint('hi')\n```"
    )

    assert "First line<br>Second line" in rendered
    assert "<ul>" in rendered
    assert "<li>one</li>" in rendered
    assert "<pre><code>print(&#x27;hi&#x27;)</code></pre>" in rendered


def test_assistant_html_keeps_inline_code_literal():
    """Verify inline markdown does not style text inside code spans."""
    rendered = _assistant_text_to_html("Use `**literal**` and **bold**")

    assert "<code>**literal**</code>" in rendered
    assert "<strong>bold</strong>" in rendered


def test_empty_annotations_keep_assistant_html_unchanged():
    """Empty annotations stay on the old rendering path."""
    text = "First line\n\n- one\n\n**bold**"

    assert _assistant_text_to_html(text) == _assistant_text_to_html(text, annotations=[])


def test_assistant_annotations_escape_untrusted_text_and_tooltips():
    """Annotation spans are Wisp-owned and do not inject raw HTML."""
    text = "Mark <script>alert(1)</script>"
    rendered = _assistant_text_to_html(
        text,
        annotations=[
            {
                "start": 5,
                "end": len(text),
                "color": "#ffcc00",
                "tooltip": '"quoted" <tip>',
                "id": "unsafe",
            }
        ],
    )

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "title=\"&quot;quoted&quot; &lt;tip&gt;\"" in rendered
    assert 'data-annotation-id="unsafe"' in rendered


def test_annotations_preserve_inline_code_and_markdown_structure():
    """Annotations decorate text nodes without taking over markdown structure."""
    text = "Use `CUDA` and **CUDA** now"
    annotations = annotations_from_keyword_rules(text, [{"match": "CUDA", "color": "#ffd166"}])

    rendered = _assistant_text_to_html(text, annotations=annotations)

    assert "<code>CUDA</code>" in rendered
    assert "<strong><span" in rendered
    assert rendered.count("background-color:#ffd166") == 1


def test_tts_read_highlight_composes_with_annotation_background():
    """Read-position foreground styling should not erase addon highlighting."""
    text = "**CUDA** ready"
    rendered = _assistant_text_to_html(
        text,
        read_count=1,
        annotations=[{"start": 2, "end": 6, "color": "#abc123"}],
    )

    assert "background-color:#abc123" in rendered
    assert "font-weight:bold" in rendered
    assert "color:" in rendered


def test_user_text_annotations_escape_and_preserve_newlines():
    """User messages can opt into safe annotation rendering."""
    rendered = _user_text_to_html(
        "hello\n<world>",
        annotations=[{"start": 6, "end": 13, "kind": "underline", "color": "#00ffaa"}],
    )

    assert "<br>" in rendered
    assert "&lt;world&gt;" in rendered
    assert "text-decoration: underline" in rendered
