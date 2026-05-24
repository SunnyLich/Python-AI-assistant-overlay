from __future__ import annotations

import unittest

from ui.agent.log_parser import log_body, parse_live_log_event


class AgentRunLogParserTests(unittest.TestCase):
    def test_strips_timestamp(self):
        self.assertEqual(log_body("[12:34:56] agent turn 1/3: Builder"), "agent turn 1/3: Builder")

    def test_parses_agent_turn(self):
        event = parse_live_log_event("[12:34:56] agent turn 1/3: Builder")

        self.assertEqual(event.kind, "agent_turn")
        self.assertEqual(event.agent, "Builder")

    def test_parses_direct_message(self):
        event = parse_live_log_event("[12:34:56] message: Builder -> Reviewer: Ready")

        self.assertEqual(event.kind, "message")


if __name__ == "__main__":
    unittest.main()

