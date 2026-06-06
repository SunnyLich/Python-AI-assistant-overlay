import AppKit
import ApplicationServices
import AVFoundation
import CoreGraphics

struct NativeContextSnapshot {
    var appName: String
    var bundleID: String
    var processID: pid_t?
    var windowTitle: String?
    var clipboardText: String?
    var selectedText: String?
    var accessibilityTrusted: Bool

    var ambientText: String {
        ambientText(includeClipboard: true)
    }

    func ambientText(includeClipboard: Bool) -> String {
        [
            "Active app: \(appName)",
            bundleID.isEmpty ? nil : "Bundle ID: \(bundleID)",
            processID.map { "PID: \($0)" },
            nonEmpty(windowTitle).map { "Window: \($0)" },
            includeClipboard ? nonEmpty(clipboardText).map { "Clipboard:\n\($0)" } : nil,
        ]
        .compactMap { $0 }
        .joined(separator: "\n\n")
    }

    var displayText: String {
        [
            "Active app: \(appName)",
            bundleID.isEmpty ? nil : "Bundle ID: \(bundleID)",
            processID.map { "PID: \($0)" },
            nonEmpty(windowTitle).map { "Focused window: \($0)" },
            "Accessibility: \(accessibilityTrusted ? "trusted" : "not trusted")",
            nonEmpty(selectedText).map { "Selected text:\n\($0)" } ?? "Selected text: unavailable",
            nonEmpty(clipboardText).map { "Clipboard:\n\($0)" } ?? "Clipboard: empty or non-text",
        ]
        .compactMap { $0 }
        .joined(separator: "\n\n")
    }

    var logSummary: String {
        "app=\(appName) bundle=\(bundleID) ax=\(accessibilityTrusted) selected=\(selectedText?.count ?? 0) clipboard=\(clipboardText?.count ?? 0)"
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct NativePermissionSnapshot {
    var accessibilityTrusted: Bool
    var screenRecordingTrusted: Bool
    var microphoneStatus: String

    var displayText: String {
        """
        Accessibility: \(accessibilityTrusted ? "trusted" : "not trusted")
        Screen Recording: \(screenRecordingTrusted ? "trusted" : "not trusted")
        Microphone: \(microphoneStatus)
        """
    }

    func status(for kind: NativePermissionKind) -> String {
        switch kind {
        case .accessibility:
            return accessibilityTrusted ? "trusted" : "not trusted"
        case .screenRecording:
            return screenRecordingTrusted ? "trusted" : "not trusted"
        case .microphone:
            return microphoneStatus
        }
    }

    func isTrusted(for kind: NativePermissionKind) -> Bool {
        switch kind {
        case .accessibility:
            return accessibilityTrusted
        case .screenRecording:
            return screenRecordingTrusted
        case .microphone:
            return microphoneStatus == "authorized"
        }
    }
}

enum NativePermissionKind: String, CaseIterable, Identifiable {
    case accessibility
    case screenRecording
    case microphone

    var id: String { rawValue }

    var title: String {
        switch self {
        case .accessibility:
            return "Accessibility"
        case .screenRecording:
            return "Screen Recording"
        case .microphone:
            return "Microphone"
        }
    }

    var systemImage: String {
        switch self {
        case .accessibility:
            return "keyboard"
        case .screenRecording:
            return "rectangle.dashed"
        case .microphone:
            return "mic"
        }
    }

    var settingsURL: URL {
        switch self {
        case .accessibility:
            return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!
        case .screenRecording:
            return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!
        case .microphone:
            return URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!
        }
    }
}

@MainActor
final class NativeContextController {

    func snapshot(promptForAccessibility: Bool) -> NativeContextSnapshot {
        let app = NSWorkspace.shared.frontmostApplication
        let pid = app?.processIdentifier
        let accessibilityTrusted = accessibilityIsTrusted(prompt: promptForAccessibility)

        return NativeContextSnapshot(
            appName: app?.localizedName ?? "Unknown",
            bundleID: app?.bundleIdentifier ?? "",
            processID: pid,
            windowTitle: accessibilityTrusted ? focusedWindowTitle(pid: pid) : nil,
            clipboardText: clippedText(NSPasteboard.general.string(forType: .string), limit: 1600),
            selectedText: accessibilityTrusted ? clippedText(selectedText(pid: pid), limit: 1600) : nil,
            accessibilityTrusted: accessibilityTrusted
        )
    }

    func permissions(promptForAccessibility: Bool) -> NativePermissionSnapshot {
        NativePermissionSnapshot(
            accessibilityTrusted: accessibilityIsTrusted(prompt: promptForAccessibility),
            screenRecordingTrusted: CGPreflightScreenCaptureAccess(),
            microphoneStatus: microphoneStatusText()
        )
    }

    func requestPermission(_ kind: NativePermissionKind) async -> NativePermissionSnapshot {
        switch kind {
        case .accessibility:
            _ = accessibilityIsTrusted(prompt: true)
            NSWorkspace.shared.open(kind.settingsURL)
        case .screenRecording:
            if !CGPreflightScreenCaptureAccess() {
                _ = CGRequestScreenCaptureAccess()
            }
            NSWorkspace.shared.open(kind.settingsURL)
        case .microphone:
            if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
                _ = await withCheckedContinuation { continuation in
                    AVCaptureDevice.requestAccess(for: .audio) { granted in
                        continuation.resume(returning: granted)
                    }
                }
            } else {
                NSWorkspace.shared.open(kind.settingsURL)
            }
        }
        return permissions(promptForAccessibility: false)
    }

    private func accessibilityIsTrusted(prompt: Bool) -> Bool {
        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let options = [promptKey: prompt] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    private func focusedWindowTitle(pid: pid_t?) -> String? {
        guard let pid else { return nil }
        let appElement = AXUIElementCreateApplication(pid)
        var windowValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(appElement, kAXFocusedWindowAttribute as CFString, &windowValue) == .success,
              let windowElement = axElement(windowValue) else {
            return nil
        }

        var titleValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(windowElement, kAXTitleAttribute as CFString, &titleValue) == .success else {
            return nil
        }
        return titleValue as? String
    }

    private func selectedText(pid: pid_t?) -> String? {
        guard let pid else { return nil }
        let appElement = AXUIElementCreateApplication(pid)
        var focusedValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(appElement, kAXFocusedUIElementAttribute as CFString, &focusedValue) == .success,
              let focusedElement = axElement(focusedValue) else {
            return nil
        }

        var selectedValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(focusedElement, kAXSelectedTextAttribute as CFString, &selectedValue) == .success else {
            return nil
        }
        return selectedValue as? String
    }

    private func axElement(_ value: CFTypeRef?) -> AXUIElement? {
        guard let value, CFGetTypeID(value) == AXUIElementGetTypeID() else {
            return nil
        }
        return (value as! AXUIElement)
    }

    private func clippedText(_ text: String?, limit: Int) -> String? {
        guard let text else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard trimmed.count > limit else { return trimmed }

        let end = trimmed.index(trimmed.startIndex, offsetBy: limit)
        return "\(trimmed[..<end])\n...[truncated]"
    }

    private func microphoneStatusText() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return "authorized"
        case .denied:
            return "denied"
        case .restricted:
            return "restricted"
        case .notDetermined:
            return "not determined"
        @unknown default:
            return "unknown"
        }
    }
}
