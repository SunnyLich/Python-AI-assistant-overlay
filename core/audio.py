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
import numpy as np
import config
from core import tts as tts_module
from core.system import macos_safety


def _macos_audio_enabled() -> bool:
    """Return True when in-process macOS audio playback is explicitly enabled."""
    return macos_safety.audio_enabled()

_SD_IMPORT_ERROR: Exception | None = None
_SF_IMPORT_ERROR: ImportError | None = None
_sd_loaded = False
_sf_loaded = False


def _raise_sounddevice_unavailable() -> None:
    raise ModuleNotFoundError("sounddevice is required for audio playback") from _SD_IMPORT_ERROR


class _MissingRawOutputStream:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        _raise_sounddevice_unavailable()

    def __enter__(self):
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN003
        return False


class _MissingSoundDevice:
    RawOutputStream = _MissingRawOutputStream

    class default:
        device = (None, None)

    @staticmethod
    def play(*args, **kwargs):  # noqa: ANN002, ANN003
        _raise_sounddevice_unavailable()

    @staticmethod
    def wait() -> None:
        _raise_sounddevice_unavailable()

    @staticmethod
    def stop() -> None:
        _raise_sounddevice_unavailable()

    @staticmethod
    def query_devices():  # noqa: ANN201
        _raise_sounddevice_unavailable()


class _MissingSoundFile:
    @staticmethod
    def read(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ModuleNotFoundError("soundfile is required for filler audio decoding") from _SF_IMPORT_ERROR


sd = _MissingSoundDevice()
sf = _MissingSoundFile()


def _load_sounddevice_if_allowed():
    global sd, _SD_IMPORT_ERROR, _sd_loaded
    if _sd_loaded:
        return sd
    if not _macos_audio_enabled():
        return sd
    try:
        import sounddevice as _sd
        sd = _sd
    except (ImportError, OSError) as exc:
        _SD_IMPORT_ERROR = exc
    finally:
        _sd_loaded = True
    return sd


def _load_soundfile_if_allowed():
    global sf, _SF_IMPORT_ERROR, _sf_loaded
    if _sf_loaded:
        return sf
    if not _macos_audio_enabled():
        return sf
    try:
        import soundfile as _sf
        sf = _sf
    except ImportError as exc:
        _SF_IMPORT_ERROR = exc
    finally:
        _sf_loaded = True
    return sf


if _macos_audio_enabled():
    _load_sounddevice_if_allowed()
    _load_soundfile_if_allowed()

_tts_speed_boost = False
_tts_speed_lock = threading.Lock()

# Tracks the currently-playing TTS stream so stop() can abort it from any thread.
_playback_lock = threading.Lock()
_current_stop_event: threading.Event | None = None


def stop() -> None:
    """
    Immediately stop any in-progress streaming TTS playback and discard buffered
    audio. Safe to call from any thread; a no-op if nothing is playing.

    Used whenever a new generation supersedes the current one (a fresh query or
    voice capture) so the previous reply is cut off instead of talking over the
    new one.
    """
    with _playback_lock:
        ev = _current_stop_event
    if ev is not None:
        ev.set()


def set_tts_speed_boost(enabled: bool) -> None:
    """Called by the UI while the user holds the speech bubble."""
    global _tts_speed_boost
    with _tts_speed_lock:
        _tts_speed_boost = enabled


def _current_tts_rate() -> float:
    with _tts_speed_lock:
        boosted = _tts_speed_boost
    rate = config.TTS_HOLD_PLAYBACK_RATE if boosted else config.TTS_PLAYBACK_RATE
    return max(0.25, min(4.0, float(rate)))


def _speed_adjust_pcm(chunk: bytes, dtype: str, rate: float) -> bytes:
    """Change PCM playback speed by resampling chunk length before playback."""
    if abs(rate - 1.0) < 0.01 or not chunk:
        return chunk
    np_dtype = np.float32 if dtype == "float32" else np.int16
    samples = np.frombuffer(chunk, dtype=np_dtype)
    if samples.size < 2:
        return chunk
    out_len = max(1, int(samples.size / rate))
    src_x = np.arange(samples.size, dtype=np.float32)
    dst_x = np.linspace(0, samples.size - 1, out_len, dtype=np.float32)
    if np_dtype is np.float32:
        adjusted = np.interp(dst_x, src_x, samples).astype(np.float32)
    else:
        adjusted = np.interp(dst_x, src_x, samples.astype(np.float32))
        adjusted = np.clip(adjusted, -32768, 32767).astype(np.int16)
    return adjusted.tobytes()


# ------------------------------------------------------------------
# Filler audio
# ------------------------------------------------------------------

# Decoded filler clips kept in memory so the hotkey path does zero disk I/O.
_filler_clips: list[tuple[np.ndarray, int]] = []   # (samples, samplerate)
_filler_loaded = False


def prewarm_filler() -> None:
    """Decode all filler WAVs into memory once so play_filler() never touches
    disk on the latency-critical hotkey path. Safe to call repeatedly.

    Loads from both the bundled assets dir and the user-data dir (where
    TTS-baked voice-matched clips live), so a user with a Cartesia voice gets
    a mix of stock + voice-matched fillers."""
    if not _macos_audio_enabled():
        return
    global _filler_clips, _filler_loaded
    sf_mod = _load_soundfile_if_allowed()
    clips: list[tuple[np.ndarray, int]] = []
    dirs = [config.FILLER_AUDIO_DIR, getattr(config, "USER_FILLER_AUDIO_DIR", "")]
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if not f.lower().endswith(".wav"):
                continue
            try:
                data, samplerate = sf_mod.read(os.path.join(d, f), dtype="float32")
                clips.append((data, samplerate))
            except Exception as e:
                print(f"[audio] filler precache error for {f}: {e}")
    _filler_clips = clips
    _filler_loaded = True


def play_filler():
    """
    Play a random pre-decoded filler clip instantly (non-blocking).
    Safe to call from any thread.
    """
    if not _macos_audio_enabled():
        return
    if not _filler_loaded:
        prewarm_filler()
    if not _filler_clips:
        return  # no filler files available, skip silently

    data, samplerate = random.choice(_filler_clips)
    threading.Thread(target=_play_clip, args=(data, samplerate), daemon=True).start()


def _play_clip(data: np.ndarray, samplerate: int):
    try:
        sd_mod = _load_sounddevice_if_allowed()
        # sd.play opens and starts a PortAudio output stream, so it must run on
        # the main thread (see _run_on_main): opening it on this worker thread
        # segfaults inside CoreAudio under Qt's Cocoa run loop, exactly like the
        # TTS stream below.
        #
        # sd.wait() is NOT safe to call here: in this sounddevice version it does
        # `self.event.wait()` then `self.stream.close()` (see
        # _CallbackContext.wait), so it would tear the CoreAudio stream down from
        # this worker thread — the same off-main hazard as opening it. Instead
        # capture the callback context atomically with the play() call (on main),
        # block on its completion event here (a plain flag wait, safe off-main),
        # then hop the close back onto the main thread.
        def _start():
            sd_mod.play(data, samplerate)
            return sd_mod._last_callback  # the _CallbackContext play() just created
        ctx = _run_on_main(_start)
        if ctx is not None:
            ctx.event.wait()  # ~<1s; blocks only on a flag, no native call
            _run_on_main(lambda: ctx.stream.close(ignore_errors=True))
    except Exception as e:
        print(f"[audio] filler playback error: {e}")


# ------------------------------------------------------------------
# Streaming TTS playback
# ------------------------------------------------------------------

# Every native PortAudio handle (filler clip, TTS output stream) must be opened
# and torn down on the GUI main thread; doing it on a worker thread segfaults
# inside CoreAudio while Qt's Cocoa run loop owns the process. set_main_thread_runner
# is re-exported so existing callers (main.py) register the runner the same way;
# stt.py and the macOS capture paths share the same runner via core.system.main_thread.
from core.system.main_thread import run_on_main as _run_on_main, set_main_thread_runner  # noqa: F401


def play_tts_stream(text: str, on_done: callable | None = None):
    """
    Stream TTS for `text` and play it as chunks arrive.
    Non-blocking — runs in a daemon thread.
    """
    play_tts_stream_from_chunks(iter([text]), on_done=on_done)


def play_tts_stream_from_chunks(text_chunks, on_done: callable | None = None,
                                on_audio_start: callable | None = None,
                                on_word_timestamps: callable | None = None,
                                on_amplitude: callable | None = None):
    """
    Stream TTS from an iterable of text chunks (e.g. live LLM stream) and play.
    Non-blocking — runs in a daemon thread.

    Args:
        text_chunks: Iterable[str] of text pieces fed incrementally.
        on_done: Optional callback invoked when playback finishes.
        on_audio_start: Optional callback invoked when the first audio chunk
                        is written to the output stream (i.e. audio is audible).
        on_word_timestamps: Optional callback(words, start_ms) from Cartesia timestamps.
    """
    threading.Thread(
        target=_stream_and_play_chunks, args=(text_chunks, on_done, on_audio_start, on_word_timestamps, on_amplitude), daemon=True
    ).start()


def _stream_and_play_chunks(text_chunks, on_done: callable | None,
                            on_audio_start: callable | None,
                            on_word_timestamps: callable | None,
                            on_amplitude: callable | None):
    provider = config.TTS_PROVIDER.lower()
    if not _macos_audio_enabled():
        provider = "none"
    if provider == "none":
        # No audio device should be opened in text-only mode. On macOS, even an
        # unused PortAudio stream can enter CoreAudio and crash under Qt/Cocoa.
        for _ in text_chunks:
            pass
        if on_done:
            on_done()
        return

    sd_mod = _load_sounddevice_if_allowed()

    chunk_q: queue.Queue[bytes | None] = queue.Queue()

    # Register this playback as the current one so stop() can abort it.
    global _current_stop_event
    stop_event = threading.Event()
    with _playback_lock:
        _current_stop_event = stop_event

    # Producer: fetch TTS audio chunks
    def producer():
        try:
            for chunk in tts_module.stream_audio_from_chunks(text_chunks,
                                                              on_word_timestamps=on_word_timestamps):
                if stop_event.is_set():
                    break
                chunk_q.put(chunk)
        finally:
            chunk_q.put(None)  # sentinel

    threading.Thread(target=producer, daemon=True).start()

    # Consumer: feed chunks to sounddevice output stream
    # Derive playback params from the configured provider so that ElevenLabs
    # (22050 Hz int16) and Cartesia (44100 Hz float32) both play correctly,
    # regardless of which was used last.
    if provider == "elevenlabs":
        sample_rate = tts_module._EL_SAMPLE_RATE
        channels    = tts_module.CHANNELS
        dtype       = tts_module._EL_DTYPE
    else:
        sample_rate = tts_module.SAMPLE_RATE
        channels    = tts_module.CHANNELS
        dtype       = tts_module.DTYPE

    # macOS CoreAudio segfaults when two PortAudio streams are open on the same
    # device at once. The filler clip (play_filler -> sd.play) is still playing
    # when we get here, so stop it before opening the TTS output stream. sd.stop()
    # tears down that PortAudio stream, so it must run on the main thread too (see
    # _run_on_main). Harmless on Windows/Linux (just ends the filler a few ms
    # early, which is desired).
    try:
        _run_on_main(sd_mod.stop)
    except Exception:
        pass

    def _open_stream():
        s = sd_mod.RawOutputStream(samplerate=sample_rate, channels=channels, dtype=dtype)
        s.start()  # RawOutputStream's context manager normally does this on __enter__
        return s

    def _close_stream(s):
        try:
            s.stop()
        finally:
            s.close()

    _audio_started = False
    stream = None
    try:
        print(f"[audio] opening TTS stream (rate={sample_rate}, ch={channels}, dtype={dtype})", flush=True)
        # Open on the main thread (see _run_on_main): CoreAudio + Qt's run loop
        # segfault if the stream is opened on this worker thread. Writes below
        # stay on the worker so the UI never blocks during playback.
        stream = _run_on_main(_open_stream)
        print("[audio] TTS stream opened", flush=True)
        while True:
            if stop_event.is_set():
                # abort() tears down the PortAudio stream — keep it on the main
                # thread like open/close (writes below stay on this worker).
                _run_on_main(stream.abort)  # discard buffered audio immediately
                break
            try:
                chunk = chunk_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break
            if not _audio_started:
                _audio_started = True
                print(f"[audio] writing first chunk ({len(chunk)} bytes)", flush=True)
                if on_audio_start:
                    try:
                        on_audio_start()
                    except Exception:
                        pass
            adjusted_chunk = _speed_adjust_pcm(chunk, dtype, _current_tts_rate())
            stream.write(adjusted_chunk)
            if on_amplitude:
                try:
                    amp_dtype = np.float32 if dtype == "float32" else np.int16
                    samples = np.frombuffer(adjusted_chunk, dtype=amp_dtype)
                    amp = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                    on_amplitude(min(amp, 1.0))
                except Exception:
                    pass
    except ModuleNotFoundError as exc:
        print(f"[audio] streaming playback unavailable: {exc}")
    finally:
        if stream is not None:
            try:
                _run_on_main(lambda: _close_stream(stream))
            except Exception:
                pass

    with _playback_lock:
        if _current_stop_event is stop_event:
            _current_stop_event = None

    # Suppress the completion callback when interrupted — the superseding
    # generation owns the UI state now.
    if on_done and not stop_event.is_set():
        on_done()
