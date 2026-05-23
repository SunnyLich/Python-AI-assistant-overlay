# Wisp Code Overview

Wisp is a Windows-first desktop assistant. The app is organized around a small
PyQt overlay, global hotkeys, context capture, model routing, streaming audio,
and local memory.

## Top-Level Layout

- `main.py` wires the application together: Qt lifecycle, hotkeys, context
  buffering, memory, intent flow, chat windows, and voice/snip interactions.
- `config.py` loads runtime configuration from `.env` and keychain-backed
  secrets.
- `core/` contains service and integration code: LLM routing, audio, TTS/STT,
  capture, context fetching, memory, auth, tool discovery, and agent execution.
- `ui/` contains PyQt widgets and dialogs: the doll overlay, chat window,
  settings, intent picker, snip overlay, memory viewer, and agent task UI.
- `tools/installed/` is the local script-tool plugin directory discovered by
  `core.tool_registry`.
- `tests/` covers parser helpers, config/settings behavior, model route
  fallback logic, tool discovery, secret storage, TTS config, and the agent
  runner.

## Runtime Flow

1. `main.App` starts Qt, registers hotkeys, initializes the overlay, and warms
   TTS/STT connections.
2. A caller hotkey captures selected text or a screen snippet, then opens the
   intent picker.
3. The selected intent builds a prompt with optional ambient context, documents,
   tool access, memory retrieval, and screenshots.
4. `core.llm` routes the request to the configured provider with fallback
   routes where enabled.
5. Reply chunks feed the overlay/chat UI and TTS stream while memory receives
   the completed exchange.

## Quality Notes

- `core.env_utils` centralizes env-file IO and typed environment parsing. Prefer
  it over ad hoc `int(os.getenv(...))` and string-only boolean checks.
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
