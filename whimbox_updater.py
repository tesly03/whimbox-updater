# -*- coding: utf-8 -*-
"""Whimbox updater: verified downloads, transactional recovery and script sync.

Run the source with Whimbox's embedded Python, or use the Windows executable.
Only the build process needs PyInstaller; the updater uses the standard library.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timedelta
import glob
import hashlib
import json
import os
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

VERSION = "1.2.0"
DEFAULT_APP_DIR = r"C:\Program Files\whimbox_app"
RELEASES_API = "https://api.github.com/repos/nikkigallery/Whimbox/releases?per_page=30"
SCRIPTS_ARCHIVE_URL = "https://github.com/nikkigallery/WhimboxScripts/archive/refs/heads/main.zip"
SCRIPTS_COMMIT_API = "https://api.github.com/repos/nikkigallery/WhimboxScripts/commits/main"
PROXIES = ["https://gh-proxy.com/", "https://ghfast.top/", "https://ghproxy.net/", ""]
PIP_MIRRORS = [
    "https://mirrors.ustc.edu.cn/pypi/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
]
UA = "WhimboxUpdater/" + VERSION
CHUNK = 256 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_SCRIPT_BYTES = 32 * 1024 * 1024
BACKUP_KEEP = 3
SCRIPT_BACKUP_KEEP = 10
DOWNLOAD_KEEP = 3
LOG_KEEP_DAYS = 14
QUIET = False
_LOG_PATH = None
_DIRECT_ONLY = False
_CUSTOM_PROXIES = None


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
    global APP_DIR, APP_EXE, PY_EXE, SITE_PACKAGES, ASAR, SCRIPTS_DIR, DOWNLOAD_DIR
    global LOG_DIR_GLOB, SCRIPT_DIR, DATA_DIR, BACKUP_DIR, LEGACY_BACKUP_DIR
    global LOG_DIR, WATCHLIST_FILE, STATE_FILE, RELEASE_CACHE, RELEASE_META, SCRIPTS_STATE, _LOG_PATH
    APP_DIR = os.path.abspath(app_dir or _detect_app_dir())
    APP_EXE = os.path.join(APP_DIR, "whimbox_app.exe")
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
    RELEASE_META = os.path.join(DATA_DIR, "releases_meta.json")
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


def _is_transient_lock_error(exc):
    if isinstance(exc, PermissionError):
        return True
    winerror = getattr(exc, "winerror", None)
    return winerror in (5, 32, 33)  # Access denied / sharing violation / lock violation


def _retry_fs(func, *args, retries=3, base_delay=0.1, **kwargs):
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except OSError as exc:
            if attempt >= retries or not _is_transient_lock_error(exc):
                raise
            time.sleep(base_delay * (attempt + 1))


def atomic_write_bytes(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".updater-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        _retry_fs(os.replace, temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def atomic_write_json(path, data):
    atomic_write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _read_json(path):
    with open(path, "r", encoding="utf-8-sig") as source:
        return json.load(source)


def _normalize_proxy_url(raw):
    value = (raw or "").strip()
    if not value or value.lower() in ("direct", "none"):
        return ""
    if not value.startswith("https://"):
        raise ValueError("代理地址必须以 https:// 开头：%s" % value)
    return value if value.endswith("/") else value + "/"


def get_proxies():
    if _DIRECT_ONLY:
        return [""]
    if _CUSTOM_PROXIES is not None:
        return list(_CUSTOM_PROXIES)
    env_value = os.environ.get("WHIMBOX_PROXIES")
    if env_value is not None:
        stripped = env_value.strip()
        if stripped.lower() in ("direct", "none"):
            return [""]
        if stripped:
            items = [_normalize_proxy_url(part) for part in stripped.split(",")]
            result = list(dict.fromkeys(items))
            if "" not in result:
                result.append("")
            return result
    return list(PROXIES)


def _github_token():
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def _is_official_github_api(url):
    return isinstance(url, str) and url.startswith("https://api.github.com/")


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
    """Build an isolated environment matching Whimbox's embedded Python setup."""
    environment = os.environ.copy()
    for var in ("PYTHONPATH", "_MEIPASS", "_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY"):
        environment.pop(var, None)
    python_dir = os.path.dirname(os.path.abspath(PY_EXE))
    scripts_dir = os.path.join(python_dir, "Scripts")
    existing_path = environment.get("PATH", "")
    path_parts = [python_dir, scripts_dir]
    if existing_path:
        path_parts.append(existing_path)
    environment["PATH"] = os.pathsep.join(path_parts)
    environment["PYTHONHOME"] = python_dir
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def run_cmd(args, timeout=300, cwd=None):
    log("$ " + subprocess.list2cmdline([str(arg) for arg in args]))
    try:
        process = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env_for_pip(), cwd=cwd,
        )
        output = ((process.stdout or "") + "\n" + (process.stderr or "")).strip()
        for line in output.splitlines()[-40:]:
            log("  | " + line, "CMD")
        return process.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"
    except OSError as exc:
        return False, str(exc)


def _dir_is_writable(path):
    target = os.path.abspath(path)
    while not os.path.isdir(target):
        parent = os.path.dirname(target)
        if parent == target:
            return False
        target = parent
    try:
        fd, probe = tempfile.mkstemp(prefix=".updater-perm-", suffix=".tmp", dir=target)
        os.close(fd)
        os.remove(probe)
        return True
    except OSError:
        return False


def _path_is_within(path, root):
    try:
        norm_path = os.path.normcase(os.path.abspath(path))
        norm_root = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([norm_path, norm_root]) == norm_root
    except ValueError:
        return False


def find_running_whimbox_processes(app_dir=None):
    """Detect running Whimbox frontend/backend processes under APP_DIR on Windows."""
    if os.name != "nt":
        return []
    target_app_dir = os.path.abspath(app_dir or APP_DIR)
    updater_dir = os.path.abspath(SCRIPT_DIR)
    updater_subdir = (_path_is_within(updater_dir, target_app_dir)
                      and os.path.normcase(updater_dir) != os.path.normcase(target_app_dir))
    whimbox_exe_names = {"whimbox_app.exe", "whimbox_launcher.exe"}
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    invalid_handle = ctypes.c_void_p(-1).value
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == invalid_handle or not snapshot:
        return []
    entries = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                entries[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile)
                if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel.CloseHandle(snapshot)

    # Exclude our own process and ancestor chain (important in source mode using embedded python).
    excluded = set()
    cur_pid = os.getpid()
    for _ in range(16):
        if cur_pid <= 0 or cur_pid in excluded:
            break
        excluded.add(cur_pid)
        cur_pid = entries.get(cur_pid, (0, ""))[0]

    running = []
    for pid, (_, exe_name) in entries.items():
        if pid in excluded or pid == 0:
            continue
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        image_path = None
        if handle:
            try:
                buf_len = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(buf_len.value)
                if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len)):
                    image_path = buf.value
            finally:
                kernel.CloseHandle(handle)
        if image_path:
            if updater_subdir and _path_is_within(image_path, updater_dir):
                continue
            if _path_is_within(image_path, target_app_dir):
                running.append((pid, exe_name, image_path))
        elif exe_name.lower() in whimbox_exe_names:
            running.append((pid, exe_name, None))
    return running


def _ensure_whimbox_not_running():
    running = find_running_whimbox_processes(APP_DIR)
    if running:
        summary = "、".join("%s (PID %d)" % (name, pid) for pid, name, _ in running[:5])
        raise RuntimeError("检测到奇想盒正在运行（%s），请先完全退出奇想盒后再更新或回滚。" % summary)


class _ElevatedExit(SystemExit):
    """The elevated child owns interactive output and its final pause."""


def _child_args_after_config_edit(raw_args):
    """Strip --edit-subscriptions / --watch so an elevated child never opens Notepad again."""
    result = []
    index = 0
    saw_edit_or_watch = False
    while index < len(raw_args):
        token = raw_args[index]
        if token == "--edit-subscriptions":
            saw_edit_or_watch = True
            index += 1
        elif token == "--watch":
            saw_edit_or_watch = True
            index += 2
        elif token.startswith("--watch="):
            saw_edit_or_watch = True
            index += 1
        else:
            result.append(token)
            index += 1
    if saw_edit_or_watch and "--scripts-only" not in result:
        result.append("--scripts-only")
    return result


def ensure_admin(strip_config_edit=False):
    if os.name != "nt":
        return
    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    log("正在以管理员身份重新启动更新器。")
    base_args = _child_args_after_config_edit(sys.argv[1:]) if strip_config_edit else list(sys.argv[1:])
    if getattr(sys, "frozen", False):
        arguments = base_args
        if len(sys.argv) == 1 and not QUIET:
            arguments.append("--pause")
    else:
        arguments = [os.path.abspath(__file__), *base_args]
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
            header = source.read(16)
            if len(header) != 16:
                raise ValueError("ASAR header too short")
            _, length, _, json_size = struct.unpack("<4I", header)
            if not 8 <= length <= 32 * 1024 * 1024:
                raise ValueError("ASAR header length")
            if not 0 < json_size <= length - 8:
                raise ValueError("ASAR JSON length")
            base = 8 + length
            data = json.loads(source.read(json_size).decode("utf-8"))
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


def http_get(url, timeout=20, headers=None):
    request_headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if _is_official_github_api(url):
        token = _github_token()
        if token:
            request_headers["Authorization"] = "Bearer " + token
    request_headers.update(headers or {})
    return urllib.request.urlopen(urllib.request.Request(url, headers=request_headers), timeout=timeout)


def http_json(url, timeout=20, headers=None):
    with http_get(url, timeout=timeout, headers=headers) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("远端 JSON 超过大小限制")
        return json.loads(raw.decode("utf-8"))


def _http_json_conditional(url, etag=None, timeout=20, headers=None):
    req_headers = dict(headers or {})
    if etag:
        req_headers["If-None-Match"] = etag
    try:
        with http_get(url, timeout=timeout, headers=req_headers) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("远端 JSON 超过大小限制")
            return json.loads(raw.decode("utf-8")), response.headers.get("ETag"), False
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, etag, True
        raise


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_releases(data):
    if not isinstance(data, list):
        raise ValueError("Release 响应类型异常")
    valid = [release for release in data if isinstance(release, dict)
             and not release.get("draft") and not release.get("prerelease")]
    valid.sort(key=lambda release: release.get("published_at") or "", reverse=True)
    return valid


def cache_releases(releases, etag=None, source="direct"):
    slim = [{key: release.get(key) for key in ("tag_name", "name", "published_at", "body", "draft", "prerelease", "assets")}
            for release in releases[:30]]
    atomic_write_json(RELEASE_CACHE, slim)
    atomic_write_json(RELEASE_META, {
        "etag": etag,
        "source": source,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    })


def _load_cached_releases():
    try:
        cached = _read_json(RELEASE_CACHE)
        if isinstance(cached, list) and cached:
            return cached
    except (OSError, ValueError):
        pass
    return None


def _load_release_meta():
    try:
        meta = _read_json(RELEASE_META)
        if isinstance(meta, dict):
            return meta
    except (OSError, ValueError):
        pass
    return {}


def _asset_digest_map(releases):
    mapping = {}
    for release in releases[:15]:
        tag = release.get("tag_name") or ""
        for asset in release.get("assets", []):
            name = asset.get("name") or ""
            digest = asset_digest(asset)
            if name and digest:
                mapping[(tag, name)] = digest
    return mapping


def _select_verified_proxy_releases(first_releases, second_releases):
    first_map = _asset_digest_map(first_releases)
    second_map = _asset_digest_map(second_releases)
    common = set(first_map) & set(second_map)
    for key in common:
        if first_map[key] != second_map[key]:
            raise RuntimeError(
                "不同代理通道返回的 %s (%s) SHA256 摘要不一致，已中止以防篡改。" % (key[1], key[0])
            )
    if not common:
        raise RuntimeError("不同代理通道返回的 Release 列表无共同可验证条目，已中止。")
    corroborated = []
    for release in first_releases:
        tag = release.get("tag_name") or ""
        assets = release.get("assets", [])
        if any((tag, asset.get("name") or "") in common for asset in assets):
            corroborated.append(release)
    return corroborated or first_releases


def fetch_releases():
    last_error = None
    cached = _load_cached_releases()
    meta = _load_release_meta()
    cached_etag = meta.get("etag") if cached and meta.get("source") == "direct" else None

    # 1. Always prefer official GitHub API directly so SHA256 digests come from GitHub itself.
    try:
        data, new_etag, not_modified = _http_json_conditional(RELEASES_API, etag=cached_etag)
        if not_modified and cached:
            log("Release 信息未变更（304 缓存命中）。")
            return cached
        valid = _normalize_releases(data)
        if valid:
            cache_releases(valid, etag=new_etag, source="direct")
            return valid
    except (OSError, ValueError) as exc:
        last_error = exc

    # 2. Fallback to proxy channels with cross-proxy SHA256 digest consensus when >=2 proxies respond.
    proxy_prefixes = [prefix for prefix in get_proxies() if prefix]
    proxy_results = []
    for prefix in proxy_prefixes:
        try:
            data = http_json(prefix + RELEASES_API)
            valid = _normalize_releases(data)
            if valid:
                proxy_results.append((prefix, valid))
                if len(proxy_results) >= 2:
                    break
        except (OSError, ValueError) as exc:
            last_error = exc

    if proxy_results:
        first_prefix, first_releases = proxy_results[0]
        if len(proxy_results) >= 2:
            second_prefix, second_releases = proxy_results[1]
            chosen = _select_verified_proxy_releases(first_releases, second_releases)
            log("直连 GitHub API 失败，已通过两个代理通道交叉验证 Release 摘要（%s、%s）。"
                % (first_prefix, second_prefix), "WARN")
        else:
            chosen = first_releases
            log("直连 GitHub API 失败，仅通过单个代理通道获取 Release 摘要（%s）；建议直连或设置 GITHUB_TOKEN。"
                % first_prefix, "WARN")
        cache_releases(chosen, etag=None, source="proxy")
        return chosen

    log("版本查询通道暂不可用，尝试本地缓存：%s" % last_error, "WARN")
    if cached:
        log("正在使用缓存版本信息，可能不是最新发布。", "WARN")
        return cached
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


def _summarize_release_notes(body, max_lines=5):
    if not body:
        return []
    cleaned = re.sub(r"<[^>]+>", "", body)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return lines[:max_lines]


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


def _format_size(num_bytes):
    if num_bytes >= 1024 * 1024:
        return "%.1f MB" % (num_bytes / (1024 * 1024))
    if num_bytes >= 1024:
        return "%.1f KB" % (num_bytes / 1024)
    return "%d B" % num_bytes


def download_asset(url, dest, expected_size=None, expected_sha=None, is_whl=False):
    if not expected_sha or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise RuntimeError("安装包缺少有效 SHA256，已保留当前安装；请检查上游 Release。")
    if not url.startswith("https://github.com/nikkigallery/Whimbox/releases/download/"):
        raise ValueError("安装包地址不是预期的官方 Release 地址")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    expected_sha = expected_sha.lower()

    def verify(path, computed_sha=None):
        if expected_size and os.path.getsize(path) != expected_size:
            raise ValueError("安装包文件大小不符")
        actual_sha = computed_sha if computed_sha is not None else sha256_file(path)
        if actual_sha != expected_sha:
            raise ValueError("安装包 SHA256 校验失败")
        return verify_wheel(path) if is_whl else None

    if os.path.isfile(dest):
        try:
            version = verify(dest)
            log("已验证下载缓存：%s" % os.path.basename(dest))
            return version
        except (OSError, ValueError, zipfile.BadZipFile):
            pass

    budget = 420 if (expected_size or 0) > 64 * 1024 * 1024 else 240
    for prefix in get_proxies():
        fd, temporary = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=os.path.dirname(os.path.abspath(dest)))
        os.close(fd)
        channel = prefix or "GitHub 直连"
        try:
            log("下载通道：%s" % channel)
            request = urllib.request.Request(prefix + url, headers={"User-Agent": UA})
            stream_hash = hashlib.sha256()
            with urllib.request.urlopen(request, timeout=25 if prefix else 15) as response, open(temporary, "wb") as output:
                total = 0
                deadline = time.monotonic() + budget
                last_report = time.monotonic()
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("下载超过单通道时间预算")
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > (expected_size or 512 * 1024 * 1024):
                        raise ValueError("下载内容超过预期大小")
                    stream_hash.update(chunk)
                    output.write(chunk)
                    now = time.monotonic()
                    if not QUIET and now - last_report >= 3.0:
                        if expected_size:
                            pct = min(100, int(total * 100 / expected_size))
                            print("  下载进度：%d%% (%s / %s)" % (pct, _format_size(total), _format_size(expected_size)), flush=True)
                        else:
                            print("  已下载：%s" % _format_size(total), flush=True)
                        last_report = now
            version = verify(temporary, computed_sha=stream_hash.hexdigest())
            _retry_fs(os.replace, temporary, dest)
            log("下载与 SHA256 校验完成：%s（%s）" % (os.path.basename(dest), _format_size(os.path.getsize(dest))))
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
        _retry_fs(os.replace, temporary, final)
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


def _backup_sort_key(path):
    created = ""
    try:
        meta = _read_json(os.path.join(path, "backup.json"))
        if isinstance(meta, dict) and isinstance(meta.get("created"), str):
            created = meta["created"]
    except (OSError, ValueError):
        pass
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return (created, mtime)


def latest_backup():
    candidates = _backup_candidates()
    return max(candidates, key=_backup_sort_key) if candidates else None


def rollback_backend(new_ver=None, backup_dir=None):
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
                _retry_fs(os.replace, path, saved)
                moved.append((path, saved))
        for name in names:
            destination = os.path.join(SITE_PACKAGES, name)
            _retry_fs(os.replace, os.path.join(stage, name), destination)
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
                    _retry_fs(shutil.rmtree, path)
            except OSError as recovery_error:
                recovery_errors.append(str(recovery_error))
                log("清理未完成的恢复目录失败：%s：%s" % (path, recovery_error), "ERROR")
        for path, saved in reversed(moved):
            try:
                if os.path.exists(saved):
                    _retry_fs(os.replace, saved, path)
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
    candidates.sort(key=_backup_sort_key, reverse=True)
    for path in candidates[BACKUP_KEEP:]:
        if path != protected:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                log("旧备份清理失败：%s" % exc, "WARN")


def _prune_script_backups():
    candidates = [path for path in glob.glob(os.path.join(BACKUP_DIR, "scripts-*")) if os.path.isdir(path)]
    candidates.sort(key=lambda path: (os.path.basename(path), os.path.getmtime(path)), reverse=True)
    for path in candidates[SCRIPT_BACKUP_KEEP:]:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            log("旧路线备份清理失败：%s" % exc, "WARN")


def _prune_downloads(protected=None):
    keep_set = {os.path.normcase(os.path.abspath(p)) for p in (protected or ()) if p}
    for pattern in ("*.whl", "*.exe"):
        files = [path for path in glob.glob(os.path.join(DOWNLOAD_DIR, pattern)) if os.path.isfile(path)]
        files.sort(key=os.path.getmtime, reverse=True)
        for path in files[DOWNLOAD_KEEP:]:
            if os.path.normcase(os.path.abspath(path)) not in keep_set:
                try:
                    os.remove(path)
                except OSError as exc:
                    log("旧下载缓存清理失败：%s" % exc, "WARN")


def _prune_logs():
    cutoff = (datetime.now() - timedelta(days=LOG_KEEP_DAYS)).strftime("%Y-%m-%d")
    current = os.path.normcase(os.path.abspath(_LOG_PATH)) if _LOG_PATH else None
    for path in glob.glob(os.path.join(LOG_DIR, "updater-*.log")):
        if current and os.path.normcase(os.path.abspath(path)) == current:
            continue
        match = re.fullmatch(r"updater-(\d{4}-\d{2}-\d{2})\.log", os.path.basename(path))
        if match and match.group(1) < cutoff:
            try:
                os.remove(path)
            except OSError:
                pass


def install_whl(path, force=False, sync_deps=False):
    if sync_deps:
        log("已启用 --sync-deps：将通过 PyPI 镜像同步第三方依赖（第三方依赖变更不在后端回滚范围内）。", "WARN")
        installed_ok = False
        for mirror in [*PIP_MIRRORS, ""]:
            command = [PY_EXE, "-s", "-m", "pip", "install", "--disable-pip-version-check"]
            if mirror:
                command += ["-i", mirror]
            if force:
                command.append("--force-reinstall")
            ok, _ = run_cmd(command + [path], timeout=600)
            if ok:
                installed_ok = True
                break
            log("pip 源安装失败（%s），尝试下一个源…" % (mirror or "PyPI 默认源"), "WARN")
        if not installed_ok:
            return False
    else:
        # Install only the application package. A rollback must leave dependencies intact.
        command = [PY_EXE, "-s", "-m", "pip", "install", "--no-index", "--no-deps",
                   "--disable-pip-version-check"]
        if force:
            command.append("--force-reinstall")
        ok, _ = run_cmd(command + [path], timeout=300)
        if not ok:
            return False
    ok, check_output = run_cmd([PY_EXE, "-s", "-m", "pip", "check"], timeout=90)
    if not ok:
        details = "; ".join(line.strip() for line in (check_output or "").splitlines() if line.strip())[:300]
        if sync_deps:
            log("依赖检查未通过（%s），将恢复更新前版本。" % (details or "详见命令输出"), "ERROR")
        else:
            log("依赖检查未通过（%s），将恢复更新前版本。如需同步更新第三方依赖，请添加 --sync-deps，或在奇想盒设置中手动安装：%s"
                % (details or "详见命令输出", path), "ERROR")
        return False
    verify_script = (
        "import importlib.util, whimbox; "
        "assert importlib.util.find_spec('whimbox.main') is not None, 'missing whimbox.main'; "
        "print('whimbox import OK')"
    )
    ok, _ = run_cmd([PY_EXE, "-s", "-c", verify_script], timeout=60, cwd=APP_DIR)
    return ok


def _load_scripts_state():
    try:
        state = _read_json(SCRIPTS_STATE)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def fetch_scripts_commit():
    """Fetch latest commit SHA of WhimboxScripts (with ETag cache) for incremental sync."""
    state = _load_scripts_state()
    cached_sha = state.get("commit_sha") if isinstance(state.get("commit_sha"), str) else None
    cached_etag = state.get("etag") if cached_sha and isinstance(state.get("etag"), str) else None

    try:
        data, new_etag, not_modified = _http_json_conditional(SCRIPTS_COMMIT_API, etag=cached_etag, timeout=10)
        if not_modified and cached_sha:
            return cached_sha, cached_etag
        if isinstance(data, dict) and isinstance(data.get("sha"), str) and data["sha"]:
            return data["sha"], new_etag
    except (OSError, ValueError):
        pass

    for prefix in [p for p in get_proxies() if p]:
        try:
            data = http_json(prefix + SCRIPTS_COMMIT_API, timeout=10)
            if isinstance(data, dict) and isinstance(data.get("sha"), str) and data["sha"]:
                return data["sha"], None
        except (OSError, ValueError):
            pass
    return None, None


def download_scripts_archive():
    os.makedirs(DATA_DIR, exist_ok=True)
    for prefix in get_proxies():
        fd, temporary = tempfile.mkstemp(prefix=".scripts-", suffix=".zip", dir=DATA_DIR)
        os.close(fd)
        try:
            log("拉取路线仓库：%s" % (prefix or "GitHub 直连"))
            request = urllib.request.Request(prefix + SCRIPTS_ARCHIVE_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=25 if prefix else 15) as response, open(temporary, "wb") as output:
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
                if archive.testzip() is not None:
                    raise ValueError("路线归档压缩包损坏")
                scripts = {}
                for entry in archive.infolist():
                    parts = entry.filename.split("/")
                    if len(parts) != 2 or not parts[1].lower().endswith(".json"):
                        continue
                    name = parts[1]
                    if not _safe_filename(name) or entry.file_size > MAX_SCRIPT_BYTES:
                        raise ValueError("路线文件名或大小异常")
                    scripts[name] = archive.read(entry)
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


def _match_wanted_scripts(remote_files, only):
    selected = {}
    missing = set()
    fold_map = {name.casefold(): name for name in remote_files}
    for wanted in only:
        if wanted in remote_files:
            selected[wanted] = remote_files[wanted]
        elif os.name == "nt" and wanted.casefold() in fold_map:
            canonical = fold_map[wanted.casefold()]
            selected[canonical] = remote_files[canonical]
        else:
            missing.add(wanted)
    return selected, missing


def _local_script_exists(name):
    if os.path.isfile(os.path.join(SCRIPTS_DIR, name)):
        return True
    if os.name == "nt" and os.path.isdir(SCRIPTS_DIR):
        target = name.casefold()
        return any(entry.casefold() == target for entry in os.listdir(SCRIPTS_DIR))
    return False


def sync_scripts(force=False, only=None):
    # Validate the subscription before starting network/file operations.
    if only is not None and not all(_safe_filename(name) for name in only):
        raise ValueError("订阅名称无效")

    remote_sha, remote_etag = fetch_scripts_commit()
    if remote_sha and not force:
        saved = _load_scripts_state()
        if saved.get("commit_sha") == remote_sha:
            if only:
                if all(_local_script_exists(name) for name in only):
                    log("路线仓库无新提交（%s），订阅脚本已是最新，跳过下载。" % remote_sha[:7])
                    return {"added": 0, "updated": 0, "same": len(only), "failed": 0, "skipped_download": True}
            else:
                saved_files = saved.get("files") if isinstance(saved.get("files"), list) else []
                if saved.get("scope") == "all" and saved_files and all(_local_script_exists(name) for name in saved_files):
                    log("路线仓库无新提交（%s），全部 %d 条路线已是最新，跳过下载。" % (remote_sha[:7], len(saved_files)))
                    return {"added": 0, "updated": 0, "same": len(saved_files), "failed": 0, "skipped_download": True}

    remote_files = download_scripts_archive()
    if only:
        remote_files, missing = _match_wanted_scripts(remote_files, only)
    else:
        missing = set()
    counts = {"added": 0, "updated": 0, "same": 0, "failed": len(missing)}
    if missing:
        log("订阅脚本在上游缺失：%s" % "、".join(sorted(missing)), "WARN")
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    backup = os.path.join(BACKUP_DIR, "scripts-%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8]))
    backed_up_any = False
    for name, remote in remote_files.items():
        try:
            if not _safe_filename(name):
                raise ValueError("路线文件名无效")
            parsed = json.loads(remote.decode("utf-8-sig"))
            if not isinstance(parsed, dict):
                raise ValueError("路线 JSON 根节点应为对象")
            destination = os.path.join(SCRIPTS_DIR, name)
            if os.path.isfile(destination):
                with open(destination, "rb") as source:
                    previous = source.read()
                if previous == remote and not force:
                    counts["same"] += 1
                    continue
                atomic_write_bytes(os.path.join(backup, name), previous)
                backed_up_any = True
                action = "updated"
            else:
                action = "added"
            atomic_write_bytes(destination, remote)
            counts[action] += 1
            log("%s路线：%s" % ("新增" if action == "added" else "更新", name))
        except (OSError, ValueError) as exc:
            counts["failed"] += 1
            log("路线 %s 同步失败，原文件已保留：%s" % (name, exc), "ERROR")
    if backed_up_any:
        _prune_script_backups()
    if remote_sha and counts["failed"] == 0:
        atomic_write_json(SCRIPTS_STATE, {
            "commit_sha": remote_sha,
            "etag": remote_etag,
            "scope": "only" if only else "all",
            "files": sorted(remote_files.keys()),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    log("路线同步：新增 %(added)d / 更新 %(updated)d / 相同 %(same)d / 失败 %(failed)d" % counts)
    return counts


def _frontend_install_cmdline(exe_path, app_dir):
    """Build NSIS silent installer command line. /D= must be final and unquoted per NSIS spec."""
    target = os.path.abspath(app_dir)
    if any(ord(ch) >= 128 for ch in target) or any(ch in target for ch in '"\r\n\x00'):
        raise ValueError("官方前端安装器仅支持纯英文且不含引号的路径：%s" % target)
    return '"%s" /S /D=%s' % (os.path.abspath(exe_path), target)


def install_frontend(exe_path, expected_ver):
    if not os.path.isfile(exe_path):
        return False
    log("正在静默安装前端到 %s，请等待安装进程结束。" % APP_DIR)
    try:
        cmdline = _frontend_install_cmdline(exe_path, APP_DIR)
        if os.name == "nt":
            process = subprocess.run(cmdline, timeout=420, env=os.environ.copy())
        else:
            process = subprocess.run([exe_path, "/S", "/D=" + APP_DIR], timeout=420, env=os.environ.copy())
        if process.returncode != 0:
            log("前端安装器返回错误：%s" % process.returncode, "ERROR")
            return False
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
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


def _needs_elevation(args):
    if os.name != "nt" or args.no_elevate or args.check:
        return False
    if args.install_frontend:
        return True
    if args.rollback:
        targets = [SITE_PACKAGES]
    elif args.scripts_only:
        targets = [SCRIPTS_DIR]
    else:
        targets = []
        if not args.no_backend:
            targets.append(SITE_PACKAGES)
        if not args.no_scripts:
            targets.append(SCRIPTS_DIR)
    return not all(_dir_is_writable(path) for path in targets)


def _parser():
    parser = argparse.ArgumentParser(description="Whimbox 奇想盒更新器（源码与 EXE 通用）")
    parser.add_argument("--version", action="version", version="Whimbox Updater " + VERSION)
    parser.add_argument("--app-dir", help="奇想盒安装目录；默认从更新器位置向上查找")
    parser.add_argument("--data-dir", help="配置、缓存、日志和备份目录；默认使用用户数据目录")
    parser.add_argument("--print-data-dir", action="store_true", help="输出数据目录路径并退出")
    parser.add_argument("--check", action="store_true", help="仅检查版本，不修改奇想盒安装（会保存日志与缓存）")
    parser.add_argument("--no-backend", action="store_true", help="跳过后端 wheel 更新")
    parser.add_argument("--no-scripts", action="store_true", help="跳过路线同步")
    parser.add_argument("--scripts-only", action="store_true", help="仅同步路线脚本，不更新前后端")
    parser.add_argument("--watch", metavar="名字,名字", help="设置订阅；空字符串表示全量")
    parser.add_argument("--all-scripts", action="store_true", help="本次忽略订阅，进行全量同步")
    parser.add_argument("--edit-subscriptions", action="store_true", help="打开订阅配置，保存关闭后仅同步路线")
    parser.add_argument("--force", action="store_true", help="相同版本也强制重装后端或强制重拉路线")
    parser.add_argument("--sync-deps", action="store_true", help="后端安装时允许从国内 PyPI 镜像同步第三方依赖（慎用）")
    parser.add_argument("--install-frontend", action="store_true", help="同时安装新版前端（前端安装由官方 NSIS 安装器执行）")
    parser.add_argument("--rollback", action="store_true", help="恢复最近一次完整后端备份")
    parser.add_argument("--direct", action="store_true", help="仅直连 GitHub，不使用第三方下载代理")
    parser.add_argument("--proxy", action="append", metavar="URL", help="自定义 HTTPS 下载代理前缀（可多次指定）")
    parser.add_argument("--quiet", action="store_true", help="减少控制台输出，仍记录日志")
    parser.add_argument("--no-elevate", action="store_true", help="跳过自动提权，供已提权任务使用")
    parser.add_argument("--pause", action="store_true", help="完成后等待回车，供双击启动使用")
    return parser


def _execute(args):
    _validate_app()
    if args.check and (args.rollback or args.watch is not None or args.edit_subscriptions
                       or args.install_frontend or args.sync_deps):
        raise ValueError("--check 请单独使用，避免与安装、配置或回滚操作混用。")
    if args.scripts_only and (args.no_scripts or args.install_frontend or args.rollback):
        raise ValueError("--scripts-only 不能与 --no-scripts、--install-frontend 或 --rollback 同时使用。")

    os.makedirs(DATA_DIR, exist_ok=True)

    # Edit/save user configuration BEFORE any elevation so Notepad never runs as Administrator.
    did_config_edit = False
    if args.edit_subscriptions or args.watch is not None:
        with UpdateLock(os.path.join(DATA_DIR, "updater.lock")):
            _migrate_legacy_data()
            if args.edit_subscriptions:
                if os.name != "nt":
                    raise ValueError("订阅编辑器入口用于 Windows；其他平台可直接编辑配置。")
                if not os.path.isfile(WATCHLIST_FILE):
                    save_watchlist([])
                subprocess.run(["notepad.exe", WATCHLIST_FILE], check=True)
                load_watchlist()  # Stop before elevation or sync if the saved JSON is invalid.
                args.scripts_only = True
                args.no_scripts = False
                did_config_edit = True
            if args.watch is not None:
                names = [name.strip() for name in args.watch.replace("，", ",").split(",") if name.strip()]
                names = [name if name.lower().endswith(".json") else name + ".json" for name in names]
                save_watchlist(names)
                log("订阅已保存：%s" % WATCHLIST_FILE)
                if args.no_scripts or not names:
                    return 0
                args.scripts_only = True
                did_config_edit = True

    if _needs_elevation(args):
        ensure_admin(strip_config_edit=did_config_edit)

    with UpdateLock(os.path.join(DATA_DIR, "updater.lock")):
        _migrate_legacy_data()
        _prune_logs()
        if args.rollback:
            _ensure_whimbox_not_running()
            ok = rollback_backend(backend_version())
            save_state({
                "run_id": uuid.uuid4().hex,
                "backend_version": backend_version(),
                "frontend_version": frontend_version(),
                "status": "rolled_back" if ok else "failed",
                **({"last_rollback": datetime.now().isoformat(timespec="seconds")}
                   if ok else {"rollback_error": "手动回滚失败，请查看日志。"}),
            })
            return 0 if ok else 1
        if args.scripts_only:
            return _sync_scripts_only(args)
        return _update(args)


def _sync_scripts_only(args):
    result = {
        "run_id": uuid.uuid4().hex,
        "backend_version": backend_version(),
        "frontend_version": frontend_version(),
        "status": "success",
    }
    try:
        only = None if args.all_scripts else (load_watchlist() or None)
        result["scripts"] = sync_scripts(force=args.force, only=only)
        if result["scripts"]["failed"]:
            result["scripts_error"] = "%d 个路线脚本同步失败" % result["scripts"]["failed"]
    except Exception as exc:
        result["scripts_error"] = str(exc)
        log("路线同步失败：%s" % exc, "ERROR")
    failed = "scripts_error" in result
    result["status"] = "failed" if failed else "success"
    save_state(result)
    log("%s。日志与状态目录：%s" % ("路线同步存在失败" if failed else "路线同步完成", DATA_DIR),
        "ERROR" if failed else "INFO")
    return 1 if failed else 0


def _update(args):
    bver, fver = backend_version(), frontend_version()
    log("本地版本：后端 %s / 前端 %s" % (bver, fver))
    releases = fetch_releases()
    whl_release, wheel, wheel_version = pick_asset(releases, ".whl", [r"whimbox-([\d.]+)-py3-none-any\.whl"])
    exe_release, executable, exe_version = pick_asset(releases, ".exe", [r"whimbox_app-setup-([\d.]+)\.exe"])
    if wheel is None and not args.no_backend:
        raise RuntimeError("Release 中没有找到有效后端 wheel；已保留当前安装。")
    result = {"run_id": uuid.uuid4().hex, "backend_version": bver, "frontend_version": fver,
              "latest_release": releases[0].get("tag_name"), "latest_whl": wheel_version,
              "latest_exe": exe_version, "status": "success"}
    need_backend = (not args.no_backend) and (wheel is not None) and (args.force or vtuple(wheel_version) > vtuple(bver))
    need_frontend = executable is not None and (
        vtuple(exe_version) > vtuple(fver) or (args.install_frontend and args.force)
    )
    log("远端版本：后端 %s / 前端 %s" % (wheel_version or "无安装包", exe_version or "无安装包"))

    if need_backend and whl_release:
        notes = _summarize_release_notes(whl_release.get("body"))
        if notes:
            log("后端 %s 更新说明：" % wheel_version)
            for line in notes:
                log("  " + line)
    if need_frontend and exe_release and exe_release is not whl_release:
        notes = _summarize_release_notes(exe_release.get("body"))
        if notes:
            log("前端 %s 更新说明：" % exe_version)
            for line in notes:
                log("  " + line)

    if args.check:
        result["backend_update_available"] = need_backend
        result["frontend_update_available"] = need_frontend
        save_state(result)
        log("检查完成，奇想盒安装未修改。")
        return 0

    if need_backend or (args.install_frontend and need_frontend):
        _ensure_whimbox_not_running()

    protected_downloads = []
    if need_backend:
        backup = None
        installation_started = False
        try:
            destination = os.path.join(DOWNLOAD_DIR, wheel["name"])
            protected_downloads.append(destination)
            verified_version = download_asset(wheel["browser_download_url"], destination,
                                              wheel.get("size"), asset_digest(wheel), is_whl=True)
            if verified_version and vtuple(verified_version) != vtuple(wheel_version):
                raise ValueError("wheel 内部版本与文件名不一致，已停止安装。")
            backup = backup_backend(bver)
            installation_started = True
            if not install_whl(destination, force=args.force, sync_deps=args.sync_deps):
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
    elif not args.no_backend:
        log("后端已是最新版本。")

    if args.install_frontend and need_frontend:
        try:
            destination = os.path.join(DOWNLOAD_DIR, executable["name"])
            protected_downloads.append(destination)
            download_asset(executable["browser_download_url"], destination,
                           executable.get("size"), asset_digest(executable))
            if not install_frontend(destination, exe_version):
                raise RuntimeError("前端安装器或安装后版本验证失败")
            result["frontend_version"] = frontend_version()
            result["last_frontend_update"] = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            result["frontend_error"] = str(exc)
            log("前端更新失败：%s" % exc, "ERROR")
    elif args.install_frontend:
        log("前端已是最新版本。")
    elif need_frontend:
        log("前端可更新至 %s；可运行 update_frontend.bat 或添加 --install-frontend 更新前端。" % exe_version)

    if protected_downloads:
        _prune_downloads(protected=protected_downloads)

    if not args.no_scripts:
        try:
            only = None if args.all_scripts else (load_watchlist() or None)
            result["scripts"] = sync_scripts(force=args.force, only=only)
            if result["scripts"]["failed"]:
                result["scripts_error"] = "%d 个路线脚本同步失败" % result["scripts"]["failed"]
        except Exception as exc:
            result["scripts_error"] = str(exc)
            log("路线同步失败：%s" % exc, "ERROR")

    failed = any(key in result for key in ("backend_error", "frontend_error", "scripts_error", "rollback_error"))
    result["status"] = "failed" if failed else "success"
    save_state(result)
    log("%s。日志与状态目录：%s" % ("本次存在失败步骤" if failed else "更新完成", DATA_DIR), "ERROR" if failed else "INFO")
    if (need_backend and "backend_error" not in result) or (args.install_frontend and need_frontend and "frontend_error" not in result):
        log("请重启奇想盒以使用新版本。")
    return 1 if failed else 0


def main():
    global QUIET, _DIRECT_ONLY, _CUSTOM_PROXIES
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
        if args.direct and args.proxy:
            raise ValueError("--direct 与 --proxy 不能同时使用。")
        _DIRECT_ONLY = bool(args.direct)
        _CUSTOM_PROXIES = None
        if args.proxy:
            normalized = []
            for entry in args.proxy:
                for item in entry.split(","):
                    normalized.append(_normalize_proxy_url(item))
            normalized = list(dict.fromkeys(normalized))
            if "" not in normalized:
                normalized.append("")
            _CUSTOM_PROXIES = normalized
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
