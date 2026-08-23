# ADR-0001：版本查询走 registry HTTP API，不 shell 出 npm CLI

- 状态：已接受（2025-08-24，grill-with-docs 访谈定稿）
- 关联：ADR-0003（分安装类型的更新语义）

## 背景

查询 dsh 最新版本有两种途径：

1. 子进程执行 `npm view @deepseek-ai/dsh version`
2. HTTP GET `<registry>/@deepseek-ai/dsh/latest`，解析 JSON 的 `version` 字段

本机实测（2025-08）：`npm view` 因 `~/.npm/_cacache` 中存在历史 npm bug 遗留的
root 属主文件而 EPERM 失败——**只读查询也会挂**。另实测本机 registry 配置为
`https://registry.npmmirror.com`（国内镜像），与 `registry.npmjs.org` 内容可能滞后。

## 决策

- 最新版本查询一律走 HTTP GET registry API（Python stdlib `urllib`，无新依赖）。
- registry 地址**尊重用户 npm 配置**：优先 `npm config get registry`（带超时保护），
  失败回退解析 `~/.npmrc`，再回退 `https://registry.npmjs.org`。
- scoped 包名 URL 编码：`@deepseek-ai/dsh` → `/@deepseek-ai%2Fdsh/latest`。
- 超时（连接+读取共 ~10s）即静默放弃本次检查，绝不阻塞托盘。
- 比较当前版本与 latest 使用 semver 规则（含 prerelease：`0.1.0-rc.7 < 0.1.0`；
  prerelease 段按点分标识符逐段比，数字段按数值、字母段按 ASCII）。手写 ~30 行
  实现，不引入新依赖。**镜像滞后导致 latest < 当前版本时不提示升级**。

## 后果

- 查询不依赖 npm CLI 健康状态（缓存损坏、npm 不在 PATH 均不影响）。
- 与用户真实安装源一致，避免“npmjs 有新版但镜像没有”的假提示。
- 需自行维护 semver 比较（含 prerelease）正确性，配单元测试覆盖。
