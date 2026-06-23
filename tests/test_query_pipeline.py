"""Tests for test query pipeline."""

import threading
import unittest

import pytest

from core.query_pipeline import (
    ContextInputs,
    GenerationCounter,
    build_context,
)


def _build(**kwargs):
    # Default the document reader to one that echoes a marker so tests never
    # touch the real llm client.
    """Verify build behavior."""
    reader = kwargs.pop("read_document_file", lambda p: f"DOC<{p}>")
    return build_context(ContextInputs(intent_prompt="ask", **kwargs), read_document_file=reader)


class BuildContextTests(unittest.TestCase):
    """Test case for build context tests behavior."""
    def test_user_message_is_the_intent_prompt(self):
        """Verify user message is the intent prompt behavior."""
        out = _build()
        self.assertEqual(out.user_message, "ask")
        self.assertEqual(out.ambient_ctx, "")
        self.assertIsNone(out.screenshot_b64)

    def test_single_selection_context_is_labeled(self):
        """Verify single selection context is labeled."""
        out = _build(selected="hello")
        self.assertEqual(out.ambient_ctx, "[Selection]\nhello")

    def test_multiple_contexts_are_source_labeled(self):
        """Verify multiple contexts are source labeled."""
        out = _build(buffered_items=["one"], selected="two")
        self.assertEqual(out.ambient_ctx, "[Buffered context]\none\n\n[Selection]\ntwo")

    def test_ambient_text_precedes_selection_block(self):
        """Verify ambient text precedes the selection block."""
        out = _build(ambient_text="AMB", selected="sel")
        self.assertEqual(out.ambient_ctx, "AMB\n\n[Selection]\nsel")

    def test_ambient_text_alone_when_no_other_context(self):
        """Verify ambient text alone when no other context behavior."""
        out = _build(ambient_text="AMB")
        self.assertEqual(out.ambient_ctx, "AMB")

    def test_clipboard_appended_after_buffered_items(self):
        """Verify clipboard appended after buffered items behavior."""
        out = _build(buffered_items=["buf"], clipboard_text="clip")
        self.assertEqual(out.ambient_ctx, "[Buffered context]\nbuf\n\n[Clipboard]\nclip")

    def test_clipboard_none_is_ignored(self):
        """Verify clipboard none is ignored behavior."""
        out = _build(buffered_items=["buf"], clipboard_text=None)
        self.assertEqual(out.ambient_ctx, "[Buffered context]\nbuf")

    def test_dropped_image_becomes_vision_input_when_none_present(self):
        """Verify dropped image becomes vision input when none present behavior."""
        out = _build(drop_items=[("shot.png", "BASE64", "image")])
        self.assertEqual(out.screenshot_b64, "BASE64")
        self.assertEqual(out.ambient_ctx, "")

    def test_dropped_image_kept_as_context_when_screenshot_exists(self):
        """Verify dropped image kept as context when screenshot exists behavior."""
        out = _build(screenshot_b64="EXISTING", drop_items=[("shot.png", "BASE64", "image")])
        self.assertEqual(out.screenshot_b64, "EXISTING")
        self.assertEqual(out.ambient_ctx, "[Dropped context: shot.png]\nBASE64")

    def test_dropped_document_is_read_and_labelled(self):
        """Verify dropped document is read and labelled behavior."""
        out = _build(drop_items=[("notes.txt", "/tmp/notes.txt", "document_path")])
        self.assertEqual(out.ambient_ctx, "[Document: notes.txt]\nDOC</tmp/notes.txt>")

    def test_dropped_document_empty_read_is_skipped(self):
        """Verify dropped document empty read is skipped behavior."""
        out = _build(
            drop_items=[("empty.txt", "/tmp/empty.txt", "document_path")],
            read_document_file=lambda p: "",
        )
        self.assertEqual(out.ambient_ctx, "")

    def test_active_document_appended_when_no_screenshot(self):
        """Verify active document appended when no screenshot behavior."""
        out = _build(selected="sel", active_document_text="ACTIVE")
        self.assertEqual(out.ambient_ctx, "[Selection]\nsel\n\n[Active document]\nACTIVE")

    def test_priority_note_added_when_browser_and_document_context_exist(self):
        """Verify priority note added when browser and document context exist behavior."""
        out = _build(
            ambient_text="[Browser/Web]\nWEB",
            active_document_text="ACTIVE",
            priority_context="Browser/Web",
        )
        self.assertEqual(
            out.ambient_ctx,
            "[Context priority]\nPrioritize Browser/Web because it was the active "
            "or last-used context when this request was captured. Use the other "
            "context as supporting context unless the user asks otherwise.\n\n"
            "[Browser/Web]\nWEB\n\n"
            "[Active document]\nACTIVE",
        )

    def test_priority_note_omitted_for_single_context(self):
        """Verify priority note omitted for single context behavior."""
        out = _build(
            active_document_text="ACTIVE",
            priority_context="Active document",
        )
        self.assertEqual(out.ambient_ctx, "[Active document]\nACTIVE")

    def test_active_document_kept_when_screenshot_present(self):
        # A screenshot shows pixels, not document text — enabling documents must
        # still inject them even on vision queries.
        """Verify active document kept when screenshot present behavior."""
        out = _build(screenshot_b64="SHOT", active_document_text="ACTIVE")
        self.assertEqual(out.ambient_ctx, "[Active document]\nACTIVE")

    def test_active_document_kept_when_dropped_image_promotes_to_screenshot(self):
        """Verify active document kept when dropped image promotes to screenshot behavior."""
        out = _build(
            drop_items=[("shot.png", "BASE64", "image")],
            active_document_text="ACTIVE",
        )
        self.assertEqual(out.screenshot_b64, "BASE64")
        self.assertEqual(out.ambient_ctx, "[Active document]\nACTIVE")

    def test_full_precedence_order(self):
        """Verify full precedence order behavior."""
        out = _build(
            buffered_items=["buf"],
            drop_items=[("d.txt", "/p", "document_path"), ("x", "raw", "text")],
            clipboard_text="clip",
            selected="sel",
            ambient_text="AMB",
            active_document_text="ACTIVE",
        )
        self.assertEqual(
            out.ambient_ctx,
            "AMB\n\n"
            "[Buffered context]\nbuf\n\n"
            "[Document: d.txt]\nDOC</p>\n\n"
            "[Dropped context: x]\nraw\n\n"
            "[Clipboard]\nclip\n\n"
            "[Selection]\nsel\n\n"
            "[Active document]\nACTIVE",
        )

    @pytest.mark.workflow
    def test_privacy_mode_redacts_sensitive_text_by_default(self):
        """Verify privacy mode redacts sensitive text by default behavior."""
        out = _build(
            selected="api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
            clipboard_text="Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            active_document_text="password=supersecret",
        )

        self.assertIn("[API_KEY]", out.ambient_ctx)
        self.assertIn("[BEARER_TOKEN]", out.ambient_ctx)
        self.assertIn("[REDACTED_CREDENTIAL]", out.ambient_ctx)
        self.assertNotIn("sk-proj-", out.ambient_ctx)
        self.assertNotIn("supersecret", out.ambient_ctx)

    @pytest.mark.workflow
    def test_privacy_mode_reports_detected_and_censored_items(self):
        """Verify privacy mode records safe redaction report metadata."""
        out = _build(
            selected="api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
            clipboard_text="Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            active_document_text="password=supersecret",
        )

        self.assertEqual(out.privacy_report["count"], 3)
        self.assertEqual(out.privacy_report["categories"]["api_key"], 1)
        self.assertEqual(out.privacy_report["categories"]["bearer_token"], 1)
        self.assertEqual(out.privacy_report["categories"]["credential"], 1)
        self.assertEqual(out.privacy_report["sources"]["selection"], 1)
        self.assertEqual(out.privacy_report["sources"]["clipboard"], 1)
        self.assertEqual(out.privacy_report["sources"]["active_document"], 1)
        rendered = repr(out.privacy_report)
        self.assertIn("sk-...7890", rendered)
        self.assertNotIn("supersecret", rendered)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz1234567890", rendered)

    @pytest.mark.workflow
    def test_privacy_mode_redacts_common_provider_tokens(self):
        """Verify provider-specific token formats are censored."""
        github = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcd"
        slack = "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwxyz"
        stripe = "sk_live_abcdefghijklmnopqrstuvwxyz123456"
        google = "AIza" + ("A" * 35)
        npm = "npm_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ123456"
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "signaturepart1234567890"
        )
        discord = "MTIzNDU2Nzg5MDEyMzQ1Njc4.ABCDEF.abcdefghijklmnopqrstuvwxyz123"
        out = _build(
            selected="\n".join(
                [
                    github,
                    "AKIAIOSFODNN7EXAMPLE",
                    slack,
                    stripe,
                    google,
                    npm,
                    jwt,
                    discord,
                ]
            )
        )

        self.assertGreaterEqual(out.ambient_ctx.count("[API_KEY]"), 6)
        self.assertGreaterEqual(out.ambient_ctx.count("[BEARER_TOKEN]"), 2)
        for raw in (github, slack, stripe, google, npm, jwt, discord, "AKIAIOSFODNN7EXAMPLE"):
            self.assertNotIn(raw, out.ambient_ctx)
            self.assertNotIn(raw, repr(out.privacy_report))
        self.assertGreaterEqual(out.privacy_report["categories"]["api_key"], 6)
        self.assertGreaterEqual(out.privacy_report["categories"]["bearer_token"], 2)

    @pytest.mark.workflow
    def test_privacy_mode_redacts_url_credential_params(self):
        """Verify sensitive URL query values are censored without hiding safe params."""
        out = _build(
            selected=(
                "Open https://example.test/callback?access_token=abc123"
                "&refresh_token=def456&code=secret-code&state=visible"
            )
        )

        self.assertIn("https://example.test/callback?access_token=[URL_CREDENTIAL]", out.ambient_ctx)
        self.assertIn("&refresh_token=[URL_CREDENTIAL]", out.ambient_ctx)
        self.assertIn("&code=[URL_CREDENTIAL]", out.ambient_ctx)
        self.assertIn("&state=visible", out.ambient_ctx)
        self.assertNotIn("abc123", out.ambient_ctx)
        self.assertNotIn("def456", out.ambient_ctx)
        self.assertNotIn("secret-code", out.ambient_ctx)
        self.assertEqual(out.privacy_report["categories"]["url_credential"], 3)

    @pytest.mark.workflow
    def test_privacy_mode_can_be_disabled_for_context_building(self):
        """Verify privacy mode can be disabled for context building behavior."""
        out = _build(
            selected="api_key = sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
            trust_privacy_mode=False,
        )

        self.assertIn("sk-proj-abcdefghijklmnopqrstuvwxyz1234567890", out.ambient_ctx)
        self.assertEqual(out.privacy_report["count"], 0)


class GenerationCounterTests(unittest.TestCase):
    """Test case for generation counter tests behavior."""
    def test_starts_at_zero(self):
        """Verify starts at zero behavior."""
        self.assertEqual(GenerationCounter().current, 0)

    def test_next_increments_and_returns(self):
        """Verify next increments and returns behavior."""
        c = GenerationCounter()
        self.assertEqual(c.next(), 1)
        self.assertEqual(c.next(), 2)
        self.assertEqual(c.current, 2)

    def test_is_current_only_for_latest(self):
        """Verify is current only for latest behavior."""
        c = GenerationCounter()
        first = c.next()
        second = c.next()
        self.assertFalse(c.is_current(first))
        self.assertTrue(c.is_current(second))

    def test_concurrent_next_yields_unique_ids(self):
        """Verify concurrent next yields unique ids behavior."""
        c = GenerationCounter()
        seen: list[int] = []
        lock = threading.Lock()

        def worker():
            """Verify worker behavior."""
            gid = c.next()
            with lock:
                seen.append(gid)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(seen), list(range(1, 51)))
        self.assertEqual(c.current, 50)


if __name__ == "__main__":
    unittest.main()
