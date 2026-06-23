"""Tests for intent context preview text helpers."""

from __future__ import annotations


def test_context_preview_text_is_redacted_and_trimmed(monkeypatch):
    """Verify context preview snippets are compact and privacy-safe."""
    import config
    from runtime.supervisor.flows import FlowController

    monkeypatch.setattr(config, "TRUST_PRIVACY_MODE", True, raising=False)
    preview = FlowController._context_preview_text(
        "OpenAI key sk-" + ("a" * 24) + " should not be visible " + ("x" * 240),
        limit=80,
    )

    assert "[API_KEY]" in preview
    assert "sk-" not in preview
    assert len(preview) <= 80
