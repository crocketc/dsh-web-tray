# DSH Web GUI 托盘守护器 - 开发计划

## 项目背景

当前 `pnpm dsh web` 启动后会占用一个终端窗口，关闭窗口会杀掉服务器。用户需要一个更优雅的启动方式：双击启动，后台运行，不弹终端，可通过系统托盘控制。

## 需求分析

### 核心需求

1. **后台运行**：启动 `dsh web` 时不显示终端窗口
2. **系统托盘集成**：最小化到系统托盘，提供右键菜单，**显示运行状态**
3. **开机自启**：可选开机自动启动
4. **优雅退出**：通过托盘菜单退出时按 dsh 官方信号契约停止进程（见下文）
5. **崩溃感知**：dsh web 意外退出时托盘能感知并提示，可一键重启

### 用户场景

- 场景 1：双击启动器 → 后台启动 dsh web → 托盘显示图标 → 浏览器访问
- 场景 2：开机自动启动 → 托盘图标出现 → 右键菜单打开浏览器
- 场景 3：右键托盘 → 退出 → **优雅停止** dsh 进程树 → 托盘图标消失
- 场景 4：dsh web 崩溃/被外部杀死 → 托盘切换到"已停止"状态 → 菜单提供"重新启动"
- 场景 5：终端里已在跑 dsh web → 托盘检测到端口已监听 → 显示"运行中（外部启动）"，不重复启动

## 技术方案

### 技术选型：Python + pystray（跨平台）

| 方案 | 优点 | 缺点 | 结论 |
|-----|------|------|------|
| Python + pystray | 代码简洁、跨平台（Windows/macOS/Linux）、易维护 | 需要 Python 环境 | ✅ 推荐 |
| AutoHotkey | 无需运行时 | 仅支持 Windows、学习曲线陡、UI 难做 | ❌ |
| C# WinForms | 原生体验 | 仅支持 Windows、开发复杂、需编译 | ❌ |
| Electron | 跨平台桌面应用 | 打包体积大（100MB+）、开发复杂 | ❌ |

**跨平台支持**：
- ✅ Windows（pystray + 注册表自启）
- ✅ macOS（pystray + Launch Agents；可选备选：[rumps](https://github.com/jaraco/rumps)）
- ⚠️ Linux（pystray + XDG autostart，可选支持）

**macOS 后端备注**：
- pystray 在 macOS 依赖 pyobjc（pip 按平台标记自动安装）；PyInstaller 打包时需验证 hidden imports 是否被收全
- pystray 的 `icon.run()` 在 macOS 必须在**主线程**执行；所有回调里不要做耗时操作，子进程/IO 放线程
- tkinter 在 macOS 也要主线程，**与 pystray 冲突**（见"注意事项"），配置向导必须放到子进程跑

### 关键事实：dsh web 的 supervisor 契约（源码核实）

> 本节结论来自对 `C:\deepseek-harness` 源码的核实，是进程管理设计的依据。

| 事实 | 源码位置 | 对托盘设计的意义 |
|------|---------|----------------|
| **SIGTERM = 官方约定的 supervisor 优雅停止信号，exit 0** | `apps/cli/src/profile-boot.ts:218-222`："SIGTERM is a supervisor's ordinary stop request and exits 0 on every surface" | 退出 dsh web 必须先发 SIGTERM（POSIX），它会自行 `fiber.dispose()` 清理整棵进程树（含 agent 子进程）。**直接 kill 是错的** |
| **stdout 的 URL 行是官方就绪信号** | `packages/bundle/web-app/src/index.ts:159-168`："The URL line is a readiness signal: supervisors ... RPC as soon as they observe it" | 就绪检测应解析 `dsh web: http://...` 行，而不是轮询端口。该行打印时所有 API 路由已挂载完毕 |
| **`--port 0` 支持系统自动分配端口** | `apps/cli/src/startup.ts:49` | 托盘可不预设端口，从 URL 行解析实际地址（并存入配置供"打开浏览器"使用） |
| **仅绑定 loopback，`0.0.0.0` 被刻意禁止** | `web-app/src/startup.ts:70`、`index.ts:71` | 托盘的端口配置不应试图暴露 LAN；`打开浏览器` 用解析出的 URL |
| **源码安装必须先构建前端** | `web-app/src/index.ts:122`：未构建时抛 `frontend dist not built; run pnpm run build` | 安装检测需包含"dist 是否已构建"检查，未构建时引导 `pnpm install && pnpm run build` |
| **Windows 上 pnpm 是 .cmd shim，直接 spawn 会失败** | `apps/cli/src/plugin.ts:127-133`，harness 自己也要 `shell: true` 兜底 | Python 同理：必须 `shutil.which()` 解析全路径，否则 `Popen(["pnpm", ...])` 直接 `FileNotFoundError` |

### 进程生命周期设计（统一，跨平台核心）

这是本计划的核心章节。四个阶段：

```
启动 ──→ 就绪 ──→ 运行监控 ──→ 退出
 │         │          │           │
 PATH解析  解析stdout  崩溃检测    SIGTERM→树杀兜底
 spawn     URL行      状态菜单    (Windows: taskkill /T)
 记录PID
```

#### 阶段 1：启动（PATH 解析 + spawn + 记录 PID）

**坑 1（P0）：两个平台都存在"找不到可执行文件"问题**

- **macOS**：从 Finder / LaunchAgent 启动的 GUI 应用，PATH 只有 `/usr/bin:/bin:/usr/sbin:/sbin`。Homebrew/nvm/volta 装的 node/pnpm 全不在路径上。手动终端测试正常、双击 .app 失败——极难排查。
- **Windows**：`pnpm` 是 `pnpm.cmd`，`CreateProcess` 不解析 `.cmd` shim。

**解法：配置阶段解析绝对路径 + 登录 shell 兜底**

```python
import os
import shlex
import shutil
import subprocess
import sys

def resolve_command(argv):
    """把 argv[0] 解析为绝对路径的完整 argv。
    返回 None 表示找不到命令（触发安装引导流程）。
    """
    exe = shutil.which(argv[0])
    if exe:
        return [exe, *argv[1:]]
    # macOS GUI 环境 PATH 不含用户工具 → 用登录 shell 拿真实 PATH 兜底
    if sys.platform != "win32":
        shell = os.environ.get("SHELL", "/bin/zsh")
        try:
            result = subprocess.run(
                [shell, "-l", "-c", f"command -v {shlex.quote(argv[0])}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return [result.stdout.strip(), *argv[1:]]
        except (subprocess.SubprocessError, OSError):
            pass
    return None
```

> 解析结果（绝对路径 argv）**持久化到配置文件**，之后每次启动直接使用，不再依赖运行时 PATH。

**spawn：平台参数表**

```python
import re
import threading

READY_RE = re.compile(r"dsh web: (http://\S+)")

class DshProcess:
    """dsh web 子进程的完整生命周期管理。"""

    def __init__(self, argv, cwd, log_path):
        self.argv, self.cwd, self.log_path = argv, cwd, log_path
        self.proc = None
        self.url = None          # 就绪后从 URL 行解析
        self._lock = threading.Lock()

    def start(self):
        kwargs = dict(
            cwd=self.cwd,
            stdout=subprocess.PIPE,   # 必须读，否则管道写满会阻塞 dsh
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if sys.platform == "win32":
            # 无终端窗口（进程仍有不可见的控制台）
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            # 独立进程组：脱离控制终端，托盘退出不受终端关闭影响
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.argv, **kwargs)
        threading.Thread(target=self._drain, daemon=True).start()
        return self.proc

    def _drain(self):
        """持续排空 stdout：既解析就绪行，也写日志。
        坑：PIPE 后不读，子进程 console.log 写满缓冲区后会永久阻塞。
        """
        with open(self.log_path, "a", encoding="utf-8") as log:
            for line in self.proc.stdout:
                log.write(line)
                if self.url is None:
                    m = READY_RE.search(line)
                    if m:
                        self.url = m.group(1)
```

#### 阶段 2：就绪（解析 stdout URL 行，不轮询端口）

```python
def wait_ready(self, timeout=120):
    """阻塞等待 URL 行出现。首次启动含 profile bootstrap，可能远超 5 秒，
    故默认 120 秒；端口轮询既慢又不可靠（端口通 ≠ API 就绪）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if self.url:
            return self.url
        if self.proc.poll() is not None:
            raise RuntimeError(f"dsh web 启动即退出（code {self.proc.returncode}），查看日志 {self.log_path}")
        time.sleep(0.2)
    raise TimeoutError(f"dsh web {timeout}s 内未就绪")
```

**附带红利**：支持 `--port 0`（系统自动分配端口），URL 从解析结果拿；LAN 地址也在同一行输出。

#### 阶段 3：运行监控（崩溃感知）

```python
def watch(self, on_state_change):
    """后台线程：轮询子进程存活状态，意外退出时回调更新托盘。"""
    def loop():
        while True:
            code = self.proc.wait()      # 阻塞至退出
            on_state_change("crashed" if self.url else "start-failed", code)
            return
    threading.Thread(target=loop, daemon=True).start()
```

托盘菜单需呈现状态：`正在启动… / 运行中 (http://127.0.0.1:PORT) / 已停止 / 意外退出 [重新启动]`。

#### 阶段 4：退出（SIGTERM 优先，树杀兜底）

```python
def stop(self, timeout=10):
    """优雅停止：POSIX 发 SIGTERM（官方契约，exit 0，自行清理整棵树）；
    超时或 Windows 才升级为树杀。"""
    if self.proc is None or self.proc.poll() is not None:
        return
    try:
        self.proc.terminate()   # POSIX: SIGTERM（优雅）  Windows: TerminateProcess（硬杀）
        self.proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    # 兜底：杀整棵进程树（dsh web 运行期会派生 agent/后台任务子进程）
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        import psutil
        try:
            parent = psutil.Process(self.proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            psutil.wait_procs(children, timeout=5)
            parent.kill()
        except psutil.NoSuchProcess:
            pass
```

**Windows 的诚实说明**：Windows 无法向无控制台的进程投递 SIGTERM（CTRL 事件要求共享控制台），`terminate()`/`taskkill /F` 都是硬杀。缓解因素：harness 的会话存储带 torn-tail 截断容错，硬杀不会损坏数据，但可能留下孤儿进程，故 Windows 用 `taskkill /T` 树杀。

#### 为什么不用 cmdline 全局扫描做常规退出

计划早期版本的"遍历进程列表做子串匹配"有三个缺陷，**仅保留为'接管遗孤进程'场景的工具**：
- **误杀**：任何 cmdline 恰含 "dsh web" 的进程（如开着本文档的编辑器）都可能中招
- **漏杀**：真实链路 `pnpm.cmd → cmd.exe → node dsh.js web → agent 子进程`，node 的 cmdline 里没有 "pnpm"，匹配不到
- **树残留**：匹配第一个就 `break`，同树其余进程存活

常规路径只依赖自己 spawn 时记录的 PID + `psutil` children 递归。

## 功能清单

### Phase 1: 基础功能（MVP，含 macOS）

- [ ] `resolve_command`：PATH 解析 + 登录 shell 兜底（**P0，双平台**）
- [ ] 启动 `dsh web` 隐藏终端（Windows `CREATE_NO_WINDOW` / POSIX `start_new_session`）
- [ ] stdout 排空线程 + URL 行就绪检测（**P0**）
- [ ] 系统托盘图标 + 状态显示（启动中/运行中/已停止/意外退出）
- [ ] 右键菜单：打开浏览器（用解析出的 URL）、退出
- [ ] 退出：SIGTERM → 超时树杀兜底（**P0**）
- [ ] 崩溃监控 + 重新启动菜单项
- [ ] **端口/实例冲突检测**：启动前查端口占用，已运行则显示"运行中（外部启动）"（从"后续扩展"提前至此：这是第一天就会遇到的场景）

### Phase 2: 配置管理（含 macOS）

- [ ] 首次运行配置向导（**macOS 上向导跑在子进程**，规避 tkinter/pystray 主线程冲突）
- [ ] 自动检测 dsh 安装方式（`where`/`which` 按平台选择）
- [ ] **源码安装的构建前置检测**：dist 未构建时引导 `pnpm install && pnpm run build`
- [ ] **未安装时的安装指引**（三种方式 + 复制命令 + 官方文档链接）
- [ ] 安装验证和重试检测
- [ ] 配置持久化（`~/.dsh-web-tray/config.json`，**命令存 argv 数组**）
- [ ] 端口配置（支持 `0` = 自动分配）
- [ ] 测试连接功能（复用 `DshProcess.wait_ready`，不轮询端口）
- [ ] 手动配置窗口（高级用户）

### Phase 3: 增强功能（含 macOS）

- [ ] 开机自启切换（Windows 注册表 / macOS LaunchAgent，均需 **PyInstaller frozen 检测**）
- [ ] 重新配置菜单
- [ ] 防止重复启动（锁文件 + PID 存活检查）
- [ ] 日志输出（stdout 已在排空线程落盘）

### Phase 4: 打包分发（按平台）

- [ ] Windows：pyinstaller 打包 .exe（`--noconsole`）
- [ ] macOS：pyinstaller 打包 .app（`--windowed` + `LSUIElement`）+ **ad-hoc 签名** + DMG
- [ ] CI 双架构构建（macOS arm64 / x86_64 分开出包）
- [ ] 一键安装依赖脚本（macOS 版需 `brew install python-tk`）
- [ ] 自定义图标（.ico / .icns）

## dsh 安装检测逻辑

### 支持的安装方式

| 安装方式 | 检测方法 | 启动命令 |
|---------|---------|---------|
| 源码安装 | 向上查找 `pnpm-workspace.yaml` 或含 `dsh` 脚本的 `package.json` | `pnpm dsh web`（**需先确认前端已构建**） |
| npm 全局 | `npm list -g @deepseek-ai/dsh` + `where dsh`（Win）/ `which dsh`（macOS/Linux） | `dsh web` |
| npm 本地 | 检查 `node_modules/@deepseek-ai/dsh` | `npx @deepseek-ai/dsh web` |

### 检测优先级

1. 源码安装（当前目录及其父目录）→ 附加 dist 构建检查
2. npm 全局安装
3. npm 本地安装

**统一原则**：检测通过后立刻 `resolve_command()` 解析出绝对路径 argv 存入配置——运行期（尤其 macOS GUI 环境）不再依赖 PATH。

### 配置结构

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

> **坑（P1）**：命令必须存 **argv 数组**。存字符串再用 `.split()` 解析，遇到 `C:\Program Files\...` 这类带空格路径即碎。`dshArgvDisplay` 仅供 UI 展示。

## 实现步骤

### Step 1: 环境准备

```powershell
# Windows
pip install pystray psutil pillow
```

```bash
# macOS（Homebrew Python 需额外装 tk，否则 tkinter 不可用）
brew install python-tk
pip3 install pystray psutil pillow
```

### Step 2: 进程生命周期核心

- `resolve_command`（PATH 解析，双平台）
- `DshProcess` 类：start / _drain / wait_ready / watch / stop
- 单元测试覆盖：URL 行解析、SIGTERM 退出码 0、树杀兜底

### Step 3: 托盘与状态

- 平台模块 `platforms/`（**不可叫 `platform/`，会遮蔽 Python 标准库**）
- 托盘图标 + 状态菜单 + 崩溃回调

### Step 4: 配置向导

- tkinter 弹窗（**macOS 上以子进程运行**）
- dsh 安装检测（含构建前置检查）
- 配置保存/加载（argv 数组）

### Step 5: 开机自启

- Windows 注册表 / macOS LaunchAgent（`launchctl bootstrap`，非废弃的 `load`）
- 自启命令统一走 `get_self_invocation()`（frozen 检测）
- 菜单动态更新

### Step 6: 安装指引与帮助功能

- 未安装检测弹窗设计
- 安装指引界面（三种安装方式）
- 复制命令功能
- 打开浏览器访问官方文档
- 重试检测逻辑
- 手动配置窗口
- 测试连接功能（复用 `wait_ready`）

### Step 7: 测试与优化

- 进程生命周期全链路（启动→就绪→崩溃→重启→退出）
- 托盘菜单响应
- 异常处理

### Step 8: 打包分发

- 按平台打包（见"打包命令"）
- 依赖安装脚本
- 使用文档

## 注意事项

### 进程管理

- **信号契约优先**：POSIX 上 SIGTERM 是 dsh 官方约定的 supervisor 停止信号（exit 0，自行清理进程树）；`proc.kill()`（SIGKILL）会跳过全部清理，仅作超时兜底
- **常规退出只信自己记录的 PID**：cmdline 子串扫描仅用于"接管遗孤"场景（防误杀/漏杀/树残留，见生命周期设计）
- **stdout 必须持续排空**：PIPE 后不读，子进程写满缓冲区即阻塞
- **防止重复启动**：锁文件（含 PID）+ 存活检查；启动前查端口占用，区分"自己启动/外部启动"

### 环境与 PATH

- **macOS GUI 环境 PATH 只有系统路径**：双击 .app、LaunchAgent 场景下 node/pnpm 不可见。所有可执行文件路径在配置阶段解析为绝对路径并持久化（P0）
- **Windows pnpm 是 .cmd shim**：`Popen(["pnpm", ...])` 直接 `FileNotFoundError`，必须 `shutil.which()` 全路径（harness 源码 `plugin.ts:127` 有同样的坑与兜底）

### 配置向导

- **tkinter 与 pystray 的主线程冲突（macOS）**：两者都要求主线程；从托盘回调弹 tkinter 窗口会挂起/崩溃。**向导必须 `subprocess.Popen([sys.executable, wizard.py])` 子进程运行**，结果经配置文件回传。备选：macOS 用 rumps 重写托盘可共存
- **Homebrew Python 缺 tk**：`brew install python-tk`，依赖脚本需覆盖
- **自动检测 vs 手动选择**：检测到多个安装方式 → 让用户选择；单个 → 默认选中
- **端口验证**：0-65535（0 = 系统自动分配，从 URL 行回读实际端口）
- **源码安装的构建前置**：检测 dist 未构建时引导 `pnpm install && pnpm run build`，否则启动必失败（`frontend dist not built`）

### 开机自启

- **Windows**：注册表 `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`
- **macOS**：`~/Library/LaunchAgents/com.dsh.webtray.plist` + `launchctl bootstrap gui/$(id -u)`（`load/unload` 已废弃）
- **PyInstaller 冻结检测（P1）**：打包后 `Path(__file__)` 指向临时解压目录（onefile，运行后删除）、脚本体不复存在。自启命令必须统一走：

```python
def get_self_invocation():
    """开机自启应执行的 argv。打包后 __file__ 无效，必须用 sys.executable。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]
```

- 用户级自启（非系统级），避免权限问题

### 跨平台代码规范

- 所有 `subprocess.CREATE_NO_WINDOW` / `creationflags` 引用必须包在 `sys.platform == "win32"` 守卫里（该常量在 macOS/Linux 上 **不存在**，直接 `AttributeError`）
- 模块目录命名 **`platforms/`**，禁用 `platform/`（遮蔽标准库，psutil/PIL 内部 import 会炸）
- 外部命令调用一律 `subprocess.run([...])` 列表参数；禁止 `os.system(f"... {path}")`（路径含空格即碎，且无法捕获错误）
- pystray `icon.run()` 必须主线程（macOS）；回调内不做耗时操作

### 异常处理

- tkinter 未安装 → 提示手动编辑配置文件
- **未检测到 dsh 安装 → 弹窗提供完整安装指引**（见下方详细流程）
- 进程启动失败 → 日志记录（排空线程已落盘）
- **macOS 无需特殊系统权限**：菜单栏图标 + 启动子进程不需要 Accessibility/完全磁盘访问，不要在 UI 里引导用户去开权限（早期版本的"辅助功能权限"章节系误导，已删除）

#### 未安装 dsh web 的引导流程

**触发条件**：首次运行时，检测不到任何 dsh 安装方式。

**引导界面**：

```
╔════════════════════════════════════════════════════╗
║         未检测到 DSH Web GUI 安装                 ║
╠════════════════════════════════════════════════════╣
║                                                    ║
║  请选择安装方式：                                  ║
║                                                    ║
║  ① 全局安装（推荐）- 所有用户可用                  ║
║     打开命令行，执行：                              ║
║     npm install -g @deepseek-ai/dsh               ║
║                                                    ║
║  ② 源码安装（开发者）- 可修改源码                   ║
║     打开命令行，执行：                              ║
║     git clone https://github.com/deepseek-ai/harness.git ║
║     cd harness && pnpm install && pnpm run build  ║
║                                                    ║
║  ③ 手动配置 - 已安装但未检测到                      ║
║                                                    ║
║  [复制安装命令]  [访问官方文档]  [重试检测]        ║
║                                                    ║
╚════════════════════════════════════════════════════╝
```

**按钮功能**：

| 按钮 | 功能 |
|------|------|
| 复制安装命令 | 将选中的安装命令复制到剪贴板 |
| 访问官方文档 | 打开浏览器，跳转到 DSH GitHub/文档 |
| 重试检测 | 重新执行检测流程 |
| 手动配置 | 打开高级配置窗口，手动输入路径和命令 |

**安装完成后的验证**：

```python
def verify_dsh_installation(dsh_type, dsh_dir):
    """验证用户是否正确安装了 dsh"""
    try:
        if dsh_type == "global":
            result = subprocess.run(["dsh", "--version"], capture_output=True, text=True)
            return result.returncode == 0
        elif dsh_type == "pnpm":
            pkg_json = Path(dsh_dir) / "package.json"
            if not pkg_json.exists():
                return False
            with open(pkg_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "dsh" not in data.get("scripts", {}):
                    return False
            # 构建前置检查：源码安装必须已构建前端 dist
            # （否则 dsh web 启动即抛 "frontend dist not built"）
            dist_marker = Path(dsh_dir) / "apps" / "web" / "dist"
            return dist_marker.exists()
        return False
    except Exception:
        return False
```

**用户点击"重试检测"后**：

```
检测结果：
  ✅ 检测到全局安装（dsh 命令可用）
  
  [使用此配置]  [重新选择]  [取消]
```

**高级配置窗口（手动配置）**：

```
╔════════════════════════════════════════════════════╗
║              手动配置 DSH Web GUI                  ║
╠════════════════════════════════════════════════════╣
║                                                    ║
║  安装类型：                                         ║
║  ○ 源码安装 (pnpm)  ○ 全局安装  ○ 本地安装 (npm)   ║
║                                                    ║
║  DSH 工作目录：                                     ║
║  [ C:\deepseek-harness               ] [浏览...] ║
║                                                    ║
║  启动命令：                                         ║
║  [ pnpm dsh web                       ]           ║
║                                                    ║
║  端口号（0 = 自动分配）：                           ║
║  [ 3080                                ]           ║
║                                                    ║
║  [测试连接]  [保存]  [取消]                        ║
╚════════════════════════════════════════════════════╝
```

**测试连接功能**：

```python
def test_connection(dsh_argv, dsh_dir, log_path):
    """测试配置是否可用：复用 DshProcess（spawn → 解析 URL 行 → 停止）。
    不轮询端口：端口通 ≠ API 就绪，且首次启动可能远超固定等待时间。"""
    dsh = DshProcess(dsh_argv, dsh_dir, log_path)
    try:
        dsh.start()
        url = dsh.wait_ready(timeout=120)   # 官方就绪信号
        return True, f"✅ 配置成功！{url}"
    except (RuntimeError, TimeoutError) as e:
        return False, f"❌ 启动失败：{e}（日志：{log_path}）"
    finally:
        dsh.stop()
```

## 托盘菜单设计

```
状态行（动态）
├── ● 运行中 (http://127.0.0.1:3080)     ← 从 URL 行解析，非拼凑
├── ───────────
├── 打开浏览器
├── 重新启动            ← 崩溃/停止时可用
├── 停止
├── 开机自启 (已启用/未启用)
├── 重新配置
├── ───────────
├── 帮助 / 安装指引
│   ├── 如何安装 DSH
│   ├── 访问官方文档
│   └── 打开日志目录
└── 退出                 ← SIGTERM 优先，见生命周期设计
```

## 打包命令

### Windows

```powershell
# 安装 pyinstaller
pip install pyinstaller

# 打包为单文件 exe，无控制台窗口
pyinstaller --onefile --noconsole --name=dsh-web-tray dsh-web-tray.py
```

输出：`dist/dsh-web-tray.exe`（约 30-50MB，包含 Python 运行时）

### macOS

```bash
# 安装 pyinstaller
pip3 install pyinstaller

# 打包为 app
pyinstaller \
    --name="DSH Web Tray" \
    --windowed \
    --onefile \
    --icon=resources/icon.icns \
    --osx-bundle-identifier=com.dsh.webtray \
    dsh-web-tray.py

# 必做：ad-hoc 签名（未签名 .app 会被 Gatekeeper 判为"已损坏"）
codesign --force --deep --sign - "dist/DSH Web Tray.app"

# 创建 DMG 镜像
hdiutil create -volname "DSH Web Tray" \
    -srcfolder "dist/DSH Web Tray.app" \
    -ov -format UDZO dsh-web-tray.dmg
```

输出：`dist/DSH Web Tray.app`（约 40-60MB）

**分发坑（macOS）**：
- **Gatekeeper**：即使 ad-hoc 签名，首次打开仍需用户右键→打开，或 `xattr -cr "DSH Web Tray.app"`。正式分发需 Apple Developer ID 签名 + 公证（notarization），否则用户体验很差
- **架构**：PyInstaller 只产出构建主机架构的包；arm64 与 x86_64（Intel Mac）需分别在对应架构/CI runner 上构建
- **PyInstaller + pyobjc**：验证 hidden imports 是否收全（打完包必须实测托盘图标出现）

### 跨平台自动化打包脚本

**scripts/build.sh**：

```bash
#!/bin/bash
set -euo pipefail

echo "Building DSH Web Tray..."

pip install pyinstaller pystray psutil pillow

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Building for macOS ($(uname -m))..."
    pyinstaller \
        --name="DSH Web Tray" \
        --windowed \
        --onefile \
        --icon=resources/icon.icns \
        --osx-bundle-identifier=com.dsh.webtray \
        --add-data="resources:resources" \
        dsh-web-tray.py

    codesign --force --deep --sign - "dist/DSH Web Tray.app"

    hdiutil create -volname "DSH Web Tray" \
        -srcfolder "dist/DSH Web Tray.app" \
        -ov -format UDZO "dsh-web-tray-$(uname -m).dmg"
    echo "Build complete: dsh-web-tray-$(uname -m).dmg"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo "Building for Windows..."
    pyinstaller \
        --onefile \
        --noconsole \
        --name=dsh-web-tray \
        --icon=resources/icon.ico \
        --add-data="resources;resources" \
        dsh-web-tray.py
    echo "Build complete: dist/dsh-web-tray.exe"
else
    echo "Unsupported platform: $OSTYPE"
    exit 1
fi
```

## 文件结构

```
dsh-web-tray/
├── dsh-web-tray.py          # 主脚本（跨平台，托盘 + 生命周期编排）
├── wizard.py                # 配置向导（tkinter；macOS 上由主进程以子进程拉起）
├── platforms/               # ⚠️ 不能叫 platform/ —— 会遮蔽 Python 标准库 platform 模块
│   ├── __init__.py
│   ├── windows.py           # Windows 特定实现（注册表、CREATE_NO_WINDOW）
│   ├── macos.py             # macOS 特定实现（LaunchAgent、icns）
│   └── linux.py             # Linux 特定实现（可选，XDG autostart）
├── resources/
│   ├── icon.ico             # Windows 图标
│   ├── icon.icns            # macOS 图标
│   └── icon.png             # 源图标（生成 icns 用）
├── scripts/
│   ├── install-dependencies.ps1  # Windows 依赖安装
│   ├── install-dependencies.sh   # macOS/Linux 依赖安装（含 brew install python-tk）
│   ├── build-exe.ps1             # Windows 打包
│   ├── build-app.sh              # macOS 打包（含 codesign + DMG）
│   └── build.sh                  # 跨平台打包入口
├── dist/                      # 打包输出（gitignore）
├── README.md                 # 通用文档
├── README-Windows.md         # Windows 专用文档
└── README-macOS.md           # macOS 专用文档
```

**跨平台模块加载**：

```python
# dsh-web-tray.py
import sys

# 动态加载平台特定模块（目录名必须是 platforms，不能是 platform）
if sys.platform == "win32":
    from platforms import windows as platform_impl
elif sys.platform == "darwin":
    from platforms import macos as platform_impl
else:
    from platforms import linux as platform_impl

# 使用统一接口
def set_autostart(enable):
    return platform_impl.set_autostart(enable)
```

## macOS 跨平台适配（v1.5）

### 1. Launch Agents 开机自启

**文件位置**：`~/Library/LaunchAgents/com.dsh.webtray.plist`

**Python 实现**（修正点：`bootstrap` 替代废弃的 `load`；列表参数替代 `os.system`；frozen 检测；**PATH 陷阱见生命周期设计——.app 由 launchd 拉起时 PATH 只有系统路径，运行期靠配置里的绝对路径 argv**）：

```python
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.dsh.webtray"

def _plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

def set_autostart_macos(enable):
    plist_path = _plist_path()
    gui = f"gui/{os.getuid()}"

    if enable:
        # 先清掉旧注册（幂等）
        subprocess.run(["launchctl", "bootout", gui, str(plist_path)],
                       capture_output=True)

        plist_content = {
            "Label": LABEL,
            "ProgramArguments": get_self_invocation(),   # frozen 检测，见"注意事项"
            "RunAtLoad": True,
            "KeepAlive": False,
            # 日志放用户日志目录
            "StandardOutPath": str(Path.home() / "Library" / "Logs" / f"{LABEL}.log"),
            "StandardErrorPath": str(Path.home() / "Library" / "Logs" / f"{LABEL}.err"),
        }

        plist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "wb") as f:
            plistlib.dump(plist_content, f)

        # 现代写法：bootstrap（load/unload 已废弃）
        result = subprocess.run(["launchctl", "bootstrap", gui, str(plist_path)],
                                capture_output=True, text=True)
        return result.returncode == 0
    else:
        subprocess.run(["launchctl", "bootout", gui, str(plist_path)],
                       capture_output=True)
        plist_path.unlink(missing_ok=True)
        return True
```

### 2. .app 打包与 Info.plist

PyInstaller `--windowed` 自动生成 .app。若手工组装，`Info.plist` 关键键：

```xml
<key>LSUIElement</key><true/>          <!-- 不出现在 Dock：纯菜单栏应用 -->
<key>NSHighResolutionCapable</key><true/> <!-- Retina -->
```

**分发必读**：见"打包命令 → 分发坑（macOS）"——ad-hoc 签名、Gatekeeper、双架构。

### 3. macOS 图标（icns）

```bash
# iconutil（macOS 自带）。iconset 需含 16/32/128/256/512 各尺寸 + @2x 变体
mkdir icon.iconset
# ... 放入各尺寸 PNG ...
iconutil -c icns icon.iconset
```

菜单栏图标（非 .app 图标）推荐 22×22（Retina 44×44），可用模板图像自动适配深浅色主题。

### 4. macOS 进程管理

**无需任何特殊系统权限**：菜单栏图标、启动/停止子进程、LaunchAgent 都不涉及 Accessibility/完全磁盘访问。早期计划中"辅助功能权限"章节系误判，已删除——不要在 UI 中引导用户去开权限。

进程生命周期（启动/就绪/监控/退出）完全复用统一设计，无 macOS 分支；唯一 macOS 特有项是 PATH 解析的登录 shell 兜底（见阶段 1）。

### 5. macOS 测试清单

**环境矩阵**（每个场景 PATH 都不同，专门验证 PATH 解析）：
- [ ] 终端运行 `python3 dsh-web-tray.py`（PATH 完整）
- [ ] 双击 .app 启动（PATH 仅系统路径 → 验证配置内绝对路径 argv 生效）
- [ ] LaunchAgent 自启（同上 + 验证 `launchctl bootstrap`）
- [ ] Homebrew Python + `python-tk`（向导子进程可用）

**功能清单**：
- [ ] 托盘图标正确显示（含 Retina、深浅色主题）
- [ ] 状态菜单（运行中 URL / 崩溃 / 重启）
- [ ] SIGTERM 优雅退出，exit 0，无孤儿进程（`pgrep -fl dsh` 验证）
- [ ] 超时树杀兜底
- [ ] stdout 排空：长时间运行 dsh 不因管道满而阻塞
- [ ] 日志写入 `~/Library/Logs/`
- [ ] 打包后自启路径有效（frozen 检测）
- [ ] ad-hoc 签名后 .app 可启动
- [ ] tkinter 向导从托盘菜单拉起不挂起（子进程方案）

### 6. macOS 安装方式

**方式 1：DMG**

```bash
open "DSH Web Tray.app"
# 或拖入 /Applications/；首次打开可能需右键→打开（未公证）
```

**方式 2：Homebrew Cask（可选，正式分发时）**

```ruby
cask "dsh-web-tray" do
  version "1.5.0"
  sha256 "xxx"

  url "https://github.com/yourorg/dsh-web-tray/releases/download/v#{version}/DSH-Web-Tray-arm64-#{version}.dmg"
  name "DSH Web Tray"
  desc "System tray supervisor for DSH Web GUI"
  homepage "https://github.com/yourorg/dsh-web-tray"

  app "DSH Web Tray.app"

  uninstall delete: [
    "~/Library/LaunchAgents/com.dsh.webtray.plist",
    "~/Library/Logs/com.dsh.webtray.log"
  ]
end
```

### 7. CI 自动构建（双架构）

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags: ['v*']

jobs:
  release-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install pyinstaller pystray psutil pillow
      - run: pwsh scripts/build-exe.ps1
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/dsh-web-tray.exe

  release-macos:
    strategy:
      matrix:
        runner: [macos-14, macos-13]   # arm64 + x86_64 各出一份
    runs-on: ${{ matrix.runner }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: |
          brew install python-tk
          pip3 install pyinstaller pystray psutil pillow
      - run: bash scripts/build-app.sh   # 内含 codesign + DMG
      - uses: softprops/action-gh-release@v2
        with:
          files: dsh-web-tray-*.dmg
```

> 正式分发还需在 CI 中接入 Developer ID 签名 + `notarytool` 公证；个人/内部使用 ad-hoc 签名即可（用户首次打开需右键→打开）。

### 跨平台兼容性测试矩阵

| 功能 | Windows 10/11 | macOS 12+ | Linux (Ubuntu) |
|-----|---------------|-----------|----------------|
| PATH 解析（GUI 环境） | ✅ .cmd shim 解析 | ✅ 登录 shell 兜底 | ⚠️ |
| 托盘图标 | ✅ | ✅ | ⚠️ |
| 状态菜单 + 崩溃重启 | ✅ | ✅ | ⚠️ |
| URL 行就绪检测 | ✅ | ✅ | ✅ |
| 优雅退出（SIGTERM） | ⚠️ 树杀兜底 | ✅ | ✅ |
| 开机自启 | ✅ 注册表 | ✅ LaunchAgent | ⚠️ |
| 打包分发 | ✅ .exe | ✅ .app/.dmg | ⚠️ |

**注**：⚠️ 表示可选支持，可后续添加

## 后续扩展

- [ ] 自定义托盘图标
- [ ] 多语言支持
- [ ] 日志查看功能（当前为落盘文件）
- [ ] 接管遗孤进程（cmdline 扫描场景的唯一保留用途）
- [ ] 多实例支持（同时运行多个 dsh web，`--port 0` 已为前置）

## 参考资料

### 通用
- [pystray 文档](https://pystray.readthedocs.io/)
- [psutil 文档](https://psutil.readthedocs.io/)
- [pyinstaller 文档](https://pyinstaller.org/)
- [rumps（macOS 托盘备选）](https://github.com/jaraco/rumps)
- [DSH GitHub 仓库](https://github.com/deepseek-ai/harness)

### Windows
- [Windows 注册表自启](https://docs.microsoft.com/en-us/windows/win32/setup/run-runoncekeys)
- [subprocess.STARTF_USESHOWWINDOW](https://docs.python.org/3/library/subprocess.html#subprocess.STARTF_USESHOWWINDOW)
- [CVE-2024-27980（.cmd shim 与 spawn 加固背景）](https://nodejs.org/en/blog/vulnerability/april-2024-security-releases-2)

### macOS
- [Launch Agents 官方文档](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
- [launchd.plist(5) 手册（含 bootstrap/bootout 语义）](https://www.manpagez.com/man/5/launchd.plist/)
- [macOS 图标设计指南](https://developer.apple.com/design/human-interface-guidelines/app-icons)
- [iconutil 使用方法](https://ss64.com/osx/iconutil.html)
- [LSUIElement 说明](https://developer.apple.com/documentation/bundleresources/information_property_list/lsuielement)
- [公证 Notarizing macOS 软件](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

### Linux（可选）
- [XDG Autostart 规范](https://specifications.freedesktop.org/autostart-spec/autostart-spec-latest.html)
- [Systemd User Services](https://www.freedesktop.org/software/systemd/man/systemd.user.html)

## 版本历史

- v1.0 - MVP：基本托盘功能 + 进程管理（Windows）
- v1.1 - 配置向导 + 自动检测（Windows）
- v1.2 - 开机自启 + 重新配置（Windows）
- v1.3 - **安装指引 + 测试连接 + 帮助菜单**（Windows）
- v1.4 - 打包分发 + 依赖安装脚本（Windows）
- v1.5 - **macOS 跨平台适配 + 全面评审修订**：
  - 新增：macOS LaunchAgent / .app 打包 / icns / DMG / 双架构 CI / Gatekeeper 签名说明
  - **重设计**：统一进程生命周期（PATH 解析 → URL 行就绪 → 崩溃监控 → SIGTERM 优雅退出 + 树杀兜底），依据 dsh 源码核实的 supervisor 契约
  - 修正：`platform/` 目录遮蔽标准库 → 改名 `platforms/`；跨平台代码中的 `CREATE_NO_WINDOW` 加平台守卫；命令存储改 argv 数组（防路径空格碎裂）；开机自启加 PyInstaller frozen 检测；`launchctl load` 废弃 API → `bootstrap/bootout`；`os.system` 拼接命令 → 列表参数；删除误导性的"macOS 辅助功能权限"章节；tkinter/pystray 主线程冲突改为向导子进程方案；注册表路径笔误；重复的 Step 6 编号；macOS 任务并入 Phase 1-4 清单

---

**文档创建日期**：2025-01-XX
**最后更新**：2025-01-XX（评审修订版）
**macOS 适配版本**：v1.5
