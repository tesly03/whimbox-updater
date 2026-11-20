# Whimbox 奇想盒 · 免费自动更新器

依据奇想盒官方文档「手动更新奇想盒和路线」实现的**免费**替代方案——无需开通付费的「自动更新」会员，即可自动把后端、前端、跑图路线脚本保持在最新版本。

> 奇想盒（Whimbox）本身是开源免费的；付费会员仅提供官方自动更新与脚本订阅。本工具走的是官方文档给出的手动更新路径，不涉及任何破解。

## 能做什么

1. 查询 GitHub Releases 最新版本（直连 + 国内镜像多通道，自动规避墙与 API 限流）
2. 自动下载后端 `.whl`，用奇想盒内置 Python/pip 安装（等价于 APP 里的「手动更新后端」）
3. 自动同步 `nikkigallery/WhimboxScripts` 跑图路线脚本（可配「我的订阅」只跟指定脚本）
4. 可选：下载并静默安装前端 `whimbox_app-setup-x.y.z.exe`
5. 全程 sha256 校验 + 备份 + 失败自动回滚，支持手动回滚

只依赖 Python 标准库，直接用奇想盒自带的内置 Python 运行，无需额外安装。

## 快速开始（解压即用）

1. 到本仓库 **Releases** 下载最新的 `whimbox-updater-vX.Y.Z.zip`。
2. 解压到奇想盒安装目录内的任意一个文件夹，例如：

   ```
   C:\Program Files\whimbox_app\whimbox-updater\
   ```

   路径会自动推断：脚本会从自身所在位置向上查找奇想盒安装根目录
   （识别 `python-embedded\` 与 `resources\app.asar`），**无需修改任何配置**。
   即使奇想盒装在 D 盘等非默认位置也能直接使用。

3. 双击对应的 bat 即可：

| 文件 | 作用 |
|---|---|
| `update_now.bat` | 一键更新（后端 + 路线脚本） |
| `check_only.bat` | 只看版本，不改任何文件 |
| `rollback.bat` | 回滚后端到最近一次备份 |
| `subscriptions.bat` | 编辑「我的订阅」清单（只更新指定的脚本） |
| `install_task.bat` | 注册「每天 09:00 自动检查更新」的 Windows 计划任务 |

## 命令行用法

```bash
python whimbox_updater.py --check              # 仅检查版本，不改动
python whimbox_updater.py                      # 更新后端 + 同步脚本（推荐）
python whimbox_updater.py --install-frontend   # 同时静默安装新版前端 exe
python whimbox_updater.py --no-scripts         # 不同步跑图路线
python whimbox_updater.py --force              # 已是最新也强制重装
python whimbox_updater.py --rollback           # 回滚到最近一次后端备份
python whimbox_updater.py --watch "家园日常,朝夕心愿_采集"   # 设置订阅清单
python whimbox_updater.py --watch ""           # 清空订阅清单，恢复全量
python whimbox_updater.py --all-scripts        # 本次忽略订阅清单，全量同步
python whimbox_updater.py --help               # 查看全部参数
```

## 「我的订阅」清单

`_data/watchlist.json`（首次运行自动生成）里的 `scripts` 列表，只更新这里列出的脚本：

```json
{
  "scripts": ["家园日常.json", "朝夕心愿_采集.json"]
}
```

- 留空 `[]` 或删除该文件 → 全量同步所有脚本（默认）。
- 可用脚本名见 `C:\Program Files\whimbox_app\scripts\*.json`。

## 配置

路径已经全部自动推断，一般**无需改动**。需要调整的参数在 `whimbox_updater.py` 顶部「配置区」：

- `APP_DIR` — 奇想盒安装目录（默认 `C:\Program Files\whimbox_app`）
- `PROXIES` — GitHub 镜像通道列表（默认已按可用性排序）
- `PIP_MIRRORS` — pip 国内源列表

## 注意事项

- 更新前请**先关闭奇想盒 APP**，更新完成后**重启**，新后端才会生效。
- 下载依赖国内 GitHub 镜像，全部不可达时会回退到本地缓存/离线安装。
- 更新器放在安装目录内，卸载/重装奇想盒前请先备份 `_data\backups\`。

## 免责声明（重要）

- 本工具**仅自动化奇想盒官方文档公开的手动更新步骤**，不包含、也不分发奇想盒的任何程序文件（不含 `.exe`、`.whl` 等二进制产物），不逆向、不破解、不绕过奇想盒的任何付费功能或服务端鉴权。
- 版本信息来自 GitHub 公开 Releases，脚本来自公开仓库 `nikkigallery/WhimboxScripts`，均为公开资源；本工具不访问、不依赖奇想盒的付费「自动更新 / 脚本订阅」接口。
- 奇想盒（Whimbox）及其脚本、安装包、商标的著作权均归原作者所有。**请支持正版**；如需官方自动更新与脚本订阅服务，请通过官方渠道开通。
- 本项目与奇想盒官方无任何关联，作者不对使用本工具造成的任何后果负责。

## 许可

[MIT License](LICENSE)
