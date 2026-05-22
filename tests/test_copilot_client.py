import os
import sys
import types
import unittest
from unittest.mock import patch

from core import copilot_client


class CopilotClientBridgeTests(unittest.TestCase):
    def test_client_options_preserve_environment_and_set_state_home(self):
        options = copilot_client._client_options("github_pat_test")

        self.assertEqual(options["env"]["COPILOT_GITHUB_TOKEN"], "github_pat_test")
        self.assertEqual(
            options["env"]["XDG_STATE_HOME"],
            copilot_client._COPILOT_STATE_HOME,
        )
        self.assertEqual(options["env"].get("PATH"), os.environ.get("PATH"))

    def test_sync_ask_awaits_async_sdk_methods(self):
        calls = []

        class FakeSession:
            async def send_and_wait(self, options):
                calls.append(("send_and_wait", options))
                return {"data": {"content": "OK"}}

        class FakeCopilotClient:
            def __init__(self, options):
                calls.append(("init", options))

            async def start(self):
                calls.append(("start", None))

            async def create_session(self, options):
                calls.append(("create_session", options))
                return FakeSession()

            async def stop(self):
                calls.append(("stop", None))
                return []

        fake_copilot = types.ModuleType("copilot")
        fake_copilot.CopilotClient = FakeCopilotClient

        with patch.dict(sys.modules, {"copilot": fake_copilot}), patch(
            "core.copilot_auth.get_token",
            return_value="github_pat_test",
        ), patch(
            "core.copilot_auth.validate_token_format",
            return_value=(True, "ok"),
        ):
            result = copilot_client._ask_sync(
                "hello",
                "gpt-test",
                system="system",
                session_id="test-session",
                allow_tools=False,
            )

        self.assertEqual(result, "OK")
        self.assertEqual(
            [name for name, _value in calls],
            ["init", "start", "create_session", "send_and_wait", "stop"],
        )
        session_options = calls[2][1]
        self.assertEqual(session_options["model"], "gpt-test")
        self.assertEqual(session_options["session_id"], "test-session")
        self.assertEqual(session_options["available_tools"], [])


if __name__ == "__main__":
    unittest.main()
