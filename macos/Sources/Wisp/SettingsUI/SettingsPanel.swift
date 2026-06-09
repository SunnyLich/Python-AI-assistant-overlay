import AppKit
import SwiftUI

struct SettingsIntentDraft: Identifiable, Equatable {
    var id = UUID()
    var key: String
    var label: String
    var hint: String
    var prompt: String
}

struct SettingsCallerDraft: Identifiable, Equatable {
    var id = UUID()
    var hotkey: String
    var label: String
    var pasteBack: Bool
    var customKey: String
    var contextAmbient: Bool
    var contextDocuments: Bool
    var contextTools: Bool
    var contextScreenshot: ScreenshotMode
    var contextClipboard: Bool
    var intents: [SettingsIntentDraft]
}

struct SettingsDraft: Equatable {
    var llmProvider: String
    var llmModel: String
    var llmFallbacks: String
    var visionProvider: String
    var visionModel: String
    var visionFallbacks: String
    var memoryProvider: String
    var memoryModel: String
    var memoryFallbacks: String
    var toolModel: String
    var toolPluginDir: String
    var toolGitRoot: String
    var customBaseURL: String
    var githubClientID: String
    var githubOAuthScopes: String

    var ttsProvider: String
    var cartesiaVoiceID: String
    var sttModel: String
    var sttComputeType: String
    var sttLanguage: String
    var ttsPlaybackRate: String
    var ttsHoldPlaybackRate: String
    var voiceHotkey: String

    var memoryAutoConsolidate: Bool
    var memoryConsolidationInterval: String
    var memoryTopK: String
    var memoryRelevanceMaxDistance: String
    var memorySTMTokenBudget: String
    var contextBrowserMaxChars: String
    var contextAmbientDocumentMaxChars: String
    var contextToolDocumentMaxChars: String

    var addContextHotkey: String
    var clearContextHotkey: String
    var snipHotkey: String
    var snipContextAmbient: Bool
    var snipContextDocuments: Bool
    var snipContextTools: Bool

    var themeMode: String
    var iconAutoHide: Bool
    var iconSize: String
    var iconBackstopMS: String
    var chatAutoElaborate: Bool
    var chatElaboratePrompt: String
    var bubbleWidth: String
    var bubbleLines: String
    var bubbleColor: String
    var bubbleTextColor: String
    var bubbleReadWordColor: String
    var bubbleRevealWPM: String
    var bubbleHoldRevealWPM: String
    var bubbleHideDelayMS: String
    var systemPromptUtility: String

    var callers: [SettingsCallerDraft]

    private static let defaultSystemPromptUtility = """
    You are a concise desktop assistant. Answer in 1-3 short sentences. Be direct and plain. No markdown. If a [Memory] section appears in this prompt, it contains facts about the user from previous sessions - consider using them to personalize your answers without announcing that you are doing so. You have access to a web_search tool and a get_context tool. Use web_search for current information and use get_context with a URL when the user asks about a specific page. Never print or simulate tool calls in the reply.
    """

    static func load(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        readDotEnv: Bool = true
    ) -> SettingsDraft {
        let values = WispConfig.loadValues(environment: environment, readDotEnv: readDotEnv)
        let config = WispConfig.load(environment: environment, readDotEnv: readDotEnv)

        let llmProvider = values["LLM_PROVIDER"] ?? "chatgpt"
        let llmModel = values["LLM_MODEL"] ?? "gpt-5.4"
        let defaultToolPluginDir = WispConfig.repoRoot(environment: environment)
            .appendingPathComponent("model_tools")
            .path

        return SettingsDraft(
            llmProvider: llmProvider,
            llmModel: llmModel,
            llmFallbacks: values["LLM_FALLBACKS"] ?? "",
            visionProvider: values["VISION_LLM_PROVIDER"] ?? "",
            visionModel: values["VISION_LLM_MODEL"] ?? "",
            visionFallbacks: values["VISION_LLM_FALLBACKS"] ?? "",
            memoryProvider: values["MEMORY_LLM_PROVIDER"] ?? llmProvider,
            memoryModel: values["MEMORY_LLM_MODEL"] ?? llmModel,
            memoryFallbacks: values["MEMORY_LLM_FALLBACKS"] ?? "",
            toolModel: values["TOOL_LLM_MODEL"] ?? "",
            toolPluginDir: values["TOOL_PLUGIN_DIR"] ?? defaultToolPluginDir,
            toolGitRoot: values["TOOL_GIT_ROOT"] ?? WispConfig.repoRoot(environment: environment).path,
            customBaseURL: values["CUSTOM_BASE_URL"] ?? "",
            githubClientID: values["GITHUB_CLIENT_ID"] ?? values["GITHUB_DEFAULT_CLIENT_ID"] ?? "",
            githubOAuthScopes: values["GITHUB_OAUTH_SCOPES"] ?? "repo read:user user:email",
            ttsProvider: values["TTS_PROVIDER"] ?? "none",
            cartesiaVoiceID: values["CARTESIA_VOICE_ID"] ?? "",
            sttModel: values["STT_MODEL"] ?? "base",
            sttComputeType: values["STT_COMPUTE_TYPE"] ?? "int8",
            sttLanguage: values["STT_LANGUAGE"] ?? "en",
            ttsPlaybackRate: values["TTS_PLAYBACK_RATE"] ?? "1.0",
            ttsHoldPlaybackRate: values["TTS_HOLD_PLAYBACK_RATE"] ?? "1.35",
            voiceHotkey: values["HOTKEY_VOICE"] ?? "f9",
            memoryAutoConsolidate: boolValue(values["MEMORY_AUTO_CONSOLIDATE"], default: false),
            memoryConsolidationInterval: values["MEMORY_CONSOLIDATION_INTERVAL"] ?? "15",
            memoryTopK: values["MEMORY_TOP_K"] ?? "3",
            memoryRelevanceMaxDistance: values["MEMORY_RELEVANCE_MAX_DISTANCE"] ?? "0.55",
            memorySTMTokenBudget: values["MEMORY_STM_TOKEN_BUDGET"] ?? "4000",
            contextBrowserMaxChars: values["CONTEXT_BROWSER_MAX_CHARS"] ?? "4000",
            contextAmbientDocumentMaxChars: values["CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS"] ?? "8000",
            contextToolDocumentMaxChars: values["CONTEXT_TOOL_DOCUMENT_MAX_CHARS"] ?? "50000",
            addContextHotkey: values["HOTKEY_ADD_CONTEXT"] ?? "alt+q",
            clearContextHotkey: values["HOTKEY_CLEAR_CONTEXT"] ?? "alt+w",
            snipHotkey: values["HOTKEY_SNIP"] ?? "ctrl+alt+q",
            snipContextAmbient: boolValue(values["SNIP_CONTEXT_AMBIENT"], default: true),
            snipContextDocuments: boolValue(values["SNIP_CONTEXT_DOCUMENTS"], default: false),
            snipContextTools: boolValue(values["SNIP_CONTEXT_TOOLS"], default: false),
            themeMode: values["THEME_MODE"] ?? "system",
            iconAutoHide: boolValue(values["ICON_AUTO_HIDE"] ?? values["DOLL_AUTO_HIDE"], default: false),
            iconSize: values["ICON_SIZE"] ?? values["DOLL_SIZE"] ?? "80",
            iconBackstopMS: values["ICON_BACKSTOP_MS"] ?? values["DOLL_ICON_BACKSTOP_MS"] ?? "5000",
            chatAutoElaborate: boolValue(values["CHAT_AUTO_ELABORATE"], default: false),
            chatElaboratePrompt: values["CHAT_ELABORATE_PROMPT"] ?? "Please elaborate on that.",
            bubbleWidth: values["BUBBLE_WIDTH"] ?? "340",
            bubbleLines: values["BUBBLE_LINES"] ?? "3",
            bubbleColor: values["BUBBLE_COLOR"] ?? "#1c1c24dc",
            bubbleTextColor: values["BUBBLE_TEXT_COLOR"] ?? "#e6e6e6",
            bubbleReadWordColor: values["BUBBLE_READ_WORD_COLOR"] ?? "#4da3ff",
            bubbleRevealWPM: values["BUBBLE_REVEAL_WPM"] ?? "170",
            bubbleHoldRevealWPM: values["BUBBLE_HOLD_REVEAL_WPM"] ?? "480",
            bubbleHideDelayMS: values["BUBBLE_HIDE_DELAY_MS"] ?? "3500",
            systemPromptUtility: values["SYSTEM_PROMPT_UTILITY"] ?? defaultSystemPromptUtility,
            callers: config.callers.map(SettingsCallerDraft.init(caller:))
        )
    }

    static let empty = SettingsDraft(
        llmProvider: "chatgpt",
        llmModel: "gpt-5.4",
        llmFallbacks: "",
        visionProvider: "",
        visionModel: "",
        visionFallbacks: "",
        memoryProvider: "chatgpt",
        memoryModel: "gpt-5.4",
        memoryFallbacks: "",
        toolModel: "",
        toolPluginDir: "model_tools",
        toolGitRoot: "",
        customBaseURL: "",
        githubClientID: "",
        githubOAuthScopes: "repo read:user user:email",
        ttsProvider: "none",
        cartesiaVoiceID: "",
        sttModel: "base",
        sttComputeType: "int8",
        sttLanguage: "en",
        ttsPlaybackRate: "1.0",
        ttsHoldPlaybackRate: "1.35",
        voiceHotkey: "f9",
        memoryAutoConsolidate: false,
        memoryConsolidationInterval: "15",
        memoryTopK: "3",
        memoryRelevanceMaxDistance: "0.55",
        memorySTMTokenBudget: "4000",
        contextBrowserMaxChars: "4000",
        contextAmbientDocumentMaxChars: "8000",
        contextToolDocumentMaxChars: "50000",
        addContextHotkey: "alt+q",
        clearContextHotkey: "alt+w",
        snipHotkey: "ctrl+alt+q",
        snipContextAmbient: true,
        snipContextDocuments: false,
        snipContextTools: false,
        themeMode: "system",
        iconAutoHide: false,
        iconSize: "80",
        iconBackstopMS: "5000",
        chatAutoElaborate: false,
        chatElaboratePrompt: "Please elaborate on that.",
        bubbleWidth: "340",
        bubbleLines: "3",
        bubbleColor: "#1c1c24dc",
        bubbleTextColor: "#e6e6e6",
        bubbleReadWordColor: "#4da3ff",
        bubbleRevealWPM: "170",
        bubbleHoldRevealWPM: "480",
        bubbleHideDelayMS: "3500",
        systemPromptUtility: defaultSystemPromptUtility,
        callers: WispConfig.defaultCallers.map(SettingsCallerDraft.init(caller:))
    )

    func save(environment: [String: String] = ProcessInfo.processInfo.environment) throws {
        let root = WispConfig.repoRoot(environment: environment)
        try DotEnvFile.write(
            envValues(),
            removingPrefixes: ["CALLER_"],
            to: root.appendingPathComponent(".env")
        )
    }

    func envValues() -> [String: String] {
        var values: [String: String] = [
            "LLM_PROVIDER": normalizedProvider(llmProvider, default: "chatgpt"),
            "LLM_MODEL": llmModel,
            "LLM_FALLBACKS": llmFallbacks,
            "VISION_LLM_PROVIDER": visionProvider,
            "VISION_LLM_MODEL": visionModel,
            "VISION_LLM_FALLBACKS": visionFallbacks,
            "MEMORY_LLM_PROVIDER": memoryProvider,
            "MEMORY_LLM_MODEL": memoryModel,
            "MEMORY_LLM_FALLBACKS": memoryFallbacks,
            "TOOL_LLM_MODEL": toolModel,
            "TOOL_PLUGIN_DIR": toolPluginDir,
            "TOOL_GIT_ROOT": toolGitRoot,
            "CUSTOM_BASE_URL": customBaseURL,
            "GITHUB_CLIENT_ID": githubClientID,
            "GITHUB_OAUTH_SCOPES": githubOAuthScopes,
            "TTS_PROVIDER": normalizedTTSProvider(ttsProvider),
            "CARTESIA_VOICE_ID": cartesiaVoiceID,
            "STT_MODEL": sttModel,
            "STT_COMPUTE_TYPE": sttComputeType,
            "STT_LANGUAGE": sttLanguage,
            "TTS_PLAYBACK_RATE": ttsPlaybackRate,
            "TTS_HOLD_PLAYBACK_RATE": ttsHoldPlaybackRate,
            "HOTKEY_VOICE": voiceHotkey,
            "MEMORY_AUTO_CONSOLIDATE": memoryAutoConsolidate ? "true" : "false",
            "MEMORY_CONSOLIDATION_INTERVAL": memoryConsolidationInterval,
            "MEMORY_TOP_K": memoryTopK,
            "MEMORY_RELEVANCE_MAX_DISTANCE": memoryRelevanceMaxDistance,
            "MEMORY_STM_TOKEN_BUDGET": memorySTMTokenBudget,
            "CONTEXT_BROWSER_MAX_CHARS": contextBrowserMaxChars,
            "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": contextAmbientDocumentMaxChars,
            "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": contextToolDocumentMaxChars,
            "HOTKEY_ADD_CONTEXT": addContextHotkey,
            "HOTKEY_CLEAR_CONTEXT": clearContextHotkey,
            "HOTKEY_SNIP": snipHotkey,
            "SNIP_CONTEXT_AMBIENT": snipContextAmbient ? "true" : "false",
            "SNIP_CONTEXT_DOCUMENTS": snipContextDocuments ? "true" : "false",
            "SNIP_CONTEXT_TOOLS": snipContextTools ? "true" : "false",
            "THEME_MODE": themeMode,
            "ICON_AUTO_HIDE": iconAutoHide ? "true" : "false",
            "ICON_SIZE": iconSize,
            "ICON_BACKSTOP_MS": iconBackstopMS,
            "CHAT_AUTO_ELABORATE": chatAutoElaborate ? "true" : "false",
            "CHAT_ELABORATE_PROMPT": chatElaboratePrompt,
            "BUBBLE_WIDTH": bubbleWidth,
            "BUBBLE_LINES": bubbleLines,
            "BUBBLE_COLOR": bubbleColor,
            "BUBBLE_TEXT_COLOR": bubbleTextColor,
            "BUBBLE_READ_WORD_COLOR": bubbleReadWordColor,
            "BUBBLE_REVEAL_WPM": bubbleRevealWPM,
            "BUBBLE_HOLD_REVEAL_WPM": bubbleHoldRevealWPM,
            "BUBBLE_HIDE_DELAY_MS": bubbleHideDelayMS,
            "SYSTEM_PROMPT_UTILITY": systemPromptUtility,
            "CALLER_COUNT": String(callers.count),
        ]

        for (index, caller) in callers.enumerated() {
            let n = index + 1
            values["CALLER_\(n)_HOTKEY"] = caller.hotkey
            values["CALLER_\(n)_LABEL"] = caller.label
            values["CALLER_\(n)_PASTE_BACK"] = caller.pasteBack ? "true" : "false"
            values["CALLER_\(n)_CUSTOM_KEY"] = caller.customKey
            values["CALLER_\(n)_CONTEXT_AMBIENT"] = caller.contextAmbient ? "true" : "false"
            values["CALLER_\(n)_CONTEXT_DOCUMENTS"] = caller.contextDocuments ? "true" : "false"
            values["CALLER_\(n)_CONTEXT_TOOLS"] = caller.contextTools ? "true" : "false"
            values["CALLER_\(n)_CONTEXT_SCREENSHOT"] = caller.contextScreenshot.rawValue
            values["CALLER_\(n)_CONTEXT_CLIPBOARD"] = caller.contextClipboard ? "true" : "false"
            values["CALLER_\(n)_INTENT_COUNT"] = String(caller.intents.count)

            for (intentIndex, intent) in caller.intents.enumerated() {
                let m = intentIndex + 1
                values["CALLER_\(n)_INTENT_\(m)_KEY"] = intent.key
                values["CALLER_\(n)_INTENT_\(m)_LABEL"] = intent.label
                values["CALLER_\(n)_INTENT_\(m)_HINT"] = intent.hint
                values["CALLER_\(n)_INTENT_\(m)_PROMPT"] = intent.prompt
            }
        }

        return values
    }

    private static func boolValue(_ raw: String?, default fallback: Bool) -> Bool {
        guard let raw else { return fallback }
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "1", "true", "yes", "on":
            return true
        case "0", "false", "no", "off":
            return false
        default:
            return fallback
        }
    }

    private func normalizedProvider(_ raw: String, default fallback: String) -> String {
        raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? fallback : raw
    }

    private func normalizedTTSProvider(_ raw: String) -> String {
        raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "none" : raw
    }
}

enum SettingsLLMTestRoute: String, Hashable {
    case main
    case vision
    case memory

    var routeName: String {
        switch self {
        case .main:
            return "LLM"
        case .vision:
            return "VISION_LLM"
        case .memory:
            return "MEMORY_LLM"
        }
    }

    var usesImage: Bool {
        self == .vision
    }

    func provider(in draft: SettingsDraft) -> String {
        switch self {
        case .main:
            return draft.llmProvider
        case .vision:
            return draft.visionProvider
        case .memory:
            return draft.memoryProvider
        }
    }

    func model(in draft: SettingsDraft) -> String {
        switch self {
        case .main:
            return draft.llmModel
        case .vision:
            return draft.visionModel
        case .memory:
            return draft.memoryModel
        }
    }

    func fallbacks(in draft: SettingsDraft) -> String {
        switch self {
        case .main:
            return draft.llmFallbacks
        case .vision:
            return draft.visionFallbacks
        case .memory:
            return draft.memoryFallbacks
        }
    }
}

extension SettingsCallerDraft {
    init(caller: CallerConfig) {
        self.hotkey = caller.hotkey
        self.label = caller.label
        self.pasteBack = caller.pasteBack
        self.customKey = caller.customKey
        self.contextAmbient = caller.contextAmbient
        self.contextDocuments = caller.contextDocuments
        self.contextTools = caller.contextTools
        self.contextScreenshot = caller.contextScreenshot
        self.contextClipboard = caller.contextClipboard
        self.intents = caller.intents.map(SettingsIntentDraft.init(intent:))
    }

    static let empty = SettingsCallerDraft(
        hotkey: "ctrl+option+space",
        label: "New caller",
        pasteBack: false,
        customKey: "s",
        contextAmbient: true,
        contextDocuments: true,
        contextTools: true,
        contextScreenshot: .off,
        contextClipboard: false,
        intents: [SettingsIntentDraft.empty]
    )
}

extension SettingsIntentDraft {
    init(intent: IntentConfig) {
        self.key = intent.key
        self.label = intent.label
        self.hint = intent.hint
        self.prompt = intent.prompt
    }

    static let empty = SettingsIntentDraft(
        key: "w",
        label: "New intent",
        hint: "",
        prompt: ""
    )
}

struct SettingsSecretStatus: Identifiable, Equatable {
    var name: String
    var label: String
    var source: String
    var configured: Bool
    var value: String = ""

    var id: String { name }

    var statusText: String {
        if configured {
            switch source {
            case "keychain":
                return "Stored in OS keychain"
            case "env":
                return "Loaded from environment"
            case "none":
                return "Configured"
            default:
                return source.isEmpty ? "Configured" : source
            }
        }
        return "Not configured"
    }

    init(name: String, label: String, source: String = "none", configured: Bool = false, value: String = "") {
        self.name = name
        self.label = label
        self.source = source
        self.configured = configured
        self.value = value
    }

    init?(payload: [String: Any]) {
        guard let name = payload["name"] as? String else { return nil }
        self.name = name
        self.label = payload["label"] as? String ?? name
        self.source = payload["source"] as? String ?? "none"
        self.configured = payload["configured"] as? Bool ?? false
        self.value = ""
    }

    static let defaultRows = [
        SettingsSecretStatus(name: "OPENAI_API_KEY", label: "OpenAI"),
        SettingsSecretStatus(name: "ANTHROPIC_API_KEY", label: "Anthropic"),
        SettingsSecretStatus(name: "GROQ_API_KEY", label: "Groq"),
        SettingsSecretStatus(name: "GOOGLE_API_KEY", label: "Google"),
        SettingsSecretStatus(name: "CARTESIA_API_KEY", label: "Cartesia"),
        SettingsSecretStatus(name: "ELEVENLABS_API_KEY", label: "ElevenLabs"),
        SettingsSecretStatus(name: "CUSTOM_API_KEY", label: "Custom provider"),
        SettingsSecretStatus(name: "DEEPSEEK_API_KEY", label: "DeepSeek"),
        SettingsSecretStatus(name: "OPENROUTER_API_KEY", label: "OpenRouter"),
        SettingsSecretStatus(name: "MISTRAL_API_KEY", label: "Mistral"),
        SettingsSecretStatus(name: "XAI_API_KEY", label: "xAI"),
        SettingsSecretStatus(name: "TOGETHER_API_KEY", label: "Together"),
        SettingsSecretStatus(name: "CEREBRAS_API_KEY", label: "Cerebras"),
    ]
}

struct SettingsProviderAuthStatus: Identifiable, Equatable {
    var name: String
    var label: String
    var configured: Bool
    var message: String

    var id: String { name }

    init(name: String, label: String, configured: Bool = false, message: String = "Not logged in") {
        self.name = name
        self.label = label
        self.configured = configured
        self.message = message
    }

    init?(payload: [String: Any]) {
        guard let name = payload["name"] as? String else { return nil }
        self.name = name
        self.label = payload["label"] as? String ?? name
        self.configured = payload["configured"] as? Bool ?? false
        self.message = payload["message"] as? String ?? (configured ? "Configured" : "Not configured")
    }

    static let defaultRows = [
        SettingsProviderAuthStatus(name: "chatgpt", label: "ChatGPT"),
        SettingsProviderAuthStatus(name: "github", label: "GitHub"),
        SettingsProviderAuthStatus(name: "copilot", label: "GitHub Copilot", message: "Not configured"),
    ]
}

@MainActor
final class SettingsPanel: NSPanel {

    private let model: SettingsModel

    init(
        onSave: @escaping (SettingsDraft) -> Void,
        onTestLLM: @escaping (SettingsDraft, SettingsLLMTestRoute) -> Void,
        onTestTTS: @escaping (SettingsDraft) -> Void,
        onRefreshSecrets: @escaping () -> Void,
        onSaveSecret: @escaping (SettingsSecretStatus) -> Void,
        onClearSecret: @escaping (SettingsSecretStatus) -> Void,
        onRefreshAuth: @escaping () -> Void,
        onStartChatGPTLogin: @escaping () -> Void,
        onStartGitHubLogin: @escaping (SettingsDraft) -> Void,
        onClearAuthProvider: @escaping (String) -> Void,
        onSaveCopilotToken: @escaping (String) -> Void,
        onTestCopilotToken: @escaping () -> Void,
        onResetAll: @escaping () -> Void
    ) {
        self.model = SettingsModel(
            onSave: onSave,
            onTestLLM: onTestLLM,
            onTestTTS: onTestTTS,
            onRefreshSecrets: onRefreshSecrets,
            onSaveSecret: onSaveSecret,
            onClearSecret: onClearSecret,
            onRefreshAuth: onRefreshAuth,
            onStartChatGPTLogin: onStartChatGPTLogin,
            onStartGitHubLogin: onStartGitHubLogin,
            onClearAuthProvider: onClearAuthProvider,
            onSaveCopilotToken: onSaveCopilotToken,
            onTestCopilotToken: onTestCopilotToken,
            onResetAll: onResetAll
        )
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 780, height: 620),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Settings"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 640, height: 520)
        contentView = NSHostingView(rootView: SettingsPanelView(model: model))
        center()
    }

    func showSettings(draft: SettingsDraft) {
        model.load(draft)
        model.refreshSettingsStatus()
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setStatus(_ status: String) {
        model.isSaving = false
        model.isTestingTTS = false
        model.testingLLMRoute = nil
        model.isLoadingSecrets = false
        model.isLoadingAuth = false
        model.savingSecretName = nil
        model.clearingSecretName = nil
        model.authOperation = nil
        model.isResetting = false
        model.status = status
    }

    func fail(_ message: String) {
        model.isSaving = false
        model.isTestingTTS = false
        model.testingLLMRoute = nil
        model.isLoadingSecrets = false
        model.isLoadingAuth = false
        model.savingSecretName = nil
        model.clearingSecretName = nil
        model.authOperation = nil
        model.isResetting = false
        model.status = "Settings error"
        model.errorText = message
    }

    func setLLMStatus(_ route: SettingsLLMTestRoute, message: String, ok: Bool) {
        if model.testingLLMRoute == route {
            model.testingLLMRoute = nil
        }
        model.status = ok ? "\(route.routeName) test OK" : "\(route.routeName) test failed"
        model.errorText = ok ? "" : message
        model.llmTestText[route] = message
        model.llmTestOK[route] = ok
    }

    func setTTSStatus(_ message: String, ok: Bool) {
        model.isTestingTTS = false
        model.status = ok ? "TTS test OK" : "TTS test failed"
        model.errorText = ok ? "" : message
        model.ttsTestText = message
    }

    func setSecretStatuses(_ statuses: [SettingsSecretStatus], status: String? = nil) {
        model.isLoadingSecrets = false
        model.savingSecretName = nil
        model.clearingSecretName = nil
        model.secrets = statuses
        model.errorText = ""
        if let status {
            model.status = status
        }
    }

    func setAuthStatuses(_ statuses: [SettingsProviderAuthStatus], status: String? = nil) {
        model.isLoadingAuth = false
        model.authOperation = nil
        model.authStatuses = statuses
        model.copilotToken = ""
        model.errorText = ""
        if let status {
            model.status = status
        }
    }

    func setAuthProgress(_ message: String, provider: String) {
        model.status = message
        model.errorText = ""
        if let index = model.authStatuses.firstIndex(where: { $0.name == provider }) {
            model.authStatuses[index].message = message
        }
    }
}

@MainActor
private final class SettingsModel: ObservableObject {
    @Published var draft = SettingsDraft.empty
    @Published var status = "Ready"
    @Published var errorText = ""
    @Published var llmTestText: [SettingsLLMTestRoute: String] = [:]
    @Published var llmTestOK: [SettingsLLMTestRoute: Bool] = [:]
    @Published var ttsTestText = ""
    @Published var isSaving = false
    @Published var testingLLMRoute: SettingsLLMTestRoute?
    @Published var isTestingTTS = false
    @Published var secrets = SettingsSecretStatus.defaultRows
    @Published var isLoadingSecrets = false
    @Published var savingSecretName: String?
    @Published var clearingSecretName: String?
    @Published var authStatuses = SettingsProviderAuthStatus.defaultRows
    @Published var copilotToken = ""
    @Published var isLoadingAuth = false
    @Published var authOperation: String?
    @Published var isResetting = false

    private let onSave: (SettingsDraft) -> Void
    private let onTestLLM: (SettingsDraft, SettingsLLMTestRoute) -> Void
    private let onTestTTS: (SettingsDraft) -> Void
    private let onRefreshSecrets: () -> Void
    private let onSaveSecret: (SettingsSecretStatus) -> Void
    private let onClearSecret: (SettingsSecretStatus) -> Void
    private let onRefreshAuth: () -> Void
    private let onStartChatGPTLogin: () -> Void
    private let onStartGitHubLogin: (SettingsDraft) -> Void
    private let onClearAuthProvider: (String) -> Void
    private let onSaveCopilotToken: (String) -> Void
    private let onTestCopilotToken: () -> Void
    private let onResetAll: () -> Void

    var hasBlockingOperation: Bool {
        return isSaving
            || testingLLMRoute != nil
            || isTestingTTS
            || isLoadingSecrets
            || savingSecretName != nil
            || clearingSecretName != nil
            || isLoadingAuth
            || authOperation != nil
            || isResetting
    }

    init(
        onSave: @escaping (SettingsDraft) -> Void,
        onTestLLM: @escaping (SettingsDraft, SettingsLLMTestRoute) -> Void,
        onTestTTS: @escaping (SettingsDraft) -> Void,
        onRefreshSecrets: @escaping () -> Void,
        onSaveSecret: @escaping (SettingsSecretStatus) -> Void,
        onClearSecret: @escaping (SettingsSecretStatus) -> Void,
        onRefreshAuth: @escaping () -> Void,
        onStartChatGPTLogin: @escaping () -> Void,
        onStartGitHubLogin: @escaping (SettingsDraft) -> Void,
        onClearAuthProvider: @escaping (String) -> Void,
        onSaveCopilotToken: @escaping (String) -> Void,
        onTestCopilotToken: @escaping () -> Void,
        onResetAll: @escaping () -> Void
    ) {
        self.onSave = onSave
        self.onTestLLM = onTestLLM
        self.onTestTTS = onTestTTS
        self.onRefreshSecrets = onRefreshSecrets
        self.onSaveSecret = onSaveSecret
        self.onClearSecret = onClearSecret
        self.onRefreshAuth = onRefreshAuth
        self.onStartChatGPTLogin = onStartChatGPTLogin
        self.onStartGitHubLogin = onStartGitHubLogin
        self.onClearAuthProvider = onClearAuthProvider
        self.onSaveCopilotToken = onSaveCopilotToken
        self.onTestCopilotToken = onTestCopilotToken
        self.onResetAll = onResetAll
    }

    func load(_ draft: SettingsDraft) {
        self.draft = draft
        self.status = "Ready"
        self.errorText = ""
        self.llmTestText = [:]
        self.llmTestOK = [:]
        self.ttsTestText = ""
        self.isSaving = false
        self.testingLLMRoute = nil
        self.isTestingTTS = false
        self.isLoadingSecrets = false
        self.savingSecretName = nil
        self.clearingSecretName = nil
        self.isLoadingAuth = false
        self.authOperation = nil
        self.isResetting = false
    }

    func save() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        isSaving = true
        status = "Saving..."
        onSave(draft)
    }

    func testLLM(_ route: SettingsLLMTestRoute) {
        guard !hasBlockingOperation else { return }
        errorText = ""
        llmTestText[route] = ""
        llmTestOK.removeValue(forKey: route)
        testingLLMRoute = route
        status = "Testing \(route.routeName)..."
        onTestLLM(draft, route)
    }

    func testTTS() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        ttsTestText = ""
        isTestingTTS = true
        status = "Testing TTS..."
        onTestTTS(draft)
    }

    func addCaller() {
        draft.callers.append(.empty)
    }

    func removeCaller(_ caller: SettingsCallerDraft) {
        draft.callers.removeAll { $0.id == caller.id }
    }

    func refreshSecrets() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        isLoadingSecrets = true
        status = "Loading API keys..."
        onRefreshSecrets()
    }

    func refreshSettingsStatus() {
        errorText = ""
        isLoadingSecrets = true
        isLoadingAuth = true
        status = "Loading settings status..."
        onRefreshSecrets()
        onRefreshAuth()
    }

    func saveSecret(_ secret: SettingsSecretStatus) {
        guard !hasBlockingOperation else { return }
        let value = secret.value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            errorText = "\(secret.label) key is empty"
            status = "API key not saved"
            return
        }
        errorText = ""
        savingSecretName = secret.name
        status = "Saving \(secret.label)..."
        onSaveSecret(secret)
    }

    func clearSecret(_ secret: SettingsSecretStatus) {
        guard !hasBlockingOperation else { return }
        errorText = ""
        clearingSecretName = secret.name
        status = "Clearing \(secret.label)..."
        onClearSecret(secret)
    }

    func isSecretBusy(_ secret: SettingsSecretStatus) -> Bool {
        savingSecretName == secret.name || clearingSecretName == secret.name
    }

    func refreshAuth() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        isLoadingAuth = true
        status = "Loading auth status..."
        onRefreshAuth()
    }

    func startChatGPTLogin() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        authOperation = "chatgpt"
        status = "Starting ChatGPT sign-in..."
        onStartChatGPTLogin()
    }

    func startGitHubLogin() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        authOperation = "github"
        status = "Starting GitHub sign-in..."
        onStartGitHubLogin(draft)
    }

    func clearAuthProvider(_ provider: String) {
        guard !hasBlockingOperation else { return }
        errorText = ""
        authOperation = provider
        status = "Signing out..."
        onClearAuthProvider(provider)
    }

    func saveCopilotToken() {
        guard !hasBlockingOperation else { return }
        let token = copilotToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else {
            errorText = "Copilot token is empty"
            status = "Copilot token not saved"
            return
        }
        errorText = ""
        authOperation = "copilot"
        status = "Saving Copilot token..."
        onSaveCopilotToken(token)
    }

    func testCopilotToken() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        authOperation = "copilot-test"
        status = "Testing Copilot token..."
        onTestCopilotToken()
    }

    func resetAll() {
        guard !hasBlockingOperation else { return }
        errorText = ""
        isResetting = true
        status = "Resetting settings..."
        onResetAll()
    }
}

private struct SecretKeyRow: View {
    @Binding var secret: SettingsSecretStatus
    var isBusy: Bool
    var actionsDisabled: Bool
    var onSave: () -> Void
    var onClear: () -> Void

    private var statusColor: Color {
        secret.configured ? Color.green : Color.secondary
    }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .trailing, spacing: 2) {
                Text(secret.label)
                    .foregroundStyle(.secondary)
                Text(secret.statusText)
                    .font(.caption)
                    .foregroundStyle(statusColor)
                    .lineLimit(1)
            }
            .frame(width: 135, alignment: .trailing)

            SecureField("New API key", text: $secret.value)
                .textFieldStyle(.roundedBorder)
                .foregroundStyle(SettingsInputPalette.inputText)
                .tint(SettingsInputPalette.inputText)

            Button {
                onSave()
            } label: {
                Image(systemName: "key.fill")
            }
            .buttonStyle(.borderless)
            .help("Save \(secret.label) key")
            .disabled(actionsDisabled || isBusy || secret.value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

            Button {
                onClear()
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help("Clear \(secret.label) key")
            .disabled(actionsDisabled || isBusy || !secret.configured)
        }
    }
}

private struct ProviderAuthRow: View {
    var status: SettingsProviderAuthStatus
    var actionsDisabled: Bool
    var primaryIcon: String?
    var primaryHelp: String = ""
    var onPrimary: (() -> Void)?
    var onClear: (() -> Void)?

    private var statusColor: Color {
        status.configured ? Color.green : Color.secondary
    }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .trailing, spacing: 2) {
                Text(status.label)
                    .foregroundStyle(.secondary)
                Text(status.message)
                    .font(.caption)
                    .foregroundStyle(statusColor)
                    .lineLimit(2)
            }
            .frame(width: 135, alignment: .trailing)

            if let primaryIcon, let onPrimary {
                Button {
                    onPrimary()
                } label: {
                    Image(systemName: primaryIcon)
                }
                .buttonStyle(.borderless)
                .help(primaryHelp)
                .disabled(actionsDisabled)
            }

            if let onClear {
                Button {
                    onClear()
                } label: {
                    Image(systemName: "rectangle.portrait.and.arrow.right")
                }
                .buttonStyle(.borderless)
                .help("Sign out")
                .disabled(actionsDisabled || !status.configured)
            }

            Spacer()
        }
    }
}

private struct SettingsPanelView: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            TabView {
                modelsTab
                    .tabItem { Text("Models") }
                keysTab
                    .tabItem { Text("Keys") }
                authTab
                    .tabItem { Text("Auth") }
                callersTab
                    .tabItem { Text("Callers") }
                voiceTab
                    .tabItem { Text("Voice") }
                memoryTab
                    .tabItem { Text("Memory") }
                uiTab
                    .tabItem { Text("UI") }
            }
            .padding(12)
            Divider()
            footer
        }
        .frame(minWidth: 640, minHeight: 520)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("Settings")
                .font(.system(size: 15, weight: .semibold))
            Text(model.status)
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer()
            if model.hasBlockingOperation {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .padding(.horizontal, 14)
        .frame(height: 42)
    }

    private var modelsTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("Main") {
                    ProviderPicker("Provider", selection: $model.draft.llmProvider)
                    SettingsTextField("Model", text: $model.draft.llmModel)
                    SettingsTextField("Fallbacks", text: $model.draft.llmFallbacks)
                    SettingsTextField("Tool model", text: $model.draft.toolModel)
                    SettingsTextField("Tool plugin folder", text: $model.draft.toolPluginDir)
                    SettingsTextField("Tool git root", text: $model.draft.toolGitRoot)
                    SettingsTextField("Custom base URL", text: $model.draft.customBaseURL)
                    llmTestRow(.main)
                }

                SettingsSection("Vision") {
                    ProviderPicker("Provider", selection: $model.draft.visionProvider, includeEmpty: true)
                    SettingsTextField("Model", text: $model.draft.visionModel)
                    SettingsTextField("Fallbacks", text: $model.draft.visionFallbacks)
                    llmTestRow(.vision)
                }

                SettingsSection("GitHub") {
                    SettingsTextField("Client ID", text: $model.draft.githubClientID)
                    SettingsTextField("OAuth scopes", text: $model.draft.githubOAuthScopes)
                }

                SettingsSection("Memory Model") {
                    ProviderPicker("Provider", selection: $model.draft.memoryProvider)
                    SettingsTextField("Model", text: $model.draft.memoryModel)
                    SettingsTextField("Fallbacks", text: $model.draft.memoryFallbacks)
                    llmTestRow(.memory)
                }

                SettingsSection("System Prompt") {
                    SettingsTextEditor("Utility prompt", text: $model.draft.systemPromptUtility, minHeight: 150)
                }
            }
            .padding(4)
        }
    }

    private var keysTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("API Keys") {
                    HStack(spacing: 10) {
                        Spacer()
                            .frame(width: 135)
                        Button {
                            model.refreshSecrets()
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .buttonStyle(.borderless)
                        .help("Refresh keychain status")
                        .disabled(model.hasBlockingOperation)
                        Spacer()
                    }

                    ForEach($model.secrets) { $secret in
                        SecretKeyRow(
                            secret: $secret,
                            isBusy: model.isSecretBusy(secret),
                            actionsDisabled: model.hasBlockingOperation && !model.isSecretBusy(secret),
                            onSave: { model.saveSecret(secret) },
                            onClear: { model.clearSecret(secret) }
                        )
                    }
                }
            }
            .padding(4)
        }
    }

    private var authTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("Provider Auth") {
                    HStack(spacing: 10) {
                        Spacer()
                            .frame(width: 135)
                        Button {
                            model.refreshAuth()
                        } label: {
                            Image(systemName: "arrow.clockwise")
                        }
                        .buttonStyle(.borderless)
                        .help("Refresh auth status")
                        .disabled(model.hasBlockingOperation)
                        Spacer()
                    }

                    ProviderAuthRow(
                        status: authStatus("chatgpt"),
                        actionsDisabled: model.hasBlockingOperation,
                        primaryIcon: "person.crop.circle.badge.plus",
                        primaryHelp: "Sign in with ChatGPT",
                        onPrimary: { model.startChatGPTLogin() },
                        onClear: { model.clearAuthProvider("chatgpt") }
                    )

                    ProviderAuthRow(
                        status: authStatus("github"),
                        actionsDisabled: model.hasBlockingOperation,
                        primaryIcon: "person.crop.circle.badge.plus",
                        primaryHelp: "Sign in with GitHub",
                        onPrimary: { model.startGitHubLogin() },
                        onClear: { model.clearAuthProvider("github") }
                    )
                }

                SettingsSection("GitHub Copilot") {
                    ProviderAuthRow(
                        status: authStatus("copilot"),
                        actionsDisabled: model.hasBlockingOperation,
                        primaryIcon: nil,
                        onPrimary: nil,
                        onClear: { model.clearAuthProvider("copilot") }
                    )

                    HStack(spacing: 10) {
                        Text("Token")
                            .frame(width: 135, alignment: .trailing)
                            .foregroundStyle(.secondary)
                        SecureField("GitHub Copilot token", text: $model.copilotToken)
                            .textFieldStyle(.roundedBorder)
                            .foregroundStyle(SettingsInputPalette.inputText)
                            .tint(SettingsInputPalette.inputText)
                        Button {
                            model.saveCopilotToken()
                        } label: {
                            Image(systemName: "key.fill")
                        }
                        .buttonStyle(.borderless)
                        .help("Save Copilot token")
                        .disabled(model.hasBlockingOperation || model.copilotToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button {
                            model.testCopilotToken()
                        } label: {
                            Image(systemName: "checkmark.seal")
                        }
                        .buttonStyle(.borderless)
                        .help("Test Copilot token")
                        .disabled(model.hasBlockingOperation)
                    }
                }
            }
            .padding(4)
        }
    }

    private func llmTestRow(_ route: SettingsLLMTestRoute) -> some View {
        HStack(spacing: 10) {
            Spacer()
                .frame(width: 135)
            Button {
                model.testLLM(route)
            } label: {
                Image(systemName: "checkmark.seal")
            }
            .buttonStyle(.borderless)
            .help("Test \(route.routeName) route")
            .disabled(model.hasBlockingOperation)
            Text(model.llmTestText[route] ?? "")
                .font(.caption)
                .foregroundStyle(model.llmTestOK[route] == false ? Color.red : Color.secondary)
                .lineLimit(2)
                .textSelection(.enabled)
        }
    }

    private func authStatus(_ name: String) -> SettingsProviderAuthStatus {
        model.authStatuses.first { $0.name == name }
            ?? SettingsProviderAuthStatus.defaultRows.first { $0.name == name }
            ?? SettingsProviderAuthStatus(name: name, label: name, message: "Not configured")
    }

    private var promptsTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("System Prompt") {
                    SettingsTextEditor("Utility prompt", text: $model.draft.systemPromptUtility, minHeight: 260)
                }
            }
            .padding(4)
        }
    }

    private var callersTab: some View {
        VStack(spacing: 10) {
            SettingsSection("Context Limits") {
                SettingsTextField("Browser chars", text: $model.draft.contextBrowserMaxChars)
                SettingsTextField("Auto doc chars", text: $model.draft.contextAmbientDocumentMaxChars)
                SettingsTextField("Tool doc chars", text: $model.draft.contextToolDocumentMaxChars)
            }
            .padding(4)

            SettingsSection("Context Hotkeys") {
                SettingsTextField("Add context", text: $model.draft.addContextHotkey)
                SettingsTextField("Clear context", text: $model.draft.clearContextHotkey)
            }
            .padding(4)

            HStack {
                Text("\(model.draft.callers.count) caller\(model.draft.callers.count == 1 ? "" : "s")")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    model.addCaller()
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("Add caller")
            }

            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach($model.draft.callers) { $caller in
                        CallerSettingsEditor(
                            caller: $caller,
                            onDelete: { model.removeCaller(caller) }
                        )
                    }
                }
                .padding(4)
            }
        }
    }

    private var voiceTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("Voice Query") {
                    SettingsTextField("Hotkey", text: $model.draft.voiceHotkey)
                }

                SettingsSection("TTS") {
                    PickerRow("Provider", selection: $model.draft.ttsProvider, options: ["cartesia", "elevenlabs", "none"])
                    SettingsTextField("Cartesia voice", text: $model.draft.cartesiaVoiceID)
                    SettingsTextField("Playback rate", text: $model.draft.ttsPlaybackRate)
                    SettingsTextField("Hold playback rate", text: $model.draft.ttsHoldPlaybackRate)
                    HStack(spacing: 10) {
                        Spacer()
                            .frame(width: 135)
                        Button {
                            model.testTTS()
                        } label: {
                            Image(systemName: "waveform")
                        }
                        .buttonStyle(.borderless)
                        .help("Test TTS route")
                        .disabled(model.hasBlockingOperation)
                        Text(model.ttsTestText)
                            .font(.caption)
                            .foregroundStyle(model.errorText.isEmpty ? Color.secondary : Color.red)
                            .lineLimit(2)
                            .textSelection(.enabled)
                    }
                }

                SettingsSection("STT") {
                    SettingsTextField("Model", text: $model.draft.sttModel)
                    PickerRow("Compute", selection: $model.draft.sttComputeType, options: ["int8", "float16", "float32"])
                    SettingsTextField("Language", text: $model.draft.sttLanguage)
                }
            }
            .padding(4)
        }
    }

    private var memoryTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("Consolidation") {
                    Toggle("Auto consolidate", isOn: $model.draft.memoryAutoConsolidate)
                    SettingsTextField("Interval", text: $model.draft.memoryConsolidationInterval)
                    SettingsTextField("Top K", text: $model.draft.memoryTopK)
                    SettingsTextField("Max distance", text: $model.draft.memoryRelevanceMaxDistance)
                    SettingsTextField("STM token budget", text: $model.draft.memorySTMTokenBudget)
                }

                SettingsSection("Snip") {
                    SettingsTextField("Hotkey", text: $model.draft.snipHotkey)
                    Toggle("Ambient context", isOn: $model.draft.snipContextAmbient)
                    Toggle("Open documents", isOn: $model.draft.snipContextDocuments)
                    Toggle("Tools", isOn: $model.draft.snipContextTools)
                }
            }
            .padding(4)
        }
    }

    private var uiTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("Appearance") {
                    PickerRow("Theme", selection: $model.draft.themeMode, options: ["system", "dark", "light"])
                }

                SettingsSection("Overlay") {
                    Toggle("Auto-hide icon", isOn: $model.draft.iconAutoHide)
                    SettingsTextField("Icon size", text: $model.draft.iconSize)
                    SettingsTextField("Backstop ms", text: $model.draft.iconBackstopMS)
                }

                SettingsSection("Chat") {
                    Toggle("Auto-elaborate on open", isOn: $model.draft.chatAutoElaborate)
                    SettingsTextField("Elaborate prompt", text: $model.draft.chatElaboratePrompt)
                }

                SettingsSection("Bubble") {
                    SettingsTextField("Width", text: $model.draft.bubbleWidth)
                    SettingsTextField("Lines", text: $model.draft.bubbleLines)
                    SettingsTextField("Background", text: $model.draft.bubbleColor)
                    SettingsTextField("Text", text: $model.draft.bubbleTextColor)
                    SettingsTextField("Read word", text: $model.draft.bubbleReadWordColor)
                    SettingsTextField("Reveal WPM", text: $model.draft.bubbleRevealWPM)
                    SettingsTextField("Hold WPM", text: $model.draft.bubbleHoldRevealWPM)
                    SettingsTextField("Hide delay ms", text: $model.draft.bubbleHideDelayMS)
                }
            }
            .padding(4)
        }
    }

    private var toolsTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SettingsSection("Tool Calling") {
                    SettingsTextField("Tool model", text: $model.draft.toolModel)
                    SettingsTextField("Tool git root", text: $model.draft.toolGitRoot)
                    SettingsTextField("Tool plugin folder", text: $model.draft.toolPluginDir)
                }
            }
            .padding(4)
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Button {
                model.resetAll()
            } label: {
                Text("Reset All...")
            }
            .help("Reset all settings")
            .disabled(model.hasBlockingOperation)
            Text(model.errorText.isEmpty ? model.status : model.errorText)
                .font(.system(size: 12))
                .foregroundStyle(model.errorText.isEmpty ? Color.secondary : Color.red)
                .lineLimit(2)
                .textSelection(.enabled)
            Spacer()
            Button {
                NSApp.keyWindow?.close()
            } label: {
                Text("Cancel")
            }
            .disabled(model.hasBlockingOperation)
            Button {
                model.save()
            } label: {
                Text("Apply")
            }
            .help("Save")
            .disabled(model.hasBlockingOperation)
        }
        .padding(12)
    }
}

private struct SettingsSection<Content: View>: View {
    var title: String
    var content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
            VStack(spacing: 8) {
                content
            }
        }
    }
}

private struct SettingsTextField: View {
    var label: String
    @Binding var text: String

    init(_ label: String, text: Binding<String>) {
        self.label = label
        self._text = text
    }

    var body: some View {
        HStack(spacing: 10) {
            Text(label)
                .frame(width: 135, alignment: .trailing)
                .foregroundStyle(.secondary)
            TextField(label, text: $text)
                .textFieldStyle(.roundedBorder)
                .foregroundStyle(SettingsInputPalette.inputText)
                .tint(SettingsInputPalette.inputText)
        }
    }
}

private struct SettingsTextEditor: View {
    var label: String
    @Binding var text: String
    var minHeight: CGFloat

    init(_ label: String, text: Binding<String>, minHeight: CGFloat = 100) {
        self.label = label
        self._text = text
        self.minHeight = minHeight
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(label)
                .frame(width: 135, alignment: .trailing)
                .foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.system(size: 12))
                .foregroundStyle(SettingsInputPalette.inputText)
                .scrollContentBackground(.hidden)
                .background(SettingsInputPalette.inputBackground)
                .frame(minHeight: minHeight)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color(nsColor: NSColor.separatorColor), lineWidth: 1)
                )
        }
    }
}

private struct PickerRow: View {
    var label: String
    @Binding var selection: String
    var options: [String]

    init(_ label: String, selection: Binding<String>, options: [String]) {
        self.label = label
        self._selection = selection
        self.options = options
    }

    var body: some View {
        HStack(spacing: 10) {
            Text(label)
                .frame(width: 135, alignment: .trailing)
                .foregroundStyle(.secondary)
            Picker(label, selection: $selection) {
                ForEach(options, id: \.self) { option in
                    Text(option.isEmpty ? "default" : option).tag(option)
                }
            }
            .labelsHidden()
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct ProviderPicker: View {
    var label: String
    @Binding var selection: String
    var includeEmpty: Bool

    private var providers: [String] {
        var values = ["chatgpt", "openai", "anthropic", "google", "groq", "copilot", "deepseek", "openrouter", "mistral", "xai", "together", "cerebras", "custom"]
        if includeEmpty {
            values.insert("", at: 0)
        }
        return values
    }

    init(_ label: String, selection: Binding<String>, includeEmpty: Bool = false) {
        self.label = label
        self._selection = selection
        self.includeEmpty = includeEmpty
    }

    var body: some View {
        PickerRow(label, selection: $selection, options: providers)
    }
}

private struct CallerSettingsEditor: View {
    @Binding var caller: SettingsCallerDraft
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                TextField("Label", text: $caller.label)
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(SettingsInputPalette.inputText)
                    .tint(SettingsInputPalette.inputText)
                TextField("Hotkey", text: $caller.hotkey)
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(SettingsInputPalette.inputText)
                    .tint(SettingsInputPalette.inputText)
                    .frame(width: 150)
                Button {
                    onDelete()
                } label: {
                    Image(systemName: "trash")
                }
                .buttonStyle(.borderless)
                .help("Delete caller")
            }

            HStack(spacing: 16) {
                Toggle("Paste back", isOn: $caller.pasteBack)
                Toggle("Ambient", isOn: $caller.contextAmbient)
                Toggle("Documents", isOn: $caller.contextDocuments)
                Toggle("Tools", isOn: $caller.contextTools)
                Toggle("Clipboard", isOn: $caller.contextClipboard)
                Spacer()
                Picker("Screenshot", selection: $caller.contextScreenshot) {
                    Text("Off").tag(ScreenshotMode.off)
                    Text("Auto").tag(ScreenshotMode.auto)
                    Text("Model").tag(ScreenshotMode.model)
                }
                .labelsHidden()
                .frame(width: 110)
            }

            HStack(spacing: 10) {
                Text("Custom key")
                    .foregroundStyle(.secondary)
                TextField("Key", text: $caller.customKey)
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(SettingsInputPalette.inputText)
                    .tint(SettingsInputPalette.inputText)
                    .frame(width: 60)
                Spacer()
                Button {
                    caller.intents.append(.empty)
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("Add intent")
            }

            ForEach($caller.intents) { $intent in
                IntentSettingsEditor(
                    intent: $intent,
                    onDelete: { caller.intents.removeAll { $0.id == intent.id } }
                )
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: NSColor.controlBackgroundColor))
        )
    }
}

private struct IntentSettingsEditor: View {
    @Binding var intent: SettingsIntentDraft
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                TextField("Key", text: $intent.key)
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(SettingsInputPalette.inputText)
                    .tint(SettingsInputPalette.inputText)
                    .frame(width: 54)
                TextField("Label", text: $intent.label)
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(SettingsInputPalette.inputText)
                    .tint(SettingsInputPalette.inputText)
                TextField("Hint", text: $intent.hint)
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(SettingsInputPalette.inputText)
                    .tint(SettingsInputPalette.inputText)
                Button {
                    onDelete()
                } label: {
                    Image(systemName: "minus.circle")
                }
                .buttonStyle(.borderless)
                .help("Delete intent")
            }
            TextEditor(text: $intent.prompt)
                .font(.system(size: 12))
                .foregroundStyle(SettingsInputPalette.inputText)
                .scrollContentBackground(.hidden)
                .background(SettingsInputPalette.inputBackground)
                .frame(minHeight: 54)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color(nsColor: NSColor.separatorColor), lineWidth: 1)
                )
        }
        .padding(.leading, 12)
    }
}

private enum SettingsInputPalette {
    static let inputText = Color(nsColor: NSColor.textColor)
    static let inputBackground = Color(nsColor: NSColor.textBackgroundColor)
}
