<div align="center">

<img src="assets/doll/idle.png" width="100" alt="Wisp" />

# Wisp

**Your AI — one keystroke from anywhere.**

Press `Ctrl+Q`. Wisp reads what's on your screen, thinks out loud, and answers in under two seconds — spoken aloud, word by word, right where you're working. No switching apps. No copy-pasting. No waiting.

[![Platform](https://img.shields.io/badge/platform-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-555?style=flat-square)](#install)
[![Python](https://img.shields.io/badge/python-3.12.13-3572A5?style=flat-square)](#install)
[![License](https://img.shields.io/badge/license-MIT-9F7AEA?style=flat-square)](LICENSE)

</div>

---

## Quick start

Download the repo and **double-click one file**:

- **macOS** — `Start Wisp.command`
- **Linux** — `Start Wisp.sh`
- **Windows** — `Start Wisp.bat`

The first time, it installs everything Wisp needs and then starts the app. On
macOS the launcher uses `requirements-macos.lock`; Windows and Linux use
`requirements.txt`. Every time after, it just launches. That's the whole setup.

> Requires **Python 3.12.13** (pinned in `.python-version`). The launcher finds it
> automatically — install via [pyenv](https://github.com/pyenv/pyenv)
> (`pyenv install 3.12.13`) on macOS, or from [python.org](https://www.python.org/downloads/release/python-31213/) on Windows.

---

## What it does

Wisp lives as a small animated icon in the corner of your screen — always on top, never in your way. Hit the hotkey and a slick intent picker drops in. Pick an action or type your own, and Wisp immediately:

1. **Grabs context** — highlighted text, open documents, clipboard, active browser tab, or a screenshot you draw
2. **Fires the query** — to your chosen LLM with full context already attached
3. **Answers in ~1.5s** — streaming text to a speech bubble *and* speaking it aloud, word by word, in sync

The icon plays a filler sound (`hm…`, `let me think…`) the instant you press the hotkey, so there's never a silent gap. By the time that finishes, the real answer is already coming in.

Click the icon at any time to open a full chat window for deeper conversations with memory of everything you've discussed.

---

## Features

- **Instant hotkey** — `Ctrl+Q` drops a WASD intent picker; one more keypress fires the query. Zero mouse required.
- **Speaks the answer** — Cartesia or ElevenLabs TTS streams the reply word-by-word, synced to the bubble text, at ~75ms voice latency
- **Voice input** — hold `F9` to talk; release to transcribe with local Whisper and fire the query
- **Context without lifting a finger** — reads your highlighted text, open Word/Excel/PDF/PowerPoint files, and live browser pages automatically
- **See it, ask it** — `Ctrl+Alt+Q` draws a snip region; a vision model answers questions about whatever you captured
- **Rewrite & paste** — `Ctrl+Shift+Q` rewrites selected text (fix grammar, simplify, change tone) and pastes the result back in place in one motion
- **Remembers you** — a local vector database stores facts across sessions; the most relevant ones surface automatically on every query
- **Bring your own model** — Groq, Anthropic, OpenAI, Google, DeepSeek, OpenRouter, Mistral, Ollama, GitHub Copilot, and more
- **Addons** — extend Wisp with query hooks, tray actions, settings, and model-callable tools; each addon runs in its own process
- **Feels instant** — filler audio plays in milliseconds to mask the LLM round-trip; the real answer usually arrives before the filler finishes
- **Stays out of the way** — icon auto-hides when idle, pops up on hotkey, disappears after the answer fades out

---

## Quick look

> **Scene 1:** You're staring at a Python traceback. You highlight the error, press `Ctrl+Q`, hit `D` ("How do I fix this?"):

```
[filler audio: "hm…"]
Wisp: "That's a circular import — move the import inside the function body
       or restructure the module to break the cycle."
       ↳ spoken aloud + bubble fades in word-by-word
```
Total time from keypress to first spoken word: **~1.5 seconds.**

> **Scene 2:** You highlight a clunky sentence and press `Ctrl+Shift+Q`:

```
Before: "The thing does the stuff when you click it"
After:  "Click the button to trigger the action."
        ↳ rewritten and pasted back automatically
```

> **Scene 3:** You see a confusing UI in your browser. Press `Ctrl+Alt+Q`, draw a box around it:

```
Wisp: "That's a CAPTCHA verification step — click the checkbox
       and complete the image puzzle to continue."
```

---

## Install

**Requirements:** Python 3.12.13, Windows 10/11, macOS, or Linux (X11)

```bash
git clone https://github.com/SunnyLich/Python-AI-assistant-overlay.git
cd Python-AI-assistant-overlay
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in at least one LLM API key.

```bash
python -m macos_py.supervisor.app
```

Or use the platform launcher, which provisions dependencies and starts the same
pure-Python worker supervisor:

```bash
bash "Start Wisp.command"   # macOS
bash "Start Wisp.sh"        # Linux
```

Run the macOS Python test gate with:

```bash
bash scripts/run_macos_tests.command
```

### Contributor setup

If you want to develop Wisp, install the runtime dependencies plus developer
tools (`pytest`, Ruff, and MyPy) with the setup script for your platform:

```powershell
.\scripts\setup_dev.ps1       # Windows PowerShell
```

```bash
bash scripts/setup_dev.sh     # macOS / Linux
```

Then run checks from the repo root:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check core\context_hotkey.py core\llm_clients\messages.py macos_py\supervisor\tool_modes.py ui\agent\combo_helpers.py ui\settings_panel\helpers.py tests\test_context_hotkey_snapshot.py
.\.venv\Scripts\python.exe -m mypy core\settings_model.py core\llm_clients\logging_utils.py macos_py\supervisor\tool_modes.py ui\agent\combo_helpers.py --follow-imports=skip
```

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check core/context_hotkey.py core/llm_clients/messages.py macos_py/supervisor/tool_modes.py ui/agent/combo_helpers.py ui/settings_panel/helpers.py tests/test_context_hotkey_snapshot.py
.venv/bin/python -m mypy core/settings_model.py core/llm_clients/logging_utils.py macos_py/supervisor/tool_modes.py ui/agent/combo_helpers.py --follow-imports=skip
```

---

## Configuration

All settings live in `.env`. The most important ones:

### LLM

```env
LLM_PROVIDER=groq                  # groq | anthropic | openai | google | deepseek | ollama | ...
LLM_MODEL=llama3-8b-8192
LLM_FALLBACKS=anthropic:claude-sonnet-4-6   # optional fallback chain

VISION_LLM_PROVIDER=anthropic      # model used for screenshots / screen snippets
VISION_LLM_MODEL=claude-opus-4-5

# The chat window shares LLM_PROVIDER / LLM_MODEL above (one combined model).
TOOL_LLM_MODEL=                    # optional: override the model only when tools are active
```

### TTS

```env
TTS_PROVIDER=cartesia              # cartesia | elevenlabs | none
CARTESIA_API_KEY=...
TTS_PLAYBACK_RATE=1.0
```

### Hotkeys

```env
CALLER_1_HOTKEY=ctrl+q             # intent picker (general queries)
CALLER_2_HOTKEY=ctrl+shift+q       # intent picker (rewrite & paste)
HOTKEY_ADD_CONTEXT=alt+q           # append selection to context buffer
HOTKEY_CLEAR_CONTEXT=alt+w         # clear context buffer
HOTKEY_SNIP=ctrl+alt+q             # screen region selector
HOTKEY_VOICE=f9                    # push-to-talk
```

### UI

```env
ICON_SIZE=80
ICON_AUTO_HIDE=false               # hide icon when idle; show on hotkey
BUBBLE_WIDTH=340
BUBBLE_LINES=3
```

### Memory

```env
MEMORY_AUTO_CONSOLIDATE=false      # set true to consolidate STM → LTM automatically
MEMORY_TOP_K=3                     # facts injected per query
```

### Callers (hotkey profiles)

Each `CALLER_N_*` block defines what one hotkey invocation does:

```env
CALLER_COUNT=2

CALLER_1_HOTKEY=ctrl+q
CALLER_1_LABEL=General
CALLER_1_CONTEXT_AMBIENT=true      # read active window / clipboard
CALLER_1_CONTEXT_DOCUMENTS=true    # read open Word / Excel / PDF / etc.
CALLER_1_CONTEXT_TOOLS=true        # allow web_search / get_context tool calls
CALLER_1_CONTEXT_SCREENSHOT=off    # off | model (let model screenshot on demand) | auto (always)
CALLER_1_PASTE_BACK=false

CALLER_2_HOTKEY=ctrl+shift+q
CALLER_2_LABEL=Rewrite
CALLER_2_CONTEXT_DOCUMENTS=false
CALLER_2_PASTE_BACK=true           # auto-paste result back into the active app
```

---

## Hotkey reference

| Hotkey | Action |
|--------|--------|
| `Ctrl+Q` | Open intent picker (general) |
| `Ctrl+Shift+Q` | Open intent picker (rewrite & paste) |
| `Ctrl+Alt+Q` | Screen region selector → intent picker with screenshot |
| `Alt+Q` | Append selected text to context buffer |
| `Alt+W` | Clear context buffer |
| `F9` (hold) | Record voice; release to transcribe and query |
| `W` / `A` / `D` | Select intent preset in picker |
| `S` | Custom prompt mode in picker |
| `Esc` | Cancel picker |

---

## Supported LLM providers

| Provider | Notes |
|----------|-------|
| Groq | Default; fastest TTFT for quick queries |
| Anthropic | Claude models; default vision provider |
| OpenAI | GPT-4o and variants |
| Google | Gemini via generative language API |
| DeepSeek / Mistral / XAI / Together / Cerebras | OpenAI-compatible endpoints |
| OpenRouter | Route to any model |
| Ollama | Local models; set `OLLAMA_BASE_URL` |
| GitHub Copilot | OAuth-based; uses Copilot subscription |

---

## Memory

Wisp remembers things so you don't have to repeat yourself. It uses a two-tier system:

- **Short-term (STM):** a rolling in-session log that auto-compresses as it grows, so long sessions stay fast
- **Long-term (LTM):** a local ChromaDB vector store of atomic facts extracted from your conversations

On every query, the top-k most relevant facts are pulled by semantic similarity and quietly injected into the prompt. Over time, Wisp builds a picture of your projects, preferences, and recurring problems — and uses it. Tray icon → **Memory Viewer** to browse, edit, or delete anything stored.

---

## Agent framework

`core/agent/` is an experimental background task runner for bigger jobs — think multi-step automations rather than quick lookups. Each task runs in a sandboxed workspace, logs every step auditably, and asks for approval before mutating files. Still early; not yet wired into the main UI.

---

## Addons

Addons live under `addons/<id>/` and declare an `addon.toml` manifest. They can
observe or modify query context, observe responses, contribute tray actions,
expose settings, and register model-callable tools.

Each enabled addon runs in its own Python host process. A crashing or stuck
addon is isolated from the brain worker and from other addons; Wisp talks to it
over a small JSON IPC protocol.

Start with [addons/README.md](addons/README.md) and the reference
`addons/healthcheck` addon.

---

## Platform status

| Platform | Status |
|----------|--------|
| Windows 11 | Full support |
| Windows 10 | Supported |
| Linux (X11) | Functional; no native tray integration |
| Linux (Wayland) | In progress |
| macOS | Shared Qt UI parity in progress; platform-specific backend paths live under `core/platform*` |

---

## Contributing

PRs and issues are welcome. Adding a new LLM provider is intentionally easy — each one is a small adapter in [core/llm_clients/routes.py](core/llm_clients/routes.py) that implements the same streaming interface. Add your adapter, register it in the route table, and it lights up everywhere (quick query, chat, vision, memory, fallback chains).

---

## License

MIT
