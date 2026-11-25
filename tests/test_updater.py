from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import whimbox_updater as updater  # noqa: E402


class UpdaterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="whimbox-updater-test-")
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.data = self.root / "data"
        self.app.mkdir()
        self.data.mkdir()
        self._globals = {
            name: getattr(updater, name)
            for name in (
                "APP_DIR", "PY_EXE", "SITE_PACKAGES", "ASAR", "SCRIPTS_DIR",
                "DOWNLOAD_DIR", "DATA_DIR", "BACKUP_DIR", "LEGACY_BACKUP_DIR", "LOG_DIR",
                "WATCHLIST_FILE", "STATE_FILE", "RELEASE_CACHE", "SCRIPTS_STATE",
                "LOG_DIR_GLOB", "SCRIPT_DIR", "QUIET", "_LOG_PATH",
            )
            if hasattr(updater, name)
        }
        updater.configure_paths(app_dir=str(self.app), data_dir=str(self.data))
        updater.LEGACY_BACKUP_DIR = str(self.data / "legacy-backups")
        Path(updater.LEGACY_BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        self._make_app_layout()
        Path(updater.SITE_PACKAGES).mkdir(parents=True, exist_ok=True)
        Path(updater.SCRIPTS_DIR).mkdir(parents=True, exist_ok=True)
        Path(updater.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        Path(updater.LOG_DIR).mkdir(parents=True, exist_ok=True)
        updater.QUIET = True
        updater._LOG_PATH = None

    def tearDown(self):
        for name, value in self._globals.items():
            setattr(updater, name, value)
        self.temp.cleanup()

    def _make_app_layout(self):
        py = Path(updater.PY_EXE)
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"mock embedded python")
        asar = Path(updater.ASAR)
        asar.parent.mkdir(parents=True, exist_ok=True)
        asar.write_bytes(b"mock app.asar")
        Path(updater.SCRIPTS_DIR).mkdir(parents=True, exist_ok=True)

    def make_package(self, version: str, marker: str):
        site = Path(updater.SITE_PACKAGES)
        package = site / "whimbox"
        package.mkdir(parents=True, exist_ok=True)
        (package / "fixture-version.txt").write_text(marker, encoding="utf-8")
        info = site / f"whimbox-{version}.dist-info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "METADATA").write_text(
            f"Name: whimbox\nVersion: {version}\n", encoding="utf-8"
        )
        return package, info

    @staticmethod
    def release(backend="2.0.0", frontend=None):
        assets = [{
            "name": f"whimbox-{backend}-py3-none-any.whl",
            "size": 16,
            "browser_download_url": "https://example.invalid/whimbox.whl",
        }]
        if frontend:
            assets.append({
                "name": f"whimbox_app-setup-{frontend}.exe",
                "size": 32,
                "browser_download_url": "https://example.invalid/whimbox.exe",
            })
        return [{
            "tag_name": f"v{backend}",
            "published_at": "2026-01-01T00:00:00Z",
            "assets": assets,
        }]

    def run_main(self, args):
        with mock.patch.object(sys, "argv", ["whimbox_updater.py", *args, "--quiet"]), \
                mock.patch.object(updater, "UpdateLock", return_value=contextlib.nullcontext()):
            return updater.main()

    def test_configure_paths_does_not_create_data_files(self):
        app = self.root / "other-app"
        data = self.root / "not-created"
        updater.configure_paths(app_dir=str(app), data_dir=str(data))
        self.assertEqual(Path(updater.APP_DIR), app)
        self.assertEqual(Path(updater.DATA_DIR), data)
        self.assertFalse(data.exists())

    def test_runtime_dir_uses_executable_directory_when_frozen(self):
        executable = self.root / "bundle" / "updater.exe"
        with mock.patch.object(updater.sys, "frozen", True, create=True), \
                mock.patch.object(updater.sys, "executable", str(executable)):
            self.assertEqual(Path(updater.runtime_dir()), executable.parent)

    def test_entrypoint_elevated_parent_preserves_exit_code_without_pausing(self):
        for arguments in (["updater.exe"], ["updater.exe", "--pause"]):
            with self.subTest(arguments=arguments):
                with mock.patch.object(updater.sys, "frozen", True, create=True), \
                        mock.patch.object(updater.sys, "argv", arguments), \
                        mock.patch.object(updater, "QUIET", False), \
                        mock.patch.object(updater, "main", side_effect=updater._ElevatedExit(23)), \
                        mock.patch("builtins.input", return_value="") as pause:
                    self.assertEqual(updater.entrypoint(), 23)
                pause.assert_not_called()

    def test_frozen_no_argument_elevation_passes_pause_to_child(self):
        shell = mock.Mock()
        kernel = mock.Mock()
        windll = mock.Mock()
        windll.shell32.IsUserAnAdmin.return_value = False
        captured = {}

        def execute(info):
            captured["parameters"] = info._obj.lpParameters
            info._obj.hProcess = 42
            return True

        def exit_code(handle, code):
            code._obj.value = 7
            return True

        shell.ShellExecuteExW.side_effect = execute
        kernel.WaitForSingleObject.return_value = 0
        kernel.GetExitCodeProcess.side_effect = exit_code
        libraries = {"shell32": shell, "kernel32": kernel}
        with mock.patch.object(updater.os, "name", "nt"), \
                mock.patch.object(updater.sys, "frozen", True, create=True), \
                mock.patch.object(updater.sys, "argv", ["updater.exe"]), \
                mock.patch.object(updater, "QUIET", False), \
                mock.patch.object(updater, "log"), \
                mock.patch.object(updater.ctypes, "windll", windll, create=True), \
                mock.patch.object(updater.ctypes, "WinDLL", side_effect=lambda name, **kwargs: libraries[name], create=True):
            with self.assertRaises(updater._ElevatedExit) as raised:
                updater.ensure_admin()
        self.assertEqual(raised.exception.code, 7)
        self.assertIn("--pause", captured["parameters"].split())
        kernel.CloseHandle.assert_called_once_with(42)

    def test_version_constant(self):
        self.assertEqual(updater.VERSION, "1.1.0")

    def test_backend_version_fallback_uses_semantic_order(self):
        site = Path(updater.SITE_PACKAGES)
        (site / "whimbox-1.9.0.dist-info").mkdir()
        (site / "whimbox-1.10.0.dist-info").mkdir()
        with mock.patch.object(updater.subprocess, "run", side_effect=OSError("offline")):
            self.assertEqual(updater.backend_version(), "1.10.0")

    def test_load_watchlist_empty_is_empty_but_invalid_input_raises(self):
        watchlist = Path(updater.WATCHLIST_FILE)
        self.assertEqual(updater.load_watchlist(), [])
        watchlist.parent.mkdir(parents=True, exist_ok=True)
        watchlist.write_text('{"scripts": []}', encoding="utf-8")
        self.assertEqual(updater.load_watchlist(), [])
        for invalid in ("{", '{"scripts": "all"}', "42"):
            with self.subTest(invalid=invalid):
                watchlist.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    updater.load_watchlist()

    def test_valid_watchlist_limits_script_sync(self):
        watchlist = Path(updater.WATCHLIST_FILE)
        watchlist.write_text('{"scripts": ["wanted.json"]}', encoding="utf-8")
        remote = {
            "wanted.json": b'{"route": "wanted"}',
            "other.json": b'{"route": "other"}',
        }
        with mock.patch.object(updater, "download_scripts_archive", return_value=remote):
            counts = updater.sync_scripts(only=updater.load_watchlist())
        scripts = Path(updater.SCRIPTS_DIR)
        self.assertEqual((scripts / "wanted.json").read_bytes(), remote["wanted.json"])
        self.assertFalse((scripts / "other.json").exists())
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["failed"], 0)

    def test_update_lock_rejects_second_owner_and_releases_after_exit(self):
        lock_path = self.data / "updater.lock"
        with updater.UpdateLock(str(lock_path)):
            with self.assertRaises(RuntimeError):
                with updater.UpdateLock(str(lock_path)):
                    pass
        with updater.UpdateLock(str(lock_path)):
            self.assertTrue(lock_path.is_file())

    def test_sync_scripts_failed_atomic_replace_preserves_original(self):
        scripts = Path(updater.SCRIPTS_DIR)
        target = scripts / "route.json"
        target.write_bytes(b'{"version": 1}')
        original_replace = updater.os.replace

        def replace(source, destination):
            if os.path.abspath(destination) == str(target):
                raise OSError("mock replace failure")
            return original_replace(source, destination)

        with mock.patch.object(updater, "download_scripts_archive", return_value={"route.json": b'{"version": 2}'}), \
                mock.patch.object(updater.os, "replace", side_effect=replace):
            counts = updater.sync_scripts()
        self.assertEqual(target.read_bytes(), b'{"version": 1}')
        self.assertGreater(counts["failed"], 0)

    def test_sync_scripts_reports_write_failure_and_keeps_previous_file(self):
        target = Path(updater.SCRIPTS_DIR) / "route.json"
        target.write_bytes(b'{"version": 1}')
        original_write = updater.atomic_write_bytes
        written_paths = []

        def write(path, data):
            written_paths.append(os.path.abspath(path))
            if os.path.abspath(path) == str(target):
                raise OSError("disk full")
            original_write(path, data)

        with mock.patch.object(updater, "download_scripts_archive", return_value={"route.json": b'{"version": 2}'}), \
                mock.patch.object(updater, "atomic_write_bytes", side_effect=write) as atomic_write:
            counts = updater.sync_scripts()
        self.assertEqual(target.read_bytes(), b'{"version": 1}')
        self.assertIn(str(target), written_paths)
        self.assertTrue(any(os.path.abspath(call.args[0]) == str(target) for call in atomic_write.call_args_list))
        self.assertGreater(counts["failed"], 0)

    def test_backup_and_explicit_rollback_restore_selected_backup(self):
        package, info = self.make_package("1.0.0", "old")
        backup = updater.backup_backend("1.0.0")
        self.assertTrue(Path(backup).is_dir())
        (package / "fixture-version.txt").write_text("new", encoding="utf-8")
        (info / "METADATA").write_text("Name: whimbox\nVersion: 2.0.0\n", encoding="utf-8")
        new_info = Path(updater.SITE_PACKAGES) / "whimbox-2.0.0.dist-info"
        new_info.mkdir()
        with mock.patch.object(updater.subprocess, "run", side_effect=OSError("no embedded python")):
            self.assertTrue(updater.rollback_backend("2.0.0", backup_dir=backup))
        self.assertEqual((package / "fixture-version.txt").read_text(encoding="utf-8"), "old")
        self.assertFalse(new_info.exists())

    def test_rollback_compensation_failure_preserves_previous_package(self):
        package, info = self.make_package("1.0.0", "known-good-old")
        backup = updater.backup_backend("1.0.0")
        new_info = info.with_name("whimbox-2.0.0.dist-info")
        info.rename(new_info)
        (new_info / "METADATA").write_text("Name: whimbox\nVersion: 2.0.0\n", encoding="utf-8")
        (package / "fixture-version.txt").write_text("current-only-copy", encoding="utf-8")
        original_replace = updater.os.replace

        def replace(source, destination):
            if (os.path.abspath(destination) == str(package)
                    and os.path.basename(source).startswith("previous-")):
                raise PermissionError("mock Windows lock during rollback compensation")
            return original_replace(source, destination)

        with mock.patch.object(updater, "backend_version", return_value="2.0.0"), \
                mock.patch.object(updater.os, "replace", side_effect=replace):
            self.assertFalse(updater.rollback_backend("2.0.0", backup_dir=backup))
        stages = list(Path(updater.SITE_PACKAGES).glob(".whimbox-restore-*"))
        self.assertEqual(len(stages), 1)
        retained = [path / "fixture-version.txt" for path in stages[0].glob("previous-*")
                    if (path / "fixture-version.txt").is_file()]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_text(encoding="utf-8"), "current-only-copy")
        self.assertTrue((stages[0] / "recovery.json").is_file())
        self.assertTrue(new_info.is_dir())
        self.assertTrue(Path(backup).is_dir())

    def test_corrupt_backup_is_rejected_without_touching_live_package(self):
        package, _ = self.make_package("1.0.0", "original")
        backup = Path(updater.backup_backend("1.0.0"))
        (package / "fixture-version.txt").write_text("current", encoding="utf-8")
        new_info = Path(updater.SITE_PACKAGES) / "whimbox-2.0.0.dist-info"
        new_info.mkdir()
        (new_info / "METADATA").write_text("Name: whimbox\nVersion: 2.0.0\n", encoding="utf-8")
        (backup / "whimbox" / "fixture-version.txt").write_text("tampered", encoding="utf-8")
        with mock.patch.object(updater.subprocess, "run", side_effect=OSError("no embedded python")):
            self.assertFalse(updater.rollback_backend("2.0.0", backup_dir=str(backup)))
        self.assertEqual((package / "fixture-version.txt").read_text(encoding="utf-8"), "current")
        self.assertTrue(new_info.is_dir())

    def test_install_whl_uses_no_deps_and_pip_check(self):
        calls = []

        def run(args, timeout=600):
            calls.append(list(args))
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertTrue(updater.install_whl("fixture.whl"))
        install_calls = [args for args in calls if "install" in args]
        self.assertTrue(install_calls)
        self.assertTrue(all("--no-deps" in args for args in install_calls))
        self.assertFalse(any("setuptools" in args for args in install_calls))
        self.assertTrue(any("check" in args for args in calls))

    def test_install_whl_pip_check_failure_fails_install(self):
        calls = []

        def run(args, timeout=600):
            calls.append(list(args))
            if args[-1] == "check":
                return False, "mock dependency conflict"
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertFalse(updater.install_whl("fixture.whl"))
        self.assertTrue(any("check" in args for args in calls))
        self.assertFalse(any("import whimbox" in args for args in calls))

    def test_install_whl_force_adds_force_reinstall(self):
        calls = []

        def run(args, timeout=600):
            calls.append(list(args))
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertTrue(updater.install_whl("fixture.whl", force=True))
        install_calls = [args for args in calls if "install" in args]
        self.assertEqual(len(install_calls), 1)
        self.assertIn("--force-reinstall", install_calls[0])
        self.assertIn("--no-deps", install_calls[0])

    def test_main_force_forwards_reinstall_flag(self):
        wheel_name = "whimbox-2.0.0-py3-none-any.whl"
        this_backup = str(self.root / "this-run-backup")
        with mock.patch.object(updater, "backend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release("2.0.0")), \
                mock.patch.object(updater, "download_asset", return_value="2.0.0"), \
                mock.patch.object(updater, "backup_backend", return_value=this_backup), \
                mock.patch.object(updater, "install_whl", return_value=True) as install:
            rc = self.run_main(["--no-elevate", "--no-scripts", "--force"])
        self.assertEqual(rc, 0)
        install.assert_called_once_with(os.path.join(updater.DOWNLOAD_DIR, wheel_name), force=True)

    def test_download_failure_does_not_rollback_historical_backup(self):
        self.make_package("2.0.0", "current")
        with mock.patch.object(updater, "backend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release()), \
                mock.patch.object(updater, "download_asset", side_effect=RuntimeError("offline")), \
                mock.patch.object(updater, "rollback_backend") as rollback:
            rc = self.run_main(["--no-elevate", "--no-scripts"])
        self.assertNotEqual(rc, 0)
        rollback.assert_not_called()

    def test_backup_failure_does_not_rollback_historical_backup(self):
        self.make_package("1.0.0", "current")
        with mock.patch.object(updater, "backend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release()), \
                mock.patch.object(updater, "download_asset", return_value="2.0.0"), \
                mock.patch.object(updater, "backup_backend", side_effect=OSError("backup failed")), \
                mock.patch.object(updater, "rollback_backend") as rollback:
            rc = self.run_main(["--no-elevate", "--no-scripts"])
        self.assertNotEqual(rc, 0)
        rollback.assert_not_called()

    def test_install_failure_rolls_back_using_this_run_backup(self):
        self.make_package("1.0.0", "current")
        this_backup = str(self.root / "this-run-backup")
        with mock.patch.object(updater, "backend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release()), \
                mock.patch.object(updater, "download_asset", return_value="2.0.0"), \
                mock.patch.object(updater, "backup_backend", return_value=this_backup), \
                mock.patch.object(updater, "install_whl", return_value=False), \
                mock.patch.object(updater, "rollback_backend", return_value=True) as rollback:
            rc = self.run_main(["--no-elevate", "--no-scripts"])
        self.assertNotEqual(rc, 0)
        rollback.assert_called_once_with("2.0.0", backup_dir=this_backup)

    def test_invalid_watchlist_does_not_fall_back_to_full_sync(self):
        Path(updater.WATCHLIST_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(updater.WATCHLIST_FILE).write_text("{", encoding="utf-8")
        with mock.patch.object(updater, "backend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release("2.0.0")), \
                mock.patch.object(updater, "sync_scripts") as sync:
            rc = self.run_main(["--no-elevate"])
        self.assertNotEqual(rc, 0)
        sync.assert_not_called()

    def test_script_sync_failure_causes_nonzero_exit(self):
        failed = {"added": 0, "updated": 0, "same": 0, "failed": 1}
        with mock.patch.object(updater, "backend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release("2.0.0")), \
                mock.patch.object(updater, "load_watchlist", return_value=[]), \
                mock.patch.object(updater, "sync_scripts", return_value=failed):
            rc = self.run_main(["--no-elevate"])
        self.assertNotEqual(rc, 0)

    def test_frontend_failure_causes_nonzero_exit(self):
        releases = self.release("2.0.0", "2.0.0")
        with mock.patch.object(updater, "backend_version", return_value="2.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=releases), \
                mock.patch.object(updater, "download_asset", return_value=None), \
                mock.patch.object(updater, "install_frontend", return_value=False), \
                mock.patch.object(updater, "sync_scripts", return_value={"added": 0, "updated": 0, "same": 0, "failed": 0}):
            rc = self.run_main(["--no-elevate", "--no-scripts", "--install-frontend"])
        self.assertNotEqual(rc, 0)

    def test_save_state_clears_stale_errors_and_preserves_last_success(self):
        state = Path(updater.STATE_FILE)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({
            "last_backend_update": "2026-01-01 00:00:00",
            "backend_error": "old backend failure",
            "frontend_error": "old frontend failure",
            "scripts_error": "old scripts failure",
        }), encoding="utf-8")
        updater.save_state({"backend_version": "2.0.0"})
        saved = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_backend_update"], "2026-01-01 00:00:00")
        self.assertNotIn("backend_error", saved)
        self.assertNotIn("frontend_error", saved)
        self.assertNotIn("scripts_error", saved)


if __name__ == "__main__":
    unittest.main()
