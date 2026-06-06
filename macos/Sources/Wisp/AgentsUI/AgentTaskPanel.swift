import AppKit
import SwiftUI

struct AgentTaskDraft: Equatable {
    var title: String
    var objective: String
    var requiredContext: String
    var completionCriteria: String
    var scopeFolder: String
    var provider: String
    var model: String
    var modelFallbacks: String
    var reasoningEffort: String
    var maxRuntimeMinutes: String
    var maxTurns: String
    var allowShell: Bool
    var allowNetwork: Bool
    var allowGit: Bool
    var allowFileCreate: Bool
    var allowFileEdit: Bool
    var allowFileDelete: Bool
    var agentName: String
    var agentRole: String
    var agentResponsibility: String

    static func load(
        environment: [String: String] = ProcessInfo.processInfo.environment,
        readDotEnv: Bool = true
    ) -> AgentTaskDraft {
        let values = WispConfig.loadValues(environment: environment, readDotEnv: readDotEnv)
        let root = WispConfig.repoRoot(environment: environment)
        let provider = values["LLM_PROVIDER"] ?? "same as app"
        let model = values["LLM_MODEL"] ?? ""
        return AgentTaskDraft(
            title: "",
            objective: "",
            requiredContext: "",
            completionCriteria: "Summarize changes, changed files, verification, and risks.",
            scopeFolder: root.path,
            provider: provider,
            model: model,
            modelFallbacks: values["LLM_FALLBACKS"] ?? "",
            reasoningEffort: values["REASONING_EFFORT"] ?? "medium",
            maxRuntimeMinutes: "60",
            maxTurns: "30",
            allowShell: true,
            allowNetwork: false,
            allowGit: true,
            allowFileCreate: true,
            allowFileEdit: true,
            allowFileDelete: false,
            agentName: "Builder",
            agentRole: "Implementer",
            agentResponsibility: AgentRoleDefaults.responsibility(for: "Implementer")
        )
    }

    init(
        title: String,
        objective: String,
        requiredContext: String,
        completionCriteria: String,
        scopeFolder: String,
        provider: String,
        model: String,
        modelFallbacks: String,
        reasoningEffort: String,
        maxRuntimeMinutes: String,
        maxTurns: String,
        allowShell: Bool,
        allowNetwork: Bool,
        allowGit: Bool,
        allowFileCreate: Bool,
        allowFileEdit: Bool,
        allowFileDelete: Bool,
        agentName: String,
        agentRole: String,
        agentResponsibility: String
    ) {
        self.title = title
        self.objective = objective
        self.requiredContext = requiredContext
        self.completionCriteria = completionCriteria
        self.scopeFolder = scopeFolder
        self.provider = provider
        self.model = model
        self.modelFallbacks = modelFallbacks
        self.reasoningEffort = reasoningEffort
        self.maxRuntimeMinutes = maxRuntimeMinutes
        self.maxTurns = maxTurns
        self.allowShell = allowShell
        self.allowNetwork = allowNetwork
        self.allowGit = allowGit
        self.allowFileCreate = allowFileCreate
        self.allowFileEdit = allowFileEdit
        self.allowFileDelete = allowFileDelete
        self.agentName = agentName
        self.agentRole = agentRole
        self.agentResponsibility = agentResponsibility
    }

    init?(payload: [String: Any]) {
        let agents = payload["agents"] as? [[String: Any]] ?? []
        let firstAgent = agents.first ?? [:]
        self.init(
            title: payload["title"] as? String ?? "",
            objective: payload["objective"] as? String ?? "",
            requiredContext: payload["required_context"] as? String ?? "",
            completionCriteria: payload["completion_criteria"] as? String ?? "",
            scopeFolder: payload["scope_folder"] as? String ?? "",
            provider: payload["provider"] as? String ?? "same as app",
            model: payload["model"] as? String ?? "",
            modelFallbacks: payload["model_fallbacks"] as? String ?? "",
            reasoningEffort: payload["reasoning_effort"] as? String ?? "medium",
            maxRuntimeMinutes: AgentTaskDraft.stringValue(payload["max_runtime_minutes"], default: "60"),
            maxTurns: AgentTaskDraft.stringValue(payload["max_turns"], default: "30"),
            allowShell: payload["allow_shell"] as? Bool ?? true,
            allowNetwork: payload["allow_network"] as? Bool ?? false,
            allowGit: payload["allow_git"] as? Bool ?? true,
            allowFileCreate: payload["allow_file_create"] as? Bool ?? true,
            allowFileEdit: payload["allow_file_edit"] as? Bool ?? true,
            allowFileDelete: payload["allow_file_delete"] as? Bool ?? false,
            agentName: firstAgent["name"] as? String ?? "Builder",
            agentRole: firstAgent["role"] as? String ?? "Implementer",
            agentResponsibility: firstAgent["responsibility"] as? String ?? AgentRoleDefaults.responsibility(for: "Implementer")
        )
        if cleaned(title).isEmpty || cleaned(objective).isEmpty {
            return nil
        }
    }

    var payload: [String: Any] {
        [
            "title": cleaned(title),
            "objective": cleaned(objective),
            "scope_folder": cleaned(scopeFolder),
            "sandbox_mode": "workspace-write: scope folder only",
            "approval_policy": "ask before escalation",
            "provider": cleaned(provider).isEmpty ? "same as app" : cleaned(provider),
            "model": cleaned(model),
            "reasoning_effort": cleaned(reasoningEffort).isEmpty ? "medium" : cleaned(reasoningEffort),
            "max_runtime_minutes": intValue(maxRuntimeMinutes, default: 60),
            "max_turns": intValue(maxTurns, default: 30),
            "allow_shell": allowShell,
            "allow_network": allowNetwork,
            "allow_git": allowGit,
            "allow_file_create": allowFileCreate,
            "allow_file_edit": allowFileEdit,
            "allow_file_delete": allowFileDelete,
            "shell_permission_mode": allowShell ? "auto" : "never permit",
            "network_permission_mode": allowNetwork ? "auto" : "never permit",
            "git_permission_mode": allowGit ? "auto" : "never permit",
            "file_create_permission_mode": allowFileCreate ? "auto" : "never permit",
            "file_edit_permission_mode": allowFileEdit ? "auto" : "never permit",
            "file_delete_permission_mode": allowFileDelete ? "auto" : "never permit",
            "allowed_file_globs": [],
            "blocked_file_globs": [],
            "required_context": cleaned(requiredContext),
            "completion_criteria": cleaned(completionCriteria),
            "report_format": "Summary + changed files + verification",
            "model_fallbacks": cleaned(modelFallbacks),
            "agents": [
                [
                    "name": cleaned(agentName).isEmpty ? "Builder" : cleaned(agentName),
                    "role": cleaned(agentRole).isEmpty ? "Implementer" : cleaned(agentRole),
                    "provider": "same as task",
                    "model": "same as task",
                    "responsibility": cleaned(agentResponsibility),
                ]
            ],
            "communications": [],
            "parallel_read_only_briefing": true,
            "parallel_execution": false,
            "max_parallel_agents": 4,
            "full_turn_max_tokens": 8192,
            "delta_turn_max_tokens": 6144,
            "read_only_max_tokens": 3072,
            "agent_temperature": 0.0,
            "tool_result_text_limit": 6000,
            "tool_result_command_limit": 8000,
            "tool_result_value_limit": 3000,
            "tool_result_list_limit": 120,
            "visible_files_full_limit": 200,
            "visible_files_delta_limit": 80,
        ]
    }

    func validationError() -> String? {
        if cleaned(title).isEmpty {
            return "Title is required."
        }
        if cleaned(objective).isEmpty {
            return "Objective is required."
        }
        let scope = cleaned(scopeFolder)
        if scope.isEmpty {
            return "Scope folder is required."
        }
        var isDirectory: ObjCBool = false
        if !FileManager.default.fileExists(atPath: scope, isDirectory: &isDirectory) || !isDirectory.boolValue {
            return "Scope folder does not exist."
        }
        return nil
    }

    private func cleaned(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func intValue(_ value: String, default fallback: Int) -> Int {
        Int(cleaned(value)) ?? fallback
    }

    private static func stringValue(_ value: Any?, default fallback: String) -> String {
        if let value = value as? String {
            return value
        }
        if let value = value as? Int {
            return String(value)
        }
        return fallback
    }
}

struct AgentRunResult: Equatable {
    var runDir: String
    var final: String
    var error: String
    var cancelled: Bool
}

enum AgentRoleDefaults {
    static let roles = ["Coordinator", "Planner", "Implementer", "Reviewer", "Tester", "Researcher"]

    static func responsibility(for role: String) -> String {
        switch role {
        case "Coordinator":
            return "Break down the task, assign work, merge decisions, arbitrate conflicts, and decide when the group is done."
        case "Planner":
            return "Turn the objective into a concrete plan, identify risks and dependencies, and hand clear next steps to the right agent."
        case "Reviewer":
            return "Inspect proposed changes, call out defects or missing tests, request fixes when needed, and approve completion when no Coordinator exists."
        case "Tester":
            return "Design and run focused verification, reproduce failures, report exact commands and results, and confirm fixes."
        case "Researcher":
            return "Gather read-only context before implementation: inspect files, docs, logs, APIs, and constraints, then brief the team with findings and recommendations."
        default:
            return "Make the code changes inside the selected scope, keep edits focused, run relevant checks, and report risks or blockers."
        }
    }
}

@MainActor
final class AgentTaskPanel: NSPanel {

    private let model: AgentTaskModel

    init(onStart: @escaping (AgentTaskDraft) -> Void, onCancel: @escaping () -> Void) {
        self.model = AgentTaskModel(onStart: onStart, onCancel: onCancel)
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 660),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Start Agent Task"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 680, height: 520)
        contentView = NSHostingView(rootView: AgentTaskPanelView(model: model))
        center()
    }

    func showTask() {
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func reloadDraft() {
        guard !model.isRunning else { return }
        model.draft = AgentTaskDraft.load()
        model.status = "Ready"
        model.errorText = ""
    }

    func setDraft(_ draft: AgentTaskDraft) {
        guard !model.isRunning else { return }
        model.draft = draft
        model.status = "Ready"
        model.errorText = ""
        model.finalText = ""
        model.runLines = []
        model.runDir = ""
    }

    func beginRun() {
        model.isRunning = true
        model.errorText = ""
        model.runLines = []
        model.finalText = ""
        model.runDir = ""
        model.status = "Agent running"
    }

    func appendLog(_ line: String) {
        model.appendLog(line)
    }

    func appendTrace(_ line: String) {
        model.appendLog("trace: \(line)")
    }

    func finishRun(_ result: AgentRunResult) {
        model.isRunning = false
        model.runDir = result.runDir
        model.finalText = result.final
        model.errorText = result.error
        if result.cancelled {
            model.status = "Agent cancelled"
        } else if !result.error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            model.status = "Agent failed"
        } else {
            model.status = "Agent complete"
        }
    }

    func fail(_ message: String) {
        model.isRunning = false
        model.status = "Agent error"
        model.errorText = message
    }
}

@MainActor
private final class AgentTaskModel: ObservableObject {
    @Published var draft = AgentTaskDraft.load()
    @Published var isRunning = false
    @Published var status = "Ready"
    @Published var errorText = ""
    @Published var runLines: [String] = []
    @Published var finalText = ""
    @Published var runDir = ""
    @Published var scrollToken = 0

    private let onStart: (AgentTaskDraft) -> Void
    private let onCancel: () -> Void

    init(onStart: @escaping (AgentTaskDraft) -> Void, onCancel: @escaping () -> Void) {
        self.onStart = onStart
        self.onCancel = onCancel
    }

    func chooseScope() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Use Folder"
        if FileManager.default.fileExists(atPath: draft.scopeFolder) {
            panel.directoryURL = URL(fileURLWithPath: draft.scopeFolder)
        }
        if panel.runModal() == .OK, let url = panel.url {
            draft.scopeFolder = url.path
        }
    }

    func start() {
        if let error = draft.validationError() {
            errorText = error
            status = "Agent needs input"
            return
        }
        errorText = ""
        onStart(draft)
    }

    func cancel() {
        guard isRunning else { return }
        onCancel()
        status = "Cancelling..."
    }

    func appendLog(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        runLines.append(trimmed)
        if runLines.count > 400 {
            runLines.removeFirst(runLines.count - 400)
        }
        scrollToken += 1
    }
}

private struct AgentTaskPanelView: View {
    @ObservedObject var model: AgentTaskModel
    @FocusState private var objectiveFocused: Bool

    var body: some View {
        HSplitView {
            form
                .frame(minWidth: 360, idealWidth: 440)
            runView
                .frame(minWidth: 300)
        }
        .frame(minWidth: 680, minHeight: 520)
        .onAppear { objectiveFocused = true }
    }

    private var form: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    AgentSection("Task") {
                        AgentTextField("Title", text: $model.draft.title)
                        AgentTextEditor("Objective", text: $model.draft.objective, minHeight: 110)
                            .focused($objectiveFocused)
                        AgentTextEditor("Context", text: $model.draft.requiredContext, minHeight: 74)
                        AgentTextEditor("Completion", text: $model.draft.completionCriteria, minHeight: 62)
                    }

                    AgentSection("Scope") {
                        HStack(spacing: 8) {
                            AgentTextField("Folder", text: $model.draft.scopeFolder)
                            Button {
                                model.chooseScope()
                            } label: {
                                Image(systemName: "folder")
                            }
                            .help("Choose scope folder")
                        }
                    }

                    AgentSection("Model") {
                        AgentTextField("Provider", text: $model.draft.provider)
                        AgentTextField("Model", text: $model.draft.model)
                        AgentTextEditor("Fallbacks", text: $model.draft.modelFallbacks, minHeight: 50)
                        Picker("Reasoning", selection: $model.draft.reasoningEffort) {
                            ForEach(["minimal", "low", "medium", "high"], id: \.self) { value in
                                Text(value).tag(value)
                            }
                        }
                    }

                    AgentSection("Agent") {
                        AgentTextField("Name", text: $model.draft.agentName)
                        Picker("Role", selection: $model.draft.agentRole) {
                            ForEach(AgentRoleDefaults.roles, id: \.self) { role in
                                Text(role).tag(role)
                            }
                        }
                        .onChange(of: model.draft.agentRole) { newRole in
                            model.draft.agentResponsibility = AgentRoleDefaults.responsibility(for: newRole)
                        }
                        AgentTextEditor("Responsibility", text: $model.draft.agentResponsibility, minHeight: 76)
                    }

                    AgentSection("Runtime") {
                        HStack(spacing: 10) {
                            AgentTextField("Minutes", text: $model.draft.maxRuntimeMinutes)
                            AgentTextField("Turns", text: $model.draft.maxTurns)
                        }
                        Toggle("Shell", isOn: $model.draft.allowShell)
                        Toggle("Network", isOn: $model.draft.allowNetwork)
                        Toggle("Git", isOn: $model.draft.allowGit)
                        Toggle("Create files", isOn: $model.draft.allowFileCreate)
                        Toggle("Edit files", isOn: $model.draft.allowFileEdit)
                        Toggle("Delete files", isOn: $model.draft.allowFileDelete)
                    }
                }
                .padding(14)
            }
            Divider()
            footer
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Agent Task")
                    .font(.system(size: 16, weight: .semibold))
                Text(model.status)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if model.isRunning {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if !model.errorText.isEmpty {
                Text(model.errorText)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
            Spacer()
            if model.isRunning {
                Button("Cancel") { model.cancel() }
            }
            Button("Start") { model.start() }
                .disabled(model.isRunning)
                .keyboardShortcut(.return, modifiers: [.command])
        }
        .padding(12)
    }

    private var runView: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Run")
                    .font(.system(size: 16, weight: .semibold))
                Spacer()
                if !model.runDir.isEmpty {
                    Text(model.runDir)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            Divider()

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 6) {
                        if model.runLines.isEmpty {
                            Text("No run output yet")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.top, 28)
                        } else {
                            ForEach(Array(model.runLines.enumerated()), id: \.offset) { index, line in
                                Text(line)
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .id(index)
                            }
                        }
                    }
                    .padding(12)
                }
                .onChange(of: model.scrollToken) { _ in
                    if !model.runLines.isEmpty {
                        proxy.scrollTo(model.runLines.count - 1, anchor: .bottom)
                    }
                }
            }

            if !model.finalText.isEmpty {
                Divider()
                ScrollView {
                    Text(model.finalText)
                        .font(.system(size: 13))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
                .frame(minHeight: 120, idealHeight: 180)
            }
        }
    }
}

private struct AgentSection<Content: View>: View {
    private let title: String
    private let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            content
        }
    }
}

private struct AgentTextField: View {
    let label: String
    @Binding var text: String

    init(_ label: String, text: Binding<String>) {
        self.label = label
        self._text = text
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            TextField(label, text: $text)
                .textFieldStyle(.roundedBorder)
        }
    }
}

private struct AgentTextEditor: View {
    let label: String
    @Binding var text: String
    var minHeight: CGFloat

    init(_ label: String, text: Binding<String>, minHeight: CGFloat) {
        self.label = label
        self._text = text
        self.minHeight = minHeight
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.system(size: 13))
                .frame(minHeight: minHeight)
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                )
        }
    }
}
