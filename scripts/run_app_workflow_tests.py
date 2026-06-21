"""Run Wisp user-workflow tests from one entry point.

Default run:
    python scripts/run_app_workflow_tests.py

Live GPT 5.5 run:
    python scripts/run_app_workflow_tests.py --real-gpt55

Real host/native smoke run:
    python scripts/run_app_workflow_tests.py --real-host
    python scripts/run_app_workflow_tests.py --real-host-interactive

Pass extra pytest args after ``--``:
    python scripts/run_app_workflow_tests.py -- -vv -s
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
import time
from pathlib import Path


WORKFLOW_TESTS = (
    "tests/test_app_user_workflows.py",
    "tests/test_real_gpt55_integration.py",
    "tests/test_real_host_native_smoke.py",
)
REAL_HOST_TESTS = ("tests/test_real_host_native_smoke.py",)
LOG_ROOT_NAME = "build_logs"
LATEST_LOG_POINTER = "latest_app_workflow_tests.txt"
LATEST_FAILURE_POINTER = "latest_app_workflow_tests_failure.txt"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)


def _make_log_dir(root: Path) -> Path:
    log_root = root / LOG_ROOT_NAME
    log_dir = log_root / f"app_workflow_tests_{_timestamp()}_{os.getpid()}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_root / LATEST_LOG_POINTER).write_text(str(log_dir), encoding="utf-8")
    return log_dir


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


def _write_failure_pointer(root: Path, log_path: Path) -> None:
    log_root = root / LOG_ROOT_NAME
    log_root.mkdir(parents=True, exist_ok=True)
    (log_root / LATEST_FAILURE_POINTER).write_text(str(log_path), encoding="utf-8")


def _run_logged(name: str, cmd: list[str], *, root: Path, env: dict[str, str], log_dir: Path) -> tuple[int, Path]:
    log_path = log_dir / f"{_safe_name(name)}.log"
    print("Running:", " ".join(cmd), flush=True)
    print("Log:", log_path, flush=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"name={name}\n")
        log.write(f"cwd={root}\n")
        log.write(f"command={' '.join(cmd)}\n\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        status = proc.wait()
        log.write(f"\nexit_code={status}\n")
    return status, log_path


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
        "--real-host",
        action="store_true",
        help="Enable opt-in tests that touch the real desktop, clipboard, screenshot, and tray APIs.",
    )
    parser.add_argument(
        "--real-host-interactive",
        action="store_true",
        help="Also enable real host tests that synthesize input into focused test windows.",
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
    log_dir = _make_log_dir(root)
    summary_path = log_dir / "summary.txt"
    print("Test logs:", log_dir, flush=True)
    base_env = os.environ.copy()
    real_host = args.real_host or args.real_host_interactive
    env = base_env.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.real_gpt55:
        env["WISP_RUN_REAL_GPT55_TESTS"] = "1"

    extra = _with_cache_disabled(_with_default_basetemp(_normalize_pytest_args(args.pytest_args), root))
    python = _preferred_python(root)
    if args.all_tests:
        cmd = [python, "-m", "pytest", *extra]
    else:
        marker = "workflow and not real_host" if real_host else "workflow"
        cmd = [python, "-m", "pytest", "-m", marker, *WORKFLOW_TESTS, *extra]

    summary_lines = [
        "Wisp app workflow test run",
        f"started={_dt.datetime.now().isoformat(timespec='seconds')}",
        f"cwd={root}",
        f"python={python}",
        f"args={' '.join(argv if argv is not None else sys.argv[1:])}",
        "",
    ]
    if args.real_gpt55:
        print("Real GPT 5.5 workflow test enabled; this may spend tokens.", flush=True)
    if real_host:
        print("Real host/native tests enabled; the second pytest process may touch clipboard, screenshots, tray, and visible windows.", flush=True)
        if sys.platform == "darwin":
            print(
                "macOS grants privacy permissions to the launcher running pytest "
                "(Terminal, Codex, or Python), not automatically to the packaged Wisp app.",
                flush=True,
            )
    if args.real_host_interactive:
        print("Interactive real host tests enabled; keep the machine idle while keyboard/paste checks run.", flush=True)
    status, main_log = _run_logged("pytest-main", cmd, root=root, env=env, log_dir=log_dir)
    summary_lines.extend(
        [
            f"pytest-main.exit_code={status}",
            f"pytest-main.log={main_log}",
        ]
    )
    if status != 0 or not real_host:
        summary_lines.append(f"final_exit_code={status}")
        if status != 0:
            summary_lines.append(f"failure_log={main_log}")
            _write_failure_pointer(root, main_log)
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        print("Summary:", summary_path, flush=True)
        if status != 0:
            print("Failure log:", main_log, flush=True)
        return status

    host_env = base_env.copy()
    host_env.pop("QT_QPA_PLATFORM", None)
    host_env["WISP_RUN_REAL_HOST_TESTS"] = "1"
    if args.real_host_interactive:
        host_env["WISP_RUN_REAL_HOST_INTERACTIVE_TESTS"] = "1"
    host_extra = _with_cache_disabled(
        _with_default_basetemp(_normalize_pytest_args(args.pytest_args), root)
    )
    host_cmd = [python, "-m", "pytest", "-m", "real_host", *REAL_HOST_TESTS, *host_extra]
    host_status, host_log = _run_logged("pytest-real-host", host_cmd, root=root, env=host_env, log_dir=log_dir)
    summary_lines.extend(
        [
            f"pytest-real-host.exit_code={host_status}",
            f"pytest-real-host.log={host_log}",
            f"final_exit_code={host_status}",
        ]
    )
    if host_status != 0:
        summary_lines.append(f"failure_log={host_log}")
        _write_failure_pointer(root, host_log)
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print("Summary:", summary_path, flush=True)
    if host_status != 0:
        print("Failure log:", host_log, flush=True)
    return host_status


if __name__ == "__main__":
    raise SystemExit(main())
