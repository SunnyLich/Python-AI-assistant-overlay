"""core/stt_device.py — resolve faster-whisper device/compute from settings.

Shared by the in-process path (``core.stt``) and the out-of-process worker path
(``core.macos_helper.handlers``) so GPU selection behaves identically in both.
Kept dependency-light: ``ctranslate2`` is imported lazily inside the resolver so
importing this module never pulls in the native stack.
"""
from __future__ import annotations

from typing import Callable

Log = Callable[[str], None]


def resolve_device(requested: str, log: Log = print) -> str:
    """Map the STT_DEVICE setting to a concrete faster-whisper device.

    ``cuda``/``auto`` only resolve to ``cuda`` when an NVIDIA/CUDA device is
    actually present; otherwise fall back to CPU so a GPU choice never breaks
    transcription on a machine without one.
    """
    requested = (requested or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"
    try:
        from ctranslate2 import get_cuda_device_count
        has_cuda = get_cuda_device_count() > 0
    except Exception:
        has_cuda = False
    if has_cuda:
        return "cuda"
    if requested == "cuda":
        log("STT_DEVICE=cuda requested but no CUDA GPU was found; using CPU.")
    return "cpu"


def resolve_compute_type(device: str, compute: str, log: Log = print) -> str:
    """float16 compute types only work on GPU; downgrade to int8 on CPU so an
    auto-fallback (cuda->cpu) with a float16 setting doesn't error on load."""
    if device == "cpu" and compute in ("float16", "int8_float16"):
        log(f"compute_type {compute!r} needs a GPU; using 'int8' on CPU.")
        return "int8"
    return compute


def _warmup_encode(model) -> None:
    """Force one encoder pass so a compute_type the GPU can't actually run fails
    here (at load) rather than later on the user's first real clip."""
    import numpy as np
    audio = (np.random.default_rng(0).standard_normal(16_000).astype("float32")) * 0.01
    segments, _info = model.transcribe(audio, beam_size=1, vad_filter=False)
    list(segments)


def build_model(WhisperModel, model_name: str, device: str, compute: str, log: Log = print):
    """Construct a WhisperModel and, on GPU, warm it up so the user's first clip
    is fast — not stuck paying CUDA kernel compilation.

    The warmup encode doubles as a self-heal for the int8-on-GPU cuBLAS gap: some
    newer NVIDIA GPUs (e.g. Blackwell / RTX 50xx) raise
    ``CUBLAS_STATUS_NOT_SUPPORTED`` for int8 GEMM at *encode* time — which can't
    be detected at construction — so we catch it and rebuild with float16.
    Returns ``(model, effective_compute)``.
    """
    model = WhisperModel(model_name, device=device, compute_type=compute)
    if device != "cuda":
        return model, compute  # CPU has no kernel-warmup payoff; keep load cheap
    try:
        _warmup_encode(model)
    except Exception as exc:  # noqa: BLE001 — only swallow the known cuBLAS gap
        msg = str(exc).upper()
        if "int8" in compute and ("CUBLAS" in msg or "NOT_SUPPORTED" in msg):
            log(f"compute_type {compute!r} not supported on this GPU "
                f"({type(exc).__name__}); falling back to 'float16'.")
            model = WhisperModel(model_name, device=device, compute_type="float16")
            compute = "float16"
            try:
                _warmup_encode(model)  # warm the fallback model too
            except Exception:  # noqa: BLE001 — best effort; first clip just pays JIT
                pass
        else:
            raise
    return model, compute
