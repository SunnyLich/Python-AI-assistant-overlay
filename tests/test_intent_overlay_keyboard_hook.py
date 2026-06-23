"""Regression tests for intent overlay key swallowing."""

from __future__ import annotations

import ast
from pathlib import Path


def test_windows_intent_keyboard_hook_suppresses_underlying_app_keys():
    """Verify raw picker keys are swallowed without suppressing every key."""
    tree = ast.parse(Path("ui/intent_overlay.py").read_text(encoding="utf-8-sig"))

    def is_suppressed_shortcut_hook(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "on_press_key":
            return False
        return any(
            keyword.arg == "suppress"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )

    def is_suppress_all_hook(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "hook":
            return False
        return any(
            keyword.arg == "suppress"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )

    assert any(is_suppressed_shortcut_hook(node) for node in ast.walk(tree))
    assert not any(is_suppress_all_hook(node) for node in ast.walk(tree))


def test_raw_custom_shortcut_does_not_drop_next_typed_s():
    """Verify the raw Windows hook does not arm the custom-trigger key drop."""
    tree = ast.parse(Path("ui/intent_overlay.py").read_text(encoding="utf-8-sig"))

    class RawKeyVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.in_raw_key = False
            self.found = False

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            was_in_raw_key = self.in_raw_key
            self.in_raw_key = node.name == "_on_raw_key"
            self.generic_visit(node)
            self.in_raw_key = was_in_raw_key

        def visit_Call(self, node: ast.Call) -> None:
            if (
                self.in_raw_key
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_select"
                and any(
                    keyword.arg == "drop_trigger_key"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                    for keyword in node.keywords
                )
            ):
                self.found = True
            self.generic_visit(node)

    visitor = RawKeyVisitor()
    visitor.visit(tree)

    assert visitor.found


def test_stale_raw_keys_are_ignored_after_hook_removal():
    """Verify queued raw events cannot pick an intent after custom mode unhooks."""
    tree = ast.parse(Path("ui/intent_overlay.py").read_text(encoding="utf-8-sig"))

    class RawKeyGuardVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.in_raw_key = False
            self.has_closed_guard = False

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            was_in_raw_key = self.in_raw_key
            self.in_raw_key = node.name == "_on_raw_key"
            self.generic_visit(node)
            self.in_raw_key = was_in_raw_key

        def visit_Call(self, node: ast.Call) -> None:
            if (
                self.in_raw_key
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "_closed"
            ):
                self.has_closed_guard = True
            self.generic_visit(node)

    visitor = RawKeyGuardVisitor()
    visitor.visit(tree)

    assert visitor.has_closed_guard
