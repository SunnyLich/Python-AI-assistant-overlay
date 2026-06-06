import Foundation

enum NativeLaunchDiagnostics {
    static let startupMarkerName = "native-app-launch.log"

    static func writeStartupRecord(config: WispConfig) {
        let environment = RunLogLocator.environmentByResolvingLogDirectory(
            environment: ProcessInfo.processInfo.environment,
            resourceURL: Bundle.main.resourceURL
        )
        guard let logDirectory = RunLogLocator.logDirectory(
            environment: environment,
            resourceURL: Bundle.main.resourceURL
        ) else { return }
        do {
            try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
            let url = logDirectory.appendingPathComponent(startupMarkerName)
            let record = startupRecord(environment: environment, config: config)
            try record.write(to: url, atomically: true, encoding: .utf8)
            NSLog("[wisp] native launch marker written: %@", url.path)
        } catch {
            NSLog("[wisp] native launch marker failed: %@", String(describing: error))
        }
    }

    static func startupRecord(
        now: Date = Date(),
        environment: [String: String] = ProcessInfo.processInfo.environment,
        config: WispConfig,
        resourceURL: URL? = Bundle.main.resourceURL
    ) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]

        return [
            "started_at=\(formatter.string(from: now))",
            "repo_root=\(environment["WISP_REPO_ROOT"] ?? "")",
            "run_log_dir=\(environment["WISP_RUN_LOG_DIR"] ?? "")",
            "resource_url=\(resourceURL?.path ?? "")",
            "caller_count=\(config.callers.count)",
            "snip_hotkey=\(config.snip.hotkey)",
        ].joined(separator: "\n") + "\n"
    }
}
