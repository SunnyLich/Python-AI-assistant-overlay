import unittest

from core.memory_store.commands import extract_remember_fact


class MemoryCommandTests(unittest.TestCase):
    def test_extracts_explicit_memory_command(self):
        self.assertEqual(
            extract_remember_fact("please remember that I like concise answers"),
            "I like concise answers",
        )

    def test_ignores_conversational_remember_phrasing(self):
        self.assertIsNone(extract_remember_fact("Do you remember what I said?"))
        self.assertIsNone(extract_remember_fact("I remember that already."))


if __name__ == "__main__":
    unittest.main()

