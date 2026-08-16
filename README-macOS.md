# DSH Web Tray — macOS 使用说明

## 安装

1. Python 3.9+（Homebrew Python 需额外装 tk，配置向导要用）：

```bash
brew install python-tk
pip3 install -r requirements.txt
# 或
bash scripts/install-dependencies.sh
```

2. 启动：

```bash
python3 dsh-web-tray.py
```

菜单栏出现状态图标；首次运行弹配置向导。

## macOS 关键点

### PATH 解析（双击/自启场景的核心）

从 Finder 或 LaunchAgent 启动的 GUI 应用，PATH 只有 `/usr/bin:/bin:/usr/sbin:/sbin`——Homebrew/nvm/volta 装的 node/pnpm 全不在路径上。本工具的对策：

- 配置保存时立即把命令解析为**绝对路径 argv** 存入配置，运行期不依赖 PATH
- 解析不到时用**登录 shell**（`$SHELL -l -c 'command -v …'`）兜底拿真实 PATH

### 无需任何特殊权限

菜单栏图标、启动/停止子进程、LaunchAgent 自启都**不涉及**辅助功能（Accessibility）/完全磁盘访问。不要被任何引导去开这些权限。

### tkinter 与 pystray 主线程冲突

两者都要求主线程。配置向导因此**始终以子进程运行**（`Popen([sys.executable, wizard.py])`），结果经配置文件回传——从托盘菜单拉起向导不会挂起。

## 开机自启

托盘菜单勾选"开机自启"：

- 写 `~/Library/LaunchAgents/com.dsh.webtray.plist`
- 用现代 API `launchctl bootstrap gui/$(id -u)`（`load/unload` 已废弃）
- 日志重定向到 `~/Library/Logs/com.dsh.webtray.log`

取消自启 = `bootout` + 删除 plist。

## 优雅退出（官方信号契约）

托盘"退出"向 dsh web 发 **SIGTERM**——这是 dsh 官方约定的 supervisor 停止信号（exit 0，dsh 自行 `fiber.dispose()` 清理整棵进程树，含 agent 子进程）。超时才升级树杀兜底。

验证：

```bash
pgrep -fl dsh   # 退出托盘后应无残留
```

## 打包为 .app / DMG

```bash
bash scripts/build-app.sh
```

流程：PyInstaller `--windowed --onefile` → **ad-hoc 签名**（必做，未签名 .app 会被 Gatekeeper 判"已损坏"）→ 生成 DMG。

分发注意：

- **首次打开**：ad-hoc 签名未公证，需右键→打开，或 `xattr -cr "DSH Web Tray.app"`
- **正式分发**：需 Apple Developer ID 签名 + `notarytool` 公证
- **双架构**：arm64 与 x86_64 必须分别在对应架构构建（本仓库 CI 已配双架构矩阵）

## 测试清单（macOS 环境）

- [ ] 终端 `python3 dsh-web-tray.py`（PATH 完整）
- [ ] 双击 .app（PATH 仅系统路径 → 验证配置内绝对路径 argv）
- [ ] LaunchAgent 自启（验证 `launchctl bootstrap`）
- [ ] SIGTERM 优雅退出，`pgrep -fl dsh` 无残留
- [ ] 托盘图标 Retina/深浅色主题
- [ ] tkinter 向导从托盘菜单拉起不挂起（子进程方案）
- [ ] Homebrew Python + python-tk 可用
