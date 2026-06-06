import AppKit

struct SnipSelection {
    var captureRect: CGRect
}

@MainActor
final class SnipOverlayPanel: NSPanel {

    private let snipView: SnipOverlayView

    init(onSelection: @escaping (SnipSelection) -> Void, onCancel: @escaping () -> Void) {
        self.snipView = SnipOverlayView(onSelection: onSelection, onCancel: onCancel)
        let screenFrame = Self.virtualScreenFrame()

        super.init(
            contentRect: screenFrame,
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )

        level = .screenSaver
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        contentView = snipView
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    func showSnip() {
        let screenFrame = Self.virtualScreenFrame()
        setFrame(screenFrame, display: true)
        snipView.screenFrame = screenFrame
        snipView.frame = NSRect(origin: .zero, size: screenFrame.size)
        snipView.resetSelection()
        makeKeyAndOrderFront(nil)
        makeFirstResponder(snipView)
        NSApp.activate(ignoringOtherApps: true)
    }

    private static func virtualScreenFrame() -> NSRect {
        let screens = NSScreen.screens
        guard var frame = screens.first?.frame else {
            return NSRect(x: 0, y: 0, width: 800, height: 600)
        }
        for screen in screens.dropFirst() {
            frame = frame.union(screen.frame)
        }
        return frame
    }
}

private final class SnipOverlayView: NSView {
    var screenFrame: NSRect = .zero

    private let onSelection: (SnipSelection) -> Void
    private let onCancel: () -> Void
    private var origin: NSPoint?
    private var current: NSPoint?

    init(onSelection: @escaping (SnipSelection) -> Void, onCancel: @escaping () -> Void) {
        self.onSelection = onSelection
        self.onCancel = onCancel
        super.init(frame: .zero)
        wantsLayer = true
    }

    required init?(coder: NSCoder) {
        return nil
    }

    override var acceptsFirstResponder: Bool { true }

    func resetSelection() {
        origin = nil
        current = nil
        needsDisplay = true
    }

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .crosshair)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.black.withAlphaComponent(0.45).setFill()
        bounds.fill()

        if let selectionRect {
            NSColor(calibratedRed: 0.55, green: 0.72, blue: 1.0, alpha: 0.95).setStroke()
            let path = NSBezierPath(rect: selectionRect)
            path.lineWidth = 2
            path.stroke()

            NSColor(calibratedRed: 0.55, green: 0.72, blue: 1.0, alpha: 0.16).setFill()
            selectionRect.fill()
        }

        drawInstructions()
    }

    override func mouseDown(with event: NSEvent) {
        origin = convert(event.locationInWindow, from: nil)
        current = origin
        needsDisplay = true
    }

    override func mouseDragged(with event: NSEvent) {
        current = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        current = convert(event.locationInWindow, from: nil)
        guard let selectionRect, selectionRect.width > 4, selectionRect.height > 4 else {
            finishCancelled()
            return
        }

        let captureRect = CGRect(
            x: screenFrame.minX + selectionRect.minX,
            y: screenFrame.maxY - selectionRect.maxY,
            width: selectionRect.width,
            height: selectionRect.height
        )
        finish()
        onSelection(SnipSelection(captureRect: captureRect))
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 {
            finishCancelled()
        } else {
            super.keyDown(with: event)
        }
    }

    private var selectionRect: NSRect? {
        guard let origin, let current else { return nil }
        return NSRect(
            x: min(origin.x, current.x),
            y: min(origin.y, current.y),
            width: abs(current.x - origin.x),
            height: abs(current.y - origin.y)
        )
    }

    private func drawInstructions() {
        let text = "Click and drag to select a region  |  ESC to cancel" as NSString
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 14, weight: .medium),
            .foregroundColor: NSColor.white.withAlphaComponent(0.82),
        ]
        let size = text.size(withAttributes: attributes)
        let rect = NSRect(
            x: bounds.midX - size.width / 2,
            y: bounds.minY + 30,
            width: size.width,
            height: size.height
        )
        text.draw(in: rect, withAttributes: attributes)
    }

    private func finishCancelled() {
        finish()
        onCancel()
    }

    private func finish() {
        origin = nil
        current = nil
        window?.orderOut(nil)
    }
}
