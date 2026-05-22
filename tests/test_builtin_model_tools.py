import unittest

from core import llm


class BuiltinModelToolsTests(unittest.TestCase):
    def test_git_and_github_tools_are_registered(self):
        names = {schema["name"] for schema in llm._get_tool_schemas()}

        self.assertIn("git_status", names)
        self.assertIn("git_diff", names)
        self.assertIn("github_repo", names)
        self.assertIn("github_issue", names)


if __name__ == "__main__":
    unittest.main()
