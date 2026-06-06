import Foundation

/// Resolves where the Python interpreter and the `wisp_brain` package live,
/// covering both the shipped app and local development.
///
/// - Release: the embedded python-build-standalone runtime and a copy of the
///   brain (+ `core`) are bundled under `Wisp.app/Contents/Resources`.
/// - Dev: point at a checkout via env vars so you can iterate without bundling:
///     WISP_BRAIN_PYTHON  — interpreter (default: checkout .venv, then /usr/bin/python3)
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

        let devBundleRepoRoot = resourceURL.flatMap {
            repoRootForDevBundle(resourceURL: $0, fileManager: fm)
        }
        let environmentRepoRoot = nonEmpty(environment["WISP_REPO_ROOT"])
            .map { URL(fileURLWithPath: $0) }
        let inferredRepoRoot = environmentRepoRoot ?? devBundleRepoRoot

        // 2. Dev fallback via environment.
        if environment["WISP_BRAIN_PYTHON"] != nil
            || environment["WISP_BRAIN_DIR"] != nil {
            let python = devPython(
                environmentValue: environment["WISP_BRAIN_PYTHON"],
                repoRoot: inferredRepoRoot,
                fileManager: fm
            )
            let brainDir = devBrainDirectory(
                environmentValue: environment["WISP_BRAIN_DIR"],
                repoRoot: inferredRepoRoot,
                currentDirectory: currentDirectory
            )
            return BrainClient.Config(
                pythonExecutable: python,
                brainDirectory: brainDir,
                extraPythonPath: inferredRepoRoot.map { [$0] } ?? []
            )
        }

        // 3. Finder-launched dev bundle from build/WispNative/Wisp.app.
        if let repoRoot = devBundleRepoRoot {
            return BrainClient.Config(
                pythonExecutable: devPython(environmentValue: nil, repoRoot: repoRoot, fileManager: fm),
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

    private static func devPython(
        environmentValue rawValue: String?,
        repoRoot: URL?,
        fileManager fm: FileManager
    ) -> URL {
        if let raw = nonEmpty(rawValue) {
            if raw.hasPrefix("/") {
                return URL(fileURLWithPath: raw)
            }
            if raw.contains("/") {
                return (repoRoot ?? URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
                    .appendingPathComponent(raw)
            }
            if let repoRoot, let venvPython = venvPython(in: repoRoot, fileManager: fm) {
                return venvPython
            }
            return URL(fileURLWithPath: "/usr/bin/python3")
        }

        if let repoRoot, let venvPython = venvPython(in: repoRoot, fileManager: fm) {
            return venvPython
        }
        return URL(fileURLWithPath: "/usr/bin/python3")
    }

    private static func devBrainDirectory(
        environmentValue rawValue: String?,
        repoRoot: URL?,
        currentDirectory: URL
    ) -> URL {
        if let raw = nonEmpty(rawValue) {
            if raw.hasPrefix("/") {
                return URL(fileURLWithPath: raw)
            }
            return (repoRoot ?? currentDirectory).appendingPathComponent(raw)
        }
        if let repoRoot {
            return repoRoot.appendingPathComponent("macos/brain")
        }
        return currentDirectory.appendingPathComponent("brain")
    }

    private static func venvPython(in repoRoot: URL, fileManager fm: FileManager) -> URL? {
        for relativePath in [".venv/bin/python", ".venv/bin/python3"] {
            let candidate = repoRoot.appendingPathComponent(relativePath)
            if fm.fileExists(atPath: candidate.path) {
                return candidate
            }
        }
        return nil
    }

    private static func nonEmpty(_ value: String?) -> String? {
        let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func repoRootForDevBundle(resourceURL: URL, fileManager fm: FileManager) -> URL? {
        let repoRoot = resourceURL
            .deletingLastPathComponent() // Contents
            .deletingLastPathComponent() // Wisp.app
            .deletingLastPathComponent() // WispNative
            .deletingLastPathComponent() // build
            .deletingLastPathComponent()
        let brain = repoRoot.appendingPathComponent("macos/brain")
        guard fm.fileExists(atPath: brain.path) else {
            return nil
        }
        return repoRoot
    }
}
