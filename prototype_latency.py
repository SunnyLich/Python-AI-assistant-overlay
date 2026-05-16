"""
prototype_latency.py — First-build latency loop test.

Tests the critical path: capture → LLM stream → TTS stream → audio out.
No UI. Measures real-world numbers so you know if the product premise holds
before investing in the doll UI.

Usage:
    python prototype_latency.py --text "What is a kernel?"
    python prototype_latency.py --screenshot   # grabs primary monitor
"""
import argparse
import time
import sys
import os

# Make sure imports resolve from project root
sys.path.insert(0, os.path.dirname(__file__))

import config
from core import llm, tts, audio


def run_latency_test(user_text: str, image_base64: str | None = None):
    print(f"\n{'='*60}")
    print(f"  LLM provider : {config.LLM_PROVIDER} / {config.LLM_MODEL}")
    print(f"  TTS provider : {config.TTS_PROVIDER}")
    print(f"  Input        : {user_text[:80]!r}{'...' if len(user_text) > 80 else ''}")
    print(f"{'='*60}\n")

    t_start = time.perf_counter()

    # 1. Filler audio — plays immediately, non-blocking
    print("[0] Playing filler audio (non-blocking)…")
    audio.play_filler()
    t_filler = time.perf_counter()
    print(f"    filler dispatched in {(t_filler - t_start)*1000:.0f} ms")

    # 2. LLM + TTS pipelined: feed LLM chunks directly into TTS as they arrive
    print("\n[1+2] LLM → TTS pipelined…")
    import queue
    import threading

    full_text = ""
    t_llm_first = None
    t_llm_done = None
    t_tts_first_audio = None
    llm_chunk_q: queue.Queue[str | None] = queue.Queue()
    done_event = threading.Event()

    def llm_producer():
        nonlocal full_text, t_llm_first, t_llm_done
        try:
            for chunk in llm.stream_response(user_text, image_base64):
                if t_llm_first is None:
                    t_llm_first = time.perf_counter()
                    print(f"    ✓ LLM first token in {(t_llm_first - t_start)*1000:.0f} ms")
                full_text += chunk
                print(chunk, end="", flush=True)
                llm_chunk_q.put(chunk)
        finally:
            t_llm_done_local = time.perf_counter()
            llm_chunk_q.put(None)  # sentinel
            # store in outer scope via a mutable container trick
            nonlocal t_llm_done
            t_llm_done = t_llm_done_local

    def llm_chunk_iter():
        while True:
            chunk = llm_chunk_q.get()
            if chunk is None:
                return
            yield chunk

    def tts_consumer():
        nonlocal t_tts_first_audio
        if config.TTS_PROVIDER == "none":
            done_event.set()
            return
        import sounddevice as sd
        with sd.RawOutputStream(
            samplerate=tts.SAMPLE_RATE,
            channels=tts.CHANNELS,
            dtype=tts.DTYPE,
        ) as stream:
            for audio_chunk in tts.stream_audio_from_chunks(llm_chunk_iter()):
                if t_tts_first_audio is None:
                    t_tts_first_audio = time.perf_counter()
                    print(f"\n    ✓ TTS first audio in {(t_tts_first_audio - t_start)*1000:.0f} ms")
                stream.write(audio_chunk)
        done_event.set()

    t_prod = threading.Thread(target=llm_producer, daemon=True)
    t_cons = threading.Thread(target=tts_consumer, daemon=True)
    t_cons.start()
    t_prod.start()
    t_prod.join()
    print(f"\n    ✓ LLM full response in {(t_llm_done - t_start)*1000:.0f} ms")
    print(f"    Response: {full_text!r}")

    done_event.wait(timeout=30)
    t_tts_done = time.perf_counter()
    if config.TTS_PROVIDER != "none":
        print(f"    ✓ TTS playback done in {(t_tts_done - t_start)*1000:.0f} ms total")
    else:
        print("\n[2] TTS skipped (provider=none)")

    # 4. Summary
    total_ms = (t_tts_done - t_start) * 1000
    first_audio_ms = (t_tts_first_audio - t_start) * 1000 if t_tts_first_audio else None

    print(f"\n{'='*60}")
    print(f"  TTFT (first LLM token)   : {(t_llm_first - t_start)*1000:.0f} ms" if t_llm_first else "  TTFT: N/A")
    if first_audio_ms:
        print(f"  Time to first audio      : {first_audio_ms:.0f} ms")
        target = config.LATENCY_TARGET_MS
        ceiling = config.LATENCY_CEILING_MS
        status = "✓ UNDER TARGET" if first_audio_ms < target else (
            "⚠ ABOVE TARGET (under ceiling)" if first_audio_ms < ceiling else "✗ ABOVE CEILING — revisit stack"
        )
        print(f"  Status                   : {status}")
    print(f"  Total end-to-end         : {total_ms:.0f} ms")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Latency loop prototype")
    parser.add_argument("--text", default="What is a kernel in operating systems?",
                        help="Query text to send to LLM")
    parser.add_argument("--screenshot", action="store_true",
                        help="Capture primary monitor and send as vision input")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of back-to-back runs (use 3+ to see warm latency)")
    args = parser.parse_args()

    image_b64 = None
    if args.screenshot:
        from core.capture import get_screen_snippet, image_to_base64
        print("Capturing screenshot…")
        img = get_screen_snippet()
        image_b64 = image_to_base64(img)
        print(f"Screenshot captured ({img.size[0]}×{img.size[1]})")

    print("Pre-warming connections…")
    from core import tts as tts_module
    tts_module.prewarm()
    print("Ready.\n")

    run_latency_test(args.text, image_b64)
    for i in range(1, args.runs):
        print(f"\n--- Run {i+1}/{args.runs} ---")
        run_latency_test(args.text, image_b64)


if __name__ == "__main__":
    main()
