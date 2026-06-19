#!/usr/bin/env bash
# Regenerates the locked macOS requirements file from the shared inputs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -s .python-version ]; then
  echo "ERROR: .python-version is required and must contain an exact Python version like 3.12.13." >&2
  exit 1
fi
WANT="$(tr -d '[:space:]' < .python-version)"
if [[ ! "$WANT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: .python-version must contain an exact Python version like 3.12.13." >&2
  exit 1
fi
WANT_MM="$(printf '%s' "$WANT" | cut -d. -f1,2)"

if [ ! -s requirements.txt ]; then
  echo "ERROR: requirements.txt is required to compile the macOS lock." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to compile the macOS lock file." >&2
  echo "Install it from https://docs.astral.sh/uv/ and rerun this script." >&2
  exit 1
fi

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}")' 2>/dev/null || true
}

find_python() {
  local c
  for c in "${PYTHON_BIN:-}" ./.venv/bin/python "python$WANT_MM" python3 python; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1; then
      c="$(command -v "$c")"
    elif [ ! -x "$c" ]; then
      continue
    fi
    if [ "$(python_version "$c")" = "$WANT" ]; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "Could not find Python $WANT. Install Python $WANT or set PYTHON_BIN." >&2
  exit 1
fi

uv pip compile requirements.txt \
  --no-header \
  --python "$PYTHON" \
  --python-version "$WANT_MM" \
  --python-platform aarch64-apple-darwin \
  --output-file requirements-macos.lock

echo "Updated requirements-macos.lock for macOS arm64 / Python $WANT."
