#!/usr/bin/env bash
# Wisp macOS/Linux launcher.
#
# Double-click this on macOS. It starts the shared Python/Qt Wisp UI, matching
# the Windows surface. Platform-specific behavior stays behind that UI in
# core/platform_* helpers.
set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT="$(pwd)"
OS_NAME="$(uname -s 2>/dev/null || true)"

WANT="$(tr -d '[:space:]' < .python-version 2>/dev/null || true)"
WANT="${WANT:-3.12.13}"
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"

VPY="$REPO_ROOT/.venv/bin/python"
REQ_FILE="$REPO_ROOT/requirements.txt"
STAMP_FILE="$REPO_ROOT/.venv/.wisp-deps.stamp"

if [ "$OS_NAME" = "Darwin" ] && [ -f "$REPO_ROOT/requirements-macos.lock" ]; then
  REQ_FILE="$REPO_ROOT/requirements-macos.lock"
  STAMP_FILE="$REPO_ROOT/.venv/.wisp-macos-shared-ui-deps.stamp"
fi

python_mm() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true
}

python_matches_want() {
  [ -n "${1:-}" ] && [ "$(python_mm "$1")" = "$WANT_MM" ]
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
  "$py" -m pip install --upgrade pip
  "$py" -m pip install -r "$REQ_FILE"
  req_hash > "$STAMP_FILE"
}

setup_venv() {
  local py uv
  if venv_ready; then
    return 0
  fi

  if [ -x "$VPY" ] && python_matches_want "$VPY"; then
    echo "Installing dependencies into the existing environment..."
    install_requirements "$VPY"
    return 0
  fi

  py="$(find_local_python || true)"
  rm -rf "$REPO_ROOT/.venv"

  if [ -n "$py" ]; then
    echo "Building environment with $py..."
    "$py" -m venv "$REPO_ROOT/.venv"
    install_requirements "$VPY"
    return 0
  fi

  uv="$(ensure_uv || true)"
  if [ -z "$uv" ]; then
    echo "ERROR: setup failed and uv could not be installed." >&2
    echo "Install Python $WANT_MM or uv manually, then rerun this launcher." >&2
    exit 1
  fi

  echo "Provisioning Python $WANT with uv..."
  "$uv" venv --python "$WANT" "$REPO_ROOT/.venv"
  "$uv" pip install --python "$VPY" -r "$REQ_FILE"
  req_hash > "$STAMP_FILE"
}

setup_venv

export WISP_REPO_ROOT="$REPO_ROOT"
exec "$VPY" main.py
