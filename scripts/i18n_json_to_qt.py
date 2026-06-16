"""Build Qt Linguist catalogs from Wisp's legacy JSON translations.

This is a migration bridge: the app loads compiled .qm files first, then falls
back to JSON for strings that have not moved through the Qt catalog yet.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "ui" / "locales"
QT_LOCALES_DIR = LOCALES_DIR / "qt"
LANGUAGES = ("zh", "zh-Hant", "es", "fr")
CONTEXT = "Wisp"


def _indent(element: ET.Element, level: int = 0) -> None:
    spacing = "\n" + level * "  "
    if len(element):
        if not element.text or not element.text.strip():
            element.text = spacing + "  "
        for child in element:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = spacing
    if level and (not element.tail or not element.tail.strip()):
        element.tail = spacing


def _messages(raw: dict) -> dict[str, str]:
    messages: dict[str, str] = {}
    strings = raw.get("strings", {})
    if isinstance(strings, dict):
        for source, translation in strings.items():
            if isinstance(source, str) and isinstance(translation, str):
                messages[source] = translation
    for section in ("dynamic_prefixes", "dynamic_suffixes"):
        pairs = raw.get(section, [])
        if not isinstance(pairs, list):
            continue
        for item in pairs:
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], str)
            ):
                messages[item[0]] = item[1]
    return messages


def write_ts(language: str) -> Path:
    source_path = LOCALES_DIR / f"{language}.json"
    with source_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    root = ET.Element("TS", version="2.1", language=language)
    context = ET.SubElement(root, "context")
    ET.SubElement(context, "name").text = CONTEXT
    for source, translation in _messages(raw).items():
        message = ET.SubElement(context, "message")
        ET.SubElement(message, "source").text = source
        ET.SubElement(message, "translation").text = translation

    _indent(root)
    QT_LOCALES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = QT_LOCALES_DIR / f"wisp_{language}.ts"
    tree = ET.ElementTree(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def _lrelease_path() -> str | None:
    found = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if found:
        return found
    exe_name = "pyside6-lrelease.exe" if sys.platform.startswith("win") else "pyside6-lrelease"
    bundled = ROOT / ".venv" / ("Scripts" if sys.platform.startswith("win") else "bin") / exe_name
    return str(bundled) if bundled.exists() else None


def compile_qm(ts_path: Path) -> None:
    lrelease = _lrelease_path()
    if not lrelease:
        raise RuntimeError("Could not find pyside6-lrelease or lrelease")
    subprocess.run([lrelease, str(ts_path)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile", action="store_true", help="also build .qm files with pyside6-lrelease")
    parser.add_argument("languages", nargs="*", default=LANGUAGES, choices=LANGUAGES)
    args = parser.parse_args()

    for language in args.languages:
        ts_path = write_ts(language)
        print(ts_path.relative_to(ROOT))
        if args.compile:
            compile_qm(ts_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
