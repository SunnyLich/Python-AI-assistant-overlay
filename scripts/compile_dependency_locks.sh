#!/usr/bin/env bash
# Regenerates locked requirement files from the shared human-edited manifests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

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

require_input() {
  local path="$1"
  if [ ! -s "$path" ]; then
    echo "ERROR: $path is required to compile dependency locks." >&2
    exit 1
  fi
}

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to compile dependency lock files." >&2
  echo "Install it from https://docs.astral.sh/uv/ and rerun this script." >&2
  exit 1
fi

require_input requirements.txt
require_input requirements-dev.txt
require_input requirements-build.txt

compile_runtime_lock() {
  local platform="$1"
  local output="$2"
  uv pip compile requirements.txt \
    --upgrade \
    --no-header \
    --python-version "$WANT_MM" \
    --python-platform "$platform" \
    --output-file "$output"
  echo "Updated $output for $platform / Python $WANT_MM."
}

compile_universal_lock() {
  local input="$1"
  local output="$2"
  uv pip compile "$input" \
    --upgrade \
    --universal \
    --no-header \
    --python-version "$WANT_MM" \
    --output-file "$output"
  echo "Updated $output for Python $WANT_MM."
}

targets=("$@")
if [ "${#targets[@]}" -eq 0 ]; then
  targets=(all)
fi

for target in "${targets[@]}"; do
  case "$target" in
    all)
      compile_runtime_lock x86_64-pc-windows-msvc requirements-windows.lock
      compile_runtime_lock x86_64-manylinux_2_34 requirements-linux.lock
      compile_runtime_lock aarch64-apple-darwin requirements-macos.lock
      compile_universal_lock requirements-dev.txt requirements-dev.lock
      compile_universal_lock requirements-build.txt requirements-build.lock
      ;;
    windows)
      compile_runtime_lock x86_64-pc-windows-msvc requirements-windows.lock
      ;;
    linux)
      compile_runtime_lock x86_64-manylinux_2_34 requirements-linux.lock
      ;;
    macos)
      compile_runtime_lock aarch64-apple-darwin requirements-macos.lock
      ;;
    dev)
      compile_universal_lock requirements-dev.txt requirements-dev.lock
      ;;
    build)
      compile_universal_lock requirements-build.txt requirements-build.lock
      ;;
    *)
      echo "ERROR: unknown lock target '$target'. Use all, windows, linux, macos, dev, or build." >&2
      exit 1
      ;;
  esac
done
