#!/usr/bin/env bash
# macOS/Linux developer setup script for creating the pinned Wisp virtual environment.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
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
VENV_DIR="$ROOT/.venv"
VENV_BACKUP_DIR="$ROOT/.venv.rebuild-backup"
VPY="$VENV_DIR/bin/python"
OS_NAME="$(uname -s 2>/dev/null || true)"
REQ_FILE="$ROOT/requirements-linux.lock"
DEV_REQ_FILE="$ROOT/requirements-dev.lock"

if [ "$OS_NAME" = "Darwin" ]; then
  REQ_FILE="$ROOT/requirements-macos.lock"
fi
if [ ! -s "$REQ_FILE" ]; then
  echo "ERROR: ${REQ_FILE##*/} is required for developer setup." >&2
  echo "Regenerate locks with: bash scripts/compile_dependency_locks.sh" >&2
  exit 1
fi
if [ ! -s "$DEV_REQ_FILE" ]; then
  echo "ERROR: requirements-dev.lock is required for developer setup." >&2
  echo "Regenerate locks with: bash scripts/compile_dependency_locks.sh" >&2
  exit 1
fi

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true
}

python_matches_want() {
  local version
  version="$(python_version "$1")"
  [ "$version" = "$WANT" ] || [[ "$WANT" =~ ^[0-9]+\.[0-9]+$ && "$version" == "$WANT".* ]]
}

find_python() {
  local candidate
  for candidate in "python$WANT_MM" python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && python_matches_want "$(command -v "$candidate")"; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

find_uv() {
  local candidate
  for candidate in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    elif [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

ensure_uv() {
  local uv
  uv="$(find_uv || true)"
  if [ -n "$uv" ]; then
    echo "$uv"
    return 0
  fi

  echo "Installing uv to provision Python $WANT..." >&2
  curl -LsSf https://astral.sh/uv/install.sh | sh >&2
  find_uv
}

BACKED_UP_VENV=0
SETUP_SUCCEEDED=0

backup_venv_for_rebuild() {
  if [ ! -e "$VENV_DIR" ]; then
    return 0
  fi
  if [ -e "$VENV_BACKUP_DIR" ]; then
    echo "ERROR: .venv.rebuild-backup already exists." >&2
    echo "Remove it after confirming no setup is in progress, then rerun this script." >&2
    exit 1
  fi
  mv "$VENV_DIR" "$VENV_BACKUP_DIR"
  BACKED_UP_VENV=1
}

restore_venv_backup() {
  local status=$?
  if [ "$SETUP_SUCCEEDED" -ne 1 ] && [ "$BACKED_UP_VENV" -eq 1 ] && [ -e "$VENV_BACKUP_DIR" ]; then
    echo "Restoring previous .venv after setup failure..." >&2
    rm -rf "$VENV_DIR"
    mv "$VENV_BACKUP_DIR" "$VENV_DIR"
  fi
  return "$status"
}

cleanup_venv_backup() {
  if [ "$BACKED_UP_VENV" -eq 1 ] && [ -e "$VENV_BACKUP_DIR" ]; then
    if ! rm -rf "$VENV_BACKUP_DIR"; then
      echo "WARNING: setup succeeded but could not remove $VENV_BACKUP_DIR" >&2
    fi
    BACKED_UP_VENV=0
  fi
}

trap restore_venv_backup ERR

REBUILD_VENV=0
if [ -x "$VPY" ] && ! python_matches_want "$VPY"; then
  echo "Existing .venv is not Python $WANT; rebuilding it for development..."
  REBUILD_VENV=1
fi

if [ ! -x "$VPY" ] || [ "$REBUILD_VENV" -eq 1 ]; then
  PY="$(find_python || true)"
  if [ -n "${PY:-}" ]; then
    if [ "$REBUILD_VENV" -eq 1 ]; then
      backup_venv_for_rebuild
    fi
    echo "Creating development environment at $VENV_DIR..."
    "$PY" -m venv "$VENV_DIR"
  else
    echo "No local Python $WANT found; using uv to provision it..."
    UV="$(ensure_uv || true)"
    if [ -z "${UV:-}" ]; then
      echo "ERROR: could not find or install uv." >&2
      echo "Install Python $WANT or uv manually, then rerun this script." >&2
      exit 1
    fi
    if [ "$REBUILD_VENV" -eq 1 ]; then
      backup_venv_for_rebuild
    fi
    "$UV" venv --python "$WANT" "$VENV_DIR"
  fi
fi

"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r "$REQ_FILE" -r "$DEV_REQ_FILE"
"$VPY" scripts/check_dev_environment.py
SETUP_SUCCEEDED=1
cleanup_venv_backup
trap - ERR

echo
echo "Developer environment ready."
echo "Run checks with:"
echo "  $VPY -m pytest"
echo "  $VPY -m ruff check ."
echo "  $VPY -m mypy ."
