import AppKit

// Executable entry point. `.accessory` keeps Wisp out of the Dock / app switcher
// (a menubar + floating-overlay assistant, not a windowed app). When this graduates
// to a bundled Xcode target, the same delegate is reused behind an `@main` app.
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
