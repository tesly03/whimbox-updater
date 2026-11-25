# Whimbox 奇想盒更新器

自动执行奇想盒公开的手动更新步骤：检查 GitHub Releases、安装后端 wheel、同步公开路线脚本，并按需运行官方前端安装器。

**v1.1.0 支持 Windows EXE：电脑上无需额外安装 Python。** EXE 在 GitHub Actions 的 Windows 构建机上生成，本机只运行更新器。

## 下载与运行

在 [Releases](https://github.com/tesly03/whimbox-updater/releases) 下载最新的 `whimbox-updater-vX.Y.Z-windows-x64.zip`，解压到奇想盒安装目录内，例如：

```text
C:\Program Files\whimbox_app\whimbox-updater\
```

双击 `update_now.bat` 或 `whimbox-updater.exe`。更新器从所在位置向上查找 `python-embedded\python.exe` 和 `resources\app.asar`。安装在其他盘符也适用。写入安装目录时会申请管理员权限；更新前请退出奇想盒，完成后重新启动。

Windows x64 EXE 面向 Windows 10/11。它包含更新器自己的 Python 运行时；安装后端仍使用奇想盒原有的内置 Python，更新器不会重新打包奇想盒程序文件。

独立 EXE 也可放在其他目录，用命令行指定奇想盒的位置：

```powershell
.\whimbox-updater.exe --app-dir "D:\Apps\whimbox_app"
```

源码用户下载 source ZIP，将更新器放入安装目录后，通过原有 BAT 或内置 Python 运行即可，无第三方运行依赖。

## 常用入口

| 入口 | 作用 |
| --- | --- |
| `update_now.bat` | 更新后端并同步路线 |
| `check_only.bat` | 仅检查版本，保留安装不变 |
| `rollback.bat` | 从最近一次完整备份恢复后端 |
| `subscriptions.bat` | 编辑订阅，保存并关闭记事本后同步 |
| `install_task.bat` | 安装每日 09:00 自动更新任务 |

BAT 优先启动同目录 EXE；源码包则查找奇想盒的内置 Python。退出码会保留，失败时控制台显示错误。

## 更新与恢复

- 安装包必须通过 GitHub Release 提供的 SHA256 校验；缺少校验值时停止安装。
- 下载和备份失败时，现有后端保持原状。只有安装已开始且本次完整备份可用时才执行自动回滚。
- 备份先写入临时目录，完整后才登记；恢复前检查文件摘要，恢复时保留当前目录供失败撤回。
- 后端 pip 安装使用 `--no-deps`，保留现有依赖；安装后执行 `pip check` 与 `import whimbox`。如果新版本需要尚未安装的依赖，将恢复旧后端，并提示通过官方安装器补齐依赖后重试。
- 路线、配置和状态文件使用同目录临时文件及原子替换。路线写入失败时保留原文件；已有同名路线被更新前会备份。
- 同一数据目录的更新、回滚和计划任务使用进程锁，避免默认路径下重复执行。
- 前端安装、路线同步或后端更新任一步失败，整体退出码为非零，计划任务可以识别失败。

前端安装由官方 NSIS 安装器执行，前端程序本身不在后端备份的恢复范围。安装中断时请检查日志，必要时重新运行官方安装器。

## 官方前端与后端

奇想盒由 [Electron 前端](https://github.com/nikkigallery/whimbox_app) 和 [Python 后端](https://github.com/nikkigallery/Whimbox) 组成，前端使用安装目录内的 `python-embedded` 启动后端。前端仓库已说明，后续安装包统一发布到 [后端 Releases](https://github.com/nikkigallery/Whimbox/releases)。更新器分别选择最新可用 wheel 和前端安装器，不假定每次 Release 都同时提供两种附件，前后端版本也不要求相同。

默认只更新后端与路线；`--install-frontend` 才运行官方前端安装器。官方安装脚本会保留 Python 环境、配置和路线等用户数据。本工具使用公开 Release 与脚本仓库，无需登录奇想盒，也不访问其会员自动更新接口。

## 配置、日志与备份

v1.1.0 默认将数据移到当前用户可写的位置，按奇想盒安装路径分别存储：

```text
%LOCALAPPDATA%\WhimboxUpdater\<安装路径标识>\
  watchlist.json
  state.json
  downloads\
  logs\
  backups\
```

运行时会输出实际路径。使用 `--data-dir "D:\WhimboxUpdaterData"` 可指定固定目录；手动运行和计划任务应使用相同目录。请保留该目录以便恢复后端。

更新器会从同目录旧版 `_data` 自动迁移订阅和 Release 缓存，并继续识别旧备份；迁移保留旧文件。新后端备份自动保留最近 3 份。路线备份暂由用户按需清理。

## 我的订阅

双击 `subscriptions.bat`，在记事本中填写：

```json
{
  "scripts": ["家园日常.json", "朝夕心愿_采集.json"]
}
```

`scripts: []` 表示全量同步。JSON 错误、类型错误或无效文件名会停止本次路线同步，避免意外扩大范围。路线名在上游缺失时会报告失败。同步保留仓库里没有的本地文件；同名文件仍视为上游维护的路线，因此自定义路线请使用独立名称。

## 命令行

以下参数也适用于 `python whimbox_updater.py`：

```powershell
.\whimbox-updater.exe --version
.\whimbox-updater.exe --check
.\whimbox-updater.exe --no-scripts
.\whimbox-updater.exe --force
.\whimbox-updater.exe --install-frontend
.\whimbox-updater.exe --rollback
.\whimbox-updater.exe --watch "家园日常,朝夕心愿_采集"
.\whimbox-updater.exe --watch ""
.\whimbox-updater.exe --all-scripts
.\whimbox-updater.exe --edit-subscriptions
.\whimbox-updater.exe --app-dir "D:\Apps\whimbox_app" --data-dir "D:\UpdaterData"
.\whimbox-updater.exe --help
```

`--force` 会向 pip 传入 `--force-reinstall`，依赖仍保持不变。`--check` 只保留日志、缓存与检查状态，不更新奇想盒安装。无参数双击 EXE 后会等待回车，便于查看结果；`--pause` 可为其他命令启用相同行为。

## 计划任务

双击 `install_task.bat`，默认安装每日 09:00 运行的 `WhimboxAutoUpdate`。任务优先使用 EXE，使用最高权限、与交互运行一致的数据目录，并限制同一任务并发实例。建议先手动完成一次更新，再启用定时任务。

删除任务：

```powershell
Unregister-ScheduledTask -TaskName WhimboxAutoUpdate -Confirm:$false
```

## GitHub 云端打包

推送 `vX.Y.Z` 标签后，[Build and release 工作流](https://github.com/tesly03/whimbox-updater/actions) 在 Windows x64 上：

1. 使用 Python 3.12 运行标准库回归测试。
2. 安装固定版本 PyInstaller，构建单文件控制台 EXE。
3. 执行 EXE 的 `--version` 和 `--help` 冒烟测试。
4. 打包 Windows 便携 ZIP、源码 ZIP、独立 EXE 及 `SHA256SUMS.txt`。
5. 上传构建产物，并创建草稿 Release，供维护者核对后发布。

也可在 Actions 页面手动运行工作流进行构建。构建使用 GitHub 自动提供的短期 `GITHUB_TOKEN`，源码中无需配置个人令牌。个人电脑无需安装打包工具。

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
