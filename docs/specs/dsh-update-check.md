# Spec：dsh 版本更新检测与手动升级（托盘集成）

- 来源：2025-08-24 grill-with-docs 设计访谈 → ADR 0001–0006 / 术语表
- 状态：已实现（分支 feature/dsh-update-check；2025-08-24 交付）
- 修订：2025-08-24 执行方式定稿为**静默升级**（ADR-0002/0004 已同步改写）

## Problem Statement

dsh（@deepseek-ai/dsh）迭代很快（当前本机 0.1.0-rc.7，registry latest 已是 0.1.1-rc.2），
但托盘用户对此毫无感知：装完 dsh-web-tray 之后，dsh 什么时候出了新版、要不要升、怎么升，
全靠用户自己去查。结果就是长期挂在托盘里的 dsh web 一直跑着旧版本，缺修复、缺新功能，
用户还以为自己是最新状态。普通用户（非开发者）甚至不知道 dsh 是 npm 包、可以升级。

## Solution

托盘自动检测 dsh 新版本并提示，升级完全由用户手动触发、绝不强制：

- 托盘启动约 30 秒后静默检查一次，之后每 24 小时一次；帮助子菜单随时可手动「检查更新」。
- 检测方式按安装类型区分：npm 全局/本地安装对比 registry latest 版本号（semver 规则，
  含 prerelease）；pnpm 源码安装对比 git 远端是否有新提交；manual 安装不做自动判定。
- 发现新版本：系统通知弹一次（同一版本只弹一次），顶层菜单出现「🆕 升级到 x.y.z」。
- 用户点击升级：托盘**静默后台执行**（无窗口、不自动 sudo）：npm 类 pin 具体版本；
  源码类 git pull + pnpm install + pnpm run build；manual 只打开发布页。输出全量写升级日志。
- 结束后通知结果：成功 →「升级完成，建议重启」+（托盘自管进程在跑时）「重启以应用新版本」
  一键重启项；失败 → 原因摘要 + 完整手动升级命令（供 sudo 场景自行执行）+ 日志入口。
  全程不自动重启、不打断会话。

## User Stories

1. 作为 npm 全局安装的托盘用户，我想在 dsh 发布新版本时收到一次系统通知，以便不必自己盯着 registry 也能及时知道有更新。
2. 作为 npm 全局安装的托盘用户，我想在托盘顶层菜单看到「升级到 x.y.z」入口，以便想升的时候一键点到，而不是去翻安装命令。
3. 作为长期挂机不重启托盘的用户，我想让托盘每 24 小时自动检查一次新版本，以便连续运行几天也能在一天内感知到更新。
4. 作为谨慎的用户，我想让升级完全由我手动触发，以便托盘绝不在我不知情时改动我的 dsh 安装。
5. 作为谨慎的用户，我想在升级完成后由我自己决定何时重启 dsh web（菜单一键重启），以便升级不打断我正在进行的会话。
6. 作为手动点击「检查更新」的用户，我想无论有无新版本都得到明确通知（含当前版本号），以便确认点击生效了。
7. 作为手动点击「检查更新」的用户，我想在已是最新时看到「已是最新 x.y.z」而非沉默，以便与“检查失败”区分开。
8. 作为 npx 本地安装的用户，我想让更新检测走 registry latest 版本号对比，以便与我的安装事实来源一致。
9. 作为 npx 本地安装的用户，我想让升级命令在正确的目录下执行 npm install（pin 版本），以便本地 node_modules 精确升级到提示的版本。
10. 作为 pnpm 源码安装的用户，我想让更新检测用 git 远端提交对比（而非 npm 版本号），以便源码工作区可能领先于 npm 发布版时不会被误判。
11. 作为 pnpm 源码安装的用户，我想让升级命令自动串起 git pull && pnpm install && pnpm run build，以便一条命令完成源码升级全流程。
12. 作为 manual 安装的用户，我想让托盘诚实地只提示“有新版本”并打开发布页而不执行命令，以便我按自己的安装方式手动升级。
13. 作为使用国内镜像（npmmirror 等）的用户，我想让版本查询走我配置的 registry，以便提示与我的实际安装源一致。
14. 作为使用镜像的用户，我想在镜像滞后（latest 比本地还旧）时不收到升级提示，以免被误导降级。
15. 作为 Linux 发行版 node 的用户（全局目录归 root），我想在静默升级失败时收到带完整手动命令的失败通知，以便我自己加 sudo 执行完成升级。
16. 作为遇到 npm 缓存损坏（EPERM）的用户，我想在失败通知里看到 npm 报错摘要、在升级日志里看到完整输出，以便定位并修复后重试。
17. 作为非开发者用户，我想让升级全程无窗口弹出、一键后台完成并通知结果，以便升级不吓人也不需要我懂终端。
18. 作为外部启动（external 态）的用户，我想同样收到新版本提示并能升级安装本体，以便不是我启动的 dsh web 也能被更新（但重启由我自己管）。
19. 作为升级进行中的用户，我想让菜单升级项置灰防重入，以免不小心并发触发两次升级互相踩踏。
20. 作为升级失败的用户，我想收到失败通知并能把日志目录打开，以便定位失败原因。
21. 作为升级成功的用户，我想在托盘自管进程运行时看到「重启以应用新版本」，以便一步完成版本切换。
22. 作为离线/网络受限的用户，我想让检查失败完全静默（只记日志、不弹错误），以便断网时托盘行为与现状无异。
23. 作为每次开机都启动托盘的用户，我想让自动检查尊重 24 小时冷却记录，以便频繁重启电脑不会刷屏检查或通知。
24. 作为只想安静的极简用户，我想让同一目标版本的系统通知只弹一次（菜单入口常驻），以便提示不变成打扰。
25. 作为旧配置升级上来的用户，我想让新增配置键缺省即“未检查过”而无需迁移，以便升级托盘应用本身零成本。
26. 作为关注安全的用户，我想让升级命令固定 pin 提示中的具体版本号而非 @latest，以便所见即所得、可预期可审计。
27. 作为需要诊断“为什么没提示更新”的用户，我想有一条命令行入口直接打印检测判定（当前版本/registry/结论），以便不猜不翻日志。

## Implementation Decisions

（决策依据见 ADR 0001–0006；术语见 glossary。执行方式为静默升级：ADR-0002/0004 修订版。）

- **新模块 updater**：承载全部更新领域逻辑，纯函数为主，不 import 托盘 UI。
  - 版本查询 fetch_latest_version：stdlib urllib HTTP GET registry latest 元数据，解析 version 字段；连接+读取总超时约 10 秒，任何异常静默返回 None。不 shell 出 npm CLI。
  - registry 解析 _resolve_registry：优先 npm config get registry（子进程带超时），失败回退解析用户 .npmrc，再回退官方 registry；scoped 包名按 URL 编码。
  - semver 比较 semver_gt：手写实现（无新依赖），支持 prerelease 语义（0.1.0-rc.7 < 0.1.0 < 0.1.1-rc.2）；latest ≤ 当前时一律“无更新”。
  - 当前版本 current_version：执行配置 dshArgv 首元素 --version（绝对路径，不依赖 PATH），输出取首个 semver 匹配；失败返回 None。
  - 源码更新判定：git fetch --quiet（超时保护）后统计远端领先提交数；git 异常/detached HEAD → 未知，视为无更新不提示。
  - 升级命令构造 build_upgrade_command：按安装类型分派——global → npm install -g 包@pin版本；local → 在 dshDir 下 npm install 包@pin版本；pnpm → git pull && pnpm install && pnpm run build（cwd=dshDir）；manual → 无命令，仅打开发布页动作。同时产出完整手动命令文案（失败通知用）。
  - 静默执行器：托盘后台线程 spawn 升级命令——Windows CREATE_NO_WINDOW，POSIX start_new_session 无终端启动（不绑 Job Object：由执行线程 wait() 监管、约 30 分钟超时 kill，托盘意外退出时让升级自然跑完）；stdout/stderr 全量写升级日志（含轮转）；daemon 线程 wait 返回（退出码, stderr 尾部摘要）。不自动加 sudo、不弹任何窗口。
- **config 扩展**：新增三键 lastUpdateCheckAt（检查冷却时间戳）、lastNotifiedVersion（通知节流）、lastKnownLatestVersion（菜单显示缓存）。default_config 提供缺省值，旧配置无需迁移、_is_valid 不收紧。
- **CLI 诊断入口 --check-update**：打印当前版本/registry latest/判定结论（含源码安装的 git 判定与“未知”），退出码 0；纯诊断，不影响托盘 UI。不提供 --upgrade 入口（用户已确认）。
- **TrayApp 接线**：
  - 更新检查调度：daemon 线程，启动延迟约 30 秒首查，此后每 24 小时；手动「检查更新」绕过冷却但仍写检查时间戳。所有网络/子进程失败只记日志。
  - 菜单：顶层动态「🆕 升级到 x.y.z」（manual → 「打开 DSH 发布页」，点击即打开 URL）；升级中置灰「升级中…」；帮助子菜单新增「检查更新」；升级成功且托盘自管进程在跑时出现「重启以应用新版本」（复用现有 restart 动作）。external 态不提供一键重启，通知文案说明需自行重启外部实例。无更新时菜单与现状完全一致。
  - 通知：检测到新版本且 lastNotifiedVersion ≠ 目标版本（源码安装按落后提交数区分）→ 系统通知一次并记录；手动检查必反馈（有更新 → 提示；无更新 → “已是最新 x.y.z”；无法判定 → 如实原因，不谎称已最新）。
  - 升级结果：执行器线程直接以退出码回调（无 marker 文件）——0 → 成功通知 + 一键重启项（自管进程在跑时）；非 0 → 失败通知（原因摘要 + 手动命令）+ 保留升级项可重试。成功后清 lastNotifiedVersion/lastKnownLatestVersion 并触发重新检查。检查/升级对三键的每次变更均写回磁盘（重启后冷却与节流仍生效）。
  - 升级中置灰防重入；约 30 分钟超时终止子进程并恢复菜单，状态回到“发现新版本”。
- **无新依赖**：全部用 stdlib（urllib、json、subprocess、threading）。
- 既有行为零改动：状态机、图标、退出语义、单实例、自启等一律不动。

## Testing Decisions

- 好的测试只测模块的外部行为：给定输入（配置 dict、版本串、命令列表、伪造的 git 仓库/registry 响应）断言输出（布尔、命令串、解析值），不打真网络、不起真升级、不碰托盘 UI。沿用仓库既有先例：unittest + mock.patch.object + tests.new_test_dir() 临时目录。
- 四个测试缝（已与用户确认；静默执行后第三缝从 platforms 转为 updater 执行器，新增缝仍只有 updater 一个模块）：
  1. **updater 模块纯函数 API**（唯一新缝）：semver_gt 全矩阵（含 prerelease、镜像滞后不提示）；_resolve_registry 三级回退（npm config 成功/失败、.npmrc 存在/缺失）；fetch_latest_version（mock urllib：正常 JSON、超时、非 200、坏 JSON → None）；current_version（mock 子进程：正常输出、非零退出、无版本串 → None）；build_upgrade_command 四种安装类型（pin 版本、cwd、手动命令文案）；源码判定（临时 git 仓库 fetch/比较 mock）。
  2. **config 缝（复用）**：新三键 round-trip、default_config 缺省、旧配置（无新键）仍判定有效。
  3. **updater 静默执行器（照 platforms/test 先例 mock subprocess）**：argv/cwd/Windows creationflags 构造、成功退出码透传、输出落盘升级日志、失败返回 stderr 尾部摘要。
  4. **托盘菜单装配不自动化**：实机人工验证（与现有 integration-live 同类，不新增自动化）。
- 先例：test_detect.py（临时目录伪造源码工作区）、test_config.py（app_dir 打桩）、test_platforms_icons.py（纯函数直测）。

## Out of Scope

- 托盘应用自身的自动更新（dsh-web-tray 的 exe/dmg 升级）。
- 任何形式的强制升级或自动重启 dsh web。
- 可见终端/交互式升级窗口（用户已否决；如未来需要再立新 ADR 取代 ADR-0002）。
- “关闭自动检查”开关 / 通知总开关（保持零配置；有真实需求再加）。
- 图标角标等视觉提示（已明确不要）。
- Linux 发行版 node 场景的自动 sudo / 提权（以失败通知+手动命令兜底）。
- 镜像滞后时的跨源交叉验证（如同时查官方 registry 比对）。
- npm 之外的包管理器（brew 等）安装的 dsh。
- --upgrade 命令行入口（已确认不提供）。

## Further Notes

- 关键实机教训（ADR-0001 背景）：npm view 可能因 ~/.npm 缓存 root 残留而 EPERM——只读查询也靠不住，这是“查询走 HTTP”的直接证据；本机 registry 实测为 npmmirror 镜像，这是“尊重用户 registry 配置”的直接证据。
- 静默升级的失败兜底三件套：失败通知（含 stderr 尾部摘要）+ 完整手动命令 + 升级日志入口；Linux root 目录场景用户自行加 sudo 执行手动命令。
- external 态的一键重启刻意缺席：外部进程不归托盘管（与既有“只管自己启动的进程”原则一致），通知文案需说明。
- 源码判定的 origin/main 依赖 fetch 成功且默认分支配置正常；异常一律“未知→不提示”，宁可漏报不可误报。
