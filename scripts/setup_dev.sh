#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
WANT="$(tr -d '[:space:]' < .python-version 2>/dev/null || true)"
WANT="${WANT:-3.12.13}"
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"
VPY="$ROOT/.venv/bin/python"

python_mm() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true
}

find_python() {
  local candidate
  for candidate in "python$WANT_MM" python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && [ "$(python_mm "$(command -v "$candidate")")" = "$WANT_MM" ]; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

if [ -x "$VPY" ] && [ "$(python_mm "$VPY")" != "$WANT_MM" ]; then
  echo "Existing .venv is not Python $WANT_MM; rebuilding it for development..."
  rm -rf "$ROOT/.venv"
fi

if [ ! -x "$VPY" ]; then
  PY="$(find_python || true)"
  if [ -z "${PY:-}" ]; then
    echo "ERROR: Python $WANT_MM is required for development setup." >&2
    echo "Install Python $WANT_MM or run Start Wisp.command once to provision .venv." >&2
    exit 1
  fi
  echo "Creating development environment at $ROOT/.venv..."
  "$PY" -m venv "$ROOT/.venv"
fi

"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r requirements.txt -r requirements-dev.txt

echo
echo "Developer environment ready."
echo "Run checks with:"
echo "  $VPY -m pytest"
echo "  $VPY -m ruff check ."
echo "  $VPY -m mypy ."
