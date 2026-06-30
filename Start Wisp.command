#!/usr/bin/env bash
# Wisp macOS/Linux launcher.
#
# Double-click this on macOS/Linux to start the Python Wisp app.
# This launches the shared pure-Python multi-process host.
set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT="$(pwd)"
OS_NAME="$(uname -s 2>/dev/null || true)"
WISP_APP_LAUNCHED=0

close_macos_terminal_on_exit() {
  if [ "$OS_NAME" != "Darwin" ]; then
    return
  fi
  if [ "$WISP_APP_LAUNCHED" != "1" ]; then
    return
  fi
  if [ "${WISP_KEEP_TERMINAL_ON_EXIT:-}" = "1" ]; then
    return
  fi
  if [ "${WISP_RUNTIME_LOG_MODE:-}" = "debug" ]; then
    return
  fi
  if [ ! -t 0 ]; then
    return
  fi
  local tty_path tty_name
  tty_path="$(tty 2>/dev/null || true)"
  if [ -z "$tty_path" ] || [ "$tty_path" = "not a tty" ]; then
    return
  fi
  tty_name="${tty_path#/dev/}"
  nohup /bin/sh -c '
    sleep 0.2
    /usr/bin/osascript >/dev/null 2>&1 <<OSA
tell application "Terminal"
  repeat with w in windows
    repeat with t in tabs of w
      try
        set tabTty to tty of t
        if tabTty is "'"$tty_path"'" or tabTty is "'"$tty_name"'" then
          if (count of tabs of w) is 1 then
            close w
          else
            close t
          end if
          return
        end if
      end try
    end repeat
  end repeat
end tell
OSA
  ' >/dev/null 2>&1 &
}
trap close_macos_terminal_on_exit EXIT

if [ ! -s .python-version ]; then
  echo "ERROR: .python-version is required and must contain a Python version like 3.12 or 3.12.13." >&2
  exit 1
fi
WANT="$(tr -d '[:space:]' < .python-version)"
if [[ ! "$WANT" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
  echo "ERROR: .python-version must contain a Python version like 3.12 or 3.12.13." >&2
  exit 1
fi
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"

VPY="$REPO_ROOT/.venv/bin/python"
REQ_FILE="$REPO_ROOT/requirements-linux.lock"
STAMP_FILE="$REPO_ROOT/.venv/.wisp-linux-python-deps.stamp"

if [ "$OS_NAME" = "Darwin" ]; then
  REQ_FILE="$REPO_ROOT/requirements-macos.lock"
  STAMP_FILE="$REPO_ROOT/.venv/.wisp-macos-python-deps.stamp"
fi
if [ ! -s "$REQ_FILE" ]; then
  echo "ERROR: ${REQ_FILE##*/} is required for setup." >&2
  echo "Regenerate locks with: bash scripts/compile_dependency_locks.sh" >&2
  exit 1
fi

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true
}

python_matches_want() {
  local version
  [ -n "${1:-}" ] || return 1
  version="$(python_version "$1")"
  [ "$version" = "$WANT" ] || [[ "$WANT" =~ ^[0-9]+\.[0-9]+$ && "$version" == "$WANT".* ]]
}

try_python() {
  local candidate="$1"
  local path=""
  if [ -z "$candidate" ]; then
    return 1
  fi
  if command -v "$candidate" >/dev/null 2>&1; then
    path="$(command -v "$candidate")"
  elif [ -x "$candidate" ]; then
    path="$candidate"
  else
    return 1
  fi
  if python_matches_want "$path"; then
    echo "$path"
    return 0
  fi
  return 1
}

find_local_python() {
  local root d candidate
  root="${PYENV_ROOT:-$HOME/.pyenv}"
  if [ -d "$root/versions" ]; then
    while IFS= read -r d; do
      for candidate in "$d/bin/python" "$d/bin/python3"; do
        try_python "$candidate" && return
      done
    done < <(find "$root/versions" -maxdepth 1 -type d -name "$WANT_MM.*" 2>/dev/null | sort -r)
  fi

  for candidate in "python$WANT_MM" \
                   "/Library/Frameworks/Python.framework/Versions/$WANT_MM/bin/python3" \
                   "/opt/homebrew/bin/python$WANT_MM" \
                   "/usr/local/bin/python$WANT_MM" \
                   python3 python; do
    try_python "$candidate" && return
  done
}

req_hash() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ_FILE" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$REQ_FILE" | awk '{print $1}'
  else
    cksum "$REQ_FILE" | awk '{print $1}'
  fi
}

ui_deps_ok() {
  local py="$1"
  "$py" - <<'PY' >/dev/null 2>&1
import PySide6
import dotenv
import PIL
import numpy
PY
}

venv_ready() {
  [ -x "$VPY" ] || return 1
  python_matches_want "$VPY" || return 1
  ui_deps_ok "$VPY" || return 1
  [ -f "$STAMP_FILE" ] || return 1
  [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" = "$(req_hash)" ] || return 1
}

ensure_uv() {
  local candidate
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done

  echo "Installing uv to provision Python $WANT..." >&2
  curl -LsSf https://astral.sh/uv/install.sh | sh >&2
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

install_requirements() {
  local py="$1"
  if ! "$py" -m pip --version >/dev/null 2>&1; then
    "$py" -m ensurepip --upgrade
  fi
  "$py" -m pip install --upgrade pip
  "$py" -m pip install -r "$REQ_FILE"
  req_hash > "$STAMP_FILE"
}

setup_venv() {
  local py uv rebuild_venv
  rebuild_venv=0
  if venv_ready; then
    return 0
  fi

  if [ -x "$VPY" ] && python_matches_want "$VPY"; then
    echo "Installing dependencies into the existing environment..."
    install_requirements "$VPY"
    return 0
  fi
  if [ -x "$VPY" ]; then
    echo "Existing .venv is not Python $WANT; rebuilding it..."
    rebuild_venv=1
  fi

  py="$(find_local_python || true)"

  if [ -n "$py" ]; then
    if [ "$rebuild_venv" -eq 1 ] || [ -e "$REPO_ROOT/.venv" ]; then
      rm -rf "$REPO_ROOT/.venv"
    fi
    echo "Building environment with $py..."
    "$py" -m venv "$REPO_ROOT/.venv"
    install_requirements "$VPY"
    return 0
  fi

  uv="$(ensure_uv || true)"
  if [ -z "$uv" ]; then
    echo "ERROR: setup failed and uv could not be installed." >&2
    echo "Install Python $WANT or uv manually, then rerun this launcher." >&2
    exit 1
  fi

  echo "Provisioning Python $WANT with uv..."
  if [ "$rebuild_venv" -eq 1 ] || [ -e "$REPO_ROOT/.venv" ]; then
    rm -rf "$REPO_ROOT/.venv"
  fi
  "$uv" venv --python "$WANT" "$REPO_ROOT/.venv"
  "$uv" pip install --python "$VPY" -r "$REQ_FILE"
  req_hash > "$STAMP_FILE"
}

setup_venv

export WISP_REPO_ROOT="$REPO_ROOT"
export PYTHONUNBUFFERED=1

WISP_APP_LAUNCHED=1
"$VPY" -m runtime.supervisor.app
