@echo off
REM Wisp - double-click to start.
REM Creates the local .venv on first run and installs dependencies; after that it
REM just launches. Prefers Python from .python-version, but uses an existing
REM working environment rather than rebuilding in a loop.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "WANT=3.12.13"
if exist ".python-version" ( set /p WANT=<.python-version )
for /f "tokens=1,2 delims=." %%a in ("!WANT!") do set "WANT_MM=%%a.%%b"

REM Is the pinned Python available (via the py launcher)?
set "WANTED_PY="
where py >nul 2>nul && ( py -!WANT_MM! --version >nul 2>nul && set "WANTED_PY=py -!WANT_MM!" )

set "BUILD=0"
if not exist ".venv\Scripts\python.exe" (
  set "BUILD=1"
) else (
  for /f %%v in ('".venv\Scripts\python.exe" -c "import sys;print(str(sys.version_info[0])+chr(46)+str(sys.version_info[1]))" 2^>nul') do set "HAVE=%%v"
  if not "!HAVE!"=="!WANT_MM!" (
    if defined WANTED_PY (
      echo Environment is Python !HAVE!; rebuilding with !WANT_MM! ...
      rmdir /s /q .venv
      set "BUILD=1"
    ) else (
      echo NOTE: environment is Python !HAVE! and !WANT_MM! was not found - using it as-is.
    )
  )
)

if "!BUILD!"=="1" (
  set "PYTHON=!WANTED_PY!"
  if not defined PYTHON ( where python >nul 2>nul && set "PYTHON=python" )
  if not defined PYTHON (
    echo ERROR: No Python found. Install Python !WANT! from python.org, then relaunch.
    pause
    exit /b 1
  )
  echo Setting up Wisp with !PYTHON! ...
  !PYTHON! -m venv .venv
  if errorlevel 1 ( echo Failed to create .venv & pause & exit /b 1 )
)

".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies (this takes a minute)...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo ERROR: dependency install failed. If you are not on !WANT_MM!, install it,
    echo        delete the .venv folder, and relaunch.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" main.py
