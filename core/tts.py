"""
core/tts.py — Streaming TTS client.

Supports:
  - Cartesia 3.x (~75ms TTFT) via WebSocket — pcm_f32le, 44100 Hz
  - ElevenLabs (~300-500ms TTFT)

The Cartesia WebSocket connection is kept alive as a singleton so every call
avoids the ~600ms handshake penalty. Call prewarm() at app startup.

Yields raw PCM audio bytes as they stream in, ready for sounddevice playback.

For Cartesia, also exposes stream_audio_from_chunks() which accepts an
iterator of text pieces (e.g. LLM stream chunks) so TTS starts before the
full LLM response is available — this is the lowest-latency path.
"""
from __future__ import annotations
import config
import threading
from typing import Generator, Iterable


SAMPLE_RATE = 44100   # Hz  (Cartesia / default)
CHANNELS = 1
DTYPE = "float32"     # pcm_f32le  (Cartesia / default)

# ElevenLabs uses a different output format
_EL_SAMPLE_RATE = 22050
_EL_DTYPE = "int16"

# ------------------------------------------------------------------
# Singleton Cartesia WebSocket connection
# ------------------------------------------------------------------
_cartesia_client = None
_cartesia_ws_manager = None   # the context manager returned by websocket_connect()
_cartesia_ws = None           # the entered connection object (has .context())
_cartesia_ws_lock = threading.Lock()


def _get_cartesia_ws():
    global _cartesia_client, _cartesia_ws_manager, _cartesia_ws
    with _cartesia_ws_lock:
        if _cartesia_ws is None:
            from cartesia import Cartesia  # type: ignore
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


def prewarm():
    """
    Open connections eagerly at app startup so the first user query is fast.
    Call once from main.py after the event loop starts.
    """
    if config.TTS_PROVIDER.lower() == "cartesia":
        _get_cartesia_ws()


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
    provider = config.TTS_PROVIDER.lower()
    if provider == "cartesia":
        yield from _stream_cartesia(text_chunks, on_word_timestamps)
    elif provider == "elevenlabs":
        # ElevenLabs doesn't support incremental push; collect then stream
        yield from _stream_elevenlabs("".join(text_chunks))
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
    from elevenlabs.client import ElevenLabs  # type: ignore

    client = ElevenLabs(api_key=config.ELEVENLABS_API_KEY)

    audio_stream = client.generate(
        text=text,
        stream=True,
        output_format="pcm_22050",
    )
    for chunk in audio_stream:
        if chunk:
            yield chunk


def test_connection(
    provider: str | None = None,
    *,
    cartesia_api_key: str | None = None,
    cartesia_voice_id: str | None = None,
    elevenlabs_api_key: str | None = None,
) -> tuple[bool, str]:
    provider = (provider or config.TTS_PROVIDER).lower().strip()
    cartesia_api_key = config.CARTESIA_API_KEY if cartesia_api_key is None else cartesia_api_key
    cartesia_voice_id = config.CARTESIA_VOICE_ID if cartesia_voice_id is None else cartesia_voice_id
    elevenlabs_api_key = config.ELEVENLABS_API_KEY if elevenlabs_api_key is None else elevenlabs_api_key
    try:
        if provider == "none":
            return True, "TTS is disabled (provider=none)."
        if provider == "cartesia":
            if not cartesia_api_key:
                raise ValueError("CARTESIA_API_KEY is not configured.")
            if not cartesia_voice_id:
                raise ValueError("CARTESIA_VOICE_ID is not configured.")
            got_audio = False
            from cartesia import Cartesia  # type: ignore

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
            from elevenlabs.client import ElevenLabs  # type: ignore

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
        raise ValueError(f"Unknown TTS provider: {provider}")
    except Exception as exc:
        if provider == "cartesia" and cartesia_api_key is None:
            _reset_cartesia_ws()
        return False, f"TTS test failed: {exc}"
