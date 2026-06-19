#!/usr/bin/env bash
#
# Pure-Python macOS test runner.
#
# This is the macOS gate now that Wisp uses one Python app across Windows and
# macOS. It provisions the repo .venv from requirements-macos.lock and runs the
# normal Python test suite plus the runtime worker tests.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="$REPO_ROOT/build_logs/macos_tests_$RUN_ID"
SUMMARY_LOG="$LOG_DIR/summary.log"
LATEST_LOG_POINTER="$REPO_ROOT/build_logs/latest_macos_tests.txt"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$SUMMARY_LOG") 2>&1

{
  echo "script=scripts/run_macos_tests.command"
  echo "run_id=$RUN_ID"
  echo "log_dir=$LOG_DIR"
  echo "summary_log=$SUMMARY_LOG"
} > "$LATEST_LOG_POINTER"

if [ "$(uname -s 2>/dev/null || true)" != "Darwin" ]; then
  echo "ERROR: this macOS test runner must run on macOS." >&2
  echo "Logs written to: $LOG_DIR"
  exit 1
fi

if [ ! -s .python-version ]; then
  echo "ERROR: .python-version is required and must contain an exact Python version like 3.12.13." >&2
  echo "Logs written to: $LOG_DIR"
  exit 1
fi
WANT="$(tr -d '[:space:]' < .python-version)"
if [[ ! "$WANT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: .python-version must contain an exact Python version like 3.12.13." >&2
  echo "Logs written to: $LOG_DIR"
  exit 1
fi
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"
REQ_FILE="$REPO_ROOT/requirements-macos.lock"
VPY="$REPO_ROOT/.venv/bin/python"
STAMP_FILE="$REPO_ROOT/.venv/.wisp-macos-python-deps.stamp"

if [ ! -s "$REQ_FILE" ]; then
  echo "ERROR: requirements-macos.lock is required for macOS setup." >&2
  echo "Logs written to: $LOG_DIR"
  exit 1
fi

run_logged() {
  local name="$1"
  shift
  local log="$LOG_DIR/$name.log"

  echo
  echo "== $name =="
  echo "command: $*"
  set +e
  "$@" 2>&1 | tee "$log"
  local status=${PIPESTATUS[0]}
  set -e
  if [ "$status" -ne 0 ]; then
    echo "FAILED: $name (exit $status)"
    echo "Logs written to: $LOG_DIR"
    return "$status"
  fi
  echo "PASS: $name"
}

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true
}

python_matches_want() {
  [ -n "${1:-}" ] && [ "$(python_version "$1")" = "$WANT" ]
}

try_python() {
  local c="$1" p
  if [ -z "$c" ]; then
    return 1
  fi
  if command -v "$c" >/dev/null 2>&1; then
    p="$(command -v "$c")"
  elif [ -x "$c" ]; then
    p="$c"
  else
    return 1
  fi
  if python_matches_want "$p"; then
    echo "$p"
    return 0
  fi
  return 1
}

find_local_python() {
  local root d c
  root="${PYENV_ROOT:-$HOME/.pyenv}"
  if [ -d "$root/versions" ]; then
    while IFS= read -r d; do
      for c in "$d/bin/python" "$d/bin/python3"; do
        try_python "$c" && return
      done
    done < <(find "$root/versions" -maxdepth 1 -type d -name "$WANT_MM.*" 2>/dev/null | sort -r)
  fi

  for c in "python$WANT_MM" \
           "/Library/Frameworks/Python.framework/Versions/$WANT_MM/bin/python3" \
           "/opt/homebrew/bin/python$WANT_MM" \
           "/usr/local/bin/python$WANT_MM" \
           python3 python; do
    try_python "$c" && return
  done
}

lock_hash() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ_FILE" | awk '{print $1}'
  else
    cksum "$REQ_FILE" | awk '{print $1}'
  fi
}

python_deps_ok() {
  "$1" - <<'PY' >/dev/null 2>&1
import PySide6
import dotenv
import PIL
import numpy
PY
}

venv_ready() {
  [ -x "$VPY" ] || return 1
  python_matches_want "$VPY" || return 1
  python_deps_ok "$VPY" || return 1
  [ -f "$STAMP_FILE" ] || return 1
  [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" = "$(lock_hash)" ] || return 1
}

find_uv() {
  local c
  for c in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do
    if command -v "$c" >/dev/null 2>&1; then
      command -v "$c"
      return 0
    elif [ -x "$c" ]; then
      echo "$c"
      return 0
    fi
  done
}

ensure_uv() {
  local uv
  uv="$(find_uv || true)"
  if [ -n "$uv" ]; then
    echo "$uv"
    return 0
  fi

  curl -LsSf https://astral.sh/uv/install.sh | sh > "$LOG_DIR/uv-install.log" 2>&1
  find_uv
}

ensure_pip() {
  local py="$1"
  if ! "$py" -m pip --version >/dev/null 2>&1; then
    run_logged "python-pip-bootstrap" "$py" -m ensurepip --upgrade
  fi
}

setup_venv() {
  local py uv
  if venv_ready; then
    echo "Using existing macOS .venv: $VPY"
    return 0
  fi

  py="$(find_local_python || true)"

  if [ -n "$py" ]; then
    if [ -e "$REPO_ROOT/.venv" ]; then
      rm -rf "$REPO_ROOT/.venv"
    fi
    echo "Creating .venv with local Python: $py"
    run_logged "python-venv-create" "$py" -m venv "$REPO_ROOT/.venv"
    ensure_pip "$VPY"
    run_logged "python-pip-upgrade" "$VPY" -m pip install --upgrade pip
    run_logged "python-deps-install" "$VPY" -m pip install -r "$REQ_FILE"
  else
    echo "Installing/locating uv because Python $WANT was not found locally..."
    uv="$(ensure_uv)"
    if [ -z "$uv" ]; then
      echo "ERROR: could not find/install uv to provision Python $WANT." >&2
      exit 1
    fi
    if [ -e "$REPO_ROOT/.venv" ]; then
      rm -rf "$REPO_ROOT/.venv"
    fi
    echo "Creating .venv with uv Python $WANT"
    run_logged "uv-venv-create" "$uv" venv --python "$WANT" "$REPO_ROOT/.venv"
    run_logged "uv-deps-install" "$uv" pip install --python "$VPY" -r "$REQ_FILE"
    ensure_pip "$VPY"
  fi

  if ! python_deps_ok "$VPY"; then
    echo "ERROR: installed .venv is still missing Python app dependencies." >&2
    exit 1
  fi
  lock_hash > "$STAMP_FILE"
  echo "macOS .venv ready: $VPY"
}

setup_venv
ensure_pip "$VPY"

export WISP_REPO_ROOT="$REPO_ROOT"
export PYTHONUNBUFFERED=1

echo "Repo: $REPO_ROOT"
echo "Python: $VPY"
"$VPY" --version
echo "Logs: $LOG_DIR"
echo "Latest log pointer: $LATEST_LOG_POINTER"

if ! "$VPY" -c "import pytest" >/dev/null 2>&1; then
  run_logged "python-pytest-install" "$VPY" -m pip install pytest
fi

run_logged "python-tests" "$VPY" -m pytest tests runtime/brain/tests -q

echo
echo "Pure-Python macOS verification passed."
echo "Logs written to: $LOG_DIR"
echo "Latest log pointer: $LATEST_LOG_POINTER"
