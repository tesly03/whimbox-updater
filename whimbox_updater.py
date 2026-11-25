# -*- coding: utf-8 -*-
"""Whimbox updater: verified downloads, transactional recovery and script sync.

Run the source with Whimbox's embedded Python, or use the Windows executable.
Only the build process needs PyInstaller; the updater uses the standard library.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

VERSION = "1.1.0"
DEFAULT_APP_DIR = r"C:\Program Files\whimbox_app"
RELEASES_API = "https://api.github.com/repos/nikkigallery/Whimbox/releases?per_page=30"
SCRIPTS_ARCHIVE_URL = "https://github.com/nikkigallery/WhimboxScripts/archive/refs/heads/main.zip"
SCRIPTS_COMMIT_API = "https://api.github.com/repos/nikkigallery/WhimboxScripts/commits/main"
PROXIES = ["https://gh-proxy.com/", "https://ghfast.top/", "https://ghproxy.net/", ""]
PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
]
UA = "WhimboxUpdater/" + VERSION
CHUNK = 256 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_SCRIPT_BYTES = 32 * 1024 * 1024
BACKUP_KEEP = 3
QUIET = False
_LOG_PATH = None


def runtime_dir():
    """A frozen executable's data belongs next to the executable, never _MEIPASS."""
    location = sys.executable if getattr(sys, "frozen", False) else __file__
    return os.path.dirname(os.path.abspath(location))


def _detect_app_dir():
    cur = runtime_dir()
    for _ in range(12):
        if (os.path.isfile(os.path.join(cur, "python-embedded", "python.exe"))
                and os.path.isfile(os.path.join(cur, "resources", "app.asar"))):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return DEFAULT_APP_DIR


def configure_paths(app_dir=None, data_dir=None):
    global APP_DIR, PY_EXE, SITE_PACKAGES, ASAR, SCRIPTS_DIR, DOWNLOAD_DIR
    global LOG_DIR_GLOB, SCRIPT_DIR, DATA_DIR, BACKUP_DIR, LEGACY_BACKUP_DIR
    global LOG_DIR, WATCHLIST_FILE, STATE_FILE, RELEASE_CACHE, SCRIPTS_STATE, _LOG_PATH
    APP_DIR = os.path.abspath(app_dir or _detect_app_dir())
    SCRIPT_DIR = runtime_dir()
    PY_EXE = os.path.join(APP_DIR, "python-embedded", "python.exe")
    SITE_PACKAGES = os.path.join(APP_DIR, "python-embedded", "Lib", "site-packages")
    ASAR = os.path.join(APP_DIR, "resources", "app.asar")
    SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")
    LOG_DIR_GLOB = os.path.join(APP_DIR, "logs", "app-*.log")
    install_id = hashlib.sha256(os.path.normcase(APP_DIR).encode("utf-8")).hexdigest()[:12]
    user_data = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    DATA_DIR = os.path.abspath(data_dir or os.path.join(user_data, "WhimboxUpdater", install_id))
    BACKUP_DIR = os.path.join(DATA_DIR, "backups")
    LEGACY_BACKUP_DIR = os.path.join(SCRIPT_DIR, "_data", "backups")
    LOG_DIR = os.path.join(DATA_DIR, "logs")
    DOWNLOAD_DIR = os.path.join(DATA_DIR, "downloads")
    WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
    STATE_FILE = os.path.join(DATA_DIR, "state.json")
    RELEASE_CACHE = os.path.join(DATA_DIR, "releases_cache.json")
    SCRIPTS_STATE = os.path.join(DATA_DIR, "scripts_state.json")
    _LOG_PATH = None


configure_paths()


def _reconfigure_stdout():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_reconfigure_stdout()


def log(msg, level="INFO"):
    global _LOG_PATH
    if not QUIET:
        print("[%s] [%s] %s" % (datetime.now().strftime("%H:%M:%S"), level, msg), flush=True)
    try:
        if _LOG_PATH is None:
            os.makedirs(LOG_DIR, exist_ok=True)
            _LOG_PATH = os.path.join(LOG_DIR, "updater-%s.log" % datetime.now().strftime("%Y-%m-%d"))
        with open(_LOG_PATH, "a", encoding="utf-8") as output:
            output.write("[%s] [%s] %s\n" % (datetime.now().isoformat(timespec="seconds"), level, msg))
    except OSError:
        pass  # Logging must not prevent permission checks or error reporting.


def atomic_write_bytes(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".updater-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def atomic_write_json(path, data):
    atomic_write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _read_json(path):
    with open(path, "r", encoding="utf-8-sig") as source:
        return json.load(source)


class UpdateLock:
    """OS-managed lock: released on exit/crash; covers manual and scheduled runs."""
    def __init__(self, lock_path):
        self.path = os.path.abspath(lock_path)
        self.file = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.file = open(self.path, "a+b")
        try:
            self.file.seek(0, os.SEEK_END)
            if self.file.tell() == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("另一个更新器正在运行，请等待其结束。") from exc
        return self

    def __exit__(self, *args):
        if self.file is not None:
            try:
                self.file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


def env_for_pip():
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_cmd(args, timeout=300):
    log("$ " + subprocess.list2cmdline([str(arg) for arg in args]))
    try:
        process = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=timeout, env=env_for_pip())
        output = ((process.stdout or "") + (process.stderr or "")).strip()
        for line in output.splitlines()[-40:]:
            log("  | " + line, "CMD")
        return process.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"
    except OSError as exc:
        return False, str(exc)


class _ElevatedExit(SystemExit):
    """The elevated child owns interactive output and its final pause."""


def ensure_admin():
    if os.name != "nt":
        return
    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    log("正在以管理员身份重新启动更新器。")
    if getattr(sys, "frozen", False):
        arguments = sys.argv[1:]
        if len(sys.argv) == 1 and not QUIET:
            arguments.append("--pause")
    else:
        arguments = [os.path.abspath(__file__), *sys.argv[1:]]
    arguments += ["--no-elevate", "--app-dir", APP_DIR, "--data-dir", DATA_DIR]
    # ShellExecuteEx permits waiting and preserving the elevated child's exit code.
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.nShow = 1
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "管理员启动未完成，请右键选择以管理员身份运行。")
    try:
        kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD(1)
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), "读取更新进程结果失败")
        raise _ElevatedExit(code.value)
    finally:
        kernel.CloseHandle(info.hProcess)


def vtuple(version):
    parts = re.findall(r"\d+", version or "")[:4]
    values = tuple(int(part) for part in parts)
    return values + (0,) * (4 - len(values))


def backend_version():
    try:
        process = subprocess.run(
            [PY_EXE, "-s", "-c", "import importlib.metadata as m; print(m.version('whimbox'))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, env=env_for_pip())
        lines = (process.stdout or "").strip().splitlines()
        if process.returncode == 0 and lines and re.fullmatch(r"\d+(?:\.\d+){1,3}", lines[-1].strip()):
            return lines[-1].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    versions = [os.path.basename(path)[8:-10] for path in glob.glob(os.path.join(SITE_PACKAGES, "whimbox-*.dist-info"))]
    versions = [version for version in versions if re.fullmatch(r"\d+(?:\.\d+){1,3}", version)]
    if len(versions) > 1:
        log("检测到多份后端版本信息：%s；建议先修复安装环境。" % ", ".join(versions), "WARN")
    return max(versions, key=vtuple) if versions else "unknown"


def frontend_version():
    try:
        with open(ASAR, "rb") as source:
            header = source.read(8)
            length = struct.unpack("<I", header[4:8])[0]
            if not 8 <= length <= 32 * 1024 * 1024:
                raise ValueError("ASAR header length")
            base = 8 + length
            source.seek(16)
            data, _ = json.JSONDecoder().raw_decode(source.read(length - 8).decode("utf-8"))
            package = data["files"]["package.json"]
            size = int(package["size"])
            if not 0 < size < 1024 * 1024:
                raise ValueError("package.json length")
            source.seek(base + int(package["offset"]))
            return str(json.loads(source.read(size).decode("utf-8"))["version"])
    except (OSError, ValueError, KeyError, struct.error):
        pass
    for path in sorted(glob.glob(LOG_DIR_GLOB), reverse=True)[:7]:
        try:
            with open(path, encoding="utf-8", errors="replace") as source:
                versions = re.findall(r"Whimbox App version=(\d+\.\d+\.\d+)", source.read())
            if versions:
                return versions[-1]
        except OSError:
            pass
    return "unknown"


def http_get(url, timeout=20, headers=None, stream=False):
    request_headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    request_headers.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=request_headers), timeout=timeout)


def http_json(url, timeout=20, headers=None):
    with http_get(url, timeout=timeout, headers=headers) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("远端 JSON 超过大小限制")
        return json.loads(raw.decode("utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_releases(releases):
    slim = [{key: release.get(key) for key in ("tag_name", "name", "published_at", "body", "draft", "prerelease", "assets")}
            for release in releases[:30]]
    atomic_write_json(RELEASE_CACHE, slim)


def fetch_releases():
    last_error = None
    for url in [RELEASES_API] + [prefix + RELEASES_API for prefix in PROXIES if prefix]:
        try:
            data = http_json(url)
            if not isinstance(data, list):
                raise ValueError("Release 响应类型异常")
            valid = [release for release in data if isinstance(release, dict)
                     and not release.get("draft") and not release.get("prerelease")]
            valid.sort(key=lambda release: release.get("published_at") or "", reverse=True)
            if valid:
                cache_releases(valid)
                return valid
        except (OSError, ValueError) as exc:
            last_error = exc
    log("版本查询通道暂不可用，尝试本地缓存：%s" % last_error, "WARN")
    try:
        cached = _read_json(RELEASE_CACHE)
        if isinstance(cached, list) and cached:
            log("正在使用缓存版本信息，可能不是最新发布。", "WARN")
            return cached
    except (OSError, ValueError):
        pass
    raise RuntimeError("版本查询失败且没有有效缓存")


def asset_version(release, asset, patterns):
    for pattern in patterns:
        match = re.search(pattern, asset.get("name", ""), re.I)
        if match:
            return match.group(1)
    return (release.get("tag_name") or "unknown").lstrip("v")


def pick_asset(releases, suffix, patterns):
    for release in releases:
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if (name.lower().endswith(suffix) and _safe_filename(name)
                    and any(re.fullmatch(pattern, name, re.I) for pattern in patterns)):
                return release, asset, asset_version(release, asset, patterns)
    return None, None, None


def asset_digest(asset):
    digest = asset.get("digest") or ""
    return digest[7:].lower() if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) else None


def _safe_filename(name):
    return (isinstance(name, str) and name not in ("", ".", "..")
            and not any(char in name for char in '/\\:\x00<>"|?*')
            and name == name.strip() and not name.endswith("."))


def verify_wheel(path):
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("wheel 解压大小超过限制")
        if archive.testzip() is not None:
            raise ValueError("wheel 压缩包损坏")
        metadata = [name for name in archive.namelist() if re.fullmatch(r"whimbox-[^/]+\.dist-info/METADATA", name, re.I)]
        if len(metadata) != 1:
            raise ValueError("wheel 缺少唯一的 whimbox METADATA")
        text = archive.read(metadata[0]).decode("utf-8")
        version = re.search(r"^Version:\s*(\S+)", text, re.M)
        if not version or not re.search(r"^Name:\s*whimbox\s*$", text, re.M | re.I):
            raise ValueError("wheel 不是 whimbox 包")
        return version.group(1)


def download_asset(url, dest, expected_size=None, expected_sha=None, is_whl=False):
    if not expected_sha or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise RuntimeError("安装包缺少有效 SHA256，已保留当前安装；请检查上游 Release。")
    if not url.startswith("https://github.com/nikkigallery/Whimbox/releases/download/"):
        raise ValueError("安装包地址不是预期的官方 Release 地址")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    expected_sha = expected_sha.lower()

    def verify(path):
        if expected_size and os.path.getsize(path) != expected_size:
            raise ValueError("安装包文件大小不符")
        if sha256_file(path) != expected_sha:
            raise ValueError("安装包 SHA256 校验失败")
        return verify_wheel(path) if is_whl else None

    if os.path.isfile(dest):
        try:
            version = verify(dest)
            log("已验证下载缓存：%s" % os.path.basename(dest))
            return version
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
    for prefix in PROXIES:
        fd, temporary = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=os.path.dirname(os.path.abspath(dest)))
        os.close(fd)
        channel = prefix or "GitHub 直连"
        try:
            log("下载通道：%s" % channel)
            request = urllib.request.Request(prefix + url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=25 if prefix else 12) as response, open(temporary, "wb") as output:
                total = 0
                deadline = time.monotonic() + 240
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("下载超过单通道时间预算")
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > (expected_size or 512 * 1024 * 1024):
                        raise ValueError("下载内容超过预期大小")
                    output.write(chunk)
            version = verify(temporary)
            os.replace(temporary, dest)
            log("下载与 SHA256 校验完成：%s" % os.path.basename(dest))
            return version
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            log("下载通道失败：%s" % str(exc)[:200], "WARN")
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    raise RuntimeError("所有安装包下载通道均失败；当前安装保持原状。")


def _copytree(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _snapshot_hashes(directory):
    output = {}
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        for name in sorted(files):
            if name == "backup.json" or name.endswith(".pyc"):
                continue
            path = os.path.join(root, name)
            output[os.path.relpath(path, directory).replace(os.sep, "/")] = sha256_file(path)
    return output


def backup_backend(ver):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    identifier = "backend-%s-from-%s-%s" % (stamp, ver, uuid.uuid4().hex[:8])
    final = os.path.join(BACKUP_DIR, identifier)
    temporary = tempfile.mkdtemp(prefix=".pending-", dir=BACKUP_DIR)
    try:
        package = os.path.join(SITE_PACKAGES, "whimbox")
        info = os.path.join(SITE_PACKAGES, "whimbox-%s.dist-info" % ver)
        existed = os.path.isdir(package) or bool(glob.glob(os.path.join(SITE_PACKAGES, "whimbox-*.dist-info")))
        if existed:
            if not (os.path.isdir(package) and os.path.isdir(info)):
                raise RuntimeError("现有后端目录或版本信息不完整，已停止安装以保留当前环境。")
            _copytree(package, os.path.join(temporary, "whimbox"))
            _copytree(info, os.path.join(temporary, os.path.basename(info)))
        atomic_write_json(os.path.join(temporary, "backup.json"), {
            "version": ver, "created": stamp, "type": "backend", "format": 2,
            "had_backend": existed, "files": _snapshot_hashes(temporary),
            "app_dir": APP_DIR, "dependency_policy": "unchanged --no-deps",
        })
        os.replace(temporary, final)
        log("已完成并校验后端备份：%s" % final)
        return final
    finally:
        if os.path.isdir(temporary):
            shutil.rmtree(temporary)


def _backup_candidates():
    output = []
    for directory in dict.fromkeys((BACKUP_DIR, LEGACY_BACKUP_DIR)):
        output.extend(path for path in glob.glob(os.path.join(directory, "backend-*"))
                      if os.path.isfile(os.path.join(path, "backup.json")))
    return output


def latest_backup():
    candidates = _backup_candidates()
    return max(candidates, key=os.path.getmtime) if candidates else None


def rollback_backend(new_ver, backup_dir=None):
    directory = backup_dir or latest_backup()
    if directory is None:
        log("没有可用后端备份。", "ERROR")
        return False
    stage = None
    preserve_stage = False
    moved = []
    installed = []
    try:
        metadata = _read_json(os.path.join(directory, "backup.json"))
        old = metadata["version"]
        if metadata.get("app_dir") and os.path.normcase(os.path.abspath(metadata["app_dir"])) != os.path.normcase(APP_DIR):
            raise ValueError("备份属于另一个安装目录")
        had_backend = metadata.get("had_backend", True)
        names = ["whimbox", "whimbox-%s.dist-info" % old] if had_backend else []
        if names and not all(os.path.isdir(os.path.join(directory, name)) for name in names):
            raise ValueError("备份缺少后端目录或版本信息，停止恢复")
        if "files" in metadata and _snapshot_hashes(directory) != metadata["files"]:
            raise ValueError("备份完整性校验失败，停止恢复")
        os.makedirs(SITE_PACKAGES, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".whimbox-restore-", dir=SITE_PACKAGES)
        for name in names:
            _copytree(os.path.join(directory, name), os.path.join(stage, name))
        current = [os.path.join(SITE_PACKAGES, "whimbox"),
                   *glob.glob(os.path.join(SITE_PACKAGES, "whimbox-*.dist-info"))]
        for index, path in enumerate(current):
            if os.path.exists(path):
                saved = os.path.join(stage, "previous-%d" % index)
                os.replace(path, saved)
                moved.append((path, saved))
        for name in names:
            destination = os.path.join(SITE_PACKAGES, name)
            os.replace(os.path.join(stage, name), destination)
            installed.append(destination)
        got = backend_version()
        if (had_backend and vtuple(got) != vtuple(old)) or (not had_backend and got != "unknown"):
            raise RuntimeError("恢复后版本验证失败：期望 %s，实际 %s" % (old, got))
        log("后端已恢复：%s（备份 %s）" % (old, directory))
        return True
    except Exception as exc:
        log("后端恢复失败：%s" % exc, "ERROR")
        recovery_errors = []
        for path in installed:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
            except OSError as recovery_error:
                recovery_errors.append(str(recovery_error))
                log("清理未完成的恢复目录失败：%s：%s" % (path, recovery_error), "ERROR")
        for path, saved in reversed(moved):
            try:
                if os.path.exists(saved):
                    os.replace(saved, path)
            except OSError as recovery_error:
                recovery_errors.append(str(recovery_error))
                log("撤回恢复操作失败，暂存 %s -> %s：%s" % (saved, path, recovery_error), "ERROR")
        preserve_stage = bool(recovery_errors)
        if preserve_stage:
            try:
                atomic_write_json(os.path.join(stage, "recovery.json"), {
                    "app_dir": APP_DIR, "backup_dir": directory,
                    "previous_paths": [{"destination": path, "saved": saved} for path, saved in moved],
                    "errors": recovery_errors,
                })
            except OSError as recovery_error:
                log("恢复说明写入失败：%s" % recovery_error, "ERROR")
            log("撤回未完成，已保留当前后端的恢复目录：%s。请勿删除，并退出奇想盒后修复安装。" % stage, "ERROR")
        return False
    finally:
        if stage and not preserve_stage and os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)


def _prune_backups(protected=None):
    candidates = [path for path in _backup_candidates() if os.path.dirname(path) == BACKUP_DIR]
    candidates.sort(key=os.path.getmtime, reverse=True)
    for path in candidates[BACKUP_KEEP:]:
        if path != protected:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                log("旧备份清理失败：%s" % exc, "WARN")


def install_whl(path, force=False):
    # Install only the application package. A rollback must leave dependencies intact.
    command = [PY_EXE, "-s", "-m", "pip", "install", "--no-index", "--no-deps",
               "--disable-pip-version-check"]
    if force:
        command.append("--force-reinstall")
    ok, _ = run_cmd(command + [path], timeout=300)
    if not ok:
        return False
    ok, _ = run_cmd([PY_EXE, "-s", "-m", "pip", "check"], timeout=90)
    if not ok:
        log("依赖检查未通过，将恢复更新前版本；请使用官方安装器补齐依赖。", "ERROR")
        return False
    ok, _ = run_cmd([PY_EXE, "-s", "-c", "import whimbox; print('whimbox import OK')"], timeout=60)
    return ok


def download_scripts_archive():
    os.makedirs(DATA_DIR, exist_ok=True)
    for prefix in PROXIES:
        fd, temporary = tempfile.mkstemp(prefix=".scripts-", suffix=".zip", dir=DATA_DIR)
        os.close(fd)
        try:
            log("拉取路线仓库：%s" % (prefix or "GitHub 直连"))
            request = urllib.request.Request(prefix + SCRIPTS_ARCHIVE_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=25 if prefix else 12) as response, open(temporary, "wb") as output:
                total = 0
                deadline = time.monotonic() + 90
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES or time.monotonic() > deadline:
                        raise ValueError("路线归档超过大小或时间预算")
                    output.write(chunk)
            with zipfile.ZipFile(temporary) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_ARCHIVE_BYTES:
                    raise ValueError("路线归档解压大小超过限制")
                scripts = {}
                for entry in archive.infolist():
                    parts = entry.filename.split("/")
                    if len(parts) != 2 or not parts[1].lower().endswith(".json"):
                        continue
                    name = parts[1]
                    if not _safe_filename(name) or entry.file_size > MAX_SCRIPT_BYTES:
                        raise ValueError("路线文件名或大小异常")
                    data = archive.read(entry)
                    json.loads(data.decode("utf-8-sig"))
                    scripts[name] = data
            if not scripts:
                raise ValueError("归档中没有有效路线脚本")
            return scripts
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            log("路线通道失败：%s" % str(exc)[:200], "WARN")
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    raise RuntimeError("路线仓库下载失败，已保留本地脚本。")


def load_watchlist():
    if not os.path.isfile(WATCHLIST_FILE):
        return []
    try:
        data = _read_json(WATCHLIST_FILE)
        names = data.get("scripts") if isinstance(data, dict) else data
        if not isinstance(names, list) or not all(isinstance(name, str) and _safe_filename(name)
                                               and name.lower().endswith(".json") for name in names):
            raise ValueError("scripts 应为 JSON 文件名字符串列表")
        return list(dict.fromkeys(names))
    except (OSError, ValueError) as exc:
        raise ValueError("订阅清单读取失败，本次路线同步已停止：%s" % exc) from exc


def save_watchlist(names):
    if not all(isinstance(name, str) and _safe_filename(name) and name.lower().endswith(".json") for name in names):
        raise ValueError("订阅名称应是文件名，如 家园日常.json")
    atomic_write_json(WATCHLIST_FILE, {
        "scripts": list(dict.fromkeys(names)), "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "scripts 留空 [] 表示全量同步。配置损坏时停止同步。",
    })


def sync_scripts(force=False, only=None):
    # Validate the subscription before starting network/file operations.
    if only is not None and not all(_safe_filename(name) for name in only):
        raise ValueError("订阅名称无效")
    remote_files = download_scripts_archive()
    if only:
        wanted = set(only)
        missing = wanted - set(remote_files)
        remote_files = {name: data for name, data in remote_files.items() if name in wanted}
    else:
        missing = set()
    counts = {"added": 0, "updated": 0, "same": 0, "failed": len(missing)}
    if missing:
        log("订阅脚本在上游缺失：%s" % "、".join(sorted(missing)), "WARN")
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    backup = os.path.join(BACKUP_DIR, "scripts-%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8]))
    for name, remote in remote_files.items():
        try:
            if not _safe_filename(name):
                raise ValueError("路线文件名无效")
            json.loads(remote.decode("utf-8-sig"))
            destination = os.path.join(SCRIPTS_DIR, name)
            if os.path.isfile(destination):
                with open(destination, "rb") as source:
                    previous = source.read()
                if previous == remote and not force:
                    counts["same"] += 1
                    continue
                atomic_write_bytes(os.path.join(backup, name), previous)
                action = "updated"
            else:
                action = "added"
            atomic_write_bytes(destination, remote)
            counts[action] += 1
            log("%s路线：%s" % ("新增" if action == "added" else "更新", name))
        except (OSError, ValueError) as exc:
            counts["failed"] += 1
            log("路线 %s 同步失败，原文件已保留：%s" % (name, exc), "ERROR")
    log("路线同步：新增 %(added)d / 更新 %(updated)d / 相同 %(same)d / 失败 %(failed)d" % counts)
    return counts


def install_frontend(exe_path, expected_ver):
    if not os.path.isfile(exe_path):
        return False
    log("正在静默安装前端，请等待安装进程结束。")
    try:
        process = subprocess.run([exe_path, "/S"], timeout=420, env=env_for_pip())
        if process.returncode != 0:
            log("前端安装器返回错误：%s" % process.returncode, "ERROR")
            return False
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("前端安装进程异常：%s" % exc, "ERROR")
        return False
    got = frontend_version()
    ok = got != "unknown" and vtuple(got) >= vtuple(expected_ver)
    log("前端安装%s：当前 %s，目标 %s" % ("已验证" if ok else "验证失败", got, expected_ver), "INFO" if ok else "ERROR")
    return ok


def save_state(data):
    try:
        old = _read_json(STATE_FILE)
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}
    for key in ("backend_error", "frontend_error", "scripts_error", "rollback_error", "scripts"):
        old.pop(key, None)
    old.update(data)
    old["last_check"] = datetime.now().isoformat(timespec="seconds")
    old["updater_version"] = VERSION
    atomic_write_json(STATE_FILE, old)


def _migrate_legacy_data():
    legacy = os.path.join(SCRIPT_DIR, "_data")
    if os.path.abspath(legacy) == DATA_DIR:
        return
    for name in ("watchlist.json", "releases_cache.json"):
        source = os.path.join(legacy, name)
        target = os.path.join(DATA_DIR, name)
        if os.path.isfile(source) and not os.path.exists(target):
            with open(source, "rb") as stream:
                atomic_write_bytes(target, stream.read())
            log("已迁移旧版配置：%s" % name)


def _validate_app():
    missing = [path for path in (PY_EXE, ASAR) if not os.path.isfile(path)]
    if missing:
        raise ValueError("未找到奇想盒安装：%s。请放在安装目录内运行，或使用 --app-dir 指定安装目录。" % APP_DIR)


def _parser():
    parser = argparse.ArgumentParser(description="Whimbox 奇想盒更新器（源码与 EXE 通用）")
    parser.add_argument("--version", action="version", version="Whimbox Updater " + VERSION)
    parser.add_argument("--app-dir", help="奇想盒安装目录；默认从更新器位置向上查找")
    parser.add_argument("--data-dir", help="配置、缓存、日志和备份目录；默认使用用户数据目录")
    parser.add_argument("--print-data-dir", action="store_true", help="输出数据目录路径并退出")
    parser.add_argument("--check", action="store_true", help="仅检查版本，不修改奇想盒安装（会保存日志与缓存）")
    parser.add_argument("--no-scripts", action="store_true", help="跳过路线同步")
    parser.add_argument("--watch", metavar="名字,名字", help="设置订阅；空字符串表示全量")
    parser.add_argument("--all-scripts", action="store_true", help="本次忽略订阅，进行全量同步")
    parser.add_argument("--edit-subscriptions", action="store_true", help="打开订阅配置，保存关闭后同步路线")
    parser.add_argument("--force", action="store_true", help="相同版本也强制重装后端")
    parser.add_argument("--install-frontend", action="store_true", help="同时安装新版前端（前端安装由官方安装器执行）")
    parser.add_argument("--rollback", action="store_true", help="恢复最近一次完整后端备份")
    parser.add_argument("--quiet", action="store_true", help="减少控制台输出，仍记录日志")
    parser.add_argument("--no-elevate", action="store_true", help="跳过自动提权，供已提权任务使用")
    parser.add_argument("--pause", action="store_true", help="完成后等待回车，供双击启动使用")
    return parser


def _execute(args):
    _validate_app()
    if args.check and (args.rollback or args.watch is not None or args.edit_subscriptions):
        raise ValueError("--check 请单独使用，避免与配置或回滚操作混用。")
    # Checking and editing user configuration do not require writing Program Files.
    modifies_app = not args.check and not (args.watch is not None and args.no_scripts)
    if modifies_app and not args.no_elevate:
        ensure_admin()
    os.makedirs(DATA_DIR, exist_ok=True)
    with UpdateLock(os.path.join(DATA_DIR, "updater.lock")):
        _migrate_legacy_data()
        if args.edit_subscriptions:
            if os.name != "nt":
                raise ValueError("订阅编辑器入口用于 Windows；其他平台可直接编辑配置。")
            if not os.path.isfile(WATCHLIST_FILE):
                save_watchlist([])
            subprocess.run(["notepad.exe", WATCHLIST_FILE], check=True)
            load_watchlist()  # Stop before any sync if the saved JSON is invalid.
            args.no_scripts = False
        if args.watch is not None:
            names = [name.strip() for name in args.watch.replace("，", ",").split(",") if name.strip()]
            names = [name if name.lower().endswith(".json") else name + ".json" for name in names]
            save_watchlist(names)
            log("订阅已保存：%s" % WATCHLIST_FILE)
            if args.no_scripts or not names:
                return 0
        if args.rollback:
            return 0 if rollback_backend(backend_version()) else 1
        return _update(args)


def _update(args):
    bver, fver = backend_version(), frontend_version()
    log("本地版本：后端 %s / 前端 %s" % (bver, fver))
    releases = fetch_releases()
    whl_release, wheel, wheel_version = pick_asset(releases, ".whl", [r"whimbox-([\d.]+)-py3-none-any\.whl"])
    _, executable, exe_version = pick_asset(releases, ".exe", [r"whimbox_app-setup-([\d.]+)\.exe"])
    if wheel is None:
        raise RuntimeError("Release 中没有找到有效后端 wheel；已保留当前安装。")
    result = {"run_id": uuid.uuid4().hex, "backend_version": bver, "frontend_version": fver,
              "latest_release": releases[0].get("tag_name"), "latest_whl": wheel_version,
              "latest_exe": exe_version, "status": "success"}
    need_backend = args.force or vtuple(wheel_version) > vtuple(bver)
    need_frontend = executable is not None and vtuple(exe_version) > vtuple(fver)
    log("远端版本：后端 %s / 前端 %s" % (wheel_version, exe_version or "无安装包"))
    if args.check:
        result["backend_update_available"] = need_backend
        result["frontend_update_available"] = need_frontend
        save_state(result)
        log("检查完成，奇想盒安装未修改。")
        return 0
    if need_backend:
        backup = None
        installation_started = False
        try:
            destination = os.path.join(DOWNLOAD_DIR, wheel["name"])
            verified_version = download_asset(wheel["browser_download_url"], destination,
                                              wheel.get("size"), asset_digest(wheel), is_whl=True)
            if verified_version and vtuple(verified_version) != vtuple(wheel_version):
                raise ValueError("wheel 内部版本与文件名不一致，已停止安装。")
            backup = backup_backend(bver)
            installation_started = True
            if not install_whl(destination, force=args.force):
                raise RuntimeError("后端安装或健康检查失败")
            got = backend_version()
            if vtuple(got) != vtuple(wheel_version):
                raise RuntimeError("安装后版本不符：期望 %s，实际 %s" % (wheel_version, got))
            result.update(backend_version=got, last_backup=backup,
                          last_backend_update=datetime.now().isoformat(timespec="seconds"))
            log("后端更新已完成并验证：%s -> %s" % (bver, got))
            _prune_backups(protected=backup)
        except Exception as exc:
            result["backend_error"] = str(exc)
            log("后端更新失败：%s" % exc, "ERROR")
            if installation_started and backup:
                if not rollback_backend(wheel_version, backup_dir=backup):
                    result["rollback_error"] = "本次备份恢复失败，请查看日志并使用官方安装器修复。"
            else:
                log("安装尚未开始，当前后端保持原状。")
            result["backend_version"] = backend_version()
    else:
        log("后端已是最新版本。")
    if args.install_frontend and need_frontend:
        try:
            destination = os.path.join(DOWNLOAD_DIR, executable["name"])
            download_asset(executable["browser_download_url"], destination,
                           executable.get("size"), asset_digest(executable))
            if not install_frontend(destination, exe_version):
                raise RuntimeError("前端安装器或安装后版本验证失败")
            result["frontend_version"] = frontend_version()
        except Exception as exc:
            result["frontend_error"] = str(exc)
            log("前端更新失败：%s" % exc, "ERROR")
    elif need_frontend:
        log("前端可更新至 %s；需要时添加 --install-frontend。" % exe_version)
    if not args.no_scripts:
        try:
            only = None if args.all_scripts else (load_watchlist() or None)
            result["scripts"] = sync_scripts(only=only)
            if result["scripts"]["failed"]:
                result["scripts_error"] = "%d 个路线脚本同步失败" % result["scripts"]["failed"]
        except Exception as exc:
            result["scripts_error"] = str(exc)
            log("路线同步失败：%s" % exc, "ERROR")
    failed = any(key in result for key in ("backend_error", "frontend_error", "scripts_error", "rollback_error"))
    result["status"] = "failed" if failed else "success"
    save_state(result)
    log("%s。日志与状态目录：%s" % ("本次存在失败步骤" if failed else "更新完成", DATA_DIR), "ERROR" if failed else "INFO")
    if need_backend and "backend_error" not in result:
        log("请重启奇想盒以使用新版后端。")
    return 1 if failed else 0


def main():
    global QUIET
    args = _parser().parse_args()
    QUIET = args.quiet
    # Preserve explicitly configured module paths for embedding/tests; normal CLI
    # starts with the default configuration initialized at import time.
    if args.app_dir or args.data_dir:
        configure_paths(args.app_dir or APP_DIR, args.data_dir)
    if args.print_data_dir:
        print(DATA_DIR)
        return 0
    log("Whimbox Updater %s" % VERSION)
    try:
        return _execute(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        log(str(exc), "ERROR")
        return 1


def entrypoint():
    code = 1
    elevated_parent = False
    try:
        code = main()
    except _ElevatedExit as exc:
        code = exc.code
        elevated_parent = True
    except KeyboardInterrupt:
        log("用户中断。", "WARN")
        code = 130
    except Exception as exc:
        import traceback
        log("未预期异常：%s\n%s" % (exc, traceback.format_exc()), "ERROR")
    finally:
        double_click = getattr(sys, "frozen", False) and len(sys.argv) == 1
        if not elevated_parent and ("--pause" in sys.argv or (double_click and not QUIET)):
            try:
                input("\n按回车关闭窗口…")
            except (EOFError, KeyboardInterrupt):
                pass
    return code


if __name__ == "__main__":
    sys.exit(entrypoint())
