from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
QT_DIR = ROOT / "ui" / "locales" / "qt"
LANGUAGES = ("es", "fr", "zh", "zh-Hant")
HOTKEY_CONTEXT_SOURCE = (
    "These default to the context dropdowns on the hotkey - changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected."
)
STALE_HOTKEY_CONTEXT_SOURCES = (
    "These default to the context dropdowns on the hotkey â€” changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey — changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey Ã¢â‚¬â€ changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey \u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u009d changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
    "These default to the context dropdowns on the hotkey вЂ” changing one here "
    "overrides the dropdown for that tool only. Automatic context "
    "(dropdowns set to On) is unaffected.",
)


def _translations(language: str) -> dict[str, str]:
    tree = ET.parse(QT_DIR / f"wisp_{language}.ts")
    out: dict[str, str] = {}
    for message in tree.findall(".//message"):
        source = message.findtext("source")
        translation = message.findtext("translation")
        if source and translation:
            out[source] = translation
    return out


def _sources(language: str) -> set[str]:
    tree = ET.parse(QT_DIR / f"wisp_{language}.ts")
    return {
        source
        for message in tree.findall(".//message")
        if (source := message.findtext("source"))
    }


def test_qt_catalog_sources_are_in_sync() -> None:
    """Verify shipped Qt catalogs expose the same translation source keys."""
    catalogs = {language: _sources(language) for language in LANGUAGES}
    expected = catalogs[LANGUAGES[0]]
    for language, sources in catalogs.items():
        assert sources == expected, language
        assert HOTKEY_CONTEXT_SOURCE in sources
        for stale_source in STALE_HOTKEY_CONTEXT_SOURCES:
            assert stale_source not in sources


def test_context_badge_sources_have_catalog_translations() -> None:
    """Verify right-of-icon context badge labels exist in every catalog."""
    expected = {
        "zh": {
            "App": "程序",
            "Browser/Web": "浏览器/网页",
            "Context": "上下文",
            "Memory": "记忆",
            "Screenshot": "截图",
            "Git/GitHub": "Git/GitHub",
            "Selection": "选择内容",
            "Clipboard": "剪贴板",
            "Files": "文件",
        },
        "zh-Hant": {
            "App": "程式",
            "Browser/Web": "瀏覽器/網頁",
            "Context": "上下文",
            "Memory": "記憶",
            "Screenshot": "截圖",
            "Git/GitHub": "Git/GitHub",
            "Selection": "選取內容",
            "Clipboard": "剪貼簿",
            "Files": "檔案",
        },
        "es": {
            "App": "Aplicación",
            "Browser/Web": "Navegador/Web",
            "Context": "Contexto",
            "Memory": "Memoria",
            "Screenshot": "Captura",
            "Git/GitHub": "Git/GitHub",
            "Selection": "Selección",
            "Clipboard": "Portapapeles",
            "Files": "Archivos",
        },
        "fr": {
            "App": "Application",
            "Browser/Web": "Navigateur/Web",
            "Context": "Contexte",
            "Memory": "Mémoire",
            "Screenshot": "Capture",
            "Git/GitHub": "Git/GitHub",
            "Selection": "Sélection",
            "Clipboard": "Presse-papiers",
            "Files": "Fichiers",
        },
    }
    for language, pairs in expected.items():
        catalog = _translations(language)
        for source, translation in pairs.items():
            assert catalog[source] == translation


@pytest.mark.workflow
def test_setup_health_voice_sources_have_catalog_translations() -> None:
    """Verify new setup, health, and voice confirmation strings are localized."""
    required = {
        "Run setup check",
        "Check provider, speech, hotkey, and privacy readiness.",
        "Setup check",
        "Voice transcript",
        "Dictation transcript",
        "Choose or edit the transcript:",
        "Read selection aloud",
        "Reading",
        "No selected text to read aloud.",
        "TTS is off. Choose a voice provider in Settings first.",
        "Could not read selected text",
        "Could not read selected text aloud.",
        "No status details available.",
        "Advanced settings",
        "Planned reply chunks",
        "Planned reply min chars",
        "Reasoning effort",
        "Runtime Status",
        "On + open docs",
        "Local files",
        "Privacy Report",
        "Privacy redaction report",
        "Privacy: {count} redacted",
        "Privacy: {count} item(s) detected and censored.",
        "item(s) detected and censored.",
        "Sensitive data",
        "API key",
        "Bearer token",
        "Card number",
        "Credential",
        "Email",
        "Private key",
        "SSN",
        "URL credential",
        "Active document",
        "Prompt",
        "Document",
        "Dropped file",
        "Additional redactions were hidden from this compact report.",
        "Custom",
        "Custom (OpenAI-compatible)",
        "Custom / enter manually…",
        "Bubble scroll snap delay (s)",
        "Intent context keys:",
        "Timeout ms:",
        "App Settings",
        "Memory Settings",
        "Start Wisp when you sign in",
        "Launch Wisp automatically after you sign in to this computer.",
        "Could not update startup setting",
        "Your preference was saved, but Wisp could not update the operating system startup entry:\n\n{error}",
        "Dictation (hold to type)",
        "OpenAI API",
        "Fetch the full readable text of a specific web page URL on demand. Use this when the user asks about a website/page and the passive browser preview is missing, partial, stale, or not enough.",
        "None",
        "unavailable",
        "authorized",
        "denied",
        "not_determined",
        "restricted",
        "PASS",
        "WARN",
        "FAIL",
        "LLM provider",
        "Speech to text",
        "Hotkeys",
        "Privacy redaction",
        "UI worker",
        "Brain worker",
        "Audio worker",
        "Native worker",
        "Context capture",
        "Screenshot capture",
        "Microphone",
        "LLM route configured: {route}.",
        "TTS is off.",
        "This provider does not send real word timestamps. The bubble uses normal reveal speed instead of audio-synced word highlighting.",
        "STT model configured: {model}.",
        "{count} hotkeys configured.",
        "Privacy redaction is on.",
        "{label} worker responded.",
        "Speech recognition is ready.",
        "TTS is off; replies will stay text-only.",
        "Speak assistant replies automatically",
        "When off, configured voices are still available for read-selection-aloud and Test TTS.",
        "Auto-speak replies",
        "Review transcript and context before asking",
        "After F9 transcription, open the intent overlay with the transcript in the custom prompt field.",
        "Accessibility permission: {value}.",
        "Screen recording permission: {value}.",
        "Microphone permission: {value}.",
        "LLM route uses {provider} but you are not logged in.",
        "No privacy redactions in the latest request.",
        "Addon folder installed.",
        "Recommendation: open Addon Manager, inspect the addon diagnostics, then repair or disable it.",
        "Installed addon: ",
        "Technical detail: ",
        "Installing Kokoro: {detail}.",
        "Install Kokoro GPU support",
        "Kokoro is installed, but the selected GPU device needs a CUDA-enabled Torch install.",
        "Kokoro is installed with CPU support.",
        "Kokoro is installed with GPU support ({device}).",
        "Kokoro is installed with CPU-only Torch. Auto detected a CUDA-capable system, but Kokoro will use CPU until GPU support is installed.",
        "Kokoro is not installed. The selected device will install GPU support and may download several GB.",
        "Wisp will upgrade Kokoro's optional package layer with GPU support.\n\n",
        "Wisp will install Kokoro into its user-writable optional packages folder.\n\n",
        "kokoro>=0.9.4, soundfile, CUDA-enabled Torch, English speech model",
        "kokoro>=0.9.4, soundfile, English speech model",
        "The GPU install may download several GB and can take a long time. It requires an NVIDIA GPU and compatible driver. ",
        "{action_note}Packages: {package_label}\nEstimated storage: up to about 2 GB for CPU, or several GB for GPU if speech dependencies are missing. {storage_note}First use may also download the Kokoro model cache.\n\nCurrent Kokoro settings:\nVoice: {voice}\nLanguage code: {lang_code}\nDevice: {device}\nSpeed: {speed}\nSample rate: {sample_rate} Hz\nVolume: {volume}\n\nOn Windows, Kokoro may also need eSpeak NG installed separately if Test TTS reports a phoneme/espeak error.\n\nContinue?",
        "Kokoro GPU support installed and local voice is ready.",
        "Kokoro installed and local voice is ready.",
        "Kokoro installed, but local voice preparation failed: {exc}. Connect to the internet and click Test TTS once to finish setup.",
        "preparing local voice assets",
        "Preparing local voice... {detail}",
        "starting pip",
        "checking installed packages",
        "resolving packages",
        "downloading packages",
        "installing packages",
        "finalizing",
        "working - installer is still running",
        "Chunking controls for read-aloud TTS and long speech-to-text recordings.",
        "Read-aloud min words",
        "Read-aloud max words",
        "STT first chunk trigger (s)",
        "STT chunk cadence (s)",
        "STT live-edge delay (s)",
        "STT overlap (s)",
    }
    for language in LANGUAGES:
        catalog = _translations(language)
        for source in required:
            assert catalog.get(source), f"{language}: {source}"


@pytest.mark.workflow
def test_agent_activity_sources_have_catalog_translations() -> None:
    """Verify auto-agent meeting/log activity strings are localized."""
    required = {
        "Agent",
        "Agent Detail",
        "Recent Activity",
        "Shared Board",
        "Agent Meeting",
        "Reset Layout",
        "Restore every agent card to its default position and size",
        "Model prompts, responses, parsed JSON, and tool payloads appear here while the task runs.",
        "Final report appears here when the task finishes.",
        "Waiting {elapsed}",
        "Receiving response ({elapsed})",
        "Handing off to {agent}",
        "Explicit handoff to {agent}",
        "Prompt {summary}",
        "Using {tool}",
        "avg {avg} | invalid {invalid} | repair {repairs} | fallback {fallbacks}",
        "calls {calls}, average latency {avg}s, invalid JSON {invalid}, repairs {repairs}, fallbacks {fallbacks}",
        "Told {target}: {message}",
        "Heard from {source}: {message}",
        "Thought: {message}",
        "thought: {message}",
        "Handoff ({status}): {reason}",
        "{agent} returned final response",
        "returned final response",
        "agent turn {turn}: {agent}",
        "agent read-only turn: {agent}",
        "prompt prepared for {agent}: {chars} chars ({mode})",
        "requesting LLM tool response via {route}",
        "model call still waiting after {elapsed} via {route}",
        "model first token after {elapsed} via {route}",
        "model response received in {elapsed}s ({chars} chars)",
        "model callback response received in {elapsed}s ({chars} chars)",
        "tool {tool} failed: {message}",
        "tool {tool}: exit {code}: {message}",
        "tool call: {tool}",
        "{agent} tool call: {tool}",
        "Message cannot be empty.",
        "delta",
        "full",
        "read-only full",
        "waiting",
        "blocked",
        "done",
        "continue",
        "ready_for_review",
        "complete",
    }
    for language in LANGUAGES:
        catalog = _translations(language)
        for source in required:
            assert catalog.get(source), f"{language}: {source}"
