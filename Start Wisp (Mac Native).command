#!/usr/bin/env bash
# Experimental native macOS launcher.
#
# The default macOS product UI is `Start Wisp.command`, which launches the same
# Python/Qt interface as Windows. This script is retained for validating the
# Swift/AppKit prototype and native-service work.
set -euo pipefail

cd "$(dirname "$0")"
exec /bin/bash scripts/macos_phase1_validate.sh --run
