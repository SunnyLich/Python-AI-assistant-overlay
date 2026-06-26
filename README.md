<div align="center">

<img src="assets/doll/idle.png" width="112" alt="Wisp icon" />

# Wisp

**Tired of typing the same prompts and pasting the same context?**

Wisp gives you hotkey-driven AI that can read your selection, clipboard, app, browser, documents, or screen snip while you stay where you are. Press a hotkey, choose an intent, and stream the answer into a small overlay.

[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-333333?style=flat-square)](#platform-status)
[![Python](https://img.shields.io/badge/python-3.12-3572A5?style=flat-square)](#quick-start)
[![Local first](https://img.shields.io/badge/local--first-context%20and%20memory-4B8F8C?style=flat-square)](#privacy-and-control)
[![License](https://img.shields.io/badge/license-MIT-7C3AED?style=flat-square)](#license)

**Languages:** English | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [Français](README.fr.md) | [Español](README.es.md)

[Quick start](#quick-start) | [What it does](#what-wisp-does) | [Demos](#demos) | [Configuration](#configuration) | [Free APIs](#free-model-api-sources) | [Privacy](#privacy-and-control)

![Wisp Ctrl+Q demo](ReadMe%201st%20Demo.gif)

**Overlay query:** Press a hotkey, choose an intent, and get a streamed answer without leaving the app you are already using.
</div>

---

## What Wisp Does

Wisp is for the moments when opening a chat app would break your flow.

Highlight text, press `Ctrl+Q`, hit one intent key, and Wisp asks your configured model with only the context sources you enabled. Replies stream into a compact bubble next to the floating icon. If TTS is enabled, the answer is spoken as it arrives.

| Instead of... | Wisp lets you... |
| --- | --- |
| Copying text into a separate chat window | Ask from the app you are already using |
| Rewriting the same prompts again and again | Bind prompts to hotkeys and intent rows |
| Reading long responses every time | Hear the answer through streaming TTS |
| Explaining what is on screen manually | Capture selection, clipboard, documents, browser pages, and screen snippets |
| Trusting a remote assistant with storage | Keep memory and configuration on your machine |

## Highlights

- **Overlay first** - a floating icon, intent picker, and reply bubble stay on top without taking over your desktop.
- **Privacy by default** - Wisp has no hosted storage layer; data stays on your machine unless you send it to your chosen model, and privacy mode can warn or redact before sensitive context leaves.
- **Highly customizable** - every hotkey, intent key, prompt, context source, paste-back behavior, model route, voice setting, and bubble dimension can be changed.
- **Approachable GUI** - Settings, setup checks, privacy reports, memory tools, and model warnings explain what is happening without requiring you to read the code.
- **Context capture** - Wisp can read selected text, clipboard text, focused UI, open documents, browser content, recent files, and optional screenshots, so it does not have to rely on screen grabs alone.
- **Voice in and out** - local STT via faster-whisper, plus Cartesia, ElevenLabs, OpenAI, OpenAI-compatible, or disabled TTS.
- **Vision snips** - draw a region with `Ctrl+Alt+Q` and send the screenshot to a vision model.
- **Rewrite and paste** - use `Ctrl+Shift+Q` to rewrite selected text with captured context and paste the result back into the active field.
- **Bring your own provider** - Groq, Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Mistral, XAI, Together, Cerebras, custom OpenAI-compatible servers, GitHub Copilot, and more.
- **Local memory** - optional short-term and long-term memory are stored locally, with a viewer for editing or deleting facts.
- **Addons** - extend Wisp with hooks, tray actions, settings, model-callable tools, intents, and hotkeys.
- **Agent tasks** - a sandboxed task framework exists for longer jobs that need decomposition, review, and artifacts.

## Demos

![Wisp Ctrl+Alt+Q screen snip demo](ReadMe%202nd%20Demo.gif)

**Vision snip:** The snip flow is for cases where visual context matters. `Ctrl+Alt+Q` lets you draw a region, send just that crop to a vision model, and keep the answer in the overlay instead of switching apps.

![Wisp context-aware rewrite demo](ReadMe%203rd%20Demo.gif)

**Context-aware rewrite:** Wisp can gather useful app context without taking a screenshot, so the model knows what you are working on. Then `Ctrl+Shift+Q` rewrites only the selected text and pastes the replacement back where you were working.

![Wisp multi-agent task demo](ReadMe%204th%20Demo.gif)

**Sandboxed agent run:** The agent task flow is for longer workspace jobs. Wisp can split a task across coordinator, builder, and reviewer roles, inspect project files, make a focused change, run checks, and leave behind a final report and artifacts for the run.

## Workflow

```text
highlight text, choose context, or draw a snip
  -> press the caller hotkey
  -> Wisp captures only the selected or enabled context
  -> pick an intent or type a custom prompt
  -> send directly to your configured model provider
  -> stream model reply
  -> show bubble + optional TTS
  -> optionally store useful memory locally
```

Example flows:

| Moment | Action | Result |
| --- | --- | --- |
| You want an explanation of selected text | Highlight the text, press `Ctrl+Q`, then choose `W` (`What is this?`) or `A` (`Explain simply`) | Wisp explains the selection in the overlay |
| You want to rewrite a sentence | Highlight the sentence first, press `Ctrl+Shift+Q`, then choose `W`, `A`, or `D` for grammar, simplification, or tone | Wisp rewrites the selected text and can paste it back |
| You need to ask your own question | Press `Ctrl+Q`, press `S`, type the prompt, then press Enter | Wisp sends your custom prompt with whatever context is enabled for that caller |
| A UI element or image is confusing | Press `Ctrl+Alt+Q`, draw a box, then choose an intent or custom prompt | Wisp sends the snip to a vision model |
| You want to ask the model by voice | Hold `F9`, speak, then release | Wisp transcribes your voice and sends it as a model query |
| You want to dictate into another app | Hold `F8`, speak, then release | Wisp transcribes your speech directly into the focused text field |

## Quick Start

There are two supported ways to start Wisp.

### Option 1: Packaged App

Use this if you want the app without cloning the repo or managing Python dependencies.

1. Download the latest asset for your platform from [GitHub Releases](https://github.com/SunnyLich/Python-AI-assistant-overlay/releases).
2. Unpack the archive and start the packaged app.
3. Open Settings to add your model provider keys, voice settings, and preferred hotkeys.

| OS | Release artifact | Start with |
| --- | --- | --- |
| Windows | `Wisp-<tag>-windows-x64.zip` | `Wisp.exe` |
| macOS | `Wisp-<tag>-macos-<arch>.zip` | `Wisp.app` |
| Linux | `Wisp-<tag>-linux-x64.tar.gz` | `Wisp` |

### Option 2: Repo Launcher

Use this if you want to run from source, develop Wisp, or test the latest checkout.

Clone the repo:

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
```

Then start Wisp with the repo launcher for your platform:

| OS | Start with | Dependency source |
| --- | --- | --- |
| Windows | `Start Wisp.bat` | `requirements.txt` |
| macOS | `Start Wisp.command` | `requirements-macos.lock` |
| Linux | `Start Wisp.sh` | `requirements.txt` |

The first launch provisions the Python environment and installs dependencies. Later launches go straight into the app.

To build your own packaged copy, see [Building an EXE](docs/BUILDING_EXE.md) for local build commands and the tagged-release workflow.

Requirements:

- Python `3.12`, pinned in `.python-version`
- Windows 10/11, macOS 13+, or Linux with X11 for the full hotkey/screenshot path
- At least one configured LLM provider key or local compatible server

For full runtime logs, use the matching debug launcher:

```text
Start Wisp Debug.bat
Start Wisp Debug.command
Start Wisp Debug.sh
```

## Configuration

Use the Settings window for normal setup. It can store provider keys, choose model routes, configure voice, run a setup check, explain missing optional features, and show warnings for unsupported model capabilities.

For source builds and advanced setups, `.env.example` documents the available configuration keys. You usually do not need to edit those by hand.

## Free Model API Sources

Wisp is free, and you can keep your model costs at zero too. Several providers offer a genuinely free tier, free monthly credits, or no-cost rate-limited access. Wisp reaches most of them through its OpenAI-compatible client — a few have a dedicated `LLM_PROVIDER` value, and the rest work through the `custom` endpoint by pointing `CUSTOM_BASE_URL` at the provider's OpenAI-compatible URL. Add the key itself in **Settings → LLM**.

| Provider | What's free | Good for |
| --- | --- | --- |
| OpenRouter | `:free` models — ~20 req/min and 50/day with no credits, 1,000/day after a one-time $10 top-up; plus an `openrouter/free` router | Easiest "one API, many models" option |
| Google AI Studio | Gemini API free tier in supported regions, with rate limits | Multimodal and long-context work, including vision |
| Mistral | Free experimental tier on La Plateforme, rate-limited | European, GDPR-friendly models and function calling |
| NVIDIA | Free API access to many open models via the NVIDIA API Catalog | Trying many open-weight models on fast hosted endpoints |
| GroqCloud | Free tier with rate limits | Very fast inference for open models like Llama and Qwen |
| Cerebras Inference | Free API tier for Cerebras-hosted models | Extremely fast text inference and prototyping |
| GitHub Models | Rate-limited no-cost access for every GitHub account | Prototyping, experiments, GitHub-integrated workflows |
| Hugging Face Inference Providers | Free monthly credits (currently ~$0.10/month for free users) | Trying lots of open models through one ecosystem |
| Cloudflare Workers AI | Workers free plan with a free daily allocation | Apps already on Cloudflare; serverless AI endpoints |
| Vercel AI Gateway | Free tier with $5/month of gateway credit for eligible models | Next.js/Vercel projects; unified OpenAI-compatible access |
| SambaNova Cloud | $5 of free API credit, no credit card required | Fast hosted open-model inference |
| Puter.js | Front-end JS access to many models with no API key of your own | Browser apps and demos; not a Wisp backend provider |
| Local — Ollama / LM Studio / vLLM | Free whenever you run the model yourself | Privacy, no token billing, OpenAI-compatible local endpoints |

Free tiers are rate-limited and change often, so add at least one fallback route, and avoid sending sensitive context to providers that may train on your prompts (Wisp's redaction still applies). For the full how-to-connect guide and caveats, see the **Free API sources** page in the [Wisp documentation site](Wisp%20Website/Wisp%20Docs.html).

## Default Hotkeys

| Hotkey | Action |
| --- | --- |
| `Ctrl+Q` | Open the general intent picker |
| `Ctrl+Shift+Q` | Open the rewrite/paste intent picker |
| `Ctrl+Alt+Q` | Draw a screen snip for vision |
| `Alt+Q` | Add the current selection to the context buffer |
| `Alt+W` | Clear the context buffer |
| `F9` hold | Record voice, transcribe, and query |
| `F8` hold | Direct dictation into the focused text field |
| `W` / `A` / `D` | Trigger built-in intent rows |
| `S` | Custom prompt mode |
| `Esc` | Cancel the picker |

Every caller, hotkey, label, prompt, context source, paste-back setting, and UI dimension is configurable from Settings.

## Addons

Addons are the supported way to extend Wisp. Each addon lives in its own folder under `addons/` with an `addon.toml` manifest, and runs in its own isolated Python host process, so a crash, a slow hook, or a bad dependency in one addon cannot take down the brain worker or any other addon. Capabilities are opt-in: an addon only gets what its manifest declares, and missing permissions are denied. Addons that need third-party packages get a dedicated virtual environment that you approve before it runs.

An addon can hook into Wisp at several points:

- **Context** - read or rewrite the prompt and context before a query is sent.
- **Tools** - register model-callable tools the model can invoke mid-answer.
- **Responses** - observe completed responses to log, save, or forward them.
- **Intents and hotkeys** - add its own intent rows and global hotkeys with custom prompts.
- **UI** - contribute tray actions, settings fields, and notifications.
- **LLM actions** - run its own capped model calls from a hook or hotkey.

**What addons can do:** because an addon can inject context, expose tools, and react to responses, the surface is broad. A few examples, and the hook each one uses:

| You want to... | Hook | Manifest needs |
| --- | --- | --- |
| Pull your git diff, calendar, or an open ticket into the prompt automatically | Context (`before_query`) | `query = "modify"` |
| Give the model a tool to search an internal wiki, query a database, hit a weather or stock API, or toggle a smart-home device | Tools (`get_tools`) | `tools = true` (plus `[dependencies]` for any packages) |
| Redact or tag sensitive context on its way out for compliance | Context (`before_query`) | `query = "modify"` |
| Append every answer to a daily journal, or push it to Notion or Slack | Responses (`after_response`) | `response = "read"` |
| Add a one-key "rewrite this in our house style" intent backed by its own prompt | Intents and hotkeys | `[[intents]]` / `[[hotkeys]]`, `hotkeys = true` |

If you can write it in Python and it fits one of the hook points above, you can wire it into the same hotkey-driven overlay you already use.

The bundled `addons/healthcheck` addon is a working reference that exercises every hook. See the [Addon guide](addons/README.md) for the full manifest and hook contract, or the **Add-ons** page in the [Wisp documentation site](Wisp%20Website/Wisp%20Docs.html).

## Privacy And Control

Wisp is designed as a local desktop assistant. Storage stays on your machine, and requests go directly to the model provider or local server you configure.

- Local data stays local: settings, chats, memory, privacy reports, and configuration are stored on your machine.
- Model requests go straight from your machine to the provider or local server you configured.
- Your configured model provider receives only the prompt you send and the context sources selected or enabled for that caller.
- Wisp may inspect available context locally to show token estimates, availability, and privacy redaction counts before you send. Previewing a source does not send it to the model provider or save it as chat/memory.
- Context is controlled per hotkey profile: ambient app context, clipboard, documents, browser pages, GitHub context, memory, and screenshots can each be disabled, attached up front, or exposed as model-fetchable context where supported.
- Allowed tools are separate from context controls and cover the remaining model-callable capabilities, such as local file tools and add-on tools.
- Privacy mode keeps privacy-first setup checks and warning behavior enabled, including redaction status before sensitive context is sent.
- Optional voice, document reading, browser content, screenshots, GitHub Copilot, and addons stay inactive until configured.
- Cloud TTS, model providers, compatible servers, or GitHub Copilot are contacted only when you configure and use those features.
- Addons run in isolated Python host processes and must declare the capabilities they need.
- Setup checks avoid importing heavy provider, audio, or STT stacks unless the feature is enabled.

## Platform Status

| Platform | Status |
| --- | --- |
| Windows 11 | Full support |
| Windows 10 | Supported |
| macOS 13+ | Supported with native/audio work isolated in workers |
| Linux X11 | Functional |
| Linux Wayland | Limited; use X11 for the full hotkey/screenshot path |

## Feedback And Platform Help

Bug reports are welcome, especially for desktop behaviors that depend on OS permissions, window managers, audio devices, or display servers. If you hit a crash, missing permission, broken hotkey, capture issue, paste-back failure, or setup-check warning that looks wrong, please open an issue with your OS version, launcher, logs, and the action that triggered it.

Help testing and improving macOS support and Linux Wayland support is especially useful. These platforms have the most native integration edge cases, so real-world reports from different machines, desktop environments, and permission states make Wisp better for everyone.

<details>
<summary>Contributor docs</summary>

- [Developer README](docs/DEVELOPER_README.md) - setup, runtime entrypoints, checks, and debugging notes.
- [Code overview](docs/OVERVIEW.md) - subsystem ownership and runtime boundaries.
- [Addon guide](addons/README.md) - addon manifest, permissions, hooks, tools, hotkeys, and packaging.
- [Building an EXE](docs/BUILDING_EXE.md) - Windows packaging notes.

</details>

## License

MIT
