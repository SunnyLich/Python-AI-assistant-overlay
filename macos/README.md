# Wisp macOS Native App

The macOS product direction is now the Swift/AppKit host in `Sources/Wisp`.
It should match the Windows Wisp workflow in look, behavior, configuration,
and feature coverage while moving crash-prone OS work out of the Python/Qt
process.

This `macos/` directory contains the native app and its Python brain sidecar:

```text
macos/
  brain/             Python sidecar for LLM, memory, TTS/STT, and agents
  Sources/Wisp/      Swift/AppKit product host
  Tests/WispTests/   Swift protocol tests
  ui_host/           legacy Python/Qt fallback host kept outside the Swift path
```

The Swift app owns the macOS overlay, tray, caller/intent picker, chat, memory,
core settings, plugin manager, snip overlay, agent task starter/run monitor,
agent history browser, native context, screen capture, voice recording, and
audio playback surfaces. The Python sidecar keeps using the existing
OS-agnostic backend modules until their visible windows are ported.

Plugins remain shared Python/runtime extensions. Plugin authors should not write
Swift for macOS support: implement hooks, tray actions, and model-callable tools
once in `plugins/<name>/__init__.py` or the shared tool contract. The native
Swift plugin manager reads generic metadata from `brain.plugins.list` and runs
declared tray actions through `brain.plugins.run_action`; it must not contain
plugin-specific implementation logic.

Plugin authors should not write Swift for macOS support.
The native Swift plugin manager reads generic metadata from `brain.plugins.list`.
It runs declared tray actions through `brain.plugins.run_action`.

For the remaining migration gates, use
`docs/MACOS_MIGRATION_FINISH_PLAN.md`. It defines the quick-test, dev-launch,
live-parity, package, signed-launch, and final regression evidence required
before the native macOS version should be treated as complete.

## Default Mac Launch

The shared Python/Qt launcher remains available as a fallback while the Swift UI
reaches full parity:

```bash
Start Wisp.command
```

That launcher creates or refreshes `.venv` from `requirements-macos.lock`, then
runs:

```bash
.venv/bin/python main.py
```

## Native Swift Launch

To run the Swift/AppKit app on a Mac:

```bash
bash scripts/macos_phase1_validate.sh --run
```

or double-click:

```bash
Start Wisp (Mac Native).command
```

This path validates the Python sidecar, Swift package, native menubar/overlay,
quits any stale dev app, and launches the generated dev `.app` bundle through
macOS `open`. The generated dev bundle lives at
`build/WispNative/Wisp.app` and stages release-shaped resources under
`Contents/Resources`: `brain`, `core`, `.env.example`, and `assets/doll`.
When launched from Finder, that dev bundle can infer the checkout-relative `.venv` and
`macos/brain` sidecar from its `build/WispNative` location. A fully standalone
bundle still needs `Contents/Resources/python-runtime/bin/python3` plus signing
and notarization.

To test the embedded-runtime packaging path before signing, point
`WISP_PYTHON_RUNTIME_DIR` at an existing Python runtime whose layout contains
`bin/python3`:

```bash
WISP_PYTHON_RUNTIME_DIR=/path/to/python-runtime bash scripts/macos_phase1_validate.sh --open
```

The builder copies that directory to
`Wisp.app/Contents/Resources/python-runtime`. The launch marker records
`brain_python=.../Contents/Resources/python-runtime/bin/python3` when the app is
using the embedded runtime. It also records `brain_python_configured`, which is
the raw configured value before Wisp resolves fallbacks such as the checkout
`.venv/bin/python`, plus app identity fields and executable/existence checks for
the resolved Python and brain directory.

Use `Start Wisp (Mac Native).command --run` or
`bash scripts/macos_phase1_validate.sh --run` when you want the app attached to
Terminal stdout/stderr. Use `--open` when you want to test the generated `.app`
bundle the way Finder opens it, without relying on `WISP_BRAIN_*` environment
variables:

```bash
bash scripts/macos_phase1_validate.sh --open
```

In `--open` mode, Wisp infers the checkout root and newest native log folder
from the dev bundle location, then seeds `WISP_REPO_ROOT` and
`WISP_RUN_LOG_DIR` before starting the Python brain. The native app should read
the same `.env` as the terminal path, and voice recordings or screen captures
should land beside the validation logs instead of in a random temporary
directory. The validation script waits for
`build_logs/macos_phase1_<timestamp>/native-app-launch.log`, which proves the
app reached native startup after LaunchServices opened it. The script validates
the copied `dev-launch.env` resource before opening the app and rejects a launch
marker that resolves the sidecar to bare `python`, a missing Python path, a
non-executable Python path, or a missing brain directory.

The tray menu includes a `Launch at Login` toggle backed by macOS
`SMAppService.mainApp`; validate it from System Settings after toggling.
Use `Run Echo Smoke`, `Context Snapshot`, and `Capture Screen Smoke` from the
tray to quickly verify sidecar streaming, native context capture, and screen
capture without starting a full live prompt.
Use `Speak Last Response` to validate native TTS playback and the
amplitude-driven overlay pulse.
Use `Open Config Folder` from the tray or overlay menu to jump to the active
`.env` directory, whether that is the checkout or
`~/Library/Application Support/Wisp`.
The native Settings window keeps provider/model routing in the `Models` tab,
stored API keys in the `Keys` tab, and provider authentication in the `Auth`
tab. `Provider Auth` shows ChatGPT/GitHub auth status, browser/device sign-in,
and sign-out actions. `GitHub Copilot` keeps token save/test/clear controls
beside Copilot auth status. `API Keys` shows API-key status, save, and clear
actions. Those key actions call the Python brain sidecar and reuse the shared
OS-keychain secret store; stored key values are never shown and are not written
to `.env`.
The footer reset action asks for confirmation, clears stored credentials through
the Python sidecar, deletes the active `.env`, and reloads native settings.

## Native Test Button

For quick verification after a macOS parity change, double-click:

```bash
Test Wisp (Mac Native).command
```

or run:

```bash
bash scripts/run_macos_native_tests.command
```

That creates or refreshes the repo `.venv` from `requirements-macos.lock`, then
runs the offline Python brain tests, shared config environment tests, and Swift
package tests. Use `--build` to include `swift build`, or `--full` for the
slower package validation path. Use `--open` to run the full checks and then
launch the generated dev `.app` bundle through macOS `open`.

Quick-test logs are written to:

```bash
build_logs/macos_native_tests_<timestamp>/
```

Start with `summary.log`; individual command logs sit beside it. Both the quick
test runner and the full `macos_phase1_validate.sh` path copy
`docs/MACOS_LIVE_PARITY_CHECKLIST.md` to `live-parity-checklist.md` in the active
log folder so interactive Mac-only checks can be tracked beside the automated
result. The newest native test log path is also recorded at:

```bash
build_logs/latest_macos_native_tests.txt
build_logs/latest_macos_phase1.txt
```

You can also double-click `Open Wisp Mac Logs.command` from the repo root to
open the newest native macOS log folder, summary, and live checklist.

When the packaged app is launched outside the checkout and no validation log
environment is present, Wisp writes runtime artifacts and native launch markers
under:

```bash
~/Library/Logs/Wisp/
```

Packaged app settings are read from and saved to:

```bash
~/Library/Application Support/Wisp/.env
```

On first packaged launch, if that file does not exist yet, Wisp copies the
bundled `Contents/Resources/.env.example` template there. Existing user config
is never overwritten.

## Native Package Signing

To build a release-shaped app with an embedded runtime and Developer ID
signature, run this on macOS after storing notarization credentials in
notarytool:

```bash
WISP_PYTHON_RUNTIME_DIR=/path/to/python-runtime \
WISP_BUNDLE_IDENTIFIER=com.yourname.wisp \
WISP_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
WISP_NOTARY_PROFILE=wisp-notary \
bash scripts/macos_package_release.sh
```

`WISP_BUNDLE_IDENTIFIER` defaults to `com.wisp.native` for local package
testing. Set it to your real reverse-DNS app id before shipping; the package
script rejects the dev identifier `dev.wisp.native` so a signed release cannot
accidentally share the development app identity.

By default the script signs with `macos/Wisp.entitlements`, which allows
microphone input, Apple Events automation prompts, and loading the bundled
Python/native-wheel libraries under the hardened runtime. Override with
`WISP_CODESIGN_ENTITLEMENTS=/path/to/entitlements.plist` when testing a stricter
release profile.

For a local signed-only package, add `WISP_SKIP_NOTARIZATION=1`. The packaging
script first runs an embedded Python import probe against the bundled runtime,
then writes logs under `build_logs/macos_package_<timestamp>/`, copies the live
parity checklist into that log folder, and creates a zip under
`build/WispNative/`. When notarization is enabled, the final zip is created after
stapling so it contains the notarized app.
The newest package log path is recorded at:

```bash
build_logs/latest_macos_package.txt
```

To make the package script launch the signed app through macOS `open` and wait
for `native-app-launch.log`, add:

```bash
WISP_VALIDATE_APP_LAUNCH=1
```

The package script copies the signed app to a temporary folder outside the
checkout, verifies that copied app's code signature, clears Wisp dev/test environment variables, and then calls `open`, so this launch behaves like
Finder/LaunchServices instead of reusing the repo-local dev bundle location.
The signed app writes that marker under `~/Library/Logs/Wisp/`; the package
script copies it back into the same `build_logs/macos_package_<timestamp>/`
folder as release evidence.
`brain_python` must point at the temporary app copy's embedded
`Contents/Resources/python-runtime/bin/python3`, and the marker must prove that
path exists, is executable, and can see the bundled brain directory. The package
script removes dev-only `dev-launch.env` state before signing, writes a zipped log archive
under `build_logs/`, and copies recent Wisp/Python/Swift crash reports into
`build_logs/macos_package_<timestamp>/crash_reports/` when the flow fails. A
complete public release still requires a real Developer ID identity, successful
notarization, and this signed-app launch validation on a Mac.
