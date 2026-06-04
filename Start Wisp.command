#!/usr/bin/env bash
# Wisp — double-click to start.
# Creates the local .venv on first run and installs dependencies; after that it
# just launches. It prefers Python from .python-version, but will use an existing
# working environment rather than rebuilding in a loop.
set -e
cd "$(dirname "$0")"

WANT="$(cat .python-version 2>/dev/null | tr -d '[:space:]')"   # e.g. 3.12.13
WANT="${WANT:-3.12.13}"
WANT_MM="$(echo "$WANT" | cut -d. -f1,2)"                        # e.g. 3.12

py_minor() { "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true; }

# Locate a Python matching WANT_MM. Works even when pyenv/Homebrew aren't on the
# PATH that Finder gives a double-clicked .command (a non-login shell).
find_wanted_python() {
  local c
  # pyenv builds (direct paths — no pyenv init required)
  for c in "$HOME/.pyenv/versions/$WANT/bin/python" "$HOME"/.pyenv/versions/"$WANT_MM".*/bin/python; do
    [ -x "$c" ] && [ "$(py_minor "$c")" = "$WANT_MM" ] && { echo "$c"; return; }
  done
  # python.org framework + Homebrew + PATH
  for c in \
    "/Library/Frameworks/Python.framework/Versions/$WANT_MM/bin/python3" \
    "/opt/homebrew/bin/python$WANT_MM" \
    "/usr/local/bin/python$WANT_MM" \
    "$(command -v "python$WANT_MM" 2>/dev/null)"; do
    [ -n "$c" ] && [ -x "$c" ] && [ "$(py_minor "$c")" = "$WANT_MM" ] && { echo "$c"; return; }
  done
}

# Any usable Python, for the case where WANT isn't installed at all.
find_any_python() {
  command -v python3 >/dev/null 2>&1 && { command -v python3; return; }
  command -v python  >/dev/null 2>&1 && { command -v python; return; }
}

# Decide whether to build. Only rebuild for a version mismatch when a correct
# interpreter actually exists — otherwise keep the working env (no rebuild loop).
PYTHON=""
build=0
if [ ! -x ".venv/bin/python" ]; then
  build=1
else
  have="$(py_minor ./.venv/bin/python)"
  if [ "$have" != "$WANT_MM" ]; then
    cand="$(find_wanted_python || true)"
    if [ -n "$cand" ]; then
      echo "Environment is Python $have; rebuilding with $WANT_MM ($cand)..."
      rm -rf .venv
      PYTHON="$cand"
      build=1
    else
      echo "NOTE: environment is Python $have and $WANT_MM was not found — using it as-is."
      echo "      For the supported version: pyenv install $WANT  (then delete .venv and relaunch)."
    fi
  fi
fi

if [ "$build" = 1 ]; then
  [ -z "$PYTHON" ] && PYTHON="$(find_wanted_python || true)"
  [ -z "$PYTHON" ] && PYTHON="$(find_any_python || true)"
  if [ -z "$PYTHON" ]; then
    echo "ERROR: No Python found. Install Python $WANT (recommended: pyenv install $WANT), then relaunch."
    exit 1
  fi
  [ "$(py_minor "$PYTHON")" != "$WANT_MM" ] && \
    echo "WARNING: building with Python $(py_minor "$PYTHON") (wanted $WANT_MM)."
  echo "Setting up Wisp with $PYTHON ..."
  "$PYTHON" -m venv .venv
fi

# Ensure dependencies are present (covers a fresh or half-installed venv).
if ! ./.venv/bin/python -c "import PySide6" >/dev/null 2>&1; then
  echo "Installing dependencies (this takes a minute)..."
  ./.venv/bin/python -m pip install --upgrade pip
  if ! ./.venv/bin/python -m pip install -r requirements.txt; then
    echo "ERROR: dependency install failed on Python $(py_minor ./.venv/bin/python)."
    echo "       If you're not on $WANT_MM, install it (pyenv install $WANT), delete .venv, and relaunch."
    exit 1
  fi
  echo "Setup complete — starting Wisp."
fi

exec ./.venv/bin/python main.py
