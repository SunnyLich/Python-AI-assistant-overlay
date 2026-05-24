import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.system.env_utils import env_bool, env_float, env_int, write_env_file


class EnvUtilsTests(unittest.TestCase):
    def test_env_bool_accepts_common_true_and_false_values(self):
        with patch.dict(os.environ, {"FLAG_YES": "yes", "FLAG_OFF": "off"}, clear=False):
            self.assertTrue(env_bool("FLAG_YES", False))
            self.assertFalse(env_bool("FLAG_OFF", True))

    def test_numeric_helpers_fall_back_on_invalid_values(self):
        with patch.dict(os.environ, {"COUNT": "many", "RATE": "fast"}, clear=False):
            self.assertEqual(env_int("COUNT", 3), 3)
            self.assertEqual(env_float("RATE", 1.25), 1.25)

    def test_write_env_file_preserves_comments_and_quotes_special_values(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OLD=value\n# keep me\n", encoding="utf-8")

            write_env_file(
                env_path,
                {"OLD": "new value", "PROMPT": "hello # world"},
            )

            text = env_path.read_text(encoding="utf-8")
            self.assertIn("OLD=new value", text)
            self.assertIn("# keep me", text)
            self.assertIn('PROMPT="hello # world"', text)


if __name__ == "__main__":
    unittest.main()

