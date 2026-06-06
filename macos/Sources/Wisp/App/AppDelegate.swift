import AppKit

private struct PendingSnipContext {
    var screenshotB64: String
    var ambientText: String
    var useTools: Bool
    var capturePath: String
}

private struct PendingNativeContext {
    var snapshot: NativeContextSnapshot
}

private enum AgentHistoryStartMode {
    case retry
    case continueRun

    var method: String {
        switch self {
        case .retry:
            return "brain.agent.history.retry_spec"
        case .continueRun:
            return "brain.agent.history.continue_spec"
        }
    }

    var statusText: String {
        switch self {
        case .retry:
            return "agent retry"
        case .continueRun:
            return "agent continue"
        }
    }
}

/// Phase-1/2 app wiring: bring up the menubar item and the floating overlay, then
/// perform the brain handshake (spawn the Python sidecar, `ping` it, stream a
/// `brain.echo`) and surface the result in the menu. This is the runnable proof
/// that the Swift host can drive the verified Python seam.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var statusController: StatusItemController?
    private var overlay: OverlayPanel?
    private var responseBubble: ResponseBubblePanel?
    private var promptPanel: PromptPanel?
    private var intentPanel: IntentPanel?
    private var chatPanel: ChatPanel?
    private var memoryPanel: MemoryPanel?
    private var settingsPanel: SettingsPanel?
    private var permissionsPanel: PermissionsPanel?
    private var pluginPanel: PluginManagerPanel?
    private var agentTaskPanel: AgentTaskPanel?
    private var agentHistoryPanel: AgentHistoryPanel?
    private var agentDiffPanel: AgentDiffPanel?
    private var snipPanel: SnipOverlayPanel?
    private var hotkey: HotkeyController?
    private var appConfig = WispConfig.load(environment: [:], readDotEnv: false)
    private let nativeContext = NativeContextController()
    private let pasteback = NativePastebackController()
    private let screenCapture = ScreenCaptureController()
    private let audioRecorder = AudioRecorder()
    private let audioPlayer = AudioPlayer()
    private var brain: BrainClient?
    private var pendingIntentContext: PendingNativeContext?
    private var pendingVoiceContext: PendingNativeContext?
    private var pendingSnip: PendingSnipContext?
    private var contextBuffer: [String] = []
    private var agentRunTask: Task<Void, Never>?
    private var activeTTSPlaybackID: Int?

    func applicationDidFinishLaunching(_ notification: Notification) {
        RunLogLocator.seedProcessEnvironmentIfMissing()
        WispConfig.seedProcessEnvironmentIfMissing()
        WispConfig.seedUserDotEnvIfMissing()
        appConfig = WispConfig.load()
        applyTheme(SettingsDraft.load().themeMode)

        let brainConfig = BrainLocator.resolve()
        NativeLaunchDiagnostics.writeStartupRecord(config: appConfig, brainConfig: brainConfig)
        let client = BrainClient(config: brainConfig)
        brain = client
        audioPlayer.onFinish = { [weak self] playbackID, success in
            self?.finishSpeechPlayback(playbackID: playbackID, successfully: success)
        }
        audioPlayer.onAmplitude = { [weak self] playbackID, amplitude in
            self?.updateSpeechAmplitude(playbackID: playbackID, amplitude: amplitude)
        }

        let prompt = PromptPanel { [weak self] text, mode in
            Task { await self?.runPrompt(text, mode: mode) }
        }
        promptPanel = prompt

        responseBubble = ResponseBubblePanel { [weak self] in
            self?.showNativeChat(new: false)
        }

        intentPanel = IntentPanel { [weak self] selection in
            Task { await self?.runIntent(selection) }
        }

        chatPanel = ChatPanel { [weak self] text in
            Task { await self?.runChatMessage(text) }
        }

        memoryPanel = MemoryPanel(
            onRefresh: { [weak self] in
                Task { await self?.loadMemoryFacts() }
            },
            onAdd: { [weak self] text, category in
                Task { await self?.addMemoryFact(text, category: category) }
            },
            onUpdate: { [weak self] fact in
                Task { await self?.updateMemoryFact(fact) }
            },
            onDelete: { [weak self] fact in
                Task { await self?.deleteMemoryFact(fact) }
            },
            onSearch: { [weak self] query in
                Task { await self?.searchMemoryPanel(query) }
            }
        )

        settingsPanel = SettingsPanel(
            onSave: { [weak self] draft in
                Task { await self?.saveSettings(draft) }
            },
            onTestLLM: { [weak self] draft, route in
                Task { await self?.testLLMSettings(draft, route: route) }
            },
            onTestTTS: { [weak self] draft in
                Task { await self?.testTTSSettings(draft) }
            },
            onRefreshSecrets: { [weak self] in
                Task { await self?.loadSettingsSecrets() }
            },
            onSaveSecret: { [weak self] secret in
                Task { await self?.saveSettingsSecret(secret) }
            },
            onClearSecret: { [weak self] secret in
                Task { await self?.clearSettingsSecret(secret) }
            }
        )

        permissionsPanel = PermissionsPanel(
            onRefresh: { [weak self] promptForAccessibility in
                self?.nativeContext.permissions(promptForAccessibility: promptForAccessibility)
                    ?? NativePermissionSnapshot(
                        accessibilityTrusted: false,
                        screenRecordingTrusted: false,
                        microphoneStatus: "unknown"
                    )
            },
            onRequest: { [weak self] kind in
                guard let self else {
                    return NativePermissionSnapshot(
                        accessibilityTrusted: false,
                        screenRecordingTrusted: false,
                        microphoneStatus: "unknown"
                    )
                }
                return await self.nativeContext.requestPermission(kind)
            }
        )

        pluginPanel = PluginManagerPanel(
            onRefresh: { [weak self] in
                Task { await self?.loadPlugins() }
            },
            onRunAction: { [weak self] plugin, action in
                Task { await self?.runPluginAction(plugin, action: action) }
            },
            onOpenFolder: { path in
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
            }
        )

        agentTaskPanel = AgentTaskPanel(
            onStart: { [weak self] draft in
                self?.startNativeAgentTask(draft)
            },
            onCancel: { [weak self] in
                self?.cancelNativeAgentTask()
            }
        )

        agentDiffPanel = AgentDiffPanel(
            onOpenFolder: { path in
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
            }
        )

        agentHistoryPanel = AgentHistoryPanel(
            onRefresh: { [weak self] in
                Task { await self?.loadAgentHistory() }
            },
            onSelectRun: { [weak self] run in
                Task { await self?.loadAgentRunDetail(run) }
            },
            onRetryRun: { [weak self] run in
                Task { await self?.startAgentHistoryRun(run, mode: .retry) }
            },
            onContinueRun: { [weak self] run in
                Task { await self?.startAgentHistoryRun(run, mode: .continueRun) }
            },
            onOpenDiff: { [weak self] detail in
                self?.showAgentDiff(detail)
            },
            onOpenFolder: { path in
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
            }
        )

        snipPanel = SnipOverlayPanel(
            onSelection: { [weak self] selection in
                self?.handleSnipSelection(selection)
            },
            onCancel: { [weak self] in
                self?.cancelSnip()
            }
        )

        let panel = OverlayPanel(
            onTap: { [weak self] in
                self?.showIntentPicker()
            },
            onRightClick: { [weak self] event in
                self?.showOverlayMenu(event)
            }
        )
        panel.showAtLaunch()
        overlay = panel

        let status = StatusItemController(
            onShowPrompt: { [weak self] in self?.showIntentPicker() },
            onRunEchoSmoke: { [weak self] in self?.runEchoSmoke() },
            onShowContext: { [weak self] in self?.showContextSnapshot() },
            onShowPermissions: { [weak self] in self?.showPermissionSnapshot() },
            onCaptureScreen: { [weak self] in self?.captureScreenSmoke() },
            onStartSnip: { [weak self] in self?.startSnip() },
            onOpenRunLogs: { [weak self] in self?.openRunLogs() },
            onOpenConfigFolder: { [weak self] in self?.openConfigFolder() },
            onShowSettings: { [weak self] in self?.showNativeSettings() },
            onShowChat: { [weak self] in self?.showNativeChat(new: false) },
            onShowNewChat: { [weak self] in self?.showNativeChat(new: true) },
            onShowMemory: { [weak self] in self?.showNativeMemory() },
            onShowPluginManager: { [weak self] in self?.showNativePluginManager() },
            onShowAgentTask: { [weak self] in self?.showNativeAgentTask() },
            onShowAgentHistory: { [weak self] in self?.showNativeAgentHistory() },
            onStartVoiceQuery: { [weak self] in self?.startVoiceQuery() },
            onStopVoiceQuery: { [weak self] in self?.stopVoiceQuery() },
            onSpeakResponse: { [weak self] in self?.speakLastResponse() },
            onRememberPrompt: { [weak self] in self?.rememberPrompt() },
            onSearchMemory: { [weak self] in self?.searchMemory() },
            onToggleOverlay: { [weak self] in self?.overlay?.toggleVisibility() },
            onToggleLoginItem: { [weak self] in self?.toggleLaunchAtLogin() },
            onRetryHotkey: { [weak self] in self?.installHotkey(promptForPermission: true) }
        )
        status.setLoginItemStatus(LoginItemController.status)
        statusController = status

        let hotkey = HotkeyController { [weak self] action in
            switch action {
            case .caller(let callerIndex):
                self?.showIntentPicker(callerIndex: callerIndex)
            case .snip:
                self?.startSnip()
            case .addContext:
                self?.addNativeContext()
            case .clearContext:
                self?.clearNativeContext()
            case .voiceStart:
                self?.startVoiceQuery()
            case .voiceStop:
                self?.stopVoiceQuery()
            }
        }
        self.hotkey = hotkey
        installHotkey(promptForPermission: true)

        Task { await self.handshake(client, status: status, panel: panel) }
    }

    func applicationWillTerminate(_ notification: Notification) {
        agentRunTask?.cancel()
        let client = brain
        Task { await client?.shutdown() }
    }

    /// Spawn + ping + a streamed echo. Mirrors the Python `test_brain_host.py`
    /// flow, but driven from Swift to validate the real transport end to end.
    private func handshake(_ client: BrainClient, status: StatusItemController, panel: OverlayPanel) async {
        do {
            panel.setState(.thinking)
            let pong = try await client.call("ping", ["value": "hello-from-swift"])
            let pid = (pong?["pid"] as? Int).map(String.init) ?? "?"
            status.setBrainStatus("ok (pid \(pid))")

            panel.setState(.speaking)
            var assembled = ""
            for try await item in client.stream("brain.echo", ["text": "the brain seam works"]) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let text = data?["text"] as? String { assembled += text }
                case .result(let result):
                    assembled = (result?["text"] as? String) ?? assembled
                default:
                    break
                }
            }
            NSLog("[wisp] echo stream assembled: %@", assembled)
            panel.setState(.idle)
        } catch {
            status.setBrainStatus("error: \(error)")
            panel.setState(.idle)
            NSLog("[wisp] brain handshake failed: %@", String(describing: error))
        }
    }

    private func showPrompt() {
        promptPanel?.showPrompt()
    }

    private func showOverlayMenu(_ event: NSEvent) {
        guard let overlay, let view = overlay.contentView else { return }
        let menu = NSMenu()

        func addItem(_ title: String, _ action: Selector) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
            item.target = self
            menu.addItem(item)
        }

        addItem("Ask Wisp", #selector(overlayMenuAskWisp))
        addItem("New Chat", #selector(overlayMenuNewChat))
        addItem("Snip Screen Region", #selector(overlayMenuSnip))
        menu.addItem(.separator())
        addItem("Settings", #selector(overlayMenuSettings))
        addItem("Open Run Logs", #selector(overlayMenuOpenLogs))
        addItem("Open Config Folder", #selector(overlayMenuOpenConfigFolder))
        menu.addItem(.separator())
        addItem("Hide Overlay", #selector(overlayMenuHide))
        let quitItem = NSMenuItem(title: "Quit Wisp", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        quitItem.target = NSApp
        menu.addItem(quitItem)

        let point = view.convert(event.locationInWindow, from: nil)
        menu.popUp(positioning: nil, at: point, in: view)
    }

    @objc private func overlayMenuAskWisp() {
        showIntentPicker()
    }

    @objc private func overlayMenuNewChat() {
        showNativeChat(new: true)
    }

    @objc private func overlayMenuSnip() {
        startSnip()
    }

    @objc private func overlayMenuSettings() {
        showNativeSettings()
    }

    @objc private func overlayMenuOpenLogs() {
        openRunLogs()
    }

    @objc private func overlayMenuOpenConfigFolder() {
        openConfigFolder()
    }

    @objc private func overlayMenuHide() {
        overlay?.orderOut(nil)
    }

    private func showIntentPicker(callerIndex: Int = 0) {
        appConfig = WispConfig.load()
        let caller = callerIndex < appConfig.callers.count ? appConfig.callers[callerIndex] : CallerConfig.empty
        pendingIntentContext = PendingNativeContext(
            snapshot: nativeContext.snapshot(promptForAccessibility: false)
        )
        intentPanel?.show(caller: caller)
    }

    private func runIntent(_ selection: IntentSelection) async {
        let pendingContext = pendingIntentContext
        pendingIntentContext = nil
        promptPanel?.setPrompt(selection.prompt)
        if selection.caller.pasteBack {
            await runPasteBack(selection.prompt, context: pendingContext)
        } else {
            await runPrompt(
                selection.prompt,
                mode: .query,
                caller: selection.caller,
                contextSnapshot: pendingContext?.snapshot
            )
        }
    }

    private func runEchoSmoke() {
        promptPanel?.showPrompt()
        Task { await self.runPrompt("tray smoke test", mode: .echo) }
    }

    private func installHotkey(promptForPermission: Bool) {
        guard let hotkey else { return }
        appConfig = WispConfig.load()
        let settings = SettingsDraft.load()
        let result = hotkey.start(
            callers: appConfig.callers,
            snip: appConfig.snip,
            addContextHotkey: settings.addContextHotkey,
            clearContextHotkey: settings.clearContextHotkey,
            voiceHotkey: settings.voiceHotkey,
            promptForPermission: promptForPermission
        )
        statusController?.setHotkeyStatus(result.statusText)
    }

    private func addNativeContext() {
        let snapshot = nativeContext.snapshot(promptForAccessibility: false)
        let selected = snapshot.selectedText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let text = selected.isEmpty ? snapshot.ambientText : selected
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            statusController?.setBrainStatus("no context captured")
            responseBubble?.showNotice("No context captured.", anchor: overlay?.frame, timeout: 2.0)
            return
        }

        contextBuffer.append(trimmed)
        statusController?.setBrainStatus("\(contextBuffer.count) context item\(contextBuffer.count == 1 ? "" : "s") queued")
        responseBubble?.showNotice("Context added for next prompt.", anchor: overlay?.frame, timeout: 2.0)
        NSLog("[wisp] native context queued: %@", snapshot.logSummary)
    }

    private func clearNativeContext() {
        contextBuffer.removeAll()
        statusController?.setBrainStatus("context cleared")
        responseBubble?.showNotice("Context cleared.", anchor: overlay?.frame, timeout: 2.0)
        NSLog("[wisp] native context buffer cleared")
    }

    private func consumeBufferedContext() -> String {
        guard !contextBuffer.isEmpty else { return "" }
        let text = contextBuffer
            .enumerated()
            .map { index, item in "[Context item \(index + 1)]\n\(item)" }
            .joined(separator: "\n\n")
        contextBuffer.removeAll()
        return text
    }

    private func showContextSnapshot() {
        let snapshot = nativeContext.snapshot(promptForAccessibility: true)
        promptPanel?.showPrompt()
        promptPanel?.setResponse(snapshot.displayText)
        statusController?.setBrainStatus("context snapshot ok")
        NSLog("[wisp] context snapshot: %@", snapshot.logSummary)
    }

    private func showPermissionSnapshot() {
        let snapshot = nativeContext.permissions(promptForAccessibility: true)
        permissionsPanel?.showPermissions(snapshot: snapshot)
        statusController?.setHotkeyStatus(snapshot.accessibilityTrusted ? "Ctrl-Option-Space ready" : "Accessibility permission needed")
        NSLog("[wisp] permission snapshot: ax=%@ screen=%@ mic=%@",
              String(describing: snapshot.accessibilityTrusted),
              String(describing: snapshot.screenRecordingTrusted),
              snapshot.microphoneStatus)
    }

    private func captureScreenSmoke() {
        promptPanel?.showPrompt()
        overlay?.setState(.thinking)

        do {
            let result = try screenCapture.captureMainDisplay(promptForPermission: true)
            promptPanel?.setResponse(result.displayText)
            overlay?.setState(.idle)
            statusController?.setBrainStatus("screen capture ok")
            NSLog("[wisp] screen capture saved: %@ (%dx%d)", result.url.path, result.width, result.height)
        } catch {
            promptPanel?.failRequest(String(describing: error))
            overlay?.setState(.idle)
            statusController?.setBrainStatus("screen capture error")
            NSLog("[wisp] screen capture failed: %@", String(describing: error))
        }
    }

    private func startSnip() {
        appConfig = WispConfig.load()
        pendingIntentContext = nil
        pendingSnip = nil
        overlay?.setState(.listening)
        statusController?.setBrainStatus("snip selecting")
        snipPanel?.showSnip()
    }

    private func handleSnipSelection(_ selection: SnipSelection) {
        do {
            let result = try screenCapture.captureRegion(selection.captureRect, promptForPermission: true)
            let data = try Data(contentsOf: result.url)
            let snapshot = nativeContext.snapshot(promptForAccessibility: false)
            let snip = appConfig.snip
            pendingSnip = PendingSnipContext(
                screenshotB64: data.base64EncodedString(),
                ambientText: snip.contextAmbient ? snapshot.ambientText(includeClipboard: false) : "",
                useTools: snip.contextTools,
                capturePath: result.url.path
            )
            let caller = snipCaller()
            pendingIntentContext = nil
            statusController?.setBrainStatus("snip captured")
            intentPanel?.show(caller: caller)
            NSLog("[wisp] snip captured: %@ (%dx%d)", result.url.path, result.width, result.height)
        } catch {
            pendingSnip = nil
            overlay?.setState(.idle)
            promptPanel?.showPrompt()
            promptPanel?.failRequest(String(describing: error))
            statusController?.setBrainStatus("snip error")
            NSLog("[wisp] snip failed: %@", String(describing: error))
        }
    }

    private func cancelSnip() {
        pendingIntentContext = nil
        pendingSnip = nil
        overlay?.setState(.idle)
        statusController?.setBrainStatus("snip cancelled")
    }

    private func snipCaller() -> CallerConfig {
        let base = appConfig.callers.first ?? CallerConfig.empty
        let snip = appConfig.snip
        return CallerConfig(
            hotkey: snip.hotkey,
            label: "Screen Snip",
            pasteBack: false,
            customKey: base.customKey,
            contextAmbient: snip.contextAmbient,
            contextDocuments: snip.contextDocuments,
            contextTools: snip.contextTools,
            contextScreenshot: .off,
            contextClipboard: false,
            intents: base.intents
        )
    }

    private func openRunLogs() {
        if !RunLogLocator.openLogDirectory() {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("Run log directory is unavailable. Launch with scripts/macos_phase1_validate.sh --run to enable this.")
            NSLog("[wisp] run log directory unavailable")
        }
    }

    private func openConfigFolder() {
        if WispConfig.openConfigDirectory() {
            statusController?.setBrainStatus("opened config folder")
        } else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("Config folder is unavailable: \(WispConfig.configDirectory().path)")
            statusController?.setBrainStatus("config folder unavailable")
        }
    }

    private func toggleLaunchAtLogin() {
        do {
            let status = try LoginItemController.toggle()
            statusController?.setLoginItemStatus(status)
            statusController?.setBrainStatus("launch at login \(status.displayText)")
        } catch {
            statusController?.setBrainStatus("launch at login error")
            NSLog("[wisp] launch-at-login toggle failed: %@", String(describing: error))
        }
    }

    private func showNativeSettings() {
        settingsPanel?.showSettings(draft: SettingsDraft.load())
        statusController?.setBrainStatus("settings opened")
    }

    private func showNativeChat(new: Bool) {
        let autoMessage = new ? nil : chatAutoElaboratePrompt()
        chatPanel?.showChat(startNew: new, autoMessage: autoMessage)
        statusController?.setBrainStatus(new ? "new chat opened" : "chat opened")
    }

    private func chatAutoElaboratePrompt() -> String? {
        guard chatPanel?.hasConversationHistory() == true else { return nil }
        let values = WispConfig.loadValues()
        guard boolValue(values["CHAT_AUTO_ELABORATE"], default: false) else { return nil }
        let prompt = (values["CHAT_ELABORATE_PROMPT"] ?? "Please elaborate on that.")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return prompt.isEmpty ? nil : prompt
    }

    private func boolValue(_ raw: String?, default fallback: Bool) -> Bool {
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

    private func showNativeMemory() {
        memoryPanel?.showMemory()
        statusController?.setBrainStatus("memory opened")
    }

    private func showNativePluginManager() {
        pluginPanel?.showPluginManager()
        statusController?.setBrainStatus("plugins opened")
    }

    private func showNativeAgentTask() {
        agentTaskPanel?.reloadDraft()
        agentTaskPanel?.showTask()
        statusController?.setBrainStatus("agent task opened")
    }

    private func startNativeAgentTask(_ draft: AgentTaskDraft) {
        cancelSpeechPlayback()
        guard agentRunTask == nil else {
            agentTaskPanel?.fail("An agent task is already running.")
            return
        }
        guard brain != nil else {
            agentTaskPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("agent error")
            return
        }

        agentTaskPanel?.beginRun()
        overlay?.setState(.thinking)
        statusController?.setBrainStatus("agent running")
        let params: [String: Any] = ["spec": draft.payload]
        agentRunTask = Task { [weak self] in
            guard let self else { return }
            await self.runNativeAgentTask(params: params)
        }
    }

    private func cancelNativeAgentTask() {
        agentRunTask?.cancel()
        statusController?.setBrainStatus("agent cancelling")
    }

    private func runNativeAgentTask(params: [String: Any]) async {
        guard let client = brain else { return }
        defer {
            agentRunTask = nil
            overlay?.setState(.idle)
        }

        var resultPayload: [String: Any]?
        do {
            for try await item in client.stream("brain.agent.run", params) {
                try Task.checkCancellation()
                switch item {
                case .event(let name, let data) where name == "agent.log":
                    if let line = data?["line"] as? String {
                        agentTaskPanel?.appendLog(line)
                    }
                case .event(let name, let data) where name == "agent.trace":
                    if let entry = data?["entry"] as? String {
                        agentTaskPanel?.appendTrace(entry)
                    }
                case .event(let name, let data) where name == "agent.done":
                    resultPayload = data
                case .result(let result):
                    resultPayload = result
                default:
                    break
                }
            }

            let result = agentRunResult(from: resultPayload)
            agentTaskPanel?.finishRun(result)
            statusController?.setBrainStatus(result.cancelled ? "agent cancelled" : "agent done")
            NSLog("[wisp] agent run finished: %@", result.runDir)
        } catch is CancellationError {
            agentTaskPanel?.finishRun(AgentRunResult(runDir: "", final: "", error: "", cancelled: true))
            statusController?.setBrainStatus("agent cancelled")
            NSLog("[wisp] agent run cancelled")
        } catch {
            let message = String(describing: error)
            agentTaskPanel?.fail(message)
            statusController?.setBrainStatus("agent error")
            NSLog("[wisp] agent run failed: %@", message)
        }
    }

    private func agentRunResult(from payload: [String: Any]?) -> AgentRunResult {
        AgentRunResult(
            runDir: payload?["run_dir"] as? String ?? "",
            final: payload?["final"] as? String ?? "",
            error: payload?["error"] as? String ?? "",
            cancelled: payload?["cancelled"] as? Bool ?? false
        )
    }

    private func showNativeAgentHistory() {
        agentHistoryPanel?.showHistory()
        statusController?.setBrainStatus("agent history opened")
    }

    private func showAgentDiff(_ detail: AgentRunDetail) {
        agentDiffPanel?.showDiff(
            title: detail.summary.title,
            runDir: detail.summary.runDir,
            diffPatch: detail.diffPatch
        )
        statusController?.setBrainStatus("agent diff opened")
    }

    private func loadAgentHistory() async {
        guard let client = brain else {
            agentHistoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("agent history error")
            return
        }

        agentHistoryPanel?.beginLoading("Loading agent runs...")
        do {
            let result = try await client.call("brain.agent.history.list", timeout: .seconds(30))
            let rows = result?["runs"] as? [[String: Any]] ?? []
            let runs = rows.compactMap { AgentRunSummary(payload: $0) }
            let runsRoot = result?["runs_root"] as? String ?? ""
            agentHistoryPanel?.setRuns(runs, runsRoot: runsRoot)
            statusController?.setBrainStatus("agent history loaded")
        } catch {
            agentHistoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("agent history error")
            NSLog("[wisp] agent history list failed: %@", String(describing: error))
        }
    }

    private func loadAgentRunDetail(_ run: AgentRunSummary) async {
        guard let client = brain else {
            agentHistoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("agent history error")
            return
        }

        agentHistoryPanel?.beginLoading("Loading run...")
        do {
            let result = try await client.call(
                "brain.agent.history.read",
                ["run_dir": run.runDir],
                timeout: .seconds(30)
            )
            if let result, let detail = AgentRunDetail(payload: result) {
                agentHistoryPanel?.setDetail(detail)
                statusController?.setBrainStatus("agent run loaded")
            } else {
                agentHistoryPanel?.fail("agent run payload was empty")
                statusController?.setBrainStatus("agent history error")
            }
        } catch {
            agentHistoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("agent history error")
            NSLog("[wisp] agent history read failed: %@", String(describing: error))
        }
    }

    private func startAgentHistoryRun(_ run: AgentRunSummary, mode: AgentHistoryStartMode) async {
        guard let client = brain else {
            agentHistoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("\(mode.statusText) error")
            return
        }

        agentHistoryPanel?.beginLoading("\(mode.statusText.capitalized)...")
        do {
            let result = try await client.call(
                mode.method,
                ["run_dir": run.runDir],
                timeout: .seconds(30)
            )
            guard
                let spec = result?["spec"] as? [String: Any],
                let draft = AgentTaskDraft(payload: spec)
            else {
                agentHistoryPanel?.fail("agent task spec payload was empty")
                statusController?.setBrainStatus("\(mode.statusText) error")
                return
            }

            agentTaskPanel?.setDraft(draft)
            agentTaskPanel?.showTask()
            startNativeAgentTask(draft)
            statusController?.setBrainStatus("\(mode.statusText) started")
        } catch {
            agentHistoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("\(mode.statusText) error")
            NSLog("[wisp] %@ failed: %@", mode.statusText, String(describing: error))
        }
    }

    private func startVoiceQuery() {
        guard !audioRecorder.isRecording else { return }
        cancelSpeechPlayback()
        appConfig = WispConfig.load()
        pendingVoiceContext = PendingNativeContext(
            snapshot: nativeContext.snapshot(promptForAccessibility: false)
        )
        promptPanel?.showPrompt()
        overlay?.setState(.listening)
        responseBubble?.showListening(anchor: overlay?.frame)
        promptPanel?.setResponse("Listening...")
        statusController?.setBrainStatus("voice recording")

        Task {
            do {
                let url = try await audioRecorder.start()
                promptPanel?.setResponse("Listening...\n\nRecording to:\n\(url.path)")
                NSLog(
                    "[wisp] voice query recording started: %@ context=%@",
                    url.path,
                    self.pendingVoiceContext?.snapshot.logSummary ?? "none"
                )
            } catch {
                pendingVoiceContext = nil
                overlay?.setState(.idle)
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("voice error")
                NSLog("[wisp] voice query start failed: %@", String(describing: error))
            }
        }
    }

    private func stopVoiceQuery() {
        guard audioRecorder.isRecording else { return }
        promptPanel?.showPrompt()
        overlay?.setState(.thinking)
        responseBubble?.startThinking(anchor: overlay?.frame)
        promptPanel?.setResponse("Stopping recording and transcribing...")
        statusController?.setBrainStatus("transcribing")

        Task {
            do {
                let recording = try audioRecorder.stop()
                let voiceContext = pendingVoiceContext
                pendingVoiceContext = nil
                promptPanel?.setResponse(recording.displayText + "\n\nTranscribing...")
                let transcript = try await transcribe(recording.url)
                guard !transcript.isEmpty else {
                    overlay?.setState(.idle)
                    promptPanel?.setResponse(recording.displayText + "\n\nNo speech detected.")
                    statusController?.setBrainStatus("voice empty")
                    return
                }

                promptPanel?.setPrompt(transcript)
                promptPanel?.setResponse("Transcript:\n\(transcript)\n\nQuerying...")
                NSLog("[wisp] voice transcript: %@", transcript)
                await self.runPrompt(
                    transcript,
                    mode: .query,
                    caller: appConfig.callers.first,
                    contextSnapshot: voiceContext?.snapshot
                )
            } catch {
                pendingVoiceContext = nil
                overlay?.setState(.idle)
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("voice error")
                NSLog("[wisp] voice query stop failed: %@", String(describing: error))
            }
        }
    }

    private func speakLastResponse() {
        let text = promptPanel?.currentResponse().trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("No response to speak yet.")
            return
        }

        promptPanel?.showPrompt()
        statusController?.setBrainStatus("tts synthesizing")
        overlay?.setState(.speaking)

        Task {
            do {
                let url = try await synthesizeSpeech(text)
                let playbackID = try audioPlayer.play(url: url)
                activeTTSPlaybackID = playbackID
                statusController?.setBrainStatus("tts playing")
                promptPanel?.setResponse("\(text)\n\nSpeaking:\n\(url.path)")
                NSLog("[wisp] tts playback started id=%d", playbackID)
            } catch {
                activeTTSPlaybackID = nil
                overlay?.setState(.idle)
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("tts error")
                NSLog("[wisp] tts failed: %@", String(describing: error))
            }
        }
    }

    private func finishSpeechPlayback(playbackID: Int, successfully: Bool) {
        guard activeTTSPlaybackID == playbackID else { return }
        activeTTSPlaybackID = nil
        overlay?.setSpeechAmplitude(0)
        overlay?.setState(.idle)
        statusController?.setBrainStatus(successfully ? "tts finished" : "tts stopped")
    }

    private func cancelSpeechPlayback() {
        activeTTSPlaybackID = nil
        overlay?.setSpeechAmplitude(0)
        audioPlayer.stop()
    }

    private func updateSpeechAmplitude(playbackID: Int, amplitude: Double) {
        guard activeTTSPlaybackID == playbackID else { return }
        overlay?.setSpeechAmplitude(amplitude)
    }

    private func transcribe(_ url: URL) async throws -> String {
        guard let client = brain else { throw BrainError.notRunning }
        let result = try await client.call("brain.transcribe", ["pcm_path": url.path], timeout: .seconds(120))
        return result?["text"] as? String ?? ""
    }

    private func synthesizeSpeech(_ text: String) async throws -> URL {
        guard let client = brain else { throw BrainError.notRunning }
        let result = try await client.call("brain.tts.synthesize", ["text": text], timeout: .seconds(120))
        guard let path = result?["path"] as? String else {
            throw BrainError.malformedResponse
        }
        if let bytes = result?["bytes"] as? Int, bytes == 0 {
            throw BrainError.remote("TTS produced no audio; check TTS_PROVIDER and API keys")
        }
        return URL(fileURLWithPath: path)
    }

    private func rememberPrompt() {
        let text = promptPanel?.currentPrompt().trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("No prompt text to remember.")
            return
        }

        promptPanel?.showPrompt()
        promptPanel?.setResponse("Saving memory...")
        statusController?.setBrainStatus("memory saving")

        Task {
            do {
                guard let client = brain else { throw BrainError.notRunning }
                let result = try await client.call("brain.memory.add", ["text": text], timeout: .seconds(60))
                let stored = result?["text"] as? String ?? text
                promptPanel?.setResponse("Remembered:\n\(stored)")
                statusController?.setBrainStatus("memory saved")
                if memoryPanel?.isVisible == true {
                    await self.loadMemoryFacts()
                }
                NSLog("[wisp] memory saved: %@", stored)
            } catch {
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("memory error")
                NSLog("[wisp] memory save failed: %@", String(describing: error))
            }
        }
    }

    private func searchMemory() {
        let text = promptPanel?.currentPrompt().trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("Type a memory search query in the prompt first.")
            return
        }

        promptPanel?.showPrompt()
        promptPanel?.setResponse("Searching memory...")
        statusController?.setBrainStatus("memory searching")

        Task {
            do {
                guard let client = brain else { throw BrainError.notRunning }
                let result = try await client.call("brain.memory.search", ["query": text, "top_k": 5], timeout: .seconds(60))
                let found = (result?["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines)
                promptPanel?.setResponse(found?.isEmpty == false ? found! : "No relevant memory found.")
                statusController?.setBrainStatus("memory searched")
                NSLog("[wisp] memory searched for: %@", text)
            } catch {
                promptPanel?.failRequest(String(describing: error))
                statusController?.setBrainStatus("memory error")
                NSLog("[wisp] memory search failed: %@", String(describing: error))
            }
        }
    }

    private func loadMemoryFacts() async {
        guard let client = brain else {
            memoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("memory error")
            return
        }

        memoryPanel?.beginLoading("Loading memory...")
        do {
            let result = try await client.call("brain.memory.list", timeout: .seconds(60))
            let rows = result?["facts"] as? [[String: Any]] ?? []
            let facts = rows.compactMap { MemoryFact(payload: $0) }
            memoryPanel?.setFacts(facts)
            statusController?.setBrainStatus("memory loaded")
        } catch {
            memoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("memory error")
            NSLog("[wisp] memory list failed: %@", String(describing: error))
        }
    }

    private func addMemoryFact(_ text: String, category: String) async {
        guard let client = brain else {
            memoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("memory error")
            return
        }

        memoryPanel?.beginLoading("Saving memory...")
        do {
            _ = try await client.call(
                "brain.memory.add",
                ["text": text, "category": category],
                timeout: .seconds(60)
            )
            await loadMemoryFacts()
            memoryPanel?.setStatus("Memory saved")
            statusController?.setBrainStatus("memory saved")
            NSLog("[wisp] memory panel saved fact")
        } catch {
            memoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("memory error")
            NSLog("[wisp] memory panel save failed: %@", String(describing: error))
        }
    }

    private func updateMemoryFact(_ fact: MemoryFact) async {
        guard let client = brain else {
            memoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("memory error")
            return
        }

        memoryPanel?.beginLoading("Saving memory...")
        do {
            _ = try await client.call(
                "brain.memory.update",
                ["fact_id": fact.id, "text": fact.text, "category": fact.category],
                timeout: .seconds(60)
            )
            await loadMemoryFacts()
            memoryPanel?.setStatus("Memory updated")
            statusController?.setBrainStatus("memory updated")
            NSLog("[wisp] memory panel updated fact %@", fact.id)
        } catch {
            memoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("memory error")
            NSLog("[wisp] memory panel update failed: %@", String(describing: error))
        }
    }

    private func deleteMemoryFact(_ fact: MemoryFact) async {
        guard let client = brain else {
            memoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("memory error")
            return
        }

        memoryPanel?.beginLoading("Deleting memory...")
        do {
            _ = try await client.call(
                "brain.memory.delete",
                ["fact_id": fact.id],
                timeout: .seconds(60)
            )
            await loadMemoryFacts()
            memoryPanel?.setStatus("Memory deleted")
            statusController?.setBrainStatus("memory deleted")
            NSLog("[wisp] memory panel deleted fact %@", fact.id)
        } catch {
            memoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("memory error")
            NSLog("[wisp] memory panel delete failed: %@", String(describing: error))
        }
    }

    private func searchMemoryPanel(_ query: String) async {
        guard let client = brain else {
            memoryPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("memory error")
            return
        }

        memoryPanel?.beginLoading("Searching memory...")
        do {
            let result = try await client.call(
                "brain.memory.search",
                ["query": query, "top_k": 5],
                timeout: .seconds(60)
            )
            let found = result?["text"] as? String ?? ""
            memoryPanel?.setSearchResult(query: query, text: found)
            statusController?.setBrainStatus("memory searched")
            NSLog("[wisp] memory panel searched for: %@", query)
        } catch {
            memoryPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("memory error")
            NSLog("[wisp] memory panel search failed: %@", String(describing: error))
        }
    }

    private func loadSettingsSecrets(statusMessage: String? = nil) async {
        guard let client = brain else {
            settingsPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("settings key error")
            return
        }

        do {
            let result = try await client.call("brain.secrets.status", timeout: .seconds(30))
            let rows = result?["secrets"] as? [[String: Any]] ?? []
            let statuses = rows.compactMap { SettingsSecretStatus(payload: $0) }
            settingsPanel?.setSecretStatuses(
                statuses.isEmpty ? SettingsSecretStatus.defaultRows : statuses,
                status: statusMessage ?? "API keys loaded"
            )
            statusController?.setBrainStatus("api keys loaded")
        } catch {
            settingsPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("settings key error")
            NSLog("[wisp] api key status failed: %@", String(describing: error))
        }
    }

    private func saveSettingsSecret(_ secret: SettingsSecretStatus) async {
        guard let client = brain else {
            settingsPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("settings key error")
            return
        }

        let value = secret.value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            settingsPanel?.fail("\(secret.label) key is empty")
            statusController?.setBrainStatus("settings key error")
            return
        }

        do {
            _ = try await client.call(
                "brain.secrets.set",
                ["name": secret.name, "value": value],
                timeout: .seconds(60)
            )
            try await reloadBrainConfig()
            await loadSettingsSecrets(statusMessage: "\(secret.label) key saved")
            NSLog("[wisp] api key saved: %@", secret.name)
        } catch {
            settingsPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("settings key error")
            NSLog("[wisp] api key save failed for %@: %@", secret.name, String(describing: error))
        }
    }

    private func clearSettingsSecret(_ secret: SettingsSecretStatus) async {
        guard let client = brain else {
            settingsPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("settings key error")
            return
        }

        do {
            _ = try await client.call(
                "brain.secrets.clear",
                ["name": secret.name],
                timeout: .seconds(60)
            )
            try await reloadBrainConfig()
            await loadSettingsSecrets(statusMessage: "\(secret.label) key cleared")
            NSLog("[wisp] api key cleared: %@", secret.name)
        } catch {
            settingsPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("settings key error")
            NSLog("[wisp] api key clear failed for %@: %@", secret.name, String(describing: error))
        }
    }

    private func saveSettings(_ draft: SettingsDraft) async {
        do {
            try draft.save()
            applyTheme(draft.themeMode)
            appConfig = WispConfig.load()
            installHotkey(promptForPermission: false)
            try await reloadBrainConfig()
            settingsPanel?.setStatus("Settings saved")
            statusController?.setBrainStatus("settings saved")
            NSLog("[wisp] native settings saved")
        } catch {
            settingsPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("settings error")
            NSLog("[wisp] native settings save failed: %@", String(describing: error))
        }
    }

    private func applyTheme(_ raw: String) {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "dark":
            NSApp.appearance = NSAppearance(named: .darkAqua)
        case "light":
            NSApp.appearance = NSAppearance(named: .aqua)
        default:
            NSApp.appearance = nil
        }
        NSLog("[wisp] theme applied: %@", raw)
    }

    private func testTTSSettings(_ draft: SettingsDraft) async {
        guard let client = brain else {
            settingsPanel?.setTTSStatus("brain client is not available", ok: false)
            statusController?.setBrainStatus("tts test error")
            return
        }

        do {
            let values = draft.envValues()
            let result = try await client.call(
                "brain.tts.test",
                [
                    "provider": values["TTS_PROVIDER"] ?? "none",
                    "cartesia_voice_id": values["CARTESIA_VOICE_ID"] ?? "",
                ],
                timeout: .seconds(60)
            )
            let ok = result?["ok"] as? Bool ?? false
            let message = result?["message"] as? String ?? (ok ? "TTS route OK" : "TTS test failed")
            settingsPanel?.setTTSStatus(message, ok: ok)
            statusController?.setBrainStatus(ok ? "tts test ok" : "tts test failed")
            NSLog("[wisp] tts settings test: %@", message)
        } catch {
            let message = String(describing: error)
            settingsPanel?.setTTSStatus(message, ok: false)
            statusController?.setBrainStatus("tts test error")
            NSLog("[wisp] tts settings test failed: %@", message)
        }
    }

    private func testLLMSettings(_ draft: SettingsDraft, route: SettingsLLMTestRoute) async {
        guard let client = brain else {
            settingsPanel?.setLLMStatus(route, message: "brain client is not available", ok: false)
            statusController?.setBrainStatus("llm test error")
            return
        }

        do {
            let result = try await client.call(
                "brain.llm.test",
                [
                    "provider": route.provider(in: draft),
                    "model": route.model(in: draft),
                    "route_name": route.routeName,
                    "image": route.usesImage,
                    "custom_base_url": draft.customBaseURL,
                ],
                timeout: .seconds(60)
            )
            let ok = result?["ok"] as? Bool ?? false
            let message = result?["message"] as? String ?? (ok ? "\(route.routeName) route OK" : "\(route.routeName) test failed")
            settingsPanel?.setLLMStatus(route, message: message, ok: ok)
            statusController?.setBrainStatus(ok ? "llm test ok" : "llm test failed")
            NSLog("[wisp] %@ settings test: %@", route.routeName, message)
        } catch {
            let message = String(describing: error)
            settingsPanel?.setLLMStatus(route, message: message, ok: false)
            statusController?.setBrainStatus("llm test error")
            NSLog("[wisp] %@ settings test failed: %@", route.routeName, message)
        }
    }

    private func reloadBrainConfig() async throws {
        guard let client = brain else { throw BrainError.notRunning }
        _ = try await client.call("brain.config.reload", timeout: .seconds(30))
    }

    private func loadPlugins(statusMessage: String? = nil) async {
        guard let client = brain else {
            pluginPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("plugins error")
            return
        }

        pluginPanel?.beginLoading("Loading plugins...")
        do {
            let result = try await client.call("brain.plugins.list", timeout: .seconds(30))
            let rows = result?["plugins"] as? [[String: Any]] ?? []
            let plugins = rows.compactMap { PluginSummary(payload: $0) }
            let pluginsDir = result?["plugins_dir"] as? String ?? ""
            pluginPanel?.setPlugins(plugins, pluginsDir: pluginsDir, status: statusMessage)
            statusController?.setBrainStatus("plugins loaded")
        } catch {
            pluginPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("plugins error")
            NSLog("[wisp] plugin list failed: %@", String(describing: error))
        }
    }

    private func runPluginAction(_ plugin: PluginSummary, action: String) async {
        guard let client = brain else {
            pluginPanel?.fail("brain client is not available")
            statusController?.setBrainStatus("plugin action error")
            return
        }

        pluginPanel?.beginLoading("Running \(action)...")
        do {
            let result = try await client.call(
                "brain.plugins.run_action",
                ["plugin_name": plugin.name, "label": action],
                timeout: .seconds(60)
            )
            let message = result?["message"] as? String ?? "Plugin action complete"
            statusController?.setBrainStatus("plugin action ok")
            NSLog("[wisp] %@", message)
            await loadPlugins(statusMessage: message)
        } catch {
            pluginPanel?.fail(String(describing: error))
            statusController?.setBrainStatus("plugin action error")
            NSLog("[wisp] plugin action failed: %@", String(describing: error))
        }
    }

    private func runChatMessage(_ text: String) async {
        guard let client = brain else {
            chatPanel?.failAssistant("brain client is not available")
            statusController?.setBrainStatus("chat error")
            return
        }

        cancelSpeechPlayback()
        chatPanel?.showChat(startNew: false)
        let messages = chatPanel?.beginUserMessage(text) ?? [["role": "user", "content": text]]
        overlay?.setState(.thinking)
        statusController?.setBrainStatus("chat running")

        do {
            var assembled = ""
            for try await item in client.stream("brain.chat", ["messages": messages]) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let chunk = data?["text"] as? String {
                        assembled += chunk
                        chatPanel?.appendAssistantChunk(chunk)
                    }
                case .result(let result):
                    if let finalText = result?["text"] as? String {
                        assembled = finalText
                        chatPanel?.finishAssistant(finalText)
                    }
                default:
                    break
                }
            }
            if assembled.isEmpty {
                chatPanel?.failAssistant("No reply from model. Check model name or API key in Settings.")
            } else {
                chatPanel?.finishAssistant(assembled)
            }
            overlay?.setState(.idle)
            statusController?.setBrainStatus("chat ok")
        } catch {
            let message = String(describing: error)
            chatPanel?.failAssistant(message)
            overlay?.setState(.idle)
            statusController?.setBrainStatus("chat error")
            NSLog("[wisp] chat failed: %@", message)
        }
    }

    private func runPasteBack(_ intentPrompt: String, context: PendingNativeContext?) async {
        guard let client = brain else {
            promptPanel?.failRequest("brain client is not available")
            statusController?.setBrainStatus("paste-back error")
            return
        }

        cancelSpeechPlayback()
        let selected = context?.snapshot.selectedText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !selected.isEmpty else {
            promptPanel?.showPrompt()
            promptPanel?.setResponse("No selected text was captured for paste-back.")
            responseBubble?.showNotice("No selected text was captured for paste-back.", anchor: overlay?.frame)
            overlay?.setState(.idle)
            statusController?.setBrainStatus("paste-back skipped")
            return
        }

        promptPanel?.beginRequest(mode: .query)
        overlay?.setState(.thinking)
        responseBubble?.startThinking(anchor: overlay?.frame)
        statusController?.setBrainStatus("paste-back running")

        var assembled = ""
        do {
            for try await item in client.stream(
                "brain.rewrite",
                ["intent_prompt": intentPrompt, "selected_text": selected]
            ) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let chunk = data?["text"] as? String {
                        assembled += chunk
                        responseBubble?.appendChunk(chunk)
                        promptPanel?.setResponse(assembled)
                    }
                case .result(let result):
                    if let finalText = result?["text"] as? String {
                        assembled = finalText
                        responseBubble?.setText(finalText)
                        promptPanel?.setResponse(finalText)
                    }
                default:
                    break
                }
            }

            let reply = assembled.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !reply.isEmpty else {
                promptPanel?.setResponse("(empty rewrite)")
                responseBubble?.showNotice("The rewrite was empty.", anchor: overlay?.frame)
                statusController?.setBrainStatus("paste-back empty")
                overlay?.setState(.idle)
                promptPanel?.finishRequest()
                return
            }

            try await pasteback.paste(reply, intoProcessID: context?.snapshot.processID)
            promptPanel?.setResponse(reply)
            responseBubble?.finish()
            chatPanel?.recordExchange(user: "\(intentPrompt):\n\n\(selected)", assistant: reply)
            statusController?.setBrainStatus("paste-back ok")
            NSLog("[wisp] paste-back completed for %@", context?.snapshot.appName ?? "unknown app")
        } catch {
            let message = String(describing: error)
            promptPanel?.failRequest(message)
            responseBubble?.showNotice("Paste-back error: \(message)", anchor: overlay?.frame)
            statusController?.setBrainStatus("paste-back error")
            NSLog("[wisp] paste-back failed: %@", message)
        }

        overlay?.setState(.idle)
        promptPanel?.finishRequest()
    }

    private func runPrompt(
        _ text: String,
        mode: PromptMode,
        caller: CallerConfig? = nil,
        contextSnapshot: NativeContextSnapshot? = nil
    ) async {
        guard let client = brain else {
            promptPanel?.failRequest("brain client is not available")
            statusController?.setBrainStatus("error: missing client")
            return
        }

        cancelSpeechPlayback()
        promptPanel?.beginRequest(mode: mode)
        overlay?.setState(mode == .echo ? .speaking : .thinking)
        if mode == .echo {
            responseBubble?.showNotice("Running local echo smoke...", anchor: overlay?.frame, timeout: 2.0)
        } else {
            responseBubble?.startThinking(anchor: overlay?.frame)
        }
        statusController?.setBrainStatus("\(mode.rawValue.lowercased()) running")

        do {
            let params = try paramsForPrompt(text, mode: mode, caller: caller, contextSnapshot: contextSnapshot)
            var assembled = ""

            for try await item in client.stream(mode.method, params) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let chunk = data?["text"] as? String {
                        assembled += chunk
                        responseBubble?.appendChunk(chunk)
                        promptPanel?.setResponse(assembled)
                    }
                case .result(let result):
                    if let finalText = result?["text"] as? String {
                        assembled = finalText
                        responseBubble?.setText(finalText)
                        promptPanel?.setResponse(finalText)
                    }
                default:
                    break
                }
            }

            if assembled.isEmpty {
                promptPanel?.setResponse("(empty response)")
                responseBubble?.showNotice("No reply from model. Check model name or API key in Settings.", anchor: overlay?.frame)
            } else {
                responseBubble?.finish()
                if mode != .echo {
                    chatPanel?.recordExchange(user: text, assistant: assembled)
                }
            }
            promptPanel?.finishRequest()
            overlay?.setState(.idle)
            statusController?.setBrainStatus("\(mode.rawValue.lowercased()) ok")
            NSLog("[wisp] %@ prompt completed: %@", mode.rawValue, assembled)
        } catch {
            let message = String(describing: error)
            promptPanel?.failRequest(message)
            responseBubble?.showNotice("Error: \(message)", anchor: overlay?.frame)
            overlay?.setState(.idle)
            statusController?.setBrainStatus("error: \(error)")
            NSLog("[wisp] %@ prompt failed: %@", mode.rawValue, String(describing: error))
        }
    }

    private func paramsForPrompt(
        _ text: String,
        mode: PromptMode,
        caller: CallerConfig? = nil,
        contextSnapshot: NativeContextSnapshot? = nil
    ) throws -> [String: Any] {
        switch mode {
        case .echo:
            return ["text": text, "chunk_size": 1, "delay": 0.02]
        case .query, .queryScreen:
            if let snip = pendingSnip {
                pendingSnip = nil
                var ambient = snip.ambientText
                let bufferedContext = consumeBufferedContext()
                if !bufferedContext.isEmpty {
                    ambient += "\(ambient.isEmpty ? "" : "\n\n")[Buffered context]\n\(bufferedContext)"
                }
                if !snip.capturePath.isEmpty {
                    ambient += "\(ambient.isEmpty ? "" : "\n\n")Screen snip saved: \(snip.capturePath)"
                }
                return [
                    "intent_prompt": text,
                    "ambient_text": ambient,
                    "screenshot_b64": snip.screenshotB64,
                    "use_tools": snip.useTools,
                    "allow_screenshot_tool": false,
                ]
            }
            let policy = caller ?? (appConfig.callers.first ?? CallerConfig.empty)
            let snapshot = contextSnapshot ?? nativeContext.snapshot(promptForAccessibility: false)
            let bufferedContext = consumeBufferedContext()
            var ambientText = policy.contextAmbient ? snapshot.ambientText(includeClipboard: policy.contextClipboard) : ""
            if !bufferedContext.isEmpty {
                ambientText += "\(ambientText.isEmpty ? "" : "\n\n")[Buffered context]\n\(bufferedContext)"
            }
            var params: [String: Any] = [
                "intent_prompt": text,
                "ambient_text": ambientText,
                "use_tools": policy.contextTools,
                "allow_screenshot_tool": policy.contextScreenshot == .model,
            ]
            if let selected = snapshot.selectedText, !selected.isEmpty {
                params["selected"] = selected
            }
            if mode == .queryScreen || policy.contextScreenshot == .auto {
                let capture = try screenCapture.captureMainDisplay(promptForPermission: true)
                let data = try Data(contentsOf: capture.url)
                params["screenshot_b64"] = data.base64EncodedString()
                let baseAmbient = params["ambient_text"] as? String ?? ""
                params["ambient_text"] = "\(baseAmbient)\(baseAmbient.isEmpty ? "" : "\n\n")Screenshot saved: \(capture.url.path)"
                NSLog("[wisp] query screenshot attached: %@ (%dx%d)", capture.url.path, capture.width, capture.height)
            }
            return params
        }
    }
}
