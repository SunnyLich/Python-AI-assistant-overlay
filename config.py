"""
config.py — Central configuration loaded from .env
"""
import os
from dotenv import load_dotenv
from core import secret_store
from core.env_utils import env_bool, env_float, env_int
from core.paths import FILLER_AUDIO_DIR as DEFAULT_FILLER_AUDIO_DIR
from core.paths import REPO_ROOT, TOOLS_INSTALLED_DIR

load_dotenv()

BASE_DIR = str(REPO_ROOT)

# --- API Keys ---
GROQ_API_KEY = secret_store.get_secret("GROQ_API_KEY")
OPENAI_API_KEY = secret_store.get_secret("OPENAI_API_KEY")
ANTHROPIC_API_KEY = secret_store.get_secret("ANTHROPIC_API_KEY")
GOOGLE_API_KEY = secret_store.get_secret("GOOGLE_API_KEY")
CARTESIA_API_KEY = secret_store.get_secret("CARTESIA_API_KEY")
ELEVENLABS_API_KEY = secret_store.get_secret("ELEVENLABS_API_KEY")

# --- LLM ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")       # groq | openai | anthropic | google | chatgpt | copilot
LLM_MODEL = os.getenv("LLM_MODEL", "llama3-8b-8192")
LLM_FALLBACKS = os.getenv("LLM_FALLBACKS", "")

# --- Chat / elaborate LLM (defaults to same as above) ---
CHAT_LLM_PROVIDER = os.getenv("CHAT_LLM_PROVIDER", LLM_PROVIDER)
CHAT_LLM_MODEL    = os.getenv("CHAT_LLM_MODEL",    LLM_MODEL)
CHAT_LLM_FALLBACKS = os.getenv("CHAT_LLM_FALLBACKS", "")

# --- Tool-capable LLM (used when web_search / get_context tools are active) ---
# Haiku does not invoke web_search_20250305; Sonnet does. Defaults to Sonnet so tools
# work out of the box even if LLM_MODEL is set to Haiku.
TOOL_LLM_MODEL = os.getenv("TOOL_LLM_MODEL", "claude-sonnet-4-5")

# --- Vision LLM (for screen-snip queries — must support image input) ---
# Leave empty to get a helpful error when snip is used without configuration.
VISION_LLM_PROVIDER = os.getenv("VISION_LLM_PROVIDER", "")   # e.g. anthropic | openai | google
VISION_LLM_MODEL    = os.getenv("VISION_LLM_MODEL",    "")   # e.g. claude-opus-4-5
VISION_LLM_FALLBACKS = os.getenv("VISION_LLM_FALLBACKS", "")

# --- TTS ---
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "cartesia")    # cartesia | elevenlabs | none
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

# --- App behaviour ---
DOLL_AUTO_HIDE = env_bool("DOLL_AUTO_HIDE", True)  # hide doll when idle
CHAT_AUTO_ELABORATE = env_bool("CHAT_AUTO_ELABORATE", True)  # auto-send elaborate prompt on chat open
CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")
GITHUB_DEFAULT_CLIENT_ID = os.getenv("GITHUB_DEFAULT_CLIENT_ID", "")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", GITHUB_DEFAULT_CLIENT_ID)
GITHUB_OAUTH_SCOPES = os.getenv("GITHUB_OAUTH_SCOPES", "repo read:user user:email")
COPILOT_CLI_URL = os.getenv("COPILOT_CLI_URL", "")
COPILOT_CLI_PATH = os.getenv("COPILOT_CLI_PATH", "")

# --- Hotkeys ---
HOTKEY_ADD_CONTEXT   = os.getenv("HOTKEY_ADD_CONTEXT",   "alt+q")       # add selected text to context buffer
HOTKEY_CLEAR_CONTEXT = os.getenv("HOTKEY_CLEAR_CONTEXT", "alt+w")       # clear context buffer
HOTKEY_SNIP          = os.getenv("HOTKEY_SNIP",          "ctrl+alt+q")  # draw screen region → intent picker
HOTKEY_VOICE         = os.getenv("HOTKEY_VOICE",         "f9")          # push-to-talk voice input

SNIP_CONTEXT_AMBIENT = env_bool("SNIP_CONTEXT_AMBIENT", True)
SNIP_CONTEXT_DOCUMENTS = env_bool("SNIP_CONTEXT_DOCUMENTS", False)
SNIP_CONTEXT_TOOLS = env_bool("SNIP_CONTEXT_TOOLS", False)

# --- STT (Speech-to-Text) ---
STT_MODEL        = os.getenv("STT_MODEL",        "base")   # tiny | base | small | medium | large-v3
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")   # int8 | float16 | float32
STT_LANGUAGE     = os.getenv("STT_LANGUAGE",     "en")     # ISO 639-1 code, or "" for auto-detect

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
        "context_tools": True,
        "context_screenshot": False,
        "intents": [
            {"key": "w", "label": "What is this?",      "prompt": "What is this? Give me a clear, plain-English explanation in 2-3 sentences."},
            {"key": "a", "label": "Explain simply",     "prompt": "Explain this as simply as possible. Assume I have no technical background whatsoever."},
            {"key": "d", "label": "How do I fix this?", "prompt": "How do I fix this? Give me: 1, what error is this in 1 sentence; 2, concise, actionable steps I can follow right now."},
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
        "context_screenshot": False,
        "intents": [
            {"key": "w", "label": "Fix grammar",   "prompt": "Fix the grammar and spelling of the following text. Output ONLY the corrected text."},
            {"key": "a", "label": "Simplify",      "prompt": "Simplify the following text for a general audience. Output ONLY the simplified text."},
            {"key": "d", "label": "Improve tone",  "prompt": "Rewrite the following text to sound more professional and polished. Output ONLY the rewritten text."},
        ],
    },
]


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
                "prompt": os.getenv(f"CALLER_{n}_INTENT_{m}_PROMPT", d_intent.get("prompt", "")),
            })
        rows.append({
            "hotkey":     os.getenv(f"CALLER_{n}_HOTKEY",     default.get("hotkey", "")),
            "label":      os.getenv(f"CALLER_{n}_LABEL",      default.get("label", "")),
            "paste_back": env_bool(f"CALLER_{n}_PASTE_BACK", bool(default.get("paste_back", False))),
            "custom_key": os.getenv(f"CALLER_{n}_CUSTOM_KEY", default.get("custom_key", "s")),
            "context_ambient": env_bool(f"CALLER_{n}_CONTEXT_AMBIENT", bool(default.get("context_ambient", True))),
            "context_documents": env_bool(f"CALLER_{n}_CONTEXT_DOCUMENTS", bool(default.get("context_documents", True))),
            "context_tools": env_bool(f"CALLER_{n}_CONTEXT_TOOLS", bool(default.get("context_tools", True))),
            "context_screenshot": env_bool(f"CALLER_{n}_CONTEXT_SCREENSHOT", bool(default.get("context_screenshot", False))),
            "intents":    intents,
        })
    return rows


CALLER_ROWS: list[dict] = _load_caller_rows()

# --- Context budgets ---
CONTEXT_BROWSER_MAX_CHARS = env_int("CONTEXT_BROWSER_MAX_CHARS", 4000)
CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS = env_int("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", 8000)
CONTEXT_TOOL_DOCUMENT_MAX_CHARS = env_int("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", 50000)
TOOL_PLUGIN_DIR = os.getenv(
    "TOOL_PLUGIN_DIR",
    str(TOOLS_INSTALLED_DIR),
)
TOOL_GIT_ROOT = os.getenv("TOOL_GIT_ROOT", BASE_DIR)

# --- UI sizes ---
BUBBLE_WIDTH      = env_int("BUBBLE_WIDTH",      340)  # px wide (not including tail)
BUBBLE_LINES      = env_int("BUBBLE_LINES",      2)    # max lines shown at once
BUBBLE_COLOR      = os.getenv("BUBBLE_COLOR",      "#1c1c24dc") # bubble fill, #RRGGBB or #RRGGBBAA
BUBBLE_TEXT_COLOR = os.getenv("BUBBLE_TEXT_COLOR", "#e6e6e6")   # unread/default text color
BUBBLE_READ_WORD_COLOR = os.getenv("BUBBLE_READ_WORD_COLOR", "#4da3ff") # read/current word color
DOLL_SIZE         = env_int("DOLL_SIZE",         80)   # doll icon size px (square, sprite fallback)
DOLL_ICON_BACKSTOP_MS = env_int("DOLL_ICON_BACKSTOP_MS", 5000) # max icon-only visible time
BUBBLE_HIDE_DELAY_MS = env_int("BUBBLE_HIDE_DELAY_MS", 3500) # ms after finish before bubble/icon hide
BUBBLE_REVEAL_WPM = env_int("BUBBLE_REVEAL_WPM", 170) # word-reveal speed (WPM fallback mode)
BUBBLE_HOLD_REVEAL_WPM = env_int("BUBBLE_HOLD_REVEAL_WPM", 480) # hold bubble to speed up reveal
TTS_PLAYBACK_RATE = env_float("TTS_PLAYBACK_RATE", 1.0) # normal TTS playback speed
TTS_HOLD_PLAYBACK_RATE = env_float("TTS_HOLD_PLAYBACK_RATE", 1.35) # hold bubble to speed up TTS

# --- Memory ---
# LLM used for consolidation / compression (defaults to chat LLM).
MEMORY_LLM_PROVIDER             = os.getenv("MEMORY_LLM_PROVIDER",             CHAT_LLM_PROVIDER)
MEMORY_LLM_MODEL                = os.getenv("MEMORY_LLM_MODEL",                CHAT_LLM_MODEL)
# How often (minutes) to extract facts from the session and write them to LTM.
MEMORY_CONSOLIDATION_INTERVAL   = env_int("MEMORY_CONSOLIDATION_INTERVAL", 15)
# How many LTM facts to retrieve per LLM call (semantic top-k).
MEMORY_TOP_K                    = env_int("MEMORY_TOP_K", 3)
# Approximate token budget for raw STM turns before mid-session compression kicks in.
MEMORY_STM_TOKEN_BUDGET         = env_int("MEMORY_STM_TOKEN_BUDGET", 4000)

# --- Audio ---
FILLER_AUDIO_DIR = str(DEFAULT_FILLER_AUDIO_DIR)
FILLER_MAX_DURATION_MS = 1000   # filler clips must be < 1s

# --- Latency targets (ms) ---
LATENCY_TARGET_MS = 1500
LATENCY_CEILING_MS = 3000

# --- System prompt ---
SYSTEM_PROMPT_UTILITY = os.getenv(
    "SYSTEM_PROMPT_UTILITY",
    "You are a concise desktop assistant. "
    "Answer in 1-3 short sentences. Be direct and plain. No markdown. "
    "You have access to a web_search tool and a get_context tool. "
    "Use web_search for current information and use get_context with a URL "
    "when the user asks about a specific page. Never print or simulate tool "
    "calls in the reply."
)

def get_system_prompt() -> str:
    return SYSTEM_PROMPT_UTILITY


def reload() -> None:
    """Re-read .env and update every module-level variable in-place.

    Call this after writing a new .env so changes take effect without a restart.
    Note: UI size constants (BUBBLE_WIDTH, DOLL_SIZE, …) require widget recreation
    and only fully apply after a restart; everything else is live.
    """
    global GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
    global CARTESIA_API_KEY, ELEVENLABS_API_KEY
    global LLM_PROVIDER, LLM_MODEL, LLM_FALLBACKS
    global CHAT_LLM_PROVIDER, CHAT_LLM_MODEL, CHAT_LLM_FALLBACKS, TOOL_LLM_MODEL
    global VISION_LLM_PROVIDER, VISION_LLM_MODEL, VISION_LLM_FALLBACKS
    global TTS_PROVIDER, CARTESIA_VOICE_ID
    global DOLL_AUTO_HIDE, CHAT_AUTO_ELABORATE, CHAT_ELABORATE_PROMPT
    global GITHUB_DEFAULT_CLIENT_ID, GITHUB_CLIENT_ID, GITHUB_OAUTH_SCOPES
    global HOTKEY_ADD_CONTEXT, HOTKEY_CLEAR_CONTEXT, HOTKEY_SNIP
    global SNIP_CONTEXT_AMBIENT, SNIP_CONTEXT_DOCUMENTS, SNIP_CONTEXT_TOOLS
    global CONTEXT_BROWSER_MAX_CHARS, CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS, CONTEXT_TOOL_DOCUMENT_MAX_CHARS
    global TOOL_PLUGIN_DIR, TOOL_GIT_ROOT
    global HOTKEY_VOICE, STT_MODEL, STT_COMPUTE_TYPE, STT_LANGUAGE
    global CALLER_ROWS
    global BUBBLE_WIDTH, BUBBLE_LINES, BUBBLE_COLOR, BUBBLE_TEXT_COLOR, BUBBLE_READ_WORD_COLOR
    global DOLL_SIZE, DOLL_ICON_BACKSTOP_MS, BUBBLE_HIDE_DELAY_MS
    global BUBBLE_REVEAL_WPM, BUBBLE_HOLD_REVEAL_WPM
    global TTS_PLAYBACK_RATE, TTS_HOLD_PLAYBACK_RATE
    global SYSTEM_PROMPT_UTILITY
    global MEMORY_LLM_PROVIDER, MEMORY_LLM_MODEL
    global COPILOT_CLI_URL, COPILOT_CLI_PATH
    global MEMORY_CONSOLIDATION_INTERVAL, MEMORY_TOP_K, MEMORY_STM_TOKEN_BUDGET

    load_dotenv(override=True)   # push .env values back into os.environ

    GROQ_API_KEY      = secret_store.get_secret("GROQ_API_KEY")
    OPENAI_API_KEY    = secret_store.get_secret("OPENAI_API_KEY")
    ANTHROPIC_API_KEY = secret_store.get_secret("ANTHROPIC_API_KEY")
    GOOGLE_API_KEY    = secret_store.get_secret("GOOGLE_API_KEY")
    CARTESIA_API_KEY  = secret_store.get_secret("CARTESIA_API_KEY")
    ELEVENLABS_API_KEY = secret_store.get_secret("ELEVENLABS_API_KEY")

    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
    LLM_MODEL    = os.getenv("LLM_MODEL", "llama3-8b-8192")
    LLM_FALLBACKS = os.getenv("LLM_FALLBACKS", "")
    CHAT_LLM_PROVIDER = os.getenv("CHAT_LLM_PROVIDER", LLM_PROVIDER)
    CHAT_LLM_MODEL    = os.getenv("CHAT_LLM_MODEL",    LLM_MODEL)
    CHAT_LLM_FALLBACKS = os.getenv("CHAT_LLM_FALLBACKS", "")
    TOOL_LLM_MODEL    = os.getenv("TOOL_LLM_MODEL",    "claude-sonnet-4-5")
    VISION_LLM_PROVIDER = os.getenv("VISION_LLM_PROVIDER", "")
    VISION_LLM_MODEL    = os.getenv("VISION_LLM_MODEL",    "")
    VISION_LLM_FALLBACKS = os.getenv("VISION_LLM_FALLBACKS", "")
    CONTEXT_BROWSER_MAX_CHARS = env_int("CONTEXT_BROWSER_MAX_CHARS", 4000)
    CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS = env_int("CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", 8000)
    CONTEXT_TOOL_DOCUMENT_MAX_CHARS = env_int("CONTEXT_TOOL_DOCUMENT_MAX_CHARS", 50000)
    TOOL_PLUGIN_DIR = os.getenv(
        "TOOL_PLUGIN_DIR",
        str(TOOLS_INSTALLED_DIR),
    )
    TOOL_GIT_ROOT = os.getenv("TOOL_GIT_ROOT", BASE_DIR)

    TTS_PROVIDER      = os.getenv("TTS_PROVIDER", "cartesia")
    CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID", "")

    DOLL_AUTO_HIDE        = env_bool("DOLL_AUTO_HIDE", True)
    CHAT_AUTO_ELABORATE   = env_bool("CHAT_AUTO_ELABORATE", True)
    CHAT_ELABORATE_PROMPT = os.getenv("CHAT_ELABORATE_PROMPT", "Please elaborate on that.")
    GITHUB_DEFAULT_CLIENT_ID = os.getenv("GITHUB_DEFAULT_CLIENT_ID", "")
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", GITHUB_DEFAULT_CLIENT_ID)
    GITHUB_OAUTH_SCOPES = os.getenv("GITHUB_OAUTH_SCOPES", "repo read:user user:email")
    COPILOT_CLI_URL = os.getenv("COPILOT_CLI_URL", "")
    COPILOT_CLI_PATH = os.getenv("COPILOT_CLI_PATH", "")

    HOTKEY_ADD_CONTEXT       = os.getenv("HOTKEY_ADD_CONTEXT",   "alt+q")
    HOTKEY_CLEAR_CONTEXT     = os.getenv("HOTKEY_CLEAR_CONTEXT", "alt+w")
    HOTKEY_SNIP              = os.getenv("HOTKEY_SNIP",          "ctrl+alt+q")
    SNIP_CONTEXT_AMBIENT = env_bool("SNIP_CONTEXT_AMBIENT", True)
    SNIP_CONTEXT_DOCUMENTS = env_bool("SNIP_CONTEXT_DOCUMENTS", False)
    SNIP_CONTEXT_TOOLS = env_bool("SNIP_CONTEXT_TOOLS", False)
    HOTKEY_VOICE             = os.getenv("HOTKEY_VOICE",         "f9")
    STT_MODEL        = os.getenv("STT_MODEL",        "base")
    STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8")
    STT_LANGUAGE     = os.getenv("STT_LANGUAGE",     "en")

    CALLER_ROWS.clear()
    CALLER_ROWS.extend(_load_caller_rows())

    BUBBLE_WIDTH      = env_int("BUBBLE_WIDTH",      340)
    BUBBLE_LINES      = env_int("BUBBLE_LINES",      2)
    BUBBLE_COLOR      = os.getenv("BUBBLE_COLOR",      "#1c1c24dc")
    BUBBLE_TEXT_COLOR = os.getenv("BUBBLE_TEXT_COLOR", "#e6e6e6")
    BUBBLE_READ_WORD_COLOR = os.getenv("BUBBLE_READ_WORD_COLOR", "#4da3ff")
    DOLL_SIZE         = env_int("DOLL_SIZE",         80)
    DOLL_ICON_BACKSTOP_MS = env_int("DOLL_ICON_BACKSTOP_MS", 5000)
    BUBBLE_HIDE_DELAY_MS = env_int("BUBBLE_HIDE_DELAY_MS", 3500)
    BUBBLE_REVEAL_WPM = env_int("BUBBLE_REVEAL_WPM", 170)
    BUBBLE_HOLD_REVEAL_WPM = env_int("BUBBLE_HOLD_REVEAL_WPM", 480)
    TTS_PLAYBACK_RATE = env_float("TTS_PLAYBACK_RATE", 1.0)
    TTS_HOLD_PLAYBACK_RATE = env_float("TTS_HOLD_PLAYBACK_RATE", 1.35)

    MEMORY_LLM_PROVIDER           = os.getenv("MEMORY_LLM_PROVIDER",           CHAT_LLM_PROVIDER)
    MEMORY_LLM_MODEL              = os.getenv("MEMORY_LLM_MODEL",              CHAT_LLM_MODEL)
    MEMORY_CONSOLIDATION_INTERVAL = env_int("MEMORY_CONSOLIDATION_INTERVAL", 15)
    MEMORY_TOP_K                  = env_int("MEMORY_TOP_K", 3)
    MEMORY_STM_TOKEN_BUDGET       = env_int("MEMORY_STM_TOKEN_BUDGET", 4000)

    SYSTEM_PROMPT_UTILITY = os.getenv(
        "SYSTEM_PROMPT_UTILITY",
        "You are a concise desktop assistant. "
        "Answer in 1-3 short sentences. Be direct and plain. No markdown. "
        "You have access to a web_search tool and a get_context tool. "
        "Use web_search for current information and use get_context with a URL "
        "when the user asks about a specific page. Never print or simulate tool "
        "calls in the reply."
    )
