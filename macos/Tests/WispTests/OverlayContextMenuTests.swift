import XCTest
import Foundation

final class OverlayContextMenuTests: XCTestCase {

    func testOverlayForwardsRightClickFromPanelAndHostedView() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/OverlayPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("onRightClick: @escaping (NSEvent) -> Void"))
        XCTAssertTrue(source.contains("contentView = OverlayHostingView(rootView: OverlayView(model: model), onRightClick: onRightClick)"))
        XCTAssertTrue(source.contains("override func rightMouseDown(with event: NSEvent)"))
        XCTAssertTrue(source.contains("onRightClick(event)"))
    }

    func testOverlayHostingViewKeepsRequiredRootViewInitializer() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/OverlayPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private final class OverlayHostingView: NSHostingView<OverlayView>"))
        XCTAssertTrue(source.contains("required init(rootView: OverlayView)"))
        XCTAssertTrue(source.contains("self.onRightClick = { _ in }"))
        XCTAssertTrue(source.contains("required dynamic init?(coder: NSCoder)"))
    }

    func testAppDelegateOverlayMenuContainsCoreNativeActions() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private func showOverlayMenu(_ event: NSEvent)"))
        XCTAssertTrue(source.contains("menu.popUp(positioning: nil, at: point, in: view)"))

        for title in [
            "Ask Wisp",
            "New Chat",
            "Snip Screen Region",
            "Settings",
            "Open Run Logs",
            "Open Config Folder",
            "Hide Overlay",
            "Quit Wisp",
        ] {
            XCTAssertTrue(source.contains(title), "Overlay menu is missing \(title).")
        }
    }

    private func sourceRoot() -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }
}
