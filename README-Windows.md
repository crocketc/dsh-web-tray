# DSH Web Tray — Windows 使用指南

## 安装

### 方式一：下载 exe（推荐）

1. 打开 [Releases 页面](https://github.com/crocketc/dsh-web-tray/releases/latest)，下载 `dsh-web-tray.exe`
2. 放到一个**固定位置**（建议 `%LocalAppData%\Programs\dsh-web-tray\`，因为开机自启要记住它的路径）
3. 双击运行

首次运行会弹 SmartScreen 蓝色警告（"Windows 已保护你的电脑"）——这是因为 exe 没有购买代码签名证书，不是真的有问题。点 **"更多信息" → "仍要运行"** 即可。

### 方式二：从源码跑

需要 [Python 3.9+](https://www.python.org/downloads/)（安装时勾选 **Add to PATH**）：

```powershell
pip install -r requirements.txt
# 国内网络可用镜像：
# powershell -ExecutionPolicy Bypass -File scripts\install-dependencies.ps1 -Index https://pypi.tuna.tsinghua.edu.cn/simple
```

**日常启动：双击 `start-tray.cmd`**（同目录下）——它用 `pythonw` 后台启动，无窗口、随手关终端都不影响。

> ⚠️ 注意区别：在终端里敲 `python dsh-web-tray.py` 启动的托盘**附着在那个终端上**，关掉终端窗口托盘就退出了（这是 Windows 控制台的固有行为）。日常使用一律走 `start-tray.cmd`；`python` 方式留作调试（能直接看输出）。

## 首次运行

弹出配置向导：

1. 自动检测 dsh 安装（npm 全局 `dsh web` / 源码 `pnpm dsh web` / 本地 `npx`，多份共存时让你选）
2. 选端口（默认 3080；填 0 = 系统自动分配）
3. 点"保存并启动"→ 托盘出现图标（可能在托盘折叠区 `^` 里，建议拖出来固定）

## 日常使用

| 操作 | 怎么做 |
|------|--------|
| 打开 Web 界面 | **双击托盘图标**（自动带正确端口），或右键 → 打开浏览器 |
| 看当前状态 | 图标颜色：🟢绿=运行中 / 🩵青=外部启动 / 🔴红=挂了 / ⚪灰=已停止 / 🔵蓝=启动中 |
| 重启 dsh | 右键 → 重新启动 |
| 停止 dsh | 右键 → 停止 |
| 彻底退出 | 右键 → 退出（会先优雅停掉自己启动的 dsh，再消失） |

**一个重要语义**：托盘只管理**自己启动的** dsh。如果 dsh 是你在终端里手动跑的（图标显示青色"外部启动"），退出托盘**不会**碰它——防止误杀你正在用的会话。想让托盘全权管理，先停掉终端里那份，再右键 → 重新启动。

## 开机自启

右键托盘 → **开机自启**，勾上即可。无需管理员权限。

- 原理：写当前用户注册表 `HKCU\...\Run`（exe 用户注册 exe 路径，源码用户注册 pythonw + 脚本路径）
- 想取消：再点一次菜单项
- 注意：用 exe 的话，**先把它挪到固定路径再开自启**，之后别移动文件

## 排障速查

| 症状 | 处理 |
|------|------|
| 双击没反应、托盘无图标 | SmartScreen 拦截（见安装）；源码用户跑 `python -c "import pystray, PIL, psutil"` 验依赖 |
| 红色"启动失败" | 源码安装的 dsh 缺前端构建：去 dsh 仓库根目录 `pnpm install && pnpm run build`，然后右键 → 重新启动 |
| 一直蓝色"启动中" | 首次启动含初始化，等 30-60 秒正常；转红后看日志 |
| 显示"外部启动" | 端口上已有别的 dsh 实例（预期行为，不是 bug），见上文"重要语义" |
| 想换端口 | 右键 → 重新配置 |

日志位置（菜单"帮助 → 打开日志目录"直达）：

```
C:\Users\你\.dsh-web-tray\
├── config.json          # 配置（可手改，改完重启托盘生效）
└── logs\
    ├── dsh-web.log      # dsh 的输出（排障主战场）
    └── tray.log         # 托盘自身诊断
```

## 技术备注（可选阅读）

- **退出机制**：Windows 无法向后台进程投递 SIGTERM，本工具用进程树终止（先枚举整棵子进程树再逐个终止，进程内 API 完成，不依赖外部命令）。dsh 自身的会话存储带容错设计，不会因此损坏数据
- **防孤儿**：dsh 子进程绑进 Job Object——就算托盘被任务管理器强杀，Windows 内核也会自动带走整棵 dsh 进程树
- **pnpm 兼容**：Windows 的 pnpm 是 `.cmd` 脚本，配置保存时已自动解析为完整路径；手动填了无扩展名路径（如 `...\npm\dsh`）也会自动补 `.cmd`
- **配置兼容**：`config.json` 用记事本/PowerShell 改过（带 BOM）也能正常读

## 自己打包 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
# 产出 dist\dsh-web-tray.exe（单文件、无控制台）
```
