import XCTest
import Foundation
@testable import Wisp

final class BrainLocatorTests: XCTestCase {

    func testBundledRuntimeBrainAndCoreWinOverDevelopmentEnvironment() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-brain-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let resourceURL = root.appendingPathComponent("Wisp.app/Contents/Resources")
        let python = resourceURL.appendingPathComponent("python-runtime/bin/python3")
        let brain = resourceURL.appendingPathComponent("brain")
        let core = resourceURL.appendingPathComponent("core")
        try FileManager.default.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: brain, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: core, withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: python.path, contents: Data()))

        let config = BrainLocator.resolve(
            environment: [
                "WISP_BRAIN_PYTHON": "/tmp/dev-python",
                "WISP_BRAIN_DIR": "/tmp/dev-brain",
                "WISP_REPO_ROOT": "/tmp/dev-repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/dev-repo/macos"),
            resourceURL: resourceURL,
            fileManager: .default
        )

        XCTAssertEqual(config.pythonExecutable.standardizedFileURL.path, python.standardizedFileURL.path)
        XCTAssertEqual(config.brainDirectory.standardizedFileURL.path, brain.standardizedFileURL.path)
        XCTAssertEqual(config.extraPythonPath.map { $0.standardizedFileURL.path }, [resourceURL.standardizedFileURL.path])
    }

    func testIncompleteBundledRuntimeFallsBackToDevelopmentEnvironment() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-brain-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let resourceURL = root.appendingPathComponent("Wisp.app/Contents/Resources")
        let python = resourceURL.appendingPathComponent("python-runtime/bin/python3")
        let brain = resourceURL.appendingPathComponent("brain")
        try FileManager.default.createDirectory(at: python.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: brain, withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: python.path, contents: Data()))

        let config = BrainLocator.resolve(
            environment: [
                "WISP_BRAIN_PYTHON": "/tmp/dev-python",
                "WISP_BRAIN_DIR": "/tmp/dev-brain",
                "WISP_REPO_ROOT": "/tmp/dev-repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/dev-repo/macos"),
            resourceURL: resourceURL,
            fileManager: .default
        )

        XCTAssertEqual(config.pythonExecutable.path, "/tmp/dev-python")
        XCTAssertEqual(config.brainDirectory.path, "/tmp/dev-brain")
        XCTAssertEqual(config.extraPythonPath.map(\.path), ["/tmp/dev-repo"])
    }

    func testEnvironmentBrainSettingsWinWhenNoBundledRuntimeExists() {
        let config = BrainLocator.resolve(
            environment: [
                "WISP_BRAIN_PYTHON": "/tmp/python",
                "WISP_BRAIN_DIR": "/tmp/brain",
                "WISP_REPO_ROOT": "/tmp/repo",
            ],
            currentDirectory: URL(fileURLWithPath: "/tmp/repo/macos"),
            resourceURL: nil,
            fileManager: .default
        )

        XCTAssertEqual(config.pythonExecutable.path, "/tmp/python")
        XCTAssertEqual(config.brainDirectory.path, "/tmp/brain")
        XCTAssertEqual(config.extraPythonPath.map(\.path), ["/tmp/repo"])
    }

    func testFinderLaunchedDevBundleInfersRepoBrainFromBundlePath() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("wisp-brain-locator-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let resourceURL = root
            .appendingPathComponent("build/WispNative/Wisp.app/Contents/Resources")
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent(".venv/bin"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: root.appendingPathComponent("macos/brain"),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(at: resourceURL, withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(
            atPath: root.appendingPathComponent(".venv/bin/python").path,
            contents: Data()
        ))

        let config = BrainLocator.resolve(
            environment: [:],
            currentDirectory: URL(fileURLWithPath: "/"),
            resourceURL: resourceURL,
            fileManager: .default
        )

        XCTAssertEqual(
            config.pythonExecutable.standardizedFileURL.path,
            root.appendingPathComponent(".venv/bin/python").standardizedFileURL.path
        )
        XCTAssertEqual(
            config.brainDirectory.standardizedFileURL.path,
            root.appendingPathComponent("macos/brain").standardizedFileURL.path
        )
        XCTAssertEqual(config.extraPythonPath.map { $0.standardizedFileURL.path }, [root.standardizedFileURL.path])
    }
}
