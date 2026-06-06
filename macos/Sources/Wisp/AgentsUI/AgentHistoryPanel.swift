import AppKit
import SwiftUI

struct AgentRunSummary: Identifiable, Equatable {
    var id: String
    var runDir: String
    var title: String
    var objective: String
    var status: String
    var modifiedDisplay: String
    var hasFinal: Bool
    var hasError: Bool
    var hasDiff: Bool

    init?(payload: [String: Any]) {
        let runDir = payload["run_dir"] as? String ?? ""
        guard !runDir.isEmpty else { return nil }
        self.id = payload["id"] as? String ?? runDir
        self.runDir = runDir
        self.title = payload["title"] as? String ?? id
        self.objective = payload["objective"] as? String ?? ""
        self.status = payload["status"] as? String ?? "unknown"
        self.modifiedDisplay = payload["modified_display"] as? String ?? ""
        self.hasFinal = payload["has_final"] as? Bool ?? false
        self.hasError = payload["has_error"] as? Bool ?? false
        self.hasDiff = payload["has_diff"] as? Bool ?? false
    }
}

struct AgentRunDetail: Equatable {
    var summary: AgentRunSummary
    var taskJSON: String
    var final: String
    var error: String
    var runLog: String
    var diffPatch: String

    init?(payload: [String: Any]) {
        guard let summary = AgentRunSummary(payload: payload) else { return nil }
        self.summary = summary
        self.taskJSON = payload["task_json"] as? String ?? ""
        self.final = payload["final"] as? String ?? ""
        self.error = payload["error"] as? String ?? ""
        self.runLog = payload["run_log"] as? String ?? ""
        self.diffPatch = payload["diff_patch"] as? String ?? ""
    }
}

@MainActor
final class AgentHistoryPanel: NSPanel {

    private let model: AgentHistoryModel

    init(
        onRefresh: @escaping () -> Void,
        onSelectRun: @escaping (AgentRunSummary) -> Void,
        onRetryRun: @escaping (AgentRunSummary) -> Void,
        onContinueRun: @escaping (AgentRunSummary) -> Void,
        onOpenFolder: @escaping (String) -> Void
    ) {
        self.model = AgentHistoryModel(
            onRefresh: onRefresh,
            onSelectRun: onSelectRun,
            onRetryRun: onRetryRun,
            onContinueRun: onContinueRun,
            onOpenFolder: onOpenFolder
        )
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 920, height: 620),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Agent Task History"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 720, height: 480)
        contentView = NSHostingView(rootView: AgentHistoryPanelView(model: model))
        center()
    }

    func showHistory() {
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        model.refresh()
    }

    func beginLoading(_ status: String) {
        model.isLoading = true
        model.status = status
        model.errorText = ""
    }

    func setRuns(_ runs: [AgentRunSummary], runsRoot: String) {
        model.runs = runs
        model.runsRoot = runsRoot
        model.isLoading = false
        model.status = runs.isEmpty ? "No agent runs" : "\(runs.count) run\(runs.count == 1 ? "" : "s")"
        if let selected = model.selectedRun, !runs.contains(where: { $0.runDir == selected.runDir }) {
            model.selectedRun = nil
            model.detail = nil
        }
        if model.selectedRun == nil, let first = runs.first {
            model.select(first)
        }
    }

    func setDetail(_ detail: AgentRunDetail) {
        model.detail = detail
        model.selectedRun = detail.summary
        model.isLoading = false
        model.status = detail.summary.status
    }

    func fail(_ message: String) {
        model.isLoading = false
        model.status = "History error"
        model.errorText = message
    }
}

@MainActor
private final class AgentHistoryModel: ObservableObject {
    @Published var runs: [AgentRunSummary] = []
    @Published var selectedRun: AgentRunSummary?
    @Published var detail: AgentRunDetail?
    @Published var runsRoot = ""
    @Published var status = "Ready"
    @Published var errorText = ""
    @Published var isLoading = false

    private let onRefresh: () -> Void
    private let onSelectRun: (AgentRunSummary) -> Void
    private let onRetryRun: (AgentRunSummary) -> Void
    private let onContinueRun: (AgentRunSummary) -> Void
    private let onOpenFolder: (String) -> Void

    init(
        onRefresh: @escaping () -> Void,
        onSelectRun: @escaping (AgentRunSummary) -> Void,
        onRetryRun: @escaping (AgentRunSummary) -> Void,
        onContinueRun: @escaping (AgentRunSummary) -> Void,
        onOpenFolder: @escaping (String) -> Void
    ) {
        self.onRefresh = onRefresh
        self.onSelectRun = onSelectRun
        self.onRetryRun = onRetryRun
        self.onContinueRun = onContinueRun
        self.onOpenFolder = onOpenFolder
    }

    func refresh() {
        errorText = ""
        onRefresh()
    }

    func select(_ run: AgentRunSummary) {
        selectedRun = run
        errorText = ""
        onSelectRun(run)
    }

    func openSelectedFolder() {
        guard let selectedRun else { return }
        onOpenFolder(selectedRun.runDir)
    }

    func retrySelectedRun() {
        guard let selectedRun, !isLoading else { return }
        onRetryRun(selectedRun)
    }

    func continueSelectedRun() {
        guard let selectedRun, !isLoading else { return }
        onContinueRun(selectedRun)
    }

    func openRootFolder() {
        guard !runsRoot.isEmpty else { return }
        onOpenFolder(runsRoot)
    }
}

private struct AgentHistoryPanelView: View {
    @ObservedObject var model: AgentHistoryModel
    @State private var selectedTab = "final"

    var body: some View {
        HSplitView {
            sidebar
                .frame(minWidth: 260, idealWidth: 320)
            detail
                .frame(minWidth: 420)
        }
        .frame(minWidth: 720, minHeight: 480)
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Agent History")
                        .font(.system(size: 15, weight: .semibold))
                    Text(model.status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if model.isLoading {
                    ProgressView()
                        .controlSize(.small)
                }
                Button {
                    model.refresh()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help("Refresh")
                .buttonStyle(.borderless)
            }
            .padding(12)

            Divider()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    if !model.errorText.isEmpty {
                        Text(model.errorText)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.bottom, 6)
                    }

                    if model.runs.isEmpty {
                        Text("No agent runs")
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, minHeight: 260)
                    } else {
                        ForEach(model.runs) { run in
                            AgentRunRow(
                                run: run,
                                selected: model.selectedRun?.runDir == run.runDir
                            )
                            .contentShape(Rectangle())
                            .onTapGesture { model.select(run) }
                        }
                    }
                }
                .padding(10)
            }

            Divider()

            HStack {
                Text(model.runsRoot)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button {
                    model.openRootFolder()
                } label: {
                    Image(systemName: "folder")
                }
                .help("Open runs folder")
                .buttonStyle(.borderless)
                .disabled(model.runsRoot.isEmpty)
            }
            .padding(10)
        }
    }

    private var detail: some View {
        VStack(spacing: 0) {
            detailHeader
            Divider()

            Picker("", selection: $selectedTab) {
                Text("Final").tag("final")
                Text("Log").tag("log")
                Text("Task").tag("task")
                Text("Diff").tag("diff")
            }
            .pickerStyle(.segmented)
            .padding(10)

            Divider()

            if let detail = model.detail {
                switch selectedTab {
                case "log":
                    TextScrollView(text: detail.runLog.isEmpty ? "(no run log)" : detail.runLog, monospace: true)
                case "task":
                    TextScrollView(text: detail.taskJSON.isEmpty ? "(missing task.json)" : detail.taskJSON, monospace: true)
                case "diff":
                    TextScrollView(text: detail.diffPatch.isEmpty ? "(no diff artifact)" : detail.diffPatch, monospace: true)
                default:
                    let final = detail.error.isEmpty ? detail.final : "Error:\n\(detail.error)\n\nFinal report:\n\(detail.final)"
                    TextScrollView(text: final.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "(no final report)" : final)
                }
            } else {
                Text("Select a run")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    private var detailHeader: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(model.detail?.summary.title ?? "No run selected")
                    .font(.system(size: 15, weight: .semibold))
                    .lineLimit(1)
                Text(model.detail?.summary.runDir ?? "")
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .textSelection(.enabled)
            }
            Spacer()
            Button {
                model.retrySelectedRun()
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .help("Retry selected run")
            .buttonStyle(.borderless)
            .disabled(model.selectedRun == nil || model.isLoading)
            Button {
                model.continueSelectedRun()
            } label: {
                Image(systemName: "play")
            }
            .help("Continue selected run")
            .buttonStyle(.borderless)
            .disabled(model.selectedRun == nil || model.isLoading)
            Button {
                model.openSelectedFolder()
            } label: {
                Image(systemName: "folder")
            }
            .help("Open selected run folder")
            .buttonStyle(.borderless)
            .disabled(model.selectedRun == nil)
        }
        .padding(12)
    }
}

private struct AgentRunRow: View {
    var run: AgentRunSummary
    var selected: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Text(run.title)
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
                Spacer()
                Text(run.status)
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(statusColor)
            }
            if !run.objective.isEmpty {
                Text(run.objective)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            HStack(spacing: 8) {
                Text(run.modifiedDisplay)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                if run.hasDiff {
                    Text("diff")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(9)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(selected ? Color.accentColor.opacity(0.16) : Color(nsColor: .controlBackgroundColor))
        )
    }

    private var statusColor: Color {
        switch run.status {
        case "failed":
            return .red
        case "cancelled":
            return .orange
        case "complete":
            return .green
        default:
            return .secondary
        }
    }
}

private struct TextScrollView: View {
    var text: String
    var monospace = false

    var body: some View {
        ScrollView {
            Text(text)
                .font(monospace ? .system(size: 12, design: .monospaced) : .system(size: 13))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
        }
    }
}
