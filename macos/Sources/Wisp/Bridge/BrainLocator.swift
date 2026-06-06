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
        return resolve(
            environment: ProcessInfo.processInfo.environment,
            currentDirectory: URL(fileURLWithPath: fm.currentDirectoryPath),
            resourceURL: Bundle.main.resourceURL,
            fileManager: fm
        )
    }

    static func resolve(
        environment: [String: String],
        currentDirectory: URL,
        resourceURL: URL?,
        fileManager fm: FileManager
    ) -> BrainClient.Config {
        // 1. Bundled runtime (release).
        if let res = resourceURL {
            let python = res.appendingPathComponent("python-runtime/bin/python3")
            let brain = res.appendingPathComponent("brain")
            let core = res.appendingPathComponent("core")
            if fm.fileExists(atPath: python.path),
               fm.fileExists(atPath: brain.path),
               fm.fileExists(atPath: core.path) {
                // The bundled `brain` dir is laid out so `core` sits alongside it.
                return BrainClient.Config(
                    pythonExecutable: python,
                    brainDirectory: brain,
                    extraPythonPath: [res]
                )
            }
        }

        // 2. Dev fallback via environment.
        if environment["WISP_BRAIN_PYTHON"] != nil
            || environment["WISP_BRAIN_DIR"] != nil
            || environment["WISP_REPO_ROOT"] != nil {
            let python = environment["WISP_BRAIN_PYTHON"].map { URL(fileURLWithPath: $0) }
                ?? URL(fileURLWithPath: "/usr/bin/python3")
            let brainDir = environment["WISP_BRAIN_DIR"].map { URL(fileURLWithPath: $0) }
                ?? currentDirectory.appendingPathComponent("brain")
            let repoRoot = environment["WISP_REPO_ROOT"].map { URL(fileURLWithPath: $0) }
            return BrainClient.Config(
                pythonExecutable: python,
                brainDirectory: brainDir,
                extraPythonPath: repoRoot.map { [$0] } ?? []
            )
        }

        // 3. Finder-launched dev bundle from build/WispNative/Wisp.app.
        if let res = resourceURL,
           let repoRoot = repoRootForDevBundle(resourceURL: res, fileManager: fm) {
            return BrainClient.Config(
                pythonExecutable: repoRoot.appendingPathComponent(".venv/bin/python"),
                brainDirectory: repoRoot.appendingPathComponent("macos/brain"),
                extraPythonPath: [repoRoot]
            )
        }

        // 4. Plain swift run/build fallback from the macos package directory.
        let python = URL(fileURLWithPath: "/usr/bin/python3")
        let brainDir = currentDirectory.appendingPathComponent("brain")

        return BrainClient.Config(
            pythonExecutable: python,
            brainDirectory: brainDir,
            extraPythonPath: []
        )
    }

    private static func repoRootForDevBundle(resourceURL: URL, fileManager fm: FileManager) -> URL? {
        let repoRoot = resourceURL
            .deletingLastPathComponent() // Contents
            .deletingLastPathComponent() // Wisp.app
            .deletingLastPathComponent() // WispNative
            .deletingLastPathComponent() // build
            .deletingLastPathComponent()
        let python = repoRoot.appendingPathComponent(".venv/bin/python")
        let brain = repoRoot.appendingPathComponent("macos/brain")
        guard fm.fileExists(atPath: python.path), fm.fileExists(atPath: brain.path) else {
            return nil
        }
        return repoRoot
    }
}
