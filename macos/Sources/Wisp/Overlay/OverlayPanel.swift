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
final class OverlayPanel: NSPanel {

    enum DollState { case idle, listening, thinking, speaking }

    private let model = OverlayModel()

    init() {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 96, height: 96),
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

        contentView = NSHostingView(rootView: OverlayView(model: model))
        positionBottomTrailing()
    }

    func setState(_ state: DollState) { model.state = state }

    private func positionBottomTrailing() {
        guard let screen = NSScreen.main else { return }
        let v = screen.visibleFrame
        let margin: CGFloat = 24
        setFrameOrigin(NSPoint(x: v.maxX - frame.width - margin,
                               y: v.minY + margin))
    }
}

/// Observable backing the SwiftUI overlay view.
final class OverlayModel: ObservableObject {
    @Published var state: OverlayPanel.DollState = .idle
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

    var body: some View {
        Circle()
            .fill(color.opacity(0.9))
            .overlay(Circle().stroke(.white.opacity(0.6), lineWidth: 2))
            .frame(width: 64, height: 64)
            .shadow(radius: 6)
            .padding(16)
            .animation(.easeInOut(duration: 0.25), value: model.state)
    }
}
