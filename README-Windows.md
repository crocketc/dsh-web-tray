# DSH Web Tray — Windows 使用说明

## 安装

1. 安装 Python 3.9+（勾选 Add to PATH）
2. 安装依赖：

```powershell
pip install -r requirements.txt
# 或
powershell -ExecutionPolicy Bypass -File scripts\install-dependencies.ps1
# 国内网络可带镜像：
powershell -ExecutionPolicy Bypass -File scripts\install-dependencies.ps1 -Index https://pypi.tuna.tsinghua.edu.cn/simple
```

3. 启动：

```powershell
python dsh-web-tray.py
```

首次运行弹配置向导：自动检测 `dsh` 安装（npm 全局 / 源码 pnpm / 本地 npx）→ 选端口 → 保存即启动。

### 后台运行（关掉终端不受影响）

`python dsh-web-tray.py` 附着在终端控制台上——**关闭终端 = 托盘退出**（Windows 控制台的 `CTRL_CLOSE_EVENT` 行为，且 Job Object 会按设计带走 dsh 树）。日常使用请用：

- **双击 `start-tray.cmd`**（推荐）：用 `pythonw.exe`（GUI 子系统，不附着任何控制台）后台启动，无窗口、关终端无影响
- 或在终端里执行 `pythonw dsh-web-tray.py`，之后随手关掉终端也不影响
- 或打包成 exe（见下文"打包"），双击运行

终端里的 `python dsh-web-tray.py` 适合开发调试（能直接看输出）。

## 日常使用

- 托盘图标颜色即状态：🔵 启动中 / 🟢 运行中 / 🔵(青) 外部启动 / ⚪ 已停止 / 🔴 意外退出或启动失败
- **双击托盘图标 = 打开浏览器**（用启动日志解析出的真实 URL）
- 右键菜单：重新启动 / 停止 / 开机自启 / 重新配置 / 帮助 / 退出
- 数据目录：`~\.dsh-web-tray\`（config.json + logs\）

## 开机自启

托盘菜单勾选"开机自启"即可（写入当前用户注册表，无需管理员）：

```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
  DSHWebTray = "C:\...\python.exe" "C:\...\dsh-web-tray.py"
```

打包成 exe 后同样适用（frozen 检测自动切换为 exe 自身路径）。

## Windows 特有行为（务必了解）

| 事项 | 说明 |
|------|------|
| 无终端窗口 | 子进程以 `CREATE_NO_WINDOW` 启动，后台静默运行 |
| pnpm 是 .cmd shim | 配置保存时用 `shutil.which()` 解析为全路径（直接 spawn `pnpm` 会 `FileNotFoundError`） |
| **无 SIGTERM** | Windows 无法向无控制台进程投递 SIGTERM；退出采用**进程树终止**（psutil 进程内枚举，taskkill /T /F 仅兜底）。dsh 存储带 torn-tail 容错，不会损坏数据 |
| 托盘被硬杀 | 子进程纳入 Job Object（KILL_ON_JOB_CLOSE）：任务管理器杀托盘 → 内核自动带走整棵 dsh 树，无孤儿 |
| 无扩展名 shim | 手动配置输 `...\npm\dsh`（无扩展名）也能自动补 `.cmd` 解析 |

## 打包为 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
# 产出 dist\dsh-web-tray.exe（单文件、无控制台，约 30-50MB）
```

把 exe 放到任意固定路径（如 `LocalAppData\Programs`），开机自启照常工作。

## 排障

- 日志：`~\.dsh-web-tray\logs\dsh-web.log`（dsh 输出）与 `tray.log`（托盘诊断）
- "启动失败"最常见原因：源码安装未构建前端 → 在仓库根执行 `pnpm install && pnpm run build`
- 端口冲突：配置端口被占用时显示"运行中（外部启动）"，属预期行为
- 用 Windows 工具手改 `config.json`（带 BOM）也能正常读取
