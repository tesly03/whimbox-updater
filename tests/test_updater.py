from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import whimbox_updater as updater  # noqa: E402


class _MockHTTPResponse:
    def __init__(self, body: bytes, headers=None, status=200):
        self._buf = io.BytesIO(body)
        self.headers = headers or {}
        self.status = status

    def read(self, size=-1):
        return self._buf.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._buf.close()


def _build_asar(package_version: str) -> bytes:
    pkg_bytes = json.dumps({"name": "whimbox_app", "version": package_version}).encode("utf-8")
    header_json = json.dumps({
        "files": {
            "package.json": {"size": len(pkg_bytes), "offset": "0"},
        }
    }).encode("utf-8")
    pad = (-len(header_json)) % 4
    padded_json = header_json + (b"\x00" * pad)
    inner_pickle_size = 4 + len(padded_json)
    header_size = 4 + inner_pickle_size
    prefix = struct.pack("<4I", 4, header_size, inner_pickle_size, len(header_json))
    return prefix + padded_json + pkg_bytes


def _build_wheel(path: Path, version: str = "2.0.0", name: str = "whimbox", extra_metadata: bool = False) -> bytes:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("whimbox/__init__.py", "")
        zf.writestr("whimbox/main.py", "def main(): pass\n")
        zf.writestr(f"whimbox-{version}.dist-info/METADATA", f"Name: {name}\nVersion: {version}\n")
        if extra_metadata:
            zf.writestr("whimbox-1.0.0.dist-info/METADATA", f"Name: {name}\nVersion: 1.0.0\n")
    return path.read_bytes()


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
                "APP_DIR", "APP_EXE", "PY_EXE", "SITE_PACKAGES", "ASAR", "SCRIPTS_DIR",
                "DOWNLOAD_DIR", "DATA_DIR", "BACKUP_DIR", "LEGACY_BACKUP_DIR", "LOG_DIR",
                "WATCHLIST_FILE", "STATE_FILE", "RELEASE_CACHE", "RELEASE_META", "SCRIPTS_STATE",
                "LOG_DIR_GLOB", "SCRIPT_DIR", "QUIET", "_LOG_PATH",
                "_DIRECT_ONLY", "_CUSTOM_PROXIES",
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
        updater._DIRECT_ONLY = False
        updater._CUSTOM_PROXIES = None

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
    def release(backend="2.0.0", frontend=None, digest="a" * 64, body=""):
        assets = []
        if backend:
            assets.append({
                "name": f"whimbox-{backend}-py3-none-any.whl",
                "size": 16,
                "digest": f"sha256:{digest}",
                "browser_download_url": f"https://github.com/nikkigallery/Whimbox/releases/download/{backend}/whimbox-{backend}-py3-none-any.whl",
            })
        if frontend:
            assets.append({
                "name": f"whimbox_app-setup-{frontend}.exe",
                "size": 32,
                "digest": f"sha256:{digest}",
                "browser_download_url": f"https://github.com/nikkigallery/Whimbox/releases/download/{frontend}/whimbox_app-setup-{frontend}.exe",
            })
        return [{
            "tag_name": f"v{backend or frontend}",
            "published_at": "2026-01-01T00:00:00Z",
            "body": body,
            "assets": assets,
        }]

    def run_main(self, args):
        with mock.patch.object(sys, "argv", ["whimbox_updater.py", *args, "--quiet"]), \
                mock.patch.object(updater, "UpdateLock", return_value=contextlib.nullcontext()), \
                mock.patch.object(updater, "find_running_whimbox_processes", return_value=[]):
            return updater.main()

    # ------------------------------------------------------------------
    # Existing core regression tests
    # ------------------------------------------------------------------
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

    def test_version_constant_matches_semver_and_changelog(self):
        self.assertRegex(updater.VERSION, r"^\d+\.\d+\.\d+$")
        changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## v{updater.VERSION}", changelog)

    def test_repo_batch_files_are_crlf_and_install_task_ps1_is_ascii(self):
        bat_files = list(REPO_ROOT.glob("*.bat"))
        self.assertTrue(bat_files)
        for bat in bat_files:
            raw = bat.read_bytes()
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), f"{bat.name} must use CRLF")
        (REPO_ROOT / "install_task.ps1").read_bytes().decode("ascii")

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
        with mock.patch.object(updater, "fetch_scripts_commit", return_value=(None, None)), \
                mock.patch.object(updater, "download_scripts_archive", return_value=remote):
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

        with mock.patch.object(updater, "fetch_scripts_commit", return_value=(None, None)), \
                mock.patch.object(updater, "download_scripts_archive", return_value={"route.json": b'{"version": 2}'}), \
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

        with mock.patch.object(updater, "fetch_scripts_commit", return_value=(None, None)), \
                mock.patch.object(updater, "download_scripts_archive", return_value={"route.json": b'{"version": 2}'}), \
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
                mock.patch.object(updater.os, "replace", side_effect=replace), \
                mock.patch.object(updater.time, "sleep"):
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

    def test_install_whl_uses_no_deps_and_pip_check_and_cwd(self):
        calls = []

        def run(args, timeout=600, cwd=None):
            calls.append((list(args), cwd))
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertTrue(updater.install_whl("fixture.whl"))
        install_calls = [args for args, _ in calls if "install" in args]
        self.assertTrue(install_calls)
        self.assertTrue(all("--no-deps" in args for args in install_calls))
        self.assertFalse(any("setuptools" in args for args in install_calls))
        self.assertTrue(any("check" in args for args, _ in calls))
        import_calls = [(args, cwd) for args, cwd in calls if any("importlib.util, whimbox" in a for a in args)]
        self.assertEqual(len(import_calls), 1)
        self.assertEqual(import_calls[0][1], updater.APP_DIR)

    def test_install_whl_pip_check_failure_fails_install(self):
        calls = []

        def run(args, timeout=600, cwd=None):
            calls.append(list(args))
            if args[-1] == "check":
                return False, "whimbox 3.2.0 requires watchdog==6.0.0, which is not installed."
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertFalse(updater.install_whl("fixture.whl"))
        self.assertTrue(any("check" in args for args in calls))
        self.assertFalse(any(any("whimbox" in item and "import" in item for item in args) for args in calls))

    def test_install_whl_force_adds_force_reinstall(self):
        calls = []

        def run(args, timeout=600, cwd=None):
            calls.append(list(args))
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertTrue(updater.install_whl("fixture.whl", force=True))
        install_calls = [args for args in calls if "install" in args]
        self.assertEqual(len(install_calls), 1)
        self.assertIn("--force-reinstall", install_calls[0])
        self.assertIn("--no-deps", install_calls[0])

    def test_install_whl_sync_deps_uses_mirrors_with_fallback(self):
        calls = []

        def run(args, timeout=600, cwd=None):
            calls.append(list(args))
            if "install" in args and "-i" in args and args[args.index("-i") + 1] == updater.PIP_MIRRORS[0]:
                return False, "mirror 0 failed"
            return True, "ok"

        with mock.patch.object(updater, "run_cmd", side_effect=run):
            self.assertTrue(updater.install_whl("fixture.whl", sync_deps=True))
        install_calls = [args for args in calls if "install" in args]
        self.assertEqual(len(install_calls), 2)
        self.assertFalse(any("--no-deps" in args for args in install_calls))
        self.assertIn(updater.PIP_MIRRORS[0], install_calls[0])
        self.assertIn(updater.PIP_MIRRORS[1], install_calls[1])

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
        install.assert_called_once_with(
            os.path.join(updater.DOWNLOAD_DIR, wheel_name), force=True, sync_deps=False
        )

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

    # ------------------------------------------------------------------
    # New v1.2.0 security, frontend/backend, sync, and hygiene tests
    # ------------------------------------------------------------------
    def test_frontend_version_parses_asar_and_falls_back_to_logs(self):
        Path(updater.ASAR).write_bytes(_build_asar("3.1.4"))
        self.assertEqual(updater.frontend_version(), "3.1.4")

        # Corrupt ASAR falls back to latest log file.
        Path(updater.ASAR).write_bytes(b"corrupt")
        logs_dir = self.app / "logs"
        logs_dir.mkdir(exist_ok=True)
        (logs_dir / "app-2026-11-01.log").write_text("Whimbox App version=3.1.0\n", encoding="utf-8")
        (logs_dir / "app-2026-11-08.log").write_text("Whimbox App version=3.1.3\n", encoding="utf-8")
        self.assertEqual(updater.frontend_version(), "3.1.3")

    def test_verify_wheel_validates_package_and_rejects_malformed(self):
        good = self.root / "good.whl"
        _build_wheel(good, version="3.1.5")
        self.assertEqual(updater.verify_wheel(str(good)), "3.1.5")

        wrong_name = self.root / "wrong.whl"
        _build_wheel(wrong_name, version="3.1.5", name="otherpkg")
        with self.assertRaises(ValueError):
            updater.verify_wheel(str(wrong_name))

        dup_meta = self.root / "dup.whl"
        _build_wheel(dup_meta, version="3.1.5", extra_metadata=True)
        with self.assertRaises(ValueError):
            updater.verify_wheel(str(dup_meta))

        bad_zip = self.root / "bad.whl"
        bad_zip.write_bytes(b"not a zip")
        with self.assertRaises(zipfile.BadZipFile):
            updater.verify_wheel(str(bad_zip))

    def test_download_asset_verifies_url_sha_size_cache_and_fallback(self):
        wheel_src = self.root / "src.whl"
        wheel_bytes = _build_wheel(wheel_src, version="3.1.5")
        wheel_sha = hashlib.sha256(wheel_bytes).hexdigest()
        official_url = "https://github.com/nikkigallery/Whimbox/releases/download/3.1.5/whimbox-3.1.5-py3-none-any.whl"
        dest = self.data / "downloads" / "whimbox-3.1.5-py3-none-any.whl"

        # Reject missing SHA256 and non-official URL.
        with self.assertRaises(RuntimeError):
            updater.download_asset(official_url, str(dest), len(wheel_bytes), None, is_whl=True)
        with self.assertRaises(ValueError):
            updater.download_asset("https://evil.example/whimbox.whl", str(dest), len(wheel_bytes), wheel_sha, is_whl=True)

        # First proxy returns bad bytes; second returns valid wheel.
        attempts = []

        def fake_urlopen(req, timeout=20):
            attempts.append(req.full_url)
            if len(attempts) == 1:
                return _MockHTTPResponse(b"corrupted" * 10)
            return _MockHTTPResponse(wheel_bytes)

        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=fake_urlopen):
            version = updater.download_asset(official_url, str(dest), len(wheel_bytes), wheel_sha, is_whl=True)
        self.assertEqual(version, "3.1.5")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(dest.read_bytes(), wheel_bytes)

        # Cached file hits without network.
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=AssertionError("should use cache")):
            self.assertEqual(
                updater.download_asset(official_url, str(dest), len(wheel_bytes), wheel_sha, is_whl=True),
                "3.1.5",
            )

    def test_fetch_releases_uses_etag_304_and_never_sends_token_to_proxies(self):
        releases_payload = self.release("3.1.5", "3.1.4", digest="b" * 64)
        seen_requests = []

        def first_urlopen(req, timeout=20):
            seen_requests.append((req.full_url, dict(req.header_items())))
            return _MockHTTPResponse(json.dumps(releases_payload).encode("utf-8"), headers={"ETag": '"etag-v1"'})

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}, clear=False), \
                mock.patch.object(updater.urllib.request, "urlopen", side_effect=first_urlopen):
            got = updater.fetch_releases()
        self.assertEqual(got[0]["tag_name"], "v3.1.5")
        self.assertEqual(seen_requests[0][1].get("Authorization"), "Bearer secret-token")

        # Second call sends If-None-Match and handles 304 without re-downloading payload.
        seen_second = []

        def not_modified_urlopen(req, timeout=20):
            seen_second.append((req.full_url, dict(req.header_items())))
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)

        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=not_modified_urlopen):
            got_cached = updater.fetch_releases()
        self.assertEqual(got_cached[0]["tag_name"], "v3.1.5")
        self.assertEqual(seen_second[0][1].get("If-none-match"), '"etag-v1"')

        # When direct API fails and proxies are used, Authorization header MUST NOT be sent.
        proxy_requests = []

        def proxy_only_urlopen(req, timeout=20):
            proxy_requests.append((req.full_url, dict(req.header_items())))
            if req.full_url == updater.RELEASES_API:
                raise OSError("direct blocked")
            return _MockHTTPResponse(json.dumps(releases_payload).encode("utf-8"))

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"}, clear=False), \
                mock.patch.object(updater.urllib.request, "urlopen", side_effect=proxy_only_urlopen):
            got_proxy = updater.fetch_releases()
        self.assertEqual(got_proxy[0]["tag_name"], "v3.1.5")
        proxy_calls = [hdrs for url, hdrs in proxy_requests if url != updater.RELEASES_API]
        self.assertEqual(len(proxy_calls), 2)
        for hdrs in proxy_calls:
            self.assertNotIn("Authorization", hdrs)

    def test_fetch_releases_proxy_consensus_rejects_mismatched_digest(self):
        rel_a = self.release("3.1.5", digest="1" * 64)
        rel_b = self.release("3.1.5", digest="2" * 64)
        call_num = {"n": 0}

        def tampered_urlopen(req, timeout=20):
            if req.full_url == updater.RELEASES_API:
                raise OSError("direct blocked")
            call_num["n"] += 1
            payload = rel_a if call_num["n"] == 1 else rel_b
            return _MockHTTPResponse(json.dumps(payload).encode("utf-8"))

        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=tampered_urlopen):
            with self.assertRaises(RuntimeError) as ctx:
                updater.fetch_releases()
        self.assertIn("摘要不一致", str(ctx.exception))

    def test_proxy_consensus_filters_uncorroborated_fake_tag(self):
        real = self.release("3.1.5", digest="a" * 64)[0]
        fake = self.release("9.9.9", digest="f" * 64)[0]
        chosen = updater._select_verified_proxy_releases([fake, real], [real])
        self.assertEqual([r["tag_name"] for r in chosen], ["v3.1.5"])

    def test_env_for_pip_isolates_embedded_python(self):
        with mock.patch.dict(os.environ, {
            "PYTHONPATH": "/some/external/path",
            "_MEIPASS": "/tmp/pyinstaller",
            "TCL_LIBRARY": "/tmp/tcl",
        }, clear=False):
            env = updater.env_for_pip()
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("_MEIPASS", env)
        self.assertNotIn("TCL_LIBRARY", env)
        py_dir = os.path.dirname(os.path.abspath(updater.PY_EXE))
        self.assertEqual(env["PYTHONHOME"], py_dir)
        self.assertTrue(env["PATH"].startswith(py_dir + os.pathsep))
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_frontend_install_cmdline_uses_unquoted_nsis_dir_and_rejects_non_ascii(self):
        cmd = updater._frontend_install_cmdline(r"/tmp/setup.exe", r"/tmp/whimbox app")
        self.assertTrue(cmd.endswith("/S /D=" + os.path.abspath(r"/tmp/whimbox app")))
        self.assertNotIn('"/D=', cmd)
        with self.assertRaises(ValueError):
            updater._frontend_install_cmdline(r"/tmp/setup.exe", str(self.root / "奇想盒"))

    def test_running_whimbox_blocks_backend_update_and_rollback_but_allows_check_and_scripts(self):
        running = [(1234, "whimbox_app.exe", str(self.app / "whimbox_app.exe"))]
        with mock.patch.object(sys, "argv", ["whimbox_updater.py", "--no-elevate", "--no-scripts", "--quiet"]), \
                mock.patch.object(updater, "UpdateLock", return_value=contextlib.nullcontext()), \
                mock.patch.object(updater, "find_running_whimbox_processes", return_value=running), \
                mock.patch.object(updater, "backend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release("2.0.0")), \
                mock.patch.object(updater, "download_asset") as dl:
            rc = updater.main()
        self.assertNotEqual(rc, 0)
        dl.assert_not_called()

        # --check is allowed even when Whimbox is running.
        with mock.patch.object(sys, "argv", ["whimbox_updater.py", "--check", "--quiet"]), \
                mock.patch.object(updater, "UpdateLock", return_value=contextlib.nullcontext()), \
                mock.patch.object(updater, "find_running_whimbox_processes", return_value=running), \
                mock.patch.object(updater, "backend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.0.0"), \
                mock.patch.object(updater, "fetch_releases", return_value=self.release("2.0.0")):
            self.assertEqual(updater.main(), 0)

        # --scripts-only is allowed (Whimbox watches scripts/ for hot reload).
        with mock.patch.object(sys, "argv", ["whimbox_updater.py", "--no-elevate", "--scripts-only", "--quiet"]), \
                mock.patch.object(updater, "UpdateLock", return_value=contextlib.nullcontext()), \
                mock.patch.object(updater, "find_running_whimbox_processes", return_value=running), \
                mock.patch.object(updater, "sync_scripts", return_value={"added": 1, "updated": 0, "same": 0, "failed": 0}):
            self.assertEqual(updater.main(), 0)

    def test_incremental_script_sync_skips_download_when_commit_unchanged(self):
        remote = {"a.json": b'{"info": {"name": "a"}}'}
        with mock.patch.object(updater, "fetch_scripts_commit", return_value=("abc123456789", '"etag1"')), \
                mock.patch.object(updater, "download_scripts_archive", return_value=remote) as dl:
            first = updater.sync_scripts()
        self.assertEqual(first["added"], 1)
        self.assertEqual(dl.call_count, 1)

        # Second sync with same commit SHA and file present skips archive download.
        with mock.patch.object(updater, "fetch_scripts_commit", return_value=("abc123456789", '"etag1"')), \
                mock.patch.object(updater, "download_scripts_archive", side_effect=AssertionError("should skip")):
            second = updater.sync_scripts()
        self.assertTrue(second.get("skipped_download"))
        self.assertEqual(second["same"], 1)

        # Removing local file triggers re-download even with same commit SHA.
        (Path(updater.SCRIPTS_DIR) / "a.json").unlink()
        with mock.patch.object(updater, "fetch_scripts_commit", return_value=("abc123456789", '"etag1"')), \
                mock.patch.object(updater, "download_scripts_archive", return_value=remote) as dl2:
            third = updater.sync_scripts()
        self.assertEqual(dl2.call_count, 1)
        self.assertEqual(third["added"], 1)

        # --force bypasses commit cache.
        with mock.patch.object(updater, "fetch_scripts_commit", return_value=("abc123456789", '"etag1"')), \
                mock.patch.object(updater, "download_scripts_archive", return_value=remote) as dl3:
            fourth = updater.sync_scripts(force=True)
        self.assertEqual(dl3.call_count, 1)
        self.assertEqual(fourth["updated"], 1)

    def test_sync_scripts_isolates_bad_json_and_matches_case_insensitive_on_windows(self):
        remote = {
            "Good.json": b'{"info": {"name": "good"}}',
            "broken.json": b'{not valid json',
        }
        with mock.patch.object(updater, "fetch_scripts_commit", return_value=(None, None)), \
                mock.patch.object(updater, "download_scripts_archive", return_value=remote):
            counts = updater.sync_scripts()
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["failed"], 1)
        self.assertTrue((Path(updater.SCRIPTS_DIR) / "Good.json").is_file())

        with mock.patch.object(updater.os, "name", "nt"):
            matched, missing = updater._match_wanted_scripts(remote, ["good.json"])
        self.assertEqual(list(matched.keys()), ["Good.json"])
        self.assertEqual(missing, set())

    def test_smart_elevation_and_config_edit_before_elevation(self):
        args = updater._parser().parse_args(["--no-scripts"])
        with mock.patch.object(updater.os, "name", "nt"), \
                mock.patch.object(updater, "_dir_is_writable", return_value=True):
            self.assertFalse(updater._needs_elevation(args))
        with mock.patch.object(updater.os, "name", "nt"), \
                mock.patch.object(updater, "_dir_is_writable", return_value=False):
            self.assertTrue(updater._needs_elevation(args))

        # --install-frontend always requires elevation on Windows.
        fe_args = updater._parser().parse_args(["--install-frontend", "--no-backend", "--no-scripts"])
        with mock.patch.object(updater.os, "name", "nt"), \
                mock.patch.object(updater, "_dir_is_writable", return_value=True):
            self.assertTrue(updater._needs_elevation(fe_args))

        # Stripping --edit-subscriptions / --watch replaces them with --scripts-only for elevated child.
        self.assertEqual(
            updater._child_args_after_config_edit(["--edit-subscriptions", "--pause"]),
            ["--pause", "--scripts-only"],
        )
        self.assertEqual(
            updater._child_args_after_config_edit(["--watch", "a.json", "--pause"]),
            ["--pause", "--scripts-only"],
        )

        # --watch "" saves watchlist and exits 0 without ever calling ensure_admin.
        with mock.patch.object(updater.os, "name", "nt"), \
                mock.patch.object(updater, "ensure_admin") as elevate:
            rc = self.run_main(["--watch", ""])
        self.assertEqual(rc, 0)
        elevate.assert_not_called()

    def test_rollback_updates_state_and_latest_backup_prefers_created_timestamp(self):
        b1 = Path(updater.BACKUP_DIR) / "backend-20260101-010000-from-1.0.0-aaaa1111"
        b2 = Path(updater.BACKUP_DIR) / "backend-20260201-010000-from-1.5.0-bbbb2222"
        b1.mkdir(parents=True)
        b2.mkdir(parents=True)
        (b1 / "backup.json").write_text(json.dumps({"version": "1.0.0", "created": "20260101-010000"}), encoding="utf-8")
        (b2 / "backup.json").write_text(json.dumps({"version": "1.5.0", "created": "20260201-010000"}), encoding="utf-8")
        # Make older backup have newer filesystem mtime; latest_backup should still pick b2.
        os.utime(b1, (2000000000, 2000000000))
        os.utime(b2, (1700000000, 1700000000))
        self.assertEqual(Path(updater.latest_backup()), b2)

        with mock.patch.object(updater, "rollback_backend", return_value=True), \
                mock.patch.object(updater, "backend_version", return_value="1.5.0"), \
                mock.patch.object(updater, "frontend_version", return_value="1.5.0"):
            rc = self.run_main(["--no-elevate", "--rollback"])
        self.assertEqual(rc, 0)
        state = json.loads(Path(updater.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "rolled_back")
        self.assertEqual(state["backend_version"], "1.5.0")
        self.assertIn("last_rollback", state)

    def test_pruning_downloads_script_backups_and_old_logs(self):
        dl_dir = Path(updater.DOWNLOAD_DIR)
        dl_dir.mkdir(parents=True, exist_ok=True)
        wheels = []
        for idx in range(5):
            p = dl_dir / f"whimbox-3.1.{idx}-py3-none-any.whl"
            p.write_bytes(b"whl")
            os.utime(p, (1700000000 + idx * 100, 1700000000 + idx * 100))
            wheels.append(p)
        updater._prune_downloads(protected=[str(wheels[0])])
        remaining = sorted(p.name for p in dl_dir.glob("*.whl"))
        self.assertIn(wheels[0].name, remaining)  # protected
        self.assertEqual(len(remaining), updater.DOWNLOAD_KEEP + 1)

        # Script backups keep latest SCRIPT_BACKUP_KEEP.
        for idx in range(updater.SCRIPT_BACKUP_KEEP + 3):
            (Path(updater.BACKUP_DIR) / f"scripts-202601{idx+1:02d}-000000-aaaa").mkdir(parents=True)
        updater._prune_script_backups()
        self.assertEqual(len(list(Path(updater.BACKUP_DIR).glob("scripts-*"))), updater.SCRIPT_BACKUP_KEEP)

        # Logs older than LOG_KEEP_DAYS are pruned.
        old_day = (datetime.now() - timedelta(days=updater.LOG_KEEP_DAYS + 5)).strftime("%Y-%m-%d")
        recent_day = datetime.now().strftime("%Y-%m-%d")
        old_log = Path(updater.LOG_DIR) / f"updater-{old_day}.log"
        recent_log = Path(updater.LOG_DIR) / f"updater-{recent_day}.log"
        old_log.write_text("old", encoding="utf-8")
        recent_log.write_text("recent", encoding="utf-8")
        updater._prune_logs()
        self.assertFalse(old_log.exists())
        self.assertTrue(recent_log.exists())

    def test_proxy_configuration_and_validation(self):
        with mock.patch.dict(os.environ, {"WHIMBOX_PROXIES": "direct"}, clear=False):
            self.assertEqual(updater.get_proxies(), [""])
        with mock.patch.dict(os.environ, {"WHIMBOX_PROXIES": "https://mirror.example"}, clear=False):
            self.assertEqual(updater.get_proxies(), ["https://mirror.example/", ""])
        with self.assertRaises(ValueError):
            updater._normalize_proxy_url("http://insecure.example")
        # Invalid --proxy and --direct+--proxy return exit code 1 cleanly.
        self.assertEqual(self.run_main(["--direct", "--proxy", "https://mirror.example/"]), 1)
        self.assertEqual(self.run_main(["--proxy", "http://insecure.example"]), 1)

    def test_frontend_only_mode_skips_backend_and_scripts(self):
        releases = self.release("3.1.5", "3.1.4")
        with mock.patch.object(updater, "backend_version", return_value="3.1.0"), \
                mock.patch.object(updater, "frontend_version", side_effect=["3.1.0", "3.1.4"]), \
                mock.patch.object(updater, "fetch_releases", return_value=releases), \
                mock.patch.object(updater, "download_asset", return_value=None) as dl, \
                mock.patch.object(updater, "install_whl") as whl_install, \
                mock.patch.object(updater, "install_frontend", return_value=True) as fe_install, \
                mock.patch.object(updater, "sync_scripts") as sync:
            rc = self.run_main(["--no-elevate", "--no-backend", "--no-scripts", "--install-frontend"])
        self.assertEqual(rc, 0)
        whl_install.assert_not_called()
        sync.assert_not_called()
        fe_install.assert_called_once()
        self.assertEqual(dl.call_count, 1)


if __name__ == "__main__":
    unittest.main()
