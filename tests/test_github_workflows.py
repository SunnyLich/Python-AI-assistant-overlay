import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitHubWorkflowTests(unittest.TestCase):
    def test_macos_lock_check_uses_configured_python_minor(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "macos.yml").read_text(encoding="utf-8")

        self.assertIn('python-version-file: ".python-version"', workflow)
        self.assertIn('python_minor="$(python -c', workflow)
        self.assertIn('--python-version "$python_minor"', workflow)
        self.assertNotIn("--python-version 3.12", workflow)

    def test_workflows_use_python_version_file(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        workflows = sorted(workflow_dir.glob("*.yml"))
        self.assertTrue(workflows)

        for workflow in workflows:
            text = workflow.read_text(encoding="utf-8")
            if "actions/setup-python" not in text:
                continue
            with self.subTest(workflow=workflow.name):
                self.assertIn('python-version-file: ".python-version"', text)


if __name__ == "__main__":
    unittest.main()
