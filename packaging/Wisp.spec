# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for the Windows Wisp executable bundle.

from pathlib import Path
import sys
import PySide6
from PyInstaller.utils.hooks import collect_all, collect_submodules

# LiteParse ships a loose pdfium.dll that its native extension loads at
# runtime; PyInstaller's dependency scanner does not pick it up, so collect
# the package's data/binaries explicitly or the frozen app panics on parse.
LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")
LANGUAGE_TAGS_DATAS, LANGUAGE_TAGS_BINARIES, LANGUAGE_TAGS_HIDDENIMPORTS = collect_all("language_tags")
# faster_whisper's Python modules are pulled into the archive by the import
# scanner, but its Silero VAD model (faster_whisper/assets/silero_vad_v6.onnx)
# is a loose data file the scanner misses; STT loads it on every transcribe and
# the frozen app raises NO_SUCHFILE without it.
FASTER_WHISPER_DATAS, FASTER_WHISPER_BINARIES, FASTER_WHISPER_HIDDENIMPORTS = collect_all("faster_whisper")

def _repo_root() -> Path:
    start = Path(SPECPATH).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / ".python-version").exists() and (candidate / "requirements.txt").exists():
            return candidate
    return start


ROOT = _repo_root()
sys.path.insert(0, str(ROOT / "runtime" / "brain"))
APP_ICON_ICO = ROOT / "assets" / "app.ico"
PYSIDE6_ROOT = Path(PySide6.__file__).resolve().parent
RUNTIME_WORKER_HIDDENIMPORTS = collect_submodules("runtime.workers")
BRAIN_HIDDENIMPORTS = collect_submodules("wisp_brain")
PIP_HIDDENIMPORTS = collect_submodules("pip")
MODULE_MODE_HIDDENIMPORTS = [
    "core.addon_host",
]
# Optional packages are installed after PyInstaller analysis and can therefore
# import stdlib modules that the frozen app did not otherwise need. Torch (used
# by Kokoro) imports timeit during startup, so keep it in the base bundle.
OPTIONAL_RUNTIME_HIDDENIMPORTS = [
    "cProfile",
    "pickletools",
    "pstats",
    "timeit",
]
QT_RUNTIME_DLLS = [
    (str(path), "PySide6")
    for pattern in (
        "concrt*.dll",
        "msvcp140*.dll",
        "vcruntime140*.dll",
    )
    for path in PYSIDE6_ROOT.glob(pattern)
]
UV_BINARIES = [
    (str(path), "bin")
    for path in (
        ROOT / "bin" / "uv.exe",
        ROOT / "tools" / "uv.exe",
    )
    if path.exists()
]


block_cipher = None


a = Analysis(
    [str(ROOT / "runtime" / "supervisor" / "app.py")],
    pathex=[str(ROOT)],
    binaries=QT_RUNTIME_DLLS + LITEPARSE_BINARIES + LANGUAGE_TAGS_BINARIES + FASTER_WHISPER_BINARIES + UV_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "addons" / "mcp_bridge"), "addons/mcp_bridge"),
        (str(ROOT / "ui" / "locales"), "ui/locales"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "pyproject.toml"), "."),
    ] + LITEPARSE_DATAS + LANGUAGE_TAGS_DATAS + FASTER_WHISPER_DATAS,
    hiddenimports=[
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "win32clipboard",
        "win32con",
        "win32gui",
        "win32process",
        "comtypes",
        "comtypes.client",
    ] + MODULE_MODE_HIDDENIMPORTS + OPTIONAL_RUNTIME_HIDDENIMPORTS + RUNTIME_WORKER_HIDDENIMPORTS + BRAIN_HIDDENIMPORTS + PIP_HIDDENIMPORTS + LITEPARSE_HIDDENIMPORTS + LANGUAGE_TAGS_HIDDENIMPORTS + FASTER_WHISPER_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "tmp_debug_agent",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wisp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(APP_ICON_ICO) if APP_ICON_ICO.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Wisp",
)
