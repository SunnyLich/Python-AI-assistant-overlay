"""macOS platform-logic tests.

These stub out pyobjc (Quartz/AppKit) and force sys.platform to "darwin", so the
macOS branches in core.platform_utils can be exercised on any host OS (including
the Windows dev box and the macOS CI runner). They verify our wiring, not pyobjc
itself — the GitHub Actions macOS job exercises the real pyobjc calls.
"""
import importlib
import sys
import unittest
from unittest.mock import patch


def _fake_quartz(windows):
    mod = type(sys)("Quartz")
    mod.kCGWindowListOptionOnScreenOnly = 1
    mod.kCGWindowListExcludeDesktopElements = 16
    mod.kCGNullWindowID = 0
    mod.CGWindowListCopyWindowInfo = lambda opts, rel: list(windows)
    return mod


def _fake_appkit(front_pid, activated):
    mod = type(sys)("AppKit")
    mod.NSApplicationActivateIgnoringOtherApps = 0

    class _App:
        def processIdentifier(self):
            return front_pid

    class _Workspace:
        def frontmostApplication(self):
            return _App() if front_pid is not None else None

    class NSWorkspace:
        @staticmethod
        def sharedWorkspace():
            return _Workspace()

    class _Running:
        def __init__(self, pid):
            self._pid = pid

        def activateWithOptions_(self, _opts):
            activated.append(self._pid)

    class NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            return _Running(pid)

    mod.NSWorkspace = NSWorkspace
    mod.NSRunningApplication = NSRunningApplication
    return mod


class MacPlatformTests(unittest.TestCase):
    def setUp(self):
        # Reload platform_utils under a faked darwin platform so its module-level
        # IS_MAC / COPY_COMBO constants take the macOS values.
        self._platform_patch = patch.object(sys, "platform", "darwin")
        self._platform_patch.start()
        import core.platform_utils as pu
        self.pu = importlib.reload(pu)

    def tearDown(self):
        self._platform_patch.stop()
        import core.platform_utils as pu
        importlib.reload(pu)  # restore real-platform constants for other tests

    def test_clipboard_combos_use_cmd_on_mac(self):
        self.assertTrue(self.pu.IS_MAC)
        self.assertEqual(self.pu.COPY_COMBO, "cmd+c")
        self.assertEqual(self.pu.PASTE_COMBO, "cmd+v")

    def test_send_keys_uses_out_of_process_helper_first(self):
        from core.platform import macos_native

        calls: list[str] = []
        with patch.object(macos_native, "send_key_combo", side_effect=lambda combo: calls.append(combo) or True), \
             patch.dict(sys.modules, {"Quartz": None}):
            self.pu.send_keys("cmd+c")
        self.assertEqual(calls, ["cmd+c"])

    def test_get_foreground_window_returns_frontmost_layer0_window(self):
        windows = [
            {"kCGWindowNumber": 7, "kCGWindowOwnerPID": 999, "kCGWindowLayer": 25},  # menu bar
            {"kCGWindowNumber": 42, "kCGWindowOwnerPID": 1234, "kCGWindowLayer": 0,
             "kCGWindowName": "Doc.txt", "kCGWindowOwnerName": "TextEdit"},
        ]
        with patch.dict(sys.modules, {"Quartz": _fake_quartz(windows),
                                      "AppKit": _fake_appkit(1234, [])}):
            self.assertEqual(self.pu.get_foreground_window(), 42)

    def test_window_title_and_pid_lookup(self):
        windows = [
            {"kCGWindowNumber": 42, "kCGWindowOwnerPID": 1234, "kCGWindowLayer": 0,
             "kCGWindowName": "Doc.txt", "kCGWindowOwnerName": "TextEdit"},
        ]
        with patch.dict(sys.modules, {"Quartz": _fake_quartz(windows),
                                      "AppKit": _fake_appkit(1234, [])}):
            self.assertEqual(self.pu.get_window_title(42), "Doc.txt")
            self.assertEqual(self.pu.get_window_pid(42), 1234)

    def test_window_title_falls_back_to_owner_without_screen_recording(self):
        # kCGWindowName is absent when Screen Recording permission is not granted.
        windows = [
            {"kCGWindowNumber": 42, "kCGWindowOwnerPID": 1234, "kCGWindowLayer": 0,
             "kCGWindowOwnerName": "TextEdit"},
        ]
        with patch.dict(sys.modules, {"Quartz": _fake_quartz(windows),
                                      "AppKit": _fake_appkit(1234, [])}):
            self.assertEqual(self.pu.get_window_title(42), "TextEdit")

    def test_set_foreground_window_activates_owning_app(self):
        windows = [
            {"kCGWindowNumber": 42, "kCGWindowOwnerPID": 1234, "kCGWindowLayer": 0,
             "kCGWindowOwnerName": "TextEdit"},
        ]
        activated: list[int] = []
        with patch.dict(sys.modules, {"Quartz": _fake_quartz(windows),
                                      "AppKit": _fake_appkit(1234, activated)}):
            self.pu.set_foreground_window(42)
        self.assertEqual(activated, [1234])

    def test_window_helpers_degrade_when_pyobjc_missing(self):
        # No Quartz/AppKit in sys.modules and import fails -> safe defaults, no raise.
        with patch.dict(sys.modules, {"Quartz": None, "AppKit": None}):
            self.assertEqual(self.pu.get_foreground_window(), 0)
            self.assertEqual(self.pu.list_visible_windows(), [])
            self.assertEqual(self.pu.get_window_title(42), "")
            self.assertEqual(self.pu.get_window_pid(42), 0)


class MacHotkeyTests(unittest.TestCase):
    def setUp(self):
        self._platform_patch = patch.object(sys, "platform", "darwin")
        self._platform_patch.start()
        import core.hotkeys as hotkeys
        self.hotkeys = importlib.reload(hotkeys)

    def tearDown(self):
        self._platform_patch.stop()
        import core.hotkeys as hotkeys
        importlib.reload(hotkeys)

    def test_ctrl_hotkeys_gain_cmd_alias_on_mac(self):
        self.assertEqual(
            self.hotkeys._to_pynput_hotkeys("ctrl+shift+q"),
            ["<ctrl>+<shift>+q", "<cmd>+<shift>+q"],
        )

    def test_cmd_hotkeys_do_not_duplicate_aliases_on_mac(self):
        self.assertEqual(
            self.hotkeys._to_pynput_hotkeys("win+shift+q"),
            ["<cmd>+<shift>+q"],
        )

    def test_carbon_hotkey_parser_maps_modifiers_and_keycode(self):
        # cmdKey=0x100, shiftKey=0x200, controlKey=0x1000; 'q' is virtual keycode 12.
        self.assertEqual(
            self.hotkeys._parse_hotkey_carbon("ctrl+shift+q"),
            (0x1000 | 0x200, 12),
        )
        # 'cmd'/'win' both map to cmdKey; space is keycode 49.
        self.assertEqual(self.hotkeys._parse_hotkey_carbon("cmd+space"), (0x100, 49))

    def test_carbon_hotkey_parser_rejects_modifier_only(self):
        self.assertIsNone(self.hotkeys._parse_hotkey_carbon("ctrl+shift"))

    def test_carbon_hotkey_parser_rejects_bare_typing_keys(self):
        self.assertFalse(self.hotkeys.is_safe_global_hotkey("space"))
        self.assertFalse(self.hotkeys.is_safe_global_hotkey("s"))
        self.assertFalse(self.hotkeys.is_safe_global_hotkey("shift+space"))
        self.assertFalse(self.hotkeys.is_safe_global_hotkey("shift+s"))
        self.assertIsNone(self.hotkeys._parse_hotkey_carbon("space"))
        self.assertIsNone(self.hotkeys._parse_hotkey_carbon("s"))

    def test_function_keys_remain_valid_global_hotkeys(self):
        self.assertTrue(self.hotkeys.is_safe_global_hotkey("f9"))
        self.assertEqual(self.hotkeys._parse_hotkey_carbon("f9"), (0, 101))

    def test_carbon_hotkey_event_kind_constants_match_hitevents(self):
        self.assertEqual(self.hotkeys._kEventHotKeyPressed, 5)
        self.assertEqual(self.hotkeys._kEventHotKeyReleased, 6)

    def test_accessibility_check_uses_application_services_trust(self):
        class FakeAppServices:
            def __init__(self):
                self.AXIsProcessTrusted = lambda: True
                self.AXIsProcessTrusted.restype = None

        with patch("ctypes.util.find_library", return_value="ApplicationServices"), \
             patch("ctypes.cdll.LoadLibrary", return_value=FakeAppServices()):
            self.assertTrue(self.hotkeys._macos_accessibility_enabled())


if __name__ == "__main__":
    unittest.main()
