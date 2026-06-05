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
