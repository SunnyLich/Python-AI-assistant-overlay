# Wisp Structure Cleanup Plan

This is the working plan for bringing the project structure closer to a
standard Python desktop application without interrupting product work.

## Decisions

- Keep Python pinned to `3.12.13` for now. The project, CI, launchers, and
  PyInstaller packaging already target 3.12. Moving to 3.14 should wait until
  the GUI/audio/native dependency stack officially supports it.
- Treat `macos_py.supervisor.app` as the primary runtime entrypoint. The
  platform launchers should continue to start this module.
- Keep `main.py` only as a temporary legacy entrypoint until packaged builds
  and any remaining callers are moved to the supervisor runtime.
- Keep user data (`.env`, chats, memory, addon installs) outside versioned
  source. Runtime logs and build outputs stay ignored.

## In This Cleanup Pass

- Update CI triggers so changes under `macos_py/**` and nested runtime packages
  run the relevant checks.
- Add standard Python tooling metadata in `pyproject.toml`.
- Add `requirements-dev.txt` for test/lint/type tooling.
- Keep auto venv launchers runtime-only for normal users.
- Add explicit contributor setup scripts for Windows PowerShell and
  macOS/Linux shell users that install `requirements-dev.txt` into the local
  project venv.
- Add a typed settings snapshot object while preserving current module-level
  `config.*` compatibility.
- Start replacing direct LLM `print()` diagnostics with structured logging.
- Keep Ruff/MyPy as a focused cleanup baseline until legacy modules are ready
  for repo-wide lint/type enforcement.
- Update architecture docs so they match the current supervisor-first runtime.

## Follow-Up Refactors

- Split `core.llm_clients.client` by provider and request building:
  `messages.py`, `openai_compat.py`, `anthropic.py`, `codex.py`, and
  `fallbacks.py`.
- Split `macos_py.supervisor.flows.FlowController` by workflow:
  hotkeys, chat, voice/dictation, snip, plugins, memory, and agent tasks.
- Split `ui.settings_panel.dialog.SettingsDialog` into tab/page builders and
  keep only dialog orchestration in the main class.
- Split `ui.agent.task_window` into task form, communication map, history/run
  dialogs, and reusable combo/value helpers.

## Plain-English Notes

- CI path filters decide which changed files wake up GitHub Actions. If a
  folder is missing from those filters, GitHub may skip tests even when runtime
  code changed.
- Lower-bound-only dependencies (`package>=x`) allow newer package versions to
  install later. That is flexible, but less reproducible than a lockfile.
- The current test count is healthy for this app size. The issue is not "too
  many tests"; it is making the fast/local checks easy to run consistently.
