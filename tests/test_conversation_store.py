import importlib

import core.system.paths as paths
from core.conversation_store import store


def _isolate(tmp_path, monkeypatch):
    chats = tmp_path / "chats"
    monkeypatch.setattr(store, "CHATS_DIR", chats)
    monkeypatch.setattr(store, "PROJECTS_FILE", chats / "projects.json")
    monkeypatch.setattr(store, "CONVERSATIONS_FILE", chats / "conversations.json")


def test_general_project_always_present(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    projects = store.load_projects()
    assert projects[0]["id"] == store.GENERAL_PROJECT_ID
    assert projects[0]["name"] == "General"


def test_add_and_delete_project(tmp_path, monkeypatch):
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
    _isolate(tmp_path, monkeypatch)
    assert store.delete_project(store.GENERAL_PROJECT_ID) is False


def test_conversation_round_trip_and_title(tmp_path, monkeypatch):
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


def test_pin_and_rename_round_trip(tmp_path, monkeypatch):
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


def test_deleting_project_reassigns_conversations(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    proj = store.add_project("Temp")
    store.save_conversations([
        {"messages": [{"role": "user", "content": "hi"}], "project_id": proj["id"]},
    ])
    store.delete_project(proj["id"])
    loaded = store.load_conversations()
    assert loaded[0]["project_id"] == store.GENERAL_PROJECT_ID


def test_paths_expose_chats_locations():
    importlib.reload(paths)
    assert paths.CHATS_DIR.name == "chats"
    assert paths.PROJECTS_FILE.name == "projects.json"
