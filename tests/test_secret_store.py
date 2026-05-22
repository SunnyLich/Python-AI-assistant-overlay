import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core import secret_store


class FakeKeyring(types.SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class SecretStoreTests(unittest.TestCase):
    def test_get_secret_prefers_keychain_over_env(self):
        fake = FakeKeyring()
        fake.set_password("python-ai-overlay", "openai_api_key", "keychain-value")
        with patch.dict(sys.modules, {"keyring": fake}), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "env-value"}
        ):
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "keychain-value")

    def test_get_secret_falls_back_to_env_for_migration(self):
        fake = FakeKeyring()
        with patch.dict(sys.modules, {"keyring": fake}), patch.dict(
            "os.environ", {"OPENAI_API_KEY": "env-value"}
        ):
            self.assertEqual(secret_store.get_secret("OPENAI_API_KEY"), "env-value")

    def test_migrate_env_secrets_writes_missing_keys(self):
        fake = FakeKeyring()
        env = {"OPENAI_API_KEY": "sk-test", "GROQ_API_KEY": ""}
        with patch.dict(sys.modules, {"keyring": fake}):
            migrated = secret_store.migrate_env_secrets(env)

        self.assertEqual(migrated, ["OPENAI_API_KEY"])
        self.assertEqual(
            fake.get_password("python-ai-overlay", "openai_api_key"),
            "sk-test",
        )

    def test_secret_source_reports_keychain_env_or_none(self):
        fake = FakeKeyring()
        fake.set_password("python-ai-overlay", "openai_api_key", "keychain-value")
        with patch.dict(sys.modules, {"keyring": fake}), patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "env-value"}, clear=False
        ):
            self.assertEqual(secret_store.secret_source("OPENAI_API_KEY"), "keychain")
            self.assertEqual(secret_store.secret_source("ANTHROPIC_API_KEY"), "env")
            self.assertEqual(secret_store.secret_source("GROQ_API_KEY"), "none")

    def test_configured_marker_counts_as_has_secret_for_display(self):
        fake = FakeKeyring()
        with TemporaryDirectory() as tmp, patch.dict(sys.modules, {"keyring": fake}), patch.object(
            secret_store, "_META_FILE", Path(tmp) / ".secret_status.json"
        ):
            self.assertFalse(secret_store.has_secret("OPENAI_API_KEY"))
            secret_store.set_configured_marker("OPENAI_API_KEY", True)
            self.assertTrue(secret_store.has_secret("OPENAI_API_KEY"))


if __name__ == "__main__":
    unittest.main()
