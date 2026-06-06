import XCTest
import Foundation

final class PermissionsAndLoginSourceTests: XCTestCase {

    func testPermissionsPanelAndNativePermissionRequestsStayWired() throws {
        let sourceRoot = sourceRoot().appendingPathComponent("Sources/Wisp")
        let permissionsPanel = try String(
            contentsOf: sourceRoot.appendingPathComponent("App/PermissionsPanel.swift"),
            encoding: .utf8
        )
        let nativeContext = try String(
            contentsOf: sourceRoot.appendingPathComponent("Context/NativeContextController.swift"),
            encoding: .utf8
        )
        let appDelegate = try String(
            contentsOf: sourceRoot.appendingPathComponent("App/AppDelegate.swift"),
            encoding: .utf8
        )
        let statusItem = try String(
            contentsOf: sourceRoot.appendingPathComponent("Tray/StatusItem.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(permissionsPanel.contains("PermissionsPanelView"))
        XCTAssertTrue(permissionsPanel.contains("ForEach(NativePermissionKind.allCases)"))
        XCTAssertTrue(permissionsPanel.contains("model.request(kind)"))
        XCTAssertTrue(permissionsPanel.contains("arrow.up.forward.app"))

        for expected in [
            "case accessibility",
            "case screenRecording",
            "case microphone",
            "Privacy_Accessibility",
            "Privacy_ScreenCapture",
            "Privacy_Microphone",
            "AXIsProcessTrustedWithOptions",
            "CGPreflightScreenCaptureAccess",
            "CGRequestScreenCaptureAccess",
            "AVCaptureDevice.requestAccess",
        ] {
            XCTAssertTrue(nativeContext.contains(expected), "Missing permission source marker: \(expected)")
        }

        XCTAssertTrue(appDelegate.contains("permissionsPanel = PermissionsPanel"))
        XCTAssertTrue(appDelegate.contains("showPermissionSnapshot()"))
        XCTAssertTrue(appDelegate.contains("nativeContext.requestPermission(kind)"))
        XCTAssertTrue(statusItem.contains("NSMenuItem(title: \"Permissions\""))
        XCTAssertTrue(statusItem.contains("onShowPermissions()"))
    }

    func testLaunchAtLoginMenuUsesSMAppServiceController() throws {
        let sourceRoot = sourceRoot().appendingPathComponent("Sources/Wisp")
        let loginController = try String(
            contentsOf: sourceRoot.appendingPathComponent("App/LoginItemController.swift"),
            encoding: .utf8
        )
        let appDelegate = try String(
            contentsOf: sourceRoot.appendingPathComponent("App/AppDelegate.swift"),
            encoding: .utf8
        )
        let statusItem = try String(
            contentsOf: sourceRoot.appendingPathComponent("Tray/StatusItem.swift"),
            encoding: .utf8
        )

        for expected in [
            "SMAppService.mainApp.status",
            "SMAppService.mainApp.register()",
            "SMAppService.mainApp.unregister()",
            "Launch at Login:",
            "requiresApproval",
            "notRegistered",
            "notFound",
        ] {
            XCTAssertTrue(loginController.contains(expected), "Missing login source marker: \(expected)")
        }

        XCTAssertTrue(appDelegate.contains("status.setLoginItemStatus(LoginItemController.status)"))
        XCTAssertTrue(appDelegate.contains("toggleLaunchAtLogin()"))
        XCTAssertTrue(appDelegate.contains("LoginItemController.toggle()"))
        XCTAssertTrue(statusItem.contains("loginItemMenuItem"))
        XCTAssertTrue(statusItem.contains("setLoginItemStatus(_ status: LoginItemStatus)"))
        XCTAssertTrue(statusItem.contains("onToggleLoginItem()"))
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
