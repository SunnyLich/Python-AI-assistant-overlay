"""Compile Wisp Qt Linguist catalogs."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_LOCALES_DIR = ROOT / "ui" / "locales" / "qt"
LANGUAGES = ("zh", "zh-Hant", "es", "fr")


def _lrelease_path() -> str | None:
    found = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if found:
        return found
    exe_name = "pyside6-lrelease.exe" if sys.platform.startswith("win") else "pyside6-lrelease"
    bundled = ROOT / ".venv" / ("Scripts" if sys.platform.startswith("win") else "bin") / exe_name
    return str(bundled) if bundled.exists() else None


def compile_qm(language: str) -> None:
    lrelease = _lrelease_path()
    if not lrelease:
        raise RuntimeError("Could not find pyside6-lrelease or lrelease")
    ts_path = QT_LOCALES_DIR / f"wisp_{language}.ts"
    if not ts_path.exists():
        raise FileNotFoundError(ts_path)
    subprocess.run([lrelease, str(ts_path)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("languages", nargs="*", choices=LANGUAGES)
    args = parser.parse_args()

    for language in args.languages or LANGUAGES:
        compile_qm(language)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
