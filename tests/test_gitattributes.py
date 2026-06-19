import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def attribute_lines() -> dict[str, tuple[str, ...]]:
    attrs: dict[str, tuple[str, ...]] = {}
    for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        pattern, *attributes = text.split()
        attrs[pattern] = tuple(attributes)
    return attrs


class GitAttributesTests(unittest.TestCase):
    def test_text_file_line_endings_are_pinned(self) -> None:
        attrs = attribute_lines()

        for pattern in (".gitattributes", "*.md", "*.py", "*.toml", "*.txt", "*.ps1", "*.yml", "*.yaml"):
            self.assertEqual(attrs[pattern], ("text", "eol=lf"))

    def test_launcher_line_endings_stay_platform_specific(self) -> None:
        attrs = attribute_lines()

        self.assertEqual(attrs["*.command"], ("text", "eol=lf"))
        self.assertEqual(attrs["*.sh"], ("text", "eol=lf"))
        self.assertEqual(attrs["*.bat"], ("text", "eol=crlf"))


if __name__ == "__main__":
    unittest.main()
