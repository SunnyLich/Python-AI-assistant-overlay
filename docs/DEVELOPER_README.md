# Wisp Developer README

This document is the practical entry point for working on Wisp. The root
`README.md` explains the product; this file explains how the codebase is put
together, how to run it locally, and where to look when changing behavior.

For a guided visual walkthrough, open `docs/factory_tour/index.html`. It treats
the app like a factory tour, with tabs for each runtime floor and links into the
source files.

## Quick Setup

Wisp is pinned to Python `3.12.13`.

On Windows:

```powershell
.\scripts\setup_dev.ps1
.\.venv\Scripts\python.exe scripts\check_dev_environment.py
.\.venv\Scripts\python.exe -m pytest
```

On macOS or Linux:

```bash
bash scripts/setup_dev.sh
.venv/bin/python scripts/check_dev_environment.py
.venv/bin/python -m pytest
```

The platform launchers also provision dependencies before starting the app:

```powershell
.\Start Wisp.bat
```

```bash
bash "Start Wisp.command"
bash "Start Wisp.sh"
```

Normal launchers keep runtime logs off unless Wisp exits abruptly. Use
`Start Wisp Debug.bat`, `Start Wisp Debug.command`, or `Start Wisp Debug.sh`
to keep full runtime logs during local debugging.

## Runtime Entrypoints

- `runtime/supervisor/app.py` is the current supervisor-first app entrypoint.
  It starts isolated worker processes, installs logging/crash diagnostics, and
  wires product flows through `FlowController`.
- `config.py` loads `.env`, resolves secrets, builds typed settings snapshots,
  and preserves compatibility module globals for older callers.

## Code Map

- `core/` contains shared runtime services: model routing, audio/TTS/STT,
  context capture, memory, auth, addons, settings models, hotkeys, and agent
  execution.
- `ui/` contains Qt widgets and dialogs. UI modules should own presentation,
  signal wiring, and user interaction; pure parsing and persistence should live
  in `core/` or small tested helpers.
- `runtime/` contains the process supervisor, worker hosts, worker protocol,
  and the headless brain package used by the app.
- `addons/` contains process-hosted addon packages with `addon.toml` manifests.
- `model_tools/` and `tools/installed/` contain model-callable or legacy local
  tool definitions.
- `tests/` covers settings, routing, worker boundaries, UI helpers, memory,
  agent behavior, addon behavior, and platform safety.
- `experiments/` contains exploratory code that is not part of the stable app
  contract but can inform future features.

## Worker Architecture

The supervisor starts four primary workers:

- `wisp-ui` owns all PySide widgets and emits user events.
- `wisp-native` owns OS integration such as hotkeys, focused-window context,
  screenshot capture, clipboard access, and pasteback.
- `wisp-audio` owns audio playback, TTS, recording, and STT imports.
- `wisp-brain` owns model calls, memory, addons, tools, chat, and agent runs.

Workers communicate with newline-delimited JSON over stdin/stdout. The
supervisor assigns request ids, matches responses, forwards scoped stream
events, captures stderr, and shuts workers down together.

See `docs/factory_tour/index.html` for a visual factory tour, and
`docs/COMMUNICATION_GRAPH.md` for diagrams of the process graph and common
feature flows.

## Common Flows

- Hotkey query: native hotkey -> supervisor flow -> native context capture ->
  UI intent picker -> brain query stream -> UI bubble/chat updates -> optional
  audio playback.
- Snip query: native or UI snip request -> screenshot capture -> intent picker
  -> brain vision query -> UI/audio response.
- Rewrite and pasteback: caller config enables pasteback -> brain rewrite ->
  native pasteback into the original focused app.
- Chat: UI chat request -> supervisor -> brain chat stream -> UI chat window.
- Memory: UI memory viewer edits are proxied through supervisor to brain memory
  handlers; query-time retrieval happens in brain/core memory code.
- Addons: brain discovers enabled addons, starts addon host processes, applies
  hooks/tools/actions, and reports addon-manager state back to UI.

## Configuration And Data

- `.env` is local and gitignored. Start from `.env.example`.
- `core/system/paths.py` defines canonical runtime paths.
- `memory/` and `chats/` are gitignored user data.
- `build_logs/` stores runtime logs and worker stderr logs during local runs.
- Secrets are loaded through `core.secret_store` and provider-specific auth
  helpers under `core/auth/`.

## Tests And Checks

Check that the local `.venv` uses the pinned Python and has runtime/developer tools:

```powershell
.\.venv\Scripts\python.exe scripts\check_dev_environment.py
```

Run the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Focused lint/type baseline used by the current README:

```powershell
.\.venv\Scripts\python.exe -m ruff check core\context_hotkey.py core\llm_clients\messages.py runtime\supervisor\tool_modes.py ui\agent\combo_helpers.py ui\settings_panel\helpers.py tests\test_context_hotkey_snapshot.py
.\.venv\Scripts\python.exe -m mypy core\settings_model.py core\llm_clients\logging_utils.py runtime\supervisor\tool_modes.py ui\agent\combo_helpers.py --follow-imports=skip
```

On macOS, the focused worker gate is:

```bash
bash scripts/run_macos_tests.command
```

## Documentation Conventions

- Every Python module should start with a module docstring describing the file's
  responsibility.
- Every class, function, method, and nested helper should have a concise
  docstring as its first statement.
- Prefer behavior summaries over line-by-line narration.
- Preserve compatibility modules as thin forwarding layers and document that
  relationship directly.
- Document intentionally invalid fixtures with comments, but do not repair their
  invalid syntax unless a test explicitly changes.

## Debugging Notes

- Debug supervisor logs are written under `build_logs/wisp_runtime_*`.
- Normal launcher runs only write `build_logs/wisp_crash_*` after an abrupt
  supervisor or UI-worker exit.
- `build_logs/latest_wisp_runtime.txt` points at the newest supervisor log
  directory when the supervisor entrypoint created it.
- UI freeze reports are emitted by the UI worker watchdog when the Qt event loop
  or IPC pump stalls.
- Worker stderr is captured by the supervisor and surfaced in timeout errors.

## Change Guidelines

- Keep shared contracts and pure helpers in `core/`.
- Keep Qt widget construction and signal wiring in `ui/`.
- Keep OS-specific or native-library ownership inside the responsible worker.
- Prefer typed settings snapshots from `config.get_settings()` for new code.
- Add or update tests when changing config parsing, model routing, memory,
  worker protocol, addon behavior, or agent execution.
