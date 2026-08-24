# ADR 索引

| 编号 | 标题 | 一句话决策 |
|---|---|---|
| [0001](0001-registry-http-version-query.md) | 版本查询走 registry HTTP API | 不 shell 出 npm CLI；尊重用户配置的 registry（npmmirror 等），stdlib urllib 实现 |
| [0002](0002-visible-terminal-upgrade-execution.md) | 升级静默后台执行 | 不弹窗口不自动 sudo；输出落日志；失败通知带原因+手动命令（原可见终端方案已否决） |
| [0003](0003-install-type-specific-update-semantics.md) | 分安装类型的更新语义 | npm 类比 latest 版本号、源码比 git 提交、manual 只提示；升级命令 pin 具体版本 |
| [0004](0004-upgrade-result-marker-and-manual-restart.md) | 升级结果托盘直接感知 | daemon 线程 wait 退出码（无 marker 文件）；成功后提示一键重启，绝不自动 |
| [0005](0005-check-schedule-and-notify-throttle.md) | 检查时机与通知节流 | 启动后 30s + 每 24h + 手动；每版本只弹一次通知 |
| [0006](0006-menu-and-notification-presentation.md) | 菜单与通知呈现 | 顶层动态升级项、检查更新进帮助子菜单、无图标角标 |

配套术语表：[../glossary.md](../glossary.md)；实现规格：[../specs/dsh-update-check.md](../specs/dsh-update-check.md)。来源：2025-08-24 grill-with-docs 设计访谈（dsh 版本更新检测与手动升级）。
