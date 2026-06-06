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

    func showChat(startNew: Bool = false, autoMessage: String? = nil) {
        if startNew {
            model.startNewConversation()
        }
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let autoMessage {
            model.sendAutoMessage(autoMessage)
        }
    }

    func hasConversationHistory() -> Bool {
        model.hasConversationHistory
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

    @Published var conversations: [Conversation]
    @Published var activeID: UUID
    @Published var input = ""
    @Published var isStreaming = false
    @Published var scrollToken = 0

    private let onSend: (String) -> Void

    init(onSend: @escaping (String) -> Void) {
        let initialConversation = Conversation(id: UUID(), title: "New chat", messages: [])
        self.onSend = onSend
        self.conversations = [initialConversation]
        self.activeID = initialConversation.id
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

    var hasConversationHistory: Bool {
        conversations.contains { $0.messages.isEmpty == false }
    }

    func sendInput() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }
        input = ""
        onSend(text)
    }

    func sendAutoMessage(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isStreaming, hasConversationHistory else { return }
        input = ""
        onSend(trimmed)
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
            .background(ChatPanelPalette.contentBackground)
        }
        .frame(minWidth: 560, minHeight: 420)
        .background(ChatPanelPalette.contentBackground)
        .onAppear { inputFocused = true }
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Chat")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(ChatPanelPalette.primaryText)
                Spacer()
                Button {
                    model.startNewConversation()
                } label: {
                    Image(systemName: "plus")
                        .foregroundStyle(ChatPanelPalette.secondaryText)
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
                                .foregroundStyle(conversation.id == model.activeID ? ChatPanelPalette.primaryText : ChatPanelPalette.secondaryText)
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
        .background(ChatPanelPalette.sidebarBackground)
    }

    private var header: some View {
        HStack {
            Text(model.activeConversation.title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(ChatPanelPalette.primaryText)
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
                    .foregroundStyle(ChatPanelPalette.secondaryText)
            }
            .buttonStyle(.borderless)
            .help("Delete selected conversation")
            .disabled(model.isStreaming)
        }
        .padding(.horizontal, 14)
        .frame(height: 42)
        .background(ChatPanelPalette.headerBackground)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if model.activeConversation.messages.isEmpty {
                        Text("Ask Wisp anything.")
                            .foregroundStyle(ChatPanelPalette.secondaryText)
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
            .background(ChatPanelPalette.contentBackground)
            .onChange(of: model.scrollToken) { _ in
                if let last = model.activeConversation.messages.last {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            ZStack(alignment: .topLeading) {
                if model.input.isEmpty {
                    Text("Message Wisp")
                        .font(.system(size: 13))
                        .foregroundStyle(ChatPanelPalette.placeholderText)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                        .allowsHitTesting(false)
                }

                TextField("", text: $model.input, axis: .vertical)
                    .lineLimit(1...5)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .foregroundStyle(ChatPanelPalette.primaryText)
                    .tint(ChatPanelPalette.primaryText)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .focused($inputFocused)
                    .onSubmit { model.sendInput() }
                    .disabled(model.isStreaming)
            }
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .fill(ChatPanelPalette.inputBackground)
                    .overlay(
                        RoundedRectangle(cornerRadius: 7)
                            .stroke(ChatPanelPalette.inputBorder, lineWidth: 1)
                    )
            )

            Button("Send") {
                model.sendInput()
            }
            .keyboardShortcut(.return, modifiers: [.command])
            .buttonStyle(ChatSendButtonStyle())
            .disabled(model.isStreaming || model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(12)
        .background(ChatPanelPalette.composerBackground)
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
                .foregroundStyle(ChatPanelPalette.primaryText)
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
            return ChatPanelPalette.userBubbleBackground
        }
        return ChatPanelPalette.assistantBubbleBackground
    }
}

private struct ChatSendButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(isEnabled ? ChatPanelPalette.buttonText : ChatPanelPalette.disabledButtonText)
            .padding(.horizontal, 14)
            .frame(height: 32)
            .background(
                RoundedRectangle(cornerRadius: 7)
                    .fill(buttonBackground(isPressed: configuration.isPressed))
            )
            .opacity(configuration.isPressed ? 0.86 : 1.0)
    }

    private func buttonBackground(isPressed: Bool) -> Color {
        if !isEnabled {
            return ChatPanelPalette.disabledButtonBackground
        }
        return isPressed ? ChatPanelPalette.buttonPressedBackground : ChatPanelPalette.buttonBackground
    }
}

private enum ChatPanelPalette {
    static let sidebarBackground = Color(nsColor: NSColor(calibratedWhite: 0.10, alpha: 1.0))
    static let headerBackground = Color(nsColor: NSColor(calibratedWhite: 0.14, alpha: 1.0))
    static let contentBackground = Color(nsColor: NSColor(calibratedWhite: 0.16, alpha: 1.0))
    static let composerBackground = Color(nsColor: NSColor(calibratedWhite: 0.13, alpha: 1.0))
    static let inputBackground = Color(nsColor: NSColor(calibratedWhite: 0.08, alpha: 1.0))
    static let inputBorder = Color(nsColor: NSColor(calibratedWhite: 0.32, alpha: 1.0))
    static let userBubbleBackground = Color(nsColor: NSColor(calibratedRed: 0.28, green: 0.30, blue: 0.42, alpha: 1.0))
    static let assistantBubbleBackground = Color(nsColor: NSColor(calibratedRed: 0.22, green: 0.23, blue: 0.30, alpha: 1.0))
    static let buttonBackground = Color(nsColor: NSColor(calibratedRed: 0.18, green: 0.36, blue: 0.86, alpha: 1.0))
    static let buttonPressedBackground = Color(nsColor: NSColor(calibratedRed: 0.14, green: 0.28, blue: 0.68, alpha: 1.0))
    static let disabledButtonBackground = Color(nsColor: NSColor(calibratedWhite: 0.28, alpha: 1.0))
    static let buttonText = Color(nsColor: NSColor(calibratedWhite: 1.0, alpha: 1.0))
    static let disabledButtonText = Color(nsColor: NSColor(calibratedWhite: 0.78, alpha: 1.0))
    static let primaryText = Color(nsColor: NSColor(calibratedWhite: 0.96, alpha: 1.0))
    static let secondaryText = Color(nsColor: NSColor(calibratedWhite: 0.76, alpha: 1.0))
    static let placeholderText = Color(nsColor: NSColor(calibratedWhite: 0.62, alpha: 1.0))
}
