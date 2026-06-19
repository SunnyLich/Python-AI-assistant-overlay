import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildScriptTests(unittest.TestCase):
    def test_windows_build_requires_exact_python_version_pin(self) -> None:
        script = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        self.assertIn('$ExpectedPython = ""', script)
        self.assertNotIn('$ExpectedPython = "3.12.13"', script)
        self.assertIn(".python-version is required and must contain an exact Python version like 3.12.13", script)
        self.assertIn("$ExpectedPython -notmatch '^\\d+\\.\\d+\\.\\d+$'", script)
        self.assertIn(".python-version must contain an exact Python version like 3.12.13", script)
        self.assertIn("function Get-PythonVersion", script)
        self.assertIn("sys.version_info[2]", script)
        self.assertIn("$ActualVersion -ne $ExpectedPython", script)
        self.assertNotIn("function Get-PythonMinor", script)
        self.assertNotIn("Assert-PythonMinor", script)

    def test_posix_build_requires_exact_python_version_pin(self) -> None:
        script = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        self.assertIn('if [ ! -s "$ROOT/.python-version" ]; then', script)
        self.assertIn('WANT="$(tr -d', script)
        self.assertNotIn('printf "3.12.13"', script)
        self.assertIn(".python-version is required and must contain an exact Python version like 3.12.13", script)
        self.assertIn('[[ ! "$WANT" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$ ]]', script)
        self.assertIn(".python-version must contain an exact Python version like 3.12.13", script)
        self.assertIn("python_version()", script)
        self.assertIn("sys.version_info[2]", script)
        self.assertIn('HAVE_VERSION="$(python_version "$PYTHON")"', script)
        self.assertNotIn("py_minor", script)

    def test_build_scripts_check_dependency_manifests_before_mutating_outputs(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        self.assertIn("function Require-PackagingFile", powershell)
        self.assertIn("$RequiredBuildFiles = @(", powershell)
        self.assertIn('Path = (Join-Path $Root $RequirementsFile); Name = $RequirementsFile', powershell)
        self.assertIn('Path = (Join-Path $Root $BuildRequirementsFile); Name = $BuildRequirementsFile', powershell)
        self.assertIn("$Name is required for packaging.", powershell)
        self.assertLess(powershell.index("$RequiredBuildFiles = @("), powershell.index("function New-ProjectVenv"))
        self.assertLess(powershell.index("$RequiredBuildFiles = @("), powershell.index("Remove-Item -LiteralPath"))

        shell = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        self.assertIn("require_file()", shell)
        self.assertIn('require_file "$REQUIREMENTS_FILE" "requirements.txt"', shell)
        self.assertIn('require_file "$BUILD_REQUIREMENTS_FILE" "requirements-build.txt"', shell)
        self.assertIn('echo "ERROR: $name is required for packaging."', shell)
        self.assertLess(shell.index('require_file "$BUILD_REQUIREMENTS_FILE" "requirements-build.txt"'), shell.index('"$CREATE_PYTHON" -m venv'))
        self.assertLess(shell.index('require_file "$BUILD_REQUIREMENTS_FILE" "requirements-build.txt"'), shell.index('rm -rf "$ROOT/build" "$ROOT/dist"'))

    def test_build_scripts_check_packaging_inputs_before_mutating_outputs(self) -> None:
        powershell = (ROOT / "tools" / "build_exe.ps1").read_text(encoding="utf-8")

        required_windows_inputs = [
            'Path = $Spec; Name = "packaging\\$SpecName"',
            'Path = (Join-Path $Root "runtime\\supervisor\\app.py"); Name = "runtime\\supervisor\\app.py"',
            'Path = (Join-Path $Root ".env.example"); Name = ".env.example"',
            'Path = (Join-Path $Root "assets"); Name = "assets"',
            'Path = (Join-Path $Root "ui\\locales"); Name = "ui\\locales"',
            'Require-PackagingFile -Path $IconSourcePng -Name "assets\\doll\\idle.png"',
        ]
        for snippet in required_windows_inputs:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, powershell)

        self.assertIn("function Require-PackagingDirectory", powershell)
        self.assertIn("$Name must contain files for packaging.", powershell)
        first_packaging_check = powershell.index("$RequiredPackagingFiles = @(")
        self.assertLess(first_packaging_check, powershell.index("function New-ProjectVenv"))
        self.assertLess(first_packaging_check, powershell.index("Remove-Item -LiteralPath"))
        self.assertLess(first_packaging_check, powershell.index('Invoke-CheckedPython -Python $Python -CommandArgs @("-m", "pip", "install"'))

        shell = (ROOT / "tools" / "build_exe.sh").read_text(encoding="utf-8")

        required_posix_inputs = [
            'require_file "$SPEC" "packaging/$SPEC_NAME"',
            'require_file "$ROOT/runtime/supervisor/app.py" "runtime/supervisor/app.py"',
            'require_file "$ROOT/.env.example" ".env.example"',
            'require_dir "$ROOT/assets" "assets"',
            'require_dir "$ROOT/ui/locales" "ui/locales"',
            'require_file "$ICON_SOURCE_PNG" "assets/doll/idle.png"',
        ]
        for snippet in required_posix_inputs:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, shell)

        self.assertIn("require_dir()", shell)
        self.assertIn('echo "ERROR: $name must contain files for packaging."', shell)
        first_shell_packaging_check = shell.index('require_file "$SPEC" "packaging/$SPEC_NAME"')
        self.assertLess(first_shell_packaging_check, shell.index('"$CREATE_PYTHON" -m venv'))
        self.assertLess(first_shell_packaging_check, shell.index('rm -rf "$ROOT/build" "$ROOT/dist"'))
        self.assertLess(first_shell_packaging_check, shell.index('"$PYTHON" -m pip install'))

    def test_build_docs_describe_exact_patch_version_requirement(self) -> None:
        docs = (ROOT / "docs" / "BUILDING_EXE.md").read_text(encoding="utf-8")

        self.assertIn("exact patch version", docs)
        self.assertNotIn("minor version", docs)


if __name__ == "__main__":
    unittest.main()
