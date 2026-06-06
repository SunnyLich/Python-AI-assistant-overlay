import ApplicationServices
import XCTest
@testable import Wisp

final class HotkeyControllerTests: XCTestCase {

    func testParsesCallerHotkeyString() throws {
        let hotkey = try XCTUnwrap(
            HotkeyDefinition.parse("ctrl+shift+q", callerIndex: 1, label: "Rewrite")
        )

        XCTAssertEqual(hotkey.action, .caller(1))
        XCTAssertEqual(hotkey.keyCode, 12)
        XCTAssertTrue(hotkey.modifiers.contains(.maskControl))
        XCTAssertTrue(hotkey.modifiers.contains(.maskShift))
        XCTAssertFalse(hotkey.modifiers.contains(.maskAlternate))
    }

    func testParsesOptionAliasAndNamedKeys() throws {
        let hotkey = try XCTUnwrap(
            HotkeyDefinition.parse("control+option+space", callerIndex: 0)
        )

        XCTAssertEqual(hotkey.keyCode, 49)
        XCTAssertTrue(hotkey.modifiers.contains(.maskControl))
        XCTAssertTrue(hotkey.modifiers.contains(.maskAlternate))
    }

    func testMatchesExactModifierSet() throws {
        let general = try XCTUnwrap(HotkeyDefinition.parse("ctrl+q", callerIndex: 0))
        let rewrite = try XCTUnwrap(HotkeyDefinition.parse("ctrl+shift+q", callerIndex: 1))

        XCTAssertTrue(general.matches(keyCode: 12, flags: [.maskControl]))
        XCTAssertFalse(general.matches(keyCode: 12, flags: [.maskControl, .maskShift]))
        XCTAssertTrue(rewrite.matches(keyCode: 12, flags: [.maskControl, .maskShift]))
    }

    func testRejectsInvalidHotkey() {
        XCTAssertNil(HotkeyDefinition.parse("ctrl+unknown-key", callerIndex: 0))
        XCTAssertNil(HotkeyDefinition.parse("ctrl+q+w", callerIndex: 0))
        XCTAssertNil(HotkeyDefinition.parse("", callerIndex: 0))
    }

    func testParsesSnipHotkeyAction() throws {
        let hotkey = try XCTUnwrap(
            HotkeyDefinition.parse("ctrl+alt+q", action: .snip, label: "Snip")
        )

        XCTAssertEqual(hotkey.action, .snip)
        XCTAssertEqual(hotkey.keyCode, 12)
        XCTAssertTrue(hotkey.modifiers.contains(.maskControl))
        XCTAssertTrue(hotkey.modifiers.contains(.maskAlternate))
    }

    func testParsesContextHotkeyActions() throws {
        let add = try XCTUnwrap(
            HotkeyDefinition.parse("alt+q", action: .addContext, label: "Add context")
        )
        let clear = try XCTUnwrap(
            HotkeyDefinition.parse("alt+w", action: .clearContext, label: "Clear context")
        )

        XCTAssertEqual(add.action, .addContext)
        XCTAssertEqual(add.keyCode, 12)
        XCTAssertTrue(add.modifiers.contains(.maskAlternate))
        XCTAssertEqual(clear.action, .clearContext)
        XCTAssertEqual(clear.keyCode, 13)
        XCTAssertTrue(clear.modifiers.contains(.maskAlternate))
    }

    func testParsesVoiceHotkeyAction() throws {
        let hotkey = try XCTUnwrap(
            HotkeyDefinition.parse("f9", action: .voiceStart, label: "Voice")
        )

        XCTAssertEqual(hotkey.action, .voiceStart)
        XCTAssertEqual(hotkey.keyCode, 101)
    }
}
