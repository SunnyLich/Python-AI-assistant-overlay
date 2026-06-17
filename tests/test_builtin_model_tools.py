import unittest
from unittest.mock import patch

from core.llm_clients import client as llm
from core.llm_clients import prompt_guidance


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

    def test_memory_search_note_only_when_tool_offered(self):
        base = "You are a concise desktop assistant."

        self.assertIn("memory_search tool", llm._with_memory_search_note(base, ["memory_search"]))
        self.assertEqual(llm._with_memory_search_note(base, ["memory_save"]), base)
        self.assertEqual(llm._with_memory_search_note(base, None), base)

    def test_prompt_guidance_builds_query_notes_from_one_place(self):
        system = prompt_guidance.apply_query_guidance(
            "Base prompt",
            tools_offered=True,
            allowed_tools=["memory_search", "memory_save"],
            allow_screenshot_tool=True,
        )

        self.assertIn("live tools available", system)
        self.assertIn("capture_screen tool", system)
        self.assertIn("memory_search tool", system)
        self.assertIn("memory_save tool", system)

    def test_frontloaded_memory_search_uses_query(self):
        captured = {}

        class FakeManager:
            def retrieve_relevant(self, query):
                captured["query"] = query
                return "[Memory]\n- I prefer concise answers"

        with patch("core.memory_store.store.get_manager", return_value=FakeManager()):
            ambient = llm._inject_frontloaded_tool_context(
                "Active app: Notes",
                ["memory_search"],
                query="what do you remember about my answer style?",
            )

        self.assertIn("Active app: Notes", ambient)
        self.assertIn("[Memory]", ambient)
        self.assertEqual(captured["query"], "what do you remember about my answer style?")

    def test_frontloaded_memory_search_stays_opt_in(self):
        with patch("core.memory_store.store.get_manager") as get_manager:
            ambient = llm._inject_frontloaded_tool_context(
                "Active app: Notes",
                None,
                query="what do you remember?",
            )

        self.assertEqual(ambient, "Active app: Notes")
        get_manager.assert_not_called()

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

    def test_pinned_tools_bypass_keyword_filter(self):
        # git_status is keyword-gated, so an unrelated prompt drops it even when
        # allowed — unless it is pinned ("On" in the per-caller tool list).
        filtered = {
            schema["name"]
            for schema in llm._get_tool_schemas("hello", allowed_tools=["git_status"])
        }
        pinned = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["git_status"],
                pinned_tools=["git_status"],
            )
        }

        self.assertNotIn("git_status", filtered)
        self.assertIn("git_status", pinned)

    def test_pinned_tools_bypass_keyword_filter_openai_format(self):
        pinned = {
            (schema.get("function") or {}).get("name")
            for schema in llm._get_openai_tool_schemas(
                "hello",
                allowed_tools=["git_status"],
                pinned_tools=["git_status"],
            )
        }

        self.assertIn("git_status", pinned)

    def test_pinned_context_source_grants_offer_get_context_schema(self):
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["get_context.browser"],
                pinned_tools=["get_context"],
            )
        }

        self.assertIn("get_context", names)

    def test_pinned_browser_mode_offers_web_and_context_for_anthropic(self):
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["web_search", "get_context.browser"],
                pinned_tools=["web_search", "get_context"],
            )
        }

        self.assertIn("web_search", names)
        self.assertIn("get_context", names)

    def test_pinned_browser_mode_openai_offers_context_function(self):
        names = {
            (schema.get("function") or {}).get("name")
            for schema in llm._get_openai_tool_schemas(
                "hello",
                allowed_tools=["web_search", "get_context.browser"],
                pinned_tools=["web_search", "get_context"],
            )
        }

        self.assertIn("get_context", names)
        self.assertNotIn("web_search", names)

    def test_pinned_opt_in_tools_are_not_added(self):
        # capture_screen is governed by the screenshot setting, never by the
        # per-caller tool list, even if someone hand-writes it into the env.
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["capture_screen"],
                pinned_tools=["capture_screen"],
            )
        }

        self.assertNotIn("capture_screen", names)


if __name__ == "__main__":
    unittest.main()
