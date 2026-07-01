# -*- mode: python ; coding: utf-8 -*-
# PyInstaller specification for the Linux Wisp executable bundle.

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules


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

# LiteParse ships a loose pdfium shared library that its native extension
# loads at runtime; collect the package explicitly or the frozen app panics
# on parse. (See Wisp.spec for the Windows equivalent.)
LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")
LANGUAGE_TAGS_DATAS, LANGUAGE_TAGS_BINARIES, LANGUAGE_TAGS_HIDDENIMPORTS = collect_all("language_tags")
# faster_whisper's Silero VAD model (faster_whisper/assets/silero_vad_v6.onnx)
# is a loose data file the import scanner misses; STT loads it on every
# transcribe and the frozen app raises NO_SUCHFILE without it. The .onnx ships
# in faster_whisper's universal wheel, so this gap is identical on every OS.
FASTER_WHISPER_DATAS, FASTER_WHISPER_BINARIES, FASTER_WHISPER_HIDDENIMPORTS = collect_all("faster_whisper")
RUNTIME_WORKER_HIDDENIMPORTS = collect_submodules("runtime.workers")
BRAIN_HIDDENIMPORTS = collect_submodules("wisp_brain")
PIP_HIDDENIMPORTS = collect_submodules("pip")
MODULE_MODE_HIDDENIMPORTS = [
    "core.addon_host",
]
# Runtime-installed audio packages are invisible to PyInstaller analysis.
# Torch (used by Kokoro) imports timeit while it initializes.
OPTIONAL_RUNTIME_HIDDENIMPORTS = [
    "cProfile",
    "pickletools",
    "pstats",
    "timeit",
]
UV_BINARIES = [
    (str(path), "bin")
    for path in (
        ROOT / "bin" / "uv",
        ROOT / "tools" / "uv",
    )
    if path.exists()
]

block_cipher = None


a = Analysis(
    [str(ROOT / "runtime" / "supervisor" / "app.py")],
    pathex=[str(ROOT)],
    binaries=LITEPARSE_BINARIES + LANGUAGE_TAGS_BINARIES + FASTER_WHISPER_BINARIES + UV_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "addons" / "mcp_bridge"), "addons/mcp_bridge"),
        (str(ROOT / "ui" / "locales"), "ui/locales"),
        (str(ROOT / ".env.example"), "."),
        (str(ROOT / "pyproject.toml"), "."),
    ] + LITEPARSE_DATAS + LANGUAGE_TAGS_DATAS + FASTER_WHISPER_DATAS,
    hiddenimports=[
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
        # SSL support — required for any https:// request from the bundle
        "ssl",
        "_ssl",
        "certifi",
    ] + MODULE_MODE_HIDDENIMPORTS + OPTIONAL_RUNTIME_HIDDENIMPORTS + RUNTIME_WORKER_HIDDENIMPORTS + BRAIN_HIDDENIMPORTS + PIP_HIDDENIMPORTS + LITEPARSE_HIDDENIMPORTS + LANGUAGE_TAGS_HIDDENIMPORTS + FASTER_WHISPER_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "tmp_debug_agent",
    ],
    cipher=block_cipher,
    noarchive=False,
)

# Strip bundled libssl/libcrypto — venv and system versions can mismatch.
# The system's OpenSSL pair is always self-consistent.
a.binaries = [
    b for b in a.binaries
    if not (b[0].startswith("libssl") or b[0].startswith("libcrypto"))
]

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
