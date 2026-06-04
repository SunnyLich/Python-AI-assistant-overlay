import AppKit

/// Phase-1/2 app wiring: bring up the menubar item and the floating overlay, then
/// perform the brain handshake (spawn the Python sidecar, `ping` it, stream a
/// `brain.echo`) and surface the result in the menu. This is the runnable proof
/// that the Swift host can drive the verified Python seam.
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var statusController: StatusItemController?
    private var overlay: OverlayPanel?
    private var brain: BrainClient?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let status = StatusItemController()
        statusController = status

        let panel = OverlayPanel()
        panel.orderFrontRegardless()
        overlay = panel

        let client = BrainClient(config: BrainLocator.resolve())
        brain = client

        Task { await handshake(client, status: status, panel: panel) }
    }

    func applicationWillTerminate(_ notification: Notification) {
        let client = brain
        Task { await client?.shutdown() }
    }

    /// Spawn + ping + a streamed echo. Mirrors the Python `test_brain_host.py`
    /// flow, but driven from Swift to validate the real transport end to end.
    private func handshake(_ client: BrainClient, status: StatusItemController, panel: OverlayPanel) async {
        do {
            panel.setState(.thinking)
            let pong = try await client.call("ping", ["value": "hello-from-swift"])
            let pid = (pong?["pid"] as? Int).map(String.init) ?? "?"
            await MainActor.run { status.setBrainStatus("ok (pid \(pid))") }

            panel.setState(.speaking)
            var assembled = ""
            for try await item in client.stream("brain.echo", ["text": "the brain seam works"]) {
                switch item {
                case .event(let name, let data) where name == "reply.chunk":
                    if let text = data?["text"] as? String { assembled += text }
                case .result(let result):
                    assembled = (result?["text"] as? String) ?? assembled
                default:
                    break
                }
            }
            NSLog("[wisp] echo stream assembled: %@", assembled)
            panel.setState(.idle)
        } catch {
            await MainActor.run { status.setBrainStatus("error: \(error)") }
            panel.setState(.idle)
            NSLog("[wisp] brain handshake failed: %@", String(describing: error))
        }
    }
}
