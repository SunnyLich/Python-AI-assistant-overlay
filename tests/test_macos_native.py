import types
import unittest
from unittest import mock

from core.platform import macos_native


class MacosNativeHelperTests(unittest.TestCase):
    def test_helpers_are_noops_off_macos(self):
        with mock.patch.object(macos_native, "IS_MAC", False):
            self.assertIsNone(macos_native.get_clipboard_text())
            self.assertFalse(macos_native.set_clipboard_text("x"))
            self.assertIsNone(macos_native.get_selected_text())
            self.assertFalse(macos_native.paste_text("x"))

    def test_get_clipboard_text_uses_pbpaste(self):
        result = types.SimpleNamespace(returncode=0, stdout="hello", stderr="")
        with mock.patch.object(macos_native, "IS_MAC", True), \
             mock.patch.object(macos_native.subprocess, "run", return_value=result) as run:
            self.assertEqual(macos_native.get_clipboard_text(), "hello")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/pbpaste"])

    def test_set_clipboard_text_uses_pbcopy(self):
        result = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(macos_native, "IS_MAC", True), \
             mock.patch.object(macos_native.subprocess, "run", return_value=result) as run:
            self.assertTrue(macos_native.set_clipboard_text("hello"))
        self.assertEqual(run.call_args.args[0], ["/usr/bin/pbcopy"])
        self.assertEqual(run.call_args.kwargs["input"], "hello")

    def test_get_selected_text_restores_clipboard(self):
        restored: list[str] = []
        with mock.patch.object(macos_native, "IS_MAC", True), \
             mock.patch.object(macos_native, "send_key_combo", return_value=True) as send, \
             mock.patch.object(macos_native, "get_clipboard_text", side_effect=["before", "after"]), \
             mock.patch.object(macos_native, "set_clipboard_text", side_effect=lambda text: restored.append(text) or True), \
             mock.patch.object(macos_native.time, "sleep"):
            self.assertEqual(macos_native.get_selected_text("cmd+c"), "after")
        send.assert_called_once_with("cmd+c")
        self.assertEqual(restored, ["before"])

    def test_paste_text_sets_clipboard_then_sends_combo(self):
        with mock.patch.object(macos_native, "IS_MAC", True), \
             mock.patch.object(macos_native, "set_clipboard_text", return_value=True) as set_clip, \
             mock.patch.object(macos_native, "send_key_combo", return_value=True) as send, \
             mock.patch.object(macos_native.time, "sleep"):
            self.assertTrue(macos_native.paste_text("hello", "cmd+v"))
        set_clip.assert_called_once_with("hello")
        send.assert_called_once_with("cmd+v")

    def test_list_document_windows_parses_jxa_json(self):
        result = types.SimpleNamespace(
            returncode=0,
            stdout='[{"process_name":"TextEdit","pid":123,"frontmost":true,"title":"Notes.txt - TextEdit"}]',
            stderr="",
        )
        with mock.patch.object(macos_native, "IS_MAC", True), \
             mock.patch.object(macos_native.subprocess, "run", return_value=result) as run:
            rows = macos_native.list_document_windows()
        self.assertEqual(
            rows,
            [{"process_name": "TextEdit", "pid": 123, "frontmost": True, "title": "Notes.txt - TextEdit"}],
        )
        self.assertEqual(run.call_args.args[0][:3], ["/usr/bin/osascript", "-l", "JavaScript"])


if __name__ == "__main__":
    unittest.main()
