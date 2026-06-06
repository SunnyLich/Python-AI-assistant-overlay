@preconcurrency import AppKit
import SwiftUI

struct IntentSelection {
    var prompt: String
    var caller: CallerConfig
    var intent: IntentConfig?
    var isCustom: Bool
}

struct IntentRow: Identifiable, Equatable {
    var id: String { key.lowercased() }
    var key: String
    var label: String
    var subtitle: String
    var prompt: String
    var isCustom: Bool
}

@MainActor
final class IntentPanel: NSPanel {

    private let model = IntentPickerModel()
    private let onSelect: (IntentSelection) -> Void
    private var keyMonitor: Any?
    private var currentCaller = CallerConfig.empty

    init(onSelect: @escaping (IntentSelection) -> Void) {
        self.onSelect = onSelect
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 292),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )

        isFloatingPanel = true
        level = .floating
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        hidesOnDeactivate = false
        contentView = NSHostingView(rootView: IntentPickerView(model: model))

        model.onRow = { [weak self] row in self?.choose(row) }
        model.onCustomSubmit = { [weak self] text in self?.submitCustom(text) }
        model.onCancel = { [weak self] in self?.cancel() }
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    func show(caller: CallerConfig) {
        currentCaller = caller
        let rows = caller.intentRows()
        model.configure(title: caller.label.isEmpty ? "Wisp" : caller.label, rows: rows)

        let height = CGFloat(22 + rows.count * 64 + (model.isCustomMode ? 54 : 0) + 34)
        setContentSize(NSSize(width: 320, height: height))
        centerOnActiveScreen()
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        installKeyMonitor()
    }

    override func orderOut(_ sender: Any?) {
        removeKeyMonitor()
        super.orderOut(sender)
    }

    private func choose(_ row: IntentRow) {
        if row.isCustom {
            model.enterCustomMode()
            setContentSize(NSSize(width: 320, height: CGFloat(22 + model.rows.count * 64 + 54 + 34)))
            centerOnActiveScreen()
            return
        }
        finish(prompt: row.prompt, intent: intent(for: row), isCustom: false)
    }

    private func submitCustom(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        finish(prompt: trimmed, intent: nil, isCustom: true)
    }

    private func finish(prompt: String, intent: IntentConfig?, isCustom: Bool) {
        orderOut(nil)
        onSelect(IntentSelection(prompt: prompt, caller: currentCaller, intent: intent, isCustom: isCustom))
    }

    private func cancel() {
        orderOut(nil)
    }

    private func intent(for row: IntentRow) -> IntentConfig? {
        currentCaller.intents.first { $0.key.caseInsensitiveCompare(row.key) == .orderedSame }
    }

    private func installKeyMonitor() {
        removeKeyMonitor()
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            MainActor.assumeIsolated {
                guard let self, self.isVisible else { return event }
                return self.handleKeyDown(event)
            }
        }
    }

    private func removeKeyMonitor() {
        if let keyMonitor {
            NSEvent.removeMonitor(keyMonitor)
            self.keyMonitor = nil
        }
    }

    private func handleKeyDown(_ event: NSEvent) -> NSEvent? {
        if event.keyCode == 53 {
            cancel()
            return nil
        }
        if model.isCustomMode {
            return event
        }
        let key = event.charactersIgnoringModifiers?.lowercased() ?? ""
        guard !key.isEmpty else { return event }
        if let row = model.rows.first(where: { $0.key.lowercased() == key }) {
            choose(row)
            return nil
        }
        return event
    }

    private func centerOnActiveScreen() {
        let screen = NSScreen.main ?? NSScreen.screens.first
        guard let frame = screen?.visibleFrame else { return }
        setFrameOrigin(NSPoint(
            x: frame.midX - self.frame.width / 2,
            y: frame.midY - self.frame.height / 2
        ))
    }
}

@MainActor
private final class IntentPickerModel: ObservableObject {
    @Published var title = "Wisp"
    @Published var rows: [IntentRow] = []
    @Published var isCustomMode = false
    @Published var customPrompt = ""
    @Published var focusToken = 0

    var onRow: ((IntentRow) -> Void)?
    var onCustomSubmit: ((String) -> Void)?
    var onCancel: (() -> Void)?

    func configure(title: String, rows: [IntentRow]) {
        self.title = title
        self.rows = rows
        self.isCustomMode = false
        self.customPrompt = ""
        self.focusToken += 1
    }

    func enterCustomMode() {
        isCustomMode = true
        customPrompt = ""
        focusToken += 1
    }

    func submitCustom() {
        onCustomSubmit?(customPrompt)
    }
}

private struct IntentPickerView: View {
    @ObservedObject var model: IntentPickerModel
    @FocusState private var customFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            Text(model.title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.white.opacity(0.72))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 14)
                .padding(.top, 10)
                .padding(.bottom, 2)

            ForEach(model.rows) { row in
                Button {
                    model.onRow?(row)
                } label: {
                    IntentRowView(row: row)
                }
                .buttonStyle(.plain)
            }

            if model.isCustomMode {
                HStack(spacing: 8) {
                    TextField("Type your prompt", text: $model.customPrompt)
                        .textFieldStyle(.roundedBorder)
                        .focused($customFocused)
                        .onSubmit { model.submitCustom() }

                    Button("Send") {
                        model.submitCustom()
                    }
                    .disabled(model.customPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .padding(.horizontal, 12)
                .padding(.top, 8)
            }

            Text("ESC to cancel")
                .font(.caption2)
                .foregroundStyle(Color.white.opacity(0.48))
                .frame(maxWidth: .infinity)
                .padding(.top, 8)
                .padding(.bottom, 10)
        }
        .background(
            RoundedRectangle(cornerRadius: 14)
                .fill(Color(nsColor: NSColor(calibratedWhite: 0.08, alpha: 0.97)))
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .stroke(Color.white.opacity(0.08), lineWidth: 1)
                )
        )
        .frame(width: 320)
        .onChange(of: model.focusToken) { _ in
            customFocused = model.isCustomMode
        }
    }
}

private struct IntentRowView: View {
    var row: IntentRow

    var body: some View {
        HStack(spacing: 12) {
            Text(row.key.uppercased())
                .font(.system(size: 16, weight: .bold, design: .rounded))
                .foregroundStyle(Color(red: 0.62, green: 0.55, blue: 1.0))
                .frame(width: 38, height: 38)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.white.opacity(0.08))
                )

            VStack(alignment: .leading, spacing: 4) {
                Text(row.label)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color.white.opacity(0.94))
                    .lineLimit(1)
                Text(row.subtitle)
                    .font(.caption)
                    .foregroundStyle(Color.white.opacity(0.62))
                    .lineLimit(1)
            }
            Spacer()
        }
        .padding(.horizontal, 12)
        .frame(height: 64)
        .contentShape(Rectangle())
        .background(Color.white.opacity(0.001))
    }
}

private extension CallerConfig {
    func intentRows() -> [IntentRow] {
        var rows = intents.map { intent in
            IntentRow(
                key: intent.key.isEmpty ? "?" : intent.key,
                label: intent.label,
                subtitle: intent.prompt.isEmpty ? intent.hint : intent.prompt,
                prompt: intent.prompt,
                isCustom: false
            )
        }
        rows.append(
            IntentRow(
                key: customKey.isEmpty ? "s" : customKey,
                label: "Custom prompt",
                subtitle: "Ask anything",
                prompt: "",
                isCustom: true
            )
        )
        return rows
    }
}
