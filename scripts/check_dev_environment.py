"""Non-mutating preflight check for Wisp's local developer environment."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.check_python_version import parse_version, version_text
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.check_python_version import parse_version, version_text

DEFAULT_DEV_MODULES = ("pytest", "ruff", "mypy")
RUNTIME_MODULES = ("PySide6", "dotenv", "PIL", "numpy")
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
EXACT_REQUIREMENT_RE = re.compile(r"^\s*[A-Za-z0-9_.-]+(?:\[[^\]]+\])?==[^,\s]+$")
REQUIRES_PYTHON_LINE_RE = re.compile(r"^\s*requires-python\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$")
SYS_PLATFORM_EQ_RE = re.compile(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]")
SYS_PLATFORM_NE_RE = re.compile(r"sys_platform\s*!=\s*['\"]([^'\"]+)['\"]")
TOML_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


def read_expected_version(root: Path) -> str:
    version_file = root / ".python-version"
    if not version_file.exists():
        raise ValueError(".python-version is missing")
    value = version_file.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(".python-version is empty")
    return value


def read_project_required_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise ValueError("pyproject.toml is missing")
    in_project = False
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        section = TOML_SECTION_RE.match(line)
        if section:
            in_project = section.group(1).strip() == "project"
            continue
        if not in_project:
            continue
        match = REQUIRES_PYTHON_LINE_RE.match(line)
        if match:
            specifier = match.group(2).strip()
            if not specifier.startswith("=="):
                raise ValueError(f"pyproject.toml requires-python must be an exact == pin, got {specifier!r}")
            version = specifier[2:].strip()
            try:
                parse_version(version)
            except ValueError as exc:
                raise ValueError(f"pyproject.toml requires-python has invalid exact pin {specifier!r}: {exc}") from exc
            return version
        if "requires-python" in line:
            raise ValueError("pyproject.toml requires-python must be a quoted exact == pin")
    raise ValueError("pyproject.toml is missing requires-python")


def requirement_text(line: str) -> str | None:
    text = line.split("#", 1)[0].split(";", 1)[0].strip()
    if not text or text.startswith(("-", "http://", "https://")):
        return None
    return text


def requirement_marker(line: str) -> str:
    text = line.split("#", 1)[0]
    if ";" not in text:
        return ""
    return text.split(";", 1)[1].strip()


def requirement_import_name(line: str) -> str | None:
    text = requirement_text(line)
    if text is None:
        return None
    match = REQUIREMENT_NAME_RE.match(text)
    if not match:
        return None
    return match.group(1).replace("-", "_")


def canonical_requirement_name(line: str) -> str | None:
    text = requirement_text(line)
    if text is None:
        return None
    match = REQUIREMENT_NAME_RE.match(text)
    if not match:
        return None
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def requirement_applies_to_platform(line: str, platform: str) -> bool:
    marker = requirement_marker(line)
    if not marker:
        return True
    allowed_platforms = set(SYS_PLATFORM_EQ_RE.findall(marker))
    if allowed_platforms:
        return platform in allowed_platforms
    denied_platforms = set(SYS_PLATFORM_NE_RE.findall(marker))
    return platform not in denied_platforms


def requirement_is_exact_pin(line: str) -> bool:
    text = requirement_text(line)
    return bool(text and EXACT_REQUIREMENT_RE.match(text))


def requirement_names_for_platform(path: Path, platform: str) -> set[str]:
    return {
        name
        for line in path.read_text(encoding="utf-8").splitlines()
        if requirement_applies_to_platform(line, platform)
        if (name := canonical_requirement_name(line))
    }


def dev_modules(root: Path) -> tuple[str, ...]:
    requirements = root / "requirements-dev.txt"
    if not requirements.exists():
        raise ValueError("requirements-dev.txt is missing")
    modules = [
        module
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if (module := requirement_import_name(line))
    ]
    if not modules:
        raise ValueError("requirements-dev.txt has no developer requirements")
    return tuple(modules)


def ensure_requirement_file(root: Path, filename: str, description: str) -> None:
    requirements = root / filename
    if not requirements.exists():
        raise ValueError(f"{filename} is missing")
    has_requirements = any(
        requirement_import_name(line)
        for line in requirements.read_text(encoding="utf-8").splitlines()
    )
    if not has_requirements:
        raise ValueError(f"{filename} has no {description} requirements")


def ensure_runtime_requirements(root: Path) -> None:
    ensure_requirement_file(root, "requirements.txt", "runtime")


def ensure_build_requirements(root: Path) -> None:
    ensure_requirement_file(root, "requirements-build.txt", "build")


def ensure_macos_lock(root: Path) -> None:
    ensure_requirement_file(root, "requirements-macos.lock", "locked macOS")
    lock_file = root / "requirements-macos.lock"
    unlocked = [
        text
        for line in lock_file.read_text(encoding="utf-8").splitlines()
        if (text := requirement_text(line)) and not requirement_is_exact_pin(line)
    ]
    if unlocked:
        raise ValueError(f"requirements-macos.lock contains unlocked requirement {unlocked[0]!r}")
    ensure_runtime_requirements(root)
    runtime_requirements = requirement_names_for_platform(root / "requirements.txt", "darwin")
    locked_requirements = requirement_names_for_platform(lock_file, "darwin")
    missing = sorted(runtime_requirements - locked_requirements)
    if missing:
        raise ValueError(f"requirements-macos.lock is missing locked runtime requirement {missing[0]!r}")


def venv_python(root: Path, os_name: str | None = None) -> Path:
    name = os_name or os.name
    if name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def setup_command(os_name: str | None = None) -> str:
    name = os_name or os.name
    if name == "nt":
        return r".\scripts\setup_dev.ps1"
    return "bash scripts/setup_dev.sh"


def interpreter_version(python: Path) -> tuple[int, int, int] | None:
    proc = subprocess.run(
        [
            str(python),
            "-c",
            "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        return parse_version(proc.stdout.strip())
    except ValueError:
        return None


def module_probe_problem(python: Path, modules: tuple[str, ...], label: str) -> str | None:
    code = (
        "import importlib.util, sys; "
        f"missing = [name for name in {modules!r} if importlib.util.find_spec(name) is None]; "
        "print('\\n'.join(missing)); "
        "raise SystemExit(1 if missing else 0)"
    )
    proc = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return None
    missing = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if missing:
        return f"missing {label} modules: " + ", ".join(missing)
    detail = proc.stderr.strip() or f"exit code {proc.returncode}"
    return f"could not inspect {label} modules: {detail}"


def runtime_module_problem(python: Path) -> str | None:
    return module_probe_problem(python, RUNTIME_MODULES, "runtime")


def dev_module_problem(python: Path, modules: tuple[str, ...] = DEFAULT_DEV_MODULES) -> str | None:
    return module_probe_problem(python, modules, "developer")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    root = args.root.resolve()
    try:
        expected_text = read_expected_version(root)
    except ValueError as exc:
        print(f"Developer environment is not ready: {exc}.")
        return 1
    try:
        expected = parse_version(expected_text)
    except ValueError as exc:
        print(f"Developer environment is not ready: invalid .python-version {expected_text!r}: {exc}.")
        return 1
    try:
        project_required = read_project_required_version(root)
    except ValueError as exc:
        print(f"Developer environment is not ready: {exc}.")
        return 1
    if project_required != expected_text:
        print(
            "Developer environment is not ready: "
            f".python-version is {expected_text!r} but pyproject.toml requires {project_required!r}."
        )
        return 1
    try:
        ensure_runtime_requirements(root)
    except ValueError as exc:
        print(f"Developer environment is not ready: {exc}.")
        return 1
    try:
        ensure_macos_lock(root)
    except ValueError as exc:
        print(f"Developer environment is not ready: {exc}.")
        return 1
    try:
        developer_modules = dev_modules(root)
    except ValueError as exc:
        print(f"Developer environment is not ready: {exc}.")
        return 1
    try:
        ensure_build_requirements(root)
    except ValueError as exc:
        print(f"Developer environment is not ready: {exc}.")
        return 1
    python = venv_python(root)
    problems: list[str] = []

    if not python.exists():
        problems.append(f"missing virtual environment interpreter: {python}")
    else:
        actual = interpreter_version(python)
        if actual != expected:
            actual_text = version_text(actual) if actual else "unknown"
            problems.append(f"expected Python {expected_text}, found {actual_text} at {python}")
        runtime_problem = runtime_module_problem(python)
        if runtime_problem:
            problems.append(runtime_problem)
        module_problem = dev_module_problem(python, developer_modules)
        if module_problem:
            problems.append(module_problem)

    if problems:
        print("Developer environment is not ready.")
        for problem in problems:
            print(f"- {problem}")
        print()
        print("Run setup with:")
        print(f"  {setup_command()}")
        return 1

    print(f"Developer environment ready: {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
