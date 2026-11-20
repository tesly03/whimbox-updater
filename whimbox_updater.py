# -*- coding: utf-8 -*-
"""
Whimbox 奇想盒 · 免费自动更新器
=====================================================================
依据官方文档「手动更新奇想盒和路线」实现的免费替代方案（无需开通付费自动更新）：

  1. 从 GitHub Releases 查询最新版本，自动下载后端 .whl（走国内可达镜像）
  2. 用奇想盒自带的 python-embedded/pip 安装，等价于 APP 里的「手动更新后端」
  3. 自动同步 WhimboxScripts 跑图路线仓库的 .json 脚本
  4. 可选：下载并静默安装前端 whimbox_app-setup-x.y.z.exe
  5. 全程备份 + 校验 + 失败自动回滚，支持 --rollback 手动回滚

用法：
  python whimbox_updater.py --check              仅检查，不改动任何文件
  python whimbox_updater.py                     更新后端 + 同步脚本（推荐）
  python whimbox_updater.py --install-frontend  同时静默安装新版前端 exe
  python whimbox_updater.py --no-scripts        不同步跑图路线
  python whimbox_updater.py --force             已是最新版也强制重装
  python whimbox_updater.py --rollback          回滚到最近一次后端备份
  python whimbox_updater.py --help              查看全部参数

只使用 Python 标准库，直接用奇想盒内置 Python 运行：
  "C:\\Program Files\\whimbox_app\\python-embedded\\python.exe" whimbox_updater.py
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from datetime import datetime
import urllib.request

# ---------------------------------------------------------------- 配置区
DEFAULT_APP_DIR = r"C:\Program Files\whimbox_app"


def _detect_app_dir():
    """自动推断奇想盒安装目录：从脚本所在位置逐级向上找，
    命中「同时含 python-embedded 与 resources\app.asar」的目录即视为安装根目录。
    这样把本文件夹解压到任意位置的奇想盒目录下都能直接使用，无需改配置。"""
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        if (os.path.isdir(os.path.join(cur, "python-embedded"))
                and os.path.exists(os.path.join(cur, "resources", "app.asar"))):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return DEFAULT_APP_DIR


APP_DIR = _detect_app_dir()
PY_EXE = os.path.join(APP_DIR, "python-embedded", "python.exe")
SITE_PACKAGES = os.path.join(APP_DIR, "python-embedded", "Lib", "site-packages")
ASAR = os.path.join(APP_DIR, "resources", "app.asar")
SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")
DOWNLOAD_DIR = os.path.join(APP_DIR, "downloads")
LOG_DIR_GLOB = os.path.join(APP_DIR, "logs", "app-*.log")

RELEASES_API = "https://api.github.com/repos/nikkigallery/Whimbox/releases?per_page=15"
# 路线仓库用「整包 zip」一次拉取，避免为 39 个脚本各打一次 API（GitHub 未鉴权限额仅 60 次/小时）
SCRIPTS_ARCHIVE_URL = "https://github.com/nikkigallery/WhimboxScripts/archive/refs/heads/main.zip"

# GitHub 在国内常被墙：优先走可达镜像，直连放最后兜底（"" = 直连）。
# 实测 gh-proxy.com 对 api.github.com 与附件下载都稳定；ghfast.top 目前仅附件下载可用。
PROXIES = ["https://gh-proxy.com/", "https://ghfast.top/", "https://ghproxy.net/", ""]
PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "https://mirrors.cloud.tencent.com/pypi/simple/",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.ustc.edu.cn/pypi/simple/",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "_data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
LOG_DIR = os.path.join(DATA_DIR, "logs")
# 本地「我的订阅」清单：存在且非空时，只自动更新清单内的脚本（相当于只订阅这几个）
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
RELEASE_CACHE = os.path.join(DATA_DIR, "releases_cache.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WhimboxAutoUpdater/1.0"
CHUNK = 256 * 1024


# ---------------------------------------------------------------- 基础工具
def _reconfigure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_reconfigure_stdout()
QUIET = False
_LOG_PATH = None


def log(msg: str, level: str = "INFO"):
    line = "[%s] [%s] %s" % (datetime.now().strftime("%H:%M:%S"), level, msg)
    if not QUIET:
        print(line, flush=True)
    global _LOG_PATH
    if _LOG_PATH is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        _LOG_PATH = os.path.join(LOG_DIR, "updater-%s.log" % datetime.now().strftime("%Y-%m-%d"))
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] [%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, msg))
    except Exception:
        pass


def http_get(url: str, timeout: int = 30, headers: dict | None = None, stream: bool = False):
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def http_json(url: str, timeout: int = 30, headers: dict | None = None):
    with http_get(url, timeout=timeout, headers=headers) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def vtuple(v: str):
    parts = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in parts[:4]) if parts else (0,)


def ensure_admin():
    """写入 Program Files 需要管理员权限；非管理员时以 runas 重新拉起自身。"""
    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    log("需要管理员权限，正在请求提权（UAC）…", "WARN")
    script = os.path.abspath(__file__)
    args = ['"%s"' % script] + ['"%s"' % a for a in sys.argv[1:]] + ['"--no-elevate"']
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(args), None, 1
    )
    if int(rc) <= 32:
        log("提权失败（ShellExecuteW=%s），请以管理员身份重试" % rc, "ERROR")
        sys.exit(2)
    sys.exit(0)


def env_for_pip():
    e = os.environ.copy()
    e["PYTHONIOENCODING"] = "utf-8"
    e["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return e


def run_cmd(args, timeout=600):
    """执行命令并实时把输出写进日志。"""
    log("$ " + " ".join('"%s"' % a if " " in str(a) else str(a) for a in args))
    try:
        p = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env_for_pip(),
        )
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if out:
            for line in out.splitlines()[-40:]:
                log("  | " + line, "CMD")
        return p.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"
    except Exception as e:
        return False, repr(e)


# ---------------------------------------------------------------- 版本探测
def backend_version() -> str:
    """等价于 APP 的 backendManager.refreshBackendStatus()：importlib.metadata.version('whimbox')"""
    try:
        p = subprocess.run(
            [PY_EXE, "-s", "-c", "import importlib.metadata as m;print(m.version('whimbox'))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=40, env=env_for_pip(),
        )
        v = (p.stdout or "").strip().splitlines()[-1].strip()
        if re.fullmatch(r"[\d.]+", v):
            return v
    except Exception:
        pass
    dirs = glob.glob(os.path.join(SITE_PACKAGES, "whimbox-*.dist-info"))
    if dirs:
        return os.path.basename(sorted(dirs)[-1]).replace("whimbox-", "").replace(".dist-info", "")
    return "unknown"


def frontend_version() -> str:
    """从 app.asar 内的 package.json 读前端版本；失败则回退到启动日志。"""
    try:
        with open(ASAR, "rb") as f:
            head = f.read(8)
            hblen = struct.unpack("<I", head[4:8])[0]
            base = 8 + hblen
            f.seek(16)
            hdr = f.read(hblen - 8).decode("utf-8", errors="replace")
            j, _ = json.JSONDecoder().raw_decode(hdr)
            pj = j["files"]["package.json"]
            f.seek(base + int(pj["offset"]))
            pkg = json.loads(f.read(int(pj["size"])).decode("utf-8", errors="replace"))
            if pkg.get("version"):
                return str(pkg["version"])
    except Exception:
        pass
    for p in sorted(glob.glob(LOG_DIR_GLOB), reverse=True)[:7]:
        try:
            txt = open(p, "r", encoding="utf-8", errors="replace").read()
            m = re.findall(r"Whimbox App version=(\d+\.\d+\.\d+)", txt)
            if m:
                return m[-1]
        except Exception:
            continue
    return "unknown"


# ---------------------------------------------------------------- Releases
def cache_releases(releases):
    """缓存精简后的 release 元数据，供 GitHub API 限流 / 断网时兜底。"""
    slim = []
    for r in releases[:15]:
        slim.append({
            "tag_name": r.get("tag_name"),
            "name": r.get("name"),
            "published_at": r.get("published_at"),
            "body": r.get("body"),
            "draft": False,
            "prerelease": False,
            "assets": [{
                "name": a.get("name"),
                "size": a.get("size"),
                "digest": a.get("digest"),
                "browser_download_url": a.get("browser_download_url"),
            } for a in r.get("assets", [])],
        })
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(RELEASE_CACHE, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def fetch_releases():
    """查最新版本：直连 + 镜像代理多通道依次尝试；全部失败再回退本地缓存。

    只查 api.github.com 容易被限流（未鉴权 60 次/小时，403 rate limit）。
    这里和下载附件一样，逐个尝试把 API 地址也套进镜像前缀。
    """
    last_err = None
    # 通道 1：直连 api.github.com（最快，但可能被墙或限流）
    candidates = [RELEASES_API]
    # 通道 2：镜像代理前缀套在 API 地址前（如 ghfast.top/https://api.github.com/...）
    candidates += [p + RELEASES_API for p in PROXIES if p]
    for url in candidates:
        try:
            data = http_json(url, timeout=20)
            if not isinstance(data, list):
                raise RuntimeError("GitHub 返回异常：%s" % str(data)[:200])
            valid = [r for r in data if not r.get("draft") and not r.get("prerelease")]
            valid.sort(key=lambda r: r.get("published_at") or "", reverse=True)
            if valid:
                cache_releases(valid)
                return valid
        except Exception as e:
            last_err = e
            continue
    log("GitHub Releases 直连+镜像均不可用（%s），改用本地缓存" % str(last_err)[:120], "WARN")
    if os.path.exists(RELEASE_CACHE):
        cached = json.load(open(RELEASE_CACHE, encoding="utf-8"))
        if isinstance(cached, list) and cached:
            log("已载入 %d 条缓存 release（最新 %s），网络恢复后会自动刷新" %
                (len(cached), cached[0].get("tag_name")), "WARN")
            return cached
    raise RuntimeError("无法获取版本信息（网络不可用且无本地缓存）")


def asset_version(release, asset, patterns) -> str:
    tag = (release.get("tag_name") or "").lstrip("v")
    if re.fullmatch(r"\d+(\.\d+)+", tag):
        return tag
    for pat in patterns:
        m = re.search(pat, asset.get("name", ""), re.I)
        if m:
            return m.group(1)
    return tag or "unknown"


def pick_asset(releases, suffix, patterns):
    """从新到旧，找出第一个带指定后缀附件的 release。"""
    for r in releases:
        for a in r.get("assets", []):
            if a.get("name", "").lower().endswith(suffix):
                return r, a, asset_version(r, a, patterns)
    return None, None, None


def asset_digest(asset):
    d = asset.get("digest") or ""
    return d.split(":", 1)[1] if d.startswith("sha256:") else None


def verify_wheel(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError("wheel 压缩包损坏")
        metas = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
        if not metas:
            raise RuntimeError("wheel 缺少 METADATA，不是合法的 whimbox 包")
        meta = z.read(metas[0]).decode("utf-8", errors="replace")
        m = re.search(r"^Version:\s*(\S+)", meta, re.M)
        if not m or not re.search(r"^Name:\s*whimbox\s*$", meta, re.M | re.I):
            raise RuntimeError("wheel 不是 whimbox 包")
        return m.group(1)


def download_asset(url: str, dest: str, expected_size=None, expected_sha=None, is_whl=False):
    """依次尝试直连 / 各镜像；用 size + sha256 + wheel 完整性三重校验。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        try:
            ok = (not expected_size or os.path.getsize(dest) == expected_size) and (
                not expected_sha or sha256_file(dest) == expected_sha
            )
            if ok:
                v = verify_wheel(dest) if is_whl else None
                log("已存在有效缓存，跳过下载：%s%s" % (os.path.basename(dest), (" (v%s)" % v) if v else ""))
                return v
        except Exception:
            pass
        try:
            os.remove(dest)
        except Exception:
            pass

    for prefix in PROXIES:
        full = (prefix + url) if prefix else url
        tag = prefix.rstrip("/").split("//")[-1] if prefix else "直连 github.com"
        tmp = dest + ".part"
        try:
            log("尝试下载通道：%s" % tag)
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            chan_timeout = 12 if not prefix else 30
            with urllib.request.urlopen(req, timeout=chan_timeout) as r, open(tmp, "wb") as f:
                total = 0
                last = 0
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    if total - last >= 8 * 1024 * 1024:
                        log("  已下载 %.1f MB" % (total / 1024 / 1024))
                        last = total
            if expected_size and os.path.getsize(tmp) != expected_size:
                raise RuntimeError("文件大小不符 (%d != %d)，疑似被拦截" % (os.path.getsize(tmp), expected_size))
            if expected_sha and sha256_file(tmp) != expected_sha:
                raise RuntimeError("SHA256 校验失败")
            v = verify_wheel(tmp) if is_whl else None
            os.replace(tmp, dest)
            log("下载完成：%s (%.1f MB)%s" % (
                os.path.basename(dest), os.path.getsize(dest) / 1024 / 1024,
                (" v" + v) if v else ""))
            return v
        except Exception as e:
            log("  通道 %s 失败：%s" % (tag, str(e)[:160]), "WARN")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    raise RuntimeError("所有下载通道均失败，请检查网络或稍后重试")


# ---------------------------------------------------------------- 备份/回滚
def _copytree(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def backup_backend(ver: str):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = os.path.join(BACKUP_DIR, "backend-%s-from-%s" % (stamp, ver))
    os.makedirs(d, exist_ok=True)
    pkg = os.path.join(SITE_PACKAGES, "whimbox")
    info = os.path.join(SITE_PACKAGES, "whimbox-%s.dist-info" % ver)
    n = 0
    if os.path.isdir(pkg):
        _copytree(pkg, os.path.join(d, "whimbox")); n += 1
    if os.path.isdir(info):
        _copytree(info, os.path.join(d, "whimbox-%s.dist-info" % ver)); n += 1
    with open(os.path.join(d, "backup.json"), "w", encoding="utf-8") as f:
        json.dump({"version": ver, "created": stamp, "type": "backend"}, f, ensure_ascii=False)
    log("已备份当前后端 whimbox %s -> %s（%d 项）" % (ver, d, n))
    return d


def rollback_backend(new_ver: str):
    cands = [d for d in glob.glob(os.path.join(BACKUP_DIR, "backend-*"))
             if os.path.exists(os.path.join(d, "backup.json"))]
    if not cands:
        log("没有可用的后端备份，无法回滚", "ERROR")
        return False
    d = max(cands, key=os.path.getmtime)
    meta = json.load(open(os.path.join(d, "backup.json"), encoding="utf-8"))
    old = meta["version"]
    log("开始回滚：移除 %s，恢复 %s（备份 %s）" % (new_ver, old, d), "WARN")
    for name in ["whimbox", "whimbox-%s.dist-info" % new_ver]:
        p = os.path.join(SITE_PACKAGES, name)
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)
    for name in ["whimbox", "whimbox-%s.dist-info" % old]:
        s = os.path.join(d, name)
        if os.path.isdir(s):
            _copytree(s, os.path.join(SITE_PACKAGES, name))
    got = backend_version()
    ok = (got == old)
    log("回滚%s，当前后端版本：%s" % ("成功" if ok else "未完全成功", got), "INFO" if ok else "ERROR")
    return ok


def latest_backup():
    cands = [d for d in glob.glob(os.path.join(BACKUP_DIR, "backend-*"))
             if os.path.exists(os.path.join(d, "backup.json"))]
    return max(cands, key=os.path.getmtime) if cands else None


# ---------------------------------------------------------------- 安装后端
def install_whl(path: str) -> bool:
    """复刻 APP 的 pythonManager.installWheelPackage：python -s -m pip install -i 镜像 <whl>"""
    # 官方流程会先装 setuptools，失败不阻断
    run_cmd([PY_EXE, "-s", "-m", "pip", "install", "-i", PIP_MIRRORS[0],
             "--disable-pip-version-check", "setuptools"], timeout=180)
    for mirror in PIP_MIRRORS:
        ok, _ = run_cmd([PY_EXE, "-s", "-m", "pip", "install",
                         "-i", mirror, "--disable-pip-version-check",
                         "--retries", "1", "--timeout", "15", path], timeout=600)
        if ok:
            log("pip 安装完成（源：%s）" % mirror)
            return True
        log("pip 源 %s 失败，尝试下一个" % mirror, "WARN")
    # 离线兜底：依赖已本机齐全时，直接从 wheel 安装，完全不联网
    log("所有镜像失败，改用离线安装（--no-index --no-deps）", "WARN")
    ok, _ = run_cmd([PY_EXE, "-s", "-m", "pip", "install", "--no-index", "--no-deps",
                     "--force-reinstall", "--disable-pip-version-check", path], timeout=300)
    return ok


# ---------------------------------------------------------------- 同步路线脚本
def download_scripts_archive():
    """一次下载整个 WhimboxScripts 仓库 zip（走镜像），返回 {文件名: 字节内容}。"""
    tmp = os.path.join(DATA_DIR, "WhimboxScripts-main.zip")
    for prefix in PROXIES:
        full = (prefix + SCRIPTS_ARCHIVE_URL) if prefix else SCRIPTS_ARCHIVE_URL
        tag = prefix.rstrip("/").split("//")[-1] if prefix else "直连 github.com"
        try:
            log("拉取路线仓库整包：%s" % tag)
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            chan_timeout = 12 if not prefix else 40
            with urllib.request.urlopen(req, timeout=chan_timeout) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, CHUNK)
            with zipfile.ZipFile(tmp) as z:
                if z.testzip() is not None:
                    raise RuntimeError("zip 损坏")
                out = {}
                for n in z.namelist():
                    # 只取仓库根目录一层的脚本，跳过 docs/ 等子目录
                    parts = n.split("/")
                    if len(parts) != 2:
                        continue
                    base = parts[1]
                    if not base.lower().endswith(".json"):
                        continue
                    data = z.read(n)
                    json.loads(data.decode("utf-8", errors="replace"))  # 必须是合法 JSON
                    out[base] = data
            if not out:
                raise RuntimeError("zip 中没有找到 .json 脚本")
            log("路线仓库整包解析成功：%d 个脚本（%.1f MB）" %
                (len(out), os.path.getsize(tmp) / 1024 / 1024))
            return out
        except Exception as e:
            log("  通道 %s 失败：%s" % (tag, str(e)[:160]), "WARN")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    raise RuntimeError("路线仓库整包下载失败（所有通道）")


def load_watchlist():
    """返回本地订阅清单（脚本文件名列表）。文件不存在或为空则返回 []。"""
    if not os.path.exists(WATCHLIST_FILE):
        return []
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            d = json.load(f)
        names = d.get("scripts") if isinstance(d, dict) else d
        if not isinstance(names, list):
            return []
        return [os.path.basename(str(x)) for x in names if str(x).strip()]
    except Exception as e:
        log("订阅清单解析失败（将按全量同步处理）：%s；请确认文件是 UTF-8 编码的合法 JSON" % e, "WARN")
        return []


def save_watchlist(names):
    os.makedirs(DATA_DIR, exist_ok=True)
    data = {
        "scripts": names,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "本文件即「我的订阅」：只自动更新这里列出的脚本。删掉本文件或清空 scripts 即恢复全量同步。",
    }
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sync_scripts(force=False, only=None):
    """只新增/更新仓库里存在的 .json，绝不删除用户自己的脚本。

    only 为文件名列表时（本地订阅清单），只同步这些脚本；其余保持不动。
    """
    remote_files = download_scripts_archive()
    total_remote = len(remote_files)
    if only:
        wanted = {os.path.basename(n) for n in only}
        remote_files = {k: v for k, v in remote_files.items() if k in wanted}
        missing = sorted(wanted - set(remote_files))
        if missing:
            log("订阅清单里有 %d 个脚本在仓库中找不到（可能已下架）：%s" %
                (len(missing), "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")), "WARN")
    counts = {"added": 0, "updated": 0, "same": 0, "failed": 0}
    changed_backup = os.path.join(BACKUP_DIR, "scripts-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    for name, remote in remote_files.items():
        try:
            dest = os.path.join(SCRIPTS_DIR, name)
            if os.path.exists(dest):
                with open(dest, "rb") as f:
                    if f.read() == remote and not force:
                        counts["same"] += 1
                        continue
                os.makedirs(changed_backup, exist_ok=True)
                shutil.copy2(dest, os.path.join(changed_backup, name))
                action = "updated"
            else:
                action = "added"
            with open(dest, "wb") as f:
                f.write(remote)
            counts[action] += 1
            log("  %s %s" % ("新增" if action == "added" else "更新", name))
        except Exception as e:
            counts["failed"] += 1
            log("  脚本 %s 处理失败：%s" % (name, str(e)[:120]), "WARN")

    log("路线同步完成%s：新增 %d / 更新 %d / 无变化 %d / 失败 %d（共 %d）" %
        ("（仅订阅清单）" if only else "", counts["added"], counts["updated"],
         counts["same"], counts["failed"], len(remote_files)))
    if counts["updated"] and os.path.isdir(changed_backup):
        log("被覆盖的旧脚本已备份：%s" % changed_backup)
    return counts


# ---------------------------------------------------------------- 前端 exe
def install_frontend(exe_path: str, expected_ver: str):
    if not os.path.exists(exe_path):
        log("前端安装包不存在：%s" % exe_path, "ERROR")
        return False
    log("静默安装前端（NSIS /S），请勿手动关闭奇想盒…", "WARN")
    try:
        subprocess.run([exe_path, "/S"], timeout=420, env=env_for_pip())
    except Exception as e:
        log("前端安装进程异常：%s" % e, "WARN")
    time.sleep(3)
    got = frontend_version()
    ok = vtuple(got) >= vtuple(expected_ver)
    log("前端安装%s，当前前端版本：%s（目标 %s）" %
        ("成功" if ok else "未能确认（可能需重启 APP 后生效）", got, expected_ver),
        "INFO" if ok else "WARN")
    return ok


# ---------------------------------------------------------------- 主流程
def save_state(d: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    d["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        old = json.load(open(STATE_FILE, encoding="utf-8")) if os.path.exists(STATE_FILE) else {}
    except Exception:
        old = {}
    old.update(d)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False, indent=2)


def main():
    global QUIET
    ap = argparse.ArgumentParser(description="Whimbox 奇想盒 免费自动更新器")
    ap.add_argument("--check", action="store_true", help="仅检查版本，不改动任何文件")
    ap.add_argument("--no-scripts", action="store_true", help="不同步跑图路线脚本")
    ap.add_argument("--watch", metavar="名字,名字", default=None,
                    help="设置「我的订阅」清单（逗号分隔的脚本文件名），只自动更新这些；传空串则清空恢复全量")
    ap.add_argument("--all-scripts", action="store_true", help="忽略订阅清单，本次全量同步所有脚本")
    ap.add_argument("--force", action="store_true", help="已是最新版也强制重装后端")
    ap.add_argument("--install-frontend", action="store_true", help="下载并静默安装新版前端 exe")
    ap.add_argument("--rollback", action="store_true", help="回滚后端到最近一次备份")
    ap.add_argument("--quiet", action="store_true", help="减少控制台输出（仍写日志，供定时任务用）")
    ap.add_argument("--no-elevate", action="store_true", help="跳过 UAC 提权（供已提权的定时任务用）")
    args = ap.parse_args()
    QUIET = args.quiet

    os.makedirs(DATA_DIR, exist_ok=True)
    log("=" * 62)
    log("Whimbox 自动更新器  |  日志：%s" % os.path.join(LOG_DIR, "updater-%s.log" % datetime.now().strftime("%Y-%m-%d")))

    need_admin = not args.check and not args.rollback and not args.no_elevate
    if args.watch is not None and args.no_scripts:
        need_admin = False  # 只改订阅清单，不碰安装目录
    if need_admin:
        ensure_admin()

    for p, label in [(PY_EXE, "内置 Python"), (ASAR, "前端 app.asar"), (SCRIPTS_DIR, "脚本目录")]:
        if not os.path.exists(p):
            log("找不到%s：%s（请检查 APP_DIR 配置）" % (label, p), "ERROR")
            return 2

    if args.watch is not None:
        names = [x.strip() for x in args.watch.replace("，", ",").split(",") if x.strip()]
        names = [n if n.lower().endswith(".json") else n + ".json" for n in names]
        save_watchlist(names)
        if names:
            log("已更新「我的订阅」清单（%d 个）：%s" % (len(names), WATCHLIST_FILE))
            for n in names:
                log("  · %s%s" % (n, "" if os.path.exists(os.path.join(SCRIPTS_DIR, n)) else "（本地暂无，本次会下载）"))
        else:
            log("已清空「我的订阅」清单，恢复全量同步。")
        if not names or args.no_scripts:
            return 0

    # ---- 回滚模式
    if args.rollback:
        cur = backend_version()
        return 0 if rollback_backend(cur) else 1

    # ---- 本地版本
    bver = backend_version()
    fver = frontend_version()
    log("本地版本：后端 whimbox %s ｜ 前端 whimbox_app %s" % (bver, fver))

    # ---- 远端版本
    try:
        releases = fetch_releases()
    except Exception as e:
        log("无法获取 GitHub Releases：%s" % e, "ERROR")
        return 1
    latest_tag = releases[0].get("tag_name") if releases else "unknown"
    whl_r, whl_a, whl_v = pick_asset(releases, ".whl", [r"whimbox-([\d.]+)-py3-none-any\.whl"])
    exe_r, exe_a, exe_v = pick_asset(releases, ".exe", [r"whimbox_app-setup-([\d.]+)\.exe"])

    log("最新版本：Release %s ｜ 后端 whl %s ｜ 前端 exe %s" %
        (latest_tag, whl_v or "无", exe_v or "无"))
    if whl_r and whl_r.get("body"):
        note = " ".join(whl_r["body"].split())[:160]
        log("更新说明：%s" % note)

    need_backend = bool(whl_a) and (args.force or vtuple(whl_v) > vtuple(bver))
    need_frontend = bool(exe_a) and vtuple(exe_v) > vtuple(fver)
    log("检查结果：后端%s ｜ 前端%s" %
        ("需更新 %s -> %s" % (bver, whl_v) if need_backend else "已是最新",
         "落后（%s -> %s），本版本无 exe 时按官方流程只需更新后端" % (fver, exe_v)
         if need_frontend else "已是最新"))

    if args.check:
        log("--check 模式：未做任何改动。")
        save_state({"backend_version": bver, "frontend_version": fver,
                    "latest_release": latest_tag, "latest_whl": whl_v, "latest_exe": exe_v})
        return 0

    # ---- 后端更新
    result = {"backend_version": bver, "frontend_version": fver,
              "latest_release": latest_tag, "latest_whl": whl_v, "latest_exe": exe_v}
    if need_backend:
        dest = os.path.join(DOWNLOAD_DIR, whl_a["name"])
        log("开始更新后端 whimbox %s -> %s" % (bver, whl_v))
        try:
            v = download_asset(whl_a["browser_download_url"], dest,
                               whl_a.get("size"), asset_digest(whl_a), is_whl=True)
            if v and vtuple(v) != vtuple(whl_v):
                log("注意：wheel 内版本 %s 与 release 标签 %s 不一致，以 wheel 为准" % (v, whl_v), "WARN")
            bdir = backup_backend(bver)
            if install_whl(dest):
                got = backend_version()
                if vtuple(got) >= vtuple(whl_v) or got == whl_v:
                    log("✅ 后端更新成功：whimbox %s -> %s" % (bver, got))
                    result["backend_version"] = got
                    result["last_backend_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    result["last_backup"] = bdir
                else:
                    raise RuntimeError("安装后版本校验失败：期望 %s，实际 %s" % (whl_v, got))
            else:
                raise RuntimeError("pip 安装失败")
        except Exception as e:
            log("❌ 后端更新失败：%s" % e, "ERROR")
            log("正在自动回滚…", "WARN")
            rollback_backend(whl_v)
            result["backend_error"] = str(e)
    else:
        log("后端已是最新版本，跳过安装。" + ("（--force 可强制重装）" if not args.force else ""))

    # ---- 前端
    if args.install_frontend and need_frontend and exe_a:
        # 安装包放在工具目录，避免 NSIS 安装时改写 APP 目录导致自身被占用
        exe_dir = os.path.join(DATA_DIR, "downloads")
        os.makedirs(exe_dir, exist_ok=True)
        dest = os.path.join(exe_dir, exe_a["name"])
        log("开始下载前端安装包 %s（约 %.0f MB）" % (exe_a["name"], exe_a.get("size", 0) / 1024 / 1024))
        try:
            download_asset(exe_a["browser_download_url"], dest,
                           exe_a.get("size"), asset_digest(exe_a))
            install_frontend(dest, exe_v)
            result["frontend_version"] = frontend_version()
        except Exception as e:
            log("前端更新失败：%s" % e, "ERROR")
            result["frontend_error"] = str(e)
    elif need_frontend:
        log("提示：前端落后于 %s。官方规则是「只有本次 release 同时带 exe 时才需要装前端」，"
            "当前最新 release %s 仅含 whl，故本次不装 exe。" % (exe_v, latest_tag))
        log("      如需补齐前端，可加 --install-frontend 参数，或手动运行 %s" %
            (exe_a["name"] if exe_a else "对应 setup.exe"))

    if args.watch is not None:
        if not args.no_scripts and args.watch.strip():
            log("提示：本次同时按新的订阅清单同步一次脚本。")

    # ---- 路线脚本
    if not args.no_scripts:
        try:
            only = None if args.all_scripts else (load_watchlist() or None)
            if only:
                log("按「我的订阅」清单同步 %d 个脚本（--all-scripts 可全量）" % len(only))
            result["scripts"] = sync_scripts(only=only)
        except Exception as e:
            log("路线同步失败（不影响后端更新）：%s" % e, "WARN")
            result["scripts_error"] = str(e)

    save_state(result)
    log("=" * 62)
    log("完成。当前：后端 %s ｜ 前端 %s ｜ 状态文件 %s" %
        (backend_version(), frontend_version(), STATE_FILE))
    log("请重启奇想盒 APP 使新后端生效（旧的后端进程不会自动热替换）。")
    return 0 if "backend_error" not in result else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("用户中断", "WARN")
        sys.exit(130)
    except Exception as exc:
        log("未捕获异常：%r" % exc, "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")
        sys.exit(1)
