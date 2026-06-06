import XCTest
import Foundation
@testable import Wisp

final class RunLogLocatorTests: XCTestCase {

    func testExplicitRunLogDirectoryWins() {
        let url = RunLogLocator.logDirectory(
            environment: [
                "WISP_RUN_LOG_DIR": "/tmp/wisp-logs",
                "WISP_REPO_ROOT": "/tmp/repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/repo/macos")
        )

        XCTAssertEqual(url?.path, "/tmp/wisp-logs")
    }

    func testFallsBackToNewestNativeMacLogDirectory() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-run-log-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let logs = root.appendingPathComponent("build_logs")
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        let old = logs.appendingPathComponent("macos_native_tests_20260101-010101")
        let newest = logs.appendingPathComponent("macos_phase1_20260202-020202")
        let ignored = logs.appendingPathComponent("windows_build_20260303-030303")
        try FileManager.default.createDirectory(at: old, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: newest, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: ignored, withIntermediateDirectories: true)

        let oldDate = Date(timeIntervalSince1970: 100)
        let newestDate = Date(timeIntervalSince1970: 200)
        try FileManager.default.setAttributes([.modificationDate: oldDate], ofItemAtPath: old.path)
        try FileManager.default.setAttributes([.modificationDate: newestDate], ofItemAtPath: newest.path)
        try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 300)], ofItemAtPath: ignored.path)

        let url = RunLogLocator.logDirectory(
            environment: ["WISP_REPO_ROOT": root.path],
            currentDirectory: root.appendingPathComponent("macos")
        )

        XCTAssertEqual(url?.standardizedFileURL.path, newest.standardizedFileURL.path)
    }
}
