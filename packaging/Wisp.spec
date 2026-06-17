# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import PySide6
from PyInstaller.utils.hooks import collect_all

# LiteParse ships a loose pdfium.dll that its native extension loads at
# runtime; PyInstaller's dependency scanner does not pick it up, so collect
# the package's data/binaries explicitly or the frozen app panics on parse.
LITEPARSE_DATAS, LITEPARSE_BINARIES, LITEPARSE_HIDDENIMPORTS = collect_all("liteparse")

def _repo_root() -> Path:
    start = Path(SPECPATH).resolve()
    candidates = [start, *start.parents]
    for candidate in candidates:
        if (candidate / ".python-version").exists() and (candidate / "requirements.txt").exists():
            return candidate
    return start


ROOT = _repo_root()
PYSIDE6_ROOT = Path(PySide6.__file__).resolve().parent
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
    [str(ROOT / "macos_py" / "supervisor" / "app.py")],
    pathex=[str(ROOT)],
    binaries=QT_RUNTIME_DLLS + LITEPARSE_BINARIES + UV_BINARIES,
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "ui" / "locales"), "ui/locales"),
        (str(ROOT / ".env.example"), "."),
    ] + LITEPARSE_DATAS,
    hiddenimports=[
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "win32clipboard",
        "win32con",
        "win32gui",
        "win32process",
        "comtypes",
        "comtypes.client",
    ] + LITEPARSE_HIDDENIMPORTS,
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
    icon=str(ROOT / "assets" / "app.ico") if (ROOT / "assets" / "app.ico").exists() else None,
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
