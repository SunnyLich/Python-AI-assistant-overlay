"""
wisp_brain.handlers — methods that execute INSIDE the brain sidecar.

Each entry in ``HANDLERS`` maps a protocol ``method`` to a callable. Methods in
``STREAMING`` receive a ``StreamContext`` as their first positional argument and
may push ``reply.chunk``-style events (tagged with the request id) before they
return their final result; everything else is a plain unary call whose return
value becomes the response ``result``.

Heavy / OS-agnostic brain modules (``core.query_pipeline``,
``core.llm_clients.client``, faster-whisper, ...) are imported LAZILY inside the
handlers, never at module import, so the sidecar boots and can answer ``ping`` on
any platform with no API keys or models present. That is what lets this file be
tested from Windows/CI without the LLM stack.
"""
from __future__ import annotations

import json
import os
import ast
import threading
import time
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterator

# Keep optional-dependency chatter off the protocol channel's stderr mirror.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

HANDLERS: dict[str, Callable[..., Any]] = {}
STREAMING: set[str] = set()
_STT_MODEL = None


class StreamContext:
    """Passed to streaming handlers; ``emit`` tags events with the request id so
    the host can route partial output back to the originating call."""

    __slots__ = ("_emit", "req_id", "cancelled")

    def __init__(self, emit: Callable[[str, Any, Any], None], req_id: Any) -> None:
        self._emit = emit          # (event_name, data, req_id) -> None
        self.req_id = req_id
        self.cancelled = False

    def emit(self, event: str, data: Any = None) -> None:
        self._emit(event, data, self.req_id)


def handler(name: str, *, streaming: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        HANDLERS[name] = fn
        if streaming:
            STREAMING.add(name)
        return fn
    return deco


def _log(msg: str) -> None:
    print(f"[brain] {msg}", flush=True)  # -> stderr (host redirects fd 1 to fd 2)


def _runtime_output_dir() -> Path:
    """Directory for large sidecar artifacts returned by path over IPC."""
    run_log_dir = os.getenv("WISP_RUN_LOG_DIR")
    if run_log_dir:
        out = Path(run_log_dir)
    else:
        import tempfile
        out = Path(tempfile.gettempdir()) / "wisp-brain"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Offline / deterministic seams (tests + dev smoke; OFF in production).
#
# These are guarded by environment variables that are unset in the shipped app,
# so the real LLM / TTS paths run normally on the Mac. When set, the brain
# produces deterministic output with no network or models, which is what lets
# the full handler set -- including ``brain.query`` and ``brain.agent.run`` --
# be exercised end-to-end from Windows/CI. This mirrors the existing
# ``WISP_MACOS_*`` env-flag style used elsewhere in the codebase.
# ---------------------------------------------------------------------------

def _offline_brain() -> bool:
    """True when the brain should answer deterministically without network/models.

    Driven by ``WISP_BRAIN_FAKE_LLM`` (any non-empty value). Used by the query,
    tts, and agent handlers so an off-Mac integration run never touches a real
    provider, model, or API key.
    """
    return bool(os.getenv("WISP_BRAIN_FAKE_LLM"))


def _write_silent_wav(path: Path, *, sample_rate: int = 22_050, milliseconds: int = 120) -> int:
    """Write a mono int16 silent WAV and return the byte count of its frames."""
    frames = max(1, int(sample_rate * milliseconds / 1000))
    silence = b"\x00\x00" * frames
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence)
    return len(silence)


def _agent_test_model_callback() -> Callable[[str], str] | None:
    """Return a model callback that drives the agent loop without a real LLM.

    Resolution order:
      1. ``WISP_BRAIN_AGENT_TEST_SCRIPT`` -> path to a JSON array of agent
         response strings (or objects). Each model turn pops the next entry; once
         exhausted the agent is told to finish. This lets a test script multi-turn
         tool-call behavior deterministically.
      2. ``WISP_BRAIN_FAKE_LLM`` -> single-turn callback that immediately returns a
         valid ``final`` agent response, so the loop completes in one turn offline.
      3. Otherwise ``None`` -> the runner calls the configured provider (production).
    """
    script_path = os.getenv("WISP_BRAIN_AGENT_TEST_SCRIPT")
    if script_path:
        raw = json.loads(Path(script_path).read_text(encoding="utf-8"))
        responses = [r if isinstance(r, str) else json.dumps(r) for r in raw]
        index = {"i": 0}
        lock = threading.Lock()
        done = json.dumps({"thought": "script exhausted", "final": "Done.", "tool_calls": []})

        def scripted(_prompt: str) -> str:
            with lock:
                i = index["i"]
                index["i"] = i + 1
            return responses[i] if i < len(responses) else done

        return scripted

    if _offline_brain():
        canned = json.dumps(
            {"thought": "fake offline run", "final": "Fake agent run complete.", "tool_calls": []}
        )

        def fake(_prompt: str) -> str:
            return canned

        return fake

    return None


# ---------------------------------------------------------------------------
# Diagnostics (no heavy imports -- always available)
# ---------------------------------------------------------------------------

@handler("ping")
def ping(value: Any = None) -> dict[str, Any]:
    """Liveness / round-trip check. Echoes *value* and reports the sidecar pid."""
    return {"pong": True, "value": value, "pid": os.getpid()}


@handler("brain.config.reload")
def brain_config_reload() -> dict[str, Any]:
    """Reload .env-backed Python config after the native Settings panel saves."""
    import config

    config.reload()
    return {
        "ok": True,
        "llm_provider": getattr(config, "LLM_PROVIDER", ""),
        "llm_model": getattr(config, "LLM_MODEL", ""),
        "tts_provider": getattr(config, "TTS_PROVIDER", ""),
    }


@handler("brain.plugins.list")
def brain_plugins_list() -> dict[str, Any]:
    """Return loaded/discoverable plugins for the native macOS Plugin Manager."""
    from core.system.paths import PLUGINS_DIR

    plugins_dir = Path(PLUGINS_DIR)
    return {
        "plugins_dir": str(plugins_dir),
        "plugins": _plugin_summaries(plugins_dir),
    }


def _plugin_summaries(plugins_dir: Path) -> list[dict[str, Any]]:
    try:
        from core.plugin_manager import get_manager

        manager = get_manager()
        mods = getattr(manager, "_mods", [])
        return [_loaded_plugin_payload(mod) for mod in mods]
    except Exception:
        return _discover_plugin_payloads(plugins_dir)


def _loaded_plugin_payload(mod: Any) -> dict[str, Any]:
    module = getattr(mod, "module", None)
    path = getattr(module, "__file__", "") or ""
    hooks = _plugin_hook_names(module)
    return {
        "name": str(getattr(mod, "name", "")),
        "path": str(Path(path).parent) if path else "",
        "status": "loaded",
        "hooks": hooks,
        "tray_actions": _safe_tray_action_labels(module),
        "tools": _safe_tool_names(module),
        "error": "",
    }


def _discover_plugin_payloads(plugins_dir: Path) -> list[dict[str, Any]]:
    if not plugins_dir.exists():
        return []

    payloads: list[dict[str, Any]] = []
    for child in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        init_path = child / "__init__.py"
        if not init_path.exists():
            continue
        hooks = _declared_hook_names(init_path)
        payloads.append({
            "name": child.name,
            "path": str(child),
            "status": "discovered",
            "hooks": hooks,
            "tray_actions": [],
            "tools": [],
            "error": "",
        })
    return payloads


def _plugin_hook_names(module: Any) -> list[str]:
    return [
        hook
        for hook in _PLUGIN_HOOKS
        if module is not None and hasattr(module, hook)
    ]


def _declared_hook_names(init_path: Path) -> list[str]:
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    except Exception:
        return []
    declared = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return [hook for hook in _PLUGIN_HOOKS if hook in declared]


def _safe_tray_action_labels(module: Any) -> list[str]:
    fn = getattr(module, "get_tray_actions", None)
    if not callable(fn):
        return []
    try:
        items = fn()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [
        str(item.get("label", "Action"))
        for item in items
        if isinstance(item, dict)
    ]


def _safe_tool_names(module: Any) -> list[str]:
    fn = getattr(module, "get_tools", None)
    if not callable(fn):
        return []
    try:
        items = fn()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [
        str(item.get("name", "?"))
        for item in items
        if isinstance(item, dict)
    ]


_PLUGIN_HOOKS = (
    "on_startup",
    "on_shutdown",
    "before_query",
    "after_response",
    "get_tools",
    "get_tray_actions",
    "get_system_prompt_section",
)


@handler("brain.echo", streaming=True)
def brain_echo(ctx: StreamContext, text: str = "", chunk_size: int = 1, delay: float = 0.0) -> dict[str, Any]:
    """Stream *text* back word-by-word as ``reply.chunk`` events, then return the
    whole string. Pure-Python, no models or network -- this is the streaming
    handshake the Phase-1 test exercises to prove event correlation works."""
    words = text.split(" ") if text else []
    sent: list[str] = []
    for i in range(0, len(words), max(1, chunk_size)):
        if ctx.cancelled:
            break
        piece = " ".join(words[i:i + max(1, chunk_size)])
        if i + max(1, chunk_size) < len(words):
            piece += " "
        sent.append(piece)
        ctx.emit("reply.chunk", {"text": piece})
        if delay:
            time.sleep(delay)
    full = "".join(sent)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


# ---------------------------------------------------------------------------
# Audio model endpoints -- Swift owns CoreAudio; Python only reads/writes paths.
# ---------------------------------------------------------------------------

@handler("brain.transcribe")
def brain_transcribe(pcm_path: str = "", language: str | None = None) -> dict[str, Any]:
    """Transcribe a WAV/audio file recorded by Swift.

    This deliberately does NOT import ``core.stt`` because that module still owns
    legacy sounddevice recording. The native shell has already captured audio;
    the sidecar only loads faster-whisper and transcribes a normalized numpy
    array. Large PCM never crosses IPC, only ``pcm_path``.
    """
    if not pcm_path:
        raise ValueError("pcm_path is required")

    import numpy as np
    import soundfile as sf
    import config

    data, sample_rate = sf.read(pcm_path, dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    if sample_rate != 16_000:
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(sample_rate), 16_000)
            data = resample_poly(data, 16_000 // g, int(sample_rate) // g).astype("float32")
        except Exception:
            # Linear fallback keeps the handler usable if scipy is missing.
            duration = len(data) / float(sample_rate)
            src_x = np.linspace(0.0, duration, num=len(data), endpoint=False)
            dst_len = max(1, int(duration * 16_000))
            dst_x = np.linspace(0.0, duration, num=dst_len, endpoint=False)
            data = np.interp(dst_x, src_x, data).astype("float32")

    if len(data) < 16_000 * 0.25:
        return {"text": "", "duration": len(data) / 16_000, "reason": "too_short"}

    global _STT_MODEL
    if _STT_MODEL is None:
        from faster_whisper import WhisperModel
        _STT_MODEL = WhisperModel(
            config.STT_MODEL,
            device="cpu",
            compute_type=config.STT_COMPUTE_TYPE,
        )
        _log(f"loaded STT model {config.STT_MODEL!r}")
    model = _STT_MODEL
    segments, _info = model.transcribe(
        data,
        beam_size=1,
        language=language or config.STT_LANGUAGE or None,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    _log(f"transcribed {pcm_path!r}: {text!r}")
    return {"text": text, "duration": len(data) / 16_000}


@handler("brain.tts.synthesize")
def brain_tts_synthesize(text: str = "", voice: str | None = None) -> dict[str, Any]:
    """Synthesize text to a standard int16 WAV file for Swift playback."""
    if not text.strip():
        raise ValueError("text is required")

    if _offline_brain():
        out_path = _runtime_output_dir() / f"tts-{int(time.time() * 1000)}.wav"
        n_bytes = _write_silent_wav(out_path, sample_rate=22_050, milliseconds=120)
        return {"path": str(out_path), "sample_rate": 22_050, "bytes": n_bytes, "provider": "fake"}

    import numpy as np
    import config
    from core import tts

    chunks = list(tts.stream_audio(text))
    out_path = _runtime_output_dir() / f"tts-{int(time.time() * 1000)}.wav"

    provider = config.TTS_PROVIDER.lower()
    if provider == "none" or not chunks:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22_050)
            wf.writeframes(b"")
        return {"path": str(out_path), "sample_rate": 22_050, "bytes": 0, "provider": provider}

    if provider == "elevenlabs":
        sample_rate = tts._EL_SAMPLE_RATE
        pcm_i16 = b"".join(chunks)
    else:
        sample_rate = tts.SAMPLE_RATE
        # np.frombuffer yields a read-only view over the immutable bytes, so
        # nan_to_num must copy (copy=False raises "assignment destination is
        # read-only"). clip then returns its own writable array.
        audio_f32 = np.frombuffer(b"".join(chunks), dtype=np.float32)
        audio_f32 = np.nan_to_num(audio_f32)
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_i16 = (audio_f32 * 32767.0).astype("<i2").tobytes()

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16)

    _log(f"tts synthesized {len(pcm_i16)} bytes -> {out_path}")
    return {"path": str(out_path), "sample_rate": sample_rate, "bytes": len(pcm_i16), "provider": provider}


@handler("brain.tts.test")
def brain_tts_test(provider: str = "", cartesia_voice_id: str = "") -> dict[str, Any]:
    """Validate the configured TTS route for the native Settings panel."""
    if _offline_brain():
        selected = (provider or "fake").strip().lower()
        return {"ok": True, "message": f"TTS route OK: {selected}", "provider": selected}

    import config
    from core import tts

    selected = (provider or config.TTS_PROVIDER or "none").strip().lower()
    voice = cartesia_voice_id if cartesia_voice_id is not None else config.CARTESIA_VOICE_ID
    ok, message = tts.test_connection(
        selected,
        cartesia_voice_id=voice,
    )
    return {"ok": ok, "message": message, "provider": selected}


@handler("brain.llm.test")
def brain_llm_test(
    provider: str = "",
    model: str = "",
    route_name: str = "LLM",
    image: bool = False,
    custom_base_url: str = "",
) -> dict[str, Any]:
    """Validate one configured LLM route for the native Settings panel."""
    selected_provider = provider.strip().lower()
    selected_model = model.strip()
    label = route_name.strip() or "LLM"
    if not selected_provider or not selected_model:
        return {
            "ok": False,
            "message": f"{label} test failed: No model configured.",
            "provider": selected_provider,
            "model": selected_model,
        }

    if _offline_brain():
        suffix = " vision route" if image else " route"
        return {
            "ok": True,
            "message": f"{label}{suffix} OK: {selected_provider} / {selected_model}",
            "provider": selected_provider,
            "model": selected_model,
        }

    from core.llm_clients import client as llm

    ok, message = llm.test_route_connection(
        selected_provider,
        selected_model,
        label,
        image=image,
        custom_base_url=custom_base_url.strip() or None,
    )
    return {"ok": ok, "message": message, "provider": selected_provider, "model": selected_model}


# ---------------------------------------------------------------------------
# Real query path -- wired to the existing pipeline, exercised on the Mac / online.
# Imports are lazy so this module still loads with no LLM deps/keys present.
# ---------------------------------------------------------------------------

@handler("brain.query", streaming=True)
def brain_query(
    ctx: StreamContext,
    intent_prompt: str = "",
    selected: str | None = None,
    screenshot_b64: str | None = None,
    ambient_text: str = "",
    memory_context: str = "",
    use_tools: bool = False,
    allow_screenshot_tool: bool = False,
) -> dict[str, Any]:
    """Assemble context and stream an LLM reply, mirroring App._query_and_speak.

    Reuses the OS-agnostic brain verbatim: ``core.query_pipeline.build_context``
    for precedence rules and ``core.llm_clients.client.stream_response`` for the
    token stream. Each chunk becomes a ``reply.chunk`` event tagged with this
    request's id; the full text is the final response result.
    """
    from core.query_pipeline import ContextInputs, build_context

    if not memory_context:
        try:
            from core.memory_store import store
            memory_context = store.get_manager().retrieve_relevant(intent_prompt) or ""
        except Exception as exc:  # memory should not block answering
            _log(f"memory retrieval skipped: {type(exc).__name__}: {exc}")

    built = build_context(
        ContextInputs(
            intent_prompt=intent_prompt,
            selected=selected,
            screenshot_b64=screenshot_b64,
            ambient_text=ambient_text,
        )
    )

    parts: list[str] = []
    for chunk in _stream_query_reply(built, memory_context, use_tools, allow_screenshot_tool):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


def _stream_query_reply(
    built: Any,
    memory_context: str,
    use_tools: bool,
    allow_screenshot_tool: bool,
) -> Iterator[str]:
    """Token stream for ``brain.query``: real provider, or deterministic offline.

    In offline mode (``WISP_BRAIN_FAKE_LLM``) the assembled prompt is still built
    by ``core.query_pipeline.build_context`` -- so context precedence is exercised
    for real -- and the reply just echoes the intent plus the assembled ambient
    context (selected text, clipboard, app/window) with a ``[fake-llm]`` tag, so
    tests can assert reassembly and that each of their inputs reached the model.
    """
    if _offline_brain():
        prompt = (getattr(built, "user_message", "") or "").strip()
        ambient = (getattr(built, "ambient_ctx", "") or "").strip()
        combined = (prompt + ("\n" + ambient if ambient else "")).strip()
        reply = f"[fake-llm] {combined}".strip()
        for word in reply.split(" "):
            yield word + " "
        return

    from core.llm_clients.client import stream_response

    yield from stream_response(
        built.user_message,
        image_base64=built.screenshot_b64,
        ambient_context=built.ambient_ctx,
        memory_context=memory_context,
        use_tools=use_tools,
        allow_screenshot_tool=allow_screenshot_tool,
    )


@handler("brain.rewrite", streaming=True)
def brain_rewrite(
    ctx: StreamContext,
    selected_text: str = "",
    intent_prompt: str = "Rewrite or fix the following text",
) -> dict[str, Any]:
    """Stream an inline rewrite for native paste-back callers."""
    selected_text = selected_text.strip()
    if not selected_text:
        raise ValueError("selected_text is required")

    parts: list[str] = []
    for chunk in _stream_rewrite_reply(selected_text, intent_prompt):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


def _stream_rewrite_reply(selected_text: str, intent_prompt: str) -> Iterator[str]:
    if _offline_brain():
        reply = f"[fake-rewrite] {intent_prompt}: {selected_text}"
        for word in reply.split(" "):
            yield word + " "
        return

    from core.llm_clients.client import stream_rewrite

    yield from stream_rewrite(selected_text, intent_prompt)


@handler("brain.chat", streaming=True)
def brain_chat(
    ctx: StreamContext,
    messages: list[dict[str, Any]] | None = None,
    memory_context: str = "",
) -> dict[str, Any]:
    """Stream a multi-turn chat reply from the existing chat LLM path."""
    turns = _normalize_chat_messages(messages or [])
    if not turns:
        raise ValueError("messages must include at least one user turn")

    if not memory_context:
        last_user = next(
            (str(m.get("content") or "") for m in reversed(turns) if m.get("role") == "user"),
            "",
        )
        if last_user:
            try:
                from core.memory_store import store
                memory_context = store.get_manager().retrieve_relevant(last_user) or ""
            except Exception as exc:  # memory should not block chat
                _log(f"chat memory retrieval skipped: {type(exc).__name__}: {exc}")

    parts: list[str] = []
    for chunk in _stream_chat_reply(turns, memory_context):
        if ctx.cancelled:
            break
        parts.append(chunk)
        ctx.emit("reply.chunk", {"text": chunk})

    full = "".join(parts)
    ctx.emit("reply.done", {"text": full})
    return {"text": full}


def _normalize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    allowed_roles = {"system", "user", "assistant"}
    turns: list[dict[str, str]] = []
    for raw in messages:
        role = str(raw.get("role") or "").strip().lower()
        content = raw.get("content")
        if role not in allowed_roles or content is None:
            continue
        text = str(content).strip()
        if text:
            turns.append({"role": role, "content": text})
    return turns


def _stream_chat_reply(messages: list[dict[str, str]], memory_context: str) -> Iterator[str]:
    if _offline_brain():
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = f"[fake-chat] {last_user}".strip()
        for word in reply.split(" "):
            yield word + " "
        return

    from core.llm_clients.client import stream_response_with_history

    yield from stream_response_with_history(messages, memory_context=memory_context)


@handler("brain.memory.add")
def brain_memory_add(text: str = "", category: str | None = None) -> dict[str, Any]:
    """Add a durable memory fact through the existing memory store."""
    fact = text.strip()
    if not fact:
        raise ValueError("text is required")

    from core.memory_store import store

    manager = store.get_manager()
    if category:
        manager.add_fact_manual(fact, category)
        used_category = category
    else:
        manager.add_explicit_fact(fact)
        used_category = "auto"
    return {"ok": True, "category": used_category, "text": fact}


@handler("brain.memory.search")
def brain_memory_search(query: str = "", top_k: int | None = None) -> dict[str, Any]:
    """Return the same memory block injected into LLM context."""
    if not query.strip():
        raise ValueError("query is required")

    from core.memory_store import store

    text = store.get_manager().retrieve_relevant(query, top_k=top_k) or ""
    return {"text": text}


@handler("brain.memory.list")
def brain_memory_list() -> dict[str, Any]:
    """Return active memory facts for the native macOS memory panel."""
    from core.memory_store import store

    facts = store.get_manager().get_all_facts()
    return {"facts": [_memory_fact_payload(fact) for fact in facts]}


@handler("brain.memory.update")
def brain_memory_update(fact_id: str = "", text: str = "", category: str | None = None) -> dict[str, Any]:
    """Update one durable memory fact through the existing memory store."""
    cleaned_id = fact_id.strip()
    cleaned_text = text.strip()
    if not cleaned_id:
        raise ValueError("fact_id is required")
    if not cleaned_text:
        raise ValueError("text is required")

    from core.memory_store import store

    store.get_manager().update_fact(cleaned_id, cleaned_text, category)
    return {"ok": True, "id": cleaned_id, "text": cleaned_text, "category": category}


@handler("brain.memory.delete")
def brain_memory_delete(fact_id: str = "") -> dict[str, Any]:
    """Delete one durable memory fact through the existing memory store."""
    cleaned_id = fact_id.strip()
    if not cleaned_id:
        raise ValueError("fact_id is required")

    from core.memory_store import store

    store.get_manager().delete_fact(cleaned_id)
    return {"ok": True, "id": cleaned_id}


def _memory_fact_payload(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(fact.get("id") or ""),
        "text": str(fact.get("text") or ""),
        "category": str(fact.get("category") or "general"),
        "source": str(fact.get("source") or "unknown"),
        "created_at": str(fact.get("created_at") or ""),
        "last_seen": str(fact.get("last_seen") or ""),
    }


def _agent_runs_root(log_root: str | None = None) -> Path:
    if log_root:
        root = Path(log_root)
    else:
        root = _runtime_output_dir() / "agent-runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@handler("brain.agent.history.list")
def brain_agent_history_list(log_root: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Return recent agent run folders and lightweight metadata for native UI."""
    root = _agent_runs_root(log_root)
    run_dirs = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    runs = [
        _agent_run_summary(path)
        for path in run_dirs
        if (path / "task.json").exists() or (path / "final.md").exists() or (path / "run.log").exists()
    ]
    return {"runs_root": str(root), "runs": runs[:max(1, int(limit or 100))]}


@handler("brain.agent.history.read")
def brain_agent_history_read(run_dir: str = "") -> dict[str, Any]:
    """Return full readable artifacts for one agent run."""
    path = _agent_run_dir(run_dir)
    if not any((path / name).exists() for name in ("task.json", "final.md", "run.log", "error.txt")):
        raise ValueError("run_dir is not an agent run")

    return {
        **_agent_run_summary(path),
        "task_json": _read_text(path / "task.json"),
        "final": _read_text(path / "final.md"),
        "error": _read_text(path / "error.txt"),
        "run_log": _read_text(path / "run.log"),
        "diff_patch": _read_text(path / "diff.patch"),
    }


@handler("brain.agent.history.retry_spec")
def brain_agent_history_retry_spec(run_dir: str = "") -> dict[str, Any]:
    """Return the original task spec for a previous run."""
    from core.agent.task_spec import retry_spec_from_run

    spec = retry_spec_from_run(_agent_run_dir(run_dir))
    return {"spec": asdict(spec)}


@handler("brain.agent.history.continue_spec")
def brain_agent_history_continue_spec(run_dir: str = "") -> dict[str, Any]:
    """Return a continuation task spec with compact prior-run context."""
    from core.agent.task_spec import continue_spec_from_run

    spec = continue_spec_from_run(_agent_run_dir(run_dir))
    return {"spec": asdict(spec)}


def _agent_run_dir(run_dir: str) -> Path:
    cleaned = run_dir.strip()
    if not cleaned:
        raise ValueError("run_dir is required")
    path = Path(cleaned).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("run_dir does not exist")
    return path


def _agent_run_summary(run_dir: Path) -> dict[str, Any]:
    task = _read_json(run_dir / "task.json")
    final = _read_text(run_dir / "final.md")
    error = _read_text(run_dir / "error.txt")
    run_log = _read_text(run_dir / "run.log", max_chars=12_000)
    title = str((task or {}).get("title") or run_dir.name)
    status = _agent_run_status(final, error, run_log)
    modified = run_dir.stat().st_mtime
    return {
        "id": run_dir.name,
        "run_dir": str(run_dir),
        "title": title,
        "objective": str((task or {}).get("objective") or ""),
        "status": status,
        "modified": modified,
        "modified_display": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(modified)),
        "has_final": bool(final.strip()),
        "has_error": bool(error.strip()),
        "has_diff": (run_dir / "diff.patch").exists(),
    }


def _agent_run_status(final: str, error: str, run_log: str) -> str:
    if error.strip():
        return "failed"
    if "agent run cancelled" in run_log:
        return "cancelled"
    if final.strip() or "agent run finished" in run_log:
        return "complete"
    return "in progress"


def _read_text(path: Path, *, max_chars: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if max_chars is not None and len(text) > max_chars:
        return text[-max_chars:]
    return text


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Agent runtime -- the scoped multi-agent task runner behind the Windows tray's
# "Start agent task" dialog, exposed to the native shell. Reuses the OS-agnostic
# ``core.agent`` verbatim; only the log/trace plumbing and cancel bridging are
# brain-specific. Run artifacts are written under the run-log dir (never the
# repo's memory/agent_runs) so the sidecar stays self-contained on the Mac.
# ---------------------------------------------------------------------------

@handler("brain.agent.run", streaming=True)
def brain_agent_run(ctx: StreamContext, spec: Any = None, log_root: str | None = None) -> dict[str, Any]:
    """Run one scoped agent task, streaming its log/trace lines as events.

    ``spec`` is the same serialized ``AgentTaskSpec`` dict the Windows GUI builds
    (title/objective/scope_folder/permissions/agents/...). Each run.log line is
    emitted as an ``agent.log`` event and each verbose entry as ``agent.trace``,
    both tagged with this request id so the host can render live progress. The
    final result carries the run directory and the final report; the runner never
    raises (it captures failures into ``error.txt``), so a failed task returns its
    error text rather than erroring the call.
    """
    if not isinstance(spec, dict):
        raise ValueError("spec (a serialized agent task dict) is required")

    from core.agent.task_spec import agent_task_spec_from_dict
    from core.agent.runner import AgentTaskRunner
    from core.agent.runtime import AgentRunControl

    task_spec = agent_task_spec_from_dict(spec)
    control = AgentRunControl()

    # Bridge cooperative cancel: the host cancels the stream (brain.cancel) ->
    # ctx.cancelled flips -> propagate into the agent's own cancel token so the
    # run loop stops at its next checkpoint.
    stop_watch = threading.Event()

    def _watch_cancel() -> None:
        while not stop_watch.wait(0.1):
            if ctx.cancelled:
                control.cancel()
                return

    watcher = threading.Thread(target=_watch_cancel, daemon=True)
    watcher.start()

    runs_dir = _agent_runs_root(log_root)
    runner = AgentTaskRunner(
        log_root=runs_dir,
        model_callback=_agent_test_model_callback(),
        control=control,
    )

    def on_log(line: str) -> None:
        ctx.emit("agent.log", {"line": line})

    def on_trace(entry: str) -> None:
        ctx.emit("agent.trace", {"entry": entry})

    try:
        run_dir = runner.run(task_spec, on_log, on_trace)
    finally:
        stop_watch.set()

    run_dir = Path(run_dir)
    final_text = ""
    final_path = run_dir / "final.md"
    if final_path.exists():
        final_text = final_path.read_text(encoding="utf-8", errors="replace")
    error_text = ""
    error_path = run_dir / "error.txt"
    if error_path.exists():
        error_text = error_path.read_text(encoding="utf-8", errors="replace")

    result = {
        "run_dir": str(run_dir),
        "final": final_text,
        "error": error_text,
        "cancelled": control.is_cancelled(),
    }
    ctx.emit("agent.done", result)
    return result


__all__ = ["HANDLERS", "STREAMING", "StreamContext", "handler"]
