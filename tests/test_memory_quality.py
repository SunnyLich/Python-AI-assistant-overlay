import json
import sys
import threading
import time
import types

from core.memory_store import store
from unittest.mock import patch


def test_summarizer_rejects_task_request():
    assert not store._is_memory_worthy_fact("Please fix the settings dialog", source="summarizer")


def test_summarizer_keeps_durable_preference():
    assert store._is_memory_worthy_fact("I prefer concise answers", source="summarizer")


def test_explicit_can_keep_short_command_shaped_fact():
    assert store._is_memory_worthy_fact("fix grammar before pasting text", source="explicit")


def test_rejects_secrets():
    assert not store._is_memory_worthy_fact("My API key is sk-testabcdefghijklmnop", source="explicit")


def test_memory_manager_tolerates_storage_directory_creation_failure():
    with patch.object(store.os, "makedirs", side_effect=PermissionError("denied")), \
         patch.object(store.MemoryManager, "_init_chromadb", autospec=True, return_value=None), \
         patch.object(store.MemoryManager, "_sync_consolidation_timer", autospec=True, return_value=None):
        manager = store.MemoryManager()

    assert manager._collection is None
    assert manager._chroma_ok is False


def test_macos_memory_uses_json_fallback_when_safe_mode_is_disabled():
    with patch.object(store.macos_safety.sys, "platform", "darwin"), \
         patch.dict(store.macos_safety.os.environ, {"WISP_MACOS_SAFE_MODE": "0"}, clear=True), \
         patch.object(store.os, "makedirs", return_value=None), \
         patch.object(store.MemoryManager, "_sync_consolidation_timer", autospec=True, return_value=None):
        manager = store.MemoryManager()

    assert manager._collection is None
    assert manager._chroma_ok is False


def test_lightweight_fact_list_does_not_initialize_memory_manager(tmp_path, monkeypatch):
    fallback = tmp_path / "facts_fallback.json"
    fallback.write_text(
        json.dumps([
            {"id": "keep", "text": "I prefer concise answers", "archived": False},
            {"id": "skip", "text": "old fact", "archived": True},
        ]),
        encoding="utf-8",
    )

    class BrokenMemoryManager:
        def __init__(self):
            raise AssertionError("MemoryManager should not be constructed")

    class BrokenClient:
        def __init__(self, path):
            raise RuntimeError("chroma unavailable")

    monkeypatch.setattr(store, "_manager", None)
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))
    monkeypatch.setattr(store, "MemoryManager", BrokenMemoryManager)
    monkeypatch.setitem(sys.modules, "chromadb", types.SimpleNamespace(PersistentClient=BrokenClient))

    assert store.get_loaded_manager() is None
    assert store.get_all_facts_lightweight() == [
        {"id": "keep", "text": "I prefer concise answers", "archived": False}
    ]


def test_lightweight_fact_list_skips_chroma_import_when_db_is_empty(tmp_path, monkeypatch):
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir()
    db_path = chroma_dir / "chroma.sqlite3"
    con = store.sqlite3.connect(db_path)
    try:
        con.execute("create table embeddings (id integer primary key)")
        con.commit()
    finally:
        con.close()

    class BrokenMemoryManager:
        def __init__(self):
            raise AssertionError("MemoryManager should not be constructed")

    class BrokenChromaModule:
        def __getattr__(self, _name):
            raise AssertionError("chromadb should not be imported for an empty DB")

    monkeypatch.setattr(store, "_manager", None)
    monkeypatch.setattr(store, "_CHROMA_DIR", str(chroma_dir))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(store, "MemoryManager", BrokenMemoryManager)
    monkeypatch.setitem(sys.modules, "chromadb", BrokenChromaModule())

    assert store.get_all_facts_lightweight() == []


def test_lightweight_manual_fact_write_uses_json_store(tmp_path, monkeypatch):
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    assert store.add_fact_manual_lightweight("I prefer fast memory settings", "general") is True

    facts = json.loads(fallback.read_text(encoding="utf-8"))
    assert len(facts) == 1
    assert facts[0]["text"] == "I prefer fast memory settings"
    assert facts[0]["category"] == "general"
    assert facts[0]["source"] == "manual"


def test_get_all_facts_merges_json_and_chroma(monkeypatch):
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._chroma_ok = True
    manager._collection = types.SimpleNamespace(
        get=lambda **_kwargs: {
            "ids": ["chroma-1"],
            "documents": ["I use Chroma facts"],
            "metadatas": [{"category": "project_context", "source": "manual"}],
        }
    )
    monkeypatch.setattr(
        manager,
        "_fallback_get_all",
        lambda: [{"id": "json-1", "text": "I use JSON facts", "category": "general"}],
    )

    assert [fact["id"] for fact in manager.get_all_facts()] == ["json-1", "chroma-1"]


def test_retrieve_relevant_returns_json_facts_when_chroma_empty(monkeypatch):
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._chroma_ok = True
    manager._collection = types.SimpleNamespace(count=lambda: 0)
    manager._ctx_router_lock = threading.Lock()
    manager._ctx_router = None
    manager._ctx_router_fact_count = -1
    monkeypatch.setattr(
        store,
        "get_all_facts_lightweight",
        lambda: [{"id": "json-1", "text": "I prefer fast memory settings", "category": "general"}],
    )

    assert manager.retrieve_relevant("memory") == "[Memory]\n- I prefer fast memory settings"


def test_retrieve_relevant_skips_chroma_query_by_default(monkeypatch):
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._chroma_ok = True
    manager._collection = types.SimpleNamespace(
        count=lambda: 1,
        query=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Chroma query should not run")),
    )
    manager._ctx_router_lock = threading.Lock()
    manager._ctx_router = None
    manager._ctx_router_fact_count = -1
    monkeypatch.delenv("WISP_ENABLE_SEMANTIC_MEMORY_QUERY", raising=False)
    monkeypatch.setattr(
        store,
        "get_all_facts_lightweight",
        lambda: [{"id": "json-1", "text": "I prefer fast memory settings", "category": "general"}],
    )

    assert manager.retrieve_relevant("fast memory") == "[Memory]\n- I prefer fast memory settings"


def test_retrieve_relevant_semantic_query_is_opt_in(monkeypatch):
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._chroma_ok = True
    manager._collection = types.SimpleNamespace(
        count=lambda: 1,
        query=lambda **_kwargs: {
            "documents": [["I prefer semantic memory"]],
            "distances": [[0.1]],
        },
    )
    manager._ctx_router_lock = threading.Lock()
    manager._ctx_router = None
    manager._ctx_router_fact_count = -1
    monkeypatch.setenv("WISP_ENABLE_SEMANTIC_MEMORY_QUERY", "1")
    monkeypatch.setattr(store, "get_all_facts_lightweight", lambda: [])

    assert manager.retrieve_relevant("memory") == "[Memory]\n- I prefer semantic memory"


def test_get_manager_is_thread_safe(monkeypatch):
    created: list[object] = []
    release = threading.Event()

    class SlowMemoryManager:
        def __init__(self):
            created.append(self)
            release.wait(timeout=2)

    monkeypatch.setattr(store, "_manager", None)
    monkeypatch.setattr(store, "_manager_lock", threading.Lock())
    monkeypatch.setattr(store, "MemoryManager", SlowMemoryManager)

    results: list[object] = []

    def call_get_manager():
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
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._ltm_lock = threading.RLock()
    manager._chroma_ok = False
    manager._collection = None

    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def slow_fallback(_text, _category, _source, _project=None):
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
    manager = store.MemoryManager.__new__(store.MemoryManager)
    manager._chroma_ok = False
    manager._collection = None
    manager._ltm_lock = threading.RLock()
    return manager


def test_save_memory_scopes_general_and_project(tmp_path, monkeypatch):
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
    fallback = tmp_path / "facts_fallback.json"
    monkeypatch.setattr(store, "_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(store, "_FALLBACK_PATH", str(fallback))

    manager = _fallback_manager()
    store.set_active_project(None)
    result = manager.save_memory("I work best in the mornings", scope="project")
    assert result["ok"] and result["scope"] == "general" and result["project"] is None


def test_memory_save_tool_executor(monkeypatch):
    from core.llm_clients import client

    captured: dict = {}

    class FakeManager:
        def save_memory(self, text, scope="general"):
            captured["text"] = text
            captured["scope"] = scope
            return {"ok": True, "scope": scope, "project": None, "text": text}

    monkeypatch.setattr(store, "get_manager", lambda: FakeManager())
    out = client._execute_memory_save({"text": "I like green tea", "scope": "general"})
    assert "green tea" in out
    assert captured == {"text": "I like green tea", "scope": "general"}
