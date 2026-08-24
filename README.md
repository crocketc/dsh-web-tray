# DSH Web Tray

把 DSH Web GUI 装进系统托盘：双击启动、后台运行、不占终端、状态一眼可见。

![托盘右键菜单](docs/images/tray-menu.png)

[![Release](https://img.shields.io/badge/release-v1.6.0-blue)](https://github.com/crocketc/dsh-web-tray/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-lightgrey)](https://github.com/crocketc/dsh-web-tray/releases/latest)

## 它解决什么问题

直接在终端里跑 `dsh web` 有几个日常烦恼：

- **占一个终端窗口**——关掉窗口，服务就停了
- **看不出它活没活着**——端口多少、地址是什么，都得翻终端输出
- **每次开机要手动敲命令**

DSH Web Tray 把它变成一个"托盘小服务"：

| 你得到的 | 说明 |
|---------|------|
| 🖱️ 双击启动 | 无终端窗口，后台静默运行 |
| 🎨 状态一眼可见 | 托盘图标颜色实时反映运行状态，悬停显示访问地址 |
| 🌐 一键打开浏览器 | 双击托盘图标即可打开 Web 界面（自动带正确端口） |
| 💥 崩溃自动感知 | dsh 意外退出时图标变红，一键重启 |
| 🔄 更新检测与一键升级 | 自动检测 dsh 新版本，菜单一键静默升级 + 手动重启应用新版 |
| 🔁 防重复启动 | 检测到已在运行的实例会显示"外部启动"，不会起第二份 |
| ⏻ 干净退出 | 从托盘退出时**只关自己启动的** dsh，不碰外部进程，不留孤儿进程 |
| 💾 可选开机自启 | 托盘菜单上勾一下就行 |

## 安装

### 方式一：下载安装包（推荐）

去 [Releases 页面](https://github.com/crocketc/dsh-web-tray/releases/latest) 下载对应平台的包：

| 下载文件 | 适用平台 | 怎么用 |
|---------|---------|--------|
| `dsh-web-tray.exe` | Windows | 下载后双击即用 |
| `dsh-web-tray-arm64-*.dmg` | Mac（M 系列芯片） | 打开 DMG，把应用拖到里面的「应用程序」快捷方式 |
| `dsh-web-tray-x86_64-*.dmg` | Mac（Intel 芯片） | 同上 |

**无需安装 Python 或任何依赖。**

> - Windows 首次运行 exe 可能弹 SmartScreen 蓝色警告（未购买代码签名证书的通病）：点"更多信息" → "仍要运行"
> - Mac 首次打开需**右键点应用 → 打开**（同理，未做 Apple 公证）；它只出现在菜单栏，不占 Dock

### 方式二：从源码运行

需要 Python 3.9+：

```bash
pip install -r requirements.txt

# Windows：双击 start-tray.cmd（后台运行，关终端无影响）
# 或命令行：
pythonw dsh-web-tray.py     # 后台
python dsh-web-tray.py      # 前台（调试用，能看输出）
```

```bash
# macOS（Homebrew Python 需先装 tk）
brew install python-tk
pip3 install -r requirements.txt
python3 dsh-web-tray.py
```

## 首次运行

第一次启动会弹出**配置向导**，全程只需点几下：

![首次运行配置向导](docs/images/wizard-first-run.png)

1. 自动检测本机的 dsh 安装（npm 全局 / 源码 pnpm / npx 本地，多份共存时让你选）
2. 选端口号（默认 3080；填 0 = 每次自动分配）
3. 保存后自动启动，托盘出现图标

没装 dsh？向导会给出安装命令（可一键复制）和官方文档链接，装好后点"重试检测"即可。

## 日常使用

### 托盘图标 = 状态灯

| 图标 | 状态 | 含义 |
|------|------|------|
| 🔵 蓝色 | 启动中 | 正在拉起 dsh web（首次启动含初始化，可能要等十几秒） |
| 🟢 绿色 | 运行中 | 服务正常，悬停图标可看访问地址 |
| 🩵 青色（双环） | 运行中·外部启动 | 检测到你自己在外部启动的 dsh（比如终端里跑的），托盘只监控、不接管 |
| 🔴 红色（✕） | 意外退出 / 启动失败 | 前者点"重新启动"；后者看日志排查 |
| ⚪ 灰色 | 已停止 | 点"重新启动"可再次拉起 |

### 右键菜单

![托盘右键菜单](docs/images/tray-menu.png)

```
● 运行中 (http://127.0.0.1:3080)      ← 状态行，实时更新
──────────────────
打开浏览器          ← 也可以直接双击托盘图标
重新启动
停止
☑ 开机自启            ← 勾选框，点一下切换
重新配置            ← 重跑配置向导
🆕 升级到 0.1.2     ← 检测到新版本时才出现（静默升级入口）
🔄 升级中…          ← 升级执行中置灰防重入
🔄 重启以应用新版本   ← 升级成功后出现（自管进程在跑时）
──────────────────
帮助
  ├── 检查更新        ← 立即检查一次，结果必有通知
  ├── 如何安装 DSH
  ├── 访问官方文档
  └── 打开日志目录
退出                ← 优雅停止 dsh 并退出托盘
```

### 两个值得知道的行为

- **退出托盘 ≠ 一定杀掉 dsh**：托盘只停掉**自己启动的**那份。如果你在终端里手动跑了一个 dsh web（青色"外部启动"状态），退出托盘不会碰它——避免误杀你正在用的会话
- **换端口 / 换安装方式**：菜单 → 重新配置，向导里改完保存即可

### 更新检测与升级

托盘会替你盯着 dsh 的新版本，升级完全由你手动触发、绝不强制：

**什么时候检查？**

- 启动约 30 秒后静默检查一次，之后每 24 小时一次（重启托盘也尊重 24 小时冷却，不会刷屏）
- 帮助 → 检查更新：随时手动触发，无论有无更新都弹通知告知结果

**怎么判定"有新版本"？**（按你的安装方式区分，不做跨源误判）

| 安装方式 | 判定方式 | 升级动作 |
|---------|---------|--------|
| npm 全局 / npx 本地 | 对比你配置的 registry 上的 latest 版本号（semver 语义，含 rc 等预发布版） | `npm install` 精确安装提示的那个版本 |
| pnpm 源码 | `git fetch` 后对比远端是否有新提交 | `git pull && pnpm install && pnpm run build` |
| 手动安装 | 不自动判定（托盘无从知晓安装方式） | 只提示并打开发布页，不执行命令 |

镜像滞后（latest 比你本地还旧）不会提示升级；断网时检查完全静默，行为与现状无异。

**升级怎么执行？**

点击菜单的「🆕 升级到 x.y.z」后，托盘在**后台静默执行**：无窗口弹出、不自动加 sudo，
输出全量写入升级日志（`~/.dsh-web-tray/logs/upgrade.log`）。结束后系统通知结果：

- **成功** →「升级完成，建议重启」；托盘自管的 dsh 在跑时菜单出现「🔄 重启以应用新版本」，
  点一下完成版本切换（**绝不自动重启**，不打断你进行中的会话）
- **失败** → 通知带失败原因摘要 + 完整手动升级命令（Linux 全局目录归 root 等场景，
  复制命令自行加 sudo 执行即可），升级项保留在菜单可重试
- **外部启动（青色）态** → 同样能升级安装本体，但重启由你自己管（托盘不碰外部进程）

同一目标版本的系统通知只弹一次（菜单入口常驻）；升级超过 30 分钟未完成会自动终止并通知。

**排查"为什么没提示更新"**：命令行直接打印检测判定——

```bash
python dsh-web-tray.py --check-update
# 安装类型: global
# registry: https://registry.npmmirror.com
# 当前版本: 0.1.0
# 最新版本: 0.1.1-rc.2
# 结论: 有新版本 → 0.1.1-rc.2
```

## 常见问题

**双击后托盘没图标？**
Windows 检查是否被 SmartScreen 拦截（见上文）；源码用户确认依赖装了没：`python -c "import pystray, PIL, psutil"`。仍不行就看日志（见下文"文件都在哪"）。

**状态是红色的"启动失败"？**
最常见原因：源码安装的 dsh 没构建前端。去 dsh 仓库根目录执行 `pnpm install && pnpm run build`，然后托盘菜单 → 重新启动。

**一直蓝色"启动中"？**
首次启动要做初始化，等 30-60 秒属正常。超时后会转红并提示看日志。

**显示"外部启动"但我明明没开？**
可能是之前某次手动启动的 dsh 还在后台。想统一交给托盘管理：先停掉那个进程，再托盘菜单 → 重新启动。

**升级失败了怎么办？**
看失败通知里的原因摘要和手动命令：把通知里的完整命令复制到终端执行（Linux 全局目录归 root 时自行加 sudo）。
完整输出在升级日志 `~/.dsh-web-tray/logs/upgrade.log`（菜单「帮助 → 打开日志目录」直达），修复后菜单里的升级项可重试。

**「检查更新」说无法判定？**
源码（pnpm）安装时多为 git 网络问题或 detached HEAD；手动安装类型本来就不做自动判定。
这类情况宁可漏报不误报——修好网络后再点一次「检查更新」即可。

**数据都存在哪？**

| 内容 | 位置 |
|------|------|
| 配置 | `~/.dsh-web-tray/config.json`（Windows 即 `C:\Users\你\.dsh-web-tray\`） |
| dsh 运行日志 | `~/.dsh-web-tray/logs/dsh-web.log`（超 5MB 自动轮转） |
| 托盘自身日志 | `~/.dsh-web-tray/logs/tray.log`（排障先看这个） |
| 升级日志 | `~/.dsh-web-tray/logs/upgrade.log`（超 5MB 自动轮转） |

菜单"帮助 → 打开日志目录"可以直接跳过去。

## 平台专属说明

- [README-Windows.md](README-Windows.md) — 安装细节、开机自启、Windows 特有行为
- [README-macOS.md](README-macOS.md) — 安装细节、菜单栏使用、首次打开引导

## 技术细节（可选阅读）

好奇内部实现或想参与开发的，这里是速览：

- **就绪判定**：不轮询端口。解析 dsh 官方的 stdout 就绪信号（`dsh web: http://...` URL 行），该行打印时所有 API 路由已挂载完毕；天然支持 `--port 0` 自动分配端口
- **优雅退出**：macOS/Linux 上向 dsh 发 SIGTERM（dsh 官方约定的 supervisor 停止信号，exit 0，dsh 自行清理整棵进程树）；Windows 无法投递 SIGTERM，改用进程树终止 + Job Object 绑定（托盘被强杀时内核自动带走整棵 dsh 树，不留孤儿）
- **macOS GUI 环境的 PATH 陷阱**：双击启动的应用看不到 Homebrew/nvm/volta 装的工具（GUI 进程的 PATH 只有系统目录）。对策是配置保存时即解析为绝对路径（登录 shell + 常见安装目录双路兜底），运行期不依赖 PATH
- **配置存 argv 数组**而非字符串：路径带空格也不会碎；读取兼容 UTF-8 BOM
- **单实例锁**：锁文件 + PID + 进程创建时间三重校验，防误判也防重复启动
- **更新检测**：版本查询走 registry HTTP API（不依赖 npm CLI 健康状态），尊重你配置的
  registry（含国内镜像）；semver 语义比较手写实现、无新依赖；源码安装走 git 提交对比；
  所有网络操作带超时、失败静默只记日志。设计决策见 `docs/adr/`（ADR-0001 ~ 0006）

测试：170+ 个单元测试 + 真实 dsh 集成测试（启动→就绪→HTTP 探活→停止→验证无孤儿）：

```bash
python -m unittest discover -s tests
DSH_WEB_TRAY_LIVE=1 python -m unittest tests.test_integration_live -v   # 集成
```

## 许可

随 DSH（DeepSeek Harness）生态使用：<https://github.com/deepseek-ai/harness>
