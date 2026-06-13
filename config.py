"""
config.py — Central configuration loaded from .env
"""
import os
from dotenv import load_dotenv
from core import secret_store
from core.system.env_utils import (
    env_bool, env_float, env_int, env_screenshot_mode, parse_tool_modes,
)
from core.system.paths import FILLER_AUDIO_DIR as DEFAULT_FILLER_AUDIO_DIR
from core.system.paths import USER_FILLER_AUDIO_DIR as DEFAULT_USER_FILLER_AUDIO_DIR
from core.system.paths import REPO_ROOT, MODEL_TOOLS_DIR

_ENV_FILE = REPO_ROOT / ".env"
load_dotenv(_ENV_FILE)

BASE_DIR = str(REPO_ROOT)

# Static constants — not .env-configurable; do not belong in _load_config().
FILLER_AUDIO_DIR = str(DEFAULT_FILLER_AUDIO_DIR)
# Writable companion dir that holds filler clips synthesised in the user's
# chosen TTS voice. Loaded alongside the bundled WAVs.
USER_FILLER_AUDIO_DIR = str(DEFAULT_USER_FILLER_AUDIO_DIR)
FILLER_MAX_DURATION_MS = 1000   # filler clips must be < 1s
LATENCY_TARGET_MS = 1500
LATENCY_CEILING_MS = 3000

# --- Caller rows ---
# Each "caller" is a hotkey that shows the WASD intent picker.
# Stored as CALLER_COUNT + CALLER_N_* env vars (1-indexed).
_CALLER_DEFAULTS: list[dict] = [
    {
        "hotkey": "ctrl+q",
        "label": "General",
        "paste_back": False,
        "custom_key": "s",
        "context_ambient": True,
        "context_documents": True,
        "context_tools": False,
        "context_documents_mode": "auto",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "auto",
        "context_screenshot": "off",   # "off" | "auto" | "model"
        "context_clipboard": False,
        "intents": [
            {"key": "w", "label": "What is this?",      "hint": "Quick explanation, plain English",  "prompt": "What is this? Give me a clear, plain-English explanation in 2-3 sentences."},
            {"key": "a", "label": "Explain simply",     "hint": "ELI5 — no jargon",                 "prompt": "Explain this as simply as possible. Assume I have no technical background whatsoever."},
            {"key": "d", "label": "How do I fix this?", "hint": "Debug, fix, or rewrite it",         "prompt": "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now."},
        ],
    },
    {
        "hotkey": "ctrl+shift+q",
        "label": "Rewrite & Paste",
        "paste_back": True,
        "custom_key": "s",
        "context_ambient": True,
        "context_documents": False,
        "context_tools": False,
        "context_documents_mode": "off",
        "context_browser_mode": "off",
        "context_github_mode": "off",
        "context_memory_mode": "off",
        "context_screenshot": "off",   # "off" | "auto" | "model"
        "context_clipboard": False,
        "intents": [
            {"key": "w", "label": "Fix grammar",  "hint": "Correct spelling and grammar",     "prompt": "Fix the grammar and spelling of the following text. Output ONLY the corrected text."},
            {"key": "a", "label": "Simplify",     "hint": "Make it easier to read",           "prompt": "Simplify the following text for a general audience. Output ONLY the simplified text."},
            {"key": "d", "label": "Improve tone", "hint": "Polish for clarity and style",     "prompt": "Rewrite the following text to sound more professional and polished. Output ONLY the rewritten text."},
        ],
    },
]


def _context_mode(value: str | None, default: str = "off") -> str:
    mode = (value or default or "off").strip().lower()
    return mode if mode in {"off", "auto", "model"} else default


# The static tool sentence older builds baked into the default system prompt
# (and therefore into saved .env prompts). Stripped on load — see
# SYSTEM_PROMPT_UTILITY in _load_config().
_LEGACY_TOOL_PROMPT_SENTENCE = (
    "You have access to a web_search tool and a get_context tool. "
    "Use web_search for current information and use get_context with a URL "
    "when the user asks about a specific page. Never print or simulate tool "
    "calls in the reply."
)


# Voice (push-to-talk) context defaults mirror the General caller so existing
# behavior — voice used to borrow caller 1's config — is unchanged by default.
_VOICE_DEFAULTS: dict = {
    "label": "Voice",
    "paste_back": False,
    "context_ambient": True,
    "context_clipboard": False,
    "context_documents_mode": "auto",
    "context_browser_mode": "off",
    "context_github_mode": "off",
    "context_memory_mode": "auto",
    "context_screenshot": "off",   # "off" | "auto" | "model"
}


def _load_voice_caller() -> dict:
    """Read VOICE_CONTEXT_* env vars into a caller-shaped row for push-to-talk."""
    d = _VOICE_DEFAULTS
    documents_mode = _context_mode(
        os.getenv("VOICE_CONTEXT_DOCUMENTS_MODE"), str(d["context_documents_mode"])
    )
    browser_mode = _context_mode(os.getenv("VOICE_CONTEXT_BROWSER_MODE"), str(d["context_browser_mode"]))
    github_mode = _context_mode(os.getenv("VOICE_CONTEXT_GITHUB_MODE"), str(d["context_github_mode"]))
    memory_mode = _context_mode(os.getenv("VOICE_CONTEXT_MEMORY_MODE"), str(d["context_memory_mode"]))
    return {
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
        "context_screenshot": env_screenshot_mode("VOICE_CONTEXT_SCREENSHOT", str(d["context_screenshot"])),
        "tools": parse_tool_modes(os.getenv("VOICE_TOOLS")),
    }


def _load_caller_rows() -> list[dict]:
    """Read CALLER_COUNT + CALLER_N_* env vars, fall back to _CALLER_DEFAULTS."""
    count = env_int("CALLER_COUNT", len(_CALLER_DEFAULTS))
    rows: list[dict] = []
    for i in range(count):
        n = i + 1
        default = _CALLER_DEFAULTS[i] if i < len(_CALLER_DEFAULTS) else {}
        intent_count = env_int(f"CALLER_{n}_INTENT_COUNT", len(default.get("intents", [])))
        intents = []
        for j in range(intent_count):
            m = j + 1
            di = default.get("intents", [])
            d_intent = di[j] if j < len(di) else {}
            intents.append({
                "key":    os.getenv(f"CALLER_{n}_INTENT_{m}_KEY",    d_intent.get("key", "")),
                "label":  os.getenv(f"CALLER_{n}_INTENT_{m}_LABEL",  d_intent.get("label", "")),
                "hint":   os.getenv(f"CALLER_{n}_INTENT_{m}_HINT",   d_intent.get("hint", "")),
                "prompt": os.getenv(f"CALLER_{n}_INTENT_{m}_PROMPT", d_intent.get("prompt", "")),
            })
        legacy_documents = env_bool(
            f"CALLER_{n}_CONTEXT_DOCUMENTS",
            bool(default.get("context_documents", True)),
        )
        legacy_tools = env_bool(
            f"CALLER_{n}_CONTEXT_TOOLS",
            bool(default.get("context_tools", False)),
        )
        default_documents_mode = "auto" if legacy_documents else ("model" if legacy_tools else "off")
        documents_mode = _context_mode(
            os.getenv(f"CALLER_{n}_CONTEXT_DOCUMENTS_MODE"),
            str(default_documents_mode),
        )
        browser_default = "model" if legacy_tools else str(default.get("context_browser_mode") or "off")
        github_default = "model" if legacy_tools else str(default.get("context_github_mode") or "off")
        browser_mode = _context_mode(os.getenv(f"CALLER_{n}_CONTEXT_BROWSER_MODE"), browser_default)
        github_mode = _context_mode(os.getenv(f"CALLER_{n}_CONTEXT_GITHUB_MODE"), github_default)
        memory_mode = _context_mode(
            os.getenv(f"CALLER_{n}_CONTEXT_MEMORY_MODE"),
            str(default.get("context_memory_mode") or "auto"),
        )
        rows.append({
            "hotkey":     os.getenv(f"CALLER_{n}_HOTKEY",     default.get("hotkey", "")),
            "label":      os.getenv(f"CALLER_{n}_LABEL",      default.get("label", "")),
            "paste_back": env_bool(f"CALLER_{n}_PASTE_BACK", bool(default.get("paste_back", False))),
            "custom_key": os.getenv(f"CALLER_{n}_CUSTOM_KEY", default.get("custom_key", "s")),
            "context_ambient": env_bool(f"CALLER_{n}_CONTEXT_AMBIENT", bool(default.get("context_ambient", True))),
            "context_documents": documents_mode == "auto",
            "context_tools": any(m == "model" for m in (documents_mode, browser_mode, github_mode, memory_mode)),
            "context_documents_mode": documents_mode,
            "context_browser_mode": browser_mode,
            "context_github_mode": github_mode,
            "context_memory_mode": memory_mode,
            "context_screenshot": env_screenshot_mode(f"CALLER_{n}_CONTEXT_SCREENSHOT", default.get("context_screenshot", "off")),
            "context_clipboard": env_bool(f"CALLER_{n}_CONTEXT_CLIPBOARD", bool(default.get("context_clipboard", False))),
            "tools":      parse_tool_modes(os.getenv(f"CALLER_{n}_TOOLS")),
            "intents":    intents,
        })
    return rows


def _load_config() -> None:
    """Assign all .env-backed module-level config vars. Call after load_dotenv()."""
    global GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
    global CARTESIA_API_KEY, ELEVENLABS_API_KEY
    global CUSTOM_API_KEY, CUSTOM_BASE_URL
    global DEEPSEEK_API_KEY, OPENROUTER_API_KEY, MISTRAL_API_KEY
    global XAI_API_KEY, TOGETHER_API_KEY, CEREBRAS_API_KEY
    global LLM_PROVIDER, LLM_MODEL, LLM_FALLBACKS
    global CHAT_LLM_PROVIDER, CHAT_LLM_MODEL, CHAT_LLM_FALLBACKS, TOOL_LLM_MODEL
    global VISION_LLM_PROVIDER, VISION_LLM_MODEL, VISION_LLM_FALLBACKS
    global TTS_PROVIDER, CARTESIA_VOICE_ID
    global THEME_MODE, DARK_MODE, ICON_AUTO_HIDE, CHAT_AUTO_ELABORATE, CHAT_ELABORATE_PROMPT
    global THEME_DARK_BG, THEME_DARK_SURFACE, THEME_DARK_TEXT, THEME_DARK_ACCENT
    global THEME_LIGHT_BG, THEME_LIGHT_SURFACE, THEME_LIGHT_TEXT, THEME_LIGHT_ACCENT
    global GITHUB_DEFAULT_CLIENT_ID, GITHUB_CLIENT_ID, GITHUB_OAUTH_SCOPES
    global COPILOT_CLI_URL, COPILOT_CLI_PATH
    global HOTKEY_ADD_CONTEXT, HOTKEY_CLEAR_CONTEXT, HOTKEY_SNIP, HOTKEY_VOICE
    global SNIP_CONTEXT_AMBIENT, SNIP_CONTEXT_DOCUMENTS, SNIP_CONTEXT_TOOLS
    global STT_MODEL, STT_COMPUTE_TYPE, STT_LANGUAGE
    global CALLER_ROWS, VOICE_CALLER
    global CONTEXT_BROWSER_MAX_CHARS, CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS, CONTEXT_TOOL_DOCUMENT_MAX_CHARS
    global TOOL_PLUGIN_DIR, TOOL_GIT_ROOT
    global BUBBLE_WIDTH, BUBBLE_LINES, BUBBLE_COLOR, BUBBLE_TEXT_COLOR, BUBBLE_READ_WORD_COLOR
    global ICON_SIZE, ICON_BACKSTOP_MS, BUBBLE_HIDE_DELAY_MS
    global BUBBLE_REVEAL_WPM, BUBBLE_HOLD_REVEAL_WPM
    global TTS_PLAYBACK_RATE, TTS_HOLD_PLAYBACK_RATE
    global MEMORY_LLM_PROVIDER, MEMORY_LLM_MODEL, MEMORY_LLM_FALLBACKS, MEMORY_AUTO_CONSOLIDATE
    global MEMORY_CONSOLIDATION_INTERVAL, MEMORY_TOP_K, MEMORY_RELEVANCE_MAX_DISTANCE, MEMORY_STM_TOKEN_BUDGET
    global SYSTEM_PROMPT_UTILITY

    # --- API Keys ---
    GROQ_API_KEY      = secret_store.get_secret("GROQ_API_KEY")
    OPENAI_API_KEY    = secret_store.get_secret("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = secret_store.get_secret("ANTHROPIC_API_KEY")
    GOOGLE_API_KEY    = secret_store.get_secret("GOOGLE_API_KEY")
    CARTESIA_API_KEY  = secret_store.get_secret("CARTESIA_API_KEY")
    ELEVENLABS_API_KEY = secret_store.get_secret("ELEVENLABS_API_KEY")
    CUSTOM_API_KEY    = secret_store.get_secret("CUSTOM_API_KEY")
    CUSTOM_BASE_URL   = os.getenv("CUSTOM_BASE_URL", "")
    DEEPSEEK_API_KEY  = secret_store.get_secret("DEEPSEEK_API_KEY")
    OPENROUTER_API_KEY = secret_store.get_secret("OPENROUTER_API_KEY")
    MISTRAL_API_KEY   = secret_store.get_secret("MISTRAL_API_KEY")
    XAI_API_KEY       = secret_store.get_secret("XAI_API_KEY")
    TOGETHER_API_KEY  = secret_store.get_secret("TOGETHER_API_KEY")
    CEREBRAS_API_KEY  = secret_store.get_secret("CEREBRAS_API_KEY")

    # --- LLM ---
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "chatgpt")    # groq | openai | anthropic | google | chatgpt | copilot
    LLM_MODEL    = os.getenv("LLM_MODEL", "gpt-5.4")
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

    # --- Vision LLM ---
    VISION_LLM_PROVIDER  = os.getenv("VISION_LLM_PROVIDER",  "")
    VISION_LLM_MODEL     = os.getenv("VISION_LLM_MODEL",     "")
    VISION_LLM_FALLBACKS = os.getenv("VISION_LLM_FALLBACKS", "")

    # --- TTS ---
    TTS_PROVIDER      = os.getenv("TTS_PROVIDER", "none")    # cartesia | elevenlabs | none
    CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

    # --- App behaviour ---
    THEME_MODE            = os.getenv("THEME_MODE", "system")  # "dark" | "light" | "system"
    DARK_MODE             = env_bool("DARK_MODE", THEME_MODE == "dark")
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
    CHAT_AUTO_ELABORATE   = env_bool("CHAT_AUTO_ELABORATE", False)
    CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")
    GITHUB_DEFAULT_CLIENT_ID = os.getenv("GITHUB_DEFAULT_CLIENT_ID", "")
    GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", GITHUB_DEFAULT_CLIENT_ID)
    GITHUB_OAUTH_SCOPES  = os.getenv("GITHUB_OAUTH_SCOPES", "repo read:user user:email")
    COPILOT_CLI_URL      = os.getenv("COPILOT_CLI_URL", "")
    COPILOT_CLI_PATH     = os.getenv("COPILOT_CLI_PATH", "")

    # --- Hotkeys ---
    HOTKEY_ADD_CONTEXT   = os.getenv("HOTKEY_ADD_CONTEXT",   "alt+q")
    HOTKEY_CLEAR_CONTEXT = os.getenv("HOTKEY_CLEAR_CONTEXT", "alt+w")
    HOTKEY_SNIP          = os.getenv("HOTKEY_SNIP",          "ctrl+alt+q")
    HOTKEY_VOICE         = os.getenv("HOTKEY_VOICE",         "f9")

    SNIP_CONTEXT_AMBIENT   = env_bool("SNIP_CONTEXT_AMBIENT",   True)
    SNIP_CONTEXT_DOCUMENTS = env_bool("SNIP_CONTEXT_DOCUMENTS", False)
    SNIP_CONTEXT_TOOLS     = env_bool("SNIP_CONTEXT_TOOLS",     False)

    # --- STT ---
    STT_MODEL        = os.getenv("STT_MODEL",        "base")
    STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
    STT_LANGUAGE     = os.getenv("STT_LANGUAGE",     "en")

    # --- Caller rows ---
    new_rows = _load_caller_rows()
    if "CALLER_ROWS" in globals():
        CALLER_ROWS.clear()
        CALLER_ROWS.extend(new_rows)
    else:
        CALLER_ROWS = new_rows

    # --- Voice (push-to-talk) caller ---
    new_voice = _load_voice_caller()
    if "VOICE_CALLER" in globals():
        VOICE_CALLER.clear()
        VOICE_CALLER.update(new_voice)
    else:
        VOICE_CALLER = new_voice

    # --- Context budgets ---
    CONTEXT_BROWSER_MAX_CHARS          = env_int("CONTEXT_BROWSER_MAX_CHARS",          4000)
    CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS = env_int("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", 8000)
    CONTEXT_TOOL_DOCUMENT_MAX_CHARS    = env_int("CONTEXT_TOOL_DOCUMENT_MAX_CHARS",    50000)
    TOOL_PLUGIN_DIR = os.getenv("TOOL_PLUGIN_DIR", str(MODEL_TOOLS_DIR))
    TOOL_GIT_ROOT   = os.getenv("TOOL_GIT_ROOT", BASE_DIR)

    # --- UI sizes ---
    BUBBLE_WIDTH           = env_int("BUBBLE_WIDTH",      340)
    BUBBLE_LINES           = env_int("BUBBLE_LINES",      3)
    BUBBLE_COLOR           = os.getenv("BUBBLE_COLOR",           "#1c1c24dc")
    BUBBLE_TEXT_COLOR      = os.getenv("BUBBLE_TEXT_COLOR",      "#e6e6e6")
    BUBBLE_READ_WORD_COLOR = os.getenv("BUBBLE_READ_WORD_COLOR", "#4da3ff")
    # ICON_SIZE / ICON_BACKSTOP_MS (formerly DOLL_SIZE / DOLL_ICON_BACKSTOP_MS) —
    # old keys still honored for back-compat.
    ICON_SIZE              = env_int("ICON_SIZE",     env_int("DOLL_SIZE",             80))
    ICON_BACKSTOP_MS       = env_int("ICON_BACKSTOP_MS", env_int("DOLL_ICON_BACKSTOP_MS", 5000))
    BUBBLE_HIDE_DELAY_MS   = env_int("BUBBLE_HIDE_DELAY_MS",   3500)
    BUBBLE_REVEAL_WPM      = env_int("BUBBLE_REVEAL_WPM",      170)
    BUBBLE_HOLD_REVEAL_WPM = env_int("BUBBLE_HOLD_REVEAL_WPM", 480)
    TTS_PLAYBACK_RATE      = env_float("TTS_PLAYBACK_RATE",      1.0)
    TTS_HOLD_PLAYBACK_RATE = env_float("TTS_HOLD_PLAYBACK_RATE", 1.35)

    # --- Memory ---
    MEMORY_LLM_PROVIDER             = os.getenv("MEMORY_LLM_PROVIDER",             LLM_PROVIDER)
    MEMORY_LLM_MODEL                = os.getenv("MEMORY_LLM_MODEL",                LLM_MODEL)
    MEMORY_LLM_FALLBACKS            = os.getenv("MEMORY_LLM_FALLBACKS",            "")
    MEMORY_AUTO_CONSOLIDATE         = env_bool("MEMORY_AUTO_CONSOLIDATE", False)
    MEMORY_CONSOLIDATION_INTERVAL   = env_int("MEMORY_CONSOLIDATION_INTERVAL", 15)
    MEMORY_TOP_K                    = env_int("MEMORY_TOP_K", 3)
    MEMORY_RELEVANCE_MAX_DISTANCE   = env_float("MEMORY_RELEVANCE_MAX_DISTANCE", 0.55)
    MEMORY_STM_TOKEN_BUDGET         = env_int("MEMORY_STM_TOKEN_BUDGET", 4000)

    # --- System prompt ---
    # NOTE: do not claim tool access here. Tools are only sometimes offered
    # (per-caller modes), so the tool note is appended at request time by
    # core.llm_clients.client when tools are actually attached.
    SYSTEM_PROMPT_UTILITY = os.getenv(
        "SYSTEM_PROMPT_UTILITY",
        "You are a concise desktop assistant. "
        "Answer in 1-3 short sentences. Be direct and plain. No markdown. "
        "If a [Memory] section appears in this prompt, it contains facts about the user "
        "from previous sessions — consider using them to personalize your answers without announcing "
        "that you are doing so."
    )
    # Migration: older saved prompts contain the static tool claim; strip it so
    # the model is no longer promised tools on queries that attach none.
    SYSTEM_PROMPT_UTILITY = SYSTEM_PROMPT_UTILITY.replace(
        _LEGACY_TOOL_PROMPT_SENTENCE, ""
    ).strip()


_load_config()


def get_system_prompt() -> str:
    return SYSTEM_PROMPT_UTILITY


def reload() -> None:
    """Re-read .env and update every module-level variable in-place.

    Call this after writing a new .env so changes take effect without a restart.
    Note: UI size constants (BUBBLE_WIDTH, ICON_SIZE, …) require widget recreation
    and only fully apply after a restart; everything else is live.
    """
    secret_store.refresh_cache()
    load_dotenv(_ENV_FILE, override=True)
    _load_config()
