"""Tests for test memory quality."""

import json
import sys
import threading
import time

from core.memory_store import store
from unittest.mock import patch


def _seed_router_attrs(manager):
    """Verify seed router attrs behavior."""
    manager._ctx_router_lock = threading.Lock()
    manager._ctx_router = None
    manager._ctx_router_fact_count = -1
    return manager


def test_summarizer_rejects_task_request():
    """Verify summarizer rejects task request behavior."""
    assert not store._is_memory_worthy_fact("Please fix the settings dialog", source="summarizer")


def test_summarizer_keeps_durable_preference():
    """Verify summarizer keeps durable preference behavior."""
    assert store._is_memory_worthy_fact("I prefer concise answers", source="summarizer")


def test_explicit_can_keep_short_command_shaped_fact():
    """Verify explicit can keep short command shaped fact behavior."""
    assert store._is_memory_worthy_fact("fix grammar before pasting text", source="explicit")


def test_rejects_secrets():
    """Verify rejects secrets behavior."""
    assert not store._is_memory_worthy_fact("My API key is sk-testabcdefghijklmnop", source="explicit")


def test_memory_manager_tolerates_storage_directory_creation_failure():
    """Verify memory manager tolerates storage directory creation failure behavior."""
    with patch.object(store.os, "makedirs", side_effect=PermissionError("denied")), \
         patch.object(store, "_migrate_legacy_chroma_to_json", return_value=0), \
         patch.object(store.MemoryManager, "_sync_consolidation_timer", autospec=True, return_value=None):
        manager = store.MemoryManager()

    assert manager._stm == []


def test_memory_uses_json_store_even_when_macos_safe_mode_is_disabled():
    """Verify memory uses json store even when macos safe mode is disabled behavior."""
    with patch.object(store.macos_safety.sys, "platform", "darwin"), \
         patch.dict(store.macos_safety.os.environ, {"WISP_MACOS_SAFE_MODE": "0"}, clear=True), \
         patch.object(store.os, "makedirs", return_value=None), \
         patch.object(store, "_migrate_legacy_chroma_to_json", return_value=0), \
         patch.object(store.MemoryManager, "_sync_consolidation_timer", autospec=True, return_value=None):
        manager = store.MemoryManager()

    assert manager._stm == []


def test_lightweight_fact_list_does_not_initialize_memory_manager(tmp_path, monkeypatch):
    """Verify lightweight fact list does not initialize memory manager behavior."""
    fallback = tmp_path / "facts_fallback.json"
    fallback.write_text(
        json.dumps([
            {"id": "keep", "text": "I prefer concise answers", "archived": False},
            {"id": "skip", "text": "old fact", "archived": True},
        ]),
        encoding="utf-8",
    )

    class BrokenMemoryManager:
        """Coordinate broken memory manager behavior."""
        def __init__(self):
            """Initialize the broken memory manager instance."""
            raise AssertionError("MemoryManager should not be constructed")

    monkeypatch.setattr(store, "_manager", None)
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))
    monkeypatch.setattr(store, "_migrate_legacy_chroma_to_json", lambda: 0)
    monkeypatch.setattr(store, "MemoryManager", BrokenMemoryManager)

    assert store.get_loaded_manager() is None
    assert store.get_all_facts_lightweight() == [
        {"id": "keep", "text": "I prefer concise answers", "archived": False}
    ]


def test_lightweight_manual_fact_write_uses_json_store(tmp_path, monkeypatch):
    """Verify lightweight manual fact write uses json store behavior."""
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    assert store.add_fact_manual_lightweight("I prefer fast memory settings", "general") is True

    facts = json.loads(fallback.read_text(encoding="utf-8"))
    assert len(facts) == 1
    assert facts[0]["text"] == "I prefer fast memory settings"
    assert facts[0]["category"] == "general"
    assert facts[0]["source"] == "manual"


def test_legacy_chroma_facts_are_imported_into_json(tmp_path, monkeypatch):
    """Verify legacy chroma facts are imported into json behavior."""
    fallback = tmp_path / "facts_fallback.json"
    fallback.write_text(
        json.dumps([{"id": "json-1", "text": "I use JSON facts", "category": "general"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))
    monkeypatch.setattr(
        store,
        "_legacy_chroma_facts",
        lambda: [{"id": "chroma-1", "text": "I use imported facts", "category": "project_context"}],
    )

    assert store._migrate_legacy_chroma_to_json() == 1
    assert [fact["id"] for fact in store._fallback_get_all_from_path()] == ["json-1", "chroma-1"]


def test_retrieve_relevant_returns_json_facts(monkeypatch):
    """Verify retrieve relevant returns json facts behavior."""
    manager = store.MemoryManager.__new__(store.MemoryManager)
    _seed_router_attrs(manager)
    monkeypatch.setattr(
        store,
        "get_all_facts_lightweight",
        lambda: [{"id": "json-1", "text": "I prefer fast memory settings", "category": "general"}],
    )

    assert manager.retrieve_relevant("memory") == "[Memory]\n- I prefer fast memory settings"


def test_memory_block_does_not_fallback_to_unmatched_facts():
    """Verify unrelated prompts do not receive arbitrary memory facts."""
    facts = [
        {"id": "pref-1", "text": "I prefer concise answers", "category": "general"},
        {"id": "proj-1", "text": "This project uses PySide6 widgets", "category": "project_context"},
    ]

    assert store._format_memory_block(facts, "weather tomorrow") == ""


def test_memory_block_allows_explicit_memory_inventory_query():
    """Verify users can still ask what memory contains."""
    facts = [
        {"id": "pref-1", "text": "I prefer concise answers", "category": "general"},
        {"id": "proj-1", "text": "This project uses PySide6 widgets", "category": "project_context"},
    ]

    assert store._format_memory_block(facts, "what do you remember about me?") == (
        "[Memory]\n- I prefer concise answers\n- This project uses PySide6 widgets"
    )


def test_retrieve_relevant_returns_empty_for_unmatched_query(monkeypatch):
    """Verify retrieval only injects memory when a fact earns relevance."""
    manager = store.MemoryManager.__new__(store.MemoryManager)
    _seed_router_attrs(manager)
    monkeypatch.setattr(
        store,
        "get_all_facts_lightweight",
        lambda: [{"id": "json-1", "text": "I prefer fast memory settings", "category": "general"}],
    )

    assert manager.retrieve_relevant("weather tomorrow") == ""


def test_retrieve_relevant_allows_explicit_memory_inventory_query(monkeypatch):
    """Verify direct memory-inspection prompts can return stored facts."""
    manager = store.MemoryManager.__new__(store.MemoryManager)
    _seed_router_attrs(manager)
    monkeypatch.setattr(
        store,
        "get_all_facts_lightweight",
        lambda: [{"id": "json-1", "text": "I prefer fast memory settings", "category": "general"}],
    )

    assert manager.retrieve_relevant("what do you remember about me?") == (
        "[Memory]\n- I prefer fast memory settings"
    )


def test_get_manager_is_thread_safe(monkeypatch):
    """Verify get manager is thread safe behavior."""
    created: list[object] = []
    release = threading.Event()

    class SlowMemoryManager:
        """Coordinate slow memory manager behavior."""
        def __init__(self):
            """Initialize the slow memory manager instance."""
            created.append(self)
            release.wait(timeout=2)

    monkeypatch.setattr(store, "_manager", None)
    monkeypatch.setattr(store, "_manager_lock", threading.Lock())
    monkeypatch.setattr(store, "MemoryManager", SlowMemoryManager)

    results: list[object] = []

    def call_get_manager():
        """Verify call get manager behavior."""
        results.append(store.get_manager())

    t1 = threading.Thread(target=call_get_manager)
    t2 = threading.Thread(target=call_get_manager)
    t1.start()
    time.sleep(0.05)
    t2.start()
    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert len(created) == 1
    assert len(results) == 2
    assert results[0] is results[1]


def test_manual_fact_writes_are_serialized(monkeypatch):
    """Verify manual fact writes are serialized behavior."""
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._ltm_lock = threading.RLock()
    _seed_router_attrs(manager)

    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def slow_fallback(_text, _category, _source, _project=None):
        """Verify slow fallback behavior."""
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1

    monkeypatch.setattr(manager, "_fallback_upsert", slow_fallback)

    t1 = threading.Thread(target=manager.add_fact_manual, args=("I prefer tests", "general"))
    t2 = threading.Thread(target=manager.add_fact_manual, args=("I use project facts", "project_context"))
    t1.start()
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert max_active == 1


def _fallback_manager():
    """Verify fallback manager behavior."""
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._ltm_lock = threading.RLock()
    return _seed_router_attrs(manager)


def test_save_memory_scopes_general_and_project(tmp_path, monkeypatch):
    """Verify save memory scopes general and project behavior."""
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    manager = _fallback_manager()
    try:
        store.set_active_project(None)
        assert manager.save_memory("I prefer concise answers", scope="general")["ok"]

        store.set_active_project("proj-1")
        result = manager.save_memory("This project uses PySide6 widgets", scope="project")
        assert result["ok"] and result["scope"] == "project" and result["project"] == "proj-1"

        facts = store._fallback_get_all_from_path()

        # General fact is visible regardless of active project.
        assert "concise" in store._format_memory_block(facts, "concise", project_id=None)
        assert "concise" in store._format_memory_block(facts, "concise", project_id="proj-1")

        # Project fact is hidden globally and in other projects, shown only in its own.
        assert "PySide6" not in store._format_memory_block(facts, "PySide6", project_id=None)
        assert "PySide6" not in store._format_memory_block(facts, "PySide6", project_id="proj-2")
        assert "PySide6" in store._format_memory_block(facts, "PySide6", project_id="proj-1")
    finally:
        store.set_active_project(None)


def test_save_memory_project_scope_falls_back_to_general_without_active_project(tmp_path, monkeypatch):
    """Verify save memory project scope falls back to general without active project behavior."""
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    manager = _fallback_manager()
    store.set_active_project(None)
    result = manager.save_memory("I work best in the mornings", scope="project")
    assert result["ok"] and result["scope"] == "general" and result["project"] is None


def test_memory_save_note_only_when_tool_offered():
    """Verify memory save note only when tool offered behavior."""
    from core.llm_clients import client

    base = "You are a concise desktop assistant."
    assert "memory_save tool" in client._with_memory_save_note(base, ["memory_save"])
    assert client._with_memory_save_note(base, ["web_search"]) == base
    assert client._with_memory_save_note(base, None) == base


def test_save_memory_default_scope_follows_active_project(tmp_path, monkeypatch):
    """Verify save memory default scope follows active project behavior."""
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    manager = _fallback_manager()
    try:
        store.set_active_project("proj-9")
        # No scope -> defaults to the active project (context-anchored).
        result = manager.save_memory("This project ships on Friday")
        assert result["ok"] and result["scope"] == "project" and result["project"] == "proj-9"
        # Explicit general promotes the fact out of the project even mid-project.
        promoted = manager.save_memory("I prefer concise answers", scope="general")
        assert promoted["ok"] and promoted["scope"] == "general" and promoted["project"] is None
    finally:
        store.set_active_project(None)


def test_add_explicit_fact_defaults_to_active_project(tmp_path, monkeypatch):
    """Verify add explicit fact defaults to active project behavior."""
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    manager = _fallback_manager()
    try:
        store.set_active_project("proj-7")
        manager.add_explicit_fact("the deadline is next Friday")
        stored = next(
            f for f in store._fallback_get_all_from_path() if "deadline" in f["text"]
        )
        assert stored.get("project") == "proj-7"
        assert stored.get("category") == "project_context"
    finally:
        store.set_active_project(None)


def test_update_fact_moves_scope_between_general_and_project(tmp_path, monkeypatch):
    """Verify update fact moves scope between general and project behavior."""
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    manager = _fallback_manager()
    store.set_active_project(None)
    manager.add_fact_manual("I like dark mode", project="")  # general
    fid = store._fallback_get_all_from_path()[0]["id"]

    # Move it into a specific project via the viewer's update path.
    manager.update_fact(fid, "I like dark mode", project="proj-3")
    moved = store._fallback_get_all_from_path()[0]
    assert moved["project"] == "proj-3"
    assert moved["category"] == "project_context"

    # And back to General.
    manager.update_fact(fid, "I like dark mode", project="")
    back = store._fallback_get_all_from_path()[0]
    assert (back.get("project") or "") == ""
    assert back["category"] == "general"


def test_memory_save_tool_executor(monkeypatch):
    """Verify memory save tool executor behavior."""
    from core.llm_clients import client

    captured: dict = {}

    class FakeManager:
        """Coordinate fake manager behavior."""
        def save_memory(self, text, scope="general"):
            """Verify save memory behavior."""
            captured["text"] = text
            captured["scope"] = scope
            return {"ok": True, "scope": scope, "project": None, "text": text}

    monkeypatch.setattr(store, "get_manager", lambda: FakeManager())
    out = client._execute_memory_save({"text": "I like green tea", "scope": "general"})
    assert "green tea" in out
    assert captured == {"text": "I like green tea", "scope": "general"}
