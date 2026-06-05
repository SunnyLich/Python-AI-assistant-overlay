"""Regression tests for the macOS SSL-context-construction race.

Background: on macOS, building an SDK client creates an SSL context, which reads
the system trust store through the Security framework. Doing that from two
threads at once segfaults. A single query already runs the LLM stream and the TTS
stream on separate threads, so two cold clients could build their SSL contexts
concurrently on the first request and crash (see core.system.native_locks).

The fix is a single process-wide lock (ssl_init_lock) shared across all SDK
client construction, plus per-provider caching so the build happens once. These
tests pin that contract. They fake sys.platform/darwin via native_locks._IS_MAC
and mock the SDK constructors, so they run on any host OS — no network, no keys,
no openai/cartesia/anthropic install required.

The *real* concurrent-construction repro (which actually segfaults without the
fix) lives in scripts/macos_testbot.py and must be run on a Mac.
"""
import contextlib
import sys
import threading
import time
import types
import unittest
from unittest import mock

import config
import core.system.native_locks as native_locks
from core.system.native_locks import keychain_lock, native_init_lock, ssl_init_lock
import core.tts as tts_module
import core.llm_clients.client as llm


class _ConcurrencyProbe:
    """Records the maximum number of threads simultaneously inside build()."""

    def __init__(self, hold: float = 0.02):
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.hold = hold

    def build(self):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.hold)  # widen the window for any overlap to show up
        with self._lock:
            self.active -= 1


def _run_concurrently(fn, n: int = 8) -> list[Exception]:
    """Start n threads that all call fn() at the same instant; return any errors."""
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker():
        try:
            barrier.wait()
            fn()
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class SslInitLockTests(unittest.TestCase):
    def test_lock_is_noop_off_macos(self):
        with mock.patch.object(native_locks, "_IS_MAC", False):
            self.assertIsInstance(ssl_init_lock(), contextlib.nullcontext)

    def test_returns_one_shared_lock_on_macos(self):
        with mock.patch.object(native_locks, "_IS_MAC", True):
            self.assertIs(ssl_init_lock(), native_locks._ssl_init_lock)
            self.assertIs(ssl_init_lock(), ssl_init_lock())
            self.assertIs(native_init_lock(), native_locks._ssl_init_lock)
            self.assertIs(keychain_lock(), native_locks._ssl_init_lock)

    def test_tts_and_llm_use_the_same_lock_function(self):
        # Both modules import the singleton helper by reference, so a build in
        # TTS and a build in the LLM client serialize against each other.
        self.assertIs(tts_module.ssl_init_lock, llm.ssl_init_lock)

    def test_lock_serializes_concurrent_builds_on_macos(self):
        probe = _ConcurrencyProbe()
        with mock.patch.object(native_locks, "_IS_MAC", True):
            def build():
                with ssl_init_lock():
                    probe.build()
            errors = _run_concurrently(build, n=8)
        self.assertEqual(errors, [])
        self.assertEqual(probe.max_active, 1)  # never two SSL builds at once

    def test_lock_allows_overlap_off_macos(self):
        # Sanity: off-macOS the no-op context must NOT serialize (otherwise the
        # macOS-only test above would be meaningless). nullcontext lets threads
        # overlap, so max_active should climb above 1.
        probe = _ConcurrencyProbe(hold=0.05)
        with mock.patch.object(native_locks, "_IS_MAC", False):
            def build():
                with ssl_init_lock():
                    probe.build()
            errors = _run_concurrently(build, n=8)
        self.assertEqual(errors, [])
        self.assertGreater(probe.max_active, 1)


class DynamicClientCachingTests(unittest.TestCase):
    def setUp(self):
        llm.reset_clients()
        self.addCleanup(llm.reset_clients)

    def test_openai_client_built_once_under_lock_then_cached(self):
        calls: list[str] = []

        def fake_build(provider):
            calls.append(provider)
            # On macOS the SSL lock must be held while the client is constructed.
            self.assertTrue(native_locks._ssl_init_lock.locked())
            return ("client", provider)

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.object(llm, "_build_dynamic_openai_client", fake_build):
            a = llm._dynamic_openai_client("openai")
            b = llm._dynamic_openai_client("openai")

        self.assertIs(a, b)
        self.assertEqual(calls, ["openai"])  # built once, served from cache after

    def test_openai_client_cached_per_provider(self):
        with mock.patch.object(llm, "_build_dynamic_openai_client",
                               side_effect=lambda p: ("client", p)) as build:
            groq = llm._dynamic_openai_client("groq")
            openai = llm._dynamic_openai_client("openai")
            groq_again = llm._dynamic_openai_client("groq")
        self.assertEqual(groq, ("client", "groq"))
        self.assertEqual(openai, ("client", "openai"))
        self.assertIs(groq, groq_again)
        self.assertEqual(build.call_count, 2)  # one per distinct provider

    def test_concurrent_first_use_builds_openai_client_once(self):
        # The real failure mode: many threads racing the very first build.
        build_count = []

        def fake_build(provider):
            build_count.append(provider)
            time.sleep(0.02)
            return object()

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.object(llm, "_build_dynamic_openai_client", fake_build):
            errors = _run_concurrently(lambda: llm._dynamic_openai_client("openai"), n=8)

        self.assertEqual(errors, [])
        self.assertEqual(len(build_count), 1)  # cache + lock => single build

    def test_anthropic_client_built_once_and_cached(self):
        created: list[dict] = []

        class FakeAnthropic:
            def __init__(self, **kwargs):
                created.append(kwargs)

        fake_mod = types.ModuleType("anthropic")
        fake_mod.Anthropic = FakeAnthropic

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.dict(sys.modules, {"anthropic": fake_mod}):
            a = llm._dynamic_anthropic_client()
            b = llm._dynamic_anthropic_client()

        self.assertIs(a, b)
        self.assertEqual(len(created), 1)

    def test_reset_clients_clears_dynamic_caches(self):
        llm._dynamic_openai_clients["openai"] = object()
        llm._dynamic_anthropic_client_cache = object()
        llm.reset_clients()
        self.assertEqual(llm._dynamic_openai_clients, {})
        self.assertIsNone(llm._dynamic_anthropic_client_cache)

    def test_stdlib_openai_compat_request_runs_under_native_lock(self):
        probe = self

        class _FakeResponse:
            def __init__(self):
                self.headers = {"Content-Encoding": ""}

            def read(self):
                probe.assertTrue(native_locks._ssl_init_lock.locked())
                return b'{"choices":[{"message":{"content":"ok"}}]}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeOpener:
            def open(self, _request, timeout=0):
                return _FakeResponse()

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.object(llm, "_openai_compat_base_url", return_value="https://example.test"), \
             mock.patch.object(llm, "_openai_compat_api_key", return_value="k"), \
             mock.patch("urllib.request.build_opener", return_value=_FakeOpener()):
            self.assertEqual(
                llm._openai_compat_stdlib_completion_text("google", {"model": "m", "messages": []}),
                "ok",
            )


class PrewarmTests(unittest.TestCase):
    def setUp(self):
        llm.reset_clients()
        self.addCleanup(llm.reset_clients)

    def test_prewarm_builds_openai_compat_provider(self):
        calls: list[str] = []
        with mock.patch.object(config, "LLM_PROVIDER", "groq"), \
             mock.patch.object(llm, "_dynamic_openai_client", lambda p: calls.append(p)):
            llm.prewarm()
        self.assertEqual(calls, ["groq"])

    def test_prewarm_builds_anthropic_provider(self):
        calls: list[str] = []
        with mock.patch.object(config, "LLM_PROVIDER", "anthropic"), \
             mock.patch.object(llm, "_dynamic_anthropic_client", lambda: calls.append("a")):
            llm.prewarm()
        self.assertEqual(calls, ["a"])

    def test_prewarm_skips_codex_providers(self):
        # chatgpt/copilot use the Codex transport; prewarm must not try to build
        # an OpenAI/Anthropic SSL client for them.
        oai, ant = [], []
        with mock.patch.object(config, "LLM_PROVIDER", "chatgpt"), \
             mock.patch.object(llm, "_dynamic_openai_client", lambda p: oai.append(p)), \
             mock.patch.object(llm, "_dynamic_anthropic_client", lambda: ant.append(1)):
            llm.prewarm()
        self.assertEqual((oai, ant), ([], []))

    def test_prewarm_is_best_effort_on_error(self):
        def boom(_provider):
            raise RuntimeError("no API key configured")
        with mock.patch.object(config, "LLM_PROVIDER", "openai"), \
             mock.patch.object(llm, "_dynamic_openai_client", boom):
            llm.prewarm()  # must swallow — a bad key cannot crash startup


if __name__ == "__main__":
    unittest.main()
