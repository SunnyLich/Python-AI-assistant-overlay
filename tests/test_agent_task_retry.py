from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.agent.task_spec import continue_spec_from_run, role_responsibility, retry_spec_from_run


class AgentTaskRetryTests(unittest.TestCase):
    def test_researcher_role_has_default_responsibility(self):
        self.assertIn("read-only context", role_responsibility("Researcher"))

    def test_retry_spec_loads_original_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "task.json").write_text(
                json.dumps({
                    "title": "Fix tests",
                    "objective": "Make tests pass.",
                    "scope_folder": tmp,
                    "provider": "copilot",
                    "model": "gpt-5.3-codex",
                }),
                encoding="utf-8",
            )

            spec = retry_spec_from_run(run_dir)

            self.assertEqual(spec.title, "Fix tests")
            self.assertEqual(spec.objective, "Make tests pass.")

    def test_continue_spec_adds_previous_run_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "task.json").write_text(
                json.dumps({
                    "title": "Fix tests",
                    "objective": "Make tests pass.",
                    "scope_folder": tmp,
                    "provider": "copilot",
                    "model": "gpt-5.3-codex",
                    "required_context": "Existing context.",
                }),
                encoding="utf-8",
            )
            (run_dir / "final.md").write_text("Previous final.", encoding="utf-8")
            (run_dir / "run.log").write_text(
                "\n".join([
                    "[11:00:00] model streaming response: 7024 chars received after 48.4s",
                    "[11:00:01] model response still streaming after 50s (7279 chars received)",
                    "[11:00:02] agent turn 4/59: Builder",
                    "[11:00:03] Builder thought: I need to patch snake_game.py.",
                    "[11:00:04] Builder tool call: patch_file",
                ]),
                encoding="utf-8",
            )

            spec = continue_spec_from_run(run_dir)

            self.assertEqual(spec.title, "Continue: Fix tests")
            self.assertIn("Existing context.", spec.required_context)
            self.assertIn("Previous final.", spec.required_context)
            self.assertIn("Previous useful run events", spec.required_context)
            self.assertIn("Builder tool call: patch_file", spec.required_context)
            self.assertNotIn("model streaming response", spec.required_context)
            self.assertNotIn("response still streaming", spec.required_context)


if __name__ == "__main__":
    unittest.main()

