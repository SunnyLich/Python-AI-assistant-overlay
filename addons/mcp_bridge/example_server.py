"""A tiny, dependency-free MCP server over stdio for testing the bridge.

Speaks JSON-RPC 2.0, one message per line, exactly like a real MCP server.
The tools span the spectrum from trivial round-trip proofs (``echo``, ``add``)
to ones that show MCP doing real work the model otherwise can't: reading the
clock, reading a file, and listing a directory. Logs go to stderr only;
stdout carries protocol messages and nothing else.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import sys
from pathlib import Path

_MAX_FILE_CHARS = 4000

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the text you send. (Round-trip proof.)",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo."}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers and return the sum.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First addend."},
                "b": {"type": "number", "description": "Second addend."},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "current_time",
        "description": "Return the current local and UTC time. Use when the user asks what time or date it is.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_text_file",
        "description": "Read a UTF-8 text file from disk and return its contents (truncated). Use to inspect a file the user names by path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to a text file."},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 4000)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": "List the entries in a directory (folders get a trailing slash). Use to see what files exist somewhere.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path (default current directory)."}},
            "required": [],
        },
    },
    {
        "name": "system_info",
        "description": "Report the OS, Python version, working directory, and process id of this MCP server.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def _send(obj: dict) -> None:
    """Write one JSON-RPC message and flush."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _ok(mid, result) -> None:
    """Send a JSON-RPC result."""
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _err(mid, code, message) -> None:
    """Send a JSON-RPC error."""
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def _tool_echo(args: dict) -> str:
    """Return the text we were given."""
    return str(args.get("text", ""))


def _tool_add(args: dict) -> str:
    """Return the sum of two numbers."""
    return str(float(args.get("a", 0)) + float(args.get("b", 0)))


def _tool_current_time(_args: dict) -> str:
    """Return local and UTC time the model can't know on its own."""
    local = datetime.datetime.now().astimezone()
    utc = datetime.datetime.now(datetime.timezone.utc)
    return f"local: {local.isoformat(timespec='seconds')}\nutc:   {utc.isoformat(timespec='seconds')}"


def _tool_read_text_file(args: dict) -> str:
    """Read a text file from disk, capped to keep results small."""
    path = Path(str(args.get("path", "")).strip()).expanduser()
    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    cap = int(args.get("max_chars", _MAX_FILE_CHARS) or _MAX_FILE_CHARS)
    data = path.read_text(encoding="utf-8", errors="replace")
    clipped = data[:cap]
    if len(data) > cap:
        clipped += f"\n... [truncated, {len(data) - cap} more chars]"
    return clipped


def _tool_list_directory(args: dict) -> str:
    """List directory entries, folders marked with a trailing slash."""
    path = Path(str(args.get("path", ".") or ".").strip()).expanduser()
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    names = sorted(os.listdir(path))[:200]
    lines = [name + ("/" if (path / name).is_dir() else "") for name in names]
    return "\n".join(lines) or "(empty)"


def _tool_system_info(_args: dict) -> str:
    """Report basic environment facts about this server process."""
    return (
        f"os:     {platform.platform()}\n"
        f"python: {platform.python_version()}\n"
        f"cwd:    {os.getcwd()}\n"
        f"pid:    {os.getpid()}"
    )


_HANDLERS = {
    "echo": _tool_echo,
    "add": _tool_add,
    "current_time": _tool_current_time,
    "read_text_file": _tool_read_text_file,
    "list_directory": _tool_list_directory,
    "system_info": _tool_system_info,
}


def _call(name: str, args: dict) -> str:
    """Dispatch one tool call to its handler."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"unknown tool: {name}")
    return handler(args or {})


def main() -> int:
    """Read JSON-RPC lines from stdin and answer them until EOF."""
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _ok(mid, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "wisp-example-mcp", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue  # notification: no response
        elif method == "tools/list":
            _ok(mid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            try:
                text = _call(name, args)
                _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as exc:
                _ok(mid, {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True})
        elif mid is not None:
            _err(mid, -32601, f"unknown method: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
