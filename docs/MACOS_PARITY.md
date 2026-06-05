# Wisp Windows/macOS Parity Contract

The macOS version must look and behave like the Windows version. Only the
platform implementation behind the shared UI may differ.

## Product Rule

`main.py` and the `ui/` Qt widgets are the product UI source of truth on both
Windows and macOS:

1. Same floating Wisp icon and state art.
2. Same speech bubble behavior.
3. Same caller hotkeys and WASD/custom intent picker.
4. Same chat, settings, memory, plugin manager, snip, and agent windows.
5. Same `.env` configuration keys and prompt/query semantics.

macOS-specific code may handle native capture, key injection, focus, audio,
permissions, packaging, and crash isolation. It must not replace the visible
workflow with a separate Swift/AppKit product surface.

## Source Of Truth

| Layer | Source of truth | Windows implementation | macOS implementation |
|---|---|---|---|
| Product UI | `ui/`, launched by `main.py` | Qt widgets | Same Qt widgets |
| App launch | Root launchers | `Start Wisp.bat` -> `main.py` | `Start Wisp.command` -> `main.py` |
| LLM routing, prompts, tools | `core/llm_clients`, `core/query_pipeline`, `core/tool_registry` | Direct Python calls | Same direct Python calls |
| Memory | `core/memory_store` | Direct Python calls | Same direct Python calls |
| Agent runtime | `core/agent` | Direct Python calls | Same direct Python calls |
| Config and secrets | `.env`, `config.py`, `core/secret_store` | Qt Settings | Same Qt Settings |
| Hotkeys | `core/hotkeys.py`, `config.CALLER_ROWS` | Win32 hotkeys | Carbon hotkeys behind the same caller flow |
| Capture/context/focus | `core/capture.py`, `core/context_fetcher.py`, `core/platform_utils.py` | Win32/Python helpers | macOS helpers behind the same UI |
| Audio/STT/TTS | `core/audio.py`, `core/stt.py`, `core/tts.py` | Python audio path | Python audio path with macOS main-thread/helper guards |

The Swift/AppKit tree under `macos/` is experimental native-service work. It may
be used for validation or future backend helpers, but it is not the default
macOS product UI.

## Capability Contract

These are the cross-platform capabilities the product expects. Each platform can
implement the backend differently, but the user-facing behavior must match.

| Capability | Shared contract | Windows status | macOS status |
|---|---|---:|---:|
| Launch app | Root launcher starts `main.py` | Done | Done |
| Floating overlay | Wisp state art: idle/listening/thinking/speaking | Done | Shared Qt UI |
| Tray/menu | Ask, chat, memory, plugins, settings, quit | Done | Shared Qt UI |
| Intent picker | Caller rows from `CALLER_*`; W/A/D + custom prompt | Done | Shared Qt UI |
| Text prompt | Query path selected through the intent picker | Done | Shared Qt UI |
| Streaming reply | Bubble and chat receive streamed chunks | Done | Shared Qt UI; macOS safe mode may yield one final chunk for OpenAI-compatible routes |
| Selected text | Include selected text when enabled | Done | macOS backend helper |
| Clipboard context | Include clipboard when enabled | Done | macOS backend helper |
| Screenshot context | Caller config controls screenshot behavior | Done | macOS backend helper |
| Ambient app/window context | Active app/window/document context | Done | macOS backend helper |
| Voice query | Push-to-talk, local STT, query | Done | Backend in progress |
| TTS playback | Stream reply audio and sync bubble | Done | Disabled in macOS safe mode; opt in with `WISP_MACOS_ENABLE_AUDIO=1` while validating CoreAudio stability |
| Memory add/search | `core.memory_store` | Done | Shared Qt UI |
| Settings UI | Edits same `.env` keys | Done | Shared Qt UI |
| Chat window | Persistent conversation UI | Done | Shared Qt UI |
| Snip overlay | Region capture UI | Done | Shared Qt UI plus macOS capture helper |
| Agent task window | Run/inspect agent tasks | Done | Shared Qt UI |
| Plugin/tool manager | Manage tool plugins | Done | Shared Qt UI |
| Packaging | Distributable app | PyInstaller path | Missing |

## UI Parity Rules

1. New visible UI belongs in `ui/` unless it is a platform permission dialog or
   packaging-only shell.
2. Mac-only permission prompts must be additive, not a different product flow.
3. If a macOS backend cannot support a shared UI action yet, disable or explain
   that action in the shared UI instead of replacing the workflow.
4. Any platform-specific code should live under `core/platform*`,
   `core/macos_helper`, or packaging/launcher scripts.
5. The default macOS launcher must continue to start the shared Qt app.

## Definition Of Done For A Shared Feature

A feature is parity-ready only when:

1. The same visible UI path is used on Windows and macOS.
2. Windows and macOS use the same config keys and prompt/query semantics.
3. Platform-specific work is hidden behind `core/` contracts or helper
   processes.
4. There is at least one platform-neutral Python test for shared behavior.
5. macOS backend limitations are documented in this table rather than papered
   over with a separate UI.

## Near-Term macOS Parity Order

1. Keep `Start Wisp.command` launching `main.py`.
2. Validate the shared Qt overlay, tray, intent picker, chat, settings, memory,
   plugins, snip, and agent windows on a Mac.
3. Finish backend parity for voice and TTS without changing the visible UI.
4. Keep the Swift/AppKit prototype available only for native-service
   experiments until it can support the same shared UI contract.
5. Build signed/notarized packaging around the shared UI.

## macOS Stability Gates

`WISP_MACOS_SAFE_MODE` is enabled by default on macOS. It keeps the shared UI
intact while routing crash-prone backend work through conservative paths. Set
`WISP_MACOS_SAFE_MODE=0` only when validating the native stack on a Mac.

- OpenAI-compatible streaming routes are non-streaming in safe mode after a
  reproducible segfault in `client.chat.completions.create(..., stream=True)`.
  This covers query, vision, rewrite, chat, and route-test probes. The same
  prompt is yielded into the same bubble/chat UI as one final chunk. Set
  `WISP_MACOS_OPENAI_COMPAT_STREAMING=1` only when validating a runtime fix
  (`WISP_MACOS_GOOGLE_STREAMING=1` remains as a Google-only validation switch).
- OpenAI-compatible live context/screenshot tools are disabled in safe mode; set
  `WISP_MACOS_ENABLE_OPENAI_TOOLS=1` only while validating that tool loop.
- In-process macOS audio, filler playback, TTS prewarm, and TTS playback are
  disabled in safe mode so text prompts can complete without touching CoreAudio
  inside the Qt process. Set `WISP_MACOS_ENABLE_AUDIO=1` only when testing audio.
- STT model prewarm is skipped in safe mode unless `WISP_MACOS_ENABLE_AUDIO=1`
  or `WISP_MACOS_ENABLE_STT_PREWARM=1` is set.
- The optional background filesystem watcher is disabled in safe mode; set
  `WISP_MACOS_ENABLE_FS_WATCHER=1` only when validating watchdog on macOS.
- ChromaDB-backed semantic memory and background memory LLM jobs are disabled in
  safe mode; memory falls back to plain JSON storage. Set
  `WISP_MACOS_ENABLE_CHROMADB=1` or
  `WISP_MACOS_ENABLE_MEMORY_BACKGROUND_LLM=1` only while validating those paths.
