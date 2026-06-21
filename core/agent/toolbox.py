"""Tool execution facade for scoped agent tasks."""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from core.agent.runtime import (
    AgentPermissions,
    ApprovalCallback,
    LogCallback,
    PermissionDenied,
    ToolResult,
)
from core.agent.workspace import ScopedWorkspace


class AgentToolbox:
    """Scoped tools available to the future autonomous agent loop."""

    _BASE_COMMAND_ALLOWLIST: tuple[tuple[str, ...], ...] = (
        ("python", "-m", "py_compile"),
        ("python", "-m", "unittest"),
        ("python", "-m", "pytest"),
        ("python", "-m", "ruff"),
        ("python", "-m", "mypy"),
        ("pytest",),
        ("ruff",),
        ("mypy",),
        ("rg",),
        ("node", "--check"),
    )
    _PROJECT_COMMAND_ALLOWLIST: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        (("npm", "test"), ("package.json",)),
        (("npm", "run", "build"), ("package.json",)),
        (("cargo", "test"), ("Cargo.toml",)),
        (("go", "test"), ("go.mod",)),
    )
    _GIT_COMMAND_ALLOWLIST: tuple[tuple[str, ...], ...] = (
        ("git", "status"),
        ("git", "diff"),
    )

    def __init__(
        self,
        workspace: ScopedWorkspace,
        permissions: AgentPermissions,
        *,
        log: LogCallback | None = None,
        approval_callback: ApprovalCallback | None = None,
        require_approval: bool = False,
        permission_modes: dict[str, str] | None = None,
    ):
        """Initialize the agent toolbox instance."""
        self.workspace = workspace
        self.permissions = permissions
        self._log = log
        self._approval_callback = approval_callback
        self._require_approval = require_approval
        self._permission_modes = permission_modes or {}

    def list_files(self, folder: str | Path = ".", *, limit: int = 300) -> ToolResult:
        """List files."""
        files = self.workspace.list_files(folder, limit=limit)
        return self._result("list_files", True, f"{len(files)} file(s)", files)

    def read_file(self, path: str, *, max_chars: int = 20_000) -> ToolResult:
        """Read file."""
        text = self.workspace.read_text(path, max_chars=max_chars)
        return self._result("read_file", True, self.workspace.relative(path), text)

    def create_file(self, path: str, content: str) -> ToolResult:
        """Create file."""
        if not self.permissions.allow_file_create:
            raise PermissionDenied("Creating files is disabled for this task.")
        self._approve("create_file", {"path": path, "chars": len(content)})
        self.workspace.write_text(path, content, create=True, edit=False)
        return self._result("create_file", True, self.workspace.relative(path))

    def write_file(self, path: str, content: str) -> ToolResult:
        """Write file."""
        resolved = self.workspace.resolve(path)
        exists = resolved.exists()
        if exists and not self.permissions.allow_file_edit:
            raise PermissionDenied("Editing files is disabled for this task.")
        if not exists and not self.permissions.allow_file_create:
            raise PermissionDenied("Creating files is disabled for this task.")
        before = resolved.read_text(encoding="utf-8", errors="replace") if exists else ""
        self._approve(
            "write_file",
            {
                "path": str(resolved),
                "exists": exists,
                "chars": len(content),
                "diff": _unified_text_diff(before, content, resolved.name),
            },
        )
        if exists:
            current = resolved.read_text(encoding="utf-8", errors="replace")
            if current != before:
                raise PermissionDenied("write_file refused because the file changed after approval preview.")
        elif resolved.exists():
            raise PermissionDenied("write_file refused because the file was created after approval preview.")
        self.workspace.write_text(
            path,
            content,
            create=self.permissions.allow_file_create,
            edit=self.permissions.allow_file_edit,
        )
        return self._result("write_file", True, self.workspace.relative(path))

    def patch_file(self, path: str, old: str, new: str, *, action_name: str = "patch_file") -> ToolResult:
        """Handle patch file for agent toolbox."""
        resolved = self.workspace.resolve(path)
        before = resolved.read_text(encoding="utf-8", errors="replace")
        count = before.count(old)
        if count != 1:
            raise ValueError(f"Patch expected exactly 1 match, found {count}.")
        after = before.replace(old, new, 1)
        self._approve(
            action_name,
            {
                "path": str(resolved),
                "old_chars": len(old),
                "new_chars": len(new),
                "diff": _unified_text_diff(before, after, resolved.name),
            },
        )
        current = resolved.read_text(encoding="utf-8", errors="replace")
        if current != before:
            raise PermissionDenied(f"{action_name} refused because the file changed after approval preview.")
        count = self.workspace.patch_text(
            path,
            old,
            new,
            edit=self.permissions.allow_file_edit,
        )
        return self._result("patch_file", True, f"{self.workspace.relative(path)} patched", {"replacements": count})

    def delete_file(self, path: str) -> ToolResult:
        """Delete file."""
        self._approve("delete_file", {"path": path})
        self.workspace.delete_file(path, delete=self.permissions.allow_file_delete)
        return self._result("delete_file", True, self.workspace.relative(path))

    def run_command(self, args: Sequence[str], *, timeout_seconds: int = 30) -> ToolResult:
        """Run command."""
        clean_args = [str(arg) for arg in args if str(arg)]
        if not clean_args:
            raise ValueError("Command cannot be empty.")
        if not self.permissions.allow_shell and not self._is_read_only_git_command(clean_args):
            raise PermissionDenied("Shell commands are disabled for this task.")
        if not self._is_command_allowed(clean_args):
            raise PermissionDenied(f"Command is not allowlisted: {' '.join(clean_args)}")
        if not self._is_read_only_git_command(clean_args):
            self._approve("run_command", {"args": clean_args})
        else:
            self._approve("git", {"args": clean_args})
            no_repo = self._not_git_repo_result(clean_args)
            if no_repo is not None:
                return no_repo
        display_args = list(clean_args)
        exec_args = list(clean_args)
        if exec_args[0].lower() in {"python", "py"}:
            exec_args[0] = sys.executable
        elif exec_args[0].lower() in {"pytest", "ruff", "mypy"}:
            exec_args = [sys.executable, "-m", *exec_args]

        try:
            completed = subprocess.run(
                exec_args,
                cwd=str(self.workspace.root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            command = " ".join(clean_args)
            data = {
                "returncode": None,
                "stdout": (exc.stdout or "")[-20_000:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-20_000:] if isinstance(exc.stderr, str) else "",
                "timeout_seconds": timeout_seconds,
            }
            return self._result("run_command", False, f"timed out after {timeout_seconds}s: {command}", data)
        data = {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-20_000:],
            "stderr": completed.stderr[-20_000:],
        }
        return self._result(
            "run_command",
            completed.returncode == 0,
            f"exit {completed.returncode}: {' '.join(display_args)}",
            data,
        )

    def _is_command_allowed(self, args: list[str]) -> bool:
        """Return whether command allowed is true."""
        allowed = list(self._BASE_COMMAND_ALLOWLIST)
        if self.permissions.allow_git:
            allowed.extend(self._GIT_COMMAND_ALLOWLIST)
        lowered = [arg.lower() for arg in args]
        for prefix in allowed:
            if lowered[: len(prefix)] == list(prefix):
                return True
        for prefix, required_files in self._PROJECT_COMMAND_ALLOWLIST:
            if lowered[: len(prefix)] != list(prefix):
                continue
            if all((self.workspace.root / required).exists() for required in required_files):
                return True
        return False

    def verification_commands(self) -> list[list[str]]:
        """Handle verification commands for agent toolbox."""
        commands = []
        try:
            python_files = [
                path for path in self.workspace.list_files(".", limit=50)
                if path.endswith(".py")
            ][:10]
        except Exception:
            python_files = []
        commands.extend(["python", "-m", "py_compile", path] for path in python_files)
        commands.extend([
            ["python", "-m", "unittest"],
            ["python", "-m", "pytest"],
            ["pytest"],
            ["python", "-m", "ruff", "check", "."],
            ["ruff", "check", "."],
            ["python", "-m", "mypy", "."],
            ["mypy", "."],
        ])
        if (self.workspace.root / "package.json").exists():
            commands.extend([["npm", "test"], ["npm", "run", "build"]])
        if (self.workspace.root / "Cargo.toml").exists():
            commands.append(["cargo", "test"])
        if (self.workspace.root / "go.mod").exists():
            commands.append(["go", "test", "./..."])
        return [cmd for cmd in commands if self._is_command_allowed(cmd)]

    def git_status(self) -> ToolResult:
        """Handle git status for agent toolbox."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self.run_command(["git", "status", "--short"], timeout_seconds=5)

    def git_diff(self) -> ToolResult:
        """Handle git diff for agent toolbox."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self.run_command(["git", "diff", "--", "."], timeout_seconds=5)

    def _not_git_repo_result(self, args: list[str]) -> ToolResult | None:
        """Handle not git repo result for agent toolbox."""
        if not self._is_read_only_git_command(args):
            return None
        if self._find_git_root() is not None:
            return None
        command = " ".join(args)
        return self._result(
            "run_command",
            False,
            f"exit 128: {command}",
            {"returncode": 128, "stdout": "", "stderr": "fatal: not a git repository"},
        )

    def _find_git_root(self) -> Path | None:
        """Find git root."""
        root = self.workspace.root
        for folder in (root, *root.parents):
            marker = folder / ".git"
            if marker.exists():
                return folder
        return None

    @staticmethod
    def _is_read_only_git_command(args: list[str]) -> bool:
        """Return whether read only git command is true."""
        lowered = [arg.lower() for arg in args]
        return lowered[:2] in (["git", "status"], ["git", "diff"])

    def _approve(self, action: str, details: dict) -> None:
        """Handle approve for agent toolbox."""
        category = self._permission_category(action)
        mode = str(self._permission_modes.get(category, "") or "").lower()
        if mode in {"never", "never permit", "deny"}:
            raise PermissionDenied(f"{category.replace('_', ' ').title()} permission is set to never permit.")
        if mode in {"auto", "allow", "always"}:
            return
        if not self._require_approval and mode not in {"ask", "ask permission", "ask for permission"}:
            return
        if self._approval_callback is None:
            raise PermissionDenied(f"Approval required for {action}, but no approval UI is available.")
        request = {"action": action, "details": details}
        if "path" in details:
            request["path"] = details["path"]
        if "diff" in details:
            request["diff"] = details["diff"]
        if not self._approval_callback(request):
            raise PermissionDenied(f"User declined {action}.")

    @staticmethod
    def _permission_category(action: str) -> str:
        """Handle permission category for agent toolbox."""
        if action in {"create_file", "create_file_base64"}:
            return "file_create"
        if action in {"write_file", "write_file_base64", "patch_file", "edit_file"}:
            return "file_edit"
        if action == "delete_file":
            return "file_delete"
        if action == "git":
            return "git"
        if action == "run_command":
            return "shell"
        return action

    def _result(
        self,
        tool: str,
        ok: bool,
        message: str,
        data: dict | list | str | None = None,
    ) -> ToolResult:
        """Handle result for agent toolbox."""
        result = ToolResult(tool=tool, ok=ok, message=message, data=data)
        if self._log:
            self._log(f"tool {tool}: {message}")
        return result


def _unified_text_diff(before: str, after: str, filename: str) -> str:
    """Build a compact unified diff for approval UI."""
    import difflib

    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return "\n".join(diff)[:20_000]
