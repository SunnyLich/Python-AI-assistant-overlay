import AppKit

// SwiftPM treats a file named `main.swift` as the executable's top-level entry
// point. Keep this as top-level code and explicitly enter MainActor before
// touching AppKit or constructing the main-actor app delegate.
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
