#!/usr/bin/env bash
# Launch the pure-Python multi-process macOS Wisp target.
set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT="$(pwd)"
WANT="$(tr -d '[:space:]' < .python-version 2>/dev/null || true)"
WANT="${WANT:-3.12.13}"
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"
VPY="$REPO_ROOT/.venv/bin/python"
REQ_FILE="$REPO_ROOT/requirements-macos.lock"
STAMP_FILE="$REPO_ROOT/.venv/.wisp-macos-python-deps.stamp"

python_mm() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true
}

req_hash() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQ_FILE" | awk '{print $1}'
  else
    sha256sum "$REQ_FILE" | awk '{print $1}'
  fi
}

venv_ready() {
  [ -x "$VPY" ] || return 1
  [ "$(python_mm "$VPY")" = "$WANT_MM" ] || return 1
  "$VPY" - <<'PY' >/dev/null 2>&1
import PySide6
import dotenv
import PIL
import numpy
PY
  [ -f "$STAMP_FILE" ] || return 1
  [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" = "$(req_hash)" ] || return 1
}

find_python() {
  for candidate in "python$WANT_MM" \
                   "/Library/Frameworks/Python.framework/Versions/$WANT_MM/bin/python3" \
                   "/opt/homebrew/bin/python$WANT_MM" \
                   "/usr/local/bin/python$WANT_MM" \
                   python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      path="$(command -v "$candidate")"
      if [ "$(python_mm "$path")" = "$WANT_MM" ]; then
        echo "$path"
        return 0
      fi
    fi
  done
}

find_uv() {
  for candidate in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    elif [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
}

if ! venv_ready; then
  py="$(find_python || true)"
  rm -rf "$REPO_ROOT/.venv"
  if [ -n "${py:-}" ]; then
    "$py" -m venv "$REPO_ROOT/.venv"
    "$VPY" -m pip install --upgrade pip
    "$VPY" -m pip install -r "$REQ_FILE"
  else
    uv_bin="$(find_uv || true)"
    if [ -z "${uv_bin:-}" ]; then
      cat >&2 <<EOF
ERROR: Python $WANT_MM was not found, and uv is not installed.

No admin rights are needed. Install uv into your user account, then rerun this launcher:

  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="\$HOME/.local/bin:\$PATH"
  ./"Start Wisp (Mac Python).command"

EOF
      exit 1
    fi
    "$uv_bin" python install "$WANT_MM"
    "$uv_bin" venv --python "$WANT_MM" "$REPO_ROOT/.venv"
    "$uv_bin" pip install --python "$VPY" -r "$REQ_FILE"
  fi
  req_hash > "$STAMP_FILE"
fi

export WISP_REPO_ROOT="$REPO_ROOT"
export PYTHONUNBUFFERED=1
exec "$VPY" -m macos_py.supervisor.app
