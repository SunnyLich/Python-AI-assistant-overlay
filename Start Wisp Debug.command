#!/usr/bin/env bash
# Wisp debug launcher - keeps timestamped runtime logs under build_logs.
set -euo pipefail
cd "$(dirname "$0")"
export WISP_RUNTIME_LOG_MODE=debug
export WISP_KEEP_TERMINAL_ON_EXIT=1
exec bash "./Start Wisp.command"
