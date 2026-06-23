"""wisp-audio worker: microphone, STT, TTS synthesis, and playback."""

from __future__ import annotations

import os
import re
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.service_host import run_host

_emit: Callable[[str, Any, Any], None] | None = None
_playback_stop = threading.Event()
_config_prewarm_lock = threading.Lock()
_config_prewarm_generation = 0


def set_event_sink(fn: Callable[[str, Any, Any], None]) -> None:
    """Set event sink."""
    global _emit
    _emit = fn


def _event(name: str, data: Any = None) -> None:
    """Handle event for runtime workers audio host."""
    if _emit is not None:
        _emit(name, data, None)


def _output_dir() -> Path:
    """Handle output dir for runtime workers audio host."""
    root = os.environ.get("WISP_RUN_LOG_DIR")
    if root:
        out = Path(root).expanduser() / "audio"
    else:
        import tempfile

        out = Path(tempfile.gettempdir()) / "wisp-audio"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _prewarm_after_config_reload(generation: int, provider: str) -> None:
    """Warm STT/TTS after config reload without blocking the IPC response."""
    stt_status = "skipped"
    tts_status = "skipped"
    try:
        from core.macos_helper import handlers as stt_handlers

        stt_handlers.stt_prewarm()
        stt_status = "started"
    except Exception as exc:  # noqa: BLE001
        stt_status = f"error: {type(exc).__name__}: {exc}"
    try:
        from core import tts

        tts.prewarm()
        tts_status = "ok"
    except Exception as exc:  # noqa: BLE001
        tts_status = f"error: {type(exc).__name__}: {exc}"
    with _config_prewarm_lock:
        stale = generation != _config_prewarm_generation
    suffix = " stale" if stale else ""
    print(
        f"[audio] config reload background prewarm{suffix}: "
        f"tts_provider={provider!r} stt={stt_status} tts={tts_status}",
        flush=True,
    )


def audio_config_reload() -> dict[str, Any]:
    """Reload .env-backed config in the audio process after Settings → Apply.

    The audio worker is long-lived and owns the live TTS path (audio.tts.synthesize
    -> core.tts.stream_audio), so without this its config.TTS_PROVIDER and cached
    Cartesia WebSocket stay frozen at app-start values — the new provider/voice/key
    never takes effect until restart. Mirrors the desktop dialog's apply: reload
    config, drop cached TTS connections, then re-prewarm under the new settings.
    """
    global _config_prewarm_generation
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    config.reload()
    tts.reset_connections()
    stt_handlers.stt_reset_model()
    provider = config.TTS_PROVIDER.lower()
    with _config_prewarm_lock:
        _config_prewarm_generation += 1
        generation = _config_prewarm_generation
    threading.Thread(
        target=_prewarm_after_config_reload,
        args=(generation, provider),
        daemon=True,
        name="audio-config-reload-prewarm",
    ).start()
    print(f"[audio] config reloaded: tts_provider={provider!r} prewarm=background", flush=True)
    return {"ok": True, "tts_provider": provider, "prewarm": "background"}


def audio_prewarm() -> dict[str, Any]:
    """Warm audio/STT/TTS dependencies in the audio process only."""
    result: dict[str, Any] = {"stt": "skipped", "tts": "skipped"}
    try:
        from core.macos_helper import handlers as stt_handlers

        stt_handlers.stt_prewarm()
        result["stt"] = "started"
    except Exception as exc:  # noqa: BLE001
        result["stt"] = f"error: {type(exc).__name__}: {exc}"
    try:
        from core import tts

        tts.prewarm()
        result["tts"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["tts"] = f"error: {type(exc).__name__}: {exc}"
    return result


def record_start() -> dict[str, Any]:
    """Record start."""
    from core.macos_helper import handlers as stt_handlers

    stt_handlers.stt_start_recording()
    _event("audio.recording.started", {})
    return {"recording": True}


def record_stop_transcribe() -> dict[str, Any]:
    """Record stop transcribe."""
    from core.macos_helper import handlers as stt_handlers

    text = stt_handlers.stt_stop_and_transcribe()
    _event("audio.transcribed", {"text": text})
    return {"text": text}


def stt_is_ready() -> dict[str, Any]:
    """Handle STT is ready for runtime workers audio host."""
    from core.macos_helper import handlers as stt_handlers

    return stt_handlers.stt_is_ready()


def _write_empty_wav(path: Path, *, sample_rate: int = 22_050) -> dict[str, Any]:
    """Write empty wav."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"")
    return {"path": str(path), "sample_rate": sample_rate, "bytes": 0, "provider": "none"}


def _estimated_word_timestamps(text: str, duration_ms: int) -> dict[str, list]:
    """Estimate word timings for providers that do not emit timestamps."""
    words = re.findall(r"\S+", text.strip())
    if not words or duration_ms <= 0:
        return {"words": [], "start_ms": [], "estimated": True}
    step = duration_ms / max(1, len(words))
    return {"words": words, "start_ms": [int(i * step) for i in range(len(words))], "estimated": True}


def tts_synthesize(text: str = "", voice: str | None = None) -> dict[str, Any]:
    """Synthesize text into a WAV file and return its path."""
    if not text.strip():
        raise ValueError("text is required")
    if os.environ.get("WISP_BRAIN_FAKE_LLM"):
        path = _output_dir() / f"tts-{int(time.time() * 1000)}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22_050)
            wf.writeframes(b"\x00\x00" * 1024)
        return {"path": str(path), "sample_rate": 22_050, "bytes": 2048, "provider": "fake"}

    import numpy as np

    import config
    from core import tts

    provider = config.TTS_PROVIDER.lower()
    print(f"[audio] tts.synthesize provider={provider!r} text={text[:40]!r}", flush=True)

    def _synth() -> tuple[list[bytes], list[str], list[int]]:
        # Capture Cartesia word timestamps alongside the PCM so the UI can lock
        # the word highlight to the spoken voice instead of a fixed-WPM estimate.
        # Only Cartesia emits these; other providers leave the lists empty. Fresh
        # lists per call so a retry can't accumulate duplicate timestamps.
        """Handle synth for runtime workers audio host."""
        words: list[str] = []
        start_ms: list[int] = []

        def _collect_ts(ws: list, sms: list) -> None:
            """Handle collect ts for runtime workers audio host."""
            words.extend(ws)
            start_ms.extend(int(x) for x in sms)

        chunks = list(tts.stream_audio_from_chunks([text], on_word_timestamps=_collect_ts))
        return chunks, words, start_ms

    # The prewarmed Cartesia WebSocket can be stale/dead on the first prompt of a
    # session (idle since startup). It either raises (the socket is dropped by
    # _stream_cartesia) or returns no audio — both yield silence on the first try.
    # Retry once with a fresh connection so the first prompt isn't lost.
    expects_audio = provider not in ("none", "")
    try:
        chunks, words, start_ms = _synth()
        if expects_audio and not chunks:
            raise RuntimeError(f"{provider} returned no audio")
    except Exception as exc:  # noqa: BLE001
        print(f"[audio] tts.synthesize first attempt failed ({type(exc).__name__}: {exc}); retrying fresh", flush=True)
        tts.reset_connections()
        try:
            chunks, words, start_ms = _synth()
        except Exception as exc2:  # noqa: BLE001 — surface as silence, not a crash
            print(f"[audio] tts.synthesize retry failed ({type(exc2).__name__}: {exc2})", flush=True)
            chunks, words, start_ms = [], [], []

    path = _output_dir() / f"tts-{int(time.time() * 1000)}.wav"
    if provider == "none" or not chunks:
        return _write_empty_wav(path)

    sample_rate, _channels, dtype = tts.playback_format(provider)
    if dtype == "int16":
        pcm_i16 = b"".join(chunks)
    else:
        audio_f32 = np.frombuffer(b"".join(chunks), dtype=np.float32)
        audio_f32 = np.nan_to_num(audio_f32)
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_i16 = (audio_f32 * 32767.0).astype("<i2").tobytes()

    duration_ms = int((len(pcm_i16) / 2) / sample_rate * 1000) if sample_rate else 0
    word_timestamps = {"words": words, "start_ms": start_ms, "estimated": False}
    if not words:
        word_timestamps = _estimated_word_timestamps(text, duration_ms)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16)
    return {
        "path": str(path),
        "sample_rate": sample_rate,
        "bytes": len(pcm_i16),
        "provider": provider,
        "word_timestamps": word_timestamps,
    }


def play_file(path: str = "") -> dict[str, Any]:
    """Play a WAV file through sounddevice. Returns after playback completes."""
    if not path:
        raise ValueError("path is required")
    _playback_stop.clear()
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    import config

    data, sample_rate = sf.read(path, dtype="float32")
    volume = max(0.0, min(2.0, float(getattr(config, "TTS_VOLUME", 1.0) or 1.0)))
    if volume != 1.0:
        data = np.clip(data * volume, -1.0, 1.0).astype("float32", copy=False)
    _event("audio.playback.started", {"path": path})
    sd.play(data, sample_rate)
    while not _playback_stop.is_set():
        try:
            sd.wait()
            break
        except KeyboardInterrupt:
            _playback_stop.set()
    if _playback_stop.is_set():
        sd.stop()
    _event("audio.playback.done", {"path": path, "stopped": _playback_stop.is_set()})
    return {"played": True, "stopped": _playback_stop.is_set()}


def audio_stop() -> dict[str, Any]:
    """Handle audio stop for runtime workers audio host."""
    _playback_stop.set()
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass
    return {"stopped": True}


def audio_speed_boost(enabled: bool = False) -> dict[str, Any]:
    """Handle audio speed boost for runtime workers audio host."""
    from core import audio_state

    audio_state.set_tts_speed_boost(bool(enabled))
    return {"enabled": bool(enabled)}


HANDLERS = {
    "audio.config.reload": audio_config_reload,
    "audio.prewarm": audio_prewarm,
    "audio.record.start": record_start,
    "audio.record.stop_transcribe": record_stop_transcribe,
    "audio.stt.is_ready": stt_is_ready,
    "audio.tts.synthesize": tts_synthesize,
    "audio.play_file": play_file,
    "audio.stop": audio_stop,
    "audio.speed_boost": audio_speed_boost,
}


def main() -> int:
    """Handle main for runtime workers audio host."""
    return run_host(role="audio", handlers=HANDLERS, event_sink_setter=set_event_sink, threaded=True)


if __name__ == "__main__":
    raise SystemExit(main())
