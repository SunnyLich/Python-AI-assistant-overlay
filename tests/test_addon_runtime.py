import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import addon_runtime


class AddonRuntimeProvisionTests(unittest.TestCase):
    def _seed_env(self, root: Path, deps: addon_runtime.AddonDependencies) -> Path:
        env_dir = root / "demo"
        py = addon_runtime.python_path(env_dir)
        py.parent.mkdir(parents=True)
        py.write_text("old python", encoding="utf-8")
        addon_runtime._write_marker(env_dir, deps)
        return env_dir

    def test_force_provision_without_builder_preserves_existing_environment(self) -> None:
        deps = addon_runtime.AddonDependencies(python=">=3.11", packages=["demo-pkg"])
        with tempfile.TemporaryDirectory() as tmp:
            envs_root = Path(tmp) / "addon_envs"
            with (
                mock.patch.object(addon_runtime, "ADDON_ENVS_DIR", envs_root),
                mock.patch.object(addon_runtime, "_find_uv", return_value=""),
                mock.patch.object(sys, "frozen", True, create=True),
            ):
                env_dir = self._seed_env(envs_root, deps)
                py = addon_runtime.python_path(env_dir)

                with self.assertRaisesRegex(RuntimeError, "uv is required"):
                    addon_runtime.provision_environment("demo", deps, force=True)

                self.assertEqual(py.read_text(encoding="utf-8"), "old python")
                self.assertTrue((env_dir / "addon-env.json").exists())
                self.assertFalse(env_dir.with_name("demo.rebuild-backup").exists())

    def test_force_provision_restores_existing_environment_when_rebuild_fails(self) -> None:
        deps = addon_runtime.AddonDependencies(python=">=3.11", packages=["demo-pkg"])
        with tempfile.TemporaryDirectory() as tmp:
            envs_root = Path(tmp) / "addon_envs"

            def fail_run(_cmd: list[str]) -> None:
                raise RuntimeError("network failed")

            with (
                mock.patch.object(addon_runtime, "ADDON_ENVS_DIR", envs_root),
                mock.patch.object(addon_runtime, "_find_uv", return_value="uv"),
                mock.patch.object(addon_runtime, "_run", fail_run),
            ):
                env_dir = self._seed_env(envs_root, deps)
                py = addon_runtime.python_path(env_dir)

                with self.assertRaisesRegex(RuntimeError, "network failed"):
                    addon_runtime.provision_environment("demo", deps, force=True)

                self.assertEqual(py.read_text(encoding="utf-8"), "old python")
                self.assertTrue((env_dir / "addon-env.json").exists())
                self.assertFalse(env_dir.with_name("demo.rebuild-backup").exists())

    def test_force_provision_discards_backup_after_successful_rebuild(self) -> None:
        deps = addon_runtime.AddonDependencies(python=">=3.11")
        with tempfile.TemporaryDirectory() as tmp:
            envs_root = Path(tmp) / "addon_envs"

            def fake_run(cmd: list[str]) -> None:
                env_dir = Path(cmd[-1])
                py = addon_runtime.python_path(env_dir)
                py.parent.mkdir(parents=True)
                py.write_text("new python", encoding="utf-8")

            with (
                mock.patch.object(addon_runtime, "ADDON_ENVS_DIR", envs_root),
                mock.patch.object(addon_runtime, "_find_uv", return_value="uv"),
                mock.patch.object(addon_runtime, "_run", fake_run),
            ):
                env_dir = self._seed_env(envs_root, deps)
                py = addon_runtime.python_path(env_dir)

                status = addon_runtime.provision_environment("demo", deps, force=True)

                self.assertTrue(status["ready"])
                self.assertEqual(py.read_text(encoding="utf-8"), "new python")
                self.assertTrue((env_dir / "addon-env.json").exists())
                self.assertFalse(env_dir.with_name("demo.rebuild-backup").exists())


if __name__ == "__main__":
    unittest.main()
