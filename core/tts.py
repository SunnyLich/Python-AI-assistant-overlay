"""
core/tts.py — Streaming TTS client.

Supports:
  - Cartesia 3.x (~75ms TTFT) via WebSocket — pcm_f32le, 44100 Hz
  - ElevenLabs (~300-500ms TTFT) — pcm_22050, optional voice id + model
  - OpenAI TTS — raw PCM, 24000 Hz int16 (reuses OPENAI_API_KEY)
  - OpenAI-compatible endpoint — any server exposing /audio/speech with a
    response_format=pcm option (self-hosted Kokoro/LocalAI, Groq, …); base URL,
    key, voice, model and sample rate are all configurable.

The Cartesia WebSocket connection is kept alive as a singleton so every call
avoids the ~600ms handshake penalty. Call prewarm() at app startup.

Yields raw PCM audio bytes as they stream in, ready for sounddevice playback.
Each provider yields its own sample rate / dtype — use playback_format() to get
the (sample_rate, channels, dtype) the player must open the output stream with.

For Cartesia, also exposes stream_audio_from_chunks() which accepts an
iterator of text pieces (e.g. LLM stream chunks) so TTS starts before the
full LLM response is available — this is the lowest-latency path.
"""
from __future__ import annotations
import config
import inspect
import io
import os
import threading
import time
import wave
from pathlib import Path
from typing import Generator, Iterable

from core.system import macos_safety
from core.system.native_locks import ssl_init_lock
from core.system import sdk_clients


SAMPLE_RATE = 44100   # Hz  (Cartesia / default)
CHANNELS = 1
DTYPE = "float32"     # pcm_f32le  (Cartesia / default)

# ElevenLabs uses a different output format
_EL_SAMPLE_RATE = 22050
_EL_DTYPE = "int16"

# OpenAI TTS streams raw PCM at 24 kHz, signed 16-bit, mono.
_OPENAI_SAMPLE_RATE = 24000
_OPENAI_DTYPE = "int16"

# GPT-SoVITS API returns WAV by default; Wisp plays signed 16-bit PCM.
_GPT_SOVITS_SAMPLE_RATE = 32000
_GPT_SOVITS_DTYPE = "int16"

# Kokoro returns float audio at 24 kHz; Wisp plays signed 16-bit PCM.
_KOKORO_SAMPLE_RATE = 24000
_KOKORO_DTYPE = "int16"


def playback_format(provider: str | None = None) -> tuple[int, int, str]:
    """Return (sample_rate, channels, dtype) the player must use for `provider`.

    Each provider streams its own PCM format, so the output stream has to be
    opened with matching parameters or playback is garbled / wrong-pitch.
    """
    provider = (provider or config.TTS_PROVIDER).lower().strip()
    if provider == "elevenlabs":
        return _EL_SAMPLE_RATE, CHANNELS, _EL_DTYPE
    if provider == "openai":
        return _OPENAI_SAMPLE_RATE, CHANNELS, _OPENAI_DTYPE
    if provider == "openai_compatible":
        rate = int(getattr(config, "TTS_CUSTOM_SAMPLE_RATE", 24000) or 24000)
        return rate, CHANNELS, "int16"
    if provider == "gpt_sovits":
        rate = int(getattr(config, "GPT_SOVITS_SAMPLE_RATE", _GPT_SOVITS_SAMPLE_RATE) or _GPT_SOVITS_SAMPLE_RATE)
        return rate, CHANNELS, _GPT_SOVITS_DTYPE
    if provider == "kokoro":
        rate = int(getattr(config, "KOKORO_SAMPLE_RATE", _KOKORO_SAMPLE_RATE) or _KOKORO_SAMPLE_RATE)
        return rate, CHANNELS, _KOKORO_DTYPE
    return SAMPLE_RATE, CHANNELS, DTYPE  # cartesia / default

# ------------------------------------------------------------------
# Singleton Cartesia WebSocket connection
# ------------------------------------------------------------------
_cartesia_client = None
_cartesia_ws_manager = None   # the context manager returned by websocket_connect()
_cartesia_ws = None           # the entered connection object (has .context())
_cartesia_ws_lock = threading.Lock()
_kokoro_pipeline = None
_kokoro_pipeline_lang = ""
_kokoro_pipeline_device = ""
_kokoro_lock = threading.RLock()
_KOKORO_LOCK_TIMEOUT_SECONDS = 15.0


def _kokoro_diag(message: str) -> None:
    """Log Kokoro diagnostics to stderr and a repo/run-local breadcrumb file."""
    line = f"[tts] {message}"
    print(line, flush=True)
    try:
        root = os.environ.get("WISP_RUN_LOG_DIR")
        path = Path(root) / "kokoro-debug.log" if root else Path(config.BASE_DIR) / "build_logs" / "kokoro-debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except Exception:
        pass


def _get_cartesia_ws():
    """Return cartesia ws."""
    global _cartesia_client, _cartesia_ws_manager, _cartesia_ws
    with _cartesia_ws_lock:
        if _cartesia_ws is None:
            sdk_clients.install_proxy_guard()
            from cartesia import Cartesia  # type: ignore
            # Building the client creates an SSL context (Security framework on
            # macOS) — serialize against concurrent LLM-client construction.
            with ssl_init_lock():
                _cartesia_client = Cartesia(api_key=config.CARTESIA_API_KEY)
                _cartesia_ws_manager = _cartesia_client.tts.websocket_connect()
                _cartesia_ws = _cartesia_ws_manager.__enter__()
        return _cartesia_ws


def _reset_cartesia_ws() -> None:
    """Discard the current WebSocket so the next call to _get_cartesia_ws() reconnects."""
    global _cartesia_ws, _cartesia_ws_manager
    with _cartesia_ws_lock:
        if _cartesia_ws_manager is not None:
            try:
                _cartesia_ws_manager.__exit__(None, None, None)
            except Exception:
                pass
        _cartesia_ws = None
        _cartesia_ws_manager = None


def reset_connections() -> None:
    """Discard cached TTS connections so new provider/key/voice settings apply."""
    _reset_cartesia_ws()
    _reset_kokoro_pipeline()


def prewarm():
    """
    Open connections eagerly at app startup so the first user query is fast.
    Call once after the event loop starts.
    """
    if not macos_safety.tts_prewarm_enabled():
        print("[tts] prewarm skipped in macOS safe mode.")
        return
    if config.TTS_PROVIDER.lower() == "cartesia":
        _get_cartesia_ws()
    elif config.TTS_PROVIDER.lower() == "kokoro":
        for chunk in _stream_kokoro("ok"):
            if chunk:
                break


def close():
    """Close the persistent WebSocket (call on app exit)."""
    global _cartesia_ws, _cartesia_ws_manager
    if _cartesia_ws_manager is not None:
        try:
            _cartesia_ws_manager.__exit__(None, None, None)
        except Exception:
            pass
        _cartesia_ws = None
        _cartesia_ws_manager = None


def stream_audio(text: str) -> Generator[bytes, None, None]:
    """
    Stream TTS audio bytes for the given complete text string.

    Yields:
        Raw PCM float32 audio bytes (mono, SAMPLE_RATE Hz).
    """
    yield from stream_audio_from_chunks(iter([text]))


def stream_audio_from_chunks(text_chunks: Iterable[str],
                             on_word_timestamps=None) -> Generator[bytes, None, None]:
    """
    Stream TTS by feeding text pieces incrementally (e.g. from an LLM stream).
    Audio starts arriving before all text is available.

    Args:
        on_word_timestamps: Optional callback(words: list[str], start_ms: list[int])
                            called whenever Cartesia returns a timestamps event.

    Yields:
        Raw PCM float32 audio bytes.
    """
    if not macos_safety.audio_enabled():
        for _ in text_chunks:
            pass
        return
    provider = config.TTS_PROVIDER.lower()
    if provider == "cartesia":
        yield from _stream_cartesia(text_chunks, on_word_timestamps)
    elif provider == "elevenlabs":
        # ElevenLabs doesn't support incremental push; collect then stream
        yield from _stream_elevenlabs("".join(text_chunks))
    elif provider == "openai":
        # HTTP synthesis: collect the text then stream the PCM response.
        yield from _stream_openai("".join(text_chunks))
    elif provider == "openai_compatible":
        yield from _stream_openai(
            "".join(text_chunks),
            base_url=config.TTS_CUSTOM_BASE_URL,
            api_key=config.TTS_CUSTOM_API_KEY,
            model=config.TTS_CUSTOM_MODEL,
            voice=config.TTS_CUSTOM_VOICE,
        )
    elif provider == "gpt_sovits":
        yield from _stream_gpt_sovits("".join(text_chunks))
    elif provider == "kokoro":
        yield from _stream_kokoro("".join(text_chunks))
    elif provider == "none":
        # Drain the iterator so the caller's on_done fires only after LLM finishes.
        for _ in text_chunks:
            pass
        return
    else:
        raise ValueError(f"Unknown TTS provider: {provider}")


# ------------------------------------------------------------------
# Cartesia 3.x  (WebSocket, pcm_f32le, sonic-3)
# ------------------------------------------------------------------

def _stream_cartesia(text_chunks: Iterable[str],
                     on_word_timestamps=None) -> Generator[bytes, None, None]:
    """Stream cartesia."""
    try:
        ws = _get_cartesia_ws()

        ctx = ws.context(
            model_id="sonic-3",
            voice={"mode": "id", "id": config.CARTESIA_VOICE_ID},
            output_format={
                "container": "raw",
                "encoding": "pcm_f32le",
                "sample_rate": SAMPLE_RATE,
            },
            language="en",
            add_timestamps=True,
        )

        for piece in text_chunks:
            if piece:
                ctx.push(piece)
        ctx.no_more_inputs()

        for response in ctx.receive():
            if response.type == "chunk" and response.audio:
                yield response.audio
            elif response.type == "timestamps" and on_word_timestamps:
                wt = response.word_timestamps
                if wt and wt.words:
                    start_ms = [int(t * 1000) for t in wt.start]
                    on_word_timestamps(wt.words, start_ms)
    except Exception:
        # Reset the dead connection so the next call reconnects cleanly.
        _reset_cartesia_ws()
        raise


# ------------------------------------------------------------------
# ElevenLabs
# ------------------------------------------------------------------

def _stream_elevenlabs(text: str) -> Generator[bytes, None, None]:
    """Stream elevenlabs."""
    try:
        sdk_clients.install_proxy_guard()
        from elevenlabs.client import ElevenLabs  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ElevenLabs support is not installed in this build. Enable Windows long paths and reinstall dependencies to bundle it."
        ) from exc

    with ssl_init_lock():
        client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    # voice id is optional: blank falls back to the library's default voice.
    kwargs = {
        "text": text,
        "stream": True,
        "output_format": "pcm_22050",
        "model": config.ELEVENLABS_MODEL or "eleven_turbo_v2_5",
    }
    if config.ELEVENLABS_VOICE_ID:
        kwargs["voice"] = config.ELEVENLABS_VOICE_ID
    audio_stream = client.generate(**kwargs)
    for chunk in audio_stream:
        if chunk:
            yield chunk


# ------------------------------------------------------------------
# Kokoro local library
# ------------------------------------------------------------------

def _reset_kokoro_pipeline() -> None:
    """Discard the cached Kokoro pipeline so language changes apply."""
    global _kokoro_pipeline, _kokoro_pipeline_lang, _kokoro_pipeline_device
    if not _kokoro_lock.acquire(timeout=1.0):
        return
    try:
        _kokoro_pipeline = None
        _kokoro_pipeline_lang = ""
        _kokoro_pipeline_device = ""
    finally:
        _kokoro_lock.release()


def _resolve_kokoro_device(requested: str | None = None) -> str:
    """Resolve Kokoro's requested backend to a concrete device string."""
    requested = (requested or getattr(config, "KOKORO_DEVICE", "auto") or "auto").strip().lower()
    _kokoro_diag(f"Kokoro device resolve starting requested={requested!r}")
    if requested == "cpu":
        _kokoro_diag("Kokoro device requested='cpu'; resolved='cpu'")
        return "cpu"
    torch_version = "not installed"
    torch_cuda = ""
    cuda_name = ""
    try:
        import torch  # type: ignore

        torch_version = str(getattr(torch, "__version__", "unknown"))
        torch_cuda = str(getattr(getattr(torch, "version", None), "cuda", "") or "")
        has_cuda = bool(torch.cuda.is_available())
        if has_cuda:
            try:
                cuda_name = str(torch.cuda.get_device_name(0))
            except Exception:
                cuda_name = "unknown"
    except Exception as exc:
        torch_version = f"unavailable ({type(exc).__name__}: {exc})"
        has_cuda = False
    if has_cuda:
        _kokoro_diag(
            "Kokoro device "
            f"requested={requested!r}; torch={torch_version}; torch_cuda={torch_cuda or 'unknown'}; "
            f"cuda_available=True; cuda_device={cuda_name or 'unknown'}; resolved='cuda'"
        )
        return "cuda"
    if requested == "cuda":
        _kokoro_diag(
            "Kokoro device requested='cuda' but Torch CUDA is unavailable; "
            f"torch={torch_version}; torch_cuda={torch_cuda or 'none'}; resolved='cpu'"
        )
    else:
        _kokoro_diag(
            "Kokoro device "
            f"requested={requested!r}; torch={torch_version}; torch_cuda={torch_cuda or 'none'}; "
            "cuda_available=False; resolved='cpu'"
        )
    return "cpu"


def _build_kokoro_pipeline(KPipeline, *, lang_code: str, device: str):
    """Construct KPipeline, returning the pipeline and effective device."""
    kwargs = {"lang_code": lang_code}
    try:
        accepts_device = "device" in inspect.signature(KPipeline).parameters
    except (TypeError, ValueError):
        accepts_device = False
    if accepts_device:
        kwargs["device"] = device
    _kokoro_diag(
        f"Building Kokoro pipeline lang={lang_code!r} requested_device={device!r} "
        f"passes_device={accepts_device}"
    )
    try:
        pipeline = KPipeline(**kwargs)
    except TypeError:
        if "device" not in kwargs:
            raise
        kwargs.pop("device")
        pipeline = KPipeline(**kwargs)
        _kokoro_diag("Installed Kokoro does not accept device=; using its default backend.")
        return pipeline, "default"
    except RuntimeError as exc:
        if kwargs.get("device") not in ("cuda", "mps"):
            raise
        fallback = dict(kwargs)
        fallback["device"] = "cpu"
        _kokoro_diag(f"Kokoro failed on {kwargs['device']}: {exc}; falling back to CPU.")
        return KPipeline(**fallback), "cpu"
    effective_device = kwargs.get("device", "default")
    _kokoro_diag(f"Kokoro pipeline ready on device={effective_device!r}")
    return pipeline, effective_device


def _get_kokoro_pipeline():
    """Return a cached Kokoro pipeline for the configured language code."""
    global _kokoro_pipeline, _kokoro_pipeline_lang, _kokoro_pipeline_device
    lang_code = (getattr(config, "KOKORO_LANG_CODE", "a") or "a").strip()
    device = _resolve_kokoro_device()
    if not _kokoro_lock.acquire(timeout=_KOKORO_LOCK_TIMEOUT_SECONDS):
        raise RuntimeError("Kokoro is still warming up. Try again when local speech is ready.")
    try:
        if (
            _kokoro_pipeline is None
            or _kokoro_pipeline_lang != lang_code
            or _kokoro_pipeline_device != device
        ):
            try:
                _kokoro_diag("Importing kokoro.KPipeline")
                from kokoro import KPipeline  # type: ignore
                _kokoro_diag("Imported kokoro.KPipeline")
            except ImportError as exc:
                raise RuntimeError(
                    "Kokoro support is not installed. Run: python -m pip install kokoro>=0.9.4 soundfile"
                ) from exc
            _kokoro_pipeline, device = _build_kokoro_pipeline(KPipeline, lang_code=lang_code, device=device)
            _kokoro_pipeline_lang = lang_code
            _kokoro_pipeline_device = device
        return _kokoro_pipeline
    finally:
        _kokoro_lock.release()


def _float_audio_to_pcm16(audio, *, source_rate: int, target_rate: int) -> bytes:
    """Convert a Kokoro audio array/tensor to mono signed 16-bit PCM."""
    import numpy as np

    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    samples = np.asarray(audio)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.dtype == np.int16:
        pcm = samples
    else:
        samples = samples.astype(np.float32)
        if samples.size >= 2 and source_rate != target_rate:
            out_len = max(1, int(samples.size * target_rate / source_rate))
            src_x = np.arange(samples.size, dtype=np.float32)
            dst_x = np.linspace(0, samples.size - 1, out_len, dtype=np.float32)
            samples = np.interp(dst_x, src_x, samples).astype(np.float32)
        pcm = np.clip(samples, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
    return pcm.tobytes()


def _stream_kokoro(text: str) -> Generator[bytes, None, None]:
    """Synthesize speech with the local Kokoro Python package."""
    if not text.strip():
        return
    if not _kokoro_lock.acquire(timeout=_KOKORO_LOCK_TIMEOUT_SECONDS):
        raise RuntimeError("Kokoro is still warming up. Try again when local speech is ready.")
    try:
        pipeline = _get_kokoro_pipeline()
        voice = (getattr(config, "KOKORO_VOICE", "af_heart") or "af_heart").strip()
        speed = float(getattr(config, "KOKORO_SPEED", 1.0) or 1.0)
        split_pattern = getattr(config, "KOKORO_SPLIT_PATTERN", r"\n+")
        target_rate = int(getattr(config, "KOKORO_SAMPLE_RATE", _KOKORO_SAMPLE_RATE) or _KOKORO_SAMPLE_RATE)
        _kokoro_diag(f"Kokoro synthesis starting text_chars={len(text)} voice={voice!r} speed={speed}")
        generator = pipeline(text, voice=voice, speed=speed, split_pattern=split_pattern)
        saw_audio = False
        for _graphemes, _phonemes, audio in generator:
            if not saw_audio:
                _kokoro_diag("Kokoro synthesis yielded first audio chunk")
                saw_audio = True
            chunk = _float_audio_to_pcm16(audio, source_rate=_KOKORO_SAMPLE_RATE, target_rate=target_rate)
            if chunk:
                yield chunk
    finally:
        _kokoro_lock.release()


# ------------------------------------------------------------------
# GPT-SoVITS local API
# ------------------------------------------------------------------

def _gpt_sovits_tts_url(base_url: str) -> str:
    """Return the GPT-SoVITS /tts endpoint URL from a base URL or full route."""
    url = (base_url or "http://127.0.0.1:9880").strip().rstrip("/")
    if url.endswith("/tts"):
        return url
    return f"{url}/tts"


def _wav_bytes_to_pcm16(wav_bytes: bytes, *, target_rate: int) -> bytes:
    """Decode mono 16-bit WAV bytes to PCM16, resampling if needed."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if sample_width != 2:
        raise RuntimeError(f"GPT-SoVITS returned {sample_width * 8}-bit WAV; expected 16-bit PCM.")
    if channels != 1:
        import audioop

        frames = audioop.tomono(frames, sample_width, 0.5, 0.5)
    if sample_rate == target_rate:
        return frames

    import numpy as np

    samples = np.frombuffer(frames, dtype=np.int16)
    if samples.size < 2:
        return frames
    out_len = max(1, int(samples.size * target_rate / sample_rate))
    src_x = np.arange(samples.size, dtype=np.float32)
    dst_x = np.linspace(0, samples.size - 1, out_len, dtype=np.float32)
    resampled = np.interp(dst_x, src_x, samples.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def _stream_gpt_sovits(text: str) -> Generator[bytes, None, None]:
    """Synthesize via a local GPT-SoVITS api_v2.py server."""
    if not text.strip():
        return
    import requests

    ref_audio_path = getattr(config, "GPT_SOVITS_REF_AUDIO_PATH", "")
    prompt_lang = getattr(config, "GPT_SOVITS_PROMPT_LANG", "en")
    text_lang = getattr(config, "GPT_SOVITS_TEXT_LANG", "en")
    target_rate = int(getattr(config, "GPT_SOVITS_SAMPLE_RATE", _GPT_SOVITS_SAMPLE_RATE) or _GPT_SOVITS_SAMPLE_RATE)
    if not ref_audio_path:
        raise ValueError("GPT_SOVITS_REF_AUDIO_PATH is not configured.")
    if not prompt_lang:
        raise ValueError("GPT_SOVITS_PROMPT_LANG is not configured.")
    if not text_lang:
        raise ValueError("GPT_SOVITS_TEXT_LANG is not configured.")

    payload = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": ref_audio_path,
        "prompt_text": getattr(config, "GPT_SOVITS_PROMPT_TEXT", ""),
        "prompt_lang": prompt_lang,
        "text_split_method": getattr(config, "GPT_SOVITS_TEXT_SPLIT_METHOD", "cut5"),
        "batch_size": int(getattr(config, "GPT_SOVITS_BATCH_SIZE", 1) or 1),
        "speed_factor": float(getattr(config, "GPT_SOVITS_SPEED_FACTOR", 1.0) or 1.0),
        "seed": int(getattr(config, "GPT_SOVITS_SEED", -1) or -1),
        "media_type": "wav",
        "streaming_mode": False,
    }
    response = requests.post(
        _gpt_sovits_tts_url(getattr(config, "GPT_SOVITS_URL", "")),
        json=payload,
        timeout=float(getattr(config, "GPT_SOVITS_TIMEOUT_SECONDS", 120) or 120),
    )
    if response.status_code >= 400:
        message = response.text.strip()
        raise RuntimeError(f"GPT-SoVITS returned HTTP {response.status_code}: {message[:500]}")
    yield _wav_bytes_to_pcm16(response.content, target_rate=target_rate)


# ------------------------------------------------------------------
# OpenAI TTS  /  OpenAI-compatible endpoints
# ------------------------------------------------------------------

def _stream_openai(text: str, *, base_url: str | None = None,
                   api_key: str | None = None, model: str | None = None,
                   voice: str | None = None) -> Generator[bytes, None, None]:
    """Stream raw PCM from OpenAI's (or a compatible server's) /audio/speech.

    `base_url` selects a self-hosted / third-party endpoint; left None it hits
    OpenAI directly with OPENAI_API_KEY. response_format=pcm returns signed
    16-bit mono — 24 kHz from OpenAI, server-defined elsewhere (see
    playback_format)."""
    if not text.strip():
        return
    sdk_clients.install_proxy_guard()
    from openai import OpenAI  # type: ignore

    resolved_key = api_key or config.OPENAI_API_KEY
    resolved_model = model or config.OPENAI_TTS_MODEL or "gpt-4o-mini-tts"
    resolved_voice = voice or config.OPENAI_TTS_VOICE or "alloy"
    with ssl_init_lock():
        client = OpenAI(
            api_key=resolved_key or "sk-none",  # compatible servers ignore the key
            base_url=base_url or None,
        )
    with client.audio.speech.with_streaming_response.create(
        model=resolved_model,
        voice=resolved_voice,
        input=text,
        response_format="pcm",
    ) as response:
        for chunk in response.iter_bytes():
            if chunk:
                yield chunk


def test_connection(
    provider: str | None = None,
    *,
    cartesia_api_key: str | None = None,
    cartesia_voice_id: str | None = None,
    elevenlabs_api_key: str | None = None,
    openai_api_key: str | None = None,
    openai_voice: str | None = None,
    openai_model: str | None = None,
    custom_base_url: str | None = None,
    custom_api_key: str | None = None,
    custom_voice: str | None = None,
    custom_model: str | None = None,
    gpt_sovits_url: str | None = None,
    gpt_sovits_ref_audio_path: str | None = None,
    gpt_sovits_prompt_text: str | None = None,
    gpt_sovits_prompt_lang: str | None = None,
    gpt_sovits_text_lang: str | None = None,
    kokoro_voice: str | None = None,
    kokoro_lang_code: str | None = None,
    kokoro_device: str | None = None,
) -> tuple[bool, str]:
    """Verify connection behavior."""
    provider = (provider or config.TTS_PROVIDER).lower().strip()
    cartesia_api_key = config.CARTESIA_API_KEY if cartesia_api_key is None else cartesia_api_key
    cartesia_voice_id = config.CARTESIA_VOICE_ID if cartesia_voice_id is None else cartesia_voice_id
    elevenlabs_api_key = config.ELEVENLABS_API_KEY if elevenlabs_api_key is None else elevenlabs_api_key
    openai_api_key = config.OPENAI_API_KEY if openai_api_key is None else openai_api_key
    custom_base_url = config.TTS_CUSTOM_BASE_URL if custom_base_url is None else custom_base_url
    custom_api_key = config.TTS_CUSTOM_API_KEY if custom_api_key is None else custom_api_key
    gpt_sovits_url = config.GPT_SOVITS_URL if gpt_sovits_url is None else gpt_sovits_url
    gpt_sovits_ref_audio_path = (
        config.GPT_SOVITS_REF_AUDIO_PATH
        if gpt_sovits_ref_audio_path is None
        else gpt_sovits_ref_audio_path
    )
    gpt_sovits_prompt_text = (
        config.GPT_SOVITS_PROMPT_TEXT
        if gpt_sovits_prompt_text is None
        else gpt_sovits_prompt_text
    )
    gpt_sovits_prompt_lang = (
        config.GPT_SOVITS_PROMPT_LANG
        if gpt_sovits_prompt_lang is None
        else gpt_sovits_prompt_lang
    )
    gpt_sovits_text_lang = (
        config.GPT_SOVITS_TEXT_LANG
        if gpt_sovits_text_lang is None
        else gpt_sovits_text_lang
    )
    kokoro_voice = config.KOKORO_VOICE if kokoro_voice is None else kokoro_voice
    kokoro_lang_code = config.KOKORO_LANG_CODE if kokoro_lang_code is None else kokoro_lang_code
    kokoro_device = config.KOKORO_DEVICE if kokoro_device is None else kokoro_device
    try:
        if provider == "none":
            return True, "TTS is disabled (provider=none)."
        if provider == "cartesia":
            if not cartesia_api_key:
                raise ValueError("CARTESIA_API_KEY is not configured.")
            if not cartesia_voice_id:
                raise ValueError("CARTESIA_VOICE_ID is not configured.")
            got_audio = False
            sdk_clients.install_proxy_guard()
            from cartesia import Cartesia  # type: ignore

            with ssl_init_lock():
                client = Cartesia(api_key=cartesia_api_key)
                ws_manager = client.tts.websocket_connect()
                ws = ws_manager.__enter__()
            ctx = ws.context(
                model_id="sonic-3",
                voice={"mode": "id", "id": cartesia_voice_id},
                output_format={
                    "container": "raw",
                    "encoding": "pcm_f32le",
                    "sample_rate": SAMPLE_RATE,
                },
                language="en",
                add_timestamps=False,
            )
            ctx.push("ok")
            ctx.no_more_inputs()
            for response in ctx.receive():
                if response.type == "chunk" and response.audio:
                    got_audio = True
                    break
            try:
                ws_manager.__exit__(None, None, None)
            except Exception:
                pass
            if not got_audio:
                raise RuntimeError("Cartesia connected but returned no audio.")
            return True, "TTS route OK: cartesia"
        if provider == "elevenlabs":
            if not elevenlabs_api_key:
                raise ValueError("ELEVENLABS_API_KEY is not configured.")
            sdk_clients.install_proxy_guard()
            from elevenlabs.client import ElevenLabs  # type: ignore

            with ssl_init_lock():
                client = ElevenLabs(api_key=elevenlabs_api_key)
            audio_stream = client.generate(
                text="ok",
                stream=True,
                output_format="pcm_22050",
            )
            for chunk in audio_stream:
                if chunk:
                    return True, "TTS route OK: elevenlabs"
            raise RuntimeError("ElevenLabs connected but returned no audio.")
        if provider in ("openai", "openai_compatible"):
            if provider == "openai":
                if not openai_api_key:
                    raise ValueError("OPENAI_API_KEY is not configured.")
                base_url = None
                api_key = openai_api_key
                model = openai_model
                voice = openai_voice
            else:
                if not custom_base_url:
                    raise ValueError("TTS_CUSTOM_BASE_URL is not configured.")
                base_url = custom_base_url
                api_key = custom_api_key
                model = custom_model
                voice = custom_voice
            for chunk in _stream_openai(
                "ok", base_url=base_url, api_key=api_key, model=model, voice=voice
            ):
                if chunk:
                    return True, f"TTS route OK: {provider}"
            raise RuntimeError(f"{provider} connected but returned no audio.")
        if provider == "gpt_sovits":
            if not gpt_sovits_url:
                raise ValueError("GPT_SOVITS_URL is not configured.")
            if not gpt_sovits_ref_audio_path:
                raise ValueError("GPT_SOVITS_REF_AUDIO_PATH is not configured.")
            old_values = (
                getattr(config, "GPT_SOVITS_URL", ""),
                getattr(config, "GPT_SOVITS_REF_AUDIO_PATH", ""),
                getattr(config, "GPT_SOVITS_PROMPT_TEXT", ""),
                getattr(config, "GPT_SOVITS_PROMPT_LANG", "en"),
                getattr(config, "GPT_SOVITS_TEXT_LANG", "en"),
            )
            try:
                config.GPT_SOVITS_URL = gpt_sovits_url
                config.GPT_SOVITS_REF_AUDIO_PATH = gpt_sovits_ref_audio_path
                config.GPT_SOVITS_PROMPT_TEXT = gpt_sovits_prompt_text or ""
                config.GPT_SOVITS_PROMPT_LANG = gpt_sovits_prompt_lang or "en"
                config.GPT_SOVITS_TEXT_LANG = gpt_sovits_text_lang or "en"
                for chunk in _stream_gpt_sovits("ok"):
                    if chunk:
                        return True, "TTS route OK: gpt_sovits"
                raise RuntimeError("GPT-SoVITS connected but returned no audio.")
            finally:
                (
                    config.GPT_SOVITS_URL,
                    config.GPT_SOVITS_REF_AUDIO_PATH,
                    config.GPT_SOVITS_PROMPT_TEXT,
                    config.GPT_SOVITS_PROMPT_LANG,
                    config.GPT_SOVITS_TEXT_LANG,
                ) = old_values
        if provider == "kokoro":
            if not kokoro_voice:
                raise ValueError("KOKORO_VOICE is not configured.")
            if not kokoro_lang_code:
                raise ValueError("KOKORO_LANG_CODE is not configured.")
            old_values = (
                getattr(config, "KOKORO_VOICE", "af_heart"),
                getattr(config, "KOKORO_LANG_CODE", "a"),
                getattr(config, "KOKORO_DEVICE", "auto"),
            )
            try:
                config.KOKORO_VOICE = kokoro_voice
                config.KOKORO_LANG_CODE = kokoro_lang_code
                config.KOKORO_DEVICE = kokoro_device or "auto"
                for chunk in _stream_kokoro("ok"):
                    if chunk:
                        return True, "TTS route OK: kokoro"
                raise RuntimeError("Kokoro connected but returned no audio.")
            finally:
                config.KOKORO_VOICE, config.KOKORO_LANG_CODE, config.KOKORO_DEVICE = old_values
                _reset_kokoro_pipeline()
        raise ValueError(f"Unknown TTS provider: {provider}")
    except Exception as exc:
        if provider == "cartesia" and cartesia_api_key is None:
            _reset_cartesia_ws()
        return False, f"TTS test failed: {exc}"
