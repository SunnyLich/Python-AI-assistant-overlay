// swift-tools-version: 5.9
//
// Wisp.app — native macOS shell (Phase-1 skeleton).
//
// NOTE: This target can only be built on macOS (it links AppKit / AVFoundation /
// ScreenCaptureKit). It does not compile on Windows; the Python brain it drives
// (../brain) is what gets verified off-Mac. Build with `swift build` for a quick
// CLI smoke test, or wrap in an Xcode app target for the bundled, signed .app.
import PackageDescription

let package = Package(
    name: "Wisp",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Wisp",
            path: "Sources/Wisp"
        ),
        .testTarget(
            name: "WispTests",
            dependencies: ["Wisp"],
            path: "Tests/WispTests"
        ),
    ]
)
