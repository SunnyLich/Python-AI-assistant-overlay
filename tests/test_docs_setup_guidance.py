import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocsSetupGuidanceTests(unittest.TestCase):
    def test_readme_does_not_recommend_generic_venv_creation(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertNotIn("python -m venv .venv", readme)

    def test_developer_readme_points_to_preflight_after_setup(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        developer_readme = (ROOT / "docs" / "DEVELOPER_README.md").read_text(encoding="utf-8")

        self.assertIn("docs/DEVELOPER_README.md", readme)
        self.assertIn(r".\.venv\Scripts\python.exe scripts\check_dev_environment.py", developer_readme)
        self.assertIn(".venv/bin/python scripts/check_dev_environment.py", developer_readme)

    def test_readme_guides_normal_configuration_through_settings(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Use the Settings window for normal setup", readme)
        self.assertNotIn("cp .env.example .env", readme)
        self.assertNotIn("Copy-Item .env.example .env", readme)

    def test_dependency_docs_cover_all_requirement_manifests(self) -> None:
        docs = (ROOT / "docs" / "DEPENDENCY_LOCKS.md").read_text(encoding="utf-8")

        self.assertIn("`requirements.txt`", docs)
        self.assertIn("`requirements-dev.txt`", docs)
        self.assertIn("`requirements-build.txt`", docs)
        self.assertIn("`requirements-macos.lock`", docs)
        self.assertIn("PyInstaller", docs)


if __name__ == "__main__":
    unittest.main()
