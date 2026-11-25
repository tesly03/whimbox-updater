# Whimbox 奇想盒更新器

自动执行奇想盒公开的手动更新步骤：检查 GitHub Releases、安装后端 wheel、增量同步公开路线脚本，并按需运行官方前端安装器。

**支持独立 Windows x64 EXE：电脑上无需额外安装 Python。** EXE 在 GitHub Actions 的 Windows 构建机上生成，本机只运行更新器。

## 下载与运行

在 [Releases](https://github.com/tesly03/whimbox-updater/releases) 下载最新的 `whimbox-updater-vX.Y.Z-windows-x64.zip`，解压到奇想盒安装目录内（压缩包顶层自带 `whimbox-updater\` 目录），例如：

```text
C:\Program Files\whimbox_app\whimbox-updater\
```

双击 `update_now.bat`（更新后端 + 路线）、`update_all.bat`（前后端 + 路线一起更新）或 `whimbox-updater.exe`。更新器从所在位置向上查找 `python-embedded\python.exe` 和 `resources\app.asar`。安装在其他盘符也适用。仅在目标安装目录缺少写入权限（或安装前端）时才申请管理员权限；更新前请退出奇想盒，更新器会自动检测运行中的前端进程，并可自动清理官方前端退出后残留的后台 `python.exe` 孤儿进程。

Windows x64 EXE 面向 Windows 10/11。它包含更新器自己的 Python 运行时；安装后端仍使用奇想盒原有的内置 Python，更新器不会重新打包奇想盒程序文件。

独立 EXE 也可放在其他目录，用命令行指定奇想盒的位置：

```powershell
.\whimbox-updater.exe --app-dir "D:\Apps\whimbox_app"
```

源码用户下载 source ZIP，将更新器放入安装目录后，通过原有 BAT 或内置 Python 运行即可，无第三方运行依赖。

## 常用入口

| 入口 | 作用 |
| --- | --- |
| `update_now.bat` | 更新后端并增量同步路线（检测到新前端时会给出提示） |
| `update_all.bat` | 一键更新后端、官方 Electron 前端并增量同步路线 |
| `update_frontend.bat` | 仅使用官方 NSIS 安装器静默更新 Electron 前端 |
| `check_only.bat` | 仅检查前后端版本并显示更新说明，保留安装不变 |
| `rollback.bat` | 从最近一次完整备份恢复后端 |
| `subscriptions.bat` | 普通权限编辑订阅，保存并关闭记事本后仅同步路线 |
| `install_task.bat` | 安装每日 09:00 自动更新任务（支持 `-Time`、`-AppDir`、`-DataDir`、`-StopRunning`、`-BackgroundS4U`） |

BAT 优先启动同目录 EXE；源码包则查找奇想盒的内置 Python。退出码会保留，失败时控制台显示错误。传入 `--quiet` 时会自动跳过结束暂停，便于自动化调用。

## 更新与恢复

- **智能进程守卫**：修改后端或前端前自动检查目标安装目录下的 `whimbox_app.exe` 与内置 `python.exe`（排除更新器自身及父进程链）。前端活跃运行时默认拒绝修改以免损坏安装（可传入 `--stop-running` 自动关闭）；若前端已退出仅残留后台 `python.exe` 孤儿进程，默认自动清理以继续更新（可用 `--no-kill-orphan-backend` 关闭）。
- **免限流可信校验链**：优先直连 GitHub REST API（支持 `ETag` 304 缓存及可选 `GITHUB_TOKEN`，令牌绝不发给非官方接口）；API 触发 403 限流时自动直连 GitHub `releases.atom` 与 `expanded_assets` 页面获取官方附件链接与原生 SHA256 摘要（零 API 配额消耗）；直连不通时回退至多代理通道并在独立运营方间交叉比对 SHA256，不一致立即中止。下载支持 `Range` 断点续传、单遍流式 SHA256 校验与停滞超时切换，缺少校验值时停止安装。
- **事务备份与回滚**：下载、预检和备份失败时现有后端保持原状；只有安装已开始且本次完整备份可用时才执行自动回滚。备份先写入临时目录并记录环境包版本快照，完整后才登记；恢复前检查文件摘要，恢复时保留当前目录供失败撤回，文件替换包含短暂锁重试。
- **依赖预检与全新环境适配**：存量升级默认在备份前先预检 wheel `METADATA` 的 `Requires-Dist`，默认执行 `--no-deps` 并采用基线差异 `pip check`（避免环境中无关存量包警告阻塞更新）+ `whimbox.main` 入口验证；若检测到全新安装环境（尚无后端，仅含官方裸 `Python312.zip`），会自动通过国内 PyPI 镜像补齐依赖；存量升级遇到上游依赖变更时可按需传入 `--sync-deps`。
- **增量路线同步**：优先通过 API `ETag` 或免配额 Git Smart-HTTP `info/refs` 查询 `WhimboxScripts` 最新提交，无更新且本地齐全时跳过下载；少量文件名订阅直接按需拉取单文件，跳过整仓下载；订阅支持仓库文件名与奇想盒界面显示名（`info.name`）；全量同步时仅在本地未被修改的情况下安全归档清理上游已移除的旧路线。
- **并发与退出码**：同一数据目录使用 OS 进程锁避免重复执行；前后端安装或路线同步任一步失败，整体退出码为非零，计划任务可准确识别失败。

前端安装由官方 NSIS 安装器执行（自动通过 `/D=` 指向当前 `APP_DIR`、清理 PyInstaller 环境变量并将工作目录切换至下载目录，且遵循官方纯英文安装路径要求），前端程序本身不在后端备份的恢复范围。

## 官方前端与后端

奇想盒采用前后端分离架构，由 [Electron 前端](https://github.com/nikkigallery/whimbox_app) 和 [Python 后端](https://github.com/nikkigallery/Whimbox) 组成，前端通过安装目录内的 `python-embedded` 启动后端 RPC 服务。前后端安装包统一发布在 [后端 Releases](https://github.com/nikkigallery/Whimbox/releases)，并非每次发布都同时包含前端和后端附件，前后端版本号可以不同（例如前端 `3.1.4` 搭配后端 `3.1.5`）。

默认 `update_now.bat` 只更新后端与路线；需要单独更新前端时双击 `update_frontend.bat`，前后端一起更新时双击 `update_all.bat` 或添加 `--install-frontend`。官方 NSIS 安装脚本（`installer.nsh`）在覆盖更新时会自动保留 `python-embedded`、`configs`、`scripts`、`downloads` 与 `logs`。本工具仅使用公开 Release 与脚本仓库，无需登录奇想盒，也不访问其会员自动更新接口。

## 配置、日志与备份

默认将数据存储在当前用户可写的位置，按奇想盒安装路径分别隔离：

```text
%LOCALAPPDATA%\WhimboxUpdater\<安装路径标识>\
  watchlist.json
  state.json
  scripts_state.json
  releases_cache.json
  releases_meta.json
  downloads\
  logs\
  backups\
```

运行时会输出实际路径。使用 `--data-dir "D:\WhimboxUpdaterData"` 可指定固定目录；手动运行和计划任务应使用相同目录。请保留该目录以便恢复后端。

更新器会从同目录旧版 `_data` 自动迁移订阅和 Release 缓存，并继续识别旧备份；迁移保留旧文件。磁盘自动维护策略：后端备份保留最近 3 份，路线备份保留最近 10 份，下载缓存（`.whl`/`.exe` 各）保留最近 3 份，过期断点 `.part` 保留 3 天，日志保留最近 14 天。

## 我的订阅

双击 `subscriptions.bat`，在普通权限记事本中填写（支持仓库文件名或奇想盒界面内显示名称 `info.name`，可用 `--list-scripts` 查看完整清单）：

```json
{
  "scripts": ["家园日常.json", "祈愿树林 星荧草"]
}
```

`scripts: []` 表示全量同步。JSON 错误、类型错误或无效名称会在提权和同步前直接停止，避免意外扩大范围。路线名在上游缺失时会报告失败。同步保留仓库里没有的本地自定义文件，用户修改过的本地文件也不会被自动清理。

## 命令行与环境变量

以下参数也适用于 `python whimbox_updater.py`：

```powershell
.\whimbox-updater.exe --version
.\whimbox-updater.exe --check
.\whimbox-updater.exe --check-self
.\whimbox-updater.exe --list-scripts
.\whimbox-updater.exe --no-scripts
.\whimbox-updater.exe --no-backend --install-frontend
.\whimbox-updater.exe --scripts-only
.\whimbox-updater.exe --force
.\whimbox-updater.exe --sync-deps
.\whimbox-updater.exe --install-frontend
.\whimbox-updater.exe --rollback
.\whimbox-updater.exe --stop-running
.\whimbox-updater.exe --no-kill-orphan-backend
.\whimbox-updater.exe --watch "家园日常,祈愿树林 星荧草"
.\whimbox-updater.exe --watch ""
.\whimbox-updater.exe --all-scripts
.\whimbox-updater.exe --edit-subscriptions
.\whimbox-updater.exe --direct
.\whimbox-updater.exe --proxy "https://your-mirror.example/"
.\whimbox-updater.exe --app-dir "D:\Apps\whimbox_app" --data-dir "D:\UpdaterData"
.\whimbox-updater.exe --help
```

可选环境变量：
- `GITHUB_TOKEN` / `GH_TOKEN`：仅用于直连 `api.github.com` 提升限额，绝不发送给网页端或第三方代理。
- `WHIMBOX_PROXIES`：逗号分隔的自定义 HTTPS 代理前缀列表，或设为 `direct` 表示仅直连。

`--force` 会向 pip 传入 `--force-reinstall` 并忽略路线 commit 缓存强制拉取。`--check` 只保留日志、缓存与检查状态，不修改奇想盒安装。无参数双击 EXE 后会等待回车，便于查看结果；`--pause` 可为其他命令启用相同行为，传入 `--quiet` 或 `--help`/`--version` 时自动跳过暂停。

## 计划任务

双击 `install_task.bat`，默认安装每日 09:00 运行的 `WhimboxAutoUpdate`（采用 `Interactive` + 最高权限，兼容微软账户/PIN 登录并复用当前用户代理设置，错过时间时登录后自动补跑）。也可在命令行自定义时间和选项：

```powershell
.\install_task.bat -Time 08:30 -AppDir "D:\Apps\whimbox_app" -StopRunning
```

如需无登录会话后台运行，可额外传入 `-BackgroundS4U`。任务优先使用 EXE，使用与交互用户一致的数据目录，并限制同一任务并发实例。建议先手动完成一次更新，再启用定时任务。

删除任务：

```powershell
Unregister-ScheduledTask -TaskName WhimboxAutoUpdate -Confirm:$false
```

## GitHub 云端打包

每次推送到 `main` 或发起 PR 会触发跨平台 `CI` 回归测试（含 Python 单测、BAT CRLF/ASCII 校验及 PowerShell AST 语法检查）；推送 `vX.Y.Z` 标签后，[Windows x64 release 工作流](https://github.com/tesly03/whimbox-updater/actions) 会：

1. 使用 Python 3.12 运行标准库回归测试。
2. 安装 `requirements-build.txt` 中固定版本的 PyInstaller，嵌入版本资源信息并裁剪无关标准库模块，构建单文件控制台 EXE。
3. 执行 EXE 的 `--version`、`--help` 和 `--print-data-dir` 冒烟测试。
4. 打包 Windows 便携 ZIP（顶层含 `whimbox-updater/` 目录）、源码 ZIP、独立 EXE 及 `SHA256SUMS.txt`。
5. 生成 GitHub 构建溯源证明（`actions/attest-build-provenance`），自动提取 `CHANGELOG.md` 对应版本说明，并创建草稿 Release 供维护者核对后发布。

也可在 Actions 页面手动运行工作流进行构建。构建使用 GitHub 自动提供的短期 `GITHUB_TOKEN`，源码中无需配置个人令牌。

校验下载文件：

```powershell
Get-FileHash .\whimbox-updater.exe -Algorithm SHA256
```

将输出与同版本的 `SHA256SUMS.txt` 对比。EXE 暂未做商业代码签名，Windows SmartScreen 可能提示未知发布者；请从本仓库 Release 获取并核对摘要。

开发测试：

```bash
python -m unittest discover -s tests -v
```

## 项目说明与许可

本工具仅自动化奇想盒官方文档公开的手动更新步骤，不包含或分发奇想盒的 EXE、wheel 等程序文件。版本与安装包来自 `nikkigallery/Whimbox` 的公开 Releases，路线来自公开仓库 `nikkigallery/WhimboxScripts`，不依赖官方付费自动更新和订阅接口。

本项目与奇想盒官方无关联。奇想盒及相关脚本、安装包和商标属于其原作者。请备份自己的配置与路线，支持原作者。

[MIT License](LICENSE)
