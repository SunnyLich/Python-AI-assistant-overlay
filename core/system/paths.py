"""Canonical filesystem locations for the app."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME = "Wisp"


def _user_data_dir() -> Path:
    """
    Return a platform-appropriate, user-writable directory for settings and
    data that must survive rebuilds and updates.

    Linux:   $XDG_CONFIG_HOME/wisp  (~/.config/wisp)
    Windows: %APPDATA%\\Wisp
    macOS:   ~/Library/Application Support/Wisp
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / _APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        return xdg / _APP_NAME.lower()


def _bundle_root() -> Path:
    """
    Return the root of read-only bundled files (assets).
    In a PyInstaller onedir build this is sys._MEIPASS (_internal/).
    In a dev run it is the repo root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    """Return the repo root in dev mode; user data dir when frozen."""
    override = os.environ.get("WISP_REPO_ROOT")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
    if getattr(sys, "frozen", False):
        d = _user_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Public paths
# ---------------------------------------------------------------------------

REPO_ROOT = _repo_root()          # user-writable: .env, memory, plugins
_BUNDLE = _bundle_root()           # read-only bundled files

ASSETS_DIR       = _BUNDLE / "assets"
DOLL_ASSETS_DIR  = ASSETS_DIR / "doll"
FILLER_AUDIO_DIR = ASSETS_DIR / "filler"
# User-writable filler dir, used for TTS-baked clips in the user's chosen
# voice. Played in addition to the bundled clips. Survives PyInstaller
# rebuilds because it lives under user-data, not _MEIPASS.
USER_FILLER_AUDIO_DIR = _user_data_dir() / "filler"

# Single-instance lock. Lives in the user-data dir (not the repo / bundle) so a
# dev run (`python -m macos_py.supervisor.app`) and an installed build contend for the *same* lock —
# only one Wisp can be active at a time, regardless of how it was launched.
SINGLE_INSTANCE_LOCK = _user_data_dir() / "wisp.lock"

MEMORY_DIR        = REPO_ROOT / "memory"
AGENT_RUNS_DIR    = MEMORY_DIR / "agent_runs"
# Persisted chat history + project definitions (user-writable, gitignored).
CHATS_DIR         = REPO_ROOT / "chats"
PROJECTS_FILE     = CHATS_DIR / "projects.json"
CONVERSATIONS_FILE = CHATS_DIR / "conversations.json"
TOOLS_INSTALLED_DIR = REPO_ROOT / "tools" / "installed"
MODEL_TOOLS_DIR     = REPO_ROOT / "model_tools"
ADDONS_DIR          = REPO_ROOT / "addons"
PLUGINS_DIR         = ADDONS_DIR  # compatibility name for older plugin-manager callers
TOOL_KEYWORDS_FILE  = REPO_ROOT / "tool_keywords.json"
