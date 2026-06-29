"""
config.py — Central configuration loaded from .env
"""
import os
import sys
from dotenv import dotenv_values, load_dotenv
from core import secret_store
from core.system.env_utils import (
    env_bool, env_file_access_mode, env_float, env_int, env_screenshot_mode,
    normalize_file_access_mode, parse_tool_modes, write_env_file,
)
from core.system.paths import REPO_ROOT, MODEL_FILE_ACCESS_DIR, MODEL_TOOLS_DIR
from core.settings_model import (
    AppSettings,
    ContextBudgets,
    ModelSettings,
    ProfileSettings,
    ToolTurnBudgets,
)
from core.prompt_i18n import (
    ASSISTANT_RESPONSE_LANGUAGE_NAMES as _ASSISTANT_RESPONSE_LANGUAGE_NAMES,
    CALLER_INTENT_TEMPLATES as _CALLER_INTENT_TEMPLATES,
    CALLER_TEMPLATE_FIELDS as _CALLER_TEMPLATE_FIELDS,
    DEFAULT_SYSTEM_PROMPT_UTILITY,
    LEGACY_TOOL_PROMPT_SENTENCE as _LEGACY_TOOL_PROMPT_SENTENCE,
    SYSTEM_PROMPT_UTILITY_TEMPLATES as _SYSTEM_PROMPT_UTILITY_TEMPLATES,
    assistant_language_instruction as _assistant_language_instruction,
    caller_intent_template as _caller_intent_template,
    default_caller_intents,
    intent_template_language as _intent_template_language,
    localize_chat_elaborate_prompt_if_default,
    localize_intent_if_default,
    localize_system_prompt_utility_if_default,
)

_ENV_FILE = REPO_ROOT / ".env"
_LOADED_DOTENV_KEYS: set[str] = set()


def _dotenv_keys() -> set[str]:
    """Return keys currently defined by the .env file."""
    if not _ENV_FILE.exists():
        return set()
    return {
        key
        for key, value in dotenv_values(_ENV_FILE).items()
        if key is not None and value is not None
    }


def _load_dotenv() -> None:
    """Load .env values and remember which keys are .env-managed."""
    global _LOADED_DOTENV_KEYS
    load_dotenv(_ENV_FILE)
    _LOADED_DOTENV_KEYS = _dotenv_keys()


def _reload_dotenv() -> None:
    """Reload .env and clear keys that were removed from the file."""
    global _LOADED_DOTENV_KEYS
    current_keys = _dotenv_keys()
    for key in _LOADED_DOTENV_KEYS - current_keys:
        os.environ.pop(key, None)
    load_dotenv(_ENV_FILE, override=True)
    _LOADED_DOTENV_KEYS = current_keys


_load_dotenv()

BASE_DIR = str(REPO_ROOT)

DEFAULT_TOOL_FILE_BLOCKED_GLOBS = [
    ".git/**",
    ".env*",
    "**/.env*",
    "**/*secret*",
    "**/*token*",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
]

# Static constants — not .env-configurable; do not belong in _load_config().
LATENCY_TARGET_MS = 1500
LATENCY_CEILING_MS = 3000

# --- Caller rows ---
# Each "caller" is a hotkey that shows the WASD intent picker.
# Stored as CALLER_COUNT + CALLER_N_* env vars (1-indexed).
def _platform_default_hotkey(windows: str, other: str) -> str:
    """Return a default hotkey that avoids common app-quit bindings off Windows."""
    return windows if sys.platform == "win32" else other


def _caller_default_hotkey(index: int) -> str:
    """Return the platform-aware built-in caller hotkey for a row index."""
    defaults = (
        ("ctrl+q", "ctrl+alt+space"),
        ("ctrl+shift+q", "ctrl+alt+shift+space"),
    )
    if 0 <= index < len(defaults):
        return _platform_default_hotkey(*defaults[index])
    return ""


_CALLER_DEFAULTS: list[dict] = [
    {
        "hotkey": _caller_default_hotkey(0),
        "label": "General",
        "paste_back": False,
        "custom_key": "s",
        "custom_label": "",
        "context_ambient": False,
        "context_documents": False,
        "context_tools": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",   # "off" | "auto" | "model"
        "context_clipboard": False,
        "file_access": "off",
        "intents": default_caller_intents(0),
    },
    {
        "hotkey": _caller_default_hotkey(1),
        "label": "Rewrite & Paste",
        "paste_back": True,
        "custom_key": "s",
        "custom_label": "",
        "context_ambient": False,
        "context_documents": False,
        "context_tools": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",   # "off" | "auto" | "model"
        "context_clipboard": False,
        "file_access": "off",
        "intents": default_caller_intents(1),
    },
]

_PROFILE_DEFAULTS: list[dict] = [
    {
        "id": "default",
        "label": "Default",
        "context": {
            "documents": "off",
            "browser": "off",
            "github": "off",
            "memory": "off",
            "screenshot": "off",
            "file_access": "off",
        },
        "tool": {
            "max_calls": 25,
            "max_result_chars": 120000,
            "max_total_chars": 300000,
        },
    },
    {
        "id": "fast",
        "label": "Fast",
        "context": {
            "documents": "off",
            "browser": "off",
            "github": "off",
            "memory": "off",
            "screenshot": "off",
            "file_access": "off",
        },
        "tool": {
            "max_calls": 8,
            "max_result_chars": 30000,
            "max_total_chars": 90000,
        },
    },
    {
        "id": "balanced",
        "label": "Balanced",
        "context": {
            "documents": "auto",
            "browser": "auto",
            "github": "off",
            "memory": "on",
            "screenshot": "off",
            "file_access": "off",
        },
        "tool": {
            "max_calls": 25,
            "max_result_chars": 120000,
            "max_total_chars": 300000,
        },
    },
    {
        "id": "deep",
        "label": "Deep",
        "context": {
            "documents": "model",
            "browser": "model",
            "github": "model",
            "memory": "model",
            "screenshot": "model",
            "file_access": "read",
        },
        "tool": {
            "max_calls": 50,
            "max_result_chars": 160000,
            "max_total_chars": 600000,
        },
    },
    {
        "id": "private",
        "label": "Private",
        "context": {
            "documents": "off",
            "browser": "off",
            "github": "off",
            "memory": "off",
            "screenshot": "off",
            "file_access": "off",
        },
        "tool": {
            "max_calls": 1,
            "max_result_chars": 8000,
            "max_total_chars": 12000,
        },
    },
    {
        "id": "coding",
        "label": "Coding",
        "context": {
            "documents": "model",
            "browser": "model",
            "github": "model",
            "memory": "on",
            "screenshot": "off",
            "file_access": "read",
        },
        "tool": {
            "max_calls": 50,
            "max_result_chars": 160000,
            "max_total_chars": 600000,
        },
    },
]


def _context_mode(value: str | None, default: str = "off") -> str:
    """Handle context mode for config."""
    mode = (value or default or "off").strip().lower()
    return mode if mode in {"off", "auto", "model"} else default


def _memory_context_mode(value: str | None, default: str = "on") -> str:
    """Normalize memory's context mode.

    Older settings used ``auto`` for front-loaded memory. Keep reading that
    value, but canonicalize it to the clearer ``on`` name for new saves.
    """
    fallback = (default or "on").strip().lower()
    if fallback == "auto":
        fallback = "on"
    if fallback not in {"off", "on", "model"}:
        fallback = "on"
    mode = (value or fallback).strip().lower()
    if mode == "auto":
        return "on"
    return mode if mode in {"off", "on", "model"} else fallback


def _file_permission_mode(value: str | None, default: str = "never") -> str:
    """Normalize live local-file mutation mode."""
    mode = (value or default or "never").strip().lower()
    return mode if mode in {"never", "ask", "auto"} else default


def _env_list(name: str, *, default: list[str] | None = None) -> list[str]:
    """Parse env lists from newlines, semicolons, or os.pathsep-delimited text."""
    value = os.getenv(name)
    if value is None:
        return list(default or [])
    parts: list[str] = []
    for chunk in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        for item in chunk.split(os.pathsep):
            item = item.strip()
            if item:
                parts.append(item)
    return parts


def _default_tool_file_roots() -> list[str]:
    """Return the default app-local folder for model file access."""
    try:
        MODEL_FILE_ACCESS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return [str(MODEL_FILE_ACCESS_DIR)]


def _file_access_default_from_tool_overrides(tools: dict[str, str]) -> str:
    """Infer the new per-caller file mode from legacy local-file tool overrides."""
    enabled = {name for name, mode in (tools or {}).items() if mode in {"on", "model"}}
    if enabled & {"create_file", "edit_file", "write_file"}:
        return normalize_file_access_mode(os.getenv("TOOL_FILE_MODE"), "ask")
    if enabled & {"list_files", "read_file"}:
        return "read"
    return "off"


def _profile_id(value: str | None, default: str = "default") -> str:
    """Normalize a profile id for env/config use."""
    import re

    text = str(value or default or "default").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-")
    return text or default


def _profile_from_template(template: dict, prefix: str | None = None) -> ProfileSettings:
    """Build a profile from a default template plus optional PROFILE_N_* overrides."""
    raw_id = os.getenv(f"{prefix}_ID") if prefix else str(template.get("id") or "default")
    profile_id = _profile_id(raw_id)
    label = (os.getenv(f"{prefix}_LABEL") if prefix else None) or str(
        template.get("label") or profile_id.title()
    )
    context_defaults = dict(template.get("context") or {})
    tool_defaults = dict(template.get("tool") or {})

    def env(name: str, default: str = "") -> str:
        return os.getenv(f"{prefix}_{name}", default) if prefix else default

    llm_provider = env("LLM_PROVIDER", LLM_PROVIDER)
    llm_model = env("LLM_MODEL", LLM_MODEL)
    llm_fallbacks = env("LLM_FALLBACKS", LLM_FALLBACKS)
    chat_provider = env("CHAT_LLM_PROVIDER", llm_provider)
    chat_model = env("CHAT_LLM_MODEL", llm_model)
    chat_fallbacks = env("CHAT_LLM_FALLBACKS", llm_fallbacks)
    vision_provider = env("VISION_LLM_PROVIDER", VISION_LLM_PROVIDER)
    vision_model = env("VISION_LLM_MODEL", VISION_LLM_MODEL)
    vision_fallbacks = env("VISION_LLM_FALLBACKS", VISION_LLM_FALLBACKS)
    memory_provider = env("MEMORY_LLM_PROVIDER", os.getenv("MEMORY_LLM_PROVIDER", llm_provider))
    memory_model = env("MEMORY_LLM_MODEL", os.getenv("MEMORY_LLM_MODEL", llm_model))
    memory_fallbacks = env("MEMORY_LLM_FALLBACKS", os.getenv("MEMORY_LLM_FALLBACKS", ""))

    documents_mode = _context_mode(
        env("CONTEXT_DOCUMENTS_MODE", str(context_defaults.get("documents") or "auto")),
        "auto",
    )
    browser_mode = _context_mode(
        env("CONTEXT_BROWSER_MODE", str(context_defaults.get("browser") or "off")),
        "off",
    )
    github_mode = _context_mode(
        env("CONTEXT_GITHUB_MODE", str(context_defaults.get("github") or "off")),
        "off",
    )
    memory_mode = _memory_context_mode(
        env("CONTEXT_MEMORY_MODE", str(context_defaults.get("memory") or "off")),
        "off",
    )
    screenshot_mode = env_screenshot_mode(
        f"{prefix}_CONTEXT_SCREENSHOT" if prefix else "__WISP_PROFILE_CONTEXT_SCREENSHOT__",
        str(context_defaults.get("screenshot") or "off"),
    )
    file_access = env_file_access_mode(
        f"{prefix}_FILE_ACCESS" if prefix else "__WISP_PROFILE_FILE_ACCESS__",
        str(context_defaults.get("file_access") or "off"),
    )

    context = ContextBudgets(
        browser_max_chars=env_int(
            f"{prefix}_CONTEXT_BROWSER_MAX_CHARS" if prefix else "__WISP_PROFILE_BROWSER_CHARS__",
            CONTEXT_BROWSER_MAX_CHARS,
        ),
        ambient_document_max_chars=env_int(
            f"{prefix}_CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS" if prefix else "__WISP_PROFILE_AMBIENT_DOC_CHARS__",
            CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS,
        ),
        tool_document_max_chars=env_int(
            f"{prefix}_CONTEXT_TOOL_DOCUMENT_MAX_CHARS" if prefix else "__WISP_PROFILE_TOOL_DOC_CHARS__",
            CONTEXT_TOOL_DOCUMENT_MAX_CHARS,
        ),
    )
    tools = ToolTurnBudgets(
        max_calls=env_int(
            f"{prefix}_TOOL_TURN_MAX_CALLS" if prefix else "__WISP_PROFILE_TOOL_CALLS__",
            int(tool_defaults.get("max_calls") or TOOL_TURN_MAX_CALLS),
        ),
        max_result_chars=env_int(
            f"{prefix}_TOOL_TURN_MAX_RESULT_CHARS" if prefix else "__WISP_PROFILE_TOOL_RESULT_CHARS__",
            int(tool_defaults.get("max_result_chars") or TOOL_TURN_MAX_RESULT_CHARS),
        ),
        max_total_chars=env_int(
            f"{prefix}_TOOL_TURN_MAX_TOTAL_CHARS" if prefix else "__WISP_PROFILE_TOOL_TOTAL_CHARS__",
            int(tool_defaults.get("max_total_chars") or TOOL_TURN_MAX_TOTAL_CHARS),
        ),
    )
    return ProfileSettings(
        profile_id=profile_id,
        label=label,
        llm=ModelSettings(llm_provider, llm_model, llm_fallbacks),
        chat_llm=ModelSettings(chat_provider, chat_model, chat_fallbacks),
        vision_llm=ModelSettings(vision_provider, vision_model, vision_fallbacks),
        memory_llm=ModelSettings(memory_provider, memory_model, memory_fallbacks),
        context=context,
        tools=tools,
        caller_defaults={
            "context_documents_mode": documents_mode,
            "context_browser_mode": browser_mode,
            "context_github_mode": github_mode,
            "context_memory_mode": memory_mode,
            "context_screenshot": screenshot_mode,
            "file_access": file_access,
        },
    )


def _load_profiles() -> list[ProfileSettings]:
    """Load built-in and optional env-defined profiles."""
    profiles = [_profile_from_template(template) for template in _PROFILE_DEFAULTS]
    by_id = {profile.profile_id: idx for idx, profile in enumerate(profiles)}
    count = env_int("PROFILE_COUNT", 0)
    for i in range(count):
        profile = _profile_from_template({}, prefix=f"PROFILE_{i + 1}")
        idx = by_id.get(profile.profile_id)
        if idx is None:
            by_id[profile.profile_id] = len(profiles)
            profiles.append(profile)
        else:
            profiles[idx] = profile
    return profiles


def _profile_map() -> dict[str, ProfileSettings]:
    """Return currently loaded profiles by id."""
    return {profile.profile_id: profile for profile in globals().get("PROFILES", [])}


def resolve_profile(profile_id: str | None = None) -> ProfileSettings:
    """Return a profile by id, falling back to the active/default profile."""
    profiles = _profile_map()
    active = _profile_id(str(globals().get("ACTIVE_PROFILE", "default") or "default"))
    requested = _profile_id(profile_id, active)
    if requested in profiles:
        return profiles[requested]
    if active in profiles:
        return profiles[active]
    if "default" in profiles:
        return profiles["default"]
    return next(iter(profiles.values()))


def effective_caller(caller: dict | None) -> dict:
    """Overlay a caller on top of its selected profile defaults."""
    row = dict(caller or {})
    try:
        profile = resolve_profile(str(row.get("profile") or ""))
    except Exception:
        return row
    defaults = dict(profile.caller_defaults)
    merged = {
        "profile": profile.profile_id,
        "context_documents_mode": defaults.get("context_documents_mode", "auto"),
        "context_browser_mode": defaults.get("context_browser_mode", "off"),
        "context_github_mode": defaults.get("context_github_mode", "off"),
        "context_memory_mode": defaults.get("context_memory_mode", "off"),
        "context_screenshot": defaults.get("context_screenshot", "off"),
        "file_access": defaults.get("file_access", "off"),
    }
    merged.update(row)
    return merged


def tool_turn_budget(profile_id: str | None = None) -> ToolTurnBudgets:
    """Return per-turn tool limits for a profile."""
    return resolve_profile(profile_id).tools


# Voice (push-to-talk) context defaults mirror the memory-only General caller.
_VOICE_DEFAULTS: dict = {
    "label": "Voice",
    "paste_back": False,
    "context_ambient": False,
    "context_clipboard": False,
    "context_documents_mode": "off",
    "context_browser_mode": "off",
    "context_github_mode": "off",
    "context_memory_mode": "off",
    "context_screenshot": "off",   # "off" | "auto" | "model"
    "file_access": "off",
}


_SNIP_DEFAULTS: dict = {
    "label": "Snip screen region",
    "paste_back": False,
    "context_ambient": False,
    "context_clipboard": False,
    "context_documents_mode": "off",
    "context_browser_mode": "off",
    "context_github_mode": "off",
    "context_memory_mode": "off",
    "context_screenshot": "off",
    "file_access": "off",
}


def _load_voice_caller() -> dict:
    """Read VOICE_CONTEXT_* env vars into a caller-shaped row for push-to-talk."""
    d = _VOICE_DEFAULTS
    profile_env = os.getenv("VOICE_PROFILE")
    profile_id = _profile_id(profile_env, ACTIVE_PROFILE)
    profile_defaults = (
        dict(resolve_profile(profile_id).caller_defaults)
        if profile_env or ACTIVE_PROFILE != "default"
        else {}
    )
    documents_mode = _context_mode(
        os.getenv("VOICE_CONTEXT_DOCUMENTS_MODE"),
        str(profile_defaults.get("context_documents_mode") or d["context_documents_mode"]),
    )
    browser_mode = _context_mode(
        os.getenv("VOICE_CONTEXT_BROWSER_MODE"),
        str(profile_defaults.get("context_browser_mode") or d["context_browser_mode"]),
    )
    github_mode = _context_mode(
        os.getenv("VOICE_CONTEXT_GITHUB_MODE"),
        str(profile_defaults.get("context_github_mode") or d["context_github_mode"]),
    )
    memory_mode = _memory_context_mode(
        os.getenv("VOICE_CONTEXT_MEMORY_MODE"),
        str(profile_defaults.get("context_memory_mode") or d["context_memory_mode"]),
    )
    return {
        "profile": profile_id,
        "hotkey": os.getenv("HOTKEY_VOICE", "f9"),
        "label": d["label"],
        "paste_back": False,
        "context_ambient": env_bool("VOICE_CONTEXT_AMBIENT", bool(d["context_ambient"])),
        "context_clipboard": env_bool("VOICE_CONTEXT_CLIPBOARD", bool(d["context_clipboard"])),
        "context_documents": documents_mode == "auto",
        "context_tools": any(m == "model" for m in (documents_mode, browser_mode, github_mode, memory_mode)),
        "context_documents_mode": documents_mode,
        "context_browser_mode": browser_mode,
        "context_github_mode": github_mode,
        "context_memory_mode": memory_mode,
        "context_screenshot": env_screenshot_mode(
            "VOICE_CONTEXT_SCREENSHOT",
            str(profile_defaults.get("context_screenshot") or d["context_screenshot"]),
        ),
        "file_access": env_file_access_mode(
            "VOICE_FILE_ACCESS",
            str(profile_defaults.get("file_access") or d.get("file_access") or "off"),
        ),
        "tools": parse_tool_modes(os.getenv("VOICE_TOOLS")),
    }


def _load_snip_caller() -> dict:
    """Read SNIP_CONTEXT_* env vars into a caller-shaped row for region snips."""
    d = _SNIP_DEFAULTS
    profile_env = os.getenv("SNIP_PROFILE")
    profile_id = _profile_id(profile_env, ACTIVE_PROFILE)
    profile_defaults = (
        dict(resolve_profile(profile_id).caller_defaults)
        if profile_env or ACTIVE_PROFILE != "default"
        else {}
    )
    profile_documents_mode = str(
        profile_defaults.get("context_documents_mode")
        or d["context_documents_mode"]
    )
    profile_browser_mode = str(
        profile_defaults.get("context_browser_mode")
        or d["context_browser_mode"]
    )
    profile_github_mode = str(
        profile_defaults.get("context_github_mode")
        or d["context_github_mode"]
    )
    profile_memory_mode = str(
        profile_defaults.get("context_memory_mode")
        or d["context_memory_mode"]
    )
    legacy_documents_key = "SNIP_CONTEXT_DOCUMENTS"
    legacy_tools_key = "SNIP_CONTEXT_TOOLS"
    legacy_documents = env_bool(legacy_documents_key, profile_documents_mode == "auto")
    legacy_tools = env_bool(
        legacy_tools_key,
        any(
            mode == "model"
            for mode in (
                profile_documents_mode,
                profile_browser_mode,
                profile_github_mode,
                profile_memory_mode,
            )
        ),
    )
    if os.getenv(legacy_documents_key) is not None or os.getenv(legacy_tools_key) is not None:
        default_documents_mode = "auto" if legacy_documents else ("model" if legacy_tools else "off")
    else:
        default_documents_mode = profile_documents_mode
    documents_mode = _context_mode(
        os.getenv("SNIP_CONTEXT_DOCUMENTS_MODE"),
        default_documents_mode,
    )
    browser_default = "model" if os.getenv(legacy_tools_key) is not None and legacy_tools else profile_browser_mode
    github_default = "model" if os.getenv(legacy_tools_key) is not None and legacy_tools else profile_github_mode
    browser_mode = _context_mode(os.getenv("SNIP_CONTEXT_BROWSER_MODE"), browser_default)
    github_mode = _context_mode(os.getenv("SNIP_CONTEXT_GITHUB_MODE"), github_default)
    memory_mode = _memory_context_mode(
        os.getenv("SNIP_CONTEXT_MEMORY_MODE"),
        profile_memory_mode,
    )
    tools = parse_tool_modes(os.getenv("SNIP_TOOLS"))
    return {
        "profile": profile_id,
        "hotkey": os.getenv("HOTKEY_SNIP", "ctrl+alt+q"),
        "label": d["label"],
        "paste_back": False,
        "context_ambient": env_bool("SNIP_CONTEXT_AMBIENT", bool(d["context_ambient"])),
        "context_clipboard": env_bool("SNIP_CONTEXT_CLIPBOARD", bool(d["context_clipboard"])),
        "context_documents": documents_mode == "auto",
        "context_tools": any(m == "model" for m in (documents_mode, browser_mode, github_mode, memory_mode)),
        "context_documents_mode": documents_mode,
        "context_browser_mode": browser_mode,
        "context_github_mode": github_mode,
        "context_memory_mode": memory_mode,
        "context_screenshot": "off",
        "file_access": env_file_access_mode(
            "SNIP_FILE_ACCESS",
            str(profile_defaults.get("file_access") or d.get("file_access") or "off"),
        ),
        "tools": tools,
    }


def _load_caller_rows() -> list[dict]:
    """Read CALLER_COUNT + CALLER_N_* env vars, fall back to _CALLER_DEFAULTS."""
    count = env_int("CALLER_COUNT", len(_CALLER_DEFAULTS))
    rows: list[dict] = []
    for i in range(count):
        n = i + 1
        default = _CALLER_DEFAULTS[i] if i < len(_CALLER_DEFAULTS) else {}
        profile_env = os.getenv(f"CALLER_{n}_PROFILE")
        profile_id = _profile_id(profile_env, ACTIVE_PROFILE)
        profile_defaults = (
            dict(resolve_profile(profile_id).caller_defaults)
            if profile_env or ACTIVE_PROFILE != "default"
            else {}
        )
        intent_count = env_int(f"CALLER_{n}_INTENT_COUNT", len(default.get("intents", [])))
        intents = []
        for j in range(intent_count):
            m = j + 1
            di = default.get("intents", [])
            d_intent = di[j] if j < len(di) else {}
            intent = {
                "key":    os.getenv(f"CALLER_{n}_INTENT_{m}_KEY",    d_intent.get("key", "")),
                "label":  os.getenv(f"CALLER_{n}_INTENT_{m}_LABEL",  d_intent.get("label", "")),
                "hint":   os.getenv(f"CALLER_{n}_INTENT_{m}_HINT",   d_intent.get("hint", "")),
                "prompt": os.getenv(f"CALLER_{n}_INTENT_{m}_PROMPT", d_intent.get("prompt", "")),
            }
            intents.append(localize_intent_if_default(i, j, intent, os.getenv("ASSISTANT_LANGUAGE", "")))
        profile_documents_mode = str(
            profile_defaults.get("context_documents_mode")
            or default.get("context_documents_mode")
            or "off"
        )
        profile_browser_mode = str(
            profile_defaults.get("context_browser_mode")
            or default.get("context_browser_mode")
            or "off"
        )
        profile_github_mode = str(
            profile_defaults.get("context_github_mode")
            or default.get("context_github_mode")
            or "off"
        )
        profile_memory_mode = str(
            profile_defaults.get("context_memory_mode")
            or default.get("context_memory_mode")
            or "on"
        )
        legacy_documents_key = f"CALLER_{n}_CONTEXT_DOCUMENTS"
        legacy_tools_key = f"CALLER_{n}_CONTEXT_TOOLS"
        legacy_documents = env_bool(
            legacy_documents_key,
            profile_documents_mode == "auto",
        )
        legacy_tools = env_bool(
            legacy_tools_key,
            any(
                mode == "model"
                for mode in (
                    profile_documents_mode,
                    profile_browser_mode,
                    profile_github_mode,
                    profile_memory_mode,
                )
            ),
        )
        if os.getenv(legacy_documents_key) is not None or os.getenv(legacy_tools_key) is not None:
            default_documents_mode = "auto" if legacy_documents else ("model" if legacy_tools else "off")
        else:
            default_documents_mode = profile_documents_mode
        documents_mode = _context_mode(
            os.getenv(f"CALLER_{n}_CONTEXT_DOCUMENTS_MODE"),
            str(default_documents_mode),
        )
        browser_default = "model" if os.getenv(legacy_tools_key) is not None and legacy_tools else profile_browser_mode
        github_default = "model" if os.getenv(legacy_tools_key) is not None and legacy_tools else profile_github_mode
        browser_mode = _context_mode(os.getenv(f"CALLER_{n}_CONTEXT_BROWSER_MODE"), browser_default)
        github_mode = _context_mode(os.getenv(f"CALLER_{n}_CONTEXT_GITHUB_MODE"), github_default)
        memory_mode = _memory_context_mode(
            os.getenv(f"CALLER_{n}_CONTEXT_MEMORY_MODE"),
            profile_memory_mode,
        )
        tools = parse_tool_modes(os.getenv(f"CALLER_{n}_TOOLS"))
        rows.append({
            "profile":    profile_id,
            "hotkey":     os.getenv(f"CALLER_{n}_HOTKEY",     _caller_default_hotkey(i) or default.get("hotkey", "")),
            "label":      os.getenv(f"CALLER_{n}_LABEL",      default.get("label", "")),
            "paste_back": env_bool(f"CALLER_{n}_PASTE_BACK", bool(default.get("paste_back", False))),
            "custom_key": os.getenv(f"CALLER_{n}_CUSTOM_KEY", default.get("custom_key", "s")),
            "custom_label": os.getenv(f"CALLER_{n}_CUSTOM_LABEL", default.get("custom_label", "")),
            "context_ambient": env_bool(f"CALLER_{n}_CONTEXT_AMBIENT", bool(default.get("context_ambient", False))),
            "context_documents": documents_mode == "auto",
            "context_tools": any(m == "model" for m in (documents_mode, browser_mode, github_mode, memory_mode)),
            "context_documents_mode": documents_mode,
            "context_browser_mode": browser_mode,
            "context_github_mode": github_mode,
            "context_memory_mode": memory_mode,
            "context_screenshot": env_screenshot_mode(
                f"CALLER_{n}_CONTEXT_SCREENSHOT",
                str(profile_defaults.get("context_screenshot") or default.get("context_screenshot", "off")),
            ),
            "context_clipboard": env_bool(f"CALLER_{n}_CONTEXT_CLIPBOARD", bool(default.get("context_clipboard", False))),
            "file_access": env_file_access_mode(
                f"CALLER_{n}_FILE_ACCESS",
                str(
                    profile_defaults.get("file_access")
                    or default.get("file_access")
                    or _file_access_default_from_tool_overrides(tools)
                ),
            ),
            "tools":      tools,
            "intents":    intents,
        })
    return rows


def _intent_context_toggle_keys(raw: str | None) -> str:
    """Normalize the overlay-local keys used to toggle context chips."""
    keys = []
    for ch in str(raw or "").strip():
        if ch.isspace() or ch in keys:
            continue
        keys.append(ch)
        if len(keys) >= 8:
            break
    for ch in "12345678":
        if ch not in keys:
            keys.append(ch)
        if len(keys) >= 8:
            break
    return "".join(keys) or "12345678"


def _load_config() -> None:
    """Assign all .env-backed module-level config vars. Call after load_dotenv()."""
    global GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
    global CARTESIA_API_KEY, ELEVENLABS_API_KEY, TTS_CUSTOM_API_KEY
    global CUSTOM_API_KEY, CUSTOM_BASE_URL
    global DEEPSEEK_API_KEY, OPENROUTER_API_KEY, MISTRAL_API_KEY
    global XAI_API_KEY, TOGETHER_API_KEY, CEREBRAS_API_KEY, ZAI_API_KEY
    global NVIDIA_API_KEY, SAMBANOVA_API_KEY, GITHUB_MODELS_API_KEY, HUGGINGFACE_API_KEY
    global CHUTES_API_KEY, VERCEL_API_KEY, FIREWORKS_API_KEY, COHERE_API_KEY, AI21_API_KEY, NEBIUS_API_KEY
    global LLM_PROVIDER, LLM_MODEL, LLM_FALLBACKS
    global CHAT_LLM_PROVIDER, CHAT_LLM_MODEL, CHAT_LLM_FALLBACKS, TOOL_LLM_MODEL
    global CHAT_REASONING_EFFORT, CHAT_TOOL_TRACE_UI
    global PLANNED_CHUNKING, PLANNED_CHUNKING_CHUNKS, PLANNED_CHUNKING_MIN_PROMPT_CHARS
    global VISION_LLM_PROVIDER, VISION_LLM_MODEL, VISION_LLM_FALLBACKS
    global ACTIVE_PROFILE, PROFILES
    global TTS_PROVIDER, CARTESIA_VOICE_ID
    global ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL
    global OPENAI_TTS_VOICE, OPENAI_TTS_MODEL
    global TTS_CUSTOM_BASE_URL, TTS_CUSTOM_VOICE, TTS_CUSTOM_MODEL, TTS_CUSTOM_SAMPLE_RATE
    global GPT_SOVITS_URL, GPT_SOVITS_REF_AUDIO_PATH, GPT_SOVITS_PROMPT_TEXT
    global GPT_SOVITS_PROMPT_LANG, GPT_SOVITS_TEXT_LANG, GPT_SOVITS_SAMPLE_RATE
    global GPT_SOVITS_TEXT_SPLIT_METHOD, GPT_SOVITS_BATCH_SIZE, GPT_SOVITS_SPEED_FACTOR
    global GPT_SOVITS_SEED, GPT_SOVITS_TIMEOUT_SECONDS
    global KOKORO_VOICE, KOKORO_LANG_CODE, KOKORO_DEVICE, KOKORO_SPEED, KOKORO_SAMPLE_RATE, KOKORO_SPLIT_PATTERN
    global THEME_MODE, DARK_MODE, ICON_AUTO_HIDE, START_ON_LOGIN, CHAT_AUTO_ELABORATE, CHAT_ELABORATE_PROMPT
    global TRUST_PRIVACY_MODE
    global APP_LANGUAGE, ASSISTANT_LANGUAGE
    global THEME_DARK_BG, THEME_DARK_SURFACE, THEME_DARK_TEXT, THEME_DARK_ACCENT
    global THEME_LIGHT_BG, THEME_LIGHT_SURFACE, THEME_LIGHT_TEXT, THEME_LIGHT_ACCENT
    global GITHUB_DEFAULT_CLIENT_ID, GITHUB_CLIENT_ID, GITHUB_OAUTH_SCOPES
    global COPILOT_CLI_URL, COPILOT_CLI_PATH
    global HOTKEY_ADD_CONTEXT, HOTKEY_CLEAR_CONTEXT, HOTKEY_SNIP, HOTKEY_READ_SELECTION_ALOUD, HOTKEY_VOICE, HOTKEY_DICTATE, DICTATE_MODE
    global VOICE_TRANSCRIPT_CONFIRM
    global INTENT_CONTEXT_TOGGLE_KEYS, INTENT_OVERLAY_TIMEOUT_MS
    global SNIP_CONTEXT_AMBIENT, SNIP_CONTEXT_DOCUMENTS, SNIP_CONTEXT_TOOLS, SNIP_CALLER
    global STT_MODEL, STT_COMPUTE_TYPE, STT_LANGUAGE, STT_BEAM_SIZE, STT_DEVICE
    global CALLER_ROWS, VOICE_CALLER
    global CONTEXT_BROWSER_MAX_CHARS, CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS, CONTEXT_TOOL_DOCUMENT_MAX_CHARS
    global TOOL_TURN_MAX_CALLS, TOOL_TURN_MAX_RESULT_CHARS, TOOL_TURN_MAX_TOTAL_CHARS
    global TOOL_PLUGIN_DIR, TOOL_GIT_ROOT, TOOL_FILE_ROOTS, TOOL_FILE_MODE, TOOL_FILE_BLOCKED_GLOBS
    global BUBBLE_WIDTH, BUBBLE_LINES, BUBBLE_FONT_SIZE, CHAT_FONT_SCALE
    global BUBBLE_COLOR, BUBBLE_TEXT_COLOR, BUBBLE_READ_WORD_COLOR
    global BUBBLE_SCROLL_ENABLED, BUBBLE_SCROLL_SNAP_ENABLED, BUBBLE_SCROLL_SNAP_DELAY_MS
    global ICON_SIZE, ICON_BACKSTOP_MS, BUBBLE_HIDE_DELAY_MS
    global BUBBLE_REVEAL_WPM, BUBBLE_HOLD_REVEAL_WPM
    global TTS_PLAYBACK_RATE, TTS_HOLD_PLAYBACK_RATE, TTS_VOLUME
    global TTS_READ_ALOUD_MIN_WORDS, TTS_READ_ALOUD_MAX_WORDS
    global STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS, STT_BACKGROUND_CHUNK_STEP_SECONDS
    global STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS, STT_BACKGROUND_CHUNK_OVERLAP_SECONDS
    global MEMORY_LLM_PROVIDER, MEMORY_LLM_MODEL, MEMORY_LLM_FALLBACKS, MEMORY_AUTO_CONSOLIDATE
    global MEMORY_CONSOLIDATION_INTERVAL, MEMORY_TOP_K, MEMORY_STM_TOKEN_BUDGET
    global SETTINGS
    global SYSTEM_PROMPT_UTILITY

    # --- API Keys ---
    GROQ_API_KEY      = secret_store.get_secret("GROQ_API_KEY")
    OPENAI_API_KEY    = secret_store.get_secret("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = secret_store.get_secret("ANTHROPIC_API_KEY")
    GOOGLE_API_KEY    = secret_store.get_secret("GOOGLE_API_KEY")
    CARTESIA_API_KEY  = secret_store.get_secret("CARTESIA_API_KEY")
    ELEVENLABS_API_KEY = secret_store.get_secret("ELEVENLABS_API_KEY")
    TTS_CUSTOM_API_KEY = secret_store.get_secret("TTS_CUSTOM_API_KEY")
    CUSTOM_API_KEY    = secret_store.get_secret("CUSTOM_API_KEY")
    CUSTOM_BASE_URL   = os.getenv("CUSTOM_BASE_URL", "")
    DEEPSEEK_API_KEY  = secret_store.get_secret("DEEPSEEK_API_KEY")
    OPENROUTER_API_KEY = secret_store.get_secret("OPENROUTER_API_KEY")
    MISTRAL_API_KEY   = secret_store.get_secret("MISTRAL_API_KEY")
    XAI_API_KEY       = secret_store.get_secret("XAI_API_KEY")
    TOGETHER_API_KEY  = secret_store.get_secret("TOGETHER_API_KEY")
    CEREBRAS_API_KEY  = secret_store.get_secret("CEREBRAS_API_KEY")
    ZAI_API_KEY       = secret_store.get_secret("ZAI_API_KEY")
    NVIDIA_API_KEY    = secret_store.get_secret("NVIDIA_API_KEY")
    SAMBANOVA_API_KEY = secret_store.get_secret("SAMBANOVA_API_KEY")
    GITHUB_MODELS_API_KEY = secret_store.get_secret("GITHUB_MODELS_API_KEY")
    HUGGINGFACE_API_KEY = secret_store.get_secret("HUGGINGFACE_API_KEY")
    CHUTES_API_KEY    = secret_store.get_secret("CHUTES_API_KEY")
    VERCEL_API_KEY    = secret_store.get_secret("VERCEL_API_KEY")
    FIREWORKS_API_KEY = secret_store.get_secret("FIREWORKS_API_KEY")
    COHERE_API_KEY    = secret_store.get_secret("COHERE_API_KEY")
    AI21_API_KEY      = secret_store.get_secret("AI21_API_KEY")
    NEBIUS_API_KEY    = secret_store.get_secret("NEBIUS_API_KEY")

    # --- LLM ---
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")    # groq | openai | anthropic | google | chatgpt | copilot | zai | custom
    LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-5.5")
    LLM_FALLBACKS = os.getenv("LLM_FALLBACKS", "")

    # --- Chat / elaborate LLM: combined with the Main LLM. ---
    # The chat window and overlay share one "brain", so these mirror LLM_*.
    # A legacy CHAT_LLM_* override in .env is intentionally ignored.
    CHAT_LLM_PROVIDER  = LLM_PROVIDER
    CHAT_LLM_MODEL     = LLM_MODEL
    CHAT_LLM_FALLBACKS = LLM_FALLBACKS

    # --- Tool model override (optional) ---
    # Empty = use the Main LLM model for tool calls. Set this only to force a
    # different model when tools are active (e.g. a more capable Anthropic model).
    TOOL_LLM_MODEL = os.getenv("TOOL_LLM_MODEL", "")
    CHAT_REASONING_EFFORT = os.getenv("CHAT_REASONING_EFFORT", "high").strip().lower()
    CHAT_TOOL_TRACE_UI = env_bool("CHAT_TOOL_TRACE_UI", False)
    PLANNED_CHUNKING = env_bool("WISP_PLANNED_CHUNKING", False)
    PLANNED_CHUNKING_CHUNKS = max(2, min(env_int("WISP_PLANNED_CHUNKING_CHUNKS", 3), 4))
    PLANNED_CHUNKING_MIN_PROMPT_CHARS = max(0, env_int("WISP_PLANNED_CHUNKING_MIN_PROMPT_CHARS", 80))

    # --- Vision LLM ---
    VISION_LLM_PROVIDER  = os.getenv("VISION_LLM_PROVIDER",  "")
    VISION_LLM_MODEL     = os.getenv("VISION_LLM_MODEL",     "")
    VISION_LLM_FALLBACKS = os.getenv("VISION_LLM_FALLBACKS", "")

    # --- TTS ---
    # cartesia | elevenlabs | openai | openai_compatible | gpt_sovits | kokoro | none
    TTS_PROVIDER      = os.getenv("TTS_PROVIDER", "none")
    CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")
    # ElevenLabs: voice id is optional (blank uses the account default voice).
    ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
    ELEVENLABS_MODEL    = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
    # OpenAI TTS reuses OPENAI_API_KEY. Streams raw PCM (24 kHz, 16-bit mono).
    OPENAI_TTS_VOICE  = os.getenv("OPENAI_TTS_VOICE", "alloy")
    OPENAI_TTS_MODEL  = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    # OpenAI-compatible endpoint (self-hosted Kokoro/LocalAI, Groq, etc.). Hits
    # the /audio/speech route with response_format=pcm; sample rate varies by
    # server so it is configurable.
    TTS_CUSTOM_BASE_URL    = os.getenv("TTS_CUSTOM_BASE_URL", "")
    TTS_CUSTOM_VOICE       = os.getenv("TTS_CUSTOM_VOICE", "")
    TTS_CUSTOM_MODEL       = os.getenv("TTS_CUSTOM_MODEL", "")
    TTS_CUSTOM_SAMPLE_RATE = env_int("TTS_CUSTOM_SAMPLE_RATE", 24000)
    # GPT-SoVITS local api_v2.py server. The app only calls the local HTTP API;
    # GPT-SoVITS itself is installed/run separately by the user.
    GPT_SOVITS_URL = os.getenv("GPT_SOVITS_URL", "http://127.0.0.1:9880")
    GPT_SOVITS_REF_AUDIO_PATH = os.getenv("GPT_SOVITS_REF_AUDIO_PATH", "")
    GPT_SOVITS_PROMPT_TEXT = os.getenv("GPT_SOVITS_PROMPT_TEXT", "")
    GPT_SOVITS_PROMPT_LANG = os.getenv("GPT_SOVITS_PROMPT_LANG", "en")
    GPT_SOVITS_TEXT_LANG = os.getenv("GPT_SOVITS_TEXT_LANG", "en")
    GPT_SOVITS_SAMPLE_RATE = env_int("GPT_SOVITS_SAMPLE_RATE", 32000)
    GPT_SOVITS_TEXT_SPLIT_METHOD = os.getenv("GPT_SOVITS_TEXT_SPLIT_METHOD", "cut5")
    GPT_SOVITS_BATCH_SIZE = env_int("GPT_SOVITS_BATCH_SIZE", 1)
    GPT_SOVITS_SPEED_FACTOR = env_float("GPT_SOVITS_SPEED_FACTOR", 1.0)
    GPT_SOVITS_SEED = env_int("GPT_SOVITS_SEED", -1)
    GPT_SOVITS_TIMEOUT_SECONDS = env_float("GPT_SOVITS_TIMEOUT_SECONDS", 120.0)
    # Kokoro local Python package. No server and no reference voice are needed.
    KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
    KOKORO_LANG_CODE = os.getenv("KOKORO_LANG_CODE", "a")
    KOKORO_DEVICE = os.getenv("KOKORO_DEVICE", "auto").strip().lower()
    KOKORO_SPEED = env_float("KOKORO_SPEED", 1.0)
    KOKORO_SAMPLE_RATE = env_int("KOKORO_SAMPLE_RATE", 24000)
    KOKORO_SPLIT_PATTERN = os.getenv("KOKORO_SPLIT_PATTERN", r"\n+")

    # --- App behaviour ---
    THEME_MODE            = os.getenv("THEME_MODE", "system")  # "dark" | "light" | "system"
    DARK_MODE             = env_bool("DARK_MODE", THEME_MODE == "dark")
    TRUST_PRIVACY_MODE    = env_bool("TRUST_PRIVACY_MODE", True)
    # Customizable theme templates. Each mode (light/dark) is a template of four
    # base colours; switching mode swaps the template. The rest of the palette
    # (cards, borders, buttons, hovers) is derived from these four, so the user
    # only picks four swatches per mode in Settings → App.
    THEME_DARK_BG         = os.getenv("THEME_DARK_BG",      "#1c1e26")  # window background
    THEME_DARK_SURFACE    = os.getenv("THEME_DARK_SURFACE", "#17181d")  # inputs / sunken fields
    THEME_DARK_TEXT       = os.getenv("THEME_DARK_TEXT",    "#e8e8f0")  # primary text
    THEME_DARK_ACCENT     = os.getenv("THEME_DARK_ACCENT",  "#8b87ff")  # highlight / focus / buttons
    THEME_LIGHT_BG        = os.getenv("THEME_LIGHT_BG",      "#f2f2f7")
    THEME_LIGHT_SURFACE   = os.getenv("THEME_LIGHT_SURFACE", "#ffffff")
    THEME_LIGHT_TEXT      = os.getenv("THEME_LIGHT_TEXT",    "#1c1c1e")
    THEME_LIGHT_ACCENT    = os.getenv("THEME_LIGHT_ACCENT",  "#5856d6")
    # ICON_AUTO_HIDE (formerly DOLL_AUTO_HIDE) — old key still honored for back-compat.
    ICON_AUTO_HIDE        = env_bool("ICON_AUTO_HIDE", env_bool("DOLL_AUTO_HIDE", False))
    START_ON_LOGIN        = env_bool("START_ON_LOGIN", False)
    APP_LANGUAGE          = os.getenv("APP_LANGUAGE", "")
    ASSISTANT_LANGUAGE    = os.getenv("ASSISTANT_LANGUAGE", "")
    CHAT_AUTO_ELABORATE   = env_bool("CHAT_AUTO_ELABORATE", False)
    CHAT_ELABORATE_PROMPT = localize_chat_elaborate_prompt_if_default(
        os.getenv("CHAT_ELABORATE_PROMPT", ""),
        ASSISTANT_LANGUAGE,
    )
    GITHUB_DEFAULT_CLIENT_ID = os.getenv("GITHUB_DEFAULT_CLIENT_ID", "")
    GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", GITHUB_DEFAULT_CLIENT_ID)
    GITHUB_OAUTH_SCOPES  = os.getenv("GITHUB_OAUTH_SCOPES", "repo read:user user:email")
    COPILOT_CLI_URL      = os.getenv("COPILOT_CLI_URL", "")
    COPILOT_CLI_PATH     = os.getenv("COPILOT_CLI_PATH", "")

    # --- Hotkeys ---
    HOTKEY_ADD_CONTEXT   = os.getenv("HOTKEY_ADD_CONTEXT",   "alt+q")
    HOTKEY_CLEAR_CONTEXT = os.getenv("HOTKEY_CLEAR_CONTEXT", "alt+w")
    HOTKEY_SNIP          = os.getenv("HOTKEY_SNIP",          "ctrl+alt+q")
    HOTKEY_READ_SELECTION_ALOUD = os.getenv("HOTKEY_READ_SELECTION_ALOUD", "f7")
    HOTKEY_VOICE         = os.getenv("HOTKEY_VOICE",         "f9")
    # Push-to-talk dictation: hold to transcribe straight into the focused text
    # field (no assistant). Set empty to disable. DICTATE_MODE: "raw" pastes the
    # transcript verbatim; "llm" runs it through the LLM for punctuation/cleanup.
    HOTKEY_DICTATE       = os.getenv("HOTKEY_DICTATE",       "f8")
    DICTATE_MODE         = os.getenv("DICTATE_MODE",         "raw")
    VOICE_TRANSCRIPT_CONFIRM = env_bool("VOICE_TRANSCRIPT_CONFIRM", False)
    INTENT_CONTEXT_TOGGLE_KEYS = _intent_context_toggle_keys(
        os.getenv("INTENT_CONTEXT_TOGGLE_KEYS", "12345678")
    )
    INTENT_OVERLAY_TIMEOUT_MS = max(
        0,
        env_int("INTENT_OVERLAY_TIMEOUT_MS", 60000),
    )

    SNIP_CONTEXT_AMBIENT   = env_bool("SNIP_CONTEXT_AMBIENT",   False)
    SNIP_CONTEXT_DOCUMENTS = env_bool("SNIP_CONTEXT_DOCUMENTS", False)
    SNIP_CONTEXT_TOOLS     = env_bool("SNIP_CONTEXT_TOOLS",     False)

    # --- Context and tool budgets ---
    CONTEXT_BROWSER_MAX_CHARS          = env_int("CONTEXT_BROWSER_MAX_CHARS",          12000)
    CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS = env_int("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", 8000)
    CONTEXT_TOOL_DOCUMENT_MAX_CHARS    = env_int("CONTEXT_TOOL_DOCUMENT_MAX_CHARS",    50000)
    TOOL_TURN_MAX_CALLS                = env_int("TOOL_TURN_MAX_CALLS",                25)
    TOOL_TURN_MAX_RESULT_CHARS         = env_int("TOOL_TURN_MAX_RESULT_CHARS",         120000)
    TOOL_TURN_MAX_TOTAL_CHARS          = env_int("TOOL_TURN_MAX_TOTAL_CHARS",          300000)

    # --- STT ---
    STT_MODEL        = os.getenv("STT_MODEL",        "base")
    STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
    STT_LANGUAGE     = os.getenv("STT_LANGUAGE",     "en")
    # cpu | cuda | auto. "auto" uses the GPU when an NVIDIA/CUDA device is
    # present and falls back to CPU otherwise. Resolved in core.stt._get_model.
    STT_DEVICE       = os.getenv("STT_DEVICE",        "auto").strip().lower()
    # Beam width for Whisper decoding. 5 is Whisper's own default and noticeably
    # more accurate than greedy (1); clamp to a sane range so a bad .env value
    # can't wedge the decoder.
    STT_BEAM_SIZE    = max(1, min(env_int("STT_BEAM_SIZE", 5), 10))
    TTS_READ_ALOUD_MIN_WORDS = max(1, env_int("TTS_READ_ALOUD_MIN_WORDS", 50))
    TTS_READ_ALOUD_MAX_WORDS = max(TTS_READ_ALOUD_MIN_WORDS, env_int("TTS_READ_ALOUD_MAX_WORDS", 110))
    STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS = max(
        1.0,
        env_float("STT_BACKGROUND_CHUNK_FIRST_TRIGGER_SECONDS", 15.0),
    )
    STT_BACKGROUND_CHUNK_STEP_SECONDS = max(
        1.0,
        env_float("STT_BACKGROUND_CHUNK_STEP_SECONDS", 10.0),
    )
    STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS = max(
        0.0,
        env_float("STT_BACKGROUND_CHUNK_LIVE_DELAY_SECONDS", 4.5),
    )
    STT_BACKGROUND_CHUNK_OVERLAP_SECONDS = max(
        0.0,
        env_float("STT_BACKGROUND_CHUNK_OVERLAP_SECONDS", 1.0),
    )

    # --- Profiles ---
    new_profiles = _load_profiles()
    if "PROFILES" in globals():
        PROFILES.clear()
        PROFILES.extend(new_profiles)
    else:
        PROFILES = new_profiles
    requested_profile = _profile_id(
        os.getenv("SETTINGS_PROFILE", os.getenv("ACTIVE_PROFILE", "default"))
    )
    active_profile = resolve_profile(requested_profile)
    ACTIVE_PROFILE = active_profile.profile_id
    LLM_PROVIDER = active_profile.llm.provider
    LLM_MODEL = active_profile.llm.model
    LLM_FALLBACKS = active_profile.llm.fallbacks
    CHAT_LLM_PROVIDER = active_profile.chat_llm.provider
    CHAT_LLM_MODEL = active_profile.chat_llm.model
    CHAT_LLM_FALLBACKS = active_profile.chat_llm.fallbacks
    VISION_LLM_PROVIDER = active_profile.vision_llm.provider
    VISION_LLM_MODEL = active_profile.vision_llm.model
    VISION_LLM_FALLBACKS = active_profile.vision_llm.fallbacks
    CONTEXT_BROWSER_MAX_CHARS = active_profile.context.browser_max_chars
    CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS = active_profile.context.ambient_document_max_chars
    CONTEXT_TOOL_DOCUMENT_MAX_CHARS = active_profile.context.tool_document_max_chars
    TOOL_TURN_MAX_CALLS = active_profile.tools.max_calls
    TOOL_TURN_MAX_RESULT_CHARS = active_profile.tools.max_result_chars
    TOOL_TURN_MAX_TOTAL_CHARS = active_profile.tools.max_total_chars

    # --- Caller rows ---
    new_rows = _load_caller_rows()
    if "CALLER_ROWS" in globals():
        CALLER_ROWS.clear()
        CALLER_ROWS.extend(new_rows)
    else:
        CALLER_ROWS = new_rows

    # --- Snip screen-region caller context ---
    new_snip = _load_snip_caller()
    if "SNIP_CALLER" in globals():
        SNIP_CALLER.clear()
        SNIP_CALLER.update(new_snip)
    else:
        SNIP_CALLER = new_snip

    # --- Voice (push-to-talk) caller ---
    new_voice = _load_voice_caller()
    if "VOICE_CALLER" in globals():
        VOICE_CALLER.clear()
        VOICE_CALLER.update(new_voice)
    else:
        VOICE_CALLER = new_voice

    TOOL_PLUGIN_DIR = os.getenv("TOOL_PLUGIN_DIR", str(MODEL_TOOLS_DIR))
    TOOL_GIT_ROOT   = os.getenv("TOOL_GIT_ROOT", BASE_DIR)
    TOOL_FILE_ROOTS = _env_list("TOOL_FILE_ROOTS", default=_default_tool_file_roots())
    TOOL_FILE_MODE = _file_permission_mode(os.getenv("TOOL_FILE_MODE"), "never")
    TOOL_FILE_BLOCKED_GLOBS = _env_list(
        "TOOL_FILE_BLOCKED_GLOBS",
        default=DEFAULT_TOOL_FILE_BLOCKED_GLOBS,
    )

    # --- UI sizes ---
    BUBBLE_WIDTH           = env_int("BUBBLE_WIDTH",      340)
    BUBBLE_LINES           = env_int("BUBBLE_LINES",      4)
    BUBBLE_FONT_SIZE       = max(6, min(env_int("BUBBLE_FONT_SIZE", 10), 32))
    # Chat-window text zoom multiplier (Ctrl+wheel / Ctrl+±). Clamped 0.7–2.5×.
    CHAT_FONT_SCALE        = max(0.7, min(env_float("CHAT_FONT_SCALE", 1.0), 2.5))
    BUBBLE_COLOR           = os.getenv("BUBBLE_COLOR",           "#1c1c24dc")
    BUBBLE_TEXT_COLOR      = os.getenv("BUBBLE_TEXT_COLOR",      "#e6e6e6")
    BUBBLE_READ_WORD_COLOR = os.getenv("BUBBLE_READ_WORD_COLOR", "#4da3ff")
    BUBBLE_SCROLL_ENABLED  = env_bool("BUBBLE_SCROLL_ENABLED", True)
    BUBBLE_SCROLL_SNAP_ENABLED = env_bool("BUBBLE_SCROLL_SNAP_ENABLED", True)
    BUBBLE_SCROLL_SNAP_DELAY_MS = env_int("BUBBLE_SCROLL_SNAP_DELAY_MS", 2500)
    # ICON_SIZE / ICON_BACKSTOP_MS (formerly DOLL_SIZE / DOLL_ICON_BACKSTOP_MS) —
    # old keys still honored for back-compat.
    ICON_SIZE              = env_int("ICON_SIZE",     env_int("DOLL_SIZE",             60))
    ICON_BACKSTOP_MS       = env_int("ICON_BACKSTOP_MS", env_int("DOLL_ICON_BACKSTOP_MS", 5000))
    BUBBLE_HIDE_DELAY_MS   = env_int("BUBBLE_HIDE_DELAY_MS",   3500)
    BUBBLE_REVEAL_WPM      = env_int("BUBBLE_REVEAL_WPM",      170)
    BUBBLE_HOLD_REVEAL_WPM = env_int("BUBBLE_HOLD_REVEAL_WPM", 480)
    TTS_PLAYBACK_RATE      = env_float("TTS_PLAYBACK_RATE",      1.0)
    TTS_HOLD_PLAYBACK_RATE = env_float("TTS_HOLD_PLAYBACK_RATE", 1.35)
    TTS_VOLUME             = env_float("TTS_VOLUME",             1.0)

    # --- Memory ---
    MEMORY_LLM_PROVIDER             = active_profile.memory_llm.provider
    MEMORY_LLM_MODEL                = active_profile.memory_llm.model
    MEMORY_LLM_FALLBACKS            = active_profile.memory_llm.fallbacks
    MEMORY_AUTO_CONSOLIDATE         = env_bool("MEMORY_AUTO_CONSOLIDATE", False)
    MEMORY_CONSOLIDATION_INTERVAL   = env_int("MEMORY_CONSOLIDATION_INTERVAL", 15)
    MEMORY_TOP_K                    = env_int("MEMORY_TOP_K", 3)
    MEMORY_STM_TOKEN_BUDGET         = env_int("MEMORY_STM_TOKEN_BUDGET", 4000)

    # --- System prompt ---
    # NOTE: do not claim tool access here. Tools are only sometimes offered
    # (per-caller modes), so the tool note is appended at request time by
    # core.llm_clients.client when tools are actually attached.
    SYSTEM_PROMPT_UTILITY = os.getenv(
        "SYSTEM_PROMPT_UTILITY",
        DEFAULT_SYSTEM_PROMPT_UTILITY,
    )
    # Migration: older saved prompts contain the static tool claim; strip it so
    # the model is no longer promised tools on queries that attach none.
    SYSTEM_PROMPT_UTILITY = SYSTEM_PROMPT_UTILITY.replace(
        _LEGACY_TOOL_PROMPT_SENTENCE, ""
    ).strip()
    SYSTEM_PROMPT_UTILITY = localize_system_prompt_utility_if_default(
        SYSTEM_PROMPT_UTILITY,
        ASSISTANT_LANGUAGE,
    )
    SETTINGS = AppSettings.from_config(globals())


_load_config()


def get_system_prompt() -> str:
    """Return system prompt."""
    language_instruction = _assistant_language_instruction(ASSISTANT_LANGUAGE)
    if not language_instruction:
        return SYSTEM_PROMPT_UTILITY
    return f"{SYSTEM_PROMPT_UTILITY}\n\n{language_instruction}"


def get_settings() -> AppSettings:
    """Return an immutable typed snapshot of the current runtime settings."""
    return SETTINGS


def reload() -> None:
    """Re-read .env and update every module-level variable in-place.

    Call this after writing a new .env so changes take effect without a restart.
    Note: UI size constants (BUBBLE_WIDTH, ICON_SIZE, …) require widget recreation
    and only fully apply after a restart; everything else is live.
    """
    secret_store.refresh_cache()
    _reload_dotenv()
    _load_config()


def set_chat_font_scale(scale: float) -> float:
    """Persist the chat-window text zoom multiplier to .env and update the global.

    Clamped to the same 0.7–2.5× range as the loader. Returns the stored value.
    """
    global CHAT_FONT_SCALE
    clamped = max(0.7, min(float(scale), 2.5))
    CHAT_FONT_SCALE = clamped
    try:
        write_env_file(_ENV_FILE, {"CHAT_FONT_SCALE": f"{clamped:.3f}".rstrip("0").rstrip(".")})
    except Exception:
        pass  # best-effort: the in-memory scale still applies this session
    return clamped
