# DSH Web Tray — macOS 使用指南

## 安装

### 方式一：下载 DMG（推荐）

1. 打开 [Releases 页面](https://github.com/crocketc/dsh-web-tray/releases/latest)，按芯片型号下载：
   - **M 系列**（M1/M2/M3/M4）→ `dsh-web-tray-arm64-*.dmg`
   - **Intel** → `dsh-web-tray-x86_64-*.dmg`（不确定型号：左上角  → 关于本机）
2. 打开 DMG，把 **DSH Web Tray** 拖入"应用程序"
3. **首次打开：右键点应用 → 打开 → 再点"打开"**（它没做 Apple 公证——正式公证要付费开发者账号；右键打开是一次性放行，之后正常双击即可）

应用只出现在**菜单栏**（右上角），不占 Dock。

### 方式二：从源码跑

需要 Python 3.9+。Homebrew 装的 Python 要额外装 tk（配置向导依赖）：

```bash
brew install python-tk
pip3 install -r requirements.txt
python3 dsh-web-tray.py
```

## 首次运行

弹出配置向导：自动检测 dsh 安装 → 选端口（默认 3080）→ 保存即启动，菜单栏出现图标。

## 日常使用

| 操作 | 怎么做 |
|------|--------|
| 打开 Web 界面 | 点一下菜单栏图标（默认项即"打开浏览器"，自动带正确端口） |
| 看当前状态 | 图标颜色：🟢绿=运行中 / 🩵青=外部启动 / 🔴红=挂了 / ⚪灰=已停止 / 🔵蓝=启动中 |
| 重启 / 停止 dsh | 点图标呼出菜单 |
| 彻底退出 | 菜单 → 退出（先优雅停掉自己启动的 dsh，再消失） |

**一个重要语义**：托盘只管理**自己启动的** dsh。终端里手动跑的那份显示为青色"外部启动"，退出托盘不会碰它。想统一交给托盘管理，先 Ctrl+C 停掉终端里那份，再菜单 → 重新启动。

## 开机自启

菜单勾选 **开机自启** 即可（登录后自动启动，`~/Library/LaunchAgents/` 下写一个 LaunchAgent，无需任何权限确认）。取消 = 再点一次。

## 无需任何特殊权限

菜单栏图标、启动/停止 dsh、开机自启——**都不涉及**辅助功能（Accessibility）、完全磁盘访问这类系统权限。如果哪天有弹窗引导你去开这些权限，那不是本应用的行为，请警惕。

## 排障速查

| 症状 | 处理 |
|------|------|
| 应用打不开，提示"已损坏" | Gatekeeper 误判（未公证应用的通病）：终端执行 `xattr -cr /Applications/DSH\ Web\ Tray.app` 后再打开 |
| 红色"启动失败" | 源码安装的 dsh 缺前端构建：去 dsh 仓库根目录 `pnpm install && pnpm run build`，然后菜单 → 重新启动 |
| 一直蓝色"启动中" | 首次启动含初始化，等 30-60 秒正常 |
| 显示"外部启动" | 端口上已有别的 dsh 实例（预期行为），见上文"重要语义" |
| 想换端口 | 菜单 → 重新配置 |

日志位置（菜单"帮助 → 打开日志目录"直达）：

```
~/.dsh-web-tray/
├── config.json          # 配置（可手改，改完重启应用生效）
└── logs/
    ├── dsh-web.log      # dsh 的输出（排障主战场）
    └── tray.log         # 托盘自身诊断
```

## 技术备注（可选阅读）

- **优雅退出**：退出时向 dsh 发 SIGTERM——这是 dsh 官方约定的 supervisor 停止信号（exit 0，dsh 自行清理整棵进程树，包括 agent 子进程），不留孤儿。超时才升级强杀兜底
- **PATH 陷阱的对策**：从 Finder/LaunchAgent 启动的应用看不到 Homebrew/nvm/volta 装的 node/pnpm（GUI 进程的 PATH 只有系统目录）。本工具在**保存配置时就解析成绝对路径**，运行期不依赖 PATH——所以双击启动和终端里跑一样可靠
- **配置向导的隔离**：向导在独立子进程运行（tkinter 与菜单栏框架都要求主线程，同进程会冲突挂起），所以从菜单拉起向导永远不会卡死应用

## 自己打包 .app / DMG

```bash
bash scripts/build-app.sh
# 产出 DSH Web Tray.app + dsh-web-tray-<架构>.dmg（含 ad-hoc 签名）
```
