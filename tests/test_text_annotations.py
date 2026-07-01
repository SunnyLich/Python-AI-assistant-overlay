"""Tests for safe text annotation normalization and composition."""

from __future__ import annotations

from ui.text_annotations import (
    DEFAULT_HIGHLIGHT_COLOR,
    annotations_from_keyword_rules,
    compose_annotated_slices,
    normalize_keyword_rules,
    normalize_range_annotations,
)


def test_normalize_range_annotations_sanitizes_payloads() -> None:
    """Invalid ranges are dropped and unsafe display metadata is normalized."""
    annotations = normalize_range_annotations(
        [
            {
                "start": -5,
                "end": 4,
                "kind": "unknown",
                "color": "javascript:bad",
                "tooltip": "x" * 400,
                "source": "addon:demo",
            },
            {"start": 5, "end": 5},
            {"start": True, "end": 7},
        ],
        "hello world",
    )

    assert len(annotations) == 1
    assert annotations[0].start == 0
    assert annotations[0].end == 4
    assert annotations[0].kind == "highlight"
    assert annotations[0].color == DEFAULT_HIGHLIGHT_COLOR
    assert len(annotations[0].tooltip) == 240


def test_keyword_rules_expand_with_case_and_word_boundaries() -> None:
    """Keyword rules are literal, bounded, and safe for streaming text."""
    annotations = annotations_from_keyword_rules(
        "CUDA cuda cudagraph CUDA_2",
        [
            {
                "match": "cuda",
                "case_sensitive": False,
                "whole_word": True,
                "color": "#abc",
            }
        ],
    )

    assert [(item.start, item.end, item.color) for item in annotations] == [
        (0, 4, "#abc"),
        (5, 9, "#abc"),
    ]


def test_keyword_rules_are_limited_and_sanitized() -> None:
    """Huge or malformed rule lists cannot flood the renderer."""
    rules = normalize_keyword_rules([{"match": f"k{i}"} for i in range(80)])

    assert len(rules) == 64
    assert normalize_keyword_rules([{"match": ""}, object()]) == []


def test_compose_annotated_slices_resolves_overlaps_deterministically() -> None:
    """Earlier and longer annotations win without creating nested spans."""
    annotations = normalize_range_annotations(
        [
            {"start": 1, "end": 4, "id": "first"},
            {"start": 2, "end": 5, "id": "overlap"},
            {"start": 5, "end": 6, "id": "last"},
        ],
        "abcdef",
    )

    slices = compose_annotated_slices("abcdef", annotations)

    assert [(item.text, item.annotation.id if item.annotation else "") for item in slices] == [
        ("a", ""),
        ("bcd", "first"),
        ("e", ""),
        ("f", "last"),
    ]
