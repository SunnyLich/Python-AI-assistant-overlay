import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.tool_registry import ToolRegistry, ToolSpec


class ToolRegistryTests(unittest.TestCase):
    def test_registers_builtin_schema_and_executes_callback(self):
        registry = ToolRegistry(plugin_dir=Path("does-not-exist"))
        registry.register_builtin(
            ToolSpec(
                name="echo_builtin",
                description="Echo input",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                executor=lambda inputs: inputs["text"],
            )
        )

        self.assertEqual(registry.execute("echo_builtin", {"text": "hello"}), "hello")
        self.assertEqual(registry.schemas()[0]["name"], "echo_builtin")

    def test_discovers_and_executes_script_tool(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool_dir = root / "echo"
            tool_dir.mkdir()
            (tool_dir / "tool.toml").write_text(
                textwrap.dedent(
                    """
                    name = "echo_script"
                    description = "Echo text from a subprocess."
                    enabled = true
                    timeout_seconds = 5
                    max_output_chars = 200

                    [input_schema]
                    type = "object"
                    required = ["text"]

                    [input_schema.properties.text]
                    type = "string"
                    description = "Text to echo."
                    """
                ).strip(),
                encoding="utf-8",
            )
            (tool_dir / "tool.py").write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys

                    payload = json.loads(sys.stdin.read())
                    text = payload["inputs"]["text"]
                    print(json.dumps({"content": "echo:" + text}))
                    """
                ).strip(),
                encoding="utf-8",
            )

            registry = ToolRegistry(plugin_dir=root)
            schemas = registry.schemas(include_server_tools=False)

            self.assertEqual(len(schemas), 1)
            self.assertEqual(schemas[0]["name"], "echo_script")
            self.assertEqual(
                registry.execute("echo_script", {"text": "hi"}),
                "echo:hi",
            )

    def test_ignores_disabled_script_tool(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool_dir = root / "disabled"
            tool_dir.mkdir()
            (tool_dir / "tool.toml").write_text(
                'name = "disabled_tool"\nenabled = false\n',
                encoding="utf-8",
            )
            (tool_dir / "tool.py").write_text(
                'print("{}")\n',
                encoding="utf-8",
            )

            registry = ToolRegistry(plugin_dir=root)

            self.assertEqual(registry.schemas(include_server_tools=False), [])


if __name__ == "__main__":
    unittest.main()
