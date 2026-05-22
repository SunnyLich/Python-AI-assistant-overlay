import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ui import settings


class SettingsEnvTests(unittest.TestCase):
    def test_write_env_removes_secret_keys(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=old\nLLM_MODEL=old-model\n# comment\n",
                encoding="utf-8",
            )
            with patch.object(settings, "ENV_PATH", env_path):
                settings._write_env(
                    {"LLM_MODEL": "new-model"},
                    remove_keys={"OPENAI_API_KEY"},
                )

            text = env_path.read_text(encoding="utf-8")
            self.assertNotIn("OPENAI_API_KEY", text)
            self.assertIn("LLM_MODEL=new-model", text)
            self.assertIn("# comment", text)


if __name__ == "__main__":
    unittest.main()
