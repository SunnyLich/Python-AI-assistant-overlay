"""Tool execution facade for scoped agent tasks."""
from __future__ import annotations

import subprocess
import sys
import fnmatch
import os
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

    def edit_file(self, path: str, old: str, new: str) -> ToolResult:
        """Edit a file by replacing one exact text block."""
        return self.patch_file(path, old, new, action_name="edit_file")

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
        return self._result(action_name, True, f"{self.workspace.relative(path)} patched", {"replacements": count})

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
        is_git_command = self._is_git_command(clean_args)
        if not self.permissions.allow_shell and not (self.permissions.allow_git and is_git_command):
            raise PermissionDenied("Shell commands are disabled for this task.")
        if not self._is_command_allowed(clean_args):
            raise PermissionDenied(f"Command is not allowlisted: {' '.join(clean_args)}")
        if self.permissions.allow_git and is_git_command:
            no_repo = self._not_git_repo_result(clean_args)
            if no_repo is not None and self._is_read_only_git_command(clean_args):
                return no_repo
            self._approve("git", {"args": clean_args})
            if no_repo is not None:
                return no_repo
        else:
            self._approve("run_command", {"args": clean_args})
        display_args = list(clean_args)
        exec_args = list(clean_args)
        if exec_args[0].lower() in {"python", "py"}:
            exec_args[0] = sys.executable
        elif exec_args[0].lower() in {"pytest", "ruff", "mypy"}:
            exec_args = [sys.executable, "-m", *exec_args]
        env = None
        if self._is_git_commit_command(clean_args):
            env = os.environ.copy()
            env.setdefault("GIT_AUTHOR_NAME", "Wisp Agent")
            env.setdefault("GIT_AUTHOR_EMAIL", "wisp-agent@example.invalid")
            env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
            env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])

        try:
            completed = subprocess.run(
                exec_args,
                cwd=str(self.workspace.root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
                env=env,
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
        lowered = [arg.lower() for arg in args]
        if self.permissions.allow_git and self._is_git_command_allowed(args):
            return True
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

    def git_init(self) -> ToolResult:
        """Initialize a Git repository in the scoped workspace."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self._retag_result(self.run_command(["git", "init"], timeout_seconds=10), "git_init")

    def git_status(self) -> ToolResult:
        """Handle git status for agent toolbox."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self._retag_result(self.run_command(["git", "status", "--short"], timeout_seconds=5), "git_status")

    def git_diff(self) -> ToolResult:
        """Handle git diff for agent toolbox."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self._retag_result(self.run_command(["git", "diff", "--", "."], timeout_seconds=5), "git_diff")

    def git_add(self, paths: Sequence[str]) -> ToolResult:
        """Stage scoped paths in Git."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        clean_paths = [str(path) for path in paths if str(path)]
        return self._retag_result(self.run_command(["git", "add", *clean_paths], timeout_seconds=10), "git_add")

    def git_commit(self, message: str) -> ToolResult:
        """Create a Git commit with a simple message."""
        if not self.permissions.allow_git:
            raise PermissionDenied("Git is disabled for this task.")
        return self._retag_result(self.run_command(["git", "commit", "-m", str(message or "")], timeout_seconds=20), "git_commit")

    @staticmethod
    def _retag_result(result: ToolResult, tool: str) -> ToolResult:
        """Return a command result under a dedicated tool name."""
        return ToolResult(tool=tool, ok=result.ok, message=result.message, data=result.data)

    def _not_git_repo_result(self, args: list[str]) -> ToolResult | None:
        """Handle not git repo result for agent toolbox."""
        if not self._is_git_command(args) or self._is_git_init_command(args):
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

    @staticmethod
    def _is_git_command(args: list[str]) -> bool:
        """Return whether args describe a git command."""
        return bool(args) and args[0].lower() == "git"

    @staticmethod
    def _is_git_init_command(args: list[str]) -> bool:
        """Return whether args are a scoped git init command."""
        lowered = [arg.lower() for arg in args]
        return lowered == ["git", "init"]

    @staticmethod
    def _is_git_commit_command(args: list[str]) -> bool:
        """Return whether args are a scoped git commit command."""
        lowered = [arg.lower() for arg in args]
        return len(lowered) >= 2 and lowered[:2] == ["git", "commit"]

    def _is_git_command_allowed(self, args: list[str]) -> bool:
        """Allow safe Git lifecycle commands within the scoped workspace."""
        if not self._is_git_command(args) or len(args) < 2:
            return False
        lowered = [arg.lower() for arg in args]
        verb = lowered[1]
        if verb == "init":
            return lowered == ["git", "init"]
        if verb == "status":
            return all(arg in {"git", "status", "--short", "--porcelain", "-s"} for arg in lowered)
        if verb == "diff":
            return len(args) == 2 or lowered[2:] == ["--", "."]
        if verb == "add":
            return self._is_git_add_allowed(args[2:])
        if verb == "commit":
            return self._is_git_commit_allowed(args[2:])
        return False

    def _is_git_add_allowed(self, pathspecs: list[str]) -> bool:
        """Allow git add only for scoped paths."""
        if not pathspecs:
            return False
        paths = [arg for arg in pathspecs if arg != "--"]
        if not paths:
            return False
        for path in paths:
            if path.startswith("-") or path.startswith(":"):
                return False
            if path == ".":
                if self._has_blocked_files():
                    return False
                continue
            try:
                self.workspace.resolve(path)
            except Exception:
                return False
        return True

    @staticmethod
    def _is_git_commit_allowed(args: list[str]) -> bool:
        """Allow simple message-based commits."""
        if len(args) != 2 or args[0] not in {"-m", "--message"}:
            return False
        return bool(args[1].strip())

    def _has_blocked_files(self) -> bool:
        """Return whether git add . would include files blocked from agent access."""
        blocked = getattr(self.workspace, "blocked_globs", [])
        if not blocked:
            return False
        for path in self.workspace.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = str(path.resolve().relative_to(self.workspace.root)).replace("\\", "/")
            except ValueError:
                continue
            if any(fnmatch.fnmatch(rel, pattern) for pattern in blocked):
                return True
        return False

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
        decision = self._approval_callback(request)
        approved = bool(decision)
        feedback = ""
        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
            feedback = str(decision.get("feedback") or "").strip()
        if not approved:
            if feedback:
                raise PermissionDenied(f"User requested changes for {action}: {feedback}")
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
