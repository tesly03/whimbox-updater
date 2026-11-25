# 更新记录

## v1.3.0

### 网络高可用与免配额回退

- 保留官方 GitHub REST API（支持 `ETag` 304 与可选 `GITHUB_TOKEN`）为首选通道，新增**免 API 限流的直连网页回退链**：当 `api.github.com` 触发 403 限流或暂不可用时，自动直连 `releases.atom` 与 `releases/expanded_assets/<tag>` 获取官方附件链接与原生 SHA256 摘要，并正确解码 HTML 实体更新说明。
- `WhimboxScripts` 最新提交检测新增 Git Smart-HTTP `info/refs?service=git-upload-pack` 通道（仅约 300 字节且不受 GitHub REST API 配额限制），支持直连与代理无缝回退。
- 移除已失效的 `ghproxy.net`，调整内置代理为 `gh-proxy.org`、`ghfast.top`、`gh-proxy.com`，新增单次运行内代理熔断机制（仅熔断超时/连接错误/5xx，保留对端点 4xx 限制的兼容）以及跨独立运营方的多代理 API + 网页摘要交叉校验。
- 安装包下载支持 `Range` 断点续传（按 SHA256 隔离 `.part` 临时缓存，支持校验失败自动清除并重下、自动清理 3 天以上旧断点文件）及无进展 60 秒停滞超时检测。

### 奇想盒前后端协同与安装健壮性

- 解决官方前端退出时未终止 `python-embedded\python.exe` 导致后台孤儿进程阻塞更新器的问题：进程守卫可精准区分前端活跃运行与残留后台 `python.exe`，当仅存在残留后端时默认自动安全清理（支持 `--no-kill-orphan-backend` 关闭）；若前端正在运行仍默认拒绝修改，并新增 `--stop-running` 按需自动关闭前后端。
- 全新安装自动补齐依赖：当目标环境尚未安装 `whimbox`（`backend_version == "unknown"`，仅含官方裸 `Python312.zip`）时自动启用国内 PyPI 镜像补齐运行依赖；升级存量环境时在备份/写入前直接读取 wheel `METADATA` 执行 `Requires-Dist` 预检，缺少依赖立即提示且不触碰当前环境。
- `pip check` 升级为基线差异比对：仅阻断涉及 `whimbox` 的依赖问题或本次安装新引入的冲突，不再因环境中原本存在的无关第三方包提示永久卡死更新；同时在 `backup.json` 中记录环境包版本快照。
- `backend_version()` 新增单 `.dist-info` 纯文件快速探测路径，减少 Windows 下重复拉起 `python.exe` 的冷启动开销。

### 路线同步、交互入口与构建安全

- 订阅匹配同时支持仓库文件名、省略 `.json` 名称以及奇想盒界面内显示名称（`info.name`，如 `祈愿树林 星荧草`），新增 `--list-scripts` 列出全部远端路线及显示名。
- 8 条以内的文件名订阅在已知 commit SHA 时直接通过 `raw.githubusercontent.com` 按需精准拉取，跳过整仓 `main.zip` 下载；全量同步时可安全归档上游已移除且未被用户修改过的旧路线文件，保护用户自定义与修改过的路线。
- 修复 `update_now.bat --quiet` 或 `--help`/`--version` 被 `--pause` 阻塞的问题；禁用 `argparse` 参数缩写防止绕过提权前配置编辑剥离；运行前端 NSIS 安装器时隔离 PyInstaller 环境变量并将工作目录切换至下载目录避免占用 `APP_DIR`。
- 新增 `update_all.bat` 一键联合更新前后端与路线，新增 `--check-self` 检查更新器自身版本。
- `install_task.ps1` 默认改用 `-LogonType Interactive -RunLevel Highest`（兼容微软账户/PIN 登录并复用当前用户本地代理，保留 `-BackgroundS4U` 与 `-StopRunning` 可选开关）。
- 便携版 ZIP 顶层包裹 `whimbox-updater/` 目录防止解压散落；PyInstaller 打包嵌入 Windows 版本资源信息并裁剪无关标准库模块；CI 增加 PowerShell AST 语法检查，Release 工作流增加 `actions/attest-build-provenance` 构建溯源证明。

## v1.2.0

### 前后端分离适配与运行安全

- 新增运行中进程检测：更新或回滚前通过 Win32 API 检测目标安装目录下正在运行的 `whimbox_app.exe` 与内置 `python.exe`（自动排除更新器自身与父进程链），防止文件锁导致半成品残局。
- 新增 `update_frontend.bat` 与 `--no-backend` / `--scripts-only` 独立入口，契合奇想盒 Electron 前端与 Python 后端分离发布结构；前端静默安装支持通过 NSIS `/D=` 参数精准覆盖自定义安装目录，并校验纯英文路径约束。
- 后端运行环境与官方 `WindowsPythonEnvironmentService` 对齐：隔离 `PYTHONHOME`、`PATH` 并清理 `PYTHONPATH` 与 PyInstaller 运行时变量；安装后健康检查在 `APP_DIR` 下验证 `whimbox` 与 `whimbox.main` 入口。
- 默认后端安装维持 `--no-deps` + `pip check` 事务安全策略；依赖检查失败时提取具体冲突包名，并新增显式可选 `--sync-deps` 参数（通过国内 PyPI 镜像同步第三方依赖）或提示在奇想盒设置中手动安装已下载 wheel。
- 改进 `app.asar` 版本解析（按 ASAR 头部 `json_size` 精确读取），并在检查与更新日志中展示新版 Release 更新说明摘要。

### 安全校验、网络与增量路线同步

- GitHub Releases 查询支持 `ETag` / `If-None-Match`（304 不消耗 API 额度）及可选 `GITHUB_TOKEN`（仅发往官方 `api.github.com`，绝不透传给第三方代理）。
- 当直连 GitHub API 失败回退至第三方代理时，自动对多代理返回的安装包 SHA256 摘要做交叉一致校验，摘要不一致或伪造版本时立即中止。
- 支持 `--direct`、`--proxy` 与 `WHIMBOX_PROXIES` 自定义代理策略；下载过程改为单遍流式计算 SHA256 并输出下载进度。
- 路线同步接入 `WhimboxScripts` 最新提交 `SHA` 与 `scripts_state.json` 增量缓存，上游无新提交且本地文件齐全时跳过 `main.zip` 下载；单条异常路线不再中断整批拉取，Windows 下支持订阅名大小写不敏感匹配。

### 权限、磁盘维护与构建流

- 按需提权：写入目录当前用户可写且无需安装前端时不再弹出 UAC；`--edit-subscriptions` 与 `--watch` 移至提权前执行，避免以管理员权限打开记事本，且仅触发路线同步。
- 修复 `install_task.bat` 提权时丢失命令行参数与原用户 `%LOCALAPPDATA%` 的问题，`install_task.ps1` 新增 `-Time` 参数自定义计划任务时间。
- 自动清理旧日志（保留 14 天）、旧下载包（各保留 3 份）与旧路线备份（保留 10 份）；备份排序优先依据 `backup.json` 创建时间，文件替换与删除增加短暂 Windows 锁重试退避；手动 `--rollback` 同步刷新 `state.json`。
- 新增跨平台 `CI` 工作流（Ubuntu + Windows 单测与格式校验）；Release 工作流改为从 `CHANGELOG.md` 自动提取发布说明并在 `ubuntu-latest` 创建草稿 Release；构建脚本自动从 `requirements-build.txt` 读取 PyInstaller 版本。

## v1.1.0

### Windows EXE 与云端构建

- 新增独立 Windows x64 EXE，无需另装 Python。
- 新增 GitHub Actions：回归测试、PyInstaller 构建、EXE 冒烟测试、便携包、源码包和 SHA256 校验清单。
- 标签构建生成草稿 Release，核对后发布；运行时不需要 GitHub 个人令牌。
- BAT 统一启动逻辑，优先使用 EXE，保留子进程退出码。

### 更新可靠性

- 修复下载、校验、备份失败也触发历史回滚的问题。
- 自动回滚绑定本次完整备份；备份原子登记、恢复前验证文件摘要。
- 恢复过程先暂存当前版本，失败时尝试撤回，清理重复版本 metadata；撤回再次失败时保留恢复暂存和说明，避免删除可恢复文件。
- 后端安装保留现有依赖，不再自动改动 setuptools 或依赖环境。
- 安装后检查版本、依赖和模块导入；失败时恢复更新前后端。
- `--force` 实际传入 `--force-reinstall`。
- 严格要求安装包 SHA256，并检查官方 Release 下载地址与附件名称。

### 路线与配置

- 路线、订阅与状态改为原子写入，文件写入失败时保留旧版本。
- 订阅配置损坏时停止同步，避免意外全量覆盖；缺失订阅路线会报告失败。
- 新增进程锁，避免默认数据目录下手动运行与计划任务互相覆盖。
- 数据迁至用户可写目录，按安装路径隔离；自动迁移旧订阅并识别旧备份。
- 前端与路线失败反映到整体退出码；成功运行清除历史错误状态。
- 修复版本探测回退时 1.9.0 与 1.10.0 的字符串排序问题。

### Windows 入口

- 更新与回滚在写入安装目录前检查提权；支持 EXE 自身提权并等待真实退出结果。
- 订阅入口先创建配置，通过统一更新器编辑和同步。
- 计划任务优先使用 EXE、固定数据路径、禁止同一任务并发，并使用一小时总时限。
- 新增 `--app-dir`、`--data-dir`、`--print-data-dir`、`--edit-subscriptions`、`--pause` 与 `--version`。

### 使用提示

- 更新前退出奇想盒，更新后重新启动。
- 若新版本所需依赖与本机不匹配，更新器会回滚；请通过官方安装器补齐依赖后重试。
- 后端回滚范围不包含官方前端安装器对应用目录的改动。
- 本版 EXE 暂无商业代码签名；请核对 Release 中的 SHA256SUMS。

## v1.0.0

- 初始版本：检查版本、安装后端、路线订阅同步、前端可选更新与计划任务。
