"""MCP Bridge addon.

Connects to one or more Model Context Protocol (MCP) servers listed in
``servers.json`` and re-exposes every tool they advertise as a Wisp tool via
``get_tools()``. One addon imports an entire external toolkit.

This is the *client* side of MCP: each server stays its own process (any
language, local or via a launcher), and this addon translates between Wisp's
tool registry and the MCP wire protocol. The client is synchronous on purpose
so it matches the addon host's synchronous hook model.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent
SERVERS_FILE = ADDON_DIR / "servers.json"
_DEFAULT_TIMEOUT = 8.0  # stay under the host's 8s execute_tool cap

# Live clients keyed by server name, and a map from the tool name we expose to
# Wisp back to (server name, original MCP tool name). Both persist for the life
# of the addon host process, so get_tools() and executors share them.
_clients: "dict[str, MCPStdioClient]" = {}
_tool_index: "dict[str, tuple[str, str]]" = {}


_TRUE = {"1", "true", "yes", "on"}


def _setting(key: str, default):
    """Read a persisted addon setting, falling back to *default* when run standalone."""
    try:
        from core.addon_manager import addon_setting
        value = addon_setting("mcp-bridge", key, default)
        return default if value is None else value
    except Exception:
        return default


def _setting_bool(key: str, default: bool) -> bool:
    """Read a setting as a boolean."""
    return str(_setting(key, "true" if default else "false")).strip().lower() in _TRUE


def _setting_int(key: str, default: int) -> int:
    """Read a setting as an integer."""
    try:
        return int(float(str(_setting(key, default)).strip()))
    except Exception:
        return int(default)


def _setting_float(key: str, default: float) -> float:
    """Read a setting as a float."""
    try:
        return float(str(_setting(key, default)).strip())
    except Exception:
        return float(default)


def _log(message: str) -> None:
    """Append a line to the addon host's stderr (captured in addon logs)."""
    print(f"{_setting('log_prefix', '[mcp-bridge]')} {message}", file=sys.stderr, flush=True)


class MCPStdioClient:
    """A minimal synchronous MCP client speaking JSON-RPC 2.0 over stdio."""

    def __init__(self, name, command, args=None, env=None, cwd=None, timeout=_DEFAULT_TIMEOUT):
        """Initialize the client (does not spawn the server yet)."""
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.cwd = cwd
        self.timeout = float(timeout)
        self._proc: "subprocess.Popen[str] | None" = None
        self._ids = itertools.count(1)
        self._write_lock = threading.Lock()
        self._cond = threading.Condition()
        self._responses: "dict[int, dict]" = {}
        self._alive = False

    def start(self) -> None:
        """Spawn the server and run the MCP initialize handshake (idempotent)."""
        if self._proc and self._proc.poll() is None:
            return
        env = os.environ.copy()
        if self.env:
            env.update({str(k): str(v) for k, v in self.env.items()})
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self.cwd,
            env=env,
        )
        self._alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "wisp-mcp-bridge", "version": "1.0.0"},
        })
        self._notify("notifications/initialized", {})

    def list_tools(self) -> list:
        """Return the server's advertised tools (name/description/inputSchema)."""
        result = self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict) -> str:
        """Invoke one tool and return its text content (joined text blocks)."""
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        parts = [
            str(block.get("text", ""))
            for block in (result.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(parts).strip()
        if result.get("isError"):
            return f"[tool error] {text}" if text else "[tool error]"
        return text or "(no output)"

    def stop(self) -> None:
        """Terminate the server process."""
        self._alive = False
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass

    # -- internals -----------------------------------------------------------

    def _read_loop(self) -> None:
        """Parse server stdout; stash responses by id and wake waiters."""
        try:
            assert self._proc and self._proc.stdout
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if isinstance(msg, dict) and msg.get("id") is not None and ("result" in msg or "error" in msg):
                    with self._cond:
                        self._responses[msg["id"]] = msg
                        self._cond.notify_all()
        finally:
            self._alive = False
            with self._cond:
                self._cond.notify_all()

    def _send(self, obj: dict) -> None:
        """Write one JSON-RPC message to the server."""
        if not self._proc or self._proc.stdin is None:
            raise RuntimeError(f"MCP server {self.name!r} is not running")
        with self._write_lock:
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict:
        """Send a request and block until its response (or timeout)."""
        rid = next(self._ids)
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        with self._cond:
            while rid not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._alive:
                    raise TimeoutError(f"MCP {self.name!r} {method} timed out")
                self._cond.wait(remaining)
            msg = self._responses.pop(rid)
        if "error" in msg:
            raise RuntimeError(f"MCP {self.name!r} error: {msg['error'].get('message')}")
        result = msg.get("result")
        return result if isinstance(result, dict) else {}


def _load_servers() -> list:
    """Read servers.json; return a list of server config dicts."""
    try:
        data = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception as exc:
        _log(f"servers.json unreadable: {exc}")
        return []
    servers = data.get("servers") if isinstance(data, dict) else data
    return [s for s in (servers or []) if isinstance(s, dict)]


def _resolve_command(server: dict) -> "tuple[str, list[str]]":
    """Map 'python' to this interpreter and resolve bundled script paths."""
    command = str(server.get("command") or "").strip()
    if command in ("", "python", "python3"):
        command = sys.executable
    elif not Path(command).is_absolute() and (ADDON_DIR / command).exists():
        command = str(ADDON_DIR / command)
    args = []
    for raw in (server.get("args") or []):
        candidate = ADDON_DIR / str(raw)
        args.append(str(candidate) if candidate.exists() else str(raw))
    return command, args


def _get_client(server: dict) -> MCPStdioClient:
    """Return a started client for this server, creating it once and caching."""
    name = str(server.get("name") or "server").strip() or "server"
    client = _clients.get(name)
    if client is None:
        command, args = _resolve_command(server)
        client = MCPStdioClient(
            name, command, args,
            env=server.get("env"),
            cwd=server.get("cwd"),
            timeout=float(server.get("timeout", _setting_float("call_timeout_seconds", _DEFAULT_TIMEOUT))),
        )
        _clients[name] = client
    client.start()
    return client


def _safe_name(name: str) -> str:
    """Coerce a name into the [A-Za-z0-9_-] set the tool registry requires."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return cleaned or "mcp_tool"


def _make_executor(exposed: str):
    """Build the executor closure for one exposed tool."""
    def _exec(inputs: dict) -> str:
        """Forward a tool call to its MCP server and return the text result."""
        mapping = _tool_index.get(exposed)
        if not mapping:
            return f"Unknown MCP tool: {exposed}"
        server_name, original = mapping
        client = _clients.get(server_name)
        if client is None:
            return f"MCP server {server_name!r} is not connected"
        try:
            return client.call_tool(original, inputs or {})
        except Exception as exc:
            return f"[MCP error] {exc}"
    return _exec


def get_tools() -> list:
    """Discover every enabled server's tools and expose them to Wisp."""
    tools = []
    _tool_index.clear()
    tag = str(_setting("description_tag", "")).strip()
    cap = max(0, _setting_int("max_tools_per_server", 50))
    announce = _setting_bool("announce_tools", True)
    for server in _load_servers():
        if not server.get("enabled", True):
            continue
        name = str(server.get("name") or "server").strip() or "server"
        try:
            mcp_tools = _get_client(server).list_tools()
        except Exception as exc:
            _log(f"server {name!r} unavailable: {exc}")
            continue
        count = 0
        for tool in mcp_tools[:cap]:
            original = str(tool.get("name") or "").strip()
            if not original:
                continue
            exposed = _safe_name(f"mcp_{name}_{original}")
            _tool_index[exposed] = (name, original)
            description = f"[MCP:{name}] " + str(tool.get("description") or original)
            if tag:
                description = f"{tag} {description}"
            tools.append({
                "name": exposed,
                "description": description,
                "input_schema": tool.get("inputSchema") or {"type": "object", "properties": {}, "required": []},
                "executor": _make_executor(exposed),
            })
            count += 1
        if announce:
            _log(f"server {name!r}: exposed {count} tool(s)")
    return tools


def get_settings() -> list:
    """Settings shown in the Addon Manager — one of each control type, all wired."""
    return [
        {"key": "log_prefix", "label": "Log prefix", "type": "text",
         "default": "[mcp-bridge]",
         "help": "Prefix for this addon's log lines — change it and watch the Logs view."},
        {"key": "announce_tools", "label": "Log discovered tools", "type": "bool",
         "default": "true",
         "help": "When on, log how many tools each server exposed."},
        {"key": "description_tag", "label": "Tool description tag", "type": "text",
         "default": "",
         "help": "Optional text prepended to every exposed tool's description, e.g. [demo]."},
        {"key": "call_timeout_seconds", "label": "Call timeout (seconds)", "type": "choice",
         "options": ["4", "6", "8"], "default": "8",
         "help": "How long to wait for an MCP tool result before giving up."},
        {"key": "max_tools_per_server", "label": "Max tools per server", "type": "number",
         "default": "50",
         "help": "Cap on how many tools each server may expose to Wisp."},
    ]


def on_startup(app_context) -> None:
    """Lifecycle hook: announce startup (connections happen lazily)."""
    _log("starting")


def on_shutdown() -> None:
    """Lifecycle hook: terminate every connected MCP server."""
    for client in list(_clients.values()):
        try:
            client.stop()
        except Exception:
            pass
    _clients.clear()
    _tool_index.clear()


if __name__ == "__main__":
    # Standalone smoke test against the bundled example server.
    specs = get_tools()
    print(f"discovered {len(specs)} MCP tool(s):")
    for spec in specs:
        print(f"  - {spec['name']}: {spec['description']}")
    by_name = {spec["name"]: spec for spec in specs}
    if "mcp_example_echo" in by_name:
        print("echo('hi there') ->", by_name["mcp_example_echo"]["executor"]({"text": "hi there"}))
    if "mcp_example_add" in by_name:
        print("add(2, 3) ->", by_name["mcp_example_add"]["executor"]({"a": 2, "b": 3}))
    on_shutdown()
