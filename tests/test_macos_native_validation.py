from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_native_validation_scripts_copy_canonical_live_checklist():
    scripts = {
        "run_macos_native_tests.command": "latest_macos_native_tests.txt",
        "macos_phase1_validate.sh": "latest_macos_phase1.txt",
        "macos_package_release.sh": "latest_macos_package.txt",
    }

    for script_name, latest_pointer in scripts.items():
        script = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")

        assert 'CHECKLIST_TEMPLATE="$REPO_ROOT/docs/MACOS_LIVE_PARITY_CHECKLIST.md"' in script
        assert 'LIVE_CHECKLIST="$LOG_DIR/live-parity-checklist.md"' in script
        assert f'LATEST_LOG_POINTER="$REPO_ROOT/build_logs/{latest_pointer}"' in script
        assert 'cat "$CHECKLIST_TEMPLATE"' in script
        assert "write_live_checklist" in script
        assert "Live parity checklist: $LIVE_CHECKLIST" in script
        assert "Latest log pointer: $LATEST_LOG_POINTER" in script


def test_native_test_runners_execute_checklist_guard():
    for script_name in ["run_macos_native_tests.command", "macos_phase1_validate.sh"]:
        script = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")

        assert "tests/test_macos_native_validation.py" in script


def test_native_test_runners_execute_shared_config_guard():
    for script_name in ["run_macos_native_tests.command", "macos_phase1_validate.sh"]:
        script = (REPO_ROOT / "scripts" / script_name).read_text(encoding="utf-8")

        assert "tests/test_config_env.py" in script


def test_repo_has_double_click_latest_log_helper():
    helper = (REPO_ROOT / "Open Wisp Mac Logs.command").read_text(encoding="utf-8")
    quick = (REPO_ROOT / "Test Wisp (Mac Native).command").read_text(encoding="utf-8")
    start = (REPO_ROOT / "Start Wisp (Mac Native).command").read_text(encoding="utf-8")

    assert "latest_macos_*.txt" in helper
    assert "summary_log" in helper
    assert "live_parity_checklist" in helper
    assert "Latest pointer is stale" in helper
    assert "newest_log_dir_from_folders" in helper
    assert "Test Wisp (Mac Native).command or Start Wisp (Mac Native).command first" in helper
    assert 'open "$log_dir"' in helper
    for launcher in [quick, start]:
        assert 'chmod +x \\' in launcher
        assert '"Open Wisp Mac Logs.command"' in launcher
        assert "scripts/run_macos_native_tests.command" in launcher
        assert "scripts/run_brain_tests.command" in launcher


def test_repo_has_double_click_dev_app_launcher():
    quick = (REPO_ROOT / "Test Wisp (Mac Native).command").read_text(encoding="utf-8")
    start = (REPO_ROOT / "Start Wisp (Mac Native).command").read_text(encoding="utf-8")

    assert "does not launch or replace a running Wisp.app" in quick
    assert "Type launch to rebuild and open Wisp now" in quick
    assert "scripts/run_macos_native_tests.command --open" in quick
    assert "Start Wisp (Mac Native).command" in quick
    assert 'if [ "${1:-}" = "--run" ]' in start
    assert "scripts/macos_phase1_validate.sh --run" in start
    assert "scripts/run_macos_native_tests.command --open" in start


def test_quick_native_runner_provisions_same_macos_venv_as_launch_runner():
    quick = (REPO_ROOT / "scripts" / "run_macos_native_tests.command").read_text(encoding="utf-8")

    for expected in [
        'REQ_FILE="$REPO_ROOT/requirements-macos.lock"',
        'STAMP_FILE="$REPO_ROOT/.venv/.wisp-macos-deps.stamp"',
        "python_matches_want",
        "find_local_python",
        "brain_deps_ok",
        "venv_ready",
        "ensure_uv",
        "setup_venv",
        "Using existing macOS .venv:",
        "Creating .venv with local Python:",
        "Creating .venv with uv Python",
        'run_logged "python-deps-install"',
        'run_logged "uv-deps-install"',
        'export WISP_BRAIN_PYTHON="$PY"',
    ]:
        assert expected in quick

    assert "Run scripts/macos_phase1_validate.sh once to create the .venv." not in quick


def test_live_parity_checklist_tracks_current_manual_gates():
    checklist = (REPO_ROOT / "docs" / "MACOS_LIVE_PARITY_CHECKLIST.md").read_text(encoding="utf-8")

    for expected in [
        "Overlay right-click opens the native context menu.",
        "WASD/caller intent overlay is readable in light mode and dark mode.",
        "Response bubble reply/listening/notice text is readable.",
        "Chat window is readable in light mode and dark mode.",
        "Settings > LLM > API Keys can refresh, save, and clear API-key status.",
        "Settings > LLM > Authentication can refresh provider status.",
        "Reset All clears credentials/.env only after confirmation and reloads UI.",
        "Launch at Login toggle updates System Settings state.",
        "`--open` launch writes `native-app-launch.log` with expected app identity and executable `brain_python`.",
        "Signed app launches with `WISP_VALIDATE_APP_LAUNCH=1` and executable embedded-runtime `brain_python`.",
    ]:
        assert expected in checklist


def test_parity_doc_points_to_current_native_validation_workflow():
    parity = (REPO_ROOT / "docs" / "MACOS_PARITY.md").read_text(encoding="utf-8")

    for expected in [
        "docs/MACOS_MIGRATION_FINISH_PLAN.md",
        "Open Wisp Mac Logs.command",
        "build_logs/latest_macos_native_tests.txt",
        "docs/MACOS_LIVE_PARITY_CHECKLIST.md",
        "build_logs/latest_macos_package.txt",
    ]:
        assert expected in parity


def test_macos_readme_matches_current_settings_layout():
    readme = (REPO_ROOT / "macos" / "README.md").read_text(encoding="utf-8")
    settings_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "SettingsWiringTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "Settings window keeps provider/model routing in the `Models` tab",
        "stored API keys in the `Keys` tab",
        "provider authentication in the `Auth`",
        "`Provider Auth` shows ChatGPT/GitHub auth status",
        "`GitHub Copilot` keeps token save/test/clear controls",
        "`API Keys`",
        "shows API-key status, save, and clear actions",
    ]:
        assert expected in readme

    for stale in [
        "authentication and API keys in the `LLM` tab",
        "`Authentication` shows ChatGPT/GitHub/Copilot auth status",
    ]:
        assert stale not in readme

    assert '.tabItem { Text(\\"Models\\") }' in settings_test
    assert '.tabItem { Text(\\"Keys\\") }' in settings_test
    assert '.tabItem { Text(\\"Auth\\") }' in settings_test
    assert 'SettingsSection(\\"Provider Auth\\")' in settings_test
    assert 'SettingsSection(\\"GitHub Copilot\\")' in settings_test
    assert 'SettingsSection(\\"API Keys\\")' in settings_test


def test_finish_plan_documents_all_remaining_migration_gates():
    plan = (REPO_ROOT / "docs" / "MACOS_MIGRATION_FINISH_PLAN.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "macos" / "README.md").read_text(encoding="utf-8")

    for expected in [
        "Test Wisp (Mac Native).command",
        "Start Wisp (Mac Native).command",
        "bash scripts/run_macos_native_tests.command",
        "bash scripts/macos_phase1_validate.sh --open",
        "native-app-launch.log",
        "brain_python",
        "brain_python_configured",
        "docs/MACOS_LIVE_PARITY_CHECKLIST.md",
        "live-parity-checklist.md",
        "Open Wisp Mac Logs.command",
        "WISP_PYTHON_RUNTIME_DIR",
        "WISP_BUNDLE_IDENTIFIER",
        "WISP_CODESIGN_IDENTITY",
        "scripts/macos_package_release.sh",
        "Developer ID",
        "notarization",
        "WISP_VALIDATE_APP_LAUNCH=1",
        "build_logs/latest_macos_native_tests.txt",
        "build_logs/latest_macos_phase1.txt",
        "build_logs/latest_macos_package.txt",
        "Accessibility",
        "Screen Recording",
        "Microphone",
        "Launch at Login",
        "Settings API-key management",
        "ChatGPT browser sign-in",
        "GitHub device sign-in",
        "Copilot token save/test/clear",
        "Reset All",
    ]:
        assert expected in plan

    assert "docs/MACOS_MIGRATION_FINISH_PLAN.md" in readme


def test_native_permission_and_launch_login_sources_are_guarded():
    permissions_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "PermissionsAndLoginSourceTests.swift"
    ).read_text(encoding="utf-8")
    parity = (REPO_ROOT / "docs" / "MACOS_PARITY.md").read_text(encoding="utf-8")

    for expected in [
        "Implemented, pending live release validation",
        "Implemented, pending live TCC validation",
        "Implemented, pending live System Settings validation",
    ]:
        assert expected in parity

    for expected in [
        "PermissionsPanelView",
        "NativePermissionKind.allCases",
        "AXIsProcessTrustedWithOptions",
        "CGPreflightScreenCaptureAccess",
        "CGRequestScreenCaptureAccess",
        "AVCaptureDevice.requestAccess",
        "Privacy_Accessibility",
        "Privacy_ScreenCapture",
        "Privacy_Microphone",
        "SMAppService.mainApp.status",
        "SMAppService.mainApp.register()",
        "SMAppService.mainApp.unregister()",
        "LoginItemController.toggle()",
        "setLoginItemStatus(_ status: LoginItemStatus)",
    ]:
        assert expected in permissions_test


def test_phase1_dev_bundle_writes_launch_environment_resource():
    script = (REPO_ROOT / "scripts" / "macos_phase1_validate.sh").read_text(encoding="utf-8")
    locator = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Bridge" / "BrainLocator.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        'cat > "$resources_dir/dev-launch.env"',
        "WISP_REPO_ROOT=$REPO_ROOT",
        "WISP_BRAIN_PYTHON=$BRAIN_PY",
        "WISP_BRAIN_DIR=$REPO_ROOT/macos/brain",
        "WISP_RUN_LOG_DIR=$LOG_DIR",
        'WISP_APP_BUNDLE_ID="${WISP_APP_BUNDLE_ID:-dev.wisp.native}"',
        "plist_escape",
        'plist_bundle_id="$(plist_escape "$WISP_APP_BUNDLE_ID")"',
        "<string>$plist_bundle_id</string>",
        "<string>$plist_name</string>",
        "bundle_id=$WISP_APP_BUNDLE_ID",
        "dev_launch_env=$resources_dir/dev-launch.env",
        "validate_dev_launch_env",
        "Dev launch env validated:",
        "quit_existing_wisp_app",
        'for bundle_id in "$WISP_APP_BUNDLE_ID" "dev.wisp.native" "com.wisp.native"',
        'tell application id \\"$bundle_id\\" to quit',
        '"dev.wisp.native" "com.wisp.native"',
        "/usr/bin/pkill -x Wisp",
        "No embedded runtime present; marker must resolve brain_python to: $BRAIN_PY",
        "FAILED: native app resolved unexpected Python.",
        "FAILED: native app resolved Python path does not exist.",
        "FAILED: native app resolved Python path is not executable.",
        "FAILED: native app brain directory does not exist.",
        "FAILED: native app still resolved or configured bare python.",
    ]:
        assert expected in script

    for expected in [
        "devLaunchEnvironment(resourceURL:",
        'resourceURL.appendingPathComponent("dev-launch.env")',
        'devLaunch["WISP_BRAIN_PYTHON"]',
        'devLaunch["WISP_BRAIN_DIR"]',
        'devLaunch["WISP_REPO_ROOT"]',
    ]:
        assert expected in locator


def test_brain_client_never_spawns_bare_python_when_repo_venv_is_available():
    client = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Bridge" / "BrainClient.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "func resolvedPythonExecutable",
        "let pythonExecutable = config.resolvedPythonExecutable()",
        "proc.executableURL = pythonExecutable",
        "func normalized",
        "self.config = config.normalized()",
        '".venv/bin/python"',
        '".venv/bin/python3"',
        'URL(fileURLWithPath: "/usr/bin/python3")',
        "return systemPython",
        "FileManager.default.isExecutableFile(atPath: pythonExecutable.path)",
        "Python runtime is not executable or was not found",
        "Start Wisp (Mac Native).command",
        "WISP_PYTHON_RUNTIME_DIR",
        "extraPythonPath",
        "Attempted python:",
        "Configured python:",
        "Brain dir:",
    ]:
        assert expected in client

    resolver_tail = client.split('let systemPython = URL(fileURLWithPath: "/usr/bin/python3")', 1)[1]
    assert "return pythonExecutable" not in resolver_tail.split("func normalized", 1)[0]


def test_native_launch_marker_records_resolved_and_configured_python():
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    diagnostics = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "NativeLaunchDiagnostics.swift"
    ).read_text(encoding="utf-8")
    diagnostics_tests = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "NativeLaunchDiagnosticsTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "let resolvedPython = brainConfig.resolvedPythonExecutable()",
        "process_id=\\(ProcessInfo.processInfo.processIdentifier)",
        "bundle_identifier=\\(Bundle.main.bundleIdentifier ?? \"\")",
        "bundle_path=\\(Bundle.main.bundleURL.path)",
        "executable_path=\\(Bundle.main.executableURL?.path ?? \"\")",
        "brain_python=\\(resolvedPython.path)",
        "brain_python_exists=\\(fileManager.fileExists(atPath: resolvedPython.path))",
        "brain_python_is_executable=\\(fileManager.isExecutableFile(atPath: resolvedPython.path))",
        "brain_python_configured=\\(configuredPython.path)",
        "brain_python_configured_exists=\\(fileManager.fileExists(atPath: configuredPython.path))",
        "brain_dir_exists=\\(fileManager.fileExists(atPath: brainConfig.brainDirectory.path))",
    ]:
        assert expected in diagnostics

    assert "let brainConfig = BrainLocator.resolve()" in app_delegate
    assert "NativeLaunchDiagnostics.writeStartupRecord(config: appConfig, brainConfig: brainConfig)" in app_delegate
    assert "let client = BrainClient(config: brainConfig)" in app_delegate
    assert "let brainConfig = BrainLocator.resolve().normalized()" not in app_delegate
    assert "testStartupRecordShowsResolvedVirtualenvPythonForMissingConfiguredPython" in diagnostics_tests


def test_agent_task_inputs_use_readable_adaptive_system_colors():
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "AgentsUI" / "AgentTaskPanel.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "AgentTaskPanelTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "private enum AgentTaskPalette",
        "NSColor.textColor",
        "NSColor.textBackgroundColor",
        ".foregroundStyle(AgentTaskPalette.inputText)",
        ".tint(AgentTaskPalette.inputText)",
        ".background(AgentTaskPalette.inputBackground)",
        "textView.textColor = .textColor",
        "textView.backgroundColor = .textBackgroundColor",
    ]:
        assert expected in panel
        assert expected in swift_test

    assert "textView.textColor = .labelColor" not in panel


def test_settings_inputs_use_readable_adaptive_system_colors():
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "SettingsUI" / "SettingsPanel.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "SettingsWiringTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "private enum SettingsInputPalette",
        "NSColor.textColor",
        "NSColor.textBackgroundColor",
        ".foregroundStyle(SettingsInputPalette.inputText)",
        ".tint(SettingsInputPalette.inputText)",
        ".scrollContentBackground(.hidden)",
        ".background(SettingsInputPalette.inputBackground)",
    ]:
        assert expected in panel
        assert expected in swift_test

    for expected in [
        'SecureField("New API key", text: $secret.value)',
        'SecureField("GitHub Copilot token", text: $model.copilotToken)',
    ]:
        assert expected in panel
        assert expected.replace('"', '\\"') in swift_test


def test_native_settings_exposes_shared_llm_fallback_routes():
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "SettingsUI" / "SettingsPanel.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    handlers = (
        REPO_ROOT / "macos" / "brain" / "wisp_brain" / "handlers.py"
    ).read_text(encoding="utf-8")
    handler_test = (
        REPO_ROOT / "macos" / "brain" / "tests" / "test_handler_config.py"
    ).read_text(encoding="utf-8")
    settings_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "SettingsWiringTests.swift"
    ).read_text(encoding="utf-8")
    config_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "WispConfigTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "var llmFallbacks: String",
        "var visionFallbacks: String",
        "var memoryFallbacks: String",
        'llmFallbacks: values["LLM_FALLBACKS"] ?? ""',
        'visionFallbacks: values["VISION_LLM_FALLBACKS"] ?? ""',
        'memoryFallbacks: values["MEMORY_LLM_FALLBACKS"] ?? ""',
        '"LLM_FALLBACKS": llmFallbacks',
        '"VISION_LLM_FALLBACKS": visionFallbacks',
        '"MEMORY_LLM_FALLBACKS": memoryFallbacks',
        'SettingsTextField("Fallbacks", text: $model.draft.llmFallbacks)',
        'SettingsTextField("Fallbacks", text: $model.draft.visionFallbacks)',
        'SettingsTextField("Fallbacks", text: $model.draft.memoryFallbacks)',
        "llmTestRow(.main)",
        "llmTestRow(.vision)",
        "llmTestRow(.memory)",
        "func fallbacks(in draft: SettingsDraft) -> String",
        "return draft.llmFallbacks",
        "return draft.visionFallbacks",
        "return draft.memoryFallbacks",
    ]:
        assert expected in panel

    for expected in [
        'SettingsTextField("Fallbacks", text: $model.draft.llmFallbacks)',
        'SettingsTextField("Fallbacks", text: $model.draft.visionFallbacks)',
        'SettingsTextField("Fallbacks", text: $model.draft.memoryFallbacks)',
        "llmTestRow(.main)",
        "llmTestRow(.vision)",
        "llmTestRow(.memory)",
        "func fallbacks(in draft: SettingsDraft) -> String",
        "return draft.llmFallbacks",
        "return draft.visionFallbacks",
        "return draft.memoryFallbacks",
    ]:
        assert expected.replace('"', '\\"') in settings_test

    assert '"fallbacks": route.fallbacks(in: draft)' in app_delegate
    assert '\\"fallbacks\\": route.fallbacks(in: draft)' in settings_test

    for expected in [
        '"LLM_FALLBACKS": "anthropic:claude-sonnet-4-5\\ngroq:llama-3.3-70b-versatile"',
        '"VISION_LLM_FALLBACKS": "openai:gpt-4.1"',
        '"MEMORY_LLM_FALLBACKS": "openai:gpt-4.1-mini"',
        'draft.llmFallbacks = "openai:gpt-4.1\\ngroq:llama-3.3-70b-versatile"',
        'draft.visionFallbacks = "openai:gpt-4.1"',
        'draft.memoryFallbacks = "anthropic:claude-haiku-4-5"',
        'XCTAssertEqual(values["LLM_FALLBACKS"], "openai:gpt-4.1\\ngroq:llama-3.3-70b-versatile")',
        'XCTAssertEqual(values["VISION_LLM_FALLBACKS"], "openai:gpt-4.1")',
        'XCTAssertEqual(values["MEMORY_LLM_FALLBACKS"], "anthropic:claude-haiku-4-5")',
    ]:
        assert expected in config_test

    for expected in [
        "fallbacks: str = \"\"",
        "route_candidates(selected_provider, selected_model, fallbacks)",
        "custom_base_url=_custom_base_url_for_route(route_provider, custom_base_url)",
        "def _route_test_label(index: int) -> str:",
        "def _short_route_test_message(message: str, route_name: str) -> str:",
        "def _custom_base_url_for_route(provider: str, custom_base_url: str) -> str | None:",
        "if provider.strip().lower() != \"custom\":",
        '"routes": _route_payloads(routes)',
        '"message": f"{label} route chain {status}:\\n" + "\\n".join(lines)',
    ]:
        assert expected in handlers

    for expected in [
        "test_llm_test_offline_reports_fallback_chain",
        "test_llm_test_forwards_fallback_chain_to_client",
        "test_llm_test_scopes_custom_base_url_to_custom_provider",
        'fallbacks="anthropic:claude-sonnet-4-5\\ngroq:llama-3.3-70b-versatile"',
        'fallbacks="openai:gpt-4.1\\nanthropic:claude-sonnet-4-5"',
        '"Fallback 2 - groq / llama-3.3-70b-versatile: no key"',
        '("custom", "custom-model", "LLM", False, "https://api.example.test/v1")',
        '("openai", "gpt-4.1", "LLM", False, None)',
    ]:
        assert expected in handler_test


def test_prompt_panel_input_and_response_use_readable_adaptive_system_colors():
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Input" / "PromptPanel.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "PromptPanelTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "private enum PromptPanelPalette",
        "NSColor.textColor",
        ".foregroundStyle(PromptPanelPalette.inputText)",
        ".tint(PromptPanelPalette.inputText)",
    ]:
        assert expected in panel
        assert expected in swift_test


def test_memory_inputs_use_readable_adaptive_system_colors():
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "MemoryUI" / "MemoryPanel.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "MemoryPanelTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "private enum MemoryInputPalette",
        "NSColor.textColor",
        "NSColor.textBackgroundColor",
        ".foregroundStyle(MemoryInputPalette.inputText)",
        ".tint(MemoryInputPalette.inputText)",
    ]:
        assert expected in panel
        assert expected in swift_test


def test_plugin_manager_exposes_native_plugin_contract():
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "PluginsUI" / "PluginManagerPanel.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "PluginManagerPanelTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "struct PluginSummary",
        "var hooks: [String]",
        "var trayActions: [String]",
        "var tools: [String]",
        'payload["tray_actions"]',
        'PluginTagLine(title: "Hooks"',
        'PluginTagLine(title: "Tools"',
        "ForEach(plugin.trayActions",
        "onRunAction(action)",
        "model.openFolder(plugin.path)",
        "model.refresh()",
    ]:
        assert expected in panel
        assert expected.replace('"', '\\"') in swift_test

    for expected in [
        "pluginPanel = PluginManagerPanel",
        "brain.plugins.list",
        "brain.plugins.run_action",
        "showNativePluginManager()",
        "pluginPanel?.setPlugins",
        "pluginPanel?.fail",
    ]:
        assert expected in app_delegate
        assert expected in swift_test


def test_native_plugin_contract_keeps_plugins_python_only_and_swift_generic():
    overview = (REPO_ROOT / "docs" / "OVERVIEW.md").read_text(encoding="utf-8")
    parity = (REPO_ROOT / "docs" / "MACOS_PARITY.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "macos" / "README.md").read_text(encoding="utf-8")
    plugin_manager = (REPO_ROOT / "core" / "plugin_manager.py").read_text(encoding="utf-8")
    handlers = (
        REPO_ROOT / "macos" / "brain" / "wisp_brain" / "handlers.py"
    ).read_text(encoding="utf-8")
    panel = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "PluginsUI" / "PluginManagerPanel.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "PluginManagerPanelTests.swift"
    ).read_text(encoding="utf-8")
    overview_text = " ".join(overview.split())
    parity_text = " ".join(parity.split())
    readme_text = " ".join(readme.split())

    for expected in [
        "Plugin authors should not write Swift",
        "Third-party plugins belong in the shared Python plugin/runtime layer",
        "plugins/<name>/__init__.py",
        "brain.plugins.list",
        "brain.plugins.run_action",
        "Plugin-specific business logic, provider calls, hooks, tools",
    ]:
        assert expected in overview_text

    for expected in [
        "Plugins are shared Python/runtime extensions, not Swift extensions",
        "Plugin authors should implement hooks, tray actions, and tools once",
        "Swift must stay a generic metadata/action host",
    ]:
        assert expected in parity_text

    for expected in [
        "Plugins remain shared Python/runtime extensions",
        "Plugin authors should not write Swift for macOS support",
        "The native Swift plugin manager reads generic metadata from `brain.plugins.list`",
        "runs declared tray actions through `brain.plugins.run_action`",
        "must not contain plugin-specific implementation logic",
    ]:
        assert expected in readme_text

    for expected in [
        "Mods are Python packages under plugins/<name>/__init__.py",
        "get_tray_actions() -> list[dict]",
        "get_tools() -> list[dict]",
        "PluginManager",
    ]:
        assert expected in plugin_manager

    for expected in [
        '@handler("brain.plugins.list")',
        '@handler("brain.plugins.run_action")',
        "_loaded_plugin_manager(plugins_dir)",
        'importlib.import_module("core.plugin_manager")',
        "plugin_manager.init(plugins_dir)",
        "get_tray_actions",
        "get_tools",
        '"tray_actions"',
        '"tools"',
        '"hooks"',
    ]:
        assert expected in handlers

    for expected in [
        "test_plugins_list_initializes_shared_manager_and_action_can_run",
        "Do Native Thing",
        "brain.plugins.run_action",
        "marker.read_text",
    ]:
        assert expected in (
            REPO_ROOT / "macos" / "brain" / "tests" / "test_handler_plugins.py"
        ).read_text(encoding="utf-8")

    for expected in [
        "struct PluginSummary",
        'payload["hooks"]',
        'payload["tray_actions"]',
        'payload["tools"]',
        "ForEach(plugin.trayActions",
        "onRunAction(plugin, label)",
    ]:
        assert expected in panel
        assert expected.replace('"', '\\"') in swift_test

    assert "import PythonKit" not in panel
    assert "import PythonKit" not in app_delegate


def test_native_query_runs_shared_plugin_lifecycle_hooks():
    handlers = (
        REPO_ROOT / "macos" / "brain" / "wisp_brain" / "handlers.py"
    ).read_text(encoding="utf-8")
    handler_test = (
        REPO_ROOT / "macos" / "brain" / "tests" / "test_handler_query.py"
    ).read_text(encoding="utf-8")

    for expected in [
        "built = _apply_plugin_before_query(built)",
        "_notify_plugin_after_response(full)",
        "def _apply_plugin_before_query(built: Any) -> Any:",
        ".before_query(",
        "def _notify_plugin_after_response(text: str) -> None:",
        ".after_response(text)",
        "plugin before_query skipped",
        "plugin after_response skipped",
    ]:
        assert expected in handlers

    for expected in [
        "test_query_runs_shared_plugin_before_and_after_hooks",
        "FakeManager",
        "before_query",
        "after_response",
        "original prompt + plugin prompt",
        "plugin context",
    ]:
        assert expected in handler_test


def test_agent_history_and_diff_expose_native_run_actions():
    history = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "AgentsUI" / "AgentHistoryPanel.swift"
    ).read_text(encoding="utf-8")
    diff = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "AgentsUI" / "AgentDiffPanel.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "AgentHistoryPanelTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "onRetryRun",
        "onContinueRun",
        "onOpenDiff",
        "retrySelectedRun()",
        "continueSelectedRun()",
        "openDiff()",
        'Text("Final").tag("final")',
        'Text("Log").tag("log")',
        'Text("Task").tag("task")',
        'Text("Diff").tag("diff")',
        "detail.hasDisplayableDiff",
    ]:
        assert expected in history
        assert expected.replace('"', '\\"') in swift_test

    for expected in [
        "final class AgentDiffPanel",
        "func showDiff(title: String, runDir: String, diffPatch: String)",
        "DiffTextScrollView",
        "model.openFolder()",
    ]:
        assert expected in diff
        assert expected in swift_test

    for expected in [
        "brain.agent.history.list",
        "brain.agent.history.read",
        "brain.agent.history.retry_spec",
        "brain.agent.history.continue_spec",
        "showAgentDiff(detail)",
        "agentTaskPanel?.showTask",
    ]:
        assert expected in app_delegate
        assert expected in swift_test


def test_overlay_menu_exposes_live_parity_utilities():
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    overlay_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "OverlayContextMenuTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        'addItem("Snip Screen Region", #selector(overlayMenuSnip))',
        'addItem("Open Run Logs", #selector(overlayMenuOpenLogs))',
        'addItem("Open Config Folder", #selector(overlayMenuOpenConfigFolder))',
        "@objc private func overlayMenuSnip()",
        "@objc private func overlayMenuOpenLogs()",
        "@objc private func overlayMenuOpenConfigFolder()",
        "startSnip()",
        "openRunLogs()",
        "openConfigFolder()",
    ]:
        assert expected in app_delegate

    for expected in [
        'addItem(\\"Snip Screen Region\\"',
        'addItem(\\"Open Run Logs\\"',
        'addItem(\\"Open Config Folder\\"',
    ]:
        assert expected in overlay_test

    for diagnostic in [
        'addItem(\\"Run Echo Smoke\\"',
        'addItem(\\"Context Snapshot\\"',
        'addItem(\\"Capture Screen Smoke\\"',
    ]:
        overlay_section = overlay_test.split("func testStatusMenuKeepsPythonCoreOrderBeforeMacUtilities()", 1)[0]
        assert diagnostic not in overlay_section


def test_status_item_uses_native_template_icon_with_ascii_fallback():
    status = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Tray" / "StatusItem.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "OverlayContextMenuTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        "private func configureStatusButton()",
        "image.isTemplate = true",
        "button.image = image",
    ]:
        assert expected in status
        assert expected in swift_test

    assert 'NSImage(systemSymbolName: "sparkles", accessibilityDescription: "Wisp")' in status
    assert 'NSImage(systemSymbolName: \\"sparkles\\", accessibilityDescription: \\"Wisp\\")' in swift_test
    assert 'button.title = ""' in status
    assert 'button.title = \\"\\"' in swift_test
    assert 'button.title = "W"' in status
    assert 'button.title = \\"W\\"' in swift_test

    assert 'statusItem.button?.title = "✦"' not in status


def test_status_menu_exposes_native_diagnostic_smoke_actions():
    status = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Tray" / "StatusItem.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "OverlayContextMenuTests.swift"
    ).read_text(encoding="utf-8")

    for expected in [
        'addItem("Run Echo Smoke", action: #selector(runEchoSmoke), keyEquivalent: "e")',
        'addItem("Context Snapshot", action: #selector(showContext), keyEquivalent: "c")',
        'addItem("Capture Screen Smoke", action: #selector(captureScreen), keyEquivalent: "s")',
        "@objc private func runEchoSmoke()",
        "@objc private func showContext()",
        "@objc private func captureScreen()",
    ]:
        assert expected in status

    for expected in [
        'addItem(\\"Run Echo Smoke\\"',
        'addItem(\\"Context Snapshot\\"',
        'addItem(\\"Capture Screen Smoke\\"',
    ]:
        assert expected in swift_test


def test_overlay_behavior_and_response_bubble_workflow_is_guarded():
    overlay = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Overlay" / "OverlayPanel.swift"
    ).read_text(encoding="utf-8")
    bubble = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Overlay" / "ResponseBubblePanel.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "OverlayBehaviorTests.swift"
    ).read_text(encoding="utf-8")

    def swift_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    for expected in [
        "enum DollState: Hashable { case idle, listening, thinking, speaking }",
        "styleMask: [.nonactivatingPanel, .borderless]",
        "level = .floating",
        "backgroundColor = .clear",
        "isOpaque = false",
        "collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]",
        "isMovableByWindowBackground = true",
        "contentView = OverlayHostingView(rootView: OverlayView(model: model), onRightClick: onRightClick)",
        "override func rightMouseDown(with event: NSEvent)",
        "func showAtLaunch()",
        "func setState(_ state: DollState)",
        "if state != .speaking",
        "model.amplitude = 0",
        "case .idle:",
        "scheduleAutoHide()",
        "case .listening, .thinking, .speaking:",
        "orderFrontRegardless()",
        "func setSpeechAmplitude(_ amplitude: Double)",
        "model.amplitude = max(0, min(1, amplitude))",
        "func toggleVisibility()",
        "ICON_AUTO_HIDE",
        "DOLL_AUTO_HIDE",
        "ICON_BACKSTOP_MS",
        "DOLL_ICON_BACKSTOP_MS",
        "let iconSize: CGFloat",
        "private let images: [OverlayPanel.DollState: NSImage]",
        "self.images = DollAssetLocator.loadImages()",
        "func image(for state: OverlayPanel.DollState) -> NSImage?",
        'let raw = values["ICON_SIZE"] ?? values["DOLL_SIZE"] ?? "80"',
        "return CGFloat(max(32, min(160, parsed)))",
        "case .idle:      return .gray",
        "case .listening: return .blue",
        "case .thinking:  return .yellow",
        "case .speaking:  return .green",
        "guard model.state == .speaking else { return 1.0 }",
        "return 1.0 + CGFloat(model.amplitude) * 0.10",
        "guard model.state == .speaking else { return 6 }",
        "return 6 + CGFloat(model.amplitude) * 8",
        "Image(nsImage: image)",
        "Circle()",
        ".animation(.easeInOut(duration: 0.25), value: model.state)",
        ".animation(.easeOut(duration: 0.08), value: model.amplitude)",
        '.idle: "idle.png"',
        '.listening: "listening.png"',
        '.thinking: "thinking.png"',
        '.speaking: "speaking.png"',
        'resourceURL.appendingPathComponent("assets/doll")',
        'resourceURL.appendingPathComponent("doll")',
        'ProcessInfo.processInfo.environment["WISP_REPO_ROOT"]',
        '.appendingPathComponent("../assets/doll")',
    ]:
        assert expected in overlay
        assert swift_literal(expected) in swift_test

    for expected in [
        "final class ResponseBubblePanel: NSPanel",
        "styleMask: [.nonactivatingPanel, .borderless]",
        "collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]",
        "func startThinking(anchor: NSRect?)",
        "model.mode = .thinking",
        "model.dotCount = 1",
        "startDots()",
        "func showListening(anchor: NSRect?)",
        'model.setInstantText("Recording - release to send")',
        "func appendChunk(_ chunk: String)",
        "guard !chunk.isEmpty else { return }",
        "model.mode = .reply",
        "model.appendChunk(chunk)",
        "startRevealIfNeeded()",
        "func setText(_ text: String)",
        "model.replaceBufferedText(text)",
        "func showNotice(_ text: String, anchor: NSRect?, timeout: TimeInterval = 6.0)",
        "model.mode = .notice",
        "scheduleHide(after: timeout)",
        "func finish()",
        "No reply from model. Check model name or API key in Settings.",
        "if model.hasUnrevealedWords",
        "model.isFinishing = true",
        "scheduleHide(after: hideDelay())",
        "x: anchor.minX - frame.width - margin",
        "NSScreen.main?.visibleFrame",
        "Timer.scheduledTimer(withTimeInterval: 0.45",
        "Timer.scheduledTimer(withTimeInterval: revealInterval()",
        "BUBBLE_REVEAL_WPM",
        "BUBBLE_HIDE_DELAY_MS",
        "final class ResponseBubbleModel: ObservableObject",
        "enum Mode",
        "case hidden",
        "case thinking",
        "case listening",
        "case reply",
        "case notice",
        "var displayText: String",
        'return String(repeating: ".", count: dotCount)',
        "var hasUnrevealedWords: Bool",
        "fullText.split(whereSeparator: { $0.isWhitespace }).map(String.init)",
        'guard !revealed.isEmpty else { return fullText.isEmpty ? [] : [" "] }',
        "return Array(revealed.suffix(54))",
        "func setInstantText(_ text: String)",
        "revealedCount = words.count",
        "func replaceBufferedText(_ text: String)",
        "revealedCount = min(previousCount, words.count)",
        "func revealNextWord()",
        "let highlight = Color(nsColor: model.config.readWordColor)",
        "index == words.count - 1 ? highlight : normal",
        "BubbleTail()",
    ]:
        assert expected in bubble
        assert swift_literal(expected) in swift_test


def test_snip_capture_native_workflow_is_guarded():
    capture = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Capture" / "ScreenCaptureController.swift"
    ).read_text(encoding="utf-8")
    overlay = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Capture" / "SnipOverlayPanel.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "SnipCaptureTests.swift"
    ).read_text(encoding="utf-8")

    def swift_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    for expected in [
        "CGPreflightScreenCaptureAccess()",
        "CGRequestScreenCaptureAccess()",
        "CGDisplayCreateImage(displayID)",
        "CGWindowListCreateImage(",
        ".optionOnScreenOnly",
        "kCGNullWindowID",
        "[.bestResolution]",
        "normalized.width > 4, normalized.height > 4",
        'outputURL(prefix: "screen-snip")',
        "NSBitmapImageRep(cgImage: image)",
        "rep.representation(using: .png, properties: [:])",
        "RunLogLocator.writableLogDirectory()",
        '\\(prefix)-\\(stamp).png',
    ]:
        assert expected in capture
        assert swift_literal(expected) in swift_test

    for expected in [
        "struct SnipSelection",
        "var captureRect: CGRect",
        "level = .screenSaver",
        "collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]",
        "backgroundColor = .clear",
        "override var canBecomeKey: Bool { true }",
        "makeFirstResponder(snipView)",
        "NSScreen.screens",
        "frame = frame.union(screen.frame)",
        "addCursorRect(bounds, cursor: .crosshair)",
        "NSColor.black.withAlphaComponent(0.45).setFill()",
        "Click and drag to select a region  |  ESC to cancel",
        "screenFrame.minX + selectionRect.minX",
        "screenFrame.maxY - selectionRect.maxY",
        "onSelection(SnipSelection(captureRect: captureRect))",
        "finishCancelled()",
    ]:
        assert expected in overlay
        assert swift_literal(expected) in swift_test

    for expected in [
        "private struct PendingSnipContext",
        "var screenshotB64: String",
        "private var snipPanel: SnipOverlayPanel?",
        "private let screenCapture = ScreenCaptureController()",
        "private var pendingSnip: PendingSnipContext?",
        "snipPanel = SnipOverlayPanel(",
        "handleSnipSelection(selection)",
        "cancelSnip()",
        "case .snip:",
        "startSnip()",
        "let result = try screenCapture.captureRegion(selection.captureRect, promptForPermission: true)",
        "let data = try Data(contentsOf: result.url)",
        "pendingSnip = PendingSnipContext(",
        "screenshotB64: data.base64EncodedString()",
        'ambientText: snip.contextAmbient ? snapshot.ambientText(includeClipboard: false) : ""',
        "useTools: snip.contextTools",
        "capturePath: result.url.path",
        "intentPanel?.show(caller: caller)",
        'label: "Screen Snip"',
        "contextScreenshot: .off",
        "if let snip = pendingSnip",
        '"screenshot_b64": snip.screenshotB64',
        '"use_tools": snip.useTools',
        '"allow_screenshot_tool": false',
        "Screen snip saved: \\(snip.capturePath)",
    ]:
        assert expected in app_delegate
        assert swift_literal(expected) in swift_test


def test_chat_intent_and_hotkey_native_workflow_is_guarded():
    chat = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Chat" / "ChatPanel.swift"
    ).read_text(encoding="utf-8")
    intent = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Input" / "IntentPanel.swift"
    ).read_text(encoding="utf-8")
    hotkey = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Input" / "HotkeyController.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "ChatIntentFlowTests.swift"
    ).read_text(encoding="utf-8")

    def swift_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    for expected in [
        "final class ChatPanel: NSPanel",
        "func showChat(startNew: Bool = false, autoMessage: String? = nil)",
        "model.sendAutoMessage(autoMessage)",
        "func hasConversationHistory() -> Bool",
        "func recordExchange(user: String, assistant: String)",
        "func beginUserMessage(_ text: String) -> [[String: String]]",
        "func appendAssistantChunk(_ chunk: String)",
        "func finishAssistant(_ finalText: String? = nil)",
        "func failAssistant(_ message: String)",
        "conversations.contains { $0.messages.isEmpty == false }",
        "guard !trimmed.isEmpty, !isStreaming, hasConversationHistory else { return }",
        'conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "user", content: text))',
        'conversation.messages.append(ChatTranscriptMessage(id: UUID(), role: "assistant", content: chunk))',
        'activeConversation.messages.map { ["role": $0.role, "content": $0.content] }',
    ]:
        assert expected in chat
        assert swift_literal(expected) in swift_test

    for expected in [
        "struct IntentSelection",
        "var caller: CallerConfig",
        "var intent: IntentConfig?",
        "var isCustom: Bool",
        "model.onRow = { [weak self] row in self?.choose(row) }",
        "model.onCustomSubmit = { [weak self] text in self?.submitCustom(text) }",
        "model.onCancel = { [weak self] in self?.cancel() }",
        "let rows = caller.intentRows()",
        'model.configure(title: caller.label.isEmpty ? "Wisp" : caller.label, rows: rows)',
        "if row.isCustom",
        "finish(prompt: row.prompt, intent: intent(for: row), isCustom: false)",
        "finish(prompt: trimmed, intent: nil, isCustom: true)",
        "NSEvent.addLocalMonitorForEvents(matching: .keyDown)",
        "if event.keyCode == 53",
        "event.charactersIgnoringModifiers?.lowercased()",
        "currentCaller.intents.first { $0.key.caseInsensitiveCompare(row.key) == .orderedSame }",
        "rows.append(",
        'label: "Custom prompt"',
        'key: customKey.isEmpty ? "s" : customKey',
    ]:
        assert expected in intent
        assert swift_literal(expected) in swift_test

    for expected in [
        "case caller(Int)",
        "case snip",
        "case addContext",
        "case clearContext",
        "case voiceStart",
        "case voiceStop",
        "definitions = callers.enumerated().compactMap",
        "HotkeyDefinition.parse(caller.hotkey, callerIndex: index, label: caller.label)",
        'HotkeyDefinition.parse($0.hotkey, action: .snip, label: "Snip")',
        'HotkeyDefinition.parse($0, action: .addContext, label: "Add context")',
        'HotkeyDefinition.parse($0, action: .clearContext, label: "Clear context")',
        'HotkeyDefinition.parse($0, action: .voiceStart, label: "Voice")',
        "AXIsProcessTrustedWithOptions(options)",
        "CGEvent.tapCreate(",
        "guard typeRawValue != CGEventType.keyDown.rawValue || !isRepeat else { return }",
        "if definition.action == .voiceStart",
        "onTrigger(.voiceStop)",
        "onTrigger(definition.action)",
    ]:
        assert expected in hotkey
        assert swift_literal(expected) in swift_test

    for expected in [
        "intentPanel = IntentPanel { [weak self] selection in",
        "Task { await self?.runIntent(selection) }",
        "chatPanel = ChatPanel { [weak self] text in",
        "Task { await self?.runChatMessage(text) }",
        "case .caller(let callerIndex):",
        "self?.showIntentPicker(callerIndex: callerIndex)",
        "pendingIntentContext = PendingNativeContext(",
        "intentPanel?.show(caller: caller)",
        "private func runIntent(_ selection: IntentSelection) async",
        "promptPanel?.setPrompt(selection.prompt)",
        "if selection.caller.pasteBack",
        "await runPasteBack(selection.prompt, context: pendingContext)",
        "await runPrompt(",
        "contextSnapshot: pendingContext?.snapshot",
        "let params = try await paramsForPrompt(text, mode: mode, caller: caller, contextSnapshot: contextSnapshot)",
        "private func paramsForPrompt(",
        ") async throws -> [String: Any]",
        "let willAttachScreenshot = mode == .queryScreen || policy.contextScreenshot == .auto",
        '"include_active_document": policy.contextDocuments && !willAttachScreenshot',
        "private func showNativeChat(new: Bool)",
        "let autoMessage = new ? nil : chatAutoElaboratePrompt()",
        "chatPanel?.showChat(startNew: new, autoMessage: autoMessage)",
        "CHAT_AUTO_ELABORATE",
        "CHAT_ELABORATE_PROMPT",
        "private func runChatMessage(_ text: String) async",
        'let messages = chatPanel?.beginUserMessage(text) ?? [["role": "user", "content": text]]',
        'client.stream("brain.chat", ["messages": messages])',
        "chatPanel?.appendAssistantChunk(chunk)",
        "chatPanel?.finishAssistant(finalText)",
        'chatPanel?.failAssistant("No reply from model. Check model name or API key in Settings.")',
        "chatPanel?.recordExchange(user: text, assistant: assembled)",
        "client.stream(mode.method, params)",
        "responseBubble?.appendChunk(chunk)",
        "promptPanel?.setResponse(assembled)",
        "responseBubble?.finish()",
    ]:
        assert expected in app_delegate
        assert swift_literal(expected) in swift_test


def test_query_active_document_context_is_guarded():
    handlers = (
        REPO_ROOT / "macos" / "brain" / "wisp_brain" / "handlers.py"
    ).read_text(encoding="utf-8")
    handler_test = (
        REPO_ROOT / "macos" / "brain" / "tests" / "test_handler_query.py"
    ).read_text(encoding="utf-8")

    for expected in [
        "include_active_document: bool = False",
        "active_document_text: str = \"\"",
        "active_document = brain_context_active_document().get(\"text\", \"\")",
        "active_document_text=active_document",
        '@handler("brain.context.active_document")',
        "read_active_document_for_context()",
        'text.startswith(("Could not", "File type", "Failed to"))',
    ]:
        assert expected in handlers

    for expected in [
        "test_query_includes_active_document_when_requested",
        "test_query_skips_active_document_when_screenshot_attached",
        "test_active_document_context_handler_filters_error_strings",
        "include_active_document=True",
        "\"[Active document]\\nACTIVE DOC TEXT\"",
        "active document should not be read for screenshot query",
    ]:
        assert expected in handler_test


def test_voice_and_tts_native_workflow_is_guarded():
    recorder = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Audio" / "AudioRecorder.swift"
    ).read_text(encoding="utf-8")
    player = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "Audio" / "AudioPlayer.swift"
    ).read_text(encoding="utf-8")
    app_delegate = (
        REPO_ROOT / "macos" / "Sources" / "Wisp" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    swift_test = (
        REPO_ROOT / "macos" / "Tests" / "WispTests" / "VoiceAndTTSTests.swift"
    ).read_text(encoding="utf-8")

    def swift_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    for expected in [
        "AVCaptureDevice.authorizationStatus(for: .audio)",
        "AVCaptureDevice.requestAccess(for: .audio)",
        "AVAudioEngine()",
        "input.installTap(onBus: 0",
        "AVAudioFile(forWriting: url, settings: settings)",
        "AVFormatIDKey: kAudioFormatLinearPCM",
        "RunLogLocator.writableLogDirectory()",
        'voice-\\(stamp).wav',
    ]:
        assert expected in recorder
        assert swift_literal(expected) in swift_test

    for expected in [
        "AVAudioPlayer(contentsOf: url)",
        "player.isMeteringEnabled = true",
        "startAmplitudeMeter(for: playbackID)",
        "Timer.scheduledTimer(withTimeInterval: 1.0 / 24.0",
        "player.updateMeters()",
        "Self.normalizedAmplitude(averagePower: player.averagePower(forChannel: 0))",
        "self.onAmplitude?(playbackID, amplitude)",
        "onFinish?(playbackID, flag)",
    ]:
        assert expected in player
        assert swift_literal(expected) in swift_test

    for expected in [
        "private let audioRecorder = AudioRecorder()",
        "private let audioPlayer = AudioPlayer()",
        "audioPlayer.onFinish = { [weak self] playbackID, success in",
        "audioPlayer.onAmplitude = { [weak self] playbackID, amplitude in",
        "startVoiceQuery()",
        "stopVoiceQuery()",
        "pendingVoiceContext = PendingNativeContext",
        "overlay?.setState(.listening)",
        "responseBubble?.showListening(anchor: overlay?.frame)",
        "let url = try await audioRecorder.start()",
        "let recording = try audioRecorder.stop()",
        "let transcript = try await transcribe(recording.url)",
        "await self.runPrompt(",
        "contextSnapshot: voiceContext?.snapshot",
        "speakLastResponse()",
        "let url = try await synthesizeSpeech(text)",
        "let playbackID = try audioPlayer.play(url: url)",
        "activeTTSPlaybackID = playbackID",
        "overlay?.setSpeechAmplitude(amplitude)",
        "overlay?.setSpeechAmplitude(0)",
        "brain.transcribe",
        "brain.tts.synthesize",
        "brain.tts.test",
    ]:
        assert expected in app_delegate
        assert swift_literal(expected) in swift_test


def test_package_launch_validation_checks_embedded_python_marker():
    script = (REPO_ROOT / "scripts" / "macos_package_release.sh").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "macos" / "README.md").read_text(encoding="utf-8")
    finish_plan = (REPO_ROOT / "docs" / "MACOS_MIGRATION_FINISH_PLAN.md").read_text(encoding="utf-8")

    for expected in [
        'PACKAGE_BUNDLE_IDENTIFIER="${WISP_BUNDLE_IDENTIFIER:-com.wisp.native}"',
        'WISP_APP_BUNDLE_ID="$PACKAGE_BUNDLE_IDENTIFIER"',
        'rm -f "$APP_BUNDLE/Contents/Resources/dev-launch.env"',
        "Removed dev launch environment from release bundle.",
        "validate_bundle_metadata",
        "release package must not use the dev bundle identifier dev.wisp.native",
        "open_signed_app_without_wisp_env",
        'unset WISP_BRAIN_PYTHON',
        'unset WISP_RUN_LOG_DIR',
        'launch_root="${TMPDIR:-/tmp}/wisp-signed-launch-$RUN_ID"',
        'launch_app="$launch_root/Wisp.app"',
        'expected_python="$launch_app/Contents/Resources/python-runtime/bin/python3"',
        "copy-signed-app-for-launch",
        "codesign-verify-launch-copy",
        'codesign --verify --deep --strict --verbose=2 "$launch_app"',
        "quit_existing_wisp_app",
        '"dev.wisp.native" "com.wisp.native"',
        "/usr/bin/pkill -x Wisp",
        "launch-validation app copy still contains dev-launch.env",
        'open_signed_app_without_wisp_env "$launch_app"',
        "This launch clears WISP_* dev/test variables before calling open.",
        'app_marker="$HOME/Library/Logs/Wisp/native-app-launch.log"',
        'if ! cp "$app_marker" "$marker"',
        "FAILED: could not copy signed app launch marker into package log folder.",
        "Copied launch marker to package log evidence:",
        'for bundle_id in "$PACKAGE_BUNDLE_IDENTIFIER" "dev.wisp.native" "com.wisp.native"',
        'tell application id \\"$bundle_id\\" to quit',
        "FAILED: signed app resolved unexpected Python.",
        "FAILED: signed app resolved Python path does not exist.",
        "FAILED: signed app resolved Python path is not executable.",
        "FAILED: signed app brain directory does not exist.",
        "Expected brain_python=$expected_python",
        "FAILED: signed app still resolved or configured bare python.",
        'grep -Fx "brain_python=$expected_python"',
    ]:
        assert expected in script

    for expected in [
        "brain_python_configured",
        "dev-launch.env",
        "rejects a launch",
        "removes",
        "verifies that copied app's code signature",
        "clears Wisp dev/test environment variables",
        "temporary folder outside the",
        "Contents/Resources/python-runtime/bin/python3",
    ]:
        assert expected in readme

    for expected in [
        "brain_python",
        "brain_python_configured",
        "~/Library/Logs/Wisp/native-app-launch.log",
        "signature-verified temporary app copy outside the checkout",
        "must verify and open a temporary app copy outside",
        "release launch must resolve to `Contents/Resources/python-runtime/bin/python3`",
        "must not ship `Contents/Resources/dev-launch.env`",
        "clear Wisp dev/test environment variables before `open`",
    ]:
        assert expected in finish_plan
