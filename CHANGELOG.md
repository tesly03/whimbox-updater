# 更新记录

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
