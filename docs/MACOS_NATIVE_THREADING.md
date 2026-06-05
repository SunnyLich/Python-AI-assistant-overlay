# macOS Native Threading

Wisp uses worker threads for hotkeys, capture, transcription, LLM streaming,
TTS streaming, model probes, OAuth flows, and memory maintenance. On macOS,
some native frameworks are not safe to touch from arbitrary workers.

Use these two rules for new code:

1. AppKit, Quartz/CoreGraphics, CoreAudio/PortAudio, and screen capture must run
   through `core.system.main_thread.run_on_main()`.
2. SDK/client construction and keychain operations that can enter OpenSSL or the
   Security framework must run through `core.system.native_locks.native_init_lock()`
   or its narrow aliases, `ssl_init_lock()` and `keychain_lock()`.

The same native-init lock is shared by LLM clients, TTS clients, model listing,
settings probes, memory summaries, online search, filler audio baking, and OAuth
keychain storage. This prevents two cold worker threads from initializing
OpenSSL/Security at the same time after a fresh install or dependency rebuild.

Known protected boundaries:

- `core.audio` and `core.stt`: sounddevice stream open/start/stop/close.
  In-process macOS playback, filler prewarm, TTS prewarm, and STT prewarm are
  disabled in safe mode; set `WISP_MACOS_ENABLE_AUDIO=1` only when validating
  CoreAudio stability, or `WISP_MACOS_ENABLE_STT_PREWARM=1` for STT-only warmup.
- `core.capture` and `core.context_fetcher`: screen capture uses macOS'
  out-of-process `screencapture` helper instead of in-process `mss`.
- `core.platform_utils`: simple copy/paste key combos use out-of-process
  `osascript`; window enumeration and app activation still hop to the main
  thread while they remain PyObjC-backed.
- `core.capture`, `core.context_fetcher`, and paste-back in `main`: macOS
  clipboard reads/writes use `pbpaste`/`pbcopy` instead of pyperclip.
- `core.hotkeys` and `ui.intent_overlay`: no pynput global event tap on macOS.
- `core.llm_clients.client`, `core.tts`, `core.filler_bake`,
  `core.memory_store.store`, and `core.context_fetcher`: SDK client creation.
- `core.secret_store` and `core.auth.*`: keyring/keychain access.
- `core.context_fetcher.start_fs_watcher()`: disabled in macOS safe mode; when
  explicitly enabled it uses watchdog polling instead of the native FSEvents
  observer to avoid `_watchdog_fsevents` callback threads in the Qt/PyObjC
  process.
- `core.llm_clients.client`: OpenAI-compatible query, vision, rewrite, chat, and
  route-test probes run non-streaming in macOS safe mode after a reproducible
  segfault during `stream=True`; set `WISP_MACOS_OPENAI_COMPAT_STREAMING=1` only
  while validating a fix. Live OpenAI-compatible context tools are separately
  gated behind `WISP_MACOS_ENABLE_OPENAI_TOOLS=1`.
- `core.memory_store.store`: ChromaDB-backed semantic memory and background
  memory LLM jobs are explicit opt-ins on macOS, even when
  `WISP_MACOS_SAFE_MODE=0`. The memory UI and explicit facts still work through
  the plain JSON fallback; set `WISP_MACOS_ENABLE_CHROMADB=1` or
  `WISP_MACOS_ENABLE_MEMORY_BACKGROUND_LLM=1` only while validating those
  native/background paths.
- `core.system.sdk_clients`: OpenAI/Anthropic/httpx clients disable environment
  proxy discovery in macOS safe mode, and startup installs a process-wide urllib
  proxy guard for SDKs that build their own HTTP clients. This avoids Python's
  `urllib.request.getproxies_macosx_sysconf` path, which enters macOS
  SystemConfiguration from whichever worker thread is constructing the SDK
  client. Set `WISP_MACOS_TRUST_ENV_PROXIES=1` only while validating proxy use.

When adding a new provider SDK or native macOS API, wrap the smallest possible
construction/access block. Do not hold the lock for a full streaming response
unless the native library requires it.

## Out-of-process native worker (`core.macos_helper`)

Beyond routing native work to the main thread, the heaviest / most crash-prone
native subsystems can be moved out of the GUI process entirely, into a
supervised worker subprocess (`python -m core.macos_helper.host`). A segfault
there kills the worker, not Qt; and the worker's CoreAudio/run-loop machinery
runs on *its own* main thread, never contending with Qt's Cocoa run loop.

Gated behind `WISP_MACOS_HELPER=1` (macOS only) via `core.macos_helper.is_enabled()`;
off by default, so the in-process paths remain the shipping behavior until the
worker is proven on the Mac.

Pieces:
- `protocol.py` — newline-delimited JSON framing (request / response / event).
- `host.py` — worker entry point; redirects fd 1 → stderr so library prints
  can't corrupt the protocol channel, then serves requests in order.
- `handlers.py` — methods that run *inside* the worker. Native deps
  (sounddevice, faster-whisper) are imported lazily so the worker boots and can
  answer `ping` on any OS (this is what lets the IPC harness be tested off-mac).
- `client.py` — parent-side supervisor: lazy spawn, reader thread, request/
  response correlation, event dispatch, restart-on-death, fail-fast on exit.

**Migrated so far:** STT (faster-whisper/torch + mic). `core.stt` delegates to
`core.macos_helper.stt_client` when enabled; only the final transcript crosses
back, so JSON framing suffices and `core.stt` no longer imports `sounddevice`.

**Design note for the audio stage (TTS + playback):** these belong in the worker
*together*, not separately. If synthesis moved out but playback stayed in the
GUI, raw PCM would have to stream across IPC just to be played — worst of both
worlds. Instead the whole "text chunks → synthesized PCM → speaker" pipeline
runs in the worker: text chunks go worker-ward (small), PCM is played *in* the
worker and never crosses IPC, and only lightweight events (audio_start,
amplitude, word_timestamps, done) stream back for bubble/lip-sync. That needs a
bidirectional streaming session (not plain request/response) and must be
verified on the Mac on top of a proven STT foundation before it is built.

## Window parenting (separate from the threading rules above)

The floating icon overlay (`ui/overlay.py`) is a `Qt.WindowType.Tool` window —
an NSPanel on macOS. Do **not** parent a normal top-level window (settings,
plugin manager, chat, viewers) to it: attaching a regular child NSWindow to an
NSPanel segfaults Cocoa on `show()`. Open these windows with `parent=None` and
let them grab focus via `raise_()` + `activateWindow()`. `open_settings()` and
`open_plugin_manager()` keep the parent only on Linux; everywhere else they pass
`None`. This is a Cocoa window-graph hazard, not a worker-thread hazard.
