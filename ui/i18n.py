"""Localization helper for Wisp's Qt UI.

Qt Linguist catalogs are the primary translation path. The legacy JSON tables
remain as a fallback while older screens are migrated away from live text
matching.
"""
from __future__ import annotations

import json
import locale
import re
from pathlib import Path
from typing import Any

LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("System default", ""),
    ("English", "en"),
    ("Chinese (Simplified)", "zh"),
    ("Chinese (Traditional)", "zh-Hant"),
    ("Spanish", "es"),
    ("French", "fr"),
)

_LANGUAGE_ALIASES = {
    "english": "en",
    "en": "en",
    "chinese": "zh",
    "chinese (simplified)": "zh",
    "zh": "zh",
    "zh_cn": "zh",
    "zh-cn": "zh",
    "chinese (traditional)": "zh-Hant",
    "traditional chinese": "zh-Hant",
    "zh_hant": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh_tw": "zh-Hant",
    "zh-tw": "zh-Hant",
    "zh_hk": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh_mo": "zh-Hant",
    "zh-mo": "zh-Hant",
    "spanish": "es",
    "es": "es",
    "french": "fr",
    "fr": "fr",
}

_SUPPORTED_LANGUAGES = {"en", "zh", "zh-Hant", "es", "fr"}
_QT_CONTEXT = "Wisp"

_qt_translator: Any = None
_qt_translator_language = ""


def _system_language() -> str:
    loc = ""
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    normalized = loc.replace("-", "_").lower()
    if "hant" in normalized or normalized.startswith(("zh_tw", "zh_hk", "zh_mo")):
        return "zh-Hant"
    code = normalized.split("_", 1)[0]
    return code if code in {"zh", "es", "fr"} else "en"


def _normalize_language(raw: str) -> str:
    code = _LANGUAGE_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
    return code if code in _SUPPORTED_LANGUAGES else "en"


def current_language() -> str:
    try:
        import config

        raw = getattr(config, "APP_LANGUAGE", "") or ""
    except Exception:
        raw = ""
    code = _LANGUAGE_ALIASES.get(str(raw).strip().lower(), str(raw).strip().lower())
    if not code:
        code = _system_language()
    return _normalize_language(code)


_LOCALES_DIR = Path(__file__).with_name("locales")
_QT_LOCALES_DIR = _LOCALES_DIR / "qt"
_LOCALE_CODES = ("zh", "zh-Hant", "es", "fr")


def _qt_catalog_path(code: str) -> Path:
    return _QT_LOCALES_DIR / f"wisp_{code}.qm"


def set_language(language: str | None = None, app: Any = None) -> str:
    """Install the Qt Linguist catalog for the active UI language.

    JSON catalogs below remain a compatibility fallback while older screens are
    migrated. The primary path is Qt's standard QTranslator/.qm pipeline.
    """
    global _qt_translator, _qt_translator_language

    code = _normalize_language(language or current_language())
    if code == _qt_translator_language:
        return code

    try:
        from PySide6.QtCore import QCoreApplication, QTranslator
    except Exception:
        return code

    app = app or QCoreApplication.instance()
    if app is None:
        return code

    if _qt_translator is not None:
        try:
            app.removeTranslator(_qt_translator)
        except Exception:
            pass
        _qt_translator = None

    _qt_translator_language = code
    if code == "en":
        return code

    path = _qt_catalog_path(code)
    if not path.exists():
        return code

    translator = QTranslator()
    if translator.load(str(path)):
        app.installTranslator(translator)
        _qt_translator = translator
    return code


def _ensure_qt_translator(language: str) -> None:
    if language == _qt_translator_language:
        return
    set_language(language)


def _translate_qt(text: str, context: str = _QT_CONTEXT) -> str:
    try:
        from PySide6.QtCore import QCoreApplication
    except Exception:
        return text
    translated = QCoreApplication.translate(context, text)
    return translated if translated else text


def _load_locale(code: str) -> dict[str, Any]:
    path = _LOCALES_DIR / f"{code}.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {"strings": {}, "dynamic_prefixes": (), "dynamic_suffixes": ()}
    if not isinstance(raw, dict):
        return {"strings": {}, "dynamic_prefixes": (), "dynamic_suffixes": ()}
    return raw


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def _string_pairs(raw: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            pairs.append((item[0], item[1]))
    return tuple(pairs)


_LOCALE_DATA = {code: _load_locale(code) for code in _LOCALE_CODES}
_TRANSLATIONS: dict[str, dict[str, str]] = {
    code: _string_map(_LOCALE_DATA[code].get("strings")) for code in _LOCALE_CODES
}
_DYNAMIC_PREFIXES_BY_LANGUAGE = {
    code: _string_pairs(_LOCALE_DATA[code].get("dynamic_prefixes")) for code in _LOCALE_CODES
}
_DYNAMIC_SUFFIXES_BY_LANGUAGE = {
    code: _string_pairs(_LOCALE_DATA[code].get("dynamic_suffixes")) for code in _LOCALE_CODES
}


_HTML_TAG_RE = re.compile(r"(<[^>]+>|[^<]+)")


def _translate_html_chunks(text: str, table: dict[str, str]) -> str:
    if "<" not in text or ">" not in text:
        return text
    parts = []
    changed = False
    for match in _HTML_TAG_RE.finditer(text):
        part = match.group(0)
        if part.startswith("<"):
            parts.append(part)
            continue
        translated = _translate_plain(part, table)
        changed = changed or translated != part
        parts.append(translated)
    return "".join(parts) if changed else text


def _translate_plain(text: str, table: dict[str, str]) -> str:
    if text in table:
        return table[text]
    stripped = text.strip()
    if stripped and stripped in table:
        return text[: len(text) - len(text.lstrip())] + table[stripped] + text[len(text.rstrip()) :]
    language = current_language()
    prefixes = _DYNAMIC_PREFIXES_BY_LANGUAGE.get(language, ())
    suffixes = _DYNAMIC_SUFFIXES_BY_LANGUAGE.get(language, ())
    if prefixes or suffixes:
        for prefix, translated_prefix in prefixes:
            if text.startswith(prefix):
                rest = text[len(prefix):]
                leading = rest[: len(rest) - len(rest.lstrip())]
                return translated_prefix + leading + t(rest.strip())
        for suffix, translated_suffix in suffixes:
            if text.endswith(suffix):
                return text[: -len(suffix)] + translated_suffix
    return text


def t(text: str, context: str = _QT_CONTEXT) -> str:
    if not isinstance(text, str) or not text:
        return text
    language = current_language()
    _ensure_qt_translator(language)
    if language != "en":
        translated = _translate_qt(text, context)
        if translated != text:
            return translated
    table = _TRANSLATIONS.get(language, {})
    translated = _translate_plain(text, table)
    if translated != text:
        return translated
    translated = _translate_html_chunks(text, table)
    return translated


def _original(obj: Any, prop_name: str, value: str) -> str:
    store_name = f"_wisp_i18n_{prop_name}"
    try:
        original = obj.property(store_name)
        if original is None:
            obj.setProperty(store_name, value)
            return value
        return str(original)
    except Exception:
        return value


def _translate_action(action: Any) -> None:
    try:
        text = action.text()
    except Exception:
        return
    if text:
        action.setText(t(_original(action, "text", text)))
    try:
        tip = action.toolTip()
        if tip:
            action.setToolTip(t(_original(action, "tooltip", tip)))
    except Exception:
        pass


def localize_widget_tree(root: Any) -> None:
    try:
        from PySide6.QtWidgets import (
            QAbstractButton,
            QComboBox,
            QGroupBox,
            QLabel,
            QLineEdit,
            QTabWidget,
            QTextEdit,
            QWidget,
        )
    except Exception:
        return

    widgets = [root]
    try:
        if isinstance(root, QWidget):
            widgets.extend(root.findChildren(QWidget))
    except Exception:
        pass

    for widget in widgets:
        try:
            title = widget.windowTitle()
            if title:
                widget.setWindowTitle(t(_original(widget, "window_title", title)))
        except Exception:
            pass
        if isinstance(widget, QLabel):
            text = widget.text()
            if text:
                widget.setText(t(_original(widget, "text", text)))
        elif isinstance(widget, QAbstractButton):
            text = widget.text()
            if text:
                widget.setText(t(_original(widget, "text", text)))
        elif isinstance(widget, QGroupBox):
            title = widget.title()
            if title:
                widget.setTitle(t(_original(widget, "title", title)))
        if isinstance(widget, (QLineEdit, QTextEdit)):
            placeholder = widget.placeholderText()
            if placeholder:
                widget.setPlaceholderText(t(_original(widget, "placeholder", placeholder)))
        if isinstance(widget, QComboBox):
            for idx in range(widget.count()):
                text = widget.itemText(idx)
                if not text:
                    continue
                original = widget.itemData(idx, 0x0100 + 1)
                if original is None:
                    original = text
                    widget.setItemData(idx, original, 0x0100 + 1)
                widget.setItemText(idx, t(str(original)))
        if isinstance(widget, QTabWidget):
            for idx in range(widget.count()):
                text = widget.tabText(idx)
                if not text:
                    continue
                key = f"_wisp_i18n_tab_{idx}"
                original = widget.property(key)
                if original is None:
                    widget.setProperty(key, text)
                    original = text
                widget.setTabText(idx, t(str(original)))
        try:
            tip = widget.toolTip()
            if tip:
                widget.setToolTip(t(_original(widget, "tooltip", tip)))
        except Exception:
            pass
        try:
            for action in widget.actions():
                _translate_action(action)
        except Exception:
            pass


class _I18nEventFilter:
    def __init__(self, qobject_cls: type) -> None:
        self._qobject_cls = qobject_cls
        self._instance = None

    def instance(self):
        if self._instance is None:
            from PySide6.QtCore import QEvent
            from PySide6.QtWidgets import QWidget

            class Filter(self._qobject_cls):
                def eventFilter(self, obj, event):  # noqa: N802
                    if (
                        event.type() == QEvent.Type.Show
                        and isinstance(obj, QWidget)
                        and obj.isWindow()
                    ):
                        localize_widget_tree(obj)
                    return False

            self._instance = Filter()
        return self._instance


_filter_holder: _I18nEventFilter | None = None


def install(app: Any) -> None:
    global _filter_holder
    if app is None:
        return
    try:
        from PySide6.QtCore import QObject

        set_language(app=app)
        if _filter_holder is None:
            _filter_holder = _I18nEventFilter(QObject)
        app.installEventFilter(_filter_holder.instance())
    except Exception:
        pass


def refresh_all_widgets(app: Any = None) -> None:
    try:
        from PySide6.QtWidgets import QApplication

        app = app or QApplication.instance()
        if app is None:
            return
        set_language(app=app)
        for widget in app.topLevelWidgets():
            localize_widget_tree(widget)
    except Exception:
        pass
