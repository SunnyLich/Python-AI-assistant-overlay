import threading
import unittest

from core.query_pipeline import (
    ContextInputs,
    GenerationCounter,
    build_context,
)


def _build(**kwargs):
    # Default the document reader to one that echoes a marker so tests never
    # touch the real llm client.
    reader = kwargs.pop("read_document_file", lambda p: f"DOC<{p}>")
    return build_context(ContextInputs(intent_prompt="ask", **kwargs), read_document_file=reader)


class BuildContextTests(unittest.TestCase):
    def test_user_message_is_the_intent_prompt(self):
        out = _build()
        self.assertEqual(out.user_message, "ask")
        self.assertEqual(out.ambient_ctx, "")
        self.assertIsNone(out.screenshot_b64)

    def test_single_context_is_not_numbered(self):
        out = _build(selected="hello")
        self.assertEqual(out.ambient_ctx, "hello")

    def test_multiple_contexts_are_numbered(self):
        out = _build(buffered_items=["one"], selected="two")
        self.assertEqual(out.ambient_ctx, "Context 1:\none\n\nContext 2:\ntwo")

    def test_ambient_text_is_prefixed_with_separator(self):
        out = _build(ambient_text="AMB", selected="sel")
        self.assertEqual(out.ambient_ctx, "AMB\n\n---\nsel")

    def test_ambient_text_alone_when_no_other_context(self):
        out = _build(ambient_text="AMB")
        self.assertEqual(out.ambient_ctx, "AMB")

    def test_clipboard_appended_after_buffered_items(self):
        out = _build(buffered_items=["buf"], clipboard_text="clip")
        self.assertEqual(out.ambient_ctx, "Context 1:\nbuf\n\nContext 2:\nclip")

    def test_clipboard_none_is_ignored(self):
        out = _build(buffered_items=["buf"], clipboard_text=None)
        self.assertEqual(out.ambient_ctx, "buf")

    def test_dropped_image_becomes_vision_input_when_none_present(self):
        out = _build(drop_items=[("shot.png", "BASE64", "image")])
        self.assertEqual(out.screenshot_b64, "BASE64")
        self.assertEqual(out.ambient_ctx, "")

    def test_dropped_image_kept_as_context_when_screenshot_exists(self):
        out = _build(screenshot_b64="EXISTING", drop_items=[("shot.png", "BASE64", "image")])
        self.assertEqual(out.screenshot_b64, "EXISTING")
        self.assertEqual(out.ambient_ctx, "BASE64")

    def test_dropped_document_is_read_and_labelled(self):
        out = _build(drop_items=[("notes.txt", "/tmp/notes.txt", "document_path")])
        self.assertEqual(out.ambient_ctx, "[notes.txt]\nDOC</tmp/notes.txt>")

    def test_dropped_document_empty_read_is_skipped(self):
        out = _build(
            drop_items=[("empty.txt", "/tmp/empty.txt", "document_path")],
            read_document_file=lambda p: "",
        )
        self.assertEqual(out.ambient_ctx, "")

    def test_active_document_appended_when_no_screenshot(self):
        out = _build(selected="sel", active_document_text="ACTIVE")
        self.assertEqual(out.ambient_ctx, "sel\n\n---\n[Active document]\nACTIVE")

    def test_active_document_kept_when_screenshot_present(self):
        # A screenshot shows pixels, not document text — enabling documents must
        # still inject them even on vision queries.
        out = _build(screenshot_b64="SHOT", active_document_text="ACTIVE")
        self.assertEqual(out.ambient_ctx, "[Active document]\nACTIVE")

    def test_active_document_kept_when_dropped_image_promotes_to_screenshot(self):
        out = _build(
            drop_items=[("shot.png", "BASE64", "image")],
            active_document_text="ACTIVE",
        )
        self.assertEqual(out.screenshot_b64, "BASE64")
        self.assertEqual(out.ambient_ctx, "[Active document]\nACTIVE")

    def test_full_precedence_order(self):
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
            "AMB\n\n---\n"
            "Context 1:\nbuf\n\n"
            "Context 2:\n[d.txt]\nDOC</p>\n\n"
            "Context 3:\nraw\n\n"
            "Context 4:\nclip\n\n"
            "Context 5:\nsel\n\n"
            "---\n[Active document]\nACTIVE",
        )


class GenerationCounterTests(unittest.TestCase):
    def test_starts_at_zero(self):
        self.assertEqual(GenerationCounter().current, 0)

    def test_next_increments_and_returns(self):
        c = GenerationCounter()
        self.assertEqual(c.next(), 1)
        self.assertEqual(c.next(), 2)
        self.assertEqual(c.current, 2)

    def test_is_current_only_for_latest(self):
        c = GenerationCounter()
        first = c.next()
        second = c.next()
        self.assertFalse(c.is_current(first))
        self.assertTrue(c.is_current(second))

    def test_concurrent_next_yields_unique_ids(self):
        c = GenerationCounter()
        seen: list[int] = []
        lock = threading.Lock()

        def worker():
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
