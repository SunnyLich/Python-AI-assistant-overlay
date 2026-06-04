import Foundation

/// Resolves where the Python interpreter and the `wisp_brain` package live,
/// covering both the shipped app and local development.
///
/// - Release: the embedded python-build-standalone runtime and a copy of the
///   brain (+ `core`) are bundled under `Wisp.app/Contents/Resources`.
/// - Dev: point at a checkout via env vars so you can iterate without bundling:
///     WISP_BRAIN_PYTHON  — interpreter (default: /usr/bin/python3)
///     WISP_BRAIN_DIR     — dir containing the `wisp_brain` package (macos/brain)
///     WISP_REPO_ROOT     — repo root (added to PYTHONPATH so `core` imports)
enum BrainLocator {

    static func resolve() -> BrainClient.Config {
        let fm = FileManager.default

        // 1. Bundled runtime (release).
        if let res = Bundle.main.resourceURL {
            let python = res.appendingPathComponent("python-runtime/bin/python3")
            let brain = res.appendingPathComponent("brain")
            if fm.fileExists(atPath: python.path), fm.fileExists(atPath: brain.path) {
                // The bundled `brain` dir is laid out so `core` sits alongside it.
                return BrainClient.Config(
                    pythonExecutable: python,
                    brainDirectory: brain,
                    extraPythonPath: [res]
                )
            }
        }

        // 2. Dev fallback via environment.
        let env = ProcessInfo.processInfo.environment
        let python = env["WISP_BRAIN_PYTHON"].map { URL(fileURLWithPath: $0) }
            ?? URL(fileURLWithPath: "/usr/bin/python3")
        let brainDir = env["WISP_BRAIN_DIR"].map { URL(fileURLWithPath: $0) }
            ?? URL(fileURLWithPath: fm.currentDirectoryPath).appendingPathComponent("brain")
        let repoRoot = env["WISP_REPO_ROOT"].map { URL(fileURLWithPath: $0) }

        return BrainClient.Config(
            pythonExecutable: python,
            brainDirectory: brainDir,
            extraPythonPath: repoRoot.map { [$0] } ?? []
        )
    }
}
