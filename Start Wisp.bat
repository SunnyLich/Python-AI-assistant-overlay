@echo off
REM Wisp - double-click to start.
REM Order of preference (no unnecessary downloads):
REM   1. If the local .venv already works, just launch.
REM   2. If it exists but is missing deps, install them.
REM   3. Otherwise build the venv with the pinned Python already on this machine.
REM   4. Only if none of that works, fall back to uv (which fetches pinned Python),
REM      installing uv first if it isn't present.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "WANT="
if not exist ".python-version" (
  echo ERROR: .python-version is required and must contain an exact Python version like 3.12.13.
  pause
  exit /b 1
)
set /p WANT=<.python-version
if not defined WANT (
  echo ERROR: .python-version is required and must contain an exact Python version like 3.12.13.
  pause
  exit /b 1
)
echo(!WANT!| findstr /r "^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo ERROR: .python-version must contain an exact Python version like 3.12.13.
  pause
  exit /b 1
)
if not exist "requirements.txt" (
  echo ERROR: requirements.txt is required for setup.
  pause
  exit /b 1
)
for %%I in ("requirements.txt") do if %%~zI EQU 0 (
  echo ERROR: requirements.txt is required for setup.
  pause
  exit /b 1
)
for /f "tokens=1,2 delims=." %%a in ("!WANT!") do set "WANT_MM=%%a.%%b"
set "VPY=.venv\Scripts\python.exe"
set "STAMP_FILE=.venv\.wisp-deps.stamp"
set "REBUILD_VENV=0"

REM Rebuild stale virtual environments created with a different Python version.
if exist "%VPY%" (
  "%VPY%" "scripts\check_python_version.py" "!WANT!" >nul 2>nul
  if errorlevel 1 (
    echo Existing environment is not Python !WANT!; rebuilding it...
    set "REBUILD_VENV=1"
  )
)

REM --- 1) Already set up? Just run. ---
if "!REBUILD_VENV!"=="0" if exist "%VPY%" (
  call :venv_ready
  if not errorlevel 1 goto run
)

echo Setting up Wisp...

REM --- 2) venv exists but deps missing -> install into it ---
if "!REBUILD_VENV!"=="0" if exist "%VPY%" (
  echo Installing dependencies into the existing environment...
  "%VPY%" -m pip --version >nul 2>nul
  if errorlevel 1 "%VPY%" -m ensurepip --upgrade
  "%VPY%" -m pip install --upgrade pip >nul 2>nul
  "%VPY%" -m pip install -r requirements.txt
  if not errorlevel 1 call :write_req_stamp
  if not errorlevel 1 call :runtime_deps_ok
  if not errorlevel 1 goto run
)

REM --- 3) build with a Python already installed (prefer the py launcher's major/minor selector) ---
set "PYCMD="
where py >nul 2>nul && ( py -!WANT_MM! "scripts\check_python_version.py" "!WANT!" >nul 2>nul && set "PYCMD=py -!WANT_MM!" )
if not defined PYCMD (
  where python >nul 2>nul && (
    python "scripts\check_python_version.py" "!WANT!" >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
  )
)
if defined PYCMD (
  echo Building environment with !PYCMD! ...
  if exist ".venv" rmdir /s /q .venv
  !PYCMD! -m venv .venv
  if not errorlevel 1 (
    "%VPY%" -m pip --version >nul 2>nul
    if errorlevel 1 "%VPY%" -m ensurepip --upgrade
    "%VPY%" -m pip install --upgrade pip >nul 2>nul
    "%VPY%" -m pip install -r requirements.txt
    if not errorlevel 1 call :write_req_stamp
    if not errorlevel 1 call :runtime_deps_ok
    if not errorlevel 1 goto run
  )
  echo Local Python couldn't produce a working environment - falling back to uv.
) else (
  echo No suitable Python found locally - using uv.
)

REM --- 4) uv fallback: provisions pinned Python + deps. Install uv if missing. ---
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV (
  echo Installing uv ^(one-time^)...
  powershell.exe -ExecutionPolicy ByPass -NoProfile -c "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
)
if not defined UV (
  echo ERROR: setup failed and uv could not be installed.
  echo        Install Python !WANT! or uv manually: https://docs.astral.sh/uv/
  pause
  exit /b 1
)
echo Provisioning Python !WANT! with uv...
if exist ".venv" rmdir /s /q .venv
"!UV!" venv --python "!WANT!"
if errorlevel 1 ( echo Failed to create environment & pause & exit /b 1 )
"!UV!" pip install --python "%VPY%" -r requirements.txt
if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )
call :write_req_stamp
if errorlevel 1 ( echo Failed to record dependency stamp. & pause & exit /b 1 )
call :runtime_deps_ok
if errorlevel 1 ( echo Installed dependencies, but runtime imports still failed. & pause & exit /b 1 )

:run
set "WISP_REPO_ROOT=%CD%"
set "PYTHONUNBUFFERED=1"
"%VPY%" -m runtime.supervisor.app
exit /b %ERRORLEVEL%

:runtime_deps_ok
"%VPY%" -c "import PySide6; import dotenv; import PIL; import numpy" >nul 2>nul
exit /b %ERRORLEVEL%

:venv_ready
if not exist "%VPY%" exit /b 1
"%VPY%" "scripts\check_python_version.py" "!WANT!" >nul 2>nul
if errorlevel 1 exit /b 1
call :runtime_deps_ok
if errorlevel 1 exit /b 1
call :deps_stamp_ok
if errorlevel 1 exit /b 1
exit /b 0

:deps_stamp_ok
if not exist "%STAMP_FILE%" exit /b 1
call :read_req_hash
if errorlevel 1 exit /b 1
set "STAMP_HASH="
set /p STAMP_HASH=<"%STAMP_FILE%"
if /i "!STAMP_HASH!"=="!REQ_HASH!" exit /b 0
exit /b 1

:write_req_stamp
call :read_req_hash
if errorlevel 1 exit /b 1
>"%STAMP_FILE%" echo !REQ_HASH!
exit /b 0

:read_req_hash
set "REQ_HASH="
for /f "usebackq delims=" %%h in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements.txt').Hash.ToLowerInvariant()"`) do set "REQ_HASH=%%h"
if not defined REQ_HASH exit /b 1
exit /b 0
