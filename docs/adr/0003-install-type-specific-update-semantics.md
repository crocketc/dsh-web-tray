# ADR-0003：分安装类型定义「有新版本」与升级命令

- 状态：已接受（2025-08-24，grill-with-docs 访谈定稿；用户确认“全覆盖，量力而行”）

## 背景

配置 `dshType` 有四种：`pnpm`（源码）/ `global`（npm 全局）/ `local`（npx 本地）/
`manual`。源码工作区的 git 状态与 npm 发布版是两条独立时间线（本机源码
0.1.0-rc.7 vs npm latest 0.1.1-rc.2，源码可能反超），不能拿 npm 版本号套源码。

## 决策

| dshType | 新版本判定 | 升级动作（终端中执行） |
|---|---|---|
| `global` | `<registry>/latest` 的 version > 当前（`dsh --version`） | `npm install -g @deepseek-ai/dsh@<版本号>`（pin 具体版本，非 @latest） |
| `local` | 同上（npx 场景 registry 即事实来源） | `npm install @deepseek-ai/dsh@<版本号>`（在 `dshDir` 下执行） |
| `pnpm` | 后台 `git fetch`（超时保护）后 `HEAD..origin/main` 有新提交 | `git pull && pnpm install && pnpm run build`（cwd=`dshDir`） |
| `manual` | 不自动判定（无从知晓安装方式） | 只提示 + 打开 dsh 仓库 Releases/文档页，不执行任何命令 |

- 当前版本获取：用配置里的 `dshArgv[0] --version`（绝对路径，不依赖 PATH），
  取输出中首个 semver 匹配。
- 源码判定失败（无 git、网络问题、detached HEAD）→ 静默视为“未知”，不提示。

## 后果

- 每种类型信号真实：npm 类比版本号、源码比 git 提交，不产生假阳/假阴。
- `manual` 类型保持诚实：不知道怎么装的就不装作能升级。
- 源码 `git fetch` 引入网络操作，须限超时且不阻塞托盘（独立线程）。
