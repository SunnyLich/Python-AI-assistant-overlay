import importlib
import sys
import unittest
from unittest.mock import patch


class WindowsContextFetcherDocumentTests(unittest.TestCase):
    def setUp(self):
        self._platform_patch = patch.object(sys, "platform", "win32")
        self._platform_patch.start()
        import core.context_fetcher as context_fetcher
        self.cf = importlib.reload(context_fetcher)

    def tearDown(self):
        self._platform_patch.stop()
        import core.context_fetcher as context_fetcher
        importlib.reload(context_fetcher)

    def test_resolve_doc_path_matches_open_file_from_process(self):
        win = self.cf.WindowInfo(
            title="Notes.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\Notes.txt"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\Notes.txt")

    def test_resolve_doc_path_strips_modified_marker(self):
        win = self.cf.WindowInfo(
            title="*Notes.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\Notes.txt"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\Notes.txt")

    def test_extract_doc_name_supports_localized_notepad_title(self):
        win = self.cf.WindowInfo(
            title="Summary.txt - 記事本",
            process_name="notepad.exe",
            pid=101,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Summary.txt")

    def test_extract_doc_name_uses_office_process_when_title_suffix_is_localized(self):
        win = self.cf.WindowInfo(
            title="Budget.xlsx - Excel Localized",
            process_name="EXCEL.EXE",
            pid=101,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Budget.xlsx")

    def test_extract_doc_name_uses_pdf_process_when_title_suffix_is_unknown(self):
        win = self.cf.WindowInfo(
            title="Report.pdf - Acrobat Localized",
            process_name="Acrobat.exe",
            pid=101,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Report.pdf")

    def test_process_based_doc_title_resolves_open_pdf_path(self):
        win = self.cf.WindowInfo(
            title="Report - Acrobat Localized",
            process_name="Acrobat.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\Report.pdf"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\Report.pdf")

    def test_pdf_xchange_editor_title_resolves_open_pdf_path_without_extension(self):
        win = self.cf.WindowInfo(
            title="laptop walmart invoice - PDF-XChange Editor",
            process_name="PXCEditor.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\laptop walmart invoice.pdf"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\laptop walmart invoice.pdf")

    def test_open_document_window_texts_use_localized_notepad_hotkey_window(self):
        self.cf._context_window = self.cf.WindowInfo(
            title="Summary.txt - 記事本",
            process_name="notepad.exe",
            pid=101,
            hwnd=777,
        )

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_get_window_text_uia", return_value="hello from localized notepad"):
            docs = self.cf.get_all_open_document_window_texts()

        self.assertEqual(docs, [("Summary.txt", "hello from localized notepad")])

    def test_open_document_paths_prioritize_passed_active_window(self):
        self.cf._context_window = self.cf.WindowInfo(
            title="Summary.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=111,
        )
        active = self.cf.WindowInfo(
            title="Budget.xlsx - Excel",
            process_name="EXCEL.EXE",
            pid=202,
            hwnd=222,
        )

        def open_files(pid: int) -> list[str]:
            if pid == 101:
                return [r"C:\Users\sunny\Documents\Summary.txt"]
            if pid == 202:
                return [r"C:\Users\sunny\Documents\Budget.xlsx"]
            return []

        with patch.object(self.cf, "_win_open_files_for_pid", side_effect=open_files), \
             patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            paths = self.cf.get_all_open_document_paths(active_window=active)

        self.assertEqual(paths, [r"C:\Users\sunny\Documents\Budget.xlsx"])

    def test_open_document_window_texts_prioritize_passed_unsaved_calc_window(self):
        self.cf._context_window = self.cf.WindowInfo(
            title="Summary.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=111,
        )
        active = self.cf.WindowInfo(
            title="Untitled 1 \u2014 LibreOffice Calc",
            process_name="soffice.bin",
            pid=202,
            hwnd=222,
        )

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_get_window_text_uia", return_value="A1\tB1"):
            docs = self.cf.get_all_open_document_window_texts(active_window=active)

        self.assertEqual(docs, [("Untitled 1", "A1\tB1")])

    def test_open_document_window_texts_use_hotkey_window(self):
        self.cf._context_window = self.cf.WindowInfo(
            title="Notes.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=777,
        )

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_get_window_text_uia", return_value="hello from notepad"):
            docs = self.cf.get_all_open_document_window_texts()

        self.assertEqual(docs, [("Notes.txt", "hello from notepad")])


if __name__ == "__main__":
    unittest.main()
