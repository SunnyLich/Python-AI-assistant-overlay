import unittest

from core.llm_clients import client as llm


class BuiltinModelToolsTests(unittest.TestCase):
    _GIT_TOOLS = {"git_status", "git_diff", "github_repo", "github_issue"}

    def test_git_and_github_tools_are_registered(self):
        names = {schema["name"] for schema in llm._TOOL_REGISTRY.schemas()}

        self.assertTrue(self._GIT_TOOLS <= names)

    def test_git_and_github_tools_surface_for_relevant_prompt(self):
        # These tools are keyword-gated (see tool_keywords.json): an empty prompt
        # excludes them, but a relevant prompt brings them back.
        empty = {schema["name"] for schema in llm._get_tool_schemas("")}
        self.assertTrue(self._GIT_TOOLS.isdisjoint(empty))

        relevant = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "show me the git status and git diff, and the github repo and issue"
            )
        }
        self.assertTrue(self._GIT_TOOLS <= relevant)

    def test_allowed_tool_filter_limits_general_schemas(self):
        prompt = "show me the git status and github issue, then search the web"

        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                prompt,
                allowed_tools=["web_search", "get_context.browser"],
            )
        }

        self.assertIn("web_search", names)
        self.assertIn("get_context", names)
        self.assertTrue(self._GIT_TOOLS.isdisjoint(names))

    def test_memory_search_is_opt_in(self):
        default_names = {schema["name"] for schema in llm._get_tool_schemas("remember my project")}
        allowed_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "remember my project",
                allowed_tools=["memory_search"],
            )
        }

        self.assertNotIn("memory_search", default_names)
        self.assertIn("memory_search", allowed_names)

    def test_get_context_execution_respects_source_allowlist(self):
        self.assertIn(
            "disabled",
            llm._execute_model_tool(
                "get_context",
                {},
                allowed_tools=["get_context.browser"],
            ),
        )
        self.assertIn(
            "disabled",
            llm._execute_model_tool(
                "get_context",
                {"url": "https://example.com"},
                allowed_tools=["get_context.documents"],
            ),
        )


if __name__ == "__main__":
    unittest.main()
