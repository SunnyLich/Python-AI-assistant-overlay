import AppKit
import SwiftUI

struct ChatTranscriptMessage: Identifiable, Equatable {
    let id: UUID
    var role: String
    var content: String
}

@MainActor
final class ChatPanel: NSPanel {

    private let model: ChatModel

    init(onSend: @escaping (String) -> Void) {
        self.model = ChatModel(onSend: onSend)
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 720, height: 540),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Chat"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 560, height: 420)
        contentView = NSHostingView(rootView: ChatPanelView(model: model))
        center()
    }

    func showChat(startNew: Bool = false) {
        if startNew {
            model.startNewConversation()
        }
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func recordExchange(user: String, assistant: String) {
        model.recordExchange(user: user, assistant: assistant)
    }

    func beginUserMessage(_ text: String) -> [[String: String]] {
        model.beginUserMessage(text)
    }

    func appendAssistantChunk(_ chunk: String) {
        model.appendAssistantChunk(chunk)
    }

    func finishAssistant(_ finalText: String? = nil) {
        model.finishAssistant(finalText)
    }

    func failAssistant(_ message: String) {
        model.failAssistant(message)
    }
}

@MainActor
private final class ChatModel: ObservableObject {
    struct Conversation: Identifiable, Equatable {
        let id: UUID
        var title: String
        var messages: [ChatTranscriptMessage]
    }

    @Published var conversations: [Conversation] = [
        Conversation(id: UUID(), title: "New chat", messages: [])
    ]
    @Published var activeID: UUID
    @Published var input = ""
    @Published var isStreaming = false
    @Published var scrollToken = 0

    private let onSend: (String) -> Void

    init(onSend: @escaping (String) -> Void) {
        self.onSend = onSend
        self.activeID = conversations[0].id
    }

    var activeConversation: Conversation {
        get {
            conversations.first(where: { $0.id == activeID }) ?? conversations[0]
        }
        set {
            guard let index = conversations.firstIndex(where: { $0.id == newValue.id }) else { return }
            conversations[index] = newValue
        }
    }

    func sendInput() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }
        input = ""
        onSend(text)
    }

    func select(_ id: UUID) {
        guard !isStreaming else { return }
        activeID = id
        scrollToken += 1
    }

    func startNewConversation() {
        let conversation = Conversation(id: UUID(), title: "New chat", messages: [])
        conversations.append(conversation)
        activeID = conversation.id
        input = ""
        isStreaming = false
        scrollToken += 1
    }

    func deleteActiveConversation() {
        guard !isStreaming else { return }
        conversations.removeAll { $0.id == activeID }
        if conversations.isEmpty {
            conversations.append(Conversation(id: UUID(), title: "New chat", messages: []))
        }
        activeID = conversations.last?.id ?? conversations[0].id
        input = ""
        scrollToken += 1
    }

    func recordExchange(user: String, assistant: String) {
        if activeConversation.messages.isEmpty == false {
            startNewConversation()
        }
        var conversation = activeConversation
        conversation.title = title(for: user)
        conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "user", content: user))
        conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "assistant", content: assistant))
        activeConversation = conversation
        scrollToken += 1
    }

    func beginUserMessage(_ text: String) -> [[String: String]] {
        var conversation = activeConversation
        if conversation.messages.isEmpty {
            conversation.title = title(for: text)
        }
        conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "user", content: text))
        activeConversation = conversation
        isStreaming = true
        scrollToken += 1
        return serializedMessages()
    }

    func appendAssistantChunk(_ chunk: String) {
        guard !chunk.isEmpty else { return }
        var conversation = activeConversation
        if let lastIndex = conversation.messages.indices.last,
           conversation.messages[lastIndex].role == "assistant" {
            conversation.messages[lastIndex].content += chunk
        } else {
            conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "assistant", content: chunk))
        }
        activeConversation = conversation
        scrollToken += 1
    }

    func finishAssistant(_ finalText: String? = nil) {
        if let finalText, !finalText.isEmpty {
            var conversation = activeConversation
            if let lastIndex = conversation.messages.indices.last,
               conversation.messages[lastIndex].role == "assistant" {
                conversation.messages[lastIndex].content = finalText
            } else {
                conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "assistant", content: finalText))
            }
            activeConversation = conversation
        }
        isStreaming = false
        scrollToken += 1
    }

    func failAssistant(_ message: String) {
        appendAssistantChunk("Error: \(message)")
        isStreaming = false
    }

    private func serializedMessages() -> [[String: String]] {
        activeConversation.messages.map { ["role": $0.role, "content": $0.content] }
    }

    private func title(for text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count > 34 else { return trimmed.isEmpty ? "New chat" : trimmed }
        let end = trimmed.index(trimmed.startIndex, offsetBy: 34)
        return String(trimmed[..<end]) + "..."
    }
}

private struct ChatPanelView: View {
    @ObservedObject var model: ChatModel
    @FocusState private var inputFocused: Bool

    var body: some View {
        HStack(spacing: 0) {
            sidebar
            Divider()
            VStack(spacing: 0) {
                header
                Divider()
                transcript
                Divider()
                composer
            }
        }
        .frame(minWidth: 560, minHeight: 420)
        .onAppear { inputFocused = true }
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Chat")
                    .font(.system(size: 15, weight: .semibold))
                Spacer()
                Button {
                    model.startNewConversation()
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("New chat")
            }
            .padding(12)

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    ForEach(model.conversations.reversed()) { conversation in
                        Button {
                            model.select(conversation.id)
                        } label: {
                            Text(conversation.title)
                                .font(.system(size: 12))
                                .lineLimit(2)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 8)
                                .background(
                                    RoundedRectangle(cornerRadius: 6)
                                        .fill(conversation.id == model.activeID ? Color.accentColor.opacity(0.18) : Color.clear)
                                )
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 8)
            }
        }
        .frame(width: 190)
        .background(Color(nsColor: NSColor(calibratedWhite: 0.075, alpha: 1.0)))
    }

    private var header: some View {
        HStack {
            Text(model.activeConversation.title)
                .font(.system(size: 14, weight: .semibold))
                .lineLimit(1)
            Spacer()
            if model.isStreaming {
                ProgressView()
                    .controlSize(.small)
            }
            Button {
                model.deleteActiveConversation()
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help("Delete selected conversation")
            .disabled(model.isStreaming)
        }
        .padding(.horizontal, 14)
        .frame(height: 42)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if model.activeConversation.messages.isEmpty {
                        Text("Ask Wisp anything.")
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, minHeight: 240)
                    } else {
                        ForEach(model.activeConversation.messages) { message in
                            ChatMessageView(message: message)
                                .id(message.id)
                        }
                    }
                }
                .padding(16)
            }
            .onChange(of: model.scrollToken) { _ in
                if let last = model.activeConversation.messages.last {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Message Wisp", text: $model.input, axis: .vertical)
                .lineLimit(1...5)
                .textFieldStyle(.roundedBorder)
                .focused($inputFocused)
                .onSubmit { model.sendInput() }
                .disabled(model.isStreaming)

            Button("Send") {
                model.sendInput()
            }
            .keyboardShortcut(.return, modifiers: [.command])
            .disabled(model.isStreaming || model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(12)
    }
}

private struct ChatMessageView: View {
    var message: ChatTranscriptMessage

    var body: some View {
        HStack {
            if message.role == "user" {
                Spacer(minLength: 54)
            }

            Text(message.content.isEmpty ? " " : message.content)
                .font(.system(size: 13))
                .textSelection(.enabled)
                .foregroundStyle(.primary)
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(background)
                )
                .frame(maxWidth: 420, alignment: message.role == "user" ? .trailing : .leading)

            if message.role != "user" {
                Spacer(minLength: 54)
            }
        }
        .frame(maxWidth: .infinity, alignment: message.role == "user" ? .trailing : .leading)
    }

    private var background: Color {
        if message.role == "user" {
            return Color(nsColor: NSColor(calibratedRed: 0.23, green: 0.23, blue: 0.36, alpha: 1.0))
        }
        return Color(nsColor: NSColor(calibratedRed: 0.15, green: 0.15, blue: 0.22, alpha: 1.0))
    }
}
