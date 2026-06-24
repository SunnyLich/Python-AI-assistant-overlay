"""Tests for test builtin model tools."""

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import config
from core.settings_model import ToolTurnBudgets
from core.llm_clients import client as llm
from core.llm_clients import prompt_guidance


class BuiltinModelToolsTests(unittest.TestCase):
    """Test case for builtin model tools tests behavior."""
    _GIT_TOOLS = {"git_status", "git_diff", "github_repo", "github_issue"}

    def test_git_and_github_tools_are_registered(self):
        """Verify git and github tools are registered behavior."""
        names = {schema["name"] for schema in llm._TOOL_REGISTRY.schemas()}

        self.assertTrue(self._GIT_TOOLS <= names)
        self.assertIn("retrieve_website", names)

    def test_git_and_github_tools_surface_for_relevant_prompt(self):
        # These tools are keyword-gated (see tool_keywords.json): an empty prompt
        # excludes them, but a relevant prompt brings them back.
        """Verify git and github tools surface for relevant prompt behavior."""
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
        """Verify allowed tool filter limits general schemas behavior."""
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

    def test_tool_result_budget_clips_large_outputs(self):
        """Verify active profile tool-result budgets cap returned text."""
        settings = SimpleNamespace(
            tool_turn=ToolTurnBudgets(
                max_calls=3,
                max_result_chars=10,
                max_total_chars=12,
            )
        )

        with patch.object(llm.config, "get_settings", return_value=settings):
            first, spent = llm._clip_tool_result_for_turn("abcdefghijklmno", 0)
            second, spent = llm._clip_tool_result_for_turn("uvwxyz", spent)

        self.assertTrue(first.startswith("abcdefghij"))
        self.assertIn("truncated", first)
        self.assertTrue(second.startswith("uv"))
        self.assertIn("truncated", second)
        self.assertEqual(spent, 12)

    def test_retrieve_website_is_browser_scoped(self):
        """Verify retrieve_website follows explicit Browser/Web tool grants."""
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "read this website",
                allowed_tools=["retrieve_website"],
                pinned_tools=["retrieve_website"],
            )
        }
        blocked = llm._execute_model_tool(
            "retrieve_website",
            {"url": "https://example.com"},
            allowed_tools=["get_context.documents"],
        )

        self.assertIn("retrieve_website", names)
        self.assertIn("disabled", blocked)

    def test_memory_search_is_opt_in(self):
        """Verify memory search is opt in behavior."""
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
        """Verify memory search note only when tool offered behavior."""
        base = "You are a concise desktop assistant."

        self.assertIn("memory_search tool", llm._with_memory_search_note(base, ["memory_search"]))
        self.assertEqual(llm._with_memory_search_note(base, ["memory_save"]), base)
        self.assertEqual(llm._with_memory_search_note(base, None), base)

    def test_prompt_guidance_builds_query_notes_from_one_place(self):
        """Verify prompt guidance builds query notes from one place behavior."""
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
        """Verify frontloaded memory search uses query behavior."""
        captured = {}

        class FakeManager:
            """Coordinate fake manager behavior."""
            def retrieve_relevant(self, query):
                """Verify retrieve relevant behavior."""
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
        """Verify frontloaded memory search stays opt in behavior."""
        with patch("core.memory_store.store.get_manager") as get_manager:
            ambient = llm._inject_frontloaded_tool_context(
                "Active app: Notes",
                [],
                query="what do you remember?",
            )

        self.assertEqual(ambient, "Active app: Notes")
        get_manager.assert_not_called()

    def test_get_context_execution_respects_source_allowlist(self):
        """Verify get context execution respects source allowlist behavior."""
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
        """Verify pinned tools bypass keyword filter behavior."""
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
        """Verify pinned tools bypass keyword filter openai format behavior."""
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
        """Verify pinned context source grants offer get context schema behavior."""
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
        """Verify pinned browser mode offers web and context for anthropic behavior."""
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
        """Verify pinned browser mode openai offers context function behavior."""
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
        """Verify pinned opt in tools are not added behavior."""
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["capture_screen"],
                pinned_tools=["capture_screen"],
            )
        }

        self.assertNotIn("capture_screen", names)

    def test_local_file_tools_are_explicit_opt_in(self):
        """Verify local file tools only surface when a caller grants them."""
        default_names = {schema["name"] for schema in llm._get_tool_schemas("edit this file")}
        model_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "edit this file",
                allowed_tools=["edit_file"],
            )
        }
        irrelevant_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["edit_file"],
            )
        }
        pinned_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["edit_file"],
                pinned_tools=["edit_file"],
            )
        }

        self.assertNotIn("edit_file", default_names)
        self.assertIn("edit_file", model_names)
        self.assertNotIn("edit_file", irrelevant_names)
        self.assertIn("edit_file", pinned_names)

    def test_local_file_tools_execute_with_scope_and_approval(self):
        """Verify scoped read/edit behavior for live local file tools."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_mode = getattr(config, "TOOL_FILE_MODE", "never")
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_file_edit_approval_callback(None)
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                note = root / "note.txt"
                note.write_text("hello world", encoding="utf-8")
                secret = root / ".env"
                secret.write_text("TOKEN=shh", encoding="utf-8")

                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = [".env*", "**/.env*"]
                config.TOOL_FILE_MODE = "never"

                self.assertIn(
                    "hello world",
                    llm._execute_model_tool(
                        "read_file",
                        {"path": str(note)},
                        allowed_tools=["read_file"],
                    ),
                )
                self.assertIn(
                    "failed",
                    llm._execute_model_tool(
                        "read_file",
                        {"path": str(secret)},
                        allowed_tools=["read_file"],
                    ),
                )
                self.assertIn(
                    "disabled",
                    llm._execute_model_tool(
                        "edit_file",
                        {"path": str(note), "old": "world", "new": "there"},
                        allowed_tools=["edit_file"],
                    ),
                )
                self.assertEqual(note.read_text(encoding="utf-8"), "hello world")

                approvals: list[dict] = []
                config.TOOL_FILE_MODE = "ask"
                llm.set_file_edit_approval_callback(lambda request: approvals.append(request) or True)
                result = llm._execute_model_tool(
                    "edit_file",
                    {"path": str(note), "old": "world", "new": "there"},
                    allowed_tools=["edit_file"],
                )

                self.assertIn("Edited", result)
                self.assertEqual(note.read_text(encoding="utf-8"), "hello there")
                self.assertEqual(approvals[0]["action"], "edit_file")
                self.assertIn("-hello world", approvals[0]["diff"])

                created = root / "hello.py"
                result = llm._execute_model_tool(
                    "create_file",
                    {"path": str(created), "content": 'print("Hello, world!")\n'},
                    allowed_tools=["create_file"],
                )

                self.assertIn("hello.py", result)
                self.assertEqual(created.read_text(encoding="utf-8"), 'print("Hello, world!")\n')
                self.assertIn(
                    "failed",
                    llm._execute_model_tool(
                        "create_file",
                        {"path": str(created), "content": "again\n"},
                        allowed_tools=["create_file"],
                    ),
                )
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_MODE = old_mode
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_file_edit_approval_callback(None)

    def test_local_file_edit_refuses_stale_approval_preview(self):
        """Verify ask-mode edits fail if the file changes after preview approval."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_mode = getattr(config, "TOOL_FILE_MODE", "never")
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                note = root / "note.txt"
                note.write_text("alpha beta", encoding="utf-8")
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []
                config.TOOL_FILE_MODE = "ask"

                def approve_and_change(_request):
                    note.write_text("changed beta", encoding="utf-8")
                    return True

                llm.set_file_edit_approval_callback(approve_and_change)
                result = llm._execute_model_tool(
                    "edit_file",
                    {"path": str(note), "old": "beta", "new": "gamma"},
                    allowed_tools=["edit_file"],
                )

                self.assertIn("changed after approval", result)
                self.assertEqual(note.read_text(encoding="utf-8"), "changed beta")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_MODE = old_mode
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_file_edit_approval_callback(None)

    @patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "0"}, clear=False)
    def test_codex_responses_tool_loop_can_write_local_file(self):
        """Verify ChatGPT Responses function calls execute local file tools."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                first = SimpleNamespace(
                    id="resp_1",
                    output_text="",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call_1",
                            name="create_file",
                            arguments='{"path":"whatever.txt","content":"Whatever"}',
                        )
                    ],
                )
                second = SimpleNamespace(id="resp_2", output_text="Created whatever.txt.", output=[])

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        return first if len(self.calls) == 1 else second

                client = SimpleNamespace(responses=Responses())

                text = "".join(
                    llm._stream_codex(
                        "make one text file",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file", "write_file", "read_file", "list_files"],
                        pinned_tools=["create_file"],
                    )
                )

                self.assertEqual(text, "Created whatever.txt.")
                self.assertEqual((root / "whatever.txt").read_text(encoding="utf-8"), "Whatever")
                self.assertIn("tools", client.responses.calls[0])
                self.assertIs(client.responses.calls[0]["store"], True)
                self.assertEqual(client.responses.calls[1]["previous_response_id"], "resp_1")
                self.assertIs(client.responses.calls[1]["store"], True)
                self.assertIn("instructions", client.responses.calls[1])
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    def test_codex_tool_prompt_names_allowed_local_file_root(self):
        """Verify file-tool prompts tell the model which local folder is allowed."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("ask")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        return SimpleNamespace(id="resp_1", output_text="ok", output=[])

                responses = Responses()
                client = SimpleNamespace(responses=responses)

                text = "".join(
                    llm._stream_codex(
                        "update hello_world.py",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file", "edit_file", "write_file", "read_file"],
                        pinned_tools=["create_file", "edit_file", "write_file", "read_file"],
                    )
                )

                self.assertEqual(text, "ok")
                instructions = responses.calls[0]["instructions"]
                self.assertIn("Local file access is available", instructions)
                for configured_root in llm.configured_file_roots():
                    self.assertIn(str(configured_root), instructions)
                self.assertIn("complete the requested operation", instructions)
                self.assertIn("reading a file is not a substitute", instructions)
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    @patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "0"}, clear=False)
    def test_codex_tool_loop_continues_when_file_edit_stops_after_read(self):
        """Verify mutating file requests do not stop after only listing/reading."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "hello_world.py"
                target.write_text('print("Hello, world!")\n', encoding="utf-8")
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                first = SimpleNamespace(
                    id="resp_1",
                    output_text="",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call_1",
                            name="read_file",
                            arguments='{"path":"hello_world.py"}',
                        )
                    ],
                )
                premature = SimpleNamespace(
                    id="resp_2",
                    output_text='hello_world.py contains:\n\nprint("Hello, world!")',
                    output=[],
                )
                edit = SimpleNamespace(
                    id="resp_3",
                    output_text="",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call_2",
                            name="edit_file",
                            arguments=(
                                '{"path":"hello_world.py",'
                                '"old":"print(\\"Hello, world!\\")\\n",'
                                '"new":"# Say hello\\nprint(\\"Hello, world!\\")\\n"}'
                            ),
                        )
                    ],
                )
                final = SimpleNamespace(id="resp_4", output_text="Updated hello_world.py.", output=[])

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        if len(self.calls) == 1:
                            return first
                        if len(self.calls) == 2:
                            return premature
                        if len(self.calls) == 3:
                            assert "only gathered file context" in str(kwargs.get("input"))
                            return edit
                        return final

                responses = Responses()
                client = SimpleNamespace(responses=responses)

                text = "".join(
                    llm._stream_codex(
                        "add a comment to hello_world.py",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["read_file", "edit_file", "write_file"],
                        pinned_tools=["read_file", "edit_file", "write_file"],
                    )
                )

                self.assertEqual(text, "Updated hello_world.py.")
                self.assertEqual(target.read_text(encoding="utf-8"), '# Say hello\nprint("Hello, world!")\n')
                self.assertEqual(len(responses.calls), 4)
                self.assertIn("previous_response_id", responses.calls[2])
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    def test_codex_tool_loop_marks_pre_tool_text_as_progress(self):
        """Verify Responses pre-tool text streams as progress narration."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                first = SimpleNamespace(
                    id="resp_1",
                    output_text="Checking the file first.",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call_1",
                            name="create_file",
                            arguments='{"path":"note.txt","content":"hello"}',
                        )
                    ],
                )
                final = SimpleNamespace(id="resp_2", output_text="Created note.txt.", output=[])

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        return first if len(self.calls) == 1 else final

                client = SimpleNamespace(responses=Responses())

                chunks = list(
                    llm._stream_codex(
                        "create note.txt",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file"],
                        pinned_tools=["create_file"],
                    )
                )

                self.assertEqual(chunks, ["Checking the file first.", "Created note.txt."])
                self.assertEqual(getattr(chunks[0], "kind", ""), "progress")
                self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "hello")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    def test_responses_reasoning_summary_deltas_stream_as_thoughts(self):
        """Verify Responses reasoning deltas are surfaced as thought chunks."""
        events = [
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Thinking "),
            SimpleNamespace(type="response.reasoning_text.delta", delta="through it."),
            SimpleNamespace(type="response.output_text.delta", delta="Done."),
        ]

        class FakeStream:
            def __enter__(self):
                return iter(events)

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeResponses:
            def stream(self, **_kwargs):
                return FakeStream()

        chunks = list(
            llm._response_stream_text(
                SimpleNamespace(responses=FakeResponses()),
                {"model": "gpt-test", "input": "hi"},
                provider="chatgpt",
                model="gpt-test",
            )
        )

        self.assertEqual(chunks, ["Thinking ", "through it.", "Done."])
        self.assertEqual([getattr(chunk, "kind", "answer") for chunk in chunks], ["thought", "thought", "answer"])

    def test_responses_chat_requests_configured_reasoning_effort(self):
        """Verify Responses calls request the configured chat reasoning effort."""
        old_effort = getattr(config, "CHAT_REASONING_EFFORT", "")
        try:
            config.CHAT_REASONING_EFFORT = "high"

            class FakeResponses:
                def __init__(self):
                    self.calls = []

                def create(self, **kwargs):
                    self.calls.append(kwargs)
                    return SimpleNamespace(id="resp_1", output_text="Done.", output=[])

            responses = FakeResponses()
            response = llm._responses_create_with_retries(
                SimpleNamespace(responses=responses),
                {"model": "gpt-test", "input": "hi"},
                provider="chatgpt",
                model="gpt-test",
            )

            self.assertEqual(response.output_text, "Done.")
            self.assertEqual(responses.calls[0]["reasoning"], {"effort": "high"})
        finally:
            config.CHAT_REASONING_EFFORT = old_effort

    @patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "0"}, clear=False)
    def test_codex_responses_tool_loop_falls_back_when_tool_output_loses_state(self):
        """Verify tool output fallback handles routes that do not retain prior calls."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                first = SimpleNamespace(
                    id="resp_1",
                    output_text="",
                    output=[
                        SimpleNamespace(
                            id="fc_1",
                            type="function_call",
                            call_id="call_1",
                            name="create_file",
                            arguments='{"path":"fallback.txt","content":"Fallback"}',
                        )
                    ],
                )
                final = SimpleNamespace(id="resp_2", output_text="Created fallback.txt.", output=[])

                class MissingToolCall(RuntimeError):
                    status_code = 400

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        if len(self.calls) == 1:
                            return first
                        if kwargs.get("previous_response_id"):
                            raise MissingToolCall(
                                "No tool call found for function call output with call_id call_1."
                            )
                        return final

                responses = Responses()
                client = SimpleNamespace(responses=responses)

                text = "".join(
                    llm._stream_codex(
                        "create fallback file",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file"],
                        pinned_tools=["create_file"],
                    )
                )

                self.assertEqual(text, "Created fallback.txt.")
                self.assertEqual((root / "fallback.txt").read_text(encoding="utf-8"), "Fallback")
                self.assertEqual(len(responses.calls), 3)
                self.assertEqual(responses.calls[1]["input"][0]["type"], "function_call_output")
                self.assertNotIn("previous_response_id", responses.calls[2])
                self.assertEqual(responses.calls[2]["input"][0]["type"], "function_call")
                self.assertEqual(responses.calls[2]["input"][1]["type"], "function_call_output")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    @patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "0"}, clear=False)
    def test_codex_responses_tool_loop_handles_store_must_be_false_routes(self):
        """Verify store=false-only Responses routes still complete tool loops."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                first = SimpleNamespace(
                    id="resp_1",
                    output_text="",
                    output=[
                        SimpleNamespace(
                            id="fc_1",
                            type="function_call",
                            call_id="call_1",
                            name="create_file",
                            arguments='{"path":"store_false.txt","content":"Store false"}',
                        )
                    ],
                )
                final = SimpleNamespace(id="resp_2", output_text="Created store_false.txt.", output=[])

                class StoreMustBeFalse(RuntimeError):
                    status_code = 400

                class MissingToolCall(RuntimeError):
                    status_code = 400

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        if kwargs.get("store") is True:
                            raise StoreMustBeFalse("{'detail': 'Store must be set to false'}")
                        if len(self.calls) <= 2:
                            return first
                        if kwargs.get("previous_response_id"):
                            raise MissingToolCall(
                                "No tool call found for function call output with call_id call_1."
                            )
                        return final

                responses = Responses()
                client = SimpleNamespace(responses=responses)

                text = "".join(
                    llm._stream_codex(
                        "create store false file",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file"],
                        pinned_tools=["create_file"],
                    )
                )

                self.assertEqual(text, "Created store_false.txt.")
                self.assertEqual((root / "store_false.txt").read_text(encoding="utf-8"), "Store false")
                self.assertGreaterEqual(len(responses.calls), 5)
                self.assertIs(responses.calls[0]["store"], True)
                self.assertIs(responses.calls[1]["store"], False)
                self.assertIs(responses.calls[2]["store"], True)
                self.assertIs(responses.calls[3]["store"], False)
                self.assertNotIn("previous_response_id", responses.calls[-1])
                self.assertEqual(responses.calls[-1]["input"][0]["type"], "function_call")
                self.assertEqual(responses.calls[-1]["input"][1]["type"], "function_call_output")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    @patch.dict("os.environ", {"WISP_UNIFIED_CHAT_TOOL_LOOP": "0"}, clear=False)
    def test_codex_stream_required_tool_loop_can_write_local_file(self):
        """Verify stream-required ChatGPT Responses models still execute tools."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm._route_capabilities.clear()
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                class StreamRequiredError(RuntimeError):
                    status_code = 400

                first_item = SimpleNamespace(
                    type="function_call",
                    call_id="call_1",
                    name="create_file",
                    arguments='{"path":"streamed.txt","content":"Hello world"}',
                )

                class ResponseStream:
                    def __init__(self, events):
                        self.events = events

                    def __enter__(self):
                        return iter(self.events)

                    def __exit__(self, *_args):
                        return False

                class Responses:
                    def __init__(self):
                        self.create_calls = []
                        self.stream_calls = []

                    def create(self, **kwargs):
                        self.create_calls.append(kwargs)
                        raise StreamRequiredError("stream must be true for this model")

                    def stream(self, **kwargs):
                        self.stream_calls.append(kwargs)
                        if len(self.stream_calls) == 1:
                            return ResponseStream([
                                SimpleNamespace(type="response.output_item.done", item=first_item),
                                SimpleNamespace(
                                    type="response.completed",
                                    response=SimpleNamespace(id="resp_1", output_text="", output=[first_item]),
                                ),
                            ])
                        return ResponseStream([
                            SimpleNamespace(type="response.output_text.delta", delta="Created streamed.txt."),
                            SimpleNamespace(
                                type="response.completed",
                                response=SimpleNamespace(id="resp_2", output_text="", output=[]),
                            ),
                        ])

                responses = Responses()
                client = SimpleNamespace(responses=responses)

                text = "".join(
                    llm._stream_codex(
                        "create a hello world file",
                        "gpt-test-stream",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file", "write_file", "read_file", "list_files"],
                        pinned_tools=["create_file"],
                    )
                )

                self.assertEqual(text, "Created streamed.txt.")
                self.assertEqual((root / "streamed.txt").read_text(encoding="utf-8"), "Hello world")
                self.assertEqual(len(responses.create_calls), 2)
                self.assertEqual(len(responses.stream_calls), 2)
                self.assertIn("tools", responses.stream_calls[0])
                self.assertIs(responses.stream_calls[0]["store"], True)
                self.assertEqual(responses.stream_calls[1]["previous_response_id"], "resp_1")
                self.assertIs(responses.stream_calls[1]["store"], True)
                self.assertIn("instructions", responses.stream_calls[1])
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)
            llm._route_capabilities.clear()

    def test_chatgpt_stream_required_error_wording_is_detected(self):
        """Verify ChatGPT stream-required wording is treated as recoverable."""
        exc = Exception("stream isn't true")

        self.assertTrue(llm._requires_stream_error(exc))
        self.assertTrue(llm._stream_mode_error(exc))

    def test_chatgpt_responses_stream_sets_explicit_stream_body(self):
        """Verify Responses streaming sends stream=true for ChatGPT Codex."""
        calls = []

        class StreamContext:
            """Minimal Responses stream context manager."""

            def __enter__(self):
                """Enter the fake stream."""
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, *_args):
                """Exit the fake stream."""
                return False

        class Responses:
            """Fake Responses API."""

            def stream(self, **kwargs):
                """Record streaming kwargs."""
                calls.append(kwargs)
                return StreamContext()

        client = SimpleNamespace(responses=Responses())
        text = "".join(
            llm._response_stream_text(
                client,
                {
                    "model": "gpt-5.5-test",
                    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
                    "store": False,
                },
                provider="chatgpt",
                model="gpt-5.5-test",
            )
        )

        self.assertEqual(text, "OK")
        self.assertEqual(calls[0]["extra_body"]["stream"], True)


if __name__ == "__main__":
    unittest.main()
