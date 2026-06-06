import AppKit
import ApplicationServices

enum HotkeyInstallResult: Equatable {
    case installed(Int)
    case accessibilityNeeded
    case failed(String)

    var statusText: String {
        switch self {
        case .installed(let count):
            return count == 1 ? "1 hotkey ready" : "\(count) hotkeys ready"
        case .accessibilityNeeded:
            return "Accessibility permission needed"
        case .failed(let message):
            return "failed: \(message)"
        }
    }
}

enum HotkeyAction: Equatable {
    case caller(Int)
    case snip
    case addContext
    case clearContext
    case voiceStart
    case voiceStop
}

struct HotkeyDefinition: Equatable {
    var action: HotkeyAction
    var label: String
    var display: String
    var keyCode: Int64
    var modifiers: CGEventFlags

    static func parse(_ raw: String, callerIndex: Int, label: String = "") -> HotkeyDefinition? {
        parse(raw, action: .caller(callerIndex), label: label)
    }

    static func parse(_ raw: String, action: HotkeyAction, label: String = "") -> HotkeyDefinition? {
        let tokens = raw
            .split(separator: "+")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
            .filter { !$0.isEmpty }
        guard !tokens.isEmpty else { return nil }

        var modifiers = CGEventFlags(rawValue: 0)
        var keyCode: Int64?

        for token in tokens {
            switch token {
            case "ctrl", "control":
                modifiers.insert(.maskControl)
            case "alt", "option", "opt":
                modifiers.insert(.maskAlternate)
            case "shift":
                modifiers.insert(.maskShift)
            case "cmd", "command", "meta", "win":
                modifiers.insert(.maskCommand)
            default:
                guard keyCode == nil, let parsedCode = keyCodeForToken(token) else {
                    return nil
                }
                keyCode = parsedCode
            }
        }

        guard let keyCode else { return nil }
        return HotkeyDefinition(
            action: action,
            label: label,
            display: raw,
            keyCode: keyCode,
            modifiers: modifiers
        )
    }

    func matches(keyCode: Int64, flags: CGEventFlags) -> Bool {
        guard self.keyCode == keyCode else { return false }
        let checkedModifiers: [CGEventFlags] = [.maskControl, .maskAlternate, .maskShift, .maskCommand]
        for required in checkedModifiers {
            if flags.contains(required) != modifiers.contains(required) {
                return false
            }
        }
        return true
    }

    private static func keyCodeForToken(_ token: String) -> Int64? {
        if let code = namedKeyCodes[token] {
            return code
        }
        if token.count == 1, let code = ansiKeyCodes[token] {
            return code
        }
        if token.hasPrefix("f"),
           let number = Int(token.dropFirst()),
           let code = functionKeyCodes[number] {
            return code
        }
        return nil
    }

    private static let ansiKeyCodes: [String: Int64] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
        "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
        "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
        "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
        "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
        "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
        "n": 45, "m": 46, ".": 47, "`": 50,
    ]

    private static let namedKeyCodes: [String: Int64] = [
        "space": 49,
        "spacebar": 49,
        "space_bar": 49,
        "tab": 48,
        "enter": 36,
        "return": 36,
        "backspace": 51,
        "delete": 51,
        "escape": 53,
        "esc": 53,
        "left": 123,
        "right": 124,
        "down": 125,
        "up": 126,
    ]

    private static let functionKeyCodes: [Int: Int64] = [
        1: 122, 2: 120, 3: 99, 4: 118, 5: 96, 6: 97,
        7: 98, 8: 100, 9: 101, 10: 109, 11: 103, 12: 111,
    ]
}

@MainActor
final class HotkeyController {

    private let onTrigger: (HotkeyAction) -> Void
    private var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    private var definitions: [HotkeyDefinition] = []

    init(onTrigger: @escaping (HotkeyAction) -> Void) {
        self.onTrigger = onTrigger
    }

    func start(
        callers: [CallerConfig],
        snip: SnipConfig? = nil,
        addContextHotkey: String? = nil,
        clearContextHotkey: String? = nil,
        voiceHotkey: String? = nil,
        promptForPermission: Bool
    ) -> HotkeyInstallResult {
        stop()
        definitions = callers.enumerated().compactMap { index, caller in
            HotkeyDefinition.parse(caller.hotkey, callerIndex: index, label: caller.label)
        }
        if let snipDefinition = snip.flatMap({ HotkeyDefinition.parse($0.hotkey, action: .snip, label: "Snip") }) {
            definitions.append(snipDefinition)
        }
        if let addDefinition = addContextHotkey.flatMap({
            HotkeyDefinition.parse($0, action: .addContext, label: "Add context")
        }) {
            definitions.append(addDefinition)
        }
        if let clearDefinition = clearContextHotkey.flatMap({
            HotkeyDefinition.parse($0, action: .clearContext, label: "Clear context")
        }) {
            definitions.append(clearDefinition)
        }
        if let voiceDefinition = voiceHotkey.flatMap({
            HotkeyDefinition.parse($0, action: .voiceStart, label: "Voice")
        }) {
            definitions.append(voiceDefinition)
        }
        guard !definitions.isEmpty else {
            return .failed("no valid hotkeys configured")
        }

        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let options = [promptKey: promptForPermission] as CFDictionary
        guard AXIsProcessTrustedWithOptions(options) else {
            NSLog("[wisp] hotkey unavailable: Accessibility permission is not trusted yet")
            return .accessibilityNeeded
        }

        let mask = CGEventMask((1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.keyUp.rawValue))
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: hotkeyEventCallback,
            userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else {
            NSLog("[wisp] hotkey unavailable: CGEvent.tapCreate returned nil")
            return .failed("event tap could not be created")
        }

        guard let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) else {
            return .failed("run loop source could not be created")
        }

        eventTap = tap
        runLoopSource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        NSLog("[wisp] hotkeys installed: %@", definitions.map(\.display).joined(separator: ", "))
        return .installed(definitions.count)
    }

    func stop() {
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
        }
        if let source = runLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
        }
        eventTap = nil
        runLoopSource = nil
    }

    fileprivate func handleEvent(typeRawValue: UInt32, keyCode: Int64, flagsRawValue: UInt64, isRepeat: Bool) {
        if typeRawValue == CGEventType.tapDisabledByTimeout.rawValue ||
            typeRawValue == CGEventType.tapDisabledByUserInput.rawValue {
            if let eventTap {
                CGEvent.tapEnable(tap: eventTap, enable: true)
                NSLog("[wisp] hotkey event tap re-enabled after disable event")
            }
            return
        }

        guard typeRawValue == CGEventType.keyDown.rawValue || typeRawValue == CGEventType.keyUp.rawValue else { return }
        guard typeRawValue != CGEventType.keyDown.rawValue || !isRepeat else { return }

        let flags = CGEventFlags(rawValue: flagsRawValue)
        guard let definition = definitions.first(where: { $0.matches(keyCode: keyCode, flags: flags) }) else {
            return
        }
        NSLog("[wisp] hotkey triggered: %@", definition.display)
        if typeRawValue == CGEventType.keyUp.rawValue {
            if definition.action == .voiceStart {
                onTrigger(.voiceStop)
            }
            return
        }
        onTrigger(definition.action)
    }
}

private let hotkeyEventCallback: CGEventTapCallBack = { _, type, event, userInfo in
    if let userInfo {
        let controller = Unmanaged<HotkeyController>.fromOpaque(userInfo).takeUnretainedValue()
        let keyCode = event.getIntegerValueField(.keyboardEventKeycode)
        let isRepeat = event.getIntegerValueField(.keyboardEventAutorepeat) != 0
        let flagsRawValue = event.flags.rawValue
        let typeRawValue = type.rawValue
        Task { @MainActor in
            controller.handleEvent(
                typeRawValue: typeRawValue,
                keyCode: keyCode,
                flagsRawValue: flagsRawValue,
                isRepeat: isRepeat
            )
        }
    }
    return Unmanaged.passUnretained(event)
}
