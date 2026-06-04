import XCTest
@testable import Wisp

/// Pure framing tests — no Python sidecar required, so they run in plain CI.
/// End-to-end transport is covered by the Python harness
/// (`macos/brain/tests/test_brain_host.py`); a Swift-driven integration test that
/// spawns the sidecar belongs in a macOS CI job once the runtime is bundled.
final class ProtocolTests: XCTestCase {

    func testRequestEncodesAsSingleNewlineTerminatedLine() throws {
        let line = try BrainProtocol.encodeLine(
            BrainProtocol.request(id: 7, method: "ping", params: ["value": "hi"])
        )
        XCTAssertEqual(line.last, 0x0A, "frame must end with a single newline")
        XCTAssertEqual(line.filter { $0 == 0x0A }.count, 1, "exactly one newline per frame")

        let decoded = BrainProtocol.decodeLine(line.dropLast())
        XCTAssertEqual(decoded?["id"] as? Int, 7)
        XCTAssertEqual(decoded?["method"] as? String, "ping")
    }

    func testDecodeRejectsGarbageLine() {
        XCTAssertNil(BrainProtocol.decodeLine(Data("not json".utf8)))
        XCTAssertNil(BrainProtocol.decodeLine(Data()))
    }

    func testDecodeEventCarriesId() throws {
        let raw = Data(#"{"event":"reply.chunk","id":3,"data":{"text":"x"}}"#.utf8)
        let msg = BrainProtocol.decodeLine(raw)
        XCTAssertEqual(msg?["event"] as? String, "reply.chunk")
        XCTAssertEqual(msg?["id"] as? Int, 3)
    }
}
