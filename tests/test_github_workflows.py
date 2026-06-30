import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GitHubWorkflowTests(unittest.TestCase):
    def test_macos_lock_check_uses_configured_python_minor(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "macos.yml").read_text(encoding="utf-8")

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
                if workflow.name == "ci.yml":
                    self.assertIn('python-version: "3.12"', text)
                    self.assertIn('python-version-file: ".python-version"', text)
                else:
                    self.assertIn('python-version-file: ".python-version"', text)

    def test_workflows_use_node24_actions(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        workflow_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(workflow_dir.glob("*.yml")))

        self.assertIn("actions/checkout@v7", workflow_text)
        self.assertIn("actions/setup-python@v6", workflow_text)
        self.assertNotIn("actions/checkout@v4", workflow_text)
        self.assertNotIn("actions/setup-python@v5", workflow_text)

    def test_pages_workflow_skips_cleanly_when_pages_is_not_enabled(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")

        self.assertIn("Check Pages setup", workflow)
        self.assertIn("https://api.github.com/repos/$REPO/pages", workflow)
        self.assertIn('if [ "$status" = "404" ]; then', workflow)
        self.assertIn("GitHub Pages is not enabled", workflow)
        self.assertIn("Skip deploy until Pages is enabled", workflow)
        self.assertIn("if: steps.pages_setup.outputs.enabled == 'true'", workflow)

    def test_build_workflow_publishes_three_platform_release_manifest(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

        self.assertIn("build-windows:", workflow)
        self.assertIn("build-linux:", workflow)
        self.assertIn("build-macos:", workflow)
        self.assertIn("publish-release:", workflow)
        self.assertIn("scripts/make_release_manifest.py", workflow)
        self.assertIn("wisp-release-manifest.json", workflow)
        self.assertIn("Upload Windows release asset", workflow)
        self.assertIn("Upload Linux release asset", workflow)
        self.assertIn("Upload macOS release asset", workflow)
        self.assertIn('gh release upload "$GITHUB_REF_NAME" release-assets/wisp-release-manifest.json --clobber', workflow)


if __name__ == "__main__":
    unittest.main()
