"""Tests for test agent task window layout."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    pytest.importorskip("PySide6", reason="PySide6 not installed") is None,
    reason="PySide6 not installed",
)


def _qapp():
    """Verify qapp behavior."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _form_layouts(widget):
    """Verify form layouts behavior."""
    from PySide6.QtWidgets import QFormLayout, QWidget

    seen = set()
    found = []

    def visit_layout(layout):
        """Verify visit layout behavior."""
        if layout is None or id(layout) in seen:
            return
        seen.add(id(layout))
        if isinstance(layout, QFormLayout):
            found.append(layout)
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item is None:
                continue
            visit_layout(item.layout())
            child = item.widget()
            if child is not None:
                visit_layout(child.layout())

    visit_layout(widget.layout())
    for child in widget.findChildren(QWidget):
        visit_layout(child.layout())
    return found


def test_agent_task_forms_expand_across_platform_styles():
    """Verify agent task forms expand across platform styles behavior."""
    _qapp()
    from PySide6.QtWidgets import QFormLayout
    from ui.agent.task_window import AgentTaskDialog

    dialog = AgentTaskDialog()
    try:
        dialog._toggle_advanced_settings()
        forms = _form_layouts(dialog)

        assert len(forms) >= 6
        assert {
            form.fieldGrowthPolicy()
            for form in forms
        } == {QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow}
    finally:
        dialog.deleteLater()


def test_agent_task_title_field_uses_extra_window_width():
    """Verify agent task title field uses extra window width behavior."""
    app = _qapp()
    from ui.agent.task_window import AgentTaskDialog

    dialog = AgentTaskDialog()
    try:
        dialog.show()
        dialog.resize(600, 520)
        app.processEvents()
        narrow_width = dialog.title_edit.width()

        dialog.resize(900, 520)
        app.processEvents()
        wide_width = dialog.title_edit.width()

        assert wide_width > narrow_width
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_agent_communication_forms_expand_across_platform_styles():
    """Verify agent communication forms expand across platform styles behavior."""
    _qapp()
    from PySide6.QtWidgets import QFormLayout
    from ui.agent.task_window import (
        AgentCommunicationDialog,
        AgentCommunicationMapWindow,
        AgentNudgeDialog,
        AgentTaskDialog,
    )

    task_dialog = AgentTaskDialog()
    map_window = AgentCommunicationMapWindow(task_dialog)
    communication_dialog = AgentCommunicationDialog(["Planner", "Builder"])
    nudge_dialog = AgentNudgeDialog(["Planner", "Builder"])
    widgets = [task_dialog, map_window, communication_dialog, nudge_dialog]
    try:
        forms = [form for widget in widgets for form in _form_layouts(widget)]

        assert forms
        assert {
            form.fieldGrowthPolicy()
            for form in forms
        } == {QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow}
    finally:
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()


def test_agent_communication_i18n_preserves_internal_values():
    """Verify agent communication i18n preserves internal values behavior."""
    app = _qapp()

    import config
    from ui import i18n
    from ui.agent.task_window import AgentCommunicationDialog, AgentCommunicationMapWindow, AgentTaskDialog

    old_language = getattr(config, "APP_LANGUAGE", "")
    config.APP_LANGUAGE = "zh-Hant"
    i18n.set_language(app=app)
    task_dialog = AgentTaskDialog()
    map_window = AgentCommunicationMapWindow(task_dialog)
    exchange_dialog = AgentCommunicationDialog(["Coordinator", "Builder"])
    widgets = [task_dialog, map_window, exchange_dialog]
    try:
        map_window.refresh()

        role_index = map_window.map_agent_role.findData("Coordinator")
        phase_index = map_window.map_comm_phase.findData("Planning")
        from_index = map_window.map_comm_from.findData("Coordinator")

        assert role_index >= 0
        assert phase_index >= 0
        assert from_index >= 0
        assert map_window.map_agent_role.itemText(role_index) == "\u5354\u8abf\u54e1"
        assert map_window.map_comm_phase.itemText(phase_index) == "\u898f\u5283"
        assert map_window.map_comm_from.itemText(from_index) == "\u5354\u8abf\u54e1"
        assert "Coordinator" not in {
            map_window.map_agent_role.itemText(i)
            for i in range(map_window.map_agent_role.count())
        }
        assert "Planning" not in {
            map_window.map_comm_phase.itemText(i)
            for i in range(map_window.map_comm_phase.count())
        }

        map_window.map_comm_from.setCurrentIndex(from_index)
        map_window.map_comm_to.setCurrentIndex(map_window.map_comm_to.findData("Builder"))
        map_window.map_comm_phase.setCurrentIndex(phase_index)
        map_window._save_exchange_form()

        assert task_dialog._communication_specs[0]["from_agent"] == "Coordinator"
        assert task_dialog._communication_specs[0]["to_agent"] == "Builder"
        assert task_dialog._communication_specs[0]["phase"] == "Planning"
        assert exchange_dialog.from_combo.itemText(
            exchange_dialog.from_combo.findData("Coordinator")
        ) == "\u5354\u8abf\u54e1"
        assert exchange_dialog.phase_combo.itemText(
            exchange_dialog.phase_combo.findData("Planning")
        ) == "\u898f\u5283"
    finally:
        config.APP_LANGUAGE = old_language
        i18n.set_language(app=app)
        for widget in reversed(widgets):
            widget.close()
            widget.deleteLater()
        app.processEvents()
