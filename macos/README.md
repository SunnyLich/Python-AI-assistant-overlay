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
`Contents/Resources`: `brain`, `core`, and `assets/doll`. When launched from
Finder, that dev bundle can infer the checkout-relative `.venv` and
`macos/brain` sidecar from its `build/WispNative` location. A fully standalone
bundle still needs `Contents/Resources/python-runtime/bin/python3` plus signing
and notarization.

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
