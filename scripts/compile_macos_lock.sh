#!/usr/bin/env bash
# Compatibility wrapper for regenerating only the macOS runtime lock.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bash "$ROOT/scripts/compile_dependency_locks.sh" macos
