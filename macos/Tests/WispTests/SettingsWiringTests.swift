import XCTest
import Foundation

final class SettingsWiringTests: XCTestCase {

    func testSettingsPanelExposesKeysAuthAndResetSurfaces() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/SettingsUI/SettingsPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            ".tabItem { Text(\"Keys\") }",
            ".tabItem { Text(\"Auth\") }",
            "SettingsSection(\"API Keys\")",
            "SettingsSection(\"Provider Auth\")",
            "SettingsSection(\"GitHub Copilot\")",
            ".help(\"Reset all settings\")",
            "model.resetAll()",
        ] {
            XCTAssertTrue(source.contains(expected), "SettingsPanel is missing \(expected).")
        }
    }

    func testSettingsPanelModelKeepsNativeCredentialCallbacks() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/SettingsUI/SettingsPanel.swift"),
            encoding: .utf8
        )

        for expected in [
            "onRefreshSecrets",
            "onSaveSecret",
            "onClearSecret",
            "onRefreshAuth",
            "onStartChatGPTLogin",
            "onStartGitHubLogin",
            "onClearAuthProvider",
            "onSaveCopilotToken",
            "onTestCopilotToken",
            "onResetAll",
        ] {
            XCTAssertTrue(source.contains(expected), "SettingsPanel is missing callback \(expected).")
        }
    }

    func testAppDelegateWiresSettingsToBrainHandlers() throws {
        let source = try String(
            contentsOf: sourceRoot().appendingPathComponent("Sources/Wisp/App/AppDelegate.swift"),
            encoding: .utf8
        )

        for expected in [
            "brain.secrets.status",
            "brain.secrets.set",
            "brain.secrets.clear",
            "brain.auth.status",
            "brain.auth.chatgpt.browser_login",
            "brain.auth.github.device_login",
            "brain.auth.chatgpt.clear",
            "brain.auth.github.clear",
            "brain.auth.copilot.clear",
            "brain.auth.copilot.set",
            "brain.auth.copilot.test",
            "brain.settings.reset_credentials",
            "confirmResetAllSettings()",
            "FileManager.default.removeItem(at: dotEnv)",
            "reloadBrainConfig()",
        ] {
            XCTAssertTrue(source.contains(expected), "AppDelegate is missing Settings brain wiring for \(expected).")
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
