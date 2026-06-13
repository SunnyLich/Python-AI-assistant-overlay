# Wisp Plugin System Redesign — Plan

Status: **planning** (living doc — update as decisions land)

## 0. Where we are today

Three separate extension mechanisms exist:

| System | Location | Runs where | Contract |
|---|---|---|---|
| Mods | `plugins/<name>/__init__.py` | **in-process** in the brain worker | 7 optional hook functions, full Python access |
| Model tools | `model_tools/<name>/` | subprocess per call | `tool.toml` + `tool.py`, JSON stdin→stdout |
| Installed tools | `tools/installed/<name>/` | subprocess per call | same as model tools |

Known problems the redesign must fix:

1. **No permission model.** Mods get unrestricted in-process Python; the only
   mitigation is a README warning.
2. **Frozen-build breakage.** `core/tool_registry.py::_run_script_tool` spawns
   `[sys.executable, tool.py]`; in a PyInstaller build `sys.executable` is
   `Wisp.exe`, so script tools cannot run from the exe. In-process mods can only
   import packages that happen to be bundled into the exe — no way to add deps.
3. **No dependency story.** A plugin needing `requests` or `numpy` simply
   crashes on import unless the dev env happens to have it.
4. **`.env` pollution.** Plugin settings/enabled flags are slug-mangled env keys
   in the main `.env` (collision-prone: `my-mod` and `my_mod` share a slug).
5. **Three systems to document and maintain** that are really one package
   format with different contribution types (and two of the three are
   literally the same contract in different folders).
6. **Callables in the contract** (`executor`, tray `callback`) block any
   out-of-process or sandboxed future because they can't cross IPC.

What's already good and should be preserved: discovery-by-folder simplicity,
hooks-are-optional ergonomics, crash-isolation of hook exceptions, live
enable/disable with live tool (un)registration, the `brain.plugins.*` IPC
handlers + supervisor flows + Plugin Manager dialog plumbing.

## 1. Architecture decision: where plugins run

**Recommendation: plugins run out-of-process in "plugin host" processes,
speaking the existing JSON IPC.** One shared host for dependency-free plugins;
one host per plugin that declares its own dependencies (see §4).

Why:
- The app is *already* a supervisor + worker-process architecture with JSON
  IPC (`macos_py/supervisor/ipc.py`); a plugin host is just another worker
  kind. The `macos_helper` subprocess precedent shows the pattern works.
- Permissions become **enforceable at the IPC boundary** instead of honor-system:
  the supervisor only services requests the manifest declared and the user
  approved. In-process "permissions" would be decorative.
- A hung or crashed plugin can't take down the brain. Hook timeouts become
  real (kill the call, not the app).
- Per-plugin virtualenvs (the dependency answer) require a separate process
  anyway — you can't import from a different env into a frozen brain.

Honest threat model (state this in user-facing docs): permissions are a
**consent and least-privilege UX, not a security sandbox**. A malicious plugin
is still arbitrary code on the user's machine (same posture as VS Code or
Obsidian). We enforce what Wisp *hands to* the plugin; we don't pretend to
confine what Python itself can do. OS-level sandboxing is explicitly out of
scope for v1.

## 2. Manifest

Every plugin gets a `plugin.toml` (replaces "any folder with `__init__.py`"):

```toml
[plugin]
id = "weather-context"          # stable id, [a-z0-9-], used for storage keys
name = "Weather Context"
version = "1.2.0"
description = "Injects local weather into ambient context."
entry = "main.py"               # module exposing the hook functions
api_version = "1"               # wisp plugin API major version
min_wisp_version = "0.9"
platforms = ["windows", "macos"]  # optional; default all

[permissions]                   # everything default-deny; see §3
query = "modify"                # none | read | modify
response = "read"               # none | read | modify
context = ["selection", "clipboard"]
tools = true
network = true
llm = false
memory = "none"
ui = ["tray", "notifications", "settings"]
filesystem = "data-dir"         # data-dir | declared paths list

[dependencies]                  # optional; triggers per-plugin env (see §4)
python = ">=3.11"
packages = ["requests>=2.31", "beautifulsoup4"]

[settings]                      # replaces get_settings() for static cases
# ... same descriptor shape as today, declarative

[[tools]]                       # declarative script tool (tool-only plugins);
name = "lookup"                 # code plugins may instead register tools
script = "lookup.py"            # dynamically via get_tools()
# description, input_schema, timeout_seconds, max_output_chars — as tool.toml today
```

A tool-only plugin is just `[plugin]` + `[permissions]` + `[[tools]]` — barely
bigger than today's `tool.toml`, so the "drop a script in a folder" authoring
path is preserved (see §8).

Rules:
- Missing `[permissions]` entries mean **denied**. The plugin host raises a
  clear `PermissionDenied` if the plugin calls an API it didn't declare.
- `id` is the storage key (per-plugin data dir, settings file) — no more slug
  collisions.
- `api_version` lets us evolve the SDK with a deprecation window.

## 3. Permissions (question 1)

Permission = an IPC capability the supervisor grants the host for that plugin.
Enforced server-side (supervisor/brain), not in the plugin process.

| Permission | Grants | Risk tier shown in UI |
|---|---|---|
| `query: read` | receive prompt/context in `before_query` | sees your prompts |
| `query: modify` | return modified prompt/context | can change what the model is asked |
| `response: read/modify` | `after_response` text; modify before display/TTS | sees/changes replies |
| `context: [selection, clipboard, screenshot, ambient]` | each context source individually | sees screen/clipboard |
| `tools` | register model-callable tools | model can invoke its code |
| `llm` | make its own LLM calls through Wisp's routing & keys | spends your tokens |
| `memory: read/write` | query / append to the memory store | reads/writes your memory |
| `ui: [tray, notifications, settings, intents]` | declarative UI contributions | low |
| `hotkeys` | register global hotkeys via the native worker | medium |
| `tts` | speak text through the TTS pipeline | low |
| `events: [...]` | subscribe to app events (see §5) | varies by event |
| `network` | *declared, not enforceable* — informational badge | talks to the internet |
| `filesystem` | beyond its own data dir — declared paths, informational | reads/writes files |
| `config` | read **filtered** config (never secrets/API keys) | low |

UX:
- Approval happens **at enable time**: the Plugin Manager shows the declared
  permission list with risk tiers; enabling = consenting. (Per-call prompts are
  out of scope; too noisy for a background assistant.)
- If an update adds permissions, the plugin is disabled until re-approved.
- Secrets (API keys, tokens) are **never** exposed; `llm` permission exists so
  plugins can use models without ever seeing keys.

## 4. Dependencies & packaging (question 3)

Two tiers, decided by whether the manifest has `[dependencies]`:

**Tier 1 — dependency-free plugins.** Stdlib + the `wisp_sdk` only. Run in the
shared plugin host process (which is launched from Wisp's own runtime, so this
works identically in dev and frozen builds). Zero install cost, instant load.
Most "shape my prompt / add a tray action / small tool" plugins live here.

**Tier 2 — plugins with packages.** Wisp provisions a **per-plugin virtualenv**
and runs that plugin's host process from it:

- Bundle a standalone Python runtime with the app to make this work from the
  exe. **Recommendation: ship the `uv` binary** (~15 MB, no runtime deps):
  `uv venv` + `uv pip install` + `uv python install` handles fetching a managed
  CPython, creating the env, and installing packages — fast, with a local
  cache. Fallback option: ship python-build-standalone ourselves and call its
  pip. (Decision point — see §9.)
- Install flow: user enables plugin → Wisp shows the exact package list →
  user confirms → `uv` resolves into
  `<user-data>/plugin-envs/<id>/` → host process starts from that env's
  python. Errors (offline, resolution conflict) surface in the Plugin Manager
  card, never a silent failure.
- The `wisp_sdk` (pure-Python, the hook decorators + IPC client) is installed
  into each env from a wheel bundled with the app.
- Envs are rebuilt when the manifest's `packages` change (hash the list);
  "Repair environment" button in the Plugin Manager for manual rebuilds.
- Dev mode: if the plugin folder has no `[dependencies]` and you're running
  from source, nothing changes from today's ergonomics.

This also **fixes the frozen script-tool bug**: script tools become Tier 1/2
plugins executed by a real Python, never `sys.executable`.

Disk/size note: bundling uv adds ~15 MB to the installer; each Tier 2 env costs
whatever its packages cost. Acceptable; called out so it's a conscious choice.

## 5. Inputs — what plugins receive from Wisp (question 2)

All delivered as IPC messages to the plugin host; the SDK turns them into
plain function calls so plugin code looks like today's hooks.

**Lifecycle:** `on_startup(ctx)`, `on_shutdown()`, plus new `on_enable()` /
`on_disable()` so enable/disable doesn't need a restart and plugins can
clean up.

**Query pipeline (needs `query`/`response` permission):**
- `before_query(q)` where `q` is a dict, not two bare strings:
  `{prompt, context, intent, caller, attachments: [...], session_id}` —
  extensible without breaking signatures. Return the (modified) dict or `None`
  for "no change".
- `after_response(r)`: `{text, provider, model, duration_ms, token_usage,
  session_id}`.

**Events (subscribe via manifest `events: [...]`):** `intent.selected`,
`snip.captured` (image bytes by reference, not inline), `stt.transcript`,
`tts.started/finished`, `hotkey.fired` (own hotkeys only), `settings.changed`
(own settings only), `memory.written`, `app.idle`.

**Context object (`ctx`)** — replaces raw `config` module + `signals`:
- `ctx.config` — read-only, filtered (no secrets)
- `ctx.settings` — typed accessors for the plugin's own settings
- `ctx.data_dir` — per-plugin writable directory under user data
- `ctx.log` — logger that lands in a per-plugin log surfaced in the
  Plugin Manager (diagnose in-app, not via terminal — Mac testing rule)
- `ctx.wisp` — the capability API (only granted methods exist; see §6)

## 6. Outputs — what plugins can do to Wisp (question 4)

Via `ctx.wisp.*`, each gated by its permission:

- **Modify the pipeline**: return values from `before_query` /
  `after_response(modify)` — with a deterministic plugin ordering (manifest
  `priority` int, ties broken by id) and a per-hook time budget (default 2 s;
  on timeout the hook is skipped and the pipeline proceeds unmodified, error
  shown on the plugin card).
- **Register model tools**: same as today, but a hook-registered executor runs
  in the plugin host (the brain sends a `tool.execute` IPC request, with the
  existing timeout/output-cap semantics) — this is what makes today's
  `executor` callable IPC-safe. Declarative `[[tools]]` script tools keep
  spawn-per-call execution instead (see §8); no resident process needed.
- **Contribute intents**: new — add entries to the intent picker that run a
  plugin-defined prompt template or call back into the plugin.
- **Declarative UI**: tray actions (label + message id, no callables),
  notifications/toasts, settings page (declarative descriptors, rendered by
  the existing settings panel). **No direct Qt access ever** — plugins are
  headless; all UI is rendered by the UI worker from descriptors. (This also
  sidesteps the macOS main-thread/Qt-parenting crash classes.)
- **Speak** via TTS; **trigger a query** programmatically (`ctx.wisp.ask(...)`,
  rate-limited); **read/write memory**; **register hotkeys**; **make LLM
  calls** through Wisp's routing (`llm` permission, with a per-plugin
  spend/rate cap in settings).
- **Background work**: a plugin may run its own threads/timers inside its host
  process freely (it's its own process); scheduled callbacks
  (`ctx.wisp.schedule(...)`) for convenience.

Explicit non-goals: replacing the core pipeline/provider routing, reading
other plugins' data or settings, reading secrets, synchronous UI (dialog with
return value) in v1.

## 7. Storage & settings migration

- Move per-plugin enabled flags + settings out of `.env` into
  `<user-data>/plugins.json` (or one JSON per plugin id). Typed values, no
  slug mangling, no `.env` churn from the brain process.
- Keep the "read live" property: plugin hosts get `settings.changed` pushes.
- One-time migration: on first run, lift `PLUGIN_*` keys from `.env` into the
  new store and comment them out.

## 8. Consolidation: one package format, multiple contribution types

The merge is **plumbing, not taxonomy**. "App mod" and "model tool" stay
distinct concepts with distinct UI; what unifies is the machinery underneath —
discovery, manifest, trust/permissions, settings, dependency envs,
frozen-build handling.

- A **plugin is the unit of distribution and trust** (folder + `plugin.toml`).
  What it *contributes* is declared per type: lifecycle/query hooks, model
  tools, tray actions, intents, settings. A package may contribute one kind
  or several. The code already half-works this way: mod tools and script
  tools land in the same `ToolSpec` registry; only the folders still pretend
  they're different systems.
- **UI keeps the user's mental model.** The Plugin Manager shows separate
  categories — "App mods" (has hooks/events) and "Model tools" (tool-only) —
  even though both are the same package format underneath.
- **The lightweight authoring path survives.** A tool-only plugin is a folder
  with a script plus a manifest barely bigger than today's `tool.toml`
  (declarative `[[tools]]` entry — see §2). No hooks → no resident host:
  simple script tools keep spawn-per-call execution (under a real Python, not
  `sys.executable` — see §4), and only gain a host process if they declare
  hooks or dependencies.
- **Why not keep tools a separate system:** every upcoming feature would be
  built twice — dependency venvs, enable/disable + settings UX, permission
  declarations (a tool that hits the network needs the same consent), the
  frozen-exe fix — and a tool that grows a setting or a startup hook would
  need a rewrite as a mod instead of one manifest line.
- `model_tools/` vs `tools/installed/` is pure duplication (identical
  contract, two folders) and merges unconditionally. Both keep working
  through a thin legacy shim during the transition; a bare `tool.toml` is
  mechanically upgradable to a manifest.
- **Legacy mods**: clean break on the contract (dict-based hooks, manifest
  required) — only `healthcheck` exists in-repo, so cost is low. Ship a
  migration guide + port `healthcheck` as the reference plugin. A
  manifest-less folder shows in the Plugin Manager as "legacy — needs
  manifest" rather than silently loading.
- Plugin Manager dialog grows: permission display + approval, per-plugin log
  viewer, env status/repair, install-from-folder/zip.

## 9. Open decision points

1. **uv vs self-managed python-build-standalone** for Tier 2 envs
   (recommend uv).
2. **Shared host for all Tier 1 plugins vs process-per-plugin always.**
   Shared host saves memory; process-per-plugin is simpler isolation.
   Recommend: shared Tier 1 host, per-plugin for Tier 2 (forced by venvs).
3. **Approve-at-enable vs granular first-use prompts** (recommend at-enable).
4. **Distribution format** for sharing: bare folder vs zip (`.wisp` file) with
   install button (recommend zip in Phase 4; folder drop always works).
5. Whether `network`/`filesystem` stay informational or we later add an
   opt-in audited mode.

## 10. Phasing

- **Phase 1 — contract + manifest (no process move yet).** Define `plugin.toml`,
  `wisp_sdk` hook surface (dict-based hooks, no callables in contracts),
  permission gating *inside* the brain (decorative but API-final), new
  settings store + migration, port `healthcheck`. Tests: manifest parsing,
  permission denial, settings migration.
- **Phase 2 — plugin host process.** New worker kind in the supervisor; move
  plugin loading out of the brain; IPC-dispatch hooks/tools/tray; hook
  timeouts; per-plugin logs in the Plugin Manager. Permissions become real.
- **Phase 3 — dependency runtime.** Bundle uv, per-plugin env provisioning,
  consent UI, env repair, frozen-build verification on Windows exe + Mac.
- **Phase 4 — surface growth + distribution.** Events catalog, intents,
  notifications, hotkeys, `ctx.wisp.ask`/`llm` with caps, zip install.

Each phase leaves the app shippable; Phases 1–2 are pure-refactor visible only
to plugin authors.
