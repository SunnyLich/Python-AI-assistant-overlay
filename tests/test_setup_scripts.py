import unittest
import re
from pathlib import Path

from scripts import check_dev_environment


ROOT = Path(__file__).resolve().parents[1]


def shell_imports(script_name: str) -> tuple[str, ...]:
    imports: list[str] = []
    for line in (ROOT / script_name).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text.startswith("import "):
            imports.append(text.removeprefix("import ").split()[0])
    return tuple(imports)


def batch_inline_imports(script_name: str) -> tuple[str, ...]:
    imports: list[str] = []
    script = (ROOT / script_name).read_text(encoding="utf-8")
    for command in re.findall(r'-c "([^"]*import [^"]*)"', script):
        for part in command.split(";"):
            text = part.strip()
            if text.startswith("import "):
                imports.extend(module.strip() for module in text.removeprefix("import ").split(","))
    return tuple(imports)


class SetupScriptTests(unittest.TestCase):
    def test_windows_setup_wraps_failure_prone_native_commands(self) -> None:
        script = (ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")

        self.assertIn("function Invoke-Native", script)
        self.assertIn('Invoke-Native "virtual environment creation"', script)
        self.assertIn('Invoke-Native "uv virtual environment creation"', script)
        self.assertIn('Invoke-Native "pip upgrade"', script)
        self.assertIn('Invoke-Native "dependency install"', script)
        self.assertIn('Invoke-Native "developer environment preflight"', script)

    def test_windows_setup_does_not_call_success_critical_pip_directly(self) -> None:
        script = (ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")

        self.assertNotIn("& $Python -m pip install", script)

    def test_posix_setup_runs_preflight_before_ready_message(self) -> None:
        script = (ROOT / "scripts" / "setup_dev.sh").read_text(encoding="utf-8")

        preflight = '"$VPY" scripts/check_dev_environment.py'
        ready = 'echo "Developer environment ready."'
        self.assertIn(preflight, script)
        self.assertIn(ready, script)
        self.assertLess(script.index(preflight), script.index(ready))

    def test_posix_setup_uses_locked_runtime_dependencies_on_macos(self) -> None:
        script = (ROOT / "scripts" / "setup_dev.sh").read_text(encoding="utf-8")

        self.assertIn('OS_NAME="$(uname -s 2>/dev/null || true)"', script)
        self.assertIn('REQ_FILE="$ROOT/requirements.txt"', script)
        self.assertIn('if [ "$OS_NAME" = "Darwin" ]; then', script)
        self.assertIn('REQ_FILE="$ROOT/requirements-macos.lock"', script)
        self.assertIn("requirements-macos.lock is required for macOS setup", script)
        self.assertIn('"$VPY" -m pip install -r "$REQ_FILE" -r requirements-dev.txt', script)
        self.assertNotIn('"$VPY" -m pip install -r requirements.txt -r requirements-dev.txt', script)

    def test_install_entry_points_check_dependency_manifests_before_mutating_venv(self) -> None:
        launcher = (ROOT / "Start Wisp.command").read_text(encoding="utf-8")
        self.assertIn('if [ ! -s "$REQ_FILE" ]; then', launcher)
        self.assertIn("requirements.txt is required for setup", launcher)
        self.assertLess(launcher.index("requirements.txt is required for setup"), launcher.index("rm -rf"))

        setup_sh = (ROOT / "scripts" / "setup_dev.sh").read_text(encoding="utf-8")
        self.assertIn('if [ ! -s "$REQ_FILE" ]; then', setup_sh)
        self.assertIn('if [ ! -s "$ROOT/requirements-dev.txt" ]; then', setup_sh)
        self.assertIn("requirements-dev.txt is required for developer setup", setup_sh)
        self.assertLess(setup_sh.index("requirements-dev.txt is required for developer setup"), setup_sh.index('mv "$VENV_DIR" "$VENV_BACKUP_DIR"'))

        compile_lock = (ROOT / "scripts" / "compile_macos_lock.sh").read_text(encoding="utf-8")
        self.assertIn("requirements.txt is required to compile the macOS lock", compile_lock)
        self.assertLess(compile_lock.index("requirements.txt is required to compile"), compile_lock.index("uv pip compile"))

        batch = (ROOT / "Start Wisp.bat").read_text(encoding="utf-8")
        self.assertIn('if not exist "requirements.txt"', batch)
        self.assertIn('for %%I in ("requirements.txt") do if %%~zI EQU 0', batch)
        self.assertIn("requirements.txt is required for setup", batch)
        self.assertLess(batch.index("requirements.txt is required for setup"), batch.index("rmdir /s /q .venv"))

        powershell = (ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")
        self.assertIn('$RequiredDependencyFiles = @(', powershell)
        self.assertIn('Join-Path $Root "requirements.txt"', powershell)
        self.assertIn('Join-Path $Root "requirements-dev.txt"', powershell)
        self.assertIn("$Name is required for developer setup.", powershell)
        self.assertLess(powershell.index("$RequiredDependencyFiles = @("), powershell.index("Move-Item -LiteralPath $VenvDir -Destination $VenvBackupDir"))

    def test_dev_setup_keeps_stale_venv_until_replacement_is_known(self) -> None:
        setup_sh = (ROOT / "scripts" / "setup_dev.sh").read_text(encoding="utf-8")

        self.assertIn("REBUILD_VENV=0", setup_sh)
        self.assertIn("REBUILD_VENV=1", setup_sh)
        python_lookup = setup_sh.index('PY="$(find_python || true)"')
        uv_lookup = setup_sh.index('UV="$(ensure_uv || true)"')
        self.assertLess(python_lookup, setup_sh.index("backup_venv_for_rebuild", python_lookup))
        self.assertLess(uv_lookup, setup_sh.index("backup_venv_for_rebuild", uv_lookup))

        powershell = (ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")

        self.assertIn("$RebuildVenv = $false", powershell)
        self.assertIn("$RebuildVenv = $true", powershell)
        self.assertLess(powershell.index("if ($null -ne $PyExe)"), powershell.index("$MovedVenvBackup = Move-VenvForRebuild"))
        self.assertLess(powershell.index("$Uv = Ensure-Uv"), powershell.rindex("$MovedVenvBackup = Move-VenvForRebuild"))

    def test_dev_setup_restores_previous_venv_when_rebuild_fails(self) -> None:
        setup_sh = (ROOT / "scripts" / "setup_dev.sh").read_text(encoding="utf-8")

        self.assertIn('VENV_BACKUP_DIR="$ROOT/.venv.rebuild-backup"', setup_sh)
        self.assertIn("backup_venv_for_rebuild()", setup_sh)
        self.assertIn('mv "$VENV_DIR" "$VENV_BACKUP_DIR"', setup_sh)
        self.assertIn("restore_venv_backup()", setup_sh)
        self.assertIn('trap restore_venv_backup ERR', setup_sh)
        self.assertIn('rm -rf "$VENV_DIR"', setup_sh)
        self.assertIn('mv "$VENV_BACKUP_DIR" "$VENV_DIR"', setup_sh)
        self.assertIn("cleanup_venv_backup()", setup_sh)
        self.assertIn('trap - ERR', setup_sh)
        self.assertLess(setup_sh.index('"$VPY" scripts/check_dev_environment.py'), setup_sh.index("SETUP_SUCCEEDED=1"))
        setup_succeeded = setup_sh.index("SETUP_SUCCEEDED=1")
        self.assertLess(setup_succeeded, setup_sh.index("cleanup_venv_backup", setup_succeeded))

        powershell = (ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")

        self.assertIn('$VenvBackupDir = Join-Path $Root ".venv.rebuild-backup"', powershell)
        self.assertIn("function Move-VenvForRebuild", powershell)
        self.assertIn("Move-Item -LiteralPath $VenvDir -Destination $VenvBackupDir", powershell)
        self.assertIn("function Restore-VenvBackup", powershell)
        self.assertIn("Restoring previous .venv after setup failure", powershell)
        self.assertIn("Move-Item -LiteralPath $VenvBackupDir -Destination $VenvDir", powershell)
        self.assertIn("function Remove-VenvBackup", powershell)
        self.assertIn("} catch {", powershell)
        self.assertIn("Restore-VenvBackup", powershell)
        preflight = powershell.index('Invoke-Native "developer environment preflight"')
        self.assertLess(preflight, powershell.index("Remove-VenvBackup", preflight))

    def test_launch_paths_keep_stale_venv_until_replacement_is_known(self) -> None:
        launcher = (ROOT / "Start Wisp.command").read_text(encoding="utf-8")

        self.assertIn("rebuild_venv=0", launcher)
        self.assertIn("rebuild_venv=1", launcher)
        self.assertNotIn('rm -rf "$REPO_ROOT/.venv"', launcher[: launcher.index('py="$(find_local_python || true)"')])
        self.assertLess(launcher.index('py="$(find_local_python || true)"'), launcher.index('rm -rf "$REPO_ROOT/.venv"'))
        self.assertLess(launcher.index('uv="$(ensure_uv || true)"'), launcher.rindex('rm -rf "$REPO_ROOT/.venv"'))

        macos_runner = (ROOT / "scripts" / "run_macos_tests.command").read_text(encoding="utf-8")

        self.assertNotIn(
            'rm -rf "$REPO_ROOT/.venv"',
            macos_runner[: macos_runner.index('py="$(find_local_python || true)"')],
        )
        self.assertLess(
            macos_runner.index('py="$(find_local_python || true)"'),
            macos_runner.index('rm -rf "$REPO_ROOT/.venv"'),
        )
        self.assertLess(
            macos_runner.index('uv="$(ensure_uv)"'),
            macos_runner.rindex('rm -rf "$REPO_ROOT/.venv"'),
        )

        batch = (ROOT / "Start Wisp.bat").read_text(encoding="utf-8")

        self.assertIn('set "REBUILD_VENV=0"', batch)
        self.assertIn('set "REBUILD_VENV=1"', batch)
        self.assertIn('if "!REBUILD_VENV!"=="0" if exist "%VPY%"', batch)
        self.assertNotIn("rmdir /s /q .venv", batch[: batch.index('set "PYCMD="')])
        self.assertLess(batch.index('set "PYCMD="'), batch.index("rmdir /s /q .venv"))
        self.assertLess(batch.index("if not defined UV ("), batch.rindex("rmdir /s /q .venv"))

    def test_entry_points_reject_non_exact_python_version_pin(self) -> None:
        posix_scripts = [
            "Start Wisp.command",
            "scripts/setup_dev.sh",
            "scripts/run_macos_tests.command",
            "scripts/compile_macos_lock.sh",
        ]
        for script_name in posix_scripts:
            with self.subTest(script=script_name):
                script = (ROOT / script_name).read_text(encoding="utf-8")
                self.assertIn("[ ! -s .python-version ]", script)
                self.assertNotIn('WANT="${WANT:-3.12.13}"', script)
                self.assertIn(".python-version is required and must contain an exact Python version like 3.12.13", script)
                self.assertIn('[[ ! "$WANT" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$ ]]', script)
                self.assertIn(".python-version must contain an exact Python version like 3.12.13", script)

        batch = (ROOT / "Start Wisp.bat").read_text(encoding="utf-8")
        self.assertIn('set "WANT="', batch)
        self.assertIn('if not exist ".python-version"', batch)
        self.assertNotIn('set "WANT=3.12.13"', batch)
        self.assertIn(".python-version is required and must contain an exact Python version like 3.12.13", batch)
        self.assertIn('findstr /r "^[0-9][0-9]*\\.[0-9][0-9]*\\.[0-9][0-9]*$"', batch)
        self.assertIn(".python-version must contain an exact Python version like 3.12.13", batch)

        powershell = (ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")
        self.assertIn('$Want = ""', powershell)
        self.assertNotIn('$Want = "3.12.13"', powershell)
        self.assertIn(".python-version is required and must contain an exact Python version like 3.12.13", powershell)
        self.assertIn("$Want -notmatch '^\\d+\\.\\d+\\.\\d+$'", powershell)
        self.assertIn(".python-version must contain an exact Python version like 3.12.13", powershell)

    def test_macos_launcher_requires_lock_file(self) -> None:
        script = (ROOT / "Start Wisp.command").read_text(encoding="utf-8")

        self.assertIn('if [ "$OS_NAME" = "Darwin" ]; then', script)
        self.assertIn('REQ_FILE="$REPO_ROOT/requirements-macos.lock"', script)
        self.assertIn('if [ ! -s "$REQ_FILE" ]; then', script)
        self.assertIn("requirements-macos.lock is required for macOS setup", script)
        self.assertIn("bash scripts/compile_macos_lock.sh", script)

    def test_macos_test_runner_requires_lock_file(self) -> None:
        script = (ROOT / "scripts" / "run_macos_tests.command").read_text(encoding="utf-8")

        self.assertIn('REQ_FILE="$REPO_ROOT/requirements-macos.lock"', script)
        self.assertIn('if [ ! -s "$REQ_FILE" ]; then', script)
        self.assertIn("requirements-macos.lock is required for macOS setup", script)

    def test_runtime_dependency_probes_match_preflight(self) -> None:
        expected = check_dev_environment.RUNTIME_MODULES

        self.assertEqual(shell_imports("Start Wisp.command"), expected)
        self.assertEqual(shell_imports("scripts/run_macos_tests.command"), expected)
        self.assertEqual(batch_inline_imports("Start Wisp.bat"), expected)

    def test_windows_launcher_requires_dependency_stamp_for_ready_venv(self) -> None:
        script = (ROOT / "Start Wisp.bat").read_text(encoding="utf-8")

        self.assertIn('set "STAMP_FILE=.venv\\.wisp-deps.stamp"', script)
        self.assertIn("call :venv_ready", script)
        self.assertIn("call :deps_stamp_ok", script)
        self.assertIn('if not exist "%STAMP_FILE%" exit /b 1', script)
        self.assertIn("powershell.exe -NoProfile -ExecutionPolicy Bypass", script)
        self.assertIn("Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements.txt'", script)

    def test_windows_launcher_writes_dependency_stamp_after_installs(self) -> None:
        script = (ROOT / "Start Wisp.bat").read_text(encoding="utf-8")

        self.assertEqual(script.count("call :write_req_stamp"), 3)
        self.assertIn('>"%STAMP_FILE%" echo !REQ_HASH!', script)


if __name__ == "__main__":
    unittest.main()
