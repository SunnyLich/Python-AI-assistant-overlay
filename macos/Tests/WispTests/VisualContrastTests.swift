import XCTest
import Foundation

final class VisualContrastTests: XCTestCase {

    func testCustomDarkSwiftUISurfacesDoNotUseAdaptivePrimaryOrSecondaryText() throws {
        for url in try swiftSourcesWithCustomDarkSurfaces() {
            let source = try String(contentsOf: url, encoding: .utf8)
            let path = sourceRoot().relativePath(to: url)
            for forbiddenTextStyle in forbiddenAdaptiveTextStyles {
                XCTAssertFalse(
                    source.contains(forbiddenTextStyle),
                    "\(path) has a custom dark surface; use explicit readable colors instead of \(forbiddenTextStyle)."
                )
            }
        }
    }

    func testChatPanelUsesExplicitReadablePalette() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Chat/ChatPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private enum ChatPanelPalette"))
        XCTAssertTrue(source.contains("primaryText"))
        XCTAssertTrue(source.contains("secondaryText"))
        XCTAssertTrue(source.contains("inputBackground"))
        XCTAssertTrue(source.contains("placeholderText"))
        XCTAssertTrue(source.contains("ChatSendButtonStyle"))
        XCTAssertFalse(source.contains(".textFieldStyle(.roundedBorder)"))

        for forbiddenTextStyle in forbiddenAdaptiveTextStyles {
            XCTAssertFalse(
                source.contains(forbiddenTextStyle),
                "ChatPanel has a fixed dark surface; use ChatPanelPalette text colors instead of \(forbiddenTextStyle)."
            )
        }
    }

    func testIntentPanelUsesExplicitReadablePalette() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Input/IntentPanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("private enum IntentPanelPalette"))
        XCTAssertTrue(source.contains("primaryText"))
        XCTAssertTrue(source.contains("placeholderText"))
        XCTAssertTrue(source.contains("inputBackground"))
        XCTAssertTrue(source.contains("IntentSendButtonStyle"))
        XCTAssertFalse(source.contains(".textFieldStyle(.roundedBorder)"))

        for forbiddenTextStyle in forbiddenAdaptiveTextStyles {
            XCTAssertFalse(
                source.contains(forbiddenTextStyle),
                "IntentPanel has a fixed dark surface; use IntentPanelPalette text colors instead of \(forbiddenTextStyle)."
            )
        }
    }

    func testResponseBubbleUsesExplicitReadableConfiguredColors() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/Overlay/ResponseBubblePanel.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("BUBBLE_COLOR"))
        XCTAssertTrue(source.contains("BUBBLE_TEXT_COLOR"))
        XCTAssertTrue(source.contains("BUBBLE_READ_WORD_COLOR"))
        XCTAssertTrue(source.contains("textColor"))
        XCTAssertTrue(source.contains("readWordColor"))
        XCTAssertTrue(source.contains("foregroundStyle(textColor)"))
        XCTAssertTrue(source.contains("model.config.textColor"))

        for forbiddenTextStyle in forbiddenAdaptiveTextStyles {
            XCTAssertFalse(
                source.contains(forbiddenTextStyle),
                "ResponseBubblePanel has a fixed dark surface; use configured explicit colors instead of \(forbiddenTextStyle)."
            )
        }
    }

    private let forbiddenAdaptiveTextStyles = [
        ".foregroundStyle(.primary)",
        ".foregroundStyle(.secondary)",
        ".foregroundColor(.primary)",
        ".foregroundColor(.secondary)",
        "Color.primary",
        "Color.secondary",
        ".labelColor",
        ".secondaryLabelColor",
        ".tertiaryLabelColor",
        ".quaternaryLabelColor"
    ]

    private func swiftSourcesWithCustomDarkSurfaces() throws -> [URL] {
        let root = sourceRoot().appendingPathComponent("Sources/Wisp")
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: nil
        ) else {
            return []
        }

        return try enumerator.compactMap { item in
            guard let url = item as? URL, url.pathExtension == "swift" else { return nil }
            let source = try String(contentsOf: url, encoding: .utf8)
            return hasCustomDarkSurface(source) ? url : nil
        }
    }

    private func hasCustomDarkSurface(_ source: String) -> Bool {
        source.contains("calibratedWhite: 0.")
            || source.contains("calibratedRed: 0.")
            || source.contains("NSColor.black")
            || source.contains("Color.black")
    }

    private func sourceRoot() -> URL {
        let currentDirectory = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        return sourceRoot(from: currentDirectory)
    }

    private func sourceRoot(from currentDirectory: URL) -> URL {
        let direct = currentDirectory.appendingPathComponent("Sources/Wisp")
        if FileManager.default.fileExists(atPath: direct.path) {
            return currentDirectory
        }
        return currentDirectory.appendingPathComponent("macos")
    }
}

private extension URL {
    func relativePath(to child: URL) -> String {
        let base = standardizedFileURL.path
        let target = child.standardizedFileURL.path
        guard target.hasPrefix(base) else { return target }
        return String(target.dropFirst(base.count + 1))
    }
}
