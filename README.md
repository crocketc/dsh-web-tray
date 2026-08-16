# DSH Web Tray

[English-ish quick facts below; 完整说明以中文为准]

DSH Web GUI（`dsh web`）的系统托盘守护器：双击启动、后台运行、不弹终端，通过系统托盘控制并显示运行状态。跨平台支持 Windows 与 macOS（Linux 可选）。

## 功能

- **后台运行**：启动 `dsh web` 不显示终端窗口（Windows `CREATE_NO_WINDOW` / POSIX 独立进程组）
- **系统托盘集成**：状态图标（启动中 / 运行中 / 运行中·外部启动 / 已停止 / 意外退出）+ 右键菜单
- **官方就绪信号**：解析 stdout 的 `dsh web: http://...` URL 行判定就绪（不轮询端口），支持 `--port 0` 自动分配端口
- **优雅退出**：POSIX 发 SIGTERM（dsh 官方 supervisor 契约，exit 0，自行清理整棵进程树）；Windows 用进程树终止（无法投递 SIGTERM，见下文"平台差异"）
- **崩溃感知**：dsh web 意外退出即切换托盘状态，一键重新启动
- **外部实例检测**：终端里已在跑 dsh web 时，托盘显示"运行中（外部启动）"，不重复启动
- **开机自启**：Windows 注册表（HKCU Run）/ macOS LaunchAgent（`launchctl bootstrap`）
- **配置向导**：自动检测安装方式（源码 / npm 全局 / npm 本地，含前端 dist 构建前置检查），未安装时提供安装指引
- **防重复启动**：锁文件 + PID 存活检查（create_time 防 PID 复用）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt        # pystray psutil pillow

# 2. 运行（Windows）
python dsh-web-tray.py

# 2. 运行（macOS，Homebrew Python 需先 brew install python-tk）
python3 dsh-web-tray.py
```

首次运行会弹出配置向导：自动检测 dsh 安装 → 选择端口 → 保存后自动启动。托盘图标出现在系统托盘/菜单栏。

## 托盘菜单

```
状态行（动态：● 运行中 (http://127.0.0.1:3080) 等）
───────────
打开浏览器          ← 双击托盘图标同效，用解析出的 URL
重新启动            ← 崩溃/停止时可用
停止
开机自启（已启用/未启用）
重新配置
───────────
帮助
  ├── 如何安装 DSH
  ├── 访问官方文档
  └── 打开日志目录
退出                ← 优雅停止 dsh 进程树
```

## 配置

配置文件：`~/.dsh-web-tray/config.json`（可用 `DSH_WEB_TRAY_HOME` 环境变量重定向，便于测试/便携部署）

```json
{
  "dshType": "pnpm",
  "dshArgv": ["C:/deepseek-harness/node_modules/.bin/pnpm.cmd", "dsh", "web"],
  "dshArgvDisplay": "pnpm dsh web",
  "dshDir": "C:\\deepseek-harness",
  "dshPort": 3080,
  "lastUrl": "http://127.0.0.1:3080",
  "autostart": false
}
```

要点：

- **命令存 argv 数组**（不存字符串）：路径含空格也安全（`C:\Program Files\...`）
- `dshArgv` 在保存时已解析为**绝对路径**——运行期（尤其 macOS GUI 环境 PATH 不全）不再依赖 PATH
- `dshPort = 0` 表示系统自动分配，实际地址从启动日志 URL 行回读
- 读取兼容 UTF-8 BOM（Windows 工具手改配置不炸）

## 日志

- `~/.dsh-web-tray/logs/dsh-web.log` — dsh web 的 stdout/stderr（排空线程持续落盘，>5MB 自动轮转）
- `~/.dsh-web-tray/logs/tray.log` — 托盘自身诊断日志

## 进程生命周期（设计依据 dsh 源码核实的 supervisor 契约）

```
启动 ──→ 就绪 ──→ 运行监控 ──→ 退出
 │         │          │           │
 PATH解析  解析stdout  崩溃检测    SIGTERM→树杀兜底
 spawn     URL行      状态菜单    (Windows: 树终止)
 记录PID
```

| 契约 | 源码依据 |
|------|---------|
| stdout 的 `dsh web: http://...` 行是官方就绪信号 | `packages/bundle/web-app/src/index.ts` |
| SIGTERM = 官方 supervisor 优雅停止（exit 0，自行清理进程树） | `apps/cli/src/profile-boot.ts` |
| `--port 0` 支持系统自动分配端口 | `packages/bundle/web-app/src/startup.ts` |
| 源码安装必须先构建前端 dist | `web-app/src/index.ts`（`frontend dist not built` 报错） |

### 平台差异（诚实的说明）

- **Windows 无法向无控制台的进程投递 SIGTERM**（CTRL 事件要求共享控制台），因此退出采用**进程树终止**（psutil 枚举整树逐个终止，进程内 API；taskkill /T /F 仅作兜底）。缓解：dsh 会话存储带 torn-tail 容错，硬杀不损坏数据。
- **Windows 额外保护**：子进程纳入 Job Object（`KILL_ON_JOB_CLOSE`）——托盘自身被硬杀（任务管理器等）时，内核自动终止整棵 dsh 树，不留孤儿。
- **macOS 无需任何特殊系统权限**：菜单栏图标、启动/停止子进程、LaunchAgent 均不涉及辅助功能/完全磁盘访问。

## 开发

```bash
# 单元测试（50 个）
python -m unittest discover -s tests

# 真实 dsh 集成测试（--port 0，不影响运行中的实例）
DSH_WEB_TRAY_LIVE=1 python -m unittest tests.test_integration_live -v

# 生成图标资源
python scripts/generate_icons.py
```

项目结构：

```
dsh-web-tray/
├── dsh-web-tray.py     # 主程序（托盘 + 状态机 + 编排）
├── dsh_process.py      # 进程生命周期核心（PATH 解析/spawn/URL 就绪/监控/退出）
├── config.py           # 配置持久化（argv 数组）
├── detect.py           # dsh 安装检测（源码/全局/本地 + dist 构建检查）
├── wizard.py           # 配置向导（tkinter，子进程运行）
├── trayicons.py        # 状态图标（PIL 动态生成）
├── singleinstance.py   # 单实例锁（PID + create_time）
├── platforms/          # ⚠️ 不能叫 platform/（遮蔽标准库）
│   ├── windows.py      # 注册表自启 + explorer
│   ├── macos.py        # LaunchAgent + open
│   └── linux.py        # XDG autostart + xdg-open（可选）
├── resources/          # icon.png / icon.ico（macOS icns 打包时生成）
├── scripts/            # 依赖安装 / 打包 / 图标生成
└── tests/              # 单元 + 集成测试
```

## 打包分发

```bash
bash scripts/build.sh          # 跨平台入口
# Windows → dist/dsh-web-tray.exe（PyInstaller --onefile --noconsole）
# macOS   → DSH Web Tray.app + dsh-web-tray-<arch>.dmg（含 ad-hoc 签名）
```

平台细节见 [README-Windows.md](README-Windows.md) 与 [README-macOS.md](README-macOS.md)。

## 常见问题

**托盘没有图标出现？** 确认依赖完整：`python -c "import pystray, PIL, psutil"`；查看 `~/.dsh-web-tray/logs/tray.log`。

**启动失败（start_failed）？** 查看日志 `~/.dsh-web-tray/logs/dsh-web.log`。源码安装需先 `pnpm install && pnpm run build`（前端 dist 未构建时 dsh 启动即失败）；通过菜单"重新配置"重跑检测。

**显示"运行中（外部启动）"？** 配置端口已被其它 dsh 实例监听（如终端里手动启动的）。托盘只监控、不重复启动；"停止"对外部实例不可用。

**想换端口？** 托盘菜单 → 重新配置；或手改 `config.json` 的 `dshPort`（0 = 自动分配）。

## 许可

随 DSH（DeepSeek Harness）生态使用，见上游仓库：<https://github.com/deepseek-ai/harness>
