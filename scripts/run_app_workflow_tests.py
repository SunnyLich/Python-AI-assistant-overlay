"""Run Wisp user-workflow tests from one entry point.

Default run:
    python scripts/run_app_workflow_tests.py

Live GPT 5.5 run:
    python scripts/run_app_workflow_tests.py --real-gpt55

Pass extra pytest args after ``--``:
    python scripts/run_app_workflow_tests.py -- -vv -s
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


WORKFLOW_TESTS = (
    "tests/test_app_user_workflows.py",
    "tests/test_real_gpt55_integration.py",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _preferred_python(root: Path) -> str:
    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _normalize_pytest_args(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        return raw[1:]
    return raw


def _with_default_basetemp(args: list[str], root: Path) -> list[str]:
    if any(arg == "--basetemp" or arg.startswith("--basetemp=") for arg in args):
        return args
    suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    basetemp = root / ".tmp_pytest" / f"app_workflows_{suffix}"
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    return [*args, "--basetemp", str(basetemp)]


def _with_cache_disabled(args: list[str]) -> list[str]:
    if "cacheprovider" in " ".join(args):
        return args
    return ["-p", "no:cacheprovider", *args]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Wisp app user-workflow test suite.",
    )
    parser.add_argument(
        "--real-gpt55",
        action="store_true",
        help="Enable the opt-in real GPT 5.5 workflow test. This can spend tokens.",
    )
    parser.add_argument(
        "--all-tests",
        action="store_true",
        help="Run the full pytest suite instead of only workflow-marked tests.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra pytest arguments, optionally after --.",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.real_gpt55:
        env["WISP_RUN_REAL_GPT55_TESTS"] = "1"

    extra = _with_cache_disabled(_with_default_basetemp(_normalize_pytest_args(args.pytest_args), root))
    python = _preferred_python(root)
    if args.all_tests:
        cmd = [python, "-m", "pytest", *extra]
    else:
        cmd = [python, "-m", "pytest", "-m", "workflow", *WORKFLOW_TESTS, *extra]

    print("Running:", " ".join(cmd), flush=True)
    if args.real_gpt55:
        print("Real GPT 5.5 workflow test enabled; this may spend tokens.", flush=True)
    return subprocess.run(cmd, cwd=root, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
