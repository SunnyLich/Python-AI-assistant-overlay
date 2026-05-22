import unittest

from core import llm


class LlmFallbackTests(unittest.TestCase):
    def test_route_candidates_dedupes_primary_and_fallbacks(self):
        routes = llm._route_candidates(
            "chatgpt",
            "gpt-5.5",
            "chatgpt:gpt-5.5\nanthropic:claude-sonnet-4-5; openai:gpt-4o",
        )

        self.assertEqual(
            routes,
            [
                ("chatgpt", "gpt-5.5"),
                ("anthropic", "claude-sonnet-4-5"),
                ("openai", "gpt-4o"),
            ],
        )

    def test_stream_with_fallbacks_tries_next_route_before_output(self):
        attempts = []

        def factory(provider, model):
            attempts.append((provider, model))
            if provider == "bad":
                raise RuntimeError("boom")
            yield "ok"

        chunks = list(
            llm._stream_with_fallbacks(
                "query",
                [("bad", "first"), ("good", "second")],
                factory,
            )
        )

        self.assertEqual(chunks, ["ok"])
        self.assertEqual(attempts, [("bad", "first"), ("good", "second")])

    def test_stream_with_fallbacks_does_not_mix_after_output(self):
        def factory(provider, model):
            yield "partial"
            raise RuntimeError("late")

        with self.assertRaises(RuntimeError):
            list(
                llm._stream_with_fallbacks(
                    "query",
                    [("bad", "first"), ("good", "second")],
                    factory,
                )
            )


if __name__ == "__main__":
    unittest.main()
