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
    var customBaseURL: String

    var ttsProvider: String
    var cartesiaVoiceID: String
    var sttModel: String
    var sttComputeType: String
    var sttLanguage: String
    var ttsPlaybackRate: String
    var ttsHoldPlaybackRate: String

    var memoryAutoConsolidate: Bool
    var memoryConsolidationInterval: String
    var memoryTopK: String
    var memoryRelevanceMaxDistance: String
    var memorySTMTokenBudget: String

    var snipHotkey: String
    var snipContextAmbient: Bool
    var snipContextDocuments: Bool
    var snipContextTools: Bool

    var callers: [SettingsCallerDraft]

    static func load(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        readDotEnv: Bool = true
    ) -> SettingsDraft {
        let values = WispConfig.loadValues(environment: environment, readDotEnv: readDotEnv)
        let config = WispConfig.load(environment: environment, readDotEnv: readDotEnv)

        let llmProvider = values["LLM_PROVIDER"] ?? "chatgpt"
        let llmModel = values["LLM_MODEL"] ?? "gpt-5.4"

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
            customBaseURL: values["CUSTOM_BASE_URL"] ?? "",
            ttsProvider: values["TTS_PROVIDER"] ?? "none",
            cartesiaVoiceID: values["CARTESIA_VOICE_ID"] ?? "",
            sttModel: values["STT_MODEL"] ?? "base",
            sttComputeType: values["STT_COMPUTE_TYPE"] ?? "int8",
            sttLanguage: values["STT_LANGUAGE"] ?? "en",
            ttsPlaybackRate: values["TTS_PLAYBACK_RATE"] ?? "1.0",
            ttsHoldPlaybackRate: values["TTS_HOLD_PLAYBACK_RATE"] ?? "1.35",
            memoryAutoConsolidate: boolValue(values["MEMORY_AUTO_CONSOLIDATE"], default: false),
            memoryConsolidationInterval: values["MEMORY_CONSOLIDATION_INTERVAL"] ?? "15",
            memoryTopK: values["MEMORY_TOP_K"] ?? "3",
            memoryRelevanceMaxDistance: values["MEMORY_RELEVANCE_MAX_DISTANCE"] ?? "0.55",
            memorySTMTokenBudget: values["MEMORY_STM_TOKEN_BUDGET"] ?? "4000",
            snipHotkey: values["HOTKEY_SNIP"] ?? "ctrl+alt+q",
            snipContextAmbient: boolValue(values["SNIP_CONTEXT_AMBIENT"], default: true),
            snipContextDocuments: boolValue(values["SNIP_CONTEXT_DOCUMENTS"], default: false),
            snipContextTools: boolValue(values["SNIP_CONTEXT_TOOLS"], default: false),
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
        customBaseURL: "",
        ttsProvider: "none",
        cartesiaVoiceID: "",
        sttModel: "base",
        sttComputeType: "int8",
        sttLanguage: "en",
        ttsPlaybackRate: "1.0",
        ttsHoldPlaybackRate: "1.35",
        memoryAutoConsolidate: false,
        memoryConsolidationInterval: "15",
        memoryTopK: "3",
        memoryRelevanceMaxDistance: "0.55",
        memorySTMTokenBudget: "4000",
        snipHotkey: "ctrl+alt+q",
        snipContextAmbient: true,
        snipContextDocuments: false,
        snipContextTools: false,
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
            "CUSTOM_BASE_URL": customBaseURL,
            "TTS_PROVIDER": normalizedTTSProvider(ttsProvider),
            "CARTESIA_VOICE_ID": cartesiaVoiceID,
            "STT_MODEL": sttModel,
            "STT_COMPUTE_TYPE": sttComputeType,
            "STT_LANGUAGE": sttLanguage,
            "TTS_PLAYBACK_RATE": ttsPlaybackRate,
            "TTS_HOLD_PLAYBACK_RATE": ttsHoldPlaybackRate,
            "MEMORY_AUTO_CONSOLIDATE": memoryAutoConsolidate ? "true" : "false",
            "MEMORY_CONSOLIDATION_INTERVAL": memoryConsolidationInterval,
            "MEMORY_TOP_K": memoryTopK,
            "MEMORY_RELEVANCE_MAX_DISTANCE": memoryRelevanceMaxDistance,
            "MEMORY_STM_TOKEN_BUDGET": memorySTMTokenBudget,
            "HOTKEY_SNIP": snipHotkey,
            "SNIP_CONTEXT_AMBIENT": snipContextAmbient ? "true" : "false",
            "SNIP_CONTEXT_DOCUMENTS": snipContextDocuments ? "true" : "false",
            "SNIP_CONTEXT_TOOLS": snipContextTools ? "true" : "false",
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

@MainActor
final class SettingsPanel: NSPanel {

    private let model: SettingsModel

    init(
        onSave: @escaping (SettingsDraft) -> Void,
        onTestLLM: @escaping (SettingsDraft, SettingsLLMTestRoute) -> Void,
        onTestTTS: @escaping (SettingsDraft) -> Void
    ) {
        self.model = SettingsModel(onSave: onSave, onTestLLM: onTestLLM, onTestTTS: onTestTTS)
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
        model.status = status
    }

    func fail(_ message: String) {
        model.isSaving = false
        model.isTestingTTS = false
        model.testingLLMRoute = nil
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

    private let onSave: (SettingsDraft) -> Void
    private let onTestLLM: (SettingsDraft, SettingsLLMTestRoute) -> Void
    private let onTestTTS: (SettingsDraft) -> Void

    init(
        onSave: @escaping (SettingsDraft) -> Void,
        onTestLLM: @escaping (SettingsDraft, SettingsLLMTestRoute) -> Void,
        onTestTTS: @escaping (SettingsDraft) -> Void
    ) {
        self.onSave = onSave
        self.onTestLLM = onTestLLM
        self.onTestTTS = onTestTTS
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
    }

    func save() {
        guard !isSaving, testingLLMRoute == nil, !isTestingTTS else { return }
        errorText = ""
        isSaving = true
        status = "Saving..."
        onSave(draft)
    }

    func testLLM(_ route: SettingsLLMTestRoute) {
        guard testingLLMRoute == nil, !isSaving else { return }
        errorText = ""
        llmTestText[route] = ""
        llmTestOK.removeValue(forKey: route)
        testingLLMRoute = route
        status = "Testing \(route.routeName)..."
        onTestLLM(draft, route)
    }

    func testTTS() {
        guard !isTestingTTS, testingLLMRoute == nil, !isSaving else { return }
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
                callersTab
                    .tabItem { Text("Callers") }
                voiceTab
                    .tabItem { Text("Voice") }
                memoryTab
                    .tabItem { Text("Memory") }
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
            if model.isSaving || model.isTestingTTS || model.testingLLMRoute != nil {
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
                    SettingsTextField("Custom base URL", text: $model.draft.customBaseURL)
                    llmTestRow(.main)
                }

                SettingsSection("Vision") {
                    ProviderPicker("Provider", selection: $model.draft.visionProvider, includeEmpty: true)
                    SettingsTextField("Model", text: $model.draft.visionModel)
                    SettingsTextField("Fallbacks", text: $model.draft.visionFallbacks)
                    llmTestRow(.vision)
                }

                SettingsSection("Memory Model") {
                    ProviderPicker("Provider", selection: $model.draft.memoryProvider)
                    SettingsTextField("Model", text: $model.draft.memoryModel)
                    SettingsTextField("Fallbacks", text: $model.draft.memoryFallbacks)
                    llmTestRow(.memory)
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
            .disabled(model.isSaving || model.isTestingTTS || model.testingLLMRoute != nil)
            Text(model.llmTestText[route] ?? "")
                .font(.caption)
                .foregroundStyle(model.llmTestOK[route] == false ? Color.red : Color.secondary)
                .lineLimit(2)
                .textSelection(.enabled)
        }
    }

    private var callersTab: some View {
        VStack(spacing: 10) {
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
                        .disabled(model.isTestingTTS || model.isSaving || model.testingLLMRoute != nil)
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

    private var footer: some View {
        HStack(spacing: 10) {
            if !model.errorText.isEmpty {
                Text(model.errorText)
                    .font(.system(size: 12))
                    .foregroundStyle(.red)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
            Spacer()
            Button {
                model.save()
            } label: {
                Image(systemName: "checkmark")
            }
            .help("Save")
            .disabled(model.isSaving || model.isTestingTTS || model.testingLLMRoute != nil)
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
                TextField("Hotkey", text: $caller.hotkey)
                    .textFieldStyle(.roundedBorder)
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
                    .frame(width: 54)
                TextField("Label", text: $intent.label)
                    .textFieldStyle(.roundedBorder)
                TextField("Hint", text: $intent.hint)
                    .textFieldStyle(.roundedBorder)
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
                .frame(minHeight: 54)
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Color(nsColor: NSColor.separatorColor), lineWidth: 1)
                )
        }
        .padding(.leading, 12)
    }
}
