<div align="center">

<img src="assets/doll/idle.png" width="112" alt="Wisp icon" />

# Wisp

**A local-first desktop AI assistant that lives where you work.**

Press a hotkey, choose an intent, and Wisp captures the right context, streams the answer into a small overlay, and can read it aloud while you stay in the current app.

[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-333333?style=flat-square)](#platform-status)
[![Python](https://img.shields.io/badge/python-3.12.13-3572A5?style=flat-square)](#quick-start)
[![Local first](https://img.shields.io/badge/local--first-context%20and%20memory-4B8F8C?style=flat-square)](#privacy-and-control)
[![License](https://img.shields.io/badge/license-MIT-7C3AED?style=flat-square)](#license)

[Quick start](#quick-start) | [What it does](#what-wisp-does) | [Configuration](#configuration) | [Developer docs](#developer-docs)

</div>

---

## What Wisp Does

Wisp is for the moments when opening a chat app would break your flow.

Highlight text, press `Ctrl+Q`, hit one intent key, and Wisp asks your configured model with the current screen context already attached. Replies stream into a compact bubble next to the floating icon. If TTS is enabled, the answer is spoken as it arrives.

| Instead of... | Wisp lets you... |
| --- | --- |
| Copying text into a separate chat window | Ask from the app you are already using |
| Rewriting the same prompts again and again | Bind prompts to hotkeys and intent rows |
| Reading long responses every time | Hear the answer through streaming TTS |
| Explaining what is on screen manually | Capture selection, clipboard, documents, browser pages, and screen snippets |
| Trusting a remote assistant with storage | Keep memory and configuration on your machine |

## Highlights

- **Overlay first** - a floating icon, intent picker, and reply bubble stay on top without taking over your desktop.
- **Context capture** - Wisp can read selected text, clipboard text, focused UI, open documents, browser content, recent files, and optional screenshots.
- **Voice in and out** - local STT via faster-whisper, plus Cartesia, ElevenLabs, OpenAI, OpenAI-compatible, or disabled TTS.
- **Vision snips** - draw a region with `Ctrl+Alt+Q` and send the screenshot to a vision model.
- **Rewrite and paste** - use `Ctrl+Shift+Q` to rewrite selected text and paste the result back into the active field.
- **Bring your own provider** - Groq, Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Mistral, XAI, Together, Cerebras, custom OpenAI-compatible servers, GitHub Copilot, and more.
- **Local memory** - optional short-term and long-term memory are stored locally, with a viewer for editing or deleting facts.
- **Process isolation** - UI, native OS work, audio, and brain/model work run in separate worker processes under one Python supervisor.
- **Addons** - extend Wisp with hooks, tray actions, settings, model-callable tools, intents, and hotkeys.
- **Agent tasks** - a sandboxed task framework exists for longer jobs that need decomposition, review, and artifacts.

## How It Feels

```text
hotkey
  -> capture context
  -> pick an intent
  -> stream model reply
  -> show bubble + optional TTS
  -> store useful memory locally
```

Example flows:

| Moment | Action | Result |
| --- | --- | --- |
| A traceback is selected | `Ctrl+Q`, then the "fix this" intent | Wisp explains the likely cause without leaving the editor |
| A sentence needs polish | `Ctrl+Shift+Q` | Wisp rewrites it and pastes the result back |
| A UI element is confusing | `Ctrl+Alt+Q`, draw a box | Wisp sends the snip to a vision model |
| You do not want to type | Hold `F9` | Wisp records, transcribes, and sends the query |

## Quick Start

Clone the repo:

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
```

Then launch with the script for your platform:

| OS | Launcher | Dependency source |
| --- | --- | --- |
| Windows | `Start Wisp.bat` | `requirements.txt` |
| macOS | `Start Wisp.command` | `requirements-macos.lock` |
| Linux | `Start Wisp.sh` | `requirements.txt` |

The first launch provisions the Python environment and installs dependencies. Later launches go straight into the app.

Requirements:

- Python `3.12.13` exactly, pinned in `.python-version`
- Windows 10/11, macOS 13+, or Linux with X11 for the full hotkey/screenshot path
- At least one configured LLM provider key or local compatible server

For full runtime logs, use the matching debug launcher:

```text
Start Wisp Debug.bat
Start Wisp Debug.command
Start Wisp Debug.sh
```

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Set one model route first:

```env
LLM_PROVIDER=groq
LLM_MODEL=llama-3.1-8b-instant
LLM_FALLBACKS=anthropic:claude-haiku-4-5; openai:gpt-5.4-mini
```

Optional vision route:

```env
VISION_LLM_PROVIDER=anthropic
VISION_LLM_MODEL=claude-opus-4-8
```

Optional voice:

```env
STT_MODEL=base
TTS_PROVIDER=none

# Cloud / compatible TTS examples:
# TTS_PROVIDER=cartesia
# TTS_PROVIDER=elevenlabs
# TTS_PROVIDER=openai
# TTS_PROVIDER=openai_compatible
```

The Settings window can store provider keys, run a setup check, explain missing optional features, and show warnings for unsupported model capabilities.

## Default Hotkeys

| Hotkey | Action |
| --- | --- |
| `Ctrl+Q` | Open the general intent picker |
| `Ctrl+Shift+Q` | Open the rewrite/paste intent picker |
| `Ctrl+Alt+Q` | Draw a screen snip for vision |
| `Alt+Q` | Add the current selection to the context buffer |
| `Alt+W` | Clear the context buffer |
| `F9` hold | Record voice, transcribe, and query |
| `W` / `A` / `D` | Trigger built-in intent rows |
| `S` | Custom prompt mode |
| `Esc` | Cancel the picker |

Every caller, hotkey, label, prompt, context source, paste-back setting, and UI dimension is configurable from `.env` or Settings.

## Privacy And Control

Wisp is designed as a local desktop assistant, not a hosted service.

- There is no "Wisp cloud."
- Your configured model provider receives only the query and context you ask Wisp to send.
- Memory is stored locally on disk.
- Optional voice, document reading, browser content, screenshots, GitHub Copilot, and addons stay inactive until configured.
- Addons run in isolated Python host processes and must declare the capabilities they need.
- Setup checks avoid importing heavy provider, audio, or STT stacks unless the feature is enabled.

## Architecture

Wisp runs as one supervisor plus isolated workers:

| Layer | Owns |
| --- | --- |
| `runtime/supervisor/` | App lifecycle, hotkeys, context buffers, worker routing |
| `wisp-ui` worker | Qt icon, intent picker, bubble, settings, chat, memory UI |
| `wisp-native` worker | OS capture, global hotkeys, platform integration |
| `wisp-audio` worker | STT, TTS, filler audio, playback |
| `wisp-brain` worker | LLM routing, tools, memory, agent flows |
| `addons/<id>/` | Optional extensions, one host process per addon |

For the longer map, read [docs/OVERVIEW.md](docs/OVERVIEW.md) and [docs/COMMUNICATION_GRAPH.md](docs/COMMUNICATION_GRAPH.md).

## Development

Install runtime and developer tools with the platform setup script:

```powershell
.\scripts\setup_dev.ps1
.\.venv\Scripts\python.exe scripts\check_dev_environment.py
```

```bash
bash scripts/setup_dev.sh
.venv/bin/python scripts/check_dev_environment.py
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

```bash
.venv/bin/python -m pytest
```

Focused lint/type baseline:

```powershell
.\.venv\Scripts\python.exe -m ruff check core\context_hotkey.py core\llm_clients\messages.py runtime\supervisor\tool_modes.py ui\agent\combo_helpers.py ui\settings_panel\helpers.py tests\test_context_hotkey_snapshot.py
.\.venv\Scripts\python.exe -m mypy core\settings_model.py core\llm_clients\logging_utils.py runtime\supervisor\tool_modes.py ui\agent\combo_helpers.py --follow-imports=skip
```

```bash
.venv/bin/python -m ruff check core/context_hotkey.py core/llm_clients/messages.py runtime/supervisor/tool_modes.py ui/agent/combo_helpers.py ui/settings_panel/helpers.py tests/test_context_hotkey_snapshot.py
.venv/bin/python -m mypy core/settings_model.py core/llm_clients/logging_utils.py runtime/supervisor/tool_modes.py ui/agent/combo_helpers.py --follow-imports=skip
```

## Platform Status

| Platform | Status |
| --- | --- |
| Windows 11 | Full support |
| Windows 10 | Supported |
| macOS 13+ | Supported with native/audio work isolated in workers |
| Linux X11 | Functional |
| Linux Wayland | Limited; use X11 for the full hotkey/screenshot path |

## Developer Docs

- [Developer README](docs/DEVELOPER_README.md) - setup, runtime entrypoints, checks, and debugging notes.
- [Code overview](docs/OVERVIEW.md) - subsystem ownership and runtime boundaries.
- [Communication graph](docs/COMMUNICATION_GRAPH.md) - supervisor, worker, UI, memory, addon, and agent flow.
- [Addon guide](addons/README.md) - addon manifest, permissions, hooks, tools, hotkeys, and packaging.
- [Building an EXE](docs/BUILDING_EXE.md) - Windows packaging notes.

## License

MIT
