import unittest

from ui.settings_panel.helpers import parse_fallback_rows


class SettingsFallbackRowsTests(unittest.TestCase):
    def test_parse_fallback_rows_accepts_lines_and_semicolons(self):
        self.assertEqual(
            parse_fallback_rows("chatgpt:gpt-5.5\nanthropic:claude-sonnet-4-5; openai:gpt-4o"),
            [
                ("chatgpt", "gpt-5.5"),
                ("anthropic", "claude-sonnet-4-5"),
                ("openai", "gpt-4o"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

