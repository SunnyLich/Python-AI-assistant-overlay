from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QT_DIR = ROOT / "ui" / "locales" / "qt"
LANGUAGES = ("es", "fr", "zh", "zh-Hant")
HOTKEY_CONTEXT_SOURCE = (
    "These default to the context dropdowns on the hotkey - changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected."
)
STALE_HOTKEY_CONTEXT_SOURCES = (
    "These default to the context dropdowns on the hotkey â€” changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey — changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey Ã¢â‚¬â€ changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey \u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u009d changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey вЂ” changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
)


def _translations(language: str) -> dict[str, str]:
    tree = ET.parse(QT_DIR / f"wisp_{language}.ts")
    out: dict[str, str] = {}
    for message in tree.findall(".//message"):
        source = message.findtext("source")
        translation = message.findtext("translation")
        if source and translation:
            out[source] = translation
    return out


def _sources(language: str) -> set[str]:
    tree = ET.parse(QT_DIR / f"wisp_{language}.ts")
    return {
        source
        for message in tree.findall(".//message")
        if (source := message.findtext("source"))
    }


def test_qt_catalog_sources_are_in_sync() -> None:
    """Verify shipped Qt catalogs expose the same translation source keys."""
    catalogs = {language: _sources(language) for language in LANGUAGES}
    expected = catalogs[LANGUAGES[0]]
    for language, sources in catalogs.items():
        assert sources == expected, language
        assert HOTKEY_CONTEXT_SOURCE in sources
        for stale_source in STALE_HOTKEY_CONTEXT_SOURCES:
            assert stale_source not in sources


def test_context_badge_sources_have_catalog_translations() -> None:
    """Verify right-of-icon context badge labels exist in every catalog."""
    expected = {
        "zh": {
            "App": "程序",
            "Browser/Web": "浏览器/网页",
            "Context": "上下文",
            "Memory": "记忆",
            "Screenshot": "截图",
            "Selection": "选择内容",
            "Clipboard": "剪贴板",
            "Files": "文件",
        },
        "zh-Hant": {
            "App": "程式",
            "Browser/Web": "瀏覽器/網頁",
            "Context": "上下文",
            "Memory": "記憶",
            "Screenshot": "截圖",
            "Selection": "選取內容",
            "Clipboard": "剪貼簿",
            "Files": "檔案",
        },
        "es": {
            "App": "Aplicación",
            "Browser/Web": "Navegador/Web",
            "Context": "Contexto",
            "Memory": "Memoria",
            "Screenshot": "Captura",
            "Selection": "Selección",
            "Clipboard": "Portapapeles",
            "Files": "Archivos",
        },
        "fr": {
            "App": "Application",
            "Browser/Web": "Navigateur/Web",
            "Context": "Contexte",
            "Memory": "Mémoire",
            "Screenshot": "Capture",
            "Selection": "Sélection",
            "Clipboard": "Presse-papiers",
            "Files": "Fichiers",
        },
    }
    for language, pairs in expected.items():
        catalog = _translations(language)
        for source, translation in pairs.items():
            assert catalog[source] == translation
