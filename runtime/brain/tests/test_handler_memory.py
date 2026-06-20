"""Unit tests for the ``brain.memory.add`` / ``brain.memory.search`` handlers.

A fake ``core.memory_store`` is injected so the tests exercise the handler's
routing (explicit vs. categorized add, query passthrough, validation) without
touching the real on-disk memory store or any background LLM jobs.
"""
from __future__ import annotations

import sys
import types

import pytest

from wisp_brain import handlers


class FakeManager:
    """Coordinate fake manager behavior."""
    def __init__(self):
        """Initialize the fake manager instance."""
        self.explicit: list[str] = []
        self.manual: list[tuple[str, str, object]] = []
        self.searches: list[tuple[str, object]] = []
        self.updates: list[tuple[str, str, object, object]] = []
        self.deletes: list[str] = []
        self.facts: list[dict] = [
            {
                "id": "fact-1",
                "text": "likes tea",
                "category": "general",
                "source": "manual",
                "created_at": "2026-06-01T10:00:00",
                "last_seen": "2026-06-02T10:00:00",
                "project": "",
            },
            {"id": "fact-2", "text": "ships Fridays"},
        ]

    def add_explicit_fact(self, fact: str) -> None:
        """Verify add explicit fact behavior."""
        self.explicit.append(fact)

    def add_fact_manual(self, fact: str, category: str, project=None) -> None:
        """Verify add fact manual behavior."""
        self.manual.append((fact, category, project))

    def retrieve_relevant(self, query: str, top_k=None) -> str:
        """Verify retrieve relevant behavior."""
        self.searches.append((query, top_k))
        return f"MEM[{query}|{top_k}]"

    def get_all_facts(self) -> list[dict]:
        """Verify get all facts behavior."""
        return self.facts

    def update_fact(self, fact_id: str, text: str, category=None, project=None) -> None:
        """Verify update fact behavior."""
        self.updates.append((fact_id, text, category, project))

    def delete_fact(self, fact_id: str) -> None:
        """Verify delete fact behavior."""
        self.deletes.append(fact_id)


@pytest.fixture
def manager(monkeypatch):
    """Verify manager behavior."""
    mgr = FakeManager()
    fake_pkg = types.ModuleType("core.memory_store")
    fake_pkg.store = types.SimpleNamespace(get_manager=lambda: mgr)
    monkeypatch.setitem(sys.modules, "core.memory_store", fake_pkg)
    return mgr


def test_memory_handlers_registered():
    """Verify memory handlers registered behavior."""
    assert "brain.memory.add" in handlers.HANDLERS
    assert "brain.memory.search" in handlers.HANDLERS
    assert "brain.memory.list" in handlers.HANDLERS
    assert "brain.memory.update" in handlers.HANDLERS
    assert "brain.memory.delete" in handlers.HANDLERS


def test_add_without_category_is_explicit(manager):
    """Verify add without category is explicit behavior."""
    result = handlers.HANDLERS["brain.memory.add"](text="  the user likes tea  ")
    assert result == {"ok": True, "category": "auto", "text": "the user likes tea"}
    assert manager.explicit == ["the user likes tea"]
    assert manager.manual == []


def test_add_with_category_is_manual(manager):
    """Verify add with category is manual behavior."""
    result = handlers.HANDLERS["brain.memory.add"](text="ships on Fridays", category="project")
    assert result["category"] == "project"
    assert manager.manual == [("ships on Fridays", "project", None)]
    assert manager.explicit == []


def test_add_with_project_is_manual_scoped(manager):
    """Verify add with project forwards scoped manual fact behavior."""
    result = handlers.HANDLERS["brain.memory.add"](text="ships on Fridays", project="proj-1")
    assert result == {
        "ok": True,
        "category": "project_context",
        "project": "proj-1",
        "text": "ships on Fridays",
    }
    assert manager.manual == [("ships on Fridays", "project_context", "proj-1")]
    assert manager.explicit == []


def test_add_requires_text(manager):
    """Verify add requires text behavior."""
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.add"](text="   ")


def test_search_returns_relevant_block(manager):
    """Verify search returns relevant block behavior."""
    result = handlers.HANDLERS["brain.memory.search"](query="tea", top_k=5)
    assert result == {"text": "MEM[tea|5]"}
    assert manager.searches == [("tea", 5)]


def test_search_requires_query(manager):
    """Verify search requires query behavior."""
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.search"](query="")


def test_list_returns_normalized_facts(manager):
    """Verify list returns normalized facts behavior."""
    result = handlers.HANDLERS["brain.memory.list"]()

    assert result == {
        "facts": [
            {
                "id": "fact-1",
                "text": "likes tea",
                "category": "general",
                "source": "manual",
                "project": "",
                "created_at": "2026-06-01T10:00:00",
                "last_seen": "2026-06-02T10:00:00",
            },
            {
                "id": "fact-2",
                "text": "ships Fridays",
                "category": "general",
                "source": "unknown",
                "project": "",
                "created_at": "",
                "last_seen": "",
            },
        ]
    }


def test_update_validates_and_forwards(manager):
    """Verify update validates and forwards behavior."""
    result = handlers.HANDLERS["brain.memory.update"](
        fact_id=" fact-1 ",
        text="  prefers green tea  ",
        category="general",
    )

    assert result == {
        "ok": True,
        "id": "fact-1",
        "text": "prefers green tea",
        "category": "general",
    }
    assert manager.updates == [("fact-1", "prefers green tea", "general", None)]


def test_update_with_project_forwards_scope(manager):
    """Verify update with project forwards scope behavior."""
    result = handlers.HANDLERS["brain.memory.update"](
        fact_id=" fact-1 ",
        text="  prefers green tea  ",
        category="general",
        project="proj-2",
    )

    assert result == {
        "ok": True,
        "id": "fact-1",
        "text": "prefers green tea",
        "category": "project_context",
        "project": "proj-2",
    }
    assert manager.updates == [("fact-1", "prefers green tea", "general", "proj-2")]


def test_update_requires_id_and_text(manager):
    """Verify update requires id and text behavior."""
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.update"](fact_id="", text="hello")
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.update"](fact_id="fact-1", text=" ")


def test_delete_validates_and_forwards(manager):
    """Verify delete validates and forwards behavior."""
    result = handlers.HANDLERS["brain.memory.delete"](fact_id=" fact-2 ")

    assert result == {"ok": True, "id": "fact-2"}
    assert manager.deletes == ["fact-2"]


def test_delete_requires_id(manager):
    """Verify delete requires id behavior."""
    with pytest.raises(ValueError):
        handlers.HANDLERS["brain.memory.delete"](fact_id=" ")
