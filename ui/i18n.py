"""Qt Linguist localization for Wisp's UI."""
from __future__ import annotations

import locale
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
_QT_LOCALES_DIR = Path(__file__).with_name("locales") / "qt"
_COMBO_ORIGINAL_TEXT_ROLE = 0x0100 + 1000

_qt_translator: Any = None
_qt_translator_language = ""


def _system_language() -> str:
    """Handle system language for UI i18n."""
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
    """Normalize language."""
    value = str(raw or "").strip()
    code = _LANGUAGE_ALIASES.get(value.lower(), value)
    return code if code in _SUPPORTED_LANGUAGES else "en"


def current_language() -> str:
    """Handle current language for UI i18n."""
    try:
        import config

        raw = getattr(config, "APP_LANGUAGE", "") or ""
    except Exception:
        raw = ""
    return _normalize_language(raw or _system_language())


def _qt_catalog_path(code: str) -> Path:
    """Handle qt catalog path for UI i18n."""
    return _QT_LOCALES_DIR / f"wisp_{code}.qm"


def set_language(language: str | None = None, app: Any = None) -> str:
    """Install the compiled Qt catalog for the requested language."""
    global _qt_translator, _qt_translator_language

    code = _normalize_language(language or current_language())

    try:
        from PySide6.QtCore import QCoreApplication, QTranslator
    except Exception:
        return code

    app = app or QCoreApplication.instance()
    if app is None:
        return code
    if code == _qt_translator_language:
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


def t(text: str, context: str = _QT_CONTEXT) -> str:
    """Translate UI source text through Qt Linguist."""
    if not isinstance(text, str) or not text:
        return text
    set_language()
    try:
        from PySide6.QtCore import QCoreApplication
    except Exception:
        return text
    return QCoreApplication.translate(context, text) or text


def install(app: Any) -> None:
    """Apply the saved UI language to the Qt application."""
    if app is not None:
        set_language(app=app)


def _original(obj: Any, prop_name: str, value: str) -> str:
    """Handle original for UI i18n."""
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
    """Handle translate action for UI i18n."""
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
    """Retranslate a hand-built widget tree with the active Qt catalog."""
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
        if isinstance(widget, QComboBox) and not widget.isEditable():
            for idx in range(widget.count()):
                text = widget.itemText(idx)
                if not text:
                    continue
                original = widget.itemData(idx, _COMBO_ORIGINAL_TEXT_ROLE)
                if original is None:
                    original = text
                    widget.setItemData(idx, original, _COMBO_ORIGINAL_TEXT_ROLE)
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


def refresh_all_widgets(app: Any = None) -> None:
    """Refresh all widgets."""
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
