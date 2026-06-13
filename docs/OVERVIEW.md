# Wisp Code Overview

Wisp is a desktop assistant with one Python application architecture across
Windows, macOS, and Linux. The shared pure-Python supervisor splits UI, native
OS work, audio, and brain/model work into isolated worker processes.

## Top-Level Layout

- `macos_py/supervisor/` wires the application together: worker lifecycle,
  hotkeys, context buffering, memory, intent flow, chat windows, and voice/snip
  interactions.
- `main.py` is the legacy single-process Qt entrypoint kept temporarily while
  the supervisor path becomes the only app runtime.
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
- Root-level modules such as `core.llm`, `core.agent_runner`, `ui.settings`, and
  `ui.agent_task_mockup` are compatibility aliases for older imports.
- `addons/` contains process-hosted addons discovered by `core.addon_manager`.
- `tools/installed/` is the legacy local script-tool directory discovered by
  `core.tool_registry`.
- `tests/` covers parser helpers, config/settings behavior, model route
  fallback logic, tool discovery, secret storage, TTS config, macOS worker
  boundaries, and the agent runner.
- `macos_py/` contains the pure-Python supervisor, worker processes, and the
  headless brain package used by the app.

## Addon Contract

Addons belong in the shared Python runtime layer: Python packages under
`addons/<id>/` with an `addon.toml` manifest. Each enabled addon runs in its own
Python host process and communicates with Wisp over JSON IPC. Both Windows and
macOS discover the same addon metadata, hooks, tray actions, settings, and
model-callable tools from that shared layer.

The old `core.plugin_manager` import path remains as a compatibility facade for
callers and older addon code, but new work should use addon naming and
`core.addon_manager`.

## Runtime Flow

1. `macos_py.supervisor` starts the UI, native, audio, and brain workers, then
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
- The largest extraction candidates are `core/agent_runner.py`,
  `ui/agent_task_mockup.py`, `ui/settings.py`, `core/llm.py`, and
  `core/context_fetcher.py`. Split these only around stable boundaries such as data
  models, provider adapters, view components, and pure parsers.
- Keep UI modules responsible for presentation and user interaction; move pure
  parsing, formatting, and persistence helpers into `core/` or small UI helper
  modules with tests.
- Configuration changes should include a settings test and a reload test when
  the value can be changed at runtime.

## Development Checks

Run the full test suite from the repository root:

```powershell
pytest
```
