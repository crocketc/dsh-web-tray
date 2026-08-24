# 术语表（Glossary）

维护约定：新概念进入设计文档前先在此登记；术语与实现标识符保持一致。

| 术语 | 实现标识符 | 定义 |
|---|---|---|
| 安装类型 | `dshType` | dsh 在本机的安装形态：`pnpm`（源码工作区）/ `global`（npm 全局）/ `local`（npx 本地）/ `manual`（用户手填命令）。决定更新检测与升级方式（ADR-0003）。 |
| 源码工作区 | `dshDir` (type=pnpm) | 含 `pnpm-workspace.yaml` 且根 package.json 有 `dsh` 脚本的 git 仓库，`apps/web/dist` 需已构建。 |
| dist-tag latest | — | npm registry 上包的默认安装标签；`/<pkg>/latest` 返回其元数据，`version` 字段即“最新发布版”。dsh 当前 latest 可能是 prerelease（如 0.1.1-rc.2）。 |
| prerelease 语义比较 | `semver_gt()` | semver 规则：`0.1.0-rc.7 < 0.1.0 < 0.1.1-rc.2`；prerelease 段按点分逐段比，数字段数值比较、字母段 ASCII。用于避免镜像滞后时误报升级。 |
| registry | `_resolve_registry()` | 用户 npm 配置的包仓库地址（本机实测为 npmmirror 国内镜像）；查询/升级版本必须与之一致，回退顺序：npm config → ~/.npmrc → registry.npmjs.org（ADR-0001）。 |
| 版本查询 | `fetch_latest_version()` | HTTP GET `<registry>/@deepseek-ai%2Fdsh/latest`，stdlib urllib，超时静默放弃。不 shell 出 npm CLI（EPERM 教训，ADR-0001）。 |
| 当前版本 | `current_version()` | 执行 `dshArgv[0] --version`，输出中首个 semver 匹配；绝对路径执行，不依赖 PATH。 |
| 源码更新判定 | git `HEAD..origin/main` | 源码安装的“有新版本”= 静默 `git fetch` 后 `HEAD..origin/main` 领先提交数 > 0（ADR-0003）。 |
| 静默升级执行 | `updater` 执行器 | 托盘后台线程 spawn 升级命令，无任何窗口（Windows CREATE_NO_WINDOW / POSIX 无终端）；不自动加 sudo（ADR-0002 修订版）。 |
| 升级日志 | logs 目录升级日志文件 | 静默升级 stdout/stderr 全量落盘（含轮转）；失败通知从中取 stderr 尾部摘要（ADR-0002）。 |
| 手动升级命令 | 完整命令文案 | 失败通知/菜单中给出的完整可粘贴升级命令，供 Linux root 目录等场景自行（必要时加 sudo）执行（ADR-0002）。 |
| 一键重启 | 「重启以应用新版本」菜单项 | 升级成功且托盘自管进程在跑时出现的菜单项，复用现有 restart 动作；用户点击才重启，绝不自动（ADR-0004）。 |
| 通知节流 | `lastNotifiedVersion` | config 键：同一目标版本（源码安装按落后提交数）只弹一次系统通知；菜单常驻项不受此限制（ADR-0005）。 |
| 检查冷却 | `lastUpdateCheckAt` | config 键：上次自动检查时间戳，24h 冷却，防重启风暴（ADR-0005）。 |
| 升级项（顶层） | 「🆕 升级到 x.y.z」 | 仅检测到新版本时出现的顶层菜单项；manual 类型显示「打开 DSH 发布页」；升级中置灰防重入（ADR-0006）。 |
| 检查更新 | 帮助子菜单项 | 手动触发一次检测，结果必有通知反馈（ADR-0005/0006）。 |
| 外部启动 | state=`external` | 既有概念：端口已被非托盘启动的实例占用时的监控态。更新升级同样适用（升级安装本体）；升级成功后不提供一键重启，文案提示自行重启外部实例（ADR-0004/0006）。 |
