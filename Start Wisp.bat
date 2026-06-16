@echo off
REM Wisp - double-click to start.
REM Order of preference (no unnecessary downloads):
REM   1. If the local .venv already works, just launch.
REM   2. If it exists but is missing deps, install them.
REM   3. Otherwise build the venv with a Python already on this machine.
REM   4. Only if none of that works, fall back to uv (which fetches Python 3.12),
REM      installing uv first if it isn't present.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "WANT=3.12.13"
if exist ".python-version" ( set /p WANT=<.python-version )
for /f "tokens=1,2 delims=." %%a in ("!WANT!") do set "WANT_MM=%%a.%%b"
set "VPY=.venv\Scripts\python.exe"

REM Rebuild stale virtual environments created with a different Python minor.
if exist "%VPY%" (
  "%VPY%" -c "import sys; raise SystemExit(0 if f'{sys.version_info[0]}.{sys.version_info[1]}' == '!WANT_MM!' else 1)" >nul 2>nul
  if errorlevel 1 (
    echo Existing environment is not Python !WANT_MM!; rebuilding it...
    rmdir /s /q .venv
  )
)

REM --- 1) Already set up? Just run. ---
if exist "%VPY%" (
  "%VPY%" -c "import PySide6" >nul 2>nul
  if not errorlevel 1 goto run
)

echo Setting up Wisp...

REM --- 2) venv exists but deps missing -> install into it ---
if exist "%VPY%" (
  echo Installing dependencies into the existing environment...
  "%VPY%" -m pip --version >nul 2>nul
  if errorlevel 1 "%VPY%" -m ensurepip --upgrade
  "%VPY%" -m pip install --upgrade pip >nul 2>nul
  "%VPY%" -m pip install -r requirements.txt
  "%VPY%" -c "import PySide6" >nul 2>nul && goto run
)

REM --- 3) build with a Python already installed (prefer the py launcher's WANT_MM) ---
set "PYCMD="
where py >nul 2>nul && ( py -!WANT_MM! --version >nul 2>nul && set "PYCMD=py -!WANT_MM!" )
if not defined PYCMD (
  where python >nul 2>nul && (
    python -c "import sys; raise SystemExit(0 if f'{sys.version_info[0]}.{sys.version_info[1]}' == '!WANT_MM!' else 1)" >nul 2>nul
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
    "%VPY%" -c "import PySide6" >nul 2>nul && goto run
  )
  echo Local Python couldn't produce a working environment - falling back to uv.
) else (
  echo No suitable Python found locally - using uv.
)

REM --- 4) uv fallback: provisions Python 3.12 + deps. Install uv if missing. ---
set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV (
  echo Installing uv ^(one-time^)...
  powershell -ExecutionPolicy ByPass -NoProfile -c "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
  if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
)
if not defined UV (
  echo ERROR: setup failed and uv could not be installed.
  echo        Install Python !WANT_MM! or uv manually: https://docs.astral.sh/uv/
  pause
  exit /b 1
)
echo Provisioning Python !WANT_MM! with uv...
if exist ".venv" rmdir /s /q .venv
"!UV!" venv --python "!WANT!"
if errorlevel 1 ( echo Failed to create environment & pause & exit /b 1 )
"!UV!" pip install --python "%VPY%" -r requirements.txt
if errorlevel 1 ( echo Dependency install failed. & pause & exit /b 1 )

:run
set "WISP_REPO_ROOT=%CD%"
set "PYTHONUNBUFFERED=1"
"%VPY%" -m macos_py.supervisor.app
