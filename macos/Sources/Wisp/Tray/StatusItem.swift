import AppKit

/// The menubar presence (`NSStatusItem`) — replaces the Qt system tray. Owns a
/// small menu showing brain-sidecar status and a Quit item. Kept deliberately
/// thin for Phase 1/2; intent actions get wired in later phases.
final class StatusItemController {

    private let statusItem: NSStatusItem
    private let statusMenuItem: NSMenuItem

    init() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "✦"
        statusItem.button?.toolTip = "Wisp"

        let menu = NSMenu()
        statusMenuItem = NSMenuItem(title: "Brain: starting…", action: nil, keyEquivalent: "")
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Wisp", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    /// Reflect the result of the brain handshake in the menu.
    func setBrainStatus(_ text: String) {
        statusMenuItem.title = "Brain: \(text)"
    }
}
