import XCTest
@testable import Wisp

final class WispConfigTests: XCTestCase {

    func testDefaultCallersMatchWindowsIntentShape() {
        let config = WispConfig.load(environment: [:], readDotEnv: false)

        XCTAssertEqual(config.callers.count, 2)
        XCTAssertEqual(config.callers[0].hotkey, "ctrl+q")
        XCTAssertEqual(config.callers[0].label, "General")
        XCTAssertFalse(config.callers[0].pasteBack)
        XCTAssertEqual(config.callers[0].customKey, "s")
        XCTAssertEqual(config.callers[0].intents.map(\.key), ["w", "a", "d"])
        XCTAssertEqual(config.callers[1].label, "Rewrite & Paste")
        XCTAssertTrue(config.callers[1].pasteBack)
        XCTAssertEqual(config.snip.hotkey, "ctrl+alt+q")
        XCTAssertTrue(config.snip.contextAmbient)
        XCTAssertFalse(config.snip.contextDocuments)
        XCTAssertFalse(config.snip.contextTools)
    }

    func testCallerEnvironmentOverridesUseSameKeysAsPythonSettings() {
        let config = WispConfig.load(environment: [
            "CALLER_COUNT": "1",
            "CALLER_1_HOTKEY": "ctrl+option+space",
            "CALLER_1_LABEL": "Research",
            "CALLER_1_PASTE_BACK": "true",
            "CALLER_1_CUSTOM_KEY": "x",
            "CALLER_1_CONTEXT_AMBIENT": "false",
            "CALLER_1_CONTEXT_DOCUMENTS": "false",
            "CALLER_1_CONTEXT_TOOLS": "false",
            "CALLER_1_CONTEXT_SCREENSHOT": "model",
            "CALLER_1_CONTEXT_CLIPBOARD": "true",
            "CALLER_1_INTENT_COUNT": "2",
            "CALLER_1_INTENT_1_KEY": "r",
            "CALLER_1_INTENT_1_LABEL": "Review",
            "CALLER_1_INTENT_1_HINT": "Find risks",
            "CALLER_1_INTENT_1_PROMPT": "Review this.",
            "CALLER_1_INTENT_2_KEY": "f",
            "CALLER_1_INTENT_2_LABEL": "Fix",
            "CALLER_1_INTENT_2_PROMPT": "Fix this.",
        ], readDotEnv: false)

        XCTAssertEqual(config.callers.count, 1)
        let caller = try XCTUnwrap(config.callers.first)
        XCTAssertEqual(caller.hotkey, "ctrl+option+space")
        XCTAssertEqual(caller.label, "Research")
        XCTAssertTrue(caller.pasteBack)
        XCTAssertEqual(caller.customKey, "x")
        XCTAssertFalse(caller.contextAmbient)
        XCTAssertFalse(caller.contextDocuments)
        XCTAssertFalse(caller.contextTools)
        XCTAssertEqual(caller.contextScreenshot, .model)
        XCTAssertTrue(caller.contextClipboard)
        XCTAssertEqual(caller.intents.map(\.key), ["r", "f"])
        XCTAssertEqual(caller.intents[0].hint, "Find risks")
        XCTAssertEqual(caller.intents[1].prompt, "Fix this.")
    }

    func testSnipEnvironmentOverridesUseSameKeysAsPythonSettings() {
        let config = WispConfig.load(environment: [
            "HOTKEY_SNIP": "ctrl+option+4",
            "SNIP_CONTEXT_AMBIENT": "false",
            "SNIP_CONTEXT_DOCUMENTS": "true",
            "SNIP_CONTEXT_TOOLS": "true",
        ], readDotEnv: false)

        XCTAssertEqual(config.snip.hotkey, "ctrl+option+4")
        XCTAssertFalse(config.snip.contextAmbient)
        XCTAssertTrue(config.snip.contextDocuments)
        XCTAssertTrue(config.snip.contextTools)
    }

    func testSettingsDraftLoadsToolPluginDirectoryKey() {
        let draft = SettingsDraft.load(environment: [
            "TOOL_PLUGIN_DIR": "/Users/example/wisp/model_tools",
        ], readDotEnv: false)

        XCTAssertEqual(draft.toolPluginDir, "/Users/example/wisp/model_tools")
    }

    func testSettingsDraftDefaultsToolPluginDirectoryToRepoModelTools() {
        let draft = SettingsDraft.load(environment: [
            "WISP_REPO_ROOT": "/Users/example/wisp",
        ], readDotEnv: false)

        XCTAssertEqual(draft.toolPluginDir, "/Users/example/wisp/model_tools")
    }

    func testSettingsDraftLoadsNativeUIEnvironmentKeys() {
        let draft = SettingsDraft.load(environment: [
            "ICON_AUTO_HIDE": "yes",
            "ICON_SIZE": "112",
            "ICON_BACKSTOP_MS": "2750",
            "CHAT_AUTO_ELABORATE": "on",
            "CHAT_ELABORATE_PROMPT": "Say more with examples.",
            "BUBBLE_WIDTH": "420",
            "BUBBLE_LINES": "5",
            "BUBBLE_COLOR": "#101820ee",
            "BUBBLE_TEXT_COLOR": "#f7f7f7",
            "BUBBLE_READ_WORD_COLOR": "#ffcc33",
            "BUBBLE_REVEAL_WPM": "155",
            "BUBBLE_HOLD_REVEAL_WPM": "430",
            "BUBBLE_HIDE_DELAY_MS": "4800",
        ], readDotEnv: false)

        XCTAssertTrue(draft.iconAutoHide)
        XCTAssertEqual(draft.iconSize, "112")
        XCTAssertEqual(draft.iconBackstopMS, "2750")
        XCTAssertTrue(draft.chatAutoElaborate)
        XCTAssertEqual(draft.chatElaboratePrompt, "Say more with examples.")
        XCTAssertEqual(draft.bubbleWidth, "420")
        XCTAssertEqual(draft.bubbleLines, "5")
        XCTAssertEqual(draft.bubbleColor, "#101820ee")
        XCTAssertEqual(draft.bubbleTextColor, "#f7f7f7")
        XCTAssertEqual(draft.bubbleReadWordColor, "#ffcc33")
        XCTAssertEqual(draft.bubbleRevealWPM, "155")
        XCTAssertEqual(draft.bubbleHoldRevealWPM, "430")
        XCTAssertEqual(draft.bubbleHideDelayMS, "4800")
    }

    func testSettingsDraftLoadsLegacyIconEnvironmentFallbacks() {
        let draft = SettingsDraft.load(environment: [
            "DOLL_AUTO_HIDE": "true",
            "DOLL_SIZE": "96",
            "DOLL_ICON_BACKSTOP_MS": "6400",
        ], readDotEnv: false)

        XCTAssertTrue(draft.iconAutoHide)
        XCTAssertEqual(draft.iconSize, "96")
        XCTAssertEqual(draft.iconBackstopMS, "6400")
    }

    func testScreenshotModeAcceptsLegacyBooleanValues() {
        XCTAssertEqual(ScreenshotMode.normalized("true"), .auto)
        XCTAssertEqual(ScreenshotMode.normalized("tool"), .model)
        XCTAssertEqual(ScreenshotMode.normalized("no"), .off)
        XCTAssertEqual(ScreenshotMode.normalized("???", default: .model), .model)
    }

    func testDotEnvRenderUpdatesAndRemovesCallerRows() {
        let original = """
        # keep comments
        LLM_PROVIDER=chatgpt
        CALLER_COUNT=2
        CALLER_1_LABEL=Old
        CALLER_2_LABEL=Stale
        TTS_PROVIDER=none
        """

        let rendered = DotEnvFile.renderUpdating(
            original,
            updates: [
                "LLM_PROVIDER": "openai",
                "CALLER_COUNT": "1",
                "CALLER_1_LABEL": "General",
                "CALLER_1_INTENT_1_PROMPT": "Explain this clearly.",
            ],
            removingPrefixes: ["CALLER_"]
        )

        XCTAssertTrue(rendered.contains("# keep comments"))
        XCTAssertTrue(rendered.contains("LLM_PROVIDER=openai"))
        XCTAssertTrue(rendered.contains("TTS_PROVIDER=none"))
        XCTAssertTrue(rendered.contains("CALLER_COUNT=1"))
        XCTAssertTrue(rendered.contains("CALLER_1_LABEL=General"))
        XCTAssertTrue(rendered.contains("CALLER_1_INTENT_1_PROMPT=\"Explain this clearly.\""))
        XCTAssertFalse(rendered.contains("CALLER_2_LABEL"))
        XCTAssertEqual(DotEnvFile.readValues(fromText: rendered)["CALLER_1_LABEL"], "General")
    }

    func testSettingsDraftSerializesCallerContract() {
        var draft = SettingsDraft.empty
        draft.llmProvider = "anthropic"
        draft.llmModel = "claude-sonnet-4-5"
        draft.toolPluginDir = "/Users/example/wisp/model_tools"
        draft.callers = [
            SettingsCallerDraft(
                hotkey: "ctrl+option+space",
                label: "Research",
                pasteBack: true,
                customKey: "x",
                contextAmbient: false,
                contextDocuments: true,
                contextTools: false,
                contextScreenshot: .model,
                contextClipboard: true,
                intents: [
                    SettingsIntentDraft(
                        key: "r",
                        label: "Review",
                        hint: "Find risks",
                        prompt: "Review this."
                    )
                ]
            )
        ]

        let values = draft.envValues()

        XCTAssertEqual(values["LLM_PROVIDER"], "anthropic")
        XCTAssertEqual(values["LLM_MODEL"], "claude-sonnet-4-5")
        XCTAssertEqual(values["TOOL_PLUGIN_DIR"], "/Users/example/wisp/model_tools")
        XCTAssertEqual(values["CALLER_COUNT"], "1")
        XCTAssertEqual(values["CALLER_1_LABEL"], "Research")
        XCTAssertEqual(values["CALLER_1_PASTE_BACK"], "true")
        XCTAssertEqual(values["CALLER_1_CONTEXT_SCREENSHOT"], "model")
        XCTAssertEqual(values["CALLER_1_CONTEXT_CLIPBOARD"], "true")
        XCTAssertEqual(values["CALLER_1_INTENT_COUNT"], "1")
        XCTAssertEqual(values["CALLER_1_INTENT_1_KEY"], "r")
        XCTAssertEqual(values["CALLER_1_INTENT_1_PROMPT"], "Review this.")
        XCTAssertEqual(values["HOTKEY_SNIP"], "ctrl+alt+q")
        XCTAssertEqual(values["SNIP_CONTEXT_AMBIENT"], "true")
        XCTAssertEqual(values["SNIP_CONTEXT_DOCUMENTS"], "false")
        XCTAssertEqual(values["SNIP_CONTEXT_TOOLS"], "false")
    }

    func testSettingsDraftSerializesNativeUIEnvironmentContract() {
        var draft = SettingsDraft.empty
        draft.iconAutoHide = true
        draft.iconSize = "104"
        draft.iconBackstopMS = "3750"
        draft.chatAutoElaborate = true
        draft.chatElaboratePrompt = "Continue the last answer."
        draft.bubbleWidth = "390"
        draft.bubbleLines = "4"
        draft.bubbleColor = "#202530dd"
        draft.bubbleTextColor = "#eeeeee"
        draft.bubbleReadWordColor = "#7ab8ff"
        draft.bubbleRevealWPM = "165"
        draft.bubbleHoldRevealWPM = "460"
        draft.bubbleHideDelayMS = "4100"

        let values = draft.envValues()

        XCTAssertEqual(values["ICON_AUTO_HIDE"], "true")
        XCTAssertEqual(values["ICON_SIZE"], "104")
        XCTAssertEqual(values["ICON_BACKSTOP_MS"], "3750")
        XCTAssertEqual(values["CHAT_AUTO_ELABORATE"], "true")
        XCTAssertEqual(values["CHAT_ELABORATE_PROMPT"], "Continue the last answer.")
        XCTAssertEqual(values["BUBBLE_WIDTH"], "390")
        XCTAssertEqual(values["BUBBLE_LINES"], "4")
        XCTAssertEqual(values["BUBBLE_COLOR"], "#202530dd")
        XCTAssertEqual(values["BUBBLE_TEXT_COLOR"], "#eeeeee")
        XCTAssertEqual(values["BUBBLE_READ_WORD_COLOR"], "#7ab8ff")
        XCTAssertEqual(values["BUBBLE_REVEAL_WPM"], "165")
        XCTAssertEqual(values["BUBBLE_HOLD_REVEAL_WPM"], "460")
        XCTAssertEqual(values["BUBBLE_HIDE_DELAY_MS"], "4100")
    }
}
