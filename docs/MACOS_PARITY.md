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
| Floating overlay | Wisp state art: idle/listening/thinking/speaking | Qt overlay | Swift `OverlayPanel` with shared icon sizing and auto-hide |
| Tray/menu | Ask, chat, memory, plugins, settings, voice, quit | Qt tray | Swift `StatusItemController` plus overlay right-click menu |
| Caller intents/hotkeys | `CALLER_*` config, caller hotkeys, W/A/D/custom picker | Qt `IntentOverlay` | Swift `HotkeyController` + `IntentPanel` |
| Prompt/query brain | `core.query_pipeline`, `core.llm_clients` | Direct Python calls | Python sidecar `brain.query` and `brain.rewrite` |
| Native context | app/window/selected/clipboard/screenshot policy | Win32/Python helpers | Swift `NativeContextController` with hardened AX reads, `NativePastebackController`, and `ScreenCaptureController` |
| Voice/TTS | same provider config and playback behavior | Python audio path | Swift capture/playback with buffered native context + Python sidecar models |
| Response bubble | live compact reply surface, reveal timing, click-through to chat | Qt `SpeechBubble` | Swift `ResponseBubblePanel` with native word reveal, read-word highlight, and shared sizing/colors |
| Chat | multi-turn conversation history, auto-elaborate, and streaming replies | Qt window | Swift `ChatPanel` with native history controls, streaming replies, and auto-elaborate |
| Memory | durable facts: list, add, edit, delete, search | Qt `MemoryViewer` | Swift `MemoryPanel` + `brain.memory.*` |
| Settings | shared `.env` config, models, callers, voice, memory, chat, UI knobs, OS-keychain API keys, provider auth, factory reset | Qt `SettingsDialog` | Swift `SettingsPanel` with LLM/TTS tests, caller editing, shared UI keys, API key status/save/clear, provider-auth status, ChatGPT browser sign-in, GitHub device sign-in, Copilot token save/test/clear, and reset-all via the Python sidecar |
| Plugins | loaded/discoverable plugin list, hooks, tools, folder open, tray actions | Qt `PluginManagerDialog` | Swift `PluginManagerPanel` with action execution and status refresh |
| Snip overlay | drag-select region, attach image to intent query | Qt `SnipOverlay` | Swift `SnipOverlayPanel` + `ScreenCaptureController` region capture |
| Agents | same user-facing task/history windows and config | Qt windows | Swift `AgentTaskPanel`, `AgentHistoryPanel`, and `AgentDiffPanel` |
| Packaging | distributable app | PyInstaller path | Swift dev `.app` bundle with staged `brain`, `core`, art resources, optional embedded Python runtime, embedded-runtime import probe, hardened-runtime entitlements, and scripted signing/notarization flow |

## Capability Status

| Capability | Windows | Swift macOS |
|---|---:|---:|
| Native launch | Done | Quick-test/dev launch path |
| Floating overlay | Done | Done |
| Tray/menu | Done | Native menu + overlay context menu |
| Caller intent picker/hotkeys | Done | Native |
| Text prompt/query | Done | Native Swift + `brain.query` |
| Streaming reply surface | Done | Native bubble + chat stream |
| Selected text and ambient context | Done | Native snapshot + buffered context |
| Screenshot context | Done | Native capture + snip intent flow |
| Voice query | Done | Native capture + sidecar transcription |
| TTS playback | Done | Native playback lifecycle with amplitude-driven overlay pulse |
| Chat window | Done | Native |
| Settings window | Done | Native, with tray/overlay access to active `.env` config folder |
| Memory window | Done | Native |
| Plugin manager | Done | Native |
| Snip overlay | Done | Native |
| Agent task window | Done | Native |
| Agent task history | Done | Native |
| Packaging | Implemented, pending live release validation | Release-shaped dev bundle plus runtime probe, entitlements, signing/notary script, optional signed-app launch gate, and archived crash/log diagnostics; needs credentials and live signed-app validation |
| Permissions/TCC onboarding | Implemented, pending live TCC validation | Native permissions panel for Accessibility, Screen Recording, and Microphone status plus System Settings/request actions; needs live Mac validation |
| Launch at login | Implemented, pending live System Settings validation | Native tray toggle backed by `SMAppService.mainApp`; needs live Mac validation in System Settings |

## Current Migration Order

Use `docs/MACOS_MIGRATION_FINISH_PLAN.md` as the canonical finish plan. The
short operating order is:

1. Keep running `Test Wisp (Mac Native).command` after native changes; it must
   pass Python brain/config/validation tests and Swift package tests.
2. Open the generated log evidence:
   `build_logs/latest_macos_native_tests.txt` points to the newest quick-test
   folder, and `Open Wisp Mac Logs.command` opens the newest native log folder,
   `summary.log`, and `live-parity-checklist.md` from Finder.
3. Validate live native flows on a real Mac using
   `docs/MACOS_LIVE_PARITY_CHECKLIST.md`: caller intents, right-click overlay
   menu, chat contrast, snip query, voice query, TTS playback, memory/plugin
   actions, settings keys/auth/reset, launch-at-login toggle, permissions, and
   agent task/history flows.
4. Close any remaining live-behavior gaps found by the Mac validation loop.
5. Run `scripts/macos_package_release.sh` with Developer ID/notary credentials
   and validate the signed/notarized `.app`; package logs write
   `build_logs/latest_macos_package.txt` and a copied live parity checklist.

## Stability Gates

Native macOS code should live in Swift/AppKit/AVFoundation helpers or isolated
Python sidecar calls. Python packages that enter macOS frameworks or native ML /
vector runtimes from worker threads must stay out of the hot UI process and
remain explicit opt-ins until validated.
