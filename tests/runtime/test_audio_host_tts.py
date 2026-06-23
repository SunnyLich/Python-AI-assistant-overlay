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
