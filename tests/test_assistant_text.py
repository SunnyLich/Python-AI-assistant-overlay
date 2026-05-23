import unittest

from core.assistant_text import (
    ThoughtStreamParser,
    extract_reply_text,
    merge_segment_iterables,
    split_tagged_text,
)


class AssistantTextTests(unittest.TestCase):
    def test_split_tagged_text_separates_thoughts_and_reply(self):
        self.assertEqual(
            split_tagged_text("<think>plan</think>Answer"),
            [("plan", True), ("Answer", False)],
        )

    def test_stream_parser_handles_split_tags_across_chunks(self):
        parser = ThoughtStreamParser()
        segments = []
        for chunk in ("<th", "ink>plan", "</thi", "nk>Ans", "wer"):
            segments.extend(parser.feed(chunk))
        segments.extend(parser.finish())
        segments = merge_segment_iterables(segments)

        self.assertEqual(segments, [("plan", True), ("Answer", False)])

    def test_extract_reply_text_removes_thought_blocks(self):
        self.assertEqual(
            extract_reply_text("<think>internal\nnotes</think>Visible reply"),
            "Visible reply",
        )


if __name__ == "__main__":
    unittest.main()