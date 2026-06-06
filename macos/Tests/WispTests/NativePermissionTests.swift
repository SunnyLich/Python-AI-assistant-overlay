import XCTest
@testable import Wisp

final class NativePermissionTests: XCTestCase {

    func testPermissionKindsMapToPrivacySettingsPanes() {
        XCTAssertEqual(
            NativePermissionKind.accessibility.settingsURL.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        )
        XCTAssertEqual(
            NativePermissionKind.screenRecording.settingsURL.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        )
        XCTAssertEqual(
            NativePermissionKind.microphone.settingsURL.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        )
    }

    func testPermissionSnapshotStatusHelpers() {
        let snapshot = NativePermissionSnapshot(
            accessibilityTrusted: true,
            screenRecordingTrusted: false,
            microphoneStatus: "authorized"
        )

        XCTAssertEqual(snapshot.status(for: .accessibility), "trusted")
        XCTAssertEqual(snapshot.status(for: .screenRecording), "not trusted")
        XCTAssertEqual(snapshot.status(for: .microphone), "authorized")
        XCTAssertTrue(snapshot.isTrusted(for: .accessibility))
        XCTAssertFalse(snapshot.isTrusted(for: .screenRecording))
        XCTAssertTrue(snapshot.isTrusted(for: .microphone))
    }

    func testPermissionSnapshotRequiresAuthorizedMicrophone() {
        let snapshot = NativePermissionSnapshot(
            accessibilityTrusted: false,
            screenRecordingTrusted: false,
            microphoneStatus: "not determined"
        )

        XCTAssertFalse(snapshot.isTrusted(for: .microphone))
    }
}
