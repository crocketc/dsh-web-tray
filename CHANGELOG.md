# Changelog

本项目所有显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循语义化版本（SemVer）。

## [1.6.1] — 2026-08-24

### 修复
- **检测在 App 里一直失败**（macOS GUI PATH 陷阱）：dsh 是 node 脚本（shebang `#!/usr/bin/env node`），
  Finder 启动的 App PATH 没有 node，`dsh --version` 以 127 退出导致永远「无法获取当前版本」——
  检测子进程改用与 dsh 启动同款的增强 PATH（build_subprocess_env）
- **检查失败被文案说成「已是最新」**：手动检查失败现在如实报「检查失败：<原因>」
- **macOS 通知不显示**（pystray 走 osascript，macOS 15 上常被静默丢弃）：改用系统原生
  UNUserNotificationCenter（首次启动弹授权询问，授权后可靠送达；失败回退 pystray）

## [1.6.0] — 2026-08-24

### 新增
- 更新检测：启动约 30 秒后自动检查 dsh 新版本，此后每 24 小时一次；帮助菜单可手动「检查更新」（必有结果通知；无法判定时如实反馈原因）
- 分安装类型检测：npm 全局/本地对比 registry latest（semver 语义，含 prerelease；尊重用户 registry 配置，镜像滞后不误报；本地安装读 node_modules 包描述取版本）；pnpm 源码对比 git 远端落后提交数（计数变化可再次通知）；manual 不自动判定
- 升级入口：发现新版本时顶层菜单出现「🆕 升级到 x.y.z」（源码安装显示落后提交数），点击后台静默升级（无窗口、不自动 sudo），全程输出落升级日志
- 升级结果闭环：成功通知 + 「重启以应用新版本」一键重启（绝不自动重启）；失败通知含原因摘要与完整手动命令；external 态提示自行重启外部实例；超 30 分钟自动终止
- 通知节流：同一目标版本（源码按落后数）只弹一次系统通知；24h 检查冷却防重启风暴；冷却/节流/缓存状态落盘，重启不失效
- 诊断入口：`dsh-web-tray.py --check-update` 打印安装类型/registry/当前版本/最新版本/结论（源码安装含 git 判定），排查更新提示问题无需翻日志

### 修复
- **v1.6.0 首发包启动即退出**：菜单装配引用了未实现的 `_build_restart_menu_item`，托盘一进菜单构建即 AttributeError 退出——已实现该方法并新增整条菜单装配链回归测试（tests/test_tray_menu_assembly.py，源码实跑 + 打包存活探测双重验证后重新发布制品）
- 更新状态三键（lastUpdateCheckAt/lastNotifiedVersion/lastKnownLatestVersion）此前仅存内存，重启后冷却与节流失效——现随检查/升级落盘
- 本地（npx）安装当前版本此前误取 npx 自身版本号，导致永远判"已是最新"——改读 node_modules 包描述
- 源码安装通知节流键此前恒为 unknown，导致一生只通知一次——改按落后提交数区分
- 重新配置向导后清空旧更新缓存，避免换安装类型后残留升级项

### 设计
- ADR-0001 registry HTTP 查询（不依赖 npm CLI）；ADR-0002 静默升级执行；ADR-0003 分安装类型语义；ADR-0004 结果直感+一键重启；ADR-0005 调度节流；ADR-0006 菜单呈现（docs/adr/）
- 实机手动验收清单：docs/acceptance-update-check-live.md

## [1.5.0] 及更早

见 git 历史与 README 平台说明（Windows/macOS 安装包、配置向导、托盘状态灯、外部启动感知、优雅退出等基础能力）。

### 已知问题（存量，与本功能无关）
- wizard.py:307 except 变量被 lambda 延迟捕获的竞态（tkinter after 回调可能 NameError）
