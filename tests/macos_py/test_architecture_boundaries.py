from __future__ import annotations

import ast
from pathlib import Path

from macos_py.boundaries import ROLE_FORBIDDEN_PREFIXES

ROOT = Path(__file__).resolve().parents[2]


def _top_level_imports(path: str) -> set[str]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0].lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0].lower())
    return names


def test_boundary_roles_are_defined_for_all_workers():
    assert {"supervisor", "native", "ui", "brain", "audio"} <= set(ROLE_FORBIDDEN_PREFIXES)


def test_ui_host_does_not_import_native_audio_or_ml_at_module_top():
    imports = _top_level_imports("macos_py/workers/ui_host.py")
    forbidden = [
        "appkit",
        "quartz",
        "sounddevice",
        "faster_whisper",
        "torch",
        "onnxruntime",
    ]
    for needle in forbidden:
        assert needle not in imports


def test_overlay_does_not_import_core_audio():
    source = (ROOT / "ui/overlay.py").read_text(encoding="utf-8")

    assert "from core import audio" not in source
    assert "import core.audio" not in source
    assert "set_tts_speed_boost" not in source


def test_native_host_does_not_import_qt_audio_or_ml_at_module_top():
    imports = _top_level_imports("macos_py/workers/native_host.py")
    forbidden = [
        "pyside6",
        "sounddevice",
        "faster_whisper",
        "torch",
        "onnxruntime",
    ]
    for needle in forbidden:
        assert needle not in imports


def test_audio_host_does_not_import_qt_or_appkit_at_module_top():
    imports = _top_level_imports("macos_py/workers/audio_host.py")
    forbidden = [
        "pyside6",
        "appkit",
        "quartz",
        "applicationservices",
    ]
    for needle in forbidden:
        assert needle not in imports


def test_audio_speed_boost_handler_does_not_import_core_audio():
    source = (ROOT / "macos_py/workers/audio_host.py").read_text(encoding="utf-8")

    assert "from core import audio\n" not in source
    assert "import core.audio" not in source
    assert "audio.set_tts_speed_boost" not in source


def test_supervisor_does_not_import_worker_stacks():
    imports = _top_level_imports("macos_py/supervisor/ipc.py")
    forbidden = [
        "pyside6",
        "appkit",
        "sounddevice",
        "faster_whisper",
        "torch",
    ]
    for needle in forbidden:
        assert needle not in imports
