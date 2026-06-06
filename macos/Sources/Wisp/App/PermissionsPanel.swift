import AppKit
import SwiftUI

@MainActor
final class PermissionsPanel: NSPanel {

    private let model: PermissionsModel

    init(
        onRefresh: @escaping (_ promptForAccessibility: Bool) -> NativePermissionSnapshot,
        onRequest: @escaping (NativePermissionKind) async -> NativePermissionSnapshot
    ) {
        let initial = onRefresh(false)
        self.model = PermissionsModel(
            snapshot: initial,
            onRefresh: onRefresh,
            onRequest: onRequest
        )
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 260),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        title = "Permissions"
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        minSize = NSSize(width: 380, height: 240)
        contentView = NSHostingView(rootView: PermissionsPanelView(model: model))
        center()
    }

    func showPermissions(snapshot: NativePermissionSnapshot) {
        model.snapshot = snapshot
        model.status = "Ready"
        if !isVisible {
            center()
        }
        makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@MainActor
private final class PermissionsModel: ObservableObject {
    @Published var snapshot: NativePermissionSnapshot
    @Published var status = "Ready"
    @Published var requesting: NativePermissionKind?

    private let onRefresh: (_ promptForAccessibility: Bool) -> NativePermissionSnapshot
    private let onRequest: (NativePermissionKind) async -> NativePermissionSnapshot

    init(
        snapshot: NativePermissionSnapshot,
        onRefresh: @escaping (_ promptForAccessibility: Bool) -> NativePermissionSnapshot,
        onRequest: @escaping (NativePermissionKind) async -> NativePermissionSnapshot
    ) {
        self.snapshot = snapshot
        self.onRefresh = onRefresh
        self.onRequest = onRequest
    }

    func refresh() {
        snapshot = onRefresh(false)
        status = "Refreshed"
    }

    func request(_ kind: NativePermissionKind) {
        guard requesting == nil else { return }
        requesting = kind
        status = "Opening \(kind.title)..."
        Task {
            let updated = await onRequest(kind)
            snapshot = updated
            requesting = nil
            status = updated.isTrusted(for: kind) ? "\(kind.title) ready" : "\(kind.title) needs review"
        }
    }
}

private struct PermissionsPanelView: View {
    @ObservedObject var model: PermissionsModel

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            VStack(spacing: 10) {
                ForEach(NativePermissionKind.allCases) { kind in
                    PermissionRow(
                        kind: kind,
                        snapshot: model.snapshot,
                        isRequesting: model.requesting == kind,
                        onRequest: { model.request(kind) }
                    )
                }
            }
            .padding(14)
            Spacer(minLength: 0)
        }
        .frame(minWidth: 380, minHeight: 240)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Text("Permissions")
                .font(.system(size: 15, weight: .semibold))
            Text(model.status)
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer()
            Button {
                model.refresh()
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help("Refresh permission status")
            .disabled(model.requesting != nil)
        }
        .padding(.horizontal, 14)
        .frame(height: 42)
    }
}

private struct PermissionRow: View {
    var kind: NativePermissionKind
    var snapshot: NativePermissionSnapshot
    var isRequesting: Bool
    var onRequest: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: kind.systemImage)
                .frame(width: 22)
                .foregroundStyle(snapshot.isTrusted(for: kind) ? Color.green : Color.orange)

            VStack(alignment: .leading, spacing: 2) {
                Text(kind.title)
                    .font(.system(size: 13, weight: .semibold))
                Text(snapshot.status(for: kind))
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }

            Spacer()

            if isRequesting {
                ProgressView()
                    .controlSize(.small)
            }

            Button {
                onRequest()
            } label: {
                Image(systemName: snapshot.isTrusted(for: kind) ? "gearshape" : "arrow.up.forward.app")
            }
            .buttonStyle(.borderless)
            .help(snapshot.isTrusted(for: kind) ? "Open \(kind.title) settings" : "Request \(kind.title) permission")
            .disabled(isRequesting)
        }
        .padding(.horizontal, 12)
        .frame(height: 52)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: NSColor.controlBackgroundColor))
        )
    }
}
