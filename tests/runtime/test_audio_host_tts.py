"""Tests for audio worker TTS synthesis and playback helpers."""

from __future__ import annotations

import sys
import types
import wave
import shutil
from pathlib import Path

import pytest

from runtime.workers import audio_host

np = pytest.importorskip("numpy")


@pytest.fixture(autouse=True)
def _run_log_dir(monkeypatch, request):
    """Keep generated audio files inside the test temp dir."""
    root = Path.cwd() / ".pytest-audio-host" / request.node.name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    monkeypatch.setenv("WISP_RUN_LOG_DIR", str(root))
    yield
    shutil.rmtree(root, ignore_errors=True)
    try:
        root.parent.rmdir()
    except OSError:
        pass


def test_tts_synthesize_uses_provider_pcm_format_for_kokoro(monkeypatch):
    """Kokoro streams int16 PCM, so the audio worker must not parse it as float32."""
    import config
    from core import tts

    samples = np.resize(np.array([0, 1200, -1200, 2400], dtype="<i2"), 24_000)
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(tts, "playback_format", lambda provider: (24_000, 1, "int16"))
    monkeypatch.setattr(
        tts,
        "stream_audio_from_chunks",
        lambda chunks, on_word_timestamps=None: iter([samples.tobytes()]),
    )

    result = audio_host.tts_synthesize("hello local voice")

    assert result["provider"] == "kokoro"
    assert result["sample_rate"] == 24_000
    assert result["bytes"] == samples.nbytes
    assert result["word_timestamps"]["words"] == ["hello", "local", "voice"]
    assert result["word_timestamps"]["estimated"] is True
    with wave.open(result["path"], "rb") as wf:
        assert wf.getframerate() == 24_000
        assert wf.readframes(wf.getnframes()) == samples.tobytes()


def test_audio_prewarm_reports_local_warmup_events(monkeypatch):
    """Startup warmup should tell the supervisor when local audio is ready."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    stt_calls: list[bool] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: stt_calls.append(wait))
    monkeypatch.setattr(tts, "prewarm", lambda: None)
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert stt_calls == [True]
    assert events[0] == (
        "audio.warmup.started",
        {"items": ["stt", "tts"], "provider": "kokoro", "reason": "startup"},
    )
    assert events[-1][0] == "audio.warmup.done"
    assert events[-1][1]["ok"] is True


def test_audio_prewarm_reports_component_progress(monkeypatch):
    """Warmup progress should reveal whether STT or local TTS is still loading."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: None)
    monkeypatch.setattr(tts, "prewarm", lambda: None)
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    audio_host.audio_prewarm()

    progress = [(data["item"], data["status"]) for name, data in events if name == "audio.warmup.progress"]
    assert sorted(progress) == sorted([
        ("stt", "started"),
        ("stt", "ok"),
        ("tts", "started"),
        ("tts", "ok"),
    ])
    assert progress.index(("stt", "started")) < progress.index(("stt", "ok"))
    assert progress.index(("tts", "started")) < progress.index(("tts", "ok"))


def test_audio_prewarm_warms_stt_and_tts_in_parallel(monkeypatch):
    """Local voice should start warming before STT finishes."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    calls: list[str] = []
    tts_started = audio_host.threading.Event()
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(config, "KOKORO_DEVICE", "cpu", raising=False)
    monkeypatch.setattr(config, "STT_DEVICE", "cpu", raising=False)

    def stt_prewarm(wait=False):
        calls.append(f"stt-start:{wait}")
        assert tts_started.wait(1.0)
        calls.append("stt-end")

    def tts_prewarm():
        calls.append("tts")
        tts_started.set()

    monkeypatch.setattr(stt_handlers, "stt_prewarm", stt_prewarm)
    monkeypatch.setattr(tts, "prewarm", tts_prewarm)

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert calls.index("tts") < calls.index("stt-end")


def test_audio_prewarm_serializes_kokoro_cuda_warmup(monkeypatch):
    """CUDA Kokoro warmup should not race STT GPU initialization."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    calls: list[str] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(config, "KOKORO_DEVICE", "cuda", raising=False)
    monkeypatch.setattr(config, "STT_DEVICE", "auto", raising=False)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: calls.append(f"stt:{wait}"))
    monkeypatch.setattr(tts, "prewarm", lambda: calls.append("tts"))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert calls == ["tts", "stt:True"]


def test_audio_prewarm_warms_cartesia_tts(monkeypatch):
    """Cartesia has a connection prewarm even though it is not a local model."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    events: list[tuple[str, dict]] = []
    calls: list[str] = []
    monkeypatch.setattr(config, "TTS_PROVIDER", "cartesia", raising=False)
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda wait=False: calls.append(f"stt:{wait}"))
    monkeypatch.setattr(tts, "prewarm", lambda: calls.append("tts"))
    audio_host.set_event_sink(lambda name, data, _req_id: events.append((name, data)))

    result = audio_host.audio_prewarm()

    assert result == {"stt": "ok", "tts": "ok"}
    assert sorted(calls) == ["stt:True", "tts"]
    assert events[0] == (
        "audio.warmup.started",
        {"items": ["stt", "tts"], "provider": "cartesia", "reason": "startup"},
    )
    assert events[-1][1]["ok"] is True


def test_tts_synthesize_raises_while_local_voice_is_warming(monkeypatch):
    """A user TTS request should not sit behind a stuck local warmup forever."""
    import config

    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    audio_host._set_local_tts_warmup(warming=True, ready=False)
    try:
        with pytest.raises(RuntimeError, match="still warming up"):
            audio_host.tts_synthesize("hello")
    finally:
        audio_host._set_local_tts_warmup(warming=False, ready=False)


def test_tts_synthesize_raises_when_provider_returns_no_audio(monkeypatch):
    """Provider failures should not be converted into silent success WAVs."""
    import config
    from core import tts

    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    audio_host._set_local_tts_warmup(warming=False, ready=True)
    monkeypatch.setattr(tts, "stream_audio_from_chunks", lambda *_args, **_kwargs: iter([]))
    monkeypatch.setattr(tts, "reset_connections", lambda: None)

    with pytest.raises(RuntimeError, match="returned no audio"):
        audio_host.tts_synthesize("hello")


def test_play_file_applies_tts_volume(monkeypatch):
    """The Settings volume slider applies to file-based worker playback too."""
    import config

    played = {}
    fake_sd = types.SimpleNamespace(
        play=lambda data, sample_rate: played.update(data=np.array(data), sample_rate=sample_rate),
        wait=lambda: None,
        stop=lambda: None,
    )
    fake_sf = types.SimpleNamespace(
        read=lambda path, dtype="float32": (np.array([0.8, -0.8], dtype=np.float32), 22_050)
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)
    monkeypatch.setattr(config, "TTS_VOLUME", 0.5, raising=False)

    result = audio_host.play_file("voice.wav")

    assert result == {"played": True, "stopped": False}
    assert played["sample_rate"] == 22_050
    assert np.allclose(played["data"], np.array([0.4, -0.4], dtype=np.float32))


def test_config_reload_schedules_prewarm_in_background(monkeypatch):
    """Settings Apply should not block while Kokoro or Whisper warms up."""
    import config
    from core import tts
    from core.macos_helper import handlers as stt_handlers

    calls: list[str] = []
    threads: list[dict] = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=False, name="") -> None:
            threads.append({"target": target, "args": args, "daemon": daemon, "name": name})

        def start(self) -> None:
            calls.append("thread.start")

    monkeypatch.setattr(config, "reload", lambda: calls.append("config.reload"))
    monkeypatch.setattr(config, "TTS_PROVIDER", "kokoro", raising=False)
    monkeypatch.setattr(tts, "reset_connections", lambda: calls.append("tts.reset_connections"))
    monkeypatch.setattr(stt_handlers, "stt_reset_model", lambda: calls.append("stt_reset_model"))
    monkeypatch.setattr(stt_handlers, "stt_prewarm", lambda: calls.append("stt_prewarm"))
    monkeypatch.setattr(tts, "prewarm", lambda: calls.append("tts.prewarm"))
    monkeypatch.setattr(audio_host.threading, "Thread", FakeThread)

    result = audio_host.audio_config_reload()

    assert result == {"ok": True, "tts_provider": "kokoro", "prewarm": "background"}
    assert calls == ["config.reload", "tts.reset_connections", "stt_reset_model", "thread.start"]
    assert threads[0]["name"] == "audio-config-reload-prewarm"
