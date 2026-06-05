# Wisp macOS Native Experiments

The default macOS product is the shared Python/Qt app launched from the repo
root with `Start Wisp.command`. It should match the Windows UI in look,
workflow, and functionality. Platform-specific macOS behavior belongs behind
that UI in `core/platform*`, `core/macos_helper`, launchers, or packaging.

This `macos/` directory contains experimental native-service work:

```text
macos/
  brain/             Python sidecar protocol prototype
  Sources/Wisp/      Swift/AppKit prototype host
  Tests/WispTests/   Swift protocol tests
  ui_host/           Qt host used by the Swift prototype
```

These pieces are useful for validating native macOS services such as sidecar
IPC, AppKit capture, Swift audio recording/playback, and future packaging. They
are not the default product UI.

## Default Mac Launch

From the repo root, double-click:

```bash
Start Wisp.command
```

That launcher creates or refreshes `.venv` from `requirements-macos.lock`, then
runs:

```bash
.venv/bin/python main.py
```

The visible overlay, tray menu, intent picker, chat, settings, memory, plugin
manager, snip overlay, and agent windows therefore come from the same `ui/`
modules used on Windows.

## Experimental Native Launch

To run the Swift/AppKit prototype on a Mac:

```bash
bash scripts/macos_phase1_validate.sh --run
```

or double-click:

```bash
Start Wisp (Mac Native).command
```

This path validates the Python sidecar, Swift package, and prototype menubar
host. Treat it as backend research unless it is explicitly brought back into the
shared UI parity contract in `docs/MACOS_PARITY.md`.
