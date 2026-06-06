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
and the current Swift parity slice. The generated dev bundle lives at
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
using the embedded runtime.

Use `--run` when you want the app attached to Terminal stdout/stderr. Use
`--open` when you want to test the generated `.app` bundle the way Finder opens
it, without relying on `WISP_BRAIN_*` environment variables:

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
app reached native startup after LaunchServices opened it.

The tray menu includes a `Launch at Login` toggle backed by macOS
`SMAppService.mainApp`; validate it from System Settings after toggling.
Use `Speak Last Response` to validate native TTS playback and the
amplitude-driven overlay pulse.
Use `Open Config Folder` from the tray or overlay menu to jump to the active
`.env` directory, whether that is the checkout or
`~/Library/Application Support/Wisp`.
The native Settings window includes a `Keys` tab for API-key status, save, and
clear actions. Those actions call the Python brain sidecar and reuse the shared
OS-keychain secret store; stored key values are never shown and are not written
to `.env`.
It also includes an `Auth` tab for ChatGPT/GitHub/Copilot auth status,
ChatGPT browser sign-in launch, GitHub device sign-in, sign-out actions, and
Copilot token save/test/clear through the same shared auth modules used by
Windows.

## Native Test Button

For quick verification after a macOS parity change, double-click:

```bash
Test Wisp (Mac Native).command
```

or run:

```bash
bash scripts/run_macos_native_tests.command
```

That runs the offline Python brain tests, shared config environment tests, and
Swift package tests. Use `--build` to include `swift build`, or `--full` for the
slower provisioning/package validation path. Use `--open` to run the full checks
and then launch the generated dev `.app` bundle through macOS `open`.

Quick-test logs are written to:

```bash
build_logs/macos_native_tests_<timestamp>/
```

Start with `summary.log`; individual command logs sit beside it.

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
WISP_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
WISP_NOTARY_PROFILE=wisp-notary \
bash scripts/macos_package_release.sh
```

By default the script signs with `macos/Wisp.entitlements`, which allows
microphone input, Apple Events automation prompts, and loading the bundled
Python/native-wheel libraries under the hardened runtime. Override with
`WISP_CODESIGN_ENTITLEMENTS=/path/to/entitlements.plist` when testing a stricter
release profile.

For a local signed-only package, add `WISP_SKIP_NOTARIZATION=1`. The packaging
script first runs an embedded Python import probe against the bundled runtime,
then writes logs under `build_logs/macos_package_<timestamp>/` and creates a zip
under `build/WispNative/`. When notarization is enabled, the final zip is
created after stapling so it contains the notarized app.

To make the package script launch the signed app through macOS `open` and wait
for `native-app-launch.log`, add:

```bash
WISP_VALIDATE_APP_LAUNCH=1
```

That marker is expected in the same `build_logs/macos_package_<timestamp>/`
folder. The package script also writes a zipped log archive under `build_logs/`
and copies recent Wisp/Python/Swift crash reports into
`build_logs/macos_package_<timestamp>/crash_reports/` when the flow fails. A
complete public release still requires a real Developer ID identity, successful
notarization, and this signed-app launch validation on a Mac.
