from __future__ import annotations


class _Bubble:
    """Capture reply chunks sent to the speech bubble."""

    def __init__(self) -> None:
        self.chunks: list[tuple[str, bool]] = []

    def append_chunk(self, text: str, is_thought: bool = False) -> None:
        self.chunks.append((text, is_thought))


def test_reply_chunk_accepts_progress_metadata() -> None:
    """Verify ui.reply.chunk accepts supervisor progress metadata."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    bubble = _Bubble()
    host._ensure_bubble = lambda: bubble  # type: ignore[attr-defined]

    result = host._reply_chunk(text="Reading files...", is_progress=True)

    assert result == {"appended": len("Reading files..."), "is_progress": True}
    assert bubble.chunks == [("Reading files...", False)]


def test_chat_add_conversation_stamps_metadata() -> None:
    """Verify hotkey-created conversations carry display-only timestamps."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    persisted = []
    host._persist_conversations = lambda: persisted.append(True)  # type: ignore[attr-defined]

    result = host._chat_add_conversation(user="hi", assistant="hello")

    assert result == {"count": 1, "continued": False}
    assert persisted == [True]
    conv = host._all_conversations[0]
    assert conv["created_at"]
    assert conv["updated_at"] == conv["created_at"]
    assert conv["messages"][0]["created_at"] == conv["created_at"]
    assert conv["messages"][1]["created_at"] == conv["created_at"]


def test_chat_add_conversation_persists_file_context() -> None:
    """Verify hotkey-created conversations store file tool metadata."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._active_project_id = "general"
    host._all_conversations = []
    host._chat = None
    host._persist_conversations = lambda: None  # type: ignore[attr-defined]
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

    host._chat_add_conversation(user="create", assistant="done", file_context=file_context)

    assert host._all_conversations[0]["file_context"] == file_context


def test_chat_request_reuses_active_conversation_tool_context() -> None:
    """Verify chat sends stored tool policy when continuing a conversation."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._all_conversations = [
        {
            "messages": [{"role": "user", "content": "hi"}],
            "tool_context": {
                "allowed_tools": ["read_file", "edit_file"],
                "pinned_tools": ["read_file", "edit_file"],
                "file_access_mode": "ask",
            },
        }
    ]
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()
    emitted = []

    def emit(event, payload):
        emitted.append((event, payload))
        request_id = payload["request_id"]
        host._chat_done(request_id=request_id, text="ok", tool_context=payload["tool_context"])

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "continue"}]))

    assert emitted[0][0] == "ui.chat.request"
    assert emitted[0][1]["tool_context"]["file_access_mode"] == "ask"
    assert emitted[0][1]["tool_context"]["allowed_tools"] == ["read_file", "edit_file"]
    assert result == [
        {
            "type": "metadata",
            "file_context": [],
            "tool_context": emitted[0][1]["tool_context"],
        },
        {"type": "final", "text": "ok"},
    ]


def test_chat_stream_preserves_structured_thought_chunks() -> None:
    """Verify chat stream yields thought metadata instead of flattening it."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = None
    host._all_conversations = []
    host._chat_request_ids = iter([1])
    host._chat_streams = {}
    import threading

    host._chat_streams_lock = threading.Lock()

    def emit(_event, payload):
        request_id = payload["request_id"]
        host._chat_chunk(request_id=request_id, text="Thinking first.", is_thought=True)
        host._chat_chunk(request_id=request_id, text="Answer.")
        host._chat_done(request_id=request_id, text="Answer.")

    host.emit = emit  # type: ignore[method-assign]

    result = list(host._make_chat_send_fn()([{"role": "user", "content": "hi"}]))

    assert result == [
        {"type": "chunk", "text": "Thinking first.", "is_thought": True},
        "Answer.",
    ]


def test_active_history_includes_context_and_user_images() -> None:
    """Verify selected conversation replay includes ambient context and images."""
    from runtime.workers.ui_host import QtProtocolHost

    host = QtProtocolHost.__new__(QtProtocolHost)
    host._active_conversation_idx = 0
    host._active_project_id = "general"
    host._all_conversations = [
        {
            "project_id": "general",
            "context": "Original ambient context",
            "messages": [
                {"role": "user", "content": "what is this?", "image_base64": "abc123"},
                {"role": "assistant", "content": "a screenshot"},
            ],
        }
    ]

    history = host._chat_active_history()

    assert history["context"] == "Original ambient context"
    assert history["history"][0]["image_base64"] == "abc123"
