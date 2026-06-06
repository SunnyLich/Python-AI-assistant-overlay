import XCTest
import Foundation
@testable import Wisp

final class NativeLaunchDiagnosticsTests: XCTestCase {

    func testStartupRecordContainsOnlyLaunchDiagnostics() {
        let config = WispConfig.load(
            environment: [
                "CALLER_COUNT": "1",
                "CALLER_1_LABEL": "Research",
                "HOTKEY_SNIP": "ctrl+option+4",
            ],
            readDotEnv: false
        )
        let record = NativeLaunchDiagnostics.startupRecord(
            now: Date(timeIntervalSince1970: 1_704_067_200),
            environment: [
                "WISP_REPO_ROOT": "/tmp/wisp",
                "WISP_RUN_LOG_DIR": "/tmp/wisp/build_logs/macos_phase1_20260101-000000",
                "OPENAI_API_KEY": "should-not-be-written",
            ],
            config: config,
            resourceURL: URL(fileURLWithPath: "/tmp/wisp/build/WispNative/Wisp.app/Contents/Resources")
        )

        XCTAssertTrue(record.contains("started_at=2024-01-01T00:00:00Z"))
        XCTAssertTrue(record.contains("repo_root=/tmp/wisp"))
        XCTAssertTrue(record.contains("run_log_dir=/tmp/wisp/build_logs/macos_phase1_20260101-000000"))
        XCTAssertTrue(record.contains("resource_url=/tmp/wisp/build/WispNative/Wisp.app/Contents/Resources"))
        XCTAssertTrue(record.contains("caller_count=1"))
        XCTAssertTrue(record.contains("snip_hotkey=ctrl+option+4"))
        XCTAssertFalse(record.contains("should-not-be-written"))
    }
}
