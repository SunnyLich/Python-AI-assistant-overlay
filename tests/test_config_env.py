import os
import unittest
from unittest.mock import patch

import config


class ConfigEnvTests(unittest.TestCase):
    def test_reload_parses_icon_size_and_bool_aliases(self):
        previous = {
            "DOLL_SIZE": config.DOLL_SIZE,
            "DOLL_AUTO_HIDE": config.DOLL_AUTO_HIDE,
            "SNIP_CONTEXT_DOCUMENTS": config.SNIP_CONTEXT_DOCUMENTS,
        }
        try:
            with patch("config.load_dotenv"), patch.dict(
                os.environ,
                {
                    "DOLL_SIZE": "96",
                    "DOLL_AUTO_HIDE": "yes",
                    "SNIP_CONTEXT_DOCUMENTS": "off",
                },
                clear=False,
            ):
                config.reload()

            self.assertEqual(config.DOLL_SIZE, 96)
            self.assertTrue(config.DOLL_AUTO_HIDE)
            self.assertFalse(config.SNIP_CONTEXT_DOCUMENTS)
        finally:
            for name, value in previous.items():
                setattr(config, name, value)


if __name__ == "__main__":
    unittest.main()
