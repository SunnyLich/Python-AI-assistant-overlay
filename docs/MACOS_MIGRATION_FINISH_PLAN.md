# Native macOS Migration Finish Plan

This is the remaining one-pass plan for finishing the Swift/AppKit macOS
migration while keeping Windows untouched. Windows continues to use `main.py`
and `ui/`; macOS-visible UI work belongs in `macos/Sources/Wisp` with the
Python brain sidecar used only for shared backend contracts.

## Completion Gates

| Gate | Purpose | Command or evidence | Done when |
|---|---|---|---|
| 1. Offline contract | Prove shared config/brain contracts and Swift sources still build/test | `Test Wisp (Mac Native).command` or `bash scripts/run_macos_native_tests.command` | Python brain/config/native validation tests and `swift test` pass |
| 2. Dev app launch | Prove the generated `.app` starts like Finder/LaunchServices starts it | `bash scripts/macos_phase1_validate.sh --open` | `native-app-launch.log` appears in the newest `build_logs/macos_phase1_*` folder |
| 3. Live parity | Prove migrated UI behavior in a real interactive macOS session | `live-parity-checklist.md` copied into the newest log folder | Every item from `docs/MACOS_LIVE_PARITY_CHECKLIST.md` is checked or a bug is filed/fixed |
| 4. Release package | Prove a standalone-shaped app can carry its own runtime and sign correctly | `WISP_PYTHON_RUNTIME_DIR=... WISP_CODESIGN_IDENTITY=... bash scripts/macos_package_release.sh` | Embedded Python import probe, codesign verify, notarization/stapling, and final zip pass |
| 5. Signed app launch | Prove the signed/notarized app starts outside the dev shell | Add `WISP_VALIDATE_APP_LAUNCH=1` to the package command | `native-app-launch.log` appears in the newest `build_logs/macos_package_*` folder |
| 6. Final regression pass | Prove no Mac-only fixes broke shared behavior | Re-run Gate 1 after live/package fixes | Latest quick-test pointer references a passing run |

## Remaining Implementation Loop

1. Run Gate 1 after every Swift or sidecar change.
2. Open evidence with `Open Wisp Mac Logs.command`.
3. Run Gate 2 when the change affects launch, resources, logging, `.env`
   discovery, bundled brain/core files, or app startup.
4. Work through `docs/MACOS_LIVE_PARITY_CHECKLIST.md` on a real Mac. Fix any
   unchecked item in Swift/AppKit or the isolated Python sidecar, then repeat
   Gates 1 and 2.
5. Package only after live parity passes. Run Gate 4 with Developer ID and
   notarization credentials.
6. Run Gate 5 on the signed app, then repeat the live checklist against that
   signed/notarized build.

## Evidence Rules

- The newest quick-test log pointer is `build_logs/latest_macos_native_tests.txt`.
- The newest full dev-launch pointer is `build_logs/latest_macos_phase1.txt`.
- The newest package pointer is `build_logs/latest_macos_package.txt`.
- Every native runner must copy `docs/MACOS_LIVE_PARITY_CHECKLIST.md` to
  `live-parity-checklist.md` inside its log folder.
- Crash reports collected by the Mac scripts belong beside the failing log
  folder and should be fixed before declaring parity complete.

## Areas That Must Be Proven Live

- Overlay visuals, overlay right-click menu, and caller/WASD intent contrast in
  both light mode and dark mode.
- Chat, response bubble, settings, memory, plugin manager, snip overlay, agent
  task/history, voice recording, TTS playback, and overlay pulse.
- Settings API-key management, provider auth, ChatGPT browser sign-in,
  GitHub device sign-in, Copilot token save/test/clear, and Reset All.
- Accessibility, Screen Recording, Microphone permissions, Launch at Login, and
  packaged app `.env` behavior under `~/Library/Application Support/Wisp`.
- Signed/notarized app startup with `WISP_VALIDATE_APP_LAUNCH=1`.

## Current Hard Boundary

From a Windows workspace we can maintain source, docs, scripts, and Python
tests. We cannot truthfully finish Swift compilation, AppKit interaction, TCC
permission prompts, signing, notarization, or signed-app launch validation
without running the Mac gates on real macOS hardware.
