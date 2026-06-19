# Wisp Code Overview

Wisp is a desktop assistant with one Python application architecture across
Windows, macOS, and Linux. The shared pure-Python supervisor splits UI, native
OS work, audio, and brain/model work into isolated worker processes.

## Top-Level Layout

- `runtime/supervisor/` is the primary runtime entrypoint and wires the
  application together: worker lifecycle,
  hotkeys, context buffering, memory, intent flow, chat windows, and voice/snip
  interactions.
- `config.py` loads runtime configuration from `.env` and keychain-backed
  secrets.
- `core/` contains service and integration code: LLM routing, audio, TTS/STT,
  capture, context fetching, memory, auth, tool discovery, and agent execution.
- `core/system/` defines canonical paths, platform setup, and environment-file
  helpers.
- `core/agent/` contains the serializable agent task contract, runner runtime
  primitives, and scoped agent execution.
- `core/auth/` contains provider authentication helpers for ChatGPT, GitHub, and
  Copilot.
- `core/llm_clients/` contains the streaming LLM client and fallback route
  parsing for LLM requests.
- `core/memory_store/` contains memory storage and explicit memory-command
  parsing.
- `ui/` contains Qt widgets and dialogs: the icon overlay, chat window,
  settings, intent picker, snip overlay, memory viewer, and agent task UI.
- `ui/agent/`, `ui/settings_panel/`, and `ui/shared/` group the largest UI
  domains and shared widget helpers.
- Compatibility modules should stay thin. New runtime work belongs under
  `runtime/`, `core/`, or `ui/` according to ownership.
- `addons/` contains process-hosted addons discovered by `core.addon_manager`.
- `tools/installed/` is the legacy local script-tool directory discovered by
  `core.tool_registry`.
- `tests/` covers parser helpers, config/settings behavior, model route
  fallback logic, tool discovery, secret storage, TTS config, runtime worker
  boundaries, and the agent runner.
- `runtime/` contains the pure-Python supervisor, worker processes, and the
  headless brain package used by the app.

## Addon Contract

Addons belong in the shared Python runtime layer: Python packages under
`addons/<id>/` with an `addon.toml` manifest. Each enabled addon runs in its own
Python host process and communicates with Wisp over JSON IPC. Both Windows and
macOS discover the same addon metadata, hooks, tray actions, settings, and
model-callable tools from that shared layer.

The old `core.addon_manager` import path remains as a compatibility facade for
callers and older addon code, but new work should use addon naming and
`core.addon_manager`.

## Runtime Flow

1. `runtime.supervisor` starts the UI, native, audio, and brain workers, then
   registers platform hotkeys through the native worker.
2. A caller hotkey captures selected text or a screen snippet, then opens the
   intent picker.
3. The selected intent builds a prompt with optional ambient context, documents,
   tool access, memory retrieval, and screenshots.
4. `core.llm` routes the request to the configured provider with fallback
   routes where enabled.
5. Reply chunks feed the overlay/chat UI and TTS stream while memory receives
   the completed exchange.

## Quality Notes

- `core.system.env_utils` centralizes env-file IO and typed environment parsing. Prefer
  it over ad hoc `int(os.getenv(...))` and string-only boolean checks.
- Keep shared contracts and pure helpers in `core/`; keep PyQt object creation,
  signal wiring, and widget layout in `ui/`.
- The largest extraction candidates are `core/llm_clients/client.py`,
  `runtime/supervisor/flows.py`, `ui/settings_panel/dialog.py`, and
  `ui/agent/task_window.py`. Split these only around stable boundaries such as
  data models, provider adapters, workflow controllers, view components, and
  pure parsers.
- Prefer typed settings snapshots from `config.get_settings()` for new code.
  The module-level `config.*` names remain for compatibility with existing code
  and tests.
- Prefer named loggers over direct `print()` diagnostics. Worker stdout/stderr
  is still captured, but structured logger names make runtime logs searchable.
- Keep UI modules responsible for presentation and user interaction; move pure
  parsing, formatting, and persistence helpers into `core/` or small UI helper
  modules with tests.
- Configuration changes should include a settings test and a reload test when
  the value can be changed at runtime.

## Development Checks

Run the full test suite from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\check_dev_environment.py
.\.venv\Scripts\python.exe -m pytest
```

On Windows, the pytest harness suppresses modal native crash dialogs from child
processes so a failing worker cannot block the run. If you still see repeated
`python.exe` access-violation events, check the venv version first: Wisp is
pinned to Python `3.12.13`, and a stale Python 3.14 venv should be rebuilt with
`.\scripts\setup_dev.ps1`.

Run the current focused lint/type baseline with:

```powershell
.\.venv\Scripts\python.exe -m ruff check core\context_hotkey.py core\llm_clients\messages.py runtime\supervisor\tool_modes.py ui\agent\combo_helpers.py ui\settings_panel\helpers.py tests\test_context_hotkey_snapshot.py
.\.venv\Scripts\python.exe -m mypy core\settings_model.py core\llm_clients\logging_utils.py runtime\supervisor\tool_modes.py ui\agent\combo_helpers.py --follow-imports=skip
```
