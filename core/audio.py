"""
core/audio.py — Audio playback engine.

Two responsibilities:
  1. Filler audio: instantly plays a random pre-cached WAV on hotkey press
     to mask LLM+TTS latency and provide sub-50ms acoustic feedback.
  2. Streaming TTS playback: plays PCM chunks from core.tts as they arrive.
"""
from __future__ import annotations
import os
import random
import threading
import queue
import sounddevice as sd
import soundfile as sf
import numpy as np
import config
from core import tts as tts_module


# ------------------------------------------------------------------
# Filler audio
# ------------------------------------------------------------------

_filler_files: list[str] = []
_filler_loaded = False


def _load_filler_files():
    global _filler_files, _filler_loaded
    d = config.FILLER_AUDIO_DIR
    if os.path.isdir(d):
        _filler_files = [
            os.path.join(d, f)
            for f in os.listdir(d)
            if f.lower().endswith(".wav")
        ]
    _filler_loaded = True


def play_filler():
    """
    Play a random filler clip instantly (non-blocking).
    Safe to call from any thread.
    """
    if not _filler_loaded:
        _load_filler_files()
    if not _filler_files:
        return  # no filler files available yet, skip silently

    path = random.choice(_filler_files)
    threading.Thread(target=_play_wav_file, args=(path,), daemon=True).start()


def _play_wav_file(path: str):
    try:
        data, samplerate = sf.read(path, dtype="float32")
        sd.play(data, samplerate)
        sd.wait()
    except Exception as e:
        print(f"[audio] filler playback error: {e}")


# ------------------------------------------------------------------
# Streaming TTS playback
# ------------------------------------------------------------------

def play_tts_stream(text: str, on_done: callable | None = None):
    """
    Stream TTS for `text` and play it as chunks arrive.
    Non-blocking — runs in a daemon thread.
    """
    play_tts_stream_from_chunks(iter([text]), on_done=on_done)


def play_tts_stream_from_chunks(text_chunks, on_done: callable | None = None):
    """
    Stream TTS from an iterable of text chunks (e.g. live LLM stream) and play.
    Non-blocking — runs in a daemon thread.

    Args:
        text_chunks: Iterable[str] of text pieces fed incrementally.
        on_done: Optional callback invoked when playback finishes.
    """
    threading.Thread(
        target=_stream_and_play_chunks, args=(text_chunks, on_done), daemon=True
    ).start()


def _stream_and_play_chunks(text_chunks, on_done: callable | None):
    chunk_q: queue.Queue[bytes | None] = queue.Queue()

    # Producer: fetch TTS audio chunks
    def producer():
        try:
            for chunk in tts_module.stream_audio_from_chunks(text_chunks):
                chunk_q.put(chunk)
        finally:
            chunk_q.put(None)  # sentinel

    threading.Thread(target=producer, daemon=True).start()

    # Consumer: feed chunks to sounddevice output stream
    sample_rate = tts_module.SAMPLE_RATE
    channels = tts_module.CHANNELS


    with sd.RawOutputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype=tts_module.DTYPE,
    ) as stream:
        while True:
            chunk = chunk_q.get()
            if chunk is None:
                break
            stream.write(chunk)

    if on_done:
        on_done()
