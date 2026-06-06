import AppKit
import SwiftUI

/// The floating icon overlay — the macOS replacement for the Qt `Qt.Tool` window
/// that segfaulted under PyObjC (see MACOS_NATIVE_PLAN.md §0 and the
/// no-child-windows memory). Here it is a first-class `NSPanel`:
///
///   - `.nonactivatingPanel` so clicking it doesn't steal key focus from the
///     user's frontmost app (the whole point of an ambient assistant overlay).
///   - `.borderless`, `level = .floating`, joins all Spaces + fullscreen aux.
///   - SwiftUI content hosted via `NSHostingView` (AppKit owns the fragile window
///     graph; SwiftUI just draws — the architecture chosen in the plan).
///
/// Crucially, normal top-level windows (settings, chat) must be opened with
/// `parent: nil`, NOT as children of this panel — parenting a regular NSWindow to
/// an NSPanel is the documented Cocoa crash this whole rewrite avoids.
@MainActor
final class OverlayPanel: NSPanel {

    enum DollState: Hashable { case idle, listening, thinking, speaking }

    private let model: OverlayModel
    private let onRightClick: (NSEvent) -> Void
    private let autoHide: Bool
    private let autoHideDelay: TimeInterval
    private var hideTimer: Timer?

    init(onTap: @escaping () -> Void = {}, onRightClick: @escaping (NSEvent) -> Void = { _ in }) {
        let model = OverlayModel(onTap: onTap)
        self.model = model
        self.onRightClick = onRightClick
        self.autoHide = Self.loadAutoHide()
        self.autoHideDelay = Self.loadAutoHideDelay()
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: model.panelSize, height: model.panelSize),
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = .floating
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        isMovableByWindowBackground = true

        contentView = OverlayHostingView(rootView: OverlayView(model: model), onRightClick: onRightClick)
        positionBottomTrailing()
    }

    override func rightMouseDown(with event: NSEvent) {
        onRightClick(event)
    }

    func showAtLaunch() {
        if autoHide {
            orderOut(nil)
        } else {
            positionBottomTrailing()
            orderFrontRegardless()
        }
    }

    func setState(_ state: DollState) {
        model.state = state
        if state != .speaking {
            model.amplitude = 0
        }
        guard autoHide else {
            if !isVisible {
                positionBottomTrailing()
                orderFrontRegardless()
            }
            return
        }

        switch state {
        case .idle:
            scheduleAutoHide()
        case .listening, .thinking, .speaking:
            hideTimer?.invalidate()
            positionBottomTrailing()
            orderFrontRegardless()
        }
    }

    func setSpeechAmplitude(_ amplitude: Double) {
        model.amplitude = max(0, min(1, amplitude))
    }

    func toggleVisibility() {
        hideTimer?.invalidate()
        if isVisible {
            orderOut(nil)
        } else {
            positionBottomTrailing()
            orderFrontRegardless()
        }
    }

    private func positionBottomTrailing() {
        guard let screen = NSScreen.main else { return }
        let v = screen.visibleFrame
        let margin: CGFloat = 24
        setFrameOrigin(NSPoint(x: v.maxX - frame.width - margin,
                               y: v.minY + margin))
    }

    private func scheduleAutoHide() {
        hideTimer?.invalidate()
        hideTimer = Timer.scheduledTimer(withTimeInterval: autoHideDelay, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.orderOut(nil)
            }
        }
    }

    private static func loadAutoHide() -> Bool {
        let values = WispConfig.loadValues()
        let raw = values["ICON_AUTO_HIDE"] ?? values["DOLL_AUTO_HIDE"] ?? "false"
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "1", "true", "yes", "on":
            return true
        default:
            return false
        }
    }

    private static func loadAutoHideDelay() -> TimeInterval {
        let values = WispConfig.loadValues()
        let raw = values["ICON_BACKSTOP_MS"] ?? values["DOLL_ICON_BACKSTOP_MS"] ?? "5000"
        let milliseconds = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 5000
        return Double(max(500, milliseconds)) / 1000.0
    }
}

/// Observable backing the SwiftUI overlay view.
@MainActor
final class OverlayModel: ObservableObject {
    @Published var state: OverlayPanel.DollState = .idle
    @Published var amplitude: Double = 0

    let onTap: () -> Void
    let iconSize: CGFloat
    private let images: [OverlayPanel.DollState: NSImage]

    init(onTap: @escaping () -> Void) {
        self.onTap = onTap
        self.iconSize = Self.loadIconSize()
        self.images = DollAssetLocator.loadImages()
    }

    func image(for state: OverlayPanel.DollState) -> NSImage? {
        images[state]
    }

    var panelSize: CGFloat {
        iconSize + 18
    }

    private static func loadIconSize() -> CGFloat {
        let values = WispConfig.loadValues()
        let raw = values["ICON_SIZE"] ?? values["DOLL_SIZE"] ?? "80"
        let parsed = Int(raw.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 80
        return CGFloat(max(32, min(160, parsed)))
    }
}

private struct OverlayView: View {
    @ObservedObject var model: OverlayModel

    private var color: Color {
        switch model.state {
        case .idle:      return .gray
        case .listening: return .blue
        case .thinking:  return .yellow
        case .speaking:  return .green
        }
    }

    private var speechScale: CGFloat {
        guard model.state == .speaking else { return 1.0 }
        return 1.0 + CGFloat(model.amplitude) * 0.10
    }

    private var speechShadow: CGFloat {
        guard model.state == .speaking else { return 6 }
        return 6 + CGFloat(model.amplitude) * 8
    }

    var body: some View {
        Group {
            if let image = model.image(for: model.state) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(width: model.iconSize, height: model.iconSize)
                    .scaleEffect(speechScale)
                    .shadow(radius: speechShadow)
            } else {
                Circle()
                    .fill(color.opacity(0.9))
                    .overlay(Circle().stroke(.white.opacity(0.6), lineWidth: 2))
                    .frame(width: model.iconSize, height: model.iconSize)
                    .scaleEffect(speechScale)
                    .shadow(radius: speechShadow)
            }
        }
        .padding(9)
        .contentShape(Rectangle())
        .onTapGesture {
            model.onTap()
        }
        .animation(.easeInOut(duration: 0.25), value: model.state)
        .animation(.easeOut(duration: 0.08), value: model.amplitude)
    }
}

@MainActor
private final class OverlayHostingView: NSHostingView<OverlayView> {
    private let onRightClick: (NSEvent) -> Void

    init(rootView: OverlayView, onRightClick: @escaping (NSEvent) -> Void) {
        self.onRightClick = onRightClick
        super.init(rootView: rootView)
    }

    required init(rootView: OverlayView) {
        self.onRightClick = { _ in }
        super.init(rootView: rootView)
    }

    @MainActor
    required dynamic init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func rightMouseDown(with event: NSEvent) {
        onRightClick(event)
    }
}

private enum DollAssetLocator {

    static func loadImages() -> [OverlayPanel.DollState: NSImage] {
        let names: [OverlayPanel.DollState: String] = [
            .idle: "idle.png",
            .listening: "listening.png",
            .thinking: "thinking.png",
            .speaking: "speaking.png",
        ]

        for directory in candidateDirectories() {
            var images: [OverlayPanel.DollState: NSImage] = [:]
            for (state, name) in names {
                let url = directory.appendingPathComponent(name)
                if let image = NSImage(contentsOf: url) {
                    images[state] = image
                }
            }
            if images[.idle] != nil {
                return images
            }
        }
        return [:]
    }

    private static func candidateDirectories() -> [URL] {
        var directories: [URL] = []
        if let resourceURL = Bundle.main.resourceURL {
            directories.append(resourceURL.appendingPathComponent("assets/doll"))
            directories.append(resourceURL.appendingPathComponent("doll"))
        }
        if let repoRoot = ProcessInfo.processInfo.environment["WISP_REPO_ROOT"] {
            directories.append(URL(fileURLWithPath: repoRoot).appendingPathComponent("assets/doll"))
        }
        directories.append(
            URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("../assets/doll")
                .standardizedFileURL
        )
        return directories
    }
}
