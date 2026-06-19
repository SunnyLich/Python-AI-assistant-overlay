"""Windows hotkey backend regression tests."""
from __future__ import annotations

import pytest


def test_win32_stop_waits_for_message_pump_to_unregister(monkeypatch):
    """Verify stop waits for the pump thread so replacement keybinds can register."""
    import core.hotkeys as hotkeys

    if not hasattr(hotkeys, "_Win32Impl"):
        pytest.skip("Win32 hotkey backend is only defined on Windows")

    post_calls: list[tuple[int, int, int, int]] = []
    join_calls: list[float | None] = []

    class FakeUser32:
        def PostThreadMessageW(self, tid, msg, wparam, lparam):
            post_calls.append((tid, msg, wparam, lparam))
            return 1

    class FakeThread:
        def __init__(self) -> None:
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout=None) -> None:
            join_calls.append(timeout)
            self._alive = False

    impl = hotkeys._Win32Impl([])
    impl._pump_tid = 123
    impl._pump_thread = FakeThread()
    monkeypatch.setattr(hotkeys, "_user32", FakeUser32())

    impl.stop()

    assert post_calls == [(123, hotkeys.WM_QUIT, 0, 0)]
    assert join_calls == [2.0]
    assert impl._pump_tid == 0
    assert impl._pump_thread is None
