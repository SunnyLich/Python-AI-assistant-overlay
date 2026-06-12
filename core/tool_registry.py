"""
core/tool_registry.py - Discover and execute model tools.

Installed script tools live under tools/installed/<tool-name>/ and need:
  - tool.toml or tool.json
  - tool.py

tool.py receives JSON on stdin:
  {"inputs": {...}, "context": {...}}

It should print JSON such as:
  {"content": "plain text returned to the model"}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config


ToolExecutor = Callable[[dict], str]

# Default keyword filters for built-in tools.
# Empty list = always send. Non-empty = only send when prompt contains a keyword.
_DEFAULT_KEYWORD_MAP: dict[str, list[str]] = {
    "git_status":   ["git", "commit", "branch", "status", "merge", "push", "pull", "stash"],
    "git_diff":     ["git", "diff", "commit", "branch", "change", "merge"],
    "github_repo":  ["github", "repo", "repository"],
    "github_issue": ["github", "issue", "pr", "pull request", "ticket"],
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    executor: ToolExecutor | None = None
    server_schema: dict | None = None
    source: str = "builtin"
    # Tools excluded from the default schema sets and offered only when a caller
    # explicitly opts in (e.g. capture_screen, gated by the screenshot setting).
    opt_in: bool = False

    def anthropic_schema(self) -> dict:
        if self.server_schema:
            return dict(self.server_schema)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    def __init__(self, plugin_dir: Path | None = None):
        self.plugin_dir = plugin_dir or Path(config.TOOL_PLUGIN_DIR)
        self._builtins: dict[str, ToolSpec] = {}
        self._scripts: dict[str, ToolSpec] | None = None
        self._keyword_map: dict[str, list[str]] = dict(_DEFAULT_KEYWORD_MAP)

    # ------------------------------------------------------------------
    # Keyword filter persistence
    # ------------------------------------------------------------------

    def load_keyword_filters(self, path: Path) -> None:
        """Load keyword→tool filters from a JSON file, falling back to defaults."""
        if path.exists():
            try:
                import json
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged = {
                        k: [w.lower().strip() for w in v]
                        for k, v in data.items()
                        if isinstance(v, list)
                    }
                    # Fill in defaults for any tool not yet in the file.
                    for name, kws in _DEFAULT_KEYWORD_MAP.items():
                        merged.setdefault(name, kws)
                    self._keyword_map = merged
                    return
            except Exception:
                pass
        self._keyword_map = dict(_DEFAULT_KEYWORD_MAP)

    def save_keyword_filters(self, path: Path) -> None:
        import json
        path.write_text(
            json.dumps(self._keyword_map, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def set_keyword_filter(self, tool_name: str, keywords: list[str]) -> None:
        self._keyword_map[tool_name] = [k.lower().strip() for k in keywords if k.strip()]

    def _tool_visible(self, name: str, prompt: str) -> bool:
        """Return True if this tool should be sent for the given prompt."""
        keywords = self._keyword_map.get(name)
        if not keywords:
            return True  # empty list or not in map → always include
        prompt_lower = prompt.lower()
        return any(kw in prompt_lower for kw in keywords)

    def filtered_schemas(self, prompt: str, include_server_tools: bool = True) -> list[dict]:
        """Anthropic schemas filtered to only tools relevant to `prompt`."""
        return [
            s.anthropic_schema()
            for s in self.list_tools(include_server_tools=include_server_tools)
            if self._tool_visible(s.name, prompt)
        ]

    def filtered_openai_schemas(self, prompt: str) -> list[dict]:
        """OpenAI schemas filtered to only tools relevant to `prompt`."""
        return [
            s.openai_schema()
            for s in self.list_tools(include_server_tools=False)
            if self._tool_visible(s.name, prompt)
        ]

    def register_builtin(self, spec: ToolSpec) -> None:
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", spec.name):
            raise ValueError(f"Invalid tool name {spec.name!r} — use only letters, digits, _ or -")
        self._builtins[spec.name] = spec

    def unregister_source(self, source: str) -> int:
        """Drop every builtin tool registered under `source` (e.g. "mod:<name>").
        Returns the number removed."""
        doomed = [name for name, spec in self._builtins.items() if spec.source == source]
        for name in doomed:
            del self._builtins[name]
        return len(doomed)

    def schemas(self, include_server_tools: bool = True) -> list[dict]:
        specs = self.list_tools(include_server_tools=include_server_tools)
        return [spec.anthropic_schema() for spec in specs]

    def openai_schemas(self) -> list[dict]:
        """Tool schemas in OpenAI function-calling format. Excludes Anthropic-only server tools."""
        specs = self.list_tools(include_server_tools=False)
        return [spec.openai_schema() for spec in specs]

    def list_tools(self, include_server_tools: bool = True) -> list[ToolSpec]:
        # Opt-in tools are kept out of the default sets; callers add them back
        # explicitly. Server tools are Anthropic-only, so the OpenAI/Groq path
        # (include_server_tools=False) drops both.
        tools = []
        for spec in self._builtins.values():
            if spec.opt_in:
                continue
            if not include_server_tools and spec.server_schema:
                continue
            tools.append(spec)
        tools.extend(self._load_script_tools().values())
        return tools

    def get_tool(self, name: str) -> ToolSpec | None:
        """Return a tool spec by name, including opt-in tools hidden from list_tools."""
        return self._builtins.get(name) or self._load_script_tools().get(name)

    def execute(self, name: str, inputs: dict) -> str:
        spec = self._builtins.get(name) or self._load_script_tools().get(name)
        if not spec:
            return f"Unknown tool: {name!r}"
        if not spec.executor:
            return f"Tool {name!r} is model-side only and cannot be executed locally."
        return spec.executor(inputs or {})

    def refresh(self) -> None:
        self._scripts = None

    def _load_script_tools(self) -> dict[str, ToolSpec]:
        if self._scripts is not None:
            return self._scripts

        loaded: dict[str, ToolSpec] = {}
        root = self.plugin_dir
        if not root.exists():
            self._scripts = loaded
            return loaded

        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            try:
                spec = self._load_script_tool(child)
            except Exception as exc:
                print(f"[tools] Skipping {child}: {exc}")
                continue
            if spec:
                loaded[spec.name] = spec

        self._scripts = loaded
        return loaded

    def _load_script_tool(self, folder: Path) -> ToolSpec | None:
        manifest_path = _first_existing(folder / "tool.toml", folder / "tool.json")
        script_path = folder / "tool.py"
        if not manifest_path or not script_path.exists():
            return None

        manifest = _load_manifest(manifest_path)
        if not _as_bool(manifest.get("enabled", True)):
            return None

        name = str(manifest.get("name") or folder.name).strip()
        if not _valid_tool_name(name):
            raise ValueError(f"invalid tool name {name!r}")

        description = str(manifest.get("description") or manifest.get("label") or name).strip()
        input_schema = manifest.get("input_schema") or {
            "type": "object",
            "properties": {},
            "required": [],
        }
        timeout = float(manifest.get("timeout_seconds", 8))
        max_output_chars = int(manifest.get("max_output_chars", 12000))

        def _executor(inputs: dict, *, _script=script_path, _timeout=timeout, _max=max_output_chars) -> str:
            return _run_script_tool(_script, inputs, timeout=_timeout, max_output_chars=_max)

        return ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            executor=_executor,
            source=str(folder),
        )


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _load_manifest(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        return _load_simple_toml(text)


def _load_simple_toml(text: str) -> dict:
    """Parse the small TOML subset used by tool manifests on Python < 3.11."""
    data: dict = {}
    section: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = [part.strip() for part in line[1:-1].split(".") if part.strip()]
            target = data
            for part in section:
                target = target.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        target = data
        for part in section:
            target = target.setdefault(part, {})
        target[key] = _parse_simple_toml_value(value)
    return data


def _parse_simple_toml_value(value: str):
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        if not body:
            return []
        return [_parse_simple_toml_value(part.strip()) for part in body.split(",")]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _run_script_tool(
    script_path: Path,
    inputs: dict,
    timeout: float,
    max_output_chars: int,
) -> str:
    payload = json.dumps({"inputs": inputs, "context": {}}, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, str(script_path)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(script_path.parent),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return f"Tool {script_path.parent.name!r} failed: {err[:max_output_chars]}"

    output = (proc.stdout or "").strip()
    if not output:
        return ""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return output[:max_output_chars]

    if isinstance(data, dict):
        content = data.get("content", data.get("text", data))
    else:
        content = data
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return content[:max_output_chars]


def _valid_tool_name(name: str) -> bool:
    if not name:
        return False
    return all(c.isalnum() or c in {"_", "-"} for c in name)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
