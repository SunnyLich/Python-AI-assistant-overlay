# Wisp Windows/macOS Parity Contract

The macOS version must match the Windows Wisp workflow, but it no longer has to
reuse the Python/Qt UI. The macOS product surface is the Swift/AppKit app under
`macos/Sources/Wisp`; Python remains the brain sidecar for OS-agnostic backend
work until each visible surface is ported.

## Product Rule

1. Windows remains implemented by `main.py` and `ui/`.
2. macOS visible UI belongs in Swift/AppKit under `macos/Sources/Wisp`.
3. Both platforms must keep the same `.env` keys, caller/intents model, prompt
   semantics, LLM routing, memory behavior, tools, and agent contracts.
4. The macOS Swift app should not depend on the Python/Qt UI bridge for visible
   product windows.

## Source Of Truth

| Layer | Shared contract | Windows implementation | macOS implementation |
|---|---|---|---|
| Floating overlay | Wisp state art: idle/listening/thinking/speaking | Qt overlay | Swift `OverlayPanel` |
| Tray/menu | Ask, chat, memory, plugins, settings, voice, quit | Qt tray | Swift `StatusItemController` |
| Caller intents/hotkeys | `CALLER_*` config, caller hotkeys, W/A/D/custom picker | Qt `IntentOverlay` | Swift `HotkeyController` + `IntentPanel` |
| Prompt/query brain | `core.query_pipeline`, `core.llm_clients` | Direct Python calls | Python sidecar `brain.query` and `brain.rewrite` |
| Native context | app/window/selected/clipboard/screenshot policy | Win32/Python helpers | Swift `NativeContextController`, `NativePastebackController`, and `ScreenCaptureController` |
| Voice/TTS | same provider config and playback behavior | Python audio path | Swift capture/playback with buffered native context + Python sidecar models |
| Chat | multi-turn conversation history and streaming replies | Qt window | Swift `ChatPanel` with native history controls in progress |
| Memory | durable facts: list, add, edit, delete, search | Qt `MemoryViewer` | Swift `MemoryPanel` + `brain.memory.*` |
| Settings | shared `.env` config, models, callers, voice, memory knobs | Qt `SettingsDialog` | Swift `SettingsPanel` with LLM/TTS route tests in progress |
| Plugins | loaded/discoverable plugin list, hooks, tools, folder open | Qt `PluginManagerDialog` | Swift `PluginManagerPanel` with action execution in progress |
| Snip overlay | drag-select region, attach image to intent query | Qt `SnipOverlay` | Swift `SnipOverlayPanel` with multi-display selection in progress |
| Agents | same user-facing task/history windows and config | Qt windows | Swift `AgentTaskPanel` multi-agent/communication setup + `AgentHistoryPanel` with retry/continue and `AgentDiffPanel` in progress |
| Packaging | distributable app | PyInstaller path | Swift `.app` bundle path |

## Capability Status

| Capability | Windows | Swift macOS |
|---|---:|---:|
| Native launch | Done | In progress |
| Floating overlay | Done | Done |
| Tray/menu | Done | Partial |
| Caller intent picker/hotkeys | Done | In progress native |
| Text prompt/query | Done | In progress through `brain.query` |
| Streaming reply surface | Done | In progress native bubble |
| Selected text and ambient context | Done | In progress native snapshot |
| Screenshot context | Done | Partial |
| Voice query | Done | In progress native with buffered context |
| TTS playback | Done | In progress native playback lifecycle |
| Chat window | Done | In progress native |
| Settings window | Done | In progress native |
| Memory window | Done | In progress native |
| Plugin manager | Done | In progress native |
| Snip overlay | Done | In progress native |
| Agent task window | Done | In progress native |
| Agent task history | Done | In progress native |
| Packaging | Partial | Partial dev app bundle |

## Current Migration Order

1. Replace the prototype Swift prompt entry with the Windows caller/intent
   workflow backed by the same `CALLER_*` keys.
2. Make the Swift response surface behave like the Windows bubble/chat stream.
3. Broaden native agent task/history controls to match Windows visual communication map and remaining run review flows.
4. Finish native paste-back, context buffering, and voice parity.
5. Build a signed/notarized `.app`.

## Stability Gates

Native macOS code should live in Swift/AppKit/AVFoundation helpers or isolated
Python sidecar calls. Python packages that enter macOS frameworks or native ML /
vector runtimes from worker threads must stay out of the hot UI process and
remain explicit opt-ins until validated.
