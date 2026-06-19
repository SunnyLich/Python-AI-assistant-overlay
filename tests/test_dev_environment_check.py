import contextlib
import io
import tempfile
import unittest
from subprocess import CompletedProcess
from pathlib import Path
from unittest import mock

from scripts import check_dev_environment


class DevEnvironmentCheckTests(unittest.TestCase):
    def test_requirement_import_name_parses_pinned_requirement(self) -> None:
        self.assertEqual(check_dev_environment.requirement_import_name("pytest>=8.0.0"), "pytest")
        self.assertEqual(check_dev_environment.requirement_import_name("some-package[extra]>=1 ; python_version>'3'"), "some_package")
        self.assertIsNone(check_dev_environment.requirement_import_name("# comment only"))

    def test_canonical_requirement_name_normalizes_distribution_names(self) -> None:
        self.assertEqual(check_dev_environment.canonical_requirement_name("Pillow>=12.0"), "pillow")
        self.assertEqual(check_dev_environment.canonical_requirement_name("python_dotenv>=1"), "python-dotenv")
        self.assertEqual(check_dev_environment.canonical_requirement_name("cartesia[websockets]>=3"), "cartesia")

    def test_requirement_applies_to_platform_reads_simple_markers(self) -> None:
        self.assertTrue(check_dev_environment.requirement_applies_to_platform("common>=1", "darwin"))
        self.assertTrue(
            check_dev_environment.requirement_applies_to_platform(
                'pyobjc-framework-Cocoa>=10; sys_platform == "darwin"',
                "darwin",
            )
        )
        self.assertFalse(
            check_dev_environment.requirement_applies_to_platform(
                'pywin32>=311; sys_platform == "win32"',
                "darwin",
            )
        )
        self.assertFalse(
            check_dev_environment.requirement_applies_to_platform(
                'linux-only>=1; sys_platform != "darwin"',
                "darwin",
            )
        )

    def test_requirement_is_exact_pin(self) -> None:
        self.assertTrue(check_dev_environment.requirement_is_exact_pin("PySide6==6.11.1"))
        self.assertTrue(check_dev_environment.requirement_is_exact_pin("cartesia[websockets]==3.2.0 ; python_version>'3'"))
        self.assertFalse(check_dev_environment.requirement_is_exact_pin("PySide6>=6.11.0"))
        self.assertFalse(check_dev_environment.requirement_is_exact_pin("PySide6==6.11.1,>=6.0"))
        self.assertFalse(check_dev_environment.requirement_is_exact_pin("# comment only"))

    def test_dev_modules_follow_requirements_dev(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertEqual(check_dev_environment.dev_modules(root), ("pytest", "ruff", "mypy"))

    def test_dev_modules_rejects_missing_requirements_dev(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                check_dev_environment.dev_modules(Path(tmp))

    def test_dev_modules_rejects_empty_requirements_dev(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements-dev.txt").write_text("# nothing here\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.dev_modules(Path(tmp))

    def test_runtime_requirements_accepts_project_requirements_txt(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertIsNone(check_dev_environment.ensure_runtime_requirements(root))

    def test_runtime_requirements_rejects_missing_requirements_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                check_dev_environment.ensure_runtime_requirements(Path(tmp))

    def test_runtime_requirements_rejects_empty_requirements_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements.txt").write_text("# nothing here\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.ensure_runtime_requirements(Path(tmp))

    def test_build_requirements_accepts_project_requirements_build_txt(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertIsNone(check_dev_environment.ensure_build_requirements(root))

    def test_build_requirements_rejects_missing_requirements_build_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                check_dev_environment.ensure_build_requirements(Path(tmp))

    def test_build_requirements_rejects_empty_requirements_build_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements-build.txt").write_text("# nothing here\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.ensure_build_requirements(Path(tmp))

    def test_macos_lock_accepts_project_lock_file(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertIsNone(check_dev_environment.ensure_macos_lock(root))

    def test_macos_lock_rejects_missing_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                check_dev_environment.ensure_macos_lock(Path(tmp))

    def test_macos_lock_rejects_empty_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements-macos.lock").write_text("# nothing here\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.ensure_macos_lock(Path(tmp))

    def test_macos_lock_rejects_unlocked_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements-macos.lock").write_text("PySide6>=6.11.0\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.ensure_macos_lock(Path(tmp))

    def test_macos_lock_rejects_missing_runtime_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "requirements.txt").write_text(
                "\n".join(
                    [
                        "PySide6>=6.11.0",
                        "Pillow>=12.0",
                        'pywin32>=311; sys_platform == "win32"',
                    ]
                ),
                encoding="utf-8",
            )
            Path(tmp, "requirements-macos.lock").write_text("pyside6==6.11.1\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.ensure_macos_lock(Path(tmp))

    def test_read_project_required_version_reads_exact_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")

            self.assertEqual(check_dev_environment.read_project_required_version(Path(tmp)), "3.12.13")

    def test_read_project_required_version_allows_toml_quotes_and_comment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text(
                "[project]\nrequires-python = '==3.12.13'  # pinned runtime\n",
                encoding="utf-8",
            )

            self.assertEqual(check_dev_environment.read_project_required_version(Path(tmp)), "3.12.13")

    def test_read_project_required_version_uses_project_section_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text(
                '[tool.demo]\nrequires-python = "==9.9.9"\n\n[project]\nrequires-python = "==3.12.13"\n',
                encoding="utf-8",
            )

            self.assertEqual(check_dev_environment.read_project_required_version(Path(tmp)), "3.12.13")

    def test_read_project_required_version_ignores_non_project_requires_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[tool.demo]\nrequires-python = "==9.9.9"\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.read_project_required_version(Path(tmp))

    def test_read_project_required_version_rejects_non_exact_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.read_project_required_version(Path(tmp))

    def test_read_project_required_version_rejects_missing_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[project]\nname = "wisp"\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.read_project_required_version(Path(tmp))

    def test_read_project_required_version_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                check_dev_environment.read_project_required_version(Path(tmp))

    def test_read_project_required_version_rejects_invalid_exact_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12"\n', encoding="utf-8")

            with self.assertRaises(ValueError):
                check_dev_environment.read_project_required_version(Path(tmp))

    def test_main_reports_non_exact_project_python_pin(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n', encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requires-python must be an exact == pin", stdout.getvalue())

    def test_main_reports_python_version_pin_mismatch(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.14"\n', encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn(".python-version is '3.12.13'", stdout.getvalue())
        self.assertIn("pyproject.toml requires '3.12.14'", stdout.getvalue())

    def test_main_reports_missing_pyproject_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("pyproject.toml is missing", stdout.getvalue())

    def test_main_reports_missing_requirements_dev_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("PySide6==6.11.0\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-dev.txt is missing", stdout.getvalue())

    def test_main_reports_missing_requirements_macos_lock_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-macos.lock is missing", stdout.getvalue())

    def test_main_reports_empty_requirements_macos_lock_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("# none\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-macos.lock has no locked macOS requirements", stdout.getvalue())

    def test_main_reports_unlocked_requirements_macos_lock_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-macos.lock contains unlocked requirement 'PySide6>=6.11.0'", stdout.getvalue())

    def test_main_reports_requirements_macos_lock_missing_runtime_requirement(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\nPillow>=12.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("pyside6==6.11.1\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-macos.lock is missing locked runtime requirement 'pillow'", stdout.getvalue())

    def test_main_reports_missing_requirements_txt_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements.txt is missing", stdout.getvalue())

    def test_main_reports_empty_requirements_txt_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("# none\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements.txt has no runtime requirements", stdout.getvalue())

    def test_main_reports_empty_requirements_dev_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("PySide6==6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-dev.txt").write_text("# none\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-dev.txt has no developer requirements", stdout.getvalue())

    def test_main_reports_missing_requirements_build_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("PySide6==6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-dev.txt").write_text("pytest>=8.0.0\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-build.txt is missing", stdout.getvalue())

    def test_main_reports_empty_requirements_build_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12.13\n", encoding="utf-8")
            Path(tmp, "pyproject.toml").write_text('[project]\nrequires-python = "==3.12.13"\n', encoding="utf-8")
            Path(tmp, "requirements.txt").write_text("PySide6>=6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-macos.lock").write_text("PySide6==6.11.0\n", encoding="utf-8")
            Path(tmp, "requirements-dev.txt").write_text("pytest>=8.0.0\n", encoding="utf-8")
            Path(tmp, "requirements-build.txt").write_text("# none\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("requirements-build.txt has no build requirements", stdout.getvalue())

    def test_venv_python_uses_windows_layout(self) -> None:
        root = Path("repo")

        self.assertEqual(
            check_dev_environment.venv_python(root, os_name="nt"),
            Path("repo") / ".venv" / "Scripts" / "python.exe",
        )

    def test_venv_python_uses_posix_layout(self) -> None:
        root = Path("repo")

        self.assertEqual(
            check_dev_environment.venv_python(root, os_name="posix"),
            Path("repo") / ".venv" / "bin" / "python",
        )

    def test_setup_command_matches_platform(self) -> None:
        self.assertEqual(check_dev_environment.setup_command(os_name="nt"), r".\scripts\setup_dev.ps1")
        self.assertEqual(check_dev_environment.setup_command(os_name="posix"), "bash scripts/setup_dev.sh")

    def test_main_reports_invalid_python_version_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("3.12\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn("invalid .python-version", stdout.getvalue())

    def test_main_reports_missing_python_version_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn(".python-version is missing", stdout.getvalue())

    def test_main_reports_empty_python_version_file(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(stdout):
            Path(tmp, ".python-version").write_text("\n", encoding="utf-8")
            exit_code = check_dev_environment.main(["--root", tmp])

        self.assertEqual(exit_code, 1)
        self.assertIn(".python-version is empty", stdout.getvalue())

    def test_dev_module_problem_reports_missing_modules(self) -> None:
        proc = CompletedProcess(args=[], returncode=1, stdout="pytest\nruff\n", stderr="")

        with mock.patch.object(check_dev_environment.subprocess, "run", return_value=proc):
            problem = check_dev_environment.dev_module_problem(Path("python"))

        self.assertEqual(problem, "missing developer modules: pytest, ruff")

    def test_dev_module_problem_reports_probe_failure(self) -> None:
        proc = CompletedProcess(args=[], returncode=2, stdout="", stderr="interpreter exploded")

        with mock.patch.object(check_dev_environment.subprocess, "run", return_value=proc):
            problem = check_dev_environment.dev_module_problem(Path("python"))

        self.assertEqual(problem, "could not inspect developer modules: interpreter exploded")

    def test_dev_module_problem_returns_none_when_ready(self) -> None:
        proc = CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with mock.patch.object(check_dev_environment.subprocess, "run", return_value=proc):
            problem = check_dev_environment.dev_module_problem(Path("python"))

        self.assertIsNone(problem)

    def test_runtime_module_problem_reports_missing_modules(self) -> None:
        proc = CompletedProcess(args=[], returncode=1, stdout="PySide6\nPIL\n", stderr="")

        with mock.patch.object(check_dev_environment.subprocess, "run", return_value=proc):
            problem = check_dev_environment.runtime_module_problem(Path("python"))

        self.assertEqual(problem, "missing runtime modules: PySide6, PIL")


if __name__ == "__main__":
    unittest.main()
