import contextlib
import io
import sys
import unittest

from scripts import check_python_version


class PythonVersionCheckTests(unittest.TestCase):
    def test_parse_version_requires_major_minor_patch(self) -> None:
        self.assertEqual(check_python_version.parse_version("3.12.13"), (3, 12, 13))

        with self.assertRaises(ValueError):
            check_python_version.parse_version("3.12")

    def test_main_accepts_exact_current_version(self) -> None:
        expected = ".".join(str(part) for part in sys.version_info[:3])

        self.assertEqual(check_python_version.main([expected]), 0)

    def test_main_rejects_different_patch_version(self) -> None:
        major, minor, micro = sys.version_info[:3]
        expected = f"{major}.{minor}.{micro + 1}"
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = check_python_version.main([expected, "--label", "Wisp Python"])

        self.assertEqual(exit_code, 1)
        self.assertIn(f"Wisp Python {expected} is required", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
