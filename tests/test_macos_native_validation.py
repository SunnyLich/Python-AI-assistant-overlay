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

    assert "latest_macos_*.txt" in helper
    assert "summary_log" in helper
    assert "live_parity_checklist" in helper
    assert "Latest pointer is stale" in helper
    assert "newest_log_dir_from_folders" in helper
    assert 'open "$log_dir"' in helper


def test_live_parity_checklist_tracks_current_manual_gates():
    checklist = (REPO_ROOT / "docs" / "MACOS_LIVE_PARITY_CHECKLIST.md").read_text(encoding="utf-8")

    for expected in [
        "Overlay right-click opens the native context menu.",
        "WASD/caller intent overlay is readable in light mode and dark mode.",
        "Response bubble reply/listening/notice text is readable.",
        "Chat window is readable in light mode and dark mode.",
        "Settings > Keys can refresh, save, and clear API-key status.",
        "Settings > Auth can refresh provider status.",
        "Reset All clears credentials/.env only after confirmation and reloads UI.",
        "Launch at Login toggle updates System Settings state.",
        "`--open` launch writes `native-app-launch.log`.",
        "Signed app launches with `WISP_VALIDATE_APP_LAUNCH=1`.",
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


def test_finish_plan_documents_all_remaining_migration_gates():
    plan = (REPO_ROOT / "docs" / "MACOS_MIGRATION_FINISH_PLAN.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "macos" / "README.md").read_text(encoding="utf-8")

    for expected in [
        "Test Wisp (Mac Native).command",
        "bash scripts/run_macos_native_tests.command",
        "bash scripts/macos_phase1_validate.sh --open",
        "native-app-launch.log",
        "docs/MACOS_LIVE_PARITY_CHECKLIST.md",
        "live-parity-checklist.md",
        "Open Wisp Mac Logs.command",
        "WISP_PYTHON_RUNTIME_DIR",
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
