"""Tests for test conversation store."""

import importlib

import core.system.paths as paths
from core.conversation_store import store


def _isolate(tmp_path, monkeypatch):
    """Verify isolate behavior."""
    chats = tmp_path / "chats"
    monkeypatch.setattr(store, "CHATS_DIR", chats)
    monkeypatch.setattr(store, "PROJECTS_FILE", chats / "projects.json")
    monkeypatch.setattr(store, "CONVERSATIONS_FILE", chats / "conversations.json")


def test_general_project_always_present(tmp_path, monkeypatch):
    """Verify general project always present behavior."""
    _isolate(tmp_path, monkeypatch)
    projects = store.load_projects()
    assert projects[0]["id"] == store.GENERAL_PROJECT_ID
    assert projects[0]["name"] == "General"


def test_add_and_delete_project(tmp_path, monkeypatch):
    """Verify add and delete project behavior."""
    _isolate(tmp_path, monkeypatch)
    proj = store.add_project("Wisp Redesign")
    assert proj["id"] != store.GENERAL_PROJECT_ID
    assert any(p["name"] == "Wisp Redesign" for p in store.load_projects())

    # Duplicate name returns the existing project rather than creating a second.
    again = store.add_project("wisp redesign")
    assert again["id"] == proj["id"]

    assert store.delete_project(proj["id"]) is True
    assert all(p["id"] != proj["id"] for p in store.load_projects())


def test_cannot_delete_general(tmp_path, monkeypatch):
    """Verify cannot delete general behavior."""
    _isolate(tmp_path, monkeypatch)
    assert store.delete_project(store.GENERAL_PROJECT_ID) is False


def test_conversation_round_trip_and_title(tmp_path, monkeypatch):
    """Verify conversation round trip and title behavior."""
    _isolate(tmp_path, monkeypatch)
    convs = [
        {
            "messages": [
                {"role": "user", "content": "How do I configure the overlay hotkey?"},
                {"role": "assistant", "content": "Open Settings → Hotkeys."},
            ],
            "context": "",
            "project_id": store.GENERAL_PROJECT_ID,
        },
        {"messages": [], "context": ""},  # empty -> not persisted
    ]
    store.save_conversations(convs)

    loaded = store.load_conversations()
    assert len(loaded) == 1
    assert loaded[0]["title"].startswith("How do I configure the overlay hotkey")
    assert loaded[0]["project_id"] == store.GENERAL_PROJECT_ID
    assert loaded[0]["id"]
    assert all(msg.get("created_at") for msg in loaded[0]["messages"])


def test_pin_and_rename_round_trip(tmp_path, monkeypatch):
    """Verify pin and rename round trip behavior."""
    _isolate(tmp_path, monkeypatch)
    store.save_conversations([
        {
            "messages": [{"role": "user", "content": "hello"}],
            "pinned": True,
            "title_override": "My pinned chat",
        },
    ])
    loaded = store.load_conversations()
    assert loaded[0]["pinned"] is True
    assert loaded[0]["title_override"] == "My pinned chat"


def test_file_context_round_trip(tmp_path, monkeypatch):
    """Verify local file tool metadata persists with conversations."""
    _isolate(tmp_path, monkeypatch)
    file_context = [
        {
            "tool": "create_file",
            "path": r"C:\repo\model_files\hello_world.py",
            "relative_path": "hello_world.py",
            "root": r"C:\repo\model_files",
            "ok": True,
            "message": "Created hello_world.py.",
        }
    ]
    store.save_conversations([
        {
            "messages": [{"role": "user", "content": "create a file"}],
            "file_context": file_context,
        },
    ])

    assert store.load_conversations()[0]["file_context"] == file_context


def test_tool_context_round_trip(tmp_path, monkeypatch):
    """Verify conversation tool policy metadata persists."""
    _isolate(tmp_path, monkeypatch)
    tool_context = {
        "allowed_tools": ["read_file", "edit_file"],
        "pinned_tools": ["read_file", "edit_file"],
        "file_access_mode": "ask",
    }
    store.save_conversations([
        {
            "messages": [{"role": "user", "content": "edit a file"}],
            "tool_context": tool_context,
        },
    ])

    assert store.load_conversations()[0]["tool_context"] == tool_context


def test_deleting_project_reassigns_conversations(tmp_path, monkeypatch):
    """Verify deleting project reassigns conversations behavior."""
    _isolate(tmp_path, monkeypatch)
    proj = store.add_project("Temp")
    store.save_conversations([
        {"messages": [{"role": "user", "content": "hi"}], "project_id": proj["id"]},
    ])
    store.delete_project(proj["id"])
    loaded = store.load_conversations()
    assert loaded[0]["project_id"] == store.GENERAL_PROJECT_ID


def test_paths_expose_chats_locations():
    """Verify paths expose chats locations behavior."""
    importlib.reload(paths)
    assert paths.CHATS_DIR.name == "chats"
    assert paths.PROJECTS_FILE.name == "projects.json"
