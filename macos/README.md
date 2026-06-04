# Wisp — native macOS shell + Python brain sidecar

This directory is the from-scratch macOS rewrite described in
[`../MACOS_NATIVE_PLAN.md`](../MACOS_NATIVE_PLAN.md) (repo root, gitignored).
**Option A**: a native Swift/AppKit app owns every OS-bound API on the main
thread; the existing OS-agnostic Python `core/` runs as a headless sidecar over a
newline-delimited JSON seam.

```
macos/
├── brain/                     # Python sidecar (runs the existing core/ brain)
│   ├── wisp_brain/            #   protocol.py · handlers.py · host.py
│   └── tests/test_brain_host.py
└── Sources/Wisp/             # Swift app (compiles on macOS only)
    ├── Bridge/               #   Protocol · BrainClient · BrainLocator
    ├── App/                  #   main · AppDelegate
    ├── Overlay/              #   OverlayPanel (NSPanel + SwiftUI)
    └── Tray/                 #   StatusItem (NSStatusItem)
```

## Status — Phase 1 (skeleton + brain handshake)

| Piece | State |
|---|---|
| Python sidecar (`wisp_brain`) + JSON seam | **Done & tested** (`test_brain_host.py`, 5/5, runs on any OS) |
| Swift `BrainClient` / `Protocol` / overlay / tray | **Scaffolded** — needs a Mac to compile/run |
| Embedded-Python bundling + signing/notarization | Not started (Phase 1's riskiest part) |

Phase checkboxes live in `../MACOS_NATIVE_PLAN.md` §6.

## The protocol (Swift ⇄ Python)

Newline-delimited JSON, one object per line. Defined by
[`brain/wisp_brain/protocol.py`](brain/wisp_brain/protocol.py) and mirrored in
[`Sources/Wisp/Bridge/Protocol.swift`](Sources/Wisp/Bridge/Protocol.swift).

```
request   {"id": Int, "method": String, "params": {...}}        host  → brain
response  {"id": Int, "ok": Bool, "result": <any> | "error": String}  brain → host
event     {"event": String, "id": Int?, "data": <any>}          brain → host
```

Streaming methods (`brain.query`, `brain.echo`) emit `reply.chunk` events tagged
with the originating request `id`, then return the full text as the response
`result`. `brain.cancel {"target": id}` cooperatively stops a stream.
**Invariant:** large binary (PCM audio) never crosses this channel — only paths do.

Implemented methods: `ping`, `brain.echo` (streaming demo), `brain.query`
(streaming, wired to `core.query_pipeline` + `core.llm_clients.client`),
`brain.cancel`, `__shutdown__`.

## Run the verified part now (any OS)

```bash
cd macos/brain
python tests/test_brain_host.py          # or: pytest tests/test_brain_host.py
```

This spawns `python -m wisp_brain.host` and exercises ping, id-tagged streaming,
concurrency, cancel, and error propagation — the whole transport, no LLM keys or
models needed.

## Run the Swift app (on a Mac)

**Easiest — double-click** `Start Wisp (Mac Native).command` in the repo root. It
checks for the Swift toolchain, picks a Python for the brain (prefers `.venv`,
else system `python3`), runs the brain self-test, then `swift run Wisp`. This is
separate from `Start Wisp.command` (the Qt app), which is left untouched.

**Manual:**

```bash
cd macos
# Dev: point at this checkout instead of a bundled runtime.
export WISP_BRAIN_PYTHON=$(which python3)
export WISP_BRAIN_DIR="$PWD/brain"
export WISP_REPO_ROOT="$PWD/.."      # so the sidecar can import `core`
swift run Wisp                        # menubar ✦ + floating overlay + handshake
swift test                            # protocol framing tests
```

If double-click is blocked ("cannot be opened"), run once:
`chmod +x "Start Wisp (Mac Native).command"` (the git index already marks it
executable, so a fresh clone/pull won't need this).

`brain.query` additionally needs the `core/` runtime deps (see repo
`requirements.txt`) and provider API keys/`.env`; `ping`/`brain.echo` do not.

## Next

- **Phase 1 finish:** embed python-build-standalone, bundle `wisp_brain` + `core`,
  wrap in an Xcode app target, and prove deep-signing + notarization early
  (plan §7–§9).
- **Phase 2:** the overlay/tray skeleton here graduates to the real doll states.
- **Phase 3+:** CGEvent hotkey tap → intent picker → `brain.query`; then capture,
  audio, context, hardening.
