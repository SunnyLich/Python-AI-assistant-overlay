"""Pure helper functions for settings UI values."""
from __future__ import annotations

try:
    from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QToolTip, QWidget
except ImportError:  # pragma: no cover - exercised by collection without loadable Qt libraries
    QComboBox = QFormLayout = QLabel = QToolTip = QWidget = None  # type: ignore[assignment]

from ui.i18n import t


def parse_fallback_rows(raw: str) -> list[tuple[str, str]]:
    """Parse fallback rows."""
    rows: list[tuple[str, str]] = []
    for part in raw.replace(";", "\n").splitlines():
        item = part.strip()
        if not item or item.startswith("#") or ":" not in item:
            continue
        provider, model = [piece.strip() for piece in item.split(":", 1)]
        if provider and model:
            rows.append((provider, model))
    return rows


if QComboBox is not None:

    class NoScrollCombo(QComboBox):
        """QComboBox that keeps passive wheel scrolling on the settings page."""

        def wheelEvent(self, event):  # noqa: N802 - Qt override
            """Handle wheel event for no scroll combo."""
            if self.view().isVisible():
                super().wheelEvent(event)
            else:
                event.ignore()


    class WarningHeaderLabel(QLabel):
        """Header label that keeps warning help visible while hovered."""

        def __init__(self, text: str = "") -> None:
            """Initialize the warning header label instance."""
            super().__init__(text)
            self.setMouseTracking(True)

        def enterEvent(self, event):  # noqa: N802 - Qt override
            """Handle enter event for warning header label."""
            tip = self.toolTip()
            if tip:
                QToolTip.showText(
                    self.mapToGlobal(self.rect().bottomLeft()),
                    tip,
                    self,
                    self.rect(),
                    2_147_000_000,
                )
            super().enterEvent(event)

        def leaveEvent(self, event):  # noqa: N802 - Qt override
            """Handle leave event for warning header label."""
            QToolTip.hideText()
            super().leaveEvent(event)

else:

    class NoScrollCombo:  # type: ignore[no-redef]
        """Placeholder used when Qt is not installed."""

        def __init__(self, *args, **kwargs) -> None:
            """Initialize placeholder."""
            raise RuntimeError("PySide6 is required for settings UI widgets.")

    class WarningHeaderLabel:  # type: ignore[no-redef]
        """Placeholder used when Qt is not installed."""

        def __init__(self, *args, **kwargs) -> None:
            """Initialize placeholder."""
            raise RuntimeError("PySide6 is required for settings UI widgets.")


def context_mode_combo(
    value: str,
    *,
    allow_auto: bool = True,
    on_value: str = "auto",
) -> NoScrollCombo:
    """Handle context mode combo for UI settings panel helpers."""
    if QComboBox is None:
        raise RuntimeError("PySide6 is required for settings UI widgets.")
    combo = NoScrollCombo()
    combo.addItem(t("Off"), "off")
    if allow_auto:
        combo.addItem(t("On"), on_value)
    combo.addItem(t("Let model decide"), "model")
    normalized = (value or "off").strip().lower()
    if normalized == "auto" and on_value == "on":
        normalized = "on"
    idx = combo.findData(normalized)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    return combo


def expanding_form_layout(parent: QWidget | None = None) -> QFormLayout:
    """Handle expanding form layout for UI settings panel helpers."""
    if QFormLayout is None:
        raise RuntimeError("PySide6 is required for settings UI widgets.")
    form = QFormLayout(parent)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form
